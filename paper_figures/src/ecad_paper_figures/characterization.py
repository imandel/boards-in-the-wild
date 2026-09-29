"""Build receipt-bound corpus-characterization tables and publication figures.

The design unit is one ``normalization_v2`` normalized object. The correspondence unit is one
accepted CAD-side--photo-region relation produced by the frozen paper policy. Aliases do not
inflate normalized-object counts, and accepted relations are not perceptually merged across
photographs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import stat
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ecad_paper_figures import __version__

SCHEMA_VERSION = "ecad-paper-corpus-characterization/v2"
PARSER_VERSION = "kicad-streaming-characteristics/v1"
REQUIRED_RELEASE_TABLES = (
    "normalized_aliases",
    "normalized_objects",
    "render_outcomes",
    "stage7_candidate_relations",
    "stage7_instance_clusters",
    "stage7_relation_cluster_crosswalk",
)
FORMAT_ORDER = ["Altium", "Eagle", "KiCad", "Allegro", "Unknown / other", "Multiple formats"]
LAYER_ORDER = ["2", "4", "6", "8", ">8", "Other", "Unknown"]
COMPONENT_ORDER = ["0", "1–10", "11–25", "26–50", "51–100", "101–200", "201–400", ">400", "Unknown"]
AREA_ORDER = ["<250", "250–500", "500–1k", "1–2.5k", "2.5–5k", "5–10k", "10–25k", ">25k", "Unknown"]
_FOOTPRINT_START_RE = re.compile(r'^\s*\((?:footprint|module)\s+(?:"([^"]+)"|([^\s()]+))')
_PROPERTY_REFERENCE_RE = re.compile(r'\(property\s+"Reference"\s+"([^"]*)"')
_FP_TEXT_REFERENCE_RE = re.compile(r'\(fp_text\s+reference\s+(?:"([^"]*)"|([^\s()]+))')
_LAYER_ROW_RE = re.compile(r'^\s*\(\d+\s+(?:"([^"]+)"|([^\s()]+))')
_MM_RE = re.compile(r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*mm\s*$")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path, *, chunk_size: int = 8 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _lexically_contained(path: Path, root: Path) -> bool:
    path_abs = os.path.abspath(path)
    root_abs = os.path.abspath(root)
    try:
        return os.path.commonpath((path_abs, root_abs)) == root_abs
    except ValueError:
        return False


def _direct_regular_file(path: Path, root: Path, expected_size: int | None = None) -> os.stat_result:
    if not _lexically_contained(path, root):
        raise ValueError(f"path escapes configured root: {path}")
    observed = os.lstat(path)
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError(f"not a direct regular file: {path}")
    if expected_size is not None and observed.st_size != expected_size:
        raise ValueError(
            f"size mismatch for {path}: expected {expected_size}, observed {observed.st_size}"
        )
    return observed


def _release_inventory(release_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = release_root / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory = {row["artifact_id"]: row for row in manifest["artifact_inventory"]}
    for table in REQUIRED_RELEASE_TABLES:
        row = inventory.get(table)
        if row is None or row.get("artifact_type") != "canonical_table":
            raise ValueError(f"release inventory lacks required canonical table: {table}")
        path = release_root / row["relative_path"]
        _direct_regular_file(path, release_root, int(row["size_bytes"]))
        if sha256_file(path) != row["sha256"]:
            raise ValueError(f"release table checksum mismatch: {path}")
        if pq.ParquetFile(path).metadata.num_rows != int(row["rows"]):
            raise ValueError(f"release table row-count mismatch: {path}")
    return manifest, inventory


def _single_or_multiple(values: set[str], *, multiple: str, unknown: str) -> str:
    clean = sorted(value for value in values if value)
    if not clean:
        return unknown
    return clean[0] if len(clean) == 1 else multiple


def source_group(logical_root_id: str, relative_path: str, config: dict[str, Any]) -> str:
    """Map one exact alias to a documented source category."""

    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        return config["unknown"]
    prefix = pure.parts[0].lower()
    named = {key.lower(): value for key, value in config["named_prefixes"].items()}
    if prefix in named:
        return named[prefix]
    if logical_root_id == config["oshwa_logical_root_id"]:
        return config["oshwa_fallback"]
    if prefix in {value.lower() for value in config["community_prefixes"]}:
        return config["community_fallback"]
    return config["vendor_fallback"]


def format_group(
    detected_format: str,
    implementation: str,
    config: dict[str, Any],
) -> str:
    """Map one normalization attempt to its original ECAD-family category."""

    implementation_lower = implementation.lower()
    for token, category in config["implementation_contains"].items():
        if token.lower() in implementation_lower:
            return category
    return config["detected_formats"].get(detected_format, config["unknown"])


def category_layers(value: int | None) -> str:
    if value is None:
        return "Unknown"
    if value in {2, 4, 6, 8}:
        return str(value)
    return ">8" if value > 8 else "Other"


def category_components(value: int | None) -> str:
    if value is None:
        return "Unknown"
    if value == 0:
        return "0"
    if value <= 10:
        return "1–10"
    if value <= 25:
        return "11–25"
    if value <= 50:
        return "26–50"
    if value <= 100:
        return "51–100"
    if value <= 200:
        return "101–200"
    if value <= 400:
        return "201–400"
    return ">400"


def category_area(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if value < 250:
        return "<250"
    if value < 500:
        return "250–500"
    if value < 1_000:
        return "500–1k"
    if value < 2_500:
        return "1–2.5k"
    if value < 5_000:
        return "2.5–5k"
    if value < 10_000:
        return "5–10k"
    if value < 25_000:
        return "10–25k"
    return ">25k"


def _paren_delta(line: str) -> int:
    delta = 0
    quoted = False
    escaped = False
    for char in line:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "(":
            delta += 1
        elif char == ")":
            delta -= 1
    return delta


def _package_size(name: str, package_sizes: tuple[str, ...]) -> str | None:
    for size in package_sizes:
        if re.search(rf"(?<!\d){re.escape(size)}(?!\d)", name):
            return size
    return None


def scan_kicad_characteristics(path: Path, package_sizes: tuple[str, ...]) -> dict[str, Any]:
    """Stream one normalized KiCad board and extract publication metrics."""

    copper_layers: set[str] = set()
    passive_sizes: set[str] = set()
    depth = 0
    layers_parent_depth: int | None = None
    footprint_parent_depth: int | None = None
    footprint_name = ""
    footprint_reference = ""
    saw_kicad_header = False

    def finish_footprint() -> None:
        nonlocal footprint_parent_depth, footprint_name, footprint_reference
        ref = footprint_reference.upper()
        if ref.startswith(("R", "C", "L")):
            size = _package_size(footprint_name, package_sizes)
            if size is not None:
                passive_sizes.add(size)
        footprint_parent_depth = None
        footprint_name = ""
        footprint_reference = ""

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number <= 128 and "(kicad_pcb" in line:
                saw_kicad_header = True
            before = depth
            if layers_parent_depth is None and re.match(r"^\s*\(layers(?:\s|$)", line):
                layers_parent_depth = before
            elif layers_parent_depth is not None:
                match = _LAYER_ROW_RE.match(line)
                if match:
                    layer_name = match.group(1) or match.group(2) or ""
                    if re.fullmatch(r"(?:F|B|In\d+)\.Cu", layer_name):
                        copper_layers.add(layer_name)

            if footprint_parent_depth is None:
                match = _FOOTPRINT_START_RE.match(line)
                if match:
                    footprint_parent_depth = before
                    footprint_name = match.group(1) or match.group(2) or ""
            if footprint_parent_depth is not None:
                match = _PROPERTY_REFERENCE_RE.search(line)
                if match:
                    footprint_reference = match.group(1)
                else:
                    match = _FP_TEXT_REFERENCE_RE.search(line)
                    if match:
                        footprint_reference = match.group(1) or match.group(2) or ""

            depth += _paren_delta(line)
            if layers_parent_depth is not None and depth <= layers_parent_depth:
                layers_parent_depth = None
            if footprint_parent_depth is not None and depth <= footprint_parent_depth:
                finish_footprint()

    if footprint_parent_depth is not None:
        finish_footprint()
    if not saw_kicad_header:
        return {
            "normalized_parse_status": "not_modern_kicad",
            "copper_layer_count": None,
            "passive_sizes": [],
        }
    return {
        "normalized_parse_status": "ok",
        "copper_layer_count": len(copper_layers) or None,
        "passive_sizes": sorted(passive_sizes, key=package_sizes.index),
    }


def _millimeters(value: Any) -> float | None:
    match = _MM_RE.match(str(value or ""))
    if not match:
        return None
    result = float(match.group(1))
    return result if math.isfinite(result) and result > 0 else None


def read_render_area(
    meta_path: Path | None,
    expected_sha256: str,
    allowed_root: Path,
) -> dict[str, Any]:
    if meta_path is None:
        return {"area_mm2": None, "geometry_status": "no_successful_render_metadata"}
    try:
        _direct_regular_file(meta_path, allowed_root)
        content = meta_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            return {"area_mm2": None, "geometry_status": "render_metadata_checksum_mismatch"}
        metadata = json.loads(content)
        width = _millimeters(metadata.get("svg_width"))
        height = _millimeters(metadata.get("svg_height"))
        if width is None or height is None:
            return {"area_mm2": None, "geometry_status": "missing_physical_svg_dimensions"}
        return {"area_mm2": width * height, "geometry_status": "ok"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "area_mm2": None,
            "geometry_status": f"metadata_error:{type(exc).__name__}",
        }


def _measure_object(
    object_row: dict[str, Any],
    render_row: dict[str, Any] | None,
    package_sizes: tuple[str, ...],
    normalized_root: Path,
    render_root: Path,
) -> dict[str, Any]:
    path = Path(object_row["normalized_path_runtime"])
    result = {
        "parser_version": PARSER_VERSION,
        "normalized_object_id": object_row["normalized_object_id"],
        "normalized_sha256": object_row["normalized_sha256"],
        "render_meta_sha256": str(render_row["render_meta_sha256"]) if render_row else "",
        "normalized_parse_status": "not_attempted",
        "copper_layer_count": None,
        "passive_sizes": [],
        "area_mm2": None,
        "geometry_status": "not_attempted",
    }
    try:
        _direct_regular_file(path, normalized_root, int(object_row["normalized_size_bytes"]))
        result.update(scan_kicad_characteristics(path, package_sizes))
    except (OSError, ValueError) as exc:
        result["normalized_parse_status"] = f"read_error:{type(exc).__name__}"
    meta_path = Path(render_row["render_meta_path"]) if render_row else None
    result.update(
        read_render_area(
            meta_path,
            str(render_row["render_meta_sha256"]) if render_row else "",
            render_root,
        )
    )
    return result


def _load_measurement_cache(
    path: Path,
    objects_by_id: dict[str, dict[str, Any]],
    renders_by_object: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return values
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                object_row = objects_by_id[row["normalized_object_id"]]
                render = renders_by_object.get(row["normalized_object_id"])
                render_sha = str(render["render_meta_sha256"]) if render else ""
                if (
                    row.get("parser_version") == PARSER_VERSION
                    and row.get("normalized_sha256") == object_row["normalized_sha256"]
                    and row.get("render_meta_sha256") == render_sha
                ):
                    values[row["normalized_object_id"]] = row
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return values


def _measure_objects(
    objects: list[dict[str, Any]],
    renders_by_object: dict[str, dict[str, Any]],
    package_sizes: tuple[str, ...],
    normalized_root: Path,
    render_root: Path,
    cache_path: Path,
    workers: int,
) -> dict[str, dict[str, Any]]:
    objects_by_id = {row["normalized_object_id"]: row for row in objects}
    values = _load_measurement_cache(cache_path, objects_by_id, renders_by_object)
    pending = [row for row in objects if row["normalized_object_id"] not in values]
    print(
        f"measurement cache: {len(values):,} valid; {len(pending):,} pending",
        flush=True,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as cache, ProcessPoolExecutor(
        max_workers=workers,
        mp_context=mp.get_context("spawn"),
    ) as pool:
        futures = {
            pool.submit(
                _measure_object,
                row,
                renders_by_object.get(row["normalized_object_id"]),
                package_sizes,
                normalized_root,
                render_root,
            ): row["normalized_object_id"]
            for row in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            object_id = futures[future]
            try:
                measured = future.result()
            except Exception as exc:  # defensive: retain a structured unknown row
                source = objects_by_id[object_id]
                render = renders_by_object.get(object_id)
                measured = {
                    "parser_version": PARSER_VERSION,
                    "normalized_object_id": object_id,
                    "normalized_sha256": source["normalized_sha256"],
                    "render_meta_sha256": str(render["render_meta_sha256"]) if render else "",
                    "normalized_parse_status": f"worker_error:{type(exc).__name__}",
                    "copper_layer_count": None,
                    "passive_sizes": [],
                    "area_mm2": None,
                    "geometry_status": "worker_error",
                }
            values[object_id] = measured
            cache.write(json.dumps(measured, sort_keys=True) + "\n")
            if index % 25 == 0:
                cache.flush()
            if index % 250 == 0 or index == len(pending):
                cache.flush()
                print(
                    f"measured {index:,}/{len(pending):,} new; "
                    f"{len(values):,}/{len(objects):,} total",
                    flush=True,
                )
    return values


def _preferred_render_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    candidates: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "succeeded" and row.get("render_meta_path"):
            candidates[row["normalized_object_id"]].append(row)
    result = {}
    for object_id, choices in candidates.items():
        result[object_id] = min(
            choices,
            key=lambda row: (0 if row.get("side") == "F" else 1, str(row["work_item_id"])),
        )
    return result


def _select_accepted_relations(
    candidates: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    crosswalk: list[dict[str, Any]],
    paper_policy: dict[str, Any],
    policy_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidate_by_pair = {str(row["pair_id"]): row for row in candidates}
    crosswalk_by_pair = {str(row["pair_id"]): row for row in crosswalk}
    cluster_by_id = {str(row["stage7_instance_cluster_id"]): row for row in clusters}
    if len(candidate_by_pair) != len(candidates):
        raise ValueError("Stage 7 candidate pair IDs are not unique")
    if len(crosswalk_by_pair) != len(crosswalk):
        raise ValueError("Stage 7 crosswalk pair IDs are not unique")
    if len(cluster_by_id) != len(clusters):
        raise ValueError("Stage 7 cluster IDs are not unique")
    if set(candidate_by_pair) != set(crosswalk_by_pair):
        raise ValueError("Stage 7 candidate/crosswalk pair closure mismatch")
    if paper_policy["policy_id"] != policy_config["policy_id"]:
        raise ValueError("configured and receipt-bound paper policy IDs differ")

    policy_rules = paper_policy["policy"]
    if policy_rules["window_status"] != "primary_accepted":
        raise ValueError("unsupported paper-policy window status")
    maximum_delta = float(policy_rules["maximum_loma_roma_corner_delta"])
    required_label = str(policy_rules["predicted_label"])
    required_cluster_relations = int(policy_rules["spatial_cluster_relation_count"])
    required_route = str(policy_config["predicted_route"])

    selected: list[dict[str, Any]] = []
    for relation in sorted(candidates, key=lambda row: str(row["relation_id"])):
        pair_id = str(relation["pair_id"])
        relation_crosswalk = crosswalk_by_pair[pair_id]
        cluster_id = str(relation_crosswalk["stage7_instance_cluster_id"])
        cluster = cluster_by_id.get(cluster_id)
        if cluster is None:
            raise ValueError("Stage 7 crosswalk references an unknown cluster")
        if (
            str(relation["predicted_route"]) != required_route
            or str(relation["predicted_label"]) != required_label
            or float(relation["loma_roma_corner_delta"]) > maximum_delta
            or int(cluster["relation_count"]) != required_cluster_relations
        ):
            continue
        selected.append(
            {
                "relation_id": str(relation["relation_id"]),
                "pair_id": pair_id,
                "stage7_instance_cluster_id": cluster_id,
                "photo_file_location_id": str(relation["photo_file_location_id"]),
                "normalized_object_id": str(relation["normalized_object_id"]),
                "source_format_raw": str(relation_crosswalk["source_format"]),
                "side": str(relation["side"]),
                "paper_policy_id": paper_policy["policy_id"],
            }
        )

    observed_counts = {
        "relations": len(selected),
        "spatial_clusters": len({row["stage7_instance_cluster_id"] for row in selected}),
        "photo_locations": len({row["photo_file_location_id"] for row in selected}),
        "normalized_objects": len({row["normalized_object_id"] for row in selected}),
    }
    expected_counts = {
        key: int(value) for key, value in policy_config["expected_counts"].items()
    }
    if observed_counts != expected_counts:
        raise ValueError(
            f"accepted-relation policy count mismatch: {observed_counts} != {expected_counts}"
        )
    if observed_counts["relations"] != observed_counts["spatial_clusters"]:
        raise ValueError("accepted paper relations must occupy singleton spatial clusters")
    return selected, observed_counts


def _rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows)


def _write_csv(table: pa.Table, path: Path) -> None:
    import pyarrow.csv as pacsv

    pacsv.write_csv(table, path)


def _counts(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(row[key]) for row in rows)


def _coverage_rows(
    design_rows: list[dict[str, Any]],
    accepted_relation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for dimension, key in (("source", "source_group"), ("format", "source_format")):
        for tier, rows in (
            ("normalized_objects", design_rows),
            ("accepted_relations", accepted_relation_rows),
        ):
            for category, count in sorted(_counts(rows, key).items()):
                result.append(
                    {
                        "dimension": dimension,
                        "category": category,
                        "tier": tier,
                        "count": count,
                    }
                )
    return result


def _ordered_counts(
    rows: list[dict[str, Any]],
    field: str,
    order: list[str],
) -> tuple[list[str], list[int]]:
    counts = _counts(rows, field)
    categories = [value for value in order if value in counts]
    categories.extend(sorted(set(counts) - set(categories)))
    return categories, [counts[value] for value in categories]


def _plot_outputs(fig: Any, output: Path, stem: str) -> list[str]:
    outputs = []
    for suffix in ("pdf", "svg", "png"):
        path = output / f"{stem}.{suffix}"
        if suffix == "pdf":
            fig.savefig(
                path,
                bbox_inches="tight",
                metadata={"Creator": "ecad-paper-figures", "CreationDate": None, "ModDate": None},
            )
        elif suffix == "svg":
            fig.savefig(path, bbox_inches="tight", metadata={"Creator": "ecad-paper-figures", "Date": None})
        else:
            fig.savefig(path, bbox_inches="tight", dpi=300, metadata={"Software": "ecad-paper-figures"})
        outputs.append(path.name)
    return outputs


def _horizontal_bars(ax: Any, categories: list[str], values: list[int], color: str, title: str) -> None:
    positions = list(range(len(categories)))
    ax.barh(positions, values, color=color)
    ax.set_yticks(positions, labels=categories)
    ax.invert_yaxis()
    ax.set_title(title, loc="left", fontweight="bold")
    ax.grid(axis="x", color="#E3E7EB", linewidth=0.7)
    ax.set_axisbelow(True)
    maximum = max(values, default=0)
    for position, value in zip(positions, values, strict=True):
        ax.text(value + maximum * 0.012, position, f"{value:,}", va="center", fontsize=7)
    ax.set_xlim(0, maximum * 1.18 if maximum else 1)


def render_figures(
    design_rows: list[dict[str, Any]],
    accepted_relation_rows: list[dict[str, Any]],
    output: Path,
    cohort_label: str,
    package_sizes: tuple[str, ...],
) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.hashsalt": "ecad-paper-figures-v1",
        }
    )
    import matplotlib.pyplot as plt

    assets: list[str] = []

    source_design_order, source_design_values = _ordered_counts(
        design_rows,
        "source_group",
        sorted(_counts(design_rows, "source_group"), key=_counts(design_rows, "source_group").get, reverse=True),
    )
    source_accepted_counts = _counts(accepted_relation_rows, "source_group")
    source_accepted_values = [source_accepted_counts.get(value, 0) for value in source_design_order]
    format_design_order, format_design_values = _ordered_counts(design_rows, "source_format", FORMAT_ORDER)
    format_accepted_counts = _counts(accepted_relation_rows, "source_format")
    format_accepted_values = [format_accepted_counts.get(value, 0) for value in format_design_order]

    figure, axes = plt.subplots(2, 2, figsize=(11.3, 8.1), constrained_layout=True)
    _horizontal_bars(
        axes[0, 0],
        source_design_order,
        source_design_values,
        "#2E5EAA",
        f"Normalized objects by source (n={len(design_rows):,})",
    )
    _horizontal_bars(
        axes[0, 1],
        source_design_order,
        source_accepted_values,
        "#2A9D8F",
        f"Accepted CAD-side–photo-region relations by source (n={len(accepted_relation_rows):,})",
    )
    _horizontal_bars(
        axes[1, 0],
        format_design_order,
        format_design_values,
        "#6B4C9A",
        "Normalized objects by original ECAD format",
    )
    _horizontal_bars(
        axes[1, 1],
        format_design_order,
        format_accepted_values,
        "#2A9D8F",
        "Accepted CAD-side–photo-region relations by original ECAD format",
    )
    figure.suptitle(f"Corpus coverage — {cohort_label}", x=0.01, ha="left", fontweight="bold")
    assets.extend(_plot_outputs(figure, output, "corpus_coverage"))
    plt.close(figure)

    layer_categories, layer_values = _ordered_counts(design_rows, "copper_layer_category", LAYER_ORDER)
    component_categories, component_values = _ordered_counts(
        design_rows, "component_category", COMPONENT_ORDER
    )
    area_categories, area_values = _ordered_counts(design_rows, "area_category", AREA_ORDER)
    parsed = [row for row in design_rows if row["normalized_parse_status"] == "ok"]
    package_values = [
        sum(size in row["passive_sizes"] for row in parsed) * 100.0 / len(parsed)
        if parsed
        else 0.0
        for size in package_sizes
    ]
    figure, axes = plt.subplots(2, 2, figsize=(11.3, 7.2), constrained_layout=True)
    for ax, categories, values, color, title, ylabel in (
        (axes[0, 0], layer_categories, layer_values, "#3D8C74", "Copper layers", "Normalized objects"),
        (axes[0, 1], component_categories, component_values, "#C66A2B", "Components per design", "Normalized objects"),
        (axes[1, 0], area_categories, area_values, "#AA4465", "Board area (mm²)", "Normalized objects"),
        (axes[1, 1], list(package_sizes), package_values, "#B35B7B", "Passive package prevalence", "Parsed objects containing package (%)"),
    ):
        positions = list(range(len(categories)))
        ax.bar(positions, values, color=color)
        ax.set_xticks(positions, labels=categories, rotation=28, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontweight="bold")
        ax.grid(axis="y", color="#E3E7EB", linewidth=0.7)
        ax.set_axisbelow(True)
    figure.suptitle(
        f"Normalized-design characteristics — normalization v2; "
        f"one count per normalized object (n={len(design_rows):,})",
        x=0.01,
        ha="left",
        fontweight="bold",
    )
    assets.extend(_plot_outputs(figure, output, "design_characteristics"))
    plt.close(figure)

    scatter = [
        row
        for row in design_rows
        if row["area_mm2"] is not None
        and row["area_mm2"] > 0
        and row["footprint_count"] is not None
        and row["footprint_count"] > 0
    ]
    colors = {
        "Altium": "#6B4C9A",
        "Eagle": "#2A9D8F",
        "KiCad": "#C66A2B",
        "Allegro": "#5C677D",
        "Unknown / other": "#7F8C8D",
        "Multiple formats": "#9B59B6",
    }
    figure, ax = plt.subplots(figsize=(8.4, 5.6), constrained_layout=True)
    for category in FORMAT_ORDER:
        points = [row for row in scatter if row["source_format"] == category]
        if not points:
            continue
        ax.scatter(
            [row["area_mm2"] for row in points],
            [row["footprint_count"] for row in points],
            s=9,
            alpha=0.28,
            color=colors[category],
            edgecolors="none",
            label=category,
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Board area (mm², log scale)")
    ax.set_ylabel("Components per normalized object (log scale)")
    ax.set_title(
        "Area versus component complexity — normalization v2\n"
        f"n={len(scatter):,}; each normalized object counted once",
        loc="left",
        fontweight="bold",
    )
    ax.grid(color="#E3E7EB", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(title="Original ECAD format", frameon=False, ncols=3, loc="upper left")
    assets.extend(_plot_outputs(figure, output, "area_vs_complexity"))
    plt.close(figure)

    return assets


def _asset_checksums(directory: Path) -> dict[str, str]:
    return {
        path.name: sha256_file(path)
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "success.json"
    }


def _verify_completed(output_dir: Path, request_fingerprint: str) -> dict[str, Any]:
    success_path = output_dir / "success.json"
    if not success_path.is_file():
        raise FileExistsError(f"incomplete figure output exists: {output_dir}")
    success = json.loads(success_path.read_text(encoding="utf-8"))
    if success.get("request_fingerprint") != request_fingerprint:
        raise FileExistsError(f"completed figure output has different inputs: {output_dir}")
    for name, expected in success["artifact_checksums"].items():
        path = output_dir / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"completed figure artifact checksum mismatch: {path}")
    return success


def _paper_policy(evaluation_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    success_path = evaluation_dir / "success.json"
    policy_path = evaluation_dir / "policy.json"
    _direct_regular_file(success_path, evaluation_dir)
    _direct_regular_file(policy_path, evaluation_dir)
    success = json.loads(success_path.read_text(encoding="utf-8"))
    expected = success.get("outputs", {}).get("policy.json")
    if not expected or sha256_file(policy_path) != expected:
        raise ValueError("paper-evaluation policy checksum mismatch")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("policy_id") != "primary-single-populated-delta002-singleton/v1":
        raise ValueError("unexpected paper acceptance policy")
    return policy, success


def _request(
    release_root: Path,
    release_manifest: dict[str, Any],
    inventory: dict[str, dict[str, Any]],
    normalization_success: dict[str, Any],
    paper_evaluation_dir: Path,
    paper_policy: dict[str, Any],
    paper_evaluation_success: dict[str, Any],
    config_path: Path,
) -> dict[str, Any]:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "implementation_version": __version__,
        "implementation_sha256": sha256_file(Path(__file__)),
        "tool_versions": {
            "matplotlib": package_version("matplotlib"),
            "pyarrow": package_version("pyarrow"),
        },
        "parser_version": PARSER_VERSION,
        "release_id": release_manifest["release_id"],
        "release_build_fingerprint": release_manifest["build_fingerprint"],
        "release_manifest_sha256": sha256_file(release_root / "release-manifest.json"),
        "release_table_sha256s": {
            name: inventory[name]["sha256"] for name in REQUIRED_RELEASE_TABLES
        },
        "normalization_composition_fingerprint": normalization_success[
            "composition_fingerprint"
        ],
        "normalization_attempts_sha256": normalization_success["artifact_checksums"][
            "attempts.parquet"
        ],
        "paper_policy_id": paper_policy["policy_id"],
        "paper_policy_sha256": sha256_file(paper_evaluation_dir / "policy.json"),
        "paper_evaluation_request_fingerprint": paper_evaluation_success[
            "request_fingerprint"
        ],
        "paper_evaluation_success_sha256": sha256_file(
            paper_evaluation_dir / "success.json"
        ),
        "config_sha256": sha256_file(config_path),
    }
    identity["request_fingerprint"] = hashlib.sha256(_json_bytes(identity)).hexdigest()
    return identity


def build_characterization(
    *,
    release_root: Path,
    normalization_dir: Path,
    paper_evaluation_dir: Path,
    config_path: Path,
    normalized_runtime_root: Path,
    render_runtime_root: Path,
    output_dir: Path,
    measurement_cache: Path,
    workers: int,
) -> dict[str, Any]:
    release_manifest, inventory = _release_inventory(release_root)
    paper_policy, paper_evaluation_success = _paper_policy(paper_evaluation_dir)
    normalization_success_path = normalization_dir / "success.json"
    normalization_success = json.loads(normalization_success_path.read_text(encoding="utf-8"))
    attempts_path = normalization_dir / "attempts.parquet"
    if sha256_file(attempts_path) != normalization_success["artifact_checksums"][
        "attempts.parquet"
    ]:
        raise ValueError("normalization-v2 attempts checksum mismatch")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    request = _request(
        release_root,
        release_manifest,
        inventory,
        normalization_success,
        paper_evaluation_dir,
        paper_policy,
        paper_evaluation_success,
        config_path,
    )
    if output_dir.exists():
        return _verify_completed(output_dir, request["request_fingerprint"])

    table_path = {
        name: release_root / inventory[name]["relative_path"] for name in REQUIRED_RELEASE_TABLES
    }
    objects = pq.read_table(table_path["normalized_objects"]).to_pylist()
    aliases = pq.read_table(table_path["normalized_aliases"]).to_pylist()
    render_rows = pq.read_table(table_path["render_outcomes"]).to_pylist()
    candidates = pq.read_table(table_path["stage7_candidate_relations"]).to_pylist()
    clusters = pq.read_table(table_path["stage7_instance_clusters"]).to_pylist()
    crosswalk = pq.read_table(table_path["stage7_relation_cluster_crosswalk"]).to_pylist()
    attempts = pq.read_table(attempts_path).to_pylist()
    selected_relation_rows, observed_counts = _select_accepted_relations(
        candidates,
        clusters,
        crosswalk,
        paper_policy,
        config["accepted_relation_policy"],
    )

    objects_by_id = {row["normalized_object_id"]: row for row in objects}
    if len(objects_by_id) != len(objects):
        raise ValueError("normalized-object IDs are not unique")
    aliases_by_object: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for alias in aliases:
        if alias["normalized_object_id"] not in objects_by_id:
            raise ValueError("normalized alias references an unknown object")
        aliases_by_object[alias["normalized_object_id"]].append(alias)
    if set(aliases_by_object) != set(objects_by_id):
        raise ValueError("every normalized object must retain at least one alias")

    attempts_by_content = {row["content_sha256"]: row for row in attempts}
    if len(attempts_by_content) != len(attempts):
        raise ValueError("normalization attempts are not unique by raw content hash")
    renders_by_object = _preferred_render_rows(render_rows)
    package_sizes = tuple(config["categories"]["passive_package_sizes"])
    measurements = _measure_objects(
        objects,
        renders_by_object,
        package_sizes,
        normalized_runtime_root,
        render_runtime_root,
        measurement_cache,
        workers,
    )

    source_config = config["source_groups"]
    format_config = config["format_groups"]
    design_rows: list[dict[str, Any]] = []
    for object_row in sorted(objects, key=lambda row: row["normalized_object_id"]):
        object_id = object_row["normalized_object_id"]
        object_aliases = aliases_by_object[object_id]
        source_values = {
            source_group(alias["logical_root_id"], alias["relative_path"], source_config)
            for alias in object_aliases
        }
        format_values = set()
        footprint_values = set()
        pad_values = set()
        for alias in object_aliases:
            attempt = attempts_by_content.get(alias["raw_content_sha256"])
            if attempt is None:
                raise ValueError(f"normalized alias lacks normalization attempt: {alias['alias_id']}")
            format_values.add(
                format_group(
                    str(attempt.get("parent_detected_format") or ""),
                    str(alias.get("normalization_implementation") or ""),
                    format_config,
                )
            )
            footprint_values.add(int(alias["footprint_count"]))
            pad_values.add(int(alias["pad_count"]))
        if len(footprint_values) != 1 or len(pad_values) != 1:
            raise ValueError(f"object aliases disagree on structural counts: {object_id}")
        measured = measurements[object_id]
        area = measured.get("area_mm2")
        copper = measured.get("copper_layer_count")
        footprint_count = next(iter(footprint_values))
        design_rows.append(
            {
                "normalized_object_id": object_id,
                "normalized_sha256": object_row["normalized_sha256"],
                "source_group": _single_or_multiple(
                    source_values,
                    multiple=source_config["multiple"],
                    unknown=source_config["unknown"],
                ),
                "source_format": _single_or_multiple(
                    format_values,
                    multiple=format_config["multiple"],
                    unknown=format_config["unknown"],
                ),
                "alias_count": len(object_aliases),
                "footprint_count": footprint_count,
                "pad_count": next(iter(pad_values)),
                "component_category": category_components(footprint_count),
                "copper_layer_count": copper,
                "copper_layer_category": category_layers(copper),
                "area_mm2": area,
                "area_category": category_area(area),
                "passive_sizes": measured.get("passive_sizes", []),
                "normalized_parse_status": measured["normalized_parse_status"],
                "geometry_status": measured["geometry_status"],
                "accepted_relation_count": 0,
            }
        )

    design_by_id = {row["normalized_object_id"]: row for row in design_rows}
    accepted_relation_rows: list[dict[str, Any]] = []
    source_format_map = {
        "eagle": "Eagle",
        "hw_allegro": "Allegro",
        "hw_altium": "Altium",
        "kicad": "KiCad",
    }
    for selected in selected_relation_rows:
        design = design_by_id[selected["normalized_object_id"]]
        design["accepted_relation_count"] += 1
        accepted_relation_rows.append(
            {
                **selected,
                "source_group": design["source_group"],
                "source_format": source_format_map.get(
                    selected["source_format_raw"], format_config["unknown"]
                ),
            }
        )

    coverage_rows = _coverage_rows(design_rows, accepted_relation_rows)
    stage = output_dir.with_name(f"{output_dir.name}.tmp.{os.getpid()}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, mode=0o770)
    design_table = _rows_to_table(design_rows)
    relation_table = _rows_to_table(accepted_relation_rows)
    coverage_table = _rows_to_table(coverage_rows)
    pq.write_table(design_table, stage / "design_metrics.parquet", compression="zstd")
    pq.write_table(relation_table, stage / "accepted_relations.parquet", compression="zstd")
    pq.write_table(coverage_table, stage / "coverage.parquet", compression="zstd")
    _write_csv(coverage_table, stage / "coverage.csv")

    parse_counts = Counter(row["normalized_parse_status"] for row in design_rows)
    geometry_counts = Counter(row["geometry_status"] for row in design_rows)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "request_fingerprint": request["request_fingerprint"],
        "release_id": release_manifest["release_id"],
        "release_status": release_manifest["release_status"],
        "cohort_status": "complete_frozen_paper_accepted_relation_set",
        "paper_policy_id": paper_policy["policy_id"],
        "units": {
            "design": "one normalization_v2 normalized_object_id",
            "accepted_correspondence": "one accepted CAD-side--photo-region relation",
            "source": "one mutually exclusive source group per design/relation; multi-source provenance remains explicit",
            "format": "one original ECAD family per design/relation; multi-format provenance remains explicit",
        },
        "normalized_objects": len(design_rows),
        "normalized_aliases": len(aliases),
        "accepted_relations": observed_counts["relations"],
        "accepted_spatial_clusters": observed_counts["spatial_clusters"],
        "accepted_photo_locations": observed_counts["photo_locations"],
        "accepted_distinct_normalized_objects": observed_counts["normalized_objects"],
        "parse_status_counts": dict(sorted(parse_counts.items())),
        "geometry_status_counts": dict(sorted(geometry_counts.items())),
        "source_coverage": [row for row in coverage_rows if row["dimension"] == "source"],
        "format_coverage": [row for row in coverage_rows if row["dimension"] == "format"],
        "request": request,
        "runtime_inputs": {
            "release_root": str(release_root),
            "normalization_dir": str(normalization_dir),
            "paper_evaluation_dir": str(paper_evaluation_dir),
            "normalized_runtime_root": str(normalized_runtime_root),
            "render_runtime_root": str(render_runtime_root),
            "measurement_cache": str(measurement_cache),
        },
    }
    _write_json(stage / "summary.json", summary)
    _write_json(stage / "request.json", request)
    render_figures(
        design_rows,
        accepted_relation_rows,
        stage,
        f"paper accepted relations (n={len(accepted_relation_rows):,})",
        package_sizes,
    )

    validation = validate_output(stage, summary)
    _write_json(stage / "validation.json", validation)
    index = (
        "<!doctype html><meta charset='utf-8'><title>ECAD corpus characterization</title>"
        "<style>body{font:15px system-ui;margin:24px;color:#18212b;max-width:1200px}"
        "section{border:1px solid #d8dde3;padding:16px;margin:20px 0}img{max-width:100%}"
        "a{color:#075a9c}</style>"
        "<h1>Corpus characterization: complete paper accepted-relation set</h1>"
        f"<p><strong>{paper_policy['policy_id']}; "
        f"n={len(accepted_relation_rows):,} relations.</strong></p>"
        + "".join(
            f"<section><h2>{stem.replace('_', ' ').title()}</h2>"
            f"<p><a href='{stem}.pdf'>PDF</a> · <a href='{stem}.svg'>SVG</a> · "
            f"<a href='{stem}.png'>PNG</a></p><img src='{stem}.png'></section>"
            for stem in ("corpus_coverage", "design_characteristics", "area_vs_complexity")
        )
    )
    (stage / "index.html").write_text(index, encoding="utf-8")
    checksums = _asset_checksums(stage)
    success = {
        "schema_version": SCHEMA_VERSION,
        "request_fingerprint": request["request_fingerprint"],
        "release_id": release_manifest["release_id"],
        "artifact_checksums": checksums,
        "normalized_objects": len(design_rows),
        "accepted_relations": len(accepted_relation_rows),
        "validation_status": validation["status"],
    }
    _write_json(stage / "success.json", success)
    os.replace(stage, output_dir)
    return success


def validate_output(output: Path, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    if summary is None:
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    designs = pq.read_table(output / "design_metrics.parquet").to_pylist()
    relations = pq.read_table(output / "accepted_relations.parquet").to_pylist()
    coverage = pq.read_table(output / "coverage.parquet").to_pylist()
    if len(designs) != summary["normalized_objects"]:
        raise AssertionError("design table count does not match summary")
    if len({row["normalized_object_id"] for row in designs}) != len(designs):
        raise AssertionError("design table is not unique by normalized_object_id")
    if len(relations) != summary["accepted_relations"]:
        raise AssertionError("accepted-relation table count does not match summary")
    for field in ("relation_id", "pair_id", "stage7_instance_cluster_id"):
        if len({row[field] for row in relations}) != len(relations):
            raise AssertionError(f"accepted-relation table is not unique by {field}")
    for dimension in ("source", "format"):
        for tier, expected in (
            ("normalized_objects", len(designs)),
            ("accepted_relations", len(relations)),
        ):
            observed = sum(
                row["count"]
                for row in coverage
                if row["dimension"] == dimension and row["tier"] == tier
            )
            if observed != expected:
                raise AssertionError(f"{dimension}/{tier} coverage does not sum to {expected}")
    asset_bytes = {}
    for stem in ("corpus_coverage", "design_characteristics", "area_vs_complexity"):
        for suffix, magic in (("pdf", b"%PDF"), ("svg", b"<?xml"), ("png", b"\x89PNG")):
            path = output / f"{stem}.{suffix}"
            content = path.read_bytes()
            if not content.startswith(magic):
                raise AssertionError(f"invalid figure asset: {path}")
            asset_bytes[path.name] = len(content)
    return {
        "status": "passed",
        "normalized_objects": len(designs),
        "accepted_relations": len(relations),
        "assets": asset_bytes,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build tables and publication assets atomically")
    build.add_argument("--release-root", type=Path, required=True)
    build.add_argument("--normalization-dir", type=Path, required=True)
    build.add_argument("--paper-evaluation-dir", type=Path, required=True)
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--normalized-runtime-root", type=Path, required=True)
    build.add_argument("--render-runtime-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--measurement-cache", type=Path, required=True)
    build.add_argument("--workers", type=int, default=12)
    validate = subparsers.add_parser("validate", help="Validate a completed output")
    validate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        if args.workers < 1:
            raise ValueError("--workers must be positive")
        result = build_characterization(
            release_root=args.release_root,
            normalization_dir=args.normalization_dir,
            paper_evaluation_dir=args.paper_evaluation_dir,
            config_path=args.config,
            normalized_runtime_root=args.normalized_runtime_root,
            render_runtime_root=args.render_runtime_root,
            output_dir=args.output,
            measurement_cache=args.measurement_cache,
            workers=args.workers,
        )
    else:
        result = validate_output(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
