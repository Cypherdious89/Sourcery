"""Gateway contract tests (see SPEC "LLM Gateway Contract").

Providers are faked — these tests never make a network call — but the cache,
`llm_calls` logging, and cascading cleanup all hit the real local Postgres.
That's deliberate: the gateway's value IS what it writes to the database, so
mocking the DB would test nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app import gateway, rate_limits
from app.models import LLMCall, LLMCallStatus
from app.providers import (
    FatalProviderError,
    ProviderResponse,
    RetryableProviderError,
    StreamDelta,
)


class SuccessProvider:
    """Always succeeds, both buffered and streamed."""

    def __init__(self, name: str = "fake-primary", model: str = "fake-model-1"):
        self.name = name
        self.model = model
        self.calls = 0
        self.stream_calls = 0

    def generate(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            text="hello from " + self.name, prompt_tokens=10, completion_tokens=5
        )

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        self.stream_calls += 1
        yield StreamDelta(text="hello ")
        yield StreamDelta(text="from " + self.name)
        yield StreamDelta(final=True, prompt_tokens=10, completion_tokens=5)


class RetryableFailProvider:
    """Fails before producing anything — the pre-first-token fallback case."""

    def __init__(self, name: str = "fake-primary", model: str = "fake-model-1"):
        self.name = name
        self.model = model
        self.calls = 0
        self.stream_calls = 0

    def generate(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        raise RetryableProviderError("429 rate limited")

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        self.stream_calls += 1
        raise RetryableProviderError("429 rate limited")
        yield  # pragma: no cover — unreachable; makes this a generator function


class FatalFailProvider:
    """Fails in a way that must never be retried against the fallback."""

    def __init__(self, name: str = "fake-primary", model: str = "fake-model-1"):
        self.name = name
        self.model = model
        self.calls = 0
        self.stream_calls = 0

    def generate(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        raise FatalProviderError("400 bad request")

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        self.stream_calls += 1
        raise FatalProviderError("400 bad request")
        yield  # pragma: no cover


class MidStreamFailProvider:
    """Emits real text, THEN fails — the no-splicing case."""

    def __init__(self, name: str = "fake-primary", model: str = "fake-model-1"):
        self.name = name
        self.model = model
        self.calls = 0
        self.stream_calls = 0

    def generate(self, prompt: str) -> ProviderResponse:  # pragma: no cover
        raise AssertionError("buffered generate() should not be called by stream tests")

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        self.stream_calls += 1
        yield StreamDelta(text="partial answer")
        raise RetryableProviderError("connection reset mid-stream")


def _error_rows(db, notebook_id):
    return list(
        db.scalars(
            select(LLMCall).where(
                LLMCall.notebook_id == notebook_id,
                LLMCall.status == LLMCallStatus.error,
            )
        )
    )


# --------------------------------------------------------------------------- #
# call_llm — buffered
# --------------------------------------------------------------------------- #
def test_cache_miss_then_exact_hit(db, notebook, monkeypatch):
    primary = SuccessProvider()
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary])

    first = gateway.call_llm("What is a widget?", str(notebook.id))
    assert first.cache_hit is False
    assert first.status == "ok"
    assert primary.calls == 1

    second = gateway.call_llm("What is a widget?", str(notebook.id))
    assert second.cache_hit is True
    assert second.cost_usd == 0.0
    assert second.text == first.text
    assert primary.calls == 1, "cache hit must not call the provider again"

    rows = list(
        db.scalars(select(LLMCall).where(LLMCall.notebook_id == notebook.id))
    )
    assert len(rows) == 2
    assert [r.cache_hit for r in rows] == [False, True]


def test_primary_retryable_falls_over_to_working_fallback(db, notebook, monkeypatch):
    primary = RetryableFailProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])

    result = gateway.call_llm("unique prompt A", str(notebook.id))

    assert result.status == "fallback"
    assert result.provider == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1

    row = db.scalar(
        select(LLMCall).where(
            LLMCall.notebook_id == notebook.id, LLMCall.status == LLMCallStatus.fallback
        )
    )
    assert row is not None
    assert row.provider == "fallback"


def test_both_providers_fail_raises_and_logs_error_row(db, notebook, monkeypatch):
    primary = RetryableFailProvider(name="primary")
    fallback = RetryableFailProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])

    with pytest.raises(gateway.GatewayError):
        gateway.call_llm("unique prompt B", str(notebook.id))

    assert primary.calls == 1
    assert fallback.calls == 1
    assert len(_error_rows(db, notebook.id)) == 1


def test_fatal_error_never_retries_fallback(db, notebook, monkeypatch):
    """A fatal error (bad request, auth, safety block) is not worth retrying
    with the same prompt against a different provider — SPEC step 3 only
    triggers failover on timeout/5xx/rate-limit."""
    primary = FatalFailProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])

    with pytest.raises(gateway.GatewayError):
        gateway.call_llm("unique prompt C", str(notebook.id))

    assert primary.calls == 1
    assert fallback.calls == 0, "a fatal error must never fall over"
    assert len(_error_rows(db, notebook.id)) == 1


def test_no_fallback_configured_fails_immediately(db, notebook, monkeypatch):
    primary = RetryableFailProvider(name="primary")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary])

    with pytest.raises(gateway.GatewayError):
        gateway.call_llm("unique prompt D", str(notebook.id))

    assert primary.calls == 1
    assert len(_error_rows(db, notebook.id)) == 1


# --------------------------------------------------------------------------- #
# Rate-limit-aware chain (see app/rate_limits.py)
# --------------------------------------------------------------------------- #
def test_rate_limited_candidate_is_skipped_without_being_called(
    db, notebook, monkeypatch
):
    """A candidate proactively identified as out of quota is never called at
    all — the chain moves straight to the next one with headroom."""
    primary = SuccessProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])
    monkeypatch.setattr(
        rate_limits,
        "has_headroom",
        lambda db, provider, model: provider != "primary",
    )

    result = gateway.call_llm("unique rate-limit prompt A", str(notebook.id))

    assert result.status == "fallback"
    assert result.provider == "fallback"
    assert primary.calls == 0, "a rate-limited candidate must never be called"
    assert fallback.calls == 1


def test_all_candidates_rate_limited_raises_with_flag_and_calls_nothing(
    db, notebook, monkeypatch
):
    primary = SuccessProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])
    monkeypatch.setattr(rate_limits, "has_headroom", lambda db, provider, model: False)

    with pytest.raises(gateway.GatewayError) as exc_info:
        gateway.call_llm("unique rate-limit prompt B", str(notebook.id))

    assert exc_info.value.rate_limited is True
    assert primary.calls == 0
    assert fallback.calls == 0
    assert len(_error_rows(db, notebook.id)) == 1


def test_stream_rate_limited_candidate_is_skipped_without_being_called(
    db, notebook, monkeypatch
):
    primary = SuccessProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])
    monkeypatch.setattr(
        rate_limits,
        "has_headroom",
        lambda db, provider, model: provider != "primary",
    )

    chunks = list(gateway.stream_llm("unique stream rate-limit prompt", str(notebook.id)))

    assert primary.stream_calls == 0, "a rate-limited candidate must never be called"
    assert fallback.stream_calls == 1
    final = [c.result for c in chunks if c.result is not None][0]
    assert final.status == "fallback"
    assert final.provider == "fallback"


# --------------------------------------------------------------------------- #
# stream_llm
# --------------------------------------------------------------------------- #
def test_stream_cache_hit_replays_full_text_then_result(db, notebook, monkeypatch):
    primary = SuccessProvider()
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary])

    prompt = "streamed widget question"
    first_chunks = list(gateway.stream_llm(prompt, str(notebook.id)))
    assert primary.stream_calls == 1
    final = [c for c in first_chunks if c.result is not None]
    assert len(final) == 1
    assert final[0].result.cache_hit is False

    second_chunks = list(gateway.stream_llm(prompt, str(notebook.id)))
    assert primary.stream_calls == 1, "cache hit must not stream from the provider again"
    texts = [c.text for c in second_chunks if c.text]
    assert "".join(texts) == final[0].result.text
    results = [c.result for c in second_chunks if c.result is not None]
    assert results[0].cache_hit is True
    assert results[0].cost_usd == 0.0


def test_stream_falls_over_before_first_token(db, notebook, monkeypatch):
    primary = RetryableFailProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])

    chunks = list(gateway.stream_llm("unique stream prompt A", str(notebook.id)))

    assert primary.stream_calls == 1
    assert fallback.stream_calls == 1
    text = "".join(c.text for c in chunks if c.text)
    assert text == "hello from fallback"
    final = [c.result for c in chunks if c.result is not None][0]
    assert final.status == "fallback"
    assert final.provider == "fallback"


def test_stream_mid_stream_failure_does_not_fall_over(db, notebook, monkeypatch):
    """Once tokens are on the wire, a failure must raise — never silently
    restart against the fallback, which would splice two different answers
    together in front of the user."""
    primary = MidStreamFailProvider(name="primary")
    fallback = SuccessProvider(name="fallback")
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary, fallback])

    seen = []
    with pytest.raises(gateway.GatewayError):
        for chunk in gateway.stream_llm("unique stream prompt B", str(notebook.id)):
            seen.append(chunk)

    assert len(seen) == 1
    assert seen[0].text == "partial answer"
    assert fallback.stream_calls == 0, "must never fall over after streaming has started"
    assert len(_error_rows(db, notebook.id)) == 1


# --------------------------------------------------------------------------- #
# Semantic cache
# --------------------------------------------------------------------------- #
def test_semantic_cache_hits_on_paraphrase_same_context(db, notebook, monkeypatch):
    primary = SuccessProvider()
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary])
    context_hash = "same-retrieved-chunks"

    first = gateway.call_llm(
        "PROMPT ONE What is retrieval-augmented generation?",
        str(notebook.id),
        semantic=gateway.SemanticContext(
            query="What is retrieval-augmented generation?", context_hash=context_hash
        ),
    )
    assert first.cache_hit is False
    assert primary.calls == 1

    # Different prompt text (so the exact-match key misses) but a near-identical
    # paraphrase of the query, same retrieval context.
    second = gateway.call_llm(
        "PROMPT TWO what is retrieval augmented generation",
        str(notebook.id),
        semantic=gateway.SemanticContext(
            query="what is retrieval augmented generation", context_hash=context_hash
        ),
    )
    assert second.cache_hit is True
    assert second.text == first.text
    assert primary.calls == 1, "a semantic hit must not call the provider again"


def test_semantic_cache_misses_different_context_hash(db, notebook, monkeypatch):
    """A paraphrase must NOT match if retrieval returned different chunks —
    otherwise adding/removing a source would keep serving a stale answer."""
    primary = SuccessProvider()
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary])

    gateway.call_llm(
        "PROMPT THREE What is retrieval-augmented generation?",
        str(notebook.id),
        semantic=gateway.SemanticContext(
            query="What is retrieval-augmented generation?", context_hash="context-A"
        ),
    )
    assert primary.calls == 1

    second = gateway.call_llm(
        "PROMPT FOUR what is retrieval augmented generation",
        str(notebook.id),
        semantic=gateway.SemanticContext(
            query="what is retrieval augmented generation", context_hash="context-B"
        ),
    )
    assert second.cache_hit is False
    assert primary.calls == 2


def test_semantic_cache_misses_unrelated_question(db, notebook, monkeypatch):
    primary = SuccessProvider()
    monkeypatch.setattr(gateway, "_build_provider_chain", lambda: [primary])
    context_hash = "shared-context"

    gateway.call_llm(
        "PROMPT FIVE What is retrieval-augmented generation?",
        str(notebook.id),
        semantic=gateway.SemanticContext(
            query="What is retrieval-augmented generation?", context_hash=context_hash
        ),
    )
    assert primary.calls == 1

    second = gateway.call_llm(
        "PROMPT SIX Which companies sell vector databases?",
        str(notebook.id),
        semantic=gateway.SemanticContext(
            query="Which companies sell vector databases?", context_hash=context_hash
        ),
    )
    assert second.cache_hit is False
    assert primary.calls == 2
