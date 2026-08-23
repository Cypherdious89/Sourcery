"""Chat endpoints — notebook-scoped RAG with citations (see SPEC "RAG Flow").

Two shapes over the same pipeline:

* ``POST /notebooks/{id}/chat``        — buffered, returns the whole ChatResponse.
* ``POST /notebooks/{id}/chat/stream`` — Server-Sent Events: ``token`` deltas as
  the model generates, then one ``done`` event carrying the identical
  ChatResponse payload (citations + transparency metadata are only knowable
  once generation finishes).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import embeddings, gateway, rag
from app.config import get_settings
from app.auth import get_current_user
from app.db import SessionLocal, get_db
from app.models import Chunk, ChatMessage, LLMCall, MessageRole, User
from app.routers.notebooks import owned_notebook
from app.schemas import ChatRequest, ChatResponse, Citation, MessageOut

router = APIRouter(prefix="/notebooks", tags=["chat"])
_settings = get_settings()

_NO_SOURCES_ANSWER = (
    "I don't have any sources in this notebook yet, so I can't answer "
    "from them. Add a source and try again."
)


def _sse(event: str, payload: dict) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _recent_history(
    db: Session, notebook_id: uuid.UUID, limit: int
) -> list[tuple[str, str]]:
    """Last `limit` messages for this notebook, oldest first."""
    if limit <= 0:
        return []
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.notebook_id == notebook_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
    )
    return [(m.role.value, m.content) for m in reversed(rows)]


def _context_hash(chunks: list[Chunk]) -> str:
    """Fingerprint of the retrieved chunks, gating semantic cache hits.

    A paraphrase may only reuse a cached answer when retrieval returned the
    same chunks — otherwise adding or deleting a source would keep serving the
    stale answer.

    Conversation history is deliberately NOT part of this. Including it would
    make the semantic cache dead code: every turn carries a different history,
    so no two questions could ever share a context. The trade-off is that a
    paraphrased *follow-up* whose meaning depends on the conversation ("what
    about its cost?") could match an earlier one asked in a different context.
    The exact-match key still covers the full prompt including history, so this
    only affects fuzzy hits, and the tight similarity threshold keeps them rare.
    """
    return hashlib.sha256(
        "|".join(str(c.id) for c in chunks).encode()
    ).hexdigest()


def _citations_for(
    answer: str, marker_to_chunk: dict[int, Chunk]
) -> list[Citation]:
    """Map cited markers in the answer back to retrieved chunks."""
    used = rag.parse_cited_markers(answer, set(marker_to_chunk))
    return [
        Citation(
            marker=n,
            chunk_id=marker_to_chunk[n].id,
            source_id=marker_to_chunk[n].source_id,
            snippet=rag.make_snippet(marker_to_chunk[n].content),
        )
        for n in used
    ]


@router.post("/{notebook_id}/chat", response_model=ChatResponse)
def chat(
    notebook_id: uuid.UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatResponse:
    owned_notebook(notebook_id, db, user)

    query = payload.query.strip()
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "query is empty")

    # 1. Embed the query locally (same model as ingestion).
    query_embedding = embeddings.embed_text(query)

    # 2. Top-k cosine similarity search scoped to this notebook.
    results = rag.retrieve_chunks(
        db, notebook_id, query_embedding, _settings.retrieval_top_k
    )
    chunks = [chunk for chunk, _ in results]

    # History is read BEFORE persisting this turn, so the prompt sees prior
    # exchanges but not the question being asked right now.
    history = _recent_history(db, notebook_id, _settings.chat_history_turns)

    # Persist the user message first.
    user_msg = ChatMessage(
        notebook_id=notebook_id, role=MessageRole.user, content=query
    )
    db.add(user_msg)
    db.commit()

    if not chunks:
        answer = (
            "I don't have any sources in this notebook yet, so I can't answer "
            "from them. Add a source and try again."
        )
        db.add(
            ChatMessage(
                notebook_id=notebook_id,
                role=MessageRole.assistant,
                content=answer,
                cited_chunk_ids=[],
            )
        )
        db.commit()
        return ChatResponse(
            answer=answer,
            citations=[],
            provider="none",
            model="",
            status="ok",
            latency_ms=0,
            cost_usd=0.0,
            cache_hit=False,
        )

    # 3. Build the grounded prompt with [S1], [S2], ... markers.
    marker_to_chunk = {i: chunk for i, chunk in enumerate(chunks, start=1)}
    prompt = rag.build_prompt(query, chunks, history)
    semantic = gateway.SemanticContext(
        query=query, context_hash=_context_hash(chunks)
    )

    # 4. Route through the gateway (provider-agnostic).
    try:
        result = gateway.call_llm(prompt, str(notebook_id), semantic=semantic)
    except gateway.GatewayError as exc:
        if exc.rate_limited:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "All LLM providers are currently at their rate limit. Please try again in a minute.",
            ) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"All LLM providers failed: {exc}",
        ) from exc

    # 5. Map cited markers back to chunk ids.
    citations = _citations_for(result.text, marker_to_chunk)
    cited_chunk_ids = [c.chunk_id for c in citations]

    # 6. Persist assistant message with cited_chunk_ids, and link the llm_call.
    assistant_msg = ChatMessage(
        notebook_id=notebook_id,
        role=MessageRole.assistant,
        content=result.text,
        cited_chunk_ids=cited_chunk_ids,
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    if result.llm_call_id is not None:
        db.execute(
            update(LLMCall)
            .where(LLMCall.id == result.llm_call_id)
            .values(message_id=assistant_msg.id)
        )
        db.commit()

    # 7. Return the exact transparency-panel shape.
    return ChatResponse(
        answer=result.text,
        citations=citations,
        provider=result.provider,
        model=result.model,
        status=result.status,
        latency_ms=result.latency_ms,
        cost_usd=float(result.cost_usd or 0.0),
        cache_hit=result.cache_hit,
    )


@router.post("/{notebook_id}/chat/stream")
def chat_stream(
    notebook_id: uuid.UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the answer as SSE `token` events, then a final `done` event.

    The `done` payload is byte-for-byte the same shape as `POST .../chat`, so
    the transparency panel consumes one contract either way.
    """
    owned_notebook(notebook_id, db, user)

    query = payload.query.strip()
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "query is empty")

    # Retrieval happens up-front so a failure here is still a normal HTTP
    # error rather than an error buried inside a 200 stream.
    query_embedding = embeddings.embed_text(query)
    results = rag.retrieve_chunks(
        db, notebook_id, query_embedding, _settings.retrieval_top_k
    )
    chunks = [chunk for chunk, _ in results]
    history = _recent_history(db, notebook_id, _settings.chat_history_turns)

    user_msg = ChatMessage(
        notebook_id=notebook_id, role=MessageRole.user, content=query
    )
    db.add(user_msg)
    db.commit()

    marker_to_chunk = {i: chunk for i, chunk in enumerate(chunks, start=1)}
    prompt = rag.build_prompt(query, chunks, history) if chunks else ""
    semantic = gateway.SemanticContext(
        query=query, context_hash=_context_hash(chunks)
    )

    def event_stream() -> Iterator[str]:
        # The request-scoped session is closed by the time this generator runs,
        # so persistence below uses its own session.
        if not chunks:
            yield _sse("token", {"text": _NO_SOURCES_ANSWER})
            with SessionLocal() as own:
                own.add(
                    ChatMessage(
                        notebook_id=notebook_id,
                        role=MessageRole.assistant,
                        content=_NO_SOURCES_ANSWER,
                        cited_chunk_ids=[],
                    )
                )
                own.commit()
            yield _sse(
                "done",
                ChatResponse(
                    answer=_NO_SOURCES_ANSWER,
                    citations=[],
                    provider="none",
                    model="",
                    status="ok",
                    latency_ms=0,
                    cost_usd=0.0,
                    cache_hit=False,
                ).model_dump(mode="json"),
            )
            return

        try:
            for chunk in gateway.stream_llm(
                prompt, str(notebook_id), semantic=semantic
            ):
                if chunk.text:
                    yield _sse("token", {"text": chunk.text})
                    continue
                if chunk.result is None:
                    continue

                result = chunk.result
                citations = _citations_for(result.text, marker_to_chunk)
                with SessionLocal() as own:
                    assistant_msg = ChatMessage(
                        notebook_id=notebook_id,
                        role=MessageRole.assistant,
                        content=result.text,
                        cited_chunk_ids=[c.chunk_id for c in citations],
                    )
                    own.add(assistant_msg)
                    own.commit()
                    own.refresh(assistant_msg)
                    if result.llm_call_id is not None:
                        own.execute(
                            update(LLMCall)
                            .where(LLMCall.id == result.llm_call_id)
                            .values(message_id=assistant_msg.id)
                        )
                        own.commit()

                yield _sse(
                    "done",
                    ChatResponse(
                        answer=result.text,
                        citations=citations,
                        provider=result.provider,
                        model=result.model,
                        status=result.status,
                        latency_ms=result.latency_ms,
                        cost_usd=float(result.cost_usd or 0.0),
                        cache_hit=result.cache_hit,
                    ).model_dump(mode="json"),
                )
        except gateway.GatewayError as exc:
            # The response is already a 200 by now, so failures are reported
            # in-band as an `error` event.
            if exc.rate_limited:
                message = "All LLM providers are currently at their rate limit. Please try again in a minute."
            else:
                message = f"All LLM providers failed: {exc}"
            yield _sse("error", {"message": message})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx/Render from buffering the stream into one blob.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{notebook_id}/messages", response_model=list[MessageOut])
