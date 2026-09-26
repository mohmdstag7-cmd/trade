"""Tests for the basic Logs page (SPEC G3-2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.observability.logger import LogRing, RingEntry
from app.ui.pages.logs import LogsPage


def _entry(level: str, category: str, message: str) -> RingEntry:
    return RingEntry(
        ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
        level=level,
        category=category,
        thread="MainThread",
        message=message,
    )


class _FakeRing(LogRing):
    """Ring preloaded with fixed entries (bypasses loguru)."""

    def __init__(self) -> None:
        super().__init__()
        self._entries.extend(
            [
                _entry("DEBUG", "mt5", "tick EURUSD 1.0843"),
                _entry("INFO", "risk", "position sized ok"),
                _entry("WARNING", "mt5", "quote stale"),
                _entry("ERROR", "execution", "order rejected"),
                _entry("INFO", "strategy", "password=[REDACTED] in strategy log"),
            ]
        )


@pytest.fixture
def ring() -> _FakeRing:
    return _FakeRing()


@pytest.fixture
def page(translator, ring: _FakeRing, tmp_path):
    return LogsPage(translator, ring, tmp_path / "logs")


class TestRendering:
    def test_shows_entries(self, page) -> None:
        text = page._filtered_text()
        assert "position sized ok" in text
        assert "order rejected" in text

    def test_no_ring_shows_empty_message(self, translator, tmp_path) -> None:
        page = LogsPage(translator, None, tmp_path / "logs")
        assert page._filtered_text() == translator.translate("logs.empty")

    def test_empty_result_shows_empty_message(self, page) -> None:
        page._search.setText("needle-does-not-exist")
        assert "No log entries" in page._filtered_text()

    def test_max_lines_rendered(self, page, ring: _FakeRing) -> None:
        for i in range(600):
            ring._entries.append(_entry("INFO", "app", f"bulk {i}"))
        text = page._filtered_text()
        lines = text.splitlines()
        assert len(lines) == 500
        # 5 preloaded + 600 bulk = 605 entries; the last 500 are kept, so the
        # first rendered bulk line is "bulk 100" and "bulk 99" was dropped.
        assert "bulk 100" in text
        assert "bulk 99" not in text


class TestFilters:
    def test_level_filter(self, page) -> None:
        index = page._level_combo.findData("WARNING")
        page._level_combo.setCurrentIndex(index)
        text = page._filtered_text()
        assert "quote stale" in text
        assert "order rejected" in text  # ERROR ranks above WARNING
        assert "position sized ok" not in text
        assert "tick EURUSD" not in text

    def test_category_filter(self, page) -> None:
        index = page._category_combo.findData("execution")
        page._category_combo.setCurrentIndex(index)
        text = page._filtered_text()
        assert "order rejected" in text
        assert "position sized ok" not in text

    def test_search_filter(self, page) -> None:
        page._search.setText("stale")
        text = page._filtered_text()
        assert "quote stale" in text
        assert "order rejected" not in text

    def test_filters_combine(self, page) -> None:
        page._search.setText("EURUSD")
        index = page._level_combo.findData("ERROR")
        page._level_combo.setCurrentIndex(index)
        assert "No log entries" in page._filtered_text()

    def test_masked_entries_not_expanded(self, page) -> None:
        text = page._filtered_text()
        assert "hunter2" not in text


class TestBehaviour:
    def test_refresh_updates_view(self, page, ring: _FakeRing) -> None:
        ring._entries.append(_entry("INFO", "app", "fresh entry"))
        page.refresh()
        assert "fresh entry" in page._view.toPlainText()

    def test_set_source_swaps_ring(self, page, tmp_path) -> None:
        new_ring = LogRing()
        page.set_source(new_ring, tmp_path)
        assert page._filtered_text().startswith("No log entries")

    def test_auto_refresh_toggle_controls_timer(self, page) -> None:
        assert page._timer.isActive()
        page._auto_refresh.setChecked(False)
        assert not page._timer.isActive()
        page._auto_refresh.setChecked(True)
        assert page._timer.isActive()
