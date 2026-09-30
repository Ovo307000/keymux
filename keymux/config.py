"""Configuration loading for KeyMux.

The YAML document describes one record per route. A route maps the router key
a client sends in ``Authorization`` to a real upstream API key, an upstream
base URL, and an egress proxy endpoint that pins the public exit IP.

Every string value supports ``${ENVIRONMENT_VARIABLE}`` expansion so that real
secrets never have to be written into the configuration file.
"""

from __future__ import annotations

import os
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv

ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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
class RouteDefinition:
    """One router key, one upstream, one egress."""

    name: str
    incoming_key: str
    upstream: UpstreamDefinition
    egress: EgressDefinition


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


def load_settings(
    path: str | Path,
    environment: Mapping[str, str] | None = None,
) -> KeyMuxSettings:
    """Read the configuration file and return validated settings."""
    if environment is None:
        load_dotenv(Path(path).parent / ".env", override=False)
        environment = os.environ

    document = _read_document(Path(path))
    document = _expand_environment_variables(document, environment)

    defaults = document.get("upstream_defaults", {})
    timeouts = _parse_timeouts(defaults)
    pool = _parse_pool(defaults)
    routes = _parse_routes(document.get("routes"), environment)
    server = _parse_server(document.get("server", {}))
    return KeyMuxSettings(server=server, routes=routes, timeouts=timeouts, pool=pool)


def _read_document(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"configuration file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, Mapping):
        raise ConfigurationError(f"configuration root must be a mapping: {path}")
    return document


def _expand_environment_variables(value: Any, environment: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _expand_string(value, environment)
    if isinstance(value, Mapping):
        return {key: _expand_environment_variables(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment_variables(item, environment) for item in value]
    return value


def _expand_string(value: str, environment: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environment or environment[name] == "":
            raise ConfigurationError(f"environment variable {name} is referenced but not set")
        return environment[name]

    return ENVIRONMENT_VARIABLE_PATTERN.sub(replace, value)


def _parse_timeouts(defaults: Mapping[str, Any]) -> TimeoutSettings:
    return TimeoutSettings(
        connect_seconds=_positive_number(defaults, "connect_timeout_seconds", 15.0),
        read_seconds=_positive_number(defaults, "read_timeout_seconds", 600.0),
        write_seconds=_positive_number(defaults, "write_timeout_seconds", 60.0),
        pool_seconds=_positive_number(defaults, "pool_timeout_seconds", 15.0),
    )


def _parse_pool(defaults: Mapping[str, Any]) -> PoolSettings:
    return PoolSettings(
        max_connections=_positive_integer(defaults, "max_connections", 64),
        max_keepalive_connections=_positive_integer(defaults, "max_keepalive_connections", 16),
    )


def _positive_number(source: Mapping[str, Any], key: str, fallback: float) -> float:
    if key not in source:
        return fallback
    try:
        value = float(source[key])
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"upstream_defaults.{key} must be a number, got {source[key]!r}"
        ) from error
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(
            f"upstream_defaults.{key} must be a finite number greater than zero, got {source[key]!r}"
        )
    return value


def _positive_integer(source: Mapping[str, Any], key: str, fallback: int) -> int:
    value = _positive_number(source, key, fallback)
    if value != int(value):
        raise ConfigurationError(f"upstream_defaults.{key} must be a whole number, got {value}")
    return int(value)


def _parse_server(raw: Mapping[str, Any]) -> ServerSettings:
    host = str(raw.get("host", "127.0.0.1"))
    port = _port(raw.get("port", 9000), "server.port")
    allow_non_loopback = bool(raw.get("allow_non_loopback", False))
    if host not in LOOPBACK_HOSTS and not allow_non_loopback:
        raise ConfigurationError(
            f"server.host {host!r} is not a loopback address; set server.allow_non_loopback: true "
            "to expose KeyMux beyond this machine"
        )
    return ServerSettings(host=host, port=port)


def _port(value: Any, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"{field} must be an integer, got {value!r}") from error
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"{field} must be between 1 and 65535, got {port}")
    return port


def _parse_routes(raw: Any, environment: Mapping[str, str]) -> tuple[RouteDefinition, ...]:
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigurationError("configuration must define at least one route under 'routes'")

    defaults = _default_headers(environment)
    routes: list[RouteDefinition] = []
    seen_keys: dict[str, str] = {}
    for name, body in raw.items():
        route = _parse_route(str(name), body, defaults)
        if route.incoming_key in seen_keys:
            raise ConfigurationError(
                f"router key of route {route.name!r} is also used by route {seen_keys[route.incoming_key]!r}"
            )
        seen_keys[route.incoming_key] = route.name
        routes.append(route)
    return tuple(routes)


def _default_headers(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        "api_key_header": environment.get("KEYMUX_API_KEY_HEADER", "Authorization"),
        "authorization_scheme": environment.get("KEYMUX_AUTHORIZATION_SCHEME", "Bearer"),
    }


def _parse_route(name: str, body: Any, defaults: Mapping[str, str]) -> RouteDefinition:
    if not isinstance(body, Mapping):
        raise ConfigurationError(f"route {name!r} must be a mapping")

    upstream = body.get("upstream")
    egress = body.get("egress")
    if not isinstance(upstream, Mapping) or not isinstance(egress, Mapping):
        raise ConfigurationError(f"route {name!r} needs both 'upstream' and 'egress' mappings")

    return RouteDefinition(
        name=name,
        incoming_key=_required_text(body, "incoming_key", name),
        upstream=UpstreamDefinition(
            base_url=_validate_base_url(_required_text(upstream, "base_url", name), name),
            api_key=_required_text(upstream, "api_key", name),
            api_key_header=str(upstream.get("api_key_header", defaults["api_key_header"])),
            authorization_scheme=str(
                upstream.get("authorization_scheme", defaults["authorization_scheme"])
            ),
        ),
        egress=EgressDefinition(
            proxy=_optional_text(egress.get("proxy")),
            label=str(egress.get("label") or name),
        ),
    )


def _validate_base_url(value: str, route_name: str) -> str:
    """Reject a base URL that would only fail later, inside a request."""
    base_url = value.rstrip("/")
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigurationError(
            f"route {route_name!r} has base_url {value!r}; expected an absolute http(s) URL"
        )
    return base_url


def _required_text(source: Mapping[str, Any], key: str, route_name: str) -> str:
    value = _optional_text(source.get(key))
    if value is None:
        raise ConfigurationError(f"route {route_name!r} is missing required field {key!r}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
