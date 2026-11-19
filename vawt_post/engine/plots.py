# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Publication & Interactive Plotting Engine
==================================================================
Generates 18 publication-quality plots (static light-mode exports in PNG/PDF/SVG)
and reusable theme-aware Matplotlib Figure instances for the interactive Qt 6
Results Graph Preview workspace.
"""
from __future__ import annotations

import datetime
import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import matplotlib
if matplotlib.get_backend().lower() not in ("agg", "qtagg"):
    matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

PAL = ["#1F4E79", "#D1495B", "#2A9D8F", "#E9A03B", "#6A4C93", "#6C757D", "#118AB2", "#8D6E63"]
PAL_STANDARD = ["#1F77B4", "#D62728", "#2CA02C", "#FF7F0E", "#9467BD", "#8C564B", "#17BECF", "#7F7F7F"]
PAL_DARK = ["#38BDF8", "#F43F5E", "#10B981", "#F59E0B", "#A78BFA", "#CBD5E1", "#22D3EE", "#FB7185"]
APP_NAME = "VAWT CFD Post-Processor"

RESULT_FIGURE_KEYS: Tuple[str, ...] = (
    "01_cp_history.png",
    "02_cp_per_revolution.png",
    "03_cp_vs_azimuth.png",
    "04_blade_torque_vs_azimuth.png",
    "05_polar_blade_cp.png",
    "06_ct_cy.png",
    "07_cm.png",
    "08_alpha_w_re.png",
    "09_cp_spectrum.png",
    "10_convergence.png",
    "11_cp_heatmap.png",
    "12_dashboard.png",
    "13_power_contribution_quartiles.png",
    "14_cumulative_moment_distribution.png",
    "15_blade_tangential_load.png",
    "16_geometric_kinematics.png",
    "17_global_rotor_coefficients.png",
    "18_cp_convergence_history.png",
)


@dataclass
class PlotOptions:
    """Publication-quality figure formatting, styling, and export configuration (Tier 4)."""
    formats: List[str] = field(default_factory=lambda: ["png"])  # e.g. ["png", "pdf", "svg"]
    dpi: int = 300                                              # publication DPI (300 to 600)
    journal_preset: Optional[str] = None                       # "single_col" (90mm), "double_col" (190mm)
    font_size: int = 10                                         # base font size
    remove_titles: bool = False                                 # omit figure titles for manuscripts
    colorblind_safe: bool = True                                # colour-blind-safe palette
    greyscale_linestyles: bool = True                           # distinct dash styles for B&W printing
    fixed_y_limits: Optional[Dict[str, Tuple[float, float]]] = None


def generate_captions(R: Dict[str, Any]) -> Dict[str, str]:
    """Auto-generate comprehensive publication figure captions with computed quantitative KPIs."""
    p, revs, sel, res = R["p"], R["revs"], R["sel"], R["res"]
    sel_lab = ", ".join(f"rev {r['k']}" for r in sel)
    ct_str = f"{res['ct_mean']:.3f}" if res.get("ct_mean") is not None else "n/a"
    cy_str = f"{res['cy_mean']:.3f}" if res.get("cy_mean") is not None else "n/a"
    ait_raw = res.get("cp_extrapolated_aitken")
    ait_str = f"{float(ait_raw):.4f}" if isinstance(ait_raw, (int, float)) and math.isfinite(float(ait_raw)) else "n/a"
    caps: Dict[str, str] = {
        "01_cp_history.png": (
            f"Figure 1: Evolution of instantaneous and revolution-averaged power coefficient Cp "
            f"across {res['total_revs']:.1f} revolutions of flow time. The mean Cp for the reported "
            f"revolutions ({sel_lab}) is {res['cp_mean']:.4f} with last-revolution relative change "
            f"of {100.0 * res['cp_last_change']:+.2f}%."
        ),
        "02_cp_per_revolution.png": (
            f"Figure 2: Revolution-averaged power coefficient Cp per complete revolution. "
            f"Reported revolutions ({sel_lab}) are highlighted in orange, exhibiting a mean of "
            f"{res['cp_mean']:.4f} within a ±{p.stable_tol:g}% stability tolerance."
        ),
        "03_cp_vs_azimuth.png": (
            f"Figure 3: Instantaneous rotor power coefficient Cp versus blade azimuth angle θ across "
            f"the reported revolutions ({sel_lab}). Peak Cp reaches {res['cp_max']:.3f} during the upwind pass."
        ),
        "04_blade_torque_vs_azimuth.png": (
            f"Figure 4: Individual blade aerodynamic torque (N·m per m depth) versus each blade's own azimuth θ. "
            f"Maximum upwind torque of {res.get('torque_peak_up', 0.0):.3f} N·m occurs at θ = {res.get('torque_peak_up_theta', 0.0):.1f}°."
        ),
        "05_polar_blade_cp.png": (
            "Figure 5: Polar representation of blade-equivalent power coefficient Cp(θ) in the rotor coordinate system, "
            "with radial offset baseline highlighting positive torque generation and aerodynamic drag lobes."
        ),
        "06_ct_cy.png": (
            f"Figure 6: Thrust coefficient Ct (Fx) and side-force coefficient Cy (Fy) versus azimuth θ "
            f"and simulation flow time (reported mean Ct = {ct_str}, Cy = {cy_str})."
        ),
        "07_cm.png": (
            f"Figure 7: Rotor moment coefficient Cm versus azimuth θ with revolution-to-revolution min/max envelope "
            f"(cycle-averaged mean Cm = {res['cm_mean']:.4f}, starting torque ratio STR = {res.get('str', 0.0):.2f})."
        ),
        "08_alpha_w_re.png": (
            f"Figure 8: Kinematic blade flow conditions versus azimuth θ: geometric angle of attack α "
            f"(maximum {res['alpha_max']:.1f}° at θ = {res['alpha_max_theta']:.0f}°), relative velocity ratio W/U, and chord Reynolds number Re_c."
        ),
        "09_cp_spectrum.png": (
            f"Figure 9: Discrete Fourier transform amplitude spectrum of rotor Cp ripple. The dominant harmonic "
            f"order is {res['dominant_harmonic']:.2f}× rotor frequency (expected blade-passing frequency = {p.n_blades}×)."
        ),
        "10_convergence.png": (
            f"Figure 10: Revolution-to-revolution convergence history: percentage change in mean Cp (ΔCp %) "
            f"versus revolution index within the monitored ±{p.stable_tol:g}% stability band."
        ),
        "11_cp_heatmap.png": (
            "Figure 11: Space-time heatmap of instantaneous rotor power coefficient Cp over azimuth θ and revolution index."
        ),
        "12_dashboard.png": (
            f"Figure 12: Executive simulation KPI dashboard summarizing mean Cp ({res['cp_mean']:.4f}), "
            f"tip speed ratio λ ({res['tsr']:.2f}), mean power ({res['power_mean_W']:.1f} W/m), and aerodynamic convergence status."
        ),
        "13_power_contribution_quartiles.png": (
            "Figure 13: Aerodynamic power contribution segmented across four rotation quadrants: "
            "Upwind [45°–135°], Leeward [135°–225°], Downwind [225°–315°], and Windward [315°–45°]."
        ),
        "14_cumulative_moment_distribution.png": (
            "Figure 14: Normalized cumulative aerodynamic moment distribution across 360° rotor rotation "
            "demonstrating torque generation accumulation and half-cycle work extraction profile."
        ),
        "15_blade_tangential_load.png": (
            "Figure 15: Instantaneous blade tangential load and driving torque (N·m) on Blade 1 versus azimuth θ, "
            "highlighting peak torque and dynamic wake interaction zones."
        ),
        "16_geometric_kinematics.png": (
            "Figure 16: Analytical geometric blade kinematics versus azimuth θ: geometric angle of attack "
            "and normalized relative velocity (kinematic theoretical values without rotor flow induction)."
        ),
        "17_global_rotor_coefficients.png": (
            f"Figure 17: Global rotor aerodynamic performance coefficients (Cp, Cm, Ct, Cy) versus rotor azimuth θ "
            f"(mean Cm = {res['cm_mean']:.4f}, mean Ct = {ct_str}, mean Cy = {cy_str})."
        ),
        "18_cp_convergence_history.png": (
            f"Figure 18: Rotor power coefficient Cp convergence history across {len(revs)} revolutions with "
            f"asymptotic Aitken extrapolation limit ({ait_str}) and ±{p.stable_tol:g}% stability tolerance band."
        ),
    }
    return caps


def export_captions(R: Dict[str, Any], outdir: str) -> str:
    """Export formatted academic captions to captions.txt in output directory."""
    caps = generate_captions(R)
    out_txt = os.path.join(outdir, "captions.txt")
    with open(out_txt, "w", encoding="utf-8") as fh:
        fh.write("=" * 80 + "\n")
        fh.write("VAWT CFD Post-Processor: Academic Figure Captions\n")
        fh.write(f"Generated: {_dt_now()}\n")
        fh.write("=" * 80 + "\n\n")
        for fig_name, cap_text in caps.items():
            fh.write(f"[{fig_name}]\n")
            fh.write(f"{cap_text}\n\n")
    return out_txt


def _dt_now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _build_rc_dict(opts: Optional[PlotOptions] = None) -> Dict[Any, Any]:
    dpi_val = opts.dpi if opts else 130
    fsize = opts.font_size if opts else 10
    from cycler import cycler
    cb = opts.colorblind_safe if opts else True
    base_pal = PAL if cb else PAL_STANDARD
    return {
        "figure.dpi": dpi_val,
        "savefig.dpi": dpi_val,
        "font.family": "DejaVu Sans",
        "font.size": fsize,
        "axes.titlesize": fsize + 0.5,
        "axes.titleweight": "bold",
        "axes.labelsize": fsize,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.28,
        "grid.linestyle": "-",
        "axes.prop_cycle": cycler(color=base_pal),
        "legend.frameon": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }


def _apply_style(opts: Optional[PlotOptions] = None) -> None:
    plt.rcParams.update(_build_rc_dict(opts))


def _make_theme_spec(dark_mode: bool, opts: Optional[PlotOptions] = None) -> Dict[str, Any]:
    fsize = float(opts.font_size if opts else 10)
    cb = opts.colorblind_safe if opts else True
    ls_cycle = ["-", "--", "-.", ":"] if (opts is None or opts.greyscale_linestyles) else ["-"]
    j_preset = opts.journal_preset if opts else None
    if dark_mode:
        return {
            "dark": True,
            "pal": PAL_DARK if cb else PAL_STANDARD,
            "ls_cycle": ls_cycle,
            "journal_preset": j_preset,
            "fig_bg": "#141414",
            "ax_bg": "#1A1A1A",
            "fg": "#E2E8F0",
            "muted": "#94A3B8",
            "spine": "#475569",
            "grid": "#334155",
            "grid_alpha": 0.45,
            "mean_line": "#F8FAFC",
            "zero_line": "#64748B",
            "bar_edge": "#0F172A",
            "bar_stable": "#3B82F6",
            "bar_settling": "#475569",
            "upwind_bg": "#1E3A5F",
            "upwind_alpha": 0.35,
            "upwind_txt": "#7DD3FC",
            "downwind_txt": "#94A3B8",
            "leg_fc": "#1E293B",
            "leg_ec": "#334155",
            "annot_fc": "#1E293B",
            "annot_arrow": "#CBD5E1",
            "band_fc": "#10B981",
            "band_alpha": 0.16,
            "card_fc": "#1E293B",
            "card_ec": "#334155",
            "card_title": "#94A3B8",
            "badge_ok": "#10B981",
            "badge_warn": "#FB7185",
            "notice_fc": "#1E293B",
            "notice_ec": "#334155",
            "notice_fg": "#94A3B8",
            "stage_col": "#C084FC",
            "title_fs": 10.0,
            "label_fs": 9.0,
            "tick_fs": 8.5,
            "leg_fs": 8.0,
        }
    return {
        "dark": False,
        "pal": PAL if cb else PAL_STANDARD,
        "ls_cycle": ls_cycle,
        "journal_preset": j_preset,
        "fig_bg": "white",
        "ax_bg": "white",
        "fg": "#111827",
        "muted": "#555555",
        "spine": "#4B5563",
        "grid": "#9CA3AF",
        "grid_alpha": 0.28,
        "mean_line": "black",
        "zero_line": "#777777",
        "bar_edge": "white",
        "bar_stable": "#9DB4C8",
        "bar_settling": "#CFCFCF",
        "upwind_bg": "#EAF1F8",
        "upwind_alpha": 0.55,
        "upwind_txt": "#5B7C99",
        "downwind_txt": "#8A8A8A",
        "leg_fc": "white",
        "leg_ec": "#D1D5DB",
        "annot_fc": "#FEF2F2",
        "annot_arrow": "#444444",
        "band_fc": "#DDF2EE",
        "band_alpha": 0.8,
        "card_fc": "#F3F7FB",
        "card_ec": "#C9D8E6",
        "card_title": "#555555",
        "badge_ok": "#2A7F62",
        "badge_warn": "#B03A48",
        "notice_fc": "#F8FAFC",
        "notice_ec": "#CBD5E1",
        "notice_fg": "#475569",
        "stage_col": "#6A4C93",
        "title_fs": fsize + 0.5,
        "label_fs": fsize,
        "tick_fs": max(8.0, fsize - 1.0),
        "leg_fs": max(8.0, fsize - 1.5),
    }


def _style_cartesian_ax(ax: Any, st: Dict[str, Any], *, keep_right: bool = False) -> None:
    ax.set_facecolor(st["ax_bg"])
    ax.grid(True, linestyle="--" if st["dark"] else "-", alpha=st["grid_alpha"], color=st["grid"])
    ax.tick_params(colors=st["fg"], labelsize=st["tick_fs"])
    ax.xaxis.label.set_color(st["fg"])
    ax.yaxis.label.set_color(st["fg"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(keep_right)
    for sp_name, sp in ax.spines.items():
        if sp.get_visible():
            sp.set_color(st["spine"])


def _style_legend(ax_or_handles: Any, st: Dict[str, Any], **leg_kw: Any) -> Any:
    leg = ax_or_handles.legend(**leg_kw)
    if leg is not None:
        frame = leg.get_frame()
        if st["dark"]:
            frame.set_facecolor(st["leg_fc"])
            frame.set_edgecolor(st["leg_ec"])
            frame.set_alpha(0.92)
            leg.set_frame_on(True)
        for txt in leg.get_texts():
            txt.set_color(st["fg"])
    return leg


def _azaxis(ax: Any, upwind: bool = True, st: Optional[Dict[str, Any]] = None) -> None:
    fg = st["fg"] if st else "#111827"
    up_bg = st["upwind_bg"] if st else "#EAF1F8"
    up_alpha = st["upwind_alpha"] if st else 0.55
    up_txt = st["upwind_txt"] if st else "#5B7C99"
    dn_txt = st["downwind_txt"] if st else "#8A8A8A"
    lbl_fs = st["label_fs"] if st else 10
    ax.set_xlim(0, 360)
    ax.set_xticks(range(0, 361, 60))
    ax.set_xlabel("Blade azimuth θ (deg)  —  0° = blade at top moving into the wind", color=fg, fontsize=lbl_fs)
    if upwind:
        ax.axvspan(0, 180, color=up_bg, alpha=up_alpha, lw=0, zorder=0)
        ax.text(90, 0.03, "upwind half", transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=8, color=up_txt)
        ax.text(270, 0.03, "downwind half", transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=8, color=dn_txt)


def _prepare_fig(
    figsize: Tuple[float, float],
    dpi: int,
    st: Dict[str, Any],
    target_fig: Optional[Figure] = None,
) -> Figure:
    if target_fig is not None:
        target_fig.clear()
        target_fig.set_facecolor(st["fig_bg"])
        try:
            target_fig.set_layout_engine("constrained")
        except Exception:
            pass
        return target_fig
    w, h = figsize
    jp = st.get("journal_preset")
    if not st["dark"] and jp == "single_col" and w > 0:
        new_w = min(w, 5.2)
        figsize = (new_w, max(2.8, h * (new_w / w)))
    elif not st["dark"] and jp == "double_col" and w > 0:
        new_w = min(max(w, 7.5), 9.5)
        figsize = (new_w, max(3.2, h * (new_w / w)))
    fig = Figure(figsize=figsize, dpi=dpi, layout="constrained")
    fig.set_facecolor(st["fig_bg"])
    return fig


def _title_kw(st: Dict[str, Any], pad: float = 8.0) -> Dict[str, Any]:
    return {
        "color": st["fg"],
        "fontsize": st["title_fs"],
        "fontweight": "bold",
        "pad": pad,
    }


def _annotate_continuity_overlays(
    ax: Any,
    d: Dict[str, Any],
    rev_axis: np.ndarray,
    t_origin: float,
    Trev: float,
    st: Dict[str, Any],
    y_arr: Optional[np.ndarray] = None,
) -> None:
    """Highlight backup-to-resumed checkpoint splices and any recovered gap intervals."""
    rec_ranges = d.get("recovered_time_ranges") or []
    rec_mask = d.get("recovered_mask")
    splice_ts = d.get("splice_times") or []

    if rec_ranges and Trev > 0:
        for idx_r, (ta, tb) in enumerate(rec_ranges):
            ra = (float(ta) - t_origin) / Trev
            rb = max(ra + 1e-4, (float(tb) - t_origin) / Trev)
            ax.axvspan(
                ra, rb, color="#F59E0B", alpha=0.18, lw=0,
                label="Recovered gap" if idx_r == 0 else None,
            )
        if y_arr is not None and rec_mask is not None and len(rec_mask) == len(rev_axis) and np.any(rec_mask):
            ax.plot(rev_axis[rec_mask], y_arr[rec_mask], color="#F59E0B", ls="--", lw=1.4, label="_nolegend_")
    elif splice_ts and Trev > 0:
        for idx_s, ts_val in enumerate(splice_ts):
            rs = (float(ts_val) - t_origin) / Trev
            ax.axvline(
                rs, color="#06B6D4", ls=":", lw=1.2, alpha=0.85,
                label="Resume point" if idx_s == 0 else None,
            )


def _plot_01_cp_history(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    d, revs, sel, res = R["data"], R["revs"], R["sel"], R["res"]
    t, Trev = d["t"], res["Trev"]
    t_origin = (revs[0]["a"] - (revs[0]["k"] - 1) * Trev) if revs else float(t[0])
    rev_axis = (t - t_origin) / Trev
    pal = st["pal"]

    fig = _prepare_fig((11, 4.4), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)

    ax.plot(rev_axis, d["Cp"], lw=0.8, color=pal[0], alpha=0.88, label="Instantaneous Cp (rotor)")
    for r in revs:
        ax.hlines(r["cp"], (r["a"] - t_origin) / Trev, (r["b"] - t_origin) / Trev, color=pal[1], lw=2.2)
    ax.hlines([], 0, 0, color=pal[1], lw=2.2, label="Revolution average")
    for idx_s, r_s in enumerate(sel):
        ax.axvspan(
            (r_s["a"] - t_origin) / Trev,
            (r_s["b"] - t_origin) / Trev,
            color=pal[3], alpha=0.22, lw=0,
            label="Reported revolutions" if idx_s == 0 else None,
        )

    if len(res.get("phases", [])) > 1:
        for idx_ph, ph in enumerate(res["phases"][1:]):
            t_bound = ph.get("t_from", t[0])
            rev_b = (t_bound - t_origin) / Trev
            ax.axvline(rev_b, color=st["stage_col"], ls="--", lw=1.2, alpha=0.85)
            y_frac = 0.10 if (idx_ph % 2 == 0) else 0.24
            ax.text(
                rev_b + 0.04, y_frac,
                f"Stage {ph.get('phase_index', '')}\n(Step {ph['step_from']}: {ph['deg_per_step']:.2f}°/step)",
                transform=ax.get_xaxis_transform(),
                fontsize=8, color=st["stage_col"], va="bottom", ha="left",
                bbox=dict(boxstyle="round,pad=0.2", fc=st["annot_fc"], ec=st["stage_col"], alpha=0.9, lw=0.8),
            )

    _annotate_continuity_overlays(ax, d, rev_axis, t_origin, Trev, st, y_arr=d["Cp"])

    ax.set_xlabel("Revolutions of flow time", color=st["fg"], fontsize=st["label_fs"])
    ax.set_ylabel("Power coefficient Cp", color=st["fg"], fontsize=st["label_fs"])
    title_suffix = f" ({len(res['phases'])} Multi-Stage Phases)" if len(res.get("phases", [])) > 1 else ""
    ax.set_title(f"Power coefficient history across simulation{title_suffix}", **_title_kw(st))
    _style_legend(ax, st, ncol=3, loc="upper right", fontsize=st["leg_fs"])
    return fig


def _plot_02_cp_per_rev(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    revs, res = R["revs"], R["res"]
    pal = st["pal"]

    fig = _prepare_fig((9, 4.4), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)

    from matplotlib.ticker import MaxNLocator

    ks = [r["k"] for r in revs]
    colors = [pal[3] if r["selected"] else (st["bar_stable"] if r["stable"] else st["bar_settling"]) for r in revs]
    ax.bar(ks, [r["cp"] for r in revs], color=colors, edgecolor=st["bar_edge"])
    for r in revs:
        v_cp = float(r["cp"])
        ax.annotate(
            f"{v_cp:.3f}",
            xy=(r["k"], v_cp),
            xytext=(0, 3 if v_cp >= 0 else -3),
            textcoords="offset points",
            ha="center",
            va="bottom" if v_cp >= 0 else "top",
            fontsize=8,
            color=st["fg"],
        )
    ax.axhline(0, color=st["zero_line"], lw=0.8, alpha=0.6)
    ax.axhline(res["cp_mean"], color=pal[1], ls="--", lw=1.2, label=f"Mean of reported revs = {res['cp_mean']:.4f}")
    lo = min((r["cp"] for r in revs), default=0.0)
    hi = max((r["cp"] for r in revs), default=1.0)
    span = max(0.01, hi - lo)
    ax.set_ylim(min(0.0, lo - 0.22 * span - 0.01), max(0.05, hi + 0.18 * span + 0.01))
    ax.set_xlabel("Complete revolution (counted back from end of run)", color=st["fg"], fontsize=st["label_fs"])
    ax.set_ylabel("Revolution-averaged Cp", color=st["fg"], fontsize=st["label_fs"])
    if len(ks) <= 16:
        ax.set_xticks(ks)
    else:
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_title("Cp per revolution (orange = reported, blue = stable, grey = settling)", **_title_kw(st))
    _style_legend(ax, st, loc="lower right", fontsize=st["leg_fs"])
    return fig


def _plot_03_cp_vs_azimuth(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, sel, az, res = R["p"], R["sel"], R["az"], R["res"]
    th = az["theta"]
    pal = st["pal"]
    sel_lab = ", ".join(f"rev {r['k']}" for r in sel)

    fig = _prepare_fig((11, 4.8), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)
    _azaxis(ax, st=st)

    ls_cyc = st.get("ls_cycle") or ["-"]
    for i, dd in enumerate(az["rev"]):
        ax.plot(
            th, dd["cp"],
            lw=2.3 - 0.4 * min(i, 3),
            ls=ls_cyc[i % len(ls_cyc)],
            color=pal[[0, 2, 4, 6][i % 4]],
            alpha=0.88,
            label=f"rev {dd['k']}",
        )
    ax.plot(th, az["cp_mean"], color=st["mean_line"], lw=1.3, ls=(0, (4, 2)), zorder=5, label="mean of reported revs")
    ax.axhline(res["cp_mean"], color=pal[1], ls="--", lw=1.2, label=f"rev-averaged Cp = {res['cp_mean']:.4f}")
    ax.set_ylabel("Instantaneous rotor Cp", color=st["fg"], fontsize=st["label_fs"])
    rev_title_desc = f"reported revolutions ({sel_lab})" if getattr(p, "custom_revs", None) else f"last {len(sel)} revolutions ({sel_lab})"
    ax.set_title(f"Rotor Cp vs azimuth, {rev_title_desc}", **_title_kw(st))
    _style_legend(ax, st, ncol=min(4, max(1, len(az["rev"]) + 2)), loc="upper right", fontsize=st["leg_fs"])
    return fig


def _plot_04_blade_torque(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    d, az = R["data"], R["az"]
    th = az["theta"]
    pal = st["pal"]

    if d["have_blades"]:
        fig = _prepare_fig((11, 4.8), dpi, st, target_fig)
        ax = fig.add_subplot(111)
        _style_cartesian_ax(ax, st)
        _azaxis(ax, st=st)
        ls_cyc = st.get("ls_cycle") or ["-"]
        for i, m in enumerate(az["M_b_mean"]):
            ax.plot(th, m, lw=1.3, ls=ls_cyc[i % len(ls_cyc)], color=pal[i % len(pal)], alpha=0.85, label=f"blade {i + 1}")
        ax.plot(th, az["M_avg"], color=st["mean_line"], lw=2.3, label="mean of blades")
        m_up = th < 180
        m_cand = np.where(m_up, az["M_avg"], -np.inf) if np.any(m_up) else az["M_avg"]
        if np.any(np.isfinite(m_cand)):
            iu = int(np.nanargmax(m_cand))
            x_text = th[iu] - 80 if th[iu] > 135 else th[iu] + 35
            ax.annotate(
                f"peak {az['M_avg'][iu]:.3f} N·m at {th[iu]:.1f}°",
                (th[iu], az["M_avg"][iu]),
                xytext=(max(5.0, min(300.0, x_text)), az["M_avg"][iu] * 0.90),
                arrowprops=dict(arrowstyle="->", color=st["annot_arrow"]),
                fontsize=8.5,
                color=st["fg"],
            )
        ax.axhline(0, color=st["zero_line"], lw=0.8)
        ax.set_ylabel("Blade torque (N·m per m depth)", color=st["fg"], fontsize=st["label_fs"])
        ax.set_title("Blade torque vs blade's own azimuth (reported revolutions)", **_title_kw(st))
        _style_legend(ax, st, ncol=min(4, len(az["M_b_mean"]) + 1), loc="upper right", fontsize=st["leg_fs"])
        return fig

    fig = _prepare_fig((8, 4), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(st["ax_bg"])
    ax.text(0.5, 0.5, "Per-blade torque requires individual blade monitor files.", ha="center", va="center", color=st["muted"])
    ax.axis("off")
    return fig


def _plot_05_polar_cp(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, d, az = R["p"], R["data"], R["az"]
    th = az["theta"]
    pal = st["pal"]

    fig = _prepare_fig((6.4, 6.4), dpi, st, target_fig)
    ax_polar: Any = fig.add_subplot(111, projection="polar")
    ax_polar.set_facecolor(st["ax_bg"])
    if not d["have_blades"]:
        ax_polar.text(0, 0, "No blade files provided", ha="center", va="center", color=st["muted"])
        ax_polar.tick_params(colors=st["fg"])
        return fig

    is_cw = getattr(p, "rotation_dir", "ccw") == "cw"
    ax_polar.set_theta_zero_location("S" if is_cw else "N")
    ax_polar.set_theta_direction(-1 if is_cw else 1)
    r_ = np.radians(np.append(th, th[0]))
    v = np.append(az["Cp_blade_eq"], az["Cp_blade_eq"][0])

    from matplotlib.ticker import FuncFormatter

    min_v = float(np.min(v))
    if min_v < 0:
        offset = abs(min_v) + 0.05 * (float(np.max(v)) - min_v)
        v_plot = v + offset
        zero_r = offset
        ax_polar._polar_offset = offset
        ax_polar.plot(r_, v_plot, color=pal[0], lw=2, label="Cp(θ)")
        ax_polar.plot(r_, np.full_like(r_, zero_r), color=st["muted"], ls="--", lw=1.2, label="Cp = 0 reference")
        ax_polar.fill_between(r_, zero_r, v_plot, where=(v >= 0), interpolate=True, color=pal[2], alpha=0.35, label="Positive lobe")
        ax_polar.fill_between(r_, v_plot, zero_r, where=(v < 0), interpolate=True, color=pal[1], alpha=0.35, label="Negative lobe")
        ax_polar.yaxis.set_major_formatter(FuncFormatter(lambda y, _pos, off=offset: f"{y - off:.2f}"))
    else:
        ax_polar._polar_offset = 0.0
        ax_polar.plot(r_, v, color=pal[0], lw=2, label="Cp(θ)")
        ax_polar.fill_between(r_, 0, v, color=pal[2], alpha=0.35, label="Positive lobe")
        ax_polar.plot(r_, np.zeros_like(r_), color=st["muted"], ls="--", lw=1.0)

    ax_polar.set_title("Blade-resolved Cp (N × per-blade contribution)\nwind from the left →", **_title_kw(st, pad=16))
    ax_polar.grid(True, alpha=0.4, color=st["grid"])
    ax_polar.tick_params(colors=st["fg"], labelsize=st["tick_fs"])
    for sp in ax_polar.spines.values():
        sp.set_color(st["spine"])
    _style_legend(ax_polar, st, loc="lower left", bbox_to_anchor=(-0.08, -0.06), fontsize=8)
    return fig


def _plot_06_ct_cy(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    d, revs, az, res = R["data"], R["revs"], R["az"], R["res"]
    t, Trev, th = d["t"], res["Trev"], az["theta"]
    t_origin = (revs[0]["a"] - (revs[0]["k"] - 1) * Trev) if revs else float(t[0])
    rev_axis = (t - t_origin) / Trev
    pal = st["pal"]

    if d["Ct"] is not None or d["Cy"] is not None:
        fig = _prepare_fig((12, 4.4), dpi, st, target_fig)
        ax0 = fig.add_subplot(121)
        ax1 = fig.add_subplot(122)
        _style_cartesian_ax(ax0, st)
        _style_cartesian_ax(ax1, st)
        _azaxis(ax0, upwind=True, st=st)
        for i, dd in enumerate(az["rev"]):
            if "ct" in dd:
                ax0.plot(th, dd["ct"], lw=1.1, color=pal[i % len(pal)], alpha=0.75, label=f"Ct rev {dd['k']}")
            if "cy" in dd:
                ax0.plot(th, dd["cy"], lw=1.1, color=pal[(i + 2) % len(pal)], alpha=0.75, ls="--", label=f"Cy rev {dd['k']}")
        if "ct_mean" in az:
            ax0.plot(th, az["ct_mean"], color=st["mean_line"], lw=2, label="Ct mean")
        if "cy_mean" in az:
            ax0.plot(th, az["cy_mean"], color=pal[4], lw=2, ls=":", label="Cy mean")
        ax0.set_ylabel("Force coefficient", color=st["fg"], fontsize=st["label_fs"])
        ax0.set_title("Thrust (Fx) & side-force (Fy) vs azimuth", **_title_kw(st))
        _style_legend(ax0, st, ncol=2, loc="best", fontsize=7.5)

        if d["Ct"] is not None:
            ax1.plot(rev_axis, d["Ct"], lw=0.7, color=pal[0], label="Ct (Fx)")
        if d["Cy"] is not None:
            ax1.plot(rev_axis, d["Cy"], lw=0.7, color=pal[1], label="Cy (Fy)")
        _annotate_continuity_overlays(ax1, d, rev_axis, t_origin, Trev, st, y_arr=d["Ct"] if d["Ct"] is not None else d["Cy"])
        ax1.set_xlabel("Revolutions of flow time", color=st["fg"], fontsize=st["label_fs"])
        ax1.set_ylabel("Coefficient", color=st["fg"], fontsize=st["label_fs"])
        ax1.set_title("Force coefficient history", **_title_kw(st))
        _style_legend(ax1, st, fontsize=st["leg_fs"])
        return fig

    fig = _prepare_fig((8, 4), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(st["ax_bg"])
    ax.text(0.5, 0.5, "Thrust & Side force plots require Force X/Y files.", ha="center", va="center", color=st["muted"])
    ax.axis("off")
    return fig


def _plot_07_cm(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, d, az, res = R["p"], R["data"], R["az"], R["res"]
    th = az["theta"]
    pal = st["pal"]

    fig = _prepare_fig((12, 4.4), dpi, st, target_fig)
    ax0 = fig.add_subplot(121)
    ax1 = fig.add_subplot(122)
    _style_cartesian_ax(ax0, st)
    _style_cartesian_ax(ax1, st)

    _azaxis(ax0, st=st)
    cm_revs = [dd["cm"] for dd in az["rev"]]
    cm_min_env = np.min(cm_revs, axis=0) if cm_revs else az["cm_mean"]
    cm_max_env = np.max(cm_revs, axis=0) if cm_revs else az["cm_mean"]

    ax0.plot(th, az["cm_mean"], color=st["mean_line"], lw=2, label="Rotor Cm (mean)")
    ax0.fill_between(th, cm_min_env, cm_max_env, color=pal[0], alpha=0.25, label="Min/Max envelope")
    ax0.axhline(res["cm_mean"], color=pal[1], ls="--", lw=1.2, label=f"Mean Cm = {res['cm_mean']:.4f}")
    ax0.set_ylabel("Rotor moment coefficient Cm", color=st["fg"], fontsize=st["label_fs"])
    ax0.set_title("Rotor Cm vs azimuth (with min/max envelope)", **_title_kw(st))
    _style_legend(ax0, st, fontsize=st["leg_fs"])

    _azaxis(ax1, st=st)
    if d["have_blades"]:
        for i in range(p.n_blades):
            b_line = az["M_b_mean"][i] / res["Mref"]
            ax1.plot(th, b_line, lw=1.5, color=pal[i % len(pal)], label=f"Blade {i + 1}")
            if all("Cm_b" in dd for dd in az["rev"]):
                b_revs = [dd["Cm_b"][i] for dd in az["rev"]]
                ax1.fill_between(th, np.min(b_revs, axis=0), np.max(b_revs, axis=0), color=pal[i % len(pal)], alpha=0.15)
        ax1.set_ylabel("Blade Cm", color=st["fg"], fontsize=st["label_fs"])
        ax1.set_title("Per-blade Cm vs own azimuth (mean ± envelope)", **_title_kw(st))
        _style_legend(ax1, st, fontsize=st["leg_fs"])
    else:
        for i, dd in enumerate(az["rev"]):
            ax1.plot(th, dd["cm"], lw=1.0, color=pal[i % len(pal)], alpha=0.75, label=f"Rev {dd['k']}")
        ax1.plot(th, az["cm_mean"], color=st["mean_line"], lw=2, label="Mean")
        ax1.fill_between(th, cm_min_env, cm_max_env, color=pal[0], alpha=0.2, label="Envelope")
        ax1.set_ylabel("Cm", color=st["fg"], fontsize=st["label_fs"])
        ax1.set_title("Revolution-to-revolution Cm vs azimuth", **_title_kw(st))
        _style_legend(ax1, st, fontsize=8)
    return fig


def _plot_08_alpha_w_re(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    az, res = R["az"], R["res"]
    th = az["theta"]
    pal = st["pal"]

    fig = _prepare_fig((15, 4.2), dpi, st, target_fig)
    axs = [fig.add_subplot(1, 3, idx + 1) for idx in range(3)]
    for ax, key, lab, ttl in zip(
        axs,
        ["alpha", "w_over_u", "re_c"],
        ["α geometric (deg)", "W / U∞", "Chord Reynolds number"],
        ["Geometric angle of attack", "Relative velocity ratio", "Chord Reynolds number"],
    ):
        _style_cartesian_ax(ax, st)
        _azaxis(ax, st=st)
        ax.plot(th, az[key], color=pal[0], lw=2, label=ttl)
        ax.set_ylabel(lab, color=st["fg"], fontsize=st["label_fs"])
        ax.set_title(ttl, **_title_kw(st))
        ax.set_xlabel("θ (deg)", color=st["fg"], fontsize=st["label_fs"])

    axs[0].axhline(0, color=st["zero_line"], lw=0.8)
    a_max_th = float(res["alpha_max_theta"])
    a_max_v = float(res["alpha_max"])
    x_ann = max(10.0, min(280.0, a_max_th - 75.0 if a_max_th > 120.0 else a_max_th + 45.0))
    axs[0].annotate(
        f"max {a_max_v:.2f}° at {a_max_th:.0f}°",
        (a_max_th, a_max_v),
        xytext=(x_ann, a_max_v * 0.65),
        arrowprops=dict(arrowstyle="->", color=st["annot_arrow"]),
        fontsize=8.5,
        color=st["fg"],
    )
    return fig


def _plot_09_spectrum(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, res = R["p"], R["res"]
    harm, spec = R["spec"]
    pal = st["pal"]

    fig = _prepare_fig((9, 4.2), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)

    m_spec = (harm > 0) & (harm <= 15)
    ax.vlines(harm[m_spec], 0, spec[m_spec], color=pal[0], lw=1.4, label="Harmonic amplitude")
    ax.axvline(p.n_blades, color=pal[1], ls="--", lw=1.2, label=f"{p.n_blades}× rotor frequency (blade passing)")
    ax.set_xlabel("Frequency / rotor frequency", color=st["fg"], fontsize=st["label_fs"])
    ax.set_ylabel("Cp amplitude", color=st["fg"], fontsize=st["label_fs"])
    ax.set_title(f"Cp ripple spectrum (dominant harmonic: {res['dominant_harmonic']:.2f}× rotor frequency)", **_title_kw(st))
    ax.set_xlim(0, 15)
    _style_legend(ax, st, fontsize=st["leg_fs"])
    return fig


def _plot_10_convergence(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, d, revs = R["p"], R["data"], R["revs"]
    pal = st["pal"]

    fig = _prepare_fig((9, 4.4), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)

    if len(revs) > 1:
        revs_plot = revs if (revs[0]["k"] > 1 and revs[0].get("dcp") is not None) else revs[1:]
        kk = [r["k"] for r in revs_plot]
        dv = [100.0 * (r["dcp"] or 0.0) for r in revs_plot]
    else:
        kk = [revs[0]["k"]]
        dv = [100.0 * (revs[0].get("dcp") or 0.0)]
        ax.set_xticks(kk)
    ax.axhspan(-p.stable_tol, p.stable_tol, color=st["band_fc"], alpha=st["band_alpha"], lw=0, label=f"±{p.stable_tol:g}% stability band")
    ax.plot(kk, dv, "o-", color=pal[0], lw=1.8, label="ΔCp (%)")
    ax.axhline(0, color=st["zero_line"], lw=0.8)
    ax.set_xlabel("Revolution", color=st["fg"], fontsize=st["label_fs"])
    ax.set_ylabel("Change in revolution-averaged Cp (%)", color=st["fg"], fontsize=st["label_fs"])
    ax.set_title("Revolution-to-revolution convergence", **_title_kw(st))

    from matplotlib.ticker import MaxNLocator

    ax2 = None
    if d["have_blades"]:
        ax2 = ax.twinx()
        ax2.grid(False)
        ax2.tick_params(colors=pal[3], labelsize=st["tick_fs"])
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color(pal[3])
        ax2.plot([r["k"] for r in revs], [100.0 * r["spread"] for r in revs], "s--", color=pal[3], ms=4, lw=1.2, label="blade spread (%)")
        ax2.set_ylabel("Blade-to-blade spread of mean torque (%)", color=pal[3], fontsize=st["label_fs"])
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    h1, l1 = ax.get_legend_handles_labels()
    if ax2 is not None:
        h2, l2 = ax2.get_legend_handles_labels()
        _style_legend(ax, st, handles=h1 + h2, labels=l1 + l2, loc="upper right", fontsize=st["leg_fs"])
    else:
        _style_legend(ax, st, loc="upper right", fontsize=st["leg_fs"])
    return fig


def _plot_11_heatmap(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    from vawt_post.engine.analysis import bin_azimuth

    p, d, revs = R["p"], R["data"], R["revs"]
    t = d["t"]
    mat = []
    for r in revs:
        m_win = (t >= r["a"]) & (t <= r["b"])
        mat.append(bin_azimuth(d["th1"][m_win], d["Cp"][m_win], p.bin_deg)[0])
    mat_arr = np.array(mat)

    from matplotlib.ticker import MaxNLocator

    fig = _prepare_fig((11, 4.6), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st, keep_right=True)
    rev_ks = [r["k"] for r in revs]
    is_contiguous = len(rev_ks) == (rev_ks[-1] - rev_ks[0] + 1) if rev_ks else True
    y_ext = (float(rev_ks[0]) - 0.5, float(rev_ks[-1]) + 0.5) if is_contiguous else (-0.5, float(len(rev_ks)) - 0.5)
    im = ax.imshow(
        mat_arr,
        aspect="auto",
        origin="lower",
        cmap="RdYlBu_r",
        extent=(0.0, 360.0, y_ext[0], y_ext[1]),
    )
    ax.set_xticks(range(0, 361, 60))
    ax.set_xlabel("Rotor azimuth θ1 (deg)", color=st["fg"], fontsize=st["label_fs"])
    ax.set_ylabel("Revolution", color=st["fg"], fontsize=st["label_fs"])
    if not is_contiguous:
        ax.set_yticks(range(len(rev_ks)))
        ax.set_yticklabels([str(k) for k in rev_ks])
    elif len(rev_ks) <= 16:
        ax.set_yticks(rev_ks)
    else:
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(False)
    cbar = fig.colorbar(im, ax=ax, label="Instantaneous Cp")
    cbar.ax.yaxis.label.set_color(st["fg"])
    cbar.ax.tick_params(colors=st["fg"], labelsize=st["tick_fs"])
    ax.set_title("Cp map: revolution × azimuth", **_title_kw(st))
    return fig


def _plot_12_dashboard(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, sel, res = R["p"], R["sel"], R["res"]
    sel_lab = ", ".join(f"rev {r['k']}" for r in sel)
    pal = st["pal"]

    fig = _prepare_fig((11, 6.2), dpi, st, target_fig)
    try:
        fig.set_layout_engine("none")
    except Exception:
        pass
    fig.suptitle(f"{APP_NAME}: Key Simulation Results", fontsize=12.5, fontweight="bold", x=0.05, ha="left", color=st["fg"])

    cp_card_sub = f"Mean Cp ({sel_lab})" if len(sel) <= 3 else (
        f"Mean Cp (revs {sel[0]['k']}–{sel[-1]['k']})" if getattr(p, "custom_revs", None) else f"Mean Cp (last {len(sel)} revs)"
    )
    stable_label = f"{res['stable_n']}" + (f" (Cp={res['cp_stable_mean']:.4f})" if "cp_stable_mean" in res else "")

    kp = [
        (cp_card_sub, f"{res['cp_mean']:.4f}"),
        ("Tip Speed Ratio λ", f"{res['tsr']:.2f}"),
        ("Mean Cm", f"{res['cm_mean']:.4f}"),
        ("Mean Power", f"{res['power_mean_W']:.1f} W/m"),
        ("Thrust Ct (Fx)", f"{res['ct_mean']:.3f}" if res["ct_mean"] is not None else "n/a"),
        ("Cp Ripple (p-p/mean)", f"{res['ripple_pp']:.2f}"),
        ("Last-Rev Change", f"{100.0 * res['cp_last_change']:+.2f}%"),
        ("Trailing Stable Revs", stable_label),
        ("Max Geometric α", f"{res['alpha_max']:.1f}°"),
    ]

    for i, (title_k, val_k) in enumerate(kp):
        x, y = 0.05 + (i % 3) * 0.31, 0.70 - (i // 3) * 0.27
        fig.patches.append(patches.Rectangle((x, y), 0.28, 0.20, transform=fig.transFigure, fc=st["card_fc"], ec=st["card_ec"], lw=1.1))
        fig.text(x + 0.14, y + 0.125, val_k, ha="center", va="center", fontsize=17, fontweight="bold", color=pal[0])
        fig.text(x + 0.14, y + 0.045, title_k, ha="center", va="center", fontsize=9, color=st["card_title"])

    status = "STABLE" if res["is_stable"] else "NOT YET STABLE"
    color_status = st["badge_ok"] if res["is_stable"] else st["badge_warn"]
    stable_txt = f"Trailing Stable: {res['stable_n']} revs (Cp={res.get('cp_stable_mean', res['cp_mean']):.4f})"
    stage_txt = f"   |   Multi-Stage: {len(res['phases'])} phases" if len(res.get("phases", [])) > 1 else ""
    fig.text(
        0.05, 0.05,
        f"Convergence Status: {status}   |   Reported: {sel_lab}   |   {stable_txt}   |   Total: {res['n_full']} revs{stage_txt}",
        fontsize=9.5, fontweight="bold", color=color_status,
    )
    return fig


def _plot_13_quartiles(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    d, az = R["data"], R["az"]
    th = az["theta"]
    th_deg = np.asarray(th) % 360.0
    cp_for_quartiles = az["Cp_blade_eq"] if (d.get("have_blades") and "Cp_blade_eq" in az) else az["cp_mean"]

    mask_up = (th_deg >= 45.0) & (th_deg < 135.0)
    mask_lee = (th_deg >= 135.0) & (th_deg < 225.0)
    mask_down = (th_deg >= 225.0) & (th_deg < 315.0)
    mask_wind = (th_deg >= 315.0) | (th_deg < 45.0)

    sums = [
        float(np.nansum(cp_for_quartiles[mask_up])),
        float(np.nansum(cp_for_quartiles[mask_lee])),
        float(np.nansum(cp_for_quartiles[mask_down])),
        float(np.nansum(cp_for_quartiles[mask_wind])),
    ]
    total_q = sum(sums)
    quartile_pcts = [(s / abs(total_q)) * 100.0 for s in sums] if abs(total_q) > 1e-9 else [0.0, 0.0, 0.0, 0.0]
    quartile_labels = ["Upwind\n[45°–135°)", "Leeward\n[135°–225°)", "Downwind\n[225°–315°)", "Windward\n[315°–45°)"]
    pal = st["pal"]
    q_colors = [pal[0], pal[1], pal[2], pal[3]]

    fig = _prepare_fig((8.5, 4.8), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)
    q_bars = ax.bar(quartile_labels, quartile_pcts, color=q_colors, width=0.55, edgecolor=st["bar_edge"], linewidth=1.2, zorder=3)
    ax.axhline(0, color=st["zero_line"], linewidth=1.0, linestyle="-", zorder=2)
    ax.set_ylabel("Power Contribution Share (%)", fontweight="bold", color=st["fg"], fontsize=st["label_fs"])
    ax.set_title("Figure 13: Power Contribution by Azimuthal Quartile", **_title_kw(st, pad=8))
    ymin = min(0.0, min(quartile_pcts) * 1.25) - 5.0
    ymax = max(5.0, max(quartile_pcts) * 1.25 + 10.0)
    ax.set_ylim(ymin, ymax)
    for bar, pct in zip(q_bars, quartile_pcts):
        h = bar.get_height()
        ax.annotate(
            f"{pct:.1f}%",
            xy=(bar.get_x() + bar.get_width() / 2.0, h),
            xytext=(0, 4 if h >= 0 else -4),
            textcoords="offset points",
            ha="center",
            va="bottom" if h >= 0 else "top",
            fontweight="bold",
            fontsize=9.5,
            color=st["fg"],
        )
    ax.grid(True, linestyle="--", alpha=0.45, axis="y", color=st["grid"])
    return fig


def _plot_14_cumulative_moment(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    az = R["az"]
    th = az["theta"]
    cm_cum = np.cumsum(az["cm_mean"])
    min_accum = np.min(cm_cum)
    accum_range = np.max(cm_cum) - min_accum
    cm_norm = ((cm_cum - min_accum) / accum_range) * 100.0 if accum_range > 1e-12 else np.linspace(0, 100, len(th))
    pal = st["pal"]

    fig = _prepare_fig((8.5, 4.8), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)
    ax.plot(th, cm_norm, color=pal[0], linewidth=2.3, label=r"Normalized Cumulative Moment $\sum C_m$")
    ax.axhline(100.0, color=pal[2], linestyle="--", linewidth=1.2, alpha=0.85, label="100% Accumulated Torque")
    ax.axhline(50.0, color=st["muted"], linestyle=":", linewidth=1.0, alpha=0.85, label="50% Median Work Threshold")
    _azaxis(ax, upwind=True, st=st)
    ax.set_ylabel("Cumulative Moment Distribution (% of Max)", fontweight="bold", color=st["fg"], fontsize=st["label_fs"])
    ax.set_title("Figure 14: Cumulative Moment Distribution vs. Azimuth", **_title_kw(st, pad=8))
    _style_legend(ax, st, loc="upper left", frameon=True, framealpha=0.92, fontsize=st["leg_fs"])
    return fig


def _plot_15_blade_tangential(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    d, az, res = R["data"], R["az"], R["res"]
    th = az["theta"]
    if d.get("have_blades") and "M_b_mean" in az and len(az["M_b_mean"]) > 0:
        t_b1 = az["M_b_mean"][0]
        curve_label = r"Blade 1 Torque $T_{\mathrm{blade\,1}}$"
    elif d.get("have_blades") and "M_avg" in az:
        t_b1 = az["M_avg"]
        curve_label = r"Mean Blade Torque $T_{\mathrm{avg}}$"
    else:
        t_b1 = az["cm_mean"] * res["Mref"]
        curve_label = r"Rotor Torque $T_{\mathrm{rotor}}$"

    pal = st["pal"]
    fig = _prepare_fig((8.5, 4.8), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)
    ax.plot(th, t_b1, color=pal[0], linewidth=2.2, label=curve_label)
    ax.axhline(0, color=st["zero_line"], linestyle="-", linewidth=0.9, alpha=0.75)
    t_max_val = float(np.max(t_b1))
    t_max_th = float(th[np.argmax(t_b1)])
    peak_col = pal[1]
    ax.plot(t_max_th, t_max_val, marker="^", markersize=8, color=peak_col, label=f"Peak Torque: {t_max_val:.2f} N·m @ {t_max_th:.0f}°")
    x_txt = max(15.0, min(285.0, t_max_th - 80.0 if t_max_th > 220.0 else t_max_th + 18.0))
    y_span = max(abs(float(np.max(t_b1) - np.min(t_b1))), 1e-6)
    ax.annotate(
        f"Peak: {t_max_val:.2f} N·m\n@ θ = {t_max_th:.0f}°",
        xy=(t_max_th, t_max_val),
        xytext=(x_txt, t_max_val - 0.14 * y_span),
        arrowprops=dict(arrowstyle="->", color=peak_col, lw=1.2),
        fontsize=8.5, fontweight="bold", color=peak_col,
        bbox=dict(boxstyle="round,pad=0.3", fc=st["annot_fc"], ec=peak_col, lw=1),
    )
    _azaxis(ax, upwind=True, st=st)
    ax.set_ylabel("Dimensional Torque (N·m)", fontweight="bold", color=st["fg"], fontsize=st["label_fs"])
    ax.set_title("Figure 15: Instantaneous Blade Torque vs. Azimuth", **_title_kw(st, pad=8))
    _style_legend(ax, st, loc="upper right", frameon=True, framealpha=0.92, fontsize=st["leg_fs"])
    return fig


def _plot_16_geometric_kinematics(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    az = R["az"]
    th = az["theta"]
    pal = st["pal"]

    fig = _prepare_fig((8.5, 4.8), dpi, st, target_fig)
    ax16_1 = fig.add_subplot(111)
    _style_cartesian_ax(ax16_1, st)
    ax16_2 = ax16_1.twinx()
    ax16_2.grid(False)
    ax16_2.spines["top"].set_visible(False)
    c_alpha = pal[3] if st["dark"] else "#D97706"
    c_vel = pal[0] if st["dark"] else "#0284C7"
    ax16_2.spines["right"].set_color(c_vel)
    ax16_2.tick_params(colors=c_vel, labelsize=st["tick_fs"])

    l1 = ax16_1.plot(th, az["alpha"], color=c_alpha, linewidth=2.2, linestyle="-", label=r"Geometric Angle of Attack $\alpha_{\mathrm{geometric}}$")
    l2 = ax16_2.plot(th, az["w_over_u"], color=c_vel, linewidth=2.2, linestyle="--", label=r"Geometric Relative Velocity $W/U_\infty$")
    ax16_1.axhline(0, color=st["zero_line"], linestyle=":", linewidth=1.0)
    _azaxis(ax16_1, upwind=True, st=st)
    ax16_1.set_ylabel("Geometric Angle of Attack α_geometric (deg)", color=c_alpha, fontweight="bold", fontsize=st["label_fs"])
    ax16_2.set_ylabel("Geometric Relative Velocity W/U∞ (-)", color=c_vel, fontweight="bold", fontsize=st["label_fs"])
    lines = l1 + l2
    labels = [str(ln.get_label()) for ln in lines]
    _style_legend(ax16_1, st, handles=lines, labels=labels, loc="upper right", frameon=True, framealpha=0.92, fontsize=st["leg_fs"])
    ax16_1.set_title("Figure 16: Geometric Kinematics vs. Azimuth (No Flow Induction)", **_title_kw(st, pad=8))
    ax16_1.text(
        0.5, 0.02,
        "Notice: Geometric kinematic values do not account for rotor induction flow deceleration.",
        transform=ax16_1.transAxes, ha="center", va="bottom", fontsize=8.0,
        fontstyle="italic", color=st["notice_fg"],
        bbox=dict(boxstyle="square,pad=0.2", fc=st["notice_fc"], ec=st["notice_ec"], lw=0.8),
    )
    return fig


def _plot_17_global_coefficients(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    az = R["az"]
    th = az["theta"]
    pal = st["pal"]

    fig = _prepare_fig((8.5, 4.8), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)
    ax.plot(th, az["cp_mean"], color=pal[0], linewidth=2.2, linestyle="-", label=r"Power Coefficient $C_p(\theta)$")
    ax.plot(th, az["cm_mean"], color=pal[2], linewidth=2.0, linestyle="-.", label=r"Moment Coefficient $C_m(\theta)$")
    if "ct_mean" in az and az["ct_mean"] is not None:
        ax.plot(th, az["ct_mean"], color=pal[1], linewidth=1.8, linestyle="--", label=r"Thrust Coefficient $C_t(\theta)$")
    if "cy_mean" in az and az["cy_mean"] is not None:
        ax.plot(th, az["cy_mean"], color=pal[4], linewidth=1.8, linestyle=":", label=r"Lateral Force Coefficient $C_y(\theta)$")
    ax.axhline(0, color=st["zero_line"], linestyle="-", linewidth=0.9, alpha=0.75)
    _azaxis(ax, upwind=True, st=st)
    ax.set_ylabel("Dimensionless Aerodynamic Coefficient (-)", fontweight="bold", color=st["fg"], fontsize=st["label_fs"])
    ax.set_title("Figure 17: Global Rotor Performance Coefficients (Cp, Cm, Ct, Cy)", **_title_kw(st, pad=8))
    _style_legend(ax, st, loc="upper right", frameon=True, framealpha=0.92, fontsize=st["leg_fs"])
    return fig


def _plot_18_cp_convergence(R: Dict[str, Any], st: Dict[str, Any], dpi: int, target_fig: Optional[Figure]) -> Figure:
    p, revs, res = R["p"], R["revs"], R["res"]
    pal = st["pal"]

    fig = _prepare_fig((8.5, 4.8), dpi, st, target_fig)
    ax = fig.add_subplot(111)
    _style_cartesian_ax(ax, st)

    rev_nums = [r["k"] for r in revs]
    rev_cps = [r["cp"] for r in revs]
    ax.plot(rev_nums, rev_cps, marker="o", markersize=6, color=pal[0], linewidth=2.0, label="Revolution Mean Cp")
    ax.axhline(res["cp_mean"], color=pal[2], linestyle="-", linewidth=1.8, label=f"Reported Mean Cp = {res['cp_mean']:.4f}")
    if isinstance(res.get("cp_extrapolated_aitken"), (int, float)):
        ax.axhline(res["cp_extrapolated_aitken"], color=pal[4], linestyle="--", linewidth=1.5, label=f"Aitken Extrapolated Limit = {res['cp_extrapolated_aitken']:.4f}")
    from matplotlib.ticker import MaxNLocator

    tol_pct = float(getattr(p, "stable_tol", 5.0) or 5.0)
    band_half = abs(float(res["cp_mean"])) * (tol_pct / 100.0)
    band_lo = float(res["cp_mean"]) - band_half
    band_hi = float(res["cp_mean"]) + band_half
    if len(rev_nums) > 1:
        ax.fill_between(rev_nums, band_lo, band_hi, color=pal[2], alpha=0.15, label=f"±{tol_pct:g}% Stability Tolerance Band")
    else:
        ax.axhspan(band_lo, band_hi, color=pal[2], alpha=0.15, label=f"±{tol_pct:g}% Stability Tolerance Band")

    is_contiguous = len(rev_nums) >= 2 and (rev_nums[-1] - rev_nums[-2] == 1)
    last_dcp = (
        float(abs(rev_cps[-1] - rev_cps[-2]) / abs(rev_cps[-2]))
        if is_contiguous and abs(rev_cps[-2]) > 1e-6
        else abs(float(res.get("cp_last_change") or 0.0))
    )
    is_st = bool(res.get("is_stable", False))
    stability_flag = "PERIODIC STABLE" if is_st else "TRANSIENT/UNSTABLE"
    flag_color = st["badge_ok"] if is_st else st["badge_warn"]
    ax.text(
        0.03, 0.94,
        f"Convergence Flag: {stability_flag}\nLast ΔCp: {last_dcp * 100:.2f}% (Threshold < {tol_pct:g}%)",
        transform=ax.transAxes, ha="left", va="top", fontsize=9.0, fontweight="bold", color=flag_color,
        bbox=dict(boxstyle="round,pad=0.3", fc=st["annot_fc"], ec=flag_color, lw=1.2),
    )
    ax.set_xlabel("Revolution Number", fontweight="bold", color=st["fg"], fontsize=st["label_fs"])
    ax.set_ylabel("Mean Power Coefficient Cp (-)", fontweight="bold", color=st["fg"], fontsize=st["label_fs"])
    if len(rev_nums) <= 16:
        ax.set_xticks(rev_nums)
    else:
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_title("Figure 18: Cp Convergence History & Stability Validation", **_title_kw(st, pad=8))
    _style_legend(ax, st, loc="lower right", frameon=True, framealpha=0.92, fontsize=st["leg_fs"])
    return fig


_FIGURE_BUILDERS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any], int, Optional[Figure]], Figure]] = {
    "01_cp_history.png": _plot_01_cp_history,
    "02_cp_per_revolution.png": _plot_02_cp_per_rev,
    "03_cp_vs_azimuth.png": _plot_03_cp_vs_azimuth,
    "04_blade_torque_vs_azimuth.png": _plot_04_blade_torque,
    "05_polar_blade_cp.png": _plot_05_polar_cp,
    "06_ct_cy.png": _plot_06_ct_cy,
    "07_cm.png": _plot_07_cm,
    "08_alpha_w_re.png": _plot_08_alpha_w_re,
    "09_cp_spectrum.png": _plot_09_spectrum,
    "10_convergence.png": _plot_10_convergence,
    "11_cp_heatmap.png": _plot_11_heatmap,
    "12_dashboard.png": _plot_12_dashboard,
    "13_power_contribution_quartiles.png": _plot_13_quartiles,
    "14_cumulative_moment_distribution.png": _plot_14_cumulative_moment,
    "15_blade_tangential_load.png": _plot_15_blade_tangential,
    "16_geometric_kinematics.png": _plot_16_geometric_kinematics,
    "17_global_rotor_coefficients.png": _plot_17_global_coefficients,
    "18_cp_convergence_history.png": _plot_18_cp_convergence,
}


def build_result_figure(
    fig_key: str,
    R: Dict[str, Any],
    dark_mode: bool = False,
    plot_opts: Optional[PlotOptions] = None,
    target_fig: Optional[Figure] = None,
) -> Figure:
    """Build a single Matplotlib Figure for the given result key in light or dark theme."""
    builder = _FIGURE_BUILDERS.get(fig_key)
    if builder is None:
        raise KeyError(f"Unknown figure key: {fig_key}")
    dpi_val = plot_opts.dpi if plot_opts else (105 if dark_mode else 130)
    st = _make_theme_spec(dark_mode=dark_mode, opts=plot_opts)
    fig = builder(R, st, dpi_val, target_fig)
    if plot_opts and plot_opts.fixed_y_limits and fig_key in plot_opts.fixed_y_limits:
        ylo, yhi = plot_opts.fixed_y_limits[fig_key]
        for ax in fig.get_axes():
            if ax.get_label() != "<colorbar>" and getattr(ax, "name", "") != "polar":
                ax.set_ylim(ylo, yhi)
    return fig


def make_figures(
    R: Dict[str, Any],
    outdir: str,
    log: Callable[[str], None] = print,
    plot_opts: Optional[PlotOptions] = None,
) -> Dict[str, str]:
    """Generate all publication-quality figures and save them to disk in configured formats (PNG/PDF/SVG)."""
    os.makedirs(outdir, exist_ok=True)
    figs: Dict[str, str] = {}

    formats = plot_opts.formats if (plot_opts and plot_opts.formats) else ["png"]
    dpi_save = plot_opts.dpi if plot_opts else 130
    rem_titles = plot_opts.remove_titles if plot_opts else False

    with plt.rc_context(_build_rc_dict(plot_opts)):
        for name in RESULT_FIGURE_KEYS:
            fig = build_result_figure(name, R, dark_mode=False, plot_opts=plot_opts)
            base, _ = os.path.splitext(name)
            if rem_titles:
                fig.suptitle("")
                for ax in fig.get_axes():
                    ax.set_title("")
            norm_exts = [fmt.lstrip(".").lower() for fmt in formats if fmt and fmt.lstrip(".")]
            if not norm_exts:
                norm_exts = ["png"]
            primary_path: Optional[str] = None
            for ext in norm_exts:
                target_path = os.path.join(outdir, f"{base}.{ext}")
                fig.savefig(target_path, bbox_inches="tight", dpi=dpi_save)
                if ext == "png" or primary_path is None:
                    primary_path = target_path
            fig.clear()
            figs[name] = primary_path or os.path.join(outdir, f"{base}.png")

    export_captions(R, outdir)
    log(f"Rendered {len(figs)} publication figures in {outdir}")
    return figs
