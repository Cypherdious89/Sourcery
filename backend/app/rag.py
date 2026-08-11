"""RAG retrieval, prompt construction, and citation parsing (see SPEC "RAG Flow")."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk

# Markers are `[S1]`, not `[1]`. Retrieved chunks — Wikipedia especially —
# are full of their own `[1]`-style footnotes that the model sometimes echoes,
# which a bare-digit pattern would misread as a citation.
_MARKER_RE = re.compile(r"\[S(\d+)\]")

_SYSTEM_INSTRUCTIONS = (
    "You are a helpful assistant that answers questions using ONLY the provided "
    "sources. Each source is labelled with a marker like [S1], [S2]. Base your "
    "answer strictly on these sources. Cite the markers you actually use inline "
    "in your answer, e.g. \"the sky is blue [S1][S2]\". Use the exact [S<number>] "
    "form — never a bare number in brackets, even if the source text contains "
    "one. If the sources do not contain the answer, say that you don't know."
)

_HISTORY_INSTRUCTIONS = (
    "The conversation so far is provided for context. Use it to resolve "
    "follow-up references (\"it\", \"that\"), but still answer only from the "
    "sources below."
)

# Keep replayed history cheap — old answers can be long.
_HISTORY_SNIPPET_CHARS = 400


def retrieve_chunks(
    db: Session, notebook_id: uuid.UUID, query_embedding: list[float], k: int
) -> list[tuple[Chunk, float]]:
    """Top-k cosine similarity search over a notebook's chunks (pgvector <=>).

    Returns ``(chunk, distance)`` pairs, nearest first. ``distance`` is cosine
    distance in [0, 2]; similarity = 1 - distance.
    """
    distance = Chunk.embedding.cosine_distance(query_embedding).label("distance")
    stmt = (
        select(Chunk, distance)
        .where(Chunk.notebook_id == notebook_id)
        .order_by(distance)
        .limit(k)
    )
    return [(row[0], float(row[1])) for row in db.execute(stmt).all()]


def format_history(turns: list[tuple[str, str]]) -> str:
    """Render prior turns as ``User:``/``Assistant:`` lines, truncated."""
    lines = []
    for role, content in turns:
        text = " ".join(content.split())
        if len(text) > _HISTORY_SNIPPET_CHARS:
            text = text[:_HISTORY_SNIPPET_CHARS].rstrip() + "…"
        lines.append(f"{'User' if role == 'user' else 'Assistant'}: {text}")
    return "\n".join(lines)


def build_prompt(
    query: str,
    chunks: list[Chunk],
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Assemble the grounded prompt with 1-indexed ``[S1]`` source markers.

    ``history`` is prior (role, content) turns, oldest first, so follow-up
    questions can resolve pronouns against the conversation.
    """
    lines = [f"[S{i}] {chunk.content}" for i, chunk in enumerate(chunks, start=1)]
    sources_block = "\n".join(lines)

    parts = [_SYSTEM_INSTRUCTIONS]
    if history:
        parts.append(
            f"\n{_HISTORY_INSTRUCTIONS}\n\nConversation so far:\n"
            f"{format_history(history)}"
        )
    parts.append(f"\nSources:\n{sources_block}\n\nQuestion: {query}\n\nAnswer:")
    return "\n".join(parts)


def parse_cited_markers(answer: str, valid_markers: set[int]) -> list[int]:
    """Extract cited markers from the answer, in order, de-duplicated.

    Only markers that correspond to a retrieved chunk are kept.
    """
    used: list[int] = []
    seen: set[int] = set()
    for match in _MARKER_RE.finditer(answer):
        n = int(match.group(1))
        if n in valid_markers and n not in seen:
            seen.add(n)
            used.append(n)
    return used


def make_snippet(content: str, max_len: int = 200) -> str:
    text = " ".join(content.split())
    return text if len(text) <= max_len else text[:max_len].rstrip() + "…"
