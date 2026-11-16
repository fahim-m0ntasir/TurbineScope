# -*- coding: utf-8 -*-
"""
Tests for Live Monitor Engine (Part B)
=====================================
Verifies:
- Incremental parsing (bytes read equals bytes appended).
- Partial trailing line buffering.
- Missing-column alert fires on first tick.
- Stalled alert fires when files stop growing.
- Truncation and restart handling.
- Cp per revolution matches offline analysis once data are complete.
- Regression: offline pipeline results unchanged when diagnostics find no problems.
"""
import math
import os
import queue
import time
import pytest
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.live_monitor import (
    IncrementalFileReader, LiveMonitorEngine, LiveMonitorWorker
)
from vawt_post.engine.analysis import analyze
from vawt_post.engine.synthetic import make_fluent_file


def test_live_incremental_bytes_read(tmp_path):
    """Verify that bytes read per tick equals bytes appended."""
    p_file = tmp_path / "stream.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.100 0.001\n', encoding="utf-8")

    reader = IncrementalFileReader(str(p_file), "total")
    count, steps, times, vals = reader.read_new_chunk()
    assert count == 1
    assert steps == [1]
    assert reader.byte_offset == os.path.getsize(str(p_file))

    # Append row 2
    offset_before = reader.byte_offset
    with open(str(p_file), "a", encoding="utf-8") as f:
        f.write("2 0.200 0.002\n")

    size_now = os.path.getsize(str(p_file))
    bytes_appended = size_now - offset_before

    count2, steps2, _, _ = reader.read_new_chunk()
    assert count2 == 1
    assert steps2 == [2]
    assert reader.byte_offset == size_now
    assert (reader.byte_offset - offset_before) == bytes_appended


def test_live_partial_trailing_line_buffering(tmp_path):
    """Verify incomplete trailing line is buffered until next chunk completes it."""
    p_file = tmp_path / "partial.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.100 0.001\n2 0.20', encoding="utf-8")

    reader = IncrementalFileReader(str(p_file), "total")
    count, steps, _, _ = reader.read_new_chunk()
    assert count == 1  # Only row 1 is complete
    assert steps == [1]
    assert reader.buffer == "2 0.20"

    # Append completion of row 2
    with open(str(p_file), "a", encoding="utf-8") as f:
        f.write("0 0.002\n3 0.300 0.003\n")

    count2, steps2, _, _ = reader.read_new_chunk()
    assert count2 == 2  # Completed row 2 and row 3
    assert steps2 == [2, 3]


def test_live_missing_column_alert(tmp_path):
    """Verify that a file missing value column raises alert within one refresh interval."""
    p_file = tmp_path / "missing_col.out"
    p_file.write_text('("Time Step" "flow-time")\n1 0.001\n2 0.002\n', encoding="utf-8")

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f = InputFiles(total=str(p_file))
    engine = LiveMonitorEngine(p, f)
    engine.tick()

    snap = engine.get_snapshot()
    assert any("lacks a value column" in a["message"] for a in snap.alerts)
    assert snap.file_stats["total"].status_color == "red"


def test_live_stalled_detection(tmp_path):
    """Verify that stalled status occurs when files stop growing past timeout."""
    p_file = tmp_path / "stalled.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.100 0.001\n', encoding="utf-8")

    # Manually set mtime in the past
    past_time = time.time() - 120.0
    os.utime(str(p_file), (past_time, past_time))

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3)
    f = InputFiles(total=str(p_file))
    engine = LiveMonitorEngine(p, f)
    engine.tick()

    snap = engine.get_snapshot()
    assert snap.status == "Stalled / finished?"
    assert snap.file_stats["total"].status_color == "amber"


def test_live_worker_and_offline_analysis_consistency(tmp_path):
    """Verify live revolution calculations match offline analysis once data are complete."""
    omega = 20.0
    dt = 0.001
    steps = np.arange(1, 751)
    t = steps * dt
    m = 0.5 + 0.1 * np.sin(omega * t)

    f_tot = str(tmp_path / "total.out")
    make_fluent_file(f_tot, "total", steps, m, t)

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=omega)
    f = InputFiles(total=f_tot)

    # 1. Run LiveMonitorEngine tick
    engine = LiveMonitorEngine(p, f)
    engine.tick()
    snap = engine.get_snapshot()

    # 2. Run offline analysis
    R = analyze(p, f)

    # Both must identify the same number of completed revolutions
    Trev = 2.0 * np.pi / omega
    n_expected = int(np.floor((t[-1] - t[0]) / Trev + 1e-9))

    assert len(snap.rev_history) == n_expected
    assert R["res"]["n_full"] == n_expected

    # Cp of the last complete revolution must match within numerical precision
    cp_live_last = snap.rev_history[-1]["cp"]
    cp_offline_last = R["revs"][-1]["cp"]
    np.testing.assert_allclose(cp_live_last, cp_offline_last, rtol=1e-5, atol=1e-5)


