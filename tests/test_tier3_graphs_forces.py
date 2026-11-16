# -*- coding: utf-8 -*-
"""
Tests for Tier 3.2 (External Benchmark Overlay), Tier 3.4 (Blade Forces & Dynamic Stall),
Tier 3.5 (Specialized Analysis Graphs & Animation), and Tier 3.6 (3D Span Scaling).
======================================================================================
"""
import os
import json
import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.synthetic import make_fluent_file
from vawt_post.engine.analysis import run_pipeline, analyze
from vawt_post.engine.external import (
    read_external_csv, compute_agreement_metrics, apply_blockage_correction
)
from vawt_post.engine.forces import (
    compute_blade_kinematics, compute_normal_tangential_coefficients,
    plot_hysteresis_loop, plot_dynamic_stall_aids
)
from vawt_post.engine.specialized_plots import (
    plot_per_azimuth_convergence, plot_upwind_downwind_split,
    plot_cumulative_energy, plot_load_rose, plot_cp_spectrogram,
    plot_torque_ripple, import_yplus_monitor, plot_yplus_monitor,
    create_animated_cp_wheel, plot_qa_dashboard_panel
)


def test_3_2_external_benchmark_overlay(tmp_path):
    """Tier 3.2: Import external benchmark CSV, compute agreement metrics and blockage correction."""
    csv_file = str(tmp_path / "literature_danao2014.csv")
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("# label: Danao et al. (2014) Wind Tunnel Experiment\n")
        f.write("theta, cm\n")
        for th in range(0, 360, 30):
            cm_val = 0.12 * np.sin(np.radians(th)) + 0.05
            f.write(f"{th},{cm_val:.4f}\n")

    ext = read_external_csv(csv_file)
    assert ext["label"] == "Danao et al. (2014) Wind Tunnel Experiment"
    assert len(ext["x"]) == 12

    # Simulation curve
    sim_th = np.linspace(0, 360, 361)
    sim_cm = 0.11 * np.sin(np.radians(sim_th)) + 0.048

    metrics = compute_agreement_metrics(sim_th, sim_cm, ext["x"], ext["y"])
    assert metrics["rms_error"] < 0.05
    assert metrics["correlation_r"] > 0.95
    assert len(metrics["point_errors"]) == 12

    # Blockage correction
    corr = apply_blockage_correction(
        U_inf=8.0,
        R=0.5,
        tunnel_width=2.0,
        Ct=0.8,
        formula="maskell"
    )
    assert corr["blockage_ratio"] == 0.5  # 2R / W = 1.0 / 2.0 = 0.5
    assert corr["U_corrected"] > 8.0
    assert corr["cp_multiplier"] < 1.0


def test_3_4_blade_forces_and_dynamic_stall(tmp_path):
    """Tier 3.4: Blade kinematics, Cn/Ct coefficients, hysteresis loop and dynamic stall aids."""
    th_deg = np.linspace(0, 360, 72, endpoint=False)
    tsr = 2.5
    chord = 0.06
    R = 0.5
    omega = 25.0
    U = 5.0

    kin = compute_blade_kinematics(
        theta_deg=th_deg,
        tsr=tsr,
        chord=chord,
        R=R,
        omega=omega,
        U=U,
        static_stall_deg=14.0
    )
    assert "alpha" in kin
    assert "w_over_u" in kin
    assert "k_reduced" in kin
    assert kin["stall_fraction_pct"] > 0  # Low TSR exhibits dynamic stall

    # Forces
    fx = 15.0 * np.sin(np.radians(th_deg)) ** 2
    fy = 10.0 * np.cos(np.radians(th_deg))
    cn, ct = compute_normal_tangential_coefficients(fx, fy, th_deg, rho=1.225, U=U, chord=chord, depth=1.0)
    assert len(cn) == 72
    assert len(ct) == 72

    # Hysteresis loop plot
    cm_mock = 0.08 * np.sin(np.radians(th_deg))
    out_hyst = str(tmp_path / "hysteresis.png")
    plot_hysteresis_loop(kin["alpha"], cm_mock, th_deg, filepath=out_hyst)
    assert os.path.isfile(out_hyst)

    # Dynamic stall aids plot
    out_stall = str(tmp_path / "dynamic_stall.png")
    plot_dynamic_stall_aids(kin, cm_mock, filepath=out_stall)
    assert os.path.isfile(out_stall)


