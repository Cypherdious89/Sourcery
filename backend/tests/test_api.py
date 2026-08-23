"""API-level smoke tests, in auth-disabled (local-dev) mode.

Deliberately does not exercise a real LLM call (no network, no cost, no
flakiness) — the gateway's own behavior is covered by test_gateway.py's faked
providers. These tests cover routing, ownership 404s, validation, and the
"no sources yet" branch, which needs no provider call at all.
"""

from __future__ import annotations

import uuid


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


def test_failed_source_response_includes_safe_failure_details(client, db):
    """The source API must expose the safe failure payload used by the UI."""
    from app.models import Source, SourceStatus, SourceType

    created = client.post("/notebooks", json={"title": "failure details"})
    nb_id = created.json()["id"]
    try:
        source = Source(
            notebook_id=uuid.UUID(nb_id),
            type=SourceType.pdf,
            original_name_or_url="scanned.pdf",
            status=SourceStatus.failed,
            progress=0,
        )
        source.error_code = "EMPTY_CONTENT"
        source.error_message = "No extractable text was found in this source."
        db.add(source)
        db.commit()
        db.refresh(source)

        response = client.get(f"/notebooks/{nb_id}/sources/{source.id}")

        assert response.status_code == 200
        assert response.json()["error_code"] == "EMPTY_CONTENT"
        assert response.json()["error_message"] == (
            "No extractable text was found in this source."
        )
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_retry_requeues_a_failed_url_source(client, db):
    from app.models import Source, SourceStatus

    created = client.post("/notebooks", json={"title": "retry test"})
    nb_id = created.json()["id"]
    try:
        added = client.post(
            f"/notebooks/{nb_id}/sources",
            json={"url": "https://en.wikipedia.org/wiki/Test"},
        )
        source_id = added.json()["id"]

        # Force it into a failed state directly, rather than depending on a
        # real network failure to exercise the retry path.
        row = db.get(Source, uuid.UUID(source_id))
        row.status = SourceStatus.failed
        row.progress = 0
        db.commit()

        retried = client.post(f"/notebooks/{nb_id}/sources/{source_id}/retry")
        assert retried.status_code == 202
        # The response body is a pre-task snapshot (pending) — the background
        # task itself runs synchronously in TestClient but mutates the DB
        # after the response is serialized, so re-fetch to see the outcome.
        assert retried.json()["status"] == "pending"
        after = client.get(f"/notebooks/{nb_id}/sources/{source_id}").json()
        assert after["status"] in ("ready", "failed")
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_retry_rejects_a_non_failed_source(client, db):
    from app.models import Source, SourceStatus

    created = client.post("/notebooks", json={"title": "retry reject test"})
    nb_id = created.json()["id"]
    try:
        added = client.post(
            f"/notebooks/{nb_id}/sources",
            json={"url": "https://en.wikipedia.org/wiki/Test"},
        )
        source_id = added.json()["id"]
        row = db.get(Source, uuid.UUID(source_id))
        row.status = SourceStatus.ready
        db.commit()

        retried = client.post(f"/notebooks/{nb_id}/sources/{source_id}/retry")
        assert retried.status_code == 409
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_retry_rejects_file_type_sources(client, db):
    """File bytes aren't persisted past the original request, so a failed
    PDF/DOCX upload can't be retried server-side — only re-uploaded."""
    from app.models import Source, SourceStatus, SourceType

    created = client.post("/notebooks", json={"title": "retry file reject test"})
    nb_id = created.json()["id"]
    try:
        row = Source(
            notebook_id=uuid.UUID(nb_id),
            type=SourceType.pdf,
            original_name_or_url="paper.pdf",
            status=SourceStatus.failed,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        retried = client.post(f"/notebooks/{nb_id}/sources/{row.id}/retry")
        assert retried.status_code == 422
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
        "rate_limits",
    ):
        assert key in body
    assert isinstance(body["by_provider"], list)


def test_stats_rate_limits_cover_every_configured_model(client):
    from app import rate_limits

    body = client.get("/stats").json()
    seen = {(r["provider"], r["model"]) for r in body["rate_limits"]}
    assert seen == set(rate_limits.LIMITS.keys())
    for r in body["rate_limits"]:
        assert r["requests_today"] >= 0
        assert r["requests_this_minute"] >= 0