def test_live_restart_and_truncation_recovery(tmp_path):
    """Verify recovery when file is truncated or steps reset."""
    p_file = tmp_path / "restart.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.1 0.001\n2 0.2 0.002\n3 0.3 0.003\n', encoding="utf-8")

    reader = IncrementalFileReader(str(p_file), "total")
    count, steps, _, _ = reader.read_new_chunk()
    assert count == 3

    # Now simulate restart: file rewritten with new initial steps
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.5 0.001\n', encoding="utf-8")

    count2, steps2, _, vals2 = reader.read_new_chunk()
    assert count2 == 1
    assert steps2 == [1]
    assert vals2 == [0.5]


def test_regression_offline_pipeline_unchanged(tmp_path):
    """Verify offline pipeline results are unaffected when diagnostics find no problems."""
    omega = 20.0
    dt = 0.001
    steps = np.arange(1, 751)
    t = steps * dt
    m = 0.5 + 0.1 * np.sin(omega * t)

    f_tot = str(tmp_path / "clean_total.out")
    make_fluent_file(f_tot, "total", steps, m, t)

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=omega)
    f = InputFiles(total=f_tot)

    R = analyze(p, f)
    assert R["res"]["cp_reported_mean"] > 0
    assert "diagnostics" in R
    assert R["diagnostics"].has_errors is False


def test_incremental_reader_step_deduplication(tmp_path):
    """Verify that repeated step index (e.g. from iteration rewriting) is properly deduplicated."""
    p_file = tmp_path / "dedup.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.1 0.001\n2 0.2 0.002\n', encoding="utf-8")

    reader = IncrementalFileReader(str(p_file), "total")
    count1, steps1, times1, vals1 = reader.read_new_chunk()
    assert count1 == 2
    assert steps1 == [1, 2]
    assert vals1 == [0.1, 0.2]

    # Append a duplicate step 2 with updated value, then step 3
    with open(p_file, "a", encoding="utf-8") as f:
        f.write("2 0.25 0.002\n3 0.30 0.003\n")

    count2, steps2, times2, vals2 = reader.read_new_chunk()
    assert count2 == 1
    assert steps2 == [3]
    # Check that reader's raw_vals for step 2 was updated to 0.25
    assert reader.raw_steps == [1, 2, 3]
    assert reader.raw_vals[1] == 0.25
    assert reader.raw_vals[2] == 0.30


def test_worker_wake_responsiveness(tmp_path):
    """Verify worker wakes and services immediate refresh requests promptly."""
    p_file = tmp_path / "wake_test.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.1 0.001\n2 0.2 0.002\n', encoding="utf-8")

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=20.0)
    f = InputFiles(total=str(p_file))
    q = queue.Queue()

    worker = LiveMonitorWorker(p=p, f=f, snapshot_queue=q, graph_refresh_interval=60.0)
    worker.start()
    try:
        # First initial snapshot
        initial_snap = q.get(timeout=2.0)
        assert initial_snap is not None
        assert initial_snap.is_full_refresh is True

        # Now append data and trigger immediate refresh
        with open(p_file, "a", encoding="utf-8") as fh:
            fh.write("3 0.3 0.003\n4 0.4 0.004\n")

        worker.request_immediate_refresh()
        next_snap = q.get(timeout=2.0)
        assert next_snap is not None
        assert next_snap.is_full_refresh is True
        assert next_snap.current_step == 4
    finally:
        worker.stop()


