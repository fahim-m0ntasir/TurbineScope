# -*- coding: utf-8 -*-
"""
Tests for AeroPost-VAWT: Advanced Aerodynamics & Non-Destructive Excel Embedding
================================================================================
Verifies:
1. Extraction of simulation data across sheets (Azimuth_Cp, Blade_Azimuth, Alpha_Re, Revolutions).
2. Generation of 6 publication-grade figures with LaTeX annotations and engineering formatting.
3. Non-destructive Excel embedding with openpyxl collision detection (scanning ws._images).
4. Aerodynamic stability summary computation (Cp, Cm, Ct, ripple factor, convergence).
5. Shaft moment autodetection and force classification.
"""
import os
import shutil
from typing import Any, List
import pytest
import openpyxl

from vawt_post.engine.excel_post import (
    extract_aeropost_data,
    generate_aeropost_figures,
    process_and_embed_aeropost_graphs,
)
from vawt_post.engine.parser import parse_report_filename_role


def test_aeropost_extraction_and_figure_generation(tmp_path):
    """Verify data extraction and 6 publication-grade figure generation."""
    src = "sample_output/VAWT_analysis_sample_20261010_113852.xlsx"
    if not os.path.isfile(src):
        pytest.skip(f"Sample workbook {src} not found")

    test_xl = str(tmp_path / "test_extract.xlsx")
    shutil.copyfile(src, test_xl)

    # 1. Extract data
    data = extract_aeropost_data(test_xl)
    assert "Azimuth_Cp" in data
    assert "Blade_Azimuth" in data
    assert "Alpha_Re" in data
    assert "Revolutions" in data

    df_az = data["Azimuth_Cp"]
    assert len(df_az) > 0
    assert "theta" in df_az.columns
    assert "cp" in df_az.columns
    assert "cm" in df_az.columns

    df_blade = data["Blade_Azimuth"]
    assert len(df_blade) > 0
    assert "t_blade_1" in df_blade.columns
    assert "cp_contrib" in df_blade.columns

    df_alpha = data["Alpha_Re"]
    assert len(df_alpha) > 0
    assert "alpha_geom" in df_alpha.columns
    assert "w_over_u" in df_alpha.columns

    df_rev = data["Revolutions"]
    assert len(df_rev) > 0
    assert "mean_cp" in df_rev.columns

    # 2. Generate 6 figures
    fig_dir = str(tmp_path / "figs")
    figs = generate_aeropost_figures(data, fig_dir)
    assert len(figs) == 6
    expected_keys = [
        "01_power_contribution_quartiles.png",
        "02_cumulative_moment_distribution.png",
        "03_blade_tangential_load.png",
        "04_geometric_kinematics.png",
        "05_global_rotor_coefficients.png",
        "06_cp_convergence_history.png",
    ]
    for k in expected_keys:
        assert k in figs
        assert os.path.isfile(figs[k])
        assert os.path.getsize(figs[k]) > 5000  # Valid non-empty PNG


