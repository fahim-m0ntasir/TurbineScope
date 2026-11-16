# -*- coding: utf-8 -*-
"""Comprehensive tests for Prior Run Linking, Checkpoint Deduplication & Gap Recovery."""
from __future__ import annotations

import math
import os
import zipfile

import numpy as np
import pytest

from vawt_post.engine.analysis import analyze
from vawt_post.engine.config import InputFiles, Params
from vawt_post.engine.continuity import (
    export_stitched_report_files,
    load_prior_series,
    resolve_prior_source_directory,
    stitch_and_recover_all,
    stitch_series_pair,
    write_fluent_report_file,
)
from vawt_post.engine.diagnostics import run_file_health_diagnostics
from vawt_post.engine.live_monitor import LiveMonitorEngine
from vawt_post.engine.parser import read_fluent_report
from vawt_post.engine.plots import _make_theme_spec, _plot_01_cp_history


_UPLOADED_DIR = (
    r"C:\Users\Fahim Montasir\.gemini\antigravity\brain\4d189283-e1d6-46b4-ac62-a17d212a6312\.user_uploaded"
)
_REAL_ZIP = os.path.join(_UPLOADED_DIR, "media_1794788523087_740f108c.zip")
_REAL_CURR = {
    "total": os.path.join(_UPLOADED_DIR, "media_1794788611303_942f034c.out"),
    "blade1": os.path.join(_UPLOADED_DIR, "media_1794788611301_1776ae27.out"),
    "blade2": os.path.join(_UPLOADED_DIR, "media_1794788611301_9b3c0ee0.out"),
    "blade3": os.path.join(_UPLOADED_DIR, "media_1794788611303_02881b9a.out"),
    "fx": os.path.join(_UPLOADED_DIR, "media_1794788611299_8499030f.out"),
    "fy": os.path.join(_UPLOADED_DIR, "media_1794788611300_84e1cdde.out"),
    "fx_shaft": os.path.join(_UPLOADED_DIR, "media_1794788611300_bbaef130.out"),
}


def _make_vawt_params(dt: float = 0.0005, omega: float = 35.0) -> Params:
    return Params(
        U=8.0,
        tsr=None,
        omega=omega,
        R=0.5,
        chord=0.06,
        n_blades=3,
        depth=1.0,
        dt=dt,
        stable_tol=5.0,
        n_stable=2,
    )


def _synth_vawt_signal(t: np.ndarray, omega: float, phase: float = 0.0) -> np.ndarray:
    return (
        0.45
        + 0.85 * np.sin(omega * t + phase)
        - 0.35 * np.cos(2.0 * omega * t + 2.0 * phase)
        + 0.15 * np.sin(3.0 * omega * t + 3.0 * phase)
    )


def test_zip_cache_and_role_matching(tmp_path):
    """ZIP archives resolve lightweight monitor files quickly without touching .cas.h5."""
    zip_path = tmp_path / "backup.zip"
    out_file = tmp_path / "moment-rfile.out"
    steps = np.arange(0, 100, dtype=np.int64)
    times = steps * 0.001
    vals = _synth_vawt_signal(times, 20.0)
    write_fluent_report_file(str(out_file), steps, vals, times, "moment")

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(out_file, arcname="Fluent/moment-rfile.out")
        zf.writestr("Fluent/huge_case.cas.h5", b"FAKE_BINARY_CAS_HEADER")

    resolved_dir, is_zip = resolve_prior_source_directory(str(zip_path))
    assert is_zip is True
    assert os.path.isfile(os.path.join(resolved_dir, "moment-rfile.out"))
    assert not os.path.exists(os.path.join(resolved_dir, "huge_case.cas.h5"))

    loaded = load_prior_series(str(zip_path), n_blades=3)
    assert "total" in loaded["series"]
    assert len(loaded["series"]["total"]["step"]) == 100


