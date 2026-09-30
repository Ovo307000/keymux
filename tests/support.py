"""Shared test scaffolding: threads for the mock upstream, the fake proxies and
an in-process KeyMux server."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer

import httpx
import uvicorn

from keymux.config import (
    EgressDefinition,
    KeyMuxSettings,
    PoolSettings,
    RouteDefinition,
    ServerSettings,
    TimeoutSettings,
    UpstreamDefinition,
)

SERVER_START_TIMEOUT_SECONDS = 15.0

@dataclass
class ThreadedServer:
    """A uvicorn instance running in its own thread."""

    server: uvicorn.Server
    thread: threading.Thread
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=SERVER_START_TIMEOUT_SECONDS)


def start_keymux(application) -> ThreadedServer:
    """Start a KeyMux app with real sockets so streaming behaves like production."""
    config = uvicorn.Config(
        application, host="127.0.0.1", port=0, log_level="warning",
        lifespan="on", access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("uvicorn exited before it started")
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn did not start in time")

    port = server.servers[0].sockets[0].getsockname()[1]
    return ThreadedServer(server=server, thread=thread, port=port)


@dataclass
class ThreadedHttpServer:
    """A ``http.server`` instance running in its own thread."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=SERVER_START_TIMEOUT_SECONDS)


def start_server(server_factory) -> ThreadedHttpServer:
    """Start an HTTP server built by ``server_factory(address)`` on a free port."""
    server = server_factory(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return ThreadedHttpServer(server=server, thread=thread, port=server.server_address[1])


def build_settings(
    routes: tuple[RouteDefinition, ...],
    *,
    read_timeout_seconds: float = 30.0,
    connect_timeout_seconds: float = 5.0,
) -> KeyMuxSettings:
    return KeyMuxSettings(
        server=ServerSettings(host="127.0.0.1", port=0),
        routes=routes,
        timeouts=TimeoutSettings(
            connect_seconds=connect_timeout_seconds,
            read_seconds=read_timeout_seconds,
            write_seconds=10.0,
            pool_seconds=5.0,
        ),
        pool=PoolSettings(max_connections=32, max_keepalive_connections=8),
    )


def build_route(
    name: str,
    incoming_key: str,
    base_url: str,
    api_key: str,
    *,
    proxy: str | None = None,
    egress_label: str | None = None,
    api_key_header: str = "Authorization",
    authorization_scheme: str = "Bearer",
) -> RouteDefinition:
    return RouteDefinition(
        name=name,
        incoming_key=incoming_key,
        upstream=UpstreamDefinition(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            api_key_header=api_key_header,
            authorization_scheme=authorization_scheme,
        ),
        egress=EgressDefinition(proxy=proxy, label=egress_label or name),
    )


def start_keymux_with(routes: tuple[RouteDefinition, ...], **timeouts) -> ThreadedServer:
    """Start a KeyMux server for the given routes and return its address."""
    from keymux.main import create_app

    return start_keymux(create_app(build_settings(routes, **timeouts)))


def make_client(base_url: str, **kwargs) -> httpx.AsyncClient:
    """A client that ignores the machine's proxy environment variables."""
    return httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=60.0, **kwargs)
