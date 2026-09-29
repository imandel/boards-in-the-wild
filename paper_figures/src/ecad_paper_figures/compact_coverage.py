"""Render the compact, receipt-bound corpus coverage figure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

VERSION = "compact-corpus-coverage/v3"
BLUE = "#356AA0"
GREEN = "#218C74"
GRID = "#D9DEE5"
TEXT = "#1F2933"
FORMAT_ORDER = ["Altium", "Eagle", "KiCad", "Allegro", "Unknown / other"]
SOURCE_SHORT = {
    "OSHWA registry — other": "Other OSHWA",
    "Non-OSHWA GitHub / community": "Other GitHub/community",
    "Other vendor corpora": "Other vendors",
    "Multiple source groups": "Multiple sources",
}
FIGURE_WIDTH_INCHES = 7.0
FIGURE_HEIGHT_INCHES = 2.28
PNG_DPI = 220


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _setup() -> None:
    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 7.0,
            "axes.titlesize": 7.6,
            "axes.labelsize": 7.0,
            "xtick.labelsize": 6.2,
            "ytick.labelsize": 6.2,
            "legend.fontsize": 6.2,
            "text.color": TEXT,
            "axes.labelcolor": TEXT,
            "axes.edgecolor": "#89939E",
            "xtick.color": TEXT,
            "ytick.color": TEXT,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "savefig.transparent": False,
        }
    )


def coverage_rows(
    designs: list[dict[str, Any]], relations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    design_sources = Counter(str(row["source_group"]) for row in designs)
    relation_sources = Counter(str(row["source_group"]) for row in relations)
    design_formats = Counter(str(row["source_format"]) for row in designs)
    relation_formats = Counter(str(row["source_format"]) for row in relations)
    source_order = [category for category, _ in design_sources.most_common()]
    format_order = [category for category in FORMAT_ORDER if category in design_formats]
    format_order.extend(sorted(set(design_formats) - set(format_order)))
    result = []
    for dimension, categories, left, right in (
        ("source", source_order, design_sources, relation_sources),
        ("format", format_order, design_formats, relation_formats),
    ):
        result.extend(
            {
                "dimension": dimension,
                "category": category,
                "normalized_objects": int(left[category]),
                "accepted_mappings": int(right[category]),
                "display_order": index,
            }
            for index, category in enumerate(categories)
        )
    return result


def _grouped_bars(
    axis: plt.Axes,
    categories: list[str],
    normalized: list[int],
    accepted: list[int],
    title: str,
) -> None:
    y = np.arange(len(categories))
    height = 0.30
    offset = 0.22
    axis.barh(
        y - offset,
        normalized,
        height=height,
        color=BLUE,
        label="Normalized objects",
    )
    axis.barh(
        y + offset,
        accepted,
        height=height,
        color=GREEN,
        label="Accepted mappings",
    )
    axis.set_yticks(y, [SOURCE_SHORT.get(category, category) for category in categories])
    axis.invert_yaxis()
    axis.set_title(title, loc="left", fontweight="bold", pad=2)
    axis.grid(axis="x", color=GRID, linewidth=0.5)
    axis.set_axisbelow(True)
    axis.tick_params(axis="y", length=0, pad=2)
    axis.tick_params(axis="x", length=2, pad=1)
    maximum = max([*normalized, *accepted])
    axis.set_xlim(0, maximum * 1.18)
    for row, normalized_count, accepted_count in zip(
        y, normalized, accepted, strict=True
    ):
        axis.text(
            normalized_count + maximum * 0.012,
            row - offset,
            f"{normalized_count:,}",
            va="center",
            fontsize=5.1,
            color=BLUE,
        )
        axis.text(
            accepted_count + maximum * 0.012,
            row + offset,
            f"{accepted_count:,}",
            va="center",
            fontsize=5.1,
            color=GREEN,
        )


def render_coverage(rows: list[dict[str, Any]], output_dir: Path) -> None:
    _setup()
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for dimension in ("source", "format"):
        by_dimension[dimension] = sorted(
            (row for row in rows if row["dimension"] == dimension),
            key=lambda row: int(row["display_order"]),
        )
    if not by_dimension["source"] or not by_dimension["format"]:
        raise ValueError("coverage figure requires source and format rows")
    figure, axes = plt.subplots(
        1,
        2,
        figsize=(FIGURE_WIDTH_INCHES, FIGURE_HEIGHT_INCHES),
        gridspec_kw={"width_ratios": [1.63, 1]},
        constrained_layout=False,
    )
    figure.subplots_adjust(left=0.17, right=0.992, bottom=0.115, top=0.80, wspace=0.35)
    for axis, dimension, title in (
        (axes[0], "source", "a  Source"),
        (axes[1], "format", "b  Original ECAD format"),
    ):
        dimension_rows = by_dimension[dimension]
        _grouped_bars(
            axis,
            [str(row["category"]) for row in dimension_rows],
            [int(row["normalized_objects"]) for row in dimension_rows],
            [int(row["accepted_mappings"]) for row in dimension_rows],
            title,
        )
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        loc="upper center",
        ncols=2,
        frameon=False,
        bbox_to_anchor=(0.5, 0.92),
        borderaxespad=0,
        columnspacing=1.4,
        handlelength=2.2,
        handletextpad=0.45,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        output_dir / "corpus_coverage.png",
        dpi=PNG_DPI,
        facecolor="white",
        metadata={"Software": VERSION},
    )
    figure.savefig(
        output_dir / "corpus_coverage.pdf",
        facecolor="white",
        metadata={"Creator": VERSION, "CreationDate": None, "ModDate": None},
    )
    plt.close(figure)


def _verify_completed(output_dir: Path, fingerprint: str) -> dict[str, Any]:
    request = json.loads((output_dir / "request.json").read_text(encoding="utf-8"))
    if request.get("request_fingerprint") != fingerprint:
        raise ValueError("completed compact coverage output has another fingerprint")
    success = json.loads((output_dir / "success.json").read_text(encoding="utf-8"))
    for relative, expected in success["outputs"].items():
        path = output_dir / relative
        if not path.is_file() or _sha256_file(path) != expected:
            raise ValueError(f"compact coverage checksum mismatch: {path}")
    return success


def build(
    *,
    design_metrics_path: Path,
    accepted_relations_path: Path,
    output_dir: Path,
    expected_designs: int,
    expected_relations: int,
    source_fingerprint: str,
    code_revision: str,
) -> dict[str, Any]:
    if expected_designs < 1 or expected_relations < 1:
        raise ValueError("expected row counts must be positive")
    if not code_revision or any(character not in "0123456789abcdef" for character in code_revision):
        raise ValueError("code revision must be a lowercase hexadecimal commit ID")
    inputs = {
        "design_metrics": _sha256_file(design_metrics_path),
        "accepted_relations": _sha256_file(accepted_relations_path),
    }
    config = {
        "figure_width_inches": FIGURE_WIDTH_INCHES,
        "figure_height_inches": FIGURE_HEIGHT_INCHES,
        "png_dpi": PNG_DPI,
        "legend": "dedicated_top_center_strip/v1",
        "layout": "paired_horizontal_bars/v2",
        "source_fingerprint": source_fingerprint,
        "code_revision": code_revision,
    }
    payload = {"implementation": VERSION, "inputs": inputs, "config": config}
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if output_dir.exists():
        return _verify_completed(output_dir, fingerprint)

    designs = pq.read_table(design_metrics_path).to_pylist()
    relations = pq.read_table(accepted_relations_path).to_pylist()
    if len(designs) != expected_designs or len(relations) != expected_relations:
        raise ValueError(
            "compact coverage input count mismatch: "
            f"{len(designs)} designs/{len(relations)} relations"
        )
    rows = coverage_rows(designs, relations)
    if sum(row["normalized_objects"] for row in rows if row["dimension"] == "source") != len(
        designs
    ):
        raise ValueError("source coverage does not close over normalized objects")
    if sum(row["accepted_mappings"] for row in rows if row["dimension"] == "source") != len(
        relations
    ):
        raise ValueError("source coverage does not close over accepted mappings")

    stage = output_dir.with_name(f"{output_dir.name}.tmp.{os.getpid()}")
    stage.mkdir(parents=True)
    render_coverage(rows, stage)
    pq.write_table(pa.Table.from_pylist(rows), stage / "coverage_counts.parquet", compression="zstd")
    summary = {
        "schema_version": VERSION,
        "request_fingerprint": fingerprint,
        "normalized_objects": len(designs),
        "accepted_mappings": len(relations),
        "source_categories": sum(row["dimension"] == "source" for row in rows),
        "format_categories": sum(row["dimension"] == "format" for row in rows),
        "counting_units_or_values_changed": False,
        "config": config,
        "inputs": inputs,
    }
    _write_json(stage / "summary.json", summary)
    _write_json(
        stage / "request.json",
        {"schema_version": f"{VERSION}-request", **payload, "request_fingerprint": fingerprint},
    )
    output_names = [
        "corpus_coverage.png",
        "corpus_coverage.pdf",
        "coverage_counts.parquet",
        "summary.json",
        "request.json",
    ]
    success = {
        "schema_version": f"{VERSION}-success",
        "request_fingerprint": fingerprint,
        "outputs": {name: _sha256_file(stage / name) for name in output_names},
    }
    _write_json(stage / "success.json", success)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stage, output_dir)
    return success


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-metrics", type=Path, required=True)
    parser.add_argument("--accepted-relations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-designs", type=int, required=True)
    parser.add_argument("--expected-relations", type=int, required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                design_metrics_path=args.design_metrics,
                accepted_relations_path=args.accepted_relations,
                output_dir=args.output_dir,
                expected_designs=args.expected_designs,
                expected_relations=args.expected_relations,
                source_fingerprint=args.source_fingerprint,
                code_revision=args.code_revision,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
