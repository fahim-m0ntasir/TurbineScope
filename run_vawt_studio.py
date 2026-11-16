# -*- coding: utf-8 -*-
"""
TurbineScope v4.1.1 — Standalone Executable Entry Point
=======================================================
Used by PyInstaller (`build_exe.py`) to package TurbineScope v4.1.1
into a single portable Windows executable (`TurbineScope.exe`).
"""
import sys
from vawt_post.cli import main

if __name__ == "__main__":
    sys.exit(main())
