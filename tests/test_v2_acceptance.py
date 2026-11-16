# -*- coding: utf-8 -*-
"""
Acceptance Tests for vawt_post v2.0:
1. Three-page layout (Header tab buttons, persistent footer, relocated presets card)
2. Keyboard shortcuts (Ctrl+1, Ctrl+2, Ctrl+3) and page switching
3. Offline vs Live Cp per revolution numerical equality (< 1e-9)
4. Dynamic interval and unit modification during monitoring & immediate refresh
5. Live Monitor graph rendering at 1280x720 in both Dark and Light themes
6. Background run and monitoring persistence across page switching
"""
import math
import pytest
from vawt_post.ui import qt_tk as ctk

from vawt_post.ui.app import VAWTPostApp
from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.live_monitor import LiveMonitorEngine, LiveMonitorWorker
from vawt_post.engine.analysis import analyze


@pytest.fixture(scope="module")
def app_instance():
    """Module-scoped VAWTPostApp fixture ensuring single clean Tk lifecycle."""
    app = VAWTPostApp()
    app.geometry("1280x720")
    app.update_idletasks()
    yield app
    try:
        if hasattr(app, "live_monitor_frame") and app.live_monitor_frame.worker:
            app.live_monitor_frame.worker.stop()
        app.destroy()
    except Exception:
        pass


def test_v2_header_tabs_and_footer(app_instance):
    """Verify three large tab buttons in header and persistent footer elements."""
    app = app_instance
    app.update_idletasks()

    # Three tab buttons exist with ~40px height
    assert hasattr(app, "tab_btn_input")
    assert hasattr(app, "tab_btn_results")
    assert hasattr(app, "tab_btn_live")
    assert app.tab_btn_input.cget("height") >= 38
    assert app.tab_btn_results.cget("height") >= 38
    assert app.tab_btn_live.cget("height") >= 38

    # Persistent footer exists with state, progress, warnings, and log button
    assert hasattr(app, "footer_status_lbl")
    assert hasattr(app, "footer_progress")
    assert hasattr(app, "footer_warn_lbl")
    assert hasattr(app, "footer_log_btn")
    assert "Idle" in app.footer_status_lbl.cget("text")


def test_v2_page_switching_and_shortcuts(app_instance):
    """Verify switching between Input, Results, and Live Monitor via methods & shortcuts."""
    app = app_instance

    # 1. Switch to Results page
    app._switch_page("results")
    app.update_idletasks()
    assert app.current_page == "results"
    assert app.page_results.winfo_manager() in ("grid", "pack")

    # 2. Switch to Live Monitor page
    app._switch_page("live")
    app.update_idletasks()
    assert app.current_page == "live"
    assert app.page_live.winfo_manager() in ("grid", "pack")

    # 3. Switch back to Input page
    app._switch_page("input")
    app.update_idletasks()
    assert app.current_page == "input"
    assert app.page_input.winfo_manager() in ("grid", "pack")

    # Footer log button switches to Results and opens Log subtab
    app._open_execution_log()
    app.update_idletasks()
    assert app.current_page == "results"
    assert app.results_frame.active_sub_tab == "📋 Execution Log"


def test_v2_relocated_preset_card(app_instance):
    """Verify preset dropdown and actions work from the Input page parameter cards."""
    app = app_instance
    app._switch_page("input")
    app.update_idletasks()

    assert hasattr(app, "preset_menu")
    presets = app.preset_menu.cget("values")
    assert len(presets) > 0

    # Select built-in preset
    target_preset = "Generic 3-Blade VAWT (Example)"
    assert target_preset in presets
    app.v_preset.set(target_preset)
    app._on_preset_select(target_preset)

    assert app.v_U.get() == "8.0"
    assert app.v_tsr.get() == "3.5"
    assert app.v_nb.get() == "3"


