from __future__ import annotations

import json
from typing import Any


ENRICHMENT_PROMPT_VERSION = "mysql-enrichment-v1"
SUMMARY_MAX_CHARS = 240
LIST_LIMITS = {
    "concepts": (8, 40),
    "aliases": (8, 60),
    "entities": (8, 60),
    "time_hints": (6, 60),
}


def normalize_search_enrichment(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if set(value) != {"summary", *LIST_LIMITS}:
        return None
    raw_summary = value.get("summary")
    if not isinstance(raw_summary, str):
        return None
    summary = raw_summary.strip()
    if not summary or len(summary) > SUMMARY_MAX_CHARS:
        return None
    result: dict[str, Any] = {"summary": summary}
    for key, (maximum_items, maximum_chars) in LIST_LIMITS.items():
        raw_items = value.get(key)
        if not isinstance(raw_items, list):
            return None
        if len(raw_items) > maximum_items:
            return None
        items: list[str] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, str):
                return None
            item = raw_item.strip()
            if not item or len(item) > maximum_chars:
                return None
            if item not in items:
                items.append(item)
        result[key] = items
    return result


def enrichment_text(value: dict[str, Any]) -> str:
    parts = [str(value["summary"])]
    for key in LIST_LIMITS:
        parts.extend(str(item) for item in value.get(key) or [])
    return "\n".join(dict.fromkeys(part for part in parts if part))


def enrichment_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def enrichment_tool_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "summary": {"type": "string", "minLength": 1, "maxLength": SUMMARY_MAX_CHARS}
    }
    for key, (maximum_items, maximum_chars) in LIST_LIMITS.items():
        properties[key] = {
            "type": "array",
            "maxItems": maximum_items,
            "items": {"type": "string", "minLength": 1, "maxLength": maximum_chars},
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["summary", *LIST_LIMITS],
        "additionalProperties": False,
    }
