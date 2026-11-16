# -*- coding: utf-8 -*-
"""Tests for 3-tier start-azimuth (theta0) detection, diagnostics, and Live Monitor alerts."""
from __future__ import annotations

import math
import time
import numpy as np
import pytest

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.analysis import analyze
from vawt_post.engine.azimuth_detect import detect_start_azimuth, compute_az_base, wrap_deg_360
from vawt_post.engine.diagnostics import run_file_health_diagnostics
from vawt_post.engine.parser import detect_folder_simulation_parameters
from vawt_post.engine.live_monitor import LiveMonitorEngine


def _write_rfile(path, col_name: str, steps, vals, times):
    lines = [f'("Time Step" "{col_name}" "flow-time")\n']
    for s, v, t in zip(steps, vals, times):
        lines.append(f"{int(s)} {float(v):.8f} {float(t):.8f}\n")
    path.write_text("".join(lines), encoding="utf-8")


def _synthetic_blade_curves(omega: float, t: np.ndarray, shift_deg: float, n_blades: int = 3):
    """Synthesize physically consistent Darrieus blade torque curves with peak torque at ~90° azimuth."""
    shift_rad = math.radians(shift_deg)
    m_blades = []
    for k in range(n_blades):
        phase_k = shift_rad - 2.0 * math.pi * k / n_blades
        th = omega * t + phase_k
        # Positive peak near theta = 90 deg (upstream pass), plus blade-pass 3P harmonic so total M(t) oscillates realistically
        mk = 0.20 + 0.55 * np.sin(th) + 0.14 * np.cos(2.0 * th) + 0.08 * np.sin(n_blades * th)
        m_blades.append(mk)
    m_tot = np.sum(m_blades, axis=0)
    return m_tot, m_blades


def test_tier1_flow_time_midspin_auto_compensated(tmp_path):
    """Mid-run continuous recording (t[0] > 0, step[0] > 1) auto-compensates via absolute t."""
    omega = 20.0
    dt = 0.002
    Trev = 2.0 * math.pi / omega
    # Continuous run recorded from rev ~2.375 (135 deg into rev 2) to rev ~5.0
    start_step = int(round((2.375 * Trev) / dt))
    steps = np.arange(start_step, start_step + 600)
    t = steps * dt
    m_tot, m_b = _synthetic_blade_curves(omega, t, shift_deg=0.0, n_blades=3)

    f_tot = tmp_path / "moment-rfile.out"
    f_b1 = tmp_path / "moment1-rfile.out"
    f_b2 = tmp_path / "moment2-rfile.out"
    f_b3 = tmp_path / "moment3-rfile.out"
    _write_rfile(f_tot, "moment", steps, m_tot, t)
    _write_rfile(f_b1, "moment1", steps, m_b[0], t)
    _write_rfile(f_b2, "moment2", steps, m_b[1], t)
    _write_rfile(f_b3, "moment3", steps, m_b[2], t)

    p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, theta0=0.0)
    az_res = detect_start_azimuth(p, t=t, step=steps, m_b1=m_b[0], m_total=m_tot, apply_moment_sign=False)
    assert az_res.is_mid_spin is True
    assert az_res.mid_spin_compensated_by_flow_time is True
    assert az_res.needs_correction is False
    assert az_res.method == "flow_time"

    f = InputFiles(total=str(f_tot), blades=[str(f_b1), str(f_b2), str(f_b3)])
    R = analyze(p, f)
    assert R["res"]["start_azimuth"]["mid_spin_compensated_by_flow_time"] is True

    engine = LiveMonitorEngine(p=p, f=f, watch_dir=str(tmp_path))
    snap = engine.tick_full()
    # Both offline analyze() and live engine use absolute flow-time, so latest Cp & azimuth agree
    assert snap.current_azimuth == pytest.approx(
        float(wrap_deg_360(compute_az_base(p) + math.degrees(omega * t[-1]))), abs=0.5
    )
    assert snap.kpi["running_cp"] > 0.0
    assert snap.parameter_checks["start_azimuth"]["status"] == "PASS"


