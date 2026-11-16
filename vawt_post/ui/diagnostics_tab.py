# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: File Diagnostics Tab (UI)
==================================================
Interactive GUI tab for reviewing monitor file-health findings, inspecting headers,
filtering by severity/file, exporting diagnostics reports, and executing verified
data repairs (e.g. blade reconstruction).
"""
from __future__ import annotations

import os
from typing import Optional
from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import ttk, messagebox, filedialog

from vawt_post.engine.config import Params
from vawt_post.engine.diagnostics import (
    run_file_health_diagnostics, reconstruct_missing_blade,
    export_diagnostics_json, export_diagnostics_csv,
    DiagnosticsReport, DiagnosticFinding
)


class FileDiagnosticsFrame(ctk.CTkFrame):
    """File-Health Diagnostics tab presenting issues, header inspection, and repairs."""

    def __init__(self, master, app=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app
        self.report: Optional[DiagnosticsReport] = None
        self.selected_finding: Optional[DiagnosticFinding] = None
        self._sort_reverse = False

        self._build_ui()

    def _build_ui(self):
        # 1. Top Summary Bar
        top_bar = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray85", "gray17"))
        top_bar.pack(fill="x", padx=8, pady=(8, 6))

        # Status badge
        self.badge_lbl = ctk.CTkLabel(
            top_bar,
            text="✓ File Health:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray20", "gray80")
        )
        self.badge_lbl.pack(side="left", padx=12, pady=8)

        # Counter chips
        self.cnt_err = ctk.CTkLabel(top_bar, text="Errors: 0", text_color="#EF4444", font=ctk.CTkFont(size=12, weight="bold"))
        self.cnt_err.pack(side="left", padx=8, pady=8)

        self.cnt_warn = ctk.CTkLabel(top_bar, text="Warnings: 0", text_color="#F59E0B", font=ctk.CTkFont(size=12, weight="bold"))
        self.cnt_warn.pack(side="left", padx=8, pady=8)

        self.cnt_info = ctk.CTkLabel(top_bar, text="Info: 0", text_color="#3B82F6", font=ctk.CTkFont(size=12))
        self.cnt_info.pack(side="left", padx=8, pady=8)

        # Filter combo
        ctk.CTkLabel(top_bar, text="Filter:").pack(side="left", padx=(16, 4), pady=8)
        self.filter_var = ctk.StringVar(value="All Findings")
        self.filter_menu = ctk.CTkOptionMenu(
            top_bar,
            values=["All Findings", "Errors Only", "Warnings & Errors", "Info Only"],
            variable=self.filter_var,
            width=140,
            command=self._apply_filter
        )
        self.filter_menu.pack(side="left", padx=4, pady=8)

        # Re-check button
        ctk.CTkButton(
            top_bar,
            text="🔄 Re-check Files",
            width=120,
            fg_color=("RoyalBlue3", "#2563EB"),
            hover_color=("RoyalBlue4", "#1D4ED8"),
            command=self.run_diagnostics
        ).pack(side="right", padx=(4, 10), pady=8)

        # 2. Middle Table (ttk.Treeview)
        table_frame = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "gray14"))
        table_frame.pack(fill="both", expand=True, padx=8, pady=4)

        columns = ("#", "Severity", "File", "Check", "Location", "Explanation", "Suggested Fix")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", height=7)

        # Column headings and widths
        widths = {"#": 35, "Severity": 80, "File": 80, "Check": 160, "Location": 110, "Explanation": 260, "Suggested Fix": 260}
        for col in columns:
            self.tree.heading(col, text=col, command=lambda c=col: self._sort_by_column(c))
            self.tree.column(col, width=widths.get(col, 100), anchor="w")
        self.tree.column("#", anchor="center")
        self.tree.column("Severity", anchor="center")

        # Scrollbars
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        # Treeview styling tags
        self.tree.tag_configure("ERROR", foreground="#DC2626")
        self.tree.tag_configure("WARNING", foreground="#D97706")
        self.tree.tag_configure("INFO", foreground="#2563EB")

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # 3. Bottom Action Toolbar (two compact rows so note never squishes Export buttons)
        action_bar = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray85", "gray17"))
        action_bar.pack(fill="x", padx=8, pady=(4, 8))

        action_btns = ctk.CTkFrame(action_bar, fg_color="transparent")
        action_btns.pack(fill="x", padx=8, pady=(6, 2))

        self.btn_header = ctk.CTkButton(
            action_btns,
            text="👁 Show File Header",
            width=135,
            state="disabled",
            command=self._show_file_header
        )
        self.btn_header.pack(side="left", padx=(2, 4), pady=2)

        self.btn_repair = ctk.CTkButton(
            action_btns,
            text="🔧 Apply Suggested Repair",
            width=175,
            fg_color=("SeaGreen3", "#059669"),
            hover_color=("SeaGreen4", "#047857"),
            state="disabled",
            command=self._apply_suggested_repair
        )
        self.btn_repair.pack(side="left", padx=4, pady=2)

        ctk.CTkButton(
            action_btns,
            text="📄 Export CSV",
            width=105,
            fg_color="transparent",
            border_width=1,
            command=self._export_csv
        ).pack(side="right", padx=(4, 2), pady=2)

        ctk.CTkButton(
            action_btns,
            text="💾 Export JSON",
            width=105,
            fg_color="transparent",
            border_width=1,
            command=self._export_json
        ).pack(side="right", padx=4, pady=2)

        self.repair_note_lbl = ctk.CTkLabel(
            action_bar,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            anchor="w",
            wraplength=680
        )
        self.repair_note_lbl.pack(fill="x", padx=12, pady=(0, 6))

    def run_diagnostics(self) -> DiagnosticsReport:
        """Execute full diagnostics suite on currently loaded files."""
        if not self.app:
            return DiagnosticsReport()

        try:
            p = self.app._gather_params()
        except Exception:
            def safe_f(v_str, default):
                try:
                    s = str(v_str).strip().replace(",", ".")
                    return float(s) if s else default
                except Exception:
                    return default
            def safe_i(v_str, default):
                try:
                    s = str(v_str).strip().replace(",", ".")
                    return int(float(s)) if s else default
                except Exception:
                    return default
            nb = safe_i(self.app.v_nb.get(), 3)
            p = Params(
                U=safe_f(self.app.v_U.get(), 8.0),
                tsr=safe_f(self.app.v_tsr.get(), 3.5),
                omega=safe_f(self.app.v_omega.get(), None),
                R=safe_f(self.app.v_R.get(), 0.5),
                chord=safe_f(self.app.v_chord.get(), 0.06),
                n_blades=max(1, min(8, nb)) if nb > 0 else 3,
                theta0=safe_f(self.app.v_theta0.get(), 0.0),
                rotation_dir=self.app.v_rot_dir.get() if hasattr(self.app, "v_rot_dir") else "ccw",
                dt=safe_f(self.app.v_dt.get(), None),
                bin_deg=safe_f(self.app.v_bin.get(), 1.0),
            )

        f = self.app._gather_files()

        # Determine watch folder if available
        watch_folder = None
        for path in [f.total] + [b for b in f.blades if b]:
            if path and os.path.isfile(path):
                watch_folder = os.path.dirname(os.path.abspath(path))
                break

        # Run diagnostics
        rep = run_file_health_diagnostics(p, f, watch_folder=watch_folder)
        self.report = rep
        self._populate_table(rep)
        self._update_badge(rep)

        # Enable/disable repair button
        if rep.suggested_repairs:
            self.btn_repair.configure(state="normal")
            first_repair = rep.suggested_repairs[0]
            self.repair_note_lbl.configure(text=f"Available: {first_repair['description']}")
        else:
            self.btn_repair.configure(state="disabled")
            self.repair_note_lbl.configure(text="")

        return rep

    def _populate_table(self, rep: DiagnosticsReport):
        """Populate treeview rows from diagnostics report."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        filter_mode = self.filter_var.get()
        idx = 1
        for orig_idx, finding in enumerate(rep.findings):
            if filter_mode == "Errors Only" and finding.severity != "ERROR":
                continue
            if filter_mode == "Warnings & Errors" and finding.severity not in ("ERROR", "WARNING"):
                continue
            if filter_mode == "Info Only" and finding.severity != "INFO":
                continue

            icon = "✖ ERROR" if finding.severity == "ERROR" else ("⚠ WARN" if finding.severity == "WARNING" else "ℹ INFO")
            self.tree.insert(
                "",
                "end",
                iid=str(orig_idx),
                values=(
                    idx,
                    icon,
                    finding.file,
                    finding.check,
                    finding.location,
                    finding.explanation,
                    finding.suggested_fix
                ),
                tags=(finding.severity,)
            )
            idx += 1

    def _update_badge(self, rep: DiagnosticsReport):
        """Update top status badge and counters."""
        n_err = rep.summary_counts.get("ERROR", 0)
        n_warn = rep.summary_counts.get("WARNING", 0)
        n_info = rep.summary_counts.get("INFO", 0)

        self.cnt_err.configure(text=f"Errors: {n_err}")
        self.cnt_warn.configure(text=f"Warnings: {n_warn}")
        self.cnt_info.configure(text=f"Info: {n_info}")

        if n_err > 0:
            self.badge_lbl.configure(text="✖ File Health Issues:", text_color="#EF4444")
            if self.app and hasattr(self.app, "main_diag_badge"):
                self.app.main_diag_badge.configure(text=f"✖ {n_err} Error(s)", text_color="#EF4444")
        elif n_warn > 0:
            self.badge_lbl.configure(text="⚠️ File Health Advisory:", text_color="#F59E0B")
            if self.app and hasattr(self.app, "main_diag_badge"):
                self.app.main_diag_badge.configure(text=f"⚠️ {n_warn} Warning(s)", text_color="#F59E0B")
        else:
            self.badge_lbl.configure(text="✓ File Health Clean:", text_color="#10B981")
            if self.app and hasattr(self.app, "main_diag_badge"):
                self.app.main_diag_badge.configure(text="✓ Verified", text_color="#10B981")

        if self.app and hasattr(self.app, "_update_tab_badges"):
            self.app._update_tab_badges()

    def _apply_filter(self, _choice=None):
        if self.report:
            self._populate_table(self.report)

    def _sort_by_column(self, col: str):
        if not self.report:
            return
        self._sort_reverse = not self._sort_reverse
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        if col == "#":
            def _num_key(pair):
                try:
                    return (0, int(pair[0]))
                except Exception:
                    return (1, str(pair[0]))
            items.sort(key=_num_key, reverse=self._sort_reverse)
        else:
            items.sort(key=lambda pair: str(pair[0]).lower(), reverse=self._sort_reverse)
        for index, (_, k) in enumerate(items):
            self.tree.move(k, "", index)

    def _on_row_select(self, _event=None):
        selected = self.tree.selection()
        if selected and self.report:
            try:
                row_idx = int(selected[0])
            except (TypeError, ValueError):
                row_idx = -1
            if 0 <= row_idx < len(self.report.findings):
                self.selected_finding = self.report.findings[row_idx]
                self.btn_header.configure(state="normal")
                return
        self.selected_finding = None
        self.btn_header.configure(state="disabled")

    def _on_double_click(self, _event=None):
        selected = self.tree.selection()
        if not selected or not self.report:
            return
        row_idx = int(selected[0])
        if 0 <= row_idx < len(self.report.findings):
            f = self.report.findings[row_idx]
            msg = (
                f"Check: {f.check}\n"
                f"Severity: {f.severity}\n"
                f"File Role: {f.file}\n"
                f"Location: {f.location}\n\n"
                f"Explanation:\n{f.explanation}\n\n"
                f"Suggested Fix:\n{f.suggested_fix}"
            )
            messagebox.showinfo(f"Diagnostic Detail: {f.check}", msg)

    def _show_file_header(self):
        """Display raw file header and initial lines for the selected file."""
        if not self.selected_finding or not self.app:
            return

        f = self.app._gather_files()
        role = self.selected_finding.file.lower()
        path = None
        if "total" in role:
            path = f.total
        elif "fx_shaft" in role:
            path = f.fx_shaft
        elif "fy_shaft" in role:
            path = f.fy_shaft
        elif "shaft" in role:
            path = f.shaft
        elif "blade" in role:
            m = role.split()[-1]
            try:
                b_idx = int(m) - 1
                if 0 <= b_idx < len(f.blades):
                    path = f.blades[b_idx]
            except Exception:
                pass
        elif "fx" in role:
            path = f.fx
        elif "fy" in role:
            path = f.fy

        if not path or not os.path.isfile(path):
            messagebox.showwarning("File Header", f"File path for role '{self.selected_finding.file}' is not accessible.")
            return

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                first_lines = [fh.readline() for _ in range(30)]
            header_text = "".join(l for l in first_lines if l)

            # Show in modal popup
            top = ctk.CTkToplevel(self)
            top.title(f"File Header: {os.path.basename(path)}")
            top.geometry("700x500")
            txt = ctk.CTkTextbox(top, font=ctk.CTkFont(family="Consolas", size=11))
            txt.pack(fill="both", expand=True, padx=12, pady=12)
            txt.insert("1.0", f"File: {path}\n" + "=" * 70 + "\n\n" + header_text)
            txt.configure(state="disabled")
        except Exception as e:
            messagebox.showerror("File Header", f"Error reading file header: {e}")

    def _apply_suggested_repair(self):
        """Prompt confirmation and execute data repair (e.g. blade reconstruction)."""
        if not self.report or not self.report.suggested_repairs or not self.app:
            return

        repair = self.report.suggested_repairs[0]
        action = repair["action"]

        if action.startswith("reconstruct_blade:"):
            b_idx = int(action.split(":")[-1])
            confirm = messagebox.askyesno(
                "Confirm Data Repair",
                f"{repair['description']}\n\n"
                f"This will reconstruct Blade {b_idx + 1} torque mathematically as Total - Other Blades - Shaft, "
                f"save the file to output directory, and assign it to Blade {b_idx + 1}.\n\n"
                f"The repair will be explicitly recorded in Excel and parameters.\n\n"
                f"Apply repair now?"
            )
            if not confirm:
                return

            try:
                out_dir = self.app.v_out.get() or os.path.join(os.path.expanduser("~"), "VAWT_results")
                f = self.app._gather_files()
                recon_path = reconstruct_missing_blade(f, b_idx, out_dir)

                # Assign to UI
                self.app.v_blades[b_idx].set(recon_path)
                # Record repair
                rep_name = f"Blade {b_idx + 1} reconstructed as Total - Other Blades"
                if rep_name not in self.app.applied_repairs:
                    self.app.applied_repairs.append(rep_name)

                messagebox.showinfo("Repair Applied", f"Successfully reconstructed Blade {b_idx + 1}!\nSaved to: {recon_path}")
                self.run_diagnostics()
            except Exception as e:
                messagebox.showerror("Repair Failed", f"Could not reconstruct blade: {e}")

    def _export_json(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")], title="Export Diagnostics JSON")
        if path:
            try:
                export_diagnostics_json(self.report, path)
                messagebox.showinfo("Export Successful", f"Diagnostics saved to {path}")
            except Exception as exc:
                messagebox.showerror("Export Failed", f"Could not write diagnostics JSON: {exc}")

    def _export_csv(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")], title="Export Diagnostics CSV")
        if path:
            try:
                export_diagnostics_csv(self.report, path)
                messagebox.showinfo("Export Successful", f"Diagnostics saved to {path}")
            except Exception as exc:
                messagebox.showerror("Export Failed", f"Could not write diagnostics CSV: {exc}")
