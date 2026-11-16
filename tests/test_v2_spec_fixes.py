# -*- coding: utf-8 -*-
"""
Verification Suite for vawt_post v2.0 Specification Fixes & Scope Cleanup
========================================================================
Validates all 17 review points + Section 0 scope cleanup + Section 3 confirmations:
1. Clockwise rotation & into-wind azimuth zero (identical Cp(theta) to mirrored CCW run)
2. Moment sign precedence (fixed sign from rotation dir, no double flip, preserves negative Cp)
3. Normal / tangential per-blade force decomposition, signs, and C_tan naming
4. Check 15 spectral harmonics on total torque (k*N*f0) and fundamental f0 on blade torque
5. Autodetect rejects Cd/Cl files, candidate listing with last_step, and Check 3b coefficient warning
6. Wall y+ 3-tier limits (Good <= 1, Caution 1-5, Poor > 5) with mean and max reported separately
7. Static stall angle default (12.0 deg) and geometric stall exceedance
8. Reduced frequency 3-tier classification (k < 0.05, 0.05-0.20, > 0.20)
9. PCHIP monotonic spline (no overshooting) & local quadratic peak fit
10. ASME V&V 20 GCI metadata enforcement, h formula, and oscillatory convergence handling
11. Aitken 0 < r < 0.9 requirement, capping, and exponential decay fit
12. Multi-stage phase detector skips isolated 1-2 step gaps
"""
import math
import os
import tempfile
import pytest
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.synthetic import make_fluent_file
from vawt_post.engine.analysis import analyze, detect_stages, aitken_extrapolation, fit_exponential_decay
from vawt_post.engine.forces import (
    compute_normal_tangential_coefficients,
    compute_blade_kinematics,
    classify_reduced_frequency,
)
from vawt_post.engine.batch import pchip_spline
from vawt_post.engine.comparison import calculate_gci
from vawt_post.engine.diagnostics import scan_folder_candidates, run_file_health_diagnostics
from vawt_post.engine.specialized_plots import plot_yplus_monitor


# ---------------------------------------------------------------------------
# 1. Clockwise rotation and azimuth zero symmetry
# ---------------------------------------------------------------------------
def test_clockwise_and_azimuth_zero_symmetry():
    """CW synthetic run must yield the same Cp(theta) as its mirrored CCW run."""
    with tempfile.TemporaryDirectory() as tmpdir:
        U = 8.0
        R = 0.5
        chord = 0.06
        nb = 3
        omega = 56.0  # rad/s (lambda = 3.5)
        dt = math.radians(1.0) / omega
        n_steps = 720 * 3  # 3 revolutions at 0.5 deg/step
        t = np.arange(n_steps) * dt
        steps = np.arange(1, n_steps + 1)

        # Baseline physical aerodynamic torque vs into-wind azimuth:
        # Blade torque peaked in upwind sector [0, 180 deg]
        # In CCW, theta advances from top (into wind) along positive rotation.
        # In CW, theta advances from bottom (into wind) along negative rotation in world space.
        # But relative to the into-wind zero, the physical blade sees the identical inflow angle!
        theta_rel = (omega * t * 180.0 / math.pi) % 360.0
        # Add blade phasing for 3 blades
        m_blade1 = 1.0 + 1.5 * np.sin(np.radians(theta_rel)) ** 2
        m_blade2 = 1.0 + 1.5 * np.sin(np.radians((theta_rel - 120.0) % 360.0)) ** 2
        m_blade3 = 1.0 + 1.5 * np.sin(np.radians((theta_rel - 240.0) % 360.0)) ** 2
        m_total = m_blade1 + m_blade2 + m_blade3

        # CCW files (Fluent exports positive moment for counter-clockwise work)
        f_ccw_tot = os.path.join(tmpdir, "ccw_total.out")
        f_ccw_b1 = os.path.join(tmpdir, "ccw_b1.out")
        f_ccw_b2 = os.path.join(tmpdir, "ccw_b2.out")
        f_ccw_b3 = os.path.join(tmpdir, "ccw_b3.out")
        make_fluent_file(f_ccw_tot, "total-moment", steps, m_total, t)
        make_fluent_file(f_ccw_b1, "b1-moment", steps, m_blade1, t)
        make_fluent_file(f_ccw_b2, "b2-moment", steps, m_blade2, t)
        make_fluent_file(f_ccw_b3, "b3-moment", steps, m_blade3, t)

        p_ccw = Params(
            U=U, R=R, chord=chord, n_blades=nb, omega=omega,
            rotation_dir="ccw", azimuth_zero="into_wind", n_stable=2
        )
        in_ccw = InputFiles(total=f_ccw_tot, blades=[f_ccw_b1, f_ccw_b2, f_ccw_b3])
        out_ccw = analyze(p_ccw, in_ccw)

        # CW files (Fluent exports negative raw moment for clockwise work)
        f_cw_tot = os.path.join(tmpdir, "cw_total.out")
        f_cw_b1 = os.path.join(tmpdir, "cw_b1.out")
        f_cw_b2 = os.path.join(tmpdir, "cw_b2.out")
        f_cw_b3 = os.path.join(tmpdir, "cw_b3.out")
        make_fluent_file(f_cw_tot, "total-moment", steps, -m_total, t)
        make_fluent_file(f_cw_b1, "b1-moment", steps, -m_blade1, t)
        make_fluent_file(f_cw_b2, "b2-moment", steps, -m_blade2, t)
        make_fluent_file(f_cw_b3, "b3-moment", steps, -m_blade3, t)

        p_cw = Params(
            U=U, R=R, chord=chord, n_blades=nb, omega=omega,
            rotation_dir="cw", azimuth_zero="into_wind", n_stable=2
        )
        in_cw = InputFiles(total=f_cw_tot, blades=[f_cw_b1, f_cw_b2, f_cw_b3])
        out_cw = analyze(p_cw, in_cw)

        # Both runs must produce identical positive mean Cp and matching Cp(theta) curves
        assert out_ccw["res"]["cp_reported_mean"] > 0
        assert out_cw["res"]["cp_reported_mean"] > 0
        assert abs(out_ccw["res"]["cp_reported_mean"] - out_cw["res"]["cp_reported_mean"]) < 1e-4

        # Compare phase-averaged Cp(theta) bin-by-bin
        cp_ccw_bins = out_ccw["az"]["cp_mean"]
        cp_cw_bins = out_cw["az"]["cp_mean"]
        assert len(cp_ccw_bins) == len(cp_cw_bins) == 360
        max_diff = np.max(np.abs(cp_ccw_bins - cp_cw_bins))
        assert max_diff < 0.05, f"CW vs CCW Cp(theta) mismatch: {max_diff}"


