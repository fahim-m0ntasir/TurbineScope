# -*- coding: utf-8 -*-
"""
Build Portable Windows Executable (`TurbineScope.exe`)
======================================================
Packages TurbineScope v4.1.1 (Qt 6 / PySide6) into a single standalone `.exe`
with the custom VAWT rotor icon embedded for Explorer, Desktop, and Taskbar.

Usage:
    python build_exe.py
"""
from __future__ import annotations

import os
import subprocess
import sys

from vawt_post.icon_assets import ensure_app_icons


def build() -> int:
    root_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path, png_path = ensure_app_icons()
    assets_dir = os.path.dirname(ico_path)

    sep = ";" if sys.platform.startswith("win") else ":"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        "TurbineScope",
        "--icon",
        ico_path,
        "--add-data",
        f"{assets_dir}{sep}vawt_post/assets",
        "--hidden-import",
        "matplotlib.backends.backend_qtagg",
        "--hidden-import",
        "matplotlib.backends.backend_agg",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "pyright",
        "--exclude-module",
        "pyflakes",
        "--exclude-module",
        "IPython",
        "--exclude-module",
        "jupyter",
        "--exclude-module",
        "customtkinter",
        "--exclude-module",
        "tkinterdnd2",
        "--exclude-module",
        "PySide6.QtWebEngineCore",
        "--exclude-module",
        "PySide6.QtWebEngineWidgets",
        "--exclude-module",
        "PySide6.QtQuick",
        "--exclude-module",
        "PySide6.QtQml",
        "--exclude-module",
        "PySide6.Qt3DCore",
        "--exclude-module",
        "PySide6.QtMultimedia",
        os.path.join(root_dir, "run_vawt_studio.py"),
    ]

    print("Building single-file portable Windows executable (TurbineScope.exe)...")
    res = subprocess.run(cmd, cwd=root_dir)
    if res.returncode == 0:
        exe_out = os.path.join(root_dir, "dist", "TurbineScope.exe")
        print(f"\nBuild complete! Portable executable created at:\n  {exe_out}")
    return res.returncode


if __name__ == "__main__":
    sys.exit(build())
