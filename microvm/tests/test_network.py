import http.client
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from microvm.interfaces.network import NetworkInterface


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        body = b"hello from upstream"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address
    srv.shutdown(); srv.server_close()


def test_start_stop():
    net = NetworkInterface()
    port = net.start()
    assert port > 0
    net.stop()


def test_http_passthrough(upstream):
    host, port = upstream
    net = NetworkInterface()
    net.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", net.port, timeout=5)
        conn.request("GET", f"http://{host}:{port}/x")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert body == b"hello from upstream"
    finally:
        net.stop()
    j = net.journal()
    assert any(e.kind == "http" and e.status == 200 for e in j)


def test_blocklist(upstream):
    host, port = upstream
    net = NetworkInterface(blocklist={host})
    net.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", net.port, timeout=5)
        conn.request("GET", f"http://{host}:{port}/x")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 403
    finally:
        net.stop()
    assert any(e.blocked for e in net.journal())


def test_handler_short_circuits(upstream):
    host, port = upstream

    def h(method, url, headers, body):
        return (418, {"Content-Type": "text/plain"}, b"teapot")

    net = NetworkInterface(handler=h)
    net.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", net.port, timeout=5)
        conn.request("GET", f"http://{host}:{port}/x")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 418
        assert body == b"teapot"
    finally:
        net.stop()


def test_two_proxies_isolated(upstream):
    host, port = upstream
    a = NetworkInterface(); a.start()
    b = NetworkInterface(); b.start()
    try:
        assert a.port != b.port
        conn = http.client.HTTPConnection("127.0.0.1", a.port, timeout=5)
        conn.request("GET", f"http://{host}:{port}/x")
        conn.getresponse().read()
        assert len(a.journal()) == 1
        assert len(b.journal()) == 0
    finally:
        a.stop(); b.stop()
