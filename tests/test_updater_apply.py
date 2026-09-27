"""Tests for the native update installer (:mod:`app.updater.apply`).

The installer replaces the 0.6.x generated ``.bat`` whose cmd.exe
execution model failed in the field (OEM codepage vs Persian paths,
console-less wait hacks, stale PIDs). These tests pin the replacement's
contract: wait for the app → swap files with retries → delete removed
files → verify → clean staging → relaunch — and log everything, with
``apply OK`` / ``apply FAILED`` phrases the resume flow greps for.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest

from app.updater.apply import (
    ApplyError,
    ApplyLog,
    install_file,
    install_tree,
    parse_staging_version,
    read_removed_list,
    removed_list_path,
    run_apply_update,
    sweep_old_files,
)
from app.updater.manifest import build_manifest


def _make_tree(root: pathlib.Path, files: dict[str, bytes]) -> None:
    for rel, blob in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)


@pytest.fixture
def logs(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "update-apply.log"


# -- helpers ------------------------------------------------------------------------


def test_parse_staging_version(tmp_path: pathlib.Path) -> None:
    staging = tmp_path / "MT5TradingWorkstation.update-0.7.1"
    assert parse_staging_version(staging) == "0.7.1"
    with pytest.raises(ApplyError):
        parse_staging_version(tmp_path / "unrelated")


def test_apply_log_writes_phrases(tmp_path: pathlib.Path) -> None:
    log_path = tmp_path / "update-apply.log"
    log = ApplyLog(log_path)
    log("apply OK")
    log("apply FAILED: boom")
    text = log_path.read_text(encoding="utf-8")
    assert "--- apply start" in text
    assert "apply OK" in text
    assert "apply FAILED: boom" in text


def test_apply_log_unwritable_path_is_noop(tmp_path: pathlib.Path) -> None:
    blocked = tmp_path / "blocked" / "log.log"
    blocked.parent.mkdir()
    (blocked.parent / "log.log").mkdir()  # a directory where the log should be
    log = ApplyLog(blocked)  # open fails → logging disabled, no crash
    log("ignored")  # must not raise


def test_read_removed_list_normalizes_separators(tmp_path: pathlib.Path) -> None:
    marker = tmp_path / ".removed_0.7.1.txt"
    marker.write_text("_internal/old.dll\nnew name\\x.dll\n\n", encoding="utf-8")
    assert read_removed_list(marker) == ("_internal/old.dll", "new name/x.dll")
    assert read_removed_list(tmp_path / "missing.txt") == ()


def test_sweep_old_files(tmp_path: pathlib.Path, logs: pathlib.Path) -> None:
    _make_tree(tmp_path, {"app.old": b"x", "_internal/dll.old": b"y", "keep.txt": b"z"})
    sweep_old_files(tmp_path, ApplyLog(logs))
    assert not (tmp_path / "app.old").exists()
    assert not (tmp_path / "_internal" / "dll.old").exists()
    assert (tmp_path / "keep.txt").exists()


# -- file install -----------------------------------------------------------------


def test_install_file_moves_content(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "stage" / "app.exe"
    _make_tree(tmp_path / "stage", {"app.exe": b"new"})
    dest = tmp_path / "app" / "app.exe"
    _make_tree(tmp_path / "app", {"app.exe": b"old"})
    install_file(src, dest, ApplyLog(None))
    assert dest.read_bytes() == b"new"


def test_install_file_retries_when_destination_locked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, logs: pathlib.Path
) -> None:
    src = tmp_path / "stage" / "app.exe"
    _make_tree(tmp_path / "stage", {"app.exe": b"new"})
    dest = tmp_path / "app" / "app.exe"
    _make_tree(tmp_path / "app", {"app.exe": b"old"})

    real_replace = os.replace
    attempts = {"n": 0}

    def flaky_replace(src_path: Any, dest_path: Any) -> None:
        # simulate a virus scanner holding the destination twice
        if dest_path == dest and attempts["n"] < 2:
            attempts["n"] += 1
            raise PermissionError(13, "file in use")
        real_replace(src_path, dest_path)

    monkeypatch.setattr("app.updater.apply.os.replace", flaky_replace)
    monkeypatch.setattr("app.updater.apply.time.sleep", lambda _s: None)
    install_file(src, dest, ApplyLog(logs))
    assert dest.read_bytes() == b"new"
    assert attempts["n"] == 2


def test_install_file_gives_up_after_max_attempts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "stage" / "app.exe"
    _make_tree(tmp_path / "stage", {"app.exe": b"new"})
    dest = tmp_path / "app" / "app.exe"
    _make_tree(tmp_path / "app", {"app.exe": b"old"})

    def always_locked(src_path: Any, dest_path: Any) -> None:
        if src_path == src:  # the install attempt itself stays locked
            raise PermissionError(13, "locked forever")
        real_replace(src_path, dest_path)  # rename-aside passes through

    real_replace = os.replace
    monkeypatch.setattr("app.updater.apply.os.replace", always_locked)
    monkeypatch.setattr("app.updater.apply.time.sleep", lambda _s: None)
    with pytest.raises(ApplyError, match="locked forever"):
        install_file(src, dest, ApplyLog(None))


def test_install_tree_sweeps_and_counts(tmp_path: pathlib.Path, logs: pathlib.Path) -> None:
    app_dir = tmp_path / "app"
    staging = tmp_path / "app.update-0.7.1"
    _make_tree(app_dir, {"keep.dll": b"same", "stale.old": b"junk"})
    _make_tree(staging, {"keep.dll": b"same", "app.exe": b"new", "_internal/a.dll": b"aaa"})
    count = install_tree(app_dir, staging, ApplyLog(logs))
    assert count == 3
    assert (app_dir / "app.exe").read_bytes() == b"new"
    assert (app_dir / "_internal" / "a.dll").read_bytes() == b"aaa"
    assert (app_dir / "keep.dll").read_bytes() == b"same"
    assert not (app_dir / "stale.old").exists()


# -- full pipeline ------------------------------------------------------------------


def _full_run_setup(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    app_dir = tmp_path / "app"
    staging = tmp_path / "app.update-0.7.1"
    _make_tree(app_dir, {"app.exe": b"old", "gone.dll": b"bye", "keep.dll": b"same"})
    _make_tree(staging, {"app.exe": b"new", "keep.dll": b"same", "extra.dll": b"x"})
    manifest = build_manifest(staging, "0.7.1")
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / ".removed_0.7.1.txt").write_text("gone.dll", encoding="utf-8")
    return app_dir, staging, tmp_path / "update-apply.log"


def test_run_apply_update_happy_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, staging, log_path = _full_run_setup(tmp_path)
    monkeypatch.setattr("app.updater.apply.wait_for_exit", lambda pid, timeout_s=75.0: True)
    relaunched: list[str] = []

    def fake_relaunch(app_dir: pathlib.Path, log: Any) -> None:
        relaunched.append(str(app_dir / "MT5TradingWorkstation.exe"))

    monkeypatch.setattr("app.updater.apply.relaunch", fake_relaunch)
    code = run_apply_update(app_dir, staging, parent_pid=4242, log_path=log_path)
    assert code == 0
    assert (app_dir / "app.exe").read_bytes() == b"new"
    assert (app_dir / "extra.dll").exists()
    assert not (app_dir / "gone.dll").exists()  # removed list honoured
    assert not staging.exists()  # staging cleaned on success
    assert not (staging.parent / ".removed_0.7.1.txt").exists()
    assert relaunched == [str(app_dir / "MT5TradingWorkstation.exe")]
    text = log_path.read_text(encoding="utf-8")
    assert "apply OK" in text
    assert "post-install verification OK" in text


def test_run_apply_update_missing_staging_keeps_message(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    log_path = tmp_path / "update-apply.log"
    monkeypatch.setattr("app.updater.apply.relaunch", lambda *_a: None)
    code = run_apply_update(app_dir, tmp_path / "nope.update-0.7.1", log_path=log_path)
    assert code == 1
    assert "apply FAILED" in log_path.read_text(encoding="utf-8")


def test_run_apply_update_missing_manifest_fails_cleanly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, staging, log_path = _full_run_setup(tmp_path)
    (staging / "manifest.json").unlink()
    monkeypatch.setattr("app.updater.apply.wait_for_exit", lambda pid, timeout_s=75.0: True)
    monkeypatch.setattr("app.updater.apply.relaunch", lambda *_a: None)
    code = run_apply_update(app_dir, staging, parent_pid=0, log_path=log_path)
    assert code == 1
    assert "apply FAILED" in log_path.read_text(encoding="utf-8")
    # staging kept for the retry flow
    assert staging.exists()


def test_run_apply_update_kills_wedged_parent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, staging, log_path = _full_run_setup(tmp_path)
    killed: list[int] = []
    monkeypatch.setattr("app.updater.apply.wait_for_exit", lambda pid, timeout_s=75.0: False)
    monkeypatch.setattr("app.updater.apply.force_kill", lambda pid: killed.append(pid))
    monkeypatch.setattr("app.updater.apply.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.updater.apply.relaunch", lambda *_a: None)
    code = run_apply_update(app_dir, staging, parent_pid=999, log_path=log_path)
    assert code == 0
    assert killed == [999]


def test_run_apply_update_reports_verification_mismatch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir, staging, log_path = _full_run_setup(tmp_path)
    # corrupt a staged file AFTER its manifest was written
    (staging / "extra.dll").write_bytes(b"tampered")
    monkeypatch.setattr("app.updater.apply.wait_for_exit", lambda pid, timeout_s=75.0: True)
    monkeypatch.setattr("app.updater.apply.relaunch", lambda *_a: None)
    code = run_apply_update(app_dir, staging, parent_pid=0, log_path=log_path)
    assert code == 0  # install proceeds; the mismatch is logged, not fatal
    assert "post-install verification mismatch" in log_path.read_text(encoding="utf-8")


def test_removed_list_path_derives_from_staging_name(tmp_path: pathlib.Path) -> None:
    staging = tmp_path / "app.update-1.2.3"
    staging.mkdir()
    assert removed_list_path(tmp_path / "app", staging) == tmp_path / ".removed_1.2.3.txt"


def test_run_apply_update_relaunch_failure_is_not_an_apply_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed auto-launch must not mark the swap itself as failed."""
    app_dir, staging, log_path = _full_run_setup(tmp_path)
    monkeypatch.setattr("app.updater.apply.wait_for_exit", lambda pid, timeout_s=75.0: True)

    def broken_relaunch(_app_dir: pathlib.Path, _log: Any) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr("app.updater.apply.relaunch", broken_relaunch)
    code = run_apply_update(app_dir, staging, parent_pid=0, log_path=log_path)
    text = log_path.read_text(encoding="utf-8")
    assert code == 0
    assert "apply OK" in text
    assert "relaunch failed (update installed)" in text
    assert (app_dir / "app.exe").read_bytes() == b"new"
