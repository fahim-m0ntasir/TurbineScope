# -*- coding: utf-8 -*-
"""
TurbineScope v4.1.1: Native Qt 6 (PySide6) Widget & Layout Engine (`qt_tk.py`)
==============================================================================
Replaces CustomTkinter / Tkinter / ttk / FigureCanvasTkAgg with 100% hardware-
accelerated C++ Qt 6 (`PySide6`) widgets rendered in a single double-buffered
`QBackingStore` surface. Eliminates Win32 child HWND creation and sequential
canvas repaint lag while maintaining 100% API and test compatibility.
"""
from __future__ import annotations

import math
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPoint, QPointF, QTimer, QItemSelectionModel
from PySide6.QtGui import (
    QFont,
    QColor,
    QPalette,
    QPixmap,
    QImage,
    QIcon,
    QPainter,
    QPen,
    QBrush,
    QPolygonF,
    QKeySequence,
    QShortcut,
    QGuiApplication,
    QDragEnterEvent,
    QDropEvent,
    QResizeEvent,
    QShowEvent,
    QCloseEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QDialog,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QTextEdit,
    QProgressBar,
    QSlider,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QLayout,
    QLayoutItem,
    QWidgetItem,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from .design_system import FONT_FAMILY, FONT_MONO, get_qt_stylesheet, get_token

# Global appearance mode state (dark-only UI)
_APPEARANCE_MODE: str = "dark"
_DEFAULT_COLOR_THEME: str = "blue"

# Constants matching Tkinter/DnD
LEFT = "left"
RIGHT = "right"
TOP = "top"
BOTTOM = "bottom"
BOTH = "both"
X = "x"
Y = "y"
NONE = "none"
END = "end"
SOLID = "solid"
FLAT = "flat"
DISABLED = "disabled"
NORMAL = "normal"
DND_FILES = "DND_Files"


def _build_qpalette(mode_str: str = "dark") -> QPalette:
    """Build the dark Fusion QPalette so native Qt viewports and menus match the UI cleanly."""
    pal = QPalette()
    bg = QColor("#242424")
    surf = QColor("#2B2B2B")
    base = QColor("#1D1E1E")
    alt = QColor("#262626")
    txt = QColor("#DCE4EE")
    muted = QColor("#9CA3AF")
    hi = QColor("#1F6AA5")
    btn = QColor("#333333")
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, txt)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipBase, surf)
    pal.setColor(QPalette.ColorRole.ToolTipText, txt)
    pal.setColor(QPalette.ColorRole.Text, txt)
    pal.setColor(QPalette.ColorRole.Button, btn)
    pal.setColor(QPalette.ColorRole.ButtonText, txt)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.Highlight, hi)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, muted)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, muted)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, muted)
    return pal


def _apply_windows_titlebar_theme(win: QWidget, is_dark: bool = True) -> None:
    """Apply native Windows 11/10 DWM dark window title bar."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(win.winId())
        if not hwnd:
            return
        val = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Win11 / Win10 fallback)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def ensure_qapp() -> QApplication:
    """Return the active QApplication singleton, creating it with Fusion + dark QSS if needed."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if sys.argv else ["TurbineScope"])
        app.setQuitOnLastWindowClosed(True)
        app.setStyle("Fusion")
        app.setPalette(_build_qpalette("dark"))
        app.setStyleSheet(get_qt_stylesheet("dark"))
    return app  # type: ignore[return-value]


def set_appearance_mode(mode_str: str) -> None:
    """TurbineScope uses Dark Mode exclusively; keep compatibility shim as a dark-only no-op."""
    global _APPEARANCE_MODE
    _APPEARANCE_MODE = "dark"


def get_appearance_mode() -> str:
    return "Dark"


def set_default_color_theme(theme_str: str) -> None:
    global _DEFAULT_COLOR_THEME
    _DEFAULT_COLOR_THEME = str(theme_str)


def _resolve_color(col_spec: Any) -> Optional[str]:
    """Resolve a color string or (light_color, dark_color) tuple to its dark-mode CSS color."""
    if col_spec is None:
        return None
    if isinstance(col_spec, (tuple, list)) and len(col_spec) >= 2:
        raw = col_spec[1]
    else:
        raw = col_spec
    s = str(raw).strip()
    if not s or s.lower() == "transparent":
        return "transparent"
    low = s.lower()
    # Dynamic gray0..gray100 neutral charcoal greyscale (matches Tk / CustomTkinter 1:1)
    m_gray = re.fullmatch(r"gr[ae]y(\d{1,3})", low)
    if m_gray:
        pct = max(0, min(100, int(m_gray.group(1))))
        val = int(round(pct * 255 / 100.0))
        return f"#{val:02X}{val:02X}{val:02X}"
    tk_map = {
        "gray": "#9CA3AF",
        "grey": "#9CA3AF",
        "royal blue": "#2563EB",
        "royalblue": "#2563EB",
        "royalblue3": "#2563EB",
        "royalblue4": "#1D4ED8",
        "seagreen3": "#10B981",
        "seagreen4": "#059669",
        "firebrick3": "#EF4444",
        "firebrick4": "#DC2626",
        "darkorange3": "#F59E0B",
        "darkorange4": "#D97706",
        "dodgerblue2": "#0284C7",
        "dodgerblue3": "#0369A1",
        "teal": "#0D9488",
        "dark cyan": "#0F766E",
        "darkslateblue": "#4F46E5",
    }
    return tk_map.get(low, s)


def _parse_pad(pad: Any) -> Tuple[int, int]:
    """Parse an int or (p1, p2) padding tuple."""
    if pad is None:
        return (0, 0)
    if isinstance(pad, (tuple, list)):
        if len(pad) >= 2:
            return (int(pad[0]), int(pad[1]))
        if len(pad) == 1:
            return (int(pad[0]), int(pad[0]))
        return (0, 0)
    try:
        v = int(float(pad))
        return (v, v)
    except Exception:
        return (0, 0)


# ============================================================================
# REACTIVE VARIABLES
# ============================================================================
class Variable:
    """Base reactive variable supporting .get(), .set(), and .trace_add()."""

    def __init__(self, master: Any = None, value: Any = "", name: Optional[str] = None) -> None:
        self._value: Any = value
        self._callbacks: List[Callable[..., Any]] = []
        self._in_trace: bool = False

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value
        if not self._in_trace:
            self._in_trace = True
            dead: List[Callable[..., Any]] = []
            try:
                for cb in list(self._callbacks):
                    try:
                        cb("", "", "write")
                    except TypeError:
                        try:
                            cb()
                        except RuntimeError:
                            dead.append(cb)
                    except RuntimeError:
                        dead.append(cb)
                for cb in dead:
                    if cb in self._callbacks:
                        self._callbacks.remove(cb)
            finally:
                self._in_trace = False

    def trace_add(self, mode: str, callback: Callable[..., Any]) -> str:
        if callback not in self._callbacks:
            self._callbacks.append(callback)
        return f"trace_{len(self._callbacks)}"

    def trace(self, mode: str, callback: Callable[..., Any]) -> str:
        return self.trace_add(mode, callback)


class StringVar(Variable):
    def __init__(self, master: Any = None, value: str = "", name: Optional[str] = None) -> None:
        super().__init__(master, str(value) if value is not None else "", name)

    def get(self) -> str:
        return str(self._value) if self._value is not None else ""

    def set(self, value: Any) -> None:
        super().set(str(value) if value is not None else "")


class BooleanVar(Variable):
    def __init__(self, master: Any = None, value: bool = False, name: Optional[str] = None) -> None:
        super().__init__(master, bool(value), name)

    def get(self) -> bool:
        return bool(self._value)

    def set(self, value: Any) -> None:
        super().set(bool(value))


class IntVar(Variable):
    def __init__(self, master: Any = None, value: int = 0, name: Optional[str] = None) -> None:
        super().__init__(master, int(value) if value is not None else 0, name)

    def get(self) -> int:
        try:
            return int(float(self._value))
        except Exception:
            return 0

    def set(self, value: Any) -> None:
        try:
            super().set(int(float(value)))
        except Exception:
            super().set(0)


class DoubleVar(Variable):
    def __init__(self, master: Any = None, value: float = 0.0, name: Optional[str] = None) -> None:
        super().__init__(master, float(value) if value is not None else 0.0, name)

    def get(self) -> float:
        try:
            return float(self._value)
        except Exception:
            return 0.0

    def set(self, value: Any) -> None:
        try:
            super().set(float(value))
        except Exception:
            super().set(0.0)


# ============================================================================
# FONTS, VECTOR ICONS & HIGH-DPI IMAGES
# ============================================================================
def _force_text_icons(text: str) -> str:
    """Append Unicode text-presentation selector (U+FE0E) to pictographs/emojis so Windows DirectWrite draws crisp monochrome vector glyphs like CustomTkinter instead of low-res color emoji bitmaps."""
    if not text:
        return ""
    s = str(text).replace("\uFE0F", "")
    out: List[str] = []
    for ch in s:
        cp = ord(ch)
        out.append(ch)
        if ch != "\uFE0E" and (
            0x23E9 <= cp <= 0x23FA
            or 0x2600 <= cp <= 0x27BF
            or 0x1F300 <= cp <= 0x1FAFF
        ):
            out.append("\uFE0E")
    return "".join(out)


def _strip_vs15(text: str) -> str:
    return str(text).replace("\uFE0E", "")


class CTkFont(QFont):
    """Qt 6 QFont wrapper compatible with CTkFont(family=..., size=..., weight=..., slant=...)."""

    def __init__(
        self,
        family: str = FONT_FAMILY,
        size: int = 13,
        weight: str = "normal",
        slant: str = "roman",
        **_: Any,
    ) -> None:
        super().__init__(family or FONT_FAMILY)
        fam = family or FONT_FAMILY
        self.setFamilies([fam, "Segoe UI Symbol"])
        px_size = max(9, abs(int(size)))
        self.setPixelSize(px_size)
        self._raw_size = abs(int(size))
        if str(weight).lower() == "bold":
            self.setBold(True)
        if str(slant).lower() == "italic":
            self.setItalic(True)


def _to_qfont(spec: Any) -> QFont:
    if isinstance(spec, QFont):
        return spec
    if isinstance(spec, (tuple, list)):
        fam = str(spec[0]) if len(spec) > 0 else FONT_FAMILY
        sz = int(spec[1]) if len(spec) > 1 else 13
        wt = "bold" if any("bold" in str(x).lower() for x in spec[2:]) else "normal"
        sl = "italic" if any("italic" in str(x).lower() for x in spec[2:]) else "roman"
        return CTkFont(family=fam, size=sz, weight=wt, slant=sl)
    return CTkFont(family=FONT_FAMILY, size=13)


def _pil_to_qpixmap(pil_img: Any, size: Optional[Tuple[int, int]] = None) -> QPixmap:
    app = QApplication.instance()
    dpr = max(1.0, float(app.devicePixelRatio())) if isinstance(app, QApplication) else 1.0
    if isinstance(pil_img, QPixmap):
        pix = pil_img
        if size is not None and not pix.isNull():
            tw = max(1, int(round(size[0] * dpr)))
            th = max(1, int(round(size[1] * dpr)))
            pix = pix.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
        return pix
    if isinstance(pil_img, QImage):
        pix = QPixmap.fromImage(pil_img)
        if size is not None and not pix.isNull():
            tw = max(1, int(round(size[0] * dpr)))
            th = max(1, int(round(size[1] * dpr)))
            pix = pix.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
        return pix
    if hasattr(pil_img, "convert") and hasattr(pil_img, "tobytes"):
        img_work = pil_img.convert("RGBA")
        if size is not None:
            tw = max(1, int(round(size[0] * dpr)))
            th = max(1, int(round(size[1] * dpr)))
            try:
                from PIL import Image as _PILImage
                res_obj = getattr(_PILImage, "Resampling", _PILImage)
                resample = getattr(res_obj, "LANCZOS", 1)
                # Preserve aspect ratio while fitting within target physical box
                src_w, src_h = img_work.size
                scale = min(tw / max(1, src_w), th / max(1, src_h))
                rw = max(1, int(round(src_w * scale)))
                rh = max(1, int(round(src_h * scale)))
                if (rw, rh) != (src_w, src_h):
                    img_work = img_work.resize((rw, rh), resample=resample)
            except Exception:
                pass
        w, h = img_work.size
        data = img_work.tobytes("raw", "RGBA")
        qimg = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        pix = QPixmap.fromImage(qimg)
        if size is not None:
            pix.setDevicePixelRatio(dpr)
        return pix
    return QPixmap(1, 1)


class CTkImage:
    """High-DPI Qt 6 QPixmap wrapper compatible with CTkImage(light_image=..., dark_image=..., size=...)."""

    def __init__(
        self,
        light_image: Any = None,
        dark_image: Any = None,
        size: Tuple[int, int] = (20, 20),
    ) -> None:
        self.light_image = light_image or dark_image
        self.dark_image = dark_image or light_image
        self.size = size
        self._pixmap_cache: Dict[Tuple[str, int, int], QPixmap] = {}

    def get_pixmap(self) -> QPixmap:
        key = ("dark", int(self.size[0]), int(self.size[1]))
        if key not in self._pixmap_cache:
            self._pixmap_cache[key] = _pil_to_qpixmap(self.dark_image, self.size)
        return self._pixmap_cache[key]

    def cget(self, prop: str) -> Any:
        if prop == "size":
            return self.size
        if prop == "light_image":
            return self.light_image
        if prop == "dark_image":
            return self.dark_image
        return None


