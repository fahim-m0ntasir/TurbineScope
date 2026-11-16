# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Modern Desktop Application (CustomTkinter)
===================================================================
A polished, general-purpose graphical interface for post-processing ANSYS Fluent
VAWT simulations. Features dynamic blade configuration, dark studio interface,
parameter preset saving, worker threading, live logging, and responsive graph previews.
"""
from __future__ import annotations

import glob
import json
import math
import os
import queue
import re
import sys
import threading
import traceback
from typing import Dict, List, Optional, Any, Callable

from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import filedialog, messagebox, DND_FILES

_HAS_DND = True


class TkinterDnD:
    class DnDWrapper:
        pass


def configure_treeview_styles(mode: str = "dark"):
    """Qt 6 QSS automatically styles QTableWidget / Treeview in dark mode."""
    pass

from vawt_post.engine.config import (
    Params, InputFiles, ConfigManager, CaseMetadata, FLUENT_DEFAULTS, BUILTIN_PRESETS
)
from vawt_post.engine.parser import autodetect, compute_file_sha256, detect_folder_simulation_parameters
from vawt_post.engine.analysis import run_pipeline
from vawt_post.ui.components import ToolTip, SectionCard, PresetDialog, MultiStageDialog
from vawt_post.ui.diagnostics_tab import FileDiagnosticsFrame
from vawt_post.ui.live_monitor_tab import LiveMonitorFrame
from vawt_post.ui.results_tab import ResultsFrame
from vawt_post.ui.workbench_tab import WorkbenchStudioFrame

APP_TITLE = "TurbineScope"
APP_VERSION = "4.1.1"


class _TabviewCompat:
    """Compatibility adapter for any external references to app.tabview."""
    def __init__(self, app):
        self.app = app

    def set(self, name: str):
        low = name.lower()
        if "live" in low:
            self.app._switch_page("live")
        elif "gci" in low:
            self.app._switch_page("gci")
        elif "work" in low:
            self.app._switch_page("workbench")
        elif "fluent" in low or "run" in low:
            self.app._switch_page("live")
        elif "res" in low:
            self.app._switch_page("results")
        elif "log" in low:
            self.app._switch_page("results")
            self.app.results_frame.select_sub_tab("Log")
        elif "diag" in low:
            self.app._switch_page("input")
        elif "prev" in low:
            self.app._switch_page("results")
            self.app.results_frame.select_sub_tab("Preview")
        else:
            self.app._switch_page("input")


class VAWTPostApp(ctk.CTk, TkinterDnD.DnDWrapper):  # type: ignore
    """Main application window."""

    def __init__(
        self,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        show_immediately: bool = True,
    ):
        self._startup_progress = progress_cb
        self._is_closing = False
        self._cleanup_done = False
        self.config_mgr = ConfigManager()

        # Set explicit Windows Taskbar AppUserModelID so Windows uses our custom VAWT icon
        # instead of grouping the window under python.exe's default taskbar icon.
        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    f"TurbineScope.VAWT.CFDStudio.{APP_VERSION}"
                )
            except Exception:
                pass

        # Enforce Dark Mode exclusively
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        super().__init__(defer_show=True)
        configure_treeview_styles("dark")

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self._apply_window_icon()
        saved_geom = self.config_mgr.get_window_geometry()
        try:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
        except Exception:
            avail = None

        default_w, default_h = 1300, 880
        if avail is not None:
            default_w = min(default_w, max(1000, avail.width() - 24))
            default_h = min(default_h, max(620, avail.height() - 24))

        if saved_geom:
            try:
                if avail is not None and "x" in saved_geom:
                    size_part = saved_geom.split("+", 1)[0]
                    sw, sh = (int(v) for v in size_part.split("x", 1))
                    sw = min(sw, avail.width())
                    sh = min(sh, avail.height())
                    pos_part = saved_geom[len(size_part):]
                    self.geometry(f"{sw}x{sh}{pos_part}")
                else:
                    self.geometry(saved_geom)
            except Exception:
                self.geometry(f"{default_w}x{default_h}")
        else:
            self.geometry(f"{default_w}x{default_h}")
        min_w = min(1040, avail.width() - 24) if avail is not None else 1040
        min_h = min(620, avail.height() - 24) if avail is not None else 620
        self.minsize(min_w, min_h)

        # Threading communication queue
        self.msg_queue: queue.Queue = queue.Queue()
        self.is_running = False

        # State storage
        self.current_result: Optional[Dict[str, Any]] = None
        self.output_folder: Optional[str] = None

        # Build UI layout while window remains hidden so no intermediate frames flash
        self._init_variables()
        self._build_ui()
        self._report_startup(94, "Restoring parameters & saved workspace...")
        self._load_initial_parameters()
        self._bind_blade_count_change()

        # Keyboard shortcuts (Ctrl+1: Input, Ctrl+2: Results, Ctrl+3: Live Monitor, Ctrl+4: Workbench & GCI)
        self.bind_all("<Control-Key-1>", lambda _: self._switch_page("input"))
        self.bind_all("<Control-Key-2>", lambda _: self._switch_page("results"))
        self.bind_all("<Control-Key-3>", lambda _: self._switch_page("live"))
        self.bind_all("<Control-Key-4>", lambda _: self._switch_page("workbench"))
        self.bind_all("<Control-KP_1>", lambda _: self._switch_page("input"))
        self.bind_all("<Control-KP_2>", lambda _: self._switch_page("results"))
        self.bind_all("<Control-KP_3>", lambda _: self._switch_page("live"))
        self.bind_all("<Control-KP_4>", lambda _: self._switch_page("workbench"))

        # Switch to last active page
        saved_page = self.config_mgr.get_last_page() or "input"
        self._switch_page(saved_page, force=True)

        # Window closing handler
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Setup drag-and-drop if tkinterdnd2 is installed
        self._setup_dnd()

        # Pre-compute full layout while hidden so first visible frame is clean
        self.update_idletasks()
        if show_immediately:
            self.show()
            self.update()

        # Start periodic queue polling
        self.after(100, self._poll_queue)

    def _report_startup(self, pct: int, message: str) -> None:
        if self._startup_progress is not None:
            try:
                self._startup_progress(pct, message)
            except Exception:
                pass

    def _apply_window_icon(self):
        """Apply custom multi-resolution VAWT rotor icon to the titlebar and Windows taskbar."""
        try:
            from vawt_post.icon_assets import build_qt_app_icon, ensure_app_icons
            ico_path, png_path = ensure_app_icons()
            ic = build_qt_app_icon(ico_path=ico_path, png_path=png_path)
            if not ic.isNull():
                self.setWindowIcon(ic)
                self._qapp.setWindowIcon(ic)
            if sys.platform.startswith("win") and os.path.isfile(ico_path):
                self._win_ico_path = ico_path
                self.iconbitmap(default=ico_path)
                if self.isVisible():
                    self._sync_win32_icon_and_theme()
            if os.path.isfile(png_path):
                from PIL import Image
                from vawt_post.ui.qt_tk import ImageTk
                pil_icon = Image.open(png_path)
                self._tk_icon_photo = ImageTk.PhotoImage(pil_icon)
                self.iconphoto(True, self._tk_icon_photo)  # type: ignore[arg-type]
        except Exception:
            pass

    def _init_variables(self):
        """Initialize Tkinter / CustomTkinter tracking variables."""
        # Parameters (U, R, N, and TSR/omega start empty and required)
        self.v_U = ctk.StringVar(value="")
        self.v_tsr = ctk.StringVar(value="")
        self.v_omega = ctk.StringVar(value="")
        self.v_R = ctk.StringVar(value="")
        self.v_chord = ctk.StringVar(value="")
        self.v_nb = ctk.StringVar(value="")

        self.v_depth = ctk.StringVar(value=str(FLUENT_DEFAULTS["depth"]))
        self.v_rho = ctk.StringVar(value=str(FLUENT_DEFAULTS["rho"]))
        self.v_mu = ctk.StringVar(value=str(FLUENT_DEFAULTS["mu"]))

        self.v_theta0 = ctk.StringVar(value="0.0")
        self.v_offsets = ctk.StringVar(value="auto")
        self.v_sign = ctk.StringVar(value="fixed")
        self.v_rot_dir = ctk.StringVar(value="ccw")
        self.v_azimuth_zero = ctk.StringVar(value="into_wind")
        self.v_azimuth_offset = ctk.StringVar(value="0.0")
        self.v_static_stall = ctk.StringVar(value="12.0")

        self.v_tol = ctk.StringVar(value="5.0")
        self.v_nstable = ctk.StringVar(value="2")
        self.v_custom_revs = ctk.StringVar(value="")
        self.v_bin = ctk.StringVar(value="1.0")
        self.v_dt = ctk.StringVar(value="")
        self.v_write_ts = ctk.BooleanVar(value=True)

        # Files
        self.v_total = ctk.StringVar()
        self.v_shaft = ctk.StringVar()
        self.v_fx = ctk.StringVar()
        self.v_fy = ctk.StringVar()
        self.v_fx_shaft = ctk.StringVar()
        self.v_fy_shaft = ctk.StringVar()
        self.v_blades = [ctk.StringVar() for _ in range(8)]
        self.v_fx_blades = [ctk.StringVar() for _ in range(8)]
        self.v_fy_blades = [ctk.StringVar() for _ in range(8)]
        self.v_prior_source = ctk.StringVar()
        self.v_continuity_mode = ctk.StringVar(value="Auto: Deduplicate + Recover Gaps")

        # Output
        default_out = os.path.join(os.path.expanduser("~"), "VAWT_results")
        self.v_out = ctk.StringVar(value=default_out)
        self.v_prefix = ctk.StringVar(value="VAWT_analysis")

        # Preset name
        self.v_preset = ctk.StringVar(value="Select Preset...")

        # Case Metadata fields (Tier 2)
        self.v_meta_cells = ctk.StringVar(value="")
        self.v_meta_turb = ctk.StringVar(value="")
        self.v_meta_dt = ctk.StringVar(value="")
        self.v_meta_iter = ctk.StringVar(value="20")
        self.v_meta_res = ctk.StringVar(value="1e-5")
        self.v_meta_domain = ctk.StringVar(value="30R x 20R")
        self.v_meta_interface = ctk.StringVar(value="Sliding Mesh")
        self.v_meta_center_ok = ctk.BooleanVar(value=True)

        # Worker cancellation and diagnostics
        self.cancel_event: Optional[threading.Event] = None
        self.last_error_traceback: Optional[str] = None
        self.applied_repairs: List[str] = []
        self.v_ignore_diag_errors: bool = False

        # Top-level page tracking and navigation options
        self.current_page: str = "input"
        self.has_new_results: bool = False
        self.total_warnings: int = 0
        self.v_auto_open_results = ctk.BooleanVar(value=True)

    def _build_ui(self):
        """Construct top header bar, persistent footer, and 3-page main container."""
        # ---- 1. Top Header Bar (Always visible) ----
        top_frame = ctk.CTkFrame(self, height=56, corner_radius=0)
        top_frame.pack(fill="x", side="top", padx=0, pady=0)

        # Left: App title & custom VAWT rotor logo
        title_box = ctk.CTkFrame(top_frame, fg_color="transparent")
        title_box.pack(side="left", padx=(14, 12), pady=8)

        self._header_logo_img = None
        try:
            from PIL import Image
            from vawt_post.icon_assets import ensure_app_icons
            _, png_path = ensure_app_icons()
            if os.path.isfile(png_path):
                pil_logo = Image.open(png_path)
                self._header_logo_img = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(30, 30))
        except Exception:
            self._header_logo_img = None

        if self._header_logo_img is not None:
            logo_lbl = ctk.CTkLabel(title_box, text="", image=self._header_logo_img)
            logo_lbl.pack(side="left", padx=(0, 8))
            title_lbl = ctk.CTkLabel(
                title_box,
                text=APP_TITLE,
                font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
            )
            title_lbl.pack(side="left")
        else:
            title_lbl = ctk.CTkLabel(
                title_box,
                text=f"🌀  {APP_TITLE}",
                font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
            )
            title_lbl.pack(side="left")

        ver_lbl = ctk.CTkLabel(
            title_box,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="gray"
        )
        ver_lbl.pack(side="left", padx=6)

        # Center / User-marked position: The Four Large Tab Buttons (~40px high, equal width)
        tabs_box = ctk.CTkFrame(top_frame, fg_color="transparent")
        tabs_box.pack(side="left", padx=(20, 20), pady=6, fill="y")

        btn_w = 145
        btn_h = 38
        self.tab_btn_input = ctk.CTkButton(
            tabs_box,
            text="📝 Input",
            width=btn_w,
            height=btn_h,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self._switch_page("input")
        )
        self.tab_btn_input.pack(side="left", padx=3)

        self.tab_btn_results = ctk.CTkButton(
            tabs_box,
            text="📊 Results",
            width=btn_w,
            height=btn_h,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self._switch_page("results")
        )
        self.tab_btn_results.pack(side="left", padx=3)

        self.tab_btn_live = ctk.CTkButton(
            tabs_box,
            text="📡 Live Monitor",
            width=btn_w,
            height=btn_h,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self._switch_page("live")
        )
        self.tab_btn_live.pack(side="left", padx=3)

        self.tab_btn_workbench = ctk.CTkButton(
            tabs_box,
            text="📁 Workbench & GCI",
            width=165,
            height=btn_h,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self._switch_page("workbench")
        )
        self.tab_btn_workbench.pack(side="left", padx=3)

        # Tooltips for page tabs
        ToolTip(self.tab_btn_input, "Setup simulation parameters & input files (Ctrl+1)")
        ToolTip(self.tab_btn_results, "Inspect post-processed results, graphs & logs (Ctrl+2)")
        ToolTip(self.tab_btn_live, "Watch active CFD simulation run in real-time (Ctrl+3)")
        ToolTip(self.tab_btn_workbench, "Workbench project explorer & ASME V&V 20 GCI analysis (Ctrl+4)")

        # ---- 2. Persistent Bottom Footer (Always visible) ----
        self.footer = ctk.CTkFrame(self, height=36, corner_radius=0, fg_color=("gray85", "gray15"))
        self.footer.pack(fill="x", side="bottom", padx=0, pady=0)

        # Footer Left: State status
        self.footer_status_lbl = ctk.CTkLabel(
            self.footer,
            text="● Idle",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=("gray25", "gray75"),
            width=220,
            anchor="w"
        )
        self.footer_status_lbl.pack(side="left", padx=(14, 8), pady=6)

        # Footer progress bar
        self.footer_progress = ctk.CTkProgressBar(self.footer, mode="indeterminate", width=120)
        self.footer_progress.pack(side="left", padx=6, pady=6)
        self.footer_progress.set(0)

        # Footer warning count badge
        self.footer_warn_lbl = ctk.CTkLabel(
            self.footer,
            text="⚠️ Warnings: 0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="gray"
        )
        self.footer_warn_lbl.pack(side="left", padx=8, pady=6)

        # Footer Right: Clickable last log message
        self.footer_log_btn = ctk.CTkButton(
            self.footer,
            text="📋 Log: Ready",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            fg_color="transparent",
            text_color=("gray30", "gray70"),
            hover_color=("gray75", "gray25"),
            height=26,
            command=self._open_execution_log
        )
        self.footer_log_btn.pack(side="right", padx=(4, 14), pady=5)
        ToolTip(self.footer_log_btn, "Click to view full Execution Log in Results page")

        # ---- 3. Main Workspace Container ----
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(fill="both", expand=True, padx=8, pady=4)
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)

        # Page 1: Input Page Frame
        self._report_startup(38, "Building Input & File Health workspace...")
        self.page_input = ctk.CTkFrame(self.main_container, fg_color=("gray92", "gray14"), corner_radius=0)
        self._build_input_page(self.page_input)

        # Page 2: Results Page Frame
        self._report_startup(56, "Preparing Results & Excel reporting...")
        self.page_results = ctk.CTkFrame(self.main_container, fg_color=("gray92", "gray14"), corner_radius=0)
        self.results_frame = ResultsFrame(self.page_results, app=self)
        self.results_frame.pack(fill="both", expand=True)

        # Page 3: Live Monitor Page Frame
        self._report_startup(72, "Initializing Live Monitor & Matplotlib plots...")
        self.page_live = ctk.CTkFrame(self.main_container, fg_color=("gray92", "gray14"), corner_radius=0)
        self.live_monitor_frame = LiveMonitorFrame(self.page_live, app=self)
        self.live_monitor_frame.pack(fill="both", expand=True)

        # Page 4: Workbench & GCI Studio Page Frame
        self._report_startup(86, "Initializing Workbench & GCI Studio...")
        self.page_workbench = ctk.CTkFrame(self.main_container, fg_color=("gray92", "gray14"), corner_radius=0)
        self.workbench_frame = WorkbenchStudioFrame(self.page_workbench, app=self)
        self.workbench_frame.pack(fill="both", expand=True)

        # Map all pages once initially to pre-warm their Win32 layout & canvas sizing
        for pg in (self.page_input, self.page_results, self.page_live, self.page_workbench):
            pg.grid(row=0, column=0, sticky="nsew")
        self.update_idletasks()
        for pg in (self.page_input, self.page_results, self.page_live, self.page_workbench):
            pg.grid_remove()

        # Compatibility aliases
        self.log_text = self.results_frame.log_text
        self.preview_frame = self.results_frame
        self.progress_bar = self.footer_progress
        self.tabview = _TabviewCompat(self)

    def _build_input_page(self, parent):
        """Construct Page 1: Input (Two columns: Left parameters, Right files + diagnostics + run)."""
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # Left Column: Parameter Cards (Scrollable)
        left_scroll = ctk.CTkScrollableFrame(body, width=490, corner_radius=10)
        left_scroll.pack(side="left", fill="both", padx=(0, 6), pady=0)
        self._build_parameter_cards(left_scroll)

        # Right Column: Files, File Check, Output Settings, Run Controls (Scrollable)
        right_scroll = ctk.CTkScrollableFrame(body, corner_radius=10)
        right_scroll.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=0)
        self._build_right_column(right_scroll)

    def _build_parameter_cards(self, parent):
        """Build grouped parameter cards in left panel with Saved Parameters at top."""
        # 0. Saved Parameters & Presets Card
        p_card = SectionCard(parent, title="Saved Parameters", subtitle="Turbine Profiles & Presets")
        p_card.pack(fill="x", pady=(0, 8))

        row0 = ctk.CTkFrame(p_card.content_frame, fg_color="transparent")
        row0.pack(fill="x", pady=(2, 4))
        ctk.CTkLabel(row0, text="Preset Profile:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(2, 6))

        presets = list(self.config_mgr.get_presets().keys())
        self.preset_menu = ctk.CTkOptionMenu(
            row0,
            variable=self.v_preset,
            values=presets if presets else ["No presets"],
            width=220,
            command=self._on_preset_select
        )
        self.preset_menu.pack(side="left", padx=4)

        row1 = ctk.CTkFrame(p_card.content_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        ctk.CTkButton(
            row1,
            text="💾 Save as Preset...",
            width=135,
            height=28,
            command=self._save_preset_dialog
        ).pack(side="left", padx=(2, 4))

        ctk.CTkButton(
            row1,
            text="⭐ Set as Default",
            width=115,
            height=28,
            fg_color=("teal", "#0D9488"),
            hover_color=("dark cyan", "#0F766E"),
            command=self._save_as_default
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row1,
            text="↺ Reset",
            width=65,
            height=28,
            fg_color="transparent",
            border_width=1,
            command=self._reset_to_default
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row1,
            text="🗑️ Delete",
            width=65,
            height=28,
            fg_color="transparent",
            border_width=1,
            text_color="#EF4444",
            hover_color=("gray85", "#3A1B1B"),
            command=self._delete_current_preset
        ).pack(side="left", padx=2)

        row2 = ctk.CTkFrame(p_card.content_frame, fg_color="transparent")
        row2.pack(fill="x", pady=(2, 4))
        ctk.CTkButton(
            row2,
            text="Export JSON",
            width=90,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._export_json
        ).pack(side="right", padx=(2, 4))

        ctk.CTkButton(
            row2,
            text="Import JSON",
            width=90,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._import_json
        ).pack(side="right", padx=2)

        # 1. Operating Point
        c1 = SectionCard(parent, title="Operating Point", subtitle="Flow & Rotor Speed")
        c1.pack(fill="x", pady=8)
        self._add_entry(c1.content_frame, 0, "Free-stream speed U", self.v_U, "m/s", "Inflow wind speed.")
        self._add_entry(c1.content_frame, 1, "Tip speed ratio λ", self.v_tsr, "-", "TSR = ω·R / U.")
        self._add_entry(c1.content_frame, 2, "or Rotor speed ω", self.v_omega, "rad/s", "Leave blank to derive from λ·U/R.")

        self.lbl_derived_physics = ctk.CTkLabel(
            c1.content_frame, text="", font=ctk.CTkFont(size=11),
            text_color=("royal blue", "#60A5FA"), justify="left", wraplength=428
        )
        self.lbl_derived_physics.grid(row=3, column=0, columnspan=3, sticky="w", padx=4, pady=(4, 2))
        for v in (self.v_U, self.v_tsr, self.v_omega, self.v_R, self.v_depth, self.v_rho):
            v.trace_add("write", lambda *_: self._update_derived_physics())

        # 2. Geometry
        c2 = SectionCard(parent, title="Turbine Geometry", subtitle="Dimensions & Blades")
        c2.pack(fill="x", pady=8)
        self._add_entry(c2.content_frame, 0, "Rotor radius R", self.v_R, "m", "Distance from axis of rotation to blade chord line.")
        self._add_entry(c2.content_frame, 1, "Blade chord c", self.v_chord, "m", "Airfoil chord length.")

        ctk.CTkLabel(c2.content_frame, text="Number of blades N:").grid(row=2, column=0, sticky="w", pady=4)
        nb_row = ctk.CTkFrame(c2.content_frame, fg_color="transparent")
        nb_row.grid(row=2, column=1, sticky="w", pady=4)
        self.nb_slider = ctk.CTkSlider(
            nb_row, from_=1, to=8, number_of_steps=7, width=120,
            command=self._on_blade_slider_change
        )
        self.nb_slider.set(float(self.v_nb.get() or 3))
        self.nb_slider.pack(side="left", padx=(0, 8))
        self.nb_label = ctk.CTkLabel(nb_row, textvariable=self.v_nb, width=24, font=ctk.CTkFont(weight="bold"))
        self.nb_label.pack(side="left")
        ToolTip(self.nb_slider, "Number of straight blades (1 to 8). Dynamically configures blade file pickers.")

        self._add_entry(c2.content_frame, 3, "Static stall angle", self.v_static_stall, "deg", "Airfoil static stall angle magnitude α_ss (default ±12°).")

        # 3. Analysis & Convergence (Placed prominently near top of Left Column)
        c5 = SectionCard(parent, title="Analysis & Convergence", subtitle="Revolution Selection, Tolerances & Bins")
        c5.pack(fill="x", pady=8)
        self._add_entry(c5.content_frame, 0, "Stability tolerance", self.v_tol, "%", "Max allowable rev-to-rev Cp percentage change to qualify as stable (default 5.0%).")
        self._add_entry(c5.content_frame, 1, "Revolutions to report", self.v_nstable, "revs", "Number of trailing revolutions to average and report when Specific revolution(s) is blank (default 2).")

        lbl_crevs = ctk.CTkLabel(c5.content_frame, text="Specific revolution(s):", anchor="w")
        lbl_crevs.grid(row=2, column=0, sticky="w", pady=3)
        self.entry_custom_revs = ctk.CTkEntry(
            c5.content_frame, textvariable=self.v_custom_revs, width=110
        )
        self.entry_custom_revs.grid(row=2, column=1, sticky="w", padx=6, pady=3)
        ctk.CTkLabel(c5.content_frame, text="e.g. 14-16 or 16", text_color="gray", anchor="w").grid(row=2, column=2, sticky="w", pady=3)
        ToolTip(
            self.entry_custom_revs,
            "Leave blank for default analysis (reports last N revolutions and keeps all revolutions). "
            "Enter revolution number(s) like '16' or '14-16' to analyze and export ONLY those specific revolution(s)."
        )

        self._add_entry(c5.content_frame, 3, "Azimuth bin width", self.v_bin, "deg", "Bin width for phase averaging (e.g. 1°, 0.5°, 2°). Must divide 360°.")
        self._add_entry(c5.content_frame, 4, "Time step fallback dt", self.v_dt, "s", "Only required if monitor files have no flow-time column.")

        ctk.CTkCheckBox(
            c5.content_frame,
            text="Write full time series to Excel (Processed sheet)",
            variable=self.v_write_ts
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 2))

        # 4. Fluid & 2D Reference
        c3 = SectionCard(parent, title="Fluid & 2D Reference", subtitle="Fluent Defaults Available")
        c3.pack(fill="x", pady=8)
        self._add_entry(c3.content_frame, 0, "Air density ρ", self.v_rho, "kg/m³", "Fluent air default: 1.225 kg/m³.")
        self._add_entry(c3.content_frame, 1, "Viscosity μ", self.v_mu, "Pa·s", "Fluent air default: 1.7894e-5 Pa·s.")
        self._add_entry(c3.content_frame, 2, "2D depth reference", self.v_depth, "m", "Spanwise extrusion depth in 2D simulation (Fluent default 1.0 m).")

        ctk.CTkButton(
            c3.content_frame,
            text="Reset to Fluent Defaults",
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._reset_fluent_defaults
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))

        # 5. Azimuth & Moment Sign
        c4 = SectionCard(parent, title="Azimuth & Moment Sign", subtitle="Coordinate Conventions")
        c4.pack(fill="x", pady=8)
        self._add_entry(c4.content_frame, 0, "Blade 1 start azimuth θ0", self.v_theta0, "deg", "Azimuth at t=0.")

        ctk.CTkLabel(c4.content_frame, text="Zero position:").grid(row=1, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(
            c4.content_frame,
            variable=self.v_azimuth_zero,
            values=["into_wind", "top", "front", "bottom", "rear"],
            width=110
        ).grid(row=1, column=1, sticky="w", pady=4)

        self._add_entry(c4.content_frame, 2, "Azimuth free offset", self.v_azimuth_offset, "deg", "Additional free azimuthal offset.")

        ctk.CTkLabel(c4.content_frame, text="Rotation direction:").grid(row=3, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(
            c4.content_frame,
            variable=self.v_rot_dir,
            values=["ccw", "cw"],
            width=110
        ).grid(row=3, column=1, sticky="w", pady=4)

        ctk.CTkLabel(c4.content_frame, text="Blade phasing:").grid(row=4, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(
            c4.content_frame,
            variable=self.v_offsets,
            values=["auto", "even"],
            width=110
        ).grid(row=4, column=1, sticky="w", pady=4)

        ctk.CTkLabel(c4.content_frame, text="Moment sign:").grid(row=5, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(
            c4.content_frame,
            variable=self.v_sign,
            values=["fixed", "auto", "+1", "-1"],
            width=110
        ).grid(row=5, column=1, sticky="w", pady=4)

        self.lbl_theta0_status = ctk.CTkLabel(
            c4.content_frame,
            text="Start azimuth θ0 check: Run Auto-Detect or Diagnostics to verify",
            font=ctk.CTkFont(size=10),
            text_color="#94A3B8",
            anchor="w",
            justify="left",
            wraplength=320,
        )
        self.lbl_theta0_status.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 2))

        # 6. CFD Setup & Case Metadata (Tier 2)
        c6 = SectionCard(parent, title="CFD Case & Mesh Metadata", subtitle="Numerical Reproducibility")
        c6.pack(fill="x", pady=8)
        self._add_entry(c6.content_frame, 0, "Mesh cell count", self.v_meta_cells, "cells", "Total cells in mesh domain.")
        self._add_entry(c6.content_frame, 1, "Turbulence model", self.v_meta_turb, "-", "e.g. k-omega SST, Transition SST.")
        self._add_entry(c6.content_frame, 2, "Time step size dt", self.v_meta_dt, "s", "Nominal simulation physical time step.")
        self._add_entry(c6.content_frame, 3, "Iterations / step", self.v_meta_iter, "-", "Inner iterations per time step.")
        self._add_entry(c6.content_frame, 4, "Residual target", self.v_meta_res, "-", "Convergence target (e.g. 1e-5).")
        self._add_entry(c6.content_frame, 5, "Domain size", self.v_meta_domain, "-", "e.g. 30R x 20R.")
        self._add_entry(c6.content_frame, 6, "Interface type", self.v_meta_interface, "-", "e.g. Sliding Mesh, Overset.")
        ctk.CTkCheckBox(
            c6.content_frame,
            text="Moment center = rotor axis confirmed",
            variable=self.v_meta_center_ok
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 2))

    def _build_right_column(self, parent):
        """Build file pickers, file check panel, output paths, and run controls."""
        # 1. Input Files Card
        f_card = SectionCard(parent, title="Input Files", subtitle="ANSYS Fluent Monitor (.out / .txt / .dat)")
        f_card.pack(fill="x", pady=(0, 6))

        # Top file tools bar (two clean rows so buttons never clip at 1280x720)
        f_top = ctk.CTkFrame(f_card.content_frame, fg_color="transparent")
        f_top.pack(fill="x", pady=(0, 6))

        f_top_btns = ctk.CTkFrame(f_top, fg_color="transparent")
        f_top_btns.pack(fill="x")

        ctk.CTkButton(
            f_top_btns,
            text="📁 Import Workbench (.wbpj)...",
            command=self._quick_import_workbench,
            fg_color=("teal", "#0D9488"),
            hover_color=("dark cyan", "#0F766E")
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            f_top_btns,
            text="🔍 Auto-detect from Folder...",
            command=self._autodetect_folder,
            fg_color=("RoyalBlue3", "#2563EB"),
            hover_color=("RoyalBlue4", "#1D4ED8")
        ).pack(side="left")

        ctk.CTkButton(
            f_top_btns,
            text="⚡ Multi-Stage Generator...",
            command=self._open_multistage_generator,
            fg_color=("SeaGreen3", "#059669"),
            hover_color=("SeaGreen4", "#047857")
        ).pack(side="left", padx=6)

        ctk.CTkLabel(
            f_top,
            text="Supports 0.25°, 0.5°, 1°, 2°, 5° shifts",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            anchor="w"
        ).pack(fill="x", padx=2, pady=(4, 0))

        # File rows container
        self.file_rows_container = ctk.CTkFrame(f_card.content_frame, fg_color="transparent")
        self.file_rows_container.pack(fill="x")
        self.file_rows_container.columnconfigure(1, weight=1)

        self.blade_widgets = []
        self._build_file_inputs()

        # Compact Prior Run / Backup Recovery panel inside Input Files card
        prior_box = ctk.CTkFrame(f_card.content_frame, corner_radius=8, fg_color=("gray88", "#18202B"))
        prior_box.pack(fill="x", pady=(10, 2))

        p_hdr = ctk.CTkFrame(prior_box, fg_color="transparent")
        p_hdr.pack(fill="x", padx=8, pady=(6, 3))
        ctk.CTkLabel(
            p_hdr,
            text="Prior Run / Backup Link (Crash & Gap Recovery)",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="left")

        self._cont_mode_to_code = {
            "Auto: Deduplicate + Recover Gaps": "auto",
            "Prefer Backup on Overlap": "prefer_backup",
            "Splice Only (No Gap Fill)": "splice_only",
            "Disabled": "off",
        }
        self._cont_code_to_mode = {v: k for k, v in self._cont_mode_to_code.items()}
        self.continuity_mode_menu = ctk.CTkOptionMenu(
            p_hdr,
            values=list(self._cont_mode_to_code.keys()),
            variable=self.v_continuity_mode,
            width=210,
            height=24,
            font=ctk.CTkFont(size=10),
            command=lambda _: self._on_prior_changed(run_diag=True),
        )
        self.continuity_mode_menu.pack(side="right")

        p_row = ctk.CTkFrame(prior_box, fg_color="transparent")
        p_row.pack(fill="x", padx=8, pady=2)
        p_row.columnconfigure(0, weight=1)

        self.entry_prior_source = ctk.CTkEntry(
            p_row,
            textvariable=self.v_prior_source,
            height=28,
            placeholder_text="Optional: initial backup folder or backup.zip...",
        )
        self.entry_prior_source.grid(row=0, column=0, sticky="we", padx=(0, 6))
        self.entry_prior_source.bind("<Return>", lambda _: self._on_prior_changed(run_diag=True))
        self.entry_prior_source.bind("<FocusOut>", lambda _: self._on_prior_changed(run_diag=False))

        ctk.CTkButton(
            p_row, text="📂 Folder...", width=82, height=28,
            command=self._browse_prior_folder,
        ).grid(row=0, column=1, padx=2)
        ctk.CTkButton(
            p_row, text="🗜️ .zip...", width=72, height=28,
            command=self._browse_prior_zip,
        ).grid(row=0, column=2, padx=2)
        ctk.CTkButton(
            p_row, text="✕", width=28, height=28,
            fg_color="transparent", border_width=1,
            command=self._clear_prior_source,
        ).grid(row=0, column=3, padx=(2, 0))

        p_foot = ctk.CTkFrame(prior_box, fg_color="transparent")
        p_foot.pack(fill="x", padx=8, pady=(2, 6))
        self.lbl_prior_status = ctk.CTkLabel(
            p_foot,
            text="Link an initial run folder or backup.zip to stitch restarts & recover lost steps.",
            font=ctk.CTkFont(size=10),
            text_color="#94A3B8",
            anchor="w",
            justify="left",
            wraplength=410,
        )
        self.lbl_prior_status.pack(side="left", fill="x", expand=True)

        self.btn_export_stitched = ctk.CTkButton(
            p_foot,
            text="💾 Export Stitched...",
            width=128,
            height=26,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("#0284C7", "#0369A1"),
            hover_color=("#0369A1", "#075985"),
            command=self._export_stitched_files,
        )
        self.btn_export_stitched.pack(side="right", padx=(6, 0))

        # 2. File Check & Diagnostics Panel (Part A)
        diag_card = SectionCard(parent, title="File Check", subtitle="Embedded Health Table & Verification")
        diag_card.pack(fill="x", pady=6)
        self.diagnostics_frame = FileDiagnosticsFrame(diag_card.content_frame, app=self)
        self.diagnostics_frame.pack(fill="both", expand=True, padx=2, pady=2)

        # 3. Output Settings Card
        o_card = SectionCard(parent, title="Output Settings", subtitle="Destination & Prefix")
        o_card.pack(fill="x", pady=6)
        o_card.content_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(o_card.content_frame, text="Output Directory:").grid(row=0, column=0, sticky="w", pady=3)
        ctk.CTkEntry(o_card.content_frame, textvariable=self.v_out).grid(row=0, column=1, sticky="we", padx=6, pady=3)
        ctk.CTkButton(
            o_card.content_frame,
            text="Browse...",
            width=80,
            command=self._browse_output_dir
        ).grid(row=0, column=2, padx=(0, 2), pady=3)

        ctk.CTkLabel(o_card.content_frame, text="File Prefix:").grid(row=1, column=0, sticky="w", pady=3)
        ctk.CTkEntry(o_card.content_frame, textvariable=self.v_prefix, width=200).grid(row=1, column=1, sticky="w", padx=6, pady=3)

        # 4. Run Controls Action Bar
        action_bar = ctk.CTkFrame(parent, corner_radius=10, fg_color=("gray85", "gray17"))
        action_bar.pack(fill="x", pady=6)

        btn_row = ctk.CTkFrame(action_bar, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(8, 2))

        self.run_btn = ctk.CTkButton(
            btn_row,
            text="▶  Run Analysis",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            height=40,
            width=150,
            fg_color=("#16A34A", "#15803D"),
            hover_color=("#15803D", "#166534"),
            command=self._start_analysis
        )
        self.run_btn.pack(side="left", padx=(0, 4))

        self.cancel_btn = ctk.CTkButton(
            btn_row,
            text="⏹  Cancel",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            height=40,
            width=90,
            fg_color=("#DC2626", "#B91C1C"),
            hover_color=("#B91C1C", "#991B1B"),
            state="disabled",
            command=self._cancel_analysis
        )
        self.cancel_btn.pack(side="left", padx=4)

        self.diag_btn = ctk.CTkButton(
            btn_row,
            text="🛠  Save Diagnostics",
            height=36,
            width=145,
            fg_color=("gray70", "gray30"),
            hover_color=("gray60", "gray40"),
            command=self._save_diagnostics
        )
        self.diag_btn.pack(side="right", padx=(4, 0))

        self.open_dir_btn = ctk.CTkButton(
            btn_row,
            text="📁 Open Output",
            height=36,
            width=130,
            state="disabled",
            command=self._open_output_dir
        )
        self.open_dir_btn.pack(side="right", padx=4)

        status_row = ctk.CTkFrame(action_bar, fg_color="transparent")
        status_row.pack(fill="x", padx=12, pady=(2, 2))

        self.status_lbl = ctk.CTkLabel(status_row, text="Ready", font=ctk.CTkFont(size=12), anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)

        self.main_diag_badge = ctk.CTkLabel(
            status_row,
            text="Diagnostics: ✓ Not Run",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray",
            anchor="e"
        )
        self.main_diag_badge.pack(side="right", padx=(8, 0))

        opt_row = ctk.CTkFrame(action_bar, fg_color="transparent")
        opt_row.pack(fill="x", padx=12, pady=(2, 8))

        ctk.CTkCheckBox(
            opt_row,
            text="After a successful run, open the Results page",
            variable=self.v_auto_open_results
        ).pack(side="left")

    def _switch_page(self, target: str, force: bool = False):
        """Switch active page instantaneously in a single atomic Win32 paint frame."""
        target = target.lower()
        if target not in ("input", "results", "live", "workbench"):
            return

        is_wb = (target in ("workbench", "gci"))
        prev_page = getattr(self, "current_page", None)
        if not force and prev_page == target:
            if target == "results" and self.has_new_results:
                self.has_new_results = False
                self._update_tab_badges()
            return

        self.current_page = target
        self.config_mgr.set_last_page(target)

        active_fg = ("#2563EB", "#1D4ED8")
        inactive_fg = ("gray80", "gray22")
        active_hover = ("#1D4ED8", "#1E40AF")
        inactive_hover = ("gray70", "gray30")
        active_tc = ("#FFFFFF", "#FFFFFF")
        inactive_tc = ("#111827", "#DCE4EE")

        btn_map = {
            "input": self.tab_btn_input,
            "results": self.tab_btn_results,
            "live": self.tab_btn_live,
            "workbench": self.tab_btn_workbench,
        }

        page_map = {
            "input": self.page_input,
            "results": self.page_results,
            "live": self.page_live,
            "workbench": self.page_workbench,
        }

        if force or prev_page not in btn_map:
            for k, btn in btn_map.items():
                is_act = (k == "workbench" and is_wb) or (k == target)
                btn.configure(
                    fg_color=active_fg if is_act else inactive_fg,
                    hover_color=active_hover if is_act else inactive_hover,
                    text_color=active_tc if is_act else inactive_tc,
                )
        else:
            prev_key = "workbench" if prev_page in ("workbench", "gci") else prev_page
            curr_key = "workbench" if is_wb else target
            if prev_key in btn_map and prev_key != curr_key:
                btn_map[prev_key].configure(fg_color=inactive_fg, hover_color=inactive_hover, text_color=inactive_tc)
            if curr_key in btn_map:
                btn_map[curr_key].configure(fg_color=active_fg, hover_color=active_hover, text_color=active_tc)

        if target == "results" and self.has_new_results:
            self.has_new_results = False
            self._update_tab_badges()
        elif force:
            self._update_tab_badges()

        # Unmap all inactive pages first so old content never bleeds through during transition
        curr_page_key = "workbench" if is_wb else target
        for k, pg in page_map.items():
            if k != curr_page_key:
                pg.grid_remove()

        # Map the selected page into the clean container
        page_map[curr_page_key].grid(row=0, column=0, sticky="nsew")

        if target == "results":
            self.results_frame.on_show()
        elif target == "live":
            self.live_monitor_frame.on_show()
        elif is_wb:
            if target == "gci" and hasattr(self, "workbench_frame") and hasattr(self.workbench_frame, "focus_gci"):
                self.workbench_frame.focus_gci()

    def _update_tab_badges(self):
        """Update notification badges on the three header tabs."""
        # 1. Input tab badge (warnings/errors count)
        input_text = "📝 Input"
        if hasattr(self, "diagnostics_frame") and self.diagnostics_frame.report:
            rep = self.diagnostics_frame.report
            errs = rep.summary_counts.get("ERROR", 0)
            warns = rep.summary_counts.get("WARNING", 0)
            if errs > 0:
                input_text += f"  (⚠️ {errs + warns})"
            elif warns > 0:
                input_text += f"  (⚠️ {warns})"
            else:
                input_text += "  (✓)"
        self.tab_btn_input.configure(text=input_text)

        # 2. Results tab badge (dot when new results exist)
        res_text = "📊 Results"
        if getattr(self, "has_new_results", False):
            res_text += "  ●"
        self.tab_btn_results.configure(text=res_text)

        # 3. Live Monitor tab badge (green pulsing while monitoring, amber when stalled)
        if hasattr(self, "live_monitor_frame"):
            lm = self.live_monitor_frame
            is_live_active = getattr(self, "current_page", "") == "live"
            if lm.is_monitoring:
                if lm.latest_snapshot and "Stalled" in lm.latest_snapshot.status:
                    tc = "#FDE68A" if is_live_active else "#F59E0B"
                    self.tab_btn_live.configure(text="📡 Live Monitor  ● Stalled", text_color=tc)
                else:
                    dot = "●" if int(lm.time_until_refresh) % 2 == 0 else "○"
                    tc = "#A7F3D0" if is_live_active else "#10B981"
                    self.tab_btn_live.configure(text=f"📡 Live Monitor  {dot}", text_color=tc)
            else:
                tc = ("#FFFFFF", "#FFFFFF") if is_live_active else ("#111827", "#DCE4EE")
                self.tab_btn_live.configure(text="📡 Live Monitor", text_color=tc)

    def _open_execution_log(self):
        """Navigate directly to the Execution Log inside Results."""
        self._switch_page("results")
        self.results_frame.select_sub_tab("Log")

    def _build_file_inputs(self):
        """Construct entries and browse buttons for all file rows."""
        # Total moment
        self._add_file_row(0, "Total moment file:", self.v_total, "Monitored rotor moment (all blades + shaft).")

        # Blades 1..8
        self.blade_widgets.clear()
        for i in range(8):
            row_widgets = self._add_file_row(
                1 + i,
                f"Blade {i + 1} moment:",
                self.v_blades[i],
                f"Moment file for blade {i + 1}."
            )
            self.blade_widgets.append(row_widgets)

        # Shaft
        self._add_file_row(9, "Shaft moment (optional):", self.v_shaft, "Shaft drag moment monitor.")
        # Force X (thrust)
        self._add_file_row(10, "Force X / Thrust (optional):", self.v_fx, "Streamwise force monitor (thrust).")
        # Force Y (side force)
        self._add_file_row(11, "Force Y / Side force (optional):", self.v_fy, "Cross-stream force monitor (side force).")

        self._update_blade_visibility()

    def _add_file_row(self, row: int, label_text: str, var: ctk.StringVar, hint: str):
        lbl = ctk.CTkLabel(self.file_rows_container, text=label_text, width=170, anchor="w")
        lbl.grid(row=row, column=0, sticky="w", pady=2)

        entry = ctk.CTkEntry(self.file_rows_container, textvariable=var, placeholder_text="Path to .out file...")
        entry.grid(row=row, column=1, sticky="we", padx=6, pady=2)

        def browse():
            p = filedialog.askopenfilename(
                title=label_text,
                filetypes=[("Fluent Report Files", "*.out *.txt *.dat *.csv"), ("All Files", "*.*")]
            )
            if p:
                var.set(p)
                if hasattr(self, "_on_prior_changed") and self.v_prior_source.get().strip():
                    self._on_prior_changed(run_diag=False)
                if hasattr(self, "diagnostics_frame"):
                    self.diagnostics_frame.run_diagnostics()

        btn = ctk.CTkButton(self.file_rows_container, text="Browse...", width=75, command=browse)
        btn.grid(row=row, column=2, sticky="e", pady=2)

        ToolTip(lbl, hint)
        return (lbl, entry, btn)

    def _add_entry(self, parent, row: int, label: str, var: ctk.StringVar, unit: str, hint: str):
        lbl = ctk.CTkLabel(parent, text=f"{label}:", anchor="w")
        lbl.grid(row=row, column=0, sticky="w", pady=3)

        ent = ctk.CTkEntry(parent, textvariable=var, width=110)
        ent.grid(row=row, column=1, sticky="w", padx=6, pady=3)

        u_lbl = ctk.CTkLabel(parent, text=unit, text_color="gray", anchor="w")
        u_lbl.grid(row=row, column=2, sticky="w", pady=3)

        ToolTip(ent, f"{label} ({unit}): {hint}")

    def _on_blade_slider_change(self, val):
        nb = int(round(val))
        self.v_nb.set(str(nb))
        self._update_blade_visibility()

    def _bind_blade_count_change(self):
        self.v_nb.trace_add("write", lambda *a: self._update_blade_visibility())

    def _update_blade_visibility(self):
        try:
            nb = int(self.v_nb.get())
        except ValueError:
            nb = 3
        nb = max(1, min(8, nb))
        for i, widgets in enumerate(self.blade_widgets):
            if i < nb:
                for w in widgets:
                    w.grid()
            else:
                for w in widgets:
                    w.grid_remove()

    def _setup_dnd(self):
        """Enable drag-and-drop support if tkinterdnd2 is available."""
        if _HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)  # type: ignore
                self.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                self.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _on_drop(self, event):
        try:
            raw_data = getattr(event, "data", "")
            if not raw_data:
                return
            paths = self.tk.splitlist(raw_data)
            for item in paths:
                p = os.path.abspath(item.strip("{}'\""))
                if os.path.isdir(p):
                    # Check if directory contains a .wbpj or is a _files directory
                    wbpj_in_dir = glob.glob(os.path.join(p, "*.wbpj"))
                    if wbpj_in_dir or p.endswith("_files"):
                        self._quick_import_workbench(project_path=p)
                        return
                    self._autodetect_folder(folder_path=p)
                    return
                elif p.lower().endswith(".wbpj"):
                    self._quick_import_workbench(project_path=p)
                    return
                elif p.lower().endswith(".json"):
                    self._import_json(file_path=p)
                    return
                elif p.lower().endswith(".zip"):
                    self.v_prior_source.set(p)
                    self._log(f"Linked prior backup archive: {os.path.basename(p)}", "ok")
                    self._on_prior_changed(run_diag=True)
                    return
                elif p.lower().endswith((".out", ".txt", ".dat", ".csv")):
                    low = os.path.basename(p).lower()
                    if "shaft" in low and any(k in low for k in ("force_shaftx", "shaftx", "shaft_x", "shaft-x", "fx_shaft", "fx-shaft")):
                        self.v_fx_shaft.set(p)
                        self._log(f"Assigned shaft force X file: {os.path.basename(p)}", "ok")
                    elif "shaft" in low and any(k in low for k in ("force_shafty", "shafty", "shaft_y", "shaft-y", "fy_shaft", "fy-shaft")):
                        self.v_fy_shaft.set(p)
                        self._log(f"Assigned shaft force Y file: {os.path.basename(p)}", "ok")
                    elif "shaft" in low:
                        self.v_shaft.set(p)
                        self._log(f"Assigned shaft file: {os.path.basename(p)}", "ok")
                    elif "forcex" in low or "force-x" in low or "force_x" in low or low.startswith("fx"):
                        self.v_fx.set(p)
                        self._log(f"Assigned force X (thrust) file: {os.path.basename(p)}", "ok")
                    elif "forcey" in low or "force-y" in low or "force_y" in low or low.startswith("fy"):
                        self.v_fy.set(p)
                        self._log(f"Assigned force Y (side) file: {os.path.basename(p)}", "ok")
                    else:
                        m = re.search(r"(?:moment|blade|torque)[_-]?(\d+)", low)
                        if m and 1 <= int(m.group(1)) <= 8:
                            idx = int(m.group(1)) - 1
                            self.v_blades[idx].set(p)
                            self._log(f"Assigned Blade {idx + 1} file: {os.path.basename(p)}", "ok")
                        elif "moment" in low or "total" in low or "all" in low:
                            self.v_total.set(p)
                            self._log(f"Assigned total moment file: {os.path.basename(p)}", "ok")
            if hasattr(self, "diagnostics_frame"):
                self.diagnostics_frame.run_diagnostics()
        except Exception as exc:
            self._log(f"Drag-and-drop warning: {exc}", "warn")

    # ---- Parameter Persistence & Presets ----
    def _load_initial_parameters(self):
        """Load last session or user default parameters into UI variables."""
        last = self.config_mgr.get_last_session()
        if last and "params" in last:
            self._apply_params_dict(last["params"])
            if "files" in last:
                self._apply_files_dict(last["files"])
            if "out_dir" in last and last["out_dir"]:
                self.v_out.set(last["out_dir"])
            if "prefix" in last and last["prefix"]:
                self.v_prefix.set(last["prefix"])
        else:
            default_p = self.config_mgr.get_user_default()
            self._apply_params_dict(default_p.__dict__)

    def _apply_params_dict(self, d: Dict[str, Any]):
        mapping = {
            "U": self.v_U, "tsr": self.v_tsr, "omega": self.v_omega,
            "R": self.v_R, "chord": self.v_chord, "n_blades": self.v_nb,
            "depth": self.v_depth, "rho": self.v_rho, "mu": self.v_mu,
            "theta0": self.v_theta0, "offsets": self.v_offsets, "moment_sign": self.v_sign,
            "rotation_dir": self.v_rot_dir, "azimuth_zero": self.v_azimuth_zero,
            "azimuth_offset": self.v_azimuth_offset,
            "static_stall_deg": self.v_static_stall,
            "stable_tol": self.v_tol, "n_stable": self.v_nstable,
            "custom_revs": self.v_custom_revs,
            "bin_deg": self.v_bin, "dt": self.v_dt,
        }
        for k, var in mapping.items():
            if k in d and d[k] is not None:
                var.set(str(d[k]))
            elif k in d and d[k] is None:
                var.set("")

        if "write_timeseries" in d:
            self.v_write_ts.set(bool(d["write_timeseries"]))
        if "preset_name" in d and d["preset_name"]:
            self.v_preset.set(d["preset_name"])

        # Apply Case Metadata fields (Tier 2)
        if "metadata" in d and d["metadata"]:
            m = d["metadata"]
            if hasattr(m, "__dict__"):
                m = m.__dict__
            if isinstance(m, dict):
                self.v_meta_cells.set(str(m.get("mesh_cells", "")) if m.get("mesh_cells") is not None else "")
                self.v_meta_turb.set(str(m.get("turb_model", "") or ""))
                self.v_meta_dt.set(str(m.get("time_step_s", "")) if m.get("time_step_s") is not None else "")
                self.v_meta_iter.set(str(m.get("iter_per_step", "20")) if m.get("iter_per_step") is not None else "20")
                self.v_meta_res.set(str(m.get("residual_target", "1e-5") or "1e-5"))
                self.v_meta_domain.set(str(m.get("domain_size", "30R x 20R") or "30R x 20R"))
                self.v_meta_interface.set(str(m.get("interface_type", "Sliding Mesh") or "Sliding Mesh"))
                self.v_meta_center_ok.set(bool(m.get("moment_center_confirmed", True)))

        # Sync blade slider
        try:
            nb = int(self.v_nb.get())
            self.nb_slider.set(nb)
        except Exception:
            pass
        self._update_blade_visibility()
        self._update_derived_physics()

    def _apply_files_dict(self, f_dict: Dict[str, Any]):
        self.v_total.set(f_dict.get("total") or "")
        self.v_shaft.set(f_dict.get("shaft") or "")
        self.v_fx.set(f_dict.get("fx") or "")
        self.v_fy.set(f_dict.get("fy") or "")
        self.v_fx_shaft.set(f_dict.get("fx_shaft") or "")
        self.v_fy_shaft.set(f_dict.get("fy_shaft") or "")
        blades = f_dict.get("blades", [])
        fx_blades = f_dict.get("fx_blades", [])
        fy_blades = f_dict.get("fy_blades", [])
        for i in range(8):
            self.v_blades[i].set(blades[i] if i < len(blades) and blades[i] else "")
            if hasattr(self, "v_fx_blades") and i < len(self.v_fx_blades):
                self.v_fx_blades[i].set(fx_blades[i] if i < len(fx_blades) and fx_blades[i] else "")
            if hasattr(self, "v_fy_blades") and i < len(self.v_fy_blades):
                self.v_fy_blades[i].set(fy_blades[i] if i < len(fy_blades) and fy_blades[i] else "")
        if hasattr(self, "v_prior_source"):
            self.v_prior_source.set(f_dict.get("prior_source") or "")
        if hasattr(self, "v_continuity_mode") and hasattr(self, "_cont_code_to_mode"):
            code = str(f_dict.get("continuity_mode") or "auto")
            self.v_continuity_mode.set(self._cont_code_to_mode.get(code, "Auto: Deduplicate + Recover Gaps"))
            self._on_prior_changed(run_diag=False)
        self.v_ignore_diag_errors = False
        self.applied_repairs.clear()

    def _browse_prior_folder(self):
        folder = filedialog.askdirectory(title="Select Prior / Initial Run Folder")
        if folder:
            self.v_prior_source.set(folder)
            self._log(f"Linked prior backup folder: {folder}", "ok")
            self._on_prior_changed(run_diag=True)

    def _browse_prior_zip(self):
        path = filedialog.askopenfilename(
            title="Select Prior / Backup Archive (.zip)",
            filetypes=[("ZIP Archives", "*.zip"), ("All Files", "*.*")],
        )
        if path:
            self.v_prior_source.set(path)
            self._log(f"Linked prior backup archive: {os.path.basename(path)}", "ok")
            self._on_prior_changed(run_diag=True)

    def _clear_prior_source(self):
        self.v_prior_source.set("")
        self._on_prior_changed(run_diag=True)

    def _get_continuity_code(self) -> str:
        if hasattr(self, "_cont_mode_to_code") and hasattr(self, "v_continuity_mode"):
            return self._cont_mode_to_code.get(self.v_continuity_mode.get(), "auto")
        return "auto"

    def _on_prior_changed(self, run_diag: bool = False):
        """Update continuity preview summary whenever backup path/mode or current files change."""
        src = self.v_prior_source.get().strip() if hasattr(self, "v_prior_source") else ""
        mode = self._get_continuity_code()
        if not src:
            if hasattr(self, "lbl_prior_status"):
                self.lbl_prior_status.configure(
                    text="Link an initial run folder or backup.zip to stitch restarts & recover lost steps.",
                    text_color="#94A3B8",
                )
        elif mode == "off":
            if hasattr(self, "lbl_prior_status"):
                self.lbl_prior_status.configure(
                    text=f"Prior link paused ({os.path.basename(src)}) — Continuity mode is Disabled.",
                    text_color="#94A3B8",
                )
        else:
            try:
                from vawt_post.engine.continuity import load_prior_series, stitch_and_recover_all
                from vawt_post.engine.parser import read_fluent_report

                f_cur = self._gather_files()
                p_safe = self._gather_params_safe_for_diag()
                curr_roles: Dict[str, Any] = {}
                if f_cur.total and os.path.isfile(f_cur.total):
                    curr_roles["total"] = read_fluent_report(f_cur.total)
                for idx_b, bp in enumerate(f_cur.blades):
                    if bp and os.path.isfile(bp):
                        curr_roles[f"blade{idx_b + 1}"] = read_fluent_report(bp)
                if f_cur.shaft and os.path.isfile(f_cur.shaft):
                    curr_roles["shaft"] = read_fluent_report(f_cur.shaft)
                if f_cur.fx and os.path.isfile(f_cur.fx):
                    curr_roles["fx"] = read_fluent_report(f_cur.fx)
                if f_cur.fy and os.path.isfile(f_cur.fy):
                    curr_roles["fy"] = read_fluent_report(f_cur.fy)

                if curr_roles:
                    _, rep = stitch_and_recover_all(
                        curr_roles, prior_source=src, p=p_safe, mode=mode, current_files=f_cur
                    )
                    st = rep.get("status", "")
                    col = "#10B981" if st in ("overlap_verified", "exact_adjacent") else ("#F59E0B" if st in ("gap_recovered", "overlap_divergent", "gap_unfilled") else "#EF4444")
                    self.lbl_prior_status.configure(text=rep.get("summary_text", ""), text_color=col)
                else:
                    loaded = load_prior_series(src, n_blades=(p_safe.n_blades or 3))
                    n_found = len(loaded.get("series", {}))
                    self.lbl_prior_status.configure(
                        text=f"Backup archive ready: {n_found} monitor file(s) in '{loaded['source_label']}'. Select current files to preview seam.",
                        text_color="#10B981" if n_found > 0 else "#F59E0B",
                    )
            except Exception as exc:
                self.lbl_prior_status.configure(
                    text=f"Backup check error: {exc}",
                    text_color="#EF4444",
                )
        if run_diag and hasattr(self, "diagnostics_frame"):
            self.diagnostics_frame.run_diagnostics()

    def _gather_params_safe_for_diag(self) -> Params:
        """Best-effort Params construction for continuity preview without failing on empty UI fields."""
        def _sf(var, default):
            try:
                s = str(var.get()).strip().replace(",", ".")
                return float(s) if s else default
            except Exception:
                return default
        try:
            nb = int(float(self.v_nb.get() or 3))
        except Exception:
            nb = 3
        return Params(
            U=_sf(self.v_U, 8.0),
            tsr=_sf(self.v_tsr, 3.5),
            omega=_sf(self.v_omega, None),
            R=_sf(self.v_R, 0.5),
            chord=_sf(self.v_chord, 0.06),
            n_blades=max(1, min(8, nb)),
            theta0=_sf(self.v_theta0, 0.0),
            rotation_dir=self.v_rot_dir.get() if hasattr(self, "v_rot_dir") else "ccw",
            dt=_sf(self.v_dt, None),
        )

    def _export_stitched_files(self):
        """Export stitched & gap-recovered Fluent .out files to a user-selected directory."""
        f = self._gather_files()
        if not f.prior_source:
            messagebox.showinfo(
                "Backup Required",
                "Please select a prior backup folder or backup.zip archive first."
            )
            return
        has_any_curr = any(
            x and os.path.isfile(x)
            for x in ([f.total, f.shaft, f.fx, f.fy, f.fx_shaft, f.fy_shaft] + list(f.blades))
        )
        if not has_any_curr:
            messagebox.showinfo(
                "Current Files Required",
                "Please assign at least one current monitor .out file to stitch with the backup."
            )
            return
        dest_dir = filedialog.askdirectory(title="Select Output Folder for Clean Stitched .out Files")
        if not dest_dir:
            return
        try:
            from vawt_post.engine.continuity import export_stitched_report_files

            p_safe = self._gather_params_safe_for_diag()
            mode = self._get_continuity_code()
            res_exp = export_stitched_report_files(f, dest_dir, p=p_safe, mode=mode)
            n_written = len(res_exp.get("written_files", {}))
            sum_txt = res_exp.get("summary", {}).get("summary_text", "")
            self._log(f"Exported {n_written} stitched .out file(s) to {dest_dir}: {sum_txt}", "ok")
            messagebox.showinfo(
                "Stitched Files Exported",
                f"Successfully wrote {n_written} merged .out file(s) and continuity report to:\n"
                f"{dest_dir}\n\n{sum_txt}"
            )
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Failed to export stitched files:\n{exc}")

    def _on_preset_select(self, preset_name: str):
        p = self.config_mgr.load_preset(preset_name)
        if p:
            self._apply_params_dict(p.__dict__)
            self._log(f"Loaded preset profile: '{preset_name}'", "ok")

    def _save_preset_dialog(self):
        try:
            p = self._gather_params()
        except ValueError as exc:
            messagebox.showerror("Parameter Error", str(exc))
            return

        def on_confirm(name: str, desc: str):
            p.preset_name = name
            self.config_mgr.save_preset(name, p, desc)
            # Update menu
            presets = list(self.config_mgr.get_presets().keys())
            self.preset_menu.configure(values=presets)
            self.v_preset.set(name)
            self._log(f"Saved custom preset '{name}' to program storage.", "ok")

        PresetDialog(self, on_confirm)

    def _save_as_default(self):
        try:
            p = self._gather_params()
        except ValueError as exc:
            messagebox.showerror("Parameter Error", str(exc))
            return
        self.config_mgr.set_user_default(p)
        self._log("Current parameters saved as permanent program defaults.", "ok")
        messagebox.showinfo("Saved", "Current parameters set as default for future runs.")

    def _reset_to_default(self):
        p = self.config_mgr.get_user_default()
        self._apply_params_dict(p.__dict__)
        self._log("Reset parameters to user defaults.", "ok")

    def _delete_current_preset(self):
        name = self.v_preset.get().strip()
        if not name or name in ("Select Preset...", "No presets"):
            return
        if name in BUILTIN_PRESETS:
            messagebox.showinfo("Built-in Preset", f"'{name}' is a built-in turbine profile and cannot be deleted.")
            return
        if messagebox.askyesno("Delete Preset", f"Are you sure you want to delete custom preset '{name}'?"):
            if self.config_mgr.delete_preset(name):
                presets = list(self.config_mgr.get_presets().keys())
                self.preset_menu.configure(values=presets if presets else ["No presets"])
                self.v_preset.set(presets[0] if presets else "Select Preset...")
                self._log(f"Deleted custom preset: '{name}'", "warn")

    def _reset_fluent_defaults(self):
        self.v_rho.set(str(FLUENT_DEFAULTS["rho"]))
        self.v_mu.set(str(FLUENT_DEFAULTS["mu"]))
        self.v_depth.set(str(FLUENT_DEFAULTS["depth"]))
        self._log("Fluid and 2D depth reset to standard Fluent defaults.", "ok")
        self._update_derived_physics()

    def _update_derived_physics(self):
        try:
            U = float(self.v_U.get().strip().replace(",", ".") or 0.0)
            R = float(self.v_R.get().strip().replace(",", ".") or 0.0)
            depth = float(self.v_depth.get().strip().replace(",", ".") or 1.0)
            rho = float(self.v_rho.get().strip().replace(",", ".") or 1.225)
            w_str = self.v_omega.get().strip().replace(",", ".")
            if w_str:
                omega = float(w_str)
                tsr = (omega * R / U) if U > 0 else 0.0
            else:
                tsr = float(self.v_tsr.get().strip().replace(",", ".") or 0.0)
                omega = (tsr * U / R) if R > 0 else 0.0

            if omega > 0 and R > 0 and U > 0:
                rpm = omega * 60.0 / (2.0 * math.pi)
                Trev = 2.0 * math.pi / omega
                A = 2.0 * R * depth
                Pref = 0.5 * rho * (U ** 3) * A
                txt = (
                    f"⚡ Derived: ω = {omega:.1f} rad/s ({rpm:.0f} RPM) | "
                    f"Trev = {Trev:.4f} s | A = {A:.2f} m² | Pref = {Pref:.1f}\u00A0W"
                )
                self.lbl_derived_physics.configure(text=txt)
            else:
                self.lbl_derived_physics.configure(text="")
        except Exception:
            self.lbl_derived_physics.configure(text="")

    def _export_json(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        try:
            p = self._gather_params()
            f = self._gather_files()
            data = {
                "params": p.__dict__,
                "files": f.__dict__,
                "out_dir": self.v_out.get(),
                "prefix": self.v_prefix.get()
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            self._log(f"Settings exported to: {path}", "ok")
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc))

    def _import_json(self, file_path: Optional[str] = None):
        path = file_path or filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if "params" in data:
                self._apply_params_dict(data["params"])
            if "files" in data:
                self._apply_files_dict(data["files"])
            if "out_dir" in data:
                self.v_out.set(data["out_dir"])
            if "prefix" in data:
                self.v_prefix.set(data["prefix"])
            self._log(f"Loaded settings from: {path}", "ok")
            if hasattr(self, "diagnostics_frame"):
                self.diagnostics_frame.run_diagnostics()
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc))

    def _quick_import_workbench(self, project_path: Optional[str] = None):
        """Open file dialog to pick Workbench project and load into Workbench Studio."""
        path = project_path or filedialog.askopenfilename(
            title="Select ANSYS Workbench Project (.wbpj)",
            filetypes=[("ANSYS Workbench Project", "*.wbpj"), ("All Files", "*.*")]
        )
        if path:
            self._switch_page("workbench")
            self.workbench_frame.entry_proj_path.delete(0, "end")
            self.workbench_frame.entry_proj_path.insert(0, path)
            self.workbench_frame._on_scan_project()

    def _autodetect_folder(self, folder_path: Optional[str] = None):
        folder = folder_path or filedialog.askdirectory(title="Select dp0/FFF-{n} or Fluent Run Folder")
        if not folder:
            return

        # 1. Detect physical simulation parameters and case metadata from folder
        p_detected = detect_folder_simulation_parameters(folder)

        # 2. Determine blade count: prefer detected, fallback to existing entry or 3
        if "n_blades" in p_detected and p_detected["n_blades"]:
            nb = int(p_detected["n_blades"])
            nb = max(1, min(8, nb))
            self.v_nb.set(str(nb))
            if hasattr(self, "nb_slider"):
                self.nb_slider.set(float(nb))
        else:
            try:
                nb = int(self.v_nb.get())
            except ValueError:
                nb = 3

        # 3. Detect and populate monitor files
        fd = autodetect(folder, nb)
        self.v_total.set(fd.total or "")
        self.v_shaft.set(fd.shaft or "")
        self.v_fx.set(fd.fx or "")
        self.v_fy.set(fd.fy or "")
        self.v_fx_shaft.set(getattr(fd, "fx_shaft", None) or "")
        self.v_fy_shaft.set(getattr(fd, "fy_shaft", None) or "")
        for i in range(8):
            b_val = fd.blades[i] if i < len(fd.blades) else None
            self.v_blades[i].set(b_val or "")
            if hasattr(self, "v_fx_blades") and i < len(self.v_fx_blades):
                fx_b = fd.fx_blades[i] if i < len(fd.fx_blades) else None
                self.v_fx_blades[i].set(fx_b or "")
            if hasattr(self, "v_fy_blades") and i < len(self.v_fy_blades):
                fy_b = fd.fy_blades[i] if i < len(fd.fy_blades) else None
                self.v_fy_blades[i].set(fy_b or "")
        self.v_ignore_diag_errors = False
        self.applied_repairs.clear()

        # 4. Populate detected physical parameters non-destructively (never overwrite with blank)
        param_summary = []
        if "U" in p_detected and p_detected["U"] is not None:
            val_u = p_detected["U"]
            self.v_U.set(f"{val_u:.4g}" if isinstance(val_u, float) else str(val_u))
            param_summary.append(f"U={self.v_U.get()} m/s")

        if "omega" in p_detected and p_detected["omega"] is not None:
            val_w = p_detected["omega"]
            self.v_omega.set(f"{val_w:.4g}" if isinstance(val_w, float) else str(val_w))
            param_summary.append(f"ω={self.v_omega.get()} rad/s")
        elif "tsr" in p_detected and p_detected["tsr"] is not None:
            self.v_omega.set("")

        if "tsr" in p_detected and p_detected["tsr"] is not None:
            val_tsr = p_detected["tsr"]
            self.v_tsr.set(f"{val_tsr:.4g}" if isinstance(val_tsr, float) else str(val_tsr))
            param_summary.append(f"λ={self.v_tsr.get()}")

        if "R" in p_detected and p_detected["R"] is not None:
            val_r = p_detected["R"]
            self.v_R.set(f"{val_r:.4g}" if isinstance(val_r, float) else str(val_r))
            param_summary.append(f"R={self.v_R.get()} m")

        if "chord" in p_detected and p_detected["chord"] is not None:
            val_c = p_detected["chord"]
            self.v_chord.set(f"{val_c:.4g}" if isinstance(val_c, float) else str(val_c))
            param_summary.append(f"c={self.v_chord.get()} m")

        if "n_blades" in p_detected and p_detected["n_blades"] is not None:
            param_summary.append(f"N={nb}")

        if "rho" in p_detected and p_detected["rho"] is not None:
            val_rho = p_detected["rho"]
            self.v_rho.set(f"{val_rho:.4g}" if isinstance(val_rho, float) else str(val_rho))
            param_summary.append(f"ρ={self.v_rho.get()} kg/m³")

        if "mu" in p_detected and p_detected["mu"] is not None:
            val_mu = p_detected["mu"]
            self.v_mu.set(f"{val_mu:.6g}" if isinstance(val_mu, float) else str(val_mu))
            param_summary.append(f"μ={self.v_mu.get()} Pa·s")

        if "dt" in p_detected and p_detected["dt"] is not None:
            val_dt = p_detected["dt"]
            dt_str = f"{val_dt:.8g}" if isinstance(val_dt, float) else str(val_dt)
            self.v_dt.set(dt_str)
            self.v_meta_dt.set(dt_str)
            param_summary.append(f"Δt={dt_str} s")

        # Case Metadata fields (Tier 2)
        if "cells" in p_detected and p_detected["cells"] is not None:
            self.v_meta_cells.set(str(p_detected["cells"]))
            param_summary.append(f"cells={p_detected['cells']}")

        mesh_dom = (p_detected.get("mesh_info") or {}).get("mesh", {}).get("domain", {})
        if mesh_dom.get("length_m") and mesh_dom.get("width_m") and hasattr(self, "v_meta_domain"):
            self.v_meta_domain.set(f"{mesh_dom['length_m']:.2f} m × {mesh_dom['width_m']:.2f} m")

        if "turb_model" in p_detected and p_detected["turb_model"]:
            self.v_meta_turb.set(str(p_detected["turb_model"]))
            param_summary.append(f"turb={p_detected['turb_model']}")

        if "iterations" in p_detected and p_detected["iterations"] is not None:
            self.v_meta_iter.set(str(p_detected["iterations"]))
            param_summary.append(f"iter/step={p_detected['iterations']}")

        if p_detected.get("moment_center_confirmed"):
            self.v_meta_center_ok.set(True)

        if p_detected.get("theta0") is not None:
            val_th0 = float(p_detected["theta0"])
            self.v_theta0.set(f"{val_th0:.1f}" if abs(val_th0 % 1.0) > 1e-6 else f"{val_th0:.1f}")
            param_summary.append(f"θ0={self.v_theta0.get()}°")

        if p_detected.get("rotation_dir") in ("ccw", "cw"):
            self.v_rot_dir.set(p_detected["rotation_dir"])

        saz = p_detected.get("start_azimuth")
        if isinstance(saz, dict) and hasattr(self, "lbl_theta0_status"):
            if saz.get("needs_correction") and saz.get("recommended_theta0_deg") is not None:
                self.lbl_theta0_status.configure(
                    text=f"⚠ Mid-spin start detected: applied θ0 = {float(saz['recommended_theta0_deg']):.1f}° ({saz.get('method', '')})",
                    text_color="#F59E0B",
                )
                self._log(f"Start azimuth auto-detect: {saz.get('detail', saz.get('summary', ''))}", "warn")
            elif saz.get("mid_spin_compensated_by_flow_time"):
                self.lbl_theta0_status.configure(
                    text=f"ℹ Mid-run start at θ≈{float(saz.get('flow_start_angle_deg') or 0.0):.1f}° (flow-time auto-aligned; θ0=0°)",
                    text_color="#38BDF8",
                )
                self._log(f"Start azimuth auto-detect: {saz.get('summary', '')}", "info")
            elif saz.get("started_at_zero") is True:
                self.lbl_theta0_status.configure(
                    text=f"✓ {saz.get('summary', '0° start verified')}",
                    text_color="#10B981",
                )
            else:
                self.lbl_theta0_status.configure(
                    text=str(saz.get("summary") or "Start azimuth check: pending"),
                    text_color="#94A3B8",
                )

        # Trigger derived physics badge recalculation
        self._update_derived_physics()

        # Log confirmation & Workbench / Mesh / Setup auto-detected notes
        src_text = f" (from {', '.join(p_detected['source_files'])})" if p_detected.get("source_files") else ""
        self._log(f"Auto-detected Fluent files in folder: {folder}", "ok")
        if param_summary:
            self._log(f"Auto-detected parameters{src_text}: {', '.join(param_summary)}", "ok")
        for note in (p_detected.get("setup_notes") or []):
            self._log(f"Auto-Note: {note}", "info")

        # Set sensible output dir next to system/data folder if still default
        if "VAWT_results" in self.v_out.get():
            sys_ctx = p_detected.get("system_context") or {}
            out_base = str(sys_ctx.get("system_dir") or folder)
            self.v_out.set(os.path.join(out_base, "results"))

        # Run file diagnostics immediately after loading files
        if hasattr(self, "_on_prior_changed") and self.v_prior_source.get().strip():
            self._on_prior_changed(run_diag=False)
        if hasattr(self, "diagnostics_frame"):
            self.diagnostics_frame.run_diagnostics()

    def _open_multistage_generator(self):
        try:
            p = self._gather_params()
        except Exception:
            def safe_f(v_str, default):
                try:
                    return float(v_str.strip().replace(",", "."))
                except Exception:
                    return default
            p = Params(
                U=safe_f(self.v_U.get(), 8.0),
                tsr=safe_f(self.v_tsr.get(), 3.5),
                R=safe_f(self.v_R.get(), 0.5),
                chord=safe_f(self.v_chord.get(), 0.06),
                n_blades=int(safe_f(self.v_nb.get(), 3)),
            )

        def on_generated(inp_files: InputFiles, out_dir: str):
            self.v_total.set(inp_files.total or "")
            self.v_shaft.set(inp_files.shaft or "")
            self.v_fx.set(inp_files.fx or "")
            self.v_fy.set(inp_files.fy or "")
            self.v_fx_shaft.set(getattr(inp_files, "fx_shaft", None) or "")
            self.v_fy_shaft.set(getattr(inp_files, "fy_shaft", None) or "")
            for i in range(8):
                b_inp = inp_files.blades[i] if i < len(inp_files.blades) else None
                self.v_blades[i].set(b_inp or "")
                if hasattr(self, "v_fx_blades") and i < len(self.v_fx_blades):
                    fx_b = inp_files.fx_blades[i] if i < len(getattr(inp_files, "fx_blades", [])) else None
                    self.v_fx_blades[i].set(fx_b or "")
                if hasattr(self, "v_fy_blades") and i < len(self.v_fy_blades):
                    fy_b = inp_files.fy_blades[i] if i < len(getattr(inp_files, "fy_blades", [])) else None
                    self.v_fy_blades[i].set(fy_b or "")
            self.v_ignore_diag_errors = False
            self.applied_repairs.clear()
            results_dir = os.path.join(out_dir, "analysis_results")
            self.v_out.set(results_dir)
            self._log(f"Multi-stage simulation files generated and loaded into analyzer from:\n{out_dir}\nOutput folder set to:\n{results_dir}", "ok")

        MultiStageDialog(self, p, on_generated)

    def _browse_output_dir(self):
        d = filedialog.askdirectory(title="Select Output Folder")
        if d:
            self.v_out.set(d)

    # ---- Validation & Gathering ----
    def _gather_params(self) -> Params:
        def parse_float(var: ctk.StringVar, name: str, default=None) -> Optional[float]:
            s = var.get().strip().replace(",", ".")
            if not s:
                return default
            try:
                return float(s)
            except ValueError:
                raise ValueError(f"Invalid numeric value for {name}: '{s}'.")

        def parse_int(var: ctk.StringVar, name: str, default=None) -> Optional[int]:
            s = var.get().strip().replace(",", ".")
            if not s:
                return default
            try:
                return int(float(s))
            except ValueError:
                raise ValueError(f"Invalid integer value for {name}: '{s}'.")

        def parse_float_or(var: ctk.StringVar, name: str, default: float) -> float:
            val = parse_float(var, name, default=None)
            return default if val is None else val

        def parse_int_or(var: ctk.StringVar, name: str, default: int) -> int:
            val = parse_int(var, name, default=None)
            return default if val is None else val

        custom_revs_str = self.v_custom_revs.get().strip() if hasattr(self, "v_custom_revs") else ""
        p = Params(
            U=parse_float(self.v_U, "Free-stream speed U", default=None),
            tsr=parse_float(self.v_tsr, "Tip speed ratio λ", default=None),
            omega=parse_float(self.v_omega, "Rotor speed ω", default=None),
            R=parse_float(self.v_R, "Rotor radius R", default=None),
            chord=parse_float(self.v_chord, "Blade chord c", default=None),
            n_blades=parse_int(self.v_nb, "Number of blades", default=None),
            depth=parse_float_or(self.v_depth, "2D depth reference", 1.0),
            rho=parse_float_or(self.v_rho, "Air density ρ", 1.225),
            mu=parse_float_or(self.v_mu, "Viscosity μ", 1.7894e-5),
            theta0=parse_float_or(self.v_theta0, "Blade 1 start azimuth θ0", 0.0),
            rotation_dir=self.v_rot_dir.get(),
            azimuth_zero=self.v_azimuth_zero.get(),
            azimuth_offset=parse_float_or(self.v_azimuth_offset, "Azimuth offset", 0.0),
            static_stall_deg=parse_float_or(self.v_static_stall, "Static stall angle", 12.0),
            offsets=self.v_offsets.get(),
            moment_sign=self.v_sign.get(),
            stable_tol=parse_float_or(self.v_tol, "Stability tolerance", 5.0),
            n_stable=parse_int_or(self.v_nstable, "Revolutions to report", 2),
            custom_revs=custom_revs_str or None,
            bin_deg=parse_float_or(self.v_bin, "Azimuth bin width", 1.0),
            dt=parse_float(self.v_dt, "Time step dt fallback", default=None),
            write_timeseries=self.v_write_ts.get(),
            preset_name=self.v_preset.get() if self.v_preset.get() not in ("Select Preset...", "No presets") else None
        )

        # Set Case Metadata (Tier 2)
        p.metadata = CaseMetadata(
            mesh_cells=parse_int(self.v_meta_cells, "Mesh cell count", default=None),
            turb_model=self.v_meta_turb.get().strip() or None,
            time_step_s=parse_float(self.v_meta_dt, "Time step size", default=None),
            iter_per_step=parse_int_or(self.v_meta_iter, "Iterations per step", 20),
            residual_target=self.v_meta_res.get().strip() or "1e-5",
            domain_size=self.v_meta_domain.get().strip() or "30R x 20R",
            interface_type=self.v_meta_interface.get().strip() or "Sliding Mesh",
            moment_center_confirmed=self.v_meta_center_ok.get(),
        )

        for fld in ["U", "tsr", "omega", "R", "chord", "n_blades"]:
            if getattr(p, fld, None) is not None:
                p.param_sources[fld] = "typed by user"

        errors = p.validate()
        if errors:
            raise ValueError("\n".join(errors))
        return p

    def _gather_files(self) -> InputFiles:
        total = self.v_total.get().strip() or None
        shaft = self.v_shaft.get().strip() or None
        fx = self.v_fx.get().strip() or None
        fy = self.v_fy.get().strip() or None
        fx_shaft = self.v_fx_shaft.get().strip() if hasattr(self, "v_fx_shaft") else None
        fy_shaft = self.v_fy_shaft.get().strip() if hasattr(self, "v_fy_shaft") else None
        try:
            nb = int(self.v_nb.get())
        except ValueError:
            nb = 3
        nb = max(1, min(8, nb))
        blades = [(self.v_blades[i].get().strip() or None) for i in range(nb)]
        fx_blades = [(self.v_fx_blades[i].get().strip() or None) for i in range(nb)]
        fy_blades = [(self.v_fy_blades[i].get().strip() or None) for i in range(nb)]
        return InputFiles(
            total=total,
            blades=blades,
            shaft=shaft,
            fx=fx,
            fy=fy,
            fx_shaft=fx_shaft or None,
            fy_shaft=fy_shaft or None,
            fx_blades=fx_blades,
            fy_blades=fy_blades,
            prior_source=(self.v_prior_source.get().strip() or None) if hasattr(self, "v_prior_source") else None,
            continuity_mode=self._get_continuity_code() if hasattr(self, "_get_continuity_code") else "auto",
        )

    # ---- Analysis Execution ----
    def _start_analysis(self):
        if self.is_running:
            return

        self.total_warnings = 0
        self.last_error_traceback = ""
        self.footer_warn_lbl.configure(text="⚠️ Warnings: 0")

        try:
            p = self._gather_params()
            f = self._gather_files()
        except ValueError as exc:
            messagebox.showerror("Parameter Validation Error", str(exc))
            return

        # Check required files
        have_blades = all(b is not None for b in f.blades) and len(f.blades) > 0
        if not f.total and not have_blades:
            messagebox.showwarning(
                "Files Required",
                "Please provide either the total rotor moment file, or individual moment files for all blades."
            )
            return

        # Part A.2: File-health diagnostics run before any analysis
        if hasattr(self, "diagnostics_frame"):
            diag_rep = self.diagnostics_frame.run_diagnostics()
            if diag_rep.has_errors and not self.v_ignore_diag_errors:
                n_err = diag_rep.summary_counts.get("ERROR", 0)
                proceed = messagebox.askyesno(
                    "Critical Diagnostic Errors Detected",
                    f"File-Health Diagnostics detected {n_err} critical ERROR(s) in your monitor files "
                    f"(e.g., missing data columns, zero overlap, or non-numeric tokens).\n\n"
                    f"Running the analysis with these errors will produce invalid or erroneous results.\n\n"
                    f"Would you like to IGNORE the diagnostic errors and run anyway?\n\n"
                    f"• Click 'Yes' to ignore errors and proceed (this choice will be recorded in reports).\n"
                    f"• Click 'No' to review findings in the 'File Diagnostics' tab."
                )
                if not proceed:
                    self._switch_page("input")
                    return
                else:
                    self.v_ignore_diag_errors = True
                    p.ignore_diagnostic_errors = True
                    if "User explicitly ignored diagnostic errors" not in self.applied_repairs:
                        self.applied_repairs.append("User explicitly ignored diagnostic errors")

        p.applied_repairs = list(self.applied_repairs)
        p.ignore_diagnostic_errors = self.v_ignore_diag_errors

        out_dir = self.v_out.get().strip()
        prefix = self.v_prefix.get().strip() or "VAWT_analysis"

        # Persist last session parameters automatically!
        self.config_mgr.save_last_session(p, f, out_dir, prefix)

        # UI state transition
        self.is_running = True
        self.cancel_event = threading.Event()
        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_dir_btn.configure(state="disabled")
        self.footer_progress.start()
        self.status_lbl.configure(text="Processing simulation data...")
        self.footer_status_lbl.configure(text="● Running Analysis...", text_color="#3B82F6")

        self.log_text.delete("1.0", "end")
        self._log(f"Starting VAWT Post-Processing: {p.n_blades}-blade turbine (R={p.R:g}m, c={p.chord:g}m, U={p.U:g}m/s)...")

        # Launch worker thread
        def worker():
            try:
                res = run_pipeline(
                    p, f, out_dir, prefix,
                    log=lambda m: self.msg_queue.put(("log", m)),
                    cancel_token=self.cancel_event
                )
                self.msg_queue.put(("done", res))
            except InterruptedError as exc:
                self.msg_queue.put(("cancelled", str(exc)))
            except Exception as exc:
                self.msg_queue.put(("error", f"{exc}\n{traceback.format_exc()}"))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_analysis(self):
        if self.is_running and self.cancel_event:
            self.cancel_event.set()
            self.status_lbl.configure(text="Cancelling analysis...")
            self.footer_status_lbl.configure(text="● Cancelling...", text_color="#F59E0B")
            self._log("User requested analysis cancellation. Terminating worker pipeline between stages...", "warn")

    def _save_diagnostics(self):
        """Save a comprehensive diagnostic report containing environment info, parameters, hashes, and file headers."""
        import platform
        import datetime as dt_mod
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"vawt_diagnostics_{dt_mod.datetime.now():%Y%m%d_%H%M%S}.txt",
            filetypes=[("Text Report", "*.txt"), ("All Files", "*.*")]
        )
        if not path:
            return
        try:
            lines: List[str] = []
            lines.append("=" * 70)
            lines.append(f"{APP_TITLE} v{APP_VERSION} - System & Execution Diagnostics")
            lines.append("=" * 70)
            lines.append(f"Timestamp: {dt_mod.datetime.now().isoformat()}")
            lines.append(f"OS / Platform: {platform.platform()} ({platform.architecture()[0]})")
            lines.append(f"Python: {platform.python_version()} ({sys.executable})")

            import numpy as np  # type: ignore
            import openpyxl  # type: ignore
            import matplotlib  # type: ignore
            lines.append(f"NumPy: {np.__version__}")
            lines.append(f"OpenPyXL: {openpyxl.__version__}")
            lines.append(f"Matplotlib: {matplotlib.__version__}")
            lines.append("-" * 70)

            # Active parameters
            try:
                p = self._gather_params()
                lines.append("\n[Active Turbine Parameters]")
                for k, v in p.__dict__.items():
                    if k != "metadata":
                        lines.append(f"  {k}: {v}")
                lines.append("\n[CFD Case Metadata]")
                for k, v in p.metadata.__dict__.items():
                    lines.append(f"  {k}: {v}")
            except Exception as e:
                lines.append(f"\n[Parameters Parse Error]: {e}")

            # Input files and headers
            f = self._gather_files()
            lines.append("\n[Input Monitor Files & Checksums]")
            f_targets = [("total", f.total), ("shaft", f.shaft), ("fx", f.fx), ("fy", f.fy)]
            for i, b in enumerate(f.blades):
                if b:
                    f_targets.append((f"blade_{i+1}", b))

            for role, fpath in f_targets:
                if fpath and os.path.isfile(fpath):
                    sha = compute_file_sha256(fpath)
                    sz = os.path.getsize(fpath)
                    lines.append(f"\n--- {role.upper()}: {fpath} ({sz} bytes, SHA-256: {sha}) ---")
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                            all_lines = fh.readlines()
                        lines.append("First 10 lines:")
                        for l in all_lines[:10]:
                            lines.append("  " + l.rstrip())
                        if len(all_lines) > 10:
                            lines.append(f"... ({len(all_lines) - 15} lines omitted) ...")
                            lines.append("Last 5 lines:")
                            for l in all_lines[-5:]:
                                lines.append("  " + l.rstrip())
                    except Exception as fe:
                        lines.append(f"  Error reading file: {fe}")
                else:
                    lines.append(f"  {role}: Not provided or not found")

            # Last result QA checks if available
            if self.current_result and "qa_checks" in self.current_result:
                lines.append("\n[Latest QA Checks]")
                for q in self.current_result["qa_checks"]:
                    lines.append(f"  [{q['status']}] {q['check']}: {q['value']} -- {q['detail']}")

            # Last error traceback if any
            if self.last_error_traceback:
                lines.append("\n[Last Execution Traceback / Error]")
                lines.append(self.last_error_traceback)

            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))

            self._log(f"Diagnostics report saved to: {path}", "ok")
            messagebox.showinfo("Diagnostics Saved", f"Diagnostic report successfully written to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Diagnostics Failed", f"Could not write diagnostics: {exc}")

    def _poll_queue(self):
        if getattr(self, "_is_closing", False):
            return
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    tag = "warn" if payload.startswith("WARNING") else None
                    self._log(payload, tag)
                elif kind == "cancelled":
                    self.is_running = False
                    self.footer_progress.stop()
                    self.run_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.status_lbl.configure(text="Analysis Cancelled")
                    self.footer_status_lbl.configure(text="● Analysis Cancelled", text_color="#F59E0B")
                    self._log(f"CANCELLED: {payload}", "warn")
                    messagebox.showinfo("Analysis Cancelled", "Post-processing pipeline was safely halted.")
                elif kind == "error":
                    self.is_running = False
                    self.footer_progress.stop()
                    self.run_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    first_err = payload.strip().split("\n")[0]
                    fail_msg = f"Analysis Failed: {first_err}"
                    self.status_lbl.configure(text=fail_msg)
                    self.footer_status_lbl.configure(text=f"● {fail_msg}", text_color="#EF4444")
                    self.last_error_traceback = payload
                    self._log(f"ERROR: {payload}", "err")
                    messagebox.showerror("Post-Processing Failed", first_err)
                elif kind == "done":
                    self.is_running = False
                    self.footer_progress.stop()
                    self.run_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.open_dir_btn.configure(state="normal")
                    self.status_lbl.configure(text="Analysis Complete")
                    self.footer_status_lbl.configure(text="● Analysis Complete", text_color="#10B981")

                    R = payload
                    res = R["res"]
                    self.current_result = R
                    self.output_folder = os.path.dirname(R["xlsx"])

                    # Log summary
                    self._log("\n" + "=" * 60)
                    self._log("SUCCESS: Analysis Completed!", "ok")
                    if len(res.get("phases", [])) > 1:
                        self.status_lbl.configure(text=f"Analysis Complete • {len(res['phases'])} Stages")
                        self._log(f"Multi-Stage Simulation Auto-Detected ({len(res['phases'])} phases):", "ok")
                        for ph in res["phases"]:
                            self._log(
                                f"   • Stage {ph.get('phase_index', 1)}: Steps {ph['step_from']}–{ph['step_to']} "
                                f"({ph.get('n_steps', ph['step_to'] - ph['step_from'])} steps, {ph.get('revolutions', 0):.2f} revs) | "
                                f"Δt = {ph['dt']:.6e} s | Δθ = {ph['deg_per_step']:.3f}°/step ({ph['steps_per_rev']:.1f} steps/rev)",
                                "ok"
                            )
                    self._log(f"Reported Revolutions: {', '.join(str(r['k']) for r in R['sel'])}", "ok")
                    self._log(f"Mean Cp (reported): {res['cp_mean']:.4f}  |  Mean Cm: {res['cm_mean']:.4f}", "ok")
                    if res.get("stable_n", 0) > 0 and "cp_stable_mean" in res:
                        stable_k_str = ", ".join(f"rev {k}" for k in res.get("stable_revs", []))
                        self._log(f"Mean Cp (trailing {res['stable_n']} stable revs: {stable_k_str}): {res['cp_stable_mean']:.4f}", "ok")
                    if "cp_all_mean" in res:
                        self._log(f"Mean Cp (whole analysis, all {res['n_full']} revs): {res['cp_all_mean']:.4f}", "info")
                    self._log(f"Status: {'CONVERGED / STABLE' if res['is_stable'] else 'NOT YET STABLE'}", "ok" if res["is_stable"] else "warn")
                    self._log(f"Excel Workbook: {R['xlsx']}", "ok")
                    self._log("=" * 60 + "\n")

                    # Update Results and Preview
                    self.results_frame.set_results(R)
                    self.preview_frame.set_figures(R["figs"], "03_cp_vs_azimuth.png")
                    self.has_new_results = True
                    self._update_tab_badges()

                    # Auto-open Results page if requested (never navigate away if currently on Live Monitor)
                    if self.v_auto_open_results.get() and self.current_page != "live":
                        self._switch_page("results")
        except queue.Empty:
            pass
        except Exception as exc:
            self._log(f"Queue poll warning: {exc}", "warn")
        finally:
            if not getattr(self, "_is_closing", False):
                self.after(100, self._poll_queue)

    def _log(self, msg: str, tag: Optional[str] = None):
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")

        if tag == "warn" or "WARNING" in msg:
            self.total_warnings += 1
            self.footer_warn_lbl.configure(text=f"⚠️ Warnings: {self.total_warnings}")

        first_line = msg.strip().split("\n")[0]
        if first_line:
            if len(first_line) > 65:
                first_line = first_line[:62] + "..."
            self.footer_log_btn.configure(text=f"📋 {first_line}")

    def _open_output_dir(self):
        folder = self.output_folder or self.v_out.get().strip()
        if folder and os.path.isdir(folder):
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        else:
            messagebox.showinfo("Output Folder", f"Output folder does not exist yet:\n{folder}")

    def _cleanup_before_close(self):
        """Stop all background workers, Qt timers, and Matplotlib figures before exiting."""
        if getattr(self, "_cleanup_done", False):
            return
        self._is_closing = True
        self._cleanup_done = True

        # Signal any active analysis thread to cancel cleanly
        if hasattr(self, "cancel_event") and self.cancel_event is not None:
            try:
                self.cancel_event.set()
            except Exception:
                pass

        # Stop Live Monitor background thread and periodic timers
        if hasattr(self, "live_monitor_frame") and self.live_monitor_frame is not None:
            try:
                self.live_monitor_frame.shutdown()
            except Exception:
                pass

        # Release Results interactive Matplotlib canvas
        if hasattr(self, "results_frame") and self.results_frame is not None:
            try:
                self.results_frame.shutdown()
            except Exception:
                pass

        # Release Workbench & GCI Matplotlib canvas
        if hasattr(self, "workbench_frame") and hasattr(self.workbench_frame, "shutdown"):
            try:
                self.workbench_frame.shutdown()
            except Exception:
                pass

        # Halt every pending QTimer anywhere under this window
        try:
            from PySide6.QtCore import QTimer
            for qt in self.findChildren(QTimer):
                try:
                    qt.stop()
                except Exception:
                    pass
        except Exception:
            pass

        # Close open Matplotlib figures cleanly
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            pass

        try:
            self.config_mgr.set_window_geometry(self.geometry() or "")
            self.config_mgr.set_last_page(self.current_page)
        except Exception:
            pass

    def _on_close(self) -> bool:
        if self.is_running:
            if not messagebox.askyesno(
                "Exit Confirmation",
                "An analysis is currently running in the background. Do you really want to exit?",
            ):
                return False
        self._cleanup_before_close()
        if not getattr(self, "_in_close_event", False):
            self.destroy()
        return True


def launch_gui():
    """Launch the TurbineScope Qt 6 GUI with the startup loading screen."""
    from vawt_post.ui.splash import launch_with_splash
    launch_with_splash()


if __name__ == "__main__":
    launch_gui()
