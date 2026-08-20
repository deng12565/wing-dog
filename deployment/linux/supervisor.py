from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

STOP_REQUESTED = False


def _handle_stop(_signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def load_env(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Hermes env not found: {path}")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {Path(command[0]).name} exit={completed.returncode}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("command returned no JSON")
    payload = json.loads(lines[-1])
    if not payload.get("ok"):
        raise RuntimeError("command reported failure")
    return payload


def gateway_action(action: str) -> None:
    completed = subprocess.run(
        ["hermes", "gateway", action],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"gateway {action} failed")


def clear_expired_media(home: Path, *, now: float | None = None) -> int:
    registry = home / "state" / "goutoujunshi-media.json"
    if not registry.exists():
        return 0
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(entries, list):
        return 0
    cutoff = (now if now is not None else time.time()) - 24 * 60 * 60
    roots = [(home / "cache" / name).resolve() for name in ("images", "audio", "documents")]
    retained: list[dict[str, Any]] = []
    removed = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            created = str(entry["created_at"]).replace("Z", "+00:00")
            created_at = datetime.fromisoformat(created).timestamp()
            path = Path(str(entry["path"])).resolve()
        except (KeyError, OSError, ValueError):
            continue
        allowed = any(path == root or root in path.parents for root in roots)
        if created_at < cutoff:
            if allowed and path.is_file():
                path.unlink(missing_ok=True)
            removed += 1
        else:
            retained.append(entry)
    temporary = registry.with_suffix(registry.suffix + ".tmp")
    temporary.write_text(json.dumps(retained, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, registry)
    return removed


def run_cycle(cli: Path, config: Path, home: Path) -> dict[str, Any]:
    health = run_json([sys.executable, str(cli), "health"])
    reconcile = run_json(
        [sys.executable, str(cli), "reconcile-config", "--config", str(config)]
    )
    if reconcile.get("changed"):
        gateway_action("restart")
    exports = run_json([sys.executable, str(cli), "retry-exports", "--limit", "25"])
    removed = clear_expired_media(home)
    return {
        "database": bool(health.get("ok")),
        "routes_changed": bool(reconcile.get("changed")),
        "active_routes": int(reconcile.get("active_routes", 0)),
        "exports_done": int(exports.get("done", 0)),
        "exports_failed": int(exports.get("failed", 0)),
        "media_removed": removed,
    }


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()
    project = Path(os.environ.get("WING_DOG_PROJECT_ROOT", "/opt/wing-dog")).resolve()
    cli = project / "runtime" / "goutoujunshi_cli.py"
    config = home / "config.yaml"
    interval = max(15, int(os.environ.get("WING_DOG_SUPERVISOR_INTERVAL_SECONDS", "60")))
    load_env(home / ".env")
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    failure_count = 0
    gateway_started = False
    while not STOP_REQUESTED:
        try:
            if not gateway_started:
                run_json([sys.executable, str(cli), "health"])
                run_json(
                    [sys.executable, str(cli), "reconcile-config", "--config", str(config)]
                )
                gateway_action("start")
                gateway_started = True
                print(json.dumps({"component": "supervisor", "code": "gateway_started"}), flush=True)
            result = run_cycle(cli, config, home)
            failure_count = 0
            print(json.dumps({"component": "supervisor", "code": "cycle", **result}), flush=True)
        except Exception as exc:
            failure_count += 1
            print(
                json.dumps(
                    {
                        "component": "supervisor",
                        "code": "error",
                        "error": type(exc).__name__,
                        "consecutive_failures": failure_count,
                    }
                ),
                flush=True,
            )
        deadline = time.monotonic() + min(300, interval * max(1, failure_count))
        while not STOP_REQUESTED and time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