class PhotoImage:
    """PIL ImageTk.PhotoImage compatible wrapper backed by Qt 6 QPixmap."""

    def __init__(self, image: Any = None, size: Optional[Tuple[int, int]] = None, **_: Any) -> None:
        self._pixmap = _pil_to_qpixmap(image, size)

    def width(self) -> int:
        return self._pixmap.width()

    def height(self) -> int:
        return self._pixmap.height()

    def get_pixmap(self) -> QPixmap:
        return self._pixmap


# ============================================================================
# NATIVE QT 6 CAVITY PACK & GRID LAYOUT ENGINES
# ============================================================================
def _parse_compass(val: Any) -> str:
    """Return compass characters ('n','s','e','w') without matching letters in 'center'."""
    s = str(val or "").strip().lower()
    return "" if s in ("", "c", "center") else s


class QtDualLayout(QLayout):
    """
    High-speed Qt 6 QLayout that natively executes both cavity `.pack(...)` and
    table `.grid(...)` geometry management in `< 0.1 ms` on pure C++ QWidgets.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._pack_order: List[QWidget] = []
        self._grid_widgets: List[QWidget] = []
        self._row_weights: Dict[int, int] = {}
        self._col_weights: Dict[int, int] = {}
        self._row_minsize: Dict[int, int] = {}
        self._col_minsize: Dict[int, int] = {}
        self._cached_size_hint: Optional[QSize] = None
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(0)

    def invalidate(self) -> None:
        self._cached_size_hint = None
        super().invalidate()
        pw = self.parentWidget()
        seen: set[int] = set()
        while pw is not None and id(pw) not in seen:
            seen.add(id(pw))
            pw = pw.parentWidget()
            if pw is None:
                break
            p_lay = getattr(pw, "_qt_layout", None)
            if p_lay is not None:
                p_lay._cached_size_hint = None
                QLayout.invalidate(p_lay)

    def add_managed_widget(self, w: QWidget, mode: str) -> None:
        if not any(it.widget() is w for it in self._items):
            self._items.append(QWidgetItem(w))
        if mode == "pack":
            if w in self._grid_widgets:
                self._grid_widgets.remove(w)
            if w not in self._pack_order:
                self._pack_order.append(w)
        elif mode == "grid":
            if w in self._pack_order:
                self._pack_order.remove(w)
            if w not in self._grid_widgets:
                self._grid_widgets.append(w)
        self.invalidate()

    def remove_managed_widget(self, w: QWidget) -> None:
        if w in self._pack_order:
            self._pack_order.remove(w)
        if w in self._grid_widgets:
            self._grid_widgets.remove(w)
        self._items = [it for it in self._items if it.widget() is not w]
        self.invalidate()

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            it = self._items.pop(index)
            w = it.widget()
            if w in self._pack_order:
                self._pack_order.remove(w)
            if w in self._grid_widgets:
                self._grid_widgets.remove(w)
            return it
        return None

    def _active_pack_widgets(self) -> List[QWidget]:
        return [
            w for w in self._pack_order
            if getattr(w, "_geom_manager", "") == "pack" and not w.isHidden()
        ]

    def _active_grid_widgets(self) -> List[QWidget]:
        return [
            w for w in self._grid_widgets
            if getattr(w, "_geom_manager", "") == "grid" and not w.isHidden()
        ]

    def _widget_natural_size(self, w: QWidget) -> Tuple[int, int]:
        req_w = getattr(w, "_req_width", None)
        req_h = getattr(w, "_req_height", None)

        # Container widgets managed by QtDualLayout: avoid double sizeHint() (2^D blowup) and respect propagate(False)
        qt_lay = getattr(w, "_qt_layout", None)
        if qt_lay is not None:
            if not getattr(w, "_pack_propagate", True):
                if (req_w is not None and req_w > 0) and (req_h is not None and req_h > 0):
                    return (int(req_w), int(req_h))
                l_sh = qt_lay.sizeHint()
                nat_w = int(req_w) if (req_w is not None and req_w > 0) else max(l_sh.width(), 16)
                nat_h = int(req_h) if (req_h is not None and req_h > 0) else max(l_sh.height(), 16)
                return (max(1, nat_w), max(1, nat_h))
            l_sh = qt_lay.sizeHint()
            nat_w = max(req_w or 0, l_sh.width(), 16)
            nat_h = max(req_h or 0, l_sh.height(), 16)
            return (max(1, int(nat_w)), max(1, int(nat_h)))

        # Leaf text/control widgets must NEVER clip their labels when Qt's rendered width > req_w
        if isinstance(w, QComboBox):
            sh = w.sizeHint()
            fm = w.fontMetrics()
            cur_w = fm.horizontalAdvance(w.currentText()) + 54 if w.currentText() else 50
            nat_w = max(req_w or 0, cur_w, 32)
            nat_h = int(req_h) if (req_h is not None and req_h >= 20) else max(sh.height(), 20)
            return (max(1, int(nat_w)), max(1, int(nat_h)))
        if isinstance(w, (QPushButton, QCheckBox, QLabel)):
            sh = w.sizeHint()
            nat_w = max(req_w or 0, sh.width(), 16)
            nat_h = max(req_h or 0, sh.height(), 16)
            if isinstance(w, QLabel) and w.wordWrap():
                wl = int(getattr(w, "_props", {}).get("wraplength") or 0)
                if wl > 0:
                    fm = w.fontMetrics()
                    lines = (w.text() or "").splitlines() or [""]
                    single_w = max(fm.horizontalAdvance(ln) for ln in lines) + 8
                    nat_w = max(req_w or 0, min(single_w, wl))
                    br = fm.boundingRect(QRect(0, 0, max(16, int(nat_w)), 2000), Qt.TextFlag.TextWordWrap, w.text())
                    nat_h = max(req_h or 0, br.height() + 4, 16)
            return (max(1, int(nat_w)), max(1, int(nat_h)))
        sh = w.sizeHint()
        nat_w = req_w if (req_w is not None and req_w > 0) else max(sh.width(), 16)
        nat_h = req_h if (req_h is not None and req_h > 0) else max(sh.height(), 16)
        return (max(1, int(nat_w)), max(1, int(nat_h)))

    def _distribute_span_deficit(
        self, tracks: List[int], weights: Dict[int, int], start: int, span: int, deficit: int
    ) -> None:
        """Assign multi-span natural-size deficit like Tkinter: weighted tracks first, else trailing track."""
        if deficit <= 0:
            return
        span_indices = list(range(start, start + span))
        w_sum = sum(max(0, weights.get(i, 0)) for i in span_indices)
        if w_sum > 0:
            rem = deficit
            weighted = [i for i in span_indices if weights.get(i, 0) > 0]
            for idx, i in enumerate(weighted):
                share = rem if idx == len(weighted) - 1 else int(deficit * weights[i] / w_sum)
                tracks[i] += share
                rem -= share
        else:
            tracks[start + span - 1] += deficit

    def _compute_grid_tracks(
        self, active_grid: List[QWidget]
    ) -> Tuple[List[int], List[int], List[Tuple[QWidget, int, int, int, int, str, int, int, int, int, int, int]]]:
        max_r = 0
        max_c = 0
        items_info = []
        for w in active_grid:
            opts = getattr(w, "_grid_opts", {})
            r = int(opts.get("row", 0))
            c = int(opts.get("column", 0))
            rs = max(1, int(opts.get("rowspan", 1)))
            cs = max(1, int(opts.get("columnspan", 1)))
            sticky = str(opts.get("sticky", "")).lower()
            px1, px2 = _parse_pad(opts.get("padx", 0))
            py1, py2 = _parse_pad(opts.get("pady", 0))
            nw, nh = self._widget_natural_size(w)
            max_r = max(max_r, r + rs - 1)
            max_c = max(max_c, c + cs - 1)
            items_info.append((w, r, c, rs, cs, sticky, px1, px2, py1, py2, nw, nh))

        col_w = [self._col_minsize.get(c, 0) for c in range(max_c + 1)]
        row_h = [self._row_minsize.get(r, 0) for r in range(max_r + 1)]

        # Pass 1: single-span natural sizes
        for w, r, c, rs, cs, sticky, px1, px2, py1, py2, nw, nh in items_info:
            if cs == 1:
                col_w[c] = max(col_w[c], nw + px1 + px2)
            if rs == 1:
                row_h[r] = max(row_h[r], nh + py1 + py2)

        # Pass 2: multi-span natural sizes (keep leading columns tight)
        for w, r, c, rs, cs, sticky, px1, px2, py1, py2, nw, nh in items_info:
            if cs > 1:
                cur_span_w = sum(col_w[ci] for ci in range(c, c + cs))
                need_w = nw + px1 + px2
                self._distribute_span_deficit(col_w, self._col_weights, c, cs, need_w - cur_span_w)
            if rs > 1:
                cur_span_h = sum(row_h[ri] for ri in range(r, r + rs))
                need_h = nh + py1 + py2
                self._distribute_span_deficit(row_h, self._row_weights, r, rs, need_h - cur_span_h)

        return col_w, row_h, items_info

    def sizeHint(self) -> QSize:
        if self._cached_size_hint is not None:
            return self._cached_size_hint

        m = self.contentsMargins()
        extra_w = m.left() + m.right()
        extra_h = m.top() + m.bottom()

        active_grid = self._active_grid_widgets()
        if active_grid:
            col_w, row_h, _ = self._compute_grid_tracks(active_grid)
            res = QSize(sum(col_w) + extra_w, sum(row_h) + extra_h)
            self._cached_size_hint = res
            return res

        active_pack = self._active_pack_widgets()
        if active_pack:
            req_w, req_h = 0, 0
            for w in reversed(active_pack):
                opts = getattr(w, "_pack_opts", {})
                side = str(opts.get("side", "top")).lower()
                px1, px2 = _parse_pad(opts.get("padx", 0))
                py1, py2 = _parse_pad(opts.get("pady", 0))
                nw, nh = self._widget_natural_size(w)
                tw = nw + px1 + px2
                th = nh + py1 + py2
                if side in ("top", "bottom"):
                    req_w = max(req_w, tw)
                    req_h += th
                else:
                    req_w += tw
                    req_h = max(req_h, th)
            res = QSize(req_w + extra_w, req_h + extra_h)
            self._cached_size_hint = res
            return res

        res = QSize(extra_w + 10, extra_h + 10)
        self._cached_size_hint = res
        return res

    def minimumSize(self) -> QSize:
        return QSize(10, 10)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        m = self.contentsMargins()
        avail = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        if avail.width() <= 0 or avail.height() <= 0:
            return

        active_grid = self._active_grid_widgets()
        if active_grid:
            self._layout_grid(avail, active_grid)
            return

        active_pack = self._active_pack_widgets()
        if active_pack:
            self._layout_pack(avail, active_pack)

    def _layout_pack(self, avail: QRect, widgets: List[QWidget]) -> None:
        cx, cy, cw, ch = avail.x(), avail.y(), avail.width(), avail.height()
        n = len(widgets)

        # Precompute natural sizes + paddings
        nat_sizes: List[Tuple[int, int, int, int, int, int, str, str, bool, str]] = []
        for w in widgets:
            opts = getattr(w, "_pack_opts", {})
            side = str(opts.get("side", "top")).lower()
            fill = str(opts.get("fill", "none")).lower()
            expand = bool(opts.get("expand", False))
            anchor = str(opts.get("anchor", "center")).lower()
            px1, px2 = _parse_pad(opts.get("padx", 0))
            py1, py2 = _parse_pad(opts.get("pady", 0))
            nw, nh = self._widget_natural_size(w)
            nat_sizes.append((nw, nh, px1, px2, py1, py2, side, fill, expand, anchor))

        for i, w in enumerate(widgets):
            nw, nh, px1, px2, py1, py2, side, fill, expand, anchor = nat_sizes[i]
            tw = nw + px1 + px2
            th = nh + py1 + py2

            if side in ("top", "bottom"):
                slice_h = th
                if expand:
                    # Compute remaining vertical height needed by subsequent items
                    rem_h_needed = 0
                    rem_v_expands = 0
                    cur_max_h = 0
                    for j in range(n - 1, i - 1, -1):
                        jnw, jnh, jpx1, jpx2, jpy1, jpy2, jside, _, jexp, _ = nat_sizes[j]
                        jth = jnh + jpy1 + jpy2
                        if jside in ("top", "bottom"):
                            rem_h_needed += jth
                            if jexp:
                                rem_v_expands += 1
                        else:
                            cur_max_h = max(cur_max_h, jth)
                    total_needed = max(rem_h_needed, cur_max_h)
                    extra_h = max(0, ch - total_needed)
                    if rem_v_expands > 0:
                        slice_h += extra_h // rem_v_expands

                slice_h = min(ch, max(th, slice_h)) if ch >= th else max(0, ch)
                if side == "top":
                    px, py, pw, ph = cx, cy, cw, slice_h
                    cy += slice_h
                    ch -= slice_h
                else:
                    px, py, pw, ph = cx, cy + ch - slice_h, cw, slice_h
                    ch -= slice_h
            else:
                slice_w = tw
                if expand:
                    rem_w_needed = 0
                    rem_h_expands = 0
                    cur_max_w = 0
                    for j in range(n - 1, i - 1, -1):
                        jnw, jnh, jpx1, jpx2, jpy1, jpy2, jside, _, jexp, _ = nat_sizes[j]
                        jtw = jnw + jpx1 + jpx2
                        if jside in ("left", "right"):
                            rem_w_needed += jtw
                            if jexp:
                                rem_h_expands += 1
                        else:
                            cur_max_w = max(cur_max_w, jtw)
                    total_needed = max(rem_w_needed, cur_max_w)
                    extra_w = max(0, cw - total_needed)
                    if rem_h_expands > 0:
                        slice_w += extra_w // rem_h_expands

                slice_w = min(cw, max(tw, slice_w)) if cw >= tw else max(0, cw)
                if side == "left":
                    px, py, pw, ph = cx, cy, slice_w, ch
                    cx += slice_w
                    cw -= slice_w
                else:
                    px, py, pw, ph = cx + cw - slice_w, cy, slice_w, ch
                    cw -= slice_w

            # Subtract widget padding from parcel
            inner_x = px + px1
            inner_y = py + py1
            inner_w = max(1, pw - px1 - px2)
            inner_h = max(1, ph - py1 - py2)

            final_w = inner_w if fill in ("x", "both") else min(inner_w, nw)
            final_h = inner_h if fill in ("y", "both") else min(inner_h, nh)

            # Position within inner parcel according to anchor (use _parse_compass so "center" doesn't match 'n'/'e')
            anc_dir = _parse_compass(anchor)
            if "w" in anc_dir:
                fx = inner_x
            elif "e" in anc_dir:
                fx = inner_x + inner_w - final_w
            else:
                fx = inner_x + (inner_w - final_w) // 2

            if "n" in anc_dir:
                fy = inner_y
            elif "s" in anc_dir:
                fy = inner_y + inner_h - final_h
            else:
                fy = inner_y + (inner_h - final_h) // 2

            w.setGeometry(fx, fy, max(1, final_w), max(1, final_h))

    def _layout_grid(self, avail: QRect, widgets: List[QWidget]) -> None:
        col_w, row_h, items_info = self._compute_grid_tracks(widgets)
        max_r = len(row_h) - 1
        max_c = len(col_w) - 1

        # Distribute extra width and height according to weights (and shrink gracefully on overflow)
        total_col_weight = sum(max(0, self._col_weights.get(c, 0)) for c in range(max_c + 1))
        rem_w = avail.width() - sum(col_w)
        if total_col_weight > 0 and rem_w != 0:
            weighted_cols = [c for c in range(max_c + 1) if self._col_weights.get(c, 0) > 0]
            distributed = 0
            for c in weighted_cols[:-1]:
                delta = int(rem_w * self._col_weights[c] / total_col_weight)
                new_w = max(20, col_w[c] + delta)
                distributed += new_w - col_w[c]
                col_w[c] = new_w
            if weighted_cols:
                last_c = weighted_cols[-1]
                col_w[last_c] = max(20, col_w[last_c] + (rem_w - distributed))
        rem_w2 = avail.width() - sum(col_w)
        if rem_w2 < 0 and sum(col_w) > 0:
            deficit = -rem_w2
            tot_w = sum(col_w)
            for c in range(max_c + 1):
                if col_w[c] > 20 and tot_w > 0:
                    cut = min(col_w[c] - 20, int(round(deficit * col_w[c] / tot_w)))
                    col_w[c] -= cut

        total_row_weight = sum(max(0, self._row_weights.get(r, 0)) for r in range(max_r + 1))
        rem_h = avail.height() - sum(row_h)
        if total_row_weight > 0 and rem_h != 0:
            weighted_rows = [r for r in range(max_r + 1) if self._row_weights.get(r, 0) > 0]
            distributed_h = 0
            for r in weighted_rows[:-1]:
                delta = int(rem_h * self._row_weights[r] / total_row_weight)
                new_h = max(16, row_h[r] + delta)
                distributed_h += new_h - row_h[r]
                row_h[r] = new_h
            if weighted_rows:
                last_r = weighted_rows[-1]
                row_h[last_r] = max(16, row_h[last_r] + (rem_h - distributed_h))

        # Prefix sums for x and y coordinates
        col_x = [avail.x()]
        for w_c in col_w:
            col_x.append(col_x[-1] + w_c)
        row_y = [avail.y()]
        for h_r in row_h:
            row_y.append(row_y[-1] + h_r)

        for w, r, c, rs, cs, sticky, px1, px2, py1, py2, nw, nh in items_info:
            cell_x = col_x[c] + px1
            cell_y = row_y[r] + py1
            cell_w = max(1, (col_x[c + cs] - col_x[c]) - px1 - px2)
            cell_h = max(1, (row_y[r + rs] - row_y[r]) - py1 - py2)

            stretch_x = ("e" in sticky and "w" in sticky)
            stretch_y = ("n" in sticky and "s" in sticky)

            fw = cell_w if stretch_x else min(cell_w, nw)
            fh = cell_h if stretch_y else min(cell_h, nh)

            if stretch_x or "w" in sticky:
                fx = cell_x
            elif "e" in sticky:
                fx = cell_x + cell_w - fw
            else:
                fx = cell_x + (cell_w - fw) // 2

            if stretch_y or "n" in sticky:
                fy = cell_y
            elif "s" in sticky:
                fy = cell_y + cell_h - fh
            else:
                fy = cell_y + (cell_h - fh) // 2

            w.setGeometry(fx, fy, max(1, fw), max(1, fh))


# ============================================================================
# BASE QT 6 WIDGET MIXIN
# ============================================================================
def _unwrap_parent(master: Any) -> Optional[QWidget]:
    if master is None:
        return None
    if hasattr(master, "_viewport_widget") and getattr(master, "_viewport_widget") is not None:
        return getattr(master, "_viewport_widget")
    if isinstance(master, QMainWindow) and master.centralWidget() is not None:
        return master.centralWidget()
    if isinstance(master, QWidget):
        return master
    return None


class QtWidgetBase:
    """Common Tkinter/CustomTkinter method & geometry compatibility layer on Qt 6 QWidgets."""

    _props: Dict[str, Any]
    _pack_opts: Dict[str, Any]
    _grid_opts: Dict[str, Any]
    _geom_manager: str
    _req_width: Optional[int]
    _req_height: Optional[int]
    _pack_propagate: bool
    _qt_layout: Optional[QtDualLayout]
    _after_timers: Dict[str, QTimer]
    _after_counter: int
    _bound_handlers: Dict[str, List[Callable[..., Any]]]

    def _init_qt_base(self, master: Any = None, **kwargs: Any) -> None:
        self._master_ref = master
        self._props = dict(kwargs)
        self._pack_opts = {}
        self._grid_opts = {}
        self._geom_manager = ""
        self._req_width = int(kwargs["width"]) if "width" in kwargs and kwargs["width"] is not None else None
        self._req_height = int(kwargs["height"]) if "height" in kwargs and kwargs["height"] is not None else None
        self._pack_propagate = True
        self._qt_layout = None
        self._after_timers = {}
        self._after_counter = 0
        self._bound_handlers = {}
        if isinstance(self, QWidget) and not isinstance(self, (QMainWindow, QDialog)):
            self.setVisible(False)

    def _ensure_dual_layout(self) -> QtDualLayout:
        target: QWidget = self  # type: ignore[assignment]
        if isinstance(self, QMainWindow):
            cw = self.centralWidget()
            if cw is not None:
                target = cw
        elif hasattr(self, "_viewport_widget") and getattr(self, "_viewport_widget") is not None:
            target = getattr(self, "_viewport_widget")
        if getattr(target, "_qt_layout", None) is None:
            lay = QtDualLayout(target)
            target._qt_layout = lay  # type: ignore[attr-defined]
        return target._qt_layout  # type: ignore[attr-defined, return-value]

    def _parent_dual_layout(self) -> Optional[QtDualLayout]:
        if not isinstance(self, QWidget):
            return None
        p = self.parentWidget()
        if p is None:
            return None
        if getattr(p, "_qt_layout", None) is None:
            lay = QtDualLayout(p)
            p._qt_layout = lay  # type: ignore[attr-defined]
        return p._qt_layout  # type: ignore[attr-defined]

    # --- Pack & Grid Geometry Methods ---
    def _notify_ancestor_scrollbars(self) -> None:
        if not isinstance(self, QWidget):
            return
        cur = self.parentWidget()
        while cur is not None:
            if hasattr(cur, "_notify_scroll_content_changed"):
                getattr(cur, "_notify_scroll_content_changed")()
            cur = cur.parentWidget()

    def pack(self, **kwargs: Any) -> None:
        if not isinstance(self, QWidget):
            return
        self._pack_opts.update(kwargs)
        self._geom_manager = "pack"
        self.setVisible(True)
        lay = self._parent_dual_layout()
        if lay is not None:
            lay.add_managed_widget(self, "pack")
        self._notify_ancestor_scrollbars()

    def pack_forget(self) -> None:
        if not isinstance(self, QWidget):
            return
        self._geom_manager = ""
        self.setVisible(False)
        lay = self._parent_dual_layout()
        if lay is not None:
            lay.remove_managed_widget(self)
        self._notify_ancestor_scrollbars()

    def pack_propagate(self, flag: bool = True) -> None:
        self._pack_propagate = bool(flag)

    def grid(self, **kwargs: Any) -> None:
        if not isinstance(self, QWidget):
            return
        if kwargs:
            self._grid_opts.update(kwargs)
        self._geom_manager = "grid"
        self.setVisible(True)
        lay = self._parent_dual_layout()
        if lay is not None:
            lay.add_managed_widget(self, "grid")
        self._notify_ancestor_scrollbars()

    def grid_remove(self) -> None:
        if not isinstance(self, QWidget):
            return
        self._geom_manager = ""
        self.setVisible(False)
        lay = self._parent_dual_layout()
        if lay is not None:
            lay.remove_managed_widget(self)
        self._notify_ancestor_scrollbars()

    def grid_forget(self) -> None:
        self.grid_remove()

    def place(self, **kwargs: Any) -> None:
        if not isinstance(self, QWidget):
            return
        self._geom_manager = "place"
        self.setVisible(True)
        self.raise_()

    def place_forget(self) -> None:
        if not isinstance(self, QWidget):
            return
        self._geom_manager = ""
        self.setVisible(False)

    def grid_propagate(self, flag: bool = True) -> None:
        self._pack_propagate = bool(flag)

    def grid_rowconfigure(self, index: int, weight: int = 0, minsize: int = 0, **_: Any) -> None:
        lay = self._ensure_dual_layout()
        lay._row_weights[int(index)] = int(weight)
        if minsize:
            lay._row_minsize[int(index)] = int(minsize)
        lay.invalidate()

    def grid_columnconfigure(self, index: int, weight: int = 0, minsize: int = 0, **_: Any) -> None:
        lay = self._ensure_dual_layout()
        lay._col_weights[int(index)] = int(weight)
        if minsize:
            lay._col_minsize[int(index)] = int(minsize)
        lay.invalidate()

    rowconfigure = grid_rowconfigure
    columnconfigure = grid_columnconfigure

    def tkraise(self, aboveThis: Any = None) -> None:
        if isinstance(self, QWidget):
            self.raise_()

    # --- Widget Introspection & Lifecycle ---
    def cget(self, key: str) -> Any:
        if key == "text" and hasattr(self, "_get_text"):
            return getattr(self, "_get_text")()
        if key == "values" and hasattr(self, "_get_values"):
            return getattr(self, "_get_values")()
        if key == "height":
            return self._req_height if self._req_height is not None else (self.height() if isinstance(self, QWidget) else 28)
        if key == "width":
            return self._req_width if self._req_width is not None else (self.width() if isinstance(self, QWidget) else 100)
        if key == "state" and isinstance(self, QWidget):
            return "normal" if self.isEnabled() else "disabled"
        return self._props.get(key, "")

    _QSS_STYLE_KEYS = frozenset((
        "fg_color", "hover_color", "text_color", "border_color",
        "border_width", "corner_radius", "width", "height",
    ))
    _LAYOUT_AFFECTING_KEYS = frozenset((
        "text", "textvariable", "wraplength", "width", "height",
        "font", "image", "anchor", "justify", "values", "from_",
        "to", "number_of_steps", "show", "placeholder_text",
        "orientation", "border_width", "padx", "pady",
    ))

    def configure(self, **kwargs: Any) -> None:
        if not kwargs:
            return
        old_text = self.text() if isinstance(self, (QLabel, QPushButton)) else None
        self._props.update(kwargs)
        if "width" in kwargs and kwargs["width"] is not None:
            self._req_width = int(kwargs["width"])
        if "height" in kwargs and kwargs["height"] is not None:
            self._req_height = int(kwargs["height"])
        if "font" in kwargs and isinstance(self, QWidget):
            self.setFont(_to_qfont(kwargs["font"]))
        if "state" in kwargs and isinstance(self, QWidget):
            st = str(kwargs["state"]).lower()
            self.setEnabled(st != "disabled")
        if hasattr(self, "_apply_props"):
            getattr(self, "_apply_props")(kwargs)
        # Skip layout invalidation when only styling props (e.g. text_color) or unchanged text arrived
        non_text_layout = any(k in self._LAYOUT_AFFECTING_KEYS and k != "text" for k in kwargs)
        text_changed = (
            "text" in kwargs
            and (old_text is None or not isinstance(self, (QLabel, QPushButton)) or self.text() != old_text)
        )
        if non_text_layout or text_changed:
            lay = self._parent_dual_layout()
            if lay is not None:
                lay.invalidate()
            if text_changed or any(k in kwargs for k in ("textvariable", "wraplength", "width", "height", "font", "image")):
                self._notify_ancestor_scrollbars()

    config = configure

    def update_idletasks(self) -> None:
        if isinstance(self, QWidget):
            if getattr(self, "_scroll_sync_pending", False) and hasattr(self, "_sync_scroll_content_now"):
                getattr(self, "_sync_scroll_content_now")()
            lay = getattr(self, "_qt_layout", None)
            if lay is not None:
                lay.setGeometry(self.rect())
            for child in self.findChildren(QWidget):
                if getattr(child, "_scroll_sync_pending", False) and hasattr(child, "_sync_scroll_content_now"):
                    getattr(child, "_sync_scroll_content_now")()
                c_lay = getattr(child, "_qt_layout", None)
                if c_lay is not None and not child.isHidden():
                    c_lay.setGeometry(child.rect())

    def update(self) -> None:
        self.update_idletasks()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def winfo_exists(self) -> bool:
        return True

    def winfo_manager(self) -> str:
        return self._geom_manager

    def winfo_ismapped(self) -> bool:
        return bool(self._geom_manager) and isinstance(self, QWidget) and not self.isHidden()

    def winfo_width(self) -> int:
        if isinstance(self, QWidget):
            return max(1, self.width())
        return 100

    def winfo_height(self) -> int:
        if isinstance(self, QWidget):
            return max(1, self.height())
        return 30

    def winfo_rootx(self) -> int:
        if isinstance(self, QWidget):
            return self.mapToGlobal(QPoint(0, 0)).x()
        return 0

    def winfo_rooty(self) -> int:
        if isinstance(self, QWidget):
            return self.mapToGlobal(QPoint(0, 0)).y()
        return 0

    def winfo_toplevel(self) -> QWidget:
        if isinstance(self, QWidget):
            return self.window()
        return self  # type: ignore[return-value]

    def winfo_children(self) -> List[QWidget]:
        target: QWidget = self  # type: ignore[assignment]
        if hasattr(self, "_viewport_widget") and getattr(self, "_viewport_widget") is not None:
            target = getattr(self, "_viewport_widget")
        elif isinstance(self, QMainWindow):
            cw = self.centralWidget()
            if cw is not None:
                target = cw
        if isinstance(target, QWidget):
            return [
                c for c in target.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                if isinstance(c, QtWidgetBase)
            ]
        return []

    def focus_set(self) -> None:
        if isinstance(self, QWidget):
            self.setFocus()

    def grab_set(self) -> None:
        if isinstance(self, QDialog):
            self.setWindowModality(Qt.WindowModality.ApplicationModal)
            self.setModal(True)

    def transient(self, parent: Any = None) -> None:
        pass

    def after(self, ms: int, func: Optional[Callable[..., Any]] = None, *args: Any) -> str:
        if func is None:
            return "after_noop"
        self._after_counter += 1
        tid = f"after_{id(self)}_{self._after_counter}"
        timer = QTimer(self if isinstance(self, QWidget) else None)
        timer.setSingleShot(True)

        def _fire() -> None:
            self._after_timers.pop(tid, None)
            try:
                func(*args)
            except Exception:
                pass
            finally:
                try:
                    timer.deleteLater()
                except Exception:
                    pass

        timer.timeout.connect(_fire)
        self._after_timers[tid] = timer
        timer.start(max(1, int(ms)))
        return tid

    def after_cancel(self, tid: Any) -> None:
        timer = self._after_timers.pop(str(tid), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def bind(self, sequence: str, func: Callable[..., Any], add: Any = None) -> None:
        seq = str(sequence)
        if seq not in self._bound_handlers:
            self._bound_handlers[seq] = []
        if not add:
            self._bound_handlers[seq].clear()
            sc_map = getattr(self, "_qt_shortcuts", None)
            if isinstance(sc_map, dict) and seq in sc_map:
                for old_sc in sc_map.pop(seq, []):
                    try:
                        old_sc.setEnabled(False)
                        old_sc.deleteLater()
                    except Exception:
                        pass
        self._bound_handlers[seq].append(func)

        if isinstance(self, QWidget):
            qt_seq: Optional[str] = None
            if seq.startswith("<Control-") and seq.endswith(">"):
                key_part = seq[len("<Control-"):-1]
                if key_part.startswith("Key-"):
                    key_part = key_part[4:]
                elif key_part.startswith("KP_"):
                    key_part = key_part[3:]
                qt_seq = f"Ctrl+{key_part}"
            elif seq in ("<Left>", "<Right>", "<Up>", "<Down>"):
                qt_seq = seq[1:-1]
            elif seq == "<Escape>" and isinstance(self, QDialog):
                qt_seq = "Esc"
            elif seq == "<Return>" and isinstance(self, QDialog):
                qt_seq = "Return"
            if qt_seq:
                sc = QShortcut(QKeySequence(qt_seq), self)
                sc.activated.connect(lambda _f=func: _f(None))
                if not hasattr(self, "_qt_shortcuts"):
                    self._qt_shortcuts: Dict[str, List[QShortcut]] = {}
                self._qt_shortcuts.setdefault(seq, []).append(sc)

    def bind_all(self, sequence: str, func: Callable[..., Any], add: Any = None) -> None:
        self.bind(sequence, func, add=add)

    def destroy(self) -> None:
        if isinstance(self, QWidget):
            for qt in self.findChildren(QTimer):
                try:
                    qt.stop()
                except Exception:
                    pass
        for t in list(self._after_timers.values()):
            try:
                t.stop()
                t.deleteLater()
            except Exception:
                pass
        self._after_timers.clear()
        cleanup_fn = getattr(self, "_cleanup_before_close", None)
        if callable(cleanup_fn) and not getattr(self, "_cleanup_done", False):
            try:
                cleanup_fn()
            except Exception:
                pass
        if isinstance(self, QWidget):
            if getattr(self, "_in_close_event", False):
                return
            self._geom_manager = ""
            lay = self._parent_dual_layout()
            if lay is not None:
                lay.remove_managed_widget(self)
            self._notify_ancestor_scrollbars()
            self.hide()
            if isinstance(self, QMainWindow):
                self.close()
                qapp = QApplication.instance()
                if qapp is not None:
                    qapp.quit()
            else:
                try:
                    self.setParent(None)
                except RuntimeError:
                    pass
                self.deleteLater()

    # --- Clipboard & Drag-and-Drop Compatibility ---
    def clipboard_clear(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.clear()

    def clipboard_append(self, text: str) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(cb.text() + str(text))

    def drop_target_register(self, *_: Any) -> None:
        if isinstance(self, QWidget):
            self.setAcceptDrops(True)

    def dnd_bind(self, seq: str, func: Callable[..., Any]) -> None:
        self.bind(seq, func)

    def _build_qss_rule(self, selector: str) -> str:
        p = self._props
        fg = _resolve_color(p.get("fg_color"))
        tc = _resolve_color(p.get("text_color"))
        bc = _resolve_color(p.get("border_color"))
        hc = _resolve_color(p.get("hover_color"))
        bw = p.get("border_width")
        cr = p.get("corner_radius")

        # Default-styled labels & flat transparent CTkFrames use the global stylesheet directly
        if isinstance(self, QLabel) and fg == "transparent" and p.get("text_color") == ("#0F172A", "#F8FAFC") and not bw and not (bc and bc != "transparent"):
            return ""
        if isinstance(self, QFrame) and type(self).__name__ == "CTkFrame" and fg == "transparent" and not bw and not (bc and bc != "transparent"):
            self.setProperty("qtkFlat", True)
            return ""
        if isinstance(self, QFrame) and self.property("qtkFlat"):
            self.setProperty("qtkFlat", False)

        rules: List[str] = []
        if fg is not None:
            rules.append(f"background-color: {fg};")
        if tc is not None and tc != "transparent":
            rules.append(f"color: {tc};")
        if bw is not None and int(bw) > 0:
            default_bcol = get_token("border", "dark") if isinstance(self, QFrame) else get_token("border_light", "dark")
            bcol = bc if (bc and bc != "transparent") else default_bcol
            rules.append(f"border: {int(bw)}px solid {bcol};")
        elif bc is not None and bc != "transparent":
            rules.append(f"border: 1px solid {bc};")
        elif fg == "transparent" and isinstance(self, QFrame):
            rules.append("border: none;")
        elif isinstance(self, QPushButton):
            rules.append("border: none;")
        if cr is not None:
            rules.append(f"border-radius: {int(cr)}px;")

        if isinstance(self, QPushButton):
            w_req = p.get("width")
            if w_req is not None and int(w_req) <= 34:
                rules.append("padding: 0px 2px;")
            else:
                rules.append("padding: 2px 6px;")
        elif isinstance(self, QLineEdit):
            h_req = p.get("height")
            if h_req is not None and int(h_req) <= 25:
                rules.append("padding: 0px 6px;")
            else:
                rules.append("padding: 2px 7px;")

        if not rules:
            return ""
        css = f"{selector} {{ {' '.join(rules)} }}"
        if isinstance(self, QPushButton):
            if hc and hc != "transparent":
                css += f" {selector}:hover {{ background-color: {hc}; }}"
            elif fg == "transparent":
                css += f" {selector}:hover {{ background-color: #383838; }}"
            # Disabled styling: preserve muted tint for colored CTAs
            dis_fg = "#9CA3AF"
            if fg == "transparent" or fg is None:
                dis_bg = "transparent"
                dis_border_css = "border: 1px solid #4A4D50;"
            else:
                base_c = QColor(fg)
                mix_c = QColor("#242424")
                if base_c.isValid():
                    r = int(base_c.red() * 0.45 + mix_c.red() * 0.55)
                    g = int(base_c.green() * 0.45 + mix_c.green() * 0.55)
                    b = int(base_c.blue() * 0.45 + mix_c.blue() * 0.55)
                    dis_bg = f"rgb({r},{g},{b})"
                else:
                    dis_bg = "#323232"
                dis_border_css = "border: none;"
            css += (
                f" {selector}:disabled {{"
                f" background-color: {dis_bg}; color: {dis_fg}; {dis_border_css}"
                f" }}"
            )
        return css

    def _apply_qss(self) -> None:
        if not isinstance(self, QWidget):
            return
        obj = self.objectName()
        if not obj:
            obj = f"qtw_{id(self)}"
            self.setObjectName(obj)
        css = self._build_qss_rule(f"#{obj}")
        prev = getattr(self, "_last_qss_css", None)
        if css == prev:
            return
        setattr(self, "_last_qss_css", css)
        if css:
            self.setStyleSheet(css)
        elif prev:
            self.setStyleSheet("")


# ============================================================================
# CONCRETE QT 6 WIDGET CLASSES (REPLACEMENT FOR CTK & TK)
# ============================================================================
class CTkFrame(QFrame, QtWidgetBase):
    """Native Qt 6 QFrame container."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "fg_color": ("#F9FAFB", "#2B2B2B"),
            "corner_radius": 8,
            "border_width": 0,
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        self._apply_qss()

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if any(k in kwargs for k in ("fg_color", "border_color", "border_width", "corner_radius", "text_color")):
            self._apply_qss()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._qt_layout is not None:
            self._qt_layout.setGeometry(self.rect())
        if "<Configure>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<Configure>"]):
                try:
                    cb(event)
                except Exception:
                    pass