def test_aeropost_non_destructive_embedding_preserves_existing_images(tmp_path):
    """Verify that existing images in Graphs sheet are preserved and new images placed below."""
    src = "sample_output/VAWT_analysis_sample_20261010_113852.xlsx"
    if not os.path.isfile(src):
        pytest.skip(f"Sample workbook {src} not found")

    test_xl = str(tmp_path / "test_preservation.xlsx")
    shutil.copyfile(src, test_xl)

    # Prepare test workbook with 11 initial figures to verify non-destructive appending
    wb_setup = openpyxl.load_workbook(test_xl)
    if "Graphs" in wb_setup.sheetnames:
        ws_g: Any = wb_setup["Graphs"]
        if hasattr(ws_g, "_images") and len(ws_g._images) > 11:
            ws_g._images = ws_g._images[:11]
            if ws_g.max_row >= 140:
                ws_g.delete_rows(140, ws_g.max_row - 140 + 1)
        if "Captions" in wb_setup.sheetnames:
            wcap_s = wb_setup["Captions"]
            if wcap_s.max_row > 14:
                wcap_s.delete_rows(15, wcap_s.max_row - 14 + 1)
        wb_setup.save(test_xl)
    wb_setup.close()

    # Check original image count and row anchors
    wb_orig = openpyxl.load_workbook(test_xl)
    ws_orig_g: Any = wb_orig["Graphs"]
    orig_img_count = len(getattr(ws_orig_g, "_images", []))
    wb_orig.close()
    assert orig_img_count == 11

    # Run full process and embed
    summary = process_and_embed_aeropost_graphs(test_xl)
    assert summary["cp_mean"] > 0
    assert summary["status"] in ("PERIODIC STABLE", "TRANSIENT/UNSTABLE")

    # Reload and verify preservation
    wb_new = openpyxl.load_workbook(test_xl)
    ws_new: Any = wb_new["Graphs"]
    images_list = getattr(ws_new, "_images", [])
    new_img_count = len(images_list)
    assert new_img_count == orig_img_count + 6

    # Verify that the new 6 images are placed strictly after the original images
    orig_anchors: List[int] = [
        getattr(img.anchor, "_from").row + 1
        for img in images_list[:orig_img_count]
        if hasattr(getattr(img, "anchor", None), "_from")
    ]
    max_orig_anchor = max(orig_anchors) if orig_anchors else 1

    new_anchors: List[int] = [
        getattr(img.anchor, "_from").row + 1
        for img in images_list[orig_img_count:]
        if hasattr(getattr(img, "anchor", None), "_from")
    ]
    min_new_anchor = min(new_anchors) if new_anchors else 999

    assert min_new_anchor > max_orig_anchor

    # Verify Captions sheet has been created/updated with academic captions
    assert "Captions" in wb_new.sheetnames
    wcap = wb_new["Captions"]
    caption_ids = [wcap.cell(r, 1).value for r in range(4, wcap.max_row + 1) if wcap.cell(r, 1).value]
    assert any(fig_id in caption_ids for fig_id in ["Fig 12", "Fig 13"])
    assert any(fig_id in caption_ids for fig_id in ["Fig 17", "Fig 18"])
    # Verify caption text contains quantitative KPIs
    assert any("Sectoral aerodynamic power contribution" in str(wcap.cell(r, 4).value) for r in range(4, wcap.max_row + 1))
    assert any("cumulative aerodynamic moment" in str(wcap.cell(r, 4).value).lower() for r in range(4, wcap.max_row + 1))

    # Verify Summary sheet contains AeroPost metrics and quartile breakdown
    assert "Summary" in wb_new.sheetnames
    wsum = wb_new["Summary"]
    summary_metrics = {str(wsum.cell(r, 1).value).strip(): wsum.cell(r, 2).value for r in range(1, wsum.max_row + 1) if wsum.cell(r, 1).value}
    assert any("moment coefficient cm" in k.lower() for k in summary_metrics)
    assert any("thrust coefficient ct" in k.lower() for k in summary_metrics)
    assert any("instantaneous cp" in k.lower() for k in summary_metrics)
    assert any("cp ripple" in k.lower() for k in summary_metrics)
    assert any("upwind sector" in k.lower() for k in summary_metrics)

    wb_new.close()


def test_shaft_moment_autodetection_and_force_classification():
    """Verify shaft force files are never classified as shaft moment or rotor force."""
    # Shaft forces
    role, _, _ = parse_report_filename_role("force_shaftx-rfile.out", 3)
    assert role == "shaft_fx"
    role, _, _ = parse_report_filename_role("force_shafty-rfile.out", 3)
    assert role == "shaft_fy"
    role, _, _ = parse_report_filename_role("shaft_fx-rfile.out", 3)
    assert role == "shaft_fx"
    role, _, _ = parse_report_filename_role("shaft_fy-rfile.out", 3)
    assert role == "shaft_fy"

    # Shaft moment
    role, _, _ = parse_report_filename_role("moment_shaft-rfile.out", 3)
    assert role == "shaft_moment"
    role, _, _ = parse_report_filename_role("shaft_moment-rfile.out", 3)
    assert role == "shaft_moment"
    role, _, _ = parse_report_filename_role("moment-shaft-rfile.out", 3)
    assert role == "shaft_moment"
