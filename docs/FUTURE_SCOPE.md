# Future Scope — Sourcery

## 1. Purpose

This is a roadmap, not a list of implemented claims. Today Sourcery is a
single-owner, notebook-scoped RAG application with one linear transcript per
notebook. The items here show the next sensible design steps for interview
discussion and future learning.

The order is intentional:

1. Improve navigation and understanding of content owned by one user.
2. Separate one notebook's conversations into durable threads.
3. Make source processing durable and version-aware.
4. Add multi-user collaboration only after authorization is explicit.
5. Introduce larger-scale controls when real traffic justifies them.

## 2. Future-state HLD

The existing frontend, FastAPI API, PostgreSQL/pgvector database, optional
Google auth, and LLM gateway remain the foundation. Future features extend
them with narrow services rather than putting all responsibility into routes.

~~~mermaid
flowchart LR
    UI[Next.js workspace] --> API[FastAPI API]
    API --> ACL[Authorization service]
    API --> NS[Notebook search service]
    API --> TS[Thread service]
    API --> SS[Summary orchestrator]
    API --> IW[Durable ingestion worker]
    API --> RQ[Retrieval quality layer]
    API --> GW[LLM gateway]
    ACL --> DB[(PostgreSQL + pgvector)]
    NS --> DB
    TS --> DB
    SS --> DB
    IW --> DB
    IW --> OBJ[(Object storage)]
    RQ --> DB
    GW --> DB
    API --> RT[Scoped realtime events]
~~~

Two data-model changes enable much of the roadmap: membership makes notebook
access explicit, and threads separate conversation history from the notebook
evidence boundary.