def test_overlap_checkpoint_verification_and_divergence():
    """Exact checkpoint overlap deduplicates cleanly; divergent overlaps emit warning flag."""
    dt = 0.001
    omega = 20.0
    s_b = np.arange(0, 200, dtype=np.int64)
    t_b = s_b * dt
    v_b = _synth_vawt_signal(t_b, omega)

    s_c = np.arange(180, 300, dtype=np.int64)
    t_c = s_c * dt
    v_c = _synth_vawt_signal(t_c, omega)

    prior_s = {"step": s_b, "time": t_b, "value": v_b}
    curr_s = {"step": s_c, "time": t_c, "value": v_c}
    p = _make_vawt_params(dt=dt, omega=omega)

    merged, info = stitch_series_pair(prior_s, curr_s, p=p, mode="auto")
    assert info["status"] == "overlap_verified"
    assert info["overlap_count"] == 20
    assert info["overlap_divergent"] is False
    assert len(merged["step"]) == 300
    assert merged["step"][0] == 0 and merged["step"][-1] == 299
    assert not np.any(merged["recovered_mask"])

    # Divergent overlap triggers overlap_divergent while still splicing cleanly
    curr_div = {"step": s_c, "time": t_c, "value": v_c + 0.5}
    _, div_info = stitch_series_pair(prior_s, curr_div, p=p, mode="auto")
    assert div_info["status"] == "overlap_divergent"
    assert div_info["overlap_divergent"] is True


def test_positive_gap_phase_harmonic_recovery_and_cross_role_conservation(tmp_path):
    """Positive missing-timestep gaps recover periodic VAWT curves with low error and conservation."""
    dt = 0.0005
    omega = 35.0
    p = _make_vawt_params(dt=dt, omega=omega)
    # ~5 full revolutions total (Trev ~ 0.1795 s -> ~359 steps/rev -> 1800 steps)
    steps_all = np.arange(0, 1800, dtype=np.int64)
    t_all = steps_all * dt
    b_true = [
        _synth_vawt_signal(t_all, omega, phase=0.0),
        _synth_vawt_signal(t_all, omega, phase=2.0 * math.pi / 3.0),
        _synth_vawt_signal(t_all, omega, phase=4.0 * math.pi / 3.0),
    ]
    sh_true = -0.05 + 0.01 * np.sin(omega * t_all)
    tot_true = b_true[0] + b_true[1] + b_true[2] + sh_true

    # Drop 30 timesteps (steps 1200..1229) to simulate crash gap
    gap_slice = slice(1200, 1230)
    pre_mask = steps_all < 1200
    post_mask = steps_all >= 1230

    b_dir = tmp_path / "backup"
    c_dir = tmp_path / "resumed"
    b_dir.mkdir()
    c_dir.mkdir()

    names = {
        "total": "moment-rfile.out",
        "blade1": "moment1-rfile.out",
        "blade2": "moment2-rfile.out",
        "blade3": "moment3-rfile.out",
        "shaft": "moment_shaft-rfile.out",
    }
    true_map = {
        "total": tot_true,
        "blade1": b_true[0],
        "blade2": b_true[1],
        "blade3": b_true[2],
        "shaft": sh_true,
    }
    for k, fn in names.items():
        write_fluent_report_file(str(b_dir / fn), steps_all[pre_mask], true_map[k][pre_mask], t_all[pre_mask], k)
        write_fluent_report_file(str(c_dir / fn), steps_all[post_mask], true_map[k][post_mask], t_all[post_mask], k)

    f = InputFiles(
        total=str(c_dir / names["total"]),
        blades=[str(c_dir / names["blade1"]), str(c_dir / names["blade2"]), str(c_dir / names["blade3"])],
        shaft=str(c_dir / names["shaft"]),
        prior_source=str(b_dir),
        continuity_mode="auto",
    )

    # 1. Diagnostics sees clean continuity & reports recovery notice without ERRORs
    rep = run_file_health_diagnostics(p, f)
    assert not rep.has_errors
    assert any(x.check == "Run continuity & recovery" for x in rep.findings)

    # 2. Full analyze() pipeline runs cleanly and accuracy matches ground truth closely
    R = analyze(p, f, log=lambda *_: None)
    rec_mask = R["data"]["recovered_mask"]
    assert int(np.sum(rec_mask)) == 30
    assert R["res"]["continuity"]["status"] == "gap_recovered"
    assert R["res"]["continuity"]["mean_validation_nrmse_pct"] is not None

    m1_rec = R["data"]["Mb"][0][gap_slice]
    m1_true = b_true[0][gap_slice]
    nrmse = float(np.sqrt(np.mean((m1_rec - m1_true) ** 2)) / np.std(m1_true) * 100.0)
    assert nrmse < 2.0, f"Expected accurate harmonic recovery (<2% NRMSE), got {nrmse:.2f}%"

    # Cross-role conservation: M_total == M_b1 + M_b2 + M_b3 + M_shaft on recovered rows
    tot_rec = R["data"]["raw"]["total"][gap_slice]
    sum_rec = (
        R["data"]["raw"]["blade1"][gap_slice]
        + R["data"]["raw"]["blade2"][gap_slice]
        + R["data"]["raw"]["blade3"][gap_slice]
        + R["data"]["raw"]["shaft"][gap_slice]
    )
    assert float(np.max(np.abs(tot_rec - sum_rec))) < 1e-6

    # 3. Export stitched .out files round-trips cleanly
    exp_dir = tmp_path / "stitched"
    exp = export_stitched_report_files(f, str(exp_dir), p=p)
    assert len(exp["written_files"]) == 5
    parsed_exp = read_fluent_report(exp["written_files"]["total"])
    assert len(parsed_exp["step"]) == 1800
    assert np.all(np.diff(parsed_exp["step"]) == 1)

    # 4. Static plot overlay renders cleanly with recovered band
    st = _make_theme_spec(True)
    fig = _plot_01_cp_history(R, st, dpi=100, target_fig=None)
    assert fig is not None


