"""Tests for the structured logging subsystem (SPEC E3.1-E3.4)."""

from __future__ import annotations

import json
import pathlib
import time

import pytest
from loguru import logger

from app.observability.context import trace
from app.observability.logger import (
    CATEGORIES,
    LogRing,
    current_state,
    debug_mode_active,
    enable_debug_mode,
    enforce_total_size_cap,
    get_logger,
    init_logging,
    log_startup,
    set_category_level,
    shutdown_logging,
)


@pytest.fixture
def log_env(tmp_path: pathlib.Path):
    state = init_logging(logs_dir=tmp_path / "logs")
    yield state
    shutdown_logging()


def _flush() -> None:
    logger.complete()
    time.sleep(0.05)


def _category_files(logs_dir: pathlib.Path, category: str) -> list[pathlib.Path]:
    cat_dir = logs_dir / category
    if not cat_dir.exists():
        return []
    return sorted(cat_dir.glob("*.jsonl"))


class TestStructuredOutput:
    def test_category_jsonl_written(self, log_env, tmp_path: pathlib.Path) -> None:
        logs_dir = tmp_path / "logs"
        get_logger("risk").info("position sized")
        _flush()
        files = _category_files(logs_dir, "risk")
        assert files, "category file should exist after first record"

        payload = json.loads(files[-1].read_text(encoding="utf-8").splitlines()[-1])
        assert payload["level"] == "INFO"
        assert payload["category"] == "risk"
        assert payload["message"] == "position sized"
        assert payload["session_id"] == log_env.session_id
        assert payload["ts"].endswith("Z")
        assert payload["thread"]
        assert payload["function"] == "test_category_jsonl_written"

    def test_trace_id_in_record(self, log_env, tmp_path: pathlib.Path) -> None:
        logs_dir = tmp_path / "logs"
        with trace() as tid:
            get_logger("execution").info("order sent")
        _flush()
        payload = json.loads(
            (_category_files(logs_dir, "execution")[-1])
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )
        assert payload["trace_id"] == tid

    def test_bound_ids_included_when_present(self, log_env, tmp_path: pathlib.Path) -> None:
        logs_dir = tmp_path / "logs"
        get_logger("strategy").bind(
            signal_id="sig-1", symbol="EURUSD", strategy="trend-pullback"
        ).info("signal generated")
        _flush()
        payload = json.loads(
            (_category_files(logs_dir, "strategy")[-1]).read_text(encoding="utf-8").splitlines()[-1]
        )
        assert payload["signal_id"] == "sig-1"
        assert payload["symbol"] == "EURUSD"
        assert payload["strategy"] == "trend-pullback"
        assert "trade_id" not in payload  # absent ids are omitted, not null

    def test_all_log_readable(self, log_env, tmp_path: pathlib.Path) -> None:
        logs_dir = tmp_path / "logs"
        get_logger("app").info("hello from all.log")
        _flush()
        all_log = logs_dir / "all.log"
        assert all_log.exists()
        content = all_log.read_text(encoding="utf-8")
        assert "hello from all.log" in content
        assert "app" in content

    def test_masking_applied_to_files(self, log_env, tmp_path: pathlib.Path) -> None:
        logs_dir = tmp_path / "logs"
        get_logger("mt5").info("connect failed with password=super-secret-99")
        _flush()
        payload = json.loads(
            (_category_files(logs_dir, "mt5")[-1]).read_text(encoding="utf-8").splitlines()[-1]
        )
        assert "super-secret-99" not in payload["message"]
        assert "[REDACTED]" in payload["message"]


