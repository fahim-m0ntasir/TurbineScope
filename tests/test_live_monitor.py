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




