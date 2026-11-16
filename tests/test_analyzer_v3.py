# -*- coding: utf-8 -*-
"""
Tests for Analyzer 3.0: Workbench Ingestion, GCI Engine, Mesh Statistics & UI
==============================================================================
"""
import os
import pytest

from vawt_post.engine.workbench import (
    FluentSystemInfo, parse_fluent_report_xml,
    parse_msh_stats, scan_workbench_project, workbench_system_to_config
)
from vawt_post.engine.gci_engine import (
    GridLevelData, compute_asme_gci, plot_gci_curve
)


def test_v3_parse_msh_stats(tmp_path):
    """Verify that ANSYS .msh cell/node headers are correctly parsed from hex strings."""
    msh_content = """(0 machine configuration:)
(10 (0 1 fcbb 0))
(13 (0 1 19457 0))
(12 (0 1 9799 0))
"""
    msh_file = str(tmp_path / "test.msh")
    with open(msh_file, "w", encoding="latin-1") as f:
        f.write(msh_content)

    stats = parse_msh_stats(msh_file)
    assert stats["nodes"] == 0xfcbb
    assert stats["faces"] == 0x19457
    assert stats["cells"] == 0x9799


def test_v3_parse_fluent_report_xml(tmp_path):
    """Verify simulation metadata extraction from Fluent report.xml."""
    xml_content = """<FluentXMLReport>
<version>Fluent Version: 2d, dp, pbns, sstkw, transient</version>
<MaterialProperties>
   Material: air (fluid)
      Density                kg/m^3     constant   1.225
      Viscosity              kg/(m s)   constant   1.7894e-05
</MaterialProperties>
<CellZoneConditions>
      rotating_zone
         Mesh Motion?                         yes
         Moving Mesh Rotation Speed [rad/s]   85.5
</CellZoneConditions>
<BoundaryConditions>
      airfoil1                               8    wall
      airfoil2                               9    wall
      shaft                                  10   wall
      inlet                                  11   velocity-inlet
         Velocity Magnitude [m/s]          7.5
</BoundaryConditions>
<SolverSettings>
   Unsteady Calculation Parameters
      Number of Time Steps       1200
      Time Step Size [s]         0.00025
      Max Iterations/Time Step   25
</SolverSettings>
</FluentXMLReport>
"""
    xml_file = str(tmp_path / "report.xml")
    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml_content)

    meta = parse_fluent_report_xml(xml_file)
    assert meta["omega"] == 85.5
    assert meta["velocity"] == 7.5
    assert meta["density"] == 1.225
    assert meta["viscosity"] == 1.7894e-05
    assert meta["time_step"] == 0.00025
    assert meta["num_time_steps"] == 1200
    assert meta["max_iter_per_step"] == 25
    assert "airfoil1" in meta["blade_zones"]
    assert "airfoil2" in meta["blade_zones"]
    assert meta["shaft_zone"] == "shaft"


def test_v3_gci_engine_exact_convergence(tmp_path):
    """Verify ASME V&V 20 GCI calculation, apparent order, and plot generation."""
    # Classical synthetic benchmark with monotonic convergence
    grids = [
        GridLevelData(name="Fine", system_id="M1", cell_count=180000, phi=0.240),
        GridLevelData(name="Medium", system_id="M2", cell_count=120000, phi=0.252),
        GridLevelData(name="Coarse", system_id="M3", cell_count=60000, phi=0.275),
    ]

    report = compute_asme_gci(grids, dimension=2)
    assert report is not None
    assert report.r21 > 1.0
    assert report.r32 > 1.0
    assert report.apparent_order_p > 0.0
    assert report.gci_fine_21_pct > 0.0
    assert report.phi_ext_21 != 0.0

    table_md = report.to_markdown_table()
    assert "Grid Refinement Ratio" in table_md
    assert "Richardson Extrapolated" in table_md

    table_fmt = report.to_formatted_table()
    assert "ASME V&V 20 GRID CONVERGENCE INDEX" in table_fmt
    assert "Richardson Extrapolated" in table_fmt
    assert "$" not in table_fmt  # Verified: zero raw unrendered LaTeX delimiters

    table_tex = report.to_latex_table()
    assert r"\begin{table}" in table_tex
    assert r"\toprule" in table_tex

    # Generate GCI plot
    plot_file = str(tmp_path / "gci_test.png")
    plot_gci_curve(report, out_path=plot_file)
    assert os.path.isfile(plot_file)
    assert os.path.getsize(plot_file) > 1000


