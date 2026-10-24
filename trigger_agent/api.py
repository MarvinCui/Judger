from __future__ import annotations

import json
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable
from functools import lru_cache

from .config import Config, redact


class APIError(RuntimeError):
    pass


class Interrupted(RuntimeError):
    pass


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if sys.platform == "darwin":
        # Homebrew Python can have an older CA bundle than macOS. Add the system
        # certificate stores rather than disabling certificate/hostname checks.
        for store in ("/System/Library/Keychains/SystemRootCertificates.keychain",
                      "/Library/Keychains/System.keychain"):
            try:
                result = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p", store],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and "BEGIN CERTIFICATE" in result.stdout:
                    context.load_verify_locations(cadata=result.stdout)
            except (OSError, subprocess.TimeoutExpired, ssl.SSLError):
                pass
    return context


class DeepSeek:
    def __init__(self, config: Config):
        self.config = config
        self.origin = config.base_url.rstrip("/")

    def _open(self, path: str, payload: dict | None = None):
        headers = {"Authorization": "Bearer " + self.config.api_key,
                   "Content-Type": "application/json", "User-Agent": "TriggerAgent/0.1"}
        request = urllib.request.Request(self.origin + path, headers=headers,
                                         data=json.dumps(payload, ensure_ascii=False).encode("utf-8")
                                         if payload is not None else None)
        try:
            return urllib.request.urlopen(request, timeout=45, context=tls_context())
        except urllib.error.HTTPError as error:
            detail = redact(error.read(2400).decode("utf-8", errors="replace"), self.config.api_key)
            messages = {401: 'API key rejected. Run --setup to update it.',
                        402: 'Your DeepSeek account has insufficient credit.', 429: 'DeepSeek is rate-limiting requests. Please try again shortly.'}
            raise APIError(f"HTTP {error.code}: {messages.get(error.code, detail)}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise APIError(redact(f'Could not connect to DeepSeek: {error}', self.config.api_key)) from None

    def json_request(self, path: str, payload: dict | None = None) -> dict:
        try:
            with self._open(path, payload) as response:
                return json.load(response)
        except APIError:
            raise
        except (ValueError, OSError, TimeoutError) as error:
            raise APIError(redact(f'Could not read the API response: {error}', self.config.api_key)) from None

    def stream(self, messages: list[dict], tools: list[dict] | None = None,
               on_text: Callable[[str], None] | None = None,
               cancelled: Callable[[], bool] | None = None,
               json_mode: bool = False, max_tokens: int | None = None) -> tuple[dict, dict]:
        payload = {"model": self.config.model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True},
                   "thinking": {"type": "disabled"},
                   "max_tokens": max_tokens or self.config.max_tokens}
        if tools:
            payload["tools"] = tools
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        content: list[str] = []
        calls: dict[int, dict] = {}
        usage: dict = {}
        finished = False
        finish_reason = None
        started = time.monotonic()
        try:
            if cancelled and cancelled():
                raise Interrupted()
            with self._open("/chat/completions", payload) as response:
                for raw in response:
                    if cancelled and cancelled():
                        raise Interrupted()
                    if time.monotonic() - started > 180:
                        raise APIError('The API response exceeded three minutes. Please try again.')
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    event = json.loads(data)
                    if "error" in event:
                        raise APIError(redact(str(event["error"]), self.config.api_key))
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices", []):
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            content.append(text)
                            if on_text:
                                on_text(text)
                        for part in delta.get("tool_calls") or []:
                            call = calls.setdefault(part["index"],
                                                    {"id": "", "type": "function",
                                                     "function": {"name": "", "arguments": ""}})
                            if part.get("id"):
                                call["id"] = part["id"]
                            function = part.get("function") or {}
                            call["function"]["name"] += function.get("name") or ""
                            call["function"]["arguments"] += function.get("arguments") or ""
            if cancelled and cancelled():
                raise Interrupted()
            if not finished or finish_reason not in {"stop", "tool_calls"}:
                raise APIError(f"The response ended early ({finish_reason or 'Connection interrupted'}); its tool calls were not executed.")
            message: dict = {"role": "assistant", "content": "".join(content) or None}
            if calls:
                message["tool_calls"] = [calls[index] for index in sorted(calls)]
                for call in message["tool_calls"]:
                    if not call["id"] or not call["function"]["name"]:
                        raise APIError('The API returned an incomplete tool call. Nothing was executed.')
            return message, usage
        except (APIError, Interrupted):
            raise
        except (ValueError, KeyError, TypeError, OSError, TimeoutError) as error:
            raise APIError(redact(f'Response interrupted; tools were not executed: {error}', self.config.api_key)) from None
