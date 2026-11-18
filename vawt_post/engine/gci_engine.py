# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Automated Grid Convergence Index (GCI) Engine (v3.1)
=============================================================================
Implements ASME V&V 20-2009 / Celik et al. (2008) standardized procedure for
estimation of spatial and temporal discretization uncertainty in CFD simulations.
Includes strict verification guardrails, comparability checks, asymptotic ratio
validation, and combined spatial-temporal uncertainty evaluation.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

import numpy as np


@dataclass
class GridLevelData:
    """Data for a single grid or temporal resolution level."""
    name: str                           # e.g. "Fine (FFF)", "Medium (FFF-2)", "Coarse (FFF-1)"
    system_id: str                      # e.g. "FFF"
    cell_count: int                     # N cells (or steps for temporal)
    phi: float                          # Evaluated target quantity (e.g. mean Cp, mean Cm)
    node_count: Optional[int] = None
    metric_label: str = "Mean Cp"
    dt_s: Optional[float] = None        # Time step size (s)
    dtheta_deg: Optional[float] = None  # Azimuthal step size (deg/step)
    revolutions_completed: Optional[float] = None
    window_revs: Optional[float] = None # Revolution window evaluated
    turb_model: Optional[str] = None    # Viscous model name
    velocity: Optional[float] = None    # Inflow wind speed (m/s)


