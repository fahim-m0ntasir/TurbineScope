# -*- coding: utf-8 -*-
"""Regression tests for visual, logic, and engine bug fixes across TurbineScope_Qt."""
from __future__ import annotations

import math
import numpy as np
import openpyxl


def test_parser_role_extension_and_blade_forcex():
    from vawt_post.engine.parser import parse_report_filename_role

    # '.txt' extension must never trigger an 'x' match in force_shaft.txt
    assert parse_report_filename_role("force_shaft.txt", 3)[0] == "unknown"
    assert parse_report_filename_role("force_shaftx-rfile.out", 3)[0] == "shaft_fx"
    assert parse_report_filename_role("force_shafty-rfile.out", 3)[0] == "shaft_fy"
    assert parse_report_filename_role("forcex1.out", 3)[:2] == ("blade_fx", 1)


def test_cw_normal_force_sign():
    from vawt_post.engine.forces import compute_normal_tangential_coefficients

    # For CW with theta=90° (blade at (-R, 0) into-wind), outward normal is (-1, 0) = -X.
    # A +X force (Fx=+1, Fy=0) points inward -> negative Cn.
    cn, ctan = compute_normal_tangential_coefficients(
        np.array([1.0]), np.array([0.0]), np.array([90.0]),
        rho=1.0, U=1.0, chord=2.0, depth=1.0, rotation_dir="cw"
    )
    assert math.isclose(float(cn[0]), -1.0, rel_tol=1e-6)
    assert abs(float(ctan[0])) < 1e-6

    # Zero dynamic pressure guard should return zeros cleanly without NaN/Inf
    cn0, ct0 = compute_normal_tangential_coefficients(
        np.array([1.0]), np.array([1.0]), np.array([45.0]),
        rho=1.0, U=0.0, chord=0.1, depth=1.0, rotation_dir="ccw"
    )
    assert np.all(cn0 == 0.0) and np.all(ct0 == 0.0)


def test_multigrid_four_level_gci_mapping():
    from vawt_post.engine.gci_engine import GridLevelData, compute_multi_grid_study

    grids = [
        GridLevelData(name="G1", system_id="FFF", cell_count=200000, phi=0.350),
        GridLevelData(name="G2", system_id="FFF-1", cell_count=100000, phi=0.340),
        GridLevelData(name="G3", system_id="FFF-2", cell_count=50000, phi=0.320),
        GridLevelData(name="G4", system_id="FFF-3", cell_count=25000, phi=0.280),
    ]
    report = compute_multi_grid_study(grids, dimension=2, override_checks=True)
    assert len(report.triplet_reports) == 2
    assert report._grid_gci_value(0) == report.triplet_reports[0].gci_fine_21_pct
    assert report._grid_gci_value(1) == report.triplet_reports[0].gci_medium_32_pct
    assert report._grid_gci_value(2) == report.triplet_reports[1].gci_medium_32_pct
    assert report._grid_gci_value(3) is None
    table = report.to_formatted_table()
    assert f"±{report.triplet_reports[1].gci_medium_32_pct:.2f}%" in table


def test_unstable_final_revolution_reports_zero_stable(tmp_path):
    from vawt_post.engine.config import Params, InputFiles
    from vawt_post.engine.analysis import analyze

    omega = 10.0
    Trev = 2.0 * math.pi / omega
    dt = Trev / 100.0
    steps = np.arange(0, 305)
    t = steps * dt
    # Constant torque for revs 1-2, huge jump in rev 3 -> final step unstable
    vals = np.where(t < 2.0 * Trev, 1.0, 5.0)
    tot_path = tmp_path / "moment-rfile.out"
    with open(tot_path, "w", encoding="utf-8") as fh:
        fh.write('("Time Step" "moment" "flow-time")\n')
        for s, v, ti in zip(steps, vals, t):
            fh.write(f"{s} {v:.6f} {ti:.8f}\n")

    p = Params(U=5.0, omega=omega, R=0.5, chord=0.06, n_blades=1, stable_tol=1.0, n_stable=2)
    f = InputFiles(total=str(tot_path))
    out = analyze(p, f)
    assert out["res"]["stable_n"] == 0
    assert out["res"]["is_stable"] is False


def test_excel_total_only_formula_col_and_quartile_bounds(tmp_path):
    from vawt_post.engine.config import Params, InputFiles
    from vawt_post.engine.analysis import analyze
    from vawt_post.engine.excel import write_excel

    omega = 40.0
    Trev = 2.0 * math.pi / omega
    dt = Trev / 60.0
    steps = np.arange(0, 190)
    t = steps * dt
    vals = 2.5 + 0.5 * np.sin(omega * t)
    tot_path = tmp_path / "moment-rfile.out"
    with open(tot_path, "w", encoding="utf-8") as fh:
        fh.write('("Time Step" "moment" "flow-time")\n')
        for s, v, ti in zip(steps, vals, t):
            fh.write(f"{s} {v:.6f} {ti:.8f}\n")

    p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=1, stable_tol=5.0, n_stable=2)
    out = analyze(p, InputFiles(total=str(tot_path)))
    # Total-only run has have_blades=False -> nb=0 in write_excel
    assert out["data"]["have_blades"] is False

    out_xlsx = tmp_path / "test_nb0.xlsx"
    write_excel(out, {}, str(out_xlsx), log=lambda *_: None)
    wb = openpyxl.load_workbook(out_xlsx, data_only=False)
    try:
        wpd = wb["Processed"]
        # Rotor moment is in Column E (index 5), never Column D (θ blade 1)
        assert wpd["E1"].value == "Rotor moment (N·m)"
        assert wpd["F2"].value.startswith("=(E2*")
    finally:
        wb.close()
