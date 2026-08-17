from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"
STATUS_PATH = REPO_ROOT / "docs" / "phase2" / "STATUS.md"
DECISIONS_PATH = REPO_ROOT / "docs" / "phase2" / "DECISIONS.md"
PLAN_PATH = REPO_ROOT / "docs" / "phase2" / "PHASE2_PLAN.md"

EXPECTED_CLAUDE_CONTENT = """@AGENTS.md

## Claude Code
- 项目规则的唯一权威是 `AGENTS.md`；通用项目规则只修改该文件，不得追加到本段。
- 本文件只允许上述 `@AGENTS.md` 一个导入；禁止导入长规格文档。
"""

DECISION_FOUR = (
    "负向、边界和安全拒绝用例的题面、预期、禁止项、分类及判卷契约冻结，修改需雨爷明确批准。"
    "所有既有正向题面同样不可原地改写，只能新增后继题并在题外生命周期清单中停止旧题运行。"
    "判卷契约或运行选择规则变更时，必须按同一版本包全量回放并明确披露影响。"
    "每修复一个真实缺陷，必须新增一条能在未修代码上失败、修复后通过、且走原缺陷路径的永久回归证据；"
    "缺陷属于 Golden Runtime 观察边界时才新增 Golden Task，"
    "否则放在最小且忠实的单元/集成/API/浏览器层。"
)

BOOT_GOVERNANCE_PATHS = (AGENTS_PATH, CLAUDE_PATH)
COMMIT_SHA_PATTERN = re.compile(r"`[0-9a-fA-F]{8,40}`")
CI_RUN_PATTERN = re.compile(r"\bCI run \d+\b", re.IGNORECASE)
STATUS_RESULT_PATTERN = re.compile(
    r"^- (?:pytest|Golden Gate|`tests/architecture/`)：`(?P<result>[^`]+)`",
    re.MULTILINE,
)
PASSED_RESULT_PATTERN = re.compile(r"\b(?:\d{2,}/\d{2,}|\d{2,}) passed\b")
SCRIPT_REFERENCE_PATTERN = re.compile(r"scripts/[A-Za-z0-9_./-]+\.py")
REQUIRED_VALIDATION_SCRIPTS = {
    "scripts/check_dependencies.py",
    "scripts/check_dev_environment.py",
    "scripts/check_weak_tests.py",
    "scripts/run_golden_tasks.py",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_claude_only_imports_agents_and_contains_claude_specific_section() -> None:
    assert _read(CLAUDE_PATH) == EXPECTED_CLAUDE_CONTENT


def test_boot_governance_documents_do_not_embed_commit_or_ci_ids() -> None:
    violations: list[str] = []
    for path in BOOT_GOVERNANCE_PATHS:
        content = _read(path)
        if COMMIT_SHA_PATTERN.search(content):
            violations.append(f"{path.relative_to(REPO_ROOT)} contains a commit SHA")
        if CI_RUN_PATTERN.search(content):
            violations.append(f"{path.relative_to(REPO_ROOT)} contains a CI run id")
    assert violations == []


def test_current_result_lines_exist_only_in_status() -> None:
    status_results = STATUS_RESULT_PATTERN.findall(_read(STATUS_PATH))
    assert len(status_results) == 3
    assert all("passed" in result for result in status_results)
    violations = {
        path.relative_to(REPO_ROOT).as_posix(): PASSED_RESULT_PATTERN.findall(
            _read(path)
        )
        for path in BOOT_GOVERNANCE_PATHS
        if PASSED_RESULT_PATTERN.search(_read(path))
    }
    assert violations == {}


def test_agents_validation_scripts_are_required_and_exist() -> None:
    agents = _read(AGENTS_PATH)
    validation_section = re.search(
        r"^## (?:验证命令|Validation commands)\s*$\n(?P<body>.*?)(?=^## )",
        agents,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert validation_section is not None
    references = set(
        SCRIPT_REFERENCE_PATTERN.findall(validation_section.group("body"))
    )
    assert REQUIRED_VALIDATION_SCRIPTS <= references
    missing = sorted(
        reference
        for reference in references
        if not (REPO_ROOT / reference).is_file()
    )
    assert missing == []


def test_decision_four_is_verbatim_and_has_one_authoritative_copy() -> None:
    assert _read(DECISIONS_PATH).count(DECISION_FOUR) == 1
    assert _read(AGENTS_PATH).count(DECISION_FOUR) == 0
    assert _read(CLAUDE_PATH).count(DECISION_FOUR) == 0
    assert _read(PLAN_PATH).count(DECISION_FOUR) == 0
    assert "docs/phase2/DECISIONS.md" in _read(PLAN_PATH)
