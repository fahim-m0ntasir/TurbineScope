# -*- coding: utf-8 -*-
"""
VAWT Post Engine
================
Computational engine for ANSYS Fluent vertical-axis wind turbine post-processing.
Contains parsing, numerical analysis, publication plotting, Excel generation,
and multi-stage synthetic benchmark generation.
Strictly decoupled from any graphical user interface (GUI).
"""
from vawt_post.engine.config import Params, InputFiles, ConfigManager, FLUENT_DEFAULTS
from vawt_post.engine.parser import read_fluent_report, align_series, autodetect, detect_folder_simulation_parameters
from vawt_post.engine.analysis import analyze, run_pipeline
from vawt_post.engine.plots import make_figures
from vawt_post.engine.excel import write_excel
from vawt_post.engine.latex import export_latex_snippet
from vawt_post.engine.synthetic import (
    SimulationStage,
    generate_multistage_dataset,
    generate_dataset_a,
    generate_dataset_b,
)

__all__ = [
    "Params",
    "InputFiles",
    "ConfigManager",
    "FLUENT_DEFAULTS",
    "read_fluent_report",
    "align_series",
    "autodetect",
    "detect_folder_simulation_parameters",
    "analyze",
    "run_pipeline",
    "make_figures",
    "write_excel",
    "export_latex_snippet",
    "SimulationStage",
    "generate_multistage_dataset",
    "generate_dataset_a",
    "generate_dataset_b",
]
