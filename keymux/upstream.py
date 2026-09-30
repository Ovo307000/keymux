"""Per-route HTTP clients and the streaming relay.

Every route owns its own :class:`httpx.AsyncClient` with its own transport,
connection pool and proxy. Sharing a client between routes would share
keep-alive connections, and a reused connection would leave through the wrong
exit IP, so isolation is a correctness requirement rather than a style choice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

import httpx

from .config import PoolSettings, RouteDefinition, TimeoutSettings
from .errors import translate_transport_error

STREAM_CHUNK_SIZE = 8192


class RouteUpstream:
    """One route's private channel to its upstream."""

    def __init__(
        self,
        route: RouteDefinition,
        timeouts: TimeoutSettings,
        pool: PoolSettings,
    ) -> None:
        self._route = route
        self._client = _build_client(route, timeouts, pool)

    @property
    def route(self) -> RouteDefinition:
        return self._route

    async def open_stream(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        content: bytes | None,
    ) -> httpx.Response:
        """Send the request upstream and return the unread streaming response."""
        request = self._client.build_request(
            method=method,
            url=_upstream_url(self._route, path, query),
            headers=headers,
            content=content,
        )
        try:
            return await self._client.send(request, stream=True)
        except httpx.HTTPError as error:
            raise translate_transport_error(error, self._route.egress.label) from error

    async def aclose(self) -> None:
        await self._client.aclose()


async def relay_body(response: httpx.Response) -> AsyncIterator[bytes]:
    """Yield upstream bytes as they arrive, and always release the connection.

    The generator is what Starlette drives, so a client disconnect cancels it
    and the ``finally`` block aborts the in-flight upstream request instead of
    letting it run to completion.
    """
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await _close_quietly(response)


async def _close_quietly(response: httpx.Response) -> None:
    try:
        await response.aclose()
    except Exception:  # noqa: BLE001 - closing during cancellation must never mask the original error
        pass


def _upstream_url(route: RouteDefinition, path: str, query: str) -> str:
    url = f"{route.upstream.base_url}{path}"
    if query:
        return f"{url}?{query}"
    return url


def _build_client(
    route: RouteDefinition,
    timeouts: TimeoutSettings,
    pool: PoolSettings,
) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(
        proxy=route.egress.proxy,
        limits=httpx.Limits(
            max_connections=pool.max_connections,
            max_keepalive_connections=pool.max_keepalive_connections,
        ),
        retries=0,
        trust_env=False,
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(
            connect=timeouts.connect_seconds,
            read=timeouts.read_seconds,
            write=timeouts.write_seconds,
            pool=timeouts.pool_seconds,
        ),
        follow_redirects=False,
        trust_env=False,
    )
