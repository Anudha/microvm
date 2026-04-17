"""NetworkInterface — per-instance HTTP(S) proxy.

Each instance gets its own loopback proxy on a dedicated port. The browser is
launched with --proxy-server=http://127.0.0.1:<port>, so every request it
makes passes through us. That gives us:

  * Traffic isolation: separate proxies = separate logs, separate handlers,
    no shared in-memory state between instances.
  * A clean extension point: callers can subclass or pass a `handler` to
    inspect/block/modify requests without touching the browser.
  * Observability: every request is journaled, same pattern as the FS.

Scope. We implement HTTP CONNECT tunneling for HTTPS (the browser opens a
tunnel, we blindly relay bytes) and plain HTTP proxying. We deliberately do
NOT do TLS interception — MITMing HTTPS requires a trusted local CA, which
is a different project and a security-sensitive one. For HTTPS we log
connect-targets and byte counts; for HTTP we log method, URL, status.

This is stdlib-only on purpose: one less thing to install, and the proxy
logic is small enough that a dependency would cost more than it saves.
"""
from __future__ import annotations

import json
import select
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


@dataclass
class NetEvent:
    ts: float
    kind: str           # "http" | "connect"
    method: str         # "GET"/"POST"/... or "CONNECT"
    target: str         # full URL for http, host:port for connect
    status: int = 0     # HTTP status, or 0 for connect
    bytes_in: int = 0
    bytes_out: int = 0
    blocked: bool = False
    note: str = ""


# A handler decides what to do with a plain-HTTP request before it hits the
# network. Returning None means "pass through". Returning a (status, headers,
# body) tuple means "serve this response locally without touching the net".
Handler = Callable[[str, str, dict, bytes], "tuple[int, dict, bytes] | None"]


