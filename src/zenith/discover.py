"""Safe project discovery under owner-approved roots (dry-run first, bounded, no content reads).

Never walks the whole home directory; refuses `/`, the home directory itself and credential
stores; never follows symlinks; skips hidden, build and sensitive directories; stops at
MAX_DIRS / MAX_DEPTH. A candidate is a directory that looks like a project (Git repository or
a recognised project marker). Registration happens only for the indices the owner selects.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import policy
from .kernel import Store, ZenithError

MAX_DEPTH = 3
MAX_DIRS = 2000
MARKERS = ("pyproject.toml", "setup.py", "package.json", "Cargo.toml", "go.mod", "CMakeLists.txt", "Makefile",
           "README.md", "README.rst", "README", "project.godot", "requirements.txt", "pom.xml", "build.gradle")
SKIP = policy.SKIP_DIR_NAMES | {"dist", "build", "target", "site-packages", ".cache", "Downloads", "Trash"}


@dataclass
class Candidate:
    index: int
    path: str
    name: str
    kind: str                 # git | marker
    markers: list[str]
    registered: str | None    # project name if this exact path is already a source
    inside_registered: str | None   # project name if a registered source contains this path


def _approved_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ZenithError("Root must be an existing directory")
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ZenithError("Refusing to discover under the filesystem root or the whole home directory; "
                          "name a projects folder (e.g. ~/projects)")
    if any(part.lower() in policy.SENSITIVE_DIR_NAMES for part in root.parts):
        raise ZenithError("Refusing a credential-store directory")
    allowed = policy.allowed_roots()
    if allowed and not any(root == r or r in root.parents for r in allowed):
        raise ZenithError("Root is outside ZENITH_ALLOWED_ROOTS")
    return root


def discover(store: Store, root: str, max_depth: int = MAX_DEPTH, max_dirs: int = MAX_DIRS) -> dict:
    """Dry run: list candidates under `root`. Reads directory names and marker file names only."""
    base = _approved_root(root)
    sources = [(Path(s["path"]), p["name"]) for p in store.list_projects() for s in p["sources"]]
    found: list[Candidate] = []
    visited = 0
    truncated = False
    stack: list[tuple[Path, int]] = [(base, 0)]
    while stack:
        current, depth = stack.pop()
        if visited >= max_dirs:
            truncated = True
            break
        visited += 1
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        names = {e.name for e in entries}
        is_git = ".git" in names
        markers = [m for m in MARKERS if m in names]
        if current != base and (is_git or markers):
            registered = next((n for sp, n in sources if sp == current), None)
            inside = next((n for sp, n in sources if sp != current and sp in current.parents), None)
            found.append(Candidate(len(found) + 1, str(current), current.name, "git" if is_git else "marker",
                                   markers[:4], registered, inside))
            if is_git:
                continue  # a repository is a unit; do not descend into it
        if depth >= max_depth:
            continue
        for e in reversed(entries):
            try:
                if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if e.name.startswith(".") or e.name in SKIP or policy.sensitive_reason(e.name, is_dir=True):
                continue
            stack.append((Path(e.path), depth + 1))
    return {"root": str(base), "candidates": [asdict(c) for c in found], "visited_dirs": visited,
            "truncated": truncated, "limits": {"max_depth": max_depth, "max_dirs": max_dirs},
            "note": "Dry run. Nothing registered. Use --add N[,N] to register selected candidates (as new projects) "
                    "or --attach N --project ID to add one as a source of an existing project."}


def register(store: Store, listing: dict, add: list[int], attach_to: str | None = None) -> list[dict]:
    """Register only the selected candidates. Existing identity/trust rules apply (kernel)."""
    out = []
    by_index = {c["index"]: c for c in listing["candidates"]}
    for n in add:
        c = by_index.get(n)
        if c is None:
            raise ZenithError(f"No candidate #{n} in this listing")
        if c["registered"]:
            out.append({"index": n, "path": c["path"], "skipped": f"already registered to {c['registered']}"})
            continue
        if attach_to:
            sid = store.add_source(attach_to, c["path"], label=c["name"])
            out.append({"index": n, "path": c["path"], "attached_source": sid, "project_id": attach_to})
        else:
            pid = store.add_project(c["name"], c["path"])
            out.append({"index": n, "path": c["path"], "project_id": pid, "name": c["name"]})
    return out
