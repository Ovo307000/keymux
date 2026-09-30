"""Configuration loading: environment expansion, validation, and safety rails."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from keymux.config import ConfigurationError, load_settings

ENVIRONMENT = {
    "ROUTER_KEY_A": "router-key-a",
    "ROUTER_KEY_B": "router-key-b",
    "REAL_API_KEY_A": "sk-real-a",
    "REAL_API_KEY_B": "sk-real-b",
}

CONFIGURATION = """
server:
  host: 127.0.0.1
  port: 9000
routes:
  account-a:
    incoming_key: ${ROUTER_KEY_A}
    upstream:
      base_url: https://api.example.com
      api_key: ${REAL_API_KEY_A}
    egress:
      proxy: http://127.0.0.1:7893
      label: node-a
  account-b:
    incoming_key: ${ROUTER_KEY_B}
    upstream:
      base_url: https://api.example.com/
      api_key: ${REAL_API_KEY_B}
    egress:
      proxy: http://127.0.0.1:7894
"""


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def write(self, text: str) -> Path:
        path = self.directory / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_expands_environment_variables_and_trims_base_url(self) -> None:
        settings = load_settings(self.write(CONFIGURATION), ENVIRONMENT)

        self.assertEqual(settings.server.host, "127.0.0.1")
        self.assertEqual(settings.server.port, 9000)
        self.assertEqual(len(settings.routes), 2)
        first, second = settings.routes
        self.assertEqual(first.upstream.api_key, "sk-real-a")
        self.assertEqual(first.egress.proxy, "http://127.0.0.1:7893")
        self.assertEqual(first.egress.label, "node-a")
        # A trailing slash would double up when the request path is appended.
        self.assertEqual(second.upstream.base_url, "https://api.example.com")
        # Without an explicit label the route name is used for logging.
        self.assertEqual(second.egress.label, "account-b")

    def test_missing_environment_variable_is_reported_by_name(self) -> None:
        incomplete = {key: value for key, value in ENVIRONMENT.items() if key != "REAL_API_KEY_B"}

        with self.assertRaises(ConfigurationError) as raised:
            load_settings(self.write(CONFIGURATION), incomplete)

        self.assertIn("REAL_API_KEY_B", str(raised.exception))

    def test_duplicate_router_keys_are_rejected(self) -> None:
        duplicated = CONFIGURATION.replace("${ROUTER_KEY_B}", "${ROUTER_KEY_A}")

        with self.assertRaises(ConfigurationError) as raised:
            load_settings(self.write(duplicated), ENVIRONMENT)

        self.assertIn("also used by route", str(raised.exception))

    def test_non_loopback_host_needs_explicit_opt_in(self) -> None:
        exposed = CONFIGURATION.replace("host: 127.0.0.1", "host: 0.0.0.0")

        with self.assertRaises(ConfigurationError) as raised:
            load_settings(self.write(exposed), ENVIRONMENT)

        self.assertIn("allow_non_loopback", str(raised.exception))

    def test_non_loopback_host_is_allowed_when_opted_in(self) -> None:
        opted_in = CONFIGURATION.replace(
            "host: 127.0.0.1", "host: 0.0.0.0\n  allow_non_loopback: true"
        )

        self.assertEqual(load_settings(self.write(opted_in), ENVIRONMENT).server.host, "0.0.0.0")

    def test_empty_routes_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings(self.write("routes: {}\n"), ENVIRONMENT)

    def test_route_without_upstream_is_rejected(self) -> None:
        broken = """
routes:
  account-a:
    incoming_key: ${ROUTER_KEY_A}
    egress:
      proxy: http://127.0.0.1:7893
"""
        with self.assertRaises(ConfigurationError) as raised:
            load_settings(self.write(broken), ENVIRONMENT)

        self.assertIn("upstream", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
