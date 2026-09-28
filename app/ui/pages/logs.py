"""Basic Logs page (SPEC G3-2): view recent entries, filter, open folder.

The page reads from the in-memory :class:`LogRing` (not from disk) so it is
instant and safe while log files are being written by background threads.
Full-disk search, the trace timeline and the debug bundle arrive in
Phase 13 (SPEC G3-13).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.observability.logger import CATEGORIES, VALID_LEVELS, LogRing
from app.ui.i18n.translator import Translator

#: Maximum lines rendered in the view (older entries are dropped).
MAX_RENDERED_LINES = 500

_LEVELS_FOR_FILTER: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class LogsPage(QWidget):
    """Live view of recent log entries with level/category/search filters."""

    REFRESH_MS = 1000

    def __init__(
        self,
        translator: Translator,
        ring: LogRing | None,
        logs_dir: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._ring = ring
        self._logs_dir = logs_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # -- title -----------------------------------------------------------
        self._title = QLabel()
        self._title.setObjectName("PageTitle")

        # -- toolbar -----------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._level_combo = QComboBox()
        self._category_combo = QComboBox()
        self._search = QLineEdit()

        self._auto_refresh = QCheckBox()
        self._auto_refresh.setChecked(True)

        self._refresh_btn = QPushButton()
        self._open_btn = QPushButton()
        self._open_btn.clicked.connect(self._open_logs_folder)
        self._refresh_btn.clicked.connect(self.refresh)

        self._level_label = QLabel(translator.translate("logs.filter.level"))
        self._category_label = QLabel(translator.translate("logs.filter.category"))
        toolbar.addWidget(self._level_label)
        toolbar.addWidget(self._level_combo)
        toolbar.addWidget(self._category_label)
        toolbar.addWidget(self._category_combo)
        toolbar.addWidget(self._search, 1)
        toolbar.addWidget(self._auto_refresh)
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._open_btn)

        # -- log view ------------------------------------------------------------
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self._view.setFont(font)
        self._view.setMaximumBlockCount(MAX_RENDERED_LINES)

        layout.addWidget(self._title)
        layout.addLayout(toolbar)
        layout.addWidget(self._view, 1)

        # -- filters ------------------------------------------------------------
        self._level_combo.addItem(translator.translate("logs.filter.all"), "")
        for level in _LEVELS_FOR_FILTER:
            self._level_combo.addItem(level, level)
        self._level_combo.setCurrentIndex(0)
        self._level_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self._category_combo.addItem(translator.translate("logs.filter.all"), "")
        for category in CATEGORIES:
            self._category_combo.addItem(category, category)
        self._category_combo.setCurrentIndex(0)
        self._category_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self._search.textChanged.connect(lambda _t: self.refresh())
        self._auto_refresh.toggled.connect(self._on_auto_refresh_toggled)

        # -- timer ------------------------------------------------------------
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self._on_timer_tick)
        if self._auto_refresh.isChecked():
            self._timer.start()

        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()
        self.refresh()

    # -- public -----------------------------------------------------------------
    def set_source(self, ring: LogRing | None, logs_dir: Path | None) -> None:
        """Point the page at a (new) ring; used once logging is initialised."""
        self._ring = ring
        self._logs_dir = logs_dir
        self.refresh()

    def refresh(self) -> None:
        """Re-render the view from the ring with the active filters.

        The scroll position is preserved (sticking to the bottom when the
        user is already there) — a full setPlainText used to yank the view
        to the top every second, making live-following unreadable.
        """
        bar = self._view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        new_text = self._filtered_text()
        if new_text == self._view.toPlainText():
            return
        bar_value = bar.value()
        self._view.setPlainText(new_text)
        if at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(bar_value)

    def _on_timer_tick(self) -> None:
        """Timer-driven refresh: skip while the page is hidden."""
        if self.isVisible():
            self.refresh()

    # -- internals ----------------------------------------------------------------
    def _filtered_text(self) -> str:
        if self._ring is None:
            return self._translator.translate("logs.empty")
        level = self._level_combo.currentData() or ""
        category = self._category_combo.currentData() or ""
        needle = self._search.text().lower().strip()
        level_rank = _LEVELS_FOR_FILTER.index(level) if level else -1

        lines: list[str] = []
        for entry in self._ring.snapshot():
            if level and _rank(entry.level) < level_rank:
                continue
            if category and entry.category != category:
                continue
            if needle and needle not in entry.message.lower():
                continue
            lines.append(entry.as_line())
        if not lines:
            return self._translator.translate("logs.empty")
        return "\n".join(lines[-MAX_RENDERED_LINES:])

    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        if checked:
            self._timer.start()
            self.refresh()
        else:
            self._timer.stop()

    def _open_logs_folder(self) -> None:
        if self._logs_dir is not None and self._logs_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._logs_dir)))

    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        self._title.setText(self._translator.translate("nav.logs"))
        self._search.setPlaceholderText(self._translator.translate("logs.search"))
        self._auto_refresh.setText(self._translator.translate("logs.autorefresh"))
        self._refresh_btn.setText(self._translator.translate("logs.refresh"))
        self._open_btn.setText(self._translator.translate("logs.open_folder"))
        self._level_label.setText(self._translator.translate("logs.filter.level"))
        self._category_label.setText(self._translator.translate("logs.filter.category"))
        all_index = self._level_combo.findData("")
        if all_index >= 0:
            self._level_combo.setItemText(all_index, self._translator.translate("logs.filter.all"))
        cat_index = self._category_combo.findData("")
        if cat_index >= 0:
            self._category_combo.setItemText(
                cat_index, self._translator.translate("logs.filter.all")
            )


def _rank(level: str) -> int:
    try:
        return _LEVELS_FOR_FILTER.index(level)
    except ValueError:
        return len(VALID_LEVELS)  # unknown levels (TRACE) sort last
