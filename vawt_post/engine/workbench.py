# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: ANSYS Workbench Project Parser & Extractor
===================================================================
Discovers and extracts simulation metadata, boundary conditions, cell counts,
and monitor report files directly from native ANSYS Workbench (.wbpj) projects
and their associated _files directory (dp0/FFF/Fluent, progress_files, etc.).
"""
from __future__ import annotations

import glob
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from vawt_post.engine.config import Params, InputFiles, CaseMetadata, FLUENT_DEFAULTS
from vawt_post.engine.hdf5_case import inspect_hdf5_case


@dataclass
class FluentSystemInfo:
    """Represents a single Fluent analysis system discovered inside a Workbench project."""
    system_id: str                      # e.g. "FFF", "FFF-1", "FFF-2"
    system_dir: str                     # e.g. path/to/toor_files/dp0/FFF
    fluent_dir: str                     # e.g. path/to/toor_files/dp0/FFF/Fluent
    case_path: Optional[str] = None     # Path to .cas.h5
    data_path: Optional[str] = None     # Path to .dat.h5
    msh_path: Optional[str] = None      # Path to .msh or .mshdb
    report_xml_path: Optional[str] = None  # Path to report.xml

    # Extracted Simulation Physics
    omega: Optional[float] = None       # Rotor angular speed in rad/s (from moving mesh)
    velocity: Optional[float] = None    # Inflow wind speed in m/s (from velocity inlet)
    time_step: Optional[float] = None   # Delta t in seconds
    density: Optional[float] = None     # Fluid density in kg/m^3
    viscosity: Optional[float] = None   # Dynamic viscosity in Pa.s
    turb_model: str = "SST k-omega"     # Viscous model

    # Cell / Node counts
    cell_count: Optional[int] = None
    node_count: Optional[int] = None

    # Geometry / Zones (Step B extracted via HDF5)
    rotor_radius: Optional[float] = None
    chord: Optional[float] = None
    is_2d: bool = True
    blade_zones: List[str] = field(default_factory=list)
    shaft_zone: Optional[str] = None
    rotating_zone: Optional[str] = None
    stationary_zone: Optional[str] = None

    # Discovered Monitor Files
    total_moment_file: Optional[str] = None
    shaft_moment_file: Optional[str] = None
    blade_moment_files: List[str] = field(default_factory=list)
    fx_file: Optional[str] = None
    fy_file: Optional[str] = None
    shaft_fx_file: Optional[str] = None
    shaft_fy_file: Optional[str] = None
    blade_fx_files: List[str] = field(default_factory=list)
    blade_fy_files: List[str] = field(default_factory=list)

    # Discovered Animations
    animation_names: List[str] = field(default_factory=list)

    # Calculation statistics
    total_steps: int = 0
    estimated_revolutions: float = 0.0

    # File Inventory & Active Writing Detection
    file_inventory: List[Dict[str, Any]] = field(default_factory=list)
    latest_mtime: float = 0.0
    is_actively_writing: bool = False


@dataclass
class WorkbenchProject:
    """Represents an entire ANSYS Workbench Project."""
    wbpj_path: str
    project_name: str
    files_dir: str
    design_points: List[str] = field(default_factory=list)
    systems: Dict[str, FluentSystemInfo] = field(default_factory=dict)
    active_system_id: Optional[str] = None

    def get_system(self, system_id: str) -> Optional[FluentSystemInfo]:
        return self.systems.get(system_id)


def parse_fluent_report_xml(xml_path: str) -> Dict[str, Any]:
    """
    Parse ANSYS Fluent report.xml exported in progress_files or Fluent dir.
    Extracts moving mesh angular speed, inlet velocity, time step, air properties,
    and identified zone names.
    """
    results: Dict[str, Any] = {
        "omega": None,
        "velocity": None,
        "time_step": None,
        "density": None,
        "viscosity": None,
        "turb_model": None,
        "space": None,
        "time_formulation": None,
        "blade_zones": [],
        "shaft_zone": None,
        "rotating_zone": None,
        "stationary_zone": None,
        "max_iter_per_step": None,
        "num_time_steps": None,
    }

    if not os.path.isfile(xml_path):
        return results

    try:
        with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # 1. Models Table & Version Extraction (Viscous, Space, Time)
        from vawt_post.engine.parser import detect_solver_model_from_text
        detected_model = detect_solver_model_from_text(content)
        if detected_model:
            results["turb_model"] = detected_model

        m_space = re.search(r"^\s*Space\s+([^\n\r]+)", content, re.MULTILINE)
        if m_space:
            results["space"] = m_space.group(1).strip()
        elif not results.get("space"):
            m_ver = re.search(r"Fluent Version:\s*([^\n\r<]+)", content, re.IGNORECASE)
            if m_ver:
                v_low = m_ver.group(1).lower()
                if "3d" in v_low:
                    results["space"] = "3D"
                elif "2d" in v_low:
                    results["space"] = "2D, Planar"
                elif "axi" in v_low:
                    results["space"] = "2D, Axisymmetric"

        m_time_form = re.search(r"^\s*Time\s+([^\n\r]+)", content, re.MULTILINE)
        if m_time_form:
            results["time_formulation"] = m_time_form.group(1).strip()
        elif not results.get("time_formulation"):
            m_ver = re.search(r"Fluent Version:\s*([^\n\r<]+)", content, re.IGNORECASE)
            if m_ver:
                v_low = m_ver.group(1).lower()
                if "transient" in v_low or "unsteady" in v_low:
                    results["time_formulation"] = "Unsteady"
                elif "steady" in v_low:
                    results["time_formulation"] = "Steady"

        _NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

        # 2. Moving Mesh Rotation Speed [rad/s]
        m_omega = re.search(rf"Moving Mesh Rotation Speed\s*\[rad/s\]\s+({_NUM})", content)
        if m_omega:
            try:
                results["omega"] = float(m_omega.group(1))
            except ValueError:
                pass

        # 3. Velocity Magnitude [m/s]
        m_vel = re.search(rf"Velocity Magnitude\s*\[m/s\]\s+({_NUM})", content)
        if m_vel:
            try:
                results["velocity"] = float(m_vel.group(1))
            except ValueError:
                pass

        # 4. Time Step Size [s]
        m_dt = re.search(rf"Time Step Size\s*\[s\]\s+({_NUM})", content)
        if m_dt:
            try:
                results["time_step"] = float(m_dt.group(1))
            except ValueError:
                pass

        # 5. Number of Time Steps
        m_nsteps = re.search(r"Number of Time Steps\s+([0-9]+)", content)
        if m_nsteps:
            try:
                results["num_time_steps"] = int(m_nsteps.group(1))
            except ValueError:
                pass

        # 6. Max Iterations/Time Step
        m_iter = re.search(r"Max Iterations/Time Step\s+([0-9]+)", content)
        if m_iter:
            try:
                results["max_iter_per_step"] = int(m_iter.group(1))
            except ValueError:
                pass

        # 7. Air Density & Viscosity
        # Density kg/m^3 constant 1.225
        m_dens = re.search(rf"Density\s+kg/m\^3\s+constant\s+({_NUM})", content)
        if m_dens:
            try:
                results["density"] = float(m_dens.group(1))
            except ValueError:
                pass

        # Viscosity kg/(m s) constant 1.7894e-05
        m_visc = re.search(rf"Viscosity\s+kg/\(m s\)\s+constant\s+({_NUM})", content)
        if m_visc:
            try:
                results["viscosity"] = float(m_visc.group(1))
            except ValueError:
                pass

        # 8. Boundary zones (airfoil1, airfoil2, airfoil3, shaft, etc.)
        # Under Boundary Conditions -> Zones
        blade_names = set()
        airfoil_matches = re.findall(r"\b(airfoil\d*|blade\d*)\b", content, re.IGNORECASE)
        for name in airfoil_matches:
            blade_names.add(name.lower())

        # Sort blade zones numerically e.g. airfoil1, airfoil2, airfoil3
        def _sort_key(s: str) -> Tuple[str, int]:
            nums = re.findall(r"\d+", s)
            return (re.sub(r"\d+", "", s), int(nums[0]) if nums else 0)

        results["blade_zones"] = sorted(list(blade_names), key=_sort_key)

        if re.search(r"\bshaft\b", content, re.IGNORECASE):
            results["shaft_zone"] = "shaft"

        if re.search(r"\brotating_zone\b", content, re.IGNORECASE):
            results["rotating_zone"] = "rotating_zone"
        if re.search(r"\bstationary_zone\b", content, re.IGNORECASE):
            results["stationary_zone"] = "stationary_zone"

    except Exception:
        pass

    return results


def parse_msh_stats(msh_path: str) -> Dict[str, Optional[int]]:
    """
    Read node, face, and cell count from an ANSYS ASCII / standard .msh file.
    Sections in .msh:
      (10 (0 1 <hex_nodes> 0))
      (13 (0 1 <hex_faces> 0))
      (12 (0 1 <hex_cells> 0))
    """
    stats: Dict[str, Optional[int]] = {"nodes": None, "faces": None, "cells": None}
    if not os.path.isfile(msh_path):
        return stats

    try:
        with open(msh_path, "r", encoding="latin-1", errors="ignore") as f:
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                # Nodes: (10 (0 1 <hex> ...))
                m_node = re.match(r"\(10\s*\(\s*0\s+1\s+([0-9a-fA-F]+)\b", line)
                if m_node and stats["nodes"] is None:
                    stats["nodes"] = int(m_node.group(1), 16)
                # Faces: (13 (0 1 <hex> ...))
                m_face = re.match(r"\(13\s*\(\s*0\s+1\s+([0-9a-fA-F]+)\b", line)
                if m_face and stats["faces"] is None:
                    stats["faces"] = int(m_face.group(1), 16)
                # Cells: (12 (0 1 <hex> ...))
                m_cell = re.match(r"\(12\s*\(\s*0\s+1\s+([0-9a-fA-F]+)\b", line)
                if m_cell and stats["cells"] is None:
                    stats["cells"] = int(m_cell.group(1), 16)
                if all(v is not None for v in stats.values()):
                    break
    except Exception:
        pass

    return stats


def parse_transcript_mesh_stats(trn_path: str) -> Dict[str, Optional[int]]:
    """Extract cell and node counts from Fluent Solution.trn if present."""
    stats: Dict[str, Optional[int]] = {"nodes": None, "cells": None}
    if not os.path.isfile(trn_path):
        return stats

    try:
        with open(trn_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        m_cells = re.search(r"([0-9]+)\s+cells,\s+[0-9]+\s+cell zones", content)
        if m_cells:
            stats["cells"] = int(m_cells.group(1))
        m_nodes = re.search(r"([0-9]+)\s+nodes,\s+[0-9]+\s+node zone", content)
        if m_nodes:
            stats["nodes"] = int(m_nodes.group(1))
    except Exception:
        pass
    return stats


def find_system_monitors(fluent_dir: str) -> Dict[str, Any]:
    """
    Search a Fluent directory for moment and force monitor files (.out).
    Maps them logically to:
      - total_moment
      - shaft_moment
      - blade_moments (ordered list)
      - fx (streamwise force)
      - fy (cross-stream force)
      - blade_fx, blade_fy
    """
    monitors: Dict[str, Any] = {
        "total_moment": None,
        "shaft_moment": None,
        "blade_moments": [],
        "fx": None,
        "fy": None,
        "shaft_fx": None,
        "shaft_fy": None,
        "blade_fx": [],
        "blade_fy": [],
        "animations": [],
        "total_steps": 0,
        "flow_time": 0.0,
        "dt_last": None,
    }

    if not os.path.isdir(fluent_dir):
        return monitors

    # Search for all .out files
    out_files = glob.glob(os.path.join(fluent_dir, "*.out"))
    cxa_files = glob.glob(os.path.join(fluent_dir, "*.cxa"))
    monitors["animations"] = [os.path.splitext(os.path.basename(p))[0] for p in cxa_files]

    from vawt_post.engine.parser import parse_report_filename_role

    # Categorize out files via shared role parser
    blade_m_dict: Dict[int, str] = {}
    blade_fx_dict: Dict[int, str] = {}
    blade_fy_dict: Dict[int, str] = {}
    total_priority = 99  # lower = higher priority

    for fpath in sorted(out_files):
        fname = os.path.basename(fpath).lower()
        role, b_idx, _ = parse_report_filename_role(fname, n_blades=8)

        if role == "shaft_moment":
            monitors["shaft_moment"] = fpath
        elif role == "shaft_fx":
            monitors["shaft_fx"] = fpath
        elif role == "shaft_fy":
            monitors["shaft_fy"] = fpath
        elif role == "blade_moment" and b_idx is not None:
            blade_m_dict[b_idx] = fpath
        elif role == "blade_fx" and b_idx is not None:
            blade_fx_dict[b_idx] = fpath
        elif role == "blade_fy" and b_idx is not None:
            blade_fy_dict[b_idx] = fpath
        elif role == "total_moment":
            prio = 0 if "moment_all" in fname else (1 if "moment_blades" in fname else 2)
            if prio < total_priority or not monitors["total_moment"]:
                monitors["total_moment"] = fpath
                total_priority = prio
        elif role == "fx" and not monitors["fx"]:
            monitors["fx"] = fpath
        elif role == "fy" and not monitors["fy"]:
            monitors["fy"] = fpath
        elif role == "unknown" and ("force-rfile" in fname or "force.out" in fname) and "shaft" not in fname:
            if not monitors["fx"]:
                monitors["fx"] = fpath

    # Order blade moments by index
    for idx in sorted(blade_m_dict.keys()):
        monitors["blade_moments"].append(blade_m_dict[idx])
    for idx in sorted(blade_fx_dict.keys()):
        monitors["blade_fx"].append(blade_fx_dict[idx])
    for idx in sorted(blade_fy_dict.keys()):
        monitors["blade_fy"].append(blade_fy_dict[idx])

    # Inspect total_moment file for step count, final flow time, and latest time-step dt
    primary = monitors["total_moment"] or (monitors["blade_moments"][0] if monitors["blade_moments"] else None)
    if primary and os.path.isfile(primary):
        try:
            with open(primary, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            tail_rows: List[Tuple[int, float]] = []
            for line in reversed(lines):
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        step_num = int(parts[0])
                        ft_val = float(parts[-1]) if len(parts) >= 3 else 0.0
                        tail_rows.append((step_num, ft_val))
                        if len(tail_rows) >= 2:
                            break
                    except ValueError:
                        continue
            if tail_rows:
                monitors["total_steps"] = tail_rows[0][0]
                monitors["flow_time"] = tail_rows[0][1]
                if len(tail_rows) >= 2:
                    ds = tail_rows[0][0] - tail_rows[1][0]
                    dt_diff = tail_rows[0][1] - tail_rows[1][1]
                    if ds > 0 and dt_diff > 0:
                        monitors["dt_last"] = dt_diff / ds
        except Exception:
            pass

    return monitors


def scan_workbench_project(project_path_or_dir: str) -> Optional[WorkbenchProject]:
    """
    Locate and parse an ANSYS Workbench project.
    Accepts either:
      - Path to .wbpj file (e.g. C:/path/to/toor.wbpj)
      - Path to Workbench folder (containing .wbpj or <name>_files)
    """
    if not os.path.exists(project_path_or_dir):
        return None

    wbpj_file = ""
    files_dir = ""

    if os.path.isfile(project_path_or_dir):
        if project_path_or_dir.lower().endswith(".wbpj"):
            wbpj_file = os.path.abspath(project_path_or_dir)
            base_no_ext = os.path.splitext(wbpj_file)[0]
            candidate_files = f"{base_no_ext}_files"
            if os.path.isdir(candidate_files):
                files_dir = candidate_files
            else:
                files_dir = os.path.dirname(wbpj_file)
        else:
            return None
    elif os.path.isdir(project_path_or_dir):
        # Look for .wbpj inside this directory
        wbpj_candidates = glob.glob(os.path.join(project_path_or_dir, "*.wbpj"))
        if wbpj_candidates:
            wbpj_file = os.path.abspath(wbpj_candidates[0])
            base_no_ext = os.path.splitext(wbpj_file)[0]
            candidate_files = f"{base_no_ext}_files"
            if os.path.isdir(candidate_files):
                files_dir = candidate_files
            else:
                files_dir = os.path.abspath(project_path_or_dir)
        else:
            # Check if directory itself is a _files directory
            if project_path_or_dir.rstrip("\\/").endswith("_files"):
                files_dir = os.path.abspath(project_path_or_dir)
                wbpj_parent = os.path.dirname(files_dir)
                base_name = os.path.basename(files_dir)[:-6]
                cand = os.path.join(wbpj_parent, f"{base_name}.wbpj")
                if os.path.isfile(cand):
                    wbpj_file = cand
            else:
                # Look for subdirectories ending with _files
                files_subdirs = [d for d in glob.glob(os.path.join(project_path_or_dir, "*_files")) if os.path.isdir(d)]
                if files_subdirs:
                    files_dir = os.path.abspath(files_subdirs[0])
                else:
                    files_dir = os.path.abspath(project_path_or_dir)

    if not files_dir or not os.path.isdir(files_dir):
        return None

    proj_name = os.path.splitext(os.path.basename(wbpj_file))[0] if wbpj_file else os.path.basename(files_dir)
    project = WorkbenchProject(
        wbpj_path=wbpj_file,
        project_name=proj_name,
        files_dir=files_dir
    )

    # Discover Design Points (dp0, dp1, ...) or direct system folder (dp0/FFF-n)
    dp_dirs = [
        d for d in glob.glob(os.path.join(files_dir, "dp*"))
        if os.path.isdir(d) and re.match(r"^dp\d+$", os.path.basename(d), re.IGNORECASE)
    ]
    design_points = sorted(os.path.basename(d) for d in dp_dirs)
    if not design_points:
        design_points = ["dp0"]
    project.design_points = design_points

    # Discover all Fluent systems e.g. FFF, FFF-1, FFF-2 across all discovered design points
    # (or directly if the user pointed straight at a system directory containing Fluent/)
    candidate_sys_dirs: List[Tuple[str, str, str, str]] = []  # (sys_id, sys_path, dp_name, dp_path)
    ignored_patterns = ("global", "geom", ".backup", "backup", "session_files", "progress_files", "user_files", "import_files")
    if os.path.isdir(os.path.join(files_dir, "Fluent")):
        parent_dp = os.path.basename(os.path.dirname(files_dir))
        dp_name = parent_dp if re.match(r"^dp\d+$", parent_dp, re.IGNORECASE) else "dp0"
        candidate_sys_dirs.append((os.path.basename(files_dir), files_dir, dp_name, os.path.dirname(files_dir)))
    else:
        dp_scan_list: List[Tuple[str, str]] = []
        if dp_dirs:
            for d_full in sorted(dp_dirs):
                dp_scan_list.append((os.path.basename(d_full), d_full))
        else:
            dp_scan_list.append(("dp0", files_dir))

        for dp_name, cur_dp_path in dp_scan_list:
            if not os.path.isdir(cur_dp_path):
                continue
            for entry in sorted(os.listdir(cur_dp_path)):
                full_entry = os.path.join(cur_dp_path, entry)
                if entry.startswith(".") or any(p in entry.lower() for p in ignored_patterns):
                    continue
                if os.path.isdir(full_entry) and os.path.isdir(os.path.join(full_entry, "Fluent")):
                    sys_label = entry if (len(dp_scan_list) == 1 or dp_name.lower() == "dp0") else f"{dp_name}/{entry}"
                    candidate_sys_dirs.append((sys_label, full_entry, dp_name, cur_dp_path))

    for sys_id, sys_path, cur_dp_name, cur_dp_path in candidate_sys_dirs:
        raw_sys_name = os.path.basename(sys_path)
        progress_dir = os.path.join(files_dir, "progress_files", cur_dp_name)
        fluent_dir = os.path.join(sys_path, "Fluent")
        mech_dir = os.path.join(sys_path, "MECH")
        sys_info = FluentSystemInfo(
            system_id=sys_id,
            system_dir=sys_path,
            fluent_dir=fluent_dir if os.path.isdir(fluent_dir) else sys_path
        )

        # 1. Look for .cas.h5 and .dat.h5 (prefer files matching exact system_id and solved checkpoints over stale duplicated Setup files)
        other_sys_prefixes = [
            os.path.basename(s_path_o).lower() for _, s_path_o, _, _ in candidate_sys_dirs
            if os.path.basename(s_path_o).lower() != raw_sys_name.lower()
            and os.path.basename(s_path_o).lower().startswith(raw_sys_name.lower() + "-")
        ]

        def _matches_sys_id(fpath: str) -> bool:
            b = os.path.basename(fpath).lower()
            sid_l = raw_sys_name.lower()
            # Recognize Fluent native checkpoint naming (e.g. FFF-1-00001.cas.h5 for FFF)
            # before rejecting prefixes belonging to sibling systems like FFF-1.
            if re.match(rf"^{re.escape(sid_l)}-\d+-\d+\.(cas|dat)(\.h5)?$", b):
                return True
            if any(b.startswith(op + "-") or b.startswith(op + ".") for op in other_sys_prefixes):
                return False
            return b.startswith(sid_l + ".") or b.startswith(sid_l + "-")

        cas_files = glob.glob(os.path.join(sys_info.fluent_dir, "*.cas.h5"))
        if not cas_files:
            cas_files = glob.glob(os.path.join(sys_info.fluent_dir, "*.cas"))
        if cas_files:
            cas_files.sort(
                key=lambda p: (
                    1 if _matches_sys_id(p) else 0,
                    0 if "setup" in os.path.basename(p).lower() else 1,
                    os.path.getmtime(p),
                ),
                reverse=True,
            )
            sys_info.case_path = cas_files[0]

        dat_files = glob.glob(os.path.join(sys_info.fluent_dir, "*.dat.h5"))
        if not dat_files:
            dat_files = glob.glob(os.path.join(sys_info.fluent_dir, "*.dat"))
        if dat_files:
            dat_files.sort(
                key=lambda p: (
                    1 if _matches_sys_id(p) else 0,
                    os.path.getmtime(p),
                ),
                reverse=True,
            )
            sys_info.data_path = dat_files[0]

        # 2. Extract geometry and physics directly from HDF5 case via inspect_hdf5_case (Step B)
        h5_turb_verified = False
        if sys_info.case_path and os.path.isfile(sys_info.case_path) and sys_info.case_path.lower().endswith(".h5"):
            try:
                h5_data = inspect_hdf5_case(sys_info.case_path)
                if h5_data.get("cell_count"):
                    sys_info.cell_count = h5_data["cell_count"]
                if h5_data.get("node_count"):
                    sys_info.node_count = h5_data["node_count"]
                sys_info.is_2d = (h5_data.get("dimension") == 2)
                if h5_data.get("blade_zones"):
                    sys_info.blade_zones = h5_data["blade_zones"]
                if h5_data.get("shaft_zone"):
                    sys_info.shaft_zone = h5_data["shaft_zone"]
                if h5_data.get("rotating_zone"):
                    sys_info.rotating_zone = h5_data["rotating_zone"]
                if h5_data.get("stationary_zone"):
                    sys_info.stationary_zone = h5_data["stationary_zone"]
                if h5_data.get("rotor_radius"):
                    sys_info.rotor_radius = h5_data["rotor_radius"]
                if h5_data.get("chord"):
                    sys_info.chord = h5_data["chord"]
                if h5_data.get("velocity"):
                    sys_info.velocity = h5_data["velocity"]
                if h5_data.get("omega"):
                    sys_info.omega = h5_data["omega"]
                if h5_data.get("time_step"):
                    sys_info.time_step = h5_data["time_step"]
                if h5_data.get("density"):
                    sys_info.density = h5_data["density"]
                if h5_data.get("viscosity"):
                    sys_info.viscosity = h5_data["viscosity"]
                if h5_data.get("turb_model"):
                    sys_info.turb_model = h5_data["turb_model"]
                    h5_turb_verified = True
            except Exception:
                pass

        # 3. Look for mesh files (.msh in MECH or .mshdb in global/MECH)
        msh_files = glob.glob(os.path.join(mech_dir, "*.msh"))
        if msh_files:
            sys_info.msh_path = msh_files[0]
            if sys_info.cell_count is None:
                msh_stats = parse_msh_stats(msh_files[0])
                sys_info.cell_count = msh_stats.get("cells")
                sys_info.node_count = msh_stats.get("nodes")
        else:
            global_mshdb = os.path.join(cur_dp_path, "global", "MECH", f"{raw_sys_name}.mshdb")
            if os.path.isfile(global_mshdb):
                sys_info.msh_path = global_mshdb

        # 4. Always inspect report.xml if present to fill any missing physics/model metadata
        xml_candidates = [
            os.path.join(progress_dir, raw_sys_name, "Fluent", "report.xml"),
            os.path.join(sys_info.fluent_dir, "report.xml"),
            os.path.join(files_dir, "progress_files", raw_sys_name, "report.xml"),
        ]
        for xml_p in xml_candidates:
            if os.path.isfile(xml_p):
                sys_info.report_xml_path = xml_p
                xml_data = parse_fluent_report_xml(xml_p)
                if sys_info.omega is None:
                    sys_info.omega = xml_data.get("omega")
                if sys_info.velocity is None:
                    sys_info.velocity = xml_data.get("velocity")
                if sys_info.time_step is None:
                    sys_info.time_step = xml_data.get("time_step")
                if sys_info.density is None:
                    sys_info.density = xml_data.get("density")
                if sys_info.viscosity is None:
                    sys_info.viscosity = xml_data.get("viscosity")
                if not h5_turb_verified and xml_data.get("turb_model"):
                    sys_info.turb_model = xml_data["turb_model"]
                if not sys_info.blade_zones:
                    sys_info.blade_zones = xml_data.get("blade_zones", [])
                if sys_info.shaft_zone is None:
                    sys_info.shaft_zone = xml_data.get("shaft_zone")
                if sys_info.rotating_zone is None:
                    sys_info.rotating_zone = xml_data.get("rotating_zone")
                if sys_info.stationary_zone is None:
                    sys_info.stationary_zone = xml_data.get("stationary_zone")
                break

        # 5. Check Solution.trn if cells not yet found
        if sys_info.cell_count is None:
            trn_candidates = [
                os.path.join(progress_dir, raw_sys_name, "Fluent", "Solution.trn"),
                os.path.join(sys_info.fluent_dir, "Solution.trn"),
            ]
            for trn_p in trn_candidates:
                if os.path.isfile(trn_p):
                    trn_stats = parse_transcript_mesh_stats(trn_p)
                    if sys_info.cell_count is None and trn_stats.get("cells"):
                        sys_info.cell_count = trn_stats["cells"]
                    if sys_info.node_count is None and trn_stats.get("nodes"):
                        sys_info.node_count = trn_stats["nodes"]
                    break

        # 6. Discover Monitor files
        mon_data = find_system_monitors(sys_info.fluent_dir)
        sys_info.total_moment_file = mon_data.get("total_moment")
        sys_info.shaft_moment_file = mon_data.get("shaft_moment")
        sys_info.blade_moment_files = mon_data.get("blade_moments", [])
        sys_info.fx_file = mon_data.get("fx")
        sys_info.fy_file = mon_data.get("fy")
        sys_info.shaft_fx_file = mon_data.get("shaft_fx")
        sys_info.shaft_fy_file = mon_data.get("shaft_fy")
        sys_info.blade_fx_files = mon_data.get("blade_fx", [])
        sys_info.blade_fy_files = mon_data.get("blade_fy", [])
        sys_info.animation_names = mon_data.get("animations", [])
        sys_info.total_steps = mon_data.get("total_steps", 0)
        if sys_info.time_step is None and mon_data.get("dt_last"):
            sys_info.time_step = float(mon_data["dt_last"])

        # 7. Inventory all files in fluent_dir (timestamps, sizes, active write checks)
        inv = []
        latest_t = 0.0
        now_t = time.time()
        for root, _, fnames in os.walk(sys_info.fluent_dir):
            rel_root = os.path.relpath(root, sys_info.fluent_dir)
            if rel_root != "." and any(part.startswith(".") for part in rel_root.split(os.sep)):
                continue
            for fn in fnames:
                fp = os.path.join(root, fn)
                try:
                    st = os.stat(fp)
                    sz = st.st_size
                    mt = st.st_mtime
                    latest_t = max(latest_t, mt)
                    low_fn = fn.lower()
                    ftype = "other"
                    if low_fn.endswith((".cas", ".cas.h5")):
                        ftype = "case"
                    elif low_fn.endswith((".dat", ".dat.h5")):
                        ftype = "data"
                    elif low_fn.endswith((".out", ".txt", ".csv")):
                        ftype = "monitor"
                    elif low_fn.endswith(".trn"):
                        ftype = "transcript"
                    elif low_fn.endswith((".msh", ".msh.h5")):
                        ftype = "mesh"
                    inv.append({
                        "name": fn,
                        "path": fp,
                        "size": sz,
                        "mtime": mt,
                        "type": ftype,
                        "is_writing": (now_t - mt < 120.0),
                    })
                except OSError:
                    pass
        sys_info.file_inventory = sorted(inv, key=lambda x: x["mtime"], reverse=True)
        sys_info.latest_mtime = latest_t
        sys_info.is_actively_writing = any(f["is_writing"] for f in inv)

        # Estimate revolutions completed (prefer exact flow_time from monitor files when available)
        flow_t = float(mon_data.get("flow_time") or 0.0)
        if sys_info.omega and abs(sys_info.omega) > 1e-9 and flow_t > 0.0:
            T_rev = 2.0 * math.pi / abs(sys_info.omega)
            sys_info.estimated_revolutions = flow_t / T_rev
        elif sys_info.omega and abs(sys_info.omega) > 1e-9 and sys_info.time_step and sys_info.total_steps:
            T_rev = 2.0 * math.pi / abs(sys_info.omega)
            total_time = sys_info.total_steps * sys_info.time_step
            sys_info.estimated_revolutions = total_time / T_rev
        elif sys_info.total_steps:
            sys_info.estimated_revolutions = sys_info.total_steps / 360.0

        project.systems[sys_id] = sys_info

    # Sort systems logically: FFF (Fine), FFF-2 (Medium), FFF-1 (Coarse)
    if project.systems:
        project.active_system_id = "FFF" if "FFF" in project.systems else list(project.systems.keys())[0]

    return project


def export_case_to_run_folder(
    case_path_or_sys: Any,
    data_path_or_dest: Optional[str] = None,
    dest_dir: Optional[str] = None,
    report_files: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Copy (never move) case, data, and monitor files into a dedicated external run folder.

    Workbench can overwrite or clear a system's Fluent folder when the system is updated.
    Accepts either:
      - (system: FluentSystemInfo, dest_dir: str)
      - (case_path: str, data_path: Optional[str], dest_dir: str, report_files=None)
    """
    if isinstance(case_path_or_sys, FluentSystemInfo):
        sys_obj = case_path_or_sys
        c_path = sys_obj.case_path
        d_path = sys_obj.data_path
        out_dir = data_path_or_dest or dest_dir or "."
        r_files = []
        if sys_obj.total_moment_file:
            r_files.append(sys_obj.total_moment_file)
        if sys_obj.blade_moment_files:
            r_files.extend(sys_obj.blade_moment_files)
    else:
        c_path = case_path_or_sys
        d_path = data_path_or_dest
        out_dir = dest_dir or "."
        r_files = report_files or []

    os.makedirs(out_dir, exist_ok=True)
    exported: Dict[str, Any] = {
        "case": None, "data": None, "reports": [],
        "copied_case": None, "copied_data": None
    }
    if c_path and os.path.isfile(c_path):
        tgt_case = os.path.join(out_dir, os.path.basename(c_path))
        shutil.copy2(c_path, tgt_case)
        exported["case"] = tgt_case
        exported["copied_case"] = tgt_case
    if d_path and os.path.isfile(d_path):
        tgt_data = os.path.join(out_dir, os.path.basename(d_path))
        shutil.copy2(d_path, tgt_data)
        exported["data"] = tgt_data
        exported["copied_data"] = tgt_data
    if r_files:
        for rf in r_files:
            if rf and os.path.isfile(rf):
                tgt_rf = os.path.join(out_dir, os.path.basename(rf))
                shutil.copy2(rf, tgt_rf)
                exported["reports"].append(tgt_rf)

    manifest = {
        "timestamp": time.time(),
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_case": c_path,
        "source_data": d_path,
        "exported_files": exported,
    }
    manifest_path = os.path.join(out_dir, "export_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        import json
        json.dump(manifest, fh, indent=2)
    exported["manifest"] = manifest_path
    return exported


def explain_workbench_files(system: Any) -> Dict[str, Any]:
    """Plain-language explanation of discovered files with actionable advice if files are missing."""
    if not isinstance(system, FluentSystemInfo):
        advice = [
            "No Fluent case files found in this Workbench project.",
            "In ANSYS Workbench, double-click the Setup cell to open Fluent, then choose File > Write > Case.",
            "Alternatively, build a case directly from your mesh using the Run Wizard (Step F)."
        ]
        return {
            "system_id": "Unknown",
            "has_case": False,
            "has_data": False,
            "has_reports": False,
            "is_actively_writing": False,
            "advice": advice,
            "file_count": 0,
        }
    has_case = bool(system.case_path and os.path.isfile(system.case_path))
    has_data = bool(system.data_path and os.path.isfile(system.data_path))
    has_reports = bool(system.total_moment_file or system.blade_moment_files)
    advice = []
    if not has_case:
        advice.append("No .cas.h5 file found in this system's Fluent folder. In ANSYS Workbench, double-click the Setup cell to open Fluent, then choose File > Write > Case. Alternatively, build a case directly from your mesh using the Run Wizard.")
    if not has_data:
        advice.append("No .dat.h5 data file found. The simulation has not been calculated or saved yet.")
    if not has_reports:
        advice.append("No .out monitor report files found. Ensure report definitions (moment, forces) and report files are configured in Fluent Setup.")
    if system.is_actively_writing:
        advice.append("Fluent solver is actively writing data files in this directory. Wait for calculation to finish or pause before copying.")
    return {
        "system_id": system.system_id,
        "has_case": has_case,
        "has_data": has_data,
        "has_reports": has_reports,
        "is_actively_writing": system.is_actively_writing,
        "advice": advice,
        "file_count": len(system.file_inventory),
    }


@dataclass
class WorkbenchConfigBundle:
    """Convenience bundle of Params, InputFiles, and CaseMetadata for a Workbench system."""
    params: Params
    files: InputFiles
    output_dir: str
    prefix: str
    case_meta: CaseMetadata


def workbench_system_to_config(
    system: FluentSystemInfo,
    rotor_radius: Optional[float] = None,
    chord: Optional[float] = None,
    depth: float = 1.0,
    output_dir: Optional[str] = None,
    R: Optional[float] = None,
    **kwargs
) -> WorkbenchConfigBundle:
    """Construct a populated configuration bundle directly from an extracted Workbench Fluent system.
    Supports either 'rotor_radius' or 'R' parameter. Never silently injects arbitrary physical defaults (Bug L6 fix).
    """
    if R is not None:
        rotor_radius = R

    param_sources: Dict[str, str] = {}
    if rotor_radius is not None:
        param_sources["R"] = "User provided"
    elif system.rotor_radius is not None:
        rotor_radius = system.rotor_radius
        param_sources["R"] = "Mesh geometry (blade centroid distance)"

    if chord is not None:
        param_sources["chord"] = "User provided"
    elif system.chord is not None:
        chord = system.chord
        param_sources["chord"] = "Mesh geometry (blade chord extent)"

    u = system.velocity
    if u is not None:
        param_sources["U"] = "Fluent case setup (velocity inlet)"

    omega_raw = system.omega
    rot_dir = "ccw"
    omega = None
    if omega_raw is not None:
        if float(omega_raw) < 0:
            rot_dir = "cw"
        omega = abs(float(omega_raw)) if abs(float(omega_raw)) > 0 else None
        if omega is not None:
            param_sources["omega"] = "Fluent case setup (moving mesh)"

    if system.time_step is not None:
        param_sources["dt"] = "Fluent case setup (time step)"

    tsr = None
    if omega is not None and rotor_radius is not None and u is not None and u > 0 and rotor_radius > 0:
        tsr = (omega * rotor_radius) / u
        param_sources["tsr"] = "Derived (omega * R / U)"

    nb = len(system.blade_zones) if system.blade_zones else 3
    if nb < 1 or nb > 8:
        nb = 3
    param_sources["n_blades"] = "Mesh boundary zones" if system.blade_zones else "Default (3)"

    case_meta = CaseMetadata(
        mesh_cells=system.cell_count,
        turb_model=system.turb_model,
        time_step_s=system.time_step,
        interface_type="Sliding Mesh",
        moment_center_confirmed=True
    )

    params = Params(
        U=u,
        tsr=tsr,
        omega=omega,
        R=rotor_radius,
        chord=chord,
        n_blades=nb,
        depth=depth,
        rho=system.density if system.density is not None else FLUENT_DEFAULTS["rho"],
        mu=system.viscosity if system.viscosity is not None else FLUENT_DEFAULTS["mu"],
        dt=system.time_step,
        rotation_dir=rot_dir,
        write_timeseries=True,
        metadata=case_meta,
        param_sources=param_sources
    )

    blade_files: List[Optional[str]] = [None] * 8
    for i, bf in enumerate(system.blade_moment_files[:8]):
        blade_files[i] = bf

    blade_fx: List[Optional[str]] = [None] * 8
    for i, bfx in enumerate(system.blade_fx_files[:8]):
        blade_fx[i] = bfx

    blade_fy: List[Optional[str]] = [None] * 8
    for i, bfy in enumerate(system.blade_fy_files[:8]):
        blade_fy[i] = bfy

    files = InputFiles(
        total=system.total_moment_file,
        shaft=system.shaft_moment_file,
        blades=blade_files,
        fx=system.fx_file,
        fy=system.fy_file,
        fx_shaft=system.shaft_fx_file,
        fy_shaft=system.shaft_fy_file,
        fx_blades=blade_fx,
        fy_blades=blade_fy
    )

    out_dir = output_dir or os.path.join(os.path.expanduser("~"), "VAWT_results", f"WB_{system.system_id}")
    prefix = f"WB_{system.system_id}"

    return WorkbenchConfigBundle(
        params=params,
        files=files,
        output_dir=out_dir,
        prefix=prefix,
        case_meta=case_meta
    )


def _extract_system_converged_torque(s: FluentSystemInfo) -> Dict[str, Any]:
    """Extract blades-only (or shaft-subtracted) rotor torque and compute converged revolution metrics."""
    from vawt_post.engine.parser import read_fluent_report, align_series
    from vawt_post.engine.analysis import window_mean
    import numpy as np

    out: Dict[str, Any] = {
        "mean_torque": None,
        "std_torque": None,
        "min_torque": None,
        "max_torque": None,
        "theta_arr": None,
        "cm_arr": None,
    }
    try:
        series: Dict[str, Dict[str, Any]] = {}
        blade_files = [bf for bf in (s.blade_moment_files or []) if bf and os.path.isfile(bf)]
        if len(blade_files) >= 2:
            for idx_b, bf in enumerate(blade_files):
                series[f"blade{idx_b + 1}"] = read_fluent_report(bf)
        elif s.total_moment_file and os.path.isfile(s.total_moment_file):
            series["total"] = read_fluent_report(s.total_moment_file)
            if s.shaft_moment_file and os.path.isfile(s.shaft_moment_file):
                series["shaft"] = read_fluent_report(s.shaft_moment_file)
        elif len(blade_files) == 1:
            series["blade1"] = read_fluent_report(blade_files[0])

        if not series:
            return out

        step, t, S = align_series(series, s.time_step)
        if len(t) == 0:
            return out

        if len(blade_files) >= 2 and all(f"blade{idx_b + 1}" in S for idx_b in range(len(blade_files))):
            m_rot = np.sum([S[f"blade{idx_b + 1}"] for idx_b in range(len(blade_files))], axis=0)
        elif "total" in S and "shaft" in S:
            m_rot = S["total"] - S["shaft"]
        elif "total" in S:
            m_rot = S["total"]
        else:
            m_rot = next(iter(S.values()))

        om = abs(float(s.omega)) if s.omega else None
        if om and om > 1e-9 and (t[-1] - t[0]) >= (2.0 * math.pi / om) * 0.98:
            Trev = 2.0 * math.pi / om
            n_full = int(math.floor((t[-1] - t[0]) / Trev + 1e-9))
            if n_full >= 1:
                anchor_forward = (abs(float(t[0])) <= 0.02 * Trev) or (int(step[0]) <= 1)
                n_tail_revs = min(2, n_full)
                rev_means = []
                last_a, last_b = float(t[0]), float(t[-1])
                for k in range(n_full - n_tail_revs + 1, n_full + 1):
                    if anchor_forward:
                        a = float(t[0]) + (k - 1) * Trev
                    else:
                        a = float(t[-1]) - (n_full - k + 1) * Trev
                    b = a + Trev
                    rev_means.append(window_mean(t, m_rot, a, b))
                    last_a, last_b = a, b
                out["mean_torque"] = float(np.mean(rev_means))
                m_win = (t >= last_a) & (t <= last_b)
                tail_vals = m_rot[m_win] if np.any(m_win) else m_rot[-min(len(m_rot), 360):]
                out["std_torque"] = float(np.std(tail_vals))
                out["min_torque"] = float(np.min(tail_vals))
                out["max_torque"] = float(np.max(tail_vals))
                out["theta_arr"] = np.linspace(0, 360, len(tail_vals), endpoint=False)
                out["cm_arr"] = tail_vals
                return out

        n_tail = min(len(m_rot), 360)
        tail_vals = m_rot[-n_tail:]
        out["mean_torque"] = float(np.mean(tail_vals))
        out["std_torque"] = float(np.std(tail_vals))
        out["min_torque"] = float(np.min(tail_vals))
        out["max_torque"] = float(np.max(tail_vals))
        out["theta_arr"] = np.linspace(0, 360, len(tail_vals), endpoint=False)
        out["cm_arr"] = tail_vals
    except Exception:
        pass
    return out


def load_benchmark_curve(benchmark_path: str) -> List[Dict[str, Any]]:
    """Load experimental or reference CFD Cp vs TSR (lambda) curves from an .xlsx or .csv file."""
    curves: List[Dict[str, Any]] = []
    if not benchmark_path or not os.path.isfile(benchmark_path):
        return curves
    try:
        ext = os.path.splitext(benchmark_path)[1].lower()
        rows_raw: List[List[Any]] = []
        if ext in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(benchmark_path, data_only=True, read_only=True)
            try:
                ws = wb.active
                if ws is not None:
                    for r in ws.iter_rows(values_only=True):
                        if any(v is not None for v in r):
                            rows_raw.append(list(r))
            finally:
                wb.close()
        else:
            import csv
            with open(benchmark_path, "r", encoding="utf-8", errors="ignore") as f:
                for r in csv.reader(f):
                    if any(cell.strip() for cell in r):
                        rows_raw.append(r)
        if len(rows_raw) < 2:
            return curves
        headers = [str(h).strip() if h is not None else f"Series {i}" for i, h in enumerate(rows_raw[0])]
        n_cols = len(headers)
        for col_idx in range(1, n_cols):
            tsr_list: List[float] = []
            cp_list: List[float] = []
            for r in rows_raw[1:]:
                if len(r) <= col_idx:
                    continue
                try:
                    x_val = float(r[0])
                    y_val = float(r[col_idx])
                    if not (math.isnan(x_val) or math.isnan(y_val)):
                        tsr_list.append(x_val)
                        cp_list.append(y_val)
                except (ValueError, TypeError):
                    continue
            if tsr_list:
                curves.append({
                    "label": headers[col_idx] or f"Benchmark {col_idx}",
                    "tsrs": tsr_list,
                    "cps": cp_list,
                })
    except Exception:
        pass
    return curves


def evaluate_tsr_sweep(
    systems: List[FluentSystemInfo],
    rotor_radius: Optional[float] = None,
    chord: Optional[float] = None,
    depth: float = 1.0,
    rho: float = 1.225,
    benchmark_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract TSR (lambda) and mean Cp from discovered Workbench systems and compute
    the aerodynamic performance curve with peak Cp and optimal TSR.
    Requires verified R and U per system; warns if metadata differs across points (Bug L14 fix).
    """
    points = []
    metadata_warnings = []
    turb_models = {s.turb_model for s in systems if s.turb_model}
    if len(turb_models) > 1:
        metadata_warnings.append(f"Turbulence model differs across systems: {', '.join(turb_models)}")

    for s in systems:
        r_val = rotor_radius or s.rotor_radius
        u = s.velocity
        om = s.omega
        tsr = None
        if om is not None and r_val is not None and u is not None and u > 0 and r_val > 0:
            tsr = (abs(om) * r_val) / u

        mean_torque = None
        mean_cp = None
        if tsr is not None and r_val is not None and u is not None and om is not None:
            tq_info = _extract_system_converged_torque(s)
            mean_torque = tq_info.get("mean_torque")
            if mean_torque is not None:
                denom = 0.5 * rho * (u ** 3) * (2.0 * r_val) * depth
                if denom > 0:
                    mean_cp = (float(om) * float(mean_torque)) / denom

        points.append({
            "system_id": s.system_id,
            "tsr": tsr,
            "velocity": u,
            "omega": om,
            "mean_torque": mean_torque,
            "mean_cp": mean_cp,
            "cell_count": s.cell_count,
            "turb_model": s.turb_model
        })

    points = sorted(points, key=lambda p: (p["tsr"] if p["tsr"] is not None else 0.0))
    valid_points = [p for p in points if p["mean_cp"] is not None and p["tsr"] is not None]
    tsrs = [p["tsr"] for p in valid_points]
    cps = [p["mean_cp"] for p in valid_points]

    peak_cp = max(cps) if cps else None
    opt_tsr = tsrs[cps.index(peak_cp)] if (peak_cp is not None and len(cps) > 0) else None
    benchmarks = load_benchmark_curve(benchmark_path) if benchmark_path else []

    return {
        "points": points,
        "valid_points": valid_points,
        "tsrs": tsrs,
        "cps": cps,
        "peak_cp": peak_cp,
        "opt_tsr": opt_tsr,
        "rotor_radius": rotor_radius or next((s.rotor_radius for s in systems if s.rotor_radius), None),
        "chord": chord or next((s.chord for s in systems if s.chord), None),
        "benchmarks": benchmarks,
        "warnings": metadata_warnings
    }


def plot_tsr_sweep_curve(sweep_data: Dict[str, Any], dark_mode: bool = False) -> Any:
    """Generate publication plot for Tip Speed Ratio vs Power Coefficient curve."""
    from matplotlib.figure import Figure
    import numpy as np

    fig = Figure(figsize=(7.5, 4.8), dpi=100)
    ax = fig.add_subplot(111)

    bg_col = "#0F172A" if dark_mode else "#FFFFFF"
    text_col = "#F8FAFC" if dark_mode else "#0F172A"
    grid_col = "#334155" if dark_mode else "#E2E8F0"

    fig.patch.set_facecolor(bg_col)
    ax.set_facecolor(bg_col)
    ax.grid(True, linestyle="--", alpha=0.5, color=grid_col)
    ax.tick_params(colors=text_col)
    for spine in ax.spines.values():
        spine.set_color(text_col)

    tsrs = sweep_data.get("tsrs", [])
    cps = sweep_data.get("cps", [])

    if tsrs and cps:
        ax.scatter(tsrs, cps, color="#3B82F6", s=70, zorder=5, label="Simulation Points")

        # Fit curve if >= 3 points
        if len(tsrs) >= 3:
            try:
                import importlib
                scipy_interp = importlib.import_module("scipy.interpolate")
                PchipInterpolator = getattr(scipy_interp, "PchipInterpolator")
                pchip = PchipInterpolator(tsrs, cps)
                tsr_dense = np.linspace(min(tsrs), max(tsrs), 150)
                cp_dense = pchip(tsr_dense)
                ax.plot(tsr_dense, cp_dense, color="#2563EB", lw=2.0, label="PCHIP Spline Fit")
            except Exception:
                ax.plot(tsrs, cps, color="#2563EB", lw=1.8, linestyle="--")

        if sweep_data.get("peak_cp") is not None and sweep_data.get("opt_tsr") is not None:
            opt_t = sweep_data["opt_tsr"]
            p_cp = sweep_data["peak_cp"]
            ax.scatter([opt_t], [p_cp], marker="*", s=180, color="#EF4444", zorder=6,
                       label=f"Peak: $C_{{p,max}}={p_cp:.4f}$ at $\\lambda={opt_t:.2f}$")

    bench_colors = ["#10B981", "#F59E0B", "#8B5CF6", "#EC4899"]
    bench_markers = ["s", "^", "D", "v"]
    for idx_b, b_curve in enumerate(sweep_data.get("benchmarks", []) or []):
        b_tsrs = b_curve.get("tsrs", [])
        b_cps = b_curve.get("cps", [])
        if b_tsrs and b_cps:
            col_b = bench_colors[idx_b % len(bench_colors)]
            mk_b = bench_markers[idx_b % len(bench_markers)]
            ax.plot(
                b_tsrs, b_cps,
                color=col_b, marker=mk_b, markersize=5, lw=1.6, ls="--", alpha=0.85,
                label=b_curve.get("label", f"Benchmark {idx_b + 1}")
            )

    ax.set_xlabel("Tip Speed Ratio $\\lambda = \\omega R / U$", fontsize=11, fontweight="bold", color=text_col)
    ax.set_ylabel("Power Coefficient $C_p$", fontsize=11, fontweight="bold", color=text_col)
    ax.set_title("VAWT Aerodynamic Performance: $C_p$ vs $\\lambda$ Curve",
                 fontsize=12, fontweight="bold", color=text_col)

    if dark_mode:
        leg = ax.legend(loc="best", framealpha=0.85, facecolor="#1E293B", edgecolor="#475569", fontsize=9.0)
        for t in leg.get_texts():
            t.set_color("#F8FAFC")
    else:
        ax.legend(loc="best", framealpha=0.9, fontsize=9.0)

    fig.tight_layout()
    return fig


def tsr_sweep_to_formatted_table(sweep_data: Dict[str, Any]) -> str:
    """Format TSR sweep results into a clean, monospace publication-ready text table."""
    pts = sweep_data.get("points", [])
    r = sweep_data.get("rotor_radius")
    r_str = f"{float(r):.4f} m" if r is not None else "N/A"
    peak_cp = sweep_data.get("peak_cp")
    opt_tsr = sweep_data.get("opt_tsr")

    lines = [
        "=" * 92,
        "                      TSR vs Cp AERODYNAMIC PERFORMANCE STUDY",
        "=" * 92,
        f" Rotor Radius (R)   : {r_str}",
        f" Evaluated Systems  : {len(pts)}",
    ]
    if peak_cp is not None and opt_tsr is not None:
        lines.append(f" Peak Power Point   : Cp,max = {peak_cp:.4f} at optimal TSR λ = {opt_tsr:.2f}")
    lines.extend([
        "-" * 92,
        f" {'System ID':<12} | {'Cells':<10} | {'U (m/s)':<8} | {'ω (rad/s)':<10} | {'TSR (λ)':<8} | {'Torque (N·m)':<13} | {'Cp':<8}",
        "-" * 92,
    ])

    for p in pts:
        sid = p.get("system_id", "-")
        cells = f"{p['cell_count']:,}" if p.get("cell_count") else "N/A"
        u = f"{p['velocity']:.2f}" if p.get("velocity") is not None else "-"
        om = f"{p['omega']:.2f}" if p.get("omega") is not None else "-"
        tsr = f"{p['tsr']:.3f}" if p.get("tsr") is not None else "-"
        tq = f"{p['mean_torque']:.4f}" if p.get("mean_torque") is not None else "N/A"
        cp = f"{p['mean_cp']:.4f}" if p.get("mean_cp") is not None else "N/A"
        lines.append(f" {sid:<12} | {cells:<10} | {u:<8} | {om:<10} | {tsr:<8} | {tq:<13} | {cp:<8}")

    lines.append("=" * 92)
    return "\n".join(lines)


def tsr_sweep_to_markdown_table(sweep_data: Dict[str, Any]) -> str:
    """Format TSR sweep results into a Markdown table."""
    pts = sweep_data.get("points", [])
    lines = [
        "| System ID | Cells | Inflow U (m/s) | Speed ω (rad/s) | TSR (λ) | Mean Torque (N·m) | Power Coeff Cp |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for p in pts:
        sid = p.get("system_id", "-")
        cells = f"{p['cell_count']:,}" if p.get("cell_count") else "N/A"
        u = f"{p['velocity']:.2f}" if p.get("velocity") is not None else "-"
        om = f"{p['omega']:.2f}" if p.get("omega") is not None else "-"
        tsr = f"{p['tsr']:.3f}" if p.get("tsr") is not None else "-"
        tq = f"{p['mean_torque']:.4f}" if p.get("mean_torque") is not None else "N/A"
        cp = f"{p['mean_cp']:.4f}" if p.get("mean_cp") is not None else "N/A"
        lines.append(f"| {sid} | {cells} | {u} | {om} | {tsr} | {tq} | {cp} |")
    return "\n".join(lines)


def evaluate_systems_comparison(
    systems: List[FluentSystemInfo],
    baseline_system_id: Optional[str] = None,
    rotor_radius: float = 0.35,
    depth: float = 1.0,
    rho: float = 1.225
) -> Dict[str, Any]:
    """Compare multiple Workbench systems with optional baseline pinning (computing Delta Cp and Delta Cm)."""
    items = []
    base_mom = None
    base_cp = None

    for s in systems:
        tq_info = _extract_system_converged_torque(s)
        mean_mom = tq_info.get("mean_torque")
        std_mom = tq_info.get("std_torque")
        min_mom = tq_info.get("min_torque")
        max_mom = tq_info.get("max_torque")
        theta_arr = tq_info.get("theta_arr")
        cm_arr = tq_info.get("cm_arr")
        ripple_factor = None
        if mean_mom is not None and max_mom is not None and min_mom is not None and abs(mean_mom) > 1e-6:
            ripple_factor = float((max_mom - min_mom) / abs(mean_mom))

        # Calculate Cp if omega and velocity are available
        r_sys = s.rotor_radius if s.rotor_radius and s.rotor_radius > 0 else rotor_radius
        cp_val = None
        if mean_mom is not None and s.omega and s.velocity and r_sys > 0:
            area = 2.0 * r_sys * depth
            p_ref = 0.5 * rho * (s.velocity ** 3) * area
            p_aero = mean_mom * s.omega
            if p_ref > 0:
                cp_val = p_aero / p_ref

        is_base = bool(baseline_system_id and s.system_id == baseline_system_id)
        if is_base:
            base_mom = mean_mom
            base_cp = cp_val

        items.append({
            "system_id": s.system_id,
            "cells": s.cell_count,
            "dt": s.time_step,
            "revs": s.estimated_revolutions,
            "omega": s.omega,
            "velocity": s.velocity,
            "mean_moment": mean_mom,
            "std_moment": std_mom,
            "min_moment": min_mom,
            "max_moment": max_mom,
            "ripple_factor": ripple_factor,
            "mean_cp": cp_val,
            "is_baseline": is_base,
            "theta_arr": theta_arr,
            "cm_arr": cm_arr,
            "total_moment_file": s.total_moment_file,
        })

    # Compute delta relative to pinned baseline
    for it in items:
        delta_m = None
        delta_cp = None
        if base_mom is not None and abs(base_mom) > 1e-8 and it["mean_moment"] is not None:
            delta_m = ((it["mean_moment"] - base_mom) / abs(base_mom)) * 100.0
        if base_cp is not None and abs(base_cp) > 1e-8 and it["mean_cp"] is not None:
            delta_cp = ((it["mean_cp"] - base_cp) / abs(base_cp)) * 100.0
        it["delta_moment_pct"] = delta_m
        it["delta_cp_pct"] = delta_cp

    return {
        "systems": items,
        "baseline_system_id": baseline_system_id,
        "base_moment": base_mom,
        "base_cp": base_cp
    }


def systems_comparison_to_formatted_table(comp_data: Dict[str, Any]) -> str:
    """Format multi-system comparison results into a clean monospace text table with baseline deltas."""
    items = comp_data.get("systems", [])
    base_id = comp_data.get("baseline_system_id")
    lines = [
        "=" * 108,
        "                            MULTI-SYSTEM COMPARATIVE ANALYSIS",
        "=" * 108,
        f" Total Systems Compared : {len(items)}",
        f" Pinned Baseline System : {base_id or 'None (Select a system to pin as baseline)'}",
        "-" * 108,
        f" {'System ID':<12} | {'Cells':<10} | {'U (m/s)':<8} | {'ω (rad/s)':<10} | {'Mean M (N·m)':<13} | {'ΔM (%)':<9} | {'Mean Cp':<9} | {'ΔCp (%)':<9}",
        "-" * 108,
    ]
    for s in items:
        sid = s.get("system_id", "-")
        if s.get("is_baseline"):
            sid += " [★ BASE]"
        cells = f"{s['cells']:,}" if s.get("cells") else "N/A"
        u = f"{s['velocity']:.2f}" if s.get("velocity") is not None else "-"
        om = f"{s['omega']:.2f}" if s.get("omega") is not None else "-"
        mom = f"{s['mean_moment']:.4f}" if s.get("mean_moment") is not None else "N/A"
        dm = f"{s['delta_moment_pct']:+.2f}%" if s.get("delta_moment_pct") is not None else ("0.00% (Base)" if s.get("is_baseline") else "-")
        cp = f"{s['mean_cp']:.4f}" if s.get("mean_cp") is not None else "N/A"
        dcp = f"{s['delta_cp_pct']:+.2f}%" if s.get("delta_cp_pct") is not None else ("0.00% (Base)" if s.get("is_baseline") else "-")
        lines.append(f" {sid:<12} | {cells:<10} | {u:<8} | {om:<10} | {mom:<13} | {dm:<9} | {cp:<9} | {dcp:<9}")

    lines.append("=" * 108)
    return "\n".join(lines)


def systems_comparison_to_markdown_table(comp_data: Dict[str, Any]) -> str:
    """Format multi-system comparison results into a Markdown table with baseline deltas."""
    items = comp_data.get("systems", [])
    base_id = comp_data.get("baseline_system_id")
    lines = [
        f"**Multi-System Comparative Analysis** (Pinned Baseline: `{base_id or 'None'}`)",
        "",
        "| System ID | Mesh Cells | Inflow U (m/s) | Speed ω (rad/s) | Mean Moment (N·m) | ΔM (%) | Mean Cp | ΔCp (%) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for s in items:
        sid = s.get("system_id", "-")
        if s.get("is_baseline"):
            sid += " **[Baseline]**"
        cells = f"{s['cells']:,}" if s.get("cells") else "N/A"
        u = f"{s['velocity']:.2f}" if s.get("velocity") is not None else "-"
        om = f"{s['omega']:.2f}" if s.get("omega") is not None else "-"
        mom = f"{s['mean_moment']:.4f}" if s.get("mean_moment") is not None else "N/A"
        dm = f"{s['delta_moment_pct']:+.2f}%" if s.get("delta_moment_pct") is not None else ("0.00% (Base)" if s.get("is_baseline") else "-")
        cp = f"{s['mean_cp']:.4f}" if s.get("mean_cp") is not None else "N/A"
        dcp = f"{s['delta_cp_pct']:+.2f}%" if s.get("delta_cp_pct") is not None else ("0.00% (Base)" if s.get("is_baseline") else "-")
        lines.append(f"| {sid} | {cells} | {u} | {om} | {mom} | {dm} | {cp} | {dcp} |")
    return "\n".join(lines)


# ==============================================================================
# Steady-State Convergence Plotting & Analysis
# ==============================================================================

def evaluate_steady_convergence(system: FluentSystemInfo) -> Dict[str, Any]:
    """
    Extract convergence metrics for steady-state simulation system (Bug L7 fix).
    Computes normalized slope (relative change per 100 iterations), window range %, and residual status.
    Clearly notes that steady MRF solutions serve as an initial flow field for transient runs.
    """
    from vawt_post.engine.parser import read_fluent_report, align_series
    import numpy as np

    res: Dict[str, Any] = {
        "system_id": system.system_id,
        "iterations": 0,
        "final_moment": None,
        "mean_moment": None,
        "normalized_slope_pct_per_100_iter": None,
        "window_range_pct": None,
        "status": "NO_DATA",
        "note": "Steady MRF solutions of cross-flow VAWTs inherently oscillate due to non-axisymmetric blade aerodynamics; steady results serve primarily as an initial flow field for transient mesh motion.",
        "final_fx": None,
        "final_fy": None,
        "has_moment": False,
        "has_forces": False
    }

    # Extract moment (total or step-aligned summed blades), filtering non-finite entries
    vals = None
    if system.total_moment_file and os.path.isfile(system.total_moment_file):
        try:
            r = read_fluent_report(system.total_moment_file)
            raw_vals = r.get("value")
            if raw_vals is not None and len(raw_vals) > 0:
                arr = np.asarray(raw_vals, dtype=float)
                arr = arr[np.isfinite(arr)]
                if len(arr) > 0:
                    vals = arr
                    res["iterations"] = len(arr)
                    res["final_moment"] = float(arr[-1])
                    tail_n = min(len(arr), max(10, int(len(arr) * 0.15)))
                    res["mean_moment"] = float(np.mean(arr[-tail_n:]))
                    res["has_moment"] = True
        except Exception:
            pass

    if not res["has_moment"] and system.blade_moment_files:
        try:
            series: Dict[str, Dict[str, Any]] = {}
            for idx_b, bf in enumerate(system.blade_moment_files):
                if bf and os.path.isfile(bf):
                    series[f"blade{idx_b + 1}"] = read_fluent_report(bf)
            if series:
                _, _, aligned = align_series(series, system.time_step)
                b_sum = np.sum(list(aligned.values()), axis=0)
                b_sum = b_sum[np.isfinite(b_sum)]
                if len(b_sum) > 0:
                    vals = b_sum
                    res["iterations"] = len(b_sum)
                    res["final_moment"] = float(b_sum[-1])
                    tail_n = min(len(b_sum), max(10, int(len(b_sum) * 0.15)))
                    res["mean_moment"] = float(np.mean(b_sum[-tail_n:]))
                    res["has_moment"] = True
        except Exception:
            pass

    # Normalized slope and window range calculation (Bug L7 fix)
    if vals is not None and len(vals) >= 10:
        tail_n = min(len(vals), max(10, int(len(vals) * 0.15)))
        tail_vals = np.asarray(vals[-tail_n:], dtype=float)
        tail_vals = tail_vals[np.isfinite(tail_vals)]
        if len(tail_vals) >= 2:
            mean_v = float(np.mean(tail_vals))
            mean_denom = abs(mean_v) if abs(mean_v) > 1e-9 else 1.0
            x_tail = np.arange(len(tail_vals), dtype=float)
            slope, _ = np.polyfit(x_tail, tail_vals, 1)
            res["normalized_slope_pct_per_100_iter"] = float((slope * 100.0) / mean_denom * 100.0)
            v_min, v_max = float(np.min(tail_vals)), float(np.max(tail_vals))
            res["window_range_pct"] = float((v_max - v_min) / mean_denom * 100.0)

            if res["window_range_pct"] < 0.5 and abs(res["normalized_slope_pct_per_100_iter"]) < 0.1:
                res["status"] = "CONVERGED (Initial Field Ready)"
            elif res["window_range_pct"] < 2.0 and abs(res["normalized_slope_pct_per_100_iter"]) < 0.5:
                res["status"] = "MARGINALLY CONVERGED"
            else:
                res["status"] = "OSCILLATING / UNCONVERGED (Typical for bluff-body VAWT MRF)"

    # Extract forces Fx and Fy
    if system.fx_file and os.path.isfile(system.fx_file):
        try:
            r = read_fluent_report(system.fx_file)
            f_vals = np.asarray(r.get("value"), dtype=float) if r.get("value") is not None else None
            if f_vals is not None:
                f_vals = f_vals[np.isfinite(f_vals)]
                if len(f_vals) > 0:
                    res["final_fx"] = float(f_vals[-1])
                    res["has_forces"] = True
        except Exception:
            pass

    if system.fy_file and os.path.isfile(system.fy_file):
        try:
            r = read_fluent_report(system.fy_file)
            f_vals = np.asarray(r.get("value"), dtype=float) if r.get("value") is not None else None
            if f_vals is not None:
                f_vals = f_vals[np.isfinite(f_vals)]
                if len(f_vals) > 0:
                    res["final_fy"] = float(f_vals[-1])
                    res["has_forces"] = True
        except Exception:
            pass

    return res


def steady_convergence_to_formatted_table(data: Dict[str, Any]) -> str:
    """Format steady convergence summary into a clean monospace text table."""
    sid = data.get("system_id", "-")
    iters = data.get("iterations", 0)
    m_fin = f"{data['final_moment']:.4f} N·m" if data.get("final_moment") is not None else "N/A"
    m_mean = f"{data['mean_moment']:.4f} N·m" if data.get("mean_moment") is not None else "N/A"
    fx = f"{data['final_fx']:.2f} N" if data.get("final_fx") is not None else "N/A"
    fy = f"{data['final_fy']:.2f} N" if data.get("final_fy") is not None else "N/A"
    slope_str = f"{data['normalized_slope_pct_per_100_iter']:+.3f}% / 100 iters" if data.get("normalized_slope_pct_per_100_iter") is not None else "N/A"
    range_str = f"{data['window_range_pct']:.2f}%" if data.get("window_range_pct") is not None else "N/A"
    status = data.get("status", "NO_DATA")

    lines = [
        "=" * 88,
        f"               STEADY-STATE AERODYNAMICS CONVERGENCE: [{sid}]",
        "=" * 88,
        f" Total Iterations Run          : {iters}",
        f" Total Rotor Moment (Final)    : {m_fin} (All blades + shaft combined)",
        f" Total Rotor Moment (Mean Tail): {m_mean}",
        f" Normalized Tail Slope         : {slope_str}",
        f" Tail Window Range (P-to-P)    : {range_str}",
        f" Convergence Assessment        : {status}",
        f" Streamwise Drag Force (Fx)    : {fx}",
        f" Cross-flow Lateral Force (Fy) : {fy}",
        "-" * 88,
        f" Note: {data.get('note', '')}",
        "=" * 88,
    ]
    return "\n".join(lines)


def plot_steady_convergence(system: FluentSystemInfo, dark_mode: bool = False):
    """
    Generate a 2-subplot convergence plot for steady-state VAWT analysis:
      1. Top plot: Total Rotor Moment vs Iterations (all blades + shaft combined into one total curve).
      2. Bottom plot: Aerodynamic Forces (Fx: Drag, Fy: Lateral) vs Iterations.
    """
    from matplotlib.figure import Figure
    import numpy as np
    from vawt_post.engine.parser import read_fluent_report

    bg_col = "#0F172A" if dark_mode else "#FFFFFF"
    text_col = "#F8FAFC" if dark_mode else "#0F172A"
    grid_col = "#334155" if dark_mode else "#E2E8F0"
    mom_col = "#10B981"  # Emerald
    fx_col = "#3B82F6"   # Blue
    fy_col = "#F59E0B"   # Amber

    fig = Figure(figsize=(7.5, 4.8), dpi=100)
    ax_mom = fig.add_subplot(2, 1, 1)
    ax_force = fig.add_subplot(2, 1, 2, sharex=ax_mom)
    fig.patch.set_facecolor(bg_col)

    for ax in (ax_mom, ax_force):
        ax.set_facecolor(bg_col)
        ax.grid(True, linestyle="--", alpha=0.5, color=grid_col)
        ax.tick_params(colors=text_col)
        for spine in ax.spines.values():
            spine.set_color(text_col)

    # 1. Total Rotor Moment (all blades + shaft combined)
    mom_steps = None
    mom_vals = None

    if system.total_moment_file and os.path.isfile(system.total_moment_file):
        try:
            rep = read_fluent_report(system.total_moment_file)
            mom_steps = rep.get("step")
            mom_vals = rep.get("value")
        except Exception:
            pass

    # If no single total_moment file, sum up all blade moments + shaft
    if (mom_vals is None or len(mom_vals) == 0) and system.blade_moment_files:
        try:
            all_b_vals = []
            for bf in system.blade_moment_files:
                if os.path.isfile(bf):
                    rep = read_fluent_report(bf)
                    if rep.get("value") is not None and len(rep["value"]) > 0:
                        all_b_vals.append(rep["value"])
                        if mom_steps is None:
                            mom_steps = rep.get("step")
            if system.shaft_moment_file and os.path.isfile(system.shaft_moment_file):
                s_rep = read_fluent_report(system.shaft_moment_file)
                if s_rep.get("value") is not None and len(s_rep["value"]) > 0:
                    all_b_vals.append(s_rep["value"])
            if all_b_vals:
                min_len = min(len(v) for v in all_b_vals)
                mom_vals = np.sum([v[:min_len] for v in all_b_vals], axis=0)
                if mom_steps is not None:
                    mom_steps = mom_steps[:min_len]
        except Exception:
            pass

    if mom_vals is not None and len(mom_vals) > 0:
        x_m = mom_steps if mom_steps is not None else np.arange(1, len(mom_vals) + 1)
        ax_mom.plot(x_m, mom_vals, color=mom_col, lw=2.0, label="Total Rotor Moment (Combined All Blades + Shaft)")

        # Converged reference line (mean of tail 50 or tail 15%)
        tail_n = min(len(mom_vals), max(10, int(len(mom_vals) * 0.15)))
        mean_tail = float(np.mean(mom_vals[-tail_n:]))
        ax_mom.axhline(mean_tail, color="#EC4899", linestyle="--", lw=1.2, alpha=0.8,
                       label=f"Converged M = {mean_tail:.4f} N·m")
        ax_mom.set_ylabel("Moment (N·m)", fontsize=10, fontweight="bold", color=text_col)
        ax_mom.set_title(f"System [{system.system_id}] Steady Total Moment Convergence", fontsize=11, fontweight="bold", color=text_col)
        ax_mom.legend(loc="upper right", fontsize=8, facecolor=bg_col, edgecolor=grid_col, labelcolor=text_col)
    else:
        ax_mom.text(0.5, 0.5, "No steady moment monitors found", ha="center", va="center", color=text_col, transform=ax_mom.transAxes)

    # 2. Aerodynamic Forces (Fx and Fy)
    fx_steps, fx_vals = None, None
    fy_steps, fy_vals = None, None

    if system.fx_file and os.path.isfile(system.fx_file):
        try:
            rep = read_fluent_report(system.fx_file)
            fx_steps = rep.get("step")
            fx_vals = rep.get("value")
        except Exception:
            pass

    if system.fy_file and os.path.isfile(system.fy_file):
        try:
            rep = read_fluent_report(system.fy_file)
            fy_steps = rep.get("step")
            fy_vals = rep.get("value")
        except Exception:
            pass

    has_force = False
    if fx_vals is not None and len(fx_vals) > 0:
        has_force = True
        x_fx = fx_steps if fx_steps is not None else np.arange(1, len(fx_vals) + 1)
        tail_fx = min(len(fx_vals), max(10, int(len(fx_vals) * 0.15)))
        m_fx = float(np.mean(fx_vals[-tail_fx:]))
        ax_force.plot(x_fx, fx_vals, color=fx_col, lw=1.8, label=f"Fx (Streamwise Drag, Final={m_fx:.2f} N)")

    if fy_vals is not None and len(fy_vals) > 0:
        has_force = True
        x_fy = fy_steps if fy_steps is not None else np.arange(1, len(fy_vals) + 1)
        tail_fy = min(len(fy_vals), max(10, int(len(fy_vals) * 0.15)))
        m_fy = float(np.mean(fy_vals[-tail_fy:]))
        ax_force.plot(x_fy, fy_vals, color=fy_col, lw=1.8, label=f"Fy (Cross-flow Lateral, Final={m_fy:.2f} N)")

    if has_force:
        ax_force.set_xlabel("Iteration", fontsize=10, fontweight="bold", color=text_col)
        ax_force.set_ylabel("Force (N)", fontsize=10, fontweight="bold", color=text_col)
        ax_force.set_title("Aerodynamic Forces (Fx: Drag, Fy: Lateral)", fontsize=11, fontweight="bold", color=text_col)
        ax_force.legend(loc="upper right", fontsize=8, facecolor=bg_col, edgecolor=grid_col, labelcolor=text_col)
    else:
        ax_force.text(0.5, 0.5, "No steady force monitors found", ha="center", va="center", color=text_col, transform=ax_force.transAxes)
        ax_force.set_xlabel("Iteration", fontsize=10, fontweight="bold", color=text_col)

    fig.tight_layout()
    return fig


def plot_multicase_tsr_overlay(
    sweep_data: Dict[str, Any],
    dark_mode: bool = False
) -> Any:
    """Plot multi-case / multi-system Cp vs TSR performance comparison curve."""
    from matplotlib.figure import Figure
    import numpy as np

    fig = Figure(figsize=(7.5, 4.8), dpi=100)
    ax = fig.add_subplot(111)
    bg_col = "#0F172A" if dark_mode else "#FFFFFF"
    text_col = "#F8FAFC" if dark_mode else "#0F172A"
    grid_col = "#334155" if dark_mode else "#E2E8F0"

    fig.patch.set_facecolor(bg_col)
    ax.set_facecolor(bg_col)
    ax.tick_params(colors=text_col)
    for spine in ax.spines.values():
        spine.set_color(grid_col)
    ax.grid(True, linestyle="--", alpha=0.5, color=grid_col)

    pts = sweep_data.get("points", [])
    if not pts:
        ax.text(0.5, 0.5, "No multi-case TSR points available", ha="center", va="center", color=text_col, transform=ax.transAxes)
        fig.tight_layout()
        return fig

    tsrs = [p["tsr"] for p in pts if p.get("tsr") is not None and p.get("mean_cp") is not None]
    cps = [p["mean_cp"] for p in pts if p.get("tsr") is not None and p.get("mean_cp") is not None]

    if tsrs and cps:
        sorted_pairs = sorted(zip(tsrs, cps), key=lambda x: x[0])
        x_pts, y_pts = zip(*sorted_pairs)
        color = "#38BDF8" if dark_mode else "#2563EB"
        ax.plot(x_pts, y_pts, "o-", color=color, lw=2.0, ms=7, label="Evaluated Cases")

        # Smooth spline if >= 3 points
        if len(x_pts) >= 3:
            try:
                import importlib
                scipy_interp = importlib.import_module("scipy.interpolate")
                PchipInterpolator = getattr(scipy_interp, "PchipInterpolator")
                spline = PchipInterpolator(x_pts, y_pts)
                x_dense = np.linspace(min(x_pts), max(x_pts), 120)
                y_dense = spline(x_dense)
                ax.plot(x_dense, y_dense, "--", color="#EC4899", lw=1.5, alpha=0.8, label="PCHIP Spline Fit")
            except Exception:
                pass

        # Label individual points
        for p in pts:
            if p.get("tsr") is not None and p.get("mean_cp") is not None:
                ax.annotate(
                    f"{p['system_id']}",
                    (p["tsr"], p["mean_cp"]),
                    textcoords="offset points",
                    xytext=(0, 8),
                    ha="center",
                    fontsize=8,
                    color=text_col
                )

    ax.set_xlabel("Tip Speed Ratio λ = ω·R / U", fontsize=11, fontweight="bold", color=text_col)
    ax.set_ylabel("Power Coefficient Cp", fontsize=11, fontweight="bold", color=text_col)
    ax.set_title("Multi-Case VAWT Aerodynamic Performance (Cp vs λ)", fontsize=12, fontweight="bold", color=text_col)
    ax.legend(loc="best", facecolor=bg_col, edgecolor=grid_col, labelcolor=text_col)
    fig.tight_layout()
    return fig


def plot_multicase_cm_azimuth_overlay(
    systems_data: List[Dict[str, Any]],
    dark_mode: bool = False
) -> Any:
    """Plot multi-case phase-averaged instantaneous Cm(theta) over 0-360 degrees."""
    from matplotlib.figure import Figure
    import numpy as np

    fig = Figure(figsize=(7.5, 4.8), dpi=100)
    ax = fig.add_subplot(111)
    bg_col = "#0F172A" if dark_mode else "#FFFFFF"
    text_col = "#F8FAFC" if dark_mode else "#0F172A"
    grid_col = "#334155" if dark_mode else "#E2E8F0"

    fig.patch.set_facecolor(bg_col)
    ax.set_facecolor(bg_col)
    ax.tick_params(colors=text_col)
    for spine in ax.spines.values():
        spine.set_color(grid_col)
    ax.grid(True, linestyle="--", alpha=0.5, color=grid_col)

    palette = ["#38BDF8", "#10B981", "#F59E0B", "#EC4899", "#8B5CF6", "#06B6D4", "#EF4444"]

    has_curves = False
    for i, s in enumerate(systems_data):
        cm_arr = s.get("cm_arr")
        theta_arr = s.get("theta_arr")
        sid = s.get("system_id", f"Case {i+1}")
        is_base = s.get("is_baseline", False)
        if cm_arr is not None and len(cm_arr) > 0:
            has_curves = True
            th = theta_arr if theta_arr is not None else np.linspace(0, 360, len(cm_arr), endpoint=False)
            col = palette[i % len(palette)]
            lbl = f"{sid} [★ BASE]" if is_base else f"{sid}"
            lw = 2.4 if is_base else 1.6
            ax.plot(th, cm_arr, color=col, lw=lw, label=lbl, alpha=0.9)

    if has_curves:
        ax.set_xlabel("Rotor Azimuth Angle θ (°)", fontsize=11, fontweight="bold", color=text_col)
        ax.set_ylabel("Rotor Moment (N·m)", fontsize=11, fontweight="bold", color=text_col)
        ax.set_title("Multi-Case Phase-Averaged Moment Overlay (0° – 360°)", fontsize=12, fontweight="bold", color=text_col)
        ax.set_xlim(0, 360)
        ax.legend(loc="upper right", facecolor=bg_col, edgecolor=grid_col, labelcolor=text_col)
    else:
        ax.text(0.5, 0.5, "No azimuth moment data available for selected cases", ha="center", va="center", color=text_col, transform=ax.transAxes)

    fig.tight_layout()
    return fig


def plot_multicase_ripple_comparison(
    systems_data: List[Dict[str, Any]],
    dark_mode: bool = False
) -> Any:
    """Plot torque ripple factor comparison bar chart across multiple cases."""
    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.5, 4.8), dpi=100)
    ax = fig.add_subplot(111)
    bg_col = "#0F172A" if dark_mode else "#FFFFFF"
    text_col = "#F8FAFC" if dark_mode else "#0F172A"
    grid_col = "#334155" if dark_mode else "#E2E8F0"

    fig.patch.set_facecolor(bg_col)
    ax.set_facecolor(bg_col)
    ax.tick_params(colors=text_col)
    for spine in ax.spines.values():
        spine.set_color(grid_col)
    ax.grid(axis="y", linestyle="--", alpha=0.5, color=grid_col)

    sids = []
    ripples = []
    colors = []
    for s in systems_data:
        rf = s.get("ripple_factor")
        if rf is not None:
            sids.append(s.get("system_id", "Case"))
            ripples.append(rf)
            colors.append("#EC4899" if s.get("is_baseline") else "#38BDF8")

    if ripples:
        bars = ax.bar(sids, ripples, color=colors, width=0.45, edgecolor=grid_col, alpha=0.85)
        for b, r_val in zip(bars, ripples):
            ax.annotate(
                f"{r_val:.2f}",
                (b.get_x() + b.get_width() / 2, b.get_height()),
                textcoords="offset points",
                xytext=(0, 4),
                ha="center",
                fontsize=9,
                color=text_col,
                fontweight="bold"
            )
        ax.set_ylabel("Torque Ripple Factor TR = (M_max - M_min) / M_mean", fontsize=10, fontweight="bold", color=text_col)
        ax.set_title("Multi-Case Torque Ripple Comparison", fontsize=12, fontweight="bold", color=text_col)
    else:
        ax.text(0.5, 0.5, "No ripple data available for selected cases", ha="center", va="center", color=text_col, transform=ax.transAxes)

    fig.tight_layout()
    return fig


def save_workbench_session(session_path: str, data: Dict[str, Any]) -> str:
    """Save Workbench multi-case study session to JSON file."""
    import json
    os.makedirs(os.path.dirname(os.path.abspath(session_path)), exist_ok=True)
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return session_path


def load_workbench_session(session_path: str) -> Dict[str, Any]:
    """Load Workbench multi-case study session from JSON file."""
    import json
    with open(session_path, "r", encoding="utf-8") as fh:
        return json.load(fh)




