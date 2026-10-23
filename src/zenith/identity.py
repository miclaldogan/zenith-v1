"""REQ-004: agent identity and attribution.

Stable internal identifiers never change; display names, signatures, Git identity and role
assignments are configuration. Nothing here is hard-coded to a person or an email: values
come from the environment (a config file later) or from the owner's own `git config`, and a
missing value is reported as missing, never invented. No Git write happens in this module.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .kernel import ZenithError

PLATFORM_ID = "zenith.platform"                 # the product and orchestration platform
ASSISTANT_ID = "zenith.assistant.primary"       # user-facing assistant persona (display name configurable)
ROLES = ("engineering_director", "implementer", "reviewer", "conversation", "verifier")
_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


@dataclass(frozen=True)
class Identity:
    id: str
    display_name: str
    kind: str              # platform | assistant | owner | provider
    email: str | None = None

    def git_identity(self) -> str:
        if not self.email:
            raise ZenithError(f"{self.display_name} has no configured email; refusing to invent one")
        return f"{self.display_name} <{self.email}>"


@dataclass
class IdentityConfig:
    assistant: Identity
    platform: Identity
    owner: Identity | None
    roles: dict[str, str] = field(default_factory=dict)    # role -> adapter name (functional, not permanent)
    coauthors: list[Identity] = field(default_factory=list)

    def describe(self) -> dict:
        return {"assistant": asdict(self.assistant), "platform": asdict(self.platform),
                "owner": asdict(self.owner) if self.owner else None, "roles": dict(self.roles),
                "coauthors": [asdict(c) for c in self.coauthors],
                "notification_signature": self.signature()}

    def signature(self) -> str:
        return f"— {self.assistant.display_name} ({self.platform.display_name})"


def _git_config(key: str, cwd: Path | None = None) -> str | None:
    """The owner's own Git identity, read from their config; never a credential."""
    try:
        r = subprocess.run(["git", "config", "--get", key], cwd=cwd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def _parse_person(spec: str) -> Identity:
    m = re.fullmatch(r"\s*(.+?)\s*<([^<>]+)>\s*", spec)
    if not m or not _EMAIL_RE.match(m.group(2)):
        raise ZenithError(f"Identity must look like 'Name <email>': {spec!r}")
    return Identity(id=f"person:{m.group(2).lower()}", display_name=m.group(1), kind="owner", email=m.group(2))


def load(env: dict | None = None, repo: Path | None = None) -> IdentityConfig:
    env = os.environ if env is None else env
    assistant = Identity(ASSISTANT_ID, env.get("ZENITH_ASSISTANT_NAME", "Misa"), "assistant",
                         env.get("ZENITH_ASSISTANT_EMAIL") or None)
    platform = Identity(PLATFORM_ID, "Zenith", "platform", env.get("ZENITH_PLATFORM_EMAIL") or None)
    owner_name = env.get("ZENITH_OWNER_NAME") or _git_config("user.name", repo)
    owner_email = env.get("ZENITH_OWNER_EMAIL") or _git_config("user.email", repo)
    owner = Identity(f"person:{owner_email.lower()}", owner_name, "owner", owner_email) \
        if owner_name and owner_email and _EMAIL_RE.match(owner_email) else None
    roles = {"engineering_director": env.get("ZENITH_ROLE_ENGINEERING_DIRECTOR", "codex-director"),
             "implementer": env.get("ZENITH_ROLE_IMPLEMENTER", "claude-code-implementer"),
             "reviewer": env.get("ZENITH_ROLE_REVIEWER", "codex-director"),
             "conversation": env.get("ZENITH_ROLE_CONVERSATION", "claude-code-conversation"),
             "verifier": "zenith-verifier"}
    coauthors = [_parse_person(x) for x in env.get("ZENITH_COAUTHORS", "").split(";") if x.strip()]
    return IdentityConfig(assistant, platform, owner, roles, coauthors)


def git_trailers(author: Identity, contributors: list[Identity]) -> list[str]:
    """`Co-authored-by:` trailers for contributors who are not the author; duplicates removed.

    Call it only with identities that genuinely contributed to that commit (REQ-004): a
    provider model is not a Git identity; the assistant counts when Zenith generated or
    applied the change.
    """
    seen = {author.email.lower()} if author.email else set()
    out: list[str] = []
    for c in contributors:
        if not c.email:
            raise ZenithError(f"Co-author {c.display_name} has no configured email; refusing to invent one")
        if c.email.lower() in seen:
            continue
        seen.add(c.email.lower())
        out.append(f"Co-authored-by: {c.git_identity()}")
    return out


def commit_message(subject: str, body: str, author: Identity, contributors: list[Identity]) -> str:
    """Preview of a governed commit message (REQ-002). Nothing is committed here."""
    if not subject.strip() or len(subject) > 72:
        raise ZenithError("Commit subject must be 1–72 characters")
    parts = [subject.strip()]
    if body.strip():
        parts += ["", body.strip()]
    trailers = git_trailers(author, contributors)
    if trailers:
        parts += ["", *trailers]
    return "\n".join(parts) + "\n"