def test_short_gap_pchip_and_splice_only_modes():
    """Ultra-short gaps use PCHIP cleanly; splice_only preserves unrecovered gap notice."""
    s_b = np.arange(0, 20, dtype=np.int64)
    t_b = s_b * 0.001
    v_b = np.sin(20.0 * t_b)
    s_c = np.arange(23, 40, dtype=np.int64)
    t_c = s_c * 0.001
    v_c = np.sin(20.0 * t_c)

    prior_s = {"step": s_b, "time": t_b, "value": v_b}
    curr_s = {"step": s_c, "time": t_c, "value": v_c}

    merged, info = stitch_series_pair(prior_s, curr_s, mode="auto")
    assert info["status"] == "gap_recovered"
    assert info["recovered_count"] == 3
    assert "PCHIP" in info["method"]
    assert len(merged["step"]) == 40
    assert float(np.max(np.abs(merged["value"][20:23] - np.sin(20.0 * np.arange(20, 23) * 0.001)))) < 0.02

    merged_so, info_so = stitch_series_pair(prior_s, curr_s, mode="splice_only")
    assert info_so["status"] == "gap_unfilled"
    assert len(merged_so["step"]) == 37


def test_live_monitor_engine_with_prior_and_hot_link(tmp_path):
    """LiveMonitorEngine stitches cached backup once, tails incremental writes, and supports live link/clear."""
    dt = 0.001
    omega = 20.0
    p = _make_vawt_params(dt=dt, omega=omega)

    b_dir = tmp_path / "backup"
    c_dir = tmp_path / "live"
    b_dir.mkdir()
    c_dir.mkdir()

    steps_all = np.arange(0, 900, dtype=np.int64)
    t_all = steps_all * dt
    b_vals = [
        _synth_vawt_signal(t_all, omega, 0.0),
        _synth_vawt_signal(t_all, omega, 2.0 * math.pi / 3.0),
        _synth_vawt_signal(t_all, omega, 4.0 * math.pi / 3.0),
    ]
    tot_vals = b_vals[0] + b_vals[1] + b_vals[2]

    # Write backup 0..799, skip 15 steps (800..814), write initial live 815..869
    b_idx = steps_all < 800
    c1_idx = (steps_all >= 815) & (steps_all < 870)
    write_fluent_report_file(str(b_dir / "moment-rfile.out"), steps_all[b_idx], tot_vals[b_idx], t_all[b_idx], "moment")
    write_fluent_report_file(str(c_dir / "moment-rfile.out"), steps_all[c1_idx], tot_vals[c1_idx], t_all[c1_idx], "moment")
    for k in range(3):
        write_fluent_report_file(str(b_dir / f"moment{k+1}-rfile.out"), steps_all[b_idx], b_vals[k][b_idx], t_all[b_idx], f"moment{k+1}")
        write_fluent_report_file(str(c_dir / f"moment{k+1}-rfile.out"), steps_all[c1_idx], b_vals[k][c1_idx], t_all[c1_idx], f"moment{k+1}")

    f = InputFiles(
        total=str(c_dir / "moment-rfile.out"),
        blades=[str(c_dir / f"moment{k+1}-rfile.out") for k in range(3)],
        prior_source=str(b_dir),
        continuity_mode="auto",
    )

    eng = LiveMonitorEngine(p, f, watch_dir=str(c_dir))
    snap1 = eng.tick_full()
    assert len(eng.steps) == 870
    assert snap1.continuity is not None
    assert snap1.continuity["status"] == "gap_recovered"
    assert snap1.continuity["recovered_count"] == 15
    assert snap1.timeseries["recovered_time_ranges"]

    # Append 30 more live steps (870..899) incrementally without re-reading backup
    with open(c_dir / "moment-rfile.out", "a", encoding="utf-8") as fh:
        for idx_s in range(870, 900):
            fh.write(f"{int(steps_all[idx_s])} {float(tot_vals[idx_s]):.12g} {float(t_all[idx_s]):.12g}\n")
    for k in range(3):
        with open(c_dir / f"moment{k+1}-rfile.out", "a", encoding="utf-8") as fh:
            for idx_s in range(870, 900):
                fh.write(f"{int(steps_all[idx_s])} {float(b_vals[k][idx_s]):.12g} {float(t_all[idx_s]):.12g}\n")

    snap2 = eng.tick_full()
    assert len(eng.steps) == 900
    assert snap2.current_step == 899

    # Clear backup link dynamically while monitoring
    snap3 = eng.set_prior_source(None)
    assert snap3.continuity is None
    assert len(eng.steps) == 85  # steps 815..899 only


