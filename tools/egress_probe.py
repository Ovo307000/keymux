"""Measure the public IP behind each configured egress endpoint.

This talks to Clash/Mihomo directly rather than through KeyMux, so it isolates
one question: does every egress endpoint really leave from its own public IP?

Usage::

    python tools/egress_probe.py --config config.yaml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_result import CheckResult  # noqa: E402
from keymux.config import KeyMuxSettings, load_settings  # noqa: E402

PROBE_URL = "https://api.ipify.org"
PROBE_TIMEOUT_SECONDS = 25
PROBE_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


def check_egress_ips(settings: KeyMuxSettings) -> CheckResult:
    observed = {
        route.egress.label: _probe_with_retry(route.egress.proxy) for route in settings.routes
    }
    detail = " ".join(f"{label}={address}" for label, address in observed.items())
    addresses = set(observed.values())
    if not all(looks_like_ipv4(address) for address in addresses):
        return CheckResult("egress public IPs", False, f"{detail}  <-- a probe returned no IP")
    if len(addresses) != len(observed):
        return CheckResult("egress public IPs", False, f"{detail}  <-- exit IPs are not distinct")
    return CheckResult("egress public IPs", True, detail)


def looks_like_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def probe_egress_ip(proxy: str | None) -> str:
    command = ["curl.exe", "-s", "--max-time", str(PROBE_TIMEOUT_SECONDS)]
    if proxy:
        command += ["-x", proxy]
    command.append(PROBE_URL)
    process = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return (process.stdout or "").strip() or f"<probe failed: {process.stderr.strip()[:60]}>"


def _probe_with_retry(proxy: str | None, attempts: int = PROBE_ATTEMPTS) -> str:
    last = ""
    for _ in range(attempts):
        last = probe_egress_ip(proxy)
        if looks_like_ipv4(last):
            return last
        time.sleep(RETRY_DELAY_SECONDS)
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    arguments = parser.parse_args()
    result = check_egress_ips(load_settings(arguments.config))
    print(f"[{'PASS' if result.passed else 'FAIL'}] {result.name}  {result.detail}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