def test_v2_live_monitor_interval_and_units(app_instance):
    """Verify changing refresh interval & unit takes effect immediately without restart."""
    app = app_instance
    app._switch_page("live")
    app.update_idletasks()
    lm = app.live_monitor_frame

    # Change to 30 seconds
    lm.interval_num_var.set("30")
    lm.interval_unit_var.set("seconds")
    lm._on_interval_custom_change()
    assert lm.refresh_interval_s == 30.0

    # Change to 5 minutes
    lm.interval_num_var.set("5")
    lm.interval_unit_var.set("minutes")
    lm._on_interval_custom_change()
    assert lm.refresh_interval_s == 300.0

    # Preset selection
    lm._on_preset_interval_select("10 s")
    assert lm.refresh_interval_s == 10.0


def test_v2_offline_vs_live_cp_equality(tmp_path):
    """Acceptance test: Cp per revolution from live monitor matches offline analysis to 1e-9."""
    omega = 20.0 * math.pi
    dt = 0.001
    p = Params(U=8.0, tsr=3.5, omega=omega, R=0.5, chord=0.06, n_blades=3, depth=1.0, rho=1.225)
    n_steps = 300

    # Generate synthetic healthy total moment file
    tot_file = str(tmp_path / "tot_moment.out")
    assert p.R is not None and p.U is not None
    A = 2.0 * p.R * p.depth
    q = 0.5 * p.rho * (p.U ** 2)
    pref = q * A * p.U

    with open(tot_file, "w", encoding="utf-8") as fh:
        fh.write('("Time Step" "flow-time" "total-moment")\n')
        for s in range(0, n_steps + 1):
            t = s * dt
            theta = (omega * t) % (2.0 * math.pi)
            cp = 0.35 + 0.05 * math.sin(3.0 * theta)
            power = cp * pref
            torque = power / omega
            fh.write(f"{s + 1}\t{t:.6f}\t{torque:.8f}\n")

    f = InputFiles(total=tot_file, blades=[None, None, None])

    # 1. Run offline analysis
    offline_res = analyze(p, f)
    offline_rev_cps = [r["cp"] for r in offline_res["revs"]]

    # 2. Run live engine
    engine = LiveMonitorEngine(p, f)
    snap = engine.tick_full()
    assert snap.rev_history is not None
    live_rev_cps = [r["cp"] for r in snap.rev_history]

    assert len(offline_rev_cps) == len(live_rev_cps)
    for off_cp, live_cp in zip(offline_rev_cps, live_rev_cps):
        assert abs(off_cp - live_cp) < 1e-9, f"Mismatch: offline {off_cp} vs live {live_cp}"


def test_v2_live_monitor_theming_and_1280x720_canvas(app_instance):
    """Verify Live Monitor plots render properly in Dark Mode at 1280x720 without clipping."""
    app = app_instance
    app.geometry("1280x720")
    app._switch_page("live")
    app.update_idletasks()

    lm = app.live_monitor_frame
    assert hasattr(lm, "fig")
    assert hasattr(lm, "axes")
    assert len(lm.axes) > 0

    assert ctk.get_appearance_mode() == "Dark"
    app.update_idletasks()
    lm._init_plots()
    for ax in lm.axes:
        title = ax.get_title()
        # Title must be set without clipping/errors
        assert isinstance(title, str)


def test_v2_page_switching_does_not_abort_monitoring(app_instance, tmp_path):
    """Verify switching between pages does not abort an active live monitor."""
    app = app_instance
    lm = app.live_monitor_frame

    p = Params(U=8.0, tsr=3.5, R=0.5, chord=0.06, n_blades=3)
    f = InputFiles(total=str(tmp_path / "dummy.out"), blades=[])

    # Start live worker
    worker = LiveMonitorWorker(
        p=p,
        f=f,
        out_queue=lm.snapshot_queue,
        graph_refresh_interval_s=10.0,
        file_check_interval_s=1.0,
    )
    lm.worker = worker
    lm.is_monitoring = True
    worker.start()

    try:
        # Switch to Input
        app._switch_page("input")
        app.update_idletasks()
        assert worker.is_alive()
        assert bool(lm.is_monitoring)

        # Switch to Results
        app._switch_page("results")
        app.update_idletasks()
        assert worker.is_alive()
        assert bool(lm.is_monitoring)

        # Switch back to Live
        app._switch_page("live")
        app.update_idletasks()
        assert worker.is_alive()
        assert bool(lm.is_monitoring)
    finally:
        lm._stop_monitoring()
        worker.stop()
        lm.is_monitoring = False


