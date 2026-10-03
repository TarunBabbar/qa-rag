# QABuddy.ai: multi-source hybrid RAG for QA engineers

Ask one question, get one **cited** answer grounded in the team's Selenium and
Playwright frameworks, test case repository, Jira bugs, requirement documents,
meeting notes, Lucid charts and Jenkins logs.

This build runs on **free / hosted services** — no local models, no local vector
database — so the same code runs on your machine and as a Vercel Function:

| Layer | Choice | Cost |
|---|---|---|
| Embeddings | OpenRouter `qwen/qwen3-embedding-4b`, Matryoshka-truncated to 1024-d | ~$0.02 / M tokens (whole corpus ≈ $0.003) |
| Vector DB | Pinecone serverless, one index, dense + sparse per record | Free tier |
| Reranker | Jina Reranker v2 (hosted) | Free 10M-token grant |
| Answer LLM | OpenRouter free models (comma-separated fallback list) | **$0** |
| Hosting | One Python FastAPI app on Vercel + React UI | Free / Pro |

> Command Code cannot serve this app: its catalog has no embedding models and no
> `/embeddings` endpoint, and its REST API is the headless `cmd -p` agent runner,
> not an OpenAI-compatible chat endpoint.

## Quick start (local)

```bash
cp .env.example .env      # then set OPENROUTER_API_KEY (+ PINECONE_API_KEY, JINA_API_KEY)
./run.sh                  # installs deps, ingests if empty, builds the UI, serves :8300
```

Windows (PowerShell):

```powershell
Copy-Item .env.example .env   # fill in the keys
.\run.ps1
```

The first ingest embeds the whole corpus into Pinecone and auto-creates the index
(`dotproduct`, 1024-d, aws/us-east-1) if it does not exist. Re-runs are incremental
(a sha256 manifest in `.index/`).

## Deploy to Vercel

1. Push this repo to GitHub, then **Vercel → Add New → Project → Import** it.
2. In **Settings → Environment Variables**, add:
   `OPENROUTER_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX`,
   `EMBED_PROVIDER=openrouter`, `EMBED_MODEL=qwen/qwen3-embedding-4b`, `EMBED_DIM=1024`,
   `LLM_PROVIDER=openrouter`, `LLM_MODEL=inclusionai/ling-3.0-flash-sante:free,cohere/north-mini-code:free`,
   `RERANK_PROVIDER=jina`, `JINA_API_KEY`.
3. Deploy. Vercel detects FastAPI (`pyproject.toml` → `qabuddy.vercel:app`), builds the
   UI, and serves the app from one function. Ingestion stays local
   (`python -m qabuddy ingest`); the hosted app only reads the shared Pinecone index.

`vercel.json` sets `maxDuration: 60` for the function and bundles `sources.yaml` /
`glossary.yaml`. `ui/dist` is served through the function, identical to local.

## How it works

```
INGEST (local)    data/ + Jira ─► source-aware chunkers ─► OpenRouter embeddings (1024-d)
                                          └─ code-aware BM25 (sparse, uint32)  ─► Pinecone

ASK (local or Vercel)
  question ─► embed ─► Pinecone dense + sparse ─► client-side RRF ─► Jina rerank
           ─► select (dedupe, per-source cap, quotas, token budget) ─► free LLM (SSE) ─► cited answer
```

Two deliberate differences from a Qdrant-based design:

* **Pinecone has no server-side RRF.** The dense and sparse queries run separately and are
  fused client-side (reciprocal rank fusion) — which also gives the UI each side's rank.
* **Pinecone applies no IDF.** The sparse side carries BM25 TF saturation + length
  normalisation only; the dense side supplies the semantic signal and ranks are fused.

## Data sources

```
data/
├── 00_TestCases/            VWO_500_Test_Cases.csv
├── 01_JIRA_Tickets/         VWO-26, VWO-33 exports + QAB-101..103
├── 02_Company_Docs/         QA handbook, coding standards, onboarding PDF
├── 03_Meeting_Notes/        triage meeting, sprint planning, stand-up VTT
├── 04_Lucid_charts/         login flow CSV, CI pipeline text, A/B lifecycle JSON
├── 05_PRD_SRS_BRD_FRDs/     Product Requirements Document (PRD) VWO.com
├── 06_Jenkins_Logs/         builds #142, #143, #88 + JUnit XML
└── 07_Source_Codes/         ATB13xSeleniumAdvanceFramework, AdvancePlaywrightFramework1x
```

