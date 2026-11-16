# -*- coding: utf-8 -*-
"""
Tests for Fluent report file parser and step alignment.
"""
import os
import pytest
import numpy as np

from vawt_post.engine.parser import read_fluent_report, align_series, autodetect


def test_read_fluent_standard_3col(tmp_path):
    p = tmp_path / "moment.out"
    content = (
        '("Time Step" "moment-1" "flow-time")\n'
        '1 0.125 0.001\n'
        '2 0.250 0.002\n'
        '3 0.375 0.003\n'
    )
    p.write_text(content, encoding="utf-8")
    data = read_fluent_report(str(p))
    np.testing.assert_array_equal(data["step"], [1, 2, 3])
    np.testing.assert_allclose(data["value"], [0.125, 0.250, 0.375])
    np.testing.assert_allclose(data["time"], [0.001, 0.002, 0.003])


def test_read_fluent_2col_no_time(tmp_path):
    p = tmp_path / "simple.out"
    content = (
        '("Time Step" "force_x")\n'
        '10 1.5\n'
        '20 2.5\n'
    )
    p.write_text(content, encoding="utf-8")
    data = read_fluent_report(str(p))
    np.testing.assert_array_equal(data["step"], [10, 20])
    np.testing.assert_allclose(data["value"], [1.5, 2.5])
    assert data["time"] is None


def test_align_series_deduplication(tmp_path):
    # Two series where one has repeated steps (keep the last occurrence)
    s1 = {
        "step": np.array([1, 2, 2, 3]),
        "value": np.array([10.0, 20.0, 22.0, 30.0]),
        "time": np.array([0.1, 0.2, 0.2, 0.3]),
    }
    s2 = {
        "step": np.array([1, 2, 3, 4]),
        "value": np.array([100.0, 200.0, 300.0, 400.0]),
        "time": np.array([0.1, 0.2, 0.3, 0.4]),
    }
    steps, times, vals = align_series({"m1": s1, "m2": s2}, dt_fallback=None)
    np.testing.assert_array_equal(steps, [1, 2, 3])
    np.testing.assert_allclose(times, [0.1, 0.2, 0.3])
    # The last occurrence of step 2 in s1 had value 22.0
    np.testing.assert_allclose(vals["m1"], [10.0, 22.0, 30.0])
    np.testing.assert_allclose(vals["m2"], [100.0, 200.0, 300.0])


def test_align_series_dt_fallback():
    s1 = {
        "step": np.array([1, 2, 3]),
        "value": np.array([5.0, 6.0, 7.0]),
        "time": None,
    }
    steps, times, vals = align_series({"m": s1}, dt_fallback=0.005)
    np.testing.assert_array_equal(steps, [1, 2, 3])
    np.testing.assert_allclose(times, [0.005, 0.010, 0.015])


def test_align_series_missing_time_raises():
    s1 = {
        "step": np.array([1, 2, 3]),
        "value": np.array([5.0, 6.0, 7.0]),
        "time": None,
    }
    with pytest.raises(ValueError, match="flow-time"):
        align_series({"m": s1}, dt_fallback=None)


def test_autodetect_files(tmp_path):
    # Create fake files
    (tmp_path / "moment1-rfile.out").write_text("1 1.0\n", encoding="utf-8")
    (tmp_path / "moment2-rfile.out").write_text("1 2.0\n", encoding="utf-8")
    (tmp_path / "moment3-rfile.out").write_text("1 3.0\n", encoding="utf-8")
    (tmp_path / "shaft-moment.out").write_text("1 0.1\n", encoding="utf-8")
    (tmp_path / "forcex-rfile.out").write_text("1 10.0\n", encoding="utf-8")
    (tmp_path / "forcey-rfile.out").write_text("1 2.0\n", encoding="utf-8")
    (tmp_path / "moment_all-rfile.out").write_text("1 6.1\n", encoding="utf-8")

    fd = autodetect(str(tmp_path), n_blades=3)
    assert fd.total and "moment_all" in fd.total
    assert len(fd.blades) == 3
    assert all(b is not None for b in fd.blades)
    assert fd.shaft and "shaft" in fd.shaft
    assert fd.fx and "forcex" in fd.fx
    assert fd.fy and "forcey" in fd.fy


