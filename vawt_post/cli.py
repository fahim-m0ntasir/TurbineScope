# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Command Line Interface (CLI)
=====================================================
Headless command-line execution and parameter automation.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from typing import List, Optional

from vawt_post.engine.config import Params, InputFiles, ConfigManager, FLUENT_DEFAULTS


def main(argv: Optional[List[str]] = None) -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="VAWT CFD Post-Processor: Headless and GUI runner."
    )
    parser.add_argument("--cli", action="store_true", help="Run headlessly without opening the GUI")
    parser.add_argument("--list-presets", action="store_true", help="List all available turbine presets and exit")
    parser.add_argument("--preset", type=str, help="Load a named turbine preset (e.g. 'Generic 3-Blade VAWT')")

    # Files
    parser.add_argument("--total", type=str, help="Path to total rotor moment report file")
    parser.add_argument("--blade", nargs="*", default=[], help="Paths to individual blade moment report files")
    parser.add_argument("--shaft", type=str, help="Path to shaft moment report file")
    parser.add_argument("--fx", type=str, help="Path to force-x (thrust) report file")
    parser.add_argument("--fy", type=str, help="Path to force-y (side force) report file")

    # Output
    parser.add_argument("--out", type=str, default="results", help="Output directory")
    parser.add_argument("--prefix", type=str, default="VAWT_analysis", help="Output filename prefix")

    # Turbine & Operating Parameters
    parser.add_argument("--U", type=float, default=8.0, help="Free-stream speed (m/s)")
    parser.add_argument("--tsr", type=float, default=3.5, help="Tip speed ratio lambda (-)")
    parser.add_argument("--omega", type=float, help="Rotor speed (rad/s); recomputes lambda")
    parser.add_argument("--R", type=float, default=0.5, help="Rotor radius (m)")
    parser.add_argument("--chord", type=float, default=0.06, help="Blade chord (m)")
    parser.add_argument("--blades", type=int, default=3, help="Number of blades (1 to 8)")
    parser.add_argument("--depth", type=float, default=FLUENT_DEFAULTS["depth"], help="2D depth reference (m)")
    parser.add_argument("--rho", type=float, default=FLUENT_DEFAULTS["rho"], help="Density (kg/m³)")
    parser.add_argument("--mu", type=float, default=FLUENT_DEFAULTS["mu"], help="Viscosity (Pa·s)")
    parser.add_argument("--theta0", type=float, default=0.0, help="Blade 1 azimuth at t=0 (deg)")
    parser.add_argument("--offsets", choices=["auto", "even"], default="auto", help="Blade phasing mode")
    parser.add_argument("--sign", choices=["auto", "+1", "-1"], default="auto", help="Moment sign mode")
    parser.add_argument("--tol", type=float, default=5.0, help="Stability tolerance (%% Cp change)")
    parser.add_argument("--nstable", type=int, default=2, help="Number of trailing revolutions to report")
    parser.add_argument("--bin", type=float, default=1.0, help="Azimuth bin width (deg)")
    parser.add_argument("--dt", type=float, help="Fallback time step (s) if files lack flow-time")
    parser.add_argument("--no-timeseries", action="store_true", help="Skip writing full time series to Excel")
    parser.add_argument("--generate-multistage", type=str, metavar="OUT_DIR", help="Generate synthetic multi-stage test files and exit")
    parser.add_argument("--stage", nargs=2, action="append", metavar=("STEPS", "SHIFT_DEG"), help="Add simulation stage: e.g. --stage 2880 1.0 --stage 2880 0.5")

    args = parser.parse_args(argv)
    config_mgr = ConfigManager()

    if args.list_presets:
        presets = config_mgr.get_presets()
        print("\nAvailable Turbine Presets:")
        for name, data in presets.items():
            desc = data.get("description", "")
            print(f"  • {name}: {desc}")
        return 0

    if args.generate_multistage:
        from vawt_post.engine.synthetic import SimulationStage, generate_multistage_dataset
        stages = []
        if args.stage:
            for stg_steps, stg_deg in args.stage:
                stages.append(SimulationStage(n_steps=int(float(stg_steps)), deg_per_step=float(stg_deg)))
        p_gen = Params(U=args.U, tsr=args.tsr, R=args.R, chord=args.chord, n_blades=args.blades)
        _, _, info = generate_multistage_dataset(args.generate_multistage, p_gen, stages if stages else None)
        print(f"\nSuccessfully generated multi-stage simulation files in '{args.generate_multistage}':")
        for s_inf in info["stages"]:
            print(f"  • {s_inf['name']}: {s_inf['n_steps']:,} steps @ {s_inf['deg_per_step']:.2f}°/step ({s_inf['revolutions']:.2f} revs)")
        print(f"Total: {info['total_steps']:,} steps ({info['total_revolutions']:.2f} total revs)")
        return 0

    if not args.cli:
        try:
            print("Starting TurbineScope v4.1.1 (Qt 6 Desktop Application)...")
            from vawt_post.ui.splash import launch_with_splash
            launch_with_splash()
            return 0
        except Exception as exc:
            print(f"Error launching TurbineScope GUI: {exc}", file=sys.stderr)
            traceback.print_exc()
            return 1

    # Build Params
    p = None
    if args.preset:
        p = config_mgr.load_preset(args.preset)
        if not p:
            print(f"Error: Preset '{args.preset}' not found.")
            return 1
        # Override with any explicitly supplied CLI args if different from default
    if p is None:
        p = Params(
            U=args.U,
            tsr=args.tsr,
            omega=args.omega,
            R=args.R,
            chord=args.chord,
            n_blades=args.blades,
            depth=args.depth,
            rho=args.rho,
            mu=args.mu,
            theta0=args.theta0,
            offsets=args.offsets,
            moment_sign=args.sign,
            stable_tol=args.tol,
            n_stable=args.nstable,
            bin_deg=args.bin,
            dt=args.dt,
            write_timeseries=not args.no_timeseries,
            preset_name=args.preset
        )

    f = InputFiles(
        total=args.total,
        blades=list(args.blade),
        shaft=args.shaft,
        fx=args.fx,
        fy=args.fy
    )

    def _cli_log(msg: str):
        if sys.stdout:
            try:
                print(msg, flush=True)
            except Exception:
                pass

    try:
        from vawt_post.engine.analysis import run_pipeline
        R = run_pipeline(p, f, args.out, args.prefix, log=_cli_log)
        res = R["res"]
        _cli_log("\nAnalysis Completed Successfully!")
        _cli_log(f"Reported Revolutions: {[r['k'] for r in R['sel']]}")
        _cli_log(f"Mean Cp: {res['cp_mean']:.4f}  |  Status: {'STABLE' if res['is_stable'] else 'NOT YET STABLE'}")
        _cli_log(f"Workbook: {R['xlsx']}")
        return 0
    except Exception as exc:
        if sys.stderr:
            try:
                print(f"Error during analysis: {exc}", file=sys.stderr, flush=True)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
