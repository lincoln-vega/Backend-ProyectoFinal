import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database import DATABASE_URL, fetch_one_as_dict, get_pg_connection

security = HTTPBearer(auto_error=False)

# Cache en memoria con TTL de 60 segundos para evitar llamadas HTTP redundantes a InsForge en cada request
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE_TTL = 60  # segundos


def _insforge_host() -> str:
    host = os.getenv("INSFORGE_HOST", "").strip().rstrip("/")
    if not host:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="InsForge no está configurado en el backend.",
        )
    return host


def authenticate_with_insforge(email: str, password: str) -> Dict[str, Any]:
    try:
        response = httpx.post(
            f"{_insforge_host()}/api/auth/sessions?client_type=server",
            json={"method": "password", "email": email, "password": password},
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo contactar con InsForge Auth.",
        ) from exc

    if response.status_code in (400, 401):
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
            detail="InsForge Auth no pudo completar el inicio de sesión.",
        )

    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="InsForge Auth devolvió una respuesta inválida.",
        ) from exc


def get_profile_by_auth_id(auth_user_id: str) -> Optional[Dict[str, Any]]:
    conn = get_pg_connection()
    if not conn:
        detail = (
            "No se pudo conectar con PostgreSQL."
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
                SELECT id, auth_user_id, nombres, apellidos, correo, rol, estado
                FROM users
                WHERE auth_user_id = %s::uuid
                LIMIT 1;
                """,
                (auth_user_id,),
            )
            return fetch_one_as_dict(cur)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo consultar el perfil del usuario.",
        ) from exc
    finally:
        conn.close()


def get_current_identity(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, str]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un token de InsForge.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials.strip()
    now = time.time()

    # Validación rápida en caché local para reducir latencia de red
    cached = _SESSION_CACHE.get(token)
    if cached and (now - cached["timestamp"] < _SESSION_CACHE_TTL):
        return cached["identity"]

    try:
        response = httpx.get(
            f"{_insforge_host()}/api/auth/sessions/current",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo validar la sesión con InsForge Auth.",
        ) from exc

    if response.status_code in (401, 403):
        _SESSION_CACHE.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión de InsForge no es válida o expiró.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not response.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="InsForge Auth no pudo validar la sesión.",
        )

    try:
        user = response.json().get("user")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="InsForge Auth devolvió una respuesta inválida.",
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
    profile = get_profile_by_auth_id(identity["id"])
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
