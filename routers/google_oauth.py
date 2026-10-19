import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from dotenv import load_dotenv
from requests import HTTPError

from auth import get_current_user, require_role
from database import load_db, save_db

load_dotenv()

router = APIRouter(prefix="/google", tags=["Google OAuth"])

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
]
DEFAULT_REDIRECT_URI = "http://localhost:8000/google/callback"
OAUTH_STATE_TTL_MINUTES = 10


def required_setting(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise HTTPException(
            status_code=500,
            detail=f"La configuración {name} no está definida en el backend",
        )
    return value


def client_secrets_file() -> str:
    path = Path(required_setting("GOOGLE_CLIENT_SECRETS_FILE"))
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail="No se encontró el archivo de credenciales OAuth configurado",
        )
    return str(path)


def token_store_path() -> Path:
    return Path(required_setting("GOOGLE_TOKEN_STORE"))


def load_tokens() -> dict[str, dict]:
    path = token_store_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as token_file:
            data = json.load(token_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="No se pudo leer el almacén OAuth") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="El almacén OAuth tiene un formato inválido")
    return data


def save_tokens(tokens: dict[str, dict]) -> None:
    path = token_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as token_file:
            json.dump(tokens, token_file, indent=2)
        temporary_path.replace(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="No se pudieron guardar las credenciales OAuth") from exc


def oauth_flow(
    state: str | None = None, code_verifier: str | None = None
) -> Flow:
    flow = Flow.from_client_secrets_file(
        client_secrets_file(),
        scopes=SCOPES,
        state=state,
        code_verifier=code_verifier,
    )
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    return flow


def save_oauth_state(
    state: str, user_email: str, code_verifier: str | None
) -> None:
    db = load_db()
    states = db.setdefault("googleOAuthStates", {})
    states[state] = {
        "userEmail": user_email,
        "codeVerifier": code_verifier,
        "expiresAt": (
            datetime.now(timezone.utc)
            + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)
        ).isoformat(),
    }
    save_db(db)


def consume_oauth_state(state: str) -> dict[str, str]:
    db = load_db()
    states = db.get("googleOAuthStates", {})
    state_data = states.pop(state, None)
    db["googleOAuthStates"] = states
    save_db(db)

    if not state_data:
        raise HTTPException(status_code=400, detail="Estado OAuth inválido o expirado")
    try:
        expires_at = datetime.fromisoformat(
            state_data["expiresAt"].replace("Z", "+00:00")
        )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=400, detail="Estado OAuth inválido o expirado")
        return {
            "userEmail": state_data["userEmail"],
            "codeVerifier": state_data["codeVerifier"],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Estado OAuth inválido o expirado") from exc


@router.get("/authorize")
def authorize_google(
    current_user: dict = Depends(require_role(["docente"])),
):
    state = secrets.token_urlsafe(32)
    flow = oauth_flow(state)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    save_oauth_state(state, current_user["correo"], flow.code_verifier)
    return {"authorizationUrl": authorization_url}


@router.get("/callback")
def google_callback(code: str, state: str):
    state_data = consume_oauth_state(state)
    user_email = state_data["userEmail"]

    flow = oauth_flow(state, state_data["codeVerifier"])
    try:
        flow.fetch_token(code=code)
    except OAuth2Error as exc:
        error_code = getattr(exc, "error", None) or "oauth_token_exchange_failed"
        error_description = getattr(exc, "description", None)
        detail = f"Google rechazó la autorización ({error_code})"
        if error_description:
            detail += f": {error_description}"
        raise HTTPException(status_code=400, detail=detail) from exc

    credentials = flow.credentials
    if not credentials.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Google no entregó refresh token; revoca el acceso y vuelve a autorizar",
        )

    db = load_db()
    user = next((item for item in db.get("users", []) if item.get("correo") == user_email), None)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario local no encontrado")

    try:
        identity_response = AuthorizedSession(credentials).get(
            "https://openidconnect.googleapis.com/v1/userinfo"
        )
        identity_response.raise_for_status()
        identity = identity_response.json()
    except (ValueError, KeyError, OSError, HTTPError) as exc:
        raise HTTPException(status_code=400, detail="No se pudo validar la identidad de Google") from exc

    user["googleEmail"] = identity.get("email")
    if not user["googleEmail"] or identity.get("email_verified") is not True:
        raise HTTPException(status_code=400, detail="La cuenta Google no tiene un correo verificable")
    user["googleAccountVerified"] = bool(user["googleEmail"])
    db["users"] = db.get("users", [])
    save_db(db)

    tokens = load_tokens()
    tokens[user_email] = json.loads(credentials.to_json())
    save_tokens(tokens)

    frontend_url = os.getenv("GOOGLE_FRONTEND_SUCCESS_URL")
    if frontend_url:
        return RedirectResponse(f"{frontend_url}?google=connected")
    return {"mensaje": "Cuenta Google vinculada correctamente", "googleEmail": user["googleEmail"]}


@router.get("/status")
def google_status(current_user: dict = Depends(get_current_user)):
    tokens = load_tokens()
    return {
        "connected": current_user["correo"] in tokens
        and bool(current_user.get("googleAccountVerified")),
        "googleEmail": current_user.get("googleEmail"),
    }


@router.post("/disconnect")
def disconnect_google(current_user: dict = Depends(get_current_user)):
    tokens = load_tokens()
    tokens.pop(current_user["correo"], None)
    save_tokens(tokens)

    db = load_db()
    user = next((item for item in db.get("users", []) if item.get("correo") == current_user["correo"]), None)
    if user is not None:
        user.pop("googleEmail", None)
        user["googleAccountVerified"] = False
        save_db(db)
    return {"mensaje": "Cuenta Google desvinculada correctamente"}


def credentials_for_user(user: dict) -> Credentials:
    credentials_data = load_tokens().get(user["correo"])
    if not credentials_data:
        raise HTTPException(status_code=409, detail="El docente aún no ha vinculado su cuenta Google")
    credentials = Credentials.from_authorized_user_info(credentials_data, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        tokens = load_tokens()
        tokens[user["correo"]] = json.loads(credentials.to_json())
        save_tokens(tokens)
    return credentials
