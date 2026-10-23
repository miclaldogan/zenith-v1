"""Sprint 2.6: auth modes, command-free director, context minimization, proxy hardening, smoke gating."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from zenith import sandbox
from zenith.agents.base import AdapterError, RunContext
from zenith.agents.fake import FakeDirector, FakeImplementer
from zenith.agents.providers import ClaudeCodeDirector, ClaudeCodeImplementer, CodexDirector
from zenith.demo import FIXED, make_repo
from zenith.kernel import Store, ZenithError
from zenith.netproxy import AllowlistProxy, is_private_address
from zenith.orchestration import Orchestrator

SRC = str(Path(__file__).resolve().parents[1] / "src")
CAPS = sandbox.detect()
needs_sandbox = unittest.skipUnless(CAPS.ok, "bubblewrap isolation baseline unavailable: " + "; ".join(CAPS.reasons))
SCOPE = {"allowed_files": ["src/**"], "excluded_files": ["tests/**"],
         "acceptance": [{"id": "unit", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}]}
PLAN_OUT = {"architecture_proposal": {"summary": "s", "decisions": [], "risks": [], "decomposition": []},
            "implementation_prompt": {"objective": "o", "instructions": ["i"], "architectural_constraints": [],
                                      "acceptance_criteria": [], "test_expectations": [], "output_requirements": []}}
INIT = {"type": "system", "subtype": "init", "session_id": "sess-1"}


def lines(*events):
    return "\n".join("echo " + json.dumps(json.dumps(e)) for e in events) + "\n"


def result_event(structured, cost=0.01):
    return {"type": "result", "subtype": "success", "session_id": "sess-1", "total_cost_usd": cost, "is_error": False,
            "structured_output": structured}


def echo_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            c.sendall(b"ECHO:" + c.recv(1024))
            c.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


def connect_via(sock_path, target):
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic c2VjcmV0\r\n\r\n".encode())
    head = s.recv(1024).split(b"\r\n")[0]
    if b"200" in head:
        s.sendall(b"ping")
        return head + b"|" + s.recv(1024)
    return head


class ProxyHardeningTests(unittest.TestCase):
    def test_private_and_loopback_destinations_are_blocked_by_default(self):
        for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.9", "172.16.5.5", "169.254.169.254", "::1", "fe80::1", "fd00::1", "0.0.0.0"):
            self.assertTrue(is_private_address(ip), ip)
        for ip in ("8.8.8.8", "2606:4700::1111"):
            self.assertFalse(is_private_address(ip), ip)
        srv, port = echo_server()
        with tempfile.TemporaryDirectory() as tmp:
            audit = []
            with AllowlistProxy(Path(tmp) / "p.sock", {f"127.0.0.1:{port}", f"localhost:{port}"},
                                audit=lambda k, p: audit.append((k, p))) as proxy:
                self.assertEqual(connect_via(proxy.socket_path, f"127.0.0.1:{port}"), b"HTTP/1.1 403 Forbidden")
                self.assertEqual(connect_via(proxy.socket_path, f"localhost:{port}"), b"HTTP/1.1 403 Forbidden")
            self.assertEqual([k for k, _ in audit], ["network.private_blocked", "network.private_blocked"])
            with AllowlistProxy(Path(tmp) / "p.sock", {f"127.0.0.1:{port}"}, allow_private=True) as proxy:
                self.assertTrue(connect_via(proxy.socket_path, f"127.0.0.1:{port}").endswith(b"ECHO:ping"))
                self.assertEqual(proxy.connections[-1]["address"], "127.0.0.1")
        srv.close()

    def test_ipv6_literal_connection_failure_and_log_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = []
            try:
                v6 = socket.socket(socket.AF_INET6)
                v6.bind(("::1", 0))
                v6.listen(1)
                v6port = v6.getsockname()[1]
                v6.close()  # closed port: connection must fail with 502, not hang
                has_v6 = True
            except OSError:
                has_v6, v6port = False, 1
            with AllowlistProxy(Path(tmp) / "p.sock", {f"[::1]:{v6port}"}, audit=lambda k, p: audit.append((k, p)),
                                allow_private=True, connect_timeout=2) as proxy:
                if has_v6:
                    self.assertEqual(connect_via(proxy.socket_path, f"[::1]:{v6port}"), b"HTTP/1.1 502 Bad Gateway")
                s = socket.socket(socket.AF_UNIX)
                s.connect(str(proxy.socket_path))
                s.sendall(b"GET http://x/ HTTP/1.1\r\nProxy-Authorization: Basic c2VjcmV0\r\n\r\n")
                self.assertIn(b"405", s.recv(1024))
            blob = json.dumps(audit)
            self.assertNotIn("c2VjcmV0", blob)       # the header value never reaches the audit log
            self.assertNotIn("Proxy-Authorization", blob)
            self.assertIn('"method": "GET"', blob)
            with self.assertRaises(ValueError):
                AllowlistProxy(Path(tmp) / "q.sock", {"api.example.com"})  # port required
            with self.assertRaises(ValueError):
                AllowlistProxy(Path(tmp) / "q.sock", {"api.example.com:443; rm -rf /"})

    def test_restart_reuses_socket_path_and_stop_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.sock"
            p1 = AllowlistProxy(path, {"api.example.com:443"}).start()
            p1.stop()
            self.assertFalse(path.exists())
            p2 = AllowlistProxy(path, {"api.example.com:443"}).start()
            self.assertTrue(path.exists())
            p2.stop()
            with self.assertRaises(OSError):
                socket.socket(socket.AF_UNIX).connect(str(path))


@needs_sandbox
class CredentialInjectionTests(unittest.TestCase):
    def test_env_file_is_read_inside_the_sandbox_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            work = root / "work"
            work.mkdir()
            cred = root / "tok"
            cred.write_text("tok-secret-value\n")
            cred.chmod(0o600)
            with AllowlistProxy(root / "p.sock", {"api.example.com:443"}) as proxy:
                profile = sandbox.SandboxProfile("provider", proxy_socket=proxy.socket_path,
                                                 ro_binds=[(cred, "/run/zenith/credentials/claude_credential")],
                                                 env_files={"CLAUDE_CODE_OAUTH_TOKEN": "/run/zenith/credentials/claude_credential"})
                res = sandbox.run_isolated(["python", "-c", "import os; print('TOK', os.environ['CLAUDE_CODE_OAUTH_TOKEN'] == 'tok-secret-value', "
                                            "sorted(os.listdir(os.environ['HOME'])))"], work, timeout=20, caps=CAPS, profile=profile)
                self.assertIn("TOK True []", res.output_tail)   # token present for the CLI; HOME is empty (no config to load)
                missing = sandbox.SandboxProfile("provider", proxy_socket=proxy.socket_path,
                                                 env_files={"X": "/run/zenith/credentials/nope"})
                res = sandbox.run_isolated(["true"], work, timeout=20, caps=CAPS, profile=missing)
                self.assertEqual(res.exit_code, 78)   # missing credential file: refuse to start the CLI
            with self.assertRaises(ZenithError):
                sandbox.SandboxProfile("provider", env_files={"X": "/x"}).validate()   # no forwarder without proxy
            with self.assertRaises(ZenithError):
                sandbox.SandboxProfile("provider", proxy_socket=proxy.socket_path, env_files={"PATH": "/x"}).validate()


@needs_sandbox
class AuthModeAndDirectorIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        make_repo(self.repo)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.cred = self.root / "cred"
        self.cred.write_text("x\n")
        self.cred.chmod(0o600)
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def mock(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/bash\n" + body)
        p.chmod(0o755)
        return str(p)

    def ctx(self, stage="director_plan", **kw):
        return RunContext(run_id="r", stage=stage, deadline_seconds=20, emit=lambda k, p: self.events.append((k, p)), **kw)

    def test_claude_auth_modes_select_bare_or_safe_mode(self):
        api = ClaudeCodeImplementer({"bin": "claude", "api_key_file": str(self.cred), "caps": CAPS})
        oauth = ClaudeCodeImplementer({"bin": "claude", "oauth_token_file": str(self.cred), "caps": CAPS})
        none = ClaudeCodeImplementer({"bin": "claude", "caps": CAPS})
        self.assertEqual((api.auth_mode(), oauth.auth_mode(), none.auth_mode()), ("api_key", "oauth", None))
        a, o = " ".join(api.example_argv()), " ".join(oauth.example_argv())
        self.assertIn("--bare", a); self.assertNotIn("--safe-mode", a)
        self.assertIn("--safe-mode", o); self.assertNotIn("--bare", o)
        for argv in (a, o):
            self.assertIn("--restricted", argv); self.assertIn("--permission-prompts none", argv)
            self.assertIn("--disable-slash-commands", argv); self.assertNotIn("apiKeyHelper", argv)
            self.assertNotIn("--settings", argv)
        self.assertEqual(api._env_files(), {"ANTHROPIC_API_KEY": "/run/zenith/credentials/claude_credential"})
        self.assertEqual(oauth._env_files(), {"CLAUDE_CODE_OAUTH_TOKEN": "/run/zenith/credentials/claude_credential"})
        self.assertFalse(none.check_auth().ok)
        self.assertIn("unverified", oauth.check_auth().detail)
        self.assertEqual(oauth.preflight()["authenticated_live"], "unverified")

    def test_oauth_token_reaches_claude_and_no_config_exists_in_home(self):
        # The mock reports what the CLI would see, through the structured result (events are summarized).
        probe = ('home=$(ls -A "$HOME" | wc -l | tr -d " ") token=$([ "$CLAUDE_CODE_OAUTH_TOKEN" = x ] && echo yes || echo no) '
                 'api=$([ -n "$ANTHROPIC_API_KEY" ] && echo yes || echo no) safe=$(printf "%s " "$@" | grep -c -- --safe-mode)')
        plan = json.dumps(PLAN_OUT).replace('"summary": "s"', '"summary": "PROBE"')
        body = ('[ "$1" = "--version" ] && { echo mock; exit 0; }\n'
                f'probe="{probe}"\n'
                "plan='" + plan + "'\n"
                'result="{\\"type\\":\\"result\\",\\"subtype\\":\\"success\\",\\"is_error\\":false,\\"total_cost_usd\\":0.01,\\"structured_output\\":${plan/PROBE/$probe}}"\n'
                'echo "$result"\n')
        a = ClaudeCodeDirector({"bin": self.mock("claude", body), "oauth_token_file": str(self.cred), "caps": CAPS,
                                "allowed_hosts": ["127.0.0.1:1"]})
        out = a.plan(self.ctx(read_root=self.repo), {"task": {}})
        self.assertEqual(out["architecture_proposal"]["summary"], "home=0 token=yes api=no safe=1")
        start = next(p for k, p in self.events if k == "provider.start")
        self.assertEqual((start["auth_mode"], start["credential_env"]), ("oauth", ["CLAUDE_CODE_OAUTH_TOKEN"]))
        self.assertNotIn("x\n", json.dumps(self.events))   # the token value never appears in Zenith's records

    def test_codex_director_is_command_free_and_command_items_are_violations(self):
        argv = " ".join(CodexDirector({"bin": "codex", "auth_file": str(self.cred), "caps": CAPS}).example_argv())
        self.assertIn("--disable shell_tool", argv)
        self.assertIn("--disable unified_exec", argv)
        body = ('[ "$1" = "--version" ] && { echo mock; exit 0; }\n'
                "echo '{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\",\"command\":\"cat auth.json\"}}'\n"
                "sleep 20\n")
        a = CodexDirector({"bin": self.mock("codex", body), "auth_file": str(self.cred), "caps": CAPS, "allowed_hosts": ["127.0.0.1:1"]})
        with self.assertRaises(AdapterError) as cm:
            a.plan(self.ctx(read_root=self.repo), {"task": {}})
        self.assertEqual(cm.exception.kind, "policy_violation")

    def test_director_sees_a_context_dir_not_the_repository(self):
        store = Store(self.root / "z.db")
        store.init()
        (self.repo / ".env").write_text("SECRET=1")
        (self.repo / "notes.md").write_text("design notes")
        seen = {}

        class SpyDirector(FakeDirector):
            def plan(self, ctx, context_package):
                seen["root"] = ctx.read_root
                seen["entries"] = sorted(p.relative_to(ctx.read_root).as_posix() for p in ctx.read_root.rglob("*"))
                seen["listing"] = (ctx.read_root / "listing.txt").read_text()
                return super().plan(ctx, context_package)
        orch = Orchestrator(store, SpyDirector(), FakeImplementer(), caps=CAPS, state_root=self.root / "state")
        project = store.add_project("P", str(self.repo))
        task = store.add_task(project, "T", "", risk="L2", scope={**SCOPE, "context_files": ["src/**", "*.md"]})
        orch.plan(task)
        self.assertNotEqual(seen["root"], self.repo)
        self.assertIn("context.json", seen["entries"])
        self.assertIn("files/src/calc.py", seen["entries"])
        self.assertIn("files/notes.md", seen["entries"])
        self.assertNotIn("files/tests/test_calc.py", seen["entries"])   # not selected by the owner
        self.assertNotIn(".env", seen["listing"])                         # name never leaves the machine
        self.assertIn("src/calc.py\t", seen["listing"])
        self.assertFalse(seen["root"].exists())                           # cleaned up after the stage
        self.assertIn("task.director_context", [e["event_type"] for e in store.events(20)])

    def test_worktree_with_credential_like_files_is_refused_before_implementation(self):
        store = Store(self.root / "z.db")
        store.init()
        (self.repo / ".env").write_text("SECRET=1")
        subprocess.run(["git", "-c", "user.email=t@e.invalid", "-c", "user.name=t", "add", "-f", ".env"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@e.invalid", "-c", "user.name=t", "commit", "-q", "-m", "oops"], cwd=self.repo, check=True)
        impl = FakeImplementer({"implement": {"writes": {"src/calc.py": FIXED}}})
        orch = Orchestrator(store, FakeDirector(), impl, caps=CAPS, state_root=self.root / "state")
        project = store.add_project("P", str(self.repo))
        task = store.add_task(project, "T", "", risk="L2", scope=SCOPE)
        orch.plan(task)
        store.transition(task, "AWAITING_APPROVAL"); store.approve(task); store.transition(task, "QUEUED")
        res = orch.run_once(orch.create_run(task)["run_id"])
        self.assertEqual(res["state"], "BLOCKED")
        self.assertIn("credential-like", res["exit_reason"])
        self.assertEqual(impl.calls, [])   # the implementer was never invoked


class SmokeGatingTests(unittest.TestCase):
    def test_full_smoke_preflight_returns_authorization_request_without_calling(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            b = tmp / "bin"
            b.mkdir()
            for name in ("claude", "codex"):
                (b / name).write_text("#!/bin/bash\necho SHOULD-NEVER-RUN >&2; exit 99\n")
                (b / name).chmod(0o755)
            env = {**os.environ, "ZENITH_DB_PATH": str(tmp / "z.db"), "PYTHONPATH": SRC, "ZENITH_CLAUDE_BIN": str(b / "claude"),
                   "ZENITH_CODEX_BIN": str(b / "codex"), "ZENITH_ENABLED_ADAPTERS": "codex-director,claude-code-implementer"}
            for k in ("ZENITH_ANTHROPIC_API_KEY_FILE", "ZENITH_CLAUDE_OAUTH_TOKEN_FILE", "ZENITH_CODEX_AUTH_FILE", "ZENITH_LIVE_SMOKE_ACK"):
                env.pop(k, None)

            def z(*args, ok=True):
                r = subprocess.run([sys.executable, "-m", "zenith", *args], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode == 0, ok, r.stderr + r.stdout)
                return r
            z("init")
            out = json.loads(z("smoke", "--adapter", "codex-director", "--full").stdout)
            self.assertFalse(out["live_call_performed"])
            req = out["authorization_request"]
            self.assertEqual([p["role"] for p in req["providers"]], ["director", "implementer"])
            self.assertEqual(req["runtime_caps"]["max_attempts"], 1)
            self.assertIn("synthetic", req["files_accessible_to_agents"])
            self.assertTrue(any("credential" in bl.lower() or "configured" in bl.lower() for bl in out["blockers"]))
            r = z("smoke", "--adapter", "codex-director", "--full", "--authorize-live-call", ok=False)
            self.assertIn("ZENITH_LIVE_SMOKE_ACK", r.stderr)
            self.assertNotIn("SHOULD-NEVER-RUN", r.stderr + r.stdout)
            env["ZENITH_LIVE_SMOKE_ACK"] = "I-AUTHORIZE-ONE-LIVE-PROVIDER-CALL"
            r = z("smoke", "--adapter", "codex-director", "--full", "--authorize-live-call", ok=False)
            self.assertIn("Live call refused", r.stderr)       # blockers (no credentials) still stop it
            self.assertNotIn("SHOULD-NEVER-RUN", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
