# Changelog

All notable changes to **TurbineScope** are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [4.1.1] - 2026-11-16

### Added
- **ANSYS Workbench `dp0/FFF-{number}` System-Root Support (`mesh_inspector.py`, `parser.py`)**:
  - Selecting a Workbench system directory (`dp0/FFF`, `dp0/FFF-1`, `dp0/FFF-2`) in both Input Auto-Detect and Live Monitor resolves cleanly to `Fluent/` (even before `.out` monitor files exist) while simultaneously scanning sibling `MECH/*.msh`, `DM/*.agdb`, `global/MECH/*.mshdb`, and `progress_files/dp0/FFF-{n}/Fluent/report.xml`.
  - Computes vectorized mesh sizing & quality statistics directly from `.cas.h5` (equiangle skewness, orthogonal quality, aspect ratio split between core and boundary-layer cells, adjacent cell area growth rate, domain dimensions, wind-tunnel blockage ratio, blade/shaft wall first-layer height $y_1$, and estimated nominal/peak $y^+$).
- **Excel `Mesh_Info` Sheet (`excel.py`)**:
  - Dedicated worksheet recording Workbench system artifacts, global sizing & domain blockage, mesh quality statistics & guideline thresholds, cell zone breakdown, blade boundary-layer sizing & $y^+$ estimates, face zone inventory, and solver numerical setup notes.
- **Prior Run / Backup Linking & Missing-Timestep Recovery (`continuity.py`)**:
  - Supports linking a backup folder or Fluent `.zip` archive (such as `backup.zip`) across Input, Diagnostics, Analysis, Plots, and Live Monitor.
  - Handles both checkpoint rollback overlaps (verifying consistency and deduplicating via configurable `auto` / `prefer_backup` / `splice_only` policies) and positive missing-timestep gaps via Phase-Harmonic + $C^1$ Residual Bridge reconstruction with cross-role torque conservation ($\text{total} = \sum \text{blades} + \text{shaft}$), leave-window-out NRMSE scoring, visual plot overlays, and standalone stitched `.out` export.
- **Mid-Spin & Start-Azimuth ($\theta_0$) Detection (`azimuth_detect.py`, `diagnostics.py`, `live_monitor.py`)**:
  - Automatically checks whether data collection began from $0^\circ$ or mid-spin using flow-time offsets, `.cas.h5` blade node centroids, and Blade-1 1P torque phase regression.
- **Live Monitor Alerts & Updates**:
  - Dedicated alerts feed surfaced under the probe panel reporting file attachments, mesh/setup notes, mid-spin $\theta_0$ checks, continuity stitching/gap recovery, Aitken extrapolated $C_p$, stability achievements, and solver stall detection.
- **Startup Splash & Interactive Plots**:
  - Native Qt startup splash screen (`splash.py`) eliminating startup window flicker, plus interactive Matplotlib zoom, pan, cursor readout, and curve probing in both Results and Live Monitor.

### Changed
- Standardized dark-mode palette across all Qt 6 views and figure canvases (`design_system.py`, `qt_tk.py`).
- Improved shutdown lifecycle ensuring background worker threads terminate cleanly on exit.

## [4.0.0] - 2026-11-15

### Changed
- Migrated desktop GUI from CustomTkinter to **PySide6 / Qt 6** with `QtWidgets` adapter (`qt_tk.py`), `QThread` workers, native Windows taskbar/window icon embedding, and hardware-accelerated plot canvases.
- Added standalone single-file Windows binary packaging via `build_exe.py` and `TurbineScope.spec`.
