from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from goutoujunshi.database import apply_schema, transaction
from goutoujunshi.enrichment import ENRICHMENT_PROMPT_VERSION
from goutoujunshi.enrichment_jobs import (
    enrichment_job_status,
    process_enrichment_jobs,
    queue_enrichment_backfill,
    retry_failed_enrichment_jobs,
)
from goutoujunshi.exporter import export_relationship, process_export_jobs
from goutoujunshi.legacy_import import import_evidence_file, import_legacy_file, sha256_bytes
from goutoujunshi.repository import (
    USER_MEMORY_CATEGORIES,
    USER_MEMORY_LIFESPANS,
    correct_user_memory,
    finish_control_requests,
    forget_user_memory,
    health,
    list_active_bindings,
    list_managed_chat_ids,
    list_user_memory,
    pending_control_requests,
    remember_user_memory,
    resolve_draft,
    unresolved_drafts,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def command_health(_: argparse.Namespace) -> None:
    emit(health())


def command_init(_: argparse.Namespace) -> None:
    apply_schema()
    emit({"ok": True, "schema_version": 5})


def _owner() -> str:
    owner = os.environ.get("GOUTOUJUNSHI_OWNER_ID", "").strip()
    if not owner:
        raise RuntimeError("GOUTOUJUNSHI_OWNER_ID is not configured")
    return owner


def command_user_memory_list(_: argparse.Namespace) -> None:
    emit({"ok": True, "entries": list_user_memory(_owner())})


def command_user_memory_remember(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            **remember_user_memory(
                _owner(),
                category=args.category,
                content=args.content,
                lifespan=args.lifespan,
                evidence_kind=args.evidence_kind,
                source_ref=args.source_ref,
                dedupe_seed=args.dedupe_seed,
            ),
        }
    )


def command_user_memory_correct(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            **correct_user_memory(
                _owner(),
                args.target_id,
                content=args.content,
                category=args.category,
                lifespan=args.lifespan,
                evidence_kind=args.evidence_kind,
                source_ref=args.source_ref,
                dedupe_seed=args.dedupe_seed,
            ),
        }
    )


def command_user_memory_forget(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            **forget_user_memory(
                _owner(),
                args.target_id,
                evidence_kind=args.evidence_kind,
                source_ref=args.source_ref,
                dedupe_seed=args.dedupe_seed,
            ),
        }
    )


def _archive_source(path: Path, archive_root: Path) -> tuple[Path, str]:
    path = path.resolve()
    archive_root = archive_root.resolve()
    data = path.read_bytes()
    digest = sha256_bytes(data)
    archive_root.mkdir(parents=True, exist_ok=True)
    if path.parent == archive_root:
        return path, digest
    for existing in archive_root.glob(f"*-{path.name}"):
        if existing.is_file() and sha256_bytes(existing.read_bytes()) == digest:
            return existing, digest
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = archive_root / f"{stamp}-{path.name}"
    if not target.exists():
        shutil.copy2(path, target)
        os.chmod(target, stat.S_IREAD)
        hash_path = target.with_suffix(target.suffix + ".sha256")
        hash_path.write_text(f"{digest}\n", encoding="ascii")
        os.chmod(hash_path, stat.S_IREAD)
    return target, digest


def command_import(args: argparse.Namespace) -> None:
    source = Path(args.path).resolve()
    archive, digest = _archive_source(source, Path(args.archive_root).resolve())
    result = import_legacy_file(source, args.owner, args.name)
    if result["sha256"] != digest:
        raise RuntimeError("source changed while it was being imported")
    result["archive"] = str(archive)
    emit(result)


def command_import_evidence(args: argparse.Namespace) -> None:
    source = Path(args.path).resolve()
    archive, digest = _archive_source(source, Path(args.archive_root).resolve())
    result = import_evidence_file(
        archive,
        args.relationship_id,
        source_ref=args.source_ref,
    )
    if result["sha256"] != digest:
        raise RuntimeError("source changed while it was being imported")
    result["archive"] = str(archive)
    result["export_jobs"] = process_export_jobs(limit=25)
    emit(result)


def command_export(args: argparse.Namespace) -> None:
    path = export_relationship(args.relationship_id)
    emit({"ok": True, "filename": path.name})


