# -*- coding: utf-8 -*-
"""
Tests for Workbench dp0/FFF-{number} Resolution, Mesh Sizing & Quality Inspector,
and Excel `Mesh_Info` Sheet Generation.
"""
import os
import zipfile
import numpy as np
import openpyxl
import pytest

from vawt_post.engine.config import InputFiles, Params
from vawt_post.engine.parser import (
    autodetect,
    detect_folder_simulation_parameters,
    resolve_fluent_directory,
)
from vawt_post.engine.mesh_inspector import (
    inspect_hdf5_mesh_quality,
    inspect_workbench_setup_and_mesh,
    resolve_workbench_system_context,
)
from vawt_post.engine.analysis import run_pipeline
from vawt_post.engine.continuity import write_fluent_report_file


REAL_WBPZ_ZIP = r"C:\Users\Fahim Montasir\Downloads\export_toor_26_new.wbpz.zip"


def _make_synthetic_2d_cas_h5(cas_path: str) -> None:
    """Create a minimal valid 2D Fluent-compatible .cas.h5 with 2 quad cells for CI tests."""
    import h5py

    coords = np.array([
        [0.0, 0.0],
        [0.01, 0.0],
        [0.02, 0.0],
        [0.0, 0.002],
        [0.01, 0.002],
        [0.02, 0.002],
    ], dtype=np.float64)
    # 7 edges (faces 1..7, 1-based nodes)
    # Interior face 1 separates cell 1 and cell 2; boundary faces 2..7 have c1=0
    f_nodes = np.array([
        2, 5,  # f1 interior (cell 1 <-> cell 2)
        1, 2,  # f2 bottom blade1 (cell 1)
        2, 3,  # f3 bottom blade1 (cell 2)
        4, 5,  # f4 top (cell 1)
        5, 6,  # f5 top (cell 2)
        1, 4,  # f6 inlet (cell 1)
        3, 6,  # f7 outlet (cell 2)
    ], dtype=np.uint32)
    nnodes = np.full(7, 2, dtype=np.int16)
    c0 = np.array([1, 1, 2, 1, 2, 1, 2], dtype=np.uint32)
    c1 = np.array([2, 0, 0, 0, 0, 0, 0], dtype=np.uint32)

    with h5py.File(cas_path, "w") as h:
        m = h.create_group("meshes/1")
        ng = m.create_group("nodes")
        ng.create_dataset("coords/1", data=coords)

        cg = m.create_group("cells")
        zt_c = cg.create_group("zoneTopology")
        zt_c.create_dataset("dimension", data=np.array([2], dtype=np.uint64))
        zt_c.create_dataset("id", data=np.array([6], dtype=np.int32))
        zt_c.create_dataset("minId", data=np.array([1], dtype=np.uint64))
        zt_c.create_dataset("maxId", data=np.array([2], dtype=np.uint64))
        zt_c.create_dataset("cellType", data=np.array([3], dtype=np.int32))
        zt_c.create_dataset("name", data=np.array([b"rotating_zone"]))
        cg.create_dataset("ctype/1/cell-types", data=np.array([3, 3], dtype=np.int16))

        fg = m.create_group("faces")
        fg.create_dataset("c0/1", data=c0)
        fg.create_dataset("c1/1", data=c1)
        fng = fg.create_group("nodes/1")
        fng.create_dataset("nnodes", data=nnodes)
        fng.create_dataset("nodes", data=f_nodes)
        zt_f = fg.create_group("zoneTopology")
        zt_f.create_dataset("id", data=np.array([1, 11, 8, 9], dtype=np.int32))
        zt_f.create_dataset("minId", data=np.array([1, 2, 6, 7], dtype=np.uint64))
        zt_f.create_dataset("maxId", data=np.array([1, 3, 6, 7], dtype=np.uint64))
        zt_f.create_dataset("zoneType", data=np.array([2, 3, 10, 5], dtype=np.int32))
        zt_f.create_dataset("name", data=np.array([b"interior-rot;blade1;inlet;outlet"]))