def list_messages(
    notebook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[MessageOut]:
    """Full transcript for a notebook, oldest first.

    Rehydrates the chat panel after a reload. Assistant turns carry their
    gateway metadata, joined from ``llm_calls`` via ``message_id``, and their
    citations, re-resolved from ``cited_chunk_ids``.
    """
    owned_notebook(notebook_id, db, user)

    messages = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.notebook_id == notebook_id)
            .order_by(ChatMessage.created_at)
        )
    )
    if not messages:
        return []

    # One query for the gateway metadata rather than one per message.
    calls = {
        call.message_id: call
        for call in db.scalars(
            select(LLMCall).where(
                LLMCall.message_id.in_([m.id for m in messages])
            )
        )
    }

    # And one for every cited chunk across the whole transcript.
    cited_ids = {cid for m in messages for cid in (m.cited_chunk_ids or [])}
    chunks = {
        chunk.id: chunk
        for chunk in (
            db.scalars(select(Chunk).where(Chunk.id.in_(cited_ids)))
            if cited_ids
            else []
        )
    }

    out: list[MessageOut] = []
    for m in messages:
        # Markers are positional within the message's own citation list; the
        # original retrieval order isn't stored, so renumber from 1.
        citations = [
            Citation(
                marker=i,
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                snippet=rag.make_snippet(chunk.content),
            )
            for i, chunk in enumerate(
                (chunks[cid] for cid in (m.cited_chunk_ids or []) if cid in chunks),
                start=1,
            )
        ]
        call = calls.get(m.id)
        out.append(
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                citations=citations,
                provider=call.provider if call else None,
                model=call.model if call else None,
                status=call.status.value if call else None,
                latency_ms=call.latency_ms if call else None,
                cost_usd=float(call.cost_usd) if call and call.cost_usd is not None else None,
                cache_hit=call.cache_hit if call else None,
            )
        )
    return out
