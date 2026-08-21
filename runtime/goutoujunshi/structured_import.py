from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import repository
from .database import (
    AUTHOR_ROLES,
    CHANNELS,
    EVENT_TYPES,
    append_event_with_status,
    queue_export,
    transaction,
)


PARSER_VERSION = "relationship-structured-v1"
MANIFEST_VERSION = 1
WECHAT_ARCHIVE_SCHEMA = "wechat-agent-archive/v1"
WECHAT_MESSAGE_ID_RE = re.compile(r'^<a id="(msg-[0-9a-f]+)"></a>$')
WECHAT_MESSAGE_HEADER_RE = re.compile(
    r"^### (\d{2}:\d{2}:\d{2}) \| ([^|]+) \| (.+)$"
)
WECHAT_MESSAGE_KIND_RE = re.compile(r"^`([^`]+)` `status=([A-Z]+)`$")
WECHAT_DATE_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})$")
WECHAT_REPLY_RE = re.compile(r"^回复：\[#(msg-[0-9a-f]+)\]")
ECHO_SOURCE_SHA256 = "a213400c2ccac8addb45ff960afe0cee6c5d555df492f376a491b8926a2b450f"
ECHO_SENT_LINES = (93, 95, 267, 269, 561, 563, 565)
ECHO_RECEIVED_LINES = (
    99,
    101,
    103,
    115,
    117,
    131,
    157,
    187,
    189,
    259,
    261,
    273,
    275,
    277,
    287,
    289,
    327,
    329,
    331,
    333,
    343,
    351,
    365,
    381,
    383,
    385,
    397,
    401,
    415,
    417,
    419,
    431,
    433,
    447,
    449,
    451,
    457,
    459,
    461,
    469,
    471,
    475,
    483,
    499,
    515,
    517,
    535,
    537,
    539,
    541,
    543,
    545,
    547,
    549,
    551,
    569,
    571,
    573,
    575,
    577,
)
ECHO_DRAFT_LINES = (619, 621, 623)
ECHO_USER_MEMORY_SPECS = (
    (29, "work_school", "full_line"),
    (31, "identity", "full_line"),
    (33, "preference", "full_line"),
    (35, "lifestyle", "before_first_comma"),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_person_token(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _profile_matches_alias(profile: dict[str, Any], alias: str) -> bool:
    token = _normalize_person_token(alias)
    if not token:
        return False
    values = {
        _normalize_person_token(profile.get("display_name")),
        _normalize_person_token(profile.get("slug")),
    }
    return any(token == value or token in value for value in values if value)


def resolve_unique_active_person(
    owner_key: str,
    aliases: list[str],
    *,
    cursor: Any | None = None,
) -> dict[str, Any]:
    clean_aliases = list(dict.fromkeys(str(value).strip() for value in aliases if str(value).strip()))
    if not clean_aliases:
        raise ValueError("at least one person alias is required")

    def _resolve(active_cursor: Any) -> dict[str, Any]:
        active_cursor.execute(
            """
            SELECT id,owner_key,slug,display_name,status,current_channel
            FROM relationship_profiles
            WHERE owner_key=%s AND status='active'
            ORDER BY id
            """,
            (owner_key,),
        )
        profiles = list(active_cursor.fetchall())
        per_alias = [
            {int(profile["id"]) for profile in profiles if _profile_matches_alias(profile, alias)}
            for alias in clean_aliases
        ]
        if any(not matches for matches in per_alias):
            raise LookupError("one or more person aliases have no active match")
        candidate_ids = set.intersection(*per_alias)
        if len(candidate_ids) != 1:
            raise LookupError(
                f"person aliases must resolve to exactly one active profile; found {len(candidate_ids)}"
            )
        relationship_id = next(iter(candidate_ids))
        return next(dict(profile) for profile in profiles if int(profile["id"]) == relationship_id)

    if cursor is not None:
        return _resolve(cursor)
    with transaction() as active_cursor:
        return _resolve(active_cursor)


def _line_map(raw_text: str) -> dict[int, str]:
    return {index: value for index, value in enumerate(raw_text.splitlines(), start=1)}


def _wechat_frontmatter(raw_text: str) -> dict[str, Any]:
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("WeChat archive is missing YAML frontmatter")
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise ValueError("WeChat archive frontmatter is not terminated") from exc
    loaded: dict[str, Any] = {}
    for raw_line in lines[1:closing]:
        if not raw_line.strip():
            continue
        if ":" not in raw_line or raw_line[:1].isspace():
            raise ValueError("WeChat archive frontmatter must use flat key/value fields")
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or key in loaded:
            raise ValueError("WeChat archive frontmatter has an invalid or duplicate key")
        try:
            loaded[key] = json.loads(value)
        except json.JSONDecodeError:
            loaded[key] = value
    if str(loaded.get("schema") or "") != WECHAT_ARCHIVE_SCHEMA:
        raise ValueError("unsupported WeChat archive schema")
    if str(loaded.get("conversation_type") or "") != "direct":
        raise ValueError("structured WeChat import only accepts direct conversations")
    if str(loaded.get("timezone") or "") != "Asia/Shanghai":
        raise ValueError("structured WeChat import requires Asia/Shanghai timestamps")
    return loaded


def _next_nonempty(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        if lines[index].strip():
            return index
    raise ValueError("WeChat archive ended inside a message")


def _wechat_message_content(
    lines: list[str],
    start: int,
    end: int,
) -> tuple[str, list[int], str | None]:
    content: list[str] = []
    source_lines: list[int] = []
    reply_to: str | None = None
    for value in lines[start:end]:
        reply_match = WECHAT_REPLY_RE.match(value)
        if reply_match:
            reply_to = reply_match.group(1)
            break
    for index in range(start, end):
        value = lines[index]
        if value.startswith("> >"):
            break
        if value == ">":
            if content:
                break
            continue
        if value.startswith("> "):
            content.append(value[2:])
            source_lines.append(index + 1)
        elif content:
            break
    clean_content = "\n".join(part.rstrip() for part in content).strip()
    if not clean_content or not source_lines:
        raise ValueError("WeChat archive message has no source-backed content")
    return clean_content, source_lines, reply_to


def _wechat_event_evidence(message_kind: str, source_role: str) -> str:
    if source_role == "system":
        return "wechat_export_system_event"
    if message_kind == "voice":
        return "wechat_export_asr_transcript"
    if message_kind in {"image", "sticker", "video", "unknown"}:
        return "wechat_export_media_description"
    return "wechat_export_verbatim"


def build_wechat_archive_manifest(
    source_path: Path,
    *,
    self_author: str,
    person_aliases: list[str],
) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    raw_text = source_bytes.decode("utf-8-sig")
    header = _wechat_frontmatter(raw_text)
    clean_self_author = self_author.strip()
    clean_aliases = list(
        dict.fromkeys(str(value).strip() for value in person_aliases if str(value).strip())
    )
    if not clean_self_author:
        raise ValueError("the exported local WeChat author is required")
    if not clean_aliases:
        raise ValueError("at least one person alias is required")

    lines = raw_text.splitlines()
    current_date = ""
    parsed: list[dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    index = 0
    while index < len(lines):
        date_match = WECHAT_DATE_RE.match(lines[index])
        if date_match:
            current_date = date_match.group(1)
            index += 1
            continue
        anchor_match = WECHAT_MESSAGE_ID_RE.match(lines[index])
        if not anchor_match:
            index += 1
            continue
        if not current_date:
            raise ValueError("WeChat archive message appears before its date heading")
        message_id = anchor_match.group(1)
        if message_id in seen_message_ids:
            raise ValueError(f"duplicate WeChat archive message id: {message_id}")
        seen_message_ids.add(message_id)

        header_index = _next_nonempty(lines, index + 1)
        message_header = WECHAT_MESSAGE_HEADER_RE.match(lines[header_index])
        if not message_header:
            raise ValueError(f"invalid WeChat message header after {message_id}")
        time_text, source_role, author = (value.strip() for value in message_header.groups())
        kind_index = _next_nonempty(lines, header_index + 1)
        kind_match = WECHAT_MESSAGE_KIND_RE.match(lines[kind_index])
        if not kind_match:
            raise ValueError(f"invalid WeChat message kind after {message_id}")
        message_kind, exporter_status = kind_match.groups()

        next_index = kind_index + 1
        while next_index < len(lines) and not WECHAT_MESSAGE_ID_RE.match(lines[next_index]):
            if WECHAT_DATE_RE.match(lines[next_index]):
                break
            next_index += 1
        content, source_lines, reply_to = _wechat_message_content(
            lines, kind_index + 1, next_index
        )

        if source_role == "received":
            if author == clean_self_author:
                raise ValueError("received WeChat message uses the confirmed local author")
            event_type = "received"
            author_role = "other"
        elif source_role in {"unknown", "sent"}:
            if author != clean_self_author:
                raise ValueError(
                    f"unresolved WeChat author {author!r}; only {clean_self_author!r} is confirmed local"
                )
            event_type = "sent"
            author_role = "user"
        elif source_role == "system":
            event_type = "background"
            author_role = "system"
        else:
            raise ValueError(f"unsupported WeChat source role: {source_role}")

        derived = message_kind == "voice"
        uncertain = derived or exporter_status != "PASS"
        conversation_id = str(header.get("conversation_id") or "").strip()
        if not conversation_id:
            raise ValueError("WeChat archive conversation_id is required")
        metadata = {
            "derived": derived,
            "uncertain": uncertain,
            "wechat_archive_schema": WECHAT_ARCHIVE_SCHEMA,
            "conversation_id": conversation_id,
            "source_message_id": message_id,
            "source_role": source_role,
            "source_author": author,
            "message_kind": message_kind,
            "exporter_status": exporter_status,
            "archive_export_status": str(header.get("export_status") or ""),
        }
        if reply_to:
            metadata["reply_to_source_message_id"] = reply_to
        parsed.append(
            {
                "event_type": event_type,
                "author_role": author_role,
                "channel": "微信",
                "content": content,
                "source_lines": source_lines,
                "verbatim": event_type in {"received", "sent"},
                "evidence_kind": _wechat_event_evidence(message_kind, source_role),
                "occurred_at": f"{current_date}T{time_text}+08:00",
                "external_message_id": f"wechat:{conversation_id}:{message_id}",
                "metadata": metadata,
            }
        )
        index = next_index

    expected_count = int(header.get("message_count") or -1)
    if len(parsed) != expected_count:
        raise ValueError(
            f"WeChat archive message count mismatch: expected {expected_count}, parsed {len(parsed)}"
        )
    if not parsed:
        raise ValueError("WeChat archive contains no messages")

    return {
        "manifest_version": MANIFEST_VERSION,
        "parser_version": PARSER_VERSION,
        "source": {
            "sha256": sha256_bytes(source_bytes),
            "bytes": len(source_bytes),
            "line_count": len(lines),
        },
        "person": {"aliases": clean_aliases},
        "events": parsed,
        "user_memories": [],
        "archive": {
            "schema": WECHAT_ARCHIVE_SCHEMA,
            "exporter_version": str(header.get("exporter_version") or ""),
            "conversation_id": str(header.get("conversation_id") or ""),
            "title": str(header.get("title") or ""),
            "range_start": str(header.get("range_start") or ""),
            "range_end": str(header.get("range_end") or ""),
            "export_status": str(header.get("export_status") or ""),
            "media_policy": str(header.get("media_policy") or ""),
            "confirmed_local_author": clean_self_author,
        },
    }


def _strip_outer_quote(value: str) -> str:
    clean = value.strip()
    quote_pairs = (("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'"))
    for start, end in quote_pairs:
        if len(clean) >= 2 and clean.startswith(start) and clean.endswith(end):
            return clean[len(start) : -len(end)].strip()
    match = re.match(r"^[^：:]{1,12}[：:]\s*[“\"](.+)[”\"]$", clean)
    return match.group(1).strip() if match else clean


def _echo_time_for_line(line: int) -> str:
    boundaries = (
        (119, "21:17"),
        (165, "21:31"),
        (191, "21:48"),
        (225, "21:55"),
        (263, "22:03"),
        (283, "22:09"),
        (335, "22:14"),
        (367, "22:25"),
        (403, "22:33"),
        (453, "22:43"),
        (491, "22:52"),
        (553, "22:59"),
        (589, "23:06"),
    )
    selected = "21:17"
    for upper_bound, value in boundaries:
        if line < upper_bound:
            selected = value
            break
    hour, minute = selected.split(":", 1)
    return f"2026-08-20T{hour}:{minute}:00+08:00"


def _range_content(lines: dict[int, str], start: int, end: int) -> str:
    return "\n".join(lines[index] for index in range(start, end + 1)).strip()


def _value_after_label(lines: dict[int, str], line: int) -> str:
    value = lines[line].strip()
    parts = re.split(r"[：:]", value, maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        raise ValueError(f"Echo source line {line} is missing its locked label value")
    return parts[1].strip()


def build_echo_manifest(source_path: Path) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    source_sha = sha256_bytes(source_bytes)
    if source_sha != ECHO_SOURCE_SHA256:
        raise ValueError(
            f"Echo source SHA256 mismatch: expected {ECHO_SOURCE_SHA256}, found {source_sha}"
        )
    raw_text = source_bytes.decode("utf-8-sig")
    lines = _line_map(raw_text)
    if max(ECHO_DRAFT_LINES) not in lines:
        raise ValueError("Echo source is shorter than the locked line map")

    events: list[dict[str, Any]] = []
    for event_type, author_role, selected_lines in (
        ("sent", "user", ECHO_SENT_LINES),
        ("received", "other", ECHO_RECEIVED_LINES),
    ):
        for line in selected_lines:
            content = _strip_outer_quote(lines[line])
            if not content:
                raise ValueError(f"Echo verbatim line {line} is empty")
            events.append(
                {
                    "event_type": event_type,
                    "author_role": author_role,
                    "channel": "微信",
                    "content": content,
                    "source_lines": [line],
                    "verbatim": True,
                    "evidence_kind": "structured_verbatim_transcript",
                    "occurred_at": _echo_time_for_line(line),
                    "metadata": {"derived": False, "uncertain": False},
                }
            )

    for start, end, event_type, evidence_kind in (
        (17, 25, "analysis", "structured_derived_relationship_summary"),
        (41, 85, "background", "structured_user_reply_preferences"),
        (87, 587, "background", "structured_derived_timeline"),
        (589, 615, "analysis", "structured_derived_current_state"),
        (625, 670, "analysis", "structured_derived_chat_profile"),
        (37, 39, "background", "structured_expired_historical_context"),
    ):
        events.append(
            {
                "event_type": event_type,
                "author_role": "assistant" if event_type == "analysis" else "unknown",
                "channel": "微信",
                "content": _range_content(lines, start, end),
                "source_lines": list(range(start, end + 1)),
                "verbatim": False,
                "evidence_kind": evidence_kind,
                "occurred_at": "2026-08-20T23:10:00+08:00",
                "metadata": {
                    "derived": True,
                    "uncertain": True,
                    "historical": True,
                    "expired": evidence_kind == "structured_expired_historical_context",
                },
            }
        )

    draft_parts = [_strip_outer_quote(lines[line]) for line in ECHO_DRAFT_LINES]
    events.append(
        {
            "event_type": "draft",
            "author_role": "assistant",
            "channel": "微信",
            "content": "\n".join(draft_parts),
            "source_lines": list(ECHO_DRAFT_LINES),
            "verbatim": False,
            "evidence_kind": "structured_unconfirmed_reply_draft",
            "occurred_at": "2026-08-20T23:10:00+08:00",
            "metadata": {"derived": False, "uncertain": False, "sent_status": "unconfirmed"},
        }
    )

    memories = []
    for line, category, extraction in ECHO_USER_MEMORY_SPECS:
        raw_content = lines[line].strip()
        content = (
            raw_content.split("，", 1)[0].strip()
            if extraction == "before_first_comma"
            else raw_content
        )
        if not content:
            raise ValueError(f"Echo reusable memory is empty at locked source line {line}")
        memories.append(
            {
                "category": category,
                "content": content,
                "lifespan": "persistent",
                "source_line": line,
                "evidence_kind": "structured_explicit_user_background",
                "metadata": {"derived": False, "uncertain": False},
            }
        )

    alias = _value_after_label(lines, 13)
    display_name = _value_after_label(lines, 11).rsplit(maxsplit=1)[-1]
    return {
        "manifest_version": MANIFEST_VERSION,
        "parser_version": PARSER_VERSION,
        "source": {
            "sha256": source_sha,
            "bytes": len(source_bytes),
            "line_count": len(lines),
        },
        "person": {"aliases": [alias, display_name]},
        "events": sorted(
            events,
            key=lambda item: (str(item["occurred_at"]), int(item["source_lines"][0]), item["event_type"]),
        ),
        "user_memories": memories,
    }


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _parse_occurred_at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _validate_manifest(
    source_bytes: bytes,
    manifest_bytes: bytes,
) -> tuple[dict[str, Any], str, str, dict[int, str]]:
    source_sha = sha256_bytes(source_bytes)
    manifest_sha = sha256_bytes(manifest_bytes)
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    if not isinstance(manifest, dict) or int(manifest.get("manifest_version") or 0) != MANIFEST_VERSION:
        raise ValueError("unsupported structured manifest version")
    if str(manifest.get("parser_version") or "") != PARSER_VERSION:
        raise ValueError("unsupported structured parser version")
    source_claim = manifest.get("source") or {}
    if str(source_claim.get("sha256") or "").lower() != source_sha:
        raise ValueError("structured source SHA256 does not match manifest")
    if int(source_claim.get("bytes") or -1) != len(source_bytes):
        raise ValueError("structured source byte count does not match manifest")
    raw_text = source_bytes.decode("utf-8-sig")
    lines = _line_map(raw_text)
    if int(source_claim.get("line_count") or -1) != len(lines):
        raise ValueError("structured source line count does not match manifest")
    events = manifest.get("events")
    memories = manifest.get("user_memories")
    aliases = (manifest.get("person") or {}).get("aliases")
    if not isinstance(events, list) or not isinstance(memories, list) or not isinstance(aliases, list):
        raise ValueError("structured manifest is missing events, memories, or person aliases")
    external_message_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"structured event {index} is invalid")
        event_type = str(event.get("event_type") or "")
        author_role = str(event.get("author_role") or "")
        channel = str(event.get("channel") or "")
        content = str(event.get("content") or "").strip()
        source_lines = event.get("source_lines") or []
        if event_type not in EVENT_TYPES or author_role not in AUTHOR_ROLES or channel not in CHANNELS:
            raise ValueError(f"structured event {index} has invalid type, role, or channel")
        if not content or not source_lines or any(int(line) not in lines for line in source_lines):
            raise ValueError(f"structured event {index} has invalid content or source lines")
        _parse_occurred_at(event.get("occurred_at"))
        external_message_id = str(event.get("external_message_id") or "").strip()
        if len(external_message_id) > 255:
            raise ValueError("structured external message id is too long")
        if external_message_id:
            if external_message_id in external_message_ids:
                raise ValueError("structured external message id is duplicated")
            external_message_ids.add(external_message_id)
        if event_type in {"received", "sent"}:
            if event.get("verbatim") is not True:
                raise ValueError("received/sent structured events must be verbatim quotes")
            content_lines = content.splitlines()
            if len(content_lines) != len(source_lines) or any(
                content_line not in lines[int(line)]
                for content_line, line in zip(content_lines, source_lines)
            ):
                raise ValueError("verbatim structured event is not present at its source lines")
    for index, memory in enumerate(memories):
        if not isinstance(memory, dict):
            raise ValueError(f"structured memory {index} is invalid")
        line = int(memory.get("source_line") or 0)
        content = str(memory.get("content") or "").strip()
        if line not in lines or content not in lines[line]:
            raise ValueError("structured user memory is not present at its source line")
        if str(memory.get("category") or "") not in repository.USER_MEMORY_CATEGORIES:
            raise ValueError("structured user memory category is invalid")
        if str(memory.get("lifespan") or "") != "persistent":
            raise ValueError("structured import only accepts persistent reusable user memories")
        repository._clean_user_memory_content(content)
    return manifest, source_sha, manifest_sha, lines


def preflight_structured_file(
    source_path: Path,
    manifest_path: Path,
    *,
    owner_key: str,
) -> dict[str, Any]:
    """Validate immutable inputs and resolve one active person before archiving."""
    source_bytes = source_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    manifest, source_sha, manifest_sha, _ = _validate_manifest(source_bytes, manifest_bytes)
    aliases = [str(value) for value in manifest["person"]["aliases"]]
    profile = resolve_unique_active_person(owner_key, aliases)
    return {
        "relationship_id": int(profile["id"]),
        "source_sha256": source_sha,
        "manifest_sha256": manifest_sha,
    }


def import_structured_file(
    source_path: Path,
    manifest_path: Path,
    *,
    owner_key: str,
) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    manifest, source_sha, manifest_sha, _ = _validate_manifest(source_bytes, manifest_bytes)
    raw_text = source_bytes.decode("utf-8-sig")
    aliases = [str(value) for value in manifest["person"]["aliases"]]
    events = list(manifest["events"])
    memories = list(manifest["user_memories"])
    created_events = 0
    created_memories = 0
    with transaction() as cursor:
        profile = resolve_unique_active_person(owner_key, aliases, cursor=cursor)
        relationship_id = int(profile["id"])
        cursor.execute(
            "SELECT * FROM import_manifests WHERE source_sha256=%s FOR UPDATE",
            (source_sha,),
        )
        existing = cursor.fetchone()
        if existing:
            if int(existing["relationship_id"]) != relationship_id:
                raise ValueError("structured source was imported into another relationship")
            if str(existing.get("manifest_sha256") or "").lower() != manifest_sha:
                raise ValueError("structured source was imported with a different manifest")
            return {
                "status": "already_imported",
                "relationship_id": relationship_id,
                "source_sha256": source_sha,
                "manifest_sha256": manifest_sha,
                "events": int(existing["imported_event_count"]),
                "memories": 0,
            }

        for event in events:
            source_lines = [int(value) for value in event["source_lines"]]
            event_type = str(event["event_type"])
            line_key = ",".join(str(value) for value in source_lines)
            metadata = dict(event.get("metadata") or {})
            metadata.update(
                {
                    "structured_import": True,
                    "source_sha256": source_sha,
                    "source_lines": source_lines,
                    "manifest_sha256": manifest_sha,
                }
            )
            _, created = append_event_with_status(
                cursor,
                relationship_id=relationship_id,
                event_type=event_type,
                author_role=str(event["author_role"]),
                content=str(event["content"]),
                channel=str(event["channel"]),
                evidence_kind=str(event["evidence_kind"]),
                occurred_at=_parse_occurred_at(event["occurred_at"]),
                external_message_id=(
                    str(event.get("external_message_id") or "").strip()
                    or f"structured:{source_sha}:lines:{line_key}:type:{event_type}"
                ),
                metadata=metadata,
            )
            created_events += int(created)

        for memory in memories:
            content = repository._clean_user_memory_content(str(memory["content"]))
            category = str(memory["category"])
            line = int(memory["source_line"])
            cursor.execute(
                repository._active_user_memory_sql("AND e.category=%s AND e.content=%s")
                + " ORDER BY e.id DESC LIMIT 1",
                (owner_key, category, content),
            )
            if cursor.fetchone():
                continue
            dedupe = hashlib.sha256(
                f"structured\x1f{source_sha}\x1fmemory\x1f{line}\x1f{category}".encode("utf-8")
            ).hexdigest()
            cursor.execute(
                """
                INSERT IGNORE INTO user_memory_events(
                    owner_key,operation,category,content,lifespan,expires_at,target_event_id,
                    evidence_kind,source_ref,dedupe_key,metadata
                ) VALUES (%s,'remember',%s,%s,'persistent',NULL,NULL,%s,%s,%s,%s)
                """,
                (
                    owner_key,
                    category,
                    content,
                    str(memory["evidence_kind"])[:64],
                    f"structured:{source_sha}:line:{line}"[:255],
                    dedupe,
                    json.dumps(
                        {
                            **dict(memory.get("metadata") or {}),
                            "structured_import": True,
                            "source_sha256": source_sha,
                            "source_line": line,
                            "manifest_sha256": manifest_sha,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            created_memories += int(cursor.rowcount == 1)

        cursor.execute(
            """
            INSERT INTO import_manifests(
                relationship_id,source_path,source_sha256,source_bytes,parser_version,
                raw_content,manifest_sha256,manifest_content,imported_event_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                relationship_id,
                str(source_path),
                source_sha,
                len(source_bytes),
                PARSER_VERSION,
                raw_text,
                manifest_sha,
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
                len(events),
            ),
        )
        if created_events or created_memories:
            queue_export(cursor, relationship_id)
    return {
        "status": "imported",
        "relationship_id": relationship_id,
        "source_sha256": source_sha,
        "manifest_sha256": manifest_sha,
        "events": len(events),
        "created_events": created_events,
        "memories": created_memories,
    }
