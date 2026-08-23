/**
 * Typed client for the FastAPI backend.
 *
 * Shapes mirror `backend/app/schemas.py` exactly — notably `ChatResponse`,
 * which feeds the transparency panel.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type SourceType = "pdf" | "docx" | "url";
export type SourceStatus = "pending" | "processing" | "ready" | "failed";

export interface Notebook {
  id: string;
  title: string;
  created_at: string;
}

export interface Source {
  id: string;
  notebook_id: string;
  type: SourceType;
  original_name_or_url: string;
  status: SourceStatus;
  /** 0-100, coarse checkpoint progress through parse/chunk/embed/store. */
  progress: number;
  ingested_at: string;
}

export interface Citation {
  marker: number;
  chunk_id: string;
  source_id: string;
  snippet: string;
}

/** Exact shape returned by POST /notebooks/{id}/chat. */
export interface ChatResponse {
  answer: string;
  citations: Citation[];
  provider: string;
  /** Which model answered — distinguishes a fallback from a primary call. */
  model: string;
  /** "ok" | "fallback" | "error" */
  status: string;
  latency_ms: number;
  cost_usd: number;
  cache_hit: boolean;
  /** The persisted assistant message id — null only for the "no sources
   * yet" canned reply. Needed to call regenerateAnswer on this message. */
  message_id: string | null;
}

/** A persisted message, as returned by GET /notebooks/{id}/messages. */
export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations: Citation[];
  provider: string | null;
  model: string | null;
  status: string | null;
  latency_ms: number | null;
  cost_usd: number | null;
  cache_hit: boolean | null;
}

export function listMessages(notebookId: string): Promise<StoredMessage[]> {
  return request<StoredMessage[]>(`/notebooks/${notebookId}/messages`, {
    cache: "no-store",
  });
}

export function deleteNotebook(notebookId: string): Promise<void> {
  return requestVoid(`/notebooks/${notebookId}`, { method: "DELETE" });
}

export function renameNotebook(
  notebookId: string,
  title: string,
): Promise<Notebook> {
  return request<Notebook>(`/notebooks/${notebookId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function deleteSource(
  notebookId: string,
  sourceId: string,
): Promise<void> {
  return requestVoid(`/notebooks/${notebookId}/sources/${sourceId}`, {
    method: "DELETE",
  });
}

/** Download a notebook's sources + chat transcript as a Markdown file.
 * Returns the blob and the filename the backend suggested (from
 * Content-Disposition) — bypasses the JSON-only `request` helper since this
 * response isn't JSON. */
export async function exportNotebook(
  notebookId: string,
): Promise<{ blob: Blob; filename: string }> {
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE_URL}/notebooks/${notebookId}/export`,
      withAuth(),
    );
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
      0,
    );
  }
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await res.blob(), filename: match?.[1] ?? "notebook.md" };
}

/** Re-run ingestion for a failed source. URL sources only — uploaded files'
 * bytes aren't kept past the original request, so those need re-uploading. */
export function retrySource(
  notebookId: string,
  sourceId: string,
): Promise<Source> {
  return request<Source>(`/notebooks/${notebookId}/sources/${sourceId}/retry`, {
    method: "POST",
  });
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Called on any 401 from the backend — an expired/invalid Google ID token,
 * or a missing one. Registered by AuthGate (like setAuthTokenGetter) rather
 * than imported, so this module stays free of any auth-library dependency.
 * Without this, an expired token surfaced as a raw error message in whatever
 * UI happened to be making the request, with no way back to sign-in short of
 * a manual reload.
 */
let unauthorizedHandler: (() => void) | undefined;

export function setUnauthorizedHandler(handler: (() => void) | undefined): void {
  unauthorizedHandler = handler;
}

/** FastAPI errors come back as `{detail: ...}`; unwrap to a readable string. */
async function toApiError(res: Response): Promise<ApiError> {
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail)) {
      // Pydantic validation errors.
      message = detail
        .map((d: { msg?: string }) => d?.msg ?? JSON.stringify(d))
        .join("; ");
    }
  } catch {
    // Non-JSON body (e.g. a proxy error page) — keep the status line.
  }
  if (res.status === 401) unauthorizedHandler?.();
  return new ApiError(message, res.status);
}

/**
 * Supplies the Google ID token for outgoing requests.
 *
 * Registered by AuthGate rather than imported, so this module stays free of
 * any auth-library dependency and still works when auth is switched off.
 */
let authTokenGetter: () => string | undefined = () => undefined;

export function setAuthTokenGetter(getter: () => string | undefined): void {
  authTokenGetter = getter;
}

/** Merge the bearer token into request headers, when signed in. */
function withAuth(init?: RequestInit): RequestInit {
  const token = authTokenGetter();
  if (!token) return init ?? {};
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers };
}

export function getAuthConfig(): Promise<{ auth_required: boolean }> {
  return request<{ auth_required: boolean }>("/auth/config", {
    cache: "no-store",
  });
}

export interface CurrentUser {
  id: string;
  email: string | null;
  name: string | null;
  picture: string | null;
}

