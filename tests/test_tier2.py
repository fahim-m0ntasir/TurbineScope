# -*- coding: utf-8 -*-
"""
Tier 2 Tests: Input Robustness, QA Diagnostics, Case Metadata & Reproducibility
================================================================================
Verifies:
- Multi-column Fluent report detection, inspection, and column selection.
- Vectorized parsing and decimation.
- File SHA-256 cryptographic checksums & results.json export.
- Complete QA table checks (monotonicity, duplicates, gaps, dt resolution <=1° / >1° / >2°, azimuth interpolation).
- Case metadata fields persistence & Excel formatting (QA_Checks, Warnings, Parameters metadata).
- Pipeline cancellation via threading Event token.
- Raw time series CSV export.
"""
import os
import json
import threading
import pytest
import numpy as np
import openpyxl

from vawt_post.engine.config import Params, InputFiles, CaseMetadata
from vawt_post.engine.parser import (
    read_fluent_report, inspect_fluent_report, compute_file_sha256
)
from vawt_post.engine.analysis import analyze, run_pipeline
from vawt_post.engine.synthetic import make_fluent_file


def test_2_1_multi_column_fluent_file(tmp_path):
    """Test multi-column Fluent file inspection and column selection."""
    p_multi = tmp_path / "multi_report.out"
    content = (
        '("Time Step" "flow-time" "cd" "cl" "cm")\n'
        '1 0.001 0.15 0.85 0.045\n'
        '2 0.002 0.16 0.88 0.048\n'
        '3 0.003 0.14 0.82 0.042\n'
        '4 0.004 0.15 0.84 0.044\n'
    )
    p_multi.write_text(content, encoding="utf-8")

    # Inspect
    info = inspect_fluent_report(str(p_multi))
    assert info["is_multi_column"] is True
    assert info["columns"] == ["Time Step", "flow-time", "cd", "cl", "cm"]
    assert len(info["data_columns"]) == 3
    assert info["row_count"] == 4
    assert len(info["sha256"]) == 64

    # Read specific column by name
    res_cm = read_fluent_report(str(p_multi), column_name="cm")
    assert res_cm["selected_column"] == "cm"
    np.testing.assert_allclose(res_cm["value"], [0.045, 0.048, 0.042, 0.044])

    # Read specific column by index
    res_cl = read_fluent_report(str(p_multi), column_idx=3)
    assert res_cl["selected_column"] == "cl"
    np.testing.assert_allclose(res_cl["value"], [0.85, 0.88, 0.82, 0.84])

    # Test decimation (decimate=2 keeps steps 1 and 3)
    res_deci = read_fluent_report(str(p_multi), column_name="cm", decimate=2)
    np.testing.assert_array_equal(res_deci["step"], [1, 3])
    np.testing.assert_allclose(res_deci["value"], [0.045, 0.042])


