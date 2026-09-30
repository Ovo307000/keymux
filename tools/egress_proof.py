"""The strongest KeyMux proof: which real key and which public IP a router key gets.

A throwaway KeyMux instance is started with the **same** router keys and the
same egress proxies, but with httpbin's ``/anything`` endpoint as the upstream.
httpbin echoes the ``Authorization`` header it received and the IP it saw, so a
single request shows, end to end and over the real network:

* which real upstream key KeyMux substituted for the router key, and
* which node - and therefore which public IP - the request left through.

The same probe is then repeated concurrently for every route to show that two
routes never cross egress under load.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_result import CheckResult  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROOF_UPSTREAM = "https://httpbin.org/anything"
PROOF_STARTUP_SECONDS = 25.0
PROOF_REQUESTS_PER_ROUTE = 4
HTTP_TIMEOUT_SECONDS = 90.0


async def run(settings, requests_per_route: int = PROOF_REQUESTS_PER_ROUTE) -> CheckResult:
    """Start the throwaway instance, probe every route, and return a CheckResult."""
    port = _free_port()
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "egress-proof.yaml"
        config_path.write_text(
            yaml.safe_dump(_proof_configuration(settings, port), allow_unicode=True),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "keymux.main"],
            cwd=str(PROJECT_ROOT),
            env={**os.environ, **_proof_environment(settings),
                 "KEYMUX_CONFIG": str(config_path)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            sequential, concurrent = await _probe_everything(
                process, settings, port, requests_per_route
            )
        finally:
            process.terminate()
            process.wait(timeout=10)

    return _judge(sequential, concurrent)


async def _probe_everything(process, settings, port: int, requests_per_route: int):
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=HTTP_TIMEOUT_SECONDS
    ) as client:
        if not await _wait_until_healthy(client):
            return None, None
        sequential = {route.name: await _probe_once(client, route) for route in settings.routes}
        concurrent = await asyncio.gather(
            *(
                _probe_once(client, route)
                for route in settings.routes
                for _ in range(requests_per_route)
            )
        )
        return sequential, concurrent


async def _probe_once(client: httpx.AsyncClient, route) -> dict:
    try:
        response = await client.get(
            "/v1/probe", headers={"Authorization": f"Bearer {route.incoming_key}"}
        )
    except httpx.HTTPError as error:
        return {"route": route.name, "ip": None, "key_forwarded": False, "error": repr(error)[:80]}
    document = _parse_json(response.text) or {}
    headers = document.get("headers") or {}
    return {
        "route": route.name,
        "ip": document.get("origin"),
        "key_forwarded": headers.get("Authorization") == f"Bearer {route.upstream.api_key}",
        "status": response.status_code,
    }


def _judge(sequential, concurrent) -> CheckResult:
    if sequential is None:
        return CheckResult("router key -> real key + exit IP", False, "proof instance did not start")

    addresses = {entry["ip"] for entry in sequential.values()}
    keys_forwarded = all(entry["key_forwarded"] for entry in sequential.values())
    isolation_ok = _isolation_holds(concurrent, sequential)
    detail = " ".join(
        f"{name}: ip={entry['ip']} real_key_forwarded={entry['key_forwarded']}"
        for name, entry in sequential.items()
    )
    detail += f" | concurrent_isolation={isolation_ok}"
    passed = keys_forwarded and len(addresses) == len(sequential) and isolation_ok
    return CheckResult("router key -> real key + exit IP", passed, detail)


def _isolation_holds(concurrent: list[dict], sequential: dict[str, dict]) -> bool:
    observed: dict[str, set] = {}
    for entry in concurrent:
        if entry.get("ip"):
            observed.setdefault(entry["route"], set()).add(entry["ip"])
    if len(observed) != len(sequential):
        return False
    return all(addresses == {sequential[name]["ip"]} for name, addresses in observed.items())


async def _wait_until_healthy(client: httpx.AsyncClient) -> bool:
    deadline = time.monotonic() + PROOF_STARTUP_SECONDS
    while time.monotonic() < deadline:
        try:
            if (await client.get("/health")).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.3)
    return False


def _proof_environment(settings) -> dict:
    environment = {}
    for route, token in _proof_tokens(settings):
        environment[f"PROOF_INCOMING_{token}"] = route.incoming_key
        environment[f"PROOF_UPSTREAM_{token}"] = route.upstream.api_key
    return environment


def _proof_configuration(settings, port: int) -> dict:
    return {
        "server": {"host": "127.0.0.1", "port": port, "allow_non_loopback": False},
        "routes": {
            route.name: {
                "incoming_key": f"${{PROOF_INCOMING_{token}}}",
                "upstream": {
                    "base_url": PROOF_UPSTREAM,
                    "api_key": f"${{PROOF_UPSTREAM_{token}}}",
                },
                "egress": {"proxy": route.egress.proxy, "label": route.egress.label},
            }
            for route, token in _proof_tokens(settings)
        },
    }


def _proof_tokens(settings) -> list[tuple]:
    """One unique variable-name token per route: two route names can sanitise alike."""
    return [
        (route, f"{index}_{_environment_token(route.name)}")
        for index, route in enumerate(settings.routes)
    ]


def _environment_token(name: str) -> str:
    """Environment variable names allow only [A-Za-z0-9_], route names do not."""
    return "".join(character.upper() if character.isalnum() else "_" for character in name)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
