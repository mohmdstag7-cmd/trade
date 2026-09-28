"""GitHub release asset access for the updater (public repos, no token).

Uses plain HTTPS asset URLs — no REST API — so the updater never hits the
per-IP API rate limit and works without credentials:

- latest manifest:  ``.../releases/latest/download/manifest.json``
- delta package:    ``.../releases/download/v<new>/delta-v<old>-to-v<new>.zip``
- full portable:    ``.../releases/latest/download/MT5TradingWorkstation-portable.zip``

Network hardening (each item exists because the update failed in the
field — the app fetched nothing at all from GitHub):

- **System proxy**: ``httpx`` only honours ``HTTP(S)_PROXY`` env vars. On
  Windows, VPN/proxy tools (v2ray, Clash, …) configure the WinINET
  *registry* proxy which browsers use but httpx ignores — for users in
  Iran GitHub is often unreachable without it. :func:`detect_proxy`
  reads the registry (via :func:`urllib.request.getproxies`) and the
  client is built with an explicit ``proxy=``.
- **Retries**: 3 attempts with a short backoff for the manifest probe and
  the download; release assets are served by a CDN that occasionally
  resets connections mid-transfer.
- **Resumable downloads**: a completed-but-unverified ``*.part`` file is
  continued with an HTTP ``Range`` request instead of restarting a 20 MB
  transfer from zero on a flaky connection.

All HTTP I/O goes through :class:`ReleaseFetcher` (httpx) so tests can
inject a fake.
"""

from __future__ import annotations

import pathlib
import time
import urllib.request
from collections.abc import Callable
from typing import Any, cast

import httpx
from loguru import logger

REPO_SLUG = "mohmdstag7-cmd/trade"
_USER_AGENT = "MT5TradingWorkstation-Updater"
_CONNECT_TIMEOUT_S = 20.0
_READ_TIMEOUT_S = 120.0
_DOWNLOAD_READ_TIMEOUT_S = 180.0
_RETRIES = 3
_RETRY_DELAYS_S = (1.5, 4.0)

ProgressFn = Callable[[int, int | None], None]


class NetworkUnreachable(RuntimeError):
    """GitHub could not be reached (DNS, connection, timeout, proxy).

    Carries a stable marker (``github unreachable``) so the UI can show
    a translated hint instead of a raw ``httpx.ConnectError`` repr.
    """

    MARKER = "github unreachable"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.MARKER}: {detail}")


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


def _strip_userinfo(url: str) -> str:
    """Mask any user:password@ portion before logging a proxy URL."""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    except ValueError:
        return "<proxy>"
    except Exception:
        return "<proxy>"


def detect_proxy() -> str | None:
    """Proxy URL for GitHub traffic, honouring the Windows system proxy.

    ``urllib.request.getproxies()`` reads the WinINET registry on Windows
    (what VPN/proxy tools configure) plus ``HTTP(S)_PROXY`` env vars
    everywhere else. Returns ``None`` when no proxy is configured. Values
    without a scheme (``127.0.0.1:8080``) get ``http://`` — proxies for
    HTTPS targets are usually plain HTTP CONNECT relays.
    """
    try:
        proxies = urllib.request.getproxies()
    except Exception:  # pragma: no cover - defensive: registry oddities
        return None
    for key in ("https", "http"):
        url = proxies.get(key)
        if not url:
            continue
        url = url.strip()
        if not url:
            continue
        if "://" not in url:
            url = f"http://{url}"
        return url
    return None


