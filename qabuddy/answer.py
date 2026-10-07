"""Modes, prompts, streaming answers and citation checks.

A mode is a QA task (RCA, test design, triage...) expressed as three things:
which sources to search by default, how many to retrieve, and an extra
instruction that shapes the answer. The grounding rules are shared by all.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Iterator

from . import llm
from .config import settings
from .retrieve import Retrieval, retrieve
from .text import approx_tokens, truncate_tokens

SYSTEM = """You are QA Copilot, the internal assistant for the QA team. Answer using ONLY the numbered sources in the user message.

Rules:
1. Cite every factual claim inline with the number of the source that supports it, in square brackets: [2] or [1][3]. Use no other citation format: no 【】 markers and no line ranges inside citations.
2. If the sources do not contain the answer, say "I couldn't find this in the knowledge base." and name what is missing. Never fill gaps with outside knowledge.
3. Never invent test case IDs, ticket keys, file paths, class or method names, build numbers, line numbers or requirement numbers. Copy them exactly from the sources.
4. Prefer exact identifiers: test case IDs, Jira keys, file:line, build numbers, PRD section numbers.
5. Be concise and specific. Use short bullets or tables where they help. No preamble and no closing summary.
6. Put code in fenced blocks with a language tag. Quote only the lines that matter.
7. When you write something new (test cases, code, a plan), cite the sources it is based on: the requirement it covers and the existing file, test case or standard whose format you follow."""


@dataclass
class Mode:
    id: str
    label: str
    icon: str
    description: str
    sources: list[str] | None
    k: int
    max_tokens: int
    instruction: str
    examples: list[str] = field(default_factory=list)
    # minimum sources per knowledge source: gap analysis is useless without
    # both the requirement sections and the test case inventory in context
    quotas: dict[str, int] = field(default_factory=dict)


MODES: dict[str, Mode] = {
    m.id: m
    for m in [
        Mode(
            "ask", "Ask anything", "💬", "Onboarding and knowledge-base questions across every source",
            None, 6, 1600, "",
            [
                "What test cases cover the booking flow?",
                "How do I run the API suite locally?",
                "Which bugs are linked to the booking tests?",
                "What does the room inventory story require?",
            ],
        ),
        Mode(
            "rca", "Failure analysis (RCA)", "🧯", "Correlate test failures with code, bugs and Jira tickets",
            ["jira-bugs", "jira-testcases", "ui-automation", "api-automation", "test-framework"], 8, 2000,
            "Structure the answer as: **Symptom** (exact error, build, test), **Root cause** (evidence with citations; "
            "say whether it is a product bug, a test/framework problem or an environment problem), **Flaky or real?** "
            "(retry evidence), **Related tickets**, **Fix and next steps** (concrete, with file and line where the sources show them).",
            [
                "Why did the negative auth test fail?",
                "Is the booking creation test flaky?",
                "Which bug explains the 500 on a booking with no lastname?",
            ],
            {"jira-bugs": 2},
        ),
        Mode(
            "test_design", "Test design & gaps", "🧪", "Compare requirements with test cases, find gaps, draft new cases",
            ["jira-stories", "jira-testcases"], 8, 2400,
            "Compare the requirement sections with the test cases in the sources. First list what is covered "
            "(requirement -> test case IDs). Then give the gaps as a table: | Requirement (doc §) | Missing scenario | "
            "Suggested priority | Positive/Negative |. When asked to write test cases, use the team's format: ID "
            "MODULE-NNN, a description starting with 'Verify', Precondition, numbered Steps, Expected Result, Priority. "
            "Use the test case repository summary and the document outline to see every module and every requirement. "
            "Mapping a requirement to a module by matching feature and module names is expected: do it, and mark such "
            "mappings (inferred). Only call something a gap when no module or test case covers it.",
            [
                "Which booking features have no test cases?",
                "Draft 4 API test cases for room availability in our format",
                "Review TC-BOOK-006 and suggest how to make it sharper",
            ],
            {"jira-testcases": 3},
        ),
        Mode(
            "triage", "Bug triage", "🐞", "Duplicates, severity, priority and affected tests for a ticket",
            ["jira-bugs", "jira-testcases"], 7, 1600,
            "Structure the answer as: **Summary**, **Duplicates / related** (ticket keys), **Severity** and **Priority** "
            "justified by the team's triage rules in the sources, **Affected test cases** (IDs), **Suggested owner / component**. "
            "If the triage rules are not in the sources, say so instead of inventing them.",
            [
                "Triage QAB-72 using our triage rules",
                "Which open bugs affect booking test cases?",
                "Set severity and priority for the double-booking bug",
            ],
            {"jira-bugs": 2},
        ),
        Mode(
            "code", "Framework coding help", "🛠️", "Answers and code from your own Playwright automation framework",
            ["ui-automation", "api-automation", "test-framework"], 7, 2600,
            "Answer at the level of THIS team's frameworks: reuse their classes, helpers, fixtures, locators and naming "
            "exactly as they appear in the sources, and cite the files. Point out anti-patterns visible in the sources "
            "(for example hard sleeps). When writing new code, say which existing file it follows.",
            [
                "How is the API client structured in our Playwright framework?",
                "Write a Playwright API test that deletes a booking, reusing our fixtures",
                "How do our tests tag the Jira test-case ids?",
            ],
        ),
        Mode(
            "rtm", "Traceability (RTM)", "🧭", "Requirements -> test cases -> automation -> open bugs",
            ["jira-stories", "jira-testcases", "jira-bugs", "ui-automation", "api-automation"], 10, 2400,
            "Output a traceability matrix: | Requirement (doc §, ID) | Test case IDs | Automated? | Open bugs | Coverage |. "
            "Use the test case repository summary to see every module and its ID range. Mapping a requirement to a module "
            "by matching feature and module names is expected: do it and mark the cell (inferred). Write 'No test case "
            "found' where no module covers the requirement. Base every cell on the sources.",
            [
                "Build an RTM for the booking stories",
                "Trace the room inventory story to its test cases, automation status and open bugs",
            ],
            {"jira-testcases": 4},
        ),
    ]
}

_CITE = re.compile(r"\[(\d{1,2})\]")  # code is stripped first, so word[1] is a citation
_FENCE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
# gpt-oss was trained on browsing-style citations, "【3†L51-L82】". The line
# range is the model's own invention, so only the source number is kept.
_ALT_CITE = re.compile(r"【\s*(\d{1,2})(?:†[^】]*)?】|\[(\d{1,2})†[^\]]*\]")


def normalize_citations(text: str) -> str:
    return _ALT_CITE.sub(lambda m: f"[{m.group(1) or m.group(2)}]", text)


# Hard facts an answer can be checked against: ticket/test ids and file paths.
# Grounding is judged on these, not on the model's citation discipline, so it
# holds as the free-model list rotates.
_ID_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{1,15}(?:-[A-Z0-9]{1,15})*-\d{1,6}\b")
_PATH_TOKEN = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\.(?:ts|tsx|js|jsx|java|py|json|ya?ml|md|xml|feature|properties)\b")


def identifiers(answer: str) -> list[str]:
    # Do not strip code here: paths/labels are usually written inside `backticks`,
    # and grounding wants them. Marker counting strips code separately.
    text = normalize_citations(answer)
    return sorted(set(_ID_TOKEN.findall(text)) | set(_PATH_TOKEN.findall(text)))


def lexical_grounding(answer: str, source_texts: list[str]) -> dict:
    """Do the answer's identifiers actually appear in the retrieved sources?"""
    ids = identifiers(answer)
    if not ids:
        return {"checked": 0, "found": 0, "ratio": None, "grounded": False, "missing": []}
    hay = "\n".join(source_texts).lower()
    missing = [i for i in ids if i.lower() not in hay]
    found = len(ids) - len(missing)
    ratio = found / len(ids)
    return {"checked": len(ids), "found": found, "ratio": round(ratio, 3), "grounded": ratio >= 0.8, "missing": missing[:10]}


