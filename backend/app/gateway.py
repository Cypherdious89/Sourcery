"""Resilient LLM gateway (see SPEC "LLM Gateway Contract").

Single entrypoint ``call_llm(prompt, notebook_id) -> LLMResult``:

1. Hash ``(notebook_id, normalized_prompt)`` and check the ``llm_cache`` table.
   On a hit, return immediately (``cache_hit=True``) with only the lookup
   latency and no provider call.
2. On a miss, call the primary provider (Gemini Flash).
3. On a timeout/5xx/rate-limit from the primary, retry once against the
   fallback provider (Groq, or Gemini's fallback model if no Groq key).
   ``status="fallback"``.
4. On success, write the response to ``llm_cache`` and log a row to ``llm_calls``.
5. On failure of both providers, log an ``error`` row and raise ``GatewayError``.

Callers are provider-agnostic — they never import a provider SDK. The gateway
owns its own DB session so the contract signature stays clean.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db import SessionLocal
from app.models import LLMCache, LLMCall, LLMCallStatus
from app.providers import (
    FatalProviderError,
    GeminiProvider,
    GroqProvider,
    Provider,
    ProviderResponse,
    RetryableProviderError,
    StreamDelta,
    StubProvider,
)

logger = logging.getLogger("app.gateway")
_settings = get_settings()

# USD pricing per 1M tokens (input, output), paid tier, text modality.
# Gemini: https://ai.google.dev/gemini-api/docs/pricing
# Groq's free tier costs $0 — see https://console.groq.com/docs/rate-limits.
# If you switch GROQ_FALLBACK_MODEL to a paid Groq plan, add its real rate
# here (https://groq.com/pricing) or cost_usd will silently under-report.
# adjust as rates move. A model missing from this table costs 0.0, so keep it
# in sync with the GEMINI_*_MODEL / GROQ_FALLBACK_MODEL settings.
_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3-flash-preview": (0.50, 3.00),
    # Retired for new API keys (404) but kept for historical llm_calls rows.
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "llama-3.3-70b-versatile": (0.0, 0.0),
    "offline-deterministic": (0.0, 0.0),
}


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    status: str
    cache_hit: bool
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float
    # Internal linkage so the caller can attach this call to a chat message.
    llm_call_id: uuid.UUID | None = None


@dataclass
class StreamChunk:
    """One step of a streamed gateway call.

    ``text`` carries an incremental delta. Exactly one terminal chunk carries
    ``result`` with the full metadata (provider, cost, latency, cache_hit)
    once the generation has finished.
    """

    text: str | None = None
    result: LLMResult | None = None


class GatewayError(Exception):
    """Raised when every provider fails; caller turns it into a user message."""

    def __init__(self, message: str, *, llm_call_id: uuid.UUID | None = None) -> None:
        super().__init__(message)
        self.llm_call_id = llm_call_id


def _normalize_prompt(prompt: str) -> str:
    return " ".join(prompt.split()).strip()


def _cache_key(notebook_id: str, prompt: str) -> str:
    payload = f"{notebook_id}\n{_normalize_prompt(prompt)}".encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass
class SemanticContext:
    """Everything needed to attempt a semantic (paraphrase) cache hit.

    ``context_hash`` fingerprints the retrieved chunks and conversation
    history: a paraphrase may only reuse a cached answer when the surrounding
    context is byte-identical, otherwise adding a source would keep serving
    the stale answer.
    """

    query: str
    context_hash: str


def _lookup_cache(
    db, key: str, nb_uuid: uuid.UUID, semantic: SemanticContext | None
) -> LLMCache | None:
    """Exact cache lookup, falling back to a semantic match on the query."""
    exact = db.scalar(select(LLMCache).where(LLMCache.cache_key == key))
    if exact is not None:
        return exact

    if semantic is None or not _settings.semantic_cache_enabled:
        return None

    from app import embeddings

    query_embedding = embeddings.embed_text(semantic.query)
    distance = LLMCache.query_embedding.cosine_distance(query_embedding).label(
        "distance"
    )
    row = db.execute(
        select(LLMCache, distance)
        .where(
            LLMCache.notebook_id == nb_uuid,
            LLMCache.context_hash == semantic.context_hash,
            LLMCache.query_embedding.isnot(None),
        )
        .order_by(distance)
        .limit(1)
    ).first()
    if row is None:
        return None

    entry, dist = row
    similarity = 1.0 - float(dist)
    if similarity < _settings.semantic_cache_threshold:
        return None
    logger.info(
        "semantic cache hit (similarity %.3f): %r ~ %r",
        similarity,
        semantic.query,
        entry.query_text,
    )
    return entry


def _estimate_cost(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> float:
    rates = _PRICING.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    pt = prompt_tokens or 0
    ct = completion_tokens or 0
    return round(pt / 1_000_000 * in_rate + ct / 1_000_000 * out_rate, 6)


def _build_providers() -> tuple[Provider, Provider | None]:
    """Return (primary, fallback) per configured API keys."""
    timeout = _settings.llm_timeout_seconds
    if _settings.gemini_api_key:
        primary: Provider = GeminiProvider(
            _settings.gemini_api_key, _settings.gemini_primary_model, timeout
        )
        if _settings.groq_api_key:
            fallback: Provider | None = GroqProvider(
                _settings.groq_api_key, _settings.groq_fallback_model, timeout
            )
        else:
            # Same provider, more capable model — SPEC's fallback options are
            # "Gemini 2.5 Pro or OpenAI"; Groq replaces OpenAI (see
            # providers.py), so this is the remaining same-provider option.
            fallback = GeminiProvider(
                _settings.gemini_api_key, _settings.gemini_fallback_model, timeout
            )
        return primary, fallback
    if _settings.groq_api_key:
        return (
            GroqProvider(
                _settings.groq_api_key, _settings.groq_fallback_model, timeout
            ),
            None,
        )
    # No keys: offline stub, no fallback needed.
    return StubProvider(), None


def _semantic_values(semantic: SemanticContext | None) -> dict:
    """Columns enabling a later paraphrase hit on this entry."""
    if semantic is None or not _settings.semantic_cache_enabled:
        return {}
    from app import embeddings

    return {
        "query_text": semantic.query,
        "query_embedding": embeddings.embed_text(semantic.query),
        "context_hash": semantic.context_hash,
    }


def call_llm(
    prompt: str,
    notebook_id: str,
    *,
    semantic: SemanticContext | None = None,
) -> LLMResult:
    key = _cache_key(notebook_id, prompt)
    nb_uuid = uuid.UUID(str(notebook_id))
    db = SessionLocal()
    try:
        # 1. Cache lookup ------------------------------------------------------
        t0 = perf_counter()
        cached = _lookup_cache(db, key, nb_uuid, semantic)
        if cached is not None:
            latency_ms = max(0, int((perf_counter() - t0) * 1000))
            call = LLMCall(
                notebook_id=nb_uuid,
                provider=cached.provider or "cache",
                model=cached.model or "",
                status=LLMCallStatus.ok,
                cache_hit=True,
                latency_ms=latency_ms,
                prompt_tokens=cached.prompt_tokens,
                completion_tokens=cached.completion_tokens,
                cost_usd=0,
            )
            db.add(call)
            db.commit()
            db.refresh(call)
            return LLMResult(
                text=cached.response_text,
                provider=cached.provider or "cache",
                model=cached.model or "",
                status="ok",
                cache_hit=True,
                latency_ms=latency_ms,
                prompt_tokens=cached.prompt_tokens,
                completion_tokens=cached.completion_tokens,
                cost_usd=0.0,
                llm_call_id=call.id,
            )

        # 2-3. Provider call with single fallback -----------------------------
        primary, fallback = _build_providers()
        status = LLMCallStatus.ok
        used: Provider = primary
        resp: ProviderResponse
        t0 = perf_counter()
        try:
            resp = primary.generate(prompt)
        except RetryableProviderError as primary_exc:
            if fallback is None:
                return _fail(
                    db, nb_uuid, primary, str(primary_exc), perf_counter() - t0
                )
            logger.warning(
                "primary provider %s failed (%s); falling back to %s",
                primary.name,
                primary_exc,
                fallback.name,
            )
            status = LLMCallStatus.fallback
            used = fallback
            try:
                resp = fallback.generate(prompt)
            except (RetryableProviderError, FatalProviderError) as fb_exc:
                return _fail(
                    db,
                    nb_uuid,
                    fallback,
                    f"primary+fallback failed: {primary_exc} | {fb_exc}",
                    perf_counter() - t0,
                )
        except FatalProviderError as primary_exc:
            return _fail(db, nb_uuid, primary, str(primary_exc), perf_counter() - t0)

        latency_ms = max(0, int((perf_counter() - t0) * 1000))
        cost = _estimate_cost(used.model, resp.prompt_tokens, resp.completion_tokens)

        # 4. Write cache + log success ---------------------------------------
        db.execute(
            pg_insert(LLMCache)
            .values(
                cache_key=key,
                notebook_id=nb_uuid,
                response_text=resp.text,
                provider=used.name,
                model=used.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                cost_usd=cost,
                **_semantic_values(semantic),
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
        call = LLMCall(
            notebook_id=nb_uuid,
            provider=used.name,
            model=used.model,
            status=status,
            cache_hit=False,
            latency_ms=latency_ms,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            cost_usd=cost,
        )
        db.add(call)
        db.commit()
        db.refresh(call)

        return LLMResult(
            text=resp.text,
            provider=used.name,
            model=used.model,
            status=status.value,
            cache_hit=False,
            latency_ms=latency_ms,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            cost_usd=cost,
            llm_call_id=call.id,
        )
    finally:
        db.close()


def stream_llm(
    prompt: str,
    notebook_id: str,
    *,
    semantic: SemanticContext | None = None,
) -> Iterator[StreamChunk]:
    """Streaming twin of :func:`call_llm`.

    Same contract — cache, fallback, logging — with one important difference:
    **fallback is only possible before the first token is emitted.** Once bytes
    are on the wire we cannot retract them, so a mid-stream failure is raised
    rather than silently restarted against another provider (which would
    duplicate or contradict text the user has already read).

    A cache hit replays the stored text as deltas so the UI behaves
    identically, while still reporting ``cache_hit=True`` and lookup-only
    latency.
    """
    key = _cache_key(notebook_id, prompt)
    nb_uuid = uuid.UUID(str(notebook_id))
    db = SessionLocal()
    try:
        # 1. Cache lookup ------------------------------------------------------
        t0 = perf_counter()
        cached = _lookup_cache(db, key, nb_uuid, semantic)
        if cached is not None:
            latency_ms = max(0, int((perf_counter() - t0) * 1000))
            call = LLMCall(
                notebook_id=nb_uuid,
                provider=cached.provider or "cache",
                model=cached.model or "",
                status=LLMCallStatus.ok,
                cache_hit=True,
                latency_ms=latency_ms,
                prompt_tokens=cached.prompt_tokens,
                completion_tokens=cached.completion_tokens,
                cost_usd=0,
            )
            db.add(call)
            db.commit()
            db.refresh(call)

            yield StreamChunk(text=cached.response_text)
            yield StreamChunk(
                result=LLMResult(
                    text=cached.response_text,
                    provider=cached.provider or "cache",
                    model=cached.model or "",
                    status="ok",
                    cache_hit=True,
                    latency_ms=latency_ms,
                    prompt_tokens=cached.prompt_tokens,
                    completion_tokens=cached.completion_tokens,
                    cost_usd=0.0,
                    llm_call_id=call.id,
                )
            )
            return

        # 2-3. Provider stream with pre-first-token fallback -------------------
        primary, fallback = _build_providers()
        status = LLMCallStatus.ok
        used: Provider = primary
        t0 = perf_counter()

        parts: list[str] = []
        prompt_tokens = completion_tokens = None

        def run(provider: Provider) -> Iterator[StreamDelta]:
            return provider.generate_stream(prompt)

        try:
            for delta in run(primary):
                if delta.final:
                    prompt_tokens = delta.prompt_tokens
                    completion_tokens = delta.completion_tokens
                elif delta.text:
                    parts.append(delta.text)
                    yield StreamChunk(text=delta.text)
        except RetryableProviderError as primary_exc:
            if parts:
                # Already streamed text to the client — failing over now would
                # splice two different answers together.
                _fail(
                    db, nb_uuid, primary,
                    f"stream failed after {len(parts)} chunks: {primary_exc}",
                    perf_counter() - t0,
                )
            if fallback is None:
                _fail(db, nb_uuid, primary, str(primary_exc), perf_counter() - t0)
            logger.warning(
                "primary provider %s failed before first token (%s); falling back to %s",
                primary.name, primary_exc, fallback.name,
            )
            status = LLMCallStatus.fallback
            used = fallback
            try:
                for delta in run(fallback):
                    if delta.final:
                        prompt_tokens = delta.prompt_tokens
                        completion_tokens = delta.completion_tokens
                    elif delta.text:
                        parts.append(delta.text)
                        yield StreamChunk(text=delta.text)
            except (RetryableProviderError, FatalProviderError) as fb_exc:
                _fail(
                    db, nb_uuid, fallback,
                    f"primary+fallback failed: {primary_exc} | {fb_exc}",
                    perf_counter() - t0,
                )
        except FatalProviderError as primary_exc:
            _fail(db, nb_uuid, primary, str(primary_exc), perf_counter() - t0)

        text = "".join(parts)
        latency_ms = max(0, int((perf_counter() - t0) * 1000))
        cost = _estimate_cost(used.model, prompt_tokens, completion_tokens)

        # 4. Write cache + log success ---------------------------------------
        db.execute(
            pg_insert(LLMCache)
            .values(
                cache_key=key,
                notebook_id=nb_uuid,
                response_text=text,
                provider=used.name,
                model=used.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                **_semantic_values(semantic),
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
        call = LLMCall(
            notebook_id=nb_uuid,
            provider=used.name,
            model=used.model,
            status=status,
            cache_hit=False,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        )
        db.add(call)
        db.commit()
        db.refresh(call)

        yield StreamChunk(
            result=LLMResult(
                text=text,
                provider=used.name,
                model=used.model,
                status=status.value,
                cache_hit=False,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                llm_call_id=call.id,
            )
        )
    finally:
        db.close()


def _fail(db, notebook_id: uuid.UUID, provider: Provider, message: str, elapsed: float):
    """Log an error row and raise GatewayError."""
    latency_ms = max(0, int(elapsed * 1000))
    call = LLMCall(
        notebook_id=notebook_id,
        provider=provider.name,
        model=provider.model,
        status=LLMCallStatus.error,
        cache_hit=False,
        latency_ms=latency_ms,
        prompt_tokens=None,
        completion_tokens=None,
        cost_usd=None,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    logger.error("gateway call failed: %s", message)
    raise GatewayError(message, llm_call_id=call.id)
