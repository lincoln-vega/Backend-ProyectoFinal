import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from database import DATABASE_URL, fetch_one_as_dict, get_pg_connection

security = HTTPBearer(auto_error=False)

# Cache en memoria de identidad + perfil SQL (TTL 120s) y cliente HTTP keep-alive hacia Supabase Auth
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
_PROFILE_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE_TTL = 120  # segundos

_HTTP_CLIENT = httpx.Client(
    timeout=10.0,
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=25),
)


def prime_session_cache(
    token: str,
    auth_user_id: str,
    email: str,
    profile: Optional[Dict[str, Any]] = None,
) -> None:
    """Pre-carga en memoria el token y el perfil completo justo al completar POST /usuarios/login."""
    now = time.time()
    clean_uid = str(auth_user_id).strip()
    clean_email = str(email or "").strip().lower()
    if token and clean_uid:
        _SESSION_CACHE[token.strip()] = {
            "identity": {"id": clean_uid, "email": clean_email},
            "timestamp": now,
        }
    if clean_uid and isinstance(profile, dict):
        _PROFILE_CACHE[clean_uid] = {"profile": dict(profile), "timestamp": now}


def invalidate_auth_caches() -> None:
    """Invalida la caché de perfiles cuando un administrador edita o desactiva un usuario."""
    _PROFILE_CACHE.clear()
    _SESSION_CACHE.clear()


def _supabase_url() -> str:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_URL no está configurada en el backend.",
        )
    return url


def _supabase_anon_key() -> str:
    key = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_ANON_KEY no está configurada en el backend.",
        )
    return key


def _supabase_service_key() -> Optional[str]:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return key or None


def authenticate_with_supabase(email: str, password: str) -> Dict[str, Any]:
    """Autentica credenciales contra Supabase Auth (GoTrue) y retorna user + accessToken."""
    base_url = _supabase_url()
    anon_key = _supabase_anon_key()

    try:
        response = _HTTP_CLIENT.post(
            f"{base_url}/auth/v1/token?grant_type=password",
            headers={
                "apikey": anon_key,
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo contactar con Supabase Auth.",
        ) from exc

    if response.status_code in (400, 401):
        body = {}
        try:
            body = response.json()
        except ValueError:
            pass
        err_code = str(body.get("error_code") or body.get("error") or "").lower()
        msg = str(body.get("msg") or body.get("error_description") or "").lower()
        if "email_not_confirmed" in err_code or "not confirmed" in msg:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La cuenta requiere verificación de correo antes de iniciar sesión.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    if response.status_code == 403:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta requiere verificación antes de iniciar sesión.",
        )

    if not response.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase Auth no pudo completar el inicio de sesión.",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase Auth devolvió una respuesta inválida.",
        ) from exc

    return {
        "accessToken": payload.get("access_token") or payload.get("accessToken"),
        "refreshToken": payload.get("refresh_token"),
        "user": payload.get("user"),
    }


def create_supabase_auth_user(
    email: str,
    password: str,
    full_name: str = "",
    role: str = "estudiante",
) -> Dict[str, Any]:
    """
    Crea una cuenta en Supabase Auth lista para iniciar sesión (email confirmado).
    Prioriza Admin API con SUPABASE_SERVICE_ROLE_KEY y almacena el rol en app_metadata (inmutable por el cliente).
    """
    base_url = _supabase_url()
    service_key = _supabase_service_key()
    clean_email = email.strip().lower()
    norm_role = role.strip().lower()

    if service_key:
        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(
                f"{base_url}/auth/v1/admin/users",
                headers=headers,
                json={
                    "email": clean_email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"full_name": full_name},
                    "app_metadata": {"role": norm_role},
                },
                timeout=10.0,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo contactar con Supabase Admin Auth.",
            ) from exc

        if resp.is_success:
            user = resp.json()
            user_id = user.get("id") or (user.get("user") or {}).get("id")
            if user_id:
                return {
                    "authUserId": str(user_id),
                    "emailVerified": True,
                    "requiresEmailVerification": False,
                }

        # Si el correo ya existía en auth.users (ej. recreación limpia), intentar recuperar su ID
        if resp.status_code in (400, 409, 422):
            body_text = resp.text.lower()
            if "already" in body_text or "exists" in body_text or "registered" in body_text:
                try:
                    list_resp = httpx.get(
                        f"{base_url}/auth/v1/admin/users?per_page=500",
                        headers=headers,
                        timeout=10.0,
                    )
                    if list_resp.is_success:
                        users_list = list_resp.json().get("users", [])
                        existing = next(
                            (u for u in users_list if str(u.get("email", "")).lower() == clean_email),
                            None,
                        )
                        if existing and existing.get("id"):
                            # Sincronizar contraseña y confirmar email del usuario recuperado
                            httpx.put(
                                f"{base_url}/auth/v1/admin/users/{existing['id']}",
                                headers=headers,
                                json={
                                    "password": password,
                                    "email_confirm": True,
                                    "user_metadata": {"full_name": full_name},
                                    "app_metadata": {"role": norm_role},
                                },
                                timeout=10.0,
                            )
                            return {
                                "authUserId": str(existing["id"]),
                                "emailVerified": True,
                                "requiresEmailVerification": False,
                            }
                except httpx.RequestError:
                    pass
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ya existe una cuenta en Supabase Auth con ese correo.",
                )

        detail_msg = "No se pudo crear la cuenta en Supabase Auth."
        try:
            err_json = resp.json()
            detail_msg = err_json.get("msg") or err_json.get("message") or detail_msg
        except ValueError:
            pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail_msg)

    # Fallback sin service_role_key: signup estándar con SUPABASE_ANON_KEY
    anon_key = _supabase_anon_key()
    try:
        resp = httpx.post(
            f"{base_url}/auth/v1/signup",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={
                "email": clean_email,
                "password": password,
                "data": {"full_name": full_name},
            },
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo contactar con Supabase Auth para registro.",
        ) from exc

    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supabase Auth rechazó la creación de la cuenta.",
        )

    data = resp.json()
    user_obj = data.get("user") if isinstance(data.get("user"), dict) else data
    uid = user_obj.get("id")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase Auth no devolvió el identificador del usuario creado.",
        )
    confirmed = bool(user_obj.get("email_confirmed_at") or user_obj.get("confirmed_at"))
    return {
        "authUserId": str(uid),
        "emailVerified": confirmed,
        "requiresEmailVerification": not confirmed,
    }