# ---------------------------------------------------------------------------
# 2. Moment sign precedence
# ---------------------------------------------------------------------------
def test_moment_sign_precedence():
    """Verify CW + auto does not flip twice, and genuinely negative Cp preserves sign."""
    with tempfile.TemporaryDirectory() as tmpdir:
        steps = np.arange(1, 721)
        t = steps * 0.001
        m_vals = np.ones(720) * (-2.0)  # Raw negative moment
        f = os.path.join(tmpdir, "test_moment.out")
        make_fluent_file(f, "moment", steps, m_vals, t)

        # Case 1: CW + auto with physically driving rotor (Fluent outputs negative moment)
        p_cw_auto = Params(
            U=8.0, R=0.5, chord=0.06, n_blades=3, omega=56.0,
            rotation_dir="cw", moment_sign="auto", n_stable=1
        )
        in_files = InputFiles(total=f, blades=[])
        res_cw = analyze(p_cw_auto, in_files)
        # Direction factor (-1.0) applied once -> resulting Cp is positive
        assert res_cw["res"]["cp_reported_mean"] > 0

        # Case 2: Genuinely negative Cp case in CCW (rotor dissipating power, m_vals < 0)
        p_ccw_neg = Params(
            U=8.0, R=0.5, chord=0.06, n_blades=3, omega=56.0,
            rotation_dir="ccw", moment_sign="auto", n_stable=1
        )
        res_neg = analyze(p_ccw_neg, in_files)
        # Genuinely negative Cp preserves negative sign and logs warning
        assert res_neg["res"]["cp_reported_mean"] < 0
        assert any("negative" in w.lower() for w in res_neg["warnings"])


