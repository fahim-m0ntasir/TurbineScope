# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Dedicated Live Monitoring Page (v2.0)
==============================================================
Full-page live monitoring dashboard for tracking running ANSYS Fluent simulations.
Features:
- Two independent timers: heavy graph refresh (default 2 min) + light file polling (default 10s).
- Live countdown display, "Refresh now", "Pause / Resume" auto-refresh.
- Real-time status strip: step, time, revs, azimuth θ, speed (steps/s), ETA, file freshness.
- Collapsible left panel: parameters in use with "Edit" shortcut, derived values, entered vs detected checks, file health list.
- Collapsible right panel: live KPI cards and timestamped alerts feed.
- Center large grid of live Matplotlib plots (2x2 default, 1x1, 2x1, 3x2, and Focus mode).
- 11 plots supported: Cp history, Cp per rev, Cp vs azimuth, Blade torque vs azimuth, Ct & Cy, Cm, Convergence, Heat map, Spectrum (FFT), y+ history, Kinematics.
- Non-blocking redraw via set_data and draw_idle, decimating to <= 5,000 points.
- Themes for both dark and light mode, filling 1280x720 without clipped titles.
"""
from __future__ import annotations

import copy
import datetime as _dt
import gc
import math
import os
import queue
import threading
import time
import traceback
from typing import Dict, List, Optional, Tuple, Any
from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import filedialog, messagebox, FigureCanvasTkAgg

import numpy as np
import matplotlib
import matplotlib.axes
from matplotlib.figure import Figure

from vawt_post.engine.live_monitor import (
    LiveMonitorWorker, LiveMonitorSnapshot, LiveFileStats
)
from vawt_post.engine.config import Params
from vawt_post.ui.components import ToolTip

GRID_LAYOUTS = ["2×2 Grid", "1×1 Single", "2×1 Stack", "3×2 Grid", "🔍 Focus Mode"]
PLOT_OPTIONS = [
    "01. Cp History",
    "02. Cp per Rev",
    "03. Cp vs Azimuth",
    "04. Blade Torque vs Azimuth",
    "05. Ct & Cy History",
    "06. Cm History",
    "07. Convergence & Spread",
    "08. Cp Heatmap",
    "09. Cp Spectrum (FFT)",
    "10. y+ History",
    "11. Kinematics (AoA, W/U, Re)",
]
INTERVAL_PRESETS = ["10 s", "30 s", "1 min", "2 min", "5 min", "10 min"]


class LiveParamsEditDialog(ctk.CTkToplevel):
    """Dedicated modal window for viewing and editing Live Monitor simulation & convergence parameters."""

    def __init__(
        self,
        parent: "LiveMonitorFrame",
        current_params: Params,
        watch_folder: Optional[str],
        on_apply
    ):
        super().__init__(parent)
        self.monitor_frame = parent
        self.watch_folder = watch_folder
        self.on_apply = on_apply
        self.orig_params = copy.deepcopy(current_params)
        self._detected_watch_folder: Optional[str] = None

        self.title("✏️ Edit Live Monitor Parameters")
        self.geometry("560x640")
        self.minsize(520, 580)
        self.transient(parent.winfo_toplevel())
        self._center_over_parent(parent.winfo_toplevel(), 560, 640)
        self.grab_set()

        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self._confirm())

        p = current_params
        self.v_U = ctk.StringVar(value=f"{p.U:g}" if p.U is not None else "8.0")
        self.v_tsr = ctk.StringVar(value=f"{p.tsr:g}" if p.tsr is not None else "3.5")
        self.v_omega = ctk.StringVar(value=f"{p.omega:g}" if p.omega is not None else "")
        self.v_R = ctk.StringVar(value=f"{p.R:g}" if p.R is not None else "0.5")
        self.v_chord = ctk.StringVar(value=f"{p.chord:g}" if p.chord is not None else "0.06")
        self.v_nb = ctk.StringVar(value=str(p.n_blades if p.n_blades is not None else 3))

        self.v_tol = ctk.StringVar(value=f"{p.stable_tol:g}" if p.stable_tol is not None else "5.0")
        self.v_nstable = ctk.StringVar(value=str(p.n_stable if p.n_stable is not None else 2))
        self.v_dt = ctk.StringVar(value=f"{p.dt:.6g}" if p.dt is not None else "")
        self.v_bin = ctk.StringVar(value=f"{p.bin_deg:g}" if p.bin_deg is not None else "1.0")

        self.v_rho = ctk.StringVar(value=f"{p.rho:g}" if p.rho is not None else "1.225")
        self.v_mu = ctk.StringVar(value=f"{p.mu:.6g}" if p.mu is not None else "1.7894e-5")
        self.v_depth = ctk.StringVar(value=f"{p.depth:g}" if p.depth is not None else "1.0")
        self.v_rot_dir = ctk.StringVar(value=getattr(p, "rotation_dir", "ccw") or "ccw")
        self.v_theta0 = ctk.StringVar(value=f"{getattr(p, 'theta0', 0.0) or 0.0:g}")

        self._build_ui()
        for v in (self.v_U, self.v_tsr, self.v_omega, self.v_R, self.v_depth, self.v_rho):
            v.trace_add("write", lambda *_: self._update_derived_preview())
        self._update_derived_preview()

    def _center_over_parent(self, parent, w: int, h: int):
        try:
            parent.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            x = max(0, px + (pw - w) // 2)
            y = max(0, py + (ph - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            hdr,
            text="✏️  Live Monitor — Simulation & Convergence Parameters",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        ).pack(anchor="w")
        folder_txt = f"Watched Folder: {self.watch_folder}" if self.watch_folder else "Watched Folder: (Not selected yet — click '📁 Watch Folder...' in Live Monitor)"
        ctk.CTkLabel(
            hdr,
            text=folder_txt,
            font=ctk.CTkFont(size=10),
            text_color="gray",
            justify="left",
            wraplength=510
        ).pack(anchor="w", pady=(2, 0))

        # Quick Load Toolbar
        top_btns = ctk.CTkFrame(self, fg_color="transparent")
        top_btns.pack(fill="x", padx=18, pady=(4, 6))
        ctk.CTkButton(
            top_btns,
            text="🔍 Auto-Detect from Watched Folder",
            height=28,
            width=210,
            fg_color=("RoyalBlue3", "#2563EB"),
            hover_color=("RoyalBlue4", "#1D4ED8"),
            command=self._detect_from_folder
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            top_btns,
            text="📥 Copy from Main Input Tab",
            height=28,
            width=185,
            fg_color="transparent",
            border_width=1,
            command=self._copy_from_input_tab
        ).pack(side="left", padx=4)

        # Bottom Action Buttons (packed before scrollable frame so it is never clipped on resize)
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=18, pady=(6, 14), side="bottom")

        ctk.CTkButton(
            btn_bar,
            text="Cancel",
            width=95,
            fg_color="transparent",
            border_width=1,
            command=self.destroy
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_bar,
            text="✅ Apply & Update Live",
            width=175,
            fg_color=("#16A34A", "#15803D"),
            hover_color=("#15803D", "#166534"),
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._confirm
        ).pack(side="right")

        # Scrollable Form Body
        scroll = ctk.CTkScrollableFrame(self, corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=18, pady=4)

        # Section 1: Operating Point & Geometry
        s1 = ctk.CTkFrame(scroll, corner_radius=8, fg_color=("gray90", "gray17"))
        s1.pack(fill="x", pady=(2, 8), padx=2)
        ctk.CTkLabel(s1, text="1. Operating Point & Rotor Geometry", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=12, pady=(8, 4))
        g1 = ctk.CTkFrame(s1, fg_color="transparent")
        g1.pack(fill="x", padx=12, pady=(0, 6))
        self._add_row(g1, 0, "Free-stream speed U", self.v_U, "m/s", "Inflow wind speed")
        self._add_row(g1, 1, "Tip speed ratio λ", self.v_tsr, "-", "TSR = ω·R / U")
        self._add_row(g1, 2, "or Rotor speed ω", self.v_omega, "rad/s", "Leave blank to derive ω = λ·U/R")
        self._add_row(g1, 3, "Rotor radius R", self.v_R, "m", "Turbine radius from axis to chord")
        self._add_row(g1, 4, "Blade chord c", self.v_chord, "m", "Blade airfoil chord length")
        self._add_row(g1, 5, "Number of blades N", self.v_nb, "blades", "Number of rotor blades (1 to 8)")

        self.lbl_derived = ctk.CTkLabel(
            s1,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("royal blue", "#60A5FA"),
            justify="left",
            wraplength=465
        )
        self.lbl_derived.pack(anchor="w", padx=12, pady=(2, 8))

        # Section 2: Convergence & Time Discretization
        s2 = ctk.CTkFrame(scroll, corner_radius=8, fg_color=("gray90", "gray17"))
        s2.pack(fill="x", pady=(0, 8), padx=2)
        ctk.CTkLabel(s2, text="2. Convergence & Discretization", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=12, pady=(8, 4))
        g2 = ctk.CTkFrame(s2, fg_color="transparent")
        g2.pack(fill="x", padx=12, pady=(0, 8))
        self._add_row(g2, 0, "Stability tolerance", self.v_tol, "%", "Max rev-to-rev ΔCp % change for convergence (default 5.0%)")
        self._add_row(g2, 1, "Required stable revs", self.v_nstable, "revs", "Consecutive stable revolutions required (default 2)")
        self._add_row(g2, 2, "Time step fallback Δt", self.v_dt, "s", "Optional fallback Δt if monitor files lack flow-time")
        self._add_row(g2, 3, "Azimuth bin width", self.v_bin, "deg", "Bin width for phase averaging (default 1.0°)")

        # Section 3: Fluid Properties & Coordinate Convention
        s3 = ctk.CTkFrame(scroll, corner_radius=8, fg_color=("gray90", "gray17"))
        s3.pack(fill="x", pady=(0, 4), padx=2)
        ctk.CTkLabel(s3, text="3. Fluid & Reference Properties", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=12, pady=(8, 4))
        g3 = ctk.CTkFrame(s3, fg_color="transparent")
        g3.pack(fill="x", padx=12, pady=(0, 8))
        self._add_row(g3, 0, "Air density ρ", self.v_rho, "kg/m³", "Fluent air default: 1.225 kg/m³")
        self._add_row(g3, 1, "Dynamic viscosity μ", self.v_mu, "Pa·s", "Fluent air default: 1.7894e-5 Pa·s")
        self._add_row(g3, 2, "2D domain depth", self.v_depth, "m", "Spanwise reference depth (default 1.0 m)")

        ctk.CTkLabel(g3, text="Rotation direction:", width=175, anchor="w").grid(row=3, column=0, sticky="w", pady=3)
        ctk.CTkOptionMenu(g3, variable=self.v_rot_dir, values=["ccw", "cw"], width=120, height=26).grid(row=3, column=1, sticky="w", padx=6, pady=3)
        self._add_row(g3, 4, "Blade 1 start θ₀", self.v_theta0, "deg", "Blade 1 azimuth at t = 0 s (0° = +y axis; auto-detected for mid-spin starts)")

    def _add_row(self, parent, row: int, label: str, var: ctk.StringVar, unit: str, hint: str):
        lbl = ctk.CTkLabel(parent, text=f"{label}:", width=175, anchor="w")
        lbl.grid(row=row, column=0, sticky="w", pady=3)
        ent = ctk.CTkEntry(parent, textvariable=var, width=120, height=26)
        ent.grid(row=row, column=1, sticky="w", padx=6, pady=3)
        u_lbl = ctk.CTkLabel(parent, text=unit, text_color="gray", anchor="w")
        u_lbl.grid(row=row, column=2, sticky="w", pady=3)
        ToolTip(ent, f"{label} ({unit}): {hint}")

    def _update_derived_preview(self):
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
                self.lbl_derived.configure(
                    text=f"⚡ Derived: ω = {omega:.2f} rad/s ({rpm:.0f} RPM)  |  λ = {tsr:.2f}  |  T_rev = {Trev:.4f} s  |  P_ref = {Pref:.1f} W"
                )
            else:
                self.lbl_derived.configure(text="")
        except Exception:
            self.lbl_derived.configure(text="")

    def _detect_from_folder(self):
        folder = self.watch_folder
        if not folder or not os.path.isdir(folder):
            chosen = filedialog.askdirectory(title="Select Simulation Folder to Detect From", parent=self)
            if chosen and os.path.isdir(chosen):
                folder = chosen
                self.watch_folder = chosen
            else:
                return
        try:
            from vawt_post.engine.parser import detect_folder_simulation_parameters, resolve_fluent_directory
            rf = resolve_fluent_directory(folder)
            detect_dir = rf if rf and os.path.isdir(rf) else folder
            self.watch_folder = detect_dir
            self._detected_watch_folder = detect_dir
            det = detect_folder_simulation_parameters(detect_dir)
            if not det:
                messagebox.showwarning(
                    "Auto-Detect Notice",
                    f"No simulation metadata or case files found in:\n{folder}\n\nPlease verify that case (.cas.h5), report.xml, or monitor files exist.",
                    parent=self
                )
                return
            applied = []
            if det.get("U") is not None:
                self.v_U.set(f"{float(det['U']):g}")
                applied.append(f"U = {float(det['U']):g} m/s")
            if det.get("R") is not None:
                self.v_R.set(f"{float(det['R']):g}")
                applied.append(f"R = {float(det['R']):g} m")
            if det.get("chord") is not None:
                self.v_chord.set(f"{float(det['chord']):g}")
                applied.append(f"c = {float(det['chord']):g} m")
            if det.get("n_blades") is not None:
                self.v_nb.set(str(int(det["n_blades"])))
                applied.append(f"N = {int(det['n_blades'])}")
            if det.get("omega") is not None:
                self.v_omega.set(f"{float(det['omega']):g}")
                applied.append(f"ω = {float(det['omega']):g} rad/s")
            if det.get("tsr") is not None:
                self.v_tsr.set(f"{float(det['tsr']):g}")
                applied.append(f"λ = {float(det['tsr']):g}")
            if det.get("rho") is not None:
                self.v_rho.set(f"{float(det['rho']):g}")
            if det.get("mu") is not None:
                self.v_mu.set(f"{float(det['mu']):.6g}")
            if det.get("dt") is not None:
                self.v_dt.set(f"{float(det['dt']):.6g}")
                applied.append(f"Δt = {float(det['dt']):.3e} s")
            if det.get("rotation_dir"):
                self.v_rot_dir.set(str(det["rotation_dir"]).lower())
                applied.append(f"Direction = {str(det['rotation_dir']).upper()}")
            if det.get("theta0") is not None:
                self.v_theta0.set(f"{float(det['theta0']):g}")
                applied.append(f"θ₀ = {float(det['theta0']):.1f}°")
            self._update_derived_preview()
            messagebox.showinfo(
                "Auto-Detection Successful",
                f"Successfully loaded parameters from:\n{rf}\n\n" + "\n".join(f" • {x}" for x in applied),
                parent=self
            )
        except Exception as exc:
            messagebox.showerror("Auto-Detect Error", f"Failed to detect parameters from folder:\n{exc}", parent=self)

    def _copy_from_input_tab(self):
        app = getattr(self.monitor_frame, "app", None)
        if not app:
            return
        for src_attr, dst_var in [
            ("v_U", self.v_U),
            ("v_tsr", self.v_tsr),
            ("v_omega", self.v_omega),
            ("v_R", self.v_R),
            ("v_chord", self.v_chord),
            ("v_nb", self.v_nb),
            ("v_tol", self.v_tol),
            ("v_nstable", self.v_nstable),
            ("v_dt", self.v_dt),
            ("v_bin", self.v_bin),
            ("v_rho", self.v_rho),
            ("v_mu", self.v_mu),
            ("v_depth", self.v_depth),
            ("v_rot_dir", self.v_rot_dir),
            ("v_theta0", self.v_theta0),
        ]:
            v_src = getattr(app, src_attr, None)
            if v_src is not None:
                val = v_src.get().strip()
                if val or src_attr in ("v_omega", "v_dt"):
                    dst_var.set(val)
        self._update_derived_preview()

    def _confirm(self):
        def _pf(var: ctk.StringVar, name: str, default=None):
            s = var.get().strip().replace(",", ".")
            if not s:
                return default
            try:
                return float(s)
            except ValueError:
                raise ValueError(f"Invalid numeric value for {name}: '{s}'")

        def _pi(var: ctk.StringVar, name: str, default=None):
            s = var.get().strip().replace(",", ".")
            if not s:
                return default
            try:
                return int(float(s))
            except ValueError:
                raise ValueError(f"Invalid integer value for {name}: '{s}'")

        try:
            u_val = _pf(self.v_U, "Free-stream speed U", None)
            tsr_val = _pf(self.v_tsr, "Tip speed ratio λ", None)
            w_val = _pf(self.v_omega, "Rotor speed ω", None)
            r_val = _pf(self.v_R, "Rotor radius R", None)
            c_val = _pf(self.v_chord, "Blade chord c", 0.06)
            nb_val = _pi(self.v_nb, "Number of blades N", 3)
            tol_val = _pf(self.v_tol, "Stability tolerance (%)", 5.0)
            nstable_val = _pi(self.v_nstable, "Required stable revolutions", 2)
            dt_val = _pf(self.v_dt, "Time step fallback Δt", None)
            bin_val = _pf(self.v_bin, "Azimuth bin width", 1.0)
            rho_val = _pf(self.v_rho, "Air density ρ", 1.225)
            mu_val = _pf(self.v_mu, "Dynamic viscosity μ", 1.7894e-5)
            depth_val = _pf(self.v_depth, "2D domain depth", 1.0)
            theta0_val = _pf(self.v_theta0, "Blade 1 start azimuth θ₀", 0.0)

            if w_val is not None and w_val > 0 and u_val and r_val:
                tsr_val = w_val * r_val / u_val

            new_p = copy.deepcopy(self.orig_params) if hasattr(self, "orig_params") and self.orig_params is not None else Params()
            new_p.U = u_val
            new_p.tsr = tsr_val
            new_p.omega = w_val
            new_p.R = r_val
            new_p.chord = c_val
            new_p.n_blades = nb_val
            new_p.stable_tol = tol_val if tol_val is not None else 5.0
            new_p.n_stable = nstable_val if nstable_val is not None else 2
            new_p.dt = dt_val
            new_p.bin_deg = bin_val if bin_val is not None else 1.0
            new_p.rho = rho_val if rho_val is not None else 1.225
            new_p.mu = mu_val if mu_val is not None else 1.7894e-5
            new_p.depth = depth_val if depth_val is not None else 1.0
            new_p.rotation_dir = self.v_rot_dir.get() or "ccw"
            new_p.theta0 = float(theta0_val if theta0_val is not None else 0.0)
            errs = new_p.validate()
            if errs:
                raise ValueError("\n".join(errs))
        except ValueError as exc:
            messagebox.showerror("Parameter Validation Error", str(exc), parent=self)
            return

        if (
            self._detected_watch_folder
            and hasattr(self, "monitor_frame")
            and self.monitor_frame
            and not getattr(self.monitor_frame, "is_monitoring", False)
            and not getattr(self.monitor_frame, "custom_watch_folder", None)
        ):
            self.monitor_frame.custom_watch_folder = self._detected_watch_folder
        self.on_apply(new_p)
        self.destroy()


class LiveMonitorFrame(ctk.CTkFrame):
    """Full-page Live Monitoring dashboard for active simulations."""

    @staticmethod
    def _compute_file_check_interval(refresh_s: float) -> float:
        """Use a 10s lightweight file check when refresh > 10s, else 2s."""
        val = float(refresh_s)
        return 10.0 if val > 10.0 else min(2.0, max(1.0, val))

    def __init__(self, master, app=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.worker: Optional[LiveMonitorWorker] = None
        self.snapshot_queue: queue.Queue = queue.Queue()
        self.is_monitoring: bool = False
        self.is_paused: bool = False
        self.latest_snapshot: Optional[LiveMonitorSnapshot] = None
        self._last_chart_snapshot: Optional[LiveMonitorSnapshot] = None
        self._snap_poll_after_id: Optional[str] = None
        self._countdown_after_id: Optional[str] = None

        # Timer & display configurations
        self.refresh_interval_s: float = 120.0  # Default 2 minutes
        self.file_check_interval_s: float = self._compute_file_check_interval(self.refresh_interval_s)
        self.window_mode: str = "All"
        self.grid_layout_mode: str = "2×2 Grid"
        self.focus_plot: str = PLOT_OPTIONS[0]
        self.custom_watch_folder: Optional[str] = None
        self.prior_source: Optional[str] = None
        self.continuity_mode: str = "auto"
        self.live_params: Optional[Params] = None
        self._manual_params_override: bool = False
        self._last_detected_folder: Optional[str] = None
        self.detected_turb_model: Optional[str] = None
        self.autoscale_pref: bool = True
        self._file_row_widgets: Dict[str, Dict[str, Any]] = {}
        self._rendered_alerts_count: int = 0
        self._cleared_alert_count: int = 0
        self._cleared_alert_seq: int = 0
        self._last_alerts_signature: Any = None
        self._recommended_theta0: Optional[float] = None
        self._ignored_midspin_folders: set = set()
        self._last_rendered_graph_state: Any = None
        self._probe_artists: Dict[Any, Dict[str, Any]] = {}
        self._twin_axes: Dict[Any, Any] = {}
        self._active_probe_ax: Any = None
        self._probe_enabled: bool = True
        self._mpl_motion_cid: Optional[int] = None
        self._mpl_leave_cid: Optional[int] = None
        self._mpl_draw_cid: Optional[int] = None
        self._blit_bg: Any = None
        self._last_hover_ts: float = 0.0
        self._last_hover_sig: Any = None
        self._probe_ui_cache: Dict[str, Any] = {}
        self._probe_visible_rows: int = 6

        if self.app and hasattr(self.app, "config_mgr"):
            try:
                saved = self.app.config_mgr.get_live_settings()
                self.refresh_interval_s = float(saved.get("refresh_interval_s", 120.0))
                self.file_check_interval_s = self._compute_file_check_interval(self.refresh_interval_s)
                self.window_mode = str(saved.get("window_mode", "All"))
                self.grid_layout_mode = str(saved.get("grid_layout", "2×2 Grid"))
                self.focus_plot = str(saved.get("focus_plot", PLOT_OPTIONS[0]))
                self.custom_watch_folder = saved.get("watched_folder") or None
                self.prior_source = saved.get("prior_source") or None
                self.continuity_mode = str(saved.get("continuity_mode", "auto") or "auto")
                self.autoscale_pref = bool(saved.get("autoscale", True))
                raw_ignored = saved.get("ignored_midspin_folders")
                if isinstance(raw_ignored, list):
                    self._ignored_midspin_folders = {str(p) for p in raw_ignored if p}
                if isinstance(saved.get("live_params"), dict):
                    try:
                        lp = saved["live_params"]
                        self.live_params = Params(
                            U=float(lp.get("U", 8.0)),
                            tsr=float(lp["tsr"]) if lp.get("tsr") is not None else 3.5,
                            omega=float(lp["omega"]) if lp.get("omega") is not None else None,
                            R=float(lp.get("R", 0.5)),
                            chord=float(lp.get("chord", 0.06)),
                            n_blades=int(lp.get("n_blades", 3)),
                            rho=float(lp.get("rho", 1.225)),
                            mu=float(lp.get("mu", 1.7894e-5)),
                            depth=float(lp.get("depth", 1.0)),
                            dt=float(lp["dt"]) if lp.get("dt") is not None else None,
                            stable_tol=float(lp.get("stable_tol", 5.0)),
                            n_stable=int(lp.get("n_stable", 2)),
                            bin_deg=float(lp.get("bin_deg", 1.0)),
                            rotation_dir=str(lp.get("rotation_dir", "ccw")),
                            theta0=float(lp.get("theta0", 0.0) or 0.0),
                        )
                        self._manual_params_override = bool(saved.get("manual_params_override", False))
                    except Exception:
                        pass
            except Exception as e:
                print(f"[LiveMonitor] Settings load warning: {e}")

        self.time_until_refresh: float = self.refresh_interval_s

        # Collapsible panel states & shutdown guard
        self.left_panel_visible: bool = True
        self.right_panel_visible: bool = True
        self._is_closing: bool = False

        self._build_ui()
        self._init_plots()

        # Defer periodic 200ms/1000ms timers until monitoring actually starts (zero idle CPU when stopped)
        if self.custom_watch_folder and os.path.isdir(self.custom_watch_folder):
            self.after(500, self._get_or_detect_params)

    def shutdown(self) -> None:
        """Halt Live Monitor worker thread, Matplotlib hooks, and periodic timers on exit."""
        self._is_closing = True
        if self.worker is not None:
            try:
                self.worker.stop()
            except Exception:
                pass
            self.worker = None
        for t in list(getattr(self, "_after_timers", {}).values()):
            try:
                t.stop()
                t.deleteLater()
            except Exception:
                pass
        if hasattr(self, "_after_timers"):
            self._after_timers.clear()
        if hasattr(self, "canvas") and self.canvas is not None:
            for cid_attr in ("_mpl_draw_cid", "_mpl_motion_cid", "_mpl_leave_cid"):
                cid = getattr(self, cid_attr, None)
                if cid is not None:
                    try:
                        self.canvas.mpl_disconnect(cid)
                    except Exception:
                        pass
                    setattr(self, cid_attr, None)
        self._probe_artists.clear()
        self._active_probe_ax = None
        self._blit_bg = None
        self.latest_snapshot = None
        if hasattr(self, "fig") and self.fig is not None:
            try:
                self.fig.clear()
                import matplotlib.pyplot as _plt
                _plt.close(self.fig)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # UI Layout Construction
    # -------------------------------------------------------------------------
    def _build_ui(self):
        # 1. Top Toolbar (Controls, Timers, Action Buttons)
        self.toolbar = ctk.CTkFrame(self, height=46, corner_radius=8, fg_color=("gray90", "gray17"))
        self.toolbar.pack(fill="x", padx=8, pady=(6, 4))

        self.btn_toggle = ctk.CTkButton(
            self.toolbar,
            text="▶ Start Monitoring",
            width=134,
            height=30,
            fg_color=("#16A34A", "#15803D"),
            hover_color=("#15803D", "#166534"),
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_monitoring
        )
        self.btn_toggle.pack(side="left", padx=(8, 4), pady=6)

        # Refresh Interval Number + Unit
        ctk.CTkLabel(self.toolbar, text="Refresh:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(6, 2))
        mins_val = int(self.refresh_interval_s // 60)
        secs_val = int(self.refresh_interval_s % 60)
        use_mins = (secs_val == 0 and mins_val > 0)
        self.interval_num_var = ctk.StringVar(value=str(mins_val if use_mins else int(self.refresh_interval_s)))
        self.interval_entry = ctk.CTkEntry(self.toolbar, textvariable=self.interval_num_var, width=40, height=28)
        self.interval_entry.pack(side="left", padx=2)
        self.interval_entry.bind("<Return>", lambda _: self._on_interval_custom_change())
        self.interval_entry.bind("<FocusOut>", lambda _: self._on_interval_custom_change())

        self.interval_unit_var = ctk.StringVar(value="minutes" if use_mins else "seconds")
        self.interval_unit_menu = ctk.CTkOptionMenu(
            self.toolbar,
            values=["seconds", "minutes"],
            variable=self.interval_unit_var,
            width=82,
            height=28,
            command=lambda _: self._on_interval_custom_change()
        )
        self.interval_unit_menu.pack(side="left", padx=2)

        # Wire live reactivity traces to update immediately on keystroke/choice
        self.interval_num_var.trace_add("write", lambda *_: self._on_interval_custom_change())
        self.interval_unit_var.trace_add("write", lambda *_: self._on_interval_custom_change())

        # Interval Quick Presets
        self.preset_interval_var = ctk.StringVar(value="Preset")
        ctk.CTkOptionMenu(
            self.toolbar,
            values=INTERVAL_PRESETS,
            variable=self.preset_interval_var,
            width=80,
            height=28,
            command=self._on_preset_interval_select
        ).pack(side="left", padx=2)

        # Countdown Timer Display
        self.lbl_countdown = ctk.CTkLabel(
            self.toolbar,
            text="⏱ Next: -",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=("royal blue", "#60A5FA"),
            width=92
        )
        self.lbl_countdown.pack(side="left", padx=(4, 4))

        # Refresh now & Pause buttons
        self.btn_refresh_now = ctk.CTkButton(
            self.toolbar,
            text="🔄 Refresh now",
            width=102,
            height=28,
            fg_color=("gray75", "gray28"),
            hover_color=("gray65", "gray35"),
            state="disabled",
            command=self._refresh_now
        )
        self.btn_refresh_now.pack(side="left", padx=2)

        self.btn_pause = ctk.CTkButton(
            self.toolbar,
            text="⏸ Pause",
            width=76,
            height=28,
            state="disabled",
            fg_color="transparent",
            border_width=1,
            command=self._toggle_pause
        )
        self.btn_pause.pack(side="left", padx=2)

        # Right Action Buttons: Watch Folder, Finalize, Snapshot, Edit Params
        self.btn_watch_folder = ctk.CTkButton(
            self.toolbar,
            text="📁 Watch Folder...",
            width=126,
            height=28,
            fg_color="transparent",
            border_width=1,
            command=self._choose_watch_folder
        )
        self.btn_watch_folder.pack(side="right", padx=(2, 8))

        self.btn_finalize = ctk.CTkButton(
            self.toolbar,
            text="🏁 Finalize",
            width=88,
            height=28,
            fg_color=("RoyalBlue3", "#2563EB"),
            hover_color=("RoyalBlue4", "#1D4ED8"),
            state="disabled",
            command=self._finalize_run
        )
        self.btn_finalize.pack(side="right", padx=2)

        self.btn_snapshot = ctk.CTkButton(
            self.toolbar,
            text="📸 Snapshot",
            width=90,
            height=28,
            state="disabled",
            command=self._take_snapshot
        )
        self.btn_snapshot.pack(side="right", padx=2)

        # 2. Status Strip (Real-time telemetry with compact spacing to prevent clipping)
        self.status_strip = ctk.CTkFrame(self, height=34, corner_radius=6, fg_color=("gray85", "gray17"))
        self.status_strip.pack(fill="x", padx=8, pady=(0, 4))

        self.state_badge = ctk.CTkLabel(
            self.status_strip,
            text="⏹ Stopped",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray",
            width=92,
            anchor="w"
        )
        self.state_badge.pack(side="left", padx=(8, 4))

        self.lbl_step = ctk.CTkLabel(self.status_strip, text="Step: -", font=ctk.CTkFont(size=11))
        self.lbl_step.pack(side="left", padx=4)

        self.lbl_time = ctk.CTkLabel(self.status_strip, text="Time: - s", font=ctk.CTkFont(size=11))
        self.lbl_time.pack(side="left", padx=4)

        self.lbl_dt = ctk.CTkLabel(self.status_strip, text="Δt: -", font=ctk.CTkFont(size=11))
        self.lbl_dt.pack(side="left", padx=4)
        self.dt_tooltip = ToolTip(self.lbl_dt, "Time Step Size (Δt)")

        self.lbl_current_phase = ctk.CTkLabel(self.status_strip, text="Phase: -", font=ctk.CTkFont(size=11, weight="bold"), text_color="#38BDF8")
        self.lbl_current_phase.pack(side="left", padx=4)
        self.phase_tooltip = ToolTip(self.lbl_current_phase, "Active Simulation Phase or Stage")

        self.lbl_revs = ctk.CTkLabel(self.status_strip, text="Revs: -", font=ctk.CTkFont(size=11))
        self.lbl_revs.pack(side="left", padx=4)

        self.lbl_azimuth = ctk.CTkLabel(self.status_strip, text="θ: -°", font=ctk.CTkFont(size=11))
        self.lbl_azimuth.pack(side="left", padx=4)

        self.lbl_speed = ctk.CTkLabel(self.status_strip, text="Speed: - steps/s", font=ctk.CTkFont(size=11))
        self.lbl_speed.pack(side="left", padx=4)

        self.lbl_eta = ctk.CTkLabel(self.status_strip, text="ETA: -", font=ctk.CTkFont(size=11))
        self.lbl_eta.pack(side="left", padx=4)

        self.lbl_last_refresh = ctk.CTkLabel(self.status_strip, text="Last Refresh: -", font=ctk.CTkFont(size=10), text_color="gray")
        self.lbl_last_refresh.pack(side="right", padx=(4, 8))

        self.lbl_fresh = ctk.CTkLabel(self.status_strip, text="Freshness: -", font=ctk.CTkFont(size=10), text_color="gray")
        self.lbl_fresh.pack(side="right", padx=4)

        # 3. Main Center Workspace (Left Panel | Center Plot Grid | Right Panel)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # --- Left Side Panel (Collapsible, width=268 with explicit wraplengths) ---
        self.left_panel = ctk.CTkFrame(self.body, width=268, corner_radius=8)
        self.left_panel.pack_propagate(False)
        self.left_panel.pack(side="left", fill="y", padx=(0, 4))

        # Left header
        self.l_hdr = ctk.CTkFrame(self.left_panel, height=28, fg_color="transparent")
        self.l_hdr.pack(fill="x", padx=6, pady=(4, 2))
        self.lbl_left_title = ctk.CTkLabel(self.l_hdr, text="Telemetry & Files", font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_left_title.pack(side="left")
        self.btn_collapse_left = ctk.CTkButton(
            self.l_hdr, text="◀", width=22, height=20, font=ctk.CTkFont(size=10),
            command=self._toggle_left_panel
        )
        self.btn_collapse_left.pack(side="right")

        self.left_scroll = ctk.CTkScrollableFrame(self.left_panel, fg_color="transparent")
        self.left_scroll.pack(fill="both", expand=True, padx=3, pady=2)

        # Parameters Echo Card
        c_echo = ctk.CTkFrame(self.left_scroll, corner_radius=6, fg_color=("gray92", "gray20"))
        c_echo.pack(fill="x", pady=3)
        echo_hdr = ctk.CTkFrame(c_echo, fg_color="transparent")
        echo_hdr.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(echo_hdr, text="Parameters in Use", font=ctk.CTkFont(size=10, weight="bold")).pack(side="left")
        ctk.CTkButton(echo_hdr, text="🔍 Detect", width=46, height=18, font=ctk.CTkFont(size=9), command=self._on_detect_button_clicked).pack(side="right", padx=(2, 0))
        ctk.CTkButton(echo_hdr, text="✏️ Edit", width=40, height=18, font=ctk.CTkFont(size=9), command=self._open_edit_params_dialog).pack(side="right")

        self.lbl_params_echo = ctk.CTkLabel(
            c_echo,
            text="U = - | R = - | N = -\nλ = - | ρ = - | Tol = 5%",
            font=ctk.CTkFont(size=10),
            anchor="w",
            justify="left",
            wraplength=218
        )
        self.lbl_params_echo.pack(fill="x", anchor="w", padx=6, pady=(0, 4))

        # Derived Values Card
        c_deriv = ctk.CTkFrame(self.left_scroll, corner_radius=6, fg_color=("gray92", "gray20"))
        c_deriv.pack(fill="x", pady=3)
        ctk.CTkLabel(c_deriv, text="Derived Physics", font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=6, pady=(4, 2))
        self.lbl_derived_echo = ctk.CTkLabel(
            c_deriv,
            text="ω: - rad/s (- rpm)\nT_rev: - s\nΔt: -\nDeg/step: - | Steps/rev: -",
            font=ctk.CTkFont(size=10),
            anchor="w",
            justify="left",
            wraplength=218
        )
        self.lbl_derived_echo.pack(fill="x", anchor="w", padx=6, pady=(0, 4))

        # Time Step Stages Card (Single or Multi-Stage Timeline)
        self.c_stages = ctk.CTkFrame(self.left_scroll, corner_radius=6, fg_color=("gray92", "gray20"))
        self.c_stages.pack(fill="x", pady=3)
        stages_hdr = ctk.CTkFrame(self.c_stages, fg_color="transparent")
        stages_hdr.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(stages_hdr, text="Time Step Stages (Δt)", font=ctk.CTkFont(size=10, weight="bold")).pack(side="left")
        self.lbl_stages_badge = ctk.CTkLabel(stages_hdr, text="1 Stage", font=ctk.CTkFont(size=9, weight="bold"), text_color="#10B981")
        self.lbl_stages_badge.pack(side="right")

        self.lbl_stages_content = ctk.CTkLabel(
            self.c_stages,
            text="Current: Δt = -\nSteps: -\nTime: -",
            font=ctk.CTkFont(size=10),
            anchor="w",
            justify="left",
            wraplength=218
        )
        self.lbl_stages_content.pack(fill="x", anchor="w", padx=6, pady=(0, 4))

        # Parameter Consistency Checks Card
        c_chk = ctk.CTkFrame(self.left_scroll, corner_radius=6, fg_color=("gray92", "gray20"))
        c_chk.pack(fill="x", pady=3)
        ctk.CTkLabel(c_chk, text="Entered vs Detected", font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=6, pady=(4, 2))
        self.lbl_checks_echo = ctk.CTkLabel(
            c_chk,
            text="Speed ω: ✓\nPhasing: ✓\nTime step: ✓",
            font=ctk.CTkFont(size=10),
            anchor="w",
            justify="left",
            wraplength=218
        )
        self.lbl_checks_echo.pack(fill="x", anchor="w", padx=6, pady=(0, 4))

        # File Health List Card
        c_files = ctk.CTkFrame(self.left_scroll, corner_radius=6, fg_color=("gray92", "gray20"))
        c_files.pack(fill="x", pady=3)
        files_hdr = ctk.CTkFrame(c_files, fg_color="transparent")
        files_hdr.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(files_hdr, text="File Health List", height=16, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left")
        self.lbl_files_badge = ctk.CTkLabel(files_hdr, text="0 files", height=16, font=ctk.CTkFont(size=9, weight="bold"), text_color="gray")
        self.lbl_files_badge.pack(side="right")
        self.files_list_box = ctk.CTkFrame(c_files, fg_color="transparent")
        self.files_list_box.pack(fill="x", padx=4, pady=(0, 4))

        # Prior Run / Backup Link Card (Crash Recovery & Stitching)
        c_prior = ctk.CTkFrame(self.left_scroll, corner_radius=6, fg_color=("gray92", "gray20"))
        c_prior.pack(fill="x", pady=3)
        prior_hdr = ctk.CTkFrame(c_prior, fg_color="transparent")
        prior_hdr.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(prior_hdr, text="Prior Run Link", height=16, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left")
        self.lbl_prior_badge = ctk.CTkLabel(
            prior_hdr,
            text="Linked" if self.prior_source else "None",
            height=16,
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#10B981" if self.prior_source else "gray",
        )
        self.lbl_prior_badge.pack(side="right")

        p_btns = ctk.CTkFrame(c_prior, fg_color="transparent")
        p_btns.pack(fill="x", padx=6, pady=(1, 3))
        ctk.CTkButton(
            p_btns, text="📂 Folder", width=72, height=22, font=ctk.CTkFont(size=9),
            command=self._browse_live_prior_folder,
        ).pack(side="left", padx=(0, 3))
        ctk.CTkButton(
            p_btns, text="🗜️ .zip", width=56, height=22, font=ctk.CTkFont(size=9),
            command=self._browse_live_prior_zip,
        ).pack(side="left", padx=(0, 3))
        ctk.CTkButton(
            p_btns, text="✕", width=24, height=22, font=ctk.CTkFont(size=9),
            fg_color="transparent", border_width=1,
            command=self._clear_live_prior,
        ).pack(side="right")

        self._live_cont_label_to_code = {
            "Auto + Recover": "auto",
            "Keep Backup": "prefer_backup",
            "Splice Only": "splice_only",
        }
        self._live_cont_code_to_label = {v: k for k, v in self._live_cont_label_to_code.items()}
        self.v_live_cont_mode = ctk.StringVar(
            value=self._live_cont_code_to_label.get(self.continuity_mode, "Auto + Recover")
        )
        self.live_cont_mode_menu = ctk.CTkOptionMenu(
            c_prior,
            values=list(self._live_cont_label_to_code.keys()),
            variable=self.v_live_cont_mode,
            height=22,
            font=ctk.CTkFont(size=9),
            command=lambda _: self._on_live_prior_mode_change(),
        )
        self.live_cont_mode_menu.pack(fill="x", padx=6, pady=(0, 3))

        init_prior_txt = (
            f"Source: {os.path.basename(self.prior_source)}"
            if self.prior_source
            else "Link backup folder or .zip to stitch restarts & recover lost steps."
        )
        self.lbl_prior_info = ctk.CTkLabel(
            c_prior,
            text=init_prior_txt,
            font=ctk.CTkFont(size=9),
            text_color="#94A3B8",
            anchor="w",
            justify="left",
            wraplength=218,
        )
        self.lbl_prior_info.pack(fill="x", anchor="w", padx=6, pady=(0, 5))

        # --- Right Side Panel (Collapsible) - Anchored to right edge first ---
        self.right_panel = ctk.CTkFrame(self.body, width=272, corner_radius=8)
        self.right_panel.pack_propagate(False)
        self.right_panel.pack(side="right", fill="y", padx=(4, 0))

        # Right header
        self.r_hdr = ctk.CTkFrame(self.right_panel, height=30, fg_color="transparent")
        self.r_hdr.pack(fill="x", padx=8, pady=(6, 4))
        self.btn_collapse_right = ctk.CTkButton(
            self.r_hdr, text="▶", width=22, height=22, font=ctk.CTkFont(size=10),
            command=self._toggle_right_panel
        )
        self.btn_collapse_right.pack(side="left")
        self.lbl_right_title = ctk.CTkLabel(
            self.r_hdr, text="Live KPIs & Probe", font=ctk.CTkFont(size=11, weight="bold")
        )
        self.lbl_right_title.pack(side="left", padx=(8, 0))

        self.right_scroll = ctk.CTkScrollableFrame(self.right_panel, fg_color="transparent")
        self.right_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # --- Center Main Plot Area - Takes remaining space between panels ---
        self.center_area = ctk.CTkFrame(self.body, corner_radius=8, fg_color=("gray95", "gray12"))
        self.center_area.pack(side="left", fill="both", expand=True)
        self.center_area.grid_columnconfigure(0, weight=1)
        self.center_area.grid_rowconfigure(2, weight=1)

        # Plot Layout & Display Controls Header Bar (compact 32px height)
        self.plot_bar = ctk.CTkFrame(self.center_area, height=32, corner_radius=6, fg_color=("gray90", "gray17"))
        self.plot_bar.pack_propagate(False)
        self.plot_bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))

        ctk.CTkLabel(self.plot_bar, text="Layout:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(6, 2))
        self.layout_menu = ctk.CTkOptionMenu(
            self.plot_bar,
            values=GRID_LAYOUTS,
            width=104,
            height=24,
            command=self._on_layout_change
        )
        self.layout_menu.set(self.grid_layout_mode)
        self.layout_menu.pack(side="left", padx=2)

        # Window Length
        ctk.CTkLabel(self.plot_bar, text="Window:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(6, 2))
        self.window_var = ctk.StringVar(value=self.window_mode)
        ctk.CTkOptionMenu(
            self.plot_bar,
            values=["All", "Last 3 revs", "Last 5 revs", "Last 10 revs"],
            variable=self.window_var,
            width=96,
            height=24,
            command=self._on_window_change
        ).pack(side="left", padx=2)

        # Autoscale Checkbox
        self.v_autoscale = ctk.BooleanVar(value=self.autoscale_pref)
        ctk.CTkCheckBox(
            self.plot_bar,
            text="Autoscale",
            variable=self.v_autoscale,
            width=80,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_autoscale_toggle
        ).pack(side="left", padx=(6, 2))

        # Inline Stability Tolerance (%) Entry
        ctk.CTkLabel(self.plot_bar, text="Tol (%):", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(4, 2))
        lp_init = self.live_params
        init_tol = f"{lp_init.stable_tol:g}" if (lp_init is not None and lp_init.stable_tol is not None) else "5.0"
        self.v_live_tol = ctk.StringVar(value=init_tol)
        self.entry_live_tol = ctk.CTkEntry(self.plot_bar, textvariable=self.v_live_tol, width=38, height=24)
        self.entry_live_tol.pack(side="left", padx=2)
        self.entry_live_tol.bind("<Return>", lambda _: self._on_live_tol_change())
        self.entry_live_tol.bind("<FocusOut>", lambda _: self._on_live_tol_change())
        ToolTip(self.entry_live_tol, "Stability tolerance (%) for rev-to-rev Cp convergence in Live Monitor (default 5.0%). Press Enter to apply.")

        self.lbl_plot_stamp = ctk.CTkLabel(self.plot_bar, text="Updated: -", font=ctk.CTkFont(size=10), text_color="gray")
        self.lbl_plot_stamp.pack(side="right", padx=(4, 6))

        # Dedicated Focus Mode Row (never overflows or squeezes plot_bar at 1280px)
        self.focus_slot = ctk.CTkFrame(self.center_area, height=30, corner_radius=6, fg_color=("gray90", "gray17"))
        self.focus_slot.pack_propagate(False)
        self.lbl_focus = ctk.CTkLabel(self.focus_slot, text="Focus Plot:", font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_focus.pack(side="left", padx=(8, 4))
        self.focus_menu = ctk.CTkOptionMenu(
            self.focus_slot,
            values=PLOT_OPTIONS,
            width=240,
            height=24,
            command=self._on_focus_plot_select
        )
        self.focus_menu.set(self.focus_plot)
        self.focus_menu.pack(side="left", padx=2, pady=3)
        self.focus_slot.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 2))
        if self.grid_layout_mode != "🔍 Focus Mode":
            self.focus_slot.grid_remove()

        # Canvas container for Matplotlib figure
        self.plot_canvas_box = ctk.CTkFrame(self.center_area, fg_color="transparent")
        self.plot_canvas_box.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)

        # Live KPI cards
        self._build_kpi_cards()

        # Interactive Graph Data Probe section + Live Alerts & Updates Feed
        self._build_probe_panel()
        self._build_alerts_panel()

    def _build_kpi_section_card(self, title: str, pady: Any = (0, 5)) -> ctk.CTkFrame:
        """Create a titled, bordered metric card for the right sidebar."""
        card = ctk.CTkFrame(
            self.right_scroll,
            corner_radius=7,
            fg_color=("gray92", "gray20"),
            border_width=1,
            border_color=("#D8DEE6", "#2D333B"),
        )
        card.pack(fill="x", pady=pady)
        ctk.CTkLabel(
            card,
            text=title,
            height=15,
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=("#475569", "#94A3B8"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(5, 3))
        ctk.CTkFrame(card, height=1, fg_color=("#DCE3EC", "#2D333B")).pack(
            fill="x", padx=10, pady=(0, 2)
        )
        return card

    def _build_kpi_cards(self):
        """Construct structured status banner and grouped live KPI readout cards."""
        self.kpi_status_card = ctk.CTkFrame(
            self.right_scroll,
            corner_radius=7,
            fg_color=("#FFFFFF", "#1B212A"),
            border_width=1,
            border_color=("#CBD5E1", "#334155"),
        )
        self.kpi_status_card.pack(fill="x", pady=(1, 5))

        self.kpi_verdict = ctk.CTkLabel(
            self.kpi_status_card,
            text="STATUS: NOT RUNNING",
            height=18,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
            anchor="w",
            wraplength=220,
        )
        self.kpi_verdict.pack(fill="x", padx=10, pady=5)

        conv_card = self._build_kpi_section_card("CONVERGENCE & POWER")
        self.kpi_cp = self._create_kpi_chip(conv_card, "Running Mean Cp", "0.0000", "#10B981", value_size=12)
        self.kpi_dcp = self._create_kpi_chip(conv_card, "ΔCp vs Prev Rev", "+0.00%", ("#334155", "#CBD5E1"))
        self.kpi_stable = self._create_kpi_chip(conv_card, "Stable Revolutions", "0", ("#2563EB", "#38BDF8"))
        self.kpi_extra_revs = self._create_kpi_chip(
            conv_card, "Revs to Stability", "-", ("#334155", "#CBD5E1"), bottom_divider=False
        )

        aero_card = self._build_kpi_section_card("AERODYNAMICS & FORECAST")
        self.kpi_peak = self._create_kpi_chip(aero_card, "Peak Torque", "0.00 Nm", ("#1E293B", "#E2E8F0"))
        self.kpi_ct = self._create_kpi_chip(aero_card, "Thrust Coeff (Ct)", "0.0000", ("#1E293B", "#E2E8F0"))
        self.kpi_ripple = self._create_kpi_chip(aero_card, "Cp Torque Ripple", "0.0%", ("#1E293B", "#E2E8F0"))
        self.kpi_aitken = self._create_kpi_chip(
            aero_card, "Aitken Asymptotic Cp", "-", ("#4F46E5", "#818CF8"), bottom_divider=False
        )

    def _create_kpi_chip(
        self,
        parent,
        label: str,
        value: str,
        color: Any,
        *,
        value_size: int = 11,
        bottom_divider: bool = True,
    ) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color="transparent")
        card.pack(fill="x", padx=10, pady=(2, 2 if bottom_divider else 5))
        ctk.CTkLabel(
            card,
            text=label,
            height=17,
            font=ctk.CTkFont(size=10),
            text_color=("#64748B", "#9CA3AF"),
            anchor="w",
        ).pack(side="left", padx=(0, 6))
        v_lbl = ctk.CTkLabel(
            card,
            text=value,
            height=17,
            font=ctk.CTkFont(size=value_size, weight="bold"),
            text_color=color,
            anchor="e",
        )
        v_lbl.pack(side="right")
        if bottom_divider:
            ctk.CTkFrame(parent, height=1, fg_color=("#E2E8F0", "#2A3039")).pack(
                fill="x", padx=10
            )
        return v_lbl

    def _build_probe_panel(self):
        """Construct a structured, metric-card Data Probe section with a 'Probe' toggle button and live row readouts."""
        self.probe_card = ctk.CTkFrame(
            self.right_scroll,
            corner_radius=7,
            fg_color=("gray92", "gray20"),
            border_width=1,
            border_color=("#D8DEE6", "#2D333B"),
        )
        self.probe_card.pack(fill="x", pady=(0, 4))

        # Top header bar inside card: title on left, "Probe" toggle button on right
        hdr = ctk.CTkFrame(self.probe_card, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(6, 4))

        ctk.CTkLabel(
            hdr,
            text="GRAPH DATA PROBE",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=("#475569", "#94A3B8"),
            anchor="w",
        ).pack(side="left")

        self.btn_probe_toggle = ctk.CTkButton(
            hdr,
            text="Probe: ON" if self._probe_enabled else "Probe: OFF",
            width=78,
            height=22,
            corner_radius=6,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("#10B981", "#059669") if self._probe_enabled else ("gray72", "gray30"),
            hover_color=("#059669", "#047857") if self._probe_enabled else ("gray62", "gray38"),
            text_color="#FFFFFF" if self._probe_enabled else ("gray20", "gray80"),
            command=self._toggle_probe,
        )
        self.btn_probe_toggle.pack(side="right")
        ToolTip(self.btn_probe_toggle, "Toggle interactive hover probe on all Live Monitor graphs.")

        # Thin horizontal divider
        ctk.CTkFrame(self.probe_card, height=1, fg_color=("#DCE3EC", "#2D333B")).pack(
            fill="x", padx=10, pady=(0, 4)
        )

        # Active Subplot Badge & Coordinate Header
        self.lbl_probe_graph = ctk.CTkLabel(
            self.probe_card,
            text="Ready — Hover any graph" if self._probe_enabled else "Probe Disabled",
            height=17,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#2563EB", "#38BDF8") if self._probe_enabled else ("#64748B", "#94A3B8"),
            anchor="w",
            justify="left",
            wraplength=224,
        )
        self.lbl_probe_graph.pack(fill="x", padx=10, pady=(0, 1))

        self.lbl_probe_coord = ctk.CTkLabel(
            self.probe_card,
            text="Position: —",
            height=16,
            font=ctk.CTkFont(size=10),
            text_color=("#64748B", "#9CA3AF"),
            anchor="w",
            justify="left",
            wraplength=224,
        )
        self.lbl_probe_coord.pack(fill="x", padx=10, pady=(0, 5))

        # Structured key-value rows container (matches KPI card rows)
        self.probe_rows_frame = ctk.CTkFrame(
            self.probe_card,
            corner_radius=6,
            fg_color=("#FFFFFF", "#151A22"),
            border_width=1,
            border_color=("#D8DEE6", "#2A3039"),
        )
        self.probe_rows_frame.pack(fill="x", padx=10, pady=(0, 8))

        self._probe_row_labels: List[Tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        self._probe_row_frames: List[Any] = []
        self._probe_dividers: List[Any] = []
        max_rows = 6
        for idx in range(max_rows):
            r_frame = ctk.CTkFrame(self.probe_rows_frame, fg_color="transparent")
            r_frame.pack(fill="x", padx=8, pady=(4 if idx == 0 else 2, 4 if idx == max_rows - 1 else 2))
            k_lbl = ctk.CTkLabel(
                r_frame,
                text="—",
                height=16,
                font=ctk.CTkFont(size=10),
                text_color=("#64748B", "#9CA3AF"),
                anchor="w",
                wraplength=106,
            )
            k_lbl.pack(side="left", padx=(0, 6))
            v_lbl = ctk.CTkLabel(
                r_frame,
                text="—",
                height=16,
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                text_color=("gray20", "gray85"),
                anchor="e",
                wraplength=116,
            )
            v_lbl.pack(side="right")
            self._probe_row_labels.append((k_lbl, v_lbl))
            self._probe_row_frames.append(r_frame)
            if idx < max_rows - 1:
                div = ctk.CTkFrame(self.probe_rows_frame, height=1, fg_color=("#E2E8F0", "#252B34"))
                div.pack(fill="x", padx=8)
                self._probe_dividers.append(div)

        # Lightweight text mirror for programmatic readout compatibility (no QTextEdit overhead)
        class _ProbeTextMirror:
            def __init__(self) -> None:
                self._buf = ""

            def configure(self, **_: Any) -> None:
                pass

            def delete(self, *_: Any) -> None:
                self._buf = ""

            def insert(self, _idx: Any, txt: Any) -> None:
                self._buf += str(txt)

            def get(self, *_: Any) -> str:
                return self._buf

        self.probe_box = _ProbeTextMirror()
        self._set_probe_panel_idle()

    def _build_alerts_panel(self):
        """Construct the ALERTS & UPDATES card under Graph Data Probe."""
        self.alerts_card = ctk.CTkFrame(
            self.right_scroll,
            corner_radius=7,
            fg_color=("gray92", "gray20"),
            border_width=1,
            border_color=("#D8DEE6", "#2D333B"),
        )
        self.alerts_card.pack(fill="x", pady=(0, 5))

        hdr = ctk.CTkFrame(self.alerts_card, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(6, 4))

        ctk.CTkLabel(
            hdr,
            text="ALERTS & UPDATES",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=("#475569", "#94A3B8"),
            anchor="w",
        ).pack(side="left")

        self.btn_alerts_clear = ctk.CTkButton(
            hdr,
            text="Clear",
            width=40,
            height=20,
            corner_radius=5,
            font=ctk.CTkFont(size=9, weight="bold"),
            fg_color=("gray80", "#2A3039"),
            hover_color=("gray70", "#374151"),
            text_color=("gray20", "gray85"),
            command=self._clear_alerts,
        )
        self.btn_alerts_clear.pack(side="right")

        self.lbl_alerts_badge = ctk.CTkLabel(
            hdr,
            text="0",
            width=28,
            height=20,
            corner_radius=5,
            font=ctk.CTkFont(size=9, weight="bold"),
            fg_color=("gray82", "#252B34"),
            text_color=("#64748B", "#9CA3AF"),
            anchor="center",
            justify="center",
        )
        self.lbl_alerts_badge.pack(side="right", padx=(2, 4))

        # Contextual Mid-Spin Azimuth action banner (shown only when θ0 offset needs correction)
        self.midspin_banner = ctk.CTkFrame(
            self.alerts_card,
            corner_radius=6,
            fg_color=("#FFF7ED", "#2A1F14"),
            border_width=1,
            border_color=("#F59E0B", "#B45309"),
        )
        self.lbl_midspin_msg = ctk.CTkLabel(
            self.midspin_banner,
            text="",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#B45309", "#FBBF24"),
            justify="left",
            anchor="w",
            wraplength=192,
        )
        self.lbl_midspin_msg.pack(fill="x", padx=8, pady=(5, 3))
        midspin_btn_row = ctk.CTkFrame(self.midspin_banner, fg_color="transparent")
        midspin_btn_row.pack(fill="x", padx=8, pady=(0, 6))
        self.btn_apply_theta0 = ctk.CTkButton(
            midspin_btn_row,
            text="Apply Detected θ₀",
            height=22,
            corner_radius=5,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("#D97706", "#B45309"),
            hover_color=("#B45309", "#92400E"),
            text_color="#FFFFFF",
            command=self._apply_detected_theta0,
        )
        self.btn_apply_theta0.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_ignore_theta0 = ctk.CTkButton(
            midspin_btn_row,
            text="Ignore",
            width=54,
            height=22,
            corner_radius=5,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("gray82", "#334155"),
            hover_color=("gray72", "#475569"),
            text_color=("gray20", "#E2E8F0"),
            command=self._ignore_detected_theta0,
        )
        self.btn_ignore_theta0.pack(side="right")

        # Feed list container
        self.alerts_feed_box = ctk.CTkFrame(
            self.alerts_card,
            corner_radius=6,
            fg_color=("#FFFFFF", "#151A22"),
            border_width=1,
            border_color=("#D8DEE6", "#2A3039"),
        )
        self.alerts_feed_box.pack(fill="x", padx=10, pady=(0, 8))
        self._render_empty_alerts_placeholder()

    def _render_empty_alerts_placeholder(self):
        if not hasattr(self, "alerts_feed_box"):
            return
        for ch in list(self.alerts_feed_box.winfo_children()):
            try:
                ch.destroy()
            except Exception:
                pass
        ctk.CTkLabel(
            self.alerts_feed_box,
            text="No active alerts — start monitoring for live solver, θ₀ & convergence updates.",
            font=ctk.CTkFont(size=10),
            text_color=("#64748B", "#8B949E"),
            justify="left",
            anchor="w",
            wraplength=192,
        ).pack(fill="x", padx=8, pady=8)

    def _clear_alerts(self):
        """Hide alerts received so far without stopping future incoming alerts (even at 100-alert cap)."""
        cur_alerts = list(self.latest_snapshot.alerts) if (self.latest_snapshot and self.latest_snapshot.alerts) else []
        self._cleared_alert_count = len(cur_alerts)
        if cur_alerts:
            seqs = [int(a.get("seq", 0)) for a in cur_alerts if a.get("seq") is not None]
            if seqs:
                self._cleared_alert_seq = max(self._cleared_alert_seq, max(seqs))
        self._last_alerts_signature = None
        if self.latest_snapshot is not None:
            self._update_alerts_feed(self.latest_snapshot)
        else:
            if hasattr(self, "lbl_alerts_badge"):
                self.lbl_alerts_badge.configure(text="0", fg_color=("gray82", "#252B34"), text_color=("#64748B", "#9CA3AF"))
            self._render_empty_alerts_placeholder()

    def _midspin_ignore_key(self) -> str:
        folder = getattr(self, "custom_watch_folder", None)
        if not folder:
            return "__default__"
        try:
            return os.path.normcase(os.path.abspath(folder))
        except Exception:
            return str(folder)

    def _ignore_detected_theta0(self):
        """Suppress recurring mid-spin start alerts and banner for the current simulation folder."""
        self._ignored_midspin_folders.add(self._midspin_ignore_key())
        self._save_settings()
        self._last_alerts_signature = None
        if self.latest_snapshot is not None:
            self._update_alerts_feed(self.latest_snapshot)
        elif hasattr(self, "midspin_banner") and self.midspin_banner.winfo_manager():
            self.midspin_banner.pack_forget()

    def _apply_detected_theta0(self):
        """Apply auto-detected Blade 1 start azimuth θ0 to active Live Monitor parameters."""
        if self._recommended_theta0 is None:
            return
        p = copy.deepcopy(self.live_params) if self.live_params is not None else self._get_or_detect_params()
        p.theta0 = round(float(self._recommended_theta0), 2)
        p.param_sources["theta0"] = "mid-spin detected (live)"
        self._apply_edited_params(p)
        if self.worker:
            self.worker.add_alert("SUCCESS", f"Applied θ₀ = {p.theta0:.1f}° to Live Monitor.")

    def _update_alerts_feed(self, snap: Optional[LiveMonitorSnapshot]):
        """Refresh the ALERTS & UPDATES card with caching to prevent widget churn on idle ticks."""
        if not hasattr(self, "alerts_feed_box"):
            return

        midspin_ignored = self._midspin_ignore_key() in getattr(self, "_ignored_midspin_folders", set())
        alerts_all = list(snap.alerts) if (snap and snap.alerts) else []
        has_seq = any(a.get("seq") is not None for a in alerts_all)
        if has_seq:
            visible = [a for a in alerts_all if int(a.get("seq", 0)) > getattr(self, "_cleared_alert_seq", 0)]
        else:
            if len(alerts_all) < self._cleared_alert_count:
                self._cleared_alert_count = 0
            visible = alerts_all[self._cleared_alert_count:]
        if midspin_ignored:
            visible = [a for a in visible if a.get("key") != "midspin_detected"]
        recent = list(reversed(visible))[:12]

        saz = snap.start_azimuth if (snap and isinstance(snap.start_azimuth, dict)) else None
        needs_corr = bool(
            not midspin_ignored
            and saz
            and saz.get("needs_correction")
            and saz.get("recommended_theta0_deg") is not None
        )
        rec_deg = float(saz["recommended_theta0_deg"]) if (needs_corr and saz is not None) else None
        self._recommended_theta0 = rec_deg
        midspin_sig = (needs_corr, round(rec_deg, 1) if rec_deg is not None else None)

        alerts_sig = (
            tuple((str(a.get("time", "")), str(a.get("severity", "INFO")), str(a.get("message", ""))) for a in recent),
            midspin_sig,
            len(visible),
        )
        if alerts_sig == self._last_alerts_signature:
            return
        self._last_alerts_signature = alerts_sig
        self._rendered_alerts_count = len(visible)

        # Update mid-spin quick-apply banner
        if hasattr(self, "midspin_banner"):
            if needs_corr and rec_deg is not None:
                conf = str((saz or {}).get("confidence", "")).upper()
                self.lbl_midspin_msg.configure(
                    text=f"Mid-spin start ({conf}): Blade 1 θ₀ ≈ {rec_deg:.1f}° (curves shifted)."
                )
                self.btn_apply_theta0.configure(text=f"Apply θ₀ = {rec_deg:.1f}°")
                if not self.midspin_banner.winfo_manager():
                    self.alerts_feed_box.pack_forget()
                    self.midspin_banner.pack(fill="x", padx=10, pady=(0, 6))
                    self.alerts_feed_box.pack(fill="x", padx=10, pady=(0, 8))
            elif self.midspin_banner.winfo_manager():
                self.midspin_banner.pack_forget()

        # Severity counts for visible badge
        n_err = sum(1 for a in visible if str(a.get("severity", "")).upper() == "ERROR")
        n_warn = sum(1 for a in visible if str(a.get("severity", "")).upper() == "WARNING")
        if hasattr(self, "lbl_alerts_badge"):
            if n_err > 0:
                self.lbl_alerts_badge.configure(text=f"{len(visible)}", fg_color=("#FEE2E2", "#451A1A"), text_color="#EF4444")
            elif n_warn > 0:
                self.lbl_alerts_badge.configure(text=f"{len(visible)}", fg_color=("#FEF3C7", "#3D2B10"), text_color="#F59E0B")
            elif visible:
                self.lbl_alerts_badge.configure(text=f"{len(visible)}", fg_color=("#DCFCE7", "#143326"), text_color="#10B981")
            else:
                self.lbl_alerts_badge.configure(text="0", fg_color=("gray82", "#252B34"), text_color=("#64748B", "#9CA3AF"))

        if not recent:
            self._render_empty_alerts_placeholder()
            return

        for ch in list(self.alerts_feed_box.winfo_children()):
            try:
                ch.destroy()
            except Exception:
                pass

        sev_meta = {
            "ERROR": ("✖", "#EF4444"),
            "WARNING": ("▲", "#F59E0B"),
            "SUCCESS": ("✓", "#10B981"),
            "INFO": ("●", "#3B82F6"),
        }
        for idx, al in enumerate(recent):
            sev = str(al.get("severity", "INFO")).upper()
            icon, col = sev_meta.get(sev, ("●", "#3B82F6"))
            t_str = str(al.get("time", ""))
            msg = str(al.get("message", ""))

            row = ctk.CTkFrame(self.alerts_feed_box, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=(5 if idx == 0 else 3, 5 if idx == len(recent) - 1 else 3))

            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x")
            ctk.CTkLabel(
                top,
                text=f"{icon} {sev}",
                height=14,
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color=col,
                anchor="w",
            ).pack(side="left")
            if t_str:
                ctk.CTkLabel(
                    top,
                    text=t_str,
                    height=14,
                    font=ctk.CTkFont(family="Consolas", size=9),
                    text_color=("#64748B", "#94A3B8"),
                    anchor="e",
                ).pack(side="right")

            ctk.CTkLabel(
                row,
                text=msg,
                font=ctk.CTkFont(size=10),
                text_color=("#1E293B", "#E2E8F0"),
                justify="left",
                anchor="w",
                wraplength=188,
            ).pack(fill="x", pady=(1, 0))

            if idx < len(recent) - 1:
                ctk.CTkFrame(self.alerts_feed_box, height=1, fg_color=("#E2E8F0", "#252B34")).pack(fill="x", padx=8)

    def _update_probe_rows(self, rows: List[Tuple[str, str, str]], summary_text: str = ""):
        """Populate the structured key-value probe rows cleanly without divider orphaning."""
        if hasattr(self, "_probe_row_labels"):
            max_rows = len(self._probe_row_frames)
            show_cnt = max(min(len(rows), max_rows), 4)
            if getattr(self, "_probe_visible_rows", max_rows) != show_cnt:
                for rf in self._probe_row_frames:
                    rf.pack_forget()
                for dv in getattr(self, "_probe_dividers", []):
                    dv.pack_forget()
                for idx_p in range(show_cnt):
                    self._probe_row_frames[idx_p].pack(
                        fill="x", padx=8, pady=(4 if idx_p == 0 else 2, 4 if idx_p == show_cnt - 1 else 2)
                    )
                    if idx_p < show_cnt - 1 and idx_p < len(getattr(self, "_probe_dividers", [])):
                        self._probe_dividers[idx_p].pack(fill="x", padx=8)
                self._probe_visible_rows = show_cnt

            for idx, (k_lbl, v_lbl) in enumerate(self._probe_row_labels):
                if idx < len(rows):
                    k_txt, v_txt, v_col = rows[idx]
                    target_k = (k_txt, ("gray35", "gray70"))
                    target_v = (v_txt, v_col)
                else:
                    target_k = ("", "gray")
                    target_v = ("", "gray")
                if self._probe_ui_cache.get(f"k_{idx}") != target_k:
                    self._probe_ui_cache[f"k_{idx}"] = target_k
                    k_lbl.configure(text=target_k[0], text_color=target_k[1])
                if self._probe_ui_cache.get(f"v_{idx}") != target_v:
                    self._probe_ui_cache[f"v_{idx}"] = target_v
                    v_lbl.configure(text=target_v[0], text_color=target_v[1])
        if hasattr(self, "probe_box") and self._probe_ui_cache.get("summary") != summary_text:
            self._probe_ui_cache["summary"] = summary_text
            self.probe_box.configure(state="normal")
            self.probe_box.delete("1.0", "end")
            self.probe_box.insert("1.0", summary_text)
            self.probe_box.configure(state="disabled")

    def _set_probe_panel_idle(self):
        """Reset the right-panel Data Probe display to its ready/off state."""
        if not hasattr(self, "lbl_probe_graph"):
            return
        if self._probe_enabled:
            if self._probe_ui_cache.get("graph") != "__idle__":
                self._probe_ui_cache["graph"] = "__idle__"
                self._probe_ui_cache["coord"] = "__idle__"
                self.lbl_probe_graph.configure(text="Ready — Hover any graph", text_color=("#2563EB", "#38BDF8"))
                self.lbl_probe_coord.configure(text="Move cursor over a subplot curve")
            idle_rows = [
                ("X Coordinate", "—", "gray"),
                ("Primary Value", "—", "gray"),
                ("Secondary / Δ", "—", "gray"),
                ("Phase / Rev", "—", "gray"),
            ]
            self._update_probe_rows(idle_rows, "Ready to probe")
        else:
            if self._probe_ui_cache.get("graph") != "__off__":
                self._probe_ui_cache["graph"] = "__off__"
                self._probe_ui_cache["coord"] = "__off__"
                self.lbl_probe_graph.configure(text="Probe Disabled", text_color="gray")
                self.lbl_probe_coord.configure(text="Click 'Probe: OFF' to enable")
            off_rows = [
                ("Status", "OFF", "gray"),
                ("X Coordinate", "—", "gray"),
                ("Primary Value", "—", "gray"),
                ("Phase / Rev", "—", "gray"),
            ]
            self._update_probe_rows(off_rows, "Probe is OFF")

    def _toggle_probe(self):
        """Toggle the interactive graph data probe on or off."""
        self._probe_enabled = not self._probe_enabled
        if hasattr(self, "btn_probe_toggle"):
            if self._probe_enabled:
                self.btn_probe_toggle.configure(
                    text="Probe: ON",
                    fg_color=("#10B981", "#059669"),
                    hover_color=("#059669", "#047857"),
                    text_color="#FFFFFF"
                )
            else:
                self.btn_probe_toggle.configure(
                    text="Probe: OFF",
                    fg_color=("gray72", "gray30"),
                    hover_color=("gray62", "gray38"),
                    text_color=("gray20", "gray80")
                )
        if not self._probe_enabled:
            self._hide_all_probes(redraw=True)
        self._last_hover_ts = 0.0
        self._last_hover_sig = None
        self._set_probe_panel_idle()

    # -------------------------------------------------------------------------
    # Matplotlib Figure Initialization & Layouts
    # -------------------------------------------------------------------------
    def _init_plots(self):
        """Create Matplotlib figure canvas (or reuse existing canvas) with dark styling."""
        bg_col = "#141414"

        if not hasattr(self, "fig") or self.fig is None or not hasattr(self, "canvas") or self.canvas is None:
            self.fig = Figure(figsize=(8, 6), dpi=100, constrained_layout=False)
            self.fig.patch.set_facecolor(bg_col)

            self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_canvas_box)
            self.canvas_widget = self.canvas.get_tk_widget()
            self.canvas_widget.configure(bg=bg_col, highlightthickness=0, bd=0)
            self.canvas_widget.pack(fill="both", expand=True)

            # Bind interactive probe hover & blit cache events on the Matplotlib canvas
            self._mpl_draw_cid = self.canvas.mpl_connect("draw_event", self._on_canvas_draw_event)
            self._mpl_motion_cid = self.canvas.mpl_connect("motion_notify_event", self._on_plot_hover)
            self._mpl_leave_cid = self.canvas.mpl_connect("figure_leave_event", self._on_plot_leave)
        else:
            self.fig.patch.set_facecolor(bg_col)
            try:
                self.canvas_widget.configure(bg=bg_col)
            except Exception:
                pass

        self._rebuild_plot_axes()

    def _rebuild_plot_axes(self):
        """Reconstruct plot axes according to active grid layout mode with fixed, non-shifting margins."""
        self.fig.clear()
        self._probe_artists.clear()
        self._active_probe_ax = None
        self._blit_bg = None
        self._last_hover_sig = None
        self._last_rendered_graph_state = None
        try:
            self.fig.set_layout_engine("none")
        except Exception:
            pass
        bg_fig = "#141414"
        bg_ax = "#1A1A1A"
        text_col = "#FFFFFF"
        grid_col = "gray"
        self.fig.patch.set_facecolor(bg_fig)
        if hasattr(self, "canvas_widget") and self.canvas_widget is not None:
            try:
                self.canvas_widget.configure(bg=bg_fig)
            except Exception:
                pass

        self.axes: List[matplotlib.axes.Axes] = []
        if hasattr(self, "_twin_axes"):
            self._twin_axes.clear()

        if self.grid_layout_mode == "2×2 Grid":
            for i in range(1, 5):
                self.axes.append(self.fig.add_subplot(2, 2, i))
            self.plot_names = ["01. Cp History", "02. Cp per Rev", "03. Cp vs Azimuth", "07. Convergence & Spread"]
            self.fig.subplots_adjust(left=0.095, right=0.975, top=0.92, bottom=0.10, wspace=0.25, hspace=0.46)
        elif self.grid_layout_mode == "1×1 Single":
            self.axes = [self.fig.add_subplot(1, 1, 1)]
            self.plot_names = ["01. Cp History"]
            self.fig.subplots_adjust(left=0.110, right=0.975, top=0.91, bottom=0.11)
        elif self.grid_layout_mode == "2×1 Stack":
            self.axes = [self.fig.add_subplot(2, 1, 1), self.fig.add_subplot(2, 1, 2)]
            self.plot_names = ["01. Cp History", "03. Cp vs Azimuth"]
            self.fig.subplots_adjust(left=0.110, right=0.975, top=0.92, bottom=0.10, hspace=0.45)
        elif self.grid_layout_mode == "3×2 Grid":
            for i in range(1, 7):
                self.axes.append(self.fig.add_subplot(3, 2, i))
            self.plot_names = [
                "01. Cp History", "02. Cp per Rev", "03. Cp vs Azimuth",
                "04. Blade Torque vs Azimuth", "05. Ct & Cy History", "07. Convergence & Spread"
            ]
            self.fig.subplots_adjust(left=0.095, right=0.975, top=0.93, bottom=0.085, wspace=0.26, hspace=0.60)
        elif self.grid_layout_mode == "🔍 Focus Mode":
            self.axes = [self.fig.add_subplot(1, 1, 1)]
            self.plot_names = [self.focus_plot]
            r_marg = 0.915 if "Kinematics" in (self.focus_plot or "") else 0.975
            self.fig.subplots_adjust(left=0.110, right=r_marg, top=0.91, bottom=0.11)

        title_fs = 7.5 if len(self.axes) >= 6 else (8.0 if len(self.axes) >= 4 else 9.0)
        for ax, p_name in zip(self.axes, self.plot_names):
            ax.set_facecolor(bg_ax)
            ax.set_title(p_name, color=text_col, fontsize=title_fs, weight="bold", pad=4)
            ax.tick_params(colors=text_col, labelsize=8, pad=2)
            ax.grid(True, linestyle="--", alpha=0.3, color=grid_col)
            for spine in ax.spines.values():
                spine.set_color(grid_col)

        self.canvas.draw_idle()

    @staticmethod
    def _overlay_continuity_cues(
        ax: Any,
        ts: Dict[str, Any],
        t_plot: np.ndarray,
        mask: Any = None,
        y_plot: Any = None,
    ) -> None:
        """Render resume boundary markers and shaded recovered-gap bands on live time-history plots."""
        if len(t_plot) == 0:
            return
        t_min, t_max = float(t_plot[0]), float(t_plot[-1])
        rec_ranges = ts.get("recovered_time_ranges") or []
        splice_ts = ts.get("splice_times") or []

        if rec_ranges:
            for idx_r, (ta, tb) in enumerate(rec_ranges):
                if float(tb) >= t_min and float(ta) <= t_max:
                    ax.axvspan(
                        float(ta), float(tb), color="#F59E0B", alpha=0.20, lw=0,
                        label="Recovered gap" if idx_r == 0 else None,
                    )
                    ax.text(
                        0.5 * (float(ta) + float(tb)), 0.94, "Recovered",
                        color="#F59E0B", fontsize=6.8, ha="center", va="top",
                        transform=ax.get_xaxis_transform(), clip_on=True,
                    )
            rm = ts.get("recovered_mask")
            if y_plot is not None and rm is not None:
                rm_np = np.asarray(rm, dtype=bool)[mask] if mask is not None else np.asarray(rm, dtype=bool)
                if len(rm_np) == len(t_plot) and np.any(rm_np):
                    ax.plot(t_plot[rm_np], np.asarray(y_plot)[rm_np], color="#F59E0B", ls="--", lw=1.4, label="_nolegend_")
        elif splice_ts:
            for ts_val in splice_ts:
                if t_min <= float(ts_val) <= t_max:
                    ax.axvline(float(ts_val), color="#06B6D4", ls=":", lw=1.1, alpha=0.85)
                    ax.text(
                        float(ts_val), 0.96, " Resume",
                        color="#06B6D4", fontsize=6.8, rotation=90,
                        va="top", ha="left", transform=ax.get_xaxis_transform(), clip_on=True,
                    )

    # -------------------------------------------------------------------------
    # Graph Redrawing (Non-blocking, High Performance) & Interactive Hover Probe
    # -------------------------------------------------------------------------
    def _render_graphs(self, snap: LiveMonitorSnapshot, force: bool = False):
        """Update visible matplotlib graphs without recreating figures."""
        if self.app and hasattr(self.app, "current_page") and self.app.current_page != "live":
            return

        tol_sig = float(self.live_params.stable_tol) if (self.live_params and self.live_params.stable_tol is not None) else 5.0
        rev_hist_all = getattr(snap, "rev_history", None) or []
        ts_meta = getattr(snap, "timeseries", None) or {}
        cont_meta = getattr(snap, "continuity", None) or {}
        lp_ref = self.live_params or getattr(snap, "params", None)
        lp_sig = (
            round(float(getattr(lp_ref, "U", 0.0) or 0.0), 6),
            round(float(getattr(lp_ref, "tsr", 0.0) or 0.0), 6),
            round(float(getattr(lp_ref, "omega", 0.0) or 0.0), 6),
            round(float(getattr(lp_ref, "R", 0.0) or 0.0), 6),
            round(float(getattr(lp_ref, "chord", 0.0) or 0.0), 6),
            round(float(getattr(lp_ref, "rho", 0.0) or 0.0), 6),
            round(float(getattr(lp_ref, "mu", 0.0) or 0.0), 8),
            round(float(getattr(lp_ref, "static_stall_deg", 12.0) or 12.0), 2),
            round(float(getattr(lp_ref, "theta0", 0.0) or 0.0), 4),
            str(getattr(lp_ref, "rotation_dir", "ccw") or "ccw"),
        ) if lp_ref is not None else ()
        t_arr = ts_meta.get("t")
        mb_list = ts_meta.get("Mb")
        state_sig = (
            getattr(snap, "current_step", 0),
            round(float(getattr(snap, "current_time", 0.0) or 0.0), 6),
            len(rev_hist_all),
            int(rev_hist_all[-1].get("k", 0)) if rev_hist_all else 0,
            int(len(t_arr)) if t_arr is not None else 0,
            bool(ts_meta.get("ct") is not None),
            bool(ts_meta.get("cy") is not None),
            int(len(mb_list)) if mb_list is not None else 0,
            str(cont_meta.get("status", "")),
            int(cont_meta.get("recovered_count", 0) or 0),
            self.grid_layout_mode,
            self.focus_plot,
            self.window_mode,
            bool(self.v_autoscale.get()),
            tol_sig,
            lp_sig,
            self.prior_source or "",
            self.continuity_mode,
        )
        if not force and self._last_rendered_graph_state == state_sig:
            return
        self._last_rendered_graph_state = state_sig
        self._probe_artists.clear()
        self._twin_axes.clear()
        self._active_probe_ax = None
        self._blit_bg = None
        self._last_hover_sig = None

        from matplotlib.ticker import MaxNLocator, ScalarFormatter
        if not hasattr(self, "_saved_axis_limits"):
            self._saved_axis_limits: Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]] = {}
        # Remove any previous twin axes created in earlier renders (e.g. Plot 11 W/U)
        primary_set = set(self.axes)
        for extra_ax in list(self.fig.axes):
            if extra_ax not in primary_set:
                try:
                    self.fig.delaxes(extra_ax)
                except Exception:
                    pass

        text_col = "#FFFFFF"
        grid_col = "gray"
        time_stamp = _dt.datetime.now().strftime("%H:%M:%S")
        title_fs = 7.5 if len(self.axes) >= 6 else (8.0 if len(self.axes) >= 4 else 9.0)

        # Compute window length time cut if applicable
        ts = snap.timeseries or {}
        t_arr = ts.get("t", [])
        mask = None
        if len(t_arr) > 0 and self.window_mode != "All":
            omega_val = 0.0
            if self.live_params is not None:
                try:
                    omega_val = self.live_params.omega_val()
                except Exception:
                    omega_val = 0.0
            if omega_val <= 0:
                try:
                    omega_str = str(snap.parameter_checks.get("rotor_speed", {}).get("detected", "1.0"))
                    omega_val = float(omega_str.split()[0])
                except Exception:
                    omega_val = 1.0
            Trev = 2.0 * math.pi / max(omega_val, 1e-6)
            n_revs = 3.0 if "3" in self.window_mode else (5.0 if "5" in self.window_mode else 10.0)
            t_cut = max(t_arr[0], t_arr[-1] - n_revs * Trev)
            t_np = np.asarray(t_arr)
            mask = t_np >= t_cut

        for ax, p_name in zip(self.axes, self.plot_names):
            had_lines_before = bool(ax.lines or ax.patches or ax.images)
            lim_key = f"{p_name}||{self.window_mode}"
            if had_lines_before and not self.v_autoscale.get() and "Heatmap" not in p_name:
                xl, yl = ax.get_xlim(), ax.get_ylim()
                if xl != (0.0, 1.0) or yl != (0.0, 1.0):
                    self._saved_axis_limits[lim_key] = (xl, yl)
            ax.clear()
            ax.set_facecolor("#1A1A1A")
            ax.grid(True, linestyle="--", alpha=0.3, color=grid_col)
            for spine in ax.spines.values():
                spine.set_color(grid_col)
            # Suppress additive "+2.04e-1" axis offset text that collides with subplot titles
            try:
                fmt = ScalarFormatter(useOffset=False)
                fmt.set_scientific(True)
                fmt.set_powerlimits((-3, 4))
                ax.yaxis.set_major_formatter(fmt)
            except Exception:
                pass

            if "01. Cp History" in p_name:
                if ts and len(t_arr) > 0:
                    t_plot = np.asarray(ts["t"])[mask] if mask is not None else ts["t"]
                    cp_plot = np.asarray(ts["cp"])[mask] if mask is not None else ts["cp"]
                    if len(t_plot) > 0 and len(cp_plot) > 0:
                        ax.plot(t_plot, cp_plot, color="#3B82F6", lw=1.2, label="Instantaneous Cp")

                        # Overlay vertical phase bands with Delta-theta label inside axes box
                        stages = getattr(snap, "stages", [])
                        if stages and len(stages) > 1:
                            for stg in stages:
                                t_st = stg.get("t_from", stg.get("t_start"))
                                dth = stg.get("deg_per_step", 0.0)
                                s_i = stg.get("stage_index", 1)
                                if t_st is not None and t_plot[0] <= t_st <= t_plot[-1]:
                                    ax.axvline(t_st, color="#94A3B8", linestyle="--", alpha=0.6, lw=1.0)
                                    ax.text(
                                        t_st, 0.96, f" P{s_i}: {dth:.1f}°",
                                        color="#94A3B8", fontsize=7, rotation=90,
                                        va="top", ha="left", transform=ax.get_xaxis_transform(), clip_on=True
                                    )

                        self._overlay_continuity_cues(ax, ts, t_plot, mask=mask, y_plot=cp_plot)
                        ax.set_title("01. Cp History", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                    else:
                        ax.set_title("01. Cp History (Waiting)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("01. Cp History (Waiting)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_ylabel("Cp", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Flow Time (s)", color=text_col, fontsize=8, labelpad=2)

            elif "02. Cp per Rev" in p_name:
                if snap.rev_history:
                    rev_hist = snap.rev_history
                    if self.window_mode != "All":
                        n_revs = 3 if "3" in self.window_mode else (5 if "5" in self.window_mode else 10)
                        rev_hist = rev_hist[-n_revs:]
                    revs = [r["k"] for r in rev_hist]
                    cps = [r["cp"] for r in rev_hist]
                    if revs and cps:
                        ax.bar(revs, cps, color="#10B981", alpha=0.8, edgecolor="#059669")
                        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
                        ax.set_title("02. Revolution-Averaged Cp", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                    else:
                        ax.set_title("02. Cp per Rev (Awaiting 1st Rev)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("02. Cp per Rev (Awaiting 1st Rev)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_ylabel("Mean Cp", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Revolution Index (k)", color=text_col, fontsize=8, labelpad=2)

            elif "03. Cp vs Azimuth" in p_name:
                az = snap.azimuth_curve
                if az and "theta" in az and "cp_latest" in az and len(az["theta"]) > 0:
                    ax.plot(az["theta"], az["cp_latest"], color="#10B981", lw=1.5, label="Cp(θ)")
                    ax.set_title("03. Phase-Averaged Cp(θ)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("03. Cp vs Azimuth", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_xlim(0, 360)
                ax.set_xticks([0, 60, 120, 180, 240, 300, 360])
                ax.set_ylabel("Cp(θ)", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Azimuth θ (deg)", color=text_col, fontsize=8, labelpad=2)

            elif "04. Blade Torque" in p_name:
                bt = snap.blade_torques
                if bt and "theta" in bt and "blades" in bt and len(bt["theta"]) > 0:
                    colors = ["#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#A855F7", "#06B6D4"]
                    for idx, (b_name, b_vals) in enumerate(bt["blades"].items()):
                        ax.plot(bt["theta"], b_vals, color=colors[idx % len(colors)], lw=1.3, label=b_name)
                    ax.legend(fontsize=6.5, loc="upper right", ncol=min(3, len(bt["blades"])), framealpha=0.65)
                    ax.margins(y=0.14)
                    ax.set_title("04. Blade Torques vs Azimuth", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("04. Blade Torques vs Azimuth", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_xlim(0, 360)
                ax.set_xticks([0, 60, 120, 180, 240, 300, 360])
                ax.set_ylabel("Mb (N·m)", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Rotor Azimuth θ (deg)", color=text_col, fontsize=8, labelpad=2)

            elif "05. Ct & Cy" in p_name:
                has_ct = bool(ts and ts.get("ct") is not None)
                has_cy = bool(ts and ts.get("cy") is not None)
                if ts and (has_ct or has_cy) and len(t_arr) > 0:
                    t_plot = np.asarray(ts["t"])[mask] if mask is not None else ts["t"]
                    y_cue = None
                    if has_ct:
                        ct_plot = np.asarray(ts["ct"])[mask] if mask is not None else ts["ct"]
                        if len(t_plot) > 0 and len(ct_plot) > 0:
                            ax.plot(t_plot, ct_plot, color="#6366F1", lw=1.2, label="Ct (Thrust)")
                            y_cue = ct_plot
                    if has_cy:
                        cy_plot = np.asarray(ts["cy"])[mask] if mask is not None else ts["cy"]
                        if len(t_plot) > 0 and len(cy_plot) > 0:
                            ax.plot(t_plot, cy_plot, color="#EC4899", lw=1.2, label="Cy (Side)")
                            if y_cue is None:
                                y_cue = cy_plot
                    if y_cue is not None and len(t_plot) > 0:
                        self._overlay_continuity_cues(ax, ts, t_plot, mask=mask, y_plot=y_cue)
                        ax.legend(fontsize=7, loc="upper right")
                        ax.set_title("05. Aerodynamic Force History", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                    else:
                        ax.set_title("05. Ct & Cy History (Waiting)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("05. Ct & Cy History (No force file)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_ylabel("Force Coeff", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Flow Time (s)", color=text_col, fontsize=8, labelpad=2)

            elif "06. Cm History" in p_name:
                if ts and len(t_arr) > 0:
                    t_plot = np.asarray(ts["t"])[mask] if mask is not None else ts["t"]
                    cm_plot = np.asarray(ts["cm"])[mask] if mask is not None else ts["cm"]
                    if len(t_plot) > 0 and len(cm_plot) > 0:
                        ax.plot(t_plot, cm_plot, color="#F97316", lw=1.2, label="Cm")
                        ax.set_title("06. Moment Coefficient (Cm)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                    else:
                        ax.set_title("06. Cm History (Waiting)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("06. Cm History", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_ylabel("Cm", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Flow Time (s)", color=text_col, fontsize=8, labelpad=2)

            elif "07. Convergence" in p_name:
                if snap.rev_history:
                    rev_hist = snap.rev_history
                    if self.window_mode != "All":
                        n_revs = 3 if "3" in self.window_mode else (5 if "5" in self.window_mode else 10)
                        rev_hist = rev_hist[-n_revs:]
                    revs = [r["k"] for r in rev_hist if r.get("dcp") is not None]
                    dcps = [r["dcp"] * 100.0 for r in rev_hist if r.get("dcp") is not None]
                    if dcps:
                        tol_val = float(self.live_params.stable_tol) if (self.live_params and self.live_params.stable_tol is not None) else 5.0
                        ax.plot(revs, dcps, marker="o", color="#F59E0B", lw=1.3, label="ΔCp (%)")
                        ax.axhline(tol_val, color="#10B981", linestyle="--", lw=1, alpha=0.7, label=f"±{tol_val:g}% tol")
                        ax.axhline(-tol_val, color="#10B981", linestyle="--", lw=1, alpha=0.7)
                        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
                        ax.legend(fontsize=6.8, loc="upper right", framealpha=0.7)
                    else:
                        ax.text(0.5, 0.5, "Need ≥ 2 revolutions for ΔCp (%)", ha="center", va="center", color="gray", transform=ax.transAxes, fontsize=8, clip_on=True)
                    ax.set_title("07. Rev-to-Rev ΔCp (%)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.text(0.5, 0.5, "Awaiting revolutions...", ha="center", va="center", color="gray", transform=ax.transAxes, fontsize=8, clip_on=True)
                    ax.set_title("07. Convergence ΔCp (%)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_ylabel("ΔCp (%)", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Revolution Index", color=text_col, fontsize=8, labelpad=2)

            elif "08. Cp Heatmap" in p_name:
                if snap.heat_map is not None and len(snap.heat_map) > 0:
                    hm = snap.heat_map
                    total_hm_rows = len(hm)
                    k_start = 1
                    if self.window_mode != "All":
                        n_revs_w = 3 if "3" in self.window_mode else (5 if "5" in self.window_mode else 10)
                        if total_hm_rows > n_revs_w:
                            k_start = total_hm_rows - n_revs_w + 1
                            hm = hm[-n_revs_w:]
                    k_end = k_start + len(hm) - 1
                    im_obj = ax.imshow(
                        hm, aspect="auto", cmap="viridis",
                        extent=(0.0, 360.0, float(k_end) + 0.5, float(k_start) - 0.5)
                    )
                    setattr(im_obj, "_hm_k_start", k_start)
                    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
                    ax.set_title("08. Revolution × Azimuth Heatmap", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                    ax.set_ylabel("Rev (k)", color=text_col, fontsize=8, labelpad=3)
                else:
                    ax.set_title("08. Cp Heatmap (Awaiting Revs)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_xlim(0, 360)
                ax.set_xticks([0, 60, 120, 180, 240, 300, 360])
                ax.set_xlabel("Azimuth θ (deg)", color=text_col, fontsize=8, labelpad=2)

            elif "09. Cp Spectrum" in p_name:
                sp = snap.spectrum
                if sp and "freqs" in sp and "amps" in sp and len(sp["freqs"]) > 0:
                    ax.plot(sp["freqs"], sp["amps"], color="#8B5CF6", lw=1.2, label="FFT Amplitude")
                    fbpf = sp.get("fbpf", 0.0)
                    if fbpf > 0:
                        ax.axvline(fbpf, color="#EF4444", linestyle="--", lw=1.2, label=f"BPF ({fbpf:.1f} Hz)")
                        ax.legend(fontsize=6.8, loc="upper right", framealpha=0.7)
                    ax.set_title("09. Cp Ripple Spectrum (FFT)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("09. Cp Spectrum (FFT)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_ylabel("Amplitude", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Frequency (Hz)", color=text_col, fontsize=8, labelpad=2)

            elif "10. y+" in p_name:
                ax.set_title("10. Wall y+ History", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.text(0.5, 0.5, "y+ monitoring requires Fluent y+ report file", ha="center", va="center", color="gray", transform=ax.transAxes, fontsize=8, clip_on=True)
                ax.set_ylabel("y+", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Flow Time (s)", color=text_col, fontsize=8, labelpad=2)

            elif "11. Kinematics" in p_name:
                kin = snap.kinematics
                if kin and "theta" in kin and len(kin["theta"]) > 0:
                    stall_deg = getattr(self.live_params, "static_stall_deg", 12.0) if self.live_params else 12.0
                    l1 = ax.plot(kin["theta"], kin["aoa"], color="#06B6D4", lw=1.4, label="AoA α(θ)")
                    l2 = ax.axhline(stall_deg, color="#EF4444", linestyle="--", lw=0.9, alpha=0.7, label=f"±{stall_deg:g}° stall")
                    ax.axhline(-stall_deg, color="#EF4444", linestyle="--", lw=0.9, alpha=0.7)
                    handles = list(l1) + [l2]
                    if "w_over_u" in kin and len(kin["w_over_u"]) == len(kin["theta"]):
                        ax2 = ax.twinx()
                        self._twin_axes[ax2] = ax
                        l3 = ax2.plot(kin["theta"], kin["w_over_u"], color="#F59E0B", lw=1.1, linestyle="-.", label="W/U ratio")
                        ax2.set_ylabel("W / U", color="#F59E0B", fontsize=8, labelpad=4)
                        ax2.tick_params(axis="y", colors="#F59E0B", labelsize=7.5)
                        ax2.spines["top"].set_color(grid_col)
                        ax2.spines["right"].set_color("#F59E0B")
                        handles.extend(l3)
                    labels = [str(h.get_label()) for h in handles]
                    ax.legend(handles, labels, fontsize=6.8, loc="upper right", framealpha=0.7)
                    ax.set_title("11. Theoretical Kinematics α(θ) & W/U", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                else:
                    ax.set_title("11. Kinematics (AoA)", color=text_col, fontsize=title_fs, weight="bold", pad=4)
                ax.set_xlim(0, 360)
                ax.set_xticks([0, 60, 120, 180, 240, 300, 360])
                ax.set_ylabel("AoA (deg)", color=text_col, fontsize=8, labelpad=3)
                ax.set_xlabel("Azimuth θ (deg)", color=text_col, fontsize=8, labelpad=2)

            is_azimuth_plot = any(k in p_name for k in ["Azimuth", "Blade Torque", "Heatmap", "Kinematics"])
            if not self.v_autoscale.get() and "Heatmap" not in p_name and lim_key in self._saved_axis_limits:
                saved_xl, saved_yl = self._saved_axis_limits[lim_key]
                ax.set_xlim(saved_xl)
                ax.set_ylim(saved_yl)
            else:
                try:
                    ax.relim()
                    if is_azimuth_plot:
                        ax.autoscale_view(scalex=False, scaley=True)
                        ax.set_xlim(0, 360)
                        ax.set_xticks([0, 60, 120, 180, 240, 300, 360])
                    else:
                        ax.autoscale_view()
                except Exception:
                    pass
                if is_azimuth_plot:
                    ax.set_xlim(0, 360)
                    ax.set_xticks([0, 60, 120, 180, 240, 300, 360])

            # Lock axes limits after plotting so hover probe artists never trigger autoscaling or shifting
            ax.set_autoscale_on(False)
            ax.tick_params(colors=text_col, labelsize=8, pad=2)

        self.lbl_plot_stamp.configure(text=f"Updated: {time_stamp}")
        self.canvas.draw_idle()

    def _get_or_create_probe_artists(self, ax: matplotlib.axes.Axes) -> Dict[str, Any]:
        """Lazily create crosshair, highlight dot, and floating tooltip artists on an Axes."""
        if ax in self._probe_artists:
            return self._probe_artists[ax]

        box_bg = "#0F172A"
        box_fg = "#F8FAFC"
        box_edge = "#38BDF8"
        cross_col = "#94A3B8"

        # Save and freeze current limits so adding probe artists cannot expand viewLim
        cur_xlim = ax.get_xlim()
        cur_ylim = ax.get_ylim()
        ax.set_autoscale_on(False)

        vline = ax.axvline(0.0, color=cross_col, linestyle=":", lw=0.9, alpha=0.85, visible=False, zorder=9, animated=True)
        hline = ax.axhline(0.0, color=cross_col, linestyle=":", lw=0.9, alpha=0.85, visible=False, zorder=9, animated=True)
        (dot,) = ax.plot(
            [], [], marker="o", markersize=7,
            markerfacecolor=box_edge, markeredgecolor=box_fg, markeredgewidth=1.4,
            linestyle="None", visible=False, zorder=10, scalex=False, scaley=False, animated=True
        )
        # Keep annotation object for API stability, but never show text overlay on the graph itself
        annot = ax.annotate(
            "",
            xy=(0.0, 0.0),
            xytext=(10, 10),
            textcoords="offset points",
            fontsize=8,
            fontweight="bold",
            color=box_fg,
            bbox=dict(boxstyle="round,pad=0.35", facecolor=box_bg, edgecolor=box_edge, alpha=0.92, lw=1.0),
            visible=False,
            zorder=11,
            annotation_clip=True,
            animated=True,
        )
        for art in (vline, hline, dot, annot):
            try:
                art.set_in_layout(False)
            except Exception:
                pass
        ax.set_xlim(cur_xlim)
        ax.set_ylim(cur_ylim)

        artists = {"vline": vline, "hline": hline, "dot": dot, "annot": annot}
        self._probe_artists[ax] = artists
        return artists

    def _on_canvas_draw_event(self, _event=None) -> None:
        """Cache clean subplot background bitmap after full draws for fast hover blitting."""
        if not hasattr(self, "canvas") or self.canvas is None or not hasattr(self, "fig") or self.fig is None:
            return
        try:
            self._blit_bg = self.canvas.copy_from_bbox(self.fig.bbox)
        except Exception:
            self._blit_bg = None
            return
        ax = self._active_probe_ax
        if ax is not None and ax in self._probe_artists:
            dot = self._probe_artists[ax].get("dot")
            if dot is not None and dot.get_visible():
                try:
                    ax.draw_artist(dot)
                except Exception:
                    pass

    def _blit_live_probe(self, target_ax: Any = None) -> None:
        """Restore cached figure region, draw active probe dot, and coalesce Qt paint (~2ms)."""
        if not hasattr(self, "canvas") or self.canvas is None:
            return
        if self._blit_bg is None:
            self.canvas.draw_idle()
            return
        try:
            self.canvas.restore_region(self._blit_bg)
            ax = self._active_probe_ax
            if ax is not None and ax in self._probe_artists:
                dot = self._probe_artists[ax].get("dot")
                if dot is not None and dot.get_visible():
                    ax.draw_artist(dot)
            bbox = None
            self.canvas.update_bbox_region(bbox)
        except Exception:
            self._blit_bg = None
            self.canvas.draw_idle()

    def _hide_all_probes(self, redraw: bool = True) -> None:
        """Hide probe artists across all subplots and reset right-panel Data Probe readout."""
        prev_ax = self._active_probe_ax
        changed = False
        for artists in self._probe_artists.values():
            for k in ("vline", "hline", "dot", "annot"):
                art = artists.get(k)
                if art is not None and art.get_visible():
                    art.set_visible(False)
                    changed = True
        self._active_probe_ax = None
        self._last_hover_sig = None
        self._last_hover_ts = 0.0
        self._set_probe_panel_idle()
        if changed and redraw:
            self._blit_live_probe(target_ax=prev_ax)

    def _on_plot_leave(self, _event=None) -> None:
        """Clear probe overlay when cursor leaves the figure canvas."""
        self._hide_all_probes(redraw=True)

    def _probe_axis_data(self, ax: matplotlib.axes.Axes, x_mouse: float, y_mouse: float) -> Optional[Dict[str, Any]]:
        """Extract nearest data coordinate, compact tooltip, and detailed right-panel probe report."""
        primary_ax = self._twin_axes.get(ax, ax)
        p_name = ""
        if hasattr(self, "axes") and hasattr(self, "plot_names") and primary_ax in self.axes:
            idx = self.axes.index(primary_ax)
            if idx < len(self.plot_names):
                p_name = self.plot_names[idx]
        if not p_name:
            p_name = primary_ax.get_title() or "Graph"

        # 1. Heatmap (2D AxesImage)
        if ax.images:
            img = ax.images[0]
            arr = np.asarray(img.get_array())
            ext = img.get_extent()  # (left, right, bottom, top)
            if arr.ndim == 2 and arr.size > 0:
                x0, x1, y0, y1 = float(ext[0]), float(ext[1]), float(ext[2]), float(ext[3])
                x_min, x_max = min(x0, x1), max(x0, x1)
                y_min, y_max = min(y0, y1), max(y0, y1)
                if x_min <= x_mouse <= x_max and y_min <= y_mouse <= y_max:
                    n_rows, n_cols = arr.shape
                    c_idx = int(np.clip((x_mouse - x_min) / max(x_max - x_min, 1e-9) * n_cols, 0, n_cols - 1))
                    r_idx = int(np.clip((y_mouse - y_min) / max(y_max - y_min, 1e-9) * n_rows, 0, n_rows - 1))
                    val = float(arr[r_idx, c_idx])
                    k_start = int(getattr(ax.images[0], "_hm_k_start", 1))
                    rev_num = k_start + r_idx
                    az_deg = (c_idx + 0.5) * (360.0 / max(n_cols, 1))
                    coord_str = f"Rev {rev_num}  |  θ = {az_deg:.1f}°"
                    tooltip = f"Rev {rev_num} | θ = {az_deg:.1f}°\nCp = {val:.4f}"
                    rows = [
                        ("Revolution k", f"Rev {rev_num}", "#3B82F6"),
                        ("Azimuth θ", f"{az_deg:.2f}°", "#06B6D4"),
                        ("Cp(k, θ)", f"{val:.4f}", "#10B981"),
                    ]
                    details = f"Revolution: {rev_num}\nAzimuth θ: {az_deg:.2f}°\nCp(k, θ): {val:.4f}"
                    return {
                        "x": x_mouse,
                        "y": y_mouse,
                        "graph": p_name,
                        "coord": coord_str,
                        "rows": rows,
                        "details": details,
                        "tooltip": tooltip,
                    }

        # 2. Bar Chart (e.g., 02. Cp per Rev)
        if ax.patches and ("Cp per Rev" in p_name or not ax.lines):
            best_patch: Any = None
            best_dx = float("inf")
            for patch in ax.patches:
                p_any: Any = patch
                if hasattr(p_any, "get_x") and hasattr(p_any, "get_width") and hasattr(p_any, "get_height"):
                    bx = float(p_any.get_x()) + 0.5 * float(p_any.get_width())
                    dx = abs(x_mouse - bx)
                    if dx < best_dx:
                        best_dx = dx
                        best_patch = p_any
            if best_patch is not None:
                bx = float(best_patch.get_x()) + 0.5 * float(best_patch.get_width())
                by = float(best_patch.get_height())
                rev_k = int(round(bx))
                dcp_str = ""
                rows: List[Tuple[str, str, str]] = [
                    ("Revolution k", f"Rev {rev_k}", "#3B82F6"),
                    ("Mean Cp", f"{by:.4f}", "#10B981"),
                ]
                detail_lines = [
                    f"Revolution: {rev_k}",
                    f"Mean Cp: {by:.4f}",
                ]
                if self.latest_snapshot and self.latest_snapshot.rev_history:
                    for r in self.latest_snapshot.rev_history:
                        if int(r.get("k", -1)) == rev_k:
                            if r.get("dcp") is not None:
                                dcp_pct = float(r["dcp"]) * 100.0
                                dcp_str = f"\nΔCp: {dcp_pct:+.2f}%"
                                rows.append(("ΔCp vs Prev", f"{dcp_pct:+.2f}%", "#F59E0B"))
                                detail_lines.append(f"ΔCp vs Prev: {dcp_pct:+.2f}%")
                            if r.get("t_start") is not None and r.get("t_end") is not None:
                                t_span = f"{float(r['t_start']):.3f}–{float(r['t_end']):.3f} s"
                                rows.append(("Time Span", t_span, "gray"))
                                detail_lines.append(f"Time Span: {t_span}")
                            break
                tooltip = f"Rev {rev_k}\nMean Cp: {by:.4f}{dcp_str}"
                return {
                    "x": bx,
                    "y": by,
                    "graph": p_name,
                    "coord": f"Revolution k = {rev_k}",
                    "rows": rows,
                    "details": "\n".join(detail_lines),
                    "tooltip": tooltip,
                }

        # 3. Line Curves (single or multi-curve, including paired twinx axes)
        search_axes = [primary_ax] + [twin for twin, p_ax in self._twin_axes.items() if p_ax is primary_ax]
        data_lines = []
        for s_ax in search_axes:
            p_arts = self._probe_artists.get(s_ax, {})
            excl = {p_arts.get("vline"), p_arts.get("hline"), p_arts.get("dot")}
            for ln in s_ax.lines:
                if ln in excl or not ln.get_visible():
                    continue
                xd = np.asarray(ln.get_xdata(), dtype=float)
                yd = np.asarray(ln.get_ydata(), dtype=float)
                if xd.size < 1 or yd.size < 1 or xd.size != yd.size:
                    continue
                lbl = str(ln.get_label() or "")
                if lbl.startswith("_") and len(s_ax.lines) > 1:
                    continue
                if "tol" in lbl.lower() or "stall" in lbl.lower() or "bpf" in lbl.lower():
                    continue
                data_lines.append((s_ax, ln, xd, yd, lbl))

        if not data_lines:
            return None

        # Find nearest x index on primary curve (O(log N) when sorted ascending)
        def _nearest_x_idx(arr: np.ndarray, val: float) -> int:
            n_pts = len(arr)
            if n_pts <= 1:
                return 0
            if n_pts > 256 and bool(arr[0] <= arr[-1] and np.all(arr[:-1] <= arr[1:])):
                pos = int(np.searchsorted(arr, val))
                if pos <= 0:
                    return 0
                if pos >= n_pts:
                    return n_pts - 1
                return pos - 1 if abs(val - arr[pos - 1]) <= abs(arr[pos] - val) else pos
            return int(np.argmin(np.abs(arr - val)))

        _, primary_ln, px, py, p_lbl = data_lines[0]
        idx_near = _nearest_x_idx(px, x_mouse)
        x_snap = float(px[idx_near])

        # Determine x label formatting based on plot type (always read primary axis labels!)
        xlab = primary_ax.get_xlabel() or "x"
        ylab = primary_ax.get_ylabel() or "y"
        rows = []
        detail_lines = []
        if "Azimuth" in xlab or "θ" in xlab:
            x_fmt = f"θ = {x_snap:.1f}°"
            coord_hdr = f"Azimuth θ = {x_snap:.2f}°"
            rows.append(("Azimuth θ", f"{x_snap:.2f}°", "#38BDF8"))
        elif "Revolution" in xlab:
            x_fmt = f"Rev {int(round(x_snap))}"
            coord_hdr = f"Revolution k = {int(round(x_snap))}"
            rows.append(("Revolution k", f"Rev {int(round(x_snap))}", "#38BDF8"))
        elif "Frequency" in xlab:
            x_fmt = f"f = {x_snap:.2f} Hz"
            coord_hdr = f"Frequency f = {x_snap:.2f} Hz"
            rows.append(("Frequency f", f"{x_snap:.2f} Hz", "#38BDF8"))
        else:
            x_fmt = f"t = {x_snap:.4f} s"
            coord_hdr = f"Flow Time t = {x_snap:.4f} s"
            rows.append(("Flow Time t", f"{x_snap:.4f} s", "#38BDF8"))

        # Pick closest primary-axis curve in y at x_snap for the highlight dot
        best_x = x_snap
        best_y = float(py[idx_near])
        best_dy = float("inf")
        val_lines = []

        for s_ax, ln, xd, yd, lbl in data_lines:
            i_c = _nearest_x_idx(xd, x_mouse)
            xc = float(xd[i_c])
            yc = float(yd[i_c])
            if s_ax is primary_ax:
                dy = abs(yc - y_mouse)
                if dy <= best_dy:
                    best_dy = dy
                    best_x = xc
                    best_y = yc
            clean_lbl = lbl if (lbl and not lbl.startswith("_")) else (s_ax.get_ylabel() or ylab)
            if "ΔCp" in clean_lbl or "%" in ylab:
                v_str = f"{yc:+.2f}%"
            elif abs(yc) >= 100.0 or (0.0 < abs(yc) < 1e-3):
                v_str = f"{yc:.3e}"
            else:
                v_str = f"{yc:.4f}"
            ln_col = str(ln.get_color() or "#10B981")
            val_lines.append(f"{clean_lbl}: {v_str}")
            rows.append((clean_lbl, v_str, ln_col))
            detail_lines.append(f"{clean_lbl}: {v_str}")

        # Add Revolution / Azimuth context for time-series plots if space permits in the 4-row card
        if "Flow Time" in xlab and len(rows) < 4:
            omega_val = 0.0
            if self.live_params is not None:
                try:
                    omega_val = self.live_params.omega_val()
                except Exception:
                    omega_val = 0.0
            if omega_val > 0.0:
                Trev = 2.0 * math.pi / omega_val
                t0 = 0.0
                if self.latest_snapshot and self.latest_snapshot.timeseries and len(self.latest_snapshot.timeseries.get("t", [])) > 0:
                    t0 = float(self.latest_snapshot.timeseries["t"][0])
                th0 = float(getattr(self.live_params, "theta0", 0.0) or 0.0) if self.live_params else 0.0
                dt_rel = max(0.0, x_snap - t0)
                rev_at_t = int(dt_rel // Trev) + 1
                az_at_t = (th0 + math.degrees(dt_rel * omega_val)) % 360.0
                phase_txt = f"Rev {rev_at_t} ({az_at_t:.1f}°)"
                rows.append(("Phase / Rev", phase_txt, "gray"))
                detail_lines.append(f"Phase: {phase_txt}")

        tooltip = x_fmt + "\n" + "\n".join(val_lines)
        return {
            "x": best_x,
            "y": best_y,
            "graph": p_name,
            "coord": coord_hdr,
            "rows": rows[:6],
            "details": "\n".join(detail_lines),
            "tooltip": tooltip,
        }

    def _on_plot_hover(self, event) -> None:
        """Handle Matplotlib motion_notify_event to update live data probe dot & right-panel readout via blitting."""
        if not self._probe_enabled or event is None or event.inaxes is None:
            if self._active_probe_ax is not None:
                self._hide_all_probes(redraw=True)
            return

        raw_inaxes = event.inaxes
        ax = self._twin_axes.get(raw_inaxes, raw_inaxes)
        if event.xdata is None or event.ydata is None:
            return
        x_mouse = float(event.xdata)
        if raw_inaxes is not ax and event.x is not None and event.y is not None:
            try:
                _, y_mouse_tr = ax.transData.inverted().transform((event.x, event.y))
                y_mouse = float(y_mouse_tr)
            except Exception:
                y_mouse = float(event.ydata)
        else:
            y_mouse = float(event.ydata)

        now = time.monotonic()
        if self._active_probe_ax is ax and (now - self._last_hover_ts < 0.016):
            return
        self._last_hover_ts = now

        info = self._probe_axis_data(ax, x_mouse, y_mouse)
        if info is None:
            if self._active_probe_ax is not None:
                self._hide_all_probes(redraw=True)
            return

        px, py = info["x"], info["y"]
        sig = (id(ax), f"{float(px):.7g}", f"{float(py):.7g}")
        if sig == self._last_hover_sig and self._active_probe_ax is ax:
            return
        self._last_hover_sig = sig

        prev_ax = self._active_probe_ax
        # Hide probe on previously hovered subplot if user moved to a different subplot
        if prev_ax is not None and prev_ax is not ax:
            prev_arts = self._probe_artists.get(prev_ax)
            if prev_arts:
                for k in ("vline", "hline", "dot", "annot"):
                    if prev_arts.get(k) is not None:
                        prev_arts[k].set_visible(False)

        self._active_probe_ax = ax
        arts = self._get_or_create_probe_artists(ax)

        arts["vline"].set_visible(False)
        arts["hline"].set_visible(False)
        arts["annot"].set_visible(False)
        arts["dot"].set_data([px], [py])
        arts["dot"].set_visible(True)

        # Update right-panel Data Probe section (sidebar only; skip unchanged widget updates)
        if hasattr(self, "lbl_probe_graph") and self._probe_ui_cache.get("graph") != info["graph"]:
            self._probe_ui_cache["graph"] = info["graph"]
            self.lbl_probe_graph.configure(text=info["graph"], text_color=("#2563EB", "#38BDF8"))
        if hasattr(self, "lbl_probe_coord") and self._probe_ui_cache.get("coord") != info["coord"]:
            self._probe_ui_cache["coord"] = info["coord"]
            self.lbl_probe_coord.configure(text=info["coord"])
        self._update_probe_rows(info.get("rows", []), info.get("details", ""))

        self._blit_live_probe(target_ax=ax if (prev_ax is None or prev_ax is ax) else None)

    # -------------------------------------------------------------------------
    # Queue Polling & Telemetry Updates
    # -------------------------------------------------------------------------
    def _ensure_monitor_timers(self) -> None:
        """Start UI snapshot-queue and countdown timers when active monitoring begins."""
        if getattr(self, "_is_closing", False):
            return
        if not getattr(self, "_snap_poll_after_id", None):
            self._snap_poll_after_id = self.after(200, self._poll_snapshot_queue)
        if not getattr(self, "_countdown_after_id", None):
            self._countdown_after_id = self.after(1000, self._countdown_tick)

    def _stop_monitor_timers(self) -> None:
        """Cancel active UI polling and countdown timers when stopped."""
        for attr in ("_snap_poll_after_id", "_countdown_after_id"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    self.after_cancel(tid)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _poll_snapshot_queue(self):
        """Process snapshots arriving from background worker, coalescing queued bursts."""
        self._snap_poll_after_id = None
        if getattr(self, "_is_closing", False):
            return
        try:
            latest_snap: Optional[LiveMonitorSnapshot] = None
            any_full = False
            while True:
                try:
                    snap: LiveMonitorSnapshot = self.snapshot_queue.get_nowait()
                    latest_snap = snap
                    if snap.is_full_refresh:
                        any_full = True
                except queue.Empty:
                    break

            if latest_snap is not None and self.is_monitoring:
                if any_full:
                    self._last_chart_snapshot = latest_snap
                elif self._last_chart_snapshot is not None and not latest_snap.timeseries:
                    prev_c = self._last_chart_snapshot
                    latest_snap.timeseries = prev_c.timeseries
                    latest_snap.azimuth_curve = prev_c.azimuth_curve
                    latest_snap.heat_map = prev_c.heat_map
                    latest_snap.spectrum = prev_c.spectrum
                    latest_snap.kinematics = prev_c.kinematics
                    latest_snap.blade_torques = prev_c.blade_torques
                    latest_snap.forces_az = prev_c.forces_az

                self.latest_snapshot = latest_snap
                self._update_telemetry(latest_snap)
                if any_full:
                    try:
                        if not self.app or getattr(self.app, "current_page", "live") == "live":
                            self._render_graphs(latest_snap)
                            gc.collect()
                    finally:
                        self._refresh_pending = False
                        self.time_until_refresh = self.refresh_interval_s
                        mins = int(self.time_until_refresh // 60)
                        secs = int(self.time_until_refresh % 60)
                        if not self.is_paused:
                            self.lbl_countdown.configure(text=f"⏱ Next: {mins}:{secs:02d}")
                        self.lbl_last_refresh.configure(text=f"Last Refresh: {_dt.datetime.now().strftime('%H:%M:%S')}")
        except Exception as exc:
            print(f"[LiveMonitor] Snapshot handling error: {exc}")
            traceback.print_exc()
        finally:
            if not getattr(self, "_is_closing", False) and (self.is_monitoring or not self.snapshot_queue.empty()):
                self._snap_poll_after_id = self.after(200, self._poll_snapshot_queue)

    def on_show(self):
        """Called when the Live Monitor page becomes visible."""
        if not self.is_monitoring and self.live_params is None:
            self._get_or_detect_params()
        render_snap = self._last_chart_snapshot or self.latest_snapshot
        if render_snap:
            self._render_graphs(render_snap, force=False)

    @staticmethod
    def _cfg_if_changed(widget: Any, **kwargs: Any) -> bool:
        """Configure widget only with properties whose values changed since last call."""
        cache = getattr(widget, "_lm_cfg_cache", None)
        if cache is None:
            cache = {}
            setattr(widget, "_lm_cfg_cache", cache)
        diff = {k: v for k, v in kwargs.items() if k not in cache or cache[k] != v}
        if not diff:
            return False
        cache.update(diff)
        widget.configure(**diff)
        return True

    def _countdown_tick(self):
        """1 Hz timer decrementing countdown display and auto-refreshing at 0:00."""
        self._countdown_after_id = None
        if getattr(self, "_is_closing", False):
            return
        try:
            if self.is_monitoring:
                if self.is_paused:
                    self._cfg_if_changed(self.lbl_countdown, text="⏱ Paused", text_color="#06B6D4")
                else:
                    self.time_until_refresh = max(0.0, self.time_until_refresh - 1.0)
                    cd_col = ("royal blue", "#60A5FA")
                    if self.time_until_refresh <= 0.0:
                        self._cfg_if_changed(self.lbl_countdown, text="⏱ Refreshing...", text_color=cd_col)
                        if not getattr(self, "_refresh_pending", False):
                            self._refresh_pending = True
                            self._refresh_now()
                            self.time_until_refresh = self.refresh_interval_s
                    else:
                        if not getattr(self, "_refresh_pending", False):
                            mins = int(self.time_until_refresh // 60)
                            secs = int(self.time_until_refresh % 60)
                            self._cfg_if_changed(self.lbl_countdown, text=f"⏱ Next: {mins}:{secs:02d}", text_color=cd_col)
                        else:
                            self._cfg_if_changed(self.lbl_countdown, text_color=cd_col)

                # Badge pulsing / status indicators
                if self.app and hasattr(self.app, "tab_btn_live"):
                    is_live_active = getattr(self.app, "current_page", "") == "live"
                    if self.is_paused:
                        tc = "#BAE6FD" if is_live_active else "#06B6D4"
                        self._cfg_if_changed(self.app.tab_btn_live, text="📡 Live Monitor  ⏸", text_color=tc)
                    elif self.latest_snapshot and "Stalled" in self.latest_snapshot.status:
                        tc = "#FDE68A" if is_live_active else "#F59E0B"
                        self._cfg_if_changed(self.app.tab_btn_live, text="📡 Live Monitor  ● Stalled", text_color=tc)
                    else:
                        dot = "●" if int(self.time_until_refresh) % 2 == 0 else "○"
                        tc = "#A7F3D0" if is_live_active else "#10B981"
                        self._cfg_if_changed(self.app.tab_btn_live, text=f"📡 Live Monitor  {dot}", text_color=tc)
        except Exception as exc:
            print(f"[LiveMonitor] Countdown tick error: {exc}")
            traceback.print_exc()
        finally:
            if not getattr(self, "_is_closing", False) and self.is_monitoring:
                self._countdown_after_id = self.after(1000, self._countdown_tick)

    def _update_telemetry(self, snap: LiveMonitorSnapshot):
        """Update status strip, side panels, and alerts without touching graphs."""
        cfg = self._cfg_if_changed
        # Status Badge
        if snap.status == "Monitoring":
            cfg(self.state_badge, text="● Monitoring", text_color="#10B981")
        elif "Stalled" in snap.status:
            cfg(self.state_badge, text="⚠ Stalled?", text_color="#F59E0B")
        elif "Waiting" in snap.status:
            cfg(self.state_badge, text="⏳ Waiting Files", text_color="#3B82F6")
        elif snap.status == "Paused":
            cfg(self.state_badge, text="⏸ Paused", text_color="#06B6D4")
        else:
            cfg(self.state_badge, text=f"● {snap.status}", text_color="gray")

        # Telemetry Labels
        cfg(self.lbl_step, text=f"Step: {snap.current_step:,}")
        cfg(self.lbl_time, text=f"Time: {snap.current_time:.4f} s")
        cfg(self.lbl_revs, text=f"Revs: {snap.total_revs:.2f}")
        cfg(self.lbl_azimuth, text=f"θ: {snap.current_azimuth:.1f}°")
        cfg(self.lbl_speed, text=f"Speed: {snap.steps_per_sec:.1f} steps/s")
        if snap.eta_seconds is not None and snap.eta_seconds >= 0:
            eta_m = int(snap.eta_seconds // 60)
            eta_s = int(snap.eta_seconds % 60)
            cfg(self.lbl_eta, text=f"ETA: {eta_m}m {eta_s}s")
        else:
            cfg(self.lbl_eta, text="ETA: -")

        cfg(self.lbl_fresh, text=f"Freshness: {snap.last_data_age_s:.1f}s ago")

        # Multi-stage and time step telemetry handling
        stages = getattr(snap, "stages", [])
        omega_val = 0.0
        if self.live_params is not None:
            try:
                omega_val = self.live_params.omega_val()
            except Exception:
                omega_val = 0.0
        if omega_val <= 0:
            try:
                om_str = str(snap.parameter_checks.get("rotor_speed", {}).get("detected", "0.0"))
                omega_val = float(om_str.split()[0])
            except Exception:
                omega_val = 1.0

        Trev = (2.0 * math.pi / omega_val) if omega_val > 0 else 0.0
        rpm_val = omega_val * 60.0 / (2.0 * math.pi) if omega_val > 0 else 0.0

        if stages:
            curr_stage = stages[-1]
            n_stages = len(stages)
            dt_curr = curr_stage["dt"]
            dt_curr_str = f"{dt_curr:.3e} s" if (dt_curr < 1e-3 or dt_curr >= 10) else f"{dt_curr:.5f} s"

            # 1. Status strip label & badge
            if n_stages > 1:
                cfg(self.lbl_dt, text=f"Δt: {dt_curr_str} (Stage {curr_stage['stage_index']}/{n_stages})")
                cfg(self.lbl_stages_badge, text=f"{n_stages} Stages", text_color="#3B82F6")
            else:
                cfg(self.lbl_dt, text=f"Δt: {dt_curr_str}")
                cfg(self.lbl_stages_badge, text="Uniform Δt", text_color="#10B981")

            # Update current phase label in status row
            if hasattr(self, "lbl_current_phase"):
                phase_text = f"P{curr_stage.get('stage_index', 1)}: {curr_stage.get('deg_per_step', 0.0):.1f}°"
                cfg(self.lbl_current_phase, text=phase_text, text_color="#38BDF8")

            # 2. Detailed text for Stages Card & Tooltip
            lines = []
            if n_stages > 1:
                lines.append("⏮ Previous Stage(s):")
                for st in stages[:-1]:
                    d_s = st["dt"]
                    d_str = f"{d_s:.3e} s" if (d_s < 1e-3 or d_s >= 10) else f"{d_s:.5f} s"
                    deg_s = st.get("deg_per_step", 0.0)
                    rev_s = st.get("revolutions", 0.0)
                    lines.append(
                        f" • Stage {st['stage_index']}: Steps {st['step_from']:,}–{st['step_to']:,} ({st['n_steps']:,} steps)\n"
                        f"   t: {st['t_from']:.3f}–{st['t_to']:.3f} s ({rev_s:.2f} revs)\n"
                        f"   Δt = {d_str} ({deg_s:.2f}°/step)"
                    )
                lines.append(f"\n▶ Current Stage ({curr_stage['stage_index']} of {n_stages}):")
            else:
                lines.append("▶ Current Stage (1 of 1):")

            deg_c = curr_stage.get("deg_per_step", 0.0)
            rev_c = curr_stage.get("revolutions", 0.0)
            steps_per_rev_c = curr_stage.get("steps_per_rev", 0.0)
            lines.append(
                f" • Steps {curr_stage['step_from']:,}–{curr_stage['step_to']:,} ({curr_stage['n_steps']:,} steps)\n"
                f" • Time: {curr_stage['t_from']:.3f}–{curr_stage['t_to']:.3f} s ({rev_c:.2f} revs)\n"
                f" • Current Δt = {dt_curr_str}\n"
                f" • Resolution: {deg_c:.2f}°/step ({steps_per_rev_c:,.0f} steps/rev)"
            )
            stages_text = "\n".join(lines)
            if cfg(self.lbl_stages_content, text=stages_text):
                if hasattr(self, "dt_tooltip") and self.dt_tooltip:
                    self.dt_tooltip.text = stages_text
                if hasattr(self.lbl_dt, "setToolTip"):
                    self.lbl_dt.setToolTip(stages_text)

            # 3. Derived Physics Card
            st_sfx = f" (Stage {curr_stage['stage_index']}/{n_stages})" if n_stages > 1 else ""
            cfg(
                self.lbl_derived_echo,
                text=f"ω: {omega_val:.2f} rad/s ({rpm_val:.1f} rpm)\n"
                     f"T_rev: {Trev:.4f} s\n"
                     f"Current Δt: {dt_curr_str}{st_sfx}\n"
                     f"Deg/step: {deg_c:.2f}° | Steps/rev: {steps_per_rev_c:,.0f}"
            )

            # 4. Consistency Checks Card
            if snap.parameter_checks and hasattr(self, "lbl_checks_echo"):
                dt_summary = f"{n_stages} stages, curr: {dt_curr_str}" if n_stages > 1 else dt_curr_str
                cfg(self.lbl_checks_echo, text=self._format_checks_summary(snap.parameter_checks, dt_summary))
        elif snap.dt_seconds is not None and snap.dt_seconds > 0:
            dt_s = snap.dt_seconds
            dt_fmt = f"{dt_s:.3e} s" if (dt_s < 1e-3 or dt_s >= 10) else f"{dt_s:.5f} s"
            cfg(self.lbl_dt, text=f"Δt: {dt_fmt}")
            cfg(self.lbl_stages_badge, text="Uniform Δt", text_color="#10B981")
            dtheta = (omega_val * dt_s * 180.0 / math.pi) if omega_val > 0 else 0.0
            steps_rev = round(Trev / dt_s) if (Trev > 0 and dt_s > 0) else 0
            if hasattr(self, "lbl_current_phase"):
                cfg(self.lbl_current_phase, text=f"P1: {dtheta:.1f}°", text_color="#38BDF8")
            cfg(
                self.lbl_derived_echo,
                text=f"ω: {omega_val:.2f} rad/s ({rpm_val:.1f} rpm)\nT_rev: {Trev:.4f} s\nΔt: {dt_fmt}\nDeg/step: {dtheta:.2f}° | Steps/rev: {steps_rev:,}"
            )
            st_single_txt = f"Current: Δt = {dt_fmt}\nResolution: {dtheta:.2f}°/step ({steps_rev:,} steps/rev)"
            if cfg(self.lbl_stages_content, text=st_single_txt):
                if hasattr(self, "dt_tooltip") and self.dt_tooltip:
                    self.dt_tooltip.text = st_single_txt
                if hasattr(self.lbl_dt, "setToolTip"):
                    self.lbl_dt.setToolTip(st_single_txt)
            if snap.parameter_checks and hasattr(self, "lbl_checks_echo"):
                cfg(self.lbl_checks_echo, text=self._format_checks_summary(snap.parameter_checks, dt_fmt))
        else:
            cfg(self.lbl_dt, text="Δt: -")
            cfg(self.lbl_stages_badge, text="Uniform Δt", text_color="gray")
            if hasattr(self, "lbl_current_phase"):
                cfg(self.lbl_current_phase, text="P1: -", text_color="gray")
            if getattr(self, "_lm_last_dt_tip", None) != "Time Step Size (Δt)":
                self._lm_last_dt_tip = "Time Step Size (Δt)"
                if hasattr(self.lbl_dt, "setToolTip"):
                    self.lbl_dt.setToolTip("Time Step Size (Δt)")
            if snap.parameter_checks and hasattr(self, "lbl_checks_echo"):
                cfg(self.lbl_checks_echo, text=self._format_checks_summary(snap.parameter_checks, "auto"))

        # Update KPI chips
        kpi = snap.kpi
        if kpi:
            cfg(self.kpi_cp, text=f"{kpi.get('running_cp', 0.0):.4f}")
            dcp = kpi.get("dcp_prev", 0.0)
            cfg(self.kpi_dcp, text=f"{dcp * 100.0:+.2f}%")
            cfg(self.kpi_stable, text=f"{kpi.get('stable_n', 0)} revs")
            cfg(self.kpi_peak, text=f"{kpi.get('peak_torque', 0.0):.2f} N·m")
            ct_val = kpi.get("ct")
            cfg(self.kpi_ct, text=f"{ct_val:.4f}" if ct_val is not None else "-")
            cfg(self.kpi_ripple, text=f"{kpi.get('ripple', 0.0) * 100.0:.1f}%")

            aitken = kpi.get("aitken_cp")
            cfg(self.kpi_aitken, text=f"{aitken:.4f}" if aitken is not None else "-")
            extra_r = kpi.get("extra_revs")
            cfg(self.kpi_extra_revs, text=f"+{extra_r} revs" if extra_r is not None else "-")

            req_stable = 2
            if self.live_params is not None and self.live_params.n_stable is not None:
                try:
                    ns_raw: Any = self.live_params.n_stable
                    req_stable = int(ns_raw)
                except (ValueError, TypeError):
                    req_stable = 2
            try:
                cur_stable = int(kpi.get("stable_n", 0) or 0)
            except (ValueError, TypeError):
                cur_stable = 0
            if cur_stable >= req_stable:
                cfg(self.kpi_verdict, text="STATUS: STABLE", text_color="#10B981")
                if hasattr(self, "kpi_status_card"):
                    cfg(self.kpi_status_card, border_color=("#10B981", "#059669"))
            else:
                cfg(self.kpi_verdict, text="STATUS: NOT YET STABLE", text_color="#F59E0B")
                if hasattr(self, "kpi_status_card"):
                    cfg(self.kpi_status_card, border_color=("#F59E0B", "#B45309"))

        # Update File Health List, Prior Run Link Card & Live Alerts Feed
        self._update_files_list(snap.file_stats)
        self._update_prior_card(getattr(snap, "continuity", None))
        self._update_alerts_feed(snap)

    @staticmethod
    def _format_checks_summary(parameter_checks: Dict[str, Any], dt_summary: str) -> str:
        """Format live entered-vs-detected checks concisely for the left sidebar."""
        sp_chk = parameter_checks.get("rotor_speed", {})
        sp_icon = "✓" if sp_chk.get("status") == "PASS" else "⚠"
        dt_chk = parameter_checks.get("time_step") or parameter_checks.get("step_size", {})
        dt_icon = "✓" if dt_chk.get("status", "PASS") == "PASS" else "⚠"
        saz_chk = parameter_checks.get("start_azimuth", {})
        saz_icon = "✓" if saz_chk.get("status", "PASS") == "PASS" else "⚠"
        ph_chk = parameter_checks.get("blade_phasing", {})
        ph_icon = "✓" if ph_chk.get("status", "PASS") == "PASS" else "⚠"
        return (
            f"Speed ω: {sp_icon} ({sp_chk.get('detected', '-')})\n"
            f"Time step: {dt_icon} ({dt_summary})\n"
            f"Start θ₀: {saz_icon} ({saz_chk.get('detected', '-')})\n"
            f"Phasing: {ph_icon} ({ph_chk.get('detected', 'pending')})"
        )

    @staticmethod
    def _file_role_sort_key(role: str):
        low = role.strip().lower()
        if low == "total":
            return (0, 0, "")
        if low.startswith("blade"):
            parts = low.split()
            try:
                b_idx = int(parts[-1])
            except ValueError:
                b_idx = 99
            return (1, b_idx, low)
        if low == "shaft":
            return (2, 0, "")
        if low == "fx":
            return (3, 0, "")
        if low == "fy":
            return (4, 0, "")
        if low == "fx_shaft":
            return (5, 0, "")
        if low == "fy_shaft":
            return (6, 0, "")
        return (9, 0, low)

    @staticmethod
    def _file_role_display_name(role: str) -> str:
        low = role.strip().lower()
        mapping = {
            "total": "Total",
            "shaft": "Shaft",
            "fx": "Fx",
            "fy": "Fy",
            "fx_shaft": "Shaft Fx",
            "fy_shaft": "Shaft Fy",
        }
        if low in mapping:
            return mapping[low]
        if low.startswith("blade"):
            parts = low.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                return f"Blade {parts[-1]}"
        return role.replace("_", " ").title()

    def _update_files_list(self, stats: Dict[str, LiveFileStats]):
        if not hasattr(self, "_file_row_widgets"):
            self._file_row_widgets = {}

        if hasattr(self, "lbl_files_badge"):
            n_f = len(stats)
            all_ok = n_f > 0 and all(st.status_color == "green" for st in stats.values())
            any_err = any(st.status_color == "red" for st in stats.values())
            b_col = "#EF4444" if any_err else ("#10B981" if all_ok else ("#F59E0B" if n_f > 0 else "gray"))
            self._cfg_if_changed(self.lbl_files_badge, text=f"{n_f} file{'s' if n_f != 1 else ''}", text_color=b_col)

        # Remove widgets for roles no longer present
        current_roles = set(stats.keys())
        cached_roles = set(self._file_row_widgets.keys())
        for role in cached_roles - current_roles:
            widgets = self._file_row_widgets.pop(role)
            try:
                widgets["row"].destroy()
            except Exception:
                pass

        sorted_items = sorted(stats.items(), key=lambda kv: self._file_role_sort_key(kv[0]))
        added_new_row = False

        # Update or create row for each role without destroying existing widgets
        for role, st in sorted_items:
            dot_col = "#10B981" if st.status_color == "green" else ("#F59E0B" if st.status_color == "amber" else "#EF4444")
            sub_text = f"{st.row_count:,} rows | Step {st.last_step} | {st.age_seconds:.0f}s ago"
            title_text = f"{self._file_role_display_name(role)}: {st.filename}"

            if role in self._file_row_widgets:
                w = self._file_row_widgets[role]
                self._cfg_if_changed(w["dot"], text_color=dot_col)
                self._cfg_if_changed(w["title"], text=title_text)
                self._cfg_if_changed(w["sub"], text=sub_text)
            else:
                added_new_row = True
                row = ctk.CTkFrame(self.files_list_box, corner_radius=4, fg_color=("gray86", "gray16"))
                row.pack(fill="x", pady=1)

                dot_lbl = ctk.CTkLabel(
                    row, text="●", text_color=dot_col, width=12, height=15,
                    font=ctk.CTkFont(size=11)
                )
                dot_lbl.pack(side="left", anchor="n", padx=(5, 0), pady=(3, 0))

                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", fill="x", expand=True, padx=(4, 5), pady=(2, 3))

                title_lbl = ctk.CTkLabel(
                    info, text=title_text, height=15,
                    font=ctk.CTkFont(size=10, weight="bold"),
                    anchor="w", justify="left", wraplength=204
                )
                title_lbl.pack(fill="x", anchor="w", pady=0)
                sub_lbl = ctk.CTkLabel(
                    info, text=sub_text, height=13,
                    font=ctk.CTkFont(size=9), text_color=("gray35", "gray65"),
                    anchor="w", justify="left", wraplength=204
                )
                sub_lbl.pack(fill="x", anchor="w", pady=0)

                self._file_row_widgets[role] = {
                    "row": row,
                    "dot": dot_lbl,
                    "title": title_lbl,
                    "sub": sub_lbl,
                }

        # Preserve canonical display order even when late-appearing files arrive asynchronously
        if added_new_row and len(self._file_row_widgets) > 1:
            for role, _ in sorted_items:
                if role in self._file_row_widgets:
                    r_frame = self._file_row_widgets[role]["row"]
                    r_frame.pack_forget()
                    r_frame.pack(fill="x", pady=1)

    def _open_edit_params_dialog(self):
        """Open the dedicated modal dialog to view and edit Live Monitor parameters."""
        p = self.live_params if self.live_params is not None else self._get_or_detect_params()
        LiveParamsEditDialog(
            parent=self,
            current_params=p,
            watch_folder=self.custom_watch_folder,
            on_apply=self._apply_edited_params
        )

    def _apply_edited_params(self, new_p: Params):
        """Apply parameters edited in LiveParamsEditDialog or toolbar and refresh live state."""
        self.live_params = new_p
        self._manual_params_override = True
        if hasattr(self, "v_live_tol"):
            self.v_live_tol.set(f"{new_p.stable_tol:g}")
        self._update_telemetry_echoes(new_p, model_name=self.detected_turb_model)
        self._save_settings()
        if self.worker and (self.is_monitoring or self.worker.is_alive()):
            snap = self.worker.update_params(new_p)
            self.latest_snapshot = snap
            self._update_telemetry(snap)
            self._render_graphs(snap)
        elif self.latest_snapshot:
            # Re-evaluate stability on existing snapshot rev_history with new tolerance
            tol_rel = (new_p.stable_tol or 5.0) / 100.0
            for r in self.latest_snapshot.rev_history:
                if r.get("dcp") is not None:
                    r["stable"] = abs(r["dcp"]) < tol_rel
            stable_n = 0
            for r in reversed(self.latest_snapshot.rev_history):
                if r.get("stable"):
                    stable_n += 1
                else:
                    break
            if self.latest_snapshot.kpi is not None:
                self.latest_snapshot.kpi["stable_n"] = stable_n
            self._update_telemetry(self.latest_snapshot)
            self._render_graphs(self.latest_snapshot)

    def _on_live_tol_change(self):
        """Handle inline stability tolerance (%) edits from the top toolbar."""
        try:
            raw = self.v_live_tol.get().strip().replace(",", ".")
            if not raw:
                return
            tol_val = float(raw)
            if tol_val <= 0:
                return
            p = self.live_params if self.live_params is not None else self._get_or_detect_params()
            if abs((p.stable_tol or 5.0) - tol_val) < 1e-9:
                return
            p.stable_tol = tol_val
            self._apply_edited_params(p)
        except ValueError:
            pass

    def _on_detect_button_clicked(self):
        """Interactive detection triggered by 'Detect' button in the left panel."""
        folder = self.custom_watch_folder
        if not folder or not os.path.isdir(folder):
            chosen = filedialog.askdirectory(title="Select Simulation Folder to Detect From")
            if chosen and os.path.isdir(chosen):
                from vawt_post.engine.parser import resolve_fluent_directory
                rf = resolve_fluent_directory(chosen)
                self.custom_watch_folder = os.path.abspath(rf if rf and os.path.isdir(rf) else chosen)
                self._manual_params_override = False
                self.live_params = None
                self.detected_turb_model = None
            else:
                return
        self._get_or_detect_params(force_detect=True, show_feedback=True)

    def _get_or_detect_params(self, force_detect: bool = False, show_feedback: bool = False) -> Params:
        """Autonomously determine simulation parameters for Live Monitor.
        
        Completely decoupled from the Main Input Tab:
          1. User-edited self.live_params (when force_detect is False).
          2. Auto-detection from self.custom_watch_folder.
          3. Sensible aerodynamic defaults (U=8.0, tsr=3.5, R=0.5, chord=0.06, n_blades=3, stable_tol=5.0).
        """
        if not force_detect and self.live_params is not None:
            if self._manual_params_override or not self.custom_watch_folder or self.custom_watch_folder == self._last_detected_folder:
                if hasattr(self, "v_live_tol"):
                    self.v_live_tol.set(f"{self.live_params.stable_tol:g}")
                self._update_telemetry_echoes(self.live_params, model_name=self.detected_turb_model)
                return self.live_params

        folder_changed = bool(
            self.custom_watch_folder
            and self._last_detected_folder
            and os.path.normcase(os.path.abspath(self.custom_watch_folder))
            != os.path.normcase(os.path.abspath(self._last_detected_folder))
        )
        if force_detect or folder_changed:
            self._manual_params_override = False

        det: Dict[str, Any] = {}
        if self.custom_watch_folder and os.path.isdir(self.custom_watch_folder):
            try:
                from vawt_post.engine.parser import detect_folder_simulation_parameters, resolve_fluent_directory
                rf = resolve_fluent_directory(self.custom_watch_folder)
                if rf and os.path.isdir(rf):
                    self.custom_watch_folder = os.path.abspath(rf)
                det = detect_folder_simulation_parameters(self.custom_watch_folder)
                self._last_detected_meta = dict(det) if det else {}
                self._last_detected_folder = self.custom_watch_folder
            except Exception as e:
                print(f"[LiveMonitor] Auto-detection error on {self.custom_watch_folder}: {e}")
                det = {}
                self._last_detected_meta = {}
        else:
            self._last_detected_meta = {}

        if show_feedback:
            if det:
                applied = []
                if "U" in det: applied.append(f"U = {det['U']} m/s")
                if "omega" in det: applied.append(f"ω = {det['omega']} rad/s")
                if "tsr" in det: applied.append(f"λ = {det['tsr']}")
                if "R" in det: applied.append(f"R = {det['R']} m")
                if "chord" in det: applied.append(f"c = {det['chord']} m")
                if "n_blades" in det: applied.append(f"N = {det['n_blades']}")
                if "dt" in det: applied.append(f"Δt = {det['dt']} s")
                if "turb_model" in det: applied.append(f"Model = {det['turb_model']}")
                messagebox.showinfo(
                    "Auto-Detection Successful",
                    f"Successfully loaded simulation parameters from:\n{self.custom_watch_folder}\n\n" + "\n".join(f" • {x}" for x in applied)
                )
            else:
                messagebox.showwarning(
                    "Auto-Detect Notice",
                    f"No simulation metadata or case files found in:\n{self.custom_watch_folder or 'No folder selected'}\n\nPlease verify that case (.cas.h5), report.xml, or monitor files exist."
                )

        if det.get("turb_model"):
            self.detected_turb_model = str(det["turb_model"])
        elif force_detect or folder_changed:
            self.detected_turb_model = None

        def _safe_float(getter, default):
            try:
                s = getter().strip().replace(",", ".")
                return float(s) if s else default
            except Exception:
                return default

        prev = None if folder_changed else self.live_params

        u_val = det.get("U")
        if u_val is None and prev and prev.U is not None:
            u_val = prev.U
        if u_val is None:
            u_val = 8.0

        r_val = det.get("R")
        if r_val is None and prev and prev.R is not None:
            r_val = prev.R
        if r_val is None:
            r_val = 0.5

        c_val = det.get("chord")
        if c_val is None and prev and prev.chord is not None:
            c_val = prev.chord
        if c_val is None:
            c_val = 0.06

        nb_val = det.get("n_blades")
        if nb_val is None and prev and prev.n_blades is not None:
            nb_val = prev.n_blades
        if nb_val is None:
            nb_val = 3

        w_val = det.get("omega")
        if w_val is None and prev and prev.omega is not None and not det.get("tsr"):
            w_val = prev.omega

        tsr_val = det.get("tsr")
        if tsr_val is None and prev and prev.tsr is not None and not det.get("omega"):
            tsr_val = prev.tsr

        if tsr_val is None and w_val is None:
            tsr_val = 3.5

        rho_val = det.get("rho")
        if rho_val is None and prev and prev.rho is not None:
            rho_val = prev.rho
        if rho_val is None:
            rho_val = 1.225

        mu_val = det.get("mu")
        if mu_val is None and prev and prev.mu is not None:
            mu_val = prev.mu
        if mu_val is None:
            mu_val = 1.7894e-5

        dt_val = det.get("dt")
        if dt_val is None and prev and prev.dt is not None and not force_detect:
            dt_val = prev.dt

        tol_val = (self.live_params.stable_tol if (self.live_params and self.live_params.stable_tol is not None) else None)
        if tol_val is None and hasattr(self, "v_live_tol"):
            tol_val = _safe_float(self.v_live_tol.get, 5.0)
        if tol_val is None:
            tol_val = 5.0

        nstable_val = self.live_params.n_stable if (self.live_params and self.live_params.n_stable is not None) else 2
        depth_val = self.live_params.depth if (self.live_params and self.live_params.depth is not None) else 1.0
        rot_dir_val = det.get("rotation_dir") or (getattr(prev, "rotation_dir", None) if prev else None) or (getattr(self.live_params, "rotation_dir", "ccw") if self.live_params else "ccw")
        theta0_val = det.get("theta0")
        if theta0_val is None:
            theta0_val = getattr(prev, "theta0", None) if prev else None
        if theta0_val is None:
            theta0_val = getattr(self.live_params, "theta0", 0.0) if self.live_params else 0.0
        bin_val = self.live_params.bin_deg if (self.live_params and self.live_params.bin_deg is not None) else 1.0

        p = Params(
            U=float(u_val),
            tsr=float(tsr_val) if tsr_val is not None else None,
            omega=float(w_val) if w_val is not None else None,
            R=float(r_val),
            chord=float(c_val),
            n_blades=int(nb_val),
            rho=float(rho_val),
            mu=float(mu_val),
            depth=float(depth_val),
            dt=float(dt_val) if dt_val is not None else None,
            stable_tol=float(tol_val),
            n_stable=nstable_val,
            bin_deg=float(bin_val),
            rotation_dir=str(rot_dir_val),
            theta0=float(theta0_val or 0.0),
        )
        self.live_params = p
        if hasattr(self, "v_live_tol"):
            self.v_live_tol.set(f"{p.stable_tol:g}")
        self._update_telemetry_echoes(p, model_name=self.detected_turb_model)
        self._save_settings()
        if force_detect and self.worker and (self.is_monitoring or self.worker.is_alive()):
            snap = self.worker.update_params(p)
            self.latest_snapshot = snap
            self._update_telemetry(snap)
            self._render_graphs(snap)
        return p

    def _update_telemetry_echoes(self, p: Params, model_name: Optional[str] = None):
        """Update parameter and derived physics labels on the left panel."""
        model_str = f"\nModel: {model_name}" if model_name else ""
        tsr_str = f"{p.tsr_val():.3f}" if (p.tsr is not None or (p.omega and p.U and p.R)) else "auto"
        tol_str = f"{p.stable_tol:g}%" if p.stable_tol is not None else "5%"
        th0_str = f"{getattr(p, 'theta0', 0.0) or 0.0:g}°"
        rdir_str = str(getattr(p, "rotation_dir", "ccw") or "ccw").upper()
        self.lbl_params_echo.configure(
            text=f"U = {p.U:.2f} m/s | R = {p.R:.3f} m | N = {p.n_blades}\nλ = {tsr_str} | ρ = {p.rho:.3f} | Tol = {tol_str}\nθ₀ = {th0_str} | Dir = {rdir_str}{model_str}"
        )
        omega = p.omega_val()
        Trev = 2.0 * math.pi / omega if omega > 0 else 0.0
        dt_txt = f"{p.dt:.3e} s" if p.dt else "auto"
        dtheta_txt = f"{omega * p.dt * 180.0 / math.pi:.2f}°" if (p.dt and omega > 0) else "-"
        steps_rev_txt = f"{round(Trev / p.dt):,}" if (p.dt and Trev > 0) else "-"
        self.lbl_derived_echo.configure(
            text=f"ω: {omega:.2f} rad/s ({omega*60/(2*math.pi):.1f} rpm)\nT_rev: {Trev:.4f} s\nΔt: {dt_txt}\nDeg/step: {dtheta_txt} | Steps/rev: {steps_rev_txt}"
        )

    # -------------------------------------------------------------------------
    # Actions & Worker Interaction
    # -------------------------------------------------------------------------
    def _reset_session_views(self):
        """Destroy cached file-health row widgets and clear alert feed before a new run or folder switch."""
        if hasattr(self, "_file_row_widgets"):
            for w in list(self._file_row_widgets.values()):
                try:
                    w["row"].destroy()
                except Exception:
                    pass
            self._file_row_widgets.clear()
        if hasattr(self, "files_list_box"):
            for child in list(self.files_list_box.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
        if hasattr(self, "lbl_files_badge"):
            self.lbl_files_badge.configure(text="0 files", text_color="gray")
        self._cleared_alert_count = 0
        self._cleared_alert_seq = 0
        self._last_alerts_signature = None
        self._recommended_theta0 = None
        self._rendered_alerts_count = 0
        self._last_rendered_graph_state = None
        self._refresh_pending = False
        self.latest_snapshot = None
        self._last_chart_snapshot = None
        if hasattr(self, "_probe_ui_cache"):
            self._probe_ui_cache.clear()
        if hasattr(self, "_saved_axis_limits"):
            self._saved_axis_limits.clear()
        if hasattr(self, "midspin_banner") and self.midspin_banner.winfo_manager():
            self.midspin_banner.pack_forget()
        if hasattr(self, "lbl_alerts_badge"):
            self.lbl_alerts_badge.configure(text="0", fg_color=("gray82", "#252B34"), text_color=("#64748B", "#9CA3AF"))
        if hasattr(self, "lbl_step"):
            self.lbl_step.configure(text="Step: -")
            self.lbl_time.configure(text="Time: -")
            self.lbl_revs.configure(text="Revs: 0.00")
            self.lbl_azimuth.configure(text="θ: -")
            self.lbl_speed.configure(text="Speed: - steps/s")
            self.lbl_eta.configure(text="ETA: -")
            self.lbl_fresh.configure(text="Freshness: -")
            self.lbl_dt.configure(text="Δt: -")
        if hasattr(self, "lbl_stages_badge"):
            self.lbl_stages_badge.configure(text="Uniform Δt", text_color="gray")
        if hasattr(self, "lbl_current_phase"):
            self.lbl_current_phase.configure(text="P1: -", text_color="gray")
        if hasattr(self, "lbl_dt") and hasattr(self.lbl_dt, "setToolTip"):
            self.lbl_dt.setToolTip("Time Step Size (Δt)")
        if hasattr(self, "kpi_cp"):
            self.kpi_cp.configure(text="-")
            self.kpi_dcp.configure(text="-")
            self.kpi_stable.configure(text="0 revs")
            self.kpi_peak.configure(text="-")
            self.kpi_ct.configure(text="-")
            self.kpi_ripple.configure(text="-")
            self.kpi_verdict.configure(text="STATUS: WAITING", text_color="gray")
        self._render_empty_alerts_placeholder()
        self._hide_all_probes(redraw=False)
        if hasattr(self, "fig") and hasattr(self, "_rebuild_plot_axes"):
            self._rebuild_plot_axes()

    def _toggle_monitoring(self):
        if not self.is_monitoring:
            self._start_monitoring()
        else:
            self._stop_monitoring()

    def _start_monitoring(self):
        # Live Monitor is completely independent from the Main Input Tab:
        # Prompt user to choose a watched folder if none is configured yet.
        from vawt_post.engine.parser import resolve_fluent_directory, autodetect
        if not self.custom_watch_folder or not os.path.isdir(self.custom_watch_folder):
            chosen = filedialog.askdirectory(title="Select dp0/FFF-{n} or Fluent Run Folder to Monitor")
            if chosen and os.path.isdir(chosen):
                resolved = resolve_fluent_directory(chosen)
                self.custom_watch_folder = os.path.abspath(resolved if resolved and os.path.isdir(resolved) else chosen)
                self._manual_params_override = False
                self.live_params = None
                self.detected_turb_model = None
            else:
                return
        else:
            resolved = resolve_fluent_directory(self.custom_watch_folder)
            if resolved and os.path.isdir(resolved):
                self.custom_watch_folder = os.path.abspath(resolved)

        p = self._get_or_detect_params()

        # Find monitor files exclusively from the watched folder
        f = autodetect(self.custom_watch_folder, p.n_blades or 3)
        f.prior_source = self.prior_source
        f.continuity_mode = self.continuity_mode

        assigned_files = [
            x for x in (
                [f.total, f.shaft, f.fx, f.fy, f.fx_shaft, f.fy_shaft]
                + (f.blades if f and f.blades else [])
            )
            if x and os.path.isfile(x)
        ]
        if not assigned_files:
            messagebox.showwarning(
                "No Monitor Files Available",
                "No valid Fluent monitor files (.out, .txt, .dat) found to watch in:\n"
                f"{self.custom_watch_folder or 'No folder selected'}\n\n"
                "Please verify that Fluent is writing monitor report files to this directory."
            )
            return

        self._reset_session_views()
        self._update_telemetry_echoes(p, model_name=self.detected_turb_model)
        dt_txt = f"{p.dt:.3e} s" if p.dt else "auto"
        self.lbl_stages_content.configure(
            text=f"Current: Δt = {dt_txt}\nSteps: Waiting for data...\nFlow Time: Waiting for data..."
        )

        try:
            self.worker = LiveMonitorWorker(
                p=p,
                f=f,
                snapshot_queue=self.snapshot_queue,
                graph_refresh_interval=self.refresh_interval_s,
                file_check_interval=self.file_check_interval_s,
                watch_dir=self.custom_watch_folder
            )
            self.worker.add_alert(
                "INFO",
                f"Live monitoring started ({len(assigned_files)} file(s), Δt={dt_txt}, θ₀={getattr(p, 'theta0', 0.0) or 0.0:g}°).",
                key="monitor_started",
            )
            self.is_monitoring = True
            self.is_paused = False
            self.time_until_refresh = self.refresh_interval_s
            self.worker.start()
            self._ensure_monitor_timers()
        except Exception as exc:
            self.is_monitoring = False
            self.worker = None
            messagebox.showerror("Live Monitor Error", f"Failed to start live monitor worker:\n{exc}")
            return

        self.btn_toggle.configure(text="⏹ Stop Monitoring", fg_color=("#DC2626", "#B91C1C"), hover_color=("#B91C1C", "#991B1B"))
        self.btn_refresh_now.configure(state="normal")
        self.btn_pause.configure(state="normal", text="⏸ Pause")
        self.btn_snapshot.configure(state="normal")
        self.btn_finalize.configure(state="normal")
        self.state_badge.configure(text="● Monitoring", text_color="#10B981")

    def _stop_monitoring(self):
        self.is_monitoring = False
        self.is_paused = False
        self._refresh_pending = False
        self._stop_monitor_timers()

        if self.worker:
            self.worker.stop()
            self.worker = None

        # Swap snapshot_queue so any in-flight late push from a terminating worker cannot bleed into future sessions
        while not self.snapshot_queue.empty():
            try:
                self.snapshot_queue.get_nowait()
            except Exception:
                break
        self.snapshot_queue = queue.Queue()

        self.btn_toggle.configure(text="▶ Start Monitoring", fg_color=("#16A34A", "#15803D"), hover_color=("#15803D", "#166534"))
        self.btn_refresh_now.configure(state="disabled")
        self.btn_pause.configure(state="disabled", text="⏸ Pause")
        self.btn_snapshot.configure(state="disabled")
        self.btn_finalize.configure(state="disabled")
        self.state_badge.configure(text="⏹ Stopped", text_color="gray")
        self.lbl_countdown.configure(text="⏱ Next: -", text_color=("royal blue", "#60A5FA"))

        if self.app and hasattr(self.app, "tab_btn_live"):
            is_cur = getattr(self.app, "current_page", "") == "live"
            self.app.tab_btn_live.configure(
                text="📡 Live Monitor",
                text_color="#FFFFFF" if is_cur else ("gray10", "gray90"),
            )

    def monitor_fluent_run(self, watch_dir: str, p: Optional[Params] = None, auto_switch_tab: bool = False):
        """Automatically configure and start live monitoring for an active Fluent simulation."""
        if not watch_dir or not os.path.isdir(watch_dir):
            return

        if self.is_monitoring and self.worker:
            self._stop_monitoring()

        from vawt_post.engine.parser import resolve_fluent_directory, autodetect
        prev_watch = self.custom_watch_folder
        rf = resolve_fluent_directory(watch_dir)
        self.custom_watch_folder = os.path.abspath(rf if rf and os.path.isdir(rf) else watch_dir)
        folder_switched = bool(
            prev_watch
            and os.path.normcase(os.path.abspath(prev_watch))
            != os.path.normcase(os.path.abspath(self.custom_watch_folder))
        )
        if folder_switched or (self.prior_source and not os.path.exists(self.prior_source)):
            self.prior_source = None
            if hasattr(self, "_update_prior_card"):
                self._update_prior_card(None)
        if p is not None:
            self.live_params = p
            self._manual_params_override = True
        else:
            self._manual_params_override = False
            self.live_params = None
            self.detected_turb_model = None
            p = self._get_or_detect_params(force_detect=True)

        self._reset_session_views()
        self._save_settings()
        f = autodetect(self.custom_watch_folder, p.n_blades or 3)
        f.prior_source = self.prior_source
        f.continuity_mode = self.continuity_mode
        self._update_telemetry_echoes(p, model_name=self.detected_turb_model)

        try:
            self.worker = LiveMonitorWorker(
                p=p,
                f=f,
                snapshot_queue=self.snapshot_queue,
                graph_refresh_interval=self.refresh_interval_s,
                file_check_interval=self.file_check_interval_s,
                watch_dir=self.custom_watch_folder
            )
            self.worker.add_alert(
                "INFO",
                f"Auto-attached to Fluent run in '{os.path.basename(self.custom_watch_folder or '')}' (θ₀={getattr(p, 'theta0', 0.0) or 0.0:g}°).",
                key="monitor_started",
            )
            self.is_monitoring = True
            self.is_paused = False
            self.time_until_refresh = self.refresh_interval_s
            self.worker.start()
            self._ensure_monitor_timers()

            self.btn_toggle.configure(text="⏹ Stop Monitoring", fg_color=("#DC2626", "#B91C1C"), hover_color=("#B91C1C", "#991B1B"))
            self.btn_refresh_now.configure(state="normal")
            self.btn_pause.configure(state="normal", text="⏸ Pause")
            self.btn_snapshot.configure(state="normal")
            self.btn_finalize.configure(state="normal")
            self.state_badge.configure(text="● Monitoring", text_color="#10B981")

            if self.app and hasattr(self.app, "tab_btn_live"):
                self.app.tab_btn_live.configure(text="📡 Live Monitor  ●", text_color="#10B981")

            if auto_switch_tab and self.app:
                if hasattr(self.app, "_switch_page"):
                    self.app._switch_page("live")
                elif hasattr(self.app, "select_page"):
                    self.app.select_page("live")
        except Exception as e:
            print(f"[LiveMonitor] Auto-monitor start notice: {e}")

    def _toggle_pause(self):
        if not self.is_monitoring or not self.worker:
            return
        if not self.is_paused:
            self.worker.pause_auto_refresh()
            self.is_paused = True
            self.btn_pause.configure(text="▶ Resume")
            self.state_badge.configure(text="⏸ Paused", text_color="#06B6D4")
            self.lbl_countdown.configure(text="⏱ Paused", text_color="#06B6D4")
            if self.app and hasattr(self.app, "tab_btn_live"):
                self.app.tab_btn_live.configure(text="📡 Live Monitor  ⏸", text_color="#06B6D4")
            self.worker.add_alert("INFO", "Auto-refresh paused by user.", key="pause_state")
        else:
            self.worker.resume_auto_refresh()
            self.is_paused = False
            self.btn_pause.configure(text="⏸ Pause")
            self.state_badge.configure(text="● Monitoring", text_color="#10B981")
            self.lbl_countdown.configure(text_color=("royal blue", "#60A5FA"))
            mins = int(self.time_until_refresh // 60)
            secs = int(self.time_until_refresh % 60)
            self.lbl_countdown.configure(text=f"⏱ Next: {mins}:{secs:02d}")
            self.worker.add_alert("INFO", "Auto-refresh resumed.", key="pause_state")

    def _refresh_now(self):
        """Immediately trigger heavy snapshot and graph redraw."""
        if self.worker:
            self.worker.request_immediate_refresh()
            self.time_until_refresh = self.refresh_interval_s

    def _save_settings(self):
        """Persist user live monitor preferences via ConfigManager."""
        if self.app and hasattr(self.app, "config_mgr"):
            try:
                lp_dict = None
                if self.live_params is not None:
                    lp_dict = {
                        "U": self.live_params.U,
                        "tsr": self.live_params.tsr,
                        "omega": self.live_params.omega,
                        "R": self.live_params.R,
                        "chord": self.live_params.chord,
                        "n_blades": self.live_params.n_blades,
                        "rho": self.live_params.rho,
                        "mu": self.live_params.mu,
                        "depth": self.live_params.depth,
                        "dt": self.live_params.dt,
                        "stable_tol": self.live_params.stable_tol,
                        "n_stable": self.live_params.n_stable,
                        "bin_deg": self.live_params.bin_deg,
                        "rotation_dir": getattr(self.live_params, "rotation_dir", "ccw"),
                        "theta0": getattr(self.live_params, "theta0", 0.0),
                    }
                cfg = {
                    "refresh_interval_s": self.refresh_interval_s,
                    "unit": self.interval_unit_var.get(),
                    "window_mode": self.window_mode,
                    "grid_layout": self.grid_layout_mode,
                    "focus_plot": self.focus_plot,
                    "autoscale": self.v_autoscale.get(),
                    "watched_folder": self.custom_watch_folder or "",
                    "prior_source": self.prior_source or "",
                    "continuity_mode": self.continuity_mode,
                    "live_params": lp_dict,
                    "manual_params_override": self._manual_params_override,
                    "ignored_midspin_folders": sorted(self._ignored_midspin_folders)[-20:],
                }
                self.app.config_mgr.set_live_settings(cfg)
            except Exception as e:
                print(f"[LiveMonitor] Settings save warning: {e}")

    def _update_prior_card(self, cont: Optional[Dict[str, Any]] = None) -> None:
        """Update the Prior Run Link sidebar card badge and status text."""
        if not hasattr(self, "lbl_prior_badge") or not hasattr(self, "lbl_prior_info"):
            return
        if not self.prior_source:
            self.lbl_prior_badge.configure(text="None", text_color="gray")
            self.lbl_prior_info.configure(
                text="Link backup folder or .zip to stitch restarts & recover lost steps.",
                text_color="#94A3B8",
            )
            return
        if cont and cont.get("enabled"):
            st = cont.get("status", "")
            if st == "gap_recovered":
                self.lbl_prior_badge.configure(text="Recovered", text_color="#F59E0B")
                self.lbl_prior_info.configure(text=cont.get("summary_text", ""), text_color="#F59E0B")
            elif st in ("overlap_verified", "exact_adjacent"):
                self.lbl_prior_badge.configure(text="Stitched", text_color="#10B981")
                self.lbl_prior_info.configure(text=cont.get("summary_text", ""), text_color="#10B981")
            else:
                self.lbl_prior_badge.configure(text="Warning", text_color="#F59E0B")
                self.lbl_prior_info.configure(text=cont.get("summary_text", ""), text_color="#F59E0B")
        else:
            self.lbl_prior_badge.configure(text="Linked", text_color="#10B981")
            self.lbl_prior_info.configure(
                text=f"Source: {os.path.basename(self.prior_source)}",
                text_color="#94A3B8",
            )

    def _apply_live_prior_change(self) -> None:
        self._save_settings()
        self._update_prior_card(None)
        if self.worker and (self.is_monitoring or self.worker.is_alive()):
            snap = self.worker.set_prior_source(self.prior_source, self.continuity_mode)
            self.latest_snapshot = snap
            self._update_telemetry(snap)
            self._render_graphs(snap, force=True)

    def _browse_live_prior_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Prior / Backup Simulation Folder")
        if folder and os.path.isdir(folder):
            self.prior_source = os.path.abspath(folder)
            self._apply_live_prior_change()

    def _browse_live_prior_zip(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Prior / Backup Archive (.zip)",
            filetypes=[("ZIP Archives", "*.zip"), ("All Files", "*.*")],
        )
        if path and os.path.isfile(path):
            self.prior_source = os.path.abspath(path)
            self._apply_live_prior_change()

    def _clear_live_prior(self) -> None:
        self.prior_source = None
        self._apply_live_prior_change()

    def _on_live_prior_mode_change(self) -> None:
        if hasattr(self, "_live_cont_label_to_code") and hasattr(self, "v_live_cont_mode"):
            self.continuity_mode = self._live_cont_label_to_code.get(self.v_live_cont_mode.get(), "auto")
        if self.prior_source:
            self._apply_live_prior_change()
        else:
            self._save_settings()

    def _on_interval_custom_change(self):
        """Handle custom number + unit interval change and synchronize file polling."""
        if getattr(self, "_updating_interval", False):
            return
        try:
            val_str = self.interval_num_var.get().strip()
            if not val_str:
                return
            val = float(val_str)
            if val <= 0:
                return
            unit = self.interval_unit_var.get()
            secs = val * 60.0 if "minute" in unit else val
            secs = max(2.0, min(secs, 3600.0))
            self.refresh_interval_s = secs
            self.file_check_interval_s = self._compute_file_check_interval(secs)
            if self.time_until_refresh > secs:
                self.time_until_refresh = secs
            if self.worker:
                self.worker.set_refresh_interval(secs)
                self.worker.set_file_check_interval(self.file_check_interval_s)
            self._save_settings()
        except ValueError:
            pass

    def _on_preset_interval_select(self, preset: str):
        self._updating_interval = True
        try:
            if "10 s" in preset:
                self.interval_num_var.set("10")
                self.interval_unit_var.set("seconds")
            elif "30 s" in preset:
                self.interval_num_var.set("30")
                self.interval_unit_var.set("seconds")
            elif "1 min" in preset:
                self.interval_num_var.set("1")
                self.interval_unit_var.set("minutes")
            elif "2 min" in preset:
                self.interval_num_var.set("2")
                self.interval_unit_var.set("minutes")
            elif "5 min" in preset:
                self.interval_num_var.set("5")
                self.interval_unit_var.set("minutes")
            elif "10 min" in preset:
                self.interval_num_var.set("10")
                self.interval_unit_var.set("minutes")
        finally:
            self._updating_interval = False
        self._on_interval_custom_change()

    def _on_window_change(self, val: str):
        self.window_mode = val
        self._save_settings()
        if self.latest_snapshot:
            self._render_graphs(self.latest_snapshot)

    def _on_layout_change(self, mode: str):
        self.grid_layout_mode = mode
        if hasattr(self, "focus_slot"):
            if mode == "🔍 Focus Mode":
                self.focus_slot.grid()
            else:
                self.focus_slot.grid_remove()

        self._save_settings()
        self._rebuild_plot_axes()
        if self.latest_snapshot:
            self._render_graphs(self.latest_snapshot)

    def _on_focus_plot_select(self, p_name: str):
        self.focus_plot = p_name
        self._save_settings()
        self._rebuild_plot_axes()
        if self.latest_snapshot:
            self._render_graphs(self.latest_snapshot)

    def _choose_watch_folder(self):
        f = filedialog.askdirectory(title="Select dp0/FFF-{n} or Fluent Run Folder")
        if f and os.path.isdir(f):
            was_monitoring = self.is_monitoring
            if was_monitoring:
                self._stop_monitoring()

            from vawt_post.engine.parser import resolve_fluent_directory, autodetect
            prev_watch = self.custom_watch_folder
            resolved = resolve_fluent_directory(f)
            target = resolved if resolved and os.path.isdir(resolved) else f
            self.custom_watch_folder = os.path.abspath(target)
            folder_switched = bool(
                prev_watch
                and os.path.normcase(os.path.abspath(prev_watch))
                != os.path.normcase(os.path.abspath(self.custom_watch_folder))
            )
            if folder_switched or (self.prior_source and not os.path.exists(self.prior_source)):
                self.prior_source = None
                if hasattr(self, "_update_prior_card"):
                    self._update_prior_card(None)
            self._manual_params_override = False
            self.live_params = None
            self.detected_turb_model = None
            self._reset_session_views()
            p = self._get_or_detect_params(force_detect=True)
            self._save_settings()
            try:
                det = getattr(self, "_last_detected_meta", {}) or {}
                found_files = autodetect(self.custom_watch_folder, p.n_blades or 3)
                num_files = len([
                    x for x in (
                        [found_files.total, found_files.shaft, found_files.fx, found_files.fy, found_files.fx_shaft, found_files.fy_shaft]
                        + (found_files.blades or [])
                    )
                    if x and os.path.isfile(x)
                ])

                info_lines = [f"Now watching simulation folder:\n{self.custom_watch_folder}"]
                if os.path.abspath(f) != os.path.abspath(self.custom_watch_folder):
                    info_lines.append(f"(Auto-resolved from Workbench project: {os.path.basename(f)})")
                if det:
                    summary_items = []
                    if "U" in det: summary_items.append(f"U = {det['U']} m/s")
                    if "omega" in det: summary_items.append(f"ω = {det['omega']} rad/s")
                    if "tsr" in det: summary_items.append(f"λ = {det['tsr']}")
                    if "R" in det: summary_items.append(f"R = {det['R']} m")
                    if "chord" in det: summary_items.append(f"c = {det['chord']} m")
                    if "n_blades" in det: summary_items.append(f"N = {det['n_blades']}")
                    if "dt" in det: summary_items.append(f"Δt = {det['dt']} s")
                    if "turb_model" in det: summary_items.append(f"Model = {det['turb_model']}")
                    if summary_items:
                        info_lines.append("\nAuto-detected Parameters:\n" + ", ".join(summary_items))
                    notes = det.get("setup_notes") or []
                    if notes:
                        info_lines.append("\nAuto-Detected Mesh & Setup Notes:\n• " + "\n• ".join(notes))
                info_lines.append(f"\nDiscovered {num_files} monitor report file(s).")
                if was_monitoring and num_files > 0:
                    self._start_monitoring()
                messagebox.showinfo("Watched Folder Set", "\n".join(info_lines))
            except Exception as e:
                if was_monitoring:
                    self._start_monitoring()
                messagebox.showinfo("Watched Folder", f"Now watching folder:\n{self.custom_watch_folder}\n\nNotice: {e}")

    def _get_live_output_dir(self) -> str:
        """Return an output directory dedicated to the active Live Monitor session."""
        if self.custom_watch_folder and os.path.isdir(self.custom_watch_folder):
            base = self.custom_watch_folder
            if os.path.basename(base).lower() == "fluent":
                parent = os.path.dirname(base)
                if os.path.basename(os.path.dirname(parent)).lower() == "dp0":
                    base = parent
            return os.path.join(base, "live_post_output")
        return os.path.expanduser("~/VAWT_live_results")

    def _on_autoscale_toggle(self):
        """Persist Autoscale toggle and immediately redraw axes."""
        self._save_settings()
        if self.latest_snapshot:
            self._render_graphs(self.latest_snapshot, force=True)

    def _take_snapshot(self):
        if not self.worker:
            return
        w_ref = self.worker
        out_dir = self._get_live_output_dir()
        self.btn_snapshot.configure(state="disabled")

        def _bg():
            try:
                p = w_ref.save_snapshot(out_dir, prefix="interim_live_snapshot")
                w_ref.add_alert("SUCCESS", f"Interim snapshot saved: {os.path.basename(p)}")
                def _done():
                    if not getattr(self, "_is_closing", False):
                        if self.is_monitoring:
                            self.btn_snapshot.configure(state="normal")
                        messagebox.showinfo("Snapshot Saved", f"Interim workbook & figures saved:\n{p}")
                self.after(0, _done)
            except Exception as e:
                err_msg = str(e)
                def _err():
                    if not getattr(self, "_is_closing", False):
                        if self.is_monitoring:
                            self.btn_snapshot.configure(state="normal")
                        messagebox.showerror("Snapshot Error", f"Could not create snapshot: {err_msg}")
                self.after(0, _err)

        threading.Thread(target=_bg, daemon=True, name="LiveSnapshotWorker").start()

    def _finalize_run(self):
        if not self.worker:
            return
        w_ref = self.worker
        out_dir = self._get_live_output_dir()
        self.btn_finalize.configure(state="disabled")

        def _bg():
            try:
                res = w_ref.finalize(out_dir)
                w_ref.add_alert("SUCCESS", f"Finalized offline pipeline in '{os.path.basename(out_dir)}'.")
                def _done():
                    if not getattr(self, "_is_closing", False):
                        if self.is_monitoring:
                            self.btn_finalize.configure(state="normal")
                        if self.app and hasattr(self.app, "results_frame"):
                            self.app.results_frame.set_results(res)
                        messagebox.showinfo(
                            "Finalize Run",
                            f"Offline analysis pipeline completed on accumulated live data!\nSaved to: {out_dir}",
                        )
                self.after(0, _done)
            except Exception as e:
                err_msg = str(e)
                def _err():
                    if not getattr(self, "_is_closing", False):
                        if self.is_monitoring:
                            self.btn_finalize.configure(state="normal")
                        messagebox.showerror("Finalize Error", f"Could not finalize run: {err_msg}")
                self.after(0, _err)

        threading.Thread(target=_bg, daemon=True, name="LiveFinalizeWorker").start()

    # -------------------------------------------------------------------------
    # Panel Collapse Toggles
    # -------------------------------------------------------------------------
    def _toggle_left_panel(self):
        if self.left_panel_visible:
            self.left_scroll.pack_forget()
            self.lbl_left_title.pack_forget()
            self.btn_collapse_left.configure(text="▶", width=24)
            self.btn_collapse_left.pack(side="left", padx=2, pady=2)
            self.left_panel.configure(width=34)
            self.left_panel_visible = False
        else:
            self.btn_collapse_left.pack_forget()
            self.lbl_left_title.pack(side="left")
            self.btn_collapse_left.configure(text="◀", width=22)
            self.btn_collapse_left.pack(side="right")
            self.left_panel.configure(width=268)
            self.left_scroll.pack(fill="both", expand=True, padx=4, pady=2)
            self.left_panel_visible = True

    def _toggle_right_panel(self):
        if self.right_panel_visible:
            self.right_scroll.pack_forget()
            self.lbl_right_title.pack_forget()
            self.btn_collapse_right.configure(text="◀", width=24)
            self.btn_collapse_right.pack(side="left", padx=2, pady=2)
            self.right_panel.configure(width=34)
            self.right_panel_visible = False
        else:
            self.btn_collapse_right.pack_forget()
            self.btn_collapse_right.configure(text="▶", width=22)
            self.btn_collapse_right.pack(side="left")
            self.lbl_right_title.pack(side="left", padx=(8, 0))
            self.right_panel.configure(width=272)
            self.right_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            self.right_panel_visible = True

