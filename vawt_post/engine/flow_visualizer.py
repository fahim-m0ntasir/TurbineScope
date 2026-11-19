# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Flowfield & Vortex Visualizer Engine
=============================================================
Discovers CFD flowfield frames and contour sequences exported by Fluent or CFD-Post
and synchronizes them with the instantaneous rotor torque & azimuth angle.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

import numpy as np


def _natural_sort_key(path: str) -> List[Any]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", os.path.basename(path))]


def _classify_field_type(name_lower: str) -> str:
    if "press" in name_lower:
        return "pressure"
    if "vel" in name_lower:
        return "velocity"
    if "vort" in name_lower:
        return "vorticity"
    return "generic"


@dataclass
class FlowfieldSequence:
    """Represents a discovered sequence of contour/animation frames."""
    name: str                           # e.g. "Static Pressure", "Velocity Magnitude", "Vorticity"
    field_type: str                     # "pressure", "velocity", "vorticity", "generic"
    frame_paths: List[str] = field(default_factory=list)
    preview_path: Optional[str] = None
    frame_count: int = 0


def discover_flowfield_assets(fluent_dir: str) -> Dict[str, FlowfieldSequence]:
    """
    Search a Fluent directory for exported animations, preview images, and frames.
    """
    sequences: Dict[str, FlowfieldSequence] = {}
    if not os.path.isdir(fluent_dir):
        return sequences

    escaped_dir = glob.escape(fluent_dir)
    claimed_frames: set = set()

    # 1. Check for animation frame series first (e.g. ani_press_*.hsf or png frames)
    cxa_files = sorted(glob.glob(os.path.join(escaped_dir, "*.cxa")), key=_natural_sort_key)
    for cxa in cxa_files:
        ani_prefix = os.path.splitext(os.path.basename(cxa))[0]
        frames = glob.glob(os.path.join(escaped_dir, f"{glob.escape(ani_prefix)}_*.*"))
        frames_valid = sorted(
            [f for f in frames if not f.lower().endswith(".cxa")],
            key=_natural_sort_key,
        )
        if frames_valid:
            for f in frames_valid:
                claimed_frames.add(os.path.normcase(os.path.abspath(f)))
            ftype = _classify_field_type(ani_prefix.lower())
            display_name = f"Animation: {ani_prefix.replace('_', ' ').title()}"
            sequences[display_name] = FlowfieldSequence(
                name=display_name,
                field_type=ftype,
                frame_paths=frames_valid,
                preview_path=frames_valid[0],
                frame_count=len(frames_valid),
            )

    # 2. Check for standalone preview images (excluding frames already claimed by animations)
    png_files = sorted(glob.glob(os.path.join(escaped_dir, "*.png")), key=_natural_sort_key)
    for p in png_files:
        if os.path.normcase(os.path.abspath(p)) in claimed_frames:
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        base = stem.lower()
        ftype = _classify_field_type(base)
        if ftype == "pressure":
            disp = "Pressure (Snapshot)"
        elif ftype == "velocity":
            disp = "Velocity (Snapshot)"
        elif ftype == "vorticity":
            disp = "Vorticity (Snapshot)"
        elif "contour" in base:
            disp = f"Contour: {stem}"
        else:
            continue
        if disp in sequences:
            disp = f"{disp} [{stem}]"
        sequences[disp] = FlowfieldSequence(
            name=disp,
            field_type=ftype,
            frame_paths=[p],
            preview_path=p,
            frame_count=1,
        )

    return sequences


def generate_synchronized_composite_frame(
    contour_image_path: str,
    azimuth_deg: float,
    current_cp: float,
    azimuth_array: np.ndarray,
    cp_array: np.ndarray,
    out_path: Optional[str] = None
) -> Any:
    """
    Generate a synchronized side-by-side composite frame:
      - Left: Flowfield contour
      - Right: Torque curve with current azimuth position marked.
    """
    import matplotlib.pyplot as plt
    from PIL import Image

    az_wrapped = float(azimuth_deg) % 360.0
    fig = plt.figure(figsize=(10.0, 4.8), dpi=150)
    try:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.1, 1.0])

        # Left: Contour Image
        ax_img = fig.add_subplot(gs[0, 0])
        try:
            with Image.open(contour_image_path) as img:
                ax_img.imshow(np.asarray(img))
            ax_img.set_title(f"CFD Flowfield at $\\theta = {az_wrapped:.1f}^\\circ$", fontsize=11, fontweight="bold")
        except Exception:
            ax_img.text(0.5, 0.5, "Contour Image Preview", ha="center", va="center")
        ax_img.axis("off")

        # Right: Instantaneous Torque / Cp curve
        ax_plot = fig.add_subplot(gs[0, 1])
        ax_plot.plot(azimuth_array, cp_array, color="#2563EB", linewidth=1.8, label="$C_p(\\theta)$ Cycle")
        ax_plot.scatter(
            [az_wrapped], [current_cp],
            color="#DC2626", s=120, zorder=5, label=f"$\\theta={az_wrapped:.0f}^\\circ, C_p={current_cp:.3f}$"
        )
        ax_plot.axvline(az_wrapped, color="#DC2626", linestyle=":", alpha=0.7)

        ax_plot.set_xlim(0, 360)
        ax_plot.set_xlabel("Rotor Azimuth $\\theta$ [deg]", fontsize=10, fontweight="bold")
        ax_plot.set_ylabel("Power Coefficient $C_p$", fontsize=10, fontweight="bold")
        ax_plot.set_title("Instantaneous Cycle Aerodynamics", fontsize=11, fontweight="bold")
        ax_plot.grid(True, linestyle=":", alpha=0.6)
        ax_plot.legend(loc="upper right", fontsize=8.5)

        fig.tight_layout()

        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            fig.savefig(out_path, dpi=200, bbox_inches="tight")
            plt.close(fig)
            return out_path
    except Exception:
        plt.close(fig)
        raise

    return fig
