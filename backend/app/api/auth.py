from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings
from ..dependencies import get_app_settings, get_current_admin_username, require_admin_token
from ..schemas import LoginRequest, LoginResponse, SessionRead
from ..security import create_session_token, validate_admin_credentials


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, settings: Settings = Depends(get_app_settings)):
    if not validate_admin_credentials(payload.username, payload.password, settings):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

    token = create_session_token(settings, payload.username)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        username=payload.username,
        expires_in=settings.session_expiry_hours * 3600,
    )


@router.get("/me", response_model=SessionRead, dependencies=[Depends(require_admin_token)])
def me(
    username: str = Depends(get_current_admin_username),
):
    return SessionRead(username=username, authenticated=True)
