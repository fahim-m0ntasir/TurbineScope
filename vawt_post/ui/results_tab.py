# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Dedicated Results & Publication Visualizer Page (v2.0)
================================================================================
Comprehensive results workspace providing:
1. Summary: Executive KPI Ribbon, Per-Revolution Loads Table, CFD QA Matrix.
2. Graph Preview: Full-height interactive publication viewer (Single, Dual Compare, Gallery Grid).
3. Execution Log: Full-height colored console log with search and export.
4. File Health Report: Diagnostic findings and token inspection for the run.
5. Output Files: Direct-access catalog of generated Excel workbook, figures, and reports.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import matplotlib as mpl
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.image import AxesImage
from matplotlib.patches import Rectangle
from vawt_post.engine.plots import build_result_figure
from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import FigureCanvasTkAgg, filedialog, messagebox, ttk, ImageTk
from PIL import Image

FIGURE_TITLES = {
    "01_cp_history.png": "01. Power Coefficient (Cp) History",
    "02_cp_per_revolution.png": "02. Revolution-Averaged Cp Bar Chart",
    "03_cp_vs_azimuth.png": "03. Rotor Cp vs Azimuth (Phase-Averaged)",
    "04_blade_torque_vs_azimuth.png": "04. Per-Blade Torque vs Own Azimuth",
    "05_polar_blade_cp.png": "05. Polar Blade Cp Distribution",
    "06_ct_cy.png": "06. Thrust (Ct) & Side Force (Cy) History",
    "07_cm.png": "07. Moment Coefficient (Cm)",
    "08_alpha_w_re.png": "08. Kinematics (AoA, Velocity Ratio, Re_c)",
    "09_cp_spectrum.png": "09. Cp Ripple Spectrum & Blade Harmonics",
    "10_convergence.png": "10. Revolution Convergence & Blade Spread",
    "11_cp_heatmap.png": "11. Revolution × Azimuth Cp Heatmap",
    "12_dashboard.png": "12. Key Simulation KPI Dashboard",
    "13_power_contribution_quartiles.png": "13. Power Contribution by Azimuthal Quartile",
    "14_cumulative_moment_distribution.png": "14. Cumulative Moment Distribution",
    "15_blade_tangential_load.png": "15. Instantaneous Blade Torque (N·m)",
    "16_geometric_kinematics.png": "16. Geometric Kinematics vs Azimuth",
    "17_global_rotor_coefficients.png": "17. Global Rotor Performance Coefficients (Cp, Cm, Ct, Cy)",
    "18_cp_convergence_history.png": "18. Cp Convergence History & Stability",
}

ZOOM_LEVELS = ["Fit", "50%", "75%", "100%", "125%", "150%", "200%"]
PREVIEW_MODES = ["🖼️ Single Figure", "👥 Side-by-Side Dual", "🎨 All Figures Gallery"]
SUB_TABS = ["📊 Summary", "📈 Graph Preview", "📋 Execution Log", "🛡️ File Health", "📁 Output Files"]


