# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Configuration & Parameter Management
=============================================================
Manages general-purpose turbine parameters, persistence of user settings,
saved presets, and application configuration across runs.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any


def parse_custom_revs_spec(spec: Optional[str]) -> List[int]:
    """Parse a user revolution selection string such as '16', '14-16', or '12, 14-16'.

    Returns a sorted list of unique 1-based revolution numbers, or [] if spec is blank/None.
    Raises ValueError on malformed syntax or non-positive revolution indices.
    """
    if not spec or not spec.strip():
        return []
    raw = spec.strip()
    # Strip optional unit words ('revolutions', 'revolution', 'revs', 'rev') and normalize 'to' / '..' / ':' / dashes into '-'
    cleaned = re.sub(r"(?i)\b(?:revolutions?|revs?)\b", " ", raw)
    cleaned = re.sub(r"(?i)(\d)\s*(?:revolutions?|revs?)\b", r"\1", cleaned)
    norm = re.sub(r"(?i)\s*(?:\.\.|:|–|—|-|\bto\b)\s*", "-", cleaned)
    parts = [p.strip() for p in re.split(r"[,;\s]+", norm) if p.strip()]
    if not parts:
        return []
    revs: List[int] = []
    for tok in parts:
        m_range = re.match(r"^(\d+)-(\d+)$", tok)
        if m_range:
            a, b = int(m_range.group(1)), int(m_range.group(2))
            if a < 1 or b < 1:
                raise ValueError(f"Revolution numbers must be >= 1 (got '{tok}').")
            if b < a:
                raise ValueError(f"Invalid revolution range '{tok}': end revolution ({b}) must be >= start revolution ({a}).")
            revs.extend(range(a, b + 1))
            continue
        m_single = re.match(r"^(\d+)$", tok)
        if m_single:
            k = int(m_single.group(1))
            if k < 1:
                raise ValueError(f"Revolution number must be >= 1 (got '{tok}').")
            revs.append(k)
            continue
        raise ValueError(
            f"Invalid specific revolution format '{raw}'. "
            "Use a single revolution (e.g. '16'), a range (e.g. '14-16'), or comma-separated values (e.g. '12, 14-16')."
        )
    return sorted(set(revs))

FLUENT_DEFAULTS = {
    "rho": 1.225,          # kg/m³ (air at standard conditions)
    "mu": 1.7894e-5,       # Pa·s (dynamic viscosity)
    "depth": 1.0,          # m (standard Fluent 2D span)
}

# Centralized simulation stage detection tolerance (2.0% change in time step size)
STAGE_DT_CHANGE_REL_TOL: float = 0.02

# Generic base parameters: U, R, n_blades, and tsr/omega start EMPTY and REQUIRED.
# Only Fluent physical defaults (rho, mu, depth) are pre-filled.
# Nothing is assumed from any published rotor.
GENERIC_DEFAULTS = {
    "U": None,
    "tsr": None,
    "omega": None,
    "R": None,
    "chord": None,
    "n_blades": None,
    "depth": 1.0,          # 2D domain depth (Fluent default 1.0 m; Cp is invariant to this)
    "span": None,          # Optional 3D blade span (m) for total 3D power scaling
    "rho": 1.225,
    "mu": 1.7894e-5,
    "theta0": 0.0,
    "offsets": "auto",
    "moment_sign": "fixed",
    "rotation_dir": "ccw",
    "azimuth_zero": "into_wind",
    "static_stall_deg": 12.0,
    "stable_tol": 5.0,
    "stable_tol_abs": 0.001,
    "drift_tol": None,
    "periodicity_tol": 0.01,
    "n_stable": 2,
    "bin_deg": 1.0,
    "dt": None,
    "write_timeseries": True,
}

