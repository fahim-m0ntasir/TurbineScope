# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Multi-Run Comparison & Verification Engine (Tier 3.3)
=============================================================================
Provides cross-run comparisons, case metadata diffing, phase-averaged torque
difference metrics, and the Grid / Time-Step Convergence Index (GCI) according
to Roache (1998) and Celik et al. (2008) ASME V&V 20 standards.
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Any

import numpy as np
import matplotlib
if matplotlib.get_backend().lower() not in ("agg", "qtagg"):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as COL


def load_run(path_or_dir: str) -> Dict[str, Any]:
    """Load results.json from a directory or json file."""
    if os.path.isdir(path_or_dir):
        target = os.path.join(path_or_dir, "results.json")
        if not os.path.isfile(target):
            # Find any *_results.json in the folder
            cands = [f for f in os.listdir(path_or_dir) if f.endswith("_results.json")]
            if cands:
                target = os.path.join(path_or_dir, cands[0])
            else:
                raise FileNotFoundError(f"No results.json found in directory: {path_or_dir}")
    else:
        target = path_or_dir

    if not os.path.isfile(target):
        raise FileNotFoundError(f"Run results file not found: {target}")

    with open(target, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["_source_path"] = target
    data["_run_name"] = os.path.splitext(os.path.basename(target))[0].replace("_results", "")
    return data


def compare_runs(
    runs: List[Dict[str, Any]],
    labels: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Compare two or more simulation runs.

    Computes:
    - KPI comparison table
    - Case metadata diff (highlights what changed between runs)
    - Cm and Cp azimuth difference metrics (RMS, max absolute diff, correlation)
    """
    if len(runs) < 2:
        raise ValueError("At least 2 runs are required for comparison.")

    if labels is None or len(labels) != len(runs):
        labels = [r.get("parameters", {}).get("preset_name") or r.get("_run_name", f"Run {i + 1}") for i, r in enumerate(runs)]

    # 1. Summary KPI comparison table
    kpi_keys = [
        ("cp_reported_mean", "Mean Cp (reported)", "-"),
        ("cp_trailing_stable_mean", "Mean Cp (trailing stable)", "-"),
        ("cp_all_revolutions_mean", "Mean Cp (all revolutions)", "-"),
        ("cp_extrapolated_aitken", "Aitken extrapolated Cp", "-"),
        ("extra_revs_to_converge", "Extra revs needed", "revs"),
        ("cm_mean", "Mean Cm", "-"),
        ("torque_mean", "Mean Torque", "N·m"),
        ("power_mean_W", "Mean Power", "W"),
        ("ct_mean", "Mean Ct (Fx)", "-"),
        ("cy_mean", "Mean Cy (Fy)", "-"),
        ("alpha_max", "Max Angle of Attack", "deg"),
        ("ripple_pp", "Torque Ripple Factor", "-"),
        ("is_stable", "Convergence Status", "-"),
    ]

    kpi_table = []
    for key, label, unit in kpi_keys:
        row = {"metric": label, "unit": unit, "values": []}
        for r in runs:
            v = r.get("summary_kpis", {}).get(key)
            row["values"].append(v)
        kpi_table.append(row)

    # 2. Case Metadata Diff (highlighting what changed)
    all_meta_keys = set()
    for r in runs:
        all_meta_keys.update(r.get("case_metadata", {}).keys())
        all_meta_keys.update(r.get("parameters", {}).keys())
    all_meta_keys.discard("sources")

    meta_diff = []
    for k in sorted(all_meta_keys):
        vals = []
        for r in runs:
            val = r.get("case_metadata", {}).get(k)
            if val is None:
                val = r.get("parameters", {}).get(k)
            vals.append(val)
        # Check if values differ across runs
        first = vals[0]
        has_difference = any(v != first for v in vals[1:])
        meta_diff.append({
            "field": k,
            "values": vals,
            "is_different": has_difference
        })

    # 3. Azimuth overlay comparison (against Run 1 as baseline)
    az_metrics = []
    has_az = all("azimuth_curves" in r and r["azimuth_curves"].get("theta") for r in runs)
    if has_az:
        th_ref = np.array(runs[0]["azimuth_curves"]["theta"])
        cm_ref = np.array(runs[0]["azimuth_curves"]["cm_mean"])
        cp_ref = np.array(runs[0]["azimuth_curves"]["cp_mean"])

        for i in range(1, len(runs)):
            th_i = np.array(runs[i]["azimuth_curves"]["theta"])
            cm_i = np.array(runs[i]["azimuth_curves"]["cm_mean"])
            cp_i = np.array(runs[i]["azimuth_curves"]["cp_mean"])

            # Interpolate to common azimuth grid if necessary
            if not np.array_equal(th_ref, th_i):
                cm_i = np.interp(th_ref, th_i, cm_i, period=360.0)
                cp_i = np.interp(th_ref, th_i, cp_i, period=360.0)

            diff_cm = cm_i - cm_ref
            diff_cp = cp_i - cp_ref
            rms_cm = float(np.sqrt(np.mean(diff_cm ** 2)))
            max_cm = float(np.max(np.abs(diff_cm)))
            theta_max_cm = float(th_ref[np.argmax(np.abs(diff_cm))])
            rms_cp = float(np.sqrt(np.mean(diff_cp ** 2)))

            # Correlation coefficient
            if np.std(cm_ref) > 1e-9 and np.std(cm_i) > 1e-9:
                corr_cm = float(np.corrcoef(cm_ref, cm_i)[0, 1])
            else:
                corr_cm = 1.0

            az_metrics.append({
                "comparison": f"{labels[i]} vs {labels[0]}",
                "rms_cm": rms_cm,
                "max_diff_cm": max_cm,
                "theta_max_diff": theta_max_cm,
                "rms_cp": rms_cp,
                "correlation": corr_cm
            })

    return {
        "labels": labels,
        "runs": runs,
        "kpi_table": kpi_table,
        "meta_diff": meta_diff,
        "az_metrics": az_metrics,
        "has_azimuth": has_az
    }


def calculate_gci(
    runs: List[Dict[str, Any]],
    key_metric: str = "cp_reported_mean",
    study_type: str = "mesh",  # "mesh" or "timestep"
    labels: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Calculate Grid Convergence Index (GCI) from 3 simulation runs.

    Follows Roache (1998) and Celik et al. (2008) ASME V&V 20 guidelines.

    Args:
        runs: Exactly 3 run dictionaries
        key_metric: Summary KPI name to analyze (default: "cp_reported_mean")
        study_type: "mesh" (uses mesh cell count) or "timestep" (uses dt)
        labels: Optional run labels

    Returns:
        Structured GCI report dictionary with apparent order p, extrapolated value,
        approximate relative error, and GCI percentages.
    """
    if len(runs) != 3:
        raise ValueError(f"GCI calculation requires exactly 3 runs (fine, medium, coarse); received {len(runs)}.")

    if labels is None or len(labels) != 3:
        labels = [r.get("parameters", {}).get("preset_name") or r.get("_run_name", f"Run {i + 1}") for i, r in enumerate(runs)]

    # Refuse GCI calculation if non-study metadata differs across runs (ASME V&V 20 requirement)
    def _numeric_differs(vals: List[Any], rel_tol: float = 0.002) -> bool:
        present = [v for v in vals if v is not None]
        if not present:
            return False
        if len(present) != len(vals):
            return True
        try:
            fvals = [float(v) for v in present]
        except (TypeError, ValueError):
            return len(set(present)) > 1
        ref = max(abs(max(fvals)), abs(min(fvals)), 1e-12)
        return (max(fvals) - min(fvals)) / ref > rel_tol

    mismatches = []
    if study_type == "mesh":
        turb_models = [r.get("case_metadata", {}).get("turb_model") for r in runs]
        if any(tm is not None for tm in turb_models) and len(set(turb_models)) > 1:
            mismatches.append(f"Turbulence model differs: {turb_models}")
        dts = [r.get("case_metadata", {}).get("time_step_s") or r.get("parameters", {}).get("dt") for r in runs]
        if _numeric_differs(dts):
            mismatches.append(f"Time step dt differs: {dts}")
        for param_key in ("U", "R", "tsr", "omega"):
            vals = [r.get("parameters", {}).get(param_key) for r in runs]
            if _numeric_differs(vals):
                mismatches.append(f"Parameter '{param_key}' differs: {vals}")
    else:  # "timestep"
        mesh_counts = [r.get("case_metadata", {}).get("mesh_cells") for r in runs]
        if any(mc is not None for mc in mesh_counts) and len(set(mesh_counts)) > 1:
            mismatches.append(f"Mesh cell count differs: {mesh_counts}")
        turb_models = [r.get("case_metadata", {}).get("turb_model") for r in runs]
        if any(tm is not None for tm in turb_models) and len(set(turb_models)) > 1:
            mismatches.append(f"Turbulence model differs: {turb_models}")
        for param_key in ("U", "R", "tsr", "omega"):
            vals = [r.get("parameters", {}).get(param_key) for r in runs]
            if _numeric_differs(vals):
                mismatches.append(f"Parameter '{param_key}' differs: {vals}")

    if mismatches:
        raise ValueError(
            f"GCI {study_type} convergence verification refused per ASME V&V 20: "
            f"non-{study_type} parameters differ across runs ({'; '.join(mismatches)}). "
            "All simulation parameters except the studied refinement parameter must be strictly identical."
        )

    # Extract characteristic scales h and solution values phi
    # In 2D: characteristic cell size h = (domain_area / N_cells)^0.5
    triplets = []
    for i, r in enumerate(runs):
        raw_phi = r.get("summary_kpis", {}).get(key_metric)
        if raw_phi is None:
            raise ValueError(f"Run '{labels[i]}' is missing summary KPI '{key_metric}' for GCI study.")
        phi = float(raw_phi)
        if study_type == "mesh":
            cells = r.get("case_metadata", {}).get("mesh_cells")
            if not cells or cells <= 0:
                raise ValueError(f"Run '{labels[i]}' is missing a valid positive mesh_cells metadata field for GCI study.")
            h = 1.0 / math.sqrt(float(cells))
        else:
            dt = r.get("case_metadata", {}).get("time_step_s") or r.get("parameters", {}).get("dt")
            if not dt or dt <= 0:
                raise ValueError(f"Run '{labels[i]}' is missing a valid positive time_step_s for GCI study.")
            h = float(dt)
        triplets.append((h, phi, labels[i], r))

    # Sort from fine (1) to medium (2) to coarse (3): h1 < h2 < h3
    triplets.sort(key=lambda x: x[0])
    (h1, phi1, lbl1, r1), (h2, phi2, lbl2, r2), (h3, phi3, lbl3, r3) = triplets

    r21 = h2 / h1
    r32 = h3 / h2
    if r21 <= 1.0 + 1e-9 or r32 <= 1.0 + 1e-9:
        raise ValueError(
            f"GCI requires 3 strictly distinct refinement levels (h1 < h2 < h3); "
            f"got r21 = {r21:.4f}, r32 = {r32:.4f}."
        )

    r_warning = None
    if r21 < 1.3 or r32 < 1.3:
        r_warning = f"Notice: ASME V&V 20 recommends constant refinement ratio r >= 1.3 (current: r21 = {r21:.2f}, r32 = {r32:.2f}). Smaller ratios can amplify numerical noise."

    eps21 = phi2 - phi1
    eps32 = phi3 - phi2

    if abs(eps21) < 1e-12 or abs(eps32) < 1e-12:
        return {
            "status": "CONVERGED_EXACT",
            "study_type": study_type,
            "key_metric": key_metric,
            "fine_label": lbl1, "medium_label": lbl2, "coarse_label": lbl3,
            "message": "Solution difference between grids is negligible; numerical discretization error is already below machine precision.",
            "phi1": phi1, "phi2": phi2, "phi3": phi3,
            "h1": h1, "h2": h2, "h3": h3,
            "r21": r21, "r32": r32,
            "r_warning": r_warning,
            "gci_fine_pct": 0.0,
            "observed_order_p": 2.0,
            "phi_ext": phi1
        }

    R_ratio = eps21 / eps32
    if R_ratio < 0.0:
        # Oscillatory convergence
        return {
            "status": "OSCILLATORY_CONVERGENCE",
            "study_type": study_type,
            "key_metric": key_metric,
            "fine_label": lbl1, "medium_label": lbl2, "coarse_label": lbl3,
            "message": (
                f"Oscillatory convergence detected (ratio of differences eps21/eps32 = {R_ratio:.3f} < 0). "
                "Per ASME V&V 20 and Celik et al. (2008), monotonic power-law Richardson extrapolation is not valid for oscillatory data."
            ),
            "phi1": phi1, "phi2": phi2, "phi3": phi3,
            "h1": h1, "h2": h2, "h3": h3,
            "r21": r21, "r32": r32,
            "r_warning": r_warning,
            "gci_fine_pct": float(abs((phi1 - phi2) / max(abs(phi1), 1e-6)) * 125.0),
            "observed_order_p": float("nan"),
            "phi_ext": float(0.5 * (phi1 + phi2)),
        }

    if R_ratio >= 1.0:
        return {
            "status": "DIVERGENT",
            "study_type": study_type,
            "key_metric": key_metric,
            "fine_label": lbl1, "medium_label": lbl2, "coarse_label": lbl3,
            "message": (
                f"Monotonic divergence detected (ratio of differences eps21/eps32 = {R_ratio:.3f} >= 1.0). "
                "Richardson extrapolation is not valid for divergent solutions."
            ),
            "phi1": phi1, "phi2": phi2, "phi3": phi3,
            "h1": h1, "h2": h2, "h3": h3,
            "r21": r21, "r32": r32,
            "r_warning": r_warning,
            "gci_fine_pct": float(abs((phi1 - phi2) / max(abs(phi1), 1e-6)) * 125.0),
            "observed_order_p": float("nan"),
            "phi_ext": float("nan"),
            "convergence_mode": "Divergence",
        }

    oscillatory = (R_ratio < 0.0)
    monotonic = (0.0 < R_ratio < 1.0)

    # Solve for apparent order p via fixed-point iteration
    s = 1.0 if (eps32 / eps21) > 0 else -1.0
    p = abs(math.log(abs(eps32 / eps21)) / math.log(r21))
    for _ in range(50):
        try:
            q = math.log((r21 ** p - s) / (r32 ** p - s))
            p_next = (1.0 / math.log(r21)) * abs(math.log(abs(eps32 / eps21)) + q)
            if abs(p_next - p) < 1e-6:
                p = p_next
                break
            p = 0.5 * (p + p_next)
        except Exception:
            break

    # Bound observed order p to physical range
    p = max(0.1, min(10.0, float(p)))

    # Richardson extrapolation to h -> 0
    denom = (r21 ** p) - 1.0
    phi_ext = phi1 + (phi1 - phi2) / denom if abs(denom) > 1e-9 else phi1

    # Approximate and extrapolated relative errors
    ea21_pct = abs((phi1 - phi2) / phi1) * 100.0 if abs(phi1) > 1e-9 else 0.0
    e_ext21_pct = abs((phi_ext - phi1) / phi_ext) * 100.0 if abs(phi_ext) > 1e-9 else 0.0

    ea32_pct = abs((phi2 - phi3) / phi2) * 100.0 if abs(phi2) > 1e-9 else 0.0

    # Safety factor Fs = 1.25 for three-grid study
    Fs = 1.25
    gci21_pct = (Fs * ea21_pct) / denom if abs(denom) > 1e-9 else 0.0
    denom32 = (r32 ** p) - 1.0
    gci32_pct = (Fs * ea32_pct) / denom32 if abs(denom32) > 1e-9 else 0.0

    # Asymptotic range check: GCI32 / (r21^p * GCI21) should be close to 1.0
    asymptotic_ratio = (gci32_pct / ((r21 ** p) * gci21_pct)) if gci21_pct > 0 else 1.0
    is_in_asymptotic_range = abs(asymptotic_ratio - 1.0) <= 0.15

    return {
        "status": "SUCCESS",
        "study_type": study_type,
        "key_metric": key_metric,
        "fine_label": lbl1, "medium_label": lbl2, "coarse_label": lbl3,
        "h1": h1, "h2": h2, "h3": h3,
        "phi1": phi1, "phi2": phi2, "phi3": phi3,
        "r21": r21, "r32": r32,
        "observed_order_p": p,
        "phi_ext": phi_ext,
        "ea21_pct": ea21_pct,
        "e_ext21_pct": e_ext21_pct,
        "gci_fine_pct": gci21_pct,
        "gci_medium_pct": gci32_pct,
        "asymptotic_ratio": asymptotic_ratio,
        "is_asymptotic": is_in_asymptotic_range,
        "convergence_mode": "Monotonic convergence" if monotonic else ("Oscillatory convergence" if oscillatory else "Divergence"),
    }


def plot_comparison_figures(
    comp: Dict[str, Any],
    outdir: str,
    prefix: str = "comparison"
) -> Dict[str, str]:
    """Render publication-quality comparison figures."""
    os.makedirs(outdir, exist_ok=True)
    figs = {}
    labels = comp["labels"]
    runs = comp["runs"]

    # Figure 1: Cp per revolution comparison
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=300)
    for i, r in enumerate(runs):
        revs = r.get("revolutions", [])
        if revs:
            k_vals = [rev["k"] for rev in revs]
            cp_vals = [rev["cp"] for rev in revs]
            ax.plot(k_vals, cp_vals, marker="o", label=labels[i], linewidth=1.8, markersize=5)
    ax.set_xlabel("Rotor Revolution Number $k$", fontsize=11, fontweight="bold")
    ax.set_ylabel("Revolution-Averaged Power Coefficient $C_p$", fontsize=11, fontweight="bold")
    ax.set_title("Convergence Trajectory Comparison Across Runs", fontsize=12, fontweight="bold", pad=12)
    ax.grid(True, linestyle="--", alpha=0.6)
    ax.legend(frameon=True, fontsize=10)
    plt.tight_layout()
    f1 = os.path.join(outdir, f"{prefix}_cp_revolutions.png")
    fig.savefig(f1)
    plt.close(fig)
    figs["cp_revolutions"] = f1

    # Figure 2: Cm vs Azimuth Overlay & Difference Plot
    if comp["has_azimuth"]:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.5, 6.5), dpi=300, sharex=True, gridspec_kw={"height_ratios": [2, 1]})
        th_ref = runs[0]["azimuth_curves"]["theta"]
        cm_ref = runs[0]["azimuth_curves"]["cm_mean"]

        for i, r in enumerate(runs):
            th = r["azimuth_curves"]["theta"]
            cm = r["azimuth_curves"]["cm_mean"]
            ax1.plot(th, cm, label=labels[i], linewidth=1.8)

        ax1.set_ylabel("Moment Coefficient $C_m$", fontsize=11, fontweight="bold")
        ax1.set_title("Phase-Averaged $C_m(\\theta)$ Profile Comparison", fontsize=12, fontweight="bold")
        ax1.grid(True, linestyle="--", alpha=0.6)
        ax1.legend(frameon=True, fontsize=9)

        # Difference subplot vs run 1
        for i in range(1, len(runs)):
            th = np.array(runs[i]["azimuth_curves"]["theta"])
            cm = np.array(runs[i]["azimuth_curves"]["cm_mean"])
            if not np.array_equal(th_ref, th):
                cm = np.interp(th_ref, th, cm, period=360.0)
            diff = cm - np.array(cm_ref)
            ax2.plot(th_ref, diff, label=f"{labels[i]} - {labels[0]}", linewidth=1.5)

        ax2.axhline(0, color="black", linestyle=":", alpha=0.7)
        ax2.set_xlabel("Rotor Azimuth $\\theta$ (deg)", fontsize=11, fontweight="bold")
        ax2.set_ylabel("$\\Delta C_m$ vs Baseline", fontsize=10, fontweight="bold")
        ax2.set_xlim(0, 360)
        ax2.grid(True, linestyle="--", alpha=0.6)
        ax2.legend(frameon=True, fontsize=8)

        plt.tight_layout()
        f2 = os.path.join(outdir, f"{prefix}_cm_azimuth_overlay.png")
        fig.savefig(f2)
        plt.close(fig)
        figs["cm_azimuth_overlay"] = f2

    return figs


