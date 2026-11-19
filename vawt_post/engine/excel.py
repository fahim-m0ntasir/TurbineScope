# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Excel Export Engine
============================================
Generates a structured, styled Excel workbook (.xlsx) via openpyxl
containing Summary, Graphs, Parameters, Revolutions, Azimuth_Cp, Blade_Azimuth,
Alpha_Re, Processed, and Raw_Data sheets with native charts and live formulas.
"""
from __future__ import annotations

import datetime as _dt
import math
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from openpyxl import Workbook
import openpyxl.chart as _xl_chart
from openpyxl.chart import BarChart, Reference, ScatterChart
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as COL

Series: Any = getattr(_xl_chart, "Series")

APP_NAME = "VAWT CFD Post-Processor"

# Professional palette and typography
HF = PatternFill("solid", fgColor="1F3864")  # Dark blue header
SF = PatternFill("solid", fgColor="D9E1F2")  # Soft blue section
WF = PatternFill("solid", fgColor="FDE9D9")  # Soft warning fill
YF = PatternFill("solid", fgColor="FFF2CC")  # Highlighted row fill

PASS_F = PatternFill("solid", fgColor="E2EFDA")  # Soft green
PASS_T = Font(name="Arial", size=10, bold=True, color="276A3C")
WARN_F = PatternFill("solid", fgColor="FFF2CC")  # Soft yellow/orange
WARN_T = Font(name="Arial", size=10, bold=True, color="B25E00")
FAIL_F = PatternFill("solid", fgColor="FCE4D6")  # Soft red
FAIL_T = Font(name="Arial", size=10, bold=True, color="C00000")
INFO_F = PatternFill("solid", fgColor="EDF2F8")  # Soft blue/gray
INFO_T = Font(name="Arial", size=10, bold=True, color="1F3864")

F_N = Font(name="Arial", size=10)
F_B = Font(name="Arial", size=10, bold=True)
F_H = Font(name="Arial", size=10, bold=True, color="FFFFFF")
F_IN = Font(name="Arial", size=10, color="0000FF")
F_T = Font(name="Arial", size=14, bold=True, color="1F3864")


def _hdr(ws, row: int, labels: list, col: int = 1, height: Optional[float] = None) -> None:
    for i, t in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=t)
        c.font, c.fill = F_H, HF
        c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    if height:
        ws.row_dimensions[row].height = height


def _put(ws, row: int, col: int, v: Any, font: Font = F_N, fmt: Optional[str] = None, fill: Optional[PatternFill] = None, wrap: bool = False):
    if isinstance(v, float) and not math.isfinite(v):
        v = "n/a"
        fmt = None
    c = ws.cell(row=row, column=col, value=v)
    c.font = font
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if wrap:
        c.alignment = Alignment(wrap_text=True, vertical="top")
    return c


def write_excel(
    R: Dict[str, Any],
    figs: Dict[str, str],
    path: str,
    log: Callable[[str], None] = print
) -> None:
    """Write complete post-processing results into a structured Excel workbook."""
    p, f, d, revs, sel, az, res = R["p"], R["files"], R["data"], R["revs"], R["sel"], R["az"], R["res"]
    wb = Workbook()
    nb = p.n_blades if d["have_blades"] else 0

    # ---------------- 1. Summary Sheet ----------------
    ws: Any = wb.active
    ws.title = "Summary"
    _put(ws, 1, 1, f"{APP_NAME}: Results Summary", F_T)

    preset_banner = p.preset_name if p.preset_name else "None (Custom manual input)"
    _put(ws, 2, 1, f"Turbine Preset Used: {preset_banner}  |  Generated {_dt.datetime.now():%Y-%m-%d %H:%M:%S}", F_B, fill=SF)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=4)

    _put(
        ws, 3, 1,
        "Definitions: Cp = ω·M / (½ρU³·2R·depth),  Cm = M / (½ρU²·2R·depth·R),  Ct = Fx / (½ρU²·2R·depth)."
    )

    _hdr(ws, 4, ["Metric / Parameter", "Value", "Unit", "Notes & Status"])
    sel_row_indices = [5 + i for i, r in enumerate(revs) if r.get("selected")]
    if not sel_row_indices:
        sel_row_indices = list(range(5, 5 + len(revs)))
    is_contiguous_sel = (
        len(sel_row_indices) > 0
        and sel_row_indices == list(range(sel_row_indices[0], sel_row_indices[-1] + 1))
    )
    first_sel_row = sel_row_indices[0] if sel_row_indices else 5
    last_sel_row = sel_row_indices[-1] if sel_row_indices else (len(revs) + 4)

    def _sel_avg_formula(col_let: str) -> str:
        if is_contiguous_sel or not sel_row_indices:
            return f"=AVERAGE(Revolutions!{col_let}{first_sel_row}:{col_let}{last_sel_row})"
        refs = ",".join(f"Revolutions!{col_let}{r_idx}" for r_idx in sel_row_indices)
        return f"=AVERAGE({refs})"

    tol_pct = float(getattr(p, "stable_tol", 5.0) or 5.0)
    is_contiguous_revs = len(revs) >= 2 and (revs[-1]["k"] - revs[-2]["k"] == 1)
    if is_contiguous_revs and abs(revs[-2]["cp"]) > 1e-6:
        last_dcp_rel = float(abs(revs[-1]["cp"] - revs[-2]["cp"]) / abs(revs[-2]["cp"]))
    else:
        last_dcp_rel = abs(float(res.get("cp_last_change") or 0.0))

    is_stable_flag = bool(res.get("is_stable", False))
    cp_blades_disp = res["cp_blades_only"] if res.get("cp_blades_only") is not None else "n/a"
    cp_total_disp = res["cp_total_file"] if res.get("cp_total_file") is not None else "n/a"
    aitken_disp = res["cp_extrapolated_aitken"] if res.get("cp_extrapolated_aitken") is not None else "n/a"
    extra_revs_disp = res["extra_revs_to_converge"] if res.get("extra_revs_to_converge") is not None else "n/a"

    is_custom_revs = bool(getattr(p, "custom_revs", None) and str(p.custom_revs).strip())

    if is_custom_revs:
        rows = [
            ("Mean Cp of reported revolutions", _sel_avg_formula("D"), "-", "Live Excel formula averaging selected revolution(s)", "0.0000"),
            ("Convergence stability flag", "PERIODIC STABLE" if is_stable_flag else "TRANSIENT/UNSTABLE", "-", f"Relative ΔCp = {last_dcp_rel * 100:.2f}% (< {tol_pct:g}% threshold)", None),
            ("Period-2 sub-harmonic oscillation", "DETECTED (Sub-harmonic instability)" if res.get("has_period_2") else "None (Period-1 synchronized)", "-", f"Spectral amplitude at 0.5x rotor freq: {res.get('period_2_ratio', 0.0) * 100:.1f}%", None),
            ("Cp (blades only)", cp_blades_disp, "-", "Isolated aerodynamic blade torque power coefficient", "0.0000" if isinstance(cp_blades_disp, (int, float)) else None),
            ("Cp (total file)", cp_total_disp, "-", "Total rotor moment file power coefficient", "0.0000" if isinstance(cp_total_disp, (int, float)) else None),
            ("Reported revolutions", ", ".join(str(r["k"]) for r in sel), "", "Custom selected revolution(s)", None),
            ("Convergence status", "STABLE" if res["is_stable"] else "NOT YET STABLE", "", f"Criteria: selected {len(sel)} revolution(s) satisfy stability", None),
            ("Last revolution Cp change (ΔCp)", res["cp_last_change"], "", "Relative change versus preceding revolution", "0.00%"),
            ("Std dev of Cp across reported revs", res["cp_std"] if not math.isnan(res["cp_std"]) else "n/a", "", "", "0.0000" if not math.isnan(res["cp_std"]) else None),
            ("Mean moment coefficient Cm of reported revolutions", _sel_avg_formula("F"), "-", "Cm = M / (½ρU²A·R) averaged over selected revolution(s)", "0.0000"),
            ("Mean rotor torque of reported revolutions", _sel_avg_formula("G"), "N·m", "Per metre depth as simulated (selected revolution(s))", "0.0000"),
            ("Mean rotor power of reported revolutions", res["power_mean_W"], "W", "Per metre depth as simulated (ω·M) across selected revolution(s)", "0.00"),
            ("Instantaneous Cp (max / min) of reported revolutions", f"{res['cp_max']:.3f} / {res['cp_min']:.3f}", "-", "Extrema across selected revolution(s)", None),
            ("Peak-to-peak ripple ΔCp (max - min) of reported revolutions", res["cp_max"] - res["cp_min"], "-", "Max(Cp) - Min(Cp) across selected revolution(s)", "0.0000"),
            ("Peak-to-peak ripple ΔCm (max - min) of reported revolutions", res["cm_max"] - res["cm_min"], "-", "Max(Cm) - Min(Cm) across selected revolution(s)", "0.0000"),
            ("Cp ripple (peak-to-peak / mean) of reported revolutions", res["ripple_pp"], "-", "(Cp_max - Cp_min) / Cp_mean across selected revolution(s)", "0.00"),
            ("Dominant ripple frequency of reported revolutions", res["dominant_harmonic"], "× rotor freq.", f"Expected harmonic = {p.n_blades} for {p.n_blades} blades", "0.00"),
            ("Starting torque ratio (STR = Cm_min / Cm_mean) of reported revolutions", res["str"] if not math.isnan(res["str"]) else "n/a", "-", "STR = Cm_min / Cm_mean (positive indicates self-starting capability)", "0.00"),
            ("Static self-starting capability of reported revolutions", "YES (Positive throughout cycle)" if res["is_self_starting"] else "NO (Dead-band / negative sectors)", "-", "Requires auxiliary starting mechanism if negative", None),
            ("Mean thrust coefficient Ct (Fx) of reported revolutions", _sel_avg_formula("J") if res["ct_mean"] is not None else "n/a", "-", "Fx / (½ρU²A) averaged over selected revolution(s)", "0.0000" if res["ct_mean"] is not None else None),
            ("Mean side-force coefficient Cy (Fy) of reported revolutions", _sel_avg_formula("K") if res["cy_mean"] is not None else "n/a", "-", "Fy / (½ρU²A) averaged over selected revolution(s)", "0.0000" if res["cy_mean"] is not None else None),
            ("Max geometric angle of attack", res["alpha_max"], "deg", f"at θ = {res['alpha_max_theta']:.0f}° (analytical kinematic, no induction)", "0.00"),
        ]
    else:
        rows = [
            ("Mean Cp of reported revolutions", _sel_avg_formula("D"), "-", "Live Excel formula averaging reported revolutions", "0.0000"),
            ("Mean Cp of trailing stable revolutions", res.get("cp_trailing_stable_mean", res["cp_mean"]), "-", f"Average over consecutive stable revs ({', '.join(str(k) for k in res.get('stable_revs', []))})", "0.0000"),
            ("Mean Cp of all complete revolutions", f"=AVERAGE(Revolutions!D5:D{len(revs)+4})", "-", f"Whole-analysis average across all {res['n_full']} complete revolutions", "0.0000"),
            ("Estimated converged Cp (Aitken extrapolation)", aitken_disp, "-", "Asymptotic limit from geometric progression of rev differences", "0.0000" if isinstance(aitken_disp, (int, float)) else None),
            ("Extra revolutions needed to converge", extra_revs_disp, "revs", "Estimated revolutions to reach stability tolerance", "0" if isinstance(extra_revs_disp, (int, float)) else None),
            ("Convergence stability flag", "PERIODIC STABLE" if is_stable_flag else "TRANSIENT/UNSTABLE", "-", f"Last 2 revs relative ΔCp = {last_dcp_rel * 100:.2f}% (< {tol_pct:g}% threshold)", None),
            ("Period-2 sub-harmonic oscillation", "DETECTED (Sub-harmonic instability)" if res.get("has_period_2") else "None (Period-1 synchronized)", "-", f"Spectral amplitude at 0.5x rotor freq: {res.get('period_2_ratio', 0.0) * 100:.1f}%", None),
            ("Cp (blades only)", cp_blades_disp, "-", "Isolated aerodynamic blade torque power coefficient", "0.0000" if isinstance(cp_blades_disp, (int, float)) else None),
            ("Cp (total file)", cp_total_disp, "-", "Total rotor moment file power coefficient", "0.0000" if isinstance(cp_total_disp, (int, float)) else None),
            ("Reported revolutions", ", ".join(str(r["k"]) for r in sel), "", "Counted back from simulation end", None),
            ("Convergence status", "STABLE" if res["is_stable"] else "NOT YET STABLE", "", f"Criteria: last {len(sel)} revolutions satisfy multi-criteria stability", None),
            ("Trailing stable revolutions", res["stable_n"], "", "Count of consecutive stable trailing revs", "0"),
            ("Last revolution Cp change (ΔCp)", res["cp_last_change"], "", "Relative change versus preceding revolution", "0.00%"),
            ("Std dev of Cp across reported revs", res["cp_std"], "", "", "0.0000"),

            # Moment Coefficient (Cm) multi-scope
            ("Mean moment coefficient Cm of reported revolutions", _sel_avg_formula("F"), "-", "Cm = M / (½ρU²A·R) averaged over reported revolutions", "0.0000"),
            ("Mean moment coefficient Cm of trailing stable revolutions", res.get("cm_stable_mean", res["cm_mean"]), "-", f"Average over consecutive stable revs ({', '.join(str(k) for k in res.get('stable_revs', []))})", "0.0000"),
            ("Mean moment coefficient Cm of all complete revolutions", f"=AVERAGE(Revolutions!F5:F{len(revs)+4})", "-", f"Whole-analysis average across all {res['n_full']} complete revolutions", "0.0000"),

            # Rotor Torque multi-scope
            ("Mean rotor torque of reported revolutions", _sel_avg_formula("G"), "N·m", "Per metre depth as simulated (reported revolutions)", "0.0000"),
            ("Mean rotor torque of trailing stable revolutions", res.get("torque_stable_mean", res["torque_mean"]), "N·m", "Per metre depth as simulated (trailing stable revolutions)", "0.0000"),
            ("Mean rotor torque of all complete revolutions", f"=AVERAGE(Revolutions!G5:G{len(revs)+4})", "N·m", f"Per metre depth across all {res['n_full']} complete revolutions", "0.0000"),

            # Rotor Power multi-scope
            ("Mean rotor power of reported revolutions", res["power_mean_W"], "W", "Per metre depth as simulated (ω·M) across reported revolutions", "0.00"),
            ("Mean rotor power of trailing stable revolutions", res.get("power_stable_W", res["power_mean_W"]), "W", "Per metre depth across trailing stable revolutions", "0.00"),
            ("Mean rotor power of all complete revolutions", res.get("power_all_W", res["power_mean_W"]), "W", f"Per metre depth across all {res['n_full']} complete revolutions", "0.00"),

            # Cycle Fluctuations, Ripple & Dynamics (Reported Revolutions)
            ("Instantaneous Cp (max / min) of reported revolutions", f"{res['cp_max']:.3f} / {res['cp_min']:.3f}", "-", "Extrema across reported revolutions", None),
            ("Peak-to-peak ripple ΔCp (max - min) of reported revolutions", res["cp_max"] - res["cp_min"], "-", "Max(Cp) - Min(Cp) across reported revolutions", "0.0000"),
            ("Peak-to-peak ripple ΔCm (max - min) of reported revolutions", res["cm_max"] - res["cm_min"], "-", "Max(Cm) - Min(Cm) across reported revolutions", "0.0000"),
            ("Cp ripple (peak-to-peak / mean) of reported revolutions", res["ripple_pp"], "-", "(Cp_max - Cp_min) / Cp_mean across reported revolutions", "0.00"),
            ("Dominant ripple frequency of reported revolutions", res["dominant_harmonic"], "× rotor freq.", f"Expected harmonic = {p.n_blades} for {p.n_blades} blades", "0.00"),
            ("Starting torque ratio (STR = Cm_min / Cm_mean) of reported revolutions", res["str"] if not math.isnan(res["str"]) else "n/a", "-", "STR = Cm_min / Cm_mean (positive indicates self-starting capability)", "0.00"),
            ("Static self-starting capability of reported revolutions", "YES (Positive throughout cycle)" if res["is_self_starting"] else "NO (Dead-band / negative sectors)", "-", "Requires auxiliary starting mechanism if negative", None),

            # Global Rotor Forces (Thrust Ct & Side-force Cy) multi-scope
            ("Mean thrust coefficient Ct (Fx) of reported revolutions", _sel_avg_formula("J") if res["ct_mean"] is not None else "n/a", "-", "Fx / (½ρU²A) averaged over reported revolutions", "0.0000" if res["ct_mean"] is not None else None),
            ("Mean thrust coefficient Ct (Fx) of trailing stable revolutions", res.get("ct_stable_mean", res["ct_mean"]) if res["ct_mean"] is not None else "n/a", "-", "Average over consecutive stable revs", "0.0000" if res["ct_mean"] is not None else None),
            ("Mean thrust coefficient Ct (Fx) of all complete revolutions", f"=AVERAGE(Revolutions!J5:J{len(revs)+4})" if res["ct_mean"] is not None else "n/a", "-", f"Whole-analysis average across all {res['n_full']} complete revolutions", "0.0000" if res["ct_mean"] is not None else None),

            ("Mean side-force coefficient Cy (Fy) of reported revolutions", _sel_avg_formula("K") if res["cy_mean"] is not None else "n/a", "-", "Fy / (½ρU²A) averaged over reported revolutions", "0.0000" if res["cy_mean"] is not None else None),
            ("Mean side-force coefficient Cy (Fy) of trailing stable revolutions", res.get("cy_stable_mean", res["cy_mean"]) if res["cy_mean"] is not None else "n/a", "-", "Average over consecutive stable revs", "0.0000" if res["cy_mean"] is not None else None),
            ("Mean side-force coefficient Cy (Fy) of all complete revolutions", f"=AVERAGE(Revolutions!K5:K{len(revs)+4})" if res["cy_mean"] is not None else "n/a", "-", f"Whole-analysis average across all {res['n_full']} complete revolutions", "0.0000" if res["cy_mean"] is not None else None),

            ("Max geometric angle of attack", res["alpha_max"], "deg", f"at θ = {res['alpha_max_theta']:.0f}° (analytical kinematic, no induction)", "0.00"),
        ]

    if res.get("ct_shaft_mean") is not None:
        rows.append(("Mean shaft drag coefficient Ct_shaft (Fx_shaft)", res["ct_shaft_mean"], "-", "Isolated shaft thrust drag coefficient", "0.0000"))
        if res.get("ct_blades_only_mean") is not None:
            rows.append(("Mean thrust coefficient Ct (blades only)", res["ct_blades_only_mean"], "-", "Ct_total - Ct_shaft (isolated blade thrust)", "0.0000"))
    if res.get("cy_shaft_mean") is not None:
        rows.append(("Mean shaft side-force coefficient Cy_shaft (Fy_shaft)", res["cy_shaft_mean"], "-", "Isolated shaft lateral force coefficient", "0.0000"))
        if res.get("cy_blades_only_mean") is not None:
            rows.append(("Mean side-force coefficient Cy (blades only)", res["cy_blades_only_mean"], "-", "Cy_total - Cy_shaft (isolated blade side force)", "0.0000"))

    if d["have_blades"]:
        import numpy as _np
        t1_arr = _np.asarray(az["M_b_mean"][0] if len(az.get("M_b_mean", [])) > 0 else az["M_avg"], dtype=float)
        if len(t1_arr) > 0 and _np.any(_np.isfinite(t1_arr)):
            idx_t1_max = int(_np.nanargmax(t1_arr))
            t_blade_max = float(t1_arr[idx_t1_max])
            th_blade_max = float(az["theta"][idx_t1_max])
        else:
            t_blade_max = float("nan")
            th_blade_max = 0.0
        rows += [
            ("Upwind blade-torque peak", res["torque_peak_up"], "N·m", f"at θ = {res['torque_peak_up_theta']:.1f}°", "0.0000"),
            ("Downwind blade-torque maximum", res["torque_max_down"], "N·m", f"at θ = {res['torque_max_down_theta']:.1f}°", "0.0000"),
            ("Minimum blade torque", res["torque_min"], "N·m", f"at θ = {res['torque_min_theta']:.1f}°", "0.0000"),
            ("Peak blade loading (T_blade,max)", t_blade_max, "N·m", f"Maximum single-blade torque at θ = {th_blade_max:.1f}°", "0.0000"),
            ("Upwind share of torque", res["upwind_share"], "", "Fraction of net torque generated in θ ∈ [0, 180°)", "0.0%"),
            ("Blade-to-blade torque spread", res["blade_spread"], "", "(max - min) / mean of blade torques", "0.00%"),
            ("Blade azimuth offsets", ", ".join(f"{o:.1f}°" for o in res["offsets"]), "", "Detected from cross-correlation" if p.offsets == "auto" else "Even geometric spacing", None),
        ]

    # Azimuthal quartile power shares (Upwind [45-135), Leeward [135-225), Downwind [225-315), Windward [315-45))
    import numpy as _np
    th_q = _np.asarray(az["theta"], dtype=float) % 360.0
    cp_q = _np.asarray(az["Cp_blade_eq"] if (d["have_blades"] and "Cp_blade_eq" in az) else az["cp_mean"], dtype=float)
    s_up = float(_np.nansum(cp_q[(th_q >= 45.0) & (th_q < 135.0)]))
    s_lee = float(_np.nansum(cp_q[(th_q >= 135.0) & (th_q < 225.0)]))
    s_down = float(_np.nansum(cp_q[(th_q >= 225.0) & (th_q < 315.0)]))
    s_wind = float(_np.nansum(cp_q[(th_q >= 315.0) | (th_q < 45.0)]))
    tot_q = s_up + s_lee + s_down + s_wind
    if abs(tot_q) > 1e-9:
        denom_q = abs(tot_q)
        q_up, q_lee, q_down, q_wind = s_up / denom_q, s_lee / denom_q, s_down / denom_q, s_wind / denom_q
    else:
        q_up = q_lee = q_down = q_wind = 0.0
    rows += [
        ("Power share: Upwind sector [45°–135°]", q_up, "%", "Rotor front quadrant power extraction", "0.0%"),
        ("Power share: Leeward sector [135°–225°]", q_lee, "%", "Leeward dynamic wake transition quadrant", "0.0%"),
        ("Power share: Downwind sector [225°–315°]", q_down, "%", "Downwind passage power extraction", "0.0%"),
        ("Power share: Windward sector [315°–45°]", q_wind, "%", "Upstream advancing quadrant power extraction", "0.0%"),
    ]

    if "shaft_mean_moment" in res and res["shaft_mean_moment"] is not None:
        rows.append(("Mean shaft moment", res["shaft_mean_moment"], "N·m", "Monitored shaft drag moment", "0.0000"))

    if res.get("span_H") is not None:
        rows += [
            ("Blade span H (3D scaling)", res["span_H"], "m", "User-specified 3D blade span height", "0.00"),
            ("3D swept area (2R·H)", res["swept_area_3d"], "m²", "Frontal swept area for 3D turbine", "0.00"),
            ("Estimated 3D power (Cp·Pwind)", res["power_3d_W"], "W", "Notice: 2D CFD geometric scaling; excludes tip/strut losses", "0.00"),
            ("Estimated 3D torque (P / ω)", res["torque_3d_Nm"], "N·m", "Notice: excludes tip vortex and arm losses", "0.00"),
        ]

    for i, (metric, val, unit, note, num_fmt) in enumerate(rows):
        r_idx = 5 + i
        _put(ws, r_idx, 1, metric, F_B)
        _put(ws, r_idx, 2, val, F_N, num_fmt)
        _put(ws, r_idx, 3, unit)
        _put(ws, r_idx, 4, note)

    r_run = 5 + len(rows) + 1
    _put(ws, r_run, 1, "Simulation & Operating Point Info", F_B, fill=SF)
    ws.merge_cells(start_row=r_run, start_column=1, end_row=r_run, end_column=4)
    info = [
        ("Operating point", f"U = {p.U:g} m/s, λ = {res['tsr']:.3f}, ω = {res['omega']:.4f} rad/s ({res['omega'] * 30.0 / 3.141592653589793:.1f} rpm)"),
        ("Flow time analyzed", f"{res['t0']:.5f} to {res['t1']:.5f} s  ({res['total_revs']:.2f} total revs, {res['n_full']} complete)"),
        ("Common aligned time steps", f"{res['n_steps']}"),
        ("Moment sign applied", f"{res['sign']:+.0f}"),
    ]
    for idx, ph in enumerate(res["phases"]):
        n_st = ph.get("n_steps", ph["step_to"] - ph["step_from"])
        rev_cnt = ph.get("revolutions", (ph.get("duration_s", 0)) / res["Trev"] if res.get("Trev") else 0)
        p_name = f"Simulation Stage {ph.get('phase_index', idx + 1)}" if len(res["phases"]) > 1 else "Time-step phase"
        info.append((
            f"{p_name} (steps {ph['step_from']}–{ph['step_to']})",
            f"Δt = {ph['dt']:.6e} s = {ph['deg_per_step']:.4f}°/step ({ph['steps_per_rev']:.1f} steps/rev) | {n_st:,} steps | {rev_cnt:.2f} revs"
        ))

    for i, (lbl, txt) in enumerate(info):
        _put(ws, r_run + 1 + i, 1, lbl, F_B)
        _put(ws, r_run + 1 + i, 2, txt)

    r_warn = r_run + len(info) + 2
    diag_rep = R.get("diagnostics")
    if diag_rep and (getattr(diag_rep, "has_errors", False) or getattr(diag_rep, "has_warnings", False)):
        n_err = diag_rep.summary_counts.get("ERROR", 0)
        n_warn = diag_rep.summary_counts.get("WARNING", 0)
        _put(ws, r_warn, 1, f"File Health Notice: {n_err} Error(s), {n_warn} Warning(s) detected during diagnostics. Review 'File_Health' sheet.", F_B, fill=FAIL_F if n_err > 0 else WF)
        ws.merge_cells(start_row=r_warn, start_column=1, end_row=r_warn, end_column=4)
        ws.row_dimensions[r_warn].height = 28
        r_warn += 2

    if getattr(p, "applied_repairs", None):
        _put(ws, r_warn, 1, f"Data Repair Notice: {', '.join(p.applied_repairs)} applied.", F_B, fill=SF)
        ws.merge_cells(start_row=r_warn, start_column=1, end_row=r_warn, end_column=4)
        ws.row_dimensions[r_warn].height = 28
        r_warn += 2

    if R["warnings"]:
        _put(ws, r_warn, 1, "Convergence & Setup Warnings", F_B, fill=WF)
        ws.merge_cells(start_row=r_warn, start_column=1, end_row=r_warn, end_column=4)
        for i, w in enumerate(R["warnings"]):
            _put(ws, r_warn + 1 + i, 1, w, F_N, fill=WF, wrap=True)
            ws.merge_cells(start_row=r_warn + 1 + i, start_column=1, end_row=r_warn + 1 + i, end_column=4)
            ws.row_dimensions[r_warn + 1 + i].height = 28

    for col, width in zip("ABCD", [40, 36, 14, 75]):
        ws.column_dimensions[col].width = width

    # Embed dashboard figure if available
    if "12_dashboard.png" in figs and os.path.isfile(figs["12_dashboard.png"]):
        try:
            img = XLImage(figs["12_dashboard.png"])
            img.width, img.height = 760, 430
            ws.add_image(img, "F4")
        except Exception:
            pass

    # ---------------- 2. Parameters Sheet ----------------
    wp = wb.create_sheet("Parameters")
    _hdr(wp, 1, ["Parameter", "Value", "Unit", "Source / Derivation"])
    prm = [
        ("Free-stream speed U", p.U, "m/s", p.get_source("U")),
        ("Tip speed ratio λ", res["tsr"], "-", p.get_source("tsr") if not p.omega else "Derived from ω"),
        ("Rotor speed ω", res["omega"], "rad/s", p.get_source("omega") if p.omega else "Derived: λ·U/R"),
        ("Rotor radius R", p.R, "m", p.get_source("R")),
        ("Blade chord c", p.chord if p.chord is not None else "n/a (not specified)", "m", p.get_source("chord")),
        ("Blade span H (3D)", p.span if p.span is not None else "n/a (not specified)", "m", p.get_source("span")),
        ("Number of blades N", p.n_blades, "-", p.get_source("n_blades")),
        ("2D depth reference", p.depth, "m", p.get_source("depth")),
        ("Air density ρ", p.rho, "kg/m³", p.get_source("rho")),
        ("Dynamic viscosity μ", p.mu, "Pa·s", p.get_source("mu")),
        ("Blade-1 azimuth at t = 0 (θ0)", p.theta0, "deg", p.get_source("theta0")),
        ("Azimuth zero position", getattr(p, "azimuth_zero", "top"), "-", p.get_source("azimuth_zero")),
        ("Azimuth free offset", getattr(p, "azimuth_offset", 0.0), "deg", p.get_source("azimuth_offset")),
        ("Rotation direction", getattr(p, "rotation_dir", "ccw"), "-", p.get_source("rotation_dir")),
        ("Blade phasing mode", p.offsets, "-", "auto = detected from cross-correlation"),
        ("Moment sign setting", p.moment_sign, "-", f"Applied: {res['sign']:+.0f}"),
        ("Stability tolerance (relative)", p.stable_tol, "%", p.get_source("stable_tol")),
        ("Stability tolerance (absolute)", getattr(p, "stable_tol_abs", 0.001), "-", "Default 0.001"),
        ("Reported revolutions count", p.n_stable, "-", p.get_source("n_stable")),
        ("Specific revolutions filter", str(p.custom_revs).strip() if is_custom_revs else "None (Default trailing stable)", "-", "Revolution selection"),
        ("Azimuth bin width", p.bin_deg, "deg", p.get_source("bin_deg")),
        ("Active Preset Profile", p.preset_name or "None (Custom manual input)", "-", "Preset Provenance"),
        ("Data repairs applied", ", ".join(p.applied_repairs) if getattr(p, "applied_repairs", None) else "None (Raw solver monitor data)", "-", "Data Integrity & Modifications"),
        ("Frontal swept area A = 2·R·depth", res["A"], "m²", "Derived: 2·R·depth"),
        ("Reference power ½ρU³A", res["Pref"], "W", "Derived: ½ρU³A"),
        ("Reference force ½ρU²A", res["Fref"], "N", "Derived: ½ρU²A"),
        ("Reference moment ½ρU²A·R", res["Mref"], "N·m", "Derived: Fref·R"),
        ("Rotor period Trev", res["Trev"], "s", "Derived: 2π/ω"),
    ]
    for i, (param_name, param_val, param_unit, param_src) in enumerate(prm):
        row_p = 2 + i
        _put(wp, row_p, 1, param_name)
        _put(wp, row_p, 2, param_val, F_IN if "typed" in param_src else F_N, "General")
        _put(wp, row_p, 3, param_unit)
        _put(wp, row_p, 4, param_src)

    # CFD Case & Numerical Setup Metadata (Tier 2)
    r_meta = 2 + len(prm) + 1
    _put(wp, r_meta, 1, "CFD Case & Numerical Setup Metadata", F_B, fill=SF)
    wp.merge_cells(start_row=r_meta, start_column=1, end_row=r_meta, end_column=4)

    meta_items = [
        ("Mesh cell count", p.metadata.mesh_cells if p.metadata.mesh_cells is not None else "n/a", "cells", "Spatial grid resolution"),
        ("Turbulence model", p.metadata.turb_model or "n/a", "-", "RANS / LES closure model"),
        ("Nominal time step size", f"{p.metadata.time_step_s:.6e}" if p.metadata.time_step_s is not None else (f"{p.dt:.6e}" if p.dt else "from flow-time column"), "s", "Temporal discretisation"),
        ("Iterations per time step", p.metadata.iter_per_step if p.metadata.iter_per_step is not None else "n/a", "-", "Inner convergence iterations"),
        ("Residual target", p.metadata.residual_target or "n/a", "-", "Algebraic residual threshold"),
        ("Computational domain size", p.metadata.domain_size or "n/a", "-", "Farfield / boundary dimensions"),
        ("Grid interface type", p.metadata.interface_type or "n/a", "-", "Sliding / dynamic mesh"),
        ("Moment center = rotor axis confirmed", "Yes (Confirmed)" if p.metadata.moment_center_confirmed else "No", "-", "Reference point alignment"),
    ]
    for i, (m_lbl, m_val, m_unit, m_desc) in enumerate(meta_items):
        row_m = r_meta + 1 + i
        _put(wp, row_m, 1, m_lbl)
        _put(wp, row_m, 2, m_val, F_N, "General")
        _put(wp, row_m, 3, m_unit)
        _put(wp, row_m, 4, m_desc)

    r_files = r_meta + len(meta_items) + 2
    _put(wp, r_files, 1, "Input Fluent Monitor Files & Reproducibility Hashes", F_B, fill=SF)
    wp.merge_cells(start_row=r_files, start_column=1, end_row=r_files, end_column=4)
    _hdr(wp, r_files + 1, ["File Role", "Filename", "SHA-256 Checksum", "Full Path"])

    f_list = [
        ("Total moment", "total", f.total),
        ("Shaft moment", "shaft", f.shaft),
        ("Force X (thrust)", "fx", f.fx),
        ("Force Y (side force)", "fy", f.fy),
    ]
    if getattr(f, "fx_shaft", None):
        f_list.append(("Shaft Force X (thrust)", "fx_shaft", f.fx_shaft))
    if getattr(f, "fy_shaft", None):
        f_list.append(("Shaft Force Y (side)", "fy_shaft", f.fy_shaft))
    f_list += [(f"Blade {k + 1} moment", f"blade_{k + 1}", b_path) for k, b_path in enumerate(f.blades) if b_path]

    file_hashes = R.get("file_hashes", {})
    from vawt_post.engine.parser import compute_file_sha256

    for i, (fn_lbl, fn_key, fn_path) in enumerate(f_list):
        row_f = r_files + 2 + i
        fn_sha = file_hashes.get(fn_key) or (compute_file_sha256(fn_path) if fn_path else "")
        _put(wp, row_f, 1, fn_lbl)
        _put(wp, row_f, 2, os.path.basename(fn_path) if fn_path else "Not provided")
        _put(wp, row_f, 3, fn_sha if fn_sha else "n/a")
        _put(wp, row_f, 4, fn_path if fn_path else "")

    for col, width in zip("ABCD", [36, 26, 32, 60]):
        wp.column_dimensions[col].width = width

    # ---------------- 2b. Mesh_Info Sheet (Sizing, Quality & Setup Notes) ----------------
    mesh_info = R.get("mesh_info") or {}
    if (
        mesh_info.get("mesh", {}).get("available")
        or mesh_info.get("setup")
        or mesh_info.get("context", {}).get("is_workbench_system")
    ):
        _write_mesh_info_sheet(wb, R, mesh_info)

    # ---------------- 3. Revolutions Sheet ----------------
    wr = wb.create_sheet("Revolutions")
    _put(wr, 1, 1, "Per-Revolution Statistics (complete revolutions counted back from end of simulation)", F_B)
    _put(wr, 2, 1, "Highlighted rows correspond to the reported revolutions.")

    heads = [
        "Rev", "Window start (s)", "Window end (s)", "Mean Cp", "ΔCp vs previous",
        "Mean Cm", "Mean torque (N·m)", "Max Cp", "Min Cp", "Ct", "Cy"
    ]
    if d["have_blades"]:
        heads += [f"Mean M blade {k + 1} (N·m)" for k in range(p.n_blades)] + ["Blade spread"]
    heads += ["Stable?", "Reported?"]

    _hdr(wr, 4, heads, height=45)
    for i, r in enumerate(revs):
        row_num = 5 + i
        vals = [
            r["k"], r["a"], r["b"], r["cp"], r["dcp"], r["cm"], r["m"],
            r["cp_max"], r["cp_min"], r["ct"], r["cy"]
        ]
        fmts = [
            "0", "0.00000", "0.00000", "0.0000", "0.00%", "0.0000", "0.0000",
            "0.000", "0.000", "0.0000", "0.0000"
        ]
        if d["have_blades"]:
            vals += list(r["blade_means"]) + [r["spread"]]
            fmts += ["0.0000"] * p.n_blades + ["0.00%"]
        vals += ["yes" if r["stable"] else "no", "REPORTED" if r["selected"] else ""]
        fmts += [None, None]

        fill_row = YF if r["selected"] else None
        font_row = F_B if r["selected"] else F_N
        for j, (v_cell, f_cell) in enumerate(zip(vals, fmts)):
            _put(wr, row_num, 1 + j, v_cell, font_row, f_cell, fill=fill_row)

    for j in range(len(heads)):
        wr.column_dimensions[COL(j + 1)].width = 13
    wr.freeze_panes = "B5"

    # Add native openpyxl BarChart
    try:
        bc = BarChart()
        bc.type = "col"
        bc.title = "Revolution-Averaged Cp"
        bc.height, bc.width = 8, 16
        bc.add_data(Reference(wr, min_col=4, min_row=4, max_row=4 + len(revs)), titles_from_data=True)
        bc.set_categories(Reference(wr, min_col=1, min_row=5, max_row=4 + len(revs)))
        bc.legend = None
        bc.x_axis.delete = False
        bc.y_axis.delete = False
        wr.add_chart(bc, f"A{len(revs) + 8}")
    except Exception:
        pass

    # ---------------- 4. Azimuth_Cp Sheet ----------------
    wa = wb.create_sheet("Azimuth_Cp")
    _put(wa, 1, 1, f"Cp, Cm, Ct, and Cy vs Rotor Azimuth (bins of {p.bin_deg:g}°, reported revolutions)", F_B)
    h_az = ["θ (deg)"] + [f"Cp rev {x['k']}" for x in az["rev"]] + ["Cp mean", "Cm mean"]
    if "ct_mean" in az:
        h_az.append("Ct mean")
    if "cy_mean" in az:
        h_az.append("Cy mean")
    _hdr(wa, 3, h_az, height=32)

    for i, th_val in enumerate(az["theta"]):
        row_a = 4 + i
        vals_a = [float(th_val)] + [float(x["cp"][i]) for x in az["rev"]] + [float(az["cp_mean"][i]), float(az["cm_mean"][i])]
        if "ct_mean" in az:
            vals_a.append(float(az["ct_mean"][i]))
        if "cy_mean" in az:
            vals_a.append(float(az["cy_mean"][i]))
        for j, v in enumerate(vals_a):
            _put(wa, row_a, 1 + j, v, F_N, "0.0" if j == 0 else "0.0000")

    wa.freeze_panes = "B4"
    for j in range(len(h_az)):
        wa.column_dimensions[COL(j + 1)].width = 12

    # Add native ScatterChart
    try:
        nrow = len(az["theta"])
        sc: Any = ScatterChart()
        sc.title = "Cp vs Azimuth (Reported Revolutions)"
        sc.style = 13
        sc.height, sc.width = 9, 18
        sc.x_axis.title = "Azimuth θ (deg)"
        sc.y_axis.title = "Cp"
        sc.x_axis.scaling.min, sc.x_axis.scaling.max = 0, 360
        sc.x_axis.majorUnit = 60
        sc.x_axis.delete = False
        sc.y_axis.delete = False
        xs = Reference(wa, min_col=1, min_row=4, max_row=3 + nrow)
        for c in range(2, 2 + len(az["rev"]) + 1):
            s_chart = Series(Reference(wa, min_col=c, min_row=3, max_row=3 + nrow), xs, title_from_data=True)
            s_chart.marker.symbol = "none"
            s_chart.smooth = False
            sc.series.append(s_chart)
        wa.add_chart(sc, f"{COL(len(h_az) + 2)}3")
    except Exception:
        pass

    # ---------------- 5. Blade_Azimuth Sheet ----------------
    if d["have_blades"]:
        wbk = wb.create_sheet("Blade_Azimuth")
        _put(wbk, 1, 1, "Blade Torque and Coefficients vs Blade's Own Azimuth (Reported Revolutions)", F_B)
        h_bk = ["θ (deg)"] + [f"T blade {k + 1} (N·m)" for k in range(nb)] + [
            "T mean (N·m)", "Cp contribution per blade", "Cp blade-equivalent (N× contribution)", "Cm blade-equivalent"
        ]
        _hdr(wbk, 3, h_bk, height=48)
        for i, th_val in enumerate(az["theta"]):
            row_bk = 4 + i
            vals_bk = [float(th_val)] + [float(m[i]) for m in az["M_b_mean"]] + [
                float(az["M_avg"][i]),
                float(az["M_avg"][i] * res["omega"] / res["Pref"]),
                float(az["Cp_blade_eq"][i]),
                float(az["Cm_blade_eq"][i])
            ]
            for j, v in enumerate(vals_bk):
                _put(wbk, row_bk, 1 + j, v, F_N, "0.0" if j == 0 else "0.0000")
        wbk.freeze_panes = "B4"
        for j in range(len(h_bk)):
            wbk.column_dimensions[COL(j + 1)].width = 14

        try:
            sc2: Any = ScatterChart()
            sc2.title = "Blade Torque vs Own Azimuth"
            sc2.style = 13
            sc2.height, sc2.width = 9, 18
            sc2.x_axis.title = "θ (deg)"
            sc2.y_axis.title = "Blade Torque (N·m)"
            sc2.x_axis.scaling.min, sc2.x_axis.scaling.max = 0, 360
            sc2.x_axis.majorUnit = 60
            sc2.x_axis.delete = False
            sc2.y_axis.delete = False
            xs2 = Reference(wbk, min_col=1, min_row=4, max_row=3 + len(az["theta"]))
            for c in range(2, 3 + nb):
                s2 = Series(Reference(wbk, min_col=c, min_row=3, max_row=3 + len(az["theta"])), xs2, title_from_data=True)
                s2.marker.symbol = "none"
                s2.smooth = False
                sc2.series.append(s2)
            wbk.add_chart(sc2, f"{COL(len(h_bk) + 2)}3")
        except Exception:
            pass

    # ---------------- 6. Alpha_Re Sheet ----------------
    wk = wb.create_sheet("Alpha_Re")
    _put(wk, 1, 1, "Kinematics versus Blade Azimuth (Analytical geometric, ignores rotor induction)", F_B)
    _hdr(wk, 3, ["θ (deg)", "α geometric (deg)", "W / U∞", "Chord Reynolds number"])
    for i, th_val in enumerate(az["theta"]):
        for j, v in enumerate([float(th_val), float(az["alpha"][i]), float(az["w_over_u"][i]), float(az["re_c"][i])]):
            _put(wk, 4 + i, 1 + j, v, F_N, ["0.0", "0.00", "0.000", "#,##0"][j])
    for j in range(4):
        wk.column_dimensions[COL(j + 1)].width = 20
    wk.freeze_panes = "A4"

    # ---------------- 7. Processed & Raw_Data Sheets ----------------
    if p.write_timeseries:
        wpd = wb.create_sheet("Processed")
        h_pd = ["Step", "Flow time (s)", "Revolutions", "θ blade 1 (deg)"] + [f"θ blade {k + 1}" for k in range(1, nb)] + [
            "Rotor moment (N·m)"
        ] + [f"M blade {k + 1}" for k in range(nb)] + ["Cp", "Cm"] + [f"Cp blade {k + 1}" for k in range(nb)]
        if d["Ct"] is not None:
            h_pd.append("Ct")
        if d["Cy"] is not None:
            h_pd.append("Cy")
        _hdr(wpd, 1, h_pd, height=32)

        # Check if live-formula mode is suitable (runs under 20,000 rows, Tier 4)
        use_live_formula = (len(d["t"]) <= 20000)
        row_pref_param = None
        row_omega_param = None
        row_mref_param = None
        for r_search in range(2, len(prm) + 2):
            val_p = wp.cell(row=r_search, column=1).value
            if val_p == "Reference power ½ρU³A":
                row_pref_param = r_search
            elif val_p == "Rotor speed ω":
                row_omega_param = r_search
            elif val_p == "Reference moment ½ρU²A·R":
                row_mref_param = r_search

        col_m_letter = COL(4 + max(1, nb))
        t_origin = (revs[0]["a"] - (revs[0]["k"] - 1) * res["Trev"]) if revs else float(d["t"][0])

        for i in range(len(d["t"])):
            row_idx = 2 + i
            if use_live_formula and row_pref_param and row_omega_param and row_mref_param:
                cp_cell = f"=({col_m_letter}{row_idx}*Parameters!$B${row_omega_param})/Parameters!$B${row_pref_param}"
                cm_cell = f"={col_m_letter}{row_idx}/Parameters!$B${row_mref_param}"
            else:
                cp_cell = float(d["Cp"][i])
                cm_cell = float(d["Cm"][i])

            row_data = [
                int(d["step"][i]), float(d["t"][i]), float((d["t"][i] - t_origin) / res["Trev"]), float(d["TH"][0][i])
            ] + [float(d["TH"][k][i]) for k in range(1, nb)] + [float(d["Mrot"][i])] + [
                float(d["Mb"][k][i]) for k in range(nb)
            ] + [cp_cell, cm_cell] + [float(d["Cp_b"][k][i]) for k in range(nb)]
            if d["Ct"] is not None:
                row_data.append(float(d["Ct"][i]))
            if d["Cy"] is not None:
                row_data.append(float(d["Cy"][i]))
            wpd.append(row_data)

        wpd.freeze_panes = "C2"
        for j in range(len(h_pd)):
            wpd.column_dimensions[COL(j + 1)].width = 13

        wraw = wb.create_sheet("Raw_Data")
        keys = list(d["raw"].keys())
        _hdr(wraw, 1, ["Step", "Flow time (s)"] + [f"{k} (raw)" for k in keys], height=32)
        for i in range(len(d["t"])):
            wraw.append([int(d["step"][i]), float(d["t"][i])] + [float(d["raw"][k][i]) for k in keys])
        wraw.freeze_panes = "C2"
        for j in range(len(keys) + 2):
            wraw.column_dimensions[COL(j + 1)].width = 16

    # ---------------- 8. Graphs Sheet ----------------
    wg = wb.create_sheet("Graphs", 1)  # Insert as second sheet right after Summary
    _put(wg, 1, 1, "Generated Publication Figures (saved as PNG in _figures folder)", F_T)
    order = [k for k in sorted(figs)]
    row_g = 3
    placed = 0
    for fig_key in order:
        if not os.path.isfile(figs[fig_key]):
            continue
        try:
            img = XLImage(figs[fig_key])
            scale = min(1.0, 780.0 / img.width)
            img.width, img.height = int(img.width * scale), int(img.height * scale)
            col_g = 1 if placed % 2 == 0 else 14
            if placed % 2 == 0 and placed > 0:
                row_g += 27
            _put(wg, row_g - 1, col_g, fig_key.replace(".png", "").replace("_", " ").title(), F_B, fill=SF)
            wg.merge_cells(start_row=row_g - 1, start_column=col_g, end_row=row_g - 1, end_column=col_g + 11)
            wg.add_image(img, f"{COL(col_g)}{row_g}")
            placed += 1
        except Exception:
            pass

    # ---------------- 9. QA_Checks Sheet (Tier 2) ----------------
    wq = wb.create_sheet("QA_Checks")
    _put(wq, 1, 1, "Simulation Data-Quality Assurance & Numerical Verification (QA Table)", F_T)
    _put(wq, 2, 1, "Automated checks for time-step resolution, monotonicity, periodicity, and simulation fidelity.", F_N)
    _hdr(wq, 4, ["Check Name", "Status", "Observed Value", "Acceptance Criteria & Diagnostic Detail"])
    qa_items = R.get("qa_checks", [])
    for i, q in enumerate(qa_items):
        r_qa = 5 + i
        _put(wq, r_qa, 1, q["check"], F_B)
        st = q["status"]
        f_st = PASS_T if st == "PASS" else (WARN_T if st == "WARN" else FAIL_T)
        fill_st = PASS_F if st == "PASS" else (WARN_F if st == "WARN" else FAIL_F)
        _put(wq, r_qa, 2, st, f_st, fill=fill_st)
        _put(wq, r_qa, 3, q["value"])
        _put(wq, r_qa, 4, q["detail"], wrap=True)
        wq.row_dimensions[r_qa].height = 24
    for col, width in zip("ABCD", [30, 14, 28, 70]):
        wq.column_dimensions[col].width = width

    # ---------------- 10. Warnings Sheet (Tier 2) ----------------
    if R.get("warnings"):
        ww = wb.create_sheet("Warnings")
        _put(ww, 1, 1, "Convergence & Setup Warnings Log", F_T)
        _hdr(ww, 3, ["#", "Severity", "Warning Message", "Recommended Remediation"])
        for i, w in enumerate(R["warnings"]):
            r_w = 4 + i
            _put(ww, r_w, 1, i + 1, F_B)
            sev = "HIGH" if ("FAIL" in w or "Strong" in w or "mismatch" in w.lower()) else "MODERATE"
            f_sev = FAIL_T if sev == "HIGH" else WARN_T
            fill_sev = FAIL_F if sev == "HIGH" else WARN_F
            _put(ww, r_w, 2, sev, f_sev, fill=fill_sev)
            _put(ww, r_w, 3, w, wrap=True)
            w_lower = w.lower()
            if any(k in w_lower for k in ("rotor speed", "bpf", "frequency", "spectral", "harmonic")):
                remedy = "Verify entered rotor speed ω against Fluent moving-mesh angular velocity (or UDF rotation rate)."
            elif any(k in w_lower for k in ("direction", "negative", "ccw", "cw")):
                remedy = "Verify turbine rotation direction (CCW vs CW) and sign convention of the moment monitor."
            elif "drift" in w_lower:
                remedy = "Extend simulation for additional revolutions until cycle-to-cycle drift decays below tolerance."
            elif any(k in w_lower for k in ("not yet stable", "stable", "convergence")):
                remedy = "Simulation has not converged within tolerance; run additional revolutions or adjust stability tolerance."
            elif any(k in w_lower for k in ("step", "resolution", "dt", "coarse", "deg/step")):
                remedy = "Refine time-step size in Fluent so azimuthal advance Δθ ≤ 1.0°/step (or ≤ 0.5° for high accuracy)."
            elif any(k in w_lower for k in ("gap", "missing step", "continuity")):
                remedy = "Examine simulation run logs for crashed, skipped, or manually restarted time-step intervals."
            elif "duplicate" in w_lower:
                remedy = "Deduplicate step indices; verify Fluent auto-save frequency to avoid overlapping append runs."
            elif any(k in w_lower for k in ("shaft", "blade", "discrepancy")):
                remedy = "Verify blade zone assignments in Fluent and check if shaft moment should be subtracted from total."
            elif any(k in w_lower for k in ("betz", "unphysical", "magnitude")):
                remedy = "Verify rotor radius R, blade chord c, free-stream velocity U, domain depth, and air density ρ."
            elif any(k in w_lower for k in ("autocorr", "period")):
                remedy = "Verify rotor rotational speed ω and flow-time sampling fidelity in monitor files."
            else:
                remedy = "Review aerodynamic setup and solver settings, refine discretization, or run longer until periodic."
            _put(ww, r_w, 4, remedy, wrap=True)
            ww.row_dimensions[r_w].height = 36
        for col, width in zip("ABCD", [8, 14, 65, 55]):
            ww.column_dimensions[col].width = width

    # ---------------- File_Health Sheet (Diagnostics) ----------------
    wfh = wb.create_sheet("File_Health")
    _put(wfh, 1, 1, f"{APP_NAME}: File-Health Diagnostics & Integrity Verification", F_T)
    _put(wfh, 2, 1, "Automated checks for monitor file headers, columns, time continuity, and cross-file consistency.", F_N)
    h_fh = ["#", "Severity", "File", "Check Name", "Location", "Step", "Flow Time (s)", "Explanation", "Suggested Remediation", "Repair Available"]
    _hdr(wfh, 4, h_fh, height=28)

    diag_rep = R.get("diagnostics")
    findings = diag_rep.findings if diag_rep and hasattr(diag_rep, "findings") else []
    for i, f_item in enumerate(findings):
        r_f = 5 + i
        _put(wfh, r_f, 1, i + 1, F_B)
        sev = f_item.severity
        f_sev = FAIL_T if sev == "ERROR" else (WARN_T if sev == "WARNING" else INFO_T)
        fill_sev = FAIL_F if sev == "ERROR" else (WARN_F if sev == "WARNING" else INFO_F)
        _put(wfh, r_f, 2, sev, f_sev, fill=fill_sev)
        _put(wfh, r_f, 3, f_item.file, F_B)
        _put(wfh, r_f, 4, f_item.check)
        _put(wfh, r_f, 5, f_item.location)
        _put(wfh, r_f, 6, f_item.step if f_item.step is not None else "-")
        _put(wfh, r_f, 7, f"{f_item.flow_time:.5f}" if f_item.flow_time is not None else "-")
        _put(wfh, r_f, 8, f_item.explanation, wrap=True)
        _put(wfh, r_f, 9, f_item.suggested_fix, wrap=True)
        _put(wfh, r_f, 10, "YES" if f_item.repair_available else "No", F_B if f_item.repair_available else F_N)
        wfh.row_dimensions[r_f].height = 28

    if not findings:
        _put(wfh, 5, 1, 1, F_B)
        _put(wfh, 5, 2, "PASS", PASS_T, fill=PASS_F)
        _put(wfh, 5, 3, "All files", F_B)
        _put(wfh, 5, 4, "All integrity checks passed")
        _put(wfh, 5, 5, "Global")
        _put(wfh, 5, 6, "-")
        _put(wfh, 5, 7, "-")
        _put(wfh, 5, 8, "No structural, temporal, or numerical data defects detected across monitor files.")
        _put(wfh, 5, 9, "Data files are verified and suitable for CFD aerodynamic post-processing.")
        _put(wfh, 5, 10, "No")

    for col, width in zip("ABCDEFGHIJ", [6, 14, 16, 26, 18, 12, 14, 45, 45, 16]):
        wfh.column_dimensions[col].width = width

    # ---------------- 11. Captions Sheet (Tier 4) ----------------
    wcap = wb.create_sheet("Captions")
    _put(wcap, 1, 1, f"{APP_NAME}: Publication Figure Captions", F_T)
    _hdr(wcap, 3, ["Figure ID", "Figure File", "Title", "Generated Academic Caption with Computed KPIs"], height=28)
    from vawt_post.engine.plots import generate_captions
    captions_dict = generate_captions(R)
    for i, (fig_name, cap_txt) in enumerate(captions_dict.items()):
        row_c = 4 + i
        _put(wcap, row_c, 1, f"Fig {i + 1:02d}", F_B)
        _put(wcap, row_c, 2, fig_name, F_N)
        _put(wcap, row_c, 3, cap_txt.split(":")[0] if ":" in cap_txt else fig_name, F_B)
        _put(wcap, row_c, 4, cap_txt, F_N, wrap=True)
        wcap.row_dimensions[row_c].height = 42
    wcap.column_dimensions["A"].width = 12
    wcap.column_dimensions["B"].width = 28
    wcap.column_dimensions["C"].width = 24
    wcap.column_dimensions["D"].width = 85

    # ---------------- 12. Stages Sheet (Multi-Phase Discretization) ----------------
    phases_data = res.get("phases", [])
    if phases_data:
        w_stg = wb.create_sheet("Stages")
        _put(w_stg, 1, 1, f"{APP_NAME}: Multi-Phase Simulation Stages", F_T)
        _hdr(
            w_stg, 3,
            ["Stage #", "Steps From", "Steps To", "dt (s)", "Δθ (deg/step)", "Steps/Rev", "Revolutions", "Duration (s)", "Mean Cp", "Converged Cp"],
            height=28
        )
        import numpy as np
        for i, ph in enumerate(phases_data):
            row_s = 4 + i
            ph_revs = [r["cp"] for r in revs if ph.get("t_from", 0.0) - 1e-4 <= r["a"] and r["b"] <= ph.get("t_to", 0.0) + 1e-4]
            m_cp = float(np.mean(ph_revs)) if ph_revs else (ph.get("mean_cp") if ph.get("mean_cp") is not None else "-")
            c_cp = ph.get("converged_cp") if ph.get("converged_cp") is not None else (m_cp if isinstance(m_cp, float) else "-")
            _put(w_stg, row_s, 1, ph.get("phase_index", i + 1), F_B)
            _put(w_stg, row_s, 2, ph.get("step_from", "-"))
            _put(w_stg, row_s, 3, ph.get("step_to", "-"))
            _put(w_stg, row_s, 4, ph.get("dt", 0.0), fmt="0.000000E+00")
            _put(w_stg, row_s, 5, ph.get("deg_per_step", 0.0), fmt="0.000")
            _put(w_stg, row_s, 6, ph.get("steps_per_rev", 0.0), fmt="0.0")
            _put(w_stg, row_s, 7, ph.get("revolutions", 0.0), fmt="0.00")
            _put(w_stg, row_s, 8, ph.get("duration_s", 0.0), fmt="0.0000")
            _put(w_stg, row_s, 9, m_cp, fmt="0.0000" if isinstance(m_cp, float) else None)
            _put(w_stg, row_s, 10, c_cp, fmt="0.0000" if isinstance(c_cp, float) else None)
        for col, width in zip("ABCDEFGHIJ", [10, 14, 14, 18, 16, 14, 14, 16, 14, 16]):
            w_stg.column_dimensions[col].width = width

    try:
        wb.save(path)
    finally:
        wb.close()
    log(f"Successfully generated Excel workbook: {path}")


def _write_mesh_info_sheet(wb: Workbook, R: Dict[str, Any], mesh_info: Dict[str, Any]) -> None:
    """Create the dedicated `Mesh_Info` worksheet with mesh sizing, BL, quality, and setup notes."""
    wm = wb.create_sheet("Mesh_Info")
    _put(wm, 1, 1, f"{APP_NAME}: CFD Mesh Sizing, Boundary-Layer & Quality Statistics", F_T)

    ctx = mesh_info.get("context") or {}
    m = mesh_info.get("mesh") or {}
    sz = m.get("sizing") or {}
    dom = m.get("domain") or {}
    q = m.get("quality") or {}
    st = mesh_info.get("setup") or {}
    cs = mesh_info.get("case") or {}
    notes = mesh_info.get("notes") or []

    r = 3
    _put(wm, r, 1, "1. Workbench System & Auto-Detected Artifacts", F_B, fill=SF)
    wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    r += 1
    _hdr(wm, r, ["Property / Artifact", "Value / Filename", "Unit / Type", "Location / Details"])
    r += 1
    sys_rows = [
        ("Workbench System ID", ctx.get("system_id") or "Standalone Fluent Run", "-", ctx.get("system_dir") or ctx.get("fluent_dir") or "-"),
        ("Fluent Case (.cas.h5 / .cas)", os.path.basename(str(ctx["case_h5"])) if ctx.get("case_h5") else (os.path.basename(str(ctx["legacy_case"])) if ctx.get("legacy_case") else "Not present"), "HDF5 / Case", ctx.get("case_h5") or ctx.get("legacy_case") or "-"),
        ("Fluent Data (.dat.h5)", os.path.basename(str(ctx["dat_h5"])) if ctx.get("dat_h5") else "Not present", "HDF5 Data", ctx.get("dat_h5") or "-"),
        ("Native Mesh File (.msh)", os.path.basename(str(ctx["msh_file"])) if ctx.get("msh_file") else "Not present", "Fluent Mesh", ctx.get("msh_file") or "-"),
        ("ANSYS Meshing DB (.mshdb)", os.path.basename(str(ctx["mshdb_file"])) if ctx.get("mshdb_file") else "Not present", "Workbench MECH", ctx.get("mshdb_file") or "-"),
        ("CAD Geometry (.agdb / .scdoc)", os.path.basename(str(ctx["agdb_file"])) if ctx.get("agdb_file") else "Not present", "DesignModeler / SC", ctx.get("agdb_file") or "-"),
        ("Setup Report (report.xml)", os.path.basename(str(ctx["report_xml"])) if ctx.get("report_xml") else "Not present", "XML Setup", ctx.get("report_xml") or "-"),
    ]
    for lbl, val, unit, det in sys_rows:
        _put(wm, r, 1, lbl, F_B)
        _put(wm, r, 2, val)
        _put(wm, r, 3, unit)
        _put(wm, r, 4, det)
        r += 1

    # Section 2: Global Mesh Sizing & Domain Topology
    r += 1
    _put(wm, r, 1, "2. Global Mesh Sizing & Domain Topology", F_B, fill=SF)
    wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    r += 1
    _hdr(wm, r, ["Sizing Metric", "Value", "Unit", "Notes / Provenance"])
    r += 1

    ct_summary = ", ".join(
        f"{x['type']}: {x['count']:,} ({x['pct']:.1f}%)"
        for x in (m.get("cell_type_breakdown") or [])
    ) or "n/a"

    size_rows: List[Tuple[str, Any, str, str, Optional[str]]] = [
        ("Space Dimension", f"{m.get('dimension', 2)}D" if m.get("dimension") else "n/a", "-", "From HDF5 / mesh topology", None),
        ("Total Cell Count", m.get("cell_count", "n/a"), "cells", ct_summary, "#,##0" if isinstance(m.get("cell_count"), int) else None),
        ("Total Face Count", m.get("face_count", "n/a"), "faces", "Interior + boundary + interface faces", "#,##0" if isinstance(m.get("face_count"), int) else None),
        ("Total Node Count", m.get("node_count", "n/a"), "nodes", "Mesh vertices", "#,##0" if isinstance(m.get("node_count"), int) else None),
        ("Domain Streamwise Extent [Xmin, Xmax]", f"[{dom['x_min_m']:.3f}, {dom['x_max_m']:.3f}]" if dom.get("x_min_m") is not None else "n/a", "m", f"Total Length L = {dom.get('length_m', 0.0):.3f} m" if dom.get("length_m") else "-", None),
        ("Domain Cross-Stream Extent [Ymin, Ymax]", f"[{dom['y_min_m']:.3f}, {dom['y_max_m']:.3f}]" if dom.get("y_min_m") is not None else "n/a", "m", f"Total Width W = {dom.get('width_m', 0.0):.3f} m" if dom.get("width_m") else "-", None),
        ("Domain Normalized Sizing (Up / Down / Width)", f"{dom['upstream_D']:.1f}D / {dom['downstream_D']:.1f}D / {dom['width_D']:.1f}D" if dom.get("upstream_D") is not None else "n/a", "D", "Normalized by rotor diameter D = 2R", None),
        ("Wind-Tunnel Blockage Ratio (D / W)", dom.get("blockage_ratio_pct", "n/a"), "%", "Recommended < 5-10% for unconfined wake" if dom.get("blockage_ratio_pct") is not None else "-", "0.00" if isinstance(dom.get("blockage_ratio_pct"), (int, float)) else None),
        ("Mesh-Measured Rotor Radius R / Chord c", f"R={dom.get('measured_R_m', 'n/a')} m | c={dom.get('measured_chord_m', 'n/a')} m" if dom.get("measured_R_m") else "n/a", "m", f"Sliding interface radius ≈ {dom['interface_radius_m']:.4f} m" if dom.get("interface_radius_m") else "Extracted from blade wall nodes", None),
        ("Total Meshed Area (2D)", sz.get("area_total_m2", "n/a"), "m²", "Sum of all cell areas", "0.0000" if isinstance(sz.get("area_total_m2"), (int, float)) else None),
        ("Cell Area (Min / Median / Max)", f"{sz['cell_area_min_m2']:.3e} / {sz['cell_area_median_m2']:.3e} / {sz['cell_area_max_m2']:.3e}" if sz.get("cell_area_min_m2") is not None else "n/a", "m²", "Mean cell area = " + (f"{sz['cell_area_mean_m2']:.3e} m²" if sz.get("cell_area_mean_m2") is not None else "-"), None),
        ("Equivalent Cell Size h = √A (Min / Med / Max)", f"{sz['h_eq_min_mm']:.4f} / {sz['h_eq_median_mm']:.3f} / {sz['h_eq_max_mm']:.2f}" if sz.get("h_eq_min_mm") is not None else "n/a", "mm", f"Mean h = {sz.get('h_eq_mean_mm', 0.0):.3f} mm" if sz.get("h_eq_mean_mm") is not None else "-", None),
        ("Edge Length (Min / Median / Max)", f"{sz['edge_min_mm']:.4f} / {sz['edge_median_mm']:.3f} / {sz['edge_max_mm']:.2f}" if sz.get("edge_min_mm") is not None else "n/a", "mm", "Across all mesh edges", None),
    ]
    for lbl, val, unit, det, fmt in size_rows:
        _put(wm, r, 1, lbl, F_B)
        _put(wm, r, 2, val, fmt=fmt)
        _put(wm, r, 3, unit)
        _put(wm, r, 4, det)
        r += 1

    # Section 3: Mesh Quality Statistics
    if q:
        r += 1
        _put(wm, r, 1, "3. Mesh Quality Statistics", F_B, fill=SF)
        wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
        r += 1
        _hdr(wm, r, ["Quality Metric", "Min", "P5 / Median", "Mean", "P95", "Max", "Quality Distribution / Guideline"])
        r += 1
        q_rows = [
            (
                "Equiangle Skewness (0 = ideal, 1 = degenerate)",
                q.get("skewness_min"),
                q.get("skewness_median"),
                q.get("skewness_mean"),
                q.get("skewness_p95"),
                q.get("skewness_max"),
                f"Excellent (<0.25): {(q.get('skew_excellent_lt_025_pct') or 0.0):.1f}% | Good (<0.5): {(q.get('skew_good_025_050_pct') or 0.0):.1f}% | Fair: {(q.get('skew_fair_050_080_pct') or 0.0):.1f}% | Poor (>0.8): {(q.get('skew_poor_gt_080_pct') or 0.0):.2f}%",
                "0.0000",
            ),
            (
                "Orthogonal Quality (1 = ideal, 0 = degenerate)",
                q.get("ortho_min"),
                q.get("ortho_p5"),
                q.get("ortho_mean"),
                q.get("ortho_median"),
                None,
                f"High (>0.70): {(q.get('ortho_high_gt_070_pct') or 0.0):.1f}% | Critical (<0.15): {(q.get('ortho_low_lt_015_pct') or 0.0):.2f}% (Col 3 = P5, Col 5 = Median)",
                "0.0000",
            ),
            (
                "Aspect Ratio (All Cells)",
                q.get("ar_min"),
                q.get("ar_median"),
                q.get("ar_mean"),
                q.get("ar_p95"),
                q.get("ar_max"),
                "Includes high-AR boundary-layer inflation cells near airfoils",
                "0.00",
            ),
            (
                "Aspect Ratio (Core / Triangle Cells)",
                None,
                None,
                q.get("core_ar_mean"),
                q.get("core_ar_p95"),
                q.get("core_ar_max"),
                "Uninflated core flow mesh (target < 5)",
                "0.00",
            ),
            (
                "Adjacent Cell Growth Rate (Area Ratio)",
                1.0,
                None,
                q.get("growth_rate_mean"),
                q.get("growth_rate_p95"),
                q.get("growth_rate_max"),
                f"Cells with jump > 2.0x: {(q.get('growth_rate_gt_2_pct') or 0.0):.2f}% (target smooth <= 1.2 mean)",
                "0.000",
            ),
        ]
        for lbl, v_min, v_p, v_mean, v_p95, v_max, dist_txt, fmt in q_rows:
            _put(wm, r, 1, lbl, F_B)
            _put(wm, r, 2, v_min if v_min is not None else "-", fmt=fmt if v_min is not None else None)
            _put(wm, r, 3, v_p if v_p is not None else "-", fmt=fmt if v_p is not None else None)
            _put(wm, r, 4, v_mean if v_mean is not None else "-", fmt=fmt if v_mean is not None else None)
            _put(wm, r, 5, v_p95 if v_p95 is not None else "-", fmt=fmt if v_p95 is not None else None)
            _put(wm, r, 6, v_max if v_max is not None else "-", fmt=fmt if v_max is not None else None)
            _put(wm, r, 7, dist_txt)
            r += 1

    # Section 4: Cell Zones Breakdown
    cell_zones = m.get("cell_zones") or []
    if cell_zones:
        r += 1
        _put(wm, r, 1, "4. Cell Zone Sizing & Quality Breakdown", F_B, fill=SF)
        wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1
        _hdr(wm, r, ["Cell Zone", "Zone ID", "Cell Count", "Share (%)", "Area (m²)", "Min h (mm)", "Median h (mm)", "Max h (mm)"])
        r += 1
        tot_c = max(int(m.get("cell_count") or 1), 1)
        for cz in cell_zones:
            _put(wm, r, 1, cz.get("name", ""), F_B)
            _put(wm, r, 2, cz.get("id", "-"))
            _put(wm, r, 3, cz.get("cell_count", 0), fmt="#,##0")
            _put(wm, r, 4, 100.0 * float(cz.get("cell_count", 0)) / tot_c, fmt="0.00")
            _put(wm, r, 5, cz.get("area_m2", 0.0), fmt="0.0000")
            _put(wm, r, 6, cz.get("h_min_mm", 0.0), fmt="0.0000")
            _put(wm, r, 7, cz.get("h_median_mm", 0.0), fmt="0.000")
            _put(wm, r, 8, cz.get("h_max_mm", 0.0), fmt="0.00")
            r += 1

    # Section 5: Boundary Layer & Near-Wall Sizing
    wall_layers = m.get("wall_layers") or []
    if wall_layers:
        r += 1
        _put(wm, r, 1, "5. Boundary-Layer & First-Layer Wall Sizing (Blades & Shaft)", F_B, fill=SF)
        wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
        r += 1
        _hdr(wm, r, ["Wall Zone", "Faces", "Perimeter (mm)", "Mean Δs (mm)", "y1 Min (mm)", "y1 Mean (mm)", "y1 Mean (µm)", "Est. y+ (Nom.)", "Est. y+ (Peak)"])
        r += 1
        for wl in wall_layers:
            y1_mm = wl.get("y1_mean_mm")
            yp_p = wl.get("yplus_peak_est")
            _put(wm, r, 1, wl.get("zone", ""), F_B)
            _put(wm, r, 2, wl.get("face_count", 0), fmt="#,##0")
            _put(wm, r, 3, wl.get("perimeter_mm", 0.0), fmt="0.00")
            _put(wm, r, 4, wl.get("ds_mean_mm", 0.0), fmt="0.000")
            _put(wm, r, 5, wl.get("y1_min_mm", 0.0), fmt="0.00000")
            _put(wm, r, 6, y1_mm if y1_mm is not None else "-", fmt="0.00000" if y1_mm is not None else None)
            _put(wm, r, 7, round(y1_mm * 1000.0, 2) if y1_mm is not None else "-", fmt="0.00" if y1_mm is not None else None)
            _put(wm, r, 8, wl.get("yplus_nom_est") if wl.get("yplus_nom_est") is not None else "-", fmt="0.00" if wl.get("yplus_nom_est") is not None else None)
            if yp_p is not None:
                fill_yp = PASS_F if yp_p <= 1.5 else (WARN_F if yp_p <= 10.0 else None)
                font_yp = PASS_T if yp_p <= 1.5 else (WARN_T if yp_p <= 10.0 else F_N)
                _put(wm, r, 9, yp_p, font_yp, "0.00", fill=fill_yp)
            else:
                _put(wm, r, 9, "-")
            r += 1

    # Section 6: Boundary & Face Zone Inventory
    face_zones = m.get("face_zones") or []
    if face_zones:
        r += 1
        _put(wm, r, 1, "6. Face & Boundary Zone Inventory", F_B, fill=SF)
        wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        r += 1
        _hdr(wm, r, ["Face Zone", "Zone ID", "Type Code", "Face Count", "Total Edge Length (m)"])
        r += 1
        for fz in face_zones:
            _put(wm, r, 1, fz.get("name", ""), F_B)
            _put(wm, r, 2, fz.get("id", "-"))
            _put(wm, r, 3, fz.get("zone_type_code", "-"))
            _put(wm, r, 4, fz.get("face_count") if fz.get("face_count") is not None else "-", fmt="#,##0" if isinstance(fz.get("face_count"), int) else None)
            _put(wm, r, 5, fz.get("length_m") if fz.get("length_m") is not None else "-", fmt="0.0000" if isinstance(fz.get("length_m"), (int, float)) else None)
            r += 1

    # Section 7: Solver & Numerical Setup Notes
    r += 1
    _put(wm, r, 1, "7. Auto-Detected Solver Setup & Engineering Notes", F_B, fill=SF)
    wm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    r += 1
    _hdr(wm, r, ["Setup Field", "Detected Value", "Details"])
    r += 1
    schemes = st.get("discretization_schemes") or {}
    scheme_str = ", ".join(f"{k}: {v}" for k, v in schemes.items()) if schemes else "-"
    urf = st.get("under_relaxation") or {}
    urf_str = ", ".join(f"{k}={v}" for k, v in urf.items()) if urf else "-"
    setup_rows = [
        ("Fluent Version / Release", st.get("fluent_release") or st.get("fluent_version") or "n/a", st.get("fluent_version") or "-"),
        ("Turbulence / Viscous Model", st.get("turb_model") or cs.get("turb_model") or "n/a", "Auto-detected from report.xml / .cas.h5"),
        ("Time Formulation", st.get("time_formulation") or cs.get("time_model") or "n/a", f"Planned steps: {st.get('planned_time_steps', 'n/a')} | Max iter/step: {st.get('max_iter_per_step', 'n/a')}"),
        ("Pressure-Velocity Coupling", st.get("pv_coupling") or "n/a", scheme_str),
        ("Under-Relaxation Factors", "Configured" if urf else "n/a", urf_str),
    ]
    for idx_n, note_txt in enumerate(notes, start=1):
        setup_rows.append((f"Auto-Detected Note {idx_n}", "Info", note_txt))

    for lbl, val, det in setup_rows:
        _put(wm, r, 1, lbl, F_B)
        _put(wm, r, 2, val)
        _put(wm, r, 3, det, wrap=True)
        wm.merge_cells(start_row=r, start_column=3, end_row=r, end_column=7)
        r += 1

    for col, width in zip("ABCDEFGHI", [38, 18, 18, 18, 18, 18, 22, 18, 18]):
        wm.column_dimensions[col].width = width