`sources.yaml` maps each folder to its chunker. `_`-prefixed files are never ingested.

## Chunking rules (per source)

The rule: **chunk along the unit a QA engineer asks about**, never by character count alone.

| Source | Chunk unit | Target | Overlap | Why |
|---|---|---|---|---|
| Test cases | one row | ~190 tokens | none | a test case is already atomic |
| + inventory | repository summary + one per module | ≤800 | none | "which features have no tests?" asks about absence |
| Jira | summary + description; each comment separately | ≤500 | 1 paragraph | comments carry the root cause |
| Docs (PDF/MD) | heading-aware section | 500 (max 700) | ~15% | answers live in a section |
| + outline | one per long document | ≤900 | none | lets an answer enumerate every requirement |
| Transcripts | speaker turns | ~400 | 1 turn | a hand-off is never cut |
| Lucid charts | one diagram page as nodes + flows | ≤600 | none | flows reference their nodes |
| Jenkins logs | build summary + one chunk per failure window | ≤40 lines | none | 95% of a log is noise |
| Source code | AST node (class, method, `test(...)`) | ≤1500 chars | none | tree-sitter never cuts a method |

## Modes

| Mode | Searches | Shapes the answer as |
|---|---|---|
| Ask anything | everything | onboarding / KB answer |
| Failure analysis (RCA) | Jenkins, Jira, meetings, code, diagrams, docs | symptom, root cause, flaky or real, tickets, fix |
| Test design & gaps | requirements, test cases, Jira, docs, meetings | covered vs gaps table, new cases in team format |
| Bug triage | Jira, test cases, requirements, docs | duplicates, severity, priority, affected tests |
| Framework coding help | both frameworks + standards | code in the team's own classes and helpers |
| Traceability (RTM) | requirements, test cases, Jira | requirement → test ids → automated → bugs |

## CLI

```bash
python -m qabuddy ingest [--full] [--source jira]   # build/update the Pinecone index
python -m qabuddy ask "Why did vwo-selenium-regression #142 fail?" --mode rca
python -m qabuddy search "Is VWO-33 a duplicate?"   # retrieval only, with the trace
python -m qabuddy eval                              # retrieval ablations (dense/BM25/hybrid/full)
python -m qabuddy sync-jira --jql "project = VWO"   # pull tickets, then ingest
python -m qabuddy serve                             # API + built UI on :8300
```

## Tests

18 regression tests pin the chunkers, tokenizer, citation parsing and secret redaction.
No services required:

```bash
python -m pytest -q tests
```

## Configuration

All settings live in `.env` (see `.env.example`): embeddings, Pinecone, reranker, LLM
fallback list, retrieval knobs (`PREFETCH_K`, `RERANK_CANDIDATES`, `FINAL_K`,
`CONTEXT_TOKENS`) and optional Jira credentials. Provider switches:
`EMBED_PROVIDER=openrouter|openai|ollama`, `RERANK_PROVIDER=jina|local|none`,
`LLM_PROVIDER=openrouter|groq|openai|ollama`.

## Known limits

* Free OpenRouter models share an upstream pool and can be rate-limited; the LLM list
  falls through to the next model on 404/429, but a busy pool can still make you wait.
* Reasoning-style free models spend part of the token budget on hidden reasoning, so
  answers are slower (tens of seconds); the mode budgets are set generously for this.
* Pinecone free tier: one serverless index, ~2 GB.
* Grounding rules reduce, but do not eliminate, model errors: answers cite sources so a
  human can check them, and the UI flags any answer without citations.

## License

**Tarun Kumar Babbar License** — licensed to Tarun Kumar Babbar. See [LICENSE](LICENSE).

Built on the QABuddy chapter-12 blueprint (multi-source hybrid RAG), reworked to run
entirely on hosted/free services.