def _translate_http_error(exc: Exception) -> Exception:
    """Map transport failures to :class:`NetworkUnreachable` (HTTP status
    errors pass through untouched — a 404 is not a network problem)."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return NetworkUnreachable(repr(exc))
    if isinstance(exc, httpx.PoolTimeout):
        return NetworkUnreachable(repr(exc))
    if isinstance(exc, httpx.RemoteProtocolError) and "Server disconnected" in str(exc):
        return NetworkUnreachable(repr(exc))
    return exc


class ReleaseFetcher:
    """Thin httpx wrapper: JSON GET + streamed resumable download."""

    def __init__(
        self,
        timeout_s: float = _CONNECT_TIMEOUT_S,
        proxy: str | None = None,
    ) -> None:
        self._connect_timeout_s = timeout_s
        self._proxy = proxy if proxy is not None else detect_proxy()
        if self._proxy:
            logger.info(
                "updates net: using proxy {}",
                _strip_userinfo(self._proxy),
            )

    def _client(self, *, read_timeout_s: float = _READ_TIMEOUT_S) -> httpx.Client:
        timeout = httpx.Timeout(
            self._connect_timeout_s,
            read=read_timeout_s,
            write=60.0,
            pool=self._connect_timeout_s,
        )
        return httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            proxy=self._proxy,
        )

    def _retry(self, description: str, attempt: Callable[[], Any]) -> Any:
        """Run ``attempt`` up to ``_RETRIES`` times with a small backoff."""
        last: Exception | None = None
        for tries_left in range(_RETRIES - 1, -1, -1):
            try:
                return attempt()
            except Exception as exc:
                last = exc
                translated = _translate_http_error(exc)
                if translated is not exc:
                    last = translated
                import httpx as _httpx

                if isinstance(exc, _httpx.HTTPStatusError):
                    # 404/410 are definitive answers, not transport
                    # faults - retrying only delays the fallback.
                    break
                if tries_left == 0:
                    break
                delay = _RETRY_DELAYS_S[min(len(_RETRY_DELAYS_S) - 1, _RETRIES - 1 - tries_left)]
                logger.warning(
                    "updates net: {} failed ({!r}) — retrying in {:.0f}s",
                    description,
                    exc,
                    delay,
                )
                time.sleep(delay)
        assert last is not None
        raise last

    def fetch_json(self, url: str) -> dict[str, Any]:
        """GET a JSON document (manifest)."""

        def attempt() -> dict[str, Any]:
            with self._client() as client:
                response = client.get(url)
                response.raise_for_status()
                data: object = response.json()
            if not isinstance(data, dict):
                raise ValueError(f"unexpected manifest payload from {url}")
            return data

        try:
            result = self._retry(f"fetch {url.rsplit('/', 1)[-1]}", attempt)
        except Exception as exc:
            raise _translate_http_error(exc) from exc
        return cast(dict[str, Any], result)

    def exists(self, url: str) -> bool:
        """HEAD probe used to detect whether a delta asset was published."""

        def attempt() -> bool:
            with self._client() as client:
                response = client.head(url)
            return response.status_code == 200

        try:
            return bool(self._retry(f"probe {url.rsplit('/', 1)[-1]}", attempt))
        except httpx.HTTPStatusError:
            return False
        except Exception:
            return False

    def download(self, url: str, destination: pathlib.Path, progress: ProgressFn) -> None:
        """Stream ``url`` to ``destination`` (bytes on disk, then rename).

        A leftover ``*.part`` file is continued via HTTP ``Range`` when
        the server supports it; otherwise the transfer restarts cleanly.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._retry(
                f"download {url.rsplit('/', 1)[-1]}",
                lambda: self._download_once(url, destination, progress),
            )
        except Exception as exc:
            raise _translate_http_error(exc) from exc

    def _download_once(self, url: str, destination: pathlib.Path, progress: ProgressFn) -> None:
        tmp = destination.with_suffix(destination.suffix + ".part")
        resume_from = tmp.stat().st_size if tmp.is_file() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else {}
        with (
            self._client(read_timeout_s=_DOWNLOAD_READ_TIMEOUT_S) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            if resume_from > 0 and response.status_code != 206:
                # server ignored the Range: start over
                resume_from = 0
            response.raise_for_status()
            total_header = response.headers.get("Content-Length")
            chunk_total = int(total_header) if total_header and total_header.isdigit() else None
            total: int | None = None
            if chunk_total is not None:
                total = resume_from + chunk_total
            done = resume_from
            if resume_from > 0:
                logger.info("updates net: resuming download at {} bytes", resume_from)
                progress(done, total)
            mode = "ab" if resume_from > 0 else "wb"
            with tmp.open(mode) as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    done += len(chunk)
                    progress(done, total)
        tmp.replace(destination)