def command_retry_exports(args: argparse.Namespace) -> None:
    emit(process_export_jobs(args.limit))


def _atomic_yaml(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(temp, path)


def command_reconcile(args: argparse.Namespace) -> None:
    config_path = Path(args.config).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    original = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    active = list_active_bindings()
    managed_ids = set(list_managed_chat_ids())
    gateway = config.setdefault("gateway", {})
    gateway["multiplex_profiles"] = True
    existing_routes = gateway.get("profile_routes") or []
    routes = [route for route in existing_routes if not str(route.get("name", "")).startswith("relation-")]
    for binding in active:
        routes.append(
            {
                "name": f"relation-{binding['relationship_id']}",
                "platform": "feishu",
                "chat_id": str(binding["chat_id"]),
                "profile": "goutoujunshi",
            }
        )
    gateway["profile_routes"] = routes
    feishu = config.setdefault("platforms", {}).setdefault("feishu", {})
    feishu["require_mention"] = False
    rules = feishu.setdefault("group_rules", {})
    for chat_id in managed_ids:
        rules.pop(chat_id, None)
    owner = os.environ["GOUTOUJUNSHI_OWNER_ID"]
    for binding in active:
        rules[str(binding["chat_id"])] = {
            "policy": "allowlist",
            "allowlist": [owner],
            "require_mention": False,
        }
    # Hermes currently passes only PlatformConfig.extra to the Feishu adapter.
    # Mirror these rules there so per-chat mention policy reaches admission.
    extra = feishu.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        feishu["extra"] = extra
    extra["require_mention"] = False
    extra["group_policy"] = "allowlist"
    extra["default_group_policy"] = "allowlist"
    extra["group_rules"] = {
        str(chat_id): dict(rule)
        for chat_id, rule in rules.items()
        if isinstance(rule, dict)
    }
    changed = config != original
    if changed:
        _atomic_yaml(config_path, config)
    requests = pending_control_requests()
    finish_control_requests([int(item["id"]) for item in requests])
    emit({"ok": True, "changed": changed, "active_routes": len(active)})


def command_stats(args: argparse.Namespace) -> None:
    with transaction() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM relationship_events WHERE relationship_id=%s", (args.relationship_id,))
        events = int(cursor.fetchone()["count"])
        cursor.execute("SELECT * FROM import_manifests WHERE relationship_id=%s ORDER BY id LIMIT 1", (args.relationship_id,))
        manifest = cursor.fetchone()
        cursor.execute("SELECT display_name,current_channel,status,LENGTH(latest_state) AS latest_state_chars FROM relationship_profiles WHERE id=%s", (args.relationship_id,))
        profile = cursor.fetchone()
    emit(
        {
            "ok": True,
            "events": events,
            "profile": profile,
            "import": {
                "sha256": manifest["source_sha256"],
                "source_bytes": manifest["source_bytes"],
                "imported_event_count": manifest["imported_event_count"],
            } if manifest else None,
        }
    )


def command_enrichment_backfill(args: argparse.Namespace) -> None:
    emit({"ok": True, "prompt_version": args.prompt_version, **queue_enrichment_backfill(args.prompt_version)})


def command_enrichment_work(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            "prompt_version": args.prompt_version,
            **process_enrichment_jobs(args.limit, prompt_version=args.prompt_version),
        }
    )


def command_enrichment_status(args: argparse.Namespace) -> None:
    emit({"ok": True, "prompt_version": args.prompt_version, **enrichment_job_status(args.prompt_version)})


def command_enrichment_retry_failed(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            "prompt_version": args.prompt_version,
            "queued": retry_failed_enrichment_jobs(args.prompt_version),
        }
    )


def command_draft_review_list(args: argparse.Namespace) -> None:
    emit({"ok": True, "drafts": unresolved_drafts(args.relationship_id)})