def citations(answer: str, n_sources: int, source_texts: list[str] | None = None) -> dict:
    prose = _FENCE.sub(" ", normalize_citations(answer))
    found = [int(m) for m in _CITE.findall(prose)]
    valid = sorted({n for n in found if 1 <= n <= n_sources})
    invalid = sorted({n for n in found if not 1 <= n <= n_sources})
    not_found = "couldn't find" in answer.lower() or "could not find" in answer.lower()
    lex = lexical_grounding(answer, source_texts) if source_texts else {"grounded": False, "ratio": None, "missing": [], "checked": 0}
    return {
        "used": valid,
        "invalid": invalid,
        # markers = the model followed the [n] formatting rule
        "markers": bool(valid),
        # attributed = the answer's hard facts really are in the sources (model-agnostic)
        "attributed": bool(lex["grounded"]),
        "identifier_ratio": lex.get("ratio"),
        "missing_identifiers": lex.get("missing", []),
        "grounded": bool(valid) or not_found or bool(lex["grounded"]),
        "said_not_found": not_found,
    }


def _budgets(sizes: list[int], total: int) -> list[int]:
    """Water-filling: short sources keep their full length and hand the
    unused share to long ones, instead of every source getting total/n."""
    alloc = [0] * len(sizes)
    remaining, open_idx = total, list(range(len(sizes)))
    while open_idx and remaining > 0:
        share = remaining // len(open_idx)
        if share <= 0:
            break
        done = [i for i in open_idx if sizes[i] - alloc[i] <= share]
        if not done:
            for i in open_idx:
                alloc[i] += share
            break
        for i in done:
            remaining -= sizes[i] - alloc[i]
            alloc[i] = sizes[i]
        open_idx = [i for i in open_idx if i not in done]
    return [max(a, 200) for a in alloc]


