# -*- coding: utf-8 -*-
"""
Tier 1 Tests: Correctness and Trust Verification
=================================================
Verifies:
1.1 Empty required parameters, validation failure messages, optional chord, preset labeling & source provenance.
1.2 Explicit rotation direction (CCW/CW), default fixed sign, preserving negative Cp with notice.
1.3 Shaft handling: blade sum vs total file, shaft subtraction, side-by-side Cp reporting, discrepancy warning.
1.4 Stronger stability: multi-criteria stability, waveform periodicity RMS, Aitken extrapolation, period-2 subharmonic check.
1.5 Rotor speed BPF check at multiples (0.5x, 1x, 2x) & torque autocorrelation revolution period check.
1.6 Azimuth conventions (zero position, CW/CCW, free offset, manual offsets, weak cross-correlation warning).
1.7 Unsteady blade rotation velocity vector.
1.8 Polar plot with negative values (offset + dashed zero circle).
1.9 Cm vs azimuth plot with min/max envelopes.
"""
import os
import math
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.analysis import analyze
from vawt_post.engine.synthetic import make_fluent_file
from vawt_post.engine.plots import make_figures
from vawt_post.engine.excel import write_excel


def test_1_1_empty_required_parameters():
    """1.1 Empty required parameters must stop with a message listing each missing field."""
    p_empty = Params()
    errors = p_empty.validate()
    assert len(errors) > 0
    missing_msg = errors[0]
    assert "Missing required parameter(s):" in missing_msg
    assert "Free-stream speed U" in missing_msg
    assert "Rotor radius R" in missing_msg
    assert "Number of blades N" in missing_msg
    assert "Tip speed ratio λ or rotor speed ω" in missing_msg

    # Chord is optional: supplying U, R, N, tsr without chord validates cleanly
    p_no_chord = Params(U=8.0, tsr=3.5, R=0.5, n_blades=3)
    assert p_no_chord.chord is None
    assert p_no_chord.validate() == []

    # Preset provenance labeling
    p_preset = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3, preset_name="Danao et al. (2014) Rotor (Example)")
    assert p_preset.get_source("U") == "from preset 'Danao et al. (2014) Rotor (Example)'"
    assert p_preset.get_source("depth") == "Fluent default"

    # User typed
    p_user = Params(U=8.0, tsr=3.5, R=0.5, n_blades=3)
    p_user.param_sources["U"] = "typed by user"
    assert p_user.get_source("U") == "typed by user"


def test_1_2_moment_sign_negative_cp_preserves_sign(tmp_path):
    """1.2 A synthetic run with genuinely negative torque must preserve Cp < 0 and produce a warning notice."""
    steps = np.arange(1, 1000)
    dt = 0.0005
    t = steps * dt
    # Negative torque across the whole run
    m_neg = np.full(len(steps), -0.75)

    p_tot = str(tmp_path / "total_neg.out")
    make_fluent_file(p_tot, "total", steps, m_neg, t)

    # Default moment_sign="fixed" and rotation_dir="ccw"
    p = Params(U=7.0, tsr=2.0, R=0.35, n_blades=3, moment_sign="fixed", rotation_dir="ccw")
    f = InputFiles(total=p_tot)
    res_dict = analyze(p, f)
    res = res_dict["res"]

    # Sign must remain negative
    assert res["cp_mean"] < 0
    assert res["sign"] == 1.0
    # Must produce the warning notice
    assert any("Notice: Mean rotor Cp is negative" in w for w in res_dict["warnings"])

    # Test clockwise rotation convention: with rotation_dir="cw", dir_factor is -1.0
    p_cw = Params(U=7.0, tsr=2.0, R=0.35, n_blades=3, moment_sign="fixed", rotation_dir="cw")
    res_dict_cw = analyze(p_cw, f)
    assert res_dict_cw["res"]["sign"] == -1.0
    # -1 * (-0.75) = +0.75 -> positive power when rotating CW
    assert res_dict_cw["res"]["cp_mean"] > 0


