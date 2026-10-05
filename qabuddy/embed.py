"""Dense embeddings over HTTP (OpenRouter by default).

OpenRouter serves embeddings through an OpenAI-shaped endpoint
(`POST {base}/embeddings` -> `data[i].embedding`), so the same client also
works for OpenAI, Gemini's OpenAI-compatible root, or any other gateway.

* Matryoshka truncation: Qwen3-Embedding-4B is 2560-d natively; we keep the
  first EMBED_DIM (1024) values and re-normalise, which retains almost all the
  retrieval quality at 40% of the storage. Truncating client-side keeps the
  code independent of whether the provider honours a `dimensions` parameter.
* Qwen3-Embedding is instruction-aware: queries get a task instruction,
  documents do not. Skipping the instruction costs a few points of recall.
* `embed_provider=ollama` keeps the original local `/api/embed` path for anyone
  who wants a fully offline index.
"""

from __future__ import annotations

import math
import time

import httpx

from .config import settings

QUERY_INSTRUCTION = (
    "Given a QA engineer's question, retrieve the test cases, source code, bug tickets, "
    "requirement sections, meeting notes, diagrams or CI logs that answer it"
)


class EmbedError(RuntimeError):
    pass


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _headers() -> dict[str, str]:
    s = settings()
    if s.embed_provider == "ollama":
        return {}
    h = {"Authorization": f"Bearer {s.embed_key}"}
    if s.embed_provider == "openrouter":
        h["HTTP-Referer"] = "https://qabuddy.local"
        h["X-Title"] = "QA Copilot"
    return h


def _post_ollama(inputs: list[str]) -> list[list[float]]:
    s = settings()
    body = {"model": s.embed_model, "input": inputs, "dimensions": s.embed_dim, "truncate": True, "keep_alive": "30m"}
    try:
        r = httpx.post(f"{s.ollama_url}/api/embed", json=body, timeout=300)
    except httpx.ConnectError as e:
        raise EmbedError(f"Ollama is not reachable at {s.ollama_url}. Start it with `ollama serve`.") from e
    if r.status_code == 404 or "not found" in r.text[:200].lower():
        raise EmbedError(f"Embedding model '{s.embed_model}' is not pulled. Run: ollama pull {s.embed_model}")
    r.raise_for_status()
    return r.json()["embeddings"]


def _post_openai(inputs: list[str]) -> list[list[float]]:
    s = settings()
    if not s.embed_key:
        raise EmbedError(f"No embedding API key. Set EMBED_API_KEY (or OPENROUTER_API_KEY) for EMBED_PROVIDER={s.embed_provider}.")
    body = {"model": s.embed_model, "input": inputs}
    r = httpx.post(f"{s.embed_base_url.rstrip('/')}/embeddings", headers=_headers(), json=body, timeout=120)
    if r.status_code in (401, 403):
        raise EmbedError(f"Embedding provider rejected the key (HTTP {r.status_code}). Check EMBED_API_KEY/OPENROUTER_API_KEY.")
    if r.status_code == 402:
        raise EmbedError("Embedding provider reports insufficient credits (HTTP 402).")
    if r.status_code == 404:
        raise EmbedError(f"Embedding model '{s.embed_model}' not found at {s.embed_base_url} (HTTP 404).")
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def _post(inputs: list[str]) -> list[list[float]]:
    s = settings()
    last: Exception | None = None
    for attempt in range(4):
        try:
            vecs = _post_ollama(inputs) if s.embed_provider == "ollama" else _post_openai(inputs)
            return [_normalize(v[: s.embed_dim]) for v in vecs]
        except EmbedError:
            raise
        except Exception as e:  # transient: rate limit, timeout, provider hiccup
            last = e
            time.sleep(min(20.0, 1.5 * (attempt + 1)))
    raise EmbedError(f"Embedding failed after retries: {last}")


def embed_documents(texts: list[str], batch_size: int = 32, max_batch_chars: int = 60_000, progress=None) -> list[list[float]]:
    out: list[list[float]] = []
    batch: list[str] = []
    chars = 0
    for t in texts:
        if batch and (len(batch) >= batch_size or chars + len(t) > max_batch_chars):
            out.extend(_post(batch))
            if progress:
                progress(len(out))
            batch, chars = [], 0
        batch.append(t)
        chars += len(t)
    if batch:
        out.extend(_post(batch))
        if progress:
            progress(len(out))
    return out


def embed_query(question: str) -> list[float]:
    return _post([f"Instruct: {QUERY_INSTRUCTION}\nQuery: {question}"])[0]
