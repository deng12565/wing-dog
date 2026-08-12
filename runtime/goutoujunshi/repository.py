from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .database import (
    AUTHOR_ROLES,
    CHANNELS,
    append_event,
    append_event_with_status,
    queue_export,
    queue_reconcile,
    slugify,
    transaction,
)


PROFILE_COLUMNS = (
    "latest_state",
    "known_facts",
    "conservative_judgments",
    "unknowns",
    "response_preferences",
)

USER_MEMORY_CATEGORIES = (
    "identity",
    "work_school",
    "lifestyle",
    "preference",
    "goal",
    "current_context",
)
USER_MEMORY_LIFESPANS = ("persistent", "today", "week")
USER_MEMORY_TIMEZONE = ZoneInfo("Asia/Shanghai")
COMMIT_EVENT_TYPES = {"received", "sent", "background", "analysis", "correction"}
EVENT_ROLE_DEFAULTS = {
    "received": "other",
    "sent": "user",
    "background": "user",
    "analysis": "assistant",
    "correction": "user",
}
_SENSITIVE_MEMORY = re.compile(
    r"(?i)(password|passwd|api[ _-]?key|access[ _-]?token|private[ _-]?key|bearer\s+[a-z0-9._-]+|"
    r"密码|密钥|私钥|验证码|支付密码|银行卡号|身份证号)"
)
_LONG_NUMBER = re.compile(r"(?<!\d)\d{15,19}(?!\d)")
_PRECISE_ADDRESS = re.compile(
    r"(?:详细地址|精确住址|家庭住址|住址\s*[:：])|(?:路|街|巷)\s*\d+\s*号(?:楼|室|单元)?"
)