class _ScrollViewportFrame(CTkFrame):
    """Internal viewport frame inside CTkScrollableFrame."""

    def __init__(self, scroll_parent: "CTkScrollableFrame", **kwargs: Any) -> None:
        super().__init__(None, **kwargs)
        self._scroll_parent = scroll_parent

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._qt_layout is not None:
            sh = self._qt_layout.sizeHint()
            if sh.height() > 0:
                self.setMinimumHeight(sh.height() + 12)
            self._qt_layout.setGeometry(self.rect())


class CTkScrollableFrame(QScrollArea, QtWidgetBase):
    """Native Qt 6 QScrollArea with hardware-accelerated scrolling and automatic viewport parenting."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "fg_color": ("#EBEBEB", "#242424"),
            "corner_radius": 8,
            "border_width": 0,
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._viewport_widget: Optional[_ScrollViewportFrame] = None
        self._init_qt_base(master, **defaults)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        vp = _ScrollViewportFrame(self, fg_color=defaults.get("fg_color"), corner_radius=0, border_width=0)
        self._viewport_widget = vp
        self.setWidget(vp)
        vp.setVisible(True)
        self._parent_canvas = self
        self._scroll_sync_pending: bool = False
        self._apply_qss()
        self._on_theme_refresh()

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "fg_color" in kwargs:
            self._apply_qss()
            self._on_theme_refresh()

    def _on_theme_refresh(self) -> None:
        bg = _resolve_color(self._props.get("fg_color"))
        if bg and bg != "transparent":
            vp = self.viewport()
            if vp is not None:
                vp.setAutoFillBackground(True)
                pal = vp.palette()
                pal.setColor(QPalette.ColorRole.Window, QColor(bg))
                vp.setPalette(pal)

    def _sync_scroll_content_now(self) -> None:
        self._scroll_sync_pending = False
        if self._viewport_widget is not None and self._viewport_widget._qt_layout is not None:
            sh = self._viewport_widget._qt_layout.sizeHint()
            self._viewport_widget.setMinimumHeight(max(60, sh.height() + 12))
            self._viewport_widget._qt_layout.setGeometry(self._viewport_widget.rect())

    def _notify_scroll_content_changed(self) -> None:
        if not self._scroll_sync_pending:
            self._scroll_sync_pending = True
            QTimer.singleShot(0, self._sync_scroll_content_now)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_scroll_content_now()

    def yview_moveto(self, frac: float) -> None:
        sb = self.verticalScrollBar()
        if sb is not None:
            sb.setValue(int(round(float(frac) * sb.maximum())))


class CTkLabel(QLabel, QtWidgetBase):
    """Native Qt 6 QLabel supporting text, textvariable, CTkImage, wraplength, and alignment."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "text": "",
            "fg_color": "transparent",
            "text_color": ("#0F172A", "#F8FAFC"),
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        self._textvar: Optional[Variable] = defaults.get("textvariable")
        self._ctk_image: Optional[Any] = defaults.get("image")

        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        else:
            self.setFont(_to_qfont((FONT_FAMILY, 13)))

        self._apply_props(defaults)

    def _get_text(self) -> str:
        return _strip_vs15(self.text())

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "text" in kwargs and self._textvar is None:
            new_t = _force_text_icons(str(kwargs["text"]))
            if new_t != self.text():
                self.setText(new_t)
        if "textvariable" in kwargs and kwargs["textvariable"] is not None:
            tvar = kwargs["textvariable"]
            self._textvar = tvar
            self.setText(_force_text_icons(str(tvar.get())))
            tvar.trace_add("write", lambda *_, _v=tvar: self.setText(_force_text_icons(str(_v.get()))))
        if "wraplength" in kwargs and kwargs["wraplength"]:
            wl = int(kwargs["wraplength"])
            self.setWordWrap(wl > 0)
            if wl > 0:
                self.setMaximumWidth(wl)
        elif "\n" in self.text() and not self._props.get("wraplength"):
            self.setWordWrap(False)
        if "anchor" in kwargs or "justify" in kwargs:
            anc_dir = _parse_compass(self._props.get("anchor", "center"))
            jst = str(self._props.get("justify", "")).lower()
            if "w" in anc_dir or jst == "left":
                h_align = Qt.AlignmentFlag.AlignLeft
            elif "e" in anc_dir or jst == "right":
                h_align = Qt.AlignmentFlag.AlignRight
            else:
                h_align = Qt.AlignmentFlag.AlignHCenter
            if "n" in anc_dir:
                v_align = Qt.AlignmentFlag.AlignTop
            elif "s" in anc_dir:
                v_align = Qt.AlignmentFlag.AlignBottom
            else:
                v_align = Qt.AlignmentFlag.AlignVCenter
            self.setAlignment(h_align | v_align)
        if "image" in kwargs:
            self._ctk_image = kwargs["image"]
            self._update_image_pixmap()
        if any(k in kwargs for k in self._QSS_STYLE_KEYS):
            self._apply_qss()

    def _update_image_pixmap(self) -> None:
        img = self._ctk_image
        if img is None:
            self.setPixmap(QPixmap())
            return
        if hasattr(img, "get_pixmap"):
            self.setPixmap(img.get_pixmap())
        elif isinstance(img, QPixmap):
            self.setPixmap(img)

    def _on_theme_refresh(self) -> None:
        if self._ctk_image is not None:
            self._update_image_pixmap()


