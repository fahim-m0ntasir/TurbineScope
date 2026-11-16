# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Numerical Analysis & Revolution Processing
===================================================================
Post-processes aligned monitor time series to compute coefficients (Cp, Cm, Ct, Cy),
blade-resolved kinematics, trailing stability, azimuth binning, and spectra.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import math
import os
from dataclasses import asdict
from typing import Callable, Dict, List, Optional, Tuple, Any

import numpy as np

from vawt_post.engine.config import Params, InputFiles, STAGE_DT_CHANGE_REL_TOL, parse_custom_revs_spec
from vawt_post.engine.parser import read_fluent_report, align_series, compute_file_sha256


def window_mean(t: np.ndarray, v: np.ndarray, a: float, b: float) -> float:
    """Time-weighted mean of v over [a, b] using trapezoidal integration with interpolated endpoints."""
    if b <= a:
        return float("nan")
    m = (t > a) & (t < b)
    tt = np.concatenate([[a], t[m], [b]])
    vv = np.concatenate([[np.interp(a, t, v)], v[m], [np.interp(b, t, v)]])
    return float(np.sum((tt[1:] - tt[:-1]) * (vv[1:] + vv[:-1]) / 2.0) / (b - a))


def bin_azimuth(theta: np.ndarray, values: np.ndarray, bw: float) -> Tuple[np.ndarray, np.ndarray]:
    """Average values into azimuth bins of width bw (deg); empty bins filled by periodic linear interpolation."""
    nb = round(360.0 / bw)
    idx = np.minimum((np.mod(theta, 360.0) / bw).astype(int), nb - 1)
    sums = np.bincount(idx, weights=values, minlength=nb)
    cnt = np.bincount(idx, minlength=nb)
    out = np.full(nb, np.nan)
    ok = cnt > 0
    out[ok] = sums[ok] / cnt[ok]
    if (~ok).any() and ok.sum() >= 2:
        centers = (np.arange(nb) + 0.5) * bw
        out[~ok] = np.interp(centers[~ok], centers[ok], out[ok], period=360.0)
    return out, cnt


def detect_offsets(
    t: np.ndarray,
    moments: List[np.ndarray],
    omega: float,
    n_blades: int,
    log: Callable[[str], None] = print
) -> Tuple[List[float], float]:
    """Detect blade azimuth offsets relative to blade 1 by circular cross-correlation.

    Lag sign convention (Bug L10 fix):
      Circular cross-correlation computes cc[lag] = sum_t (base_zero[t] * x_zero[t + lag]).
      The peak lag index satisfies x(t) ~ base(t - lag).
      In physical coordinates, a blade trailing blade 1 by time lag has orbital offset
      off = (-lag * 360.0 / 720.0) % 360.0.
      Works for both CCW and CW rotations and symmetric N = 1, 2, 3, 4 blades.
    """
    even = [360.0 * i / n_blades for i in range(n_blades)]
    if len(moments) < 2:
        return even, 1.0
    try:
        Trev = 2.0 * math.pi / omega
        a, b = t[-1] - Trev, t[-1]
        grid = np.linspace(a, b, 720, endpoint=False)
        base = np.interp(grid, t, moments[0])
        base_zero = base - base.mean()
        base_norm = np.sqrt(np.sum(base_zero ** 2))
        offs = [0.0]
        step = 360.0 / n_blades
        min_peak_corr = 1.0
        for idx_b, m in enumerate(moments[1:], start=2):
            x = np.interp(grid, t, m)
            x_zero = x - x.mean()
            x_norm = np.sqrt(np.sum(x_zero ** 2))
            cc = np.fft.irfft(np.conj(np.fft.rfft(base_zero)) * np.fft.rfft(x_zero), 720)
            max_cc = float(np.max(cc))
            denom = base_norm * x_norm if (base_norm > 1e-12 and x_norm > 1e-12) else 1.0
            peak_corr = max_cc / denom
            min_peak_corr = min(min_peak_corr, peak_corr)
            if peak_corr < 0.90:
                log(f"Blade {idx_b} cross-correlation peak is weak (r = {peak_corr:.3f} < 0.90); phasing may be uncertain.")
            lag = int(np.argmax(cc))  # x(t) ~ base(t - lag)
            off = (-lag * 360.0 / 720.0) % 360.0
            offs.append((round(off / step) * step) % 360.0)

        if len(set(round(o, 3) for o in offs)) != len(offs):
            log("Blade phasing detection was ambiguous; using even geometric spacing.")
            return even, min_peak_corr
        return offs, min_peak_corr
    except Exception as exc:
        log(f"Blade phasing detection failed ({exc}); falling back to even spacing.")
        return even, 0.0


def detect_stages(step: np.ndarray, t: np.ndarray, Trev: float, omega: float) -> List[Dict[str, Any]]:
    """Detects sustained simulation time-step stages using STAGE_DT_CHANGE_REL_TOL, ignoring isolated gaps (L8)."""
    dts = np.diff(t)
    phases = []
    if len(dts) == 0:
        return phases
    start = 0
    i = 1
    tol = STAGE_DT_CHANGE_REL_TOL  # Unified 2% threshold from config (Bug L8)
    while i <= len(dts):
        is_end = (i == len(dts))
        if not is_end:
            cur_dt = dts[start]
            rel_diff = abs(dts[i] - cur_dt) / max(abs(cur_dt), 1e-12)
            if rel_diff > tol:
                # Check if this change is sustained or an isolated step glitch / single-step gap
                if i + 3 <= len(dts):
                    next_diffs = [abs(dts[k] - cur_dt) / max(abs(cur_dt), 1e-12) for k in range(i + 1, min(i + 4, len(dts)))]
                    if any(nd < tol for nd in next_diffs):
                        # Reverts back immediately: isolated step gap, not a new sustained stage
                        i += 1
                        continue
            else:
                i += 1
                continue

        d = float(np.mean(dts[start:i]))
        n_st = i - start
        dur = float(t[i] - t[start])
        steps_per_rev = Trev / d if d > 0 else 0.0
        phases.append({
            "phase_index": len(phases) + 1,
            "step_from": int(step[start]),
            "step_to": int(step[i]),
            "t_from": float(t[start]),
            "t_to": float(t[i]),
            "n_steps": n_st,
            "duration_s": dur,
            "revolutions": dur / Trev if Trev > 0 else 0.0,
            "dt": d,
            "deg_per_step": math.degrees(omega * d),
            "steps_per_rev": steps_per_rev,
        })
        start = i
        i += 1
    return phases


def aitken_extrapolation(x0: float, x1: float, x2: float, tol_rel: float = 0.005) -> Dict[str, Any]:
    """Evaluates Aitken delta-squared extrapolation with 0 < r < 0.9 requirement and capping."""
    d1 = x1 - x0
    d2 = x2 - x1
    denom = d1 - d2
    is_reliable = False
    cp_asymptotic = None
    extra_revs_needed = None
    r_ratio = None

    if abs(denom) > 1e-12 and abs(d2) < abs(d1) and (d1 * d2 > 0):
        r_ratio = float(d2 / d1)
        if 0.0 < r_ratio < 0.90:
            is_reliable = True
            raw_corr = (d2 ** 2) / denom
            max_corr = 5.0 * abs(d2)
            corr = math.copysign(min(abs(raw_corr), max_corr), raw_corr)
            cp_asymptotic = float(x2 + corr)
            if abs(d2) > tol_rel * max(abs(x2), 1e-6):
                extra_revs_needed = math.ceil(math.log((tol_rel * max(abs(x2), 1e-6)) / abs(d2)) / math.log(r_ratio))
                extra_revs_needed = max(0, extra_revs_needed)
            else:
                extra_revs_needed = 0

    return {
        "is_reliable": is_reliable,
        "cp_asymptotic": cp_asymptotic,
        "r_ratio": r_ratio,
        "extra_revs_needed": extra_revs_needed
    }


def fit_exponential_decay(cp_values: Any) -> Dict[str, Any]:
    """Fits y(k) = y_inf + A * exp(-beta * k) across trailing revolutions."""
    y = np.asarray(cp_values, dtype=float)
    asymptotic_limit = None
    uncertainty = None
    if len(y) >= 4:
        try:
            n_fit = min(len(y), 5)
            y_tail = y[-n_fit:]
            k_tail = np.arange(n_fit, dtype=float)
            diffs = np.diff(y_tail)
            if (np.all(diffs > 0) or np.all(diffs < 0)) and len(diffs) >= 3:
                abs_diffs = np.abs(diffs)
                log_d = np.log(np.maximum(abs_diffs, 1e-12))
                k_d = k_tail[:-1]
                slope, intercept = np.polyfit(k_d, log_d, 1)
                beta = -slope
                if beta > 0.05:
                    denom_exp = math.exp(beta) - 1.0
                    if denom_exp > 1e-4:
                        asymptotic_limit = float(y_tail[-1] + diffs[-1] / denom_exp)
                        pred_diffs = np.exp(intercept - beta * k_d)
                        resids = abs_diffs - pred_diffs
                        uncertainty = float(np.std(resids) / max(denom_exp, 1e-3))
        except Exception:
            pass
    return {
        "asymptotic_limit": asymptotic_limit,
        "uncertainty": uncertainty
    }


