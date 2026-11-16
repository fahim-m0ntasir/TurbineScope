#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VAWT CFD Post-Processor (Forwarder Script)
===========================================
Notice: vawt_analyzer.py is the legacy monolithic entrypoint.
The post-processing and diagnostics engine has been modernized into the 'vawt_post' package.

This script forwards execution to 'vawt_post' (GUI or CLI).
For direct invocation, use:
    python -m vawt_post          # Graphical Interface
    python -m vawt_post.cli      # Command-line interface
"""
import sys
import warnings

warnings.warn(
    "vawt_analyzer.py is deprecated and maintained for backward compatibility only. "
    "Please use 'python -m vawt_post' directly.",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    if "--cli" in sys.argv:
        from vawt_post.cli import main
        main()
    else:
        from vawt_post.ui.app import main
        main()
