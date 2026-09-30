"""The proxying path: identify the route, forward the request, relay the answer."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .config import KeyMuxSettings, RouteDefinition
from .errors import KeyMuxError
from .headers import build_upstream_headers, filter_response_headers
from .request_logging import RequestLogEntry, log_request
from .routing import RouteRegistry
from .upstream import RouteUpstream, relay_body

REQUEST_ID_HEADER = "x-request-id"
ROUTER_KEY_HEADERS = ("authorization", "x-api-key")
EVENT_STREAM_CONTENT_TYPE = "text/event-stream"


class ProxyHandler:
    """Owns the per-route upstream clients and performs the relay."""

    def __init__(self, settings: KeyMuxSettings, registry: RouteRegistry) -> None:
        self._settings = settings
        self._registry = registry
        self._upstreams: dict[str, RouteUpstream] = {}

    async def startup(self) -> None:
        self._upstreams = {
            route.name: RouteUpstream(route, self._settings.timeouts, self._settings.pool)
            for route in self._settings.routes
        }

    async def shutdown(self) -> None:
        for upstream in self._upstreams.values():
            await upstream.aclose()
        self._upstreams.clear()

    async def forward(self, request: Request) -> Response:
        started_at = time.perf_counter()
        request_id = resolve_request_id(request)
        request.state.request_id = request_id

        route = self._resolve_route(request, request_id, started_at)
        headers = build_upstream_headers(request.headers, route)
        content = await request.body()
        upstream_response = await self._open_stream(
            request, route, headers, content, request_id, started_at
        )
        return self._relay(request, request_id, route, upstream_response, started_at)

    def _resolve_route(self, request: Request, request_id: str, started_at: float) -> RouteDefinition:
        try:
            return self._registry.resolve_from_headers(
                request.headers.get(ROUTER_KEY_HEADERS[0]),
                request.headers.get(ROUTER_KEY_HEADERS[1]),
            )
        except KeyMuxError as error:
            _log_outcome(
                request=request, request_id=request_id, route_name="-",
                status_code=error.status_code, is_streaming=False, egress_label="-",
                started_at=started_at, error=error,
            )
            raise

    async def _open_stream(
        self,
        request: Request,
        route: RouteDefinition,
        headers: Mapping[str, str],
        content: bytes,
        request_id: str,
        started_at: float,
    ) -> httpx.Response:
        upstream = self._upstreams[route.name]
        try:
            return await upstream.open_stream(
                method=request.method,
                path=request.url.path,
                query=request.url.query,
                headers=headers,
                content=content or None,
            )
        except KeyMuxError as error:
            _log_outcome(
                request=request, request_id=request_id, route_name=route.name,
                status_code=error.status_code, is_streaming=False,
                egress_label=route.egress.label, started_at=started_at, error=error,
            )
            raise

    def _relay(
        self,
        request: Request,
        request_id: str,
        route: RouteDefinition,
        upstream_response: httpx.Response,
        started_at: float,
    ) -> Response:
        content_type = upstream_response.headers.get("content-type", "").lower()
        is_streaming = EVENT_STREAM_CONTENT_TYPE in content_type
        response_headers = filter_response_headers(upstream_response.headers)
        response_headers[REQUEST_ID_HEADER] = request_id
        if is_streaming:
            response_headers.setdefault("cache-control", "no-cache")
            response_headers.setdefault("x-accel-buffering", "no")
        _log_outcome(
            request=request, request_id=request_id, route_name=route.name,
            status_code=upstream_response.status_code, is_streaming=is_streaming,
            egress_label=route.egress.label, started_at=started_at,
        )
        return StreamingResponse(
            relay_body(upstream_response),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )


def resolve_request_id(request: Request) -> str:
    provided = request.headers.get(REQUEST_ID_HEADER)
    if provided and provided.strip():
        return provided.strip()
    return uuid.uuid4().hex[:16]


def _log_outcome(
    *,
    request: Request,
    request_id: str,
    route_name: str,
    status_code: int,
    is_streaming: bool,
    egress_label: str,
    started_at: float,
    error: KeyMuxError | None = None,
) -> None:
    log_request(
        RequestLogEntry(
            request_id=request_id,
            route=route_name,
            method=request.method,
            path=request.url.path,
            status=status_code,
            streaming=is_streaming,
            egress=egress_label,
            latency_milliseconds=int((time.perf_counter() - started_at) * 1000),
            error=_single_line(error.message) if error else None,
        )
    )


def _single_line(message: str) -> str:
    return " ".join(message.split())
