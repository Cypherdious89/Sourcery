"""Shared fixtures.

Tests hit the real local Postgres (same one `docker compose up` provides) —
deliberately not mocked. This is a small project, and the database IS most of
the behavior worth testing: cache writes, cascading deletes, llm_calls rows.
Every fixture cleans up the exact rows it created.
"""

from __future__ import annotations

import hashlib
import math
import random
import re

import pytest

from app.auth import LOCAL_DEV_USER_ID, LOCAL_DEV_SUB
from app.db import SessionLocal
from app.models import EMBEDDING_DIM, Notebook, User


def _fake_embed_one(text: str) -> list[float]:
    """Deterministic, network-free stand-in for a real embedding.

    Buckets each word into a stable pseudo-random unit vector (seeded off
    its own hash) and sums them, so texts sharing the same words — case,
    punctuation, and word order aside — land at cosine similarity 1.0, while
    texts with unrelated words land far apart. That's exactly the property
    app.gateway's semantic-cache tests need (paraphrase => hit, different
    question => miss) without ever calling the real Gemini embedding API.
    """
    words = re.findall(r"[a-z0-9]+", text.lower()) or ["__empty__"]
    vec = [0.0] * EMBEDDING_DIM
    for word in words:
        seed = int(hashlib.sha256(word.encode()).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        for i in range(EMBEDDING_DIM):
            vec[i] += rng.uniform(-1.0, 1.0)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """Every test gets a deterministic embedding backend — no network calls,
    no GEMINI_API_KEY needed, matching this suite's "no network calls"
    guarantee even though production embeddings now call a hosted API."""
    from app import embeddings

    monkeypatch.setattr(embeddings, "embed_text", _fake_embed_one)
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts: [_fake_embed_one(t) for t in texts]
    )


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
