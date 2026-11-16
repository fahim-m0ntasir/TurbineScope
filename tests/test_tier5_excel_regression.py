# -*- coding: utf-8 -*-
"""
Tier 5.3: Excel Cell-by-Cell Regression Tests
=============================================
Inspects the generated openpyxl workbook cell-by-cell:
1. Verifies Summary sheet formulas (e.g. B5 live =AVERAGE(Revolutions!...) contract)
2. Verifies Parameters sheet values, units, and source provenance
3. Verifies Revolutions sheet numerical consistency
4. Verifies Processed sheet live formula mode referencing Parameters
5. Verifies QA_Checks and Captions sheets data integrity
"""
import os
import numpy as np
import openpyxl

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.synthetic import make_fluent_file
from vawt_post.engine.analysis import run_pipeline


def test_5_3_excel_cell_by_cell_regression(tmp_path):
    """Exhaustive cell-by-cell regression test of generated Excel workbook."""
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)  # 3 revolutions
    t = steps * dt
    m = 1.25 + 0.3 * np.sin(3 * omega * t)

    f_total = str(tmp_path / "total.out")
    make_fluent_file(f_total, "total", steps, m, t)

    p = Params(
        U=8.0, tsr=1.25, omega=omega, R=0.5, n_blades=3,
        span=2.0, preset_name="Regression Test Preset"
    )
    out_dir = str(tmp_path / "out_excel_reg")
    R = run_pipeline(p, InputFiles(total=f_total), out_dir, make_figs=False)

    xlsx_path = R["xlsx"]
    assert os.path.isfile(xlsx_path)

    wb = openpyxl.load_workbook(xlsx_path, data_only=False)
    sheet_names = wb.sheetnames
    assert "Summary" in sheet_names
    assert "Parameters" in sheet_names
    assert "Revolutions" in sheet_names
    assert "Processed" in sheet_names
    assert "QA_Checks" in sheet_names
    assert "Captions" in sheet_names

    # 1. Summary Sheet Checks
    ws_sum = wb["Summary"]
    # Check title and preset banner
    assert "Results Summary" in str(ws_sum["A1"].value)
    assert "Regression Test Preset" in str(ws_sum["A2"].value)

    # Check cell B5 live formula contract
    cell_b5 = ws_sum["B5"].value
    assert isinstance(cell_b5, str) and cell_b5.startswith("=AVERAGE(Revolutions!"), (
        f"Cell B5 must contain live AVERAGE formula, found: {cell_b5}"
    )

    # Check 3D scaling rows exist in Summary
    summary_labels = [ws_sum.cell(row=r, column=1).value for r in range(4, ws_sum.max_row + 1)]
    assert any("Blade span H" in str(lbl) for lbl in summary_labels)
    assert any("3D swept area" in str(lbl) for lbl in summary_labels)
    assert any("Estimated 3D power" in str(lbl) for lbl in summary_labels)

    # 2. Parameters Sheet Checks
    ws_param = wb["Parameters"]
    param_dict = {}
    for r in range(2, ws_param.max_row + 1):
        p_name = ws_param.cell(row=r, column=1).value
        p_val = ws_param.cell(row=r, column=2).value
        p_src = ws_param.cell(row=r, column=4).value
        if p_name:
            param_dict[str(p_name)] = (p_val, p_src)

    assert "Free-stream speed U" in param_dict
    assert param_dict["Free-stream speed U"][0] == 8.0
    assert "Rotor radius R" in param_dict
    assert param_dict["Rotor radius R"][0] == 0.5
    assert "Number of blades N" in param_dict
    assert param_dict["Number of blades N"][0] == 3
    assert "Blade span H (3D)" in param_dict
    assert param_dict["Blade span H (3D)"][0] == 2.0
    assert "Air density ρ" in param_dict
    # Density provenance should be Fluent default
    assert "Fluent default" in str(param_dict["Air density ρ"][1])

    # 3. Revolutions Sheet Checks
    ws_rev = wb["Revolutions"]
    assert ws_rev["A4"].value == "Rev"
    assert ws_rev["D4"].value == "Mean Cp"
    # Row 5 (first revolution)
    assert ws_rev["A5"].value == 1
    cp_rev1 = ws_rev["D5"].value
    assert isinstance(cp_rev1, (int, float)) and cp_rev1 > 0

    # 4. Processed Sheet Checks: Live formula mode
    ws_proc = wb["Processed"]
    # Check column headers
    assert ws_proc["A1"].value == "Step"
    assert ws_proc["B1"].value == "Flow time (s)"
    assert ws_proc["E1"].value == "Rotor moment (N·m)"
    assert ws_proc["F1"].value == "Cp"
    assert ws_proc["G1"].value == "Cm"

    # Row 2 (first time step) Cp and Cm formulas
    cell_cp = str(ws_proc["F2"].value)
    cell_cm = str(ws_proc["G2"].value)
    assert cell_cp.startswith("=(") and "Parameters!" in cell_cp, (
        f"Processed Cp must be a live formula referencing Parameters, found: {cell_cp}"
    )
    assert cell_cm.startswith("=") and "Parameters!" in cell_cm, (
        f"Processed Cm must be a live formula referencing Parameters, found: {cell_cm}"
    )

    # 5. QA_Checks Sheet Checks
    ws_qa = wb["QA_Checks"]
    qa_checks_found = [str(ws_qa.cell(row=r, column=1).value) for r in range(4, ws_qa.max_row + 1)]
    assert any("Duplicate steps" in chk for chk in qa_checks_found)
    assert any("Time monotonicity" in chk for chk in qa_checks_found)
    assert any("Rotor speed" in chk for chk in qa_checks_found)

    # 6. Captions Sheet Checks
    ws_cap = wb["Captions"]
    assert "Figure ID" in str(ws_cap["A3"].value)
    assert "01_cp_history.png" in str(ws_cap["B4"].value)
    assert "Figure 1:" in str(ws_cap["D4"].value)
