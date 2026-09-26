"""Updater unit tests — version compare, manifests, diff, service pipeline.

All network access is faked (:class:`FakeFetcher`); every test runs on a
throwaway ``tmp_path`` install tree, so nothing here touches the real
filesystem outside the pytest sandbox.
"""

from __future__ import annotations

import json
import pathlib
import zipfile

import pytest

from app.updater.github import (
    delta_zip_url,
    full_zip_url,
    latest_manifest_url,
    releases_page_url,
)
from app.updater.manifest import (
    build_manifest,
    diff_manifests,
    load_manifest,
    sha256_file,
    staging_root,
    verify_tree,
)
from app.updater.service import (
    UpdatePlan,
    UpdaterError,
    UpdateService,
    apply_script_payload,
)
from app.updater.version import is_newer, parse_version

# -- fakes ----------------------------------------------------------------------


class FakeFetcher:
    """Scriptable stand-in for ReleaseFetcher."""

    def __init__(
        self,
        manifests: dict[str, dict] | None = None,
        zips: dict[str, pathlib.Path] | None = None,
        fail_urls: set[str] | None = None,
    ) -> None:
        self.manifests = manifests or {}
        self.zips = zips or {}
        self.fail_urls = fail_urls or set()
        self.downloads: list[str] = []

    def fetch_json(self, url: str) -> dict:
        if url in self.fail_urls:
            raise RuntimeError("network down")
        return self.manifests[url]

    def exists(self, url: str) -> bool:
        return url not in self.fail_urls

    def download(self, url: str, destination: pathlib.Path, progress) -> None:
        if url in self.fail_urls:
            raise RuntimeError(f"download failed: {url}")
        self.downloads.append(url)
        source = self.zips[url]
        destination.write_bytes(source.read_bytes())
        progress(1, 1)


# -- version --------------------------------------------------------------------


def test_parse_version_strips_v_and_suffix() -> None:
    assert parse_version("v0.6.0") == (0, 6, 0)
    assert parse_version("0.10.1") == (0, 10, 1)
    assert parse_version("1.2.3rc1") == (1, 2, 3)


def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_version("banana")


def test_is_newer_handles_numeric_ordering() -> None:
    assert is_newer("0.10.0", "0.9.0")  # string compare would fail this
    assert is_newer("v0.6.0", "0.5.9")
    assert not is_newer("0.6.0", "0.6.0")
    assert not is_newer("0.5.0", "0.6.0")


# -- URLs --------------------------------------------------------------------------


def test_asset_url_shapes() -> None:
    assert (
        latest_manifest_url("a/b")
        == "https://github.com/a/b/releases/latest/download/manifest.json"
    )
    assert full_zip_url("a/b").endswith(
        "/releases/latest/download/MT5TradingWorkstation-portable.zip"
    )
    assert delta_zip_url("0.6.0", "0.5.0", "a/b") == (
        "https://github.com/a/b/releases/download/v0.6.0/delta-v0.5.0-to-v0.6.0.zip"
    )
    assert releases_page_url("a/b") == "https://github.com/a/b/releases"


# -- manifests ------------------------------------------------------------------------


def _make_tree(root: pathlib.Path, files: dict[str, bytes]) -> None:
    for rel, blob in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)


def test_build_and_load_manifest_roundtrip(tmp_path: pathlib.Path) -> None:
    _make_tree(tmp_path, {"a.txt": b"hello", "sub/b.dll": b"\x00\x01"})
    manifest = build_manifest(tmp_path, "0.6.0")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded["version"] == "0.6.0"
    assert "manifest.json" not in loaded["files"]
    assert loaded["files"]["a.txt"]["sha256"] == sha256_file(tmp_path / "a.txt")
    assert loaded["files"]["sub/b.dll"]["size"] == 2


def test_load_manifest_missing_or_corrupt(tmp_path: pathlib.Path) -> None:
    assert load_manifest(tmp_path) is None
    (tmp_path / "manifest.json").write_text("not json", encoding="utf-8")
    assert load_manifest(tmp_path) is None


def test_diff_manifests_added_changed_removed(tmp_path: pathlib.Path) -> None:
    # old tree: a.txt (will change), keep.txt (stays), gone.dll (will vanish)
    old_root = tmp_path / "old"
    _make_tree(old_root, {"a.txt": b"original", "keep.txt": b"same", "gone.dll": b"bye"})
    old = build_manifest(old_root, "0.5.0")

    # new tree: a.txt changed, new.dll added, gone.dll removed, keep.txt identical
    new_root = tmp_path / "new"
    _make_tree(new_root, {"a.txt": b"CHANGED", "keep.txt": b"same", "new.dll": b"brand new"})
    new = build_manifest(new_root, "0.6.0")

    diff = diff_manifests(old, new)
    assert set(diff.changed) == {"a.txt", "new.dll"}
    assert diff.removed == ("gone.dll",)

    # identical trees → no changes
    same = diff_manifests(old, build_manifest(old_root, "0.6.0"))
    assert not same.has_changes


