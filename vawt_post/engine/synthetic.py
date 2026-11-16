# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Synthetic Dataset Generator
====================================================
Generates validated synthetic ANSYS Fluent monitor datasets for multi-stage
simulations with arbitrary azimuthal shifts (e.g. 0.25°, 0.5°, 1°, 2°, 5°),
variable time step sizes, and benchmark verification cases (Set A & Set B).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from vawt_post.engine.config import Params, InputFiles


@dataclass
class SimulationStage:
    """Represents a discrete simulation stage/phase with a specific time step size or azimuthal shift."""
    n_steps: int = 0
    deg_per_step: Optional[float] = None   # Azimuthal shift per step in degrees (arbitrary e.g. 0.1, 0.25, 0.5, 1, 2, 5)
    dt: Optional[float] = None             # Time step in seconds (derived from deg_per_step if not given)
    name: Optional[str] = None             # Optional label (e.g. "Coarse Stage 1", "Refined Stage 2")
    n_revs: Optional[float] = None         # Duration in rotor revolutions (derives n_steps if n_steps <= 0)

    def resolve_dt(self, omega: float) -> float:
        """Resolve time step size dt (s) from deg_per_step or direct dt."""
        if self.dt is not None and self.dt > 0:
            return float(self.dt)
        if self.deg_per_step is not None and self.deg_per_step > 0:
            return float(math.radians(self.deg_per_step) / omega)
        return float(math.radians(1.0) / omega)

    def resolve_deg_per_step(self, omega: float) -> float:
        """Resolve azimuthal shift per step (deg) from dt or deg_per_step."""
        if self.deg_per_step is not None and self.deg_per_step > 0:
            return float(self.deg_per_step)
        dt_val = self.resolve_dt(omega)
        return float(math.degrees(omega * dt_val))

    def resolve_n_steps(self, omega: float) -> int:
        """Resolve total step count from n_steps or n_revs."""
        if self.n_steps > 0:
            return int(self.n_steps)
        if self.n_revs is not None and self.n_revs > 0:
            dt_val = self.resolve_dt(omega)
            Trev = 2.0 * math.pi / omega
            return max(1, int(round(self.n_revs * Trev / dt_val)))
        return 1440


