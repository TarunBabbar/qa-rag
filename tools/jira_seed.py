"""Seed the QAB Jira project from tools/seed_data.yaml.

Idempotent: an issue whose summary already exists in the project is skipped, so
re-runs only create what is missing. Relationships become real Jira issue links
("relates to"); the referenced key is also written into the description as a
fallback so QABuddy can trace it even without link traversal.

    python tools/jira_seed.py [--dry-run]

Reads JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN from .env.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROJECT = os.getenv("JIRA_PROJECT", "QAB")
TYPES = {"story": "10002", "bug": "10039", "test": "10038"}
PRIORITY = {"P0": "Highest", "P1": "High", "P2": "Medium", "P3": "Low"}


def _p(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _list(items: list[str], ordered: bool) -> dict:
    return {
        "type": "orderedList" if ordered else "bulletList",
        "content": [{"type": "listItem", "content": [_p(s)]} for s in items],
    }


def _doc(parts: list[dict]) -> dict:
    return {"type": "doc", "version": 1, "content": parts}


class Jira:
    def __init__(self) -> None:
        base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        email = os.getenv("JIRA_EMAIL", "")
        token = os.getenv("JIRA_API_TOKEN", "")
        if not (base and email and token):
            sys.exit("Set JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN in .env first.")
        self.base = base
        self.http = httpx.Client(auth=(email, token), timeout=30, headers={"Accept": "application/json"})
        me = self.http.get(f"{base}/rest/api/3/myself")
        me.raise_for_status()
        print(f"connected as {me.json()['displayName']} -> {base} project={PROJECT}")

    def existing(self) -> dict[str, str]:
        """summary -> key, for every issue already in the project."""
        out: dict[str, str] = {}
        token = None
        while True:
            params = {"jql": f"project = {PROJECT}", "fields": "summary", "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            r = self.http.get(f"{self.base}/rest/api/3/search/jql", params=params)
            r.raise_for_status()
            data = r.json()
            for issue in data.get("issues", []):
                out[issue["fields"]["summary"].strip()] = issue["key"]
            token = data.get("nextPageToken")
            if data.get("isLast", True) or not token:
                break
        return out

    def create(self, fields: dict, dry: bool) -> str | None:
        if dry:
            print(f"  [dry-run] {fields['summary']}")
            return None
        r = self.http.post(f"{self.base}/rest/api/3/issue", json={"fields": fields})
        if r.status_code >= 400:
            print(f"  ! {fields['summary']}: {r.status_code} {r.text[:200]}")
            return None
        return r.json()["key"]

    def link(self, a: str, b: str, label: str) -> None:
        if not (a and b):
            return
        r = self.http.post(
            f"{self.base}/rest/api/3/issueLink",
            json={"type": {"name": "Relates"}, "inwardIssue": {"key": a}, "outwardIssue": {"key": b}},
        )
        if r.status_code >= 400:
            print(f"  ! link {a} -> {b} ({label}): {r.status_code}")


def story_fields(s: dict) -> dict:
    parts = [_p(s["description"])]
    if s.get("acceptance"):
        parts += [_p("Acceptance criteria:"), _list(s["acceptance"], ordered=False)]
    return {
        "project": {"key": PROJECT},
        "issuetype": {"id": TYPES["story"]},
        "summary": s["summary"],
        "description": _doc(parts),
        "priority": {"name": PRIORITY.get(s.get("priority", "P1"), "High")},
        "labels": ["story", s["module"].lower()] + s.get("labels", []),
    }


def test_fields(t: dict) -> dict:
    parts = [
        _p(f"Test ID: {t['id']}"),
        _p(f"Module: {t['module']} | Type: {t['type']} | Automation: {'Automated' if t.get('automated') else 'Manual'}"),
    ]
    if t.get("covers"):
        parts.append(_p(f"Covers: {t['covers']}"))
    parts += [_p(f"Precondition: {t['precondition']}")]
    parts += [_p("Steps:"), _list(t["steps"], ordered=True)]
    parts += [_p(f"Expected Result: {t['expected']}")]
    labels = ["test-case", t["type"].lower(), t["priority"].lower(), t["module"].lower()]
    if t.get("automated"):
        labels.append("automated")
    else:
        labels.append("manual")
    return {
        "project": {"key": PROJECT},
        "issuetype": {"id": TYPES["test"]},
        "summary": t["summary"],
        "description": _doc(parts),
        "priority": {"name": PRIORITY.get(t["priority"], "Medium")},
        "labels": labels,
    }


def bug_fields(b: dict) -> dict:
    parts = [
        _p(f"Module: {b['module']} | Severity: {b.get('severity', b['priority'])}"),
    ]
    if b.get("found_by"):
        parts.append(_p(f"Found by: {b['found_by']}"))
    parts += [
        _p("Steps to reproduce:"),
        _list(b["repro"], ordered=True),
        _p(f"Expected: {b['expected']}"),
        _p(f"Actual: {b['actual']}"),
    ]
    if b.get("notes"):
        parts.append(_p(f"Notes: {b['notes']}"))
    return {
        "project": {"key": PROJECT},
        "issuetype": {"id": TYPES["bug"]},
        "summary": b["summary"],
        "description": _doc(parts),
        "priority": {"name": PRIORITY.get(b["priority"], "Medium")},
        "labels": ["bug", b["priority"].lower(), b["module"].lower()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", default=str(ROOT / "tools" / "seed_data.yaml"))
    args = ap.parse_args()

    spec = yaml.safe_load(Path(args.data).read_text(encoding="utf-8"))
    jira = Jira()
    have = jira.existing()
    print(f"{len(have)} issues already in {PROJECT}")

    story_keys = {s["id"]: have.get(s["summary"].strip()) for s in spec.get("stories", [])}
    test_keys = {t["id"]: have.get(t["summary"].strip()) for t in spec.get("tests", [])}
    made = skipped = 0

    for s in spec.get("stories", []):
        if have.get(s["summary"].strip()):
            skipped += 1
            continue
        key = jira.create(story_fields(s), args.dry_run)
        if key:
            story_keys[s["id"]] = key
            made += 1

    for t in spec.get("tests", []):
        if have.get(t["summary"].strip()):
            skipped += 1
            continue
        key = jira.create(test_fields(t), args.dry_run)
        if key:
            test_keys[t["id"]] = key
            made += 1

    for b in spec.get("bugs", []):
        if have.get(b["summary"].strip()):
            skipped += 1
            continue
        key = jira.create(bug_fields(b), args.dry_run)
        if key:
            made += 1
        if key and b.get("found_by"):
            jira.link(key, test_keys.get(b["found_by"]), "bug->test")
        if key and b.get("affects_story"):
            jira.link(key, story_keys.get(b["affects_story"]), "bug->story")

    for t in spec.get("tests", []):
        if t.get("covers"):
            jira.link(test_keys.get(t["id"]), story_keys.get(t["covers"]), "test->story")

    print(f"\ncreated {made}, skipped {skipped} (already present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
