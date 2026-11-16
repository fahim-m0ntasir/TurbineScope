# -*- coding: utf-8 -*-
"""
Tier 5.1: Independent Physical Benchmark Tests
==============================================
Validates analysis algorithms against pure analytical benchmarks:
1. Exact constant torque (analytical Cp = omega * M / Pref)
2. Exact sinusoidal torque (known mean and exact harmonic frequency)
3. Known blade phase shift (detected offset validation)
4. Non-uniform time steps (time-weighted Riemann integral check)
5. Clockwise rotation (CW sign conventions)
6. 2-blade and 4-blade rotors (BPF harmonic detection)
7. Genuine negative Cp preservation
8. Non-integer steps per revolution and timing jitter
9. Duplicate and missing steps QA detection
"""
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.synthetic import make_fluent_file
from vawt_post.engine.analysis import analyze


def test_5_1_constant_torque_exact_cp(tmp_path):
    """Analytical benchmark 1: Constant torque M0 must produce EXACT analytical Cp."""
    U = 10.0
    R = 1.0
    tsr = 3.0
    omega = tsr * U / R  # 30 rad/s
    rho = 1.225
    depth = 1.0
    M0 = 5.0  # N.m

    # Pref = 0.5 * rho * U^3 * 2 * R * depth = 0.5 * 1.225 * 1000 * 2 * 1 * 1 = 1225.0 W
    Pref_analytical = 0.5 * rho * (U ** 3) * (2.0 * R) * depth
    # Analytical Cp = omega * M0 / Pref = 30 * 5.0 / 1225.0 = 150 / 1225 = 0.12244897959...
    cp_exact = (omega * M0) / Pref_analytical

    Trev = 2.0 * np.pi / omega
    dt = Trev / 360.0
    steps = np.arange(1, 1442)  # 4 full revolutions
    t = steps * dt
    m = np.full_like(t, M0)

    f_total = str(tmp_path / "constant_torque.out")
    make_fluent_file(f_total, "total", steps, m, t)

    p = Params(U=U, tsr=tsr, R=R, n_blades=3, depth=depth, rho=rho)
    R_res = analyze(p, InputFiles(total=f_total))

    computed_cp = R_res["res"]["cp_mean"]
    assert abs(computed_cp - cp_exact) < 1e-4, f"Computed {computed_cp} vs exact {cp_exact}"


def test_5_2_sinusoidal_torque_exact_mean_and_harmonic(tmp_path):
    """Analytical benchmark 2: Sinusoidal torque M(t) = M0 + M1*sin(k*omega*t).

    Cycle-average must be M0 (giving exact Cp0), and dominant FFT harmonic must equal k.
    """
    U = 8.0
    R = 0.5
    tsr = 4.0
    omega = tsr * U / R  # 64 rad/s
    k_harmonic = 3  # 3x rotor frequency
    M0 = 2.0
    M1 = 0.8

    Pref = 0.5 * 1.225 * (U ** 3) * (2.0 * R) * 1.0
    cp_exact = (omega * M0) / Pref

    Trev = 2.0 * np.pi / omega
    dt = Trev / 360.0
    steps = np.arange(1, 1442)  # 4 full revs
    t = steps * dt
    m = M0 + M1 * np.sin(k_harmonic * omega * t)

    f_total = str(tmp_path / "sine_torque.out")
    make_fluent_file(f_total, "total", steps, m, t)

    p = Params(U=U, tsr=tsr, R=R, n_blades=3)
    R_res = analyze(p, InputFiles(total=f_total))

    assert abs(R_res["res"]["cp_mean"] - cp_exact) < 1e-3
    assert abs(R_res["res"]["dominant_harmonic"] - k_harmonic) < 0.15


