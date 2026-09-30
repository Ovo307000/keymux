"""Measure the public IP behind each configured egress endpoint.

This talks to Clash/Mihomo directly rather than through KeyMux, so it isolates
one question: does every egress endpoint really leave from its own public IP?

Usage::

    python tools/egress_probe.py --config config.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_result import CheckResult  # noqa: E402
from keymux.config import KeyMuxSettings, load_settings  # noqa: E402
from network_probe import looks_like_ipv4, probe_egress_ip_with_retry  # noqa: E402


def check_egress_ips(settings: KeyMuxSettings) -> CheckResult:
    observed = {
        route.egress.label: probe_egress_ip_with_retry(route.egress.proxy)
        for route in settings.routes
    }
    detail = " ".join(f"{label}={address}" for label, address in observed.items())
    addresses = set(observed.values())
    if not all(looks_like_ipv4(address) for address in addresses):
        return CheckResult("egress public IPs", False, f"{detail}  <-- a probe returned no IP")
    if len(addresses) != len(observed):
        return CheckResult("egress public IPs", False, f"{detail}  <-- exit IPs are not distinct")
    return CheckResult("egress public IPs", True, detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml")
    )
    arguments = parser.parse_args()
    result = check_egress_ips(load_settings(arguments.config))
    print(f"[{'PASS' if result.passed else 'FAIL'}] {result.name}  {result.detail}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
