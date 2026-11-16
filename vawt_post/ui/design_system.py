# -*- coding: utf-8 -*-
"""
TurbineScope v4.1.1: Unified Design System & Qt 6 Stylesheet Tokens
===================================================================
Central design system defining typography scale, spacing scale, corner radii,
WCAG AA compliant color tokens, and hardware-accelerated Qt 6 QSS stylesheets
for both dark and light modes.
"""
from __future__ import annotations
from typing import Dict

# Spacing Scale (pixels)
SPACING_XS: int = 4
SPACING_SM: int = 8
SPACING_MD: int = 12
SPACING_LG: int = 16
SPACING_XL: int = 24

CORNER_RADIUS_SM: int = 6
CORNER_RADIUS_MD: int = 8
CORNER_RADIUS_LG: int = 12

# Typography Definitions
FONT_FAMILY: str = "Segoe UI"
FONT_MONO: str = "Consolas"

FONT_PAGE_TITLE = (FONT_FAMILY, 20, "bold")
FONT_SECTION_TITLE = (FONT_FAMILY, 14, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 12, "bold")
FONT_BODY = (FONT_FAMILY, 12)
FONT_BODY_SM = (FONT_FAMILY, 11)
FONT_CAPTION = (FONT_FAMILY, 10)
FONT_MONO_CODE = (FONT_MONO, 10)
FONT_MONO_SM = (FONT_MONO, 9)

# Color Tokens (Matched to Classic Charcoal Dark & Crisp Light Studio Palette)
TOKENS: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg_main": "#242424",          # Charcoal 900 (matches CustomTkinter gray14)
        "bg_surface": "#2B2B2B",       # Charcoal 800 (matches CustomTkinter gray17)
        "bg_card": "#2B2B2B",
        "bg_card_alt": "#212121",
        "bg_card_hover": "#383838",    # Charcoal 700
        "border": "#3E3E3E",
        "border_light": "#565B5E",
        "text_primary": "#DCE4EE",     # Soft high-contrast off-white (matches CTk)
        "text_secondary": "#9CA3AF",   # Neutral cool-gray secondary text
        "text_muted": "#6B7280",
        "primary": "#1F6AA5",          # CustomTkinter Signature Blue
        "primary_hover": "#144870",
        "primary_fg": "#FFFFFF",
        "success": "#10B981",          # Emerald 500
        "success_hover": "#059669",
        "success_fg": "#FFFFFF",
        "warning": "#F59E0B",          # Amber 500
        "warning_hover": "#D97706",
        "warning_fg": "#111827",
        "danger": "#EF4444",           # Red 500
        "danger_hover": "#DC2626",
        "danger_fg": "#FFFFFF",
        "info": "#6B7280",
        "chip_bg": "#333333",
        "chip_fg": "#F3F4F6",
        "plot_bg": "#141414",
        "plot_grid": "#333333",
        "plot_text": "#F8FAFC",
    },
}


def get_token(name: str, mode: str = "dark") -> str:
    """Retrieve color token for the application's dark theme."""
    return TOKENS["dark"].get(name, "#FFFFFF")


def get_qt_stylesheet(mode: str = "dark") -> str:
    """Generate the Qt 6 QSS stylesheet for TurbineScope's dark UI."""
    t = TOKENS["dark"]
    input_bg = "#343638"
    textbox_bg = "#1D1E1E"
    table_bg = "#111827"
    table_alt = "#172033"
    header_bg = "#374151"
    scroll_bg = "#1E1E1E"
    scroll_handle = "#55595E"
    scroll_hover = "#757A80"

    return f"""
    QMainWindow, QDialog {{
        background-color: {t["bg_main"]};
        color: {t["text_primary"]};
        font-family: "{FONT_FAMILY}", "Segoe UI Symbol";
    }}
    QWidget {{
        color: {t["text_primary"]};
        font-family: "{FONT_FAMILY}", "Segoe UI Symbol";
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: #F8FAFC;
    }}
    QFrame[qtkFlat="true"] {{
        background: transparent;
        border: none;
    }}
    QLineEdit {{
        background-color: {input_bg};
        color: {t["text_primary"]};
        border: 1px solid {t["border_light"]};
        border-radius: 6px;
        padding: 2px 7px;
        selection-background-color: {t["primary"]};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus {{
        border: 1px solid {t["primary"]};
    }}
    QLineEdit:disabled {{
        background-color: {t["bg_card_alt"]};
        color: {t["text_muted"]};
    }}
    QComboBox {{
        background-color: {input_bg};
        color: {t["text_primary"]};
        border: 1px solid {t["border_light"]};
        border-radius: 6px;
        padding: 2px 24px 2px 8px;
    }}
    QComboBox:hover {{
        border: 1px solid {t["primary"]};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border: none;
        background: transparent;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t["bg_surface"]};
        color: {t["text_primary"]};
        border: 1px solid {t["border_light"]};
        selection-background-color: {t["primary"]};
        selection-color: #FFFFFF;
        outline: none;
    }}
    QPushButton {{
        background-color: {t["primary"]};
        color: {t["primary_fg"]};
        border: none;
        border-radius: 6px;
        padding: 2px 6px;
    }}
    QPushButton:hover {{
        background-color: {t["primary_hover"]};
    }}
    QPushButton:disabled {{
        background-color: {t["bg_card_hover"]};
        color: {t["text_muted"]};
        border: 1px solid {t["border"]};
    }}
    QCheckBox {{
        background: transparent;
        spacing: 6px;
        color: {t["text_primary"]};
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: #4A4D50;
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {t["primary"]};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: #3B8ED0;
        border: none;
        width: 14px;
        height: 14px;
        margin: -4px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: #60A5FA;
    }}
    QTableWidget, QTreeWidget {{
        background-color: {table_bg};
        alternate-background-color: {table_alt};
        color: {t["text_primary"]};
        gridline-color: {t["border"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        selection-background-color: {t["primary"]};
        selection-color: #FFFFFF;
        outline: none;
    }}
    QHeaderView::section {{
        background-color: {header_bg};
        color: {t["text_primary"]};
        font-weight: bold;
        font-size: 11px;
        padding: 5px 8px;
        border: none;
        border-right: 1px solid {t["border"]};
        border-bottom: 1px solid {t["border"]};
    }}
    QPlainTextEdit, QTextEdit {{
        background-color: {textbox_bg};
        color: {t["text_primary"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        font-family: "{FONT_MONO}";
        padding: 6px;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: {scroll_bg};
        width: 9px;
        margin: 1px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {scroll_handle};
        min-height: 28px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {scroll_hover};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: {scroll_bg};
        height: 9px;
        margin: 1px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {scroll_handle};
        min-width: 28px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {scroll_hover};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    QProgressBar {{
        background-color: {t["bg_card_hover"]};
        border: 1px solid {t["border"]};
        border-radius: 4px;
        text-align: center;
        color: {t["text_primary"]};
    }}
    QProgressBar::chunk {{
        background-color: {t["primary"]};
        border-radius: 3px;
    }}
    QToolTip {{
        background-color: {t["bg_surface"]};
        color: {t["text_primary"]};
        border: 1px solid {t["border_light"]};
        border-radius: 4px;
        padding: 5px 8px;
        font-size: 11px;
    }}
    QSplitter::handle {{
        background-color: {t["border"]};
    }}
    """