def analyze(
    p: Params,
    f: InputFiles,
    log: Callable[[str], None] = print
) -> Dict[str, Any]:
    """Execute complete numerical analysis on Fluent monitor files."""
    validation_errors = p.validate()
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    assert p.n_blades is not None and p.R is not None and p.U is not None

    warnings: List[str] = []
    log("Running file-health diagnostics ...")
    from vawt_post.engine.diagnostics import run_file_health_diagnostics
    diag_report = run_file_health_diagnostics(p, f)
    if diag_report.has_errors:
        err_msgs = [f"[{x.check} in {x.file}]: {x.explanation}" for x in diag_report.findings if x.severity == "ERROR"]
        warnings.append(f"Diagnostics: {len(err_msgs)} critical file-health error(s) detected ({'; '.join(err_msgs[:2])}).")

    log("Reading monitor files ...")

    series: Dict[str, Dict[str, Any]] = {}
    if f.total:
        series["total"] = read_fluent_report(f.total)

    blade_paths = [b for b in f.blades if b]
    have_blades = len(blade_paths) == p.n_blades and p.n_blades > 0
    if blade_paths and not have_blades:
        warnings.append(
            f"{len(blade_paths)} blade files provided for {p.n_blades} blades: per-blade analysis skipped."
        )

    if have_blades:
        for i, path in enumerate(blade_paths):
            series[f"blade{i + 1}"] = read_fluent_report(path)

    if f.shaft:
        series["shaft"] = read_fluent_report(f.shaft)
    if f.fx:
        series["fx"] = read_fluent_report(f.fx)
    if f.fy:
        series["fy"] = read_fluent_report(f.fy)
    if getattr(f, "fx_shaft", None) and os.path.isfile(str(f.fx_shaft)):
        series["fx_shaft"] = read_fluent_report(str(f.fx_shaft))
    if getattr(f, "fy_shaft", None) and os.path.isfile(str(f.fy_shaft)):
        series["fy_shaft"] = read_fluent_report(str(f.fy_shaft))

    if "total" not in series and not have_blades:
        raise ValueError("Please provide at least the total moment file, or all individual blade moment files.")

    continuity_report: Optional[Dict[str, Any]] = None
    prior_src = getattr(f, "prior_source", None)
    cont_mode = getattr(f, "continuity_mode", "auto") or "auto"
    if prior_src and cont_mode != "off":
        from vawt_post.engine.continuity import stitch_and_recover_all

        series, continuity_report = stitch_and_recover_all(
            series,
            prior_source=prior_src,
            p=p,
            mode=cont_mode,
            current_files=f,
            log=log,
        )
        c_sum = continuity_report.get("summary_text", "")
        if continuity_report.get("status") == "gap_recovered":
            warnings.append(f"Run continuity recovery applied: {c_sum}")
            rep_note = f"Recovered {continuity_report.get('recovered_count', 0)} missing timestep(s) from prior backup ({continuity_report.get('method', '')})"
            if rep_note not in p.applied_repairs:
                p.applied_repairs.append(rep_note)
        elif continuity_report.get("status") in ("overlap_divergent", "no_match", "gap_unfilled"):
            warnings.append(f"Run continuity notice: {c_sum}")

    step, t, S = align_series(series, p.dt)
    n = len(t)
    log(f"Aligned {n} common time steps, flow time range: {t[0]:.5f} to {t[-1]:.5f} s")

    # Compute aligned recovered step mask (True for synthetically bridged gap steps)
    recovered_mask = np.zeros(n, dtype=bool)
    if continuity_report and continuity_report.get("recovered_count", 0) > 0:
        for s_item in series.values():
            rm = s_item.get("recovered_mask")
            if rm is not None and np.any(rm):
                rec_steps = set(np.asarray(s_item["step"], dtype=np.int64)[rm].tolist())
                if rec_steps:
                    recovered_mask |= np.isin(step, list(rec_steps))

    # Guard against NaN/Inf in aligned series so analysis/plotting pipeline doesn't crash
    for k in list(S.keys()):
        arr_k = S[k]
        bad_mask = np.isnan(arr_k) | np.isinf(arr_k)
        if np.any(bad_mask):
            warnings.append(f"Series '{k}' contains {int(np.sum(bad_mask))} NaN/Inf values; sanitized via interpolation.")
            finite_mask = ~bad_mask
            if np.any(finite_mask):
                S[k] = np.interp(np.arange(len(arr_k)), np.where(finite_mask)[0], arr_k[finite_mask])
            else:
                S[k] = np.zeros_like(arr_k)

    omega = p.omega_val()
    lam = p.tsr_val()
    Trev = 2.0 * math.pi / omega
    A = 2.0 * p.R * p.depth
    Pref = 0.5 * p.rho * (p.U ** 3) * A
    Fref = 0.5 * p.rho * (p.U ** 2) * A
    Mref = Fref * p.R

    # ---- Rotation direction & shaft handling (Tier 1.2 & 1.3) -------------------------
    # Moving mesh direction: "ccw" (+z / +mesh omega) or "cw" (-z / -mesh omega)
    dir_factor = -1.0 if getattr(p, "rotation_dir", "ccw") == "cw" else 1.0

    Mb = [S[f"blade{i + 1}"] for i in range(p.n_blades)] if have_blades else []
    have_total = "total" in S
    have_shaft = "shaft" in S

    if have_blades:
        # Tier 1.3: When blade files exist, rotor torque MUST use blade torque sum
        Mrot = np.sum(Mb, axis=0)
    else:
        # Total file only: if shaft file provided, subtract shaft
        if have_shaft:
            Mrot = S["total"] - S["shaft"]
            log("Shaft moment file provided: subtracted shaft torque from total moment.")
        else:
            Mrot = S["total"].copy()

    tail = slice(int(0.75 * n), n)

    # Moment sign handling: single precedence (direction -> fixed sign; auto-flip off by default)
    # Rotation direction determines fixed sign: CCW = +1, CW = -1.
    # If explicit "+1" or "-1" is chosen, apply it as manual override.
    # When "fixed" or "auto" is chosen, sign is fixed by direction (sign = dir_factor).
    # Auto-flip does not invert sign; instead, when mean Cp is negative, a warning is issued
    # so that CW plus auto never flips twice, and genuinely negative Cp cases preserve negative sign.
    if p.moment_sign in ("+1", "-1"):
        sign = float(p.moment_sign)
    else:
        sign = dir_factor
        tail_cp = omega * float(np.mean(sign * Mrot[tail])) / Pref
        if tail_cp < 0:
            warnings.append(
                f"Notice: Mean rotor Cp is negative ({tail_cp:.4f} < 0). "
                "If the turbine is expected to generate positive power, check the rotation direction setting (CCW vs CW) or moment monitor sign."
            )

    Mrot = sign * Mrot
    Mb = [sign * m for m in Mb]
    Mshaft = sign * S["shaft"] if have_shaft else None
    Fx = S["fx"] if "fx" in S else None
    Fy = S["fy"] if "fy" in S else None
    Fx_shaft = S["fx_shaft"] if "fx_shaft" in S else None
    Fy_shaft = S["fy_shaft"] if "fy_shaft" in S else None

    # Side-by-side Cp (blades only) vs Cp (total file) discrepancy check (Tier 1.3)
    cp_blades_val = None
    cp_total_val = None
    if have_blades:
        cp_blades_val = omega * float(np.mean(np.sum(Mb, axis=0)[tail])) / Pref
    elif have_shaft:
        cp_blades_val = omega * float(np.mean(Mrot[tail])) / Pref

    if have_total:
        cp_total_val = omega * float(np.mean(sign * S["total"][tail])) / Pref

    if cp_blades_val is not None and cp_total_val is not None:
        diff_pct = abs(cp_blades_val - cp_total_val) / max(abs(cp_blades_val), 1e-9) * 100.0
        if diff_pct > 0.5:
            warnings.append(
                f"Cp (blades only = {cp_blades_val:.4f}) and Cp (total file = {cp_total_val:.4f}) differ by {diff_pct:.2f}% (> 0.5%). "
                "Total moment file may include shaft drag or other non-blade surfaces."
            )

    # ---- Time-step phases detection --------------------------------------------------
    phases = detect_stages(step, t, Trev, omega)
    if len(phases) > 1:
        log(f"Multi-stage simulation auto-detected ({len(phases)} phases):")
        for ph in phases:
            log(
                f"  - Stage {ph['phase_index']}: steps {ph['step_from']} to {ph['step_to']} "
                f"({ph['n_steps']} steps, {ph['revolutions']:.2f} revs) | "
                f"dt = {ph['dt']:.6e} s | {ph['deg_per_step']:.3f} deg/step ({ph['steps_per_rev']:.1f} steps/rev)"
            )
        warnings.append(
            f"The time step changes during the run ({len(phases)} phases). "
            "Revolutions are evaluated on exact flow-time, so results remain valid."
        )

        # Check if final refined stage has fewer than N stable revolutions (Bug L9)
        final_phase = phases[-1]
        if final_phase["revolutions"] < p.n_stable:
            warnings.append(
                f"Final simulation stage ({final_phase['phase_index']}) has only {final_phase['revolutions']:.2f} revolutions "
                f"(< {p.n_stable} required stable revs). Results marked 'stage too short'."
            )
    elif phases:
        ph = phases[0]
        log(f"Single time-step phase: steps {ph['step_from']} to {ph['step_to']} | dt = {ph['dt']:.6e} s ({ph['deg_per_step']:.3f} deg/step)")

    for ph in phases:
        if abs(ph["steps_per_rev"] - round(ph["steps_per_rev"])) > 0.05:
            warnings.append(
                f"Phase from step {ph['step_from']}: {ph['steps_per_rev']:.2f} steps/rev "
                f"({ph['deg_per_step']:.4f} deg/step) is not a whole number; check the time-step size."
            )
            break

    # ---- Blade azimuths, phasing & conventions (Tier 1.6) ----------------------------
    manual_offs = p.blade_offsets_manual
    if manual_offs is not None and len(manual_offs) == p.n_blades:
        offs = [float(x) for x in manual_offs]
        min_peak_corr = 1.0
        log(f"Using manual blade azimuth offsets (deg): {', '.join(f'{o:.1f}' for o in offs)}")
    elif p.offsets == "auto" and have_blades:
        offs, min_peak_corr = detect_offsets(t, Mb, omega, p.n_blades, log)
        if min_peak_corr < 0.90:
            warnings.append(
                f"Blade cross-correlation peak is weak (r = {min_peak_corr:.2f} < 0.90). "
                "Blade signals may have strong cycle-to-cycle turbulence or non-uniform flow; verify detected blade offsets."
            )
    else:
        offs = [360.0 * i / p.n_blades for i in range(p.n_blades)]
        min_peak_corr = 1.0

    if have_blades:
        log(f"Blade azimuth offsets (deg): {', '.join(f'{o:.1f}' for o in offs)}")

    # θ = 0 is defined as the position where the blade moves directly into the wind:
    # Top for CCW rotation; bottom for CW rotation.
    from vawt_post.engine.azimuth_detect import compute_az_base, detect_start_azimuth

    az_base = compute_az_base(p)

    # Blade azimuth advances along direction of rotation from into-wind datum (0° -> 360°)
    th1 = np.mod(az_base + np.degrees(omega * t), 360.0)
    TH = [np.mod(th1 + o, 360.0) for o in offs] if have_blades else [th1]

    start_az_res = detect_start_azimuth(
        p,
        t=t,
        step=step,
        m_b1=(Mb[0] if have_blades else None),
        m_total=Mrot,
        apply_moment_sign=False,
    )
    if start_az_res.needs_correction and start_az_res.recommended_theta0_deg is not None and start_az_res.confidence in ("high", "medium"):
        log(f"Mid-spin / start-azimuth check: {start_az_res.summary}")
        warnings.append(
            f"Mid-spin / θ0 offset detected: {start_az_res.detail}"
        )
    elif start_az_res.mid_spin_compensated_by_flow_time:
        log(f"Start-azimuth check: {start_az_res.summary}")

    # ---- Dimensionless coefficients -------------------------------------------------
    Cp = omega * Mrot / Pref
    Cm = Mrot / Mref
    Cp_b = [omega * m / Pref for m in Mb]
    Cm_b = [m / Mref for m in Mb]
    Ct = Fx / Fref if Fx is not None else None
    Cy = Fy / Fref if Fy is not None else None
    Ct_shaft = Fx_shaft / Fref if Fx_shaft is not None else None
    Cy_shaft = Fy_shaft / Fref if Fy_shaft is not None else None

    # ---- Revolutions ----------------------------------------------------------------
    n_full = int(math.floor((t[-1] - t[0]) / Trev + 1e-9))
    if n_full < 2:
        raise ValueError(
            f"Only {n_full} complete revolution(s) in the dataset ({(t[-1] - t[0]) / Trev:.2f} revs). "
            "At least 2 complete revolutions are required for convergence analysis."
        )

    bw = p.bin_deg
    nb_bins = round(360.0 / bw)
    centers = (np.arange(nb_bins) + 0.5) * bw

    # Anchor complete revolutions forward from t[0] when the simulation starts at t ≈ 0
    # (so trailing partial revolutions in live/interrupted runs do not shift 0°->360° cycle boundaries),
    # or backward from t[-1] when the monitor file is a truncated tail starting well after t = 0.
    anchor_forward = (abs(float(t[0])) <= 0.02 * Trev) or (int(step[0]) <= 1)

    revs = []
    for k in range(1, n_full + 1):
        if anchor_forward:
            a = float(t[0]) + (k - 1) * Trev
        else:
            a = float(t[-1]) - (n_full - k + 1) * Trev
        b = a + Trev
        m_win = (t >= a) & (t <= b)
        cm_curve, _ = bin_azimuth(th1[m_win], Cm[m_win], bw)
        ct_w = window_mean(t, Ct, a, b) if Ct is not None else None
        cy_w = window_mean(t, Cy, a, b) if Cy is not None else None
        ct_sh_w = window_mean(t, Ct_shaft, a, b) if Ct_shaft is not None else None
        cy_sh_w = window_mean(t, Cy_shaft, a, b) if Cy_shaft is not None else None
        r: Dict[str, Any] = {
            "k": k,
            "a": a,
            "b": b,
            "elapsed": (a - t[0]) / Trev,
            "cp": window_mean(t, Cp, a, b),
            "m": window_mean(t, Mrot, a, b),
            "cm": window_mean(t, Cm, a, b),
            "ct": ct_w,
            "cy": cy_w,
            "ct_shaft": ct_sh_w,
            "cy_shaft": cy_sh_w,
            "ct_blades": (ct_w - ct_sh_w) if (ct_w is not None and ct_sh_w is not None) else None,
            "cy_blades": (cy_w - cy_sh_w) if (cy_w is not None and cy_sh_w is not None) else None,
            "cp_max": float(Cp[m_win].max()) if m_win.any() else float("nan"),
            "cp_min": float(Cp[m_win].min()) if m_win.any() else float("nan"),
            "cm_max": float(Cm[m_win].max()) if m_win.any() else float("nan"),
            "cm_min": float(Cm[m_win].min()) if m_win.any() else float("nan"),
            "peak_torque": float(Mrot[m_win].max()) if m_win.any() else float("nan"),
            "cm_curve": cm_curve,
        }
        if have_blades:
            bm = [window_mean(t, x, a, b) for x in Mb]
            r["blade_means"] = bm
            bm_mean = sum(bm) / len(bm) if bm else 0.0
            r["spread"] = (max(bm) - min(bm)) / abs(bm_mean) if abs(bm_mean) > 1e-12 else float("nan")
        revs.append(r)

    # Enrich each simulation stage with its revolution range and converged Cp breakdown
    for ph in phases:
        ph_revs = [
            r for r in revs
            if r["a"] >= ph.get("t_from", 0.0) - 1e-4 and r["b"] <= ph.get("t_to", 0.0) + 1e-4
        ]
        ph["rev_from"] = ph_revs[0]["k"] if ph_revs else None
        ph["rev_to"] = ph_revs[-1]["k"] if ph_revs else None
        ph["mean_cp"] = float(np.mean([r["cp"] for r in ph_revs])) if ph_revs else None
        n_stg_tail = min(max(p.n_stable, 1), len(ph_revs))
        ph["converged_cp"] = float(np.mean([r["cp"] for r in ph_revs[-n_stg_tail:]])) if ph_revs else None
        ph["converged_revs"] = [r["k"] for r in ph_revs[-n_stg_tail:]] if ph_revs else []

    # ---- Stronger multi-criteria stability test (Tier 1.4) ---------------------------
    tol_rel = p.stable_tol / 100.0
    tol_abs = getattr(p, "stable_tol_abs", 0.001)
    raw_drift = getattr(p, "drift_tol", None)
    tol_drift = (raw_drift if raw_drift is not None else 2.0 * p.stable_tol) / 100.0
    tol_periodicity = getattr(p, "periodicity_tol", 0.01)

    for i, r in enumerate(revs):
        if i == 0:
            r["dcp"] = None
            r["dcp_abs"] = None
            r["d_peak_torque"] = None
            r["dct"] = None
            r["periodicity_metric"] = 0.0
            r["drift_3rev"] = None
            r["is_stable_step"] = True
            continue

        r_prev = revs[i - 1]
        r["dcp"] = (r["cp"] - r_prev["cp"]) / r_prev["cp"] if abs(r_prev["cp"]) > 1e-12 else 0.0
        r["dcp_abs"] = abs(r["cp"] - r_prev["cp"])
        r["d_peak_torque"] = (r["peak_torque"] - r_prev["peak_torque"]) / r_prev["peak_torque"] if abs(r_prev["peak_torque"]) > 1e-12 else None
        r["dct"] = (r["ct"] - r_prev["ct"]) / r_prev["ct"] if (r["ct"] is not None and r_prev["ct"] is not None and abs(r_prev["ct"]) > 1e-12) else None

        # Waveform periodicity metric: RMS diff / peak-to-peak
        cm_diff = r["cm_curve"] - r_prev["cm_curve"]
        rms_diff = float(np.sqrt(np.mean(cm_diff ** 2)))
        ptp = float(np.ptp(r["cm_curve"]))
        r["periodicity_metric"] = rms_diff / max(ptp, 1e-6)

        # 3-revolution drift
        if i >= 2:
            r_prev2 = revs[i - 2]
            r["drift_3rev"] = abs(r["cp"] - r_prev2["cp"]) / max(abs(r_prev2["cp"]), 1e-12)
        else:
            r["drift_3rev"] = None

        cond1 = (abs(r["dcp"]) < tol_rel) or (r["dcp_abs"] < tol_abs)
        cond2 = r["periodicity_metric"] < tol_periodicity
        r["is_stable_step"] = bool(cond1 and cond2)

    # Determine trailing stable set (consecutive trailing transitions satisfying stability criteria;
    # k stable transitions correspond to k+1 plateau revolutions, or 0 if the final step is unstable)
    if n_full <= 1:
        stable_n = n_full
    else:
        stable_steps = 0
        for i in range(n_full - 1, 0, -1):
            if revs[i]["is_stable_step"]:
                stable_steps += 1
            else:
                break
        stable_n = (stable_steps + 1) if stable_steps > 0 else 0

    # Total drift over the last 3 revolutions (Tier 1.4)
    drift_last_3 = None
    drift_ok = True
    if n_full >= 3:
        drift_last_3 = abs(revs[-1]["cp"] - revs[-3]["cp"]) / max(abs(revs[-3]["cp"]), 1e-12)
        if drift_last_3 >= tol_drift:
            drift_ok = False
            warnings.append(
                f"Monotonic drift detected: total Cp change over the last 3 revolutions ({drift_last_3 * 100:.2f}%) "
                f"exceeds drift tolerance 2×tol ({tol_drift * 100:.2f}%)."
            )

    # Selection logic (default: trailing n_stable revolutions; optional: user-specified custom_revs)
    custom_rev_indices = parse_custom_revs_spec(getattr(p, "custom_revs", None))
    if custom_rev_indices:
        out_of_range = [k for k in custom_rev_indices if k < 1 or k > n_full]
        if out_of_range:
            raise ValueError(
                f"Requested specific revolution(s) {out_of_range} exceed the {n_full} complete revolution(s) "
                f"available in the dataset (valid range: 1 to {n_full})."
            )
        sel = [r for r in revs if r["k"] in custom_rev_indices]
        ns = len(sel)
        is_stable = (stable_n >= min(max(p.n_stable, 1), n_full)) and drift_ok
    else:
        if p.n_stable == 0:
            ns = stable_n if stable_n > 1 else min(2, n_full)
        else:
            ns = min(p.n_stable, n_full)

        is_stable = (stable_n >= (ns if p.n_stable > 0 else 2)) and drift_ok
        if not is_stable:
            warnings.append(
                f"Cp is NOT yet stable: only {stable_n} trailing revolution(s) satisfy stability criteria "
                f"(user requested {ns}). The last {ns} revolutions are reported anyway; run the CFD simulation longer for full convergence."
            )
        sel = revs[-ns:]

    for r in revs:
        r["selected"] = r in sel
        r["stable"] = r["k"] > n_full - stable_n

    # Compute convergence extrapolations & Period-2 check on full history BEFORE custom_revs isolation
    cp_aitken = None
    extra_revs_needed = None
    aitken_reliable = False
    cp_exp_fit = None
    cp_exp_uncertainty = None

    if n_full >= 3:
        aitken_res = aitken_extrapolation(revs[-3]["cp"], revs[-2]["cp"], revs[-1]["cp"], tol_rel)
        cp_aitken = aitken_res["cp_asymptotic"]
        aitken_reliable = aitken_res["is_reliable"]
        extra_revs_needed = aitken_res["extra_revs_needed"]

    if n_full >= 4:
        exp_res = fit_exponential_decay([r["cp"] for r in revs])
        cp_exp_fit = exp_res["asymptotic_limit"]
        cp_exp_uncertainty = exp_res["uncertainty"]

    has_period_2 = False
    period_2_ratio = 0.0
    try:
        f_rot = omega / (2.0 * math.pi)
        m_p2_tail = t >= (t[-1] - 4.0 * Trev)
        t_tail = t[m_p2_tail] if np.sum(m_p2_tail) > 10 else t
        m_tail = Mrot[m_p2_tail] if np.sum(m_p2_tail) > 10 else Mrot
        if len(t_tail) >= 16 and t_tail[-1] > t_tail[0]:
            grid_p2 = np.linspace(t_tail[0], t_tail[-1], min(len(t_tail), 4096))
            m_p2_interp = np.interp(grid_p2, t_tail, m_tail)
            m_p2_fft = np.abs(np.fft.rfft(m_p2_interp - m_p2_interp.mean()))
            freqs_p2 = np.fft.rfftfreq(len(grid_p2), (grid_p2[-1] - grid_p2[0]) / (len(grid_p2) - 1))
            idx_half = np.argmin(np.abs(freqs_p2 - 0.5 * f_rot))
            amp_half = float(m_p2_fft[idx_half])
            peak_amp = float(np.max(m_p2_fft)) if len(m_p2_fft) > 0 else 1.0
            period_2_ratio = amp_half / max(peak_amp, 1e-9)
            if period_2_ratio > 0.10:
                has_period_2 = True
                warnings.append(
                    f"Sub-harmonic period-2 oscillation detected: spectral amplitude at 0.5x rotor frequency is "
                    f"{period_2_ratio * 100:.1f}% of main peak. Metric averages may fluctuate between alternate revolutions."
                )
    except Exception:
        pass

    if custom_rev_indices:
        # Strict isolation for specific revolution(s): keep ONLY the selected revolution(s)
        revs = sel
        for r in revs:
            if r.get("dcp") is None:
                r["dcp"] = 0.0
        m_custom = np.zeros(len(t), dtype=bool)
        for r_c in sel:
            m_custom |= (t >= r_c["a"] - 1e-9) & (t <= r_c["b"] + 1e-9)
        if np.any(m_custom):
            step = step[m_custom]
            t = t[m_custom]
            th1 = th1[m_custom]
            TH = [x[m_custom] for x in TH]
            Mrot = Mrot[m_custom]
            Mb = [x[m_custom] for x in Mb]
            if Mshaft is not None:
                Mshaft = Mshaft[m_custom]
            if Fx is not None:
                Fx = Fx[m_custom]
            if Fy is not None:
                Fy = Fy[m_custom]
            if Fx_shaft is not None:
                Fx_shaft = Fx_shaft[m_custom]
            if Fy_shaft is not None:
                Fy_shaft = Fy_shaft[m_custom]
            Cp = Cp[m_custom]
            Cm = Cm[m_custom]
            Cp_b = [x[m_custom] for x in Cp_b]
            Cm_b = [x[m_custom] for x in Cm_b]
            if Ct is not None:
                Ct = Ct[m_custom]
            if Cy is not None:
                Cy = Cy[m_custom]
            if Ct_shaft is not None:
                Ct_shaft = Ct_shaft[m_custom]
            if Cy_shaft is not None:
                Cy_shaft = Cy_shaft[m_custom]
            S = {k: v[m_custom] for k, v in S.items()}
            n = len(t)
        n_full = len(sel)
        phases = [
            ph for ph in phases
            if ph.get("t_to", 0.0) >= sel[0]["a"] - 1e-4 and ph.get("t_from", 0.0) <= sel[-1]["b"] + 1e-4
        ]
        revs_stable = [r for r in sel if r["stable"]] or sel
        stable_n = sum(1 for r in sel if r["stable"])
        is_stable = (stable_n == len(sel)) and drift_ok
        if have_blades:
            cp_blades_val = float(np.mean([r["cp"] for r in sel]))
        elif have_shaft:
            cp_blades_val = float(np.mean([r["cp"] for r in sel]))
        if have_total and "total" in S:
            cp_total_val = float(np.mean([
                window_mean(t, omega * (sign * S["total"]) / Pref, r["a"], r["b"])
                for r in sel
            ]))
    else:
        revs_stable = revs[-stable_n:] if stable_n > 0 else sel

    # Compute stable set metrics explicitly
    cp_stable_mean = float(np.mean([r["cp"] for r in revs_stable]))
    cm_stable_mean = float(np.mean([r["cm"] for r in revs_stable]))
    torque_stable_mean = float(np.mean([r["m"] for r in revs_stable]))
    ct_stable_mean = float(np.mean([r["ct"] for r in revs_stable])) if Ct is not None else None
    cy_stable_mean = float(np.mean([r["cy"] for r in revs_stable])) if Cy is not None else None
    power_stable_W = cp_stable_mean * Pref
    stable_revs_k = [r["k"] for r in revs_stable]

    # Whole analysis (all completed revolutions) metrics
    cp_all_mean = float(np.mean([r["cp"] for r in revs]))
    cm_all_mean = float(np.mean([r["cm"] for r in revs]))
    torque_all_mean = float(np.mean([r["m"] for r in revs]))
    power_all_W = cp_all_mean * Pref
    ct_all_mean = float(np.mean([r["ct"] for r in revs])) if Ct is not None else None
    cy_all_mean = float(np.mean([r["cy"] for r in revs])) if Cy is not None else None

    if custom_rev_indices:
        log(f"{n_full} complete revolutions; trailing stable set = {stable_n} ({', '.join(f'rev {k}' for k in stable_revs_k)}); reporting custom selected revolution(s): {', '.join(str(r['k']) for r in sel)}.")
    else:
        log(f"{n_full} complete revolutions; trailing stable set = {stable_n} ({', '.join(f'rev {k}' for k in stable_revs_k)}); reporting last {ns}.")

    # ---- Azimuth-resolved results (reported revolutions) ----------------------------
    az: Dict[str, Any] = {"theta": centers, "rev": []}
    az_cnt_empty = 0
    az_cnt_total = 0

    for r in sel:
        m_win = (t >= r["a"]) & (t <= r["b"])
        d_rev: Dict[str, Any] = {"k": r["k"]}
        d_rev["cp"], cnt_cp = bin_azimuth(th1[m_win], Cp[m_win], bw)
        az_cnt_empty += int(np.sum(cnt_cp == 0))
        az_cnt_total += len(cnt_cp)
        d_rev["cm"], _ = bin_azimuth(th1[m_win], Cm[m_win], bw)
        if Ct is not None:
            d_rev["ct"], _ = bin_azimuth(th1[m_win], Ct[m_win], bw)
        if Cy is not None:
            d_rev["cy"], _ = bin_azimuth(th1[m_win], Cy[m_win], bw)
        if have_blades:
            d_rev["M_b"] = [bin_azimuth(TH[i][m_win], Mb[i][m_win], bw)[0] for i in range(p.n_blades)]
            d_rev["Cp_b"] = [omega * x / Pref for x in d_rev["M_b"]]
            d_rev["Cm_b"] = [x / Mref for x in d_rev["M_b"]]
        az["rev"].append(d_rev)

    az["cp_mean"] = np.mean([d_rev["cp"] for d_rev in az["rev"]], axis=0)
    az["cm_mean"] = np.mean([d_rev["cm"] for d_rev in az["rev"]], axis=0)
    if Ct is not None:
        az["ct_mean"] = np.mean([d_rev["ct"] for d_rev in az["rev"]], axis=0)
    if Cy is not None:
        az["cy_mean"] = np.mean([d_rev["cy"] for d_rev in az["rev"]], axis=0)

    if have_blades:
        az["M_b_mean"] = [np.mean([d_rev["M_b"][i] for d_rev in az["rev"]], axis=0) for i in range(p.n_blades)]
        az["M_avg"] = np.mean(az["M_b_mean"], axis=0)
        az["Cp_blade_eq"] = p.n_blades * omega * az["M_avg"] / Pref  # N x per-blade contribution
        az["Cm_blade_eq"] = p.n_blades * az["M_avg"] / Mref

    # Geometric kinematics versus azimuth
    th_r = np.radians(centers)
    az["alpha"] = np.degrees(np.arctan2(np.sin(th_r), np.cos(th_r) + lam))
    az["w_over_u"] = np.sqrt(1.0 + 2.0 * lam * np.cos(th_r) + lam ** 2)
    az["re_c"] = (p.rho * p.U * az["w_over_u"] * p.chord / p.mu) if p.chord else np.zeros_like(centers)

    # Sanity hint: drag-only at theta = 0 and 180 deg (Tier 1.6)
    if have_blades and "M_avg" in az:
        Mavg = az["M_avg"]
        idx_0 = np.argmin(np.abs(centers - 0.0))
        idx_180 = np.argmin(np.abs(centers - 180.0))
        m_at_0 = float(Mavg[idx_0])
        m_at_180 = float(Mavg[idx_180])
        m_peak = float(np.max(np.abs(Mavg)))
        if m_peak > 1e-6 and (abs(m_at_0) > 0.70 * m_peak or abs(m_at_180) > 0.70 * m_peak):
            warnings.append(
                "Sanity hint: At θ = 0° and 180°, geometric angle of attack is zero (drag-only region), "
                "but blade torque is near peak. Check whether the azimuth zero convention or rotation direction matches your CFD setup."
            )

    # ---- Ripple spectrum of Cp over the reported revolutions -----------------------
    nsel = len(sel)
    cpg = np.concatenate([
        np.interp(np.linspace(r["a"], r["b"], 1024, endpoint=False), t, Cp)
        for r in sel
    ])
    spec = np.abs(np.fft.rfft(cpg - cpg.mean())) * 2.0 / len(cpg)
    harm = np.arange(len(spec)) / nsel
    hm = (harm > 0.5) & (harm <= 15)
    dom = float(harm[hm][np.argmax(spec[hm])]) if hm.any() else float("nan")

    # ---- Summary KPI statistics ----------------------------------------------------
    cp_sel = [r["cp"] for r in sel]
    cp_max_val = max(r["cp_max"] for r in sel)
    cp_min_val = min(r["cp_min"] for r in sel)
    cm_mean_val = float(np.mean([r["cm"] for r in sel]))
    cm_min_val = min(r["cm_min"] for r in sel) if ("cm_min" in sel[0] and not math.isnan(sel[0]["cm_min"])) else (cp_min_val / lam if lam > 0 else 0.0)
    cm_max_val = max(r["cm_max"] for r in sel) if ("cm_max" in sel[0] and not math.isnan(sel[0]["cm_max"])) else (cp_max_val / lam if lam > 0 else 0.0)
    str_ratio = (cm_min_val / cm_mean_val) if cm_mean_val != 0 else float("nan")

    res: Dict[str, Any] = {
        # Unambiguous naming (Tier 1.4)
        "cp_reported_mean": float(np.mean(cp_sel)),
        "cp_trailing_stable_mean": cp_stable_mean,
        "cp_all_revolutions_mean": cp_all_mean,
        # Backward-compatibility aliases
        "cp_mean": float(np.mean(cp_sel)),
        "cp_stable_mean": cp_stable_mean,
        "cp_all_mean": cp_all_mean,
        "cp_extrapolated_aitken": cp_aitken,
        "cp_aitken_reliable": aitken_reliable,
        "cp_exp_fit": cp_exp_fit,
        "cp_exp_uncertainty": cp_exp_uncertainty,
        "extra_revs_to_converge": extra_revs_needed,
        "has_period_2": has_period_2,
        "period_2_ratio": period_2_ratio,
        "cp_blades_only": cp_blades_val,
        "cp_total_file": cp_total_val,
        "cp_each": cp_sel,
        "cp_std": float(np.std(cp_sel, ddof=1)) if len(cp_sel) > 1 else float("nan"),
        "cp_last_change": revs[-1]["dcp"],
        "stable_n": stable_n,
        "is_stable": is_stable,
        "cm_mean": cm_mean_val,
        "cm_min": cm_min_val,
        "cm_max": cm_max_val,
        "str": str_ratio,
        "is_self_starting": bool(cm_min_val > 0),
        "power_mean_W": float(np.mean(cp_sel)) * Pref,
        "torque_mean": float(np.mean([r["m"] for r in sel])),
        "cp_max": cp_max_val,
        "cp_min": cp_min_val,
        "ripple_pp": (cp_max_val - cp_min_val) / abs(float(np.mean(cp_sel))) if abs(float(np.mean(cp_sel))) > 1e-12 else float("nan"),
        "dominant_harmonic": dom,
        "ct_mean": float(np.mean([r["ct"] for r in sel])) if Ct is not None else None,
        "cy_mean": float(np.mean([r["cy"] for r in sel])) if Cy is not None else None,
        "ct_shaft_mean": float(np.mean([r["ct_shaft"] for r in sel if r.get("ct_shaft") is not None])) if Ct_shaft is not None else None,
        "cy_shaft_mean": float(np.mean([r["cy_shaft"] for r in sel if r.get("cy_shaft") is not None])) if Cy_shaft is not None else None,
        "ct_blades_only_mean": float(np.mean([r["ct_blades"] for r in sel if r.get("ct_blades") is not None])) if (Ct is not None and Ct_shaft is not None) else None,
        "cy_blades_only_mean": float(np.mean([r["cy_blades"] for r in sel if r.get("cy_blades") is not None])) if (Cy is not None and Cy_shaft is not None) else None,
        "custom_revs_used": str(p.custom_revs).strip() if custom_rev_indices else None,
        "selection_desc": f"Specific revolutions ({', '.join(str(r['k']) for r in sel)})" if custom_rev_indices else f"Last {len(sel)} revolutions ({', '.join(str(r['k']) for r in sel)})",
        "omega": omega,
        "tsr": lam,
        "Trev": Trev,
        "Pref": Pref,
        "Fref": Fref,
        "Mref": Mref,
        "A": A,
        "n_full": n_full,
        "n_steps": n,
        "t0": float(t[0]),
        "t1": float(t[-1]),
        "total_revs": float((t[-1] - t[0]) / Trev),
        "sign": sign,
        "offsets": offs if have_blades else None,
        "alpha_max": float(np.max(az["alpha"])),
        "alpha_max_theta": float(centers[np.argmax(az["alpha"])]),
        "phases": phases,
        "is_multistage": len(phases) > 1,
        "n_stages": len(phases),
        "start_azimuth": start_az_res.to_dict(),
        "cm_stable_mean": cm_stable_mean,
        "cm_all_mean": cm_all_mean,
        "torque_stable_mean": torque_stable_mean,
        "torque_all_mean": torque_all_mean,
        "power_stable_W": power_stable_W,
        "power_all_W": power_all_W,
        "ct_stable_mean": ct_stable_mean,
        "ct_all_mean": ct_all_mean,
        "cy_stable_mean": cy_stable_mean,
        "cy_all_mean": cy_all_mean,
        "stable_revs": stable_revs_k,
        "reported_revs": [r["k"] for r in sel],
        "span_H": float(p.span) if (p.span is not None and p.span > 0) else None,
        "swept_area_3d": float(2.0 * p.R * p.span) if (p.span is not None and p.span > 0 and p.R is not None) else None,
        "Pref_3d": float(0.5 * p.rho * (p.U ** 3) * (2.0 * p.R * p.span)) if (p.span is not None and p.span > 0 and p.R is not None and p.U is not None) else None,
        "power_3d_W": float(np.mean(cp_sel) * 0.5 * p.rho * (p.U ** 3) * (2.0 * p.R * p.span)) if (p.span is not None and p.span > 0 and p.R is not None and p.U is not None) else None,
        "torque_3d_Nm": float((np.mean(cp_sel) * 0.5 * p.rho * (p.U ** 3) * (2.0 * p.R * p.span)) / omega) if (p.span is not None and p.span > 0 and p.R is not None and p.U is not None and omega > 0) else None,
        "notice_3d": "3D estimate based on geometric scaling (swept area 2R×H). Tip vortex losses, strut/support-arm parasitic drag, and tower interference are NOT included." if (p.span is not None and p.span > 0) else None,
    }

    # Rotor speed BPF check and torque autocorrelation check (Tier 1.5 / Check 12)
    dominant_harmonic_ratio = 1.0
    if len(t) >= 100:
        try:
            f0 = omega / (2.0 * math.pi)
            f_bpf = p.n_blades * f0
            grid_n = min(len(t), 8192)
            t_grid = np.linspace(t[0], t[-1], grid_n)
            dt_grid = (t[-1] - t[0]) / (grid_n - 1)
            m_interp = np.interp(t_grid, t, Mrot)
            m_zero = m_interp - m_interp.mean()
            m_fft = np.abs(np.fft.rfft(m_zero))
            freqs = np.fft.rfftfreq(grid_n, dt_grid)

            f_band = (freqs >= 0.4 * f_bpf) & (freqs <= 3.5 * f_bpf)
            if f_band.any():
                f_peak_overall = float(freqs[f_band][np.argmax(m_fft[f_band])])
                k_harmonic = max(1, round(f_peak_overall / f_bpf))
                f_expected_k = k_harmonic * f_bpf
                dominant_harmonic_ratio = f_peak_overall / f_bpf
                res["detected_blade_freq"] = f_peak_overall
                res["detected_omega"] = 2.0 * math.pi * (f_peak_overall / k_harmonic) / max(1, p.n_blades)
                res["freq_match_ratio"] = dominant_harmonic_ratio
                res["harmonic_k"] = k_harmonic

                if abs(dominant_harmonic_ratio - 1.0) > 0.10:
                    warnings.append(
                        f"Rotor speed mismatch: Rotor speed implied by the data differs from the entered ω: "
                        f"dominant frequency ({f_peak_overall:.2f} Hz) = {dominant_harmonic_ratio:.2f}× expected blade-passing frequency ({f_bpf:.2f} Hz). "
                        f"Expected harmonic k={k_harmonic} is {f_expected_k:.2f} Hz. "
                        f"Note: This spectral check cannot detect an incorrect free-stream velocity U."
                    )

            if have_blades and len(Mb) > 0:
                mb1 = Mb[0]
                mb1_interp = np.interp(t_grid, t, mb1)
                mb1_zero = mb1_interp - mb1_interp.mean()
                mb1_fft = np.abs(np.fft.rfft(mb1_zero))
                fb_band = (freqs >= 0.4 * f0) & (freqs <= 2.2 * f0)
                if fb_band.any():
                    fb_peak = float(freqs[fb_band][np.argmax(mb1_fft[fb_band])])
                    rel_err_b = abs(fb_peak - f0) / f0
                    res["detected_blade_fundamental"] = fb_peak
                    if rel_err_b > 0.10:
                        if abs(fb_peak / f0 - 2.0) <= 0.10:
                            # 2.0x is physically normal for VAWT upwind/downwind cycle; record as normal
                            res["blade_dominant_harmonic"] = "2x (Normal upwind/downwind cycle)"
                        else:
                            warnings.append(
                                f"Blade fundamental frequency mismatch: Blade 1 dominant frequency ({fb_peak:.2f} Hz) "
                                f"differs from expected f0 ({f0:.2f} Hz, ratio = {fb_peak / f0:.2f}×). "
                                f"Note: This spectral check cannot detect an incorrect free-stream velocity U."
                            )

            # Autocorrelation period check
            norm_m = np.sum(m_zero ** 2)
            if norm_m > 1e-12:
                autocorr = np.correlate(m_zero, m_zero, mode="full")[len(m_zero) - 1:] / norm_m
                lags_t = np.arange(len(autocorr)) * dt_grid
                lag_win = (lags_t >= 0.75 * Trev) & (lags_t <= 1.25 * Trev)
                if lag_win.any():
                    idx_peak = np.argmax(autocorr[lag_win])
                    autocorr_period = float(lags_t[lag_win][idx_peak])
                    autocorr_error_pct = abs(autocorr_period - Trev) / Trev * 100.0
                    res["autocorr_period"] = autocorr_period
                    res["autocorr_error_pct"] = autocorr_error_pct
                    if autocorr_error_pct > 5.0:
                        warnings.append(
                            f"Rotor revolution period implied by torque autocorrelation ({autocorr_period:.4f} s) "
                            f"differs from theoretical 2π/ω ({Trev:.4f} s) by {autocorr_error_pct:.1f}%. Verify entered rotor speed ω."
                        )
        except Exception:
            pass

    if have_blades:
        Mavg = az["M_avg"]
        up = centers < 180
        down = ~up
        up_fin = np.isfinite(Mavg[up])
        dn_fin = np.isfinite(Mavg[down])
        all_fin = np.isfinite(Mavg)
        res["torque_peak_up"] = float(np.nanmax(Mavg[up])) if np.any(up_fin) else float("nan")
        res["torque_peak_up_theta"] = float(centers[up][np.nanargmax(Mavg[up])]) if np.any(up_fin) else float("nan")
        res["torque_max_down"] = float(np.nanmax(Mavg[down])) if np.any(dn_fin) else float("nan")
        res["torque_max_down_theta"] = float(centers[down][np.nanargmax(Mavg[down])]) if np.any(dn_fin) else float("nan")
        res["torque_min"] = float(np.nanmin(Mavg)) if np.any(all_fin) else float("nan")
        res["torque_min_theta"] = float(centers[np.nanargmin(Mavg)]) if np.any(all_fin) else float("nan")
        pos = np.nansum(Mavg[up]) / np.nansum(Mavg) if np.nansum(Mavg) != 0 else float("nan")
        res["upwind_share"] = float(pos)
        spreads = [r["spread"] for r in sel]
        res["blade_spread"] = float(np.nanmean(spreads)) if any(np.isfinite(spreads)) else float("nan")

    if Mshaft is not None:
        res["shaft_mean_moment"] = float(np.mean([window_mean(t, Mshaft, r["a"], r["b"]) for r in sel]))

    # ---- Comprehensive QA Checks (Tier 2) -------------------------------------------
    qa_table = run_qa_checks(
        p=p,
        f=f,
        raw_series=series,
        aligned_step=step,
        aligned_t=t,
        res=res,
        az_cnt_empty=az_cnt_empty,
        az_cnt_total=max(1, az_cnt_total)
    )
    res["qa_checks"] = qa_table

    for qa in qa_table:
        if qa["status"] in ("WARN", "FAIL"):
            warn_msg = f"QA Check [{qa['check']}]: {qa['detail']}"
            if not any(qa['check'] in w for w in warnings):
                warnings.append(warn_msg)

    res["continuity"] = continuity_report
    res["warnings"] = warnings

    # Collect mesh sizing/quality & Workbench setup metadata from the source directory (cached)
    mesh_info: Dict[str, Any] = {}
    try:
        sample_path = next(
            (pth for pth in [f.total, f.shaft, f.fx, f.fy, *f.blades] if pth and os.path.isfile(str(pth))),
            None,
        )
        if sample_path:
            from vawt_post.engine.mesh_inspector import inspect_workbench_setup_and_mesh
            cand_dir = os.path.dirname(os.path.abspath(str(sample_path)))
            mesh_info = inspect_workbench_setup_and_mesh(
                cand_dir,
                rotor_params={
                    "U": p.U,
                    "omega": omega,
                    "R": p.R,
                    "chord": p.chord,
                    "rho": p.rho,
                    "mu": p.mu,
                },
                compute_cell_quality=True,
            )
            if mesh_info.get("mesh", {}).get("available"):
                m_q = mesh_info["mesh"]
                if p.metadata.mesh_cells is None and m_q.get("cell_count"):
                    p.metadata.mesh_cells = int(m_q["cell_count"])
                dom = m_q.get("domain", {})
                if dom.get("length_m") and dom.get("width_m") and p.metadata.domain_size in (None, "30R x 20R", ""):
                    p.metadata.domain_size = f"{dom['length_m']:.2f} m x {dom['width_m']:.2f} m"
            st = mesh_info.get("setup", {})
            if not p.metadata.turb_model and st.get("turb_model"):
                p.metadata.turb_model = str(st["turb_model"])
            dt_fallback = p.dt or (float(np.median(np.diff(t))) if len(t) > 1 else None)
            ts_val = st.get("time_step") if st.get("time_step") is not None else dt_fallback
            if p.metadata.time_step_s is None and ts_val is not None:
                p.metadata.time_step_s = float(ts_val)
    except Exception:
        mesh_info = {}

    data = dict(
        step=step, t=t, th1=th1, TH=TH, Mrot=Mrot, Mb=Mb, Mshaft=Mshaft,
        Fx=Fx, Fy=Fy, Cp=Cp, Cm=Cm, Cp_b=Cp_b, Cm_b=Cm_b, Ct=Ct, Cy=Cy,
        raw=S, have_blades=have_blades,
        recovered_mask=recovered_mask,
        splice_times=(continuity_report.get("splice_times", []) if continuity_report else []),
        recovered_time_ranges=(continuity_report.get("recovered_time_ranges", []) if continuity_report else []),
    )

    return dict(
        p=p, files=f, data=data, revs=revs, sel=sel, az=az, res=res,
        spec=(harm, spec), warnings=warnings, qa_checks=qa_table,
        diagnostics=diag_report, mesh_info=mesh_info,
    )


