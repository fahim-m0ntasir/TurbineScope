# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor & Studio — Icon & Branding Assets
=========================================================
Generates and resolves the multi-resolution Windows `.ico` and `.png` application
logo (3-blade Darrieus VAWT rotor with aerodynamic streamline rings) so the
custom icon appears in the Windows taskbar, Alt+Tab switcher, window titlebar,
header bar, and compiled PyInstaller `.exe`.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any, Optional, Tuple

from PIL import Image, ImageDraw


def get_assets_dir() -> str:
    """Return the assets directory path (compatible with normal Python and PyInstaller --onefile)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = os.path.join(getattr(sys, "_MEIPASS"), "vawt_post", "assets")
        if os.path.isdir(base_dir):
            return base_dir
    here = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(here, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    return assets_dir


def render_vawt_logo_image(size: int = 256) -> Image.Image:
    """Render a crisp, high-resolution anti-aliased VAWT rotor logo via 4x supersampling."""
    ss = 4
    w = size * ss
    c = w / 2.0
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    # 1. Rounded Dark-Navy Squircle Badge Background with Outer Cyan-Blue Rim
    pad = int(w * 0.04)
    radius = int(w * 0.23)
    draw.rounded_rectangle(
        [pad, pad, w - pad, w - pad],
        radius=radius,
        fill=(15, 23, 42, 255),       # Slate 900 (#0F172A)
        outline=(56, 189, 248, 235),  # Sky 400 (#38BDF8)
        width=max(2, int(w * 0.028)),
    )

    # Subtle inner glow ring
    in_pad = int(w * 0.085)
    draw.rounded_rectangle(
        [in_pad, in_pad, w - in_pad, w - in_pad],
        radius=int(radius * 0.84),
        fill=(22, 34, 62, 255),
        outline=(37, 99, 235, 110),
        width=max(1, int(w * 0.012)),
    )

    # 2. Swept Rotor Path Circle (dashed/glowing aerodynamic streamring)
    r_orbit = w * 0.315
    bbox_orbit = [c - r_orbit, c - r_orbit, c + r_orbit, c + r_orbit]
    for start_deg in range(0, 360, 120):
        # Wake trail arc behind each blade
        draw.arc(
            bbox_orbit,
            start=start_deg + 16,
            end=start_deg + 98,
            fill=(56, 189, 248, 175),
            width=max(2, int(w * 0.022)),
        )
        draw.arc(
            bbox_orbit,
            start=start_deg + 50,
            end=start_deg + 102,
            fill=(16, 185, 129, 210),
            width=max(2, int(w * 0.018)),
        )

    # 3. Radial Support Struts (3 struts from hub to blades)
    r_strut = r_orbit * 0.96
    blade_angles_deg = [-30.0, 90.0, 210.0]
    for ang_deg in blade_angles_deg:
        rad = math.radians(ang_deg)
        x_end = c + r_strut * math.cos(rad)
        y_end = c + r_strut * math.sin(rad)
        draw.line(
            [(c, c), (x_end, y_end)],
            fill=(148, 163, 184, 190),
            width=max(2, int(w * 0.020)),
        )

    # 4. Three Cambered Airfoil Blades Placed along the Rotor Orbit
    blade_colors = [
        ((56, 189, 248, 255), (224, 242, 254, 255)),   # Blade 1: Bright Cyan
        ((16, 185, 129, 255), (209, 250, 229, 255)),   # Blade 2: Emerald Green
        ((59, 130, 246, 255), (219, 234, 254, 255)),   # Blade 3: Royal Blue
    ]
    chord = w * 0.25
    thick = w * 0.072

    for idx, ang_deg in enumerate(blade_angles_deg):
        rad = math.radians(ang_deg)
        bx = c + r_orbit * math.cos(rad)
        by = c + r_orbit * math.sin(rad)

        # Tangent direction (CCW rotation)
        tan_rad = rad + math.pi / 2.0
        cos_t = math.cos(tan_rad)
        sin_t = math.sin(tan_rad)
        # Normal outward
        cos_n = math.cos(rad)
        sin_n = math.sin(rad)

        # Construct smooth NACA-like airfoil polygon in local (s, n) coordinates
        # s in [-0.45*chord, +0.55*chord] (leading edge at +0.55*chord in direction of motion)
        pts = []
        n_pts = 28
        for i in range(n_pts + 1):
            frac = i / n_pts  # 0 (LE) -> 1 (TE)
            s_local = (0.52 - frac) * chord
            # NACA thickness distribution + slight outward camber
            yt = thick * (
                1.45 * math.sqrt(max(frac, 0.0))
                - 0.62 * frac
                - 0.55 * (frac ** 2)
                - 0.28 * (frac ** 3)
            )
            camber = 0.16 * thick * math.sin(math.pi * frac)
            n_local = camber + yt
            px = bx + s_local * cos_t + n_local * cos_n
            py = by + s_local * sin_t + n_local * sin_n
            pts.append((px, py))

        for i in range(n_pts - 1, 0, -1):
            frac = i / n_pts
            s_local = (0.52 - frac) * chord
            yt = thick * (
                1.45 * math.sqrt(max(frac, 0.0))
                - 0.62 * frac
                - 0.55 * (frac ** 2)
                - 0.28 * (frac ** 3)
            )
            camber = 0.16 * thick * math.sin(math.pi * frac)
            n_local = camber - 0.75 * yt
            px = bx + s_local * cos_t + n_local * cos_n
            py = by + s_local * sin_t + n_local * sin_n
            pts.append((px, py))

        fill_col, edge_col = blade_colors[idx % len(blade_colors)]
        draw.polygon(pts, fill=fill_col, outline=edge_col, width=max(1, int(w * 0.010)))

    # 5. Central Rotor Shaft & Hub
    r_hub_outer = w * 0.088
    r_hub_inner = w * 0.042
    draw.ellipse(
        [c - r_hub_outer, c - r_hub_outer, c + r_hub_outer, c + r_hub_outer],
        fill=(30, 41, 59, 255),
        outline=(56, 189, 248, 255),
        width=max(2, int(w * 0.020)),
    )
    draw.ellipse(
        [c - r_hub_inner, c - r_hub_inner, c + r_hub_inner, c + r_hub_inner],
        fill=(248, 250, 252, 255),
    )

    return img.resize((size, size), Image.Resampling.LANCZOS)


def ensure_app_icons() -> Tuple[str, str]:
    """Ensure `app_icon.ico` and `app_icon.png` exist on disk and return `(ico_path, png_path)`."""
    assets_dir = get_assets_dir()
    ico_path = os.path.join(assets_dir, "app_icon.ico")
    png_path = os.path.join(assets_dir, "app_icon.png")

    if not os.path.isfile(ico_path) or not os.path.isfile(png_path):
        try:
            master_img = render_vawt_logo_image(256)
            master_img.save(png_path, format="PNG")
            master_img.save(
                ico_path,
                format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
            )
        except Exception:
            pass

    return ico_path, png_path


def build_qt_app_icon(
    ico_path: Optional[str] = None,
    png_path: Optional[str] = None,
) -> Any:
    """Build a multi-resolution PySide6 QIcon from PNG/ICO assets for crisp taskbar/titlebar rendering."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPixmap

    if ico_path is None or png_path is None:
        def_ico, def_png = ensure_app_icons()
        ico_path = ico_path or def_ico
        png_path = png_path or def_png

    ic = QIcon()
    if png_path and os.path.isfile(png_path):
        src_pm = QPixmap(png_path)
        if not src_pm.isNull():
            for sz in (16, 20, 24, 32, 40, 48, 64, 128, 256):
                scaled = src_pm.scaled(
                    sz,
                    sz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ic.addPixmap(scaled)
    if ico_path and os.path.isfile(ico_path):
        ico_ic = QIcon(ico_path)
        for sz in (16, 24, 32, 48, 64, 128, 256):
            pm = ico_ic.pixmap(sz, sz)
            if not pm.isNull():
                ic.addPixmap(pm)
    return ic


def apply_native_win32_icon(
    hwnd: int,
    ico_path: Optional[str] = None,
) -> Tuple[int, int]:
    """Explicitly bind big and small HICONs to a Win32 HWND via WM_SETICON."""
    if not sys.platform.startswith("win") or not hwnd:
        return (0, 0)
    if not ico_path:
        ico_path, _ = ensure_app_icons()
    if not ico_path or not os.path.isfile(ico_path):
        return (0, 0)
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SendMessageW.restype = wintypes.LPARAM
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        h_big = int(user32.LoadImageW(None, ico_path, IMAGE_ICON, 48, 48, LR_LOADFROMFILE) or 0)
        h_small = int(user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE) or 0)
        if h_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
        if h_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
        return (h_big, h_small)
    except Exception:
        return (0, 0)


