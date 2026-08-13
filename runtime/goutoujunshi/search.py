from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from typing import Any

from . import repository


_WORD = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+", re.IGNORECASE)
RRF_K = 60
BRANCH_WEIGHTS = {
    "exact": 1.5,
    "source_fulltext": 1.0,
    "enrichment_fulltext": 1.25,
}
MAX_BRANCH_CANDIDATES = 40
MAX_CONTENT_CHARS = 1200
MAX_TOTAL_CONTENT_CHARS = 6000


def normalize_text(value: str) -> str:
    return "".join(
        char.lower()
        for char in unicodedata.normalize("NFKC", value)
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def keyword_terms(query: str, *, maximum: int = 16) -> list[str]:
    original_query = query.strip()
    normalized_query = unicodedata.normalize("NFKC", original_query)
    terms: list[str] = [original_query]
    for token in _WORD.findall(normalized_query):
        if len(token) >= 2:
            terms.append(token)
        if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) >= 2:
            terms.extend(token[index : index + 2] for index in range(len(token) - 1))
    return list(dict.fromkeys(term for term in terms if term))[:maximum]


def keyword_score(content: str, query: str, terms: list[str]) -> float:
    normalized_content = normalize_text(content)
    normalized_query = normalize_text(query)
    score = 0.0
    if normalized_query and normalized_query in normalized_content:
        score += 12.0
    matched = 0
    for term in terms[1:]:
        normalized_term = normalize_text(term)
        if normalized_term and normalized_term in normalized_content:
            matched += 1
            score += min(len(normalized_term), 6) / 2
    if terms[1:]:
        score += 4.0 * matched / len(terms[1:])
    return score


def reciprocal_rank_fusion(
    branches: dict[str, list[int]],
    *,
    k: int = RRF_K,
    weights: dict[str, float] | None = None,
) -> dict[int, dict[str, Any]]:
    active_weights = weights or BRANCH_WEIGHTS
    scores: dict[int, dict[str, Any]] = {}
    for source, ids in branches.items():
        weight = float(active_weights[source])
        for rank, event_id in enumerate(ids, start=1):
            item = scores.setdefault(event_id, {"rrf_score": 0.0, "sources": []})
            item["rrf_score"] += weight / (k + rank)
            item["sources"].append(source)
    return scores


def _evidence_priority(event: dict[str, Any]) -> int:
    if event.get("event_type") == "correction":
        return 0
    evidence = str(event.get("evidence_kind") or "")
    if evidence.startswith("explicit_user"):
        return 1
    return 2


def _timestamp(event: dict[str, Any]) -> str:
    value = event.get("occurred_at")
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")


def _public_event(
    event: dict[str, Any],
    scoring: dict[str, Any],
    *,
    content_budget: int,
) -> dict[str, Any] | None:
    if content_budget <= 0:
        return None
    content = str(event.get("content") or "")
    keep = min(MAX_CONTENT_CHARS, content_budget)
    truncated = len(content) > keep
    return {
        "id": int(event["id"]),
        "content": content[:keep],
        "channel": event.get("channel"),
        "event_type": event.get("event_type"),
        "author_role": event.get("author_role"),
        "evidence_kind": event.get("evidence_kind"),
        "occurred_at": event.get("occurred_at"),
        "supersedes_event_id": event.get("supersedes_event_id"),
        "retrieval_sources": list(scoring.get("sources") or []),
        "retrieval_score": round(float(scoring.get("rrf_score") or 0.0), 8),
        "content_truncated": truncated,
    }


def _rank_exact(
    events: list[dict[str, Any]], query: str, terms: list[str]
) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            keyword_score(str(event.get("content") or ""), query, terms),
            _timestamp(event),
            int(event["id"]),
        ),
        reverse=True,
    )[:MAX_BRANCH_CANDIDATES]


