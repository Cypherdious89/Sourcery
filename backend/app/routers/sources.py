"""Source ingestion endpoints.

POST /notebooks/{id}/sources accepts EITHER:
  * a multipart file upload (field name ``file``) of a PDF or DOCX, or
  * a JSON body ``{"url": "https://..."}``.

The upload returns immediately (202) with status=pending; the parse/chunk/
embed/store work runs in a FastAPI BackgroundTask.
"""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.ingestion import ingest_source
from app.models import Source, SourceStatus, SourceType, User
from app.routers.notebooks import owned_notebook
from app.schemas import SourceOut, SourceURLCreate

router = APIRouter(prefix="/notebooks", tags=["sources"])

_EXT_TO_TYPE = {".pdf": SourceType.pdf, ".docx": SourceType.docx}
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB guardrail


@router.post(
    "/{notebook_id}/sources",
    response_model=SourceOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_source(
    notebook_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Source:
    owned_notebook(notebook_id, db, user)
    content_type = request.headers.get("content-type", "")

    file_bytes: bytes | None = None
    url: str | None = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "multipart request must include a 'file' field",
            )
        filename = upload.filename or ""
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        source_type = _EXT_TO_TYPE.get(ext)
        if source_type is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Unsupported file type; only .pdf and .docx are accepted",
            )
        file_bytes = await upload.read()
        if not file_bytes:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
        if len(file_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
            )
        original = filename

    elif content_type.startswith("application/json"):
        try:
            payload = SourceURLCreate.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Invalid JSON body; expected {{'url': ...}}: {exc}",
            ) from exc
        source_type = SourceType.url
        url = str(payload.url)
        original = url

    else:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Send multipart/form-data with a 'file', or application/json "
            "with a 'url'.",
        )

    # Persist the source row up front so the client gets an id + pending status.
    source = Source(
        notebook_id=notebook_id,
        type=source_type,
        original_name_or_url=original,
        status=SourceStatus.pending,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    background.add_task(
        ingest_source,
        source.id,
        notebook_id,
        source_type,
        file_bytes=file_bytes,
        url=url,
    )
    return source


@router.get("/{notebook_id}/sources", response_model=list[SourceOut])
def list_sources(
    notebook_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Source]:
    owned_notebook(notebook_id, db, user)
    return list(
        db.scalars(
            select(Source)
            .where(Source.notebook_id == notebook_id)
            .order_by(Source.ingested_at.desc())
        )
    )


@router.get("/{notebook_id}/sources/{source_id}", response_model=SourceOut)
def get_source(
    notebook_id: uuid.UUID,
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Source:
    owned_notebook(notebook_id, db, user)
    source = db.get(Source, source_id)
    if source is None or source.notebook_id != notebook_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return source


@router.delete(
    "/{notebook_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    # `-> None` would otherwise be inferred as a NoneType response model,
    # which FastAPI rejects for a bodyless 204.
    response_model=None,
)
def delete_source(
    notebook_id: uuid.UUID,
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a source; its chunks cascade, so the notebook stops citing it."""
    owned_notebook(notebook_id, db, user)
    source = db.get(Source, source_id)
    if source is None or source.notebook_id != notebook_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    db.delete(source)
    db.commit()


@router.post(
    "/{notebook_id}/sources/{source_id}/retry",
    response_model=SourceOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_source(
    notebook_id: uuid.UUID,
    source_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Source:
    """Re-run ingestion for a failed source.

    Only ``url`` sources can be retried this way: their URL is stored, so
    re-fetching is just re-running the same pipeline. Uploaded files' bytes
    are never persisted past the original request (no blob storage in this
    app), so there's nothing to re-parse — those need a fresh upload instead.
    """
    owned_notebook(notebook_id, db, user)
    source = db.get(Source, source_id)
    if source is None or source.notebook_id != notebook_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    if source.status != SourceStatus.failed:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a failed source can be retried"
        )
    if source.type != SourceType.url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Uploaded files aren't kept after ingestion — delete this source "
            "and upload the file again instead of retrying",
        )

    source.status = SourceStatus.pending
    source.progress = 0
    source.error_code = None
    source.error_message = None
    db.commit()
    db.refresh(source)

    background.add_task(
        ingest_source,
        source.id,
        notebook_id,
        source.type,
        url=source.original_name_or_url,
    )
    return source