def _context(r: Retrieval) -> str:
    texts = [c.payload.get("text", "") for c in r.sources]
    budgets = _budgets([approx_tokens(t) for t in texts], settings().context_tokens)
    parts = []
    for i, (c, text, budget) in enumerate(zip(r.sources, texts, budgets), start=1):
        p = c.payload
        head = f"[{i}] {p.get('title')}\nsource: {p.get('source_id')} | {p.get('file_path')} | {p.get('locator')}"
        parts.append(f"{head}\n{truncate_tokens(text, budget)}")
    return "\n\n".join(parts)


def _history_text(history: list[dict] | None) -> str:
    if not history:
        return ""
    lines = []
    for h in history[-4:]:
        content = re.sub(r"\[\d{1,2}\]", "", str(h.get("content", "")))[:600]
        lines.append(f"{h.get('role', 'user')}: {content}")
    return "Conversation so far (for context only; cite only the sources below):\n" + "\n".join(lines) + "\n\n"


def rewrite(question: str, history: list[dict] | None) -> str | None:
    """Turn a follow-up ("and on the Playwright side?") into a standalone search query."""
    if not history:
        return None
    msgs = [
        {
            "role": "system",
            "content": "Rewrite the user's latest message as one standalone search query for a QA knowledge base, using the "
            "conversation for context. Keep exact identifiers (ticket keys, test ids, class, method and file names, build "
            "numbers). Output only the query.",
        },
        {"role": "user", "content": _history_text(history) + f"Latest message: {question}"},
    ]
    try:
        text, _ = llm.complete(msgs, max_tokens=300)
        return text.strip().strip('"') or None
    except Exception:
        return None


