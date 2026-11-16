# -*- coding: utf-8 -*-
"""
Tests for edge cases: total-only, blades-only, fx without fy, negative sign, etc.
"""
import pytest
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.analysis import analyze
from vawt_post.engine.synthetic import make_fluent_file


def test_edge_case_total_file_only(tmp_path):
    """When only the total moment file is given, blade-resolved outputs are skipped."""
    steps = np.arange(1, 1000)
    omega = 80.0
    dt = 0.0002
    t = steps * dt
    m_tot = 0.5 + 0.1 * np.sin(omega * t)

    p_tot = str(tmp_path / "total.out")
    make_fluent_file(p_tot, "total", steps, m_tot, t)

    p = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3)
    f = InputFiles(total=p_tot)
    res = analyze(p, f)
    assert not res["data"]["have_blades"]
    assert res["res"]["n_full"] >= 2
    assert res["res"]["cp_mean"] > 0


def test_edge_case_blade_files_only(tmp_path):
    """When only blade files are given, rotor torque is the sum of blade moments."""
    steps = np.arange(1, 1200)
    omega = 80.0
    dt = 0.0002
    t = steps * dt
    b1 = 0.2 + 0.1 * np.sin(omega * t)
    b2 = 0.2 + 0.1 * np.sin(omega * t + 2 * np.pi / 3)
    b3 = 0.2 + 0.1 * np.sin(omega * t + 4 * np.pi / 3)

    p_b1 = str(tmp_path / "b1.out")
    p_b2 = str(tmp_path / "b2.out")
    p_b3 = str(tmp_path / "b3.out")

    make_fluent_file(p_b1, "b1", steps, b1, t)
    make_fluent_file(p_b2, "b2", steps, b2, t)
    make_fluent_file(p_b3, "b3", steps, b3, t)

    p = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3)
    f = InputFiles(blades=[p_b1, p_b2, p_b3])
    res = analyze(p, f)
    assert res["data"]["have_blades"]
    # Check rotor torque equals sum of blades within text-file formatting precision
    np.testing.assert_allclose(res["data"]["Mrot"], b1 + b2 + b3, atol=1e-5)


def test_edge_case_fx_without_fy(tmp_path):
    """Providing Fx without Fy should succeed and set Cy to None."""
    steps = np.arange(1, 1000)
    t = steps * 0.0002
    m_tot = np.full(len(steps), 0.5)
    fx = np.full(len(steps), 20.0)

    p_tot = str(tmp_path / "total.out")
    p_fx = str(tmp_path / "fx.out")
    make_fluent_file(p_tot, "total", steps, m_tot, t)
    make_fluent_file(p_fx, "fx", steps, fx, t)

    p = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3)
    f = InputFiles(total=p_tot, fx=p_fx)
    res = analyze(p, f)
    assert res["res"]["ct_mean"] is not None
    assert res["res"]["cy_mean"] is None


def test_edge_case_negative_moment_auto_flip(tmp_path):
    """When moment is negative and moment_sign is 'auto', sign follows direction (no auto-flip), preserves negative Cp, and warns."""
    steps = np.arange(1, 1000)
    t = steps * 0.0002
    m_neg = np.full(len(steps), -0.5)

    p_tot = str(tmp_path / "neg.out")
    make_fluent_file(p_tot, "total", steps, m_neg, t)

    p = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3, moment_sign="auto")
    f = InputFiles(total=p_tot)
    res = analyze(p, f)
    assert res["res"]["sign"] == 1.0
    assert res["res"]["cp_mean"] < 0
    assert any("negative" in w.lower() for w in res["warnings"])


def test_edge_case_fewer_than_two_revolutions_raises(tmp_path):
    """Having less than 2 revolutions raises a clear ValueError."""
    steps = np.arange(1, 50)
    t = steps * 0.0001
    p_tot = str(tmp_path / "short.out")
    make_fluent_file(p_tot, "total", steps, np.ones(len(steps)), t)

    p = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3)
    f = InputFiles(total=p_tot)
    with pytest.raises(ValueError, match="complete revolution"):
        analyze(p, f)


def test_edge_case_bin_width_not_dividing_360():
    """Bin width that does not divide 360 raises validation error."""
    p = Params(bin_deg=7.0)
    errors = p.validate()
    assert any("divide 360" in e for e in errors)
