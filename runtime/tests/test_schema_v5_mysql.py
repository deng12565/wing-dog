from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
from goutoujunshi import database as relationship_database  # noqa: E402


@unittest.skipUnless(
    os.environ.get("GOUTOUJUNSHI_RUN_SCHEMA_V6_TEST") == "1",
    "requires an explicitly authorized disposable MySQL schema v6 replay",
)
class SchemaV6MySqlIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import pymysql
        except ImportError as exc:
            raise unittest.SkipTest("PyMySQL is unavailable") from exc
        database = os.environ.get("GOUTOUJUNSHI_SCHEMA_TEST_DB_NAME", "").strip()
        if not database.startswith("goutoujunshi_schema_test"):
            raise RuntimeError(
                "GOUTOUJUNSHI_SCHEMA_TEST_DB_NAME must start with goutoujunshi_schema_test"
            )
        password = os.environ.get("GOUTOUJUNSHI_SCHEMA_TEST_DB_PASSWORD", "").strip()
        if not password:
            raise RuntimeError("GOUTOUJUNSHI_SCHEMA_TEST_DB_PASSWORD is not configured")
        cls.database = database
        cls.connection = pymysql.connect(
            host=os.environ.get("GOUTOUJUNSHI_SCHEMA_TEST_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("GOUTOUJUNSHI_SCHEMA_TEST_DB_PORT", "3306")),
            user=os.environ.get("GOUTOUJUNSHI_SCHEMA_TEST_DB_USER", "root"),
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "connection"):
            cls._clear_tables()
            cls.connection.close()

    @classmethod
    def _clear_tables(cls) -> None:
        with cls.connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                (cls.database,),
            )
            names = [str(row["TABLE_NAME"]) for row in cursor.fetchall()]
            for name in names:
                if not name.replace("_", "").isalnum():
                    raise RuntimeError("unsafe schema test table name")
                cursor.execute(f"DROP TABLE `{name}`")
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
        cls.connection.commit()

    def setUp(self) -> None:
        self._clear_tables()

    def _apply_schema(self) -> None:
        @contextmanager
        def use_disposable_schema():
            try:
                with self.connection.cursor() as cursor:
                    yield cursor
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

        with patch.object(relationship_database, "transaction", use_disposable_schema):
            relationship_database.apply_schema()

    def _apply_statements(self, statements: list[str]) -> None:
        with self.connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        self.connection.commit()

    def _apply_v4_fixture(self) -> None:
        sql = (RUNTIME / "goutoujunshi" / "schema.sql").read_text(encoding="utf-8")
        excluded = (
            "CREATE TABLE IF NOT EXISTS relationship_event_search_documents",
            "CREATE TABLE IF NOT EXISTS relationship_event_enrichment_jobs",
            "INSERT IGNORE INTO relationship_event_search_documents",
            "INSERT IGNORE INTO relationship_event_enrichment_jobs",
            "DROP TABLE IF EXISTS relationship_event_index_jobs",
            "DROP TABLE IF EXISTS relationship_search_indexes",
            "VALUES (5, 'replace vector search with enriched MySQL fulltext documents')",
            "VALUES (6, 'structured imports and append-only inferred-sent recovery')",
        )
        statements = [
            item.strip()
            for item in sql.split(";")
            if item.strip() and not any(marker in item for marker in excluded)
        ]
        self._apply_statements(statements)
        self._apply_statements(
            [
                "ALTER TABLE import_manifests DROP COLUMN manifest_content",
                "ALTER TABLE import_manifests DROP COLUMN manifest_sha256",
            ]
        )
        self._apply_statements(
            [
                """
                CREATE TABLE relationship_search_indexes (
                    event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                    relationship_id BIGINT UNSIGNED NOT NULL,
                    vector_id VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """,
                """
                CREATE TABLE relationship_event_index_jobs (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    event_id BIGINT UNSIGNED NOT NULL,
                    relationship_id BIGINT UNSIGNED NOT NULL,
                    status VARCHAR(16) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """,
                """
                INSERT INTO relationship_profiles(
                    id,owner_key,slug,display_name,latest_state,known_facts,
                    conservative_judgments,unknowns,response_preferences
                ) VALUES (1,'owner','person','Person','','','','','')
                """,
                "INSERT INTO source_channels(id,relationship_id,kind,label) VALUES (1,1,'微信','微信')",
                """
                INSERT INTO relationship_events(
                    id,relationship_id,source_channel_id,event_type,author_role,content,
                    evidence_kind,dedupe_key,metadata,occurred_at
                ) VALUES
                    (1,1,1,'received','other','历史收到','explicit_user_statement',REPEAT('1',64),JSON_OBJECT(),'2026-01-01'),
                    (2,1,1,'correction','user','历史纠正','explicit_user_correction',REPEAT('2',64),JSON_OBJECT(),'2026-01-02'),
                    (3,1,1,'draft','assistant','历史草稿','assistant_reply_suggestion',REPEAT('3',64),JSON_OBJECT(),'2026-01-03')
                """,
                "INSERT INTO relationship_search_indexes VALUES (1,1,'legacy-vector-1')",
                "INSERT INTO relationship_event_index_jobs(event_id,relationship_id,status) VALUES (1,1,'done')",
            ]
        )

    def _assert_v6_structure(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
            self.assertEqual(
                [int(row["version"]) for row in cursor.fetchall()],
                [1, 2, 3, 4, 5, 6],
            )
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=%s AND table_name='import_manifests'
                  AND column_name IN ('manifest_sha256','manifest_content')
                ORDER BY column_name
                """,
                (self.database,),
            )
            self.assertEqual(
                [row["COLUMN_NAME"] for row in cursor.fetchall()],
                ["manifest_content", "manifest_sha256"],
            )
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema=%s AND table_name IN (
                    'relationship_event_search_documents',
                    'relationship_event_enrichment_jobs',
                    'relationship_event_index_jobs',
                    'relationship_search_indexes'
                ) ORDER BY table_name
                """,
                (self.database,),
            )
            self.assertEqual(
                [row["TABLE_NAME"] for row in cursor.fetchall()],
                ["relationship_event_enrichment_jobs", "relationship_event_search_documents"],
            )
            cursor.execute(
                """
                SELECT index_name FROM information_schema.statistics
                WHERE table_schema=%s AND table_name='relationship_event_search_documents'
                  AND index_type='FULLTEXT'
                ORDER BY index_name
                """,
                (self.database,),
            )
            self.assertEqual(
                [row["INDEX_NAME"] for row in cursor.fetchall()],
                ["ft_search_document_enrichment", "ft_search_document_source"],
            )

    def test_schema_v6_applies_twice_without_duplicate_rows_or_indexes(self) -> None:
        self._apply_schema()
        self._apply_schema()
        self._assert_v6_structure()

    def test_schema_v6_migrates_v4_history_and_is_idempotent(self) -> None:
        self._apply_v4_fixture()
        self._apply_schema()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_id,source_text,status,source_sha256=SHA2(source_text,256) AS hash_ok
                FROM relationship_event_search_documents ORDER BY event_id
                """,
            )
            self.assertEqual(
                [
                    (int(row["event_id"]), row["source_text"], row["status"], int(row["hash_ok"]))
                    for row in cursor.fetchall()
                ],
                [(1, "历史收到", "raw_only", 1), (2, "历史纠正", "raw_only", 1)],
            )
            cursor.execute(
                "SELECT event_id,status,attempts FROM relationship_event_enrichment_jobs ORDER BY event_id"
            )
            self.assertEqual(
                [
                    (int(row["event_id"]), row["status"], int(row["attempts"]))
                    for row in cursor.fetchall()
                ],
                [(1, "pending", 0), (2, "pending", 0)],
            )
        self._apply_schema()
        self._assert_v6_structure()
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM relationship_event_search_documents")
            self.assertEqual(int(cursor.fetchone()["count"]), 2)
            cursor.execute("SELECT COUNT(*) AS count FROM relationship_event_enrichment_jobs")
            self.assertEqual(int(cursor.fetchone()["count"]), 2)


if __name__ == "__main__":
    unittest.main()
