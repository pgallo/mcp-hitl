"""Search ranking and situation assessment. Pure functions over signal dicts.

Formulas are mirrored operation-for-operation in typescript/src/scoring.ts. Changing
one side without the other is exactly what the conformance suite is there to catch.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .canonical import round4

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _unique(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def match_score(signal: dict[str, Any], query_tokens: list[str]) -> int:
    title = set(tokens(signal["title"]))
    tags = set(tokens(" ".join(signal["tags"])))
    summary = set(tokens(signal["summary"]))
    score = 0
    for tok in query_tokens:
        if tok in title:
            score += 3
        if tok in tags:
            score += 2
        if tok in summary:
            score += 1
    return score


def search(
    signals: list[dict[str, Any]],
    query: str,
    kinds: list[str] | None,
    site: str | None,
    min_severity: int | None,
    since: str | None,
    limit: int,
) -> tuple[int, list[dict[str, Any]]]:
    query_tokens = _unique(tokens(query))
    hits: list[dict[str, Any]] = []
    for s in signals:
        if kinds is not None and s["kind"] not in kinds:
            continue
        if site is not None and s["site"] != site:
            continue
        if min_severity is not None and s["severity"] < min_severity:
            continue
        if since is not None and s["observed_at"] < since:
            continue
        score = match_score(s, query_tokens) if query_tokens else 0
        if query_tokens and score == 0:
            continue
        hits.append(
            {
                "id": s["id"],
                "title": s["title"],
                "kind": s["kind"],
                "severity": s["severity"],
                "site": s["site"],
                "observed_at": s["observed_at"],
                "score": score,
            }
        )
    hits.sort(key=lambda h: (-h["score"], -h["severity"], h["id"]))
    return len(hits), hits[:limit]


def _related(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["site"] == b["site"]:
        return True
    if a["lane"] is not None and a["lane"] == b["lane"]:
        return True
    return bool(set(a["tags"]) & set(b["tags"]))


SINGLE_SOURCE_CAP = 0.6


def band_for(score: float) -> str:
    if score < 0.35:
        return "low"
    if score < 0.55:
        return "medium"
    if score < 0.75:
        return "high"
    return "critical"


def assess_signals(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Impact score and heuristic confidence for a group of signals.

    Score answers "how bad if true", confidence answers "how much should we believe it".
    Keeping them separate is what lets the policy refuse a severe but uncorroborated alarm.
    """
    n = len(group)
    severities = [s["severity"] for s in group]
    max_sev = max(severities)
    sum_sev = sum(severities)
    sum_rel = sum(s["source_reliability"] for s in group)
    sources = sorted({s["source"] for s in group})
    distinct_sources = len(sources)

    pairs = n * (n - 1) // 2
    related = 0
    for i in range(n):
        for j in range(i + 1, n):
            if _related(group[i], group[j]):
                related += 1
    coherence = 1.0 if n == 1 else related / pairs

    mean_sev = sum_sev / n
    mean_rel = sum_rel / n
    score_raw = 0.5 * (max_sev / 5) + 0.3 * (mean_sev / 5) + 0.2 * (min(n, 5) / 5)
    conf_raw = 0.5 * (mean_rel / 100) + 0.3 * (min(distinct_sources, 3) / 3) + 0.2 * coherence
    capped = distinct_sources == 1 and conf_raw > SINGLE_SOURCE_CAP
    if capped:
        conf_raw = SINGLE_SOURCE_CAP

    score = round4(score_raw)
    confidence = round4(conf_raw)
    rationale = [
        f"Severity peaks at {max_sev}/5 across {n} signal(s)",
        f"Sources: {distinct_sources} distinct ({', '.join(sources)}), mean reliability {math.floor(mean_rel + 0.5)}/100",
        "Coherence: single signal" if n == 1 else f"Coherence: {related}/{pairs} signal pairs share a site, lane or tag",
    ]
    if capped:
        rationale.append("Single-source cap: confidence limited to 0.6 until corroborated")
    if mean_rel < 60:
        rationale.append("Low source reliability: treat as unverified")

    return {
        "score": score,
        "confidence": confidence,
        "band": band_for(score),
        "factors": {
            "max_severity": max_sev,
            "mean_severity": round4(mean_sev),
            "distinct_sources": distinct_sources,
            "mean_reliability": round4(mean_rel),
            "coherence": round4(coherence),
        },
        "rationale": rationale,
    }