def test_regenerate_updates_message_in_place_bypassing_cache(
    client, db, notebook, monkeypatch
):
    from app import gateway
    from app.models import (
        ChatMessage,
        Chunk,
        MessageRole,
        Source,
        SourceStatus,
        SourceType,
    )
    from tests.conftest import _fake_embed_one

    source = Source(
        notebook_id=notebook.id,
        type=SourceType.url,
        original_name_or_url="https://example.com/widgets",
        status=SourceStatus.ready,
        progress=100,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    content = "Widgets are small mechanical devices."
    chunk = Chunk(
        source_id=source.id,
        notebook_id=notebook.id,
        content=content,
        embedding=_fake_embed_one(content),
        chunk_index=0,
    )
    db.add(chunk)
    db.commit()

    user_msg = ChatMessage(
        notebook_id=notebook.id, role=MessageRole.user, content="What is a widget?"
    )
    db.add(user_msg)
    db.commit()
    assistant_msg = ChatMessage(
        notebook_id=notebook.id,
        role=MessageRole.assistant,
        content="OLD ANSWER",
        cited_chunk_ids=[],
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    seen_bypass_cache = []

    def fake_call_llm(prompt, nb_id, *, semantic=None, bypass_cache=False):
        seen_bypass_cache.append(bypass_cache)
        return gateway.LLMResult(
            text="NEW ANSWER [S1]",
            provider="fake",
            model="fake-model",
            status="ok",
            cache_hit=False,
            latency_ms=5,
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
            llm_call_id=None,
        )

    monkeypatch.setattr(gateway, "call_llm", fake_call_llm)

    resp = client.post(
        f"/notebooks/{notebook.id}/messages/{assistant_msg.id}/regenerate"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "NEW ANSWER [S1]"
    assert seen_bypass_cache == [True], "regenerate must bypass the LLM cache"

    db.refresh(assistant_msg)
    assert assistant_msg.content == "NEW ANSWER [S1]"

    # The transcript didn't grow — same message updated in place, not a new one.
    messages = client.get(f"/notebooks/{notebook.id}/messages").json()
    assert len(messages) == 2


def test_regenerate_404s_for_unknown_message(client, notebook):
    fake_id = "00000000-0000-0000-0000-000000000001"
    resp = client.post(f"/notebooks/{notebook.id}/messages/{fake_id}/regenerate")
    assert resp.status_code == 404


def test_regenerate_409s_with_no_preceding_user_message(client, db, notebook):
    from app.models import ChatMessage, MessageRole

    # An assistant message with no user turn before it shouldn't happen in
    # practice, but the endpoint must fail cleanly rather than 500 on it.
    assistant_msg = ChatMessage(
        notebook_id=notebook.id,
        role=MessageRole.assistant,
        content="orphaned answer",
        cited_chunk_ids=[],
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    resp = client.post(
        f"/notebooks/{notebook.id}/messages/{assistant_msg.id}/regenerate"
    )
    assert resp.status_code == 409


def test_create_notebook_without_title_defaults_to_untitled(client):
    created = client.post("/notebooks", json={})
    assert created.status_code == 201
    nb_id = created.json()["id"]
    try:
        assert created.json()["title"] == "Untitled"
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_create_notebook_with_no_body_defaults_to_untitled(client):
    created = client.post("/notebooks")
    assert created.status_code == 201
    nb_id = created.json()["id"]
    try:
        assert created.json()["title"] == "Untitled"
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_ingestion_auto_titles_an_untitled_notebook(client):
    created = client.post("/notebooks", json={})
    nb_id = created.json()["id"]
    try:
        client.post(
            f"/notebooks/{nb_id}/sources",
            json={"url": "https://en.wikipedia.org/wiki/Test"},
        )
        notebook = client.get(f"/notebooks/{nb_id}").json()
        assert notebook["title"] != "Untitled"
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_ingestion_does_not_rename_an_already_titled_notebook(client):
    created = client.post("/notebooks", json={"title": "My real title"})
    nb_id = created.json()["id"]
    try:
        client.post(
            f"/notebooks/{nb_id}/sources",
            json={"url": "https://en.wikipedia.org/wiki/Test"},
        )
        notebook = client.get(f"/notebooks/{nb_id}").json()
        assert notebook["title"] == "My real title"
    finally:
        client.delete(f"/notebooks/{nb_id}")


def test_export_notebook_returns_markdown_with_sources_and_transcript(
    client, db, notebook
):
    from app.models import ChatMessage, MessageRole, Source, SourceStatus, SourceType

    source = Source(
        notebook_id=notebook.id,
        type=SourceType.url,
        original_name_or_url="https://example.com/article",
        status=SourceStatus.ready,
        progress=100,
    )
    db.add(source)
    db.add(
        ChatMessage(
            notebook_id=notebook.id, role=MessageRole.user, content="# Not a heading"
        )
    )
    db.add(
        ChatMessage(
            notebook_id=notebook.id,
            role=MessageRole.assistant,
            content="* A real bullet from the assistant",
            cited_chunk_ids=[],
        )
    )
    db.commit()

    resp = client.get(f"/notebooks/{notebook.id}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment" in resp.headers["content-disposition"]

    body = resp.text
    assert f"# {notebook.title}" in body
    assert "https://example.com/article" in body
    # A pasted "# heading" in the user's own question must not render as one.
    assert "\\# Not a heading" in body
    # The assistant's own markdown must NOT be escaped.
    assert "* A real bullet from the assistant" in body


def test_export_404s_for_unowned_notebook(client):
    fake_id = "00000000-0000-0000-0000-000000000001"
    resp = client.get(f"/notebooks/{fake_id}/export")
    assert resp.status_code == 404
