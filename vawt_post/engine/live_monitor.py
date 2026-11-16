# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Incremental Live Monitoring Engine
===========================================================
Watches ANSYS Fluent monitor files in real-time while the simulation is actively
running. Features:
- Byte-offset incremental parsing (reads only new appended bytes, never reparses from start).
- Incomplete trailing line buffering.
- Inter-file step synchronization up to the latest common step.
- Stall detection (default 60s timeout).
- Incremental revolution windowing and KPI calculation.
- Decimated time-series for high-performance non-blocking UI rendering.
- Headless, pure Python & NumPy worker thread; GUI communicates exclusively via Queue.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from vawt_post.engine.config import Params, InputFiles, STAGE_DT_CHANGE_REL_TOL
from vawt_post.engine.parser import inspect_fluent_report
from vawt_post.engine.analysis import window_mean


@dataclass
class LiveFileStats:
    role: str
    filename: str
    status_color: str  # "green", "amber", "red", "gray"
    row_count: int = 0
    last_step: int = 0
    last_flow_time: float = 0.0
    age_seconds: float = 0.0
    open_diagnostics: int = 0
    error_message: Optional[str] = None


@dataclass
class LiveMonitorSnapshot:
    status: str  # "Waiting", "Monitoring", "Paused", "Stalled", "Finished", "Error"
    is_full_refresh: bool = True
    current_step: int = 0
    current_time: float = 0.0
    dt_seconds: Optional[float] = None
    total_revs: float = 0.0
    current_azimuth: float = 0.0
    steps_per_sec: float = 0.0
    target_revs: Optional[float] = None
    eta_seconds: Optional[float] = None
    last_data_age_s: float = 0.0
    last_refresh_wall_time: float = 0.0
    file_stats: Dict[str, LiveFileStats] = field(default_factory=dict)
    kpi: Dict[str, Any] = field(default_factory=dict)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    timeseries: Dict[str, Any] = field(default_factory=dict)
    rev_history: List[Dict[str, Any]] = field(default_factory=list)
    azimuth_curve: Dict[str, Any] = field(default_factory=dict)
    heat_map: Optional[np.ndarray] = None
    spectrum: Dict[str, Any] = field(default_factory=dict)
    kinematics: Dict[str, Any] = field(default_factory=dict)
    blade_torques: Dict[str, Any] = field(default_factory=dict)
    forces_az: Dict[str, Any] = field(default_factory=dict)
    parameter_checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    diagnostics_summary: Dict[str, int] = field(default_factory=lambda: {"ERROR": 0, "WARNING": 0, "INFO": 0})
    stages: List[Dict[str, Any]] = field(default_factory=list)
    start_azimuth: Optional[Dict[str, Any]] = None
    continuity: Optional[Dict[str, Any]] = None


class IncrementalFileReader:
    """Manages byte-offset incremental reading and partial-line buffering for one file."""

    def __init__(self, path: str, role: str):
        self.path = path
        self.role = role
        self.byte_offset: int = 0
        self.buffer: str = ""
        self.last_mtime: float = 0.0
        self.last_size: int = 0
        self.columns: Optional[List[str]] = None
        self.step_col: int = 0
        self.time_col: Optional[int] = None
        self.val_col: int = 1
        self.is_valid: bool = True
        self.error_msg: Optional[str] = None
        self.has_missing_col_error: bool = False
        self.restart_detected: bool = False
        self.dt_fallback: Optional[float] = None

        # Accumulating raw parsed records
        self.raw_steps: List[int] = []
        self.raw_times: List[float] = []
        self.raw_vals: List[float] = []

        self._inspect_file_header()

    def _inspect_file_header(self) -> None:
        """Inspect file header using inspect_fluent_report to properly map column indices."""
        if not os.path.isfile(self.path):
            return
        try:
            info = inspect_fluent_report(self.path)
            self.columns = info.get("columns", [])
            self.step_col = info.get("step_col", 0)
            self.time_col = info.get("time_col")
            data_cols = info.get("data_columns", [])
            if data_cols:
                self.val_col = data_cols[0][0]
                self.has_missing_col_error = False
                self.is_valid = True
            else:
                self.has_missing_col_error = True
                self.error_msg = f"Missing value column in {self.role} report file"
                self.is_valid = False
        except Exception:
            pass

    def reset(self) -> None:
        """Reset state on file truncation or restart."""
        self.byte_offset = 0
        self.buffer = ""
        self.raw_steps.clear()
        self.raw_times.clear()
        self.raw_vals.clear()
        self.error_msg = None
        self.is_valid = True
        self._inspect_file_header()

    def read_new_chunk(self) -> Tuple[int, List[int], List[float], List[float]]:
        """Read newly appended bytes from the file.

        Returns (new_rows_count, new_steps, new_times, new_vals).
        """
        if not os.path.isfile(self.path):
            self.is_valid = False
            self.error_msg = "File does not exist"
            return 0, [], [], []

        try:
            st = os.stat(self.path)
            curr_size = st.st_size
            curr_mtime = st.st_mtime
            self.last_mtime = curr_mtime
        except Exception as e:
            self.is_valid = False
            self.error_msg = f"Cannot stat file: {e}"
            return 0, [], [], []

        # Detect file truncation or restart
        if curr_size < self.byte_offset:
            self.restart_detected = True
            self.reset()

        if curr_size == self.byte_offset:
            return 0, [], [], []

        # Read newly appended bytes
        new_bytes = b""
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.byte_offset)
                new_bytes = fh.read()
                self.byte_offset = fh.tell()
        except PermissionError:
            # Fluent is actively writing to file; skip this cycle without marking invalid
            return 0, [], [], []
        except OSError:
            # Transient Windows file lock; skip this cycle without marking invalid
            return 0, [], [], []
        except Exception as e:
            self.is_valid = False
            self.error_msg = f"Read error: {e}"
            return 0, [], [], []

        if not new_bytes:
            return 0, [], [], []

        # Decode text with fallback
        text = self.buffer + new_bytes.decode("utf-8", errors="ignore")
        lines = text.split("\n")

        # Incomplete trailing fragment stays in buffer
        self.buffer = lines[-1]
        completed_lines = lines[:-1]

        if not completed_lines:
            return 0, [], [], []

        if self.columns is None:
            self._inspect_file_header()

        new_steps: List[int] = []
        new_times: List[float] = []
        new_vals: List[float] = []
        inspected_on_non_numeric = False

        for line in completed_lines:
            s = line.strip()
            if not s:
                continue

            toks = s.replace("(", " ").replace(")", " ").replace(",", " ").split()
            try:
                nums = [float(x) for x in toks]
            except ValueError:
                # Non-numeric line: header or metadata
                if (not inspected_on_non_numeric) and (self.columns is None or self.has_missing_col_error):
                    self._inspect_file_header()
                    inspected_on_non_numeric = True
                continue

            if len(nums) < 2:
                continue

            # Fallback if no header was resolved
            if self.columns is None:
                self.columns = [f"Col_{i+1}" for i in range(len(nums))]
                self.step_col = 0
                if len(nums) >= 3:
                    self.val_col = 1
                    self.time_col = 2
                else:
                    self.val_col = 1
                    self.time_col = None

            step_idx = int(nums[self.step_col]) if self.step_col < len(nums) else int(nums[0])
            val_num = nums[self.val_col] if self.val_col < len(nums) else nums[1]
            time_num = nums[self.time_col] if (self.time_col is not None and self.time_col < len(nums)) else None

            # Detect step backward jump (appended restart without truncation)
            prev_step = new_steps[-1] if new_steps else (self.raw_steps[-1] if self.raw_steps else None)
            if prev_step is not None and step_idx < prev_step:
                self.restart_detected = True
                self.raw_steps.clear()
                self.raw_times.clear()
                self.raw_vals.clear()
                new_steps.clear()
                new_times.clear()
                new_vals.clear()
                self.error_msg = None
                self.is_valid = True

            # Handle step deduplication (repeated step index from partial line or iteration rewrite)
            if new_steps and step_idx == new_steps[-1]:
                new_vals[-1] = val_num
                if time_num is not None:
                    new_times[-1] = time_num
                continue
            elif not new_steps and self.raw_steps and step_idx == self.raw_steps[-1]:
                self.raw_vals[-1] = val_num
                if time_num is not None:
                    self.raw_times[-1] = time_num
                continue

            new_steps.append(step_idx)
            new_vals.append(val_num)
            new_times.append(time_num if time_num is not None else float(step_idx))

        if new_steps:
            self.raw_steps.extend(new_steps)
            self.raw_vals.extend(new_vals)
            self.raw_times.extend(new_times)

        return len(new_steps), new_steps, new_times, new_vals


