"""Notebook CRUD (minimal — create/list/get), scoped to the signed-in user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Notebook, User
from app.schemas import NotebookCreate, NotebookOut, NotebookUpdate

router = APIRouter(prefix="/notebooks", tags=["notebooks"])


def owned_notebook(
    notebook_id: uuid.UUID, db: Session, user: User
) -> Notebook:
    """Fetch a notebook the caller owns, or 404.

    Deliberately 404 rather than 403 for someone else's notebook — a 403 would
    confirm that the id exists.
    """
    notebook = db.get(Notebook, notebook_id)
    if notebook is None or notebook.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")
    return notebook


@router.post("", response_model=NotebookOut, status_code=status.HTTP_201_CREATED)
def create_notebook(
    payload: NotebookCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Notebook:
    notebook = Notebook(title=payload.title, user_id=user.id)
    db.add(notebook)
    db.commit()
    db.refresh(notebook)
    return notebook


@router.get("", response_model=list[NotebookOut])
def list_notebooks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Notebook]:
    return list(
        db.scalars(
            select(Notebook)
            .where(Notebook.user_id == user.id)
            .order_by(Notebook.created_at.desc())
        )
    )


@router.get("/{notebook_id}", response_model=NotebookOut)
def get_notebook(
    notebook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Notebook:
    return owned_notebook(notebook_id, db, user)


@router.patch("/{notebook_id}", response_model=NotebookOut)
def rename_notebook(
    notebook_id: uuid.UUID,
    payload: NotebookUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Notebook:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "title is empty")
    notebook = owned_notebook(notebook_id, db, user)
    notebook.title = title
    db.commit()
    db.refresh(notebook)
    return notebook


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_notebook(
    notebook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a notebook and everything under it.

    Sources, chunks, messages, llm_calls, and cache entries all cascade at the
    database level (ondelete="CASCADE" on their foreign keys).
    """
    notebook = owned_notebook(notebook_id, db, user)
    db.delete(notebook)
    db.commit()
