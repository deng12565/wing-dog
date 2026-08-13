from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from benchmarks import run_mysql_search_benchmark as benchmark  # noqa: E402
from benchmarks.relationship_search_cases import cases  # noqa: E402
from goutoujunshi import database, enrichment, enrichment_jobs, search  # noqa: E402


def event(event_id: int, content: str, *, event_type: str = "background", supersedes=None):
    return {
        "id": event_id,
        "content": content,
        "channel": "微信",
        "event_type": event_type,
        "author_role": "user",
        "evidence_kind": "explicit_user_statement",
        "occurred_at": f"2026-01-{event_id:02d}T10:00:00",
        "supersedes_event_id": supersedes,
    }


def coverage(*, authority: int = 1, documents: int = 1, enriched: int = 1):
    return {
        "authority_count": authority,
        "document_count": documents,
        "enriched_count": enriched,
        "pending_count": max(authority - enriched, 0),
        "running_count": 0,
        "failed_count": 0,
    }


class RelationshipSearchTests(unittest.TestCase):
    def test_keyword_terms_keep_full_phrase_and_bounded_bigrams(self) -> None:
        terms = search.keyword_terms("她最近为什么没空？")
        self.assertEqual(terms[0], "她最近为什么没空？")
        self.assertIn("最近", terms)
        self.assertLessEqual(len(terms), 16)

    def test_rrf_uses_three_fixed_weighted_branches(self) -> None:
        fused = search.reciprocal_rank_fusion(
            {"exact": [1, 2], "source_fulltext": [2, 3], "enrichment_fulltext": [2, 4]}
        )
        self.assertEqual(
            fused[2]["sources"], ["exact", "source_fulltext", "enrichment_fulltext"]
        )
        self.assertGreater(fused[2]["rrf_score"], fused[1]["rrf_score"])
        self.assertEqual(search.RRF_K, 60)
        self.assertEqual(search.BRANCH_WEIGHTS, {
            "exact": 1.5,
            "source_fulltext": 1.0,
            "enrichment_fulltext": 1.25,
        })

    def test_mysql_enriched_search_returns_only_authoritative_event_fields(self) -> None:
        authoritative = event(2, "她最近因为项目交付很忙")
        with patch.object(search.repository, "exact_event_candidates", return_value=[]), patch.object(
            search.repository, "fulltext_event_candidates", side_effect=[[], [authoritative]]
        ) as fulltext, patch.object(
            search.repository, "hydrate_search_events", return_value={2: authoritative}
        ), patch.object(search.repository, "corrections_for_events", return_value=[]), patch.object(
            search.repository, "enrichment_status", return_value=coverage()
        ):
            result = search.search_relationship_events(
                {"id": 7}, query="她工作节点的情况", channel=None, limit=8
            )
        self.assertEqual([call.kwargs["field"] for call in fulltext.call_args_list], ["source", "enrichment"])
        self.assertEqual(result["retrieval"]["effective_mode"], "mysql_enriched")
        self.assertFalse(result["retrieval"]["degraded"])
        self.assertEqual(result["events"][0]["retrieval_sources"], ["enrichment_fulltext"])
        self.assertNotIn("search_enrichment", result["events"][0])
        self.assertNotIn("enrichment_text", result["events"][0])

    def test_incomplete_coverage_reports_mysql_raw_without_claiming_absence(self) -> None:
        with patch.object(search.repository, "exact_event_candidates", return_value=[]), patch.object(
            search.repository, "fulltext_event_candidates", return_value=[]
        ), patch.object(search.repository, "hydrate_search_events", return_value={}), patch.object(
            search.repository, "corrections_for_events", return_value=[]
        ), patch.object(
            search.repository,
            "enrichment_status",
            return_value=coverage(authority=3, documents=2, enriched=0),
        ):
            result = search.search_relationship_events({"id": 7}, query="旧事", channel=None)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["retrieval"]["effective_mode"], "mysql_raw")
        self.assertEqual(result["retrieval"]["degradation_reason"], "incomplete_enrichment")

    def test_drafts_require_explicit_channel(self) -> None:
        with self.assertRaises(ValueError):
            search.search_relationship_events(
                {"id": 7}, query="上一条", channel=None, include_drafts=True
            )

    def test_explicit_exact_draft_is_not_displaced_by_three_branch_candidates(self) -> None:
        draft = event(100, "就发这一句完整草稿", event_type="draft")
        non_drafts = [event(index, f"普通候选{index}") for index in range(1, 41)]
        exact = [*non_drafts, draft]
        hydrated = {item["id"]: item for item in exact}
        with patch.object(search.repository, "exact_event_candidates", return_value=exact), patch.object(
            search.repository, "fulltext_event_candidates", return_value=non_drafts
        ), patch.object(
            search.repository, "hydrate_search_events", return_value=hydrated
        ), patch.object(search.repository, "corrections_for_events", return_value=[]), patch.object(
            search.repository,
            "enrichment_status",
            return_value=coverage(authority=40, documents=40, enriched=40),
        ):
            result = search.search_relationship_events(
                {"id": 7},
                query="就发这一句完整草稿",
                channel="微信",
                limit=8,
                include_drafts=True,
            )
        self.assertEqual(result["events"][0]["id"], 100)
        self.assertEqual(result["events"][0]["retrieval_sources"], ["exact"])

    def test_channel_and_binding_are_forwarded_to_every_candidate_branch(self) -> None:
        with patch.object(search.repository, "exact_event_candidates", return_value=[]) as exact, patch.object(
            search.repository, "fulltext_event_candidates", return_value=[]
        ) as fulltext, patch.object(search.repository, "hydrate_search_events", return_value={}), patch.object(
            search.repository, "corrections_for_events", return_value=[]
        ), patch.object(search.repository, "enrichment_status", return_value=coverage()):
            search.search_relationship_events({"id": 99}, query="约定", channel="抖音")
        self.assertEqual(exact.call_args.args[:3], ({"id": 99}, search.keyword_terms("约定"), "抖音"))
        self.assertEqual([call.args[:3] for call in fulltext.call_args_list], [
            ({"id": 99}, "约定", "抖音"),
            ({"id": 99}, "约定", "抖音"),
        ])

    def test_correction_closure_precedes_corrected_event(self) -> None:
        old = event(1, "她喜欢摇滚")
        correction = event(2, "纠正：她说的是爵士", event_type="correction", supersedes=1)
        with patch.object(search.repository, "exact_event_candidates", return_value=[old]), patch.object(
            search.repository, "fulltext_event_candidates", return_value=[]
        ), patch.object(search.repository, "hydrate_search_events", return_value={1: old}), patch.object(
            search.repository, "corrections_for_events", side_effect=[[correction], []]
        ), patch.object(search.repository, "enrichment_status", return_value=coverage(authority=2)):
            result = search.search_relationship_events({"id": 7}, query="音乐", channel=None)
        self.assertEqual([item["id"] for item in result["events"]], [2, 1])
        self.assertEqual(result["events"][0]["retrieval_sources"], ["correction_closure"])

    def test_public_results_enforce_item_and_total_content_limits(self) -> None:
        items = [event(index, str(index) * 2000) for index in range(1, 9)]
        with patch.object(search.repository, "exact_event_candidates", return_value=items), patch.object(
            search.repository, "fulltext_event_candidates", return_value=[]
        ), patch.object(
            search.repository, "hydrate_search_events", return_value={item["id"]: item for item in items}
        ), patch.object(search.repository, "corrections_for_events", return_value=[]), patch.object(
            search.repository, "enrichment_status", return_value=coverage(authority=8, documents=8, enriched=8)
        ):
            result = search.search_relationship_events({"id": 7}, query="1", channel=None, limit=8)
        self.assertLessEqual(sum(len(item["content"]) for item in result["events"]), 6000)
        self.assertTrue(all(len(item["content"]) <= 1200 for item in result["events"]))
        self.assertTrue(any(item["content_truncated"] for item in result["events"]))

    def test_enrichment_validation_is_strict_and_bounded(self) -> None:
        valid = {
            "summary": "她在准备考试",
            "concepts": ["忙碌原因"],
            "aliases": ["最近没空"],
            "entities": ["资格考试"],
            "time_hints": ["最近"],
        }
        self.assertEqual(enrichment.normalize_search_enrichment(valid), valid)
        self.assertIsNone(enrichment.normalize_search_enrichment({**valid, "summary": 42}))
        self.assertIsNone(enrichment.normalize_search_enrichment({**valid, "aliases": ["x"] * 9}))
        self.assertIsNone(enrichment.normalize_search_enrichment({**valid, "entities": [7]}))
        self.assertIsNone(enrichment.normalize_search_enrichment({**valid, "extra": "ignored"}))

    def test_batch_input_contains_all_eight_items_within_limit(self) -> None:
        rows = [
            {
                "event_id": index,
                "content": "私密正文" * 4000,
                "event_type": "background",
                "author_role": "user",
                "channel": "微信",
                "occurred_at": "2026-01-01",
            }
            for index in range(1, 9)
        ]
        payload = enrichment_jobs.build_enrichment_input(rows)
        parsed = json.loads(payload)
        self.assertEqual([item["event_id"] for item in parsed], list(range(1, 9)))
        self.assertTrue(all(item["content_truncated"] for item in parsed))
        self.assertTrue(all(len(item["content_segments"]) == 2 for item in parsed))
        self.assertLessEqual(len(payload), enrichment_jobs.MAX_BATCH_INPUT_CHARS)

    def test_batch_selector_preserves_full_events_by_claiming_fewer_rows(self) -> None:
        rows = [
            {
                "event_id": index,
                "content": "正文" * 3000,
                "event_type": "background",
                "author_role": "user",
                "channel": "微信",
            }
            for index in range(1, 4)
        ]
        selected = enrichment_jobs._select_input_batch(rows)
        payload = enrichment_jobs.build_enrichment_input(selected)
        parsed = json.loads(payload)
        self.assertEqual(len(selected), 1)
        self.assertFalse(parsed[0]["content_truncated"])

    def test_idle_enrichment_worker_does_not_call_requester(self) -> None:
        requester = Mock()
        with patch.object(enrichment_jobs, "recover_stale_enrichment_jobs", return_value=0), patch.object(
            enrichment_jobs, "claim_enrichment_jobs", return_value=[]
        ):
            result = enrichment_jobs.process_enrichment_jobs(requester=requester)
        self.assertEqual(result["claimed"], 0)
        requester.assert_not_called()

    def test_retry_failed_resets_only_selected_prompt_version(self) -> None:
        class Cursor:
            rowcount = 3

            def __init__(self):
                self.sql = ""
                self.params = ()

            def execute(self, sql, params=()):
                self.sql = " ".join(sql.split())
                self.params = params

        cursor = Cursor()

        class Transaction:
            def __enter__(self):
                return cursor

            def __exit__(self, *_):
                return False

        with patch.object(enrichment_jobs, "transaction", return_value=Transaction()):
            queued = enrichment_jobs.retry_failed_enrichment_jobs("mysql-enrichment-v2")
        self.assertEqual(queued, 3)
        self.assertIn("JOIN relationship_events e", cursor.sql)
        self.assertIn("j.source_sha256=SHA2(e.content,256)", cursor.sql)
        self.assertIn("WHERE j.prompt_version=%s AND j.status='failed'", cursor.sql)
        self.assertEqual(cursor.params, ("mysql-enrichment-v2",))

    def test_enrichment_result_rechecks_locked_authority_hash_before_write(self) -> None:
        valid = {
            "summary": "旧正文摘要",
            "concepts": ["旧正文"],
            "aliases": [],
            "entities": [],
            "time_hints": [],
        }
        claimed = [
            {
                "job_id": 5,
                "event_id": 41,
                "relationship_id": 7,
                "source_sha256": hashlib.sha256("旧正文".encode("utf-8")).hexdigest(),
                "content": "旧正文",
            }
        ]

        class Cursor:
            rowcount = 1

            def __init__(self):
                self.result = None
                self.calls = []

            def execute(self, sql, params=()):
                compact = " ".join(sql.split())
                self.calls.append((compact, params))
                if compact.startswith("SELECT relationship_id,event_type,content"):
                    self.result = {
                        "relationship_id": 7,
                        "event_type": "background",
                        "content": "新正文",
                    }

            def fetchone(self):
                return self.result

        cursor = Cursor()

        class Transaction:
            def __enter__(self):
                return cursor

            def __exit__(self, *_):
                return False

        with patch.object(enrichment_jobs, "transaction", return_value=Transaction()), patch.object(
            enrichment_jobs, "upsert_event_search_document"
        ) as upsert:
            done, failed = enrichment_jobs._store_results(
                claimed,
                [{"event_id": 41, "search_enrichment": valid}],
                prompt_version="mysql-enrichment-v1",
            )
        self.assertEqual((done, failed), (0, 1))
        upsert.assert_not_called()
        failure = next(call for call in cursor.calls if "SET status='failed'" in call[0])
        self.assertEqual(failure[1][0], "stale_source")
        self.assertIn("FOR UPDATE", cursor.calls[0][0])

    def test_non_draft_write_creates_raw_document_and_job_in_same_cursor(self) -> None:
        class Cursor:
            def __init__(self):
                self.rowcount = 0
                self.result = None
                self.document = False
                self.job = False

            def execute(self, sql, params=()):
                compact = " ".join(sql.split())
                self.rowcount = 0
                if compact.startswith("INSERT IGNORE INTO relationship_events"):
                    self.rowcount = 1
                elif compact.startswith("SELECT id,content,event_type FROM relationship_events"):
                    self.result = {"id": 41, "content": "confirmed event", "event_type": "received"}
                elif compact.startswith("INSERT INTO relationship_event_search_documents"):
                    self.document = True
                elif compact.startswith("SELECT status,enrichment_version FROM relationship_event_search_documents"):
                    self.result = {"status": "raw_only", "enrichment_version": ""}
                elif compact.startswith("INSERT INTO relationship_event_enrichment_jobs"):
                    self.job = True

            def fetchone(self):
                return self.result

        cursor = Cursor()
        with patch.object(database, "get_or_create_channel", return_value=1):
            event_id, created = database.append_event_with_status(
                cursor,
                relationship_id=7,
                event_type="received",
                author_role="other",
                content="confirmed event",
                channel="微信",
                evidence_kind="explicit_user_statement",
            )
        self.assertEqual((event_id, created), (41, True))
        self.assertTrue(cursor.document)
        self.assertTrue(cursor.job)

    def test_existing_enrichment_version_survives_empty_idempotent_replay(self) -> None:
        class Cursor:
            def __init__(self):
                self.calls = []
                self.result = None

            def execute(self, sql, params=()):
                compact = " ".join(sql.split())
                self.calls.append((compact, params))
                if compact.startswith("SELECT status,enrichment_version"):
                    self.result = {"status": "enriched", "enrichment_version": "mysql-enrichment-v2"}

            def fetchone(self):
                return self.result

        cursor = Cursor()
        status = database.upsert_event_search_document(
            cursor,
            event_id=41,
            relationship_id=7,
            source_text="authority",
            search_enrichment=None,
        )
        job_call = next(call for call in cursor.calls if "INSERT INTO relationship_event_enrichment_jobs" in call[0])
        self.assertEqual(status, "enriched")
        self.assertEqual(job_call[1][3], "mysql-enrichment-v2")
        document_call = cursor.calls[0]
        self.assertEqual(document_call[1][8], "raw_only")

    def test_write_time_enrichment_creates_done_job(self) -> None:
        valid = {
            "summary": "她在准备考试",
            "concepts": ["忙碌原因"],
            "aliases": ["最近没空"],
            "entities": ["资格考试"],
            "time_hints": ["最近"],
        }

        class Cursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((" ".join(sql.split()), params))

            def fetchone(self):
                return {"status": "enriched", "enrichment_version": "mysql-enrichment-v1"}

        cursor = Cursor()
        database.upsert_event_search_document(
            cursor,
            event_id=42,
            relationship_id=7,
            source_text="她在准备考试",
            search_enrichment=valid,
        )
        job_sql = next(sql for sql, _ in cursor.calls if "relationship_event_enrichment_jobs" in sql)
        self.assertIn("VALUES (%s,%s,%s,%s,'done'", job_sql)

    def test_schema_v5_has_ngram_documents_jobs_and_drops_old_vector_tables(self) -> None:
        schema = (RUNTIME / "goutoujunshi" / "schema.sql").read_text(encoding="utf-8")
        self.assertIn("relationship_event_search_documents", schema)
        self.assertIn("relationship_event_enrichment_jobs", schema)
        self.assertEqual(schema.count("WITH PARSER ngram"), 2)
        self.assertIn("WHERE event_type <> 'draft'", schema)
        self.assertIn(
            "SELECT e.id,e.relationship_id,SHA2(e.content,256),\n    IF(d.status='enriched'",
            schema,
        )
        self.assertIn("DROP TABLE IF EXISTS relationship_event_index_jobs", schema)
        self.assertIn("DROP TABLE IF EXISTS relationship_search_indexes", schema)
        self.assertIn("VALUES (5, 'replace vector search with enriched MySQL fulltext documents')", schema)

    def test_fixed_benchmark_split_and_semantic_non_overlap(self) -> None:
        items = cases()
        frozen = [item for item in items if item["split"] == "frozen"]
        semantic = [item for item in frozen if item["query_kind"] == "semantic"]
        self.assertEqual((len(items), len(frozen), len(semantic)), (120, 80, 40))
        self.assertEqual(len({item["category"] for item in items}), 6)
        corrections = [item for item in items if item["category"] == "correction"]
        self.assertEqual(len({item["supersedes_event_id"] for item in corrections}), 20)
        self.assertTrue(all(item["superseded_document"] for item in corrections))
        for item in semantic:
            query_bigrams = {item["query"][index : index + 2] for index in range(len(item["query"]) - 1)}
            doc_bigrams = {item["document"][index : index + 2] for index in range(len(item["document"]) - 1)}
            self.assertFalse(query_bigrams & doc_bigrams, item["case_id"])
            self.assertIn(item["search_enrichment"]["concepts"][0], item["query"])

    def test_benchmark_correction_closure_precedes_superseded_event(self) -> None:
        correction = {
            "event_id": 101,
            "supersedes_event_id": 121,
            "content": "纠正后的权威正文",
        }
        result = benchmark._assemble_results(
            [121],
            {121: "旧正文"},
            lambda event_ids: [correction] if event_ids == [121] else [],
            limit=8,
        )
        self.assertEqual(
            [item["event_id"] for item in result],
            [101, 121],
        )

    def test_no_runtime_vector_implementation_remains(self) -> None:
        self.assertFalse((RUNTIME / "goutoujunshi" / "indexing.py").exists())
        self.assertFalse((RUNTIME / "goutoujunshi" / "vector_clients.py").exists())
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (RUNTIME / "goutoujunshi").glob("*.py")
        ).lower()
        self.assertNotIn("ollama", source)
        self.assertNotIn("milvus", source)


if __name__ == "__main__":
    unittest.main()
