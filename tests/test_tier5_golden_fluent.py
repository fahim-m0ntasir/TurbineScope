# -*- coding: utf-8 -*-
"""
Tier 5.2: Golden-File Tests with Real ANSYS Fluent Headers & File Formats
========================================================================
Tests parser robustness against different ANSYS Fluent version formats:
1. Classic Fluent 6.x / 12.x / 14.x format (bracketed headers, tab/space delimited)
2. Fluent 16.x - 18.x standard monitor definitions
3. Modern Fluent 19.x - 2024 multi-column report file with multiple quantities
"""
import numpy as np

from vawt_post.engine.parser import inspect_fluent_report, read_fluent_report


FLUENT_CLASSIC_HEADER = """\
"Rotor-Moment-r-1"
(
("Time-Step" "Flow-Time" "cm-rotor-1")
1 0.0005 0.12345
2 0.0010 0.12567
3 0.0015 0.12789
4 0.0020 0.13012
)
"""

FLUENT_MODERN_MULTICOLUMN = """\
"Rotor Aerodynamic Forces and Moments"
(
("Time-Step" "Flow-Time" "total-moment" "blade-1-moment" "blade-2-moment" "thrust-fx" "side-force-fy")
1 0.00025 3.456 1.152 1.148 45.2 12.1
2 0.00050 3.512 1.180 1.150 46.1 12.3
3 0.00075 3.568 1.205 1.155 47.0 12.5
4 0.00100 3.624 1.230 1.160 47.9 12.7
)
"""

FLUENT_UNBRACKETED_CLEAN = """\
Time-Step Flow-Time torque
1 0.001 0.85
2 0.002 0.87
3 0.003 0.89
4 0.004 0.91
"""


def test_5_2_golden_classic_fluent_format(tmp_path):
    """Verify parsing of classic Fluent 6-14 monitor format."""
    fpath = str(tmp_path / "fluent_classic.out")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(FLUENT_CLASSIC_HEADER)

    meta = inspect_fluent_report(fpath)
    assert meta["step_col"] == 0
    assert meta["time_col"] == 1
    assert meta["columns"] == ["Time-Step", "Flow-Time", "cm-rotor-1"]
    assert meta["row_count"] == 4

    data = read_fluent_report(fpath)
    assert len(data["step"]) == 4
    assert np.array_equal(data["step"], [1, 2, 3, 4])
    assert np.allclose(data["time"], [0.0005, 0.0010, 0.0015, 0.0020])
    assert np.allclose(data["value"], [0.12345, 0.12567, 0.12789, 0.13012])


def test_5_2_golden_modern_multicolumn_fluent_format(tmp_path):
    """Verify parsing and column selection for Fluent 19+ multi-column reports."""
    fpath = str(tmp_path / "fluent_multi.out")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(FLUENT_MODERN_MULTICOLUMN)

    meta = inspect_fluent_report(fpath)
    assert meta["is_multi_column"] is True
    assert len(meta["data_columns"]) == 5  # total-moment, blade-1, blade-2, thrust-fx, side-force-fy

    # Select total-moment column by name
    d_tot = read_fluent_report(fpath, column_name="total-moment")
    assert d_tot["selected_column"] == "total-moment"
    assert np.allclose(d_tot["value"], [3.456, 3.512, 3.568, 3.624])

    # Select blade-1-moment by name
    d_b1 = read_fluent_report(fpath, column_name="blade-1-moment")
    assert d_b1["selected_column"] == "blade-1-moment"
    assert np.allclose(d_b1["value"], [1.152, 1.180, 1.205, 1.230])

    # Select side-force by index (column index 6 in file)
    d_fy = read_fluent_report(fpath, column_idx=6)
    assert d_fy["selected_column"] == "side-force-fy"
    assert np.allclose(d_fy["value"], [12.1, 12.3, 12.5, 12.7])


def test_5_2_golden_unbracketed_clean_format(tmp_path):
    """Verify parsing of clean unbracketed text monitors."""
    fpath = str(tmp_path / "fluent_unbracketed.txt")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(FLUENT_UNBRACKETED_CLEAN)

    data = read_fluent_report(fpath)
    assert len(data["step"]) == 4
    assert np.allclose(data["value"], [0.85, 0.87, 0.89, 0.91])