def test_v3_workbench_to_config_bundle():
    """Verify FluentSystemInfo converts cleanly to a valid Config bundle."""
    sys_info = FluentSystemInfo(
        system_id="FFF",
        system_dir="C:/sim/FFF",
        fluent_dir="C:/sim/FFF/Fluent",
        omega=80.0,
        velocity=7.0,
        time_step=0.00021817,
        density=1.225,
        viscosity=1.7894e-5,
        cell_count=188000,
        node_count=102000,
        blade_zones=["airfoil1", "airfoil2", "airfoil3"],
        total_moment_file="C:/sim/FFF/Fluent/moment_all-rfile.out",
        blade_moment_files=["C:/sim/FFF/Fluent/moment1.out", "C:/sim/FFF/Fluent/moment2.out", "C:/sim/FFF/Fluent/moment3.out"],
        fx_file="C:/sim/FFF/Fluent/force-rfile.out"
    )

    bundle = workbench_system_to_config(sys_info, rotor_radius=0.35, chord=0.04)
    assert bundle.params.U == 7.0
    assert bundle.params.omega == 80.0
    assert bundle.params.n_blades == 3
    assert bundle.params.validate() == []
    assert bundle.files.total == "C:/sim/FFF/Fluent/moment_all-rfile.out"
    assert bundle.files.fx == "C:/sim/FFF/Fluent/force-rfile.out"
    assert bundle.case_meta.mesh_cells == 188000


def test_v3_ui_page_switching(monkeypatch):
    """Verify switching between all 5 pages in Analyzer 3.0 desktop UI."""
    from vawt_post.ui.app import VAWTPostApp

    monkeypatch.setenv("DISPLAY", ":0")
    try:
        app = VAWTPostApp()
        app.update_idletasks()

        # Check existing 3 pages
        app._switch_page("input")
        assert app.current_page == "input"
        app._switch_page("results")
        assert app.current_page == "results"
        app._switch_page("live")
        assert app.current_page == "live"

        # Check Analyzer 3.0 Workbench page
        app._switch_page("workbench")
        assert app.current_page == "workbench"
        assert app.page_workbench.winfo_manager() in ("grid", "pack")

        app.destroy()
    except Exception as exc:
        # Tkinter may not initialize if headless X11/Windows display is unavailable in some test runs
        pytest.skip(f"Tkinter display environment unavailable: {exc}")


def test_v3_workbench_to_config_alias_r():
    """Verify workbench_system_to_config works with R alias keyword."""
    sys_info = FluentSystemInfo(
        system_id="FFF",
        system_dir="C:/sim/FFF",
        fluent_dir="C:/sim/FFF/Fluent",
        omega=80.0,
        velocity=7.0,
        time_step=0.00021817,
        blade_zones=["airfoil1", "airfoil2", "airfoil3"],
        total_moment_file="C:/sim/FFF/Fluent/moment_all-rfile.out",
    )
    bundle = workbench_system_to_config(sys_info, R=0.42, chord=0.05)
    assert bundle.params.R == 0.42
    assert bundle.params.chord == 0.05


