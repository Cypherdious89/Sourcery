"""Google sign-in: verify an ID token and resolve it to a ``User`` row.

The frontend (Auth.js) obtains a Google ID token and sends it as
``Authorization: Bearer <id_token>``. A bearer header rather than a cookie is
deliberate — the frontend (Vercel) and API (Render) are different origins, so
cross-site cookies would need SameSite=None plus credentialed CORS.

**Auth is optional.** With no ``GOOGLE_CLIENT_ID`` configured the API runs in
local-dev mode: every request resolves to the sentinel ``local-dev`` user, so
the app still works without an OAuth client. Setting GOOGLE_CLIENT_ID switches
authentication on, and unauthenticated requests get a 401.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

logger = logging.getLogger("app.auth")

LOCAL_DEV_SUB = "local-dev"
LOCAL_DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000de")


def auth_enabled() -> bool:
    return bool(get_settings().google_client_id)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _local_dev_user(db: Session) -> User:
    """The sentinel owner used when auth is switched off."""
    user = db.scalar(select(User).where(User.google_sub == LOCAL_DEV_SUB))
    if user is None:
        user = User(
            id=LOCAL_DEV_USER_ID,
            google_sub=LOCAL_DEV_SUB,
            name="Local Dev",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _verify_google_token(token: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    settings = get_settings()
    try:
        # Verifies signature, expiry, issuer, and that `aud` is our client id.
        return google_id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.google_client_id
        )
    except ValueError as exc:
        # Raised for bad signature, expired token, or audience mismatch.
        raise _unauthorized(f"Invalid Google ID token: {exc}") from exc


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency resolving the caller to a ``User``."""
    if not auth_enabled():
        return _local_dev_user(db)

    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Missing bearer token")

    claims = _verify_google_token(authorization.split(" ", 1)[1].strip())
    sub = claims.get("sub")
    if not sub:
        raise _unauthorized("Token has no subject claim")

    user = db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        user = User(
            google_sub=sub,
            email=claims.get("email"),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("registered new user %s", claims.get("email") or sub)
    else:
        # Keep the profile fresh — users rename themselves and rotate avatars.
        changed = False
        for field, value in (
            ("email", claims.get("email")),
            ("name", claims.get("name")),
            ("picture", claims.get("picture")),
        ):
            if value and getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if changed:
            db.commit()
    return user
