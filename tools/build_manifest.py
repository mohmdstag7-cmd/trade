"""Build the release manifest for a packaged portable tree.

Usage (CI, after PyInstaller):
    python tools/build_manifest.py dist/MT5TradingWorkstation --version 0.6.0

Writes ``manifest.json`` INTO the tree (so the portable zip ships it) and
prints the number of files hashed. Runs on any OS; Python 3.11 stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.updater.manifest import MANIFEST_NAME, build_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=pathlib.Path, help="portable app folder")
    parser.add_argument("--version", required=True, help="release version, e.g. 0.6.0")
    args = parser.parse_args(argv)

    tree = args.tree.resolve()
    if not tree.is_dir():
        print(f"ERROR: {tree} is not a directory", file=sys.stderr)
        return 2

    manifest = build_manifest(tree, args.version)
    manifest["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    target = tree / MANIFEST_NAME
    target.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {len(manifest['files'])} files -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
