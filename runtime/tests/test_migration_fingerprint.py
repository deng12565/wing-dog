from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from goutoujunshi import database  # noqa: E402


class FingerprintCursor:
    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.rows = rows
        self.result: list[dict[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        compact = " ".join(sql.split())
        if compact.startswith("SET SESSION") or compact.startswith("START TRANSACTION"):
            self.result = []
            return
        if "information_schema.COLUMNS" in compact:
            self.result = [{"COLUMN_NAME": "id"}, {"COLUMN_NAME": "private_value"}]
            return
        if "information_schema.KEY_COLUMN_USAGE" in compact:
            self.result = [{"COLUMN_NAME": "id"}]
            return
        match = re.search(r"FROM `([^`]+)`", compact)
        if not match:
            raise AssertionError(f"unexpected SQL: {compact}")
        self.result = list(self.rows[match.group(1)])

    def fetchall(self):
        return list(self.result)

    def __iter__(self):
        return iter(self.result)


class FingerprintConnection:
    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.rows = rows
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FingerprintCursor(self.rows)

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class MigrationFingerprintTests(unittest.TestCase):
    def _rows(self, value: str) -> dict[str, list[dict[str, object]]]:
        return {
            table: [{"id": index, "private_value": value if index == 1 else table}]
            for index, table in enumerate(database.MIGRATION_TABLES, start=1)
        }

    def test_fingerprint_is_stable_complete_and_does_not_return_private_values(self) -> None:
        first_connection = FingerprintConnection(self._rows("PRIVATE CHAT TEXT"))
        second_connection = FingerprintConnection(self._rows("PRIVATE CHAT TEXT"))
        with patch.object(database, "connect", side_effect=[first_connection, second_connection]):
            first = database.migration_fingerprint()
            second = database.migration_fingerprint()

        self.assertEqual(first, second)
        self.assertEqual(set(first["tables"]), set(database.MIGRATION_TABLES))
        self.assertEqual(len(first["tables"]), 12)
        self.assertNotIn("PRIVATE CHAT TEXT", str(first))
        self.assertTrue(first_connection.rolled_back)
        self.assertTrue(first_connection.closed)

    def test_fingerprint_changes_when_a_row_changes(self) -> None:
        with patch.object(
            database,
            "connect",
            side_effect=[
                FingerprintConnection(self._rows("first")),
                FingerprintConnection(self._rows("second")),
            ],
        ):
            first = database.migration_fingerprint()
            second = database.migration_fingerprint()

        self.assertNotEqual(first["sha256"], second["sha256"])
