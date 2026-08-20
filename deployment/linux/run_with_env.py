from __future__ import annotations

import os
import sys
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"environment file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        clean_value = value.strip().strip('"').strip("'")
        if not clean_key or "\x00" in clean_value or "\n" in clean_value or "\r" in clean_value:
            raise RuntimeError("invalid environment entry")
        os.environ.setdefault(clean_key, clean_value)


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("a command is required")
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    load_env(home / ".env")
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