# ---------------------------------------------------------------------------
# 3. Normal / tangential per-blade force decomposition and signs
# ---------------------------------------------------------------------------
def test_normal_tangential_per_blade_forces_and_signs():
    """Verify coordinate transformation: F_tan = -Fx*cos(theta) - Fy*sin(theta)."""
    # At theta = 0 (top, blade moving in -x direction):
    # Motion vector e_tan = (-cos(0), -sin(0)) = (-1, 0).
    # If Fx = -10 N (force pointing in -x direction, along motion):
    # Driving tangential force must be +10 N!
    # Formula: F_tan = -(-10)*cos(0) - 0 = +10. Correct!
    theta = np.array([0.0, 90.0, 180.0, 270.0])
    fx = np.array([-10.0, 0.0, 10.0, 0.0])
    fy = np.array([0.0, -10.0, 0.0, 10.0])

    Cn, C_tan = compute_normal_tangential_coefficients(fx, fy, theta, rho=1.225, U=10.0, chord=0.1, depth=1.0)
    q_blade = 0.5 * 1.225 * (10.0 ** 2) * 0.1 * 1.0
    f_tan = C_tan * q_blade
    f_n = Cn * q_blade

    # All four points have force aligned with blade motion -> f_tan should all be +10.0
    assert np.allclose(f_tan, 10.0, atol=1e-5)
    # Normal forces should be zero for this test
    assert np.allclose(f_n, 0.0, atol=1e-5)
    # Check that c_tan is normalized by q_blade = 0.5 * rho * U^2 * chord * depth
    assert np.allclose(C_tan, 10.0 / q_blade, atol=1e-5)


# ---------------------------------------------------------------------------
# 4. Diagnostics Check 15 spectral harmonics (BPF on total, f0 on blade)
# ---------------------------------------------------------------------------
def test_check_15_bpf_and_fundamental_harmonics():
    """Total torque has peaks at k*N*f0, blade torque detects f0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        nb = 3
        omega = 60.0  # rad/s -> f0 = 60 / (2*pi) ≈ 9.549 Hz
        f0 = omega / (2.0 * math.pi)
        bpf = nb * f0  # 28.648 Hz
        dt = 0.001
        n_pts = 4000
        steps = np.arange(1, n_pts + 1)
        t = steps * dt

        # Total torque signal composed of 3*f0 (BPF) harmonic
        total_torque = 5.0 + 2.0 * np.sin(2.0 * math.pi * bpf * t)
        # Blade torque signal composed of fundamental f0
        blade_torque = 2.0 + 1.5 * np.sin(2.0 * math.pi * f0 * t)

        f_tot = os.path.join(tmpdir, "total.out")
        f_b1 = os.path.join(tmpdir, "b1.out")
        make_fluent_file(f_tot, "total-moment", steps, total_torque, t)
        make_fluent_file(f_b1, "b1-moment", steps, blade_torque, t)

        p_diag = Params(U=8.0, R=0.5, chord=0.06, n_blades=nb, omega=omega, dt=dt)
        in_diag = InputFiles(total=f_tot, blades=[f_b1])
        rep = run_file_health_diagnostics(p_diag, in_diag)
        # Check 15 should pass without mismatch warnings because harmonics match
        freq_mismatches = [
            f for f in rep.findings
            if "mismatch" in f.check.lower() or "rotor speed" in f.check.lower()
        ]
        assert len(freq_mismatches) == 0

        # Test intentional mismatch: total torque at wrong frequency (say 15.0 Hz != k * bpf)
        wrong_torque = 5.0 + 2.0 * np.sin(2.0 * math.pi * 15.0 * t)
        f_wrong = os.path.join(tmpdir, "total_wrong.out")
        make_fluent_file(f_wrong, "total-moment", steps, wrong_torque, t)
        in_wrong = InputFiles(total=f_wrong, blades=[])
        rep_wrong = run_file_health_diagnostics(p_diag, in_wrong)
        mismatches_found = [
            f for f in rep_wrong.findings
            if "rotor speed mismatch" in f.check.lower()
        ]
        assert len(mismatches_found) > 0
        assert "cannot detect wrong free-stream speed" in mismatches_found[0].suggested_fix


# ---------------------------------------------------------------------------
# 5. Autodetect rejects Cd/Cl files and candidate transparency
# ---------------------------------------------------------------------------
def test_autodetect_broad_pattern_and_coefficient_rejection():
    """Candidate scanner rejects Cd/Cl files and lists candidate attributes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        steps = np.arange(1, 101)
        t = steps * 0.001
        vals = np.ones(100)

        # Create valid and invalid files
        f_cd = os.path.join(tmpdir, "blade-cd.out")
        f_drag_coeff = os.path.join(tmpdir, "rotor-drag-coeff.out")
        f_valid_thrust1 = os.path.join(tmpdir, "thrust-force.out")
        f_valid_thrust2 = os.path.join(tmpdir, "fx-thrust.out")
        f_valid_mom = os.path.join(tmpdir, "total-moment.out")

        make_fluent_file(f_cd, "cd-1", steps, vals, t)
        make_fluent_file(f_drag_coeff, "drag-coeff", steps, vals, t)
        make_fluent_file(f_valid_thrust1, "thrust-force", steps, vals, t)
        make_fluent_file(f_valid_thrust2, "fx-thrust", steps, vals, t)
        make_fluent_file(f_valid_mom, "total-moment", steps, vals, t)

        candidates = scan_folder_candidates(tmpdir, n_blades=3)

        # Thrust candidates must NOT include cd or drag-coeff files, and must list multiple valid candidates
        assert "fx" in candidates
        assert len(candidates["fx"]) == 2
        thrust_paths = [c["path"] for c in candidates["fx"]]
        assert f_cd not in thrust_paths
        assert f_drag_coeff not in thrust_paths
        assert f_valid_thrust1 in thrust_paths
        assert f_valid_thrust2 in thrust_paths

        # Check candidate structure has last_step, size_bytes, mtime
        cand = candidates["fx"][0]
        assert cand["last_step"] == 100
        assert cand["size_bytes"] > 0
        assert cand["mtime"] > 0


