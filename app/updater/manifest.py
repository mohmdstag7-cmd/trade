"""Release manifests: the exact content fingerprint of one portable build.

A manifest is a JSON document of the shape::

    {
      "version": "0.6.0",
      "generated_at": "2026-09-26T12:00:00Z",
      "files": {
        "MT5TradingWorkstation.exe": {"sha256": "...", "size": 123},
        "_internal/...": {"sha256": "...", "size": 456}
      }
    }

CI embeds it in the portable folder *and* attaches it to the release.
The updater ships the same structure: local manifest describes the
installed tree, remote manifest describes the target tree, and the diff
tells the delta builder / downloader what to move.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any

from app.updater.version import parse_version

MANIFEST_NAME = "manifest.json"
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file's fingerprint."""

    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ManifestDiff:
    """Result of comparing two manifests."""

    #: Paths present in both but with a different fingerprint, plus new ones.
    changed: tuple[str, ...]
    #: Paths present locally but gone in the new manifest.
    removed: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.changed or self.removed)


def sha256_file(path: pathlib.Path) -> str:
    """Stream a file through SHA-256 (constant memory for big DLLs)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build_manifest(root: pathlib.Path, version: str) -> dict[str, Any]:
    """Walk ``root`` and return the manifest dict (all regular files)."""
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST_NAME:
            continue
        files[rel] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    return {
        "version": version,
        "generated_at": "",
        "files": files,
    }


def load_manifest(root: pathlib.Path) -> dict[str, Any] | None:
    """Read the manifest embedded in an installed tree (None when absent)."""
    path = root / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def manifest_version(manifest: dict[str, Any]) -> str:
    """Version string recorded in a manifest ("" when missing)."""
    value = manifest.get("version")
    return str(value) if value else ""


def _entries(manifest: dict[str, Any]) -> dict[str, FileEntry]:
    out: dict[str, FileEntry] = {}
    for rel, meta in manifest.get("files", {}).items():
        if isinstance(meta, dict):
            out[str(rel)] = FileEntry(
                sha256=str(meta.get("sha256", "")), size=int(meta.get("size", 0))
            )
    return out


def diff_manifests(old: dict[str, Any], new: dict[str, Any]) -> ManifestDiff:
    """Files to fetch (changed/new) and files to delete."""
    old_entries = _entries(old)
    new_entries = _entries(new)
    changed = [rel for rel, entry in new_entries.items() if old_entries.get(rel) != entry]
    removed = sorted(set(old_entries) - set(new_entries))
    return ManifestDiff(changed=tuple(sorted(changed)), removed=tuple(removed))


def verify_tree(
    root: pathlib.Path,
    manifest: dict[str, Any],
    *,
    subset: set[str] | None = None,
) -> list[str]:
    """Return the paths whose content does NOT match the manifest.

    With ``subset`` only those paths are checked (delta staging); with the
    default ``None`` every manifest entry must be present and correct.
    """
    problems: list[str] = []
    entries = _entries(manifest)
    targets = entries if subset is None else {k: v for k, v in entries.items() if k in subset}
    for rel, entry in sorted(targets.items()):
        path = root / rel
        if not path.is_file():
            problems.append(rel)
            continue
        if path.stat().st_size != entry.size:
            problems.append(rel)
            continue
        if sha256_file(path) != entry.sha256:
            problems.append(rel)
    return problems


_SAFE_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)*(?:[-+][\w.]*)?$")


def staging_root(app_dir: pathlib.Path, new_version: str) -> pathlib.Path:
    """Sibling staging directory (same drive ⇒ fast moves, no recursion).

    The RAW version string is interpolated into a directory name, so it
    must be a strict version token: path separators or ``..`` segments
    would turn a tampered manifest version into a path-traversal write.
    """
    parse_version(new_version)  # numeric sanity (suffixes allowed)
    if not _SAFE_VERSION_RE.match(new_version):
        msg = f"unsafe version string for staging: {new_version!r}"
        raise ValueError(msg)
    return app_dir.parent / f"{app_dir.name}.update-{new_version}"
