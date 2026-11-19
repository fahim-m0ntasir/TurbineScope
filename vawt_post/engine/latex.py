# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Academic LaTeX / Overleaf Snippet Exporter
===================================================================
Generates publication-ready LaTeX snippets (`thesis_snippet.tex`) pre-filled
with computed performance metrics, booktabs tables, and graphicx figure blocks.
"""
from __future__ import annotations

import os
from typing import Dict, Any, Callable


def export_latex_snippet(
    R: Dict[str, Any],
    figs: Dict[str, str],
    filepath: str,
    log: Callable[[str], None] = print
) -> str:
    """Generate a ready-to-compile LaTeX snippet for thesis Chapter 4 / results section."""
    p, res, sel = R["p"], R["res"], R["sel"]

    lines = [
        r"% ============================================================================",
        r"% VAWT CFD Post-Processor: Automated Thesis Results Snippet (Overleaf / LaTeX)",
        r"% Generated automatically from simulation post-processing pipeline",
        r"% ============================================================================",
        r"\section{Aerodynamic Performance and Convergence Analysis}",
        r"",
        r"% --- 1. Simulation Setup and Operating Conditions Table ---",
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \small",
        r"  \caption{Simulation operating conditions and geometric rotor parameters.}",
        r"  \label{tab:vawt_simulation_parameters}",
        r"  \begin{tabular}{llcc}",
        r"    \toprule",
        r"    \textbf{Parameter} & \textbf{Symbol} & \textbf{Value} & \textbf{Unit} \\",
        r"    \midrule",
        f"    Free-stream velocity & $U_\\infty$ & {p.U:g} & m/s \\\\",
        f"    Tip speed ratio & $\\lambda$ & {res['tsr']:.3f} & -- \\\\",
        f"    Rotor angular velocity & $\\omega$ & {res['omega']:.3f} & rad/s ({res['omega'] * 30.0 / 3.141592653589793:.1f} RPM) \\\\",
        f"    Rotor radius & $R$ & {p.R:g} & m \\\\",
        f"    Blade chord & $c$ & {f'{p.chord:g}' if p.chord is not None else 'n/a'} & m \\\\",
        f"    Number of blades & $N$ & {p.n_blades} & -- \\\\",
        f"    2D reference depth & $H$ & {p.depth:g} & m \\\\",
        f"    Fluid density & $\\rho$ & {p.rho:g} & kg/m$^3$ \\\\",
        f"    Fluid dynamic viscosity & $\\mu$ & {p.mu:.4e} & Pa$\\cdot$s \\\\",
        f"    Frontal swept area & $A$ & {res['A']:.3f} & m$^2$ \\\\",
        f"    Reference wind power & $P_{{\\text{{ref}}}}$ & {res['Pref']:.2f} & W \\\\",
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
        r"",
        r"% --- 2. Computed Key Performance Indicators Table ---",
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \small",
        r"  \caption{Key performance indicators and aerodynamic convergence summary.}",
        r"  \label{tab:vawt_aerodynamic_kpis}",
        r"  \begin{tabular}{lccc}",
        r"    \toprule",
        r"    \textbf{Metric} & \textbf{Value} & \textbf{Unit} & \textbf{Status / Notes} \\",
        r"    \midrule",
        f"    Mean power coefficient ($C_p$) & {res['cp_mean']:.4f} & -- & Reported revolutions \\\\",
        f"    Mean moment coefficient ($C_m$) & {res['cm_mean']:.4f} & -- & $C_m = C_p / \\lambda$ \\\\",
        f"    Mean rotor power & {res['power_mean_W']:.2f} & W & $\\omega \\cdot \\bar{{M}}_{{\\text{{rotor}}}}$ \\\\",
        f"    Mean rotor torque & {res['torque_mean']:.4f} & N$\\cdot$m & Integrated time-average \\\\",
        f"    Convergence status & \\textbf{{{ 'CONVERGED / STABLE' if res['is_stable'] else 'NOT YET STABLE' }}} & -- & Criteria: $\\Delta C_p < {p.stable_tol:g}\\%$ \\\\",
        f"    Trailing stable revolutions & {res['stable_n']} & revs & Final consecutive stable set \\\\",
        f"    Last revolution change & {res['cp_last_change'] * 100.0:+.2f}\\% & -- & $\\Delta C_p / C_{{p,\\text{{prev}}}}$ \\\\",
        f"    Instantaneous $C_p$ extrema & [{res['cp_min']:.3f}, {res['cp_max']:.3f}] & -- & Min / Max over reported revs \\\\",
        f"    Peak-to-peak torque ripple & {res['ripple_pp']:.2f} & -- & $(C_{{p,\\max}} - C_{{p,\\min}}) / \\bar{{C}}_p$ \\\\",
        f"    Dominant ripple harmonic & {res['dominant_harmonic']:.2f}$\\times$ & rotor freq. & Expected blade pass: {p.n_blades}$\\times$ \\\\",
    ]

    if res.get("ct_mean") is not None:
        lines.append(f"    Mean thrust coefficient ($C_t$) & {res['ct_mean']:.4f} & -- & Streamwise force $F_x$ \\\\")
    if res.get("cy_mean") is not None:
        lines.append(f"    Mean side-force coefficient ($C_y$) & {res['cy_mean']:.4f} & -- & Lateral force $F_y$ \\\\")
    if res.get("span_H") is not None:
        lines += [
            f"    Turbine blade span ($H$) & {res['span_H']:g} & m & User-specified 3D span \\\\",
            f"    3D frontal swept area ($A_{{\\text{{3D}}}}$) & {res['swept_area_3d']:.3f} & m$^2$ & $2R \\cdot H$ \\\\",
            f"    Estimated 3D power ($P_{{\\text{{3D}}}}$) & {res['power_3d_W']:.2f} & W & $C_p \\cdot P_{{\\text{{wind,3D}}}}$ (geometric scaling) \\\\",
            f"    Estimated 3D torque ($Q_{{\\text{{3D}}}}$) & {res['torque_3d_Nm']:.4f} & N$\\cdot$m & $P_{{\\text{{3D}}}} / \\omega$ \\\\",
        ]

    lines += [
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
        r"",
    ]

    # Optional Multi-Stage Progression Table
    phases_list = res.get("phases", [])
    if phases_list and len(phases_list) > 1:
        lines += [
            r"% --- Simulation Multi-Phase Progression Table ---",
            r"\begin{table}[htbp]",
            r"  \centering",
            r"  \small",
            r"  \caption{Multi-stage simulation temporal discretization and performance progression.}",
            r"  \label{tab:vawt_stages}",
            r"  \begin{tabular}{ccccc}",
            r"    \toprule",
            r"    \textbf{Stage} & \textbf{$\Delta t$ (s)} & \textbf{$\Delta \theta$ ($^\circ$/step)} & \textbf{Revolutions} & \textbf{Steps} \\",
            r"    \midrule",
        ]
        for ph in phases_list:
            lines.append(
                f"    Stage {ph.get('phase_index', 1)} & {ph.get('dt', 0.0):.4e} & {ph.get('deg_per_step', 0.0):.2f}$^\\circ$ & {ph.get('revolutions', 0.0):.1f} revs & {ph.get('n_steps', 0):,} \\\\"
            )
        lines += [
            r"    \bottomrule",
            r"  \end{tabular}",
            r"\end{table}",
            r"",
        ]

    lines += [

        r"% --- 3. Academic Discussion Paragraph Pre-Filled with Numbers ---",
        f"The 2D computational fluid dynamics simulation was resolved for a total of {res['total_revs']:.2f} rotor revolutions "
        f"({res['n_full']} complete cycles, {res['n_steps']:,} time steps). "
        f"Aerodynamic convergence evaluation demonstrates that the last {len(sel)} revolutions (revolutions "
        f"{', '.join(str(r['k']) for r in sel)}) yielded a mean rotor power coefficient of $C_p = {res['cp_mean']:.4f}$ "
        f"and moment coefficient of $C_m = {res['cm_mean']:.4f}$. "
        f"The consecutive revolution-to-revolution variation settled to {res['cp_last_change'] * 100.0:+.2f}\\%, "
        f"classifying the rotor aerodynamic state as \\textbf{{{ 'stable and converged' if res['is_stable'] else 'unconverged / transient' }}} "
        f"under the configured tolerance threshold of $\\pm {p.stable_tol:g}\\%$.",
        r"",
        r"% --- 4. Publication Figures Blocks ---",
        r"\begin{figure}[htbp]",
        r"  \centering",
        r"  \includegraphics[width=0.88\textwidth]{03_cp_vs_azimuth.png}",
        f"  \\caption{{Phase-averaged rotor power coefficient $C_p$ versus blade azimuth $\\theta$ for revolutions {', '.join(str(r['k']) for r in sel)} ($U={p.U:g}$~m/s, $\\lambda={res['tsr']:.2f}$).}}",
        r"  \label{fig:vawt_cp_vs_azimuth}",
        r"\end{figure}",
        r"",
        r"\begin{figure}[htbp]",
        r"  \centering",
        r"  \includegraphics[width=0.88\textwidth]{01_cp_history.png}",
        f"  \\caption{{Time-history of instantaneous and revolution-averaged power coefficient over {res['total_revs']:.1f} rotor revolutions.}}",
        r"  \label{fig:vawt_cp_history}",
        r"\end{figure}",
        r"",
        r"\begin{figure}[htbp]",
        r"  \centering",
        r"  \includegraphics[width=0.88\textwidth]{08_alpha_w_re.png}",
        r"  \caption{Kinematic variation of geometric angle of attack $\alpha$, relative velocity ratio $W/U_\infty$, and chord Reynolds number $Re_c$ as a function of azimuthal angle $\theta$.}",
        r"  \label{fig:vawt_kinematics}",
        r"\end{figure}",
        r"",
    ]

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    log(f"Exported ready-to-compile LaTeX thesis snippet: {filepath}")
    return filepath