@pytest.mark.skipif(
    not (os.path.isfile(_REAL_ZIP) and all(os.path.isfile(p) for p in _REAL_CURR.values())),
    reason="Uploaded real Fluent backup.zip + resumed files not present on this machine.",
)
def test_real_uploaded_backup_zip_and_resumed_run_and_artificial_gap():
    """Verify against the user's real Fluent backup.zip + 7 resumed files AND an artificial gap test."""
    p = Params(U=9.0, tsr=2.4, R=0.515, chord=0.0858, n_blades=3, dt=8.726646e-05)
    f = InputFiles(
        total=_REAL_CURR["total"],
        blades=[_REAL_CURR["blade1"], _REAL_CURR["blade2"], _REAL_CURR["blade3"]],
        fx=_REAL_CURR["fx"],
        fy=_REAL_CURR["fy"],
        fx_shaft=_REAL_CURR["fx_shaft"],
        prior_source=_REAL_ZIP,
        continuity_mode="auto",
    )

    rep = run_file_health_diagnostics(p, f)
    assert not rep.has_errors
    assert any(x.check == "Run continuity verified" for x in rep.findings)

    # Full analysis on 7926 stitched steps (0..7925 with 74-step overlap deduplicated)
    R = analyze(p, f, log=lambda *_: None)
    assert len(R["data"]["step"]) == 7926
    assert int(R["data"]["step"][0]) == 0 and int(R["data"]["step"][-1]) == 7925
    assert R["res"]["continuity"]["status"] == "overlap_verified"
    assert R["res"]["continuity"]["overlap_count"] == 74
    assert R["res"]["continuity"]["overlap_max_val_diff"] == 0.0

    # Benchmark missing-step recovery directly on real Fluent blade1 waveform (removing steps 7500..7535)
    b1_real = R["data"]["raw"]["blade1"]
    t_real = R["data"]["t"]
    s_real = R["data"]["step"]
    m_pre = s_real < 7500
    m_post = s_real >= 7536
    gap_idx = (s_real >= 7500) & (s_real < 7536)

    prior_s = {"step": s_real[m_pre], "time": t_real[m_pre], "value": b1_real[m_pre]}
    curr_s = {"step": s_real[m_post], "time": t_real[m_post], "value": b1_real[m_post]}
    merged_real, info_real = stitch_series_pair(prior_s, curr_s, p=p, mode="auto")
    assert info_real["status"] == "gap_recovered"
    assert info_real["recovered_count"] == 36
    pred_gap = merged_real["value"][merged_real["recovered_mask"]]
    true_gap = b1_real[gap_idx]
    real_nrmse = float(np.sqrt(np.mean((pred_gap - true_gap) ** 2)) / np.std(b1_real[m_pre]) * 100.0)
    assert real_nrmse < 5.0, f"Expected <5% NRMSE on real Fluent crash gap, got {real_nrmse:.2f}%"


