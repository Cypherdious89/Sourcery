"""Aggregate gateway stats — SPEC's "aggregate gateway-stats page" stretch item.

Every number here reads straight from ``llm_calls``, the same table the
per-message transparency panel reads from. This endpoint just proves the
gateway's cost/latency/fallback/cache claims in aggregate instead of one
message at a time.

Scoped to the caller's own notebooks (join through ``notebooks.user_id``) —
in auth-disabled local-dev mode that's everything, owned by the sentinel user.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import rate_limits
from app.auth import get_current_user
from app.db import get_db
from app.models import LLMCall, LLMCallStatus, Notebook, User
from app.schemas import (
    DailyStat,
    ModelStat,
    NotebookStat,
    ProviderStat,
    RateLimitStat,
    StatsResponse,
    StatusStat,
)

router = APIRouter(tags=["stats"])

_DAILY_WINDOW_DAYS = 30
_TOP_NOTEBOOKS_LIMIT = 5


@router.get("/stats", response_model=StatsResponse)
def get_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StatsResponse:
    # Every query below is scoped to this — never a cross-user aggregate.
    owned = select(LLMCall).join(Notebook, Notebook.id == LLMCall.notebook_id).where(
        Notebook.user_id == user.id
    )
    owned_calls = owned.subquery()

    # --- Headline numbers -------------------------------------------------
    p50 = func.percentile_cont(0.5).within_group(owned_calls.c.latency_ms)
    p95 = func.percentile_cont(0.95).within_group(owned_calls.c.latency_ms)
    totals = db.execute(
        select(
            func.count().label("n"),
            func.coalesce(func.sum(owned_calls.c.cost_usd), 0).label("cost"),
            func.coalesce(func.avg(owned_calls.c.latency_ms), 0).label("avg_lat"),
            func.coalesce(p50, 0).label("p50"),
            func.coalesce(p95, 0).label("p95"),
            func.count().filter(owned_calls.c.cache_hit.is_(True)).label("cache_hits"),
            func.count()
            .filter(owned_calls.c.status == LLMCallStatus.fallback)
            .label("fallback_count"),
            func.count()
            .filter(owned_calls.c.status == LLMCallStatus.error)
            .label("error_count"),
        )
    ).one()

    total_calls = totals.n
    cache_hit_rate = (totals.cache_hits / total_calls) if total_calls else 0.0

    # --- By provider / model / status --------------------------------------
    by_provider = [
        ProviderStat(provider=r.provider, calls=r.n, cost_usd=float(r.cost or 0))
        for r in db.execute(
            select(
                owned_calls.c.provider,
                func.count().label("n"),
                func.coalesce(func.sum(owned_calls.c.cost_usd), 0).label("cost"),
            )
            .group_by(owned_calls.c.provider)
            .order_by(func.count().desc())
        )
    ]
    by_model = [
        ModelStat(model=r.model, calls=r.n, cost_usd=float(r.cost or 0))
        for r in db.execute(
            select(
                owned_calls.c.model,
                func.count().label("n"),
                func.coalesce(func.sum(owned_calls.c.cost_usd), 0).label("cost"),
            )
            .group_by(owned_calls.c.model)
            .order_by(func.count().desc())
        )
    ]
    by_status = [
        StatusStat(status=r.status.value, count=r.n)
        for r in db.execute(
            select(owned_calls.c.status, func.count().label("n")).group_by(
                owned_calls.c.status
            )
        )
    ]

    # --- Daily trend (last 30 days) -----------------------------------------
    since = func.now() - timedelta(days=_DAILY_WINDOW_DAYS)
    day = func.date(owned_calls.c.created_at)
    daily = [
        DailyStat(
            date=r.day.isoformat(),
            calls=r.n,
            cost_usd=float(r.cost or 0),
            cache_hits=r.cache_hits,
        )
        for r in db.execute(
            select(
                day.label("day"),
                func.count().label("n"),
                func.coalesce(func.sum(owned_calls.c.cost_usd), 0).label("cost"),
                func.count().filter(owned_calls.c.cache_hit.is_(True)).label(
                    "cache_hits"
                ),
            )
            .where(owned_calls.c.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
    ]

    # --- Top notebooks by spend ----------------------------------------------
    top_notebooks = [
        NotebookStat(
            notebook_id=r.id, title=r.title, calls=r.n, cost_usd=float(r.cost or 0)
        )
        for r in db.execute(
            select(
                Notebook.id,
                Notebook.title,
                func.count(LLMCall.id).label("n"),
                func.coalesce(func.sum(LLMCall.cost_usd), 0).label("cost"),
            )
            .join(LLMCall, LLMCall.notebook_id == Notebook.id)
            .where(Notebook.user_id == user.id)
            .group_by(Notebook.id, Notebook.title)
            .order_by(func.coalesce(func.sum(LLMCall.cost_usd), 0).desc())
            .limit(_TOP_NOTEBOOKS_LIMIT)
        )
    ]

    # --- LLM rate-limit headroom (account-wide, not scoped to the caller) --
    rate_limit_stats = [
        RateLimitStat(
            provider=u.provider,
            model=u.model,
            requests_today=u.requests_today,
            rpd_limit=u.rpd_limit,
            requests_this_minute=u.requests_this_minute,
            rpm_limit=u.rpm_limit,
        )
        for u in rate_limits.get_usage_snapshot(db)
    ]

    return StatsResponse(
        total_calls=total_calls,
        total_cost_usd=float(totals.cost or 0),
        cache_hits=totals.cache_hits,
        cache_hit_rate=cache_hit_rate,
        fallback_count=totals.fallback_count,
        error_count=totals.error_count,
        avg_latency_ms=float(totals.avg_lat or 0),
        p50_latency_ms=float(totals.p50 or 0),
        p95_latency_ms=float(totals.p95 or 0),
        by_provider=by_provider,
        by_model=by_model,
        by_status=by_status,
        daily=daily,
        top_notebooks=top_notebooks,
        rate_limits=rate_limit_stats,
    )
