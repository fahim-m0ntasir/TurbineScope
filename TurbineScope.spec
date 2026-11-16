# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/Fahim Montasir/Documents/Thesis/Analyzer/TurbineScope_Qt/run_vawt_studio.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/Fahim Montasir/Documents/Thesis/Analyzer/TurbineScope_Qt/vawt_post/assets', 'vawt_post/assets')],
    hiddenimports=['matplotlib.backends.backend_qtagg', 'matplotlib.backends.backend_agg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'pyright', 'pyflakes', 'IPython', 'jupyter', 'customtkinter', 'tkinterdnd2', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TurbineScope',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/Fahim Montasir/Documents/Thesis/Analyzer/TurbineScope_Qt/vawt_post/assets/app_icon.ico'],
)