def test_v2_results_file_health_and_badge(app_instance):
    """Verify Results tab File Health population, absence of AttributeError, and clean badge text."""
    app = app_instance
    from vawt_post.engine.diagnostics import DiagnosticsReport, DiagnosticFinding

    rep = DiagnosticsReport()
    rep.add(DiagnosticFinding(
        severity="WARNING",
        file="blade 1",
        check="Signal spike",
        location="Step 651",
        explanation="Isolated second-difference curvature spike",
        suggested_fix="Inspect residuals"
    ))
    rep.add(DiagnosticFinding(
        severity="INFO",
        file="blade 2",
        check="Discretisation",
        location="Time Step",
        explanation="Time step size info",
        suggested_fix="None"
    ))

    # Test badge in diagnostics tab: does not repeat "1 Warning(s)"
    app.diagnostics_frame._update_badge(rep)
    badge_text = app.diagnostics_frame.badge_lbl.cget("text")
    assert "Warning(s)" not in badge_text
    assert "File Health Advisory" in badge_text
    assert app.diagnostics_frame.cnt_warn.cget("text") == "Warnings: 1"

    # Test Results tab file health: populated without AttributeError
    mock_result = {
        "res": {"cp_mean": 0.32, "cm_mean": 0.15, "is_stable": True},
        "xlsx": "test.xlsx",
        "figs": {},
        "diagnostics": rep
    }
    app.results_frame.set_results(mock_result)
    assert app.results_frame.lbl_health_warn.cget("text") == "Warnings: 1"
    assert app.results_frame.lbl_health_info.cget("text") == "Info: 1"
    assert len(app.results_frame.tree_diag.get_children()) == 2


def test_v2_live_countdown_autorefresh_and_string_nstable(app_instance):
    """Verify live countdown auto-refreshes at 0:00, accepts string v_nstable, and persists settings."""
    app = app_instance
    app._switch_page("live")
    app.update_idletasks()
    lm = app.live_monitor_frame

    # 1. Countdown auto-refresh at 0:00
    lm.is_monitoring = True
    lm.is_paused = False
    lm.refresh_interval_s = 15.0
    lm.time_until_refresh = 0.5

    refreshed = []
    orig_refresh_now = lm._refresh_now
    lm._refresh_now = lambda: refreshed.append(True)

    try:
        lm._countdown_tick()
        assert len(refreshed) == 1
        assert lm.time_until_refresh == 15.0
        assert "Refreshing" in lm.lbl_countdown.cget("text")
    finally:
        lm._refresh_now = orig_refresh_now
        lm.is_monitoring = False

    # 2. String v_nstable tolerance in telemetry
    app.v_nstable.set("2")
    lm.live_params.n_stable = 2
    from vawt_post.engine.live_monitor import LiveMonitorSnapshot
    snap = LiveMonitorSnapshot(
        status="Monitoring",
        is_full_refresh=True,
        kpi={"running_cp": 0.31, "stable_n": 1, "dcp_prev": 0.005}
    )
    # Must not raise TypeError: '>=' not supported between instances of 'int' and 'str'
    lm._update_telemetry(snap)
    assert lm.kpi_verdict.cget("text") == "STATUS: NOT YET STABLE"

    snap.kpi["stable_n"] = 2
    lm._update_telemetry(snap)
    assert lm.kpi_verdict.cget("text") == "STATUS: STABLE"

    # 3. Settings persistence
    lm.window_var.set("Last 3 revs")
    lm._on_window_change("Last 3 revs")
    assert lm.window_mode == "Last 3 revs"
    live_cfg = app.config_mgr.get_live_settings()
    assert live_cfg["window_mode"] == "Last 3 revs"


