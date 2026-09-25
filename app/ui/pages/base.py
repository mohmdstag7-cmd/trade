"""Page registry and base page widgets.

Phase 1 ships every page as a designed empty state, plus a fully working
Settings page (appearance). Subsequent phases replace pages one by one,
following the phase map in ``docs/SPEC.md`` (G3).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.ui.i18n.translator import Translator


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
    ) -> None:
        super().__init__(parent)
        self._meta = meta
        self._translator = translator

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(440)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)

        self._title = QLabel()
        self._title.setObjectName("PageTitle")
        self._title.setWordWrap(True)

        self._desc = QLabel()
        self._desc.setObjectName("MutedLabel")
        self._desc.setWordWrap(True)

        self._phase_chip = QLabel()
        self._phase_chip.setObjectName("PhaseChip")

        card_layout.addWidget(self._title)
        card_layout.addWidget(self._desc)
        card_layout.addWidget(self._phase_chip, 0, Qt.AlignmentFlag.AlignLeft)
        card_layout.addStretch(1)

        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        translator.language_changed.connect(lambda _lang: self.retranslate())
        self.retranslate()

    def retranslate(self) -> None:
        """Refresh all texts for the current language."""
        self._title.setText(self._translator.translate(self._meta.title_key))
        if self._meta.desc_key:
            self._desc.setText(self._translator.translate(self._meta.desc_key))
            self._desc.show()
        else:
            self._desc.setText(self._translator.translate("empty.title"))
        self._phase_chip.setText(self._translator.translate("empty.phase", phase=self._meta.phase))
