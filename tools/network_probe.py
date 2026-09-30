"""Ask the internet what public IP it sees, optionally through a proxy.

Shared by the egress-IP check and by the Clash listener tool, so the definition
of "the probe failed" lives in exactly one place.
"""

from __future__ import annotations

import subprocess
import time

PROBE_URL = "https://api.ipify.org"
PROBE_TIMEOUT_SECONDS = 25
PROBE_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


def looks_like_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def probe_egress_ip(proxy: str | None) -> str:
    """Return the public IPv4 address seen through ``proxy``, or a failure note."""
    command = ["curl.exe", "-s", "--max-time", str(PROBE_TIMEOUT_SECONDS)]
    if proxy:
        command += ["-x", proxy]
    command.append(PROBE_URL)
    process = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return (process.stdout or "").strip() or f"<probe failed: {process.stderr.strip()[:60]}>"


def probe_egress_ip_with_retry(proxy: str | None, attempts: int = PROBE_ATTEMPTS) -> str:
    last = ""
    for _ in range(attempts):
        last = probe_egress_ip(proxy)
        if looks_like_ipv4(last):
            return last
        time.sleep(RETRY_DELAY_SECONDS)
    return last
