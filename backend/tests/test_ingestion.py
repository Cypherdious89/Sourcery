"""Behavior tests for safe ingestion failure reporting."""

from app.parsing import ParseError


def test_classify_ingestion_failure_reports_safe_empty_content_reason():
    """A parser's raw error must become a stable, user-safe failure payload."""
    from app.ingestion import classify_ingestion_failure

    error = ParseError("PDF contained no extractable text")

    assert classify_ingestion_failure(error) == (
        "EMPTY_CONTENT",
        "No extractable text was found in this source.",
    )


def test_classify_ingestion_failure_does_not_expose_url_or_library_detail():
    """Raw parser details must not be persisted for the browser to render."""
    from app.ingestion import classify_ingestion_failure

    error = ParseError(
        "Failed to scrape URL: 403 forbidden for https://private.example/report"
    )

    assert classify_ingestion_failure(error) == (
        "URL_FETCH_FAILED",
        "The URL could not be fetched. Check that it is public and try again.",
    )


def test_ingestion_persists_safe_failure_details(monkeypatch, db, notebook):
    """A background parser failure must be visible through the source record."""
    from app import ingestion
    from app.models import Source, SourceStatus, SourceType

    source = Source(
        notebook_id=notebook.id,
        type=SourceType.pdf,
        original_name_or_url="scanned.pdf",
        status=SourceStatus.pending,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    def raise_parse_error(_: bytes) -> str:
        raise ParseError("PDF contained no extractable text")

    monkeypatch.setattr(ingestion, "parse_pdf", raise_parse_error)

    ingestion._ingest_source(
        source.id,
        notebook.id,
        SourceType.pdf,
        file_bytes=b"not a readable PDF",
    )

    db.expire_all()
    failed = db.get(Source, source.id)
    assert failed is not None
    assert failed.status == SourceStatus.failed
    assert failed.error_code == "EMPTY_CONTENT"
    assert failed.error_message == "No extractable text was found in this source."
