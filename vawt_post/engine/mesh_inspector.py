# -*- coding: utf-8 -*-
"""
TurbineScope — Workbench System Context, Mesh Sizing & Quality Inspector
========================================================================
Resolves ANSYS Workbench system directories (`dp0/FFF`, `dp0/FFF-{number}`,
`.wbpj` projects, or standalone `Fluent/` run folders) and extracts detailed
mesh sizing, boundary-layer resolution, mesh quality, and solver setup notes
for Live Monitor alerts, Auto-Detect notes, and the Excel `Mesh_Info` sheet.
"""
from __future__ import annotations

import glob
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    h5py = None
    HAS_H5PY = False


# Cache heavy HDF5 quality computations keyed by (norm_path, mtime_ns, rotor_sig)
_MESH_QUALITY_CACHE: Dict[Tuple[str, int, Tuple[Any, ...]], Dict[str, Any]] = {}


def _pick_latest_file(paths: List[str], prefer_not_setup: bool = False) -> Optional[str]:
    """Select the most recent existing file, optionally preferring solved checkpoints over Setup files."""
    existing = [p for p in paths if p and os.path.isfile(p)]
    if not existing:
        return None
    if prefer_not_setup:
        existing.sort(
            key=lambda p: (
                0 if "setup" in os.path.basename(p).lower() else 1,
                os.path.getmtime(p),
            ),
            reverse=True,
        )
    else:
        existing.sort(key=os.path.getmtime, reverse=True)
    return os.path.abspath(existing[0])


def resolve_workbench_system_context(path_or_folder: str) -> Dict[str, Any]:
    """Resolve an ANSYS Workbench `dp0/FFF-{number}` system root or Fluent run folder.

    Returns a dictionary with normalized directory paths (`system_dir`, `system_id`,
    `fluent_dir`, `mech_dir`, `dm_dir`, `dp_dir`, `project_root`, `global_mech_dir`,
    `progress_fluent_dir`) and discovered artifact paths (`case_h5`, `setup_case_h5`,
    `dat_h5`, `msh_file`, `mshdb_file`, `agdb_file`, `set_file`, `report_xml`,
    `transcript_file`, `wbdp_file`).
    """
    ctx: Dict[str, Any] = {
        "input_path": path_or_folder or "",
        "is_workbench_system": False,
        "system_id": None,
        "system_dir": None,
        "fluent_dir": None,
        "mech_dir": None,
        "dm_dir": None,
        "dp_dir": None,
        "files_root": None,
        "project_root": None,
        "global_mech_dir": None,
        "progress_fluent_dir": None,
        "case_h5": None,
        "setup_case_h5": None,
        "legacy_case": None,
        "dat_h5": None,
        "msh_file": None,
        "mshdb_file": None,
        "agdb_file": None,
        "set_file": None,
        "report_xml": None,
        "transcript_file": None,
        "wbdp_file": None,
    }
    if not path_or_folder:
        return ctx

    p = os.path.abspath(path_or_folder)
    if os.path.isfile(p):
        if p.lower().endswith(".wbpj"):
            p = os.path.dirname(p)
        else:
            p = os.path.dirname(p)

    if not os.path.isdir(p):
        return ctx

    # Case 1: User selected Fluent/, MECH/, or DM/ directly inside dp0/FFF-{n}
    base = os.path.basename(p)
    parent = os.path.dirname(p)
    if base.lower() in ("fluent", "mech", "dm") and os.path.basename(os.path.dirname(parent)).lower() == "dp0":
        p = parent
        base = os.path.basename(p)
        parent = os.path.dirname(p)

    # Case 2: User selected dp0/FFF or dp0/FFF-{number}
    sys_dir: Optional[str] = None
    fluent_dir: Optional[str] = None
    dp_dir: Optional[str] = None
    sys_id: Optional[str] = None

    if os.path.basename(parent).lower() == "dp0" and re.match(r"^(FFF|FLTG|FLUID)(-\d+)?$", base, re.IGNORECASE):
        sys_dir = p
        dp_dir = parent
        sys_id = base
        ctx["is_workbench_system"] = True
        cand_fl = os.path.join(sys_dir, "Fluent")
        fluent_dir = cand_fl if os.path.isdir(cand_fl) else sys_dir
    elif os.path.isdir(os.path.join(p, "Fluent")) and (
        os.path.isdir(os.path.join(p, "MECH")) or os.path.isdir(os.path.join(p, "DM"))
    ):
        # Standalone extracted FFF folder having Fluent/ and MECH/ or DM/
        sys_dir = p
        sys_id = base
        ctx["is_workbench_system"] = True
        fluent_dir = os.path.join(sys_dir, "Fluent")
        if os.path.basename(parent).lower() == "dp0":
            dp_dir = parent
    else:
        # Case 3: User selected a .wbpj folder, *_files folder, or dp0 folder -> use scanner
        try:
            from vawt_post.engine.workbench import scan_workbench_project
            wb = scan_workbench_project(p)
            if wb and wb.systems:
                cands = []
                for s in wb.systems.values():
                    has_mon = bool(s.total_moment_file or s.blade_moment_files or s.fx_file or s.fy_file)
                    has_case = bool(s.case_path)
                    score = 2 if has_mon else (1 if has_case else 0)
                    cands.append((s, score, s.is_actively_writing, s.latest_mtime))
                cands.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
                best = cands[0][0]
                sys_dir = best.system_dir
                sys_id = best.system_id
                fluent_dir = best.fluent_dir
                ctx["is_workbench_system"] = True
                if sys_dir and os.path.basename(os.path.dirname(sys_dir)).lower() == "dp0":
                    dp_dir = os.path.dirname(sys_dir)
        except Exception:
            pass

    if not fluent_dir:
        # Plain Fluent folder fallback
        fluent_dir = p

    ctx["system_id"] = sys_id
    ctx["system_dir"] = sys_dir or fluent_dir
    ctx["fluent_dir"] = fluent_dir
    ctx["dp_dir"] = dp_dir

    if sys_dir:
        mech_dir = os.path.join(sys_dir, "MECH")
        dm_dir = os.path.join(sys_dir, "DM")
        if os.path.isdir(mech_dir):
            ctx["mech_dir"] = mech_dir
        if os.path.isdir(dm_dir):
            ctx["dm_dir"] = dm_dir

    if dp_dir:
        files_root = os.path.dirname(dp_dir)
        ctx["files_root"] = files_root
        ctx["project_root"] = os.path.dirname(files_root)
        g_mech = os.path.join(dp_dir, "global", "MECH")
        if os.path.isdir(g_mech):
            ctx["global_mech_dir"] = g_mech
        wbdp = os.path.join(dp_dir, "designPoint.wbdp")
        if os.path.isfile(wbdp):
            ctx["wbdp_file"] = wbdp
        if sys_id:
            prog_fl = os.path.join(files_root, "progress_files", "dp0", sys_id, "Fluent")
            if os.path.isdir(prog_fl):
                ctx["progress_fluent_dir"] = prog_fl

    # Artifact discovery inside resolved dirs
    fl = ctx["fluent_dir"]
    if fl and os.path.isdir(fl):
        cas_h5_list = glob.glob(os.path.join(fl, "*.cas.h5"))
        ctx["case_h5"] = _pick_latest_file(cas_h5_list, prefer_not_setup=True)
        setup_cands = [c for c in cas_h5_list if "setup" in os.path.basename(c).lower()]
        ctx["setup_case_h5"] = _pick_latest_file(setup_cands, prefer_not_setup=False)
        leg_cas = [c for c in glob.glob(os.path.join(fl, "*.cas")) if not c.lower().endswith(".cas.h5")]
        ctx["legacy_case"] = _pick_latest_file(leg_cas, prefer_not_setup=True)
        ctx["dat_h5"] = _pick_latest_file(glob.glob(os.path.join(fl, "*.dat.h5")))
        ctx["set_file"] = _pick_latest_file(glob.glob(os.path.join(fl, "*.set")))

        trn_cands = glob.glob(os.path.join(fl, "*.trn")) + glob.glob(os.path.join(fl, "*.log"))
        if ctx["progress_fluent_dir"]:
            trn_cands.extend(glob.glob(os.path.join(ctx["progress_fluent_dir"], "*.trn")))
        ctx["transcript_file"] = _pick_latest_file(trn_cands)

    xml_cands: List[str] = []
    if ctx["progress_fluent_dir"]:
        xml_cands.append(os.path.join(ctx["progress_fluent_dir"], "report.xml"))
    if fl:
        xml_cands.append(os.path.join(fl, "report.xml"))
    if sys_dir:
        xml_cands.append(os.path.join(sys_dir, "report.xml"))
    ctx["report_xml"] = _pick_latest_file(xml_cands)

    if ctx["mech_dir"]:
        ctx["msh_file"] = _pick_latest_file(
            glob.glob(os.path.join(ctx["mech_dir"], "*.msh*"))
        )
    if ctx["global_mech_dir"]:
        mshdb_cands = []
        if sys_id:
            mshdb_cands.extend(glob.glob(os.path.join(ctx["global_mech_dir"], f"{sys_id}*.mshdb")))
        if not mshdb_cands:
            mshdb_cands.extend(glob.glob(os.path.join(ctx["global_mech_dir"], "*.mshdb")))
        ctx["mshdb_file"] = _pick_latest_file(mshdb_cands)

    if ctx["dm_dir"]:
        agdb_cands = (
            glob.glob(os.path.join(ctx["dm_dir"], "*.agdb"))
            + glob.glob(os.path.join(ctx["dm_dir"], "*.scdoc"))
            + glob.glob(os.path.join(ctx["dm_dir"], "*.step"))
            + glob.glob(os.path.join(ctx["dm_dir"], "*.stp"))
        )
        ctx["agdb_file"] = _pick_latest_file(agdb_cands)

    return ctx


