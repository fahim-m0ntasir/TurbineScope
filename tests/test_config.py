# -*- coding: utf-8 -*-
"""
Tests for ConfigManager: parameter persistence, presets, and program defaults.
"""
from vawt_post.engine.config import ConfigManager, Params, InputFiles


def test_config_user_defaults(tmp_path):
    cfg_file = str(tmp_path / "test_cfg.json")
    mgr1 = ConfigManager(config_path=cfg_file)

    # Save a custom user default
    custom_p = Params(U=12.5, tsr=4.2, R=1.2, chord=0.15, n_blades=2)
    mgr1.set_user_default(custom_p)

    # Instantiate a new ConfigManager from the same file to verify persistence
    mgr2 = ConfigManager(config_path=cfg_file)
    loaded = mgr2.get_user_default()
    assert loaded.U == 12.5
    assert loaded.tsr == 4.2
    assert loaded.R == 1.2
    assert loaded.chord == 0.15
    assert loaded.n_blades == 2


def test_config_presets_crud(tmp_path):
    cfg_file = str(tmp_path / "test_cfg.json")
    mgr = ConfigManager(config_path=cfg_file)

    preset_p = Params(U=9.0, tsr=3.8, R=0.75, chord=0.08, n_blades=4)
    mgr.save_preset("My 4-Blade Experimental Rotor", preset_p, description="Field turbine model")

    # Verify present in presets list
    presets = mgr.get_presets()
    assert "My 4-Blade Experimental Rotor" in presets
    assert presets["My 4-Blade Experimental Rotor"]["description"] == "Field turbine model"

    # Load preset
    loaded_p = mgr.load_preset("My 4-Blade Experimental Rotor")
    assert loaded_p is not None
    assert loaded_p.n_blades == 4
    assert loaded_p.U == 9.0
    assert loaded_p.R == 0.75
    assert loaded_p.preset_name == "My 4-Blade Experimental Rotor"

    # Delete preset
    assert mgr.delete_preset("My 4-Blade Experimental Rotor")
    assert "My 4-Blade Experimental Rotor" not in mgr.get_presets()


def test_config_last_session_auto_restore(tmp_path):
    cfg_file = str(tmp_path / "test_cfg.json")
    mgr1 = ConfigManager(config_path=cfg_file)

    p = Params(U=10.0, tsr=5.0, R=0.8, chord=0.09, n_blades=3)
    f = InputFiles(total="C:/sim/moment.out", fx="C:/sim/fx.out")
    mgr1.save_last_session(p, f, out_dir="C:/sim/results", prefix="Case1")

    # New instance reads saved session
    mgr2 = ConfigManager(config_path=cfg_file)
    session = mgr2.get_last_session()
    assert session is not None
    assert session["params"]["U"] == 10.0
    assert session["params"]["R"] == 0.8
    assert session["files"]["total"] == "C:/sim/moment.out"
    assert session["out_dir"] == "C:/sim/results"
    assert session["prefix"] == "Case1"