def test_2_2_qa_checks_diagnostics(tmp_path):
    """Test comprehensive QA checks table for duplicate steps, non-monotonic time, and degrees per step."""
    # Run 1: High quality run with <= 1.0 deg/step
    # ω = 20 rad/s, Trev = 2π/20 = 0.314159 s
    # 720 steps per rev -> dt = Trev / 720 = 0.00043633 s -> Δθ = 0.5 deg/step
    omega = 20.0
    dt_fine = (2.0 * np.pi / omega) / 720.0
    steps_fine = np.arange(1, 2162)
    t_fine = steps_fine * dt_fine
    m_fine = 1.2 + 0.3 * np.sin(omega * t_fine)

    f_fine = str(tmp_path / "fine_run.out")
    make_fluent_file(f_fine, "total", steps_fine, m_fine, t_fine)

    p_fine = Params(
        U=8.0, tsr=1.5, R=0.6, n_blades=3,
        metadata=CaseMetadata(mesh_cells=350000, turb_model="k-omega SST", moment_center_confirmed=True)
    )
    r_fine = analyze(p_fine, InputFiles(total=f_fine))
    qa_fine = {q["check"]: q for q in r_fine["qa_checks"]}

    assert qa_fine["Time monotonicity"]["status"] == "PASS"
    assert qa_fine["Duplicate steps"]["status"] == "PASS"
    assert qa_fine["Step continuity"]["status"] == "PASS"
    assert qa_fine["Steps per revolution"]["status"] == "PASS"
    assert qa_fine["Azimuth step resolution"]["status"] == "PASS"
    assert "0.50°" in qa_fine["Azimuth step resolution"]["value"]
    assert qa_fine["Moment center alignment"]["status"] == "PASS"

    # Run 2: Coarse time step with Δθ > 2.0 deg/step -> FAIL / strong warning
    # 100 steps per rev -> dt = Trev / 100 -> Δθ = 3.6 deg/step
    dt_coarse = (2.0 * np.pi / omega) / 100.0
    steps_coarse = np.arange(1, 402)
    t_coarse = steps_coarse * dt_coarse
    m_coarse = 1.2 + 0.3 * np.sin(omega * t_coarse)

    f_coarse = str(tmp_path / "coarse_run.out")
    make_fluent_file(f_coarse, "total", steps_coarse, m_coarse, t_coarse)

    p_coarse = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    r_coarse = analyze(p_coarse, InputFiles(total=f_coarse))
    qa_coarse = {q["check"]: q for q in r_coarse["qa_checks"]}

    assert qa_coarse["Azimuth step resolution"]["status"] == "FAIL"
    assert "3.60°" in qa_coarse["Azimuth step resolution"]["value"]
    assert any("Strong Warning: Δθ" in w for w in r_coarse["warnings"])

    # Run 3: Warn on 1.0 < Δθ <= 2.0 deg/step (e.g. 240 steps/rev -> 1.5 deg/step)
    dt_mid = (2.0 * np.pi / omega) / 240.0
    steps_mid = np.arange(1, 723)
    t_mid = steps_mid * dt_mid
    m_mid = 1.2 + 0.3 * np.sin(omega * t_mid)

    f_mid = str(tmp_path / "mid_run.out")
    make_fluent_file(f_mid, "total", steps_mid, m_mid, t_mid)

    p_mid = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    r_mid = analyze(p_mid, InputFiles(total=f_mid))
    qa_mid = {q["check"]: q for q in r_mid["qa_checks"]}
    assert qa_mid["Azimuth step resolution"]["status"] == "WARN"
    assert "1.50°" in qa_mid["Azimuth step resolution"]["value"]


def test_2_3_reproducibility_and_results_json(tmp_path):
    """Test SHA-256 calculation, results.json export, and time series CSV export."""
    steps = np.arange(1, 800)
    omega = 25.0
    dt = (2.0 * np.pi / omega) / 360.0
    t = steps * dt
    m = 1.0 + 0.2 * np.cos(omega * t)

    f_tot = str(tmp_path / "run_total.out")
    make_fluent_file(f_tot, "total", steps, m, t)

    expected_sha = compute_file_sha256(f_tot)
    assert len(expected_sha) == 64

    meta = CaseMetadata(
        mesh_cells=280000,
        turb_model="Transition SST (gamma-Re_theta)",
        time_step_s=dt,
        iter_per_step=25,
        residual_target="1e-6",
        domain_size="35R x 25R",
        interface_type="Sliding Mesh",
        moment_center_confirmed=True
    )
    p = Params(
        U=9.0, tsr=3.5, R=0.5, n_blades=3,
        preset_name="Validation Mesh Run",
        metadata=meta
    )
    f = InputFiles(total=f_tot)

    outdir = str(tmp_path / "output_test")
    R = run_pipeline(p, f, outdir, prefix="AuditTest", make_figs=False, export_excel=False)

    # Verify results.json exists and is valid
    results_json_path = os.path.join(outdir, "results.json")
    assert os.path.isfile(results_json_path)

    with open(results_json_path, "r", encoding="utf-8") as fh:
        audit = json.load(fh)

    assert audit["app"]["name"] == "VAWT CFD Post-Processor"
    assert audit["app"]["version"] == "2.0.0"
    assert audit["parameters"]["U"] == 9.0
    assert audit["case_metadata"]["mesh_cells"] == 280000
    assert audit["case_metadata"]["turb_model"] == "Transition SST (gamma-Re_theta)"
    assert audit["input_files"]["total"]["sha256"] == expected_sha
    assert "summary_kpis" in audit
    assert "qa_checks" in audit

    # Verify time-series CSV export exists
    csv_path = R["csv_timeseries"]
    assert os.path.isfile(csv_path)
    with open(csv_path, "r", encoding="utf-8") as fh:
        header_line = fh.readline().strip()
    assert "Step,Flow_time_s,Revolution,Theta_b1_deg,Rotor_moment_Nm,Cp,Cm" in header_line