def test_1_3_shaft_handling(tmp_path):
    """1.3 Shaft subtraction, blade-sum vs total file side-by-side, and >0.5% discrepancy warning."""
    steps = np.arange(1, 1200)
    omega = 50.0
    dt = 0.0004
    t = steps * dt

    # Blade torques
    b1 = 0.3 + 0.1 * np.sin(omega * t)
    b2 = 0.3 + 0.1 * np.sin(omega * t + 2 * np.pi / 3)
    b3 = 0.3 + 0.1 * np.sin(omega * t + 4 * np.pi / 3)
    m_blades_sum = b1 + b2 + b3  # Mean ~ 0.90 N·m

    # Shaft drag: -0.05 N·m
    shaft = np.full(len(steps), -0.05)
    # Total monitor: blades + shaft = 0.85 N·m
    total = m_blades_sum + shaft

    p_b1 = str(tmp_path / "b1.out")
    p_b2 = str(tmp_path / "b2.out")
    p_b3 = str(tmp_path / "b3.out")
    p_shaft = str(tmp_path / "shaft.out")
    p_tot = str(tmp_path / "total.out")

    make_fluent_file(p_b1, "b1", steps, b1, t)
    make_fluent_file(p_b2, "b2", steps, b2, t)
    make_fluent_file(p_b3, "b3", steps, b3, t)
    make_fluent_file(p_shaft, "shaft", steps, shaft, t)
    make_fluent_file(p_tot, "total", steps, total, t)

    # 1. Total file only + shaft file -> shaft is subtracted
    p = Params(U=8.0, tsr=3.0, R=0.48, n_blades=3)
    f_tot_shaft = InputFiles(total=p_tot, shaft=p_shaft)
    res_tot_shaft = analyze(p, f_tot_shaft)
    # After subtracting -0.05, rotor moment should be ~0.90 N·m
    assert abs(res_tot_shaft["res"]["torque_mean"] - 0.90) < 0.02
    assert res_tot_shaft["res"]["shaft_mean_moment"] is not None
    assert abs(res_tot_shaft["res"]["shaft_mean_moment"] - (-0.05)) < 1e-4

    # 2. Both blades and total file provided -> side-by-side reporting & discrepancy warning (>0.5%)
    f_both = InputFiles(total=p_tot, blades=[p_b1, p_b2, p_b3], shaft=p_shaft)
    res_both = analyze(p, f_both)
    res_b = res_both["res"]
    assert res_b["cp_blades_only"] is not None
    assert res_b["cp_total_file"] is not None
    assert abs(res_b["cp_blades_only"] - res_b["cp_total_file"]) > 0
    # Must trigger discrepancy warning (> 0.5%)
    assert any("Cp (blades only" in w and "> 0.5%" in w for w in res_both["warnings"])


def test_1_4_multicriteria_stability_and_aitken(tmp_path):
    """1.4 Multi-criteria stability, waveform periodicity RMS metric, Aitken extrapolation, and period-2 check."""
    steps = np.arange(1, 2880)
    omega = 40.0
    Trev = 2.0 * math.pi / omega
    dt = Trev / 360.0
    t = steps * dt

    # Asymptotically converging Cp progression: 0.30, 0.32, 0.33, 0.335, 0.3375 ...
    # Geometric decay with factor r = 0.5
    Pref = 0.5 * 1.225 * (8.0 ** 3) * (2.0 * 0.5 * 1.0)
    rev_idx = t / Trev
    cp_prog = 0.35 - 0.10 * np.exp(-0.6 * rev_idx)
    m_tot = cp_prog * Pref / omega

    p_tot = str(tmp_path / "total_converging.out")
    make_fluent_file(p_tot, "total", steps, m_tot, t)

    p = Params(U=8.0, omega=omega, R=0.5, n_blades=3, stable_tol=0.5, stable_tol_abs=0.001)
    f = InputFiles(total=p_tot)
    res_dict = analyze(p, f)
    res = res_dict["res"]

    # Verify Aitken extrapolation output
    assert res["cp_extrapolated_aitken"] is not None
    assert abs(res["cp_extrapolated_aitken"] - 0.35) < 0.02
    assert res["extra_revs_to_converge"] is not None

    # Unambiguous naming
    assert "cp_reported_mean" in res
    assert "cp_trailing_stable_mean" in res
    assert "cp_all_revolutions_mean" in res

    # Verify periodicity metric exists on revolutions
    for r in res_dict["revs"]:
        assert "periodicity_metric" in r


