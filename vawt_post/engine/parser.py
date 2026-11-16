# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Fluent Report File Parser & Aligner
============================================================
Handles robust reading, column mapping, step alignment, deduplication,
and file auto-detection for ANSYS Fluent monitor output files.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import os
import re
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

from vawt_post.engine.config import InputFiles


def compute_file_sha256(path: str) -> str:
    """Compute SHA-256 cryptographic checksum of a file."""
    if not path or not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def inspect_fluent_report(path: str) -> Dict[str, Any]:
    """Inspect an ANSYS Fluent monitor file to detect column headers and data columns.

    Returns dict with keys:
        'path': filepath
        'columns': list of all detected column headers
        'data_columns': list of (index, header_name) excluding step and flow-time
        'step_col': index of time step column
        'time_col': index of flow-time column or None
        'is_multi_column': bool (True if > 1 data columns)
        'row_count': estimated number of numeric rows
        'sha256': file SHA-256 checksum
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    names: Optional[List[str]] = None
    row_count = 0
    sample_widths: List[int] = []

    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for idx, line in enumerate(fh):
            s = line.strip()
            if not s:
                continue
            toks = s.replace("(", " ").replace(")", " ").replace(",", " ").split()
            try:
                nums = [float(x) for x in toks]
                if len(nums) >= 2:
                    row_count += 1
                    if len(sample_widths) < 10:
                        sample_widths.append(len(nums))
                    continue
            except ValueError:
                pass

            # Before data rows begin, update names to the last seen header line
            if row_count == 0:
                q = re.findall(r'"([^"]+)"', s)
                if len(q) >= 2 and not re.match(r"^[-+0-9.eE\s]+$", s):
                    names = q
                elif ("(" in line or line.startswith("#") or line.lower().startswith("time") or line.lower().startswith("step")) and len(toks) >= 2 and all(not re.match(r"^[-+0-9.eE]+$", t) for t in toks):
                    names = toks

    width = min(sample_widths) if sample_widths else 2
    if names is None:
        names = [f"Col_{i + 1}" for i in range(width)]

    i_step = 0
    i_time = None
    low = [n.lower() for n in names]
    for i, n in enumerate(low):
        if "flow-time" in n or n == "time" or "flow time" in n or "flow_time" in n:
            i_time = i
        elif "step" in n or "iteration" in n or "time step" in n:
            i_step = i

    data_cols = [(i, names[i]) for i in range(len(names)) if i not in (i_step, i_time)]

    return {
        "path": path,
        "name": os.path.basename(path),
        "columns": names,
        "data_columns": data_cols,
        "step_col": i_step,
        "time_col": i_time,
        "is_multi_column": len(data_cols) > 1,
        "row_count": row_count,
        "sha256": compute_file_sha256(path),
    }


def read_fluent_report(
    path: str,
    column_name: Optional[str] = None,
    column_idx: Optional[int] = None,
    decimate: int = 1
) -> Dict[str, Any]:
    """Parse an ANSYS Fluent monitor report file (.out, .txt, .dat, .csv).

    Reads headers such as `("Time Step" "monitor-name" "flow-time")` and numeric rows.
    Handles 3-column files (step, value, time), 2-column files (step, value),
    and multi-column monitor files with report column selection and decimation.

    Args:
        path: Path to report file
        column_name: Specific report column name to extract (if multi-column)
        column_idx: Specific column index to extract (if multi-column)
        decimate: Factor by which to decimate rows (default 1 = all rows)

    Returns:
        dict with keys:
            'step': np.ndarray of int64
            'value': np.ndarray of float
            'time': np.ndarray of float or None
            'name': filename
            'columns': list of all detected column headers
            'selected_column': name of selected column
            'data_columns': list of data column names
            'is_multi_column': bool
            'sha256': file SHA-256 hash
            'raw_row_count': total rows before decimation
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    names: Optional[List[str]] = None
    rows: List[List[float]] = []

    content_lines: List[str] = []
    for attempt in range(4):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                content_lines = fh.readlines()
            break
        except (PermissionError, OSError):
            if attempt == 3:
                try:
                    with open(path, "rb") as fh:
                        content_lines = fh.read().decode("utf-8", errors="ignore").splitlines()
                    break
                except Exception:
                    pass
            import time
            time.sleep(0.04)

    for line in content_lines:
        s = line.strip()
        if not s:
            continue

        toks = s.replace("(", " ").replace(")", " ").replace(",", " ").split()
        try:
            nums = [float(x) for x in toks]
            if len(nums) >= 2:
                rows.append(nums)
                continue
        except ValueError:
            pass

        # Before data rows begin, update names to the last seen header line
        if len(rows) == 0:
            q = re.findall(r'"([^"]+)"', s)
            if len(q) >= 2 and not re.match(r"^[-+0-9.eE\s]+$", s):
                names = q
            elif ("(" in line or line.startswith("#") or line.lower().startswith("time") or line.lower().startswith("step")) and len(toks) >= 2 and all(not re.match(r"^[-+0-9.eE]+$", t) for t in toks):
                names = toks

    if not rows:
        raise ValueError(f"No numeric rows found in '{os.path.basename(path)}'. Verify that this is a valid Fluent report file.")

    raw_row_count = len(rows)
    # Use the dominant (modal) column width so partial trailing writes or short lines
    # do not truncate the entire dataset down to fewer columns.
    width_counts = Counter(len(r) for r in rows)
    width = width_counts.most_common(1)[0][0]
    filtered_rows = [r[:width] for r in rows if len(r) >= width]
    if not filtered_rows:
        width = min(len(r) for r in rows)
        filtered_rows = [r[:width] for r in rows]
    arr = np.array(filtered_rows, dtype=float)

    # Do not silently overwrite NaN/Inf here so file diagnostics can detect them.

    if decimate > 1:
        arr = arr[::decimate]

    if names is None or len(names) != width:
        names = [f"Col_{i + 1}" for i in range(width)]

    i_step_found: Optional[int] = None
    i_time = None

    low = [n.lower() for n in names]
    for i, n in enumerate(low):
        if i_time is None and ("flow-time" in n or n == "time" or "flow time" in n or "flow_time" in n):
            i_time = i
        elif i_step_found is None and ("step" in n or "iteration" in n or "time step" in n):
            i_step_found = i
    i_step = i_step_found if i_step_found is not None else 0

    available_data_cols = [i for i in range(width) if i not in (i_step, i_time)]
    data_col_names = [names[i] for i in available_data_cols]
    is_multi_column = len(available_data_cols) > 1

    # Select specific column if requested
    i_val: int = available_data_cols[0] if available_data_cols else 0
    if column_name is not None:
        matched = False
        target = column_name.lower().strip()
        for i, n in enumerate(names):
            if n.lower().strip() == target or target in n.lower():
                i_val = i
                matched = True
                break
        if not matched:
            raise ValueError(f"Requested column '{column_name}' not found in file '{os.path.basename(path)}'. Available columns: {names}")
    elif column_idx is not None:
        if 0 <= column_idx < width:
            i_val = column_idx
        else:
            raise IndexError(f"Column index {column_idx} out of range (width={width}) in '{os.path.basename(path)}'.")
    elif available_data_cols:
        i_val = available_data_cols[0]
    else:
        raise ValueError(
            f"File '{os.path.basename(path)}' lacks a monitor value column: only 'Time Step' and 'flow-time' "
            "are present (or no report definition data column exists). In ANSYS Fluent, add the report definition "
            "to this report file (Report Definitions -> Report Files -> Edit) and rerun a short test."
        )

    if i_time is not None and i_val == i_time:
        raise ValueError(
            f"File '{os.path.basename(path)}' selected column '{names[i_val]}' is flow-time, not a physical moment/force value. "
            "In ANSYS Fluent, add the report definition to this report file and rerun."
        )

    selected_col_name = names[i_val] if i_val < len(names) else f"Col_{i_val + 1}"
    sha256_hash = compute_file_sha256(path)

    step_col = arr[:, i_step]
    if not np.all(np.isfinite(step_col)):
        fallback_steps = np.arange(len(step_col), dtype=float)
        step_col = np.where(np.isfinite(step_col), step_col, fallback_steps)

    return {
        "step": np.rint(step_col).astype(np.int64),
        "value": arr[:, i_val],
        "time": arr[:, i_time] if i_time is not None else None,
        "name": os.path.basename(path),
        "columns": names,
        "selected_column": selected_col_name,
        "data_columns": data_col_names,
        "is_multi_column": is_multi_column,
        "sha256": sha256_hash,
        "raw_row_count": raw_row_count,
    }