# Optional published rotor benchmark examples (for reference/validation only - never applied automatically)
BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "Generic 3-Blade VAWT (Example)": {
        "description": "Generic straight-bladed 3-blade H-rotor example (R=0.5m, c=0.06m)",
        "params": {
            "U": 8.0,
            "tsr": 3.5,
            "omega": None,
            "R": 0.5,
            "chord": 0.06,
            "n_blades": 3,
            "depth": 1.0,
            "rho": 1.225,
            "mu": 1.7894e-5,
            "theta0": 0.0,
            "offsets": "auto",
            "moment_sign": "fixed",
            "rotation_dir": "ccw",
            "stable_tol": 5.0,
            "n_stable": 2,
            "bin_deg": 1.0,
            "dt": None,
            "write_timeseries": True,
        }
    },
    "Generic 2-Blade VAWT (Example)": {
        "description": "Generic straight-bladed 2-blade H-rotor example (R=0.6m, c=0.08m)",
        "params": {
            "U": 8.0,
            "tsr": 3.0,
            "omega": None,
            "R": 0.6,
            "chord": 0.08,
            "n_blades": 2,
            "depth": 1.0,
            "rho": 1.225,
            "mu": 1.7894e-5,
            "theta0": 0.0,
            "offsets": "auto",
            "moment_sign": "fixed",
            "rotation_dir": "ccw",
            "stable_tol": 5.0,
            "n_stable": 2,
            "bin_deg": 1.0,
            "dt": None,
            "write_timeseries": True,
        }
    },
    "Danao et al. (2014) Wind Tunnel Rotor (Example, Optional)": {
        "description": "Wind tunnel validation benchmark: Danao et al. (2014) Applied Energy, NACA 0022, R=0.35m, c=0.04m, span=0.6m, 3 blades, U=7.0m/s, TSR=4.0",
        "params": {
            "U": 7.0,
            "tsr": 4.0,
            "omega": 80.0,
            "R": 0.35,
            "chord": 0.04,
            "n_blades": 3,
            "depth": 1.0,
            "span": 0.6,
            "rho": 1.225,
            "mu": 1.7894e-5,
            "theta0": 0.0,
            "offsets": "auto",
            "moment_sign": "fixed",
            "rotation_dir": "ccw",
            "stable_tol": 5.0,
            "n_stable": 2,
            "bin_deg": 1.0,
            "dt": None,
            "write_timeseries": True,
        }
    },
    "Castelli et al. (2011) High-Solidity Rotor (Example, Optional)": {
        "description": "Numerical validation benchmark: Castelli et al. (2011) ASME Turbo Expo, NACA 0021, R=0.515m, c=0.0858m, span=1.0m, 3 blades, U=9.0m/s, TSR=2.4",
        "params": {
            "U": 9.0,
            "tsr": 2.4,
            "omega": 41.94,
            "R": 0.515,
            "chord": 0.0858,
            "n_blades": 3,
            "depth": 1.0,
            "span": 1.0,
            "rho": 1.225,
            "mu": 1.7894e-5,
            "theta0": 0.0,
            "offsets": "auto",
            "moment_sign": "fixed",
            "rotation_dir": "ccw",
            "stable_tol": 5.0,
            "n_stable": 2,
            "bin_deg": 1.0,
            "dt": None,
            "write_timeseries": True,
        }
    }
}


@dataclass
class CaseMetadata:
    mesh_cells: Optional[int] = None                  # Mesh cell count
    turb_model: Optional[str] = None                  # Turbulence model (e.g. k-omega SST, Transition SST, Laminar)
    time_step_s: Optional[float] = None               # Nominal time step size (s)
    iter_per_step: Optional[int] = 20                 # Iterations per time step
    residual_target: Optional[str] = "1e-5"           # Residual convergence target
    domain_size: Optional[str] = "30R x 20R"          # Domain size dimensions
    interface_type: Optional[str] = "Sliding Mesh"    # Interface type ("Sliding Mesh", "Overset")
    moment_center_confirmed: bool = True              # Moment centre = rotor axis confirmed

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CaseMetadata:
        if not d:
            return cls()
        valid = {k: v for k, v in d.items() if hasattr(cls, k)}
        return cls(**valid)


