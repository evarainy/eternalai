"""Compatibility exports for Golden Task assertion tests."""

from scripts.golden_task_assertions import (
    AssertionJudgement,
    assert_adapter_calls,
    assert_forbidden_absent,
    assert_response_matches,
    assert_terminal_state_matrix,
    assert_trace_sequence_contains,
    judge_assertions,
)

__all__ = (
    "AssertionJudgement",
    "assert_adapter_calls",
    "assert_forbidden_absent",
    "assert_response_matches",
    "assert_terminal_state_matrix",
    "assert_trace_sequence_contains",
    "judge_assertions",
)
