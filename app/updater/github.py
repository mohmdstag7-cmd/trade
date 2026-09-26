"""GitHub release asset access for the updater (public repos, no token).

Uses plain HTTPS asset URLs — no REST API — so the updater never hits the
per-IP API rate limit and works without credentials:

- latest manifest:  ``.../releases/latest/download/manifest.json``
- delta package:    ``.../releases/download/v<new>/delta-v<old>-to-v<new>.zip``
- full portable:    ``.../releases/latest/download/MT5TradingWorkstation-portable.zip``

All HTTP I/O goes through :class:`ReleaseFetcher` (httpx) so tests can
inject a fake.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Any

import httpx

REPO_SLUG = "mohmdstag7-cmd/trade"
_USER_AGENT = "MT5TradingWorkstation-Updater"
_TIMEOUT_S = 30.0

ProgressFn = Callable[[int, int | None], None]


def latest_manifest_url(repo: str = REPO_SLUG) -> str:
    """URL that always resolves to the newest release's manifest.json."""
    return f"https://github.com/{repo}/releases/latest/download/manifest.json"


def full_zip_url(repo: str = REPO_SLUG) -> str:
    """URL of the newest release's full portable zip."""
    return f"https://github.com/{repo}/releases/latest/download/MT5TradingWorkstation-portable.zip"


def delta_zip_url(new_version: str, current_version: str, repo: str = REPO_SLUG) -> str:
    """URL of the delta package between two versions (404 when absent)."""
    return (
        f"https://github.com/{repo}/releases/download/"
        f"v{new_version}/delta-v{current_version}-to-v{new_version}.zip"
    )


def releases_page_url(repo: str = REPO_SLUG) -> str:
    """Human-facing releases page (opened by the 'what's new' button)."""
    return f"https://github.com/{repo}/releases"


class ReleaseFetcher:
    """Thin httpx wrapper: JSON GET + streamed download with progress."""

    def __init__(self, timeout_s: float = _TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def _client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=True,
            timeout=self._timeout_s,
            headers={"User-Agent": _USER_AGENT},
        )

    def fetch_json(self, url: str) -> dict[str, Any]:
        """GET a JSON document (manifest)."""
        with self._client() as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected manifest payload from {url}")
        return data

    def exists(self, url: str) -> bool:
        """HEAD probe used to detect whether a delta asset was published."""
        try:
            with self._client() as client:
                response = client.head(url)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def download(self, url: str, destination: pathlib.Path, progress: ProgressFn) -> None:
        """Stream ``url`` to ``destination`` (bytes on disk, then rename)."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        with self._client() as client, client.stream("GET", url) as response:
            response.raise_for_status()
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            done = 0
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    done += len(chunk)
                    progress(done, total)
        tmp.replace(destination)
