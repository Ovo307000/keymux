# Status

## What exists

KeyMux 1.0.0 - a single-entry, key-based egress router.

* Entry point: `http://127.0.0.1:9000/v1` (loopback only).
* Four live routes, each on its own Clash listener and therefore its own exit:

  | route | upstream | egress label | proxy | exit IP |
  | --- | --- | --- | --- | --- |
  | `account-a` | tokenrhythm.studio | node-a-hongkong | 127.0.0.1:7893 | 104.234.20.81 |
  | `account-b` | tokenrhythm.studio | node-b-japan | 127.0.0.1:7894 | 143.20.38.92 |
  | `anyrouter-a` | anyrouter.top | node-c-taiwan | 127.0.0.1:7895 | 211.20.157.199 |
  | `anyrouter-b` | anyrouter.top | node-d-singapore | 127.0.0.1:7896 | 67.159.52.193 |

* The two `anyrouter-*` routes carry the two AnyRouter keys that opencodex also
  holds (`providers.anyrouter` and `providers.anyrouter-2`), so the same key can
  leave from a fixed, per-key IP. AgentRouter is deliberately not routed here.
* Codex reaches KeyMux through opencodex, which gained two extra providers:

  | opencodex provider | base URL | Codex model id | labelled |
  | --- | --- | --- | --- |
  | `keymux-local-relay-a` | `http://127.0.0.1:9000/v1` | `keymux-local-relay-a/gpt-6-astra` | 本地中转 A（AnyRouter key1） |
  | `keymux-local-relay-b` | `http://127.0.0.1:9000/v1` | `keymux-local-relay-b/gpt-6-astra` | 本地中转 B（AnyRouter key2） |

  Both are `adapter=openai-responses` with `allowPrivateNetwork`, each trusting
  one KeyMux router key, and all other models those providers pulled in are
  disabled. Everything that existed before is untouched, AgentRouter included.
* Clash Verge Rev has the four pinned listeners written into the merge profile
  `m9eBW8npCB07.yaml` of the currently selected subscription
  (`RaxfGroTgUC5`, 小火箭加速v260810).
* The main mixed port `7897` and the rest of the Clash configuration are
  unchanged, and `~/.opencodex/config.json`, `~/.codex/config.toml` and the
  AgentRouter provider were not modified.

## Verified (measured, not assumed)

* Exit IPs: `7893 -> 104.234.20.81`, `7894 -> 143.20.38.92`,
  `7895 -> 211.20.157.199`, `7896 -> 67.159.52.193` (all distinct).
* `python tools/verify_routes.py` - 9 of 9 checks passed, including the
  per-router-key proof that each key forwards its own real key and leaves from
  its own IP, sequentially and under concurrency.
* `POST /v1/responses` works both streaming (SSE) and non-streaming on the
  TokenRhythm routes. AnyRouter's `/v1/responses` answers
  `400 invalid codex request` for anything that is not a genuine Codex request
  (documented upstream policy) - KeyMux forwards that body and status unchanged,
  and `/v1/models` returns 200 through both AnyRouter routes.
* `python -m unittest discover -s tests -t .` - 66 tests pass.
* No router key or real key appears in `logs/`.

## Fixed after review

* A non-ASCII router key reached `secrets.compare_digest` as a `str` and raised
  `TypeError`, turning an unauthenticated request into a 500. Keys are now
  compared as UTF-8 bytes.
* `httpx` exception text (which embeds the request URL and the proxy URL) was
  echoed to the client and the log. Only the exception class name is kept now.
* `tools/setup_clash_listeners.py` could write a Clash configuration that does
  not start, and only discovered it after overwriting both files. It now
  validates nodes, ports, the resulting YAML and pre-existing `listeners:`
  before writing, and restores backups on a rejected reload.
* A second review round closed the remaining gaps: any spelling of a foreign
  top-level `listeners:`, a write failure, a restoring reload that also fails,
  and non-finite numeric configuration values. The tool now has 10 tests of its
  own.

## Open items

* `tokenrhythm.studio` rate-limits the upstream keys (`429 RATE_LIMITED`,
  limit 30) under bursty concurrency. That is upstream behaviour, passed
  through unchanged; the verification tool reports it separately from
  KeyMux-side failures.
* Clash Verge Rev does **not** watch the profile merge file, so a listener
  change only reaches the runtime configuration after the profile is applied.
  The setup tool writes both the merge file and the runtime configuration and
  then reloads Mihomo, so this is handled; re-run it after changing nodes.
