"""Update orchestration: check → download → verify → stage → apply.

The :class:`UpdateService` is synchronous and framework-free. Errors are
typed (:class:`UpdaterError`) so the UI worker can present them. Every
downloaded byte is verified against the *new* release manifest before it
may touch the staging area — and nothing touches the installed tree until
the user restarts, at which point a generated ``apply_update.bat``
(a) waits for this process to exit, (b) copies the staged tree over the
app folder, (c) deletes the files the new release removed, and
(d) relaunches the app.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import zipfile
from dataclasses import dataclass
from typing import Any

from app.updater.github import (
    REPO_SLUG,
    ReleaseFetcher,
    delta_zip_url,
    full_zip_url,
    latest_manifest_url,
    releases_page_url,
)
from app.updater.manifest import (
    diff_manifests,
    load_manifest,
    manifest_version,
    staging_root,
    verify_tree,
)
from app.updater.version import is_newer

#: Payload file inside a delta zip describing removed files + metadata.
_DELTA_META = "__delta__.json"
_EXE_NAME = "MT5TradingWorkstation.exe"


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    """What the updater intends to do, decided by :meth:`check`."""

    current_version: str
    new_version: str
    mode: str  # "delta" or "full"
    release_page: str

    @property
    def mode_key(self) -> str:
        """i18n key describing the mode."""
        return "updates.mode.delta" if self.mode == "delta" else "updates.mode.full"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a version check."""

    available: bool
    plan: UpdatePlan | None
    error: str = ""


class UpdaterError(RuntimeError):
    """Raised for any updater failure the UI should surface."""


def app_install_dir() -> pathlib.Path:
    """Directory of the installed (frozen) app; repo root in dev mode."""
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parents[2]


