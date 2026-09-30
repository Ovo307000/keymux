"""Pin Clash Verge Rev / mihomo listeners to fixed outbound nodes.

KeyMux reaches the internet through one Clash listener per route, so each route
leaves from a fixed node and therefore a fixed public IP. This tool

1. locates the Clash Verge Rev data directory and the merge file that belongs
   to the currently selected profile,
2. refuses to write if a requested node does not exist in the running
   configuration, if a listener would collide with a pre-existing ``listeners:``
   section, or if the result would not parse as YAML,
3. backs up both files, writes the listeners into the merge file (so a
   subscription update keeps them) and into the generated runtime
   configuration,
4. reloads mihomo through its named-pipe controller and, if mihomo rejects the
   result, restores the backups so the next start still works, and
5. reports the public IP observed through every listener.

Usage::

    python tools/setup_clash_listeners.py \
        --listener keymux-route-a 7893 "香港 01 HKIX" \
        --listener keymux-route-b 7894 "日本 01 NTTドコモ"
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mihomo_controller  # noqa: E402

VERGE_DATA_DIRECTORY_NAME = "io.github.clash-verge-rev.clash-verge-rev"
RUNTIME_CONFIG_FILENAME = "clash-verge.yaml"
PROFILES_INDEX_FILENAME = "profiles.yaml"
MANAGED_BLOCK_MARKER = "# --- keymux managed listeners (generated, keep this block last) ---"
TOP_LEVEL_LISTENERS_PATTERN = re.compile(r"(?m)^listeners:\s*$")
EGRESS_PROBE_URL = "https://api.ipify.org"
PROBE_TIMEOUT_SECONDS = 25
MIN_PORT = 1
MAX_PORT = 65535
RELOAD_SUCCESS_CODES = (200, 204)


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
    merged = prepare_managed_content(layout.merge_file, block)
    runtime = prepare_managed_content(layout.runtime_config, block)

    backups = _backup_all(layout)
    _write_text(layout.merge_file, merged)
    _write_text(layout.runtime_config, runtime)
    print("wrote listeners into the merge file and the runtime configuration")

    status, body = mihomo_controller.reload_runtime_config(layout.runtime_config, secret)
    if status not in RELOAD_SUCCESS_CODES:
        print(f"mihomo rejected the configuration: HTTP {status} {body[:400]!r}", file=sys.stderr)
        _restore_and_reload(backups, layout.runtime_config, secret)
        return 1
    print(f"mihomo reloaded the configuration (HTTP {status})")

    if arguments.no_verify:
        return 0
    return _report_egress_ip(listeners)


def locate_verge_layout() -> VergeLayout:
    data_directory = Path(os.environ["APPDATA"]) / VERGE_DATA_DIRECTORY_NAME
    if not data_directory.is_dir():
        raise SystemExit(f"Clash Verge Rev data directory not found: {data_directory}")
    return VergeLayout(
        data_directory=data_directory,
        runtime_config=data_directory / RUNTIME_CONFIG_FILENAME,
        merge_file=data_directory / "profiles"
        / _current_merge_filename(data_directory / PROFILES_INDEX_FILENAME),
    )


def _current_merge_filename(profiles_index: Path) -> str:
    document = _load_yaml(profiles_index)
    current_uid = document["current"]
    items = {item["uid"]: item for item in document["items"]}
    if current_uid not in items:
        raise SystemExit(f"current profile {current_uid!r} is missing from profiles.yaml")
    merge_uid = (items[current_uid].get("option") or {}).get("merge")
    if not merge_uid:
        raise SystemExit(
            f"the selected profile {current_uid!r} has no merge profile attached; "
            "create one in Clash Verge Rev first"
        )
    if merge_uid not in items:
        raise SystemExit(f"merge profile {merge_uid!r} is missing from profiles.yaml")
    return items[merge_uid]["file"]


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def render_managed_block(listeners: tuple[Listener, ...]) -> str:
    """Render the block through a YAML dumper, so node names cannot break it."""
    document = {
        "listeners": [
            {
                "name": listener.name,
                "type": "mixed",
                "listen": "127.0.0.1",
                "port": listener.port,
                "proxy": listener.node,
            }
            for listener in listeners
        ]
    }
    dumped = yaml.safe_dump(
        document, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000
    )
    return f"{MANAGED_BLOCK_MARKER}\n{dumped}"


def prepare_managed_content(path: Path, block: str) -> str:
    """Return the new file content, or refuse before anything is written."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    marker_index = existing.find(MANAGED_BLOCK_MARKER)
    head = (existing[:marker_index] if marker_index != -1 else existing).rstrip("\n")
    if TOP_LEVEL_LISTENERS_PATTERN.search(head):
        raise SystemExit(
            f"refusing to write {path}: it already declares a top-level 'listeners:' section "
            "outside the managed block. Merge the two by hand first."
        )
    content = f"{head}\n\n{block}" if head else block
    _require_parseable(content, path)
    return content


def _require_parseable(content: str, path: Path) -> None:
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise SystemExit(f"refusing to write {path}: the result is not valid YAML ({error})") from error
    if not isinstance(document, dict) or not isinstance(document.get("listeners"), list):
        raise SystemExit(f"refusing to write {path}: the result has no usable 'listeners' list")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _backup_all(layout: VergeLayout) -> list[tuple[Path, Path]]:
    return [
        (path, _backup(path))
        for path in (layout.merge_file, layout.runtime_config)
        if path.is_file()
    ]


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".keymux-{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def _restore_and_reload(
    backups: list[tuple[Path, Path]], runtime_config: Path, secret: str
) -> None:
    for original, backup in backups:
        shutil.copy2(backup, original)
    mihomo_controller.reload_runtime_config(runtime_config, secret)
    print("restored the previous configuration from:", file=sys.stderr)
    for _, backup in backups:
        print(f"  {backup}", file=sys.stderr)


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


def _report_egress_ip(listeners: tuple[Listener, ...]) -> int:
    failures = 0
    observed: dict[str, str] = {}
    for listener in listeners:
        address = probe_egress(listener)
        observed[listener.node] = address
        print(f"{listener.port}  {listener.node}  ->  {address}")
        if not _looks_like_ip(address):
            failures += 1
    if len(set(observed.values())) < len(listeners):
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
        if not parts[0].strip():
            raise SystemExit("listener names must not be empty")
        listeners.append(Listener(name=parts[0], port=_port(parts[1]), node=" ".join(parts[2:])))
    if len({listener.port for listener in listeners}) != len(listeners):
        raise SystemExit("listener ports must be unique")
    if len({listener.name for listener in listeners}) != len(listeners):
        raise SystemExit("listener names must be unique")
    return tuple(listeners)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise SystemExit(f"listener port {value!r} is not an integer") from error
    if not MIN_PORT <= port <= MAX_PORT:
        raise SystemExit(f"listener port {port} is outside {MIN_PORT}-{MAX_PORT}")
    return port


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listener", nargs="+", action="append", metavar="NAME PORT NODE")
    parser.add_argument("--no-verify", action="store_true", help="skip the egress IP probe")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
