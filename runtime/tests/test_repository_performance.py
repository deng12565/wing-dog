from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from goutoujunshi import repository  # noqa: E402


class DummyCursor:
    pass


class SnapshotCursor:
    def __init__(self) -> None:
        self.result = None
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        compact = " ".join(sql.split())
        self.statements.append(compact)
        if compact.startswith("SELECT latest_state"):
            self.result = {
                "latest_state": "same",
                "known_facts": "facts",
                "conservative_judgments": "judgments",
                "unknowns": "unknowns",
                "response_preferences": "preferences",
            }
        elif compact.startswith("SELECT COALESCE(MAX(version),0) AS current_version"):
            self.result = {"current_version": 3}
        else:
            raise AssertionError(f"unexpected SQL: {compact}")

    def fetchone(self):
        return self.result


class RepositoryPerformanceTests(unittest.TestCase):
    def test_context_working_set_prioritizes_groups_and_respects_budget(self) -> None:
        corrections = [
            {"id": 1, "event_type": "correction", "content": "C" * 30, "occurred_at": "2026-01-01"}
        ]
        draft = [{"id": 2, "event_type": "draft", "content": "D" * 20, "occurred_at": "2026-01-02"}]
        exchanges = [
            {"id": 3, "event_type": "received", "content": "R" * 20, "occurred_at": "2026-01-03"}
        ]
        background = [
            {"id": 4, "event_type": "background", "content": "B" * 60, "occurred_at": "2026-01-04"}
        ]

        selected, chars = repository._context_working_set(
            [corrections, draft, exchanges, background],
            max_chars=70,
        )

        self.assertEqual([item["id"] for item in selected], [1, 2, 3])
        self.assertEqual(chars, 70)

    def test_compact_context_events_respect_actual_serialized_budget(self) -> None:
        events = [
            {
                "id": index,
                "event_type": "received",
                "author_role": "other",
                "content": "message" * 20,
                "channel": "wechat",
                "occurred_at": f"2026-01-{index:02d}",
                "evidence_kind": "unused-in-prompt",
            }
            for index in range(1, 10)
        ]

        selected, serialized_chars = repository._compact_context_events(events, 700)

        self.assertGreater(len(selected), 0)
        self.assertLessEqual(serialized_chars, 700)
        self.assertNotIn("evidence_kind", selected[0])

    def test_commit_turn_is_idempotent_and_queues_one_export(self) -> None:
        inserted: dict[str, int] = {}
        queued: list[int] = []

        @contextmanager
        def fake_transaction():
            yield DummyCursor()

        def fake_append(_cursor, **kwargs):
            key = kwargs["external_message_id"]
            created = key not in inserted
            inserted.setdefault(key, len(inserted) + 1)
            return inserted[key], created

        binding = {"id": 7, "current_channel": "微信"}
        payload = {
            "source_ref": "feishu:message",
            "events": [
                {"event_type": "received", "content": "hello", "channel": "微信", "current_inbound": True}
            ],
            "draft": {"content": "reply", "channel": "微信"},
        }
        with patch.object(repository, "transaction", fake_transaction), patch.object(
            repository, "append_event_with_status", side_effect=fake_append
        ), patch.object(repository, "queue_export", side_effect=lambda _cursor, rel_id: queued.append(rel_id)):
            first = repository.commit_turn(binding, **payload)
            second = repository.commit_turn(binding, **payload)

        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(first["event_ids"], second["event_ids"])
        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertEqual(queued, [7])

    def test_commit_turn_rejects_unsafe_draft_confirmation(self) -> None:
        @contextmanager
        def fake_transaction():
            yield DummyCursor()

        with patch.object(repository, "transaction", fake_transaction):
            with self.assertRaises(ValueError):
                repository.commit_turn(
                    {"id": 7, "current_channel": "微信"},
                    source_ref="feishu:message",
                    events=[
                        {
                            "event_type": "received",
                            "content": "historical",
                            "channel": "微信",
                            "confirm_previous_draft": True,
                        }
                    ],
                )

    def test_snapshot_noop_does_not_create_a_version(self) -> None:
        cursor = SnapshotCursor()

        version, changed = repository._update_snapshot_in_transaction(
            cursor,
            {"id": 7},
            {"latest_state": "same"},
        )

        self.assertEqual(version, 3)
        self.assertFalse(changed)
        self.assertEqual(len(cursor.statements), 2)


if __name__ == "__main__":
    unittest.main()
