"""QSS generation from design tokens (SPEC F1).

One function builds the full application stylesheet for a theme. Widgets
opt into named styles via ``objectName`` / dynamic properties, e.g.:

- ``#Card``             surface card with 12px radius
- ``#SidebarButton``    sidebar navigation button (``:checked`` = active)
- ``#ConnectionDot``    status dot with dynamic property ``connected``
- ``#KillSwitchButton`` destructive action styling
"""

from __future__ import annotations

from app.ui.theme.tokens import ThemeTokens

_QSS_TEMPLATE = """
* {{
  outline: none;
}}
QWidget {{
  background: {t.bg};
  color: {t.text};
  font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Vazirmatn", sans-serif;
  font-size: 13px;
}}
QMainWindow, QDialog {{
  background: {t.bg};
}}
#CentralArea {{
  background: {t.bg};
}}
#Card {{
  background: {t.card};
  border: 1px solid {t.border};
  border-radius: 12px;
}}
#PageTitle {{
  font-size: 20px;
  font-weight: 700;
}}
#CardTitle {{
  font-size: 15px;
  font-weight: 600;
}}
#MutedLabel {{
  color: {t.text_secondary};
}}
#PhaseChip {{
  background: {t.sidebar_active};
  color: {t.text_secondary};
  border-radius: 9px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
}}
#Sidebar {{
  background: {t.sidebar_bg};
  border-right: 1px solid {t.border};
}}
#SidebarGroupLabel {{
  color: {t.text_secondary};
  font-size: 11px;
  font-weight: 600;
  padding: 10px 12px 4px 12px;
  letter-spacing: 1px;
}}
#SidebarButton {{
  text-align: left;
  padding: 8px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: {t.text_secondary};
}}
#SidebarButton:hover {{
  background: {t.sidebar_hover};
  color: {t.text};
}}
#SidebarButton:checked {{
  background: {t.sidebar_active};
  color: {t.text};
  font-weight: 600;
}}
#SidebarCollapseButton {{
  border: none;
  border-radius: 8px;
  background: transparent;
  color: {t.text_secondary};
  padding: 6px;
  font-weight: 600;
}}
#SidebarCollapseButton:hover {{
  background: {t.sidebar_hover};
  color: {t.text};
}}
QPushButton {{
  background: {t.surface};
  border: 1px solid {t.border};
  border-radius: 8px;
  padding: 6px 14px;
  color: {t.text};
}}
QPushButton:hover {{
  border-color: {t.accent};
}}
QPushButton:pressed {{
  background: {t.sidebar_active};
}}
QPushButton:disabled {{
  color: {t.text_secondary};
  background: {t.surface};
  border-color: {t.border};
}}
QPushButton#AccentButton {{
  background: {t.accent};
  border: none;
  color: #FFFFFF;
  font-weight: 600;
}}
QPushButton#AccentButton:hover {{
  background: {t.accent_hover};
}}
QPushButton#AccentButton:pressed {{
  background: {t.accent_pressed};
}}
QPushButton#KillSwitchButton {{
  background: transparent;
  border: 1px solid {t.loss};
  color: {t.loss};
  font-weight: 600;
}}
QPushButton#KillSwitchButton:hover {{
  background: {t.loss};
  color: #FFFFFF;
}}
QPushButton#KillSwitchButton:disabled {{
  color: {t.text_secondary};
  border-color: {t.border};
  background: transparent;
}}
QLineEdit, QComboBox, QSpinBox {{
  background: {t.input_bg};
  border: 1px solid {t.border};
  border-radius: 8px;
  padding: 6px 10px;
  color: {t.text};
  selection-background-color: {t.accent};
}}
QLineEdit:focus, QComboBox:focus {{
  border-color: {t.accent};
}}
QComboBox::drop-down {{
  border: none;
  width: 22px;
}}
QListView {{
  background: {t.surface};
  border: 1px solid {t.border};
  border-radius: 8px;
  color: {t.text};
}}
QListView::item {{
  padding: 8px 12px;
}}
QListView::item:selected {{
  background: {t.sidebar_active};
  color: {t.text};
}}
QListView::item:hover {{
  background: {t.sidebar_hover};
}}
QStatusBar {{
  background: {t.surface};
  border-top: 1px solid {t.border};
  color: {t.text_secondary};
}}
QStatusBar QLabel {{
  background: transparent;
  padding: 2px 8px;
}}
#ConnectionDot {{
  background: {t.text_secondary};
  border-radius: 5px;
  min-width: 10px;
  max-width: 10px;
  min-height: 10px;
  max-height: 10px;
}}
#ConnectionDot[connected="true"] {{
  background: {t.profit};
}}
#ConnectionDot[connected="false"] {{
  background: {t.loss};
}}
#Badge {{
  background: {t.sidebar_active};
  border-radius: 8px;
  padding: 2px 10px;
  color: {t.text_secondary};
  font-size: 11px;
  font-weight: 600;
}}
QToolTip {{
  background: {t.card};
  color: {t.text};
  border: 1px solid {t.border};
  padding: 4px 8px;
}}
QScrollBar:vertical {{
  background: transparent;
  width: 10px;
  margin: 2px;
}}
QScrollBar::handle:vertical {{
  background: {t.border};
  border-radius: 4px;
  min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
  background: {t.text_secondary};
}}
QScrollBar:horizontal {{
  background: transparent;
  height: 10px;
  margin: 2px;
}}
QScrollBar::handle:horizontal {{
  background: {t.border};
  border-radius: 4px;
  min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
  height: 0px;
  width: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
  background: transparent;
}}
QMenu {{
  background: {t.card};
  border: 1px solid {t.border};
  border-radius: 8px;
  padding: 4px;
}}
QMenu::item {{
  padding: 6px 16px;
  border-radius: 6px;
}}
QMenu::item:selected {{
  background: {t.sidebar_active};
}}
#CommandPalette {{
  background: {t.bg};
  border: 1px solid {t.border};
  border-radius: 12px;
}}
"""


def build_qss(tokens: ThemeTokens) -> str:
    """Generate the full application stylesheet for ``tokens``."""
    return _QSS_TEMPLATE.format(t=tokens)