def run_qa_checks(
    p: Params,
    f: InputFiles,
    raw_series: Dict[str, Dict[str, Any]],
    aligned_step: np.ndarray,
    aligned_t: np.ndarray,
    res: Dict[str, Any],
    az_cnt_empty: int = 0,
    az_cnt_total: int = 1,
) -> List[Dict[str, Any]]:
    """Execute complete numerical and setup data-quality checks (Tier 2).

    Returns a list of structured QA check dictionaries:
        {'check': str, 'status': 'PASS'|'WARN'|'FAIL', 'value': str, 'detail': str}
    """
    qa: List[Dict[str, Any]] = []

    # 1. Time Monotonicity
    is_monotonic = bool(np.all(np.diff(aligned_t) > 0)) if len(aligned_t) > 1 else True
    qa.append({
        "check": "Time monotonicity",
        "status": "PASS" if is_monotonic else "FAIL",
        "value": "Strictly monotonic" if is_monotonic else "Non-monotonic",
        "detail": "Flow time strictly advances forward." if is_monotonic else "Flow time is non-monotonic or contains backward steps."
    })

    # 2. Duplicate Steps
    dup_counts = {k: len(s["step"]) - len(np.unique(s["step"])) for k, s in raw_series.items() if "step" in s}
    total_dups = sum(dup_counts.values())
    qa.append({
        "check": "Duplicate steps",
        "status": "PASS" if total_dups == 0 else "WARN",
        "value": f"{total_dups} duplicates" if total_dups > 0 else "0 duplicates",
        "detail": "No duplicate steps in monitor files." if total_dups == 0 else f"Found duplicate steps in {', '.join(k for k, v in dup_counts.items() if v > 0)}; deduplicated by keeping last."
    })

    # 3. Step Continuity & Missing Steps
    step_diff = np.diff(aligned_step) if len(aligned_step) > 1 else np.array([1])
    max_gap = int(np.max(step_diff)) if len(step_diff) > 0 else 1
    has_gaps = bool(np.any(step_diff > 1))
    qa.append({
        "check": "Step continuity",
        "status": "PASS" if not has_gaps else "WARN",
        "value": "Continuous (Δstep=1)" if not has_gaps else f"Gaps detected (max gap={max_gap})",
        "detail": "Uniform time-step increment." if not has_gaps else f"Missing time steps detected; max gap between consecutive steps is {max_gap}."
    })

    # 4. Simulation Restarts / Step Decreases
    has_restart = any(bool(np.any(np.diff(s["step"]) < 0)) for s in raw_series.values() if "step" in s and len(s["step"]) > 1)
    qa.append({
        "check": "Simulation restarts",
        "status": "PASS" if not has_restart else "WARN",
        "value": "No step regressions" if not has_restart else "Restart detected",
        "detail": "Steps advance monotonically within all files." if not has_restart else "Negative step index decrement detected, indicating simulation restart."
    })

    # 5. File Length Consistency
    lengths = {k: len(s["step"]) for k, s in raw_series.items() if "step" in s}
    if lengths:
        min_l, max_l = min(lengths.values()), max(lengths.values())
        equal_len = (min_l == max_l)
        qa.append({
            "check": "File length consistency",
            "status": "PASS" if equal_len else "WARN",
            "value": f"Equal ({min_l} steps)" if equal_len else f"Mismatch ({min_l} to {max_l})",
            "detail": f"All {len(lengths)} input monitor files have identical lengths." if equal_len else f"Input files have unequal lengths ({min_l} to {max_l} steps); aligned on inner intersection."
        })

    # 6. Steps Per Revolution (Integer Alignment)
    try:
        omega = p.omega_val()
        Trev = 2.0 * math.pi / omega
        dt_nom = float(np.median(np.diff(aligned_t))) if len(aligned_t) > 1 else (p.dt or 1.0)
        steps_per_rev = Trev / dt_nom
        rem = abs(steps_per_rev - round(steps_per_rev))
        is_int = rem <= 0.05
        qa.append({
            "check": "Steps per revolution",
            "status": "PASS" if is_int else "WARN",
            "value": f"{steps_per_rev:.1f} steps/rev",
            "detail": "Steps per revolution is an exact integer." if is_int else f"Non-integer steps per revolution ({steps_per_rev:.2f}). Azimuthal discretisation may drift between revolutions."
        })
    except Exception:
        steps_per_rev = 0.0
        dt_nom = p.dt or 1.0

    # 7. Azimuth Step Resolution (degrees per step)
    try:
        omega = p.omega_val()
        deg_per_step = omega * dt_nom * 180.0 / math.pi
        if deg_per_step <= 1.0:
            status_deg = "PASS"
            det_deg = f"Δθ = {deg_per_step:.2f}° / step (<= 1.0° satisfies standard VAWT CFD guidelines)."
        elif deg_per_step <= 2.0:
            status_deg = "WARN"
            det_deg = f"Warning: Δθ = {deg_per_step:.2f}° / step exceeds 1.0°. Finer time step recommended."
        else:
            status_deg = "FAIL"
            det_deg = f"Strong Warning: Δθ = {deg_per_step:.2f}° / step exceeds 2.0°. Coarse time step may clip peak aerodynamic loads."
        qa.append({
            "check": "Azimuth step resolution",
            "status": status_deg,
            "value": f"{deg_per_step:.2f}° / step",
            "detail": det_deg
        })
    except Exception:
        pass

    # 8. Azimuth Bin Interpolation Share
    interp_share = (az_cnt_empty / az_cnt_total * 100.0) if az_cnt_total > 0 else 0.0
    if interp_share == 0.0:
        status_interp = "PASS"
        det_interp = "0.0% empty bins; all azimuth bins populated with CFD data points."
    elif interp_share <= 5.0:
        status_interp = "WARN"
        det_interp = f"Notice: {interp_share:.1f}% of azimuth bins were empty and interpolated."
    else:
        status_interp = "FAIL"
        det_interp = f"Strong Warning: {interp_share:.1f}% of azimuth bins had to be interpolated (> 5.0%). Time step is coarse relative to bin width {p.bin_deg}°."
    qa.append({
        "check": "Azimuth bin interpolation",
        "status": status_interp,
        "value": f"{interp_share:.1f}% empty bins",
        "detail": det_interp
    })

    # 9. Moment Center Alignment Confirmation
    confirmed = getattr(p.metadata, "moment_center_confirmed", True)
    qa.append({
        "check": "Moment center alignment",
        "status": "PASS" if confirmed else "WARN",
        "value": "Confirmed (0, 0)" if confirmed else "Unconfirmed",
        "detail": "User confirmed moment reference center matches rotor rotation axis." if confirmed else "Moment monitor center is not confirmed to match rotor axis; coordinate offset error possible."
    })

    # 10. Shaft / Blade Torque Consistency
    cp_b = res.get("cp_blades_only")
    cp_tot = res.get("cp_total_file")
    if cp_b is not None and cp_tot is not None and abs(cp_b) > 1e-6:
        disc = abs(cp_b - cp_tot) / abs(cp_b) * 100.0
        qa.append({
            "check": "Shaft / blade consistency",
            "status": "PASS" if disc <= 0.5 else "WARN",
            "value": f"{disc:.2f}% discrepancy",
            "detail": "Blade torque sum matches total moment file within 0.5%." if disc <= 0.5 else f"Blade torque sum differs from total moment file by {disc:.2f}% (> 0.5%). Check shaft subtraction or zone definitions."
        })
    else:
        qa.append({
            "check": "Shaft / blade consistency",
            "status": "PASS",
            "value": "N/A (Single source)",
            "detail": "Single moment data source applied."
        })

    # 11. Rotor Speed / BPF Dominant Frequency
    match_ratio = res.get("freq_match_ratio", 1.0)
    bpf_ok = abs(match_ratio - 1.0) <= 0.10
    qa.append({
        "check": "Rotor speed / BPF check",
        "status": "PASS" if bpf_ok else "WARN",
        "value": f"k = {match_ratio:.2f}× BPF",
        "detail": "Dominant torque frequency matches blade-passing frequency." if bpf_ok else f"Dominant torque frequency ({res.get('detected_blade_freq', 0):.2f} Hz) = {match_ratio:.2f}× expected BPF. Rotor speed mismatch."
    })

    # 12. Multi-Criteria Convergence
    is_stable = res.get("is_stable", False)
    qa.append({
        "check": "Multi-criteria convergence",
        "status": "PASS" if is_stable else "WARN",
        "value": "STABLE" if is_stable else "NOT YET STABLE",
        "detail": f"Trailing {res.get('stable_n', 0)} revolutions satisfied relative, absolute, drift, and periodicity criteria." if is_stable else "Simulation has not yet met all multi-criteria convergence thresholds across trailing revolutions."
    })

    saz = res.get("start_azimuth")
    if isinstance(saz, dict):
        if saz.get("needs_correction") and saz.get("recommended_theta0_deg") is not None:
            saz_status = "WARN"
            saz_val = f"Offset ~{float(saz['recommended_theta0_deg']):.1f}°"
        elif saz.get("mid_spin_compensated_by_flow_time"):
            saz_status = "PASS"
            saz_val = f"Mid-run (θ≈{float(saz.get('flow_start_angle_deg') or 0.0):.1f}°)"
        elif saz.get("started_at_zero") is True:
            saz_status = "PASS"
            saz_val = f"0° verified (θ0={float(saz.get('entered_theta0_deg') or 0.0):.1f}°)"
        elif saz.get("is_mid_spin"):
            saz_status = "PASS"
            saz_val = f"Aligned (θ0={float(saz.get('entered_theta0_deg') or 0.0):.1f}°)"
        else:
            saz_status = "PASS"
            saz_val = saz.get("method", "checked")
        qa.append({
            "check": "Start azimuth (θ0)",
            "status": saz_status,
            "value": saz_val,
            "detail": str(saz.get("detail") or saz.get("summary") or "Start azimuth checked.")
        })

    return qa