def plot_gci_extrapolation(
    gci: Dict[str, Any],
    filepath: str
) -> str:
    """Render Richardson extrapolation curve with GCI error band towards h -> 0."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=300)
    try:
        h_pts = np.array([gci["h1"], gci["h2"], gci["h3"]])

        # Theoretical continuum curve phi(h) = phi_ext + alpha * h^p (when valid)
        p = gci.get("observed_order_p", float("nan"))
        phi_ext = gci.get("phi_ext", float("nan"))
        has_fit = (
            isinstance(p, (int, float)) and math.isfinite(p)
            and isinstance(phi_ext, (int, float)) and math.isfinite(phi_ext)
        )
        if has_fit:
            alpha = (gci["phi1"] - phi_ext) / (gci["h1"] ** p) if gci["h1"] > 0 else 0.0
            h_fine_grid = np.linspace(0, max(h_pts) * 1.15, 200)
            phi_model = phi_ext + alpha * (h_fine_grid ** p)
            ax.plot(h_fine_grid, phi_model, "k--", label=f"Richardson Fit ($p={p:.2f}$)", linewidth=1.5)

        # Plot grid points with fine GCI error bar on grid 1
        gci_val = gci["gci_fine_pct"] / 100.0 * abs(gci["phi1"])
        ax.errorbar(
            [gci["h1"]], [gci["phi1"]], yerr=[gci_val], fmt="ro", markersize=7,
            capsize=5, capthick=1.5, label=f"Fine Grid 1 $\\pm \\text{{GCI}}_{{21}}$ ({gci['gci_fine_pct']:.2f}%)"
        )
        ax.plot([gci["h2"]], [gci["phi2"]], "bs", markersize=6, label=f"Medium Grid 2 ({gci.get('medium_label', 'Medium')})")
        ax.plot([gci["h3"]], [gci["phi3"]], "g^", markersize=6, label=f"Coarse Grid 3 ({gci.get('coarse_label', 'Coarse')})")

        # Plot extrapolated zero-grid point
        if has_fit:
            ax.plot([0], [phi_ext], "m*", markersize=10, label=f"Extrapolated ($h \\to 0$): {phi_ext:.4f}")

        scale_lbl = "$h = 1/\\sqrt{N_{\\text{cells}}}$" if gci.get("study_type") == "mesh" else "$\\Delta t$ (s)"
        ax.set_xlabel(f"Discretization Scale ({scale_lbl})", fontsize=11, fontweight="bold")
        ax.set_ylabel(f"Solution Metric ({gci.get('key_metric', 'Cp')})", fontsize=11, fontweight="bold")
        p_txt = f"Observed Order $p = {p:.2f}$" if has_fit else f"Status: {gci.get('status', 'N/A')}"
        ax.set_title(f"Grid Convergence Index (GCI) Verification Study ({p_txt})", fontsize=12, fontweight="bold", pad=12)
        ax.grid(True, linestyle="--", alpha=0.6)
        ax.legend(frameon=True, fontsize=9)

        fig.tight_layout()
        fig.savefig(filepath)
    finally:
        plt.close(fig)
    return filepath


def export_comparison_excel(
    comp: Dict[str, Any],
    filepath: str,
    gci: Optional[Dict[str, Any]] = None
) -> None:
    """Export structured multi-run comparison workbook (.xlsx) via openpyxl."""
    wb = Workbook()
    ws_kpi: Any = wb.active
    ws_kpi.title = "KPI_Comparison"

    HF = PatternFill("solid", fgColor="1F3864")
    DIFF_F = PatternFill("solid", fgColor="FFF2CC")
    F_H = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    F_B = Font(name="Arial", size=10, bold=True)
    F_N = Font(name="Arial", size=10)

    # 1. KPI Comparison Sheet
    ws_kpi.cell(row=1, column=1, value="VAWT Multi-Run Simulation Comparison").font = Font(size=14, bold=True, color="1F3864")
    labels = comp["labels"]

    # Header
    ws_kpi.cell(row=3, column=1, value="Key Performance Indicator").font, ws_kpi.cell(row=3, column=1).fill = F_H, HF
    ws_kpi.cell(row=3, column=2, value="Unit").font, ws_kpi.cell(row=3, column=2).fill = F_H, HF
    for col_idx, lbl in enumerate(labels):
        c = ws_kpi.cell(row=3, column=3 + col_idx, value=lbl)
        c.font, c.fill = F_H, HF
        c.alignment = Alignment(horizontal="center")

    for row_idx, kpi in enumerate(comp["kpi_table"]):
        r_num = 4 + row_idx
        ws_kpi.cell(row=r_num, column=1, value=kpi["metric"]).font = F_B
        ws_kpi.cell(row=r_num, column=2, value=kpi["unit"]).font = F_N
        for col_idx, val in enumerate(kpi["values"]):
            c = ws_kpi.cell(row=r_num, column=3 + col_idx, value=val)
            c.font = F_N
            if isinstance(val, float):
                c.number_format = "0.0000" if abs(val) < 10 else "0.00"

    ws_kpi.column_dimensions["A"].width = 36
    ws_kpi.column_dimensions["B"].width = 12
    for j in range(len(labels)):
        ws_kpi.column_dimensions[COL(3 + j)].width = 22

    # 2. Case Metadata Diff Sheet
    ws_diff = wb.create_sheet("Case_Metadata_Diff")
    ws_diff.cell(row=1, column=1, value="Simulation Setup & Case Metadata Diff").font = Font(size=14, bold=True, color="1F3864")
    ws_diff.cell(row=2, column=1, value="Highlighted rows indicate parameters that differ across runs.").font = F_N

    ws_diff.cell(row=4, column=1, value="Parameter / Setting").font, ws_diff.cell(row=4, column=1).fill = F_H, HF
    for col_idx, lbl in enumerate(labels):
        c = ws_diff.cell(row=4, column=2 + col_idx, value=lbl)
        c.font, c.fill = F_H, HF
        c.alignment = Alignment(horizontal="center")

    for row_idx, item in enumerate(comp["meta_diff"]):
        r_num = 5 + row_idx
        fill_row = DIFF_F if item["is_different"] else None
        c1 = ws_diff.cell(row=r_num, column=1, value=item["field"])
        c1.font = F_B if item["is_different"] else F_N
        c1.fill = fill_row if fill_row else PatternFill(fill_type=None)
        for col_idx, val in enumerate(item["values"]):
            c = ws_diff.cell(row=r_num, column=2 + col_idx, value=str(val) if val is not None else "-")
            c.font = F_B if item["is_different"] else F_N
            if fill_row:
                c.fill = fill_row

    ws_diff.column_dimensions["A"].width = 32
    for j in range(len(labels)):
        ws_diff.column_dimensions[COL(2 + j)].width = 24

    # 3. GCI Sheet if available
    if gci and gci.get("status") == "SUCCESS":
        ws_gci = wb.create_sheet("GCI_Verification")
        ws_gci.cell(row=1, column=1, value="Grid / Time-Step Convergence Index (GCI) Analysis").font = Font(size=14, bold=True, color="1F3864")
        ws_gci.cell(row=2, column=1, value="Evaluation per Roache (1998) & Celik et al. (2008) ASME V&V 20 guidelines.").font = F_N

        ws_gci.cell(row=4, column=1, value="GCI Verification Metric").font, ws_gci.cell(row=4, column=1).fill = F_H, HF
        ws_gci.cell(row=4, column=2, value="Value").font, ws_gci.cell(row=4, column=2).fill = F_H, HF
        ws_gci.cell(row=4, column=3, value="Notes & Acceptance Criteria").font, ws_gci.cell(row=4, column=3).fill = F_H, HF

        gci_rows = [
            ("Analyzed solution metric", gci["key_metric"], "KPI targeted for convergence analysis"),
            ("Study domain", gci["study_type"].title(), "Mesh spatial resolution or physical time-step"),
            ("Fine grid solution (φ1)", gci["phi1"], f"Fine level: {gci['fine_label']}"),
            ("Medium grid solution (φ2)", gci["phi2"], f"Medium level: {gci['medium_label']}"),
            ("Coarse grid solution (φ3)", gci["phi3"], f"Coarse level: {gci['coarse_label']}"),
            ("Grid refinement ratio r21", f"{gci['r21']:.3f}", "Refinement factor h2 / h1"),
            ("Grid refinement ratio r32", f"{gci['r32']:.3f}", "Refinement factor h3 / h2"),
            ("Convergence mode", gci["convergence_mode"], "Monotonic convergence satisfies Richardson criteria"),
            ("Observed order of accuracy p", f"{gci['observed_order_p']:.3f}", "Theoretical order: 2.0 for 2nd order schemes"),
            ("Richardson extrapolated value (φ_ext)", gci["phi_ext"], "Asymptotic continuum solution as h -> 0"),
            ("Approximate relative error (ea21)", f"{gci['ea21_pct']:.3f}%", "|φ1 - φ2| / |φ1|"),
            ("Extrapolated relative error (e_ext21)", f"{gci['e_ext21_pct']:.3f}%", "|φ_ext - φ1| / |φ_ext|"),
            ("Fine Grid Convergence Index (GCI_fine)", f"{gci['gci_fine_pct']:.3f}%", "Uncertainty band with safety factor Fs = 1.25"),
            ("Medium Grid Convergence Index (GCI_medium)", f"{gci['gci_medium_pct']:.3f}%", "Medium level uncertainty band"),
            ("Asymptotic range check ratio", f"{gci['asymptotic_ratio']:.3f}", "GCI32 / (r21^p * GCI21); near 1.0 indicates asymptotic regime"),
            ("Asymptotic regime confirmed?", "YES" if gci["is_asymptotic"] else "NO", "Validates that grids are in asymptotic range"),
        ]

        for idx, (lbl, val, note) in enumerate(gci_rows):
            r_num = 5 + idx
            ws_gci.cell(row=r_num, column=1, value=lbl).font = F_B
            c = ws_gci.cell(row=r_num, column=2, value=val)
            c.font = F_B if "GCI" in lbl or "extrapolated" in lbl else F_N
            if isinstance(val, float):
                c.number_format = "0.0000"
            ws_gci.cell(row=r_num, column=3, value=note).font = F_N

        ws_gci.column_dimensions["A"].width = 38
        ws_gci.column_dimensions["B"].width = 24
        ws_gci.column_dimensions["C"].width = 55

    try:
        wb.save(filepath)
    finally:
        wb.close()