def align_series(
    series: Dict[str, Dict[str, Any]],
    dt_fallback: Optional[float] = None
) -> Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray]]:
    """Inner-join all series on step number.

    Deduplicates repeated step numbers by keeping the last occurrence.
    If flow-time is absent, falls back to step * dt_fallback.

    Returns:
        (steps, flow_time, {series_name: values})
    """
    if not series:
        raise ValueError("No series provided to align.")

    common_steps = None
    for s in series.values():
        steps_set = set(s["step"].tolist())
        common_steps = steps_set if common_steps is None else common_steps & steps_set

    if not common_steps:
        raise ValueError("The selected files share no common time steps. Check that they come from the same simulation run.")

    common = np.array(sorted(common_steps), dtype=np.int64)
    out: Dict[str, np.ndarray] = {}
    time: Optional[np.ndarray] = None

    for name, s in series.items():
        # Keep the last occurrence of any repeated step
        _, idx = np.unique(s["step"][::-1], return_index=True)
        idx = len(s["step"]) - 1 - idx
        order = np.argsort(s["step"][idx])
        steps_u = s["step"][idx][order]
        vals_u = s["value"][idx][order]

        pos = np.searchsorted(steps_u, common)
        out[name] = vals_u[pos]

        if time is None and s["time"] is not None:
            tt = s["time"][idx][order]
            time = tt[pos]

    if time is None:
        if dt_fallback is None or dt_fallback <= 0:
            raise ValueError(
                "The files lack a flow-time column and no valid time step size (dt) was entered. "
                "Please enter the time step (s) in the Analysis parameters."
            )
        time = common * float(dt_fallback)

    return common, np.asarray(time, dtype=float), out


def parse_report_filename_role(filename: str, n_blades: int = 8) -> Tuple[str, Optional[int], List[str]]:
    """Parse role of monitor file from filename with strict tokenization (Bug L3 fix).

    Returns:
        (role, blade_index_1_based_or_None, warnings)
        where role in ('total_moment', 'blade_moment', 'shaft_moment', 'fx', 'fy', 'blade_fx', 'blade_fy', 'coefficient', 'unknown')
    """
    low = filename.lower().strip()
    stem = os.path.splitext(low)[0]
    warnings: List[str] = []

    # Check for coefficient notation
    is_coeff = any(tok in stem for tok in ("-cd", "_cd", ".cd", "cd-", "drag-coeff", "lift-coeff", "-cl", "_cl", ".cl", "cl-", "coeff"))
    if is_coeff:
        warnings.append(f"File '{filename}' appears to be a dimensionless coefficient (Cd/Cl/Cm) rather than dimensional force/moment.")

    # Shaft force detection (Fx, Fy on shaft e.g. force_shaftx-rfile.out, force_shafty.out)
    # Use stem (extension stripped) so '.txt' never triggers an 'x' match.
    if "shaft" in stem:
        if any(fx_tok in stem for fx_tok in ("forcex", "force-x", "force_x", "fx", "thrust", "shaftx", "shaft-x", "shaft_x")) or (
            "force" in stem and (bool(re.search(r"(?:force|shaft)[_-]?x|[_-]x(?:[_-]|$)", stem)) or "drag" in stem)
        ):
            return "shaft_fx", None, warnings
        if any(fy_tok in stem for fy_tok in ("forcey", "force-y", "force_y", "fy", "side", "shafty", "shaft-y", "shaft_y")) or (
            "force" in stem and (bool(re.search(r"(?:force|shaft)[_-]?y|[_-]y(?:[_-]|$)", stem)) or "lift" in stem)
        ):
            return "shaft_fy", None, warnings
        if "force" in stem and not any(m in stem for m in ("moment", "torque", "cm")):
            return "unknown", None, warnings

    # Shaft moment / torque detection:
    # Usually named moment_shaft (e.g. moment_shaft-rfile.out, moment_shaft.out),
    # shaft_moment, shaft-moment, moment-shaft, shaft_torque, torque_shaft, cm_shaft.
    # Must explicitly contain both "shaft" and ("moment", "torque", or "cm").
    # Must NEVER match force report files.
    if "shaft" in stem and any(k in stem for k in ("moment", "torque", "cm")) and not any(f in stem for f in ("force", "fx", "fy", "drag", "lift")):
        return "shaft_moment", None, warnings

    # Blade number detection: strict regex matching digit attached to blade, bl, moment, torque, fx, fy, forcex, forcey
    # Examples: moment1, moment-1, moment_1, blade1, bl2, torque3, fx_1, fy-2, forcex1, blade_2_torque
    m_blade_num = re.search(r"(?:blade|bl|moment|torque|fx|fy|force[_-]?[xy])[_-]?([1-8])(?![0-9])", stem)
    # Also check if filename has single digit after - or _ before extension or -rfile
    if not m_blade_num:
        m_blade_num = re.search(r"[_-]([1-8])(?:[_-]rfile|$)", stem)

    blade_idx = int(m_blade_num.group(1)) if m_blade_num else None

    # Forces (Fx, Fy)
    if ("forcex" in stem or "force-x" in stem or "force_x" in stem or "fx" in stem or "drag" in stem):
        if blade_idx is not None and 1 <= blade_idx <= n_blades:
            return "blade_fx", blade_idx, warnings
        return "fx", None, warnings

    if ("forcey" in stem or "force-y" in stem or "force_y" in stem or "fy" in stem or "lift" in stem):
        if blade_idx is not None and 1 <= blade_idx <= n_blades:
            return "blade_fy", blade_idx, warnings
        return "fy", None, warnings

    # Moments: distinguish blade moments from total rotor moment
    if blade_idx is not None and 1 <= blade_idx <= n_blades:
        # File has an explicit blade number 1..N -> strictly a blade moment!
        # MUST NEVER be total moment even if it contains "rfile" or "moment"
        return "blade_moment", blade_idx, warnings

    # Check for total moment tokens:
    # "total", "rotor", "moment_all", or exact total files like "moment-rfile.out", "moment.out", "torque.out"
    if any(k in stem for k in ("total", "rotor", "all_blades", "moment_all", "moment-all")):
        if any(m in stem for m in ("moment", "torque", "cm", "rfile")):
            return "total_moment", None, warnings

    # Exact known generic names (without any blade digit)
    if stem in ("moment-rfile", "moment", "torque-rfile", "torque", "total-moment", "rotor-moment"):
        return "total_moment", None, warnings

    if "moment" in stem or "torque" in stem:
        return "total_moment", None, warnings

    if is_coeff:
        return "coefficient", None, warnings

    return "unknown", None, warnings


