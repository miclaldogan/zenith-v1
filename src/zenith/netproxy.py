"""Host-side allowlisting HTTP CONNECT proxy served on a unix socket.

The sandbox has no network namespace of its own; the only way out is this socket, bound
read-only into it. Every CONNECT is checked against an explicit `host:port` allowlist and
audited through a callback. TLS is passed through untouched (no interception), so provider
credentials inside the sandbox are never visible to the proxy and the proxy can never add
credentials — it only decides *where* bytes may go.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import threading
from pathlib import Path
from typing import Callable

_CONNECT = re.compile(rb"^CONNECT\s+([A-Za-z0-9.\-]+|\[[0-9a-fA-F:.]+\]):(\d{1,5})\s+HTTP/1\.[01]\r?\n")
MAX_HEADER = 8192


def is_private_address(ip: str) -> bool:
    """Loopback, link-local, RFC1918/ULA, multicast, unspecified, reserved: never a provider."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
            or addr.is_unspecified or addr.is_reserved)


def resolve_public(host: str, port: int) -> list[tuple]:
    """Resolve on the host side (the sandbox has no DNS) and keep only public addresses.

    Connecting to the *resolved* address, not the name, closes DNS-rebinding: a permitted name
    cannot be steered to a private address between check and connect.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [(fam, addr) for fam, _, _, _, addr in infos if not is_private_address(addr[0])]


class AllowlistProxy:
    def __init__(self, socket_path: Path, allowed: set[str], audit: Callable[[str, dict], None] | None = None,
                 connect_timeout: float = 10.0, allow_private: bool = False):
        self.socket_path = Path(socket_path)
        self.allowed = {a.strip().lower() for a in allowed if a.strip()}
        for entry in self.allowed:
            if not re.fullmatch(r"([a-z0-9.\-]+|\[[0-9a-f:.]+\]):\d{1,5}", entry):
                raise ValueError(f"allowlist entries must be host:port, got {entry!r}")
        # Loopback/private targets are only for tests; a provider allowlist never needs them.
        self.allow_private = allow_private
        self.audit = audit or (lambda kind, payload: None)
        self.connect_timeout = connect_timeout
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.connections: list[dict] = []

    # -- lifecycle --------------------------------------------------------------------------
    def start(self) -> "AllowlistProxy":
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        srv.listen(16)
        srv.settimeout(0.2)
        self._server = srv
        self._thread = threading.Thread(target=self._serve, name="zenith-netproxy", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._server:
            self._server.close()
        if self.socket_path.exists():
            self.socket_path.unlink()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- serving ----------------------------------------------------------------------------
    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(10)
        try:
            head = b""
            while b"\r\n\r\n" not in head and b"\n\n" not in head:
                chunk = client.recv(1024)
                if not chunk:
                    return
                head += chunk
                if len(head) > MAX_HEADER:
                    client.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                    return
            m = _CONNECT.match(head)
            if not m:
                # Log the method token only: never headers (a Proxy-Authorization line could follow).
                method = head.split(b" ", 1)[0][:16].decode("latin1", "replace")
                self._record("denied", {"reason": "not_connect", "method": method})
                client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                return
            raw_host, port = m.group(1).decode().lower(), int(m.group(2))
            host = raw_host.strip("[]")
            target = f"{raw_host}:{port}"
            if target not in self.allowed and f"{host}:{port}" not in self.allowed:
                self._record("denied", {"target": target})
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                return
            if self.allow_private:
                try:
                    candidates = [(fam, addr) for fam, _, _, _, addr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
                except OSError:
                    candidates = []
            else:
                candidates = resolve_public(host, port)
                if not candidates:
                    self._record("private_blocked", {"target": target})
                    client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                    return
            upstream = None
            last_error = "no address"
            for fam, addr in candidates:  # IPv4 and IPv6 alike; connect to the vetted address itself
                try:
                    upstream = socket.socket(fam, socket.SOCK_STREAM)
                    upstream.settimeout(self.connect_timeout)
                    upstream.connect(addr)
                    break
                except OSError as exc:
                    last_error = str(exc)
                    upstream.close()
                    upstream = None
            if upstream is None:
                self._record("upstream_failed", {"target": target, "error": last_error[:200]})
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            self._record("allowed", {"target": target, "address": upstream.getpeername()[0]})
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(None)
            upstream.settimeout(None)
            a = threading.Thread(target=self._pump, args=(client, upstream), daemon=True)
            b = threading.Thread(target=self._pump, args=(upstream, client), daemon=True)
            a.start(); b.start(); a.join(); b.join()
            upstream.close()
        except OSError:
            pass
        finally:
            client.close()

    @staticmethod
    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _record(self, decision: str, payload: dict) -> None:
        entry = {"decision": decision, **payload}
        self.connections.append(entry)
        self.audit("network." + decision, payload)
