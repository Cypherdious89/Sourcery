"""Provider-level unit tests — no network, no DB.

Covers classification logic that's cheap to get subtly wrong and expensive to
debug in production (see the gemini-3.6-flash incident this file guards
against).
"""

from __future__ import annotations

from app.providers import GeminiProvider


def _client_error(code: int, message: str):
    from google.genai import errors

    return errors.ClientError(
        code, {"error": {"code": code, "message": message, "status": "X"}}, None
    )


def _provider() -> GeminiProvider:
    return GeminiProvider(api_key="unused", model="gemini-3.6-flash", timeout=1.0)


def test_rejects_thinking_matches_generic_400_with_no_thinking_wording():
    """Regression test: gemini-3.6-flash rejects thinking_config with a plain
    400 whose message never mentions "thinking" — confirmed against the real
    API (production incident, 2026-08-23). The previous message-substring
    check silently missed this, misclassifying it as fatal and aborting the
    whole fallback chain instead of retrying without thinking_config."""
    exc = _client_error(400, "Request contains an invalid argument.")
    assert _provider()._rejects_thinking(exc) is True


def test_rejects_thinking_still_matches_explicit_thinking_wording():
    exc = _client_error(400, "thinking_config is not supported for this model")
    assert _provider()._rejects_thinking(exc) is True


def test_rejects_thinking_is_false_for_non_400_codes():
    """A 429/5xx isn't a thinking-config rejection — those are handled by
    _classify's retryable-status path instead."""
    exc = _client_error(429, "Resource has been exhausted")
    assert _provider()._rejects_thinking(exc) is False


def test_rejects_thinking_is_false_for_non_api_errors():
    assert _provider()._rejects_thinking(ValueError("not an APIError")) is False
