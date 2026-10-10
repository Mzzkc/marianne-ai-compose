#!/usr/bin/env python3
"""Copy the class-based hello score into its workspace with absolute asset paths.

The hello-setup score has already created and checked the machine's class map.
This copy gives the chained score a stable workspace and lets assets resolve
when it runs outside the examples directory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


def _absolute_asset_paths(node: Any, assets: Path) -> Any:
    if isinstance(node, str):
        return node.replace("{{ workspace }}/../../examples/getting-started/assets", str(assets))
    if isinstance(node, dict):
        return {key: _absolute_asset_paths(value, assets) for key, value in node.items()}
    if isinstance(node, list):
        return [_absolute_asset_paths(value, assets) for value in node]
    return node


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: resolve-hello.py <workspace-dir> <hello-template.yaml>", file=sys.stderr)
        return 2
    workspace = Path(sys.argv[1]).resolve()
    template = Path(sys.argv[2]).resolve()
    try:
        config = yaml.safe_load(template.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or config.get("instrument") != "workhorse":
            raise ValueError("hello template must use the workhorse class")
        config["workspace"] = str(workspace)
        config = _absolute_asset_paths(config, template.parent / "assets")
        workspace.mkdir(parents=True, exist_ok=True)
        output = workspace / "hello-resolved.yaml"
        output.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Cannot prepare hello score: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared {output} with instrument: workhorse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
