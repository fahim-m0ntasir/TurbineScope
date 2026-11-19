# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: AeroPost-VAWT Advanced Aerodynamics & Non-Destructive Excel Embedding
=============================================================================================
Autonomous module for:
1. Ingesting VAWT simulation data from an Excel workbook (.xlsx).
2. Generating 6 publication-grade aerodynamic figures via matplotlib & seaborn with proper sequential numbering:
   - Power Contribution by Azimuthal Quartile (Bar Chart)
   - Cumulative Moment Distribution (Line Graph)
   - Instantaneous Blade Tangential Load / Torque (Line Graph)
   - Geometric Kinematics vs. Azimuth (Dual-Axis Line Graph with Mandatory Induction Notice)
   - Global Rotor Performance Coefficients (Line Graph: Cp, Cm, Ct, Cy)
   - Cp Convergence History (Line Graph with Asymptotic Aitken Extrapolation)
3. Non-destructive Excel embedding with openpyxl image collision detection (scanning ws._images).
4. Updating the 'Summary' sheet with aerodynamic metrics (Cm, Ct, Extrema, Ripple, Quartile breakdown).
5. Appending complete academic captions with computed KPIs into the 'Captions' sheet.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter as COL

import matplotlib
if matplotlib.get_backend().lower() not in ("agg", "qtagg"):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns


# Styling tokens for Excel
HF = PatternFill("solid", fgColor="1F3864")  # Dark blue header
SF = PatternFill("solid", fgColor="D9E1F2")  # Soft blue section
PASS_F = PatternFill("solid", fgColor="E2EFDA")
PASS_T = Font(name="Arial", size=10, bold=True, color="276A3C")
WARN_F = PatternFill("solid", fgColor="FFF2CC")
WARN_T = Font(name="Arial", size=10, bold=True, color="B25E00")

F_N = Font(name="Arial", size=10)
F_B = Font(name="Arial", size=10, bold=True)
F_H = Font(name="Arial", size=10, bold=True, color="FFFFFF")
F_T = Font(name="Arial", size=14, bold=True, color="1F3864")


