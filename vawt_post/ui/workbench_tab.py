# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: Workbench Project & GCI Studio UI
==========================================================
Interactive interface for discovering native ANSYS Workbench (.wbpj) projects,
inspecting multi-grid systems, auto-populating simulation parameters, and
performing automated ASME V&V 20 Grid Convergence Index (GCI) analyses.
"""
from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Any

from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import filedialog, messagebox, ttk, FigureCanvasTkAgg
import matplotlib.pyplot as plt

from vawt_post.engine.workbench import (
    WorkbenchProject, FluentSystemInfo, scan_workbench_project,
    evaluate_tsr_sweep, plot_tsr_sweep_curve, evaluate_systems_comparison,
    tsr_sweep_to_formatted_table, tsr_sweep_to_markdown_table,
    systems_comparison_to_formatted_table, systems_comparison_to_markdown_table,
    plot_steady_convergence, evaluate_steady_convergence, steady_convergence_to_formatted_table,
    _extract_system_converged_torque
)
from vawt_post.engine.gci_engine import (
    GridLevelData, GCIReport, plot_gci_curve,
    MultiGridStudyReport, compute_multi_grid_study, plot_multi_grid_curve
)
from vawt_post.ui.components import ToolTip, SectionCard


class WorkbenchStudioFrame(ctk.CTkFrame):
    """
    Workbench & GCI Studio tab interface.
    """

    def __init__(self, parent, app=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.app = app
        self.project: Optional[WorkbenchProject] = None
        self.current_system: Optional[FluentSystemInfo] = None
        self.gci_report: Optional[GCIReport] = None
        self.multi_grid_report: Optional[MultiGridStudyReport] = None
        self.tsr_sweep_result: Optional[Dict[str, Any]] = None
        self.comp_result: Optional[Dict[str, Any]] = None
        self.current_plot_type: str = "none"
        self.gci_canvas: Optional[FigureCanvasTkAgg] = None
        self._active_mpl_fig = None
        self._scan_in_progress: bool = False

        self._build_ui()

    def _build_ui(self):
        """Construct the 2-column or split layout: Left Workbench explorer, Right GCI studio."""
        # Top Project Selector Bar
        top_bar = ctk.CTkFrame(self, corner_radius=10)
        top_bar.pack(fill="x", padx=6, pady=(4, 8))

        ctk.CTkLabel(
            top_bar,
            text="📁 ANSYS Workbench Project:",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left", padx=(14, 8), pady=10)

        self.entry_proj_path = ctk.CTkEntry(
            top_bar,
            placeholder_text="Select or drop .wbpj project file or Workbench directory...",
            width=500
        )
        self.entry_proj_path.pack(side="left", fill="x", expand=True, padx=6, pady=10)

        ctk.CTkButton(
            top_bar,
            text="Browse...",
            width=90,
            command=self._on_browse_project
        ).pack(side="left", padx=4, pady=10)

        ctk.CTkButton(
            top_bar,
            text="⚡ Scan Project",
            width=120,
            fg_color=("teal", "#0D9488"),
            hover_color=("dark cyan", "#0F766E"),
            command=self._on_scan_project
        ).pack(side="left", padx=(4, 14), pady=10)

        # Main Body: Split into Left (Project Systems & Parameter Loader) and Right (GCI Studio)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=2, pady=2)
        body.grid_columnconfigure(0, weight=4)
        body.grid_columnconfigure(1, weight=5)
        body.grid_rowconfigure(0, weight=1)

        # Left Column: Project Systems & Auto-Loader
        left_col = ctk.CTkScrollableFrame(body, corner_radius=10)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        self._build_left_column(left_col)

        # Right Column: GCI Studio & Uncertainty Visualizer
        right_col = ctk.CTkScrollableFrame(body, corner_radius=10)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)
        self._build_right_column(right_col)

    def _build_left_column(self, parent):
        """Build the left panel containing project systems overview and auto-loader."""
        # 1. Project Summary Card
        self.card_summary = SectionCard(parent, title="Project Overview", subtitle="Design Points & Systems")
        self.card_summary.pack(fill="x", pady=(0, 8))

        self.lbl_proj_info = ctk.CTkLabel(
            self.card_summary.content_frame,
            text="No project loaded. Click 'Browse' or select a .wbpj project file.",
            font=ctk.CTkFont(size=11),
            justify="left",
            wraplength=420,
            anchor="w"
        )
        self.lbl_proj_info.pack(fill="x", padx=4, pady=4)

        # 2. Systems Catalog (Multi-System Table: supports 3, 5, 10, 20-30 systems)
        self.card_catalog = SectionCard(parent, title="Project Systems Catalog", subtitle="All Discovered Systems & Mesh Levels")
        self.card_catalog.pack(fill="x", pady=8)

        tree_frame = ctk.CTkFrame(self.card_catalog.content_frame, fg_color="transparent")
        tree_frame.pack(fill="x", padx=4, pady=4)

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical")
        self.tree_systems = ttk.Treeview(
            tree_frame,
            columns=("cells", "u", "omega", "dt", "revs", "status"),
            show="headings",
            height=6,
            selectmode="extended",
            yscrollcommand=tree_scroll.set
        )
        tree_scroll.config(command=self.tree_systems.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree_systems.pack(side="left", fill="x", expand=True)

        self.tree_systems.heading("cells", text="Mesh Cells (N)")
        self.tree_systems.heading("u", text="U (m/s)")
        self.tree_systems.heading("omega", text="ω (rad/s)")
        self.tree_systems.heading("dt", text="Δt (s)")
        self.tree_systems.heading("revs", text="Revs")
        self.tree_systems.heading("status", text="Monitors Found")

        self.tree_systems.column("cells", width=95, anchor="center")
        self.tree_systems.column("u", width=65, anchor="center")
        self.tree_systems.column("omega", width=70, anchor="center")
        self.tree_systems.column("dt", width=85, anchor="center")
        self.tree_systems.column("revs", width=55, anchor="center")
        self.tree_systems.column("status", width=110, anchor="w")

        self.tree_systems.bind("<<TreeviewSelect>>", self._on_tree_system_select)

        # Quick action buttons row below table
        cat_btn_row = ctk.CTkFrame(self.card_catalog.content_frame, fg_color="transparent")
        cat_btn_row.pack(fill="x", padx=4, pady=(4, 2))

        ctk.CTkButton(
            cat_btn_row, text="☑ Select All", width=90, height=26,
            fg_color=("#1D4ED8", "#2563EB"), command=self._select_all_systems
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            cat_btn_row, text="☐ Clear Sel", width=85, height=26,
            fg_color=("#4B5563", "#374151"), command=self._deselect_all_systems
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            cat_btn_row, text="⚡ Auto-Meshes", width=105, height=26,
            fg_color=("#D97706", "#B45309"), command=self._select_all_meshed_systems
        ).pack(side="left", padx=2)

        self.lbl_catalog_selection = ctk.CTkLabel(
            self.card_catalog.content_frame,
            text="Selected for Study: 0 systems (Click, Shift+Click, or Ctrl+Click rows)",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=("gray40", "gray70"),
            anchor="w"
        )
        self.lbl_catalog_selection.pack(fill="x", padx=6, pady=(2, 4))


        # 3. Detected Systems Dropdown & Detailed Specifications
        self.card_systems = SectionCard(parent, title="System Specifications", subtitle="Operating Setup & Monitor Details")
        self.card_systems.pack(fill="x", pady=8)

        sys_select_row = ctk.CTkFrame(self.card_systems.content_frame, fg_color="transparent")
        sys_select_row.pack(fill="x", pady=(2, 6))

        ctk.CTkLabel(sys_select_row, text="Select System:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=4)
        self.menu_systems = ctk.CTkOptionMenu(
            sys_select_row,
            values=["No systems found"],
            width=200,
            command=self._on_select_system
        )
        self.menu_systems.pack(side="left", padx=6)

        # System details text box
        self.txt_sys_details = ctk.CTkTextbox(self.card_systems.content_frame, height=160, font=("Consolas", 10))
        self.txt_sys_details.pack(fill="x", padx=4, pady=6)
        self.txt_sys_details.insert("1.0", "System details will appear here upon scanning...")
        self.txt_sys_details.configure(state="disabled")

        # 1-Click Load into Main Input Page Button
        self.btn_load_to_input = ctk.CTkButton(
            self.card_systems.content_frame,
            text="⚡ Load Selected System into Main Analysis Page",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=34,
            fg_color=("#2563EB", "#1D4ED8"),
            hover_color=("#1D4ED8", "#1E40AF"),
            command=self._load_selected_into_input
        )
        self.btn_load_to_input.pack(fill="x", padx=4, pady=(6, 2))
        ToolTip(self.btn_load_to_input, "Transfer all extracted parameters, cell counts, and monitor files into the main Input page.")

        self.btn_load_to_live = ctk.CTkButton(
            self.card_systems.content_frame,
            text="📡 Monitor Selected System in Live Monitor",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=32,
            fg_color=("#0891B2", "#0E7490"),
            hover_color=("#0E7490", "#155E75"),
            command=self._load_selected_into_live_monitor
        )
        self.btn_load_to_live.pack(fill="x", padx=4, pady=(2, 2))
        ToolTip(self.btn_load_to_live, "Open this Workbench Fluent system directly in the dedicated Live Monitor dashboard.")

        self.btn_plot_steady = ctk.CTkButton(
            self.card_systems.content_frame,
            text="📈 Plot Steady Convergence (Moment & Forces)",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=32,
            fg_color=("#10B981", "#059669"),
            hover_color=("#059669", "#047857"),
            command=self._on_plot_steady_clicked
        )
        self.btn_plot_steady.pack(fill="x", padx=4, pady=(2, 4))
        ToolTip(self.btn_plot_steady, "Display combined Total Rotor Moment (all blades + shaft) and Aerodynamic Forces (Fx, Fy) convergence curves.")

    def _build_right_column(self, parent):
        """Build the right panel containing Multi-Grid ASME, TSR Curve, and Comparative Analysis."""
        card_studio = SectionCard(
            parent,
            title="Multi-System & Multi-Grid Studio",
            subtitle="ASME V&V 20 · TSR Sweep · System Comparison"
        )
        card_studio.pack(fill="x", pady=(0, 8))

        # Study Mode Selector Bar
        mode_bar = ctk.CTkFrame(card_studio.content_frame, fg_color="transparent")
        mode_bar.pack(fill="x", padx=4, pady=(2, 6))

        ctk.CTkLabel(
            mode_bar, text="Study Mode:",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(side="left", padx=(2, 6))

        self.v_study_mode = ctk.StringVar(value="📊 Grid Dependency Study (Multi-Mesh ASME)")
        self.menu_study_mode = ctk.CTkOptionMenu(
            mode_bar,
            variable=self.v_study_mode,
            values=[
                "📊 Grid Dependency Study (Multi-Mesh ASME)",
                "📈 TSR vs Cp Performance Curve (TSR Sweep)",
                "⚖ Multi-System Comparative Analysis",
                "🔄 Steady Aerodynamics (Total Moment & Forces)",
            ],
            width=340,
            command=self._on_study_mode_changed
        )
        self.menu_study_mode.pack(side="left", padx=4)

        # Dynamic Description Label
        self.lbl_mode_desc = ctk.CTkLabel(
            card_studio.content_frame,
            text="Evaluates spatial discretization uncertainty across arbitrary mesh counts (N ≥ 2).\n"
                 "For N ≥ 3, calculates apparent order p, Richardson extrapolation, and local GCI bands.",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray70"),
            justify="left",
            wraplength=600,
        )
        self.lbl_mode_desc.pack(anchor="w", padx=6, pady=(0, 6))

        # Mode Specific Configuration Frames Container
        self.opt_container = ctk.CTkFrame(card_studio.content_frame, fg_color=("gray92", "gray18"))
        self.opt_container.pack(fill="x", padx=4, pady=4)

        # 1. Grid Dependency Options
        self.frame_opt_grid = ctk.CTkFrame(self.opt_container, fg_color="transparent")
        self.frame_opt_grid.pack(fill="x", padx=6, pady=4)

        row_g1 = ctk.CTkFrame(self.frame_opt_grid, fg_color="transparent")
        row_g1.pack(fill="x", pady=2)
        ctk.CTkLabel(row_g1, text="Spatial Dimension:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=4)
        self.v_gci_dim = ctk.StringVar(value="2D (D=2)")
        ctk.CTkOptionMenu(row_g1, variable=self.v_gci_dim, values=["2D (D=2)", "3D (D=3)"], width=110).pack(side="left", padx=4)

        self.v_gci_override_checks = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            row_g1,
            text="⚠️ Override Guardrails",
            variable=self.v_gci_override_checks,
            font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=12)

        # 2. TSR Sweep Options
        self.frame_opt_tsr = ctk.CTkFrame(self.opt_container, fg_color="transparent")

        row_t1 = ctk.CTkFrame(self.frame_opt_tsr, fg_color="transparent")
        row_t1.pack(fill="x", pady=2)
        ctk.CTkLabel(row_t1, text="Rotor Radius R (m):", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=4)
        self.v_tsr_radius = ctk.StringVar(value="0.35")
        ctk.CTkEntry(row_t1, textvariable=self.v_tsr_radius, width=70).pack(side="left", padx=4)

        ctk.CTkLabel(row_t1, text="Rotor Depth (m):", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=10)
        self.v_tsr_depth = ctk.StringVar(value="1.0")
        ctk.CTkEntry(row_t1, textvariable=self.v_tsr_depth, width=60).pack(side="left", padx=4)

        row_t2 = ctk.CTkFrame(self.frame_opt_tsr, fg_color="transparent")
        row_t2.pack(fill="x", pady=(2, 2))
        self.v_tsr_benchmark = ctk.StringVar(value="")
        ctk.CTkLabel(row_t2, text="Benchmark:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=4)
        ctk.CTkEntry(row_t2, textvariable=self.v_tsr_benchmark, width=220, placeholder_text="Optional .xlsx / .csv").pack(side="left", padx=4)
        ctk.CTkButton(
            row_t2, text="Browse", width=70, height=24,
            fg_color=("#475569", "#334155"),
            command=self._on_browse_benchmark
        ).pack(side="left", padx=4)

        # 3. System Comparison Options
        self.frame_opt_comp = ctk.CTkFrame(self.opt_container, fg_color="transparent")
        ctk.CTkLabel(
            self.frame_opt_comp,
            text="Compares boundary conditions, meshes, time stepping, and aerodynamics across all selected systems.",
            font=ctk.CTkFont(size=11, slant="italic")
        ).pack(anchor="w", padx=4, pady=2)

        # Legacy variables kept for 100% backward compatibility
        self.v_gci_sys1 = ctk.StringVar(value="FFF")
        self.v_gci_n1 = ctk.StringVar(value="188181")
        self.v_gci_phi1 = ctk.StringVar(value="0.2397")
        self.menu_gci_sys1 = ctk.CTkOptionMenu(self.frame_opt_grid, variable=self.v_gci_sys1, values=["FFF"], width=10)
        self.v_gci_sys2 = ctk.StringVar(value="FFF-2")
        self.v_gci_n2 = ctk.StringVar(value="118964")
        self.v_gci_phi2 = ctk.StringVar(value="0.3259")
        self.menu_gci_sys2 = ctk.CTkOptionMenu(self.frame_opt_grid, variable=self.v_gci_sys2, values=["FFF-2"], width=10)
        self.v_gci_sys3 = ctk.StringVar(value="FFF-1")
        self.v_gci_n3 = ctk.StringVar(value="64699")
        self.v_gci_phi3 = ctk.StringVar(value="0.3376")
        self.menu_gci_sys3 = ctk.CTkOptionMenu(self.frame_opt_grid, variable=self.v_gci_sys3, values=["FFF-1"], width=10)

        # Action Buttons Row
        btn_box = ctk.CTkFrame(card_studio.content_frame, fg_color="transparent")
        btn_box.pack(fill="x", pady=6)

        self.btn_run_study = ctk.CTkButton(
            btn_box,
            text="🚀 Compute Selected Study",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("teal", "#0D9488"),
            hover_color=("dark cyan", "#0F766E"),
            command=self._on_run_selected_study
        )
        self.btn_run_study.pack(side="left", padx=4)

        ctk.CTkButton(
            btn_box,
            text="⚡ Select All Systems",
            width=150,
            fg_color="transparent",
            border_width=1,
            command=self._select_all_systems
        ).pack(side="left", padx=4)

        # Results Copy Bar
        copy_bar = ctk.CTkFrame(card_studio.content_frame, fg_color="transparent")
        copy_bar.pack(fill="x", padx=2, pady=(6, 2))

        ctk.CTkButton(
            copy_bar, text="📋 Copy Clean Table", width=125, height=26,
            fg_color="transparent", border_width=1, command=self._copy_study_formatted
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            copy_bar, text="📑 Copy Markdown", width=120, height=26,
            fg_color="transparent", border_width=1, command=self._copy_study_markdown
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            copy_bar, text="📄 Copy LaTeX", width=105, height=26,
            fg_color="transparent", border_width=1, command=self._copy_study_latex
        ).pack(side="left", padx=2)

        # Study Results Text Display (Monospace for perfect alignment)
        self.txt_gci_output = ctk.CTkTextbox(card_studio.content_frame, height=200, font=("Consolas", 10))
        self.txt_gci_output.pack(fill="x", padx=2, pady=4)
        self.txt_gci_output.insert("1.0", "Study calculations & verification table will appear here...")
        self.txt_gci_output.configure(state="disabled")

        # Matplotlib Plot Container
        self.gci_plot_container = ctk.CTkFrame(card_studio.content_frame, height=260, corner_radius=8)
        self.gci_plot_container.pack(fill="x", padx=2, pady=(6, 2))

    def _on_browse_benchmark(self):
        """Open file dialog to browse for experimental/CFD benchmark .xlsx or .csv."""
        file_path = filedialog.askopenfilename(
            title="Select Benchmark Curve (.xlsx / .csv)",
            filetypes=[("Excel or CSV", "*.xlsx *.csv"), ("All Files", "*.*")]
        )
        if file_path:
            self.v_tsr_benchmark.set(file_path)

    def _on_browse_project(self):
        """Open file dialog to browse for .wbpj or folder."""
        file_path = filedialog.askopenfilename(
            title="Select ANSYS Workbench Project (.wbpj)",
            filetypes=[("ANSYS Workbench Project", "*.wbpj"), ("All Files", "*.*")]
        )
        if file_path:
            self.entry_proj_path.delete(0, "end")
            self.entry_proj_path.insert(0, file_path)
            self._on_scan_project()

    def _clear_study_state(self):
        """Reset cached study results and plots when loading a new Workbench project."""
        self.current_system = None
        self.gci_report = None
        self.multi_grid_report = None
        self.tsr_sweep_result = None
        self.comp_result = None
        self.current_plot_type = "none"
        if hasattr(self, "v_tsr_benchmark"):
            self.v_tsr_benchmark.set("")
        if hasattr(self, "txt_gci_output"):
            self.txt_gci_output.configure(state="normal")
            self.txt_gci_output.delete("1.0", "end")
            self.txt_gci_output.configure(state="disabled")
        if hasattr(self, "gci_plot_container"):
            for child in self.gci_plot_container.winfo_children():
                child.destroy()
        if self._active_mpl_fig is not None:
            try:
                plt.close(self._active_mpl_fig)
            except Exception:
                pass
            self._active_mpl_fig = None
        self.gci_canvas = None

    def _on_scan_project(self):
        """Scan the project path specified in the entry field asynchronously."""
        path = self.entry_proj_path.get().strip()
        if not path:
            messagebox.showwarning("Workbench Scanner", "Please select or type a valid .wbpj project path.")
            return
        if self._scan_in_progress:
            return

        self._scan_in_progress = True
        self.lbl_proj_info.configure(text=f"Scanning Workbench project...\n{path}")

        def _worker():
            try:
                proj = scan_workbench_project(path)
                self.after(0, lambda p=proj, raw_p=path: self._finish_scan_project(p, raw_p, None))
            except Exception as exc:
                err_msg = str(exc)
                self.after(0, lambda m=err_msg, raw_p=path: self._finish_scan_project(None, raw_p, m))

        threading.Thread(target=_worker, daemon=True, name="WorkbenchScanner").start()

    def _finish_scan_project(self, proj: Optional[WorkbenchProject], path: str, error_msg: Optional[str]):
        """Apply scanned Workbench project results back on the UI thread."""
        self._scan_in_progress = False
        if getattr(self, "_is_closing", False) or getattr(self, "_is_destroyed", False):
            return
        if error_msg is not None:
            self.lbl_proj_info.configure(text="Scan failed. Select a valid .wbpj project file.")
            messagebox.showerror("Workbench Scanner Error", f"Failed to parse Workbench project:\n{error_msg}")
            return
        if not proj or not proj.systems:
            self.lbl_proj_info.configure(text="No valid Fluent systems found in selected project.")
            messagebox.showerror("Workbench Scanner", f"No valid Fluent systems found in project at:\n{path}")
            return

        try:
            self._clear_study_state()
            self.project = proj

            # Update Project Summary
            sys_keys = list(self.project.systems.keys())
            summary_txt = (
                f"Project: {self.project.project_name}\n"
                f"Location: {self.project.files_dir}\n"
                f"Design Points: {', '.join(self.project.design_points)}\n"
                f"Detected Fluent Systems: {len(sys_keys)} ({', '.join(sys_keys)})"
            )
            self.lbl_proj_info.configure(text=summary_txt)

            # Update Systems dropdown and catalog treeview
            self._populate_systems_tree()
            self.menu_systems.configure(values=sys_keys)
            self.menu_gci_sys1.configure(values=sys_keys)
            self.menu_gci_sys2.configure(values=sys_keys)
            self.menu_gci_sys3.configure(values=sys_keys)

            default_sys = self.project.active_system_id or sys_keys[0]
            self.menu_systems.set(default_sys)
            self._on_select_system(default_sys)

            # Auto-populate TSR radius from detected HDF5 mesh geometry if available
            for s_obj in self.project.systems.values():
                if s_obj.rotor_radius and s_obj.rotor_radius > 0:
                    self.v_tsr_radius.set(f"{s_obj.rotor_radius:.4f}")
                    break

            # Auto-detect benchmark Excel file in the project root folder if present
            proj_root = os.path.dirname(self.project.wbpj_path) if self.project.wbpj_path else os.path.dirname(self.project.files_dir)
            if proj_root and os.path.isdir(proj_root) and not self.v_tsr_benchmark.get().strip():
                import glob as _glob
                for cand_xlsx in _glob.glob(os.path.join(proj_root, "*.xlsx")):
                    b_low = os.path.basename(cand_xlsx).lower()
                    if any(k in b_low for k in ("cp", "lambda", "tsr", "bench", "fig")):
                        self.v_tsr_benchmark.set(cand_xlsx)
                        break

            # Auto fill GCI grids if at least 3 systems exist
            self._auto_fill_gci_grids()

            messagebox.showinfo(
                "Workbench Scanner",
                f"Successfully parsed Workbench Project '{self.project.project_name}'.\n"
                f"Discovered {len(sys_keys)} Fluent systems across project."
            )
        except Exception as e:
            messagebox.showerror("Workbench Scanner Error", f"Failed to update Workbench UI:\n{str(e)}")

    def _populate_systems_tree(self):
        """Populate the Systems Catalog Treeview with all discovered systems."""
        for item in self.tree_systems.get_children():
            self.tree_systems.delete(item)

        if not self.project:
            return

        for sys_id, s in self.project.systems.items():
            cells_str = f"{s.cell_count:,}" if s.cell_count is not None else "N/A"
            u_str = f"{s.velocity:.1f}" if s.velocity is not None else "-"
            om_str = f"{s.omega:.1f}" if s.omega is not None else "-"
            dt_str = f"{s.time_step:.6g}" if s.time_step is not None else "N/A"
            revs_str = f"{s.estimated_revolutions:.1f}" if s.estimated_revolutions else "N/A"
            mon_parts = []
            if s.total_moment_file:
                mon_parts.append("Moment")
            if s.blade_moment_files:
                mon_parts.append(f"{len(s.blade_moment_files)}b")
            if s.fx_file:
                mon_parts.append("Forces")
            mon_str = ", ".join(mon_parts) if mon_parts else "None"

            self.tree_systems.insert(
                "", "end", iid=sys_id,
                values=(cells_str, u_str, om_str, dt_str, revs_str, mon_str)
            )

    def _on_tree_system_select(self, event=None):
        """Triggered when a user clicks a row in the Systems Catalog treeview."""
        sel = self.tree_systems.selection()
        if not sel:
            if hasattr(self, "lbl_catalog_selection"):
                self.lbl_catalog_selection.configure(text="Selected for Study: 0 systems")
            return
        sys_id = sel[0]
        self.menu_systems.set(sys_id)
        self._on_select_system(sys_id)
        if hasattr(self, "lbl_catalog_selection"):
            names = ", ".join(sel[:5]) + ("..." if len(sel) > 5 else "")
            self.lbl_catalog_selection.configure(
                text=f"Selected for Study: {len(sel)} system{'s' if len(sel) > 1 else ''} ({names})"
            )

    def _select_all_systems(self):
        """Select all systems in the catalog table."""
        items = self.tree_systems.get_children()
        self.tree_systems.selection_set(items)
        self._on_tree_system_select()

    def _deselect_all_systems(self):
        """Clear all selections in the catalog table."""
        self.tree_systems.selection_set([])
        self._on_tree_system_select()

    def _select_all_meshed_systems(self):
        """Select all systems that have valid mesh cell counts."""
        if not self.project:
            return
        meshed = [sid for sid, s in self.project.systems.items() if s.cell_count is not None]
        self.tree_systems.selection_set(meshed)
        self._on_tree_system_select()

    def _get_selected_system_ids(self) -> List[str]:
        """Return list of selected system IDs, or all project system IDs if none explicitly chosen."""
        sel = list(self.tree_systems.selection())
        if not sel and self.project:
            sel = list(self.project.systems.keys())
        return sel

    def _set_selected_as_grid(self, grid_idx: int):
        """Assign the currently selected system to Grid 1 (Fine), 2 (Medium), or 3 (Coarse)."""
        sel = self.tree_systems.selection()
        sys_id = sel[0] if sel else self.menu_systems.get()
        if not self.project or sys_id not in self.project.systems:
            messagebox.showwarning("System Selection", "Please select a valid system first.")
            return

        menu = self.menu_gci_sys1 if grid_idx == 1 else (self.menu_gci_sys2 if grid_idx == 2 else self.menu_gci_sys3)
        var = self.v_gci_sys1 if grid_idx == 1 else (self.v_gci_sys2 if grid_idx == 2 else self.v_gci_sys3)
        var.set(sys_id)
        menu.set(sys_id)
        self._on_gci_system_changed(grid_idx, sys_id)

    def _on_gci_system_changed(self, grid_idx: int, sys_id: str):
        """Update N and phi when user picks a system for a grid level."""
        if not self.project or sys_id not in self.project.systems:
            return

        s = self.project.systems[sys_id]
        n_var = self.v_gci_n1 if grid_idx == 1 else (self.v_gci_n2 if grid_idx == 2 else self.v_gci_n3)
        phi_var = self.v_gci_phi1 if grid_idx == 1 else (self.v_gci_phi2 if grid_idx == 2 else self.v_gci_phi3)

        if s.cell_count is not None:
            n_var.set(str(s.cell_count))

        if s.total_moment_file and os.path.isfile(s.total_moment_file):
            try:
                from vawt_post.engine.parser import read_fluent_report
                import numpy as np
                rep = read_fluent_report(s.total_moment_file)
                vals = rep.get("value")
                if vals is not None and len(vals) > 0:
                    n_tail = min(len(vals), 360)
                    phi_var.set(f"{float(np.mean(vals[-n_tail:])):.4f}")
            except Exception:
                pass

    def _copy_gci_formatted(self):
        """Copy clean formatted text table to system clipboard."""
        if not self.gci_report:
            messagebox.showinfo("Clipboard", "Please run GCI computation first.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.gci_report.to_formatted_table())
        messagebox.showinfo("Copied", "Clean formatted GCI report copied to clipboard!")

    def _copy_gci_markdown(self):
        """Copy clean Markdown table to system clipboard."""
        if not self.gci_report:
            messagebox.showinfo("Clipboard", "Please run GCI computation first.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.gci_report.to_markdown_table())
        messagebox.showinfo("Copied", "Markdown table copied to clipboard!")

    def _copy_gci_latex(self):
        """Copy LaTeX table snippet to system clipboard."""
        if not self.gci_report:
            messagebox.showinfo("Clipboard", "Please run GCI computation first.")
            return
        self.clipboard_clear()
        self.clipboard_append(self.gci_report.to_latex_table())
        messagebox.showinfo("Copied", "LaTeX table snippet copied to clipboard!")

    def _on_select_system(self, sys_id: str):
        """Display detailed specifications for the selected system."""
        if not self.project or sys_id not in self.project.systems:
            return

        s = self.project.systems[sys_id]
        self.current_system = s

        details = [
            f"=== System [{s.system_id}] Specifications ===",
            f"• Mesh Cells: {s.cell_count:,}" if s.cell_count else "• Mesh Cells: N/A",
            f"• Mesh Nodes: {s.node_count:,}" if s.node_count else "• Mesh Nodes: N/A",
            f"• Inflow Wind Velocity (U): {s.velocity} m/s" if s.velocity else "• Inflow Velocity: N/A",
            f"• Moving Mesh Angular Speed (ω): {s.omega} rad/s" if s.omega else "• Moving Mesh Speed: N/A",
            f"• Time Step Size (Δt): {s.time_step} s" if s.time_step else "• Time Step: N/A",
            f"• Turbulence Model: {s.turb_model}",
            f"• Air Properties: ρ = {s.density} kg/m³, μ = {s.viscosity} Pa·s",
            f"• Identified Blades: {', '.join(s.blade_zones) if s.blade_zones else 'N/A'}",
            f"• Identified Shaft: {s.shaft_zone or 'None'}",
            f"• Total Steps Run: {s.total_steps} (~{s.estimated_revolutions:.1f} revolutions)",
            f"• Total Moment File: {os.path.basename(s.total_moment_file) if s.total_moment_file else 'None'}",
            f"• Blade Moment Monitors: {len(s.blade_moment_files)} files found",
            f"• Aerodynamic Force Monitors: {'fx, fy found' if s.fx_file and s.fy_file else ('fx found' if s.fx_file else 'None')}",
            f"• Animation Sequences: {', '.join(s.animation_names) if s.animation_names else 'None'}",
        ]

        self.txt_sys_details.configure(state="normal")
        self.txt_sys_details.delete("1.0", "end")
        self.txt_sys_details.insert("1.0", "\n".join(details))
        self.txt_sys_details.configure(state="disabled")

    def _load_selected_into_input(self):
        """Transfer extracted settings and monitor files into the main app Input page."""
        if not self.current_system or not self.app:
            messagebox.showwarning("Load System", "Please select a valid system first.")
            return

        s = self.current_system
        try:
            # 1. Update operating and geometry parameters in app
            if s.velocity is not None:
                self.app.v_U.set(str(s.velocity))
            if s.omega is not None:
                self.app.v_omega.set(str(abs(float(s.omega))))
                if hasattr(self.app, "v_rot_dir"):
                    self.app.v_rot_dir.set("cw" if float(s.omega) < 0 else "ccw")
            if s.rotor_radius is not None and s.rotor_radius > 0:
                self.app.v_R.set(f"{s.rotor_radius:.6g}")
            if s.chord is not None and s.chord > 0:
                self.app.v_chord.set(f"{s.chord:.6g}")
            if s.omega is not None and s.rotor_radius is not None and s.velocity is not None and s.velocity > 0:
                tsr_calc = (abs(s.omega) * s.rotor_radius) / s.velocity
                self.app.v_tsr.set(f"{tsr_calc:.4f}")
            if s.density is not None:
                self.app.v_rho.set(str(s.density))
            if s.viscosity is not None:
                self.app.v_mu.set(str(s.viscosity))
            if s.time_step is not None:
                self.app.v_dt.set(str(s.time_step))
            if s.blade_zones:
                nb = min(8, max(1, len(s.blade_zones)))
                self.app.v_nb.set(str(nb))
                self.app.nb_slider.set(nb)
                self.app._on_blade_slider_change(nb)

            # 2. Update files (explicitly clearing stale paths from any prior load)
            self.app.v_total.set(s.total_moment_file or "")
            self.app.v_shaft.set(s.shaft_moment_file or "")
            self.app.v_fx.set(s.fx_file or "")
            self.app.v_fy.set(s.fy_file or "")
            if hasattr(self.app, "v_fx_shaft"):
                self.app.v_fx_shaft.set(getattr(s, "shaft_fx_file", None) or "")
            if hasattr(self.app, "v_fy_shaft"):
                self.app.v_fy_shaft.set(getattr(s, "shaft_fy_file", None) or "")

            blade_list = list(s.blade_moment_files or [])
            for i in range(len(self.app.v_blades)):
                self.app.v_blades[i].set(blade_list[i] if i < len(blade_list) else "")
            for attr_list in ("v_fx_blades", "v_fy_blades"):
                for var in getattr(self.app, attr_list, []):
                    var.set("")
            if hasattr(self.app, "v_prior_source"):
                self.app.v_prior_source.set("")
                if hasattr(self.app, "_on_prior_changed"):
                    self.app._on_prior_changed(run_diag=False)

            # 3. Update case metadata
            self.app.v_meta_cells.set(str(s.cell_count) if s.cell_count is not None else "")
            self.app.v_meta_turb.set(s.turb_model or "")
            self.app.v_meta_dt.set(str(s.time_step) if s.time_step is not None else "")

            # 4. Update output prefix
            self.app.v_prefix.set(f"WB_{s.system_id}")

            # Notify and switch to Input tab
            messagebox.showinfo(
                "System Loaded",
                f"Loaded all parameters and monitor files from system '{s.system_id}' into the Input page!"
            )
            self.app._switch_page("input")

        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to transfer system data:\n{str(e)}")

    def _load_selected_into_live_monitor(self):
        """Configure and switch to Live Monitor for the selected Workbench system."""
        if not self.current_system or not self.app:
            messagebox.showwarning("Live Monitor", "Please select a valid system first.")
            return

        s = self.current_system
        watch_folder = s.fluent_dir if (s.fluent_dir and os.path.isdir(s.fluent_dir)) else s.system_dir
        if not watch_folder or not os.path.isdir(watch_folder):
            messagebox.showwarning("Live Monitor", f"System directory does not exist:\n{watch_folder}")
            return

        from vawt_post.engine.config import Params
        om_val: Optional[float] = None
        rot_dir = "ccw"
        if s.omega is not None:
            om_f = float(s.omega)
            if abs(om_f) > 0:
                om_val = abs(om_f)
                rot_dir = "cw" if om_f < 0 else "ccw"
        p = Params(
            U=float(s.velocity) if s.velocity is not None else 8.0,
            omega=om_val,
            tsr=None if om_val is not None else 3.5,
            R=float(s.rotor_radius) if s.rotor_radius is not None and s.rotor_radius > 0 else 0.5,
            chord=float(s.chord) if s.chord is not None and s.chord > 0 else 0.06,
            n_blades=min(8, max(1, len(s.blade_zones))) if s.blade_zones else 3,
            rho=float(s.density) if s.density is not None else 1.225,
            mu=float(s.viscosity) if s.viscosity is not None else 1.7894e-5,
            dt=float(s.time_step) if s.time_step is not None else None,
            rotation_dir=rot_dir,
            stable_tol=5.0,
            n_stable=2,
        )
        if hasattr(self.app, "live_monitor_frame") and hasattr(self.app.live_monitor_frame, "monitor_fluent_run"):
            self.app.live_monitor_frame.monitor_fluent_run(watch_folder, p=p)
            self.app._switch_page("live")
        else:
            messagebox.showinfo("Live Monitor", f"Configured live monitor for system '{s.system_id}'.")

    def _auto_fill_gci_grids(self):
        """Auto-populate GCI fields with top 3 finest meshes from scanned project systems."""
        if not self.project or len(self.project.systems) < 3:
            return

        # Sort systems by cell count descending
        sorted_sys = sorted(
            [s for s in self.project.systems.values() if s.cell_count is not None],
            key=lambda x: x.cell_count or 0,
            reverse=True
        )
        if len(sorted_sys) >= 3:
            s_fine, s_med, s_coarse = sorted_sys[0], sorted_sys[1], sorted_sys[2]
            self.v_gci_sys1.set(s_fine.system_id)
            self.menu_gci_sys1.set(s_fine.system_id)
            self.v_gci_n1.set(str(s_fine.cell_count))

            self.v_gci_sys2.set(s_med.system_id)
            self.menu_gci_sys2.set(s_med.system_id)
            self.v_gci_n2.set(str(s_med.cell_count))

            self.v_gci_sys3.set(s_coarse.system_id)
            self.menu_gci_sys3.set(s_coarse.system_id)
            self.v_gci_n3.set(str(s_coarse.cell_count))

            # Auto-extract converged blades/shaft-subtracted rotor torque for GCI grids
            try:
                for s_item, phi_var in [(s_fine, self.v_gci_phi1), (s_med, self.v_gci_phi2), (s_coarse, self.v_gci_phi3)]:
                    tq = _extract_system_converged_torque(s_item)
                    if tq.get("mean_torque") is not None:
                        phi_var.set(f"{float(tq['mean_torque']):.4f}")
            except Exception:
                pass

    def _on_study_mode_changed(self, choice: str):
        """Update visible options and description when study mode changes."""
        self.frame_opt_grid.pack_forget()
        self.frame_opt_tsr.pack_forget()
        self.frame_opt_comp.pack_forget()

        if "Grid Dependency" in choice:
            self.frame_opt_grid.pack(fill="x", padx=6, pady=4)
            self.lbl_mode_desc.configure(
                text="Evaluates spatial discretization uncertainty across arbitrary mesh counts (N ≥ 2).\n"
                     "For N ≥ 3, calculates apparent order p, Richardson extrapolation, and local GCI bands."
            )
        elif "TSR" in choice:
            self.frame_opt_tsr.pack(fill="x", padx=6, pady=4)
            self.lbl_mode_desc.configure(
                text="Plots Tip-Speed Ratio (λ = ωR/U) vs Power Coefficient (Cp) performance curve,\n"
                     "fits PCHIP spline, and extracts peak power point (Cp,max)."
            )
        elif "Steady" in choice:
            self.lbl_mode_desc.configure(
                text="Evaluates steady-state RANS convergence for Total Rotor Moment (all blades + shaft combined)\n"
                     "and Aerodynamic Forces (Fx: Drag, Fy: Lateral) across steady solver iterations."
            )
        else:
            self.frame_opt_comp.pack(fill="x", padx=6, pady=4)
            self.lbl_mode_desc.configure(
                text="Compares boundary conditions, meshes, time stepping, and aerodynamics across all selected systems."
            )

    def focus_gci(self):
        """Switch study mode to Grid Dependency Study (ASME GCI) and focus."""
        self.v_study_mode.set("📊 Grid Dependency Study (Multi-Mesh ASME)")
        self._on_study_mode_changed("📊 Grid Dependency Study (Multi-Mesh ASME)")

    def _on_run_selected_study(self):
        """Execute the active study mode on all selected systems."""
        mode = self.v_study_mode.get()
        if "Grid Dependency" in mode:
            self._run_grid_dependency_study()
        elif "TSR" in mode:
            self._run_tsr_curve_study()
        elif "Steady" in mode:
            self._run_steady_convergence_study()
        else:
            self._run_system_comparison_study()

    def _run_grid_dependency_study(self):
        """Run ASME V&V 20 Multi-Grid Study across arbitrary number of selected meshes (N >= 2)."""
        if not self.project or not self.project.systems:
            messagebox.showwarning("Grid Study", "Please scan a Workbench project first.")
            return

        chosen_sids = self._get_selected_system_ids()
        systems = [self.project.systems[s] for s in chosen_sids if s in self.project.systems]
        meshed_systems = [s for s in systems if s.cell_count is not None]

        if len(meshed_systems) < 2:
            # Fall back to all meshed systems in project
            meshed_systems = [s for s in self.project.systems.values() if s.cell_count is not None]

        if len(meshed_systems) < 2:
            messagebox.showerror(
                "Grid Study Error",
                "At least 2 systems with valid mesh cell counts are required for a grid dependency study."
            )
            return

        # Sort systems finest to coarsest
        meshed_systems.sort(key=lambda s: s.cell_count or 0, reverse=True)

        grids: List[GridLevelData] = []
        skipped_ids: List[str] = []
        for s in meshed_systems:
            tq = _extract_system_converged_torque(s)
            mt = tq.get("mean_torque")
            if mt is None:
                skipped_ids.append(s.system_id)
                continue
            phi_val = float(mt)

            grids.append(GridLevelData(
                name=f"System {s.system_id}",
                system_id=s.system_id,
                cell_count=int(s.cell_count or 0),
                phi=phi_val,
                metric_label="Mean Rotor Torque (N·m)",
                dt_s=s.time_step,
                revolutions_completed=s.estimated_revolutions,
                turb_model=s.turb_model,
                velocity=s.velocity,
            ))

        if len(grids) < 2:
            skip_note = f"\nSkipped systems without torque data: {', '.join(skipped_ids)}" if skipped_ids else ""
            messagebox.showerror(
                "Grid Study Error",
                "At least 2 systems with valid mesh cell counts and torque monitor data are required for a grid dependency study."
                + skip_note
            )
            return
        if skipped_ids:
            messagebox.showwarning(
                "Grid Study Notice",
                f"Excluded {len(skipped_ids)} system(s) with no readable torque monitor data: {', '.join(skipped_ids)}."
            )

        dim = 2 if "2D" in self.v_gci_dim.get() else 3
        override = bool(self.v_gci_override_checks.get())

        try:
            self.gci_report = None
            report = compute_multi_grid_study(grids, dimension=dim, override_checks=override)
            self.multi_grid_report = report

            # For backward compatibility, if triplet reports exist, assign primary triplet
            if len(report.triplet_reports) > 0:
                self.gci_report = report.triplet_reports[0]

            self.txt_gci_output.configure(state="normal")
            self.txt_gci_output.delete("1.0", "end")
            self.txt_gci_output.insert("1.0", report.to_formatted_table())
            self.txt_gci_output.configure(state="disabled")

            self.current_plot_type = "multi_grid"
            self._render_current_plot()

        except Exception as e:
            messagebox.showerror("Grid Study Error", f"Failed to compute grid study:\n{str(e)}")

    def _run_tsr_curve_study(self):
        """Extract TSR and mean Cp across selected systems and plot the performance curve."""
        if not self.project or not self.project.systems:
            messagebox.showwarning("TSR Study", "Please scan a Workbench project first.")
            return

        chosen_sids = self._get_selected_system_ids()
        systems = [self.project.systems[s] for s in chosen_sids if s in self.project.systems]

        if len(systems) < 2:
            systems = list(self.project.systems.values())

        if len(systems) < 2:
            messagebox.showerror("TSR Study Error", "At least 2 systems are required to construct a TSR vs Cp curve.")
            return

        try:
            r = float(self.v_tsr_radius.get())
            depth = float(self.v_tsr_depth.get())
        except ValueError:
            r = 0.35
            depth = 1.0

        try:
            bench_p = self.v_tsr_benchmark.get().strip() if hasattr(self, "v_tsr_benchmark") else ""
            res = evaluate_tsr_sweep(
                systems,
                rotor_radius=r,
                depth=depth,
                benchmark_path=bench_p if bench_p else None,
            )
            self.tsr_sweep_result = res

            table_txt = tsr_sweep_to_formatted_table(res)
            self.txt_gci_output.configure(state="normal")
            self.txt_gci_output.delete("1.0", "end")
            self.txt_gci_output.insert("1.0", table_txt)
            self.txt_gci_output.configure(state="disabled")

            self.current_plot_type = "tsr"
            self._render_current_plot()

        except Exception as e:
            messagebox.showerror("TSR Study Error", f"Failed to compute TSR curve:\n{str(e)}")

    def _run_system_comparison_study(self):
        """Generate comparative analysis matrix across selected systems."""
        if not self.project or not self.project.systems:
            messagebox.showwarning("Comparison", "Please scan a Workbench project first.")
            return

        chosen_sids = self._get_selected_system_ids()
        systems = [self.project.systems[s] for s in chosen_sids if s in self.project.systems]

        if not systems:
            systems = list(self.project.systems.values())

        try:
            try:
                r_comp = float(self.v_tsr_radius.get())
            except ValueError:
                r_comp = 0.35
            res = evaluate_systems_comparison(systems, rotor_radius=r_comp)
            self.comp_result = res

            table_txt = systems_comparison_to_formatted_table(res)
            self.txt_gci_output.configure(state="normal")
            self.txt_gci_output.delete("1.0", "end")
            self.txt_gci_output.insert("1.0", table_txt)
            self.txt_gci_output.configure(state="disabled")

            self.current_plot_type = "comp"
            self._render_current_plot()

        except Exception as e:
            messagebox.showerror("Comparison Error", f"Failed to compare systems:\n{str(e)}")

    def _run_steady_convergence_study(self):
        """Evaluate and plot steady-state moment and force convergence for selected system."""
        sys_to_plot = self.current_system
        if not sys_to_plot:
            chosen_sids = self._get_selected_system_ids()
            if self.project and chosen_sids:
                sys_to_plot = self.project.systems.get(chosen_sids[0])

        if not sys_to_plot:
            messagebox.showwarning("Steady Convergence", "Please select a system from the catalog first.")
            return

        try:
            self.current_system = sys_to_plot
            data = evaluate_steady_convergence(sys_to_plot)

            self.txt_gci_output.configure(state="normal")
            self.txt_gci_output.delete("1.0", "end")
            self.txt_gci_output.insert("1.0", steady_convergence_to_formatted_table(data))
            self.txt_gci_output.configure(state="disabled")

            self.current_plot_type = "steady"
            self._render_current_plot()
        except Exception as e:
            messagebox.showerror("Steady Convergence Error", f"Failed to compute steady convergence:\n{str(e)}")

    def _on_plot_steady_clicked(self):
        """Handler for the 'Plot Steady Convergence' quick-action button in System Specifications."""
        self.v_study_mode.set("🔄 Steady Aerodynamics (Total Moment & Forces)")
        self._on_study_mode_changed("🔄 Steady Aerodynamics (Total Moment & Forces)")
        self._run_steady_convergence_study()

    def _copy_study_formatted(self):
        """Copy formatted text table of active study to clipboard."""
        txt = self.txt_gci_output.get("1.0", "end").strip()
        if not txt or self.current_plot_type == "none":
            messagebox.showinfo("Clipboard", "Please run a study calculation first.")
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        messagebox.showinfo("Copied", "Formatted study table copied to clipboard!")

    def _copy_study_markdown(self):
        """Copy Markdown table of active study to clipboard."""
        md_txt = ""
        pt = self.current_plot_type
        def _fmt_num(v, unit: str) -> str:
            if v is None:
                return "N/A"
            try:
                return f"{float(v):.4f} {unit}"
            except Exception:
                return f"{v} {unit}"

        if pt == "multi_grid" and self.multi_grid_report:
            md_txt = self.multi_grid_report.to_markdown_table()
        elif pt == "gci" and self.gci_report:
            md_txt = self.gci_report.to_markdown_table()
        elif pt == "tsr" and self.tsr_sweep_result:
            md_txt = tsr_sweep_to_markdown_table(self.tsr_sweep_result)
        elif pt == "steady" and self.current_system:
            try:
                data = evaluate_steady_convergence(self.current_system)
                md_txt = (
                    f"| Metric | Value |\n| :--- | :--- |\n"
                    f"| System | {data.get('system_id')} |\n"
                    f"| Iterations Run | {data.get('iterations')} |\n"
                    f"| Total Rotor Moment (Final) | {_fmt_num(data.get('final_moment'), 'N·m')} |\n"
                    f"| Total Rotor Moment (Mean Tail) | {_fmt_num(data.get('mean_moment'), 'N·m')} |\n"
                    f"| Streamwise Drag Force (Fx) | {_fmt_num(data.get('final_fx'), 'N')} |\n"
                    f"| Cross-flow Lateral Force (Fy) | {_fmt_num(data.get('final_fy'), 'N')} |\n"
                )
            except Exception:
                md_txt = ""
        elif pt == "comp" and self.comp_result:
            md_txt = systems_comparison_to_markdown_table(self.comp_result)

        if not md_txt:
            messagebox.showinfo("Clipboard", "Please run a study calculation first.")
            return
        self.clipboard_clear()
        self.clipboard_append(md_txt)
        messagebox.showinfo("Copied", "Markdown table copied to clipboard!")

    def _copy_study_latex(self):
        """Copy LaTeX table snippet of active study to clipboard."""
        latex_txt = ""
        pt = self.current_plot_type
        if pt == "multi_grid" and self.multi_grid_report:
            latex_txt = self.multi_grid_report.to_latex_table()
        elif pt == "gci" and self.gci_report:
            latex_txt = self.gci_report.to_latex_table()

        if not latex_txt:
            messagebox.showinfo("Clipboard", "LaTeX export is available for Grid Dependency & GCI studies.")
            return
        self.clipboard_clear()
        self.clipboard_append(latex_txt)
        messagebox.showinfo("Copied", "LaTeX table copied to clipboard!")

    def _on_compute_gci(self):
        """Perform ASME V&V 20 GCI calculation (supports backward-compatibility)."""
        self._run_grid_dependency_study()

    def _render_current_plot(self):
        """Render the appropriate study figure inside the embedded Matplotlib canvas."""
        # Clear previous canvas and close prior figure to prevent leak
        for child in self.gci_plot_container.winfo_children():
            child.destroy()
        if self._active_mpl_fig is not None:
            try:
                plt.close(self._active_mpl_fig)
            except Exception:
                pass
            self._active_mpl_fig = None
        self.gci_canvas = None

        fig = None
        if self.current_plot_type == "multi_grid" and self.multi_grid_report:
            fig = plot_multi_grid_curve(self.multi_grid_report, dark_mode=True)
        elif self.current_plot_type == "tsr" and self.tsr_sweep_result:
            fig = plot_tsr_sweep_curve(self.tsr_sweep_result, dark_mode=True)
        elif self.current_plot_type == "comp" and self.comp_result:
            fig = self._build_comparison_figure(self.comp_result, dark_mode=True)
        elif self.current_plot_type == "steady" and self.current_system:
            fig = plot_steady_convergence(self.current_system, dark_mode=True)
        elif self.gci_report:
            fig = plot_gci_curve(self.gci_report, dark_mode=True)

        if fig is not None:
            self._active_mpl_fig = fig
            self.gci_canvas = FigureCanvasTkAgg(fig, master=self.gci_plot_container)
            self.gci_canvas.draw()
            self.gci_canvas.get_tk_widget().pack(fill="both", expand=True)

    def shutdown(self) -> None:
        """Close active Matplotlib figures and clear study plot canvas on app exit."""
        self._is_closing = True
        if self._active_mpl_fig is not None:
            try:
                plt.close(self._active_mpl_fig)
            except Exception:
                pass
            self._active_mpl_fig = None
        if hasattr(self, "gci_plot_container"):
            for child in self.gci_plot_container.winfo_children():
                try:
                    child.destroy()
                except Exception:
                    pass
        self.gci_canvas = None

    def _render_gci_plot(self):
        """Render GCI plot (backward-compatibility wrapper)."""
        if self.gci_report and self.current_plot_type == "none":
            self.current_plot_type = "gci"
        self._render_current_plot()

    def _build_comparison_figure(self, comp_data: Dict[str, Any], dark_mode: bool = False):
        """Create a side-by-side comparative bar chart for systems."""
        fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=100, layout="constrained")

        bg_col = "#0F172A" if dark_mode else "#FFFFFF"
        text_col = "#F8FAFC" if dark_mode else "#0F172A"
        grid_col = "#334155" if dark_mode else "#E2E8F0"

        fig.patch.set_facecolor(bg_col)
        ax.set_facecolor(bg_col)
        ax.grid(True, linestyle="--", alpha=0.5, color=grid_col)
        ax.tick_params(colors=text_col)
        for spine in ax.spines.values():
            spine.set_color(text_col)

        systems = comp_data.get("systems", [])
        labels = [s.get("system_id", "?") for s in systems]
        moments = [s.get("mean_moment") or 0.0 for s in systems]

        colors = ["#3B82F6", "#10B981", "#F59E0B", "#EC4899", "#8B5CF6", "#06B6D4"]
        bar_colors = [colors[i % len(colors)] for i in range(len(labels))]

        bars = ax.bar(labels, moments, color=bar_colors, width=0.55, edgecolor=text_col, lw=0.8)
        for b, m in zip(bars, moments):
            if m != 0.0:
                va = "bottom" if m >= 0 else "top"
                dy = 4 if m >= 0 else -4
                ax.annotate(
                    f"{m:.4f}",
                    xy=(b.get_x() + b.get_width() / 2.0, b.get_height()),
                    xytext=(0, dy),
                    textcoords="offset points",
                    ha="center",
                    va=va,
                    fontsize=9,
                    fontweight="bold",
                    color=text_col,
                )

        ax.margins(y=0.18)
        ax.set_xlabel("System ID", fontsize=9.5, fontweight="bold", color=text_col)
        ax.set_ylabel("Mean Total Moment (N·m)", fontsize=9.5, fontweight="bold", color=text_col)
        ax.set_title("Multi-System Aerodynamic Comparison", fontsize=9.5, fontweight="bold", color=text_col, pad=6)
        return fig