@dataclass
class GCIReport:
    """Complete ASME V&V 20 GCI calculation report with guardrails."""
    fine: GridLevelData
    medium: GridLevelData
    coarse: GridLevelData
    metric_name: str
    dimension: int                      # 2 for 2D, 3 for 3D

    # Grid Refinement Ratios
    r21: float                          # h2 / h1 = (N1 / N2)^(1/D)
    r32: float                          # h3 / h2 = (N2 / N3)^(1/D)

    # Grid Differences
    eps21: float                        # phi2 - phi1
    eps32: float                        # phi3 - phi2

    # Apparent Order of Convergence
    apparent_order_p: float
    is_monotonic_convergence: bool
    is_oscillatory_convergence: bool
    is_divergent: bool

    # Richardson Extrapolation (None if oscillatory or divergent)
    phi_ext_21: Optional[float]         # Extrapolated continuum solution

    # Error estimates
    e_a_21: float                       # Approximate relative error |(phi1 - phi2)/phi1| (or heuristic for oscillatory)
    e_ext_21: Optional[float]           # Extrapolated relative error |(phi_ext - phi1)/phi_ext|

    # GCI Uncertainties (Fs = 1.25)
    gci_fine_21_pct: float              # GCI for fine grid in %
    gci_medium_32_pct: float            # GCI for medium grid in %

    # Asymptotic Range Check
    asymptotic_range: float             # GCI32 / (r21^p * GCI21) ~ 1.0
    in_asymptotic_range: bool

    # Guardrails & Verification Status
    is_valid_gci: bool = True
    failed_checks: List[str] = field(default_factory=list)
    override_checks: bool = False

    # Temporal & Combined Uncertainty
    is_temporal_gci: bool = False
    u_temporal_pct: Optional[float] = None
    u_combined_pct: Optional[float] = None

    # Notes & Methodological Explanations
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "fine_cells": self.fine.cell_count,
            "medium_cells": self.medium.cell_count,
            "coarse_cells": self.coarse.cell_count,
            "fine_phi": self.fine.phi,
            "medium_phi": self.medium.phi,
            "coarse_phi": self.coarse.phi,
            "r21": self.r21,
            "r32": self.r32,
            "apparent_order_p": self.apparent_order_p,
            "phi_ext_21": self.phi_ext_21,
            "e_a_21_pct": self.e_a_21 * 100.0,
            "e_ext_21_pct": (self.e_ext_21 * 100.0) if self.e_ext_21 is not None else None,
            "gci_fine_21_pct": self.gci_fine_21_pct,
            "gci_medium_32_pct": self.gci_medium_32_pct,
            "asymptotic_range": self.asymptotic_range,
            "in_asymptotic_range": self.in_asymptotic_range,
            "is_valid_gci": self.is_valid_gci,
            "failed_checks": self.failed_checks,
            "override_checks": self.override_checks,
            "is_temporal_gci": self.is_temporal_gci,
            "u_temporal_pct": self.u_temporal_pct,
            "u_combined_pct": self.u_combined_pct,
            "notes": self.notes,
            "convergence_type": (
                "Monotonic Convergence" if self.is_monotonic_convergence else
                "Oscillatory Convergence" if self.is_oscillatory_convergence else
                "Monotonic Divergence"
            )
        }

    def _get_guardrail_banner(self) -> List[str]:
        """Construct guardrail warning / banner lines if verification checks failed."""
        banner: List[str] = []
        if not self.is_valid_gci:
            banner.extend([
                "****************************************************************************************",
                "  [!] NOT A VALID GCI COMPARISON - DISCRETIZATION VERIFICATION CRITERIA NOT MET",
                "****************************************************************************************",
                "  Failed Verification Guardrail(s):"
            ])
            for chk in self.failed_checks:
                banner.append(f"    - {chk}")
            if self.override_checks:
                banner.extend([
                    "  [OVERRIDDEN - User explicitly accepted comparison caveats for preliminary review]",
                ])
            else:
                banner.extend([
                    "  CAUTION: Results below are mathematically extrapolated but scientifically invalid.",
                    "  Ensure identical time-step dt, matching revolution counts, and r >= 1.3 across grids."
                ])
            banner.append("****************************************************************************************")
        return banner

    def to_formatted_table(self) -> str:
        """Format as clean, aligned ASCII report for GUI text displays and terminal output."""
        status_tag = "[OK - Asymptotic Range Satisfied]" if self.in_asymptotic_range else "[Deviated - Caution]"
        lines: List[str] = []

        banner = self._get_guardrail_banner()
        if banner:
            lines.extend(banner)
            lines.append("")

        phi_ext_str = f"{self.phi_ext_21:.6f}" if self.phi_ext_21 is not None else ("Heuristic: No Extrap." if self.is_oscillatory_convergence else "Divergent: No Extrap.")
        e_a_label = "0.5*|(phi3-phi1)|/phi1 (Heuristic)" if self.is_oscillatory_convergence else "|(phi1-phi2)/phi1|"
        e_ext_str = f"{self.e_ext_21*100:.3f}%" if self.e_ext_21 is not None else "N/A"

        lines.extend([
            "=" * 84,
            "ASME V&V 20 GRID CONVERGENCE INDEX (GCI) & DISCRETIZATION UNCERTAINTY REPORT",
            "=" * 84,
            f"Target Quantity (phi): {self.metric_name}",
            f"Analysis Mode:         {'Temporal GCI (Time-Step Convergence)' if self.is_temporal_gci else 'Spatial GCI (Grid Convergence)'}",
            f"Convergence Mode:      {'Monotonic Convergence' if self.is_monotonic_convergence else ('Oscillatory Convergence (Heuristic Error)' if self.is_oscillatory_convergence else 'Monotonic Divergence')}",
            "-" * 84,
            f"{'PARAMETER':<30} {'NOTATION / FORMULA':<24} {'VALUE':<16} {'UNITS':<10}",
            "-" * 84,
            f"{'Fine Level Cells / Steps':<30} {'N1':<24} {f'{self.fine.cell_count:,}':<16} {'cells':<10}",
            f"{'Medium Level Cells / Steps':<30} {'N2':<24} {f'{self.medium.cell_count:,}':<16} {'cells':<10}",
            f"{'Coarse Level Cells / Steps':<30} {'N3':<24} {f'{self.coarse.cell_count:,}':<16} {'cells':<10}",
            f"{'Fine Grid Solution':<30} {'phi_1':<24} {f'{self.fine.phi:.6f}':<16} {'-':<10}",
            f"{'Medium Grid Solution':<30} {'phi_2':<24} {f'{self.medium.phi:.6f}':<16} {'-':<10}",
            f"{'Coarse Grid Solution':<30} {'phi_3':<24} {f'{self.coarse.phi:.6f}':<16} {'-':<10}",
            f"{'Refinement Ratio (r21)':<30} {'(N1/N2)^(1/D)':<24} {f'{self.r21:.4f}':<16} {'-':<10}",
            f"{'Refinement Ratio (r32)':<30} {'(N2/N3)^(1/D)':<24} {f'{self.r32:.4f}':<16} {'-':<10}",
            f"{'Apparent Order of Conv. (p)':<30} {'p':<24} {f'{self.apparent_order_p:.3f}':<16} {'-':<10}",
            f"{'Richardson Extrapolated':<30} {'phi_ext (h -> 0)':<24} {phi_ext_str:<16} {'-':<10}",
            f"{'Approximate Relative Error':<30} {e_a_label:<24} {f'{self.e_a_21*100:.3f}%':<16} {'%':<10}",
            f"{'Extrapolated Relative Error':<30} {'e_ext,21 = |(ext-phi1)/ext|':<24} {e_ext_str:<16} {'%':<10}",
            f"{'Fine Convergence Index':<30} {'GCI_fine,21 (Fs=1.25)':<24} {f'{self.gci_fine_21_pct:.3f}%':<16} {'%':<10}",
            f"{'Medium Convergence Index':<30} {'GCI_med,32 (Fs=1.25)':<24} {f'{self.gci_medium_32_pct:.3f}%':<16} {'%':<10}",
            f"{'Asymptotic Range Check (AR)':<30} {'GCI_32 / (r21^p*GCI_21)':<24} {f'{self.asymptotic_range:.4f}':<16} {('[OK]' if self.in_asymptotic_range else '[Deviated]'):<10}",
        ])

        if self.u_combined_pct is not None:
            lines.extend([
                "-" * 84,
                f"{'Spatial Uncertainty (U_h)':<30} {'GCI_spatial':<24} {f'+/-{self.gci_fine_21_pct:.2f}%':<16} {'%':<10}",
                f"{'Temporal Uncertainty (U_dt)':<30} {'GCI_temporal':<24} {f'+/-{self.u_temporal_pct:.2f}%':<16} {'%':<10}",
                f"{'Combined Uncertainty':<30} {'sqrt(U_h^2 + U_dt^2)':<24} {f'+/-{self.u_combined_pct:.2f}%':<16} {'%':<10}",
                "Note: Combined uncertainty assumes spatial and temporal discretization errors are approximately independent (ASME V&V 20-2009).",
            ])

        lines.extend([
            "-" * 84,
            "VERIFICATION SANITY SUMMARY:",
            f"* Apparent Order of Convergence p = {self.apparent_order_p:.3f} (Formal theoretical spatial order: 2.00).",
            f"* Discretization Uncertainty in Fine Solution: +/-{self.gci_fine_21_pct:.2f}%.",
            f"* Asymptotic Range Ratio: {self.asymptotic_range:.4f} {status_tag}",
            "  " + (
                "The 3 solutions are within the asymptotic range of convergence (0.90 <= AR <= 1.10)."
                if self.in_asymptotic_range else
                "Ratio deviates from 1.0 (recommended: 0.90 - 1.10). Further refinement or smaller time step required."
            ),
            "=" * 84,
        ])
        return "\n".join(lines)

    def to_markdown_table(self) -> str:
        """Format as clean, academic Markdown table for thesis and reports."""
        rows: List[str] = []
        if not self.is_valid_gci:
            rows.extend([
                "> [!WARNING]",
                "> **NOT A VALID GCI COMPARISON**: Discretization verification criteria failed:",
            ])
            for chk in self.failed_checks:
                rows.append(f"> - {chk}")
            if self.override_checks:
                rows.append("> *(Warning overridden by user for preliminary review)*")
            rows.append("")

        phi_ext_str = f"{self.phi_ext_21:.6f}" if self.phi_ext_21 is not None else ("N/A (Oscillatory Heuristic)" if self.is_oscillatory_convergence else "N/A (Divergent)")
        e_ext_str = f"{self.e_ext_21*100:.3f}%" if self.e_ext_21 is not None else "N/A"
        e_a_formula = "0.5*|(phi3-phi1)|/phi1 (Heuristic)" if self.is_oscillatory_convergence else "|(phi1 - phi2)/phi1|"

        rows.extend([
            "| Parameter | Formula / Notation | Value | Units |",
            "| :--- | :--- | :--- | :--- |",
            f"| Target Quantity | phi | {self.metric_name} | - |",
            f"| Fine Level Cells / Steps (N1) | N1 | {self.fine.cell_count:,} | cells |",
            f"| Medium Level Cells / Steps (N2) | N2 | {self.medium.cell_count:,} | cells |",
            f"| Coarse Level Cells / Steps (N3) | N3 | {self.coarse.cell_count:,} | cells |",
            f"| Fine Solution (phi1) | phi1 | {self.fine.phi:.6f} | - |",
            f"| Medium Solution (phi2) | phi2 | {self.medium.phi:.6f} | - |",
            f"| Coarse Solution (phi3) | phi3 | {self.coarse.phi:.6f} | - |",
            f"| Grid Refinement Ratio (r21) | (N1/N2)^(1/D) | {self.r21:.4f} | - |",
            f"| Grid Refinement Ratio (r32) | (N2/N3)^(1/D) | {self.r32:.4f} | - |",
            f"| Apparent Order (p) | p | {self.apparent_order_p:.3f} | - |",
            f"| Richardson Extrapolated (phi_ext) | phi_ext | {phi_ext_str} | - |",
            f"| Approximate Relative Error (e_a,21) | {e_a_formula} | {self.e_a_21*100:.3f}% | % |",
            f"| Extrapolated Relative Error (e_ext,21) | |(phi_ext - phi1)/phi_ext| | {e_ext_str} | % |",
            f"| Fine Convergence Index | GCI_21 | {self.gci_fine_21_pct:.3f}% | % |",
            f"| Medium Convergence Index | GCI_32 | {self.gci_medium_32_pct:.3f}% | % |",
            f"| Asymptotic Range Check (AR) | GCI_32 / (r21^p * GCI_21) | {self.asymptotic_range:.4f} | {'[OK]' if self.in_asymptotic_range else '[Deviated]'} |",
        ])
        if self.u_combined_pct is not None:
            rows.extend([
                f"| Spatial Uncertainty (U_h) | GCI_spatial | +/-{self.gci_fine_21_pct:.2f}% | % |",
                f"| Temporal Uncertainty (U_dt) | GCI_temporal | +/-{self.u_temporal_pct:.2f}% | % |",
                f"| Combined Discretization Uncertainty | sqrt(U_h^2 + U_dt^2) | +/-{self.u_combined_pct:.2f}% | % |",
            ])
        return "\n".join(rows)

    def to_latex_table(self) -> str:
        """Format as ready-to-compile LaTeX table for thesis."""
        latex_lines = [
            r"\begin{table}[htbp]",
            r"  \centering",
            r"  \caption{ASME V\&V 20 Grid Convergence Index (GCI) and Discretization Uncertainty.}",
            r"  \label{tab:gci_study}",
        ]
        if not self.is_valid_gci:
            latex_lines.append(r"  % WARNING: Discretization verification criteria not met: " + ", ".join(self.failed_checks))
            if self.override_checks:
                latex_lines.append(r"  % (Overridden by user)")

        phi_ext_str = f"{self.phi_ext_21:.6f}" if self.phi_ext_21 is not None else r"\text{N/A}"
        e_ext_str = f"{self.e_ext_21*100:.3f}\\%" if self.e_ext_21 is not None else r"\text{N/A}"

        latex_lines.extend([
            r"  \begin{tabular}{llcc}",
            r"    \toprule",
            r"    Parameter & Notation / Formula & Value & Units \\",
            r"    \midrule",
            f"    Target Quantity & $\\phi$ & {self.metric_name} & -- \\\\",
            f"    Fine Level Cells & $N_1$ & {self.fine.cell_count:,} & cells \\\\",
            f"    Medium Level Cells & $N_2$ & {self.medium.cell_count:,} & cells \\\\",
            f"    Coarse Level Cells & $N_3$ & {self.coarse.cell_count:,} & cells \\\\",
            f"    Fine Solution & $\\phi_1$ & {self.fine.phi:.6f} & -- \\\\",
            f"    Medium Solution & $\\phi_2$ & {self.medium.phi:.6f} & -- \\\\",
            f"    Coarse Solution & $\\phi_3$ & {self.coarse.phi:.6f} & -- \\\\",
            f"    Refinement Ratio & $r_{{21}} = (N_1/N_2)^{{1/D}}$ & {self.r21:.4f} & -- \\\\",
            f"    Refinement Ratio & $r_{{32}} = (N_2/N_3)^{{1/D}}$ & {self.r32:.4f} & -- \\\\",
            f"    Apparent Order & $p$ & {self.apparent_order_p:.3f} & -- \\\\",
            f"    Richardson Extrapolation & $\\phi_{{\\text{{ext}}}}^{{21}}$ & {phi_ext_str} & -- \\\\",
            f"    Approximate Relative Error & $e_a^{{21}}$ & {self.e_a_21*100:.3f}\\% & \\% \\\\",
            f"    Extrapolated Relative Error & $e_{{\\text{{ext}}}}^{{21}}$ & {e_ext_str} & \\% \\\\",
            f"    Fine Convergence Index & $\\text{{GCI}}_{{21}}$ & {self.gci_fine_21_pct:.3f}\\% & \\% \\\\",
            f"    Medium Convergence Index & $\\text{{GCI}}_{{32}}$ & {self.gci_medium_32_pct:.3f}\\% & \\% \\\\",
            f"    Asymptotic Range Check & $\\text{{GCI}}_{{32}} / (r_{{21}}^p \\text{{GCI}}_{{21}})$ & {self.asymptotic_range:.4f} & {('[OK]' if self.in_asymptotic_range else '[Deviated]')} \\\\",
        ])

        if self.u_combined_pct is not None:
            latex_lines.extend([
                r"    \midrule",
                f"    Spatial Uncertainty ($U_h$) & $\\text{{GCI}}_{{h}}$ & $\\pm {self.gci_fine_21_pct:.2f}\\%$ & \\% \\\\",
                f"    Temporal Uncertainty ($U_{{\\Delta t}}$) & $\\text{{GCI}}_{{\\Delta t}}$ & $\\pm {self.u_temporal_pct:.2f}\\%$ & \\% \\\\",
                f"    Combined Uncertainty & $\\sqrt{{U_h^2 + U_{{\\Delta t}}^2}}$ & $\\pm {self.u_combined_pct:.2f}\\%$ & \\% \\\\",
            ])

        latex_lines.extend([
            r"    \bottomrule",
            r"  \end{tabular}",
            r"\end{table}",
        ])
        return "\n".join(latex_lines)


