"""Render the compact, vector area-versus-complexity figure."""

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
import pyarrow as pa
import pyarrow.parquet as pq
from matplotlib.lines import Line2D

VERSION = "compact-area-scatter/v3"
FORMAT_ORDER = ["Altium", "Eagle", "KiCad", "Allegro", "Unknown / other"]
FORMAT_COLORS = {
    "Altium": "#6B4C9A",
    "Eagle": "#168C7A",
    "KiCad": "#D2692D",
    "Allegro": "#5C677D",
    "Unknown / other": "#9AA3AD",
}
GRID = "#D9DEE5"
TEXT = "#1F2933"
FIGURE_WIDTH_INCHES = 7.0
FIGURE_HEIGHT_INCHES = 2.42
PNG_DPI = 440
POINT_SIZE = 3.0
POINT_ALPHA = 0.20


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


def scatter_rows(
    designs: list[dict[str, Any]], audit: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """One point per measured object; an area audit (camera_ready_tables area-audit)
    replaces the plotted area and marks broken outlines."""
    return [
        {
            "normalized_object_id": str(row["normalized_object_id"]),
            "source_format": str(row["source_format"]),
            "area_mm2": float(audit[row["normalized_object_id"]]["plotted_area_mm2"]) if audit else float(row["area_mm2"]),
            "area_rule": str(audit[row["normalized_object_id"]]["area_rule"]) if audit else "outline_extent",
            "footprint_count": int(row["footprint_count"]),
        }
        for row in designs
        if row.get("area_mm2") is not None
        and float(row["area_mm2"]) > 0
        and row.get("footprint_count") is not None
        and int(row["footprint_count"]) > 0
        and str(row["source_format"]) in FORMAT_ORDER
    ]


def legend_handles(formats: list[str]) -> list[Line2D]:
    return [
        Line2D(
            [],
            [],
            linestyle="none",
            marker="o",
            markersize=4.5,
            markerfacecolor=FORMAT_COLORS[format_name],
            markeredgecolor="none",
            alpha=1.0,
            label=format_name,
        )
        for format_name in formats
    ]


def render_area_scatter(rows: list[dict[str, Any]], output_dir: Path) -> None:
    _setup()
    if not rows:
        raise ValueError("area-versus-complexity figure requires measured objects")
    present_formats = [
        format_name
        for format_name in FORMAT_ORDER
        if any(row["source_format"] == format_name for row in rows)
    ]
    figure, axis = plt.subplots(
        figsize=(FIGURE_WIDTH_INCHES, FIGURE_HEIGHT_INCHES),
        constrained_layout=True,
    )
    broken = [row for row in rows if row.get("area_rule") == "broken_outline"]
    for format_name in present_formats:
        points = [row for row in rows if row["source_format"] == format_name and row.get("area_rule") != "broken_outline"]
        axis.scatter(
            [row["area_mm2"] for row in points],
            [row["footprint_count"] for row in points],
            s=POINT_SIZE,
            alpha=POINT_ALPHA,
            color=FORMAT_COLORS[format_name],
            edgecolors="none",
            rasterized=False,
        )
    if broken:
        axis.scatter(
            [row["area_mm2"] for row in broken],
            [row["footprint_count"] for row in broken],
            s=POINT_SIZE * 3,
            facecolors="none",
            edgecolors="#1F2933",
            linewidths=0.5,
            rasterized=False,
        )
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.grid(color=GRID, linewidth=0.45)
    axis.set_axisbelow(True)
    axis.set_xlabel("Board area (mm²)", labelpad=1)
    axis.set_ylabel("Components", labelpad=1)
    axis.tick_params(axis="both", which="both", length=2, pad=1)
    axis.legend(
        handles=legend_handles(present_formats),
        ncols=5,
        frameon=False,
        loc="upper left",
        columnspacing=0.95,
        handletextpad=0.24,
    )
    axis.text(
        0.99,
        0.02,
        f"n={len(rows):,}",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=6,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        output_dir / "area_vs_complexity.png",
        dpi=PNG_DPI,
        facecolor="white",
        metadata={"Software": VERSION},
    )
    figure.savefig(
        output_dir / "area_vs_complexity.pdf",
        facecolor="white",
        metadata={"Creator": VERSION, "CreationDate": None, "ModDate": None},
    )
    plt.close(figure)


def _verify_completed(output_dir: Path, fingerprint: str) -> dict[str, Any]:
    request = json.loads((output_dir / "request.json").read_text(encoding="utf-8"))
    if request.get("request_fingerprint") != fingerprint:
        raise ValueError("completed compact scatter output has another fingerprint")
    success = json.loads((output_dir / "success.json").read_text(encoding="utf-8"))
    for relative, expected in success["outputs"].items():
        path = output_dir / relative
        if not path.is_file() or _sha256_file(path) != expected:
            raise ValueError(f"compact scatter checksum mismatch: {path}")
    return success


def build(
    *,
    design_metrics_path: Path,
    area_audit_path: Path | None = None,
    output_dir: Path,
    expected_designs: int,
    expected_points: int,
    source_fingerprint: str,
    code_revision: str,
) -> dict[str, Any]:
    if expected_designs < 1 or expected_points < 1:
        raise ValueError("expected row counts must be positive")
    if not code_revision or any(
        character not in "0123456789abcdef" for character in code_revision
    ):
        raise ValueError("code revision must be a lowercase hexadecimal commit ID")
    inputs = {"design_metrics": _sha256_file(design_metrics_path)}
    if area_audit_path is not None:
        inputs["area_audit"] = _sha256_file(area_audit_path)
    config = {
        "figure_width_inches": FIGURE_WIDTH_INCHES,
        "figure_height_inches": FIGURE_HEIGHT_INCHES,
        "png_dpi": PNG_DPI,
        "point_representation": "vector/v1",
        "point_size": POINT_SIZE,
        "point_alpha": POINT_ALPHA,
        "legend_alpha": 1.0,
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
    if len(designs) != expected_designs:
        raise ValueError(
            f"compact scatter input count mismatch: {len(designs)} designs"
        )
    audit = None
    if area_audit_path is not None:
        audit = {row["normalized_object_id"]: row for row in pq.read_table(area_audit_path).to_pylist()}
    rows = scatter_rows(designs, audit)
    if len(rows) != expected_points:
        raise ValueError(f"compact scatter point count mismatch: {len(rows)} points")
    rows.sort(key=lambda row: str(row["normalized_object_id"]))

    stage = output_dir.with_name(f"{output_dir.name}.tmp.{os.getpid()}")
    stage.mkdir(parents=True)
    render_area_scatter(rows, stage)
    pq.write_table(
        pa.Table.from_pylist(rows), stage / "scatter_points.parquet", compression="zstd"
    )
    format_counts = Counter(str(row["source_format"]) for row in rows)
    summary = {
        "schema_version": VERSION,
        "request_fingerprint": fingerprint,
        "normalized_objects": len(designs),
        "measured_objects": len(rows),
        "format_counts": {
            format_name: int(format_counts[format_name])
            for format_name in FORMAT_ORDER
            if format_name in format_counts
        },
        "counting_units_or_values_changed": area_audit_path is not None,
        "area_rule_counts": dict(Counter(str(row["area_rule"]) for row in rows)),
        "config": config,
        "inputs": inputs,
    }
    _write_json(stage / "summary.json", summary)
    _write_json(
        stage / "request.json",
        {
            "schema_version": f"{VERSION}-request",
            **payload,
            "request_fingerprint": fingerprint,
        },
    )
    output_names = [
        "area_vs_complexity.png",
        "area_vs_complexity.pdf",
        "scatter_points.parquet",
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
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--area-audit", type=Path)
    parser.add_argument("--expected-designs", type=int, required=True)
    parser.add_argument("--expected-points", type=int, required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                design_metrics_path=args.design_metrics,
                area_audit_path=args.area_audit,
                output_dir=args.output_dir,
                expected_designs=args.expected_designs,
                expected_points=args.expected_points,
                source_fingerprint=args.source_fingerprint,
                code_revision=args.code_revision,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
