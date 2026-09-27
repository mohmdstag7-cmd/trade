"""Update orchestration: check → download → verify → stage → apply.

The :class:`UpdateService` is synchronous and framework-free. Errors are
typed (:class:`UpdaterError`) so the UI worker can present them. Every
downloaded byte is verified against the *new* release manifest before it
may touch the staging area — and nothing touches the installed tree until
the user restarts, at which point the app's own executable re-launches
itself in the ``--apply-update`` helper mode (:mod:`app.updater.apply`):
wait for this process to exit, move the staged files over the install
folder (per-file retries, rename-aside for locked files), delete the
files the new release removed, verify the result against the staged
manifest, relaunch the app and log every step to
``%LOCALAPPDATA%\\MT5TradingWorkstation\\update-apply.log``. On failure
the staging tree is kept so the next start offers a retry instead of a
silent re-download.

The staged tree always receives the NEW release's ``manifest.json`` —
a delta zip cannot contain it (the manifest excludes itself), and
without it the next check would probe a stale delta name and fall back
to a 165 MB full download.
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
    MANIFEST_NAME,
    diff_manifests,
    load_manifest,
    manifest_version,
    staging_root,
    verify_tree,
)
from app.updater.version import is_newer, parse_version

#: Payload file inside a delta zip describing removed files + metadata.
_DELTA_META = "__delta__.json"
_APPLY_LOG_DIRNAME = "MT5TradingWorkstation"
_APPLY_LOG_NAME = "update-apply.log"


def app_dir_writable(app_dir: pathlib.Path) -> bool:
    """True when the install tree accepts writes without elevation.

    ``os.access`` alone lies for UAC-protected directories, so a real
    touch probe is used. Rare for this app — both the portable zip and
    the installer live under ``%LOCALAPPDATA%`` — but keep the guard:
    a manually relocated install may sit somewhere protected.
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
    def current_version(self) -> str:
        """The version this service is updating from."""
        return self._current_version

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
        self._write_staged_manifest(staging, remote)
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
        self._write_staged_manifest(staging, remote)

    def _write_staged_manifest(self, staging: pathlib.Path, remote: dict[str, Any]) -> None:
        """Embed the NEW release manifest in the staging tree.

        A delta zip can never carry ``manifest.json`` (the manifest
        excludes itself, so it is absent from every diff) — without this
        the freshly updated install would keep the OLD manifest and the
        next check would probe a stale delta name and fall back to a
        full 165 MB download.
        """
        (staging / MANIFEST_NAME).write_text(
            json.dumps(remote, indent=1, sort_keys=True), encoding="utf-8"
        )

    # -- apply helpers --------------------------------------------------------------
    def _removed_list_path(self, new_version: str) -> pathlib.Path:
        return staging_parent(self._app_dir) / f".removed_{new_version}.txt"

    def _write_removed_list(self, plan: UpdatePlan, removed: tuple[str, ...]) -> None:
        """Persist deletions next to (not inside) the staging tree."""
        self._removed_list_path(plan.new_version).write_text("\n".join(removed), encoding="utf-8")

    def apply_update_command(self, new_version: str) -> list[str]:
        """Argv that turns THIS executable into the update installer.

        The frozen exe is re-run in the ``--apply-update`` helper mode
        (see :mod:`app.updater.apply`): no cmd.exe, no batch codepage
        traps, native Unicode paths, and the parent PID is passed at
        spawn time — never baked into a stale script.
        """
        staging = staging_root(self._app_dir, new_version)
        command = [sys.executable, "--apply-update", "--update-staging", str(staging)]
        pid = os.getpid()
        if pid > 0:
            command += ["--update-pid", str(pid)]
        return command

    def pending_staged_update(self) -> str | None:
        """Version of a fully staged update awaiting restart, if any.

        A staging tree counts as usable only when its embedded manifest
        matches the version encoded in its directory name AND that
        version is newer than the running one — anything else is
        leftover debris from a failed attempt.
        """
        parent = staging_parent(self._app_dir)
        prefix = f"{self._app_dir.name}.update-"
        for child in sorted(parent.glob(f"{prefix}*")):
            if not child.is_dir():
                continue
            version = child.name.removeprefix(prefix)
            try:
                parse_version(version)
            except ValueError:
                continue
            local = load_manifest(child)
            if (
                local is not None
                and manifest_version(local) == version
                and is_newer(version, self._current_version)
            ):
                return version
        return None

    def cleanup_stale_artifacts(self, keep_version: str | None = None) -> None:
        """Delete staging trees / removed-lists / legacy scripts of failed
        past attempts so they never accumulate next to the app folder."""
        parent = staging_parent(self._app_dir)
        prefix = f"{self._app_dir.name}.update-"
        for child in parent.glob(f"{prefix}*"):
            if child.is_dir() and child.name != f"{prefix}{keep_version}":
                shutil.rmtree(child, ignore_errors=True)
        for script in parent.glob("apply_update_*.bat"):
            script.unlink(missing_ok=True)  # legacy 0.6.x leftovers
        for marker in parent.glob(".removed_*.txt"):
            version = marker.name.removeprefix(".removed_").removesuffix(".txt")
            if version != keep_version:
                marker.unlink(missing_ok=True)


def staging_parent(app_dir: pathlib.Path) -> pathlib.Path:
    """Directory holding staging trees, apply scripts and removed-lists."""
    return app_dir.parent


def _noop_progress(done: int, total: int | None) -> None:  # pragma: no cover
    _ = done, total
