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
import tomllib
import zlib
from pathlib import Path
from typing import Any

import yaml


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
HERMES_VISION_PATCH_VERSION = "0.20.4"
HERMES_VISION_RUN_SHA256 = "b671d6cd9ce1373c399215995cffe5d918142b33f3f0659ede95eee74ee17ab6"
HERMES_VISION_PATCHED_SHA256 = "66c12945bbde1bab43151f6b3d4c3e7e1bb4d5b55bdec38a28111ccecf10198d"


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


def install_hermes_vision_patch(
    agent_root: Path,
    backup_dir: Path,
    *,
    expected_version: str = HERMES_VISION_PATCH_VERSION,
    expected_sha256: str = HERMES_VISION_RUN_SHA256,
    expected_patched_sha256: str = HERMES_VISION_PATCHED_SHA256,
) -> dict[str, Any]:
    agent_root = agent_root.resolve()
    backup_dir = backup_dir.resolve()
    target = agent_root / "gateway" / "run.py"
    version_file = agent_root / "hermes_cli" / "__init__.py"
    version_match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        version_file.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    actual_version = version_match.group(1) if version_match else "unknown"
    if actual_version != expected_version:
        raise RuntimeError(
            f"Hermes version mismatch: expected {expected_version}, found {actual_version}"
        )
    current_sha256 = _sha256_file(target)
    if current_sha256 == expected_patched_sha256:
        return {
            "ok": True,
            "status": "already_patched",
            "version": actual_version,
            "target": str(target),
            "sha256": current_sha256,
        }
    if current_sha256 != expected_sha256:
        raise RuntimeError(
            f"Hermes gateway SHA256 mismatch: expected {expected_sha256}, found {current_sha256}"
        )
    patched = _patch_hermes_vision_source(target.read_bytes().decode("utf-8"))
    patched_bytes = patched.encode("utf-8")
    patched_sha256 = hashlib.sha256(patched_bytes).hexdigest()
    if patched_sha256 != expected_patched_sha256:
        raise RuntimeError(
            f"Hermes patched SHA256 mismatch: expected {expected_patched_sha256}, built {patched_sha256}"
        )
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"gateway-run.py.{expected_sha256}.bak"
    if backup.exists() and _sha256_file(backup) != expected_sha256:
        raise RuntimeError(f"Hermes patch backup SHA256 mismatch: {backup}")
    if not backup.exists():
        shutil.copy2(target, backup)
    staging = target.with_name(f".{target.name}.goutoujunshi-{secrets.token_hex(6)}.tmp")
    replaced = False
    try:
        staging.write_bytes(patched_bytes)
        if _sha256_file(staging) != expected_patched_sha256:
            raise RuntimeError("staged Hermes vision patch failed verification")
        os.replace(staging, target)
        replaced = True
        if _sha256_file(target) != expected_patched_sha256:
            raise RuntimeError("installed Hermes vision patch failed verification")
    except Exception:
        if replaced:
            shutil.copy2(backup, staging)
            os.replace(staging, target)
        raise
    finally:
        staging.unlink(missing_ok=True)
    return {
        "ok": True,
        "status": "patched",
        "version": actual_version,
        "target": str(target),
        "backup": str(backup),
        "sha256": expected_patched_sha256,
    }


def inspect_hermes_vision_patch(agent_root: Path) -> dict[str, Any]:
    agent_root = agent_root.resolve()
    target = agent_root / "gateway" / "run.py"
    version_file = agent_root / "hermes_cli" / "__init__.py"
    version_match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        version_file.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    source = target.read_text(encoding="utf-8")
    patched = _patch_hermes_vision_source(source).encode("utf-8")
    return {
        "ok": True,
        "version": version_match.group(1) if version_match else "unknown",
        "source_sha256": _sha256_file(target),
        "patched_sha256": hashlib.sha256(patched).hexdigest(),
    }


def command_install_hermes_vision_patch(args: argparse.Namespace) -> None:
    emit(
        install_hermes_vision_patch(
            Path(args.agent_root),
            Path(args.backup_dir),
        )
    )


def command_inspect_hermes_vision_patch(args: argparse.Namespace) -> None:
    emit(inspect_hermes_vision_patch(Path(args.agent_root)))


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
    os.replace(temp, path)


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


def command_verify(args: argparse.Namespace) -> None:
    global_config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    profile_config = yaml.safe_load(Path(args.profile_config).read_text(encoding="utf-8")) or {}
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
    inspect_vision_patch = commands.add_parser("inspect-hermes-vision-patch")
    inspect_vision_patch.add_argument("--agent-root", required=True)
    inspect_vision_patch.set_defaults(func=command_inspect_hermes_vision_patch)
    verify = commands.add_parser("verify")
    verify.add_argument("--config", required=True)
    verify.add_argument("--profile-config", required=True)
    verify.add_argument("--profile-env")
    verify.add_argument("--env", required=True)
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
