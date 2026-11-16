# -*- coding: utf-8 -*-
"""
Tests for Multi-Stage Simulation Generator with Custom Azimuthal Shifts & Preview Views
"""
import os

from vawt_post.engine.config import Params
from vawt_post.engine.analysis import analyze
from vawt_post.engine.synthetic import SimulationStage, generate_multistage_dataset
from vawt_post.engine.plots import make_figures
from vawt_post.engine.excel import write_excel


def test_custom_azimuthal_shift_simulation(tmp_path):
    """Test generating and analyzing multi-stage dataset with arbitrary custom azimuthal shifts (e.g. 0.75°, 1.5°, 0.35°)."""
    out_dir = str(tmp_path / "custom_shifts")
    p = Params(U=7.0, tsr=4.0, R=0.35, chord=0.04, n_blades=3, bin_deg=1.0)

    stages = [
        SimulationStage(n_steps=240, deg_per_step=1.5, name="Stage 1 (1.5°/step)"),     # 1 rev
        SimulationStage(n_steps=480, deg_per_step=0.75, name="Stage 2 (0.75°/step)"),   # 1 rev
        SimulationStage(n_steps=960, deg_per_step=0.375, name="Stage 3 (0.375°/step)"), # 1 rev
    ]

    p_ret, f_ret, info = generate_multistage_dataset(
        out_dir=out_dir,
        params=p,
        stages=stages,
        target_cp_final=0.35,
        settling_revs=2,
        ct_target=1.02,
    )

    res_dict = analyze(p, f_ret)
    res = res_dict["res"]

    # Verify detected phases
    assert len(res["phases"]) == 3
    deg_detected = [round(ph["deg_per_step"], 3) for ph in res["phases"]]
    assert deg_detected == [1.5, 0.75, 0.375]

    # Verify complete revolutions
    assert res["n_full"] == 3

    # Verify target Cp convergence
    assert abs(res["cp_stable_mean"] - 0.35) < 0.05
    assert abs(res["ct_mean"] - 1.02) < 0.05

    # Verify figure rendering and excel generation work without issues
    fig_dir = str(tmp_path / "figures")
    figs = make_figures(res_dict, fig_dir)
    assert len(figs) == 18

    xl_path = str(tmp_path / "report.xlsx")
    write_excel(res_dict, figs, xl_path)
    assert os.path.isfile(xl_path)

    # Deliberate Excel regression check: verify Stages sheet is populated for multi-stage runs
    from openpyxl import load_workbook
    wb_test = load_workbook(xl_path)
    assert "Stages" in wb_test.sheetnames, "Stages sheet must be generated for multi-stage datasets"
    ws_stages = wb_test["Stages"]
    assert ws_stages.cell(row=3, column=1).value == "Stage #"
    assert ws_stages.cell(row=3, column=4).value == "dt (s)"
    assert ws_stages.cell(row=4, column=1).value == 1



def test_simulation_stage_revs_and_steps_resolution():
    """Verify SimulationStage resolves steps from n_revs correctly with arbitrary custom shifts."""
    omega = 80.0  # rad/s

    # 1. 4 revs at 0.75 deg/step -> 4 * (360 / 0.75) = 1920 steps
    s1 = SimulationStage(deg_per_step=0.75, n_revs=4.0)
    assert s1.resolve_n_steps(omega) == 1920

    # 2. 5 revs at 0.1 deg/step -> 5 * (360 / 0.1) = 18000 steps
    s2 = SimulationStage(deg_per_step=0.1, n_revs=5.0)
    assert s2.resolve_n_steps(omega) == 18000

    # 3. Direct steps overrides n_revs
    s3 = SimulationStage(n_steps=500, deg_per_step=0.5, n_revs=10.0)
    assert s3.resolve_n_steps(omega) == 500
