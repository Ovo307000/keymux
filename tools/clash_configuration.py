"""Read, validate and write the managed ``listeners:`` block of a Clash file.

Kept apart from :mod:`setup_clash_listeners` because the failure mode is
different in kind: a bad block here stops Mihomo from loading at all, so the
result is parsed before it is ever written.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MANAGED_BLOCK_MARKER = "# --- keymux managed listeners (generated, keep this block last) ---"

# A top-level key only: nested keys are indented, and the managed block itself
# is cut off before this is applied.
TOP_LEVEL_LISTENERS_PATTERN = re.compile(r"(?m)^listeners\s*:")


def render_managed_block(listeners) -> str:
    """Render the block through a YAML dumper, so node names cannot break it.

    ``listeners`` is any iterable of objects with ``name``, ``port`` and ``node``.
    """
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
    require_parseable(content, path)
    return content


def require_parseable(content: str, path: Path) -> None:
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise SystemExit(f"refusing to write {path}: the result is not valid YAML ({error})") from error
    if not isinstance(document, dict) or not isinstance(document.get("listeners"), list):
        raise SystemExit(f"refusing to write {path}: the result has no usable 'listeners' list")
