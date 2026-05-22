from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .database import get_db
from .security import verify_session_token


bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings() -> Settings:
    return get_settings()


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_app_settings),
) -> None:
    _resolve_admin_identity(credentials, settings)


def get_current_admin_username(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_app_settings),
) -> str:
    return _resolve_admin_identity(credentials, settings)


def _resolve_admin_identity(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> str:
    provided = credentials.credentials if credentials else ""
    legacy_expected = settings.admin_api_token.strip()

    if not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if legacy_expected and provided == legacy_expected:
        return settings.dashboard_admin_username

    payload = verify_session_token(provided, settings)
    return str(payload.get("sub", settings.dashboard_admin_username))


__all__ = ["get_db", "get_app_settings", "require_admin_token", "get_current_admin_username"]