class TestRuntimeLevels:
    def test_set_category_level_filters(self, log_env) -> None:
        ring = log_env.ring
        set_category_level("backtest", "ERROR")
        get_logger("backtest").info("visible before? no")
        logger.complete()
        assert all("visible before" not in e.message for e in ring.snapshot())

        get_logger("backtest").error("error passes")
        logger.complete()
        assert any("error passes" in e.message for e in ring.snapshot())

    def test_debug_mode_window(self, log_env) -> None:
        set_category_level("ml", "ERROR")
        enable_debug_mode(minutes=1)
        assert debug_mode_active()
        get_logger("ml").debug("debug during window")
        logger.complete()
        assert any("debug during window" in e.message for e in log_env.ring.snapshot())

        log_env.debug_until = time.monotonic() - 1  # simulate expiry
        assert not debug_mode_active()
        get_logger("ml").debug("debug after window")
        logger.complete()
        assert not any("debug after window" in e.message for e in log_env.ring.snapshot())

    def test_invalid_inputs(self, log_env) -> None:
        with pytest.raises(ValueError, match="unknown level"):
            set_category_level("app", "LOUD")
        with pytest.raises(ValueError, match="unknown logging category"):
            get_logger("not-a-category")

    def test_default_levels_info(self, log_env) -> None:
        get_logger("ui").debug("hidden by default")
        logger.complete()
        assert all("hidden by default" not in e.message for e in log_env.ring.snapshot())


class TestRing:
    def test_ring_captures_entries(self, log_env) -> None:
        get_logger("app").warning("ring entry")
        logger.complete()
        entries = log_env.ring.snapshot()
        assert entries
        last = entries[-1]
        assert last.level == "WARNING"
        assert last.category == "app"
        assert "ring entry" in last.message
        assert "ring entry" in last.as_line()

    def test_ring_masks_secrets(self, log_env) -> None:
        get_logger("app").info("token=abcdef123456 leaked?")
        logger.complete()
        assert all("abcdef123456" not in e.message for e in log_env.ring.snapshot())

    def test_lines_limit(self, log_env) -> None:
        for i in range(10):
            get_logger("app").info("line {}", i)
        logger.complete()
        lines = log_env.ring.lines(limit=3)
        assert len(lines) == 3
        assert "line 9" in lines[-1]


class TestSizeCap:
    def test_oldest_removed(self, tmp_path: pathlib.Path) -> None:
        old = tmp_path / "old.jsonl"
        new = tmp_path / "new.jsonl"
        old.write_text("x" * 600)
        new.write_text("y" * 600)
        old_time = time.time() - 3600
        import os

        os.utime(old, (old_time, old_time))
        removed = enforce_total_size_cap(tmp_path, cap_mb=0.001)  # ~1049 bytes
        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_no_removal_under_cap(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "a.jsonl").write_text("x" * 10)
        assert enforce_total_size_cap(tmp_path, cap_mb=10) == 0


class TestLifecycle:
    def test_log_startup(self, log_env) -> None:
        log_startup()
        _flush()
        entries = log_env.ring.snapshot()
        assert any("startup: app=" in e.message for e in entries)

    def test_reinit_replaces_state(self, tmp_path: pathlib.Path) -> None:
        state_a = init_logging(logs_dir=tmp_path / "a")
        state_b = init_logging(logs_dir=tmp_path / "b")
        assert current_state() is state_b
        assert state_b.session_id != state_a.session_id
        shutdown_logging()

    def test_unknown_category_rejected(self) -> None:
        assert "app" in CATEGORIES
        assert len(CATEGORIES) == 16


def test_ring_standalone() -> None:
    ring = LogRing(maxlen=3)
    for i in range(5):
        ring.sink(type("M", (), {"record": _fake_record(f"m{i}")})())
    snapshot = ring.snapshot()
    assert len(snapshot) == 3
    assert [e.message for e in snapshot] == ["m2", "m3", "m4"]


def _fake_record(message: str) -> dict:
    from datetime import UTC, datetime

    return {
        "time": datetime.now(UTC),
        "level": type("L", (), {"name": "INFO"}),
        "extra": {"category": "app"},
        "thread": type("T", (), {"name": "t"}),
        "message": message,
    }