def search_relationship_events(
    binding: dict[str, Any],
    *,
    query: str,
    channel: str | None,
    limit: int = 8,
    include_drafts: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    query = query.strip()
    if not query:
        raise ValueError("query cannot be empty")
    if len(query) > 500:
        raise ValueError("query cannot exceed 500 characters")
    if channel is not None and channel not in repository.CHANNELS:
        raise ValueError("unsupported channel")
    if include_drafts and not channel:
        raise ValueError("include_drafts=true requires an explicit channel")
    limit = min(max(int(limit), 1), 20)
    terms = keyword_terms(query)

    exact = _rank_exact(
        repository.exact_event_candidates(
            binding,
            terms,
            channel,
            include_drafts=include_drafts,
            limit=MAX_BRANCH_CANDIDATES,
        ),
        query,
        terms,
    )
    source_fulltext = repository.fulltext_event_candidates(
        binding, query, channel, field="source", limit=MAX_BRANCH_CANDIDATES
    )
    enrichment_fulltext = repository.fulltext_event_candidates(
        binding, query, channel, field="enrichment", limit=MAX_BRANCH_CANDIDATES
    )
    branches = {
        "exact": [int(event["id"]) for event in exact],
        "source_fulltext": [int(event["id"]) for event in source_fulltext],
        "enrichment_fulltext": [int(event["id"]) for event in enrichment_fulltext],
    }
    rrf = reciprocal_rank_fusion(branches)
    hydrated = repository.hydrate_search_events(
        binding,
        list(rrf),
        channel,
        include_drafts=include_drafts,
    )
    missing_candidates = len(set(rrf) - set(hydrated))
    exact_draft_ids = {
        int(event["id"])
        for event in exact
        if include_drafts
        and event.get("event_type") == "draft"
        and normalize_text(query) in normalize_text(str(event.get("content") or ""))
    }
    ranked_ids = sorted(
        hydrated,
        key=lambda event_id: (
            event_id in exact_draft_ids,
            float(rrf[event_id]["rrf_score"]),
            -_evidence_priority(hydrated[event_id]),
            _timestamp(hydrated[event_id]),
            event_id,
        ),
        reverse=True,
    )

    corrections: list[dict[str, Any]] = []
    correction_seen: set[int] = set()
    frontier = list(ranked_ids)
    for _ in range(8):
        layer = repository.corrections_for_events(binding, frontier, channel)
        new_layer = [item for item in layer if int(item["id"]) not in correction_seen]
        if not new_layer:
            break
        corrections.extend(new_layer)
        correction_seen.update(int(item["id"]) for item in new_layer)
        frontier = [int(item["id"]) for item in new_layer]
    corrections_by_target: dict[int, list[dict[str, Any]]] = {}
    for correction in corrections:
        target = int(correction["supersedes_event_id"])
        corrections_by_target.setdefault(target, []).append(correction)
    expanded: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[int] = set()

    def append_corrections(target_id: int, base_score: float, depth: int = 0) -> None:
        if depth >= 8:
            return
        for correction in corrections_by_target.get(target_id, []):
            correction_id = int(correction["id"])
            append_corrections(correction_id, base_score, depth + 1)
            if correction_id not in seen:
                expanded.append(
                    (
                        correction,
                        {
                            "rrf_score": base_score + 1.0 + (8 - depth) / 100,
                            "sources": ["correction_closure"],
                        },
                    )
                )
                seen.add(correction_id)

    for event_id in ranked_ids:
        append_corrections(event_id, float(rrf[event_id]["rrf_score"]))
        if event_id not in seen:
            expanded.append((hydrated[event_id], rrf[event_id]))
            seen.add(event_id)

    events: list[dict[str, Any]] = []
    remaining_chars = MAX_TOTAL_CONTENT_CHARS
    for event, scoring in expanded:
        if len(events) >= limit or remaining_chars <= 0:
            break
        public = _public_event(event, scoring, content_budget=remaining_chars)
        if public is None:
            break
        events.append(public)
        remaining_chars -= len(public["content"])

    coverage = repository.enrichment_status(int(binding["id"]))
    authority_count = coverage["authority_count"]
    enriched_count = coverage["enriched_count"]
    effective_mode = "mysql_enriched" if enriched_count else "mysql_raw"
    incomplete = (
        coverage["document_count"] != authority_count
        or enriched_count != authority_count
        or coverage["pending_count"] > 0
        or coverage["running_count"] > 0
        or coverage["failed_count"] > 0
    )
    degradation_reason = "incomplete_enrichment" if incomplete else None
    elapsed_ms = (time.monotonic() - started) * 1000
    return {
        "events": events,
        "retrieval": {
            "requested_mode": "mysql_enriched",
            "effective_mode": effective_mode,
            "degraded": incomplete,
            "degradation_reason": degradation_reason,
            "channel_scope": [channel] if channel else "all",
            "include_drafts": include_drafts,
            "candidate_counts": {
                "exact": len(branches["exact"]),
                "source_fulltext": len(branches["source_fulltext"]),
                "enrichment_fulltext": len(branches["enrichment_fulltext"]),
                "authority_dropped": missing_candidates,
                "fused": len(expanded),
            },
            "enrichment_coverage": coverage,
            "has_more": len(expanded) > len(events),
            "result_content_chars": MAX_TOTAL_CONTENT_CHARS - remaining_chars,
            "elapsed_ms": round(elapsed_ms, 2),
        },
    }