# =============================================================================
# Helper: Intelligent Header Search for Excel Sheets
# =============================================================================
def _load_sheet_with_smart_header(
    excel_path_or_wb: Any,
    sheet_name: str,
    key_col_patterns: List[str]
) -> pd.DataFrame:
    """
    Search the first 14 rows of an Excel worksheet to find the true table header row
    matching any of the key_col_patterns (requiring >= 2 columns), then construct a DataFrame.
    """
    close_wb = False
    if isinstance(excel_path_or_wb, str):
        wb = openpyxl.load_workbook(excel_path_or_wb, read_only=True, data_only=True)
        close_wb = True
    else:
        wb = excel_path_or_wb

    try:
        if sheet_name not in wb.sheetnames:
            raise ValueError(f"Sheet '{sheet_name}' not found in workbook. Available: {wb.sheetnames}")
        ws = wb[sheet_name]
        all_rows = list(ws.iter_rows(values_only=True))
    finally:
        if close_wb:
            wb.close()

    if not all_rows:
        return pd.DataFrame()

    best_header_row = 0
    for r_idx, row in enumerate(all_rows[:14]):
        non_empty = [c for c in row if c is not None]
        if len(non_empty) < 2:
            continue
        row_str = " ".join(str(cell) for cell in non_empty).lower()
        if any(pat.lower() in row_str for pat in key_col_patterns):
            best_header_row = r_idx
            break

    header_vals = [str(c).strip() if c is not None else f"Unnamed: {i}" for i, c in enumerate(all_rows[best_header_row])]
    data_rows = all_rows[best_header_row + 1:]
    return pd.DataFrame(data_rows, columns=header_vals)


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find the first matching column name using case-insensitive check and substring matching."""
    cols = list(df.columns)
    for cand in candidates:
        cand_low = cand.lower().strip()
        for col in cols:
            col_low = str(col).lower().strip()
            if cand_low == col_low or cand_low in col_low:
                return col
    return None


def _to_num_series(df: pd.DataFrame, col_name: Optional[str], default_val: float = 0.0) -> pd.Series:
    """Extract column as float series, coercing non-numeric values to NaN and filling with default."""
    if col_name and col_name in df.columns:
        s = pd.Series(pd.to_numeric(df[col_name], errors="coerce"), index=df.index, dtype=float)
        return s.fillna(default_val)
    return pd.Series(default_val, index=df.index, dtype=float)


# =============================================================================
# 1. Data Extraction Directives
# =============================================================================
def extract_aeropost_data(excel_path: str) -> Dict[str, Any]:
    """
    Extract VAWT simulation parameters and time/azimuth series from respective sheets:
    - Azimuth_Cp: theta, Cp, Cm, Ct, Cy
    - Blade_Azimuth: theta, T_blade_1, Cp contribution per blade
    - Alpha_Re: theta, alpha_geometric, W/Uinf
    - Revolutions: Rev, Mean Cp, Mean Cm, Ct, Cy, Stable?, Reported?
    """
    if not os.path.isfile(excel_path):
        raise FileNotFoundError(f"Excel workbook not found: {excel_path}")

    data: Dict[str, Any] = {}
    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    try:
        # Extract stable_tol from Parameters sheet if present (default 5.0%)
        stable_tol_pct = 5.0
        if "Parameters" in wb.sheetnames:
            for row in wb["Parameters"].iter_rows(values_only=True):
                if row and row[0] and "stability tolerance (relative)" in str(row[0]).lower():
                    try:
                        if row[1] is not None:
                            stable_tol_pct = float(str(row[1]))
                    except (ValueError, TypeError):
                        pass
                    break
        data["stable_tol_pct"] = stable_tol_pct

        # Sheet: Azimuth_Cp
        df_az = _load_sheet_with_smart_header(wb, "Azimuth_Cp", ["θ (deg)", "theta", "cp mean", "cm mean"])
        col_th = _find_column(df_az, ["θ (deg)", "theta (deg)", "theta", "azimuth"])
        col_cp = _find_column(df_az, ["cp mean", "rotor cp", "cp"])
        col_cm = _find_column(df_az, ["cm mean", "rotor cm", "cm"])
        col_ct = _find_column(df_az, ["ct mean", "thrust", "ct"])
        col_cy = _find_column(df_az, ["cy mean", "side force", "cy"])

        data["Azimuth_Cp"] = pd.DataFrame({
            "theta": _to_num_series(df_az, col_th, 0.0),
            "cp": _to_num_series(df_az, col_cp, 0.0),
            "cm": _to_num_series(df_az, col_cm, 0.0),
            "ct": _to_num_series(df_az, col_ct, np.nan) if col_ct else None,
            "cy": _to_num_series(df_az, col_cy, np.nan) if col_cy else None,
        }).dropna(subset=["theta"]).reset_index(drop=True)

        # Sheet: Blade_Azimuth (optional when workbook was generated without individual blade monitors)
        if "Blade_Azimuth" in wb.sheetnames:
            df_blade = _load_sheet_with_smart_header(wb, "Blade_Azimuth", ["θ (deg)", "t blade 1", "cp contribution"])
            col_b_th = _find_column(df_blade, ["θ (deg)", "theta (deg)", "theta"])
            col_b_t1 = _find_column(df_blade, ["t blade 1 (n·m)", "t blade 1", "m blade 1", "blade 1 torque"])
            col_b_cp = _find_column(df_blade, ["cp contribution per blade", "cp contribution", "cp blade"])

            data["Blade_Azimuth"] = pd.DataFrame({
                "theta": _to_num_series(df_blade, col_b_th, 0.0),
                "t_blade_1": _to_num_series(df_blade, col_b_t1, 0.0),
                "cp_contrib": _to_num_series(df_blade, col_b_cp, 0.0),
            }).dropna(subset=["theta"]).reset_index(drop=True)
        else:
            data["Blade_Azimuth"] = pd.DataFrame(columns=["theta", "t_blade_1", "cp_contrib"])

        # Sheet: Alpha_Re
        df_alpha = _load_sheet_with_smart_header(wb, "Alpha_Re", ["θ (deg)", "α geometric", "w / u∞", "chord reynolds"])
        col_a_th = _find_column(df_alpha, ["θ (deg)", "theta (deg)", "theta"])
        col_a_aoa = _find_column(df_alpha, ["α geometric (deg)", "alpha geometric", "alpha", "aoa"])
        col_a_w = _find_column(df_alpha, ["w / u∞", "w/u", "w / u", "relative velocity"])

        data["Alpha_Re"] = pd.DataFrame({
            "theta": _to_num_series(df_alpha, col_a_th, 0.0),
            "alpha_geom": _to_num_series(df_alpha, col_a_aoa, 0.0),
            "w_over_u": _to_num_series(df_alpha, col_a_w, 1.0),
        }).dropna(subset=["theta"]).reset_index(drop=True)

        # Sheet: Revolutions
        df_rev = _load_sheet_with_smart_header(wb, "Revolutions", ["rev", "window start", "mean cp", "mean cm"])
        col_r_rev = _find_column(df_rev, ["rev", "revolution"])
        col_r_cp = _find_column(df_rev, ["mean cp", "cp"])
        col_r_cm = _find_column(df_rev, ["mean cm", "cm"])
        col_r_ct = _find_column(df_rev, ["ct", "thrust"])
        col_r_cy = _find_column(df_rev, ["cy", "side force"])
        col_r_st = _find_column(df_rev, ["stable?", "stable"])
        col_r_rep = _find_column(df_rev, ["reported?", "reported"])

        data["Revolutions"] = pd.DataFrame({
            "rev": df_rev[col_r_rev] if col_r_rev else np.arange(1, len(df_rev) + 1),
            "mean_cp": pd.to_numeric(df_rev[col_r_cp], errors="coerce") if col_r_cp else np.zeros(len(df_rev)),
            "mean_cm": pd.to_numeric(df_rev[col_r_cm], errors="coerce") if col_r_cm else np.zeros(len(df_rev)),
            "ct": pd.to_numeric(df_rev[col_r_ct], errors="coerce") if col_r_ct else None,
            "cy": pd.to_numeric(df_rev[col_r_cy], errors="coerce") if col_r_cy else None,
            "stable": df_rev[col_r_st].astype(str) if col_r_st else "yes",
            "reported": df_rev[col_r_rep].astype(str) if col_r_rep else "",
        })
    finally:
        wb.close()

    return data


# =============================================================================
# 2. Graph Generation & Analysis Protocol (6 Engineering Figures)
# =============================================================================
def _compute_quartile_shares(df_blade: pd.DataFrame, df_az: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    """Compute non-overlapping azimuthal quadrant shares [45,135), [135,225), [225,315), [315,45)."""
    src = df_blade if (df_blade is not None and not df_blade.empty and "cp_contrib" in df_blade.columns) else None
    val_col = "cp_contrib"
    if src is None and df_az is not None and not df_az.empty and "cp" in df_az.columns:
        src = df_az
        val_col = "cp"
    if src is None or len(src) == 0:
        return {"pct_up": 0.0, "pct_lee": 0.0, "pct_down": 0.0, "pct_wind": 0.0}

    th = np.asarray(src["theta"].values, dtype=float) % 360.0
    cp_b = np.asarray(src[val_col].values, dtype=float)
    s_up = float(np.nansum(cp_b[(th >= 45.0) & (th < 135.0)]))
    s_lee = float(np.nansum(cp_b[(th >= 135.0) & (th < 225.0)]))
    s_down = float(np.nansum(cp_b[(th >= 225.0) & (th < 315.0)]))
    s_wind = float(np.nansum(cp_b[(th >= 315.0) | (th < 45.0)]))
    tot = s_up + s_lee + s_down + s_wind
    if abs(tot) > 1e-9:
        denom = abs(tot)
        return {
            "pct_up": (s_up / denom) * 100.0,
            "pct_lee": (s_lee / denom) * 100.0,
            "pct_down": (s_down / denom) * 100.0,
            "pct_wind": (s_wind / denom) * 100.0,
        }
    return {"pct_up": 0.0, "pct_lee": 0.0, "pct_down": 0.0, "pct_wind": 0.0}


def generate_aeropost_figures(
    data: Dict[str, Any],
    output_dir: Optional[str] = None,
    start_fig_num: int = 1,
    return_meta: bool = False,
    dpi: int = 130
) -> Any:
    """
    Generate the 6 publication-grade figures formatted to high engineering standards:
    1. Power Contribution by Azimuthal Quartile (Bar Chart)
    2. Cumulative Moment Distribution (Line Graph)
    3. Instantaneous Blade Tangential Load / Torque (Line Graph)
    4. Geometric Kinematics vs. Azimuth (Dual-Axis Line Graph with Mandatory Induction Notice)
    5. Global Rotor Performance Coefficients (Line Graph)
    6. Cp Convergence History (Line Graph with Asymptotic Aitken Extrapolation)

    start_fig_num controls sequential figure numbering (e.g., 12, 13, 14, ...).
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="aeropost_figs_")
    os.makedirs(output_dir, exist_ok=True)

    figures: Dict[str, str] = {}
    figures_meta: List[Dict[str, Any]] = []

    rc_overrides: Dict[Any, Any] = {
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.labelsize": 11,
        "axes.titlesize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "figure.titlesize": 13,
        "mathtext.fontset": "dejavusans",
    }

    with sns.axes_style("whitegrid"), plt.rc_context(rc_overrides):
        # -------------------------------------------------------------------------
        # 1. Power Contribution by Azimuthal Quartile (Bar Chart)
        # -------------------------------------------------------------------------
        df_blade = data.get("Blade_Azimuth", pd.DataFrame())
        df_az_src = data.get("Azimuth_Cp", pd.DataFrame())

        q_shares = _compute_quartile_shares(df_blade, df_az_src)
        pct_up = q_shares["pct_up"]
        pct_lee = q_shares["pct_lee"]
        pct_down = q_shares["pct_down"]
        pct_wind = q_shares["pct_wind"]

        quartiles = [
            "Upwind\n[45°–135°)",
            "Leeward\n[135°–225°)",
            "Downwind\n[225°–315°)",
            "Windward\n[315°–45°)"
        ]
        pcts = [pct_up, pct_lee, pct_down, pct_wind]
        colors = ["#1F4E79", "#D1495B", "#2A9D8F", "#E9A03B"]

        f1_num = start_fig_num + 0
        f1_filename = f"{f1_num:02d}_power_contribution_quartiles.png"
        f1_path = os.path.join(output_dir, f1_filename)
        fig1, ax1 = plt.subplots(figsize=(7.5, 4.8), dpi=dpi)
        try:
            bars = ax1.bar(quartiles, pcts, color=colors, width=0.55, edgecolor="#1A2530", linewidth=1.2, zorder=3)
            ax1.axhline(0, color="#1A2530", linewidth=1.0, linestyle="-", zorder=2)
            ax1.set_ylabel("Power Contribution Share [%]", fontweight="bold")
            ax1.set_title(f"Figure {f1_num}: Power Contribution by Azimuthal Quartile", fontweight="bold", pad=12)
            ymin = min(0.0, min(pcts) * 1.25) - 5.0
            ymax = max(5.0, max(pcts) * 1.25 + 10.0)
            ax1.set_ylim(ymin, ymax)

            for bar, pct in zip(bars, pcts):
                y_pos = bar.get_height() + (1.5 if bar.get_height() >= 0 else -3.5)
                ax1.text(bar.get_x() + bar.get_width() / 2.0, y_pos, f"{pct:.1f}%", ha="center", va="bottom" if bar.get_height() >= 0 else "top", fontweight="bold", fontsize=10, color="#1F2937")

            ax1.grid(True, linestyle="--", alpha=0.5, axis="y")
            fig1.tight_layout()
            fig1.savefig(f1_path, dpi=dpi)
        finally:
            plt.close(fig1)
        figures[f1_filename] = f1_path
        figures["01_power_contribution_quartiles.png"] = f1_path  # alias for backward-compatibility

        dominant_sector = quartiles[int(np.argmax(pcts))].split("\n")[0]
        figures_meta.append({
            "id": f"Fig {f1_num:02d}",
            "num": f1_num,
            "file": f1_filename,
            "title": "Power Contribution by Azimuthal Quartile",
            "banner_title": f"Figure {f1_num}: Power Contribution by Azimuthal Quartile",
            "caption": (
                f"Figure {f1_num}: Sectoral aerodynamic power contribution across four rotation quadrants: "
                f"Upwind [45°–135°] ({pct_up:.1f}%), Leeward [135°–225°] ({pct_lee:.1f}%), "
                f"Downwind [225°–315°] ({pct_down:.1f}%), and Windward [315°–45°] ({pct_wind:.1f}%). "
                f"Total power generation is dominated by the {dominant_sector} quadrant."
            )
        })

        # -------------------------------------------------------------------------
        # 2. Cumulative Moment Distribution (Line Graph)
        # -------------------------------------------------------------------------
        df_az = data["Azimuth_Cp"].sort_values("theta").reset_index(drop=True)
        th_az = df_az["theta"].values
        cm_vals = df_az["cm"].values

        cm_cum = np.cumsum(cm_vals)
        if len(cm_cum) > 0:
            max_accum = float(np.max(cm_cum))
            min_accum = float(np.min(cm_cum))
            accum_range = max_accum - min_accum if (max_accum - min_accum) > 1e-9 else 1.0
            if max_accum > 1e-9:
                cm_norm = (cm_cum / max_accum) * 100.0
            else:
                cm_norm = ((cm_cum - min_accum) / accum_range) * 100.0
        else:
            cm_norm = np.array([], dtype=float)

        f2_num = start_fig_num + 1
        f2_filename = f"{f2_num:02d}_cumulative_moment_distribution.png"
        f2_path = os.path.join(output_dir, f2_filename)
        fig2, ax2 = plt.subplots(figsize=(7.5, 4.8), dpi=dpi)
        try:
            ax2.plot(th_az, cm_norm, color="#1F4E79", linewidth=2.4, label=r"Normalized Cumulative Moment $\sum C_m$")
            ax2.axhline(100.0, color="#10B981", linestyle="--", linewidth=1.2, alpha=0.8, label="100% Accumulated Torque")
            ax2.axhline(50.0, color="#64748B", linestyle=":", linewidth=1.0, alpha=0.8, label="50% Median Threshold")

            ax2.axvspan(0, 180, color="#EBF3FB", alpha=0.6, label="Upwind Half [0°–180°]")
            ax2.axvspan(180, 360, color="#F8FAFC", alpha=0.4, label="Downwind Half [180°–360°]")

            ax2.set_xlim(0, 360)
            ax2.set_xticks(range(0, 361, 60))
            ax2.set_xlabel(r"Rotor Azimuth Angle $\theta$ [deg]", fontweight="bold")
            ax2.set_ylabel(r"Cumulative Moment Distribution [% of Max]", fontweight="bold")
            ax2.set_title(f"Figure {f2_num}: Cumulative Moment Distribution vs. Azimuth", fontweight="bold", pad=12)
            ax2.legend(loc="lower right", frameon=True, framealpha=0.92)
            ax2.grid(True, linestyle="--", alpha=0.55)
            fig2.tight_layout()
            fig2.savefig(f2_path, dpi=dpi)
        finally:
            plt.close(fig2)
        figures[f2_filename] = f2_path
        figures["02_cumulative_moment_distribution.png"] = f2_path

        idx_50 = np.where(cm_norm >= 50.0)[0]
        th_50 = float(th_az[idx_50[0]]) if len(idx_50) > 0 else 180.0
        figures_meta.append({
            "id": f"Fig {f2_num:02d}",
            "num": f2_num,
            "file": f2_filename,
            "title": "Cumulative Moment Distribution",
            "banner_title": f"Figure {f2_num}: Cumulative Moment Distribution (0° to 360°)",
            "caption": (
                f"Figure {f2_num}: Normalized cumulative aerodynamic moment coefficient Cm accumulated over a complete 360° "
                f"rotor revolution (scaling 0% to 100%). Half-cycle accumulated torque reaches 50% of total cycle work at θ = {th_50:.1f}°."
            )
        })

        # -------------------------------------------------------------------------
        # 3. Instantaneous Blade Tangential Load / Torque (Line Graph)
        # -------------------------------------------------------------------------
        if not df_blade.empty and "t_blade_1" in df_blade.columns and "theta" in df_blade.columns:
            t1_vals = np.asarray(df_blade["t_blade_1"].values, dtype=float)
            th_b = np.asarray(df_blade["theta"].values, dtype=float)
        else:
            t1_vals = np.array([], dtype=float)
            th_b = np.array([], dtype=float)
        has_t1 = len(t1_vals) > 0 and np.any(np.isfinite(t1_vals))
        t_max_idx = int(np.nanargmax(t1_vals)) if has_t1 else 0
        t_min_idx = int(np.nanargmin(t1_vals)) if has_t1 else 0

        f3_num = start_fig_num + 2
        f3_filename = f"{f3_num:02d}_blade_tangential_load.png"
        f3_path = os.path.join(output_dir, f3_filename)
        fig3, ax3 = plt.subplots(figsize=(7.5, 4.8), dpi=dpi)
        try:
            if has_t1:
                ax3.plot(th_b, t1_vals, color="#2563EB", linewidth=2.2, label=r"Blade 1 Torque $T_{\mathrm{blade\,1}}$")
            ax3.axhline(0, color="#1A2530", linestyle="--", linewidth=1.1, alpha=0.85, label="Zero Torque Baseline")
            ax3.axvspan(0, 180, color="#EBF3FB", alpha=0.5, label="Upwind Sector")

            if has_t1:
                ax3.plot(th_b[t_max_idx], t1_vals[t_max_idx], marker="o", color="#DC2626", markersize=6)
                ax3.annotate(
                    f"Peak Load: {t1_vals[t_max_idx]:.3f} N·m\n@ {th_b[t_max_idx]:.1f}°",
                    xy=(th_b[t_max_idx], t1_vals[t_max_idx]),
                    xytext=(th_b[t_max_idx] + 15, t1_vals[t_max_idx] * 0.95),
                    arrowprops=dict(facecolor="#DC2626", shrink=0.08, width=1.0, headwidth=5),
                    fontweight="bold", fontsize=9, color="#991B1B"
                )
            else:
                ax3.text(
                    0.5, 0.5, "Individual blade torque data unavailable in this workbook.",
                    transform=ax3.transAxes, ha="center", va="center", fontsize=10, color="#64748B"
                )

            ax3.set_xlim(0, 360)
            ax3.set_xticks(range(0, 361, 60))
            ax3.set_xlabel(r"Blade Azimuth Angle $\theta$ [deg]", fontweight="bold")
            ax3.set_ylabel("Dimensional Torque (N·m)", fontweight="bold")
            ax3.set_title(f"Figure {f3_num}: Instantaneous Blade Tangential Load / Torque vs. Azimuth", fontweight="bold", pad=12)
            ax3.legend(loc="upper right", frameon=True, framealpha=0.92)
            ax3.grid(True, linestyle="--", alpha=0.55)
            fig3.tight_layout()
            fig3.savefig(f3_path, dpi=dpi)
        finally:
            plt.close(fig3)
        figures[f3_filename] = f3_path
        figures["03_blade_tangential_load.png"] = f3_path

        if has_t1:
            f3_caption = (
                f"Figure {f3_num}: Instantaneous tangential aerodynamic driving torque (N·m per m depth) on Blade 1 versus "
                f"blade azimuth angle θ. Peak driving torque of {t1_vals[t_max_idx]:.3f} N·m occurs at θ = {th_b[t_max_idx]:.1f}°, "
                f"with minimum torque of {t1_vals[t_min_idx]:.3f} N·m at θ = {th_b[t_min_idx]:.1f}° indicating dynamic stall recovery."
            )
        else:
            f3_caption = (
                f"Figure {f3_num}: Instantaneous tangential aerodynamic driving torque on Blade 1 versus blade azimuth angle θ "
                f"(individual blade torque data unavailable in this workbook)."
            )
        figures_meta.append({
            "id": f"Fig {f3_num:02d}",
            "num": f3_num,
            "file": f3_filename,
            "title": "Instantaneous Blade Tangential Load",
            "banner_title": f"Figure {f3_num}: Instantaneous Blade Tangential Load / Torque vs. Azimuth",
            "caption": f3_caption
        })

        # -------------------------------------------------------------------------
        # 4. Geometric Kinematics vs. Azimuth (Dual-Axis Line Graph with Mandatory Warning)
        # -------------------------------------------------------------------------
        df_alpha = data["Alpha_Re"].sort_values("theta").reset_index(drop=True)
        th_k = df_alpha["theta"].values
        aoa = df_alpha["alpha_geom"].values
        w_u = df_alpha["w_over_u"].values

        f4_num = start_fig_num + 3
        f4_filename = f"{f4_num:02d}_geometric_kinematics.png"
        f4_path = os.path.join(output_dir, f4_filename)
        fig4, ax4_1 = plt.subplots(figsize=(7.5, 4.8), dpi=dpi)
        try:
            ax4_2 = ax4_1.twinx()

            line1 = ax4_1.plot(th_k, aoa, color="#1F4E79", linewidth=2.2, label=r"Geometric Angle of Attack $\alpha_{\mathrm{geom}}$")
            line2 = ax4_2.plot(th_k, w_u, color="#D97706", linewidth=2.0, linestyle="--", label=r"Normalized Relative Velocity $W / U_\infty$")

            ax4_1.set_xlim(0, 360)
            ax4_1.set_xticks(range(0, 361, 60))
            ax4_1.set_xlabel(r"Blade Azimuth Angle $\theta$ [deg]", fontweight="bold")
            ax4_1.set_ylabel(r"Geometric Angle of Attack $\alpha_{\mathrm{geom}}$ [deg]", color="#1F4E79", fontweight="bold")
            ax4_2.set_ylabel(r"Normalized Relative Velocity $W / U_\infty$ [-]", color="#D97706", fontweight="bold")
            ax4_1.tick_params(axis="y", labelcolor="#1F4E79")
            ax4_2.tick_params(axis="y", labelcolor="#D97706")

            alert_text = "Notice: Geometric kinematics reflect theoretical inflow and ignore rotor flow deceleration/induction."
            ax4_1.text(
                0.5, 0.04, alert_text,
                transform=ax4_1.transAxes, ha="center", va="bottom",
                fontsize=8.5, fontweight="bold", color="#78350F",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#FEF3C7", edgecolor="#F59E0B", alpha=0.92)
            )

            lines = line1 + line2
            labels = [str(l.get_label()) for l in lines]
            ax4_1.legend(lines, labels, loc="upper right", frameon=True, framealpha=0.92)
            ax4_1.set_title(f"Figure {f4_num}: Geometric Kinematics vs. Azimuth (Analytical Inflow)", fontweight="bold", pad=12)
            ax4_1.grid(True, linestyle="--", alpha=0.55)
            fig4.tight_layout()
            fig4.savefig(f4_path, dpi=dpi)
        finally:
            plt.close(fig4)
        figures[f4_filename] = f4_path
        figures["04_geometric_kinematics.png"] = f4_path

        aoa_min = float(np.min(aoa)) if len(aoa) > 0 else 0.0
        aoa_max = float(np.max(aoa)) if len(aoa) > 0 else 0.0
        wu_min = float(np.min(w_u)) if len(w_u) > 0 else 0.0
        wu_max = float(np.max(w_u)) if len(w_u) > 0 else 0.0
        figures_meta.append({
            "id": f"Fig {f4_num:02d}",
            "num": f4_num,
            "file": f4_filename,
            "title": "Geometric Kinematics vs. Azimuth",
            "banner_title": f"Figure {f4_num}: Geometric Kinematics vs. Azimuth (Inflow Deceleration Notice)",
            "caption": (
                f"Figure {f4_num}: Theoretical blade inflow conditions versus blade azimuth angle θ: geometric angle of attack "
                f"α_geom (extrema [{aoa_min:.1f}°, {aoa_max:.1f}°]) and normalized relative velocity W/U_inf (range [{wu_min:.2f}, {wu_max:.2f}]). "
                f"Notice: Kinematic analytical values do not account for rotor induction flow deceleration."
            )
        })

        # -------------------------------------------------------------------------
        # 5. Global Rotor Performance Coefficients (Line Graph)
        # -------------------------------------------------------------------------
        f5_num = start_fig_num + 4
        f5_filename = f"{f5_num:02d}_global_rotor_coefficients.png"
        f5_path = os.path.join(output_dir, f5_filename)
        fig5, ax5 = plt.subplots(figsize=(7.5, 4.8), dpi=dpi)
        try:
            ax5.plot(th_az, df_az["cp"].values, color="#1E3A8A", linewidth=2.3, linestyle="-", label=r"Power Coefficient $C_p(\theta)$")
            ax5.plot(th_az, df_az["cm"].values, color="#059669", linewidth=2.0, linestyle="-.", label=r"Moment Coefficient $C_m(\theta)$")

            if df_az["ct"] is not None and not df_az["ct"].isna().all():
                ax5.plot(th_az, df_az["ct"].values, color="#DC2626", linewidth=1.8, linestyle="--", label=r"Thrust Coefficient $C_t(\theta)$")
            if df_az["cy"] is not None and not df_az["cy"].isna().all():
                ax5.plot(th_az, df_az["cy"].values, color="#7C3AED", linewidth=1.8, linestyle=":", label=r"Lateral Force Coefficient $C_y(\theta)$")

            ax5.axhline(0, color="#1A2530", linestyle="-", linewidth=0.9, alpha=0.7)
            ax5.set_xlim(0, 360)
            ax5.set_xticks(range(0, 361, 60))
            ax5.set_xlabel(r"Rotor Azimuth Angle $\theta$ [deg]", fontweight="bold")
            ax5.set_ylabel(r"Dimensionless Aerodynamic Coefficient [-]", fontweight="bold")
            ax5.set_title(f"Figure {f5_num}: Global Rotor Performance Coefficients (Cp, Cm, Ct, Cy)", fontweight="bold", pad=12)
            ax5.legend(loc="upper right", frameon=True, framealpha=0.92)
            ax5.grid(True, linestyle="--", alpha=0.55)
            fig5.tight_layout()
            fig5.savefig(f5_path, dpi=dpi)
        finally:
            plt.close(fig5)
        figures[f5_filename] = f5_path
        figures["05_global_rotor_coefficients.png"] = f5_path

        ct_mean_val = np.mean(df_az["ct"].dropna().values) if df_az["ct"] is not None and not df_az["ct"].isna().all() else None
        cy_mean_val = np.mean(df_az["cy"].dropna().values) if df_az["cy"] is not None and not df_az["cy"].isna().all() else None
        ct_str = f"{ct_mean_val:.4f}" if ct_mean_val is not None else "n/a"
        cy_str = f"{cy_mean_val:.4f}" if cy_mean_val is not None else "n/a"

        figures_meta.append({
            "id": f"Fig {f5_num:02d}",
            "num": f5_num,
            "file": f5_filename,
            "title": "Global Rotor Performance Coefficients",
            "banner_title": f"Figure {f5_num}: Global Rotor Performance Coefficients (Cp, Cm, Ct, Cy)",
            "caption": (
                f"Figure {f5_num}: Simultaneous evolution of macroscopic rotor aerodynamic coefficients across rotor azimuth θ: "
                f"power coefficient Cp (mean = {np.mean(df_az['cp'].values):.4f}), moment coefficient Cm (mean = {np.mean(df_az['cm'].values):.4f}), "
                f"thrust coefficient Ct (mean = {ct_str}), and lateral force coefficient Cy (mean = {cy_str})."
            )
        })

        # -------------------------------------------------------------------------
        # 6. Cp Convergence History (Line Graph)
        # -------------------------------------------------------------------------
        df_rev = data["Revolutions"].dropna(subset=["mean_cp"]).reset_index(drop=True)
        rev_nums = df_rev["rev"].values
        cp_revs = df_rev["mean_cp"].values
        tol_pct = float(data.get("stable_tol_pct", 5.0) or 5.0)

        f6_num = start_fig_num + 5
        f6_filename = f"{f6_num:02d}_cp_convergence_history.png"
        f6_path = os.path.join(output_dir, f6_filename)
        cp_extrap = None
        fig6, ax6 = plt.subplots(figsize=(7.5, 4.8), dpi=dpi)
        try:
            ax6.plot(rev_nums, cp_revs, marker="o", color="#2563EB", linewidth=2.0, markersize=5.5, label=r"Revolution-Averaged $\overline{C}_p$")

            rep_mask = df_rev["reported"].str.upper().str.contains("REPORTED")
            if rep_mask.any():
                ax6.plot(rev_nums[rep_mask], cp_revs[rep_mask], marker="s", color="#D97706", linestyle="none", markersize=7.5, label="Reported Revolutions")

            if len(cp_revs) >= 3:
                p0, p1, p2 = float(cp_revs[-3]), float(cp_revs[-2]), float(cp_revs[-1])
                denom = p2 - 2.0 * p1 + p0
                if abs(denom) > 1e-6 and abs(p1 - p0) > 1e-12:
                    ratio_a = (p2 - p1) / (p1 - p0)
                    cand = p2 - ((p2 - p1) ** 2) / denom
                    if 0.0 < ratio_a < 0.9 and np.isfinite(cand) and abs(cand - p2) <= max(1.0, abs(p2) * 2.0):
                        cp_extrap = cand
                        ax6.axhline(cp_extrap, color="#10B981", linestyle="--", linewidth=1.5, label=f"Aitken Asymptotic limit $C_{{p,\\infty}} = {cp_extrap:.4f}$")

            last_cp = float(cp_revs[-1]) if len(cp_revs) > 0 else 0.0
            band_half = abs(last_cp) * (tol_pct / 100.0)
            ax6.axhspan(last_cp - band_half, last_cp + band_half, color="#10B981", alpha=0.15, label=f"±{tol_pct:g}% Convergence Band")

            ax6.set_xlabel("Revolution Number [-]", fontweight="bold")
            ax6.set_ylabel(r"Mean Rotor Power Coefficient $\overline{C}_p$ [-]", fontweight="bold")
            ax6.set_title(f"Figure {f6_num}: Rotor Power Coefficient (Cp) Convergence History", fontweight="bold", pad=12)
            ax6.legend(loc="best", frameon=True, framealpha=0.92)
            ax6.grid(True, linestyle="--", alpha=0.55)
            fig6.tight_layout()
            fig6.savefig(f6_path, dpi=dpi)
        finally:
            plt.close(fig6)
        figures[f6_filename] = f6_path
        figures["06_cp_convergence_history.png"] = f6_path

        n_revs = len(cp_revs)
    last_dcp = float(abs(cp_revs[-1] - cp_revs[-2]) / abs(cp_revs[-2])) if n_revs >= 2 and abs(cp_revs[-2]) > 1e-6 else 0.0
    is_stable = (last_dcp < (tol_pct / 100.0)) if n_revs >= 2 else False
    aitken_str = f"{cp_extrap:.4f}" if cp_extrap is not None else "n/a"

    figures_meta.append({
        "id": f"Fig {f6_num:02d}",
        "num": f6_num,
        "file": f6_filename,
        "title": "Cp Convergence History",
        "banner_title": f"Figure {f6_num}: Rotor Power Coefficient (Cp) Convergence History",
        "caption": (
            f"Figure {f6_num}: Revolution-averaged power coefficient Cp convergence history across {n_revs} revolutions "
            f"(last revolution change ΔCp = {last_dcp * 100.0:.2f}%). Asymptotic Aitken extrapolated limit is Cp_inf = {aitken_str}, "
            f"demonstrating {'PERIODIC STABLE' if is_stable else 'TRANSIENT/UNSTABLE'} status within ±{tol_pct:g}% tolerance."
        )
    })

    quartile_info = {
        "pct_up": pct_up,
        "pct_lee": pct_lee,
        "pct_down": pct_down,
        "pct_wind": pct_wind,
    }

    if return_meta:
        return figures, figures_meta, quartile_info
    return figures