def test_live_multistage_timestep_detection(tmp_path):
    """Verify live monitor accurately detects previous and current time step stages."""
    p_file = tmp_path / "multistage_stream.out"
    # Stage 1: dt = 0.002, 50 steps (t from 0.002 to 0.100)
    # Stage 2: dt = 0.001, 50 steps (t from 0.101 to 0.150)
    lines = ['("Time Step" "cm" "flow-time")']
    t_curr = 0.0
    for s in range(1, 51):
        t_curr += 0.002
        lines.append(f"{s} 0.15 {t_curr:.6f}")
    for s in range(51, 101):
        t_curr += 0.001
        lines.append(f"{s} 0.18 {t_curr:.6f}")
    p_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=20.0)
    f = InputFiles(total=str(p_file))
    engine = LiveMonitorEngine(p=p, f=f)
    snap = engine.tick_full()

    assert snap is not None
    assert len(snap.stages) == 2

    # Previous Stage (Stage 1)
    st1 = snap.stages[0]
    assert st1["stage_index"] == 1
    assert st1["dt"] == pytest.approx(0.002, rel=1e-3)
    assert st1["step_from"] == 1
    assert st1["step_to"] == 50
    assert st1["is_current"] is False

    # Current Stage (Stage 2)
    st2 = snap.stages[1]
    assert st2["stage_index"] == 2
    assert st2["dt"] == pytest.approx(0.001, rel=1e-3)
    assert st2["step_to"] == 100
    assert st2["is_current"] is True

    # Current snapshot dt should match active stage
    assert snap.dt_seconds == pytest.approx(0.001, rel=1e-3)
    assert snap.parameter_checks["step_size"]["stages_count"] == 2


def test_worker_timer_synchronization_and_resource_conservation(tmp_path):
    """Verify worker synchronizes file_check_interval with graph_refresh_interval to avoid wasting resources."""
    p_file = tmp_path / "sync_test.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.1 0.001\n', encoding="utf-8")

    p = Params(U=8.0, tsr=1.5, R=0.6, n_blades=3, omega=20.0)
    f = InputFiles(total=str(p_file))
    q = queue.Queue()

    # 1. When initialized with graph_refresh_interval = 60.0, file_check_interval defaults to 60.0
    worker = LiveMonitorWorker(p=p, f=f, snapshot_queue=q, graph_refresh_interval=60.0)
    assert worker.graph_refresh_interval == 60.0
    assert worker.file_check_interval == 60.0

    # 2. When set_refresh_interval is called, file_check_interval is also synchronized
    worker.set_refresh_interval(180.0)
    assert worker.graph_refresh_interval == 180.0
    assert worker.file_check_interval == 180.0

    # 3. Explicit sub-polling interval is respected if caller requests it
    worker.set_file_check_interval(2.0)
    assert worker.file_check_interval == 2.0
    assert worker.graph_refresh_interval == 180.0


def test_live_monitor_folder_detection_and_discrepancy(tmp_path):
    """Verify LiveMonitorEngine detects folder parameters and validates discrepancy checks."""
    p_file = tmp_path / "moment-rfile.out"
    # Generate 150 steps of steady moment data with dt = 0.001 s
    lines = ['("Time Step" "cm" "flow-time")\n']
    for i in range(1, 151):
        t = i * 0.001
        # 3 blades, base freq ~ 5 Hz -> omega = 10*pi rad/s (~31.4 rad/s)
        cm = 0.15 + 0.05 * math.sin(3.0 * 31.4159 * t)
        lines.append(f"{i} {cm:.6f} {t:.6f}\n")
    p_file.write_text("".join(lines), encoding="utf-8")

    # 1. Matching parameters -> PASS
    p_good = Params(U=10.0, omega=31.4159, R=0.5, chord=0.08, n_blades=3, dt=0.001)
    f = InputFiles(total=str(p_file))
    engine = LiveMonitorEngine(p=p_good, f=f, watch_dir=str(tmp_path))
    snap = engine.tick_full()

    assert snap is not None
    assert "rotor_speed" in snap.parameter_checks
    assert "time_step" in snap.parameter_checks
    assert snap.parameter_checks["time_step"]["status"] == "PASS"

    # 2. Time step mismatch -> dt WARNING alert
    p_bad_dt = Params(U=10.0, omega=31.4159, R=0.5, chord=0.08, n_blades=3, dt=0.005)
    engine_bad = LiveMonitorEngine(p=p_bad_dt, f=f, watch_dir=str(tmp_path))
    snap_bad = engine_bad.tick_full()
    assert snap_bad.parameter_checks["time_step"]["status"] == "WARNING"


