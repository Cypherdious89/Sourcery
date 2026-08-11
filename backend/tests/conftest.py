"""Shared fixtures.

Tests hit the real local Postgres (same one `docker compose up` provides) —
deliberately not mocked. This is a small project, and the database IS most of
the behavior worth testing: cache writes, cascading deletes, llm_calls rows.
Every fixture cleans up the exact rows it created.
"""

from __future__ import annotations

import pytest

from app.auth import LOCAL_DEV_USER_ID, LOCAL_DEV_SUB
from app.db import SessionLocal
from app.models import Notebook, User


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def local_dev_user(db):
    """The sentinel owner used when auth is disabled — mirrors app.auth."""
    user = db.get(User, LOCAL_DEV_USER_ID)
    if user is None:
        user = User(id=LOCAL_DEV_USER_ID, google_sub=LOCAL_DEV_SUB, name="Local Dev")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@pytest.fixture
def notebook(db, local_dev_user):
    """A throwaway notebook, deleted (cascading) after the test."""
    nb = Notebook(title="pytest fixture notebook", user_id=local_dev_user.id)
    db.add(nb)
    db.commit()
    db.refresh(nb)
    yield nb
    db.delete(nb)
    db.commit()


@pytest.fixture
def client():
    """TestClient against the real app, in auth-disabled (local-dev) mode.

    GOOGLE_CLIENT_ID is unset in the dev .env, so every request resolves to
    the sentinel local-dev user with no token needed.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
