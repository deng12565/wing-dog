from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Any

from .database import transaction, upsert_event_search_document
from .enrichment import (
    ENRICHMENT_PROMPT_VERSION,
    enrichment_tool_schema,
    normalize_search_enrichment,
)


MAX_BATCH_ITEMS = 8
MAX_BATCH_INPUT_CHARS = 12000
MAX_ATTEMPTS = 5
CODEX_USER_AGENT = "codex_cli_rs/0.0.0"


def _validated_prompt_version(value: str) -> str:
    version = str(value or "").strip()
    if not version or len(version) > 64:
        raise ValueError("invalid enrichment prompt version")
    return version


def queue_enrichment_backfill(prompt_version: str = ENRICHMENT_PROMPT_VERSION) -> dict[str, int]:
    version = _validated_prompt_version(prompt_version)
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS count FROM relationship_event_enrichment_jobs
            WHERE status='running' AND prompt_version <> %s
            """,
            (version,),
        )
        if int(cursor.fetchone()["count"] or 0):
            raise RuntimeError("a different enrichment prompt version is still running")
        cursor.execute(
            """
            INSERT IGNORE INTO relationship_event_search_documents(
                event_id,relationship_id,source_text,source_sha256,enrichment_json,
                enrichment_text,enrichment_source,enrichment_version,status
            )
            SELECT id,relationship_id,content,SHA2(content,256),JSON_OBJECT(),'',
                'none','','raw_only'
            FROM relationship_events
            WHERE event_type <> 'draft'
            """
        )
        cursor.execute(
            """
            INSERT IGNORE INTO relationship_event_enrichment_jobs(
                event_id,relationship_id,source_sha256,prompt_version,status
            )
            SELECT e.id,e.relationship_id,SHA2(e.content,256),%s,'pending'
            FROM relationship_events e
            WHERE e.event_type <> 'draft'
            """,
            (version,),
        )
        cursor.execute(
            """
            UPDATE relationship_event_enrichment_jobs j
            JOIN relationship_events e ON e.id=j.event_id
            SET j.relationship_id=e.relationship_id,j.source_sha256=SHA2(e.content,256),
                j.prompt_version=%s,j.status='pending',j.attempts=0,
                j.started_at=NULL,j.completed_at=NULL,
                j.last_error_code=NULL,j.last_error=NULL
            WHERE e.event_type <> 'draft' AND j.prompt_version <> %s
              AND j.status <> 'running'
            """,
            (version, version),
        )
        cursor.execute(
            """
            UPDATE relationship_event_enrichment_jobs j
            JOIN relationship_event_search_documents d ON d.event_id=j.event_id
            SET j.status='done',j.completed_at=COALESCE(j.completed_at,CURRENT_TIMESTAMP(6)),
                j.started_at=NULL,j.last_error_code=NULL,j.last_error=NULL
            WHERE j.prompt_version=%s AND j.status <> 'running'
              AND d.status='enriched' AND d.enrichment_version=%s
            """,
            (version, version),
        )
        cursor.execute(
            """
            UPDATE relationship_event_enrichment_jobs j
            JOIN relationship_event_search_documents d ON d.event_id=j.event_id
            SET j.status='pending',j.attempts=0,j.started_at=NULL,j.completed_at=NULL,
                j.last_error_code=NULL,j.last_error=NULL
            WHERE j.prompt_version=%s AND j.status='done'
              AND (d.status='raw_only' OR d.enrichment_version <> %s)
            """,
            (version, version),
        )
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(status='pending'),0) AS pending,
                COALESCE(SUM(status='running'),0) AS running,
                COALESCE(SUM(status='done'),0) AS done,
                COALESCE(SUM(status='failed'),0) AS failed
            FROM relationship_event_enrichment_jobs
            WHERE prompt_version=%s
            """,
            (version,),
        )
        counts = cursor.fetchone()
    return {key: int(value or 0) for key, value in counts.items()}


