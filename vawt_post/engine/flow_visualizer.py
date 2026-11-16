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
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

import numpy as np


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

    # 1. Check for standalone preview images (press.png, vel.png, vorti.png, etc.)
    png_files = glob.glob(os.path.join(fluent_dir, "*.png"))
    for p in png_files:
        base = os.path.splitext(os.path.basename(p))[0].lower()
        if "press" in base:
            sequences["Pressure (Snapshot)"] = FlowfieldSequence(
                name="Pressure (Snapshot)",
                field_type="pressure",
                frame_paths=[p],
                preview_path=p,
                frame_count=1
            )
        elif "vel" in base:
            sequences["Velocity (Snapshot)"] = FlowfieldSequence(
                name="Velocity (Snapshot)",
                field_type="velocity",
                frame_paths=[p],
                preview_path=p,
                frame_count=1
            )
        elif "vort" in base:
            sequences["Vorticity (Snapshot)"] = FlowfieldSequence(
                name="Vorticity (Snapshot)",
                field_type="vorticity",
                frame_paths=[p],
                preview_path=p,
                frame_count=1
            )
        elif "contour" in base:
            sequences[f"Contour: {base}"] = FlowfieldSequence(
                name=f"Contour: {base}",
                field_type="generic",
                frame_paths=[p],
                preview_path=p,
                frame_count=1
            )

    # 2. Check for animation frame series e.g. ani_press_*.hsf or png frames
    cxa_files = glob.glob(os.path.join(fluent_dir, "*.cxa"))
    for cxa in cxa_files:
        ani_prefix = os.path.splitext(os.path.basename(cxa))[0]
        # Look for frames matching prefix
        frames = glob.glob(os.path.join(fluent_dir, f"{ani_prefix}_*.*"))
        frames_valid = [f for f in sorted(frames) if not f.endswith(".cxa")]
        if frames_valid:
            ftype = "pressure" if "press" in ani_prefix else ("velocity" if "vel" in ani_prefix else "vorticity")
            display_name = f"Animation: {ani_prefix.replace('_', ' ').title()}"
            sequences[display_name] = FlowfieldSequence(
                name=display_name,
                field_type=ftype,
                frame_paths=frames_valid,
                preview_path=frames_valid[0] if frames_valid else None,
                frame_count=len(frames_valid)
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
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    fig = plt.figure(figsize=(10.0, 4.8), dpi=150)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.1, 1.0])

    # Left: Contour Image
    ax_img = fig.add_subplot(gs[0, 0])
    try:
        img = Image.open(contour_image_path)
        ax_img.imshow(img)
        ax_img.set_title(f"CFD Flowfield at $\\theta = {azimuth_deg:.1f}^\\circ$", fontsize=11, fontweight="bold")
    except Exception:
        ax_img.text(0.5, 0.5, "Contour Image Preview", ha="center", va="center")
    ax_img.axis("off")

    # Right: Instantaneous Torque / Cp curve
    ax_plot = fig.add_subplot(gs[0, 1])
    ax_plot.plot(azimuth_array, cp_array, color="#2563EB", linewidth=1.8, label="$C_p(\\theta)$ Cycle")
    ax_plot.scatter(
        [azimuth_deg], [current_cp],
        color="#DC2626", s=120, zorder=5, label=f"$\\theta={azimuth_deg:.0f}^\\circ, C_p={current_cp:.3f}$"
    )
    ax_plot.axvline(azimuth_deg, color="#DC2626", linestyle=":", alpha=0.7)

    ax_plot.set_xlim(0, 360)
    ax_plot.set_xlabel("Rotor Azimuth $\\theta$ [deg]", fontsize=10, fontweight="bold")
    ax_plot.set_ylabel("Power Coefficient $C_p$", fontsize=10, fontweight="bold")
    ax_plot.set_title("Instantaneous Cycle Aerodynamics", fontsize=11, fontweight="bold")
    ax_plot.grid(True, linestyle=":", alpha=0.6)
    ax_plot.legend(loc="upper right", fontsize=8.5)

    plt.tight_layout()

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        plt.savefig(out_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
        return out_path

    return fig
