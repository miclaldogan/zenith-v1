"""Engineering Director protocol: versioned, provider-independent structured contracts.

Every message between Zenith and a director/implementer adapter is a plain JSON object
validated here. Validation is strict (required keys, types, no unknown keys) so that a model
cannot smuggle extra fields such as `authorization` into an artifact. Nothing here parses
prose; adapters must emit structured output.
"""
from __future__ import annotations

import hashlib
import json
import re

from .kernel import ZenithError

SCHEMA_VERSION = "zenith.director.v1"


class ContractError(ZenithError):
    """Structured output did not satisfy its contract (invalid model output)."""


# Field spec: name -> (type or tuple of types, required). Lists are checked element-wise by _LIST.
_STR, _INT, _NUM, _BOOL, _DICT = str, int, (int, float), bool, dict


def _list(of):
    return ("list", of)


CONTRACTS: dict[str, dict[str, tuple]] = {
    # Zenith -> Director: everything the director is allowed to know, with evidence citations.
    "context_package": {
        "project_id": (_STR, True), "task_id": (_STR, True), "task": (_DICT, True),
        "sources": (_list(_DICT), True), "evidence": (_list(_DICT), True),
        "brief_status": (_DICT, True), "previous_prompt_id": (_STR, False),
        "previous_review_id": (_STR, False), "verification_id": (_STR, False),
        "constraints": (_list(_STR), True), "generated_at": (_STR, True),
    },
    # Director -> Zenith (planning).
    "architecture_proposal": {
        "summary": (_STR, True), "decisions": (_list(_DICT), True), "risks": (_list(_STR), True),
        "decomposition": (_list(_DICT), True), "assumptions": (_list(_STR), False),
    },
    "engineering_decision": {
        "title": (_STR, True), "decision": (_STR, True), "rationale": (_STR, True),
        "alternatives": (_list(_STR), False),
    },
    "task_decomposition": {
        "steps": (_list(_DICT), True), "ordering_rationale": (_STR, False),
    },
    # Director -> Zenith: the implementation prompt. Zenith stamps scope/authorization itself.
    "implementation_prompt": {
        "objective": (_STR, True), "instructions": (_list(_STR), True),
        "architectural_constraints": (_list(_STR), True), "acceptance_criteria": (_list(_STR), True),
        "test_expectations": (_list(_STR), True), "output_requirements": (_list(_STR), True),
        "proposed_scope": (_list(_STR), False), "proposed_exclusions": (_list(_STR), False),
        "revision_reason": (_STR, False),
    },
    # Implementer -> Zenith: claims, never proof.
    "implementation_report": {
        "claimed_status": (_STR, True), "summary": (_STR, True), "files_changed": (_list(_STR), True),
        "tests_claimed": (_list(_DICT), True), "blocking_questions": (_list(_STR), True),
        "notes": (_STR, False),
    },
    # Verifier (Zenith code) -> everyone.
    "verification_report": {
        "passed": (_BOOL, True), "base_head": (_STR, True), "observed_head": (_STR, True),
        "changed_paths": (_list(_STR), True), "out_of_scope_paths": (_list(_STR), True),
        "commands": (_list(_DICT), True), "artifacts": (_list(_DICT), True),
        "findings": (_list(_STR), True), "evidence_ids": (_list(_STR), True),
        "context_fresh": (_BOOL, True), "claims_mismatch": (_list(_STR), True),
    },
    # Director -> Zenith (review). Advisory: verdicts never change state by themselves.
    "review_findings": {
        "verdict": (_STR, True), "findings": (_list(_DICT), True), "summary": (_STR, True),
        "next_step": (_DICT, True),
    },
    "next_step_proposal": {
        "kind": (_STR, True), "reason": (_STR, True), "revised_prompt": (_DICT, False),
    },
    "approval_request": {
        "operation": (_STR, True), "task_id": (_STR, True), "scope_hash": (_STR, True),
        "reason": (_STR, True), "risk": (_STR, True),
    },
    # Misa conversation (provider-independent). Zenith -> provider.
    "conversation_request": {
        "language": (_STR, True), "question": (_STR, True), "question_type": (_STR, True),
        "active_project": (_DICT, False), "topic_project": (_DICT, False), "compare_project": (_DICT, False),
        "focus_ids": (_list(_STR), False),
        "projects": (_list(_DICT), True), "items": (_list(_DICT), True), "recent_turns": (_list(_DICT), True),
        "summary": (_STR, False), "instructions": (_list(_STR), True), "allowed_actions": (_list(_STR), True),
        "limits": (_DICT, True), "generated_at": (_STR, True),
    },
    # provider -> Zenith. `cited_ids` must be a subset of the request items; text is shown to the owner.
    "conversation_reply": {
        "text": (_STR, True), "cited_ids": (_list(_STR), True), "uncertainty": (_list(_STR), True),
        "follow_up": (_STR, False), "actions": (_list(_DICT), True), "needs_clarification": (_BOOL, True),
    },
}