# =============================================================================
# 3. Non-Destructive Excel Embedding & Workbook Updating
# =============================================================================
def update_summary_sheet_aeropost(wb: openpyxl.Workbook, summary: Dict[str, Any], quartile_info: Dict[str, Any]) -> None:
    """Safely populate and update the Summary sheet with aerodynamic KPIs and quartile breakdown."""
    if "Summary" in wb.sheetnames:
        ws = wb["Summary"]
    else:
        ws = wb.create_sheet("Summary", 0)
        ws.cell(row=1, column=1, value="VAWT CFD Post-Processor: Results Summary").font = F_T
        for col_idx, h in enumerate(["Metric / Parameter", "Value", "Unit", "Notes & Status"], 1):
            c = ws.cell(row=4, column=col_idx, value=h)
            c.font, c.fill = F_H, HF

    existing_metrics: Dict[str, int] = {}
    for r in range(4, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if v:
            existing_metrics[str(v).strip().lower()] = r

    updates = [
        ("Mean moment coefficient Cm of reported revolutions", summary["cm_mean"], "-", "0.0000", "Cm = M / (½ρU²A·R)"),
        ("Mean thrust coefficient Ct (Fx) of reported revolutions", summary["ct_mean"] if summary["ct_mean"] is not None else "n/a", "-", "0.0000", "Fx / (½ρU²A)"),
        ("Mean side-force coefficient Cy (Fy) of reported revolutions", summary["cy_mean"] if summary.get("cy_mean") is not None else "n/a", "-", "0.0000", "Fy / (½ρU²A)"),
        ("Instantaneous Cp (max / min) of reported revolutions", f"{summary['cp_max']:.3f} / {summary['cp_min']:.3f}", "-", None, "Extrema across reported revolutions"),
        ("Peak-to-peak ripple ΔCp (max - min) of reported revolutions", summary.get("delta_cp", summary["cp_max"] - summary["cp_min"]), "-", "0.0000", "Max(Cp) - Min(Cp) across reported revolutions"),
        ("Peak-to-peak ripple ΔCm (max - min) of reported revolutions", summary.get("delta_cm", summary.get("cm_max", 0.0) - summary.get("cm_min", 0.0)), "-", "0.0000", "Max(Cm) - Min(Cm) across reported revolutions"),
        ("Peak blade loading (T_blade,max)", summary.get("t_blade_max", 0.0), "N·m", "0.0000", f"Maximum single-blade torque at θ = {summary.get('theta_max', 0.0):.1f}°"),
        ("Convergence stability flag", summary["status"], "-", None, f"Last 2 revs relative ΔCp = {summary['last_dcp_pct']:.2f}% (Threshold < {summary.get('stable_tol_pct', 5.0):g}%)"),
        ("Cp ripple (peak-to-peak / mean) of reported revolutions", summary["ripple_pp"], "-", "0.00", "(Cp_max - Cp_min) / Cp_mean across reported revolutions"),
    ]

    for lbl, val, unit, num_fmt, note in updates:
        lbl_key = lbl.lower()
        matched_row = None
        for k, r_idx in existing_metrics.items():
            if lbl_key == k:
                matched_row = r_idx
                break
        if not matched_row:
            for k, r_idx in existing_metrics.items():
                if lbl_key in k or k in lbl_key:
                    matched_row = r_idx
                    break
        if matched_row:
            cur_val = ws.cell(row=matched_row, column=2).value
            if not (isinstance(cur_val, str) and cur_val.startswith("=")):
                c_val = ws.cell(row=matched_row, column=2, value=val)
                if num_fmt and isinstance(val, (int, float)):
                    c_val.number_format = num_fmt
            ws.cell(row=matched_row, column=3, value=unit).font = F_N
            ws.cell(row=matched_row, column=4, value=note).font = F_N
        else:
            r_new = ws.max_row + 1
            ws.cell(row=r_new, column=1, value=lbl).font = F_B
            c_val = ws.cell(row=r_new, column=2, value=val)
            c_val.font = F_N
            if num_fmt and isinstance(val, (int, float)):
                c_val.number_format = num_fmt
            ws.cell(row=r_new, column=3, value=unit).font = F_N
            ws.cell(row=r_new, column=4, value=note).font = F_N

    # Dedicated section for Quartile Performance & Stability
    pct_up = quartile_info.get("pct_up", 0.0) / 100.0
    pct_lee = quartile_info.get("pct_lee", 0.0) / 100.0
    pct_down = quartile_info.get("pct_down", 0.0) / 100.0
    pct_wind = quartile_info.get("pct_wind", 0.0) / 100.0

    q_rows = [
        ("Power share: Upwind sector [45°–135°]", pct_up, "%", "0.0%", "Rotor front quadrant power extraction"),
        ("Power share: Leeward sector [135°–225°]", pct_lee, "%", "0.0%", "Leeward dynamic wake transition quadrant"),
        ("Power share: Downwind sector [225°–315°]", pct_down, "%", "0.0%", "Downwind passage power extraction"),
        ("Power share: Windward sector [315°–45°]", pct_wind, "%", "0.0%", "Upstream advancing quadrant power extraction"),
    ]
    for qlbl, qval, qunit, qfmt, qnote in q_rows:
        qlbl_key = qlbl.lower()
        matched_row = None
        for k, r_idx in existing_metrics.items():
            if qlbl_key in k or k in qlbl_key:
                matched_row = r_idx
                break
        if matched_row:
            c_qv = ws.cell(row=matched_row, column=2, value=qval)
            if qfmt and isinstance(qval, (int, float)):
                c_qv.number_format = qfmt
        else:
            r_q = ws.max_row + 1
            ws.cell(row=r_q, column=1, value=qlbl).font = F_B
            c_qv = ws.cell(row=r_q, column=2, value=qval)
            c_qv.font = F_N
            if qfmt and isinstance(qval, (int, float)):
                c_qv.number_format = qfmt
            ws.cell(row=r_q, column=3, value=qunit).font = F_N
            ws.cell(row=r_q, column=4, value=qnote).font = F_N


def update_captions_sheet_aeropost(wb: openpyxl.Workbook, figures_meta: List[Dict[str, Any]]) -> None:
    """Safely append generated academic figure captions with computed KPIs into Captions sheet."""
    if "Captions" in wb.sheetnames:
        wcap = wb["Captions"]
    else:
        wcap = wb.create_sheet("Captions")
        wcap.cell(row=1, column=1, value="VAWT CFD Post-Processor: Publication Figure Captions").font = F_T
        headers = ["Figure ID", "Figure File", "Title", "Generated Academic Caption with Computed KPIs"]
        for col_idx, h in enumerate(headers, 1):
            c = wcap.cell(row=3, column=col_idx, value=h)
            c.font, c.fill = F_H, HF
            c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        wcap.row_dimensions[3].height = 28
        for col, width in zip("ABCD", [12, 34, 28, 90]):
            wcap.column_dimensions[col].width = width

    existing_files = set()
    for r in range(4, wcap.max_row + 1):
        fn = wcap.cell(row=r, column=2).value
        if fn:
            existing_files.add(str(fn).strip())

    next_row = max(4, wcap.max_row + 1)
    for meta in figures_meta:
        if meta["file"] in existing_files:
            continue
        c1 = wcap.cell(row=next_row, column=1, value=meta["id"])
        c1.font = F_B
        c2 = wcap.cell(row=next_row, column=2, value=meta["file"])
        c2.font = F_N
        c3 = wcap.cell(row=next_row, column=3, value=meta["title"])
        c3.font = F_B
        c4 = wcap.cell(row=next_row, column=4, value=meta["caption"])
        c4.font = F_N
        c4.alignment = Alignment(wrap_text=True, vertical="center")
        wcap.row_dimensions[next_row].height = 42
        next_row += 1


def embed_aeropost_figures_safe(
    excel_path: str,
    data: Optional[Dict[str, Any]] = None,
    figures_dir: Optional[str] = None,
    figures: Optional[Dict[str, str]] = None,
    sheet_name: str = "Graphs",
    start_row: Optional[int] = None
) -> Tuple[int, Dict[str, Any]]:
    """
    Safely embed figures into the Excel workbook:
    1. Inspects existing images to calculate sequential start_fig_num and max_occupied_row.
    2. Generates figures with sequential numbering (e.g. Fig 12..17) if data is provided.
    3. Embeds figures in a 2-column grid layout with formatted title headers.
    4. Updates the Summary sheet with aerodynamic KPIs.
    5. Appends figure entries to the Captions sheet.
    """
    if data is None:
        data = extract_aeropost_data(excel_path)

    summary = compute_aerodynamic_summary(data)

    wb = openpyxl.load_workbook(excel_path)
    try:
        if sheet_name in wb.sheetnames:
            ws: Any = wb[sheet_name]
        else:
            ws = wb.create_sheet(sheet_name, 1)
            ws.cell(row=1, column=1, value="Generated Publication Figures (AeroPost-VAWT)").font = F_T

        if figures_dir is None:
            base_dir = os.path.dirname(os.path.abspath(excel_path))
            base_name = os.path.splitext(os.path.basename(excel_path))[0]
            figures_dir = os.path.join(base_dir, f"{base_name}_figures")
        os.makedirs(figures_dir, exist_ok=True)

        # Determine sequential figure numbering from existing images or Captions
        existing_fig_count = 0
        if "Captions" in wb.sheetnames:
            wcap = wb["Captions"]
            for r in range(4, wcap.max_row + 1):
                val = wcap.cell(row=r, column=1).value
                if val and str(val).lower().startswith("fig"):
                    existing_fig_count += 1
        elif hasattr(ws, "_images") and ws._images:
            existing_fig_count = len(ws._images)

        # Check if AeroPost figures are already embedded in the Graphs sheet
        existing_titles = set()
        for r in range(1, ws.max_row + 1):
            for c in (1, 14):
                v_title = ws.cell(row=r, column=c).value
                if v_title:
                    existing_titles.add(str(v_title).strip().lower())

        already_embedded = any("power contribution" in t and "quartile" in t for t in existing_titles)

        # Compute non-overlapping quartile percentages directly (falls back to Azimuth_Cp if Blade_Azimuth missing)
        quartile_info = _compute_quartile_shares(data.get("Blade_Azimuth", pd.DataFrame()), data.get("Azimuth_Cp"))

        if already_embedded:
            # Avoid duplicating image drawings if already inserted by write_excel
            update_summary_sheet_aeropost(wb, summary, quartile_info)
            wb.save(excel_path)
            return ws.max_row, summary

        start_fig_num = existing_fig_count + 1 if existing_fig_count > 0 else 1

        # Generate figures with proper sequential numbering and academic metadata
        figs_dict, figures_meta, quartile_info = generate_aeropost_figures(
            data,
            output_dir=figures_dir,
            start_fig_num=start_fig_num,
            return_meta=True
        )

        # Calculate bounding box for collision-free embedding
        max_occupied_row = max(1, ws.max_row)
        if hasattr(ws, "_images") and ws._images:
            for img in ws._images:
                img_row = 1
                if hasattr(img, "anchor"):
                    if isinstance(img.anchor, str):
                        m = re.search(r"\d+", img.anchor)
                        if m:
                            img_row = int(m.group(0))
                    elif hasattr(img.anchor, "_from") and hasattr(img.anchor._from, "row"):
                        img_row = img.anchor._from.row + 1

                height_px = 380.0
                anchor_ext = getattr(getattr(img, "anchor", None), "ext", None)
                if anchor_ext is not None and getattr(anchor_ext, "cy", None):
                    height_px = float(anchor_ext.cy) / 9525.0
                elif hasattr(img, "height") and isinstance(img.height, (int, float)) and img.height < 5000:
                    height_px = float(img.height)

                span_rows = max(12, int(height_px / 20.0) + 2)
                max_occupied_row = max(max_occupied_row, img_row + span_rows)

        if start_row is None:
            start_row = max(3, max_occupied_row + 3)

        title_font = Font(name="Arial", size=11, bold=True, color="1F3864")
        section_fill = PatternFill("solid", fgColor="D9E1F2")

        current_row: int = int(start_row)
        placed_idx = 0
        for meta in figures_meta:
            fig_path = figs_dict.get(meta["file"])
            if not fig_path or not os.path.isfile(fig_path):
                continue

            col_idx = 1 if (placed_idx % 2 == 0) else 14
            col_letter = COL(col_idx)

            if placed_idx % 2 == 0 and placed_idx > 0:
                current_row += 28

            end_col = col_idx + 11
            ws.merge_cells(start_row=current_row - 1, start_column=col_idx, end_row=current_row - 1, end_column=end_col)
            for col_c in range(col_idx, end_col + 1):
                c_fill = ws.cell(row=current_row - 1, column=col_c)
                c_fill.fill = section_fill
            c_title = ws.cell(row=current_row - 1, column=col_idx, value=meta["banner_title"])
            c_title.font = title_font
            c_title.alignment = Alignment(vertical="center", indent=1)

            try:
                xl_img = XLImage(fig_path)
                scale = min(1.0, 680.0 / xl_img.width)
                xl_img.width = int(xl_img.width * scale)
                xl_img.height = int(xl_img.height * scale)
                ws.add_image(xl_img, f"{col_letter}{current_row}")
            except Exception as e:
                ws.cell(row=current_row, column=col_idx, value=f"[Image Embedding Error: {e}]")
            placed_idx += 1

        # Update Summary Sheet
        update_summary_sheet_aeropost(wb, summary, quartile_info)

        # Update Captions Sheet
        update_captions_sheet_aeropost(wb, figures_meta)

        wb.save(excel_path)
        return current_row + 28, summary
    finally:
        wb.close()


# =============================================================================
# 4. Aerodynamic Stability Summary Evaluation
# =============================================================================
def compute_aerodynamic_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate key aerodynamic stability metrics and convergence parameters according to AeroPost protocol."""
    df_az = data["Azimuth_Cp"]
    df_blade = data.get("Blade_Azimuth", pd.DataFrame())
    df_rev = data["Revolutions"].dropna(subset=["mean_cp"])

    cp_arr = np.asarray(df_az["cp"].values, dtype=float)
    cm_arr = np.asarray(df_az["cm"].values, dtype=float)
    ct_arr = np.asarray(df_az["ct"].values, dtype=float) if df_az["ct"] is not None else None
    cy_arr = np.asarray(df_az["cy"].values, dtype=float) if df_az["cy"] is not None else None

    # 1. Mean Forces & Moments: arithmetic mean of Ct, Cy, and Cm
    cp_mean = float(np.nanmean(cp_arr)) if np.any(np.isfinite(cp_arr)) else 0.0
    cm_mean = float(np.nanmean(cm_arr)) if np.any(np.isfinite(cm_arr)) else 0.0
    ct_mean = float(np.nanmean(ct_arr)) if (ct_arr is not None and np.any(np.isfinite(ct_arr))) else None
    cy_mean = float(np.nanmean(cy_arr)) if (cy_arr is not None and np.any(np.isfinite(cy_arr))) else None

    # 2. Peak-to-Peak Ripple (ΔCp & ΔCm): Max(Cp) - Min(Cp) and Max(Cm) - Min(Cm)
    cp_max = float(np.nanmax(cp_arr)) if np.any(np.isfinite(cp_arr)) else 0.0
    cp_min = float(np.nanmin(cp_arr)) if np.any(np.isfinite(cp_arr)) else 0.0
    delta_cp = cp_max - cp_min
    cm_max = float(np.nanmax(cm_arr)) if np.any(np.isfinite(cm_arr)) else 0.0
    cm_min = float(np.nanmin(cm_arr)) if np.any(np.isfinite(cm_arr)) else 0.0
    delta_cm = cm_max - cm_min
    ripple_pp = float(delta_cp / abs(cp_mean)) if abs(cp_mean) > 1e-6 else 0.0

    # 3. Peak Blade Loading: maximum single-blade torque (T_blade,max) and exact azimuth (theta_max)
    t_blade_max = 0.0
    theta_max = 0.0
    if not df_blade.empty and "t_blade_1" in df_blade.columns and len(df_blade) > 0:
        t_vals = np.asarray(df_blade["t_blade_1"].values, dtype=float)
        th_vals = np.asarray(df_blade["theta"].values, dtype=float)
        if np.any(np.isfinite(t_vals)):
            idx_max = int(np.nanargmax(t_vals))
            t_blade_max = float(t_vals[idx_max])
            theta_max = float(th_vals[idx_max])

    # 4. Convergence Stability Flag: Compare Mean Cp of last two revs against stable_tol_pct (default 5.0%)
    tol_pct = float(data.get("stable_tol_pct", 5.0) or 5.0)
    rev_cp = df_rev["mean_cp"].values
    n_revs = len(rev_cp)
    last_dcp = float(abs(rev_cp[-1] - rev_cp[-2]) / abs(rev_cp[-2])) if n_revs >= 2 and abs(rev_cp[-2]) > 1e-6 else 0.0
    is_stable = (last_dcp < (tol_pct / 100.0)) if n_revs >= 2 else False
    status_str = "PERIODIC STABLE" if is_stable else "TRANSIENT/UNSTABLE"

    summary = {
        "n_revolutions": n_revs,
        "cp_mean": cp_mean,
        "cm_mean": cm_mean,
        "ct_mean": ct_mean,
        "cy_mean": cy_mean,
        "cp_max": cp_max,
        "cp_min": cp_min,
        "delta_cp": delta_cp,
        "cm_max": cm_max,
        "cm_min": cm_min,
        "delta_cm": delta_cm,
        "t_blade_max": t_blade_max,
        "theta_max": theta_max,
        "ripple_pp": ripple_pp,
        "last_dcp_pct": last_dcp * 100.0,
        "stable_tol_pct": tol_pct,
        "is_stable": is_stable,
        "status": status_str
    }
    return summary


# =============================================================================
# 5. Master Workflow & CLI Interface
# =============================================================================
def process_and_embed_aeropost_graphs(
    excel_path: str,
    figures_dir: Optional[str] = None,
    verbose: bool = False
) -> Dict[str, Any]:
    """
    High-level entry point:
    1. Extracts data from excel_path.
    2. Computes the 6 figures with sequential figure numbers.
    3. Safely embeds the figures into the Excel workbook's 'Graphs' sheet.
    4. Updates 'Summary' and 'Captions' sheets.
    5. Returns aerodynamic summary metrics dictionary silently without terminal dump.
    """
    data = extract_aeropost_data(excel_path)
    final_row, summary = embed_aeropost_figures_safe(
        excel_path=excel_path,
        data=data,
        figures_dir=figures_dir
    )
    if verbose:
        print(f"[AeroPost-VAWT] Successfully embedded figures into {excel_path} (occupied rows up to {final_row}).")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="AeroPost-VAWT: Rigorous Aerodynamic Plotting & Non-Destructive Excel Embedding"
    )
    parser.add_argument("excel_file", help="Path to the VAWT simulation Excel workbook (.xlsx)")
    parser.add_argument("--outdir", default=None, help="Directory to save generated figure PNGs (optional)")
    parser.add_argument("--verbose", action="store_true", help="Print operational logs")
    args = parser.parse_args()

    try:
        process_and_embed_aeropost_graphs(args.excel_file, figures_dir=args.outdir, verbose=args.verbose)
    except Exception as e:
        print(f"\n[AeroPost-VAWT Error]: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
