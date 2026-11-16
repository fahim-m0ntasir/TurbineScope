# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: External Benchmark Overlay & Agreement Metrics (Tier 3.2)
==================================================================================
Imports external literature / experimental CSV benchmark data, computes statistical
agreement metrics (RMS error, Pearson correlation, peak difference, azimuth shift,
point-by-point error table), and applies optional wind tunnel blockage corrections.
"""
from __future__ import annotations

import csv
import os
from typing import Dict, List, Any

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_external_csv(path: str) -> Dict[str, Any]:
    """Parse an external literature or experimental benchmark CSV file.

    Expects CSV with columns such as:
        x, y (or theta, cm or tsr, cp or lambda, cp)
    Optional header or comment lines starting with '#' are parsed for metadata.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"External benchmark file not found: {path}")

    label = os.path.splitext(os.path.basename(path))[0]
    x_vals: List[float] = []
    y_vals: List[float] = []
    x_name = "x"
    y_name = "y"

    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row or not any(row):
                continue
            first = row[0].strip()
            if first.startswith("#"):
                # Check for metadata line: # label: Danao et al. (2014)
                if ":" in first:
                    parts = first.lstrip("#").split(":", 1)
                    if parts[0].strip().lower() == "label":
                        label = parts[1].strip()
                continue

            # Check if header line
            try:
                x_val = float(row[0].strip().replace(",", "."))
                y_val = float(row[1].strip().replace(",", "."))
                x_vals.append(x_val)
                y_vals.append(y_val)
            except ValueError:
                # Text header
                if len(row) >= 2:
                    x_name = row[0].strip()
                    y_name = row[1].strip()
                continue

    if not x_vals:
        raise ValueError(f"No numeric data rows found in external CSV file: {path}")

    x_arr = np.array(x_vals, dtype=float)
    y_arr = np.array(y_vals, dtype=float)

    # Sort by x
    order = np.argsort(x_arr)
    return {
        "x": x_arr[order],
        "y": y_arr[order],
        "label": label,
        "x_name": x_name,
        "y_name": y_name,
        "count": len(x_arr)
    }


def compute_agreement_metrics(
    cfd_x: np.ndarray,
    cfd_y: np.ndarray,
    ext_x: np.ndarray,
    ext_y: np.ndarray,
    is_periodic_azimuth: bool = True
) -> Dict[str, Any]:
    """Compute comprehensive agreement metrics between CFD and external reference data."""
    if len(cfd_x) < 2 or len(ext_x) < 2:
        raise ValueError("Insufficient points to compute agreement metrics.")

    # Interpolate CFD results onto external measurement points
    period = 360.0 if is_periodic_azimuth else None
    cfd_interp = np.interp(ext_x, cfd_x, cfd_y, period=period)

    diff = cfd_interp - ext_y
    abs_diff = np.abs(diff)
    rms_error = float(np.sqrt(np.mean(diff ** 2)))
    max_diff = float(np.max(abs_diff))
    idx_max = int(np.argmax(abs_diff))
    x_at_max_diff = float(ext_x[idx_max])

    # Pearson correlation coefficient R
    std_cfd = np.std(cfd_interp)
    std_ext = np.std(ext_y)
    if std_cfd > 1e-9 and std_ext > 1e-9:
        corr_r = float(np.corrcoef(cfd_interp, ext_y)[0, 1])
    else:
        corr_r = 1.0

    # Peaks and peak shift
    idx_peak_cfd = int(np.argmax(cfd_y))
    idx_peak_ext = int(np.argmax(ext_y))
    peak_cfd = float(cfd_y[idx_peak_cfd])
    peak_ext = float(ext_y[idx_peak_ext])
    x_peak_cfd = float(cfd_x[idx_peak_cfd])
    x_peak_ext = float(ext_x[idx_peak_ext])

    peak_diff = peak_cfd - peak_ext
    peak_x_shift = (x_peak_cfd - x_peak_ext)

    # Point-by-point error table
    point_errors: List[Dict[str, Any]] = []
    pct_errors = []
    for i in range(len(ext_x)):
        ref_denom = abs(ext_y[i]) if abs(ext_y[i]) > 1e-4 else 1.0
        pct_err = abs(diff[i]) / ref_denom * 100.0
        pct_errors.append(pct_err)
        point_errors.append({
            "x": float(ext_x[i]),
            "y_ext": float(ext_y[i]),
            "y_cfd": float(cfd_interp[i]),
            "error": float(diff[i]),
            "pct_error": float(pct_err)
        })

    mean_abs_pct_error = float(np.mean(pct_errors)) if pct_errors else 0.0

    return {
        "rms_error": rms_error,
        "correlation_r": corr_r,
        "r_squared": corr_r ** 2,
        "max_diff": max_diff,
        "x_at_max_diff": x_at_max_diff,
        "peak_cfd": peak_cfd,
        "peak_ext": peak_ext,
        "peak_diff": peak_diff,
        "peak_x_cfd": x_peak_cfd,
        "peak_x_ext": x_peak_ext,
        "peak_x_shift": peak_x_shift,
        "mean_abs_pct_error": mean_abs_pct_error,
        "point_errors": point_errors,
        "n_points": len(ext_x)
    }


