# -*- coding: utf-8 -*-
"""
VAWT Post Package Main Entry Point
==================================
Invoked when running `python -m vawt_post`.
"""
import sys
from vawt_post.cli import main

if __name__ == "__main__":
    sys.exit(main())