def health() -> dict[str, Any]:
    with transaction() as cursor:
        cursor.execute("SELECT DATABASE() AS db, 1 AS ok")
        row = cursor.fetchone()
    return {"ok": row["ok"] == 1, "database": row["db"]}


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=USER_MEMORY_TIMEZONE)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def user_memory_expiry(
    lifespan: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    if lifespan not in USER_MEMORY_LIFESPANS:
        raise ValueError("记忆有效期只支持：persistent、today、week")
    if lifespan == "persistent":
        return None
    local_now = now or datetime.now(USER_MEMORY_TIMEZONE)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=USER_MEMORY_TIMEZONE)
    else:
        local_now = local_now.astimezone(USER_MEMORY_TIMEZONE)
    if lifespan == "today":
        boundary = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        days_until_monday = 7 - local_now.weekday()
        boundary = (local_now + timedelta(days=days_until_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return _utc_naive(boundary)


def _clean_user_memory_content(content: str) -> str:
    clean = " ".join(str(content or "").strip().split())
    if not clean:
        raise ValueError("个人记忆内容不能为空")
    if len(clean) > 500:
        raise ValueError("个人记忆单条不能超过 500 个字符")
    if (
        _SENSITIVE_MEMORY.search(clean)
        or _LONG_NUMBER.search(clean)
        or _PRECISE_ADDRESS.search(clean)
        or "-----BEGIN " in clean
    ):
        raise ValueError("拒绝保存密码、密钥、令牌、证件号、支付信息或精确住址")
    return clean


def _memory_dedupe(
    owner_key: str,
    operation: str,
    category: str,
    content: str,
    target_event_id: int | None,
    source_ref: str,
    dedupe_seed: str,
) -> str:
    payload = json.dumps(
        {
            "owner": owner_key,
            "operation": operation,
            "category": category,
            "content": content,
            "target": target_event_id,
            "source_ref": source_ref,
            "seed": dedupe_seed,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _active_user_memory_sql(extra_where: str = "") -> str:
    return f"""
        SELECT e.id,e.owner_key,e.operation,e.category,e.content,e.lifespan,
            e.expires_at,e.evidence_kind,e.source_ref,e.created_at
        FROM user_memory_events e
        WHERE e.owner_key=%s
          AND e.operation IN ('remember','correct')
          AND (e.expires_at IS NULL OR e.expires_at > UTC_TIMESTAMP(6))
          AND NOT EXISTS (
              SELECT 1 FROM user_memory_events next_event
              WHERE next_event.target_event_id=e.id
                AND next_event.operation IN ('correct','forget')
          )
          {extra_where}
    """


def list_user_memory(
    owner_key: str,
    max_chars: int | None = None,
    per_category_limit: int = 8,
) -> list[dict[str, Any]]:
    with transaction() as cursor:
        cursor.execute(
            _active_user_memory_sql() +
            " ORDER BY (e.category='current_context') DESC,e.created_at DESC,e.id DESC",
            (owner_key,),
        )
        rows = cursor.fetchall()
    result: list[dict[str, Any]] = []
    total = 0
    category_counts: dict[str, int] = {}
    for row in rows:
        item = dict(row)
        category = str(item["category"])
        if category_counts.get(category, 0) >= max(1, per_category_limit):
            continue
        size = len(str(item["content"]))
        if max_chars is not None and result and total + size > max_chars:
            continue
        total += size
        result.append(item)
        category_counts[category] = category_counts.get(category, 0) + 1
    return result


def _get_active_user_memory(cursor: Any, owner_key: str, event_id: int) -> dict[str, Any] | None:
    cursor.execute(
        _active_user_memory_sql("AND e.id=%s") + " LIMIT 1 FOR UPDATE",
        (owner_key, event_id),
    )
    return cursor.fetchone()


def _get_user_memory_event(cursor: Any, owner_key: str, event_id: int) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT id,owner_key,operation,category,content,lifespan,expires_at
        FROM user_memory_events
        WHERE owner_key=%s AND id=%s AND operation IN ('remember','correct')
        LIMIT 1 FOR UPDATE
        """,
        (owner_key, event_id),
    )
    return cursor.fetchone()


def remember_user_memory(
    owner_key: str,
    *,
    category: str,
    content: str,
    lifespan: str = "persistent",
    evidence_kind: str = "explicit_user_statement",
    source_ref: str = "",
    dedupe_seed: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if category not in USER_MEMORY_CATEGORIES:
        raise ValueError("不支持的个人记忆类别")
    clean = _clean_user_memory_content(content)
    expires_at = user_memory_expiry(lifespan, now=now)
    source_ref = str(source_ref or "")[:255]
    dedupe = _memory_dedupe(owner_key, "remember", category, clean, None, source_ref, dedupe_seed)
    with transaction() as cursor:
        cursor.execute(
            _active_user_memory_sql("AND e.category=%s AND e.content=%s") + " ORDER BY e.id DESC LIMIT 1",
            (owner_key, category, clean),
        )
        existing = cursor.fetchone()
        if existing:
            return {"id": int(existing["id"]), "created": False}
        cursor.execute(
            """
            INSERT IGNORE INTO user_memory_events(
                owner_key,operation,category,content,lifespan,expires_at,target_event_id,
                evidence_kind,source_ref,dedupe_key,metadata
            ) VALUES (%s,'remember',%s,%s,%s,%s,NULL,%s,%s,%s,%s)
            """,
            (
                owner_key,
                category,
                clean,
                lifespan,
                expires_at,
                evidence_kind[:64],
                source_ref,
                dedupe,
                json.dumps({}, ensure_ascii=False),
            ),
        )
        created = cursor.rowcount == 1
        cursor.execute("SELECT id FROM user_memory_events WHERE dedupe_key=%s", (dedupe,))
        row = cursor.fetchone()
    return {"id": int(row["id"]), "created": created}


def correct_user_memory(
    owner_key: str,
    target_event_id: int,
    *,
    content: str,
    category: str | None = None,
    lifespan: str | None = None,
    evidence_kind: str = "explicit_user_correction",
    source_ref: str = "",
    dedupe_seed: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    clean = _clean_user_memory_content(content)
    with transaction() as cursor:
        target = _get_user_memory_event(cursor, owner_key, int(target_event_id))
        if not target:
            raise LookupError("指定个人记忆不存在")
        selected_category = category or str(target["category"])
        selected_lifespan = lifespan or str(target["lifespan"])
        if selected_category not in USER_MEMORY_CATEGORIES:
            raise ValueError("不支持的个人记忆类别")
        if lifespan is None:
            expires_at = target["expires_at"]
        else:
            expires_at = user_memory_expiry(selected_lifespan, now=now)
        source_ref = str(source_ref or "")[:255]
        dedupe = _memory_dedupe(
            owner_key,
            "correct",
            selected_category,
            clean,
            int(target_event_id),
            source_ref,
            dedupe_seed,
        )
        cursor.execute("SELECT id FROM user_memory_events WHERE dedupe_key=%s", (dedupe,))
        existing = cursor.fetchone()
        if existing:
            return {"id": int(existing["id"]), "corrected_id": int(target_event_id)}
        if not _get_active_user_memory(cursor, owner_key, int(target_event_id)):
            raise LookupError("指定个人记忆已失效或已被修正")
        cursor.execute(
            """
            INSERT INTO user_memory_events(
                owner_key,operation,category,content,lifespan,expires_at,target_event_id,
                evidence_kind,source_ref,dedupe_key,metadata
            ) VALUES (%s,'correct',%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                owner_key,
                selected_category,
                clean,
                selected_lifespan,
                expires_at,
                int(target_event_id),
                evidence_kind[:64],
                source_ref,
                dedupe,
                json.dumps({}, ensure_ascii=False),
            ),
        )
        cursor.execute("SELECT id FROM user_memory_events WHERE dedupe_key=%s", (dedupe,))
        row = cursor.fetchone()
    return {"id": int(row["id"]), "corrected_id": int(target_event_id)}


def forget_user_memory(
    owner_key: str,
    target_event_id: int,
    *,
    evidence_kind: str = "explicit_user_forget",
    source_ref: str = "",
    dedupe_seed: str = "",
) -> dict[str, Any]:
    with transaction() as cursor:
        target = _get_user_memory_event(cursor, owner_key, int(target_event_id))
        if not target:
            raise LookupError("指定个人记忆不存在")
        content = f"忘记个人记忆 #{int(target_event_id)}"
        source_ref = str(source_ref or "")[:255]
        dedupe = _memory_dedupe(
            owner_key,
            "forget",
            str(target["category"]),
            content,
            int(target_event_id),
            source_ref,
            dedupe_seed,
        )
        cursor.execute("SELECT id FROM user_memory_events WHERE dedupe_key=%s", (dedupe,))
        existing = cursor.fetchone()
        if existing:
            return {"id": int(existing["id"]), "forgotten_id": int(target_event_id)}
        if not _get_active_user_memory(cursor, owner_key, int(target_event_id)):
            raise LookupError("指定个人记忆已失效或已被修正")
        cursor.execute(
            """
            INSERT INTO user_memory_events(
                owner_key,operation,category,content,lifespan,expires_at,target_event_id,
                evidence_kind,source_ref,dedupe_key,metadata
            ) VALUES (%s,'forget',%s,%s,'persistent',NULL,%s,%s,%s,%s,%s)
            """,
            (
                owner_key,
                str(target["category"]),
                content,
                int(target_event_id),
                evidence_kind[:64],
                source_ref,
                dedupe,
                json.dumps({}, ensure_ascii=False),
            ),
        )
        cursor.execute("SELECT id FROM user_memory_events WHERE dedupe_key=%s", (dedupe,))
        row = cursor.fetchone()
    return {"id": int(row["id"]), "forgotten_id": int(target_event_id)}


def create_profile(owner_key: str, display_name: str, slug: str | None = None) -> dict[str, Any]:
    slug = slugify(slug or display_name)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO relationship_profiles(
                owner_key, slug, display_name, latest_state, known_facts,
                conservative_judgments, unknowns, response_preferences
            ) VALUES (%s,%s,%s,'尚未建立状态。','尚无已确认事实。','保持未知，不推断好感。','尚无。','简短、自然、低压力。')
            ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), status='active'
            """,
            (owner_key, slug, display_name),
        )
        cursor.execute(
            "SELECT * FROM relationship_profiles WHERE owner_key=%s AND slug=%s",
            (owner_key, slug),
        )
        profile = cursor.fetchone()
        for channel in CHANNELS:
            cursor.execute(
                "INSERT IGNORE INTO source_channels(relationship_id, kind, label) VALUES (%s,%s,%s)",
                (profile["id"], channel, channel),
            )
    return profile


def find_profile(owner_key: str, name: str) -> dict[str, Any] | None:
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT * FROM relationship_profiles
            WHERE owner_key=%s AND (display_name=%s OR slug=%s)
            ORDER BY status='active' DESC, id DESC LIMIT 1
            """,
            (owner_key, name.strip(), slugify(name)),
        )
        return cursor.fetchone()


def bind_chat(owner_key: str, chat_id: str, relationship_id: int) -> None:
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO chat_bindings(platform, chat_id, owner_key, relationship_id, active)
            VALUES ('feishu',%s,%s,%s,TRUE)
            ON DUPLICATE KEY UPDATE owner_key=VALUES(owner_key),
                relationship_id=VALUES(relationship_id), active=TRUE, archived_at=NULL
            """,
            (chat_id, owner_key, relationship_id),
        )
        cursor.execute(
            "UPDATE relationship_profiles SET status='active' WHERE id=%s AND owner_key=%s",
            (relationship_id, owner_key),
        )
        queue_reconcile(cursor, chat_id)


def get_binding(chat_id: str, owner_key: str | None = None) -> dict[str, Any] | None:
    where_owner = " AND b.owner_key=%s" if owner_key else ""
    params: tuple[Any, ...] = (chat_id, owner_key) if owner_key else (chat_id,)
    with transaction() as cursor:
        cursor.execute(
            f"""
            SELECT b.chat_id,b.owner_key,b.active,p.id,p.slug,p.display_name,p.status,
                p.current_channel,p.latest_state,p.known_facts,p.conservative_judgments,
                p.unknowns,p.response_preferences,p.created_at,p.updated_at
            FROM chat_bindings b JOIN relationship_profiles p ON p.id=b.relationship_id
            WHERE b.platform='feishu' AND b.chat_id=%s AND b.active=TRUE{where_owner}
            LIMIT 1
            """,
            params,
        )
        return cursor.fetchone()


def is_managed_chat(chat_id: str) -> bool:
    with transaction() as cursor:
        cursor.execute(
            "SELECT 1 AS managed FROM chat_bindings WHERE platform='feishu' AND chat_id=%s LIMIT 1",
            (chat_id,),
        )
        return cursor.fetchone() is not None


def archive_binding(owner_key: str, chat_id: str) -> dict[str, Any] | None:
    binding = get_binding(chat_id, owner_key)
    if not binding:
        return None
    with transaction() as cursor:
        cursor.execute(
            "UPDATE chat_bindings SET active=FALSE, archived_at=NOW(6) WHERE platform='feishu' AND chat_id=%s",
            (chat_id,),
        )
        cursor.execute(
            "UPDATE relationship_profiles SET status='archived' WHERE id=%s",
            (binding["id"],),
        )
        queue_reconcile(cursor, chat_id)
    return binding


def set_channel(owner_key: str, chat_id: str, channel: str) -> dict[str, Any]:
    if channel not in CHANNELS:
        raise ValueError("渠道只支持：微信、抖音、朋友圈、线下、其他")
    binding = get_binding(chat_id, owner_key)
    if not binding:
        raise LookupError("当前群尚未绑定人物")
    with transaction() as cursor:
        cursor.execute(
            "UPDATE relationship_profiles SET current_channel=%s WHERE id=%s",
            (channel, binding["id"]),
        )
        append_event(
            cursor,
            relationship_id=binding["id"],
            event_type="correction",
            author_role="user",
            content=f"当前来源渠道切换为：{channel}",
            channel=channel,
            evidence_kind="explicit_user_command",
        )
        queue_export(cursor, binding["id"])
    return get_binding(chat_id, owner_key) or binding


def confirm_latest_draft_with_status(
    cursor: Any,
    relationship_id: int,
    channel: str,
    *,
    external_message_id: str | None = None,
) -> tuple[int | None, bool]:
    cursor.execute(
        """
        SELECT e.id, e.content FROM relationship_events e
        JOIN source_channels c ON c.id=e.source_channel_id
        WHERE e.relationship_id=%s AND c.kind=%s AND e.event_type='draft'
          AND NOT EXISTS (
            SELECT 1 FROM relationship_events sent
            WHERE sent.supersedes_event_id=e.id AND sent.event_type='sent'
          )
        ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1
        """,
        (relationship_id, channel),
    )
    draft = cursor.fetchone()
    if not draft:
        return None, False
    return append_event_with_status(
        cursor,
        relationship_id=relationship_id,
        event_type="sent",
        author_role="user",
        content=draft["content"],
        channel=channel,
        evidence_kind="inferred_from_subsequent_received_same_channel",
        external_message_id=external_message_id,
        supersedes_event_id=draft["id"],
        metadata={"confirmation_rule": "same_relationship_same_channel"},
    )


def confirm_latest_draft(cursor: Any, relationship_id: int, channel: str) -> int | None:
    event_id, _ = confirm_latest_draft_with_status(cursor, relationship_id, channel)
    return event_id


def add_event(
    binding: dict[str, Any],
    *,
    event_type: str,
    author_role: str,
    content: str,
    channel: str | None = None,
    evidence_kind: str = "explicit_user_statement",
    external_message_id: str | None = None,
    auto_confirm_previous_draft: bool = False,
    metadata: dict[str, Any] | None = None,
) -> int:
    selected_channel = channel or binding["current_channel"]
    with transaction() as cursor:
        if event_type == "received" and auto_confirm_previous_draft:
            confirm_latest_draft(cursor, binding["id"], selected_channel)
        event_id = append_event(
            cursor,
            relationship_id=binding["id"],
            event_type=event_type,
            author_role=author_role,
            content=content,
            channel=selected_channel,
            evidence_kind=evidence_kind,
            external_message_id=external_message_id,
            metadata=metadata,
        )
        queue_export(cursor, binding["id"])
    return event_id


def _update_snapshot_in_transaction(
    cursor: Any,
    binding: dict[str, Any],
    updates: dict[str, str],
) -> tuple[int | None, bool]:
    clean = {key: value.strip() for key, value in updates.items() if key in PROFILE_COLUMNS and value.strip()}
    if not clean:
        return None, False
    cursor.execute(
        f"SELECT {','.join(PROFILE_COLUMNS)} FROM relationship_profiles WHERE id=%s FOR UPDATE",
        (binding["id"],),
    )
    current = cursor.fetchone()
    if not current:
        raise LookupError("relationship profile not found")
    changed = {key: value for key, value in clean.items() if str(current.get(key) or "") != value}
    cursor.execute(
        "SELECT COALESCE(MAX(version),0) AS current_version FROM relationship_snapshots WHERE relationship_id=%s",
        (binding["id"],),
    )
    current_version = int(cursor.fetchone()["current_version"])
    if not changed:
        return current_version, False
    assignments = ", ".join(f"{key}=%s" for key in changed)
    cursor.execute(
        f"UPDATE relationship_profiles SET {assignments} WHERE id=%s",
        (*changed.values(), binding["id"]),
    )
    version = current_version + 1
    cursor.execute(
        "INSERT INTO relationship_snapshots(relationship_id, version, snapshot_json) VALUES (%s,%s,%s)",
        (binding["id"], version, json.dumps(changed, ensure_ascii=False)),
    )
    return version, True


def update_snapshot(binding: dict[str, Any], updates: dict[str, str]) -> int:
    clean = {key: value.strip() for key, value in updates.items() if key in PROFILE_COLUMNS and value.strip()}
    if not clean:
        raise ValueError("没有可更新的状态字段")
    with transaction() as cursor:
        version, changed = _update_snapshot_in_transaction(cursor, binding, clean)
        if changed:
            queue_export(cursor, binding["id"])
    return int(version or 0)


def commit_turn(
    binding: dict[str, Any],
    *,
    source_ref: str,
    events: list[dict[str, Any]] | None = None,
    draft: dict[str, Any] | None = None,
    snapshot_updates: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not source_ref:
        raise ValueError("current message source is unavailable")
    event_items = list(events or [])
    if len(event_items) > 12:
        raise ValueError("a turn can commit at most 12 events")
    current_inbound_count = sum(bool(item.get("current_inbound")) for item in event_items)
    if current_inbound_count > 1:
        raise ValueError("a turn can contain at most one current inbound event")

    event_ids: list[int] = []
    confirmed_draft_id: int | None = None
    draft_id: int | None = None
    snapshot_version: int | None = None
    changed = False
    with transaction() as cursor:
        for index, item in enumerate(event_items):
            event_type = str(item.get("event_type") or "")
            if event_type not in COMMIT_EVENT_TYPES:
                raise ValueError(f"unsupported commit event_type: {event_type}")
            author_role = str(item.get("author_role") or EVENT_ROLE_DEFAULTS[event_type])
            if author_role not in AUTHOR_ROLES:
                raise ValueError(f"unsupported author_role: {author_role}")
            channel = str(item.get("channel") or binding["current_channel"])
            if channel not in CHANNELS:
                raise ValueError(f"unsupported channel: {channel}")
            current_inbound = bool(item.get("current_inbound"))
            confirm_previous = bool(item.get("confirm_previous_draft"))
            if current_inbound and event_type != "received":
                raise ValueError("current_inbound is valid only for received events")
            if confirm_previous and not (current_inbound and event_type == "received"):
                raise ValueError("draft confirmation requires the current received event")
            if confirm_previous:
                confirmed_id, confirmed_created = confirm_latest_draft_with_status(
                    cursor,
                    int(binding["id"]),
                    channel,
                    external_message_id=f"{source_ref}:commit:confirmed-draft",
                )
                confirmed_draft_id = confirmed_id
                changed = changed or confirmed_created
            event_id, event_created = append_event_with_status(
                cursor,
                relationship_id=int(binding["id"]),
                event_type=event_type,
                author_role=author_role,
                content=str(item.get("content") or ""),
                channel=channel,
                evidence_kind=str(item.get("evidence_kind") or "explicit_user_statement"),
                external_message_id=f"{source_ref}:commit:event:{index}:{event_type}",
            )
            event_ids.append(event_id)
            changed = changed or event_created

        if draft is not None:
            channel = str(draft.get("channel") or binding["current_channel"])
            if channel not in CHANNELS:
                raise ValueError(f"unsupported draft channel: {channel}")
            draft_id, draft_created = append_event_with_status(
                cursor,
                relationship_id=int(binding["id"]),
                event_type="draft",
                author_role="assistant",
                content=str(draft.get("content") or ""),
                channel=channel,
                evidence_kind="assistant_reply_suggestion",
                external_message_id=f"{source_ref}:commit:draft",
            )
            changed = changed or draft_created

        snapshot_version, snapshot_changed = _update_snapshot_in_transaction(
            cursor,
            binding,
            snapshot_updates or {},
        )
        changed = changed or snapshot_changed
        if changed:
            queue_export(cursor, int(binding["id"]))

    return {
        "event_ids": event_ids,
        "confirmed_draft_id": confirmed_draft_id,
        "draft_id": draft_id,
        "snapshot_version": snapshot_version,
        "changed": changed,
    }


def _context_working_set(
    groups: list[list[dict[str, Any]]],
    max_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    total = 0
    marker = "...[truncated; use relationship_search_events for the full event]"
    for group in groups:
        for raw_event in group:
            event = dict(raw_event)
            event_id = int(event["id"])
            if event_id in seen:
                continue
            content = str(event.get("content") or "")
            remaining = max_chars - total
            if remaining <= 0:
                continue
            if len(content) > remaining:
                if remaining <= len(marker):
                    continue
                content = content[: remaining - len(marker)] + marker
                event["content"] = content
                event["context_truncated"] = True
            selected.append(event)
            seen.add(event_id)
            total += len(content)
    return selected, total


def _compact_context_events(
    events: list[dict[str, Any]],
    max_serialized_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    serialized_chars = 2  # JSON list brackets.
    marker = "...[truncated; use relationship_search_events]"
    for event in events:
        occurred_at = event.get("occurred_at")
        if isinstance(occurred_at, datetime):
            occurred_at = occurred_at.isoformat(timespec="seconds")
        item = {
            "id": int(event["id"]),
            "event_type": str(event["event_type"]),
            "author_role": str(event.get("author_role") or ""),
            "content": str(event.get("content") or ""),
            "channel": event.get("channel"),
            "occurred_at": occurred_at,
        }
        separator_chars = 1 if selected else 0
        encoded = json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))
        remaining = max_serialized_chars - serialized_chars - separator_chars
        if len(encoded) > remaining:
            overflow = len(encoded) - remaining
            content = item["content"]
            keep = len(content) - overflow - len(marker)
            if keep <= 0:
                continue
            item["content"] = content[:keep] + marker
            item["context_truncated"] = True
            encoded = json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))
            if len(encoded) > remaining:
                continue
        selected.append(item)
        serialized_chars += separator_chars + len(encoded)
    selected.sort(key=lambda item: (str(item.get("occurred_at") or ""), int(item["id"])))
    return selected, serialized_chars


def recent_context(
    binding: dict[str, Any],
    limit: int = 12,
    max_chars: int = 4000,
    max_serialized_chars: int = 3000,
) -> dict[str, Any]:
    relationship_id = int(binding["id"])
    current_channel = str(binding["current_channel"])
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT MAX(created_at) AS snapshot_at
            FROM relationship_snapshots
            WHERE relationship_id=%s
            """,
            (relationship_id,),
        )
        snapshot_at = cursor.fetchone()["snapshot_at"]
        cursor.execute(
            """
            SELECT e.id,e.event_type,e.author_role,e.content,e.evidence_kind,e.occurred_at,c.kind AS channel
            FROM relationship_events e LEFT JOIN source_channels c ON c.id=e.source_channel_id
            WHERE e.relationship_id=%s AND e.event_type='correction'
              AND (%s IS NULL OR e.occurred_at > %s)
            ORDER BY e.occurred_at DESC,e.id DESC LIMIT 5
            """,
            (relationship_id, snapshot_at, snapshot_at),
        )
        corrections = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT e.id,e.event_type,e.author_role,e.content,e.evidence_kind,e.occurred_at,c.kind AS channel
            FROM relationship_events e JOIN source_channels c ON c.id=e.source_channel_id
            WHERE e.relationship_id=%s AND c.kind=%s AND e.event_type IN ('received','sent')
            ORDER BY e.occurred_at DESC,e.id DESC LIMIT %s
            """,
            (relationship_id, current_channel, min(max(limit, 1), 12)),
        )
        exchanges = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT e.id,e.event_type,e.author_role,e.content,e.evidence_kind,e.occurred_at,c.kind AS channel
            FROM relationship_events e JOIN source_channels c ON c.id=e.source_channel_id
            WHERE e.relationship_id=%s AND c.kind=%s AND e.event_type='draft'
              AND NOT EXISTS (
                  SELECT 1 FROM relationship_events sent
                  WHERE sent.supersedes_event_id=e.id AND sent.event_type='sent'
              )
            ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1
            """,
            (relationship_id, current_channel),
        )
        draft = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT e.id,e.event_type,e.author_role,e.content,e.evidence_kind,e.occurred_at,c.kind AS channel
            FROM relationship_events e LEFT JOIN source_channels c ON c.id=e.source_channel_id
            WHERE e.relationship_id=%s AND e.event_type='background'
            ORDER BY e.occurred_at DESC,e.id DESC LIMIT 3
            """,
            (relationship_id,),
        )
        background = list(cursor.fetchall())
    prioritized, _ = _context_working_set(
        [corrections, draft, exchanges, background],
        max_chars=max_chars,
    )
    context = {
        "relationship": {
            "id": binding["id"],
            "display_name": binding["display_name"],
            "status": binding["status"],
            "current_channel": binding["current_channel"],
        },
        "latest_state": binding["latest_state"],
        "known_facts": binding["known_facts"],
        "conservative_judgments": binding["conservative_judgments"],
        "unknowns": binding["unknowns"],
        "response_preferences": binding["response_preferences"],
        "recent_events": [],
        "context_stats": {
            "events": 0,
            "event_chars": 0,
            "max_event_chars": max_chars,
            "max_serialized_chars": max_serialized_chars,
        },
    }
    base_chars = len(json.dumps(context, ensure_ascii=False, default=str, separators=(",", ":")))
    event_budget = max(2, max_serialized_chars - base_chars - 64)
    selected, _ = _compact_context_events(prioritized, event_budget)
    context["recent_events"] = selected
    context["context_stats"]["events"] = len(selected)
    context["context_stats"]["event_chars"] = sum(len(str(item["content"])) for item in selected)
    serialized_chars = len(json.dumps(context, ensure_ascii=False, default=str, separators=(",", ":")))
    context["context_stats"]["serialized_chars"] = serialized_chars
    return context


def search_events(binding: dict[str, Any], query: str, channel: str | None, limit: int = 30) -> list[dict[str, Any]]:
    clauses = ["e.relationship_id=%s", "e.content LIKE %s"]
    params: list[Any] = [binding["id"], f"%{query}%"]
    if channel:
        clauses.append("c.kind=%s")
        params.append(channel)
    params.append(min(max(limit, 1), 100))
    with transaction() as cursor:
        cursor.execute(
            f"""
            SELECT e.id,e.event_type,e.author_role,e.content,e.evidence_kind,e.occurred_at,c.kind AS channel
            FROM relationship_events e LEFT JOIN source_channels c ON c.id=e.source_channel_id
            WHERE {' AND '.join(clauses)}
            ORDER BY e.occurred_at DESC,e.id DESC LIMIT %s
            """,
            tuple(params),
        )
        return events_to_json(cursor.fetchall())


def events_to_json(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for event in events:
        item = dict(event)
        if isinstance(item.get("occurred_at"), datetime):
            item["occurred_at"] = item["occurred_at"].isoformat(timespec="seconds")
        result.append(item)
    return result


def list_active_bindings() -> list[dict[str, Any]]:
    with transaction() as cursor:
        cursor.execute(
            """
            SELECT b.chat_id,b.owner_key,p.id AS relationship_id,p.display_name
            FROM chat_bindings b JOIN relationship_profiles p ON p.id=b.relationship_id
            WHERE b.platform='feishu' AND b.active=TRUE AND p.status='active'
            ORDER BY b.id
            """
        )
        return cursor.fetchall()


def list_managed_chat_ids() -> list[str]:
    with transaction() as cursor:
        cursor.execute("SELECT chat_id FROM chat_bindings WHERE platform='feishu' ORDER BY id")
        return [str(row["chat_id"]) for row in cursor.fetchall()]


def pending_control_requests() -> list[dict[str, Any]]:
    with transaction() as cursor:
        cursor.execute(
            "SELECT * FROM control_requests WHERE status IN ('pending','failed') AND attempts < 10 ORDER BY id LIMIT 100"
        )
        return cursor.fetchall()


def finish_control_requests(ids: list[int], error: str | None = None) -> None:
    if not ids:
        return
    placeholders = ",".join(["%s"] * len(ids))
    with transaction() as cursor:
        if error:
            cursor.execute(
                f"UPDATE control_requests SET status='failed',attempts=attempts+1,last_error=%s WHERE id IN ({placeholders})",
                (error[:1000], *ids),
            )
        else:
            cursor.execute(
                f"UPDATE control_requests SET status='done',attempts=attempts+1,processed_at=NOW(6),last_error=NULL WHERE id IN ({placeholders})",
                tuple(ids),
            )
