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
