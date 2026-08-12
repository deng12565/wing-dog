from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from goutoujunshi.legacy_import import classify, infer_channel, parse_legacy_markdown  # noqa: E402


class LegacyImportTests(unittest.TestCase):
    def test_event_types_stay_separate(self) -> None:
        self.assertEqual(classify("用户已发送", "你好")[0], "sent")
        self.assertEqual(classify("建议稿（未发送）", "你好")[0], "draft")
        self.assertEqual(classify("对方已回复", "你好")[0], "received")
        self.assertEqual(classify("用户明确纠正", "并未发送")[0], "correction")
        self.assertEqual(classify("女生特点（根据聊天推断）", "回复积极")[0], "analysis")
        self.assertEqual(classify("后续聊天策略", "保持自然")[0], "analysis")
        self.assertEqual(classify("避免事项", "不要快速推进")[0], "analysis")

    def test_channels_stay_separate(self) -> None:
        self.assertEqual(infer_channel("朋友圈更新", ""), "朋友圈")
        self.assertEqual(infer_channel("抖音私信", ""), "抖音")
        self.assertEqual(infer_channel("微信聊天", ""), "微信")
        self.assertEqual(infer_channel("线下见面", ""), "线下")

    def test_parse_preserves_ambiguous_evidence(self) -> None:
        text = """# 人物与背景\n已认识。\n# 最新状态\n等待回复。\n## 2026-08-01 补充\n来源不明。\n"""
        snapshot, blocks = parse_legacy_markdown(text, datetime(2026, 8, 1))
        self.assertIn("已认识", snapshot["known_facts"])
        self.assertEqual(blocks[0].event_type, "background")
        self.assertEqual(blocks[0].evidence_kind, "legacy_unclassified_evidence")


if __name__ == "__main__":
    unittest.main()
