# KeyMux

A single-entry, key-based egress router for OpenAI-compatible APIs, running on
your own machine.

Every client (Codex, any OpenAI-compatible tool) talks to **one** address:

```
http://127.0.0.1:9000/v1
```

KeyMux reads the router key from `Authorization`, and that key alone decides

1. which **real upstream API key** is used,
2. which **upstream base URL** is used, and
3. which **Clash/Mihomo exit node** - and therefore which **public IP** - the
   request leaves through.

```
Codex / client
      |  Authorization: Bearer <router key>
      |  http://127.0.0.1:9000/v1
      v
  +--------------------+
  |      KeyMux        |   replaces the router key with the real key
  |  router key -> route |
  +---------+----------+
            |  http://127.0.0.1:7893  or  http://127.0.0.1:7894
            v
  +--------------------+
  |   Clash / Mihomo   |   one listener per node, pinned outbound
  +---------+----------+
            v
     upstream API
```

The Clash ports (`7893`, `7894`) are an internal detail between KeyMux and
Clash. Clients never see them, and they are not part of your configuration
language: the concept is **route -> egress**, not "key equals port".

## Why not let Clash read the API key?

Upstream API traffic is HTTPS. Inside a CONNECT tunnel the `Authorization`
header is encrypted, so Clash cannot route on it - and it must not. Network
routing and credential selection are separate jobs:

* **KeyMux** answers "which credential and which upstream?"
* **Clash/Mihomo** answers "which exit node and which public IP?"

Mihomo cannot select a different outbound per connection through one proxy
port, and switching a proxy group per request races under concurrency
(request A selects Node A, request B selects Node B, request A then leaves
through Node B). KeyMux therefore uses **one Mihomo listener per route**
(`listeners:` with a fixed `proxy:`), which is concurrent-safe and sticky by
construction.

## Requirements

* Windows
* Python 3.11 or newer
* Clash Verge Rev with the Mihomo core (tested with Mihomo Meta v1.19.29)

## Install

```bat
python -m pip install -r requirements.txt
```

## Configure

### 1. Secrets: `.env`

```bat
copy .env.example .env
```

Fill in four values. The router keys are what you hand to clients; the real
keys stay on this machine and are never given to a client.

```
ROUTER_KEY_A=kmx_a_...
ROUTER_KEY_B=kmx_b_...
REAL_API_KEY_A=sk-...
REAL_API_KEY_B=sk-...
```

`.env` is git-ignored. Real keys never appear in source code or in the log.

### 2. Routes: `config.yaml`

```bat
copy config.example.yaml config.yaml
```

```yaml
server:
  host: 127.0.0.1        # KeyMux refuses to bind 0.0.0.0 unless you opt in
  port: 9000

routes:
  account-a:
    incoming_key: ${ROUTER_KEY_A}
    upstream:
      base_url: https://tokenrhythm.studio
      api_key: ${REAL_API_KEY_A}
    egress:
      proxy: http://127.0.0.1:7893
      label: node-a-hongkong
```

**URL rule.** The upstream URL is `base_url` + the incoming path + the incoming
query string, so `base_url` is an origin (an optional path prefix is allowed).
A client calling `POST /v1/responses` with `base_url: https://api.example.com`
reaches `https://api.example.com/v1/responses`.

Per route you can also set `upstream.api_key_header` (default `Authorization`)
and `upstream.authorization_scheme` (default `Bearer`).

**Verification probe.** `tools/verify_routes.py` asks each route for a `200` on
the endpoint it declares, because upstreams differ: AnyRouter serves
`GET /v1/models` and `POST /v1/responses` but answers `404` for
`/v1/chat/completions`. The default probe is `POST /v1/chat/completions` with a
streaming check; override it per route:

```yaml
    verify:
      method: GET
      path: /v1/models
      streaming: false   # skip the incremental-SSE check for this route
```

## Configure Clash Verge Rev

Two nodes with **different public IPs** are required. Node names that differ do
not guarantee different exit IPs, so the tool below measures the IP instead of
trusting the name.

