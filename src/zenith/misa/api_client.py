"""In-sandbox Anthropic Messages API client for Misa's conversation role (stdlib only).

Runs under bubblewrap via the forwarder: the API key arrives only as an environment variable
injected inside the sandbox; egress goes through the allowlisting CONNECT proxy (HTTPS_PROXY),
always as a CONNECT tunnel. Reads the request document, posts ONE message, writes the raw reply.
Nothing here imports Zenith. Exit codes: 0 ok, 64 usage, 65 bad input, 69 network, 77 auth, 70 other.
"""
import http.client
import json
import os
import sys
from urllib.parse import urlsplit

API_VERSION = "2023-06-01"


def _connection(base: str, timeout: float):
    u = urlsplit(base)
    secure = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if secure else 80)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    cls = http.client.HTTPSConnection if secure else http.client.HTTPConnection
    if proxy:
        p = urlsplit(proxy)
        conn = cls(p.hostname, p.port or 3128, timeout=timeout)
        conn.set_tunnel(host, port)          # CONNECT host:port through the allowlist proxy
    else:
        conn = cls(host, port, timeout=timeout)
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
    body = {"model": doc["model"], "max_tokens": int(doc.get("max_tokens", 1200)), "system": doc["system"],
            "messages": [{"role": "user", "content": doc["user"]}], "temperature": 0.2}
    try:
        conn, prefix = _connection(base, float(os.environ.get("ANTHROPIC_TIMEOUT", "80")))
        conn.request("POST", f"{prefix}/v1/messages", body=json.dumps(body).encode(),
                     headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION,
                              "accept": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
    except (OSError, http.client.HTTPException) as exc:
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
