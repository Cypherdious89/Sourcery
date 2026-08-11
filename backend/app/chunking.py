"""Text chunking.

SPEC calls for ~500-token chunks with ~50-token overlap. We use the allowed
word-count approximation: words are counted as tokens, and each chunk records
the character span ``[start, end)`` it occupies in the original text so it can
be stored in ``chunks.char_span`` (int4range) for later citation highlighting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings

_settings = get_settings()

# Matches runs of non-whitespace as "words" (our token approximation), keeping
# their start/end character offsets in the source text.
_WORD_RE = re.compile(r"\S+")


@dataclass
class Chunk:
    content: str
    chunk_index: int
    start_char: int
    end_char: int  # exclusive


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """Split ``text`` into overlapping word-windows.

    Returns chunks in order. ``start_char``/``end_char`` index into ``text``.
    """
    chunk_size = chunk_size or _settings.chunk_size_tokens
    overlap = overlap if overlap is not None else _settings.chunk_overlap_tokens
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    words = list(_WORD_RE.finditer(text))
    if not words:
        return []

    step = chunk_size - overlap
    chunks: list[Chunk] = []
    index = 0
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size]
        if not window:
            break
        start_char = window[0].start()
        end_char = window[-1].end()
        content = text[start_char:end_char]
        chunks.append(
            Chunk(
                content=content,
                chunk_index=index,
                start_char=start_char,
                end_char=end_char,
            )
        )
        index += 1
        # Reached the end of the text; the last window already covered the tail.
        if start + chunk_size >= len(words):
            break
    return chunks