@dataclass
class Params:
    U: Optional[float] = None                       # free-stream speed (m/s) - REQUIRED
    tsr: Optional[float] = None                     # tip speed ratio lambda - REQUIRED (or omega)
    omega: Optional[float] = None                   # rotor speed (rad/s); None -> lambda*U/R
    R: Optional[float] = None                       # rotor radius (m) - REQUIRED
    chord: Optional[float] = None                   # blade chord (m) - OPTIONAL (only drives Re)
    n_blades: Optional[int] = None                  # number of blades (1 to 8) - REQUIRED
    depth: float = 1.0                              # 2D depth (m); Fluent default 1.0 m
    rho: float = 1.225                              # density (kg/m³); Fluent default air
    mu: float = 1.7894e-5                           # dynamic viscosity (Pa·s); Fluent default air
    theta0: float = 0.0                             # blade 1 azimuth at flow time 0 (deg)
    offsets: str = "auto"                           # "auto" or "even"
    moment_sign: str = "fixed"                      # "fixed" (default), "auto", "+1", "-1"
    rotation_dir: str = "ccw"                       # "ccw" (+mesh omega) or "cw" (-mesh omega)
    azimuth_zero: str = "into_wind"                 # "into_wind" (default), "top", "front", "rear", "bottom"
    azimuth_offset: float = 0.0                     # extra user offset (deg)
    static_stall_deg: float = 12.0                  # static stall angle (deg, default 12.0°)
    blade_offsets_manual: Optional[List[float]] = None
    stable_tol: float = 5.0                         # % change in rev-averaged Cp for stability
    stable_tol_abs: float = 0.001                   # absolute change in rev-averaged Cp (default 0.001)
    drift_tol: Optional[float] = None               # 3-rev drift tolerance (default 2*tol)
    periodicity_tol: float = 0.01                   # waveform RMS periodicity metric tolerance (1%)
    n_stable: int = 2                               # number of last stable revolutions to report
    custom_revs: Optional[str] = None               # optional specific revolution(s) to analyze, e.g. "14-16" or "16"
    bin_deg: float = 1.0                            # azimuth bin width (deg, must divide 360)
    dt: Optional[float] = None                      # time step fallback (s)
    write_timeseries: bool = True                   # whether to write full time series to Excel
    span: Optional[float] = None                    # real blade span H (m) for 3D scaling (Tier 3.6)
    preset_name: Optional[str] = None               # optional label for preset provenance
    applied_repairs: List[str] = field(default_factory=list) # explicit record of applied data repairs (e.g. reconstructed blade)
    ignore_diagnostic_errors: bool = False          # explicit record if user ignored diagnostic errors
    metadata: CaseMetadata = field(default_factory=CaseMetadata) # Case metadata fields (Tier 2)
    param_sources: Dict[str, str] = field(default_factory=dict) # source provenance per parameter

    def omega_val(self) -> float:
        if self.omega is not None and self.omega > 0:
            return float(self.omega)
        if self.tsr is not None and self.U is not None and self.R is not None and self.R > 0:
            return float(self.tsr * self.U / self.R)
        raise ValueError("Cannot determine rotor speed ω: provide either positive ω or both positive λ, U, and R.")

    def tsr_val(self) -> float:
        if self.tsr is not None and self.tsr > 0:
            return float(self.tsr)
        if self.omega is not None and self.R is not None and self.U is not None and self.U > 0:
            return float(self.omega * self.R / self.U)
        raise ValueError("Cannot determine tip speed ratio λ: provide either positive λ or both positive ω, R, and U.")

    def get_source(self, key: str) -> str:
        """Return provenance label for a parameter."""
        if self.param_sources and key in self.param_sources:
            return self.param_sources[key]
        if key in ("depth", "rho", "mu"):
            val = getattr(self, key, None)
            if val is not None and abs(val - FLUENT_DEFAULTS.get(key, 0.0)) < 1e-9:
                return "Fluent default"
        if self.preset_name:
            return f"from preset '{self.preset_name}'"
        return "typed by user"

    def validate(self) -> List[str]:
        """Validate parameters and return list of specific error messages."""
        errors: List[str] = []
        missing: List[str] = []

        def _finite_pos(val: Optional[float]) -> bool:
            return val is not None and math.isfinite(float(val)) and float(val) > 0.0

        if self.U is None:
            missing.append("Free-stream speed U")
        elif not _finite_pos(self.U):
            errors.append("Free-stream speed U must be a finite number greater than 0.")

        if self.R is None:
            missing.append("Rotor radius R")
        elif not _finite_pos(self.R):
            errors.append("Rotor radius R must be a finite number greater than 0.")

        if self.n_blades is None:
            missing.append("Number of blades N")
        elif not (1 <= self.n_blades <= 8):
            errors.append("Number of blades must be between 1 and 8.")

        has_tsr = self.tsr is not None
        has_omega = self.omega is not None
        if has_tsr and not _finite_pos(self.tsr):
            errors.append("Tip speed ratio λ must be a finite number greater than 0.")
        if has_omega and not _finite_pos(self.omega):
            errors.append("Rotor speed ω must be a finite number greater than 0.")
        if not (_finite_pos(self.tsr) or _finite_pos(self.omega)) and not (has_tsr or has_omega):
            missing.append("Tip speed ratio λ or rotor speed ω")

        if missing:
            errors.insert(0, f"Missing required parameter(s): {', '.join(missing)}.")

        if self.chord is not None and not _finite_pos(self.chord):
            errors.append("Blade chord c must be a finite number greater than 0 if specified.")
        if self.span is not None and not _finite_pos(self.span):
            errors.append("Blade span H must be a finite number greater than 0 if specified.")
        if not _finite_pos(self.depth):
            errors.append("2D depth reference must be a finite number greater than 0.")
        if not _finite_pos(self.rho):
            errors.append("Fluid density ρ must be a finite number greater than 0.")
        if not _finite_pos(self.mu):
            errors.append("Fluid dynamic viscosity μ must be a finite number greater than 0.")
        if not _finite_pos(self.stable_tol):
            errors.append("Stability tolerance must be a finite number greater than 0%.")
        if self.n_stable < 0:
            errors.append("Revolutions to report must be 0 (auto-detect all stable revolutions) or positive integer.")
        if self.custom_revs and self.custom_revs.strip():
            try:
                parse_custom_revs_spec(self.custom_revs)
            except ValueError as exc:
                errors.append(str(exc))
        if not _finite_pos(self.bin_deg) or self.bin_deg > 180.0:
            errors.append("Azimuth bin width must be a finite positive number <= 180°.")
        elif abs(round(360.0 / self.bin_deg) * self.bin_deg - 360.0) > 1e-6:
            errors.append(f"Azimuth bin width ({self.bin_deg}°) must divide 360° evenly (e.g. 0.5°, 1°, 2°, 5°).")
        if not math.isfinite(float(self.theta0)):
            errors.append("Blade 1 start azimuth θ₀ must be a finite number.")
        if not math.isfinite(float(self.azimuth_offset)):
            errors.append("Azimuth offset must be a finite number.")
        if self.offsets not in ("auto", "even"):
            errors.append("Blade phasing must be 'auto' or 'even'.")
        if self.moment_sign not in ("fixed", "auto", "+1", "-1"):
            errors.append("Moment sign must be 'fixed', 'auto', '+1', or '-1'.")
        if self.rotation_dir not in ("ccw", "cw"):
            errors.append("Rotation direction must be 'ccw' (counter-clockwise) or 'cw' (clockwise).")
        if self.azimuth_zero not in ("into_wind", "top", "front", "rear", "bottom"):
            errors.append("Azimuth zero position must be 'into_wind', 'top', 'front', 'rear', or 'bottom'.")
        if not _finite_pos(self.static_stall_deg):
            errors.append("Static stall angle must be a finite number greater than 0°.")
        return errors