```bat
python tools/setup_clash_listeners.py ^
    --listener keymux-route-a 7893 "香港 01 HKIX" ^
    --listener keymux-route-b 7894 "日本 01 NTTドコモ"
```

What it does:

1. locates the Clash Verge Rev data directory and the merge file attached to the
   **currently selected profile**,
2. refuses to write if a node name does not exist in the running configuration
   (a bad listener name would stop Mihomo from loading at all),
3. writes a `listeners:` block into that merge file, so subscription updates
   keep it, and into the generated runtime configuration,
4. reloads Mihomo through its named-pipe controller (`\\.\pipe\verge-mihomo`;
   Clash Verge Rev ships with the TCP controller disabled),
5. prints the public IP observed through every listener.

Your subscription file itself is never modified, and the main mixed port
(`7897`) keeps working exactly as before.

Notes:

* The generated block is kept **last** in the merge file. Do not add keys after
  it - the tool replaces everything from its marker to the end of the file.
* Re-run the tool after switching to a different profile, or if a subscription
  update renames the nodes.
* The tool validates the node names, the ports and the resulting YAML **before**
  writing anything, backs both files up, and restores them if mihomo rejects the
  result.
* No REST API juggling, no round-robin, no per-request node switching.

## Run

```bat
run.bat
```

or

```bat
python -m keymux.main
```

`KEYMUX_CONFIG` overrides the configuration path (default `config.yaml`),
`KEYMUX_LOG_LEVEL` the log level (default `info`).

## Verify

```bat
python tools/verify_routes.py
```

It checks, against the real network:

| Check | What it proves |
| --- | --- |
| health | `GET /health` answers `{"status":"ok"}` |
| routes document | every route is listed and no credential leaks |
| egress public IPs | each egress proxy reports a public IP, and they differ |
| route answers | every route returns a real `200` on the endpoint it declares in its `verify` block |
| streaming (SSE) | `text/event-stream` arrives in several chunks, not one buffer |
| KeyMux 401 handling | no key and an unknown key are both answered `401` by KeyMux itself |
| upstream error passthrough | an upstream `4xx` reaches the client unchanged, body and content type included |
| concurrency | parallel requests produce no KeyMux-side failure (an upstream `5xx` or `429` that travelled back untouched is reported separately, not counted as a defect) |
| router key -> real key + exit IP | a throwaway instance pointed at `httpbin` shows that each router key forwards **its own** real key and leaves from **its own** IP, also under concurrency |

## Use it from Codex

Both Codex instances only need to know one address. Neither needs the Clash
ports, node names or any Clash setting.

Instance A:

```bat
set OPENAI_BASE_URL=http://127.0.0.1:9000/v1
set OPENAI_API_KEY=<router key A>
```

Instance B:

```bat
set OPENAI_BASE_URL=http://127.0.0.1:9000/v1
set OPENAI_API_KEY=<router key B>
```

With a `config.toml`-based Codex setup the same two values go to
`openai_base_url` / the API key entry.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | `{"status": "ok"}` |
| `GET /routes` | route names, upstream base URLs, egress labels and proxy endpoints. Never returns a router key or a real key. |
| `/v1`, `/v1/*` | transparent proxy: `POST /v1/responses`, `POST /v1/chat/completions`, and any other `/v1/...` path, for `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS` and `HEAD` |

Method, path, query string, request body, upstream status code, upstream body
and upstream content type are preserved. Every response carries an
`x-request-id` header (a client-supplied one is kept).

## Behaviour details

**Credential handling.** The router key is read from `Authorization: Bearer
<key>`, falling back to `x-api-key`. All credential headers (`authorization`,
`x-api-key`, `api-key`, `x-goog-api-key`) are dropped and only the configured
one is re-added with the route's real key, so a router key can never reach the
upstream.

**Client isolation.** Each route owns its own `httpx.AsyncClient` with its own
transport and connection pool, so keep-alive connections can never be reused
across routes - a reused connection would leave through the wrong exit node.

**Streaming.** The upstream response body is relayed chunk by chunk as it
arrives; nothing is buffered first. When a client disconnects, the relay
generator is cancelled and the in-flight upstream request is closed.

**Errors.**

