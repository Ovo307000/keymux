# Experience

Verified on 2026-09-30 on this machine. Each entry states the evidence.

## Clash Verge Rev / Mihomo

**`listeners:` with a fixed `proxy:` is the supported way to pin an inbound
port to one outbound node.**
Evidence: Mihomo v1.19.29 (`verge-mihomo.exe -v`), official docs
`wiki.metacubex.one/config/inbound/` - "proxy: 如果不为空则直接将该入站流量交由指定
proxy处理". Confirmed by measurement: traffic through listen port 7893 leaves
from the node's IP and bypasses rule matching.

**The Mihomo REST API is only reachable through the named pipe
`\\.\pipe\verge-mihomo`, not TCP.**
The generated runtime configuration has `external-controller: ''` and
`external-controller-pipe: \\.\pipe\verge-mihomo`. Large responses are
`Transfer-Encoding: chunked`, so a pipe client must de-chunk. The controller
secret is in the runtime configuration (`secret:`).

**Clash Verge Rev's profile enhancement files are applied on every
configuration generation, and `listeners` from a merge profile is inserted as
a new top-level key.**
Evidence: `src-tauri/src/enhance/merge.rs` (`use_merge` / `deep_merge`: mappings
merge recursively, other values replace, absent keys are inserted). Confirmed
empirically: `DOMAIN-SUFFIX,tokenrhythm.studio,DIRECT` from the profile's rules
file appears in the generated `clash-verge.yaml`.

**Clash Verge Rev does not watch profile files.**
Writing the merge file did not change `clash-verge.yaml`'s modification time.
Apply via the user interface, or write the runtime configuration and reload it
through the pipe controller.

**A `listeners` entry referencing a missing node stops Mihomo from loading the
whole configuration.** Validate node names against `GET /proxies` before
writing.

**Node names do not imply distinct exits.** Measured distinct exits:
`香港 01 HKIX` -> `104.234.20.81`, `香港 11 HKT&HOME` -> `218.250.31.40`,
`台湾 01 中華電信` -> `211.20.157.199`, `日本 01 NTTドコモ` -> `143.20.38.92`,
`日本 05 SKYSPARK` -> `143.20.38.96`, `韩国 01 SK텔레콤` -> `82.117.226.109`,
`新加坡 01 Singtel` -> `67.159.52.193`, `美国 01 AT&SANJOSE` -> `23.174.104.75`,
`德国 01 法兰克福` -> `45.154.207.222`, `英国 01 伦敦` -> `94.156.250.87`.
`香港 15 HKT&HOME` failed the TLS handshake at the time of measurement.

## KeyMux

**httpx 0.28 takes `proxy=` on `AsyncHTTPTransport`, and passing `transport=`
to `AsyncClient` makes the client-level `proxy=` argument a no-op.** The proxy
must be given to the transport.

**`trust_env=False` is required** on both the transport and the client,
otherwise the machine's `NO_PROXY` / proxy environment variables interfere with
per-route egress.

**Per-route clients are a correctness requirement, not a preference.** One
shared client means shared keep-alive connections, and a reused connection can
leave through the previous route's node.

**httpx's `ASGITransport` buffers the whole response before returning**, so it
cannot test incremental streaming. Streaming tests need a real socket.

**`unittest.IsolatedAsyncioTestCase` plus `uvicorn.Server` in a thread** is
enough to test streaming, timing and client disconnect with no test
dependencies beyond the runtime requirements.

**Responses must have `content-length` and `content-encoding` removed**:
httpx transparently decodes the body, so forwarding the upstream's values
describes bytes the client will not receive.

**`secrets.compare_digest` raises `TypeError` on a non-ASCII `str`.** HTTP header
values arrive as bytes and uvicorn decodes them as latin-1, so any client can
send a non-ASCII router key. Compare UTF-8 bytes, or an unauthenticated request
becomes a 500.

**`str(httpx_error)` embeds the request URL and, for proxy failures, the proxy
URL.** Never echo it to a client or a log; keep `type(error).__name__`.

**Disconnect handling needs two layers.** Starlette cancels the response
generator on disconnect, which covers the streaming phase. The window before
upstream response headers arrive is not covered by that, so the send itself has
to be wrapped in a task group that polls `request.is_disconnected()` — the same
task-group shape Starlette uses internally.

**httpx's `is_disconnected()`-style polling is only safe after the whole request
body has been read**, otherwise the poll consumes a body message.

**`yaml.safe_dump` beats an f-string for writing node names into another
program's configuration file.** Proxy names legitimately contain quotes, colons
and `#`.

## Relay upstreams (measured 2026-10-01)

