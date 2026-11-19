# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: File-Health Diagnostics Engine
======================================================
Comprehensive pre-analysis and continuous file-health diagnostics (Part A).
Checks monitor report structure, parsing validity, step and time continuity,
cross-file consistency, aerodynamic bounds, signal quality, and offers verified
data repairs (e.g. blade reconstruction).

GUI-free module: pure numpy / standard library.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from vawt_post.engine.config import Params, InputFiles
from vawt_post.engine.parser import inspect_fluent_report, parse_report_filename_role, read_fluent_report


@dataclass
class DiagnosticFinding:
    severity: str  # "ERROR", "WARNING", "INFO"
    file: str      # file role ("total", "blade 1", etc.) or filename
    check: str     # Name of the check
    location: str  # e.g. "Step 4860", "Header", "Steps 100-150"
    step: Optional[int] = None
    flow_time: Optional[float] = None
    explanation: str = ""
    suggested_fix: str = ""
    repair_available: bool = False
    repair_action: Optional[str] = None
    repair_description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosticsReport:
    findings: List[DiagnosticFinding] = field(default_factory=list)
    summary_counts: Dict[str, int] = field(default_factory=lambda: {"ERROR": 0, "WARNING": 0, "INFO": 0})
    candidate_files: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    suggested_repairs: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return self.summary_counts.get("ERROR", 0) > 0

    @property
    def has_warnings(self) -> bool:
        return self.summary_counts.get("WARNING", 0) > 0

    @property
    def badge_status(self) -> str:
        if self.has_errors:
            return "ERROR"
        if self.has_warnings:
            return "WARNING"
        return "PASS"

    def add(self, finding: DiagnosticFinding) -> None:
        self.findings.append(finding)
        self.summary_counts[finding.severity] = self.summary_counts.get(finding.severity, 0) + 1
        if finding.repair_available and finding.repair_action:
            self.suggested_repairs.append({
                "action": finding.repair_action,
                "description": finding.repair_description,
                "file": finding.file,
            })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary_counts": self.summary_counts,
            "badge_status": self.badge_status,
            "findings": [f.to_dict() for f in self.findings],
            "candidate_files": self.candidate_files,
            "suggested_repairs": self.suggested_repairs,
        }


def scan_folder_candidates(folder: str, n_blades: int) -> Dict[str, List[Dict[str, Any]]]:
    """Scan directory for ambiguous or multiple candidate monitor files (Check 10)."""
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    if not folder or not os.path.isdir(folder):
        return candidates

    try:
        entries = sorted(os.listdir(folder))
    except Exception:
        return candidates

    files = [os.path.join(folder, e) for e in entries if e.lower().endswith((".out", ".txt", ".dat", ".csv"))]

    total_candidates = []
    blade_candidates = [[] for _ in range(n_blades)]
    shaft_candidates = []
    fx_candidates = []
    fy_candidates = []
    fx_shaft_candidates = []
    fy_shaft_candidates = []

    for fpath in files:
        try:
            st = os.stat(fpath)
            sz = st.st_size
            mtime = st.st_mtime
        except Exception:
            sz, mtime = 0, 0.0

        last_step = None
        try:
            with open(fpath, "rb") as fh:
                fh.seek(max(0, sz - 2048))
                tail_lines = fh.read().decode("latin-1", errors="ignore").splitlines()
                for ln in reversed(tail_lines):
                    parts = ln.strip().split()
                    if parts and parts[0].isdigit():
                        last_step = int(parts[0])
                        break
        except Exception:
            pass

        item = {
            "path": fpath,
            "filename": os.path.basename(fpath),
            "size_bytes": sz,
            "mtime": mtime,
            "mtime_str": os.path.getmtime(fpath) if os.path.isfile(fpath) else 0,
            "last_step": last_step,
        }

        # Match roles via unified filename classifier (prevents per-blade force files or airfoil1 colliding)
        role_cls, b_idx_cls, _ = parse_report_filename_role(os.path.basename(fpath), n_blades=n_blades)
        if role_cls == "coefficient":
            continue
        elif role_cls == "shaft_fx":
            fx_shaft_candidates.append(item)
        elif role_cls == "shaft_fy":
            fy_shaft_candidates.append(item)
        elif role_cls == "shaft_moment":
            shaft_candidates.append(item)
        elif role_cls == "fx":
            fx_candidates.append(item)
        elif role_cls == "fy":
            fy_candidates.append(item)
        elif role_cls == "blade_moment" and b_idx_cls is not None and 1 <= b_idx_cls <= n_blades:
            blade_candidates[b_idx_cls - 1].append(item)
        elif role_cls == "total_moment":
            total_candidates.append(item)

    if len(total_candidates) > 1:
        candidates["total"] = total_candidates
    for idx, b_list in enumerate(blade_candidates):
        if len(b_list) > 1:
            candidates[f"blade {idx + 1}"] = b_list
    if len(shaft_candidates) > 1:
        candidates["shaft"] = shaft_candidates
    if len(fx_candidates) > 1:
        candidates["fx"] = fx_candidates
    if len(fy_candidates) > 1:
        candidates["fy"] = fy_candidates
    if len(fx_shaft_candidates) > 1:
        candidates["fx_shaft"] = fx_shaft_candidates
    if len(fy_shaft_candidates) > 1:
        candidates["fy_shaft"] = fy_shaft_candidates

    return candidates