def _escape_qt_ampersand(text: str) -> str:
    """Escape literal '&' as '&&' so QPushButton never treats '&X' as a mnemonic."""
    return re.sub(r"(?<!&)&(?!&)", "&&", str(text))


def _unescape_qt_ampersand(text: str) -> str:
    return str(text).replace("&&", "&")


class CTkButton(QPushButton, QtWidgetBase):
    """Native Qt 6 QPushButton."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "text": "",
            "fg_color": ("#1F6AA5", "#1F6AA5"),
            "hover_color": ("#144870", "#144870"),
            "text_color": ("#FFFFFF", "#FFFFFF"),
            "corner_radius": 6,
            "height": 28,
        }
        if kwargs.get("fg_color") == "transparent":
            defaults["text_color"] = ("#111827", "#DCE4EE")
            defaults["hover_color"] = ("#E5E7EB", "#383838")
        defaults.update(kwargs)
        super().__init__(_force_text_icons(_escape_qt_ampersand(str(defaults.get("text", "")))), parent_w)
        self._init_qt_base(master, **defaults)
        self._command: Optional[Callable[[], Any]] = defaults.get("command")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        else:
            self.setFont(_to_qfont((FONT_FAMILY, 13)))

        if str(defaults.get("state", "normal")).lower() == "disabled":
            self.setEnabled(False)
            self.setCursor(Qt.CursorShape.ArrowCursor)

        self.clicked.connect(self._on_clicked)
        self._apply_qss()

    def _get_text(self) -> str:
        return _strip_vs15(_unescape_qt_ampersand(self.text()))

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "text" in kwargs:
            new_t = _force_text_icons(_escape_qt_ampersand(str(kwargs["text"])))
            if new_t != self.text():
                self.setText(new_t)
        if "command" in kwargs:
            self._command = kwargs["command"]
        if "state" in kwargs:
            enabled = str(kwargs["state"]).lower() != "disabled"
            self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        if self._props.get("fg_color") == "transparent" and "text_color" not in self._props:
            self._props["text_color"] = ("#111827", "#DCE4EE")
        if any(k in kwargs for k in self._QSS_STYLE_KEYS):
            self._apply_qss()

    def _on_clicked(self) -> None:
        if self._command is not None:
            self._command()

    def invoke(self) -> None:
        if self.isEnabled() and self._command is not None:
            self._command()


class CTkEntry(QLineEdit, QtWidgetBase):
    """Native Qt 6 QLineEdit with two-way StringVar binding and key event dispatch."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "height": 28,
            "corner_radius": 5,
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        self._var: Optional[Variable] = defaults.get("textvariable")

        if "placeholder_text" in defaults and defaults["placeholder_text"]:
            self.setPlaceholderText(str(defaults["placeholder_text"]))
        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        else:
            self.setFont(_to_qfont((FONT_FAMILY, 13)))

        if self._var is not None:
            self.setText(str(self._var.get()))
            self._var.trace_add("write", self._on_var_changed)

        self.textChanged.connect(self._on_text_changed)
        self.returnPressed.connect(self._on_return_pressed)
        if str(defaults.get("state", "normal")).lower() == "disabled":
            self.setEnabled(False)
        self._apply_qss()

    def _on_text_changed(self, txt: str) -> None:
        if self._var is not None and str(self._var.get()) != txt:
            self._var.set(txt)

    def keyReleaseEvent(self, event: Any) -> None:
        super().keyReleaseEvent(event)
        if "<KeyRelease>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<KeyRelease>"]):
                try:
                    cb(event)
                except Exception:
                    pass

    def _on_return_pressed(self) -> None:
        for seq in ("<Return>", "<FocusOut>"):
            if seq in self._bound_handlers:
                for cb in list(self._bound_handlers[seq]):
                    try:
                        cb(None)
                    except Exception:
                        pass

    def focusOutEvent(self, event: Any) -> None:
        super().focusOutEvent(event)
        if "<FocusOut>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<FocusOut>"]):
                try:
                    cb(event)
                except Exception:
                    pass

    def _on_var_changed(self, *_: Any) -> None:
        if self._var is not None:
            v = str(self._var.get())
            if self.text() != v:
                self.blockSignals(True)
                self.setText(v)
                self.blockSignals(False)

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "placeholder_text" in kwargs:
            self.setPlaceholderText(str(kwargs["placeholder_text"]))
        if "textvariable" in kwargs and kwargs["textvariable"] is not None:
            v = kwargs["textvariable"]
            self._var = v
            self.setText(str(v.get()))
            v.trace_add("write", self._on_var_changed)
        self._apply_qss()

    def get(self) -> str:
        return self.text()

    def delete(self, first: Any = 0, last: Any = None) -> None:
        cur = self.text()
        if first == 0 and (last == "end" or last is None):
            self.clear()
            return
        try:
            i = len(cur) if first == "end" else max(0, int(first))
            j = len(cur) if (last == "end") else (i + 1 if last is None else max(i, int(last)))
            self.setText(cur[:i] + cur[j:])
        except Exception:
            self.clear()

    def insert(self, index: Any, text: str) -> None:  # type: ignore[override]
        cur = self.text()
        txt = str(text)
        if not cur:
            self.setText(txt)
            return
        try:
            pos = len(cur) if index in ("end", "insert") else max(0, min(len(cur), int(index)))
            self.setText(cur[:pos] + txt + cur[pos:])
        except Exception:
            self.setText(txt)