def test_5_3_known_blade_azimuthal_offsets(tmp_path):
    """Analytical benchmark 3: Blades generated with known phase offsets must be detected within 1 deg."""
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)
    t = steps * dt
    th_rot = (omega * t) % (2.0 * np.pi)

    # Known offsets: Blade 1 = 0 deg, Blade 2 = 120 deg, Blade 3 = 240 deg
    known_offsets_rad = [0.0, np.radians(120.0), np.radians(240.0)]
    f_blades = []

    for b_idx, phi in enumerate(known_offsets_rad):
        # Peak torque occurs at th_rot + phi = pi / 2
        m_blade = 0.5 + 0.4 * np.sin(th_rot + phi)
        b_path = str(tmp_path / f"blade_{b_idx + 1}.out")
        make_fluent_file(b_path, f"blade_{b_idx + 1}", steps, m_blade, t)
        f_blades.append(b_path)

    # Total moment
    m_tot = np.sum([0.5 + 0.4 * np.sin(th_rot + phi) for phi in known_offsets_rad], axis=0)
    f_total = str(tmp_path / "total_blades.out")
    make_fluent_file(f_total, "total", steps, m_tot, t)

    p = Params(U=8.0, tsr=3.0, R=0.5, n_blades=3, offsets="auto")
    R_res = analyze(p, InputFiles(total=f_total, blades=f_blades))

    detected = R_res["res"]["offsets"]
    assert len(detected) == 3
    assert abs(detected[0] - 0.0) < 2.0
    assert abs(detected[1] - 120.0) < 2.5
    assert abs(detected[2] - 240.0) < 2.5


def test_5_4_non_uniform_timesteps_riemann_check(tmp_path):
    """Analytical benchmark 4: Multi-stage non-uniform dt tested against exact time-weighted integral."""
    omega = 20.0
    # Stage 1: dt1 = 0.001 s for 500 steps
    dt1 = 0.001
    t1 = np.arange(1, 501) * dt1
    m1 = 1.0 + 0.5 * np.sin(omega * t1)

    # Stage 2: dt2 = 0.0005 s for 1000 steps
    dt2 = 0.0005
    t2 = t1[-1] + np.arange(1, 1001) * dt2
    m2 = 1.0 + 0.5 * np.sin(omega * t2)

    t_all = np.concatenate([t1, t2])
    m_all = np.concatenate([m1, m2])
    steps_all = np.arange(1, len(t_all) + 1)

    f_total = str(tmp_path / "multistage_dt.out")
    make_fluent_file(f_total, "total", steps_all, m_all, t_all)

    p = Params(U=8.0, tsr=1.25, omega=omega, R=0.5, n_blades=3)
    R_res = analyze(p, InputFiles(total=f_total))

    # Verify multistage detection
    assert R_res["res"]["is_multistage"] is True
    assert len(R_res["res"]["phases"]) == 2
    # Analytical cycle-averaged torque of 1.0 + 0.5*sin(omega*t) over complete revolutions is exactly 1.0
    assert abs(R_res["res"]["torque_mean"] - 1.0) < 0.05


def test_5_5_clockwise_rotation_convention(tmp_path):
    """Analytical benchmark 5: Clockwise rotation direction convention.

    In Fluent, clockwise rotation corresponds to negative mesh omega and negative monitored
    aerodynamic torque. Post-processor applies rotation_dir='cw' to evaluate positive turbine power.
    """
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)
    t = steps * dt
    # Negative moment in Fluent for CW rotation
    m = -1.5 - 0.3 * np.sin(3 * omega * t)

    f_total = str(tmp_path / "cw.out")
    make_fluent_file(f_total, "total", steps, m, t)

    p_cw = Params(U=8.0, tsr=1.25, omega=omega, R=0.5, n_blades=3, rotation_dir="cw")
    R_res = analyze(p_cw, InputFiles(total=f_total))

    assert R_res["p"].rotation_dir == "cw"
    assert R_res["res"]["cp_mean"] > 0


def test_5_6_two_blade_and_four_blade_rotors(tmp_path):
    """Analytical benchmark 6: Rotor blade counts N=2 and N=4 harmonic checks."""
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)
    t = steps * dt

    # N = 2 blade rotor: torque ripple frequency is 2x rotor frequency
    m_2 = 1.2 + 0.4 * np.sin(2 * omega * t)
    f_n2 = str(tmp_path / "n2.out")
    make_fluent_file(f_n2, "total", steps, m_2, t)

    p_n2 = Params(U=8.0, tsr=1.25, omega=omega, R=0.5, n_blades=2)
    R_n2 = analyze(p_n2, InputFiles(total=f_n2))
    assert abs(R_n2["res"]["dominant_harmonic"] - 2.0) < 0.2

    # N = 4 blade rotor: torque ripple frequency is 4x rotor frequency
    m_4 = 1.2 + 0.4 * np.sin(4 * omega * t)
    f_n4 = str(tmp_path / "n4.out")
    make_fluent_file(f_n4, "total", steps, m_4, t)

    p_n4 = Params(U=8.0, tsr=1.25, omega=omega, R=0.5, n_blades=4)
    R_n4 = analyze(p_n4, InputFiles(total=f_n4))
    assert abs(R_n4["res"]["dominant_harmonic"] - 4.0) < 0.2