def test_verify_tree_detects_mismatch(tmp_path: pathlib.Path) -> None:
    _make_tree(tmp_path, {"good.txt": b"ok", "bad.txt": b"original", "missing.txt": b"x"})
    manifest = build_manifest(tmp_path, "0.6.0")
    assert verify_tree(tmp_path, manifest) == []

    # tamper with one file and delete another — against the SAME manifest
    (tmp_path / "bad.txt").write_bytes(b"tampered")
    (tmp_path / "missing.txt").unlink()
    problems = verify_tree(tmp_path, manifest)
    assert set(problems) == {"bad.txt", "missing.txt"}


def test_staging_root_is_sibling(tmp_path: pathlib.Path) -> None:
    app_dir = tmp_path / "MT5TradingWorkstation"
    staging = staging_root(app_dir, "0.6.0")
    assert staging == tmp_path / "MT5TradingWorkstation.update-0.6.0"
    with pytest.raises(ValueError):
        staging_root(app_dir, "nope")


# -- apply script ----------------------------------------------------------------------


def test_apply_script_waits_copies_and_restarts() -> None:
    payload = apply_script_payload(
        pathlib.Path(r"C:\Apps\MT5TradingWorkstation"),
        pathlib.Path(r"C:\Apps\MT5TradingWorkstation.update-0.6.0"),
        pid=4242,
        removed=("old.dll", "sub/ancient.pdb"),
    )
    assert 'set "TARGET_PID=4242"' in payload
    assert 'tasklist /FI "PID eq %TARGET_PID%"' in payload
    assert 'robocopy "%STAGE%" "%APP_DIR%" /E' in payload
    assert 'start "" "%APP_DIR%\\MT5TradingWorkstation.exe"' in payload
    assert "old.dll" in payload and "ancient.pdb" in payload


# -- service pipeline -----------------------------------------------------------------------


def _service(tmp_path: pathlib.Path, fetcher: FakeFetcher, version: str = "0.5.0") -> UpdateService:
    app_dir = tmp_path / "MT5TradingWorkstation"
    app_dir.mkdir(exist_ok=True)
    return UpdateService(
        current_version=version,
        app_dir=app_dir,
        download_dir=tmp_path / "downloads",
        fetcher=fetcher,  # type: ignore[arg-type]
        repo="test/repo",
    )


def _delta_zip(
    tmp_path: pathlib.Path, changed: dict[str, bytes], removed: list[str]
) -> pathlib.Path:
    zip_path = tmp_path / f"delta-{len(list(tmp_path.glob('delta-*')))}.zip"
    with zipfile.ZipFile(zip_path, "w") as bundle:
        for rel, blob in changed.items():
            bundle.writestr(rel, blob)
        bundle.writestr("__delta__.json", json.dumps({"removed": removed}))
    return zip_path


def test_check_disabled_in_dev_mode(tmp_path: pathlib.Path) -> None:
    service = _service(tmp_path, FakeFetcher())
    # in tests sys.frozen is False → never any update
    result = service.check()
    assert result.available is False
    assert result.plan is None


