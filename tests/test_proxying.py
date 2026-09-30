"""End-to-end behaviour of the KeyMux hop, against a real mock upstream."""

from __future__ import annotations

import logging
import re
import unittest

from keymux.main import create_app

from tests.mock_upstream import UpstreamState, start_upstream
from tests.support import (
    build_route,
    build_settings,
    make_client,
    start_keymux,
    start_keymux_with,
)

ROUTER_KEY_A = "router-key-a"
ROUTER_KEY_B = "router-key-b"
REAL_KEY_A = "sk-real-key-a"
REAL_KEY_B = "sk-real-key-b"

PRESERVED_STATUS_CODES = (400, 401, 403, 429, 500, 503)


class ProxyingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream_state = UpstreamState()
        cls.upstream = start_upstream(cls.upstream_state)
        application = create_app(
            build_settings(
                (
                    build_route("account-a", ROUTER_KEY_A, cls.upstream.base_url, REAL_KEY_A,
                                egress_label="node-a"),
                    build_route("account-b", ROUTER_KEY_B, cls.upstream.base_url, REAL_KEY_B,
                                egress_label="node-b"),
                )
            )
        )
        cls.keymux = start_keymux(application)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.keymux.stop()
        cls.upstream.stop()

    async def asyncSetUp(self) -> None:
        self.client = make_client(self.keymux.base_url)
        self.addAsyncCleanup(self.client.aclose)

    async def test_missing_router_key_is_401(self) -> None:
        response = await self.client.post("/v1/chat/completions", json={"model": "test"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "missing_api_key")

    async def test_unknown_router_key_is_401(self) -> None:
        response = await self.client.post(
            "/v1/chat/completions",
            json={"model": "test"},
            headers={"Authorization": "Bearer not-a-route-key"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_api_key")

    async def test_non_ascii_router_key_is_401_not_500(self) -> None:
        # Header values are bytes on the wire and uvicorn decodes them as
        # latin-1, so a non-ASCII router key can reach the comparison as a
        # non-ASCII str. secrets.compare_digest raises TypeError on those, so
        # the comparison must go through bytes or a client can force a 500.
        response = await self.client.post(
            "/v1/chat/completions",
            json={"model": "test"},
            headers={"Authorization": b"Bearer caf\xe9"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_api_key")

    async def test_router_key_is_replaced_by_the_real_upstream_key(self) -> None:
        response = await self.client.post(
            "/v1/echo",
            json={"probe": True},
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
        )

        self.assertEqual(response.status_code, 200)
        echoed = response.json()
        self.assertEqual(echoed["authorization"], f"Bearer {REAL_KEY_A}")
        self.assertNotIn(ROUTER_KEY_A, response.text)

    async def test_second_route_uses_its_own_real_key(self) -> None:
        response = await self.client.post(
            "/v1/echo",
            json={"probe": True},
            headers={"Authorization": f"Bearer {ROUTER_KEY_B}"},
        )

        self.assertEqual(response.json()["authorization"], f"Bearer {REAL_KEY_B}")

    async def test_x_api_key_router_key_is_consumed_and_not_forwarded(self) -> None:
        response = await self.client.post(
            "/v1/echo",
            json={"probe": True},
            headers={"x-api-key": ROUTER_KEY_B},
        )

        echoed = response.json()
        self.assertEqual(echoed["authorization"], f"Bearer {REAL_KEY_B}")
        self.assertIsNone(echoed["x_api_key"])

    async def test_method_path_query_and_body_are_preserved(self) -> None:
        payload = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        response = await self.client.post(
            "/v1/echo?alpha=1&beta=two",
            json=payload,
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
        )

        echoed = response.json()
        self.assertEqual(echoed["method"], "POST")
        self.assertEqual(echoed["path"], "/v1/echo")
        self.assertEqual(echoed["query"], "alpha=1&beta=two")
        self.assertEqual(echoed["body"], '{"model":"test","messages":[{"role":"user","content":"hi"}]}')

    async def test_client_headers_reach_the_upstream(self) -> None:
        response = await self.client.get(
            "/v1/request-headers",
            headers={
                "Authorization": f"Bearer {ROUTER_KEY_A}",
                "x-trace-note": "keep-me",
                "accept-encoding": "identity",
            },
        )

        received = response.json()
        self.assertEqual(received["x-trace-note"], "keep-me")
        self.assertEqual(received["authorization"], f"Bearer {REAL_KEY_A}")
        # httpx negotiates its own coding because it transparently decodes.
        self.assertNotEqual(received.get("accept-encoding"), "identity")

    async def test_hop_by_hop_headers_do_not_reach_the_upstream(self) -> None:
        distinctive = "x-keymux-hop-by-hop-test"
        response = await self.client.get(
            "/v1/request-headers",
            headers={
                "Authorization": f"Bearer {ROUTER_KEY_A}",
                "connection": distinctive,
                "proxy-connection": distinctive,
                "proxy-authorization": "Basic c2VjcmV0",
            },
        )

        received = response.json()
        for name in ("connection", "proxy-connection", "proxy-authorization"):
            self.assertNotEqual(received.get(name), distinctive, name)
        self.assertNotIn("proxy-connection", received)
        self.assertNotIn("proxy-authorization", received)

    async def test_upstream_status_body_and_content_type_are_preserved(self) -> None:
        for status_code in PRESERVED_STATUS_CODES:
            with self.subTest(status_code=status_code):
                response = await self.client.get(
                    f"/v1/status/{status_code}",
                    headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
                )

                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.headers["content-type"], "application/json")
                self.assertEqual(response.json()["error"]["code"], f"mock_{status_code}")

    async def test_upstream_response_headers_are_preserved(self) -> None:
        response = await self.client.get(
            "/v1/status/429",
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
        )

        self.assertEqual(response.headers["retry-after"], "7")

    async def test_every_response_carries_a_request_id(self) -> None:
        response = await self.client.post(
            "/v1/echo",
            json={},
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
        )

        self.assertTrue(response.headers["x-request-id"])

    async def test_client_supplied_request_id_is_kept(self) -> None:
        response = await self.client.post(
            "/v1/echo",
            json={},
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}", "x-request-id": "trace-42"},
        )

        self.assertEqual(response.headers["x-request-id"], "trace-42")


if __name__ == "__main__":
    unittest.main()