def test_2_4_excel_qa_and_metadata_sheets(tmp_path):
    """Test Excel workbook generation with QA_Checks, Parameters metadata, and Warnings sheets."""
    steps = np.arange(1, 800)
    omega = 30.0
    dt = (2.0 * np.pi / omega) / 360.0
    t = steps * dt
    m = 0.8 + 0.15 * np.sin(omega * t)

    f_tot = str(tmp_path / "excel_run.out")
    make_fluent_file(f_tot, "total", steps, m, t)

    meta = CaseMetadata(
        mesh_cells=150000,
        turb_model="k-omega SST",
        time_step_s=dt,
        domain_size="25R x 18R",
        moment_center_confirmed=False  # triggers QA warning
    )
    p = Params(U=7.0, tsr=3.0, R=0.4, n_blades=3, metadata=meta)
    f = InputFiles(total=f_tot)

    outdir = str(tmp_path / "excel_output")
    R = run_pipeline(p, f, outdir, make_figs=False)
    xlsx_path = R["xlsx"]
    assert os.path.isfile(xlsx_path)

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    # 1. QA_Checks sheet
    assert "QA_Checks" in wb.sheetnames
    ws_qa = wb["QA_Checks"]
    assert ws_qa["A4"].value == "Check Name"
    assert ws_qa["B4"].value == "Status"
    # Find Moment center alignment check row
    found_moment_check = False
    for r in range(5, 20):
        if ws_qa.cell(row=r, column=1).value == "Moment center alignment":
            assert ws_qa.cell(row=r, column=2).value == "WARN"
            found_moment_check = True
            break
    assert found_moment_check is True

    # 2. Parameters sheet: Metadata and Checksum
    ws_param = wb["Parameters"]
    param_text = [str(ws_param.cell(row=r, column=1).value) for r in range(1, 45)]
    assert any("CFD Case & Numerical Setup Metadata" in txt for txt in param_text)
    assert any("Mesh cell count" in txt for txt in param_text)
    assert any("SHA-256 Checksum" in str(ws_param.cell(row=r, column=3).value) for r in range(1, 45))

    # 3. Warnings sheet
    assert "Warnings" in wb.sheetnames
    ws_warn = wb["Warnings"]
    assert ws_warn["B3"].value == "Severity"


def test_2_5_pipeline_cancellation(tmp_path):
    """Test that setting cancel_token immediately halts the pipeline."""
    steps = np.arange(1, 400)
    t = steps * 0.001
    m = np.ones_like(t)

    f_tot = str(tmp_path / "cancel_run.out")
    make_fluent_file(f_tot, "total", steps, m, t)

    p = Params(U=8.0, tsr=3.0, R=0.5, n_blades=3)
    f = InputFiles(total=f_tot)

    cancel_evt = threading.Event()
    cancel_evt.set()  # Pre-trigger cancellation

    with pytest.raises(InterruptedError, match="Analysis cancelled by user"):
        run_pipeline(p, f, str(tmp_path / "cancel_out"), cancel_token=cancel_evt)
