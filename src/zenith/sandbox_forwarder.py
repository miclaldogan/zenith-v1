"""In-sandbox helper: forward 127.0.0.1:<port> to the host proxy's unix socket, then run a command.

Runs *inside* bubblewrap with `python -I` and no access to the rest of Zenith. The child
inherits HTTP(S)_PROXY pointing at the forwarder, so its only route out is the host's
allowlisting proxy. Exit status mirrors the child's.
"""
import os
import signal
import socket
import subprocess
import sys
import threading


def _pump(src, dst):
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


def _handle(client, sock_path):
    try:
        up = socket.socket(socket.AF_UNIX)
        up.connect(sock_path)
    except OSError:
        client.close()
        return
    a = threading.Thread(target=_pump, args=(client, up), daemon=True)
    b = threading.Thread(target=_pump, args=(up, client), daemon=True)
    a.start(); b.start(); a.join(); b.join()
    up.close(); client.close()


def _serve(listener, sock_path):
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(client, sock_path), daemon=True).start()


def main(argv):
    # forwarder.py SOCKET PORT [--env-file VAR=PATH ...] -- CMD [ARGS...]
    if len(argv) < 4:
        print("usage: forwarder.py SOCKET_PATH PORT [--env-file VAR=PATH]... -- CMD [ARGS...]", file=sys.stderr)
        return 64
    sock_path, port = argv[1], int(argv[2])
    env_files = {}
    i = 3
    while i < len(argv) and argv[i] == "--env-file":
        var, _, path = argv[i + 1].partition("=")
        env_files[var] = path
        i += 2
    if i >= len(argv) or argv[i] != "--":
        print("usage: forwarder.py SOCKET_PATH PORT [--env-file VAR=PATH]... -- CMD [ARGS...]", file=sys.stderr)
        return 64
    cmd = argv[i + 1:]
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(32)
    threading.Thread(target=_serve, args=(listener, sock_path), daemon=True).start()
    proxy = f"http://127.0.0.1:{port}"
    env = {**os.environ, "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
           "http_proxy": proxy, "https_proxy": proxy, "all_proxy": proxy, "NO_PROXY": "", "no_proxy": ""}
    for var, path in env_files.items():  # secret stays in this sandbox: file -> child env, nothing printed
        try:
            with open(path) as fh:
                env[var] = fh.read().strip()
        except OSError as exc:
            print(f"forwarder: cannot read credential file for {var}: {exc.strerror}", file=sys.stderr)
            return 78
    child = subprocess.Popen(cmd, env=env)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: child.send_signal(s))
    rc = child.wait()
    return 128 - rc if rc < 0 else rc  # a signal death stays visible to the host as 128+signal


if __name__ == "__main__":
    sys.exit(main(sys.argv))