def test_live_monitor_late_arriving_files_discovery(tmp_path):
    """Verify LiveMonitorEngine dynamically discovers newly created monitor files in watch_dir."""
    p_file = tmp_path / "moment-rfile.out"
    p_file.write_text('("Time Step" "cm" "flow-time")\n1 0.1 0.001\n', encoding="utf-8")

    f = InputFiles(total=str(p_file), fx=None, fy=None)
    p = Params(U=10.0, omega=20.0, R=0.5, n_blades=3, dt=0.001)

    engine = LiveMonitorEngine(p=p, f=f, watch_dir=str(tmp_path))
    assert "fx" not in engine.readers

    # Later, Fluent creates forcex-rfile.out in watch_dir
    fx_file = tmp_path / "forcex-rfile.out"
    fx_file.write_text('("Time Step" "cd" "flow-time")\n1 0.5 0.001\n', encoding="utf-8")

    # Engine tick triggers _check_late_files()
    engine.tick_light()

    assert "fx" in engine.readers
    assert engine.f.fx == str(fx_file)


def test_live_monitor_tracks_all_nine_roles_and_canonical_order(tmp_path):
    """Verify LiveMonitorEngine tracks shaft force files (fx_shaft, fy_shaft) and UI sorts roles canonically."""
    from vawt_post.engine.parser import autodetect
    from vawt_post.ui.live_monitor_tab import LiveMonitorFrame

    names = [
        ("moment-rfile.out", "moment"),
        ("moment1-rfile.out", "moment1"),
        ("moment2-rfile.out", "moment2"),
        ("moment3-rfile.out", "moment3"),
        ("moment_shaft-rfile.out", "moment_shaft"),
        ("forcex-rfile.out", "forcex"),
        ("forcey-rfile.out", "forcey"),
        ("force_shaftx-rfile.out", "force_shaftx"),
        ("force_shafty-rfile.out", "force_shafty"),
    ]
    for fname, col in names:
        (tmp_path / fname).write_text(
            f'("Time Step" "{col}" "flow-time")\n0 0.0 0.0\n1 0.12 0.0004\n2 0.15 0.0008\n',
            encoding="utf-8"
        )

    f = autodetect(str(tmp_path), 3)
    p = Params(U=9.3, tsr=2.642, omega=46.1359, R=0.5325, chord=0.06, n_blades=3, dt=0.0004)
    engine = LiveMonitorEngine(p=p, f=f, watch_dir=str(tmp_path))
    snap = engine.tick_full()

    assert len(snap.file_stats) == 9
    assert "fx_shaft" in snap.file_stats
    assert "fy_shaft" in snap.file_stats

    sorted_roles = sorted(snap.file_stats.keys(), key=LiveMonitorFrame._file_role_sort_key)
    assert sorted_roles == [
        "total", "blade 1", "blade 2", "blade 3", "shaft", "fx", "fy", "fx_shaft", "fy_shaft"
    ]
    assert [LiveMonitorFrame._file_role_display_name(r) for r in sorted_roles] == [
        "Total", "Blade 1", "Blade 2", "Blade 3", "Shaft", "Fx", "Fy", "Shaft Fx", "Shaft Fy"
    ]


def test_live_monitor_multistage_fft_omega_no_false_mismatch(tmp_path):
    """Verify multi-stage dt reduction does not warp FFT frequency axis or trigger false dt/omega warnings."""
    omega_true = 43.864
    dt1 = 3.979e-4
    dt2 = 1.9895e-4
    p_file = tmp_path / "moment-rfile.out"

    lines = ['("Time Step" "moment" "flow-time")\n']
    t = 0.0
    for step in range(1, 3201):
        dt = dt1 if step <= 1200 else dt2
        t += dt
        m_val = 1.2 + 0.6 * math.sin(3.0 * omega_true * t)
        lines.append(f"{step} {m_val:.6f} {t:.7f}\n")
    p_file.write_text("".join(lines), encoding="utf-8")

    p = Params(U=9.3, omega=omega_true, R=0.5325, chord=0.06, n_blades=3, dt=dt1)
    engine = LiveMonitorEngine(p=p, f=InputFiles(total=str(p_file)), watch_dir=str(tmp_path))
    snap = engine.tick_full()

    assert len(snap.stages) == 2
    assert snap.parameter_checks["rotor_speed"]["status"] == "PASS"
    assert snap.parameter_checks["time_step"]["status"] == "PASS"