def parse_msh_file_summary(msh_path: str) -> Dict[str, Any]:
    """Parse counts, dimension, and zone names from a Fluent ASCII/binary `.msh` file."""
    info: Dict[str, Any] = {
        "path": msh_path,
        "dimension": None,
        "nodes": None,
        "faces": None,
        "cells": None,
        "zones": [],
    }
    if not msh_path or not os.path.isfile(msh_path):
        return info

    try:
        size = os.path.getsize(msh_path)
        with open(msh_path, "r", encoding="latin-1", errors="ignore") as f:
            head = f.read(min(size, 65536))
            tail = ""
            if size > 65536:
                f.seek(max(0, size - 65536))
                tail = f.read()

        m_dim = re.search(r"^\(\s*2\s+(\d+)\s*\)", head, re.MULTILINE)
        if m_dim:
            info["dimension"] = int(m_dim.group(1))
        m_node = re.search(r"\(10\s*\(\s*0\s+1\s+([0-9a-fA-F]+)\b", head)
        if m_node:
            info["nodes"] = int(m_node.group(1), 16)
        m_face = re.search(r"\(13\s*\(\s*0\s+1\s+([0-9a-fA-F]+)\b", head)
        if m_face:
            info["faces"] = int(m_face.group(1), 16)
        m_cell = re.search(r"\(12\s*\(\s*0\s+1\s+([0-9a-fA-F]+)\b", head)
        if m_cell:
            info["cells"] = int(m_cell.group(1), 16)

        zone_matches = re.findall(
            r"\((?:39|45)\s*\(\s*(\d+)\s+([a-zA-Z0-9_\-]+)\s+([a-zA-Z0-9_.\-]+)",
            head + "\n" + tail,
        )
        seen = set()
        for zid, ztype, zname in zone_matches:
            key = (int(zid), zname)
            if key not in seen:
                seen.add(key)
                info["zones"].append({"id": int(zid), "type": ztype, "name": zname})
    except Exception:
        pass
    return info