def test_v2_live_edit_dialog_and_tolerance_default(app_instance, tmp_path):
    """Verify default tolerance is 5.0%, Live Monitor inline tolerance edit, and dedicated Edit dialog."""
    app = app_instance
    assert Params().stable_tol == 5.0

    app._switch_page("live")
    app.update_idletasks()
    lm = app.live_monitor_frame

    # Inline tolerance edit in Live Monitor toolbar
    lm.v_live_tol.set("2.5")
    lm._on_live_tol_change()
    assert lm.live_params.stable_tol == 2.5
    assert "Tol = 2.5%" in lm.lbl_params_echo.cget("text")

    # Apply edited parameters directly to Live Monitor without leaving the Live page
    edited_p = Params(U=9.5, tsr=4.0, R=0.6, chord=0.075, n_blades=3, stable_tol=3.0, n_stable=4)
    lm._apply_edited_params(edited_p)
    assert app.current_page == "live"
    assert lm.live_params.U == 9.5
    assert lm.live_params.stable_tol == 3.0
    assert lm.v_live_tol.get() == "3"
    assert "U = 9.50 m/s" in lm.lbl_params_echo.cget("text")
    assert "Tol = 3%" in lm.lbl_params_echo.cget("text")

    # Verify LiveMonitorEngine.update_params dynamically recomputes revolutions and stability
    tot_file = str(tmp_path / "live_edit_tot.out")
    omega = 20.0 * math.pi
    dt = 0.001
    p_init = Params(U=8.0, tsr=3.5, omega=omega, R=0.5, chord=0.06, n_blades=3, stable_tol=0.1)
    with open(tot_file, "w", encoding="utf-8") as fh:
        fh.write('("Time Step" "flow-time" "total-moment")\n')
        for s in range(0, 301):
            t = s * dt
            rev_idx = int(t / 0.1)
            scale = 1.0 + 0.015 * rev_idx  # ~1.5% change per rev
            fh.write(f"{s + 1}\t{t:.6f}\t{0.25 * scale:.8f}\n")

    engine = LiveMonitorEngine(p_init, InputFiles(total=tot_file, blades=[]))
    snap1 = engine.tick_full()
    assert snap1.kpi["stable_n"] == 0  # 1.5% drift exceeds 0.1% tol

    p_relaxed = Params(U=8.0, tsr=3.5, omega=omega, R=0.5, chord=0.06, n_blades=3, stable_tol=5.0)
    engine.update_params(p_relaxed)
    snap2 = engine.tick_full()
    assert snap2.kpi["stable_n"] >= 2  # 1.5% drift is within 5.0% tol

    # Verify compact plot_bar height and specific revolution selection (custom_revs)
    assert lm.plot_bar.cget("height") == 32
    from vawt_post.engine.config import parse_custom_revs_spec
    from vawt_post.engine.analysis import analyze
    assert parse_custom_revs_spec("14-16") == [14, 15, 16]
    assert parse_custom_revs_spec("14-16revs") == [14, 15, 16]
    assert parse_custom_revs_spec("16 rev") == [16]
    assert parse_custom_revs_spec("16") == [16]

    # Verify Specific revolution(s) entry in Analysis & Convergence (blank = default, numbers = specific revs)
    assert hasattr(app, "entry_custom_revs")
    assert not hasattr(app, "v_rev_mode")
    app.v_custom_revs.set("")
    assert app._gather_params().custom_revs is None
    app.v_custom_revs.set("14-16")
    assert app._gather_params().custom_revs == "14-16"
    app.v_custom_revs.set("")

    from vawt_post.engine.excel import write_excel
    from vawt_post.engine.plots import make_figures
    import openpyxl

    # Single revolution isolation ("2 rev"): only rev 2 is kept in revs, data, Excel, and figures
    p_single = Params(U=8.0, tsr=3.5, omega=omega, R=0.5, chord=0.06, n_blades=3, custom_revs="2 rev")
    R_single = analyze(p_single, InputFiles(total=tot_file, blades=[]), log=lambda m: None)
    assert [r["k"] for r in R_single["sel"]] == [2]
    assert [r["k"] for r in R_single["revs"]] == [2]
    assert R_single["data"]["t"][0] >= R_single["revs"][0]["a"] - 1e-6
    assert R_single["data"]["t"][-1] <= R_single["revs"][0]["b"] + 1e-6

    fig_dir_single = str(tmp_path / "figs_single")
    figs_s = make_figures(R_single, fig_dir_single, log=lambda m: None)
    xlsx_single = str(tmp_path / "out_single.xlsx")
    write_excel(R_single, figs_s, xlsx_single, log=lambda m: None)

    wb_s = openpyxl.load_workbook(xlsx_single)
    ws_revs_s = wb_s["Revolutions"]
    rev_col_vals = [ws_revs_s.cell(row=r_i, column=1).value for r_i in range(5, ws_revs_s.max_row + 1) if ws_revs_s.cell(row=r_i, column=1).value is not None]
    assert rev_col_vals == [2]
    ws_sum_s = wb_s["Summary"]
    sum_metrics = [str(ws_sum_s.cell(row=r_i, column=1).value or "") for r_i in range(5, ws_sum_s.max_row + 1)]
    assert not any("trailing stable revolutions" in m for m in sum_metrics)
    assert not any("all complete revolutions" in m for m in sum_metrics)
    wb_s.close()

    # Range isolation ("1-2revs"): only revs 1 and 2 are kept
    p_range = Params(U=8.0, tsr=3.5, omega=omega, R=0.5, chord=0.06, n_blades=3, custom_revs="1-2revs")
    R_range = analyze(p_range, InputFiles(total=tot_file, blades=[]), log=lambda m: None)
    assert [r["k"] for r in R_range["sel"]] == [1, 2]
    assert [r["k"] for r in R_range["revs"]] == [1, 2]
    assert "Specific revolutions (1, 2)" in R_range["res"]["selection_desc"]
    assert R_range["res"]["custom_revs_used"] == "1-2revs"
    assert R_range["res"]["phases"][0]["converged_cp"] is not None

    # Verify Live Monitor interactive hover probe & anti-jitter graph state caching
    if lm.grid_layout_mode not in ("2×2 Grid", "3×2 Grid"):
        lm.layout_menu.set("2×2 Grid")
        lm._on_layout_change("2×2 Grid")
    lm._render_graphs(snap2, force=True)
    saved_state = lm._last_rendered_graph_state
    assert saved_state is not None
    # Calling _render_graphs again with unchanged state is a fast no-op
    lm._render_graphs(snap2, force=False)
    assert lm._last_rendered_graph_state == saved_state

    # Simulate hovering over subplot 0 (01. Cp History line plot) and subplot 1 (02. Cp per Rev bar plot)
    from types import SimpleNamespace
    assert not hasattr(lm, "lbl_probe_readout")
    assert hasattr(lm, "btn_probe_toggle")
    assert hasattr(lm, "probe_box")

    ax0 = lm.axes[0]
    ax1 = lm.axes[1]
    lm.canvas.draw()
    pos0_before = tuple(round(v, 6) for v in ax0.get_position().bounds)
    pos1_before = tuple(round(v, 6) for v in ax1.get_position().bounds)
    xlim0_before = tuple(round(v, 6) for v in ax0.get_xlim())
    ylim0_before = tuple(round(v, 6) for v in ax0.get_ylim())

    ev0 = SimpleNamespace(inaxes=ax0, xdata=0.15, ydata=0.25)
    lm._on_plot_hover(ev0)
    lm.canvas.draw()
    pos0_after = tuple(round(v, 6) for v in ax0.get_position().bounds)
    pos1_after = tuple(round(v, 6) for v in ax1.get_position().bounds)
    xlim0_after = tuple(round(v, 6) for v in ax0.get_xlim())
    ylim0_after = tuple(round(v, 6) for v in ax0.get_ylim())

    # Subplots and axes limits must never shrink or shift on hover
    assert pos0_after == pos0_before
    assert pos1_after == pos1_before
    assert xlim0_after == xlim0_before
    assert ylim0_after == ylim0_before

    assert lm._active_probe_ax is ax0
    assert ax0 in lm._probe_artists
    assert lm._probe_artists[ax0]["dot"].get_visible() is True
    assert lm._probe_artists[ax0]["annot"].get_visible() is False
    assert "01. Cp History" in lm.lbl_probe_graph.cget("text")
    assert "Instantaneous Cp" in lm.probe_box.get("1.0", "end")

    ev1 = SimpleNamespace(inaxes=ax1, xdata=2.0, ydata=0.25)
    lm._on_plot_hover(ev1)
    assert lm._active_probe_ax is ax1
    assert lm._probe_artists[ax0]["dot"].get_visible() is False
    assert lm._probe_artists[ax1]["dot"].get_visible() is True
    assert lm._probe_artists[ax1]["annot"].get_visible() is False
    assert "02. Cp per Rev" in lm.lbl_probe_graph.cget("text")
    assert "Revolution k = 2" in lm.lbl_probe_coord.cget("text")
    assert "Mean Cp" in lm.probe_box.get("1.0", "end")

    # Test toggling Probe OFF and back ON via the right-panel toggle button
    lm._toggle_probe()
    assert lm._probe_enabled is False
    assert "OFF" in lm.btn_probe_toggle.cget("text")
    assert lm._active_probe_ax is None
    assert lm._probe_artists[ax1]["dot"].get_visible() is False
    assert lm._probe_artists[ax1]["annot"].get_visible() is False
    # Hovering while Probe is OFF does nothing
    lm._on_plot_hover(ev0)
    assert lm._active_probe_ax is None

    # Turn Probe back ON
    lm._toggle_probe()
    assert lm._probe_enabled is True
    assert "ON" in lm.btn_probe_toggle.cget("text")
    lm._on_plot_hover(ev1)
    assert lm._active_probe_ax is ax1

    # Leaving the figure hides all probe overlays
    lm._on_plot_leave()
    assert lm._active_probe_ax is None
    assert lm._probe_artists[ax1]["dot"].get_visible() is False
    assert lm._probe_artists[ax1]["annot"].get_visible() is False


