# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Blade-Resolved Forces, Dynamic Stall & Kinematics (Tier 3.4)
=====================================================================================
Computes normal and tangential blade force coefficients (Cn, Ct), geometric angle of
attack (alpha), reduced frequency (k = omega*c / 2W), dynamic stall hysteresis loops
(Cm or Cn vs alpha colored by azimuth), and deep stall zone aids.
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple, Any

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.collections import LineCollection


def classify_reduced_frequency(k: float) -> str:
    """Classify flow unsteadiness based on reduced frequency k = omega*c / (2W).

    Classes:
      k < 0.05: Quasi-steady aerodynamics
      0.05 <= k <= 0.2: Unsteady aerodynamic effects significant
      k > 0.2: Strongly unsteady flow
    """
    if k < 0.05:
        return "Quasi-steady aerodynamics (k < 0.05)"
    elif k <= 0.2:
        return "Unsteady aerodynamic effects significant (0.05 <= k <= 0.2)"
    else:
        return "Strongly unsteady flow (k > 0.2)"


def compute_blade_kinematics(
    theta_deg: np.ndarray,
    tsr: float,
    chord: Optional[float],
    R: float,
    omega: float,
    U: float,
    static_stall_deg: float = 12.0,
    rotation_dir: str = "ccw"
) -> Dict[str, Any]:
    """Calculate geometric angle of attack, relative velocity, and reduced frequency across azimuth.

    Note: All angles of attack (alpha) are purely geometric kinematics based on blade orbital
    geometry without aerodynamic induction corrections.
    theta_deg is measured from the into-wind position (0° -> 360° along rotation direction).
    """
    th = np.asarray(theta_deg, dtype=float)
    th_r = np.radians(th)

    # Relative velocity ratio W / U (at constant free-stream speed U)
    w_over_u = np.sqrt(1.0 + 2.0 * tsr * np.cos(th_r) + tsr ** 2)
    w_speed = U * w_over_u

    # Geometric angle of attack (deg, no induction)
    # At theta = 0 (into wind), alpha = 0.
    alpha = np.degrees(np.arctan2(np.sin(th_r), np.cos(th_r) + tsr))

    # Reduced frequency k = omega * c / (2 * W)
    if chord and chord > 0:
        k_reduced = (omega * chord) / (2.0 * np.maximum(w_speed, 1e-6))
    else:
        k_reduced = np.zeros_like(th)

    # Dynamic stall indicators (based on geometric stall exceedance)
    is_deep_stall = np.abs(alpha) >= static_stall_deg
    stall_fraction_pct = float(np.mean(is_deep_stall) * 100.0) if len(is_deep_stall) > 0 else 0.0

    # First azimuth angle in [0, 180) where geometric stall is exceeded
    upwind_mask = (th >= 0.0) & (th < 180.0)
    stall_upwind = th[upwind_mask & is_deep_stall]
    geometric_stall_exceedance_theta = float(stall_upwind[0]) if len(stall_upwind) > 0 else None

    if len(th) > 0:
        idx_peak_alpha = int(np.argmax(np.abs(alpha)))
        max_alpha = float(np.abs(alpha[idx_peak_alpha]))
        max_alpha_theta = float(th[idx_peak_alpha])
    else:
        max_alpha = 0.0
        max_alpha_theta = 0.0

    k_min = float(np.min(k_reduced)) if len(k_reduced) > 0 else 0.0
    k_mean = float(np.mean(k_reduced)) if len(k_reduced) > 0 else 0.0
    k_max = float(np.max(k_reduced)) if len(k_reduced) > 0 else 0.0
    k_regime = classify_reduced_frequency(k_max)

    return {
        "theta": th,
        "w_over_u": w_over_u,
        "w_speed": w_speed,
        "w_mean": float(np.mean(w_speed)) if len(w_speed) > 0 else 0.0,
        "alpha": alpha,
        "k_reduced": k_reduced,
        "k_min": k_min,
        "k_mean": k_mean,
        "k_max": k_max,
        "k_regime": k_regime,
        "regime": k_regime,
        "static_stall_deg": static_stall_deg,
        "is_deep_stall": is_deep_stall,
        "stall_fraction_pct": stall_fraction_pct,
        "geometric_stall_exceedance_theta": geometric_stall_exceedance_theta,
        "stall_onset_theta": geometric_stall_exceedance_theta,  # backwards compatibility alias
        "max_alpha": max_alpha,
        "max_alpha_theta": max_alpha_theta,
    }


