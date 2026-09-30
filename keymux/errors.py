"""Errors KeyMux produces on its own, plus the mapping of transport failures.

Upstream status codes are never rewritten by KeyMux: a response that the
upstream produced is forwarded unchanged. Only failures that happen *before*
an upstream response exists are translated, and they keep a specific code
instead of collapsing into a generic 500.
"""

from __future__ import annotations

import httpx

ERROR_TYPE_INVALID_REQUEST = "invalid_request_error"
ERROR_TYPE_AUTHENTICATION = "authentication_error"
ERROR_TYPE_UPSTREAM = "upstream_error"


class KeyMuxError(Exception):
    """Base class for errors that KeyMux answers itself."""

    status_code = 500
    code = "internal_error"
    error_type = "server_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_response_body(self) -> dict[str, object]:
        return {"error": {"message": self.message, "type": self.error_type, "code": self.code}}


class MissingApiKeyError(KeyMuxError):
    status_code = 401
    code = "missing_api_key"
    error_type = ERROR_TYPE_AUTHENTICATION


class InvalidApiKeyError(KeyMuxError):
    status_code = 401
    code = "invalid_api_key"
    error_type = ERROR_TYPE_AUTHENTICATION


class UpstreamConnectionError(KeyMuxError):
    status_code = 502
    code = "upstream_connection_failed"
    error_type = ERROR_TYPE_UPSTREAM


class UpstreamTimeoutError(KeyMuxError):
    status_code = 504
    code = "upstream_timeout"
    error_type = ERROR_TYPE_UPSTREAM


class UpstreamProtocolError(KeyMuxError):
    status_code = 502
    code = "upstream_protocol_error"
    error_type = ERROR_TYPE_UPSTREAM


def translate_transport_error(error: httpx.HTTPError, egress_label: str) -> KeyMuxError:
    """Turn an httpx failure into the status code the client should see."""
    detail = f"{type(error).__name__}: {error}"
    if isinstance(error, httpx.TimeoutException):
        return UpstreamTimeoutError(
            f"upstream timed out through egress {egress_label!r} ({detail})"
        )
    if isinstance(error, httpx.TransportError):
        return UpstreamConnectionError(
            f"upstream unreachable through egress {egress_label!r} ({detail})"
        )
    return UpstreamProtocolError(f"upstream request failed through egress {egress_label!r} ({detail})")
