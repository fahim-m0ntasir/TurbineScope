# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Publication Graph Preview & Interactive Viewer
========================================================================
Interactive viewer for post-processing figures supporting:
- Single View (with custom zoom levels, fit-to-window, and category filters)
- Side-by-Side Dual Compare View (simultaneous inspection of two figures)
- Gallery Grid View (scrollable visual thumbnail gallery)
- Copy Image to Clipboard (instant paste into Word / PowerPoint / LaTeX)
- Save Image As... (export high-res publication figures anywhere)
- Full-size OS viewer opening and Explorer folder navigation
- Keyboard navigation (Left/Right arrow keys)
"""
from __future__ import annotations

import os
import shutil
import sys
import subprocess
from typing import Dict, List, Optional
from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import filedialog, messagebox
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

CATEGORIES = {
    "All": list(FIGURE_TITLES.keys()),
    "⚡ Power & Cp": [
        "01_cp_history.png", "02_cp_per_revolution.png", "03_cp_vs_azimuth.png",
        "12_dashboard.png", "13_power_contribution_quartiles.png", "14_cumulative_moment_distribution.png"
    ],
    "🌪️ Blades & Forces": [
        "04_blade_torque_vs_azimuth.png", "05_polar_blade_cp.png", "06_ct_cy.png", "07_cm.png",
        "08_alpha_w_re.png", "15_blade_tangential_load.png", "16_geometric_kinematics.png", "17_global_rotor_coefficients.png"
    ],
    "📊 Convergence & FFT": [
        "09_cp_spectrum.png", "10_convergence.png", "11_cp_heatmap.png", "12_dashboard.png", "18_cp_convergence_history.png"
    ],
}

ZOOM_LEVELS = ["Fit", "50%", "75%", "100%", "125%", "150%", "200%"]


class GraphPreviewFrame(ctk.CTkFrame):
    """Interactive preview panel for post-processing figures with multiple viewing modes."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.figs: Dict[str, str] = {}
        self.current_img_tk: Optional[ctk.CTkImage] = None
        self.dual_left_tk: Optional[ctk.CTkImage] = None
        self.dual_right_tk: Optional[ctk.CTkImage] = None
        self.gallery_tks: List[ctk.CTkImage] = []

        self._last_resize_dims = (0, 0)
        self._last_dual_dims = (0, 0)
        self._resize_after_id = None
        self._dual_resize_after_id = None
        self.active_category: str = "All"
        self.filtered_keys: List[str] = []
        self.view_mode: str = "Single View"  # "Single View", "Dual Compare", "Gallery Grid"
        self.zoom_mode: str = "Fit"          # "Fit", "50%", "75%", "100%", "125%", "150%", "200%"

        self._build_top_controls()
        self._build_view_containers()

        # Keyboard navigation bindings
        self.bind("<Left>", lambda e: self._prev_fig())
        self.bind("<Right>", lambda e: self._next_fig())

    def _build_top_controls(self):
        # ---- Master Header Bar: Mode Switcher & Global Actions ----
        master_bar = ctk.CTkFrame(self, fg_color="transparent")
        master_bar.pack(fill="x", padx=10, pady=(6, 4))

        ctk.CTkLabel(
            master_bar,
            text="Graph View:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        ).pack(side="left", padx=(0, 6))

        self.mode_selector = ctk.CTkSegmentedButton(
            master_bar,
            values=["🖼️ Single View", "⚖️ Dual Compare", "▦ Gallery Grid"],
            command=self._on_view_mode_change,
            font=ctk.CTkFont(size=11, weight="bold"),
            height=28
        )
        self.mode_selector.set("🖼️ Single View")
        self.mode_selector.pack(side="left", padx=4)

        # Right-side global productivity buttons
        ctk.CTkButton(
            master_bar,
            text="📁 Open Folder",
            width=95,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._open_figures_folder
        ).pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            master_bar,
            text="🔍 Open Full Size",
            width=105,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color=("royal blue", "#2563EB"),
            hover_color=("blue", "#1D4ED8"),
            command=self._open_full_size
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            master_bar,
            text="💾 Save As...",
            width=90,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._save_image_as
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            master_bar,
            text="📋 Copy Image",
            width=95,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._copy_image_to_clipboard
        ).pack(side="right", padx=4)

        # ---- Secondary Sub-Bar for Single View Controls ----
        self.single_controls_bar = ctk.CTkFrame(self, corner_radius=6, fg_color=("gray92", "gray18"))
        self.single_controls_bar.pack(fill="x", padx=10, pady=(2, 6))

        cat_row = ctk.CTkFrame(self.single_controls_bar, fg_color="transparent")
        cat_row.pack(fill="x", padx=4, pady=(2, 0))

        # Category radiobuttons
        ctk.CTkLabel(
            cat_row,
            text="Category:",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        ).pack(side="left", padx=(4, 4), pady=2)

        self.cat_var = ctk.StringVar(value="All")
        for cat in CATEGORIES.keys():
            ctk.CTkRadioButton(
                cat_row,
                text=cat,
                variable=self.cat_var,
                value=cat,
                command=self._on_category_change,
                font=ctk.CTkFont(size=11),
                width=20
            ).pack(side="left", padx=4, pady=2)

        self.single_nav_row = ctk.CTkFrame(self.single_controls_bar, fg_color="transparent")
        self.single_nav_row.pack(fill="x", padx=4, pady=(0, 2))

        # Stepping buttons
        self.btn_prev = ctk.CTkButton(
            self.single_nav_row,
            text="◀ Prev",
            width=62,
            height=26,
            command=self._prev_fig
        )
        self.btn_prev.pack(side="left", padx=(4, 3), pady=3)

        self.btn_next = ctk.CTkButton(
            self.single_nav_row,
            text="Next ▶",
            width=62,
            height=26,
            command=self._next_fig
        )
        self.btn_next.pack(side="left", padx=3, pady=3)

        # Figure title dropdown
        self.fig_var = ctk.StringVar(value="No figures yet")
        self.fig_menu = ctk.CTkOptionMenu(
            self.single_nav_row,
            variable=self.fig_var,
            values=["No figures yet"],
            width=260,
            command=self._on_select_fig_title
        )
        self.fig_menu.pack(side="left", padx=6, pady=3)

        self.idx_label = ctk.CTkLabel(
            self.single_nav_row,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=("gray30", "gray70")
        )
        self.idx_label.pack(side="left", padx=4, pady=3)

        # Zoom controls
        ctk.CTkLabel(
            self.single_nav_row,
            text="Zoom:",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold")
        ).pack(side="left", padx=(8, 2), pady=3)

        ctk.CTkButton(
            self.single_nav_row,
            text="➖",
            width=28,
            height=24,
            command=self._zoom_out
        ).pack(side="left", padx=1, pady=3)

        self.zoom_menu = ctk.CTkOptionMenu(
            self.single_nav_row,
            values=ZOOM_LEVELS,
            width=75,
            height=24,
            command=self._on_zoom_select
        )
        self.zoom_menu.set("Fit")
        self.zoom_menu.pack(side="left", padx=2, pady=3)

        ctk.CTkButton(
            self.single_nav_row,
            text="➕",
            width=28,
            height=24,
            command=self._zoom_in
        ).pack(side="left", padx=1, pady=3)

        self.info_label = ctk.CTkLabel(
            self.single_nav_row,
            text="",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="gray"
        )
        self.info_label.pack(side="right", padx=8, pady=3)

        # ---- Secondary Sub-Bar for Dual Compare Controls (Initially hidden) ----
        self.dual_controls_bar = ctk.CTkFrame(self, corner_radius=6, fg_color=("gray92", "gray18"))

        ctk.CTkLabel(
            self.dual_controls_bar,
            text="Left Graph:",
            font=ctk.CTkFont(weight="bold", size=11)
        ).pack(side="left", padx=(8, 4), pady=4)

        self.dual_left_var = ctk.StringVar(value="01. Power Coefficient (Cp) History")
        self.dual_left_menu = ctk.CTkOptionMenu(
            self.dual_controls_bar,
            variable=self.dual_left_var,
            values=list(FIGURE_TITLES.values()),
            width=250,
            command=lambda v: self._render_dual()
        )
        self.dual_left_menu.pack(side="left", padx=4, pady=4)

        ctk.CTkButton(
            self.dual_controls_bar,
            text="⇄ Swap",
            width=65,
            height=24,
            command=self._swap_dual_figures
        ).pack(side="left", padx=8, pady=4)

        ctk.CTkLabel(
            self.dual_controls_bar,
            text="Right Graph:",
            font=ctk.CTkFont(weight="bold", size=11)
        ).pack(side="left", padx=(8, 4), pady=4)

        self.dual_right_var = ctk.StringVar(value="03. Rotor Cp vs Azimuth (Phase-Averaged)")
        self.dual_right_menu = ctk.CTkOptionMenu(
            self.dual_controls_bar,
            variable=self.dual_right_var,
            values=list(FIGURE_TITLES.values()),
            width=250,
            command=lambda v: self._render_dual()
        )
        self.dual_right_menu.pack(side="left", padx=4, pady=4)

    def _build_view_containers(self):
        # 1. Single View Container
        self.single_container = ctk.CTkFrame(self, corner_radius=8, border_width=1)
        self.single_container.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.single_container.pack_propagate(False)

        self.img_label = ctk.CTkLabel(
            self.single_container,
            text="Run an analysis or multi-stage simulation to preview publication figures.",
            text_color="gray"
        )
        self.img_label.pack(fill="both", expand=True, padx=4, pady=4)
        self.single_container.bind("<Configure>", self._on_configure)

        # 2. Dual Compare View Container (Initially hidden)
        self.dual_container = ctk.CTkFrame(self, corner_radius=8, border_width=1)
        self.dual_container.pack_propagate(False)
        self.dual_container.columnconfigure(0, weight=1)
        self.dual_container.columnconfigure(1, weight=1)
        self.dual_container.rowconfigure(0, weight=1)

        self.dual_left_frame = ctk.CTkFrame(self.dual_container, fg_color="transparent")
        self.dual_left_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.dual_left_label = ctk.CTkLabel(self.dual_left_frame, text="")
        self.dual_left_label.pack(fill="both", expand=True)

        self.dual_right_frame = ctk.CTkFrame(self.dual_container, fg_color="transparent")
        self.dual_right_frame.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        self.dual_right_label = ctk.CTkLabel(self.dual_right_frame, text="")
        self.dual_right_label.pack(fill="both", expand=True)

        self.dual_container.bind("<Configure>", self._on_dual_configure)

        # 3. Gallery Grid View Container (Initially hidden)
        self.gallery_container = ctk.CTkScrollableFrame(self, corner_radius=8, border_width=1)
        self.gallery_container.pack_propagate(False)
        for _col in range(3):
            self.gallery_container.columnconfigure(_col, weight=1, uniform="gal")

    def set_figures(self, figs_dict: Dict[str, str], default_key: str = "03_cp_vs_azimuth.png") -> None:
        """Update figure dictionary and display the default or first figure."""
        self.figs = {k: v for k, v in sorted(figs_dict.items())}
        self._update_filtered_keys()

        if self.filtered_keys:
            chosen = default_key if default_key in self.filtered_keys else self.filtered_keys[0]
            self._set_current_key(chosen)
            # Update dual compare defaults
            if len(self.filtered_keys) >= 2:
                self.dual_left_var.set(FIGURE_TITLES.get(self.filtered_keys[0], self.filtered_keys[0]))
                self.dual_right_var.set(FIGURE_TITLES.get(self.filtered_keys[1], self.filtered_keys[1]))
        else:
            self.fig_menu.configure(values=["No figures yet"])
            self.fig_var.set("No figures yet")
            self.img_label.configure(image=None, text="No figures available.")
            self.idx_label.configure(text="")
            self.info_label.configure(text="")

        if self.view_mode == "Dual Compare":
            self._render_dual()
        elif self.view_mode == "Gallery Grid":
            self._render_gallery()

    populate = set_figures

    def _update_filtered_keys(self) -> None:
        cat = self.cat_var.get()
        all_keys = list(self.figs.keys())
        if cat == "All":
            self.filtered_keys = all_keys
        else:
            allowed = set(CATEGORIES.get(cat, []))
            self.filtered_keys = [k for k in all_keys if k in allowed]

        titles = [FIGURE_TITLES.get(k, k) for k in self.filtered_keys]
        if titles:
            self.fig_menu.configure(values=titles)
            self.dual_left_menu.configure(values=titles)
            self.dual_right_menu.configure(values=titles)
        else:
            self.fig_menu.configure(values=["No figures in this category"])

    def _on_view_mode_change(self, mode: str):
        if "Dual" in mode:
            self.view_mode = "Dual Compare"
            self.single_controls_bar.pack_forget()
            self.single_container.pack_forget()
            self.gallery_container.pack_forget()

            self.dual_controls_bar.pack(fill="x", padx=10, pady=(2, 6))
            self.dual_container.pack(fill="both", expand=True, padx=10, pady=(0, 8))
            self._render_dual()
        elif "Gallery" in mode:
            self.view_mode = "Gallery Grid"
            self.dual_controls_bar.pack_forget()
            self.dual_container.pack_forget()
            self.single_container.pack_forget()
            self.single_nav_row.pack_forget()

            self.single_controls_bar.pack(fill="x", padx=10, pady=(2, 6))
            self.gallery_container.pack(fill="both", expand=True, padx=10, pady=(0, 8))
            self._render_gallery()
        else:
            self.view_mode = "Single View"
            self.dual_controls_bar.pack_forget()
            self.dual_container.pack_forget()
            self.gallery_container.pack_forget()

            self.single_nav_row.pack(fill="x", padx=4, pady=(0, 2))
            self.single_controls_bar.pack(fill="x", padx=10, pady=(2, 6))
            self.single_container.pack(fill="both", expand=True, padx=10, pady=(0, 8))
            self._render_current()

    def _on_category_change(self) -> None:
        self._update_filtered_keys()
        if self.filtered_keys:
            self._set_current_key(self.filtered_keys[0])
        else:
            self.img_label.configure(image=None, text=f"No figures match category: {self.cat_var.get()}")

        if self.view_mode == "Gallery Grid":
            self._render_gallery()

    def _get_current_key(self) -> Optional[str]:
        title = self.fig_var.get()
        for k, t in FIGURE_TITLES.items():
            if t == title and k in self.figs:
                return k
        if title in self.figs:
            return title
        return None

    def _set_current_key(self, key: str) -> None:
        if key in self.figs:
            title = FIGURE_TITLES.get(key, key)
            self.fig_var.set(title)
            self._render_current()

    def _on_select_fig_title(self, selected_title: str) -> None:
        key = self._get_current_key()
        if key:
            self._render_current()

    def _prev_fig(self) -> None:
        if not self.filtered_keys:
            return
        curr = self._get_current_key()
        if curr in self.filtered_keys:
            idx = self.filtered_keys.index(curr)
            new_idx = (idx - 1) % len(self.filtered_keys)
            self._set_current_key(self.filtered_keys[new_idx])

    def _next_fig(self) -> None:
        if not self.filtered_keys:
            return
        curr = self._get_current_key()
        if curr in self.filtered_keys:
            idx = self.filtered_keys.index(curr)
            new_idx = (idx + 1) % len(self.filtered_keys)
            self._set_current_key(self.filtered_keys[new_idx])

    def _zoom_in(self):
        curr = self.zoom_mode
        if curr == "Fit":
            new_zoom = "100%"
        else:
            idx = ZOOM_LEVELS.index(curr) if curr in ZOOM_LEVELS else 3
            new_zoom = ZOOM_LEVELS[min(len(ZOOM_LEVELS) - 1, idx + 1)]
        self.zoom_mode = new_zoom
        self.zoom_menu.set(new_zoom)
        self._render_current()

    def _zoom_out(self):
        curr = self.zoom_mode
        if curr == "Fit":
            new_zoom = "50%"
        else:
            idx = ZOOM_LEVELS.index(curr) if curr in ZOOM_LEVELS else 3
            new_zoom = ZOOM_LEVELS[max(0, idx - 1)]
        self.zoom_mode = new_zoom
        self.zoom_menu.set(new_zoom)
        self._render_current()

    def _on_zoom_select(self, val: str):
        self.zoom_mode = val
        self._render_current()

    def _on_configure(self, event=None) -> None:
        w = self.single_container.winfo_width()
        h = self.single_container.winfo_height()
        if abs(w - self._last_resize_dims[0]) > 25 or abs(h - self._last_resize_dims[1]) > 25:
            self._last_resize_dims = (w, h)
            if self.view_mode == "Single View":
                if self._resize_after_id is not None:
                    try:
                        self.after_cancel(self._resize_after_id)
                    except Exception:
                        pass
                self._resize_after_id = self.after(100, self._render_current)

    def _on_dual_configure(self, event=None) -> None:
        w = self.dual_container.winfo_width()
        h = self.dual_container.winfo_height()
        if abs(w - self._last_dual_dims[0]) > 25 or abs(h - self._last_dual_dims[1]) > 25:
            self._last_dual_dims = (w, h)
            if self.view_mode == "Dual Compare":
                if self._dual_resize_after_id is not None:
                    try:
                        self.after_cancel(self._dual_resize_after_id)
                    except Exception:
                        pass
                self._dual_resize_after_id = self.after(100, self._render_dual)

    def _render_current(self) -> None:
        key = self._get_current_key()
        path = self.figs.get(key) if key else None
        if not path or not os.path.isfile(path):
            return

        if key in self.filtered_keys:
            pos = self.filtered_keys.index(key) + 1
            tot = len(self.filtered_keys)
            self.idx_label.configure(text=f"[{pos}/{tot}]")

        try:
            cw = max(self.single_container.winfo_width() - 20, 200)
            ch = max(self.single_container.winfo_height() - 20, 200)

            with Image.open(path) as img:
                orig_w, orig_h = img.size

                if self.zoom_mode == "Fit":
                    scale = min(cw / orig_w, ch / orig_h)
                    new_w = max(int(orig_w * scale), 1)
                    new_h = max(int(orig_h * scale), 1)
                else:
                    percent = float(self.zoom_mode.replace("%", "")) / 100.0
                    new_w = max(int(orig_w * percent), 1)
                    new_h = max(int(orig_h * percent), 1)

                # Supersample 2x for high-DPI display crispness
                if orig_w >= new_w * 2 and orig_h >= new_h * 2:
                    render_img = img.resize((new_w * 2, new_h * 2), Image.Resampling.LANCZOS)
                else:
                    render_img = img.copy()

                self.current_img_tk = ctk.CTkImage(light_image=render_img, dark_image=render_img, size=(new_w, new_h))
                self.img_label.configure(image=self.current_img_tk, text="")
                self.info_label.configure(text=f"{orig_w}×{orig_h} px  ({self.zoom_mode})")
        except Exception as exc:
            self.img_label.configure(image=None, text=f"Error displaying image: {exc}")

    def _swap_dual_figures(self):
        l = self.dual_left_var.get()
        r = self.dual_right_var.get()
        self.dual_left_var.set(r)
        self.dual_right_var.set(l)
        self._render_dual()

    def _render_dual(self):
        if not self.figs:
            return

        def get_path_for_title(title: str) -> Optional[str]:
            for k, t in FIGURE_TITLES.items():
                if t == title and k in self.figs:
                    return self.figs[k]
            if title in self.figs:
                return self.figs[title]
            return None

        p_left = get_path_for_title(self.dual_left_var.get())
        p_right = get_path_for_title(self.dual_right_var.get())

        half_w = max(int((self.dual_container.winfo_width() - 30) / 2), 150)
        h = max(self.dual_container.winfo_height() - 20, 150)

        if p_left and os.path.isfile(p_left):
            try:
                with Image.open(p_left) as img:
                    scale = min(half_w / img.width, h / img.height)
                    nw, nh = max(int(img.width * scale), 1), max(int(img.height * scale), 1)
                    if img.width >= nw * 2 and img.height >= nh * 2:
                        render_img = img.resize((nw * 2, nh * 2), Image.Resampling.LANCZOS)
                    else:
                        render_img = img.copy()
                    self.dual_left_tk = ctk.CTkImage(light_image=render_img, dark_image=render_img, size=(nw, nh))
                    self.dual_left_label.configure(image=self.dual_left_tk, text="")
            except Exception:
                pass

        if p_right and os.path.isfile(p_right):
            try:
                with Image.open(p_right) as img:
                    scale = min(half_w / img.width, h / img.height)
                    nw, nh = max(int(img.width * scale), 1), max(int(img.height * scale), 1)
                    if img.width >= nw * 2 and img.height >= nh * 2:
                        render_img = img.resize((nw * 2, nh * 2), Image.Resampling.LANCZOS)
                    else:
                        render_img = img.copy()
                    self.dual_right_tk = ctk.CTkImage(light_image=render_img, dark_image=render_img, size=(nw, nh))
                    self.dual_right_label.configure(image=self.dual_right_tk, text="")
            except Exception:
                pass

    def _render_gallery(self):
        for widget in self.gallery_container.winfo_children():
            widget.destroy()

        self.gallery_tks.clear()
        if not self.filtered_keys:
            ctk.CTkLabel(self.gallery_container, text="No figures available.").pack(pady=20)
            return

        # Grid layout with 3 columns
        cols = 3
        for idx, key in enumerate(self.filtered_keys):
            path = self.figs.get(key)
            if not path or not os.path.isfile(path):
                continue

            r = idx // cols
            c = idx % cols

            card = ctk.CTkFrame(self.gallery_container, corner_radius=8, border_width=1)
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")

            title = FIGURE_TITLES.get(key, key)
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(weight="bold", size=11), wraplength=220, justify="left").pack(padx=8, pady=(8, 4), anchor="w")

            try:
                with Image.open(path) as img:
                    thumb_w, thumb_h = 240, 140
                    scale = min(thumb_w / img.width, thumb_h / img.height)
                    nw, nh = max(int(img.width * scale), 1), max(int(img.height * scale), 1)
                    thumb_img = img.resize((nw * 2, nh * 2), Image.Resampling.LANCZOS)
                    ctk_img = ctk.CTkImage(light_image=thumb_img, dark_image=thumb_img, size=(nw, nh))
                    self.gallery_tks.append(ctk_img)

                    lbl_btn = ctk.CTkLabel(card, image=ctk_img, text="", cursor="hand2")
                    lbl_btn.pack(padx=8, pady=4)

                    def make_click_fn(k=key):
                        def on_click(e=None):
                            self.mode_selector.set("🖼️ Single View")
                            self._on_view_mode_change("🖼️ Single View")
                            self._set_current_key(k)
                        return on_click

                    lbl_btn.bind("<Button-1>", make_click_fn(key))

                    # View button
                    ctk.CTkButton(
                        card,
                        text="Inspect Full",
                        height=22,
                        font=ctk.CTkFont(size=10),
                        command=make_click_fn(key)
                    ).pack(padx=8, pady=(2, 8))
            except Exception:
                pass

    def _copy_image_to_clipboard(self):
        key = self._get_current_key()
        path = self.figs.get(key) if key else None
        if not path or not os.path.isfile(path):
            messagebox.showinfo("Clipboard", "No figure is currently active.")
            return

        try:
            if sys.platform.startswith("win"):
                # Use Windows native PowerShell Set-Clipboard without flashing console window
                abs_path = os.path.abspath(path).replace("'", "''")
                cmd = f"Set-Clipboard -Path '{abs_path}'"
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True,
                    check=True,
                    creationflags=flags
                )
                messagebox.showinfo("Success", "Figure copied to clipboard!\nYou can now paste (Ctrl+V) directly into Word, PowerPoint, or LaTeX.")
            else:
                self.clipboard_clear()
                self.clipboard_append(path)
                messagebox.showinfo("Success", f"File path copied to clipboard:\n{path}")
        except Exception as exc:
            messagebox.showerror("Clipboard Error", f"Could not copy to clipboard: {exc}")

    def _save_image_as(self):
        key = self._get_current_key()
        path = self.figs.get(key) if key else None
        if not path or not os.path.isfile(path):
            return

        dest = filedialog.asksaveasfilename(
            title="Save Publication Figure As...",
            initialfile=os.path.basename(path),
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png"), ("All Files", "*.*")]
        )
        if dest:
            try:
                if os.path.abspath(path) == os.path.abspath(dest):
                    messagebox.showinfo("Saved", f"Figure is already saved at:\n{dest}")
                    return
                shutil.copyfile(path, dest)
                messagebox.showinfo("Success", f"Figure saved to:\n{dest}")
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to save figure: {exc}")

    def _open_full_size(self) -> None:
        key = self._get_current_key()
        path = self.figs.get(key) if key else None
        if path and os.path.isfile(path):
            try:
                if sys.platform.startswith("win"):
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception:
                pass

    def _open_figures_folder(self) -> None:
        key = self._get_current_key()
        path = self.figs.get(key) if key else None
        if path:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                try:
                    if sys.platform.startswith("win"):
                        os.startfile(folder)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
                except Exception:
                    pass
