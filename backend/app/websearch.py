"""Web search adapter (Tavily Search API).

Search is a *source-discovery* feature, not a retrieval path: it returns links,
which the caller then feeds to the existing URL ingestion pipeline
(``POST /notebooks/{id}/sources``). Nothing here touches the RAG flow, so
citations keep resolving to real ``chunks`` rows.

Tavily can also return already-extracted page text (``include_raw_content``),
but we deliberately don't use it — letting the normal trafilatura ingestion
fetch each URL keeps one parsing path for uploads, pasted URLs, and search
results alike.

Kept thin and behind a typed error taxonomy, mirroring ``providers.py`` — the
router never sees an httpx exception.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger("app.websearch")

_ENDPOINT = "https://api.tavily.com/search"
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchError(Exception):
    """Search failed in a way the caller should surface to the user."""


class SearchNotConfigured(SearchError):
    """No TAVILY_API_KEY set — the feature is switched off."""


def is_configured() -> bool:
    return bool(get_settings().tavily_api_key)


def _clean(text: str | None) -> str:
    """Strip any markup and decode HTML entities."""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", text)).strip()


def search(query: str, count: int | None = None) -> list[SearchResult]:
    """Run a web search and return result links.

    Raises ``SearchNotConfigured`` if no API key is set, or ``SearchError``
    on rate limiting / auth failure / upstream error.
    """
    settings = get_settings()
    api_key = settings.tavily_api_key
    if not api_key:
        raise SearchNotConfigured(
            "Web search is not configured. Set TAVILY_API_KEY to enable it."
        )

    n = min(max(count or settings.search_result_count, 1), 20)

    try:
        response = httpx.post(
            _ENDPOINT,
            json={
                "query": query,
                "max_results": n,
                # "basic" costs 1 API credit per search; "advanced" costs 2.
                "search_depth": "basic",
                # We re-fetch each chosen URL through the normal ingestion
                # pipeline, so there's no need to pay for content here.
                "include_raw_content": False,
                "include_answer": False,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=settings.search_timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise SearchError(
            f"Web search timed out after {settings.search_timeout_seconds}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise SearchError(f"Web search request failed: {exc}") from exc

    if response.status_code >= 400:
        raise _error_for(response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise SearchError("Web search returned a malformed response.") from exc

    return parse_results(payload)


def _error_for(response: httpx.Response) -> SearchError:
    """Turn a Tavily error response into an actionable message.

    Tavily reports problems as ``{"detail": {"error": "..."}}`` (and sometimes
    a bare ``{"error": "..."}``), so pull the human-readable string out rather
    than surfacing a naked status code.
    """
    detail = ""
    try:
        body = response.json()
        raw = body.get("detail", body)
        if isinstance(raw, dict):
            detail = raw.get("error") or raw.get("message") or ""
        elif isinstance(raw, str):
            detail = raw
    except ValueError:
        pass

    if response.status_code in (401, 403):
        return SearchError(
            "Web search rejected the API key. Check TAVILY_API_KEY "
            "(get one at https://app.tavily.com/)."
        )
    if response.status_code == 429:
        return SearchError("Web search rate limit reached. Wait a moment and retry.")
    # 432/433 are Tavily's plan/credit-exhausted codes.
    if response.status_code in (432, 433):
        return SearchError(
            "Web search credits exhausted for this plan. "
            f"{detail}".strip()
        )
    if detail:
        return SearchError(f"Web search failed: {detail}")
    return SearchError(f"Web search failed with HTTP {response.status_code}.")


def parse_results(payload: dict) -> list[SearchResult]:
    """Extract results from a Tavily search payload.

    Split out from ``search`` so parsing can be exercised without a live API
    key. Tavily returns a flat ``results`` list of
    ``{title, url, content, score}``; entries missing a URL are skipped rather
    than surfaced as broken rows.
    """
    raw = payload.get("results") or []
    results: list[SearchResult] = []
    for item in raw:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                title=_clean(item.get("title")) or url,
                url=url,
                # Tavily calls the relevant excerpt "content".
                snippet=_clean(item.get("content")),
            )
        )
    return results