def validate_gci_comparability(
    grids: List[GridLevelData],
    r21: float,
    r32: float,
    asymptotic_range: float,
    is_temporal: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Apply ASME V&V 20 / Celik et al. guardrails:
      1. Check time step difference across systems <= 0.2% (spatial GCI) or mesh cell count equality (temporal GCI)
      2. Check revolutions completed match
      3. Check refinement ratios r21, r32 >= 1.10 (mandatory), >= 1.30 (recommended)
      4. Check turbulence model and operating parameters match
      5. Check asymptotic ratio AR within [0.90, 1.10]
    """
    failed_checks: List[str] = []

    # 1. Compare time step (spatial GCI) or mesh cell count (temporal GCI) across systems
    if not is_temporal:
        dt_vals = [g.dt_s for g in grids if g.dt_s is not None and g.dt_s > 0]
        if len(dt_vals) == 3:
            dt_max = max(dt_vals)
            dt_min = min(dt_vals)
            dt_mean = sum(dt_vals) / 3.0
            rel_dt_diff = (dt_max - dt_min) / dt_mean
            if rel_dt_diff > 0.002:
                failed_checks.append(
                    f"Time-step mismatch: dt varies by {rel_dt_diff*100:.2f}% (> 0.2%) across grids "
                    f"({dt_min:.6e}s to {dt_max:.6e}s). Runs must share identical dt for spatial GCI."
                )
    else:
        dt_vals = [g.dt_s for g in grids if g.dt_s is not None and g.dt_s > 0]
        cell_vals = [g.cell_count for g in grids if g.cell_count is not None and g.cell_count > 0]
        if len(dt_vals) == 3 and len(cell_vals) == 3 and len(set(cell_vals)) > 1:
            failed_checks.append(
                f"Mesh cell count mismatch: temporal GCI runs use different meshes "
                f"({min(cell_vals):,} to {max(cell_vals):,} cells). Runs must share identical mesh for temporal GCI."
            )

    # 2. Compare revolutions completed
    rev_vals = [g.revolutions_completed for g in grids if g.revolutions_completed is not None and g.revolutions_completed > 0]
    if len(rev_vals) == 3:
        if max(rev_vals) - min(rev_vals) > 0.5:
            failed_checks.append(
                f"Revolution count mismatch: runs completed different numbers of cycles "
                f"({min(rev_vals):.1f} to {max(rev_vals):.1f} revs). Must evaluate equivalent development."
            )

    # 3. Refinement ratio check (r >= 1.10 mandatory, r >= 1.30 recommended)
    if r21 < 1.10:
        failed_checks.append(f"Fine-Medium refinement ratio r21 = {r21:.3f} is below mandatory minimum 1.10.")
    elif r21 < 1.30:
        failed_checks.append(f"Fine-Medium refinement ratio r21 = {r21:.3f} is below recommended minimum 1.30 (ASME V&V 20).")
    if r32 < 1.10:
        failed_checks.append(f"Medium-Coarse refinement ratio r32 = {r32:.3f} is below mandatory minimum 1.10.")
    elif r32 < 1.30:
        failed_checks.append(f"Medium-Coarse refinement ratio r32 = {r32:.3f} is below recommended minimum 1.30 (ASME V&V 20).")

    # 4. Turbulence model consistency check
    turb_models = {g.turb_model for g in grids if g.turb_model}
    if len(turb_models) > 1:
        failed_checks.append(f"Turbulence model mismatch: systems use different models ({', '.join(turb_models)}). Spatial GCI requires identical physics.")

    # 5. Inflow velocity consistency check
    vels = [float(g.velocity) for g in grids if g.velocity is not None]
    if len(vels) == 3:
        v_mean = sum(vels) / 3.0
        if v_mean > 1e-12 and (max(vels) - min(vels)) / v_mean > 0.005:
            failed_checks.append("Inflow velocity mismatch across systems. Spatial GCI requires identical operating parameters.")

    # 6. Asymptotic range ratio check (0.90 <= AR <= 1.10)
    if not (0.90 <= asymptotic_range <= 1.10):
        failed_checks.append(
            f"Asymptotic range check AR = {asymptotic_range:.4f} is outside [0.90, 1.10]. "
            "Grids may not be asymptotically converged."
        )

    is_valid = (len(failed_checks) == 0)
    return is_valid, failed_checks


def compute_asme_gci(
    grids: List[GridLevelData],
    dimension: int = 2,
    safety_factor: float = 1.25,
    override_checks: bool = False,
    is_temporal: bool = False,
    u_temporal_input_pct: Optional[float] = None
) -> Optional[GCIReport]:
    """
    Perform ASME V&V 20 Grid Convergence Index analysis on 3 grid levels.
    Applies guardrails for time step, revolution duration, refinement ratio >= 1.3,
    and asymptotic range check.
    """
    if len(grids) != 3:
        return None

    # Sort from finest to coarsest:
    # - For temporal GCI with distinct positive dt_s, sort ascending by dt_s (smallest dt = finest)
    # - Otherwise sort descending by cell_count (largest N = finest)
    use_dt_refinement = (
        is_temporal
        and all(g.dt_s is not None and g.dt_s > 0 for g in grids)
    )
    if use_dt_refinement:
        sorted_grids = sorted(grids, key=lambda g: float(g.dt_s or 0.0))
        g1, g2, g3 = sorted_grids[0], sorted_grids[1], sorted_grids[2]
        dt1, dt2, dt3 = float(g1.dt_s or 0.0), float(g2.dt_s or 0.0), float(g3.dt_s or 0.0)
        if dt1 <= 0 or dt2 <= dt1 or dt3 <= dt2:
            return None
        r21 = dt2 / dt1
        r32 = dt3 / dt2
    else:
        sorted_grids = sorted(grids, key=lambda g: g.cell_count, reverse=True)
        g1, g2, g3 = sorted_grids[0], sorted_grids[1], sorted_grids[2]

        N1, N2, N3 = float(g1.cell_count), float(g2.cell_count), float(g3.cell_count)
        if dimension <= 0 or N3 <= 0 or N1 <= N2 or N2 <= N3:
            return None

        # 1. Grid refinement ratios (D = 1 for 1D temporal step counts, else spatial dimension)
        D = 1.0 if is_temporal else float(dimension)
        r21 = (N1 / N2) ** (1.0 / D)
        r32 = (N2 / N3) ** (1.0 / D)

    phi1, phi2, phi3 = float(g1.phi), float(g2.phi), float(g3.phi)

    # 2. Grid step differences
    eps21 = phi2 - phi1
    eps32 = phi3 - phi2

    # Check convergence condition according to ASME V&V 20 / Celik (2008):
    # Convergence ratio R_conv = eps21 / eps32 = (phi2 - phi1) / (phi3 - phi2)
    # Monotonic convergence: 0 <= R_conv < 1 (eps21 == 0 means fine-grid exact convergence)
    # Oscillatory convergence: R_conv < 0
    # Divergence: R_conv >= 1
    denom_eps32 = eps32 if abs(eps32) > 1e-14 else (1e-14 if eps32 >= 0 else -1e-14)
    R_conv = eps21 / denom_eps32

    is_exact_fine = abs(eps21) <= 1e-12
    is_monotonic_conv = (0.0 < R_conv < 1.0) or (is_exact_fine and abs(eps32) > 1e-12)
    is_oscillatory_conv = (not is_exact_fine) and (R_conv < 0.0)
    is_divergent = (not is_exact_fine) and (R_conv >= 1.0)

    # 3. Solve for apparent order p via fixed-point iteration (Celik et al. 2008)
    denom_eps21 = eps21 if abs(eps21) > 1e-14 else (1e-14 if eps21 >= 0 else -1e-14)
    ratio_32_21 = eps32 / denom_eps21
    s = 1.0 if ratio_32_21 >= 0 else -1.0
    abs_ratio = max(abs(ratio_32_21), 1e-9)

    log_r21 = max(math.log(max(r21, 1.0001)), 1e-6)

    # Initial guess
    p = abs(math.log(abs_ratio) / log_r21)
    if p < 0.1:
        p = 1.0
    elif p > 10.0:
        p = 2.0

    # If refinement ratios are identical, q(p) = 0 analytically
    if abs(r21 - r32) < 1e-5:
        p = abs(math.log(abs_ratio) / log_r21)
    else:
        for _ in range(120):
            try:
                num = (r21 ** p) - s
                den = (r32 ** p) - s
                if den <= 0 or num <= 0:
                    q_p = 0.0
                else:
                    q_p = math.log(num / den)
                p_next = (1.0 / log_r21) * abs(math.log(abs_ratio) + q_p)
                if math.isnan(p_next) or math.isinf(p_next):
                    break
                if abs(p_next - p) < 1e-6:
                    p = p_next
                    break
                p = 0.6 * p + 0.4 * p_next
            except (ValueError, OverflowError, ZeroDivisionError):
                break

    p = max(0.1, min(p, 8.0))

    # 4. Richardson Extrapolated value & Relative errors
    denom21 = (r21**p) - 1.0
    denom32 = (r32**p) - 1.0

    if abs(denom21) < 1e-6:
        denom21 = 1e-6
    if abs(denom32) < 1e-6:
        denom32 = 1e-6

    denom_phi1 = abs(phi1) if abs(phi1) > 1e-9 else 1.0
    denom_phi2 = abs(phi2) if abs(phi2) > 1e-9 else 1.0

    notes: List[str] = []
    if abs(eps21) <= 1e-12 and abs(eps32) <= 1e-12:
        p = 1.0
        is_monotonic_conv = False
        is_oscillatory_conv = False
        is_divergent = False
        phi_ext_21 = phi1
        e_a_21 = 0.0
        e_a_32 = 0.0
        e_ext_21 = 0.0
        gci_fine_21_pct = 0.0
        gci_medium_32_pct = 0.0
        notes.append("Grid solutions are numerically identical across all 3 levels (GCI = 0.00%).")
    elif is_exact_fine:
        is_monotonic_conv = True
        is_oscillatory_conv = False
        is_divergent = False
        phi_ext_21 = phi1
        e_a_21 = 0.0
        e_a_32 = abs((phi2 - phi3) / denom_phi2)
        e_ext_21 = 0.0
        gci_fine_21_pct = 0.0
        gci_medium_32_pct = (safety_factor * e_a_32 / abs(denom32)) * 100.0
        notes.append("Fine and medium solutions are numerically identical (GCI_21 = 0.00%).")
    elif is_monotonic_conv:
        phi_ext_21 = ((r21**p) * phi1 - phi2) / denom21
        denom_ext = abs(phi_ext_21) if abs(phi_ext_21) > 1e-9 else 1.0
        e_a_21 = abs((phi1 - phi2) / denom_phi1)
        e_a_32 = abs((phi2 - phi3) / denom_phi2)
        e_ext_21 = abs((phi_ext_21 - phi1) / denom_ext)
        gci_fine_21_pct = (safety_factor * e_a_21 / abs(denom21)) * 100.0
        gci_medium_32_pct = (safety_factor * e_a_32 / abs(denom32)) * 100.0
    elif is_oscillatory_conv:
        # Oscillatory convergence: Richardson extrapolation is mathematically invalid (L12)
        phi_ext_21 = None
        e_ext_21 = None
        # Celik / ASME oscillatory heuristic error: 0.5 * |phi3 - phi1|
        e_a_21 = 0.5 * abs(phi3 - phi1) / denom_phi1
        e_a_32 = 0.5 * abs(phi3 - phi1) / denom_phi2
        gci_fine_21_pct = (safety_factor * e_a_21) * 100.0
        gci_medium_32_pct = (safety_factor * e_a_32) * 100.0
        notes.append("Oscillatory convergence detected: Richardson extrapolation suppressed, Celik heuristic error metric applied.")
    else:  # is_divergent
        phi_ext_21 = None
        e_ext_21 = None
        e_a_21 = abs((phi1 - phi2) / denom_phi1)
        e_a_32 = abs((phi2 - phi3) / denom_phi2)
        gci_fine_21_pct = (safety_factor * e_a_21 / abs(denom21)) * 100.0
        gci_medium_32_pct = (safety_factor * e_a_32 / abs(denom32)) * 100.0
        notes.append("Monotonic divergence detected: Richardson extrapolation suppressed.")

    # 7. Asymptotic Range Check: GCI32 / (r21^p * GCI21) ~ 1.0
    denom_ar = (r21**p) * (gci_fine_21_pct / 100.0)
    if abs(denom_ar) > 1e-12:
        asymptotic_range = (gci_medium_32_pct / 100.0) / denom_ar
    else:
        asymptotic_range = 1.0

    in_asymptotic_range = (0.90 <= asymptotic_range <= 1.10)

    # 8. Comparability Guardrails
    is_valid, failed_checks = validate_gci_comparability(
        grids=sorted_grids,
        r21=r21,
        r32=r32,
        asymptotic_range=asymptotic_range,
        is_temporal=is_temporal,
    )

    # Combined spatial-temporal uncertainty calculation
    u_comb = None
    if u_temporal_input_pct is not None and u_temporal_input_pct > 0:
        u_comb = math.sqrt((gci_fine_21_pct ** 2) + (u_temporal_input_pct ** 2))

    return GCIReport(
        fine=g1,
        medium=g2,
        coarse=g3,
        metric_name=g1.metric_label,
        dimension=dimension,
        r21=r21,
        r32=r32,
        eps21=eps21,
        eps32=eps32,
        apparent_order_p=p,
        is_monotonic_convergence=is_monotonic_conv,
        is_oscillatory_convergence=is_oscillatory_conv,
        is_divergent=is_divergent,
        phi_ext_21=phi_ext_21,
        e_a_21=e_a_21,
        e_ext_21=e_ext_21,
        gci_fine_21_pct=gci_fine_21_pct,
        gci_medium_32_pct=gci_medium_32_pct,
        asymptotic_range=asymptotic_range,
        in_asymptotic_range=in_asymptotic_range,
        is_valid_gci=is_valid,
        failed_checks=failed_checks,
        override_checks=override_checks,
        is_temporal_gci=is_temporal,
        u_temporal_pct=u_temporal_input_pct,
        u_combined_pct=u_comb,
        notes=notes
    )


def plot_gci_curve(report: GCIReport, out_path: Optional[str] = None, dark_mode: bool = False) -> Any:
    """
    Generate an academic publication plot showing the Richardson Extrapolation
    and explicitly labeled GCI uncertainty error bands (GCI21, GCI32).
    """
    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.8, 5.0), dpi=150)
    ax = fig.add_subplot(111)

    if dark_mode:
        fig.patch.set_facecolor("#1E293B")
        ax.set_facecolor("#0F172A")
        ax.tick_params(colors="#E2E8F0")
        ax.xaxis.label.set_color("#F8FAFC")
        ax.yaxis.label.set_color("#F8FAFC")
        ax.title.set_color("#F8FAFC")
        for spine in ax.spines.values():
            spine.set_color("#475569")
        ax.grid(True, linestyle=":", alpha=0.35, color="#94A3B8")
    else:
        ax.grid(True, linestyle=":", alpha=0.6)

    h1 = 1.0
    h2 = float(report.r21)
    h3 = float(report.r21 * report.r32)

    err_points = [
        abs(report.fine.phi) * (report.gci_fine_21_pct / 100.0),
        abs(report.medium.phi) * (report.gci_medium_32_pct / 100.0),
    ]

    # Richardson extrapolation curve
    if report.phi_ext_21 is not None and report.apparent_order_p is not None:
        h_dense = np.linspace(0.0, h3 * 1.08, 100)
        alpha = (report.fine.phi - report.phi_ext_21) / (h1 ** report.apparent_order_p) if h1 > 0 else 0.0
        phi_dense = report.phi_ext_21 + alpha * (h_dense ** report.apparent_order_p)

        curve_color = "#38BDF8" if dark_mode else "#2563EB"
        ax.plot(
            h_dense, phi_dense,
            color=curve_color, linestyle="--", linewidth=1.8,
            label=f"Richardson Fit ($p={report.apparent_order_p:.2f}$)"
        )

        # Extrapolated continuum limit at h = 0
        ax.scatter(
            [0.0], [report.phi_ext_21],
            color="#F87171" if dark_mode else "#DC2626", marker="*", s=160, zorder=5,
            label=f"Extrapolated $\\phi_{{ext}} = {report.phi_ext_21:.4f}$"
        )
    else:
        ax.text(
            0.5, 0.95,
            "Richardson Extrapolation N/A (Non-monotonic)",
            transform=ax.transAxes, ha="center", va="top",
            fontsize=10, color="#F59E0B", fontstyle="italic"
        )

    # Plot simulated points with unambiguous pair error bar labels
    fine_color = "#60A5FA" if dark_mode else "#1D4ED8"
    fine_err = "#93C5FD" if dark_mode else "#60A5FA"
    med_color = "#FBBF24" if dark_mode else "#D97706"
    med_err = "#FDE68A" if dark_mode else "#FBBF24"
    coarse_color = "#CBD5E1" if dark_mode else "#4B5563"

    ax.errorbar(
        [h1], [report.fine.phi], yerr=[err_points[0]],
        fmt="o", color=fine_color, ecolor=fine_err, elinewidth=2.0, capsize=5, capthick=1.5,
        markersize=8, zorder=6,
        label=f"Fine: $N_1={report.fine.cell_count:,}$ [$\\mathrm{{GCI}}_{{21}} = \\pm{report.gci_fine_21_pct:.2f}\\%$]"
    )
    ax.errorbar(
        [h2], [report.medium.phi], yerr=[err_points[1]],
        fmt="s", color=med_color, ecolor=med_err, elinewidth=2.0, capsize=5, capthick=1.5,
        markersize=7, zorder=6,
        label=f"Medium: $N_2={report.medium.cell_count:,}$ [$\\mathrm{{GCI}}_{{32}} = \\pm{report.gci_medium_32_pct:.2f}\\%$]"
    )
    ax.scatter(
        [h3], [report.coarse.phi],
        color=coarse_color, marker="^", s=70, zorder=6,
        label=f"Coarse: $N_3={report.coarse.cell_count:,}$"
    )

    # Add warning banner text on plot if invalid
    if not report.is_valid_gci and not report.override_checks:
        ax.text(
            0.5, 0.05, "NOT A VALID GCI (Caveats Detected)",
            transform=ax.transAxes, color="#EF4444", fontsize=11, fontweight="bold",
            ha="center", bbox=dict(boxstyle="round,pad=0.3", fc="#FEF2F2", ec="#EF4444", lw=1.5)
        )

    ax.set_xlabel("Normalized Spacing $h / h_{\\mathrm{fine}}$", fontsize=11, fontweight="bold")
    ax.set_ylabel(f"{report.metric_name}", fontsize=11, fontweight="bold")
    ax.set_title(
        f"ASME V&V 20 Discretization Study: {report.metric_name}\n"
        f"$\\mathrm{{GCI}}_{{21}} = {report.gci_fine_21_pct:.2f}\\%$, Order $p = {report.apparent_order_p:.2f}$",
        fontsize=12, fontweight="bold", pad=12
    )

    if dark_mode:
        leg = ax.legend(loc="best", framealpha=0.85, facecolor="#1E293B", edgecolor="#475569", fontsize=9.0)
        for text in leg.get_texts():
            text.set_color("#F8FAFC")
    else:
        ax.legend(loc="best", framealpha=0.9, fontsize=9.0)

    if out_path:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
        fig.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        fig.savefig(out_path, dpi=300, bbox_inches="tight")
        fig.clear()
        return out_path

    fig.tight_layout()
    return fig


@dataclass
class MultiGridStudyReport:
    """Complete Multi-Mesh Grid Dependency Report supporting any number of mesh levels (N >= 2)."""
    grids: List[GridLevelData]                     # Sorted finest to coarsest
    dimension: int
    triplet_reports: List[GCIReport]               # Consecutive ASME V&V 20 trios
    refinement_ratios: List[float]                 # r_{i, i+1} = (N_i / N_{i+1})^(1/D)
    phi_extrapolated: Optional[float]              # Continuum limit from finest trio
    finest_gci_pct: Optional[float]                # GCI of the finest grid level
    is_converged: bool = True

    def _grid_gci_value(self, i: int) -> Optional[float]:
        """Return GCI (%) for grid index i using consecutive trio reports (coarsest grid returns None)."""
        if not self.triplet_reports:
            return None
        if i == 0:
            return self.triplet_reports[0].gci_fine_21_pct
        if 1 <= i <= len(self.triplet_reports):
            return self.triplet_reports[i - 1].gci_medium_32_pct
        return None

    def to_formatted_table(self) -> str:
        """Render publication-grade ASCII table across all mesh levels."""
        lines = [
            "==========================================================================================",
            "                   ASME V&V 20 MULTI-GRID CONVERGENCE STUDY (ALL MESHES)                  ",
            "==========================================================================================",
            f"Evaluated Grids: {len(self.grids)} levels  |  Spatial Dimension: {self.dimension}D",
            "------------------------------------------------------------------------------------------",
            f"{'Grid ID':<10} {'Mesh Cells (N)':<16} {'Norm. Spacing h':<18} {'Target φ':<14} {'Refinement r':<14} {'GCI (%)':<10}",
            "------------------------------------------------------------------------------------------",
        ]
        N_fine = float(self.grids[0].cell_count) if self.grids else 1.0
        for i, g in enumerate(self.grids):
            h_norm = (N_fine / float(g.cell_count)) ** (1.0 / self.dimension) if g.cell_count > 0 else 1.0
            r_str = f"{self.refinement_ratios[i]:.4f}" if i < len(self.refinement_ratios) else "Base / Coarsest"

            gci_val = self._grid_gci_value(i)
            gci_str = f"±{gci_val:.2f}%" if gci_val is not None else "-"

            lines.append(
                f"{g.system_id:<10} {g.cell_count:<16,d} {h_norm:<18.4f} {g.phi:<14.6f} {r_str:<14} {gci_str:<10}"
            )
        lines.append("------------------------------------------------------------------------------------------")
        if self.phi_extrapolated is not None:
            lines.append(f"Richardson Continuum Extrapolated φ_ext: {self.phi_extrapolated:.6f}")
        if self.finest_gci_pct is not None:
            lines.append(f"Finest Grid Discretization Uncertainty GCI: ±{self.finest_gci_pct:.3f}%")
        if self.triplet_reports:
            p_vals = [f"Trio {idx+1}: p={t.apparent_order_p:.2f}" for idx, t in enumerate(self.triplet_reports)]
            lines.append(f"Apparent Order(s) of Convergence: {', '.join(p_vals)}")
        lines.append("==========================================================================================")
        return "\n".join(lines)

    def to_markdown_table(self) -> str:
        """Render publication-grade Markdown table across all mesh levels."""
        lines = [
            "| Level | System ID | Cells (N) | Spacing h | Solution φ | Refinement r | GCI (%) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        N_fine = float(self.grids[0].cell_count) if self.grids else 1.0
        for i, g in enumerate(self.grids):
            h_norm = (N_fine / float(g.cell_count)) ** (1.0 / self.dimension) if g.cell_count > 0 else 1.0
            r_str = f"{self.refinement_ratios[i]:.4f}" if i < len(self.refinement_ratios) else "-"
            gci_val = self._grid_gci_value(i)
            gci_str = f"±{gci_val:.2f}%" if gci_val is not None else "-"
            lines.append(f"| {i+1} | {g.system_id} | {g.cell_count:,} | {h_norm:.4f} | {g.phi:.6f} | {r_str} | {gci_str} |")
        return "\n".join(lines)

    def to_latex_table(self) -> str:
        """Render publication-grade LaTeX tabular snippet."""
        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{ASME V\&V 20 Multi-Grid Convergence Study}",
            r"\begin{tabular}{ccccccc}",
            r"\toprule",
            r"Level & System ID & Cells ($N$) & $h/h_1$ & $\phi$ & $r$ & GCI \\",
            r"\midrule",
        ]
        N_fine = float(self.grids[0].cell_count) if self.grids else 1.0
        for i, g in enumerate(self.grids):
            h_norm = (N_fine / float(g.cell_count)) ** (1.0 / self.dimension) if g.cell_count > 0 else 1.0
            r_str = f"{self.refinement_ratios[i]:.4f}" if i < len(self.refinement_ratios) else "--"
            gci_val = self._grid_gci_value(i)
            gci_str = f"$\\pm {gci_val:.2f}\\%$" if gci_val is not None else "--"
            lines.append(f"{i+1} & {g.system_id} & {g.cell_count:,} & {h_norm:.4f} & {g.phi:.6f} & {r_str} & {gci_str} \\\\")
        lines.extend([
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ])
        return "\n".join(lines)


def compute_multi_grid_study(
    grids: List[GridLevelData],
    dimension: int = 2,
    safety_factor: float = 1.25,
    override_checks: bool = False
) -> MultiGridStudyReport:
    """
    Compute generalized multi-mesh grid dependency analysis across any number of mesh levels (N >= 2).
    Sorts grids descending by cell count, calculates refinement ratios, and computes ASME V&V 20
    GCI reports across sliding consecutive trios.
    """
    if len(grids) < 2:
        raise ValueError("At least 2 grid levels are required for a grid dependency study.")

    if dimension <= 0:
        raise ValueError("Spatial dimension must be positive (2 or 3).")

    sorted_grids = sorted(grids, key=lambda g: g.cell_count, reverse=True)
    D = float(dimension)

    r_ratios: List[float] = []
    for i in range(len(sorted_grids) - 1):
        c_hi = float(sorted_grids[i].cell_count)
        c_lo = float(sorted_grids[i + 1].cell_count)
        if c_lo > 0 and c_hi > 0:
            r_i = (c_hi / c_lo) ** (1.0 / D)
        else:
            r_i = 1.0
        r_ratios.append(r_i)

    triplets: List[GCIReport] = []
    if len(sorted_grids) >= 3:
        for i in range(len(sorted_grids) - 2):
            trio_grids = [sorted_grids[i], sorted_grids[i+1], sorted_grids[i+2]]
            rep = compute_asme_gci(
                trio_grids,
                dimension=dimension,
                safety_factor=safety_factor,
                override_checks=override_checks
            )
            if rep:
                triplets.append(rep)

    fine_gci = triplets[0].gci_fine_21_pct if triplets else None
    phi_ext = triplets[0].phi_ext_21 if triplets else None

    return MultiGridStudyReport(
        grids=sorted_grids,
        dimension=dimension,
        triplet_reports=triplets,
        refinement_ratios=r_ratios,
        phi_extrapolated=phi_ext,
        finest_gci_pct=fine_gci
    )


def plot_multi_grid_curve(
    report: MultiGridStudyReport,
    dark_mode: bool = False,
    out_path: Optional[str] = None
) -> Any:
    """
    Generate clean publication curve for multi-grid dependency study across all mesh levels.
    """
    from matplotlib.figure import Figure
    import numpy as np

    fig = Figure(figsize=(7.5, 4.8), dpi=100)
    ax = fig.add_subplot(111)

    bg_col = "#0F172A" if dark_mode else "#FFFFFF"
    text_col = "#F8FAFC" if dark_mode else "#0F172A"
    grid_col = "#334155" if dark_mode else "#E2E8F0"

    fig.patch.set_facecolor(bg_col)
    ax.set_facecolor(bg_col)
    ax.grid(True, linestyle="--", alpha=0.5, color=grid_col)
    ax.tick_params(colors=text_col)
    for spine in ax.spines.values():
        spine.set_color(text_col)

    N_fine = float(report.grids[0].cell_count) if report.grids else 1.0
    h_vals = [
        (N_fine / float(g.cell_count)) ** (1.0 / report.dimension) if g.cell_count > 0 else 1.0
        for g in report.grids
    ]
    phi_vals = [g.phi for g in report.grids]

    colors = ["#3B82F6", "#10B981", "#F59E0B", "#EC4899", "#8B5CF6", "#06B6D4"]

    # Plot simulated mesh points
    for idx, (g, h_val, phi_val) in enumerate(zip(report.grids, h_vals, phi_vals)):
        c = colors[idx % len(colors)]
        err = 0.0
        if idx == 0 and report.finest_gci_pct:
            err = abs(phi_val) * (report.finest_gci_pct / 100.0)
            ax.errorbar([h_val], [phi_val], yerr=[err], fmt="o", color=c, ecolor=c,
                        capsize=5, elinewidth=1.8, markersize=8, zorder=5,
                        label=f"{g.system_id}: {g.cell_count:,} cells [±{report.finest_gci_pct:.2f}%]")
        else:
            ax.scatter([h_val], [phi_val], color=c, s=60, zorder=5,
                       label=f"{g.system_id}: {g.cell_count:,} cells (φ={phi_val:.4f})")

    # If Richardson continuum extrapolation exists, plot fit curve
    if report.phi_extrapolated is not None and report.triplet_reports:
        p = report.triplet_reports[0].apparent_order_p
        h_fit = np.linspace(0.0, max(h_vals) * 1.15, 100)
        phi_fit = report.phi_extrapolated + (phi_vals[0] - report.phi_extrapolated) * (h_fit ** p)
        ax.plot(h_fit, phi_fit, linestyle="--", color="#60A5FA" if dark_mode else "#2563EB", lw=1.6,
                label=f"Richardson Fit ($p={p:.2f}$)")
        ax.scatter([0.0], [report.phi_extrapolated], marker="*", s=160,
                   color="#F87171" if dark_mode else "#DC2626", zorder=6,
                   label=f"Extrapolated φ_ext = {report.phi_extrapolated:.4f}")

    ax.set_xlabel("Normalized Mesh Spacing $h / h_1$", fontsize=10, fontweight="bold", color=text_col)
    ax.set_ylabel("Target Quantity φ", fontsize=10, fontweight="bold", color=text_col)
    ax.set_title(f"Multi-Mesh Grid Dependency Study ({len(report.grids)} Levels)",
                 fontsize=11, fontweight="bold", color=text_col)

    if dark_mode:
        leg = ax.legend(loc="best", framealpha=0.85, facecolor="#1E293B", edgecolor="#475569", fontsize=8.5)
        for t in leg.get_texts():
            t.set_color("#F8FAFC")
    else:
        ax.legend(loc="best", framealpha=0.9, fontsize=8.5)

    if out_path:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
        fig.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        fig.savefig(out_path, dpi=300, bbox_inches="tight")
        fig.clear()
        return out_path

    fig.tight_layout()
    return fig

