"""The non-proxying endpoints: health, routes document and unknown paths."""

from __future__ import annotations

import unittest

from keymux.main import create_app

from tests.mock_upstream import UpstreamState, start_upstream
from tests.support import build_route, build_settings, make_client, start_keymux

ROUTER_KEY_A = "router-key-a"
ROUTER_KEY_B = "router-key-b"
REAL_KEY_A = "sk-real-key-a"
REAL_KEY_B = "sk-real-key-b"


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream_state = UpstreamState()
        cls.upstream = start_upstream(cls.upstream_state)
        cls.keymux = start_keymux(
            create_app(
                build_settings(
                    (
                        build_route("account-a", ROUTER_KEY_A, cls.upstream.base_url, REAL_KEY_A,
                                    proxy="http://127.0.0.1:7893", egress_label="node-a"),
                        build_route("account-b", ROUTER_KEY_B, cls.upstream.base_url, REAL_KEY_B,
                                    proxy="http://127.0.0.1:7894", egress_label="node-b"),
                    )
                )
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.keymux.stop()
        cls.upstream.stop()

    async def asyncSetUp(self) -> None:
        self.client = make_client(self.keymux.base_url)
        self.addAsyncCleanup(self.client.aclose)

    async def test_health_is_ok(self) -> None:
        response = await self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_routes_lists_both_routes_without_secrets(self) -> None:
        response = await self.client.get("/routes")

        body = response.text
        self.assertEqual(response.status_code, 200)
        self.assertIn("account-a", body)
        self.assertIn("account-b", body)
        self.assertIn("http://127.0.0.1:7893", body)
        for secret in (ROUTER_KEY_A, ROUTER_KEY_B, REAL_KEY_A, REAL_KEY_B):
            self.assertNotIn(secret, body)

    async def test_unknown_path_is_404(self) -> None:
        response = await self.client.get("/not-v1")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
