# TurbineScope v4.1.1 (VAWT CFD Post-Processor & Studio): Complete User Manual

> **Software Version:** 4.1.1 (PySide6 / Qt 6)  
> **Target Audience:** CFD Engineers, Wind Energy Researchers, Thesis & Academic Investigators  
> **Applicable Solvers:** ANSYS Fluent (19.0 to 2026 R1+), standalone Fluent monitor logs, and ANSYS Workbench projects (`.wbpj` / `dp0/FFF-{n}`).

---

## Table of Contents

1. [System Overview & Architecture](#1-system-overview--architecture)
2. [Installation & Getting Started](#2-installation--getting-started)
3. [Global UI Navigation & Keybindings](#3-global-ui-navigation--keybindings)
4. [Input & Analysis Tab (Main Workspace)](#4-input--analysis-tab-main-workspace)
   - [Operating Point & Fluid Parameters](#operating-point--fluid-parameters)
   - [Rotor Geometry & Kinematics](#rotor-geometry--kinematics)
   - [Numerical Discretization & Convergence Controls](#numerical-discretization--convergence-controls)
   - [Specific Revolution Selection Filter](#specific-revolution-selection-filter)
   - [Monitor File Assignment & Auto-Detection](#monitor-file-assignment--auto-detection)
   - [Presets Management](#presets-management)
   - [Running the Offline Pipeline](#running-the-offline-pipeline)
5. [Results & Publication Studio Tab](#5-results--publication-studio-tab)
   - [Key Performance Indicator (KPI) Dashboard](#key-performance-indicator-kpi-dashboard)
   - [The 11 Publication-Grade Plots](#the-11-publication-grade-plots)
   - [Export & Publishing Tools](#export--publishing-tools)
6. [Live Monitor Tab (Real-Time CFD Surveillance)](#6-live-monitor-tab-real-time-cfd-surveillance)
   - [Dual-Timer Architecture (Zero-Lag Engine)](#dual-timer-architecture-zero-lag-engine)
   - [Top Toolbar & Session Controls](#top-toolbar--session-controls)
   - [Real-Time Telemetry Status Strip](#real-time-telemetry-status-strip)
   - [Collapsible Left Panel (Telemetry & Files)](#collapsible-left-panel-telemetry--files)
   - [Center Multi-Grid Plot Canvas](#center-multi-grid-plot-canvas)
   - [Collapsible Right Panel (Live KPIs & Alerts)](#collapsible-right-panel-live-kpis--alerts)
   - [Interim Snapshots & Run Finalization](#interim-snapshots--run-finalization)
7. [Workbench & GCI Studio Tab](#7-workbench--gci-studio-tab)
   - [Workbench Project Scanner & System Catalog](#workbench-project-scanner--system-catalog)
   - [1-Click Data Transfer & Live Monitoring](#1-click-data-transfer--live-monitoring)
   - [ASME V&V 20 Grid Convergence Index (GCI) Study](#asme-vv-20-grid-convergence-index-gci-study)
   - [Multi-System TSR vs Cp Curve Construction](#multi-system-tsr-vs-cp-curve-construction)
   - [Steady Convergence Comparison](#steady-convergence-comparison)
8. [Excel Workbook Architecture & Sheet Guide](#8-excel-workbook-architecture--sheet-guide)
   - [Sheet Descriptions & Engineering Metrics](#sheet-descriptions--engineering-metrics)
   - [Interpreting QA Checks & File Health Findings](#interpreting-qa-checks--file-health-findings)
9. [Troubleshooting & Diagnostic Remediation Guide](#9-troubleshooting--diagnostic-remediation-guide)

---

## 1. System Overview & Architecture

**VAWT CFD Post-Processor & Studio** is a dedicated post-processing, verification, and live tracking environment engineered specifically for Vertical Axis Wind Turbines (VAWTs), including Darrieus, H-rotor, troposkien, and cross-flow hydrokinetic rotors.

### Core Capabilities
- **Dual Operating Modes**:
  1. **Offline Analysis Pipeline**: Rigorous mathematical reduction, phase averaging, cycle-averaged convergence analysis, non-destructive Excel report compilation, and LaTeX thesis exports.
  2. **Real-Time Live Monitor**: High-frequency streaming monitor for running ANSYS Fluent simulations, detecting multi-stage time steps, tracking Aitken asymptotic convergence, and alerting on discrepancies.
- **ANSYS Workbench Deep Integration**: Direct parser for `.wbpj` project files and `_files/dp0` directory hierarchies, automatically resolving active simulation setups (`FFF`, `FFF-1`, `FFF-2`), cell counts, viscous turbulence models, and monitor assignments.
- **ASME V&V 20 Grid Convergence Index (GCI)**: Automated numerical verification assessing spatial discretization error, apparent order of accuracy ($p$), and Richardson extrapolation.
- **Non-Destructive Reporting**: Compiles comprehensive multi-sheet Excel workbooks with embedded high-resolution figures without corrupting existing sheets or formatting.

---

## 2. Installation & Getting Started

### Prerequisites
- Python 3.10, 3.11, 3.12, or 3.13.
- Operating System: Windows 10/11, Ubuntu 20.04+, or macOS 12+.

### Quick Installation
Clone the repository, navigate to the root directory, and install dependencies:

```powershell
pip install -r requirements.txt
```

### Launching the Application
Run the main graphical entry point (or launch the standalone `dist/TurbineScope.exe` built via `python build_exe.py`):

```powershell
python run_vawt_studio.py
# or
python -m vawt_post
```

---

## 3. Global UI Navigation & Keybindings

The user interface features a responsive CustomTkinter layout equipped with light/dark theme toggle, persistent settings, and standard keyboard shortcuts:

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `Ctrl + 1` | Switch to **Input** Tab | Main aerodynamic parameter setup and file picker |
| `Ctrl + 2` | Switch to **Results** Tab | KPI cards and interactive publication graph viewer |
| `Ctrl + 3` | Switch to **Live Monitor** Tab | Real-time simulation streaming dashboard |
| `Ctrl + 4` | Switch to **Workbench & GCI** Tab | Project tree, multi-grid GCI, and TSR performance |
| `Ctrl + O` | Select Watched / Working Folder | Opens directory browser for Fluent monitor files |
| `Ctrl + S` | Save Preset / Snapshot | Saves current settings as preset or captures live snapshot |
| `Ctrl + W` | Toggle Collapse Side Panels | Collapses/expands telemetry and alert panels in Live Monitor |
| `F5` | Refresh Graphs / Poll Now | Triggers immediate re-read of monitor files |
| `Esc` | Close Active Modal Dialog | Closes open edit forms or inspection popups |

---

## 4. Input & Analysis Tab (Main Workspace)

The **Input** page is the primary staging area for defining physical parameters, assigning Fluent monitor files, and running offline analysis.

```
+-----------------------------------------------------------------------------------------+
| [⚡ Preset: Rezaeiha 2018 v]  [Save Preset]  [Restore Defaults]            [Theme: Dark] |
+-----------------------------------------------------------------------------------------+
| 1. Operating Point & Rotor Geometry | 2. Discretization & Stability | 3. Fluid & Domain |
|  - U: Inflow speed (m/s)            |  - Δt: Time step fallback (s) |  - ρ: Density     |
|  - λ: Tip speed ratio (-)           |  - Tol: Stability tol (%)     |  - μ: Viscosity   |
|  - ω: Rotor speed (rad/s)           |  - N_stable: Stable revs      |  - Depth / Span   |
|  - R: Rotor radius (m)              |  - Δθ_bin: Azimuth bin (deg)  |  - Rotation: CCW  |
|  - c: Blade chord (m)               |  - Revolutions: [blank=all]   |  - Moment: Fixed  |
|  - N: Blade count (1 to 8)          |                               |                   |
+-------------------------------------+-------------------------------+-------------------+
| 4. Monitor Output Files (Auto-Detect / Manual Selection)                                |
|  - Total Rotor Moment: [ moment-rfile.out               ] [Browse]                      |
|  - Shaft Moment (subtracted): [ moment_shaft-rfile.out   ] [Browse]                      |
|  - Blade 1..N Moments: [ moment1-rfile.out, ...         ] [Browse]                      |
|  - Aerodynamic Forces (Fx, Fy): [ forcex-rfile.out, ... ] [Browse]                      |
|  [⚡ Auto-Detect from Folder]   [🧪 Generate Synthetic Data]   [🔍 Inspect File Health]   |
+-----------------------------------------------------------------------------------------+
| [▶ Run Complete Analysis Pipeline]                                                      |
+-----------------------------------------------------------------------------------------+
```

### Operating Point & Fluid Parameters
- **Free-stream Velocity ($U$)** `[m/s]`: Undisturbed inflow wind or water velocity upstream of the rotor.
- **Tip Speed Ratio ($\lambda$ or TSR)** `[-]`: Dimensionless operating ratio defined as:
  $$\lambda = \frac{\omega R}{U}$$
- **Rotor Speed ($\omega$)** `[rad/s]`: Rotational angular velocity. You may enter either $\lambda$ or $\omega$; entering one automatically derives the other.
- **Air Density ($\rho$)** `[kg/m³]`: Fluid density (default: $1.225\text{ kg/m}^3$ for standard sea-level air; $998.2\text{ kg/m}^3$ for water).
- **Dynamic Viscosity ($\mu$)** `[Pa·s]`: Fluid dynamic viscosity (default: $1.7894\times 10^{-5}\text{ Pa}\cdot\text{s}$).

### Rotor Geometry & Kinematics
- **Rotor Radius ($R$)** `[m]`: Distance from the rotational axis to the blade chord line.
- **Blade Chord ($c$)** `[m]`: Airfoil chord length. Drives the blade Reynolds number $\text{Re}_c$ and rotor solidity:
  $$\sigma = \frac{N c}{R}$$
- **Blade Count ($N$)**: Number of rotor blades ($1$ to $8$). Sliders and file slots adjust dynamically.
- **Domain Depth / Span ($H$)** `[m]`: Spanwise length. For 2D planar simulations, Fluent defaults to $1.0\text{ m}$. For 3D rotors, enter the true blade span.
- **Rotation Direction**: Choose between **CCW** (Counter-Clockwise, positive angular vector) and **CW** (Clockwise).
- **Moment Sign Convention**:
  - `fixed` (default): Uses the standard aerodynamic convention where turbine power generation is positive.
  - `auto`: Automatically infers direction from sign of torque integral.
  - `+1` / `-1`: Hardcoded sign inversion.

### Numerical Discretization & Convergence Controls
- **Time Step Fallback ($\Delta t$)** `[s]`: Used when monitor files contain step numbers without flow-time headers. When flow-time columns are present, exact timestamps are extracted automatically.
- **Stability Tolerance ($\text{Tol}$)** `[%]`: Maximum relative rev-to-rev change in revolution-averaged power coefficient ($\Delta C_p$) required to declare periodic steady state:
  $$\Delta C_{p,k} = \frac{|C_{p,k} - C_{p,k-1}|}{|C_{p,k}|} \times 100\% \le \text{Tol}$$
  Default is `5.0%`. Editable directly from both Input and Live Monitor tabs.
- **Required Stable Revolutions ($N_{\text{stable}}$)**: Number of consecutive revolutions that must satisfy the stability tolerance (default: `2`).
- **Azimuth Bin Width ($\Delta\theta_{\text{bin}}$)** `[deg]`: Discretization bin width for phase-averaging curves (default: `1.0°`).

### Specific Revolution Selection Filter
By default, the **Revolutions** field is **blank**, instructing the engine to automatically analyze the trailing $N_{\text{stable}}$ revolutions leading up to the end of the simulation.

To isolate specific revolutions:
- **Single Revolution**: Enter `16` to isolate only revolution 16.
- **Revolution Range**: Enter `14-16` to evaluate revolutions 14, 15, and 16.
- **Custom List**: Enter `10, 12, 14-16` to isolate non-consecutive cycles.

> [!IMPORTANT]
> When specific revolutions are specified, the entire Excel workbook, time-series tables, and generated figures reflect **only the specified revolution(s)**, clearing away non-selected transient data.

### Monitor File Assignment & Auto-Detection
- **Total Rotor Moment**: Output file recording torque on all rotating components (blades, hub, and shaft).
- **Shaft Moment (Optional)**: If your total monitor includes the center shaft, providing this file subtracts shaft parasitic drag from net power output:
  $$M_{\text{rotor}} = M_{\text{total}} - M_{\text{shaft}}$$
- **Blade 1..N Moments**: Individual moment monitors for each blade. Required for per-blade phase averaging and dynamic stall load tracing.
- **Aerodynamic Forces ($F_x, F_y$)**: Streamwise drag/thrust ($F_x$) and cross-stream lateral force ($F_y$).
- **⚡ Auto-Detect from Folder**:
  - Click to select any simulation folder or ANSYS Workbench directory.
  - Resolves nested paths (`..._files/dp0/FFF[-n]/Fluent/`), assigns files based on header tags and naming regexes, and populates physical parameters ($U, \omega, R, c, N, \Delta t$).

### Presets Management
Save standard turbine setups (e.g. your research turbine geometry) to avoid re-entering values:
- Click **Save Preset** to record the current parameters.
- Click **Restore Defaults** or select from built-in benchmarks:
  - *Rezaeiha 2018 (2D 2-blade/3-blade high-resolution validation)*
  - *Toor 2026 (Transient 3-blade setup)*
  - *Sandia 17-meter Darrieus*
  - *Castelli 2011 Model*

### Running the Offline Pipeline
Click **▶ Run Analysis**. A progress dialog displays stage progress:
1. File health and integrity checks.
2. Synchronized time-series alignment on common time bounds.
3. Flow-time stage discretization and $\Delta t$ detection.
4. Revolution binning and phase averaging.
5. 11 Matplotlib publication figures rendered at 300 DPI.
6. Multi-sheet Excel workbook and LaTeX thesis snippet generated.

---

## 5. Results & Publication Studio Tab

Once analysis completes, the application automatically switches to the **Results** page.

```
+-----------------------------------------------------------------------------------------+
| [KPI Cards]                                                                             |
|  Mean Cp: 0.3842   | ΔCp: +0.42%      | Stable Revs: 4     | Status: STABLE             |
|  Peak Torque: 14.2 | Thrust Ct: 0.741 | Ripple: 84.2%      | Aitken Cp_inf: 0.3855      |
+-----------------------------------------------------------------------------------------+
| Graph Selector: [ 03. Phase-Averaged Cp(θ) vs Azimuth v ]  [Autoscale: On]  [Window: All]|
+-----------------------------------------------------------------------------------------+
|                                                                                         |
|                             [ Interactive Matplotlib Canvas ]                           |
|                                                                                         |
+-----------------------------------------------------------------------------------------+
| [📋 Copy to Clipboard]  [💾 Export PNG]  [📄 Export SVG]  [📑 Export PDF]  [📊 Open Excel] |
+-----------------------------------------------------------------------------------------+
```

### Key Performance Indicator (KPI) Dashboard
- **Running Mean $C_p$**: Time-averaged power coefficient across the reported revolutions:
  $$C_p = \frac{\omega \cdot \overline{M}_{\text{rotor}}}{P_{\text{ref}}}, \quad P_{\text{ref}} = \frac{1}{2}\rho U^3 (2 R H)$$
- **$\Delta C_p$ vs Previous Rev**: Relative change from the penultimate to final reported revolution.
- **Stable Revolutions**: Count of consecutive revolutions satisfying stability tolerance.
- **Status Verdict**: **`STATUS: STABLE`** (green) or **`STATUS: NOT YET STABLE`** (amber).
- **Peak Torque ($M_{\text{peak}}$)**: Maximum instantaneous rotor torque recorded during the cycle.
- **Thrust Coefficient ($C_t$)**: Streamwise rotor force normalized by $\frac{1}{2}\rho U^2 (2 R H)$.
- **Torque Ripple Factor**: Peak-to-peak fluctuation normalized by cycle mean:
  $$\text{Ripple} = \frac{C_{p,\text{max}} - C_{p,\text{min}}}{C_{p,\text{mean}}}$$
- **Aitken Asymptotic Limit ($C_{p,\infty}$)**: Accelerates convergence estimation using non-linear Aitken extrapolation over consecutive cycles:
  $$C_{p,\infty} = x_k - \frac{(x_k - x_{k-1})^2}{x_k - 2x_{k-1} + x_{k-2}}$$

### The 11 Publication-Grade Plots
Select any plot from the dropdown menu:

1. **01. Cp History**: Full continuous time series of instantaneous $C_p(t)$ with cycle endpoints marked.
2. **02. Revolution-Averaged Cp**: Bar chart displaying mean $C_p$ per revolution with stability threshold bands.
3. **03. Phase-Averaged Cp vs Azimuth**: Single-revolution $C_p(\theta)$ curve ($0^\circ$ to $360^\circ$) highlighting azimuthal torque production zones.
4. **04. Blade Torques vs Azimuth**: Individual torque curves for all $N$ blades plotted simultaneously, displaying blade phase offsets ($120^\circ$ for 3 blades, $180^\circ$ for 2 blades).
5. **05. Aerodynamic Force History ($C_t, C_y$)**: Thrust and lateral side forces across flow time.
6. **06. Cm History**: Moment coefficient time series.
7. **07. Convergence & Spread ($\Delta C_p$ %)**: Step-by-step decay of rev-to-rev variation vs tolerance.
8. **08. Cp Azimuthal Heatmap**: 2D contour of azimuth $\theta$ vs revolution index $k$, visualizing cycle periodicity and vortex shed drift.
9. **09. Spectral Power Density (FFT)**: Fast Fourier Transform of torque signal showing fundamental rotor frequency ($f_0$), blade-passing frequency ($f_{\text{bpf}} = N f_0$), and harmonics.
10. **10. Wall $y^+$ & Boundary Layer Diagnostics**: Tracks near-wall mesh resolution across the revolution cycle.
11. **11. Kinematics ($\alpha_{\text{eff}}, W_{\text{rel}}$)**: Effective angle of attack and relative velocity vectors vs azimuth, highlighting deep dynamic stall boundaries ($|\alpha| > \alpha_{\text{static stall}}$).

### Export & Publishing Tools
- **Copy to Clipboard**: Copies the active publication-grade figure directly to the operating system clipboard for direct pasting into Microsoft Word, PowerPoint, or Overleaf.
- **Export Formats**: One-click export to **PNG** (300 DPI raster), **SVG** (vector graphics for publication), and **PDF** (vector print quality).
- **Open Excel Results**: Launches the generated multi-sheet Excel workbook in your default spreadsheet viewer.

---

## 6. Live Monitor Tab (Real-Time CFD Surveillance)

The **Live Monitor** is an independent, real-time streaming dashboard engineered to track actively running ANSYS Fluent simulations. It is completely decoupled from the main Input tab, allowing users to monitor overnight cluster jobs, watch folder writes, or track local solver execution without stopping the solver.

```
+---------------------------------------------------------------------------------------------------+
| [⏹ Stop Monitoring] Refresh: [15 v] sec | Preset: [Default v] | ⏱ Next: 0:09 | [Refresh now] [⏸] |
+---------------------------------------------------------------------------------------------------+
| ● Monitoring | Step: 10,084 | Time: 3.1734s | Δt: 2.448e-04s (Stage 2/2) | Revs: 18.01 | Speed: 8.2 s/s |
+-------------------------------+-----------------------------------+-------------------------------+
| Telemetry & Files         [<] | 2×2 / 3×2 Multi-Plot Canvas       | Live KPIs & Alerts        [>] |
|                               |                                   |                               |
| Parameters in Use [Detect][Edit]| [ 01. Cp History ] [ 02. Rev Cp ]| STATUS: STABLE                |
| - U=9.0 m/s | R=0.515m | N=3  |                                   | Mean Cp: 0.1390               |
| - λ=2.042   | Model: TransSST |                                   | ΔCp: -1.03%                   |
|                               | [ 03. Phase Cp   ] [ 04. Torques ]| Stable Revs: 8                |
| Entered vs Detected           |                                   | Peak Torque: 6.92 N·m         |
| - Speed ω: ✓ (43.85 rad/s)    |                                   | Live Alerts Feed:             |
| - Time step: ✓ (2 stages)     |                                   | [00:57:11] Rev 18 complete    |
| File Health List              | Layout: [ 2×2 Grid v ]            | [00:57:11] Convergence reached|
| - moment1-rfile.out (10,084)  | Window: [ Last 3 revs v ]         |                               |
| - moment2-rfile.out (10,084)  | Tol (%): [ 5.0 ]  [x] Autoscale   |                               |
+-------------------------------+-----------------------------------+-------------------------------+
```

### Dual-Timer Architecture (Zero-Lag Engine)
To prevent GUI freezes when reading multi-gigabyte Fluent output files:
1. **Lightweight File Poller (10s cadence)**: Performs incremental byte-offset reads, checking file modified timestamps and appending new rows to memory in milliseconds without touching plots.
2. **Heavy Graph Refresh (User-Configured, default 15s - 2 min)**: Asynchronously updates the Matplotlib canvas, decodes revolution metrics, and recalculates FFT spectra.

### Top Toolbar & Session Controls
- **Start / Stop Monitoring**: Initiates or terminates background file surveillance. If no folder is configured, prompts for directory selection.
- **Refresh Interval Dropdown**: Select between `5s`, `10s`, `15s`, `30s`, `60s`, `120s`, or `300s`.
- **Next Countdown Timer**: Displays real-time countdown to the next complete visual refresh.
- **Refresh Now**: Forces an immediate incremental read and graph redraw.
- **Pause / Resume**: Suspends plotting while keeping background data acquisition active.
- **Watch Folder...**: Prompts to select a `dp0/FFF-{n}` Workbench system folder or Fluent run folder. Automatically resolves `dp0/FFF-{n}/Fluent/` (even before `.out` files exist), updates solver & mesh metadata, and posts an auto-detected mesh/setup note in **Alerts & Updates**.
- **Prior Run Link (Sidebar Card)**: Link an initial backup folder or `.zip` archive (`backup.zip`) either before starting or live mid-run to deduplicate checkpoint-rollback overlaps, reconstruct missing crash-gap timesteps via phase-harmonic recovery, and display continuity status badges (`Linked`, `Stitched`, `Recovered`, `Warning`).

### Real-Time Telemetry Status Strip
- **Status Badge**:
  - `● Monitoring` (green): Files are actively growing.
  - `⚠ Stalled?` (amber): Solver has not written new data for over 60 seconds.
  - `⏳ Waiting Files` (blue): Awaiting Fluent output creation.
  - `⏸ Paused` (cyan): Data polling paused by user.
- **Current Step**: Latest synchronized time-step number.
- **Flow Time**: Cumulative physical flow time ($t$).
- **Active $\Delta t$ & Multi-Stage Indicator**: Shows active time-step size and phase index (e.g., `Stage 2/2`).
- **Revs**: Total turbine revolutions completed ($t / T_{\text{rev}}$).
- **Azimuth ($\theta$)**: Instantaneous angular position of Blade 1 ($0.0^\circ$ to $360.0^\circ$).
- **Speed (steps/s)**: Real-time computational throughput of the CFD solver.
- **ETA**: Wall-clock time estimated to reach the target revolution count.
- **Freshness**: Elapsed seconds since the solver last modified the monitor file on disk.

### Collapsible Left Panel (Telemetry & Files)
- **Parameters in Use**:
  - Displays $U, R, N, \lambda, \rho, \text{Tol}$, and detected turbulence model.
  - **⚡ Detect Button**: Prompts folder selection (if unset), auto-detects all parameters from Fluent setup/report files, and displays a summary dialog.
  - **✏️ Edit Button**: Opens the **Live Parameters Modal Dialog** to adjust parameters, stability tolerance, or fluid properties live without restarting the solver.
- **Derived Physics Card**: Instantaneous $\omega$, rotor RPM, revolution period $T_{\text{rev}}$, active time-step size, azimuthal degrees per step ($\Delta\theta$), and steps per revolution.
- **Time Step Stages ($\Delta t$)**:
  - Automatically identifies when the CFD engineer refines the time step mid-run (e.g. coarse $1.0^\circ/\text{step}$ initialization transitioned to fine $0.25^\circ/\text{step}$).
  - Displays start/end steps, duration in seconds, revolution count, and resolution for each stage.
- **Entered vs Detected Consistency Card**:
  - **Speed $\omega$**: Computes real-time FFT spectrum on live torque and validates entered speed against signal dynamics. Displays `✓` (PASS, $\le 10\%$) or `⚠` (WARNING).
  - **Time Step**: Compares entered $\Delta t$ against median step size. Displays `✓` (PASS, $\le 2\%$) or `⚠` (WARNING).
- **File Health List**: Live status of all monitored report files on disk, displaying row counts, last step, and file freshness.

### Center Multi-Grid Plot Canvas
- **Layout Selector**:
  - `2×2 Grid`: Standard 4-plot view (Cp History, Rev Cp, Phase Cp, Blade Torques).
  - `1×1 Single`: Maximized view of a single plot.
  - `2×1 Stack`: Two wide plots stacked vertically.
  - `3×2 Grid`: 6-plot comprehensive overview.
  - `🔍 Focus Mode`: Enlarged primary plot with secondary plots minimized below.
- **Window Dropdown**: Filters plotting window to `Last 1 rev`, `Last 3 revs`, `Last 5 revs`, or `All data`.
- **Autoscale Checkbox**: Toggles dynamic Y-axis fitting or fixed bounds.
- **Tolerance % Input (`Tol (%)`)**: Editable text box allowing live adjustments of convergence tolerance (default: `5.0%`).

### Collapsible Right Panel (Live KPIs & Alerts)
- **Live KPI Chips**: Running Mean $C_p$, Rev-to-Rev $\Delta C_p$, Stable Revs, Peak Torque, Thrust $C_t$, Torque Ripple %, and Aitken Extrapolated $C_{p,\infty}$.
- **Live Alerts Feed**: Timestamped chronological log recording stage transitions, revolution completions, stability achievements, and solver stalls.

### Interim Snapshots & Run Finalization
- **Snapshot**: Captures an interim multi-sheet Excel workbook and figure bundle into `<watch_dir>/live_post_output/` without stopping the simulation.
- **Finalize**: Stops live monitoring and immediately executes the full offline analytical pipeline on all accumulated data, generating final thesis figures and full Excel reports.

---

## 7. Workbench & GCI Studio Tab

The **Workbench & GCI** studio provides direct integration with ANSYS Workbench project databases (`.wbpj`).

### Workbench Project Scanner & System Catalog
- Click **Browse Workbench Project (.wbpj)** or select any project directory.
- The scanner parses `_files/dp0/` and extracts every Fluent system (e.g. `FFF`, `FFF-1`, `FFF-2`).
- Displays an interactive catalog with:
  - System ID and active writing status (`Active Run`).
  - Total mesh cell count and node count.
  - Viscous turbulence model (e.g., Transition SST, $k$-$\omega$ SST).
  - Flow velocity ($U$), rotational speed ($\omega$), and time step ($\Delta t$).
  - Identified moment and force monitor files.

### 1-Click Data Transfer & Live Monitoring
- **⚡ Load Selected System into Main Analysis Page**: Transfers all extracted parameters, cell counts, and file paths into the Input page for immediate offline processing.
- **📡 Monitor Selected System in Live Monitor**: Transitions directly to the Live Monitor tab and begins live streaming the selected Workbench simulation.

### ASME V&V 20 Grid Convergence Index (GCI) Study
The GCI engine implements the standard **ASME V&V 20-2009** protocol for discretization error estimation:

1. **Select 3 Grids**: Choose Fine ($h_1$), Medium ($h_2$), and Coarse ($h_3$) meshes from the scanned systems.
2. Click **Compute ASME GCI**:
   - Calculates effective grid refinement ratios:
     $$r_{21} = \left(\frac{N_1}{N_2}\right)^{1/D}, \quad r_{32} = \left(\frac{N_2}{N_3}\right)^{1/D}$$
   - Solves for apparent order of accuracy ($p$) via fixed-point iteration:
     $$p = \frac{1}{\ln(r_{21})} |\ln|\epsilon_{32}/\epsilon_{21}| + q(p)|$$
   - Computes extrapolated value ($\phi_{\text{ext}}^{21}$), approximate relative error ($e_a^{21}$), extrapolated relative error ($e_{\text{ext}}^{21}$), and Fine-Grid GCI index:
     $$\text{GCI}_{\text{fine}}^{21} = \frac{1.25 \cdot e_a^{21}}{r_{21}^p - 1}$$
3. **Export Table**: Export formatted tables directly to **LaTeX** (ready for thesis inclusion) or **Markdown**.

### Multi-System TSR vs Cp Curve Construction
- Automatically gathers converged mean $C_p$ values across multiple Workbench systems operating at different tip speed ratios ($\lambda$).
- Fits cubic splines through data points to construct the complete turbine power performance curve.
- Identifies optimal operating point $(\lambda_{\text{opt}}, C_{p,\text{max}})$.

---

## 8. Excel Workbook Architecture & Sheet Guide

Every completed run exports an Excel workbook (`.xlsx`). The workbook is organized as follows:

| Sheet Name | Content & Purpose |
| :--- | :--- |
| **Summary** | High-level engineering dashboard: key KPIs, convergence verdict, setup summary, continuity audit, and embedded `12_dashboard.png` figure. |
| **Graphs** | Non-destructive gallery embedding publication-grade figures cleanly sized onto the sheet. |
| **Parameters** | Complete rotor, fluid, kinematic, reproducibility SHA-256 hashes, and CFD case setup provenance. |
| **Mesh_Info** | Dedicated Workbench/HDF5 mesh audit: `.cas.h5`/`.msh`/`.mshdb`/`.agdb`/`report.xml` artifacts, domain sizing & blockage ratio, cell & boundary zone breakdowns, first-layer blade height $y_1$ & estimated $y^+$, **Equiangle Skewness**, **Orthogonal Quality**, **Aspect Ratio** (core vs. BL), **Growth Rate**, and solver setup notes. |
| **Revolutions** | Cycle-by-cycle metrics table ($k, t_{\text{start}}, t_{\text{end}}, C_{p,\text{mean}}, C_{m,\text{mean}}, C_t, C_y, \Delta C_p\%$, stability & selection flags). |
| **Azimuth_Cp** | Phase-averaged $C_p(\theta)$ and $C_m(\theta)$ (plus $C_t, C_y$) tabulated across $0^\circ$ to $360^\circ$ at $\Delta\theta_{\text{bin}}$ resolution. |
| **Blade_Azimuth** | Phase-averaged individual blade moments ($M_{b1}$ to $M_{bN}$) and blade $C_p$ vs azimuth. |
| **Alpha_Re** | Kinematic evaluation: geometric angle of attack $\alpha(\theta)$, relative velocity ratio $W/U(\theta)$, and blade chord Reynolds number $\text{Re}_c(\theta)$. |
| **Processed** | Downsampled processed time-series data for quick external inspection. |
| **Raw_Data** | Synchronized monitor time series (plus continuity recovery flags if prior runs were linked). |
| **QA_Checks** | Structured numerical quality assurance table checking monotonicity, duplicate steps, integer step alignment, start azimuth $\theta_0$, and Betz limits. |
| **Warnings** | Detailed log of triggered setup, continuity, and convergence warnings alongside engineering remediations. |
| **File_Health** | Diagnostic log detailing monitor file parsing health, header validations, mid-spin $\theta_0$ detection, and data continuity checks. |
| **Captions** | Publication-ready academic captions and figure explanations formatted for papers and theses. |
| **Stages** | Multi-phase time-step breakdown table listing step intervals, flow-time ranges, $\Delta\theta$ resolution, and per-stage $C_p$ (when multiple $\Delta t$ phases exist). |

---

## 9. Troubleshooting & Diagnostic Remediation Guide

### Common Warnings & Remediations

#### 1. Negative Mean Power Coefficient ($C_p < 0$)
- **Cause**: Rotor moment was monitored with opposite sign convention, or rotation direction is inverted.
- **Remediation**: In the **Input** tab, check **Rotation Direction** (switch CCW to CW) or set **Moment Sign** to `-1`.

#### 2. Rotor Speed Mismatch Alert ($\omega_{\text{det}} \ne \omega_{\text{entered}}$)
- **Cause**: The dominant frequency in the torque monitor FFT does not match the blade-passing frequency ($N \cdot \omega / 2\pi$).
- **Remediation**: Check the angular velocity specified in Fluent under *Cell Zone Conditions -> Mesh Motion*. Ensure units in Fluent (rad/s vs rpm) match your input.

#### 3. Azimuth Step Resolution $\Delta\theta > 1.0^\circ/\text{step}$
- **Cause**: Time step $\Delta t$ is too coarse relative to rotor speed $\omega$.
- **Remediation**: For high-fidelity VAWT CFD, standard practice dictates $\Delta\theta \le 1.0^\circ/\text{step}$ (or $\le 0.5^\circ$ for dynamic stall resolution). Refine $\Delta t$ in Fluent:
  $$\Delta t \le \frac{1.0^\circ \times \pi / 180^\circ}{\omega}$$

#### 4. Simulation Not Yet Stable
- **Cause**: The relative change in $C_p$ across the last $N_{\text{stable}}$ revolutions exceeds the tolerance setting (e.g. $> 5\%$).
- **Remediation**: VAWT simulations typically require 15 to 25 full revolutions to shed starting vortex transients and establish limit-cycle periodicity. Continue the simulation for additional revolutions.

#### 5. Missing Blade File Reconstructable
- **Cause**: One blade monitor file was omitted or corrupted, but total rotor moment and remaining blades are available.
- **Remediation**: In the **File Health** inspection dialog, click **Apply suggested repair** to mathematically reconstruct the missing blade monitor:
  $$M_{\text{missing}} = M_{\text{total}} - \sum M_{\text{other blades}} - M_{\text{shaft}}$$

#### 6. Live Monitor Status Shows "Stalled / Finished?"
- **Cause**: Fluent has stopped writing to monitor files for more than 60 seconds (solver finished, paused, or crashed).
- **Remediation**: Check the Fluent solver console or job scheduler to verify if iterations are progressing.