def export_timeseries_csv(R: Dict[str, Any], path: str) -> None:
    """Export processed CFD time series to a lightweight CSV file."""
    p, d, res = R["p"], R["data"], R["res"]
    nb = p.n_blades if d["have_blades"] else 0
    headers = [
        "Step", "Flow_time_s", "Revolution", "Theta_b1_deg"
    ] + [f"Theta_b{k + 1}_deg" for k in range(1, nb)] + [
        "Rotor_moment_Nm"
    ] + [f"M_blade_{k + 1}_Nm" for k in range(nb)] + [
        "Cp", "Cm"
    ] + [f"Cp_blade_{k + 1}" for k in range(nb)]
    if d["Ct"] is not None:
        headers.append("Ct")
    if d["Cy"] is not None:
        headers.append("Cy")

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        n_rows = len(d["t"])
        for i in range(n_rows):
            row = [
                int(d["step"][i]),
                f"{d['t'][i]:.6e}",
                f"{(d['t'][i] - d['t'][0]) / res['Trev']:.4f}",
                f"{d['TH'][0][i]:.2f}",
            ] + [f"{d['TH'][k][i]:.2f}" for k in range(1, nb)] + [
                f"{d['Mrot'][i]:.6e}",
            ] + [f"{d['Mb'][k][i]:.6e}" for k in range(nb)] + [
                f"{d['Cp'][i]:.6f}",
                f"{d['Cm'][i]:.6f}",
            ] + [f"{d['Cp_b'][k][i]:.6f}" for k in range(nb)]
            if d["Ct"] is not None:
                row.append(f"{d['Ct'][i]:.6f}")
            if d["Cy"] is not None:
                row.append(f"{d['Cy'][i]:.6f}")
            writer.writerow(row)


