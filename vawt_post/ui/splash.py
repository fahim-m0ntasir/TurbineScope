# -*- coding: utf-8 -*-
"""
TurbineScope v4.1.1 Startup Loading Screen
==========================================
Lightweight Qt 6 splash screen displayed immediately on GUI startup so
scientific modules and workspace tabs initialize behind a single clean card.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from vawt_post.icon_assets import build_qt_app_icon, ensure_app_icons
from vawt_post.ui import qt_tk as ctk

APP_TITLE = "TurbineScope"
APP_VERSION = "4.1.1"


class TurbineScopeSplash(QWidget):
    """Frameless dark-mode startup loading card with custom rotor icon and progress."""

    def __init__(self, *, icon_png: Optional[str] = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(508, 304)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 24)

        card_bg = "#11161E"
        card_border = "#283242"
        title_col = "#F8FAFC"
        sub_col = "#94A3B8"
        pill_bg = "#172536"
        pill_border = "#2563EB"
        pill_fg = "#38BDF8"
        track_bg = "#1E293B"
        status_col = "#CBD5E1"
        muted_col = "#64748B"
        divider_col = "#1E293B"

        self.card = QFrame(self)
        self.card.setObjectName("splashCard")
        self.card.setStyleSheet(
            f"QFrame#splashCard {{"
            f"  background-color: {card_bg};"
            f"  border: 1px solid {card_border};"
            f"  border-radius: 14px;"
            f"}}"
        )
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        body = QVBoxLayout(self.card)
        body.setContentsMargins(24, 20, 24, 18)
        body.setSpacing(0)

        # Top accent line
        accent = QFrame(self.card)
        accent.setFixedHeight(3)
        accent.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #2563EB, stop:0.55 #38BDF8, stop:1 #10B981);"
            "border-radius: 1px;"
        )
        body.addWidget(accent)
        body.addSpacing(18)

        # Brand header row (logo + title + version pill + subtitle)
        hdr = QHBoxLayout()
        hdr.setSpacing(16)
        hdr.setContentsMargins(0, 0, 0, 0)

        logo_lbl = QLabel(self.card)
        logo_lbl.setFixedSize(60, 60)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setStyleSheet("background: transparent; border: none;")
        if not (icon_png and os.path.isfile(icon_png)):
            try:
                _, icon_png = ensure_app_icons()
            except Exception:
                icon_png = None
        if icon_png and os.path.isfile(icon_png):
            pix = QPixmap(icon_png).scaled(
                58,
                58,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_lbl.setPixmap(pix)
        hdr.addWidget(logo_lbl)

        title_col_box = QVBoxLayout()
        title_col_box.setSpacing(4)
        title_col_box.setContentsMargins(0, 2, 0, 2)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_row.setContentsMargins(0, 0, 0, 0)

        lbl_title = QLabel(APP_TITLE, self.card)
        lbl_title.setStyleSheet(
            f"color: {title_col}; font-family: 'Segoe UI'; font-size: 23px;"
            " font-weight: 700; background: transparent; border: none;"
        )
        name_row.addWidget(lbl_title)

        lbl_ver = QLabel(f"v{APP_VERSION}", self.card)
        lbl_ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_ver.setStyleSheet(
            f"color: {pill_fg}; background-color: {pill_bg};"
            f" border: 1px solid {pill_border}; border-radius: 6px;"
            " padding: 2px 8px; font-family: 'Segoe UI'; font-size: 11px; font-weight: 700;"
        )
        name_row.addWidget(lbl_ver)
        name_row.addStretch(1)
        title_col_box.addLayout(name_row)

        lbl_sub = QLabel("Vertical-Axis Wind Turbine CFD Studio", self.card)
        lbl_sub.setStyleSheet(
            f"color: {sub_col}; font-family: 'Segoe UI'; font-size: 12px;"
            " font-weight: 500; background: transparent; border: none;"
        )
        title_col_box.addWidget(lbl_sub)
        hdr.addLayout(title_col_box, 1)
        body.addLayout(hdr)

        body.addStretch(1)

        # Progress caption + percentage row
        stat_row = QHBoxLayout()
        stat_row.setContentsMargins(0, 0, 0, 6)
        self.lbl_status = QLabel("Starting Qt 6 workspace...", self.card)
        self.lbl_status.setStyleSheet(
            f"color: {status_col}; font-family: 'Segoe UI'; font-size: 11px;"
            " font-weight: 500; background: transparent; border: none;"
        )
        stat_row.addWidget(self.lbl_status, 1)

        self.lbl_pct = QLabel("0%", self.card)
        self.lbl_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_pct.setStyleSheet(
            f"color: {pill_fg}; font-family: 'Consolas', 'Segoe UI'; font-size: 11px;"
            " font-weight: 700; background: transparent; border: none;"
        )
        stat_row.addWidget(self.lbl_pct)
        body.addLayout(stat_row)

        # Sleek gradient progress bar
        self.progress = QProgressBar(self.card)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        self.progress.setStyleSheet(
            f"QProgressBar {{"
            f"  background-color: {track_bg};"
            f"  border: none;"
            f"  border-radius: 3px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  border-radius: 3px;"
            f"  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 #2563EB, stop:1 #38BDF8);"
            f"}}"
        )
        body.addWidget(self.progress)
        body.addSpacing(14)

        # Bottom divider + subtle capability caption
        div = QFrame(self.card)
        div.setFixedHeight(1)
        div.setStyleSheet(f"background-color: {divider_col}; border: none;")
        body.addWidget(div)
        body.addSpacing(10)

        lbl_footer = QLabel(
            "ANSYS Fluent Live Monitor  •  ASME V&V 20 Workbench Suite  •  Qt 6 Desktop",
            self.card,
        )
        lbl_footer.setStyleSheet(
            f"color: {muted_col}; font-family: 'Segoe UI'; font-size: 10px;"
            " background: transparent; border: none;"
        )
        body.addWidget(lbl_footer)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)

    def set_progress(self, percent: int, message: str) -> None:
        clamped = max(0, min(100, int(percent)))
        self.progress.setValue(clamped)
        self.lbl_pct.setText(f"{clamped}%")
        self.lbl_status.setText(message)
        self.repaint()
        qapp = ctk.ensure_qapp()
        qapp.processEvents()


def launch_with_splash() -> None:
    """Display the startup splash immediately, initialize VAWTPostApp cleanly, then swap windows."""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            windll = getattr(ctypes, "windll", None)
            if windll is not None:
                windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    f"TurbineScope.VAWT.CFDStudio.{APP_VERSION}"
                )
        except Exception:
            pass

    qapp = ctk.ensure_qapp()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    ico_path, png_path = ensure_app_icons()
    app_icon = build_qt_app_icon(ico_path, png_path)
    if not app_icon.isNull():
        qapp.setWindowIcon(app_icon)

    splash = TurbineScopeSplash(icon_png=png_path)
    if not app_icon.isNull():
        splash.setWindowIcon(app_icon)
    splash.show()
    splash.set_progress(10, "Initializing Qt 6 desktop environment...")

    splash.set_progress(24, "Loading aerodynamics & plotting modules...")
    from vawt_post.ui.app import VAWTPostApp

    app = None
    try:
        app = VAWTPostApp(progress_cb=splash.set_progress, show_immediately=False)
        splash.set_progress(100, "Launching TurbineScope workspace...")

        app.show()
        app._apply_window_icon()
        app._sync_win32_icon_and_theme()
        app.raise_()
        app.activateWindow()
        app.update()
        qapp.processEvents()
    finally:
        try:
            splash.close()
            splash.deleteLater()
            qapp.processEvents()
        except Exception:
            pass

    if app is not None:
        app.mainloop()
