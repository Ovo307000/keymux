"""The individual gateway checks that :mod:`verify_routes` runs."""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from check_result import CheckResult
from keymux.settings import KeyMuxSettings

CONCURRENT_REQUESTS_PER_ROUTE = 4

# Error codes KeyMux itself produces; anything else in a 5xx body came from the
# upstream and is a correct pass-through, not a KeyMux defect.
KEYMUX_FAILURE_CODES = frozenset(
    {"upstream_connection_failed", "upstream_timeout", "upstream_protocol_error", "internal_error"}
)


async def check_health(client: httpx.AsyncClient) -> CheckResult:
    response = await client.get("/health")
    return CheckResult(
        "health", response.status_code == 200 and response.json() == {"status": "ok"},
        f"HTTP {response.status_code} {response.text[:120]}",
    )


async def check_routes_document(client: httpx.AsyncClient, settings: KeyMuxSettings) -> CheckResult:
    response = await client.get("/routes")
    names = {entry["route"] for entry in response.json()["routes"]}
    expected = {route.name for route in settings.routes}
    leaked = [
        route.name
        for route in settings.routes
        if route.incoming_key in response.text or route.upstream.api_key in response.text
    ]
    passed = response.status_code == 200 and names == expected and not leaked
    return CheckResult("routes document", passed, f"routes={sorted(names)} leaked={leaked}")


async def check_routes_answer(
    client: httpx.AsyncClient, settings: KeyMuxSettings, model: str
) -> CheckResult:
    """Probe every route on the endpoint it declares, and expect a 200.

    The endpoint is declared per route because upstreams differ: AnyRouter
    serves ``GET /v1/models`` and ``POST /v1/responses`` but answers 404 for
    ``/v1/chat/completions``.
    """
    outcomes = []
    passed = True
    for route in settings.routes:
        response = await _probe_route(client, route, model)
        passed = passed and response.status_code == 200
        outcomes.append(
            f"{route.name}[{route.verify.method} {route.verify.path}]={response.status_code}"
        )
        if response.status_code != 200:
            outcomes.append(response.text[:160].replace("\n", " "))
    return CheckResult("route answers", passed, " ".join(outcomes))


async def _probe_route(client: httpx.AsyncClient, route, model: str) -> httpx.Response:
    headers = {"Authorization": f"Bearer {route.incoming_key}"}
    if route.verify.method == "GET":
        return await client.get(route.verify.path, headers=headers)
    return await client.post(
        route.verify.path,
        json={"model": model, "messages": [{"role": "user", "content": "say ok"}]},
        headers=headers,
    )


async def check_streaming(
    client: httpx.AsyncClient, settings: KeyMuxSettings, model: str
) -> CheckResult:
    streaming_routes = [route for route in settings.routes if route.verify.streaming]
    if not streaming_routes:
        return CheckResult("streaming (SSE)", True, "no route declares streaming")
    measurements = [await _measure_stream(client, route, model) for route in streaming_routes]
    detail = " | ".join(f"{item['route']}: {item['detail']}" for item in measurements)
    return CheckResult("streaming (SSE)", all(item["passed"] for item in measurements), detail)


async def _measure_stream(client: httpx.AsyncClient, route, model: str) -> dict:
    started_at = time.perf_counter()
    first_chunk_at: float | None = None
    chunks = 0
    body = b""
    async with client.stream(
        route.verify.method,
        route.verify.path,
        json={
            "model": model,
            "stream": True,
            "messages": [{"role": "user", "content": "count from 1 to 5"}],
        },
        headers={"Authorization": f"Bearer {route.incoming_key}"},
    ) as response:
        content_type = response.headers.get("content-type", "")
        async for chunk in response.aiter_bytes():
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter() - started_at
            chunks += 1
            body += chunk
    streamed = "text/event-stream" in content_type and chunks > 3 and b"data:" in body
    first_chunk = "n/a" if first_chunk_at is None else f"{first_chunk_at:.3f}s"
    return {
        "route": route.name,
        "passed": response.status_code == 200 and streamed,
        "detail": f"HTTP {response.status_code} chunks={chunks} first_chunk={first_chunk}",
    }


async def check_error_handling(
    client: httpx.AsyncClient, settings: KeyMuxSettings, model: str
) -> CheckResult:
    details = []
    passed = True

    missing = await client.post("/v1/chat/completions", json={"model": model})
    passed = passed and missing.status_code == 401
    details.append(f"no key={missing.status_code}")

    unknown = await client.post(
        "/v1/chat/completions",
        json={"model": model},
        headers={"Authorization": "Bearer definitely-not-a-router-key"},
    )
    passed = passed and unknown.status_code == 401
    details.append(f"unknown key={unknown.status_code}")

    return CheckResult("KeyMux 401 handling", passed, " | ".join(details))


async def check_upstream_error_passthrough(
    client: httpx.AsyncClient, settings: KeyMuxSettings
) -> CheckResult:
    route = next((item for item in settings.routes if item.verify.method == "POST"), None)
    if route is None:
        return CheckResult("upstream error passthrough", True, "no POST route to probe")

    response = await client.post(
        route.verify.path,
        json={
            "model": "definitely-not-a-real-model-xyz",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": f"Bearer {route.incoming_key}"},
    )
    body_is_json = _parse_json(response.text) is not None
    passed = 400 <= response.status_code < 500 and body_is_json
    detail = (
        f"{route.name}: HTTP {response.status_code} "
        f"content_type={response.headers.get('content-type')} json={body_is_json}"
    )
    return CheckResult("upstream error passthrough", passed, detail)


async def check_concurrency(
    client: httpx.AsyncClient, settings: KeyMuxSettings, model: str
) -> CheckResult:
    """Load every route at once and look for KeyMux-side failures.

    The upstream is free to answer 429; that is the upstream's business and it
    proves the status code travelled back untouched. Only 5xx and transport
    errors are KeyMux failures here.
    """
    responses = await asyncio.gather(
        *_concurrent_calls(client, settings, model), return_exceptions=True
    )
    failures = [response for response in responses if _is_keymux_failure(response)]
    statuses = [getattr(response, "status_code", 0) for response in responses]
    upstream_errors = [status for status in statuses if 500 <= status < 600]
    detail = (
        f"{len(statuses)} requests, {len(failures)} KeyMux failures, "
        f"upstream_5xx={len(upstream_errors)}, upstream_rate_limited={statuses.count(429)}"
    )
    if failures:
        detail += f" {[_describe_failure(f) for f in failures[:4]]}"
    return CheckResult("concurrency", not failures, detail)


def _is_keymux_failure(response) -> bool:
    """An upstream 5xx that travelled back untouched is not a KeyMux failure."""
    if isinstance(response, Exception):
        return True
    if response.status_code < 500:
        return False
    body = _parse_json(response.text) or {}
    return (body.get("error") or {}).get("code") in KEYMUX_FAILURE_CODES


def _describe_failure(response) -> str:
    if isinstance(response, Exception):
        return repr(response)[:80]
    return f"{response.status_code}:{response.text[:80]}"


def _concurrent_calls(client: httpx.AsyncClient, settings: KeyMuxSettings, model: str) -> list:
    payload = {"model": model, "messages": [{"role": "user", "content": "ok"}]}
    return [
        client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {route.incoming_key}"},
        )
        for route in settings.routes
        for _ in range(CONCURRENT_REQUESTS_PER_ROUTE)
    ]


def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