def test_live_step_zero_gap_refinement_and_incompat_backup(tmp_path):
    """Verify step-0 start processing, gap refinement rev reset, and incompatible backup protection."""
    # 1. Step 0 alone is processed immediately (last_step_processed initialized to -1)
    p0_file = tmp_path / "step0.out"
    p0_file.write_text('("Time Step" "cm" "flow-time")\n0 0.123 0.0\n', encoding="utf-8")
    p = Params(U=8.0, tsr=1.5, R=0.5, n_blades=3, omega=24.0, dt=0.001)
    eng0 = LiveMonitorEngine(p=p, f=InputFiles(total=str(p0_file)), watch_dir=str(tmp_path))
    snap0 = eng0.tick_full()
    assert snap0.current_step == 0
    assert len(eng0.steps) == 1

    # 2. Incompatible backup (longer backup 0..200 vs fresh live 0..15) is ignored cleanly
    b_dir = tmp_path / "stale_backup"
    c_dir = tmp_path / "fresh_live"
    b_dir.mkdir()
    c_dir.mkdir()
    from vawt_post.engine.continuity import write_fluent_report_file
    import numpy as np
    sb = np.arange(0, 200, dtype=np.int64)
    tb = sb * 0.001
    vb = np.sin(24.0 * tb)
    write_fluent_report_file(str(b_dir / "moment-rfile.out"), sb, vb, tb, "moment")

    sc = np.arange(0, 15, dtype=np.int64)
    tc = sc * 0.0005
    vc = 0.2 * np.cos(24.0 * tc)
    write_fluent_report_file(str(c_dir / "moment-rfile.out"), sc, vc, tc, "moment")

    eng_inc = LiveMonitorEngine(
        p=Params(U=8.0, R=0.5, omega=24.0, n_blades=3, dt=0.0005),
        f=InputFiles(total=str(c_dir / "moment-rfile.out"), prior_source=str(b_dir), continuity_mode="auto"),
        watch_dir=str(c_dir),
    )
    snap_inc = eng_inc.tick_full()
    assert snap_inc.current_step == 14
    assert len(eng_inc.steps) == 15
    assert snap_inc.continuity is not None
    assert snap_inc.continuity["status"] == "incompatible_backup"

    # 3. Gap refinement when post-gap rows cross 24 recomputes revolutions cleanly
    g_b_dir = tmp_path / "gap_b"
    g_c_dir = tmp_path / "gap_c"
    g_b_dir.mkdir()
    g_c_dir.mkdir()
    omega_g = 20.0
    dt_g = 0.001
    s_all = np.arange(0, 420, dtype=np.int64)
    t_all = s_all * dt_g
    v_all = 1.0 + 0.5 * np.sin(omega_g * t_all)
    write_fluent_report_file(str(g_b_dir / "moment-rfile.out"), s_all[:320], v_all[:320], t_all[:320], "moment")
    # Initially write only 5 post-gap steps (330..334)
    write_fluent_report_file(str(g_c_dir / "moment-rfile.out"), s_all[330:335], v_all[330:335], t_all[330:335], "moment")
    eng_gap = LiveMonitorEngine(
        p=Params(U=8.0, omega=omega_g, R=0.5, n_blades=3, dt=dt_g),
        f=InputFiles(total=str(g_c_dir / "moment-rfile.out"), prior_source=str(g_b_dir), continuity_mode="auto"),
        watch_dir=str(g_c_dir),
    )
    snap_g1 = eng_gap.tick_full()
    assert eng_gap._prefix_refined_for_gap is False
    assert snap_g1.continuity["recovered_count"] == 10

    with open(g_c_dir / "moment-rfile.out", "a", encoding="utf-8") as fh:
        for idx_s in range(335, 365):
            fh.write(f"{int(s_all[idx_s])} {float(v_all[idx_s]):.12g} {float(t_all[idx_s]):.12g}\n")
    snap_g2 = eng_gap.tick_full()
    assert eng_gap._prefix_refined_for_gap is True
    assert snap_g2.current_step == 364
    assert len(eng_gap.steps) == 365
    assert snap_g2.rev_history  # recomputed cleanly including the refined gap