class LiveMonitorEngine:
    """Core numerical state manager for incremental live monitoring."""

    def __init__(self, p: Params, f: InputFiles, watch_dir: Optional[str] = None):
        self.p = p
        self.f = f
        self.watch_dir = watch_dir
        self.detected_omega: Optional[float] = None
        self.detected_folder_params: Dict[str, Any] = {}
        if self.watch_dir and os.path.isdir(self.watch_dir):
            try:
                from vawt_post.engine.parser import detect_folder_simulation_parameters, resolve_fluent_directory
                rf = resolve_fluent_directory(self.watch_dir)
                self.detected_folder_params = detect_folder_simulation_parameters(rf)
                if self.detected_folder_params.get("omega"):
                    self.detected_omega = float(self.detected_folder_params["omega"])
            except Exception:
                pass
        self.readers: Dict[str, IncrementalFileReader] = {}
        self.init_readers()

        # Aligned synchronized time series
        self.steps: np.ndarray = np.array([], dtype=np.int64)
        self.times: np.ndarray = np.array([], dtype=float)
        self.Mrot: np.ndarray = np.array([], dtype=float)
        self.Mb: List[np.ndarray] = [np.array([], dtype=float) for _ in range(p.n_blades or 3)]
        self.Fx: Optional[np.ndarray] = None
        self.Fy: Optional[np.ndarray] = None
        self.Cp: np.ndarray = np.array([], dtype=float)
        self.Cm: np.ndarray = np.array([], dtype=float)
        self.Ct: Optional[np.ndarray] = None
        self.Cy: Optional[np.ndarray] = None

        # Revolution tracking
        self.rev_history: List[Dict[str, Any]] = []
        self.completed_revs_count: int = 0
        self.last_rev_end_time: float = 0.0
        self.last_step_processed: int = -1

        # Performance timing
        self.last_update_wall_time: float = time.time()
        self.steps_per_sec: float = 0.0
        self.last_step_count: int = 0

        # Alerts feed
        self.alerts: List[Dict[str, Any]] = []
        self.logged_alert_keys: set = set()

        # Azimuth & live health telemetry state
        self.start_azimuth_info: Optional[Dict[str, Any]] = None
        self.blade_phasing_info: Optional[Dict[str, Any]] = None
        self._az_checked_rev: int = -1
        self._stall_alert_active: bool = False
        self._stall_episode: int = 0
        self._last_aitken_alert_rev: int = -1

        # Convergence state
        self.stable_reached_notified: bool = False
        self.stalled_timeout: float = 60.0
        self.heat_map: Optional[np.ndarray] = None
        self.spectrum: Dict[str, Any] = {}
        self.blade_torques: Dict[str, Any] = {}
        self.forces_az: Dict[str, Any] = {}
        self.last_refresh_wall_time: float = 0.0
        self._last_late_check_time: float = 0.0
        self.last_stage_count: int = 1
        self.run_segment: int = 1

        # Prior run linking & missing-step continuity cache
        self.prior_series_cache: Dict[str, Dict[str, Any]] = {}
        self.prior_source_label: Optional[str] = None
        self.continuity_summary: Optional[Dict[str, Any]] = None
        self._prefix_by_role: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        self._prefix_cutoff_step: Dict[str, int] = {}
        self._role_continuity_info: Dict[str, Dict[str, Any]] = {}
        self._prefix_refined_for_gap: bool = False
        self.recovered_mask: np.ndarray = np.array([], dtype=bool)
        self._load_prior_if_needed()

        if self.readers:
            roles_txt = ", ".join(sorted(self.readers.keys()))
            self.add_alert(
                "INFO",
                f"Attached {len(self.readers)} monitor file(s): {roles_txt}.",
                key="session_watch",
            )
        setup_notes = self.detected_folder_params.get("setup_notes") or []
        for idx_n, note_txt in enumerate(setup_notes):
            self.add_alert("INFO", note_txt, key=f"setup_note_{idx_n}")

    def _load_prior_if_needed(self) -> None:
        """Load matched prior/backup monitor series once into memory cache."""
        self.prior_series_cache.clear()
        self.prior_source_label = None
        self.continuity_summary = None
        self._prefix_by_role.clear()
        self._prefix_cutoff_step.clear()
        self._role_continuity_info.clear()
        self._prefix_refined_for_gap = False

        prior_src = getattr(self.f, "prior_source", None)
        cont_mode = getattr(self.f, "continuity_mode", "auto") or "auto"
        if not prior_src or cont_mode == "off":
            return

        try:
            from vawt_post.engine.continuity import load_prior_series

            curr_names = {
                role: os.path.basename(rd.path)
                for role, rd in self.readers.items()
                if rd and rd.path
            }
            loaded = load_prior_series(
                prior_source=prior_src,
                n_blades=int(self.p.n_blades or 3),
                current_files=self.f,
                current_role_names=curr_names,
            )
            self.prior_series_cache = loaded["series"]
            self.prior_source_label = loaded["source_label"]
            if self.prior_series_cache:
                self.add_alert(
                    "INFO",
                    f"Backup linked from '{self.prior_source_label}' ({len(self.prior_series_cache)} prior series loaded).",
                    key="prior_loaded",
                )
            else:
                self.add_alert(
                    "WARNING",
                    f"Linked prior source '{self.prior_source_label}' contained 0 matching monitor files.",
                    key="prior_loaded",
                )
        except Exception as exc:
            self.add_alert(
                "WARNING",
                f"Could not load linked backup '{os.path.basename(str(prior_src))}': {exc}",
                key="prior_loaded",
            )

    def set_prior_source(
        self,
        prior_source: Optional[str],
        continuity_mode: str = "auto",
        target_revs: Optional[float] = None,
    ) -> LiveMonitorSnapshot:
        """Dynamically attach, change, or clear a prior/backup run link during monitoring."""
        self.f.prior_source = prior_source or None
        self.f.continuity_mode = continuity_mode or "auto"
        for k_clear in ("prior_loaded", "continuity_stitch"):
            self.logged_alert_keys.discard(k_clear)
        self.last_step_processed = -1
        self.completed_revs_count = 0
        self.rev_history.clear()
        self.stable_reached_notified = False
        self.heat_map = None
        self.spectrum = {}
        self.blade_torques = {}
        self.forces_az = {}
        self.start_azimuth_info = None
        self.blade_phasing_info = None
        self._az_checked_rev = -1
        self._load_prior_if_needed()
        self.tick()
        return self.get_snapshot(target_revs=target_revs, is_full_refresh=True)

    def init_readers(self) -> None:
        """Initialize incremental file readers for all assigned monitor files."""
        self.readers.clear()
        if self.f.total and os.path.isfile(self.f.total):
            self.readers["total"] = IncrementalFileReader(self.f.total, "total")
        for i, bp in enumerate(self.f.blades):
            if bp and os.path.isfile(bp):
                self.readers[f"blade {i + 1}"] = IncrementalFileReader(bp, f"blade {i + 1}")
        if self.f.shaft and os.path.isfile(self.f.shaft):
            self.readers["shaft"] = IncrementalFileReader(self.f.shaft, "shaft")
        if self.f.fx and os.path.isfile(self.f.fx):
            self.readers["fx"] = IncrementalFileReader(self.f.fx, "fx")
        if self.f.fy and os.path.isfile(self.f.fy):
            self.readers["fy"] = IncrementalFileReader(self.f.fy, "fy")
        if self.f.fx_shaft and os.path.isfile(self.f.fx_shaft):
            self.readers["fx_shaft"] = IncrementalFileReader(self.f.fx_shaft, "fx_shaft")
        if self.f.fy_shaft and os.path.isfile(self.f.fy_shaft):
            self.readers["fy_shaft"] = IncrementalFileReader(self.f.fy_shaft, "fy_shaft")

    def _check_late_files(self) -> None:
        """Attach monitor files that appear after monitoring started (throttled folder scan)."""
        now = time.time()
        if (now - self._last_late_check_time) < 15.0:
            return
        self._last_late_check_time = now
        # If watch_dir is known, look for newly created monitor files
        if self.watch_dir and os.path.isdir(self.watch_dir):
            need_total = not self.f.total or not os.path.isfile(self.f.total)
            need_blades = (not self.f.blades) or any(not b or not os.path.isfile(b) for b in self.f.blades)
            need_shaft = not self.f.shaft or not os.path.isfile(self.f.shaft)
            need_fx = not self.f.fx or not os.path.isfile(self.f.fx)
            need_fy = not self.f.fy or not os.path.isfile(self.f.fy)
            need_fx_shaft = not self.f.fx_shaft or not os.path.isfile(self.f.fx_shaft)
            need_fy_shaft = not self.f.fy_shaft or not os.path.isfile(self.f.fy_shaft)
            if need_total or need_blades or need_shaft or need_fx or need_fy or need_fx_shaft or need_fy_shaft:
                try:
                    from vawt_post.engine.parser import autodetect
                    nb_target = self.p.n_blades or 3
                    auto_f = autodetect(self.watch_dir, nb_target)
                    if need_total and auto_f.total and os.path.isfile(auto_f.total):
                        self.f.total = auto_f.total
                    if need_shaft and auto_f.shaft and os.path.isfile(auto_f.shaft):
                        self.f.shaft = auto_f.shaft
                    if need_fx and auto_f.fx and os.path.isfile(auto_f.fx):
                        self.f.fx = auto_f.fx
                    if need_fy and auto_f.fy and os.path.isfile(auto_f.fy):
                        self.f.fy = auto_f.fy
                    if need_fx_shaft and auto_f.fx_shaft and os.path.isfile(auto_f.fx_shaft):
                        self.f.fx_shaft = auto_f.fx_shaft
                    if need_fy_shaft and auto_f.fy_shaft and os.path.isfile(auto_f.fy_shaft):
                        self.f.fy_shaft = auto_f.fy_shaft
                    if len(self.f.blades) < nb_target:
                        self.f.blades.extend([None] * (nb_target - len(self.f.blades)))
                    for i in range(len(self.f.blades)):
                        b_path = self.f.blades[i]
                        auto_b = auto_f.blades[i] if i < len(auto_f.blades) else None
                        if (not b_path or not os.path.isfile(b_path)) and auto_b and os.path.isfile(auto_b):
                            self.f.blades[i] = auto_b
                except Exception:
                    pass

        if self.f.total and "total" not in self.readers and os.path.isfile(self.f.total):
            self.readers["total"] = IncrementalFileReader(self.f.total, "total")
            self.add_alert("INFO", f"Attached late-appearing total moment file '{os.path.basename(self.f.total)}'")
        for i, bp in enumerate(self.f.blades):
            rk = f"blade {i + 1}"
            if bp and rk not in self.readers and os.path.isfile(bp):
                self.readers[rk] = IncrementalFileReader(bp, rk)
                self.add_alert("INFO", f"Attached late-appearing blade {i+1} file '{os.path.basename(bp)}'")
        if self.f.shaft and "shaft" not in self.readers and os.path.isfile(self.f.shaft):
            self.readers["shaft"] = IncrementalFileReader(self.f.shaft, "shaft")
        if self.f.fx and "fx" not in self.readers and os.path.isfile(self.f.fx):
            self.readers["fx"] = IncrementalFileReader(self.f.fx, "fx")
        if self.f.fy and "fy" not in self.readers and os.path.isfile(self.f.fy):
            self.readers["fy"] = IncrementalFileReader(self.f.fy, "fy")
        if self.f.fx_shaft and "fx_shaft" not in self.readers and os.path.isfile(self.f.fx_shaft):
            self.readers["fx_shaft"] = IncrementalFileReader(self.f.fx_shaft, "fx_shaft")
        if self.f.fy_shaft and "fy_shaft" not in self.readers and os.path.isfile(self.f.fy_shaft):
            self.readers["fy_shaft"] = IncrementalFileReader(self.f.fy_shaft, "fy_shaft")

    def add_alert(self, severity: str, message: str, key: Optional[str] = None) -> None:
        """Add timestamped notification to alerts feed."""
        if key and key in self.logged_alert_keys:
            return
        if key:
            self.logged_alert_keys.add(key)
        stamp_str = _dt.datetime.now().strftime("%H:%M:%S")
        self.alerts.append({
            "time": stamp_str,
            "severity": severity,  # "ERROR", "WARNING", "INFO", "SUCCESS"
            "message": message,
            "key": key,
        })
        # Keep latest 100 alerts
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-100:]

    def tick_light(self) -> bool:
        """Lightweight tick: read new bytes and check file statuses without heavy computations."""
        self._check_late_files()
        new_data_found = False
        for role, reader in self.readers.items():
            count, _, _, _ = reader.read_new_chunk()
            if count > 0:
                new_data_found = True
            if getattr(reader, "restart_detected", False):
                reader.restart_detected = False
                self.run_segment += 1
                # Start new run segment cleanly; never silently merge disjoint runs
                self.steps = np.array([], dtype=np.int64)
                self.times = np.array([], dtype=float)
                self.Mrot = np.array([], dtype=float)
                self.Mb = [np.array([], dtype=float) for _ in range(self.p.n_blades or 3)]
                self.Fx = None
                self.Fy = None
                self.Cp = np.array([], dtype=float)
                self.Cm = np.array([], dtype=float)
                self.Ct = None
                self.Cy = None
                self.rev_history.clear()
                self.completed_revs_count = 0
                self.last_rev_end_time = 0.0
                self.last_step_processed = -1
                self.last_step_count = 0
                self.stable_reached_notified = False
                self.heat_map = None
                self.spectrum = {}
                self.blade_torques = {}
                self.forces_az = {}
                self.last_stage_count = 1
                self.start_azimuth_info = None
                self.blade_phasing_info = None
                self._az_checked_rev = -1
                self._stall_alert_active = False
                self._last_aitken_alert_rev = -1
                for k_clear in (
                    "conv_stable", "rotor_speed_mismatch", "dt_mismatch",
                    "midspin_detected", "midspin_flowtime", "theta0_verified",
                    "blade_phasing", "dt_res_initial", "coarse_az_res",
                    "cp_physical_range", "continuity_stitch",
                ):
                    self.logged_alert_keys.discard(k_clear)
                self._prefix_by_role.clear()
                self._prefix_cutoff_step.clear()
                self._role_continuity_info.clear()
                self._prefix_refined_for_gap = False
                self.recovered_mask = np.array([], dtype=bool)
                self.add_alert(
                    "WARNING",
                    f"Simulation restart or truncation detected in '{os.path.basename(reader.path)}': "
                    f"started new run segment #{self.run_segment}. Previous run preserved without silent merging.",
                    key=f"restart_{role}_{time.time()}"
                )
            if reader.has_missing_col_error:
                self.add_alert(
                    "ERROR",
                    f"File '{os.path.basename(reader.path)}' ({role}) lacks a value column! Only 'Time Step' and 'flow-time' exist.",
                    key=f"missing_col_{role}"
                )

        # Continually compute speed metrics even during light ticks
        now = time.time()
        dt_wall = now - self.last_update_wall_time
        if dt_wall >= 1.0 and self.readers:
            total_raw = max((len(rd.raw_steps) for rd in self.readers.values() if rd.is_valid), default=0)
            if self.last_step_count > 0:
                delta_steps = total_raw - self.last_step_count
                self.steps_per_sec = max(0.0, delta_steps / dt_wall)
            self.last_update_wall_time = now
            self.last_step_count = total_raw

        return new_data_found

    @staticmethod
    def _monotone_clean(
        steps: np.ndarray,
        times: np.ndarray,
        vals: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Keep strictly increasing unique steps (preserving latest row on duplicates)."""
        if len(steps) <= 1:
            return steps, times, vals
        diffs = np.diff(steps)
        if np.all(diffs > 0):
            return steps, times, vals
        last_idx: Dict[int, int] = {}
        for i, s in enumerate(steps.tolist()):
            last_idx[int(s)] = i
        keep = [last_idx[k] for k in sorted(last_idx.keys())]
        idx_arr = np.asarray(keep, dtype=np.int64)
        return steps[idx_arr], times[idx_arr], vals[idx_arr]

    def _resolve_role_arrays(
        self,
        valid_readers: Dict[str, IncrementalFileReader],
    ) -> Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """Return effective (steps, times, vals, recovered_mask) per role, with cached prior splice."""
        cont_mode = getattr(self.f, "continuity_mode", "auto") or "auto"
        has_prior = bool(self.prior_series_cache) and cont_mode != "off"

        out: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        need_summary_update = False

        if has_prior:
            from vawt_post.engine.continuity import normalize_role_key, stitch_series_pair

            min_curr_len = min((len(rd.raw_steps) for rd in valid_readers.values()), default=0)
            should_refine = (
                not self._prefix_refined_for_gap
                and min_curr_len >= 24
                and any(info.get("recovered_count", 0) > 0 for info in self._role_continuity_info.values())
            )
            if should_refine:
                self._prefix_by_role.clear()
                self._prefix_cutoff_step.clear()
                self._prefix_refined_for_gap = True
                # Recompute revolutions cleanly over the refined gap-recovered prefix
                self.last_step_processed = -1
                self.completed_revs_count = 0
                self.rev_history.clear()
                self.stable_reached_notified = False
                self._last_aitken_alert_rev = -1
                need_summary_update = True

            for role, rd in valid_readers.items():
                c_steps = np.asarray(rd.raw_steps, dtype=np.int64)
                c_times = np.asarray(rd.raw_times, dtype=float)
                if rd.time_col is None and self.p.dt and self.p.dt > 0:
                    c_times = c_times * float(self.p.dt)
                c_vals = np.asarray(rd.raw_vals, dtype=float)
                c_steps, c_times, c_vals = self._monotone_clean(c_steps, c_times, c_vals)

                canon = normalize_role_key(role)
                prior_s = self.prior_series_cache.get(canon) or self.prior_series_cache.get(role)
                if prior_s is None:
                    out[role] = (c_steps, c_times, c_vals, np.zeros(len(c_steps), dtype=bool))
                    continue

                if role not in self._prefix_by_role:
                    curr_dict = {"step": c_steps, "time": c_times, "value": c_vals}
                    stitched, r_info = stitch_series_pair(prior_s, curr_dict, p=self.p, mode=cont_mode)
                    self._role_continuity_info[role] = r_info
                    m_step = stitched["step"]
                    m_time = stitched["time"]
                    m_val = stitched["value"]
                    m_rec = stitched["recovered_mask"]

                    if r_info.get("status") == "incompatible_backup":
                        cutoff = int(c_steps[0]) if len(c_steps) > 0 else 0
                    elif cont_mode == "prefer_backup":
                        cutoff = int(prior_s["step"][-1]) + 1 if len(prior_s["step"]) > 0 else int(c_steps[0])
                    else:
                        cutoff = int(c_steps[0]) if len(c_steps) > 0 else 0

                    pref_mask = m_step < cutoff
                    self._prefix_by_role[role] = (
                        m_step[pref_mask],
                        m_time[pref_mask],
                        m_val[pref_mask],
                        m_rec[pref_mask],
                    )
                    self._prefix_cutoff_step[role] = cutoff
                    need_summary_update = True

                ps, pt, pv, pr = self._prefix_by_role[role]
                cutoff = self._prefix_cutoff_step[role]
                tail_mask = c_steps >= cutoff
                if len(ps) > 0:
                    eff_s = np.concatenate([ps, c_steps[tail_mask]])
                    eff_t = np.concatenate([pt, c_times[tail_mask]])
                    eff_v = np.concatenate([pv, c_vals[tail_mask]])
                    eff_r = np.concatenate([pr, np.zeros(int(np.sum(tail_mask)), dtype=bool)])
                else:
                    eff_s = c_steps[tail_mask]
                    eff_t = c_times[tail_mask]
                    eff_v = c_vals[tail_mask]
                    eff_r = np.zeros(len(eff_s), dtype=bool)
                out[role] = (eff_s, eff_t, eff_v, eff_r)

            if need_summary_update and self._role_continuity_info:
                ov_max = max((int(r.get("overlap_count", 0)) for r in self._role_continuity_info.values()), default=0)
                rec_max = max((int(r.get("recovered_count", 0)) for r in self._role_continuity_info.values()), default=0)
                any_div = any(bool(r.get("overlap_divergent", False)) for r in self._role_continuity_info.values())
                max_vd = max((float(r.get("overlap_max_val_diff", 0.0)) for r in self._role_continuity_info.values()), default=0.0)
                first_rep = next(iter(self._role_continuity_info.values()))
                nrmse_vals = [float(r["val_nrmse_pct"]) for r in self._role_continuity_info.values() if r.get("val_nrmse_pct") is not None]
                mean_nrmse = float(np.mean(nrmse_vals)) if nrmse_vals else None
                st_name = first_rep.get("status", "overlap_verified")
                all_incompat = all(
                    r.get("status") == "incompatible_backup"
                    for r in self._role_continuity_info.values()
                )

                if all_incompat:
                    b_rng = first_rep.get("backup_step_range")
                    c_rng = first_rep.get("current_step_range")
                    b_txt = f"{b_rng[0]}–{b_rng[1]}" if b_rng else "unknown"
                    c_txt = f"{c_rng[0]}–{c_rng[1]}" if c_rng else "unknown"
                    sum_txt = (
                        f"Ignored incompatible prior source '{self.prior_source_label}': "
                        f"backup steps {b_txt} do not precede current run ({c_txt}). Using live run only."
                    )
                    self.add_alert("WARNING", sum_txt, key="continuity_stitch")
                    st_name = "incompatible_backup"
                    ov_max = 0
                    any_div = False
                    rec_max = 0
                elif rec_max > 0:
                    rngs = first_rep.get("recovered_step_ranges", [])
                    rng_txt = ", ".join(f"{a}–{b}" for a, b in rngs) if rngs else "gap"
                    acc = f" (est. NRMSE {mean_nrmse:.2f}%)" if mean_nrmse is not None else ""
                    sum_txt = (
                        f"Stitched {len(self._role_continuity_info)} role(s) with '{self.prior_source_label}': "
                        f"recovered {rec_max} missing step(s) ({rng_txt}) via {first_rep.get('method', '')}{acc}."
                    )
                    self.add_alert("WARNING" if rec_max > 30 else "INFO", sum_txt, key="continuity_stitch")
                elif st_name == "gap_unfilled":
                    n_miss = int(first_rep.get("missing_step_count", 0))
                    sum_txt = f"Linked '{self.prior_source_label}' (Splice Only): {n_miss} missing step(s) left unfilled."
                    self.add_alert("WARNING", sum_txt, key="continuity_stitch")
                elif ov_max > 0:
                    if any_div:
                        sum_txt = (
                            f"Stitched {len(self._role_continuity_info)} role(s) with '{self.prior_source_label}': "
                            f"{ov_max}-step overlap diverges (max |Δval|={max_vd:.3e}); spliced."
                        )
                        self.add_alert("WARNING", sum_txt, key="continuity_stitch")
                    else:
                        sum_txt = (
                            f"Stitched {len(self._role_continuity_info)} role(s) with '{self.prior_source_label}': "
                            f"{ov_max}-step checkpoint overlap verified exact."
                        )
                        self.add_alert("SUCCESS", sum_txt, key="continuity_stitch")
                else:
                    sum_txt = f"Stitched {len(self._role_continuity_info)} role(s) seamlessly with '{self.prior_source_label}'."
                    self.add_alert("SUCCESS", sum_txt, key="continuity_stitch")

                self.continuity_summary = {
                    "enabled": True,
                    "status": "incompatible_backup" if all_incompat else ("gap_recovered" if rec_max > 0 else ("overlap_divergent" if any_div else st_name)),
                    "source_label": self.prior_source_label,
                    "mode": cont_mode,
                    "matched_roles": list(self._role_continuity_info.keys()),
                    "overlap_count": ov_max,
                    "overlap_divergent": any_div,
                    "overlap_max_val_diff": max_vd,
                    "recovered_count": rec_max,
                    "recovered_step_ranges": first_rep.get("recovered_step_ranges", []),
                    "recovered_time_ranges": first_rep.get("recovered_time_ranges", []),
                    "splice_steps": first_rep.get("splice_steps", []),
                    "splice_times": first_rep.get("splice_times", []),
                    "method": first_rep.get("method", "direct_splice"),
                    "mean_validation_nrmse_pct": mean_nrmse,
                    "summary_text": sum_txt,
                }
        else:
            for role, rd in valid_readers.items():
                c_steps = np.asarray(rd.raw_steps, dtype=np.int64)
                c_times = np.asarray(rd.raw_times, dtype=float)
                if rd.time_col is None and self.p.dt and self.p.dt > 0:
                    c_times = c_times * float(self.p.dt)
                c_vals = np.asarray(rd.raw_vals, dtype=float)
                c_steps, c_times, c_vals = self._monotone_clean(c_steps, c_times, c_vals)
                out[role] = (c_steps, c_times, c_vals, np.zeros(len(c_steps), dtype=bool))

        return out

    def tick(self) -> None:
        """Execute one incremental poll and update live physics state."""
        self.tick_light()

        if not self.readers:
            return

        # 2. Synchronize files on common time steps
        valid_readers = {r: rd for r, rd in self.readers.items() if rd.is_valid and rd.raw_steps}
        if not valid_readers:
            return

        eff_arrays = self._resolve_role_arrays(valid_readers)

        # Common step numbers
        common_steps_set = None
        for s_arr, _, _, _ in eff_arrays.values():
            s_set = set(s_arr.tolist())
            common_steps_set = s_set if common_steps_set is None else common_steps_set & s_set

        if not common_steps_set:
            return

        sorted_steps = sorted(common_steps_set)
        latest_common_step = sorted_steps[-1]

        if latest_common_step <= self.last_step_processed:
            return

        # Align series up to latest common step
        common_arr = np.array(sorted_steps, dtype=np.int64)
        first_role = next(iter(eff_arrays.keys()))
        ref_steps, ref_times, _, _ = eff_arrays[first_role]

        pos_ref = np.searchsorted(ref_steps, common_arr)
        aligned_t = ref_times[pos_ref]

        # Extract values & recovered mask
        aligned_vals: Dict[str, np.ndarray] = {}
        aligned_rec = np.zeros(len(common_arr), dtype=bool)
        for role, (s_arr, _, v_arr, r_arr) in eff_arrays.items():
            pos_v = np.searchsorted(s_arr, common_arr)
            aligned_vals[role] = v_arr[pos_v]
            if len(r_arr) == len(s_arr) and np.any(r_arr):
                aligned_rec |= r_arr[pos_v]

        self.steps = common_arr
        self.times = aligned_t
        self.recovered_mask = aligned_rec

        # Physics derivation
        omega_val = self.p.omega_val() if (self.p.omega or (self.p.tsr and self.p.U and self.p.R)) else 1.0
        A = 2.0 * (self.p.R or 0.5) * self.p.depth
        Pref = 0.5 * self.p.rho * ((self.p.U or 8.0) ** 3) * A
        Mref = Pref / omega_val

        sign_val = -1.0 if getattr(self.p, "rotation_dir", "ccw") == "cw" else 1.0

        nb_count = self.p.n_blades or 0
        have_blades = nb_count > 0 and all(f"blade {i + 1}" in aligned_vals for i in range(nb_count))
        if have_blades:
            self.Mb = [sign_val * aligned_vals[f"blade {i + 1}"] for i in range(nb_count)]
            self.Mrot = np.sum(self.Mb, axis=0)
        elif "total" in aligned_vals:
            self.Mrot = sign_val * aligned_vals["total"]
            if "shaft" in aligned_vals:
                self.Mrot = self.Mrot - sign_val * aligned_vals["shaft"]
        else:
            self.Mrot = np.zeros_like(self.times)

        Fref = 0.5 * self.p.rho * ((self.p.U or 8.0) ** 2) * A
        if "fx" in aligned_vals:
            fx_net = aligned_vals["fx"]
            if "fx_shaft" in aligned_vals:
                fx_net = fx_net - aligned_vals["fx_shaft"]
            self.Fx = fx_net
            self.Ct = fx_net / Fref
        else:
            self.Fx = None
            self.Ct = None
        if "fy" in aligned_vals:
            fy_net = aligned_vals["fy"]
            if "fy_shaft" in aligned_vals:
                fy_net = fy_net - aligned_vals["fy_shaft"]
            self.Fy = fy_net
            self.Cy = fy_net / Fref
        else:
            self.Fy = None
            self.Cy = None

        self.Cp = omega_val * self.Mrot / Pref
        self.Cm = self.Mrot / Mref

        # Update speed metrics
        now = time.time()
        dt_wall = now - self.last_update_wall_time
        if dt_wall >= 1.0:
            delta_steps = len(self.steps) - self.last_step_count
            self.steps_per_sec = max(0.0, delta_steps / dt_wall)
            self.last_update_wall_time = now
            self.last_step_count = len(self.steps)

        # 3. Incremental revolution calculation
        Trev = 2.0 * math.pi / omega_val
        t_span = self.times[-1] - self.times[0]
        n_full_revs = int(math.floor(t_span / Trev + 1e-9))

        if n_full_revs > self.completed_revs_count:
            # New revolution(s) completed!
            for k in range(self.completed_revs_count + 1, n_full_revs + 1):
                t_a = self.times[0] + (k - 1) * Trev
                t_b = t_a + Trev
                cp_k = window_mean(self.times, self.Cp, t_a, t_b)
                cm_k = window_mean(self.times, self.Cm, t_a, t_b)
                ct_k = window_mean(self.times, self.Ct, t_a, t_b) if self.Ct is not None else None
                rev_entry: Dict[str, Any] = {
                    "k": k,
                    "t_start": t_a,
                    "t_end": t_b,
                    "cp": cp_k,
                    "cm": cm_k,
                    "ct": ct_k,
                    "dcp": None,
                    "stable": False,
                }
                if len(self.rev_history) > 0:
                    cp_prev = self.rev_history[-1]["cp"]
                    dcp = (cp_k - cp_prev) / cp_prev if abs(cp_prev) > 1e-9 else 0.0
                    rev_entry["dcp"] = dcp
                    tol_rel = (self.p.stable_tol or 5.0) / 100.0
                    rev_entry["stable"] = abs(dcp) < tol_rel

                self.rev_history.append(rev_entry)
                self.add_alert(
                    "INFO",
                    f"Revolution {k} complete: Mean Cp = {cp_k:.4f}" + (f" (ΔCp = {rev_entry['dcp']*100:+.2f}%)" if rev_entry['dcp'] is not None else "")
                )

            self.completed_revs_count = n_full_revs

            # Check if stable reached
            if len(self.rev_history) >= 2:
                try:
                    n_req = self.p.n_stable or 2
                except (ValueError, TypeError):
                    n_req = 2
                trailing_stable = 0
                for r in reversed(self.rev_history):
                    if r["stable"]:
                        trailing_stable += 1
                    else:
                        break
                if trailing_stable >= n_req and not self.stable_reached_notified:
                    self.stable_reached_notified = True
                    self.add_alert("SUCCESS", f"Convergence reached: trailing {trailing_stable} revolutions satisfied stability tolerance!", key="conv_stable")

            # Update heavy revolution items (heat map, spectrum, blade torques)
            self._update_heavy_revolution_metrics(omega_val, Trev)
        elif not self.blade_torques and self.Mb and len(self.times) > 10:
            self._update_heavy_revolution_metrics(omega_val, Trev)

        if len(self.times) >= 5 and self._az_checked_rev < 0:
            self._evaluate_start_azimuth_and_health(omega_val, Trev)

        self.last_step_processed = latest_common_step

    def _evaluate_start_azimuth_and_health(self, omega_val: float, Trev: float, force: bool = False) -> None:
        """Evaluate start-azimuth alignment, Δt resolution, blade phasing, and physical Cp bounds."""
        from vawt_post.engine.azimuth_detect import detect_start_azimuth

        # 1. Start-azimuth check (early flow-time/mesh before Rev 1, full 1P torque phase once Rev >= 1)
        if force or self._az_checked_rev < self.completed_revs_count:
            m_b1 = self.Mb[0] if (self.Mb and len(self.Mb[0]) == len(self.times)) else None
            m_tot = self.Mrot if len(self.Mrot) == len(self.times) else None
            if len(self.times) > 0:
                az_res = detect_start_azimuth(
                    self.p,
                    t=self.times,
                    step=self.steps,
                    m_b1=m_b1,
                    m_total=m_tot,
                    apply_moment_sign=False,
                    folder=self.watch_dir,
                )
                self.start_azimuth_info = az_res.to_dict()
                if az_res.needs_correction and az_res.recommended_theta0_deg is not None and az_res.confidence in ("high", "medium"):
                    self.add_alert(
                        "WARNING",
                        f"Mid-spin start detected: {az_res.summary}. Click 'Apply θ₀' to align curves.",
                        key="midspin_detected",
                    )
                elif az_res.mid_spin_compensated_by_flow_time:
                    self.add_alert(
                        "INFO",
                        az_res.summary,
                        key="midspin_flowtime",
                    )
                elif az_res.started_at_zero is True or (az_res.is_mid_spin and not az_res.needs_correction and az_res.confidence in ("high", "medium")):
                    self.add_alert(
                        "SUCCESS",
                        az_res.summary,
                        key="theta0_verified",
                    )
            self._az_checked_rev = self.completed_revs_count

        # 2. Time-step angular resolution telemetry
        if len(self.times) >= 5 and omega_val > 0 and Trev > 0:
            dts = np.diff(self.times[-min(len(self.times), 200):])
            pos_dts = dts[dts > 1e-12]
            if len(pos_dts) > 0:
                dt_now = float(np.median(pos_dts))
                deg_step = omega_val * dt_now * 180.0 / math.pi
                steps_rev = Trev / dt_now if dt_now > 0 else 0.0
                self.add_alert(
                    "INFO",
                    f"Resolution: Δt = {dt_now:.3e} s ({deg_step:.2f}°/step, {steps_rev:,.0f} steps/rev).",
                    key="dt_res_initial",
                )
                if deg_step > 2.0:
                    self.add_alert(
                        "WARNING",
                        f"Coarse angular resolution ({deg_step:.2f}°/step > 2.0°) may clip peak blade loads.",
                        key="coarse_az_res",
                    )

        # 3. Blade phasing & physical Cp range once >= 1 revolution completes
        if self.completed_revs_count >= 1:
            if len(self.Mb) >= 2 and all(len(m) == len(self.times) for m in self.Mb):
                try:
                    from vawt_post.engine.analysis import detect_offsets

                    offs, min_corr = detect_offsets(
                        self.times, self.Mb, omega_val, len(self.Mb), log=lambda *_: None
                    )
                    offs_txt = ", ".join(f"{o:.0f}°" for o in offs)
                    self.blade_phasing_info = {"offsets": offs, "min_corr": float(min_corr)}
                    if min_corr < 0.90:
                        self.add_alert(
                            "WARNING",
                            f"Weak blade-to-blade correlation (r = {min_corr:.2f} < 0.90); verify blade file order or turbulence asymmetry.",
                            key="blade_phasing",
                        )
                    else:
                        self.add_alert(
                            "INFO",
                            f"Blade phasing verified ({offs_txt}, correlation r = {min_corr:.2f}).",
                            key="blade_phasing",
                        )
                except Exception:
                    pass

            if self.rev_history:
                latest_cp = float(self.rev_history[-1]["cp"])
                if latest_cp < -0.05:
                    self.add_alert(
                        "WARNING",
                        f"Rev {self.completed_revs_count} Mean Cp is negative ({latest_cp:.4f}) — verify rotation direction (CCW/CW) or moment sign.",
                        key="cp_physical_range",
                    )
                elif latest_cp > 0.593:
                    self.add_alert(
                        "WARNING",
                        f"Rev {self.completed_revs_count} Mean Cp ({latest_cp:.4f}) exceeds Betz limit (0.593) — verify U, R, depth, or blockage.",
                        key="cp_physical_range",
                    )

    def update_params(self, new_p: Params, target_revs: Optional[float] = None) -> LiveMonitorSnapshot:
        """Update simulation parameters dynamically and recompute accumulated physics & convergence."""
        self.p = copy.deepcopy(new_p)
        for reader in self.readers.values():
            reader.dt_fallback = self.p.dt
        self.last_step_processed = -1
        self.completed_revs_count = 0
        self.rev_history.clear()
        self.stable_reached_notified = False
        self.Ct = None
        self.Cy = None
        self.Fx = None
        self.Fy = None
        self.heat_map = None
        self.spectrum = {}
        self.blade_torques = {}
        self.forces_az = {}
        self.start_azimuth_info = None
        self.blade_phasing_info = None
        self._az_checked_rev = -1
        self._last_aitken_alert_rev = -1
        self._prefix_by_role.clear()
        self._prefix_cutoff_step.clear()
        self._role_continuity_info.clear()
        self._prefix_refined_for_gap = False
        self.continuity_summary = None
        self.logged_alert_keys.clear()
        if self.prior_series_cache and self.prior_source_label:
            self.add_alert(
                "INFO",
                f"Backup linked from '{self.prior_source_label}' ({len(self.prior_series_cache)} prior series loaded).",
                key="prior_loaded",
            )
        self.tick()
        return self.get_snapshot(target_revs=target_revs, is_full_refresh=True)

    def _update_heavy_revolution_metrics(self, omega_val: float, Trev: float) -> None:
        """Compute heavy items (heat map, FFT spectrum, blade torques) once per completed revolution."""
        from vawt_post.engine.azimuth_detect import compute_az_base

        az_base = compute_az_base(self.p)
        bw = self.p.bin_deg or 1.0
        nb_bins = round(360.0 / bw)
        centers = (np.arange(nb_bins) + 0.5) * bw

        # 1. 2D Heat map
        hm_rows = []
        for k in range(1, self.completed_revs_count + 1):
            t_a = self.times[0] + (k - 1) * Trev
            t_b = t_a + Trev
            m_k = (self.times >= t_a) & (self.times < t_b)
            if np.any(m_k):
                th_k = np.mod(az_base + np.degrees(omega_val * self.times[m_k]), 360.0)
                idx_b = np.clip(np.floor(th_k / bw).astype(int), 0, nb_bins - 1)
                b_sums = np.zeros(nb_bins)
                b_cnts = np.zeros(nb_bins)
                np.add.at(b_sums, idx_b, self.Cp[m_k])
                np.add.at(b_cnts, idx_b, 1)
                valid = b_cnts > 0
                row = np.zeros(nb_bins)
                row[valid] = b_sums[valid] / b_cnts[valid]
                if not np.all(valid) and np.any(valid):
                    row[~valid] = np.interp(centers[~valid], centers[valid], row[valid], period=360.0)
                hm_rows.append(row)
        if hm_rows:
            self.heat_map = np.array(hm_rows)

        # 2. FFT Spectrum
        if self.completed_revs_count >= 1:
            t_end_rev = self.times[0] + self.completed_revs_count * Trev
            # Window over trailing completed revolutions (up to 5 revs) to exclude startup transients
            n_revs_window = min(5.0, float(self.completed_revs_count))
            t_start_rev = max(self.times[0], t_end_rev - n_revs_window * Trev)
            m_sig = (self.times >= t_start_rev) & (self.times <= t_end_rev)
            if np.sum(m_sig) >= 32:
                cp_sig = self.Cp[m_sig]
                t_sig = self.times[m_sig]
                # Equispaced resampling over window to handle multi-stage Δt without frequency distortion
                grid_n = min(len(t_sig), 4096)
                t_grid = np.linspace(t_sig[0], t_sig[-1], grid_n)
                dt_grid = float((t_sig[-1] - t_sig[0]) / (grid_n - 1))
                if dt_grid > 0:
                    cp_interp = np.interp(t_grid, t_sig, cp_sig)
                    fft_vals = np.abs(np.fft.rfft(cp_interp - np.mean(cp_interp))) * (2.0 / grid_n)
                    freqs = np.fft.rfftfreq(grid_n, d=dt_grid)
                    f0 = omega_val / (2.0 * math.pi)
                    fbpf = (self.p.n_blades or 3) * f0
                    self.spectrum = {
                        "freqs": freqs,
                        "amps": fft_vals,
                        "f0": f0,
                        "fbpf": fbpf,
                    }
                    # Dominant blade-passing frequency & implied rotor speed
                    f_band = (freqs >= 0.4 * fbpf) & (freqs <= 3.5 * fbpf)
                    if f_band.any():
                        f_peak = float(freqs[f_band][np.argmax(fft_vals[f_band])])
                        k_harmonic = max(1, round(f_peak / max(fbpf, 1e-9)))
                        omega_det = 2.0 * math.pi * (f_peak / k_harmonic) / max(1, self.p.n_blades or 3)
                        # Ground truth priority: if Fluent setup file gave moving-mesh omega and FFT is consistent (within 10%), retain ground truth
                        folder_om = float(self.detected_folder_params["omega"]) if self.detected_folder_params.get("omega") else None
                        if folder_om and folder_om > 0 and abs(omega_det - folder_om) / folder_om <= 0.10:
                            self.detected_omega = folder_om
                        else:
                            self.detected_omega = omega_det

        # 3. Blade torques vs azimuth
        if self.Mb and any(len(m) for m in self.Mb):
            b_dict = {}
            th_all = np.mod(az_base + np.degrees(omega_val * self.times), 360.0)
            win_tail = self.times >= max(self.times[0], self.times[-1] - Trev)
            for idx, mb_arr in enumerate(self.Mb):
                if len(mb_arr) == len(self.times):
                    idx_bins = np.clip(np.floor(th_all[win_tail] / bw).astype(int), 0, nb_bins - 1)
                    b_sums = np.zeros(nb_bins)
                    b_cnts = np.zeros(nb_bins)
                    np.add.at(b_sums, idx_bins, mb_arr[win_tail])
                    np.add.at(b_cnts, idx_bins, 1)
                    valid = b_cnts > 0
                    mb_az = np.zeros(nb_bins)
                    mb_az[valid] = b_sums[valid] / b_cnts[valid]
                    if not np.all(valid) and np.any(valid):
                        mb_az[~valid] = np.interp(centers[~valid], centers[valid], mb_az[valid], period=360.0)
                    b_dict[f"Blade {idx + 1}"] = mb_az
            self.blade_torques = {"theta": centers, "blades": b_dict}

        # 4. Re-evaluate start azimuth, phasing & health once per completed revolution
        self._evaluate_start_azimuth_and_health(omega_val, Trev)

    def get_kinematics(self) -> Dict[str, Any]:
        """Compute theoretical AoA, W/U, and Re vs azimuth for current parameters."""
        U = self.p.U or 8.0
        c = self.p.chord or 0.06
        tsr = self.p.tsr_val() if (self.p.tsr or (self.p.omega and self.p.U and self.p.R)) else 3.5
        rho = self.p.rho or 1.225
        mu = self.p.mu or 1.7894e-5
        theta = np.linspace(0, 360, 361)
        gamma = np.radians(theta)
        w_over_u = np.sqrt(np.sin(gamma)**2 + (np.cos(gamma) + tsr)**2)
        aoa = np.degrees(np.arctan2(np.sin(gamma), np.cos(gamma) + tsr))
        re_c = (rho * (w_over_u * U) * c) / mu
        return {
            "theta": theta,
            "aoa": aoa,
            "w_over_u": w_over_u,
            "re_c": re_c,
        }

    def detect_stages(self, omega_val: float, Trev: float) -> List[Dict[str, Any]]:
        """Detect simulation stages (time step size phases) from accumulated steps and times."""
        if len(self.times) < 2:
            dt_single = float(self.p.dt) if self.p.dt else None
            if dt_single:
                return [{
                    "stage_index": 1,
                    "step_from": int(self.steps[0]) if len(self.steps) else 0,
                    "step_to": int(self.steps[-1]) if len(self.steps) else 0,
                    "t_from": float(self.times[0]) if len(self.times) else 0.0,
                    "t_to": float(self.times[-1]) if len(self.times) else 0.0,
                    "n_steps": len(self.steps),
                    "duration_s": float(self.times[-1] - self.times[0]) if len(self.times) else 0.0,
                    "revolutions": float(self.times[-1] - self.times[0]) / Trev if (len(self.times) and Trev > 0) else 0.0,
                    "dt": dt_single,
                    "deg_per_step": math.degrees(omega_val * dt_single) if omega_val > 0 else 0.0,
                    "steps_per_rev": Trev / dt_single if (dt_single > 0 and Trev > 0) else 0.0,
                    "is_current": True,
                }]
            return []

        dts = np.diff(self.times)
        stages = []
        if len(dts):
            start = 0
            for i in range(1, len(dts) + 1):
                is_end = (i == len(dts))
                is_jump = (not is_end) and (abs(dts[i] - dts[start]) / max(abs(dts[start]), 1e-12) > STAGE_DT_CHANGE_REL_TOL)
                # Ignore isolated 1-step dt glitches that immediately revert on the next step
                if is_jump and (i + 1 < len(dts)) and (abs(dts[i + 1] - dts[start]) / max(abs(dts[start]), 1e-12) <= STAGE_DT_CHANGE_REL_TOL):
                    continue
                if is_end or is_jump:
                    d = float(np.median(dts[start:i]))
                    t_f = float(self.times[start]) if len(self.times) > start else 0.0
                    t_t = float(self.times[i]) if len(self.times) > i else float(self.times[-1])
                    dur = max(0.0, t_t - t_f)
                    step_f = int(self.steps[start]) if len(self.steps) > start else start
                    step_t = int(self.steps[i]) if len(self.steps) > i else (int(self.steps[-1]) if len(self.steps) else i)
                    n_st = max(1, step_t - step_f if step_t > step_f else (i - start))
                    stages.append({
                        "stage_index": len(stages) + 1,
                        "step_from": step_f,
                        "step_to": step_t,
                        "t_from": t_f,
                        "t_to": t_t,
                        "t_start": t_f,
                        "t_end": t_t,
                        "n_steps": n_st,
                        "duration_s": dur,
                        "revolutions": dur / Trev if Trev > 0 else 0.0,
                        "dt": d,
                        "deg_per_step": math.degrees(omega_val * d) if omega_val > 0 else 0.0,
                        "steps_per_rev": Trev / d if (d > 0 and Trev > 0) else 0.0,
                        "is_current": is_end,
                    })
                    start = i
        return stages

    def get_snapshot(self, target_revs: Optional[float] = None, is_full_refresh: bool = True) -> LiveMonitorSnapshot:
        """Produce an immutable snapshot for thread-safe consumption by GUI."""
        now = time.time()
        omega_val = self.p.omega_val() if (self.p.omega or (self.p.tsr and self.p.U and self.p.R)) else 1.0
        Trev = 2.0 * math.pi / omega_val

        from vawt_post.engine.azimuth_detect import compute_az_base

        az_base = compute_az_base(self.p)
        curr_step = int(self.steps[-1]) if len(self.steps) > 0 else 0
        curr_t = float(self.times[-1]) if len(self.times) > 0 else 0.0
        tot_revs = float((self.times[-1] - self.times[0]) / Trev) if len(self.times) > 1 else 0.0

        # Current azimuth (0 to 360) in continuous absolute flow-time
        curr_az = float(np.mod(az_base + np.degrees(omega_val * curr_t), 360.0))

        # Wall-clock ETA calculation: convert remaining revolutions -> remaining steps -> wall-clock seconds
        eta_s = None
        if target_revs and target_revs > tot_revs and self.steps_per_sec > 0 and len(self.times) > 1:
            revs_left = target_revs - tot_revs
            recent_dts = np.diff(self.times[-min(len(self.times), 100):])
            dt_est = float(np.median(recent_dts)) if len(recent_dts) > 0 else (float(self.p.dt) if self.p.dt else 0.0)
            if dt_est > 0 and Trev > 0:
                steps_left = revs_left * (Trev / dt_est)
                eta_s = max(0.0, steps_left / self.steps_per_sec)

        # File stats
        file_stats: Dict[str, LiveFileStats] = {}
        max_age = 0.0
        for role, reader in self.readers.items():
            age = max(0.0, now - reader.last_mtime) if reader.last_mtime > 0 else 0.0
            max_age = max(max_age, age)
            color = "green"
            if not reader.is_valid or reader.has_missing_col_error:
                color = "red"
            elif age > self.stalled_timeout:
                color = "amber"

            file_stats[role] = LiveFileStats(
                role=role,
                filename=os.path.basename(reader.path),
                status_color=color,
                row_count=len(reader.raw_steps),
                last_step=reader.raw_steps[-1] if reader.raw_steps else 0,
                last_flow_time=reader.raw_times[-1] if reader.raw_times else 0.0,
                age_seconds=age,
                open_diagnostics=1 if reader.has_missing_col_error else 0,
                error_message=reader.error_msg
            )

        # Status state & stall/resume alert transitions
        if not self.readers or len(self.steps) == 0:
            status_str = "Waiting for files"
        elif max_age > self.stalled_timeout:
            status_str = "Stalled / finished?"
            if not self._stall_alert_active:
                self._stall_alert_active = True
                self._stall_episode += 1
                self.add_alert(
                    "WARNING",
                    f"No new monitor rows for >{self.stalled_timeout:.0f}s (step {curr_step:,}, t={curr_t:.4f}s) — solver stalled or finished?",
                    key=f"stalled_ep_{self._stall_episode}",
                )
        else:
            status_str = "Monitoring"
            if self._stall_alert_active:
                self._stall_alert_active = False
                self.add_alert(
                    "INFO",
                    f"Monitor updates resumed at step {curr_step:,} (t={curr_t:.4f}s).",
                    key=f"resumed_ep_{self._stall_episode}",
                )

        # KPIs (windowed over latest revolution when >=1 rev is complete to exclude CFD startup impulse)
        running_cp = float(self.rev_history[-1]["cp"]) if self.rev_history else (float(np.mean(self.Cp)) if len(self.Cp) else 0.0)
        dcp_prev = float(self.rev_history[-1]["dcp"]) if (self.rev_history and self.rev_history[-1]["dcp"] is not None) else 0.0
        stable_n = 0
        for r in reversed(self.rev_history):
            if r["stable"]:
                stable_n += 1
            else:
                break

        win_kpi = (self.times >= max(self.times[0], self.times[-1] - Trev)) if (len(self.times) > 1 and self.completed_revs_count >= 1) else slice(None)
        mrot_kpi = self.Mrot[win_kpi] if len(self.Mrot) else self.Mrot
        cp_kpi = self.Cp[win_kpi] if len(self.Cp) else self.Cp
        ct_kpi = self.Ct[win_kpi] if (self.Ct is not None and len(self.Ct)) else None

        peak_tq = float(np.max(mrot_kpi)) if len(mrot_kpi) else 0.0
        ct_val = float(np.mean(ct_kpi)) if (ct_kpi is not None and len(ct_kpi)) else None
        ripple = float((np.max(cp_kpi) - np.min(cp_kpi)) / max(abs(running_cp), 1e-6)) if len(cp_kpi) else 0.0

        # Aitken extrapolation (monotonic geometric decay: 0 < r < 0.9)
        aitken_cp = None
        extra_revs = None
        if len(self.rev_history) >= 3:
            x0, x1, x2 = self.rev_history[-3]["cp"], self.rev_history[-2]["cp"], self.rev_history[-1]["cp"]
            d1, d2 = x1 - x0, x2 - x1
            denom = d1 - d2
            if abs(denom) > 1e-12 and abs(d2) < abs(d1) and (d1 * d2 > 0):
                r_ratio = d2 / d1
                if 0.0 < r_ratio < 0.9:
                    raw_corr = (d2 ** 2) / denom
                    max_corr = 5.0 * abs(d2)
                    corr = math.copysign(min(abs(raw_corr), max_corr), raw_corr)
                    aitken_cp = float(x2 + corr)
                    tol_rel = (self.p.stable_tol or 5.0) / 100.0
                    if abs(d2) > tol_rel * max(abs(x2), 1e-6):
                        extra_revs = math.ceil(math.log((tol_rel * max(abs(x2), 1e-6)) / abs(d2)) / math.log(r_ratio))
                        extra_revs = max(0, extra_revs)
                    else:
                        extra_revs = 0
                    if extra_revs > 0 and self._last_aitken_alert_rev < self.completed_revs_count:
                        self._last_aitken_alert_rev = self.completed_revs_count
                        self.add_alert(
                            "INFO",
                            f"Convergence forecast: ~{extra_revs} more rev(s) to {self.p.stable_tol or 5.0:g}% stability (Aitken Cp ≈ {aitken_cp:.4f}).",
                            key=f"aitken_eta_{self.completed_revs_count}_{extra_revs}",
                        )

        kpi_dict = {
            "running_cp": running_cp,
            "dcp_prev": dcp_prev,
            "stable_n": stable_n,
            "peak_torque": peak_tq,
            "ct": ct_val,
            "ripple": ripple,
            "aitken_cp": aitken_cp,
            "extra_revs": extra_revs,
        }

        # Decimated timeseries (max 5000 points to keep UI responsive)
        step_len = len(self.steps)
        deci = max(1, step_len // 5000)
        ts_dict = {
            "step": self.steps[::deci],
            "t": self.times[::deci],
            "cp": self.Cp[::deci],
            "cm": self.Cm[::deci],
            "ct": self.Ct[::deci] if self.Ct is not None else None,
            "cy": self.Cy[::deci] if self.Cy is not None else None,
            "Mrot": self.Mrot[::deci],
            "Mb": [m[::deci] for m in self.Mb],
            "recovered_mask": self.recovered_mask[::deci] if len(self.recovered_mask) == step_len else None,
            "splice_times": (self.continuity_summary.get("splice_times", []) if self.continuity_summary else []),
            "recovered_time_ranges": (self.continuity_summary.get("recovered_time_ranges", []) if self.continuity_summary else []),
        }

        # Azimuth curve (current partial revolution + completed revs)
        az_dict = {}
        if len(self.times) > 10:
            bw = self.p.bin_deg or 1.0
            nb_bins = round(360.0 / bw)
            centers = (np.arange(nb_bins) + 0.5) * bw
            th_curr = np.mod(az_base + np.degrees(omega_val * self.times), 360.0)

            # Bin latest 1 revolution
            win_tail = self.times >= max(self.times[0], self.times[-1] - Trev)
            b_counts = np.zeros(nb_bins, dtype=int)
            b_sums = np.zeros(nb_bins, dtype=float)
            idx_bins = np.clip(np.floor(th_curr[win_tail] / bw).astype(int), 0, nb_bins - 1)
            np.add.at(b_sums, idx_bins, self.Cp[win_tail])
            np.add.at(b_counts, idx_bins, 1)
            valid_b = b_counts > 0
            cp_az = np.zeros(nb_bins, dtype=float)
            cp_az[valid_b] = b_sums[valid_b] / b_counts[valid_b]
            if not np.all(valid_b) and np.any(valid_b):
                cp_az[~valid_b] = np.interp(centers[~valid_b], centers[valid_b], cp_az[valid_b], period=360.0)

            az_dict = {
                "theta": centers,
                "cp_latest": cp_az,
            }

        # Multi-stage detection & time step analysis
        stages = self.detect_stages(omega_val, Trev)
        if len(stages) > self.last_stage_count:
            curr_st = stages[-1]
            self.add_alert(
                "INFO",
                f"Multi-stage transition: Stage {len(stages)} active at step {curr_st['step_from']:,} "
                f"(t = {curr_st['t_from']:.4f} s) with Δt = {curr_st['dt']:.3e} s ({curr_st['deg_per_step']:.2f}°/step)",
                key=f"stage_trans_{len(stages)}"
            )
            self.last_stage_count = len(stages)

        curr_dt = stages[-1]["dt"] if stages else (float(np.median(np.diff(self.times))) if len(self.times) > 1 else (float(self.p.dt) if self.p.dt else None))

        # Parameter checks (entered vs detected)
        omega_det = getattr(self, "detected_omega", None)
        folder_om = float(self.detected_folder_params["omega"]) if self.detected_folder_params.get("omega") else None
        if folder_om and folder_om > 0:
            omega_ref = folder_om
        elif omega_det is not None and omega_det > 0:
            omega_ref = omega_det
        else:
            omega_ref = omega_val

        rel_err_om = abs(omega_ref - omega_val) / max(omega_val, 1e-12)
        om_status = "PASS" if rel_err_om <= 0.10 else "WARNING"
        om_det_str = f"{omega_ref:.2f} rad/s"
        if om_status == "WARNING":
            self.add_alert(
                "WARNING",
                f"Rotor speed mismatch: Data implies ω = {omega_ref:.2f} rad/s vs entered ω = {omega_val:.2f} rad/s",
                key="rotor_speed_mismatch"
            )

        dt_status = "PASS"
        if self.p.dt and curr_dt:
            # Check if entered dt matches current stage or any detected stage in multi-stage runs
            stage_dts = [s["dt"] for s in stages] if stages else [curr_dt]
            matches_any_stage = any(abs(self.p.dt - s_dt) / max(s_dt, 1e-12) <= 0.05 for s_dt in stage_dts)
            rel_err_dt = abs(self.p.dt - curr_dt) / max(curr_dt, 1e-12)
            if rel_err_dt > 0.02 and not matches_any_stage:
                dt_status = "WARNING"
                self.add_alert(
                    "WARNING",
                    f"Time step mismatch: Data shows Δt = {curr_dt:.3e} s vs entered Δt = {self.p.dt:.3e} s",
                    key="dt_mismatch"
                )

        dt_entry = {
            "label": "Time Step Size (Δt)",
            "entered": f"{self.p.dt:.5e} s" if self.p.dt else "auto",
            "detected": f"{curr_dt:.5e} s" if curr_dt else "auto",
            "stages_count": len(stages),
            "status": dt_status
        }
        saz_info = self.start_azimuth_info or {}
        if saz_info.get("needs_correction") and saz_info.get("recommended_theta0_deg") is not None:
            saz_status = "WARNING"
            saz_det = f"~{float(saz_info['recommended_theta0_deg']):.1f}° (mid-spin)"
        elif saz_info.get("mid_spin_compensated_by_flow_time"):
            saz_status = "PASS"
            saz_det = f"t0 -> {float(saz_info.get('flow_start_angle_deg') or 0.0):.1f}°"
        elif saz_info.get("started_at_zero") is True:
            saz_status = "PASS"
            saz_det = f"{self.p.theta0:.1f}° (✓)"
        elif saz_info.get("is_mid_spin"):
            saz_status = "PASS"
            saz_det = f"{self.p.theta0:.1f}° (aligned)"
        else:
            saz_status = "PASS"
            saz_det = f"{self.p.theta0:.1f}° (pending)"

        ph_info = self.blade_phasing_info or {}
        if ph_info:
            ph_corr = float(ph_info.get("min_corr", 1.0))
            ph_status = "PASS" if ph_corr >= 0.90 else "WARNING"
            ph_det = f"r={ph_corr:.2f}"
        else:
            ph_status = "PASS"
            ph_det = "pending"

        diag_sum = {"ERROR": 0, "WARNING": 0, "INFO": 0}
        for al in self.alerts:
            sv = str(al.get("severity", "INFO")).upper()
            if sv == "ERROR":
                diag_sum["ERROR"] += 1
            elif sv == "WARNING":
                diag_sum["WARNING"] += 1
            elif sv in ("INFO", "SUCCESS"):
                diag_sum["INFO"] += 1

        param_checks = {
            "rotor_speed": {
                "label": "Rotor Speed (ω)",
                "entered": f"{omega_val:.2f} rad/s",
                "detected": om_det_str,
                "status": om_status
            },
            "step_size": dt_entry,
            "time_step": dt_entry,
            "start_azimuth": {
                "label": "Start Azimuth (θ0)",
                "entered": f"{self.p.theta0:.1f}°",
                "detected": saz_det,
                "status": saz_status,
            },
            "blade_phasing": {
                "label": "Blade Phasing",
                "entered": "auto",
                "detected": ph_det,
                "status": ph_status,
            },
        }

        return LiveMonitorSnapshot(
            status=status_str,
            is_full_refresh=is_full_refresh,
            current_step=curr_step,
            current_time=curr_t,
            dt_seconds=curr_dt,
            total_revs=tot_revs,
            current_azimuth=curr_az,
            steps_per_sec=self.steps_per_sec,
            target_revs=target_revs,
            eta_seconds=eta_s,
            last_data_age_s=max_age,
            last_refresh_wall_time=time.time(),
            file_stats=file_stats,
            kpi=kpi_dict,
            alerts=list(self.alerts),
            timeseries=ts_dict,
            rev_history=list(self.rev_history),
            azimuth_curve=az_dict,
            heat_map=self.heat_map,
            spectrum=self.spectrum,
            kinematics=self.get_kinematics(),
            blade_torques=self.blade_torques,
            forces_az=self.forces_az,
            parameter_checks=param_checks,
            diagnostics_summary=diag_sum,
            stages=stages,
            start_azimuth=dict(saz_info) if saz_info else None,
            continuity=dict(self.continuity_summary) if self.continuity_summary else None,
        )

    def tick_full(self, target_revs: Optional[float] = None) -> LiveMonitorSnapshot:
        """Convenience method: execute tick() and return full refresh snapshot."""
        self.tick()
        return self.get_snapshot(target_revs, is_full_refresh=True)


class LiveMonitorWorker:
    """Background worker thread running two timers: light file checks and heavy graph updates."""

    def __init__(
        self,
        p: Params,
        f: InputFiles,
        snapshot_queue: Optional[queue.Queue] = None,
        graph_refresh_interval: float = 120.0,
        file_check_interval: float = 10.0,
        target_revs: Optional[float] = None,
        **kwargs
    ):
        self.p = p
        self.f = f
        q_in = snapshot_queue or kwargs.get("out_queue")
        self.queue: queue.Queue = q_in if q_in is not None else queue.Queue()

        g_int = kwargs.get("graph_refresh_interval_s", graph_refresh_interval)
        self.graph_refresh_interval = max(1.0, float(g_int))
        if "file_check_interval_s" in kwargs:
            self.file_check_interval = max(1.0, float(kwargs["file_check_interval_s"]))
        elif file_check_interval != 10.0:
            self.file_check_interval = max(1.0, float(file_check_interval))
        else:
            self.file_check_interval = self.graph_refresh_interval
        self.target_revs = target_revs or kwargs.get("target_revs")
        self.watch_dir = kwargs.get("watch_dir")

        self.engine = LiveMonitorEngine(self.p, self.f, watch_dir=self.watch_dir)
        self.engine.stalled_timeout = max(60.0, 3.0 * self.graph_refresh_interval)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._pause_graph_refresh = threading.Event()
        self._immediate_refresh_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start background polling thread."""
        self._stop_event.clear()
        self._pause_graph_refresh.clear()
        self._immediate_refresh_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="LiveMonitorWorker")
        self._thread.start()

    def stop(self) -> None:
        """Stop background polling thread."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_alive(self) -> bool:
        """Return True if background worker thread is running."""
        return bool(self._thread and self._thread.is_alive())

    def pause_auto_refresh(self) -> None:
        """Pause graph redraws and background file polling."""
        self._pause_graph_refresh.set()

    def resume_auto_refresh(self) -> None:
        """Resume graph redraws and polling."""
        self._pause_graph_refresh.clear()
        self._wake_event.set()

    # Aliases for backward compatibility
    pause = pause_auto_refresh
    resume = resume_auto_refresh

    def is_paused(self) -> bool:
        return self._pause_graph_refresh.is_set()

    def request_immediate_refresh(self) -> None:
        """Request immediate heavy graph refresh."""
        self._immediate_refresh_event.set()
        self._wake_event.set()

    def set_refresh_interval(self, interval_s: float) -> None:
        """Adjust refresh interval in seconds on the fly and synchronize file checking."""
        self.graph_refresh_interval = max(1.0, float(interval_s))
        self.file_check_interval = self.graph_refresh_interval
        self.engine.stalled_timeout = max(60.0, 3.0 * self.graph_refresh_interval)
        self._wake_event.set()

    set_poll_interval = set_refresh_interval

    def set_file_check_interval(self, interval_s: float) -> None:
        """Adjust light polling interval if explicitly configured."""
        self.file_check_interval = max(1.0, float(interval_s))
        self._wake_event.set()

    def set_target_revs(self, target: Optional[float]) -> None:
        """Set target revolution count for ETA estimation."""
        self.target_revs = target

    def update_params(self, new_p: Params) -> LiveMonitorSnapshot:
        """Update active parameters on engine and queue an immediate refreshed snapshot."""
        with self._lock:
            self.p = copy.deepcopy(new_p)
            snap = self.engine.update_params(self.p, target_revs=self.target_revs)
            if self.queue is not None:
                self.queue.put(snap)
            return snap

    def set_prior_source(self, prior_source: Optional[str], continuity_mode: str = "auto") -> LiveMonitorSnapshot:
        """Thread-safely link or clear a prior/backup run source while monitoring."""
        with self._lock:
            self.f.prior_source = prior_source or None
            self.f.continuity_mode = continuity_mode or "auto"
            snap = self.engine.set_prior_source(
                prior_source=self.f.prior_source,
                continuity_mode=self.f.continuity_mode,
                target_revs=self.target_revs,
            )
            if self.queue is not None:
                self.queue.put(snap)
            return snap

    def add_alert(self, severity: str, message: str, key: Optional[str] = None) -> LiveMonitorSnapshot:
        """Thread-safely append an alert/update to the engine and queue a refreshed snapshot."""
        with self._lock:
            self.engine.add_alert(severity, message, key=key)
            snap = self.engine.get_snapshot(self.target_revs, is_full_refresh=False)
            if self.queue is not None:
                self.queue.put(snap)
            return snap

    def _run_loop(self) -> None:
        """Worker loop running file polling and graph updates synchronized to user timer."""
        last_light_time = 0.0
        last_heavy_time = 0.0

        # Do initial tick immediately
        try:
            with self._lock:
                self.engine.tick()
                snap = self.engine.get_snapshot(self.target_revs, is_full_refresh=True)
            self.queue.put(snap)
            last_light_time = time.time()
            last_heavy_time = time.time()
        except Exception as e:
            with self._lock:
                self.engine.add_alert("ERROR", f"Initial tick error: {e}")

        while not self._stop_event.is_set():
            now = time.time()
            trigger_heavy = False
            trigger_light = False

            if self._immediate_refresh_event.is_set():
                self._immediate_refresh_event.clear()
                trigger_heavy = True
            elif self._pause_graph_refresh.is_set():
                # Paused: do not poll disk files or redraw graphs in background
                pass
            else:
                if now - last_heavy_time >= self.graph_refresh_interval:
                    trigger_heavy = True
                elif self.file_check_interval < self.graph_refresh_interval and (now - last_light_time >= self.file_check_interval):
                    trigger_light = True

            if trigger_heavy:
                try:
                    with self._lock:
                        self.engine.tick()
                        snap = self.engine.get_snapshot(self.target_revs, is_full_refresh=True)
                    self.queue.put(snap)
                except Exception as e:
                    with self._lock:
                        self.engine.add_alert("ERROR", f"Refresh error: {e}")
                finally:
                    last_heavy_time = time.time()
                    last_light_time = time.time()
            elif trigger_light:
                try:
                    with self._lock:
                        self.engine.tick_light()
                        snap = self.engine.get_snapshot(self.target_revs, is_full_refresh=False)
                    self.queue.put(snap)
                except Exception as e:
                    with self._lock:
                        self.engine.add_alert("ERROR", f"Light check error: {e}")
                finally:
                    last_light_time = time.time()

            self._wake_event.wait(timeout=0.25)
            self._wake_event.clear()

    def save_snapshot(self, out_dir: str, prefix: str = "interim_live") -> str:
        """Save interim publication figures and interim Excel workbook marked 'run in progress'."""
        from vawt_post.engine.excel import write_excel
        from vawt_post.engine.analysis import analyze

        os.makedirs(out_dir, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx_path = os.path.join(out_dir, f"{prefix}_{stamp}.xlsx")

        with self._lock:
            p_copy = copy.deepcopy(self.p)
            f_copy = copy.deepcopy(self.f)
        p_copy.preset_name = f"(INTERIM RUN IN PROGRESS) {p_copy.preset_name or ''}".strip()
        R = analyze(p_copy, f_copy)
        write_excel(R, {}, xlsx_path)
        return xlsx_path

    def finalize(self, out_dir: str) -> Dict[str, Any]:
        """Run full offline analysis pipeline on data accumulated so far."""
        from vawt_post.engine.analysis import run_pipeline
        with self._lock:
            p_copy = copy.deepcopy(self.p)
            f_copy = copy.deepcopy(self.f)
        return run_pipeline(p_copy, f_copy, out_dir)
