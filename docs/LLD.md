# Low-Level Design — Sourcery

## 1. Purpose and scope

Sourcery is a notebook-scoped Retrieval-Augmented Generation (RAG) chat
application. A user creates a notebook, adds PDF/DOCX/URL sources, and asks
grounded questions with chunk-level citations. This document explains the
code-level design behind the system overview in [HLD.md](HLD.md).

The key invariants are:

- A notebook belongs to exactly one user in the current version.
- Retrieval always filters by notebook ID; a response is grounded only in that
  notebook's chunks.
- Every LLM request passes through the gateway, which owns caching, provider
  fallback, quota checks, cost calculation, and call logging.
- PostgreSQL is the source of truth for content, chat history, and gateway
  observability; no in-memory counter is needed to explain prior usage.

## 2. Module design

| Layer | Files | Responsibility |
|---|---|---|
| API composition | app/main.py, app/routers/*.py | CORS, router registration, validation, status codes, and HTTP responses. |
| Authentication | app/auth.py, routers/auth.py | Optional Google ID-token verification and current-user resolution. |
| Notebook/source API | routers/notebooks.py, routers/sources.py | CRUD, ownership checks, source intake, and ingestion scheduling. |
| Ingestion | ingestion.py, parsing.py, chunking.py, embeddings.py | Parse → chunk → embed → persist pipeline. |
| RAG | rag.py, routers/chat.py | Vector retrieval, prompt construction, cited-marker parsing, message persistence. |
| LLM gateway | gateway.py, providers.py, rate_limits.py | Caching, provider adapters, fallback, streaming, quota headroom, and logging. |
| Persistence | models.py, db.py, alembic/versions | ORM mappings, sessions, migrations, and pgvector indexes. |
| Frontend boundary | frontend/src/lib/api.ts, components | Typed API client, bearer token injection, SSE handling, and UI state. |

Routers are deliberately thin. They validate an HTTP request, resolve the
caller, enforce notebook ownership, and delegate domain work to focused
modules. Provider SDKs are never imported by a router or frontend component.

## 3. Data model

~~~mermaid
erDiagram
    USERS ||--o{ NOTEBOOKS : owns
    NOTEBOOKS ||--o{ SOURCES : contains
    NOTEBOOKS ||--o{ CHUNKS : scopes
    SOURCES ||--o{ CHUNKS : produces
    NOTEBOOKS ||--o{ CHAT_MESSAGES : contains
    NOTEBOOKS ||--o{ LLM_CALLS : records
    CHAT_MESSAGES o|--o{ LLM_CALLS : links
    NOTEBOOKS ||--o{ LLM_CACHE : scopes

    USERS {
      uuid id PK
      text google_sub UK
      text email
      timestamptz created_at
    }
    NOTEBOOKS {
      uuid id PK
      uuid user_id FK
      text title
      timestamptz created_at
    }
    SOURCES {
      uuid id PK
      uuid notebook_id FK
      enum type
      text original_name_or_url
      enum status
      int progress
    }
    CHUNKS {
      uuid id PK
      uuid source_id FK
      uuid notebook_id FK
      text content
      vector_768 embedding
      int chunk_index
      int4range char_span
    }
    CHAT_MESSAGES {
      uuid id PK
      uuid notebook_id FK
      enum role
      text content
      uuid_array cited_chunk_ids
    }
    LLM_CALLS {
      uuid id PK
      uuid notebook_id FK
      uuid message_id FK
      text provider
      text model
      enum status
      bool cache_hit
      int latency_ms
      numeric cost_usd
    }
    LLM_CACHE {
      uuid id PK
      text cache_key UK
      uuid notebook_id FK
      vector_768 query_embedding
      text context_hash
      text response_text
    }
~~~

| Table | Design detail |
|---|---|
| users | Google subject is unique because it is the stable identity. With auth disabled, fixed local-dev user owns local data. |
| notebooks | User ID is the current authorization boundary. The ownership helper returns 404 rather than 403 for foreign records. |
| sources | Type is pdf, docx, or url. Status moves pending → processing → ready or failed; progress is a coarse 0–100 checkpoint. Failed sources also retain a safe error code/message for the UI; raw exception detail stays in backend logs. |
| chunks | Stores text, a 768-dimensional Gemini embedding, original character range, and source/notebook foreign keys. The notebook key makes the retrieval predicate direct. |
| chat_messages | Persists both user and assistant turns; assistant rows store cited chunk UUIDs. |
| llm_calls | Writes one row for every gateway outcome—including zero-cost cache hits and errors—and links to the assistant message after that message exists. |
| llm_cache | Exact key is a SHA-256 hash of notebook ID plus normalized prompt. Semantic fields exist for a guarded paraphrase cache. |

Indexes follow access paths: foreign-key scope indexes, HNSW cosine indexes on
chunk and semantic-cache embeddings, and a composite provider/model/time index
for quota queries. Alembic, rather than ORM create-all calls, owns schema
evolution.

## 4. API contract

All notebook-scoped routes resolve the caller first. The frontend sends an
Authorization bearer token with the Google ID token when auth is enabled.

| Area | Endpoint | Detail |
|---|---|---|
| Health | GET /health | Liveness check. |
| Auth | GET /auth/config, GET /auth/me | Reports auth mode and resolved user. |
| Notebooks | POST/GET /notebooks; GET/PATCH/DELETE /notebooks/{id} | Create, list, read, rename, and delete. Blank titles begin as Untitled and may be auto-derived after first ready source. |
| Sources | POST /notebooks/{id}/sources | Multipart PDF/DOCX (max 25 MB) or JSON URL; inserts pending source and returns 202. |
| Sources | GET /notebooks/{id}/sources; GET/DELETE /notebooks/{id}/sources/{sourceId} | List/poll, inspect, and delete. |
| Sources | POST /notebooks/{id}/sources/{sourceId}/retry | Restarts a failed URL only; uploaded bytes are intentionally not retained. |
| Chat | POST /notebooks/{id}/chat | Buffered answer, citations, and gateway transparency metadata. |
| Chat | POST /notebooks/{id}/chat/stream | SSE token events followed by done event with the same answer payload. |
| Chat | GET /notebooks/{id}/messages | Rehydrates transcript and joins assistant turns to call metadata. |
| Chat | POST /notebooks/{id}/messages/{messageId}/regenerate | Retrieves afresh and bypasses cache; replaces existing answer row. |
| Discovery | GET /search, GET /search/status | Optional Tavily discovery; result URLs become ordinary sources. |
| Observability | GET /stats | Per-user stats plus account-wide provider headroom. |
| Export | GET /notebooks/{id}/export | Markdown source list and transcript download. |

Errors distinguish invalid input (400/413/415/422), unauthenticated callers
(401), absent or foreign resources (404), invalid state transitions (409),
an exhausted LLM chain (429), and all-provider failure (502).

## 5. Source ingestion

The add-source request persists only source metadata and schedules work; it
returns before parsing or embedding begins. Upload bytes live only in the
background task invocation, which is why failed uploads cannot be retried.

~~~mermaid
sequenceDiagram
    participant U as Browser
    participant API as Sources router
    participant DB as PostgreSQL
    participant I as Ingestion task
    participant E as Gemini embeddings

    U->>API: POST source (file or URL)
    API->>DB: owner check; INSERT source(pending, 0)
    API-->>U: 202 SourceOut
    API->>I: schedule ingest_source
    I->>I: acquire semaphore (default 2)
    I->>DB: status processing, progress 10
    I->>I: parse PDF/DOCX or fetch/extract URL
    I->>DB: progress 30; chunk text, progress 50
    I->>E: document embeddings, batches of <=100
    E-->>I: 768-dimensional vectors
    I->>DB: delete old chunks; insert new chunks
    I->>DB: status ready, progress 100
    U->>API: poll source endpoint
    API-->>U: terminal source state
~~~

The ingestion pipeline is:

1. Parse with pypdf, python-docx, or trafilatura.
2. Split text at about 500 tokens with 50-token overlap. Each chunk retains a
   start/end character range.
3. Generate RETRIEVAL_DOCUMENT embeddings through gemini-embedding-001,
   truncated to 768 dimensions.
4. Delete prior chunks for the source, insert the new chunks, then mark ready.

A process-local semaphore bounds parallel ingestion, protecting a small host
from burst memory pressure. Every terminal path runs garbage collection and,
on Linux, malloc_trim(0) to return parsing memory to the OS. Exceptions roll
back the active transaction and mark the source failed with a stable safe
code/message, such as EMPTY_CONTENT or URL_FETCH_FAILED. Raw exception text
is logged but never sent to the browser. When a still-untitled notebook
receives its first ready source, the task derives its name from the filename
or web-page title.

## 6. RAG and citations

~~~mermaid
sequenceDiagram
    participant U as Browser
    participant C as Chat router
    participant E as Embeddings
    participant DB as PostgreSQL + pgvector
    participant G as LLM gateway

    U->>C: POST chat with query
    C->>DB: ownership check; load recent history
    C->>E: embed query with RETRIEVAL_QUERY
    C->>DB: nearest five chunks within notebook
    C->>DB: persist user message
    C->>C: build instruction + history + source markers
    C->>G: call gateway with prompt and context hash
    G-->>C: answer and gateway metadata
    C->>C: parse valid cited markers
    C->>DB: persist assistant message; link LLM call
    C-->>U: answer, citations, transparency fields
~~~

Retrieval orders cosine distance and includes a notebook-ID predicate. This is
the retrieval isolation boundary. Prompt construction labels candidates S1
through S5 and instructs the model to use only those sources. Citation parsing
accepts only marker IDs corresponding to retrieved chunks, so unrelated
footnotes or fabricated IDs cannot become citations.

Recent conversation history is intentionally small: at most
CHAT_HISTORY_TURNS individual messages, normalized and clipped to 400
characters each. It resolves follow-ups but is never presented as factual
source material. With no chunks, the route saves a user turn and deterministic
no-source response without calling an LLM.

## 7. LLM gateway

~~~mermaid
flowchart TD
    A[Prompt + notebook ID] --> B{Exact cache hit?}
    B -- yes --> C[Write zero-cost cache-hit LLM-call row]
    C --> R[Return cached answer]
    B -- no --> D[Build provider adapter chain]
    D --> E[Remove quota-exhausted candidates]
    E --> F{Candidate succeeds?}
    F -- retryable error --> G[Try next candidate]
    G --> F
    F -- fatal error or chain exhausted --> H[Write error LLM-call row]
    H --> X[Raise GatewayError]
    F -- success --> I[Write cache entry + successful call row]
    I --> J[Return LLMResult]
~~~

Call and stream gateway entry points are the only provider-facing interfaces.
They return provider, model, status (ok/fallback), cache state, latency, token
counts, cost, and persisted call ID. Gemini and Groq are adapters behind a
common protocol.

The normal configured order is Gemini 3.5 Flash → Gemini 3.6 Flash → Groq
GPT-OSS 120B → Groq GPT-OSS 20B → Gemini 3 Flash Preview, subject to keys at
runtime. Rate-limit code calculates each model's minute/day usage from
non-cache-hit LLM-call rows, so known-exhausted free-tier candidates can be
skipped even after restart.

- Exact cache hashes normalized full prompt plus notebook identity; entries
  cannot cross notebooks.
- Semantic cache compares query embeddings only when retrieved
  chunk/history context is identical. It is disabled by default because its
  threshold has not been recalibrated for the current embedding model.
- Retryable errors try the next candidate; fatal malformed/safety-style
  errors stop the chain.
- Cache hits and terminal failures are both logged, making observability and
  quota calculations consistent.

Streaming has one extra correctness rule: fallback is allowed only before the
first emitted token. Afterwards, switching providers could splice incompatible
answers. The browser uses POST fetch plus ReadableStream because it needs a
JSON body and bearer token; Stop aborts the reader but cannot undo provider
cost already incurred server-side.

## 8. Authentication and frontend state

With no Google client ID, current-user resolution creates/uses the local-dev
user. With a client ID, Auth.js obtains a Google ID token; the backend verifies
signature, issuer, expiry, and audience, then resolves or creates the user
from Google's stable subject claim.

Every notebook route invokes the ownership helper before operating on child
resources. The typed frontend client registers a bearer-token getter and
centralized 401 callback. AuthGate uses it to sign out an expired session
instead of leaving the user on a raw error.

| Frontend unit | Responsibility |
|---|---|
| NotebookDetail | Coordinates header actions, sources, chat, export, and notebook view. |
| useSources / SourcesPanel | Adds files/URLs, supports partial batch success, and polls active ingestion. |
| WebSearchPanel | Uses web search for source discovery only. |
| ChatPanel | Rehydrates history, consumes/re-paces SSE, retains stopped partial output, and regenerates answers. |
| AnswerWithCitations | Renders validated citations and snippets. |
| TransparencyPanel | Shows provider/model/status/latency/cost/cache fields. |
| Stats page | Reads gateway aggregates and renders SVG charts plus quota bars. |

The frontend owns only UI state. Its API client is the single typed HTTP
boundary; it holds no LLM credentials or authoritative notebook data.

## 9. Operations and verification

Pydantic settings load configuration from environment variables: database/CORS,
provider and embedding keys, OAuth client ID, Tavily key, retrieval settings,
semantic-cache controls, and ingestion concurrency. The checked-in environment
examples, render configuration, and [DEPLOYMENT.md](../DEPLOYMENT.md) describe
deployment without committing secrets.

The LLM-call table is both product observability and quota ledger. The stats
endpoint derives total calls/spend/cache hits, fallback/error counts, latency
percentiles, provider/model/status breakdowns, 30-day activity, top notebooks,
and provider headroom from it.

Backend tests use deterministic fake embeddings and provider fakes, with no
network calls. They cover RAG, parsing, caching, fallback, quotas, streaming,
ownership, retry, regeneration, export, and auto-titling. Run from backend:

~~~bash
python -m pytest -q
~~~

## 10. Current limits

| Situation | Current behavior |
|---|---|
| Failed file ingestion | User re-uploads; original file bytes are not persisted. |
| No sources | Deterministic no-answer response; no general web answer. |
| Provider exhaustion | Gateway logs an error and returns 429 after all candidates lack headroom. |
| Mid-stream provider error | No fallback after the first emitted token. |
| Data access | User-private notebooks only; no sharing/collaboration. |
| Scaling | FastAPI background tasks and process-local concurrency are portfolio-scale, not durable distributed jobs. |

Deferred designs for search, per-source summary, chat threads, collaboration,
durable source versions, retrieval evaluation, and tenant controls are in
[FUTURE_SCOPE.md](FUTURE_SCOPE.md).