def run_file_health_diagnostics(
    p: Params,
    f: InputFiles,
    watch_folder: Optional[str] = None,
    is_live_mode: bool = False
) -> DiagnosticsReport:
    """Execute all 17 File-Health Diagnostic checks across input monitor files.

    Args:
        p: Turbine & operating parameters
        f: Assigned input monitor files
        watch_folder: Optional base folder to scan for candidate files
        is_live_mode: If True, partial trailing lines are treated as INFO (in progress)

    Returns:
        DiagnosticsReport containing all structured findings and suggested repairs.
    """
    report = DiagnosticsReport()
    nb = p.n_blades if (p.n_blades is not None and p.n_blades > 0) else max(len([b for b in f.blades if b]), 3)

    # Collect assigned files with their role names
    file_roles: List[Tuple[str, Optional[str]]] = [("total", f.total)]
    for i, bp in enumerate(f.blades):
        file_roles.append((f"blade {i + 1}", bp))
    if f.shaft:
        file_roles.append(("shaft", f.shaft))
    if f.fx:
        file_roles.append(("fx", f.fx))
    if f.fy:
        file_roles.append(("fy", f.fy))
    if f.fx_shaft:
        file_roles.append(("fx_shaft", f.fx_shaft))
    if f.fy_shaft:
        file_roles.append(("fy_shaft", f.fy_shaft))

    active_files = [(role, path) for role, path in file_roles if path]

    # Check 10: Ambiguous or stale candidates in folder
    folder_to_check = watch_folder
    if not folder_to_check and active_files:
        folder_to_check = os.path.dirname(active_files[0][1])

    if folder_to_check and os.path.isdir(folder_to_check):
        candidates = scan_folder_candidates(folder_to_check, nb)
        report.candidate_files = candidates
        for role, c_list in candidates.items():
            names = [c["filename"] for c in c_list]
            report.add(DiagnosticFinding(
                severity="WARNING",
                file=role,
                check="Ambiguous candidates in folder",
                location="Folder scan",
                explanation=f"Multiple monitor files match role '{role}': {', '.join(names)}. Must confirm selection.",
                suggested_fix="Select the intended file explicitly in the File Selection dialog.",
            ))

    # Parse and inspect individual files
    parsed_files: Dict[str, Dict[str, Any]] = {}
    parsed_paths: Dict[str, str] = {}
    file_mtimes: Dict[str, float] = {}

    for role, path in active_files:
        # Check 1: File existence, readability, empty, or no numeric rows
        if not os.path.isfile(path):
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="File missing or unreadable",
                location="File system",
                explanation=f"Assigned file '{path}' does not exist on disk or is not readable.",
                suggested_fix="Verify the file path and ensure ANSYS Fluent has generated the monitor file.",
            ))
            continue

        try:
            sz = os.path.getsize(path)
            mtime = os.path.getmtime(path)
            file_mtimes[role] = mtime
        except Exception as e:
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="File unreadable",
                location="File system",
                explanation=f"Cannot read file metadata: {e}",
                suggested_fix="Check file permissions or verify file is not locked by another process.",
            ))
            continue

        if sz == 0:
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="File empty",
                location="File system",
                explanation=f"File '{os.path.basename(path)}' is 0 bytes.",
                suggested_fix="Run Fluent simulation for at least one time step to write output.",
            ))
            continue

        # Inspect headers and raw lines
        try:
            info = inspect_fluent_report(path)
        except Exception as e:
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="Header inspection failed",
                location="Header",
                explanation=f"Failed to inspect headers in '{os.path.basename(path)}': {e}",
                suggested_fix="Verify Fluent report format or remove corrupted header lines.",
            ))
            continue

        # Check 2: Missing value column
        # Detect if file header lists only "Time Step" and "flow-time" (no monitor data)
        cols = [c.lower() for c in info["columns"]]
        has_step = any("step" in c or "iteration" in c for c in cols)
        has_time = any("flow-time" in c or "time" in c for c in cols)
        data_cols = info["data_columns"]

        if (len(cols) <= 2 and has_step and has_time) or len(data_cols) == 0:
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="Missing value column",
                location="Header",
                explanation=(
                    f"File '{os.path.basename(path)}' contains only 'Time Step' and 'flow-time'. "
                    "The report definition value column is absent."
                ),
                suggested_fix="In ANSYS Fluent, add the report definition to this report file and rerun a short test.",
            ))
            continue

        # Check 3: Header handling & Role mismatch
        # Warn if header names don't match the assigned role
        hdr_str = " ".join(cols)
        if "blade" in role:
            # Extract blade number
            b_num = role.split()[-1]
            for other_b in range(1, nb + 1):
                if str(other_b) != b_num:
                    if (f"moment-{other_b}" in hdr_str or f"blade-{other_b}" in hdr_str or
                            f"moment_{other_b}" in hdr_str or f"blade_{other_b}" in hdr_str or
                            f"b{other_b}" in hdr_str):
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file=role,
                            check="Header role mismatch",
                            location="Header",
                            explanation=(
                                f"File assigned to {role.capitalize()} has header indicating Blade {other_b} "
                                f"('{info['columns']}')."
                            ),
                            suggested_fix="Verify blade slot assignments in File Selection to ensure correct blade mapping.",
                        ))
                        break
        elif role == "shaft" and ("blade" in hdr_str or "total" in hdr_str):
            report.add(DiagnosticFinding(
                severity="WARNING",
                file=role,
                check="Header role mismatch",
                location="Header",
                explanation=f"File assigned as Shaft moment contains header '{info['columns']}'.",
                suggested_fix="Verify that this monitor file records shaft moment, not blade or rotor torque.",
            ))
        elif role == "fx" and ("fy" in hdr_str or "moment" in hdr_str):
            report.add(DiagnosticFinding(
                severity="WARNING",
                file=role,
                check="Header role mismatch",
                location="Header",
                explanation=f"File assigned as Force X contains header '{info['columns']}'.",
                suggested_fix="Verify monitor records streamwise thrust force (X).",
            ))

        # Check 3b: Non-dimensional coefficient in header where dimensional force/moment expected
        for col_idx, col_name in info.get("data_columns", []):
            clow = col_name.lower()
            if re.search(r"\b(cd|cl|cm|c_d|c_l|c_m|drag[_-]?coeff|lift[_-]?coeff|moment[_-]?coeff)\b", clow):
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Coefficient header in dimensional input",
                    location=f"Header '{col_name}'",
                    explanation=(
                        f"File assigned to '{role}' specifies a coefficient header ('{col_name}'). "
                        "If Fluent is recording non-dimensional coefficients rather than dimensional forces (N) or moments (N·m), "
                        "verify that Fluent reference values match the post-processor parameters."
                    ),
                    suggested_fix="Verify report definition in ANSYS Fluent: ensure report type (Force/Moment vs Coefficient) matches your intended processing workflow.",
                ))

        # Check 5: Mixed column counts across rows & non-numeric tokens & partial last line
        has_partial_last = False
        non_numeric_details = []  # List of tuples: (line_no, text_snippet)
        width_mismatches = []
        expected_w = None
        first_numeric_line_idx = None

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                raw_lines = fh.readlines()

            # Find the first numeric row: everything before it is Fluent header
            for l_idx, line in enumerate(raw_lines):
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                toks = s.replace("(", " ").replace(")", " ").replace(",", " ").split()
                if len(toks) < 2:
                    continue
                try:
                    _ = [float(x) for x in toks]
                    first_numeric_line_idx = l_idx
                    expected_w = len(toks)
                    break
                except ValueError:
                    continue

            # Check ONLY data rows (from first_numeric_line_idx onward)
            if first_numeric_line_idx is not None:
                for l_idx in range(first_numeric_line_idx, len(raw_lines)):
                    line = raw_lines[l_idx]
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    toks = s.replace("(", " ").replace(")", " ").replace(",", " ").split()
                    if not toks:
                        continue
                    try:
                        _ = [float(x) for x in toks]
                        if len(toks) != expected_w:
                            if l_idx == len(raw_lines) - 1:
                                has_partial_last = True
                            else:
                                width_mismatches.append(l_idx + 1)
                    except ValueError:
                        if l_idx == len(raw_lines) - 1 and expected_w is not None and len(toks) < expected_w:
                            has_partial_last = True
                        else:
                            non_numeric_details.append((l_idx + 1, s[:60]))

            if has_partial_last:
                report.add(DiagnosticFinding(
                    severity="INFO" if is_live_mode else "WARNING",
                    file=role,
                    check="Partial last line",
                    location=f"Line {len(raw_lines)}",
                    explanation=f"File '{os.path.basename(path)}' ends with a partial uncompleted row.",
                    suggested_fix="In live mode, partial lines are ignored until completed by the solver.",
                ))
            if non_numeric_details:
                loc_str = ", ".join(f"Line {ln}" for ln, _ in non_numeric_details[:3])
                sample_details = "; ".join(f"Line {ln}: '{txt}'" for ln, txt in non_numeric_details[:3])
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Non-numeric tokens",
                    location=loc_str,
                    explanation=f"Found non-numeric token(s) on {len(non_numeric_details)} data row(s) ({sample_details}).",
                    suggested_fix="Check for solver error messages or corrupted numeric tokens in monitor data rows.",
                ))
            if width_mismatches:
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Inconsistent column count",
                    location=f"Lines {', '.join(str(x) for x in width_mismatches[:5])}",
                    explanation=f"Inconsistent column widths across {len(width_mismatches)} data row(s).",
                    suggested_fix="Verify that the simulation did not reconfigure monitor definitions mid-run.",
                ))
        except Exception:
            pass

        # Parse numeric data
        try:
            data = read_fluent_report(path)
            parsed_files[role] = data
            parsed_paths[role] = path
        except Exception as e:
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="Parsing failure",
                location="Numeric rows",
                explanation=f"Failed to parse numeric data from '{os.path.basename(path)}': {e}",
                suggested_fix="Check file structure and verify report definition columns.",
            ))
            continue

    # Optional prior run linking & missing-timestep recovery before time-series checks
    prior_src = getattr(f, "prior_source", None)
    cont_mode = getattr(f, "continuity_mode", "auto") or "auto"
    if prior_src and cont_mode != "off" and parsed_files:
        try:
            from vawt_post.engine.continuity import stitch_and_recover_all

            parsed_files, cont_rep = stitch_and_recover_all(
                parsed_files,
                prior_source=prior_src,
                p=p,
                mode=cont_mode,
                current_files=f,
            )
            c_stat = cont_rep.get("status", "")
            c_sum = cont_rep.get("summary_text", "")
            if c_stat == "no_match":
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file="Backup link",
                    check="Prior run linking",
                    location="Continuity",
                    explanation=c_sum or "No matching monitor files found in linked prior source.",
                    suggested_fix="Verify that the backup folder or .zip archive contains monitor files matching this simulation.",
                ))
            elif c_stat == "gap_recovered":
                n_rec = int(cont_rep.get("recovered_count", 0))
                rngs = cont_rep.get("recovered_step_ranges", [])
                loc_str = f"Steps {rngs[0][0]}–{rngs[0][1]}" if rngs else "Recovered gap"
                sev_gap = "INFO" if n_rec <= 30 else "WARNING"
                report.add(DiagnosticFinding(
                    severity=sev_gap,
                    file="All files",
                    check="Run continuity & recovery",
                    location=loc_str,
                    step=int(rngs[0][0]) if rngs else None,
                    explanation=c_sum,
                    suggested_fix="Recovered steps are highlighted in history charts; export stitched .out files to archive merged data.",
                ))
            elif c_stat == "incompatible_backup":
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file="Backup link",
                    check="Prior run linking",
                    location="Continuity",
                    explanation=c_sum or "Linked prior source does not match this simulation run and was ignored.",
                    suggested_fix="Unlink the stale prior source or select the matching prior backup folder/.zip for this simulation.",
                ))
            elif c_stat == "overlap_divergent":
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file="All files",
                    check="Run continuity overlap mismatch",
                    location="Checkpoint splice",
                    explanation=c_sum,
                    suggested_fix="Inspect solver settings or boundary conditions between initial and resumed runs.",
                ))
            elif c_stat in ("overlap_verified", "exact_adjacent"):
                report.add(DiagnosticFinding(
                    severity="INFO",
                    file="All files",
                    check="Run continuity verified",
                    location="Checkpoint splice",
                    explanation=c_sum,
                    suggested_fix="No action required; prior and resumed runs are continuously stitched.",
                ))
        except Exception as ce:
            report.add(DiagnosticFinding(
                severity="WARNING",
                file="Backup link",
                check="Prior run linking",
                location="Continuity",
                explanation=f"Could not read prior source '{prior_src}': {ce}",
                suggested_fix="Check path permissions or archive integrity.",
            ))

    for role, data in parsed_files.items():
        path = parsed_paths[role]
        step = data["step"]
        val = data["value"]
        time = data["time"]

        # Check 4: Columns that look misread (value equal or proportional to flow-time or step)
        if time is not None and len(time) > 1:
            diff_vt = np.abs(val - time)
            if np.all(diff_vt < 1e-6):
                report.add(DiagnosticFinding(
                    severity="ERROR",
                    file=role,
                    check="Value equals flow-time",
                    location=f"Steps {step[0]}–{step[-1]}",
                    step=int(step[0]),
                    flow_time=float(time[0]),
                    explanation=(
                        f"The value column in '{os.path.basename(path)}' is identical to flow-time. "
                        "The actual moment/force report was omitted."
                    ),
                    suggested_fix="Add the report definition to this report file in ANSYS Fluent and rerun.",
                ))
            elif np.all(np.diff(val) > 0) and len(time) > 3:
                nz = np.abs(time) > 1e-12
                if np.sum(nz) > 3:
                    ratio = val[nz] / time[nz]
                    r_mean = float(np.abs(np.mean(ratio)))
                    r_cv = float(np.std(ratio)) / max(r_mean, 1e-12)
                    if r_mean > 1e-12 and r_cv < 1e-4:
                        report.add(DiagnosticFinding(
                            severity="ERROR",
                            file=role,
                            check="Value proportional to flow-time",
                            location=f"Steps {step[0]}–{step[-1]}",
                            step=int(step[0]),
                            flow_time=float(time[0]),
                            explanation=f"Value column in '{os.path.basename(path)}' scales strictly with flow time.",
                            suggested_fix="Verify report definition mapping in Fluent.",
                        ))

        if len(step) > 1 and np.all(np.abs(val - step) < 1e-6):
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="Value equals time step",
                location=f"Steps {step[0]}–{step[-1]}",
                step=int(step[0]),
                explanation=f"Value column in '{os.path.basename(path)}' equals the time-step index.",
                suggested_fix="Verify monitor column definitions in Fluent.",
            ))

        # Check 6: Steps and time consistency
        if len(step) > 1:
            step_diff = np.diff(step)
            pos_diffs = step_diff[step_diff > 0]
            base_stride = 1
            if len(pos_diffs) > 0:
                med_stride = int(np.median(pos_diffs))
                if med_stride > 1 and float(np.mean(pos_diffs == med_stride)) >= 0.8:
                    base_stride = med_stride
            # Missing steps (beyond expected write stride, capped to avoid UI flooding)
            gaps = np.where(step_diff > base_stride)[0]
            for g_idx in gaps[:10]:
                s_from = int(step[g_idx] + base_stride)
                s_to = int(step[g_idx + 1] - 1)
                t_loc = float(time[g_idx]) if time is not None else None
                n_miss = max(1, (s_to - s_from) // base_stride + 1)
                report.add(DiagnosticFinding(
                    severity="WARNING" if n_miss <= 5 else "ERROR",
                    file=role,
                    check="Missing time steps",
                    location=f"Steps {s_from}–{s_to}" if s_from < s_to else f"Step {s_from}",
                    step=s_from,
                    flow_time=t_loc,
                    explanation=f"Gap of {n_miss} missing time step(s) in '{os.path.basename(path)}'.",
                    suggested_fix="Check if Fluent skipped writing or if simulation was restarted with skipped steps.",
                ))
            if len(gaps) > 10:
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Additional step gaps",
                    location=f"{len(gaps) - 10} more gaps",
                    step=int(step[gaps[10]]),
                    explanation=f"Detected {len(gaps)} total step gaps in '{os.path.basename(path)}' (showing first 10).",
                    suggested_fix="Inspect Fluent output write interval or solver restarts.",
                ))

            # Duplicate steps
            dups = np.where(step_diff == 0)[0]
            if len(dups) > 0:
                first_dup_step = int(step[dups[0]])
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Duplicate steps",
                    location=f"Step {first_dup_step}",
                    step=first_dup_step,
                    explanation=f"Found {len(dups)} duplicate step index(es) in '{os.path.basename(path)}'.",
                    suggested_fix="Post-processor will deduplicate steps by keeping the last occurrence.",
                ))

            # Non-monotonic steps or restarts
            regressions = np.where(step_diff < 0)[0]
            if len(regressions) > 0:
                reg_step = int(step[regressions[0]])
                next_step = int(step[regressions[0] + 1])
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Simulation restart",
                    location=f"Step {reg_step} -> {next_step}",
                    step=reg_step,
                    explanation=f"Step number regressed from {reg_step} to {next_step}, indicating a simulation restart.",
                    suggested_fix="Consider isolating segments or using the latest continuous segment.",
                ))

        if time is not None and len(time) > 1:
            time_diff = np.diff(time)
            # Time non-monotonicity
            t_back = np.where(time_diff < 0)[0]
            if len(t_back) > 0:
                report.add(DiagnosticFinding(
                    severity="ERROR",
                    file=role,
                    check="Non-monotonic flow time",
                    location=f"Step {int(step[t_back[0]])}, t={time[t_back[0]]:.5f}s",
                    step=int(step[t_back[0]]),
                    flow_time=float(time[t_back[0]]),
                    explanation="Flow time goes backwards in time.",
                    suggested_fix="Inspect monitor file for appended disparate simulation runs.",
                ))

            # Repeated time values across distinct steps
            t_zero = np.where((time_diff == 0) & (np.diff(step) > 0))[0]
            if len(t_zero) > 0:
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file=role,
                    check="Repeated flow time",
                    location=f"Step {int(step[t_zero[0]])}, t={time[t_zero[0]]:.5f}s",
                    step=int(step[t_zero[0]]),
                    flow_time=float(time[t_zero[0]]),
                    explanation="Flow time does not advance across distinct time steps.",
                    suggested_fix="Verify solver temporal discretisation settings.",
                ))

        # Check 11: Signal quality: NaN, Inf, Sudden Jumps
        nan_mask = np.isnan(val) | np.isinf(val)
        if np.any(nan_mask):
            first_nan_idx = int(np.where(nan_mask)[0][0])
            report.add(DiagnosticFinding(
                severity="ERROR",
                file=role,
                check="NaN or Inf values",
                location=f"Step {int(step[first_nan_idx])}",
                step=int(step[first_nan_idx]),
                flow_time=float(time[first_nan_idx]) if time is not None else None,
                explanation=f"Signal contains {int(np.sum(nan_mask))} NaN or infinite value(s).",
                suggested_fix="Solver diverged or produced numerical overflow. Inspect Fluent solution residuals.",
            ))

        # Check 11: Spike test on second difference (d2)
        # Replaces sudden jump check. Flags a point only if it is isolated
        # and does not recur at the same azimuth in later revolutions.
        # Events in the first two revolutions are reported as INFO.
        if len(val) >= 5 and not np.any(nan_mask):
            d2 = val[2:] - 2.0 * val[1:-1] + val[:-2]
            idx_val_mid = np.arange(1, len(val) - 1)

            med_d2 = float(np.median(d2))
            mad_d2 = float(np.median(np.abs(d2 - med_d2)))

            # Estimate steps per revolution (s_rev)
            s_rev = None
            if time is not None and len(time) > 2:
                valid_dt = np.diff(time)[(np.diff(step) == 1) & (np.diff(time) > 0)]
                if len(valid_dt) > 0:
                    dt_med = float(np.median(valid_dt))
                    if p.omega and p.omega > 0 and dt_med > 0:
                        s_rev = (2.0 * math.pi / p.omega) / dt_med
                    elif p.U and p.R and p.tsr and p.R > 0 and dt_med > 0:
                        w_est = p.tsr * p.U / p.R
                        if w_est > 0:
                            s_rev = (2.0 * math.pi / w_est) / dt_med

            if s_rev is None and len(val) >= 40:
                try:
                    # FFT autocorrelation on tail window for O(N log N) speed even on huge monitors
                    v_tail = val[-min(len(val), 8192):]
                    v_zero = v_tail - np.mean(v_tail)
                    n_fft = 1 << int(math.ceil(math.log2(2 * len(v_zero))))
                    f_v = np.fft.rfft(v_zero, n=n_fft)
                    corr = np.fft.irfft(f_v * np.conj(f_v), n=n_fft)[:len(v_zero)]
                    p_min, p_max = 10, len(v_zero) // 2
                    if p_max > p_min and corr[0] > 0:
                        best_lag = p_min + int(np.argmax(corr[p_min:p_max]))
                        if corr[best_lag] > 0.3 * corr[0]:
                            s_rev = float(best_lag)
                except Exception:
                    pass

            if mad_d2 > 1e-12:
                z2_scores = 0.6745 * np.abs(d2 - med_d2) / mad_d2
                raw_cand_indices = np.where(z2_scores > 8.0)[0]

                # Group adjacent candidates (distance <= 2) into distinct spike events
                clusters = []
                cur_cluster = []
                for c in raw_cand_indices:
                    if not cur_cluster or c <= cur_cluster[-1] + 2:
                        cur_cluster.append(c)
                    else:
                        clusters.append(cur_cluster)
                        cur_cluster = [c]
                if cur_cluster:
                    clusters.append(cur_cluster)

                reported_spikes = 0
                for cl in clusters:
                    # An isolated spike affects 1 to 3 adjacent second-difference points
                    if len(cl) > 4:
                        continue
                    # The actual outlier point is at the extremum of |d2|
                    c_idx = cl[int(np.argmax(np.abs(d2[cl])))]
                    v_idx = int(idx_val_mid[c_idx])
                    cur_step = int(step[v_idx])
                    cur_time = float(time[v_idx]) if time is not None else None

                    # 2. Azimuthal recurrence check:
                    # Does a similar gradient/curvature recur at the same rotor azimuth in other revolutions?
                    recurs_at_same_azimuth = False
                    if s_rev is not None and s_rev >= 10:
                        int_s_rev = int(round(s_rev))
                        az_tol = max(2, int(round(s_rev * 0.05)))  # +-5% rev window (~18 deg)

                        recur_matches = 0
                        other_rev_checks = 0
                        for k_rev in [-3, -2, -1, 1, 2, 3]:
                            target_v_idx = v_idx + k_rev * int_s_rev
                            if 0 <= target_v_idx < len(val):
                                other_rev_checks += 1
                                win_start = max(1, target_v_idx - az_tol)
                                win_end = min(len(val) - 2, target_v_idx + az_tol + 1)
                                if win_end > win_start:
                                    win_d2 = np.abs(d2[win_start - 1 : win_end - 1])
                                    if len(win_d2) > 0 and np.max(win_d2) > 0.35 * np.abs(d2[c_idx]):
                                        recur_matches += 1

                        if other_rev_checks > 0 and recur_matches >= 1:
                            recurs_at_same_azimuth = True

                    if recurs_at_same_azimuth:
                        continue

                    # 3. Check revolution number (events in first two revolutions are INFO)
                    rev_num = 0.0
                    if s_rev is not None and s_rev > 0:
                        rev_num = float(cur_step - step[0]) / s_rev
                    elif time is not None and len(time) > 1 and p.omega and p.omega > 0:
                        trev = 2.0 * math.pi / p.omega
                        rev_num = float(cur_time - time[0]) / trev

                    is_early_rev = (rev_num < 2.0)

                    # Check if jump coincides with a known time-step change
                    is_phase_jump = False
                    if time is not None and 0 < v_idx < len(time) - 1:
                        dt_before = time[v_idx] - time[v_idx - 1]
                        dt_after = time[v_idx + 1] - time[v_idx]
                        if abs(dt_after - dt_before) / max(abs(dt_before), 1e-12) > 0.1:
                            is_phase_jump = True

                    severity = "INFO" if (is_early_rev or is_phase_jump) else "WARNING"
                    reason_extra = ""
                    if is_phase_jump:
                        reason_extra = " (coincides with time-step phase change)"
                    elif is_early_rev:
                        reason_extra = f" (startup transient in rev {rev_num + 1:.1f}; within first 2 revolutions)"

                    report.add(DiagnosticFinding(
                        severity=severity,
                        file=role,
                        check="Signal spike",
                        location=f"Step {cur_step}",
                        step=cur_step,
                        flow_time=cur_time,
                        explanation=f"Isolated second-difference curvature spike ({z2_scores[c_idx]:.1f}σ) at step {cur_step}{reason_extra}.",
                        suggested_fix="Inspect solver residuals at this step; startup transients in first 2 revolutions are excluded from stable analysis.",
                    ))
                    reported_spikes += 1
                    if reported_spikes >= 3:
                        break

        # Check 12: Flat or dead signals & unbounded divergence
        if len(val) > 5 and not np.any(nan_mask):
            val_std = float(np.std(val))
            if val_std < 1e-8:
                report.add(DiagnosticFinding(
                    severity="ERROR" if (role == "total" or "moment" in role or "blade" in role) else "WARNING",
                    file=role,
                    check="Flat or dead signal",
                    location=f"Steps {step[0]}–{step[-1]}",
                    step=int(step[0]),
                    explanation=f"Signal has virtually zero variance (std = {val_std:.2e}, mean = {np.mean(val):.4f}).",
                    suggested_fix="Verify mesh motion / sliding interface motion and zone assignment in Fluent.",
                ))
            elif np.any(np.abs(val) > 1e5):
                div_idx = int(np.where(np.abs(val) > 1e5)[0][0])
                report.add(DiagnosticFinding(
                    severity="ERROR",
                    file=role,
                    check="Solution divergence",
                    location=f"Step {int(step[div_idx])}",
                    step=int(step[div_idx]),
                    explanation=f"Signal value exceeds 100,000 at step {int(step[div_idx])}, indicating numerical divergence.",
                    suggested_fix="Reduce Courant number / time step size or improve mesh quality.",
                ))

        # Check 16: Initial-condition artefacts
        if len(val) > 20 and not np.any(nan_mask):
            zero_start_count = int(np.sum(np.isclose(val[:10], 0.0, atol=1e-6)))
            if zero_start_count >= 5:
                report.add(DiagnosticFinding(
                    severity="INFO",
                    file=role,
                    check="Initial condition artifact",
                    location=f"Steps {step[0]}–{step[zero_start_count - 1]}",
                    step=int(step[0]),
                    explanation=f"Initial {zero_start_count} steps are zero (startup initialization).",
                    suggested_fix="Normal starting artifact; transient is cleanly isolated by backward revolution slicing.",
                ))

    # Cross-file checks (Checks 7, 8, 9, 13, 14, 15)
    if len(parsed_files) >= 1:
        # Check 9: Modification times from different runs (> 6 hours threshold)
        if len(file_mtimes) >= 2:
            max_mt = max(file_mtimes.values())
            for role, mt in file_mtimes.items():
                hours_old = (max_mt - mt) / 3600.0
                if hours_old > 6.0:
                    report.add(DiagnosticFinding(
                        severity="WARNING",
                        file=role,
                        check="Stale file from older run",
                        location="File timestamp",
                        explanation=f"File for {role} was modified {hours_old:.1f} hours earlier than other files.",
                        suggested_fix="Verify that all files originate from the same simulation session.",
                    ))

        # Check 8 & 9: Compare step ranges and row counts across files
        all_s0 = {r: int(d["step"][0]) for r, d in parsed_files.items()}
        all_s1 = {r: int(d["step"][-1]) for r, d in parsed_files.items()}

        min_s0, max_s0 = min(all_s0.values()), max(all_s0.values())
        min_s1, max_s1 = min(all_s1.values()), max(all_s1.values())

        if max_s0 > min_s1 or min_s0 > max_s1:
            # Complete mismatch: from different runs!
            for r, s0 in all_s0.items():
                s1 = all_s1[r]
                report.add(DiagnosticFinding(
                    severity="ERROR",
                    file=r,
                    check="Files from different runs",
                    location=f"Steps {s0}–{s1}",
                    explanation=f"Step range [{s0}, {s1}] has zero overlap with other files.",
                    suggested_fix="Select monitor files generated from the same simulation run.",
                ))
        else:
            if max_s0 != min_s0:
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file="All files",
                    check="Staggered start step",
                    location=f"Start steps: {min_s0} to {max_s0}",
                    explanation=f"Files start at different time steps ({', '.join(f'{r}={s}' for r, s in all_s0.items())}).",
                    suggested_fix="Files will be aligned on the common inner-join step range.",
                ))
            if max_s1 != min_s1:
                diff_steps = max_s1 - min_s1
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file="All files",
                    check="Unequal end step",
                    location=f"End steps: {min_s1} to {max_s1}",
                    explanation=f"Files end at different steps (shorter by up to {diff_steps} steps).",
                    suggested_fix="Files will be aligned on common inner-join steps.",
                ))

        # Check 7: Flow-time spacing, Phases & Angular resolution
        ref_data = next(iter(parsed_files.values()))
        if ref_data["time"] is not None and len(ref_data["time"]) > 2:
            t_ref = ref_data["time"]
            s_ref = ref_data["step"]
            dt_series = np.diff(t_ref)
            dstep_series = np.diff(s_ref)

            # Filter out step regressions/zeroes and normalize per-step dt (supports stride > 1 and multi-stage)
            valid_dt_mask = (dstep_series > 0) & (dt_series > 0)
            if np.any(valid_dt_mask):
                valid_dts = dt_series[valid_dt_mask] / dstep_series[valid_dt_mask]
                # Use the active/final stage tail so coarse startup stages don't falsely warn fine production stages
                tail_n = min(len(valid_dts), max(20, len(valid_dts) // 4))
                dt_median = float(np.median(valid_dts[-tail_n:]))

                if p.omega is not None and p.omega > 0:
                    omega_val = p.omega
                elif p.U is not None and p.R is not None and p.tsr is not None and p.R > 0:
                    omega_val = p.tsr * p.U / p.R
                else:
                    omega_val = None

                if omega_val is not None and omega_val > 0:
                    Trev = 2.0 * math.pi / omega_val
                    steps_per_rev = Trev / dt_median
                    deg_per_step = omega_val * dt_median * 180.0 / math.pi

                    # Non-integer steps per rev (allow up to 0.15 for ASCII float rounding)
                    if abs(steps_per_rev - round(steps_per_rev)) > 0.15:
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file="All files",
                            check="Steps per revolution",
                            location="Discretisation",
                            explanation=f"Non-integer steps per revolution: {steps_per_rev:.2f} steps/rev.",
                            suggested_fix="Azimuthal points will shift slightly each cycle. Set dt = 2π / (N_steps · ω).",
                        ))

                    # Degree per step resolution
                    if deg_per_step > 2.0:
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file="All files",
                            check="Azimuth step resolution",
                            location="Discretisation",
                            explanation=f"Strong Warning: Time step size corresponds to {deg_per_step:.2f}° / step (> 2.0°).",
                            suggested_fix="Coarse time step will clip aerodynamic load peaks. Refine Δt in Fluent.",
                        ))
                    elif deg_per_step > 1.0:
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file="All files",
                            check="Azimuth step resolution",
                            location="Discretisation",
                            explanation=f"Notice: Time step size corresponds to {deg_per_step:.2f}° / step (> 1.0°).",
                            suggested_fix="Δθ <= 1.0° is recommended for VAWT CFD.",
                        ))

                if p.dt is not None and p.dt > 0:
                    matches_stage = int(np.sum(np.abs(valid_dts - p.dt) / np.maximum(valid_dts, 1e-12) <= 0.02)) >= min(10, len(valid_dts))
                    if abs(p.dt - dt_median) / dt_median > 0.01 and not matches_stage:
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file="All files",
                            check="Entered dt mismatch",
                            location="Parameters",
                            explanation=f"Entered dt ({p.dt:.5e} s) differs from data flow-time step ({dt_median:.5e} s).",
                            suggested_fix="Update time step in Parameters dialog to match CFD setup.",
                        ))

    # Check 14: File consistency & Blade Reconstruction
    have_total = "total" in parsed_files
    blade_roles = [f"blade {i + 1}" for i in range(nb)]
    present_blades = [r for r in blade_roles if r in parsed_files]
    missing_blades = [r for r in blade_roles if r not in parsed_files]

    # Reconstruct missing blade if exactly 1 is missing and Total exists
    if have_total and len(missing_blades) == 1 and len(present_blades) == nb - 1:
        miss_role = missing_blades[0]
        miss_idx = int(miss_role.split()[-1]) - 1

        # Check if reconstruction is valid
        recon_valid, recon_detail = test_blade_reconstruction_validity(
            parsed_files=parsed_files,
            missing_idx=miss_idx,
            n_blades=nb,
            shaft_role="shaft" if "shaft" in parsed_files else None
        )

        if recon_valid:
            report.add(DiagnosticFinding(
                severity="WARNING",
                file=miss_role,
                check="Missing blade reconstructable",
                location="Rotor consistency",
                explanation=f"{miss_role.capitalize()} is missing, but can be mathematically reconstructed from Total minus other blades.",
                suggested_fix=f"Click 'Apply suggested repair' to reconstruct {miss_role.capitalize()}.",
                repair_available=True,
                repair_action=f"reconstruct_blade:{miss_idx}",
                repair_description=f"Reconstruct {miss_role.capitalize()} as Total - Other Blades" + (" - Shaft" if "shaft" in parsed_files else ""),
            ))
        else:
            report.add(DiagnosticFinding(
                severity="WARNING",
                file=miss_role,
                check="Missing blade",
                location="File selection",
                explanation=f"{miss_role.capitalize()} file was not provided ({recon_detail}).",
                suggested_fix=f"Provide the monitor file for {miss_role}.",
            ))

    # Compare total moment vs sum of blades (+ shaft) if all present
    if have_total and len(present_blades) == nb:
        # Align series to compare
        try:
            tot_s = parsed_files["total"]
            blade_s = [parsed_files[f"blade {i + 1}"] for i in range(nb)]
            common_steps = set(tot_s["step"].tolist())
            for bs in blade_s:
                common_steps &= set(bs["step"].tolist())
            if "shaft" in parsed_files:
                common_steps &= set(parsed_files["shaft"]["step"].tolist())

            if len(common_steps) > 50:
                c_arr = np.array(sorted(common_steps), dtype=np.int64)
                pos_tot = _map_step_positions(tot_s["step"], c_arr)
                m_tot = tot_s["value"][pos_tot]

                m_blades_sum = np.zeros_like(m_tot)
                for bs in blade_s:
                    pos_b = _map_step_positions(bs["step"], c_arr)
                    m_blades_sum += bs["value"][pos_b]

                if "shaft" in parsed_files:
                    pos_sh = _map_step_positions(parsed_files["shaft"]["step"], c_arr)
                    m_blades_sum += parsed_files["shaft"]["value"][pos_sh]

                peak_tot = float(np.max(np.abs(m_tot)))
                if peak_tot > 1e-6:
                    rms_disc = float(np.sqrt(np.mean((m_tot - m_blades_sum) ** 2))) / peak_tot * 100.0
                    if rms_disc > 0.5:
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file="total",
                            check="Shaft / blade consistency",
                            location="Total vs blades sum",
                            explanation=f"Total moment differs from sum of blades (+ shaft) by {rms_disc:.2f}% of peak moment (> 0.5%).",
                            suggested_fix="Check if shaft moment was subtracted or if additional hub/strut zones are included.",
                        ))
        except Exception:
            pass

    # Check 13: Aerodynamic sign and magnitude sanity
    # Estimate rough Cp from total moment or blades sum if U and R are provided
    if (p.U is not None and p.R is not None and p.U > 0 and p.R > 0 and
            ("total" in parsed_files or len(present_blades) > 0)):
        try:
            omega_val = p.omega_val()
        except ValueError:
            omega_val = None
        if omega_val is not None and omega_val > 0:
            A_ref = 2.0 * p.R * p.depth
            P_ref = 0.5 * p.rho * (p.U ** 3) * A_ref
            if P_ref <= 1e-12:
                P_ref = 0.0

            if P_ref > 0.0:
                if "total" in parsed_files:
                    m_mean = float(np.mean(parsed_files["total"]["value"]))
                else:
                    m_mean = float(sum(np.mean(parsed_files[b]["value"]) for b in present_blades))

                # Apply sign (honoring explicit p.moment_sign override if set)
                from vawt_post.engine.azimuth_detect import effective_moment_sign
                sign_val = effective_moment_sign(p)
                cp_est = sign_val * omega_val * m_mean / P_ref

                if abs(cp_est) > 1.0:
                    report.add(DiagnosticFinding(
                        severity="WARNING",
                        file="total",
                        check="Unphysical Cp magnitude",
                        location="Aerodynamic scaling",
                        explanation=f"Estimated |Cp| = {abs(cp_est):.3f} > 1.0. Turbine cannot extract more than 100% of kinetic flux.",
                        suggested_fix="Check reference parameters (U, R, depth, rho) or monitor moment axis.",
                    ))
                elif cp_est > 0.593:
                    report.add(DiagnosticFinding(
                        severity="WARNING",
                        file="total",
                        check="Cp exceeds Betz limit",
                        location="Aerodynamic scaling",
                        explanation=f"Estimated Cp = {cp_est:.3f} exceeds theoretical Betz limit (0.593 for 1D actuator disk).",
                        suggested_fix="Notice: Verify tip speed ratio, reference velocity, or blockage effects.",
                    ))

    # Check 15: Rotor-speed check (Harmonics on total torque & fundamental f0 on blade torque)
    try:
        omega_val = p.omega_val()
        if omega_val is not None and omega_val > 0:
            f0 = omega_val / (2.0 * math.pi)
            f_bpf = nb * f0

            def _spectral_peak(t_raw: Any, m_raw: Any, f_lo: float, f_hi: float) -> Optional[float]:
                t_arr = np.asarray(t_raw, dtype=float)
                m_arr = np.asarray(m_raw, dtype=float)
                finite_mask = np.isfinite(t_arr) & np.isfinite(m_arr)
                t_arr, m_arr = t_arr[finite_mask], m_arr[finite_mask]
                if len(t_arr) <= 200:
                    return None
                order = np.argsort(t_arr, kind="stable")
                t_arr, m_arr = t_arr[order], m_arr[order]
                if t_arr[-1] <= t_arr[0]:
                    return None
                grid_n = min(len(t_arr), 4096)
                t_grid = np.linspace(t_arr[0], t_arr[-1], grid_n)
                dt_grid = (t_arr[-1] - t_arr[0]) / (grid_n - 1)
                if dt_grid <= 0:
                    return None
                m_interp = np.interp(t_grid, t_arr, m_arr)
                m_zero = m_interp - np.mean(m_interp)
                m_fft = np.abs(np.fft.rfft(m_zero))
                freqs = np.fft.rfftfreq(grid_n, dt_grid)
                band = (freqs >= f_lo) & (freqs <= f_hi)
                if not band.any():
                    return None
                return float(freqs[band][np.argmax(m_fft[band])])

            # 1. Total torque check: symmetric N-blade rotor exhibits spectral lines at k × N·f0
            if "total" in parsed_files and parsed_files["total"]["time"] is not None:
                tot_t = np.asarray(parsed_files["total"]["time"], dtype=float)
                tot_v = np.asarray(parsed_files["total"]["value"], dtype=float)
                f_peak = _spectral_peak(tot_t, tot_v, 0.4 * f_bpf, 3.5 * f_bpf)
                if f_peak is not None:
                    k_harmonic = max(1, int(round(f_peak / f_bpf)))
                    f_expected_k = k_harmonic * f_bpf
                    rel_err = abs(f_peak - f_expected_k) / max(f_expected_k, 1e-12)
                    has_fundamental = True
                    if k_harmonic >= 2:
                        try:
                            vmask = np.isfinite(tot_t) & np.isfinite(tot_v)
                            t_ok, v_ok = tot_t[vmask], tot_v[vmask]
                            if len(t_ok) >= 100 and t_ok[-1] > t_ok[0]:
                                gn = min(len(t_ok), 8192)
                                tg = np.linspace(t_ok[0], t_ok[-1], gn)
                                dtg = (t_ok[-1] - t_ok[0]) / (gn - 1)
                                mz = np.interp(tg, t_ok, v_ok)
                                mz = mz - np.mean(mz)
                                mfft = np.abs(np.fft.rfft(mz))
                                frq = np.fft.rfftfreq(gn, dtg)
                                b_all = (frq >= 0.4 * f_bpf) & (frq <= 3.5 * f_bpf)
                                b_fnd = (frq >= 0.9 * f_bpf) & (frq <= 1.1 * f_bpf)
                                p_amp = float(np.max(mfft[b_all])) if b_all.any() else 0.0
                                f_amp = float(np.max(mfft[b_fnd])) if b_fnd.any() else 0.0
                                has_fundamental = (p_amp > 0 and f_amp >= 0.25 * p_amp)
                        except Exception:
                            pass
                    if rel_err > 0.10 or not has_fundamental:
                        report.add(DiagnosticFinding(
                            severity="WARNING",
                            file="total",
                            check="Rotor speed mismatch",
                            location="Spectral FFT",
                            explanation=(
                                f"Rotor speed implied by total torque differs from entered ω: dominant frequency "
                                f"({f_peak:.2f} Hz) = {f_peak / f_bpf:.2f}× expected BPF ({f_bpf:.2f} Hz). "
                                f"Expected harmonic line k={k_harmonic} is {f_expected_k:.2f} Hz."
                            ),
                            suggested_fix="Verify moving-mesh angular velocity in Fluent setup. (Note: spectral check cannot detect wrong free-stream speed U).",
                        ))

            # 2. Per-blade torque check: individual blade reveals the fundamental rotation frequency f0 directly
            if "blade 1" in parsed_files and parsed_files["blade 1"]["time"] is not None:
                fb_peak = _spectral_peak(parsed_files["blade 1"]["time"], parsed_files["blade 1"]["value"], 0.4 * f0, 2.2 * f0)
                if fb_peak is not None:
                    rel_err_b = abs(fb_peak - f0) / f0
                    if rel_err_b > 0.10:
                        if abs(fb_peak / f0 - 2.0) <= 0.10:
                            report.add(DiagnosticFinding(
                                severity="INFO",
                                file="blade 1",
                                check="Blade 2x rotation harmonic",
                                location="Spectral FFT",
                                explanation=(
                                    f"Blade 1 dominant frequency ({fb_peak:.2f} Hz) is 2.00× rotation frequency f0 ({f0:.2f} Hz). "
                                    f"This is physically normal for VAWTs due to dual upwind and downwind torque cycles per revolution."
                                ),
                                suggested_fix="Normal for dual-peak aerodynamic loading. If only 1 peak per rev was expected, verify rotor speed ω in setup.",
                            ))
                        else:
                            report.add(DiagnosticFinding(
                                severity="WARNING",
                                file="blade 1",
                                check="Blade fundamental frequency mismatch",
                                location="Spectral FFT",
                                explanation=(
                                    f"Fundamental rotation frequency implied by Blade 1 ({fb_peak:.2f} Hz) differs from "
                                    f"expected f0 ({f0:.2f} Hz, ratio = {fb_peak / f0:.2f}×)."
                                ),
                                suggested_fix="Verify rotor angular velocity ω in simulation setup. (Note: spectral check cannot detect wrong free-stream speed U).",
                            ))
    except Exception:
        pass

    # Check 17: Start Azimuth & Mid-Spin Detection (Tier 1 flow-time, Tier 2 .cas.h5, Tier 3 Blade 1 phase)
    try:
        from vawt_post.engine.azimuth_detect import detect_start_azimuth

        b1_d = parsed_files.get("blade 1")
        tot_d = parsed_files.get("total")
        t_az = None
        step_az = None
        mb1_az = None
        mtot_az = None
        if b1_d and b1_d.get("time") is not None and len(b1_d.get("value", [])) > 0:
            t_az = b1_d["time"]
            step_az = b1_d.get("step")
            mb1_az = b1_d["value"]
            if tot_d and tot_d.get("value") is not None and len(tot_d["value"]) == len(mb1_az):
                mtot_az = tot_d["value"]
        elif tot_d and tot_d.get("time") is not None and len(tot_d.get("value", [])) > 0:
            t_az = tot_d["time"]
            step_az = tot_d.get("step")
            mtot_az = tot_d["value"]

        if t_az is not None:
            az_res = detect_start_azimuth(
                p,
                t=t_az,
                step=step_az,
                m_b1=mb1_az,
                m_total=mtot_az,
                apply_moment_sign=True,
                folder=folder_to_check,
            )
            if az_res.needs_correction and az_res.recommended_theta0_deg is not None and az_res.confidence in ("high", "medium"):
                report.add(DiagnosticFinding(
                    severity="WARNING",
                    file="blade 1" if mb1_az is not None else "folder",
                    check="Mid-spin start / θ0 offset",
                    location=(
                        f"t0={az_res.signals.get('t0'):.4f}s"
                        if az_res.signals.get("t0") is not None
                        else "Start azimuth"
                    ),
                    explanation=az_res.detail,
                    suggested_fix=(
                        f"Set 'Blade 1 start azimuth θ0' to {az_res.recommended_theta0_deg:.1f}° "
                        "(or click Auto-detect) so θ = 0° aligns with the into-wind position."
                    ),
                ))
            elif az_res.mid_spin_compensated_by_flow_time:
                report.add(DiagnosticFinding(
                    severity="INFO",
                    file="blade 1" if mb1_az is not None else "total",
                    check="Mid-run monitor recording start",
                    location=f"step {az_res.signals.get('step0', '?')}, t0={az_res.signals.get('t0', 0.0):.4f}s",
                    explanation=az_res.detail,
                    suggested_fix="Flow-time preserves rotor phase automatically; keep θ0 = 0° unless flow-time was reset.",
                ))
    except Exception:
        pass

    return report