**AnyRouter (`https://anyrouter.top`) serves `/v1/models` and `/v1/responses`
but returns 404 for `/v1/chat/completions` on every model.** So a route carrying
an AnyRouter key cannot be verified with a chat completion; declare
`verify: {method: GET, path: /v1/models, streaming: false}` for it. Reached
`/v1/models` with 200 through `台湾 01 中華電信` and `新加坡 01 Singtel`.

**AnyRouter's `/v1/responses` rejects anything that is not a genuine Codex
request**, answering `400 invalid codex request` / `code invalid_responses_request`.
Confirmed through KeyMux with a minimal JSON body. This is request-shape
policy, not a key problem: the same key returns 200 on `/v1/models`.

**`api.ipify.org` is flaky through `香港 01 HKIX`** (curl exit 0 with an empty
body, or no response at all, while `anyrouter.top` answers 200 through the same
listener). Treat one failed ipify probe as "retry", not as "the node is down" -
which is why the egress probe retries three times.

## Codex relay wiring (measured 2026-10-01)

**Codex's model list is generated by opencodex, not written in `config.toml`.**
`~/.codex/config.toml` only holds `openai_base_url = http://127.0.0.1:10100/v1`
and `model_catalog_json`. The picker reads
`~/.codex/opencodex-catalog.json`, whose entries are produced from
opencodex's `providers` plus `customModels`. So "add a model to Codex" means
"add an opencodex provider and register its model", never "hand-write a provider
in Codex".

**A new opencodex provider needs a real restart.** Before the restart,
`ocx provider test <name>` answered `unknown provider` and a request for the new
model fell through to the default provider, which passed the whole
`provider/model` string upstream as the model name and returned the upstream's
`500 get_channel_failed`. Same key on `/v1/models` was already 200, so the
failure looks like a model problem when it is a not-loaded-yet problem.

**`ocx stop` + `ocx service start` round-trips `config.toml`.** `ocx stop`
removes `openai_base_url` and `model_catalog_json` and cuts the catalog back to
the native models; `ocx service start` restores both. The `model = ...` line
survives, and the file came back **byte-identical** to the pre-change backup.
Verify that with `Compare-Object` rather than trusting it.

**Registering a model without a context window leaves it at the gateway default
(128,000 here), and that survives fixes to other things.**
Removing a custom model registration to fix its display name also drops the
`contextWindow` it carried, so the model silently fell to 128k while the same
upstream model on the sibling providers was 1,000,000. Check the value after any
`ocx models remove`, not just after `add`.

**`ocx models add <provider> <model> --display-name "<label>"` replaces the
model's name in Codex's picker, and `--display-name` refuses a slash.**
`provider/model` is the form the picker should show, so the way to keep it is to
**not** pass `--display-name` at all - an `add` with `--context-window` only
leaves the name as the default slug. To undo a label, `ocx models remove
<provider>/<modelId> --yes`: the model survives (the upstream advertises it) and
the name falls back to the default. Recorded as 铁律 16 in the `relay-routing`
skill.

**AnyRouter's `/v1/models` advertises no `context_length` at all** (the entries
carry only `id`/`object`/`created`/`owned_by`/`permission`/`root`/`parent`).
TokenRhythm's does. So "what does this model really support" has to be answered
per upstream: the advertised field when present, otherwise the value the same
model already runs at on another provider here (AnyRouter `gpt-6-astra` is
1,000,000 on `anyrouter`, `anyrouter-2` and `agentrouter`).

**Both context values must be set**: `--context-window` on the model and
`ocx models context provider <name> on --value N` for the provider cap.
Setting only one leaves the other clamping the model.

**`scripts/doctor.ps1` used to be blind to any provider whose name did not match
`anyrouter|agentrouter|tokenrhythm`.** Both its connectivity loop and its
"模型与上下文" listing filtered on that pattern, so the two `keymux-local-relay-*`
providers were checked by nothing - which is how a 128k model went unnoticed.
Both filters now include `keymux`, and the low-context warning names the
offending slugs and prints the two commands that fix them. When a new provider
namespace is added, widen those filters in the same change.

**A new provider pulls in the upstream's entire model list.** Adding a provider
pointed at AnyRouter appended 16 models per provider, including the blocked
Claude ones and `gpt-6-astra-cc-format` (which 404s on the Responses endpoint).
Disable all but the target with `ocx models disable <provider>/<model>`, then
check `disabledModels` in `config.json`.

**AnyRouter's `400 invalid codex request` is a useful end-to-end probe.** A
synthetic minimal body always gets it, and it still proves the chain: KeyMux's
log shows `route=... egress=...` for the request, so
opencodex -> KeyMux -> the right node is confirmed without a real Codex session.
