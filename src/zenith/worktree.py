"""Disposable Git worktrees for implementation runs.

A worktree isolates *what may change* (diff scope; the primary working tree is never written),
not *what code may do* — see sandbox.py and ADR-005. Hooks are disabled and Git never prompts.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .kernel import ZenithError

_GIT = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false"]
_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(cwd: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run([*_GIT, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              check=False, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ZenithError(f"git unavailable or timed out: {exc}") from exc


def head_of(repo: Path) -> str:
    r = _git(repo, "rev-parse", "HEAD")
    if r.returncode != 0 or not _HEAD_RE.match(r.stdout.strip()):
        raise ZenithError("Source is not a Git repository with a commit")
    return r.stdout.strip()


def create(repo: Path, dest: Path, base_head: str, branch: str) -> Path:
    """`git worktree add -b <branch> <dest> <base_head>`; dest must not exist."""
    if not _HEAD_RE.match(base_head):
        raise ZenithError("base_head must be a full commit hash")
    if not re.fullmatch(r"zenith/[A-Za-z0-9._/-]{1,120}", branch):
        raise ZenithError("Branch must be zenith/<task>/<attempt>")
    if dest.exists():
        raise ZenithError(f"Worktree path already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    r = _git(repo, "worktree", "add", "--no-checkout", "-b", branch, str(dest), base_head)
    if r.returncode != 0:
        raise ZenithError(f"git worktree add failed: {r.stderr.strip()[:300]}")
    r = _git(dest, "checkout", "--quiet", base_head, "--")
    r2 = _git(dest, "checkout", "--quiet", branch) if r.returncode == 0 else r
    if r2.returncode != 0:
        raise ZenithError(f"git checkout in worktree failed: {r2.stderr.strip()[:300]}")
    return dest


def changed_paths(worktree: Path) -> list[str]:
    """Every modified, added, deleted or untracked path relative to the worktree root."""
    r = _git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    if r.returncode != 0:
        raise ZenithError(f"git status failed: {r.stderr.strip()[:300]}")
    out: list[str] = []
    parts = r.stdout.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if not entry:
            i += 1
            continue
        code, path = entry[:2], entry[3:]
        out.append(path)
        if "R" in code or "C" in code:  # renamed/copied: the next NUL-separated item is the original
            i += 1
            if i < len(parts) and parts[i]:
                out.append(parts[i])
        i += 1
    return sorted(set(out))


def remove(repo: Path, dest: Path, branch: str | None = None) -> None:
    """Explicit cleanup only; failed runs keep their worktree for inspection."""
    r = _git(repo, "worktree", "remove", "--force", str(dest))
    if r.returncode != 0:
        raise ZenithError(f"git worktree remove failed: {r.stderr.strip()[:300]}")
    if branch:
        _git(repo, "branch", "-D", branch)
