"""Build a delta update package between two portable builds.

Usage (CI, on the release runner):
    python tools/build_delta.py \
        --prev-dir prev/MT5TradingWorkstation \
        --new-dir dist/MT5TradingWorkstation \
        --from-version 0.5.0 --to-version 0.6.0 \
        --out delta-v0.5.0-to-v0.6.0.zip

The delta zip contains only files whose SHA-256 changed (or that are
new), at their original relative paths, plus a ``__delta__.json`` payload
listing removed files. The in-app updater verifies every staged file
against the new release manifest before installing.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.updater.manifest import build_manifest, diff_manifests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prev-dir", type=pathlib.Path, required=True)
    parser.add_argument("--new-dir", type=pathlib.Path, required=True)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)

    prev = args.prev_dir.resolve()
    new = args.new_dir.resolve()
    for path in (prev, new):
        if not path.is_dir():
            print(f"ERROR: {path} is not a directory", file=sys.stderr)
            return 2

    old_manifest = build_manifest(prev, args.from_version)
    new_manifest = build_manifest(new, args.to_version)
    diff = diff_manifests(old_manifest, new_manifest)

    if not diff.has_changes:
        print("delta: no differences — nothing to do")
        return 3

    written = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as bundle:
        for rel in diff.changed:
            bundle.write(new / rel, rel)
            written += 1
        bundle.writestr(
            "__delta__.json",
            json.dumps(
                {
                    "from": args.from_version,
                    "to": args.to_version,
                    "removed": list(diff.removed),
                    "changed_count": len(diff.changed),
                },
                indent=1,
            ),
        )
    size_kb = args.out.stat().st_size / 1024
    print(
        f"delta: {written} changed, {len(diff.removed)} removed → "
        f"{args.out.name} ({size_kb:.0f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
