"""Application configuration.

All settings are loaded from environment variables (see ``.env.example``).
Values map 1:1 to the variables documented there so the same names are used
across docker-compose, local dev, and deploy targets.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core / app ---
    app_name: str = "Sourcery"
    environment: str = "development"
    # Comma-separated list of allowed CORS origins for the frontend.
    cors_origins: str = "http://localhost:3000"

    # --- Database ---
    # SQLAlchemy/psycopg URL, e.g.
    # postgresql+psycopg://rag:rag@localhost:5432/rag
    database_url: str = "postgresql+psycopg://rag:rag@localhost:5432/rag"

    # --- LLM providers (see SPEC "LLM Gateway Contract") ---
    # The full resolution chain, in order, per configured API key:
    #   gemini_primary_model -> gemini_fallback_model -> groq_fallback_model
    #   -> groq_fallback_model_2 -> gemini_tertiary_model
    # Before attempting each hop, the gateway checks app/rate_limits.py
    # against that account's actual free-tier quota (logged usage in
    # llm_calls) and skips straight past any hop that's currently exhausted,
    # rather than waiting for the provider to reject it with a 429.
    gemini_api_key: str | None = None
    # Optional fallback provider. SPEC names "Gemini 2.5 Pro or OpenAI" here —
    # Groq replaces OpenAI by direct request: it's OpenAI-API-compatible (same
    # SDK, different base_url — see providers.py) and genuinely free (no card,
    # resets daily) — unlike an earlier Kimi/Moonshot AI attempt at this slot,
    # which turned out to need a paid recharge. Leave empty and the fallback
    # becomes GEMINI_FALLBACK_MODEL (same provider, more capable model).
    groq_api_key: str | None = None
    # SPEC names gemini-2.5-flash/pro, but 2.5-flash now 404s for new API keys
    # ("no longer available to new users") and 2.5-pro 429s on the free tier.
    # These are the current Flash-tier equivalents; keep _PRICING in gateway.py
    # in sync when changing them.
    #
    # 3.5-flash is primary because it accepts thinking_budget=0, which drops
    # time-to-first-token from ~4.8s to ~0.9s — the difference between a
    # streaming UI that feels live and one that stalls. Gemini 3.x models
    # reject that knob, so 3.6-flash serves as the fallback (the provider
    # retries without thinking config if a model rejects it).
    gemini_primary_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.6-flash"
    # Last-resort Gemini hop, tried after both Groq models — a third distinct
    # Flash-tier model in case 3.5/3.6 are both rate-limited or down. On this
    # account's free tier it shares the same tight RPM=5/TPM=250K/RPD=20 quota
    # as the other two, so it's most useful when 3.5/3.6 are exhausted for
    # the day but the daily quota resets are staggered.
    gemini_tertiary_model: str = "gemini-3-flash-preview"

    # Thinking tokens bill as output and delay the first token. 0 disables
    # thinking; set to None to leave the model's default in place.
    gemini_thinking_budget: int | None = 0
    # llama-3.3-70b-versatile was removed from Groq's free tier (no longer
    # listed at console.groq.com/docs/rate-limits) — these are its current
    # free-tier replacements, tried in order. $0 on the free tier either way.
    groq_fallback_model: str = "openai/gpt-oss-120b"
    groq_fallback_model_2: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 30.0

    # --- Auth (Google sign-in) ---
    # OAuth 2.0 Web client ID from Google Cloud Console. Used as the expected
    # `aud` when verifying ID tokens. Leave EMPTY to disable auth entirely —
    # the API then runs as the sentinel `local-dev` user.
    google_client_id: str | None = None

    # --- Web search (source discovery only — not a retrieval path) ---
    # Optional: without a key the /search endpoint reports 503 and the UI
    # hides the feature. Free tier: https://app.tavily.com/
    tavily_api_key: str | None = None
    search_result_count: int = 8
    search_timeout_seconds: float = 10.0

    # --- Embeddings (hosted — Gemini, same GEMINI_API_KEY as chat) ---
    # Was local (sentence-transformers/MiniLM) until torch's baseline memory
    # footprint was confirmed in production to exceed Render's free 512MB
    # ceiling for a single typical source, no concurrency involved. Free
    # tier: 100 RPM / 30,000 TPM / 1,000 RPD (aistudio.google.com/rate-limit).
    # 768 is a Matryoshka-truncated dimension (native output is 3072) —
    # smaller storage/index cost while preserving ranking quality.
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    # --- RAG parameters ---
    chunk_size_tokens: int = 500
    chunk_overlap_tokens: int = 50
    retrieval_top_k: int = 5

    # Prior turns replayed into the prompt so follow-ups ("what about that?")
    # resolve. Counts individual messages, so 6 ≈ 3 exchanges. 0 disables.
    chat_history_turns: int = 6

    # --- Semantic cache ---
    # Lets a paraphrase reuse a cached answer when retrieval returned the same
    # chunks. Threshold is cosine similarity on the query embedding.
    #
    # OFF by default since switching embeddings from local MiniLM to
    # gemini-embedding-001: the 0.97 threshold below was tuned specifically
    # for MiniLM's embedding space and does NOT transfer. A quick check
    # against gemini-embedding-001 (RETRIEVAL_QUERY, 768-dim) found the
    # margin unsafe to guess at — "what is retrieval augmented generation"
    # (want HIT) scored 0.9804, while "How does retrieval-augmented
    # generation WORK?" (a genuinely different question, want MISS) scored
    # 0.9749 — only 0.0055 apart, from a single sample pair, too thin to
    # trust without a proper multi-pair measurement against this model.
    # Exact cache_key matching (identical prompt text) is unaffected and
    # still fully active regardless of this setting.
    semantic_cache_enabled: bool = False
    semantic_cache_threshold: float = 0.97

    # --- Ingestion ---
    # Each added source spawns its own FastAPI BackgroundTask, and those run
    # concurrently in separate threads. Parsing/fetching (unlike the embed
    # step, which is already serialized — see embeddings.py) is not, so
    # bulk-adding several sources at once multiplies peak memory by however
    # many are in flight. On Render's free 512MB tier — already close to the
    # ceiling with torch + MiniLM loaded at idle — that multiplication is
    # what tips it into OOM, not any single source's footprint. Capping
    # concurrent ingestions bounds peak memory regardless of burst size, at
    # the cost of bulk adds finishing more slowly.
    max_concurrent_ingestions: int = 2

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
