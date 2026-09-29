from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ecad_paper_figures.characterization import (
    build_characterization,
    category_area,
    category_components,
    category_layers,
    format_group,
    read_render_area,
    scan_kicad_characteristics,
    source_group,
)

SOURCE_CONFIG = {
    "named_prefixes": {"ti": "TI", "sparkfun": "SparkFun"},
    "community_prefixes": ["hackaday"],
    "oshwa_logical_root_id": "legacy_oshwa",
    "oshwa_fallback": "OSHWA registry — other",
    "community_fallback": "Non-OSHWA GitHub / community",
    "vendor_fallback": "Other vendor corpora",
    "unknown": "Unknown source",
}
FORMAT_CONFIG = {
    "detected_formats": {
        "allegro_or_binary_brd": "Allegro",
        "altium_pcbdoc": "Altium",
        "eagle_xml": "Eagle",
        "kicad_native": "KiCad",
    },
    "implementation_contains": {
        "allegro": "Allegro",
        "altium": "Altium",
        "eagle": "Eagle",
    },
    "unknown": "Unknown / other",
}


def test_source_group_preserves_named_and_fallback_categories() -> None:
    assert source_group("legacy_hw", "ti/design/board.PcbDoc", SOURCE_CONFIG) == "TI"
    assert source_group("legacy_oshwa", "sparkfun/repo/board.brd", SOURCE_CONFIG) == "SparkFun"
    assert (
        source_group("legacy_oshwa", "some-owner/repo/board.kicad_pcb", SOURCE_CONFIG)
        == "OSHWA registry — other"
    )
    assert (
        source_group("legacy_hw", "hackaday/ecad/board.brd", SOURCE_CONFIG)
        == "Non-OSHWA GitHub / community"
    )
    assert source_group("legacy_hw", "adi/design/board.PcbDoc", SOURCE_CONFIG) == "Other vendor corpora"
    assert source_group("legacy_hw", "../escape", SOURCE_CONFIG) == "Unknown source"


def test_format_group_prefers_recovery_implementation() -> None:
    assert format_group("allegro_or_binary_brd", "pcb-rnd-eagle-binary", FORMAT_CONFIG) == "Eagle"
    assert format_group("allegro_or_binary_brd", "parent", FORMAT_CONFIG) == "Allegro"
    assert format_group("kicad_native", "parent", FORMAT_CONFIG) == "KiCad"
    assert format_group("cadstar_cpa", "parent", FORMAT_CONFIG) == "Unknown / other"


def test_categories_keep_unknowns_explicit() -> None:
    assert category_layers(None) == "Unknown"
    assert category_layers(12) == ">8"
    assert category_components(None) == "Unknown"
    assert category_components(26) == "26–50"
    assert category_area(None) == "Unknown"
    assert category_area(2_500) == "2.5–5k"


def test_streaming_kicad_scan_extracts_layers_and_passive_sizes(tmp_path: Path) -> None:
    board = tmp_path / "board.kicad_pcb"
    board.write_text(
        """(kicad_pcb (version 20250101)
  (layers
    (0 \"F.Cu\" signal)
    (2 \"In1.Cu\" power)
    (31 \"B.Cu\" signal)
    (36 \"B.SilkS\" user \"b.silkscreen\")
  )
  (footprint \"Resistor_SMD:R_0402_1005Metric\"
    (layer \"F.Cu\")
    (property \"Reference\" \"R1\")
  )
  (footprint \"Capacitor_SMD:C_0603_1608Metric\"
    (layer \"F.Cu\")
    (fp_text reference \"C1\")
  )
  (footprint \"Connector:PinHeader_1x06_P2.54mm\"
    (layer \"F.Cu\")
    (property \"Reference\" \"J1\")
  )
)
""",
        encoding="utf-8",
    )
    result = scan_kicad_characteristics(board, ("0402", "0603", "0805"))
    assert result == {
        "normalized_parse_status": "ok",
        "copper_layer_count": 3,
        "passive_sizes": ["0402", "0603"],
    }


def test_render_area_requires_checksum_and_physical_units(tmp_path: Path) -> None:
    path = tmp_path / "render.json"
    content = json.dumps({"svg_width": "20.0mm", "svg_height": "12.5mm"}).encode()
    path.write_bytes(content)
    result = read_render_area(path, hashlib.sha256(content).hexdigest(), tmp_path)
    assert result == {"area_mm2": 250.0, "geometry_status": "ok"}
    mismatch = read_render_area(path, "0" * 64, tmp_path)
    assert mismatch["area_mm2"] is None
    assert mismatch["geometry_status"] == "render_metadata_checksum_mismatch"