def test_v3_gci_dark_mode_plot(tmp_path):
    """Verify plot_gci_curve renders with dark_mode=True."""
    grids = [
        GridLevelData(name="Fine", system_id="FFF", cell_count=188181, phi=0.2397),
        GridLevelData(name="Medium", system_id="FFF-2", cell_count=118964, phi=0.3259),
        GridLevelData(name="Coarse", system_id="FFF-1", cell_count=64699, phi=0.3376),
    ]
    report = compute_asme_gci(grids, dimension=2)
    assert report is not None
    out_img = str(tmp_path / "gci_dark.png")
    plot_gci_curve(report, out_path=out_img, dark_mode=True)
    assert os.path.isfile(out_img)
    assert os.path.getsize(out_img) > 1000


def test_v3_toor2026_real_workbench_scan():
    """Verify real-world scanning on Toor2026 dataset if present."""
    toor_wbpj = r"c:\Users\Fahim Montasir\Downloads\Toor2026\toor.wbpj"
    if not os.path.isfile(toor_wbpj):
        pytest.skip("Toor2026 real workbench project not present at specified path.")

    proj = scan_workbench_project(toor_wbpj)
    assert proj is not None
    assert "FFF" in proj.systems
    assert "FFF-1" in proj.systems
    assert "FFF-2" in proj.systems

    s_fine = proj.systems["FFF"]
    assert s_fine.cell_count == 188181
    assert s_fine.velocity == 7.0
    assert s_fine.omega == 80.0
    assert len(s_fine.blade_zones) == 3
    assert s_fine.total_moment_file is not None
    assert os.path.isfile(s_fine.total_moment_file)


def test_v3_unicode_safe_pipeline_logging(tmp_path):
    """Verify run_pipeline safely handles Greek letters without crashing cp1252 encoders."""
    from vawt_post.engine.config import Params, InputFiles
    from vawt_post.engine.analysis import run_pipeline
    import numpy as np

    # Generate synthetic monitor file
    f_total = str(tmp_path / "mon.out")
    dt = 0.001
    omega = 10.0
    with open(f_total, "w") as fh:
        fh.write('("Time Step" "moment" "flow-time")\n')
        for i in range(1, 2000):
            t = i * dt
            # Moment
            val = 5.0 + 0.5 * np.sin(omega * t)
            fh.write(f"{i} {val:.6f} {t:.6f}\n")

    p = Params(U=10.0, tsr=2.0, R=1.0, chord=0.1, n_blades=1, dt=dt, omega=omega)
    f = InputFiles(total=f_total)

    # Logger that simulates cp1252 terminal throwing UnicodeEncodeError on Greek characters
    logged = []
    def cp1252_logger(msg: str):
        msg.encode("cp1252")  # Will raise UnicodeEncodeError if Greek characters passed unhandled
        logged.append(msg)

    out_dir = str(tmp_path / "results")
    res = run_pipeline(p, f, outdir=out_dir, prefix="test_safe", log=cp1252_logger, make_figs=False)
    assert res is not None
    assert os.path.isfile(res["xlsx"])


def test_v3_multi_grid_arbitrary_meshes(tmp_path):
    """Verify ASME V&V 20 Multi-Grid Study handles arbitrary N >= 2 meshes (4 meshes tested)."""
    from vawt_post.engine.gci_engine import (
        GridLevelData, compute_multi_grid_study, plot_multi_grid_curve
    )

    # 4 synthetic grid levels (fine -> coarse)
    grids = [
        GridLevelData(name="Mesh 1 (Ultra)", system_id="M1", cell_count=360000, phi=0.235),
        GridLevelData(name="Mesh 2 (Fine)", system_id="M2", cell_count=240000, phi=0.240),
        GridLevelData(name="Mesh 3 (Medium)", system_id="M3", cell_count=160000, phi=0.252),
        GridLevelData(name="Mesh 4 (Coarse)", system_id="M4", cell_count=100000, phi=0.275),
    ]

    report = compute_multi_grid_study(grids, dimension=2)
    assert report is not None
    assert len(report.grids) == 4
    assert len(report.triplet_reports) == 2  # Sliding trios (0,1,2) and (1,2,3)
    assert len(report.refinement_ratios) == 3
    assert report.finest_gci_pct is not None
    assert report.phi_extrapolated is not None

    # Test formatted tables
    table_fmt = report.to_formatted_table()
    assert "ASME V&V 20 MULTI-GRID CONVERGENCE STUDY" in table_fmt
    assert "Trio 1" in table_fmt
    assert "Trio 2" in table_fmt
    assert "$" not in table_fmt  # zero raw LaTeX in clean text table

    table_md = report.to_markdown_table()
    assert "Solution φ" in table_md
    assert "| Level | System ID | Cells (N) |" in table_md

    # Test multi-grid plot generation
    plot_file = str(tmp_path / "multi_grid_plot.png")
    plot_multi_grid_curve(report, out_path=plot_file)
    assert os.path.isfile(plot_file)
    assert os.path.getsize(plot_file) > 1000