def test_ui_folder_switch_and_live_subpolling(tmp_path, monkeypatch):
    """Switching Input/Live folders clears stale prior_source, updates output dir, and sub-polls at 2s."""
    from vawt_post.engine.config import ConfigManager
    from vawt_post.engine.continuity import write_fluent_report_file
    from vawt_post.engine.parser import detect_folder_simulation_parameters
    import numpy as np

    cfg_path = tmp_path / "isolated_cfg.json"
    monkeypatch.setattr(ConfigManager, "_default_config_path", staticmethod(lambda: str(cfg_path)), raising=False)
    orig_init = ConfigManager.__init__

    def _patched_init(self, config_path=None):
        orig_init(self, config_path=str(cfg_path))

    monkeypatch.setattr(ConfigManager, "__init__", _patched_init)

    # Build two separate dp0 systems: FFF-1 (with backup) and FFF-2 (fresh run)
    for name in ("FFF-1", "FFF-2"):
        fl = tmp_path / "Proj_files" / "dp0" / name / "Fluent"
        fl.mkdir(parents=True)
        s = np.arange(0, 20, dtype=np.int64)
        write_fluent_report_file(str(fl / "moment-rfile.out"), s, np.sin(s * 0.1), s * 0.001, "moment")
    b1 = tmp_path / "Proj_files" / "dp0" / "FFF-1" / "backup"
    b1.mkdir()
    write_fluent_report_file(str(b1 / "moment-rfile.out"), np.arange(0, 10), np.zeros(10), np.arange(0, 10) * 0.001, "moment")

    # Verify report.xml transient overrides setup-case Steady
    fl2 = tmp_path / "Proj_files" / "dp0" / "FFF-2" / "Fluent"
    prog2 = tmp_path / "Proj_files" / "progress_files" / "dp0" / "FFF-2" / "Fluent"
    prog2.mkdir(parents=True)
    (prog2 / "report.xml").write_text(
        "<FluentXMLReport>\nFluent Version: 2d, dp, pbns, sstkw, transient\nTime     Unsteady, 2nd-Order Implicit\n</FluentXMLReport>",
        encoding="utf-8",
    )
    from tests.test_mesh_inspector import _make_synthetic_2d_cas_h5
    _make_synthetic_2d_cas_h5(str(fl2 / "FFF-2-Setup-Output.cas.h5"))
    det2 = detect_folder_simulation_parameters(str(tmp_path / "Proj_files" / "dp0" / "FFF-2"))
    assert "unsteady" in str(det2.get("time_formulation", "")).lower()

    from vawt_post.ui.app import VAWTPostApp
    app = VAWTPostApp()
    try:
        sys1 = str(tmp_path / "Proj_files" / "dp0" / "FFF-1")
        sys2 = str(tmp_path / "Proj_files" / "dp0" / "FFF-2")

        app._autodetect_folder(sys1)
        assert os.path.normpath(app.v_out.get()) == os.path.normpath(os.path.join(sys1, "results"))

        app.v_prior_source.set(str(b1))
        app._autodetect_folder(sys2)
        assert app.v_prior_source.get() == ""
        assert os.path.normpath(app.v_out.get()) == os.path.normpath(os.path.join(sys2, "results"))

        # LiveMonitorFrame decouples 2s light file check from 120s graph refresh
        lm = app.live_monitor_frame
        assert lm.refresh_interval_s == 120.0
        assert lm.file_check_interval_s == 2.0

        # Folder switch in Live Monitor clears old prior_source and resets graph state
        lm.custom_watch_folder = str(tmp_path / "Proj_files" / "dp0" / "FFF-1" / "Fluent")
        lm.prior_source = str(b1)
        lm.monitor_fluent_run(sys2, auto_switch_tab=False)
        assert lm.prior_source is None
        lm._stop_monitoring()
    finally:
        app.destroy()


