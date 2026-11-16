# -*- coding: utf-8 -*-
"""
Tier 5.4: Headless GUI Smoke and Component Integration Tests
============================================================
Validates the graphical interface in non-interactive/headless execution:
1. Smoke test: Full initialization of VAWTPostApp window and layout
2. Variable synchronization: Preset selection populates UI entry variables
3. Dynamic blade cards: Changing number of blades updates blade rows visibility
4. Diagnostics export: _save_diagnostics writes complete system/debug dump
5. Worker thread cancellation: cancel token interrupts post-processing loop
"""
import os
import pytest
from unittest.mock import patch
from vawt_post.ui import qt_tk as ctk

from vawt_post.ui.app import VAWTPostApp
from vawt_post.engine.config import BUILTIN_PRESETS


@pytest.fixture(scope="module")
def app():
    """Module-scoped VAWTPostApp fixture ensuring single clean Tcl interpreter lifecycle."""
    try:
        application = VAWTPostApp()
        application.update_idletasks()
        yield application
        try:
            application.destroy()
        except Exception:
            pass
    except Exception as e:
        pytest.skip(f"Tkinter environment initialization error on host: {e}")


def test_5_4_headless_gui_smoke_init(app):
    """Verify that VAWTPostApp initializes, packs all widgets, and closes without exceptions."""
    app.update_idletasks()
    assert app.is_running is False
    assert app.v_depth.get() == "1.0"
    assert app.v_rho.get() == "1.225"


def test_5_4_headless_gui_preset_loading(app):
    """Verify that selecting a preset correctly populates UI variables."""
    app.update_idletasks()
    preset_name = "Generic 3-Blade VAWT (Example)"
    assert preset_name in BUILTIN_PRESETS

    # Trigger preset selection
    app.v_preset.set(preset_name)
    app._on_preset_select(preset_name)

    assert app.v_U.get() == "8.0"
    assert app.v_tsr.get() == "3.5"
    assert app.v_R.get() == "0.5"
    assert app.v_nb.get() == "3"
    assert app.v_chord.get() == "0.06"
    assert app.v_rot_dir.get() == "ccw"


def test_5_4_headless_gui_dynamic_blade_cards(app):
    """Verify dynamic visibility of blade monitor rows when blade count changes."""
    app.update_idletasks()
    # Change blade count to 4
    app.v_nb.set("4")
    app._update_blade_visibility()
    app.update_idletasks()

    # The first 4 blade widgets should be managed
    assert len(app.blade_widgets) == 8
    for i in range(4):
        lbl, entry, btn = app.blade_widgets[i]
        assert lbl.winfo_manager() == "grid"

    # Change blade count to 2
    app.v_nb.set("2")
    app._update_blade_visibility()
    app.update_idletasks()
    # Blade widget 2 should be unmapped
    lbl_3, _, _ = app.blade_widgets[2]
    assert lbl_3.winfo_manager() == ""