def parse_detailed_setup_xml(xml_path: str) -> Dict[str, Any]:
    """Extract solver models, boundary conditions, schemes, and limits from Fluent `report.xml`."""
    out: Dict[str, Any] = {}
    if not xml_path or not os.path.isfile(xml_path):
        return out

    try:
        from vawt_post.engine.workbench import parse_fluent_report_xml
        base_xml = parse_fluent_report_xml(xml_path)
        out.update({k: v for k, v in base_xml.items() if v is not None})

        with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()

        m_ver = re.search(r"Version:\s*(.+)", raw)
        if m_ver:
            out["fluent_version"] = m_ver.group(1).strip()
        m_rel = re.search(r"Release:\s*(.+)", raw)
        if m_rel:
            out["fluent_release"] = m_rel.group(1).strip()

        m_pv = re.search(r"Pressure-Velocity Coupling.*?Type\s+([A-Za-z0-9_\- ]+)", raw, re.DOTALL)
        if m_pv:
            out["pv_coupling"] = m_pv.group(1).strip().splitlines()[0].strip()

        # Extract discretization schemes block
        schemes: Dict[str, str] = {}
        m_disc = re.search(r"Discretization Scheme\s*\n\s*-+\s*\n(.*?)(?:\n\s*\n|\n\s*Solution Limits)", raw, re.DOTALL)
        if m_disc:
            for line in m_disc.group(1).splitlines():
                line = line.strip()
                if not line or line.startswith("Variable") or set(line) == {"-"}:
                    continue
                parts = re.split(r"\s{2,}", line)
                if len(parts) >= 2:
                    schemes[parts[0].strip()] = parts[1].strip()
        if schemes:
            out["discretization_schemes"] = schemes

        # Under-relaxation factors
        urf: Dict[str, str] = {}
        m_urf = re.search(r"Under-Relaxation Factors\s*\n\s*-+\s*\n(.*?)(?:\n\s*\n|\n\s*Linear Solver)", raw, re.DOTALL)
        if m_urf:
            for line in m_urf.group(1).splitlines():
                line = line.strip()
                if not line or line.startswith("Variable") or set(line) == {"-"}:
                    continue
                parts = re.split(r"\s{2,}", line)
                if len(parts) >= 2:
                    urf[parts[0].strip()] = parts[1].strip()
        if urf:
            out["under_relaxation"] = urf

        # Residual limits & time steps
        m_nsteps = re.search(r"Number of Time Steps\s+([0-9]+)", raw)
        if m_nsteps:
            out["planned_time_steps"] = int(m_nsteps.group(1))
        m_iter = re.search(r"Max Iterations/Time Step\s+([0-9]+)", raw)
        if m_iter:
            out["max_iter_per_step"] = int(m_iter.group(1))
    except Exception:
        pass
    return out


_CELL_TYPE_LABELS = {
    1: "Triangle",
    2: "Tetrahedron",
    3: "Quadrilateral",
    4: "Hexahedron",
    5: "Pyramid",
    6: "Wedge / Prism",
    7: "Polyhedron",
}


