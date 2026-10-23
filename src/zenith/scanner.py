"""Bounded, read-only metadata scanner. Never opens file contents; never follows symlinks."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .policy import SKIP_DIR_NAMES, sensitive_reason

MAX_FILES = 20_000
MAX_SECONDS = 10.0

# core.fsmonitor in a repository's own config can execute a command; force it off.
# --no-optional-locks stops `git status` from refreshing (writing) the index.
_GIT_BASE = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false"]


def scan_tree(root: Path, max_files: int = MAX_FILES, max_seconds: float = MAX_SECONDS,
              clock=time.monotonic) -> dict:
    """Fingerprint (relpath, size, mtime) of regular files below `root`.

    Symlinks are counted, never followed or stat'ed through. Sensitive entries are counted by
    reason only. The walk is deterministic (sorted) and stops at the file/time bounds.
    """
    root = root.resolve(strict=True)
    started = clock()
    digest = hashlib.sha256()
    counts = {"symlinks": 0, "symlinks_escaping_root": 0, "sensitive": 0, "unreadable": 0}
    files = dirs = total_bytes = newest = 0
    truncated = False
    stack = [root]
    while stack and not truncated:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            counts["unreadable"] += 1
            continue
        subdirs = []
        for entry in entries:
            if files >= max_files or clock() - started > max_seconds:
                truncated = True
                break
            try:
                if entry.is_symlink():
                    counts["symlinks"] += 1
                    target = Path(os.path.realpath(entry.path))
                    if target != root and root not in target.parents:
                        counts["symlinks_escaping_root"] += 1
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if sensitive_reason(entry.name, is_dir):
                    counts["sensitive"] += 1
                    continue
                if is_dir:
                    if entry.name not in SKIP_DIR_NAMES:
                        dirs += 1
                        subdirs.append(Path(entry.path))
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                counts["unreadable"] += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(entry.path, root)
            digest.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8", "surrogateescape"))
            files += 1
            total_bytes += st.st_size
            newest = max(newest, st.st_mtime_ns)
        stack.extend(reversed(subdirs))
    return {
        "file_count": files, "dir_count": dirs, "total_bytes": total_bytes,
        "latest_mtime": (datetime.fromtimestamp(newest / 1e9, timezone.utc).isoformat(timespec="seconds")
                         if newest else None),
        "fingerprint": digest.hexdigest(), "skipped": counts, "truncated": truncated,
        "limits": {"max_files": max_files, "max_seconds": max_seconds},
    }


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run([*_GIT_BASE, *args], cwd=path, capture_output=True, text=True,
                          timeout=10, check=False, env=env)


def git_metadata(path: Path) -> dict:
    """Branch / HEAD / tracked-change count. No file contents, no commit messages."""
    result: dict = {"is_git_repo": False}
    try:
        top = _git(path, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return result
        root = top.stdout.strip()
        result.update(is_git_repo=True, git_root=root,
                      git_root_outside_source=Path(root).resolve() != path.resolve())
        branch = _git(path, "branch", "--show-current")
        head = _git(path, "rev-parse", "HEAD")
        status = _git(path, "status", "--porcelain", "--untracked-files=no")
        last = _git(path, "log", "-1", "--format=%cI")
        result["branch"] = branch.stdout.strip() if branch.returncode == 0 else None
        result["head"] = head.stdout.strip() if head.returncode == 0 else None
        result["tracked_changed_count"] = len(status.stdout.splitlines()) if status.returncode == 0 else None
        result["last_commit_at"] = last.stdout.strip() if last.returncode == 0 and last.stdout.strip() else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result["git_status"] = "git unavailable or timeout"
    return result


def commits_between(path: Path, old: str, new: str) -> int | None:
    """Number of commits reachable from `new` but not `old`; None if undeterminable."""
    try:
        out = _git(path, "rev-list", "--count", f"{old}..{new}")
        return int(out.stdout.strip()) if out.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None