def test_dp0_fff_number_resolution_before_and_after_out_files(tmp_path):
    """Selecting dp0/FFF-{n} must resolve to Fluent/ even before .out files exist."""
    proj_files = tmp_path / "SimProj_files"
    sys_dir = proj_files / "dp0" / "FFF-2"
    fl_dir = sys_dir / "Fluent"
    mech_dir = sys_dir / "MECH"
    dm_dir = sys_dir / "DM"
    prog_dir = proj_files / "progress_files" / "dp0" / "FFF-2" / "Fluent"
    for d in (fl_dir, mech_dir, dm_dir, prog_dir):
        d.mkdir(parents=True)

    _make_synthetic_2d_cas_h5(str(fl_dir / "FFF-2-Setup-Output.cas.h5"))
    (mech_dir / "FFF-2.msh").write_text("(0 dummy)\n(2 2)\n", encoding="utf-8")
    (dm_dir / "FFF-2.agdb").write_bytes(b"AGDB")
    (prog_dir / "report.xml").write_text(
        "<FluentXMLReport>\n<version>\nVersion: 2d, dp, pbns, sstkw, transient\nRelease: 24.2.0\n</version>\n"
        "<CellZoneConditions>\nMoving Mesh Rotation Speed [rad/s] 40\n</CellZoneConditions>\n"
        "</FluentXMLReport>",
        encoding="utf-8",
    )

    # Even with ZERO .out files yet, dp0/FFF-2 must resolve to dp0/FFF-2/Fluent
    assert os.path.normpath(resolve_fluent_directory(str(sys_dir))) == os.path.normpath(str(fl_dir))
    ctx = resolve_workbench_system_context(str(sys_dir))
    assert ctx["is_workbench_system"] is True
    assert ctx["system_id"] == "FFF-2"
    assert os.path.normpath(ctx["fluent_dir"]) == os.path.normpath(str(fl_dir))
    assert ctx["mech_dir"] and os.path.isdir(ctx["mech_dir"])
    assert ctx["report_xml"] and os.path.isfile(ctx["report_xml"])
    assert ctx["agdb_file"] and os.path.isfile(ctx["agdb_file"])

    # Synthetic mesh quality check
    mq = inspect_hdf5_mesh_quality(
        str(fl_dir / "FFF-2-Setup-Output.cas.h5"),
        {"U": 8.0, "omega": 40.0, "R": 0.5, "chord": 0.06, "rho": 1.225, "mu": 1.7894e-5},
    )
    assert mq["available"] is True
    assert mq["cell_count"] == 2
    assert mq["quality"]["skewness_max"] == pytest.approx(0.0, abs=1e-6)
    assert mq["quality"]["ortho_min"] == pytest.approx(1.0, abs=1e-6)
    assert len(mq["wall_layers"]) == 1
    assert mq["wall_layers"][0]["y1_mean_mm"] == pytest.approx(2.0, rel=1e-3)

    # LiveMonitorEngine initialized against dp0/FFF-2 emits setup & mesh notes in alerts
    from vawt_post.engine.live_monitor import LiveMonitorEngine
    eng = LiveMonitorEngine(
        Params(U=8.0, omega=40.0, R=0.5, n_blades=3, dt=0.001),
        InputFiles(),
        watch_dir=str(sys_dir),
    )
    assert eng.detected_folder_params.get("cells") == 2
    alert_msgs = " | ".join(a.get("message", "") for a in eng.alerts)
    assert "FFF-2" in alert_msgs
    assert "Skewness" in alert_msgs or "cells" in alert_msgs


