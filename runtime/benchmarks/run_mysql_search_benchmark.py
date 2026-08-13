from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from benchmarks.relationship_search_cases import cases  # noqa: E402
from goutoujunshi.enrichment import enrichment_text  # noqa: E402
from goutoujunshi.search import BRANCH_WEIGHTS, RRF_K, keyword_score, keyword_terms  # noqa: E402


EVENT_TABLE = "benchmark_relationship_events"
DOCUMENT_TABLE = "benchmark_relationship_search_documents"
SIGNAL_EVENT_COUNT = 140
FILLER_COUNT = 10000 - SIGNAL_EVENT_COUNT
DECISIONS = ("advance", "observe", "stop", "clarify", "support")


def _pymysql():
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required; run the project setup script") from exc
    return pymysql


def _settings() -> dict[str, Any]:
    database = os.environ.get("GOUTOUJUNSHI_BENCHMARK_DB_NAME", "").strip()
    if not database.startswith("goutoujunshi_benchmark"):
        raise RuntimeError("GOUTOUJUNSHI_BENCHMARK_DB_NAME must start with goutoujunshi_benchmark")
    password = os.environ.get("GOUTOUJUNSHI_BENCHMARK_DB_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("GOUTOUJUNSHI_BENCHMARK_DB_PASSWORD is not configured")
    return {
        "host": os.environ.get("GOUTOUJUNSHI_BENCHMARK_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("GOUTOUJUNSHI_BENCHMARK_DB_PORT", "3306")),
        "user": os.environ.get("GOUTOUJUNSHI_BENCHMARK_DB_USER", "root"),
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": _pymysql().cursors.DictCursor,
        "autocommit": False,
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
    }


def _create_fixture(connection: Any) -> None:
    items = cases()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema=DATABASE()"
        )
        if int(cursor.fetchone()["count"] or 0):
            raise RuntimeError("benchmark database must be empty and dedicated to this run")
        cursor.execute(
            f"""
            CREATE TABLE {EVENT_TABLE} (
                event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                relationship_id BIGINT UNSIGNED NOT NULL,
                channel VARCHAR(32) NOT NULL,
                event_type VARCHAR(16) NOT NULL,
                content LONGTEXT NOT NULL,
                supersedes_event_id BIGINT UNSIGNED NULL,
                occurred_at DATETIME(6) NOT NULL,
                KEY ix_benchmark_scope (relationship_id,channel,event_type,event_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """
        )
        cursor.execute(
            f"""
            CREATE TABLE {DOCUMENT_TABLE} (
                event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                relationship_id BIGINT UNSIGNED NOT NULL,
                source_text LONGTEXT NOT NULL,
                enrichment_text LONGTEXT NOT NULL,
                status VARCHAR(16) NOT NULL,
                KEY ix_benchmark_document_scope (relationship_id,status,event_id),
                FULLTEXT KEY ft_benchmark_source (source_text) WITH PARSER ngram,
                FULLTEXT KEY ft_benchmark_enrichment (enrichment_text) WITH PARSER ngram
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """
        )
        event_rows: list[tuple[Any, ...]] = []
        document_rows: list[tuple[Any, ...]] = []
        for index, item in enumerate(items):
            event_rows.append(
                (
                    int(item["expected_event_id"]),
                    int(item["relationship_id"]),
                    item["channel"],
                    "correction" if item["category"] == "correction" else "background",
                    item["document"],
                    item["supersedes_event_id"],
                    f"2026-01-{1 + (index % 28):02d} 10:00:00",
                )
            )
            document_rows.append(
                (
                    int(item["expected_event_id"]),
                    int(item["relationship_id"]),
                    item["document"],
                    enrichment_text(item["search_enrichment"]),
                    "enriched",
                )
            )
            if item["supersedes_event_id"]:
                event_rows.append(
                    (
                        int(item["supersedes_event_id"]),
                        int(item["relationship_id"]),
                        item["channel"],
                        "background",
                        item["superseded_document"],
                        None,
                        f"2025-11-{1 + (index % 28):02d} 10:00:00",
                    )
                )
                document_rows.append(
                    (
                        int(item["supersedes_event_id"]),
                        int(item["relationship_id"]),
                        item["superseded_document"],
                        f"旧记录 {item['superseded_document']}",
                        "enriched",
                    )
                )
        for index in range(FILLER_COUNT):
            event_id = SIGNAL_EVENT_COUNT + index + 1
            content = f"合成干扰事件编号{event_id}，用于测量一万条记录下的检索延迟。"
            event_rows.append(
                (
                    event_id,
                    1 + index % 2,
                    ("微信", "抖音", "朋友圈", "线下", "其他")[index % 5],
                    "background",
                    content,
                    None,
                    f"2025-12-{1 + (index % 28):02d} 10:00:00",
                )
            )
            document_rows.append(
                (event_id, 1 + index % 2, content, f"基准干扰数据 性能测试 编号{event_id}", "enriched")
            )
        cursor.executemany(
            f"""
            INSERT INTO {EVENT_TABLE}(
                event_id,relationship_id,channel,event_type,content,supersedes_event_id,occurred_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            event_rows,
        )
        cursor.executemany(
            f"""
            INSERT INTO {DOCUMENT_TABLE}(
                event_id,relationship_id,source_text,enrichment_text,status
            ) VALUES (%s,%s,%s,%s,%s)
            """,
            document_rows,
        )
    connection.commit()


def _query_branch(
    connection: Any,
    item: dict[str, Any],
    *,
    branch: str,
) -> list[dict[str, Any]]:
    channel_clause = " AND channel=%s" if item["explicit_channel"] else ""
    scope: list[Any] = [int(item["relationship_id"])]
    if item["explicit_channel"]:
        scope.append(item["channel"])
    with connection.cursor() as cursor:
        if branch == "exact":
            terms = keyword_terms(item["query"])
            locates = " OR ".join(["LOCATE(%s,content)>0"] * len(terms))
            score_terms = terms[1:] or terms
            term_score_sql = " + ".join(["(LOCATE(%s,content)>0)"] * len(score_terms))
            cursor.execute(
                f"""
                SELECT event_id,content,occurred_at FROM {EVENT_TABLE}
                WHERE relationship_id=%s{channel_clause} AND event_type <> 'draft' AND ({locates})
                ORDER BY (LOCATE(%s,content)>0) DESC,({term_score_sql}) DESC,
                    occurred_at DESC,event_id DESC LIMIT 40
                """,
                (*scope, *terms, terms[0], *score_terms),
            )
            return sorted(
                cursor.fetchall(),
                key=lambda row: (
                    keyword_score(str(row["content"]), item["query"], terms),
                    str(row["occurred_at"]),
                    int(row["event_id"]),
                ),
                reverse=True,
            )[:40]
        column = "source_text" if branch == "source_fulltext" else "enrichment_text"
        status_clause = " AND d.status='enriched'" if branch == "enrichment_fulltext" else ""
        cursor.execute(
            f"""
            SELECT e.event_id,e.content,
                MATCH(d.{column}) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
            FROM {DOCUMENT_TABLE} d
            JOIN {EVENT_TABLE} e ON e.event_id=d.event_id AND e.relationship_id=d.relationship_id
            WHERE d.relationship_id=%s{channel_clause} AND e.event_type <> 'draft'{status_clause}
              AND MATCH(d.{column}) AGAINST (%s IN NATURAL LANGUAGE MODE)
            ORDER BY score DESC,e.event_id DESC LIMIT 40
            """,
            (item["query"], *scope, item["query"]),
        )
        return list(cursor.fetchall())


def _ranked_ids(connection: Any, item: dict[str, Any]) -> list[int]:
    scores: dict[int, float] = {}
    for branch in ("exact", "source_fulltext", "enrichment_fulltext"):
        for rank, row in enumerate(_query_branch(connection, item, branch=branch), start=1):
            event_id = int(row["event_id"])
            scores[event_id] = scores.get(event_id, 0.0) + BRANCH_WEIGHTS[branch] / (RRF_K + rank)
    return sorted(scores, key=lambda event_id: (scores[event_id], event_id), reverse=True)


def _hydrate(connection: Any, item: dict[str, Any], event_ids: list[int]) -> dict[int, str]:
    if not event_ids:
        return {}
    placeholders = ",".join(["%s"] * len(event_ids))
    scope = [int(item["relationship_id"]), *event_ids]
    channel_clause = " AND channel=%s" if item["explicit_channel"] else ""
    if item["explicit_channel"]:
        scope.append(item["channel"])
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT event_id,content FROM {EVENT_TABLE}
            WHERE relationship_id=%s AND event_id IN ({placeholders}){channel_clause}
            """,
            tuple(scope),
        )
        return {int(row["event_id"]): str(row["content"]) for row in cursor.fetchall()}


def _corrections(connection: Any, item: dict[str, Any], event_ids: list[int]) -> list[dict[str, Any]]:
    if not event_ids:
        return []
    placeholders = ",".join(["%s"] * len(event_ids))
    scope = [int(item["relationship_id"]), *event_ids]
    channel_clause = " AND channel=%s" if item["explicit_channel"] else ""
    if item["explicit_channel"]:
        scope.append(item["channel"])
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT event_id,supersedes_event_id,content FROM {EVENT_TABLE}
            WHERE relationship_id=%s AND event_type='correction'
              AND supersedes_event_id IN ({placeholders}){channel_clause}
            ORDER BY occurred_at DESC,event_id DESC
            """,
            tuple(scope),
        )
        return list(cursor.fetchall())


def _assemble_results(
    ranked: list[int],
    authority: dict[int, str],
    correction_loader: Any,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    corrections: list[dict[str, Any]] = []
    correction_seen: set[int] = set()
    frontier = list(ranked)
    for _ in range(8):
        layer = correction_loader(frontier)
        new_layer = [row for row in layer if int(row["event_id"]) not in correction_seen]
        if not new_layer:
            break
        corrections.extend(new_layer)
        correction_seen.update(int(row["event_id"]) for row in new_layer)
        frontier = [int(row["event_id"]) for row in new_layer]
    corrections_by_target: dict[int, list[dict[str, Any]]] = {}
    for correction in corrections:
        corrections_by_target.setdefault(int(correction["supersedes_event_id"]), []).append(correction)
    result: list[dict[str, Any]] = []
    seen: set[int] = set()

    def append_corrections(target_id: int, depth: int = 0) -> None:
        if depth >= 8:
            return
        for correction in corrections_by_target.get(target_id, []):
            correction_id = int(correction["event_id"])
            append_corrections(correction_id, depth + 1)
            if correction_id not in seen:
                result.append({"event_id": correction_id, "content": str(correction["content"])})
                seen.add(correction_id)

    for event_id in ranked:
        append_corrections(event_id)
        if event_id in authority and event_id not in seen:
            result.append({"event_id": event_id, "content": authority[event_id]})
            seen.add(event_id)
        if len(result) >= limit:
            break
    return result[:limit]


def retrieve(connection: Any, item: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    ranked = _ranked_ids(connection, item)
    authority = _hydrate(connection, item, ranked)
    return _assemble_results(
        ranked,
        authority,
        lambda event_ids: _corrections(connection, item, event_ids),
        limit=limit,
    )


def _with_connection(settings: dict[str, Any], callback: Any) -> Any:
    connection = _pymysql().connect(**settings)
    try:
        return callback(connection)
    finally:
        connection.close()


def retrieve_online_db_path(
    settings: dict[str, Any], item: dict[str, Any], limit: int = 8
) -> list[dict[str, Any]]:
    branch_rows = {
        branch: _with_connection(
            settings,
            lambda connection, branch=branch: _query_branch(connection, item, branch=branch),
        )
        for branch in ("exact", "source_fulltext", "enrichment_fulltext")
    }
    scores: dict[int, float] = {}
    for branch, rows in branch_rows.items():
        for rank, row in enumerate(rows, start=1):
            event_id = int(row["event_id"])
            scores[event_id] = scores.get(event_id, 0.0) + BRANCH_WEIGHTS[branch] / (RRF_K + rank)
    ranked = sorted(scores, key=lambda event_id: (scores[event_id], event_id), reverse=True)
    authority = _with_connection(settings, lambda connection: _hydrate(connection, item, ranked))
    return _assemble_results(
        ranked,
        authority,
        lambda event_ids: _with_connection(
            settings, lambda connection: _corrections(connection, item, event_ids)
        ),
        limit=limit,
    )


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[position]


def _latency_summary(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values) if values else 0.0,
    }


def _decision_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "critical_event_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "integer"},
                "maxItems": 8,
            },
            "decision": {"type": "string", "enum": list(DECISIONS)},
        },
        "required": ["critical_event_ids", "decision"],
        "additionalProperties": False,
    }


def _model_decision(question: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["GOUTOUJUNSHI_OPENAI_BASE_URL"].rstrip("/"),
        default_headers={"User-Agent": "codex_cli_rs/0.0.0"},
        timeout=180,
        max_retries=1,
    )
    response = client.responses.create(
        model=os.environ["GOUTOUJUNSHI_MODEL"],
        reasoning={"effort": os.environ.get("GOUTOUJUNSHI_REASONING", "high")},
        instructions=(
            "Answer only from the supplied synthetic relationship events. Select only event IDs whose "
            "content directly answers the question; normally one event is sufficient, and unrelated records "
            "must not be selected. Apply this fixed conservative decision rubric: advance only for an agreed "
            "invitation or concrete next step; support for a temporary difficulty or busy reason; stop for an "
            "explicit boundary or refusal; clarify for a correction that changes the current fact; observe for "
            "a preference or background fact that requires no immediate action. Apply the same rubric regardless "
            "of unrelated surrounding events."
        ),
        input=json.dumps({"question": question, "events": events}, ensure_ascii=False),
        tools=[
            {
                "type": "function",
                "name": "record_benchmark_decision",
                "parameters": _decision_schema(),
                "strict": True,
            }
        ],
        tool_choice={"type": "function", "name": "record_benchmark_decision"},
        max_output_tokens=600,
    )
    for output in response.model_dump().get("output") or []:
        if output.get("type") == "function_call" and output.get("name") == "record_benchmark_decision":
            arguments = output.get("arguments")
            result = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            allowed_ids = {int(event["event_id"]) for event in events}
            returned_ids = [int(value) for value in result["critical_event_ids"]]
            if any(event_id not in allowed_ids for event_id in returned_ids):
                raise RuntimeError("answer evaluator returned an event ID outside its supplied context")
            result["critical_event_ids"] = list(dict.fromkeys(returned_ids))
            return result
    raise RuntimeError("answer evaluator did not return the required tool call")


def _oracle_evaluation(connection: Any, frozen: list[dict[str, Any]]) -> dict[str, Any]:
    fact_coverage: list[float] = []
    action_matches: list[bool] = []
    case_results: list[dict[str, Any]] = []
    with connection.cursor() as cursor:
        for item in frozen:
            clauses = ["relationship_id=%s"]
            params: list[Any] = [item["relationship_id"]]
            if item["explicit_channel"]:
                clauses.append("channel=%s")
                params.append(item["channel"])
            cursor.execute(
                f"SELECT event_id,content FROM {EVENT_TABLE} WHERE {' AND '.join(clauses)} "
                f"AND event_id <= {SIGNAL_EVENT_COUNT} ORDER BY event_id",
                tuple(params),
            )
            oracle_events = [dict(row) for row in cursor.fetchall()]
            top_events = retrieve(connection, item, limit=8)
            oracle = _model_decision(item["query"], oracle_events)
            top = _model_decision(item["query"], top_events)
            oracle_ids = {int(value) for value in oracle["critical_event_ids"]}
            top_ids = {int(value) for value in top["critical_event_ids"]}
            coverage = len(oracle_ids & top_ids) / len(oracle_ids) if oracle_ids else 0.0
            action_match = str(oracle["decision"]) == str(top["decision"])
            fact_coverage.append(coverage)
            action_matches.append(action_match)
            case_results.append(
                {
                    "case_id": item["case_id"],
                    "oracle_critical_event_ids": sorted(oracle_ids),
                    "top_critical_event_ids": sorted(top_ids),
                    "top_retrieved_event_ids": [int(row["event_id"]) for row in top_events],
                    "fact_coverage": coverage,
                    "oracle_decision": oracle["decision"],
                    "top_decision": top["decision"],
                    "expected_decision": item["expected_decision"],
                    "action_match": action_match,
                }
            )
    mean_fact_coverage = statistics.fmean(fact_coverage)
    action_consistency = _rate(action_matches)
    return {
        "cases": len(frozen),
        "critical_fact_coverage": mean_fact_coverage,
        "action_direction_consistency": action_consistency,
        "passed": mean_fact_coverage >= 0.95 and action_consistency >= 0.90,
        "case_results": case_results,
    }


def run(*, answer_eval: bool) -> dict[str, Any]:
    settings = _settings()
    connection = _pymysql().connect(**settings)
    fixture_created = False
    try:
        _create_fixture(connection)
        fixture_created = True
        frozen = [item for item in cases() if item["split"] == "frozen"]
        core_latencies: list[float] = []
        online_latencies: list[float] = []
        ranks: dict[str, list[int | None]] = {"semantic": [], "exact": []}
        for repeat in range(13):
            for item in frozen:
                started = time.perf_counter()
                results = retrieve(connection, item, limit=8)
                elapsed = (time.perf_counter() - started) * 1000
                if repeat:
                    core_latencies.append(elapsed)
                if repeat == 0:
                    ids = [row["event_id"] for row in results[:5]]
                    expected = int(item["expected_event_id"])
                    ranks[item["query_kind"]].append(ids.index(expected) + 1 if expected in ids else None)
        for repeat in range(4):
            for item in frozen:
                started = time.perf_counter()
                retrieve_online_db_path(settings, item, limit=8)
                elapsed = (time.perf_counter() - started) * 1000
                if repeat:
                    online_latencies.append(elapsed)
        correction_probes = [item for item in frozen if item["category"] == "correction"]
        correction_hits = []
        for item in correction_probes:
            probe = {**item, "query": item["superseded_query"]}
            result = retrieve(connection, probe, limit=8)
            correction_hits.append(
                bool(result) and int(result[0]["event_id"]) == int(item["expected_event_id"])
            )
        semantic_ranks = ranks["semantic"]
        exact_ranks = ranks["exact"]
        core_latency = _latency_summary(core_latencies)
        online_latency = _latency_summary(online_latencies)
        retrieval = {
            "frozen_cases": len(frozen),
            "semantic_recall_at_5": _rate([rank is not None for rank in semantic_ranks]),
            "semantic_mrr_at_5": statistics.fmean([1 / rank if rank else 0 for rank in semantic_ranks]),
            "exact_recall_at_5": _rate([rank is not None for rank in exact_ranks]),
            "correction_closure_recall_at_1": _rate(correction_hits),
            "sql_core_latency": core_latency,
            "online_db_path_latency": online_latency,
            "p95_ms": online_latency["p95_ms"],
            "max_ms": online_latency["max_ms"],
        }
        retrieval["passed"] = (
            retrieval["semantic_recall_at_5"] >= 0.90
            and retrieval["semantic_mrr_at_5"] >= 0.80
            and retrieval["exact_recall_at_5"] == 1.0
            and retrieval["correction_closure_recall_at_1"] == 1.0
            and retrieval["p95_ms"] <= 250
            and retrieval["max_ms"] <= 500
        )
        report: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "fixture_events": 10000,
            "retrieval": retrieval,
            "answer_oracle": _oracle_evaluation(connection, frozen) if answer_eval else "not_run",
        }
        report["passed"] = bool(retrieval["passed"]) and (
            not answer_eval or bool(report["answer_oracle"]["passed"])
        )
        return report
    finally:
        try:
            if fixture_created:
                with connection.cursor() as cursor:
                    cursor.execute(f"DROP TABLE IF EXISTS {DOCUMENT_TABLE}")
                    cursor.execute(f"DROP TABLE IF EXISTS {EVENT_TABLE}")
                connection.commit()
        finally:
            connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MySQL enriched relationship search acceptance")
    parser.add_argument("--answer-eval", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run(answer_eval=args.answer_eval)
    output = Path(args.output) if args.output else Path(".local/benchmarks") / (
        f"mysql-search-{datetime.now():%Y%m%d-%H%M%S}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["passed"], "output": str(output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