def resolve_fluent_directory(path_or_folder: str) -> str:
    """Resolve a user-selected path or directory to the active ANSYS Fluent simulation folder.

    Handles:
      1. Direct Fluent run folders containing monitor files (*.out) or case files (*.cas.h5).
      2. ANSYS Workbench projects (.wbpj or directory containing .wbpj or *_files/dp0).
         Automatically selects the active, actively writing, or latest system with monitor files.
      3. Arbitrary parent directories with nested simulation runs (searched up to 4 levels).
    """
    if not path_or_folder:
        return path_or_folder

    folder = os.path.abspath(path_or_folder)
    if os.path.isfile(folder):
        if folder.lower().endswith(".wbpj"):
            folder = os.path.dirname(folder)
        else:
            return os.path.dirname(folder)

    if not os.path.isdir(folder):
        return path_or_folder

    # 1. Direct check: does this folder already have monitor files?
    try:
        entries = os.listdir(folder)
        has_monitors = any(
            f.lower().endswith((".out", ".dat", ".csv", ".txt")) and
            not f.lower().endswith((".trn", ".log", ".jou", ".set", ".cas", ".msh")) and
            any(k in f.lower() for k in ("moment", "force", "torque", "cd", "cl", "cm"))
            for f in entries
        )
        if has_monitors:
            return folder
    except Exception:
        pass

    # 1b. Workbench system directory (e.g. dp0/FFF or dp0/FFF-2, even before .out files exist)
    try:
        fl_sub = os.path.join(folder, "Fluent")
        parent_name = os.path.basename(os.path.dirname(folder)).lower()
        base_name = os.path.basename(folder).lower()
        if os.path.isdir(fl_sub):
            fl_entries = os.listdir(fl_sub)
            has_fluent_artifacts = any(
                e.lower().endswith((".out", ".cas.h5", ".cas", ".dat.h5", ".dat", ".set", ".ip", ".trn"))
                for e in fl_entries
            )
            if (
                has_fluent_artifacts
                or parent_name == "dp0"
                or os.path.isdir(os.path.join(folder, "MECH"))
                or os.path.isdir(os.path.join(folder, "DM"))
            ):
                return fl_sub
        elif base_name in ("mech", "dm"):
            sib_fl = os.path.join(os.path.dirname(folder), "Fluent")
            if os.path.isdir(sib_fl):
                return sib_fl
    except Exception:
        pass

    # 2. Check for ANSYS Workbench project
    try:
        from vawt_post.engine.workbench import scan_workbench_project
        wb = scan_workbench_project(folder)
        if wb and wb.systems:
            candidates = []
            for s in wb.systems.values():
                if not s.fluent_dir or not os.path.isdir(s.fluent_dir):
                    continue
                has_mon = bool(s.total_moment_file or s.blade_moment_files or s.fx_file or s.fy_file)
                has_case = bool(s.case_path)
                score_mon = 2 if has_mon else (1 if has_case else 0)
                candidates.append((s, score_mon, s.is_actively_writing, s.latest_mtime))
            if candidates:
                candidates.sort(key=lambda item: (item[1], item[2], item[3]), reverse=True)
                return candidates[0][0].fluent_dir
    except Exception:
        pass

    # 3. Recursive directory walk (up to 4 levels deep)
    best_dir = None
    best_score = (-1, -1.0)
    base_depth = len(folder.split(os.sep))
    try:
        for root, dirs, files in os.walk(folder):
            cur_depth = len(os.path.abspath(root).split(os.sep)) - base_depth
            if cur_depth > 4:
                dirs[:] = []
                continue
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and "backup" not in d.lower() and "temp" not in d.lower()
            ]
            monitors = [
                f for f in files
                if f.lower().endswith((".out", ".dat")) and
                any(k in f.lower() for k in ("moment", "force", "torque", "cd", "cl", "cm"))
            ]
            if monitors:
                mtimes = [os.path.getmtime(os.path.join(root, f)) for f in monitors]
                latest_m = max(mtimes) if mtimes else 0.0
                score = (len(monitors), latest_m)
                if score > best_score:
                    best_score = score
                    best_dir = root
    except Exception:
        pass

    return best_dir or folder


