"""Pinecone: one serverless index, dense + sparse vectors on each record.

    values        1024-d, unit-norm   OpenRouter embeddings (meaning)
    sparse_values uint32 + weights    code-aware BM25 (exact identifiers, error strings)

Pinecone stores both on one record (metric `dotproduct`, `vector_type=dense`)
and combines them in a single dot product, but — unlike Qdrant — it has no
server-side RRF fusion. So this module runs the two signals as separate
queries (the UI needs each side's rank anyway) and fuses them client-side with
reciprocal rank fusion.

The public functions keep their original signatures: `retrieve.py` and
`ingest.py` do not change. Query results are wrapped in `Match`, a tiny stand-in
for Qdrant's `ScoredPoint` (`id` / `payload` / `score`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from .config import settings
from .models import Chunk
from .sparse import SparseVec

RRF_K = 60
_pc: Pinecone | None = None
_index: Any = None


@dataclass
class Match:
    id: str
    payload: dict
    score: float = 0.0


def client() -> Pinecone:
    global _pc
    if _pc is None:
        s = settings()
        if not s.pinecone_configured:
            raise RuntimeError("Pinecone is not configured. Set PINECONE_API_KEY (and PINECONE_INDEX) in .env.")
        _pc = Pinecone(api_key=s.pinecone_api_key or "unused")
    return _pc


def index() -> Any:
    global _index
    if _index is None:
        s = settings()
        _index = client().Index(s.pinecone_host) if s.pinecone_host else client().Index(s.pinecone_index)
    return _index


def _ns() -> str:
    return settings().pinecone_namespace


def _wait_ready(name: str, timeout: float = 180.0) -> None:
    pc, deadline = client(), time.time() + timeout
    while time.time() < deadline:
        try:
            if pc.describe_index(name).status.ready:
                return
        except Exception:
            pass
        time.sleep(2)


def _wait_gone(name: str, timeout: float = 120.0) -> None:
    pc, deadline = client(), time.time() + timeout
    while time.time() < deadline:
        try:
            pc.describe_index(name)
        except Exception:
            return
        time.sleep(2)


def collection_exists() -> bool:
    s = settings()
    if s.pinecone_host:
        return True
    try:
        return client().has_index(s.pinecone_index)
    except Exception:
        return False


def create_collection(recreate: bool = False) -> None:
    s, pc = settings(), client()
    if s.pinecone_host:
        if recreate:
            index().delete(delete_all=True, namespace=_ns())
        return
    exists = pc.has_index(s.pinecone_index)
    if exists:
        dim = None
        try:
            dim = pc.describe_index(s.pinecone_index).dimension
        except Exception:
            try:
                dim = index().describe_index_stats().dimension
            except Exception:
                dim = None
        if dim is not None and dim != s.embed_dim:  # vectors from a different dim are not comparable
            pc.delete_index(s.pinecone_index)
            _wait_gone(s.pinecone_index)
            exists = False
    if exists:
        if recreate:  # fast clear: keep the index, drop its vectors
            index().delete(delete_all=True, namespace=_ns())
        return
    pc.create_index(
        name=s.pinecone_index,
        dimension=s.embed_dim,
        metric="dotproduct",
        vector_type="dense",
        spec=ServerlessSpec(cloud=s.pinecone_cloud, region=s.pinecone_region),
    )
    _wait_ready(s.pinecone_index)


def collection_dim() -> int | None:
    try:
        return index().describe_index_stats().dimension
    except Exception:
        return None


def count(source_id: str | None = None) -> int:
    try:
        stats = index().describe_index_stats()
    except Exception:
        return 0
    ns = _ns()
    if ns:
        summary = (getattr(stats, "namespaces", None) or {}).get(ns)
        return int(getattr(summary, "vector_count", 0) or 0) if summary else 0
    return int(getattr(stats, "total_vector_count", 0) or 0)


def _clean_metadata(payload: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if v is None:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float, str)):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            vals = [str(x) for x in v if x is not None]
            if vals:
                out[k] = vals
        else:
            out[k] = str(v)
    return out


def delete_file(file_path: str) -> None:
    index().delete(filter={"file_path": {"$eq": file_path}}, namespace=_ns())


def upsert(chunks: list[Chunk], dense: list[list[float]], sparse: list[SparseVec], batch: int = 100) -> None:
    records = [
        {
            "id": c.id,
            "values": d,
            "sparse_values": {"indices": [int(i) for i in sv.indices], "values": [float(v) for v in sv.values]},
            "metadata": _clean_metadata(c.payload()),
        }
        for c, d, sv in zip(chunks, dense, sparse)
    ]
    for i in range(0, len(records), batch):
        index().upsert(vectors=records[i: i + batch], namespace=_ns())


def _pf(source_ids: list[str] | None) -> dict | None:
    if not source_ids:
        return None
    return {"source_id": {"$in": list(source_ids)}}


def _sv(sparse: SparseVec) -> dict:
    return {"indices": [int(i) for i in sparse.indices], "values": [float(v) for v in sparse.values]}


def _payloads(ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    idx, ns = index(), _ns()
    for i in range(0, len(ids), 100):
        got = idx.fetch(ids=ids[i: i + 100], namespace=ns)
        vectors = getattr(got, "vectors", None)
        if vectors is None and isinstance(got, dict):
            vectors = got.get("vectors")
        for pid, rec in (vectors or {}).items():
            md = getattr(rec, "metadata", None)
            if md is None and isinstance(rec, dict):
                md = rec.get("metadata")
            out[pid] = md or {}
    return out


def _match_list(res: Any) -> list[Match]:
    matches = (res.get("matches") if isinstance(res, dict) else res.matches) or []
    return [Match(m["id"], m.get("metadata") or {}, m.get("score", 0.0)) for m in matches]


def dense_search(dense: list[float], *, source_ids: list[str] | None = None, limit: int) -> list[Match]:
    """Dense-only query with payloads (used by `eval`)."""
    res = index().query(vector=dense, top_k=limit, filter=_pf(source_ids), include_metadata=True, include_values=False, namespace=_ns())
    return _match_list(res)


def sparse_search(sparse: SparseVec, *, source_ids: list[str] | None = None, limit: int) -> list[Match]:
    """Sparse (BM25) only ranking, with payloads (used by `eval`).

    Pinecone rejects a sparse-only query on a dense index; a zero dense vector
    adds nothing to the dot product, so the ranking is driven by BM25.
    """
    res = index().query(vector=[0.0] * settings().embed_dim, sparse_vector=_sv(sparse), top_k=limit, filter=_pf(source_ids), include_metadata=True, include_values=False, namespace=_ns())
    return _match_list(res)


def hybrid(dense: list[float], sparse: SparseVec, *, source_ids: list[str] | None, prefetch_k: int, fused_k: int) -> dict[str, Any]:
    idx, ns, flt = index(), _ns(), _pf(source_ids)
    dres = idx.query(vector=dense, top_k=prefetch_k, filter=flt, include_metadata=False, include_values=False, namespace=ns)
    # sparse side: a zero dense vector, because Pinecone rejects sparse-only queries
    # on a dense index and zero contributes nothing to the dot product
    sres = idx.query(vector=[0.0] * settings().embed_dim, sparse_vector=_sv(sparse), top_k=prefetch_k, filter=flt, include_metadata=False, include_values=False, namespace=ns)
    dm = (dres.get("matches") if isinstance(dres, dict) else dres.matches) or []
    sm = (sres.get("matches") if isinstance(sres, dict) else sres.matches) or []
    dense_rank = {m["id"]: (i + 1, m["score"]) for i, m in enumerate(dm)}
    sparse_rank = {m["id"]: (i + 1, m["score"]) for i, m in enumerate(sm)}

    rrf: dict[str, float] = {}
    for ranks in (dense_rank, sparse_rank):
        for pid, (rank, _score) in ranks.items():
            rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (RRF_K + rank)
    top = sorted(rrf.items(), key=lambda kv: -kv[1])[:fused_k]
    ids = [pid for pid, _ in top]
    payloads = _payloads(ids)
    fused = [Match(pid, payloads.get(pid, {}), rrf[pid]) for pid in ids]
    return {"dense": dense_rank, "sparse": sparse_rank, "fused": fused}


_ID = re.compile(r"\b[A-Z][A-Z0-9]{1,15}-\d{1,6}\b")


def exact_ids(question: str) -> list[str]:
    return list(dict.fromkeys(_ID.findall(question.upper())))


def _scan_ids(limit: int = 20000) -> list[str]:
    """Fallback id enumeration for metadata lookups when the zero-vector query is unavailable."""
    ids: list[str] = []
    try:
        for pid in index().list(namespace=_ns()):
            ids.append(pid)
            if len(ids) >= limit:
                break
    except Exception:
        return []
    return ids


def _filter_query(flt: dict, limit: int) -> list[Match]:
    idx, ns = index(), _ns()
    try:
        res = idx.query(vector=[0.0] * settings().embed_dim, top_k=limit, filter=flt, include_metadata=True, include_values=False, namespace=ns)
        matches = (res.get("matches") if isinstance(res, dict) else res.matches) or []
        if matches:
            return [Match(m["id"], m.get("metadata") or {}, m.get("score", 0.0)) for m in matches]
        return []
    except Exception:
        pass
    # naive fallback: enumerate the (small) index and filter metadata client-side
    wanted = flt
    out: list[Match] = []
    for pid, md in _payloads(_scan_ids()).items():
        if _matches(md, wanted):
            out.append(Match(pid, md, 0.0))
            if len(out) >= limit:
                break
    return out


def _matches(md: dict, flt: dict) -> bool:
    if "$and" in flt:
        return all(_matches(md, c) for c in flt["$and"])
    if "$or" in flt:
        return any(_matches(md, c) for c in flt["$or"])
    for key, cond in flt.items():
        if "$in" in cond:
            if md.get(key) not in cond["$in"]:
                return False
        elif "$eq" in cond:
            if md.get(key) != cond["$eq"]:
                return False
    return True


def by_exact_id(ids: list[str], source_ids: list[str] | None, limit: int = 8) -> list:
    """Ticket keys and test case ids are looked up directly, not searched for."""
    if not ids:
        return []
    or_clause = {"$or": [{"jira_key": {"$in": ids}}, {"tc_id": {"$in": ids}}]}
    flt = {"$and": [or_clause, {"source_id": {"$in": list(source_ids)}}]} if source_ids else or_clause
    return _filter_query(flt, limit)


def summaries(source_ids: list[str], limit: int = 12) -> list:
    """Inventory chunks (test repository summary, document outlines) for the given sources."""
    if not source_ids:
        return []
    flt = {"$and": [{"source_id": {"$in": list(source_ids)}}, {"summary": {"$in": ["repository", "outline"]}}]}
    return _filter_query(flt, limit)


def get(point_id: str) -> dict | None:
    got = index().fetch(ids=[point_id], namespace=_ns())
    vectors = getattr(got, "vectors", None)
    if vectors is None and isinstance(got, dict):
        vectors = got.get("vectors")
    if not vectors:
        return None
    rec = next(iter(vectors.values()))
    md = getattr(rec, "metadata", None)
    if md is None and isinstance(rec, dict):
        md = rec.get("metadata")
    return md or None