def compute_normal_tangential_coefficients(
    Fx: np.ndarray,
    Fy: np.ndarray,
    theta_deg: np.ndarray,
    rho: float,
    U: float,
    chord: float,
    depth: float = 1.0,
    rotation_dir: str = "ccw"
) -> Tuple[np.ndarray, np.ndarray]:
    """Convert global blade forces (Fx, Fy) to blade-aligned normal (Cn) and tangential (Ctan) coefficients.

    Coordinate system:
      Azimuth θ is measured from the into-wind position (top for CCW, bottom for CW).
      For CCW:
        Tangential unit vector along blade motion: e_tan = (-cos θ, -sin θ).
        Outward normal unit vector: e_n = (-sin θ, cos θ).
        Ftan = -Fx·cos θ - Fy·sin θ.
        Fn = -Fx·sin θ + Fy·cos θ.
      For CW:
        Tangential unit vector along blade motion: e_tan = (-cos θ, +sin θ).
        Outward normal unit vector: e_n = (-sin θ, -cos θ).
        Ftan = -Fx·cos θ + Fy·sin θ.
        Fn = -Fx·sin θ - Fy·cos θ.

    Normalized by free-stream dynamic pressure reference:
      q_inf = 0.5 * rho * (U ** 2) * chord * depth.
      Cn = Fn / q_inf
      Ctan = Ftan / q_inf (renamed from Ct to prevent clashing with rotor thrust coefficient).
    """
    Fx_arr = np.asarray(Fx, dtype=float)
    Fy_arr = np.asarray(Fy, dtype=float)
    th_arr = np.asarray(theta_deg, dtype=float)
    if Fx_arr.size == 0 or Fy_arr.size == 0 or th_arr.size == 0:
        empty = np.zeros(0, dtype=float)
        return empty, empty

    th_r = np.radians(th_arr)
    if rotation_dir == "cw":
        Fn = -Fx_arr * np.sin(th_r) - Fy_arr * np.cos(th_r)
        Ftan = -Fx_arr * np.cos(th_r) + Fy_arr * np.sin(th_r)
    else:
        Fn = -Fx_arr * np.sin(th_r) + Fy_arr * np.cos(th_r)
        Ftan = -Fx_arr * np.cos(th_r) - Fy_arr * np.sin(th_r)

    q = 0.5 * rho * (U ** 2) * chord * depth
    if not np.isfinite(q) or q <= 1e-12:
        zeros = np.zeros_like(Fn, dtype=float)
        return zeros, zeros.copy()
    Cn = Fn / q
    Ctan = Ftan / q
    return Cn, Ctan


def plot_hysteresis_loop(
    alpha: np.ndarray,
    y_coeff: np.ndarray,
    theta: np.ndarray,
    filepath: str,
    static_stall_deg: float = 15.0,
    ylabel: str = "Blade Moment Coefficient $C_m$",
    title: str = "Dynamic Stall Hysteresis Loop ($C_m - \\alpha$)"
) -> str:
    """Render dynamic stall hysteresis loop colored by blade azimuth angle."""
    out_dir = os.path.dirname(os.path.abspath(filepath))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=300, layout="constrained")
    try:
        alpha = np.asarray(alpha, dtype=float)
        y_coeff = np.asarray(y_coeff, dtype=float)
        theta = np.asarray(theta, dtype=float)
        valid = np.isfinite(alpha) & np.isfinite(y_coeff) & np.isfinite(theta)
        alpha_v, y_v, theta_v = alpha[valid], y_coeff[valid], theta[valid]
        if len(alpha_v) >= 2:
            # Create colored line segments by azimuth
            points = np.array([alpha_v, y_v]).T.reshape(-1, 1, 2)
            segments = np.concatenate([points[:-1], points[1:]], axis=1)

            norm = Normalize(0.0, 360.0)
            lc = LineCollection(list(segments), cmap="twilight", norm=norm, linewidth=2.4)
            lc.set_array(theta_v[:-1])
            line = ax.add_collection(lc)

            # Plot start marker at theta=0
            ax.plot(alpha_v[0], y_v[0], "ko", markersize=6, label="$\\theta = 0^\\circ$")

            cbar = fig.colorbar(line, ax=ax, pad=0.03)
            cbar.set_label("Rotor Azimuth $\\theta$ (deg)", fontsize=10, fontweight="bold")
            cbar.set_ticks([0, 90, 180, 270, 360])

            pad_x = max(2.0, float(np.max(alpha_v) - np.min(alpha_v)) * 0.08)
            pad_y = max(0.05, float(np.max(y_v) - np.min(y_v)) * 0.08)
            ax.set_xlim(min(-static_stall_deg * 1.15, float(np.min(alpha_v)) - pad_x), max(static_stall_deg * 1.15, float(np.max(alpha_v)) + pad_x))
            ax.set_ylim(float(np.min(y_v)) - pad_y, float(np.max(y_v)) + pad_y)
        else:
            ax.set_xlim(-static_stall_deg * 1.5, static_stall_deg * 1.5)
            ax.set_ylim(-0.5, 0.5)

        # Static stall threshold lines
        ax.axvline(static_stall_deg, color="#DC2626", linestyle="--", linewidth=1.4, alpha=0.8, label=f"Static Stall ($\\pm{static_stall_deg:g}^\\circ$)")
        ax.axvline(-static_stall_deg, color="#DC2626", linestyle="--", linewidth=1.4, alpha=0.8)

        ax.set_xlabel("Geometric Angle of Attack $\\alpha$ (deg)", fontsize=11, fontweight="bold")
        ax.set_ylabel(ylabel, fontsize=11, fontweight="bold")
        ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        ax.grid(True, linestyle="--", alpha=0.6)
        ax.legend(loc="upper left", frameon=True, fontsize=9)

        fig.savefig(filepath)
    finally:
        plt.close(fig)
    return filepath


