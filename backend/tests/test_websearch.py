"""Tavily adapter: payload parsing and error mapping — no network calls."""

from __future__ import annotations

import httpx

from app import websearch


# --------------------------------------------------------------------------- #
# parse_results
# --------------------------------------------------------------------------- #
def test_parses_title_url_content():
    payload = {
        "results": [
            {"title": "A", "url": "https://a.example/", "content": "snippet a"},
            {"title": "B", "url": "https://b.example/", "content": "snippet b"},
        ]
    }
    results = websearch.parse_results(payload)
    assert [r.url for r in results] == ["https://a.example/", "https://b.example/"]
    assert results[0].title == "A"
    assert results[0].snippet == "snippet a"


def test_skips_entries_missing_a_url():
    payload = {"results": [{"title": "No URL", "content": "should be skipped"}]}
    assert websearch.parse_results(payload) == []


def test_title_falls_back_to_url_when_missing():
    payload = {"results": [{"url": "https://example.com/", "content": ""}]}
    results = websearch.parse_results(payload)
    assert results[0].title == "https://example.com/"


def test_strips_markup_and_decodes_entities():
    payload = {
        "results": [
            {
                "title": "T",
                "url": "https://x.example/",
                "content": "stores &amp; retrieves <b>fast</b>",
            }
        ]
    }
    snippet = websearch.parse_results(payload)[0].snippet
    assert "&amp;" not in snippet
    assert "<b>" not in snippet
    assert snippet == "stores & retrieves fast"


def test_empty_payload_returns_no_results():
    assert websearch.parse_results({}) == []


# --------------------------------------------------------------------------- #
# _error_for — Tavily's status codes, mapped to actionable messages
# --------------------------------------------------------------------------- #
def test_error_for_bad_key_returns_actionable_message():
    resp = httpx.Response(401, json={"detail": {"error": "Invalid API key"}})
    err = websearch._error_for(resp)
    assert "TAVILY_API_KEY" in str(err)


def test_error_for_rate_limit():
    resp = httpx.Response(429, json={})
    err = websearch._error_for(resp)
    assert "rate limit" in str(err).lower()


def test_error_for_credits_exhausted():
    resp = httpx.Response(432, json={"detail": {"error": "plan limit reached"}})
    err = websearch._error_for(resp)
    assert "credits exhausted" in str(err).lower()


def test_error_for_generic_detail_passthrough():
    resp = httpx.Response(400, json={"detail": {"error": "bad query syntax"}})
    err = websearch._error_for(resp)
    assert "bad query syntax" in str(err)


def test_error_for_no_body_falls_back_to_status_code():
    resp = httpx.Response(500, content=b"not json")
    err = websearch._error_for(resp)
    assert "500" in str(err)


# --------------------------------------------------------------------------- #
# is_configured
# --------------------------------------------------------------------------- #
def test_is_configured_reflects_settings(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tavily_api_key", None)
    assert websearch.is_configured() is False
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test-key")
    assert websearch.is_configured() is True
