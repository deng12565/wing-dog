from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from goutoujunshi import repository  # noqa: E402


class MemoryCursor:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.result: list[dict[str, object]] = []
        self.rowcount = 0

    def _active(self, event: dict[str, object]) -> bool:
        if event["operation"] not in {"remember", "correct"}:
            return False
        expires_at = event["expires_at"]
        if isinstance(expires_at, datetime) and expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
            return False
        return not any(
            child["target_event_id"] == event["id"]
            and child["operation"] in {"correct", "forget"}
            for child in self.events
        )

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        compact = " ".join(sql.split())
        self.rowcount = 0
        if compact.startswith("SELECT e.id"):
            rows = [
                event.copy()
                for event in self.events
                if event["owner_key"] == params[0] and self._active(event)
            ]
            if "AND e.category=%s AND e.content=%s" in compact:
                rows = [row for row in rows if row["category"] == params[1] and row["content"] == params[2]]
            if "AND e.id=%s" in compact:
                rows = [row for row in rows if row["id"] == params[1]]
            if "ORDER BY (e.category='current_context') DESC" in compact:
                rows.sort(
                    key=lambda row: (row["category"] == "current_context", int(row["id"])),
                    reverse=True,
                )
            else:
                rows.sort(key=lambda row: int(row["id"]))
            if "ORDER BY e.id DESC" in compact:
                rows.reverse()
            self.result = rows[:1] if "LIMIT 1" in compact else rows
            return
        if compact.startswith("SELECT id,owner_key,operation,category,content,lifespan,expires_at"):
            self.result = [
                event.copy()
                for event in self.events
                if event["owner_key"] == params[0]
                and event["id"] == params[1]
                and event["operation"] in {"remember", "correct"}
            ]
            return
        if compact.startswith("INSERT IGNORE INTO user_memory_events") or compact.startswith(
            "INSERT INTO user_memory_events"
        ):
            if "'remember'" in compact:
                operation = "remember"
                owner, category, content, lifespan, expires_at, evidence, source, dedupe, _ = params
                target = None
            elif "'correct'" in compact:
                operation = "correct"
                owner, category, content, lifespan, expires_at, target, evidence, source, dedupe, _ = params
            else:
                operation = "forget"
                owner, category, content, target, evidence, source, dedupe, _ = params
                lifespan, expires_at = "persistent", None
            if any(event["dedupe_key"] == dedupe for event in self.events):
                return
            self.events.append(
                {
                    "id": len(self.events) + 1,
                    "owner_key": owner,
                    "operation": operation,
                    "category": category,
                    "content": content,
                    "lifespan": lifespan,
                    "expires_at": expires_at,
                    "target_event_id": target,
                    "evidence_kind": evidence,
                    "source_ref": source,
                    "dedupe_key": dedupe,
                    "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
                }
            )
            self.rowcount = 1
            return
        if compact.startswith("SELECT id FROM user_memory_events WHERE dedupe_key=%s"):
            self.result = [
                {"id": event["id"]} for event in self.events if event["dedupe_key"] == params[0]
            ]
            return
        raise AssertionError(f"unexpected SQL: {compact}")

    def fetchone(self) -> dict[str, object] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[dict[str, object]]:
        return self.result.copy()


class UserMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cursor = MemoryCursor()

        @contextmanager
        def fake_transaction():
            yield self.cursor

        self.transaction_patch = patch.object(repository, "transaction", fake_transaction)
        self.transaction_patch.start()

    def tearDown(self) -> None:
        self.transaction_patch.stop()

    def test_event_chain_dedupes_and_isolates_owners(self) -> None:
        first = repository.remember_user_memory(
            "owner-a", category="identity", content="我的生日是农历二月二十八", source_ref="message-1"
        )
        duplicate = repository.remember_user_memory(
            "owner-a", category="identity", content="我的生日是农历二月二十八", source_ref="message-2"
        )
        other_owner = repository.remember_user_memory(
            "owner-b", category="identity", content="我的生日是农历二月二十八", source_ref="message-1"
        )

        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["id"], first["id"])
        self.assertNotEqual(other_owner["id"], first["id"])
        self.assertEqual([row["id"] for row in repository.list_user_memory("owner-a")], [first["id"]])
        self.assertEqual([row["id"] for row in repository.list_user_memory("owner-b")], [other_owner["id"]])

    def test_correction_and_forget_append_history(self) -> None:
        original = repository.remember_user_memory(
            "owner-a", category="work_school", content="我在苏州上学", source_ref="message-1"
        )
        corrected = repository.correct_user_memory(
            "owner-a", original["id"], content="我在扬州上学", source_ref="message-2"
        )
        repeated_correction = repository.correct_user_memory(
            "owner-a", original["id"], content="我在扬州上学", source_ref="message-2"
        )
        self.assertEqual(repeated_correction, corrected)

        active = repository.list_user_memory("owner-a")
        self.assertEqual([row["id"] for row in active], [corrected["id"]])
        self.assertEqual(active[0]["content"], "我在扬州上学")
        with self.assertRaises(LookupError):
            repository.forget_user_memory("owner-b", corrected["id"], source_ref="message-3")

        forgotten = repository.forget_user_memory(
            "owner-a", corrected["id"], source_ref="message-3"
        )
        repeated_forget = repository.forget_user_memory(
            "owner-a", corrected["id"], source_ref="message-3"
        )
        self.assertEqual(repeated_forget, forgotten)
        self.assertEqual(repository.list_user_memory("owner-a"), [])
        self.assertEqual(len(self.cursor.events), 3)
        self.assertEqual(self.cursor.events[-1]["id"], forgotten["id"])
        self.assertEqual(
            [event["operation"] for event in self.cursor.events],
            ["remember", "correct", "forget"],
        )

    def test_expired_context_is_not_returned(self) -> None:
        repository.remember_user_memory(
            "owner-a",
            category="current_context",
            content="今天居家办公",
            lifespan="today",
            source_ref="old-message",
            now=datetime(2020, 1, 1, 8, tzinfo=repository.USER_MEMORY_TIMEZONE),
        )
        self.assertEqual(repository.list_user_memory("owner-a"), [])

    def test_today_and_week_expire_at_china_boundaries(self) -> None:
        now = datetime(2026, 8, 10, 15, 30, tzinfo=repository.USER_MEMORY_TIMEZONE)
        self.assertEqual(repository.user_memory_expiry("today", now=now), datetime(2026, 8, 10, 16, 0))
        self.assertEqual(repository.user_memory_expiry("week", now=now), datetime(2026, 8, 16, 16, 0))
        self.assertIsNone(repository.user_memory_expiry("persistent", now=now))

    def test_sensitive_values_and_precise_addresses_are_rejected(self) -> None:
        for content in (
            "我的 API key 是 secret-value",
            "我的身份证号是 123456789012345678",
            "我的家庭住址：某市某区测试路 8 号",
        ):
            with self.subTest(content=content), self.assertRaises(ValueError):
                repository.remember_user_memory(
                    "owner-a", category="identity", content=content, source_ref="message"
                )

    def test_current_context_and_newest_entries_win_bounded_retrieval(self) -> None:
        for index in range(4):
            repository.remember_user_memory(
                "owner-a",
                category="identity",
                content=f"identity fact {index}",
                source_ref=f"identity-{index}",
            )
        current = repository.remember_user_memory(
            "owner-a",
            category="current_context",
            content="working from home today",
            lifespan="today",
            source_ref="current",
        )

        rows = repository.list_user_memory("owner-a", per_category_limit=2)

        self.assertEqual(rows[0]["id"], current["id"])
        identity_rows = [row for row in rows if row["category"] == "identity"]
        self.assertEqual([row["content"] for row in identity_rows], ["identity fact 3", "identity fact 2"])


if __name__ == "__main__":
    unittest.main()
