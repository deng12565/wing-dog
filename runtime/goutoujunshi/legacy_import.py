from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .database import append_event, queue_export, transaction
from .repository import create_profile


PARSER_VERSION = "legacy-md-v1"
EVIDENCE_PARSER_VERSION = "relationship-evidence-md-v1"
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
DATE_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:[ T](\d{1,2}):(\d{2}))?")


@dataclass(frozen=True)
class LegacyBlock:
    level: int
    title: str
    content: str
    event_type: str
    author_role: str
    channel: str
    evidence_kind: str
    occurred_at: datetime


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def infer_channel(title: str, content: str) -> str:
    sample = f"{title}\n{content[:500]}"
    if "朋友圈" in sample:
        return "朋友圈"
    if "抖音" in sample:
        return "抖音"
    if "微信" in sample:
        return "微信"
    if any(word in sample for word in ("线下", "见面", "通话", "电话")):
        return "线下"
    return "其他"


def classify(title: str, content: str) -> tuple[str, str, str]:
    sample = f"{title}\n{content[:300]}"
    if any(word in title for word in ("对方已回复", "对方已发送", "收到消息", "她回复")):
        event_type, role = "received", "other"
    elif "用户已发送" in title or "我已发送" in title:
        event_type, role = "sent", "user"
    elif any(word in title for word in ("建议稿", "草稿", "未发送", "待发送")):
        event_type, role = "draft", "assistant"
    elif any(word in title for word in ("明确反馈", "纠正", "修订", "覆盖", "否决")):
        event_type, role = "correction", "user"
    elif any(
        word in title
        for word in (
            "分析",
            "判断",
            "复盘",
            "推断",
            "策略",
            "推荐",
            "避免",
            "回复方式",
            "工作要求",
            "核心记忆摘要",
        )
    ):
        event_type, role = "analysis", "assistant"
    else:
        event_type, role = "background", "unknown"

    if "截图" in sample:
        evidence = "legacy_screenshot_summary"
    elif any(word in sample for word in ("用户确认", "明确说明", "明确反馈")):
        evidence = "legacy_explicit_confirmation"
    elif "默认规则" in sample:
        evidence = "legacy_default_send_heuristic"
    else:
        evidence = "legacy_unclassified_evidence"
    return event_type, role, evidence


def parse_date(title: str, fallback: datetime) -> datetime:
    match = DATE_RE.search(title)
    if not match:
        return fallback
    year, month, day, hour, minute = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 12), int(minute or 0))
    except ValueError:
        return fallback


