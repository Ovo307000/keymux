"""KeyMux application entry point.

Run with ``python -m keymux.main`` (or ``run.bat``). The service listens on a
single loopback address and forwards ``/v1/*`` to the route selected by the
router key in the request's ``Authorization`` header.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from . import __version__
from .config import KeyMuxSettings, load_settings
from .errors import KeyMuxError
from .proxy import REQUEST_ID_HEADER, ProxyHandler
from .request_logging import configure_logging, log_event
from .routing import RouteRegistry

PROXIED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
DEFAULT_CONFIG_FILENAME = "config.yaml"
DEFAULT_CONFIG_ENVIRONMENT_VARIABLE = "KEYMUX_CONFIG"
DEFAULT_LOG_LEVEL = "info"


def create_app(settings: KeyMuxSettings) -> FastAPI:
    """Build the ASGI application for the given settings."""
    registry = RouteRegistry(settings.routes)
    handler = ProxyHandler(settings, registry)

    application = FastAPI(
        title="KeyMux",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_build_lifespan(settings, handler),
    )
    application.state.settings = settings
    application.state.registry = registry
    application.state.proxy_handler = handler
    application.add_exception_handler(KeyMuxError, _keymux_error_response)
    application.add_api_route("/health", health, methods=["GET"])
    application.add_api_route("/routes", routes_document, methods=["GET"])
    application.add_api_route("/v1", proxy_request, methods=PROXIED_METHODS)
    application.add_api_route("/v1/{upstream_path:path}", proxy_request, methods=PROXIED_METHODS)
    return application


async def health() -> dict[str, str]:
    return {"status": "ok"}


async def routes_document(request: Request) -> dict[str, object]:
    """Route metadata without any credential material."""
    return {"routes": request.app.state.registry.describe()}


async def proxy_request(request: Request) -> Response:
    return await request.app.state.proxy_handler.forward(request)


async def _keymux_error_response(request: Request, error: KeyMuxError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(error.to_response_body(), status_code=error.status_code, headers=headers)


def _build_lifespan(settings: KeyMuxSettings, handler: ProxyHandler):
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await handler.startup()
        log_event(
            "keymux %s listening on %s:%d with %d routes",
            __version__, settings.server.host, settings.server.port, len(settings.routes),
        )
        for route in settings.routes:
            log_event(
                "route %s upstream=%s egress=%s proxy=%s",
                route.name, route.upstream.base_url, route.egress.label,
                route.egress.proxy or "direct",
            )
        try:
            yield
        finally:
            await handler.shutdown()

    return lifespan


def main() -> None:
    config_path = Path(os.environ.get(DEFAULT_CONFIG_ENVIRONMENT_VARIABLE, DEFAULT_CONFIG_FILENAME))
    settings = load_settings(config_path)
    configure_logging(os.environ.get("KEYMUX_LOG_LEVEL", DEFAULT_LOG_LEVEL))

    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
        log_level=DEFAULT_LOG_LEVEL,
        access_log=False,
    )


if __name__ == "__main__":
    main()