def test_detect_folder_simulation_parameters_scheme_and_out(tmp_path):
    from vawt_post.engine.parser import detect_folder_simulation_parameters

    # Scheme .set file
    set_content = (
        '((mrf-omega (constant . 80))\n'
        ' (reference-velocity 7.0)\n'
        ' (reference-density 1.225)\n'
        ' (reference-viscosity 1.7894e-05))\n'
    )
    (tmp_path / "case.set").write_text(set_content, encoding="utf-8")

    # Monitor file with flow-time
    mom_content = (
        '("Time Step" "moment" "flow-time")\n'
        '1 0.1 0.0000\n'
        '2 0.2 0.0002\n'
        '3 0.3 0.0004\n'
        '4 0.4 0.0006\n'
    )
    (tmp_path / "moment-rfile.out").write_text(mom_content, encoding="utf-8")

    # 3 blade files
    (tmp_path / "moment1-rfile.out").write_text("1 0.05\n", encoding="utf-8")
    (tmp_path / "moment2-rfile.out").write_text("1 0.05\n", encoding="utf-8")
    (tmp_path / "moment3-rfile.out").write_text("1 0.05\n", encoding="utf-8")

    # Journal with dual-time iterations
    (tmp_path / "run.jou").write_text("/solve/dual-time-iterate 720 30\n", encoding="utf-8")

    p = detect_folder_simulation_parameters(str(tmp_path))
    assert p["omega"] == 80.0
    assert p["U"] == 7.0
    assert p["rho"] == 1.225
    assert p["mu"] == 1.7894e-5
    assert p["dt"] == 0.0002
    assert p["n_blades"] == 3
    assert p["iterations"] == 30
    assert "case.set" in p["source_files"]
    assert "moment-rfile.out" in p["source_files"]


def test_detect_folder_simulation_parameters_empty(tmp_path):
    from vawt_post.engine.parser import detect_folder_simulation_parameters
    res = detect_folder_simulation_parameters(str(tmp_path))
    assert res == {}
    assert detect_folder_simulation_parameters("") == {}


def test_detect_solver_model_from_text_exact_matching():
    from vawt_post.engine.parser import detect_solver_model_from_text

    # Scheme flags
    assert detect_solver_model_from_text("((rp-lam? #t))") == "Laminar"
    assert detect_solver_model_from_text("((rp-inviscid? #t))") == "Inviscid"
    assert detect_solver_model_from_text("((rp-sa? #t))") == "Spalart-Allmaras"
    assert detect_solver_model_from_text("((rp-trans-sst? #t))") == "Transition SST"
    assert detect_solver_model_from_text("((rp-kw? #t) (kw-sst? #t))") == "k-omega SST"
    assert detect_solver_model_from_text("((rp-kw? #t) (kw-standard? #t))") == "Standard k-omega"
    assert detect_solver_model_from_text("((rp-kw? #t) (kw-geko-on? #t))") == "GEKO k-omega"
    assert detect_solver_model_from_text("((rp-ke? #t) (ke-realizable? #t))") == "Realizable k-epsilon"
    assert detect_solver_model_from_text("((rp-ke? #t) (ke-rng? #t))") == "RNG k-epsilon"
    assert detect_solver_model_from_text("((rp-ke? #t))") == "Standard k-epsilon"
    assert detect_solver_model_from_text("((rp-rsm? #t))") == "Reynolds Stress Model (RSM)"
    assert detect_solver_model_from_text("((rp-les? #t))") == "Large Eddy Simulation (LES)"
    assert detect_solver_model_from_text("((rp-des? #t))") == "Detached Eddy Simulation (DES)"

    # Version lines (XML reports / transcripts)
    assert detect_solver_model_from_text("<version>Fluent Version: 2d, dp, pbns, lam, transient</version>") == "Laminar"
    assert detect_solver_model_from_text("<version>Fluent Version: 2d, dp, pbns, rke, transient</version>") == "Realizable k-epsilon"
    assert detect_solver_model_from_text("<version>Fluent Version: 2d, dp, pbns, sstkw, transient</version>") == "k-omega SST"
    assert detect_solver_model_from_text("<version>Fluent Version: 2d, dp, pbns, skw, transient</version>") == "Standard k-omega"
    assert detect_solver_model_from_text("<version>Fluent Version: 2d, dp, pbns, inviscid, transient</version>") == "Inviscid"
    assert detect_solver_model_from_text("<version>Fluent Version: 2d, dp, pbns, sa, transient</version>") == "Spalart-Allmaras"

    # TUI Journal commands
    assert detect_solver_model_from_text("/define/models/viscous/laminar? yes") == "Laminar"
    assert detect_solver_model_from_text("/define/models/viscous/ke-realizable? yes") == "Realizable k-epsilon"
    assert detect_solver_model_from_text("/define/models/viscous/kw-sst? yes") == "k-omega SST"

    # Negative test: returns None without false positive fallback to k-omega SST
    assert detect_solver_model_from_text("") is None
    assert detect_solver_model_from_text("some random unconfigured text") is None
    assert detect_solver_model_from_text("((rp-lam? #f) (rp-kw? #f))") is None


