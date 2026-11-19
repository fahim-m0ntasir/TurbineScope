# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Run Continuity, Overlap Splicing & Missing-Timestep Recovery
===================================================================================
Links an initial/backup simulation folder or .zip archive with a restarted/resumed
ANSYS Fluent run (*rfile.out), handling both:
  1. Checkpoint Rollback Overlaps  (curr_start_step <= backup_end_step)
  2. Positive Missing-Step Gaps    (curr_start_step >  backup_end_step + stride)

Provides phase-locked multi-harmonic + C1 residual drift recovery for cyclic VAWT
signals, shape-preserving PCHIP fallback for short/transient gaps, leave-window-out
validation scoring, cross-role torque conservation, and clean .out file export.

GUI-free module: pure numpy / standard library.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from vawt_post.engine.config import InputFiles, Params
from vawt_post.engine.parser import (
    autodetect,
    read_fluent_report,
    resolve_fluent_directory,
)

_PRIOR_EXTRACT_EXTS = (".out", ".txt", ".dat", ".csv", ".trn", ".log", ".jou", ".set", ".ip")


def normalize_role_key(role: str) -> str:
    """Normalize role names across analysis ('blade1') and live monitor ('blade 1')."""
    r = role.strip().lower().replace("-", "_")
    m = re.match(r"^blade[\s_]+(\d+)$", r)
    if m:
        return f"blade{m.group(1)}"
    m2 = re.match(r"^(fx|fy)[\s_]+blade[\s_]+(\d+)$", r)
    if m2:
        return f"{m2.group(1)}_blade{m2.group(2)}"
    return r


def format_step_ranges(steps: np.ndarray) -> List[Tuple[int, int]]:
    """Collapse sorted integer steps into contiguous (start, end) ranges."""
    if len(steps) == 0:
        return []
    arr = np.unique(np.asarray(steps, dtype=np.int64))
    if len(arr) == 1:
        v = int(arr[0])
        return [(v, v)]
    breaks = np.where(np.diff(arr) > 1)[0]
    ranges: List[Tuple[int, int]] = []
    start_i = 0
    for b in breaks:
        ranges.append((int(arr[start_i]), int(arr[b])))
        start_i = int(b) + 1
    ranges.append((int(arr[start_i]), int(arr[-1])))
    return ranges