def test_headless_ui_prior_link_controls(tmp_path):
    """Verify Input Tab and Live Monitor Prior Run Link UI controls and persistence wiring."""
    from vawt_post.ui.app import VAWTPostApp
    from vawt_post.ui.live_monitor_tab import LiveMonitorFrame

    app = VAWTPostApp()
    try:
        app.update_idletasks()
        assert hasattr(app, "v_prior_source")
        assert hasattr(app, "v_continuity_mode")

        prior_dir = tmp_path / "prior_ui"
        curr_dir = tmp_path / "curr_ui"
        prior_dir.mkdir()
        curr_dir.mkdir()

        t_prior = np.arange(0, 60) * 0.001
        t_curr = np.arange(55, 110) * 0.001
        val_prior = 1.5 * np.sin(2.0 * np.pi * t_prior / 0.04)
        val_curr = 1.5 * np.sin(2.0 * np.pi * t_curr / 0.04)

        write_fluent_report_file(str(prior_dir / "moment-rfile.out"), np.arange(0, 60), t_prior, val_prior, "Moment Total")
        c_tot = curr_dir / "moment-rfile.out"
        write_fluent_report_file(str(c_tot), np.arange(55, 110), t_curr, val_curr, "Moment Total")

        app._apply_files_dict({
            "total": str(c_tot),
            "prior_source": str(prior_dir),
            "continuity_mode": "prefer_backup",
        })
        app.update_idletasks()

        assert app.v_prior_source.get() == str(prior_dir)
        assert app._get_continuity_code() == "prefer_backup"
        gathered = app._gather_files()
        assert gathered.prior_source == str(prior_dir)
        assert gathered.continuity_mode == "prefer_backup"

        app._on_prior_changed(run_diag=False)
        status_txt = app.lbl_prior_status.cget("text")
        assert "step" in status_txt.lower() or "linked" in status_txt.lower() or "prior" in status_txt.lower()

        app._clear_prior_source()
        assert app.v_prior_source.get() == ""

        # LiveMonitorFrame initialization & prior card state transitions
        lm = app.live_monitor_frame
        assert isinstance(lm, LiveMonitorFrame)
        lm.prior_source = str(prior_dir)
        lm.continuity_mode = "auto"
        lm._update_prior_card({
            "enabled": True,
            "status": "overlap_verified",
            "mode": "auto",
            "source_name": "prior_ui",
            "matched_roles": ["total"],
            "missing_roles": [],
            "overlap_count": 5,
            "recovered_count": 0,
            "summary_text": "Overlap: 5 steps verified",
        })
        assert lm.lbl_prior_badge.cget("text") == "Stitched"
        lm._clear_live_prior()
        assert lm.prior_source is None
        assert lm.lbl_prior_badge.cget("text") == "None"
    finally:
        try:
            app.destroy()
        except Exception:
            pass

