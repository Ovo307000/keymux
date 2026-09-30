"""Egress isolation: each router key must leave through its own proxy endpoint."""

from __future__ import annotations

import asyncio
import unittest

from keymux.main import create_app

from tests.fake_proxy import ProxyRecorder, start_proxy
from tests.support import (
    build_route,
    build_settings,
    make_client,
    start_keymux,
)

ROUTER_KEY_A = "router-key-a"
ROUTER_KEY_B = "router-key-b"
REAL_KEY_A = "sk-real-key-a"
REAL_KEY_B = "sk-real-key-b"
UPSTREAM_BASE_URL = "http://upstream.invalid"
CONCURRENT_REQUESTS_PER_ROUTE = 12


class EgressIsolationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.recorder_a = ProxyRecorder("proxy-a")
        cls.recorder_b = ProxyRecorder("proxy-b")
        cls.proxy_a = start_proxy(cls.recorder_a)
        cls.proxy_b = start_proxy(cls.recorder_b)
        cls.application = create_app(
            build_settings(
                (
                    build_route("account-a", ROUTER_KEY_A, UPSTREAM_BASE_URL, REAL_KEY_A,
                                proxy=cls.proxy_a.base_url, egress_label="node-a"),
                    build_route("account-b", ROUTER_KEY_B, UPSTREAM_BASE_URL, REAL_KEY_B,
                                proxy=cls.proxy_b.base_url, egress_label="node-b"),
                )
            )
        )
        cls.keymux = start_keymux(cls.application)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.keymux.stop()
        cls.proxy_a.stop()
        cls.proxy_b.stop()

    async def asyncSetUp(self) -> None:
        self.client = make_client(self.keymux.base_url)
        self.addAsyncCleanup(self.client.aclose)

    async def test_each_router_key_leaves_through_its_own_proxy(self) -> None:
        response_a = await self._call(ROUTER_KEY_A)
        response_b = await self._call(ROUTER_KEY_B)

        self.assertEqual(response_a.json()["served_by_proxy"], "proxy-a")
        self.assertEqual(response_b.json()["served_by_proxy"], "proxy-b")

    async def test_proxy_receives_the_real_key_and_the_full_target_url(self) -> None:
        await self._call(ROUTER_KEY_A)

        recorded = self.recorder_a.snapshot()[-1]
        self.assertTrue(recorded["target"].startswith(f"{UPSTREAM_BASE_URL}/v1/echo"))
        self.assertEqual(recorded["headers"]["authorization"], f"Bearer {REAL_KEY_A}")
        self.assertNotIn(ROUTER_KEY_A, repr(recorded))

    async def test_concurrent_requests_never_cross_egress(self) -> None:
        calls = [self._call(ROUTER_KEY_A) for _ in range(CONCURRENT_REQUESTS_PER_ROUTE)]
        calls += [self._call(ROUTER_KEY_B) for _ in range(CONCURRENT_REQUESTS_PER_ROUTE)]
        responses = await asyncio.gather(*calls)

        served_by = [response.json()["served_by_proxy"] for response in responses]
        expected = ["proxy-a"] * CONCURRENT_REQUESTS_PER_ROUTE + ["proxy-b"] * CONCURRENT_REQUESTS_PER_ROUTE
        self.assertEqual(served_by, expected)

    async def test_each_route_keeps_its_own_connection_pool(self) -> None:
        handler = self.application.state.proxy_handler

        clients = {name: upstream for name, upstream in handler._upstreams.items()}
        self.assertIsNot(clients["account-a"]._client, clients["account-b"]._client)
        self.assertIsNot(
            clients["account-a"]._client._transport,
            clients["account-b"]._client._transport,
        )

    async def _call(self, router_key: str):
        return await self.client.post(
            "/v1/echo",
            json={"probe": True},
            headers={"Authorization": f"Bearer {router_key}"},
        )


if __name__ == "__main__":
    unittest.main()