def parse_legacy_markdown(text: str, file_mtime: datetime) -> tuple[dict[str, str], list[LegacyBlock]]:
    matches = list(HEADING_RE.finditer(text))
    sections: list[tuple[int, str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections.append((len(match.group(1)), match.group(2).strip(), content))

    snapshot = {
        "latest_state": "尚未提取到最新状态。",
        "known_facts": "尚未提取到已确认事实。",
        "conservative_judgments": "保持未知，不将友好解读为恋爱兴趣。",
        "unknowns": "尚未提取到未知项。",
        "response_preferences": "简短、自然、低压力。",
    }
    section_map = {
        "最新状态": "latest_state",
        "人物与背景": "known_facts",
        "已知事实": "known_facts",
        "当前关系判断": "conservative_judgments",
        "保守判断": "conservative_judgments",
        "未知": "unknowns",
        "回复偏好": "response_preferences",
    }
    for level, title, content in sections:
        if level == 1:
            for marker, field in section_map.items():
                if marker in title and content:
                    snapshot[field] = content

    blocks: list[LegacyBlock] = []
    fallback_base = file_mtime.replace(microsecond=0)
    for index, (level, title, content) in enumerate(sections):
        if level < 2 or not content:
            continue
        event_type, role, evidence = classify(title, content)
        occurred = parse_date(title, fallback_base + timedelta(microseconds=index))
        blocks.append(
            LegacyBlock(
                level=level,
                title=title,
                content=f"[{title}]\n{content}",
                event_type=event_type,
                author_role=role,
                channel=infer_channel(title, content),
                evidence_kind=evidence,
                occurred_at=occurred,
            )
        )
    return snapshot, blocks


def import_legacy_file(path: Path, owner_key: str, display_name: str) -> dict[str, Any]:
    raw_bytes = path.read_bytes()
    raw_text = raw_bytes.decode("utf-8-sig")
    digest = sha256_bytes(raw_bytes)
    file_mtime = datetime.fromtimestamp(path.stat().st_mtime)
    with transaction() as cursor:
        cursor.execute("SELECT * FROM import_manifests WHERE source_sha256=%s", (digest,))
        existing = cursor.fetchone()
        if existing:
            return {
                "status": "already_imported",
                "relationship_id": int(existing["relationship_id"]),
                "sha256": digest,
                "events": int(existing["imported_event_count"]),
            }

    snapshot, blocks = parse_legacy_markdown(raw_text, file_mtime)
    profile = create_profile(owner_key, display_name, slug=path.stem)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE relationship_profiles SET latest_state=%s,known_facts=%s,
                conservative_judgments=%s,unknowns=%s,response_preferences=%s
            WHERE id=%s
            """,
            (
                snapshot["latest_state"],
                snapshot["known_facts"],
                snapshot["conservative_judgments"],
                snapshot["unknowns"],
                snapshot["response_preferences"],
                profile["id"],
            ),
        )
        imported = 0
        for index, block in enumerate(blocks):
            append_event(
                cursor,
                relationship_id=profile["id"],
                event_type=block.event_type,
                author_role=block.author_role,
                content=block.content,
                channel=block.channel,
                evidence_kind=block.evidence_kind,
                external_message_id=f"legacy:{digest}:{index}",
                occurred_at=block.occurred_at,
                metadata={"legacy_heading_level": block.level, "ambiguous": block.evidence_kind == "legacy_unclassified_evidence"},
            )
            imported += 1
        cursor.execute(
            """
            INSERT INTO import_manifests(
                relationship_id,source_path,source_sha256,source_bytes,parser_version,
                raw_content,imported_event_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (profile["id"], str(path), digest, len(raw_bytes), PARSER_VERSION, raw_text, imported),
        )
        queue_export(cursor, profile["id"])
    return {"status": "imported", "relationship_id": profile["id"], "sha256": digest, "events": imported}


def import_evidence_file(
    path: Path,
    relationship_id: int,
    *,
    source_ref: str = "",
) -> dict[str, Any]:
    """Append a context document to an existing profile without replacing its snapshot."""
    raw_bytes = path.read_bytes()
    raw_text = raw_bytes.decode("utf-8-sig")
    digest = sha256_bytes(raw_bytes)
    file_mtime = datetime.fromtimestamp(path.stat().st_mtime)
    _, blocks = parse_legacy_markdown(raw_text, file_mtime)

    with transaction() as cursor:
        cursor.execute("SELECT * FROM import_manifests WHERE source_sha256=%s", (digest,))
        existing = cursor.fetchone()
        if existing:
            if int(existing["relationship_id"]) != relationship_id:
                raise ValueError("this source was already imported into another relationship")
            return {
                "status": "already_imported",
                "relationship_id": relationship_id,
                "sha256": digest,
                "events": int(existing["imported_event_count"]),
            }

        cursor.execute("SELECT id FROM relationship_profiles WHERE id=%s", (relationship_id,))
        if not cursor.fetchone():
            raise LookupError(f"relationship {relationship_id} does not exist")

        imported = 0
        for index, block in enumerate(blocks):
            append_event(
                cursor,
                relationship_id=relationship_id,
                event_type=block.event_type,
                author_role=block.author_role,
                content=block.content,
                channel=block.channel,
                evidence_kind="imported_context_analysis"
                if block.event_type == "analysis"
                else "imported_historical_evidence",
                external_message_id=f"evidence:{digest}:{index}",
                occurred_at=block.occurred_at,
                metadata={
                    "source_ref": source_ref,
                    "source_sha256": digest,
                    "heading_level": block.level,
                    "historical": True,
                    "ambiguous": block.event_type == "background",
                },
            )
            imported += 1

        cursor.execute(
            """
            INSERT INTO import_manifests(
                relationship_id,source_path,source_sha256,source_bytes,parser_version,
                raw_content,imported_event_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                relationship_id,
                str(path),
                digest,
                len(raw_bytes),
                EVIDENCE_PARSER_VERSION,
                raw_text,
                imported,
            ),
        )
        queue_export(cursor, relationship_id)
    return {"status": "imported", "relationship_id": relationship_id, "sha256": digest, "events": imported}
