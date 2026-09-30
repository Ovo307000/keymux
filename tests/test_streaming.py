"""Streaming: incremental relay, and aborting the upstream on client disconnect."""

from __future__ import annotations

import asyncio
import time
import unittest

from keymux.main import create_app

from tests.mock_upstream import UpstreamState, start_upstream
from tests.support import (
    build_route,
    build_settings,
    make_client,
    start_keymux,
)

ROUTER_KEY_A = "router-key-a"
REAL_KEY_A = "sk-real-key-a"
CLIENT_DISCONNECT_GRACE_SECONDS = 4.0


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream_state = UpstreamState()
        cls.upstream = start_upstream(cls.upstream_state)
        cls.keymux = start_keymux(
            create_app(
                build_settings(
                    (
                        build_route("account-a", ROUTER_KEY_A, cls.upstream.base_url, REAL_KEY_A,
                                    egress_label="node-a"),
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

    async def test_sse_events_are_relayed_as_they_arrive(self) -> None:
        arrival_times: list[float] = []
        chunks: list[bytes] = []

        async with self.client.stream(
            "POST",
            "/v1/stream",
            json={"model": "test", "stream": True},
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                arrival_times.append(time.perf_counter())

        body = b"".join(chunks)
        self.assertIn(b'"index": 0', body)
        self.assertIn(b'"index": 3', body)
        self.assertIn(b"[DONE]", body)
        # Four events are written 0.25s apart upstream. If KeyMux buffered the
        # response, every byte would arrive in one burst instead.
        self.assertGreater(arrival_times[-1] - arrival_times[0], 0.4)
        self.assertGreater(len(arrival_times), 2)

    async def test_client_disconnect_aborts_the_upstream_stream(self) -> None:
        async with self.client.stream(
            "POST",
            "/v1/stream-long",
            json={"model": "test", "stream": True},
            headers={"Authorization": f"Bearer {ROUTER_KEY_A}"},
        ) as response:
            async for _ in response.aiter_bytes():
                break

        await self._wait_for_upstream_abort()

    async def test_client_disconnect_while_waiting_for_upstream_headers_aborts_it(self) -> None:
        # A raw socket, because httpx blocks on the response head: the point is
        # to hang up while KeyMux is still waiting for the upstream to answer.
        writer = await _open_raw_request(
            self.keymux.port, f"GET /v1/slow HTTP/1.1\r\nHost: keymux\r\n"
            f"Authorization: Bearer {ROUTER_KEY_A}\r\nConnection: close\r\n\r\n"
        )
        await asyncio.sleep(0.4)
        writer.close()
        await writer.wait_closed()

        await self._wait_for_upstream_abort()

    async def _wait_for_upstream_abort(self) -> None:
        deadline = time.monotonic() + CLIENT_DISCONNECT_GRACE_SECONDS
        while time.monotonic() < deadline:
            with self.upstream_state.lock:
                if self.upstream_state.client_went_away:
                    return
            await asyncio.sleep(0.1)
        self.fail("KeyMux kept reading the upstream after the client disconnected")


async def _open_raw_request(port: int, raw_request: str):
    _, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw_request.encode("utf-8"))
    await writer.drain()
    return writer


if __name__ == "__main__":
    unittest.main()