def test_case_inspection_result_dict_access_and_aliases():
    from vawt_post.engine.hdf5_case import CaseInspectionResult

    res = CaseInspectionResult(
        file_path="case.cas.h5",
        is_valid=True,
        rotor_radius_m=0.75,
        chord_m=0.08,
        velocity_m_s=9.0,
        omega_rad_s=45.0,
        time_step_s=0.0005,
        density_kg_m3=1.225,
        viscosity_pa_s=1.7894e-5,
        turb_model="Realizable k-epsilon",
        blade_count=3,
        cell_count=150000
    )

    # Dictionary subscripting and aliases
    assert res["rotor_radius"] == 0.75
    assert res["chord"] == 0.08
    assert res["velocity"] == 9.0
    assert res["omega"] == 45.0
    assert res["time_step"] == 0.0005
    assert res["density"] == 1.225
    assert res["viscosity"] == 1.7894e-5
    assert res["turb_model"] == "Realizable k-epsilon"
    assert res["cell_count"] == 150000

    # Dict get method with fallback
    assert res.get("rotor_radius") == 0.75
    assert res.get("non_existent", "default_val") == "default_val"


def test_shaft_moment_detection_and_force_exclusion(tmp_path):
    from vawt_post.engine.parser import parse_report_filename_role, autodetect

    # 1. Classification check
    assert parse_report_filename_role("moment_shaft-rfile.out")[0] == "shaft_moment"
    assert parse_report_filename_role("moment_shaft.out")[0] == "shaft_moment"
    assert parse_report_filename_role("shaft-moment.out")[0] == "shaft_moment"
    assert parse_report_filename_role("shaft_moment.out")[0] == "shaft_moment"
    assert parse_report_filename_role("shaft_torque.out")[0] == "shaft_moment"

    # Forces on shaft must NOT be classified as shaft moment
    assert parse_report_filename_role("force_shaftx-rfile.out")[0] == "shaft_fx"
    assert parse_report_filename_role("force_shafty-rfile.out")[0] == "shaft_fy"
    assert parse_report_filename_role("force_shaft.out")[0] == "unknown"

    # 2. Autodetect folder selection check
    # Create realistic simulation directory files as seen in Fluent runs
    (tmp_path / "moment-rfile.out").write_text("1 5.0\n", encoding="utf-8")
    (tmp_path / "moment1-rfile.out").write_text("1 1.0\n", encoding="utf-8")
    (tmp_path / "moment2-rfile.out").write_text("1 1.0\n", encoding="utf-8")
    (tmp_path / "moment3-rfile.out").write_text("1 1.0\n", encoding="utf-8")
    (tmp_path / "forcex-rfile.out").write_text("1 10.0\n", encoding="utf-8")
    (tmp_path / "forcey-rfile.out").write_text("1 2.0\n", encoding="utf-8")
    (tmp_path / "force_shaftx-rfile.out").write_text("1 0.5\n", encoding="utf-8")
    (tmp_path / "force_shafty-rfile.out").write_text("1 0.1\n", encoding="utf-8")
    (tmp_path / "moment_shaft-rfile.out").write_text("1 0.05\n", encoding="utf-8")

    files = autodetect(str(tmp_path), n_blades=3)
    assert files.total is not None and os.path.basename(files.total) == "moment-rfile.out"
    assert files.shaft is not None and os.path.basename(files.shaft) == "moment_shaft-rfile.out"
    assert files.fx is not None and os.path.basename(files.fx) == "forcex-rfile.out"
    assert files.fy is not None and os.path.basename(files.fy) == "forcey-rfile.out"
    assert files.blades[0] is not None and os.path.basename(files.blades[0]) == "moment1-rfile.out"
    assert files.blades[1] is not None and os.path.basename(files.blades[1]) == "moment2-rfile.out"
    assert files.blades[2] is not None and os.path.basename(files.blades[2]) == "moment3-rfile.out"


