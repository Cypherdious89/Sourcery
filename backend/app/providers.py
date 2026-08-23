"""LLM provider adapters used by the gateway.

Each provider exposes ``generate(prompt) -> ProviderResponse`` and its
``name``/``model``. Providers translate SDK-specific failures into two
gateway-facing errors:

* ``RetryableProviderError`` — timeout / 5xx / rate-limit: the gateway should
  fail over to the fallback provider.
* ``FatalProviderError`` — anything else (bad request, auth, safety block):
  not worth retrying with the same prompt.

A ``StubProvider`` is used when no API keys are configured so the full RAG +
gateway pipeline is exercisable offline. Setting ``GEMINI_API_KEY`` (and/or
``GROQ_API_KEY``) switches to the real providers with no other change.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from app.config import get_settings


logger = logging.getLogger("app.providers")


@dataclass
class ProviderResponse:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass
class StreamDelta:
    """One step of a streamed generation.

    Providers yield ``StreamDelta(text=...)`` as tokens arrive, then exactly
    one ``StreamDelta(final=True, ...)`` carrying usage once the stream ends.
    Usage is only known after the provider has finished, which is why it rides
    on a terminal delta rather than being returned.
    """

    text: str = ""
    final: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class RetryableProviderError(Exception):
    """Transient failure (timeout/5xx/rate-limit) — fail over to fallback."""


class FatalProviderError(Exception):
    """Non-retryable failure — do not retry with the same prompt."""


class Provider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str) -> ProviderResponse: ...

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]: ...


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GeminiProvider:
    """Gemini adapter on the `google-genai` SDK.

    Thinking is disabled by default (``GEMINI_THINKING_BUDGET=0``). For grounded
    RAG the model is synthesising from supplied chunks rather than reasoning
    from scratch, and thinking costs both latency — ~4.8s to first token versus
    ~0.9s, which is the difference between a streaming UI that feels live and
    one that stalls — and money, since thinking tokens bill as output.
    """

    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self.name = "gemini"
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._thinking_budget = get_settings().gemini_thinking_budget

    def _client(self):
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=self._api_key,
            # google-genai takes the timeout in milliseconds.
            http_options=types.HttpOptions(timeout=int(self._timeout * 1000)),
        )

    def _config(self, *, with_thinking: bool):
        from google.genai import types

        if not with_thinking or self._thinking_budget is None:
            return None
        return types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_budget=self._thinking_budget
            )
        )

    @staticmethod
    def _classify(exc: Exception) -> Exception:
        from google.genai import errors

        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", None)
            if code in _RETRYABLE_STATUS:
                return RetryableProviderError(f"gemini transient error: {exc}")
            return FatalProviderError(f"gemini error: {exc}")
        return FatalProviderError(f"gemini error: {exc}")

    def _rejects_thinking(self, exc: Exception) -> bool:
        """Whether this looks like a model rejecting the thinking_config knob.

        Confirmed against gemini-3.6-flash: the API reports this as a plain
        400 INVALID_ARGUMENT with the generic message "Request contains an
        invalid argument" — no mention of "thinking" at all, so matching on
        message text (the previous approach) silently misses it and lets a
        real rejection get misclassified as fatal, aborting the whole
        fallback chain. Any 400 on a call that included thinking_config is
        treated as a candidate: retrying once without it is cheap, and if the
        retry also 400s, that failure still propagates and gets classified
        normally — so this can't mask a genuinely bad request, only recover
        from ones caused by the thinking knob itself.
        """
        from google.genai import errors

        return isinstance(exc, errors.APIError) and getattr(exc, "code", None) == 400

    def generate(self, prompt: str) -> ProviderResponse:
        client = self._client()

        def call(with_thinking: bool):
            return client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self._config(with_thinking=with_thinking),
            )

        try:
            try:
                resp = call(True)
            except Exception as exc:  # noqa: BLE001
                if not self._rejects_thinking(exc):
                    raise
                # Model doesn't support this thinking knob — retry as-is.
                logger.info("%s rejected thinking config; retrying without", self.model)
                resp = call(False)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

        usage = getattr(resp, "usage_metadata", None)
        return ProviderResponse(
            text=resp.text or "",
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            completion_tokens=getattr(usage, "candidates_token_count", None),
        )

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        client = self._client()

        def open_stream(with_thinking: bool):
            return client.models.generate_content_stream(
                model=self.model,
                contents=prompt,
                config=self._config(with_thinking=with_thinking),
            )

        usage = None
        try:
            try:
                stream = open_stream(True)
                first = next(stream, None)
            except Exception as exc:  # noqa: BLE001
                if not self._rejects_thinking(exc):
                    raise
                logger.info("%s rejected thinking config; retrying without", self.model)
                stream = open_stream(False)
                first = next(stream, None)

            for chunk in _prepend(first, stream):
                if chunk is None:
                    continue
                if getattr(chunk, "usage_metadata", None):
                    usage = chunk.usage_metadata
                if chunk.text:
                    yield StreamDelta(text=chunk.text)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

        yield StreamDelta(
            final=True,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            completion_tokens=getattr(usage, "candidates_token_count", None),
        )


def _prepend(first, rest) -> Iterator:
    """Re-attach the peeked first chunk to the front of a stream."""
    if first is not None:
        yield first
    yield from rest


# --------------------------------------------------------------------------- #
# Groq — OpenAI-API-compatible, genuinely free tier
# --------------------------------------------------------------------------- #
# Fills this project's original OpenAI fallback slot (SPEC's LLM providers
# line names "Gemini 2.5 Pro or OpenAI") — a deliberate deviation, requested
# directly rather than introduced unprompted. Groq's free tier is real (no
# card required, resets daily) — this replaced an earlier Kimi/Moonshot AI
# attempt at the same slot, which turned out to require a paid recharge
# despite being cheap. The tradeoff: free-tier rate limits are genuinely
# tight (30 RPM / 1K RPD / 12K TPM for the default model below) — fine for a
# rarely-invoked fallback on a low-traffic app, not something to build
# production load-bearing behavior on. Its API is byte-compatible with
# OpenAI's Chat Completions API — same request/response shape, same Python
# SDK — confirmed against https://console.groq.com/docs/openai, so this
# reuses the `openai` package pointed at Groq's base_url rather than writing
# a second HTTP client from scratch.
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider:
    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self.name = "groq"
        self.model = model
        self._api_key = api_key
        self._timeout = timeout

    def _client(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self._api_key, base_url=_GROQ_BASE_URL, timeout=self._timeout
        )

    def generate(self, prompt: str) -> ProviderResponse:
        import openai

        client = self._client()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        except (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            raise RetryableProviderError(f"groq transient error: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code and exc.status_code >= 500:
                raise RetryableProviderError(
                    f"groq {exc.status_code} error: {exc}"
                ) from exc
            raise FatalProviderError(f"groq {exc.status_code} error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise FatalProviderError(f"groq error: {exc}") from exc

        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            text=text,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        import openai

        client = self._client()
        prompt_tokens = completion_tokens = None
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                # Without this, streamed responses carry no usage data at all.
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                if chunk.usage is not None:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                # The usage-only frame has an empty choices list.
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield StreamDelta(text=delta)
        except (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            raise RetryableProviderError(f"groq transient error: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code and exc.status_code >= 500:
                raise RetryableProviderError(
                    f"groq {exc.status_code} error: {exc}"
                ) from exc
            raise FatalProviderError(f"groq {exc.status_code} error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise FatalProviderError(f"groq stream error: {exc}") from exc

        yield StreamDelta(
            final=True,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


# --------------------------------------------------------------------------- #
# Offline stub (no API keys configured)
# --------------------------------------------------------------------------- #
_SOURCE_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s*(.+?)\s*$")


class StubProvider:
    """Deterministic, dependency-free provider for offline dev/testing.

    Produces a grounded answer by quoting the top retrieved sources and citing
    their markers, so the citation-parsing path has real markers to map back.
    """

    def __init__(self) -> None:
        self.name = "stub"
        self.model = "offline-deterministic"

    def generate(self, prompt: str) -> ProviderResponse:
        # Recover the labelled sources from the prompt to quote/cite them.
        sources: dict[int, str] = {}
        for line in prompt.splitlines():
            m = _SOURCE_LINE_RE.match(line)
            if m:
                sources[int(m.group(1))] = m.group(2)

        markers = sorted(sources)[:2]  # cite up to the top two sources
        if markers:
            parts = [
                "Based on the notebook's sources, here is what they say: "
            ]
            for n in markers:
                snippet = sources[n][:200].rstrip()
                parts.append(f"{snippet} [{n}]")
            text = " ".join(parts)
        else:
            text = "I could not find any relevant sources to answer that."

        return ProviderResponse(
            text=text,
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(text.split()),
        )

    def generate_stream(self, prompt: str) -> Iterator[StreamDelta]:
        resp = self.generate(prompt)
        # Emit word-by-word so the streaming path is exercisable offline.
        for i, word in enumerate(resp.text.split(" ")):
            yield StreamDelta(text=word if i == 0 else f" {word}")
        yield StreamDelta(
            final=True,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
        )
