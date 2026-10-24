"""Native Claude Code integration: the read-only Zenith MCP server and the `zenith misa --claude` launcher.

Everything here runs offline against a fake `claude` binary (ZENITH_CLAUDE_BIN). No model is called;
the real session is demonstrated only with the owner's explicit permission.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zenith.kernel import Store, ZenithError
from zenith.misa import acceptance, native
from zenith.misa.mcp_server import NOTE_TOOL, READ_TOOL_NAMES, ZenithTools, handle
from zenith.misa.profile import PROFILE_TEXT, PROFILE_VERSION, profile_sha256
from zenith.misa.repl import MisaSession

SRC = str(Path(__file__).resolve().parents[1] / "src")

FAKE_CLAUDE = """#!/usr/bin/env bash
# Fake Claude Code CLI for tests: answers `auth status` from FAKE_AUTH and records any launch.
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '%s\\n' "${FAKE_AUTH:-{\\"loggedIn\\":true,\\"authMethod\\":\\"claude.ai\\",\\"apiProvider\\":\\"firstParty\\",\\"subscriptionType\\":\\"team\\"}}"
  exit 0
fi
{
  printf 'CWD\\t%s\\n' "$PWD"
  for a in "$@"; do printf 'ARG\\t%s\\n' "$(printf '%s' "$a" | base64 -w0)"; done
  printf 'ENV_ANTHROPIC_API_KEY\\t%s\\n' "${ANTHROPIC_API_KEY-<unset>}"
  printf 'ENV_CLAUDE_CODE_OAUTH_TOKEN\\t%s\\n' "${CLAUDE_CODE_OAUTH_TOKEN-<unset>}"
  printf 'ENV_AUTO_MEMORY\\t%s\\n' "${CLAUDE_CODE_DISABLE_AUTO_MEMORY-<unset>}"
  printf 'ENV_PROFILE\\t%s\\n' "${ZENITH_MISA_PROFILE-<unset>}"
} > "$FAKE_RECORD"
exit 0
"""


class WorldBase(unittest.TestCase):
    """Synthetic world: Harbor (cloud-shared) and Lighthouse (NOT shared) plus two earlier Misa turns."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.world = acceptance.build_world(self.root / "world")
        self.store = Store(self.world["db"])
        self.store.set_project_settings(self.world["harbor"], cloud_consent=True)
        s = MisaSession(self.store, out=lambda _t: None)
        s.handle("Lighthouse ne durumda?")
        s.handle("Harbor'da nerede kalmıştık?")
        self.env = {**os.environ, "ZENITH_DB_PATH": self.world["db"], "PYTHONPATH": SRC,
                    "ZENITH_MCP_AUDIT_FILE": str(self.root / "audit.jsonl"), "ZENITH_MISA_SESSION": "sess-1"}
        self.env.pop("ZENITH_MCP_ALLOW_NOTES", None)

    def tearDown(self):
        self.temp.cleanup()

    def rpc(self, messages: list[dict], env: dict | None = None) -> list[dict]:
        inp = "".join(json.dumps(m) + "\n" for m in messages)
        r = subprocess.run([sys.executable, "-I", "-m", "zenith.misa.mcp_server"], input=inp, capture_output=True, text=True,
                           env=env or self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]

    @staticmethod
    def call(rid, name, **args):
        return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}}

    @staticmethod
    def payload(resp: dict) -> dict:
        return json.loads(resp["result"]["content"][0]["text"])


class McpProtocolTests(WorldBase):
    def test_handshake_tool_list_and_unknown_method(self):
        out = self.rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        ])
        self.assertEqual([o["id"] for o in out], [1, 2, 3, 4])            # the notification got no reply
        init = out[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "zenith")
        self.assertIn("tools", init["capabilities"])
        tools = out[2]["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], list(READ_TOOL_NAMES))
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))
        self.assertTrue(all(t["inputSchema"]["additionalProperties"] is False for t in tools))
        self.assertEqual(out[3]["error"]["code"], -32601)

    def test_parse_error_and_invalid_request_do_not_kill_the_server(self):
        r = subprocess.run([sys.executable, "-I", "-m", "zenith.misa.mcp_server"], input='not json\n[1,2]\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n',
                           capture_output=True, text=True, env=self.env, timeout=60)
        lines = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual([l.get("error", {}).get("code") for l in lines[:2]], [-32700, -32600])
        self.assertEqual(lines[2]["result"], {})

    def test_unsupported_protocol_version_falls_back_to_newest_supported(self):
        out = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}])
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-06-18")