def test_tier3_blade1_phase_detects_reset_midspin_and_fixes_diagnostics(tmp_path):
    """If recording restarted from t=0 mid-spin (shift=135°), detect theta0≈135° in Auto-Detect & Diagnostics."""
    omega = 25.0
    dt = (2.0 * math.pi / omega) / 500.0
    steps = np.arange(1, 1601)  # > 3 full revs, exact 500 steps/rev
    t = (steps - 1) * dt
    shift_deg = 135.0
    m_tot, m_b = _synthetic_blade_curves(omega, t, shift_deg=shift_deg, n_blades=3)

    _write_rfile(tmp_path / "moment-rfile.out", "moment", steps, m_tot, t)
    _write_rfile(tmp_path / "moment1-rfile.out", "moment1", steps, m_b[0], t)
    _write_rfile(tmp_path / "moment2-rfile.out", "moment2", steps, m_b[1], t)
    _write_rfile(tmp_path / "moment3-rfile.out", "moment3", steps, m_b[2], t)

    p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, theta0=0.0)
    f = InputFiles(
        total=str(tmp_path / "moment-rfile.out"),
        blades=[
            str(tmp_path / "moment1-rfile.out"),
            str(tmp_path / "moment2-rfile.out"),
            str(tmp_path / "moment3-rfile.out"),
        ],
    )

    az_res = detect_start_azimuth(p, t=t, step=steps, m_b1=m_b[0], m_total=m_tot, apply_moment_sign=False)
    assert az_res.started_at_zero is False
    assert az_res.is_mid_spin is True
    assert az_res.needs_correction is True
    assert az_res.confidence == "high"
    assert az_res.recommended_theta0_deg is not None
    assert az_res.recommended_theta0_deg == pytest.approx(135.0, abs=10.0)

    # Diagnostics reports a warning before theta0 is applied
    rep_warn = run_file_health_diagnostics(p, f, watch_folder=str(tmp_path))
    assert any("Mid-spin start" in fn.check and fn.severity == "WARNING" for fn in rep_warn.findings)

    # Folder Auto-Detect automatically populates theta0 when U, R, omega are known or default folder scan
    det = detect_folder_simulation_parameters(str(tmp_path), hint_params={"U": 8.0, "R": 0.5, "omega": omega, "n_blades": 3})
    assert "theta0" in det
    assert det["theta0"] == pytest.approx(135.0, abs=10.0)

    # Once theta0 is applied, Diagnostics passes with zero mid-spin warning
    p_corr = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, theta0=float(det["theta0"]))
    rep_clean = run_file_health_diagnostics(p_corr, f, watch_folder=str(tmp_path))
    assert not any(fn.severity == "WARNING" and "Mid-spin start" in fn.check for fn in rep_clean.findings)
    assert rep_clean.has_warnings is False


def test_zero_aligned_and_total_only_symmetry_note(tmp_path):
    """0°-aligned curve passes cleanly; total-only file notes 360°/N symmetry ambiguity."""
    omega = 20.0
    dt = 0.002
    steps = np.arange(0, 600)
    t = steps * dt
    m_tot, m_b = _synthetic_blade_curves(omega, t, shift_deg=0.0, n_blades=3)
    p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, theta0=0.0)

    az_zero = detect_start_azimuth(p, t=t, step=steps, m_b1=m_b[0], m_total=m_tot, apply_moment_sign=False)
    assert az_zero.started_at_zero is True
    assert az_zero.needs_correction is False

    az_tot = detect_start_azimuth(p, t=t, step=steps, m_b1=None, m_total=m_tot, apply_moment_sign=False)
    assert az_tot.method == "total_torque_modulo"
    assert az_tot.confidence == "low"
    assert az_tot.needs_correction is False
    assert "360" in az_tot.summary or "120" in az_tot.summary


