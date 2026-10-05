#!/usr/bin/env bash
# QA Copilot, locally, in one command. No local services to run: embeddings and the
# answer LLM are hosted on OpenRouter, vectors live in Pinecone, reranking is Jina.
#
#   ./run.sh            install deps, ingest if the index is empty, build the UI, serve
#   ./run.sh ingest     index changed files   (./run.sh ingest --full  rebuilds everything)
#   ./run.sh eval       retrieval hit-rate + MRR on eval/golden.yaml
#   ./run.sh test       regression tests (no services needed)
#   ./run.sh ask "Why did build #142 fail?" [--mode rca]
set -euo pipefail
cd "$(dirname "$0")"

PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 || true); PORT=${PORT:-8300}
PY=.venv/bin/python

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example. Add OPENROUTER_API_KEY + PINECONE_API_KEY, then re-run."; exit 1; }

python_env() {
  [ -x "$PY" ] && return
  if command -v uv >/dev/null; then
    uv venv -q --python 3.13 .venv && uv pip install -q --python "$PY" -r requirements.txt -r requirements-ingest.txt pytest
  else
    python3 -m venv .venv && "$PY" -m pip install -q -r requirements.txt -r requirements-ingest.txt pytest
  fi
}

ui_build() {
  [ -f ui/dist/index.html ] && return
  (cd ui && npm install --silent && npm run build)
}

cmd=${1:-up}
case "$cmd" in
  test)   python_env; exec "$PY" -m pytest -q tests ;;
  ingest) python_env; shift; exec "$PY" -m qabuddy ingest "$@" ;;
  eval)   python_env; exec "$PY" -m qabuddy eval ;;
  ask)    python_env; shift; exec "$PY" -m qabuddy ask "$@" ;;
  up|*)
    python_env; ui_build
    points=$("$PY" -c "from qabuddy import store; print(store.count())" 2>/dev/null || echo 0)
    if [ "${points:-0}" = "0" ]; then echo "Index is empty: ingesting (first run embeds everything)…"; "$PY" -m qabuddy ingest; fi
    echo "QA Copilot -> http://localhost:${PORT}"
    exec "$PY" -m qabuddy serve
    ;;
esac
