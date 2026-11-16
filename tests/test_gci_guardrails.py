# -*- coding: utf-8 -*-
"""
Tests for ASME V&V 20 GCI Guardrails & Temporal Uncertainty (v3.1)
==================================================================
Verifies comparability checks (dt mismatch, revolution mismatch, r < 1.3,
asymptotic ratio), warning banners, override behaviors, and combined uncertainty.
"""
from vawt_post.engine.gci_engine import (
    GridLevelData, compute_asme_gci
)


def test_gci_valid_trio_no_banner():
    """Verify that a valid, comparable grid trio passes all guardrails without invalid banners."""
    # N1=400k, N2=200k, N3=100k -> r = sqrt(2) ~ 1.414 >= 1.3
    # Solutions monotonic converging toward 0.58
    g1 = GridLevelData("Fine", "FFF", cell_count=400000, phi=0.585, dt_s=0.0001, revolutions_completed=8.0)
    g2 = GridLevelData("Med", "FFF-1", cell_count=200000, phi=0.595, dt_s=0.0001, revolutions_completed=8.0)
    g3 = GridLevelData("Coarse", "FFF-2", cell_count=100000, phi=0.615, dt_s=0.0001, revolutions_completed=8.0)

    report = compute_asme_gci([g1, g2, g3], dimension=2)
    assert report is not None
    assert report.r21 >= 1.30
    assert report.r32 >= 1.30
    # Guardrail checks should pass
    assert report.is_valid_gci is True
    assert len(report.failed_checks) == 0

    formatted = report.to_formatted_table()
    assert "NOT A VALID GCI" not in formatted

    md = report.to_markdown_table()
    assert "NOT A VALID GCI" not in md


def test_gci_guardrail_dt_mismatch():
    """Verify that differing time steps (> 0.2%) trigger the invalid comparison banner."""
    # Differing time steps: fine at 0.0001s, others at 0.0002s (100% diff)
    g1 = GridLevelData("Fine", "FFF", cell_count=400000, phi=0.585, dt_s=0.0001, revolutions_completed=8.0)
    g2 = GridLevelData("Med", "FFF-1", cell_count=200000, phi=0.595, dt_s=0.0002, revolutions_completed=8.0)
    g3 = GridLevelData("Coarse", "FFF-2", cell_count=100000, phi=0.615, dt_s=0.0002, revolutions_completed=8.0)

    report = compute_asme_gci([g1, g2, g3], dimension=2)
    assert report is not None
    assert report.is_valid_gci is False
    assert any("time-step mismatch" in chk.lower() for chk in report.failed_checks)

    formatted = report.to_formatted_table()
    assert "NOT A VALID GCI COMPARISON" in formatted
    assert "Time-step mismatch" in formatted


def test_gci_guardrail_revolutions_mismatch():
    """Verify that differing completed revolutions trigger a guardrail failure."""
    g1 = GridLevelData("Fine", "FFF", cell_count=400000, phi=0.585, dt_s=0.0001, revolutions_completed=5.0)
    g2 = GridLevelData("Med", "FFF-1", cell_count=200000, phi=0.595, dt_s=0.0001, revolutions_completed=9.0)
    g3 = GridLevelData("Coarse", "FFF-2", cell_count=100000, phi=0.615, dt_s=0.0001, revolutions_completed=9.0)

    report = compute_asme_gci([g1, g2, g3], dimension=2)
    assert report is not None
    assert report.is_valid_gci is False
    assert any("revolution count mismatch" in chk.lower() for chk in report.failed_checks)


def test_gci_guardrail_low_refinement_ratio():
    """Verify that r < 1.3 triggers a Celik et al. guardrail warning."""
    # N1=250k, N2=200k -> r = sqrt(1.25) = 1.118 < 1.3
    g1 = GridLevelData("Fine", "FFF", cell_count=250000, phi=0.585, dt_s=0.0001, revolutions_completed=8.0)
    g2 = GridLevelData("Med", "FFF-1", cell_count=200000, phi=0.590, dt_s=0.0001, revolutions_completed=8.0)
    g3 = GridLevelData("Coarse", "FFF-2", cell_count=160000, phi=0.600, dt_s=0.0001, revolutions_completed=8.0)

    report = compute_asme_gci([g1, g2, g3], dimension=2)
    assert report is not None
    assert report.is_valid_gci is False
    assert any("below recommended minimum 1.30" in chk.lower() for chk in report.failed_checks)


def test_gci_guardrail_override_behavior():
    """Verify that override_checks=True displays the override statement."""
    g1 = GridLevelData("Fine", "FFF", cell_count=250000, phi=0.585, dt_s=0.0001, revolutions_completed=5.0)
    g2 = GridLevelData("Med", "FFF-1", cell_count=200000, phi=0.590, dt_s=0.0002, revolutions_completed=8.0)
    g3 = GridLevelData("Coarse", "FFF-2", cell_count=160000, phi=0.600, dt_s=0.0002, revolutions_completed=8.0)

    report = compute_asme_gci([g1, g2, g3], dimension=2, override_checks=True)
    assert report is not None
    assert report.override_checks is True

    formatted = report.to_formatted_table()
    assert "OVERRIDDEN" in formatted


def test_temporal_and_combined_uncertainty():
    """Verify temporal GCI evaluation and combined spatial-temporal uncertainty sqrt(Uh^2 + Udt^2)."""
    g1 = GridLevelData("Fine", "FFF", cell_count=400000, phi=0.585, dt_s=0.0001, revolutions_completed=8.0)
    g2 = GridLevelData("Med", "FFF-1", cell_count=200000, phi=0.595, dt_s=0.0001, revolutions_completed=8.0)
    g3 = GridLevelData("Coarse", "FFF-2", cell_count=100000, phi=0.615, dt_s=0.0001, revolutions_completed=8.0)

    # Supply temporal uncertainty input = 2.5%
    report = compute_asme_gci(
        [g1, g2, g3],
        dimension=2,
        u_temporal_input_pct=2.5
    )
    assert report is not None
    assert report.u_temporal_pct == 2.5
    assert report.u_combined_pct is not None

    expected_comb = (report.gci_fine_21_pct**2 + 2.5**2)**0.5
    assert abs(report.u_combined_pct - expected_comb) < 1e-6

    formatted = report.to_formatted_table()
    assert "Spatial Uncertainty (U_h)" in formatted
    assert "Temporal Uncertainty (U_dt)" in formatted
    assert "Combined Uncertainty" in formatted
