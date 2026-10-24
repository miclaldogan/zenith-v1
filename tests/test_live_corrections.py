"""Live-integration corrections: HTTPS through CONNECT with verified TLS, model-aware request body,
fail-closed project-scoped cloud sharing (exact serialized provider request)."""
import http.server
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from zenith import sandbox
from zenith.agents.base import AdapterError, AgentCapabilities, AuthStatus, RunContext
from zenith.demo import make_repo
from zenith.kernel import Store
from zenith.misa import api_client
from zenith.misa.conversation import AnthropicApiConversation
from zenith.misa.repl import MisaSession

CAPS = sandbox.detect()
needs_sandbox = unittest.skipUnless(CAPS.ok, "bubblewrap isolation baseline unavailable")
needs_openssl = unittest.skipUnless(shutil.which("openssl"), "openssl not installed")


class RequestSchemaTests(unittest.TestCase):
    def test_request_body_matches_api_version_2026_10_21(self):
        """Guard: temperature/top_p/top_k are removed in this API version (400 on every model)."""
        for m in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1", "claude-sonnet-4-6"):
            body = api_client.build_messages_request({"model": m, "system": "s", "user": "u", "max_tokens": 500})
            self.assertEqual(set(body), {"model", "max_tokens", "system", "messages"}, m)
            for forbidden in api_client.REMOVED_PARAMS:
                self.assertNotIn(forbidden, body)
            self.assertEqual(body["messages"], [{"role": "user", "content": "u"}])
            self.assertEqual(body["max_tokens"], 500)
            self.assertIsInstance(body["system"], str)
        self.assertEqual(api_client.API_VERSION, "2026-10-21")
        self.assertTrue(all(f in body for f in api_client.REQUIRED_FIELDS))


class Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append({"path": self.path, "key": self.headers.get("x-api-key"), "body": body,
                             "version": self.headers.get("anthropic-version")})
        self.send_response(Handler.status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        reply = {"content": [{"type": "text", "text": json.dumps({"text": "ok over tls", "cited_ids": [], "uncertainty": ["nothing_pending"],
                                                                   "actions": [], "needs_clarification": False})}],
                 "usage": {"input_tokens": 10, "output_tokens": 5}, "model": "m", "stop_reason": "end_turn", "id": "msg"}
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, *a):
        pass


