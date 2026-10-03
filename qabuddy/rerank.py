"""Cross-encoder reranking, pluggable by provider.

Hybrid search is good at recall; a cross-encoder is good at precision. It
reads the question and each candidate together, so it can tell that
"why did login fail on CI" is answered by the IP-allowlist comment, not by
the 40 test cases that merely contain the word "login".

  RERANK_PROVIDER=local  sentence-transformers in-process (BAAI/bge-reranker-v2-m3);
                         MPS on Apple Silicon, CUDA or CPU elsewhere. Local dev.
  RERANK_PROVIDER=jina   Jina's hosted reranker (free tier); works on Vercel,
                         which cannot run PyTorch.
  RERANK_PROVIDER=none   no reranking: retrieval still works, ordering is weaker.

Reranking is an enhancement throughout: any failure returns None and retrieval
proceeds on the fused ranks. If `jina` is selected without a key it falls back
to `none` so hosting never depends on the key.
"""

from __future__ import annotations

import math
import threading
import time

import httpx

from .config import settings

_model = None
_lock = threading.Lock()
_load_error: str | None = None

JINA_URL = "https://api.jina.ai/v1/rerank"


def _provider() -> str:
    s = settings()
    p = (s.rerank_provider or "none").lower()
    if p == "jina" and not s.jina_api_key:
        return "none"  # never block hosting on the key
    return p if p in {"local", "jina", "none"} else "none"


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def load():
    global _model, _load_error
    if _provider() != "local":
        return None
    with _lock:
        if _model is None and _load_error is None:
            try:
                from sentence_transformers import CrossEncoder

                t0 = time.perf_counter()
                _model = CrossEncoder(settings().rerank_local_model, max_length=512, device=_device())
                if _device() in {"mps", "cuda"}:
                    _model.model.half()  # ~1.4s -> ~1.0s for 24 candidates on an M3 Max, same ranking
                _model.predict([("warm up", "warm up")])
                print(f"[rerank] {settings().rerank_local_model} loaded on {_device()} in {time.perf_counter() - t0:.1f}s")
            except Exception as e:  # reranking is an enhancement: retrieval still works without it
                _load_error = str(e)
                print(f"[rerank] disabled: {e}")
    return _model


def status() -> dict:
    s, p = settings(), _provider()
    return {
        "provider": p,
        "enabled": p != "none",
        "model": s.rerank_local_model if p == "local" else s.rerank_model,
        "loaded": _model is not None,
        "device": _device() if _model is not None else None,
        "error": _load_error,
    }


def _score_local(question: str, texts: list[str]) -> list[float] | None:
    model = load()
    if model is None:
        return None
    raw = model.predict([(question, t) for t in texts], batch_size=16, show_progress_bar=False)
    out = []
    for x in raw:
        x = float(x)
        out.append(x if 0.0 <= x <= 1.0 else 1 / (1 + math.exp(-x)))
    return out


def _score_jina(question: str, texts: list[str]) -> list[float]:
    s = settings()
    r = httpx.post(
        JINA_URL,
        headers={"Authorization": f"Bearer {s.jina_api_key}"},
        json={"model": s.rerank_model, "query": question, "documents": texts, "top_n": len(texts)},
        timeout=60,
    )
    r.raise_for_status()
    scores = [0.0] * len(texts)
    for item in r.json().get("results", []):
        scores[int(item["index"])] = float(item.get("relevance_score", 0.0))
    return scores


def score(question: str, texts: list[str]) -> list[float] | None:
    global _load_error
    if not texts:
        return None
    p = _provider()
    if p == "none":
        return None
    if p == "jina":
        try:
            return _score_jina(question, texts)
        except Exception as e:
            _load_error = str(e)
            print(f"[rerank] jina failed, continuing without rerank: {e}")
            return None
    return _score_local(question, texts)
