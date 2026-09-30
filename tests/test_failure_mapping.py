"""Failures that happen before an upstream response exists keep a real code."""

from __future__ import annotations

import unittest

from tests.mock_upstream import UpstreamState, start_upstream
from tests.support import build_route, make_client, start_keymux_with


class FailureMappingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.upstream_state = UpstreamState()
        self.upstream = start_upstream(self.upstream_state)
        self.addCleanup(self.upstream.stop)

    async def test_upstream_connection_refused_is_502(self) -> None:
        server = start_keymux_offline()
        self.addCleanup(server.stop)
        client = make_client(server.base_url)
        self.addAsyncCleanup(client.aclose)

        response = await client.get("/v1/models", headers={"Authorization": "Bearer router-key-a"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "upstream_connection_failed")

    async def test_failure_message_does_not_embed_urls_or_credentials(self) -> None:
        server = start_keymux_offline()
        self.addCleanup(server.stop)
        client = make_client(server.base_url)
        self.addAsyncCleanup(client.aclose)

        response = await client.get("/v1/models", headers={"Authorization": "Bearer router-key-a"})

        # httpx error text carries the request URL and, behind a proxy, the
        # proxy URL, so it must not be echoed to the caller.
        self.assertNotIn("127.0.0.1:1", response.text)
        self.assertNotIn("router-key-a", response.text)
        self.assertNotIn("sk-real-a", response.text)

    async def test_unreachable_egress_proxy_is_502(self) -> None:
        server = start_keymux_offline(proxy="http://127.0.0.1:1")
        self.addCleanup(server.stop)
        client = make_client(server.base_url)
        self.addAsyncCleanup(client.aclose)

        response = await client.get("/v1/models", headers={"Authorization": "Bearer router-key-a"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "upstream_connection_failed")

    async def test_upstream_read_timeout_is_504(self) -> None:
        routes = (
            build_route("account-a", "router-key-a", self.upstream.base_url, "sk-real-a"),
        )
        server = start_keymux_with(routes, read_timeout_seconds=0.5)
        self.addCleanup(server.stop)
        client = make_client(server.base_url)
        self.addAsyncCleanup(client.aclose)

        response = await client.get("/v1/slow", headers={"Authorization": "Bearer router-key-a"})

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "upstream_timeout")


def start_keymux_offline(proxy: str | None = None):
    """KeyMux whose upstream is a dead port, optionally behind a dead proxy."""
    routes = (build_route("account-a", "router-key-a", "http://127.0.0.1:1", "sk-real-a", proxy=proxy),)
    return start_keymux_with(routes)


if __name__ == "__main__":
    unittest.main()
