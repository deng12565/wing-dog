from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ImportError:  # Python 3.10 compatibility for operator-side validation.
    import tomli as tomllib


PROVIDER = "openai-api"
TARGET_MODEL = "gpt-5.6-terra"
TARGET_REASONING = "high"
CODEX_ROUTE = "codex-gpt"
LEGACY_VECTOR_ENV_KEYS = {
    "GOUTOUJUNSHI_SEMANTIC_SEARCH_ENABLED",
    "GOUTOUJUNSHI_MILVUS_MANAGED",
    "GOUTOUJUNSHI_MILVUS_URL",
    "GOUTOUJUNSHI_MILVUS_TOKEN",
    "GOUTOUJUNSHI_OLLAMA_URL",
    "GOUTOUJUNSHI_OLLAMA_KEEP_ALIVE",
    "GOUTOUJUNSHI_SEMANTIC_TIMEOUT_SECONDS",
    "GOUTOUJUNSHI_RRF_K",
    "GOUTOUJUNSHI_KEYWORD_WEIGHT",
    "GOUTOUJUNSHI_SEMANTIC_WEIGHT",
    "GOUTOUJUNSHI_EMBEDDING_MODEL",
}
CODEX_USER_AGENT = "codex_cli_rs/0.0.0"
USER_TOOLSET = "goutoujunshi-user"
RELATIONSHIP_TOOLSET = "goutoujunshi"
PROJECT_PLUGIN_TOOLSETS = [RELATIONSHIP_TOOLSET, USER_TOOLSET]
FEISHU_RECOVERED_TOOLSETS = ["feishu_doc", "feishu_drive", "kanban"]
HOST_NATIVE_DISABLED_TOOLSETS = ["bfl"]
GLOBAL_DISABLED_TOOLSETS = [*HOST_NATIVE_DISABLED_TOOLSETS, *FEISHU_RECOVERED_TOOLSETS]
PROFILE_DISABLED_TOOLSETS = [
    "terminal",
    "file",
    "web",
    "browser",
    "delegation",
    "memory",
    "cron",
    "mcp",
    "computer",
    *GLOBAL_DISABLED_TOOLSETS,
]
SERVER_SECRET_SOURCE_KEYS = (
    "OPENAI_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_DOMAIN",
    "FEISHU_ALLOWED_USERS",
    "GOUTOUJUNSHI_OWNER_ID",
    "GOUTOUJUNSHI_OPENAI_BASE_URL",
    "GOUTOUJUNSHI_MODEL",
    "GOUTOUJUNSHI_REASONING",
)
SKILL_NAME = "goutoujunshi"
SKILL_RUNTIME_ENTRIES = ("SKILL.md", "agents", "references", "scripts", "assets")
PLUGIN_NAME = "goutoujunshi"
HERMES_RUNTIME_PATCH_VERSION = "0.20.4"
HERMES_SESSION_SHA256 = "19910bc0e551d8475ba1c338799d4218a180e6732ee9f5708ad089280b62ac9c"
HERMES_RUNTIME_PATCH_FILES = {
    "gateway/run.py": {
        "source_sha256": "b671d6cd9ce1373c399215995cffe5d918142b33f3f0659ede95eee74ee17ab6",
        "patched_sha256": "c5420c3056d3efd40ac91dd033ecbb152f3f59073e9c7180a2a73d6eedf24101",
    },
    "agent/turn_context.py": {
        "source_sha256": "fbaa02f8b569c866c044514f700c463af5391e43d92edf92f43ac05c57698b36",
        "patched_sha256": "0e6b8102dbba05e9cd27a4fd19c6670a3351c0fd03c0fc8d7abb2985a4269a0d",
    },
    "plugins/platforms/feishu/adapter.py": {
        "source_sha256": "3b2c8b82853128ca2e33c6e0f4b57903e039f1204bc1697e268d412211e1ed42",
        "patched_sha256": "d7c0862f1ad0f1002f34a997b99578be555b6927b93d0f79820ebf0dfb8b3a51",
    },
    "hermes_logging.py": {
        "source_sha256": "ff5e0755ef0ff0cbed2ceda8b8b0c832b1bcea8252f11bbceb2cde1fe8393fb5",
        "patched_sha256": "1dd9a50d4ec93ba053bf3c8fe9402a44eba86c22d5261e9ab699b6cbea6b79ff",
    },
}
# Compatibility names retained for existing operator scripts and callers.
HERMES_VISION_PATCH_VERSION = HERMES_RUNTIME_PATCH_VERSION
HERMES_VISION_RUN_SHA256 = HERMES_RUNTIME_PATCH_FILES["gateway/run.py"]["source_sha256"]
HERMES_VISION_PATCHED_SHA256 = HERMES_RUNTIME_PATCH_FILES["gateway/run.py"]["patched_sha256"]
PROFILE_SESSION_MIGRATION_REASON = "wing_dog_profile_runtime_migration_v1"


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for entry_name in SKILL_RUNTIME_ENTRIES:
        entry = root / entry_name
        if not entry.exists():
            continue
        candidates = (entry,) if entry.is_file() else entry.rglob("*")
        for path in candidates:
            if path.is_symlink():
                raise RuntimeError(f"skill runtime cannot contain symlinks: {path}")
            if path.is_file():
                manifest[path.relative_to(root).as_posix()] = _sha256_file(path)
    if "SKILL.md" not in manifest:
        raise RuntimeError(f"SKILL.md not found under project root: {root}")
    return dict(sorted(manifest.items()))


def _manifest_digest(manifest: dict[str, str]) -> str:
    payload = "".join(f"{path}\0{digest}\n" for path, digest in manifest.items())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plugin_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"plugin runtime cannot contain symlinks: {path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        manifest[path.relative_to(root).as_posix()] = _sha256_file(path)
    if "plugin.yaml" not in manifest or "__init__.py" not in manifest:
        raise RuntimeError(f"invalid goutoujunshi plugin source: {root}")
    return dict(sorted(manifest.items()))


