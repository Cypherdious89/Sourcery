"""Web search endpoint — finds candidate URLs to add as notebook sources.

Deliberately *not* notebook-scoped: a search is independent of any notebook.
The client picks results and adds them via the existing
``POST /notebooks/{id}/sources`` with a ``{"url": ...}`` body, so ingestion,
chunking, embedding, and citation mapping are all unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import websearch
from app.auth import get_current_user
from app.models import User
from app.schemas import SearchResponse, SearchResultOut

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search_web(
    q: str = Query(..., description="Search query"),
    count: int | None = Query(None, ge=1, le=20),
    user: User = Depends(get_current_user),
) -> SearchResponse:
    query = q.strip()
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "q is empty")

    try:
        results = websearch.search(query, count)
    except websearch.SearchNotConfigured as exc:
        # 503 rather than 500: the service is fine, the feature is switched off.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except websearch.SearchError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return SearchResponse(
        query=query,
        results=[
            SearchResultOut(title=r.title, url=r.url, snippet=r.snippet)
            for r in results
        ],
    )


@router.get("/search/status")
def search_status() -> dict[str, bool]:
    """Lets the UI show or hide the search box without provoking an error."""
    return {"configured": websearch.is_configured()}