class ResultsFrame(ctk.CTkFrame):
    """Dedicated full-height Results page presenting all post-processing outputs."""

    def __init__(self, master, app=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.current_result: Optional[Dict[str, Any]] = None
        self.figs: Dict[str, str] = {}
        self.current_fig_key: Optional[str] = None
        self.current_pil_img: Optional[Image.Image] = None
        self.current_tk_img: Optional[ImageTk.PhotoImage] = None
        self.dual_left_tk: Optional[ctk.CTkImage] = None
        self.dual_right_tk: Optional[ctk.CTkImage] = None
        self.current_img_tk: Optional[ctk.CTkImage] = None
        self.gallery_tk_imgs: List[ctk.CTkImage] = []
        self.gallery_tks: List[ctk.CTkImage] = self.gallery_tk_imgs

        self.zoom_mode: str = "Fit"
        self.preview_mode: str = "🖼️ Single Figure"
        self.active_sub_tab: str = "📊 Summary"
        self._last_dims = (0, 0)
        self._img_cache: Dict[str, Image.Image] = {}
        self._rendered_single_state: Any = None
        self._resize_after_id: Optional[str] = None
        self._dual_resize_after_id: Optional[str] = None
        self._gallery_built_keys: Optional[tuple] = None
        self._outputs_built_key: Optional[tuple] = None
        self._raw_log_text: Optional[str] = None

        # Interactive Matplotlib Graph Preview state
        self.interact_tool: str = "Probe"
        self._probe_enabled: bool = True
        self._interactive_active: bool = False
        self._rendered_interactive_key: Optional[Tuple[Any, ...]] = None
        self._home_limits: Dict[Any, Tuple[Tuple[float, float], Tuple[float, float]]] = {}
        self._view_history: List[List[Tuple[Any, Tuple[float, float], Tuple[float, float]]]] = []
        self._drag_state: Optional[Dict[str, Any]] = None
        self._box_patch: Any = None
        self._probe_markers: Dict[Any, Tuple[Any, Any, Any]] = {}
        self._pin_a: Optional[Dict[str, Any]] = None
        self._pin_b: Optional[Dict[str, Any]] = None
        self._pin_next: str = "A"
        self._pin_artists: List[Any] = []
        self._last_hover_sig: Any = None
        self._last_hover_ts: float = 0.0
        self._blit_bg: Any = None
        self._layout_frozen: bool = False
        self._redraw_after_id: Optional[str] = None
        self._last_pan_draw_ts: float = 0.0
        self._probe_artists: Dict[Any, Tuple[Any, Any, Any]] = {}
        self._active_probe_ax: Any = None
        self._probe_readout_cache: Dict[str, Any] = {}

        self._build_ui()

    def _build_ui(self):
        # 1. Top Navigation Sub-Tabs Bar
        self.sub_tab_bar = ctk.CTkFrame(self, height=44, corner_radius=8, fg_color=("gray90", "gray17"))
        self.sub_tab_bar.pack(fill="x", padx=10, pady=(6, 6))

        self.sub_seg_btn = ctk.CTkSegmentedButton(
            self.sub_tab_bar,
            values=SUB_TABS,
            height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_sub_tab_change
        )
        self.sub_seg_btn.set("📊 Summary")
        self.sub_seg_btn.pack(side="left", padx=8, pady=5)

        # Quick Results Folder Button on right of sub-tab bar
        self.btn_open_folder = ctk.CTkButton(
            self.sub_tab_bar,
            text="📁 Results Folder",
            width=120,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._open_results_folder
        )
        self.btn_open_folder.pack(side="right", padx=8, pady=5)

        self.btn_open_excel = ctk.CTkButton(
            self.sub_tab_bar,
            text="📊 Open Excel",
            width=105,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("teal", "#0D9488"),
            hover_color=("dark cyan", "#0F766E"),
            command=self._open_excel_file
        )
        self.btn_open_excel.pack(side="right", padx=(0, 4), pady=5)

        # 2. Body Container for the 5 Sub-Pages
        self.content_container = ctk.CTkFrame(self, fg_color="transparent")
        self.content_container.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.content_container.grid_rowconfigure(0, weight=1)
        self.content_container.grid_columnconfigure(0, weight=1)

        # Build all 5 sub-pages once and pre-warm their grid layout
        self._build_summary_subpage()
        self._build_preview_subpage()
        self._build_log_subpage()
        self._build_health_subpage()
        self._build_outputs_subpage()

        for sp in (self.page_summary, self.page_preview, self.page_log, self.page_health, self.page_outputs):
            sp.grid(row=0, column=0, sticky="nsew")
            sp.grid_remove()

        # Show initial subpage
        self._show_subpage("📊 Summary", force=True)

    # -------------------------------------------------------------------------
    # Sub-Page 1: Summary (KPI Ribbon + Tables)
    # -------------------------------------------------------------------------
    def _build_summary_subpage(self):
        self.page_summary = ctk.CTkFrame(self.content_container, fg_color=("gray92", "gray14"), corner_radius=0)

        # Top Executive KPI Ribbon
        self.kpi_ribbon = ctk.CTkFrame(self.page_summary, corner_radius=10, fg_color=("gray85", "gray17"))
        self.kpi_ribbon.pack(fill="x", pady=(0, 8))

        cards_box = ctk.CTkFrame(self.kpi_ribbon, fg_color="transparent")
        cards_box.pack(fill="x", expand=True, padx=8, pady=8)

        self.card_cp = self._create_kpi_card(cards_box, "POWER COEFFICIENT (Cp)", "--", "Reported Revs")
        self.card_cp.pack(side="left", padx=6, fill="y", expand=True)

        self.card_cp_stable = self._create_kpi_card(cards_box, "STABLE Cp", "--", "Trailing Stable")
        self.card_cp_stable.pack(side="left", padx=6, fill="y", expand=True)

        self.card_cm_ct = self._create_kpi_card(cards_box, "MEAN Cm / Ct", "--", "Cm / Ct")
        self.card_cm_ct.pack(side="left", padx=6, fill="y", expand=True)

        self.card_power = self._create_kpi_card(cards_box, "AERO POWER", "--", "P = ½ρU³A·Cp")
        self.card_power.pack(side="left", padx=6, fill="y", expand=True)

        self.card_status = self._create_kpi_card(cards_box, "CONVERGENCE", "NOT RUN", "Status")
        self.card_status.pack(side="left", padx=6, fill="y", expand=True)

        # Tables Container (Split Top: Revolutions, Bottom: QA Matrix)
        tables_body = ctk.CTkFrame(self.page_summary, fg_color="transparent")
        tables_body.pack(fill="both", expand=True)

        # Revolutions table
        rev_box = ctk.CTkFrame(tables_body, corner_radius=8)
        rev_box.pack(fill="both", expand=True, pady=(0, 6))

        rev_hdr = ctk.CTkFrame(rev_box, fg_color="transparent")
        rev_hdr.pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkLabel(rev_hdr, text="📊 Per-Revolution Convergence & Aerodynamic Loads", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        ctk.CTkButton(
            rev_hdr,
            text="📋 Copy Summary",
            width=110,
            height=24,
            font=ctk.CTkFont(size=10),
            command=self._copy_summary
        ).pack(side="right")

        cols_rev = ("Rev", "Flow Time (s)", "Mean Cp", "ΔCp Rel", "ΔCp Abs", "Mean Cm", "Mean Ct", "Spread", "Status")
        self.tree_rev = ttk.Treeview(rev_box, columns=cols_rev, show="headings", height=6)
        widths = {"Rev": 50, "Flow Time (s)": 140, "Mean Cp": 90, "ΔCp Rel": 90, "ΔCp Abs": 90, "Mean Cm": 90, "Mean Ct": 90, "Spread": 90, "Status": 100}
        for c in cols_rev:
            self.tree_rev.heading(c, text=c)
            self.tree_rev.column(c, width=widths.get(c, 90), anchor="center")

        sb_rev = ttk.Scrollbar(rev_box, orient="vertical", command=self.tree_rev.yview)
        self.tree_rev.configure(yscrollcommand=sb_rev.set)
        self.tree_rev.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        sb_rev.pack(side="right", fill="y", pady=(0, 6), padx=(0, 6))

        # QA Checks table
        qa_box = ctk.CTkFrame(tables_body, corner_radius=8)
        qa_box.pack(fill="both", expand=True)

        ctk.CTkLabel(qa_box, text="🛡️ CFD Quality Assurance & Integrity Verification Matrix", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=8, pady=(6, 4))

        cols_qa = ("Status", "Check Name", "Value", "Detailed Explanation")
        self.tree_qa = ttk.Treeview(qa_box, columns=cols_qa, show="headings", height=5)
        widths_qa = {"Status": 80, "Check Name": 180, "Value": 150, "Detailed Explanation": 450}
        for c in cols_qa:
            self.tree_qa.heading(c, text=c)
            self.tree_qa.column(c, width=widths_qa.get(c, 100), anchor="w")
        self.tree_qa.column("Status", anchor="center")

        sb_qa = ttk.Scrollbar(qa_box, orient="vertical", command=self.tree_qa.yview)
        self.tree_qa.configure(yscrollcommand=sb_qa.set)
        self.tree_qa.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        sb_qa.pack(side="right", fill="y", pady=(0, 6), padx=(0, 6))

    def _create_kpi_card(self, parent, title: str, value: str, sub: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=8, fg_color=("gray92", "gray22"), width=155, height=68)
        card.pack_propagate(False)

        lbl_t = ctk.CTkLabel(card, text=title, height=15, font=ctk.CTkFont(size=9, weight="bold"), text_color="gray")
        lbl_t.pack(anchor="w", padx=8, pady=(5, 0))

        lbl_v = ctk.CTkLabel(card, text=value, height=22, font=ctk.CTkFont(size=16, weight="bold"))
        lbl_v.pack(anchor="w", padx=8, pady=(1, 1))

        lbl_s = ctk.CTkLabel(card, text=sub, height=14, font=ctk.CTkFont(size=9), text_color="gray")
        lbl_s.pack(anchor="w", padx=8, pady=(0, 4))

        card.lbl_val = lbl_v  # type: ignore[attr-defined]
        card.lbl_sub = lbl_s  # type: ignore[attr-defined]
        return card

    # -------------------------------------------------------------------------
    # Sub-Page 2: Graph Preview (Full Height Viewer: Single / Dual / Gallery)
    # -------------------------------------------------------------------------
    def _build_preview_subpage(self):
        self.page_preview = ctk.CTkFrame(self.content_container, fg_color=("gray92", "gray14"), corner_radius=0)

        # Top Toolbar (2 clean rows so Save As / Copy never clip on 1280x720)
        bar = ctk.CTkFrame(self.page_preview, corner_radius=8, fg_color=("gray90", "gray17"))
        bar.pack(fill="x", pady=(0, 6))

        bar_row1 = ctk.CTkFrame(bar, fg_color="transparent")
        bar_row1.pack(fill="x", padx=6, pady=(5, 3))

        # View mode selector
        self.preview_mode_var = ctk.StringVar(value="🖼️ Single Figure")
        self.mode_selector = ctk.CTkSegmentedButton(
            bar_row1,
            values=PREVIEW_MODES,
            variable=self.preview_mode_var,
            command=self._on_preview_mode_change
        )
        self.mode_selector.pack(side="left", padx=2)

        # Action Buttons on right of row 1
        ctk.CTkButton(
            bar_row1,
            text="💾 Save As",
            width=86,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._save_image_as
        ).pack(side="right", padx=(2, 2))

        ctk.CTkButton(
            bar_row1,
            text="📋 Copy",
            width=72,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._copy_image
        ).pack(side="right", padx=2)

        ctk.CTkButton(
            bar_row1,
            text="🔍 Full Size",
            width=88,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._open_full_size
        ).pack(side="right", padx=2)

        # Single figure controls container (Row 2)
        self.single_controls_box = ctk.CTkFrame(bar, fg_color="transparent")
        self.single_controls_box.pack(fill="x", padx=6, pady=(0, 5))

        ctk.CTkButton(self.single_controls_box, text="◀", width=28, height=26, command=self._prev_figure).pack(side="left", padx=2)

        self.fig_var = ctk.StringVar(value="Select Figure...")
        self.fig_menu = ctk.CTkOptionMenu(
            self.single_controls_box,
            variable=self.fig_var,
            values=list(FIGURE_TITLES.values()),
            width=260,
            command=self._on_select_figure_title
        )
        self.fig_menu.pack(side="left", padx=2)

        ctk.CTkButton(self.single_controls_box, text="▶", width=28, height=26, command=self._next_figure).pack(side="left", padx=2)

        ctk.CTkLabel(self.single_controls_box, text="Zoom:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 2))
        self.zoom_menu = ctk.CTkOptionMenu(
            self.single_controls_box,
            values=ZOOM_LEVELS,
            width=72,
            command=self._on_zoom_change
        )
        self.zoom_menu.set("Fit")
        self.zoom_menu.pack(side="left", padx=2)

        self.interact_tool_var = ctk.StringVar(value="🎯 Probe")
        self.interact_tool_seg = ctk.CTkSegmentedButton(
            self.single_controls_box,
            values=["🎯 Probe", "🔍 Box Zoom", "✋ Pan"],
            variable=self.interact_tool_var,
            height=26,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_interact_tool_change,
        )
        self.interact_tool_seg.pack(side="left", padx=(8, 4))

        self.btn_undo_view = ctk.CTkButton(
            self.single_controls_box,
            text="↩ Undo",
            width=58,
            height=26,
            fg_color="transparent",
            border_width=1,
            font=ctk.CTkFont(size=10),
            command=self._undo_interactive_view,
        )
        self.btn_undo_view.pack(side="left", padx=2)

        self.btn_reset_view = ctk.CTkButton(
            self.single_controls_box,
            text="🏠 Reset",
            width=62,
            height=26,
            fg_color="transparent",
            border_width=1,
            font=ctk.CTkFont(size=10),
            command=self._reset_interactive_view,
        )
        self.btn_reset_view.pack(side="left", padx=2)

        # Main Graphic Viewer Area (Three view containers)
        self.preview_viewer_container = ctk.CTkFrame(self.page_preview, corner_radius=8, fg_color=("gray95", "gray12"))
        self.preview_viewer_container.pack(fill="both", expand=True)

        # 1. Single View (Interactive Qt6 Matplotlib Canvas + Probe Sidebar, with Static PIL Fallback)
        self.single_view_frame = ctk.CTkFrame(self.preview_viewer_container, fg_color="transparent")
        self.canvas = ctk.CTkCanvas(self.single_view_frame, bg=("#F3F4F6", "#1E1E1E"), highlightthickness=0)
        self.vsb = ttk.Scrollbar(self.single_view_frame, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self.single_view_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hsb.set, yscrollcommand=self.vsb.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.single_view_frame.grid_rowconfigure(0, weight=1)
        self.single_view_frame.grid_columnconfigure(0, weight=1)

        self.canvas_placeholder_text = self.canvas.create_text(
            450, 260,
            text="Run an analysis or select a figure to inspect in high resolution.",
            fill="gray",
            font=("Segoe UI", 13)
        )
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._build_interactive_single_viewer()

        # 2. Side-by-Side Dual Compare View
        self.dual_view_frame = ctk.CTkFrame(self.preview_viewer_container, fg_color="transparent")
        dual_top = ctk.CTkFrame(self.dual_view_frame, fg_color="transparent")
        dual_top.pack(fill="x", padx=8, pady=4)

        self.dual_left_var = ctk.StringVar(value="01. Power Coefficient (Cp) History")
        ctk.CTkOptionMenu(dual_top, variable=self.dual_left_var, values=list(FIGURE_TITLES.values()), width=240, command=lambda _: self._render_dual()).pack(side="left", padx=4)

        ctk.CTkButton(dual_top, text="⇄ Swap", width=60, height=26, command=self._swap_dual_figures).pack(side="left", padx=6)

        self.dual_right_var = ctk.StringVar(value="03. Rotor Cp vs Azimuth (Phase-Averaged)")
        ctk.CTkOptionMenu(dual_top, variable=self.dual_right_var, values=list(FIGURE_TITLES.values()), width=240, command=lambda _: self._render_dual()).pack(side="left", padx=4)

        self.dual_container = ctk.CTkFrame(self.dual_view_frame, fg_color="transparent")
        self.dual_container.pack(fill="both", expand=True, padx=4, pady=4)
        self.dual_container.columnconfigure(0, weight=1)
        self.dual_container.columnconfigure(1, weight=1)
        self.dual_container.rowconfigure(0, weight=1)

        self.dual_left_label = ctk.CTkLabel(self.dual_container, text="", fg_color=("gray90", "gray18"), corner_radius=6)
        self.dual_left_label.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")

        self.dual_right_label = ctk.CTkLabel(self.dual_container, text="", fg_color=("gray90", "gray18"), corner_radius=6)
        self.dual_right_label.grid(row=0, column=1, padx=4, pady=4, sticky="nsew")
        self.dual_container.bind("<Configure>", self._on_dual_configure)

        # 3. All Figures Gallery Grid
        self.gallery_frame = ctk.CTkScrollableFrame(self.preview_viewer_container, fg_color="transparent")

        # Initially pack Single View
        self.single_view_frame.pack(fill="both", expand=True)

    def _build_interactive_single_viewer(self):
        """Construct embedded Qt6 Matplotlib canvas + point-only Graph Data Probe sidebar."""
        self.interactive_wrap = ctk.CTkFrame(self.single_view_frame, fg_color="transparent")
        self.interactive_wrap.grid_rowconfigure(0, weight=1)
        self.interactive_wrap.grid_columnconfigure(0, weight=1)
        self.interactive_wrap.grid_columnconfigure(1, weight=0)

        left_col = ctk.CTkFrame(self.interactive_wrap, fg_color="#141414", corner_radius=6, border_width=1, border_color="#263042")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(4, 6), pady=4)
        left_col.grid_rowconfigure(0, weight=1)
        left_col.grid_rowconfigure(1, weight=0)
        left_col.grid_columnconfigure(0, weight=1)

        self.res_fig = Figure(figsize=(8.5, 4.8), dpi=96)
        self.res_fig.set_facecolor("#141414")
        self.res_mpl_canvas = FigureCanvasTkAgg(self.res_fig, master=left_col)
        self.res_mpl_widget = self.res_mpl_canvas.get_tk_widget()
        self.res_mpl_widget.configure(bg="#141414")
        self.res_mpl_widget.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 0))

        self.lbl_interact_hint = ctk.CTkLabel(
            left_col,
            text="Wheel: Zoom  |  Drag (Box/Pan/Right/Mid): Zoom or Pan  |  Click: Pin A/B  |  Dbl-Click: Reset",
            font=ctk.CTkFont(size=10),
            text_color="#64748B",
            wraplength=760,
        )
        self.lbl_interact_hint.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 3))

        # Bind Matplotlib interaction + blit cache events cleanly
        self.res_mpl_canvas.mpl_connect("draw_event", self._on_mpl_draw)
        self.res_mpl_canvas.mpl_connect("resize_event", self._on_mpl_resize)
        self.res_mpl_canvas.mpl_connect("scroll_event", self._on_mpl_scroll)
        self.res_mpl_canvas.mpl_connect("button_press_event", self._on_mpl_press)
        self.res_mpl_canvas.mpl_connect("motion_notify_event", self._on_mpl_motion)
        self.res_mpl_canvas.mpl_connect("button_release_event", self._on_mpl_release)
        self.res_mpl_canvas.mpl_connect("figure_leave_event", self._on_mpl_leave)

        # Right Sidebar: Graph Data Probe + Pinned A/B Compare
        self.probe_sidebar = ctk.CTkFrame(
            self.interactive_wrap,
            width=246,
            corner_radius=8,
            fg_color="#181C24",
            border_width=1,
            border_color="#283242",
        )
        self.probe_sidebar.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)
        self.probe_sidebar.grid_propagate(False)

        hdr = ctk.CTkFrame(self.probe_sidebar, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(
            hdr,
            text="GRAPH DATA PROBE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#94A3B8",
        ).pack(side="left")
        self.btn_probe_toggle = ctk.CTkButton(
            hdr,
            text="Probe: ON",
            width=74,
            height=22,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=self._toggle_probe,
        )
        self.btn_probe_toggle.pack(side="right")

        self.lbl_probe_status = ctk.CTkLabel(
            self.probe_sidebar,
            text="Hover over any curve to inspect",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38BDF8",
            anchor="w",
            wraplength=220,
            justify="left",
        )
        self.lbl_probe_status.pack(fill="x", padx=10, pady=(0, 8))

        live_card = ctk.CTkFrame(self.probe_sidebar, fg_color="#11161E", corner_radius=6, border_width=1, border_color="#253042")
        live_card.pack(fill="x", padx=10, pady=(0, 10))
        self.lbl_probe_series = self._make_kv_row(live_card, "Series", "—")
        self.lbl_probe_x = self._make_kv_row(live_card, "X Coord", "—")
        self.lbl_probe_y = self._make_kv_row(live_card, "Y Value", "—")
        self.lbl_probe_extra = self._make_kv_row(live_card, "Context", "—")

        cmp_hdr = ctk.CTkFrame(self.probe_sidebar, fg_color="transparent")
        cmp_hdr.pack(fill="x", padx=10, pady=(4, 4))
        ctk.CTkLabel(
            cmp_hdr,
            text="PINNED COMPARE (A → B)",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94A3B8",
        ).pack(side="left")

        pin_card = ctk.CTkFrame(self.probe_sidebar, fg_color="#11161E", corner_radius=6, border_width=1, border_color="#253042")
        pin_card.pack(fill="x", padx=10, pady=(0, 8))
        self.lbl_pin_a = self._make_kv_row(pin_card, "Pin A", "Click point")
        self.lbl_pin_b = self._make_kv_row(pin_card, "Pin B", "Click point")
        self.lbl_pin_delta_x = self._make_kv_row(pin_card, "ΔX", "—")
        self.lbl_pin_delta_y = self._make_kv_row(pin_card, "ΔY", "—")

        self.btn_clear_pins = ctk.CTkButton(
            self.probe_sidebar,
            text="Clear Pins (A / B)",
            height=26,
            font=ctk.CTkFont(size=10),
            fg_color="transparent",
            border_width=1,
            command=self._clear_probe_pins,
        )
        self.btn_clear_pins.pack(fill="x", padx=10, pady=(0, 10))

    @staticmethod
    def _make_kv_row(parent: Any, key_text: str, val_text: str) -> ctk.CTkLabel:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(
            row,
            text=key_text,
            font=ctk.CTkFont(size=10),
            text_color="#94A3B8",
            width=58,
            anchor="w",
        ).pack(side="left")
        lbl = ctk.CTkLabel(
            row,
            text=val_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#F8FAFC",
            anchor="e",
            wraplength=150,
            justify="right",
        )
        lbl.pack(side="right", fill="x", expand=True)
        return lbl

    # -------------------------------------------------------------------------
    # Sub-Page 3: Execution Log (Full Height Console)
    # -------------------------------------------------------------------------
    def _build_log_subpage(self):
        self.page_log = ctk.CTkFrame(self.content_container, fg_color=("gray92", "gray14"), corner_radius=0)

        log_top = ctk.CTkFrame(self.page_log, height=36, corner_radius=8, fg_color=("gray90", "gray17"))
        log_top.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(log_top, text="Execution & Pipeline Event Stream:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=10, pady=5)

        self.log_search_var = ctk.StringVar()
        entry_search = ctk.CTkEntry(log_top, textvariable=self.log_search_var, placeholder_text="Filter log...", width=160, height=26)
        entry_search.pack(side="left", padx=6, pady=5)
        entry_search.bind("<KeyRelease>", lambda _: self._filter_log())

        ctk.CTkButton(
            log_top,
            text="📋 Copy Log",
            width=90,
            height=26,
            command=self._copy_log_text
        ).pack(side="right", padx=(2, 10), pady=5)

        ctk.CTkButton(
            log_top,
            text="🗑️ Clear",
            width=70,
            height=26,
            fg_color="transparent",
            border_width=1,
            command=self._clear_log
        ).pack(side="right", padx=2, pady=5)

        self.log_text = ctk.CTkTextbox(self.page_log, font=ctk.CTkFont(family="Consolas", size=11), wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_config("warn", foreground="#F59E0B")
        self.log_text.tag_config("err", foreground="#EF4444")
        self.log_text.tag_config("ok", foreground="#10B981")
        self.log_text.tag_config("info", foreground="#3B82F6")

    # -------------------------------------------------------------------------
    # Sub-Page 4: File Health Report (Comprehensive Diagnostics Summary)
    # -------------------------------------------------------------------------
    def _build_health_subpage(self):
        self.page_health = ctk.CTkFrame(self.content_container, fg_color=("gray92", "gray14"), corner_radius=0)

        h_top = ctk.CTkFrame(self.page_health, height=36, corner_radius=8, fg_color=("gray90", "gray17"))
        h_top.pack(fill="x", pady=(0, 6))

        self.lbl_health_badge = ctk.CTkLabel(h_top, text="Status: Clean", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_health_badge.pack(side="left", padx=12, pady=5)

        self.lbl_health_err = ctk.CTkLabel(h_top, text="Errors: 0", text_color="#EF4444", font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_health_err.pack(side="left", padx=8, pady=5)

        self.lbl_health_warn = ctk.CTkLabel(h_top, text="Warnings: 0", text_color="#F59E0B", font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_health_warn.pack(side="left", padx=8, pady=5)

        self.lbl_health_info = ctk.CTkLabel(h_top, text="Info: 0", text_color="#3B82F6", font=ctk.CTkFont(size=11))
        self.lbl_health_info.pack(side="left", padx=8, pady=5)

        ctk.CTkButton(
            h_top,
            text="💾 Save Diagnostics Report",
            width=160,
            height=26,
            command=self._save_diagnostics_report
        ).pack(side="right", padx=10, pady=5)

        # Diagnostics Findings Table
        cols_diag = ("Severity", "Check", "File", "Location", "Explanation", "Suggested Fix")
        self.tree_diag = ttk.Treeview(self.page_health, columns=cols_diag, show="headings", height=12)
        widths = {"Severity": 90, "Check": 180, "File": 110, "Location": 90, "Explanation": 360, "Suggested Fix": 280}
        for c in cols_diag:
            self.tree_diag.heading(c, text=c)
            self.tree_diag.column(c, width=widths.get(c, 100), anchor="w")
        self.tree_diag.column("Severity", anchor="center")
        self.tree_diag.column("Location", anchor="center")

        self.tree_diag.tag_configure("ERROR", foreground="#EF4444")
        self.tree_diag.tag_configure("WARNING", foreground="#F59E0B")
        self.tree_diag.tag_configure("INFO", foreground="#3B82F6")

        sb_diag = ttk.Scrollbar(self.page_health, orient="vertical", command=self.tree_diag.yview)
        self.tree_diag.configure(yscrollcommand=sb_diag.set)
        self.tree_diag.pack(side="left", fill="both", expand=True)
        sb_diag.pack(side="right", fill="y")

    # -------------------------------------------------------------------------
    # Sub-Page 5: Output Files (Interactive File Catalog)
    # -------------------------------------------------------------------------
    def _build_outputs_subpage(self):
        self.page_outputs = ctk.CTkFrame(self.content_container, fg_color=("gray92", "gray14"), corner_radius=0)

        o_top = ctk.CTkFrame(self.page_outputs, height=36, corner_radius=8, fg_color=("gray90", "gray17"))
        o_top.pack(fill="x", pady=(0, 6))

        self.lbl_out_dir = ctk.CTkLabel(o_top, text="Results Directory: [No run yet]", font=ctk.CTkFont(size=11), text_color="gray")
        self.lbl_out_dir.pack(side="left", padx=12, pady=5)

        ctk.CTkButton(
            o_top,
            text="📁 Open Folder in Explorer",
            width=170,
            height=26,
            command=self._open_results_folder
        ).pack(side="right", padx=10, pady=5)

        # Scrollable card container for output files
        self.out_files_scroll = ctk.CTkScrollableFrame(self.page_outputs, fg_color="transparent")
        self.out_files_scroll.pack(fill="both", expand=True)

    # -------------------------------------------------------------------------
    # Navigation & Sub-Page Switching
    # -------------------------------------------------------------------------
    def _on_sub_tab_change(self, tab_name: str):
        self._show_subpage(tab_name)

    def _show_subpage(self, tab_name: str, force: bool = False):
        if not force and self.active_sub_tab == tab_name:
            return
        self.active_sub_tab = tab_name
        self.sub_seg_btn.set(tab_name)

        subpages = (self.page_summary, self.page_preview, self.page_log, self.page_health, self.page_outputs)
        target_sp = self.page_summary
        if "Preview" in tab_name:
            target_sp = self.page_preview
        elif "Log" in tab_name:
            target_sp = self.page_log
        elif "Health" in tab_name:
            target_sp = self.page_health
        elif "Output" in tab_name:
            target_sp = self.page_outputs

        for sp in subpages:
            if sp is not target_sp:
                sp.grid_remove()
        target_sp.grid(row=0, column=0, sticky="nsew")

        if target_sp is self.page_preview:
            self._refresh_active_preview_mode()
        elif target_sp is self.page_outputs:
            self._render_output_files_list()

    def _refresh_active_preview_mode(self) -> None:
        if "Dual" in self.preview_mode:
            self._render_dual()
        elif "Gallery" in self.preview_mode:
            self._render_gallery()
        else:
            self._render_current_figure()

    def select_sub_tab(self, name: str) -> None:
        """Programmatically switch to a subpage."""
        for t in SUB_TABS:
            if name.lower() in t.lower():
                self._show_subpage(t)
                return
        self._show_subpage(SUB_TABS[0])

    def on_show(self) -> None:
        """Called when the Results page becomes visible."""
        if self.active_sub_tab == "📈 Graph Preview":
            self._refresh_active_preview_mode()

    # -------------------------------------------------------------------------
    # Populate & Data Ingestion
    # -------------------------------------------------------------------------
    def populate(self, R_or_res: Dict[str, Any], figs: Optional[Dict[str, str]] = None) -> None:
        """Convenience method to populate ResultsFrame from result dict or summary."""
        if isinstance(R_or_res, dict) and "res" not in R_or_res and "summary" in R_or_res:
            R = {
                "res": {
                    "cp_mean": R_or_res["summary"].get("mean_cp", 0.0),
                    "power_stable_W": R_or_res["summary"].get("mean_power_W", 0.0),
                    "cm_mean": R_or_res["summary"].get("mean_torque_Nm", 0.0),
                    "ct_mean": R_or_res["summary"].get("mean_ct", 0.0),
                    "tsr": 3.5,
                    "omega": 50.0,
                    "Trev": 0.12,
                    "is_stable": True,
                },
                "figs": figs or {},
                "sel": [],
                "revs": R_or_res.get("rev_stats", []),
                "qa_checks": R_or_res.get("qa_checks", []),
            }
        elif isinstance(R_or_res, dict) and figs is not None and "figs" not in R_or_res:
            R = dict(R_or_res)
            R["figs"] = figs
        elif isinstance(R_or_res, dict) and figs is None and "res" not in R_or_res and "figs" not in R_or_res:
            R = {"res": {}, "figs": dict(R_or_res), "sel": [], "revs": [], "qa_checks": []}
        else:
            R = R_or_res
        self.set_results(R)
        self._render_current_figure(force=True)

    def set_figures(self, figs: Dict[str, str], default_key: Optional[str] = None) -> None:
        """Backward-compatible helper to update only the figures dictionary."""
        self.figs = {k: v for k, v in sorted((figs or {}).items())}
        self._img_cache.clear()
        self._rendered_single_state = None
        self._rendered_interactive_key = None
        self._gallery_built_keys = None
        self._outputs_built_key = None
        all_keys = list(self.figs.keys())
        titles = [FIGURE_TITLES.get(k, k) for k in all_keys]
        if titles:
            self.fig_menu.configure(values=titles)
            chosen_k = default_key if (default_key and default_key in self.figs) else ("03_cp_vs_azimuth.png" if "03_cp_vs_azimuth.png" in self.figs else all_keys[0])
            self.current_fig_key = chosen_k
            self.fig_var.set(FIGURE_TITLES.get(chosen_k, chosen_k))
        if self.active_sub_tab == "📈 Graph Preview":
            self._render_current_figure()

    def set_results(self, R: Dict[str, Any]) -> None:
        """Update KPI cards, figure catalog, gallery, and tables from analysis result dictionary."""
        self.current_result = R
        self.figs = {k: v for k, v in sorted(R.get("figs", {}).items())}
        self._img_cache.clear()
        self._rendered_single_state = None
        self._rendered_interactive_key = None
        self._gallery_built_keys = None
        self._outputs_built_key = None
        res = R.get("res", {})
        p = R.get("p")

        # 1. Update KPI ribbon
        cp_val = float(res.get("cp_mean") if res.get("cp_mean") is not None else 0.0)
        self.card_cp.lbl_val.configure(text=f"{cp_val:.4f}")  # type: ignore[attr-defined]
        sel_k = [str(r.get("k", "")) for r in R.get("sel", [])]
        self.card_cp.lbl_sub.configure(text=f"Revs {', '.join(sel_k)}" if sel_k else "Analyzed Revs")  # type: ignore[attr-defined]

        cp_st_raw = res.get("cp_trailing_stable_mean", res.get("cp_stable_mean", cp_val))
        cp_st = float(cp_st_raw if cp_st_raw is not None else cp_val)
        self.card_cp_stable.lbl_val.configure(text=f"{cp_st:.4f}")  # type: ignore[attr-defined]
        st_revs = [str(k) for k in res.get("stable_revs", [])]
        self.card_cp_stable.lbl_sub.configure(text=f"Stable {len(st_revs)} revs" if st_revs else "No stable revs")  # type: ignore[attr-defined]

        cm_raw = res.get("cm_mean", 0.0)
        cm_str = f"{float(cm_raw):.3f}" if isinstance(cm_raw, (int, float)) else "n/a"
        ct_raw = res.get("ct_mean")
        ct_str = f"{float(ct_raw):.3f}" if isinstance(ct_raw, (int, float)) else "n/a"
        self.card_cm_ct.lbl_val.configure(text=f"{cm_str} / {ct_str}")  # type: ignore[attr-defined]
        tsr_v = float(res.get("tsr") if res.get("tsr") is not None else 0.0)
        omega_v = float(res.get("omega") if res.get("omega") is not None else 0.0)
        self.card_cm_ct.lbl_sub.configure(text=f"λ={tsr_v:.2f}, ω={omega_v:.1f} rad/s")  # type: ignore[attr-defined]

        p_raw = res.get("power_stable_W", res.get("power_mean_W", 0.0))
        p_w = float(p_raw if p_raw is not None else 0.0)
        self.card_power.lbl_val.configure(text=f"{p_w:.1f} W")  # type: ignore[attr-defined]
        trev_v = float(res.get("Trev") if res.get("Trev") is not None else 0.0)
        self.card_power.lbl_sub.configure(text=f"Trev = {trev_v:.4f} s")  # type: ignore[attr-defined]

        is_st = res.get("is_stable", False)
        tol_disp = p.stable_tol if p else 5.0
        if is_st:
            self.card_status.lbl_val.configure(text="CONVERGED", text_color="#10B981")  # type: ignore[attr-defined]
            self.card_status.lbl_sub.configure(text=f"Tol: {tol_disp:g}% met")  # type: ignore[attr-defined]
        else:
            self.card_status.lbl_val.configure(text="NOT STABLE", text_color="#F59E0B")  # type: ignore[attr-defined]
            self.card_status.lbl_sub.configure(text=f"Tol: {tol_disp:g}% not met")  # type: ignore[attr-defined]

        # 2. Update figure menus
        all_keys = list(self.figs.keys())
        titles = [FIGURE_TITLES.get(k, k) for k in all_keys]
        if titles:
            self.fig_menu.configure(values=titles)
            default_k = "03_cp_vs_azimuth.png" if "03_cp_vs_azimuth.png" in self.figs else all_keys[0]
            self.current_fig_key = default_k
            self.fig_var.set(FIGURE_TITLES.get(default_k, default_k))
        else:
            self.fig_menu.configure(values=["No figures available"])
            self.fig_var.set("No figures available")

        # 3. Populate tables
        self._populate_tables(R)

        # 4. Populate diagnostics health table
        self._populate_health_table(R)

        # 5. Refresh active view
        if self.active_sub_tab == "📈 Graph Preview":
            self._refresh_active_preview_mode()
        elif self.active_sub_tab == "📁 Output Files":
            self._render_output_files_list()

    def _populate_tables(self, R: Dict[str, Any]):
        for item in self.tree_rev.get_children():
            self.tree_rev.delete(item)
        for item in self.tree_qa.get_children():
            self.tree_qa.delete(item)

        revs = R.get("revs", [])
        for r in revs:
            dcp_rel = f"{r['dcp'] * 100.0:+.2f}%" if r.get("dcp") is not None else "-"
            dcp_abs = f"{r['dcp_abs']:.4f}" if r.get("dcp_abs") is not None else "-"
            spread = f"{r['spread'] * 100.0:.1f}%" if r.get("spread") is not None else "-"
            cm_val = f"{r['cm']:.4f}" if r.get("cm") is not None else "-"
            ct_val = f"{r['ct']:.4f}" if r.get("ct") is not None else "-"
            t_s = r.get("t_start", r.get("a", 0.0))
            t_e = r.get("t_end", r.get("b", 0.0))
            cp_r = r.get("cp")
            cp_str = f"{float(cp_r):.4f}" if isinstance(cp_r, (int, float)) else "-"
            is_st = r.get("is_stable_step") if "is_stable_step" in r else r.get("stable")
            status = "STABLE" if is_st else "TRANSIENT"

            self.tree_rev.insert("", "end", values=(
                f"Rev {r.get('k', '-')}",
                f"{float(t_s or 0.0):.4f} – {float(t_e or 0.0):.4f}",
                cp_str,
                dcp_rel,
                dcp_abs,
                cm_val,
                ct_val,
                spread,
                status
            ))

        for q in R.get("qa_checks", []):
            st = q.get("status", "PASS")
            icon = "✓ PASS" if st == "PASS" else ("⚠ WARN" if st == "WARN" else "✖ FAIL")
            self.tree_qa.insert("", "end", values=(
                icon,
                q.get("check", ""),
                q.get("value", ""),
                q.get("detail", "")
            ))

    def _populate_health_table(self, R: Dict[str, Any]):
        for item in self.tree_diag.get_children():
            self.tree_diag.delete(item)

        diag_rep = R.get("diagnostics")
        # Fallback to diagnostics_frame report on app if not in result
        if not diag_rep and self.app and hasattr(self.app, "diagnostics_frame") and self.app.diagnostics_frame.report:
            diag_rep = self.app.diagnostics_frame.report

        err_cnt = 0
        warn_cnt = 0
        info_cnt = 0

        if diag_rep and hasattr(diag_rep, "findings"):
            for f in diag_rep.findings:
                sev = getattr(f, "severity", "INFO").upper()
                if sev == "ERROR":
                    err_cnt += 1
                    icon = "✖ ERROR"
                elif sev == "WARNING":
                    warn_cnt += 1
                    icon = "⚠ WARNING"
                else:
                    info_cnt += 1
                    icon = "ℹ INFO"

                self.tree_diag.insert("", "end", values=(
                    icon,
                    getattr(f, "check", ""),
                    getattr(f, "file", ""),
                    getattr(f, "location", ""),
                    getattr(f, "explanation", ""),
                    getattr(f, "suggested_fix", "")
                ), tags=(sev,))

        self.lbl_health_err.configure(text=f"Errors: {err_cnt}")
        self.lbl_health_warn.configure(text=f"Warnings: {warn_cnt}")
        self.lbl_health_info.configure(text=f"Info: {info_cnt}")
        if err_cnt > 0:
            self.lbl_health_badge.configure(text="Status: Errors Detected", text_color="#EF4444")
        elif warn_cnt > 0:
            self.lbl_health_badge.configure(text="Status: Warnings Present", text_color="#F59E0B")
        else:
            self.lbl_health_badge.configure(text="Status: Clean (Zero Issues)", text_color="#10B981")

    # -------------------------------------------------------------------------
    # Graph Preview Renderers
    # -------------------------------------------------------------------------
    def _on_preview_mode_change(self, mode: str):
        self.preview_mode = mode
        self.single_view_frame.pack_forget()
        self.dual_view_frame.pack_forget()
        self.gallery_frame.pack_forget()

        if "Single" in mode:
            self.single_controls_box.pack(fill="x", padx=6, pady=(0, 5))
            self.single_view_frame.pack(fill="both", expand=True)
            self._render_current_figure()
        elif "Dual" in mode:
            self.single_controls_box.pack_forget()
            self.dual_view_frame.pack(fill="both", expand=True)
            self._render_dual()
        elif "Gallery" in mode:
            self.single_controls_box.pack_forget()
            self.gallery_frame.pack(fill="both", expand=True)
            self._render_gallery()

    _on_mode_change = _on_preview_mode_change
    _on_view_mode_change = _on_preview_mode_change

    def _get_cached_pil_image(self, path: str) -> Image.Image:
        """Load and LRU-cache PIL Image in memory (bounded to 24 images)."""
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
        cache_key = f"{path}|{mtime}"
        if cache_key in self._img_cache:
            val = self._img_cache.pop(cache_key)
            self._img_cache[cache_key] = val
            return val
        with Image.open(path) as im:
            copied = im.copy()
        self._img_cache[cache_key] = copied
        while len(self._img_cache) > 24:
            self._img_cache.pop(next(iter(self._img_cache)))
        return copied

    def _can_render_interactive(self, fig_key: Optional[str]) -> bool:
        """Return True when live result arrays exist to render fig_key interactively."""
        if not fig_key or not isinstance(self.current_result, dict):
            return False
        req = ("data", "revs", "az", "res", "p")
        if not all(k in self.current_result and self.current_result[k] is not None for k in req):
            return False
        if fig_key == "09_cp_spectrum.png" and "spec" not in self.current_result:
            return False
        return True

    def _render_current_figure(self, force: bool = False):
        """Render selected figure interactively via Qt6 Matplotlib canvas or static PNG fallback."""
        if not self.current_fig_key:
            return

        if self._can_render_interactive(self.current_fig_key):
            try:
                self._render_interactive_figure(force=force)
                return
            except Exception:
                pass

        self._interactive_active = False
        self.interactive_wrap.grid_remove()
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")

        if self.current_fig_key not in self.figs:
            return
        path = self.figs[self.current_fig_key]
        if not os.path.isfile(path):
            return

        cw = max(self.canvas.winfo_width(), 400)
        ch = max(self.canvas.winfo_height(), 300)
        bg_target = "#1E1E1E"
        state_key = (self.current_fig_key, path, cw, ch, self.zoom_mode)
        if not force and self._rendered_single_state == state_key and self.current_tk_img is not None:
            return

        self.canvas.configure(bg=bg_target)

        try:
            img = self._get_cached_pil_image(path)
            orig_w, orig_h = img.size
            if self.zoom_mode == "Fit":
                scale = min((cw - 20) / orig_w, (ch - 20) / orig_h)
                scale = max(scale, 0.05)
                nw = int(orig_w * scale)
                nh = int(orig_h * scale)
            else:
                pct = float(self.zoom_mode.replace("%", "")) / 100.0
                nw = int(orig_w * pct)
                nh = int(orig_h * pct)

            nw = max(nw, 10)
            nh = max(nh, 10)
            resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
            self.current_tk_img = ImageTk.PhotoImage(resized)
            self.current_img_tk = ctk.CTkImage(light_image=resized, dark_image=resized, size=(nw, nh))
            self._rendered_single_state = state_key

            self.canvas.delete("all")
            x_pos = max(cw // 2, nw // 2)
            y_pos = max(ch // 2, nh // 2)

            self.canvas.create_image(x_pos, y_pos, image=self.current_tk_img, anchor="center")
            self.canvas.configure(scrollregion=(0, 0, max(cw, nw), max(ch, nh)))
        except Exception as e:
            self.canvas.delete("all")
            self.canvas.create_text(cw // 2, ch // 2, text=f"Error displaying figure: {e}", fill="red", font=("Segoe UI", 12))

    # -------------------------------------------------------------------------
    # Interactive Results Matplotlib Graph Engine (Zoom, Pan, Point-Only Probe)
    # -------------------------------------------------------------------------
    def _render_interactive_figure(self, force: bool = False) -> None:
        if not self.current_fig_key or self.current_result is None:
            return
        self.canvas.grid_remove()
        self.vsb.grid_remove()
        self.hsb.grid_remove()
        self.interactive_wrap.grid(row=0, column=0, columnspan=2, rowspan=2, sticky="nsew")
        self._interactive_active = True

        render_key = (id(self.current_result), self.current_fig_key)
        if not force and self._rendered_interactive_key == render_key:
            return

        if self._redraw_after_id is not None:
            try:
                self.after_cancel(self._redraw_after_id)
            except Exception:
                pass
            self._redraw_after_id = None

        self._drag_state = None
        self._box_patch = None
        self._probe_markers.clear()
        self._probe_artists.clear()
        self._active_probe_ax = None
        self._blit_bg = None
        self._layout_frozen = False
        self._view_history.clear()
        self._last_hover_sig = None
        self._clear_probe_pins(redraw=False)

        if int(mpl.rcParams.get("agg.path.chunksize", 0) or 0) < 10000:
            mpl.rcParams["agg.path.chunksize"] = 10000

        build_result_figure(
            self.current_fig_key,
            self.current_result,
            dark_mode=True,
            target_fig=self.res_fig,
        )
        self._rendered_interactive_key = render_key
        self._capture_home_limits()
        self.zoom_mode = "Fit"
        self.zoom_menu.set("Fit")
        self._set_probe_readout(None)
        self.res_mpl_canvas.draw_idle()

    def _on_mpl_draw(self, _event: Any = None) -> None:
        """Freeze layout after first valid-sized constrained pass, cache blit background, and redraw overlay."""
        if not self._interactive_active:
            return
        try:
            bbox = self.res_fig.bbox
            if bbox.width < 200 or bbox.height < 120:
                return
        except Exception:
            return
        if not self._layout_frozen:
            # Freezing layout after initial positioning prevents slow Kiwisolver re-layout on every zoom/pan
            self._layout_frozen = True
            try:
                self.res_fig.set_layout_engine("none")
            except Exception:
                pass
        try:
            self._blit_bg = self.res_mpl_canvas.copy_from_bbox(self.res_fig.bbox)
        except Exception:
            self._blit_bg = None
            return
        self._draw_animated_overlays()

    def _on_mpl_resize(self, _event: Any = None) -> None:
        """Invalidate blit cache on canvas resize and re-enable one layout pass."""
        self._blit_bg = None
        if self.current_fig_key == "12_dashboard.png":
            return
        self._layout_frozen = False
        try:
            self.res_fig.set_layout_engine("constrained")
        except Exception:
            pass

    def _schedule_canvas_redraw(self, delay_ms: int = 16) -> None:
        """Coalesce rapid wheel-zoom or pan drag events into a single clean redraw."""
        if self._redraw_after_id is not None:
            return
        try:
            self._redraw_after_id = self.after(delay_ms, self._flush_scheduled_redraw)
        except Exception:
            self.res_mpl_canvas.draw_idle()

    def _flush_scheduled_redraw(self) -> None:
        self._redraw_after_id = None
        if not self._interactive_active:
            return
        try:
            self.res_mpl_canvas.draw_idle()
        except Exception:
            pass

    def _draw_animated_overlays(self) -> None:
        """Render visible animated probe markers and selection box without touching background."""
        ax_p = self._active_probe_ax
        if ax_p is not None and ax_p in self._probe_artists:
            dot, vl, hl = self._probe_artists[ax_p]
            for art in (vl, hl, dot):
                if art is not None and art.get_visible():
                    try:
                        ax_p.draw_artist(art)
                    except Exception:
                        pass
        if self._box_patch is not None and self._box_patch.get_visible():
            ax_b = getattr(self._box_patch, "axes", None)
            if ax_b is not None:
                try:
                    ax_b.draw_artist(self._box_patch)
                except Exception:
                    pass

    def _blit_overlay(self, target_ax: Any = None) -> None:
        """Fast buffer blit of probe crosshair/dot or box rectangle with async Qt update (~2-4 ms)."""
        if self._blit_bg is None:
            self.res_mpl_canvas.draw_idle()
            return
        try:
            self.res_mpl_canvas.restore_region(self._blit_bg)
            self._draw_animated_overlays()
            bbox = target_ax.bbox if (target_ax is not None and getattr(target_ax, "bbox", None) is not None) else None
            self.res_mpl_canvas.update_bbox_region(bbox)
        except Exception:
            self._blit_bg = None
            self.res_mpl_canvas.draw_idle()

    def _capture_home_limits(self) -> None:
        self._home_limits.clear()
        for ax in self.res_fig.get_axes():
            if ax.get_label() == "<colorbar>":
                continue
            if ax.get_visible() and ax.has_data():
                self._home_limits[ax] = (ax.get_xlim(), ax.get_ylim())

    def _get_axes_group(self, ax: Any) -> List[Any]:
        """Return ax together with any twinx/twiny sibling sharing its normalized position."""
        if ax is None:
            return []
        try:
            pos = ax.get_position().bounds
        except Exception:
            return [ax]
        group = []
        for candidate in self.res_fig.get_axes():
            if not candidate.get_visible() or candidate not in self._home_limits:
                continue
            try:
                cpos = candidate.get_position().bounds
            except Exception:
                continue
            if all(abs(a - b) < 1e-3 for a, b in zip(pos, cpos)):
                group.append(candidate)
        return group or ([ax] if ax in self._home_limits else [])

    def _push_view_history(self, axes_list: List[Any]) -> None:
        snap = [(a, a.get_xlim(), a.get_ylim()) for a in axes_list if a in self._home_limits]
        if not snap:
            return
        self._view_history.append(snap)
        if len(self._view_history) > 40:
            self._view_history.pop(0)

    def _reset_interactive_view(self) -> None:
        if not self._interactive_active or not self._home_limits:
            return
        for ax, (xl, yl) in self._home_limits.items():
            ax.set_xlim(xl)
            ax.set_ylim(yl)
        self._view_history.clear()
        self._hide_probe_markers(redraw=False)
        self.zoom_mode = "Fit"
        self.zoom_menu.set("Fit")
        self.res_mpl_canvas.draw_idle()

    def _undo_interactive_view(self) -> None:
        if not self._interactive_active or not self._view_history:
            return
        snap = self._view_history.pop()
        for ax, xl, yl in snap:
            ax.set_xlim(xl)
            ax.set_ylim(yl)
        self._hide_probe_markers(redraw=False)
        self.res_mpl_canvas.draw_idle()

    def _apply_interactive_zoom_preset(self, val: str) -> None:
        if not self._interactive_active or not self._home_limits:
            return
        if val == "Fit":
            self._reset_interactive_view()
            return
        try:
            pct = float(val.replace("%", ""))
            factor = 100.0 / max(pct, 10.0)
        except Exception:
            return
        target_axes = list(self._home_limits.keys())
        self._push_view_history(target_axes)
        for ax, (hx, hy) in self._home_limits.items():
            if getattr(ax, "name", "") == "polar":
                ax.set_ylim(hy[0], hy[0] + (hy[1] - hy[0]) * factor)
                continue
            cx = 0.5 * (hx[0] + hx[1])
            cy = 0.5 * (hy[0] + hy[1])
            dx = 0.5 * (hx[1] - hx[0]) * factor
            dy = 0.5 * (hy[1] - hy[0]) * factor
            ax.set_xlim(cx - dx, cx + dx)
            ax.set_ylim(cy - dy, cy + dy)
        self._hide_probe_markers(redraw=False)
        self.res_mpl_canvas.draw_idle()

    def _on_interact_tool_change(self, val: str) -> None:
        if "Box" in val:
            self.interact_tool = "Box Zoom"
            self._last_hover_sig = None
            self._hide_probe_markers(redraw=True)
            self._set_probe_readout(None)
        elif "Pan" in val:
            self.interact_tool = "Pan"
        else:
            self.interact_tool = "Probe"

    def _toggle_probe(self) -> None:
        self._probe_enabled = not self._probe_enabled
        self._last_hover_ts = 0.0
        self._last_hover_sig = None
        if self._probe_enabled:
            self.btn_probe_toggle.configure(text="Probe: ON", fg_color="#059669", hover_color="#047857")
        else:
            self.btn_probe_toggle.configure(text="Probe: OFF", fg_color="#475569", hover_color="#334155")
            self._hide_probe_markers(redraw=True)
            self._set_probe_readout(None)

    def _scale_axis_around(self, ax: Any, xdata: Optional[float], ydata: Optional[float], scale: float) -> None:
        if getattr(ax, "name", "") == "polar":
            y0, y1 = ax.get_ylim()
            ax.set_ylim(y0, y0 + max(1e-6, (y1 - y0) * scale))
            return
        xl0, xl1 = ax.get_xlim()
        yl0, yl1 = ax.get_ylim()
        cx = xdata if (xdata is not None and math.isfinite(xdata)) else 0.5 * (xl0 + xl1)
        cy = ydata if (ydata is not None and math.isfinite(ydata)) else 0.5 * (yl0 + yl1)
        ax.set_xlim(cx - (cx - xl0) * scale, cx + (xl1 - cx) * scale)
        ax.set_ylim(cy - (cy - yl0) * scale, cy + (yl1 - cy) * scale)

    def _on_mpl_scroll(self, event: Any) -> None:
        if not self._interactive_active or event.inaxes is None:
            return
        group = self._get_axes_group(event.inaxes)
        if not group:
            return
        scale = 0.84 if event.button == "up" else (1.0 / 0.84)
        self._push_view_history(group)
        primary = group[0]
        self._scale_axis_around(primary, event.xdata, event.ydata, scale)
        for sibling in group[1:]:
            if getattr(sibling, "name", "") == "polar":
                self._scale_axis_around(sibling, None, None, scale)
                continue
            # Convert cursor pixel position into sibling axis data coordinates
            try:
                sx, sy = sibling.transData.inverted().transform((event.x, event.y))
            except Exception:
                sx, sy = None, None
            self._scale_axis_around(sibling, sx, sy, scale)
        self._hide_probe_markers(redraw=False)
        self._schedule_canvas_redraw(delay_ms=16)

    def _on_mpl_press(self, event: Any) -> None:
        if not self._interactive_active or event.inaxes is None:
            return
        if event.button == 1 and getattr(event, "dblclick", False):
            self._reset_interactive_view()
            return
        group = self._get_axes_group(event.inaxes)
        if not group:
            return
        if getattr(group[0], "name", "") == "polar":
            if event.button == 1 and self.interact_tool == "Probe" and self._probe_enabled:
                sample = self._snap_nearest_sample(event)
                if sample is not None:
                    self._apply_pin_sample(sample)
            return

        if event.button == 2 or (event.button == 1 and self.interact_tool == "Pan"):
            init_lims = [(a, a.get_xlim(), a.get_ylim()) for a in group]
            self._drag_state = {
                "mode": "pan",
                "group": group,
                "start_px": (event.x, event.y),
                "init_lims": init_lims,
            }
            self._last_pan_draw_ts = 0.0
            return

        if event.button == 3 or (event.button == 1 and self.interact_tool == "Box Zoom"):
            ax0 = group[0]
            try:
                xd, yd = ax0.transData.inverted().transform((event.x, event.y))
            except Exception:
                return
            rect = Rectangle(
                (xd, yd), 0.0, 0.0,
                fill=False,
                edgecolor="#38BDF8", linewidth=1.5, linestyle="--", zorder=20,
                animated=True,
            )
            rect._ts_probe = True  # type: ignore[attr-defined]
            ax0.add_patch(rect)
            self._box_patch = rect
            self._drag_state = {
                "mode": "box",
                "group": group,
                "ax0": ax0,
                "start_px": (event.x, event.y),
                "start_data": (xd, yd),
            }
            if self._blit_bg is None:
                self.res_mpl_canvas.draw_idle()
            return

        if event.button == 1 and self.interact_tool == "Probe" and self._probe_enabled:
            sample = self._snap_nearest_sample(event)
            if sample is not None:
                self._apply_pin_sample(sample)

    def _on_mpl_motion(self, event: Any) -> None:
        if not self._interactive_active:
            return
        if self._drag_state is not None:
            mode = self._drag_state["mode"]
            if mode == "pan":
                sx, sy = self._drag_state["start_px"]
                if event.x is None or event.y is None:
                    return
                dx_px = event.x - sx
                dy_px = event.y - sy
                for ax, xl, yl in self._drag_state["init_lims"]:
                    bbox = ax.get_window_extent()
                    if bbox.width <= 1 or bbox.height <= 1:
                        continue
                    dx_data = -dx_px * (xl[1] - xl[0]) / bbox.width
                    dy_data = -dy_px * (yl[1] - yl[0]) / bbox.height
                    ax.set_xlim(xl[0] + dx_data, xl[1] + dx_data)
                    ax.set_ylim(yl[0] + dy_data, yl[1] + dy_data)
                now = time.monotonic()
                if now - self._last_pan_draw_ts >= 0.032:
                    self._last_pan_draw_ts = now
                    self._schedule_canvas_redraw(delay_ms=1)
                else:
                    self._schedule_canvas_redraw(delay_ms=28)
                return

            if mode == "box" and self._box_patch is not None:
                if event.x is None or event.y is None:
                    return
                now = time.monotonic()
                if now - self._last_hover_ts < 0.014:
                    return
                self._last_hover_ts = now
                ax0 = self._drag_state["ax0"]
                try:
                    cur_x, cur_y = ax0.transData.inverted().transform((event.x, event.y))
                except Exception:
                    return
                x0, y0 = self._drag_state["start_data"]
                xl0, xl1 = sorted((x0, cur_x))
                yl0, yl1 = sorted((y0, cur_y))
                self._box_patch.set_xy((xl0, yl0))
                self._box_patch.set_width(xl1 - xl0)
                self._box_patch.set_height(yl1 - yl0)
                self._blit_overlay(target_ax=ax0)
                return

        if not self._probe_enabled or self.interact_tool == "Box Zoom":
            return
        now = time.monotonic()
        if self._active_probe_ax is event.inaxes and (now - self._last_hover_ts < 0.016):
            return
        self._last_hover_ts = now
        sample = self._snap_nearest_sample(event)
        if sample is None:
            if self._last_hover_sig is not None:
                self._last_hover_sig = None
                self._hide_probe_markers(redraw=True)
                self._set_probe_readout(None)
            return
        sig = (id(sample["ax"]), sample["series"], round(sample["x"], 6), round(sample["y"], 6))
        if sig != self._last_hover_sig:
            self._last_hover_sig = sig
            self._show_probe_marker(sample)
            self._set_probe_readout(sample)

    def _on_mpl_release(self, event: Any) -> None:
        if self._drag_state is None:
            return
        state = self._drag_state
        self._drag_state = None
        if state["mode"] == "pan":
            self._push_view_history([a for a, _, _ in state["init_lims"]])
            self.res_mpl_canvas.draw_idle()
            return
        if state["mode"] == "box":
            ax0 = state["ax0"]
            if self._box_patch is not None:
                try:
                    self._box_patch.set_visible(False)
                    self._box_patch.remove()
                except Exception:
                    pass
                self._box_patch = None
            if event.x is None or event.y is None:
                self._blit_overlay(target_ax=ax0)
                return
            sx, sy = state["start_px"]
            if abs(event.x - sx) < 6 or abs(event.y - sy) < 6:
                self._blit_overlay(target_ax=ax0)
                return
            group = state["group"]
            try:
                ex, ey = ax0.transData.inverted().transform((event.x, event.y))
            except Exception:
                self._blit_overlay(target_ax=ax0)
                return
            x0, y0 = state["start_data"]
            xl0, xl1 = sorted((x0, ex))
            yl0, yl1 = sorted((y0, ey))
            self._push_view_history(group)
            frac_y0 = (yl0 - ax0.get_ylim()[0]) / max(1e-12, ax0.get_ylim()[1] - ax0.get_ylim()[0])
            frac_y1 = (yl1 - ax0.get_ylim()[0]) / max(1e-12, ax0.get_ylim()[1] - ax0.get_ylim()[0])
            ax0.set_xlim(xl0, xl1)
            ax0.set_ylim(yl0, yl1)
            for sib in group[1:]:
                sy0, sy1 = sib.get_ylim()
                span = sy1 - sy0
                sib.set_xlim(xl0, xl1)
                sib.set_ylim(sy0 + frac_y0 * span, sy0 + frac_y1 * span)
            self._hide_probe_markers(redraw=False)
            self.res_mpl_canvas.draw_idle()

    def _on_mpl_leave(self, _event: Any) -> None:
        if not self._interactive_active:
            return
        self._last_hover_sig = None
        self._hide_probe_markers(redraw=True)
        self._set_probe_readout(None)

    def _snap_nearest_sample(self, event: Any) -> Optional[Dict[str, Any]]:
        """Find nearest plotted point/bar/segment/heatmap cell in display pixels."""
        if event.inaxes is None or event.x is None or event.y is None:
            return None
        group = self._get_axes_group(event.inaxes)
        if not group:
            return None

        best: Optional[Dict[str, Any]] = None
        best_dist = 42.0
        mx, my = float(event.x), float(event.y)

        for ax in group:
            # 1. Line2D curves (skip helper/probe/pin/reference 2-point lines)
            for artist in ax.get_lines():
                if not artist.get_visible() or getattr(artist, "_ts_probe", False):
                    continue
                lbl = str(artist.get_label() or "")
                if lbl.startswith("_"):
                    continue
                try:
                    xd = np.asarray(artist.get_xdata(), dtype=float)
                    yd = np.asarray(artist.get_ydata(), dtype=float)
                except Exception:
                    continue
                if len(xd) == 0 or len(xd) != len(yd):
                    continue
                # Skip flat constant horizontal/vertical reference guide lines (e.g., axhline / axvline / hlines)
                if len(xd) == 2 and (np.isclose(yd[0], yd[1]) or np.isclose(xd[0], xd[1])):
                    continue
                valid = getattr(artist, "_ts_valid_mask", None)
                xd_v = getattr(artist, "_ts_xd_valid", None)
                yd_v = getattr(artist, "_ts_yd_valid", None)
                is_sorted_x = getattr(artist, "_ts_sorted_x", None)
                if xd_v is None or yd_v is None:
                    valid = np.isfinite(xd) & np.isfinite(yd)
                    if not np.any(valid):
                        continue
                    xd_v, yd_v = xd[valid], yd[valid]
                    is_sorted_x = bool(len(xd_v) > 2 and np.all(xd_v[:-1] <= xd_v[1:]))
                    try:
                        artist._ts_xd_valid = xd_v  # type: ignore[attr-defined]
                        artist._ts_yd_valid = yd_v  # type: ignore[attr-defined]
                        artist._ts_sorted_x = is_sorted_x  # type: ignore[attr-defined]
                    except Exception:
                        pass

                # Fast O(log N) windowing for large monotonic series using this axis's own transform
                is_polar = getattr(ax, "name", "") == "polar"
                if is_sorted_x and len(xd_v) > 1500 and not is_polar:
                    try:
                        ax_x, _ = ax.transData.inverted().transform((mx, my))
                    except Exception:
                        ax_x = event.xdata
                    if ax_x is not None and math.isfinite(ax_x):
                        ci = int(np.searchsorted(xd_v, float(ax_x)))
                        i0 = max(0, ci - 1200)
                        i1 = min(len(xd_v), ci + 1200)
                        xd_loc, yd_loc = xd_v[i0:i1], yd_v[i0:i1]
                    else:
                        xd_loc, yd_loc = xd_v, yd_v
                elif len(xd_v) > 6000:
                    stride = max(1, len(xd_v) // 6000)
                    xd_loc, yd_loc = xd_v[::stride], yd_v[::stride]
                else:
                    xd_loc, yd_loc = xd_v, yd_v
                if len(xd_loc) == 0:
                    continue
                try:
                    px = ax.transData.transform(np.column_stack([xd_loc, yd_loc]))
                except Exception:
                    continue
                dists = np.hypot(px[:, 0] - mx, px[:, 1] - my)
                idx = int(np.argmin(dists))
                d_min = float(dists[idx])
                if d_min < best_dist:
                    best_dist = d_min
                    x_val = float(xd_loc[idx])
                    y_val = float(yd_loc[idx])
                    polar_off = float(getattr(ax, "_polar_offset", 0.0) or 0.0) if is_polar else 0.0
                    disp_x = math.degrees(x_val) % 360.0 if is_polar else x_val
                    best = {
                        "ax": ax,
                        "series": lbl or "Curve",
                        "x": x_val,
                        "y": y_val,
                        "disp_x": disp_x,
                        "polar_val": (y_val - polar_off) if is_polar else y_val,
                        "extra": ax.get_ylabel() or ax.get_title() or "Line sample",
                        "kind": "polar" if is_polar else "line",
                    }

            # 2. BarContainer rectangles
            for container in getattr(ax, "containers", []):
                patches_list = getattr(container, "patches", [])
                for b_idx, rect in enumerate(patches_list):
                    if not isinstance(rect, Rectangle) or not rect.get_visible():
                        continue
                    bx = rect.get_x() + 0.5 * rect.get_width()
                    by0 = rect.get_y()
                    by = by0 + rect.get_height()
                    try:
                        px_pts = ax.transData.transform([[bx, by0], [bx, by]])
                    except Exception:
                        continue
                    dx_px = abs(float(px_pts[1, 0]) - mx)
                    y_lo_px = min(float(px_pts[0, 1]), float(px_pts[1, 1])) - 18.0
                    y_hi_px = max(float(px_pts[0, 1]), float(px_pts[1, 1])) + 18.0
                    if dx_px < best_dist and y_lo_px <= my <= y_hi_px:
                        best_dist = dx_px
                        xlbls = [t.get_text().replace("\n", " ") for t in ax.get_xticklabels()]
                        x_tag = xlbls[b_idx] if b_idx < len(xlbls) and xlbls[b_idx] else f"{bx:g}"
                        best = {
                            "ax": ax,
                            "series": ax.get_ylabel() or "Bar Value",
                            "x": float(bx),
                            "y": float(rect.get_height()),
                            "disp_x": x_tag,
                            "extra": f"Bar: {x_tag}",
                            "kind": "bar",
                        }

            # 3. Spectrum LineCollection (vlines)
            for col in ax.collections:
                if not isinstance(col, LineCollection) or not col.get_visible() or getattr(col, "_ts_probe", False):
                    continue
                c_lbl = str(col.get_label() or "")
                if c_lbl.startswith("_"):
                    continue
                segs = col.get_segments()
                if not segs:
                    continue
                tops = np.array([seg[-1] for seg in segs if len(seg) >= 2], dtype=float)
                if len(tops) == 0:
                    continue
                try:
                    px_s = ax.transData.transform(tops)
                except Exception:
                    continue
                dists = np.hypot(px_s[:, 0] - mx, px_s[:, 1] - my)
                idx = int(np.argmin(dists))
                d_min = float(dists[idx])
                if d_min < best_dist:
                    best_dist = d_min
                    best = {
                        "ax": ax,
                        "series": col.get_label() or "Harmonic",
                        "x": float(tops[idx, 0]),
                        "y": float(tops[idx, 1]),
                        "disp_x": float(tops[idx, 0]),
                        "extra": "FFT Harmonic peak",
                        "kind": "line",
                    }

            # 4. Heatmap AxesImage
            if best is None and ax.images:
                im = ax.images[0]
                if isinstance(im, AxesImage) and event.xdata is not None and event.ydata is not None:
                    arr = np.asarray(im.get_array(), dtype=float)
                    ex0, ex1, ey0, ey1 = im.get_extent()
                    nrows, ncols = arr.shape[:2]
                    if ex0 <= event.xdata <= ex1 and min(ey0, ey1) <= event.ydata <= max(ey0, ey1):
                        ci = int(min(ncols - 1, max(0, (event.xdata - ex0) / max(1e-9, ex1 - ex0) * ncols)))
                        ri = int(min(nrows - 1, max(0, (event.ydata - ey0) / max(1e-9, ey1 - ey0) * nrows)))
                        val = float(arr[ri, ci])
                        return {
                            "ax": ax,
                            "series": "Cp Heatmap Cell",
                            "x": float(event.xdata),
                            "y": float(event.ydata),
                            "disp_x": float(event.xdata),
                            "heat_val": val,
                            "extra": f"θ={event.xdata:.1f}°, Rev={int(round(event.ydata))}",
                            "kind": "heatmap",
                        }
        return best

    def _get_or_create_probe_artists(self, ax: Any) -> Tuple[Any, Any, Any]:
        """Lazily create persistent animated probe artists so hover uses fast blitting."""
        if ax in self._probe_artists:
            return self._probe_artists[ax]
        xl = ax.get_xlim()
        yl = ax.get_ylim()
        is_polar = getattr(ax, "name", "") == "polar"
        dot, = ax.plot(
            [0.0], [0.0], marker="o", markersize=7.5,
            color="#38BDF8", markeredgecolor="#0F172A", markeredgewidth=1.4,
            zorder=25, linestyle="none", visible=False, animated=True, scalex=False, scaley=False,
        )
        dot._ts_probe = True  # type: ignore[attr-defined]
        if is_polar:
            vl = hl = None
        else:
            vl = ax.axvline(0.0, color="#38BDF8", linestyle=":", linewidth=0.9, alpha=0.55, zorder=24, visible=False, animated=True)
            hl = ax.axhline(0.0, color="#38BDF8", linestyle=":", linewidth=0.9, alpha=0.55, zorder=24, visible=False, animated=True)
            vl._ts_probe = True  # type: ignore[attr-defined]
            hl._ts_probe = True  # type: ignore[attr-defined]
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        pair = (dot, vl, hl)
        self._probe_artists[ax] = pair
        self._probe_markers[ax] = pair
        return pair

    def _show_probe_marker(self, sample: Dict[str, Any]) -> None:
        ax = sample["ax"]
        x, y = sample["x"], sample["y"]
        prev_ax = self._active_probe_ax
        if prev_ax is not None and prev_ax is not ax and prev_ax in self._probe_artists:
            for art in self._probe_artists[prev_ax]:
                if art is not None:
                    art.set_visible(False)

        dot, vl, hl = self._get_or_create_probe_artists(ax)
        dot.set_data([x], [y])
        dot.set_visible(True)
        if vl is not None:
            vl.set_xdata([x, x])
            vl.set_visible(True)
        if hl is not None:
            hl.set_ydata([y, y])
            hl.set_visible(True)
        self._active_probe_ax = ax
        self._blit_overlay(target_ax=ax if (prev_ax is None or prev_ax is ax) else None)

    def _hide_probe_markers(self, redraw: bool = True) -> None:
        prev_ax = self._active_probe_ax
        changed = False
        for pair in self._probe_artists.values():
            for art in pair:
                if art is not None and art.get_visible():
                    art.set_visible(False)
                    changed = True
        self._active_probe_ax = None
        if redraw and changed:
            self._blit_overlay(target_ax=prev_ax)

    def _set_probe_readout(self, sample: Optional[Dict[str, Any]]) -> None:
        if sample is None:
            updates = (
                (self.lbl_probe_status, "Hover over any curve to inspect", "#38BDF8"),
                (self.lbl_probe_series, "—", None),
                (self.lbl_probe_x, "—", None),
                (self.lbl_probe_y, "—", None),
                (self.lbl_probe_extra, "—", None),
            )
        else:
            disp_x = sample["disp_x"]
            x_txt = f"{disp_x:.4g}" + ("°" if sample["kind"] == "polar" else "") if isinstance(disp_x, (int, float)) else str(disp_x)
            y_val = sample.get("polar_val", sample["y"]) if sample["kind"] == "polar" else sample["y"]
            y_txt = f"Cp = {sample.get('heat_val', 0.0):.4f}" if sample["kind"] == "heatmap" else f"{y_val:.5g}"
            updates = (
                (self.lbl_probe_status, "Tracking active sample", "#10B981"),
                (self.lbl_probe_series, str(sample["series"])[:28], None),
                (self.lbl_probe_x, x_txt, None),
                (self.lbl_probe_y, y_txt, None),
                (self.lbl_probe_extra, str(sample.get("extra", "—"))[:30], None),
            )
        for lbl, txt, col in updates:
            key = id(lbl)
            prev = self._probe_readout_cache.get(str(key))
            if prev == (txt, col):
                continue
            self._probe_readout_cache[str(key)] = (txt, col)
            if col is not None:
                lbl.configure(text=txt, text_color=col)
            else:
                lbl.configure(text=txt)

    def _apply_pin_sample(self, sample: Dict[str, Any]) -> None:
        val_y = sample.get("heat_val", sample["y"]) if sample["kind"] == "heatmap" else (
            sample.get("polar_val", sample["y"]) if sample["kind"] == "polar" else sample["y"]
        )
        pin = {
            "ax": sample["ax"],
            "x": sample["x"],
            "y": sample["y"],
            "disp_x": sample["disp_x"],
            "val_y": float(val_y),
            "series": sample["series"],
            "kind": sample["kind"],
        }
        if self._pin_next == "A":
            self._pin_a = pin
            self._pin_next = "B"
        else:
            self._pin_b = pin
            self._pin_next = "A"
        self._refresh_pin_markers()

    def _refresh_pin_markers(self, redraw: bool = True) -> None:
        for art in self._pin_artists:
            try:
                art.remove()
            except Exception:
                pass
        self._pin_artists.clear()

        for pin, col in ((self._pin_a, "#F59E0B"), (self._pin_b, "#EC4899")):
            if pin is None:
                continue
            try:
                mk, = pin["ax"].plot(
                    [pin["x"]], [pin["y"]],
                    marker="D", markersize=7.5,
                    color=col, markeredgecolor="#FFFFFF", markeredgewidth=1.1,
                    zorder=26, linestyle="none",
                )
                mk._ts_probe = True  # type: ignore[attr-defined]
                self._pin_artists.append(mk)
            except Exception:
                pass

        def _fmt_pin(p: Optional[Dict[str, Any]]) -> str:
            if p is None:
                return "Click point"
            dx = f"{p['disp_x']:.3g}" if isinstance(p["disp_x"], (int, float)) else str(p["disp_x"])
            return f"({dx}, {p['val_y']:.4g})"

        self.lbl_pin_a.configure(text=_fmt_pin(self._pin_a))
        self.lbl_pin_b.configure(text=_fmt_pin(self._pin_b))
        if self._pin_a is not None and self._pin_b is not None:
            if isinstance(self._pin_a["disp_x"], (int, float)) and isinstance(self._pin_b["disp_x"], (int, float)):
                if self._pin_a.get("kind") == "polar" or self._pin_b.get("kind") == "polar":
                    dx_val = f"{(self._pin_b['disp_x'] - self._pin_a['disp_x']):+.4g}°"
                else:
                    dx_val = f"{(self._pin_b['x'] - self._pin_a['x']):+.4g}"
            else:
                dx_val = "—"
            dy_num = self._pin_b["val_y"] - self._pin_a["val_y"]
            pct = (
                f" ({(dy_num / abs(self._pin_a['val_y'])) * 100.0:+.1f}%)"
                if abs(self._pin_a["val_y"]) > 1e-9
                else ""
            )
            self.lbl_pin_delta_x.configure(text=dx_val)
            self.lbl_pin_delta_y.configure(text=f"{dy_num:+.4g}{pct}")
        else:
            self.lbl_pin_delta_x.configure(text="—")
            self.lbl_pin_delta_y.configure(text="—")

        if redraw:
            self.res_mpl_canvas.draw_idle()

    def _clear_probe_pins(self, redraw: bool = True) -> None:
        self._pin_a = None
        self._pin_b = None
        self._pin_next = "A"
        self._refresh_pin_markers(redraw=redraw)

    def shutdown(self) -> None:
        """Release embedded interactive Matplotlib figure and image caches on window close."""
        for attr in ("_redraw_after_id", "_resize_after_id", "_dual_resize_after_id"):
            tid = getattr(self, attr, None)
            if tid is not None:
                try:
                    self.after_cancel(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._drag_state = None
        self._box_patch = None
        self._blit_bg = None
        self._probe_artists.clear()
        self._probe_markers.clear()
        self._active_probe_ax = None
        self._pin_artists.clear()
        self._img_cache.clear()
        self._probe_readout_cache.clear()
        self.gallery_tk_imgs.clear()
        if hasattr(self, "res_fig") and self.res_fig is not None:
            try:
                self.res_fig.clear()
            except Exception:
                pass

    def _on_dual_configure(self, _event=None) -> None:
        if "Dual" not in self.preview_mode or not self.figs:
            return
        if self._dual_resize_after_id is not None:
            try:
                self.after_cancel(self._dual_resize_after_id)
            except Exception:
                pass
        self._dual_resize_after_id = self.after(60, self._debounced_dual_resize)

    def _debounced_dual_resize(self) -> None:
        self._dual_resize_after_id = None
        if "Dual" in self.preview_mode:
            self._render_dual()

    def _render_dual(self):
        """Render side-by-side comparison of two publication figures."""
        def get_path_for_title(t_str: str) -> Optional[str]:
            for k, t in FIGURE_TITLES.items():
                if t == t_str and k in self.figs:
                    return self.figs[k]
            if t_str in self.figs:
                return self.figs[t_str]
            return None

        p_left = get_path_for_title(self.dual_left_var.get())
        p_right = get_path_for_title(self.dual_right_var.get())

        half_w = max(int((self.dual_container.winfo_width() - 30) / 2), 200)
        h = max(self.dual_container.winfo_height() - 20, 200)

        if p_left and os.path.isfile(p_left):
            try:
                with Image.open(p_left) as im:
                    scale = min(half_w / im.width, h / im.height)
                    nw, nh = max(int(im.width * scale), 1), max(int(im.height * scale), 1)
                    thumb = im.resize((nw * 2, nh * 2), Image.Resampling.LANCZOS)
                    self.dual_left_tk = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=(nw, nh))
                    self.dual_left_label.configure(image=self.dual_left_tk, text="")
            except Exception:
                pass

        if p_right and os.path.isfile(p_right):
            try:
                with Image.open(p_right) as im:
                    scale = min(half_w / im.width, h / im.height)
                    nw, nh = max(int(im.width * scale), 1), max(int(im.height * scale), 1)
                    thumb = im.resize((nw * 2, nh * 2), Image.Resampling.LANCZOS)
                    self.dual_right_tk = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=(nw, nh))
                    self.dual_right_label.configure(image=self.dual_right_tk, text="")
            except Exception:
                pass

    def _swap_dual_figures(self):
        l = self.dual_left_var.get()
        r = self.dual_right_var.get()
        self.dual_left_var.set(r)
        self.dual_right_var.set(l)
        self._render_dual()

    def _render_gallery(self):
        """Render scrollable grid containing all generated publication figures with captions."""
        keys_sig = tuple(sorted(self.figs.items()))
        if self._gallery_built_keys == keys_sig and len(self.gallery_frame.winfo_children()) > 0:
            return
        self._gallery_built_keys = keys_sig
        for w in self.gallery_frame.winfo_children():
            w.destroy()
        self.gallery_tk_imgs.clear()

        if not self.figs:
            ctk.CTkLabel(self.gallery_frame, text="No figures available to display.", text_color="gray").pack(pady=40)
            return

        grid_container = ctk.CTkFrame(self.gallery_frame, fg_color="transparent")
        grid_container.pack(fill="both", expand=True, padx=6, pady=6)
        grid_container.columnconfigure(0, weight=1)
        grid_container.columnconfigure(1, weight=1)

        thumb_w = 460
        thumb_h = 300

        for idx, (f_key, f_path) in enumerate(sorted(self.figs.items())):
            row = idx // 2
            col = idx % 2

            card = ctk.CTkFrame(grid_container, corner_radius=8, fg_color=("gray90", "gray17"))
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

            title = FIGURE_TITLES.get(f_key, f_key)
            lbl_title = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="bold"))
            lbl_title.pack(anchor="w", padx=8, pady=(6, 4))

            if os.path.isfile(f_path):
                try:
                    with Image.open(f_path) as im_src:
                        im_thumb = im_src.copy()
                    im_thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.BILINEAR)
                    tw, th = max(im_thumb.width, 1), max(im_thumb.height, 1)
                    ctk_thumb = ctk.CTkImage(light_image=im_thumb, dark_image=im_thumb, size=(tw, th))
                    self.gallery_tk_imgs.append(ctk_thumb)

                    btn_img = ctk.CTkButton(
                        card,
                        image=ctk_thumb,
                        text="",
                        fg_color="transparent",
                        hover_color=("gray80", "gray25"),
                        command=lambda k=f_key: self._select_gallery_item(k)
                    )
                    btn_img.pack(padx=6, pady=4)
                except Exception:
                    ctk.CTkLabel(card, text="[Image error]").pack(pady=40)

            bar = ctk.CTkFrame(card, fg_color="transparent")
            bar.pack(fill="x", padx=8, pady=(2, 6))

            ctk.CTkButton(
                bar,
                text="🔍 View Single",
                width=90,
                height=24,
                font=ctk.CTkFont(size=10),
                command=lambda k=f_key: self._select_gallery_item(k)
            ).pack(side="left", padx=2)

            ctk.CTkButton(
                bar,
                text="🖼️ Full Size",
                width=80,
                height=24,
                font=ctk.CTkFont(size=10),
                fg_color="transparent",
                border_width=1,
                command=lambda p=f_path: self._open_external_image(p)
            ).pack(side="right", padx=2)

    def _select_gallery_item(self, f_key: str):
        self.current_fig_key = f_key
        self.fig_var.set(FIGURE_TITLES.get(f_key, f_key))
        self.mode_selector.set("🖼️ Single Figure")
        self._on_preview_mode_change("🖼️ Single Figure")
        self._render_current_figure()

    def _on_select_figure_title(self, title: str):
        for k, t in FIGURE_TITLES.items():
            if t == title and k in self.figs:
                self.current_fig_key = k
                self._render_current_figure()
                return
        if title in self.figs:
            self.current_fig_key = title
            self._render_current_figure()

    def _prev_figure(self):
        keys = list(self.figs.keys())
        if not keys:
            return
        idx = keys.index(self.current_fig_key) if self.current_fig_key in keys else 0
        new_k = keys[(idx - 1) % len(keys)]
        self.current_fig_key = new_k
        self.fig_var.set(FIGURE_TITLES.get(new_k, new_k))
        self._render_current_figure()

    def _next_figure(self):
        keys = list(self.figs.keys())
        if not keys:
            return
        idx = keys.index(self.current_fig_key) if self.current_fig_key in keys else 0
        new_k = keys[(idx + 1) % len(keys)]
        self.current_fig_key = new_k
        self.fig_var.set(FIGURE_TITLES.get(new_k, new_k))
        self._render_current_figure()

    def _on_zoom_change(self, val: str):
        self.zoom_mode = val
        if self._interactive_active:
            self._apply_interactive_zoom_preset(val)
        else:
            self._render_current_figure()

    def _on_canvas_configure(self, _event=None):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 50 or h < 50:
            return
        if not self.current_fig_key or self.current_fig_key not in self.figs:
            if hasattr(self, "canvas_placeholder_text") and self.canvas_placeholder_text:
                self.canvas.coords(self.canvas_placeholder_text, w // 2, h // 2)
            return
        if (w, h) == self._last_dims:
            return
        if abs(w - self._last_dims[0]) > 25 or abs(h - self._last_dims[1]) > 25:
            self._last_dims = (w, h)
            if self._resize_after_id is not None:
                try:
                    self.after_cancel(self._resize_after_id)
                except Exception:
                    pass
            self._resize_after_id = self.after(60, self._debounced_canvas_resize)

    def _debounced_canvas_resize(self):
        self._resize_after_id = None
        if self.preview_mode == "🖼️ Single Figure":
            self._render_current_figure()

    # -------------------------------------------------------------------------
    # Output Files List Renderer
    # -------------------------------------------------------------------------
    def _render_output_files_list(self):
        xlsx_path = self.current_result.get("xlsx") if self.current_result else None
        out_sig = (xlsx_path, tuple(sorted(self.figs.items())))
        if self._outputs_built_key == out_sig and len(self.out_files_scroll.winfo_children()) > 0:
            return
        self._outputs_built_key = out_sig
        for w in self.out_files_scroll.winfo_children():
            w.destroy()

        if not self.current_result:
            ctk.CTkLabel(self.out_files_scroll, text="No simulation run completed yet.", text_color="gray").pack(pady=40)
            return

        xlsx_path = self.current_result.get("xlsx")
        out_folder = os.path.dirname(xlsx_path) if xlsx_path else ""
        if not out_folder and self.figs:
            first_fig = next((p for p in self.figs.values() if p), "")
            out_folder = os.path.dirname(first_fig) if first_fig else ""
        self.lbl_out_dir.configure(text=f"Results Directory: {out_folder or '[No output folder]'}")

        # 1. Excel Workbook Card
        if xlsx_path and os.path.isfile(xlsx_path):
            c_xl = ctk.CTkFrame(self.out_files_scroll, corner_radius=8, fg_color=("gray92", "gray20"))
            c_xl.pack(fill="x", padx=6, pady=4)

            left = ctk.CTkFrame(c_xl, fg_color="transparent")
            left.pack(side="left", padx=10, pady=6)
            ctk.CTkLabel(left, text="📊 Excel Results Workbook", height=18, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(left, text=os.path.basename(xlsx_path), height=15, text_color="gray", font=ctk.CTkFont(size=11)).pack(anchor="w")

            act = ctk.CTkFrame(c_xl, fg_color="transparent")
            act.pack(side="right", padx=10, pady=6)
            ctk.CTkButton(act, text="Open in Excel", width=110, height=28, command=self._open_excel_file).pack(side="left", padx=4)
            ctk.CTkButton(act, text="Copy Path", width=80, height=28, fg_color="transparent", border_width=1, command=lambda: self._copy_text(xlsx_path)).pack(side="left", padx=4)

        # 2. Publication Figures Cards
        for f_key, f_path in sorted(self.figs.items()):
            if not os.path.isfile(f_path):
                continue
            card = ctk.CTkFrame(self.out_files_scroll, corner_radius=8, fg_color=("gray92", "gray20"))
            card.pack(fill="x", padx=6, pady=3)

            title = FIGURE_TITLES.get(f_key, f_key)
            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", padx=10, pady=5)
            ctk.CTkLabel(left, text=f"🖼️ {title}", height=17, font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
            sz_kb = os.path.getsize(f_path) // 1024
            ctk.CTkLabel(left, text=f"{os.path.basename(f_path)} ({sz_kb} KB)", height=14, text_color="gray", font=ctk.CTkFont(size=10)).pack(anchor="w")

            act = ctk.CTkFrame(card, fg_color="transparent")
            act.pack(side="right", padx=10, pady=6)
            ctk.CTkButton(act, text="View Figure", width=95, height=26, command=lambda p=f_path: self._open_external_image(p)).pack(side="left", padx=4)
            ctk.CTkButton(act, text="Copy Path", width=80, height=26, fg_color="transparent", border_width=1, command=lambda p=f_path: self._copy_text(p)).pack(side="left", padx=4)

    # -------------------------------------------------------------------------
    # Logging Methods
    # -------------------------------------------------------------------------
    def log(self, message: str, tag: str = "info") -> None:
        """Append line to execution log console."""
        if self._raw_log_text is not None:
            self._raw_log_text += f"{message}\n"
        self.log_text.insert("end", f"{message}\n", tag)
        self.log_text.see("end")

    def _clear_log(self):
        self._raw_log_text = None
        self.log_text.delete("1.0", "end")

    def _copy_log_text(self):
        txt = self.log_text.get("1.0", "end")
        if self.app:
            self.app.clipboard_clear()
            self.app.clipboard_append(txt)
            messagebox.showinfo("Clipboard", "Execution log copied to clipboard!")

    def _filter_log(self):
        query = self.log_search_var.get().strip().lower()
        if self._raw_log_text is None:
            self._raw_log_text = self.log_text.get("1.0", "end")
        if not query:
            if self._raw_log_text is not None:
                full_txt = self._raw_log_text
                self._raw_log_text = None
                self.log_text.delete("1.0", "end")
                self.log_text.insert("1.0", full_txt.rstrip("\n"))
                self.log_text.see("end")
            return
        lines = [ln for ln in self._raw_log_text.splitlines() if query in ln.lower()]
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", "\n".join(lines) + ("\n" if lines else ""))

    @staticmethod
    def _open_path_cross_platform(path: str) -> None:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # -------------------------------------------------------------------------
    # External File Actions & System Launchers
    # -------------------------------------------------------------------------
    def _open_results_folder(self):
        if not self.current_result:
            return
        xlsx = self.current_result.get("xlsx")
        folder = os.path.dirname(xlsx) if xlsx else (self.app.v_out.get() if self.app else "")
        if folder and os.path.isdir(folder):
            self._open_path_cross_platform(folder)

    def _open_excel_file(self):
        if not self.current_result:
            return
        xlsx = self.current_result.get("xlsx")
        if xlsx and os.path.isfile(xlsx):
            self._open_path_cross_platform(xlsx)

    def _open_full_size(self):
        if not self.current_fig_key or self.current_fig_key not in self.figs:
            return
        p = self.figs[self.current_fig_key]
        self._open_external_image(p)

    def _open_external_image(self, path: str):
        if path and os.path.isfile(path):
            try:
                self._open_path_cross_platform(path)
            except Exception as e:
                messagebox.showerror("Error", f"Could not launch viewer: {e}")

    def _copy_image(self):
        if not self.current_fig_key or self.current_fig_key not in self.figs:
            return
        path = self.figs[self.current_fig_key]
        if not path or not os.path.isfile(path):
            return
        try:
            if sys.platform.startswith("win"):
                import win32clipboard  # type: ignore[import-untyped]
                with Image.open(path) as img:
                    output = io.BytesIO()
                    img.convert("RGB").save(output, "BMP")
                    data = output.getvalue()[14:]
                    output.close()
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                win32clipboard.CloseClipboard()
                messagebox.showinfo("Clipboard", "Figure copied to clipboard!")
                return
        except Exception:
            pass
        self._copy_text(path)
        messagebox.showinfo("Clipboard", f"Image path copied to clipboard:\n{path}")

    def _save_image_as(self):
        if not self.current_fig_key or self.current_fig_key not in self.figs:
            return
        src = self.figs[self.current_fig_key]
        if not src or not os.path.isfile(src):
            messagebox.showwarning("Save As", "Source figure file is missing.")
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".png",
            initialfile=os.path.basename(src),
            filetypes=[("PNG Image", "*.png"), ("JPEG Image", "*.jpg"), ("PDF Document", "*.pdf")],
        )
        if dest:
            try:
                if os.path.abspath(src) == os.path.abspath(dest):
                    messagebox.showinfo("Saved", f"Figure saved to:\n{dest}")
                    return
                ext = os.path.splitext(dest)[1].lower()
                if ext in ("", ".png"):
                    shutil.copy2(src, dest)
                else:
                    with Image.open(src) as im:
                        im.convert("RGB").save(dest)
                messagebox.showinfo("Saved", f"Figure saved to:\n{dest}")
            except Exception as exc:
                messagebox.showerror("Save Error", f"Could not save figure: {exc}")

    def _copy_summary(self):
        if not self.current_result:
            return
        res = self.current_result.get("res", {})
        ct_v = res.get("ct_mean")
        ct_txt = f"{float(ct_v):.4f}" if isinstance(ct_v, (int, float)) else "n/a"
        pw_v = res.get("power_stable_W", res.get("power_mean_W", 0.0))
        cp_st_v = res.get("cp_trailing_stable_mean", res.get("cp_stable_mean", 0.0))
        txt = (
            f"VAWT CFD Simulation Summary:\n"
            f"Mean Cp: {float(res.get('cp_mean') or 0.0):.4f}\n"
            f"Trailing Stable Cp: {float(cp_st_v or 0.0):.4f}\n"
            f"Mean Cm: {float(res.get('cm_mean') or 0.0):.4f}\n"
            f"Mean Ct: {ct_txt}\n"
            f"Power (W): {float(pw_v or 0.0):.2f}\n"
            f"TSR: {float(res.get('tsr') or 0.0):.2f}\n"
            f"Convergence: {'STABLE' if res.get('is_stable') else 'TRANSIENT'}\n"
        )
        self._copy_text(txt)
        messagebox.showinfo("Clipboard", "Summary copied to clipboard!")

    def _copy_text(self, text: str):
        if self.app:
            self.app.clipboard_clear()
            self.app.clipboard_append(text)

    def _save_diagnostics_report(self):
        if not self.current_result:
            return
        xlsx = self.current_result.get("xlsx")
        default_dir = os.path.dirname(xlsx) if xlsx else ""
        dest = filedialog.asksaveasfilename(
            initialdir=default_dir,
            initialfile="diagnostics_report.txt",
            defaultextension=".txt",
            filetypes=[("Text File", "*.txt")]
        )
        if dest:
            try:
                with open(dest, "w", encoding="utf-8") as f:
                    f.write("VAWT CFD Diagnostics Report\n===========================\n")
                    for item in self.tree_diag.get_children():
                        vals = self.tree_diag.item(item, "values")
                        f.write(f"[{vals[0]}] {vals[1]} | File: {vals[2]} | Location: {vals[3]} | Detail: {vals[4]} | Fix: {vals[5] if len(vals) > 5 else ''}\n")
                messagebox.showinfo("Saved", f"Diagnostics saved to:\n{dest}")
            except Exception as e:
                messagebox.showerror("Error", f"Could not save report: {e}")
