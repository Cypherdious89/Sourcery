"""Resilient LLM gateway (see SPEC "LLM Gateway Contract").

Single entrypoint ``call_llm(prompt, notebook_id) -> LLMResult``:

1. Hash ``(notebook_id, normalized_prompt)`` and check the ``llm_cache`` table.
   On a hit, return immediately (``cache_hit=True``) with only the lookup
   latency and no provider call.
2. On a miss, build the fallback chain (see ``_build_provider_chain``) and
   drop any candidate that's currently out of quota per
   ``app/rate_limits.py`` — proactively, using logged usage in ``llm_calls``,
   rather than waiting to be told with a 429.
3. Call the first candidate with headroom. On a timeout/5xx/rate-limit,
   move to the next candidate (``status="fallback"``); on a fatal error
   (bad request, safety block), stop — the prompt itself is the problem and
   every other provider would reject it identically.
4. On success, write the response to ``llm_cache`` and log a row to ``llm_calls``.
5. If every candidate is rate-limited, or every attempted candidate fails,
   log an ``error`` row and raise ``GatewayError`` (``rate_limited=True`` in
   the former case, so the caller can surface a distinct message).

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

from app import rate_limits
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
    "openai/gpt-oss-120b": (0.0, 0.0),
    "openai/gpt-oss-20b": (0.0, 0.0),
    # Removed from Groq's free tier; kept for historical llm_calls rows.
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

    def __init__(
        self,
        message: str,
        *,
        llm_call_id: uuid.UUID | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.llm_call_id = llm_call_id
        # True when every candidate in the chain was skipped on a proactive
        # rate-limit check — no provider was actually called. Lets the
        # caller (see routers/chat.py) return 429 with a distinct message
        # instead of a generic "the API is broken" 502.
        self.rate_limited = rate_limited


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


def _build_provider_chain() -> list[Provider]:
    """Ordered fallback chain per configured API keys.

    gemini_primary_model -> gemini_fallback_model -> groq_fallback_model ->
    groq_fallback_model_2 -> gemini_tertiary_model. A hop is only included
    when its API key is set; with no keys at all, the chain is just the
    offline stub.
    """
    timeout = _settings.llm_timeout_seconds
    chain: list[Provider] = []

    if _settings.gemini_api_key:
        chain.append(
            GeminiProvider(_settings.gemini_api_key, _settings.gemini_primary_model, timeout)
        )
        chain.append(
            GeminiProvider(_settings.gemini_api_key, _settings.gemini_fallback_model, timeout)
        )

    if _settings.groq_api_key:
        chain.append(
            GroqProvider(_settings.groq_api_key, _settings.groq_fallback_model, timeout)
        )
        chain.append(
            GroqProvider(_settings.groq_api_key, _settings.groq_fallback_model_2, timeout)
        )

    if _settings.gemini_api_key:
        chain.append(
            GeminiProvider(_settings.gemini_api_key, _settings.gemini_tertiary_model, timeout)
        )

    return chain or [StubProvider()]


def _rate_limit_filter(db, chain: list[Provider]) -> tuple[list[Provider], list[Provider]]:
    """Split a chain into (has headroom, currently rate-limited).

    Checked once up front against each candidate's own usage — a call to one
    model doesn't affect another model's separate quota, so there's no need
    to recheck mid-loop as the chain is walked.
    """
    available: list[Provider] = []
    limited: list[Provider] = []
    for provider in chain:
        if rate_limits.has_headroom(db, provider.name, provider.model):
            available.append(provider)
        else:
            limited.append(provider)
    return available, limited


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

        # 2-3. Provider call, walking the rate-limit-filtered chain -----------
        t0 = perf_counter()
        full_chain = _build_provider_chain()
        chain, skipped = _rate_limit_filter(db, full_chain)
        if not chain:
            return _fail(
                db,
                nb_uuid,
                skipped[0],
                f"All providers are currently rate-limited: "
                f"{', '.join(f'{p.name}:{p.model}' for p in skipped)}",
                perf_counter() - t0,
                rate_limited=True,
            )
        if skipped:
            logger.info(
                "skipping rate-limited candidates: %s",
                ", ".join(f"{p.name}:{p.model}" for p in skipped),
            )

        status = LLMCallStatus.ok
        used: Provider | None = None
        resp: ProviderResponse | None = None
        errors: list[str] = []
        for provider in chain:
            try:
                resp = provider.generate(prompt)
                used = provider
                # "ok" only if this is the very first candidate overall —
                # anything reached via a skip or a prior failure is a
                # fallback, even if it's index 0 of the *filtered* chain.
                status = (
                    LLMCallStatus.ok if provider is full_chain[0] else LLMCallStatus.fallback
                )
                break
            except RetryableProviderError as exc:
                errors.append(f"{provider.name}:{provider.model}: {exc}")
                logger.warning(
                    "provider %s:%s failed (%s); trying next candidate",
                    provider.name, provider.model, exc,
                )
            except FatalProviderError as exc:
                # A fatal error means the PROMPT is the problem (bad request,
                # safety block) — every other provider would reject it the
                # same way, so stop rather than burn through the rest of the
                # chain.
                return _fail(db, nb_uuid, provider, str(exc), perf_counter() - t0)

        if used is None or resp is None:
            return _fail(
                db,
                nb_uuid,
                chain[-1],
                f"All providers failed: {'; '.join(errors)}",
                perf_counter() - t0,
            )

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

        # 2-3. Provider stream, walking the rate-limit-filtered chain --------
        t0 = perf_counter()
        full_chain = _build_provider_chain()
        stream_chain, stream_skipped = _rate_limit_filter(db, full_chain)
        if not stream_chain:
            _fail(
                db,
                nb_uuid,
                stream_skipped[0],
                f"All providers are currently rate-limited: "
                f"{', '.join(f'{p.name}:{p.model}' for p in stream_skipped)}",
                perf_counter() - t0,
                rate_limited=True,
            )
        if stream_skipped:
            logger.info(
                "skipping rate-limited candidates: %s",
                ", ".join(f"{p.name}:{p.model}" for p in stream_skipped),
            )

        status = LLMCallStatus.ok
        used: Provider | None = None
        parts: list[str] = []
        prompt_tokens = completion_tokens = None
        errors: list[str] = []

        def run(provider: Provider) -> Iterator[StreamDelta]:
            return provider.generate_stream(prompt)

        for provider in stream_chain:
            try:
                for delta in run(provider):
                    if delta.final:
                        prompt_tokens = delta.prompt_tokens
                        completion_tokens = delta.completion_tokens
                    elif delta.text:
                        parts.append(delta.text)
                        yield StreamChunk(text=delta.text)
                used = provider
                status = (
                    LLMCallStatus.ok if provider is full_chain[0] else LLMCallStatus.fallback
                )
                break
            except (RetryableProviderError, FatalProviderError) as exc:
                if parts:
                    # Already streamed text to the client — failing over now
                    # would splice two different answers together.
                    _fail(
                        db, nb_uuid, provider,
                        f"stream failed after {len(parts)} chunks: {exc}",
                        perf_counter() - t0,
                    )
                if isinstance(exc, FatalProviderError):
                    # The PROMPT is the problem (bad request, safety block) —
                    # every other provider would reject it the same way.
                    _fail(db, nb_uuid, provider, str(exc), perf_counter() - t0)
                errors.append(f"{provider.name}:{provider.model}: {exc}")
                logger.warning(
                    "provider %s:%s failed before first token (%s); trying next candidate",
                    provider.name, provider.model, exc,
                )

        if used is None:
            _fail(
                db, nb_uuid, stream_chain[-1],
                f"All providers failed: {'; '.join(errors)}",
                perf_counter() - t0,
            )

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


def _fail(
    db,
    notebook_id: uuid.UUID,
    provider: Provider,
    message: str,
    elapsed: float,
    *,
    rate_limited: bool = False,
):
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
    raise GatewayError(message, llm_call_id=call.id, rate_limited=rate_limited)