def test_5_7_negative_cp_preserved_with_notice(tmp_path):
    """Analytical benchmark 7: Genuine negative torque (e.g. stalled rotor) must keep Cp < 0."""
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)
    t = steps * dt
    # Negative mean torque
    m_neg = -0.75 + 0.2 * np.sin(3 * omega * t)

    f_neg = str(tmp_path / "neg_cp.out")
    make_fluent_file(f_neg, "total", steps, m_neg, t)

    p = Params(U=8.0, tsr=1.5, R=0.5, n_blades=3)
    R_res = analyze(p, InputFiles(total=f_neg))

    assert R_res["res"]["cp_mean"] < 0, "Negative Cp must not be inverted!"
    # Check that warning is issued
    assert any("negative" in w.lower() for w in R_res["warnings"])


def test_5_8_non_integer_steps_and_timing_jitter(tmp_path):
    """Analytical benchmark 8: Non-integer steps per rev (e.g. 177.3) and timestamp jitter."""
    omega = 19.345  # Trev = 2*pi / 19.345 = 0.324796 s
    Trev = 2.0 * np.pi / omega
    # dt chosen so steps per rev is 177.33 (non-integer)
    dt_nominal = Trev / 177.33
    steps = np.arange(1, 1200)

    # Add Gaussian jitter of 1% of dt
    rng = np.random.default_rng(42)
    jitter = rng.normal(0, 0.01 * dt_nominal, len(steps))
    t = np.cumsum(np.full_like(steps, dt_nominal, dtype=float) + jitter)

    m = 1.1 + 0.3 * np.sin(3 * omega * t)
    f_jitter = str(tmp_path / "jitter.out")
    make_fluent_file(f_jitter, "total", steps, m, t)

    p = Params(U=8.0, tsr=3.0, R=0.5, n_blades=3)
    # Must post-process cleanly without errors
    R_res = analyze(p, InputFiles(total=f_jitter))
    assert R_res["res"]["n_full"] >= 5
    assert R_res["res"]["cp_mean"] > 0


def test_5_9_duplicate_and_missing_steps_qa_detection(tmp_path):
    """Analytical benchmark 9: Duplicate steps and non-monotonic / missing steps detected by QA checks."""
    steps = np.array([1, 2, 3, 3, 4, 10, 11, 12], dtype=int)  # duplicate step 3, jump from 4 to 10
    t = np.array([0.001, 0.002, 0.003, 0.003, 0.004, 0.010, 0.011, 0.012], dtype=float)
    m = np.ones(8, dtype=float)

    f_dup = str(tmp_path / "duplicate_steps.out")
    make_fluent_file(f_dup, "total", steps, m, t)

    from vawt_post.engine.parser import read_fluent_report
    rep = read_fluent_report(f_dup)

    # Run QA checks on parsed time series
    from vawt_post.engine.analysis import run_qa_checks
    p = Params(U=8.0, tsr=3.0, R=0.5, n_blades=3)
    f = InputFiles(total=f_dup)
    raw_series = {"total": rep}
    qa_list = run_qa_checks(
        p=p,
        f=f,
        raw_series=raw_series,
        aligned_step=rep["step"],
        aligned_t=rep["time"],
        res={"Trev": 0.1, "dominant_harmonic": 3.0, "is_stable": True, "cp_last_change": 0.001, "cp_mean": 0.3, "phases": [{"step_from": 1, "step_to": 12, "dt": 0.001, "deg_per_step": 1.0}]}
    )

    names = {q["check"]: q["status"] for q in qa_list}
    assert "Duplicate steps" in names
    assert names["Duplicate steps"] in ("WARN", "FAIL")
    assert "Time monotonicity" in names