export function getMe(): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/me", { cache: "no-store" });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, withAuth(init));
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
      0,
    );
  }
  if (!res.ok) throw await toApiError(res);
  return res.json() as Promise<T>;
}

/** For endpoints that answer 204 with no body. */
async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, withAuth(init));
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
      0,
    );
  }
  if (!res.ok) throw await toApiError(res);
}

export function listNotebooks(): Promise<Notebook[]> {
  return request<Notebook[]>("/notebooks", { cache: "no-store" });
}

export function getNotebook(id: string): Promise<Notebook> {
  return request<Notebook>(`/notebooks/${id}`, { cache: "no-store" });
}

/** Omit title (or pass "") to get "Untitled" — auto-renamed once the first
 * source finishes ingesting. */
export function createNotebook(title?: string): Promise<Notebook> {
  return request<Notebook>("/notebooks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title?.trim() || null }),
  });
}

export function listSources(notebookId: string): Promise<Source[]> {
  return request<Source[]>(`/notebooks/${notebookId}/sources`, {
    cache: "no-store",
  });
}

/** Upload a PDF/DOCX. Returns 202 with status=pending; ingestion runs async. */
export function addFileSource(
  notebookId: string,
  file: File,
): Promise<Source> {
  const form = new FormData();
  form.append("file", file);
  // No explicit Content-Type: the browser sets the multipart boundary.
  return request<Source>(`/notebooks/${notebookId}/sources`, {
    method: "POST",
    body: form,
  });
}

export function addUrlSource(
  notebookId: string,
  url: string,
): Promise<Source> {
  return request<Source>(`/notebooks/${notebookId}/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
}

/** A web-search hit — a candidate URL to add as a source. */
export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

/** Whether the backend has a search API key configured. */
export function getSearchStatus(): Promise<{ configured: boolean }> {
  return request<{ configured: boolean }>("/search/status", {
    cache: "no-store",
  });
}

export function searchWeb(query: string): Promise<SearchResponse> {
  return request<SearchResponse>(`/search?q=${encodeURIComponent(query)}`, {
    cache: "no-store",
  });
}

/** Mirrors backend/app/schemas.py StatsResponse — see GET /stats. */
export interface ProviderStat {
  provider: string;
  calls: number;
  cost_usd: number;
}
export interface ModelStat {
  model: string;
  calls: number;
  cost_usd: number;
}
export interface StatusStat {
  status: string;
  count: number;
}
export interface DailyStat {
  date: string;
  calls: number;
  cost_usd: number;
  cache_hits: number;
}
export interface NotebookStat {
  notebook_id: string;
  title: string;
  calls: number;
  cost_usd: number;
}

/** Current usage vs. each chain model's free-tier quota. Account-wide, not
 * scoped to the caller. */
export interface RateLimitStat {
  provider: string;
  model: string;
  requests_today: number;
  rpd_limit: number | null;
  requests_this_minute: number;
  rpm_limit: number | null;
}
export interface StatsResponse {
  total_calls: number;
  total_cost_usd: number;
  cache_hits: number;
  cache_hit_rate: number;
  fallback_count: number;
  error_count: number;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  by_provider: ProviderStat[];
  by_model: ModelStat[];
  by_status: StatusStat[];
  daily: DailyStat[];
  top_notebooks: NotebookStat[];
  rate_limits: RateLimitStat[];
}

export function getStats(): Promise<StatsResponse> {
  return request<StatsResponse>("/stats", { cache: "no-store" });
}

export function sendChat(
  notebookId: string,
  query: string,
): Promise<ChatResponse> {
  return request<ChatResponse>(`/notebooks/${notebookId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
}

/** Re-run an assistant message's answer, bypassing the LLM cache. Updates
 * the message in place server-side — the transcript doesn't grow. */
export function regenerateAnswer(
  notebookId: string,
  messageId: string,
): Promise<ChatResponse> {
  return request<ChatResponse>(
    `/notebooks/${notebookId}/messages/${messageId}/regenerate`,
    { method: "POST" },
  );
}

export interface StreamHandlers {
  onToken: (text: string) => void;
  /** Fires once with the same payload shape as the buffered endpoint. */
  onDone: (result: ChatResponse) => void;
  onError: (message: string) => void;
}

/**
 * Stream an answer over Server-Sent Events.
 *
 * Uses fetch + ReadableStream rather than `EventSource`, which can only issue
 * GET requests and cannot send a JSON body.
 */
export async function streamChat(
  notebookId: string,
  query: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE_URL}/notebooks/${notebookId}/chat/stream`,
      withAuth({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
        signal,
      }),
    );
  } catch {
    if (signal?.aborted) return;
    throw new ApiError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
      0,
    );
  }
  // Pre-stream failures (404 notebook, 422 empty query) are still plain HTTP.
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw new ApiError("Streaming is not supported here.", 0);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let event = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7).trim();
          else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
        }
        if (dataLines.length === 0) continue;

        const payload = JSON.parse(dataLines.join("\n"));
        if (event === "token") handlers.onToken(payload.text as string);
        else if (event === "done") handlers.onDone(payload as ChatResponse);
        else if (event === "error") handlers.onError(payload.message as string);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
