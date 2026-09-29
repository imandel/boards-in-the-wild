from __future__ import annotations

from pathlib import Path

import matplotlib.image as mpimg

from ecad_paper_figures.compact_coverage import coverage_rows, render_coverage


def _inputs() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    designs = [
        {"source_group": "TI", "source_format": "Altium"},
        {"source_group": "TI", "source_format": "Altium"},
        {"source_group": "SparkFun", "source_format": "Eagle"},
        {"source_group": "SparkFun", "source_format": "KiCad"},
    ]
    relations = [
        {"source_group": "TI", "source_format": "Altium"},
        {"source_group": "SparkFun", "source_format": "Eagle"},
    ]
    return designs, relations


def test_coverage_rows_close_over_each_counting_unit() -> None:
    designs, relations = _inputs()
    rows = coverage_rows(designs, relations)
    for dimension in ("source", "format"):
        selected = [row for row in rows if row["dimension"] == dimension]
        assert sum(row["normalized_objects"] for row in selected) == len(designs)
        assert sum(row["accepted_mappings"] for row in selected) == len(relations)
    format_categories = [
        row["category"] for row in rows if row["dimension"] == "format"
    ]
    assert format_categories == ["Altium", "Eagle", "KiCad"]


def test_render_coverage_uses_compact_fixed_canvas(tmp_path: Path) -> None:
    designs, relations = _inputs()
    render_coverage(coverage_rows(designs, relations), tmp_path)
    assert (tmp_path / "corpus_coverage.pdf").read_bytes().startswith(b"%PDF")
    image = mpimg.imread(tmp_path / "corpus_coverage.png")
    assert image.shape[:2] == (501, 1540)
