# -*- coding: utf-8 -*-
"""
Tests for Tier 3.3 (Run Comparison & GCI Verification) and Tier 3.1 (Batch Mode & Cp-Lambda Curves)
===================================================================================================
"""
import os
import numpy as np
import openpyxl

from vawt_post.engine.config import Params, InputFiles, CaseMetadata
from vawt_post.engine.synthetic import make_fluent_file
from vawt_post.engine.analysis import run_pipeline
from vawt_post.engine.comparison import (
    load_run, compare_runs, calculate_gci, plot_comparison_figures,
    plot_gci_extrapolation, export_comparison_excel
)
from vawt_post.engine.batch import (
    natural_cubic_spline, run_batch_analysis
)


def test_3_3_run_comparison_and_gci(tmp_path):
    """3.3 Test run comparison, case metadata diffing, and GCI calculation from 3 grid levels."""
    # Generate 3 runs with different mesh cell counts (fine=500k, medium=250k, coarse=125k)
    # Target Cp values converging monotonically: coarse=0.320, medium=0.345, fine=0.355
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)
    t = steps * dt

    runs_data = []
    cell_counts = [500000, 250000, 125000]
    m_bases = [1.25, 1.18, 1.05]

    for i, (cells, m_base) in enumerate(zip(cell_counts, m_bases)):
        m = m_base + 0.25 * np.sin(omega * t)
        fpath = str(tmp_path / f"mesh_{cells}.out")
        make_fluent_file(fpath, "total", steps, m, t)

        meta = CaseMetadata(
            mesh_cells=cells,
            turb_model="k-omega SST",
            time_step_s=dt,
            domain_size="30R x 20R",
            moment_center_confirmed=True
        )
        p = Params(
            U=8.0, tsr=3.0, R=0.5, n_blades=3,
            preset_name=f"Grid Level {i + 1} ({cells // 1000}k)",
            metadata=meta
        )
        out_dir = str(tmp_path / f"out_run_{cells}")
        run_pipeline(p, InputFiles(total=fpath), out_dir, prefix=f"run_{cells}", make_figs=False, export_excel=False)
        runs_data.append(load_run(out_dir))

    # 1. Test compare_runs
    comp = compare_runs(runs_data)
    assert len(comp["runs"]) == 3
    assert len(comp["kpi_table"]) > 0

    # Verify metadata diff highlights mesh_cells as different
    diff_fields = {item["field"]: item for item in comp["meta_diff"]}
    assert "mesh_cells" in diff_fields
    assert diff_fields["mesh_cells"]["is_different"] is True
    assert diff_fields["turb_model"]["is_different"] is False

    # Verify azimuth difference metrics
    assert len(comp["az_metrics"]) == 2  # medium vs fine, coarse vs fine
    assert comp["az_metrics"][0]["rms_cm"] > 0

    # 2. Test calculate_gci
    gci = calculate_gci(runs_data, key_metric="cp_reported_mean", study_type="mesh")
    assert gci["status"] == "SUCCESS"
    assert gci["observed_order_p"] > 0
    assert gci["r21"] > 1.0
    assert gci["gci_fine_pct"] > 0
    assert gci["phi_ext"] > gci["phi1"]  # Richardson extrapolated value

    # 3. Test plotting and excel export
    comp_figs = plot_comparison_figures(comp, str(tmp_path / "comp_figs"))
    assert "cp_revolutions" in comp_figs and os.path.isfile(comp_figs["cp_revolutions"])
    assert "cm_azimuth_overlay" in comp_figs and os.path.isfile(comp_figs["cm_azimuth_overlay"])

    gci_fig = plot_gci_extrapolation(gci, str(tmp_path / "comp_figs" / "gci_extrapolation.png"))
    assert os.path.isfile(gci_fig)

    xlsx_comp = str(tmp_path / "comparison_results.xlsx")
    export_comparison_excel(comp, xlsx_comp, gci=gci)
    assert os.path.isfile(xlsx_comp)

    wb = openpyxl.load_workbook(xlsx_comp)
    assert "KPI_Comparison" in wb.sheetnames
    assert "Case_Metadata_Diff" in wb.sheetnames
    assert "GCI_Verification" in wb.sheetnames


def test_3_1_natural_cubic_spline():
    """Test pure NumPy natural cubic spline implementation on known test points."""
    x = np.array([1.0, 2.0, 3.0, 4.0])
    y = np.array([1.0, 4.0, 9.0, 16.0])  # y = x^2

    spline_fn = natural_cubic_spline(x, y)

    # Must interpolate exact knots
    np.testing.assert_allclose(spline_fn(x), y, atol=1e-12)

    # Midway evaluation
    y_mid = spline_fn(np.array([2.5]))
    assert 6.0 < y_mid[0] < 6.5  # close to 2.5^2 = 6.25


def test_3_1_batch_mode_cp_lambda(tmp_path):
    """3.1 Test multi-TSR batch processing, peak finding, and performance curves."""
    tsrs = [2.0, 3.0, 4.0, 5.0]
    # Synthetic torque values peaking around tsr = 3.5 - 4.0
    m_peaks = [0.80, 1.25, 1.20, 0.70]

    cases = []
    for tsr, m_base in zip(tsrs, m_peaks):
        omega = tsr * 8.0 / 0.5
        dt = (2.0 * np.pi / omega) / 360.0
        steps = np.arange(1, 1082)
        t = steps * dt
        m = m_base + 0.2 * np.sin(omega * t)

        fpath = str(tmp_path / f"tsr_{tsr:g}.out")
        make_fluent_file(fpath, "total", steps, m, t)

        p = Params(U=8.0, tsr=tsr, R=0.5, n_blades=3)
        cases.append({
            "tsr": tsr,
            "files": InputFiles(total=fpath),
            "params": p
        })

    outdir = str(tmp_path / "batch_output")
    batch_res = run_batch_analysis(cases, outdir, prefix="TSR_Study")

    assert len(batch_res["points"]) == 4
    assert 3.0 < batch_res["tsr_opt"] < 4.5
    assert batch_res["cp_max"] > 0

    # Verify generated outputs
    assert os.path.isfile(batch_res["figs"]["cp_vs_tsr"])
    assert os.path.isfile(batch_res["figs"]["cm_ct_vs_tsr"])
    assert os.path.isfile(batch_res["csv_table"])
    assert os.path.isfile(batch_res["xlsx"])
    assert os.path.isfile(batch_res["results_json"])

    # Verify Excel contents
    wb = openpyxl.load_workbook(batch_res["xlsx"])
    assert "Batch_Summary" in wb.sheetnames
    assert "Data_Points" in wb.sheetnames
    assert "Spline_Curve" in wb.sheetnames
    assert wb["Batch_Summary"]["B5"].value is not None
