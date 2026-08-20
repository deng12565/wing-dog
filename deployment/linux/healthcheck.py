from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load_env(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    try:
        load_env(home / ".env")
        state = json.loads((home / "gateway_state.json").read_text(encoding="utf-8"))
        pid = int(state["pid"])
        os.kill(pid, 0)
        if state.get("gateway_state") != "running":
            return 1
        if state.get("platforms", {}).get("feishu", {}).get("state") != "connected":
            return 1
        project = Path(os.environ.get("WING_DOG_PROJECT_ROOT", "/opt/wing-dog"))
        sys.path.insert(0, str(project / "runtime"))
        from goutoujunshi.repository import health

        return 0 if health().get("ok") else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