@dataclass
class InputFiles:
    total: Optional[str] = None
    blades: List[Optional[str]] = field(default_factory=list)
    shaft: Optional[str] = None
    fx: Optional[str] = None
    fy: Optional[str] = None
    fx_shaft: Optional[str] = None
    fy_shaft: Optional[str] = None
    fx_blades: List[Optional[str]] = field(default_factory=list)
    fy_blades: List[Optional[str]] = field(default_factory=list)
    prior_source: Optional[str] = None
    continuity_mode: str = "auto"


class ConfigManager:
    """Manages persistent user configuration, saved turbine presets, and defaults."""

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = config_path
        else:
            # Prefer %APPDATA%/vawt_post on Windows, ~/.vawt_post on Linux/macOS
            if sys.platform.startswith("win"):
                app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
                base_dir = os.path.join(app_data, "vawt_post")
            else:
                base_dir = os.path.join(os.path.expanduser("~"), ".vawt_post")
            os.makedirs(base_dir, exist_ok=True)
            self.config_path = os.path.join(base_dir, "config.json")

        self.data: Dict[str, Any] = {
            "user_default": GENERIC_DEFAULTS.copy(),
            "presets": {},
            "last_session": {},
            "theme": "dark",
        }
        self.load()

    def load(self) -> None:
        """Load configuration file if exists."""
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
            except Exception:
                # Fallback to defaults on corrupted file
                pass

    def save(self) -> None:
        """Persist current configuration to disk."""
        try:
            parent = os.path.dirname(os.path.abspath(self.config_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def get_presets(self) -> Dict[str, Dict[str, Any]]:
        """Return combination of built-in presets and user presets."""
        combined = BUILTIN_PRESETS.copy()
        user_presets = self.data.get("presets")
        if isinstance(user_presets, dict):
            combined.update(user_presets)
        return combined

    def save_preset(self, name: str, params: Params, description: str = "") -> None:
        """Save a named parameter preset into program storage."""
        if "presets" not in self.data:
            self.data["presets"] = {}
        d = asdict(params)
        d["preset_name"] = name
        self.data["presets"][name] = {
            "description": description or f"User preset: {name}",
            "params": d,
        }
        self.save()

    def delete_preset(self, name: str) -> bool:
        """Delete a user preset. Built-in presets cannot be deleted."""
        if name in self.data.get("presets", {}):
            del self.data["presets"][name]
            self.save()
            return True
        return False

    def set_user_default(self, params: Params) -> None:
        """Save current parameters as the permanent user default for the program."""
        self.data["user_default"] = asdict(params)
        self.save()

    def get_user_default(self) -> Params:
        """Retrieve the user default parameters, falling back to generic defaults."""
        p_dict = self.data.get("user_default", GENERIC_DEFAULTS)
        return self._dict_to_params(p_dict)

    def save_last_session(self, params: Params, files: InputFiles, out_dir: str, prefix: str) -> None:
        """Save last-used parameters and paths so they are automatically restored next run."""
        self.data["last_session"] = {
            "params": asdict(params),
            "files": {
                "total": files.total,
                "shaft": files.shaft,
                "fx": files.fx,
                "fy": files.fy,
                "fx_shaft": files.fx_shaft,
                "fy_shaft": files.fy_shaft,
                "fx_blades": files.fx_blades,
                "fy_blades": files.fy_blades,
                "blades": files.blades,
                "prior_source": files.prior_source,
                "continuity_mode": files.continuity_mode,
            },
            "out_dir": out_dir,
            "prefix": prefix,
        }
        self.save()

    def get_last_session(self) -> Optional[Dict[str, Any]]:
        """Get last session data if available."""
        return self.data.get("last_session")

    def get_theme(self) -> str:
        return self.data.get("theme", "dark")

    def set_theme(self, theme: str) -> None:
        self.data["theme"] = theme
        self.save()

    def get_last_page(self, default: str = "input") -> str:
        """Get last active page name ('input', 'results', 'live')."""
        return self.data.get("last_page", default)

    def set_last_page(self, page: str) -> None:
        """Save active page name."""
        self.data["last_page"] = page
        self.save()

    def get_window_geometry(self, default: str = "1350x880") -> str:
        """Get saved window geometry string."""
        return self.data.get("window_geometry", default)

    def set_window_geometry(self, geometry: str) -> None:
        """Save window geometry string."""
        self.data["window_geometry"] = geometry
        self.save()

    def get_live_settings(self) -> Dict[str, Any]:
        """Get saved Live Monitor settings."""
        defaults = {
            "refresh_interval_s": 120.0,
            "unit": "minutes",
            "window_mode": "All",
            "grid_layout": "2×2 Grid",
            "focus_plot": "01. Cp History",
            "autoscale": True,
            "watched_folder": "",
            "prior_source": "",
            "continuity_mode": "auto",
        }
        saved = self.data.get("live_settings", {})
        return {**defaults, **saved}

    def set_live_settings(self, settings: Dict[str, Any]) -> None:
        """Save Live Monitor settings."""
        self.data["live_settings"] = settings
        self.save()

    def _dict_to_params(self, d: Dict[str, Any]) -> Params:
        """Safely convert dictionary to Params instance without sharing mutable state."""
        import copy as _copy
        valid_keys = set(Params.__dataclass_fields__.keys())
        filtered: Dict[str, Any] = {}
        for k, v in (d or {}).items():
            if k not in valid_keys:
                continue
            if k == "metadata":
                if isinstance(v, dict):
                    filtered[k] = CaseMetadata.from_dict(v)
                elif isinstance(v, CaseMetadata):
                    filtered[k] = _copy.deepcopy(v)
                # Ignore None so Params uses CaseMetadata default_factory
            elif k in ("param_sources", "applied_repairs"):
                if v is not None:
                    filtered[k] = _copy.deepcopy(v)
            else:
                filtered[k] = _copy.deepcopy(v) if isinstance(v, (dict, list)) else v
        return Params(**filtered)

    def load_preset(self, name: str) -> Optional[Params]:
        """Load a preset by name."""
        all_presets = self.get_presets()
        target_name = name
        if target_name not in all_presets:
            # Check with or without ' (Example)'
            if f"{name} (Example)" in all_presets:
                target_name = f"{name} (Example)"
            elif name.endswith(" (Example)") and name[:-10] in all_presets:
                target_name = name[:-10]
        if target_name in all_presets:
            p_dict = all_presets[target_name]["params"]
            p = self._dict_to_params(p_dict)
            p.preset_name = target_name
            # Record that parameters originated from this preset
            for k in p_dict:
                p.param_sources[k] = f"from preset '{target_name}'"
            return p
        return None
