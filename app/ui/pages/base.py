"""Page registry and base page widgets.

Phase 1 ships every page as a designed empty state, plus a fully working
Settings page (appearance). Subsequent phases replace pages one by one,
following the phase map in ``docs/SPEC.md`` (G3).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.i18n.translator import Translator
from app.ui.icons import PAGE_ICONS, icon
from app.ui.theme.manager import ThemeManager


@dataclass(frozen=True, slots=True)
class PageMeta:
    """Static metadata for one page."""

    key: str
    title_key: str
    group: str
    #: SPEC phase in which the page gets its real content (0 = built now).
    phase: int
    desc_key: str = ""


#: Canonical page order: sidebar groups + stacked-widget order.
PAGES: tuple[PageMeta, ...] = (
    PageMeta("dashboard", "nav.dashboard", "trade", 11, "empty.dashboard.desc"),
    PageMeta("market", "nav.market", "trade", 5),
    PageMeta("signals", "nav.signals", "trade", 6),
    PageMeta("positions", "nav.positions", "trade", 11),
    PageMeta("analytics", "nav.analytics", "analyze", 11),
    PageMeta("journal", "nav.journal", "analyze", 11),
    PageMeta("backtest", "nav.backtest", "analyze", 9),
    PageMeta("model", "nav.model", "analyze", 10),
    PageMeta("ai_lab", "nav.ai_lab", "analyze", 12),
    PageMeta("strategies", "nav.strategies", "system", 6),
    PageMeta("risk", "nav.risk", "system", 7),
    PageMeta("logs", "nav.logs", "system", 2),
    PageMeta("health", "nav.health", "system", 13),
    PageMeta("settings", "nav.settings", "system", 0, "empty.settings.desc"),
)

#: Sidebar groups: (group key, title key).
GROUPS: tuple[tuple[str, str], ...] = (
    ("trade", "nav.group.trade"),
    ("analyze", "nav.group.analyze"),
    ("system", "nav.group.system"),
)

_PAGE_INDEX: dict[str, PageMeta] = {meta.key: meta for meta in PAGES}


def page_meta(key: str) -> PageMeta:
    """Return the :class:`PageMeta` for ``key`` (raises ``KeyError``)."""
    return _PAGE_INDEX[key]


class EmptyStatePage(QWidget):
    """Designed empty state used by pages whose content arrives later."""

    def __init__(
        self,
        meta: PageMeta,
        translator: Translator,
        parent: QWidget | None = None,
        *,
        theme_manager: ThemeManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._meta = meta
        self._translator = translator
        self._theme_manager = theme_manager

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(460)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)

        self._icon_holder = QLabel()
        self._icon_holder.setObjectName("EmptyIcon")
        self._icon_holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._icon_holder, 0, Qt.AlignmentFlag.AlignTop)

        head_text = QVBoxLayout()
        head_text.setSpacing(4)
        self._title = QLabel()
        self._title.setObjectName("PageTitle")
        self._title.setWordWrap(True)
        head_text.addWidget(self._title)
        self._phase_chip = QLabel()
        self._phase_chip.setObjectName("PhaseChip")
        head_text.addWidget(self._phase_chip, 0, Qt.AlignmentFlag.AlignLeft)
        head.addLayout(head_text, 1)

        card_layout.addLayout(head)

        self._desc = QLabel()
        self._desc.setObjectName("MutedLabel")
        self._desc.setWordWrap(True)
        card_layout.addWidget(self._desc)
        card_layout.addStretch(1)

        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)

        if theme_manager is not None:
            theme_manager.theme_changed.connect(lambda _n: self._apply_icon())
        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()
        self._apply_icon()

    def _apply_icon(self) -> None:
        if self._theme_manager is None:
            return
        name = PAGE_ICONS.get(self._meta.key)
        if name is None:
            return
        ic = icon(name, self._theme_manager.tokens.accent, size=26)
        if ic is not None:
            self._icon_holder.setPixmap(ic.pixmap(26, 26))

    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        self._title.setText(self._translator.translate(self._meta.title_key))
        if self._meta.desc_key:
            self._desc.setText(self._translator.translate(self._meta.desc_key))
            self._desc.show()
        else:
            self._desc.setText(self._translator.translate("empty.title"))
        self._phase_chip.setText(self._translator.translate("empty.phase", phase=self._meta.phase))
