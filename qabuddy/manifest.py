"""The ingestion manifest: sha256 per indexed file, so re-runs are incremental.

Kept free of chunker imports (tree-sitter, pypdf, openpyxl) so the API — and the
Vercel function — can read index status without pulling the parsing stack.
"""

from __future__ import annotations

import json

from .config import settings


def manifest_path():
    return settings().index_dir / "manifest.json"


def load_manifest() -> dict:
    p = manifest_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"files": {}}


def save_manifest(m: dict) -> None:
    settings().index_dir.mkdir(parents=True, exist_ok=True)
    manifest_path().write_text(json.dumps(m, indent=1), encoding="utf-8")


def source_counts() -> dict[str, dict]:
    m = load_manifest()
    out: dict[str, dict] = {}
    for rel, info in m.get("files", {}).items():
        d = out.setdefault(info["source_id"], {"files": 0, "chunks": 0, "indexed_at": None})
        d["files"] += 1
        d["chunks"] += info.get("chunks", 0)
        d["indexed_at"] = max(filter(None, [d["indexed_at"], info.get("indexed_at")]), default=None)
    return out