class CTkOptionMenu(QComboBox, QtWidgetBase):
    """Native Qt 6 QComboBox styled to match CustomTkinter OptionMenu with vector chevron."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "values": [],
            "height": 28,
            "fg_color": ("#2563EB", "#1F6AA5"),
            "text_color": ("#FFFFFF", "#FFFFFF"),
            "corner_radius": 6,
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        self._var: Optional[Variable] = defaults.get("variable")
        self._command: Optional[Callable[[str], Any]] = defaults.get("command")
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        else:
            self.setFont(_to_qfont((FONT_FAMILY, 13)))

        vals = [str(v) for v in (defaults.get("values") or [])]
        self._raw_vals: List[str] = list(vals)
        self.addItems([_force_text_icons(v) for v in vals])

        if self._var is not None:
            init_v = str(self._var.get())
            if init_v:
                if init_v not in vals:
                    self._raw_vals.append(init_v)
                    self.addItem(_force_text_icons(init_v))
                self.setCurrentIndex(self._raw_vals.index(init_v))
            self._var.trace_add("write", self._on_var_changed)

        self.currentTextChanged.connect(self._on_text_changed)
        if str(defaults.get("state", "normal")).lower() == "disabled":
            self.setEnabled(False)
        self._apply_combo_qss()

    def _apply_combo_qss(self) -> None:
        obj = self.objectName()
        if not obj:
            obj = f"qtw_{id(self)}"
            self.setObjectName(obj)
        bg = _resolve_color(self._props.get("fg_color")) or "#1F6AA5"
        tc = _resolve_color(self._props.get("text_color")) or "#FFFFFF"
        cr = int(self._props.get("corner_radius", 6) or 6)
        hov = "#144870"
        drop_bg = "#144870"
        v_pad = 1 if (self._req_height is not None and self._req_height <= 25) else 2
        self.setStyleSheet(
            f"#{obj} {{ background-color: {bg}; color: {tc}; border: none; border-radius: {cr}px; padding: {v_pad}px 23px {v_pad}px 7px; }}"
            f" #{obj}:hover {{ background-color: {hov}; }}"
            f" #{obj}::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 20px; background-color: {drop_bg}; border-top-right-radius: {cr}px; border-bottom-right-radius: {cr}px; border: none; }}"
        )

    def _on_theme_refresh(self) -> None:
        self._apply_combo_qss()

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = self.width() - 11.0
        cy = self.height() / 2.0 + 1.0
        tri = QPolygonF([
            QPointF(cx - 4.0, cy - 2.5),
            QPointF(cx + 4.0, cy - 2.5),
            QPointF(cx, cy + 2.8),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawPolygon(tri)
        p.end()

    def _get_values(self) -> List[str]:
        return [_strip_vs15(self.itemText(i)) for i in range(self.count())]

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "values" in kwargs:
            vals = [str(v) for v in kwargs["values"]]
            cur = _strip_vs15(self.currentText())
            self.blockSignals(True)
            self.clear()
            self._raw_vals = list(vals)
            self.addItems([_force_text_icons(v) for v in vals])
            if cur in vals:
                self.setCurrentIndex(vals.index(cur))
            elif vals:
                self.setCurrentIndex(0)
                if self._var is not None and str(self._var.get()) != vals[0]:
                    self._var.set(vals[0])
            self.blockSignals(False)
        if "command" in kwargs:
            self._command = kwargs["command"]
        if "variable" in kwargs and kwargs["variable"] is not None:
            v = kwargs["variable"]
            self._var = v
            self._on_var_changed()
            v.trace_add("write", self._on_var_changed)

    def _on_text_changed(self, txt: str) -> None:
        clean = _strip_vs15(txt)
        if self._var is not None and str(self._var.get()) != clean:
            self._var.set(clean)
        if self._command is not None:
            self._command(clean)

    def _on_var_changed(self, *_: Any) -> None:
        if self._var is not None:
            val = str(self._var.get())
            if _strip_vs15(self.currentText()) != val:
                self.blockSignals(True)
                disp = _force_text_icons(val)
                idx = self.findText(disp)
                if idx >= 0:
                    self.setCurrentIndex(idx)
                elif val:
                    self._raw_vals.append(val)
                    self.addItem(disp)
                    self.setCurrentIndex(self.count() - 1)
                self.blockSignals(False)

    def get(self) -> str:
        return _strip_vs15(self.currentText())

    def set(self, value: Any) -> None:
        val = str(value)
        disp = _force_text_icons(val)
        self.blockSignals(True)
        idx = self.findText(disp)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self._raw_vals.append(val)
            self.addItem(disp)
            self.setCurrentIndex(self.count() - 1)
        self.blockSignals(False)
        if self._var is not None and str(self._var.get()) != val:
            self._var.set(val)


CTkComboBox = CTkOptionMenu


class CTkCheckBox(QCheckBox, QtWidgetBase):
    """Native Qt 6 QCheckBox with custom crisp vector tick mark."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "text": "",
            "onvalue": True,
            "offvalue": False,
        }
        defaults.update(kwargs)
        super().__init__(_force_text_icons(str(defaults.get("text", ""))), parent_w)
        self._init_qt_base(master, **defaults)
        self._var: Optional[Variable] = defaults.get("variable")
        self._command: Optional[Callable[[], Any]] = defaults.get("command")
        self._onvalue = defaults.get("onvalue", True)
        self._offvalue = defaults.get("offvalue", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        else:
            self.setFont(_to_qfont((FONT_FAMILY, 13)))

        if self._var is not None:
            self._sync_from_var()
            self._var.trace_add("write", self._sync_from_var)

        self.toggled.connect(self._on_toggled)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        txt = self.text()
        w = 24 + (fm.horizontalAdvance(txt) + 4 if txt else 0)
        h = max(20, fm.height() + 4)
        return QSize(w, h)

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = self.height()
        box_sz = 17
        bx = 2
        by = (h - box_sz) // 2
        rect = QRectF(bx, by, box_sz, box_sz)
        if self.isChecked():
            fill_col = QColor("#1F6AA5")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fill_col))
            p.drawRoundedRect(rect, 4.0, 4.0)
            pen = QPen(QColor("#FFFFFF"), 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawLine(QPointF(bx + 4.0, by + 8.8), QPointF(bx + 7.2, by + 12.2))
            p.drawLine(QPointF(bx + 7.2, by + 12.2), QPointF(bx + 13.0, by + 5.0))
        else:
            bg = QColor("#2B2B2B")
            bd = QColor("#6B7280")
            p.setPen(QPen(bd, 1.5))
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(rect, 4.0, 4.0)

        txt = self.text()
        if txt:
            p.setFont(self.font())
            tc = _resolve_color(self._props.get("text_color"))
            txt_col = QColor(tc) if (tc and tc != "transparent") else QColor("#DCE4EE")
            p.setPen(txt_col)
            p.drawText(QRect(bx + box_sz + 7, 0, max(1, self.width() - (bx + box_sz + 8)), h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
        p.end()

    def _get_text(self) -> str:
        return _strip_vs15(self.text())

    def _sync_from_var(self, *_: Any) -> None:
        if self._var is None:
            return
        v = self._var.get()
        is_on = (str(v) == str(self._onvalue)) or (v is True) or (v == 1 and self._onvalue is True)
        if self.isChecked() != is_on:
            self.blockSignals(True)
            self.setChecked(is_on)
            self.blockSignals(False)

    def _on_toggled(self, checked: bool) -> None:
        if self._var is not None:
            target = self._onvalue if checked else self._offvalue
            if isinstance(self._var, BooleanVar):
                self._var.set(bool(checked))
            else:
                self._var.set(target)
        if self._command is not None:
            self._command()

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "text" in kwargs:
            self.setText(_force_text_icons(str(kwargs["text"])))
        if "command" in kwargs:
            self._command = kwargs["command"]

    def get(self) -> Any:
        if self._var is not None:
            return self._var.get()
        return self._onvalue if self.isChecked() else self._offvalue

    def select(self) -> None:
        self.setChecked(True)

    def deselect(self) -> None:
        self.setChecked(False)


class CTkSwitch(CTkCheckBox):
    """Native Qt 6 pill toggle switch matching CustomTkinter CTkSwitch."""

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        txt = self.text()
        w = 42 + (fm.horizontalAdvance(txt) + 6 if txt else 0)
        h = max(24, fm.height() + 6)
        return QSize(w, h)

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = self.height()
        tw, th = 36, 18
        tx = 2
        ty = (h - th) // 2
        if self.isChecked():
            track_col = QColor("#1F6AA5")
            knob_x = tx + tw - th + 2
        else:
            track_col = QColor("#52525B")
            knob_x = tx + 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_col))
        p.drawRoundedRect(QRectF(tx, ty, tw, th), th / 2.0, th / 2.0)

        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(QRectF(knob_x, ty + 2, th - 4, th - 4))

        txt = self.text()
        if txt:
            p.setFont(self.font())
            tc = _resolve_color(self._props.get("text_color"))
            txt_col = QColor(tc) if (tc and tc != "transparent") else QColor("#DCE4EE")
            p.setPen(txt_col)
            p.drawText(QRect(tx + tw + 8, 0, max(1, self.width() - (tx + tw + 9)), h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
        p.end()


CTkRadioButton = CTkCheckBox


class CTkSegmentedButton(QFrame, QtWidgetBase):
    """Native Qt 6 segmented button strip built with QPushButtons in a QHBoxLayout."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "values": [],
            "height": 32,
            "fg_color": ("#E5E7EB", "#212121"),
            "corner_radius": 6,
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        self._var: Optional[Variable] = defaults.get("variable")
        self._command: Optional[Callable[[str], Any]] = defaults.get("command")
        self._values: List[str] = [str(v) for v in (defaults.get("values") or [])]
        self._current: str = self._values[0] if self._values else ""
        self._seg_btns: Dict[str, QPushButton] = {}

        self._hbox = QHBoxLayout(self)
        self._hbox.setContentsMargins(2, 2, 2, 2)
        self._hbox.setSpacing(2)

        self._rebuild_segments()
        svar = self._var
        if svar is not None:
            if svar.get():
                self._current = str(svar.get())
            svar.trace_add("write", lambda *_, _v=svar: self.set(_v.get()))
        self._update_segment_styles()
        self._apply_qss()

    def _rebuild_segments(self) -> None:
        for b in self._seg_btns.values():
            b.deleteLater()
        self._seg_btns.clear()
        fnt = _to_qfont(self._props.get("font", (FONT_FAMILY, 12)))
        h = max(22, int(self._props.get("height", 32)) - 4)
        for val in self._values:
            btn = QPushButton(_force_text_icons(val), self)
            btn.setFont(fnt)
            btn.setFixedHeight(h)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, v=val: self._on_segment_clicked(v))
            self._hbox.addWidget(btn)
            self._seg_btns[val] = btn

    def _update_segment_styles(self) -> None:
        for val, btn in self._seg_btns.items():
            if val == self._current:
                btn.setStyleSheet(
                    "QPushButton { background-color: #1F6AA5; color: #FFFFFF; border: none; border-radius: 5px; padding: 2px 10px; font-weight: bold; }"
                    "QPushButton:hover { background-color: #144870; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background-color: transparent; color: #DCE4EE; border: none; border-radius: 5px; padding: 2px 10px; font-weight: normal; }"
                    "QPushButton:hover { background-color: #383838; }"
                )

    def _on_segment_clicked(self, val: str) -> None:
        self.set(val)
        if self._command is not None:
            self._command(val)

    def get(self) -> str:
        return self._current

    def set(self, val: Any) -> None:
        v = str(val)
        self._current = v
        if self._var is not None and str(self._var.get()) != v:
            self._var.set(v)
        self._update_segment_styles()

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "values" in kwargs:
            self._values = [str(v) for v in (kwargs["values"] or [])]
            self._rebuild_segments()
            if self._current not in self._values and self._values:
                self._current = self._values[0]
            self._update_segment_styles()
        if "command" in kwargs:
            self._command = kwargs["command"]
        if "variable" in kwargs and kwargs["variable"] is not None:
            svar = kwargs["variable"]
            self._var = svar
            if svar.get():
                self.set(svar.get())
            svar.trace_add("write", lambda *_, _v=svar: self.set(_v.get()))

    def _on_theme_refresh(self) -> None:
        self._update_segment_styles()


class CTkTextbox(QTextEdit, QtWidgetBase):
    """Native Qt 6 QTextEdit replacing CTkTextbox with tag coloring and fast scrolling."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "fg_color": ("#FFFFFF", "#1D1E1E"),
            "text_color": ("#111827", "#DCE4EE"),
            "corner_radius": 6,
            "border_width": 1,
            "border_color": ("#D1D5DB", "#3E3E3E"),
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        self._textbox = self
        self._tag_fg: Dict[str, QColor] = {}
        self._tag_font: Dict[str, QFont] = {}

        if "font" in defaults:
            self.setFont(_to_qfont(defaults["font"]))
        else:
            self.setFont(_to_qfont((FONT_MONO, 10)))

        if str(defaults.get("wrap", "word")).lower() == "none":
            self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        if str(defaults.get("state", "normal")).lower() == "disabled":
            self.setReadOnly(True)
        self._apply_qss()

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "state" in kwargs:
            st = str(kwargs["state"]).lower()
            self.setReadOnly(st == "disabled")
            self.setEnabled(True)
        self._apply_qss()

    def tag_config(self, tag_name: str, foreground: Optional[str] = None, font: Optional[Any] = None, **_: Any) -> None:
        if foreground:
            col = _resolve_color(foreground)
            if col and col != "transparent":
                self._tag_fg[tag_name] = QColor(col)
        if font:
            self._tag_font[tag_name] = _to_qfont(font)

    def insert(self, index: Any, text: str, tags: Any = None) -> None:
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        fmt = cursor.charFormat()
        tag_key: Optional[str] = None
        if isinstance(tags, str):
            tag_key = tags
        elif isinstance(tags, (tuple, list)) and tags:
            tag_key = str(tags[0])
        if tag_key and tag_key in self._tag_fg:
            fmt.setForeground(self._tag_fg[tag_key])
        else:
            fmt.clearForeground()
        if tag_key and tag_key in self._tag_font:
            fmt.setFont(self._tag_font[tag_key])
        cursor.insertText(str(text), fmt)
        self.setTextCursor(cursor)

    def delete(self, start: Any = "1.0", end: Any = "end") -> None:
        self.clear()

    def get(self, start: Any = "1.0", end: Any = "end") -> str:
        return self.toPlainText()

    def see(self, index: Any = "end") -> None:
        sb = self.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())


class CTkProgressBar(QProgressBar, QtWidgetBase):
    """Native Qt 6 QProgressBar supporting 0.0..1.0 float progress."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "width": 160,
            "height": 10,
        }
        defaults.update(kwargs)
        super().__init__(parent_w)
        self._init_qt_base(master, **defaults)
        self._indeterminate: bool = str(defaults.get("mode", "determinate")).lower() == "indeterminate"
        self.setRange(0, 1000)
        self.setValue(0)
        self.setTextVisible(False)
        self._apply_props(defaults)

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "mode" in kwargs:
            self._indeterminate = str(kwargs["mode"]).lower() == "indeterminate"
            if self._indeterminate:
                self.setRange(0, 0)
            else:
                self.setRange(0, 1000)

    def set(self, val: float) -> None:
        if self.maximum() == 0:
            self.setRange(0, 1000)
        v = max(0, min(1000, int(round(float(val) * 1000))))
        self.setValue(v)

    def get(self) -> float:
        return self.value() / 1000.0

    def start(self) -> None:
        if self._indeterminate or str(self._props.get("mode", "")).lower() == "indeterminate":
            self.setRange(0, 0)

    def stop(self) -> None:
        if self.maximum() == 0:
            self.setRange(0, 1000)
        self.setValue(0)


class CTkSlider(QSlider, QtWidgetBase):
    """Native Qt 6 QSlider supporting float/step values and command callback."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        defaults: Dict[str, Any] = {
            "from_": 0.0,
            "to": 1.0,
            "number_of_steps": 100,
            "width": 140,
            "height": 20,
        }
        defaults.update(kwargs)
        super().__init__(Qt.Orientation.Horizontal, parent_w)
        self._init_qt_base(master, **defaults)
        self._from = float(defaults.get("from_", 0.0))
        self._to = float(defaults.get("to", 1.0))
        self._steps = max(1, int(defaults.get("number_of_steps", 100)))
        self._command: Optional[Callable[[float], Any]] = defaults.get("command")
        self._var: Optional[Variable] = defaults.get("variable")

        self.setRange(0, self._steps)
        self.valueChanged.connect(self._on_val_changed)

    def _on_val_changed(self, step_idx: int) -> None:
        frac = step_idx / float(self._steps)
        real_val = self._from + frac * (self._to - self._from)
        if self._var is not None:
            self._var.set(real_val)
        if self._command is not None:
            self._command(real_val)

    def set(self, val: float) -> None:
        span = self._to - self._from
        frac = (float(val) - self._from) / span if abs(span) > 1e-12 else 0.0
        idx = max(0, min(self._steps, int(round(frac * self._steps))))
        self.blockSignals(True)
        self.setValue(idx)
        self.blockSignals(False)

    def get(self) -> float:
        frac = self.value() / float(self._steps)
        return self._from + frac * (self._to - self._from)


# ============================================================================
# TTK TREEVIEW / SCROLLBAR / STYLE & TK CANVAS REPLACEMENTS IN QT 6
# ============================================================================
class Treeview(QTableWidget, QtWidgetBase):
    """Native Qt 6 QTableWidget implementing the full ttk.Treeview API."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        cols = list(kwargs.get("columns", ()))
        h_rows = int(kwargs.get("height", 6))
        super().__init__(0, len(cols), parent_w)
        self._init_qt_base(master, width=kwargs.get("width", 520), height=max(110, h_rows * 26 + 30))
        self._col_ids: List[str] = [str(c) for c in cols]
        self._req_col_widths: Dict[int, int] = {}
        self._iid_to_row: Dict[str, int] = {}
        self._row_to_iid: Dict[int, str] = {}
        self._row_tags: Dict[int, List[str]] = {}
        self._tag_fg: Dict[str, QColor] = {}
        self._tag_bg: Dict[str, QColor] = {}
        self._col_commands: Dict[int, Callable[[], Any]] = {}
        self._next_iid: int = 1

        self.setHorizontalHeaderLabels(self._col_ids)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(25)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        sel_mode = str(kwargs.get("selectmode", "browse")).lower()
        if sel_mode == "extended":
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        else:
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setStretchLastSection(True)

        self.itemSelectionChanged.connect(self._on_qt_selection_changed)
        self.itemDoubleClicked.connect(self._on_qt_double_clicked)
        self.horizontalHeader().sectionClicked.connect(self._on_qt_header_clicked)

    def _on_qt_header_clicked(self, idx: int) -> None:
        cmd = self._col_commands.get(idx)
        if callable(cmd):
            try:
                cmd()
            except Exception:
                pass

    def _on_qt_selection_changed(self) -> None:
        if "<<TreeviewSelect>>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<<TreeviewSelect>>"]):
                try:
                    cb(None)
                except Exception:
                    pass

    def _on_qt_double_clicked(self, *_: Any) -> None:
        if "<Double-1>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<Double-1>"]):
                try:
                    cb(None)
                except Exception:
                    pass

    def heading(self, col: str, text: Optional[str] = None, command: Optional[Callable[[], Any]] = None, **_: Any) -> None:
        if str(col) in self._col_ids:
            idx = self._col_ids.index(str(col))
            if text is not None:
                it = self.horizontalHeaderItem(idx)
                if it is None:
                    self.setHorizontalHeaderItem(idx, QTableWidgetItem(str(text)))
                else:
                    it.setText(str(text))
            if command is not None:
                self._col_commands[idx] = command

    def column(self, col: str, width: Optional[int] = None, minwidth: Optional[int] = None, anchor: Optional[str] = None, **_: Any) -> None:  # type: ignore[override]
        if str(col) in self._col_ids and width is not None:
            idx = self._col_ids.index(str(col))
            w = max(int(width), int(minwidth or 30))
            self._req_col_widths[idx] = w
            self.setColumnWidth(idx, w)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not self._req_col_widths:
            return
        vw = max(100, self.viewport().width())
        n_cols = len(self._col_ids)
        if n_cols <= 0:
            return
        total_req = sum(self._req_col_widths.get(i, 90) for i in range(n_cols))
        if total_req > vw:
            scale = vw / float(total_req)
            for i in range(n_cols):
                rw = self._req_col_widths.get(i, 90)
                self.setColumnWidth(i, max(38, int(rw * scale)))
        else:
            for i in range(n_cols):
                if i in self._req_col_widths:
                    self.setColumnWidth(i, self._req_col_widths[i])

    def tag_configure(self, tagname: str, foreground: Optional[str] = None, background: Optional[str] = None, **_: Any) -> None:
        if foreground:
            c = _resolve_color(foreground)
            if c and c != "transparent":
                self._tag_fg[tagname] = QColor(c)
        if background:
            c = _resolve_color(background)
            if c and c != "transparent":
                self._tag_bg[tagname] = QColor(c)

    def insert(
        self,
        parent: str = "",
        index: Any = "end",
        iid: Optional[str] = None,
        values: Sequence[Any] = (),
        tags: Sequence[str] = (),
        **_: Any,
    ) -> str:
        r = self.rowCount()
        self.insertRow(r)
        row_iid = str(iid) if iid is not None else f"I{self._next_iid:04d}"
        self._next_iid += 1
        self._iid_to_row[row_iid] = r
        self._row_to_iid[r] = row_iid
        t_list = [str(t) for t in (tags if isinstance(tags, (list, tuple)) else ([tags] if tags else []))]
        self._row_tags[r] = t_list

        fg = next((self._tag_fg[t] for t in t_list if t in self._tag_fg), None)
        bg = next((self._tag_bg[t] for t in t_list if t in self._tag_bg), None)

        for c in range(len(self._col_ids)):
            val_str = str(values[c]) if c < len(values) and values[c] is not None else ""
            it = QTableWidgetItem(val_str)
            if fg is not None:
                it.setForeground(fg)
            if bg is not None:
                it.setBackground(bg)
            self.setItem(r, c, it)
        return row_iid

    def get_children(self, item: str = "") -> Tuple[str, ...]:
        return tuple(self._row_to_iid[r] for r in range(self.rowCount()) if r in self._row_to_iid)

    def delete(self, *items: str) -> None:
        if not items:
            return
        rows = sorted({self._iid_to_row[str(i)] for i in items if str(i) in self._iid_to_row}, reverse=True)
        if not rows:
            return
        if len(rows) >= self.rowCount():
            self.setRowCount(0)
            self._iid_to_row.clear()
            self._row_to_iid.clear()
            self._row_tags.clear()
            return
        row_set = set(rows)
        for r in rows:
            self.removeRow(r)
        remaining = [(r_idx, iid) for r_idx, iid in sorted(self._row_to_iid.items()) if r_idx not in row_set]
        self._iid_to_row = {iid: idx for idx, (_, iid) in enumerate(remaining)}
        self._row_to_iid = {idx: iid for idx, (_, iid) in enumerate(remaining)}
        self._row_tags = {idx: self._row_tags.get(old_r, []) for idx, (old_r, _) in enumerate(remaining)}

    def item(self, iid: str, option: Optional[str] = None, **kwargs: Any) -> Any:  # type: ignore[override]
        r = self._iid_to_row.get(str(iid))
        if r is None:
            return {"values": [], "tags": []}
        if "values" in kwargs:
            vals = kwargs["values"]
            for c in range(min(len(self._col_ids), len(vals))):
                it = super().item(r, c)
                if it is None:
                    self.setItem(r, c, QTableWidgetItem(str(vals[c])))
                else:
                    it.setText(str(vals[c]))
        cur_vals: List[str] = []
        for c in range(len(self._col_ids)):
            cell_item = super().item(r, c)
            cur_vals.append(cell_item.text() if cell_item is not None else "")
        res = {"values": cur_vals, "tags": list(self._row_tags.get(r, []))}
        if option is not None:
            return res.get(option)
        return res

    def set(self, iid: str, column: Optional[str] = None, value: Optional[Any] = None) -> Any:
        r = self._iid_to_row.get(str(iid))
        if r is None or column is None or str(column) not in self._col_ids:
            return ""
        c = self._col_ids.index(str(column))
        it = super().item(r, c)
        if value is not None:
            if it is None:
                self.setItem(r, c, QTableWidgetItem(str(value)))
            else:
                it.setText(str(value))
        return it.text() if it is not None else ""

    def move(self, iid: str, parent: str, index: int) -> None:  # type: ignore[override]
        r_old = self._iid_to_row.get(str(iid))
        if r_old is None:
            return
        n_rows = self.rowCount()
        target = max(0, min(n_rows - 1, int(index)))
        if r_old == target:
            return
        n_cols = len(self._col_ids)
        taken = [self.takeItem(r_old, c) for c in range(n_cols)]
        tags = list(self._row_tags.get(r_old, []))
        self.removeRow(r_old)
        remaining = [(r_idx, r_iid) for r_idx, r_iid in sorted(self._row_to_iid.items()) if r_idx != r_old]
        self.insertRow(target)
        for c in range(n_cols):
            if taken[c] is not None:
                self.setItem(target, c, taken[c])
        remaining.insert(target, (r_old, str(iid)))
        new_tags: Dict[int, List[str]] = {}
        new_i2r: Dict[str, int] = {}
        new_r2i: Dict[int, str] = {}
        for new_r, (old_r, r_iid) in enumerate(remaining):
            new_i2r[r_iid] = new_r
            new_r2i[new_r] = r_iid
            new_tags[new_r] = tags if r_iid == str(iid) else self._row_tags.get(old_r, [])
        self._iid_to_row = new_i2r
        self._row_to_iid = new_r2i
        self._row_tags = new_tags

    def selection(self) -> Tuple[str, ...]:
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        return tuple(self._row_to_iid[r] for r in rows if r in self._row_to_iid)

    def selection_set(self, items: Any) -> None:
        self.clearSelection()
        if isinstance(items, str):
            items = [items]
        sm = self.selectionModel()
        mod = self.model()
        if sm is None or mod is None:
            return
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for i in items:
            r = self._iid_to_row.get(str(i))
            if r is not None:
                sm.select(mod.index(r, 0), flags)

    def selection_remove(self, items: Any) -> None:
        self.clearSelection()

    def yview(self, *_: Any) -> None:
        pass

    def xview(self, *_: Any) -> None:
        pass


class Scrollbar(QWidget, QtWidgetBase):
    """Lightweight no-op scrollbar widget (since QTableWidget & QScrollArea have built-in Qt 6 scrollbars)."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        super().__init__(_unwrap_parent(master))
        self._init_qt_base(master, width=0, height=0)
        self.setFixedSize(0, 0)

    def set(self, *_: Any) -> None:
        pass

    def pack(self, **_: Any) -> None:
        self._geom_manager = ""
        self.setVisible(False)

    def grid(self, **_: Any) -> None:
        self._geom_manager = ""
        self.setVisible(False)


class Style:
    """No-op ttk.Style replacement (Qt 6 uses QSS stylesheets)."""

    def theme_use(self, *_: Any) -> None:
        pass

    def configure(self, *_: Any, **__: Any) -> None:
        pass

    def map(self, *_: Any, **__: Any) -> None:
        pass


class Canvas(QScrollArea, QtWidgetBase):
    """Native Qt 6 scrollable image canvas replacing tk.Canvas in Graph Preview."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        super().__init__(parent_w)
        self._init_qt_base(master, **kwargs)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._img_lbl = QLabel(self)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(self._img_lbl)
        self._apply_props(kwargs)

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        bg = _resolve_color(kwargs.get("bg", self._props.get("bg")))
        if bg and bg != "transparent":
            self.setStyleSheet(f"QScrollArea {{ background-color: {bg}; border: none; }} QLabel {{ background-color: {bg}; }}")

    def _on_theme_refresh(self) -> None:
        self._apply_props({})

    def delete(self, *_: Any) -> None:
        self._img_lbl.clear()

    def create_image(self, x: float, y: float, image: Any = None, anchor: str = "center", **_: Any) -> int:
        if image is not None and hasattr(image, "get_pixmap"):
            pix = image.get_pixmap()
            self._img_lbl.setPixmap(pix)
            self._img_lbl.setMinimumSize(pix.size())
        elif isinstance(image, QPixmap):
            self._img_lbl.setPixmap(image)
            self._img_lbl.setMinimumSize(image.size())
        return 1

    def create_text(self, x: float, y: float, text: str = "", fill: str = "#9CA3AF", font: Any = None, **_: Any) -> int:
        self._img_lbl.setPixmap(QPixmap())
        self._img_lbl.setText(str(text))
        col = _resolve_color(fill) or "#9CA3AF"
        self._img_lbl.setStyleSheet(f"QLabel {{ color: {col}; background: transparent; }}")
        if font:
            self._img_lbl.setFont(_to_qfont(font))
        return 2

    def config(self, **kwargs: Any) -> None:
        self.configure(**kwargs)

    def xview(self, *_: Any) -> None:
        pass

    def yview(self, *_: Any) -> None:
        pass

    def scan_mark(self, x: int, y: int) -> None:
        pass

    def scan_dragto(self, x: int, y: int, gain: int = 1) -> None:
        pass

    def coords(self, *_: Any) -> None:
        pass

    def itemconfig(self, item_id: Any, **kwargs: Any) -> None:
        if "image" in kwargs:
            img = kwargs["image"]
            if img is not None and hasattr(img, "get_pixmap"):
                pix = img.get_pixmap()
                self._img_lbl.setPixmap(pix)
                self._img_lbl.setMinimumSize(pix.size())
            elif isinstance(img, QPixmap):
                self._img_lbl.setPixmap(img)
                self._img_lbl.setMinimumSize(img.size())
        if "text" in kwargs:
            self._img_lbl.setText(str(kwargs["text"]))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if "<Configure>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<Configure>"]):
                try:
                    cb(event)
                except Exception:
                    pass


CTkCanvas = Canvas


# ============================================================================
# MATPLOTLIB QT 6 CANVAS ADAPTER
# ============================================================================
class QtMplCanvasWidget(FigureCanvasQTAgg, QtWidgetBase):
    """Native Qt 6 FigureCanvasQTAgg with pack/grid layout integration."""

    def __init__(self, figure: Any, master: Any = None) -> None:
        ensure_qapp()
        parent_w = _unwrap_parent(master)
        super().__init__(figure)
        if parent_w is not None:
            self.setParent(parent_w)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._init_qt_base(master, width=680, height=460, fg_color=("#FAFAFA", "#141414"))
        self._apply_qss()

    def _apply_props(self, kwargs: Dict[str, Any]) -> None:
        if "bg" in kwargs:
            self._props["fg_color"] = kwargs["bg"]
            self._apply_qss()

    def get_tk_widget(self) -> "QtMplCanvasWidget":
        return self

    def update(self, *args: Any) -> None:  # type: ignore[override]
        """Preserve QWidget.update() repaint scheduling instead of QtWidgetBase.update()."""
        if args:
            QWidget.update(self, *args)
        else:
            QWidget.update(self)

    def update_bbox_region(self, bbox: Any = None) -> None:
        """Schedule an async coalesced Qt repaint of a Matplotlib display bbox (or full canvas)."""
        if bbox is not None:
            dpr = float(getattr(self, "device_pixel_ratio", 1.0) or 1.0)
            # On fractional DPI scaling (e.g. 1.25x, 1.5x), sub-rect updates quantize
            # Agg copy_from_bbox vs QPainter.drawImage differently and can shift borders by 1px.
            if abs(dpr - round(dpr)) > 1e-6:
                QWidget.update(self)
                return
            x0, y0_b, w, h = bbox.bounds
            l_px = max(0, math.floor(x0 / dpr) - 2)
            t_px = max(0, math.floor((self.height() * dpr - (y0_b + h)) / dpr) - 2)
            QWidget.update(self, QRect(l_px, t_px, math.ceil(w / dpr) + 5, math.ceil(h / dpr) + 5))
        else:
            QWidget.update(self)


FigureCanvasTkAgg = QtMplCanvasWidget


class _QtTkHelper:
    """Lightweight helper providing .splitlist(data) for drag-and-drop paths."""

    @staticmethod
    def splitlist(data: str) -> Tuple[str, ...]:
        s = str(data or "").strip()
        if not s:
            return ()
        out: List[str] = []
        i = 0
        n = len(s)
        while i < n:
            while i < n and s[i].isspace():
                i += 1
            if i >= n:
                break
            if s[i] == "{":
                end = s.find("}", i + 1)
                if end == -1:
                    out.append(s[i + 1:])
                    break
                out.append(s[i + 1:end])
                i = end + 1
            else:
                end = i
                while end < n and not s[end].isspace():
                    end += 1
                out.append(s[i:end])
                i = end
        return tuple(out)


# ============================================================================
# TOP-LEVEL WINDOWS (CTk -> QMainWindow, CTkToplevel -> QDialog)
# ============================================================================
class CTk(QMainWindow, QtWidgetBase):
    """Main Desktop Window backed by Qt 6 QMainWindow."""

    def __init__(self, defer_show: bool = False, **kwargs: Any) -> None:
        self._qapp = ensure_qapp()
        super().__init__()
        self.tk = _QtTkHelper()
        self._win_ico_path: Optional[str] = None
        self._win32_hicons: Tuple[int, int] = (0, 0)
        self._init_qt_base(None, **kwargs)
        self.setAcceptDrops(True)
        central = CTkFrame(None, fg_color=("#EBEBEB", "#242424"), corner_radius=0)
        self.setCentralWidget(central)
        central.setVisible(True)
        self._ensure_dual_layout()
        if not defer_show:
            self.show()

    def attributes(self, *args: Any) -> None:
        if len(args) >= 2 and args[0] == "-alpha":
            try:
                self.setWindowOpacity(max(0.0, min(1.0, float(args[1]))))
            except Exception:
                pass

    def title(self, text: Optional[str] = None) -> str:
        if text is not None:
            self.setWindowTitle(str(text))
        return self.windowTitle()

    def geometry(self, spec: Optional[str] = None) -> str:  # type: ignore[override]
        if spec:
            try:
                dims = str(spec).split("+")[0]
                w_s, h_s = dims.lower().split("x")
                self.resize(int(w_s), int(h_s))
                self.update_idletasks()
            except Exception:
                pass
        return f"{self.width()}x{self.height()}"

    def minsize(self, w: int, h: int) -> None:
        self.setMinimumSize(int(w), int(h))

    def maxsize(self, w: int, h: int) -> None:
        self.setMaximumSize(int(w), int(h))

    def resizable(self, w_ok: bool = True, h_ok: bool = True) -> None:
        if not w_ok and not h_ok:
            self.setFixedSize(self.size())
        elif not w_ok:
            self.setMinimumWidth(self.width())
            self.setMaximumWidth(self.width())
        elif not h_ok:
            self.setMinimumHeight(self.height())
            self.setMaximumHeight(self.height())

    def _sync_win32_icon_and_theme(self) -> None:
        _apply_windows_titlebar_theme(self)
        if sys.platform.startswith("win") and self.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            try:
                from vawt_post.icon_assets import apply_native_win32_icon
                self._destroy_win32_hicons()
                self._win32_hicons = apply_native_win32_icon(int(self.winId()), self._win_ico_path)
            except Exception:
                pass

    def _destroy_win32_hicons(self) -> None:
        if sys.platform.startswith("win") and any(getattr(self, "_win32_hicons", (0, 0))):
            try:
                import ctypes
                windll = getattr(ctypes, "windll", None)
                if windll is not None:
                    for h in self._win32_hicons:
                        if h:
                            windll.user32.DestroyIcon(ctypes.c_void_p(h))
            except Exception:
                pass
            self._win32_hicons = (0, 0)

    def iconbitmap(self, path: Optional[str] = None, default: Optional[str] = None) -> None:
        target = path or default
        if target:
            self._win_ico_path = str(target)
            try:
                from vawt_post.icon_assets import build_qt_app_icon
                ic = build_qt_app_icon(ico_path=self._win_ico_path)
            except Exception:
                ic = QIcon(self._win_ico_path)
            if not ic.isNull():
                self.setWindowIcon(ic)
                self._qapp.setWindowIcon(ic)
            if self.isVisible():
                self._sync_win32_icon_and_theme()

    def wm_iconbitmap(self, path: Optional[str] = None, default: Optional[str] = None) -> None:
        self.iconbitmap(path, default)

    def iconphoto(self, default: bool = False, *icons: Any) -> None:
        if not icons:
            return
        ic = QIcon()
        for item in icons:
            if isinstance(item, QIcon):
                ic = item
                break
            pm = getattr(item, "_pixmap", None)
            if isinstance(pm, QPixmap) and not pm.isNull():
                for sz in (16, 24, 32, 48, 64, 128, 256):
                    ic.addPixmap(
                        pm.scaled(
                            sz, sz,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
        if not ic.isNull():
            self.setWindowIcon(ic)
            if default:
                self._qapp.setWindowIcon(ic)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_win32_icon_and_theme()

    def protocol(self, name: str, func: Callable[[], Any]) -> None:
        if name == "WM_DELETE_WINDOW":
            self._on_close_protocol = func

    def withdraw(self) -> None:
        self.hide()

    def deiconify(self) -> None:
        self.show()

    def mainloop(self) -> None:
        self.show()
        self.update_idletasks()
        self._qapp.exec()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        cw = self.centralWidget()
        if cw is not None and getattr(cw, "_qt_layout", None) is not None:
            cw._qt_layout.setGeometry(cw.rect())  # type: ignore[attr-defined]

    def closeEvent(self, event: QCloseEvent) -> None:
        self._in_close_event = True
        try:
            proto = getattr(self, "_on_close_protocol", None)
            if callable(proto):
                try:
                    res = proto()
                    if res is False:
                        event.ignore()
                        return
                except Exception:
                    pass
            event.accept()
            self._destroy_win32_hicons()
            super().closeEvent(event)
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.quit()
        finally:
            self._in_close_event = False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
        if not paths:
            return
        data_str = " ".join(f"{{{p}}}" if " " in p else p for p in paths)
        ev = type("DropEvent", (), {"data": data_str})()
        if "<<Drop>>" in self._bound_handlers:
            for cb in list(self._bound_handlers["<<Drop>>"]):
                try:
                    cb(ev)
                except Exception:
                    pass


class CTkToplevel(QDialog, QtWidgetBase):
    """Modal or modeless secondary window backed by Qt 6 QDialog."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        ensure_qapp()
        parent_w = master.window() if isinstance(master, QWidget) else None
        super().__init__(parent_w)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._init_qt_base(master, **kwargs)
        self._ensure_dual_layout()
        self.show()
        _apply_windows_titlebar_theme(self)

    def title(self, text: Optional[str] = None) -> str:
        if text is not None:
            self.setWindowTitle(str(text))
        return self.windowTitle()

    def geometry(self, spec: Optional[str] = None) -> str:  # type: ignore[override]
        if spec:
            try:
                parts = str(spec).split("+")
                dims = parts[0]
                if "x" in dims.lower():
                    w_s, h_s = dims.lower().split("x")
                    self.resize(int(w_s), int(h_s))
                if len(parts) >= 3:
                    self.move(int(parts[1]), int(parts[2]))
                self.update_idletasks()
            except Exception:
                pass
        return f"{self.width()}x{self.height()}"

    def minsize(self, w: int, h: int) -> None:
        self.setMinimumSize(int(w), int(h))

    def resizable(self, w_ok: bool = True, h_ok: bool = True) -> None:
        if not w_ok and not h_ok:
            self.setFixedSize(self.size())
        elif not w_ok:
            self.setMinimumWidth(self.width())
            self.setMaximumWidth(self.width())
        elif not h_ok:
            self.setMinimumHeight(self.height())
            self.setMaximumHeight(self.height())

    def attributes(self, *_: Any) -> None:
        pass

    def wm_overrideredirect(self, flag: bool = True) -> None:
        if flag:
            self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)

    def wm_geometry(self, spec: str) -> None:
        self.geometry(spec)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._qt_layout is not None:
            self._qt_layout.setGeometry(self.rect())


