# TurbineScope v4.1.1 — VAWT CFD Post-Processor & Live Studio

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20(Qt%206)-41CD52.svg)](https://doc.qt.io/qtforpython-6/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Type Checked](https://img.shields.io/badge/pyright-0%20errors-brightgreen.svg)](https://github.com/microsoft/pyright)

**TurbineScope** is an engineering post-processing, data-integrity auditing, live simulation surveillance, and ASME V&V 20 Workbench studio purpose-built for **Vertical-Axis Wind Turbine (VAWT)** CFD simulations in **ANSYS Fluent** and **ANSYS Workbench**.

---

## Key Capabilities

- **ANSYS Workbench `dp0/FFF-{number}` System-Root Workflow**:
  - Point **Input Auto-Detect** or **Live Monitor Watch Folder** directly to `dp0/FFF`, `dp0/FFF-1`, `dp0/FFF-2`, a `.wbpj` project, or a standalone `Fluent/` directory.
  - Automatically resolves monitor outputs (even *before* Fluent begins writing `.out` files), `.cas.h5` / `.dat.h5`, `MECH/*.msh`, `DM/*.agdb`, `global/MECH/*.mshdb`, and `progress_files/.../report.xml`.
  - Extracts geometry ($R, c, N$), operating conditions ($U, \omega, \Delta t, \rho, \mu$), turbulence model, solver coupling/schemes, and iteration settings directly from Fluent binary/XML metadata without opening Fluent.
- **Mesh Sizing, Boundary-Layer & Quality Inspector (`Mesh_Info` Excel Sheet)**:
  - Computes vectorized mesh quality metrics directly from HDF5 `.cas.h5` connectivity: **Equiangle Skewness**, **Orthogonal Quality**, **Aspect Ratio** (overall vs. core vs. boundary-layer inflation cells), and **Adjacent Cell Area Growth Rate**.
  - Audits domain dimensions, wind-tunnel blockage ratio ($D/W$), sliding-interface radius, blade tangential spacing $\Delta s$, **first-layer normal height $y_1$**, and **estimated nominal / peak $y^+$**.
- **Prior Run / Backup Linking & Intelligent Crash-Gap Recovery**:
  - Link a previous run folder or `.zip` archive (such as Fluent `backup.zip`) in either the **Input** tab or **Live Monitor**.
  - Verifies checkpoint-rollback overlaps for numerical consistency and reconstructs missing timesteps using a **Phase-Harmonic + $C^1$ Residual Bridge** (with PCHIP short/transient fallback, cross-role torque conservation $\text{total} = \sum \text{blades} + \text{shaft}$, and leave-window-out NRMSE validation).
  - Export clean, continuous Fluent-format `.out` report files plus a `_continuity_report.txt` audit summary.
- **Mid-Spin & Start-Azimuth ($\theta_0$) Auto-Detection**:
  - Automatically detects whether data collection started at $\theta_0 = 0^\circ$ or mid-spin using flow-time offsets, `.cas.h5` blade node centroids, and Blade-1 1P torque phase regression.
- **5 Integrated Workspaces (PySide6 / Qt 6)**:
  1. **Input & Analysis**: Physical/kinematic setup, presets, multi-stage time-step generator, Workbench `dp0/FFF-{n}` auto-detection, Prior Run Linking, and batch/offline execution.
  2. **File Health & Diagnostics**: 17+ automated integrity checks (format, monotonicity, NaN/Inf, flatlines, duplicate/missing steps, start-azimuth alignment, continuity verification, one-click mathematical repairs).
  3. **Interactive Results Studio**: KPI cards with custom revolution slicing, interactive zoom/pan/curve-probing plots (`Cp History`, `Cp per Rev`, `Cp vs Azimuth`, `Blade Torque`, `Ct & Cy`, `Dashboard`, etc.).
  4. **Live Monitor**: Zero-lag dual-timer surveillance (`0.5s` lightweight byte-tailing telemetry + configurable visual refresh), multi-stage $\Delta t$ detection, Aitken asymptotic $C_{p,\infty}$ extrapolation, live hot-link prior continuity, probe readouts, **Alerts & Updates** feed, interim Excel snapshots, and 1-click finalization.
  5. **Workbench & GCI Studio**: Multi-system catalog scanner, **ASME V&V 20** Grid Convergence Index (GCI) calculator with Richardson extrapolation and LaTeX/Markdown table export, and multi-system $C_p-\lambda$ curves.
- **Comprehensive Exports**:
  - Multi-sheet **Excel Workbook (`.xlsx`)**: `Summary`, `Graphs`, `Parameters`, `Mesh_Info`, `Revolutions`, `Azimuth_Cp`, `Blade_Azimuth`, `Alpha_Re`, `Processed`, `Raw_Data`, `QA_Checks`, `Warnings`, `File_Health`, `Captions`, and `Stages`.
  - Ready-to-compile **LaTeX thesis snippets (`.tex`)**, full time-series **CSV**, diagnostics JSON/CSV, and reproducibility **`results.json`** with SHA-256 checksums.

---

## Installation & Quick Start

### Prerequisites
- Python **3.10 – 3.13**
- Windows 10/11, Linux (with Qt xcb dependencies), or macOS 12+

### Install Dependencies

```bash
git clone https://github.com/fahim-m0ntasir/TurbineScope.git
cd TurbineScope
pip install -r requirements.txt
```

### Launch the Desktop GUI

```bash
python run_vawt_studio.py
# or
python -m vawt_post
```

### Command-Line Interface (Batch / Headless Pipeline)

```bash
python -m vawt_post.cli --help
```

### Build Portable Windows Executable (`TurbineScope.exe`)

```bash
python build_exe.py
```

The single-file executable is generated at `dist/TurbineScope.exe`.

---

## Project Structure

```text
TurbineScope_Qt/
├── vawt_post/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                   # CLI & GUI launcher entry point
│   ├── icon_assets.py           # Embedded custom VAWT icon assets
│   ├── assets/
│   │   ├── app_icon.ico
│   │   └── app_icon.png
│   ├── engine/
│   │   ├── analysis.py          # Core aerodynamic reduction, FFT, QA, pipeline
│   │   ├── azimuth_detect.py    # Mid-spin & start-azimuth (theta0) detection
│   │   ├── batch.py             # Multi-run & TSR sweep batch processing
│   │   ├── comparison.py        # Multi-case comparison overlays & splines
│   │   ├── config.py            # Parameters, metadata, presets, session persistence
│   │   ├── continuity.py        # Backup linking, overlap audit & gap recovery
│   │   ├── diagnostics.py       # 17+ monitor health checks & repairs
│   │   ├── excel.py             # Multi-sheet .xlsx workbook & Mesh_Info writer
│   │   ├── excel_post.py        # Aerodynamic non-destructive Excel embedding
│   │   ├── external.py          # Experimental benchmark CSV loader
│   │   ├── flow_visualizer.py   # Flow field visualization helpers
│   │   ├── forces.py            # Tangential/normal blade forces & dynamic stall
│   │   ├── gci_engine.py        # ASME V&V 20 GCI & Richardson extrapolation
│   │   ├── hdf5_case.py         # Native ANSYS Fluent .cas.h5/.msh.h5 inspector
│   │   ├── latex.py             # Thesis LaTeX table & figure snippet exporter
│   │   ├── live_monitor.py      # Incremental tailing engine & worker thread
│   │   ├── mesh_inspector.py    # dp0/FFF-{n} context, BL sizing & mesh quality
│   │   ├── parser.py            # Fluent monitor parser & folder auto-detector
│   │   ├── plots.py             # Publication figures & continuity overlays
│   │   ├── specialized_plots.py # AeroPost specialized figures & animations
│   │   ├── synthetic.py         # Multi-stage synthetic monitor generator
│   │   └── workbench.py         # .wbpj scanner, GCI integration & sweeps
│   └── ui/
│       ├── app.py               # Main window, Input tab, navigation
│       ├── components.py        # Reusable Qt dark-mode cards & widgets
│       ├── design_system.py     # Unified color tokens & typography
│       ├── diagnostics_tab.py   # File Health & Diagnostics view
│       ├── live_monitor_tab.py  # Live Monitor UI, stripcharts, probes & alerts
│       ├── preview.py           # Figure gallery & side-by-side comparison
│       ├── qt_tk.py             # PySide6 widget & Matplotlib FigureCanvas adapter
│       ├── results_tab.py       # Interactive Results Studio & KPI dashboard
│       ├── splash.py            # Branded Qt startup splash screen
│       └── workbench_tab.py     # Workbench & ASME V&V 20 GCI Studio tab
├── tests/                       # Comprehensive unit, physics, HDF5 & GUI test suite
├── .github/workflows/ci.yml     # GitHub Actions lint + typecheck + test workflow
├── build_exe.py                 # PyInstaller standalone .exe builder
├── TurbineScope.spec            # PyInstaller build specification
├── pyproject.toml               # Package, pytest, and pyright configuration
├── requirements.txt
├── USERMANUAL.md                # Detailed user & workflow manual
├── CHANGELOG.md
├── CONTRIBUTING.md
└── LICENSE                      # MIT License
```

---

## Development & Automated Verification

Run static checks and the full test suite from the repository root:

```bash
pyflakes vawt_post
pyright vawt_post
pytest tests -q
```

Headless Linux execution (CI / SSH):

```bash
QT_QPA_PLATFORM=offscreen pytest tests -q
```

---

## Documentation

- [User Manual](USERMANUAL.md) — Step-by-step guide to parameters, sign conventions, Workbench `dp0/FFF-{n}` workflows, `Mesh_Info` metrics, Live Monitor, Prior Run Linking, and GCI studies.
- [Changelog](CHANGELOG.md) — Release history and new feature notes.
- [Contributing](CONTRIBUTING.md) — Developer setup and pull-request checklist.

---

## License

Released under the [MIT License](LICENSE). Copyright © 2026 Fahim Montasir.