def list_report_candidates(folder: str, n_blades: int = 8) -> List[Dict[str, Any]]:
    """List all candidate report files in directory with metadata, sizes, mtimes, and detected roles (L3)."""
    candidates: List[Dict[str, Any]] = []
    if not folder:
        return candidates
    folder = resolve_fluent_directory(folder)
    if not os.path.isdir(folder):
        return candidates

    try:
        entries = sorted(os.listdir(folder))
    except Exception:
        return candidates

    for f in entries:
        if not f.lower().endswith((".out", ".txt", ".dat", ".csv")):
            continue
        full_path = os.path.join(folder, f)
        if not os.path.isfile(full_path):
            continue

        try:
            stat = os.stat(full_path)
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = 0
            mtime = 0.0

        role, b_idx, warns = parse_report_filename_role(f, n_blades)

        # Quick tail to find last step
        last_step = None
        try:
            with open(full_path, "rb") as fh:
                fh.seek(max(0, size - 2048))
                tail_lines = fh.read().decode("utf-8", errors="ignore").splitlines()
                for line in reversed(tail_lines):
                    toks = line.replace("(", " ").replace(")", " ").replace(",", " ").split()
                    try:
                        nums = [float(x) for x in toks]
                        if len(nums) >= 2:
                            last_step = int(nums[0])
                            break
                    except ValueError:
                        pass
        except Exception:
            pass

        candidates.append({
            "path": full_path,
            "name": f,
            "size": size,
            "mtime": mtime,
            "role": role,
            "blade_index": b_idx,
            "last_step": last_step,
            "warnings": warns,
        })

    return candidates


def autodetect(folder: str, n_blades: int) -> InputFiles:
    """Auto-detect Fluent report files within a directory using strict tokenization (Bug L3 fix)."""
    f = InputFiles(blades=[None] * n_blades, fx_blades=[None] * n_blades, fy_blades=[None] * n_blades)
    if not folder:
        return f
    folder = resolve_fluent_directory(folder)
    if not os.path.isdir(folder):
        return f

    candidates = list_report_candidates(folder, n_blades)

    # Sort candidates by role confidence score and modification time descending
    def _rank_cand(cand: Dict[str, Any]) -> Tuple[int, float]:
        name = cand["name"].lower()
        role = cand["role"]
        score = 0
        if role == "shaft_moment":
            # Highest priority for moment_shaft (exact convention user expects)
            if "moment_shaft" in name or "moment-shaft" in name:
                score = 300
            elif "shaft_moment" in name or "shaft-moment" in name:
                score = 250
            elif "shaft" in name and ("torque" in name or "cm" in name):
                score = 200
            else:
                score = 100
        elif role == "total_moment":
            if any(k in name for k in ("moment_all", "moment-all", "all_blades")):
                score = 300
            elif name.startswith("moment-rfile") or name.startswith("moment."):
                score = 250
            elif "total" in name:
                score = 200
            else:
                score = 100
        elif role in ("fx", "fy", "shaft_fx", "shaft_fy"):
            if "rfile" in name:
                score = 200
            else:
                score = 100
        elif role in ("blade_moment", "blade_fx", "blade_fy"):
            if "rfile" in name:
                score = 200
            else:
                score = 100
        return (score, cand.get("mtime", 0.0))

    candidates.sort(key=_rank_cand, reverse=True)

    for cand in candidates:
        role = cand["role"]
        path = cand["path"]
        b_idx = cand["blade_index"]

        if role == "total_moment" and not f.total:
            f.total = path
        elif role == "shaft_moment" and not f.shaft:
            f.shaft = path
        elif role == "fx" and not f.fx:
            f.fx = path
        elif role == "fy" and not f.fy:
            f.fy = path
        elif role == "shaft_fx" and not f.fx_shaft:
            f.fx_shaft = path
        elif role == "shaft_fy" and not f.fy_shaft:
            f.fy_shaft = path
        elif role == "blade_moment" and b_idx is not None and 1 <= b_idx <= n_blades:
            if f.blades[b_idx - 1] is None:
                f.blades[b_idx - 1] = path
        elif role == "blade_fx" and b_idx is not None and 1 <= b_idx <= n_blades:
            if f.fx_blades[b_idx - 1] is None:
                f.fx_blades[b_idx - 1] = path
        elif role == "blade_fy" and b_idx is not None and 1 <= b_idx <= n_blades:
            if f.fy_blades[b_idx - 1] is None:
                f.fy_blades[b_idx - 1] = path

    return f


