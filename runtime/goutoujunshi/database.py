from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .enrichment import (
    ENRICHMENT_PROMPT_VERSION,
    enrichment_json,
    enrichment_text,
    normalize_search_enrichment,
)


CHANNELS = ("微信", "抖音", "朋友圈", "线下", "其他")
EVENT_TYPES = {"received", "sent", "draft", "background", "analysis", "correction"}
AUTHOR_ROLES = {"user", "other", "assistant", "system", "unknown"}


def _pymysql():
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required; run the project setup script") from exc
    return pymysql


def connection_settings() -> dict[str, Any]:
    password = os.environ.get("GOUTOUJUNSHI_DB_PASSWORD", "")
    if not password:
        raise RuntimeError("GOUTOUJUNSHI_DB_PASSWORD is not configured")
    return {
        "host": os.environ.get("GOUTOUJUNSHI_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("GOUTOUJUNSHI_DB_PORT", "3306")),
        "user": os.environ.get("GOUTOUJUNSHI_DB_USER", "goutoujunshi_app"),
        "password": password,
        "database": os.environ.get("GOUTOUJUNSHI_DB_NAME", "goutoujunshi"),
        "charset": "utf8mb4",
        "cursorclass": _pymysql().cursors.DictCursor,
        "autocommit": False,
        "connect_timeout": 5,
        "read_timeout": 15,
        "write_timeout": 15,
    }


def connect():
    return _pymysql().connect(**connection_settings())


@contextlib.contextmanager
def transaction() -> Iterator[Any]:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema() -> None:
    sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    statements = [item.strip() for item in sql.split(";") if item.strip()]
    with transaction() as cursor:
        for statement in statements:
            cursor.execute(statement)


def slugify(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\s]+", "-", value.strip())
    return value.strip("-.")[:160] or "relationship"


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def event_dedupe_key(
    relationship_id: int,
    event_type: str,
    channel: str,
    content: str,
    occurred_at: datetime,
    external_message_id: str | None,
) -> str:
    if external_message_id:
        raw = f"external\x1f{external_message_id}"
    else:
        raw = "\x1f".join(
            (str(relationship_id), event_type, channel, occurred_at.isoformat(timespec="microseconds"), content)
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_or_create_channel(cursor: Any, relationship_id: int, channel: str) -> int:
    channel = channel if channel in CHANNELS else "其他"
    cursor.execute(
        "INSERT IGNORE INTO source_channels(relationship_id, kind, label) VALUES (%s, %s, %s)",
        (relationship_id, channel, channel),
    )
    cursor.execute(
        "SELECT id FROM source_channels WHERE relationship_id=%s AND kind=%s",
        (relationship_id, channel),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("failed to resolve source channel")
    return int(row["id"])


def append_event_with_status(
    cursor: Any,
    *,
    relationship_id: int,
    event_type: str,
    author_role: str,
    content: str,
    channel: str,
    evidence_kind: str,
    occurred_at: datetime | None = None,
    external_message_id: str | None = None,
    supersedes_event_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    search_enrichment: dict[str, Any] | None = None,
) -> tuple[int, bool]:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type}")
    if author_role not in AUTHOR_ROLES:
        raise ValueError(f"unsupported author_role: {author_role}")
    clean_content = content.strip()
    if not clean_content:
        raise ValueError("event content cannot be empty")
    occurred_at = occurred_at or datetime.now()
    channel_id = get_or_create_channel(cursor, relationship_id, channel)
    dedupe = event_dedupe_key(
        relationship_id, event_type, channel, clean_content, occurred_at, external_message_id
    )
    cursor.execute(
        """
        INSERT IGNORE INTO relationship_events(
            relationship_id, source_channel_id, event_type, author_role, content,
            evidence_kind, external_message_id, dedupe_key, supersedes_event_id,
            metadata, occurred_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            relationship_id,
            channel_id,
            event_type,
            author_role,
            clean_content,
            evidence_kind,
            external_message_id,
            dedupe,
            supersedes_event_id,
            json_text(metadata or {}),
            occurred_at,
        ),
    )
    created = bool(cursor.rowcount)
    cursor.execute(
        "SELECT id,content,event_type FROM relationship_events WHERE relationship_id=%s AND dedupe_key=%s",
        (relationship_id, dedupe),
    )
    authority = cursor.fetchone()
    event_id = int(authority["id"])
    authority_content = str(authority["content"])
    authority_event_type = str(authority["event_type"])
    if authority_event_type != "draft":
        upsert_event_search_document(
            cursor,
            event_id=event_id,
            relationship_id=relationship_id,
            source_text=authority_content,
            search_enrichment=(
                search_enrichment
                if authority_content == clean_content and authority_event_type == event_type
                else None
            ),
        )
    return event_id, created


def upsert_event_search_document(
    cursor: Any,
    *,
    event_id: int,
    relationship_id: int,
    source_text: str,
    search_enrichment: dict[str, Any] | None,
    replace_enrichment: bool = False,
    enrichment_version: str = ENRICHMENT_PROMPT_VERSION,
) -> str:
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    normalized = normalize_search_enrichment(search_enrichment)
    status = "enriched" if normalized else "raw_only"
    serialized = enrichment_json(normalized) if normalized else "{}"
    flattened = enrichment_text(normalized) if normalized else ""
    cursor.execute(
        """
        INSERT INTO relationship_event_search_documents(
            event_id,relationship_id,source_text,source_sha256,enrichment_json,
            enrichment_text,enrichment_source,enrichment_version,status
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            source_text=VALUES(source_text),
            source_sha256=VALUES(source_sha256),
            enrichment_json=IF((status='raw_only' OR %s) AND VALUES(status)='enriched',
                VALUES(enrichment_json),enrichment_json),
            enrichment_text=IF((status='raw_only' OR %s) AND VALUES(status)='enriched',
                VALUES(enrichment_text),enrichment_text),
            enrichment_source=IF((status='raw_only' OR %s) AND VALUES(status)='enriched',
                VALUES(enrichment_source),enrichment_source),
            enrichment_version=IF((status='raw_only' OR %s) AND VALUES(status)='enriched',
                VALUES(enrichment_version),enrichment_version),
            status=IF((status='raw_only' OR %s) AND VALUES(status)='enriched','enriched',status)
        """,
        (
            event_id,
            relationship_id,
            source_text,
            source_sha256,
            serialized,
            flattened,
            "model" if normalized else "none",
            enrichment_version if normalized else "",
            status,
            replace_enrichment,
            replace_enrichment,
            replace_enrichment,
            replace_enrichment,
            replace_enrichment,
        ),
    )
    cursor.execute(
        "SELECT status,enrichment_version FROM relationship_event_search_documents WHERE event_id=%s",
        (event_id,),
    )
    effective_document = cursor.fetchone()
    effective_status = str(effective_document["status"])
    effective_version = str(effective_document.get("enrichment_version") or enrichment_version)
    if effective_status == "enriched":
        cursor.execute(
            """
            INSERT INTO relationship_event_enrichment_jobs(
                event_id,relationship_id,source_sha256,prompt_version,status,completed_at
            ) VALUES (%s,%s,%s,%s,'done',CURRENT_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                relationship_id=VALUES(relationship_id),
                source_sha256=VALUES(source_sha256),
                prompt_version=IF(status='running',prompt_version,VALUES(prompt_version)),
                completed_at=IF(status='running',completed_at,VALUES(completed_at)),
                started_at=IF(status='running',started_at,NULL),
                last_error_code=IF(status='running',last_error_code,NULL),
                last_error=IF(status='running',last_error,NULL),
                status=IF(status='running',status,'done')
            """,
            (event_id, relationship_id, source_sha256, effective_version),
        )
    else:
        cursor.execute(
            """
            INSERT INTO relationship_event_enrichment_jobs(
                event_id,relationship_id,source_sha256,prompt_version,status
            ) VALUES (%s,%s,%s,%s,'pending')
            ON DUPLICATE KEY UPDATE
                relationship_id=VALUES(relationship_id),
                source_sha256=VALUES(source_sha256),
                prompt_version=IF(status='running',prompt_version,VALUES(prompt_version)),
                completed_at=IF(status='done',NULL,completed_at),
                status=IF(status='done','pending',status)
            """,
            (event_id, relationship_id, source_sha256, effective_version),
        )
    return effective_status


def append_event(
    cursor: Any,
    *,
    relationship_id: int,
    event_type: str,
    author_role: str,
    content: str,
    channel: str,
    evidence_kind: str,
    occurred_at: datetime | None = None,
    external_message_id: str | None = None,
    supersedes_event_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    search_enrichment: dict[str, Any] | None = None,
) -> int:
    event_id, _ = append_event_with_status(
        cursor,
        relationship_id=relationship_id,
        event_type=event_type,
        author_role=author_role,
        content=content,
        channel=channel,
        evidence_kind=evidence_kind,
        occurred_at=occurred_at,
        external_message_id=external_message_id,
        supersedes_event_id=supersedes_event_id,
        metadata=metadata,
        search_enrichment=search_enrichment,
    )
    return event_id


def queue_export(cursor: Any, relationship_id: int) -> None:
    cursor.execute(
        """
        INSERT INTO export_jobs(relationship_id, status)
        SELECT %s, 'pending'
        WHERE NOT EXISTS (
            SELECT 1 FROM export_jobs
            WHERE relationship_id=%s AND status='pending'
        )
        """,
        (relationship_id, relationship_id),
    )


def queue_reconcile(cursor: Any, chat_id: str | None = None) -> None:
    cursor.execute(
        "INSERT INTO control_requests(request_kind, payload) VALUES ('reconcile_routes', %s)",
        (json_text({"chat_id": chat_id} if chat_id else {}),),
    )