def test_live_monitor_engine_comprehensive_alerts_and_stall_recovery(tmp_path):
    """Verify LiveMonitorEngine emits rich alerts (watch, Δt, blade phasing, midspin, stall/resume, θ0 update)."""
    omega = 25.0
    dt = 0.002
    steps = np.arange(0, 400)  # > 1 rev (Trev = 0.2513s)
    t = steps * dt
    m_tot, m_b = _synthetic_blade_curves(omega, t, shift_deg=120.0, n_blades=3)

    _write_rfile(tmp_path / "moment-rfile.out", "moment", steps, m_tot, t)
    _write_rfile(tmp_path / "moment1-rfile.out", "moment1", steps, m_b[0], t)
    _write_rfile(tmp_path / "moment2-rfile.out", "moment2", steps, m_b[1], t)
    _write_rfile(tmp_path / "moment3-rfile.out", "moment3", steps, m_b[2], t)

    p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, theta0=0.0)
    f = InputFiles(
        total=str(tmp_path / "moment-rfile.out"),
        blades=[
            str(tmp_path / "moment1-rfile.out"),
            str(tmp_path / "moment2-rfile.out"),
            str(tmp_path / "moment3-rfile.out"),
        ],
    )
    engine = LiveMonitorEngine(p=p, f=f, watch_dir=str(tmp_path))
    engine.stalled_timeout = 0.01
    snap = engine.tick_full()

    keys = {a.get("key") for a in snap.alerts}
    assert "session_watch" in keys
    assert "dt_res_initial" in keys
    assert "blade_phasing" in keys
    assert "midspin_detected" in keys
    assert snap.start_azimuth is not None
    assert snap.start_azimuth["needs_correction"] is True
    assert snap.parameter_checks["start_azimuth"]["status"] == "WARNING"
    assert snap.parameter_checks["blade_phasing"]["status"] == "PASS"

    # Simulate stall detection then resume when new steps arrive
    time.sleep(0.03)
    engine.tick_light()
    snap_stall = engine.get_snapshot()
    assert snap_stall.status == "Stalled / finished?"
    assert any(a.get("key") == "stalled_ep_1" and a.get("severity") == "WARNING" for a in snap_stall.alerts)

    with open(tmp_path / "moment-rfile.out", "a", encoding="utf-8") as fh:
        fh.write(f"400 {m_tot[-1]:.6f} {400*dt:.6f}\n")
    for idx in range(3):
        with open(tmp_path / f"moment{idx+1}-rfile.out", "a", encoding="utf-8") as fh:
            fh.write(f"400 {m_b[idx][-1]:.6f} {400*dt:.6f}\n")
    snap_resumed = engine.tick_full()
    assert snap_resumed.status == "Monitoring"
    assert any(a.get("key") == "resumed_ep_1" for a in snap_resumed.alerts)

    # Apply detected theta0 and confirm PASS check
    p_fixed = Params(
        U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt,
        theta0=float(snap.start_azimuth["recommended_theta0_deg"]),
    )
    snap_fixed = engine.update_params(p_fixed)
    assert snap_fixed.parameter_checks["start_azimuth"]["status"] == "PASS"
    assert snap_fixed.start_azimuth is not None
    assert snap_fixed.start_azimuth["needs_correction"] is False


def test_live_monitor_ui_alerts_panel_and_apply_theta0(tmp_path):
    """Headless Qt test of ALERTS & UPDATES panel, mid-spin banner, Apply θ₀ action, and Clear button."""
    try:
        from vawt_post.ui.app import VAWTPostApp
    except Exception as exc:
        pytest.skip(f"GUI init unavailable: {exc}")

    app = VAWTPostApp()
    try:
        app.update_idletasks()
        lm = app.live_monitor_frame

        omega = 25.0
        dt = 0.002
        steps = np.arange(0, 400)
        t = steps * dt
        m_tot, m_b = _synthetic_blade_curves(omega, t, shift_deg=130.0, n_blades=3)
        _write_rfile(tmp_path / "moment-rfile.out", "moment", steps, m_tot, t)
        _write_rfile(tmp_path / "moment1-rfile.out", "moment1", steps, m_b[0], t)
        _write_rfile(tmp_path / "moment2-rfile.out", "moment2", steps, m_b[1], t)
        _write_rfile(tmp_path / "moment3-rfile.out", "moment3", steps, m_b[2], t)

        p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, theta0=0.0)
        f = InputFiles(
            total=str(tmp_path / "moment-rfile.out"),
            blades=[
                str(tmp_path / "moment1-rfile.out"),
                str(tmp_path / "moment2-rfile.out"),
                str(tmp_path / "moment3-rfile.out"),
            ],
        )
        lm.live_params = p
        engine = LiveMonitorEngine(p=p, f=f, watch_dir=str(tmp_path))
        snap = engine.tick_full()
        lm.latest_snapshot = snap
        lm._update_telemetry(snap)
        app.update_idletasks()

        # Banner must appear with recommended theta0 and Apply action
        assert lm.midspin_banner.winfo_manager() == "pack"
        assert lm._recommended_theta0 is not None
        assert lm._rendered_alerts_count >= 3
        assert int(lm.lbl_alerts_badge.cget("text")) == lm._rendered_alerts_count

        # Checks summary on left panel includes Start θ₀ & Phasing
        checks_txt = lm.lbl_checks_echo.cget("text")
        assert "Start θ" in checks_txt
        assert "Phasing:" in checks_txt

        # Click Apply Detected θ₀ -> updates live_params.theta0 and refreshes cached UI
        lm._apply_detected_theta0()
        assert lm.live_params.theta0 == pytest.approx(130.0, abs=10.0)

        # Clear hides current alerts cleanly
        lm._clear_alerts()
        assert int(lm.lbl_alerts_badge.cget("text")) == 0
    finally:
        app.destroy()
