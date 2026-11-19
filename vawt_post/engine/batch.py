# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Multi-TSR Batch Mode & Cp-Lambda Curve Engine (Tier 3.1)
================================================================================
Processes multiple tip speed ratios (TSR) to generate:
- Full Cp-lambda, Ct-lambda, and Cm-lambda performance curves with error bars
- Pure NumPy natural cubic spline interpolation
- Automated identification of peak Cp (Cp,max) and optimal TSR (lambda_opt)
- Comprehensive CSV tables, Excel workbooks, and publication figures
"""
from __future__ import annotations

import copy
import csv
import datetime as _dt
import json
import math
import os
from typing import Callable, Dict, List, Optional, Any

import numpy as np
import matplotlib
if matplotlib.get_backend().lower() not in ("agg", "qtagg"):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as COL

from vawt_post.engine.config import Params
from vawt_post.engine.parser import autodetect
from vawt_post.engine.analysis import analyze


def natural_cubic_spline(x_pts: np.ndarray, y_pts: np.ndarray) -> Callable[[Any], Any]:
    """Pure NumPy natural cubic spline interpolator (zero 2nd derivative at endpoints).

    Does not depend on SciPy; solves the tridiagonal system directly with NumPy.
    """
    x = np.asarray(x_pts, dtype=float)
    y = np.asarray(y_pts, dtype=float)

    # Sort strictly by x
    order = np.argsort(x)
    x = x[order]
    y = y[order]

    n = len(x)
    if n < 2:
        raise ValueError("At least 2 points required for spline interpolation.")
    if n == 2:
        if x[1] <= x[0]:
            raise ValueError("x-coordinates must be strictly increasing.")
        m = (y[1] - y[0]) / (x[1] - x[0])
        return lambda xq: y[0] + m * (np.asarray(xq) - x[0])

    h = np.diff(x)
    if np.any(h <= 0):
        raise ValueError("x-coordinates must be strictly increasing.")

    A = np.zeros((n, n))
    d = np.zeros(n)
    A[0, 0] = 1.0
    A[n - 1, n - 1] = 1.0

    for i in range(1, n - 1):
        A[i, i - 1] = h[i - 1]
        A[i, i] = 2.0 * (h[i - 1] + h[i])
        A[i, i + 1] = h[i]
        d[i] = 6.0 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])

    M = np.linalg.solve(A, d)

    def evaluate(xq: Any) -> Any:
        xq_arr = np.atleast_1d(np.asarray(xq, dtype=float))
        yq = np.empty_like(xq_arr)

        for j, val in enumerate(xq_arr):
            if val <= x[0]:
                i = 0
            elif val >= x[-1]:
                i = n - 2
            else:
                i = int(np.searchsorted(x, val) - 1)
                i = max(0, min(n - 2, i))

            dx = val - x[i]
            hi = h[i]
            term1 = (M[i + 1] / (6.0 * hi)) * (dx ** 3)
            term2 = (M[i] / (6.0 * hi)) * ((hi - dx) ** 3)
            term3 = ((y[i + 1] / hi) - (M[i + 1] * hi / 6.0)) * dx
            term4 = ((y[i] / hi) - (M[i] * hi / 6.0)) * (hi - dx)
            yq[j] = term1 + term2 + term3 + term4

        return yq if np.ndim(xq) > 0 else float(yq[0])

    return evaluate


def pchip_spline(x_pts: np.ndarray, y_pts: np.ndarray) -> Callable[[Any], Any]:
    """Pure NumPy monotonic cubic Hermite (PCHIP / Fritsch-Carlson) interpolator.

    Eliminates cubic spline overshoot, guaranteeing that no false maxima/minima are created
    between sampled operating points.
    """
    x = np.asarray(x_pts, dtype=float)
    y = np.asarray(y_pts, dtype=float)

    order = np.argsort(x)
    x = x[order]
    y = y[order]

    n = len(x)
    if n < 2:
        raise ValueError("At least 2 points required for interpolation.")
    if n == 2:
        if x[1] <= x[0]:
            raise ValueError("x-coordinates must be strictly increasing.")
        m = (y[1] - y[0]) / (x[1] - x[0])
        return lambda xq: y[0] + m * (np.asarray(xq) - x[0])

    h = np.diff(x)
    if np.any(h <= 0):
        raise ValueError("x-coordinates must be strictly increasing.")
    delta = np.diff(y) / h

    d = np.zeros(n)
    d[0] = delta[0]
    d[-1] = delta[-1]

    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] > 0:
            w1 = 2.0 * h[i] + h[i - 1]
            w2 = h[i] + 2.0 * h[i - 1]
            d[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
        else:
            d[i] = 0.0

    for i in range(n - 1):
        if abs(delta[i]) < 1e-12:
            d[i] = 0.0
            d[i + 1] = 0.0
        else:
            alpha = d[i] / delta[i]
            beta = d[i + 1] / delta[i]
            if alpha < 0:
                d[i] = 0.0
            if beta < 0:
                d[i + 1] = 0.0
            phi = alpha ** 2 + beta ** 2
            if phi > 9.0:
                tau = 3.0 / math.sqrt(phi)
                d[i] = tau * alpha * delta[i]
                d[i + 1] = tau * beta * delta[i]

    def evaluate(xq: Any) -> Any:
        xq_arr = np.atleast_1d(np.asarray(xq, dtype=float))
        yq = np.empty_like(xq_arr)

        for j, val in enumerate(xq_arr):
            if val <= x[0]:
                i = 0
            elif val >= x[-1]:
                i = n - 2
            else:
                i = int(np.searchsorted(x, val) - 1)
                i = max(0, min(n - 2, i))

            dx = val - x[i]
            hi = h[i]
            s = dx / hi
            s2 = s * s
            s3 = s2 * s

            h00 = 2.0 * s3 - 3.0 * s2 + 1.0
            h10 = s3 - 2.0 * s2 + s
            h01 = -2.0 * s3 + 3.0 * s2
            h11 = s3 - s2

            yq[j] = h00 * y[i] + h10 * hi * d[i] + h01 * y[i + 1] + h11 * hi * d[i + 1]

        return yq if np.ndim(xq) > 0 else float(yq[0])

    return evaluate


def run_batch_analysis(
    cases: List[Dict[str, Any]],
    outdir: str,
    prefix: str = "VAWT_batch",
    log: Callable[[str], None] = print
) -> Dict[str, Any]:
    """Execute batch processing over multiple tip speed ratios (TSR).

    Args:
        cases: List of case dicts, each with:
            - 'tsr': float (lambda)
            - 'folder': path to folder with Fluent files (optional if 'files' provided)
            - 'files': InputFiles instance (optional if 'folder' provided)
            - 'params': base Params instance (tsr and omega will be assigned)
        outdir: Destination directory for batch output
        prefix: Filename prefix
        log: Logger callback

    Returns:
        Structured batch results dictionary with curve points, spline fit, peak Cp, and figures.
    """
    def _safe_log(msg: str):
        try:
            log(msg)
        except (UnicodeEncodeError, UnicodeError):
            try:
                safe_msg = (
                    msg.replace("ω", "omega")
                    .replace("θ", "theta")
                    .replace("π", "pi")
                    .replace("Δ", "Delta")
                    .replace("λ", "lambda")
                    .replace("μ", "mu")
                    .replace("ρ", "rho")
                    .replace("×", "x")
                    .replace("°", " deg")
                    .replace("✓", "[OK]")
                    .replace("⚠️", "[WARN]")
                )
                log(safe_msg.encode("ascii", errors="replace").decode("ascii"))
            except Exception:
                pass
        except Exception:
            pass

    if len(cases) < 2:
        raise ValueError("Batch analysis requires at least 2 operating points (TSR cases).")

    os.makedirs(outdir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    _safe_log(f"Starting batch post-processing for {len(cases)} operating points...")

    processed_points: List[Dict[str, Any]] = []

    for i, c in enumerate(cases):
        tsr = float(c["tsr"])
        p_base: Optional[Params] = c.get("params")
        if p_base is None:
            p_base = Params(U=8.0, R=0.5, n_blades=3)

        # Deep-clone per TSR point so metadata/applied_repairs/param_sources never bleed across runs
        p = copy.deepcopy(p_base)
        p.tsr = tsr
        p.omega = None  # derive from TSR

        # Input files
        f = c.get("files")
        if f is None and "folder" in c:
            f = autodetect(c["folder"], p.n_blades or 3)
        if f is None:
            raise ValueError(f"Case at TSR = {tsr:g} is missing input files or directory.")

        _safe_log(f"[{i + 1}/{len(cases)}] Analyzing operating condition TSR λ = {tsr:g} ...")
        R = analyze(p, f, log=lambda _: None)
        res = R["res"]
        sel = R["sel"]

        # Revolutions envelope
        cp_sel = [r["cp"] for r in sel]
        cp_mean = float(res.get("cp_reported_mean", res["cp_mean"]))
        cp_min = float(np.min(cp_sel)) if cp_sel else cp_mean
        cp_max = float(np.max(cp_sel)) if cp_sel else cp_mean
        cp_std = float(np.std(cp_sel)) if cp_sel else 0.0

        processed_points.append({
            "tsr": tsr,
            "cp_mean": cp_mean,
            "cp_min": cp_min,
            "cp_max": cp_max,
            "cp_std": cp_std,
            "cm_mean": float(res["cm_mean"]),
            "ct_mean": float(res["ct_mean"]) if res.get("ct_mean") is not None else None,
            "cy_mean": float(res["cy_mean"]) if res.get("cy_mean") is not None else None,
            "power_mean_W": float(res["power_mean_W"]),
            "torque_mean": float(res["torque_mean"]),
            "is_stable": bool(res.get("is_stable", False)),
            "case_result": R
        })

    # Sort strictly by TSR
    processed_points.sort(key=lambda x: x["tsr"])

    tsrs = np.array([p["tsr"] for p in processed_points])
    cps = np.array([p["cp_mean"] for p in processed_points])
    cms = np.array([p["cm_mean"] for p in processed_points])
    cts = np.array([p["ct_mean"] if p["ct_mean"] is not None else np.nan for p in processed_points])

    # Dense monotonic spline interpolation (PCHIP - eliminates cubic overshoot)
    tsr_dense = np.linspace(tsrs[0], tsrs[-1], 300)
    cp_spline_fn = pchip_spline(tsrs, cps)
    cp_dense = cp_spline_fn(tsr_dense)

    cm_spline_fn = pchip_spline(tsrs, cms)
    cm_dense = cm_spline_fn(tsr_dense)

    valid_ct = ~np.isnan(cts)
    have_ct = int(np.sum(valid_ct)) >= 2
    ct_dense = None
    if have_ct:
        ct_spline_fn = pchip_spline(tsrs[valid_ct], cts[valid_ct])
        ct_dense = ct_spline_fn(tsr_dense)

    # Peak Cp and optimal lambda (PCHIP + local quadratic peak refinement)
    idx_peak_samp = int(np.argmax(cps))
    if 0 < idx_peak_samp < len(tsrs) - 1:
        xs_local = tsrs[idx_peak_samp - 1:idx_peak_samp + 2]
        ys_local = cps[idx_peak_samp - 1:idx_peak_samp + 2]
        poly = np.polyfit(xs_local, ys_local, 2)
        if poly[0] < 0:
            x_quad = -poly[1] / (2.0 * poly[0])
            if xs_local[0] <= x_quad <= xs_local[-1]:
                tsr_opt_spline = float(x_quad)
                cp_max_spline = float(np.polyval(poly, x_quad))
            else:
                idx_peak = int(np.argmax(cp_dense))
                cp_max_spline = float(cp_dense[idx_peak])
                tsr_opt_spline = float(tsr_dense[idx_peak])
        else:
            idx_peak = int(np.argmax(cp_dense))
            cp_max_spline = float(cp_dense[idx_peak])
            tsr_opt_spline = float(tsr_dense[idx_peak])
    else:
        idx_peak = int(np.argmax(cp_dense))
        cp_max_spline = float(cp_dense[idx_peak])
        tsr_opt_spline = float(tsr_dense[idx_peak])

    n_supporting_points = len(processed_points)
    _safe_log(f"Batch Analysis Complete: Peak Cp = {cp_max_spline:.4f} at optimal TSR λ_opt = {tsr_opt_spline:.3f} (supported by {n_supporting_points} sampled points)")

    # Generate Publication Figures
    figs: Dict[str, str] = {}

    # 1. Cp vs TSR
    fig, ax = plt.subplots(figsize=(8, 5), dpi=300)
    try:
        yerr_lower = np.maximum(0.0, cps - np.array([p["cp_min"] for p in processed_points]))
        yerr_upper = np.maximum(0.0, np.array([p["cp_max"] for p in processed_points]) - cps)
        ax.errorbar(
            tsrs, cps, yerr=[yerr_lower, yerr_upper],
            fmt="o", color="#1F3864", ecolor="#60A5FA", elinewidth=1.6, capsize=4.5,
            markersize=6.5, label="CFD Data Points (Mean $\\pm$ Rev Envelope)"
        )
        ax.plot(tsr_dense, cp_dense, "-", color="#2563EB", linewidth=2.0, label="Monotone Spline (PCHIP)")

        # Star marker for peak Cp
        ax.plot(tsr_opt_spline, cp_max_spline, marker="*", color="#DC2626", markersize=14, label=f"Peak: $C_{{p,\\max}} = {cp_max_spline:.3f}$ at $\\lambda = {tsr_opt_spline:.2f}$")
        ax.axvline(tsr_opt_spline, color="#DC2626", linestyle=":", alpha=0.5)
        ax.axhline(cp_max_spline, color="#DC2626", linestyle=":", alpha=0.5)

        ax.set_xlabel("Tip Speed Ratio $\\lambda = \\omega R / U_\\infty$", fontsize=11, fontweight="bold")
        ax.set_ylabel("Power Coefficient $C_p$", fontsize=11, fontweight="bold")
        ax.set_title("VAWT Rotor Power Coefficient Curve ($C_p - \\lambda$)", fontsize=12, fontweight="bold", pad=12)
        ax.grid(True, linestyle="--", alpha=0.6)
        ax.legend(frameon=True, fontsize=10)
        fig.tight_layout()
        f_cp = os.path.join(outdir, f"{prefix}_{stamp}_01_cp_vs_tsr.png")
        fig.savefig(f_cp)
        figs["cp_vs_tsr"] = f_cp
    finally:
        plt.close(fig)

    # 2. Cm and Ct vs TSR
    fig, ax1 = plt.subplots(figsize=(8, 5), dpi=300)
    try:
        color_cm = "#059669"
        ax1.plot(tsrs, cms, "s", color=color_cm, markersize=6)
        ax1.plot(tsr_dense, cm_dense, "-", color=color_cm, linewidth=1.8, label="Moment Coeff $C_m$")
        ax1.set_xlabel("Tip Speed Ratio $\\lambda$", fontsize=11, fontweight="bold")
        ax1.set_ylabel("Moment Coefficient $C_m$", color=color_cm, fontsize=11, fontweight="bold")
        ax1.tick_params(axis="y", labelcolor=color_cm)
        ax1.grid(True, linestyle="--", alpha=0.5)

        if have_ct and ct_dense is not None:
            ax2 = ax1.twinx()
            color_ct = "#7C3AED"
            ax2.plot(tsrs[valid_ct], cts[valid_ct], "^", color=color_ct, markersize=6)
            ax2.plot(tsr_dense, ct_dense, "--", color=color_ct, linewidth=1.8, label="Thrust Coeff $C_t$")
            ax2.set_ylabel("Thrust Coefficient $C_t$ ($F_x$)", color=color_ct, fontsize=11, fontweight="bold")
            ax2.tick_params(axis="y", labelcolor=color_ct)

        ax1.set_title("VAWT Rotor $C_m - \\lambda$ and $C_t - \\lambda$ Characteristics", fontsize=12, fontweight="bold", pad=12)
        fig.tight_layout()
        f_cm = os.path.join(outdir, f"{prefix}_{stamp}_02_cm_ct_vs_tsr.png")
        fig.savefig(f_cm)
        figs["cm_ct_vs_tsr"] = f_cm
    finally:
        plt.close(fig)

    # Export CSV Files
    csv_table_path = os.path.join(outdir, f"{prefix}_{stamp}_cp_lambda_table.csv")
    with open(csv_table_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["TSR_lambda", "Cp_mean", "Cp_min", "Cp_max", "Cp_std", "Cm_mean", "Ct_mean", "Power_W", "Torque_Nm", "Status"])
        for p in processed_points:
            writer.writerow([
                f"{p['tsr']:.4f}",
                f"{p['cp_mean']:.6f}",
                f"{p['cp_min']:.6f}",
                f"{p['cp_max']:.6f}",
                f"{p['cp_std']:.6f}",
                f"{p['cm_mean']:.6f}",
                f"{p['ct_mean']:.6f}" if p["ct_mean"] is not None else "",
                f"{p['power_mean_W']:.3f}",
                f"{p['torque_mean']:.4f}",
                "STABLE" if p["is_stable"] else "UNCONVERGED"
            ])

    # Export Excel Workbook
    xlsx_path = os.path.join(outdir, f"{prefix}_{stamp}.xlsx")
    export_batch_excel(processed_points, tsr_dense, cp_dense, cm_dense, ct_dense, tsr_opt_spline, cp_max_spline, xlsx_path)

    # Export JSON
    batch_json_path = os.path.join(outdir, f"{prefix}_{stamp}_results.json")
    json_data = {
        "app": {"name": "VAWT CFD Post-Processor", "version": "2.0.0"},
        "peak_performance": {
            "cp_max": cp_max_spline,
            "lambda_opt": tsr_opt_spline,
            "n_supporting_points": n_supporting_points,
            "method": "PCHIP (Monotone cubic Hermite) + local quadratic refinement",
        },
        "points": [
            {k: v for k, v in pt.items() if k != "case_result"}
            for pt in processed_points
        ],
        "spline_fit": {
            "tsr": tsr_dense.tolist(),
            "cp": cp_dense.tolist(),
            "cm": cm_dense.tolist(),
            "ct": ct_dense.tolist() if ct_dense is not None else None,
        }
    }
    with open(batch_json_path, "w", encoding="utf-8") as fh:
        json.dump(json_data, fh, indent=2)

    return {
        "points": processed_points,
        "tsrs": tsrs,
        "cps": cps,
        "tsr_opt": tsr_opt_spline,
        "cp_max": cp_max_spline,
        "tsr_dense": tsr_dense,
        "cp_dense": cp_dense,
        "figs": figs,
        "csv_table": csv_table_path,
        "xlsx": xlsx_path,
        "results_json": batch_json_path
    }


def export_batch_excel(
    points: List[Dict[str, Any]],
    tsr_dense: np.ndarray,
    cp_dense: np.ndarray,
    cm_dense: np.ndarray,
    ct_dense: Optional[np.ndarray],
    tsr_opt: float,
    cp_max: float,
    filepath: str
) -> None:
    """Export complete batch results workbook (.xlsx) with native formulas and formatting."""
    wb = Workbook()
    ws_sum: Any = wb.active
    ws_sum.title = "Batch_Summary"

    HF = PatternFill("solid", fgColor="1F3864")
    PEAK_F = PatternFill("solid", fgColor="FFF2CC")
    F_H = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    F_B = Font(name="Arial", size=10, bold=True)
    F_N = Font(name="Arial", size=10)
    F_T = Font(name="Arial", size=14, bold=True, color="1F3864")

    # Summary Sheet
    ws_sum.cell(row=1, column=1, value="VAWT Multi-TSR Performance Curve Summary").font = F_T
    ws_sum.cell(row=2, column=1, value=f"Evaluated {len(points)} Tip Speed Ratios (λ ∈ [{points[0]['tsr']:g}, {points[-1]['tsr']:g}])").font = F_N

    ws_sum.cell(row=4, column=1, value="Key Performance Metric").font, ws_sum.cell(row=4, column=1).fill = F_H, HF
    ws_sum.cell(row=4, column=2, value="Value").font, ws_sum.cell(row=4, column=2).fill = F_H, HF
    ws_sum.cell(row=4, column=3, value="Unit").font, ws_sum.cell(row=4, column=3).fill = F_H, HF
    ws_sum.cell(row=4, column=4, value="Derivation & Optimization Notes").font, ws_sum.cell(row=4, column=4).fill = F_H, HF

    sum_rows = [
        ("Peak Power Coefficient (Cp,max)", cp_max, "-", "Maximum power coefficient from cubic spline fit", "0.0000"),
        ("Optimal Tip Speed Ratio (λ_opt)", tsr_opt, "-", "Operating point yielding peak Cp", "0.000"),
        ("TSR operating span", f"{points[0]['tsr']:g} to {points[-1]['tsr']:g}", "-", f"{len(points)} evaluated design conditions", None),
        ("Starting torque capability (λ -> 0)", points[0]["cm_mean"], "-", f"Mean Cm at lowest tested TSR ({points[0]['tsr']:g})", "0.0000"),
    ]
    for idx, (lbl, val, unit, note, fmt) in enumerate(sum_rows):
        r = 5 + idx
        ws_sum.cell(row=r, column=1, value=lbl).font = F_B
        c2 = ws_sum.cell(row=r, column=2, value=val)
        c2.font = F_B if "Peak" in lbl or "Optimal" in lbl else F_N
        if "Peak" in lbl or "Optimal" in lbl:
            c2.fill = PEAK_F
        if fmt and isinstance(val, (int, float)):
            c2.number_format = fmt
        ws_sum.cell(row=r, column=3, value=unit).font = F_N
        ws_sum.cell(row=r, column=4, value=note).font = F_N

    ws_sum.column_dimensions["A"].width = 38
    ws_sum.column_dimensions["B"].width = 18
    ws_sum.column_dimensions["C"].width = 10
    ws_sum.column_dimensions["D"].width = 50

    # Data Table Sheet
    ws_data = wb.create_sheet("Data_Points")
    headers = [
        "TSR λ", "Mean Cp", "Min Cp (rev)", "Max Cp (rev)", "Std Cp",
        "Mean Cm", "Mean Ct", "Mean Power (W)", "Mean Torque (N·m)", "Convergence"
    ]
    for col_idx, h in enumerate(headers):
        c = ws_data.cell(row=1, column=col_idx + 1, value=h)
        c.font, c.fill = F_H, HF
        c.alignment = Alignment(horizontal="center")

    for row_idx, p in enumerate(points):
        r_num = 2 + row_idx
        ws_data.cell(row=r_num, column=1, value=p["tsr"]).number_format = "0.000"
        ws_data.cell(row=r_num, column=2, value=p["cp_mean"]).number_format = "0.0000"
        ws_data.cell(row=r_num, column=3, value=p["cp_min"]).number_format = "0.0000"
        ws_data.cell(row=r_num, column=4, value=p["cp_max"]).number_format = "0.0000"
        ws_data.cell(row=r_num, column=5, value=p["cp_std"]).number_format = "0.0000"
        ws_data.cell(row=r_num, column=6, value=p["cm_mean"]).number_format = "0.0000"
        if p["ct_mean"] is not None:
            ws_data.cell(row=r_num, column=7, value=p["ct_mean"]).number_format = "0.0000"
        ws_data.cell(row=r_num, column=8, value=p["power_mean_W"]).number_format = "0.00"
        ws_data.cell(row=r_num, column=9, value=p["torque_mean"]).number_format = "0.0000"
        ws_data.cell(row=r_num, column=10, value="STABLE" if p["is_stable"] else "UNCONVERGED")

    for j in range(len(headers)):
        ws_data.column_dimensions[COL(j + 1)].width = 16

    # Spline Fit Dense Sheet
    ws_sp = wb.create_sheet("Spline_Curve")
    sp_headers = ["TSR λ", "Spline Cp", "Spline Cm"]
    if ct_dense is not None:
        sp_headers.append("Spline Ct")
    for col_idx, h in enumerate(sp_headers):
        c = ws_sp.cell(row=1, column=col_idx + 1, value=h)
        c.font, c.fill = F_H, HF

    for i in range(len(tsr_dense)):
        r_num = 2 + i
        ws_sp.cell(row=r_num, column=1, value=float(tsr_dense[i])).number_format = "0.0000"
        ws_sp.cell(row=r_num, column=2, value=float(cp_dense[i])).number_format = "0.0000"
        ws_sp.cell(row=r_num, column=3, value=float(cm_dense[i])).number_format = "0.0000"
        if ct_dense is not None:
            ws_sp.cell(row=r_num, column=4, value=float(ct_dense[i])).number_format = "0.0000"

    for j in range(len(sp_headers)):
        ws_sp.column_dimensions[COL(j + 1)].width = 16

    try:
        wb.save(filepath)
    finally:
        wb.close()
