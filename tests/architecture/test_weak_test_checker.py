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

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_weak_tests.py"
_spec = importlib.util.spec_from_file_location("check_weak_tests", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_weak_tests"] = _mod
_spec.loader.exec_module(_mod)

WeakTestFinding = _mod.WeakTestFinding
check_source = _mod.check_source
main = _mod.main
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
# Tests — TypeScript / TSX negative fixtures
# ---------------------------------------------------------------------------

_TYPESCRIPT_WEAK_CASES = [
    (
        "constant true equality",
        """
        it('constant true', () => {
          expect(true).toBe(true);
        });
        """,
        "tautology",
    ),
    (
        "constant truthy matcher",
        """
        it('constant truthy', () => {
          expect(true).toBeTruthy();
        });
        """,
        "tautology",
    ),
    (
        "equivalent string literals",
        """
        it('compares equivalent string literals', () => {
          expect("same").toBe('same');
        });
        """,
        "tautology",
    ),
    (
        "equal number literals",
        """
        it('compares equal number literals', () => {
          expect(1).toBe(1.0);
        });
        """,
        "tautology",
    ),
    (
        "zero assertions",
        """
        it('computes without checking', () => {
          const result = 1 + 1;
          console.info(result);
        });
        """,
        "no_assertion",
    ),
    (
        "empty body",
        """
        it('is empty', () => {});
        """,
        "pass_only",
    ),
    (
        "comment-only body",
        """
        it('contains only comments', () => {
          // no behavior is verified
          /* still no assertion */
        });
        """,
        "pass_only",
    ),
    (
        "it skip",
        """
        it.skip('is skipped', () => {
          expect(1 + 1).toBe(2);
        });
        """,
        "skipped",
    ),
    (
        "describe skip",
        """
        describe.skip('skipped suite', () => {
          it('has a real assertion', () => {
            expect(1 + 1).toBe(2);
          });
        });
        """,
        "skipped",
    ),
    (
        "it todo",
        """
        it.todo('is not implemented');
        """,
        "skipped",
    ),
    (
        "xit",
        """
        xit('is disabled', () => {
          expect(1 + 1).toBe(2);
        });
        """,
        "skipped",
    ),
    (
        "xdescribe",
        """
        xdescribe('disabled suite', () => {
          it('has a real assertion', () => {
            expect(1 + 1).toBe(2);
          });
        });
        """,
        "skipped",
    ),
    (
        "self comparison",
        """
        it('compares a value to itself', () => {
          const value = computeValue();
          expect(value).toBe(value);
        });
        """,
        "tautology",
    ),
    (
        "attribute self comparison",
        """
        it('compares an attribute to itself', () => {
          expect(record.value).toEqual(record.value);
        });
        """,
        "tautology",
    ),
    (
        "tautology beside real assertion",
        """
        it('mixes weak and real assertions', () => {
          expect(loadValue()).toBe(42);
          expect(true).toBe(true);
        });
        """,
        "tautology",
    ),
    (
        "assert true",
        """
        test('uses a constant assert', () => {
          assert(true);
        });
        """,
        "tautology",
    ),
]


@pytest.mark.parametrize(
    ("_case_name", "source", "expected_kind"),
    _TYPESCRIPT_WEAK_CASES,
    ids=[case[0] for case in _TYPESCRIPT_WEAK_CASES],
)
def test_typescript_weak_shapes_fail(
    tmp_path: Path,
    _case_name: str,
    source: str,
    expected_kind: str,
) -> None:
    path = _make_test_file(tmp_path, "sample.test.tsx", source)

    findings = check_source(path)

    assert any(finding.kind == expected_kind for finding in findings)


def test_typescript_uninspectable_callback_fails(tmp_path: Path) -> None:
    path = _make_test_file(
        tmp_path,
        "sample.test.ts",
        """
        const helper = () => expect(loadValue()).toBe(42);
        it('passes a callback reference', helper);
        """,
    )

    findings = check_source(path)

    assert any(finding.kind == "uninspectable" for finding in findings)


def test_typescript_unsupported_registration_does_not_hide_behind_real_test(
    tmp_path: Path,
) -> None:
    path = _make_test_file(
        tmp_path,
        "sample.test.ts",
        """
        it('has a real assertion', () => {
          expect(loadValue()).toBe(42);
        });
        it.unknownModifier('cannot be inspected', () => {
          expect(loadOtherValue()).toBe(7);
        });
        """,
    )

    findings = check_source(path)

    assert any(finding.kind == "uninspectable" for finding in findings)


def test_typescript_structural_parse_error_is_a_finding(tmp_path: Path) -> None:
    path = _make_test_file(
        tmp_path,
        "sample.test.tsx",
        """
        it('has an unclosed callback', () => {
          expect(loadValue()).toBe(42);
        """,
    )

    findings = check_source(path)

    assert len(findings) == 1
    assert findings[0].kind == "parse_error"
    assert "parse failed" in findings[0].description


@pytest.mark.parametrize(
    "source",
    [
        "it('bad string', () => { const value = 'unterminated;\n });",
        "it('bad comment', () => { /* unterminated",
        "it('bad regex', () => { expect(value).toMatch(/unterminated\n); });",
        "it('bad template', () => { const value = `unterminated;",
    ],
    ids=["string", "block-comment", "regex", "template"],
)
def test_typescript_lexical_parse_errors_are_findings(
    tmp_path: Path,
    source: str,
) -> None:
    path = _make_test_file(tmp_path, "sample.test.tsx", source)

    findings = check_source(path)

    assert len(findings) == 1
    assert findings[0].kind == "parse_error"
    assert "parse failed" in findings[0].description


def test_typescript_invalid_utf8_is_a_finding(tmp_path: Path) -> None:
    path = tmp_path / "sample.test.ts"
    path.write_bytes(b"\xff")

    findings = check_source(path)

    assert len(findings) == 1
    assert findings[0].kind == "parse_error"
    assert "could not be read as UTF-8" in findings[0].description


def test_typescript_without_supported_registration_fails(tmp_path: Path) -> None:
    path = _make_test_file(
        tmp_path,
        "sample.test.ts",
        """
        export const helper = () => 42;
        """,
    )

    findings = check_source(path)

    assert any(finding.kind == "uninspectable" for finding in findings)


def test_typescript_cli_returns_fail_and_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    weak_path = _make_test_file(
        tmp_path,
        "weak.test.ts",
        "it('weak', () => expect(true).toBe(true));",
    )
    strong_path = _make_test_file(
        tmp_path,
        "strong.test.tsx",
        "it('strong', () => expect(loadValue()).toBe(42));",
    )

    assert main([str(weak_path)]) == 1
    weak_output = capsys.readouterr()
    assert "Weak-test check failed" in weak_output.err
    assert main([str(strong_path)]) == 0
    strong_output = capsys.readouterr()
    assert "Weak-test check passed" in strong_output.out


# ---------------------------------------------------------------------------
# Tests — TypeScript / TSX positive fixtures
# ---------------------------------------------------------------------------

_TYPESCRIPT_STRONG_CASES = [
    (
        "real equality",
        """
        it('checks a computed result', () => {
          const actual = 1 + 1;
          expect(actual).toBe(2);
        });
        """,
    ),
    (
        "repeated calls are not assumed equal",
        """
        it('compares two independent calls', () => {
          expect(loadValue()).toBe(loadValue());
        });
        """,
    ),
    (
        "DOM matcher",
        """
        it('checks rendered output', () => {
          expect(screen.getByText('ready')).toBeInTheDocument();
        });
        """,
    ),
    (
        "promise rejection",
        """
        it('checks a rejection', async () => {
          await expect(loadMissing()).rejects.toThrow('missing');
        });
        """,
    ),
    (
        "throw matcher",
        """
        it('checks an exception', () => {
          expect(() => parseValue('bad')).toThrow(Error);
        });
        """,
    ),
    (
        "assert call",
        """
        test('uses assert', () => {
          assert(loadValue() === 42);
        });
        """,
    ),
    (
        "parameterized test",
        """
        it.each([1, 2])('keeps %s positive', (value: number) => {
          expect(value).toBeGreaterThan(0);
        });
        """,
    ),
    (
        "expression callback",
        """
        it('supports an expression callback', () => expect(loadValue()).toBe(42));
        """,
    ),
    (
        "function callback",
        """
        it('supports a function callback', function () {
          expect(loadValue()).toBe(42);
        });
        """,
    ),
    (
        "typed callback parameter",
        """
        it('handles a function type', (callback: () => void) => {
          expect(callback).toBeDefined();
        });
        """,
    ),
    (
        "TSX and regex",
        """
        it('handles TSX and regex literals', () => {
          const view = <div data-kind="result">ready</div>;
          expect(view.props.children).toMatch(/^ready$/);
        });
        """,
    ),
]


@pytest.mark.parametrize(
    ("_case_name", "source"),
    _TYPESCRIPT_STRONG_CASES,
    ids=[case[0] for case in _TYPESCRIPT_STRONG_CASES],
)
def test_typescript_strong_shapes_pass(
    tmp_path: Path,
    _case_name: str,
    source: str,
) -> None:
    path = _make_test_file(tmp_path, "sample.test.tsx", source)

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
