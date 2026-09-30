"""End-to-end verification of a running KeyMux.

Checks, in order:

1. ``GET /health``
2. ``GET /routes`` exposes both routes and no credentials
3. every configured egress proxy reports a public IP, and they differ
4. every route answers on the endpoint it declares in its ``verify`` block
5. a streaming completion where the route declares one, arriving incrementally
6. upstream errors pass through unchanged, and KeyMux answers 401 on its own
7. concurrent requests never produce a KeyMux-side failure
8. router key -> real key + exit IP, proven with httpbin (see ``egress_proof``)

Usage::

    python tools/verify_routes.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import egress_proof  # noqa: E402

from check_result import CheckResult  # noqa: E402
from route_checks import (  # noqa: E402
    check_concurrency,
    check_error_handling,
    check_health,
    check_routes_answer,
    check_routes_document,
    check_streaming,
    check_upstream_error_passthrough,
)
from egress_probe import check_egress_ips  # noqa: E402
from keymux.config import load_settings  # noqa: E402
from keymux.settings import KeyMuxSettings  # noqa: E402

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "http://127.0.0.1:9000"
HTTP_TIMEOUT_SECONDS = 90.0


def main() -> int:
    arguments = parse_arguments()
    try:
        settings = load_settings(arguments.config)
    except Exception as error:  # noqa: BLE001 - the message is the whole point here
        print(f"cannot load {arguments.config}: {error}", file=sys.stderr)
        return 2
    return asyncio.run(run(arguments, settings))


async def run(arguments: argparse.Namespace, settings: KeyMuxSettings) -> int:
    results = [check_egress_ips(settings)]
    async with httpx.AsyncClient(
        base_url=arguments.base_url, trust_env=False, timeout=HTTP_TIMEOUT_SECONDS
    ) as client:
        results.append(await check_health(client))
        results.append(await check_routes_document(client, settings))
        results.append(await check_routes_answer(client, settings, arguments.model))
        results.append(await check_streaming(client, settings, arguments.model))
        results.append(await check_error_handling(client, settings, arguments.model))
        results.append(await check_upstream_error_passthrough(client, settings))
        results.append(await check_concurrency(client, settings, arguments.model))

    if not arguments.skip_egress_proof:
        results.append(await egress_proof.run(settings))
    return report(results)


def report(results: list[CheckResult]) -> int:
    width = max(len(result.name) for result in results)
    for result in results:
        marker = "PASS" if result.passed else "FAIL"
        print(f"[{marker}] {result.name.ljust(width)}  {result.detail}")
    failures = [result for result in results if not result.passed]
    print()
    print(f"{len(results) - len(failures)}/{len(results)} checks passed")
    return 0 if not failures else 1


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--skip-egress-proof", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
