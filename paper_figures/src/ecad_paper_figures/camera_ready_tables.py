"""Camera-ready tables: sources (Appendix A), representativeness, and Fig. 5 outliers.

All inputs are SHA-256-bound in a config JSON; each subcommand writes one fresh
``paper-cr-*`` namespace with ``success.json``.

    python -m ecad_paper_figures.camera_ready_tables sources \
        --config configs/camera_ready_tables_v2.json \
        --output $ECAD_RUNS/paper-cr-sources-v1
    python -m ecad_paper_figures.camera_ready_tables representativeness ... paper-cr-representativeness-v1
    python -m ecad_paper_figures.camera_ready_tables area-outliers ... paper-cr-area-outliers-v1

For v3, point the config at the v3 accepted-mapping table and receipt.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .cr_common import (
    code_revision,
    promote,
    tex_number,
    verify_inputs,
    write_json,
    write_macros,
)

VERSION = "paper-cr-tables/v4"

# Source-entity types for Appendix A. Keys are native_source_id values of sources.parquet.
SOURCE_TYPES = {
    "adi": ("Analog Devices", "Semiconductor vendor"),
    "ti": ("Texas Instruments", "Semiconductor vendor"),
    "renesas": ("Renesas", "Semiconductor vendor"),
    "mps": ("Monolithic Power Systems", "Semiconductor vendor"),
    "nxp": ("NXP", "Semiconductor vendor"),
    "microchip": ("Microchip", "Semiconductor vendor"),
    "st": ("STMicroelectronics", "Semiconductor vendor"),
    "silabs": ("Silicon Labs", "Semiconductor vendor"),
    "semtech": ("Semtech", "Semiconductor vendor"),
    "espressif": ("Espressif", "Semiconductor vendor"),
    "nordic": ("Nordic Semiconductor", "Semiconductor vendor"),
    "raspberrypi": ("Raspberry Pi", "Semiconductor vendor"),
    "ublox": ("u-blox", "Semiconductor vendor"),
    "seeed": ("Seeed Studio", "Open-hardware maker"),
    "arduino": ("Arduino", "Open-hardware maker"),
    "buspirate": ("Bus Pirate", "Open-hardware maker"),
    "pixhawk": ("Pixhawk", "Open-hardware maker"),
    "misc_github": ("Curated GitHub projects", "Open-hardware maker"),
    "oshwhub": ("OSHWHub", "Community"),
    "hackaday": ("Hackaday.io", "Community"),
    "hackclub": ("Hack Club OnBoard", "Community"),
    "oshwlab": ("OSHWLab", "Community"),
    "ohwr": ("Open Hardware Repository", "Community"),
    "oshwa_registry": ("OSHWA certification registry", "Registry"),
    "github_repositories": ("GitHub repositories", "GitHub collection"),
}
TYPE_ORDER = ["Semiconductor vendor", "Open-hardware maker", "Community", "Registry", "GitHub collection"]

# Coverage-figure source groups (path-prefix based, as in Figure 2) mapped to the
# composition used in the text.
GROUP_COMPOSITION = {
    "TI": "vendor",
    "MPS": "vendor",
    "Microchip": "vendor",
    "Other vendor corpora": "vendor",
    "SparkFun": "commercial_maker",
    "Adafruit": "commercial_maker",
    "Seeed": "commercial_maker",
    "OSHWA registry — other": "other_github",
    "Non-OSHWA GitHub / community": "community",
    "Multiple source groups": "multiple",
}


def _table(inputs: dict[str, dict[str, str]], name: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
    return pq.read_table(inputs[name]["path"], columns=columns).to_pylist()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value.replace("'", '"')) if value.startswith("[") else [value]
    return list(value)


def _record_sources(inputs: dict[str, dict[str, str]]) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]]]:
    sources = {row["source_id"]: row["native_source_id"] for row in _table(inputs, "sources")}
    records = {
        row["source_record_id"]: sources[row["source_id"]]
        for row in _table(inputs, "source_records", ["source_record_id", "source_id"])
    }
    oshwa_linked: dict[str, set[str]] = defaultdict(set)
    for row in _table(inputs, "source_record_relations"):
        if row["relation_type"] == "associated_oshwa_project":
            oshwa_linked[row["from_source_record_id"]].add(row["to_source_record_id"])
    return sources, records, oshwa_linked


def _attribution(record_ids: list[str], records: dict[str, str]) -> str:
    natives = sorted({records[r] for r in record_ids if r in records})
    if not natives:
        return "__none__"
    if len(natives) > 1:
        return "__multiple__"
    return natives[0]


def build_sources(inputs: dict[str, dict[str, str]], expected: dict[str, int]) -> dict[str, Any]:
    sources, records, oshwa_linked = _record_sources(inputs)
    record_counts = Counter(records.values())
    objects = _table(inputs, "objects", ["normalized_object_id", "source_record_ids"])
    mappings = _table(inputs, "accepted_mappings", ["relation_id", "normalized_object_id", "source_record_ids"])
    object_attr = {o["normalized_object_id"]: _attribution(_as_list(o["source_record_ids"]), records) for o in objects}
    object_oshwa = {
        o["normalized_object_id"]: any(r in oshwa_linked for r in _as_list(o["source_record_ids"])) for o in objects
    }
    obj_counts = Counter(object_attr.values())
    map_counts = Counter(object_attr[m["normalized_object_id"]] for m in mappings)
    rows = []
    for native, (label, kind) in SOURCE_TYPES.items():
        if native not in record_counts:
            raise ValueError(f"source {native} missing from sources.parquet")
        rows.append({
            "native_source_id": native, "label": label, "type": kind,
            "source_records": record_counts[native],
            "normalized_objects": obj_counts.get(native, 0),
            "accepted_mappings": map_counts.get(native, 0),
        })
    rows.sort(key=lambda r: (TYPE_ORDER.index(r["type"]), -r["normalized_objects"], -r["source_records"]))
    extra = [
        {"label": "Records from two sources", "normalized_objects": obj_counts.get("__multiple__", 0),
         "accepted_mappings": map_counts.get("__multiple__", 0)},
        {"label": "No source record", "normalized_objects": obj_counts.get("__none__", 0),
         "accepted_mappings": map_counts.get("__none__", 0)},
    ]
    totals = {
        "sources": len(rows),
        "source_records": sum(r["source_records"] for r in rows),
        "normalized_objects": sum(r["normalized_objects"] for r in rows) + sum(r["normalized_objects"] for r in extra),
        "accepted_mappings": sum(r["accepted_mappings"] for r in rows) + sum(r["accepted_mappings"] for r in extra),
    }
    for key, value in expected.items():
        if totals[key] != value:
            raise ValueError(f"sources table total {key}={totals[key]} != expected {value}")
    oshwa = {
        "normalized_objects_via_github": sum(object_oshwa.values()),
        "accepted_mappings_via_github": sum(object_oshwa[m["normalized_object_id"]] for m in mappings),
    }
    return {"rows": rows, "extra_rows": extra, "totals": totals, "oshwa_linked": oshwa}


SHORT_TYPE = {
    "Semiconductor vendor": "Vendor",
    "Open-hardware maker": "Maker",
    "Community": "Community",
    "Registry": "Registry",
    "GitHub collection": "GitHub",
}


def _sources_tex(result: dict[str, Any]) -> str:
    lines = []
    current = None
    for row in result["rows"]:
        kind = SHORT_TYPE[row["type"]] if row["type"] != current else ""
        current = row["type"]
        objects = tex_number(row["normalized_objects"]) if row["normalized_objects"] else "--"
        maps = tex_number(row["accepted_mappings"]) if row["accepted_mappings"] else "--"
        if row["native_source_id"] == "oshwa_registry":
            o = result["oshwa_linked"]
            objects = f"({tex_number(o['normalized_objects_via_github'])})\\textsuperscript{{b}}"
            maps = f"({tex_number(o['accepted_mappings_via_github'])})\\textsuperscript{{b}}"
        lines.append(f"{kind} & {row['label']} & {tex_number(row['source_records'])} & {objects} & {maps} \\\\")
    lines.append("\\midrule")
    for row in result["extra_rows"]:
        lines.append(f" & {row['label']}\\textsuperscript{{c}} & -- & {tex_number(row['normalized_objects'])} & {tex_number(row['accepted_mappings']) if row['accepted_mappings'] else '--'} \\\\")
    t = result["totals"]
    lines.append("\\midrule")
    lines.append(f"\\multicolumn{{2}}{{@{{}}l}}{{Total ({t['sources']} sources)}} & {tex_number(t['source_records'])} & {tex_number(t['normalized_objects'])} & {tex_number(t['accepted_mappings'])} \\\\")
    return "\n".join(lines) + "\n"


def _category_shares(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(r[key]) for r in rows))


def build_representativeness(inputs: dict[str, dict[str, str]]) -> dict[str, Any]:
    design = _table(inputs, "design_metrics")
    mappings = _table(inputs, "accepted_mappings", ["normalized_object_id", "source_group", "source_record_ids", "logical_root_id", "relative_path"])
    subjects = _table(inputs, "subject_outcomes")
    plan = _table(inputs, "candidate_plan_subjects", ["normalized_subject_id", "normalized_object_id", "route_primary", "route_fallback", "route_deferred", "candidate_count"])
    mapped = {m["normalized_object_id"] for m in mappings}
    rows = [dict(r, mapped=r["normalized_object_id"] in mapped) for r in design]
    comparison = {}
    for key in ("source_format", "source_group", "copper_layer_category", "component_category", "area_category"):
        comparison[key] = {
            "mapped": _category_shares([r for r in rows if r["mapped"]], key),
            "unmapped": _category_shares([r for r in rows if not r["mapped"]], key),
        }

    def med(values: list[float]) -> float | None:
        clean = [v for v in values if v is not None]
        return statistics.median(clean) if clean else None

    medians = {}
    for key in ("footprint_count", "pad_count", "area_mm2", "copper_layer_count"):
        medians[key] = {
            "mapped": med([r[key] for r in rows if r["mapped"]]),
            "unmapped": med([r[key] for r in rows if not r["mapped"]]),
        }

    # Why objects are unmapped: the furthest stage either side reached.
    plan_by_subject = {p["normalized_subject_id"]: p for p in plan}
    stage7 = {r["normalized_object_id"] for r in _table(inputs, "stage7_candidates", ["normalized_object_id"])}
    by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in subjects:
        by_object[s["normalized_object_id"]].append(s)
    order = [
        "mapped", "excluded_at_stage7", "registered_fallback_only", "attempted_no_consensus",
        "only_deferred_photos", "no_candidate_photo", "no_render",
    ]
    outcome = {}
    for obj, sides in by_object.items():
        if obj in mapped:
            outcome[obj] = "mapped"
            continue
        states = set()
        for s in sides:
            p = plan_by_subject[s["normalized_subject_id"]]
            if s["window_status"] == "render_unavailable":
                states.add("no_render")
            elif s["selection_status"] == "not_attempted":
                states.add("only_deferred_photos" if int(p["route_deferred"]) > 0 else "no_candidate_photo")
            elif str(s["accepted_primary"]) == "True":
                states.add("excluded_at_stage7")
            elif str(s["accepted_registration_only"]) == "True":
                states.add("registered_fallback_only")
            else:
                states.add("attempted_no_consensus")
        outcome[obj] = min(states, key=order.index)
    for obj, state in outcome.items():
        if state == "excluded_at_stage7" and obj not in stage7:
            raise ValueError(f"{obj} has an accepted primary side but no Stage 7 candidate")
    decomposition = Counter(outcome.values())
    if len(outcome) != len(design):
        raise ValueError("outcome coverage does not match design metrics")

    composition = Counter(GROUP_COMPOSITION[m["source_group"]] for m in mappings)
    # Repository owner of the photograph for the "other GitHub" group (first path part
    # under the OSHWA/GitHub root), to describe who publishes these boards.
    other_owner = Counter(
        m["relative_path"].split("/")[0]
        for m in mappings
        if m["source_group"] == "OSHWA registry — other" and m["logical_root_id"] == "legacy_oshwa"
    )
    return {
        "objects": len(design),
        "mapped_objects": sum(r["mapped"] for r in rows),
        "comparison": comparison,
        "medians": medians,
        "unmapped_decomposition": {k: decomposition.get(k, 0) for k in order},
        "mapping_composition_by_coverage_group": dict(composition),
        "other_github_photo_owner_top": other_owner.most_common(15),
        "other_github_mappings": sum(other_owner.values()),
        "mappings": len(mappings),
    }


def _share(counts: dict[str, int], keys: list[str]) -> float:
    total = sum(counts.values())
    return 100 * sum(counts.get(k, 0) for k in keys) / total


def representativeness_macros(result: dict[str, Any]) -> dict[str, str]:
    """Mapped-vs-unmapped object shares and the unmapped decomposition as paper macros."""
    groups = {
        "Vendor": ["TI", "MPS", "Microchip", "Other vendor corpora"],
        "Maker": ["SparkFun", "Adafruit", "Seeed"],
        "OtherGithub": ["OSHWA registry — other"],
        "Community": ["Non-OSHWA GitHub / community"],
    }
    rows = {
        "FmtAltium": ("source_format", ["Altium"]),
        "FmtEagle": ("source_format", ["Eagle"]),
        "FmtKicad": ("source_format", ["KiCad"]),
        "FmtAllegro": ("source_format", ["Allegro"]),
        **{f"Src{k}": ("source_group", v) for k, v in groups.items()},
        "LayersTwo": ("copper_layer_category", ["2"]),
        "LayersFour": ("copper_layer_category", ["4"]),
        "LayersSixPlus": ("copper_layer_category", ["6", "8", ">8"]),
        "PartsTen": ("component_category", ["1–10"]),
        "PartsHundred": ("component_category", ["11–25", "26–50", "51–100"]),
        "PartsMore": ("component_category", ["101–200", "201–400", ">400"]),
    }
    macros: dict[str, str] = {}
    for name, (key, cats) in rows.items():
        for which in ("mapped", "unmapped"):
            macros[f"pRep{name}{which.capitalize()}"] = f"{_share(result['comparison'][key][which], cats):.1f}\\%"
    for which in ("mapped", "unmapped"):
        macros[f"fRepMedianAreaMm{which.capitalize()}"] = f"{result['medians']['area_mm2'][which]:,.0f}"
        macros[f"fRepMedianComponents{which.capitalize()}"] = f"{result['medians']['footprint_count'][which]:.0f}"
    names = {
        "excluded_at_stage7": "nUnmappedStageSeven",
        "registered_fallback_only": "nUnmappedFallbackOnly",
        "attempted_no_consensus": "nUnmappedNoConsensus",
        "only_deferred_photos": "nUnmappedDeferredOnly",
        "no_candidate_photo": "nUnmappedNoCandidate",
        "no_render": "nUnmappedNoRender",
    }
    decomposition = result["unmapped_decomposition"]
    for key, name in names.items():
        macros[name] = f"{decomposition[key]:,}"
    top6 = sum(count for _, count in result["other_github_photo_owner_top"][:6])
    macros["nOtherGithubTopSixMappings"] = f"{top6:,}"
    macros["nMappedObjects"] = f"{result['mapped_objects']:,}"
    macros["nUnmappedObjects"] = f"{result['objects'] - result['mapped_objects']:,}"
    comp = result["mapping_composition_by_coverage_group"]
    total = result["mappings"]
    for key, name in (("vendor", "Vendor"), ("commercial_maker", "Maker"), ("other_github", "OtherGithub"), ("community", "Community"), ("multiple", "Multiple")):
        macros[f"nMapComp{name}"] = f"{comp.get(key, 0):,}"
        macros[f"pMapComp{name}"] = f"{100 * comp.get(key, 0) / total:.1f}\\%"
    return macros


def build_area_outliers(inputs: dict[str, dict[str, str]], small_mm2: float, large_low: float, large_high: float) -> dict[str, Any]:
    design = _table(inputs, "design_metrics")
    objects = {o["normalized_object_id"]: o for o in _table(inputs, "objects", ["normalized_object_id", "artifact_relative_path", "normalized_input_suffix", "outline_status", "region_status", "registration_render_policy", "source_record_ids"])}
    sources, records, _ = _record_sources(inputs)
    mapped = Counter(m["normalized_object_id"] for m in _table(inputs, "accepted_mappings", ["normalized_object_id"]))

    def describe(r: dict[str, Any]) -> dict[str, Any]:
        o = objects[r["normalized_object_id"]]
        return {
            "normalized_object_id": r["normalized_object_id"],
            "area_mm2": r["area_mm2"], "footprint_count": r["footprint_count"],
            "copper_layer_count": r["copper_layer_count"], "source_format": r["source_format"],
            "source_group": r["source_group"], "source": _attribution(_as_list(o["source_record_ids"]), records),
            "outline_status": o["outline_status"], "region_status": o["region_status"],
            "render_policy": o["registration_render_policy"], "input_suffix": o["normalized_input_suffix"],
            "accepted_mappings": mapped.get(r["normalized_object_id"], 0),
        }

    valid = [r for r in design if r["area_mm2"] is not None and r["footprint_count"] is not None]
    small = sorted((describe(r) for r in valid if r["area_mm2"] < small_mm2), key=lambda d: d["area_mm2"])
    large = sorted((describe(r) for r in valid if large_low <= r["area_mm2"] <= large_high), key=lambda d: d["area_mm2"])
    return {
        "valid_measurements": len(valid),
        "small_threshold_mm2": small_mm2, "small": small,
        "large_window_mm2": [large_low, large_high], "large": large,
        "small_summary": {k: dict(Counter(str(d[k]) for d in small)) for k in ("source", "source_format", "outline_status", "render_policy")},
        "large_summary": {k: dict(Counter(str(d[k]) for d in large)) for k in ("source", "source_format", "outline_status", "render_policy")},
    }


_MM = __import__("re").compile(r"([0-9.]+)mm")


def build_area_audit(inputs: dict[str, dict[str, str]], meta_dirs: list[str], factor: float, broken_mm2: float) -> dict[str, Any]:
    """Compare each object's outline-layer extent with its rendered-board extent.

    ``area_mm2`` in design_metrics is the bounding box of the exported Edge.Cuts, mask and
    silkscreen SVG. The v2 registration render crops that SVG to the board
    (``content_crop_xywh``); its extent is the rendered-board extent. When the two differ
    by more than ``factor`` in either direction (drawing text or frames outside the board,
    or an outline covering only part of it) the rendered-board extent is plotted. Objects
    still below ``broken_mm2`` are flagged as broken outlines.
    """
    import hashlib
    import os

    design = [r for r in _table(inputs, "design_metrics") if r["area_mm2"] is not None and r["footprint_count"] is not None]
    renders: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _table(inputs, "render_bank_renders", ["normalized_object_id", "side", "status", "render_meta_sha256"]):
        if r["status"] == "succeeded":
            renders[r["normalized_object_id"]].append(r)
    available: dict[str, str] = {}
    for root in meta_dirs:
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name.endswith(".json"):
                    available.setdefault(name[:-5], os.path.join(dirpath, name))
    rows = []
    for d in design:
        sides = sorted(renders.get(d["normalized_object_id"], []), key=lambda s: s["side"] != "F")
        meta = None
        for side in sides:
            path = available.get(side["render_meta_sha256"])
            if path:
                content = Path(path).read_bytes()
                if hashlib.sha256(content).hexdigest() != side["render_meta_sha256"]:
                    raise ValueError(f"render metadata checksum mismatch: {path}")
                meta = json.loads(content)
                break
        if meta is None:
            raise ValueError(f"no render metadata for {d['normalized_object_id']}")
        width = float(_MM.match(meta["svg_width"]).group(1))
        height = float(_MM.match(meta["svg_height"]).group(1))
        raster = meta["source_raster_size"]
        raster = json.loads(raster) if isinstance(raster, str) else raster
        crop = meta["content_crop_xywh"]
        crop = json.loads(crop) if isinstance(crop, str) else crop
        scale = max(width, height) / max(raster)
        fit = (crop[2] * scale) * (crop[3] * scale)
        outline = float(d["area_mm2"])
        ratio = outline / fit if fit > 0 else float("inf")
        corrected = fit > 0 and (ratio > factor or ratio < 1 / factor)
        plotted = fit if corrected else outline
        rows.append({
            "normalized_object_id": d["normalized_object_id"],
            "outline_extent_mm2": outline,
            "rendered_board_extent_mm2": fit,
            "extent_ratio": ratio,
            "plotted_area_mm2": plotted,
            "area_rule": "broken_outline" if plotted < broken_mm2 else ("rendered_board_extent" if corrected else "outline_extent"),
            "footprint_count": d["footprint_count"],
            "source_format": d["source_format"],
        })
    counts = Counter(r["area_rule"] for r in rows)
    return {
        "rows": rows,
        "counts": dict(counts),
        "corrected_inflated": sum(1 for r in rows if r["area_rule"] != "outline_extent" and r["extent_ratio"] > factor),
        "corrected_shrunken": sum(1 for r in rows if r["area_rule"] != "outline_extent" and r["extent_ratio"] < 1 / factor),
        "outline_at_least_5e4": sum(1 for r in rows if r["outline_extent_mm2"] >= 5e4),
        "plotted_at_least_5e4": sum(1 for r in rows if r["plotted_area_mm2"] >= 5e4),
        "factor": factor,
        "broken_mm2": broken_mm2,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["sources", "representativeness", "area-outliers", "area-audit"])
    parser.add_argument("--meta-dir", action="append", default=[])
    parser.add_argument("--factor", type=float, default=4.0)
    parser.add_argument("--broken-mm2", type=float, default=10.0)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--small-mm2", type=float, default=10.0)
    parser.add_argument("--large-low-mm2", type=float, default=5e4)
    parser.add_argument("--large-high-mm2", type=float, default=2e5)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    needed = {
        "sources": ["sources", "source_records", "source_record_relations", "objects", "accepted_mappings"],
        "representativeness": ["design_metrics", "accepted_mappings", "subject_outcomes", "candidate_plan_subjects", "stage7_candidates"],
        "area-outliers": ["design_metrics", "objects", "accepted_mappings", "sources", "source_records", "source_record_relations"],
        "area-audit": ["design_metrics", "render_bank_renders"],
    }[args.command]
    inputs = {k: config["inputs"][k] for k in needed}
    verified = verify_inputs(inputs)
    params = {}
    if args.command == "area-outliers":
        params = {"small_mm2": args.small_mm2, "large_low_mm2": args.large_low_mm2, "large_high_mm2": args.large_high_mm2}
    if args.command == "area-audit":
        params = {"meta_dirs": args.meta_dir, "factor": args.factor, "broken_mm2": args.broken_mm2}
    request = {"schema_version": f"{VERSION}/{args.command}", "config_name": config["name"], "inputs": verified, "params": params}

    def build(tmp: Path) -> dict[str, Any]:
        if args.command == "sources":
            result = build_sources(inputs, config["expected_totals"])
            write_json(tmp / "sources_table.json", result)
            (tmp / "sources_table_rows.tex").write_text(_sources_tex(result), encoding="utf-8")
            t = result["totals"]
            write_macros(tmp / "numbers_sources.tex", {
                "nOshwaLinkedObjects": tex_number(result["oshwa_linked"]["normalized_objects_via_github"]),
                "nOshwaLinkedMappings": tex_number(result["oshwa_linked"]["accepted_mappings_via_github"]),
                "nObjectsTwoSources": tex_number(result["extra_rows"][0]["normalized_objects"]),
                "nObjectsNoSourceRecord": tex_number(result["extra_rows"][1]["normalized_objects"]),
            }, f"Generated by {VERSION}/sources; totals {t}.")
        elif args.command == "representativeness":
            result = build_representativeness(inputs)
            write_json(tmp / "representativeness.json", result)
            write_macros(tmp / "numbers_representativeness.tex", representativeness_macros(result),
                         f"Generated by {VERSION}/representativeness; shares are % of objects.")
        elif args.command == "area-audit":
            result = build_area_audit(inputs, args.meta_dir, args.factor, args.broken_mm2)
            import pyarrow as pa
            pq.write_table(pa.Table.from_pylist(result.pop("rows")), tmp / "area_audit.parquet", compression="zstd")
            write_json(tmp / "area_audit_summary.json", result)
            c = result["counts"]
            write_macros(tmp / "numbers_area_audit.tex", {
                "nAreaCorrected": f"{result['corrected_inflated'] + result['corrected_shrunken']:,}",
                "nAreaInflated": f"{result['corrected_inflated']:,}",
                "nAreaShrunken": f"{result['corrected_shrunken']:,}",
                "nAreaBrokenOutline": f"{c.get('broken_outline', 0):,}",
            }, f"Generated by {VERSION}/area-audit; factor {args.factor}, broken below {args.broken_mm2} mm2.")
        else:
            result = build_area_outliers(inputs, args.small_mm2, args.large_low_mm2, args.large_high_mm2)
            write_json(tmp / "area_outliers.json", result)
        return {"code_revision": code_revision(Path(__file__).parent), "schema_version": request["schema_version"]}

    print(json.dumps(promote(args.output, request, build), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
