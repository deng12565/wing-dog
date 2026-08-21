from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from goutoujunshi import repository, structured_import  # noqa: E402


class _PersonCursor:
    def __init__(self, profiles):
        self.profiles = profiles

    def execute(self, _sql, _params):
        return None

    def fetchall(self):
        return list(self.profiles)


class _ImportCursor:
    def __init__(self):
        self.result = None
        self.rowcount = 0
        self.manifest = None

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.rowcount = 0
        if compact.startswith("SELECT * FROM import_manifests"):
            self.result = self.manifest
        elif compact == "SELECT memory ORDER BY e.id DESC LIMIT 1":
            self.result = None
        elif compact.startswith("INSERT IGNORE INTO user_memory_events"):
            self.result = None
            self.rowcount = 1
        elif compact.startswith("INSERT INTO import_manifests"):
            self.manifest = {
                "relationship_id": int(params[0]),
                "source_sha256": params[2],
                "source_bytes": int(params[3]),
                "manifest_sha256": params[6],
                "imported_event_count": int(params[8]),
            }
            self.result = None
            self.rowcount = 1
        else:
            raise AssertionError(f"unexpected SQL: {compact}")

    def fetchone(self):
        return self.result


class StructuredImportTests(unittest.TestCase):
    @staticmethod
    def _manifest(source: bytes) -> dict[str, object]:
        return {
            "manifest_version": structured_import.MANIFEST_VERSION,
            "parser_version": structured_import.PARSER_VERSION,
            "source": {
                "sha256": structured_import.sha256_bytes(source),
                "bytes": len(source),
                "line_count": 2,
            },
            "person": {"aliases": ["Echo", "Person"]},
            "events": [
                {
                    "event_type": "received",
                    "author_role": "other",
                    "channel": "微信",
                    "content": "verbatim received",
                    "source_lines": [1],
                    "verbatim": True,
                    "evidence_kind": "structured_verbatim_transcript",
                    "occurred_at": "2026-08-20T21:17:00+08:00",
                    "metadata": {"derived": False, "uncertain": False},
                }
            ],
            "user_memories": [
                {
                    "category": "identity",
                    "content": "reusable user fact",
                    "lifespan": "persistent",
                    "source_line": 2,
                    "evidence_kind": "structured_explicit_user_background",
                    "metadata": {"derived": False, "uncertain": False},
                }
            ],
        }

    def test_unique_person_resolution_fails_closed_for_zero_or_multiple_matches(self) -> None:
        one = {
            "id": 1,
            "owner_key": "owner",
            "slug": "echo-person",
            "display_name": "Echo Person",
            "status": "active",
            "current_channel": "微信",
        }
        resolved = structured_import.resolve_unique_active_person(
            "owner", ["Echo", "Person"], cursor=_PersonCursor([one])
        )
        self.assertEqual(int(resolved["id"]), 1)

        with self.assertRaises(LookupError):
            structured_import.resolve_unique_active_person(
                "owner", ["Echo", "Missing"], cursor=_PersonCursor([one])
            )
        second = {**one, "id": 2, "slug": "echo-person-two"}
        with self.assertRaises(LookupError):
            structured_import.resolve_unique_active_person(
                "owner", ["Echo", "Person"], cursor=_PersonCursor([one, second])
            )

    def test_structured_import_is_idempotent_and_uses_stable_event_keys(self) -> None:
        source = b"verbatim received\nreusable user fact\n"
        manifest = self._manifest(source)
        cursor = _ImportCursor()
        appended = []

        @contextmanager
        def fake_transaction():
            yield cursor

        def append_event(_cursor, **kwargs):
            appended.append(kwargs)
            return 101, True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.txt"
            manifest_path = root / "manifest.json"
            source_path.write_bytes(source)
            manifest_path.write_bytes(structured_import.canonical_manifest_bytes(manifest))
            with patch.object(structured_import, "transaction", fake_transaction), patch.object(
                structured_import,
                "resolve_unique_active_person",
                return_value={"id": 7},
            ), patch.object(
                structured_import, "append_event_with_status", side_effect=append_event
            ), patch.object(
                structured_import.repository,
                "_active_user_memory_sql",
                return_value="SELECT memory ",
            ), patch.object(structured_import, "queue_export"):
                first = structured_import.import_structured_file(
                    source_path, manifest_path, owner_key="owner"
                )
                second_result = structured_import.import_structured_file(
                    source_path, manifest_path, owner_key="owner"
                )

        self.assertEqual(first["status"], "imported")
        self.assertEqual(first["created_events"], 1)
        self.assertEqual(first["memories"], 1)
        self.assertEqual(second_result["status"], "already_imported")
        self.assertEqual(len(appended), 1)
        self.assertEqual(
            appended[0]["external_message_id"],
            f"structured:{structured_import.sha256_bytes(source)}:lines:1:type:received",
        )

    def test_manifest_rejects_source_or_verbatim_line_mismatch(self) -> None:
        source = b"verbatim received\nreusable user fact\n"
        manifest = self._manifest(source)
        manifest["source"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            structured_import._validate_manifest(
                source, structured_import.canonical_manifest_bytes(manifest)
            )

    def test_wechat_archive_builder_maps_confirmed_local_author_and_preserves_evidence(self) -> None:
        source = """---
schema: wechat-agent-archive/v1
exporter_version: "0.2.0"
conversation_id: "conversation-1"
title: "Person"
conversation_type: "direct"
timezone: "Asia/Shanghai"
range_start: "2026-08-21T10:00:00+08:00"
range_end: "2026-08-21T10:03:00+08:00"
message_count: 4
export_status: "PARTIAL"
media_policy: "descriptions_only_no_originals"
---

# 完整时间线

## 2026-08-21

<a id="msg-aaaaaaaaaaaaaaaaaaaa"></a>

### 10:00:00 | received | Person

`text` `status=PASS`

> received text

<a id="msg-bbbbbbbbbbbbbbbbbbbb"></a>

### 10:01:00 | unknown | Local Name

`quote` `status=PASS`

> sent reply
>
> > 引用 Person: received text

回复：[#msg-aaaaaaaaaaaaaaaaaaaa](#msg-aaaaaaaaaaaaaaaaaaaa)

<a id="msg-cccccccccccccccccccc"></a>

### 10:02:00 | received | Person

`voice` `status=PASS`

> [语音转写] uncertain transcript

<a id="msg-dddddddddddddddddddd"></a>

### 10:03:00 | system | System

`recall` `status=PASS`

> [撤回] "Person" 撤回了一条消息
""".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat.md"
            path.write_bytes(source)
            manifest = structured_import.build_wechat_archive_manifest(
                path,
                self_author="Local Name",
                person_aliases=["Person"],
            )

        self.assertEqual(
            [event["event_type"] for event in manifest["events"]],
            ["received", "sent", "received", "background"],
        )
        self.assertEqual(manifest["events"][1]["author_role"], "user")
        self.assertEqual(
            manifest["events"][1]["external_message_id"],
            "wechat:conversation-1:msg-bbbbbbbbbbbbbbbbbbbb",
        )
        self.assertEqual(
            manifest["events"][1]["metadata"]["reply_to_source_message_id"],
            "msg-aaaaaaaaaaaaaaaaaaaa",
        )
        self.assertTrue(manifest["events"][2]["metadata"]["derived"])
        self.assertTrue(manifest["events"][2]["metadata"]["uncertain"])
        self.assertEqual(manifest["events"][3]["author_role"], "system")
        structured_import._validate_manifest(
            source, structured_import.canonical_manifest_bytes(manifest)
        )

    def test_wechat_archive_builder_rejects_unconfirmed_unknown_author(self) -> None:
        source = """---
schema: wechat-agent-archive/v1
conversation_id: "conversation-1"
conversation_type: "direct"
timezone: "Asia/Shanghai"
message_count: 1
---

# 完整时间线
## 2026-08-21
<a id="msg-aaaaaaaaaaaaaaaaaaaa"></a>
### 10:00:00 | unknown | Someone Else
`text` `status=PASS`
> text
""".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat.md"
            path.write_bytes(source)
            with self.assertRaisesRegex(ValueError, "unresolved WeChat author"):
                structured_import.build_wechat_archive_manifest(
                    path,
                    self_author="Local Name",
                    person_aliases=["Person"],
                )

    def test_structured_import_prefers_manifest_external_message_id(self) -> None:
        source = b"verbatim received\nreusable user fact\n"
        manifest = self._manifest(source)
        manifest["events"][0]["external_message_id"] = "wechat:conversation-1:msg-1"
        cursor = _ImportCursor()
        appended = []

        @contextmanager
        def fake_transaction():
            yield cursor

        def append_event(_cursor, **kwargs):
            appended.append(kwargs)
            return 101, True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.txt"
            manifest_path = root / "manifest.json"
            source_path.write_bytes(source)
            manifest_path.write_bytes(structured_import.canonical_manifest_bytes(manifest))
            with patch.object(structured_import, "transaction", fake_transaction), patch.object(
                structured_import,
                "resolve_unique_active_person",
                return_value={"id": 7},
            ), patch.object(
                structured_import, "append_event_with_status", side_effect=append_event
            ), patch.object(
                structured_import.repository,
                "_active_user_memory_sql",
                return_value="SELECT memory ",
            ), patch.object(structured_import, "queue_export"):
                structured_import.import_structured_file(
                    source_path, manifest_path, owner_key="owner"
                )

        self.assertEqual(appended[0]["external_message_id"], "wechat:conversation-1:msg-1")

        manifest = self._manifest(source)
        manifest["events"][0]["content"] = "invented quote"
        with self.assertRaises(ValueError):
            structured_import._validate_manifest(
                source, structured_import.canonical_manifest_bytes(manifest)
            )

    def test_echo_builder_uses_locked_rules_without_storing_duplicate_tail_quotes(self) -> None:
        lines = [f"line {index}" for index in range(1, 671)]
        for line in structured_import.ECHO_SENT_LINES + structured_import.ECHO_RECEIVED_LINES:
            lines[line - 1] = f"“quote {line}”"
        for line in structured_import.ECHO_DRAFT_LINES:
            lines[line - 1] = f"“draft {line}”"
        lines[10] = "contact label: test-person"
        lines[12] = "alias label: test-alias"
        for line, _category, extraction in structured_import.ECHO_USER_MEMORY_SPECS:
            lines[line - 1] = (
                f"memory {line}，third-party clause"
                if extraction == "before_first_comma"
                else f"memory {line}"
            )
        source = ("\n".join(lines) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "echo.txt"
            path.write_bytes(source)
            with patch.object(
                structured_import, "ECHO_SOURCE_SHA256", structured_import.sha256_bytes(source)
            ):
                manifest = structured_import.build_echo_manifest(path)

        events = manifest["events"]
        exact = [event for event in events if event["event_type"] in {"received", "sent"}]
        drafts = [event for event in events if event["event_type"] == "draft"]
        self.assertEqual(
            len(exact),
            len(structured_import.ECHO_SENT_LINES) + len(structured_import.ECHO_RECEIVED_LINES),
        )
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["source_lines"], list(structured_import.ECHO_DRAFT_LINES))
        self.assertFalse(any(595 in event["source_lines"] or 597 in event["source_lines"] for event in exact))
        self.assertEqual(len(manifest["user_memories"]), 4)
        self.assertEqual(manifest["person"]["aliases"], ["test-alias", "test-person"])
        self.assertEqual(manifest["user_memories"][-1]["content"], "memory 35")


class RecoveryTests(unittest.TestCase):
    def test_inferred_sent_corrections_are_append_only_and_idempotently_keyed(self) -> None:
        rows = [
            {
                "id": event_id,
                "relationship_id": 7,
                "event_type": "sent",
                "evidence_kind": "inferred_from_next_owner_message_same_channel",
                "channel": "微信",
            }
            for event_id in (1210, 1211)
        ]

        class Cursor:
            def execute(self, _sql, _params):
                return None

            def fetchall(self):
                return rows

        @contextmanager
        def fake_transaction():
            yield Cursor()

        appended = []

        def append_event(_cursor, **kwargs):
            appended.append(kwargs)
            return 2000 + len(appended), True

        with patch.object(repository, "transaction", fake_transaction), patch.object(
            repository, "append_event_with_status", side_effect=append_event
        ), patch.object(repository, "queue_export"):
            result = repository.correct_inferred_sent_events(
                7, [1210, 1211], source_ref="linux_migration_20260821"
            )

        self.assertEqual(result["created"], 2)
        self.assertEqual([item["supersedes_event_id"] for item in appended], [1210, 1211])
        self.assertTrue(all(item["event_type"] == "correction" for item in appended))
        self.assertTrue(all(item["metadata"]["actual_sent_status"] == "unknown" for item in appended))
        self.assertEqual(
            appended[0]["external_message_id"],
            "correction:linux_migration_20260821:sent:1210",
        )


if __name__ == "__main__":
    unittest.main()
