"""Self-installing update helper — the frozen exe in a special CLI mode.

Invoked as::

    MT5TradingWorkstation.exe --apply-update --update-staging <dir> \
        [--update-pid <pid>]

The helper runs BEFORE any Qt import, does its job, relaunches the app
and exits. It replaces the generated ``apply_update_<v>.bat`` whose
cmd.exe execution model caused real field failures:

- cmd parses batch files with the legacy OEM codepage — Persian (or any
  non-ASCII) user profile paths get mangled unless 8.3 short names exist
  and ``chcp`` tricks happen to work;
- ``timeout`` cannot run console-less, ``ping``-delays are fragile,
  ``tasklist | find`` breaks with localized output and reuses stale
  PIDs from previous sessions;
- batch escaping of arbitrary paths is a minefield; robocopy gives no
  per-file control.

Python (frozen) handles Unicode natively, retries per file with a
rename-aside fallback for virus-scanner locks, verifies the swapped
tree against the staged manifest, and logs every step — with exception
tracebacks — to ``%LOCALAPPDATA%\\MT5TradingWorkstation\\update-apply.log``.

Windows note: a file that is *running* (the exe) or memory-mapped
(loaded DLLs in ``_internal``) cannot be deleted, but it CAN be renamed
on the same volume. The helper renames locked destinations to
``<name>.old`` and moves the staged file into place; stale ``*.old``
files are swept at the start of every apply.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from app.updater.manifest import MANIFEST_NAME, load_manifest, verify_tree

#: Grace period for the running app to exit before it is force-killed.
_PARENT_TIMEOUT_S = 75.0
#: Per-file replace attempts (virus scanners hold files for a few seconds).
_FILE_ATTEMPTS = 12
_FILE_RETRY_DELAY_S = 0.4

_EXE_NAME = "MT5TradingWorkstation.exe"
_STAGING_SUFFIX = ".update-"
_REMOVED_PREFIX = ".removed_"
_REMOVED_SUFFIX = ".txt"
_OLD_SUFFIX = ".old"

#: Windows process-creation flags: detached (no console window for the
#: helper / relaunched app) and a fresh process group (no Ctrl-C inherit).
_DETACHED = 0x00000008 | 0x00000200
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_INFINITE = 0xFFFFFFFF


class ApplyError(RuntimeError):
    """A fatal step of the staged install failed (details in the log)."""


class ApplyLog:
    """Append-only UTF-8 log consumed by the app's resume flow.

    The phrase ``apply FAILED`` / ``apply OK`` is load-bearing:
    ``read_apply_log_tail`` greps for it to warn about a failed past
    attempt on the next startup.
    """

    def __init__(self, path: pathlib.Path | None) -> None:
        self._path = path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._append(f"--- apply start (pid={os.getpid()}) ---")
            except OSError:
                self._path = None

    def _append(self, line: str) -> None:
        if self._path is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {line}\n")

    def __call__(self, line: str) -> None:
        with contextlib.suppress(OSError):  # logging must never crash
            self._append(line)


def apply_log_path() -> pathlib.Path | None:
    """Where the helper writes its log (``None`` off Windows)."""
    if os.name != "nt":
        return None
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return pathlib.Path(base) / "MT5TradingWorkstation" / "update-apply.log"


def wait_for_exit(pid: int, timeout_s: float = _PARENT_TIMEOUT_S) -> bool:
    """Wait until the process ``pid`` is gone (True) or timeout (False).

    Off Windows (dev/test) there is no parent to wait for. A dead PID or
    an already-exited process returns immediately.
    """
    if pid <= 0 or os.name != "nt":
        return True
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:  # pragma: no cover - non-Windows
            return True
        kernel32 = windll.kernel32
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return True  # already gone (or access denied → treat as gone)
        try:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                status = kernel32.WaitForSingleObject(handle, 1000)
                if status == _WAIT_OBJECT_0:
                    return True
            return False
        finally:
            kernel32.CloseHandle(handle)
    except OSError:  # pragma: no cover - defensive: never wedge the swap
        return True


def force_kill(pid: int) -> None:
    """Last resort when the app refuses to exit (Windows only)."""
    if pid <= 0 or os.name != "nt":
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        check=False,
    )


def sweep_old_files(app_dir: pathlib.Path, log: ApplyLog) -> None:
    """Delete ``*.old`` leftovers renamed aside by previous applies.

    Safe because the running app (which held those files) has already
    exited by the time the helper runs.
    """
    for path in app_dir.rglob(f"*{_OLD_SUFFIX}"):
        try:
            path.unlink()
            log(f"swept leftover {path.name}")
        except OSError as exc:
            log(f"could not sweep {path}: {exc!r}")


def install_file(src: pathlib.Path, dest: pathlib.Path, log: ApplyLog) -> None:
    """Move one staged file over its destination, robustly.

    ``os.replace`` is atomic on the same volume; on Windows it fails
    with ``PermissionError`` when the destination is open (virus scan,
    lingering handle). Retry, and move a stubborn destination aside
    (renaming a locked file is allowed) before the next attempt.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: OSError | None = None
    for attempt in range(_FILE_ATTEMPTS):
        try:
            os.replace(src, dest)
            return
        except OSError as exc:
            last = exc
            if attempt >= _FILE_ATTEMPTS - 1:
                break
            aside = dest.with_name(dest.name + _OLD_SUFFIX)
            try:
                os.replace(dest, aside)
                log(f"locked destination moved aside: {dest.name}")
            except OSError:
                pass  # nothing to move aside; plain retry
            time.sleep(_FILE_RETRY_DELAY_S)
    raise ApplyError(f"cannot install {dest.name}: {last!r}")


