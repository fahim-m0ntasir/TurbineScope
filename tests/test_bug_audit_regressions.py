# -*- coding: utf-8 -*-
"""Regression tests for the codebase-wide TurbineScope bug audit fixes."""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Dict

import numpy as np
import pytest
from PIL import Image

from vawt_post.engine.analysis import analyze, detect_stages
from vawt_post.engine.batch import natural_cubic_spline, pchip_spline, run_batch_analysis
from vawt_post.engine.comparison import calculate_gci, plot_gci_extrapolation
from vawt_post.engine.config import GENERIC_DEFAULTS, InputFiles, Params
from vawt_post.engine.continuity import stitch_series_pair
from vawt_post.engine.excel import _put, write_excel
from vawt_post.engine.gci_engine import GridLevelData, compute_asme_gci, plot_gci_curve
from vawt_post.engine.hdf5_case import inspect_hdf5_case
from vawt_post.engine.live_monitor import LiveMonitorWorker
from vawt_post.engine.parser import detect_folder_simulation_parameters, inspect_fluent_report, parse_report_filename_role


# ---------------------------------------------------------------------------
# 1. Qt Adapter (qt_tk.py) regressions
# ---------------------------------------------------------------------------

def test_qt_adapter_cross_thread_after_shortcuts_image_and_radio():
    from vawt_post.ui import qt_tk as ctk
    from PySide6.QtCore import QCoreApplication

    app = ctk.ensure_qapp()
    root = ctk.CTkFrame(None)

    # 1a. Cross-thread after() marshals onto main thread
    ran_on_thread: list[int] = []
    main_tid = threading.get_ident()
    done_ev = threading.Event()

    def _bg():
        root.after(0, lambda: (ran_on_thread.append(threading.get_ident()), done_ev.set()))

    th = threading.Thread(target=_bg, daemon=True)
    th.start()
    th.join(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not done_ev.is_set() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert done_ev.is_set(), "Cross-thread widget.after(0, ...) failed to fire on GUI thread"
    assert ran_on_thread == [main_tid]

    # 1b. Duplicate shortcut replacement (Ctrl+1 bound twice must not duplicate QShortcut)
    calls: list[str] = []
    root.bind("<Control-Key-1>", lambda _e=None: calls.append("first"))
    root.bind("<Control-1>", lambda _e=None: calls.append("second"))
    assert len(root._qt_shortcuts) == 1
    assert "Ctrl+1" in root._qt_shortcuts

    # 1c. CTkButton image icon attachment & clear
    pil_im = Image.new("RGBA", (32, 24), (30, 144, 255, 255))
    ctk_im = ctk.CTkImage(light_image=pil_im, dark_image=pil_im, size=(32, 24))
    btn = ctk.CTkButton(root, text="", image=ctk_im)
    assert not btn.icon().isNull()
    btn.configure(image=None, text="No Image")
    assert btn.icon().isNull()
    assert btn.text() == "No Image"

    # 1d. CTkLabel configure(image=None, text=...) preserves text
    lbl = ctk.CTkLabel(root, text="", image=ctk_im)
    lbl.configure(image=None, text="Recovered Text")
    assert lbl.text() == "Recovered Text"

    # 1e. CTkRadioButton mutual exclusion via shared StringVar
    var = ctk.StringVar(value="a")
    rb_a = ctk.CTkRadioButton(root, text="A", variable=var, value="a")
    rb_b = ctk.CTkRadioButton(root, text="B", variable=var, value="b")
    assert rb_a.isChecked() is True
    assert rb_b.isChecked() is False
    rb_b.setChecked(True)
    assert var.get() == "b"
    assert rb_a.isChecked() is False
    assert rb_b.isChecked() is True

    root.destroy()
    app.processEvents()


# ---------------------------------------------------------------------------
# 2. Live Monitor & Continuity regressions
# ---------------------------------------------------------------------------

def test_live_worker_file_check_interval_sentinel(tmp_path):
    p = Params(U=8.0, tsr=3.5, R=0.5)
    f = InputFiles()

    # Explicit 10.0s must be honored even when graph_refresh_interval is 15.0s
    w_explicit = LiveMonitorWorker(p, f, graph_refresh_interval=15.0, file_check_interval=10.0)
    assert w_explicit.file_check_interval == pytest.approx(10.0)

    # Omitted (None) defaults to graph_refresh_interval
    w_default = LiveMonitorWorker(p, f, graph_refresh_interval=60.0)
    assert w_default.file_check_interval == pytest.approx(60.0)


def test_continuity_rejects_disjoint_future_backup():
    c_step = np.array([1, 2, 3, 4], dtype=np.int64)
    c_time = c_step * 0.001
    c_val = np.array([0.1, 0.2, 0.3, 0.4])

    # Backup starts strictly in the future relative to current run
    b_step = np.array([100, 101, 102], dtype=np.int64)
    b_time = b_step * 0.001
    b_val = np.array([0.5, 0.6, 0.7])

    prior_s = {"step": b_step, "time": b_time, "value": b_val}
    curr_s = {"step": c_step, "time": c_time, "value": c_val}
    stitched, report = stitch_series_pair(prior_s, curr_s)
    assert report["status"] == "incompatible_backup"
    assert np.array_equal(stitched["step"], c_step)


# ---------------------------------------------------------------------------
# 3. Core Engine (parser, config, analysis, excel) regressions
# ---------------------------------------------------------------------------

def test_parser_hash_header_and_role_classification(tmp_path):
    f_hash = tmp_path / "hash_header.out"
    f_hash.write_text("# Time_Step moment_z flow-time\n1 0.5 0.001\n2 0.6 0.002\n", encoding="utf-8")
    info = inspect_fluent_report(str(f_hash))
    assert info["columns"] == ["Time_Step", "moment_z", "flow-time"]
    assert info["time_col"] == 2
    assert info["data_columns"] == [(1, "moment_z")]

    assert parse_report_filename_role("yplus_1.out")[0] == "unknown"
    assert parse_report_filename_role("cd-2-rfile.out")[0] == "coefficient"
    assert parse_report_filename_role("total-moment-1-rfile.out")[0] == "total_moment"
    role_af, b_idx, _ = parse_report_filename_role("airfoil2-rfile.out", n_blades=3)
    assert role_af == "blade_moment" and b_idx == 2


def test_hdf5_setup_placeholder_dt_and_multistage_tail_detection(tmp_path):
    import h5py

    # 1. Setup-Output.cas.h5 with Fluent factory default (physical-time-step 1.) must NOT return dt=1.0
    setup_cas = tmp_path / "FFF-7-Setup-Output.cas.h5"
    with h5py.File(setup_cas, "w") as hf:
        grp = hf.create_group("settings")
        tv = "((omega . 90.) (mrf-omega (constant . 90.)))"
        rv = '((physical-time-step-expr "1") (physical-time-step 1.) (number-of-time-steps 0) (time-step 0))'
        grp.create_dataset("Thread Variables", data=[tv.encode("utf-8")])
        grp.create_dataset("Rampant Variables", data=[rv.encode("utf-8")])

    h5_setup = inspect_hdf5_case(str(setup_cas))
    assert h5_setup.time_step_s is None

    # 2. Monitor file tail dt (Stage 2) takes precedence over Stage 1 / setup placeholder
    lines = ['("Time Step" "moment" "flow-time")\n']
    t = 0.0
    for s in range(1, 20):
        t += 0.0001939255
        lines.append(f"{s} 0.5 {t:.10f}\n")
    for s in range(20, 45):
        t += 0.0000969627
        lines.append(f"{s} 0.5 {t:.10f}\n")
    (tmp_path / "moment-rfile.out").write_text("".join(lines), encoding="utf-8")

    det = detect_folder_simulation_parameters(str(tmp_path))
    assert det["dt"] == pytest.approx(9.69627e-05, rel=1e-4)

    # 3. Real solved checkpoint .cas.h5 still extracts non-placeholder dt
    solved_cas = tmp_path / "FFF-7-1-00360.cas.h5"
    with h5py.File(solved_cas, "w") as hf:
        grp = hf.create_group("settings")
        rv2 = '((physical-time-step-expr "0.00019083") (physical-time-step 0.00019083) (time-step 360))'
        grp.create_dataset("Rampant Variables", data=[rv2.encode("utf-8")])

    h5_solved = inspect_hdf5_case(str(solved_cas))
    assert h5_solved.time_step_s == pytest.approx(0.00019083)


def test_config_validation_and_defaults():
    assert GENERIC_DEFAULTS["drift_tol"] is None

    errs_bin = Params(U=8.0, tsr=3.5, R=0.5, bin_deg=200.0).validate()
    assert any("180" in e for e in errs_bin)

    errs_th = Params(U=8.0, tsr=3.5, R=0.5, theta0=float("nan")).validate()
    assert any("θ₀" in e or "theta" in e.lower() for e in errs_th)

    errs_az = Params(U=8.0, tsr=3.5, R=0.5, azimuth_offset=float("inf")).validate()
    assert any("offset" in e.lower() for e in errs_az)


def test_detect_stages_handles_step_gaps_and_eof_glitch():
    steps = np.concatenate([np.arange(1, 50), np.arange(55, 100)])  # 5-step gap
    dt = 0.001
    t = steps * dt
    t[-1] += 0.005  # Single-step EOF glitch
    phases = detect_stages(steps, t, Trev=0.36, omega=2.0 * math.pi / 0.36)
    assert len(phases) == 1
    assert phases[0]["dt"] == pytest.approx(dt, rel=1e-3)


def test_excel_sanitizes_nan_and_handles_polyhedral_none_skewness(tmp_path):
    from openpyxl import Workbook, load_workbook

    wb = Workbook()
    ws = wb.active
    _put(ws, 1, 1, float("nan"), fmt="0.000")
    _put(ws, 1, 2, float("inf"), fmt="0.000")
    assert ws.cell(1, 1).value == "n/a"
    assert ws.cell(1, 2).value == "n/a"
    wb.close()

    # Build a real minimal analysis result and attach polyhedral mesh_info with None skewness pcts
    p = Params(U=8.0, tsr=3.5, R=0.5, n_blades=3)
    omega = p.omega_val()
    Trev = 2.0 * math.pi / omega
    dt = Trev / 180.0
    steps = np.arange(1, 3 * 180 + 5)
    t = steps * dt
    m_vals = 1.0 + 0.25 * np.cos(3.0 * omega * t)
    mom_file = tmp_path / "moment-rfile.out"
    lines = ['("Time Step" "moment" "flow-time")\n'] + [
        f"{int(s)} {float(v):.6f} {float(ti):.8f}\n" for s, v, ti in zip(steps, m_vals, t)
    ]
    mom_file.write_text("".join(lines), encoding="utf-8")

    R = analyze(p, InputFiles(total=str(mom_file)), log=lambda _: None)
    R["mesh_info"] = {
        "context": {"system_id": "FFF", "case_h5": "poly.cas.h5"},
        "mesh": {
            "available": True,
            "sizing": {"total_cells": 50000, "total_nodes": 80000, "dimension": 2},
            "quality": {
                "skewness_min": None, "skewness_median": None, "skewness_mean": None,
                "skewness_p95": None, "skewness_max": None,
                "skew_excellent_lt_025_pct": None, "skew_good_025_050_pct": None,
                "skew_fair_050_080_pct": None, "skew_poor_gt_080_pct": None,
                "ortho_min": 0.5, "ortho_p5": 0.7, "ortho_mean": 0.9, "ortho_median": 0.92,
                "ortho_high_gt_070_pct": None, "ortho_low_lt_015_pct": None,
                "growth_rate_gt_2_pct": None,
            },
        },
    }
    out_xlsx = str(tmp_path / "poly_test.xlsx")
    write_excel(R, {}, out_xlsx, log=lambda _: None)
    assert os.path.isfile(out_xlsx)
    wb_check = load_workbook(out_xlsx, read_only=True)
    assert "Mesh_Info" in wb_check.sheetnames
    wb_check.close()


# ---------------------------------------------------------------------------
# 4. GCI, Comparison, Batch, and CLI regressions
# ---------------------------------------------------------------------------

def test_gci_engine_temporal_and_exact_fine_convergence(tmp_path):
    # Temporal GCI: same mesh cell_count, distinct dt_s (fine=0.00025, med=0.0005, coarse=0.001)
    t_grids = [
        GridLevelData(name="Fine", system_id="T1", cell_count=100000, phi=0.3500, dt_s=0.00025, revolutions_completed=10.0),
        GridLevelData(name="Medium", system_id="T2", cell_count=100000, phi=0.3460, dt_s=0.00050, revolutions_completed=10.0),
        GridLevelData(name="Coarse", system_id="T3", cell_count=100000, phi=0.3300, dt_s=0.00100, revolutions_completed=10.0),
    ]
    rep_t = compute_asme_gci(t_grids, dimension=2, is_temporal=True)
    assert rep_t is not None
    assert rep_t.r21 == pytest.approx(2.0)
    assert rep_t.r32 == pytest.approx(2.0)
    assert not any("Time-step mismatch" in c for c in rep_t.failed_checks)

    # Exact fine-grid convergence (phi1 == phi2 != phi3) must NOT be flagged divergent
    eq_grids = [
        GridLevelData(name="Fine", system_id="G1", cell_count=200000, phi=0.3500, dt_s=0.0005, revolutions_completed=10.0),
        GridLevelData(name="Medium", system_id="G2", cell_count=100000, phi=0.3500, dt_s=0.0005, revolutions_completed=10.0),
        GridLevelData(name="Coarse", system_id="G3", cell_count=50000, phi=0.3300, dt_s=0.0005, revolutions_completed=10.0),
    ]
    rep_eq = compute_asme_gci(eq_grids, dimension=2)
    assert rep_eq is not None
    assert rep_eq.is_divergent is False
    assert rep_eq.gci_fine_21_pct == 0.0
    assert rep_eq.phi_ext_21 == pytest.approx(0.3500)

    # plot_gci_curve saves cleanly without forcing pyplot global state
    out_png = str(tmp_path / "gci_temp.png")
    res_path = plot_gci_curve(rep_t, out_path=out_png)
    assert res_path == out_png and os.path.isfile(out_png)


def test_comparison_gci_tolerance_divergence_and_plot(tmp_path):
    def _mk_run(cells: int, cp: float, dt: float) -> Dict[str, Any]:
        return {
            "parameters": {"U": 8.0, "R": 0.5, "tsr": 3.5, "omega": 56.0, "dt": dt},
            "case_metadata": {"mesh_cells": cells, "turb_model": "SST", "time_step_s": dt},
            "summary_kpis": {"cp_reported_mean": cp},
        }

    # Tiny float jitter (< 0.2%) in dt must be accepted; divergent runs return DIVERGENT
    r_fine = _mk_run(200000, 0.30, 0.0005000)
    r_med = _mk_run(100000, 0.35, 0.0005001)
    r_coarse = _mk_run(50000, 0.36, 0.0004999)
    div = calculate_gci([r_fine, r_med, r_coarse], study_type="mesh")
    assert div["status"] == "DIVERGENT"
    assert div["medium_label"] is not None

    # Plotting a divergent GCI (NaN order p) must not crash
    p_out = str(tmp_path / "div_gci.png")
    plot_gci_extrapolation(div, p_out)
    assert os.path.isfile(p_out)

    # Duplicate mesh cell counts raise ValueError instead of ZeroDivisionError
    with pytest.raises(ValueError, match="strictly distinct"):
        calculate_gci([r_fine, r_fine, r_coarse], study_type="mesh")


def test_batch_validation_guards(tmp_path):
    with pytest.raises(ValueError, match="strictly increasing"):
        natural_cubic_spline(np.array([2.0, 2.0]), np.array([0.3, 0.4]))
    with pytest.raises(ValueError, match="strictly increasing"):
        pchip_spline(np.array([2.0, 2.0]), np.array([0.3, 0.4]))
    with pytest.raises(ValueError, match="at least 2"):
        run_batch_analysis([{"tsr": 2.5}], str(tmp_path))


def test_cli_omega_and_preset_overrides(monkeypatch, tmp_path):
    import vawt_post.cli as cli_mod
    from vawt_post.engine import analysis as analysis_mod

    captured_params: list[Params] = []

    def _fake_pipeline(p: Params, f: Any, outdir: str, prefix: str, log: Any = print) -> Dict[str, Any]:
        captured_params.append(p)
        return {
            "p": p,
            "sel": [{"k": 1}],
            "res": {"cp_mean": 0.3, "cm_mean": 0.1, "is_stable": True},
            "xlsx": str(tmp_path / "out.xlsx"),
        }

    monkeypatch.setattr(analysis_mod, "run_pipeline", _fake_pipeline)

    # 1. --omega without --tsr must compute tsr from omega (not keep default 3.5)
    rc = cli_mod.main([
        "--cli", "--total", "dummy.out", "--out", str(tmp_path),
        "--U", "10.0", "--R", "0.5", "--omega", "40.0",
    ])
    assert rc == 0
    assert len(captured_params) == 1
    assert captured_params[0].omega == pytest.approx(40.0)
    assert captured_params[0].tsr is None
    assert captured_params[0].tsr_val() == pytest.approx(2.0)

    # 2. --preset with explicit --U override must preserve preset fields while applying --U
    rc2 = cli_mod.main([
        "--cli", "--preset", "Generic 3-Blade VAWT", "--U", "12.5",
        "--total", "dummy.out", "--out", str(tmp_path),
    ])
    assert rc2 == 0
    assert len(captured_params) == 2
    assert captured_params[1].U == pytest.approx(12.5)