@needs_sandbox
@needs_openssl
class HttpsThroughConnectTests(unittest.TestCase):
    """A local HTTPS server with a certificate from a freshly generated test CA; the sandboxed client
    must CONNECT through the allowlist proxy and then verify the chain and hostname inside the tunnel."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        ca_key, self.ca = self.root / "ca.key", self.root / "ca.pem"
        srv_key, srv_crt = self.root / "srv.key", self.root / "srv.pem"
        ext = self.root / "ext.cnf"; ext.write_text("subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n")
        run = lambda *a: subprocess.run(["openssl", *a], check=True, capture_output=True)
        run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key), "-out", str(self.ca), "-days", "2",
            "-subj", "/CN=Zenith Test CA", "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")     # a proper CA: OpenSSL 3.x rejects one without these
        run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(srv_key), "-out", str(self.root / "srv.csr"),
            "-subj", "/CN=localhost")
        run("x509", "-req", "-in", str(self.root / "srv.csr"), "-CA", str(self.ca), "-CAkey", str(ca_key), "-CAcreateserial",
            "-out", str(srv_crt), "-days", "2", "-extfile", str(ext))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(srv_crt), str(srv_key))
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]
        self.key = self.root / "key"; self.key.write_text("sk-ant-test-not-real\n"); self.key.chmod(0o600)
        self.ca.chmod(0o644)
        Handler.seen.clear(); Handler.status = 200
        self.events = []

    def tearDown(self):
        self.server.shutdown(); self.temp.cleanup()

    def adapter(self, base=None, **extra):
        return AnthropicApiConversation({"api_key_file": str(self.key), "base_url": base or f"https://localhost:{self.port}",
                                         "allowed_hosts": [f"localhost:{self.port}", f"127.0.0.1:{self.port}"], "allow_private_hosts": True,
                                         "caps": CAPS, "model": "claude-sonnet-5-5", **extra})

    def ctx(self):
        return RunContext(run_id="t", stage="conversation", deadline_seconds=40, read_root=self.root,
                          emit=lambda k, p: self.events.append((k, p)))

    def request(self):
        return {"language": "tr", "question": "x", "question_type": "status", "projects": [], "items": [], "recent_turns": [],
                "instructions": [], "allowed_actions": [], "limits": {}, "generated_at": "t"}

    def test_verified_tls_inside_the_connect_tunnel(self):
        out = self.adapter(ca_bundle=str(self.ca)).respond(self.ctx(), self.request())
        self.assertEqual(out["text"], "ok over tls")
        self.assertEqual(Handler.seen[0]["key"], "sk-ant-test-not-real")
        self.assertEqual(Handler.seen[0]["version"], "2026-10-21")
        self.assertEqual(set(Handler.seen[0]["body"]), {"model", "max_tokens", "system", "messages"})   # no temperature
        kinds = [k for k, _ in self.events]
        self.assertIn("network.allowed", kinds)
        allowed = next(p for k, p in self.events if k == "network.allowed")
        self.assertEqual(allowed["target"], f"localhost:{self.port}")                                 # CONNECT seen by the proxy
        self.assertNotIn("sk-ant-test", json.dumps(self.events))

    def test_untrusted_ca_and_hostname_mismatch_fail_closed(self):
        with self.assertRaises(AdapterError) as cm:                       # no test CA → chain not trusted
            self.adapter().respond(self.ctx(), self.request())
        self.assertEqual(cm.exception.kind, "connection")
        self.assertTrue(any("tls" in p.get("line", "").lower() or "certificate" in p.get("line", "").lower()
                            for k, p in self.events if k == "provider.stderr"))
        self.assertEqual(Handler.seen, [])                                 # the server never saw a request
        self.events.clear()
        with self.assertRaises(AdapterError) as cm:                       # cert is for localhost, URL says 127.0.0.1
            self.adapter(base=f"https://127.0.0.1:{self.port}", ca_bundle=str(self.ca)).respond(self.ctx(), self.request())
        self.assertEqual(cm.exception.kind, "connection")
        self.assertEqual(Handler.seen, [])


class Spy:
    name = "spy"

    def __init__(self):
        self.seen: list[str] = []

    def capabilities(self):
        return AgentCapabilities(live=True)

    def check_auth(self):
        return AuthStatus(True, "x")

    def respond(self, ctx, request):
        self.seen.append(json.dumps(request, ensure_ascii=False))
        return {"text": "model says ok", "cited_ids": [], "uncertainty": ["nothing_pending"], "actions": [], "needs_clarification": False}


class CloudConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "z.db"); self.store.init()
        pub = self.root / "public"; make_repo(pub)
        (pub / "README.md").write_text("# Public project readme\n")
        self.pub = self.store.add_project("PublicProj", str(pub))
        self.store.scan_sources(self.pub)
        self.store.set_project_settings(self.pub, docs=["README.md"], cloud_consent=True)
        self.store.add_task(self.pub, "Public task alpha", due_at="2020-01-01")
        self.store.checkin(self.pub, "public note bravo")
        priv = self.root / "private"; priv.mkdir()
        self.priv = self.store.add_project("SecretThesis", str(priv))
        self.store.add_task(self.priv, "Thesis chapter charlie", due_at="2020-01-02")
        self.store.checkin(self.priv, "thesis note delta about committee")
        self.store.add_decision(self.priv, "Use method echo", "because foxtrot", made_by="owner")
        self.spy = Spy()
        self.session = MisaSession(self.store, out=lambda _: None, provider=self.spy)

    def tearDown(self):
        self.temp.cleanup()

    SECRET_WORDS = ("SecretThesis", "charlie", "delta", "committee", "echo", "foxtrot")

    def assert_clean(self, payload: str):
        for w in self.SECRET_WORDS:
            self.assertNotIn(w, payload, w)

    def test_unconsented_topic_never_reaches_the_provider(self):
        reply, _ = self.session.handle("SecretThesis'te nerede kalmıştık?")
        self.assertEqual(self.spy.seen, [])                                       # no call at all
        self.assertIn("bulut paylaşımı kapalı", reply)
        self.assertIn("thesis note delta", reply)                                 # local answer still works
        self.assertEqual(self.store.list_turns()[-1]["generated_by"], "deterministic-offline")

    def test_consented_topic_payload_contains_only_that_project(self):
        self.session.handle("SecretThesis ne âlemde?")                            # prior turn mentioning the private project
        self.session.handle("PublicProj'da nerede kalmıştık?")
        payload = self.spy.seen[-1]
        self.assert_clean(payload)
        self.assertIn("PublicProj", payload); self.assertIn("Public task alpha", payload); self.assertIn("public note bravo", payload)
        req = json.loads(payload)
        self.assertEqual([p["name"] for p in req["projects"]], ["PublicProj"])
        self.assertTrue(all(i["project"] == "PublicProj" for i in req["items"]))
        self.assertTrue(all("SecretThesis" not in t["text"] for t in req["recent_turns"]))
        self.assertNotIn("summary", req)                                         # derived summary spans projects → dropped

    def test_cross_project_questions_redact_and_withhold(self):
        self.session.handle("Bugün hangi işe odaklanmam mantıklı?")
        payload = self.spy.seen[-1]
        self.assert_clean(payload)
        req = json.loads(payload)
        self.assertIn("Public task alpha", payload)
        self.assertTrue(any("withheld" in i for i in req["instructions"]))
        self.session.handle("Bugün SecretThesis mi PublicProj mu daha öncelikli?")
        payload = self.spy.seen[-1]
        self.assert_clean(payload)
        self.assertIn("[proje]", json.loads(payload)["question"])               # owner's own words are redacted too

    def test_comparison_with_unconsented_project_is_not_sent(self):
        self.session.handle("PublicProj ne âlemde?")
        n = len(self.spy.seen)
        reply, _ = self.session.handle("SecretThesis ile karşılaştır")
        self.assertEqual(len(self.spy.seen), n)                                   # no new provider call
        self.assertIn("bulut paylaşımı kapalı", reply)

    def test_everything_consented_shares_normally(self):
        self.store.set_project_settings(self.priv, cloud_consent=True)
        self.session.handle("SecretThesis'te nerede kalmıştık?")
        self.assertIn("thesis note delta", self.spy.seen[-1])


if __name__ == "__main__":
    unittest.main()