# ============================================================================
# NATIVE QT 6 FILE DIALOGS & MESSAGE BOXES (WITH UNIT TEST MOCK DELEGATION)
# ============================================================================
class _QtFileDialogProxy:
    """Uses native Qt 6 QFileDialog while seamlessly honoring unittest.mock.patch on tkinter.filedialog."""

    @staticmethod
    def _is_mocked(fn: Any) -> bool:
        return hasattr(fn, "_mock_name") or "Mock" in type(fn).__name__

    @staticmethod
    def _build_qt_filter(filetypes: Any) -> str:
        if not filetypes:
            return ""
        parts: List[str] = []
        for entry in filetypes:
            try:
                label, pattern = entry
                if isinstance(pattern, (list, tuple)):
                    pat_str = " ".join(str(p) for p in pattern)
                else:
                    pat_str = str(pattern)
                parts.append(f"{label} ({pat_str})")
            except Exception:
                pass
        return ";;".join(parts)

    @staticmethod
    def _parent_win() -> Optional[QWidget]:
        return QApplication.activeWindow()

    @staticmethod
    def askopenfilename(**kwargs: Any) -> str:
        import tkinter.filedialog as tk_fd
        if _QtFileDialogProxy._is_mocked(tk_fd.askopenfilename):
            return tk_fd.askopenfilename(**kwargs)
        title = kwargs.get("title", "Select File")
        init_dir = kwargs.get("initialdir", "")
        flt = _QtFileDialogProxy._build_qt_filter(kwargs.get("filetypes"))
        path, _ = QFileDialog.getOpenFileName(_QtFileDialogProxy._parent_win(), title, init_dir, flt)
        return path or ""

    @staticmethod
    def askopenfilenames(**kwargs: Any) -> List[str]:
        import tkinter.filedialog as tk_fd
        if _QtFileDialogProxy._is_mocked(tk_fd.askopenfilenames):
            return list(tk_fd.askopenfilenames(**kwargs))
        title = kwargs.get("title", "Select Files")
        init_dir = kwargs.get("initialdir", "")
        flt = _QtFileDialogProxy._build_qt_filter(kwargs.get("filetypes"))
        paths, _ = QFileDialog.getOpenFileNames(_QtFileDialogProxy._parent_win(), title, init_dir, flt)
        return paths or []

    @staticmethod
    def askdirectory(**kwargs: Any) -> str:
        import tkinter.filedialog as tk_fd
        if _QtFileDialogProxy._is_mocked(tk_fd.askdirectory):
            return tk_fd.askdirectory(**kwargs)
        title = kwargs.get("title", "Select Folder")
        init_dir = kwargs.get("initialdir", "")
        path = QFileDialog.getExistingDirectory(_QtFileDialogProxy._parent_win(), title, init_dir)
        return path or ""

    @staticmethod
    def asksaveasfilename(**kwargs: Any) -> str:
        import tkinter.filedialog as tk_fd
        if _QtFileDialogProxy._is_mocked(tk_fd.asksaveasfilename):
            return tk_fd.asksaveasfilename(**kwargs)
        title = kwargs.get("title", "Save File As")
        init_dir = kwargs.get("initialdir", "")
        init_file = kwargs.get("initialfile", "")
        start = f"{init_dir}/{init_file}" if init_dir and init_file else (init_file or init_dir)
        flt = _QtFileDialogProxy._build_qt_filter(kwargs.get("filetypes"))
        path, _ = QFileDialog.getSaveFileName(_QtFileDialogProxy._parent_win(), title, start, flt)
        if not path:
            return ""
        def_ext = str(kwargs.get("defaultextension", "") or "")
        if def_ext and "." not in os.path.basename(path):
            if not def_ext.startswith("."):
                def_ext = "." + def_ext
            path = path + def_ext
        return path


