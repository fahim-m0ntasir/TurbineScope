# -*- coding: utf-8 -*-
"""
TurbineScope — VAWT CFD Post-Processor & Studio
===============================================
General-purpose vertical-axis wind turbine post-processing suite for ANSYS Fluent simulations.
"""
import os as _os

# Prevent OpenBLAS/MKL from reserving multi-thread arena buffers across every core at startup
for _env_k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_env_k, "1")

__version__ = "4.1.1"
