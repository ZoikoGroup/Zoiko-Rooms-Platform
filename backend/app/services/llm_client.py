"""LLM client with automatic Groq -> Gemini failover.

Groq stays the primary provider. When it is rate limited, unreachable or
answering 5xx, the same request is replayed against Gemini through Google's
OpenAI-compatible endpoint. ``FailoverClient`` exposes the same
``client.chat.completions.create(**kwargs)`` surface as the Groq SDK, and
``GeminiClient`` raises Groq SDK exception types, so callers' existing
``except RateLimitError / APIConnectionError / APIStatusError`` handlers keep
working whichever provider failed last.
"""

from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any, Iterator

import httpx
from groq import APIConnectionError, APIStatusError, RateLimitError

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class _Obj(SimpleNamespace):
    """Missing fields read as None, like the Groq SDK's response models."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return None


def _ns(value: Any) -> Any:
    """dict/list tree -> attribute-access tree (matches the Groq SDK objects)."""
    if isinstance(value, dict):
        return _Obj(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.text[:300]
    except Exception:  # noqa: BLE001 - streamed body not read
        detail = ""
    message = f"Gemini error {response.status_code}: {detail}"
    if response.status_code == 429:
        raise RateLimitError(message, response=response, body=None)
    raise APIStatusError(message, response=response, body=None)


class _GeminiCompletions:
    def __init__(self, api_key: str, timeout: float):
        self._api_key = api_key
        self._timeout = timeout
        # tool_call id -> extra_content (Gemini "thought signature"), which the
        # API requires to be echoed back on the follow-up tool-result request.
        self._extra: dict[str, Any] = {}

    def _prepare(self, messages: list[dict]) -> list[dict]:
        prepared = []
        for msg in messages:
            calls = msg.get("tool_calls")
            if calls:
                msg = {
                    **msg,
                    "tool_calls": [
                        {**c, "extra_content": self._extra[c["id"]]} if c.get("id") in self._extra else c
                        for c in calls
                    ],
                }
            prepared.append(msg)
        return prepared

    def create(self, **kwargs):
        stream = bool(kwargs.get("stream"))
        body = {k: v for k, v in kwargs.items() if v is not None}
        body["messages"] = self._prepare(body.get("messages", []))
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        url = f"{GEMINI_BASE_URL}/chat/completions"
        try:
            if not stream:
                response = httpx.post(url, json=body, headers=headers, timeout=self._timeout)
                _raise_for_status(response)
                data = response.json()
                for choice in data.get("choices", []):
                    for call in (choice.get("message") or {}).get("tool_calls") or []:
                        call.setdefault("id", f"call_{uuid.uuid4().hex[:12]}")
                        if call.get("extra_content"):
                            self._extra[call["id"]] = call["extra_content"]
                        if choice.get("finish_reason") == "stop":
                            choice["finish_reason"] = "tool_calls"
                return _ns(data)

            client = httpx.Client(timeout=self._timeout)
            response = client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
            if response.status_code >= 400:
                response.read()
                client.close()
                _raise_for_status(response)
            return self._iter_stream(client, response)
        except httpx.HTTPError as exc:
            raise APIConnectionError(request=httpx.Request("POST", url)) from exc

    def _iter_stream(self, client: httpx.Client, response: httpx.Response) -> Iterator[Any]:
        saw_tool_call = False
        next_index = 0
        try:
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                data = json.loads(payload)
                for choice in data.get("choices", []):
                    delta = choice.get("delta") or {}
                    for call in delta.get("tool_calls") or []:
                        # Gemini sends each call whole and (often) in its own
                        # chunk with no per-call index; a running counter keeps
                        # parallel calls from being merged into one.
                        call["index"] = next_index
                        next_index += 1
                        # Gemini sends whole calls (not fragments); give each a
                        # stable id so the result can be matched back.
                        call.setdefault("id", f"call_{uuid.uuid4().hex[:12]}")
                        saw_tool_call = True
                        if call.get("extra_content"):
                            self._extra[call["id"]] = call["extra_content"]
                    # Gemini ends tool-call streams with "stop"; the OpenAI/Groq
                    # contract (which the chat loop relies on) is "tool_calls".
                    if saw_tool_call and choice.get("finish_reason") == "stop":
                        choice["finish_reason"] = "tool_calls"
                yield _ns(data)
        finally:
            response.close()
            client.close()


class GeminiClient:
    def __init__(self, api_key: str, timeout: float = 45.0):
        self.chat = SimpleNamespace(completions=_GeminiCompletions(api_key, timeout))


class _FailoverCompletions:
    def __init__(self, primary: Any | None, fallback: GeminiClient | None, fallback_model: str):
        self._primary = primary
        self._fallback = fallback
        self._fallback_model = fallback_model

    def create(self, **kwargs):
        if self._primary is not None:
            try:
                return self._primary.chat.completions.create(**kwargs)
            except (RateLimitError, APIConnectionError) as exc:
                if self._fallback is None:
                    raise
                reason = type(exc).__name__
            except APIStatusError as exc:
                # Any provider-side HTTP error (400 tool_use_failed, 413 too
                # large, 401/403 key problems, 5xx...) is worth one try on the
                # other provider rather than surfacing a generic error.
                if self._fallback is None:
                    raise
                reason = f"HTTP {exc.status_code}: {str(exc)[:300]}"
            logger.warning("groq unavailable (%s); failing over to gemini", reason)
        kwargs = {**kwargs, "model": self._fallback_model}
        return self._fallback.chat.completions.create(**kwargs)


class FailoverClient:
    """Groq first, Gemini when Groq can't serve the request."""

    def __init__(self, primary: Any | None, fallback: GeminiClient | None, fallback_model: str):
        self.chat = SimpleNamespace(completions=_FailoverCompletions(primary, fallback, fallback_model))
