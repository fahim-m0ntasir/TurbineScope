# -*- coding: utf-8 -*-
"""
VAWT Post Validation Acceptance Tests (Tolerance 0.0005 on Cp)
=============================================================
Validates numerical precision against Set A and Set B benchmark datasets.
"""
import numpy as np

from vawt_post.engine.config import Params
from vawt_post.engine.analysis import analyze
from vawt_post.engine.synthetic import generate_dataset_a, generate_dataset_b


def test_validation_dataset_a(tmp_path):
    """Set A:
    1°/step, 8 revolutions, starts at step 45, λ = 4, U = 7, R = 0.35, 3 blades:
    - 7 complete revolutions
    - detected offsets 0°, 120°, 240°
    - mean Cp of last two revolutions = 0.2929 (tolerance 0.0005)
    - status NOT YET STABLE (last change -0.62%)
    - dominant harmonic 3.0
    - Ct ≈ 0.96
    - max geometric α 14.48° at 104.5°
    """
    out_dir = str(tmp_path / "set_a")
    p, f = generate_dataset_a(out_dir)
    res_dict = analyze(p, f)
    res = res_dict["res"]

    # 1. Complete revolutions
    assert res["n_full"] == 7

    # 2. Detected offsets
    np.testing.assert_allclose(res["offsets"], [0.0, 120.0, 240.0], atol=1e-5)

    # 3. Mean Cp of last two revolutions within tolerance 0.0005 of 0.2929
    assert abs(res["cp_mean"] - 0.2929) < 0.0005

    # 4. Convergence status NOT YET STABLE and last change ≈ -0.62%
    assert not res["is_stable"]
    assert abs(res["cp_last_change"] * 100.0 - (-0.62)) < 0.05

    # 5. Dominant harmonic 3.0
    assert abs(res["dominant_harmonic"] - 3.0) < 0.1

    # 6. Thrust coefficient Ct ≈ 0.96
    assert abs(res["ct_mean"] - 0.96) < 0.02

    # 7. Max geometric alpha 14.48° at 104.5°
    assert abs(res["alpha_max"] - 14.48) < 0.05
    assert abs(res["alpha_max_theta"] - 104.5) < 0.5


def test_validation_dataset_b(tmp_path):
    """Set B (two time-step phases: 2880 steps at 1.0023°/step then 2880 steps at 0.5°/step):
    - 12 complete revolutions
    - trailing stable set 3
    - mean Cp of revolutions 11 and 12 = 0.3515 (tolerance 0.0005)
    - Ct ≈ 1.01
    - a warning about the 1.0023°/step phase (359.18 steps/rev)
    - upwind torque peak ≈ 1.00 N·m at ≈ 88°
    """
    out_dir = str(tmp_path / "set_b")
    p, f = generate_dataset_b(out_dir)
    res_dict = analyze(p, f)
    res = res_dict["res"]

    # 1. Complete revolutions
    assert res["n_full"] == 12

    # 2. Trailing stable set
    assert res["stable_n"] == 3
    assert res["is_stable"]

    # 3. Mean Cp of revolutions 11 and 12 = 0.3515 (tolerance 0.0005)
    assert abs(res["cp_mean"] - 0.3515) < 0.0005

    # 4. Ct ≈ 1.01
    assert abs(res["ct_mean"] - 1.01) < 0.02

    # 5. Warning about 1.0023°/step phase
    warnings_text = " ".join(res_dict["warnings"])
    assert "steps/rev" in warnings_text
    assert "359.1" in warnings_text or "359.2" in warnings_text

    # 6. Upwind torque peak ≈ 1.00 N·m at ≈ 88°
    assert abs(res["torque_peak_up"] - 1.00) < 0.03
    assert abs(res["torque_peak_up_theta"] - 88.0) < 2.0


def test_multistage_arbitrary_shifts(tmp_path):
    """Test multi-stage simulation with azimuthal shifts of 5.0, 2.0, 1.0, 0.5, and 0.25 deg in the same run."""
    from vawt_post.engine.synthetic import SimulationStage, generate_multistage_dataset

    out_dir = str(tmp_path / "multistage_shifts")
    p = Params(U=8.0, tsr=3.5, R=0.5, chord=0.06, n_blades=3, bin_deg=0.25)
    stages = [
        SimulationStage(n_steps=360, deg_per_step=5.0, name="Stage 1 (5.0°)"),
        SimulationStage(n_steps=720, deg_per_step=2.0, name="Stage 2 (2.0°)"),
        SimulationStage(n_steps=1440, deg_per_step=1.0, name="Stage 3 (1.0°)"),
        SimulationStage(n_steps=2880, deg_per_step=0.5, name="Stage 4 (0.5°)"),
        SimulationStage(n_steps=5760, deg_per_step=0.25, name="Stage 5 (0.25°)"),
    ]
    p_ret, f_ret, info = generate_multistage_dataset(out_dir, p, stages, target_cp_final=0.33)
    res_dict = analyze(p, f_ret)
    res = res_dict["res"]

    # Verify all 5 stages were detected
    assert len(res["phases"]) == 5
    deg_steps = [round(ph["deg_per_step"], 2) for ph in res["phases"]]
    assert deg_steps == [5.0, 2.0, 1.0, 0.5, 0.25]

    # Verify total complete revolutions
    assert res["n_full"] >= 20

    # Verify 0.25 deg binning creates 1440 azimuth bins
    assert len(res_dict["az"]["theta"]) == 1440
    assert abs(res["cp_mean"] - 0.33) < 0.05


def test_stable_revolutions_metrics_and_mismatch_warning(tmp_path):
    """Test auto-reporting of stable revolutions and rotor frequency discrepancy warning."""
    out_dir = str(tmp_path / "set_b_stable")
    p, f = generate_dataset_b(out_dir)

    # 1. Test auto-reporting mode with n_stable = 0
    p.n_stable = 0
    res_dict = analyze(p, f)
    res = res_dict["res"]

    assert res["stable_n"] == 3
    assert res["is_stable"]
    assert len(res["reported_revs"]) == 3
    assert res["reported_revs"] == [10, 11, 12]
    assert abs(res["cp_stable_mean"] - 0.3516) < 0.001
    assert "cp_all_mean" in res

    # 2. Test speed mismatch detection when U=5.0 is mistakenly configured for U=7.0 case
    p_wrong = Params(U=5.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3)
    res_wrong = analyze(p_wrong, f)
    warnings_text = " ".join(res_wrong["warnings"])
    assert "Rotor speed mismatch" in warnings_text
    assert "79." in warnings_text or "80" in warnings_text