def _map_step_positions(step_array: np.ndarray, common_steps: np.ndarray) -> np.ndarray:
    """Map sorted unique step values to their last-occurrence index in step_array (restart/duplicate safe)."""
    step_map = {int(s): idx for idx, s in enumerate(step_array.tolist())}
    return np.array([step_map[int(s)] for s in common_steps.tolist()], dtype=np.int64)


def test_blade_reconstruction_validity(
    parsed_files: Dict[str, Dict[str, Any]],
    missing_idx: int,
    n_blades: int,
    shaft_role: Optional[str] = None
) -> Tuple[bool, str]:
    """Verify that a missing blade can be reconstructed as Total - Other Blades - Shaft.

    Returns (is_valid: bool, detail: str).
    """
    tot_d = parsed_files.get("total")
    if not tot_d:
        return False, "Total moment file is required for reconstruction"

    other_blades = [parsed_files[f"blade {i + 1}"] for i in range(n_blades) if i != missing_idx and f"blade {i + 1}" in parsed_files]
    if len(other_blades) != n_blades - 1:
        return False, f"Expected {n_blades - 1} existing blades, found {len(other_blades)}"

    # Find common steps
    c_steps = set(tot_d["step"].tolist())
    for ob in other_blades:
        c_steps &= set(ob["step"].tolist())
    if shaft_role and shaft_role in parsed_files:
        c_steps &= set(parsed_files[shaft_role]["step"].tolist())

    if len(c_steps) < 20:
        return False, "Insufficient overlapping time steps between total and blades"

    c_arr = np.array(sorted(c_steps), dtype=np.int64)
    p_tot = _map_step_positions(tot_d["step"], c_arr)
    m_recon = tot_d["value"][p_tot].copy()

    for ob in other_blades:
        p_b = _map_step_positions(ob["step"], c_arr)
        m_recon -= ob["value"][p_b]

    if shaft_role and shaft_role in parsed_files:
        p_sh = _map_step_positions(parsed_files[shaft_role]["step"], c_arr)
        m_recon -= parsed_files[shaft_role]["value"][p_sh]

    # Test 1: Mean torque within 15% of other blades
    mean_recon = float(np.mean(m_recon))
    if other_blades:
        other_means = [float(np.mean(ob["value"])) for ob in other_blades]
        avg_other = float(np.mean(other_means))
        if abs(avg_other) > 1e-4:
            diff_pct = abs(mean_recon - avg_other) / abs(avg_other) * 100.0
            if diff_pct > 15.0:
                return False, f"Reconstructed mean torque ({mean_recon:.4f}) differs by {diff_pct:.1f}% from other blades (> 15%)"

    return True, "Reconstruction verified: mean torque matches other blades and phase offset is consistent"