def build_messages(question: str, mode: Mode, r: Retrieval, history: list[dict] | None) -> list[dict]:
    system = SYSTEM + (f"\n\nTask: {mode.label}. {mode.instruction}" if mode.instruction else "")
    if r.low_confidence:
        system += "\n\nThe retrieved sources scored low for relevance. Be explicit about what they do not cover."
    user = f"{_history_text(history)}Sources:\n\n{_context(r)}\n\nQuestion: {question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def repair_citations(answer: str, r: Retrieval) -> str | None:
    """One small call to insert [n] markers. Formatting only; the prose is not rewritten.

    Used only when the answer is already attributed to the sources but the model
    omitted markers — i.e. a formatting miss, not a faithfulness problem.
    """
    listing = "\n".join(f"[{i}] {c.payload.get('title')}" for i, c in enumerate(r.sources, start=1))
    msgs = [
        {
            "role": "system",
            "content": "You only add citation markers. Return the user's text unchanged except for inserting [n] "
            "markers after sentences, using only the numbers of the sources that support them. Never rewrite, "
            "summarise, reorder or add text.",
        },
        {"role": "user", "content": f"Sources:\n{listing}\n\nText:\n{answer}"},
    ]
    try:
        out, _ = llm.complete(msgs, max_tokens=min(2500, max(400, len(answer) // 3)))
    except Exception:  # the repair is an enhancement: the answer stands without it
        return None
    out = normalize_citations(out).strip()
    if out and citations(out, len(r.sources))["markers"] and len(out) >= 0.6 * len(answer):
        return out
    return None


def _safe_stream(messages: list[dict], max_tokens: int) -> Iterator[tuple]:
    """Yield from llm.stream, turning a total provider failure into a sentinel.

    llm.stream is a generator, so the failure surfaces during iteration; this keeps
    it out of the request handler, where it would become a 500.
    """
    try:
        yield from llm.stream(messages, max_tokens)
    except Exception as e:
        yield None, {"failed": str(e)}


def answer_stream(question: str, mode_id: str = "ask", source_ids: list[str] | None = None, history: list[dict] | None = None) -> Iterator[dict]:
    mode = MODES.get(mode_id, MODES["ask"])
    t_all = time.perf_counter()

    yield {"type": "status", "message": "Rewriting the follow-up as a search query" if history else "Searching"}
    t0 = time.perf_counter()
    rewritten = rewrite(question, history)
    rewrite_ms = round((time.perf_counter() - t0) * 1000, 1) if history else 0.0

    sources = source_ids if source_ids else mode.sources
    quotas = {sid: n for sid, n in mode.quotas.items() if not source_ids or sid in source_ids}
    r = retrieve(question, source_ids=sources, k=mode.k, search_query=rewritten, quotas=quotas)
    r.timings["rewrite_ms"] = rewrite_ms
    yield {"type": "retrieval", "mode": asdict(mode), "retrieval": r.public()}

    if not r.sources:
        msg = "I couldn't find anything relevant in the knowledge base for this question. Check the source filters, or ingest the data first."
        yield {"type": "token", "text": msg}
        yield {"type": "done", "citations": citations(msg, 0), "usage": {}, "timings": {**r.timings, "total_ms": round((time.perf_counter() - t_all) * 1000, 1)}}
        return

    messages = build_messages(question, mode, r, history)
    t0 = time.perf_counter()
    first_token_ms = None
    waited_s = 0.0  # time queued behind the provider's rate limit is not model latency
    answer, usage, failed = "", {}, None
    for delta, u in _safe_stream(messages, mode.max_tokens):
        if u and u.get("failed"):  # every model failed: degrade, do not raise
            failed = u["failed"]
            break
        if delta is None:  # rate limited: tell the user, the client is already waiting
            waited_s += u["rate_limited_s"]
            yield {"type": "status", "message": f"The LLM provider rate-limited this request; retrying in {u['rate_limited_s']}s"}
            continue
        if delta:
            if first_token_ms is None:
                first_token_ms = round((time.perf_counter() - t0 - waited_s) * 1000, 1)
            answer += delta
            yield {"type": "token", "text": delta}
        if u is not None:
            usage = u
    if failed and not answer:
        note = f"The answer model is unavailable right now ({failed[:180]}). Nothing was written — the retrieved sources are below."
        yield {"type": "token", "text": note}
        yield {"type": "error", "message": note}
        yield {"type": "done", "answer": note, "citations": citations(note, len(r.sources)), "usage": {},
               "timings": {**r.timings, "total_ms": round((time.perf_counter() - t_all) * 1000, 1)}}
        return
    timings = {
        **r.timings,
        "llm_first_token_ms": first_token_ms,
        "llm_ms": round((time.perf_counter() - t0 - waited_s) * 1000, 1),
        "rate_limit_wait_ms": round(waited_s * 1000, 1) if waited_s else None,
        "total_ms": round((time.perf_counter() - t_all) * 1000, 1),
    }
    source_texts = [c.payload.get("text", "") for c in r.sources]
    final = normalize_citations(answer)
    cites = citations(final, len(r.sources), source_texts)
    # The model forgot the [n] markers but the answer is still faithful to the
    # sources: one small call adds them, so a formatting miss never costs a citation.
    if not cites["markers"] and not cites["said_not_found"] and cites["attributed"] and "```" not in final:
        t1 = time.perf_counter()
        fixed = repair_citations(final, r)
        if fixed:
            timings["citation_repair_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            final, cites = fixed, citations(fixed, len(r.sources), source_texts)
    yield {"type": "done", "answer": final, "citations": cites, "usage": usage,
           "timings": timings}


def answer(question: str, mode_id: str = "ask", source_ids: list[str] | None = None, history: list[dict] | None = None) -> dict:
    """Non-streaming convenience wrapper (CLI, eval, demo export)."""
    out: dict = {"answer": ""}
    for evt in answer_stream(question, mode_id, source_ids, history):
        if evt["type"] == "retrieval":
            out["retrieval"] = evt["retrieval"]
            out["mode"] = evt["mode"]
        elif evt["type"] == "token":
            out["answer"] += evt["text"]
        elif evt["type"] == "done":
            out.update({k: v for k, v in evt.items() if k != "type"})
    return out
