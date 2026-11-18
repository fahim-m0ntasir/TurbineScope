# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Native HDF5 Case and Mesh Inspector
============================================================
Directly inspects ANSYS Fluent 2024 R2 (.cas.h5, .msh.h5) files without launching Fluent.
Extracts:
  - 2D/3D dimension, cell count, node count
  - Zone topology: rotating, stationary, inlet, outlet, side walls, blades, shaft, interfaces
  - Mesh geometry: blade count, centroid-based rotor radius R, blade chord c
  - Solver setup: inlet velocity U, angular velocity omega, density, viscosity
  - Report definitions: moment (total & per-blade), forcex, forcey, moment center & axis
  - Report files: output paths, frequency, linked definitions
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    h5py = None
    HAS_H5PY = False


@dataclass
class HDF5Zone:
    zone_id: int
    name: str
    zone_type: int
    type_name: str = ""


@dataclass
class HDF5ReportDef:
    name: str
    def_type: str = ""                  # moment, force, etc.
    threads: List[str] = field(default_factory=list)
    mom_center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    mom_axis: Tuple[float, float, float] = (0.0, 0.0, 1.0)


@dataclass
class HDF5ReportFile:
    name: str
    file_name: str
    frequency: int = 1
    frequency_of: str = "iteration"     # iteration or time-step
    report_defs: List[str] = field(default_factory=list)


