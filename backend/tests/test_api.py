"""API-level smoke tests, in auth-disabled (local-dev) mode.

Deliberately does not exercise a real LLM call (no network, no cost, no
flakiness) — the gateway's own behavior is covered by test_gateway.py's faked
providers. These tests cover routing, ownership 404s, validation, and the
"no sources yet" branch, which needs no provider call at all.
"""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_config_reports_disabled(client):
    r = client.get("/auth/config")
    assert r.status_code == 200
    assert r.json() == {"auth_required": False}


def test_auth_me_returns_local_dev_user(client):
    r = client.get("/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Local Dev"


def test_notebook_crud_and_rename(client):
    created = client.post("/notebooks", json={"title": "api test notebook"})
    assert created.status_code == 201
    nb_id = created.json()["id"]

    listed = client.get("/notebooks")
    assert any(n["id"] == nb_id for n in listed.json())

    got = client.get(f"/notebooks/{nb_id}")
    assert got.status_code == 200
    assert got.json()["title"] == "api test notebook"

    renamed = client.patch(f"/notebooks/{nb_id}", json={"title": "renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "renamed"

    empty_rename = client.patch(f"/notebooks/{nb_id}", json={"title": "   "})
    assert empty_rename.status_code == 422

    deleted = client.delete(f"/notebooks/{nb_id}")
    assert deleted.status_code == 204

    gone = client.get(f"/notebooks/{nb_id}")
    assert gone.status_code == 404


def test_unknown_notebook_is_404_everywhere(client):
    fake_id = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/notebooks/{fake_id}").status_code == 404
    assert client.get(f"/notebooks/{fake_id}/sources").status_code == 404
    assert (
        client.post(f"/notebooks/{fake_id}/chat", json={"query": "hi"}).status_code
        == 404
    )


def test_chat_rejects_empty_query(client):
    created = client.post("/notebooks", json={"title": "empty query test"})
    nb_id = created.json()["id"]
    try:
        r = client.post(f"/notebooks/{nb_id}/chat", json={"query": "   "})
        assert r.status_code == 422
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_chat_with_no_sources_is_graceful_and_persists_messages(client):
    created = client.post("/notebooks", json={"title": "no sources test"})
    nb_id = created.json()["id"]
    try:
        r = client.post(f"/notebooks/{nb_id}/chat", json={"query": "anything?"})
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "none"
        assert body["citations"] == []
        assert body["cost_usd"] == 0.0

        messages = client.get(f"/notebooks/{nb_id}/messages").json()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "anything?"
        assert messages[1]["role"] == "assistant"
        # No llm_calls row exists for the no-sources branch, so metadata is None.
        assert messages[1]["provider"] is None
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_source_add_list_delete(client):
    created = client.post("/notebooks", json={"title": "source crud test"})
    nb_id = created.json()["id"]
    try:
        added = client.post(
            f"/notebooks/{nb_id}/sources",
            json={"url": "https://en.wikipedia.org/wiki/Test"},
        )
        assert added.status_code == 202
        source = added.json()
        assert source["status"] == "pending"
        assert source["type"] == "url"

        listed = client.get(f"/notebooks/{nb_id}/sources").json()
        assert any(s["id"] == source["id"] for s in listed)

        deleted = client.delete(f"/notebooks/{nb_id}/sources/{source['id']}")
        assert deleted.status_code == 204

        listed_after = client.get(f"/notebooks/{nb_id}/sources").json()
        assert all(s["id"] != source["id"] for s in listed_after)
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_search_status_shape(client):
    r = client.get("/search/status")
    assert r.status_code == 200
    assert isinstance(r.json()["configured"], bool)


def test_search_rejects_empty_query_without_calling_tavily(client):
    r = client.get("/search?q=")
    assert r.status_code == 422


def test_stats_shape(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "total_calls",
        "total_cost_usd",
        "cache_hit_rate",
        "fallback_count",
        "error_count",
        "avg_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "by_provider",
        "by_model",
        "by_status",
        "daily",
        "top_notebooks",
    ):
        assert key in body
    assert isinstance(body["by_provider"], list)
