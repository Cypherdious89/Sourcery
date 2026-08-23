# NotebookLM-Style RAG Chat, on a Self-Built LLM Gateway

A notebook-scoped Retrieval-Augmented Generation chat app. Create a "notebook,"
add sources (PDF/DOCX upload or a pasted URL), and chat with an assistant that
answers **only** from those sources, with inline citations back to the exact
retrieved chunk.

The part worth looking at is underneath: **every LLM call goes through a gateway
I built** — provider fallback, response caching, and per-call cost/latency
logging — and the UI exposes that plumbing per message instead of hiding it.

![Chat with citations and the transparency panel](docs/screenshot-transparency.png)

<sub>Light and [dark](docs/screenshot-dark.png) themes; the transparency row expands to show provider, model, status, latency, cost, and cache result.</sub>

## Why a gateway layer?

Calling a provider SDK directly from a request handler works right up until it
doesn't. The failure modes are boring and guaranteed: the provider rate-limits
you, a model gets retired out from under you, latency spikes, or you ship a
feature and discover a month later what it costs per user.

So no caller in this codebase touches a provider SDK. They call:

```python
gateway.call_llm(prompt: str, notebook_id: str) -> LLMResult
```

and the gateway owns the messy parts:

| Concern | How it's handled |
|---|---|
| **Redundant spend** | `(notebook_id, normalized_prompt)` is hashed into a Postgres cache table. A hit returns in ~2 ms at $0.00 and never touches a provider. A **semantic** layer also matches paraphrases via the query embedding, gated on the retrieved chunks being identical. |
| **Provider failure** | Timeout / 5xx / rate-limit on the primary retries once against a fallback provider, and the call is logged with `status="fallback"`. |
| **Cost blindness** | Every call writes a row to `llm_calls`: provider, model, tokens, computed USD cost, latency, cache hit. The transparency panel reads from the same data. |
| **Vendor lock-in** | Providers are adapters behind a `Provider` protocol. Swapping Gemini for Groq is a config change, not a refactor. |
| **Total failure** | If both providers fail, the gateway still logs an `error` row and raises a typed exception the API turns into a real user-facing message. |

This is deliberately the boring, observable version of an LLM integration — the
kind you can debug at 2am from a database table.

## Architecture

```
      Browser
         │
         ▼
┌──────────────────────┐   Vercel
│  Next.js (App Router)│   ─ notebook list / detail
│  TypeScript+Tailwind │   ─ citation chips, transparency panel
└──────────┬───────────┘   ─ polls source status until `ready`
           │ JSON over HTTPS (CORS-scoped)
           ▼
┌──────────────────────────────────────────────────┐   Render
│                FastAPI                            │
│                                                   │
│  /notebooks            /notebooks/{id}/sources    │
│  /notebooks/{id}/chat  ── the RAG flow ──┐        │
│                                          │        │
│  ┌───────────────────────────────────────▼─────┐  │
│  │ 1. embed query      gemini-embedding-001    │  │
│  │                     hosted API, 768d        │  │
│  │ 2. top-k=5 cosine search  (pgvector <=>)    │  │
│  │ 3. build prompt, chunks labelled [S1]..[S5] │  │
│  │ 4. gateway.call_llm() ───────────┐          │  │
│  │ 5. parse cited markers → chunk_ids│         │  │
│  └───────────────────────────────────┼─────────┘  │
│                                      ▼            │
│  ┌─────────────────── LLM Gateway ──────────────┐ │
│  │  cache lookup ──hit──▶ return (~2ms, $0.00)  │ │
│  │       │ miss                                 │ │
│  │       ▼                                      │ │
│  │  rate-limit-filtered chain, walked in order: │ │
│  │  gemini-3.5 → gemini-3.6 → groq×2 → gemini-3 │ │
│  │  each hop skipped pre-emptively if that      │ │
│  │  model's own RPM/TPM/RPD is already exhausted│ │
│  │       │ first with headroom + succeeds       │ │
│  │       ▼                                      │ │
│  │  write cache + log llm_calls row             │ │
│  │       │ every hop exhausted or fails         │ │
│  │       ▼  log error row, raise GatewayError   │ │
│  └───────────────┬──────────────────────────────┘ │
└──────────────────┼────────────────────────────────┘
                   │                    │
                   ▼                    ▼
      ┌────────────────────┐   ┌──────────────────┐
      │ Postgres + pgvector│   │ Gemini / Groq /  │
      │      (Neon)        │   │ Gemini embeddings│
      │                    │   └──────────────────┘
      │ notebooks  sources │
      │ chunks     chat_messages
      │ llm_calls  llm_cache
      └────────────────────┘
```

