"""Pin Clash Verge Rev / mihomo listeners to fixed outbound nodes.

KeyMux reaches the internet through one Clash listener per route, so each
route leaves from a fixed node and therefore a fixed public IP. This tool

1. locates the Clash Verge Rev data directory and the merge file that belongs
   to the currently selected profile,
2. refuses to write if a requested node does not exist in the running config,
3. writes the listeners into that merge file (so a subscription update keeps
   them) and into the generated runtime config,
4. reloads mihomo through its named-pipe controller, and
5. reports the public IP observed through every listener.

Usage::

    python tools/setup_clash_listeners.py \
        --listener keymux-route-a 7893 "香港 01 HKIX" \
        --listener keymux-route-b 7894 "日本 01 NTTドコモ"
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mihomo_controller  # noqa: E402

VERGE_DATA_DIRECTORY_NAME = "io.github.clash-verge-rev.clash-verge-rev"
RUNTIME_CONFIG_FILENAME = "clash-verge.yaml"
PROFILES_INDEX_FILENAME = "profiles.yaml"
MANAGED_BLOCK_MARKER = "# --- keymux managed listeners (generated, keep this block last) ---"
EGRESS_PROBE_URL = "https://api.ipify.org"
PROBE_TIMEOUT_SECONDS = 25


@dataclass(frozen=True)
class Listener:
    name: str
    port: int
    node: str


@dataclass(frozen=True)
class VergeLayout:
    data_directory: Path
    runtime_config: Path
    merge_file: Path


def main() -> int:
    arguments = _parse_arguments()
    listeners = _parse_listeners(arguments.listener)

    layout = locate_verge_layout()
    print(f"verge data directory : {layout.data_directory}")
    print(f"runtime configuration: {layout.runtime_config}")
    print(f"profile merge file   : {layout.merge_file}")

    secret = mihomo_controller.read_controller_secret(layout.runtime_config)
    _require_nodes_present([listener.node for listener in listeners], secret)

    block = render_managed_block(listeners)
    _backup(layout.merge_file)
    _backup(layout.runtime_config)
    write_managed_block(layout.merge_file, block)
    write_managed_block(layout.runtime_config, block)
    print("wrote listeners into the merge file and the runtime configuration")

    status, body = mihomo_controller.reload_runtime_config(layout.runtime_config, secret)
    if status not in (200, 204):
        print(f"mihomo rejected the configuration: HTTP {status} {body[:400]!r}", file=sys.stderr)
        return 1
    print(f"mihomo reloaded the configuration (HTTP {status})")

    if not arguments.no_verify:
        return _report_egress_ip(listeners)
    return 0


def locate_verge_layout() -> VergeLayout:
    data_directory = Path(os.environ["APPDATA"]) / VERGE_DATA_DIRECTORY_NAME
    if not data_directory.is_dir():
        raise SystemExit(f"Clash Verge Rev data directory not found: {data_directory}")

    runtime_config = data_directory / RUNTIME_CONFIG_FILENAME
    profiles_directory = data_directory / "profiles"
    merge_file = profiles_directory / _current_merge_filename(
        data_directory / PROFILES_INDEX_FILENAME
    )
    return VergeLayout(data_directory, runtime_config, merge_file)


def _current_merge_filename(profiles_index: Path) -> str:
    document = _load_yaml(profiles_index)
    current_uid = document["current"]
    for item in document["items"]:
        if item["uid"] != current_uid:
            continue
        merge_uid = (item.get("option") or {}).get("merge")
        if not merge_uid:
            raise SystemExit(
                f"the selected profile {current_uid!r} has no merge profile attached; "
                "create one in Clash Verge Rev first"
            )
        for candidate in document["items"]:
            if candidate["uid"] == merge_uid:
                return candidate["file"]
        raise SystemExit(f"merge profile {merge_uid!r} is missing from profiles.yaml")
    raise SystemExit(f"current profile {current_uid!r} is missing from profiles.yaml")


def _load_yaml(path: Path) -> dict:
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def render_managed_block(listeners: tuple[Listener, ...]) -> str:
    lines = [MANAGED_BLOCK_MARKER, "listeners:"]
    for listener in listeners:
        lines.extend(
            [
                f"  - name: {listener.name}",
                "    type: mixed",
                "    listen: 127.0.0.1",
                f"    port: {listener.port}",
                f'    proxy: "{listener.node}"',
            ]
        )
    return "\n".join(lines) + "\n"


def write_managed_block(path: Path, block: str) -> None:
    """Replace the managed block, keeping everything above it untouched."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    marker_index = existing.find(MANAGED_BLOCK_MARKER)
    head = existing[:marker_index] if marker_index != -1 else existing
    head = head.rstrip("\n")
    content = f"{head}\n\n{block}" if head else block
    path.write_text(content, encoding="utf-8", newline="\n")


def _require_nodes_present(nodes: list[str], secret: str) -> None:
    available = set(mihomo_controller.list_proxy_names(secret))
    missing = [node for node in nodes if node not in available]
    if not missing:
        return
    for node in missing:
        print(f"node not found in the running config: {node!r}", file=sys.stderr)
        for suggestion in _similar(node, available):
            print(f"  did you mean: {suggestion}", file=sys.stderr)
    raise SystemExit("refusing to write a configuration that would not start")


def _similar(node: str, available: set[str]) -> list[str]:
    stem = node.split()[0] if node.split() else node
    return sorted(name for name in available if stem and stem in name)[:8]


def _backup(path: Path) -> None:
    if not path.is_file():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_suffix(path.suffix + f".keymux-{stamp}.bak"))


def _report_egress_ip(listeners: tuple[Listener, ...]) -> int:
    failures = 0
    observed: dict[str, str] = {}
    for listener in listeners:
        address = probe_egress(listener)
        observed[listener.node] = address
        print(f"{listener.port}  {listener.node}  ->  {address}")
        if not _looks_like_ip(address):
            failures += 1
    distinct = set(observed.values())
    if len(distinct) < len(listeners):
        print("warning: listeners do not all show a distinct public IP", file=sys.stderr)
        failures += 1
    return 1 if failures else 0


def probe_egress(listener: Listener) -> str:
    process = subprocess.run(
        [
            "curl.exe", "-s", "--max-time", str(PROBE_TIMEOUT_SECONDS),
            "-x", f"http://127.0.0.1:{listener.port}", EGRESS_PROBE_URL,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (process.stdout or "").strip() or f"<probe failed: {process.stderr.strip()[:80]}>"


def _looks_like_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _parse_listeners(raw: list[list[str]] | None) -> tuple[Listener, ...]:
    if not raw:
        raise SystemExit("at least one --listener NAME PORT NODE is required")
    listeners = []
    for parts in raw:
        if len(parts) < 3:
            raise SystemExit(f"malformed --listener {parts!r}; expected NAME PORT NODE")
        listeners.append(Listener(name=parts[0], port=int(parts[1]), node=" ".join(parts[2:])))
    ports = [listener.port for listener in listeners]
    if len(set(ports)) != len(ports):
        raise SystemExit("listener ports must be unique")
    return tuple(listeners)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listener", nargs="+", action="append", metavar="NAME PORT NODE")
    parser.add_argument("--no-verify", action="store_true", help="skip the egress IP probe")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