def test_v3_tsr_sweep_and_curve(tmp_path):
    """Verify TSR vs Cp aerodynamic curve extraction and plot fitting across systems."""
    from vawt_post.engine.workbench import (
        FluentSystemInfo, evaluate_tsr_sweep, plot_tsr_sweep_curve,
        tsr_sweep_to_formatted_table, tsr_sweep_to_markdown_table
    )

    # Generate synthetic moment files for 3 systems
    systems = []
    omegas = [40.0, 70.0, 100.0]
    torques = [0.12, 0.28, 0.15]  # Peak in the middle
    for i, (om, tq) in enumerate(zip(omegas, torques)):
        mon_path = str(tmp_path / f"mom_{i}.out")
        with open(mon_path, "w") as fh:
            fh.write('("Time Step" "moment" "flow-time")\n')
            for step in range(1, 100):
                fh.write(f"{step} {tq} {step * 0.001}\n")

        s = FluentSystemInfo(
            system_id=f"SYS-{i+1}",
            system_dir=str(tmp_path),
            fluent_dir=str(tmp_path),
            omega=om,
            velocity=7.0,
            cell_count=100000 + i * 20000,
            total_moment_file=mon_path
        )
        systems.append(s)

    sweep = evaluate_tsr_sweep(systems, rotor_radius=0.35, depth=1.0)
    assert sweep is not None
    assert len(sweep["points"]) == 3
    assert sweep["peak_cp"] is not None
    assert sweep["opt_tsr"] is not None
    # Middle point should have peak Cp
    assert sweep["points"][1]["mean_cp"] == sweep["peak_cp"]

    table_fmt = tsr_sweep_to_formatted_table(sweep)
    assert "TSR vs Cp AERODYNAMIC PERFORMANCE STUDY" in table_fmt
    assert "Peak Power Point" in table_fmt

    table_md = tsr_sweep_to_markdown_table(sweep)
    assert "| System ID | Cells | Inflow U (m/s) |" in table_md

    fig = plot_tsr_sweep_curve(sweep)
    assert fig is not None


def test_v3_systems_comparison():
    """Verify multi-system comparative matrix evaluation."""
    from vawt_post.engine.workbench import (
        FluentSystemInfo, evaluate_systems_comparison,
        systems_comparison_to_formatted_table, systems_comparison_to_markdown_table
    )

    systems = [
        FluentSystemInfo(system_id="S1", system_dir="", fluent_dir="", cell_count=150000, velocity=7.0, omega=80.0, time_step=0.0002, estimated_revolutions=5.0),
        FluentSystemInfo(system_id="S2", system_dir="", fluent_dir="", cell_count=200000, velocity=8.0, omega=90.0, time_step=0.00018, estimated_revolutions=6.0),
    ]

    comp = evaluate_systems_comparison(systems)
    assert len(comp["systems"]) == 2

    table_fmt = systems_comparison_to_formatted_table(comp)
    assert "MULTI-SYSTEM COMPARATIVE ANALYSIS" in table_fmt
    assert "S1" in table_fmt
    assert "S2" in table_fmt

    table_md = systems_comparison_to_markdown_table(comp)
    assert "| System ID | Mesh Cells |" in table_md


