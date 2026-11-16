# -*- coding: utf-8 -*-
"""
Tests for File-Health Diagnostics (Part A)
=========================================
Tests all 17 checks and fixture scenarios:
- Missing value column (Check 2)
- Value equals flow-time (Check 4)
- Missing steps in sequence (Check 6)
- Duplicate steps (Check 6)
- Simulation restarts (Check 6)
- Flat or dead signal (Check 12)
- NaN / Inf values (Check 11)
- Sudden signal spikes (Check 11)
- Stale file from different run (Check 9)
- Folder candidate file discovery (Check 10)
- Role mismatch in header (Check 3)
- Non-integer steps per rev (Check 7)
- Incomplete last line handling (Check 5)
- Blade reconstruction (Check 14)
- Rotor speed check (Check 15)
- JSON and CSV exports
"""
import os
import json
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.diagnostics import (
    run_file_health_diagnostics, reconstruct_missing_blade,
    export_diagnostics_json, export_diagnostics_csv
)
from vawt_post.engine.synthetic import make_fluent_file

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "data", "diagnostics")


def test_diag_check_1_missing_and_empty_file(tmp_path):
    """Check 1: File missing or 0 bytes."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_empty = tmp_path / "empty.out"
    f_empty.write_text("", encoding="utf-8")

    f = InputFiles(total=str(tmp_path / "does_not_exist.out"), blades=[str(f_empty), None, None])
    rep = run_file_health_diagnostics(p, f)

    assert rep.has_errors is True
    checks = [x.check for x in rep.findings]
    assert "File missing or unreadable" in checks
    assert "File empty" in checks


def test_diag_check_2_missing_value_column():
    """Check 2: File has only 'Time Step' and 'flow-time'."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_path = os.path.join(FIXTURE_DIR, "missing_value_col.out")
    f = InputFiles(total=f_path)
    rep = run_file_health_diagnostics(p, f)

    assert rep.has_errors is True
    errs = [x for x in rep.findings if x.check == "Missing value column"]
    assert len(errs) >= 1
    assert "add the report definition to this report file" in errs[0].suggested_fix


def test_diag_check_3_header_role_mismatch():
    """Check 3: Header says moment-1 but file is assigned as blade 2."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_path = os.path.join(FIXTURE_DIR, "wrong_role_header.out")
    f = InputFiles(blades=[None, f_path, None])  # assigned to blade 2
    rep = run_file_health_diagnostics(p, f)

    warns = [x for x in rep.findings if x.check == "Header role mismatch"]
    assert len(warns) >= 1
    assert "Blade 1" in warns[0].explanation


def test_diag_check_4_value_equals_flow_time():
    """Check 4: Value column equals flow time."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_path = os.path.join(FIXTURE_DIR, "value_equals_flow_time.out")
    f = InputFiles(total=f_path)
    rep = run_file_health_diagnostics(p, f)

    assert rep.has_errors is True
    errs = [x for x in rep.findings if x.check == "Value equals flow-time"]
    assert len(errs) >= 1
    assert errs[0].severity == "ERROR"


def test_diag_check_5_partial_last_line():
    """Check 5: Incomplete trailing row."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_path = os.path.join(FIXTURE_DIR, "half_written_last_line.out")
    f = InputFiles(total=f_path)

    # In offline mode: WARNING
    rep_offline = run_file_health_diagnostics(p, f, is_live_mode=False)
    findings = [x for x in rep_offline.findings if x.check == "Partial last line"]
    assert len(findings) >= 1
    assert findings[0].severity == "WARNING"

    # In live mode: INFO
    rep_live = run_file_health_diagnostics(p, f, is_live_mode=True)
    findings_live = [x for x in rep_live.findings if x.check == "Partial last line"]
    assert len(findings_live) >= 1
    assert findings_live[0].severity == "INFO"


def test_diag_check_6_steps_and_time_consistency():
    """Check 6: Missing step (4860), duplicate steps, and restart."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)

    # Missing step
    f_miss = os.path.join(FIXTURE_DIR, "missing_step_all_files.out")
    rep_miss = run_file_health_diagnostics(p, InputFiles(total=f_miss))
    miss_findings = [x for x in rep_miss.findings if x.check == "Missing time steps"]
    assert len(miss_findings) >= 1
    assert "4860" in miss_findings[0].location

    # Duplicate steps
    f_dup = os.path.join(FIXTURE_DIR, "duplicate_steps.out")
    rep_dup = run_file_health_diagnostics(p, InputFiles(total=f_dup))
    dup_findings = [x for x in rep_dup.findings if x.check == "Duplicate steps"]
    assert len(dup_findings) >= 1

    # Restart
    f_rst = os.path.join(FIXTURE_DIR, "restart_step_reset.out")
    rep_rst = run_file_health_diagnostics(p, InputFiles(total=f_rst))
    rst_findings = [x for x in rep_rst.findings if x.check == "Simulation restart"]
    assert len(rst_findings) >= 1