class ConsentBoundaryTests(WorldBase):
    def test_only_shared_projects_are_ever_named(self):
        out = self.rpc([
            self.call(1, "list_projects"),
            self.call(2, "project_summary", project="Harbor", focus="last"),
            self.call(3, "project_summary", project="Lighthouse"),
            self.call(4, "tasks"),
            self.call(5, "decisions", project="harbor"),
            self.call(6, "recent_evidence", project="Harbor", limit=5),
            self.call(7, "verification_status", project="Harbor"),
            self.call(8, "previous_conversations", query="nerede kalmıştık", project="Harbor"),
            self.call(9, "previous_conversations", query="Lighthouse ne durumda"),
            self.call(10, "tasks", project=self.world["lighthouse"]),
        ])
        blob = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("Lighthouse", blob)
        self.assertNotIn("lighthouse", blob.lower().replace("lighthouse ne durumda", ""))  # only our own query text could echo it
        self.assertNotIn(self.world["lighthouse"], blob)
        listing = self.payload(out[0])
        self.assertEqual([p["name"] for p in listing["projects"]], ["Harbor"])
        self.assertEqual(listing["withheld_count"], 1)
        self.assertIn("--cloud-consent yes", listing["withheld_note"])
        summary = self.payload(out[1])
        self.assertEqual(summary["project"]["name"], "Harbor")
        self.assertTrue(summary["items"])
        self.assertTrue({"id", "kind", "epistemic", "when", "age_days"} <= set(summary["items"][0]))
        self.assertEqual(summary["provenance"]["source"], "zenith")
        self.assertTrue(all(not i.get("local_only") for i in summary["items"]))
        self.assertTrue(out[2]["result"]["isError"])
        self.assertIn("never named", self.payload(out[2])["error"])
        self.assertTrue(all(t["project"] == "Harbor" for t in self.payload(out[3])["tasks"]))
        self.assertTrue(self.payload(out[4])["decisions"])
        self.assertEqual(self.payload(out[4])["decisions"][0]["epistemic"], "decided")
        self.assertTrue(all(e["epistemic"] in ("verified", "observed", "reported", "inferred") for e in self.payload(out[5])["evidence"]))
        v = self.payload(out[6])
        self.assertIsNotNone(v["latest_verification"])
        self.assertIn("applies", v["latest_verification"])
        self.assertIn("applicability", v["latest_verification"])
        self.assertTrue(any(s["state"] != "fresh" for s in v["sources"]))      # the removed 'notes' source is reported
        turns = self.payload(out[7])["turns"]
        self.assertTrue(turns)
        self.assertTrue(all("Lighthouse" not in t["text"] for t in turns))
        self.assertEqual(self.payload(out[8])["turns"], [])                   # the Lighthouse turn is withheld entirely
        self.assertTrue(out[9]["result"]["isError"])

    def test_results_are_marked_as_data_and_audited(self):
        self.rpc([self.call(1, "project_summary", project="Harbor")])
        lines = [json.loads(l) for l in (self.root / "audit.jsonl").read_text().splitlines()]
        self.assertEqual((lines[0]["tool"], lines[0]["session"], lines[0]["projects"]), ("project_summary", "sess-1", ["Harbor"]))
        self.assertIn("untrusted", self.payload(self.rpc([self.call(1, "list_projects")])[0])["provenance"]["note"])


