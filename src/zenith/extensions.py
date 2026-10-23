"""Extension registry (REQ-003): discover, describe, enable and instantiate provider adapters.

Mechanism: Python package entry points (`importlib.metadata`, stdlib) in the group
`zenith.adapters`, merged with the built-in adapters. Each adapter exposes a manifest naming
its kind, contract version and declared permissions. Discovery is *not* isolation: an
imported plugin runs with Zenith's privileges, so live or privileged adapters must be enabled
explicitly (`ZENITH_ENABLED_ADAPTERS`) and all policy, approvals, evidence and audit stay in
the kernel. Pluggy and hot reloading were evaluated and deferred (ADR-006).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from importlib import metadata

from .agents.base import DirectorAdapter, ImplementerAdapter
from .agents.fake import FakeDirector, FakeImplementer
from .agents.providers import ClaudeCodeDirector, ClaudeCodeImplementer, CodexDirector, CodexImplementer
from .contracts import SCHEMA_VERSION
from .kernel import ZenithError

ENTRY_POINT_GROUP = "zenith.adapters"
KINDS = ("director", "implementer", "conversation")


@dataclass(frozen=True)
class AdapterManifest:
    name: str
    kind: str                      # director | implementer
    contract: str                  # director protocol schema version it speaks
    factory: object                # callable(script: dict | None) -> adapter
    live: bool = False             # calls a real provider (needs explicit enablement)
    permissions: tuple[str, ...] = ()   # e.g. ("network", "credentials:claude")
    source: str = "builtin"        # builtin | entry-point:<dist>
    version: str = "0"

    def describe(self) -> dict:
        d = asdict(self)
        d.pop("factory")
        return d


def _fake_conversation(script=None):
    from .misa.conversation import FakeConversationProvider
    return FakeConversationProvider(script)


def _claude_conversation(script=None):
    from .misa.conversation import ClaudeCodeConversation
    return ClaudeCodeConversation(script)


def _api_conversation(script=None):
    from .misa.conversation import AnthropicApiConversation
    return AnthropicApiConversation(script)


_BUILTIN = (
    AdapterManifest("fake-director", "director", SCHEMA_VERSION, FakeDirector, live=False, permissions=()),
    AdapterManifest("fake-implementer", "implementer", SCHEMA_VERSION, FakeImplementer, live=False,
                    permissions=("worktree:write",)),
    # Live provider adapters: disabled until ZENITH_ENABLED_ADAPTERS names them (ADR-006/007).
    AdapterManifest("claude-code-director", "director", SCHEMA_VERSION, ClaudeCodeDirector, live=True,
                    permissions=("network:api.anthropic.com:443", "credentials:anthropic-api-key", "repo:read"),
                    version="cli-2.1.309-verified"),
    AdapterManifest("claude-code-implementer", "implementer", SCHEMA_VERSION, ClaudeCodeImplementer, live=True,
                    permissions=("network:api.anthropic.com:443", "credentials:anthropic-api-key", "worktree:write",
                                 "tools:edit-only"), version="cli-2.1.309-verified"),
    AdapterManifest("codex-director", "director", SCHEMA_VERSION, CodexDirector, live=True,
                    permissions=("network:openai", "credentials:codex-auth", "repo:read", "commands:read-only-sandbox"),
                    version="cli-0.162.1-verified"),
    AdapterManifest("codex-implementer", "implementer", SCHEMA_VERSION, CodexImplementer, live=True,
                    permissions=("unsupported:autonomous-execution",), version="cli-0.162.1-verified"),
    AdapterManifest("fake-conversation", "conversation", SCHEMA_VERSION, _fake_conversation, live=False),
    AdapterManifest("claude-code-conversation", "conversation", SCHEMA_VERSION, _claude_conversation, live=True,
                    permissions=("network:api.anthropic.com:443", "credentials:anthropic-api-key-only", "context:read-only"),
                    version="cli-2.1.309-verified"),
    AdapterManifest("anthropic-api-conversation", "conversation", SCHEMA_VERSION, _api_conversation, live=True,
                    permissions=("network:api.anthropic.com:443", "credentials:anthropic-api-key", "context:read-only", "no-tools"),
                    version="messages-api-2023-06-01"),
)


def enabled_names() -> set[str]:
    raw = os.environ.get("ZENITH_ENABLED_ADAPTERS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def discover() -> dict[str, AdapterManifest]:
    """Built-ins plus entry points. A broken entry point is reported, never imported twice."""
    found: dict[str, AdapterManifest] = {m.name: m for m in _BUILTIN}
    try:
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # pragma: no cover - importlib.metadata oddities
        eps = ()
    for ep in eps:
        try:
            obj = ep.load()
            manifest = obj() if callable(obj) and not isinstance(obj, AdapterManifest) else obj
            if not isinstance(manifest, AdapterManifest) or manifest.kind not in KINDS:
                raise TypeError("entry point must yield an AdapterManifest")
            dist = ep.dist.name if ep.dist else "?"
            manifest = AdapterManifest(**{**asdict(manifest), "factory": manifest.factory,
                                          "source": f"entry-point:{dist}"})
        except Exception as exc:
            found[f"broken:{ep.name}"] = AdapterManifest(ep.name, "broken", "?", None, source=f"error:{exc}"[:200])
            continue
        if manifest.name in found and found[manifest.name].source == "builtin":
            continue  # built-ins cannot be shadowed by a plugin
        found[manifest.name] = manifest
    return found


def describe_all() -> list[dict]:
    enabled = enabled_names()
    out = []
    for m in discover().values():
        d = m.describe()
        d["enabled"] = (not m.live and m.kind in KINDS) or m.name in enabled
        out.append(d)
    return sorted(out, key=lambda d: (d["kind"], d["name"]))


def load(name: str, kind: str, script: dict | None = None):
    """Instantiate an adapter after checking kind, contract version and enablement."""
    manifests = discover()
    m = manifests.get(name)
    if m is None or m.kind == "broken":
        raise ZenithError(f"Unknown {kind} adapter: {name}")
    if m.kind != kind:
        raise ZenithError(f"{name} is a {m.kind} adapter, not {kind}")
    if m.contract != SCHEMA_VERSION:
        raise ZenithError(f"{name} speaks {m.contract}; this Zenith requires {SCHEMA_VERSION}")
    if m.live and name not in enabled_names():
        raise ZenithError(f"{name} is a live provider adapter; enable it explicitly with ZENITH_ENABLED_ADAPTERS")
    adapter = m.factory(script) if script is not None else m.factory()
    expected = {"director": DirectorAdapter, "implementer": ImplementerAdapter}.get(kind, DirectorAdapter)
    for attr in ("name", "capabilities", "check_auth"):
        if not hasattr(adapter, attr):
            raise ZenithError(f"{name} does not implement the {expected.__name__} port ({attr} missing)")
    return adapter
