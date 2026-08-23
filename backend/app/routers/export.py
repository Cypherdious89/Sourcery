"""Markdown export of a notebook's sources + full chat transcript."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.export import build_markdown
from app.models import Source, User
from app.routers.chat import list_messages
from app.routers.notebooks import owned_notebook

router = APIRouter(prefix="/notebooks", tags=["export"])


def _safe_filename(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", title).strip("-")
    return f"{slug or 'notebook'}.md"


@router.get("/{notebook_id}/export")
def export_notebook(
    notebook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    notebook = owned_notebook(notebook_id, db, user)
    sources = list(
        db.scalars(
            select(Source)
            .where(Source.notebook_id == notebook_id)
            .order_by(Source.ingested_at)
        )
    )
    messages = list_messages(notebook_id, db, user)

    markdown = build_markdown(notebook, sources, messages)
    filename = _safe_filename(notebook.title)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