**Ingestion** runs as a FastAPI background task: parse (`pypdf` / `python-docx` /
`trafilatura`) → chunk (~500 tokens, 50 overlap) → embed via Gemini's hosted API
→ store. At most `MAX_CONCURRENT_INGESTIONS` (default 2) run at once — a
memory-constrained host (e.g. Render's free tier) can't parse/embed unbounded
sources in parallel — so a burst of adds queues rather than running together.
The source row flips `pending → processing → ready|failed`, and the UI polls
until it settles.

**Streaming.** `POST /notebooks/{id}/chat/stream` emits Server-Sent Events —
`token` deltas as the model generates, then one `done` event carrying the exact
same payload as the buffered endpoint (citations and cost are only knowable once
generation ends). Two details make it feel live: Gemini's *thinking* phase is
disabled (`GEMINI_THINKING_BUDGET=0`), which cuts time-to-first-token from ~4.8s
to ~0.9s, and the client re-paces chunky provider deltas into a character-by-
character reveal. Fallback is only attempted **before** the first token — once
bytes are on the wire, splicing in a second provider's answer would contradict
what the user already read.

**Auth** (optional, `GOOGLE_CLIENT_ID`) is Google sign-in with real per-user
isolation: notebooks belong to a `users` row and every query is scoped to the
caller. The frontend uses Auth.js and sends Google's ID token as
`Authorization: Bearer …` — a header rather than a cookie because Vercel and
Render are separate origins. Leave the client id unset and the API runs as a
`local-dev` user, so the app works with no OAuth setup.

**Conversation memory.** The last `CHAT_HISTORY_TURNS` messages are replayed
into the prompt so follow-ups resolve ("what are *its* benefits?"). The
transcript itself rehydrates from `GET /notebooks/{id}/messages`, which rejoins
`llm_calls` so restored assistant turns keep their provider, model, latency,
cost, and cache status.

**Web search** (optional, `TAVILY_API_KEY`) is *source discovery only*: search,
tick the results you want, and they're added as ordinary `url` sources through
that same pipeline. Retrieval and citations are untouched — everything the model
cites is still a row in `chunks`. Without a key the endpoint returns 503 and the
UI hides the search box.

**Gateway stats** (`/stats`) is SPEC's "aggregate gateway-stats page" stretch
item — every number reads straight from `llm_calls`: total spend, cache hit
rate, p50/p95 latency, breakdown by provider/model/status, a 30-day trend, and
top notebooks by spend. Charts are hand-built SVG (no charting dependency),
built to a data-visualization method: fixed categorical color order validated
for colorblind-safety and contrast (`node scripts/validate_palette.js`), a
reserved status palette (never doubling as a series color), one axis per
chart (never dual-axis — cost and call volume are two separate charts), and a
hover crosshair on every line.

**Notebook rename** — `PATCH /notebooks/{id}`, inline-editable in the header
(click the title). **Multi-file upload** and **web-search batch-add** both
fire N independent `POST /sources` calls via `Promise.allSettled`, so a
partial failure doesn't lose the sources that succeeded. **Stop generating**
aborts the client-side fetch and keeps whatever text streamed so far as a
"stopped" message — the in-flight provider call still finishes server-side and
is still logged to `llm_calls` (tokens already billed can't be un-spent),
only the client stops rendering further tokens.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q      # run via `python -m`, not a bare `pytest`, so cwd is on sys.path
```

50 tests, no network calls, real local Postgres (deliberately not mocked — the
gateway's value IS what it writes to the database). Embeddings are mocked
with a deterministic, network-free stand-in (`tests/conftest.py`'s
`fake_embeddings`) — bag-of-words hashing, so paraphrases land at cosine
similarity 1.0 and unrelated questions land far apart, exactly what the
semantic-cache tests need without ever calling the real Gemini embedding API.
`tests/test_gateway.py` is the core suite: fake providers exercise every SPEC
branch — cache hit skips the provider, a retryable failure falls over to the
next hop in the chain, a rate-limited hop is skipped without being called, a
fatal error never retries, a mid-stream failure raises instead of splicing
two answers together. `test_rag.py` and `test_websearch.py` cover parsing;
`test_api.py` smoke-tests routing and ownership in auth-disabled mode.

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic |
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4 |
| Database | Postgres 16 + `pgvector` (HNSW, `vector_cosine_ops`) |
| Embeddings | `gemini-embedding-001`, hosted, 768-dim (Matryoshka-truncated from 3072) |
| LLM | Gemini (3-hop chain) + Groq adapter (OpenAI-API-compatible, free tier), rate-limit-aware fallback |

## Local setup

**Prerequisites:** Docker, Python 3.12, Node 20+.

```bash
# 1. Postgres + pgvector
docker compose up -d
docker exec notebooklm_rag_db psql -U rag -d rag \
  -c "SELECT extname FROM pg_extension WHERE extname='vector';"

# 2. Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-ingest.txt
cp .env.example .env                     # fill in GEMINI_API_KEY (also used for embeddings)
alembic upgrade head
uvicorn app.main:app --reload            # http://localhost:8000

# 3. Frontend (new shell)
cd frontend
npm install
cp .env.example .env.local               # NEXT_PUBLIC_API_BASE_URL
npm run dev                              # http://localhost:3000
```

Smoke-test the whole path:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/notebooks \
  -H 'Content-Type: application/json' -d '{"title":"Demo"}'
```

> Embeddings are a hosted API call now (`gemini-embedding-001`) — no local
> model to warm, so there's no first-request cold-load cost for it. Chat
> latency is dominated by the LLM provider call instead.

## Deploying

The full checklist for **Neon + Render + Vercel** — including pushing this
repo to GitHub, which it doesn't have a remote for yet — lives in
[`DEPLOYMENT.md`](DEPLOYMENT.md). Config lives in [`render.yaml`](render.yaml)
and [`frontend/vercel.json`](frontend/vercel.json); no secrets are committed —
every secret is marked `sync: false` and set in the provider dashboard.

## Known limitations

These are real, measured constraints — not hypotheticals.

**Every model in the LLM chain has real, tight rate limits.** This account's
Gemini free tier caps every Flash model (3.5, 3.6, 3-flash-preview) at just
5 requests/min and **20/day each** — confirmed from the account dashboard, not
the (unpublished) public docs. Groq's `openai/gpt-oss-120b`/`20b` fare better
(30 RPM, 1,000/day, 8,000 TPM) but replaced `llama-3.3-70b-versatile` after it
was removed from Groq's free tier mid-project. `app/rate_limits.py` checks
each hop's own usage (from `llm_calls`) before attempting it and skips
straight to the next one if exhausted, rather than waiting on a 429 — but
with a 5-hop chain and Gemini's 20/day ceiling per model, a busy day can
genuinely exhaust every hop; the gateway surfaces that as a 429 to the client
rather than a generic failure.

**Free-tier cold starts compound.** Render free services spin down after ~15
minutes idle. Neon's compute also scales to zero. A first request to a cold
stack can take 30–60 s, and the transparency panel will honestly report that
latency. Warm requests are ~3–14 s, dominated by the provider call.

**Embeddings are hosted, not local — a deliberate reversal mid-project.**
SPEC originally chose local embeddings (`sentence-transformers`/MiniLM): no
per-call cost, no document text sent to a third party. That held until
production confirmed otherwise — torch's baseline memory footprint plus a
single typical source's parse/chunk buffers exceeded Render's free 512MB
ceiling (`SIGKILL`/exit 137, "Ran out of memory") on a plain 19-page PDF, no
concurrency involved. Switched to `gemini-embedding-001` (same API key as
chat, 100 RPM / 30,000 TPM / 1,000 RPD free tier), which removes the whole
torch/MiniLM baseline at the cost of a network dependency and that quota. See
`app/embeddings.py`.

**Retrieval is single-shot.** Top-k=5 cosine, no reranking, no query rewriting,
no multi-hop. A question whose answer is split across seven chunks will get five
of them.

**The semantic (paraphrase) cache is disabled by default, pending
remeasurement.** Its 0.97 threshold was tuned specifically for MiniLM's
embedding space; switching to `gemini-embedding-001` invalidated that
number — a quick check found "what is retrieval augmented generation" (want
hit, scored 0.9804) and "How does retrieval-augmented generation **work**?"
(a different question, want miss, scored 0.9749) only 0.0055 apart, too thin
to trust from one sample pair. Exact `cache_key` matching (identical prompt
text) is unaffected and still fully active — only the paraphrase-matching
path is off (`SEMANTIC_CACHE_ENABLED=false`) until it's properly remeasured
against the new embedding space.

**Semantic hits (once re-enabled) would ignore conversation history.** The
exact-match key covers the full prompt including replayed turns, but the
semantic key is `(chunks, query embedding)` only — including history would
make the feature dead code, since every turn has a different history. The
trade-off is that a paraphrased *follow-up* whose meaning depends on the
conversation could match an earlier one asked in a different context.

**A model-config-fatal error does not trigger fallback.** Per spec, failover
fires on timeout/5xx/rate-limit. A `404 model not found` or `400 bad request`
is classified fatal, so it surfaces as an error instead of failing over —
which is exactly what happened when Google retired `gemini-2.5-flash` for new
API keys mid-project (now excluded from the live chain entirely, since it's a
confirmed permanent 404 rather than a rate limit). This is arguably too broad
for a multi-hop chain — a 401/404 is a provider-config problem, not a
prompt problem, and could reasonably fall through to the next hop instead of
aborting — but is unchanged from the original single-fallback design pending
a decision on whether to split that distinction out.

**Single-user by design.** No auth, no tenancy, no rate limiting. Anyone who can
reach the API can read and write every notebook. See SPEC.md "Non-goals."

## Repository layout

```
.
├── backend/
│   ├── app/
│   │   ├── gateway.py      ← fallback, caching, cost/latency logging
│   │   ├── providers.py    ← Gemini/Groq adapters + error taxonomy
│   │   ├── rag.py          ← retrieval, prompt build, citation parsing
│   │   ├── ingestion.py    ← parse → chunk → embed → store
│   │   ├── models.py       ← SQLAlchemy models (mirrors SPEC data model)
│   │   └── routers/        ← notebooks, sources, chat
│   ├── alembic/            ← migrations (creates the pgvector extension)
│   ├── embeddings.py       ← gemini-embedding-001, hosted (no local model)
│   └── requirements-ingest.txt   ← local dev + production alike
├── frontend/src/
│   ├── app/                ← App Router pages
│   ├── components/         ← SourcesPanel, ChatPanel, citations, transparency
│   └── lib/                ← typed API client, source-polling hook
├── DEPLOYMENT.md
├── render.yaml
└── docker-compose.yml
```

`Mem-Claude/SPEC.md` is the original project spec and remains the source of truth for
scope; where the implementation deviates (model IDs, mainly), the deviation is
commented at the site.