def test_3_5_specialized_graphs_and_animation(tmp_path):
    """Tier 3.5: Test all 9 specialized engineering graphs and animated GIF."""
    # Run a synthetic simulation
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1442)  # 4 revolutions
    t = steps * dt
    m = 1.2 + 0.3 * np.sin(3 * omega * t)
    fx = 25.0 + 5.0 * np.cos(3 * omega * t)
    fy = 8.0 * np.sin(omega * t)

    f_total = str(tmp_path / "total.out")
    f_fx = str(tmp_path / "fx.out")
    f_fy = str(tmp_path / "fy.out")
    make_fluent_file(f_total, "total", steps, m, t)
    make_fluent_file(f_fx, "fx", steps, fx, t)
    make_fluent_file(f_fy, "fy", steps, fy, t)

    p = Params(U=8.0, tsr=3.0, R=0.5, n_blades=3)
    R = analyze(p, InputFiles(total=f_total, fx=f_fx, fy=f_fy))

    # 1. Per-azimuth convergence
    f1 = plot_per_azimuth_convergence(R, str(tmp_path / "13_conv.png"))
    assert os.path.isfile(f1)

    # 2. Upwind/downwind split
    f2 = plot_upwind_downwind_split(R, str(tmp_path / "14_split.png"))
    assert os.path.isfile(f2)

    # 3. Cumulative energy
    f3 = plot_cumulative_energy(R, str(tmp_path / "15_energy.png"))
    assert os.path.isfile(f3)

    # 4. Load rose
    f4 = plot_load_rose(R, str(tmp_path / "16_rose.png"))
    assert os.path.isfile(f4)

    # 5. Spectrogram
    f5 = plot_cp_spectrogram(R, str(tmp_path / "17_spec.png"))
    assert os.path.isfile(f5)

    # 6. Torque ripple
    f6 = plot_torque_ripple(R, str(tmp_path / "18_ripple.png"))
    assert os.path.isfile(f6)

    # 7. y+ monitor
    yplus_file = str(tmp_path / "yplus.out")
    with open(yplus_file, "w") as fh:
        fh.write('("y-plus-monitor")\n')
        for s_idx, t_val in zip(steps[:50], t[:50]):
            fh.write(f"{s_idx} {t_val:.5f} 0.85\n")
    yp_data = import_yplus_monitor(yplus_file)
    assert yp_data["max"] == 0.85
    f7, diag7 = plot_yplus_monitor(yp_data, str(tmp_path / "19_yplus.png"), limit=1.0)
    assert os.path.isfile(f7)
    assert diag7["passed"] is True

    # 8. Animated Cp wheel (GIF)
    f8 = create_animated_cp_wheel(R, str(tmp_path / "20_wheel.gif"), n_frames=6)
    assert os.path.isfile(f8)
    assert f8.endswith(".gif")

    # 9. QA dashboard panel
    f9 = plot_qa_dashboard_panel(R["qa_checks"], str(tmp_path / "21_qa.png"))
    assert os.path.isfile(f9)


def test_3_6_3d_dimensional_scaling(tmp_path):
    """Tier 3.6: Test 3D span scaling calculation, power/torque reporting and notices."""
    omega = 20.0
    dt = (2.0 * np.pi / omega) / 360.0
    steps = np.arange(1, 1082)
    t = steps * dt
    m = 1.0 + 0.2 * np.sin(3 * omega * t)

    f_total = str(tmp_path / "total.out")
    make_fluent_file(f_total, "total", steps, m, t)

    # Set real turbine blade span H = 2.5 m
    p = Params(U=8.0, tsr=3.0, R=0.5, n_blades=3, span=2.5)
    out_dir = str(tmp_path / "out_3d")
    R = run_pipeline(p, InputFiles(total=f_total), out_dir, make_figs=False, export_excel=False)

    res = R["res"]
    assert res["span_H"] == 2.5
    assert res["swept_area_3d"] == 2.0 * 0.5 * 2.5  # 2R * H = 2.5 m^2
    assert res["power_3d_W"] > 0
    assert res["torque_3d_Nm"] > 0
    assert "notice_3d" in res
    assert "excludes tip" in res["notice_3d"].lower() or "not included" in res["notice_3d"].lower()

    # Check results.json has 3D scaling
    with open(R["results_json"], "r") as fh:
        rj = json.load(fh)
    assert rj["summary_kpis"]["span_H"] == 2.5
    assert rj["summary_kpis"]["power_3d_W"] == res["power_3d_W"]
