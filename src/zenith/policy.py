"""Sensitivity, path and redaction policy. Pure functions; no I/O except path resolution."""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path


class PolicyError(Exception):
    """Raised for policy violations; the kernel converts it to ZenithError."""


# Directories whose *contents* are never listed, fingerprinted or read.
SENSITIVE_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws", ".kube", ".azure", ".password-store"})
# Bulky/derived directories skipped by the scanner (Git data is read through `git`, not the tree).
SKIP_DIR_NAMES = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__",
                            ".mypy_cache", ".pytest_cache", ".tox"})

_SENSITIVE_EXACT = frozenset({
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd", "credentials",
    "kubeconfig", "shadow", "wallet.dat", "token", "tokens.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
_SENSITIVE_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "*.kdbx", "*.gpg", "*.secret", "*.secrets", "*secret*", "*credential*",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "service-account*.json",
)


# Patterns that are only *hints* (a source file called secrets.py is usually code). For the
# metadata scanner, which never opens files and never records names, such files are ordinary.
# Any future reader of file *contents* must call sensitive_reason(..., strict=True).
_HINT_PATTERNS = frozenset({"*secret*", "*credential*"})
_CODE_SUFFIXES = frozenset({".py", ".pyi", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
                            ".rb", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".php", ".md", ".rst"})


def sensitive_reason(name: str, is_dir: bool = False, strict: bool = True) -> str | None:
    """Return why a directory entry must be excluded, or None if it is allowed.

    strict=True (default, required for anything that could read contents) excludes every
    credential-like name. strict=False is for the metadata-only scanner and lets a *code* file
    whose name merely contains "secret"/"credential" count in the fingerprint; .env, keys,
    stores and credential directories are excluded in both modes. Excluded entries are
    neither opened nor stat'ed; only a per-reason counter is kept.
    """
    lowered = name.lower()
    if is_dir:
        return "sensitive_dir" if lowered in SENSITIVE_DIR_NAMES else None
    if lowered in _SENSITIVE_EXACT:
        return "sensitive_file"
    for pattern in _SENSITIVE_PATTERNS:
        if fnmatch.fnmatchcase(lowered, pattern):
            if not strict and pattern in _HINT_PATTERNS and Path(lowered).suffix in _CODE_SUFFIXES:
                continue
            return "sensitive_file"
    return None


def path_relation(a: str, b: str) -> str | None:
    """'same', 'is inside', 'contains' or None for two canonical absolute paths."""
    if a == b:
        return "same"
    if a.startswith(b.rstrip("/") + "/"):
        return "is inside"
    if b.startswith(a.rstrip("/") + "/"):
        return "contains"
    return None


def allowed_roots() -> list[Path]:
    """Optional operator allowlist: ZENITH_ALLOWED_ROOTS=/a:/b restricts where sources may live."""
    raw = os.environ.get("ZENITH_ALLOWED_ROOTS", "")
    return [Path(x).expanduser().resolve() for x in raw.split(os.pathsep) if x.strip()]


def validate_source_path(raw: str) -> Path:
    """Resolve and vet a directory that may become a project source.

    Raises FileNotFoundError for missing paths (callers rely on that) and PolicyError for
    paths Zenith must never register.
    """
    p = Path(raw).expanduser().resolve(strict=True)
    if not p.is_dir():
        raise PolicyError("Project path must point to an existing directory")
    if p == Path(p.anchor) or p == Path.home().resolve():
        raise PolicyError("Refusing to register the filesystem root or the whole home directory")
    if any(part.lower() in SENSITIVE_DIR_NAMES for part in p.parts):
        raise PolicyError("Refusing to register a directory inside a credential store")
    roots = allowed_roots()
    if roots and not any(p == r or r in p.parents for r in roots):
        raise PolicyError("Path is outside ZENITH_ALLOWED_ROOTS")
    return p


_REDACTIONS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
)
_KEY_VALUE = re.compile(r"(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|pwd)(\s*[:=]\s*)\S+")


def redact(text: str) -> tuple[str, int]:
    """Mask likely credentials in free text. Returns (clean_text, replacements)."""
    count = 0
    for pat in _REDACTIONS:
        text, n = pat.subn("[REDACTED]", text)
        count += n
    text, n = _KEY_VALUE.subn(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return text, count + n