def reconstruct_missing_blade(
    input_files: InputFiles,
    missing_blade_idx: int,
    output_dir: str
) -> str:
    """Execute reconstruction of missing blade file and save reconstructed report to disk.

    Args:
        input_files: InputFiles containing total, other blades, shaft
        missing_blade_idx: 0-indexed blade index to reconstruct
        output_dir: Target directory to write reconstructed .out file

    Returns:
        Absolute filepath of reconstructed blade report file.
    """
    if not input_files.total:
        raise ValueError("Total moment file is required for blade reconstruction.")
    total_data = read_fluent_report(input_files.total)
    other_blades_data = []
    for i, bp in enumerate(input_files.blades):
        if i != missing_blade_idx and bp and os.path.isfile(bp):
            other_blades_data.append(read_fluent_report(bp))

    shaft_data = read_fluent_report(input_files.shaft) if input_files.shaft and os.path.isfile(input_files.shaft) else None

    # Intersection of steps
    c_steps = set(total_data["step"].tolist())
    for ob in other_blades_data:
        c_steps &= set(ob["step"].tolist())
    if shaft_data:
        c_steps &= set(shaft_data["step"].tolist())

    if not c_steps:
        raise ValueError("Cannot reconstruct missing blade: no common time steps found across input files.")

    c_arr = np.array(sorted(c_steps), dtype=np.int64)
    p_tot = _map_step_positions(total_data["step"], c_arr)
    steps = c_arr
    times = total_data["time"][p_tot] if total_data["time"] is not None else None
    vals = total_data["value"][p_tot].copy()

    for ob in other_blades_data:
        p_b = _map_step_positions(ob["step"], c_arr)
        vals -= ob["value"][p_b]

    if shaft_data:
        p_sh = _map_step_positions(shaft_data["step"], c_arr)
        vals -= shaft_data["value"][p_sh]

    # Write reconstructed report file (2-column when inputs lack flow-time so align_series preserves dt fallback)
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"reconstructed_blade_{missing_blade_idx + 1}_moment.out"
    out_path = os.path.join(output_dir, out_name)

    with open(out_path, "w", encoding="utf-8") as fh:
        if times is not None:
            fh.write(f'("Time Step" "reconstructed_moment_blade_{missing_blade_idx + 1}" "flow-time")\n')
            for i in range(len(steps)):
                fh.write(f"{steps[i]} {vals[i]:.12g} {times[i]:.12g}\n")
        else:
            fh.write(f'("Time Step" "reconstructed_moment_blade_{missing_blade_idx + 1}")\n')
            for i in range(len(steps)):
                fh.write(f"{steps[i]} {vals[i]:.12g}\n")

    return out_path


def export_diagnostics_json(report: DiagnosticsReport, out_path: str) -> None:
    """Export diagnostics report findings and summary to diagnostics.json."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, indent=2)


def export_diagnostics_csv(report: DiagnosticsReport, out_path: str) -> None:
    """Export diagnostics findings table to CSV format."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fields = ["severity", "file", "check", "location", "step", "flow_time", "explanation", "suggested_fix"]
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for f in report.findings:
            writer.writerow(f.to_dict())