def recover_stale_enrichment_jobs(*, stale_minutes: int = 15) -> int:
    cutoff = datetime.now() - timedelta(minutes=max(stale_minutes, 1))
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE relationship_event_enrichment_jobs
            SET status='failed',last_error_code='stale_claim',
                last_error='stale running claim recovered',started_at=NULL
            WHERE status='running' AND started_at < %s
            """,
            (cutoff,),
        )
        return int(cursor.rowcount)


def claim_enrichment_jobs(
    limit: int = MAX_BATCH_ITEMS,
    *,
    prompt_version: str = ENRICHMENT_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    prompt_version = _validated_prompt_version(prompt_version)
    bounded_limit = min(max(int(limit), 1), MAX_BATCH_ITEMS)
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT j.id AS job_id,j.event_id,j.relationship_id,j.source_sha256,
                e.event_type,e.author_role,e.content,e.occurred_at,c.kind AS channel
            FROM relationship_event_enrichment_jobs j
            JOIN relationship_events e ON e.id=j.event_id AND e.relationship_id=j.relationship_id
            LEFT JOIN source_channels c ON c.id=e.source_channel_id
            WHERE j.prompt_version=%s AND j.status IN ('pending','failed')
              AND j.attempts < %s AND e.event_type <> 'draft'
            ORDER BY j.id
            LIMIT %s FOR UPDATE SKIP LOCKED
            """,
            (prompt_version, MAX_ATTEMPTS, bounded_limit),
        )
        rows = _select_input_batch([dict(row) for row in cursor.fetchall()])
        if rows:
            placeholders = ",".join(["%s"] * len(rows))
            cursor.execute(
                f"""
                UPDATE relationship_event_enrichment_jobs
                SET status='running',attempts=attempts+1,started_at=CURRENT_TIMESTAMP(6),
                    completed_at=NULL,last_error_code=NULL,last_error=NULL
                WHERE id IN ({placeholders})
                """,
                tuple(int(row["job_id"]) for row in rows),
            )
    return rows