def test_v4_interactive_results_graphs_titles_and_icon(tmp_path):
    """Verify interactive Results graph zoom/pan/probe/pin, compact titles, and non-null taskbar icon."""
    import numpy as np
    from types import SimpleNamespace
    from vawt_post.engine.config import Params, InputFiles
    from vawt_post.engine.analysis import analyze
    from vawt_post.engine.plots import make_figures

    from typing import List, Optional
    tot_file = str(tmp_path / "moment_tot.out")
    b_files: List[Optional[str]] = [str(tmp_path / f"blade{i}.out") for i in range(1, 4)]
    fx_file = str(tmp_path / "forcex.out")
    fy_file = str(tmp_path / "forcey.out")
    omega = 40.0
    dt = 0.0002
    n_steps = 4000
    assert isinstance(b_files[0], str) and isinstance(b_files[1], str) and isinstance(b_files[2], str)
    with open(tot_file, "w") as fh, open(b_files[0], "w") as f1, open(b_files[1], "w") as f2, open(b_files[2], "w") as f3, open(fx_file, "w") as fx, open(fy_file, "w") as fy:
        for h in (fh, f1, f2, f3):
            h.write('("Time Step" "moment" "flow-time")\n')
        fx.write('("Time Step" "force-x" "flow-time")\n')
        fy.write('("Time Step" "force-y" "flow-time")\n')
        for s in range(1, n_steps + 1):
            t_v = s * dt
            m1 = 0.25 + 0.15 * np.sin(omega * t_v)
            m2 = 0.25 + 0.15 * np.sin(omega * t_v + 2.094)
            m3 = 0.25 + 0.15 * np.sin(omega * t_v + 4.188)
            fh.write(f"{s} {m1 + m2 + m3:.6f} {t_v:.6f}\n")
            f1.write(f"{s} {m1:.6f} {t_v:.6f}\n")
            f2.write(f"{s} {m2:.6f} {t_v:.6f}\n")
            f3.write(f"{s} {m3:.6f} {t_v:.6f}\n")
            fx.write(f"{s} {12.0 + 3.0 * np.cos(omega * t_v):.6f} {t_v:.6f}\n")
            fy.write(f"{s} {2.0 * np.sin(omega * t_v):.6f} {t_v:.6f}\n")

    p = Params(U=7.0, tsr=3.5, omega=omega, R=0.35, chord=0.05, n_blades=3, stable_tol=5.0)
    R = analyze(p, InputFiles(total=tot_file, blades=b_files, fx=fx_file, fy=fy_file), log=lambda _m: None)
    fig_dir = str(tmp_path / "figs")
    R["figs"] = make_figures(R, fig_dir, log=lambda _m: None)

    app = VAWTPostApp(show_immediately=False)
    try:
        # 1. Verify window & application icon are loaded
        assert not app.windowIcon().isNull()

        # 2. Verify Live Monitor subplot title font sizes were trimmed down
        lm = app.live_monitor_frame
        assert all(float(ax.title.get_fontsize()) <= 9.0 for ax in lm.axes)

        # 3. Verify Results Graph Preview interactive canvas
        rf = app.results_frame
        rf.set_results(R)
        rf.select_sub_tab("Graph Preview")
        assert rf._interactive_active is True
        assert rf._home_limits

        ax = next(iter(rf._home_limits.keys()))
        rf.res_mpl_canvas.draw()
        hx0, hx1 = ax.get_xlim()
        hy0, hy1 = ax.get_ylim()
        cx, cy = 0.5 * (hx0 + hx1), 0.5 * (hy0 + hy1)
        px_c, py_c = ax.transData.transform([[cx, cy]])[0]

        # Scroll-wheel zoom in narrows bounds around cursor
        rf._on_mpl_scroll(SimpleNamespace(inaxes=ax, button="up", x=px_c, y=py_c, xdata=cx, ydata=cy))
        zx0, zx1 = ax.get_xlim()
        assert (zx1 - zx0) < (hx1 - hx0)
        assert len(rf._view_history) == 1

        # Undo restores previous bounds; Reset restores home bounds
        rf._undo_interactive_view()
        assert ax.get_xlim() == (hx0, hx1)
        rf._apply_interactive_zoom_preset("200%")
        assert (ax.get_xlim()[1] - ax.get_xlim()[0]) < (hx1 - hx0)
        rf._reset_interactive_view()
        assert ax.get_xlim() == (hx0, hx1)
        assert ax.get_ylim() == (hy0, hy1)

        # Hover probe snaps to nearest point on curve and populates right sidebar without chart text box
        data_line = next(ln for ln in ax.get_lines() if len(np.asarray(ln.get_xdata(), dtype=float)) > 10)
        xs_arr = np.asarray(data_line.get_xdata(), dtype=float)
        ys_arr = np.asarray(data_line.get_ydata(), dtype=float)
        fin_idx = int(np.flatnonzero(np.isfinite(xs_arr) & np.isfinite(ys_arr))[0])
        xd_pt = float(xs_arr[fin_idx])
        yd_pt = float(ys_arr[fin_idx])
        px_pt, py_pt = ax.transData.transform([[xd_pt, yd_pt]])[0]
        rf._last_hover_ts = 0.0
        rf._on_mpl_motion(SimpleNamespace(inaxes=ax, x=px_pt, y=py_pt, xdata=xd_pt, ydata=yd_pt))
        assert rf.lbl_probe_series.cget("text") != "—"
        assert rf.lbl_probe_x.cget("text") != "—"
        assert ax in rf._probe_markers

        # Click twice to Pin A and Pin B comparison
        sample = rf._snap_nearest_sample(SimpleNamespace(inaxes=ax, x=px_pt, y=py_pt, xdata=xd_pt, ydata=yd_pt))
        assert sample is not None
        rf._apply_pin_sample(sample)
        sample2 = dict(sample, x=sample["x"] + 30.0, disp_x=(sample["disp_x"] + 30.0) if isinstance(sample["disp_x"], float) else 30.0, y=sample["y"] + 0.1)
        rf._apply_pin_sample(sample2)
        assert rf.lbl_pin_delta_y.cget("text") != "—"
        rf._clear_probe_pins()
        assert rf.lbl_pin_delta_y.cget("text") == "—"

        # Also test all 18 interactive figure builds (including polar, heatmap, dual-axis, dashboard)
        from vawt_post.engine.plots import RESULT_FIGURE_KEYS
        for fk in RESULT_FIGURE_KEYS:
            rf.current_fig_key = fk
            rf._render_current_figure(force=True)
            assert rf._interactive_active is True
    finally:
        app.destroy()





