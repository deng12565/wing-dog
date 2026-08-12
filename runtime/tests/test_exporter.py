from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from goutoujunshi.exporter import render_markdown  # noqa: E402


class ExporterTests(unittest.TestCase):
    def test_projection_declares_mysql_authority_and_event_type(self) -> None:
        profile = {
            "id": 7,
            "display_name": "测试人物",
            "status": "active",
            "current_channel": "微信",
            "updated_at": datetime(2026, 8, 8, 12, 0),
            "known_facts": "事实",
            "conservative_judgments": "判断",
            "unknowns": "未知",
            "latest_state": "最新",
            "response_preferences": "简短",
        }
        event = {
            "id": 9,
            "event_type": "draft",
            "author_role": "assistant",
            "content": "建议正文",
            "evidence_kind": "assistant_reply_suggestion",
            "occurred_at": datetime(2026, 8, 8, 12, 1),
            "supersedes_event_id": None,
            "channel": "微信",
        }
        rendered = render_markdown(profile, [event], None)
        self.assertIn("authority: mysql", rendered)
        self.assertIn("微信 | draft", rendered)
        self.assertIn("建议正文", rendered)


if __name__ == "__main__":
    unittest.main()