def _bounded_event_view(row: dict[str, Any], maximum_chars: int) -> dict[str, Any]:
    content = str(row.get("content") or "")
    content_truncated = len(content) > maximum_chars
    if len(content) > maximum_chars:
        half = max(maximum_chars // 2, 1)
        content_segments = [content[:half], content[-half:]]
    else:
        content_segments = [content]
    occurred_at = row.get("occurred_at")
    return {
        "event_id": int(row["event_id"]),
        "event_type": str(row.get("event_type") or ""),
        "author_role": str(row.get("author_role") or ""),
        "channel": str(row.get("channel") or ""),
        "occurred_at": occurred_at.isoformat() if isinstance(occurred_at, datetime) else str(occurred_at or ""),
        "content_segments": content_segments,
        "content_truncated": content_truncated,
    }


def _select_input_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows[:MAX_BATCH_ITEMS]:
        candidate = [*selected, row]
        full_payload = json.dumps(
            [
                _bounded_event_view(item, max(len(str(item.get("content") or "")), 1))
                for item in candidate
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if selected and len(full_payload) > MAX_BATCH_INPUT_CHARS:
            break
        selected.append(row)
    return selected


def build_enrichment_input(rows: list[dict[str, Any]]) -> str:
    selected = rows[:MAX_BATCH_ITEMS]
    if not selected:
        raise ValueError("no enrichment event fits the input budget")
    full_payload = json.dumps(
        [
            _bounded_event_view(row, max(len(str(row.get("content") or "")), 1))
            for row in selected
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(full_payload) <= MAX_BATCH_INPUT_CHARS:
        return full_payload
    content_limit = max(256, (MAX_BATCH_INPUT_CHARS - 320 * len(selected)) // len(selected))
    while content_limit >= 64:
        payload = json.dumps(
            [_bounded_event_view(row, content_limit) for row in selected],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(payload) <= MAX_BATCH_INPUT_CHARS:
            return payload
        overflow_per_item = (len(payload) - MAX_BATCH_INPUT_CHARS + len(selected) - 1) // len(selected)
        content_limit -= max(overflow_per_item, 32)
    raise RuntimeError("enrichment input metadata exceeds the batch budget")


def enrichment_job_status(prompt_version: str = ENRICHMENT_PROMPT_VERSION) -> dict[str, int]:
    prompt_version = _validated_prompt_version(prompt_version)
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS total,
                COALESCE(SUM(status='pending'),0) AS pending,
                COALESCE(SUM(status='running'),0) AS running,
                COALESCE(SUM(status='done'),0) AS done,
                COALESCE(SUM(status='failed'),0) AS failed
            FROM relationship_event_enrichment_jobs
            WHERE prompt_version=%s
            """,
            (prompt_version,),
        )
        row = cursor.fetchone()
    return {key: int(value or 0) for key, value in row.items()}


def retry_failed_enrichment_jobs(prompt_version: str = ENRICHMENT_PROMPT_VERSION) -> int:
    prompt_version = _validated_prompt_version(prompt_version)
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE relationship_event_enrichment_jobs j
            JOIN relationship_events e ON e.id=j.event_id AND e.relationship_id=j.relationship_id
            SET j.source_sha256=SHA2(e.content,256),j.status='pending',j.attempts=0,
                j.started_at=NULL,j.completed_at=NULL,j.last_error_code=NULL,j.last_error=NULL
            WHERE j.prompt_version=%s AND j.status='failed' AND e.event_type <> 'draft'
            """,
            (prompt_version,),
        )
        return int(cursor.rowcount)


def _request_enrichments(rows: list[dict[str, Any]], settings: dict[str, str]) -> list[dict[str, Any]]:
    from openai import OpenAI

    input_json = build_enrichment_input(rows)
    item_schema = {
        "type": "object",
        "properties": {
            "event_id": {"type": "integer"},
            "search_enrichment": enrichment_tool_schema(),
        },
        "required": ["event_id", "search_enrichment"],
        "additionalProperties": False,
    }
    client = OpenAI(
        api_key=settings["api_key"],
        base_url=settings["base_url"].rstrip("/"),
        default_headers={"User-Agent": CODEX_USER_AGENT},
        max_retries=1,
        timeout=180,
    )
    response = client.responses.create(
        model=settings["model"],
        reasoning={"effort": settings["reasoning"]},
        instructions=(
            "For every relationship event, extract only retrieval aids supported by that event's content. "
            "When content_truncated is true, use only the supplied start/end segments and do not infer the "
            "omitted middle. "
            "Do not add people, events, causes, attitudes, or conclusions absent from the source. "
            "summary is a short faithful paraphrase; concepts are topics; aliases are alternative ways a "
            "future user may ask for the same meaning; entities and time_hints must appear in or be directly "
            "expressed by the event. Return every event exactly once through the required tool."
        ),
        input=input_json,
        tools=[
            {
                "type": "function",
                "name": "store_event_enrichments",
                "description": "Return bounded retrieval enrichment for each supplied event.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_BATCH_ITEMS,
                            "items": item_schema,
                        }
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ],
        tool_choice={"type": "function", "name": "store_event_enrichments"},
        max_output_tokens=8000,
    )
    payload = response.model_dump()
    for item in payload.get("output") or []:
        if item.get("type") == "function_call" and item.get("name") == "store_event_enrichments":
            arguments = item.get("arguments")
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            return list((parsed or {}).get("items") or [])
    raise RuntimeError("enrichment response did not contain the required function call")


def _mark_failed(rows: list[dict[str, Any]], code: str, detail: str) -> None:
    if not rows:
        return
    safe_code = code[:64]
    safe_detail = detail.replace("\r", " ").replace("\n", " ")[:240]
    with transaction() as cursor:
        placeholders = ",".join(["%s"] * len(rows))
        cursor.execute(
            f"""
            UPDATE relationship_event_enrichment_jobs
            SET status='failed',last_error_code=%s,last_error=%s,started_at=NULL
            WHERE id IN ({placeholders}) AND status='running'
            """,
            (safe_code, safe_detail, *[int(row["job_id"]) for row in rows]),
        )


def _store_results(
    rows: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    prompt_version: str,
) -> tuple[int, int]:
    claimed = {int(row["event_id"]): row for row in rows}
    received: dict[int, dict[str, Any]] = {}
    duplicates: set[int] = set()
    for item in items:
        try:
            event_id = int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        normalized = normalize_search_enrichment(item.get("search_enrichment"))
        if event_id in claimed and normalized:
            if event_id in received:
                duplicates.add(event_id)
            else:
                received[event_id] = normalized
    for event_id in duplicates:
        received.pop(event_id, None)
    done = 0
    failed = 0
    with transaction() as cursor:
        for event_id, row in claimed.items():
            normalized = received.get(event_id)
            cursor.execute(
                """
                SELECT relationship_id,event_type,content
                FROM relationship_events
                WHERE id=%s
                FOR UPDATE
                """,
                (event_id,),
            )
            authority = cursor.fetchone()
            authority_content = str(authority.get("content") or "") if authority else ""
            authority_hash = hashlib.sha256(authority_content.encode("utf-8")).hexdigest()
            authority_matches = bool(
                authority
                and int(authority["relationship_id"]) == int(row["relationship_id"])
                and str(authority["event_type"]) != "draft"
                and authority_hash == str(row["source_sha256"])
            )
            if normalized and authority_matches:
                upsert_event_search_document(
                    cursor,
                    event_id=event_id,
                    relationship_id=int(row["relationship_id"]),
                    source_text=authority_content,
                    search_enrichment=normalized,
                    replace_enrichment=True,
                    enrichment_version=prompt_version,
                )
                cursor.execute(
                    """
                    UPDATE relationship_event_enrichment_jobs
                    SET status='done',started_at=NULL,completed_at=CURRENT_TIMESTAMP(6),
                        last_error_code=NULL,last_error=NULL
                    WHERE id=%s AND status='running' AND prompt_version=%s
                    """,
                    (int(row["job_id"]), prompt_version),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("enrichment job state changed before completion")
                done += 1
            else:
                cursor.execute(
                    """
                    UPDATE relationship_event_enrichment_jobs
                    SET status='failed',last_error_code=%s,last_error=%s,started_at=NULL
                    WHERE id=%s AND status='running'
                    """,
                    (
                        "stale_source"
                        if normalized and not authority_matches
                        else "invalid_model_item"
                        if normalized
                        else "missing_model_item",
                        "authority source changed after the enrichment job was claimed"
                        if normalized and not authority_matches
                        else "model output was missing, invalid, or duplicated",
                        int(row["job_id"]),
                    ),
                )
                failed += 1
    return done, failed


def process_enrichment_jobs(
    limit: int = MAX_BATCH_ITEMS,
    *,
    prompt_version: str = ENRICHMENT_PROMPT_VERSION,
    settings: dict[str, str] | None = None,
    requester: Any = None,
) -> dict[str, int]:
    prompt_version = _validated_prompt_version(prompt_version)
    recovered = recover_stale_enrichment_jobs()
    rows = claim_enrichment_jobs(limit, prompt_version=prompt_version)
    if not rows:
        return {"recovered": recovered, "claimed": 0, "done": 0, "failed": 0}
    active_settings = settings or {
        "api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "base_url": os.environ.get("GOUTOUJUNSHI_OPENAI_BASE_URL", "").strip(),
        "model": os.environ.get("GOUTOUJUNSHI_MODEL", "").strip(),
        "reasoning": os.environ.get("GOUTOUJUNSHI_REASONING", "high").strip(),
    }
    if not all(active_settings.values()):
        _mark_failed(rows, "model_config_missing", "remote model configuration is incomplete")
        return {"recovered": recovered, "claimed": len(rows), "done": 0, "failed": len(rows)}
    try:
        items = (requester or _request_enrichments)(rows, active_settings)
        done, failed = _store_results(rows, items, prompt_version=prompt_version)
    except Exception as exc:
        _mark_failed(rows, type(exc).__name__.lower(), "remote enrichment request failed")
        return {"recovered": recovered, "claimed": len(rows), "done": 0, "failed": len(rows)}
    return {"recovered": recovered, "claimed": len(rows), "done": done, "failed": failed}
