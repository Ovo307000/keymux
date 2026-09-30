# Status

## What exists

KeyMux 1.0.0 - a single-entry, key-based egress router.

* Entry point: `http://127.0.0.1:9000/v1` (loopback only).
* Two live routes: `account-a` (egress `http://127.0.0.1:7893`, label
  `node-a-hongkong`) and `account-b` (egress `http://127.0.0.1:7894`, label
  `node-b-japan`). Both point at `https://tokenrhythm.studio`.
* Clash Verge Rev has two pinned listeners written into the merge profile
  `m9eBW8npCB07.yaml` of the currently selected subscription
  (`RaxfGroTgUC5`, 小火箭加速v260810).
* The main mixed port `7897` and the rest of the Clash configuration are
  unchanged.

## Verified (measured, not assumed)

* Exit IP through `7893`: `104.234.20.81` (node `香港 01 HKIX`).
* Exit IP through `7894`: `143.20.38.92` (node `日本 01 NTTドコモ`).
* `python tools/verify_routes.py` - 8 of 8 checks passed, including the
  per-router-key proof that each key forwards its own real key and leaves from
  its own IP, sequentially and under concurrency.
* `POST /v1/responses` works both streaming (SSE) and non-streaming.
* `python -m unittest discover -s tests -t .` - 39 tests pass.
* No router key or real key appears in `logs/`.

## Open items

* `tokenrhythm.studio` rate-limits the upstream keys (`429 RATE_LIMITED`,
  limit 30) under bursty concurrency. That is upstream behaviour, passed
  through unchanged; the verification tool reports it separately from
  KeyMux-side failures.
* Clash Verge Rev does **not** watch the profile merge file, so a listener
  change only reaches the runtime configuration after the profile is applied.
  The setup tool writes both the merge file and the runtime configuration and
  then reloads Mihomo, so this is handled; re-run it after changing nodes.