@dataclass
class CaseInspectionResult:
    file_path: str
    is_valid: bool = False
    error_message: Optional[str] = None
    dimension: int = 2
    cell_count: int = 0
    node_count: int = 0
    face_count: int = 0

    # Zones
    cell_zones: List[HDF5Zone] = field(default_factory=list)
    face_zones: List[HDF5Zone] = field(default_factory=list)
    rotating_zone: Optional[str] = None
    stationary_zone: Optional[str] = None
    inlet_zone: Optional[str] = None
    outlet_zone: Optional[str] = None
    blade_zones: List[str] = field(default_factory=list)
    shaft_zone: Optional[str] = None
    interface_zones: List[str] = field(default_factory=list)

    # Geometry derived from blade coordinates
    blade_count: int = 0
    rotor_radius_m: Optional[float] = None
    chord_m: Optional[float] = None
    blade_centroids: List[Tuple[float, float]] = field(default_factory=list)

    # Physics from case settings
    velocity_m_s: Optional[float] = None
    omega_rad_s: Optional[float] = None
    time_step_s: Optional[float] = None
    density_kg_m3: Optional[float] = None
    viscosity_pa_s: Optional[float] = None
    turb_model: Optional[str] = None
    time_model: Optional[str] = None

    # Report definitions and files
    report_definitions: Dict[str, HDF5ReportDef] = field(default_factory=dict)
    report_files: Dict[str, HDF5ReportFile] = field(default_factory=dict)

    # Verification badges / confidence
    confidence: Dict[str, str] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        aliases = {
            "rotor_radius": "rotor_radius_m",
            "chord": "chord_m",
            "velocity": "velocity_m_s",
            "omega": "omega_rad_s",
            "time_step": "time_step_s",
            "density": "density_kg_m3",
            "viscosity": "viscosity_pa_s",
        }
        resolved = aliases.get(key, key)
        if hasattr(self, resolved):
            return getattr(self, resolved)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self[key]
            return default if val is None else val
        except (KeyError, AttributeError):
            return default

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def inspect_hdf5_case(cas_path: str) -> CaseInspectionResult:
    """Inspect an ANSYS Fluent .cas.h5 or .msh.h5 file and return CaseInspectionResult."""
    res = CaseInspectionResult(file_path=cas_path)
    if not os.path.isfile(cas_path):
        res.error_message = f"File not found: {cas_path}"
        return res

    if not HAS_H5PY or h5py is None:
        res.error_message = "h5py library is not installed"
        return res

    try:
        with h5py.File(cas_path, "r") as _f:
            f: Any = _f
            res.is_valid = True

            # 1. Parse Mesh Topology and Counts
            if "meshes" in f:
                mesh_keys = list(f["meshes"].keys())
                if mesh_keys:
                    m = f["meshes"][mesh_keys[0]]

                    # Dimension and Cell count
                    if "cells" in m and "zoneTopology" in m["cells"]:
                        zt = m["cells"]["zoneTopology"]
                        if "dimension" in zt:
                            dims = zt["dimension"][:]
                            res.dimension = int(dims[0]) if len(dims) > 0 else 2
                        if "minId" in zt and "maxId" in zt:
                            min_ids = zt["minId"][:]
                            max_ids = zt["maxId"][:]
                            res.cell_count = int(np.sum(max_ids - min_ids + 1))

                        if "name" in zt and "id" in zt:
                            raw_cn = zt["name"][0]
                            names_raw = (raw_cn.decode("utf-8", errors="ignore") if isinstance(raw_cn, (bytes, bytearray)) else str(raw_cn)).split(";")
                            ids = zt["id"][:]
                            types = zt["zoneType"][:] if "zoneType" in zt else [0] * len(names_raw)
                            for z_id, z_name, z_type in zip(ids, names_raw, types):
                                zone = HDF5Zone(zone_id=int(z_id), name=z_name, zone_type=int(z_type))
                                res.cell_zones.append(zone)
                                if "rotat" in z_name.lower():
                                    res.rotating_zone = z_name
                                elif "station" in z_name.lower() or "fluid" in z_name.lower():
                                    if not res.stationary_zone:
                                        res.stationary_zone = z_name

                    # Faces and Face Zones
                    face_names: List[str] = []
                    face_min_ids = np.array([])
                    face_max_ids = np.array([])
                    if "faces" in m and "zoneTopology" in m["faces"]:
                        zf = m["faces"]["zoneTopology"]
                        if "minId" in zf and "maxId" in zf:
                            face_min_ids = zf["minId"][:]
                            face_max_ids = zf["maxId"][:]
                            res.face_count = int(np.sum(face_max_ids - face_min_ids + 1))

                        if "name" in zf and "id" in zf:
                            raw_fn = zf["name"][0]
                            face_names = (raw_fn.decode("utf-8", errors="ignore") if isinstance(raw_fn, (bytes, bytearray)) else str(raw_fn)).split(";")
                            f_ids = zf["id"][:]
                            has_ftypes = "zoneType" in zf
                            f_types = zf["zoneType"][:] if has_ftypes else [0] * len(face_names)
                            for z_id, z_name, z_type in zip(f_ids, face_names, f_types):
                                zt_int = int(z_type)
                                zone = HDF5Zone(zone_id=int(z_id), name=z_name, zone_type=zt_int)
                                res.face_zones.append(zone)
                                low = z_name.lower()
                                is_non_wall = (has_ftypes and zt_int != 3) or ("interior" in low) or ("interface" in low) or ("contact" in low)
                                if "inlet" in low:
                                    res.inlet_zone = z_name
                                elif "outlet" in low:
                                    res.outlet_zone = z_name
                                elif "interface" in low or "contact" in low:
                                    res.interface_zones.append(z_name)
                                elif ("shaft" in low or "tower" in low) and not is_non_wall:
                                    res.shaft_zone = z_name
                                elif ("blade" in low or "airfoil" in low) and not is_non_wall:
                                    res.blade_zones.append(z_name)

                    # Node count
                    coords = None
                    if "nodes" in m:
                        if "coords" in m["nodes"]:
                            coord_keys = list(m["nodes"]["coords"].keys())
                            if coord_keys:
                                coords = m["nodes"]["coords"][coord_keys[0]][:]
                                res.node_count = len(coords)

                    # Extract Blade Geometry (R, chord, centroids in XY rotation plane)
                    if res.blade_zones and coords is not None and len(face_names) > 0 and "nodes" in m["faces"]:
                        try:
                            fn_group = m["faces"]["nodes"]
                            fn_keys = list(fn_group.keys())
                            if fn_keys and "nnodes" in fn_group[fn_keys[0]] and "nodes" in fn_group[fn_keys[0]]:
                                nnodes = fn_group[fn_keys[0]]["nnodes"][:]
                                nodes_flat = fn_group[fn_keys[0]]["nodes"][:]
                                offsets = np.zeros(len(nnodes) + 1, dtype=np.int64)
                                np.cumsum(nnodes, out=offsets[1:])

                                blade_radii = []
                                blade_chords = []
                                for b_name in res.blade_zones:
                                    if b_name in face_names:
                                        b_idx = face_names.index(b_name)
                                        b_min = face_min_ids[b_idx]
                                        b_max = face_max_ids[b_idx]
                                        start_pos = offsets[b_min - 1]
                                        end_pos = offsets[b_max]
                                        b_nodes = np.unique(nodes_flat[start_pos:end_pos])
                                        if len(b_nodes) > 0:
                                            b_xy = coords[b_nodes - 1, :2]
                                            c_xy = np.mean(b_xy, axis=0)
                                            r_val = float(np.linalg.norm(c_xy))
                                            res.blade_centroids.append((float(c_xy[0]), float(c_xy[1])))
                                            blade_radii.append(r_val)

                                            # Max 2D distance between points = chord (avoid O(N^2) blowup on 3D meshes)
                                            if len(b_xy) <= 2000:
                                                diffs = b_xy[:, None, :] - b_xy[None, :, :]
                                                dists = np.sqrt(np.sum(diffs**2, axis=-1))
                                                blade_chords.append(float(np.max(dists)))
                                            else:
                                                # Project onto principal axis + sample extremal hull points in O(N)
                                                centered = b_xy - c_xy
                                                _, _, vh = np.linalg.svd(centered, full_matrices=False)
                                                proj0 = centered @ vh[0]
                                                proj1 = centered @ vh[1]
                                                ext_idx = np.unique([
                                                    int(np.argmin(proj0)), int(np.argmax(proj0)),
                                                    int(np.argmin(proj1)), int(np.argmax(proj1)),
                                                ])
                                                sub_xy = b_xy[ext_idx]
                                                diffs = sub_xy[:, None, :] - sub_xy[None, :, :]
                                                blade_chords.append(float(np.max(np.sqrt(np.sum(diffs**2, axis=-1)))))

                                if blade_radii:
                                    res.rotor_radius_m = float(np.mean(blade_radii))
                                    res.confidence["rotor_radius_m"] = "verified_from_mesh_nodes"
                                if blade_chords:
                                    res.chord_m = float(np.mean(blade_chords))
                                    res.confidence["chord_m"] = "verified_from_mesh_nodes"
                                res.blade_count = len(res.blade_zones)
                                res.confidence["blade_count"] = "verified_from_blade_walls"
                        except Exception:
                            pass

            # 2. Parse Solver Settings & Boundary Conditions
            _NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
            if "settings" in f:
                s = f["settings"]

                # Thread Variables (boundary conditions & zone motion)
                if "Thread Variables" in s:
                    tv_raw = s["Thread Variables"][0]
                    tv = tv_raw.decode("utf-8", errors="ignore") if isinstance(tv_raw, bytes) else str(tv_raw)

                    # Moving Mesh / MRF Omega in rotating zone (skip stationary zones with omega=0)
                    omega_cands = re.findall(rf"\bomega\s+\.\s+({_NUM})\)", tv)
                    omega_cands += re.findall(rf"mrf-omega\s*\(constant\s+\.\s+({_NUM})\)", tv)
                    for oc in omega_cands:
                        try:
                            ov = float(oc)
                            if abs(ov) > 0.0:
                                res.omega_rad_s = ov
                                res.confidence["omega_rad_s"] = "verified_from_case_settings"
                                break
                        except ValueError:
                            continue
                    if res.omega_rad_s is None and omega_cands:
                        try:
                            res.omega_rad_s = float(omega_cands[0])
                            res.confidence["omega_rad_s"] = "verified_from_case_settings"
                        except ValueError:
                            pass

                    # Velocity magnitude in inlet
                    m_vmag = re.search(rf"velocity-inlet[^\)]*\).*?\(vmag\s*\(constant\s+\.\s+({_NUM})\)", tv, re.DOTALL)
                    if not m_vmag:
                        m_vmag = re.search(rf"\(vmag\s*\(constant\s+\.\s+({_NUM})\)", tv)
                    if m_vmag:
                        try:
                            res.velocity_m_s = float(m_vmag.group(1))
                            res.confidence["velocity_m_s"] = "verified_from_case_settings"
                        except ValueError:
                            pass

                # Rampant Variables (report definitions and report files)
                if "Rampant Variables" in s:
                    rv_raw = s["Rampant Variables"][0]
                    rv = rv_raw.decode("utf-8", errors="ignore") if isinstance(rv_raw, bytes) else str(rv_raw)

                    # Extract Report Definitions
                    rep_def_matches = re.finditer(r'\(name\s+\.\s+"([^"]+)"\)\s*\(report-definition\s+([a-zA-Z0-9_\-]+).*?\(thread-names\s+([^)]*)\)', rv)
                    for rm in rep_def_matches:
                        d_name = rm.group(1)
                        d_type = rm.group(2)
                        d_threads = rm.group(3).split()
                        rdef = HDF5ReportDef(name=d_name, def_type=d_type, threads=d_threads)

                        # Check moment center & axis
                        sub = rv[rm.start():rm.start() + 600]
                        m_center = re.search(rf'mom-center\s+({_NUM})\s+({_NUM})\s+({_NUM})', sub)
                        if m_center:
                            try:
                                rdef.mom_center = (float(m_center.group(1)), float(m_center.group(2)), float(m_center.group(3)))
                            except ValueError:
                                pass
                        m_axis = re.search(rf'mom-axis\s+({_NUM})\s+({_NUM})\s+({_NUM})', sub)
                        if m_axis:
                            try:
                                rdef.mom_axis = (float(m_axis.group(1)), float(m_axis.group(2)), float(m_axis.group(3)))
                            except ValueError:
                                pass
                        res.report_definitions[d_name] = rdef

                    # Extract Report Files (monitor sets)
                    rep_file_matches = re.finditer(r'\(name\s+\.\s+"([^"]+)"\).*?\(file-name\s+\.\s+"([^"]+)"\).*?\(frequency\s+\.\s+([0-9]+)\).*?\(report-defs\s+([^)]+)\)', rv)
                    for rfm in rep_file_matches:
                        rf_name = rfm.group(1)
                        rf_path = rfm.group(2)
                        rf_freq = int(rfm.group(3))
                        rf_defs = [x.strip('"') for x in rfm.group(4).split()]
                        rfile = HDF5ReportFile(name=rf_name, file_name=rf_path, frequency=rf_freq, report_defs=rf_defs)
                        res.report_files[rf_name] = rfile

                    # Extract time step size if transient (Fluent legacy & 2024 R2 physical-time-step keys)
                    m_dt_explicit = re.search(rf'\(time-step-size\s+\.\s+({_NUM})\)', rv)
                    m_dt_phys = (
                        re.search(rf'\(physical-time-step\s+({_NUM})\)', rv)
                        or re.search(rf'\(physical-time-step-expr\s+"({_NUM})"\)', rv)
                    )
                    m_dt = m_dt_explicit or m_dt_phys
                    if m_dt:
                        try:
                            dt_v = float(m_dt.group(1))
                            # Fluent Workbench Setup-Output / un-stepped cases store factory default 1.0 s
                            # in (physical-time-step 1.); ignore that placeholder so report.xml / .out supply real dt.
                            is_setup_placeholder = (
                                (m_dt_explicit is None)
                                and abs(dt_v - 1.0) <= 1e-9
                                and (
                                    "setup" in os.path.basename(cas_path).lower()
                                    or bool(re.search(r'\(number-of-time-steps\s+0(?:\.0*)?\)', rv))
                                    or bool(re.search(r'\(time-step\s+0(?:\.0*)?\)', rv))
                                )
                            )
                            if dt_v > 0 and not is_setup_placeholder:
                                res.time_step_s = dt_v
                                res.confidence["time_step_s"] = "verified_from_case_settings"
                        except ValueError:
                            pass

                    # Viscous / Turbulence model detection using exact Scheme boolean flags
                    def _is_true(flag: str) -> bool:
                        return bool(re.search(rf"\({flag}\s+(?:\.|\s)*(?:#(?:t|T)|1\b)", rv, re.IGNORECASE))

                    if _is_true(r"rp-inviscid\?"):
                        res.turb_model = "Inviscid"
                    elif _is_true(r"rp-lam\?"):
                        res.turb_model = "Laminar"
                    elif _is_true(r"rp-sa\?"):
                        res.turb_model = "Spalart-Allmaras"
                    elif _is_true(r"rp-trans-sst\?"):
                        res.turb_model = "Transition SST"
                    elif _is_true(r"rp-trans-one-eqn\?"):
                        res.turb_model = "Transition (1-eqn)"
                    elif _is_true(r"rp-des\?"):
                        res.turb_model = "Detached Eddy Simulation (DES)"
                    elif _is_true(r"rp-les\?"):
                        res.turb_model = "Large Eddy Simulation (LES)"
                    elif _is_true(r"rp-sas\?"):
                        res.turb_model = "Scale-Adaptive Simulation (SAS)"
                    elif _is_true(r"rp-rsm\?") or _is_true(r"drsm\?"):
                        res.turb_model = "Reynolds Stress Model (RSM)"
                    elif _is_true(r"rp-kw\?"):
                        if _is_true(r"kw-geko-on\?"):
                            res.turb_model = "GEKO k-omega"
                        elif _is_true(r"kw-standard\?"):
                            res.turb_model = "Standard k-omega"
                        elif _is_true(r"kw-bsl\?"):
                            res.turb_model = "BSL k-omega"
                        else:
                            res.turb_model = "k-omega SST"
                    elif _is_true(r"rp-ke\?"):
                        if _is_true(r"ke-realizable\?"):
                            res.turb_model = "Realizable k-epsilon"
                        elif _is_true(r"ke-rng\?"):
                            res.turb_model = "RNG k-epsilon"
                        else:
                            res.turb_model = "Standard k-epsilon"
                    elif _is_true(r"rp-turb\?"):
                        res.turb_model = "Turbulent (Generic)"

                    if res.turb_model:
                        res.confidence["turb_model"] = "verified_from_scheme_flags"

                    # Temporal model detection
                    if _is_true(r"unsteady-2nd-order\?") or _is_true(r"rp-unsteady-2nd-order\?"):
                        res.time_model = "Unsteady (2nd-Order Implicit)"
                    elif _is_true(r"unsteady-1st-order\?") or _is_true(r"rp-unsteady-1st-order\?"):
                        res.time_model = "Unsteady (1st-Order Implicit)"
                    elif _is_true(r"unsteady\?") or _is_true(r"rp-unsteady\?"):
                        res.time_model = "Unsteady"
                    else:
                        res.time_model = "Steady"

                    # Reference fluid properties
                    m_rho = re.search(rf"\(reference-density\s+({_NUM})\)", rv)
                    if m_rho and res.density_kg_m3 is None:
                        try:
                            res.density_kg_m3 = float(m_rho.group(1))
                        except ValueError:
                            pass
                    m_mu = re.search(rf"\(reference-viscosity\s+({_NUM})\)", rv)
                    if m_mu and res.viscosity_pa_s is None:
                        try:
                            res.viscosity_pa_s = float(m_mu.group(1))
                        except ValueError:
                            pass

    except Exception as exc:
        res.is_valid = False
        res.error_message = f"Error inspecting HDF5 case: {str(exc)}"

    return res
