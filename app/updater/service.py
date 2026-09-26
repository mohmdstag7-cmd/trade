"""Update orchestration: check → download → verify → stage → apply.

The :class:`UpdateService` is synchronous and framework-free. Errors are
typed (:class:`UpdaterError`) so the UI worker can present them. Every
downloaded byte is verified against the *new* release manifest before it
may touch the staging area — and nothing touches the installed tree until
the user restarts, at which point a generated ``apply_update_<v>.bat``
(a) waits for this process to exit (ping-based delay — ``timeout`` cannot
run in a detached console-less script), (b) force-kills a wedged process
after a 90 s grace period, (c) copies the staged tree over the app
folder, (d) deletes the files the new release removed, (e) relaunches the
app, and (f) logs every step to ``%LOCALAPPDATA%\\MT5TradingWorkstation\\
update-apply.log``. On robocopy failure the staging tree is kept so the
next start offers a retry instead of a silent re-download.
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
from app.updater.version import is_newer, parse_version

#: Payload file inside a delta zip describing removed files + metadata.
_DELTA_META = "__delta__.json"
_EXE_NAME = "MT5TradingWorkstation.exe"
_APPLY_LOG_DIRNAME = "MT5TradingWorkstation"
_APPLY_LOG_NAME = "update-apply.log"

#: Windows process-creation flags: detached so the batch script survives
#: this process exiting, new process group so Ctrl-C is never inherited.
_DETACHED = 0x00000008 | 0x00000200


def ascii_safe_path(path: pathlib.Path) -> pathlib.Path:
    """Best-effort ASCII-only form of ``path`` (Windows 8.3 short name).

    ``cmd.exe`` parses batch files with the legacy OEM codepage, so a
    Persian (or any non-ASCII) user profile name inside the script gets
    mangled and robocopy never finds the folder. ``GetShortPathNameW``
    returns a pure-ASCII alias when 8.3 names are enabled on the volume;
    when they are not (or on other platforms) the path is returned
    unchanged and the caller falls back to writing the script as UTF-8
    with ``chcp 65001``.
    """
    if os.name != "nt":
        return path
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:  # pragma: no cover - non-Windows
            return path
        buffer = ctypes.create_unicode_buffer(32768)
        size = windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        if 0 < size < len(buffer) and buffer.value.isascii():
            return pathlib.Path(buffer.value)
    except OSError:  # pragma: no cover - defensive
        pass
    return path


def app_dir_writable(app_dir: pathlib.Path) -> bool:
    """True when the install tree accepts writes without elevation.

    ``os.access`` alone lies for UAC-protected directories, so a real
    touch probe is used. Installer installs under ``Program Files`` are
    not writable → the apply script must run elevated.
    """
    try:
        probe = app_dir / ".updater_write_probe"
        probe.write_bytes(b"x")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def apply_log_path() -> pathlib.Path | None:
    """Where the apply script writes its log (None off Windows)."""
    if os.name != "nt":
        return None
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return pathlib.Path(base) / _APPLY_LOG_DIRNAME / _APPLY_LOG_NAME


def read_apply_log_tail(lines: int = 30) -> str:
    """Last ``lines`` of the apply log ("" when unavailable)."""
    path = apply_log_path()
    if path is None or not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


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

    Design notes (each one exists because the first version failed in the
    field — the update downloaded but never installed):

    - the script runs DETACHED (no console). ``timeout /t 1`` REQUIRES a
      console and dies instantly there, so the wait loop must delay with
      ``ping -n 2 127.0.0.1`` instead — otherwise robocopy races the
      still-running app and every locked file fails to copy;
    - the retry counter is read with delayed expansion (``!tries!``) —
      ``%tries%`` inside a block expands at parse time and never counts;
    - every step appends to a log under ``%LOCALAPPDATA%`` so failures
      are diagnosable from the app;
    - if robocopy still fails (exit ≥ 8) the staging tree is KEPT so the
      next start can offer a retry, and the (old) app is relaunched so
      the user is never left with nothing;
    - if the app refuses to exit within 90 s it is force-killed — a
      crashed teardown must not wedge the updater forever.
    """
    lines = [
        "@echo off",
        "setlocal enabledelayedexpansion",
        f'set "APP_DIR={app_dir}"',
        f'set "STAGE={staging}"',
        f'set "TARGET_PID={pid}"',
        'set "LOGDIR=%LOCALAPPDATA%\\' + _APPLY_LOG_DIRNAME + '"',
        'if not exist "%LOGDIR%" mkdir "%LOGDIR%" 2>nul',
        'set "LOG=%LOGDIR%\\' + _APPLY_LOG_NAME + '"',
        'echo [%date% %time%] apply start pid=%TARGET_PID% >> "%LOG%"',
        "set /a tries=0",
        ":waitloop",
        'tasklist /FI "PID eq %TARGET_PID%" 2>nul | find "%TARGET_PID%" >nul',
        "if errorlevel 1 goto waitdone",
        "set /a tries+=1",
        "if !tries! GEQ 90 goto forcekill",
        "ping -n 2 127.0.0.1 >nul",
        "goto waitloop",
        ":forcekill",
        'echo app still running after 90s — force closing >> "%LOG%"',
        'taskkill /PID %TARGET_PID% /F >nul 2>&1',
        "ping -n 4 127.0.0.1 >nul",
        ":waitdone",
        'echo app exited — applying staged tree >> "%LOG%"',
    ]
    lines.extend(
        f'del /f /q "%APP_DIR%\\{rel.replace("/", chr(92))}" 2>nul' for rel in removed
    )
    lines += [
        'robocopy "%STAGE%" "%APP_DIR%" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >> "%LOG%" 2>&1',
        "set rc=!errorlevel!",
        'echo robocopy exit=!rc! >> "%LOG%"',
        "if !rc! GEQ 8 goto fail",
        'rmdir /s /q "%STAGE%" 2>nul',
        'cd /d "%APP_DIR%"',
        f'start "" "%APP_DIR%\\{_EXE_NAME}"',
        'echo apply OK >> "%LOG%"',
        "endlocal",
        "exit /b 0",
        ":fail",
        'echo apply FAILED — staging kept for retry >> "%LOG%"',
        f'start "" "%APP_DIR%\\{_EXE_NAME}"',
        "endlocal",
        "exit /b !rc!",
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
    def app_dir(self) -> pathlib.Path:
        """The installed app tree this service updates."""
        return self._app_dir

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
        self.cleanup_stale_artifacts(keep_version=plan.new_version)
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
        payload = apply_script_payload(
            ascii_safe_path(self._app_dir),
            ascii_safe_path(staging),
            os.getpid(),
            removed,
        )
        if not payload.isascii():
            # 8.3 names unavailable for a non-ASCII install path (e.g. a
            # Persian user profile): cmd must read the file as UTF-8.
            payload = payload.replace("@echo off", "@echo off\r\nchcp 65001 >nul", 1)
        script.write_text(payload, encoding="utf-8")

    def pending_staged_update(self) -> str | None:
        """Version of a fully staged update awaiting restart, if any.

        A staging tree counts as usable only when its apply script is
        present AND its embedded manifest matches that version AND the
        version is actually newer than the running one — anything else is
        leftover debris from a failed attempt and gets cleaned up.
        """
        parent = staging_parent(self._app_dir)
        for script in sorted(parent.glob("apply_update_*.bat")):
            version = script.stem.removeprefix("apply_update_")
            try:
                parse_version(version)
            except ValueError:
                continue
            local = load_manifest(staging_root(self._app_dir, version))
            if (
                local is not None
                and manifest_version(local) == version
                and is_newer(version, self._current_version)
            ):
                return version
        return None

    def cleanup_stale_artifacts(self, keep_version: str | None = None) -> None:
        """Delete staging trees / apply scripts / removed-lists of failed
        past attempts so they never accumulate next to the app folder."""
        parent = staging_parent(self._app_dir)
        prefix = f"{self._app_dir.name}.update-"
        for child in parent.glob(f"{prefix}*"):
            if child.is_dir() and child.name != f"{prefix}{keep_version}":
                shutil.rmtree(child, ignore_errors=True)
        for script in parent.glob("apply_update_*.bat"):
            if script.stem.removeprefix("apply_update_") != keep_version:
                script.unlink(missing_ok=True)
        for marker in parent.glob(".removed_*.txt"):
            version = marker.name.removeprefix(".removed_").removesuffix(".txt")
            if version != keep_version:
                marker.unlink(missing_ok=True)

    def apply_script_path(self, new_version: str) -> pathlib.Path:
        """Path of the prepared apply script (written right after staging)."""
        return staging_parent(self._app_dir) / f"apply_update_{new_version}.bat"


def staging_parent(app_dir: pathlib.Path) -> pathlib.Path:
    """Directory holding staging trees, apply scripts and removed-lists."""
    return app_dir.parent


def _noop_progress(done: int, total: int | None) -> None:  # pragma: no cover
    _ = done, total
