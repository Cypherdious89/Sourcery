"""Proactive rate-limit checks for LLM provider/model pairs.

Reuses ``llm_calls`` (already logged by the gateway for every real provider
call — see SPEC "LLM Gateway Contract") as the usage ledger, rather than
keeping a separate in-memory counter: it survives process restarts, and
there's nothing new to keep in sync. Cache hits are excluded since they never
reach the provider and don't consume its quota.

Limits below are read from each account's own dashboard, not the public
docs — Google doesn't publish free-tier numbers without login
(aistudio.google.com/rate-limit), and Groq's evolve as models rotate in/out
of the free tier (console.groq.com/docs/rate-limits). Re-check both if a
provider keeps getting skipped sooner than expected, or a 429 slips through
despite this check passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LLMCall


@dataclass(frozen=True)
class RateLimit:
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None


# A (provider, model) pair missing here is treated as unlimited — no
# proactive check is applied (e.g. the offline stub).
#
# Gemini free tier, from aistudio.google.com/rate-limit (per-account —
# confirm against your own project if these ever look off):
#   gemini-3.5-flash, gemini-3.6-flash, gemini-3-flash(-preview) all share
#   RPM=5, TPM=250,000, RPD=20. Yes, RPD=20 is genuinely that tight.
#
# Groq free tier, from console.groq.com/docs/rate-limits (Free plan table):
#   openai/gpt-oss-120b and openai/gpt-oss-20b share
#   RPM=30, TPM=8,000, RPD=1,000, TPD=200,000.
LIMITS: dict[tuple[str, str], RateLimit] = {
    ("gemini", "gemini-3.5-flash"): RateLimit(rpm=5, tpm=250_000, rpd=20),
    ("gemini", "gemini-3.6-flash"): RateLimit(rpm=5, tpm=250_000, rpd=20),
    ("gemini", "gemini-3-flash-preview"): RateLimit(rpm=5, tpm=250_000, rpd=20),
    ("groq", "openai/gpt-oss-120b"): RateLimit(rpm=30, tpm=8_000, rpd=1_000, tpd=200_000),
    ("groq", "openai/gpt-oss-20b"): RateLimit(rpm=30, tpm=8_000, rpd=1_000, tpd=200_000),
}


@dataclass
class _Usage:
    requests: int
    tokens: int


def _usage_since(db: Session, provider: str, model: str, since: datetime) -> _Usage:
    requests, tokens = db.execute(
        select(
            func.count(LLMCall.id),
            func.coalesce(
                func.sum(
                    func.coalesce(LLMCall.prompt_tokens, 0)
                    + func.coalesce(LLMCall.completion_tokens, 0)
                ),
                0,
            ),
        ).where(
            LLMCall.provider == provider,
            LLMCall.model == model,
            LLMCall.cache_hit.is_(False),
            LLMCall.created_at >= since,
        )
    ).one()
    return _Usage(requests=requests or 0, tokens=tokens or 0)


@dataclass
class ModelUsage:
    """Current usage vs. configured limits for one (provider, model) — the
    same numbers has_headroom checks, surfaced for display (see GET /stats).
    """

    provider: str
    model: str
    requests_today: int
    rpd_limit: int | None
    requests_this_minute: int
    rpm_limit: int | None


def get_usage_snapshot(db: Session) -> list[ModelUsage]:
    """Usage snapshot for every model in the fallback chain.

    Account-wide, not scoped to the caller — the quota itself is shared
    across every user of this deployment, so anyone signed in needs to see
    the same real ceiling, not just their own slice of it.
    """
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for (provider, model), limit in LIMITS.items():
        day = _usage_since(db, provider, model, midnight)
        minute = _usage_since(db, provider, model, now - timedelta(seconds=60))
        out.append(
            ModelUsage(
                provider=provider,
                model=model,
                requests_today=day.requests,
                rpd_limit=limit.rpd,
                requests_this_minute=minute.requests,
                rpm_limit=limit.rpm,
            )
        )
    return out


def has_headroom(db: Session, provider: str, model: str) -> bool:
    """Whether (provider, model) has quota left, based on logged usage.

    Errors and fallback calls still consumed a real request against the
    provider's quota, so they count too — only cache hits are excluded.
    """
    limit = LIMITS.get((provider, model))
    if limit is None:
        return True

    now = datetime.now(timezone.utc)

    if limit.rpm is not None or limit.tpm is not None:
        minute = _usage_since(db, provider, model, now - timedelta(seconds=60))
        if limit.rpm is not None and minute.requests >= limit.rpm:
            return False
        if limit.tpm is not None and minute.tokens >= limit.tpm:
            return False

    if limit.rpd is not None or limit.tpd is not None:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = _usage_since(db, provider, model, midnight)
        if limit.rpd is not None and day.requests >= limit.rpd:
            return False
        if limit.tpd is not None and day.tokens >= limit.tpd:
            return False

    return True