def resolve_prior_source_directory(source_path: str) -> Tuple[str, bool]:
    """Resolve a backup folder or .zip archive to a directory containing Fluent report files.

    For .zip archives, only lightweight report/metadata files (excluding huge .cas.h5/.dat.h5)
    are cached once under a content-stamped temp directory so repeated polls/checks are instant.

    Returns:
        (resolved_folder_path, is_zip_archive)
    """
    if not source_path:
        raise FileNotFoundError("No backup folder or .zip archive specified.")
    src = os.path.abspath(source_path.strip())
    if os.path.isdir(src):
        resolved = resolve_fluent_directory(src)
        return os.path.abspath(resolved if resolved and os.path.isdir(resolved) else src), False

    if not os.path.isfile(src) or not src.lower().endswith(".zip"):
        raise FileNotFoundError(f"Backup source not found or unsupported format: {source_path}")

    st = os.stat(src)
    sig_raw = f"{src}|{st.st_size}|{getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9))}"
    cache_key = hashlib.sha256(sig_raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    cache_root = os.path.join(tempfile.gettempdir(), "turbinescope_prior_cache", cache_key)
    marker = os.path.join(cache_root, ".extract_ok")

    if not os.path.isfile(marker):
        os.makedirs(cache_root, exist_ok=True)
        cache_root_abs = os.path.abspath(cache_root)
        with zipfile.ZipFile(src, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                low_name = info.filename.lower()
                if not low_name.endswith(_PRIOR_EXTRACT_EXTS):
                    continue
                # Guard against zip-slip path traversal
                rel = os.path.normpath(info.filename.replace("\\", "/")).lstrip("\\/")
                if rel.startswith("..") or os.path.isabs(rel):
                    continue
                dest_path = os.path.abspath(os.path.join(cache_root_abs, rel))
                if not (dest_path == cache_root_abs or dest_path.startswith(cache_root_abs + os.sep)):
                    continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with zf.open(info, "r") as src_fh, open(dest_path, "wb") as dst_fh:
                    dst_fh.write(src_fh.read())
        with open(marker, "w", encoding="utf-8") as mf:
            mf.write(src)

    resolved = resolve_fluent_directory(cache_root)
    return os.path.abspath(resolved if resolved and os.path.isdir(resolved) else cache_root), True


def _index_report_files_recursive(root_dir: str) -> Dict[str, str]:
    """Build basename -> full path index for monitor files under root_dir."""
    by_name: Dict[str, str] = {}
    if not os.path.isdir(root_dir):
        return by_name
    for cur_root, _, files in os.walk(root_dir):
        for fn in files:
            low = fn.lower()
            if low.endswith((".out", ".txt", ".dat", ".csv")) and not low.endswith((".trn", ".log", ".jou")):
                if low not in by_name:
                    by_name[low] = os.path.join(cur_root, fn)
    return by_name


def _map_input_files_to_roles(f: InputFiles) -> Dict[str, str]:
    """Extract canonical role -> filepath dictionary from an InputFiles object."""
    mapping: Dict[str, str] = {}
    if f.total:
        mapping["total"] = f.total
    for i, bp in enumerate(f.blades or []):
        if bp:
            mapping[f"blade{i + 1}"] = bp
    if f.shaft:
        mapping["shaft"] = f.shaft
    if f.fx:
        mapping["fx"] = f.fx
    if f.fy:
        mapping["fy"] = f.fy
    if f.fx_shaft:
        mapping["fx_shaft"] = f.fx_shaft
    if f.fy_shaft:
        mapping["fy_shaft"] = f.fy_shaft
    for i, bp in enumerate(getattr(f, "fx_blades", None) or []):
        if bp:
            mapping[f"fx_blade{i + 1}"] = bp
    for i, bp in enumerate(getattr(f, "fy_blades", None) or []):
        if bp:
            mapping[f"fy_blade{i + 1}"] = bp
    return mapping


def discover_prior_files(
    prior_source: str,
    n_blades: int = 3,
    current_files: Optional[InputFiles] = None,
    current_role_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve backup folder/.zip and pair prior monitor file paths by canonical role."""
    resolved_dir, is_zip = resolve_prior_source_directory(prior_source)
    prior_f = autodetect(resolved_dir, max(1, min(8, int(n_blades or 3))))
    prior_map = _map_input_files_to_roles(prior_f)

    # Fallback: exact filename match against current files inside backup tree
    file_index = _index_report_files_recursive(resolved_dir)
    if current_files is not None:
        curr_map = _map_input_files_to_roles(current_files)
        for r, cpath in curr_map.items():
            if r not in prior_map or not os.path.isfile(prior_map.get(r, "")):
                bname = os.path.basename(cpath).lower()
                if bname in file_index:
                    prior_map[r] = file_index[bname]

    if current_role_names:
        for r, bname in current_role_names.items():
            nr = normalize_role_key(r)
            if nr not in prior_map or not os.path.isfile(prior_map.get(nr, "")):
                cand = file_index.get(os.path.basename(bname).lower())
                if cand:
                    prior_map[nr] = cand

    # Keep only existing readable files
    prior_map = {r: p for r, p in prior_map.items() if p and os.path.isfile(p)}
    return {
        "resolved_dir": resolved_dir,
        "is_zip": is_zip,
        "role_paths": prior_map,
        "source_label": os.path.basename(os.path.abspath(prior_source.strip())),
    }


def load_prior_series(
    prior_source: str,
    n_blades: int = 3,
    current_files: Optional[InputFiles] = None,
    current_role_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Load and parse all matched monitor series from a prior run folder or .zip archive."""
    disc = discover_prior_files(
        prior_source=prior_source,
        n_blades=n_blades,
        current_files=current_files,
        current_role_names=current_role_names,
    )
    series: Dict[str, Dict[str, Any]] = {}
    role_files: Dict[str, str] = {}
    for role, path in disc["role_paths"].items():
        try:
            parsed = read_fluent_report(path)
            parsed["source_path"] = path
            series[role] = parsed
            role_files[role] = os.path.basename(path)
        except Exception:
            continue
    return {
        "series": series,
        "role_files": role_files,
        "resolved_dir": disc["resolved_dir"],
        "is_zip": disc["is_zip"],
        "source_label": disc["source_label"],
    }


def _clean_monotone_series(
    s: Dict[str, Any],
    dt_fallback: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Deduplicate repeated steps (keep last) and sort ascending by step index."""
    steps = np.asarray(s["step"], dtype=np.int64)
    vals = np.asarray(s["value"], dtype=float)
    raw_t = s.get("time")
    if raw_t is not None:
        times = np.asarray(raw_t, dtype=float)
    elif dt_fallback and dt_fallback > 0:
        times = steps.astype(float) * float(dt_fallback)
    else:
        times = steps.astype(float)

    if len(steps) == 0:
        return steps, times, vals

    # Keep last occurrence of each step
    _, rev_idx = np.unique(steps[::-1], return_index=True)
    idx = len(steps) - 1 - rev_idx
    order = np.argsort(steps[idx], kind="stable")
    sel = idx[order]
    return steps[sel], times[sel], vals[sel]


def _pchip_interpolator(x: np.ndarray, y: np.ndarray, x_new: np.ndarray) -> np.ndarray:
    """Pure-NumPy Fritsch-Carlson monotone piecewise cubic Hermite (PCHIP) interpolation."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xq = np.asarray(x_new, dtype=float)
    n = len(x)
    if n == 0:
        return np.zeros_like(xq)
    if n == 1:
        return np.full_like(xq, y[0])
    if n == 2:
        return np.interp(xq, x, y)

    h = np.diff(x)
    h = np.where(np.abs(h) < 1e-15, 1e-15, h)
    delta = np.diff(y) / h

    d = np.zeros(n, dtype=float)
    for k in range(1, n - 1):
        if delta[k - 1] * delta[k] > 0.0:
            w1 = 2.0 * h[k] + h[k - 1]
            w2 = h[k] + 2.0 * h[k - 1]
            d[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])
        else:
            d[k] = 0.0

    # Endpoint slopes (standard one-sided three-point shape-preserving estimate)
    d[0] = ((2.0 * h[0] + h[1]) * delta[0] - h[0] * delta[1]) / (h[0] + h[1])
    if d[0] * delta[0] <= 0.0:
        d[0] = 0.0
    elif abs(d[0]) > 3.0 * abs(delta[0]):
        d[0] = 3.0 * delta[0]

    d[-1] = ((2.0 * h[-1] + h[-2]) * delta[-1] - h[-1] * delta[-2]) / (h[-1] + h[-2])
    if d[-1] * delta[-1] <= 0.0:
        d[-1] = 0.0
    elif abs(d[-1]) > 3.0 * abs(delta[-1]):
        d[-1] = 3.0 * delta[-1]

    idx = np.clip(np.searchsorted(x, xq, side="right") - 1, 0, n - 2)
    hk = h[idx]
    u = (xq - x[idx]) / hk
    h00 = (2.0 * u - 3.0) * u * u + 1.0
    h10 = (u - 2.0) * u * u + u
    h01 = (-2.0 * u + 3.0) * u * u
    h11 = (u - 1.0) * u * u
    return h00 * y[idx] + h10 * hk * d[idx] + h01 * y[idx + 1] + h11 * hk * d[idx + 1]


def _estimate_endpoint_slope(t: np.ndarray, y: np.ndarray, from_end: bool = True) -> float:
    """Estimate smooth local derivative dy/dt near a boundary using up to 5 points."""
    n = min(5, len(t))
    if n < 2:
        return 0.0
    ts = t[-n:] if from_end else t[:n]
    ys = y[-n:] if from_end else y[:n]
    span = ts[-1] - ts[0]
    if span <= 1e-15:
        return 0.0
    t_rel = ts - (ts[-1] if from_end else ts[0])
    denom = float(np.dot(t_rel - np.mean(t_rel), t_rel - np.mean(t_rel)))
    if denom <= 1e-24:
        return 0.0
    slope = float(np.dot(t_rel - np.mean(t_rel), ys - np.mean(ys)) / denom)
    return slope if math.isfinite(slope) else 0.0


def _hermite_residual_bridge(
    t0: float, r0: float, m0: float,
    t1: float, r1: float, m1: float,
    g_times: np.ndarray,
) -> np.ndarray:
    """Smooth C1 cubic Hermite bridge of boundary residuals with clamped tangents."""
    dt_span = t1 - t0
    if dt_span <= 1e-15:
        return np.full_like(g_times, 0.5 * (r0 + r1), dtype=float)
    u = np.clip((g_times - t0) / dt_span, 0.0, 1.0)
    # Clamp endpoint tangents to prevent polynomial overshoot across long gaps
    dr = r1 - r0
    max_tan = max(abs(dr) * 2.5, 1e-12)
    t_a = float(np.clip(m0 * dt_span, -max_tan, max_tan))
    t_b = float(np.clip(m1 * dt_span, -max_tan, max_tan))
    h00 = (2.0 * u - 3.0) * u * u + 1.0
    h10 = (u - 2.0) * u * u + u
    h01 = (-2.0 * u + 3.0) * u * u
    h11 = (u - 1.0) * u * u
    return h00 * r0 + h10 * t_a + h01 * r1 + h11 * t_b


def _estimate_omega_from_signal(t: np.ndarray, y: np.ndarray) -> Optional[float]:
    """Fallback dominant rotational frequency estimator when omega isn't configured."""
    if len(t) < 64 or (t[-1] - t[0]) <= 0:
        return None
    n_grid = min(len(t), 2048)
    t_u = np.linspace(t[0], t[-1], n_grid)
    dt_u = (t[-1] - t[0]) / max(n_grid - 1, 1)
    if dt_u <= 0:
        return None
    y_u = np.interp(t_u, t, y)
    y_u = y_u - np.mean(y_u)
    if float(np.std(y_u)) < 1e-12:
        return None
    fft_mag = np.abs(np.fft.rfft(y_u))
    freqs = np.fft.rfftfreq(n_grid, dt_u)
    # Require >= 1.5 cycles inside window for reliable peak
    min_f = 1.2 / (t[-1] - t[0])
    valid = freqs >= min_f
    if not np.any(valid):
        return None
    f_peak = float(freqs[valid][np.argmax(fft_mag[valid])])
    return 2.0 * math.pi * f_peak if f_peak > 0 else None


def _recover_gap_values(
    b_times: np.ndarray,
    b_vals: np.ndarray,
    c_times: np.ndarray,
    c_vals: np.ndarray,
    g_times: np.ndarray,
    omega: Optional[float] = None,
    n_blades: int = 3,
    run_validation: bool = True,
) -> Dict[str, Any]:
    """Reconstruct missing values across g_times between (b_times, b_vals) and (c_times, c_vals).

    Automatically chooses between:
      - Tier 1: Phase-Locked Multi-Harmonic Fourier + C1 Residual Drift Bridge
      - Tier 3: Shape-Preserving PCHIP Spline Bridge (for ultra-short <=4 step gaps or non-periodic tails)
    """
    n_gap = len(g_times)
    if n_gap == 0:
        return {
            "values": np.array([], dtype=float),
            "method": "none",
            "val_rmse": None,
            "val_mae": None,
            "val_nrmse_pct": None,
        }

    # Ultra-short gaps (<= 4 steps): PCHIP preserves curvature with minimal distortion
    omega_eff = float(omega) if (omega is not None and omega > 0) else _estimate_omega_from_signal(b_times, b_vals)
    Trev = (2.0 * math.pi / omega_eff) if (omega_eff and omega_eff > 0) else None
    pre_dur = float(b_times[-1] - b_times[0]) if len(b_times) >= 2 else 0.0

    use_harmonic = (
        omega_eff is not None
        and Trev is not None
        and Trev > 0
        and n_gap > 4
        and pre_dur >= 0.85 * Trev
        and len(b_vals) >= 24
    )

    if use_harmonic and omega_eff is not None and Trev is not None:
        try:
            t_mid = 0.5 * (float(b_times[-1]) + float(c_times[0]))
            m_pre = b_times >= (float(b_times[-1]) - 2.0 * Trev)
            if int(np.sum(m_pre)) < 24:
                m_pre = np.ones(len(b_times), dtype=bool)
            m_post = c_times <= (float(c_times[0]) + 1.0 * Trev)
            if int(np.sum(m_post)) == 0 and len(c_times) > 0:
                m_post = np.zeros(len(c_times), dtype=bool)
                m_post[:min(len(c_times), 5)] = True

            tp = b_times[m_pre]
            yp = b_vals[m_pre]
            tc = c_times[m_post]
            yc = c_vals[m_post]

            t_train = np.concatenate([tp, tc])
            y_train = np.concatenate([yp, yc])

            dt_med = float(np.median(np.diff(tp))) if len(tp) > 1 else Trev / 360.0
            pts_per_rev = Trev / max(dt_med, 1e-12)
            max_h_by_pts = max(3, int(len(t_train) // 5))
            max_h_by_nyq = max(3, int(pts_per_rev * 0.20))
            n_harm = min(12, max(3, n_blades * 3), max_h_by_pts, max_h_by_nyq)

            def _design(t_vec: np.ndarray) -> np.ndarray:
                tau = (t_vec - t_mid) / Trev
                cols = [np.ones_like(tau), tau]
                for k in range(1, n_harm + 1):
                    ang = k * omega_eff * t_vec
                    cols.append(np.cos(ang))
                    cols.append(np.sin(ang))
                return np.column_stack(cols)

            X = _design(t_train)
            # Ridge regularization on linear drift + higher harmonics keeps fit well-conditioned
            xtx = X.T @ X
            reg_diag = np.zeros(X.shape[1], dtype=float)
            reg_diag[1] = 1e-3 * len(t_train)
            for k in range(1, n_harm + 1):
                scale_k = 1e-5 * len(t_train) * (1.0 + 0.05 * k)
                reg_diag[2 * k] = scale_k
                reg_diag[2 * k + 1] = scale_k
            w = np.linalg.solve(xtx + np.diag(reg_diag), X.T @ y_train)

            # Evaluate model on boundary neighborhoods + gap grid
            n_edge_b = min(6, len(b_times))
            n_edge_c = min(6, len(c_times))
            tb_edge = b_times[-n_edge_b:]
            yb_edge = b_vals[-n_edge_b:]
            tc_edge = c_times[:n_edge_c]
            yc_edge = c_vals[:n_edge_c]

            rb_edge = yb_edge - (_design(tb_edge) @ w)
            rc_edge = yc_edge - (_design(tc_edge) @ w)

            m_r0 = _estimate_endpoint_slope(tb_edge, rb_edge, from_end=True)
            m_r1 = _estimate_endpoint_slope(tc_edge, rc_edge, from_end=False)

            r_gap = _hermite_residual_bridge(
                float(tb_edge[-1]), float(rb_edge[-1]), m_r0,
                float(tc_edge[0]), float(rc_edge[0]), m_r1,
                g_times,
            )
            y_gap = (_design(g_times) @ w) + r_gap

            # Envelope guardrail against pathological blowup
            env_all = np.concatenate([yp, yc])
            y_lo, y_hi = float(np.min(env_all)), float(np.max(env_all))
            span = max(y_hi - y_lo, float(np.std(env_all)), 1e-6)
            if (
                np.all(np.isfinite(y_gap))
                and float(np.min(y_gap)) >= y_lo - 0.75 * span
                and float(np.max(y_gap)) <= y_hi + 0.75 * span
            ):
                val_rmse = val_mae = val_nrmse_pct = None
                if run_validation and len(b_vals) >= 3 * max(n_gap, 12) and pre_dur >= 1.5 * Trev:
                    val_metrics = _backtest_gap_recovery(
                        b_times, b_vals, n_gap, omega=omega_eff, n_blades=n_blades
                    )
                    val_rmse = val_metrics["rmse"]
                    val_mae = val_metrics["mae"]
                    val_nrmse_pct = val_metrics["nrmse_pct"]

                return {
                    "values": y_gap,
                    "method": f"Phase-Harmonic ({n_harm}H) + C1 Residual Bridge",
                    "val_rmse": val_rmse,
                    "val_mae": val_mae,
                    "val_nrmse_pct": val_nrmse_pct,
                }
        except Exception:
            pass

    # Tier 3 fallback: PCHIP spline over boundary window
    n_left = min(12, len(b_times))
    n_right = min(12, len(c_times))
    t_anc = np.concatenate([b_times[-n_left:], c_times[:n_right]])
    y_anc = np.concatenate([b_vals[-n_left:], c_vals[:n_right]])
    y_pchip = _pchip_interpolator(t_anc, y_anc, g_times)

    val_rmse = val_mae = val_nrmse_pct = None
    if run_validation and len(b_vals) >= 3 * max(n_gap, 8):
        val_metrics = _backtest_gap_recovery(b_times, b_vals, n_gap, omega=omega_eff, n_blades=n_blades)
        val_rmse = val_metrics["rmse"]
        val_mae = val_metrics["mae"]
        val_nrmse_pct = val_metrics["nrmse_pct"]

    return {
        "values": y_pchip,
        "method": "PCHIP Shape-Preserving Spline",
        "val_rmse": val_rmse,
        "val_mae": val_mae,
        "val_nrmse_pct": val_nrmse_pct,
    }


def _backtest_gap_recovery(
    b_times: np.ndarray,
    b_vals: np.ndarray,
    n_gap: int,
    omega: Optional[float] = None,
    n_blades: int = 3,
) -> Dict[str, Optional[float]]:
    """Hold out an intact segment right before the gap to measure empirical recovery accuracy."""
    try:
        n_total = len(b_vals)
        k_val = max(2, min(n_gap, n_total // 5))
        tail_ctx = max(12, min(36, n_total // 6))
        split_end = n_total - tail_ctx
        split_start = split_end - k_val
        if split_start < max(24, 2 * k_val):
            return {"rmse": None, "mae": None, "nrmse_pct": None}

        res_bt = _recover_gap_values(
            b_times[:split_start],
            b_vals[:split_start],
            b_times[split_end:],
            b_vals[split_end:],
            b_times[split_start:split_end],
            omega=omega,
            n_blades=n_blades,
            run_validation=False,
        )
        y_true = b_vals[split_start:split_end]
        y_pred = res_bt["values"]
        err = y_true - y_pred
        rmse = float(np.sqrt(np.mean(err ** 2)))
        mae = float(np.mean(np.abs(err)))
        sig_scale = max(
            float(np.sqrt(np.mean(b_vals ** 2))),
            float(np.ptp(b_vals)) * 0.25,
            1e-9,
        )
        nrmse_pct = float(rmse / sig_scale * 100.0)
        return {"rmse": rmse, "mae": mae, "nrmse_pct": nrmse_pct}
    except Exception:
        return {"rmse": None, "mae": None, "nrmse_pct": None}


def stitch_series_pair(
    prior_s: Dict[str, Any],
    curr_s: Dict[str, Any],
    p: Optional[Params] = None,
    mode: str = "auto",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge a prior series (backup) with a resumed continuation series (current).

    Args:
        prior_s: Parsed dictionary from read_fluent_report() for backup run.
        curr_s: Parsed dictionary from read_fluent_report() for current/resumed run.
        p: Optional turbine Params (provides omega, n_blades, dt).
        mode: 'auto' (prefer_current overlap + gap recovery),
              'prefer_backup' (prefer_backup overlap + gap recovery),
              'splice_only' (deduplicate overlap without synthesizing missing steps),
              'off' (return curr_s untouched).

    Returns:
        (merged_series_dict, role_continuity_report)
    """
    if mode == "off" or not prior_s:
        c_step, c_time, c_val = _clean_monotone_series(curr_s, getattr(p, "dt", None) if p else None)
        out = dict(curr_s)
        out["step"] = c_step
        out["time"] = c_time
        out["value"] = c_val
        out["recovered_mask"] = np.zeros(len(c_step), dtype=bool)
        return out, {"status": "disabled", "recovered_count": 0, "overlap_count": 0}

    dt_fb = getattr(p, "dt", None) if p else None
    b_step, b_time, b_val = _clean_monotone_series(prior_s, dt_fb)
    c_step, c_time, c_val = _clean_monotone_series(curr_s, dt_fb)

    if len(b_step) == 0:
        out = dict(curr_s)
        out["step"] = c_step
        out["time"] = c_time
        out["value"] = c_val
        out["recovered_mask"] = np.zeros(len(c_step), dtype=bool)
        return out, {"status": "empty_backup", "recovered_count": 0, "overlap_count": 0}
    if len(c_step) == 0:
        out = dict(prior_s)
        out["step"] = b_step
        out["time"] = b_time
        out["value"] = b_val
        out["recovered_mask"] = np.zeros(len(b_step), dtype=bool)
        return out, {"status": "empty_current", "recovered_count": 0, "overlap_count": 0}

    # Estimate step write stride
    diffs_b = np.diff(b_step)
    pos_db = diffs_b[diffs_b > 0]
    stride = int(np.median(pos_db)) if len(pos_db) > 0 and np.mean(pos_db == int(np.median(pos_db))) >= 0.8 else 1
    stride = max(1, stride)

    b_end = int(b_step[-1])
    c_start = int(c_step[0])

    omega = None
    nb = 3
    if p is not None:
        nb = int(p.n_blades or 3)
        try:
            omega = p.omega_val()
        except Exception:
            omega = float(p.omega) if (p.omega and p.omega > 0) else None

    info: Dict[str, Any] = {
        "backup_step_range": (int(b_step[0]), int(b_end)),
        "current_step_range": (int(c_start), int(c_step[-1])),
        "backup_rows": int(len(b_step)),
        "current_rows": int(len(c_step)),
        "overlap_count": 0,
        "overlap_step_range": None,
        "overlap_max_val_diff": 0.0,
        "overlap_max_time_diff": 0.0,
        "overlap_divergent": False,
        "recovered_count": 0,
        "recovered_step_ranges": [],
        "recovered_time_ranges": [],
        "splice_steps": [],
        "splice_times": [],
        "method": "direct_splice",
        "val_rmse": None,
        "val_mae": None,
        "val_nrmse_pct": None,
    }

    # Regime 1: Checkpoint Overlap (c_start <= b_end)
    if c_start <= b_end:
        overlap_steps = np.intersect1d(b_step, c_step)
        info["overlap_count"] = int(len(overlap_steps))
        # Reject disjoint future backup (backup starts strictly after current ends, or no overlap with c_start <= b_start)
        if int(c_step[-1]) < int(b_step[0]) or (len(overlap_steps) == 0 and c_start <= int(b_step[0])):
            out = dict(curr_s)
            out["step"] = c_step
            out["time"] = c_time
            out["value"] = c_val
            out["recovered_mask"] = np.zeros(len(c_step), dtype=bool)
            info["status"] = "incompatible_backup"
            info["method"] = "rejected_unrelated_backup"
            return out, info
        if len(overlap_steps) > 0:
            info["overlap_step_range"] = (int(overlap_steps[0]), int(overlap_steps[-1]))
            ib = np.searchsorted(b_step, overlap_steps)
            ic = np.searchsorted(c_step, overlap_steps)
            max_v = float(np.max(np.abs(b_val[ib] - c_val[ic])))
            max_t = float(np.max(np.abs(b_time[ib] - c_time[ic])))
            sig_scale = max(float(np.std(b_val)), float(np.max(np.abs(b_val))) * 0.1, 1e-9)
            info["overlap_max_val_diff"] = max_v
            info["overlap_max_time_diff"] = max_t
            info["overlap_divergent"] = bool((max_v / sig_scale) > 1e-3 or max_t > 1e-6)

            # Reject unrelated/stale backups when the current run starts at/before the backup start
            # and either the backup runs beyond the current end or the overlapping run diverges
            # strongly (e.g., an old simulation's backup left linked after switching folders).
            if c_start <= int(b_step[0]) and (
                b_end > int(c_step[-1])
                or max_t > 1e-5
                or (max_v / sig_scale) > 0.05
            ):
                out = dict(curr_s)
                out["step"] = c_step
                out["time"] = c_time
                out["value"] = c_val
                out["recovered_mask"] = np.zeros(len(c_step), dtype=bool)
                info["status"] = "incompatible_backup"
                info["method"] = "rejected_unrelated_backup"
                return out, info

        prefer_backup = (mode == "prefer_backup")
        if prefer_backup and int(c_step[-1]) > b_end:
            keep_c_pre = c_step < int(b_step[0])
            keep_c_post = c_step > b_end
            m_step = np.concatenate([c_step[keep_c_pre], b_step, c_step[keep_c_post]])
            m_time = np.concatenate([c_time[keep_c_pre], b_time, c_time[keep_c_post]])
            m_val = np.concatenate([c_val[keep_c_pre], b_val, c_val[keep_c_post]])
            n_pre = int(np.sum(keep_c_pre))
            splice_idx = (n_pre + len(b_step)) if np.any(keep_c_post) else (n_pre + len(b_step) - 1)
        elif prefer_backup:
            keep_c_pre = c_step < int(b_step[0])
            m_step = np.concatenate([c_step[keep_c_pre], b_step])
            m_time = np.concatenate([c_time[keep_c_pre], b_time])
            m_val = np.concatenate([c_val[keep_c_pre], b_val])
            splice_idx = int(np.sum(keep_c_pre)) + len(b_step) - 1
        else:
            keep_b = b_step < c_start
            # Never append backup rows beyond c_step[-1]: a predecessor backup must not extend past current.
            m_step = np.concatenate([b_step[keep_b], c_step])
            m_time = np.concatenate([b_time[keep_b], c_time])
            m_val = np.concatenate([b_val[keep_b], c_val])
            splice_idx = int(np.sum(keep_b))

        rec_mask = np.zeros(len(m_step), dtype=bool)
        if 0 <= splice_idx < len(m_step):
            info["splice_steps"] = [int(m_step[splice_idx])]
            info["splice_times"] = [float(m_time[splice_idx])]
        info["status"] = "overlap_divergent" if info["overlap_divergent"] else "overlap_verified"
        info["method"] = "prefer_backup_dedup" if prefer_backup else "prefer_current_dedup"

    # Non-monotonic flow-time across step boundary indicates unrelated/stale backup
    elif c_time[0] <= b_time[-1] - 1e-6:
        out = dict(curr_s)
        out["step"] = c_step
        out["time"] = c_time
        out["value"] = c_val
        out["recovered_mask"] = np.zeros(len(c_step), dtype=bool)
        info["status"] = "incompatible_backup"
        info["method"] = "rejected_nonmonotonic_time"
        return out, info

    # Regime 2: Exact adjacent (c_start == b_end + stride)
    elif c_start == b_end + stride:
        m_step = np.concatenate([b_step, c_step])
        m_time = np.concatenate([b_time, c_time])
        m_val = np.concatenate([b_val, c_val])
        rec_mask = np.zeros(len(m_step), dtype=bool)
        info["status"] = "exact_adjacent"
        info["splice_steps"] = [int(c_start)]
        info["splice_times"] = [float(c_time[0])]

    # Regime 3: Positive Missing-Timestep Gap (c_start > b_end + stride)
    else:
        g_steps = np.arange(b_end + stride, c_start, stride, dtype=np.int64)
        n_miss = len(g_steps)
        if mode == "splice_only" or n_miss == 0:
            m_step = np.concatenate([b_step, c_step])
            m_time = np.concatenate([b_time, c_time])
            m_val = np.concatenate([b_val, c_val])
            rec_mask = np.zeros(len(m_step), dtype=bool)
            info["status"] = "gap_unfilled"
            info["recovered_count"] = 0
            info["missing_step_count"] = int(n_miss)
            info["recovered_step_ranges"] = [(int(g_steps[0]), int(g_steps[-1]))] if n_miss > 0 else []
            info["splice_steps"] = [int(c_start)]
            info["splice_times"] = [float(c_time[0])]
        else:
            # Uniformly bridge flow-time endpoints across missing steps
            t_grid = np.linspace(float(b_time[-1]), float(c_time[0]), n_miss + 2, dtype=float)
            g_times = t_grid[1:-1]
            has_phys_t = bool(
                (prior_s.get("time") is not None)
                or (curr_s.get("time") is not None)
                or (dt_fb is not None and dt_fb > 0)
            )
            rec_res = _recover_gap_values(
                b_times=b_time,
                b_vals=b_val,
                c_times=c_time,
                c_vals=c_val,
                g_times=g_times,
                omega=(omega if has_phys_t else None),
                n_blades=nb,
                run_validation=True,
            )
            g_vals = rec_res["values"]
            m_step = np.concatenate([b_step, g_steps, c_step])
            m_time = np.concatenate([b_time, g_times, c_time])
            m_val = np.concatenate([b_val, g_vals, c_val])
            rec_mask = np.concatenate([
                np.zeros(len(b_step), dtype=bool),
                np.ones(n_miss, dtype=bool),
                np.zeros(len(c_step), dtype=bool),
            ])
            info["status"] = "gap_recovered"
            info["recovered_count"] = int(n_miss)
            info["recovered_step_ranges"] = [(int(g_steps[0]), int(g_steps[-1]))]
            info["recovered_time_ranges"] = [(float(g_times[0]), float(g_times[-1]))]
            info["splice_steps"] = [int(g_steps[0]), int(c_start)]
            info["splice_times"] = [float(g_times[0]), float(c_time[0])]
            info["method"] = rec_res["method"]
            info["val_rmse"] = rec_res["val_rmse"]
            info["val_mae"] = rec_res["val_mae"]
            info["val_nrmse_pct"] = rec_res["val_nrmse_pct"]

    has_time_col = bool((prior_s.get("time") is not None) or (curr_s.get("time") is not None))
    out = dict(curr_s)
    out["step"] = m_step
    out["time"] = m_time if (has_time_col or (dt_fb is not None and dt_fb > 0)) else None
    out["value"] = m_val
    out["recovered_mask"] = rec_mask
    out["continuity_info"] = info
    return out, info


def stitch_and_recover_all(
    current_series: Dict[str, Dict[str, Any]],
    prior_source: Optional[str],
    p: Optional[Params] = None,
    mode: str = "auto",
    current_files: Optional[InputFiles] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Load prior run source and stitch/recover all matching roles in current_series."""
    if not prior_source or mode == "off" or not current_series:
        return current_series, {
            "enabled": False,
            "status": "disabled",
            "matched_roles": [],
            "unmatched_roles": list(current_series.keys()),
            "recovered_count": 0,
            "recovered_step_ranges": [],
            "recovered_time_ranges": [],
            "splice_times": [],
            "splice_steps": [],
            "overlap_count": 0,
        }

    nb = int((p.n_blades if p and p.n_blades else 3) or 3)
    curr_names = {
        normalize_role_key(k): str(v.get("name", ""))
        for k, v in current_series.items()
        if v.get("name")
    }
    loaded = load_prior_series(
        prior_source=prior_source,
        n_blades=nb,
        current_files=current_files,
        current_role_names=curr_names,
    )
    prior_map = loaded["series"]

    merged: Dict[str, Dict[str, Any]] = {}
    role_reports: Dict[str, Dict[str, Any]] = {}
    matched_roles: List[str] = []
    unmatched_roles: List[str] = []

    for orig_key, curr_s in current_series.items():
        canon = normalize_role_key(orig_key)
        prior_s = prior_map.get(canon) or prior_map.get(orig_key)
        if prior_s is None:
            unmatched_roles.append(orig_key)
            s_copy = dict(curr_s)
            s_copy["recovered_mask"] = np.zeros(len(curr_s.get("step", [])), dtype=bool)
            merged[orig_key] = s_copy
            continue
        matched_roles.append(orig_key)
        m_s, rep = stitch_series_pair(prior_s, curr_s, p=p, mode=mode)
        merged[orig_key] = m_s
        role_reports[orig_key] = rep

    # Cross-role physical balance refinement:
    # When all blades 1..N AND total were gap-recovered on the exact same gap steps and
    # pre-gap hub/strut residual is tiny (< 2% RMS), keep total aligned with sum(blades) + shaft.
    blade_keys = [f"blade{i + 1}" for i in range(nb)]
    canon_to_orig = {normalize_role_key(k): k for k in merged}
    if (
        mode not in ("off", "splice_only")
        and "total" in canon_to_orig
        and all(bk in canon_to_orig for bk in blade_keys)
        and role_reports.get(canon_to_orig["total"], {}).get("recovered_count", 0) > 0
    ):
        tot_orig = canon_to_orig["total"]
        tot_s = merged[tot_orig]
        tot_rec = tot_s.get("recovered_mask")
        if tot_rec is not None and np.any(tot_rec):
            try:
                t_steps = tot_s["step"]
                b_sum = np.zeros_like(tot_s["value"])
                all_aligned = True
                for bk in blade_keys:
                    bs = merged[canon_to_orig[bk]]
                    if len(bs["step"]) != len(t_steps) or not np.array_equal(bs["step"], t_steps):
                        all_aligned = False
                        break
                    b_sum = b_sum + bs["value"]
                if all_aligned and "shaft" in canon_to_orig:
                    sh_s = merged[canon_to_orig["shaft"]]
                    if len(sh_s["step"]) == len(t_steps) and np.array_equal(sh_s["step"], t_steps):
                        b_sum = b_sum + sh_s["value"]
                if all_aligned:
                    pre_idx = np.where(~tot_rec)[0]
                    if len(pre_idx) >= 20:
                        tail_pre = pre_idx[max(0, len(pre_idx) - 400):]
                        resid = tot_s["value"][tail_pre] - b_sum[tail_pre]
                        rms_tot = max(float(np.sqrt(np.mean(tot_s["value"][tail_pre] ** 2))), 1e-9)
                        if float(np.sqrt(np.mean(resid ** 2))) / rms_tot <= 0.02:
                            mean_off = float(np.mean(resid))
                            tot_s["value"][tot_rec] = b_sum[tot_rec] + mean_off
            except Exception:
                pass

    # Aggregate summary metrics across matched roles
    all_overlap = [int(r.get("overlap_count", 0)) for r in role_reports.values()]
    all_rec = [int(r.get("recovered_count", 0)) for r in role_reports.values()]
    any_div = any(bool(r.get("overlap_divergent", False)) for r in role_reports.values())
    max_vdiff = max((float(r.get("overlap_max_val_diff", 0.0)) for r in role_reports.values()), default=0.0)
    max_tdiff = max((float(r.get("overlap_max_time_diff", 0.0)) for r in role_reports.values()), default=0.0)

    nrmse_vals = [float(r["val_nrmse_pct"]) for r in role_reports.values() if r.get("val_nrmse_pct") is not None]
    mean_nrmse = float(np.mean(nrmse_vals)) if nrmse_vals else None

    primary_rep: Dict[str, Any] = {}
    if matched_roles:
        primary_rep = (
            next((role_reports[k] for k in matched_roles if role_reports.get(k, {}).get("recovered_count", 0) > 0), None)
            or next((role_reports[k] for k in matched_roles if role_reports.get(k, {}).get("status") == "gap_unfilled"), None)
            or next((role_reports[k] for k in matched_roles if role_reports.get(k, {}).get("overlap_divergent")), None)
            or next((role_reports[k] for k in matched_roles if role_reports.get(k, {}).get("status") != "incompatible_backup"), None)
            or role_reports.get(next(iter(matched_roles), ""), {})
        )
    rec_step_ranges = primary_rep.get("recovered_step_ranges", [])
    rec_time_ranges = primary_rep.get("recovered_time_ranges", [])
    splice_steps = primary_rep.get("splice_steps", [])
    splice_times = primary_rep.get("splice_times", [])
    method = primary_rep.get("method", "none")

    max_ov = max(all_overlap, default=0)
    max_rec = max(all_rec, default=0)

    all_incompatible = bool(
        matched_roles and all(
            role_reports.get(k, {}).get("status") == "incompatible_backup"
            for k in matched_roles
        )
    )

    if not matched_roles:
        overall_status = "no_match"
        summary_text = f"Prior source '{loaded['source_label']}' had 0 matching monitor roles."
    elif all_incompatible:
        overall_status = "incompatible_backup"
        b_rng = primary_rep.get("backup_step_range")
        c_rng = primary_rep.get("current_step_range")
        b_txt = f"{b_rng[0]}–{b_rng[1]}" if b_rng else "unknown"
        c_txt = f"{c_rng[0]}–{c_rng[1]}" if c_rng else "unknown"
        summary_text = (
            f"Ignored incompatible prior source '{loaded['source_label']}' "
            f"(backup steps {b_txt} do not precede current run steps {c_txt}; using current run only)."
        )
    elif max_rec > 0:
        overall_status = "gap_recovered"
        rng_txt = ", ".join(f"{a}–{b}" for a, b in rec_step_ranges) if rec_step_ranges else "gap"
        acc_txt = f" | Est. NRMSE: {mean_nrmse:.2f}%" if mean_nrmse is not None else ""
        summary_text = (
            f"Linked {len(matched_roles)}/{len(current_series)} files from '{loaded['source_label']}' • "
            f"Recovered {max_rec} missing step(s) ({rng_txt}) via {method}{acc_txt}."
        )
    elif primary_rep.get("status") == "gap_unfilled":
        overall_status = "gap_unfilled"
        n_miss = int(primary_rep.get("missing_step_count", 0))
        summary_text = (
            f"Linked {len(matched_roles)}/{len(current_series)} files from '{loaded['source_label']}' • "
            f"{n_miss} missing step(s) detected (gap recovery disabled in Splice-Only mode)."
        )
    elif max_ov > 0:
        overall_status = "overlap_divergent" if any_div else "overlap_verified"
        ov_rng = primary_rep.get("overlap_step_range")
        ov_str = f" ({ov_rng[0]}–{ov_rng[1]})" if ov_rng else ""
        if any_div:
            summary_text = (
                f"Linked {len(matched_roles)}/{len(current_series)} files from '{loaded['source_label']}' • "
                f"{max_ov}-step overlap{ov_str} DIVERGES slightly (max |Δval|={max_vdiff:.3e}; spliced)."
            )
        else:
            summary_text = (
                f"Linked {len(matched_roles)}/{len(current_series)} files from '{loaded['source_label']}' • "
                f"{max_ov}-step checkpoint overlap{ov_str} verified exact (max Δ={max_vdiff:.2e})."
            )
    else:
        overall_status = "exact_adjacent"
        summary_text = (
            f"Linked {len(matched_roles)}/{len(current_series)} files from '{loaded['source_label']}' • "
            f"Exact step-to-step continuation."
        )

    if unmatched_roles and matched_roles:
        summary_text += f" ({len(unmatched_roles)} role(s) not present in backup)."

    if log is not None:
        log(f"Run continuity: {summary_text}")

    summary = {
        "enabled": True,
        "status": overall_status,
        "source_label": loaded["source_label"],
        "is_zip": loaded["is_zip"],
        "mode": mode,
        "matched_roles": matched_roles,
        "unmatched_roles": unmatched_roles,
        "role_reports": role_reports,
        "overlap_count": max_ov,
        "overlap_divergent": any_div,
        "overlap_max_val_diff": max_vdiff,
        "overlap_max_time_diff": max_tdiff,
        "recovered_count": max_rec,
        "recovered_step_ranges": rec_step_ranges,
        "recovered_time_ranges": rec_time_ranges,
        "splice_steps": splice_steps,
        "splice_times": splice_times,
        "method": method,
        "mean_validation_nrmse_pct": mean_nrmse,
        "summary_text": summary_text,
    }
    return merged, summary


def write_fluent_report_file(
    out_path: str,
    step: np.ndarray,
    value: np.ndarray,
    time_arr: Optional[np.ndarray],
    column_name: str = "monitor",
    report_title: Optional[str] = None,
) -> None:
    """Write a Fluent-compatible 3-line header report file (.out) from stitched/recovered arrays."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    title = report_title or os.path.splitext(os.path.basename(out_path))[0]
    has_t = time_arr is not None and len(time_arr) == len(step)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f'"{title}"\n')
        if has_t and time_arr is not None:
            fh.write(f'"Time Step" "{column_name}" "flow-time"\n')
            fh.write(f'("Time Step" "{column_name}" "flow-time")\n')
            for s, v, t in zip(step, value, time_arr):
                fh.write(f"{int(s)} {float(v):.12g} {float(t):.12g}\n")
        else:
            fh.write(f'"Time Step" "{column_name}"\n')
            fh.write(f'("Time Step" "{column_name}")\n')
            for s, v in zip(step, value):
                fh.write(f"{int(s)} {float(v):.12g}\n")


def export_stitched_report_files(
    f: InputFiles,
    out_dir: str,
    p: Optional[Params] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Stitch prior_source + current InputFiles and write clean Fluent .out files to out_dir."""
    if not f.prior_source:
        raise ValueError("Please link a backup folder or .zip archive first.")

    eff_mode = mode or getattr(f, "continuity_mode", "auto") or "auto"
    if eff_mode == "off":
        eff_mode = "auto"

    role_paths = _map_input_files_to_roles(f)
    if not role_paths:
        raise ValueError("No current monitor files are selected to stitch.")

    curr_series: Dict[str, Dict[str, Any]] = {}
    for role, path in role_paths.items():
        if path and os.path.isfile(path):
            curr_series[role] = read_fluent_report(path)

    merged, summary = stitch_and_recover_all(
        curr_series,
        prior_source=f.prior_source,
        p=p,
        mode=eff_mode,
        current_files=f,
    )

    os.makedirs(out_dir, exist_ok=True)
    written: Dict[str, str] = {}
    for role, m_s in merged.items():
        orig_path = role_paths.get(role)
        bname = os.path.basename(orig_path) if orig_path else f"{role}-stitched.out"
        dest = os.path.join(out_dir, bname)
        col_name = m_s.get("selected_column") or role
        write_fluent_report_file(
            dest,
            step=m_s["step"],
            value=m_s["value"],
            time_arr=m_s.get("time"),
            column_name=str(col_name),
            report_title=os.path.splitext(bname)[0],
        )
        written[role] = dest

    report_path = os.path.join(out_dir, "_continuity_report.txt")
    with open(report_path, "w", encoding="utf-8") as rf:
        rf.write("TurbineScope Continuity & Recovery Report\n")
        rf.write("=" * 45 + "\n")
        rf.write(f"Summary: {summary.get('summary_text', '')}\n")
        rf.write(f"Prior Source: {f.prior_source}\n")
        rf.write(f"Mode: {eff_mode}\n")
        rf.write(f"Recovered Steps: {summary.get('recovered_count', 0)}\n")
        rf.write(f"Recovered Ranges: {summary.get('recovered_step_ranges', [])}\n")
        rf.write(f"Overlap Steps: {summary.get('overlap_count', 0)}\n")
        rf.write(f"Method: {summary.get('method', '-')}\n")
        if summary.get("mean_validation_nrmse_pct") is not None:
            rf.write(f"Backtest NRMSE: {summary['mean_validation_nrmse_pct']:.3f}%\n")
        rf.write("\nExported Files:\n")
        for role, wp in written.items():
            rf.write(f"  - {role}: {wp} ({len(merged[role]['step'])} rows)\n")

    return {
        "written_files": written,
        "report_path": report_path,
        "summary": summary,
    }
