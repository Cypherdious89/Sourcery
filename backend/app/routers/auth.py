"""Auth status and current-user endpoints.

``/auth/config`` lets the frontend discover whether sign-in is required before
it renders anything, mirroring how ``/search/status`` gates web search.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import auth_enabled, get_current_user
from app.models import User
from app.schemas import AuthConfig, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfig)
def auth_config() -> AuthConfig:
    """Public: whether Google sign-in is switched on for this deployment."""
    return AuthConfig(auth_required=auth_enabled())


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    """The caller's profile. 401 when auth is on and the token is bad/missing."""
    return user