ENUMS = {
    ("conversation_request", "language"): {"tr", "en"},
    ("implementation_report", "claimed_status"): {"ready_for_verification", "blocked", "failed"},
    ("review_findings", "verdict"): {"approve", "correction_required", "blocked"},
    ("next_step_proposal", "kind"): {"complete", "revise_prompt", "escalate"},
}

MAX_STRING = 20_000
MAX_LIST = 500


def _check(kind: str, name: str, value, expected) -> None:
    if isinstance(expected, tuple) and expected and expected[0] == "list":
        if not isinstance(value, list):
            raise ContractError(f"{kind}.{name} must be a list")
        if len(value) > MAX_LIST:
            raise ContractError(f"{kind}.{name} has more than {MAX_LIST} items")
        for i, item in enumerate(value):
            _check(kind, f"{name}[{i}]", item, expected[1])
        return
    if expected is _BOOL:
        if not isinstance(value, bool):
            raise ContractError(f"{kind}.{name} must be a boolean")
        return
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ContractError(f"{kind}.{name} has the wrong type")
    if isinstance(value, str) and len(value) > MAX_STRING:
        raise ContractError(f"{kind}.{name} is longer than {MAX_STRING} characters")


def validate(kind: str, payload: object) -> dict:
    """Return a validated copy of `payload` for contract `kind`; raise ContractError otherwise."""
    if kind not in CONTRACTS:
        raise ContractError(f"Unknown contract kind: {kind}")
    if not isinstance(payload, dict):
        raise ContractError(f"{kind} must be a JSON object")
    spec = CONTRACTS[kind]
    unknown = set(payload) - set(spec) - {"schema_version", "contract"}
    if unknown:
        raise ContractError(f"{kind} has unknown fields: {', '.join(sorted(unknown))}")
    if payload.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ContractError(f"{kind}: unsupported schema_version {payload.get('schema_version')!r}")
    if payload.get("contract", kind) != kind:
        raise ContractError(f"payload contract {payload.get('contract')!r} does not match {kind}")
    out: dict = {"contract": kind, "schema_version": SCHEMA_VERSION}
    for name, (expected, required) in spec.items():
        if name not in payload:
            if required:
                raise ContractError(f"{kind}.{name} is required")
            continue
        _check(kind, name, payload[name], expected)
        out[name] = payload[name]
    for (k, field), allowed in ENUMS.items():
        if k == kind and out.get(field) not in allowed:
            raise ContractError(f"{kind}.{field} must be one of {sorted(allowed)}")
    if kind == "review_findings":
        out["next_step"] = validate("next_step_proposal", out["next_step"])
        if out["next_step"].get("revised_prompt") is not None:
            out["next_step"]["revised_prompt"] = validate("implementation_prompt", out["next_step"]["revised_prompt"])
    return out


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


# ---- file-scope globs --------------------------------------------------------------------

def glob_to_regex(pattern: str) -> re.Pattern:
    """`**` matches across directories, `*` within one segment. Patterns are repo-relative."""
    pattern = pattern.strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
        raise ZenithError(f"Invalid scope pattern: {pattern!r}")
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


def path_allowed(path: str, allowed: list[str], excluded: list[str]) -> bool:
    """A path must match at least one allowed glob and no excluded glob. Empty allow = nothing."""
    if any(glob_to_regex(x).match(path) for x in excluded):
        return False
    return any(glob_to_regex(x).match(path) for x in allowed)


def json_schema(kind: str) -> dict:
    """JSON Schema for a contract (what a provider is asked to produce). Strict: no extra keys."""
    if kind not in CONTRACTS:
        raise ContractError(f"Unknown contract kind: {kind}")

    def typ(expected) -> dict:
        if isinstance(expected, tuple) and expected and expected[0] == "list":
            return {"type": "array", "items": typ(expected[1])}
        if expected is _BOOL:
            return {"type": "boolean"}
        if expected is _STR:
            return {"type": "string"}
        if expected is _INT:
            return {"type": "integer"}
        if expected is _NUM:
            return {"type": "number"}
        if expected is _DICT:
            return {"type": "object"}
        return {}
    props = {name: typ(exp) for name, (exp, _) in CONTRACTS[kind].items()}
    for (k, field), allowed in ENUMS.items():
        if k == kind:
            props[field] = {"type": "string", "enum": sorted(allowed)}
    if kind == "review_findings":
        props["next_step"] = json_schema("next_step_proposal")
    if kind == "next_step_proposal":
        props["revised_prompt"] = json_schema("implementation_prompt")
    return {"type": "object", "properties": props,
            "required": [n for n, (_, req) in CONTRACTS[kind].items() if req], "additionalProperties": False}