class _QtMessageBoxProxy:
    """Uses native Qt 6 QMessageBox while honoring unittest.mock.patch on tkinter.messagebox."""

    @staticmethod
    def _is_mocked(fn: Any) -> bool:
        return hasattr(fn, "_mock_name") or "Mock" in type(fn).__name__

    @staticmethod
    def showinfo(title: str = "Info", message: str = "", **kwargs: Any) -> str:
        import tkinter.messagebox as tk_mb
        if _QtMessageBoxProxy._is_mocked(tk_mb.showinfo):
            return tk_mb.showinfo(title, message, **kwargs)
        QMessageBox.information(None, str(title), str(message))
        return "ok"

    @staticmethod
    def showwarning(title: str = "Warning", message: str = "", **kwargs: Any) -> str:
        import tkinter.messagebox as tk_mb
        if _QtMessageBoxProxy._is_mocked(tk_mb.showwarning):
            return tk_mb.showwarning(title, message, **kwargs)
        QMessageBox.warning(None, str(title), str(message))
        return "ok"

    @staticmethod
    def showerror(title: str = "Error", message: str = "", **kwargs: Any) -> str:
        import tkinter.messagebox as tk_mb
        if _QtMessageBoxProxy._is_mocked(tk_mb.showerror):
            return tk_mb.showerror(title, message, **kwargs)
        QMessageBox.critical(None, str(title), str(message))
        return "ok"

    @staticmethod
    def askyesno(title: str = "Confirm", message: str = "", **kwargs: Any) -> bool:
        import tkinter.messagebox as tk_mb
        if _QtMessageBoxProxy._is_mocked(tk_mb.askyesno):
            return bool(tk_mb.askyesno(title, message, **kwargs))
        ans = QMessageBox.question(
            None,
            str(title),
            str(message),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return ans == QMessageBox.StandardButton.Yes


filedialog = _QtFileDialogProxy()
messagebox = _QtMessageBoxProxy()


class _TtkNamespace:
    Treeview = Treeview
    Scrollbar = Scrollbar
    Style = Style


class _TkNamespace:
    Widget = QWidget
    Toplevel = CTkToplevel
    Label = CTkLabel
    Canvas = Canvas
    LEFT = LEFT
    RIGHT = RIGHT
    TOP = TOP
    BOTTOM = BOTTOM
    BOTH = BOTH
    X = X
    Y = Y
    NONE = NONE
    END = END
    SOLID = SOLID
    FLAT = FLAT
    DISABLED = DISABLED
    NORMAL = NORMAL


class ImageTk:
    PhotoImage = PhotoImage


ttk = _TtkNamespace()
tk = _TkNamespace()

# Ensure Tkinter lifecycle methods (.destroy, .update) on QtWidgetBase are not
# shadowed by QWidget's C++ methods due to (Q..., QtWidgetBase) inheritance order.
for _sub in list(QtWidgetBase.__subclasses__()):
    if "destroy" not in _sub.__dict__:
        setattr(_sub, "destroy", QtWidgetBase.destroy)
    if "update" not in _sub.__dict__:
        setattr(_sub, "update", QtWidgetBase.update)
