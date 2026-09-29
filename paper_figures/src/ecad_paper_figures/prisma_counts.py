"""PRISMA-style construction-flow counts for the paper's Figure 2 (camera-ready).

Reads only frozen stage summaries (each bound by SHA-256 in the config), checks that every
transition closes arithmetically, and writes ``prisma_counts.json`` plus a
``numbers_prisma.tex`` fragment in the paper's numbers.tex macro style.

    uv run --project experiments/paper_figures --frozen python -m ecad_paper_figures.prisma_counts \
        --config experiments/paper_figures/configs/camera_ready_prisma_v2.json \
        --output $ECAD_RUNS/paper-cr-prisma-v1

A v3 rerun needs only a new config naming the v3 receipts and a new output namespace.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .cr_common import code_revision, promote, tex_number, verify_inputs, write_json, write_macros

VERSION = "paper-cr-prisma-counts/v1"

# normalization_v2 effective reasons that end without a normalized object; the others are
# validated (13,064) or materialized with automatic repairs (960).
REJECT_REASONS = {
    "unsupported_detected_format": "unsupported",
    "kicad_import_failed": "import_failed",
    "empty_or_placeholder_import": "empty",
    "edge_export_failed": "export_failed",
    "edge_export_invalid_svg": "export_failed",
    "empty_or_unexportable_legacy_kicad": "export_failed",
}


def _load(inputs: dict[str, dict[str, str]], name: str) -> dict[str, Any]:
    return json.loads(Path(inputs[name]["path"]).read_text())


def _eq(label: str, left: int, right: int) -> None:
    if left != right:
        raise ValueError(f"closure failed: {label}: {left} != {right}")


def compute(inputs: dict[str, dict[str, str]], expected_catalog_candidates: int) -> dict[str, Any]:
    completion = _load(inputs, "normalization_baseline_completion")
    norm = _load(inputs, "normalization_v2_summary")
    snapshot = _load(inputs, "subject_snapshot_summary")
    render = _load(inputs, "render_bank_summary")
    plan = _load(inputs, "photo_candidate_plan_summary")
    pairs = _load(inputs, "pair_manifest_summary")
    census = _load(inputs, "match_census_summary")
    downstream = _load(inputs, "downstream_analysis_summary")
    freeze = _load(inputs, "stage7_freeze_plan_summary")
    accepted = _load(inputs, "accepted_mappings_summary")

    c: dict[str, int] = {}
    # CAD inputs.
    c["cad_locations_processed"] = completion["scope"]["unique_locations"]
    c["cad_deferred_formats"] = completion["scope"]["deferred_excluded"]
    c["cad_candidates_discovered"] = c["cad_locations_processed"] + c["cad_deferred_formats"]
    _eq("catalog board candidates", c["cad_candidates_discovered"], expected_catalog_candidates)
    c["cad_duplicate_locations"] = completion["hashing"]["duplicate_locations_avoided"]
    c["exact_cad_contents"] = completion["hashing"]["unique_contents"]
    _eq("dedup", c["cad_locations_processed"] - c["cad_duplicate_locations"], c["exact_cad_contents"])
    _eq("normalization parent rows", norm["parent_rows"], c["exact_cad_contents"])

    # Normalization.
    reasons = norm["effective_reason_counts"]
    buckets = {"unsupported": 0, "import_failed": 0, "empty": 0, "export_failed": 0}
    for reason, bucket in REJECT_REASONS.items():
        buckets[bucket] += reasons.get(reason, 0)
    c.update({f"norm_reject_{k}": v for k, v in buckets.items()})
    c["norm_rejected"] = norm["effective_counts"]["rejected"]
    _eq("reject reasons", sum(buckets.values()), c["norm_rejected"])
    c["boards_normalized"] = norm["effective_materializations"]
    _eq("normalization", c["exact_cad_contents"] - c["norm_rejected"], c["boards_normalized"])
    c["norm_validated"] = norm["effective_counts"]["validated"]
    c["norm_repaired"] = norm["effective_counts"]["materialized"]
    _eq("objects", c["norm_validated"] + c["norm_repaired"], c["boards_normalized"])

    # Sides and renders.
    c["board_sides"] = snapshot["side_subjects"]
    _eq("two sides per object", 2 * snapshot["normalized_objects"], c["board_sides"])
    c["sides_without_registration_render"] = 2 * snapshot["render_policy_counts"]["no_registration_render"]
    c["render_eligible_sides"] = render["planned"]
    _eq("render eligible", c["board_sides"] - c["sides_without_registration_render"], c["render_eligible_sides"])
    c["render_failures"] = render["failed"]
    c["render_timeouts"] = render["failure_reason_counts"].get("render_TimeoutExpired", 0)
    c["sides_rendered"] = render["succeeded"]
    _eq("render", c["render_eligible_sides"] - c["render_failures"], c["sides_rendered"])

    # Candidate graph.
    c["candidate_edges"] = plan["candidate_rows"]
    for route in ("PRIMARY", "FALLBACK", "DEFERRED"):
        c[f"edges_{route.lower()}"] = plan["route_counts"][route]
    _eq("routes", sum(plan["route_counts"].values()), c["candidate_edges"])
    c["candidate_photos"] = plan["unique_candidate_photos"]
    c["executable_pairs"] = pairs["pairs"]
    universe = census["universe"]
    c["pairs_without_render"] = universe["plan_rows_not_executable_no_render"]
    _eq("executable", c["candidate_edges"] - c["pairs_without_render"], c["executable_pairs"])
    c["deferred_pairs_not_scheduled"] = pairs["pairs_by_route"]["DEFERRED"]
    c["attempted_pairs"] = universe["attempted_pairs"]
    _eq("no deferred attempts", universe["attempted_routes"].get("DEFERRED", 0), 0)
    c["pairs_not_reached"] = universe["unattempted_frozen_route_pairs"]
    _eq(
        "attempted",
        c["executable_pairs"] - c["deferred_pairs_not_scheduled"] - c["pairs_not_reached"],
        c["attempted_pairs"],
    )

    # Side outcomes.
    status = downstream["selection_status_counts"]
    window = downstream["window_status_counts"]
    c["sides_registered"] = status["accepted"]
    c["sides_uncertain"] = status["uncertain"]
    c["sides_no_plausible_match"] = status["no_plausible_match"]
    c["sides_not_attempted"] = status["not_attempted"]
    _eq("side outcomes", sum(status.values()), c["board_sides"])
    c["sides_no_candidates"] = window["no_candidates"]
    c["sides_render_unavailable"] = window["render_unavailable"]
    _eq("not attempted", c["sides_no_candidates"] + c["sides_render_unavailable"], c["sides_not_attempted"])
    _eq("render unavailable", c["sides_render_unavailable"], c["sides_without_registration_render"] + c["render_failures"])
    c["sides_attempted"] = c["board_sides"] - c["sides_not_attempted"]
    c["sides_stopped_on_primary"] = window["primary_accepted"]
    c["sides_queue_exhausted"] = window["queue_exhausted"]
    c["sides_window_cap"] = window["exhausted_at_batch_cap"]

    # Stage 7.
    c["stage7_candidates"] = downstream["stage7_primary_candidates"]
    _eq("freeze plan candidates", freeze["stage7_candidate_relations"], c["stage7_candidates"])
    c["nonprimary_registrations"] = c["sides_registered"] - c["stage7_candidates"]
    c["photograph_regions"] = freeze["candidate_instance_clusters"]
    funnel = census["stage7_policy_funnel_of_frozen_primary_selections"]
    c["stage7_fails_delta"] = funnel["fails_delta_002"]
    c["stage7_not_singleton"] = funnel["fails_singleton_cluster"]
    c["multiboard_reviewed"] = funnel["multiboard_review_cohort"]
    c["automatic_policy"] = funnel["base_policy_member"]
    _eq("stage7 funnel", sum(funnel.values()), c["stage7_candidates"])
    counts = accepted["counts"]
    _eq("base policy", counts["base_policy_mappings"], c["automatic_policy"])
    c["multiboard_accepted"] = counts["direct_review_promotions"]
    c["multiboard_rejected"] = counts["reviewed_nonpromoted_mappings"]
    _eq("multiboard review", c["multiboard_accepted"] + c["multiboard_rejected"], c["multiboard_reviewed"])
    c["mappings_accepted"] = counts["accepted_mappings"]
    _eq("accepted", c["automatic_policy"] + c["multiboard_accepted"], c["mappings_accepted"])
    c["stage7_excluded"] = c["stage7_fails_delta"] + c["stage7_not_singleton"] + c["multiboard_rejected"]
    _eq("stage7 exclusions", c["stage7_candidates"] - c["stage7_excluded"], c["mappings_accepted"])
    return c


MACROS = {
    "nCadCandidatesDiscovered": "cad_candidates_discovered",
    "nCadDeferredFormats": "cad_deferred_formats",
    "nCadLocationsProcessed": "cad_locations_processed",
    "nCadDuplicateLocations": "cad_duplicate_locations",
    "nExactCadContents": "exact_cad_contents",
    "nNormalizationRejected": "norm_rejected",
    "nNormRejectUnsupported": "norm_reject_unsupported",
    "nNormRejectImportFailed": "norm_reject_import_failed",
    "nNormRejectEmpty": "norm_reject_empty",
    "nNormRejectExport": "norm_reject_export_failed",
    "nNormValidated": "norm_validated",
    "nNormRepaired": "norm_repaired",
    "nBoardsNormalized": "boards_normalized",
    "nNormalizedBoardSides": "board_sides",
    "nSidesWithoutRegistrationRender": "sides_without_registration_render",
    "nRenderEligibleSides": "render_eligible_sides",
    "nRenderFailures": "render_failures",
    "nRenderTimeouts": "render_timeouts",
    "nBoardSidesRendered": "sides_rendered",
    "nPhotoCandidateRelations": "candidate_edges",
    "nCandidateEdgesPrimary": "edges_primary",
    "nCandidateEdgesFallback": "edges_fallback",
    "nCandidateEdgesDeferred": "edges_deferred",
    "nCandidatePhotoLocations": "candidate_photos",
    "nPairsWithoutRender": "pairs_without_render",
    "nCandidatePairsPlanned": "executable_pairs",
    "nDeferredPairsNotScheduled": "deferred_pairs_not_scheduled",
    "nPairsNotReached": "pairs_not_reached",
    "nMatcherPairsAttempted": "attempted_pairs",
    "nSidesAttempted": "sides_attempted",
    "nSidesUncertain": "sides_uncertain",
    "nSidesNoPlausibleMatch": "sides_no_plausible_match",
    "nSidesNotAttempted": "sides_not_attempted",
    "nSidesNoCandidates": "sides_no_candidates",
    "nSidesRenderUnavailable": "sides_render_unavailable",
    "nAcceptedRegistrationRelations": "sides_registered",
    "nNonPrimaryRegistrations": "nonprimary_registrations",
    "nRegionVerificationCandidates": "stage7_candidates",
    "nCandidatePhotographRegions": "photograph_regions",
    "nStageSevenFailsDisagreement": "stage7_fails_delta",
    "nStageSevenNotSingleton": "stage7_not_singleton",
    "nStageSevenExcluded": "stage7_excluded",
    "nMultiboardCandidatesReviewed": "multiboard_reviewed",
    "nMultiboardMappingsAccepted": "multiboard_accepted",
    "nMultiboardCandidatesRejected": "multiboard_rejected",
    "nMappingsAutomaticPolicy": "automatic_policy",
    "nMappingsAccepted": "mappings_accepted",
    "nSidesStoppedOnPrimary": "sides_stopped_on_primary",
    "nSidesQueueExhausted": "sides_queue_exhausted",
    "nSidesWindowCap": "sides_window_cap",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    verified = verify_inputs(config["inputs"])
    request = {
        "schema_version": VERSION,
        "config_name": config["name"],
        "expected_catalog_candidates": config["expected_catalog_candidates"],
        "inputs": verified,
    }

    def build(tmp: Path) -> dict[str, Any]:
        counts = compute(config["inputs"], config["expected_catalog_candidates"])
        write_json(tmp / "prisma_counts.json", counts)
        macros = {name: tex_number(counts[key]) for name, key in MACROS.items()}
        write_macros(
            tmp / "numbers_prisma.tex",
            macros,
            f"Generated by {VERSION} (config {config['name']}); do not edit by hand.",
        )
        return {
            "code_revision": code_revision(Path(__file__).parent),
            "counts": counts,
            "closure_checks": "all passed",
            "schema_version": VERSION,
        }

    success = promote(args.output, request, build)
    print(json.dumps(success, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