def command_draft_review_resolve(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            **resolve_draft(
                args.draft_id,
                resolution=args.resolution,
                source_ref=args.source_ref,
            ),
        }
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Goutoujunshi relationship store maintenance")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("health").set_defaults(func=command_health)
    sub.add_parser("init").set_defaults(func=command_init)
    imp = sub.add_parser("import-legacy")
    imp.add_argument("path")
    imp.add_argument("--owner", required=True)
    imp.add_argument("--name", required=True)
    imp.add_argument("--archive-root", required=True)
    imp.set_defaults(func=command_import)
    evidence = sub.add_parser("import-evidence")
    evidence.add_argument("path")
    evidence.add_argument("--relationship-id", required=True, type=int)
    evidence.add_argument("--archive-root", required=True)
    evidence.add_argument("--source-ref", default="")
    evidence.set_defaults(func=command_import_evidence)
    exp = sub.add_parser("export")
    exp.add_argument("relationship_id", type=int)
    exp.set_defaults(func=command_export)
    retry = sub.add_parser("retry-exports")
    retry.add_argument("--limit", type=int, default=25)
    retry.set_defaults(func=command_retry_exports)
    reconcile = sub.add_parser("reconcile-config")
    reconcile.add_argument("--config", required=True)
    reconcile.set_defaults(func=command_reconcile)
    stats = sub.add_parser("stats")
    stats.add_argument("relationship_id", type=int)
    stats.set_defaults(func=command_stats)
    enrichment_backfill = sub.add_parser("enrichment-backfill")
    enrichment_backfill.add_argument("--prompt-version", default=ENRICHMENT_PROMPT_VERSION)
    enrichment_backfill.set_defaults(func=command_enrichment_backfill)
    enrichment_work = sub.add_parser("enrichment-work")
    enrichment_work.add_argument("--limit", type=int, choices=range(1, 9), default=8)
    enrichment_work.add_argument("--prompt-version", default=ENRICHMENT_PROMPT_VERSION)
    enrichment_work.set_defaults(func=command_enrichment_work)
    enrichment_status = sub.add_parser("enrichment-status")
    enrichment_status.add_argument("--prompt-version", default=ENRICHMENT_PROMPT_VERSION)
    enrichment_status.set_defaults(func=command_enrichment_status)
    enrichment_retry = sub.add_parser("enrichment-retry-failed")
    enrichment_retry.add_argument("--prompt-version", default=ENRICHMENT_PROMPT_VERSION)
    enrichment_retry.set_defaults(func=command_enrichment_retry_failed)
    drafts = sub.add_parser("draft-review-list")
    drafts.add_argument("--relationship-id", type=int)
    drafts.set_defaults(func=command_draft_review_list)
    resolve = sub.add_parser("draft-review-resolve")
    resolve.add_argument("draft_id", type=int)
    resolve.add_argument("--resolution", choices=("sent", "not-sent"), required=True)
    resolve.add_argument("--source-ref", required=True)
    resolve.set_defaults(func=command_draft_review_resolve)
    sub.add_parser("user-memory-list").set_defaults(func=command_user_memory_list)
    remember = sub.add_parser("user-memory-remember")
    remember.add_argument("content")
    remember.add_argument("--category", choices=USER_MEMORY_CATEGORIES, default="identity")
    remember.add_argument("--lifespan", choices=USER_MEMORY_LIFESPANS, default="persistent")
    remember.add_argument("--evidence-kind", default="explicit_user_statement")
    remember.add_argument("--source-ref", default="")
    remember.add_argument("--dedupe-seed", default="")
    remember.set_defaults(func=command_user_memory_remember)
    correct = sub.add_parser("user-memory-correct")
    correct.add_argument("target_id", type=int)
    correct.add_argument("content")
    correct.add_argument("--category", choices=USER_MEMORY_CATEGORIES)
    correct.add_argument("--lifespan", choices=USER_MEMORY_LIFESPANS)
    correct.add_argument("--evidence-kind", default="explicit_user_correction")
    correct.add_argument("--source-ref", default="")
    correct.add_argument("--dedupe-seed", default="")
    correct.set_defaults(func=command_user_memory_correct)
    forget = sub.add_parser("user-memory-forget")
    forget.add_argument("target_id", type=int)
    forget.add_argument("--evidence-kind", default="explicit_user_forget")
    forget.add_argument("--source-ref", default="")
    forget.add_argument("--dedupe-seed", default="")
    forget.set_defaults(func=command_user_memory_forget)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except Exception as exc:
        emit({"ok": False, "error": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
