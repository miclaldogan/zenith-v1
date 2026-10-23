"""Provider-independent adapter interface.

Adapters receive validated contracts and return *raw dicts* that Zenith validates again.
They never touch task state, approvals, evidence or the database, and never receive
credentials from Zenith. Failures are classified so the orchestrator can decide whether a
retry is safe: director stages are side-effect free, implementation is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

FAILURE_KINDS = ("auth", "invalid_output", "timeout", "crash", "cancelled", "budget",
                 "connection", "interrupted", "policy_violation", "unsupported")


class AdapterError(Exception):
    def __init__(self, kind: str, message: str):
        if kind not in FAILURE_KINDS:
            raise ValueError(kind)
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class AgentCapabilities:
    streaming: bool = False
    resume: bool = False
    structured_output: bool = True
    budget_cap: bool = False
    sandbox_modes: tuple[str, ...] = ()
    live: bool = False  # True only for adapters that call a real provider


@dataclass(frozen=True)
class AuthStatus:
    ok: bool
    detail: str  # never contains a token


@dataclass
class RunContext:
    """What Zenith hands an adapter for one stage call."""
    run_id: str
    stage: str
    deadline_seconds: float
    should_cancel: Callable[[], bool] = field(default=lambda: False)
    worktree: Path | None = None       # implementer only: the sole writable location
    read_root: Path | None = None      # director stages: repository mounted read-only
    allowed_scope: list[str] = field(default_factory=list)
    excluded_scope: list[str] = field(default_factory=list)
    emit: Callable[[str, dict], None] = field(default=lambda kind, payload: None)  # -> run_events


class DirectorAdapter(Protocol):
    name: str

    def capabilities(self) -> AgentCapabilities: ...
    def check_auth(self) -> AuthStatus: ...
    def plan(self, ctx: RunContext, context_package: dict) -> dict:
        """Return {"architecture_proposal": {...}, "implementation_prompt": {...}} (raw)."""
    def review(self, ctx: RunContext, context_package: dict, prompt: dict, report: dict,
               verification: dict) -> dict:
        """Return a raw review_findings dict. Advisory only."""


class ImplementerAdapter(Protocol):
    name: str

    def capabilities(self) -> AgentCapabilities: ...
    def check_auth(self) -> AuthStatus: ...
    def implement(self, ctx: RunContext, prompt: dict) -> dict:
        """Modify ctx.worktree only; return a raw implementation_report dict (claims)."""
    def cancel(self, ctx: RunContext) -> None: ...
