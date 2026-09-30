"""Header rewriting rules for the KeyMux hop.

Two rules matter:

* The router key must never reach the upstream. Every header that could carry
  a credential is dropped and replaced with the route's real upstream key.
* Hop-by-hop headers describe the client/KeyMux connection only. Forwarding
  them confuses the KeyMux/upstream connection, so they are dropped too.
"""

from __future__ import annotations

from collections.abc import Mapping

from .config import RouteDefinition
from .routing import AUTHORIZATION_HEADER

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# Dropped from the request and re-added from the route, so a router key can
# never leak upstream.
CREDENTIAL_HEADERS = frozenset(
    {
        AUTHORIZATION_HEADER,
        "api-key",
        "x-api-key",
        "x-goog-api-key",
    }
)

# httpx negotiates and transparently decodes its own encoding; forwarding the
# client's value would advertise a coding the body no longer has.
REQUEST_HEADERS_REPLACED_BY_HTTPX = frozenset({"content-length", "accept-encoding"})

RESPONSE_HEADERS_OWNED_BY_KEYMUX = frozenset(
    {"content-length", "content-encoding", "transfer-encoding"}
)


def build_upstream_headers(incoming: Mapping[str, str], route: RouteDefinition) -> dict[str, str]:
    """Return the headers to send upstream: client headers minus secrets."""
    headers = {
        name: value
        for name, value in incoming.items()
        if _is_forwardable_request_header(name)
    }
    headers[route.upstream.api_key_header.lower()] = upstream_authorization_value(route)
    return headers


def upstream_authorization_value(route: RouteDefinition) -> str:
    scheme = route.upstream.authorization_scheme
    if scheme:
        return f"{scheme} {route.upstream.api_key}"
    return route.upstream.api_key


def filter_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the upstream response headers that KeyMux can pass through."""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in RESPONSE_HEADERS_OWNED_BY_KEYMUX
        and name.lower() != "host"
    }


def _is_forwardable_request_header(name: str) -> bool:
    lowered = name.lower()
    if lowered in HOP_BY_HOP_HEADERS or lowered in CREDENTIAL_HEADERS:
        return False
    if lowered in REQUEST_HEADERS_REPLACED_BY_HTTPX:
        return False
    return lowered != "host"