class _ProxyHandler(BaseHTTPRequestHandler):
    # Silence default stderr logging; we have our own journal.
    def log_message(self, format, *args):  # noqa: A002
        return

    # --- HTTPS: blind CONNECT tunnel -----------------------------------
    def do_CONNECT(self):  # noqa: N802
        host, _, port = self.path.partition(":")
        port = int(port or 443)
        iface: NetworkInterface = self.server.iface  # type: ignore[attr-defined]

        # Give handler a chance to veto.
        if iface._host_blocked(host):
            self.send_error(403, "blocked by microvm policy")
            iface._record(NetEvent(time.time(), "connect", "CONNECT",
                                   f"{host}:{port}", blocked=True))
            return

        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError as e:
            self.send_error(502, f"upstream connect failed: {e}")
            return

        self.send_response(200, "Connection Established")
        self.end_headers()

        bi, bo = _relay(self.connection, upstream)
        iface._record(NetEvent(time.time(), "connect", "CONNECT",
                               f"{host}:{port}", bytes_in=bi, bytes_out=bo))

    # --- Plain HTTP -----------------------------------------------------
    def _proxy_http(self, method: str) -> None:
        iface: NetworkInterface = self.server.iface  # type: ignore[attr-defined]
        url = self.path  # absolute URL in proxy mode
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if iface._host_blocked(host):
            self.send_error(403, "blocked by microvm policy")
            iface._record(NetEvent(time.time(), "http", method, url,
                                   blocked=True))
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("proxy-connection", "connection")}

        # Optional user handler can short-circuit.
        if iface._handler is not None:
            try:
                overridden = iface._handler(method, url, headers, body)
            except Exception as e:  # handler must never crash the proxy
                overridden = None
                note = f"handler error: {e!r}"
            else:
                note = ""
            if overridden is not None:
                status, h, b = overridden
                self._write_response(status, h, b)
                iface._record(NetEvent(time.time(), "http", method, url,
                                       status=status, bytes_out=len(b),
                                       note=note or "handled"))
                return

        # Forward upstream.
        try:
            req = Request(url, data=body if body else None,
                          method=method, headers=headers)
            with urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                resp_headers = dict(resp.getheaders())
                status = resp.status
        except HTTPError as e:
            resp_body = e.read() if hasattr(e, "read") else b""
            resp_headers = dict(e.headers) if e.headers else {}
            status = e.code
        except URLError as e:
            self.send_error(502, f"upstream error: {e}")
            iface._record(NetEvent(time.time(), "http", method, url,
                                   status=502))
            return

        self._write_response(status, resp_headers, resp_body)
        iface._record(NetEvent(time.time(), "http", method, url,
                               status=status, bytes_in=len(resp_body),
                               bytes_out=len(body)))

    def _write_response(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        # Strip hop-by-hop headers.
        hop = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade"}
        for k, v in headers.items():
            if k.lower() in hop:
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    # Bind all verbs to the same proxy path.
    def do_GET(self): self._proxy_http("GET")          # noqa: N802, E704
    def do_POST(self): self._proxy_http("POST")        # noqa: N802, E704
    def do_PUT(self): self._proxy_http("PUT")          # noqa: N802, E704
    def do_DELETE(self): self._proxy_http("DELETE")    # noqa: N802, E704
    def do_HEAD(self): self._proxy_http("HEAD")        # noqa: N802, E704
    def do_OPTIONS(self): self._proxy_http("OPTIONS")  # noqa: N802, E704
    def do_PATCH(self): self._proxy_http("PATCH")      # noqa: N802, E704


def _relay(a: socket.socket, b: socket.socket) -> tuple[int, int]:
    """Bidirectional byte-relay between two sockets. Returns (a->b, b->a)."""
    a.setblocking(False)
    b.setblocking(False)
    a_to_b = b_to_a = 0
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 30)
            if not r:
                break
            for src in r:
                dst = b if src is a else a
                try:
                    chunk = src.recv(65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    return a_to_b, b_to_a
                try:
                    dst.sendall(chunk)
                except OSError:
                    return a_to_b, b_to_a
                if src is a:
                    a_to_b += len(chunk)
                else:
                    b_to_a += len(chunk)
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass
    return a_to_b, b_to_a


class NetworkInterface:
    """Per-instance HTTP(S) proxy.

    Usage:
        net = NetworkInterface()
        net.start()
        browser --proxy-server=http://127.0.0.1:{net.port}
        ...
        net.stop()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        blocklist: set[str] | None = None,
        handler: Handler | None = None,
    ):
        self._host = host
        self._port = port
        self._blocklist = set(blocklist or ())
        self._handler = handler
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._journal: list[NetEvent] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> int:
        if self._server is not None:
            return self.port
        srv = ThreadingHTTPServer((self._host, self._port), _ProxyHandler)
        srv.iface = self  # type: ignore[attr-defined]
        self._server = srv
        self._port = srv.server_address[1]
        self._thread = threading.Thread(
            target=srv.serve_forever, name="microvm-proxy", daemon=True)
        self._thread.start()
        return self._port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    # ------------------------------------------------------------------ config
    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def proxy_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def block(self, host: str) -> None:
        self._blocklist.add(host)

    def unblock(self, host: str) -> None:
        self._blocklist.discard(host)

    def set_handler(self, handler: Handler | None) -> None:
        self._handler = handler

    def _host_blocked(self, host: str) -> bool:
        # Exact match or any parent-domain match.
        h = host.lower()
        if h in self._blocklist:
            return True
        parts = h.split(".")
        for i in range(1, len(parts)):
            if ".".join(parts[i:]) in self._blocklist:
                return True
        return False

    # ------------------------------------------------------------------ journal
    def _record(self, ev: NetEvent) -> None:
        with self._lock:
            self._journal.append(ev)

    def journal(self) -> list[NetEvent]:
        with self._lock:
            return list(self._journal)

    def dump_journal(self, path: Path) -> Path:
        with open(path, "w", encoding="utf-8") as f:
            for e in self.journal():
                f.write(json.dumps(e.__dict__) + "\n")
        return path
