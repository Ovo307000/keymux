"""Minimal HTTP-over-named-pipe client for the mihomo external controller.

Clash Verge Rev runs mihomo with the TCP external controller disabled and
exposes the REST API only through the Windows named pipe
``\\.\pipe\verge-mihomo``. This module speaks plain HTTP/1.1 over that pipe.

The controller secret is read from the Verge runtime configuration so this
tool never hard-codes a credential.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PIPE_PATH = r"\\.\pipe\verge-mihomo"
FALLBACK_SECRET = "set-your-secret"
SECRET_PATTERN = re.compile(r"^secret:\s*(.+?)\s*$", re.MULTILINE)


def read_controller_secret(runtime_config_path: Path) -> str:
    """Return the controller secret declared in the generated runtime config."""
    if not runtime_config_path.is_file():
        return FALLBACK_SECRET
    match = SECRET_PATTERN.search(runtime_config_path.read_text(encoding="utf-8"))
    if not match:
        return FALLBACK_SECRET
    return match.group(1).strip().strip("'\"")


def request(
    method: str,
    path: str,
    body: dict | None = None,
    secret: str = FALLBACK_SECRET,
) -> tuple[int, bytes]:
    """Send one HTTP request to the mihomo controller pipe."""
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: localhost",
        "Connection: close",
        f"Authorization: Bearer {secret}",
    ]
    if payload:
        headers.append("Content-Type: application/json")
        headers.append(f"Content-Length: {len(payload)}")
    raw_request = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + payload

    with open(PIPE_PATH, "r+b", buffering=0) as handle:
        handle.write(raw_request)
        raw_response = _read_until_close(handle)

    return _split_response(raw_response)


def request_json(method: str, path: str, body: dict | None = None,
                 secret: str = FALLBACK_SECRET):
    status, payload = request(method, path, body, secret)
    if not payload:
        return status, None
    return status, json.loads(payload.decode("utf-8"))


def reload_runtime_config(config_path: Path, secret: str = FALLBACK_SECRET) -> tuple[int, bytes]:
    return request("PUT", "/configs?force=true", {"path": str(config_path)}, secret)


def list_proxy_names(secret: str = FALLBACK_SECRET) -> list[str]:
    _, document = request_json("GET", "/proxies", secret=secret)
    return sorted(document["proxies"].keys())


def _read_until_close(handle) -> bytes:
    buffer = b""
    while True:
        chunk = handle.read(65536)
        if not chunk:
            return buffer
        buffer += chunk


def _split_response(raw_response: bytes) -> tuple[int, bytes]:
    header_block, _, body = raw_response.partition(b"\r\n\r\n")
    status = int(header_block.split(b"\r\n")[0].split(b" ")[1])
    if b"transfer-encoding: chunked" in header_block.lower():
        body = _decode_chunked(body)
    return status, body


def _decode_chunked(payload: bytes) -> bytes:
    decoded = b""
    while True:
        line_end = payload.find(b"\r\n")
        if line_end == -1:
            return decoded
        size_field = payload[:line_end].split(b";")[0].strip()
        if not size_field:
            payload = payload[line_end + 2:]
            continue
        size = int(size_field, 16)
        if size == 0:
            return decoded
        start = line_end + 2
        decoded += payload[start:start + size]
        payload = payload[start + size + 2:]