def test_live_monitor_cm_formula_moment_sign_and_light_tip(tmp_path):
    """Verify Cm = M / (Fref*R), moment_sign override, tick_light live tip, headerless recovery, late files, and stage glitch filter."""
    # 1. Start with empty file -> header + data written later -> IncrementalFileReader recovers time_col & val_col
    m_file = tmp_path / "moment-rfile.out"
    m_file.write_text("", encoding="utf-8")
    rd = IncrementalFileReader(str(m_file), "total")
    assert rd._header_from_fallback is True
    assert rd.read_new_chunk()[0] == 0

    m_file.write_text(
        '("Time Step" "flow-time" "moment")\n'
        '1 0.001 2.0\n'
        '2 0.002 nan\n'
        '2 0.002 2.0\n'
        '3 0.003\n'
        '3 0.003 2.0\n',
        encoding="utf-8",
    )
    cnt, steps, times, vals = rd.read_new_chunk()
    assert rd._header_from_fallback is False
    assert rd.time_col == 1 and rd.val_col == 2
    assert rd.raw_steps == [1, 2, 3]
    assert rd.raw_times == pytest.approx([0.001, 0.002, 0.003])
    assert rd.raw_vals == pytest.approx([2.0, 2.0, 2.0])

    # 2. LiveMonitorEngine Cm formula (Mref = Fref * R, NOT Pref / omega) + explicit moment_sign "-1"
    p = Params(U=10.0, omega=30.0, R=0.5, depth=1.0, rho=1.225, n_blades=3, dt=0.001, moment_sign="-1")
    tot_f = tmp_path / "moment_cm.out"
    lines = ['("Time Step" "moment" "flow-time")\n']
    for s in range(0, 300):
        t = s * 0.001
        lines.append(f"{s} 2.0 {t:.6f}\n")
    tot_f.write_text("".join(lines), encoding="utf-8")

    eng = LiveMonitorEngine(p=p, f=InputFiles(total=str(tot_f)), watch_dir=str(tmp_path))
    snap = eng.tick_full()
    A = 2.0 * 0.5 * 1.0
    Pref = 0.5 * 1.225 * (10.0 ** 3) * A
    Mref = 0.5 * 1.225 * (10.0 ** 2) * A * 0.5
    assert eng.Mrot[-1] == pytest.approx(-2.0)  # moment_sign="-1" applied
    assert eng.Cp[-1] == pytest.approx(-2.0 * 30.0 / Pref, rel=1e-6)
    assert eng.Cm[-1] == pytest.approx(-2.0 / Mref, rel=1e-6)
    assert abs(eng.Cm[-1] - eng.Cp[-1]) > 1e-3  # not degenerate Cm == Cp

    # 3. tick_light() advances snapshot current_step/current_time between heavy ticks & ignores 1-step trailing glitch
    with open(tot_f, "a", encoding="utf-8") as fh:
        fh.write("300 2.0 0.300\n301 2.0 0.301\n")
    assert eng.tick_light() is True
    snap_light = eng.get_snapshot(is_full_refresh=False)
    assert snap_light.current_step == 301
    assert snap_light.current_time == pytest.approx(0.301)

    # Append 1-step dt glitch at step 302 -> detect_stages should not emit a bogus 1-step Stage 2
    with open(tot_f, "a", encoding="utf-8") as fh:
        fh.write("302 2.0 0.310\n")
    snap_glitch = eng.tick_full()
    assert len(snap_glitch.stages) == 1

    # 4. Late-arriving fy-only file forces re-processing even if lagging in step count
    fy_f = tmp_path / "forcey-rfile.out"
    fy_lines = ['("Time Step" "cy-force" "flow-time")\n'] + [f"{s} 5.0 {s*0.001:.6f}\n" for s in range(0, 290)]
    fy_f.write_text("".join(fy_lines), encoding="utf-8")
    eng._last_late_check_time = 0.0
    snap_late = eng.tick_full()
    assert eng.Cy is not None and eng.Ct is None
    assert snap_late.timeseries["cy"] is not None


