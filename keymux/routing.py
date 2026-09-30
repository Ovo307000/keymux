"""Router key resolution.

The client only ever sends its router key. KeyMux looks that key up and returns
the matching route. Comparison uses :func:`secrets.compare_digest` so that a
timing side channel cannot reveal how much of a router key is correct.
"""

from __future__ import annotations

import secrets

from .config import RouteDefinition
from .errors import InvalidApiKeyError, MissingApiKeyError

AUTHORIZATION_HEADER = "authorization"
API_KEY_HEADER = "x-api-key"
AUTHORIZATION_SCHEME = "bearer"


class RouteRegistry:
    """Immutable lookup table from router key to route."""

    def __init__(self, routes: tuple[RouteDefinition, ...]) -> None:
        self._routes = routes
        # Keys are compared as UTF-8 bytes: secrets.compare_digest rejects
        # non-ASCII strings outright, and a client must never be able to turn
        # its own header into a server error.
        self._by_key = {route.incoming_key.encode("utf-8"): route for route in routes}

    @property
    def routes(self) -> tuple[RouteDefinition, ...]:
        return self._routes

    def resolve_from_headers(self, authorization: str | None, api_key: str | None) -> RouteDefinition:
        """Return the route for the presented router key, or raise a 401 error."""
        presented = _extract_router_key(authorization, api_key)
        if presented is None:
            raise MissingApiKeyError(
                "no router key found; send 'Authorization: Bearer <router key>'"
            )
        route = self._lookup(presented)
        if route is None:
            raise InvalidApiKeyError("unknown router key")
        return route

    def describe(self) -> list[dict[str, object]]:
        """Route metadata safe to expose: no router keys, no upstream keys."""
        return [
            {
                "route": route.name,
                "upstream_base_url": route.upstream.base_url,
                "egress": route.egress.label,
                "egress_proxy": route.egress.proxy or "direct",
                "credential_header": route.upstream.api_key_header,
            }
            for route in self._routes
        ]

    def _lookup(self, presented: str) -> RouteDefinition | None:
        try:
            presented_bytes = presented.encode("utf-8")
        except UnicodeEncodeError:
            return None
        match: RouteDefinition | None = None
        for key, route in self._by_key.items():
            if secrets.compare_digest(key, presented_bytes):
                match = route
        return match


def _extract_router_key(authorization: str | None, api_key: str | None) -> str | None:
    token = _strip_authorization_scheme(authorization)
    if token:
        return token
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _strip_authorization_scheme(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2:
        if parts[0].lower() == AUTHORIZATION_SCHEME:
            return parts[1].strip() or None
        return None
    if parts[0].lower() == AUTHORIZATION_SCHEME:
        # A bare "Bearer" carries no key; report a missing key, not an unknown one.
        return None
    return parts[0].strip() or None
