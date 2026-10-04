"""Answer LLM: any OpenAI-compatible chat endpoint.

Default is OpenRouter with a comma-separated list of free models
(`LLM_MODEL=a:free,b:free,c:free`): free pools are shared and rate-limit or
retire without notice, so the next model is tried when one 404s or exhausts its
429 retries. Groq, OpenAI and Ollama work too. Only the retrieved sources are
ever sent.
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterator

import httpx

from .config import settings


def _endpoint() -> tuple[str, dict[str, str]]:
    s = settings()
    if s.llm_provider == "groq":
        return "https://api.groq.com/openai/v1", {"Authorization": f"Bearer {s.groq_api_key}"}
    if s.llm_provider == "openrouter":
        return "https://openrouter.ai/api/v1", {
            "Authorization": f"Bearer {s.openrouter_api_key}",
            "HTTP-Referer": "https://qabuddy.local",
            "X-Title": "QABuddy",
        }
    if s.llm_provider == "ollama":
        return f"{s.ollama_url}/v1", {}
    return s.openai_base_url.rstrip("/"), {"Authorization": f"Bearer {s.openai_api_key}"}


def models() -> list[str]:
    return [m.strip() for m in settings().llm_model.split(",") if m.strip()]


def configured() -> bool:
    s = settings()
    if s.llm_provider == "groq":
        return bool(s.groq_api_key)
    if s.llm_provider == "openrouter":
        return bool(s.openrouter_api_key)
    if s.llm_provider == "openai":
        return bool(s.openai_api_key)
    return True


def _body(model: str, messages: list[dict], max_tokens: int, stream: bool) -> dict:
    s = settings()
    body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens, "stream": stream}
    if "gpt-oss" in model and s.llm_provider in {"groq", "openai"}:
        body["reasoning_effort"] = "low"  # gpt-oss reasons before answering; low keeps tokens and latency down
    # OpenRouter's unified `reasoning` control: without it a free reasoning model can
    # consume the entire max_tokens on hidden reasoning and stream no answer at all.
    # Models that do not reason ignore it.
    if s.llm_provider == "openrouter" and s.llm_reasoning_max_tokens > 0:
        body["reasoning"] = {"max_tokens": s.llm_reasoning_max_tokens}
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


MAX_RETRIES = 4
MAX_WAIT_S = 30.0


def _retry_after(r: httpx.Response) -> float:
    """Seconds to wait after a 429. Groq says "Please try again in 14.835s"."""
    m = re.search(r"try again in ([\d.]+)\s*(ms|s)", r.text)
    if m:
        secs = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
    else:
        secs = float(r.headers.get("retry-after", 5) or 5)
    return min(MAX_WAIT_S, secs + 0.5)


def complete(messages: list[dict], max_tokens: int = 300) -> tuple[str, dict]:
    base, headers = _endpoint()
    errors: list[str] = []
    for model in models():
        for attempt in range(MAX_RETRIES + 1):
            r = httpx.post(f"{base}/chat/completions", headers=headers, json=_body(model, messages, max_tokens, False), timeout=60)
            if r.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(_retry_after(r))
                continue
            if r.status_code >= 400:  # unavailable/retired: try the next model
                errors.append(f"{model} {r.status_code}")
                break
            d = r.json()
            content = (d["choices"][0]["message"].get("content") or "").strip()
            if not content:  # a reasoning model may spend the whole budget on hidden reasoning
                errors.append(f"{model} empty output")
                break
            return content, d.get("usage", {})
    raise RuntimeError(f"All LLM models failed: {', '.join(errors) or 'none configured'}")


def stream(messages: list[dict], max_tokens: int = 1200) -> Iterator[tuple[str | None, dict | None]]:
    """Yields (text_delta, usage); usage is set only on the final event.

    On a rate limit (429) it yields (None, {"rate_limited_s": n}), waits, and
    retries the same model; on a 404/5xx it falls through to the next model in
    the list.
    """
    base, headers = _endpoint()
    errors: list[str] = []
    for model in models():
        for attempt in range(MAX_RETRIES + 1):
            with httpx.stream(
                "POST", f"{base}/chat/completions", headers=headers, json=_body(model, messages, max_tokens, True), timeout=120
            ) as r:
                if r.status_code == 429 and attempt < MAX_RETRIES:
                    r.read()
                    wait = _retry_after(r)
                    yield None, {"rate_limited_s": round(wait, 1)}
                    time.sleep(wait)
                    continue
                if r.status_code >= 400:
                    r.read()
                    errors.append(f"{model} {r.status_code}")
                    break  # next model
                got = False
                for delta, u in _consume(r):
                    if delta:
                        got = True
                    yield delta, u
                if got:
                    return
                errors.append(f"{model} empty output")  # reasoning-only: try the next model
                break
    raise RuntimeError(f"All LLM models failed: {', '.join(errors) or 'none configured'}")


def _consume(r: httpx.Response) -> Iterator[tuple[str, dict | None]]:
    usage: dict | None = None
    for line in r.iter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            evt = json.loads(data)
        except json.JSONDecodeError:
            continue
        u = evt.get("usage") or (evt.get("x_groq") or {}).get("usage")
        if u:
            usage = u
        for ch in evt.get("choices") or []:
            delta = (ch.get("delta") or {}).get("content")
            if delta:
                yield delta, None
    yield "", usage or {}