def test_mesh_info_sheet_created_in_excel_pipeline(tmp_path):
    """Running pipeline from a dp0/FFF-2 case produces a complete Mesh_Info Excel sheet."""
    sys_dir = tmp_path / "Proj_files" / "dp0" / "FFF-3"
    fl_dir = sys_dir / "Fluent"
    prog_dir = tmp_path / "Proj_files" / "progress_files" / "dp0" / "FFF-3" / "Fluent"
    fl_dir.mkdir(parents=True)
    prog_dir.mkdir(parents=True)

    _make_synthetic_2d_cas_h5(str(fl_dir / "FFF-3-Setup-Output.cas.h5"))
    (prog_dir / "report.xml").write_text(
        "<FluentXMLReport>\n<version>\nVersion: 2d, dp, pbns, sstkw, transient\nRelease: 24.2.0\n</version>\n"
        "Max Iterations/Time Step 25\n"
        "</FluentXMLReport>",
        encoding="utf-8",
    )

    dt = 0.001
    omega = 40.0
    steps = np.arange(0, 700, dtype=np.int64)
    t = steps * dt
    th = omega * t
    m_b1 = 2.0 + 3.0 * np.sin(th)
    m_b2 = 2.0 + 3.0 * np.sin(th + 2.0 * np.pi / 3.0)
    m_b3 = 2.0 + 3.0 * np.sin(th + 4.0 * np.pi / 3.0)
    m_tot = m_b1 + m_b2 + m_b3

    write_fluent_report_file(str(fl_dir / "moment-rfile.out"), steps, m_tot, t, "Moment Total")
    write_fluent_report_file(str(fl_dir / "moment1-rfile.out"), steps, m_b1, t, "Moment B1")
    write_fluent_report_file(str(fl_dir / "moment2-rfile.out"), steps, m_b2, t, "Moment B2")
    write_fluent_report_file(str(fl_dir / "moment3-rfile.out"), steps, m_b3, t, "Moment B3")

    det = detect_folder_simulation_parameters(str(sys_dir))
    assert det.get("cells") == 2
    assert det.get("system_context", {}).get("system_id") == "FFF-3"
    assert det.get("setup_notes")

    fd = autodetect(str(sys_dir), 3)
    p = Params(U=8.0, omega=omega, R=0.5, chord=0.06, n_blades=3, dt=dt, n_stable=2)
    outdir = str(sys_dir / "results")
    R = run_pipeline(p, fd, outdir=outdir, make_figs=False, export_excel=True)

    assert R.get("mesh_info", {}).get("mesh", {}).get("cell_count") == 2
    wb = openpyxl.load_workbook(R["xlsx"], read_only=True)
    try:
        assert "Mesh_Info" in wb.sheetnames
        ws = wb["Mesh_Info"]
        flat_vals = [str(c) for row in ws.iter_rows(values_only=True) for c in row if c is not None]
        joined = " | ".join(flat_vals)
        assert "FFF-3" in joined
        assert "Equiangle Skewness" in joined
        assert "Orthogonal Quality" in joined
        assert "24.2.0" in joined
    finally:
        wb.close()


def test_real_workbench_archive_dp0_fff_mesh_quality(tmp_path):
    """Validate against real ANSYS Workbench dp0/FFF project archive when available locally."""
    if not os.path.isfile(REAL_WBPZ_ZIP):
        pytest.skip("Real export_toor_26_new.wbpz.zip fixture not present on host.")

    with zipfile.ZipFile(REAL_WBPZ_ZIP, "r") as zf:
        zf.extractall(tmp_path)

    sys_dir = tmp_path / "export_toor_26_new_files" / "dp0" / "FFF"
    assert os.path.isdir(sys_dir)

    det = detect_folder_simulation_parameters(str(sys_dir))
    assert det.get("cells") == 155786
    assert det.get("nodes") == 92764
    assert det.get("turb_model") == "k-omega SST"
    assert det.get("U") == pytest.approx(7.0)
    assert det.get("omega") == pytest.approx(80.0)

    full = inspect_workbench_setup_and_mesh(str(sys_dir), det)
    m = full["mesh"]
    assert m["available"] is True
    assert m["cell_count"] == 155786
    assert m["quality"]["skewness_max"] < 0.70
    assert m["quality"]["ortho_min"] > 0.70
    blade_layers = [w for w in m["wall_layers"] if "blade" in w["zone"].lower()]
    assert len(blade_layers) == 3
    assert blade_layers[0]["y1_mean_mm"] == pytest.approx(0.005, rel=0.05)