def test_live_monitor_ui_alerts_clear_kinematics_probe_and_cy_plot(tmp_path, monkeypatch):
    """Verify >100 alert clearing, Plot 11 twinx probe, Plot 05 Cy-only plot, Plot 07 1-pt probe, and Autoscale redraw."""
    from vawt_post.engine.config import ConfigManager
    cfg_path = tmp_path / "isolated_ui_cfg.json"
    orig_init = ConfigManager.__init__
    monkeypatch.setattr(ConfigManager, "__init__", lambda self, config_path=None: orig_init(self, config_path=str(cfg_path)))

    from vawt_post.ui.app import VAWTPostApp
    from vawt_post.engine.live_monitor import LiveMonitorSnapshot
    app = VAWTPostApp()
    try:
        app._switch_page("live")
        lm = app.live_monitor_frame
        lm.live_params = Params(U=9.0, tsr=2.5, R=0.5, chord=0.06, n_blades=3, stable_tol=5.0)

        # 1. Clear at 100 alerts -> newly added alert #101 still shows up!
        alerts_100 = [{"seq": i + 1, "time": "12:00:00", "severity": "INFO", "message": f"msg {i+1}"} for i in range(100)]
        snap100 = LiveMonitorSnapshot(status="Monitoring", is_full_refresh=True, alerts=alerts_100)
        lm.latest_snapshot = snap100
        lm._update_alerts_feed(snap100)
        lm._clear_alerts()
        assert lm.lbl_alerts_badge.cget("text") == "0"

        alerts_101 = alerts_100[1:] + [{"seq": 101, "time": "12:00:05", "severity": "WARNING", "message": "alert 101"}]
        snap101 = LiveMonitorSnapshot(status="Monitoring", is_full_refresh=False, alerts=alerts_101)
        lm.latest_snapshot = snap101
        lm._update_alerts_feed(snap101)
        assert lm.lbl_alerts_badge.cget("text") == "1"

        # 2. Plot 11 Kinematics twinx Hover Probe finds graph title + Azimuth coord + both AoA & W/U rows
        theta = np.linspace(0.0, 360.0, 61)
        snap_kin = LiveMonitorSnapshot(
            status="Monitoring",
            is_full_refresh=True,
            current_step=10,
            current_time=0.01,
            timeseries={"t": np.linspace(0.0, 0.2, 20), "cp": np.zeros(20), "cm": np.zeros(20), "ct": None, "cy": np.ones(20) * 0.4, "Mrot": np.zeros(20), "Mb": []},
            rev_history=[{"k": 1, "cp": 0.2, "dcp": None, "stable": False}, {"k": 2, "cp": 0.21, "dcp": 0.05, "stable": True}],
            kinematics={"theta": theta, "aoa": 10.0 * np.sin(np.radians(theta)), "w_over_u": 2.5 + np.cos(np.radians(theta))},
        )
        lm.grid_layout_mode = "🔍 Focus Mode"
        lm.focus_plot = "11. Kinematics (AoA, W/U, Re)"
        lm._rebuild_plot_axes()
        lm._render_graphs(snap_kin, force=True)
        assert len(lm._twin_axes) == 1
        twin_ax = next(iter(lm._twin_axes.keys()))
        probe_k = lm._probe_axis_data(twin_ax, 90.0, 1.0)
        assert probe_k is not None
        assert "11. Kinematics" in probe_k["graph"]
        assert "Azimuth" in probe_k["coord"]
        labels_probed = [r[0] for r in probe_k["rows"]]
        assert any("AoA" in l for l in labels_probed)
        assert any("W/U" in l for l in labels_probed)

        # 3. Plot 05 renders Cy even when Ct is None
        lm.focus_plot = "05. Ct & Cy History"
        lm._rebuild_plot_axes()
        lm._render_graphs(snap_kin, force=True)
        assert "Cy (Side)" in [ln.get_label() for ln in lm.axes[0].lines]

        # 4. Plot 07 1-point curve at 2 completed revolutions is hover-probeable
        lm.focus_plot = "07. Convergence & Spread"
        lm._rebuild_plot_axes()
        lm._render_graphs(snap_kin, force=True)
        probe_conv = lm._probe_axis_data(lm.axes[0], 2.0, 5.0)
        assert probe_conv is not None and "k = 2" in probe_conv["coord"]

        # 5. Toggling Autoscale updates state & triggers graph redraw
        prev_state = lm._last_rendered_graph_state
        lm.v_autoscale.set(False)
        lm._on_autoscale_toggle()
        assert lm._last_rendered_graph_state != prev_state

        # 6. Canvas schedules real Qt paintEvent after _render_graphs without tab switch
        from vawt_post.ui.qt_tk import QApplication
        app.show()
        for _ in range(3):
            QApplication.processEvents()
        paint_calls = []
        orig_pe = lm.canvas.paintEvent
        lm.canvas.paintEvent = lambda ev: (paint_calls.append(True), orig_pe(ev))[-1]
        snap_repaint = LiveMonitorSnapshot(
            status="Monitoring",
            is_full_refresh=True,
            current_step=25,
            current_time=0.025,
            timeseries={"t": np.linspace(0.0, 0.25, 25), "cp": np.zeros(25), "cm": np.zeros(25), "ct": None, "cy": None, "Mrot": np.zeros(25), "Mb": []},
        )
        lm.grid_layout_mode = "2×2 Grid"
        lm._rebuild_plot_axes()
        for _ in range(3):
            QApplication.processEvents()
        paint_calls.clear()
        lm._render_graphs(snap_repaint)
        for _ in range(8):
            QApplication.processEvents()
        assert len(paint_calls) >= 1
    finally:
        app.destroy()






