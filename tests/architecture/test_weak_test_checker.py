"""Tests for the weak-test checker script — Phase 0 TDD.

These tests import ``scripts.check_weak_tests`` and validate that the AST-based
checker correctly catches:
  - ``assert True`` (tautological assertion)
  - pass-only test functions
  - test functions with no assertions at all
  - simple tautologies like ``assert 1 == 1``

while allowing real assertions and ``pytest.raises`` blocks.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_weak_tests.py"
_spec = importlib.util.spec_from_file_location("check_weak_tests", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_weak_tests"] = _mod
_spec.loader.exec_module(_mod)

WeakTestFinding = _mod.WeakTestFinding
check_source = _mod.check_source
severity_rank = _mod.severity_rank


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_test_file(tmp_path: Path, name: str, content: str) -> Path:
    file_path = tmp_path / name
    file_path.write_text(textwrap.dedent(content), encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# Tests — negative (should detect weak tests)
# ---------------------------------------------------------------------------

class TestWeakTestDetection:
    """Verify that weak-test patterns are caught."""

    def test_detects_assert_true(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_bad.py", """\
            def test_always_passes():
                assert True
        """)
        findings = check_source(path)
        assert len(findings) >= 1
        assert any(f.kind == "tautology" for f in findings)

    def test_detects_pass_only(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_bad.py", """\
            def test_nothing():
                pass
        """)
        findings = check_source(path)
        assert len(findings) >= 1
        assert any(
            "pass-only" in f.description.lower()
            or "no assertion" in f.description.lower()
            for f in findings
        )

    def test_detects_no_assertion(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_bad.py", """\
            def test_computes_something():
                x = 1 + 1
                result = x * 2
        """)
        findings = check_source(path)
        assert len(findings) >= 1
        assert any("no assertion" in f.description.lower() for f in findings)

    def test_detects_assert_one_equals_one(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_bad.py", """\
            def test_tautology():
                assert 1 == 1
        """)
        findings = check_source(path)
        assert len(findings) >= 1
        assert any(
            "tautology" in f.description.lower()
            or "assert True" in f.description
            for f in findings
        )

    def test_detects_self_comparison(self, tmp_path: Path) -> None:
        """Self-comparison like assert value == value is tautological."""
        path = _make_test_file(tmp_path, "test_bad.py", """\
            def test_self_compare():
                value = 42
                assert value == value
        """)
        findings = check_source(path)
        assert len(findings) >= 1
        assert any("tautology" in f.description.lower() for f in findings)

    def test_detects_tautology_alongside_pytest_raises(
        self, tmp_path: Path
    ) -> None:
        """pytest.raises + assert True should still flag the tautology."""
        path = _make_test_file(tmp_path, "test_bad.py", """\
            import pytest

            def test_mixed():
                with pytest.raises(ValueError):
                    raise ValueError("boom")
                assert True
        """)
        findings = check_source(path)
        assert len(findings) >= 1
        assert any(f.kind == "tautology" for f in findings)


# ---------------------------------------------------------------------------
# Tests — positive (should NOT flag real tests)
# ---------------------------------------------------------------------------

class TestCleanTestsPass:
    """Verify that legitimate tests are not flagged."""

    def test_real_assertion_passes(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_good.py", """\
            def test_addition():
                assert 1 + 1 == 2
        """)
        findings = check_source(path)
        assert findings == []

    def test_pytest_raises_passes(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_good.py", """\
            import pytest

            def test_raises():
                with pytest.raises(ValueError):
                    raise ValueError("boom")
        """)
        findings = check_source(path)
        assert findings == []

    def test_real_assert_with_variable_passes(self, tmp_path: Path) -> None:
        path = _make_test_file(tmp_path, "test_good.py", """\
            def test_with_variable():
                result = 2 + 2
                assert result == 4
        """)
        findings = check_source(path)
        assert findings == []

    def test_non_test_function_ignored(self, tmp_path: Path) -> None:
        """Functions not prefixed with ``test_`` should not be checked."""
        path = _make_test_file(tmp_path, "test_good.py", """\
            def helper():
                pass

            def test_real():
                assert helper() is None
        """)
        # helper() has no assertions but it's not a test_ function
        findings = check_source(path)
        assert findings == []


# ---------------------------------------------------------------------------
# Tests — severity ranking
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_tautology_is_high_severity(self) -> None:
        assert severity_rank("tautology") == "high"

    def test_pass_only_is_high_severity(self) -> None:
        assert severity_rank("pass_only") == "high"

    def test_no_assertion_is_medium_severity(self) -> None:
        assert severity_rank("no_assertion") == "medium"

    def test_unknown_kind_is_medium_severity(self) -> None:
        assert severity_rank("unknown") == "medium"