def test_full_builder_is_validated_and_exact_noop(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    release = data_root / "releases" / "fixture-release"
    tables = release / "tables"
    tables.mkdir(parents=True)
    normalization = data_root / "runs" / "normalization_v2"
    normalization.mkdir(parents=True)
    board = data_root / "artifacts" / "board.kicad_pcb"
    board.parent.mkdir(parents=True)
    board.write_text(
        """(kicad_pcb (version 20250101)
  (layers (0 \"F.Cu\" signal) (31 \"B.Cu\" signal))
  (footprint \"Resistor_SMD:R_0402_1005Metric\"
    (property \"Reference\" \"R1\")
    (pad \"1\" smd rect (at 0 0) (size 1 1) (layers \"F.Cu\")))
)
""",
        encoding="utf-8",
    )
    meta = data_root / "runs" / "renders" / "render.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"svg_width": "20mm", "svg_height": "10mm"}))
    meta_sha = hashlib.sha256(meta.read_bytes()).hexdigest()
    raw_sha = "a" * 64
    object_id = "normalized_object_fixture"
    tables_by_name = {
        "normalized_objects": [
            {
                "normalized_object_id": object_id,
                "normalized_sha256": "b" * 64,
                "normalized_size_bytes": board.stat().st_size,
                "normalized_path_runtime": str(board),
            }
        ],
        "normalized_aliases": [
            {
                "alias_id": "alias_fixture",
                "normalized_object_id": object_id,
                "logical_root_id": "legacy_hw",
                "relative_path": "ti/design/board.brd",
                "raw_content_sha256": raw_sha,
                "normalization_implementation": "parent",
                "footprint_count": 1,
                "pad_count": 1,
            }
        ],
        "render_outcomes": [
            {
                "work_item_id": "render_fixture",
                "normalized_object_id": object_id,
                "side": "F",
                "status": "succeeded",
                "render_meta_path": str(meta),
                "render_meta_sha256": meta_sha,
            }
        ],
        "stage7_candidate_relations": [
            {
                "relation_id": "relation_fixture",
                "pair_id": "pair_fixture",
                "normalized_object_id": object_id,
                "side": "F",
                "photo_file_location_id": "photo_fixture",
                "predicted_route": "PRIMARY",
                "predicted_label": "single_populated_matchable",
                "loma_roma_corner_delta": 0.01,
            }
        ],
        "stage7_instance_clusters": [
            {"stage7_instance_cluster_id": "cluster_fixture", "relation_count": 1}
        ],
        "stage7_relation_cluster_crosswalk": [
            {
                "pair_id": "pair_fixture",
                "stage7_instance_cluster_id": "cluster_fixture",
                "source_format": "eagle",
            }
        ],
    }
    inventory = []
    for name, rows in tables_by_name.items():
        path = tables / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        inventory.append(
            {
                "artifact_id": name,
                "artifact_type": "canonical_table",
                "relative_path": f"tables/{name}.parquet",
                "rows": len(rows),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    manifest = {
        "release_id": "fixture-release",
        "release_status": "internal_frozen",
        "build_fingerprint": "fixture-build",
        "artifact_inventory": inventory,
    }
    (release / "release-manifest.json").write_text(json.dumps(manifest))
    attempts = normalization / "attempts.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{"content_sha256": raw_sha, "parent_detected_format": "eagle_xml"}]
        ),
        attempts,
    )
    attempts_sha = hashlib.sha256(attempts.read_bytes()).hexdigest()
    (normalization / "success.json").write_text(
        json.dumps(
            {
                "composition_fingerprint": "fixture-composition",
                "artifact_checksums": {"attempts.parquet": attempts_sha},
            }
        )
    )
    evaluation = data_root / "runs" / "paper_evaluation"
    evaluation.mkdir(parents=True)
    policy = evaluation / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "policy_id": "primary-single-populated-delta002-singleton/v1",
                "policy": {
                    "maximum_loma_roma_corner_delta": 0.02,
                    "predicted_label": "single_populated_matchable",
                    "spatial_cluster_relation_count": 1,
                    "window_status": "primary_accepted",
                },
            }
        )
    )
    (evaluation / "success.json").write_text(
        json.dumps(
            {
                "request_fingerprint": "fixture-evaluation",
                "outputs": {"policy.json": hashlib.sha256(policy.read_bytes()).hexdigest()},
            }
        )
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "source_groups": {
                    **SOURCE_CONFIG,
                    "multiple": "Multiple source groups",
                },
                "format_groups": {
                    **FORMAT_CONFIG,
                    "multiple": "Multiple formats",
                },
                "accepted_relation_policy": {
                    "policy_id": "primary-single-populated-delta002-singleton/v1",
                    "predicted_route": "PRIMARY",
                    "expected_counts": {
                        "relations": 1,
                        "spatial_clusters": 1,
                        "photo_locations": 1,
                        "normalized_objects": 1,
                    },
                },
                "categories": {"passive_package_sizes": ["0402", "0603"]},
            }
        )
    )
    output = data_root / "runs" / "paper_figures" / "fixture"
    kwargs = {
        "release_root": release,
        "normalization_dir": normalization,
        "paper_evaluation_dir": evaluation,
        "config_path": config,
        "normalized_runtime_root": data_root,
        "render_runtime_root": data_root,
        "output_dir": output,
        "measurement_cache": data_root / "runs" / "paper_figures" / "cache.jsonl",
        "workers": 2,
    }
    first = build_characterization(**kwargs)
    second = build_characterization(**kwargs)
    assert first == second
    assert first["validation_status"] == "passed"
    assert first["normalized_objects"] == 1
    assert first["accepted_relations"] == 1
    assert (output / "corpus_coverage.pdf").read_bytes().startswith(b"%PDF")