~~~mermaid
erDiagram
    USERS ||--o{ NOTEBOOK_MEMBERS : receives
    NOTEBOOKS ||--o{ NOTEBOOK_MEMBERS : shares_with
    NOTEBOOKS ||--o{ CHAT_THREADS : contains
    CHAT_THREADS ||--o{ CHAT_MESSAGES : groups
    SOURCES ||--o{ SOURCE_VERSIONS : has
    SOURCES ||--o{ SOURCE_SUMMARIES : produces
    NOTEBOOKS ||--o{ SEARCH_DOCUMENTS : indexes

    NOTEBOOK_MEMBERS {
      uuid notebook_id FK
      uuid user_id FK
      enum role
      uuid invited_by FK
      timestamptz created_at
    }
    CHAT_THREADS {
      uuid id PK
      uuid notebook_id FK
      text title
      uuid created_by FK
      timestamptz updated_at
      timestamptz archived_at
    }
    SOURCE_VERSIONS {
      uuid id PK
      uuid source_id FK
      text content_hash
      text storage_key
      enum status
      timestamptz created_at
    }
    SOURCE_SUMMARIES {
      uuid id PK
      uuid source_version_id FK
      text content
      uuid_array cited_chunk_ids
      text prompt_version
      uuid llm_call_id FK
    }
~~~

## 3. Roadmap

| Priority | Capability | Reason and dependency |
|---|---|---|
| P0 | Search within one notebook | High user value; exercises existing source/message data without changing access control. |
| P0 | Per-source summary | Reuses the gateway and improves source navigation; needs version-aware validity. |
| P1 | Chat threads and history tab | Removes the one-transcript limitation while keeping retrieval notebook-scoped. |
| P1 | Durable uploads and source versions | Enables reliable retries, URL refresh, and summary invalidation. |
| P2 | Sharing and collaboration | Changes the trust boundary; needs roles, invitations, audit, and realtime isolation. |
| P2 | Retrieval evaluation and quality | Measures grounding before adding complexity such as reranking. |
| P3 | Tenant budgets and operational hardening | Required when multiple users compete for provider quota and jobs outlive web processes. |

## 4. In-notebook search across sources and messages (P0)

### Outcome

A user searches one notebook and receives ranked, filterable source-chunk and
chat-message results. Selecting a result opens its source context or jumps to
the message. Search must never reveal content outside the selected notebook.

### HLD

Add a Notebook Search Service. It first verifies notebook access, then runs
lexical and optional semantic search over two document types:

- Existing chunks, using source text and the existing embedding index.
- Messages, indexed through a search projection or generated text-search
  column.

PostgreSQL full-text search with a GIN index is a good first lexical layer.
Combining lexical and vector ranks using reciprocal-rank fusion keeps exact
phrases useful while preserving semantic recall. Search is navigation only;
it does not silently add content to an LLM prompt.

~~~mermaid
sequenceDiagram
    actor U as User
    participant FE as Search panel
    participant API as API
    participant ACL as Access check
    participant S as Search service
    participant DB as PostgreSQL

    U->>FE: Search notebook
    FE->>API: GET notebook search with query and filters
    API->>ACL: require viewer access
    API->>S: normalized query + notebook ID
    S->>DB: lexical and optional vector ranks
    S->>S: fuse ranks; create snippets; paginate
    S-->>API: typed results
    API-->>FE: results within authorized notebook
~~~

### Data/API design

- Add a search-documents projection keyed by notebook ID, entity type, and
  entity ID. It separates ranking metadata from source tables and supports
  stable snippets.
- Index the notebook predicate together with full-text/vector access paths.
  On message deletion or edits, update the projection transactionally.
- Add GET notebook search with query, scope (all/sources/messages), and cursor
  pagination. Results include type, score, source/thread/message ID, and
  bounded snippet.
- Do not expose counts, snippets, or autocomplete before authorization.

### Acceptance criteria

Test exact phrase search, semantic search, filters, cursor stability, result
jump targets, and 404 behavior for a notebook that is absent or unavailable to
the caller. Benchmark representative notebooks before choosing hybrid search.

## 5. Per-source summarize-this quick action (P0)

### Outcome

A ready source can be summarized without leaving the notebook. The summary is
clearly generated, cites the source chunks used, is reused when current, and
can be explicitly refreshed.

### HLD

Add a Summary Orchestrator that reads chunks for exactly one source and calls
the existing gateway. Small sources use one grounded summary prompt. Large
sources use map-reduce: summarize chunk groups, then synthesize the group
summaries. The gateway remains the single owner of provider fallback, cache,
cost, and latency logging.

~~~mermaid
flowchart LR
    A[Quick action] --> B{Current summary exists for source version?}
    B -- yes --> C[Return stored summary]
    B -- no or refresh --> D[Load source-version chunks]
    D --> E{Fits prompt budget?}
    E -- yes --> F[One grounded summary call]
    E -- no --> G[Map chunk groups]
    G --> H[Reduce group summaries]
    F --> I[Persist summary + citations + provenance]
    H --> I
    I --> J[Display summary]
~~~

### Data/API design

- Add source versions first. A summary must reference the source version it
  describes, not just the mutable source ID.
- Add source-summaries with source/version IDs, kind, content, cited chunk
  IDs, model, prompt version, LLM-call ID, and timestamps. A unique
  source-version/kind key makes ordinary requests idempotent.
- Add create-or-return and read endpoints for a source summary. Long
  map-reduce jobs should return a status object like ingestion rather than
  holding an HTTP request open.
- Store a dedicated summary prompt version so old output stays explainable
  after the prompt changes.

### Acceptance criteria

The source ID must be a hard retrieval boundary. Test one-pass and map-reduce
summaries, idempotency, explicit refresh, summary invalidation after source
update, citations, deletion cleanup, and authorization. The UI should never
present a generated summary as original source text.

## 6. Chat threads and a notebook history tab (P1)

### Outcome

A notebook contains multiple named chats. A user can create, switch, rename,
archive, and resume them in a history tab. Every chat retrieves from the same
notebook sources, but only replays its selected thread's prior messages.

### HLD

Introduce a Thread Service. A thread belongs to one notebook; each message
belongs to one thread. The RAG call receives both notebook and thread IDs:
notebook scopes evidence, thread scopes conversational memory. This makes
threads a data-model and prompt-history feature, not just a sidebar.

~~~mermaid
sequenceDiagram
    participant UI as History tab
    participant API as Thread/chat API
    participant DB as PostgreSQL
    participant RAG as RAG service

    UI->>API: Create thread
    API->>DB: insert title and creator
    UI->>API: Send message to selected thread
    API->>DB: load selected thread history
    API->>RAG: notebook chunks + thread history
    RAG-->>API: grounded response
    API->>DB: persist both turns with thread ID
    API-->>UI: response and updated thread timestamp
~~~

### Data/API design

- Add chat-threads with notebook ID, title, creator, timestamps, and archive
  marker; index notebook ID plus descending updated time.
- Add a nullable thread ID to chat messages. Backfill every existing notebook
  into one General thread, update reads/writes, then make it required.
- Add create/list/update/archive thread routes and thread-scoped messages,
  buffered chat, streaming chat, and regenerate routes.
- Export should contain a thread table of contents and one transcript per
  thread. Archive hides a thread by default but retains it for export/audit.

### Acceptance criteria

Use a staged migration: add nullable field, seed threads, backfill messages,
deploy thread-aware paths, add not-null constraint. Test thread-history
isolation, shared notebook retrieval, sort order, archive, regeneration in
place, and access checks.

## 7. Durable uploads, source versions, and re-ingestion (P1)

### Outcome

Users can retry failed uploads, refresh a URL, see when source content changed,
and keep historic citations and summaries tied to known versions.

### HLD

Move original file bytes to object storage and move work from FastAPI
background tasks to a durable queue/worker. The API creates a source version
and ingestion job; the worker downloads/fetches, parses, chunks, embeds, and
atomically marks that version current. Progress remains pollable and job
payload survives a web-process restart.

~~~mermaid
flowchart LR
    UI --> API[Sources API]
    API --> OBJ[Object storage]
    API --> DB[(Source/version/job records)]
    API --> Q[Durable queue]
    Q --> W[Ingestion worker]
    W --> OBJ
    W --> EXT[URL fetcher and embeddings]
    W --> DB
    W --> P[Progress event or status]
~~~

### Data/API design

- Add source-versions with content hash, storage key, parser metadata, status,
  and current-version relation. Chunks reference a source version.
- Add ingestion-jobs with attempt count, idempotency key, failure reason,
  lifecycle timestamps, and a worker lease/heartbeat when distributed.
- Add URL refresh and upload reprocess. Mark a replacement current only after
  its chunks are ready; retain old chunks long enough for historical citations.
- Validate detected content type, use signed direct-upload URLs, and scan
  untrusted uploads before parsing in the worker.

### Acceptance criteria

Verify crash recovery, duplicate enqueue idempotency, retained-file retry,
version-aware summary invalidation, safe retention cleanup, and historical
citation rendering after a source refresh.

## 8. Notebook sharing and collaboration (P2)

### Outcome

An owner shares a notebook with authenticated people. Viewers can read;
editors can manage sources, threads, and messages; owners can manage access.
Changes appear to collaborators without refresh, and access can be revoked.

### HLD

This extends current Google identity but replaces single-owner-only checks with
a Notebook Authorization Service. It evaluates a membership role for every
notebook-scoped operation. An Invitation Service creates expiring, auditable
invites. A Collaboration Event Service emits narrow events after committed REST
mutations; REST remains the source of truth.

| Role | Permission |
|---|---|
| Owner | All actions, membership management, deletion, and ownership transfer. |
| Editor | Read/write sources, threads, and messages; no access administration. |
| Viewer | Read sources, threads, messages, citations, and exports only. |

~~~mermaid
sequenceDiagram
    actor O as Owner
    participant API as FastAPI
    participant ACL as Authorization
    participant DB as PostgreSQL
    participant INV as Invitation service
    participant RT as Realtime channel
    actor C as Collaborator

    O->>API: Invite account as editor
    API->>ACL: require owner
    API->>INV: create expiring invitation
    INV->>DB: persist invitation and audit event
    C->>API: accept invitation
    API->>DB: create membership
    C->>RT: subscribe after membership check
    O->>API: Commit source/thread/message mutation
    API->>DB: commit
    API->>RT: publish notebook-scoped event
    RT-->>C: refetch affected state
~~~

### Data/API design

- Add notebook-members with unique notebook/user key, role, inviter, and
  timestamps. Keep notebook owner for accountability, but access must derive
  from membership once sharing launches.
- Add notebook-invitations with invitee identity, proposed role, opaque
  single-use token hash, expiry, accepted/revoked timestamps. Authorization
  decisions must not exist only in a client-visible token.
- Add append-only audit events for sharing, removal, deletion, and ownership
  transfer.
- Replace the ownership helper with require-notebook-role, and invoke it before
  loading any child resource. Filtering by notebook ID alone is insufficient.
- Revalidate membership at realtime connection and event delivery. Revocation
  must terminate active subscriptions and invalidate cached access.

### Acceptance criteria

Test every role against every endpoint, invitation expiry/replay, removal
during an active session, event fan-out isolation, optimistic concurrency for
renames, and audit trail completeness. Sharing gives data access; it does not
by itself solve shared provider quota.

## 9. Retrieval quality and evaluation (P2)

Add a Retrieval Quality Layer before prompt construction and an offline
Evaluation Runner. Start by creating a versioned set of notebook/query/expected
citation cases around existing top-k vector retrieval. Measure retrieval
recall, citation precision, grounded-answer quality, abstention correctness,
latency, and cost. Only introduce lexical-plus-vector retrieval, metadata
filters, or reranking after a measured failure supports it.

Carry rank, distance, algorithm version, and filter metadata into traces or a
retrieval-log table. Evaluation data must be consented/synthetic; a customer's
notebook should not become a shared test corpus. Every quality change must
preserve notebook and user authorization predicates.

## 10. Tenant budgets, durable jobs, and operations (P3)

At real multi-user scale, split current process-local controls into:

- Tenant Budget Service: user/notebook request, token, and spend budgets
  checked before gateway dispatch; reserve and reconcile usage atomically.
- Durable Task Platform: source jobs, summaries, exports, and evaluations
  survive deploys and retries.
- Observability Pipeline: structured request IDs, metrics, traces, alerts, and
  error reporting separate from the product-facing LLM-call ledger.

The gateway should remain the authority on provider-wide capacity; tenant
budgets are an earlier fair-use boundary. Validate atomic budget reservations,
provider-wide versus tenant limits, queued-job recovery, alert signals, and
database/queue/realtime capacity with load tests.

## 11. Explicit non-goals for now

- Autonomous tool-using agents. Source discovery is intentionally separate
  from grounded chat and would require a different permission/audit model.
- Fine-grained collaborative document editing. It is far more complex than
  notebook membership and not needed for shared RAG conversations.
- Multi-region active-active infrastructure. It should follow concrete
  availability, latency, and data-residency requirements.

## 12. Interview narrative

The project evolves by hardening boundaries in the right order: first make
single-user RAG observable and reliable; next improve navigation, summaries,
and conversation structure; then change the trust boundary with collaboration;
finally add fair-use and durable operations. Search, threads, and sharing are
therefore architectural features with data-model and authorization consequences,
not just UI additions.

For current implementation details, see [HLD.md](HLD.md) and [LLD.md](LLD.md).

