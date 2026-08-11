"""Retrieval/prompt/citation-parsing unit tests — no DB, no network."""

from __future__ import annotations

from types import SimpleNamespace

from app import rag


def chunk(content: str):
    """A stand-in for a Chunk row — build_prompt only reads `.content`."""
    return SimpleNamespace(content=content)


# --------------------------------------------------------------------------- #
# parse_cited_markers
# --------------------------------------------------------------------------- #
def test_parses_valid_markers_in_order():
    answer = "First point [S1]. Second point [S2]."
    assert rag.parse_cited_markers(answer, {1, 2}) == [1, 2]


def test_ignores_bare_digit_brackets():
    """Retrieved chunks (Wikipedia especially) carry their own `[1]`-style
    footnotes; only the `[S<n>]` form counts as a citation."""
    answer = "Some claim.[1] Our actual citation [S1]."
    assert rag.parse_cited_markers(answer, {1}) == [1]


def test_ignores_out_of_range_markers():
    answer = "Cites [S1] and [S9], but only 1 chunk was retrieved."
    assert rag.parse_cited_markers(answer, {1}) == [1]


def test_dedupes_and_preserves_first_appearance_order():
    answer = "[S2] then [S1] then [S2] again"
    assert rag.parse_cited_markers(answer, {1, 2}) == [2, 1]


def test_no_markers_returns_empty():
    assert rag.parse_cited_markers("No citations here.", {1, 2}) == []


# --------------------------------------------------------------------------- #
# build_prompt
# --------------------------------------------------------------------------- #
def test_build_prompt_labels_sources_from_one():
    prompt = rag.build_prompt("What is X?", [chunk("first"), chunk("second")])
    assert "[S1] first" in prompt
    assert "[S2] second" in prompt
    assert "Question: What is X?" in prompt


def test_build_prompt_instructs_the_bracket_s_form():
    prompt = rag.build_prompt("q", [chunk("c")])
    assert "[S1]" in prompt
    assert "never a bare number" in prompt


def test_build_prompt_omits_history_block_when_absent():
    prompt = rag.build_prompt("q", [chunk("c")])
    assert "Conversation so far" not in prompt


def test_build_prompt_includes_history_when_present():
    history = [("user", "earlier question"), ("assistant", "earlier answer")]
    prompt = rag.build_prompt("q", [chunk("c")], history)
    assert "Conversation so far:" in prompt
    assert "User: earlier question" in prompt
    assert "Assistant: earlier answer" in prompt


# --------------------------------------------------------------------------- #
# format_history
# --------------------------------------------------------------------------- #
def test_format_history_truncates_long_turns():
    long_content = "x" * 1000
    out = rag.format_history([("user", long_content)])
    assert out.startswith("User: ")
    assert len(out) < 500
    assert out.endswith("…")


def test_format_history_collapses_whitespace():
    out = rag.format_history([("assistant", "line one\n\nline   two")])
    assert out == "Assistant: line one line two"


# --------------------------------------------------------------------------- #
# make_snippet
# --------------------------------------------------------------------------- #
def test_make_snippet_leaves_short_text_untouched():
    assert rag.make_snippet("short text") == "short text"


def test_make_snippet_truncates_long_text_with_ellipsis():
    text = "word " * 100
    snippet = rag.make_snippet(text, max_len=50)
    assert len(snippet) <= 51  # 50 + ellipsis char
    assert snippet.endswith("…")


def test_make_snippet_collapses_whitespace():
    assert rag.make_snippet("a\n\nb   c") == "a b c"