def inspect_hdf5_mesh_quality(
    cas_path: str,
    rotor_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute vectorized mesh sizing, zone breakdown, BL wall height, y+, and quality stats from `.cas.h5`.

    Designed for sub-second execution (~0.3-1.5s for 150k+ cells) and cached by file mtime.
    """
    if not cas_path or not os.path.isfile(cas_path) or not HAS_H5PY or h5py is None:
        return {"available": False}

    rp = rotor_params or {}
    r_sig = (
        rp.get("U"),
        rp.get("omega"),
        rp.get("R"),
        rp.get("chord"),
        rp.get("rho"),
        rp.get("mu"),
    )
    try:
        mtime_ns = int(os.stat(cas_path).st_mtime_ns)
    except OSError:
        mtime_ns = 0
    cache_key = (os.path.abspath(cas_path), mtime_ns, r_sig)
    if cache_key in _MESH_QUALITY_CACHE:
        return _MESH_QUALITY_CACHE[cache_key]

    result: Dict[str, Any] = {
        "available": False,
        "cas_path": os.path.abspath(cas_path),
    }
    try:
        with h5py.File(cas_path, "r") as _h:
            h: Any = _h
            if "meshes" not in h or len(h["meshes"].keys()) == 0:
                return result
            m = h["meshes"][list(h["meshes"].keys())[0]]

            coords_g = m["nodes"]["coords"]
            coords = coords_g[list(coords_g.keys())[0]][:]
            n_nodes, coord_dim = coords.shape
            dim = 2
            if "cells" in m and "zoneTopology" in m["cells"] and "dimension" in m["cells"]["zoneTopology"]:
                dims_arr = m["cells"]["zoneTopology"]["dimension"][:]
                if len(dims_arr) > 0:
                    dim = int(dims_arr[0])

            # Cell zones
            zt_c = m["cells"]["zoneTopology"]
            c_names_raw = zt_c["name"][0]
            c_names = (
                c_names_raw.decode("utf-8", errors="ignore")
                if isinstance(c_names_raw, (bytes, bytearray))
                else str(c_names_raw)
            ).split(";")
            c_ids = zt_c["id"][:]
            c_min = zt_c["minId"][:].astype(np.int64) - 1
            c_max = zt_c["maxId"][:].astype(np.int64) - 1
            c_ztypes = zt_c["cellType"][:] if "cellType" in zt_c else np.zeros(len(c_names), dtype=np.int32)
            n_cells = int(np.max(c_max) + 1) if len(c_max) > 0 else 0

            # Cell types per cell
            cell_types = np.zeros(n_cells, dtype=np.int16)
            for zmn, zmx, zct in zip(c_min, c_max, c_ztypes):
                if int(zct) != 0 and zmx >= zmn:
                    cell_types[zmn : zmx + 1] = int(zct)
            if "ctype" in m["cells"]:
                mixed_indices = np.concatenate([
                    np.arange(int(zmn), int(zmx) + 1)
                    for zmn, zmx, zct in zip(c_min, c_max, c_ztypes)
                    if int(zct) == 0 and zmx >= zmn
                ]) if np.any(c_ztypes == 0) else np.arange(0)
                offset_m = 0
                for ck in sorted(m["cells"]["ctype"].keys()):
                    cg = m["cells"]["ctype"][ck]
                    for dkey in cg.keys():
                        arr_ct = cg[dkey][:]
                        if len(mixed_indices) > 0 and offset_m + len(arr_ct) <= len(mixed_indices):
                            cell_types[mixed_indices[offset_m : offset_m + len(arr_ct)]] = arr_ct
                            offset_m += len(arr_ct)
                        elif len(arr_ct) <= n_cells:
                            cell_types[: len(arr_ct)] = arr_ct

            u_ct, c_ct = np.unique(cell_types, return_counts=True)
            cell_type_breakdown = []
            for k_t, cnt_t in zip(u_ct.tolist(), c_ct.tolist()):
                lbl = _CELL_TYPE_LABELS.get(int(k_t), f"Type-{k_t}" if k_t != 0 else "Mixed / Unspecified")
                cell_type_breakdown.append({
                    "type": lbl,
                    "count": int(cnt_t),
                    "pct": round(100.0 * cnt_t / max(n_cells, 1), 2),
                })

            # Domain extents
            x_min, x_max = float(np.min(coords[:, 0])), float(np.max(coords[:, 0]))
            y_min, y_max = float(np.min(coords[:, 1])), float(np.max(coords[:, 1]))
            dom_len = x_max - x_min
            dom_wid = y_max - y_min

            # Faces
            c0_raw = m["faces"]["c0"][list(m["faces"]["c0"].keys())[0]][:].astype(np.int64) - 1
            c1_arr = m["faces"]["c1"][list(m["faces"]["c1"].keys())[0]][:].astype(np.int64) - 1
            n_faces = len(c0_raw)
            fn_g = m["faces"]["nodes"][list(m["faces"]["nodes"].keys())[0]]
            nn = fn_g["nnodes"][:]
            nf = fn_g["nodes"][:]

            ft_c = m["faces"]["zoneTopology"]
            f_names_raw = ft_c["name"][0]
            f_names = (
                f_names_raw.decode("utf-8", errors="ignore")
                if isinstance(f_names_raw, (bytes, bytearray))
                else str(f_names_raw)
            ).split(";")
            f_ids = ft_c["id"][:]
            f_min = ft_c["minId"][:].astype(np.int64) - 1
            f_max = ft_c["maxId"][:].astype(np.int64) - 1
            f_ztypes = ft_c["zoneType"][:] if "zoneType" in ft_c else np.zeros(len(f_names), dtype=np.int32)

            offs = np.zeros(len(nn) + 1, dtype=np.int64)
            np.cumsum(nn, out=offs[1:])

            result.update({
                "available": True,
                "dimension": dim,
                "cell_count": n_cells,
                "face_count": n_faces,
                "node_count": int(n_nodes),
                "cell_type_breakdown": cell_type_breakdown,
                "domain": {
                    "x_min_m": round(x_min, 6),
                    "x_max_m": round(x_max, 6),
                    "y_min_m": round(y_min, 6),
                    "y_max_m": round(y_max, 6),
                    "length_m": round(dom_len, 6),
                    "width_m": round(dom_wid, 6),
                },
                "cell_zones": [],
                "face_zones": [],
                "wall_layers": [],
                "quality": {},
                "sizing": {},
            })

            # Face zone summary (works in both 2D & 3D)
            blade_radii: List[float] = []
            blade_chords: List[float] = []
            interface_radii: List[float] = []

            for zid, zname, fmn, fmx, zt_code in zip(f_ids, f_names, f_min, f_max, f_ztypes):
                fc = int(max(0, fmx - fmn + 1))
                fz_entry: Dict[str, Any] = {
                    "id": int(zid),
                    "name": zname,
                    "face_count": fc,
                    "zone_type_code": int(zt_code),
                    "length_m": None,
                }
                if dim == 2 and fc > 0 and fmx < len(nn):
                    f_sl = np.arange(fmn, fmx + 1)
                    valid_edge = nn[f_sl] == 2
                    if np.any(valid_edge):
                        f_sl_v = f_sl[valid_edge]
                        pa = coords[nf[offs[f_sl_v]] - 1, :2]
                        pb = coords[nf[offs[f_sl_v] + 1] - 1, :2]
                        el = np.linalg.norm(pb - pa, axis=1)
                        fz_entry["length_m"] = round(float(np.sum(el)), 6)
                        low_n = zname.lower()
                        if "blade" in low_n or "airfoil" in low_n:
                            pts_u = np.unique(
                                np.concatenate([nf[offs[f_sl_v]] - 1, nf[offs[f_sl_v] + 1] - 1])
                            )
                            bxy = coords[pts_u, :2]
                            if len(bxy) > 1:
                                cxy = np.mean(bxy, axis=0)
                                blade_radii.append(float(np.linalg.norm(cxy)))
                                if len(bxy) <= 2000:
                                    diffs = bxy[:, None, :] - bxy[None, :, :]
                                    blade_chords.append(float(np.max(np.sqrt(np.sum(diffs ** 2, axis=-1)))))
                        elif "interface" in low_n and "contact" in low_n or (
                            "interface" in low_n and "interior" not in low_n
                        ):
                            r_if = float(np.mean(np.linalg.norm(pa, axis=1)))
                            if r_if > 0:
                                interface_radii.append(r_if)
                result["face_zones"].append(fz_entry)

            # 2D full-cell vectorized geometry, sizing, boundary layer & quality
            if dim == 2 and n_cells > 0 and np.any(nn == 2) and coord_dim >= 2:
                xy = coords[:, :2]
                m_valid = (nn == 2) & (c0_raw >= 0) & (c0_raw < n_cells)
                idx_f = np.flatnonzero(m_valid)
                n_a = nf[offs[idx_f]] - 1
                n_b = nf[offs[idx_f] + 1] - 1
                c0 = c0_raw[idx_f]
                c1_pad = np.full(len(nn), -1, dtype=np.int64)
                c1_pad[: len(c1_arr)] = c1_arr
                c1 = c1_pad[idx_f]

                p0 = xy[n_a]
                p1 = xy[n_b]
                elen = np.linalg.norm(p1 - p0, axis=1)

                min_edge = np.full(n_cells, np.inf)
                max_edge = np.zeros(n_cells)
                np.minimum.at(min_edge, c0, elen)
                np.maximum.at(max_edge, c0, elen)

                vc1 = (c1 >= 0) & (c1 < n_cells)
                np.minimum.at(min_edge, c1[vc1], elen[vc1])
                np.maximum.at(max_edge, c1[vc1], elen[vc1])

                # Collect unique vertices per cell and sort CCW around vertex centroid
                cell_ids = np.concatenate([c0, c0, c1[vc1], c1[vc1]])
                node_ids = np.concatenate([n_a, n_b, n_a[vc1], n_b[vc1]])
                keys = (cell_ids.astype(np.uint64) << np.uint64(32)) | node_ids.astype(np.uint64)
                uniq_keys = np.unique(keys)
                u_cells = (uniq_keys >> np.uint64(32)).astype(np.int64)
                u_nodes = (uniq_keys & np.uint64(0xFFFFFFFF)).astype(np.int64)

                cx_v = np.zeros(n_cells)
                cy_v = np.zeros(n_cells)
                vc_tot = np.zeros(n_cells)
                np.add.at(cx_v, u_cells, xy[u_nodes, 0])
                np.add.at(cy_v, u_cells, xy[u_nodes, 1])
                np.add.at(vc_tot, u_cells, 1.0)
                cx_v /= np.maximum(vc_tot, 1.0)
                cy_v /= np.maximum(vc_tot, 1.0)

                ux = xy[u_nodes, 0] - cx_v[u_cells]
                uy = xy[u_nodes, 1] - cy_v[u_cells]
                ang = np.arctan2(uy, ux)
                order = np.lexsort((ang, u_cells))
                s_cells = u_cells[order]
                s_nodes = u_nodes[order]
                _, v_counts = np.unique(s_cells, return_counts=True)
                starts = np.zeros(len(v_counts), dtype=np.int64)
                if len(v_counts) > 1:
                    np.cumsum(v_counts[:-1], out=starts[1:])

                area = np.zeros(n_cells)
                cx = cx_v.copy()
                cy = cy_v.copy()
                skew = np.full(n_cells, np.nan)
                for k_v, theta_eq in [(3, 60.0), (4, 90.0)]:
                    mask_k = v_counts == k_v
                    if not np.any(mask_k):
                        continue
                    st_k = starts[mask_k]
                    c_k = s_cells[st_k]
                    idx_k = st_k[:, None] + np.arange(k_v)[None, :]
                    pts = xy[s_nodes[idx_k]]  # (M, k, 2)
                    pts_next = np.roll(pts, -1, axis=1)
                    sh_cross = pts[:, :, 0] * pts_next[:, :, 1] - pts_next[:, :, 0] * pts[:, :, 1]
                    a2_k = np.sum(sh_cross, axis=1)
                    area[c_k] = 0.5 * np.abs(a2_k)
                    den_k = np.where(np.abs(a2_k) > 1e-30, 3.0 * a2_k, 1.0)
                    cx[c_k] = np.sum((pts[:, :, 0] + pts_next[:, :, 0]) * sh_cross, axis=1) / den_k
                    cy[c_k] = np.sum((pts[:, :, 1] + pts_next[:, :, 1]) * sh_cross, axis=1) / den_k

                    p_prev = np.roll(pts, 1, axis=1)
                    v1 = p_prev - pts
                    v2 = pts_next - pts
                    dot = np.sum(v1 * v2, axis=2)
                    nrm = np.maximum(np.linalg.norm(v1, axis=2) * np.linalg.norm(v2, axis=2), 1e-30)
                    angles = np.degrees(np.arccos(np.clip(dot / nrm, -1.0, 1.0)))
                    th_max = np.max(angles, axis=1)
                    th_min = np.min(angles, axis=1)
                    skew[c_k] = np.maximum(
                        (th_max - theta_eq) / (180.0 - theta_eq),
                        (theta_eq - th_min) / theta_eq,
                    )

                h_eq = np.sqrt(np.maximum(area, 0.0))
                ar = max_edge / np.maximum(min_edge, 1e-18)

                # Orthogonal quality across interior faces
                edge_v = p1 - p0
                f_norm = np.stack([edge_v[:, 1], -edge_v[:, 0]], axis=1)
                n_hat = f_norm / np.maximum(np.linalg.norm(f_norm, axis=1), 1e-30)[:, None]
                d_cc = np.stack([cx[c1[vc1]] - cx[c0[vc1]], cy[c1[vc1]] - cy[c0[vc1]]], axis=1)
                oq_cc = np.abs(np.sum(n_hat[vc1] * d_cc, axis=1)) / np.maximum(np.linalg.norm(d_cc, axis=1), 1e-30)
                cell_oq = np.ones(n_cells)
                np.minimum.at(cell_oq, c0[vc1], oq_cc)
                np.minimum.at(cell_oq, c1[vc1], oq_cc)

                # Growth rate (adjacent cell area ratio) across interior faces
                a0 = np.maximum(area[c0[vc1]], 1e-30)
                a1 = np.maximum(area[c1[vc1]], 1e-30)
                gr = np.maximum(a0 / a1, a1 / a0)

                valid_sk = skew[np.isfinite(skew)]
                tri_mask = cell_types == 1
                quad_mask = cell_types == 3
                core_ar = ar[tri_mask] if np.any(tri_mask) else ar
                wall_ar = ar[quad_mask] if np.any(quad_mask) else None

                result["sizing"] = {
                    "area_total_m2": round(float(np.sum(area)), 6),
                    "cell_area_min_m2": float(np.min(area)),
                    "cell_area_median_m2": float(np.median(area)),
                    "cell_area_mean_m2": float(np.mean(area)),
                    "cell_area_max_m2": float(np.max(area)),
                    "h_eq_min_mm": round(float(np.min(h_eq)) * 1000.0, 5),
                    "h_eq_median_mm": round(float(np.median(h_eq)) * 1000.0, 4),
                    "h_eq_mean_mm": round(float(np.mean(h_eq)) * 1000.0, 4),
                    "h_eq_max_mm": round(float(np.max(h_eq)) * 1000.0, 3),
                    "edge_min_mm": round(float(np.min(min_edge[np.isfinite(min_edge)])) * 1000.0, 5),
                    "edge_median_mm": round(float(np.median(min_edge[np.isfinite(min_edge)])) * 1000.0, 4),
                    "edge_max_mm": round(float(np.max(max_edge)) * 1000.0, 3),
                }

                # Domain / blockage calculations
                r_ref = rp.get("R") or (float(np.mean(blade_radii)) if blade_radii else None)
                c_ref = rp.get("chord") or (float(np.mean(blade_chords)) if blade_chords else None)
                u_ref = rp.get("U")
                om_ref = rp.get("omega")
                rho_ref = rp.get("rho") or 1.225
                mu_ref = rp.get("mu") or 1.7894e-5

                if r_ref and r_ref > 0:
                    d_ref = 2.0 * float(r_ref)
                    result["domain"]["rotor_diameter_m"] = round(d_ref, 5)
                    if dom_wid > 0:
                        result["domain"]["blockage_ratio_pct"] = round(100.0 * d_ref / dom_wid, 2)
                    result["domain"]["upstream_D"] = round(abs(x_min) / d_ref, 2)
                    result["domain"]["downstream_D"] = round(abs(x_max) / d_ref, 2)
                    result["domain"]["width_D"] = round(dom_wid / d_ref, 2)
                if blade_radii:
                    result["domain"]["measured_R_m"] = round(float(np.mean(blade_radii)), 5)
                if blade_chords:
                    result["domain"]["measured_chord_m"] = round(float(np.mean(blade_chords)), 5)
                if interface_radii:
                    result["domain"]["interface_radius_m"] = round(float(np.mean(interface_radii)), 5)

                # Per-cell-zone sizing & quality
                for zid, zname, zmn, zmx in zip(c_ids, c_names, c_min, c_max):
                    if zmx < zmn:
                        continue
                    z_sl = slice(int(zmn), int(zmx) + 1)
                    z_area = area[z_sl]
                    z_h = h_eq[z_sl]
                    z_sk = skew[z_sl]
                    z_oq = cell_oq[z_sl]
                    z_sk_v = z_sk[np.isfinite(z_sk)]
                    result["cell_zones"].append({
                        "id": int(zid),
                        "name": zname,
                        "cell_count": int(zmx - zmn + 1),
                        "area_m2": round(float(np.sum(z_area)), 6),
                        "h_min_mm": round(float(np.min(z_h)) * 1000.0, 5),
                        "h_median_mm": round(float(np.median(z_h)) * 1000.0, 4),
                        "h_max_mm": round(float(np.max(z_h)) * 1000.0, 3),
                        "skew_max": round(float(np.max(z_sk_v)), 4) if len(z_sk_v) else None,
                        "oq_min": round(float(np.min(z_oq)), 4),
                    })

                # Quality summary
                q_dict: Dict[str, Any] = {
                    "skewness_min": round(float(np.min(valid_sk)), 4) if len(valid_sk) else None,
                    "skewness_median": round(float(np.median(valid_sk)), 4) if len(valid_sk) else None,
                    "skewness_mean": round(float(np.mean(valid_sk)), 4) if len(valid_sk) else None,
                    "skewness_p95": round(float(np.percentile(valid_sk, 95)), 4) if len(valid_sk) else None,
                    "skewness_max": round(float(np.max(valid_sk)), 4) if len(valid_sk) else None,
                    "skew_excellent_lt_025_pct": round(100.0 * float(np.mean(valid_sk < 0.25)), 2) if len(valid_sk) else None,
                    "skew_good_025_050_pct": round(100.0 * float(np.mean((valid_sk >= 0.25) & (valid_sk < 0.50))), 2) if len(valid_sk) else None,
                    "skew_fair_050_080_pct": round(100.0 * float(np.mean((valid_sk >= 0.50) & (valid_sk <= 0.80))), 2) if len(valid_sk) else None,
                    "skew_poor_gt_080_pct": round(100.0 * float(np.mean(valid_sk > 0.80)), 2) if len(valid_sk) else None,
                    "ortho_min": round(float(np.min(cell_oq)), 4),
                    "ortho_p5": round(float(np.percentile(cell_oq, 5)), 4),
                    "ortho_median": round(float(np.median(cell_oq)), 4),
                    "ortho_mean": round(float(np.mean(cell_oq)), 4),
                    "ortho_low_lt_015_pct": round(100.0 * float(np.mean(cell_oq < 0.15)), 2),
                    "ortho_high_gt_070_pct": round(100.0 * float(np.mean(cell_oq > 0.70)), 2),
                    "ar_min": round(float(np.min(ar)), 2),
                    "ar_median": round(float(np.median(ar)), 2),
                    "ar_mean": round(float(np.mean(ar)), 2),
                    "ar_p95": round(float(np.percentile(ar, 95)), 2),
                    "ar_max": round(float(np.max(ar)), 2),
                    "core_ar_mean": round(float(np.mean(core_ar)), 2),
                    "core_ar_p95": round(float(np.percentile(core_ar, 95)), 2),
                    "core_ar_max": round(float(np.max(core_ar)), 2),
                    "wall_bl_ar_mean": round(float(np.mean(wall_ar)), 2) if wall_ar is not None else None,
                    "wall_bl_ar_max": round(float(np.max(wall_ar)), 2) if wall_ar is not None else None,
                    "growth_rate_mean": round(float(np.mean(gr)), 3),
                    "growth_rate_p95": round(float(np.percentile(gr, 95)), 3),
                    "growth_rate_max": round(float(np.max(gr)), 3),
                    "growth_rate_gt_2_pct": round(100.0 * float(np.mean(gr > 2.0)), 2),
                }
                result["quality"] = q_dict

                # Wall / Boundary Layer first-layer thickness y1 and estimated y+
                for zname, fmn, fmx in zip(f_names, f_min, f_max):
                    low_w = zname.lower()
                    if not ("blade" in low_w or "airfoil" in low_w or "shaft" in low_w or "tower" in low_w):
                        continue
                    if fmx < fmn or fmx >= len(nn):
                        continue
                    f_sl = np.arange(fmn, fmx + 1)
                    valid_w = (nn[f_sl] == 2) & (c0_raw[f_sl] >= 0) & (c0_raw[f_sl] < n_cells)
                    if not np.any(valid_w):
                        continue
                    f_sl = f_sl[valid_w]
                    w_cells = c0_raw[f_sl]
                    wn_offs = offs[f_sl]
                    wa = xy[nf[wn_offs] - 1]
                    wb = xy[nf[wn_offs + 1] - 1]
                    ds = np.linalg.norm(wb - wa, axis=1)
                    ev = (wb - wa) / np.maximum(ds, 1e-30)[:, None]
                    wc_xy = np.stack([cx[w_cells], cy[w_cells]], axis=1)
                    # For quad/triangle boundary cell, perpendicular centroid offset gives normal height
                    y_cent = np.abs((wc_xy[:, 0] - wa[:, 0]) * (-ev[:, 1]) + (wc_xy[:, 1] - wa[:, 1]) * ev[:, 0])
                    is_tri_w = cell_types[w_cells] == 1
                    y1_arr = np.where(is_tri_w, 3.0 * y_cent, 2.0 * y_cent)

                    yplus_mean = None
                    yplus_peak = None
                    if u_ref and om_ref and r_ref and c_ref and u_ref > 0 and c_ref > 0:
                        v_nom = float(np.hypot(float(u_ref), abs(float(om_ref)) * float(r_ref)))
                        v_peak = float(u_ref) + abs(float(om_ref)) * float(r_ref)
                        re_nom = max(1000.0, float(rho_ref) * v_nom * float(c_ref) / float(mu_ref))
                        re_peak = max(1000.0, float(rho_ref) * v_peak * float(c_ref) / float(mu_ref))
                        u_tau_nom = v_nom * np.sqrt(0.058 * (re_nom ** -0.2) / 2.0)
                        u_tau_peak = v_peak * np.sqrt(0.058 * (re_peak ** -0.2) / 2.0)
                        yplus_mean = round(float(rho_ref) * u_tau_nom * float(np.mean(y1_arr)) / float(mu_ref), 3)
                        yplus_peak = round(float(rho_ref) * u_tau_peak * float(np.mean(y1_arr)) / float(mu_ref), 3)

                    result["wall_layers"].append({
                        "zone": zname,
                        "face_count": int(len(f_sl)),
                        "perimeter_mm": round(float(np.sum(ds)) * 1000.0, 3),
                        "ds_min_mm": round(float(np.min(ds)) * 1000.0, 4),
                        "ds_mean_mm": round(float(np.mean(ds)) * 1000.0, 4),
                        "ds_max_mm": round(float(np.max(ds)) * 1000.0, 4),
                        "y1_min_mm": round(float(np.min(y1_arr)) * 1000.0, 5),
                        "y1_mean_mm": round(float(np.mean(y1_arr)) * 1000.0, 5),
                        "y1_max_mm": round(float(np.max(y1_arr)) * 1000.0, 5),
                        "yplus_nom_est": yplus_mean,
                        "yplus_peak_est": yplus_peak,
                    })

        _MESH_QUALITY_CACHE[cache_key] = result
    except Exception as exc:
        result["error"] = str(exc)

    return result


def inspect_workbench_setup_and_mesh(
    path_or_folder: str,
    rotor_params: Optional[Dict[str, Any]] = None,
    compute_cell_quality: bool = True,
) -> Dict[str, Any]:
    """Unified Workbench `dp0/FFF-{number}` inspector: system artifacts + setup + mesh quality + notes."""
    t0 = time.perf_counter()
    ctx = resolve_workbench_system_context(path_or_folder)

    # Pull case inspection (fast topology/settings) & detailed report.xml setup
    case_res = None
    cas_for_mesh = ctx.get("case_h5") or ctx.get("setup_case_h5")
    if cas_for_mesh and os.path.isfile(cas_for_mesh):
        try:
            from vawt_post.engine.hdf5_case import inspect_hdf5_case
            case_res = inspect_hdf5_case(cas_for_mesh)
        except Exception:
            case_res = None

    xml_setup = parse_detailed_setup_xml(ctx["report_xml"]) if ctx.get("report_xml") else {}
    msh_summary = parse_msh_file_summary(ctx["msh_file"]) if ctx.get("msh_file") else {}

    rp = dict(rotor_params or {})
    if case_res and case_res.is_valid:
        rp.setdefault("U", case_res.velocity_m_s)
        rp.setdefault("omega", abs(case_res.omega_rad_s) if case_res.omega_rad_s is not None else None)
        rp.setdefault("R", case_res.rotor_radius_m)
        rp.setdefault("chord", case_res.chord_m)
        rp.setdefault("rho", case_res.density_kg_m3)
        rp.setdefault("mu", case_res.viscosity_pa_s)
    if xml_setup:
        if xml_setup.get("velocity") is not None and not rp.get("U"):
            rp["U"] = xml_setup["velocity"]
        if xml_setup.get("omega") is not None and not rp.get("omega"):
            rp["omega"] = abs(float(xml_setup["omega"]))
        if xml_setup.get("density") is not None and not rp.get("rho"):
            rp["rho"] = xml_setup["density"]
        if xml_setup.get("viscosity") is not None and not rp.get("mu"):
            rp["mu"] = xml_setup["viscosity"]

    mesh_quality: Dict[str, Any] = {"available": False}
    if compute_cell_quality and cas_for_mesh and os.path.isfile(cas_for_mesh):
        mesh_quality = inspect_hdf5_mesh_quality(cas_for_mesh, rp)
    elif msh_summary.get("cells"):
        mesh_quality = {
            "available": True,
            "source": os.path.basename(ctx["msh_file"]),
            "dimension": msh_summary.get("dimension") or 2,
            "cell_count": msh_summary.get("cells"),
            "face_count": msh_summary.get("faces"),
            "node_count": msh_summary.get("nodes"),
            "cell_type_breakdown": [],
            "domain": {},
            "cell_zones": [],
            "face_zones": [
                {"id": z["id"], "name": z["name"], "zone_type_code": z["type"], "face_count": None, "length_m": None}
                for z in msh_summary.get("zones", [])
            ],
            "wall_layers": [],
            "quality": {},
            "sizing": {},
        }

    # Build engineering notes summary
    notes: List[str] = []
    if ctx.get("is_workbench_system") and ctx.get("system_id"):
        notes.append(f"Workbench system resolved: {ctx['system_id']} ({ctx.get('system_dir')})")

    art_labels = []
    for k, lbl in [
        ("case_h5", "Fluent Case (.cas.h5)"),
        ("dat_h5", "Fluent Data (.dat.h5)"),
        ("msh_file", "ANSYS Mesh (.msh)"),
        ("mshdb_file", "Meshing DB (.mshdb)"),
        ("agdb_file", "CAD Geometry"),
        ("report_xml", "Setup XML"),
        ("set_file", "Scheme Settings (.set)"),
    ]:
        if ctx.get(k):
            art_labels.append(f"{lbl}: {os.path.basename(ctx[k])}")
    if art_labels:
        notes.append("Artifacts found: " + " | ".join(art_labels))

    if mesh_quality.get("available"):
        cc = mesh_quality.get("cell_count", 0)
        nc = mesh_quality.get("node_count", 0)
        fc = mesh_quality.get("face_count", 0)
        dim = mesh_quality.get("dimension", 2)
        notes.append(f"Mesh topology ({dim}D): {cc:,} cells, {fc:,} faces, {nc:,} nodes")

        dom = mesh_quality.get("domain", {})
        if dom.get("length_m") and dom.get("width_m"):
            blk = f", blockage {dom['blockage_ratio_pct']:.1f}%" if dom.get("blockage_ratio_pct") is not None else ""
            notes.append(f"Domain extent: {dom['length_m']:.2f} m × {dom['width_m']:.2f} m{blk}")

        q = mesh_quality.get("quality", {})
        if q:
            sk_max = q.get("skewness_max")
            sk_mean = q.get("skewness_mean")
            oq_min = q.get("ortho_min")
            ar_max = q.get("ar_max")
            gr_p95 = q.get("growth_rate_p95")
            q_parts = []
            if sk_max is not None:
                q_parts.append(f"Skewness mean/max = {sk_mean:.3f}/{sk_max:.3f}")
            if oq_min is not None:
                q_parts.append(f"Ortho Quality min = {oq_min:.3f}")
            if ar_max is not None:
                q_parts.append(f"AR max = {ar_max:.1f}")
            if gr_p95 is not None:
                q_parts.append(f"Growth P95 = {gr_p95:.2f}")
            if q_parts:
                notes.append("Mesh quality: " + ", ".join(q_parts))

        wl = [w for w in mesh_quality.get("wall_layers", []) if "blade" in w["zone"].lower()]
        if wl:
            y1_vals = [w["y1_mean_mm"] for w in wl if w.get("y1_mean_mm") is not None]
            yp_vals = [w["yplus_peak_est"] for w in wl if w.get("yplus_peak_est") is not None]
            if y1_vals:
                y1_avg_um = float(np.mean(y1_vals)) * 1000.0
                yp_txt = f", est. peak y+ ≈ {max(yp_vals):.2f}" if yp_vals else ""
                if yp_vals:
                    yp_m = max(yp_vals)
                    if yp_m <= 1.0:
                        yp_txt += " (viscous sublayer resolved, y+ ≤ 1)"
                    elif yp_m <= 5.0:
                        yp_txt += " (buffer-adjacent, check y+ ≤ 1 target)"
                    else:
                        yp_txt += " (wall-function / coarse BL)"
                notes.append(f"Blade boundary layer: y1 mean = {y1_avg_um:.2f} µm ({float(np.mean(y1_vals)):.5f} mm){yp_txt}")
    elif case_res and getattr(case_res, "is_valid", False) and getattr(case_res, "cell_count", None):
        cc = int(case_res.cell_count or 0)
        nc = int(getattr(case_res, "node_count", None) or 0)
        dim = int(getattr(case_res, "dimension", None) or 2)
        notes.append(f"Mesh topology ({dim}D): {cc:,} cells, {nc:,} nodes (detailed quality runs on full analysis)")

    setup_bits = []
    if xml_setup.get("fluent_release"):
        setup_bits.append(f"Fluent {xml_setup['fluent_release']}")
    tm = xml_setup.get("turb_model") or (case_res.turb_model if case_res else None)
    if tm:
        setup_bits.append(str(tm))
    tform = xml_setup.get("time_formulation") or (case_res.time_model if case_res else None)
    if tform:
        setup_bits.append(str(tform))
    if xml_setup.get("pv_coupling"):
        setup_bits.append(f"P-V: {xml_setup['pv_coupling']}")
    if xml_setup.get("max_iter_per_step"):
        setup_bits.append(f"{xml_setup['max_iter_per_step']} iter/step")
    if setup_bits:
        notes.append("Solver setup: " + " | ".join(setup_bits))

    return {
        "context": ctx,
        "case": case_res.to_dict() if case_res and case_res.is_valid else {},
        "setup": xml_setup,
        "msh_summary": msh_summary,
        "mesh": mesh_quality,
        "notes": notes,
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }
