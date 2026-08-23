"""Pydantic request/response schemas for the API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.models import MessageRole, SourceStatus, SourceType


# --- Notebooks ---
class NotebookCreate(BaseModel):
    # Optional — omit or send blank to get UNTITLED_NOTEBOOK_TITLE, which
    # ingestion then replaces with a name derived from the first source.
    title: str | None = None


class NotebookUpdate(BaseModel):
    title: str


class NotebookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime


# --- Auth ---
class AuthConfig(BaseModel):
    """Whether this deployment requires Google sign-in."""

    auth_required: bool


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    name: str | None
    picture: str | None


# --- Sources ---
class SourceURLCreate(BaseModel):
    """JSON body for adding a URL source."""

    url: HttpUrl


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    notebook_id: uuid.UUID
    type: SourceType
    original_name_or_url: str
    status: SourceStatus
    progress: int
    error_code: str | None
    error_message: str | None
    ingested_at: datetime


# --- Web search (source discovery) ---
class SearchResultOut(BaseModel):
    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultOut]


# --- Chat ---
class ChatRequest(BaseModel):
    query: str


class Citation(BaseModel):
    marker: int
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    snippet: str


class ChatResponse(BaseModel):
    """Exact shape consumed by the frontend transparency panel."""

    answer: str
    citations: list[Citation]
    provider: str
    # Which model actually answered, and whether it was the primary or the
    # fallback — without these a failover is indistinguishable from a normal
    # call in the UI.
    model: str
    status: str
    latency_ms: int
    cost_usd: float
    cache_hit: bool
    # The persisted assistant ChatMessage id — None only for the "no sources
    # yet" canned reply, which isn't grounded in anything worth regenerating.
    # Lets the frontend call the regenerate endpoint on a message it just
    # streamed, not only ones reloaded from GET /messages.
    message_id: uuid.UUID | None = None


# --- Gateway stats (aggregate dashboard) ---
class ProviderStat(BaseModel):
    provider: str
    calls: int
    cost_usd: float


class ModelStat(BaseModel):
    model: str
    calls: int
    cost_usd: float


class StatusStat(BaseModel):
    status: str
    count: int


class DailyStat(BaseModel):
    date: str
    calls: int
    cost_usd: float
    cache_hits: int


class NotebookStat(BaseModel):
    notebook_id: uuid.UUID
    title: str
    calls: int
    cost_usd: float


class RateLimitStat(BaseModel):
    """Current usage vs. each chain model's free-tier quota — see
    app/rate_limits.py. Account-wide, not scoped to the caller: the quota
    itself is shared across every user of this deployment."""

    provider: str
    model: str
    requests_today: int
    rpd_limit: int | None
    requests_this_minute: int
    rpm_limit: int | None


class StatsResponse(BaseModel):
    """Aggregated across every llm_calls row for the caller's own notebooks.

    All-time, not filtered by date range — this is a portfolio-scale app, not
    a production monitoring dashboard, so one unfiltered view is honest and
    keeps the endpoint to a single query pass.
    """

    total_calls: int
    total_cost_usd: float
    cache_hits: int
    cache_hit_rate: float
    fallback_count: int
    error_count: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    by_provider: list[ProviderStat]
    by_model: list[ModelStat]
    by_status: list[StatusStat]
    daily: list[DailyStat]
    top_notebooks: list[NotebookStat]
    rate_limits: list[RateLimitStat]


class MessageOut(BaseModel):
    """A persisted chat message, with gateway metadata for assistant turns."""

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    citations: list[Citation] = []
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    cache_hit: bool | None = None
