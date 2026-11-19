# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Specialized Analysis Graphs & Animations (Tier 3.5)
=============================================================================
Provides 9 specialized engineering visualizations and diagnostics:
1. Per-azimuth convergence (Cp vs revolution at selected azimuths)
2. Upwind / downwind energy split (grouped bars per revolution)
3. Cumulative energy extracted vs azimuth (integral of torque)
4. Fx-Fy locus (load rose) and structural force spectra
5. Spectrogram of Cp across simulation with harmonic orders (0.5x, 1x, Nx, 2Nx)
6. Torque-ripple factor per revolution
7. Wall y+ monitor importer, limit line, and near-wall resolution check
8. Animated rotor Cp wheel (GIF) with rotating blades and moving marker
9. Data-quality visual dashboard panel (pass/fail status cards)
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import matplotlib
if matplotlib.get_backend().lower() not in ("agg", "qtagg"):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image

PAL = ["#1F4E79", "#D1495B", "#2A9D8F", "#E9A03B", "#6A4C93", "#6C757D", "#118AB2", "#8D6E63"]


def _azaxis(ax, upwind: bool = True) -> None:
    ax.set_xlim(0, 360)
    ax.set_xticks(range(0, 361, 60))
    ax.set_xlabel("Blade azimuth θ (deg)  —  0° = top moving into wind")
    if upwind:
        ax.axvspan(0, 180, color="#EAF1F8", alpha=0.55, lw=0, zorder=0)
        ax.text(90, 1.0, "upwind half", transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=8, color="#5B7C99")
        ax.text(270, 1.0, "downwind half", transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=8, color="#8A8A8A")


# =============================================================================
# 1. Per-Azimuth Convergence
# =============================================================================
def plot_per_azimuth_convergence(
    R: Dict[str, Any],
    outpath: Optional[str] = None,
    target_azimuths: Optional[List[float]] = None
) -> str:
    """Plot Cp at selected azimuths versus revolution to reveal sector drift and period-2 oscillations."""
    if target_azimuths is None:
        target_azimuths = [0.0, 60.0, 90.0, 180.0, 270.0, 330.0]

    d, revs = R["data"], R["revs"]
    t = d["t"]
    th_deg = d["th1"] % 360.0

    ks = [r["k"] for r in revs]
    curves: Dict[float, List[float]] = {az: [] for az in target_azimuths}

    for r in revs:
        m_win = (t >= r["a"]) & (t <= r["b"])
        th_w = th_deg[m_win]
        cp_w = d["Cp"][m_win]

        if len(th_w) < 5:
            for az in target_azimuths:
                curves[az].append(float("nan"))
            continue

        sort_idx = np.argsort(th_w)
        th_s = th_w[sort_idx]
        cp_s = cp_w[sort_idx]

        th_extended = np.concatenate([th_s - 360.0, th_s, th_s + 360.0])
        cp_extended = np.concatenate([cp_s, cp_s, cp_s])

        for az in target_azimuths:
            val = float(np.interp(az, th_extended, cp_extended))
            curves[az].append(val)

    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=130)
    try:
        for i, az in enumerate(target_azimuths):
            color = PAL[i % len(PAL)]
            ax.plot(ks, curves[az], "o-", label=f"θ = {az:.0f}°", color=color, lw=1.6, ms=4)

        ax.set_xlabel("Complete Revolution (counted back from end)")
        ax.set_ylabel("Instantaneous Cp at Azimuth")
        ax.set_title("Per-Azimuth Convergence across Revolutions (Sector Stability & Drift)")
        ax.set_xticks(ks)
        ax.grid(True, alpha=0.3)
        ax.legend(ncol=3, loc="best", fontsize=9)

        if outpath is None:
            outpath = "13_per_azimuth_convergence.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath


# =============================================================================
# 2. Upwind / Downwind Split
# =============================================================================
def plot_upwind_downwind_split(R: Dict[str, Any], outpath: Optional[str] = None) -> str:
    """Plot grouped bar chart of upwind (0°-180°) vs downwind (180°-360°) Cp per revolution."""
    p, d, revs = R["p"], R["data"], R["revs"]
    t = d["t"]
    have_blades = bool(d.get("have_blades")) and bool(d.get("Cp_b")) and bool(d.get("TH"))
    th_deg = d["th1"] % 360.0

    ks = [r["k"] for r in revs]
    upwind_cps: List[float] = []
    downwind_cps: List[float] = []

    for r in revs:
        m_win = (t >= r["a"]) & (t <= r["b"])
        if have_blades:
            # Sum per-blade upwind/downwind sector contributions so multi-blade rotors
            # reflect true upwind vs downwind aerodynamic extraction rather than Blade-1 phase gating.
            nb = min(len(d["Cp_b"]), len(d["TH"]))
            cp_up = 0.0
            cp_down = 0.0
            for b in range(nb):
                th_b = d["TH"][b][m_win] % 360.0
                cp_b = d["Cp_b"][b][m_win]
                m_up_b = (th_b >= 0.0) & (th_b < 180.0)
                m_dn_b = (th_b >= 180.0) & (th_b < 360.0)
                if np.any(m_up_b):
                    cp_up += float(np.mean(cp_b[m_up_b]))
                if np.any(m_dn_b):
                    cp_down += float(np.mean(cp_b[m_dn_b]))
        else:
            th_w = th_deg[m_win]
            cp_w = d["Cp"][m_win]
            m_up = (th_w >= 0.0) & (th_w < 180.0)
            m_down = (th_w >= 180.0) & (th_w < 360.0)
            cp_up = float(np.mean(cp_w[m_up])) if np.any(m_up) else 0.0
            cp_down = float(np.mean(cp_w[m_down])) if np.any(m_down) else 0.0

        upwind_cps.append(cp_up)
        downwind_cps.append(cp_down)

    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=130)
    try:
        x = np.arange(len(ks))
        width = 0.38

        ax.bar(x - width / 2, upwind_cps, width, label="Upwind Half (0°–180°)", color="#1F4E79", edgecolor="white")
        ax.bar(x + width / 2, downwind_cps, width, label="Downwind Half (180°–360°)", color="#D1495B", edgecolor="white")

        sel_up = [upwind_cps[i] for i, r in enumerate(revs) if r["selected"]]
        sel_down = [downwind_cps[i] for i, r in enumerate(revs) if r["selected"]]
        mean_up = float(np.mean(sel_up)) if sel_up else 0.0
        mean_down = float(np.mean(sel_down)) if sel_down else 0.0
        net_tot = mean_up + mean_down
        if net_tot > 1e-6:
            share_txt = f"Upwind Share: {(mean_up / net_tot) * 100.0:.1f}%"
        else:
            share_txt = "Upwind Share: n/a (net Cp <= 0)"

        ax.axhline(mean_up, color="#1F4E79", ls="--", lw=1.1, alpha=0.8, label=f"Reported Upwind Mean: {mean_up:.3f}")
        ax.axhline(mean_down, color="#D1495B", ls="--", lw=1.1, alpha=0.8, label=f"Reported Downwind Mean: {mean_down:.3f}")

        ax.set_xticks(x)
        ax.set_xticklabels([str(k) for k in ks])
        ax.set_xlabel("Revolution")
        ax.set_ylabel("Sector-Averaged Power Coefficient Cp")
        title_note = "" if (have_blades or (p.n_blades or 1) <= 1) else " [Total-Only Phase Split]"
        ax.set_title(f"Upwind vs Downwind Power Extraction per Revolution ({share_txt}){title_note}")
        ax.grid(True, alpha=0.3)
        ax.legend(ncol=2, loc="upper right", fontsize=8.5)

        if outpath is None:
            outpath = "14_upwind_downwind_split.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath


# =============================================================================
# 3. Cumulative Energy vs Azimuth
# =============================================================================
def plot_cumulative_energy(R: Dict[str, Any], outpath: Optional[str] = None) -> str:
    """Plot cumulative aerodynamic energy extracted over azimuth: E(θ) = ∫ M(θ') dθ'."""
    p, d, az, res = R["p"], R["data"], R["az"], R["res"]
    th = az["theta"]
    th_rad = np.radians(th)

    # Rotor moment M(θ)
    m_rot = az["cm_mean"] * res["Mref"]
    # Cumulative trapezoidal integral
    e_rot = np.zeros_like(th)
    for i in range(1, len(th)):
        dth = th_rad[i] - th_rad[i - 1]
        e_rot[i] = e_rot[i - 1] + 0.5 * (m_rot[i] + m_rot[i - 1]) * dth

    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=130)
    try:
        _azaxis(ax, upwind=True)

        ax.plot(th, e_rot, color="#1F4E79", lw=2.4, label=f"Total Rotor Energy (Net: {e_rot[-1]:.2f} J/m)")

        if d["have_blades"] and "M_b_mean" in az:
            for b_idx in range(p.n_blades):
                mb = az["M_b_mean"][b_idx]
                eb = np.zeros_like(th)
                for i in range(1, len(th)):
                    dth = th_rad[i] - th_rad[i - 1]
                    eb[i] = eb[i - 1] + 0.5 * (mb[i] + mb[i - 1]) * dth
                ax.plot(th, eb, lw=1.2, ls="--", color=PAL[(b_idx + 1) % len(PAL)], label=f"Blade {b_idx + 1} ({eb[-1]:.2f} J/m)")

        ax.axhline(0, color="gray", lw=0.8, ls=":")
        ax.set_ylabel("Cumulative Energy Extracted E(θ) [J per m depth]")
        ax.set_title("Cumulative Energy Extraction vs Azimuth: E(θ) = ∫ M dθ (Positive Slope = Power Generation)")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left", fontsize=9)

        if outpath is None:
            outpath = "15_cumulative_energy.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath


# =============================================================================
# 4. Fx-Fy Locus (Load Rose) & Spectra
# =============================================================================
def plot_load_rose(R: Dict[str, Any], outpath: Optional[str] = None) -> str:
    """Plot Fx-Fy structural load rose locus, azimuthal thrust/side-force, and harmonic spectra."""
    p, d, az, sel = R["p"], R["data"], R["az"], R["sel"]

    fig, axs = plt.subplots(2, 2, figsize=(12, 9), dpi=130)

    if d["Ct"] is None and d["Cy"] is None:
        for ax in axs.flat:
            ax.text(0.5, 0.5, "Thrust (Fx) & Side-force (Fy) monitor files required for load rose.", ha="center", va="center")
            ax.axis("off")
    else:
        th = az["theta"]
        ct_m = az.get("ct_mean", np.zeros_like(th))
        cy_m = az.get("cy_mean", np.zeros_like(th))

        # Panel 1: Load Rose (Ct vs Cy locus)
        ax1 = axs[0, 0]
        ax1.plot(cy_m, ct_m, color="#1F4E79", lw=2.2, label="Mean Locus")
        ax1.plot(cy_m[0], ct_m[0], "go", ms=7, label="θ = 0° (Top)")
        ax1.plot(cy_m[len(th) // 2], ct_m[len(th) // 2], "ro", ms=7, label="θ = 180° (Bottom)")
        ax1.axhline(0, color="gray", lw=0.8, ls=":")
        ax1.axvline(0, color="gray", lw=0.8, ls=":")
        ax1.set_xlabel("Side Force Coefficient Cy (Fy)")
        ax1.set_ylabel("Thrust Coefficient Ct (Fx)")
        ax1.set_title("Rotor Load Rose Locus (Ct vs Cy)")
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=8, loc="best")

        # Panel 2: Ct and Cy vs Azimuth
        ax2 = axs[0, 1]
        _azaxis(ax2, upwind=True)
        ax2.plot(th, ct_m, color="#1F4E79", lw=2, label="Ct (Thrust)")
        ax2.plot(th, cy_m, color="#D1495B", lw=2, label="Cy (Side Force)")
        ax2.axhline(0, color="gray", lw=0.8, ls=":")
        ax2.set_ylabel("Force Coefficient")
        ax2.set_title("Thrust and Side-Force vs Azimuth")
        ax2.legend(fontsize=8.5)

        # Panel 3: Ct Spectrum (per-revolution uniform resampling so multi-stage / non-contiguous sel never distort FFT)
        ax3 = axs[1, 0]
        t = d["t"]
        n_fft_grid = 1024

        def _rev_avg_spectrum(sig: Optional[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
            if sig is None or len(t) < 4:
                return np.array([], dtype=float), np.array([], dtype=float)
            rev_specs: List[np.ndarray] = []
            for r_s in sel:
                m_r = (t >= r_s["a"] - 1e-9) & (t <= r_s["b"] + 1e-9)
                if int(np.sum(m_r)) < 4:
                    continue
                t_r = t[m_r]
                s_r = sig[m_r]
                if t_r[-1] <= t_r[0]:
                    continue
                u_t = np.linspace(float(r_s["a"]), float(r_s["b"]), n_fft_grid, endpoint=False)
                u_s = np.interp(u_t, t_r, s_r)
                rev_specs.append(2.0 * np.abs(np.fft.rfft(u_s - np.mean(u_s))) / n_fft_grid)
            if not rev_specs:
                return np.array([], dtype=float), np.array([], dtype=float)
            harm_idx = np.arange(len(rev_specs[0]), dtype=float)
            return harm_idx, np.mean(rev_specs, axis=0)

        h_ct, amps_ct = _rev_avg_spectrum(d["Ct"])
        if len(h_ct) > 0:
            m_h = (h_ct >= 0) & (h_ct <= 12)
            ax3.vlines(h_ct[m_h], 0, amps_ct[m_h], color="#1F4E79", lw=1.4)
            ax3.axvline(p.n_blades, color="#D1495B", ls="--", lw=1.2, label=f"{p.n_blades}x Blade Passing")
        ax3.set_xlabel("Harmonic Order (f / f_rotor)")
        ax3.set_ylabel("Ct Amplitude")
        ax3.set_title("Thrust Coefficient Ct Dynamic Spectrum")
        ax3.set_xlim(0, 12)
        ax3.grid(True, alpha=0.3)
        ax3.legend(fontsize=8.5)

        # Panel 4: Cy Spectrum
        ax4 = axs[1, 1]
        h_cy, amps_cy = _rev_avg_spectrum(d["Cy"])
        if len(h_cy) > 0:
            m_h_y = (h_cy >= 0) & (h_cy <= 12)
            ax4.vlines(h_cy[m_h_y], 0, amps_cy[m_h_y], color="#D1495B", lw=1.4)
            ax4.axvline(p.n_blades, color="#1F4E79", ls="--", lw=1.2, label=f"{p.n_blades}x Blade Passing")
        ax4.set_xlabel("Harmonic Order (f / f_rotor)")
        ax4.set_ylabel("Cy Amplitude")
        ax4.set_title("Side-Force Coefficient Cy Dynamic Spectrum")
        ax4.set_xlim(0, 12)
        ax4.grid(True, alpha=0.3)
        ax4.legend(fontsize=8.5)

    try:
        plt.tight_layout()
        if outpath is None:
            outpath = "16_load_rose.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath


# =============================================================================
# 5. Spectrogram of Cp across Simulation
# =============================================================================
def plot_cp_spectrogram(R: Dict[str, Any], outpath: Optional[str] = None) -> str:
    """Plot short-time Fourier transform (spectrogram) of Cp over entire simulation."""
    p, d, res = R["p"], R["data"], R["res"]
    t, cp, Trev = d["t"], d["Cp"], res["Trev"]

    fig, ax = plt.subplots(figsize=(11, 5.0), dpi=130)
    try:
        # Pure NumPy STFT implementation
        n_pts = len(t)
        rendered_spec = False
        if n_pts >= 128 and float(t[-1] - t[0]) > 0:
            # Uniform resample for FFT
            grid_n = min(n_pts, 8192)
            t_unif = np.linspace(t[0], t[-1], grid_n)
            dt_unif = (t[-1] - t[0]) / (grid_n - 1)
            cp_unif = np.interp(t_unif, t, cp)

            pts_per_rev = Trev / dt_unif
            window_size = int(max(64, round(pts_per_rev * 2.0)))  # 2 revolutions per FFT window
            step_size = max(8, window_size // 4)                 # 75% overlap

            hann = np.hanning(window_size)
            spec_cols = []
            t_centers = []

            for start_idx in range(0, grid_n - window_size + 1, step_size):
                segment = cp_unif[start_idx:start_idx + window_size]
                seg_hann = (segment - np.mean(segment)) * hann
                fft_vals = np.fft.rfft(seg_hann)
                mag = (2.0 / window_size) * np.abs(fft_vals)
                spec_cols.append(mag)
                t_centers.append(t_unif[start_idx + window_size // 2])

            if spec_cols:
                spec_matrix = np.column_stack(spec_cols)
                freqs = np.fft.rfftfreq(window_size, d=dt_unif)
                harmonics = freqs * Trev

                # Limit harmonic range to [0, 12]
                m_h = harmonics <= 12.0
                h_sub = harmonics[m_h]
                mat_sub = spec_matrix[m_h, :]

                t_rev_centers = (np.array(t_centers) - t[0]) / Trev

                im = ax.pcolormesh(
                    t_rev_centers, h_sub, mat_sub,
                    shading="gouraud", cmap="inferno"
                )
                fig.colorbar(im, ax=ax, label="Cp Harmonic Amplitude")

                # Reference harmonic lines
                ax.axhline(0.5, color="cyan", ls=":", lw=1.1, alpha=0.9, label="0.5x Subharmonic (Period-2)")
                ax.axhline(1.0, color="lime", ls="--", lw=1.1, alpha=0.9, label="1.0x Rotor Frequency")
                ax.axhline(p.n_blades, color="white", ls="-.", lw=1.3, alpha=0.95, label=f"{p.n_blades}x Blade Passing")
                ax.axhline(2 * p.n_blades, color="orange", ls="--", lw=1.1, alpha=0.8, label=f"{2 * p.n_blades}x 2nd BPF")

                ax.set_ylim(0, 12)
                ax.set_xlabel("Revolutions of Flow Time")
                ax.set_ylabel("Frequency / Rotor Frequency (Harmonic Order)")
                ax.set_title("Spectrogram of Rotor Cp across Simulation (Vortex Shedding & Transition Tracking)")
                ax.legend(loc="upper right", fontsize=8.5, facecolor="#222", labelcolor="white")
                rendered_spec = True

        if not rendered_spec:
            ax.text(0.5, 0.5, "Simulation duration insufficient for spectrogram analysis.", ha="center", va="center")
            ax.axis("off")

        if outpath is None:
            outpath = "17_cp_spectrogram.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath


# =============================================================================
# 6. Torque-Ripple Factor per Revolution
# =============================================================================
def plot_torque_ripple(R: Dict[str, Any], outpath: Optional[str] = None) -> str:
    """Plot torque-ripple factor TR = (Cm,max - Cm,min) / Cm,mean per complete revolution."""
    revs, res = R["revs"], R["res"]

    ks = [r["k"] for r in revs]
    ripples: List[float] = []

    for r in revs:
        cm_mean = abs(r["cm"])
        if cm_mean > 1e-6:
            tr = (r["cp_max"] - r["cp_min"]) / max(1e-6, abs(r["cp"]))
        else:
            tr = float("nan")
        ripples.append(tr)

    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=130)
    try:
        colors = [PAL[3] if r["selected"] else ("#9DB4C8" if r["stable"] else "#CFCFCF") for r in revs]
        ax.bar(ks, ripples, color=colors, edgecolor="white", width=0.6)

        for k_val, rip in zip(ks, ripples):
            if not math.isnan(rip):
                ax.text(k_val, rip, f"{rip:.2f}", ha="center", va="bottom", fontsize=8)

        mean_rip = res.get("ripple_pp", float("nan"))
        if not math.isnan(mean_rip):
            ax.axhline(mean_rip, color=PAL[1], ls="--", lw=1.2, label=f"Reported Mean Ripple = {mean_rip:.2f}")

        ax.set_xticks(ks)
        ax.set_xlabel("Complete Revolution")
        ax.set_ylabel("Torque Ripple Factor TR = (Cp_max - Cp_min) / Cp_mean")
        ax.set_title("Torque-Ripple Factor per Revolution (Dynamic Stall & Harmonic Oscillation Severity)")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)

        if outpath is None:
            outpath = "18_torque_ripple.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath


# =============================================================================
# 7. Wall y+ Monitor Import & Resolution Diagnostic
# =============================================================================
def import_yplus_monitor(path: str) -> Dict[str, Any]:
    """Parse a Fluent wall y+ monitor file (max and/or mean y+ on blade walls)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"y+ monitor file not found: {path}")

    # Prefer structured Fluent report header parsing whenever an explicit flow-time column is present
    try:
        from vawt_post.engine.parser import inspect_fluent_report, read_fluent_report
        meta = inspect_fluent_report(path)
        if meta.get("time_col") is not None and meta.get("data_columns"):
            rep = read_fluent_report(path)
            st_arr = np.asarray(rep["step"], dtype=int)
            yp_arr = np.asarray(rep["value"], dtype=float)
            t_raw = rep.get("time")
            t_arr = np.asarray(t_raw, dtype=float) if t_raw is not None else st_arr.astype(float)
            fin = np.isfinite(yp_arr)
            return {
                "step": st_arr[fin],
                "time": t_arr[fin],
                "yplus": yp_arr[fin],
                "max": float(np.max(yp_arr[fin])) if np.any(fin) else 0.0,
                "mean": float(np.mean(yp_arr[fin])) if np.any(fin) else 0.0,
            }
    except Exception:
        pass

    steps: List[int] = []
    times: List[float] = []
    yplus_vals: List[float] = []

    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("(") or line.startswith('"'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    s = int(parts[0])
                    # Headerless fallback: 3 parts -> (step, time, yplus); 2 parts -> (step, yplus)
                    if len(parts) >= 3:
                        t = float(parts[1])
                        yp = float(parts[2])
                    else:
                        t = float(s)
                        yp = float(parts[1])
                    steps.append(s)
                    times.append(t)
                    yplus_vals.append(yp)
                except ValueError:
                    continue

    return {
        "step": np.array(steps, dtype=int),
        "time": np.array(times, dtype=float),
        "yplus": np.array(yplus_vals, dtype=float),
        "max": float(np.max(yplus_vals)) if yplus_vals else 0.0,
        "mean": float(np.mean(yplus_vals)) if yplus_vals else 0.0,
    }


def plot_yplus_monitor(
    yplus_data: Dict[str, Any],
    outpath: Optional[str] = None,
    limit: float = 1.0
) -> Tuple[str, Dict[str, Any]]:
    """Plot wall y+ history and verify near-wall boundary layer resolution using 3 tiers.

    Tiers for low-Re models:
      y+ <= 1.0: Good (viscous sublayer resolved)
      1.0 < y+ <= 5.0: Caution (transitional/buffer sublayer)
      y+ > 5.0: Poor (viscous sublayer unresolved for low-Re models)
    Reports mean and maximum y+ separately.
    """
    t = yplus_data["time"]
    yp = yplus_data["yplus"]
    max_yp = float(np.max(yp)) if len(yp) > 0 else 0.0
    mean_yp = float(np.mean(yp)) if len(yp) > 0 else 0.0

    good_lim = float(limit) if limit and limit > 0 else 1.0
    caution_lim = max(5.0, good_lim * 2.0) if good_lim <= 1.0 else good_lim * 2.0

    if max_yp <= good_lim:
        tier = "Good"
        passed = True
        status_str = f"GOOD (mean = {mean_yp:.2f}, max = {max_yp:.2f} <= {good_lim:.1f})"
        msg = f"Wall y+ is optimal (mean = {mean_yp:.2f}, max = {max_yp:.2f} <= {good_lim:.1f}) - viscous sublayer fully resolved."
    elif max_yp <= caution_lim:
        tier = "Caution"
        passed = True
        status_str = f"CAUTION (mean = {mean_yp:.2f}, max = {max_yp:.2f} <= {caution_lim:.1f})"
        msg = f"Wall y+ is in caution range (mean = {mean_yp:.2f}, max = {max_yp:.2f} <= {caution_lim:.1f}) - transitional buffer layer; low-Re models acceptable with minor damping."
    else:
        tier = "Poor"
        passed = False
        status_str = f"POOR (mean = {mean_yp:.2f}, max = {max_yp:.2f} > {caution_lim:.1f})"
        msg = f"Wall y+ is poor (mean = {mean_yp:.2f}, max = {max_yp:.2f} > {caution_lim:.1f}) - viscous sublayer unresolved for low-Re turbulence models."

    diag = {
        "passed": passed,
        "tier": tier,
        "mean_yplus": mean_yp,
        "max_yplus": max_yp,
        "limit": good_lim,
        "message": msg,
    }

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=130)
    try:
        ax.plot(t, yp, color="#1F4E79", lw=1.2, label=f"Monitored Wall y+ (mean = {mean_yp:.2f}, max = {max_yp:.2f})")
        ax.axhline(good_lim, color="#10B981", ls="--", lw=1.5, label=f"Good Limit ($y^+ \\leq {good_lim:.1f}$)")
        ax.axhline(caution_lim, color="#F59E0B", ls=":", lw=1.5, label=f"Caution Limit (${good_lim:.1f} < y^+ \\leq {caution_lim:.1f}$)")

        ax.set_xlabel("Simulation Flow Time (s)")
        ax.set_ylabel("Wall y+ on Turbine Blades")
        ax.set_title(f"Wall y+ Boundary Layer Resolution Check [{status_str}]")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)

        if outpath is None:
            outpath = "19_yplus_monitor.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath, diag


# =============================================================================
# 8. Animated Cp Wheel (GIF)
# =============================================================================
def create_animated_cp_wheel(
    R: Dict[str, Any],
    outpath: Optional[str] = None,
    n_frames: int = 36
) -> str:
    """Generate an animated GIF showing the rotating rotor and the corresponding position on the Cp(θ) curve."""
    p, az = R["p"], R["az"]
    th_curve = az["theta"]
    cp_curve = az["cp_mean"]
    R_geom = p.R if p.R else 1.0
    n_blades = p.n_blades if p.n_blades else 3

    frames_pil = []
    theta_steps = np.linspace(0, 360, n_frames, endpoint=False)

    for frame_idx, th_cur in enumerate(theta_steps):
        fig, (ax_wheel, ax_plot) = plt.subplots(1, 2, figsize=(11, 5.0), dpi=100)
        try:
            # 1. Left Wheel Representation
            ax_wheel.set_xlim(-1.4 * R_geom, 1.4 * R_geom)
            ax_wheel.set_ylim(-1.4 * R_geom, 1.4 * R_geom)
            ax_wheel.set_aspect("equal")
            ax_wheel.axis("off")

            # Rotor circular trajectory
            rotor_circle = patches.Circle((0, 0), R_geom, color="#C9D8E6", fill=False, ls="--", lw=1.5)
            shaft_circle = patches.Circle((0, 0), 0.12 * R_geom, color="#6C757D", fill=True)
            ax_wheel.add_patch(rotor_circle)
            ax_wheel.add_patch(shaft_circle)

            # Wind direction arrow from left
            ax_wheel.annotate(
                "Wind U∞", xy=(-0.9 * R_geom, 0), xytext=(-1.35 * R_geom, 0),
                arrowprops=dict(facecolor="#1F4E79", edgecolor="none", width=2.5, headwidth=8),
                fontsize=9, fontweight="bold", color="#1F4E79", va="center"
            )

            # Blade positions
            blade_spacing_deg = 360.0 / n_blades
            for b in range(n_blades):
                # With wind from left (+x): CCW starts at top (+90 deg) moving +theta;
                # CW starts at bottom (-90 deg) moving -theta so upwind theta=90 deg stays on the left.
                b_th_deg = (th_cur + b * blade_spacing_deg) % 360.0
                angle_rad = np.radians(-90.0 - b_th_deg if p.rotation_dir == "cw" else 90.0 + b_th_deg)
                bx = R_geom * np.cos(angle_rad)
                by = R_geom * np.sin(angle_rad)

                # Strut
                ax_wheel.plot([0, bx], [0, by], color="#9DB4C8", lw=1.2)
                # Blade airfoil marker
                color_b = PAL[0] if b == 0 else PAL[(b + 1) % len(PAL)]
                blade_dot = patches.Circle((bx, by), 0.08 * R_geom, color=color_b, zorder=5)
                ax_wheel.add_patch(blade_dot)
                ax_wheel.text(bx * 1.18, by * 1.18, f"B{b + 1}", ha="center", va="center", fontsize=8, fontweight="bold", color=color_b)

            ax_wheel.set_title(f"Rotor Configuration (θ1 = {th_cur:.0f}°)", fontsize=11, fontweight="bold")

            # 2. Right Plot Representation: Cp vs Azimuth with current marker
            _azaxis(ax_plot, upwind=True)
            ax_plot.plot(th_curve, cp_curve, color="#1F4E79", lw=2, label="Mean Cp(θ)")

            cur_cp = float(np.interp(th_cur, th_curve, cp_curve))
            ax_plot.plot(th_cur, cur_cp, "ro", ms=8, zorder=6, label=f"Instantaneous (θ={th_cur:.0f}°, Cp={cur_cp:.3f})")
            ax_plot.axvline(th_cur, color="red", ls=":", lw=1.1, alpha=0.7)
            ax_plot.axhline(0, color="gray", lw=0.8, ls=":")

            ax_plot.set_ylabel("Instantaneous Rotor Cp")
            ax_plot.set_title("Power Extraction over Azimuthal Cycle", fontsize=11, fontweight="bold")
            ax_plot.legend(loc="upper right", fontsize=8.5)

            fig.canvas.draw()
            buf_fn = getattr(fig.canvas, "buffer_rgba")
            rgba = np.asarray(buf_fn())
            im_pil = Image.fromarray(rgba)
            frames_pil.append(im_pil)
        finally:
            plt.close(fig)

    if outpath is None:
        outpath = "20_animated_cp_wheel.gif"
    os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
    if frames_pil:
        frames_pil[0].save(
            outpath,
            save_all=True,
            append_images=frames_pil[1:],
            duration=100,  # 100 ms per frame -> 10 fps
            loop=0
        )
    return outpath


# =============================================================================
# 9. Data-Quality Visual Dashboard Panel
# =============================================================================
def plot_qa_dashboard_panel(qa_checks: List[Dict[str, Any]], outpath: Optional[str] = None) -> str:
    """Render comprehensive CFD data quality checks as a visual dashboard with pass/warn/fail cards."""
    cols = 3
    n_rows = max(1, math.ceil(len(qa_checks) / cols))
    fig_h = max(4.2, 1.3 + n_rows * 1.65)
    fig = plt.figure(figsize=(12, fig_h), dpi=130)
    try:
        fig.patch.set_facecolor("white")
        top_margin = 0.08 if n_rows <= 4 else max(0.05, 0.4 / fig_h)
        fig.suptitle(
            "CFD Post-Processor: Data Quality & Numerical Integrity Audit",
            fontsize=14, fontweight="bold", x=0.05, y=1.0 - top_margin * 0.45, ha="left"
        )

        usable_h = 1.0 - top_margin - 0.04
        row_step = usable_h / n_rows
        card_h = row_step * 0.82
        w = 0.28

        for i, chk in enumerate(qa_checks):
            col_idx = i % cols
            row_idx = i // cols

            x = 0.05 + col_idx * 0.31
            y = (1.0 - top_margin) - (row_idx + 1) * row_step + (row_step - card_h) * 0.5

            status = chk.get("status", "PASS")
            if status == "PASS":
                bg_col = "#E2EFDA"
                border_col = "#276A3C"
                badge_txt = "✓ PASS"
                badge_col = "#276A3C"
            elif status == "WARN":
                bg_col = "#FFF2CC"
                border_col = "#B25E00"
                badge_txt = "⚠ WARN"
                badge_col = "#B25E00"
            else:
                bg_col = "#FCE4D6"
                border_col = "#C00000"
                badge_txt = "✗ FAIL"
                badge_col = "#C00000"

            fig.patches.append(patches.Rectangle((x, y), w, card_h, transform=fig.transFigure, fc=bg_col, ec=border_col, lw=1.2))

            chk_name = str(chk.get("name") or chk.get("check") or f"Check {i + 1}")
            fig.text(x + 0.015, y + card_h * 0.82, chk_name, fontsize=9.5, fontweight="bold", color="#1F3864")
            fig.text(x + w - 0.015, y + card_h * 0.82, badge_txt, fontsize=9.5, fontweight="bold", color=badge_col, ha="right")

            fig.text(x + 0.015, y + card_h * 0.56, f"Value: {chk.get('value', 'n/a')}", fontsize=8.5, color="#333")
            criteria = chk.get("criteria")
            if criteria is not None:
                fig.text(x + 0.015, y + card_h * 0.34, f"Criteria: {criteria}", fontsize=8, color="#555")

            det = str(chk.get("details") or chk.get("detail") or "")
            if len(det) > 55:
                det = det[:52] + "..."
            fig.text(x + 0.015, y + card_h * 0.12, det, fontsize=7.5, color="#666")

        if outpath is None:
            outpath = "21_qa_dashboard.png"
        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        fig.savefig(outpath, bbox_inches="tight")
    finally:
        plt.close(fig)
    return outpath
