from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

import goutoujunshi_cli  # noqa: E402


class ReconcileConfigTests(unittest.TestCase):
    def test_semantically_unchanged_config_is_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                """# Preserve user formatting and comments.
gateway:
  multiplex_profiles: true
  profile_routes:
    - name: relation-7
      platform: feishu
      chat_id: chat-1
      profile: goutoujunshi
platforms:
  feishu:
    require_mention: false
    group_rules:
      chat-1:
        policy: allowlist
        allowlist: [owner-1]
        require_mention: false
    extra:
      require_mention: false
      group_policy: allowlist
      default_group_policy: allowlist
      group_rules:
        chat-1:
          policy: allowlist
          allowlist: [owner-1]
          require_mention: false
""",
                encoding="utf-8",
            )
            original = config_path.read_bytes()
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"GOUTOUJUNSHI_OWNER_ID": "owner-1"}),
                patch.object(
                    goutoujunshi_cli,
                    "list_active_bindings",
                    return_value=[{"relationship_id": 7, "chat_id": "chat-1"}],
                ),
                patch.object(goutoujunshi_cli, "list_managed_chat_ids", return_value=["chat-1"]),
                patch.object(goutoujunshi_cli, "pending_control_requests", return_value=[]),
                patch.object(goutoujunshi_cli, "finish_control_requests"),
                redirect_stdout(output),
            ):
                goutoujunshi_cli.command_reconcile(SimpleNamespace(config=str(config_path)))

            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse(json.loads(output.getvalue())["changed"])

    def test_group_rules_are_mirrored_into_adapter_extra(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("platforms:\n  feishu: {}\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"GOUTOUJUNSHI_OWNER_ID": "owner-1"}),
                patch.object(
                    goutoujunshi_cli,
                    "list_active_bindings",
                    return_value=[{"relationship_id": 7, "chat_id": "chat-1"}],
                ),
                patch.object(goutoujunshi_cli, "list_managed_chat_ids", return_value=["chat-1"]),
                patch.object(goutoujunshi_cli, "pending_control_requests", return_value=[]),
                patch.object(goutoujunshi_cli, "finish_control_requests"),
                redirect_stdout(io.StringIO()),
            ):
                goutoujunshi_cli.command_reconcile(SimpleNamespace(config=str(config_path)))

            config = __import__("yaml").safe_load(config_path.read_text(encoding="utf-8"))
            top_level = config["platforms"]["feishu"]["group_rules"]["chat-1"]
            adapter_extra = config["platforms"]["feishu"]["extra"]["group_rules"]["chat-1"]
            self.assertEqual(adapter_extra, top_level)
            self.assertFalse(adapter_extra["require_mention"])
            self.assertFalse(config["platforms"]["feishu"]["require_mention"])
            self.assertFalse(config["platforms"]["feishu"]["extra"]["require_mention"])
            self.assertEqual(config["platforms"]["feishu"]["extra"]["group_policy"], "allowlist")
            self.assertEqual(
                config["platforms"]["feishu"]["extra"]["default_group_policy"],
                "allowlist",
            )


if __name__ == "__main__":
    unittest.main()
