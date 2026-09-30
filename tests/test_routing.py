"""Router key resolution and the shape of the public /routes document."""

from __future__ import annotations

import unittest

from keymux.errors import InvalidApiKeyError, MissingApiKeyError
from keymux.routing import RouteRegistry

from tests.support import build_route

ROUTES = (
    build_route("account-a", "kmx_a_secret-a", "https://api.example.com", "sk-real-a",
                proxy="http://127.0.0.1:7893", egress_label="node-a"),
    build_route("account-b", "kmx_b_secret-b", "https://api.example.com", "sk-real-b",
                proxy="http://127.0.0.1:7894", egress_label="node-b"),
)


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RouteRegistry(ROUTES)

    def test_bearer_scheme_selects_the_matching_route(self) -> None:
        route = self.registry.resolve_from_headers("Bearer kmx_b_secret-b", None)
        self.assertEqual(route.name, "account-b")

    def test_scheme_is_matched_case_insensitively(self) -> None:
        route = self.registry.resolve_from_headers("bearer kmx_a_secret-a", None)
        self.assertEqual(route.name, "account-a")

    def test_raw_token_without_scheme_is_accepted(self) -> None:
        route = self.registry.resolve_from_headers("kmx_a_secret-a", None)
        self.assertEqual(route.name, "account-a")

    def test_x_api_key_is_accepted_as_a_fallback(self) -> None:
        route = self.registry.resolve_from_headers(None, "kmx_b_secret-b")
        self.assertEqual(route.name, "account-b")

    def test_authorization_wins_over_x_api_key(self) -> None:
        route = self.registry.resolve_from_headers("Bearer kmx_a_secret-a", "kmx_b_secret-b")
        self.assertEqual(route.name, "account-a")

    def test_missing_router_key_is_a_401(self) -> None:
        with self.assertRaises(MissingApiKeyError) as raised:
            self.registry.resolve_from_headers(None, None)
        self.assertEqual(raised.exception.status_code, 401)

    def test_unknown_router_key_is_a_401(self) -> None:
        with self.assertRaises(InvalidApiKeyError) as raised:
            self.registry.resolve_from_headers("Bearer kmx_a_secret-z", None)
        self.assertEqual(raised.exception.status_code, 401)

    def test_a_prefix_of_a_router_key_is_not_accepted(self) -> None:
        with self.assertRaises(InvalidApiKeyError):
            self.registry.resolve_from_headers("Bearer kmx_a_secret-", None)

    def test_non_ascii_router_key_is_rejected_instead_of_erroring(self) -> None:
        # secrets.compare_digest raises TypeError on non-ASCII str, so the
        # comparison must go through bytes or any client can force a 500.
        for presented in ("café", "kmx_a_secret-a\u00e9", "\u4e2d\u6587"):
            with self.subTest(presented=presented):
                with self.assertRaises(InvalidApiKeyError):
                    self.registry.resolve_from_headers(f"Bearer {presented}", None)

    def test_describe_never_exposes_credentials(self) -> None:
        rendered = repr(self.registry.describe())

        self.assertIn("account-a", rendered)
        self.assertIn("http://127.0.0.1:7893", rendered)
        self.assertNotIn("kmx_a_secret-a", rendered)
        self.assertNotIn("kmx_b_secret-b", rendered)
        self.assertNotIn("sk-real-a", rendered)
        self.assertNotIn("sk-real-b", rendered)


if __name__ == "__main__":
    unittest.main()
