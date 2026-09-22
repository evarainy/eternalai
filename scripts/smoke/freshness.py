"""Value-safe local source freshness diagnostics for smoke commands."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

_UNKNOWN: Literal["unknown"] = "unknown"
_FULL_SHA_PATTERN = re.compile(rb"^[0-9a-f]{40}$")
_SHORT_SHA_PATTERN = re.compile(r"^[0-9a-f]{12}$")
_GIT_TIMEOUT_SECONDS = 10
_KNOWN_MAIN_REF = "refs/heads/phase0/main"
_GIT_ENVIRONMENT_OVERRIDE_NAMES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_OPTIONAL_LOCKS",
        "GIT_WORK_TREE",
    }
)

SourceHeadAttachment: TypeAlias = Literal["attached", "detached", "unknown"]
SourceDirty: TypeAlias = bool | Literal["unknown"]
SourceMainRef: TypeAlias = Literal["present", "missing", "unknown"]
SourceHistory: TypeAlias = Literal["complete", "shallow", "unknown"]
SourceRelation: TypeAlias = Literal[
    "same",
    "ahead",
    "behind",
    "diverged",
    "unknown",
]
SourceBehindCommits: TypeAlias = int | Literal["unknown"]
SourceMainEvidence: TypeAlias = Literal["local_only", "unavailable"]
SourceGate: TypeAlias = Literal["continue", "warning"]


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """A constrained diagnostic result that is safe for direct CLI rendering."""

    target_commit: str
    head_attachment: SourceHeadAttachment
    dirty: SourceDirty
    main_ref: SourceMainRef
    known_main_commit: str
    history: SourceHistory
    relation: SourceRelation
    behind_commits: SourceBehindCommits
    main_evidence: SourceMainEvidence
    remote_check: Literal["not_run"]
    gate: SourceGate


def inspect_source_freshness(repo_root: Path) -> SourceFreshness:
    """Inspect only local Git evidence and degrade safely when it is unavailable."""

    target_commit: str | None = None
    try:
        target_commit = _read_commit(repo_root, "HEAD^{commit}")
        head_attachment = _head_attachment(repo_root)
        dirty = _is_dirty(repo_root)
        history = _history(repo_root)
        if not _known_main_ref_is_present(repo_root):
            return SourceFreshness(
                target_commit=_short_sha(target_commit),
                head_attachment=head_attachment,
                dirty=dirty,
                main_ref="missing",
                known_main_commit=_UNKNOWN,
                history=history,
                relation="unknown",
                behind_commits=_UNKNOWN,
                main_evidence="local_only",
                remote_check="not_run",
                gate="warning",
            )

        known_main_commit = _read_commit(repo_root, f"{_KNOWN_MAIN_REF}^{{commit}}")
        relation, behind_commits = _classify_relation(
            repo_root,
            target_commit=target_commit,
            known_main_commit=known_main_commit,
            history=history,
        )
        return SourceFreshness(
            target_commit=_short_sha(target_commit),
            head_attachment=head_attachment,
            dirty=dirty,
            main_ref="present",
            known_main_commit=_short_sha(known_main_commit),
            history=history,
            relation=relation,
            behind_commits=behind_commits,
            main_evidence="local_only",
            remote_check="not_run",
            gate="continue" if relation in {"same", "ahead"} else "warning",
        )
    except Exception:
        return _unknown_freshness(target_commit)


def _unknown_freshness(target_commit: str | None = None) -> SourceFreshness:
    target = _short_sha(target_commit) if target_commit is not None else _UNKNOWN
    return SourceFreshness(
        target_commit=target,
        head_attachment="unknown",
        dirty=_UNKNOWN,
        main_ref="unknown",
        known_main_commit=_UNKNOWN,
        history="unknown",
        relation="unknown",
        behind_commits=_UNKNOWN,
        main_evidence="unavailable",
        remote_check="not_run",
        gate="warning",
    )


def _read_commit(repo_root: Path, revision: str) -> str:
    completed = _run_git(repo_root, "rev-parse", "--verify", revision)
    if completed.returncode != 0:
        raise ValueError("git_commit_unavailable")
    raw_commit = completed.stdout.strip()
    if _FULL_SHA_PATTERN.fullmatch(raw_commit) is None:
        raise ValueError("git_commit_invalid")
    return raw_commit.decode("ascii")


def _head_attachment(repo_root: Path) -> SourceHeadAttachment:
    completed = _run_git(repo_root, "symbolic-ref", "--quiet", "HEAD")
    if completed.returncode == 0:
        return "attached"
    if completed.returncode == 1:
        return "detached"
    raise ValueError("git_head_attachment_unavailable")


def _is_dirty(repo_root: Path) -> bool:
    completed = _run_git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
    )
    if completed.returncode != 0:
        raise ValueError("git_dirty_state_unavailable")
    return bool(completed.stdout)


def _history(repo_root: Path) -> SourceHistory:
    completed = _run_git(repo_root, "rev-parse", "--is-shallow-repository")
    if completed.returncode != 0:
        raise ValueError("git_history_unavailable")
    value = completed.stdout.strip()
    if value == b"true":
        return "shallow"
    if value == b"false":
        return "complete"
    raise ValueError("git_history_invalid")


def _known_main_ref_is_present(repo_root: Path) -> bool:
    completed = _run_git(repo_root, "show-ref", "--verify", "--quiet", _KNOWN_MAIN_REF)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ValueError("git_main_ref_unavailable")


def _classify_relation(
    repo_root: Path,
    *,
    target_commit: str,
    known_main_commit: str,
    history: SourceHistory,
) -> tuple[SourceRelation, SourceBehindCommits]:
    if target_commit == known_main_commit:
        return "same", 0

    if _is_ancestor(repo_root, target_commit, known_main_commit):
        behind_commits = _behind_commit_count(
            repo_root,
            target_commit=target_commit,
            known_main_commit=known_main_commit,
        )
        return "behind", behind_commits

    if _is_ancestor(repo_root, known_main_commit, target_commit):
        return "ahead", 0

    if history == "complete":
        return "diverged", _UNKNOWN
    return "unknown", _UNKNOWN


def _is_ancestor(repo_root: Path, left: str, right: str) -> bool:
    completed = _run_git(repo_root, "merge-base", "--is-ancestor", left, right)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ValueError("git_ancestor_relation_unavailable")


def _behind_commit_count(
    repo_root: Path,
    *,
    target_commit: str,
    known_main_commit: str,
) -> int:
    completed = _run_git(
        repo_root,
        "rev-list",
        "--count",
        f"{target_commit}..{known_main_commit}",
    )
    if completed.returncode != 0:
        raise ValueError("git_behind_count_unavailable")
    raw_count = completed.stdout.strip()
    if not raw_count.isdigit():
        raise ValueError("git_behind_count_invalid")
    behind_commits = int(raw_count)
    if behind_commits < 1:
        raise ValueError("git_behind_count_invalid")
    return behind_commits


def _short_sha(full_sha: str) -> str:
    if _SHORT_SHA_PATTERN.fullmatch(full_sha[:12]) is None:
        return _UNKNOWN
    return full_sha[:12]


def _run_git(
    repo_root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        env=_safe_git_environment(),
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _safe_git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        if (
            name in _GIT_ENVIRONMENT_OVERRIDE_NAMES
            or name.startswith("GIT_CONFIG_")
            or name.startswith("GIT_TRACE")
        ):
            environment.pop(name, None)
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment
