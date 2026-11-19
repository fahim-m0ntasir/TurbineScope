# -*- coding: utf-8 -*-
"""Regression tests for Pass-2 codebase-wide bug audit fixes."""
from __future__ import annotations

import dataclasses
import json
import math
import os

import numpy as np
import pytest

from vawt_post.engine.analysis import analyze
from vawt_post.engine.comparison import calculate_gci
from vawt_post.engine.config import InputFiles, Params
from vawt_post.engine.continuity import write_fluent_report_file
from vawt_post.engine.diagnostics import run_file_health_diagnostics
from vawt_post.engine.excel_post import _compute_quartile_shares
from vawt_post.engine.external import compute_agreement_metrics, read_external_csv
from vawt_post.engine.flow_visualizer import discover_flowfield_assets
from vawt_post.engine.gci_engine import GridLevelData, compute_asme_gci
from vawt_post.engine.latex import export_latex_snippet
from vawt_post.engine.parser import parse_report_filename_role, read_fluent_report
from vawt_post.engine.specialized_plots import import_yplus_monitor
from vawt_post.engine.synthetic import SimulationStage, generate_multistage_dataset, make_fluent_file
from vawt_post.engine.workbench import FluentSystemInfo, parse_fluent_report_xml, workbench_system_to_config


def test_qt_tk_adapter_pass2_fixes():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from vawt_post.ui import qt_tk as ctk

    ctk.ensure_qapp()
    win = ctk.CTkToplevel()
    try:
        # 1. Negative multi-monitor geometry parsing
        win.geometry("420x310-120+80")
        assert win.width() == 420
        assert win.height() == 310

        # 2. CTkEntry <Return> does not double-fire <FocusOut>
        ent = ctk.CTkEntry(win)
        calls: list[str] = []
        ent.bind("<Return>", lambda _e=None: calls.append("ret"))
        ent.bind("<FocusOut>", lambda _e=None: calls.append("out"))
        ent._on_return_pressed()
        assert calls == ["ret"]

        # 3. CTkLabel cursor="hand2" + <Button-1> click dispatch
        lbl = ctk.CTkLabel(win, text="Click me", cursor="hand2")
        assert lbl.cursor().shape() == Qt.CursorShape.PointingHandCursor
        clicked: list[bool] = []
        lbl.bind("<Button-1>", lambda _e=None: clicked.append(True))
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(5.0, 5.0),
            QPointF(5.0, 5.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        lbl.mousePressEvent(ev)
        assert clicked == [True]

        # 4. CTkScrollableFrame destroyed while scroll sync is pending does not crash
        sf = ctk.CTkScrollableFrame(win)
        sf._notify_scroll_content_changed()
        sf.destroy()
        sf._sync_scroll_content_now()

        # 5. after_cancel on non-pending ID does not leak into _cancelled_after_ids
        win.after_cancel("after_never_scheduled_999")
        assert "after_never_scheduled_999" not in getattr(win, "_cancelled_after_ids", set())
    finally:
        win.destroy()


def test_external_csv_and_metrics_pass2(tmp_path):
    csv_path = tmp_path / "bench.csv"
    csv_path.write_text(
        "# label: Smith, Jones (2024)\n"
        "theta,cm\n"
        "bad_single_col_line\n"
        "0,0.10\n"
        "90,NaN\n"
        "180,0.20\n"
        "355,0.50\n",
        encoding="utf-8",
    )
    ext = read_external_csv(str(csv_path))
    assert ext["label"] == "Smith, Jones (2024)"
    assert ext["count"] == 3
    assert np.all(np.isfinite(ext["y"]))

    # Unsorted CFD x + periodic azimuth wraparound: peak at 355 vs peak at 5 => -10 deg
    cfd_x = np.array([180.0, 355.0, 0.0, 90.0])
    cfd_y = np.array([0.1, 0.9, 0.2, 0.3])
    ext_x = np.array([5.0, 90.0, 180.0, 270.0])
    ext_y = np.array([0.8, 0.3, 0.1, 0.2])
    m = compute_agreement_metrics(cfd_x, cfd_y, ext_x, ext_y, is_periodic_azimuth=True)
    assert m["peak_x_shift"] == pytest.approx(-10.0)

    # Constant mismatched arrays must NOT report correlation_r == 1.0
    m_const = compute_agreement_metrics(
        np.array([0.0, 180.0]), np.array([0.5, 0.5]),
        np.array([0.0, 180.0]), np.array([0.1, 0.1]),
        is_periodic_azimuth=False,
    )
    assert m_const["correlation_r"] == pytest.approx(0.0)


def test_flow_visualizer_and_latex_pass2(tmp_path, monkeypatch):
    # Glob metacharacters in folder name + natural numeric sorting + no duplicate claiming
    case_dir = tmp_path / "run_[test]"
    case_dir.mkdir()
    (case_dir / "ani_custom.cxa").write_text("dummy", encoding="utf-8")
    (case_dir / "ani_custom_2.png").write_bytes(b"png2")
    (case_dir / "ani_custom_10.png").write_bytes(b"png10")
    (case_dir / "vel_snap.png").write_bytes(b"vel")

    seqs = discover_flowfield_assets(str(case_dir))
    assert "Animation: Ani Custom" in seqs
    anim = seqs["Animation: Ani Custom"]
    assert anim.field_type == "generic"
    assert [os.path.basename(p) for p in anim.frame_paths] == ["ani_custom_2.png", "ani_custom_10.png"]
    assert "Velocity (Snapshot)" in seqs
    assert len(seqs) == 2  # animation frames were not duplicated as standalone snapshots

    # LaTeX single backslash \infty and bare relative filename support
    monkeypatch.chdir(tmp_path)
    p = Params(U=8.0, R=0.5, chord=0.06, n_blades=3, tsr=2.5)
    R = {
        "p": p,
        "sel": [{"k": 3}, {"k": 4}],
        "res": {
            "tsr": 2.5, "omega": 40.0, "Re_c": 1.0e5, "A": 1.0, "Pref": 313.6,
            "cp_mean": 0.31, "cm_mean": 0.12, "ct_mean": 0.85, "cy_mean": 0.02,
            "power_mean_W": 150.0, "torque_mean": 3.75, "cp_last_change": 0.002,
            "is_stable": True, "stable_n": 2, "total_revs": 4.0, "n_full": 4, "n_steps": 720,
            "cp_min": -0.05, "cp_max": 0.65, "ripple_pp": 2.25,
            "dominant_harmonic": 3.0, "str": -0.1, "is_self_starting": False,
            "alpha_max": 23.6, "alpha_max_theta": 90.0,
        },
    }
    out = export_latex_snippet(R, {}, "summary_snippet.tex", log=lambda _: None)
    txt = (tmp_path / out).read_text(encoding="utf-8")
    assert r"$W/U_\infty$" in txt
    assert r"$W/U_\\infty$" not in txt


def test_parser_and_continuity_pass2(tmp_path):
    # 1. 2-column (flow-time, value) without step column synthesizes steps 1..N
    ft_path = tmp_path / "ft_only.out"
    ft_path.write_text(
        '("flow-time" "moment-all")\n'
        '0.0001 1.25\n'
        '0.0002 1.35\n'
        '0.0003 1.45\n',
        encoding="utf-8",
    )
    rep = read_fluent_report(str(ft_path))
    assert list(rep["step"]) == [1, 2, 3]
    assert rep["time"] is not None
    assert rep["time"][1] == pytest.approx(0.0002)
    assert rep["value"][2] == pytest.approx(1.45)

    # 2. Coefficient filenames with blade suffix must classify as coefficient
    for fname in ("cd_blade1.out", "cl_blade2.out", "cm_blade3.out"):
        assert parse_report_filename_role(fname, n_blades=3)[0] == "coefficient"

    # 3. write_fluent_report_file with time_arr=None writes 2-column header & round-trips cleanly
    out_2col = tmp_path / "stitched_2col.out"
    write_fluent_report_file(
        str(out_2col),
        np.array([1, 2, 3], dtype=np.int64),
        np.array([0.5, 0.6, 0.7], dtype=float),
        None,
        column_name="moment-all",
    )
    rep2 = read_fluent_report(str(out_2col))
    assert rep2["time"] is None
    assert list(rep2["step"]) == [1, 2, 3]
    assert np.allclose(rep2["value"], [0.5, 0.6, 0.7])


def test_analysis_4blade_autocorr_and_nan_time(tmp_path):
    p = Params(U=8.0, R=0.5, chord=0.05, n_blades=4, tsr=2.5, stable_tol=5.0, n_stable=2)
    stages = [SimulationStage(n_revs=4.0, deg_per_step=2.0)]
    p_gen, f_gen, _ = generate_multistage_dataset(str(tmp_path / "b4"), params=p, stages=stages)
    R_b = analyze(p_gen, f_gen, log=lambda _: None)
    Trev = R_b["res"]["Trev"]
    assert abs(R_b["res"]["autocorr_period"] - Trev) / Trev < 0.03

    # Total-only 4-blade case should also recover Trev via unbiased BPF autocorrelation
    f_tot_only = InputFiles(total=f_gen.total)
    R_t = analyze(p_gen, f_tot_only, log=lambda _: None)
    assert abs(R_t["res"]["autocorr_period"] - Trev) / Trev < 0.03

    # Single NaN timestamp in monitor file is linearly interpolated rather than crashing
    assert f_gen.total is not None
    raw_lines = open(f_gen.total, "r", encoding="utf-8").read().splitlines()
    parts = raw_lines[10].split()
    parts[2] = "nan"
    raw_lines[10] = " ".join(parts)
    nan_tot = tmp_path / "nan_total.out"
    nan_tot.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    R_nan = analyze(p, InputFiles(total=str(nan_tot)), log=lambda _: None)
    assert math.isfinite(R_nan["res"]["cp_mean"])
    assert any("NaN/Inf" in w for w in R_nan["warnings"])


def test_diagnostics_omega_zero_and_overtone(tmp_path):
    # 1. Zero velocity/omega must not raise ZeroDivisionError
    steps = np.arange(1, 201)
    t = steps * 0.001
    vals = np.sin(2.0 * np.pi * 10.0 * t)
    f_tot = str(tmp_path / "moment-rfile.out")
    make_fluent_file(f_tot, "moment-all", steps, vals, t)
    p_zero = Params(U=0.0, R=0.5, tsr=0.0, n_blades=3)
    rep_zero = run_file_health_diagnostics(p_zero, InputFiles(total=f_tot))
    assert rep_zero is not None

    # 2. Pure 2x BPF overtone with zero 1x BPF fundamental must warn in Check 15
    p_om = Params(U=8.0, R=0.5, omega=20.0, n_blades=3)
    f_bpf = 3.0 * (20.0 / (2.0 * math.pi))
    steps2 = np.arange(1, 1201)
    t2 = steps2 * 0.0005
    vals_2x = 2.0 + 0.8 * np.sin(2.0 * math.pi * (2.0 * f_bpf) * t2)
    f_tot2 = str(tmp_path / "moment_2x.out")
    make_fluent_file(f_tot2, "moment-all", steps2, vals_2x, t2)
    rep_ov = run_file_health_diagnostics(p_om, InputFiles(total=f_tot2))
    assert any("Rotor speed mismatch" in fd.check for fd in rep_ov.findings)


def test_workbench_report_xml_regex_and_cw_config(tmp_path):
    xml_path = tmp_path / "report.xml"
    xml_path.write_text(
        "Moving Mesh Rotation Speed [rad/s] -45.5\n"
        "Velocity Magnitude [m/s] 8.0-inlet\n"
        "Time Step Size [s] 1.5e-04\n"
        "Density kg/m^3 constant 1.225\n"
        "Viscosity kg/(m s) constant 1.7894e-05\n",
        encoding="utf-8",
    )
    parsed = parse_fluent_report_xml(str(xml_path))
    assert parsed["omega"] == pytest.approx(-45.5)
    assert parsed["velocity"] == pytest.approx(8.0)
    assert parsed["time_step"] == pytest.approx(1.5e-4)

    sys_obj = FluentSystemInfo(
        system_id="FFF-CW",
        system_dir=str(tmp_path),
        fluent_dir=str(tmp_path),
        omega=-45.5,
        velocity=8.0,
        rotor_radius=0.5,
        chord=0.06,
        blade_zones=["blade1", "blade2", "blade3"],
    )
    bundle = workbench_system_to_config(sys_obj)
    p_cw = bundle.params
    assert p_cw.omega == pytest.approx(45.5)
    assert p_cw.rotation_dir == "cw"
    assert p_cw.tsr is not None and p_cw.tsr > 0


def test_specialized_plots_yplus_fluent_order_and_quartile_negative_cp(tmp_path):
    import pandas as pd

    # Standard Fluent 3-column y+ file: (step, yplus, flow-time)
    yp_path = tmp_path / "yplus-rfile.out"
    yp_path.write_text(
        '"yplus-report"\n'
        '"Time Step" "yplus-blade" "flow-time"\n'
        '("Time Step" "yplus-blade" "flow-time")\n'
        '1 0.72 0.0005\n'
        '2 0.94 0.0010\n'
        '3 0.88 0.0015\n',
        encoding="utf-8",
    )
    res_yp = import_yplus_monitor(str(yp_path))
    assert res_yp["max"] == pytest.approx(0.94)
    assert res_yp["time"][-1] == pytest.approx(0.0015)

    # Negative net Cp quartile shares must divide by |tot| so signs aren't inverted
    df_neg = pd.DataFrame({
        "theta": [90.0, 180.0, 270.0, 0.0],
        "cp_contrib": [0.10, -0.50, -0.40, -0.20],  # sum = -1.0
    })
    q = _compute_quartile_shares(df_neg, None)
    assert q["pct_up"] == pytest.approx(10.0)
    assert q["pct_lee"] == pytest.approx(-50.0)
    assert _compute_quartile_shares(pd.DataFrame(), None) == {
        "pct_up": 0.0, "pct_lee": 0.0, "pct_down": 0.0, "pct_wind": 0.0,
    }


def test_gci_engine_and_comparison_edge_cases():
    # 1. Identical 3-grid solutions must NOT be labeled Monotonic Divergence
    g_eq = [
        GridLevelData("Fine", "S1", 200000, 0.3000),
        GridLevelData("Med", "S2", 100000, 0.3000),
        GridLevelData("Coarse", "S3", 50000, 0.3000),
    ]
    rep_eq = compute_asme_gci(g_eq, dimension=2)
    assert rep_eq is not None
    assert "Divergence" not in rep_eq.to_dict()["convergence_type"]

    # 2. Monotonically divergent grids must fail asymptotic range and comparability
    g_div = [
        GridLevelData("Fine", "S1", 200000, 0.50),
        GridLevelData("Med", "S2", 100000, 0.35),
        GridLevelData("Coarse", "S3", 50000, 0.30),
    ]
    rep_div = compute_asme_gci(g_div, dimension=2)
    assert rep_div is not None
    assert rep_div.is_divergent is True
    assert rep_div.in_asymptotic_range is False
    assert rep_div.is_valid_gci is False

    # 3. comparison.calculate_gci: eps32 == 0 with eps21 != 0 -> DIVERGENT (not CONVERGED_EXACT)
    def _mk_run(cells: int, cp: float, dim: int = 2):
        return {
            "summary_kpis": {"cp_reported_mean": cp},
            "case_metadata": {"mesh_cells": cells, "turb_model": "SST", "time_step_s": 0.001},
            "parameters": {"U": 8.0, "R": 0.5, "tsr": 2.5, "omega": 40.0},
            "mesh_info": {"mesh": {"dimension": dim}},
        }

    gci_div = calculate_gci([_mk_run(200000, 0.40), _mk_run(100000, 0.30), _mk_run(50000, 0.30)])
    assert gci_div["status"] == "DIVERGENT"

    # 3D spacing (dimension=3) + r_warning preserved on SUCCESS
    gci_3d = calculate_gci(
        [_mk_run(160000, 0.320, dim=3), _mk_run(100000, 0.310, dim=3), _mk_run(60000, 0.290, dim=3)],
        dimension=3,
    )
    assert gci_3d["status"] == "SUCCESS"
    assert gci_3d["r21"] == pytest.approx((160000 / 100000) ** (1.0 / 3.0))
    assert gci_3d["r_warning"] is not None


def test_app_export_json_and_forwarder():
    # CaseMetadata nested inside Params serializes cleanly with dataclasses.asdict
    p = Params(U=8.0, R=0.5, chord=None, n_blades=3, tsr=2.5)
    payload = json.dumps({"params": dataclasses.asdict(p), "files": dataclasses.asdict(InputFiles())})
    loaded = json.loads(payload)
    assert loaded["params"]["chord"] is None
    assert "metadata" in loaded["params"]

    # vawt_analyzer.py must not reference removed vawt_post.ui.app.main
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fwd_txt = open(os.path.join(root_dir, "vawt_analyzer.py"), "r", encoding="utf-8").read()
    assert "vawt_post.ui.app" not in fwd_txt
    assert "from vawt_post.cli import main" in fwd_txt
