from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, status

from .config import Settings


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(settings: Settings, username: str) -> str:
    payload = {
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.session_expiry_hours * 3600,
        "kind": "dashboard",
    }
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_raw).decode("utf-8").rstrip("=")
    signature = _sign(payload_b64, settings.session_secret)
    return f"{payload_b64}.{signature}"


def verify_session_token(token: str, settings: Settings) -> dict:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.") from exc

    expected = _sign(payload_b64, settings.session_secret)
    if not secrets.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature.")

    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_raw = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.") from exc

    if payload.get("kind") != "dashboard":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token kind.")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")

    return payload


def validate_admin_credentials(username: str, password: str, settings: Settings) -> bool:
    return secrets.compare_digest(username, settings.dashboard_admin_username) and secrets.compare_digest(
        password, settings.dashboard_admin_password
    )
