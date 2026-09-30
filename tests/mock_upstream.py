"""A stand-in OpenAI-compatible upstream for tests.

Plain ``http.server`` on purpose: it lets a test control exactly when each SSE
event is written, and lets a test see whether KeyMux aborted an upstream stream
after the client went away.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

UPSTREAM_STREAM_EVENT_COUNT = 4
UPSTREAM_STREAM_DELAY_SECONDS = 0.25
SLOW_STREAM_EVENT_COUNT = 15
SLOW_STREAM_DELAY_SECONDS = 0.25
SLOW_RESPONSE_SECONDS = 2.0


class UpstreamState:
    """Everything the test wants to observe about the mock upstream."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.client_went_away = False

    def record(self, entry: dict) -> None:
        with self.lock:
            self.requests.append(entry)

    def last_request(self) -> dict:
        with self.lock:
            return self.requests[-1]

    def mark_client_gone(self) -> None:
        with self.lock:
            self.client_went_away = True


class MockUpstreamServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that carries the state the handler reports to."""

    def __init__(self, address, state: UpstreamState) -> None:
        super().__init__(address, MockUpstreamHandler)
        self.state = state


class MockUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KeyMuxMockUpstream/1.0"
    server: MockUpstreamServer

    def log_message(self, *arguments) -> None:  # keep test output clean
        pass

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        body = _read_body(self)
        parsed = urlparse(self.path)
        self.server.state.record(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body": body.decode("utf-8", "replace"),
            }
        )
        self._answer(parsed.path, parsed.query, body)

    def _answer(self, path: str, query: str, body: bytes) -> None:
        if path.startswith("/v1/status/"):
            return self._send_status(int(path.rsplit("/", 1)[1]))
        if path == "/v1/echo":
            return self._send_echo(path, query, body)
        if path == "/v1/request-headers":
            return self._send_json({name.lower(): value for name, value in self.headers.items()})
        if path == "/v1/stream":
            return self._send_event_stream(UPSTREAM_STREAM_EVENT_COUNT, UPSTREAM_STREAM_DELAY_SECONDS)
        if path == "/v1/stream-long":
            return self._send_event_stream(SLOW_STREAM_EVENT_COUNT, SLOW_STREAM_DELAY_SECONDS)
        if path == "/v1/slow":
            time.sleep(SLOW_RESPONSE_SECONDS)
            return self._send_json({"slow": True})
        return self._send_json({"error": "unknown mock path", "path": path}, status=404)

    def _send_echo(self, path: str, query: str, body: bytes) -> None:
        self._send_json(
            {
                "method": self.command,
                "path": path,
                "query": query,
                "authorization": self.headers.get("authorization"),
                "x_api_key": self.headers.get("x-api-key"),
                "body": body.decode("utf-8", "replace"),
            }
        )

    def _send_status(self, code: int) -> None:
        headers = {"retry-after": "7"} if code == 429 else None
        self._send_json(
            {
                "error": {
                    "message": f"upstream deliberately returned {code}",
                    "type": "mock_upstream_error",
                    "code": f"mock_{code}",
                }
            },
            status=code,
            extra_headers=headers,
        )

    def _send_json(self, payload: dict, status: int = 200, extra_headers: dict | None = None) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_event_stream(self, event_count: int, delay_seconds: float) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for index in range(event_count):
                self.wfile.write(f'data: {{"index": {index}}}\n\n'.encode("utf-8"))
                self.wfile.flush()
                if delay_seconds:
                    time.sleep(delay_seconds)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.server.state.mark_client_gone()


from tests.support import ThreadedHttpServer, start_server


def start_upstream(state: UpstreamState) -> ThreadedHttpServer:
    """Start the mock upstream on a free loopback port."""
    return start_server(lambda address: MockUpstreamServer(address, state))


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = handler.headers.get("content-length")
    if length:
        return handler.rfile.read(int(length))
    if handler.headers.get("transfer-encoding", "").lower() == "chunked":
        return _read_chunked(handler)
    return b""


def _read_chunked(handler: BaseHTTPRequestHandler) -> bytes:
    chunks = b""
    while True:
        size_line = handler.rfile.readline().strip()
        if not size_line:
            break
        size = int(size_line.split(b";")[0], 16)
        if size == 0:
            handler.rfile.readline()
            break
        chunks += handler.rfile.read(size)
        handler.rfile.readline()
    return chunks