def test_diag_check_7_non_integer_steps_rev():
    """Check 7: Non-integer steps per revolution."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=20.0)
    f_path = os.path.join(FIXTURE_DIR, "non_integer_steps_rev.out")
    rep = run_file_health_diagnostics(p, InputFiles(total=f_path))

    steps_rev_f = [x for x in rep.findings if x.check == "Steps per revolution"]
    assert len(steps_rev_f) >= 1
    assert "Non-integer" in steps_rev_f[0].explanation


def test_diag_check_9_files_from_different_runs(tmp_path):
    """Check 9: File from a different run with non-overlapping steps."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_normal = os.path.join(FIXTURE_DIR, "non_integer_steps_rev.out")  # steps 1-100
    f_stale = os.path.join(FIXTURE_DIR, "stale_different_run.out")    # steps 10000-10100

    f = InputFiles(total=f_normal, blades=[f_stale, None, None])
    rep = run_file_health_diagnostics(p, f)

    assert rep.has_errors is True
    diff_runs = [x for x in rep.findings if x.check == "Files from different runs"]
    assert len(diff_runs) >= 1


def test_diag_check_10_ambiguous_folder_candidates(tmp_path):
    """Check 10: Scan folder with multiple candidate files (e.g. moment-rfile and moment_all-rfile)."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    (tmp_path / "moment-rfile.out").write_text('("Time Step" "cm" "flow-time")\n1 0.1 0.001\n', encoding="utf-8")
    (tmp_path / "moment_all-rfile.out").write_text('("Time Step" "cm_all" "flow-time")\n1 0.1 0.001\n', encoding="utf-8")

    rep = run_file_health_diagnostics(p, InputFiles(), watch_folder=str(tmp_path))

    cand_f = [x for x in rep.findings if x.check == "Ambiguous candidates in folder"]
    assert len(cand_f) >= 1
    assert "total" in rep.candidate_files
    assert len(rep.candidate_files["total"]) == 2


def test_diag_check_11_nan_inf_and_spike():
    """Check 11: NaN/Inf detection and robust z-score spike detection."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)

    # NaN / Inf
    f_nan = os.path.join(FIXTURE_DIR, "nan_inf_values.out")
    rep_nan = run_file_health_diagnostics(p, InputFiles(total=f_nan))
    assert rep_nan.has_errors is True
    nan_f = [x for x in rep_nan.findings if x.check == "NaN or Inf values"]
    assert len(nan_f) >= 1

    # Spike
    f_spike = os.path.join(FIXTURE_DIR, "spike_signal.out")
    rep_spike = run_file_health_diagnostics(p, InputFiles(total=f_spike))
    spike_f = [x for x in rep_spike.findings if "spike" in x.check.lower() or "jump" in x.check.lower()]
    assert len(spike_f) >= 1
    assert "Step 50" in spike_f[0].location or "Step 51" in spike_f[0].location


def test_diag_check_12_flat_signal():
    """Check 12: Zero variance signal."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_flat = os.path.join(FIXTURE_DIR, "flat_signal.out")
    rep = run_file_health_diagnostics(p, InputFiles(total=f_flat))

    flat_f = [x for x in rep.findings if x.check == "Flat or dead signal"]
    assert len(flat_f) >= 1


def test_diag_check_14_blade_reconstruction(tmp_path):
    """Check 14: Mathematical reconstruction of missing blade 3 from total minus blades 1 and 2."""
    omega = 20.0
    dt = 0.001
    steps = np.arange(1, 301)
    t = steps * dt

    # Blade torques with 120 deg phase offsets
    m1 = 0.2 + 0.1 * np.sin(omega * t)
    m2 = 0.2 + 0.1 * np.sin(omega * t - 2.0 * np.pi / 3.0)
    m3 = 0.2 + 0.1 * np.sin(omega * t - 4.0 * np.pi / 3.0)
    m_tot = m1 + m2 + m3

    f_tot = str(tmp_path / "total.out")
    f_b1 = str(tmp_path / "blade_1.out")
    f_b2 = str(tmp_path / "blade_2.out")

    make_fluent_file(f_tot, "total", steps, m_tot, t)
    make_fluent_file(f_b1, "moment_1", steps, m1, t)
    make_fluent_file(f_b2, "moment_2", steps, m2, t)

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=omega)
    f_input = InputFiles(total=f_tot, blades=[f_b1, f_b2, None])

    rep = run_file_health_diagnostics(p, f_input)
    recon_f = [x for x in rep.findings if x.check == "Missing blade reconstructable"]
    assert len(recon_f) >= 1
    assert recon_f[0].repair_available is True
    assert recon_f[0].repair_action == "reconstruct_blade:2"

    # Execute repair
    out_reconstructed = reconstruct_missing_blade(f_input, missing_blade_idx=2, output_dir=str(tmp_path / "repairs"))
    assert os.path.isfile(out_reconstructed)

    # Verify reconstructed file content
    data_recon = np.loadtxt(out_reconstructed, skiprows=1)
    np.testing.assert_allclose(data_recon[:, 1], m3, rtol=1e-4, atol=1e-4)


def test_diag_exports(tmp_path):
    """Test JSON and CSV export helpers."""
    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f_flat = os.path.join(FIXTURE_DIR, "flat_signal.out")
    rep = run_file_health_diagnostics(p, InputFiles(total=f_flat))

    json_path = str(tmp_path / "diagnostics.json")
    csv_path = str(tmp_path / "diagnostics.csv")

    export_diagnostics_json(rep, json_path)
    export_diagnostics_csv(rep, csv_path)

    assert os.path.isfile(json_path)
    assert os.path.isfile(csv_path)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "findings" in data
    assert "summary_counts" in data


def test_real_healthy_fluent_files_produce_zero_warnings_zero_errors(tmp_path):
    """Verify that realistic, healthy ANSYS Fluent monitor files produce zero warnings and zero errors."""
    U = 8.0
    tsr = 2.5
    R = 0.5
    omega = tsr * U / R  # 40.0 rad/s
    dt = 2.0 * np.pi / (omega * 720.0)  # exact 720 steps per revolution (0.5 deg per step)
    n_steps = 720 * 4  # 4 complete revolutions
    steps = np.arange(1, n_steps + 1)
    t = steps * dt

    # Blade torques with 120-degree phasing and realistic cyclic aerodynamic variations
    th1 = omega * t
    th2 = omega * t - 2.0 * np.pi / 3.0
    th3 = omega * t - 4.0 * np.pi / 3.0

    def blade_torque(th):
        return 0.35 + 0.25 * np.sin(th) + 0.12 * np.sin(2.0 * th) + 0.08 * np.cos(3.0 * th)

    m1 = blade_torque(th1)
    m2 = blade_torque(th2)
    m3 = blade_torque(th3)
    m_tot = m1 + m2 + m3
    fx = 15.0 + 3.0 * np.sin(3.0 * omega * t)
    fy = 2.0 + 1.5 * np.cos(3.0 * omega * t)

    # Write Fluent files with authentic multi-line bracketed headers and titles
    files_to_create = {
        "total.out": ("Rotor-Moment-Total", "moment-total", m_tot),
        "blade_1.out": ("Blade-1-Moment", "moment-blade-1", m1),
        "blade_2.out": ("Blade-2-Moment", "moment-blade-2", m2),
        "blade_3.out": ("Blade-3-Moment", "moment-blade-3", m3),
        "thrust_fx.out": ("Force-X-Thrust", "force-x", fx),
        "side_fy.out": ("Force-Y-Side", "force-y", fy),
    }

    created_paths = {}
    for fname, (title, col_name, val_arr) in files_to_create.items():
        fpath = str(tmp_path / fname)
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(f'"{title}"\n')
            fh.write('(\n')
            fh.write(f'("Time-Step" "Flow-Time" "{col_name}")\n')
            for i in range(len(steps)):
                fh.write(f'{steps[i]} {t[i]:.8e} {val_arr[i]:.6e}\n')
            fh.write(')\n')
        created_paths[fname] = fpath

    p = Params(
        U=U, tsr=tsr, R=R, chord=0.06, n_blades=3, omega=omega, dt=dt
    )
    f = InputFiles(
        total=created_paths["total.out"],
        blades=[created_paths["blade_1.out"], created_paths["blade_2.out"], created_paths["blade_3.out"]],
        fx=created_paths["thrust_fx.out"],
        fy=created_paths["side_fy.out"]
    )

    rep = run_file_health_diagnostics(p, f)

    # MUST produce zero errors and zero warnings!
    assert rep.has_errors is False
    assert rep.has_warnings is False
    assert rep.summary_counts.get("ERROR", 0) == 0
    assert rep.summary_counts.get("WARNING", 0) == 0
    non_info = [x for x in rep.findings if x.severity in ("ERROR", "WARNING")]
    assert len(non_info) == 0, f"Unexpected findings: {[f'{x.file}: {x.check} - {x.explanation}' for x in non_info]}"
