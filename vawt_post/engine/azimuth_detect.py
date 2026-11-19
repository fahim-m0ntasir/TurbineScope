# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Start Azimuth & Mid-Spin Detection
===========================================================
Unified 3-tier start-azimuth detector shared by Auto-Detect, File-Health
Diagnostics, Offline Analysis, and Live Monitor:

  Tier 1 (Exact): Flow-time & time-step continuity (t[0] > 0 / step[0] > 1).
  Tier 2 (Exact): Native HDF5 `.cas.h5` blade mesh centroid orientation.
  Tier 3 (Physics): Aerodynamic 1P torque harmonic & upwind-peak phase from
                    Blade 1 monitor data (plus total-torque N*f0 modulo note).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import os
from typing import Any, Dict, Optional

import numpy as np

from vawt_post.engine.config import Params


def wrap_deg_360(deg: float) -> float:
    """Wrap angle in degrees onto [0.0, 360.0)."""
    val = float(deg) % 360.0
    if abs(val - 360.0) < 1e-9 or abs(val) < 1e-9:
        return 0.0
    return val


def wrap_deg_signed(deg: float) -> float:
    """Wrap angle difference in degrees onto (-180.0, 180.0]."""
    val = ((float(deg) + 180.0) % 360.0) - 180.0
    if val <= -180.0 + 1e-12:
        return 180.0
    return val


def effective_moment_sign(p: Params) -> float:
    """Return multiplier (+1 or -1) converting Fluent moment to power-positive sign."""
    if getattr(p, "moment_sign", "fixed") in ("+1", "-1"):
        return float(p.moment_sign)
    return -1.0 if getattr(p, "rotation_dir", "ccw") == "cw" else 1.0


def compute_az_base_without_theta0(p: Params) -> float:
    """Compute fixed datum offset (zero-position map + manual azimuth_offset) without theta0."""
    is_cw = (getattr(p, "rotation_dir", "ccw") == "cw")
    zero_pos = getattr(p, "azimuth_zero", "into_wind")
    if is_cw:
        zero_map = {
            "into_wind": 0.0,
            "bottom": 0.0,
            "front": 90.0,
            "top": 180.0,
            "rear": 270.0,
        }
    else:
        zero_map = {
            "into_wind": 0.0,
            "top": 0.0,
            "front": 90.0,
            "bottom": 180.0,
            "rear": 270.0,
        }
    return float(zero_map.get(zero_pos, 0.0) + getattr(p, "azimuth_offset", 0.0))


def compute_az_base(p: Params) -> float:
    """Compute full initial azimuth datum in degrees (matching analysis.py and Live Monitor)."""
    return compute_az_base_without_theta0(p) + float(getattr(p, "theta0", 0.0) or 0.0)


@dataclass
class StartAzimuthResult:
    """Structured result describing whether collection started at 0 deg or mid-spin."""

    started_at_zero: Optional[bool] = None
    is_mid_spin: bool = False
    needs_correction: bool = False
    mid_spin_compensated_by_flow_time: bool = False
    entered_theta0_deg: float = 0.0
    recommended_theta0_deg: Optional[float] = None
    flow_start_angle_deg: Optional[float] = None
    shift_error_deg: Optional[float] = None
    method: str = "insufficient_data"
    confidence: str = "low"
    summary: str = "Insufficient data to verify start azimuth."
    detail: str = ""
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _extract_cas_blade1_angle(
    cas_info: Any,
    rotation_dir: str,
) -> Optional[float]:
    """Compute geometric azimuth angle (deg, from into-wind 0 deg) of Blade 1 from .cas.h5 metadata."""
    if cas_info is None:
        return None
    if isinstance(cas_info, dict):
        centroids = cas_info.get("blade_centroids") or []
        zones = cas_info.get("blade_zones") or []
        cx_c = float(cas_info.get("mom_center_x", 0.0) or 0.0)
        cy_c = float(cas_info.get("mom_center_y", 0.0) or 0.0)
    else:
        centroids = getattr(cas_info, "blade_centroids", None) or []
        zones = getattr(cas_info, "blade_zones", None) or []
        cx_c, cy_c = 0.0, 0.0
        rdefs = getattr(cas_info, "report_definitions", None) or {}
        for rname, rdef in rdefs.items():
            if "moment" in str(rname).lower():
                mc = getattr(rdef, "mom_center", None)
                if mc and len(mc) >= 2:
                    cx_c, cy_c = float(mc[0]), float(mc[1])
                    break

    if not centroids:
        return None

    idx = 0
    if zones and len(zones) == len(centroids):
        for i, z in enumerate(zones):
            z_low = str(z).lower()
            if z_low.endswith(("1", "_1", "-1")) or "blade1" in z_low or "blade-1" in z_low or "blade_1" in z_low:
                idx = i
                break

    bx, by = centroids[idx]
    dx = float(bx) - cx_c
    dy = float(by) - cy_c
    if math.hypot(dx, dy) <= 1e-9:
        return None

    # Matching forces.py outward normal convention:
    #   CCW: e_n = (-sin th,  cos th) -> th = atan2(-dx,  dy)
    #   CW:  e_n = (-sin th, -cos th) -> th = atan2(-dx, -dy)
    if rotation_dir == "cw":
        ang = math.degrees(math.atan2(-dx, -dy))
    else:
        ang = math.degrees(math.atan2(-dx, dy))
    return wrap_deg_360(ang)


