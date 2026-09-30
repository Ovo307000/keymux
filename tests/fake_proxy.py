"""A recording HTTP forward proxy.

It never forwards anything: it records which proxy endpoint received the
request and answers with its own name. That is exactly what the isolation tests
need to prove - that the request for router key A reached proxy A and not
proxy B.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.support import ThreadedHttpServer, start_server


class ProxyRecorder:
    """Collects the requests a single fake proxy endpoint received."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def record(self, entry: dict) -> None:
        with self.lock:
            self.requests.append(entry)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.requests)


class RecordingProxyServer(ThreadingHTTPServer):
    def __init__(self, address, recorder: ProxyRecorder) -> None:
        super().__init__(address, RecordingProxyHandler)
        self.recorder = recorder


class RecordingProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KeyMuxRecordingProxy/1.0"
    server: RecordingProxyServer

    def log_message(self, *arguments) -> None:
        pass

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def do_PUT(self) -> None:
        self._respond()

    def do_DELETE(self) -> None:
        self._respond()

    def _respond(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.recorder.record(
            {
                "method": self.command,
                "target": self.path,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body": body.decode("utf-8", "replace"),
            }
        )
        payload = json.dumps({"served_by_proxy": self.server.recorder.name}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_proxy(recorder: ProxyRecorder) -> ThreadedHttpServer:
    """Start a recording proxy on a free loopback port."""
    return start_server(lambda address: RecordingProxyServer(address, recorder))