def apply_blockage_correction(
    U_inf: float,
    R: float,
    tunnel_width: float,
    Ct: float = 0.8,
    formula: str = "maskell"
) -> Dict[str, Any]:
    """Apply wind tunnel solid / wake blockage correction (Pope or Maskell).

    In 2D wind tunnel testing, blockage ratio epsilon = 2*R / W_tunnel.
    For Maskell: Uc / Uinf = 1 + (1/4) * epsilon * Ct.
    For Pope: Uc / Uinf = 1 + (1/4) * epsilon.

    Returns:
        dict with corrected velocity Uc, velocity ratio factor, and power correction factor (Uinf / Uc)^3.
    """
    if tunnel_width <= 0:
        raise ValueError("Tunnel width must be positive.")

    frontal_width = 2.0 * R
    blockage_ratio = frontal_width / tunnel_width

    if formula.lower() == "maskell":
        # Maskell wake blockage for bluff/drag devices
        factor_u = 1.0 + 0.25 * blockage_ratio * max(0.0, Ct)
    else:
        # Standard Pope / Glauert solid blockage
        factor_u = 1.0 + 0.25 * blockage_ratio

    U_corrected = U_inf * factor_u
    # Cp ~ P / U^3 -> Cp_corrected = Cp_measured * (U_inf / U_corrected)^3
    cp_factor = 1.0 / (factor_u ** 3)
    tsr_factor = 1.0 / factor_u

    return {
        "blockage_ratio": blockage_ratio,
        "blockage_pct": blockage_ratio * 100.0,
        "velocity_factor": factor_u,
        "U_corrected": U_corrected,
        "cp_multiplier": cp_factor,
        "tsr_multiplier": tsr_factor,
        "formula": formula.title(),
        "warning": (
            f"Wind tunnel blockage ratio is {blockage_ratio * 100.0:.1f}%. "
            f"Applying {formula.title()} correction scales Cp by {cp_factor:.4f} and TSR by {tsr_factor:.4f}."
        ) if blockage_ratio > 0.05 else None
    }


def plot_overlay_comparison(
    cfd_x: np.ndarray,
    cfd_y: np.ndarray,
    ext_x: np.ndarray,
    ext_y: np.ndarray,
    metrics: Dict[str, Any],
    filepath: str,
    title: str = "CFD vs Experimental Benchmark Overlay",
    xlabel: str = "Rotor Azimuth $\\theta$ (deg)",
    ylabel: str = "Moment Coefficient $C_m$",
    cfd_label: str = "Current CFD Simulation",
    ext_label: str = "Experimental Data"
) -> str:
    """Render high-resolution overlay graph with agreement metrics annotation box."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5), dpi=300)

    # Plot CFD curve
    ax.plot(cfd_x, cfd_y, "-", color="#1F3864", linewidth=2.0, label=cfd_label)
    # Plot experimental markers
    ax.plot(ext_x, ext_y, "o", color="#DC2626", markersize=6, markerfacecolor="white", markeredgewidth=1.8, label=ext_label)

    # Annotate agreement metrics box
    box_text = (
        f"Agreement Metrics:\n"
        f"• RMS Error: {metrics['rms_error']:.4f}\n"
        f"• Correlation $R$: {metrics['correlation_r']:.3f} ($R^2={metrics['r_squared']:.3f}$)\n"
        f"• Peak $\\Delta$: {metrics['peak_diff']:+.4f}\n"
        f"• Peak Shift: {metrics['peak_x_shift']:+.1f}°\n"
        f"• Mean Abs % Err: {metrics['mean_abs_pct_error']:.1f}%"
    )
    ax.text(
        0.03, 0.96, box_text, transform=ax.transAxes,
        fontsize=9, verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#FFF2CC", edgecolor="#D97706", alpha=0.9)
    )

    ax.set_xlabel(xlabel, fontsize=11, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=11, fontweight="bold")
    ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
    ax.grid(True, linestyle="--", alpha=0.6)
    ax.legend(loc="upper right", frameon=True, fontsize=10)

    plt.tight_layout()
    fig.savefig(filepath)
    plt.close(fig)
    return filepath
