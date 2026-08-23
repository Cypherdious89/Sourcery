"""Source parsing: PDF (pypdf), DOCX (python-docx), URL (trafilatura).

Each parser returns cleaned plain text. Failures raise ``ParseError`` so the
ingestion pipeline can mark the source ``failed``.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass


class ParseError(Exception):
    """Raised when a source cannot be parsed into usable text."""


@dataclass
class ParsedURL:
    text: str
    # The page's <title> (or trafilatura's best-effort equivalent) — used to
    # auto-name a notebook from its first source. None if trafilatura
    # couldn't find one; callers fall back to the URL itself.
    title: str | None


def _normalize_whitespace(text: str) -> str:
    # Collapse runs of blank lines / trailing spaces while keeping paragraphs.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - surface any pypdf failure uniformly
        raise ParseError(f"Failed to parse PDF: {exc}") from exc
    text = _normalize_whitespace("\n\n".join(pages))
    if not text:
        raise ParseError("PDF contained no extractable text")
    return text


def parse_docx(data: bytes) -> str:
    from docx import Document

    try:
        document = Document(io.BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to parse DOCX: {exc}") from exc
    text = _normalize_whitespace("\n".join(paragraphs))
    if not text:
        raise ParseError("DOCX contained no extractable text")
    return text


def parse_url(url: str) -> ParsedURL:
    import trafilatura

    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise ParseError(f"Could not fetch URL: {url}")
        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        # Best-effort — a notebook still gets auto-named from the URL itself
        # if metadata extraction fails or the page has no title.
        title = None
        try:
            metadata = trafilatura.extract_metadata(downloaded)
            title = metadata.title if metadata else None
        except Exception:  # noqa: BLE001
            pass
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to scrape URL: {exc}") from exc
    text = _normalize_whitespace(extracted or "")
    if not text:
        raise ParseError(f"No main content extracted from URL: {url}")
    return ParsedURL(text=text, title=title)
