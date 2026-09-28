"""Ctrl+K command palette (SPEC F2)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.i18n.translator import Translator


@dataclass(frozen=True, slots=True)
class Command:
    """One palette entry."""

    id: str
    title: str
    callback: Callable[[], None]


class CommandPalette(QDialog):
    """Fuzzy-filterable list of commands, opened with Ctrl+K."""

    #: Emitted after a command executed; carries the command id.
    command_executed = Signal(str)

    _WIDTH = 560
    _HEIGHT = 440

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CommandPalette")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(False)

        self._translator = translator
        self._commands: tuple[Command, ...] = ()
        self.setWindowFlag(Qt.WindowType.Popup, True)  # auto-close on focus loss

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 130))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._input = QLineEdit()
        self._input.setPlaceholderText(translator.translate("palette.placeholder"))
        self._input.textChanged.connect(self._apply_filter)
        layout.addWidget(self._input)

        self._list = QListWidget()
        self._list.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list.itemActivated.connect(self._activate_item)
        self._list.itemClicked.connect(self._activate_item)
        layout.addWidget(self._list, 1)

        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()

    # -- public API ------------------------------------------------------------
    def set_commands(self, commands: Sequence[Command]) -> None:
        """Replace the command list and refresh the filter."""
        self._commands = tuple(commands)
        self._apply_filter(self._input.text())

    def open_at(self, anchor: QWidget) -> None:
        """Show centered over ``anchor`` with a cleared filter."""
        self.resize(self._WIDTH, self._HEIGHT)
        self._input.clear()
        self._list.clearFocus()
        geometry = anchor.frameGeometry()
        center = geometry.center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def retranslate(self) -> None:
        """Refresh static texts for the current language."""
        self._input.setPlaceholderText(self._translator.translate("palette.placeholder"))
        self.setWindowTitle(self._translator.translate("palette.title"))

    # -- internals ---------------------------------------------------------------
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        matches = [command for command in self._commands if needle in command.title.lower()]
        # rank: title starting with the query first (stable otherwise)
        matches.sort(key=lambda c: not c.title.lower().startswith(needle))

        self._list.clear()
        if not matches:
            empty = QListWidgetItem(self._translator.translate("palette.empty"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty)
            return
        for command in matches:
            item = QListWidgetItem(command.title)
            item.setData(Qt.ItemDataRole.UserRole, command.id)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _activate_item(self, item: QListWidgetItem) -> None:
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(command_id, str):
            return
        for command in self._commands:
            if command.id == command_id:
                self.accept()
                command.callback()
                self.command_executed.emit(command.id)
                return

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self._list.currentItem()
            if current is not None:
                self._activate_item(current)
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            row = min(self._list.currentRow() + 1, self._list.count() - 1)
            self._list.setCurrentRow(max(row, 0))
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            row = max(self._list.currentRow() - 1, 0)
            self._list.setCurrentRow(row)
            event.accept()
            return
        super().keyPressEvent(event)
