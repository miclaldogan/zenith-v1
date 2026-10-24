"""In-sandbox Anthropic Messages API client for Misa's conversation role (stdlib only).

Runs under bubblewrap via the forwarder: the API key arrives only as an environment variable
injected inside the sandbox. Egress: a plaintext HTTP CONNECT to the local allowlist proxy
(HTTPS_PROXY), then TLS negotiated *inside* the tunnel with the real API host — certificate
chain and hostname are verified against the system CA store (or ANTHROPIC_CA_BUNDLE for a test
CA). Nothing here imports Zenith. Exit codes: 0 ok, 64 usage, 65 bad input, 69 network/provider,
77 auth, 70 other.
"""
import http.client
import json
import os
import socket
import ssl
import sys
from urllib.parse import urlsplit

# API version 2026-10-21 (platform.claude.com/docs/en/api/versioning, read 2026-10-24): "The temperature,
# top_p, and top_k request parameters are removed. A request that sends any of them returns a 400 error
# on every model." Zenith therefore pins this version and never sends sampling parameters.
API_VERSION = "2026-10-21"
REMOVED_PARAMS = ("temperature", "top_p", "top_k")
REQUIRED_FIELDS = ("model", "max_tokens", "messages")


def build_messages_request(doc: dict) -> dict:
    """Exactly the fields the Messages API needs for one system+user turn — nothing else."""
    body = {
        "model": doc["model"],
        "max_tokens": int(doc.get("max_tokens", 1200)),
        "system": doc["system"],
        "messages": [{"role": "user", "content": doc["user"]}],
    }
    assert not any(k in body for k in REMOVED_PARAMS)
    return body


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=os.environ.get("ANTHROPIC_CA_BUNDLE") or None)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def open_connection(base: str, timeout: float):
    """CONNECT to the proxy over plaintext, then TLS to the target host through the tunnel."""
    u = urlsplit(base)
    secure = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if secure else 80)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if secure:
        ctx = _tls_context()
        if proxy:
            p = urlsplit(proxy)
            # Plain TCP to the proxy; the CONNECT request is sent in clear; only after the proxy's
            # "200 Connection Established" is the socket wrapped in TLS with server_hostname=host.
            conn = http.client.HTTPSConnection(p.hostname, p.port or 3128, timeout=timeout, context=ctx)
            conn.set_tunnel(host, port)
        else:
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    else:
        if proxy:
            p = urlsplit(proxy)
            conn = http.client.HTTPConnection(p.hostname, p.port or 3128, timeout=timeout)
            conn.set_tunnel(host, port)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
    return conn, u.path.rstrip("/")


def main(argv):
    if len(argv) != 3:
        print("usage: api_client.py REQUEST_JSON REPLY_JSON", file=sys.stderr)
        return 64
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY missing inside the sandbox", file=sys.stderr)
        return 77
    try:
        with open(argv[1]) as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"bad request document: {exc}", file=sys.stderr)
        return 65
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    body = build_messages_request(doc)
    try:
        conn, prefix = open_connection(base, float(os.environ.get("ANTHROPIC_TIMEOUT", "80")))
        conn.request("POST", f"{prefix}/v1/messages", body=json.dumps(body).encode(),
                     headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION,
                              "accept": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
    except ssl.SSLError as exc:
        print(f"tls: {exc}", file=sys.stderr)       # certificate/hostname failure: fail closed
        return 69
    except (OSError, socket.timeout, http.client.HTTPException) as exc:
        print(f"network: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 69
    if resp.status in (401, 403):
        print(f"HTTP {resp.status}: {data[:200].decode(errors='replace')}", file=sys.stderr)
        return 77
    if resp.status != 200:
        print(f"HTTP {resp.status}: {data[:300].decode(errors='replace')}", file=sys.stderr)
        return 69
    try:
        raw = json.loads(data.decode())
    except ValueError:
        print("provider returned non-JSON", file=sys.stderr)
        return 70
    text = "".join(part.get("text", "") for part in raw.get("content", []) if part.get("type") == "text")
    out = {"text": text, "usage": raw.get("usage", {}), "model": raw.get("model"), "stop_reason": raw.get("stop_reason"),
           "id": raw.get("id")}
    with open(argv[2], "w") as fh:
        json.dump(out, fh)
    print(json.dumps({"type": "api.done", "stop_reason": out["stop_reason"], "usage": out["usage"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