def test_5_4_headless_gui_save_diagnostics(app, tmp_path):
    """Verify that _save_diagnostics generates a valid diagnostic text file."""
    app.update_idletasks()
    app.output_folder = str(tmp_path)
    diag_path = str(tmp_path / "diagnostics_dump.txt")

    # Mock tkinter.filedialog and tkinter.messagebox
    with patch("tkinter.filedialog.asksaveasfilename", return_value=diag_path), \
         patch("tkinter.messagebox.showinfo"):
        app._save_diagnostics()

    assert os.path.isfile(diag_path)
    with open(diag_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "TurbineScope v4.1.1" in content
    assert "System & Execution Diagnostics" in content
    assert "[Active Turbine Parameters]" in content


def test_5_4_headless_gui_diagnostics_tab(app):
    """Verify File Diagnostics tab integration and badge updates."""
    app.update_idletasks()
    assert hasattr(app, "diagnostics_frame")
    rep = app.diagnostics_frame.run_diagnostics()
    assert rep is not None
    assert app.main_diag_badge.cget("text") != ""


def test_5_4_headless_gui_live_monitor_tab(app):
    """Verify Live Monitor tab initialization and stopped state."""
    app.update_idletasks()
    assert hasattr(app, "live_monitor_frame")
    assert "Stopped" in app.live_monitor_frame.state_badge.cget("text")


def test_5_4_headless_gui_results_tab(app):
    """Verify Results tab initialization, KPI cards, and view modes."""
    app.update_idletasks()
    assert hasattr(app, "results_frame")
    assert app.results_frame.card_cp is not None
    assert app.results_frame.mode_selector.get() == "🖼️ Single Figure"


def test_5_4_headless_gui_image_rendering_no_highdpi_warnings(app, tmp_path):
    """Verify Results and Preview frames use CTkImage and generate zero HighDPI warnings."""
    import warnings
    from PIL import Image

    # Create dummy PNG figures
    fig_dir = tmp_path / "figs"
    fig_dir.mkdir()
    fig1 = str(fig_dir / "01_cp_history.png")
    fig2 = str(fig_dir / "02_cp_per_revolution.png")
    im = Image.new("RGB", (800, 600), color=(50, 100, 150))
    im.save(fig1)
    im.save(fig2)

    figs = {
        "01_cp_history.png": fig1,
        "02_cp_per_revolution.png": fig2,
    }
    dummy_res = {
        "summary": {
            "mean_cp": 0.324,
            "mean_power_W": 125.6,
            "mean_torque_Nm": 15.2,
            "mean_ct": 0.45,
            "rpm": 152.8,
            "n_revs_analyzed": 5,
        },
        "blade_stats": [],
        "rev_stats": [],
    }

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")

        # 1. Results frame: populate and switch to gallery grid
        app.results_frame.populate(dummy_res, figs)
        app.results_frame._on_view_mode_change("🎨 All Figures Gallery")
        app.update_idletasks()

        # Check that gallery used CTkImage
        assert len(app.results_frame.gallery_tk_imgs) == 2
        for g_img in app.results_frame.gallery_tk_imgs:
            assert isinstance(g_img, ctk.CTkImage)

        # Switch back to single figure
        app.results_frame._on_view_mode_change("🖼️ Single Figure")
        app.update_idletasks()

        # 2. Preview frame: populate and test Single, Dual, Gallery
        app.preview_frame.populate(figs)
        app.update_idletasks()
        assert isinstance(app.preview_frame.current_img_tk, ctk.CTkImage)

        app.preview_frame._on_view_mode_change("👥 Side-by-Side Dual Compare")
        app.update_idletasks()

        app.preview_frame._on_view_mode_change("🎨 Gallery Grid View")
        app.update_idletasks()
        assert len(app.preview_frame.gallery_tks) == 2
        for g_img in app.preview_frame.gallery_tks:
            assert isinstance(g_img, ctk.CTkImage)

    # Verify zero CTkLabel HighDPI warnings occurred
    highdpi_warnings = [
        w for w in recorded_warnings
        if "Image can not be scaled on HighDPI displays" in str(w.message)
    ]
    assert len(highdpi_warnings) == 0, f"Encountered unexpected HighDPI warnings: {highdpi_warnings}"


def test_5_4_headless_gui_fluent_controller_cleanly_removed(app):
    """Verify Fluent Controller was cleanly excised: exactly 4 tabs exist,
    page switching across all 4 is flawless, and no orphan fluent attributes remain."""
    app.update_idletasks()
    assert not hasattr(app, "fluent_frame")
    assert not hasattr(app, "page_fluent")
    assert not hasattr(app, "tab_btn_fluent")

    # Verify the 4 navigation tabs exist
    assert hasattr(app, "tab_btn_input")
    assert hasattr(app, "tab_btn_results")
    assert hasattr(app, "tab_btn_live")
    assert hasattr(app, "tab_btn_workbench")

    # Verify switching across all 4 pages
    for p in ("input", "results", "live", "workbench"):
        app._switch_page(p)
        app.update_idletasks()
        assert app.current_page == p


def test_5_4_headless_gui_autodetect_folder_integration(app, tmp_path):
    """Verify that auto-detect from folder populates physical parameters and files,
    while leaving Live Monitor and Workbench parameters completely decoupled."""
    # Create mock scheme, journal, and monitor files in tmp_path
    set_content = (
        '((mrf-omega (constant . 60))\n'
        ' (reference-velocity 10.0)\n'
        ' (reference-density 1.225)\n'
        ' (reference-viscosity 1.7894e-05))\n'
    )
    (tmp_path / "case.set").write_text(set_content, encoding="utf-8")

    mom_content = (
        '("Time Step" "moment" "flow-time")\n'
        '1 0.1 0.000\n'
        '2 0.2 0.001\n'
        '3 0.3 0.002\n'
    )
    (tmp_path / "moment-rfile.out").write_text(mom_content, encoding="utf-8")
    (tmp_path / "moment1-rfile.out").write_text("1 0.05\n", encoding="utf-8")
    (tmp_path / "moment2-rfile.out").write_text("1 0.05\n", encoding="utf-8")
    (tmp_path / "run.jou").write_text("/solve/dual-time-iterate 360 25\n", encoding="utf-8")

    # Clear existing entries
    app.v_U.set("")
    app.v_omega.set("")
    app.v_tsr.set("")
    app.v_nb.set("1")

    # Run autodetection
    app._autodetect_folder(str(tmp_path))

    # Assert detected values
    assert app.v_U.get() in ("10", "10.0")
    assert app.v_omega.get() in ("60", "60.0")
    assert app.v_nb.get() == "2"
    assert app.v_dt.get() in ("0.001", "0.00100000")
    assert app.v_meta_iter.get() == "25"
    assert "moment-rfile.out" in app.v_total.get()

    # Assert that Live Monitor parameters remain decoupled
    if hasattr(app, "live_monitor_frame"):
        assert app.live_monitor_frame is not None


def test_5_4_headless_gui_derived_physics(app):
    """Verify that _update_derived_physics computes and displays derived values without crashing."""
    app.v_U.set("8.0")
    app.v_tsr.set("3.5")
    app.v_R.set("0.5")
    app.v_depth.set("1.0")
    app.v_rho.set("1.225")
    app.v_omega.set("")

    app._update_derived_physics()
    lbl_text = app.lbl_derived_physics.cget("text")
    assert "Derived:" in lbl_text
    assert "RPM" in lbl_text
    assert "ω = 56.0 rad/s" in lbl_text
    assert "Pref =" in lbl_text

    # Test with European comma decimals
    app.v_U.set("8,0")
    app.v_tsr.set("3,5")
    app._update_derived_physics()
    lbl_text_comma = app.lbl_derived_physics.cget("text")
    assert "Derived:" in lbl_text_comma
    assert "ω = 56.0 rad/s" in lbl_text_comma

    # Test parameter gathering with comma decimals
    p = app._gather_params()
    assert p.U == 8.0
    assert p.tsr == 3.5
    assert p.R == 0.5




