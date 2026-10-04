"""Stage API-backed sources into a transient directory for one ingest run.

Nothing is kept: the staging tree is created when ingest starts and removed when
it finishes. GitHub content arrives as a **single tarball** (one HTTP request, no
per-file API calls), and Jira issues are written as the same Markdown the local
`sync-jira` produces — so every existing chunker works unchanged.
"""

from __future__ import annotations

import fnmatch
import io
import os
import shutil
import tarfile
from pathlib import Path

import httpx

from .config import ROOT, Source, settings

GITHUB_API = "https://api.github.com"

# One download per (repo, ref) per run: several sources may narrow the same repo
# to different paths, and fetching the tarball once per source would be wasteful.
_TARBALLS: dict[tuple[str, str], bytes] = {}


def staging_root() -> Path:
    return ROOT / "data" / "_staging"


def stage_source(src: Source) -> Path | None:
    """Materialise a non-local source and return the directory to walk."""
    if src.provider == "local":
        return src.path
    dest = staging_root() / src.id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if src.provider == "jira":
        stage_jira(dest, src.jql)
    elif src.provider == "github":
        stage_github(dest, src.repo, src.ref, src.paths)
    else:
        raise RuntimeError(f"Unknown source provider: {src.provider}")
    return dest


def cleanup() -> None:
    """Remove the staging tree; it is scratch space, never a source of truth."""
    root = staging_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    _TARBALLS.clear()


def stage_jira(dest: Path, jql: str = "") -> None:
    from .jira_sync import fetch_issues, ticket_markdown

    s = settings()
    query = jql or s.jira_jql
    count = 0
    for issue in fetch_issues(query):
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in issue["key"])
        (dest / f"{safe}.md").write_text(ticket_markdown(issue, s.jira_base_url), encoding="utf-8")
        count += 1
    print(f"[fetch] jira: {count} issues ({query})")


def _tarball(repo: str, ref: str) -> bytes:
    key = (repo, ref)
    if key in _TARBALLS:
        return _TARBALLS[key]
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API}/repos/{repo}/tarball/{ref}"
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=180) as r:
        if r.status_code == 404:
            raise RuntimeError(f"GitHub repo or ref not found: {repo}@{ref}")
        r.raise_for_status()
        blob = r.read()
    _TARBALLS[key] = blob
    return blob


def stage_github(dest: Path, repo: str, ref: str = "main", paths: tuple[str, ...] = ()) -> None:
    if not repo:
        raise RuntimeError("a github source needs `repo: owner/name` in sources.yaml")
    blob = _tarball(repo, ref)

    files = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = Path(member.name).parts
            if len(parts) < 2:  # drop the tarball's top-level directory
                continue
            rel = Path(*parts[1:])
            if rel.is_absolute() or ".." in rel.parts:
                continue
            if paths and not _wanted(rel.as_posix(), paths):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tar.extractfile(member)
            if handle is not None:
                target.write_bytes(handle.read())
                files += 1
    scope = f", {len(paths)} path filter(s)" if paths else ""
    print(f"[fetch] github: {repo}@{ref} ({files} files{scope})")


def _wanted(rel: str, paths: tuple[str, ...]) -> bool:
    """fnmatch is deliberate: `*` crossing `/` keeps `src/api/*` meaning
    'everything under src/api', which is what sources.yaml intends."""
    return any(fnmatch.fnmatchcase(rel, pattern) for pattern in paths)