def detect_solver_model_from_text(content: str) -> Optional[str]:
    """Extract viscous / turbulence model from any Fluent configuration text,
    XML report, transcript, journal, or Scheme settings without hardcoding defaults.
    
    Returns the exact identified model string, or None if undetermined.
    """
    if not content or not isinstance(content, str):
        return None

    # 1. Scheme boolean flags
    def _is_flag_true(flag: str) -> bool:
        return bool(re.search(rf"\({flag}\s+(?:\.|\s)*(?:#(?:t|T)|1\b)", content, re.IGNORECASE))

    if _is_flag_true(r"rp-inviscid\?"):
        return "Inviscid"
    if _is_flag_true(r"rp-lam\?"):
        return "Laminar"
    if _is_flag_true(r"rp-sa\?"):
        return "Spalart-Allmaras"
    if _is_flag_true(r"rp-trans-sst\?"):
        return "Transition SST"
    if _is_flag_true(r"rp-trans-one-eqn\?"):
        return "Transition (1-eqn)"
    if _is_flag_true(r"rp-des\?"):
        return "Detached Eddy Simulation (DES)"
    if _is_flag_true(r"rp-les\?"):
        return "Large Eddy Simulation (LES)"
    if _is_flag_true(r"rp-sas\?"):
        return "Scale-Adaptive Simulation (SAS)"
    if _is_flag_true(r"rp-rsm\?") or _is_flag_true(r"drsm\?"):
        return "Reynolds Stress Model (RSM)"
    if _is_flag_true(r"rp-kw\?"):
        if _is_flag_true(r"kw-geko-on\?"):
            return "GEKO k-omega"
        if _is_flag_true(r"kw-standard\?"):
            return "Standard k-omega"
        if _is_flag_true(r"kw-bsl\?"):
            return "BSL k-omega"
        return "k-omega SST"
    if _is_flag_true(r"rp-ke\?"):
        if _is_flag_true(r"ke-realizable\?"):
            return "Realizable k-epsilon"
        if _is_flag_true(r"ke-rng\?"):
            return "RNG k-epsilon"
        return "Standard k-epsilon"

    # 2. Fluent Version line (XML reports & console logs) e.g.
    # <version>Fluent Version: 2d, dp, pbns, sstkw, transient</version>
    # Fluent Version: 2d, dp, pbns, lam, transient
    m_ver = re.search(r"Fluent Version:\s*([^\n\r<]+)", content, re.IGNORECASE)
    if m_ver:
        ver_tags = [tok.strip().lower() for tok in m_ver.group(1).replace(",", " ").split()]
        if "sstkw" in ver_tags:
            return "k-omega SST"
        if "skw" in ver_tags:
            return "Standard k-omega"
        if "geko" in ver_tags:
            return "GEKO k-omega"
        if "rke" in ver_tags:
            return "Realizable k-epsilon"
        if "rngke" in ver_tags:
            return "RNG k-epsilon"
        if "ske" in ver_tags:
            return "Standard k-epsilon"
        if "lam" in ver_tags or "laminar" in ver_tags:
            return "Laminar"
        if "inviscid" in ver_tags:
            return "Inviscid"
        if "sa" in ver_tags:
            return "Spalart-Allmaras"
        if "trans-sst" in ver_tags:
            return "Transition SST"
        if "rsm" in ver_tags:
            return "Reynolds Stress Model (RSM)"
        if "les" in ver_tags:
            return "Large Eddy Simulation (LES)"
        if "des" in ver_tags:
            return "Detached Eddy Simulation (DES)"

    # 3. Models table row: Viscous\s+...
    m_visc = re.search(r"^\s*Viscous\s+([^\n\r]+)", content, re.MULTILINE | re.IGNORECASE)
    if m_visc:
        raw_v = m_visc.group(1).strip()
        low_v = raw_v.lower()
        if "sst" in low_v and "k-omega" in low_v:
            return "k-omega SST"
        if "standard" in low_v and "k-omega" in low_v:
            return "Standard k-omega"
        if "geko" in low_v:
            return "GEKO k-omega"
        if "realizable" in low_v and "k-epsilon" in low_v:
            return "Realizable k-epsilon"
        if "rng" in low_v and "k-epsilon" in low_v:
            return "RNG k-epsilon"
        if "standard" in low_v and "k-epsilon" in low_v:
            return "Standard k-epsilon"
        if "spalart" in low_v:
            return "Spalart-Allmaras"
        if "transition" in low_v:
            return "Transition SST"
        if "laminar" in low_v:
            return "Laminar"
        if "inviscid" in low_v:
            return "Inviscid"
        if "reynolds stress" in low_v or "rsm" in low_v:
            return "Reynolds Stress Model (RSM)"
        if raw_v:
            return raw_v

    # 4. TUI / Journal Commands: e.g. /define/models/viscous/kw-sst? yes
    if re.search(r"(?:define/models/)?viscous/kw-sst\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "k-omega SST"
    if re.search(r"(?:define/models/)?viscous/kw-standard\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Standard k-omega"
    if re.search(r"(?:define/models/)?viscous/kw-geko\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "GEKO k-omega"
    if re.search(r"(?:define/models/)?viscous/ke-realizable\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Realizable k-epsilon"
    if re.search(r"(?:define/models/)?viscous/ke-rng\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "RNG k-epsilon"
    if re.search(r"(?:define/models/)?viscous/ke-standard\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Standard k-epsilon"
    if re.search(r"(?:define/models/)?viscous/laminar\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Laminar"
    if re.search(r"(?:define/models/)?viscous/inviscid\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Inviscid"
    if re.search(r"(?:define/models/)?viscous/spalart-allmaras\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Spalart-Allmaras"
    if re.search(r"(?:define/models/)?viscous/transition-sst\??\s+(?:yes|#t|1)", content, re.IGNORECASE):
        return "Transition SST"

    return None


def _detect_wb_context(folder: str) -> Tuple[Dict[str, Any], str]:
    """Resolve Workbench system context and target Fluent folder."""
    wb_ctx: Dict[str, Any] = {}
    try:
        from vawt_post.engine.mesh_inspector import resolve_workbench_system_context
        wb_ctx = resolve_workbench_system_context(folder)
    except Exception:
        wb_ctx = {}
    resolved = (
        str(wb_ctx["fluent_dir"])
        if wb_ctx.get("fluent_dir") and os.path.isdir(str(wb_ctx.get("fluent_dir")))
        else resolve_fluent_directory(folder)
    )
    return wb_ctx, resolved


def _apply_msh_fallback(detected: Dict[str, Any], wb_ctx: Dict[str, Any], sources: List[str]) -> None:
    """Populate missing cell/node counts from MECH/*.msh if present."""
    try:
        msh_p = wb_ctx.get("msh_file")
        if msh_p and os.path.isfile(str(msh_p)) and ("cells" not in detected or "nodes" not in detected):
            from vawt_post.engine.mesh_inspector import parse_msh_file_summary
            ms = parse_msh_file_summary(str(msh_p))
            found_msh = False
            if ms.get("cells") and "cells" not in detected:
                detected["cells"] = int(ms["cells"])
                found_msh = True
            if ms.get("nodes") and "nodes" not in detected:
                detected["nodes"] = int(ms["nodes"])
                found_msh = True
            if found_msh:
                sources.append(os.path.basename(str(msh_p)))
    except Exception:
        pass


def _attach_workbench_mesh_and_setup(
    detected: Dict[str, Any],
    wb_ctx: Dict[str, Any],
    folder: str,
    hint_params: Optional[Dict[str, Any]],
) -> None:
    """Attach resolved Workbench context, mesh quality summary, and solver notes when present."""
    has_artifacts = bool(wb_ctx.get("is_workbench_system")) or any(
        wb_ctx.get(k)
        for k in (
            "case_h5", "setup_case_h5", "legacy_case", "dat_h5",
            "msh_file", "mshdb_file", "agdb_file", "set_file",
            "report_xml", "transcript_file",
        )
    )
    if not has_artifacts:
        return

    detected["system_context"] = wb_ctx
    try:
        from vawt_post.engine.mesh_inspector import inspect_workbench_setup_and_mesh
        hints = hint_params or {}
        r_params = {
            "U": detected.get("U") or hints.get("U"),
            "omega": detected.get("omega") or hints.get("omega"),
            "R": detected.get("R") or hints.get("R"),
            "chord": detected.get("chord") or hints.get("chord"),
            "rho": detected.get("rho") or hints.get("rho") or 1.225,
            "mu": detected.get("mu") or hints.get("mu") or 1.7894e-5,
        }
        full_mesh = inspect_workbench_setup_and_mesh(
            str(wb_ctx.get("system_dir") or folder),
            rotor_params=r_params,
            compute_cell_quality=True,
        )
        if full_mesh and (
            full_mesh.get("mesh", {}).get("available")
            or full_mesh.get("setup")
            or full_mesh.get("notes")
        ):
            detected["mesh_info"] = full_mesh
            if full_mesh.get("notes"):
                detected["setup_notes"] = full_mesh["notes"]
            xml_st = full_mesh.get("setup", {})
            if "iterations" not in detected and xml_st.get("max_iter_per_step"):
                detected["iterations"] = int(xml_st["max_iter_per_step"])
    except Exception:
        pass


def detect_folder_simulation_parameters(
    folder: str,
    hint_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Auto-detect physical parameters and case metadata from a simulation directory.

    Inspects available ANSYS Fluent artifacts:
      1. Native HDF5 case (.cas.h5): radius R, chord c, blade count N, velocity U,
         omega, cell/node count, viscous/turb model, time step size, space, time formulation.
      2. Legacy case files (*.cas): omega, velocity, viscous model, time step.
      3. Fluent setup report (report.xml, including Workbench progress_files):
         omega, velocity U, time step dt, density rho, viscosity mu, inner iterations,
         blade zones, turbulence model, space, time formulation.
      4. Scheme settings files (*.set):
         mrf-omega, reference-velocity, reference-density, reference-viscosity, viscous model.
      5. Fluent transcripts (*.trn, *.log) and journal files (*.jou):
         viscous model, inner iterations, moving mesh speed, inflow velocity, time step.
      6. Monitor output files (*.out, *.dat, *.csv):
         physical time step dt derived from consecutive flow-time values, blade count.
      7. Physical coupling relationships:
         derives TSR lambda = omega * R / U or omega = lambda * U / R, R, or U.

    Returns:
        Dict[str, Any] with detected parameter keys (only includes keys with verified values).
    """
    detected: Dict[str, Any] = {}
    sources: List[str] = []
    if not folder:
        return detected

    wb_ctx, folder = _detect_wb_context(folder)
    if not os.path.isdir(folder):
        return detected

    import glob

    cas_res_best: Any = None

    # 1. Inspect HDF5 cases (*.cas.h5)
    try:
        from vawt_post.engine.hdf5_case import inspect_hdf5_case
        cas_files = sorted(glob.glob(os.path.join(folder, "*.cas.h5")), key=os.path.getmtime, reverse=True)
        for cas_path in cas_files:
            cas_res = inspect_hdf5_case(cas_path)
            if cas_res.is_valid:
                cas_res_best = cas_res
                sources.append(os.path.basename(cas_path))
                if cas_res.rotor_radius_m is not None and cas_res.rotor_radius_m > 0 and "R" not in detected:
                    detected["R"] = round(float(cas_res.rotor_radius_m), 6)
                if cas_res.chord_m is not None and cas_res.chord_m > 0 and "chord" not in detected:
                    detected["chord"] = round(float(cas_res.chord_m), 6)
                if cas_res.blade_count and cas_res.blade_count > 0 and "n_blades" not in detected:
                    detected["n_blades"] = int(cas_res.blade_count)
                if cas_res.velocity_m_s is not None and cas_res.velocity_m_s > 0 and "U" not in detected:
                    detected["U"] = float(cas_res.velocity_m_s)
                if cas_res.omega_rad_s is not None and abs(float(cas_res.omega_rad_s)) > 0 and "omega" not in detected:
                    om_raw = float(cas_res.omega_rad_s)
                    detected["omega"] = abs(om_raw)
                    if om_raw < 0 and "rotation_dir" not in detected:
                        detected["rotation_dir"] = "cw"
                if cas_res.cell_count and cas_res.cell_count > 0 and "cells" not in detected:
                    detected["cells"] = int(cas_res.cell_count)
                if cas_res.node_count and cas_res.node_count > 0 and "nodes" not in detected:
                    detected["nodes"] = int(cas_res.node_count)
                if cas_res.turb_model and "turb_model" not in detected:
                    detected["turb_model"] = str(cas_res.turb_model)
                if cas_res.dimension and "space" not in detected:
                    detected["space"] = f"{cas_res.dimension}D"
                if cas_res.time_model and "time_formulation" not in detected:
                    detected["time_formulation"] = str(cas_res.time_model)
                if cas_res.density_kg_m3 and "rho" not in detected:
                    detected["rho"] = float(cas_res.density_kg_m3)
                if cas_res.viscosity_pa_s and "mu" not in detected:
                    detected["mu"] = float(cas_res.viscosity_pa_s)
                if cas_res.time_step_s is not None and cas_res.time_step_s > 0 and "dt" not in detected:
                    detected["dt"] = float(cas_res.time_step_s)
                for rname, rdef in cas_res.report_definitions.items():
                    if "moment" in rname.lower():
                        if getattr(rdef, "mom_center", None) in ((0.0, 0.0, 0.0), (0.0, 0.0)):
                            detected["moment_center_confirmed"] = True
                break
    except Exception:
        pass

    # 1b. Legacy text case files (*.cas)
    try:
        legacy_cas = [f for f in glob.glob(os.path.join(folder, "*.cas")) if not f.endswith(".cas.h5")]
        for lcas in sorted(legacy_cas, key=os.path.getmtime, reverse=True):
            found_cas = False
            with open(lcas, "r", errors="ignore") as f:
                c_text = f.read(131072)
            if "omega" not in detected:
                m_mrf = re.search(r"\(mrf-omega\s*\(constant\s+\.\s+([0-9.+-eE]+)\)", c_text)
                if m_mrf:
                    om_raw = float(m_mrf.group(1))
                    if abs(om_raw) > 0:
                        detected["omega"] = abs(om_raw)
                        if om_raw < 0 and "rotation_dir" not in detected:
                            detected["rotation_dir"] = "cw"
                        found_cas = True
            if "U" not in detected:
                m_v = re.search(r"\(reference-velocity\s+([0-9.+-eE]+)\)", c_text)
                if m_v:
                    detected["U"] = float(m_v.group(1))
                    found_cas = True
            if "turb_model" not in detected:
                tm = detect_solver_model_from_text(c_text)
                if tm:
                    detected["turb_model"] = tm
                    found_cas = True
            if found_cas:
                sources.append(os.path.basename(lcas))
                break
    except Exception:
        pass

    # 2. Check report.xml (in folder, parent, or Workbench progress_files)
    try:
        from vawt_post.engine.workbench import parse_fluent_report_xml
        xml_candidates = []
        if wb_ctx.get("report_xml"):
            xml_candidates.append(str(wb_ctx["report_xml"]))
        xml_candidates.extend([
            os.path.join(folder, "report.xml"),
            os.path.join(os.path.dirname(folder), "report.xml"),
        ])
        norm_parts = os.path.normpath(folder).split(os.sep)
        if "dp0" in norm_parts:
            dp0_idx = norm_parts.index("dp0")
            if dp0_idx > 0:
                base_dir = os.sep.join(norm_parts[:dp0_idx])
                sub_path = os.sep.join(norm_parts[dp0_idx:])
                wb_xml = os.path.join(base_dir, "progress_files", sub_path, "report.xml")
                xml_candidates.append(wb_xml)

        for xml_path in xml_candidates:
            if os.path.isfile(xml_path):
                xml_data = parse_fluent_report_xml(xml_path)
                sources.append(os.path.basename(xml_path))
                if xml_data.get("omega") is not None and "omega" not in detected:
                    om_raw = float(xml_data["omega"])
                    if abs(om_raw) > 0:
                        detected["omega"] = abs(om_raw)
                        if om_raw < 0 and "rotation_dir" not in detected:
                            detected["rotation_dir"] = "cw"
                if xml_data.get("velocity") is not None and "U" not in detected:
                    detected["U"] = float(xml_data["velocity"])
                if xml_data.get("time_step") is not None and "dt" not in detected:
                    detected["dt"] = float(xml_data["time_step"])
                if xml_data.get("density") is not None and "rho" not in detected:
                    detected["rho"] = float(xml_data["density"])
                if xml_data.get("viscosity") is not None and "mu" not in detected:
                    detected["mu"] = float(xml_data["viscosity"])
                if xml_data.get("max_iter_per_step") is not None and "iterations" not in detected:
                    detected["iterations"] = int(xml_data["max_iter_per_step"])
                if xml_data.get("blade_zones") and "n_blades" not in detected:
                    detected["n_blades"] = len(xml_data["blade_zones"])
                if xml_data.get("turb_model") and "turb_model" not in detected:
                    detected["turb_model"] = xml_data["turb_model"]
                if xml_data.get("space") and "space" not in detected:
                    detected["space"] = xml_data["space"]
                if xml_data.get("time_formulation") and "time_formulation" not in detected:
                    detected["time_formulation"] = xml_data["time_formulation"]
                break
    except Exception:
        pass

    # 2b. Fallback cell/node count from MECH/*.msh
    _apply_msh_fallback(detected, wb_ctx, sources)

    # 3. Scheme .set files
    try:
        for sf in sorted(glob.glob(os.path.join(folder, "*.set")), key=os.path.getmtime, reverse=True):
            with open(sf, "r", errors="ignore") as f:
                content = f.read()
            found_set = False
            if "omega" not in detected:
                m_mrf = re.findall(r"\(mrf-omega\s*\(constant\s+\.\s+([0-9.+-eE]+)\)", content)
                if m_mrf:
                    v = float(m_mrf[-1])
                    if v > 0:
                        detected["omega"] = v
                        found_set = True
            if "U" not in detected:
                m_v = re.findall(r"\(reference-velocity\s+([0-9.+-eE]+)\)", content)
                if m_v:
                    detected["U"] = float(m_v[-1])
                    found_set = True
            if "rho" not in detected:
                m_rho = re.findall(r"\(reference-density\s+([0-9.+-eE]+)\)", content)
                if m_rho:
                    detected["rho"] = float(m_rho[-1])
                    found_set = True
            if "mu" not in detected:
                m_mu = re.findall(r"\(reference-viscosity\s+([0-9.+-eE]+)\)", content)
                if m_mu:
                    detected["mu"] = float(m_mu[-1])
                    found_set = True
            if "dt" not in detected:
                m_dt = re.findall(r"\(time-step-size\s+\.\s+([0-9.+-eE]+)\)", content)
                if m_dt:
                    detected["dt"] = float(m_dt[-1])
                    found_set = True
            if "turb_model" not in detected:
                t_mod = detect_solver_model_from_text(content)
                if t_mod:
                    detected["turb_model"] = t_mod
                    found_set = True

            if found_set:
                sources.append(os.path.basename(sf))
    except Exception:
        pass

    # 4. Transcripts (.trn, .log, transcript*.txt) and Jou files for inner iterations and model hints
    try:
        trn_files = sorted(
            glob.glob(os.path.join(folder, "*.trn")) + glob.glob(os.path.join(folder, "*.log")),
            key=os.path.getmtime,
            reverse=True
        )
        for tf in trn_files:
            found_trn = False
            with open(tf, "r", errors="ignore") as f:
                ttext = f.read(131072)
            if "time_formulation" not in detected:
                if "unsteady-2nd-order" in ttext:
                    detected["time_formulation"] = "Unsteady, 2nd-Order Implicit"
                    found_trn = True
                elif "unsteady-1st-order" in ttext:
                    detected["time_formulation"] = "Unsteady, 1st-Order Implicit"
                    found_trn = True
            if "turb_model" not in detected:
                tm = detect_solver_model_from_text(ttext)
                if tm:
                    detected["turb_model"] = tm
                    found_trn = True
            if "omega" not in detected:
                m_om = re.search(r"Moving Mesh Rotation Speed\s*\[rad/s\]\s+([0-9.+-eE]+)", ttext)
                if m_om:
                    detected["omega"] = float(m_om.group(1))
                    found_trn = True
            if "U" not in detected:
                m_vel = re.search(r"Velocity Magnitude\s*\[m/s\]\s+([0-9.+-eE]+)", ttext)
                if m_vel:
                    detected["U"] = float(m_vel.group(1))
                    found_trn = True
            if "dt" not in detected:
                m_dt = re.search(r"Time Step Size\s*\[s\]\s+([0-9.+-eE]+)", ttext)
                if m_dt:
                    detected["dt"] = float(m_dt.group(1))
                    found_trn = True
            if found_trn:
                sources.append(os.path.basename(tf))

        for jf in glob.glob(os.path.join(folder, "*.jou")):
            found_jou = False
            with open(jf, "r", errors="ignore") as f:
                jtext = f.read()
            m_iter = re.search(r"dual-time-iterate\s+[0-9.]+\s+([0-9]+)", jtext)
            if m_iter and "iterations" not in detected:
                detected["iterations"] = int(m_iter.group(1))
                found_jou = True
            if "turb_model" not in detected:
                tm = detect_solver_model_from_text(jtext)
                if tm:
                    detected["turb_model"] = tm
                    found_jou = True
            if found_jou:
                sources.append(os.path.basename(jf))
    except Exception:
        pass

    # 5. Monitor files for dt and blade count
    try:
        out_candidates = [
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith((".out", ".dat", ".txt", ".csv")) and not f.lower().endswith((".trn", ".log", ".jou", ".set", ".cas", ".msh"))
        ]
        # Prioritize moment monitor files first
        out_candidates.sort(key=lambda p: 0 if "moment" in os.path.basename(p).lower() else 1)
        for out_path in out_candidates:
            if "dt" in detected:
                break
            if os.path.isfile(out_path):
                info = inspect_fluent_report(out_path)
                time_col = info.get("time_col")
                if time_col is not None:
                    times: List[float] = []
                    with open(out_path, "r", errors="ignore") as fh:
                        for line in fh:
                            toks = line.replace("(", " ").replace(")", " ").replace(",", " ").split()
                            try:
                                nums = [float(x) for x in toks]
                                if len(nums) > time_col:
                                    times.append(nums[time_col])
                                    if len(times) >= 15:
                                        break
                            except ValueError:
                                pass
                    if len(times) >= 2:
                        diffs = [abs(times[i + 1] - times[i]) for i in range(len(times) - 1) if abs(times[i + 1] - times[i]) > 1e-12]
                        if diffs:
                            mean_dt = float(np.mean(diffs))
                            detected["dt"] = round(mean_dt, 8)
                            sources.append(os.path.basename(out_path))
                            break
    except Exception:
        pass

    if "n_blades" not in detected:
        try:
            cands = list_report_candidates(folder, n_blades=8)
            blade_indices = [c["blade_index"] for c in cands if c.get("blade_index") is not None]
            if blade_indices:
                detected["n_blades"] = max(blade_indices)
        except Exception:
            pass

    # 6. Physical coupling: derive TSR, Omega, Radius, or Velocity
    if "omega" in detected and abs(detected["omega"]) > 0 and "R" in detected and "U" in detected and detected["U"] > 0 and "tsr" not in detected:
        detected["tsr"] = round((abs(detected["omega"]) * detected["R"]) / detected["U"], 4)
    elif "tsr" in detected and "R" in detected and "U" in detected and detected["R"] > 0 and "omega" not in detected:
        detected["omega"] = round((detected["tsr"] * detected["U"]) / detected["R"], 4)
    elif "tsr" in detected and "omega" in detected and abs(detected["omega"]) > 0 and "U" in detected and "R" not in detected:
        detected["R"] = round((detected["tsr"] * detected["U"]) / abs(detected["omega"]), 6)
    elif "tsr" in detected and "omega" in detected and "R" in detected and detected["tsr"] > 0 and "U" not in detected:
        detected["U"] = round((abs(detected["omega"]) * detected["R"]) / detected["tsr"], 4)

    # 7. Start azimuth & mid-spin check (Tier 1 flow-time, Tier 2 .cas.h5 centroids, Tier 3 Blade 1 1P torque phase)
    try:
        hints = hint_params or {}
        om_det = detected.get("omega") or hints.get("omega")
        u_det = detected.get("U") or hints.get("U")
        r_det = detected.get("R") or hints.get("R")
        tsr_det = detected.get("tsr") or hints.get("tsr")
        if om_det or (u_det and r_det and tsr_det):
            from vawt_post.engine.azimuth_detect import detect_start_azimuth
            from vawt_post.engine.config import Params

            nb_det = int(detected.get("n_blades") or hints.get("n_blades") or 3)
            dt_det = detected.get("dt") or hints.get("dt")
            p_az = Params(
                U=float(u_det) if u_det else 8.0,
                tsr=float(tsr_det) if tsr_det else None,
                omega=abs(float(om_det)) if om_det else None,
                R=float(r_det) if r_det else 0.5,
                n_blades=max(1, min(8, nb_det)),
                dt=float(dt_det) if dt_det else None,
                rotation_dir=str(detected.get("rotation_dir") or hints.get("rotation_dir") or "ccw"),
            )
            fd_az = autodetect(folder, nb_det)
            t_az = step_az = b1_az = tot_az = None
            if fd_az.blades and fd_az.blades[0] and os.path.isfile(fd_az.blades[0]):
                rep_b1 = read_fluent_report(fd_az.blades[0])
                if len(rep_b1["value"]) > 0:
                    step_az, b1_az = rep_b1["step"], rep_b1["value"]
                    t_az = rep_b1["time"] if rep_b1["time"] is not None else (
                        step_az.astype(float) * p_az.dt if p_az.dt else None
                    )
            if fd_az.total and os.path.isfile(fd_az.total):
                rep_tot = read_fluent_report(fd_az.total)
                if len(rep_tot["value"]) > 0:
                    tot_az = rep_tot["value"]
                    t_tot = rep_tot["time"] if rep_tot["time"] is not None else (
                        rep_tot["step"].astype(float) * p_az.dt if p_az.dt else None
                    )
                    if t_az is None and t_tot is not None:
                        step_az, t_az = rep_tot["step"], t_tot
                    elif b1_az is not None and (t_az is None or len(tot_az) != len(t_az)):
                        tot_az = None

            az_res = detect_start_azimuth(
                p_az,
                t=t_az,
                step=step_az,
                m_b1=b1_az,
                m_total=tot_az,
                apply_moment_sign=True,
                cas_info=cas_res_best,
            )
            detected["start_azimuth"] = az_res.to_dict()
            if (
                az_res.needs_correction
                and az_res.recommended_theta0_deg is not None
                and az_res.confidence in ("high", "medium")
            ):
                detected["theta0"] = round(float(az_res.recommended_theta0_deg), 1)
            elif az_res.mid_spin_compensated_by_flow_time:
                detected["theta0"] = 0.0
    except Exception:
        pass

    # 8. Workbench system context, mesh quality & setup notes summary
    _attach_workbench_mesh_and_setup(detected, wb_ctx, folder, hint_params)

    if sources:
        detected["source_files"] = list(dict.fromkeys(sources))
    return detected