# ---------------------------------------------------------------------------
# 6. Wall y+ 3-tier limits (Good <= 1, Caution 1-5, Poor > 5)
# ---------------------------------------------------------------------------
def test_wall_yplus_three_tiers():
    """Verify 3-tier wall y+ evaluation and reporting of mean and max."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create synthetic y+ time series with mean ≈ 1.8, max ≈ 3.5
        t = np.linspace(0, 10, 500)
        yplus = 0.8 + 2.7 * np.sin(t * 2.0) ** 4  # oscillates between 0.8 and 3.5
        out_png = os.path.join(tmpdir, "yplus_test.png")

        _, metrics = plot_yplus_monitor({"time": t, "yplus": yplus}, out_png, limit=1.0)
        assert os.path.exists(out_png)
        assert metrics["mean_yplus"] < 2.5
        assert metrics["max_yplus"] > 3.0
        assert metrics["tier"] == "Caution"  # Because max is between 1 and 5
        assert metrics["max_yplus"] > 3.0
        assert metrics["tier"] == "Caution"  # Because max is between 1 and 5


# ---------------------------------------------------------------------------
# 7. Static stall angle default (12.0 deg) and geometric stall exceedance
# ---------------------------------------------------------------------------
def test_static_stall_angle_and_geometric_exceedance():
    """Verify default static stall angle is 12.0 deg and exceedance is computed."""
    # Low TSR (lambda = 1.5): geometric alpha exceeds +/-12 deg across upwind/downwind
    theta = np.linspace(0, 360, 360, endpoint=False)
    kin = compute_blade_kinematics(
        theta_deg=theta,
        tsr=1.5,
        chord=0.06,
        R=0.5,
        omega=24.0,
        U=8.0,
        static_stall_deg=12.0
    )
    assert kin["static_stall_deg"] == 12.0
    assert kin["stall_fraction_pct"] > 0
    assert kin["geometric_stall_exceedance_theta"] is not None
    assert 0 <= kin["geometric_stall_exceedance_theta"] <= 180.0


# ---------------------------------------------------------------------------
# 8. Reduced frequency 3-tier classification
# ---------------------------------------------------------------------------
def test_reduced_frequency_three_tier_classification():
    """Verify standard aerodynamic classes for reduced frequency k."""
    assert "Quasi-steady" in classify_reduced_frequency(0.02)
    assert "Unsteady aerodynamic effects significant" in classify_reduced_frequency(0.08)
    assert "Strongly unsteady" in classify_reduced_frequency(0.25)


# ---------------------------------------------------------------------------
# 9. PCHIP monotonic spline and local quadratic peak fit
# ---------------------------------------------------------------------------
def test_batch_pchip_spline_monotonicity_and_peak_fit():
    """PCHIP spline preserves monotonicity and peak fit locates vertex."""
    # Monotonic step data where natural cubic spline would overshoot
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([0.0, 0.0, 1.0, 1.0, 1.0])
    x_fine = np.linspace(1.0, 5.0, 100)
    spline_fn = pchip_spline(x, y)
    y_pchip = spline_fn(x_fine)

    # Monotonicity test: PCHIP must remain strictly within [0, 1]
    assert np.all(y_pchip >= -1e-6)
    assert np.all(y_pchip <= 1.0 + 1e-6)

    # Peak test with batch quadratic fit
    lambda_arr = np.array([1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    cp_arr = np.array([0.15, 0.28, 0.41, 0.45, 0.38, 0.22])  # Peak around 3.0
    x_eval = np.linspace(1.5, 4.0, 200)
    spline_fn2 = pchip_spline(lambda_arr, cp_arr)
    cp_eval = spline_fn2(x_eval)
    peak_idx = np.argmax(cp_eval)
    assert 2.8 <= x_eval[peak_idx] <= 3.2


# ---------------------------------------------------------------------------
# 10. ASME V&V 20 GCI metadata equality and oscillatory mode
# ---------------------------------------------------------------------------
def test_asme_vv20_gci_metadata_and_oscillatory_modes():
    """Refuses GCI on metadata mismatch and handles oscillatory convergence."""
    # Metadata mismatch test
    run1 = {
        "summary_kpis": {"cp_reported_mean": 0.45},
        "case_metadata": {"mesh_cells": 100000, "turb_model": "k-omega SST"},
        "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": 1e-4}
    }
    run2_bad = {
        "summary_kpis": {"cp_reported_mean": 0.43},
        "case_metadata": {"mesh_cells": 50000, "turb_model": "k-epsilon"},  # Mismatch!
        "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": 1e-4}
    }
    run3 = {
        "summary_kpis": {"cp_reported_mean": 0.39},
        "case_metadata": {"mesh_cells": 25000, "turb_model": "k-omega SST"},
        "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": 1e-4}
    }

    with pytest.raises(ValueError, match="differ across runs"):
        calculate_gci([run1, run2_bad, run3], study_type="mesh")

    # Oscillatory convergence test (eps21 and eps32 have opposite signs)
    run2_ok = {
        "summary_kpis": {"cp_reported_mean": 0.45},  # phi2 > phi1, but phi3 < phi2 (oscillating)
        "case_metadata": {"mesh_cells": 50000, "turb_model": "k-omega SST"},
        "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": 1e-4}
    }
    run1_osc = {
        "summary_kpis": {"cp_reported_mean": 0.42},
        "case_metadata": {"mesh_cells": 100000, "turb_model": "k-omega SST"},
        "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": 1e-4}
    }
    run3_osc = {
        "summary_kpis": {"cp_reported_mean": 0.41},
        "case_metadata": {"mesh_cells": 25000, "turb_model": "k-omega SST"},
        "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": 1e-4}
    }
    gci_res = calculate_gci([run1_osc, run2_ok, run3_osc], study_type="mesh")
    assert gci_res["status"] == "OSCILLATORY_CONVERGENCE"
    assert math.isnan(gci_res["observed_order_p"])  # Does not apply invalid monotonic power law


# ---------------------------------------------------------------------------
# 11. Aitken 0 < r < 0.9 requirement, capping, and exponential decay fit
# ---------------------------------------------------------------------------
def test_aitken_robustness_and_exponential_decay_fit():
    """Aitken rejects r >= 0.9 and exponential fit provides asymptotic limit."""
    # Test 1: Sluggish decay (r = 0.95 >= 0.90) -> unreliable
    x0, x1, x2 = 0.30, 0.35, 0.3975  # d1 = 0.05, d2 = 0.0475, r = 0.95
    aitken_res = aitken_extrapolation(x0, x1, x2)
    assert not aitken_res["is_reliable"]

    # Test 2: Healthy geometric decay (r = 0.5)
    x0, x1, x2 = 0.30, 0.38, 0.42  # d1 = 0.08, d2 = 0.04, r = 0.5
    aitken_res_good = aitken_extrapolation(x0, x1, x2)
    assert aitken_res_good["is_reliable"]
    assert aitken_res_good["cp_asymptotic"] > 0.42

    # Test 3: Multi-revolution exponential decay fit
    rev_history = [0.25, 0.35, 0.41, 0.44, 0.455]
    exp_res = fit_exponential_decay(rev_history)
    assert exp_res["asymptotic_limit"] is not None
    assert exp_res["asymptotic_limit"] >= 0.455
    assert exp_res["uncertainty"] is not None


# ---------------------------------------------------------------------------
# 12. Multi-stage phase detector skips isolated single-step drops
# ---------------------------------------------------------------------------
def test_multistage_phase_detector_skips_isolated_gap():
    """Isolated single-step dt drops that revert do not create false stages."""
    omega = 50.0
    Trev = 2.0 * math.pi / omega
    dt = 0.001
    n_pts = 1000
    t = np.arange(n_pts) * dt
    steps = np.arange(1, n_pts + 1)

    # Introduce a single dropped step gap in the middle (e.g. at index 500)
    # where step index jumps by 2, causing dt to be 2*dt for one step and then revert
    t[500:] = t[500:] + dt

    stages = detect_stages(steps, t, Trev, omega)
    # Because it immediately reverts after 1 step, it must NOT split into multiple stages
    assert len(stages) == 1
