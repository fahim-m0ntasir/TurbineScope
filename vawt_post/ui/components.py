# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor: UI Components & Widgets
================================================
CustomTkinter reusable widgets, modern cards, tooltips, and dialogs.
"""
import math
import os
from typing import Optional, Callable, List, Any
from vawt_post.ui import qt_tk as ctk
from vawt_post.ui.qt_tk import filedialog, messagebox


class ToolTip:
    """Native Qt 6 hover tooltip for TurbineScope widgets."""

    def __init__(self, widget: Any, text: str):
        self.widget = widget
        self.text = text
        self.tip_window: Optional[Any] = None
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(str(text))

    def show_tip(self, event=None) -> None:
        pass

    def hide_tip(self, event=None) -> None:
        pass


class SectionCard(ctk.CTkFrame):
    """Modern styled card container with header and icon/title."""

    def __init__(self, master, title: str, subtitle: str = "", **kwargs):
        super().__init__(master, corner_radius=10, border_width=1, **kwargs)
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=12, pady=(10, 4))

        self.title_label = ctk.CTkLabel(
            self.header_frame,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            anchor="w"
        )
        self.title_label.pack(side="left", anchor="w")

        if subtitle:
            self.sub_label = ctk.CTkLabel(
                self.header_frame,
                text=f"({subtitle})",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="gray",
                anchor="w"
            )
            self.sub_label.pack(side="left", padx=(6, 0), anchor="w")

        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True, padx=12, pady=(2, 10))
        self.container = self.content_frame



class PresetDialog(ctk.CTkToplevel):
    """Modal dialog to save a named turbine preset."""

    def __init__(self, parent, on_save: Callable[[str, str], None]):
        super().__init__(parent)
        self.title("Save Turbine Preset")
        self.geometry("420x240")
        self.resizable(False, False)
        self.transient(parent)

        # Center over parent
        self._center_over_parent(parent, 420, 240)
        self.grab_set()

        self.on_save = on_save

        ctk.CTkLabel(
            self,
            text="Save Parameters for Later Use",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        ).pack(padx=20, pady=(16, 6), anchor="w")

        ctk.CTkLabel(
            self,
            text="Preset Name:",
            font=ctk.CTkFont(family="Segoe UI", size=12)
        ).pack(padx=20, pady=(6, 2), anchor="w")

        self.name_entry = ctk.CTkEntry(self, width=380, placeholder_text="e.g. My NACA0018 3-Blade Rotor")
        self.name_entry.pack(padx=20, pady=(0, 10))
        self.name_entry.focus_set()
        self.name_entry.bind("<KeyRelease>", lambda e: self.name_entry.configure(border_color=("gray65", "gray35")))

        ctk.CTkLabel(
            self,
            text="Description / Notes (Optional):",
            font=ctk.CTkFont(family="Segoe UI", size=12)
        ).pack(padx=20, pady=(0, 2), anchor="w")

        self.desc_entry = ctk.CTkEntry(self, width=380, placeholder_text="e.g. U=9m/s, R=0.6m, tested for thesis")
        self.desc_entry.pack(padx=20, pady=(0, 16))

        # Keyboard bindings
        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=20, pady=(0, 12), fill="x")

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            width=90,
            fg_color="transparent",
            border_width=1,
            command=self.destroy
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_row,
            text="Save Preset",
            width=110,
            command=self._confirm
        ).pack(side="right")

    def _center_over_parent(self, parent, w: int, h: int):
        try:
            parent.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            x = max(0, px + (pw - w) // 2)
            y = max(0, py + (ph - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _confirm(self):
        name = self.name_entry.get().strip()
        desc = self.desc_entry.get().strip()
        if not name:
            self.name_entry.configure(border_color="#EF4444")
            return
        self.on_save(name, desc)
        self.destroy()


class MultiStageDialog(ctk.CTkToplevel):
    """Modal dialog allowing users to define arbitrary simulation stages with

    custom timesteps and azimuthal shifts (0.25°, 0.5°, 1°, 2°, 5°, etc.)
    and generate synthetic Fluent monitor files for testing.
    """

    def __init__(self, parent, current_params, on_generated: Callable[[Any, str], None]):
        super().__init__(parent)
        self.title("Multi-Stage Simulation & Benchmark Generator")
        self.geometry("640x580")
        self.minsize(580, 520)
        self.transient(parent)

        # Center over parent
        self._center_over_parent(parent, 640, 580)
        self.grab_set()

        self.bind("<Escape>", lambda e: self.destroy())

        self.params = current_params
        self.on_generated = on_generated

        from vawt_post.engine.synthetic import SimulationStage
        self.stages: List[SimulationStage] = [
            SimulationStage(n_steps=2880, deg_per_step=1.0, name="Stage 1 (1.0°/step)"),
            SimulationStage(n_steps=2880, deg_per_step=0.5, name="Stage 2 (0.5°/step)"),
        ]

        self._build_ui()

    def _center_over_parent(self, parent, w: int, h: int):
        try:
            parent.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            x = max(0, px + (pw - w) // 2)
            y = max(0, py + (ph - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        ctk.CTkLabel(
            self,
            text="⚡ Multi-Stage Simulation Generator",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold")
        ).pack(padx=20, pady=(16, 4), anchor="w")

        ctk.CTkLabel(
            self,
            text="Configure simulation phases with custom timesteps and azimuthal shifts (0.25°, 0.5°, 1°, 2°, 5°).\n"
                 "Files will be generated in Fluent report format and loaded into the analyzer.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            justify="left"
        ).pack(padx=20, pady=(0, 10), anchor="w")

        # Quick presets for stages
        preset_frame = ctk.CTkFrame(self, fg_color="transparent")
        preset_frame.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(preset_frame, text="Quick Stage Presets:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 8))
        self.stage_preset_menu = ctk.CTkOptionMenu(
            preset_frame,
            values=[
                "2 Stages (1.0° -> 0.5°)",
                "3 Stages (2.0° -> 1.0° -> 0.5°)",
                "4 Stages (2.0° -> 1.0° -> 0.5° -> 0.25°)",
                "5 Stages (5.0° -> 2.0° -> 1.0° -> 0.5° -> 0.25°)",
                "Single Stage (0.25° Fine)",
                "Single Stage (1.0° Standard)",
            ],
            width=260,
            command=self._apply_stage_preset
        )
        self.stage_preset_menu.pack(side="left")

        # Stages scrollable list
        ctk.CTkLabel(self, text="Current Stages:", font=ctk.CTkFont(weight="bold")).pack(padx=20, anchor="w")
        self.stage_list_frame = ctk.CTkScrollableFrame(self, height=140, corner_radius=8)
        self.stage_list_frame.pack(fill="x", padx=20, pady=(4, 8))

        # Add stage input box (two clean rows to fit 640px width comfortably)
        add_frame = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "gray17"))
        add_frame.pack(fill="x", padx=20, pady=(4, 6))

        add_row1 = ctk.CTkFrame(add_frame, fg_color="transparent")
        add_row1.pack(fill="x", padx=10, pady=(8, 3))

        ctk.CTkLabel(add_row1, text="Δθ:").pack(side="left", padx=(0, 2))
        self.new_shift_entry = ctk.CTkEntry(add_row1, width=60, placeholder_text="1.0")
        self.new_shift_entry.insert(0, "1.0")
        self.new_shift_entry.pack(side="left", padx=(0, 4))

        self.shift_preset_menu = ctk.CTkOptionMenu(
            add_row1,
            values=["Presets...", "0.1°", "0.2°", "0.25°", "0.5°", "0.75°", "1.0°", "1.5°", "2.0°", "2.5°", "3.0°", "5.0°"],
            width=105,
            command=self._on_preset_shift_chosen
        )
        self.shift_preset_menu.set("Presets...")
        self.shift_preset_menu.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(add_row1, text="Steps:").pack(side="left", padx=(0, 2))
        self.new_steps_entry = ctk.CTkEntry(add_row1, width=75, placeholder_text="1440")
        self.new_steps_entry.insert(0, "1440")
        self.new_steps_entry.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(add_row1, text="or Revs:").pack(side="left", padx=(0, 2))
        self.new_revs_entry = ctk.CTkEntry(add_row1, width=60, placeholder_text="4.0")
        self.new_revs_entry.insert(0, "4.0")
        self.new_revs_entry.pack(side="left")

        add_row2 = ctk.CTkFrame(add_frame, fg_color="transparent")
        add_row2.pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkButton(
            add_row2,
            text="➕ Add Stage",
            width=105,
            command=self._add_stage
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            add_row2,
            text="Clear All",
            width=80,
            fg_color="transparent",
            border_width=1,
            command=self._clear_stages
        ).pack(side="left")

        self.new_revs_entry.bind("<KeyRelease>", self._on_revs_key)
        self.new_steps_entry.bind("<KeyRelease>", self._on_steps_key)
        self.new_shift_entry.bind("<KeyRelease>", self._on_shift_key)

        # Target Simulation Physics Row
        sim_param_frame = ctk.CTkFrame(self, fg_color="transparent")
        sim_param_frame.pack(fill="x", padx=20, pady=(2, 4))

        ctk.CTkLabel(sim_param_frame, text="Target Final Cp:").pack(side="left", padx=(0, 4))
        self.target_cp_entry = ctk.CTkEntry(sim_param_frame, width=60)
        self.target_cp_entry.insert(0, "0.35")
        self.target_cp_entry.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(sim_param_frame, text="Settling Revs:").pack(side="left", padx=(0, 4))
        self.settling_revs_entry = ctk.CTkEntry(sim_param_frame, width=45)
        self.settling_revs_entry.insert(0, "4")
        self.settling_revs_entry.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(sim_param_frame, text="Thrust Ct Target:").pack(side="left", padx=(0, 4))
        self.target_ct_entry = ctk.CTkEntry(sim_param_frame, width=50)
        self.target_ct_entry.insert(0, "1.00")
        self.target_ct_entry.pack(side="left")

        # Output folder row
        out_frame = ctk.CTkFrame(self, fg_color="transparent")
        out_frame.pack(fill="x", padx=20, pady=4)
        out_frame.columnconfigure(1, weight=1)

        default_dir = os.path.join(os.path.expanduser("~"), "VAWT_results", "multistage_test")
        self.v_dest = ctk.StringVar(value=default_dir)

        ctk.CTkLabel(out_frame, text="Destination Folder:").grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(out_frame, textvariable=self.v_dest).grid(row=0, column=1, sticky="we", padx=8)
        def browse_dest():
            d = filedialog.askdirectory(title="Select Destination Folder")
            if d:
                self.v_dest.set(d)
        ctk.CTkButton(out_frame, text="Browse...", width=75, command=browse_dest).grid(row=0, column=2)

        # Total summary stats label
        self.summary_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=("RoyalBlue4", "#60A5FA")
        )
        self.summary_lbl.pack(padx=20, pady=(4, 8), anchor="w")

        # Bottom buttons
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill="x", padx=20, pady=(0, 14), side="bottom")

        ctk.CTkButton(
            btn_bar,
            text="Cancel",
            width=90,
            fg_color="transparent",
            border_width=1,
            command=self.destroy
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_bar,
            text="⚡ Generate & Load Files",
            width=180,
            fg_color=("#16A34A", "#15803D"),
            hover_color=("#15803D", "#166534"),
            command=self._generate
        ).pack(side="right")

        self._refresh_stages_view()

    def _refresh_stages_view(self):
        for widget in self.stage_list_frame.winfo_children():
            widget.destroy()

        try:
            omega = self.params.omega_val() if self.params else 56.0
        except Exception:
            omega = 56.0
        if not omega or omega <= 0:
            omega = 56.0

        Trev = 2.0 * math.pi / omega
        total_steps = 0
        total_time = 0.0

        for idx, stg in enumerate(self.stages):
            deg = stg.resolve_deg_per_step(omega)
            dt = stg.resolve_dt(omega)
            revs = (stg.n_steps * dt) / Trev if Trev > 0 else 0.0
            total_steps += stg.n_steps
            total_time += stg.n_steps * dt

            row = ctk.CTkFrame(self.stage_list_frame, fg_color=("gray95", "gray22"), corner_radius=6)
            row.pack(fill="x", pady=2, padx=2)

            txt = f"Stage {idx + 1}: {stg.n_steps:,} steps | {deg:.2f}° | Δt={dt:.3e} s | {revs:.2f} rev"
            ctk.CTkLabel(row, text=txt, font=ctk.CTkFont(family="Consolas", size=11), anchor="w").pack(side="left", padx=8, pady=4, fill="x", expand=True)

            def make_del(i=idx):
                def del_fn():
                    if len(self.stages) > 1:
                        self.stages.pop(i)
                        self._refresh_stages_view()
                return del_fn

            ctk.CTkButton(row, text="✕", width=28, height=22, fg_color="transparent", text_color="red", command=make_del(idx)).pack(side="right", padx=6)

        total_revs = (total_time / Trev) if Trev > 0 else 0.0
        self.summary_lbl.configure(
            text=f"Total: {len(self.stages)} Stage(s)  |  {total_steps:,} Steps  |  {total_time:.4f} s Total Time  |  {total_revs:.2f} Revolutions"
        )

    def _on_preset_shift_chosen(self, choice: str):
        if choice and choice != "Presets...":
            deg_val = choice.replace("°", "").strip()
            self.new_shift_entry.delete(0, "end")
            self.new_shift_entry.insert(0, deg_val)
            self._on_shift_key()

    def _on_shift_key(self, event=None):
        try:
            deg = float(self.new_shift_entry.get().strip())
            if deg <= 0:
                return
            revs_str = self.new_revs_entry.get().strip()
            if revs_str:
                revs = float(revs_str)
                steps = int(round(revs * 360.0 / deg))
                self.new_steps_entry.delete(0, "end")
                self.new_steps_entry.insert(0, str(steps))
        except (ValueError, ZeroDivisionError):
            pass

    def _on_revs_key(self, event=None):
        try:
            revs = float(self.new_revs_entry.get().strip())
            deg = float(self.new_shift_entry.get().strip())
            if deg <= 0 or revs <= 0:
                return
            steps = int(round(revs * 360.0 / deg))
            self.new_steps_entry.delete(0, "end")
            self.new_steps_entry.insert(0, str(steps))
        except (ValueError, ZeroDivisionError):
            pass

    def _on_steps_key(self, event=None):
        try:
            steps = int(float(self.new_steps_entry.get().strip()))
            deg = float(self.new_shift_entry.get().strip())
            if deg <= 0 or steps <= 0:
                return
            revs = steps / (360.0 / deg)
            self.new_revs_entry.delete(0, "end")
            self.new_revs_entry.insert(0, f"{revs:.2f}".rstrip("0").rstrip("."))
        except (ValueError, ZeroDivisionError):
            pass

    def _add_stage(self):
        try:
            shift_str = self.new_shift_entry.get().strip().replace("°", "").replace(",", ".")
            shift_deg = float(shift_str)
            if shift_deg <= 0:
                messagebox.showwarning("Invalid Shift", "Azimuthal shift Δθ must be greater than 0°.")
                return

            steps_str = self.new_steps_entry.get().strip().replace(",", ".")
            revs_str = self.new_revs_entry.get().strip().replace(",", ".")
            if steps_str:
                steps = int(float(steps_str))
            elif revs_str:
                revs = float(revs_str)
                steps = int(round(revs * 360.0 / shift_deg))
            else:
                steps = int(round(4.0 * 360.0 / shift_deg))

            if steps <= 0:
                messagebox.showwarning("Invalid Steps", "Step count must be at least 1.")
                return
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showwarning("Input Error", f"Invalid numeric input for stage: {exc}")
            return

        from vawt_post.engine.synthetic import SimulationStage
        self.stages.append(SimulationStage(
            n_steps=steps,
            deg_per_step=shift_deg,
            name=f"Stage {len(self.stages) + 1} ({shift_deg:g}°/step)"
        ))
        self._refresh_stages_view()

    def _clear_stages(self):
        self.stages.clear()
        self._refresh_stages_view()

    def _apply_stage_preset(self, choice: str):
        from vawt_post.engine.synthetic import SimulationStage
        self.stages.clear()
        if "2 Stages" in choice:
            self.stages = [
                SimulationStage(n_steps=2880, deg_per_step=1.0, name="Stage 1 (1.0°/step)"),
                SimulationStage(n_steps=2880, deg_per_step=0.5, name="Stage 2 (0.5°/step)"),
            ]
        elif "3 Stages" in choice:
            self.stages = [
                SimulationStage(n_steps=1440, deg_per_step=2.0, name="Stage 1 (2.0°/step)"),
                SimulationStage(n_steps=2880, deg_per_step=1.0, name="Stage 2 (1.0°/step)"),
                SimulationStage(n_steps=5760, deg_per_step=0.5, name="Stage 3 (0.5°/step)"),
            ]
        elif "4 Stages" in choice:
            self.stages = [
                SimulationStage(n_steps=1440, deg_per_step=2.0, name="Stage 1 (2.0°/step)"),
                SimulationStage(n_steps=2880, deg_per_step=1.0, name="Stage 2 (1.0°/step)"),
                SimulationStage(n_steps=5760, deg_per_step=0.5, name="Stage 3 (0.5°/step)"),
                SimulationStage(n_steps=11520, deg_per_step=0.25, name="Stage 4 (0.25°/step)"),
            ]
        elif "5 Stages" in choice:
            self.stages = [
                SimulationStage(n_steps=720, deg_per_step=5.0, name="Stage 1 (5.0°/step)"),
                SimulationStage(n_steps=1440, deg_per_step=2.0, name="Stage 2 (2.0°/step)"),
                SimulationStage(n_steps=2880, deg_per_step=1.0, name="Stage 3 (1.0°/step)"),
                SimulationStage(n_steps=5760, deg_per_step=0.5, name="Stage 4 (0.5°/step)"),
                SimulationStage(n_steps=11520, deg_per_step=0.25, name="Stage 5 (0.25°/step)"),
            ]
        elif "0.25° Fine" in choice:
            self.stages = [
                SimulationStage(n_steps=11520, deg_per_step=0.25, name="Fine Stage (0.25°/step)")
            ]
        else:
            self.stages = [
                SimulationStage(n_steps=2880, deg_per_step=1.0, name="Standard Stage (1.0°/step)")
            ]
        self._refresh_stages_view()

    def _generate(self):
        if not self.stages:
            messagebox.showwarning("No Stages", "Please add at least one simulation stage before generating.")
            return
        out_dir = self.v_dest.get().strip()
        if not out_dir:
            messagebox.showwarning("Missing Destination", "Please specify a destination folder.")
            return

        from vawt_post.engine.synthetic import generate_multistage_dataset
        try:
            try:
                target_cp = float(self.target_cp_entry.get().strip().replace(",", "."))
            except (ValueError, AttributeError):
                target_cp = 0.35

            try:
                settling = int(float(self.settling_revs_entry.get().strip().replace(",", ".")))
            except (ValueError, AttributeError):
                settling = 4

            try:
                target_ct = float(self.target_ct_entry.get().strip().replace(",", "."))
            except (ValueError, AttributeError):
                target_ct = 1.00

            _, inp_files, _ = generate_multistage_dataset(
                out_dir=out_dir,
                params=self.params,
                stages=self.stages,
                target_cp_final=target_cp,
                settling_revs=settling,
                ct_target=target_ct,
            )
            self.on_generated(inp_files, out_dir)
            messagebox.showinfo("Success", f"Multi-stage simulation files generated in:\n{out_dir}\n\nFiles loaded into analyzer.")
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Generation Error", str(exc))

