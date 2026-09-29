# ECAD paper figures

Fresh, receipt-bound figure generators for the current `ecad-corpus` graph. This package
reimplements publication views from current immutable tables; it does not import legacy
pipeline code or treat legacy figure tables as current evidence.

## Corpus characterization v1

The builder emits:

- `corpus_coverage.{pdf,svg,png}` — normalized objects and accepted
  CAD-side–photo-region relations by source and original ECAD format;
- `design_characteristics.{pdf,svg,png}` — copper layers, component count, physical
  board area, and passive-package prevalence;
- `area_vs_complexity.{pdf,svg,png}` — one point per normalized object;
- canonical Parquet/CSV inputs, summaries, validation, checksums, and an HTML index.

Counting units are intentionally separate. Aliases do not inflate normalized-object counts.
The correspondence tier contains the complete 4,319-relation result of the frozen
`primary-single-populated-delta002-singleton/v1` policy. Every accepted relation occupies a
singleton exact-photo spatial cluster, but perceptually repeated photographs remain separate
relations. The build validates 4,319 relations, 4,319 clusters, 4,317 photo locations, and
3,551 normalized objects before rendering.

The normalized-board reads are direct and manifest-bound: each worker receives one exact path
from `normalized_objects.parquet`; no parent directory is enumerated. A resumable JSONL cache
avoids rereading approximately 42 GB of normalized KiCad text after preemption. Cache entries
are reused only when parser version, normalized content hash, and render-metadata hash match.
Render metadata is selected from `render_outcomes.parquet` and checked against its recorded
SHA-256 before use.

The supporting pre-COCO release provides checksum-inventoried normalized objects, aliases,
render outcomes, Stage 7 candidates, spatial clusters, and their crosswalk. The exact acceptance
policy is independently checksum-bound through the frozen paper-evaluation success receipt.

```bash
export UV_PROJECT_ENVIRONMENT=$HOME/.venvs/ecad_paper_figures_v1
uv sync --project experiments/paper_figures --group dev

uv run --project experiments/paper_figures --frozen ecad-paper-figures build \
  --release-root $ECAD_DATA/releases/ecad-corpus-pre-coco-stage7-v1 \
  --normalization-dir $ECAD_DATA/runs/normalization_v2 \
  --paper-evaluation-dir $ECAD_DATA/runs/unified_match_pipeline_v1/stage7_paper_evaluation_v1 \
  --config experiments/paper_figures/configs/corpus_characterization_v1.json \
  --normalized-runtime-root $ECAD_DATA \
  --render-runtime-root $ECAD_DATA \
  --measurement-cache $ECAD_DATA/runs/paper_figures_v1/work/corpus_characterization_pre_coco_v1.measurements.jsonl \
  --output $ECAD_DATA/runs/paper_figures_v1/corpus_characterization_accepted_relations_v2 \
  --workers 12
```

An identical completed rerun verifies request and artifact checksums and performs no work.

## Compact coverage layout v3

`ecad_paper_figures.compact_coverage` renders the selected paired-bar coverage view from the
receipt-bound `design_metrics.parquet` and `accepted_relations.parquet`. It preserves every count
while using a fixed 7.0 × 2.28 inch canvas and a dedicated legend strip directly above the
panels. Paired count labels use separated baselines so nearby normalized and accepted totals do
not intersect at publication scale.

```bash
uv run --project experiments/paper_figures --frozen \
  python -m ecad_paper_figures.compact_coverage \
  --design-metrics /path/to/design_metrics.parquet \
  --accepted-relations /path/to/accepted_relations.parquet \
  --output-dir /path/to/new-output \
  --expected-designs 14024 \
  --expected-relations 4319 \
  --source-fingerprint 0c3a101c462a9318b1b504dfc75329f9200f29cde6d7884ac6765a38380508ac \
  --code-revision "$CODE_REVISION"
```

## Compact area-versus-complexity layout v2

`ecad_paper_figures.compact_area_scatter` reproduces the selected 7.0 × 2.42 inch Figure 4
layout without rasterizing its 13,606-point cloud. The PDF therefore remains sharp at any zoom
without occupying more manuscript space. Its legend uses separate, fully opaque marker handles;
the plotted points retain low opacity to show density under overplotting. A 440-DPI PNG is also
emitted for screen review.

```bash
uv run --project experiments/paper_figures --frozen \
  python -m ecad_paper_figures.compact_area_scatter \
  --design-metrics /path/to/design_metrics.parquet \
  --output-dir /path/to/new-output \
  --expected-designs 14024 \
  --expected-points 13606 \
  --source-fingerprint 0c3a101c462a9318b1b504dfc75329f9200f29cde6d7884ac6765a38380508ac \
  --code-revision "$CODE_REVISION"
```