def _bin_1p_phase(
    theta_deg: np.ndarray,
    values: np.ndarray,
    n_bins: int = 72,
) -> Optional[Dict[str, float]]:
    """Bin moment signal by azimuth and compute 1P harmonic centroid and peak stats."""
    bw = 360.0 / float(n_bins)
    centers = (np.arange(n_bins) + 0.5) * bw
    idx = np.clip(np.floor(np.mod(theta_deg, 360.0) / bw).astype(int), 0, n_bins - 1)
    sums = np.zeros(n_bins, dtype=float)
    cnts = np.zeros(n_bins, dtype=int)
    np.add.at(sums, idx, values)
    np.add.at(cnts, idx, 1)
    valid = cnts > 0
    if int(np.sum(valid)) < max(8, n_bins // 6):
        return None

    binned = np.zeros(n_bins, dtype=float)
    binned[valid] = sums[valid] / cnts[valid]
    if not np.all(valid):
        binned[~valid] = np.interp(centers[~valid], centers[valid], binned[valid], period=360.0)

    v_max = float(np.max(binned))
    v_min = float(np.min(binned))
    v_range = v_max - v_min
    if v_range <= 1e-12:
        return None

    th_r = np.radians(centers)
    c1 = 2.0 * float(np.mean(binned * np.cos(th_r)))
    s1 = 2.0 * float(np.mean(binned * np.sin(th_r)))
    amp1 = math.hypot(c1, s1)
    r1 = amp1 / (0.5 * v_range)
    theta_1p = wrap_deg_360(math.degrees(math.atan2(s1, c1)))
    theta_peak = float(centers[int(np.argmax(binned))])

    m0_mask = (centers <= 12.5) | (centers >= 347.5)
    m180_mask = (centers >= 167.5) & (centers <= 192.5)
    m_at_0 = float(np.mean(binned[m0_mask])) if m0_mask.any() else float(binned[0])
    m_at_180 = float(np.mean(binned[m180_mask])) if m180_mask.any() else float(binned[n_bins // 2])

    return {
        "r1": float(r1),
        "amp1": float(amp1),
        "v_range": float(v_range),
        "v_max": float(v_max),
        "v_min": float(v_min),
        "theta_1p": float(theta_1p),
        "theta_peak": float(theta_peak),
        "m_at_0": float(m_at_0),
        "m_at_180": float(m_at_180),
        "drag0_ratio": float((m_at_0 - v_min) / v_range),
    }


def detect_start_azimuth(
    p: Params,
    *,
    t: Optional[np.ndarray] = None,
    step: Optional[np.ndarray] = None,
    m_b1: Optional[np.ndarray] = None,
    m_total: Optional[np.ndarray] = None,
    apply_moment_sign: bool = True,
    cas_info: Any = None,
    folder: Optional[str] = None,
    tolerance_deg: float = 22.0,
) -> StartAzimuthResult:
    """Detect whether data collection started at 0 deg or mid-spin across Tiers 1-3.

    Parameters
    ----------
    p : Params
        Active turbine and analysis parameters.
    t : ndarray, optional
        Flow-time array (seconds).
    step : ndarray, optional
        Time-step index array.
    m_b1 : ndarray, optional
        Blade 1 moment time series (preferred for unambiguous 0-360 deg phase detection).
    m_total : ndarray, optional
        Total rotor moment time series (fallback for modulo 360/N check).
    apply_moment_sign : bool
        Set True if moment arrays are raw CFD report values; False if already signed.
    cas_info : CaseInspectionResult | dict, optional
        Parsed HDF5 `.cas.h5` geometry info (if already loaded).
    folder : str, optional
        Folder containing `.cas.h5` if `cas_info` is omitted.
    tolerance_deg : float
        Maximum acceptable deviation (deg) before flagging an uncompensated mid-spin shift.
    """
    entered_theta0 = wrap_deg_360(float(getattr(p, "theta0", 0.0) or 0.0))
    az_base = compute_az_base(p)
    rot_dir = getattr(p, "rotation_dir", "ccw") or "ccw"

    try:
        omega = abs(p.omega_val())
    except Exception:
        omega_raw = getattr(p, "omega", None)
        omega = abs(float(omega_raw)) if omega_raw is not None else 0.0

    Trev = (2.0 * math.pi / omega) if omega > 0 else 0.0
    sign = effective_moment_sign(p) if apply_moment_sign else 1.0

    # Clean finite time / step series (deduplicated & sorted by step if provided)
    t_arr: Optional[np.ndarray] = None
    step_arr: Optional[np.ndarray] = None
    b1_arr: Optional[np.ndarray] = None
    if t is not None:
        raw_t = np.asarray(t, dtype=float)
        mask_t = np.isfinite(raw_t)
        if mask_t.any():
            t_arr = raw_t[mask_t]
            if m_b1 is not None and len(m_b1) == len(raw_t):
                b1_arr = np.asarray(m_b1, dtype=float)[mask_t]
            if step is not None and len(step) == len(raw_t):
                step_arr = np.asarray(step, dtype=np.int64)[mask_t]
                # Keep last occurrence per step and sort ascending by step
                if len(step_arr) > 1 and (np.any(np.diff(step_arr) <= 0)):
                    _, rev_idx = np.unique(step_arr[::-1], return_index=True)
                    keep_idx = (len(step_arr) - 1) - rev_idx
                    order = np.argsort(step_arr[keep_idx], kind="stable")
                    keep_idx = keep_idx[order]
                    step_arr = step_arr[keep_idx]
                    t_arr = t_arr[keep_idx]
                    if b1_arr is not None:
                        b1_arr = b1_arr[keep_idx]
    if step_arr is None and step is not None and len(step) > 0:
        step_arr = np.asarray(step, dtype=np.int64)

    t0 = float(t_arr[0]) if (t_arr is not None and len(t_arr) > 0) else None
    t_end = float(t_arr[-1]) if (t_arr is not None and len(t_arr) > 0) else None
    step0 = int(step_arr[0]) if (step_arr is not None and len(step_arr) > 0) else None
    t_span = (t_end - t0) if (t0 is not None and t_end is not None) else 0.0

    # ---- Tier 1: Flow-Time & Step Continuity ----
    flow_mid_run = False
    flow_start_deg: Optional[float] = None
    if t0 is not None and omega > 0 and Trev > 0:
        flow_start_deg = wrap_deg_360(az_base + math.degrees(omega * t0))
        if abs(t0) > 0.01 * Trev or (step0 is not None and step0 > 1 and abs(t0) > 1e-6):
            flow_mid_run = True

    signals: Dict[str, Any] = {
        "t0": t0,
        "t_end": t_end,
        "step0": step0,
        "t_span": t_span,
        "revs_span": (t_span / Trev) if Trev > 0 else 0.0,
        "flow_start_mid_run": flow_mid_run,
        "flow_start_angle_deg": round(flow_start_deg, 2) if flow_start_deg is not None else None,
    }

    # ---- Tier 2: .cas.h5 Mesh Blade Geometry ----
    if cas_info is None and folder and os.path.isdir(folder):
        try:
            import glob
            from vawt_post.engine.hdf5_case import inspect_hdf5_case

            cas_files = sorted(glob.glob(os.path.join(folder, "*.cas.h5")), key=os.path.getmtime, reverse=True)
            for cf in cas_files:
                ci = inspect_hdf5_case(cf)
                if ci.is_valid and ci.blade_centroids:
                    cas_info = ci
                    break
        except Exception:
            pass

    mesh_angle = _extract_cas_blade1_angle(cas_info, rot_dir)
    mesh_vs_t0: Optional[float] = None
    mesh_vs_tend: Optional[float] = None
    if mesh_angle is not None:
        signals["mesh_angle_deg"] = round(mesh_angle, 2)
        if t0 is not None and t_end is not None and omega > 0:
            az_t0 = wrap_deg_360(az_base + math.degrees(omega * t0))
            az_tend = wrap_deg_360(az_base + math.degrees(omega * t_end))
            mesh_vs_t0 = wrap_deg_signed(mesh_angle - az_t0)
            mesh_vs_tend = wrap_deg_signed(mesh_angle - az_tend)
        else:
            mesh_vs_t0 = wrap_deg_signed(mesh_angle - az_base)
        signals["mesh_vs_t0_deg"] = round(mesh_vs_t0, 2)
        if mesh_vs_tend is not None:
            signals["mesh_vs_tend_deg"] = round(mesh_vs_tend, 2)

    # ---- Tier 3: Aerodynamic Torque Signature (requires >= ~1 rev) ----
    if omega > 0 and Trev > 0 and t_arr is not None and len(t_arr) >= 24 and t_span >= 0.95 * Trev:
        # Analyze latest trailing complete revolutions (up to 4 revs)
        n_full = max(1, int(math.floor(t_span / Trev + 1e-6)))
        n_win = min(4, n_full)
        t_win_start = float(t_arr[-1]) - n_win * Trev

        if b1_arr is not None and len(b1_arr) == len(t_arr):
            valid_pair = np.isfinite(t_arr) & np.isfinite(b1_arr)
            if int(np.sum(valid_pair)) >= 24:
                tp = t_arr[valid_pair]
                vp = sign * b1_arr[valid_pair]
                win = tp >= t_win_start - 1e-9
                if int(np.sum(win)) >= 24:
                    th_cur = np.mod(az_base + np.degrees(omega * tp[win]), 360.0)
                    stats = _bin_1p_phase(th_cur, vp[win])
                    if stats is not None:
                        signals.update(
                            {
                                "b1_r1": round(stats["r1"], 3),
                                "b1_theta_1p_deg": round(stats["theta_1p"], 2),
                                "b1_theta_peak_deg": round(stats["theta_peak"], 2),
                                "b1_drag0_ratio": round(stats["drag0_ratio"], 3),
                            }
                        )
                        if stats["r1"] >= 0.25:
                            err_deg = wrap_deg_signed(90.0 - stats["theta_1p"])
                            rec_th0 = wrap_deg_360(entered_theta0 + err_deg)
                            # Snap very near-zero recommendations cleanly to 0.0
                            if abs(wrap_deg_signed(rec_th0)) <= 2.0:
                                rec_th0 = 0.0
                            conf = "high" if stats["r1"] >= 0.45 else "medium"
                            signals["shift_error_deg"] = round(err_deg, 2)

                            # Guard against high-TSR / shed-wake aerodynamic lag tripping false MEDIUM mid-spin alerts
                            # when .cas.h5 mesh geometry already confirms t0 alignment or raw torque peak sits near 90 deg
                            mesh_confirms_t0 = (
                                not flow_mid_run
                                and mesh_vs_t0 is not None
                                and abs(mesh_vs_t0) <= 10.0
                            )
                            peak_err_deg = abs(wrap_deg_signed(90.0 - stats["theta_peak"]))
                            small_aero_lag = (
                                abs(err_deg) <= 40.0
                                and peak_err_deg <= 22.0
                                and stats["drag0_ratio"] <= 0.25
                            )
                            aero_lag_consistent = small_aero_lag and (stats["r1"] < 0.45 or mesh_confirms_t0)
                            needs_midspin_corr = (
                                abs(err_deg) > tolerance_deg
                                and not (mesh_confirms_t0 and conf != "high")
                                and not aero_lag_consistent
                            )

                            if needs_midspin_corr:
                                flow_note = (
                                    f" File begins at t0={t0:.4f}s (step {step0})"
                                    if (flow_mid_run and t0 is not None)
                                    else ""
                                )
                                return StartAzimuthResult(
                                    started_at_zero=False,
                                    is_mid_spin=True,
                                    needs_correction=True,
                                    mid_spin_compensated_by_flow_time=False,
                                    entered_theta0_deg=round(entered_theta0, 2),
                                    recommended_theta0_deg=round(rec_th0, 1),
                                    flow_start_angle_deg=round(flow_start_deg, 1) if flow_start_deg is not None else None,
                                    shift_error_deg=round(err_deg, 1),
                                    method="blade1_torque_phase",
                                    confidence=conf,
                                    summary=f"Mid-spin offset detected (~{rec_th0:.1f} deg suggested vs entered {entered_theta0:.1f} deg)",
                                    detail=(
                                        f"Blade 1 upwind torque harmonic (1P) peaks at computed theta={stats['theta_1p']:.1f} deg "
                                        f"(raw peak {stats['theta_peak']:.1f} deg) instead of expected ~90 deg, indicating an offset "
                                        f"of {err_deg:+.1f} deg.{flow_note} Adjusting Blade 1 start azimuth theta0 to "
                                        f"{rec_th0:.1f} deg restores 0 deg = into-wind alignment."
                                    ),
                                    signals=signals,
                                )

                            # Aligned with current theta0!
                            if flow_mid_run and flow_start_deg is not None:
                                return StartAzimuthResult(
                                    started_at_zero=False,
                                    is_mid_spin=True,
                                    needs_correction=False,
                                    mid_spin_compensated_by_flow_time=True,
                                    entered_theta0_deg=round(entered_theta0, 2),
                                    recommended_theta0_deg=round(entered_theta0, 1),
                                    flow_start_angle_deg=round(flow_start_deg, 1),
                                    shift_error_deg=round(err_deg, 1),
                                    method="flow_time",
                                    confidence=conf,
                                    summary=f"Mid-run start at t0={t0:.4f}s (theta~{flow_start_deg:.1f} deg) — flow-time aligned",
                                    detail=(
                                        f"Monitor recording started mid-run at step {step0 if step0 is not None else '?'} "
                                        f"(t0={t0:.4f}s, rotor angle ~{flow_start_deg:.1f} deg). Continuous flow-time preserves phase "
                                        f"(1P centroid theta={stats['theta_1p']:.1f} deg aligns with expected ~90 deg; keep theta0={entered_theta0:.1f} deg)."
                                    ),
                                    signals=signals,
                                )

                            if abs(wrap_deg_signed(entered_theta0)) > 5.0:
                                return StartAzimuthResult(
                                    started_at_zero=False,
                                    is_mid_spin=True,
                                    needs_correction=False,
                                    mid_spin_compensated_by_flow_time=False,
                                    entered_theta0_deg=round(entered_theta0, 2),
                                    recommended_theta0_deg=round(entered_theta0, 1),
                                    flow_start_angle_deg=round(flow_start_deg, 1) if flow_start_deg is not None else None,
                                    shift_error_deg=round(err_deg, 1),
                                    method="blade1_torque_phase",
                                    confidence=conf,
                                    summary=f"Mid-spin theta0={entered_theta0:.1f} deg verified",
                                    detail=(
                                        f"Entered theta0={entered_theta0:.1f} deg aligns Blade 1 upwind torque harmonic at "
                                        f"theta={stats['theta_1p']:.1f} deg (within {abs(err_deg):.1f} deg of nominal 90 deg)."
                                    ),
                                    signals=signals,
                                )

                            return StartAzimuthResult(
                                started_at_zero=True,
                                is_mid_spin=False,
                                needs_correction=False,
                                mid_spin_compensated_by_flow_time=False,
                                entered_theta0_deg=round(entered_theta0, 2),
                                recommended_theta0_deg=round(entered_theta0, 1),
                                flow_start_angle_deg=round(flow_start_deg, 1) if flow_start_deg is not None else None,
                                shift_error_deg=round(err_deg, 1),
                                method="aligned",
                                confidence=conf,
                                summary=f"0 deg start verified (1P centroid theta={stats['theta_1p']:.1f} deg)",
                                detail=(
                                    f"Blade 1 upwind torque harmonic aligns at theta={stats['theta_1p']:.1f} deg "
                                    f"with drag-minimum near 0/180 deg, consistent with theta0={entered_theta0:.1f} deg."
                                ),
                                signals=signals,
                            )

    # ---- Early Flow-Time / Mesh Fallback (less than 1 full rev or missing Blade 1 moment) ----
    if flow_mid_run and t0 is not None and flow_start_deg is not None:
        sym_note = ""
        if m_b1 is None and m_total is not None:
            nb = max(1, int(getattr(p, "n_blades", 3) or 3))
            sym_deg = 360.0 / nb
            sym_note = f" (No blade 1 file: total moment alone has {sym_deg:.0f} deg blade-symmetry)."
        return StartAzimuthResult(
            started_at_zero=False,
            is_mid_spin=True,
            needs_correction=False,
            mid_spin_compensated_by_flow_time=True,
            entered_theta0_deg=round(entered_theta0, 2),
            recommended_theta0_deg=round(entered_theta0, 1),
            flow_start_angle_deg=round(flow_start_deg, 1),
            shift_error_deg=0.0,
            method="flow_time",
            confidence="high",
            summary=f"Mid-run start at t0={t0:.4f}s (theta~{flow_start_deg:.1f} deg) — flow-time compensated",
            detail=(
                f"Monitor file starts at step {step0 if step0 is not None else '?'}, t0={t0:.4f}s "
                f"(rotor ~{flow_start_deg:.1f} deg). Continuous flow-time advances azimuth automatically{sym_note}"
            ),
            signals=signals,
        )

    # Tier 2 mesh geometry check when flow_time started near 0 and .cas.h5 wasn't simply saved at run end
    if (
        mesh_angle is not None
        and mesh_vs_t0 is not None
        and abs(mesh_vs_t0) > tolerance_deg
        and (mesh_vs_tend is None or abs(mesh_vs_tend) > 15.0)
    ):
        rec_mesh = wrap_deg_360(mesh_angle - compute_az_base_without_theta0(p))
        if abs(wrap_deg_signed(rec_mesh)) <= 2.0:
            rec_mesh = 0.0
        return StartAzimuthResult(
            started_at_zero=False,
            is_mid_spin=True,
            needs_correction=True,
            mid_spin_compensated_by_flow_time=False,
            entered_theta0_deg=round(entered_theta0, 2),
            recommended_theta0_deg=round(rec_mesh, 1),
            flow_start_angle_deg=round(flow_start_deg, 1) if flow_start_deg is not None else None,
            shift_error_deg=round(mesh_vs_t0, 1),
            method="mesh_geometry",
            confidence="medium",
            summary=f"Mesh geometry indicates Blade 1 at {mesh_angle:.1f} deg (vs {entered_theta0:.1f} deg)",
            detail=(
                f"Blade 1 mesh nodes in .cas.h5 sit at {mesh_angle:.1f} deg from into-wind 0 deg "
                f"while monitor starts at t0~0s. Set theta0={rec_mesh:.1f} deg if mesh began rotated."
            ),
            signals=signals,
        )

    # Check if total-moment-only data spans >= 1 rev without flow_mid_run (modulo symmetry note)
    if m_b1 is None and m_total is not None and omega > 0 and Trev > 0 and t_span >= 0.95 * Trev:
        nb = max(1, int(getattr(p, "n_blades", 3) or 3))
        sym_deg = 360.0 / nb
        return StartAzimuthResult(
            started_at_zero=None,
            is_mid_spin=False,
            needs_correction=False,
            mid_spin_compensated_by_flow_time=False,
            entered_theta0_deg=round(entered_theta0, 2),
            recommended_theta0_deg=None,
            flow_start_angle_deg=round(flow_start_deg, 1) if flow_start_deg is not None else None,
            shift_error_deg=None,
            method="total_torque_modulo",
            confidence="low",
            summary=f"Blade 1 monitor missing — only {sym_deg:.0f} deg cyclic symmetry check possible",
            detail=(
                f"Total moment alone is {sym_deg:.0f} deg periodic across {nb} blades; add Blade 1 moment monitor "
                "or .cas.h5 to distinguish individual blade start azimuth."
            ),
            signals=signals,
        )

    return StartAzimuthResult(
        started_at_zero=None,
        is_mid_spin=False,
        needs_correction=False,
        mid_spin_compensated_by_flow_time=False,
        entered_theta0_deg=round(entered_theta0, 2),
        recommended_theta0_deg=None,
        flow_start_angle_deg=round(flow_start_deg, 1) if flow_start_deg is not None else None,
        shift_error_deg=None,
        method="insufficient_data",
        confidence="low",
        summary="Awaiting >= 1 complete revolution to verify start azimuth...",
        detail="Fewer than 1 full revolution recorded so far; full aerodynamic phase verification runs once Rev 1 completes.",
        signals=signals,
    )