class NoteToolTests(WorldBase):
    def test_notes_absent_by_default_and_prompted_write_when_enabled(self):
        out = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.call(2, "record_note", project="Harbor", text="x")])
        self.assertNotIn(NOTE_TOOL["name"], [t["name"] for t in out[0]["result"]["tools"]])
        self.assertTrue(out[1]["result"]["isError"])
        before = len(self.store.list_evidence(self.world["harbor"]))
        env = {**self.env, "ZENITH_MCP_ALLOW_NOTES": "1"}
        out = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        self.call(2, "record_note", project="Harbor", text="Deploy planı pazartesiye kaldı"),
                        self.call(3, "record_note", project="Lighthouse", text="leak?")], env=env)
        tools = {t["name"]: t for t in out[0]["result"]["tools"]}
        self.assertFalse(tools["record_note"]["annotations"]["readOnlyHint"])
        rec = self.payload(out[1])
        self.assertEqual((rec["recorded"], rec["origin"], rec["epistemic"]), (True, "HUMAN_REPORT", "reported"))
        ev = self.store.list_evidence(self.world["harbor"])
        self.assertEqual(len(ev), before + 1)
        self.assertEqual((ev[0]["origin"], ev[0]["confidence"], ev[0]["summary"]), ("HUMAN_REPORT", "REPORTED", "Deploy planı pazartesiye kaldı"))
        self.assertTrue(out[2]["result"]["isError"])                          # unshared project: no write
        self.assertEqual(len(self.store.list_evidence(self.world["lighthouse"])), 0 or len(self.store.list_evidence(self.world["lighthouse"])))

    def test_in_process_handler_rejects_bad_arguments(self):
        tools = ZenithTools(self.store)
        resp = handle(tools, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "tasks", "arguments": {"bogus": 1}}})
        self.assertTrue(resp["result"]["isError"])
        resp = handle(tools, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(resp["result"]["isError"])


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "z.db")
        self.store.init()
        self.fake = self.root / "claude"
        self.fake.write_text(FAKE_CLAUDE)
        self.fake.chmod(0o700)
        self.record = self.root / "record.tsv"
        self.env = {"ZENITH_CLAUDE_BIN": str(self.fake), "ZENITH_MISA_CLAUDE_DIR": str(self.root / "misa"), "FAKE_RECORD": str(self.record),
                    "ANTHROPIC_API_KEY": "sk-ant-test-not-real", "CLAUDE_CODE_OAUTH_TOKEN": "tok-not-real", "ANTHROPIC_MODEL": "x"}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def recorded(self) -> dict:
        rows = [l.split("\t", 1) for l in self.record.read_text().splitlines()]
        args = [base64.b64decode(v).decode() for k, v in rows if k == "ARG"]
        return {"cwd": next(v for k, v in rows if k == "CWD"), "args": args, **{k: v for k, v in rows if k.startswith("ENV_")}}

    def test_plan_is_complete_and_starts_nothing(self):
        out = []
        rc = native.launch(self.store, plan_only=True, out=out.append)
        self.assertEqual(rc, 0)
        plan = json.loads(out[0])
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["auth"]["authMethod"], "claude.ai")
        self.assertEqual(plan["profile"], {"version": PROFILE_VERSION, "sha256": profile_sha256()})
        self.assertNotIn(PROFILE_TEXT, out[0])                               # the plan shows a placeholder, not 3 KB of prompt
        self.assertEqual(sorted(plan["stripped_env"]), ["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertFalse(self.record.exists())
        self.assertFalse((self.root / "misa" / "sessions.json").exists())
        self.assertIn("no project is cloud-shared", plan["warnings"][0])

    def test_launch_uses_only_supported_restrictions_and_native_login(self):
        rc = native.launch(self.store, out=lambda *a, **k: None)
        self.assertEqual(rc, 0)
        rec = self.recorded()
        args = rec["args"]
        self.assertEqual(rec["cwd"], str(self.root / "misa" / "workspace"))
        for flag in ("--restricted", "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", "--session-id", "--name"):
            self.assertIn(flag, args, flag)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--system-prompt") + 1], PROFILE_TEXT)
        self.assertEqual(args[args.index("--system-prompt-snapshot") + 1], "off")
        i = args.index("--allowedTools")
        self.assertEqual(args[i + 1:i + 1 + len(READ_TOOL_NAMES)], [f"mcp__zenith__{t}" for t in READ_TOOL_NAMES])
        self.assertNotIn("mcp__zenith__record_note", args)
        j = args.index("--disallowedTools")
        self.assertTrue({"Bash", "Edit", "Write", "WebFetch"} <= set(args[j + 1:j + 1 + len(native.DENIED_TOOLS)]))
        for forbidden in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--bare", "--add-dir", "-p", "--print"):
            self.assertNotIn(forbidden, args, forbidden)
        self.assertNotIn("bypassPermissions", args)
        self.assertEqual((rec["ENV_ANTHROPIC_API_KEY"], rec["ENV_CLAUDE_CODE_OAUTH_TOKEN"]), ("<unset>", "<unset>"))
        self.assertEqual(rec["ENV_AUTO_MEMORY"], "1")
        self.assertTrue(rec["ENV_PROFILE"].startswith(f"{PROFILE_VERSION}:"))
        cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
        srv = cfg["mcpServers"]["zenith"]
        self.assertEqual((srv["type"], srv["command"], srv["args"]), ("stdio", sys.executable, ["-I", "-m", "zenith.misa.mcp_server"]))
        self.assertEqual(srv["env"]["ZENITH_DB_PATH"], str(self.store.db_path))
        self.assertNotIn("ZENITH_MCP_ALLOW_NOTES", srv["env"])
        self.assertEqual(oct(Path(args[args.index("--mcp-config") + 1]).stat().st_mode & 0o777), "0o600")
        reg = json.loads((self.root / "misa" / "sessions.json").read_text())
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg[0]["session_id"], args[args.index("--session-id") + 1])
        self.assertEqual(reg[0]["auth"], {"authMethod": "claude.ai", "subscriptionType": "team"})

    def test_resume_continues_the_registered_session_with_the_same_restrictions(self):
        native.launch(self.store, out=lambda *a, **k: None)
        first = self.recorded()["args"]
        sid = first[first.index("--session-id") + 1]
        native.launch(self.store, resume=True, allow_notes=True, out=lambda *a, **k: None)
        args = self.recorded()["args"]
        self.assertEqual(args[args.index("--resume") + 1], sid)
        self.assertNotIn("--session-id", args)
        self.assertIn("--restricted", args)
        self.assertIn("--mcp-config", args)                                   # re-passed: not restored by --resume
        cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
        self.assertEqual(cfg["mcpServers"]["zenith"]["env"]["ZENITH_MCP_ALLOW_NOTES"], "1")
        self.assertNotIn("mcp__zenith__record_note", args)                    # write tool is never pre-allowed → native prompt
        reg = json.loads((self.root / "misa" / "sessions.json").read_text())
        self.assertEqual([e["resumed"] for e in reg], [False, True])

    def test_logged_out_or_api_key_auth_refuses_without_mocking(self):
        for auth in ('{"loggedIn": false, "authMethod": "none"}', '{"loggedIn": true, "authMethod": "api_key", "apiProvider": "firstParty"}',
                     '{"loggedIn": true, "authMethod": "oauth_token", "apiProvider": "firstParty"}'):
            with mock.patch.dict(os.environ, {"FAKE_AUTH": auth}):
                with self.assertRaises(ZenithError) as cm:
                    native.launch(self.store, out=lambda *a, **k: None)
                self.assertIn("offline `zenith` Misa still works", str(cm.exception))
                self.assertFalse(self.record.exists())

    def test_missing_cli_is_a_clear_error_and_offline_misa_still_answers(self):
        with mock.patch.dict(os.environ, {"ZENITH_CLAUDE_BIN": str(self.root / "nope")}):
            with self.assertRaisesRegex(ZenithError, "claude CLI not found"):
                native.launch(self.store, out=lambda *a, **k: None)
        out = []
        MisaSession(self.store, out=out.append).handle("yardım")
        self.assertTrue(out)

    def test_cli_plan_flag(self):
        env = {**os.environ, "ZENITH_DB_PATH": str(self.store.db_path), "PYTHONPATH": SRC}
        r = subprocess.run([sys.executable, "-m", "zenith", "misa", "--claude", "--plan"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertEqual(plan["session_id"].count("-"), 4)
        self.assertTrue(plan["data_boundary"].startswith("Only cloud-shared projects (cloud_consent=yes)"))
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