def delete_supabase_auth_user(auth_user_id: str) -> bool:
    """Elimina al usuario en Supabase Auth Admin API e invalida sus sesiones."""
    service_key = _supabase_service_key()
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not service_key or not url or not auth_user_id:
        return False
    try:
        resp = httpx.delete(
            f"{url}/auth/v1/admin/users/{auth_user_id}",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
            timeout=8.0,
        )
        return resp.is_success
    except httpx.RequestError:
        return False


def get_profile_by_auth_id(auth_user_id: str, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
    clean_uid = str(auth_user_id).strip()
    now = time.time()
    cached_prof = _PROFILE_CACHE.get(clean_uid)
    if cached_prof and (now - cached_prof["timestamp"] < _SESSION_CACHE_TTL):
        return dict(cached_prof["profile"])

    conn = get_pg_connection()
    if not conn:
        detail = (
            "No se pudo conectar con PostgreSQL en Supabase."
            if DATABASE_URL
            else "DATABASE_URL no está configurada en el backend."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM users
                WHERE auth_user_id = %s::uuid
                LIMIT 1;
                """,
                (clean_uid,),
            )
            profile = fetch_one_as_dict(cur)
            if profile is None and email:
                # Re-vinculación automática segura si el correo coincide pero auth_user_id estaba vacío tras migración
                cur.execute(
                    """
                    UPDATE users
                    SET auth_user_id = %s::uuid
                    WHERE LOWER(correo) = LOWER(%s) AND auth_user_id IS NULL
                    RETURNING *;
                    """,
                    (clean_uid, email.strip()),
                )
                profile = fetch_one_as_dict(cur)
                if profile:
                    conn.commit()
            if profile:
                _PROFILE_CACHE[clean_uid] = {"profile": dict(profile), "timestamp": now}
            return profile
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo consultar el perfil del usuario en PostgreSQL.",
        ) from exc
    finally:
        conn.close()


def get_current_identity(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, str]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un token Bearer de sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials.strip()
    now = time.time()

    cached = _SESSION_CACHE.get(token)
    if cached and (now - cached["timestamp"] < _SESSION_CACHE_TTL):
        return cached["identity"]

    # 1. Verificación local instantánea si SUPABASE_JWT_SECRET está definido (HS256)
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
    if jwt_secret:
        try:
            claims = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            sub = claims.get("sub")
            email = claims.get("email")
            if sub and email:
                identity = {"id": str(sub), "email": str(email).strip().lower()}
                _SESSION_CACHE[token] = {"identity": identity, "timestamp": now}
                return identity
        except JWTError:
            pass

    # 2. Verificación autoritativa contra Supabase Auth (/auth/v1/user) con conexión keep-alive
    base_url = _supabase_url()
    anon_key = _supabase_anon_key()
    try:
        response = _HTTP_CLIENT.get(
            f"{base_url}/auth/v1/user",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {token}",
            },
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo validar la sesión con Supabase Auth.",
        ) from exc

    if response.status_code in (401, 403):
        _SESSION_CACHE.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión no es válida o expiró.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not response.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase Auth no pudo validar la sesión.",
        )

    try:
        payload = response.json()
        user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase Auth devolvió una respuesta inválida.",
        ) from exc

    if not isinstance(user, dict) or not user.get("id") or not user.get("email"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión no contiene una identidad de usuario válida.",
        )

    identity = {"id": str(user["id"]), "email": str(user["email"]).strip().lower()}
    _SESSION_CACHE[token] = {"identity": identity, "timestamp": now}
    return identity


def get_current_user(
    identity: Dict[str, str] = Depends(get_current_identity),
) -> Dict[str, Any]:
    profile = get_profile_by_auth_id(identity["id"], identity.get("email"))
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta de Auth no tiene un perfil registrado en users.",
        )
    return profile


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    if not credentials or not credentials.credentials:
        return None
    try:
        identity = get_current_identity(credentials)
        return get_current_user(identity)
    except Exception:
        return None


def require_role(allowed_roles: List[str]):
    normalized_allowed_roles = {role.strip().casefold() for role in allowed_roles}

    def role_verifier(current_user: Dict[str, Any] = Depends(get_current_user)):
        role = current_user.get("rol")
        if not isinstance(role, str) or role.strip().casefold() not in normalized_allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos suficientes para realizar esta acción",
            )
        return current_user

    return role_verifier
