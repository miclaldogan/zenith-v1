"""Fail-closed command execution for untrusted repository code (tests, builds).

Isolation baseline: bubblewrap with every namespace unshared, a read-only /usr, no /home, no
network, a tmpfs HOME, a cleared environment and the worktree as the only writable bind.
`systemd-run --user` is optional lifecycle/accounting on top (not a security boundary here).
If the baseline is unavailable, execution is REFUSED; there is no host fallback.
"""
from __future__ import annotations

import hashlib
import os
import resource
import tempfile
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .kernel import ZenithError

FORWARDER = Path(__file__).with_name("sandbox_forwarder.py")
PROXY_PORT = 3128

MAX_OUTPUT_BYTES = 1_000_000
TAIL_BYTES = 4_000
SAFE_PATH = "/usr/local/bin:/usr/bin"


class SandboxUnavailable(ZenithError):
    """Mandatory isolation is missing; nothing was executed."""


@dataclass
class SandboxProfile:
    """What a sandboxed process may see besides /usr and /work.

    `verification`: nothing else — no network, no credentials (untrusted repository code).
    `provider`: a provider CLI — egress only through the host allowlist proxy socket, credential
    files bound read-only where the adapter says, extra read-only binds (e.g. the CLI itself).
    """
    name: str = "verification"
    proxy_socket: Path | None = None                       # host unix socket of AllowlistProxy
    ro_binds: list[tuple[Path, str]] = field(default_factory=list)   # (host path, sandbox path)
    rw_binds: list[tuple[Path, str]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    env_files: dict[str, str] = field(default_factory=dict)   # VAR -> sandbox path; read by the forwarder in-sandbox
    work_readonly: bool = False                            # director stages read the repo only

    def validate(self) -> None:
        if self.name not in ("verification", "provider"):
            raise ZenithError(f"Unknown sandbox profile {self.name!r}")
        if self.name == "verification" and (self.proxy_socket or self.rw_binds):
            raise ZenithError("verification profile may not have network or extra writable paths")
        for host, dest in (*self.ro_binds, *self.rw_binds):
            if not Path(host).exists():
                raise ZenithError(f"bind source does not exist: {host}")
            if not dest.startswith(("/run/zenith/", "/opt/", "/home/sandbox/")):
                raise ZenithError(f"bind destination outside the allowed sandbox prefixes: {dest}")
        for key in (*self.env, *self.env_files):
            if key.upper() in ("PATH", "HOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH"):
                raise ZenithError(f"profile may not override {key}")
        if self.env_files and self.proxy_socket is None:
            raise ZenithError("env_files need the forwarder, i.e. a provider profile with a proxy socket")


@dataclass
class Capabilities:
    bwrap: str | None = None
    bwrap_version: str | None = None
    user_namespaces: bool = False
    network_isolation: bool = False
    systemd_run: str | None = None
    ok: bool = False
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


_PROBE = ("import os,socket\n"
          "assert not os.path.exists(os.path.expanduser('~/..')) or True\n"
          "s=socket.socket(); s.settimeout(0.5)\n"
          "try:\n s.connect(('203.0.113.1', 9)); print('NET-OPEN')\n"
          "except OSError: print('NET-CLOSED')\n"
          "print('HOME-HIDDEN' if sorted(os.listdir('/home')) == ['sandbox'] else 'HOME-VISIBLE')\n")


def _base_argv(bwrap: str, worktree: Path, network: bool, profile: "SandboxProfile | None" = None) -> list[str]:
    bind = "--ro-bind" if (profile and profile.work_readonly) else "--bind"
    argv = [bwrap, "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/bin", "/sbin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home/sandbox",
            bind, str(worktree), "/work", "--chdir", "/work",
            "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--die-with-parent", "--new-session", "--clearenv",
            "--setenv", "PATH", SAFE_PATH, "--setenv", "HOME", "/home/sandbox", "--setenv", "TMPDIR", "/tmp",
            "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "GIT_TERMINAL_PROMPT", "0", "--setenv", "ZENITH_SANDBOX", "1"]
    for ro in ("/etc/ld.so.cache", "/etc/localtime", "/etc/ssl/certs", "/etc/ca-certificates", "/etc/resolv.conf"):
        if os.path.exists(ro):
            argv += ["--ro-bind", ro, ro]
    if profile is not None:
        for host, dest in profile.ro_binds:
            argv += ["--ro-bind", str(host), dest]
        for host, dest in profile.rw_binds:
            argv += ["--bind", str(host), dest]
        if profile.proxy_socket is not None:
            argv += ["--ro-bind", str(profile.proxy_socket), "/run/zenith/proxy.sock",
                     "--ro-bind", str(FORWARDER), "/opt/zenith/forwarder.py"]
        for key, value in profile.env.items():
            argv += ["--setenv", key, value]
    # The network namespace is ALWAYS unshared; a provider profile reaches the host proxy only
    # through the unix socket above (loopback inside the sandbox is up; nothing else exists).
    argv += ["--unshare-net"]
    # The sandbox root is a tmpfs; make it read-only so only /work, /tmp and /home/sandbox accept writes.
    argv += ["--remount-ro", "/"]
    return argv


def detect(probe_dir: Path | None = None) -> Capabilities:
    """Probe the real kernel/runtime; the result decides whether execution is allowed at all."""
    caps = Capabilities(bwrap=shutil.which("bwrap"), systemd_run=shutil.which("systemd-run"))
    if not caps.bwrap:
        caps.reasons.append("bubblewrap (bwrap) not installed")
        return caps
    try:
        caps.bwrap_version = subprocess.run([caps.bwrap, "--version"], capture_output=True, text=True,
                                            timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        caps.reasons.append("bwrap --version failed")
        return caps
    import tempfile
    with tempfile.TemporaryDirectory(dir=probe_dir) as tmp:
        try:
            r = subprocess.run([*_base_argv(caps.bwrap, Path(tmp), network=False), "python", "-c", _PROBE],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            caps.reasons.append(f"bwrap probe failed: {exc}")
            return caps
    if r.returncode != 0:
        caps.reasons.append(f"bwrap probe exited {r.returncode}: {r.stderr.strip()[:300]}")
        return caps
    caps.user_namespaces = True
    caps.network_isolation = "NET-CLOSED" in r.stdout
    if "HOME-VISIBLE" in r.stdout:
        caps.reasons.append("home directory visible inside sandbox")
    if not caps.network_isolation:
        caps.reasons.append("network not isolated inside sandbox")
    caps.ok = not caps.reasons
    return caps


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    output_bytes: int
    truncated: bool
    output_digest: str
    output_tail: str
    sandbox: str = "bwrap"

    def as_dict(self) -> dict:
        return asdict(self)


def _limits(cpu_seconds: int):
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    return apply


def run_isolated(argv: list[str], worktree: Path, timeout: float, caps: Capabilities | None = None,
                 network: bool = False, max_output_bytes: int = MAX_OUTPUT_BYTES,
                 profile: SandboxProfile | None = None,
                 on_line: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> CommandResult:
    """Run `argv` (no shell) inside the sandbox with the worktree mounted at /work.

    `on_line` receives each complete output line as it arrives (streaming JSONL from a CLI).
    """
    caps = caps or detect()
    if not caps.ok:
        raise SandboxUnavailable("Refusing to execute: " + "; ".join(caps.reasons))
    if not argv or not all(isinstance(a, str) and a for a in argv):
        raise ZenithError("Command must be a non-empty list of strings")
    if network:
        raise ZenithError("Direct network is never granted; use a provider profile with a proxy socket")
    worktree = Path(worktree).resolve(strict=True)
    if not worktree.is_dir():
        raise ZenithError("Worktree is not a directory")
    if profile is not None:
        profile.validate()
        if profile.proxy_socket is not None:
            env_args = [x for var, path in profile.env_files.items() for x in ("--env-file", f"{var}={path}")]
            argv = ["python", "-I", "/opt/zenith/forwarder.py", "/run/zenith/proxy.sock", str(PROXY_PORT),
                    *env_args, "--", *argv]
    full = [*_base_argv(caps.bwrap, worktree, False, profile), *argv]
    pending = b""
    stopped = False
    started = time.monotonic()
    proc = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True, preexec_fn=_limits(int(timeout) + 1), env={"PATH": SAFE_PATH})
    chunks: list[bytes] = []
    size, truncated, timed_out = 0, False, False
    deadline = started + timeout
    try:
        assert proc.stdout is not None
        os.set_blocking(proc.stdout.fileno(), False)
        while True:
            if time.monotonic() > deadline:
                timed_out = True
                break
            if should_stop is not None and should_stop():
                stopped = True
                break
            try:
                chunk = proc.stdout.read(65536)
            except BlockingIOError:
                chunk = None
            if chunk:
                if size + len(chunk) > max_output_bytes:
                    chunk = chunk[: max(0, max_output_bytes - size)]
                    truncated = True
                size += len(chunk)
                chunks.append(chunk)
                if on_line is not None:
                    pending += chunk
                    *lines, pending = pending.split(b"\n")
                    for line in lines:
                        on_line(line.decode("utf-8", "replace"))
                if truncated:
                    timed_out = False
                    break
            elif chunk == b"" or proc.poll() is not None:
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
            else:
                time.sleep(0.01)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
    out = b"".join(chunks)
    if on_line is not None and pending:
        on_line(pending.decode("utf-8", "replace"))
    return CommandResult(argv=list(argv), exit_code=None if (timed_out or truncated or stopped) else proc.returncode,
                         timed_out=timed_out, duration_seconds=round(time.monotonic() - started, 3),
                         output_bytes=size, truncated=truncated, output_digest=hashlib.sha256(out).hexdigest(),
                         output_tail=out[-TAIL_BYTES:].decode("utf-8", "replace"))
