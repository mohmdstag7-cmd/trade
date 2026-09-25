"""Cross-checks between pyproject.toml and app/__version__.py.

release-please bumps both files; this test fails if they ever drift apart
(SPEC H3-7: app/__version__.py is the single source of truth).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.__version__ import __version__


def test_version_sources_are_in_sync() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__


def test_manifest_version_is_in_sync() -> None:
    import json

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert manifest["."] == __version__
