"""The configuration model: what a loaded KeyMux configuration looks like.

Kept apart from :mod:`keymux.config` so that the loader and the model can each
stay readable, and so modules that only need the types never pull in YAML.
"""

from __future__ import annotations

from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    """Raised when the configuration cannot produce a usable setup."""


@dataclass(frozen=True)
class EgressDefinition:
    """The Clash/Mihomo endpoint that decides the public exit IP."""

    proxy: str | None
    label: str


@dataclass(frozen=True)
class UpstreamDefinition:
    """The real upstream the route forwards to."""

    base_url: str
    api_key: str
    api_key_header: str = "Authorization"
    authorization_scheme: str = "Bearer"


@dataclass(frozen=True)
class VerifyDefinition:
    """How the verification tool should probe a route.

    Not every upstream serves every path: AnyRouter answers ``GET /v1/models``
    and ``POST /v1/responses`` but returns 404 for ``/v1/chat/completions``, so
    the probe has to be declared per route instead of assumed.
    """

    method: str = "POST"
    path: str = "/v1/chat/completions"
    streaming: bool = True


@dataclass(frozen=True)
class RouteDefinition:
    """One router key, one upstream, one egress."""

    name: str
    incoming_key: str
    upstream: UpstreamDefinition
    egress: EgressDefinition
    verify: VerifyDefinition = VerifyDefinition()


@dataclass(frozen=True)
class TimeoutSettings:
    connect_seconds: float = 15.0
    read_seconds: float = 600.0
    write_seconds: float = 60.0
    pool_seconds: float = 15.0


@dataclass(frozen=True)
class PoolSettings:
    max_connections: int = 64
    max_keepalive_connections: int = 16


@dataclass(frozen=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 9000


@dataclass(frozen=True)
class KeyMuxSettings:
    server: ServerSettings
    routes: tuple[RouteDefinition, ...]
    timeouts: TimeoutSettings
    pool: PoolSettings
