from __future__ import annotations

from pathlib import Path

import matplotlib.image as mpimg

from ecad_paper_figures.compact_area_scatter import (
    legend_handles,
    render_area_scatter,
    scatter_rows,
)


def test_scatter_rows_retain_only_positive_supported_measurements() -> None:
    designs = [
        {
            "normalized_object_id": "a",
            "source_format": "Altium",
            "area_mm2": 100.0,
            "footprint_count": 20,
        },
        {
            "normalized_object_id": "b",
            "source_format": "Eagle",
            "area_mm2": None,
            "footprint_count": 10,
        },
        {
            "normalized_object_id": "c",
            "source_format": "unsupported",
            "area_mm2": 200.0,
            "footprint_count": 30,
        },
    ]
    assert scatter_rows(designs) == [
        {
            "normalized_object_id": "a",
            "source_format": "Altium",
            "area_mm2": 100.0,
            "area_rule": "outline_extent",
            "footprint_count": 20,
        }
    ]


def test_area_audit_replaces_plotted_area_and_marks_broken_outlines() -> None:
    designs = [
        {"normalized_object_id": "a", "source_format": "Altium", "area_mm2": 480000.0, "footprint_count": 400},
        {"normalized_object_id": "b", "source_format": "Eagle", "area_mm2": 0.06, "footprint_count": 15},
    ]
    audit = {
        "a": {"plotted_area_mm2": 6000.0, "area_rule": "rendered_board_extent"},
        "b": {"plotted_area_mm2": 0.06, "area_rule": "broken_outline"},
    }
    rows = scatter_rows(designs, audit)
    assert [(r["area_mm2"], r["area_rule"]) for r in rows] == [
        (6000.0, "rendered_board_extent"),
        (0.06, "broken_outline"),
    ]


def test_legend_handles_are_fully_opaque() -> None:
    handles = legend_handles(["Altium", "Eagle"])
    assert [handle.get_alpha() for handle in handles] == [1.0, 1.0]


def test_render_scatter_is_fixed_size_and_vector(tmp_path: Path) -> None:
    rows = [
        {
            "normalized_object_id": "a",
            "source_format": "Altium",
            "area_mm2": 100.0,
            "footprint_count": 20,
        },
        {
            "normalized_object_id": "b",
            "source_format": "Eagle",
            "area_mm2": 1000.0,
            "footprint_count": 100,
        },
    ]
    render_area_scatter(rows, tmp_path)
    pdf = (tmp_path / "area_vs_complexity.pdf").read_bytes()
    assert pdf.startswith(b"%PDF")
    assert b"/Subtype /Image" not in pdf
    image = mpimg.imread(tmp_path / "area_vs_complexity.png")
    assert image.shape[:2] == (1064, 3080)