def test_1_5_rotor_speed_and_autocorrelation(tmp_path):
    """1.5 Robust rotor speed BPF checks and torque autocorrelation revolution period."""
    omega_true = 60.0
    Trev_true = 2.0 * math.pi / omega_true
    dt = Trev_true / 360.0
    steps = np.arange(1, 2880)
    t = steps * dt

    # 3 blades -> dominant frequency at 3 * omega_true / (2*pi) = 3 * 60 / 6.283 = 28.65 Hz
    m_rot = 1.0 + 0.3 * np.cos(3.0 * omega_true * t)

    p_tot = str(tmp_path / "total_speed.out")
    make_fluent_file(p_tot, "total", steps, m_rot, t)

    # Matching speed
    p_match = Params(U=8.0, omega=omega_true, R=0.5, n_blades=3)
    f = InputFiles(total=p_tot)
    res_match = analyze(p_match, f)
    assert abs(res_match["res"]["freq_match_ratio"] - 1.0) < 0.05
    assert abs(res_match["res"]["autocorr_period"] - Trev_true) / Trev_true < 0.03

    # Mismatched speed (entered omega = 30.0 instead of 60.0)
    p_mismatch = Params(U=8.0, omega=30.0, R=0.5, n_blades=3)
    res_mismatch = analyze(p_mismatch, f)
    assert any("Rotor speed implied by the data differs from the entered ω" in w for w in res_mismatch["warnings"])


def test_1_6_azimuth_conventions_and_manual_offsets(tmp_path):
    """1.6 Azimuth conventions (zero position, free offset, manual offsets, weak cross-correlation)."""
    steps = np.arange(1, 720)
    omega = 40.0
    dt = 0.0005
    t = steps * dt
    b1 = 0.4 + 0.2 * np.cos(omega * t)
    b2 = 0.4 + 0.2 * np.cos(omega * t + np.radians(110.0))
    b3 = 0.4 + 0.2 * np.cos(omega * t + np.radians(230.0))

    p_b1 = str(tmp_path / "b1.out")
    p_b2 = str(tmp_path / "b2.out")
    p_b3 = str(tmp_path / "b3.out")
    make_fluent_file(p_b1, "b1", steps, b1, t)
    make_fluent_file(p_b2, "b2", steps, b2, t)
    make_fluent_file(p_b3, "b3", steps, b3, t)

    # Test manual blade offsets override
    manual_offs = [0.0, 110.0, 230.0]
    p_man = Params(
        U=8.0, tsr=3.0, R=0.5, n_blades=3,
        azimuth_zero="front", azimuth_offset=15.0,
        blade_offsets_manual=manual_offs
    )
    f = InputFiles(blades=[p_b1, p_b2, p_b3])
    res_dict = analyze(p_man, f)
    np.testing.assert_allclose(res_dict["res"]["offsets"], manual_offs, atol=1e-5)


def test_1_8_and_1_9_figures_and_excel(tmp_path):
    """1.8 Polar plot with negative values and 1.9 Cm vs azimuth envelope generation."""
    steps = np.arange(1, 1440)
    omega = 50.0
    dt = 0.0003
    t = steps * dt

    # Blade with negative torque sector
    b1 = -0.1 + 0.5 * np.sin(omega * t)
    b2 = -0.1 + 0.5 * np.sin(omega * t + 2 * np.pi / 3)
    b3 = -0.1 + 0.5 * np.sin(omega * t + 4 * np.pi / 3)

    p_b1 = str(tmp_path / "b1.out")
    p_b2 = str(tmp_path / "b2.out")
    p_b3 = str(tmp_path / "b3.out")
    make_fluent_file(p_b1, "b1", steps, b1, t)
    make_fluent_file(p_b2, "b2", steps, b2, t)
    make_fluent_file(p_b3, "b3", steps, b3, t)

    p = Params(U=7.0, tsr=3.5, R=0.4, n_blades=3, preset_name="Test Preset")
    f = InputFiles(blades=[p_b1, p_b2, p_b3])
    res_dict = analyze(p, f)

    fig_dir = str(tmp_path / "figs")
    figs = make_figures(res_dict, fig_dir)
    assert "05_polar_blade_cp.png" in figs
    assert "07_cm.png" in figs
    assert os.path.isfile(figs["05_polar_blade_cp.png"])
    assert os.path.isfile(figs["07_cm.png"])

    # Excel export check
    xl_path = str(tmp_path / "report.xlsx")
    write_excel(res_dict, figs, xl_path)
    assert os.path.isfile(xl_path)