def install_skill_package(project_root: Path, target_home: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    target_home = target_home.resolve()
    source_manifest = _runtime_manifest(project_root)
    skills_root = target_home / "skills"
    target = skills_root / SKILL_NAME
    staging = skills_root / f".{SKILL_NAME}.install-{secrets.token_hex(6)}"
    backup = skills_root / f".{SKILL_NAME}.backup-{secrets.token_hex(6)}"
    skills_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    moved_old = False
    installed_new = False
    try:
        for relative_path in source_manifest:
            source = project_root / Path(relative_path)
            destination = staging / Path(relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        installed_manifest = _runtime_manifest(staging)
        if installed_manifest != source_manifest:
            raise RuntimeError("staged skill package does not match the project runtime manifest")
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
        installed_new = True
        if _runtime_manifest(target) != source_manifest:
            raise RuntimeError("installed skill package does not match the project runtime manifest")
        if backup.exists():
            shutil.rmtree(backup)
            moved_old = False
        return {
            "ok": True,
            "target": str(target),
            "files": len(source_manifest),
            "sha256": _manifest_digest(source_manifest),
        }
    except Exception:
        if target.exists() and installed_new:
            shutil.rmtree(target)
        if moved_old and backup.exists():
            os.replace(backup, target)
            moved_old = False
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def command_install_skill(args: argparse.Namespace) -> None:
    emit(install_skill_package(Path(args.project_root), Path(args.target_home)))


def install_plugin_package(plugin_source: Path, target_home: Path) -> dict[str, Any]:
    plugin_source = plugin_source.resolve()
    target_home = target_home.resolve()
    source_manifest = _plugin_manifest(plugin_source)
    plugins_root = target_home / "plugins"
    target = plugins_root / PLUGIN_NAME
    staging = plugins_root / f".{PLUGIN_NAME}.install-{secrets.token_hex(6)}"
    backup = plugins_root / f".{PLUGIN_NAME}.backup-{secrets.token_hex(6)}"
    plugins_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    moved_old = False
    installed_new = False
    try:
        for relative_path in source_manifest:
            source = plugin_source / Path(relative_path)
            destination = staging / Path(relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if _plugin_manifest(staging) != source_manifest:
            raise RuntimeError("staged plugin package does not match the source manifest")
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
        installed_new = True
        if _plugin_manifest(target) != source_manifest:
            raise RuntimeError("installed plugin package does not match the source manifest")
        if backup.exists():
            shutil.rmtree(backup)
            moved_old = False
        return {
            "ok": True,
            "target": str(target),
            "files": len(source_manifest),
            "sha256": _manifest_digest(source_manifest),
        }
    except Exception:
        if target.exists() and installed_new:
            shutil.rmtree(target)
        if moved_old and backup.exists():
            os.replace(backup, target)
            moved_old = False
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def command_install_plugin(args: argparse.Namespace) -> None:
    emit(install_plugin_package(Path(args.plugin_source), Path(args.target_home)))


def _patch_hermes_vision_source(source: str) -> str:
    start_marker = "        enriched_parts = []\n        for path in image_paths:\n"
    end_marker = "        # Combine: vision descriptions first, then the user's original text\n"
    if source.count(start_marker) != 1 or source.count(end_marker) < 1:
        raise RuntimeError("Hermes vision patch anchors are not exact")
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    replacement = '''        concurrency = 3
        semaphore = asyncio.Semaphore(concurrency)
        vision_started = time.monotonic()

        async def analyze_one(index: int, path: str) -> tuple[int, str, bool]:
            async with semaphore:
                try:
                    logger.debug("Auto-analyzing user image index=%d", index)
                    result_json = await vision_analyze_tool(
                        image_url=path,
                        user_prompt=analysis_prompt,
                    )
                    result = json.loads(result_json)
                    if result.get("success"):
                        description = sanitize_context(result.get("analysis", ""))
                        return index, f"[Image {index + 1} analysis:\\n{description}]", False
                    return index, f"[Image {index + 1} could not be analyzed.]", True
                except Exception as exc:
                    logger.error(
                        "Vision auto-analysis error: index=%d error=%s",
                        index,
                        type(exc).__name__,
                    )
                    return index, f"[Image {index + 1} could not be analyzed.]", True

        tasks = [
            asyncio.create_task(analyze_one(index, path))
            for index, path in enumerate(image_paths)
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        enriched_parts = [""] * len(image_paths)
        failures = 0
        for default_index, item in enumerate(raw_results):
            if isinstance(item, BaseException):
                enriched_parts[default_index] = f"[Image {default_index + 1} could not be analyzed.]"
                failures += 1
                continue
            index, description, failed = item
            enriched_parts[index] = description
            failures += int(failed)
        logger.info(
            "goutoujunshi_vision_metric %s",
            json.dumps(
                {
                    "image_count": len(image_paths),
                    "vision_duration_ms": round((time.monotonic() - vision_started) * 1000, 1),
                    "concurrency": concurrency,
                    "failures": failures,
                },
                sort_keys=True,
            ),
        )

'''
    return source[:start] + replacement + source[end:]


def _replace_exact(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"Hermes patch anchor is not exact: {label}")
    return source.replace(old, new, 1)


def _patch_gateway_run_source(source: str) -> str:
    source = _patch_hermes_vision_source(source)
    source = _replace_exact(
        source,
        "import faulthandler\nimport inspect\n",
        "import faulthandler\nimport hashlib\nimport inspect\n",
        "gateway hashlib import",
    )
    source = _replace_exact(
        source,
        '''        if getattr(source, "profile_route_rejected", False) is True:
            logger.warning(
                "Dropping inbound message because its explicit profile route "
                "targets an unserved profile"
            )
            return None

        # Internal events (e.g. background-process completion notifications)
''',
        '''        if getattr(source, "profile_route_rejected", False) is True:
            logger.warning(
                "Dropping inbound message because its explicit profile route "
                "targets an unserved profile"
            )
            return None

        # Route-selected multiplex messages must run entirely inside the
        # selected profile. SessionStore and agent caches resolve their backing
        # stores from this scope, so entering later would split one turn across
        # the global and profile homes.
        if (
            getattr(getattr(self, "config", None), "multiplex_profiles", False)
            and getattr(source, "profile", None)
            and not getattr(event, "_profile_runtime_scope_active", False)
        ):
            from hermes_cli.profiles import get_profile_dir

            profile_home = get_profile_dir(str(source.profile))
            setattr(event, "_profile_runtime_scope_active", True)
            try:
                with _profile_runtime_scope(Path(profile_home)):
                    return await self._handle_message(event)
            finally:
                try:
                    delattr(event, "_profile_runtime_scope_active")
                except AttributeError:
                    pass

        # Internal events (e.g. background-process completion notifications)
''',
        "profile runtime delegation",
    )
    source = _replace_exact(
        source,
        '''        _msg_start_time = time.time()
        _platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        _msg_preview = (event.text or "")[:80].replace("\\n", " ")
        _reply_id = getattr(event, "reply_to_message_id", None)
        _reply_txt = (getattr(event, "reply_to_text", None) or "")[:80].replace("\\n", " ")
        logger.info(
            "inbound message: platform=%s user=%s chat=%s msg=%r reply_to_id=%s reply_to_text=%r",
            _platform_name, source.user_name or source.user_id or "unknown",
            source.chat_id or "unknown", _msg_preview, _reply_id, _reply_txt,
        )
''',
        '''        _msg_start_time = time.time()
        _platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        _message_text_for_log = event.text or ""
        _reply_id_for_log = str(getattr(event, "reply_to_message_id", None) or "")
        _reply_text_for_log = getattr(event, "reply_to_text", None) or ""
        _short_hash = lambda value: hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
        logger.info(
            "inbound message: platform=%s user_hash=%s chat_hash=%s chars=%d msg_hash=%s "
            "reply_id_hash=%s reply_chars=%d reply_hash=%s media=%d",
            _platform_name,
            _short_hash(source.user_id or source.user_id_alt or "unknown"),
            _short_hash(source.chat_id or "unknown"),
            len(_message_text_for_log),
            _short_hash(_message_text_for_log),
            _short_hash(_reply_id_for_log) if _reply_id_for_log else "none",
            len(_reply_text_for_log),
            _short_hash(_reply_text_for_log) if _reply_text_for_log else "none",
            len(getattr(event, "media_urls", None) or []),
        )
''',
        "gateway inbound log",
    )
    source = _replace_exact(
        source,
        '''        # Capture and immediately consume was_auto_reset so it does not
''',
        '''        if not bool(getattr(event, "internal", False)):
            _session_hook_allowed = False
            try:
                from hermes_cli.lifecycle import invoke_hook as _invoke_session_hook
                _session_hook_results = _invoke_session_hook(
                    "post_gateway_session",
                    event=event,
                    gateway=self,
                    session_store=getattr(self, "session_store", None),
                    session_entry=session_entry,
                    session_id=str(session_entry.session_id),
                    session_key=str(session_entry.session_key),
                )
            except Exception as _session_hook_exc:
                logger.warning(
                    "post_gateway_session invocation failed: %s",
                    type(_session_hook_exc).__name__,
                )
                _session_hook_results = []
            for _session_hook_result in _session_hook_results:
                if not isinstance(_session_hook_result, dict):
                    continue
                if _session_hook_result.get("action") == "skip":
                    logger.info(
                        "post_gateway_session skip: reason=%s platform=%s",
                        _session_hook_result.get("reason"),
                        source.platform.value if source.platform else "unknown",
                    )
                    return None
                if _session_hook_result.get("action") == "allow":
                    _session_hook_allowed = True
            if _platform_name == "feishu" and not _session_hook_allowed:
                logger.warning("post_gateway_session failed closed for Feishu")
                return None

        # Capture and immediately consume was_auto_reset so it does not
''',
        "post gateway session hook",
    )
    source = _replace_exact(
        source,
        '''        if not history and not await self.async_session_store.has_any_sessions():
            # Default first-contact note: a brief self-introduction.
''',
        '''        try:
            _first_message_intro = bool(
                ((_load_gateway_config().get("onboarding") or {}).get("first_message_intro", True))
            )
        except Exception:
            _first_message_intro = True
        if (
            _first_message_intro
            and not history
            and not await self.async_session_store.has_any_sessions()
        ):
            # Default first-contact note: a brief self-introduction.
''',
        "first message onboarding gate",
    )
    source = _replace_exact(
        source,
        '''            logger.info(
                "response ready: platform=%s chat=%s time=%.1fs api_calls=%d response=%d chars",
                _platform_name, source.chat_id or "unknown",
                _response_time, _api_calls, _resp_len,
            )
''',
        '''            logger.info(
                "response ready: platform=%s chat_hash=%s time=%.1fs api_calls=%d "
                "response_chars=%d response_hash=%s",
                _platform_name,
                hashlib.sha256(str(source.chat_id or "unknown").encode("utf-8")).hexdigest()[:12],
                _response_time,
                _api_calls,
                _resp_len,
                hashlib.sha256(str(response or "").encode("utf-8")).hexdigest()[:12],
            )
''',
        "gateway response log",
    )
    return source


def _patch_turn_context_source(source: str) -> str:
    source = _replace_exact(
        source,
        "import logging\nimport threading\n",
        "import hashlib\nimport logging\nimport threading\n",
        "turn context hashlib import",
    )
    source = _replace_exact(
        source,
        '''    # Log conversation turn start for debugging/observability.
    _preview_text = summarize_user_message_for_log(user_message)
    _msg_preview = (_preview_text[:80] + "...") if len(_preview_text) > 80 else _preview_text
    _msg_preview = _msg_preview.replace("\\n", " ")
    logger.info(
        "conversation turn: session=%s model=%s provider=%s platform=%s history=%d msg=%r",
        agent.session_id or "none", agent.model, agent.provider or "unknown",
        agent.platform or "unknown", len(conversation_history or []),
        _msg_preview,
    )
''',
        '''    # Log only bounded metadata; user content must never enter INFO logs.
    _message_for_log = summarize_user_message_for_log(user_message)
    logger.info(
        "conversation turn: session_hash=%s model=%s provider=%s platform=%s "
        "history=%d chars=%d msg_hash=%s",
        hashlib.sha256(str(agent.session_id or "none").encode("utf-8")).hexdigest()[:12],
        agent.model,
        agent.provider or "unknown",
        agent.platform or "unknown",
        len(conversation_history or []),
        len(_message_for_log),
        hashlib.sha256(_message_for_log.encode("utf-8")).hexdigest()[:12],
    )
''',
        "turn context conversation log",
    )
    source = _replace_exact(
        source,
        '''    if not agent.quiet_mode:
        _print_preview = summarize_user_message_for_log(user_message)
        agent._safe_print(
            f"💬 Starting conversation: '{_print_preview[:60]}"
            f"{'...' if len(_print_preview) > 60 else ''}'"
        )
''',
        '''    if not agent.quiet_mode:
        _print_text = summarize_user_message_for_log(user_message)
        _print_hash = hashlib.sha256(_print_text.encode("utf-8")).hexdigest()[:12]
        agent._safe_print(
            f"Starting conversation: chars={len(_print_text)} hash={_print_hash}"
        )
''',
        "turn context console preview",
    )
    return source


def _patch_feishu_adapter_source(source: str) -> str:
    source = _replace_exact(
        source,
        "_DEFAULT_MEDIA_BATCH_DELAY_SECONDS = 0.8\n",
        "_DEFAULT_MEDIA_BATCH_DELAY_SECONDS = 2.5\n",
        "Feishu media batch delay",
    )
    source = _replace_exact(
        source,
        '''        self._pending_media_batches = self._media_batch_state.events
        self._pending_media_batch_tasks = self._media_batch_state.tasks
''',
        '''        self._pending_media_batches = self._media_batch_state.events
        self._pending_media_batch_tasks = self._media_batch_state.tasks
        self._pending_media_batch_counts = self._media_batch_state.counts
''',
        "Feishu media batch counts",
    )
    source = _replace_exact(
        source,
        '''        self._pending_media_batches.clear()
''',
        '''        self._pending_media_batches.clear()
        self._pending_media_batch_counts.clear()
''',
        "Feishu media count reset",
    )
    source = _replace_exact(
        source,
        '''        logger.info(
            "[Feishu] Inbound %s message received: id=%s type=%s chat_id=%s sender=%s:%s text=%r media=%d",
            "dm" if chat_type == "p2p" else "group",
            message_id,
            inbound_type.value,
            getattr(message, "chat_id", "") or "",
            "bot" if is_bot else "user",
            sender_primary,
            text[:120],
            len(media_urls),
        )
''',
        '''        _log_hash = lambda value: hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
        logger.info(
            "[Feishu] Inbound %s message: id_hash=%s type=%s chat_hash=%s "
            "sender_kind=%s sender_hash=%s chars=%d text_hash=%s media=%d",
            "dm" if chat_type == "p2p" else "group",
            _log_hash(message_id),
            inbound_type.value,
            _log_hash(getattr(message, "chat_id", "") or ""),
            "bot" if is_bot else "user",
            _log_hash(sender_primary),
            len(text),
            _log_hash(text),
            len(media_urls),
        )
''',
        "Feishu inbound log",
    )
    source = _replace_exact(
        source,
        '''    async def _dispatch_inbound_event(self, event: MessageEvent) -> None:
        """Apply Feishu-specific burst protection before entering the base adapter."""
        if event.message_type == MessageType.TEXT and not event.is_command():
            await self._enqueue_text_event(event)
            return
        if self._should_batch_media_event(event):
            await self._enqueue_media_event(event)
            return
        await self._handle_message_with_guards(event)
''',
        '''    async def _dispatch_inbound_event(self, event: MessageEvent) -> None:
        """Apply Feishu-specific burst protection before entering the base adapter."""
        if event.message_type in {MessageType.TEXT, MessageType.COMMAND}:
            if await self._merge_text_into_pending_photo(event):
                return
            if event.message_type == MessageType.TEXT and not event.is_command():
                await self._enqueue_text_event(event)
                return
        if self._should_batch_media_event(event):
            await self._enqueue_media_event(event)
            return
        await self._handle_message_with_guards(event)
''',
        "Feishu dispatch media text merge",
    )
    source = _replace_exact(
        source,
        '''    @staticmethod
    def _media_batch_is_compatible(existing: MessageEvent, incoming: MessageEvent) -> bool:
        return (
            existing.message_type == incoming.message_type
            and existing.reply_to_message_id == incoming.reply_to_message_id
            and existing.reply_to_text == incoming.reply_to_text
            and existing.source.thread_id == incoming.source.thread_id
        )

    async def _enqueue_media_event(self, event: MessageEvent) -> None:
''',
        '''    @staticmethod
    def _batch_context_is_compatible(existing: MessageEvent, incoming: MessageEvent) -> bool:
        return (
            existing.source.user_id == incoming.source.user_id
            and existing.source.user_id_alt == incoming.source.user_id_alt
            and existing.reply_to_message_id == incoming.reply_to_message_id
            and existing.reply_to_text == incoming.reply_to_text
            and existing.source.thread_id == incoming.source.thread_id
        )

    @classmethod
    def _media_batch_is_compatible(cls, existing: MessageEvent, incoming: MessageEvent) -> bool:
        return (
            existing.message_type == incoming.message_type
            and cls._batch_context_is_compatible(existing, incoming)
        )

    async def _merge_text_into_pending_photo(self, event: MessageEvent) -> bool:
        key = f"{self._text_batch_key(event)}:media:{MessageType.PHOTO.value}"
        existing = self._pending_media_batches.get(key)
        if existing is None:
            return False
        if event.is_command() or not self._batch_context_is_compatible(existing, event):
            await self._flush_media_batch_now(key)
            return False
        next_count = self._pending_media_batch_counts.get(key, 1) + 1
        next_text = self._merge_caption(existing.text, event.text or "")
        if next_count > self._text_batch_max_messages or len(next_text) > self._text_batch_max_chars:
            await self._flush_media_batch_now(key)
            return False
        existing.text = next_text
        existing.timestamp = event.timestamp
        if event.message_id:
            existing.message_id = event.message_id
        self._pending_media_batch_counts[key] = next_count
        self._schedule_media_batch_flush(key)
        return True

    async def _enqueue_media_event(self, event: MessageEvent) -> None:
''',
        "Feishu media compatibility and text merge",
    )
    source = _replace_exact(
        source,
        '''        if existing is None:
            self._pending_media_batches[key] = event
            self._schedule_media_batch_flush(key)
            return
        if not self._media_batch_is_compatible(existing, event):
            await self._flush_media_batch_now(key)
            self._pending_media_batches[key] = event
            self._schedule_media_batch_flush(key)
            return
        existing.media_urls.extend(event.media_urls)
''',
        '''        if existing is None:
            self._pending_media_batches[key] = event
            self._pending_media_batch_counts[key] = 1
            self._schedule_media_batch_flush(key)
            return
        if not self._media_batch_is_compatible(existing, event):
            await self._flush_media_batch_now(key)
            self._pending_media_batches[key] = event
            self._pending_media_batch_counts[key] = 1
            self._schedule_media_batch_flush(key)
            return
        next_count = self._pending_media_batch_counts.get(key, 1) + 1
        next_text = self._merge_caption(existing.text, event.text) if event.text else existing.text
        if next_count > self._text_batch_max_messages or len(next_text or "") > self._text_batch_max_chars:
            await self._flush_media_batch_now(key)
            self._pending_media_batches[key] = event
            self._pending_media_batch_counts[key] = 1
            self._schedule_media_batch_flush(key)
            return
        existing.media_urls.extend(event.media_urls)
''',
        "Feishu media enqueue counts",
    )
    source = _replace_exact(
        source,
        '''        if event.text:
            existing.text = self._merge_caption(existing.text, event.text)
        existing.timestamp = event.timestamp
''',
        '''        if event.text:
            existing.text = next_text
        existing.timestamp = event.timestamp
''',
        "Feishu media merged caption",
    )
    source = _replace_exact(
        source,
        '''        if event.message_id:
            existing.message_id = event.message_id
        self._schedule_media_batch_flush(key)
''',
        '''        if event.message_id:
            existing.message_id = event.message_id
        self._pending_media_batch_counts[key] = next_count
        self._schedule_media_batch_flush(key)
''',
        "Feishu media merge reschedule",
    )
    source = _replace_exact(
        source,
        '''        event = self._pending_media_batches.pop(key, None)
        if not event:
            return
        logger.info(
            "[Feishu] Flushing media batch %s with %d attachment(s)",
            key,
            len(event.media_urls),
        )
''',
        '''        event = self._pending_media_batches.pop(key, None)
        self._pending_media_batch_counts.pop(key, None)
        if not event:
            return
        logger.info(
            "[Feishu] Flushing media batch key_hash=%s attachments=%d chars=%d",
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
            len(event.media_urls),
            len(event.text or ""),
        )
''',
        "Feishu media flush log",
    )
    source = _replace_exact(
        source,
        '''        logger.info(
            "[Feishu] Flushing text batch %s (%d chars)",
            key,
            len(event.text or ""),
        )
''',
        '''        logger.info(
            "[Feishu] Flushing text batch key_hash=%s chars=%d text_hash=%s",
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
            len(event.text or ""),
            hashlib.sha256((event.text or "").encode("utf-8")).hexdigest()[:12],
        )
''',
        "Feishu text flush log",
    )
    source = _replace_exact(
        source,
        '''        logger.info("[Feishu] Received raw message type=%s message_id=%s", raw_type, message_id)
''',
        '''        logger.info(
            "[Feishu] Received raw message type=%s message_id_hash=%s",
            raw_type,
            hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:12],
        )
''',
        "Feishu raw inbound log",
    )
    for label, old, media_kind in (
        ("cached image log", 'logger.info("[Feishu] Cached message image resource at %s", cached_path)', "image"),
        ("cached audio log", 'logger.info("[Feishu] Cached message audio resource at %s", cached_path)', "audio"),
        ("cached video log", 'logger.info("[Feishu] Cached message video resource at %s", cached_path)', "video"),
        ("cached document log", 'logger.info("[Feishu] Cached message document resource at %s", cached_path)', "document"),
    ):
        source = _replace_exact(
            source,
            old,
            f'logger.info("[Feishu] Cached message {media_kind} resource bytes=%d", len(raw_bytes))',
            f"Feishu {label}",
        )
    return source


def _patch_hermes_logging_source(source: str) -> str:
    source = _replace_exact(
        source,
        '''    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
''',
        '''    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
''',
        "Hermes log directory permissions",
    )
    source = _replace_exact(
        source,
        '''    def _chmod_if_managed(self):
        if self._managed:
            try:
                os.chmod(self.baseFilename, 0o660)
            except OSError:
                pass
''',
        '''    def _chmod_if_managed(self):
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
''',
        "Hermes log file permissions",
    )
    source = _replace_exact(
        source,
        '''    path.parent.mkdir(parents=True, exist_ok=True)
    handler = _ManagedRotatingFileHandler(
''',
        '''    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    handler = _ManagedRotatingFileHandler(
''',
        "Hermes rotating handler directory permissions",
    )
    return source


HERMES_RUNTIME_PATCH_TRANSFORMS = {
    "gateway/run.py": _patch_gateway_run_source,
    "agent/turn_context.py": _patch_turn_context_source,
    "plugins/platforms/feishu/adapter.py": _patch_feishu_adapter_source,
    "hermes_logging.py": _patch_hermes_logging_source,
}


def _hermes_version(agent_root: Path) -> str:
    version_file = agent_root / "hermes_cli" / "__init__.py"
    version_match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        version_file.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    return version_match.group(1) if version_match else "unknown"


def install_hermes_runtime_patch(
    agent_root: Path,
    backup_dir: Path,
    *,
    expected_version: str = HERMES_RUNTIME_PATCH_VERSION,
    expected_files: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    agent_root = agent_root.resolve()
    backup_dir = backup_dir.resolve()
    expected_files = expected_files or HERMES_RUNTIME_PATCH_FILES
    if set(expected_files) != set(HERMES_RUNTIME_PATCH_TRANSFORMS):
        raise RuntimeError("Hermes runtime patch file set mismatch")
    actual_version = _hermes_version(agent_root)
    if actual_version != expected_version:
        raise RuntimeError(
            f"Hermes version mismatch: expected {expected_version}, found {actual_version}"
        )

    current: dict[str, str] = {}
    states: set[str] = set()
    for relative_path, hashes in expected_files.items():
        target = agent_root / relative_path
        digest = _sha256_file(target)
        current[relative_path] = digest
        if digest == hashes["source_sha256"]:
            states.add("source")
        elif digest == hashes["patched_sha256"]:
            states.add("patched")
        else:
            raise RuntimeError(
                f"Hermes source SHA256 mismatch for {relative_path}: found {digest}"
            )
    if states == {"patched"}:
        return {
            "ok": True,
            "status": "already_patched",
            "version": actual_version,
            "files": current,
        }
    if states != {"source"}:
        raise RuntimeError("Hermes runtime patch is in a mixed partial state")

    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    patched_bytes_by_path: dict[str, bytes] = {}
    replaced: list[str] = []
    try:
        for relative_path, hashes in expected_files.items():
            target = agent_root / relative_path
            patched = HERMES_RUNTIME_PATCH_TRANSFORMS[relative_path](
                target.read_text(encoding="utf-8")
            ).encode("utf-8")
            built_digest = hashlib.sha256(patched).hexdigest()
            if built_digest != hashes["patched_sha256"]:
                raise RuntimeError(
                    f"Hermes patched SHA256 mismatch for {relative_path}: "
                    f"expected {hashes['patched_sha256']}, built {built_digest}"
                )
            patched_bytes_by_path[relative_path] = patched
            safe_name = relative_path.replace("/", "__")
            backup = backup_dir / f"{safe_name}.{hashes['source_sha256']}.bak"
            if backup.exists() and _sha256_file(backup) != hashes["source_sha256"]:
                raise RuntimeError(f"Hermes patch backup SHA256 mismatch: {backup}")
            if not backup.exists():
                shutil.copy2(target, backup)
                os.chmod(backup, 0o400)
            backups[relative_path] = backup
            staging = target.with_name(
                f".{target.name}.goutoujunshi-{secrets.token_hex(6)}.tmp"
            )
            staging.write_bytes(patched)
            if _sha256_file(staging) != hashes["patched_sha256"]:
                raise RuntimeError(f"staged Hermes runtime patch failed: {relative_path}")
            staged[relative_path] = staging

        for relative_path in expected_files:
            target = agent_root / relative_path
            os.replace(staged[relative_path], target)
            replaced.append(relative_path)
        for relative_path, hashes in expected_files.items():
            if _sha256_file(agent_root / relative_path) != hashes["patched_sha256"]:
                raise RuntimeError(f"installed Hermes runtime patch failed: {relative_path}")
    except Exception:
        for relative_path in reversed(replaced):
            target = agent_root / relative_path
            restore = target.with_name(
                f".{target.name}.goutoujunshi-restore-{secrets.token_hex(6)}.tmp"
            )
            shutil.copy2(backups[relative_path], restore)
            os.chmod(restore, 0o644)
            os.replace(restore, target)
        raise
    finally:
        for staging in staged.values():
            staging.unlink(missing_ok=True)
    return {
        "ok": True,
        "status": "patched",
        "version": actual_version,
        "files": {
            relative_path: {
                "target": str(agent_root / relative_path),
                "backup": str(backups[relative_path]),
                "sha256": expected_files[relative_path]["patched_sha256"],
            }
            for relative_path in expected_files
        },
    }


def inspect_hermes_runtime_patch(
    agent_root: Path,
    *,
    expected_files: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    agent_root = agent_root.resolve()
    expected_files = expected_files or HERMES_RUNTIME_PATCH_FILES
    files: dict[str, dict[str, str]] = {}
    for relative_path, hashes in expected_files.items():
        target = agent_root / relative_path
        source_digest = _sha256_file(target)
        if source_digest == hashes["source_sha256"]:
            built = HERMES_RUNTIME_PATCH_TRANSFORMS[relative_path](
                target.read_text(encoding="utf-8")
            ).encode("utf-8")
            built_digest = hashlib.sha256(built).hexdigest()
            status = "source"
        elif source_digest == hashes["patched_sha256"]:
            built_digest = source_digest
            status = "patched"
        else:
            built_digest = ""
            status = "unknown"
        files[relative_path] = {
            "status": status,
            "source_sha256": source_digest,
            "built_patched_sha256": built_digest,
            "expected_patched_sha256": hashes["patched_sha256"],
        }
    return {
        "ok": True,
        "version": _hermes_version(agent_root),
        "files": files,
    }


def install_hermes_vision_patch(agent_root: Path, backup_dir: Path, **kwargs: Any) -> dict[str, Any]:
    if "expected_sha256" in kwargs or "expected_patched_sha256" in kwargs:
        raise TypeError("single-file patch overrides are no longer supported")
    return install_hermes_runtime_patch(agent_root, backup_dir, **kwargs)


def inspect_hermes_vision_patch(agent_root: Path) -> dict[str, Any]:
    return inspect_hermes_runtime_patch(agent_root)


def command_install_hermes_vision_patch(args: argparse.Namespace) -> None:
    emit(
        install_hermes_runtime_patch(
            Path(args.agent_root),
            Path(args.backup_dir),
        )
    )


def command_inspect_hermes_vision_patch(args: argparse.Namespace) -> None:
    emit(inspect_hermes_runtime_patch(Path(args.agent_root)))


def _running_pid_from_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        payload = json.loads(raw)
        pid = int(payload.get("pid")) if isinstance(payload, dict) else int(payload)
        if pid <= 0:
            return None
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ProcessLookupError, ValueError, TypeError, json.JSONDecodeError):
        return None
    except PermissionError:
        raise RuntimeError("cannot verify whether the Hermes Gateway process is stopped")


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(6)}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def rotate_profile_sessions(
    agent_root: Path,
    global_home: Path,
    profile_home: Path,
    *,
    profile_name: str = "goutoujunshi",
    reason: str = PROFILE_SESSION_MIGRATION_REASON,
    expected_session_sha256: str = HERMES_SESSION_SHA256,
    session_db_class: Any = None,
    session_entry_class: Any = None,
) -> dict[str, Any]:
    """End profile Feishu transcripts and remove only their active routes."""
    agent_root = agent_root.resolve()
    global_home = global_home.resolve()
    profile_home = profile_home.resolve()
    if _hermes_version(agent_root) != HERMES_RUNTIME_PATCH_VERSION:
        raise RuntimeError(
            f"Hermes version mismatch: expected {HERMES_RUNTIME_PATCH_VERSION}, "
            f"found {_hermes_version(agent_root)}"
        )
    session_source = agent_root / "gateway" / "session.py"
    actual_session_sha = _sha256_file(session_source)
    if actual_session_sha != expected_session_sha256:
        raise RuntimeError(
            "Hermes gateway/session.py SHA256 mismatch: "
            f"expected {expected_session_sha256}, found {actual_session_sha}"
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile_name):
        raise ValueError("invalid Hermes profile name")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", reason):
        raise ValueError("invalid profile session migration reason")
    expected_profile_home = global_home / "profiles" / profile_name
    if profile_home != expected_profile_home.resolve():
        raise ValueError("profile home does not match the selected global Hermes home")
    running_pid = _running_pid_from_file(global_home / "gateway.pid")
    if running_pid is not None:
        raise RuntimeError(
            f"Hermes Gateway PID {running_pid} is still running; stop it before session rotation"
        )

    if session_db_class is None or session_entry_class is None:
        sys.path.insert(0, str(agent_root))
        from gateway.session import SessionEntry
        from hermes_state import SessionDB

        session_db_class = session_db_class or SessionDB
        session_entry_class = session_entry_class or SessionEntry

    prefix = f"agent:{profile_name}:feishu:"
    homes = (global_home, profile_home)
    scopes = tuple(
        dict.fromkeys(
            [
                "",
                str((global_home / "sessions").resolve()),
                str((profile_home / "sessions").resolve()),
            ]
        )
    )
    ended_session_ids: set[str] = set()
    removed_route_keys: set[str] = set()
    promoted_pairs: set[tuple[Path, str]] = set()
    stores_checked = 0

    for home in homes:
        db_path = home / "state.db"
        if not db_path.is_file():
            continue
        stores_checked += 1
        db = session_db_class(db_path=db_path)
        try:
            for scope in scopes:
                routing = db.load_gateway_routing_entries(scope=scope)
                matched_keys: list[str] = []
                for session_key, entry_json in routing.items():
                    if not str(session_key).startswith(prefix):
                        continue
                    entry = session_entry_class.from_dict(json.loads(entry_json))
                    session_id = str(entry.session_id)
                    if session_id and (db_path, session_id) not in promoted_pairs:
                        before = db.get_session(session_id)
                        if not before:
                            raise RuntimeError(
                                f"Hermes transcript is missing for profile route: {session_id}"
                            )
                        if before.get("end_reason") != reason:
                            promoted = db.promote_to_session_reset(session_id, reason)
                            after = db.get_session(session_id)
                        else:
                            promoted = True
                            after = before
                        if not promoted or not after or after.get("end_reason") != reason:
                            raise RuntimeError(
                                f"failed to end Hermes profile session with migration reason: {session_id}"
                            )
                        ended_session_ids.add(session_id)
                        promoted_pairs.add((db_path, session_id))
                    matched_keys.append(str(session_key))
                    removed_route_keys.add(str(session_key))
                if matched_keys:
                    db.delete_gateway_routing_entries(matched_keys, scope=scope)
        finally:
            db.close()

    for home in homes:
        sessions_file = home / "sessions" / "sessions.json"
        if not sessions_file.is_file():
            continue
        payload = json.loads(sessions_file.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"invalid Hermes sessions mirror: {sessions_file}")
        matched_keys: list[str] = []
        session_ids: set[str] = set()
        for session_key, entry_data in payload.items():
            if str(session_key).startswith(prefix):
                if isinstance(entry_data, dict):
                    entry = session_entry_class.from_dict(entry_data)
                    session_id = str(entry.session_id)
                    if session_id:
                        session_ids.add(session_id)
                matched_keys.append(str(session_key))
        if not matched_keys:
            continue

        # A mirror can contain the only remaining route for a transcript. End
        # every referenced session before publishing a mirror without routes,
        # so a failed promotion leaves the operation safely retryable.
        db_path = home / "state.db"
        pending = sorted(
            session_id
            for session_id in session_ids
            if (db_path, session_id) not in promoted_pairs
        )
        if pending:
            if not db_path.is_file():
                raise RuntimeError(
                    f"Hermes session store is missing for mirrored profile routes: {db_path}"
                )
            db = session_db_class(db_path=db_path)
            try:
                for session_id in pending:
                    before = db.get_session(session_id)
                    if not before:
                        raise RuntimeError(
                            f"Hermes transcript is missing for mirrored profile route: {session_id}"
                        )
                    if before.get("end_reason") != reason:
                        promoted = db.promote_to_session_reset(session_id, reason)
                        after = db.get_session(session_id)
                    else:
                        promoted = True
                        after = before
                    if not promoted or not after or after.get("end_reason") != reason:
                        raise RuntimeError(
                            f"failed to end Hermes profile session with migration reason: {session_id}"
                        )
                    ended_session_ids.add(session_id)
                    promoted_pairs.add((db_path, session_id))
            finally:
                db.close()

        for session_key in matched_keys:
            payload.pop(session_key, None)
            removed_route_keys.add(session_key)
        _atomic_private_json(sessions_file, payload)

    return {
        "ok": True,
        "profile": profile_name,
        "reason": reason,
        "stores_checked": stores_checked,
        "ended_sessions": len(ended_session_ids),
        "removed_routes": len(removed_route_keys),
        "session_source_sha256": actual_session_sha,
    }


def command_rotate_profile_sessions(args: argparse.Namespace) -> None:
    emit(
        rotate_profile_sessions(
            Path(args.agent_root),
            Path(args.global_home),
            Path(args.profile_home),
            profile_name=args.profile_name,
            reason=args.reason,
        )
    )


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_dotenv(path: Path, updates: dict[str, str], removals: set[str] | None = None) -> None:
    removals = removals or set()
    existing = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    written: set[str] = set()
    output: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in removals:
            continue
        if key in updates:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    os.chmod(path, 0o600)


def _write_restricted_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def prepare_server_secrets(source_env: Path, output_dir: Path) -> dict[str, Any]:
    source = load_dotenv(source_env)
    missing = [key for key in SERVER_SECRET_SOURCE_KEYS if not source.get(key)]
    if missing:
        raise RuntimeError(f"server secret source is missing required keys: {','.join(missing)}")
    for key in SERVER_SECRET_SOURCE_KEYS:
        if "\n" in source[key] or "\r" in source[key]:
            raise RuntimeError(f"server secret value contains a newline: {key}")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    app_password_path = output_dir / "mysql-app-password"
    root_password_path = output_dir / "mysql-root-password"
    app_password = (
        app_password_path.read_text(encoding="utf-8").strip()
        if app_password_path.exists()
        else secrets.token_urlsafe(48)
    )
    root_password = (
        root_password_path.read_text(encoding="utf-8").strip()
        if root_password_path.exists()
        else secrets.token_urlsafe(48)
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", app_password):
        raise RuntimeError("existing MySQL app password has an unsafe format")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", root_password):
        raise RuntimeError("existing MySQL root password has an unsafe format")

    hermes_values = {key: source[key] for key in SERVER_SECRET_SOURCE_KEYS}
    hermes_values.update(
        {
            "FEISHU_ALLOW_ALL_USERS": "false",
            "GOUTOUJUNSHI_DB_HOST": "mysql",
            "GOUTOUJUNSHI_DB_PORT": "3306",
            "GOUTOUJUNSHI_DB_NAME": "goutoujunshi",
            "GOUTOUJUNSHI_DB_USER": "goutoujunshi_app",
            "GOUTOUJUNSHI_DB_PASSWORD": app_password,
            "GOUTOUJUNSHI_EXPORT_ROOT": "/opt/data/relationships",
            "WEB_TOOLS_DEBUG": "false",
        }
    )
    env_content = "".join(f"{key}={value}\n" for key, value in sorted(hermes_values.items()))
    _write_restricted_text(output_dir / "hermes.env", env_content)
    _write_restricted_text(app_password_path, app_password + "\n")
    _write_restricted_text(root_password_path, root_password + "\n")
    return {
        "ok": True,
        "output": str(output_dir),
        "hermes_keys": len(hermes_values),
        "files": ["hermes.env", "mysql-app-password", "mysql-root-password"],
    }


def command_prepare_server_secrets(args: argparse.Namespace) -> None:
    emit(prepare_server_secrets(Path(args.source_env), Path(args.output_dir)))


def find_feishu_owner(sessions_file: Path) -> str:
    payload = json.loads(sessions_file.read_text(encoding="utf-8-sig"))
    owners: set[str] = set()
    entries = payload.values() if isinstance(payload, dict) else payload
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin") or {}
        if str(origin.get("platform", "")).lower() == "feishu" and origin.get("user_id"):
            owners.add(str(origin["user_id"]))
    if len(owners) != 1:
        raise RuntimeError(f"expected exactly one historical Feishu owner, found {len(owners)}")
    return next(iter(owners))


def command_prepare_secrets(args: argparse.Namespace) -> None:
    auth = json.loads(Path(args.codex_auth).read_text(encoding="utf-8-sig"))
    codex_config = tomllib.loads(Path(args.codex_config).read_text(encoding="utf-8-sig"))
    provider_name = str(codex_config.get("model_provider") or "")
    provider_config = (codex_config.get("model_providers") or {}).get(provider_name) or {}
    source_model = str(codex_config.get("model") or "").strip()
    base_url = str(provider_config.get("base_url") or "").strip().rstrip("/")
    wire_api = str(provider_config.get("wire_api") or "").strip().lower()
    if not source_model.startswith("gpt-") or not base_url or wire_api != "responses":
        raise RuntimeError("Codex is not configured with a GPT Responses provider")
    api_key = str(os.environ.get("OPENAI_API_KEY") or auth.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Codex auth.json does not contain OPENAI_API_KEY")
    env_path = Path(args.env)
    existing = load_dotenv(env_path)
    owner = find_feishu_owner(Path(args.sessions))
    updates = {
        "OPENAI_API_KEY": api_key,
        "FEISHU_ALLOW_ALL_USERS": "false",
        "FEISHU_ALLOWED_USERS": owner,
        "GOUTOUJUNSHI_OWNER_ID": owner,
        "GOUTOUJUNSHI_DB_HOST": "127.0.0.1",
        "GOUTOUJUNSHI_DB_PORT": "3306",
        "GOUTOUJUNSHI_DB_NAME": "goutoujunshi",
        "GOUTOUJUNSHI_DB_USER": "goutoujunshi_app",
        "GOUTOUJUNSHI_DB_PASSWORD": existing.get("GOUTOUJUNSHI_DB_PASSWORD") or secrets.token_urlsafe(36),
        "GOUTOUJUNSHI_EXPORT_ROOT": str(Path(args.export_root).resolve()).replace("\\", "/"),
        "GOUTOUJUNSHI_OPENAI_BASE_URL": base_url,
        "GOUTOUJUNSHI_MODEL": TARGET_MODEL,
        "GOUTOUJUNSHI_REASONING": TARGET_REASONING,
    }
    update_dotenv(env_path, updates, removals=LEGACY_VECTOR_ENV_KEYS)
    emit(
        {
            "ok": True,
            "owner_candidates": 1,
            "api_key_migrated": True,
            "api_key_source": "process_environment" if os.environ.get("OPENAI_API_KEY") else "codex_auth",
            "model": TARGET_MODEL,
            "reasoning": TARGET_REASONING,
            "wire_api": wire_api,
            "other_provider_credentials_preserved": True,
        }
    )


def _responses_request(
    api_key: str, base_url: str, model: str, reasoning: str, *, image: bool = False, tool: bool = False
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": "Reply with OK."}]
    if image:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        scanlines = b"".join(b"\x00" + b"\x20\x80\xc0\xff" * 64 for _ in range(64))
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines))
            + chunk(b"IEND", b"")
        )
        png = base64.b64encode(png_bytes).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{png}"})
    payload: dict[str, Any] = {
        "model": model,
        "reasoning": {"effort": reasoning},
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": 128,
    }
    if tool:
        payload["input"] = "Call preflight_echo with value set to OK."
        payload["tools"] = [
            {
                "type": "function",
                "name": "preflight_echo",
                "description": "Return a fixed preflight value.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ]
        payload["tool_choice"] = {"type": "function", "name": "preflight_echo"}
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers={"User-Agent": CODEX_USER_AGENT},
        max_retries=1,
        timeout=180,
    )
    try:
        response = client.responses.create(**payload)
        return response.model_dump()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        detail = ""
        if isinstance(body, dict):
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            pieces = [error.get("code"), error.get("type"), error.get("message"), error.get("detail")]
            detail = " ".join(
                str(piece).replace("\r", " ").replace("\n", " ") for piece in pieces if piece
            )[:240]
        raise RuntimeError(
            f"OpenAI preflight failed for {model}: HTTP {status or 'unknown'} {detail}".strip()
        ) from exc


def command_preflight(args: argparse.Namespace) -> None:
    values = load_dotenv(Path(args.env))
    key = values.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    model = values.get("GOUTOUJUNSHI_MODEL", "")
    reasoning = values.get("GOUTOUJUNSHI_REASONING", "")
    base_url = values.get("GOUTOUJUNSHI_OPENAI_BASE_URL", "").rstrip("/")
    if not model or not reasoning or not base_url:
        raise RuntimeError("Codex model provider settings are incomplete")
    try:
        _responses_request(key, base_url, model, reasoning)
    except Exception as exc:
        raise RuntimeError(f"text check failed: {exc}") from exc
    try:
        _responses_request(key, base_url, model, reasoning, image=True)
    except Exception as exc:
        raise RuntimeError(f"image check failed: {exc}") from exc
    try:
        tool_response = _responses_request(key, base_url, model, reasoning, tool=True)
    except Exception as exc:
        raise RuntimeError(f"function calling check failed: {exc}") from exc
    if not any(item.get("type") == "function_call" for item in tool_response.get("output") or []):
        raise RuntimeError("Responses preflight did not return the required function call")
    emit(
        {
            "ok": True,
            "model": model,
            "text": True,
            "image_input": True,
            "function_calling": True,
            "reasoning": reasoning,
        }
    )


def _configure_model(config: dict[str, Any], values: dict[str, str]) -> None:
    model_name = values["GOUTOUJUNSHI_MODEL"]
    base_url = values["GOUTOUJUNSHI_OPENAI_BASE_URL"].rstrip("/")
    reasoning = values["GOUTOUJUNSHI_REASONING"]
    model = config.setdefault("model", {})
    model.update(
        {
            "provider": PROVIDER,
            "default": model_name,
            "base_url": base_url,
            "openai_runtime": "auto",
        }
    )
    config.setdefault("agent", {})["reasoning_effort"] = reasoning
    config["fallback_providers"] = []
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    route = providers.get(CODEX_ROUTE)
    if not isinstance(route, dict):
        route = {}
        providers[CODEX_ROUTE] = route
    route.update(
        {
            "name": "Codex GPT endpoint",
            "base_url": base_url,
            "key_env": "OPENAI_API_KEY",
            "api_mode": "responses",
            "model": model_name,
            "discover_models": True,
            "extra_headers": {"User-Agent": CODEX_USER_AGENT},
        }
    )


def _configure_vision(config: dict[str, Any], values: dict[str, str]) -> None:
    """Route OCR/image analysis through the same working Responses endpoint."""
    model = config.setdefault("model", {})
    default_headers = model.get("default_headers")
    if not isinstance(default_headers, dict):
        default_headers = {}
        model["default_headers"] = default_headers
    default_headers["User-Agent"] = CODEX_USER_AGENT
    vision = config.setdefault("auxiliary", {}).setdefault("vision", {})
    vision.update(
        {
            "provider": CODEX_ROUTE,
            "model": values["GOUTOUJUNSHI_MODEL"],
            "base_url": "",
            "api_key": "",
            "api_mode": "codex_responses",
            "reasoning_effort": "low",
            "timeout": 120,
            "download_timeout": 30,
        }
    )


def _configure_compression(config: dict[str, Any]) -> None:
    compression = config.setdefault("compression", {})
    compression.update(
        {
            "enabled": True,
            "threshold_tokens": 64000,
            "proactive_prune_tokens": 48000,
            "min_tail_user_messages": 3,
            "abort_on_summary_failure": True,
            "micro_compact": False,
        }
    )


def _atomic_yaml(path: Path, config: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(temp, path)


def _configure_gateway_display(config: dict[str, Any]) -> None:
    display = config.setdefault("display", {})
    display["busy_input_mode"] = "queue"
    display["tool_progress"] = "off"
    display["interim_assistant_messages"] = False
    display["busy_ack_detail"] = False
    feishu = display.setdefault("platforms", {}).setdefault("feishu", {})
    feishu["tool_progress"] = "off"
    feishu["interim_assistant_messages"] = False
    feishu["busy_ack_detail"] = False


def _configure_known_plugin_toolsets(config: dict[str, Any]) -> list[str]:
    known_map = config.get("known_plugin_toolsets")
    if not isinstance(known_map, dict):
        known_map = {}
        config["known_plugin_toolsets"] = known_map
    existing = known_map.get("feishu")
    known = {str(item) for item in existing} if isinstance(existing, list) else set()
    known.update(PROJECT_PLUGIN_TOOLSETS)
    known_map["feishu"] = sorted(known)
    return known_map["feishu"]


def _merge_disabled_toolsets(config: dict[str, Any], required: list[str]) -> list[str]:
    agent = config.setdefault("agent", {})
    existing = agent.get("disabled_toolsets")
    merged = [str(item) for item in existing] if isinstance(existing, list) else []
    for toolset in required:
        if toolset not in merged:
            merged.append(toolset)
    agent["disabled_toolsets"] = merged
    return merged


def _resolved_hermes_toolsets(config: dict[str, Any], platform: str) -> list[str]:
    from hermes_cli.tools_config import _get_platform_tools

    return sorted(_get_platform_tools(config, platform))


def _ddgs_importable() -> bool:
    try:
        import ddgs  # noqa: F401
    except Exception:
        return False
    return True


def command_configure_global(args: argparse.Namespace) -> None:
    path = Path(args.config)
    values = load_dotenv(Path(args.source_env))
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _configure_model(config, values)
    _configure_vision(config, values)
    _configure_compression(config)
    plugins = config.setdefault("plugins", {})
    enabled = list(plugins.get("enabled") or [])
    if "goutoujunshi" not in enabled:
        enabled.append("goutoujunshi")
    plugins["enabled"] = enabled
    plugins["disabled"] = [
        str(item) for item in (plugins.get("disabled") or []) if str(item) != PLUGIN_NAME
    ]
    platform_toolsets = config.setdefault("platform_toolsets", {})
    feishu_toolsets = [USER_TOOLSET]
    platform_toolsets["feishu"] = feishu_toolsets
    known_plugin_toolsets = _configure_known_plugin_toolsets(config)
    disabled_toolsets = _merge_disabled_toolsets(config, GLOBAL_DISABLED_TOOLSETS)
    feishu = config.setdefault("platforms", {}).setdefault("feishu", {})
    feishu["enabled"] = True
    feishu["require_mention"] = False
    extra = feishu.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        feishu["extra"] = extra
    extra["require_mention"] = False
    extra["group_policy"] = "allowlist"
    extra["default_group_policy"] = "allowlist"
    _configure_gateway_display(config)
    _atomic_yaml(path, config)
    emit(
        {
            "ok": True,
            "model": values["GOUTOUJUNSHI_MODEL"],
            "provider": PROVIDER,
            "reasoning": values["GOUTOUJUNSHI_REASONING"],
            "fallback_count": 0,
            "require_mention": False,
            "feishu_toolsets": feishu_toolsets,
            "known_plugin_toolsets": known_plugin_toolsets,
            "disabled_toolsets": disabled_toolsets,
        }
    )


def command_configure_profile(args: argparse.Namespace) -> None:
    profile_home = Path(args.profile_home)
    profile_home.mkdir(parents=True, exist_ok=True)
    config_path = profile_home / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config = config or {}
    global_values = load_dotenv(Path(args.global_env))
    _configure_model(config, global_values)
    _configure_vision(config, global_values)
    _configure_compression(config)
    agent = config.setdefault("agent", {})
    agent["disabled_toolsets"] = list(PROFILE_DISABLED_TOOLSETS)
    config.setdefault("memory", {})["enabled"] = False
    config.setdefault("tools", {})["tool_search"] = False
    config.setdefault("web", {})["search_backend"] = "ddgs"
    config.setdefault("onboarding", {})["first_message_intro"] = False
    profile_toolsets = [RELATIONSHIP_TOOLSET, USER_TOOLSET]
    config.setdefault("platform_toolsets", {})["feishu"] = profile_toolsets
    _configure_known_plugin_toolsets(config)
    config.setdefault("platforms", {}).setdefault("feishu", {})["enabled"] = False
    plugins = config.setdefault("plugins", {})
    plugins["enabled"] = [PLUGIN_NAME]
    plugins["disabled"] = [
        str(item) for item in (plugins.get("disabled") or []) if str(item) != PLUGIN_NAME
    ]
    _atomic_yaml(config_path, config)

    allowed_keys = {
        "OPENAI_API_KEY",
        "GOUTOUJUNSHI_OWNER_ID",
        "GOUTOUJUNSHI_DB_HOST",
        "GOUTOUJUNSHI_DB_PORT",
        "GOUTOUJUNSHI_DB_NAME",
        "GOUTOUJUNSHI_DB_USER",
        "GOUTOUJUNSHI_DB_PASSWORD",
        "GOUTOUJUNSHI_EXPORT_ROOT",
        "GOUTOUJUNSHI_OPENAI_BASE_URL",
        "GOUTOUJUNSHI_MODEL",
        "GOUTOUJUNSHI_REASONING",
    }
    update_dotenv(
        profile_home / ".env",
        {
            **{key: global_values[key] for key in allowed_keys if key in global_values},
            "WEB_TOOLS_DEBUG": "false",
        },
        removals=LEGACY_VECTOR_ENV_KEYS,
    )
    emit(
        {
            "ok": True,
            "profile": "goutoujunshi",
            "toolsets": profile_toolsets,
            "memory": False,
            "search_backend": "ddgs",
            "web_tools_debug": False,
        }
    )


def command_configure_vision(args: argparse.Namespace) -> None:
    path = Path(args.config)
    values = load_dotenv(Path(args.source_env))
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _configure_vision(config, values)
    _atomic_yaml(path, config)
    emit(
        {
            "ok": True,
            "provider": CODEX_ROUTE,
            "model": values["GOUTOUJUNSHI_MODEL"],
            "api_mode": "codex_responses",
        }
    )


def _installed_runtime_packages(
    project_root: Path,
    global_home: Path,
    profile_home: Path,
) -> dict[str, Any]:
    skill_source = _runtime_manifest(project_root)
    plugin_source = _plugin_manifest(project_root / "runtime" / PLUGIN_NAME)
    result: dict[str, Any] = {}
    for label, home in (("global", global_home), ("profile", profile_home)):
        skill_target = home / "skills" / SKILL_NAME
        plugin_target = home / "plugins" / PLUGIN_NAME
        result[label] = {
            "skill_matches": skill_target.is_dir()
            and _runtime_manifest(skill_target) == skill_source,
            "plugin_matches": plugin_target.is_dir()
            and _plugin_manifest(plugin_target) == plugin_source,
            "skill_files": len(_runtime_manifest(skill_target)) if skill_target.is_dir() else 0,
            "plugin_files": len(_plugin_manifest(plugin_target)) if plugin_target.is_dir() else 0,
        }
    return result


def _profile_runtime_probe(profile_home: Path) -> dict[str, Any]:
    expected_tools = sorted(
        (
            "relationship_commit_turn",
            "relationship_search_events",
            "relationship_web_search",
            "user_memory_remember",
            "user_memory_correct",
            "user_memory_forget",
        )
    )
    probe = r'''
import hashlib
import json
from hermes_cli.plugins import _ensure_plugins_discovered
from tools.registry import registry
from toolsets import resolve_toolset
from agent.skill_commands import _load_skill_payload

manager = _ensure_plugins_discovered(force=True)
loaded = manager._plugins.get("goutoujunshi")
names = %r
schemas = {name: registry.get_schema(name) for name in names}
payload = _load_skill_payload("goutoujunshi", task_id="wing-dog-verify")
print("WING_DOG_PROBE=" + json.dumps({
    "plugin_discovered": bool(loaded and loaded.enabled and not loaded.error),
    "plugin_error": str(loaded.error) if loaded and loaded.error else "",
    "tools": sorted(name for name, schema in schemas.items() if isinstance(schema, dict)),
    "tool_schema_sha256": hashlib.sha256(
        json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
    "relationship_toolset": sorted(resolve_toolset("goutoujunshi")),
    "user_toolset": sorted(resolve_toolset("goutoujunshi-user")),
    "skill_loaded": bool(payload),
}, sort_keys=True))
''' % expected_tools
    env = dict(os.environ)
    env["HERMES_HOME"] = str(profile_home.resolve())
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=45,
    )
    marker = "WING_DOG_PROBE="
    output_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith(marker)),
        "",
    )
    if completed.returncode != 0 or not output_line:
        raise RuntimeError(
            "Hermes profile runtime probe failed: "
            + (completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "no result")
        )
    return json.loads(output_line[len(marker):])


def command_verify(args: argparse.Namespace) -> None:
    global_config_path = Path(args.config).resolve()
    profile_config_path = Path(args.profile_config).resolve()
    global_config = yaml.safe_load(global_config_path.read_text(encoding="utf-8")) or {}
    profile_config = yaml.safe_load(profile_config_path.read_text(encoding="utf-8")) or {}
    expected_values = load_dotenv(Path(args.env))
    profile_env_path = Path(
        getattr(args, "profile_env", None) or Path(args.profile_config).with_name(".env")
    )
    profile_values = load_dotenv(profile_env_path)
    expected_model = expected_values["GOUTOUJUNSHI_MODEL"]
    expected_reasoning = expected_values["GOUTOUJUNSHI_REASONING"]
    expected_base_url = expected_values["GOUTOUJUNSHI_OPENAI_BASE_URL"].rstrip("/")
    global_feishu = global_config.get("platforms", {}).get("feishu", {})
    global_feishu_extra = global_feishu.get("extra", {}) if isinstance(global_feishu, dict) else {}
    global_toolsets = global_config.get("platform_toolsets", {}).get("feishu") or []
    global_resolved_toolsets = _resolved_hermes_toolsets(global_config, "feishu")
    profile_resolved_toolsets = _resolved_hermes_toolsets(profile_config, "feishu")
    ddgs_importable = _ddgs_importable()
    project_root = Path(
        getattr(args, "project_root", None) or Path(__file__).resolve().parents[1]
    ).resolve()
    packages = _installed_runtime_packages(
        project_root,
        global_config_path.parent,
        profile_config_path.parent,
    )
    global_runtime_probe = _profile_runtime_probe(global_config_path.parent)
    profile_runtime_probe = _profile_runtime_probe(profile_config_path.parent)
    result = {
        "ok": True,
        "global": {
            "model": global_config.get("model", {}).get("default"),
            "provider": global_config.get("model", {}).get("provider"),
            "reasoning": global_config.get("agent", {}).get("reasoning_effort"),
            "base_url_matches_codex": global_config.get("model", {}).get("base_url", "").rstrip("/") == expected_base_url,
            "fallback_count": len(global_config.get("fallback_providers") or []),
            "codex_route_header": (
                global_config.get("providers", {}).get(CODEX_ROUTE, {}).get("extra_headers", {}).get("User-Agent")
            ),
            "feishu_toolsets": global_toolsets,
            "resolved_feishu_toolsets": global_resolved_toolsets,
            "known_plugin_toolsets": (
                global_config.get("known_plugin_toolsets", {}).get("feishu") or []
            ),
            "disabled_toolsets": global_config.get("agent", {}).get("disabled_toolsets") or [],
            "plugins_enabled": global_config.get("plugins", {}).get("enabled") or [],
            "plugins_disabled": global_config.get("plugins", {}).get("disabled") or [],
            "require_mention": global_feishu.get("require_mention"),
            "adapter_require_mention": global_feishu_extra.get("require_mention"),
            "group_policy": global_feishu_extra.get("group_policy"),
            "default_group_policy": global_feishu_extra.get("default_group_policy"),
            "compression": global_config.get("compression", {}),
            "packages": packages["global"],
            "runtime_probe": global_runtime_probe,
        },
        "profile": {
            "model": profile_config.get("model", {}).get("default"),
            "reasoning": profile_config.get("agent", {}).get("reasoning_effort"),
            "feishu_toolsets": profile_config.get("platform_toolsets", {}).get("feishu"),
            "resolved_feishu_toolsets": profile_resolved_toolsets,
            "known_plugin_toolsets": (
                profile_config.get("known_plugin_toolsets", {}).get("feishu") or []
            ),
            "memory_enabled": profile_config.get("memory", {}).get("enabled"),
            "feishu_adapter_enabled": profile_config.get("platforms", {}).get("feishu", {}).get("enabled"),
            "vision_provider": profile_config.get("auxiliary", {}).get("vision", {}).get("provider"),
            "vision_model": profile_config.get("auxiliary", {}).get("vision", {}).get("model"),
            "vision_api_mode": profile_config.get("auxiliary", {}).get("vision", {}).get("api_mode"),
            "disabled_toolsets": profile_config.get("agent", {}).get("disabled_toolsets") or [],
            "skill_view_enabled": "skills"
            not in (profile_config.get("agent", {}).get("disabled_toolsets") or []),
            "tool_search_enabled": profile_config.get("tools", {}).get("tool_search"),
            "search_backend": profile_config.get("web", {}).get("search_backend"),
            "ddgs_importable": ddgs_importable,
            "web_tools_debug": profile_values.get("WEB_TOOLS_DEBUG"),
            "plugins_enabled": profile_config.get("plugins", {}).get("enabled") or [],
            "plugins_disabled": profile_config.get("plugins", {}).get("disabled") or [],
            "compression": profile_config.get("compression", {}),
            "first_message_intro": profile_config.get("onboarding", {}).get(
                "first_message_intro"
            ),
            "packages": packages["profile"],
            "runtime_probe": profile_runtime_probe,
        },
        "deepseek_automatic": str(global_config.get("model", {}).get("provider", "")).lower() == "deepseek"
        or any("deepseek" in str(item).lower() for item in global_config.get("fallback_providers") or []),
    }
    expected = (
        result["global"]["model"] == expected_model
        and result["global"]["provider"] == PROVIDER
        and result["global"]["reasoning"] == expected_reasoning
        and result["global"]["base_url_matches_codex"]
        and result["global"]["fallback_count"] == 0
        and result["global"]["codex_route_header"] == CODEX_USER_AGENT
        and result["global"]["feishu_toolsets"] == [USER_TOOLSET]
        and result["global"]["resolved_feishu_toolsets"] == [USER_TOOLSET]
        and all(
            toolset in result["global"]["known_plugin_toolsets"]
            for toolset in PROJECT_PLUGIN_TOOLSETS
        )
        and all(
            toolset in result["global"]["disabled_toolsets"]
            for toolset in GLOBAL_DISABLED_TOOLSETS
        )
        and PLUGIN_NAME in result["global"]["plugins_enabled"]
        and PLUGIN_NAME not in result["global"]["plugins_disabled"]
        and result["global"]["require_mention"] is False
        and result["global"]["adapter_require_mention"] is False
        and result["global"]["group_policy"] == "allowlist"
        and result["global"]["default_group_policy"] == "allowlist"
        and result["profile"]["feishu_toolsets"] == [RELATIONSHIP_TOOLSET, USER_TOOLSET]
        and result["profile"]["resolved_feishu_toolsets"]
        == sorted([RELATIONSHIP_TOOLSET, USER_TOOLSET])
        and all(
            toolset in result["profile"]["known_plugin_toolsets"]
            for toolset in PROJECT_PLUGIN_TOOLSETS
        )
        and result["profile"]["memory_enabled"] is False
        and result["profile"]["feishu_adapter_enabled"] is False
        and result["profile"]["vision_provider"] == CODEX_ROUTE
        and result["profile"]["vision_model"] == expected_model
        and result["profile"]["vision_api_mode"] == "codex_responses"
        and result["profile"]["disabled_toolsets"] == PROFILE_DISABLED_TOOLSETS
        and result["profile"]["skill_view_enabled"]
        and result["profile"]["tool_search_enabled"] is False
        and result["profile"]["search_backend"] == "ddgs"
        and result["profile"]["ddgs_importable"] is True
        and str(result["profile"]["web_tools_debug"]).lower() == "false"
        and result["profile"]["plugins_enabled"] == [PLUGIN_NAME]
        and PLUGIN_NAME not in result["profile"]["plugins_disabled"]
        and result["profile"]["first_message_intro"] is False
        and all(
            packages[scope][field]
            for scope in ("global", "profile")
            for field in ("skill_matches", "plugin_matches")
        )
        and all(
            probe["plugin_discovered"] is True
            and probe["skill_loaded"] is True
            and probe["tools"]
            == sorted(
                [
                    "relationship_commit_turn",
                    "relationship_search_events",
                    "relationship_web_search",
                    "user_memory_remember",
                    "user_memory_correct",
                    "user_memory_forget",
                ]
            )
            and probe["relationship_toolset"]
            == sorted(
                [
                    "relationship_commit_turn",
                    "relationship_search_events",
                    "relationship_web_search",
                ]
            )
            and probe["user_toolset"]
            == sorted(
                ["user_memory_remember", "user_memory_correct", "user_memory_forget"]
            )
            for probe in (global_runtime_probe, profile_runtime_probe)
        )
        and all(
            cfg.get("threshold_tokens") == 64000
            and cfg.get("proactive_prune_tokens") == 48000
            and cfg.get("min_tail_user_messages") == 3
            and cfg.get("abort_on_summary_failure") is True
            and cfg.get("micro_compact") is False
            for cfg in (result["global"]["compression"], result["profile"]["compression"])
        )
        and not result["deepseek_automatic"]
    )
    result["ok"] = expected
    emit(result)
    if not expected:
        raise SystemExit(2)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-secrets")
    prepare.add_argument("--codex-auth", required=True)
    prepare.add_argument("--codex-config", required=True)
    prepare.add_argument("--sessions", required=True)
    prepare.add_argument("--env", required=True)
    prepare.add_argument("--export-root", required=True)
    prepare.set_defaults(func=command_prepare_secrets)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--env", required=True)
    preflight.set_defaults(func=command_preflight)
    server_secrets = commands.add_parser("prepare-server-secrets")
    server_secrets.add_argument("--source-env", required=True)
    server_secrets.add_argument("--output-dir", required=True)
    server_secrets.set_defaults(func=command_prepare_server_secrets)
    global_config = commands.add_parser("configure-global")
    global_config.add_argument("--config", required=True)
    global_config.add_argument("--source-env", required=True)
    global_config.set_defaults(func=command_configure_global)
    vision = commands.add_parser("configure-vision")
    vision.add_argument("--config", required=True)
    vision.add_argument("--source-env", required=True)
    vision.set_defaults(func=command_configure_vision)
    profile = commands.add_parser("configure-profile")
    profile.add_argument("--profile-home", required=True)
    profile.add_argument("--global-env", required=True)
    profile.set_defaults(func=command_configure_profile)
    install_skill = commands.add_parser("install-skill")
    install_skill.add_argument("--project-root", required=True)
    install_skill.add_argument("--target-home", required=True)
    install_skill.set_defaults(func=command_install_skill)
    install_plugin = commands.add_parser("install-plugin")
    install_plugin.add_argument("--plugin-source", required=True)
    install_plugin.add_argument("--target-home", required=True)
    install_plugin.set_defaults(func=command_install_plugin)
    install_vision_patch = commands.add_parser("install-hermes-vision-patch")
    install_vision_patch.add_argument("--agent-root", required=True)
    install_vision_patch.add_argument("--backup-dir", required=True)
    install_vision_patch.set_defaults(func=command_install_hermes_vision_patch)
    install_runtime_patch = commands.add_parser("install-hermes-runtime-patch")
    install_runtime_patch.add_argument("--agent-root", required=True)
    install_runtime_patch.add_argument("--backup-dir", required=True)
    install_runtime_patch.set_defaults(func=command_install_hermes_vision_patch)
    inspect_vision_patch = commands.add_parser("inspect-hermes-vision-patch")
    inspect_vision_patch.add_argument("--agent-root", required=True)
    inspect_vision_patch.set_defaults(func=command_inspect_hermes_vision_patch)
    inspect_runtime_patch = commands.add_parser("inspect-hermes-runtime-patch")
    inspect_runtime_patch.add_argument("--agent-root", required=True)
    inspect_runtime_patch.set_defaults(func=command_inspect_hermes_vision_patch)
    rotate_sessions = commands.add_parser("rotate-profile-sessions")
    rotate_sessions.add_argument("--agent-root", required=True)
    rotate_sessions.add_argument("--global-home", required=True)
    rotate_sessions.add_argument("--profile-home", required=True)
    rotate_sessions.add_argument("--profile-name", default="goutoujunshi")
    rotate_sessions.add_argument("--reason", default=PROFILE_SESSION_MIGRATION_REASON)
    rotate_sessions.set_defaults(func=command_rotate_profile_sessions)
    verify = commands.add_parser("verify")
    verify.add_argument("--config", required=True)
    verify.add_argument("--profile-config", required=True)
    verify.add_argument("--profile-env")
    verify.add_argument("--env", required=True)
    verify.add_argument("--project-root")
    verify.set_defaults(func=command_verify)
    return root


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
