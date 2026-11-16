# -*- coding: utf-8 -*-
"""
Pytest configuration for VAWT Analyzer test suite.
"""
import sys
import pytest

# On Windows, elevate CRT maximum open file descriptors from default 512 to 2048,
# and ensure TCL/TK library environment variables point to the active Python installation.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.cdll.msvcrt._setmaxstdio(2048)
        ctypes.cdll.ucrtbase._setmaxstdio(2048)
    except Exception:
        pass

    import os
    tcl_base = os.path.join(sys.prefix, "tcl")
    if os.path.isdir(tcl_base):
        tcl_lib = os.path.join(tcl_base, "tcl8.6")
        tk_lib = os.path.join(tcl_base, "tk8.6")
        if os.path.isdir(tcl_lib) and not os.environ.get("TCL_LIBRARY"):
            os.environ["TCL_LIBRARY"] = tcl_lib
        if os.path.isdir(tk_lib) and not os.environ.get("TK_LIBRARY"):
            os.environ["TK_LIBRARY"] = tk_lib


@pytest.fixture(autouse=True)
def cleanup_matplotlib():
    """Ensure all matplotlib figures are closed after every test."""
    yield
    try:
        import matplotlib.pyplot as plt
        plt.close("all")
    except Exception:
        pass
