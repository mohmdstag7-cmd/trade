"""Grouped, collapsible sidebar navigation (SPEC F2, v2).

v2: brand header with version chip, per-page icons (graceful fallback),
active accent bar, and slightly wider expanded width for comfortable
reading. Behavior (groups, collapse animation, persistence) is unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.core.settings import UiSettings
from app.ui.i18n.translator import Translator
from app.ui.icons import icon, page_icon
from app.ui.pages.base import GROUPS, PAGES
from app.ui.theme.manager import ThemeManager
from app.ui.theme.tokens import ThemeTokens

_EXPANDED_WIDTH = 240
_COLLAPSED_WIDTH = 64
_ANIMATION_MS = 150


class Sidebar(QFrame):
    """Left navigation with three groups, a brand header and a collapse toggle."""

    #: Emitted when the user clicks a navigation button.
    navigate = Signal(str)

    #: Emitted when the collapsed state changed (after the animation starts).
    collapsed_changed = Signal(bool)

    def __init__(
        self,
        translator: Translator,
        settings: UiSettings | None = None,
        parent: QWidget | None = None,
        *,
        theme_manager: ThemeManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._translator = translator
        self._settings = settings
        self._theme_manager = theme_manager
        self._collapsed = bool(settings.sidebar_collapsed) if settings else False
        self._animation: QParallelAnimationGroup | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(2)

        # -- brand row ---------------------------------------------------------
        brand = QWidget()
        brand.setObjectName("BrandRow")
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(6, 2, 6, 8)
        brand_row.setSpacing(9)
        self._brand_mark = QLabel()
        self._brand_mark.setObjectName("BrandMark")
        self._brand_name = QLabel()
        self._brand_name.setObjectName("BrandName")
        self._version_chip = QLabel(f"v{__version__}")
        self._version_chip.setObjectName("VersionChip")
        brand_row.addWidget(self._brand_mark)
        brand_row.addWidget(self._brand_name, 1)
        brand_row.addWidget(self._version_chip, 0, Qt.AlignmentFlag.AlignRight)
        outer.addWidget(brand)

        # -- nav -----------------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        nav_host = QWidget()
        nav_host.setStyleSheet("background: transparent;")
        self._nav_layout = QVBoxLayout(nav_host)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(2)
        scroll.setWidget(nav_host)
        outer.addWidget(scroll, 1)

        self._group_labels: dict[str, QLabel] = {}
        self._buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.idClicked.connect(self._on_group_clicked)

        for index, meta in enumerate(PAGES):
            if index == 0 or PAGES[index - 1].group != meta.group:
                label = QLabel()
                label.setObjectName("SidebarGroupLabel")
                self._group_labels[meta.group] = label
                self._nav_layout.addWidget(label)
            button = QPushButton()
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            group.addButton(button, index)
            self._nav_layout.addWidget(button)
            self._buttons[meta.key] = button
            if theme_manager is not None:
                self._apply_button_icon(button, meta.key, theme_manager.tokens)

        self._nav_layout.addStretch(1)

        self._collapse_button = QPushButton()
        self._collapse_button.setObjectName("SidebarCollapseButton")
        self._collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_button.clicked.connect(self.toggle_collapsed)
        outer.addWidget(self._collapse_button)

        if theme_manager is not None:
            theme_manager.theme_changed.connect(self._on_theme_changed)

        self.retranslate()
        translator.language_changed.connect(lambda _lang: self.retranslate())
        self._apply_width(animated=False)

    # -- public API ------------------------------------------------------------
    def set_active(self, key: str) -> None:
        """Highlight the page ``key`` in the sidebar."""
        button = self._buttons.get(key)
        if button is not None:
            button.setChecked(True)

    @property
    def collapsed(self) -> bool:
        """Whether the sidebar is collapsed to its narrow width."""
        return self._collapsed

    def toggle_collapsed(self) -> None:
        """Animate between expanded and collapsed widths."""
        self._collapsed = not self._collapsed
        if self._settings is not None:
            self._settings.sidebar_collapsed = self._collapsed
        self._apply_width(animated=True)
        self.collapsed_changed.emit(self._collapsed)

    def retranslate(self) -> None:
        """Refresh all texts and tooltips for the current language."""
        tr = self._translator.translate
        self._brand_name.setText(tr("app.brand"))
        for group_key, group_title_key in GROUPS:
            self._group_labels[group_key].setText(tr(group_title_key))
        for meta in PAGES:
            button = self._buttons[meta.key]
            title = tr(meta.title_key)
            if self._collapsed:
                button.setText("")
                button.setToolTip(title)
            else:
                button.setText(title)
                button.setToolTip("")
        self._update_collapse_button()

    # -- internals ---------------------------------------------------------------
    def _update_collapse_button(self) -> None:
        tokens = self._theme_manager.tokens if self._theme_manager is not None else None
        chevron = "collapse_right" if self._collapsed else "collapse_left"
        if tokens is not None:
            ic = icon(chevron, tokens.text_secondary)
            if ic is not None:
                self._collapse_button.setIcon(ic)
                self._collapse_button.setText("")
                self._collapse_button.setToolTip(self._translator.translate("sidebar.collapse"))
                return
        self._collapse_button.setText("»" if self._collapsed else "«")

    def _apply_button_icon(self, button: QPushButton, key: str, tokens: object) -> None:
        if not isinstance(tokens, ThemeTokens):
            return
        ic = page_icon(key, tokens)
        if ic is not None:
            button.setIcon(ic)

    def _on_theme_changed(self, _name: str) -> None:
        if self._theme_manager is None:
            return
        tokens = self._theme_manager.tokens
        for meta in PAGES:
            button = self._buttons[meta.key]
            self._apply_button_icon(button, meta.key, tokens)
        self._update_collapse_button()

    def _on_group_clicked(self, index: int) -> None:
        if 0 <= index < len(PAGES):
            self.navigate.emit(PAGES[index].key)

    def _apply_width(self, animated: bool) -> None:
        target = _COLLAPSED_WIDTH if self._collapsed else _EXPANDED_WIDTH
        if not animated:
            self.setMinimumWidth(target)
            self.setMaximumWidth(target)
            self.retranslate()
            return
        group = QParallelAnimationGroup(self)
        for prop in (b"minimumWidth", b"maximumWidth"):
            anim = QPropertyAnimation(self)
            anim.setPropertyName(prop)
            anim.setDuration(_ANIMATION_MS)
            anim.setStartValue(self.width())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(anim)
        group.finished.connect(self.retranslate)
        self._animation = group  # keep referenced until finished
        group.start()
