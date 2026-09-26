"""QSS generation from design tokens (SPEC F1, v2).

One function builds the full application stylesheet for a theme. Widgets
opt into named styles via ``objectName`` / dynamic properties, e.g.:

- ``#Card``               surface card with 14px radius
- ``#NavButton``          sidebar navigation button (``:checked`` = active)
- ``#AccentButton``       primary call-to-action
- ``#GhostButton``        quiet secondary action
- ``#DangerButton``       destructive action
- ``#Badge[variant="…"]`` semantic pills (ok / bad / warn / info)
- ``#ConnectionDot[connected="…"]`` tri-state status dot
- ``#MonoLabel``          tabular readouts (prices, clocks, sizes)
- ``#SegmentButton``      grouped checkable selector (symbols)
"""

from __future__ import annotations

from app.ui.theme.tokens import (
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_H1,
    FONT_SIZE_H2,
    FONT_SIZE_SMALL,
    MONO_FONT_STACK,
    RADIUS_LG,
    RADIUS_MD,
    RADIUS_SM,
    ThemeTokens,
)

_QSS_TEMPLATE = """
* {{
  outline: none;
}}
QWidget {{
  background: {t.bg};
  color: {t.text};
  font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Tahoma", sans-serif;
  font-size: {body}px;
}}
QMainWindow, QDialog {{
  background: {t.bg};
}}
#CentralArea {{
  background: {t.bg};
}}
QToolTip {{
  background: {t.card};
  color: {t.text};
  border: 1px solid {t.border_strong};
  border-radius: 4px;
  padding: 5px 9px;
  font-size: {small}px;
}}

/* ---------- typography --------------------------------------------------- */
#PageTitle {{
  font-size: {h1}px;
  font-weight: 700;
  letter-spacing: 0.2px;
}}
#PageSubtitle {{
  color: {t.text_secondary};
  font-size: {body}px;
}}
#CardTitle {{
  font-size: {h2}px;
  font-weight: 600;
}}
#SectionLabel {{
  color: {t.text_secondary};
  font-size: {caption}px;
  font-weight: 700;
  letter-spacing: 1.1px;
}}
#MutedLabel {{
  color: {t.text_secondary};
}}
#FaintLabel {{
  color: {t.text_disabled};
  font-size: {small}px;
}}
#MonoLabel {{
  font-family: {mono};
  font-size: {body}px;
}}

/* ---------- surfaces ----------------------------------------------------- */
#Card {{
  background: {t.card};
  border: 1px solid {t.border};
  border-radius: {radius_lg}px;
}}
#Card > QWidget {{
  background: transparent;
}}
#Toast {{
  background: {t.card};
  border: 1px solid {t.border_strong};
  border-radius: {radius_md}px;
}}
#Toast #ToastTitle {{
  font-weight: 600;
}}
#Toast #ToastBody {{
  color: {t.text_secondary};
  font-size: {small}px;
}}

/* ---------- sidebar ------------------------------------------------------ */
#Sidebar {{
  background: {t.sidebar_bg};
  border-right: 1px solid {t.border};
}}
#Sidebar > QWidget {{
  background: transparent;
}}
#BrandRow {{
  background: transparent;
}}
#BrandMark {{
  background: {t.accent};
  border-radius: 7px;
  min-width: 14px;
  max-width: 14px;
  min-height: 14px;
  max-height: 14px;
}}
#BrandName {{
  font-size: {h2}px;
  font-weight: 700;
}}
#VersionChip {{
  background: {t.sidebar_active};
  color: {t.text_secondary};
  border-radius: 8px;
  padding: 1px 8px;
  font-size: {caption}px;
  font-weight: 600;
}}
#SidebarGroupLabel {{
  color: {t.text_disabled};
  font-size: {caption}px;
  font-weight: 700;
  padding: 12px 14px 4px 14px;
  letter-spacing: 1.2px;
}}
#NavButton {{
  text-align: left;
  padding: 8px 12px;
  margin: 1px 4px;
  border: none;
  border-left: 3px solid transparent;
  border-radius: {radius_sm}px;
  background: transparent;
  color: {t.text_secondary};
  font-size: {body}px;
}}
#NavButton:hover {{
  background: {t.sidebar_hover};
  color: {t.text};
}}
#NavButton:checked {{
  background: {t.sidebar_active};
  border-left: 3px solid {t.accent};
  color: {t.text};
  font-weight: 600;
}}
#NavButton:checked {{
  padding: 8px 9px;
}}
#SidebarCollapseButton {{
  border: none;
  border-radius: {radius_sm}px;
  background: transparent;
  color: {t.text_secondary};
  padding: 7px;
  font-weight: 600;
}}
#SidebarCollapseButton:hover {{
  background: {t.sidebar_hover};
  color: {t.text};
}}

/* ---------- buttons ------------------------------------------------------ */
QPushButton {{
  background: {t.surface};
  border: 1px solid {t.border};
  border-radius: {radius_md}px;
  padding: 7px 16px;
  color: {t.text};
  font-size: {body}px;
}}
QPushButton:hover {{
  background: {t.hover};
  border-color: {t.border_strong};
}}
QPushButton:pressed {{
  background: {t.selected};
}}
QPushButton:focus {{
  border-color: {t.accent};
}}
QPushButton:disabled {{
  color: {t.text_disabled};
  background: {t.surface};
  border-color: {t.border};
}}
QPushButton#AccentButton, QPushButton#PrimaryButton {{
  background: {t.accent};
  border: 1px solid {t.accent};
  color: #FFFFFF;
  font-weight: 600;
}}
QPushButton#AccentButton:hover, QPushButton#PrimaryButton:hover {{
  background: {t.accent_hover};
  border-color: {t.accent_hover};
}}
QPushButton#AccentButton:pressed, QPushButton#PrimaryButton:pressed {{
  background: {t.accent_pressed};
  border-color: {t.accent_pressed};
}}
QPushButton#AccentButton:disabled, QPushButton#PrimaryButton:disabled {{
  background: {t.accent_soft};
  border-color: {t.accent_soft};
  color: {t.text_secondary};
}}
QPushButton#GhostButton {{
  background: transparent;
  border: 1px solid {t.border};
  color: {t.text_secondary};
}}
QPushButton#GhostButton:hover {{
  background: {t.hover};
  color: {t.text};
  border-color: {t.border_strong};
}}
QPushButton#DangerButton {{
  background: transparent;
  border: 1px solid {t.loss};
  color: {t.loss};
  font-weight: 600;
}}
QPushButton#DangerButton:hover {{
  background: {t.loss};
  color: #FFFFFF;
}}
QPushButton#DangerButton:disabled {{
  color: {t.text_disabled};
  border-color: {t.border};
  background: transparent;
}}
QPushButton#Badge {{
  background: {t.sidebar_active};
  border: none;
  border-radius: 9px;
  padding: 4px 12px;
  color: {t.text_secondary};
  font-size: {caption}px;
  font-weight: 600;
}}
QPushButton#Badge:checked {{
  background: {t.accent_soft};
  color: {t.accent};
}}
QPushButton#Badge:hover {{
  background: {t.hover};
}}
QPushButton#SegmentButton {{
  background: transparent;
  border: 1px solid {t.border};
  border-radius: 0;
  margin: 0;
  padding: 6px 14px;
  color: {t.text_secondary};
  font-weight: 600;
  font-size: {small}px;
}}
QPushButton#SegmentButton:first {{
  border-top-left-radius: {radius_md}px;
  border-bottom-left-radius: {radius_md}px;
}}
QPushButton#SegmentButton:last {{
  border-top-right-radius: {radius_md}px;
  border-bottom-right-radius: {radius_md}px;
}}
QPushButton#SegmentButton:hover {{
  background: {t.hover};
  color: {t.text};
}}
QPushButton#SegmentButton:checked {{
  background: {t.accent_soft};
  color: {t.accent};
  border-color: {t.accent};
}}

/* ---------- inputs ------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
  background: {t.input_bg};
  border: 1px solid {t.border};
  border-radius: {radius_md}px;
  padding: 7px 12px;
  color: {t.text};
  selection-background-color: {t.accent};
  selection-color: #FFFFFF;
  min-height: 18px;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
  border-color: {t.border_strong};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
  border-color: {t.accent};
  background: {t.input_bg};
}}
QLineEdit:disabled, QComboBox:disabled {{
  color: {t.text_disabled};
  background: {t.bg};
}}
QLineEdit[invalid="true"] {{
  border-color: {t.loss};
}}
QComboBox::drop-down {{
  border: none;
  width: 24px;
}}
QComboBox QAbstractItemView {{
  background: {t.surface};
  border: 1px solid {t.border_strong};
  border-radius: {radius_sm}px;
  color: {t.text};
  selection-background-color: {t.selected};
  selection-color: {t.text};
  padding: 4px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
  background: transparent;
  border: none;
  width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
  image: none;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-bottom: 5px solid {t.text_secondary};
  width: 0; height: 0;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
  image: none;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid {t.text_secondary};
  width: 0; height: 0;
}}

/* ---------- checkable ---------------------------------------------------- */
QCheckBox, QRadioButton {{
  background: transparent;
  spacing: 8px;
  color: {t.text};
  padding: 2px 0;
}}
QCheckBox::indicator, QRadioButton::indicator {{
  width: 16px;
  height: 16px;
  border: 1px solid {t.border_strong};
  background: {t.input_bg};
}}
QCheckBox::indicator {{
  border-radius: 4px;
}}
QRadioButton::indicator {{
  border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
  border-color: {t.accent};
}}
QCheckBox::indicator:checked {{
  background: {t.accent};
  border-color: {t.accent};
}}
QRadioButton::indicator:checked {{
  background: {t.accent};
  border: 4px solid {t.input_bg};
  border-radius: 8px;
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
  border-color: {t.border};
  background: {t.bg};
}}

/* ---------- tables / trees / lists --------------------------------------- */
QTableView, QTreeView, QListWidget, QTableWidget {{
  background: {t.card};
  alternate-background-color: {t.card};
  border: 1px solid {t.border};
  border-radius: {radius_md}px;
  color: {t.text};
  gridline-color: {t.border};
  selection-background-color: {t.selected};
  selection-color: {t.text};
}}
QTableView {{ selection-background-color: {t.selected}; }}
QHeaderView {{
  background: {t.card};
  border: none;
}}
QHeaderView::section {{
  background: {t.surface};
  color: {t.text_secondary};
  border: none;
  border-bottom: 1px solid {t.border};
  padding: 7px 10px;
  font-size: {caption}px;
  font-weight: 700;
  letter-spacing: 0.6px;
}}
QTableView::item, QTreeView::item, QListWidget::item {{
  padding: 6px 8px;
  border: none;
}}
QTableView::item:hover, QTreeView::item:hover {{
  background: {t.hover};
}}
QTableView::item:selected, QTreeView::item:selected, QListWidget::item:selected {{
  background: {t.selected};
  color: {t.text};
}}
QListWidget {{
  padding: 4px;
}}
QListView {{
  background: {t.surface};
  border: 1px solid {t.border};
  border-radius: {radius_md}px;
  color: {t.text};
}}
QListView::item {{
  padding: 8px 12px;
}}
QListView::item:selected {{
  background: {t.selected};
  color: {t.text};
}}
QListView::item:hover {{
  background: {t.hover};
}}
QTableCornerButton::section {{
  background: {t.surface};
  border: none;
}}

/* ---------- tabs --------------------------------------------------------- */
QTabWidget::pane {{
  background: {t.card};
  border: 1px solid {t.border};
  border-radius: {radius_md}px;
  top: -1px;
}}
QTabBar {{
  background: transparent;
}}
QTabBar::tab {{
  background: transparent;
  color: {t.text_secondary};
  border: none;
  border-bottom: 2px solid transparent;
  padding: 8px 16px;
  margin-right: 2px;
  font-weight: 600;
  font-size: {small}px;
}}
QTabBar::tab:hover {{
  color: {t.text};
}}
QTabBar::tab:selected {{
  color: {t.accent};
  border-bottom: 2px solid {t.accent};
}}

/* ---------- progress / sliders ------------------------------------------- */
QProgressBar {{
  background: {t.surface};
  border: 1px solid {t.border};
  border-radius: 7px;
  height: 14px;
  text-align: center;
  color: {t.text_secondary};
  font-size: {caption}px;
}}
QProgressBar::chunk {{
  background: {t.accent};
  border-radius: 6px;
}}
QSlider::groove:horizontal {{
  height: 4px;
  background: {t.border};
  border-radius: 2px;
}}
QSlider::handle:horizontal {{
  background: {t.accent};
  width: 14px;
  height: 14px;
  margin: -5px 0;
  border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
  background: {t.accent_hover};
}}

/* ---------- misc containers ---------------------------------------------- */
QStatusBar {{
  background: {t.surface};
  border-top: 1px solid {t.border};
  color: {t.text_secondary};
  font-size: {small}px;
}}
QStatusBar QLabel {{
  background: transparent;
  padding: 2px 8px;
}}
QStatusBar::item {{
  border: none;
}}
QSplitter::handle {{
  background: transparent;
}}
QSplitter::handle:horizontal {{
  width: 6px;
}}
QSplitter::handle:vertical {{
  height: 6px;
}}
QSplitter::handle:hover {{
  background: {t.accent_soft};
  border-radius: 2px;
}}
QGroupBox {{
  background: {t.card};
  border: 1px solid {t.border};
  border-radius: {radius_md}px;
  margin-top: 14px;
  padding: 12px 12px 12px 12px;
  font-weight: 600;
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  left: 12px;
  padding: 0 6px;
  color: {t.text_secondary};
}}
QMenuBar {{
  background: {t.surface};
  border-bottom: 1px solid {t.border};
}}
QMenuBar::item {{
  padding: 6px 12px;
  border-radius: {radius_sm}px;
}}
QMenuBar::item:selected {{
  background: {t.hover};
}}
QToolBar {{
  background: {t.surface};
  border: none;
  border-bottom: 1px solid {t.border};
  padding: 4px;
  spacing: 4px;
}}
QToolButton {{
  background: transparent;
  border: none;
  border-radius: {radius_sm}px;
  padding: 5px 8px;
  color: {t.text_secondary};
}}
QToolButton:hover {{
  background: {t.hover};
  color: {t.text};
}}
QDialogButtonBox QPushButton {{
  min-width: 84px;
}}

/* ---------- pills & dots ------------------------------------------------- */
#ConnectionDot {{
  background: {t.text_disabled};
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
#ConnectionDot[connected="connecting"] {{
  background: {t.warning};
}}
#Badge {{
  background: {t.sidebar_active};
  border-radius: 9px;
  padding: 3px 11px;
  color: {t.text_secondary};
  font-size: {caption}px;
  font-weight: 600;
}}
#Badge[variant="ok"] {{
  background: {t.profit_bg};
  color: {t.profit};
}}
#Badge[variant="bad"] {{
  background: {t.loss_bg};
  color: {t.loss};
}}
#Badge[variant="warn"] {{
  background: {t.warning_bg};
  color: {t.warning};
}}
#Badge[variant="info"] {{
  background: {t.info_bg};
  color: {t.info};
}}
#PhaseChip {{
  background: {t.accent_soft};
  color: {t.accent};
  border-radius: 9px;
  padding: 3px 11px;
  font-size: {caption}px;
  font-weight: 700;
  letter-spacing: 0.4px;
}}
#EmptyIcon {{
  background: {t.accent_soft};
  border-radius: 28px;
  min-width: 56px;
  max-width: 56px;
  min-height: 56px;
  max-height: 56px;
}}

/* ---------- scrollbars ---------------------------------------------------- */
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
  background: {t.border_strong};
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
QScrollBar::handle:horizontal:hover {{
  background: {t.border_strong};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
  height: 0px;
  width: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
  background: transparent;
}}

/* ---------- menus & palette ---------------------------------------------- */
QMenu {{
  background: {t.card};
  border: 1px solid {t.border_strong};
  border-radius: {radius_md}px;
  padding: 5px;
}}
QMenu::item {{
  padding: 7px 18px;
  border-radius: {radius_sm}px;
}}
QMenu::item:selected {{
  background: {t.selected};
}}
QMenu::separator {{
  height: 1px;
  background: {t.border};
  margin: 5px 8px;
}}
#CommandPalette {{
  background: {t.bg};
  border: 1px solid {t.border_strong};
  border-radius: {radius_lg}px;
}}
"""


def build_qss(tokens: ThemeTokens) -> str:
    """Generate the full application stylesheet for ``tokens``."""
    return _QSS_TEMPLATE.format(
        t=tokens,
        h1=FONT_SIZE_H1,
        h2=FONT_SIZE_H2,
        body=FONT_SIZE_BODY,
        small=FONT_SIZE_SMALL,
        caption=FONT_SIZE_CAPTION,
        radius_sm=RADIUS_SM,
        radius_md=RADIUS_MD,
        radius_lg=RADIUS_LG,
        mono=MONO_FONT_STACK,
    )