def install_tree(
    app_dir: pathlib.Path,
    staging: pathlib.Path,
    log: ApplyLog,
) -> int:
    """Copy the whole staged tree over the app folder; return file count."""
    sweep_old_files(app_dir, log)
    staged_files = [path for path in sorted(staging.rglob("*")) if path.is_file()]
    for src in staged_files:
        rel = src.relative_to(staging)
        install_file(src, app_dir / rel, log)
    return len(staged_files)


def apply_removed_files(
    app_dir: pathlib.Path,
    removed: tuple[str, ...],
    log: ApplyLog,
) -> None:
    """Delete files the new release no longer ships."""
    for rel in removed:
        target = app_dir / pathlib.Path(rel)
        try:
            target.unlink()
            log(f"removed {rel}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            log(f"could not remove {rel}: {exc!r}")


def parse_staging_version(staging: pathlib.Path) -> str:
    """Version encoded in the staging dir name (``<app>.update-<v>``)."""
    version = staging.name.split(_STAGING_SUFFIX, 1)[-1]
    if not version or version == staging.name:
        raise ApplyError(f"cannot derive version from staging name {staging.name!r}")
    return version


def removed_list_path(app_dir: pathlib.Path, staging: pathlib.Path) -> pathlib.Path:
    """Marker file listing deletions, written next to the staging tree."""
    version = parse_staging_version(staging)
    return staging.parent / f"{_REMOVED_PREFIX}{version}{_REMOVED_SUFFIX}"


def read_removed_list(path: pathlib.Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    lines = [
        line.strip().replace("\\", "/")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return tuple(lines)


def relaunch(app_dir: pathlib.Path, log: ApplyLog) -> None:
    """Start the (new) app detached so the helper can exit."""
    exe_name = pathlib.Path(sys.executable).name if getattr(sys, "frozen", False) else _EXE_NAME
    exe = app_dir / exe_name
    if not exe.is_file():
        # Fall back to any exe in the folder (renamed install layouts).
        candidates = sorted(app_dir.glob("*.exe"))
        if not candidates:
            raise ApplyError("no executable found to relaunch")
        exe = candidates[0]
    argv = [str(exe)]
    if os.name == "nt":
        subprocess.Popen(
            argv,
            cwd=str(app_dir),
            close_fds=True,
            creationflags=_DETACHED,
        )
    else:
        subprocess.Popen(argv, cwd=str(app_dir), close_fds=True)
    log(f"relaunched {exe.name}")


def cleanup(staging: pathlib.Path, marker: pathlib.Path, log: ApplyLog) -> None:
    """Remove the staging tree and the removed-list marker on success."""
    shutil.rmtree(staging, ignore_errors=True)
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - best effort
        log(f"could not delete marker {marker.name}: {exc!r}")
    # Legacy leftovers from the 0.6.x .bat era, next to the staging tree.
    for legacy in sorted(staging.parent.glob("apply_update_*.bat")):
        try:
            legacy.unlink()
            log(f"removed legacy script {legacy.name}")
        except OSError:  # pragma: no cover
            pass


def run_apply_update(
    app_dir: pathlib.Path,
    staging: pathlib.Path,
    parent_pid: int = 0,
    log_path: pathlib.Path | None = None,
    *,
    relaunch_app: bool = True,
) -> int:
    """Entry point for ``--apply-update``. Returns a process exit code.

    Steps: wait for the running app to exit → swap the staged tree over
    the install folder (per-file retries + rename-aside) → delete files
    the new release dropped → verify the result against the staged
    manifest → clean staging → relaunch the app. Every step is logged;
    on failure the staging tree is KEPT so the next start offers a
    retry (the resume flow re-offers it).
    """
    log = ApplyLog(log_path or apply_log_path())
    version = "*"
    with contextlib.suppress(ApplyError):
        version = parse_staging_version(staging)
    log(f"apply begin version={version} app_dir={app_dir} staging={staging}")
    try:
        if not staging.is_dir():
            raise ApplyError(f"staging tree missing: {staging}")
        manifest = load_manifest(staging)
        if manifest is None:
            raise ApplyError(f"staged {MANIFEST_NAME} missing or unreadable")

        if wait_for_exit(parent_pid):
            log(f"app (pid={parent_pid}) exited")
        else:
            log(f"app (pid={parent_pid}) still alive after {_PARENT_TIMEOUT_S:.0f}s — killing")
            force_kill(parent_pid)
            time.sleep(2.0)

        count = install_tree(app_dir, staging, log)
        log(f"installed {count} files")
        apply_removed_files(app_dir, read_removed_list(removed_list_path(app_dir, staging)), log)

        problems = verify_tree(app_dir, manifest)
        if problems:
            log(f"post-install verification mismatch: {', '.join(problems[:5])}")
        else:
            log("post-install verification OK")

        cleanup(staging, removed_list_path(app_dir, staging), log)
        if relaunch_app:
            try:
                relaunch(app_dir, log)
            except Exception as relaunch_exc:
                # The swap itself SUCCEEDED — the update is installed; only
                # the automatic launch failed (antivirus, permissions, …).
                # The user can start the app manually; never call this a
                # failed apply or the resume flow will offer a stale retry.
                log(f"relaunch failed (update installed): {relaunch_exc!r}")
        log("apply OK")
        return 0
    except Exception as exc:
        log(f"apply FAILED: {exc!r}")
        if relaunch_app:
            try:  # never leave the user with nothing to click
                relaunch(app_dir, log)
            except Exception as relaunch_exc:  # pragma: no cover
                log(f"relaunch after failure also failed: {relaunch_exc!r}")
        return 1


def read_manifest_from_staging(staging: pathlib.Path) -> dict | None:
    """Staged manifest (exposed for tests and diagnostics)."""
    if not staging.is_dir():
        return None
    try:
        data = json.loads((staging / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
