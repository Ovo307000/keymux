"""Log hygiene: the log stays useful without ever carrying credential material."""

from __future__ import annotations

import logging
import re
import unittest

from keymux.main import create_app

from tests.mock_upstream import UpstreamState, start_upstream
from tests.support import build_route, build_settings, make_client, start_keymux

ROUTER_KEY_A = "router-key-a"
ROUTER_KEY_B = "router-key-b"
REAL_KEY_A = "sk-real-key-a"
REAL_KEY_B = "sk-real-key-b"
DEAD_UPSTREAM = "http://127.0.0.1:1"


class _LineCollector(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record.getMessage())


class LogHygieneTests(unittest.IsolatedAsyncioTestCase):
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
                        build_route("account-b", ROUTER_KEY_B, DEAD_UPSTREAM, REAL_KEY_B,
                                    egress_label="node-b"),
                    )
                )
            )
        )
        cls.lines: list[str] = []
        cls.logger = logging.getLogger("keymux")
        cls.original_level = cls.logger.level
        cls.collector = _LineCollector(cls.lines)
        cls.logger.addHandler(cls.collector)
        cls.logger.setLevel(logging.INFO)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.logger.removeHandler(cls.collector)
        cls.logger.setLevel(cls.original_level)
        cls.keymux.stop()
        cls.upstream.stop()

    async def asyncSetUp(self) -> None:
        self.client = make_client(self.keymux.base_url)
        self.addAsyncCleanup(self.client.aclose)
        self.lines.clear()

    async def test_401_200_and_502_lines_are_emitted_without_credentials(self) -> None:
        await self.client.post("/v1/echo", json={})
        await self.client.post("/v1/echo", json={},
                               headers={"Authorization": f"Bearer {ROUTER_KEY_A}"})
        await self.client.post("/v1/echo", json={},
                               headers={"Authorization": f"Bearer {ROUTER_KEY_B}"})

        joined = "\n".join(self.lines)
        self.assertIn("status=401", joined)
        self.assertIn("status=200", joined)
        self.assertIn("status=502", joined)
        for secret in (ROUTER_KEY_A, ROUTER_KEY_B, REAL_KEY_A, REAL_KEY_B):
            self.assertNotIn(secret, joined)
        # No credential value, in any scheme, ever appears in a log line.
        self.assertIsNone(re.search(r"Bearer\s+\S{16,}", joined))
        self.assertIsNone(re.search(r"\bsk-[A-Za-z0-9_-]{16,}", joined))
        # The failure line still says which egress failed, which is what debugging needs.
        self.assertIn("egress=node-b", joined)

    async def test_the_log_line_carries_the_route_the_status_and_the_latency(self) -> None:
        await self.client.post("/v1/echo", json={},
                               headers={"Authorization": f"Bearer {ROUTER_KEY_A}"})

        line = self.lines[-1]
        for field in ("request_id=", "route=account-a", "method=POST", "path=/v1/echo",
                      "status=200", "streaming=false", "egress=node-a", "latency="):
            self.assertIn(field, line)


if __name__ == "__main__":
    unittest.main()
