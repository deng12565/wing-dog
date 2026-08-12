from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .database import transaction


def export_root() -> Path:
    configured = os.environ.get("GOUTOUJUNSHI_EXPORT_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd() / ".local" / "relationships"


def _load_export_data(relationship_id: int) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    with transaction() as cursor:
        cursor.execute("SELECT * FROM relationship_profiles WHERE id=%s", (relationship_id,))
        profile = cursor.fetchone()
        if not profile:
            raise LookupError(f"relationship {relationship_id} does not exist")
        cursor.execute(
            """
            SELECT e.id,e.event_type,e.author_role,e.content,e.evidence_kind,e.occurred_at,
                e.supersedes_event_id,c.kind AS channel
            FROM relationship_events e LEFT JOIN source_channels c ON c.id=e.source_channel_id
            WHERE e.relationship_id=%s ORDER BY e.occurred_at,e.id
            """,
            (relationship_id,),
        )
        events = cursor.fetchall()
        cursor.execute(
            "SELECT source_path,source_sha256,source_bytes,imported_event_count,imported_at FROM import_manifests WHERE relationship_id=%s ORDER BY id LIMIT 1",
            (relationship_id,),
        )
        manifest = cursor.fetchone()
    return profile, events, manifest


def render_markdown(profile: dict[str, Any], events: list[dict[str, Any]], manifest: dict[str, Any] | None) -> str:
    updated = profile["updated_at"].isoformat(timespec="seconds")
    lines = [
        "---",
        f"relationship_id: {profile['id']}",
        f"person: {profile['display_name']}",
        f"status: {profile['status']}",
        f"channel: {profile['current_channel']}",
        f"updated: {updated}",
        "authority: mysql",
        "generated: true",
        "---",
        "",
        "# 使用说明",
        "",
        "此文件由 MySQL 自动导出，只读。请通过飞书关系群或数据库工具追加事实与修正。",
        "收到、已发送、草稿、背景、分析和纠正始终是不同事件；跨人物、跨渠道不得互相确认。",
        "",
        "# 人物与背景",
        "",
        profile["known_facts"].strip(),
        "",
        "# 保守判断",
        "",
        profile["conservative_judgments"].strip(),
        "",
        "# 未知项",
        "",
        profile["unknowns"].strip(),
        "",
        "# 事件记录",
        "",
    ]
    for event in events:
        stamp = event["occurred_at"].isoformat(sep=" ", timespec="seconds")
        supersedes = f"，修正/确认事件 #{event['supersedes_event_id']}" if event["supersedes_event_id"] else ""
        lines.extend(
            [
                f"## {stamp} | {event['channel'] or '其他'} | {event['event_type']} | #{event['id']}",
                "",
                f"> 角色：{event['author_role']}；证据：{event['evidence_kind']}{supersedes}",
                "",
                event["content"].strip(),
                "",
            ]
        )
    lines.extend(
        [
            "# 最新状态",
            "",
            profile["latest_state"].strip(),
            "",
            "# 回复偏好",
            "",
            profile["response_preferences"].strip(),
            "",
        ]
    )
    if manifest:
        lines.extend(
            [
                "# 迁移证据",
                "",
                f"- 原文件 SHA256：`{manifest['source_sha256']}`",
                f"- 原文件字节数：`{manifest['source_bytes']}`",
                f"- 导入事件数：`{manifest['imported_event_count']}`",
                "- 原始正文保存在 MySQL 导入清单和只读迁移归档中。",
                "",
            ]
        )
    return "\n".join(lines)


def atomic_write_readonly(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, stat.S_IREAD)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def export_relationship(relationship_id: int) -> Path:
    profile, events, manifest = _load_export_data(relationship_id)
    path = export_root() / f"{profile['slug']}.md"
    atomic_write_readonly(path, render_markdown(profile, events, manifest))
    return path


def process_export_jobs(limit: int = 25) -> dict[str, int]:
    with transaction() as cursor:
        cursor.execute(
            "SELECT id,relationship_id FROM export_jobs WHERE status IN ('pending','failed') AND attempts < 10 ORDER BY requested_at LIMIT %s",
            (limit,),
        )
        jobs = cursor.fetchall()
    done = failed = 0
    for job in jobs:
        try:
            with transaction() as cursor:
                cursor.execute("UPDATE export_jobs SET status='running',attempts=attempts+1 WHERE id=%s", (job["id"],))
            export_relationship(int(job["relationship_id"]))
            with transaction() as cursor:
                cursor.execute(
                    "UPDATE export_jobs SET status='done',completed_at=NOW(6),last_error=NULL WHERE id=%s",
                    (job["id"],),
                )
            done += 1
        except Exception as exc:
            with transaction() as cursor:
                cursor.execute(
                    "UPDATE export_jobs SET status='failed',last_error=%s WHERE id=%s",
                    (str(exc)[:1000], job["id"]),
                )
            failed += 1
    return {"done": done, "failed": failed}