def apply_script_payload(
    app_dir: pathlib.Path,
    staging: pathlib.Path,
    pid: int,
    removed: tuple[str, ...],
) -> str:
    """Render the Windows batch script that performs the swap on restart.

    Kept as a pure function so tests can assert on the exact commands
    without touching the filesystem.
    """
    lines = [
        "@echo off",
        "setlocal",
        f'set "APP_DIR={app_dir}"',
        f'set "STAGE={staging}"',
        f'set "TARGET_PID={pid}"',
        "set /a tries=0",
        ":waitloop",
        'tasklist /FI "PID eq %TARGET_PID%" 2>nul | find "%TARGET_PID%" >nul',
        "if not errorlevel 1 (",
        "  set /a tries+=1",
        "  if %tries% GEQ 30 goto apply",
        "  timeout /t 1 /nobreak >nul",
        "  goto waitloop",
        ")",
        ":apply",
    ]
    lines.extend(f'del /f /q "%APP_DIR%\\{rel}" 2>nul' for rel in removed)
    lines += [
        'robocopy "%STAGE%" "%APP_DIR%" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1',
        "if %ERRORLEVEL% GEQ 8 exit /b %ERRORLEVEL%",
        'rmdir /s /q "%STAGE%"',
        f'start "" "%APP_DIR%\\{_EXE_NAME}"',
        "endlocal",
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


class UpdateService:
    """End-to-end delta updater for the portable Windows build."""

    def __init__(
        self,
        *,
        current_version: str,
        app_dir: pathlib.Path | None = None,
        download_dir: pathlib.Path | None = None,
        fetcher: ReleaseFetcher | None = None,
        repo: str = "",
    ) -> None:
        self._current_version = current_version
        self._app_dir = app_dir or app_install_dir()
        self._repo = repo or REPO_SLUG
        self._fetcher = fetcher or ReleaseFetcher()
        data_root = download_dir or (self._app_dir.parent / "updates")
        self._download_dir = data_root

    # -- properties ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """False in dev mode (running from source) — nothing to update."""
        return getattr(sys, "frozen", False)

    @property
    def release_page(self) -> str:
        return releases_page_url(self._repo)

    @property
    def download_dir(self) -> pathlib.Path:
        return self._download_dir

    # -- steps ---------------------------------------------------------------
    def check(self) -> CheckResult:
        """Compare the local install against the newest release manifest."""
        if not self.enabled:
            return CheckResult(available=False, plan=None)
        try:
            remote = self._fetcher.fetch_json(latest_manifest_url(self._repo))
        except Exception as exc:
            return CheckResult(available=False, plan=None, error=str(exc))
        new_version = manifest_version(remote)
        if not new_version:
            return CheckResult(available=False, plan=None, error="manifest without version")
        if not is_newer(new_version, self._current_version):
            return CheckResult(available=False, plan=None)

        local = load_manifest(self._app_dir)
        mode = "delta"
        if local is None:
            mode = "full"
        else:
            local_version = manifest_version(local) or self._current_version
            if not self._fetcher.exists(delta_zip_url(new_version, local_version, self._repo)):
                mode = "full"
        plan = UpdatePlan(
            current_version=self._current_version,
            new_version=new_version,
            mode=mode,
            release_page=self.release_page,
        )
        return CheckResult(available=True, plan=plan)

    def download_and_stage(
        self,
        plan: UpdatePlan,
        progress: Any = None,
    ) -> pathlib.Path:
        """Download (delta or full), verify and unpack into staging.

        ``progress(received_bytes, total_or_None)`` is invoked during the
        download. Returns the staging directory. Raises :class:`UpdaterError`
        on any verification failure (staging is wiped in that case).
        """
        report = progress if progress is not None else (lambda done, total: None)
        self._download_dir.mkdir(parents=True, exist_ok=True)
        staging = staging_root(self._app_dir, plan.new_version)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            if plan.mode == "delta":
                self._stage_delta(plan, staging, report)
            else:
                self._stage_full(plan, staging, report)
        except UpdaterError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise UpdaterError(str(exc)) from exc
        self._write_apply_script(plan, staging)
        return staging

    # -- staging internals -------------------------------------------------------
    def _stage_delta(
        self,
        plan: UpdatePlan,
        staging: pathlib.Path,
        report: Any,
    ) -> None:
        local = load_manifest(self._app_dir)
        if local is None:
            raise UpdaterError("local manifest missing — full update required")
        local_version = manifest_version(local) or self._current_version
        remote = self._fetcher.fetch_json(latest_manifest_url(self._repo))
        diff = diff_manifests(local, remote)
        if not diff.has_changes:
            raise UpdaterError("no changes detected between manifests")

        zip_path = self._download_dir / f"delta-v{local_version}-to-v{plan.new_version}.zip"
        self._fetcher.download(
            delta_zip_url(plan.new_version, local_version, self._repo),
            zip_path,
            report,
        )
        with zipfile.ZipFile(zip_path) as bundle:
            bundle.extractall(staging)

        meta_path = staging / _DELTA_META
        removed: tuple[str, ...] = ()
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                removed = tuple(str(p) for p in meta.get("removed", ()))
            except (OSError, json.JSONDecodeError):
                removed = ()
            meta_path.unlink()
        problems = verify_tree(staging, remote, subset=set(diff.changed))
        if problems:
            raise UpdaterError(f"verification failed for: {', '.join(problems[:5])}")
        self._write_removed_list(plan, removed)

    def _stage_full(
        self,
        plan: UpdatePlan,
        staging: pathlib.Path,
        report: Any,
    ) -> None:
        remote = self._fetcher.fetch_json(latest_manifest_url(self._repo))
        zip_path = self._download_dir / f"portable-v{plan.new_version}.zip"
        self._fetcher.download(full_zip_url(self._repo), zip_path, report)
        with zipfile.ZipFile(zip_path) as bundle:
            names = bundle.namelist()
            top = {name.split("/", 1)[0] for name in names if name.strip()}
            root_dir = top.pop() if len(top) == 1 else ""
            bundle.extractall(staging)
        inner = staging / root_dir if root_dir else staging
        if inner is not staging:
            # flatten: move the extracted folder's content up into staging
            for child in list(inner.iterdir()):
                shutil.move(str(child), str(staging / child.name))
            inner.rmdir()
        problems = verify_tree(staging, remote)
        if problems:
            raise UpdaterError(f"verification failed for: {', '.join(problems[:5])}")

    # -- apply helpers --------------------------------------------------------------
    def _removed_list_path(self, new_version: str) -> pathlib.Path:
        return staging_parent(self._app_dir) / f".removed_{new_version}.txt"

    def _write_removed_list(self, plan: UpdatePlan, removed: tuple[str, ...]) -> None:
        """Persist deletions next to (not inside) the staging tree."""
        self._removed_list_path(plan.new_version).write_text("\n".join(removed), encoding="utf-8")

    def _read_removed_list(self, new_version: str) -> tuple[str, ...]:
        marker = self._removed_list_path(new_version)
        if not marker.is_file():
            return ()
        lines = [
            line.strip().replace("\\", "/")
            for line in marker.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return tuple(lines)

    def _write_apply_script(self, plan: UpdatePlan, staging: pathlib.Path) -> None:
        removed = self._read_removed_list(plan.new_version)
        script = staging.parent / f"apply_update_{plan.new_version}.bat"
        payload = apply_script_payload(self._app_dir, staging, os.getpid(), removed)
        script.write_text(payload, encoding="utf-8")

    def apply_script_path(self, new_version: str) -> pathlib.Path:
        """Path of the prepared apply script (written right after staging)."""
        return staging_parent(self._app_dir) / f"apply_update_{new_version}.bat"


def staging_parent(app_dir: pathlib.Path) -> pathlib.Path:
    """Directory holding staging trees, apply scripts and removed-lists."""
    return app_dir.parent


def _noop_progress(done: int, total: int | None) -> None:  # pragma: no cover
    _ = done, total