def make_fluent_file(
    filepath: str,
    header_name: str,
    steps: np.ndarray,
    vals: np.ndarray,
    times: Optional[np.ndarray] = None
) -> None:
    """Write synthetic series into ANSYS Fluent report monitor file format."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        if times is not None:
            f.write(f'("Time Step" "{header_name}" "flow-time")\n')
            for s, v, t in zip(steps, vals, times):
                f.write(f'{s} {v:.8e} {t:.10e}\n')
        else:
            f.write(f'("Time Step" "{header_name}")\n')
            for s, v in zip(steps, vals):
                f.write(f'{s} {v:.8e}\n')


def generate_multistage_dataset(
    out_dir: str,
    params: Optional[Params] = None,
    stages: Optional[List[SimulationStage]] = None,
    start_step: int = 0,
    start_time: float = 0.0,
    target_cp_final: float = 0.32,
    settling_revs: int = 4,
    ct_target: float = 0.98,
    include_forces: bool = True,
    include_shaft: bool = False,
) -> Tuple[Params, InputFiles, Dict[str, Any]]:
    """Generate a multi-stage synthetic ANSYS Fluent monitor dataset.

    Allows assigning arbitrary numbers of stages with different timesteps or azimuthal
    shifts (such as 0.1°, 0.25°, 0.5°, 1°, 2°, 5°) in the same analysis.

    Parameters:
        out_dir: Directory where Fluent report files (.out) will be written.
        params: Turbine geometry and operating conditions (or default generic 3-blade).
        stages: List of SimulationStage objects defining step counts and azimuthal shifts.
        start_step: Initial step number in simulation (e.g. 0 or 1).
        start_time: Initial flow time in seconds.
        target_cp_final: Expected final revolution-averaged Cp.
        settling_revs: Revolutions for initial transient settling before reaching target.
        ct_target: Mean thrust force coefficient.
        include_forces: Whether to generate forcex and forcey monitors.
        include_shaft: Whether to generate shaft moment monitor.

    Returns:
        (Params, InputFiles, info_dict)
    """
    p = params or Params(U=8.0, tsr=3.5, R=0.5, chord=0.06, n_blades=3)
    omega = p.omega_val()
    Trev = 2.0 * math.pi / omega

    if not stages:
        # Default 2-stage run: Coarse 1.0 deg/step (4 revs) then fine 0.5 deg/step (4 revs)
        steps_per_rev_1 = int(round(360.0 / 1.0))
        steps_per_rev_2 = int(round(360.0 / 0.5))
        stages = [
            SimulationStage(n_steps=4 * steps_per_rev_1, deg_per_step=1.0, name="Stage 1 (1.0°/step)"),
            SimulationStage(n_steps=4 * steps_per_rev_2, deg_per_step=0.5, name="Stage 2 (0.5°/step)"),
        ]

    # Build continuous timeline and step numbers across all stages
    stage_info: List[Dict[str, Any]] = []
    step_chunks = []
    time_chunks = []

    # Include initial baseline time step at t = start_time (standard Fluent monitor step 0 at t = 0)
    step_chunks.append(np.array([start_step], dtype=np.int64))
    time_chunks.append(np.array([start_time], dtype=float))

    curr_step = start_step + 1
    curr_time = start_time

    for idx, stg in enumerate(stages):
        dt = stg.resolve_dt(omega)
        deg_step = stg.resolve_deg_per_step(omega)
        n = stg.resolve_n_steps(omega)

        stage_steps = np.arange(curr_step, curr_step + n, dtype=np.int64)
        stage_times = curr_time + (np.arange(1, n + 1, dtype=float)) * dt

        step_chunks.append(stage_steps)
        time_chunks.append(stage_times)

        stage_info.append({
            "stage_index": idx + 1,
            "name": stg.name or f"Stage {idx + 1}",
            "n_steps": n,
            "step_from": int(stage_steps[0]),
            "step_to": int(stage_steps[-1]),
            "dt": dt,
            "deg_per_step": deg_step,
            "steps_per_rev": Trev / dt,
            "revolutions": (stage_times[-1] - curr_time) / Trev,
        })

        curr_step = stage_steps[-1] + 1
        curr_time = stage_times[-1]

    steps = np.concatenate(step_chunks)
    t = np.concatenate(time_chunks)
    total_time = t[-1] - t[0]
    total_revs = total_time / Trev

    u_val = float(p.U or 8.0)
    r_val = float(p.R or 0.5)
    Pref = 0.5 * p.rho * (u_val ** 3) * (2.0 * r_val * p.depth)
    Fref = 0.5 * p.rho * (u_val ** 2) * (2.0 * r_val * p.depth)

    # Calculate smooth transient settling to target Cp
    rev_progress = (t - t[0]) / Trev

    # Convergence envelope: exponential settling to target_cp_final
    # Cp(t) = target_cp_final * (1 - 0.35 * exp(-1.5 * rev / settling_revs))
    tau = max(1.0, settling_revs / 2.0)
    cp_envelope = target_cp_final * (1.0 - 0.40 * np.exp(-rev_progress / tau))
    Mrot_target = cp_envelope * Pref / omega

    # Realistic blade azimuthal torque shape g(theta)
    def blade_shape(th_deg: np.ndarray) -> np.ndarray:
        th_rad = np.radians(th_deg)
        # Upwind lift peak at ~85 deg
        peak = np.exp(-0.5 * ((th_deg - 85.0) / 38.0) ** 2)
        # Downwind small secondary contribution
        down = 0.14 * np.sin(th_rad - np.radians(220.0))
        return 0.10 + 3.2 * peak + down

    th_grid = np.linspace(0, 360, 1000, endpoint=False)
    mean_s = float(np.mean(blade_shape(th_grid)))

    # Ensure mean of shape is exactly 1/N so sum of N blades produces Mrot_target
    nb = int(p.n_blades or 3)
    scale_amp = 1.0 / (nb * mean_s)
    g = lambda theta: blade_shape(theta) * scale_amp

    # Blade offsets (even spacing)
    offsets = [360.0 * i / nb for i in range(nb)]
    mb = []
    for off in offsets:
        th_b = np.mod(np.degrees(omega * t) + off, 360.0)
        mb.append(Mrot_target * g(th_b))

    mtot = np.sum(mb, axis=0)

    # Aerodynamic forces
    th_rot = np.mod(np.degrees(omega * t), 360.0)
    Fx = ct_target * Fref * (1.0 + 0.12 * np.sin(nb * np.radians(th_rot)))
    Fy = 0.20 * Fref * np.cos(nb * np.radians(th_rot))
    Mshaft = 0.02 * mtot if include_shaft else None

    # Write files
    p_tot = os.path.join(out_dir, "moment-rfile.out")
    make_fluent_file(p_tot, "moment-all", steps, mtot + (Mshaft if Mshaft is not None else 0.0), t)

    blade_paths = []
    for i in range(nb):
        bp = os.path.join(out_dir, f"moment{i + 1}-rfile.out")
        make_fluent_file(bp, f"moment-{i + 1}", steps, mb[i], t)
        blade_paths.append(bp)

    p_fx = None
    p_fy = None
    if include_forces:
        p_fx = os.path.join(out_dir, "forcex-rfile.out")
        p_fy = os.path.join(out_dir, "forcey-rfile.out")
        make_fluent_file(p_fx, "force-x", steps, Fx, t)
        make_fluent_file(p_fy, "force-y", steps, Fy, t)

    p_shaft = None
    if include_shaft and Mshaft is not None:
        p_shaft = os.path.join(out_dir, "shaft-moment.out")
        make_fluent_file(p_shaft, "moment-shaft", steps, Mshaft, t)

    inp_files = InputFiles(
        total=p_tot,
        blades=blade_paths,
        shaft=p_shaft,
        fx=p_fx,
        fy=p_fy
    )

    info = {
        "stages": stage_info,
        "total_steps": len(steps),
        "total_revolutions": total_revs,
        "t_start": float(t[0]),
        "t_end": float(t[-1]),
        "target_cp": target_cp_final,
    }

    return p, inp_files, info


def generate_dataset_a(out_dir: str) -> Tuple[Params, InputFiles]:
    """Generate Set A benchmark dataset:
    - 1°/step, 8 revolutions total duration, starts at step 45
    - lambda = 4, U = 7, R = 0.35, 3 blades
    - 7 complete revolutions, detected offsets 0°, 120°, 240°
    - Mean Cp of last two revolutions = 0.2929
    - Status NOT YET STABLE (last change -0.62%)
    - Dominant harmonic 3.0, Ct ~ 0.96, max geometric alpha 14.48° at 104.5°
    """
    U = 7.0
    R = 0.35
    lam = 4.0
    chord = 0.04
    n_blades = 3
    omega = lam * U / R
    Trev = 2.0 * math.pi / omega
    dt = (1.0 / 360.0) * Trev

    steps = np.arange(45, 45 + 8 * 360)
    t = steps * dt
    t_end = t[-1]
    rev_float = (t - (t_end - 7 * Trev)) / Trev

    Pref = 0.5 * 1.225 * (U ** 3) * (2.0 * R * 1.0)
    Fref = 0.5 * 1.225 * (U ** 2) * (2.0 * R * 1.0)

    # c6 = 0.2938108, c7 = 0.2919892 -> mean = 0.2929, dcp = -0.0062 (-0.62%)
    def cp_factor(rf: float) -> float:
        if rf < 1.0:
            return 0.24 + 0.04 * rf
        elif rf < 3.0:
            return 0.28 + 0.01 * (rf - 1.0)
        elif rf < 5.0:
            return 0.30 - 0.003 * (rf - 3.0)
        else:
            return 0.2938108 - 0.0018216 * (rf - 5.5)

    factors = np.array([cp_factor(r) for r in rev_float])
    Mrot_mean = factors * Pref / omega

    def blade_shape(th_deg: np.ndarray) -> np.ndarray:
        th_rad = np.radians(th_deg)
        peak = np.exp(-0.5 * ((th_deg - 85.0) / 40.0) ** 2)
        trough = 0.2 * np.sin(th_rad - np.radians(220.0))
        return 1.0 + 2.5 * peak + trough - 0.5 * np.cos(th_rad)

    th_grid = np.linspace(0, 360, 1000, endpoint=False)
    integral = float(np.mean(blade_shape(th_grid)))
    g = lambda theta: blade_shape(theta) / (3.0 * integral)

    offsets = [0.0, 120.0, 240.0]
    mb = []
    for off in offsets:
        th_b = np.mod(np.degrees(omega * t) + off, 360.0)
        mb.append(Mrot_mean * g(th_b))

    mtot = np.sum(mb, axis=0)
    th_rot = np.mod(np.degrees(omega * t), 360.0)
    Fx = 0.96 * Fref * (1.0 + 0.15 * np.sin(3.0 * np.radians(th_rot)))
    Fy = 0.20 * Fref * np.cos(3.0 * np.radians(th_rot))

    p_tot = os.path.join(out_dir, "moment-rfile.out")
    p_b1 = os.path.join(out_dir, "moment1-rfile.out")
    p_b2 = os.path.join(out_dir, "moment2-rfile.out")
    p_b3 = os.path.join(out_dir, "moment3-rfile.out")
    p_fx = os.path.join(out_dir, "forcex-rfile.out")
    p_fy = os.path.join(out_dir, "forcey-rfile.out")

    make_fluent_file(p_tot, "moment-all", steps, mtot, t)
    make_fluent_file(p_b1, "moment-1", steps, mb[0], t)
    make_fluent_file(p_b2, "moment-2", steps, mb[1], t)
    make_fluent_file(p_b3, "moment-3", steps, mb[2], t)
    make_fluent_file(p_fx, "force-x", steps, Fx, t)
    make_fluent_file(p_fy, "force-y", steps, Fy, t)

    p = Params(U=U, tsr=lam, R=R, chord=chord, n_blades=n_blades, stable_tol=0.5, preset_name="Synthetic Set A")
    f = InputFiles(total=p_tot, blades=[p_b1, p_b2, p_b3], fx=p_fx, fy=p_fy)
    return p, f


def generate_dataset_b(out_dir: str) -> Tuple[Params, InputFiles]:
    """Generate Set B benchmark dataset:
    - Two time-step phases: 2880 steps at 1.0023°/step then 2880 steps at 0.5°/step
    - 12 complete revolutions, trailing stable set 3
    - Mean Cp of revs 11 and 12 = 0.3515, Ct ~ 1.01
    - Warning about 1.0023°/step phase (359.18 steps/rev)
    - Upwind torque peak ~ 1.00 N·m at ~ 88°
    """
    U = 7.0
    R = 0.35
    lam = 4.0
    chord = 0.04
    n_blades = 3
    omega = lam * U / R
    Trev = 2.0 * math.pi / omega

    dt1 = (1.0023 / 360.0) * Trev
    dt2 = (0.5 / 360.0) * Trev

    steps1 = np.arange(1, 2881)
    t1 = steps1 * dt1
    steps2 = np.arange(2881, 5761)
    t2 = t1[-1] + (steps2 - 2880) * dt2

    steps = np.concatenate([steps1, steps2])
    t = np.concatenate([t1, t2])
    t_end = t[-1]
    n_full = 12

    Pref = 0.5 * 1.225 * (U ** 3) * (2.0 * R * 1.0)
    Fref = 0.5 * 1.225 * (U ** 2) * (2.0 * R * 1.0)

    rev_float = (t - (t_end - n_full * Trev)) / Trev

    def cp_factor(rf: float) -> float:
        if rf < 8.0:
            return 0.28 + 0.007 * rf
        elif rf < 9.0:
            return 0.3450
        elif rf < 10.0:
            return 0.3510
        else:
            return 0.3515

    factors = np.array([cp_factor(r) for r in rev_float])
    Mrot_mean = factors * Pref / omega

    def blade_shape(th_deg: np.ndarray) -> np.ndarray:
        th_rad = np.radians(th_deg)
        peak = np.exp(-0.5 * ((th_deg - 88.0) / 32.0) ** 2)
        down = 0.12 * np.sin(th_rad - np.radians(220.0))
        return 0.15 + 3.8 * peak + down

    th_grid = np.linspace(0, 360, 1000, endpoint=False)
    raw_peak = float(np.max(blade_shape(th_grid)))
    mean_s = float(np.mean(blade_shape(th_grid)))
    A = (1.5476 - (1.0 / 3.0)) / (raw_peak - mean_s)
    g = lambda theta: (1.0 / 3.0) + A * (blade_shape(theta) - mean_s)

    offsets = [0.0, 120.0, 240.0]
    mb = []
    for off in offsets:
        th_b = np.mod(np.degrees(omega * t) + off, 360.0)
        mb.append(Mrot_mean * g(th_b))

    mtot = np.sum(mb, axis=0)
    th_rot = np.mod(np.degrees(omega * t), 360.0)
    Fx = 1.01 * Fref * (1.0 + 0.12 * np.sin(3.0 * np.radians(th_rot)))
    Fy = 0.18 * Fref * np.cos(3.0 * np.radians(th_rot))

    p_tot = os.path.join(out_dir, "moment-rfile.out")
    p_b1 = os.path.join(out_dir, "moment1-rfile.out")
    p_b2 = os.path.join(out_dir, "moment2-rfile.out")
    p_b3 = os.path.join(out_dir, "moment3-rfile.out")
    p_fx = os.path.join(out_dir, "forcex-rfile.out")
    p_fy = os.path.join(out_dir, "forcey-rfile.out")

    make_fluent_file(p_tot, "moment-all", steps, mtot, t)
    make_fluent_file(p_b1, "moment-1", steps, mb[0], t)
    make_fluent_file(p_b2, "moment-2", steps, mb[1], t)
    make_fluent_file(p_b3, "moment-3", steps, mb[2], t)
    make_fluent_file(p_fx, "force-x", steps, Fx, t)
    make_fluent_file(p_fy, "force-y", steps, Fy, t)

    p = Params(U=U, tsr=lam, R=R, chord=chord, n_blades=n_blades, stable_tol=0.5, preset_name="Synthetic Set B")
    f = InputFiles(total=p_tot, blades=[p_b1, p_b2, p_b3], fx=p_fx, fy=p_fy)
    return p, f