def plot_dynamic_stall_aids(
    kin: Dict[str, Any],
    Cm: np.ndarray,
    filepath: str
) -> str:
    """Render 3-panel dynamic stall aids figure: alpha(theta), Cm(theta), and reduced frequency k(theta)."""
    out_dir = os.path.dirname(os.path.abspath(filepath))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    th = np.asarray(kin["theta"], dtype=float)
    alpha = np.asarray(kin["alpha"], dtype=float)
    k_red = np.asarray(kin["k_reduced"], dtype=float)
    cm_arr = np.asarray(Cm, dtype=float)
    stall_angle = kin["static_stall_deg"]

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(8.5, 7.5), dpi=300, sharex=True, layout="constrained")
    try:
        # Panel 1: Angle of attack vs azimuth with shaded stall band
        ax1.plot(th, alpha, color="#1F3864", linewidth=2.0, label="Geometric $\\alpha(\\theta)$")
        ax1.axhline(stall_angle, color="#DC2626", linestyle="--", linewidth=1.2, label=f"Static Stall limit $\\pm{stall_angle:g}^\\circ$")
        ax1.axhline(-stall_angle, color="#DC2626", linestyle="--", linewidth=1.2)
        ax1.fill_between(th, stall_angle, alpha, where=(alpha > stall_angle), color="#FEE2E2", alpha=0.7, label="Deep Stall Region")
        ax1.fill_between(th, -stall_angle, alpha, where=(alpha < -stall_angle), color="#FEE2E2", alpha=0.7)
        ax1.set_ylabel("AoA $\\alpha$ (deg)", fontsize=10, fontweight="bold")
        ax1.set_title(
            f"Dynamic Stall Aerodynamics (Deep Stall Duration: {kin['stall_fraction_pct']:.1f}% of rotation)",
            fontsize=12, fontweight="bold"
        )
        ax1.grid(True, linestyle="--", alpha=0.5)
        ax1.legend(loc="upper right", frameon=True, fontsize=8)

        # Panel 2: Moment coefficient vs azimuth with shaded stall band
        ax2.plot(th, cm_arr, color="#059669", linewidth=2.0, label="Blade $C_m(\\theta)$")
        fin_cm = cm_arr[np.isfinite(cm_arr)]
        if len(fin_cm) > 0 and len(th) == len(alpha):
            ax2.fill_between(th, float(np.min(fin_cm)), float(np.max(fin_cm)), where=(np.abs(alpha) > stall_angle), color="#FEE2E2", alpha=0.45, label="Stall Phase")
        ax2.set_ylabel("Blade $C_m$", fontsize=10, fontweight="bold")
        ax2.grid(True, linestyle="--", alpha=0.5)
        ax2.legend(loc="upper right", frameon=True, fontsize=8)

        # Panel 3: Reduced frequency k vs azimuth
        ax3.plot(th, k_red, color="#7C3AED", linewidth=2.0, label="Reduced Freq $k = \\omega c / 2W$")
        ax3.axhline(0.05, color="#D97706", linestyle=":", linewidth=1.5, label="Unsteady threshold ($k = 0.05$)")
        ax3.axhline(0.20, color="#DC2626", linestyle="--", linewidth=1.5, label="Strongly unsteady ($k = 0.20$)")
        ax3.set_xlabel("Rotor Azimuth $\\theta$ (deg)", fontsize=11, fontweight="bold")
        ax3.set_ylabel("Reduced Freq $k$", fontsize=10, fontweight="bold")
        ax3.set_xlim(0, 360)
        ax3.grid(True, linestyle="--", alpha=0.5)
        ax3.legend(loc="upper right", frameon=True, fontsize=8)

        fig.savefig(filepath)
    finally:
        plt.close(fig)
    return filepath
