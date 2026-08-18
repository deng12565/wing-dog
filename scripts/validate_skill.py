#!/usr/bin/env python3
"""Validate the distributable goutoujunshi skill without third-party packages."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
MIN_KNOWLEDGE_DOCUMENTS = 20
MIN_PRACTICAL_DOCUMENTS = 21

REQUIRED_KNOWLEDGE = (
    "01-证据分级与内容边界.md",
    "05-PUA操控与伦理替代.md",
    "08-同意边界性与亲密.md",
    "09-在线约会与数字关系.md",
    "17-中国法律安全与危机转介.md",
    "20-经典社交体系的机制、证据与风险边界.md",
)

REQUIRED_PRACTICAL = (
    "00-导读与使用分级.md",
    "关系投入失衡：互惠判断、降级投入与退出决策.md",
    "场景感、松弛感与社交校准：从接话到关系推进.md",
    "实战话术编排器：从一句回复到后续分支.md",
    "主动表达、第一次见面与自然接触.md",
    "自然流、内在状态与结构化互动：伦理能力转译.md",
    "功夫聊天：关系、进攻、破防、假动作与引诱.md",
    "从认识到确定关系：真诚、吸引与自然推进.md",
)

REQUIRED_SCENARIOS = (
    "chat-record-analysis-scenarios.md",
    "relationship-investment-scenarios.md",
    "social-calibration-scenarios.md",
    "tactical-reply-scenarios.md",
    "active-dating-scenarios.md",
    "classic-social-framework-scenarios.md",
    "male-dating-journey-scenarios.md",
)


def require(path: str) -> Path:
    target = ROOT / path
    if not target.exists():
        ERRORS.append(f"missing required path: {path}")
    return target


def validate_frontmatter() -> None:
    skill = require("SKILL.md")
    if not skill.is_file():
        return

    content = skill.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        ERRORS.append("SKILL.md has invalid YAML frontmatter boundaries")
        return

    frontmatter = match.group(1)
    keys = re.findall(r"^([A-Za-z0-9_-]+):", frontmatter, re.MULTILINE)
    if keys != ["name", "description"]:
        ERRORS.append(f"SKILL.md frontmatter keys must be name, description; got {keys}")

    name_match = re.search(r"^name:\s*([^\n]+)$", frontmatter, re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else ""
    description = description_match.group(1).strip() if description_match else ""
    if name != "goutoujunshi" or not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        ERRORS.append(f"invalid skill name: {name!r}")
    if not description or len(description) > 1024 or "<" in description or ">" in description:
        ERRORS.append("description is empty, too long, or contains angle brackets")


def validate_inventory(runtime_only: bool) -> None:
    require("agents/openai.yaml")
    notices = require("references/THIRD_PARTY_NOTICES.md")
    if not runtime_only:
        require("README.md")
        require("LICENSE")
    knowledge = list((ROOT / "references/knowledge").glob("*.md"))
    practical = list((ROOT / "references/practical").glob("*.md"))
    if len(knowledge) < MIN_KNOWLEDGE_DOCUMENTS:
        ERRORS.append(
            "expected at least "
            f"{MIN_KNOWLEDGE_DOCUMENTS} knowledge documents, found {len(knowledge)}"
        )
    if len(practical) < MIN_PRACTICAL_DOCUMENTS:
        ERRORS.append(
            "expected at least "
            f"{MIN_PRACTICAL_DOCUMENTS} practical documents, found {len(practical)}"
        )
    for filename in REQUIRED_KNOWLEDGE:
        require(f"references/knowledge/{filename}")
    for filename in REQUIRED_PRACTICAL:
        require(f"references/practical/{filename}")
    if not runtime_only:
        for filename in REQUIRED_SCENARIOS:
            require(f"tests/{filename}")

    agent = ROOT / "agents/openai.yaml"
    if agent.is_file() and "$goutoujunshi" not in agent.read_text(encoding="utf-8"):
        ERRORS.append("agents/openai.yaml default prompt must mention $goutoujunshi")
    if notices.is_file():
        notice_content = notices.read_text(encoding="utf-8")
        notice_markers = (
            "hotcoffeeshake/tong-jincheng-skill",
            "30d6891783d889a164f0536f5cfdca009f307d01",
            "Wike-CHI/mystery-perspective",
            "bef2c7e4b71e0f62ee5fc0f8114f3e63ca3255c5",
            "MIT License",
            "Copyright (c) 2026 hotcoffeeshake",
            "Copyright (c) 2026 Wike-CHI",
        )
        for marker in notice_markers:
            if marker not in notice_content:
                ERRORS.append(f"third-party notice missing marker: {marker}")


def validate_routes_and_regressions(runtime_only: bool) -> None:
    skill = ROOT / "SKILL.md"
    if skill.is_file():
        content = skill.read_text(encoding="utf-8")
        required_routes = (
            "references/knowledge/20-经典社交体系的机制、证据与风险边界.md",
            "references/practical/自然流、内在状态与结构化互动：伦理能力转译.md",
            "references/practical/功夫聊天：关系、进攻、破防、假动作与引诱.md",
            "references/practical/从认识到确定关系：真诚、吸引与自然推进.md",
            "默认只读取当前问题直接需要的 1–3 份参考",
            "sender/member ID",
            "不得按左右、颜色、语气或性别猜",
            "证据不足时优先选择一个能产生新信息的动作",
            "高手决策引擎",
            "Demonstrate, Don't State",
            "Compliance Test／投入测试",
            "失败时反向检查阶段、素材、表达和时机",
        )
        for route in required_routes:
            if route not in content:
                ERRORS.append(f"SKILL.md missing required progressive-disclosure route: {route}")

    scenarios = ROOT / "tests/classic-social-framework-scenarios.md"
    if runtime_only:
        return
    if scenarios.is_file():
        content = scenarios.read_text(encoding="utf-8")
        coverage_markers = (
            "冷读",
            "自然流",
            "结构化互动",
            "聊天截图",
            "按需加载",
            "明确拒绝",
            "煤气灯",
            "隔离",
            "DHV",
            "Neg",
            "投入测试",
            "Reverse Calibration",
        )
        for marker in coverage_markers:
            if marker not in content:
                ERRORS.append(
                    "classic social framework regression scenarios missing coverage: "
                    f"{marker}"
                )

    chat_scenarios = ROOT / "tests/chat-record-analysis-scenarios.md"
    if chat_scenarios.is_file():
        content = chat_scenarios.read_text(encoding="utf-8")
        coverage_markers = (
            "说话人映射",
            "sender/member ID",
            "未确认前只问一个必要问题",
            "不静默翻转说话人",
        )
        for marker in coverage_markers:
            if marker not in content:
                ERRORS.append(
                    "chat record scenarios missing speaker-mapping coverage: "
                    f"{marker}"
                )

    journey_scenarios = ROOT / "tests/male-dating-journey-scenarios.md"
    if journey_scenarios.is_file():
        content = journey_scenarios.read_text(encoding="utf-8")
        coverage_markers = (
            "没有具体对象",
            "自然邀约",
            "一次软拒绝",
            "重复没有替代安排",
            "明确拒绝",
            "多位对象档案",
            "点名童锦程",
            "已经确认关系",
        )
        for marker in coverage_markers:
            if marker not in content:
                ERRORS.append(
                    "male dating journey scenarios missing coverage: " f"{marker}"
                )


def validate_runtime_boundaries() -> None:
    if (ROOT / ".git").exists():
        tracked_research = subprocess.run(
            ["git", "ls-files", "--", "research", "恋爱知识库"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for path in tracked_research:
            ERRORS.append(
                f"raw research must remain untracked and outside runtime content: {path}"
            )

    runtime_roots = (
        ROOT / "SKILL.md",
        ROOT / "agents",
        ROOT / "references",
        ROOT / "scripts",
        ROOT / "assets",
    )
    forbidden_parts = {"research", "documentation", "tests", ".git", "__pycache__"}
    for runtime_root in runtime_roots:
        if not runtime_root.exists():
            continue
        paths = (runtime_root,) if runtime_root.is_file() else runtime_root.rglob("*")
        for path in paths:
            if forbidden_parts.intersection(path.relative_to(ROOT).parts):
                ERRORS.append(
                    "non-runtime content nested inside runtime allowlist: "
                    f"{path.relative_to(ROOT)}"
                )
            if path.is_file() and path.suffix in {".pyc", ".pyo"}:
                ERRORS.append(
                    f"compiled test/runtime artifact found: {path.relative_to(ROOT)}"
                )


def validate_markdown_links() -> None:
    link_pattern = re.compile(r"\]\(([^)]+)\)")
    for markdown in ROOT.rglob("*.md"):
        if markdown.relative_to(ROOT).parts[:2] == (".local", "backups"):
            continue
        text = markdown.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(text):
            target = raw_target.strip().split("#", 1)[0]
            if not target or re.match(r"^(?:https?://|mailto:)", target):
                continue
            resolved = (markdown.parent / target).resolve()
            if not resolved.exists():
                ERRORS.append(
                    f"broken local link in {markdown.relative_to(ROOT)}: {raw_target}"
                )


def validate_placeholders() -> None:
    for path in ROOT.rglob("*"):
        relative_parts = path.relative_to(ROOT).parts
        if (
            not path.is_file()
            or ".git" in relative_parts
            or relative_parts[:2] == (".local", "backups")
        ):
            continue
        if path.suffix.lower() not in {".md", ".yaml", ".yml", ".py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "[" + "TODO" in text:
            ERRORS.append(f"template placeholder in {path.relative_to(ROOT)}")


def main() -> int:
    unexpected_args = [arg for arg in sys.argv[1:] if arg != "--runtime"]
    if unexpected_args:
        print(f"ERROR: unsupported arguments: {' '.join(unexpected_args)}")
        return 2
    runtime_only = "--runtime" in sys.argv[1:]

    validate_frontmatter()
    validate_inventory(runtime_only)
    validate_routes_and_regressions(runtime_only)
    validate_runtime_boundaries()
    validate_markdown_links()
    validate_placeholders()
    if ERRORS:
        for error in ERRORS:
            print(f"ERROR: {error}")
        return 1
    print("goutoujunshi validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