| Situation | Answer |
| --- | --- |
| no router key | `401 missing_api_key` |
| unknown router key | `401 invalid_api_key` |
| egress proxy unreachable | `502 upstream_connection_failed` |
| upstream unreachable | `502 upstream_connection_failed` |
| upstream timed out | `504 upstream_timeout` |
| any upstream status | forwarded unchanged, body and content type included |

**Logging.** One line per proxied request:

```
request_id=c0461d976bf84224 route=account-a method=POST path=/v1/responses status=200 streaming=true egress=node-a-hongkong latency=1432ms
```

Router keys, real keys and `Authorization` values are never logged.
`latency` measures up to the upstream response head, so for streaming requests
it is the time to the first byte.

## Project layout

```
keymux/
  config.py           configuration loading, ${VAR} expansion, validation
  settings.py         the configuration model (routes, upstreams, egress, probes)
  routing.py          router key lookup (constant-time comparison)
  headers.py          header rewriting rules
  upstream.py         one HTTP client per route, streaming relay
  proxy.py            request path through KeyMux
  errors.py           KeyMux errors and transport failure mapping
  request_logging.py  safe log lines
  main.py             application factory, endpoints, entry point
tools/
  setup_clash_listeners.py   pin Mihomo listeners to fixed nodes
  clash_configuration.py     render and validate the managed listeners block
  verify_routes.py           run the checks and report
  route_checks.py            the individual gateway checks
  egress_probe.py            public IP behind each egress endpoint, no KeyMux involved
  egress_proof.py            router key -> real key + exit IP, over the real network
  network_probe.py           the public-IP probe shared by both tools above
  mihomo_controller.py       named-pipe client for the Mihomo API
  check_result.py            the shape of one verification result
tests/                       unit and integration tests (stdlib unittest)
```

`logging.py` and `routes.py` were renamed to `request_logging.py` and
`routing.py` to avoid shadowing the standard-library `logging` module and the
framework's own router concept.

## Tests

```bat
python -m unittest discover -s tests -t .
```

The suite starts a real mock upstream and real recording proxies on loopback
sockets, so streaming timing and egress isolation are exercised for real, not
mocked out.

## Troubleshooting

* **Mihomo will not start after an edit.** A `listeners:` entry names a node
  that no longer exists. Fix or remove the block in the profile's merge file
  (Clash Verge Rev data directory, `profiles/<merge file>.yaml`), or re-run
  `tools/setup_clash_listeners.py` with current node names. The tool writes a
  `*.keymux-<timestamp>.bak` next to each file it changes.
* **`502` from KeyMux.** Check that Clash is running and that both listener
  ports answer: `python tools/egress_probe.py`.
* **Both routes report the same public IP.** Choose different nodes and re-run
  the setup tool - node names are not proof of distinct exits.
* **`429 RATE_LIMITED` from the upstream.** That is the upstream's own rate
  limit travelling back untouched; KeyMux does not retry or hide it.

## Deliberate trade-offs

* **Request bodies are buffered**, response bodies are streamed. API requests
  are small; the cost of streaming request bodies (chunked uploads, fragile
  `Content-Length` handling) buys nothing here.
* **No retries.** A retried request could leave through a different connection;
  the status code is passed back so the client decides.
* **No database, queue, container or user interface.** This is a local
  single-process router.

## Known limitations

* **A stream that breaks after the response head is already sent cannot become a
  `5xx`.** Once KeyMux has forwarded `200` and `text/event-stream`, the status is
  on the wire; if the upstream then dies, the client sees a truncated stream.
  This is inherent to streaming, not something KeyMux can paper over.
* **`GET /routes` is unauthenticated** and lists route names, upstream base URLs
  and egress endpoints (never a key). That is safe while KeyMux binds loopback
  only; if you ever set `server.allow_non_loopback: true`, put it behind
  something that authenticates.
* **The egress IP probe checks IPv4** (`tools/egress_probe.py`); a node that
  only offers IPv6 egress would be reported as failing.
* **Client disconnect while waiting for the upstream response head** is watched
  with a 0.2 s poll, so cancellation is prompt but not instantaneous.
