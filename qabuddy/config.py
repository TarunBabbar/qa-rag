"""Settings come from .env; sources come from sources.yaml.

The stack is pluggable so the same code runs locally and on Vercel:

  embeddings  openrouter (HTTP, default) | openai (any OpenAI-compatible root) | ollama
  vector DB   Pinecone (serverless, dense + sparse in one index)
  reranker    local (sentence-transformers) | jina (hosted) | none
  answer LLM  openrouter | groq | openai | ollama
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


def _int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _env(key, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def is_vercel() -> bool:
    """True when running inside a Vercel Function (no local ingest, no local models)."""
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    kind: str
    path: Path | None = None
    provider: str = "local"  # local | jira | github
    repo: str = ""
    ref: str = "main"
    description: str = ""
    phase: int = 1

    @property
    def rel_path(self) -> str:
        if self.path is not None:
            return self.path.relative_to(ROOT).as_posix()
        return f"{self.provider}:{self.repo or self.id}"


@dataclass(frozen=True)
class Settings:
    # ---- embeddings ----
    embed_provider: str = field(default_factory=lambda: _env("EMBED_PROVIDER", "openrouter"))
    embed_base_url: str = field(default_factory=lambda: _env("EMBED_BASE_URL", "https://openrouter.ai/api/v1"))
    embed_api_key: str = field(default_factory=lambda: _env("EMBED_API_KEY", ""))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "qwen/qwen3-embedding-4b"))
    embed_dim: int = field(default_factory=lambda: _int("EMBED_DIM", 1024))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))

    # ---- Pinecone ----
    pinecone_api_key: str = field(default_factory=lambda: _env("PINECONE_API_KEY", ""))
    pinecone_index: str = field(default_factory=lambda: _env("PINECONE_INDEX", "qabuddy"))
    pinecone_cloud: str = field(default_factory=lambda: _env("PINECONE_CLOUD", "aws"))
    pinecone_region: str = field(default_factory=lambda: _env("PINECONE_REGION", "us-east-1"))
    pinecone_namespace: str = field(default_factory=lambda: _env("PINECONE_NAMESPACE", ""))
    pinecone_host: str = field(default_factory=lambda: _env("PINECONE_HOST", ""))

    # ---- reranker ----
    rerank_provider: str = field(default_factory=lambda: _env("RERANK_PROVIDER", "local"))
    rerank_model: str = field(default_factory=lambda: _env("RERANK_MODEL", "jina-reranker-v2-base-multilingual"))
    rerank_local_model: str = field(default_factory=lambda: _env("RERANK_LOCAL_MODEL", "BAAI/bge-reranker-v2-m3"))
    jina_api_key: str = field(default_factory=lambda: _env("JINA_API_KEY", ""))

    # ---- answer LLM ----
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openrouter"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "openai/gpt-oss-120b:free"))
    # Free reasoning models can spend the whole completion budget on hidden reasoning
    # and emit no answer at all. Capping it leaves room for content. 0 disables.
    llm_reasoning_max_tokens: int = field(default_factory=lambda: _int("LLM_REASONING_MAX_TOKENS", 800))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY", ""))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))

    # ---- retrieval knobs ----
    prefetch_k: int = field(default_factory=lambda: _int("PREFETCH_K", 40))
    rerank_candidates: int = field(default_factory=lambda: _int("RERANK_CANDIDATES", 24))
    final_k: int = field(default_factory=lambda: _int("FINAL_K", 6))
    context_tokens: int = field(default_factory=lambda: _int("CONTEXT_TOKENS", 3500))

    # ---- Jira ----
    jira_base_url: str = field(default_factory=lambda: _env("JIRA_BASE_URL", "").rstrip("/"))
    jira_email: str = field(default_factory=lambda: _env("JIRA_EMAIL", ""))
    jira_api_token: str = field(default_factory=lambda: _env("JIRA_API_TOKEN", ""))
    jira_jql: str = field(default_factory=lambda: _env("JIRA_JQL", ""))

    port: int = field(default_factory=lambda: _int("PORT", 8300))

    index_dir: Path = ROOT / ".index"

    @property
    def embed_key(self) -> str:
        return self.embed_api_key or self.openrouter_api_key

    @property
    def pinecone_configured(self) -> bool:
        return bool(self.pinecone_api_key or self.pinecone_host)

    @property
    def rerank_enabled(self) -> bool:
        if self.rerank_provider == "local":
            return True
        if self.rerank_provider == "jina":
            return bool(self.jina_api_key)
        return False

    @property
    def jira_configured(self) -> bool:
        placeholder = "your-site" in self.jira_base_url
        return bool(self.jira_base_url and self.jira_email and self.jira_api_token and not placeholder)


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def sources() -> tuple[Source, ...]:
    path = ROOT / "sources.yaml"
    if not path.exists():  # e.g. a serverless bundle without the data tree
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for s in raw["sources"]:
        rel = s.get("path")
        out.append(
            Source(
                id=s["id"],
                label=s["label"],
                kind=s["kind"],
                path=(ROOT / rel).resolve() if rel else None,
                provider=s.get("provider", "local"),
                repo=s.get("repo", ""),
                ref=s.get("ref", "main"),
                description=s.get("description", ""),
                phase=int(s.get("phase", 1)),
            )
        )
    return tuple(out)


def source_by_id(source_id: str) -> Source | None:
    return next((s for s in sources() if s.id == source_id), None)


@lru_cache
def glossary() -> dict[str, list[str]]:
    path = ROOT / "glossary.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {k.lower(): [v.lower() for v in vals] for k, vals in (raw.get("expansions") or {}).items()}