def test_check_network_error_is_surfaced(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.updater.service.UpdateService.enabled", property(lambda self: True))
    manifest = {"version": "0.6.0", "files": {}}
    fetcher = FakeFetcher(
        manifests={latest_manifest_url("test/repo"): manifest},
        fail_urls={latest_manifest_url("test/repo")},
    )
    service = _service(tmp_path, fetcher)
    result = service.check()
    assert result.available is False
    assert "network down" in result.error


def test_check_up_to_date(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.updater.service.UpdateService.enabled", property(lambda self: True))
    manifest = {"version": "0.5.0", "files": {}}
    fetcher = FakeFetcher(manifests={latest_manifest_url("test/repo"): manifest})
    result = _service(tmp_path, fetcher).check()
    assert result.available is False


def test_full_download_when_no_local_manifest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.updater.service.UpdateService.enabled", property(lambda self: True))
    # build the "new" tree, zip it as the release asset
    new_tree = tmp_path / "new"
    _make_tree(new_tree, {"MT5TradingWorkstation.exe": b"new-exe", "_internal/x.dll": b"dll"})
    manifest = build_manifest(new_tree, "0.6.0")
    zip_path = tmp_path / "portable.zip"
    with zipfile.ZipFile(zip_path, "w") as bundle:
        bundle.writestr("MT5TradingWorkstation/MT5TradingWorkstation.exe", b"new-exe")
        bundle.writestr("MT5TradingWorkstation/_internal/x.dll", b"dll")
        bundle.writestr("MT5TradingWorkstation/manifest.json", json.dumps(manifest))
    fetcher = FakeFetcher(
        manifests={latest_manifest_url("test/repo"): manifest},
        zips={full_zip_url("test/repo"): zip_path},
    )
    service = _service(tmp_path, fetcher)
    result = service.check()
    assert result.available and result.plan is not None
    assert result.plan.mode == "full"
    staging = service.download_and_stage(result.plan)
    assert (staging / "MT5TradingWorkstation.exe").read_bytes() == b"new-exe"
    assert service.apply_script_path("0.6.0").is_file()
    assert verify_tree(staging, manifest) == []


def test_delta_download_stages_only_changed_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.updater.service.UpdateService.enabled", property(lambda self: True))
    app_dir = tmp_path / "MT5TradingWorkstation"
    _make_tree(app_dir, {"app.exe": b"old-exe", "keep.dll": b"same"})
    local_manifest = build_manifest(app_dir, "0.5.0")
    (app_dir / "manifest.json").write_text(json.dumps(local_manifest), encoding="utf-8")

    # new version: app.exe changed, keep.dll identical, old.dll removed
    new_files = {"app.exe": b"new-exe", "keep.dll": b"same"}
    new_tree = tmp_path / "new"
    _make_tree(new_tree, new_files)
    remote_manifest = build_manifest(new_tree, "0.6.0")
    manifest_url = latest_manifest_url("test/repo")
    delta_url = delta_zip_url("0.6.0", "0.5.0", "test/repo")
    delta = _delta_zip(tmp_path, {"app.exe": b"new-exe"}, ["old.dll"])
    fetcher = FakeFetcher(
        manifests={manifest_url: remote_manifest},
        zips={delta_url: delta},
    )
    service = _service(tmp_path, fetcher)
    result = service.check()
    assert result.available and result.plan is not None
    assert result.plan.mode == "delta"
    staging = service.download_and_stage(result.plan)
    assert (staging / "app.exe").read_bytes() == b"new-exe"
    # only the changed file was staged (delta semantics)
    assert sorted(p.name for p in staging.iterdir()) == ["app.exe"]
    payload = service.apply_script_path("0.6.0").read_text(encoding="utf-8")
    assert "old.dll" in payload  # removed file is honoured by the apply script


def test_corrupt_delta_is_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.updater.service.UpdateService.enabled", property(lambda self: True))
    app_dir = tmp_path / "MT5TradingWorkstation"
    _make_tree(app_dir, {"app.exe": b"old"})
    local_manifest = build_manifest(app_dir, "0.5.0")
    (app_dir / "manifest.json").write_text(json.dumps(local_manifest), encoding="utf-8")
    new_tree = tmp_path / "new"
    _make_tree(new_tree, {"app.exe": b"new"})
    remote_manifest = build_manifest(new_tree, "0.6.0")
    manifest_url = latest_manifest_url("test/repo")
    delta_url = delta_zip_url("0.6.0", "0.5.0", "test/repo")
    delta = _delta_zip(tmp_path, {"app.exe": b"TAMPERED"}, [])
    fetcher = FakeFetcher(manifests={manifest_url: remote_manifest}, zips={delta_url: delta})
    service = _service(tmp_path, fetcher)
    result = service.check()
    assert result.plan is not None
    with pytest.raises(UpdaterError, match="verification failed"):
        service.download_and_stage(result.plan)


def test_delta_falls_back_to_full_when_asset_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.updater.service.UpdateService.enabled", property(lambda self: True))
    app_dir = tmp_path / "MT5TradingWorkstation"
    _make_tree(app_dir, {"app.exe": b"old"})
    local_manifest = build_manifest(app_dir, "0.5.0")
    (app_dir / "manifest.json").write_text(json.dumps(local_manifest), encoding="utf-8")
    manifest_url = latest_manifest_url("test/repo")
    remote = {"version": "0.6.0", "files": {"app.exe": {"sha256": "x", "size": 1}}}
    fetcher = FakeFetcher(
        manifests={manifest_url: remote},
        fail_urls={delta_zip_url("0.6.0", "0.5.0", "test/repo")},
    )
    result = _service(tmp_path, fetcher).check()
    assert result.plan is not None
    assert result.plan.mode == "full"


def test_update_plan_mode_key(tmp_path: pathlib.Path) -> None:
    plan = UpdatePlan(current_version="0.5.0", new_version="0.6.0", mode="delta", release_page="")
    assert plan.mode_key == "updates.mode.delta"
    full = UpdatePlan(current_version="0.5.0", new_version="0.6.0", mode="full", release_page="")
    assert full.mode_key == "updates.mode.full"