def run_pipeline(
    p: Params,
    f: InputFiles,
    outdir: str,
    prefix: str = "VAWT_analysis",
    log: Callable[[str], None] = print,
    cancel_token: Optional[Any] = None,
    make_figs: bool = True,
    export_excel: bool = True
) -> Dict[str, Any]:
    """Execute complete analysis pipeline: compute physics, render figures, write Excel workbook, export JSON."""
    from vawt_post.engine.plots import make_figures
    from vawt_post.engine.excel import write_excel
    from vawt_post.engine.latex import export_latex_snippet

    if cancel_token and cancel_token.is_set():
        raise InterruptedError("Analysis cancelled by user.")

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

    os.makedirs(outdir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Compute SHA-256 for all input files (Tier 2 Reproducibility)
    file_hashes: Dict[str, str] = {}
    if f.total:
        file_hashes["total"] = compute_file_sha256(f.total)
    if f.shaft:
        file_hashes["shaft"] = compute_file_sha256(f.shaft)
    if f.fx:
        file_hashes["fx"] = compute_file_sha256(f.fx)
    if f.fy:
        file_hashes["fy"] = compute_file_sha256(f.fy)
    for k, b_path in enumerate(f.blades):
        if b_path:
            file_hashes[f"blade_{k + 1}"] = compute_file_sha256(b_path)

    R = analyze(p, f, _safe_log)
    R["file_hashes"] = file_hashes

    # Run file diagnostics if not already present
    if "diagnostics" not in R:
        try:
            from vawt_post.engine.diagnostics import run_file_health_diagnostics as run_diagnostics
            R["diagnostics"] = run_diagnostics(p, f)
        except Exception as _de:
            _safe_log(f"File diagnostics notice: {_de}")

    if cancel_token and cancel_token.is_set():
        raise InterruptedError("Analysis cancelled by user.")

    if make_figs:
        figdir = os.path.join(outdir, f"{prefix}_{stamp}_figures")
        figs = make_figures(R, figdir, _safe_log)
    else:
        figs = {}

    if cancel_token and cancel_token.is_set():
        raise InterruptedError("Analysis cancelled by user.")

    if export_excel:
        xlsx = os.path.join(outdir, f"{prefix}_{stamp}.xlsx")
        write_excel(R, figs, xlsx, _safe_log)

        if cancel_token and cancel_token.is_set():
            raise InterruptedError("Analysis cancelled by user.")

        tex = os.path.join(outdir, f"{prefix}_{stamp}_thesis_snippet.tex")
        export_latex_snippet(R, figs, tex, _safe_log)
    else:
        xlsx = None
        tex = None

    # Export raw time series CSV (Tier 2)
    csv_ts_path = os.path.join(outdir, f"{prefix}_{stamp}_timeseries.csv")
    export_timeseries_csv(R, csv_ts_path)
    R["csv_timeseries"] = csv_ts_path

    # Export File-Health Diagnostics JSON and CSV (Part A)
    from vawt_post.engine.diagnostics import export_diagnostics_json, export_diagnostics_csv
    if "diagnostics" in R:
        diag_json_path = os.path.join(outdir, f"{prefix}_{stamp}_diagnostics.json")
        diag_csv_path = os.path.join(outdir, f"{prefix}_{stamp}_diagnostics.csv")
        export_diagnostics_json(R["diagnostics"], diag_json_path)
        export_diagnostics_csv(R["diagnostics"], diag_csv_path)
        R["diagnostics_json"] = diag_json_path
        R["diagnostics_csv"] = diag_csv_path

    # Reproducibility results.json export (Tier 2)
    res = R["res"]
    results_summary = {
        "app": {
            "name": "VAWT CFD Post-Processor",
            "version": "2.0.0",
            "timestamp": _dt.datetime.now().isoformat(),
        },
        "parameters": {
            "U": p.U,
            "tsr": p.tsr_val() if p.U and p.R else None,
            "omega": p.omega_val() if (p.omega or (p.tsr and p.U and p.R)) else None,
            "R": p.R,
            "chord": p.chord,
            "n_blades": p.n_blades,
            "depth": p.depth,
            "span": p.span,
            "rho": p.rho,
            "mu": p.mu,
            "theta0": p.theta0,
            "rotation_dir": p.rotation_dir,
            "azimuth_zero": p.azimuth_zero,
            "azimuth_offset": p.azimuth_offset,
            "moment_sign": p.moment_sign,
            "preset_name": p.preset_name,
            "sources": p.param_sources,
        },
        "case_metadata": asdict(p.metadata),
        "input_files": {
            k: {
                "path": getattr(f, k) if k in ("total", "shaft", "fx", "fy") else (f.blades[int(k.split('_')[1]) - 1] if '_' in k else None),
                "sha256": h
            }
            for k, h in file_hashes.items()
        },
        "summary_kpis": {
            "cp_reported_mean": res.get("cp_reported_mean", res["cp_mean"]),
            "cp_trailing_stable_mean": res.get("cp_trailing_stable_mean"),
            "cp_all_revolutions_mean": res.get("cp_all_revolutions_mean"),
            "cp_extrapolated_aitken": res.get("cp_extrapolated_aitken"),
            "extra_revs_to_converge": res.get("extra_revs_to_converge"),
            "is_stable": res.get("is_stable"),
            "stable_n": res.get("stable_n"),
            "cm_mean": res.get("cm_mean"),
            "torque_mean": res.get("torque_mean"),
            "power_mean_W": res.get("power_mean_W"),
            "ct_mean": res.get("ct_mean"),
            "cy_mean": res.get("cy_mean"),
            "alpha_max": res.get("alpha_max"),
            "ripple_pp": res.get("ripple_pp"),
            "is_self_starting": res.get("is_self_starting"),
            "cp_blades_only": res.get("cp_blades_only"),
            "cp_total_file": res.get("cp_total_file"),
            "dominant_harmonic": res.get("dominant_harmonic"),
            "span_H": res.get("span_H"),
            "swept_area_3d": res.get("swept_area_3d"),
            "power_3d_W": res.get("power_3d_W"),
            "torque_3d_Nm": res.get("torque_3d_Nm"),
            "notice_3d": res.get("notice_3d"),
        },
        "qa_checks": R["qa_checks"],
        "diagnostics": R["diagnostics"].to_dict() if "diagnostics" in R else None,
        "continuity": res.get("continuity"),
        "mesh_info": R.get("mesh_info") or None,
        "warnings": R["warnings"],
        "azimuth_curves": {
            "theta": [float(x) for x in R["az"]["theta"]],
            "cp_mean": [float(x) for x in R["az"]["cp_mean"]],
            "cm_mean": [float(x) for x in R["az"]["cm_mean"]],
            "ct_mean": [float(x) for x in R["az"]["ct_mean"]] if "ct_mean" in R["az"] else None,
            "cy_mean": [float(x) for x in R["az"]["cy_mean"]] if "cy_mean" in R["az"] else None,
        },
        "revolutions": [
            {
                "k": r["k"],
                "cp": float(r["cp"]),
                "cm": float(r["cm"]),
                "ct": float(r["ct"]) if r.get("ct") is not None else None,
                "cy": float(r["cy"]) if r.get("cy") is not None else None,
                "stable": bool(r.get("stable", False)),
                "selected": bool(r.get("selected", False)),
            }
            for r in R["revs"]
        ],
    }
    results_json_path = os.path.join(outdir, "results.json")
    results_stamp_json_path = os.path.join(outdir, f"{prefix}_{stamp}_results.json")
    with open(results_json_path, "w", encoding="utf-8") as fh:
        json.dump(results_summary, fh, indent=2)
    with open(results_stamp_json_path, "w", encoding="utf-8") as fh:
        json.dump(results_summary, fh, indent=2)
    R["results_json"] = results_json_path
    _safe_log(f"Exported reproducibility audit data: {results_json_path}")

    for w in R["warnings"]:
        _safe_log("WARNING: " + w)

    R["xlsx"] = xlsx
    R["tex"] = tex
    R["figs"] = figs
    return R
