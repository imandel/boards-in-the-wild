# Provenance of `paper_figures/`

Copied from the private `imandel/ecad-corpus` repository, directory
`experiments/paper_figures`, at commit `55bf6906f9e4c1b22122bc8140807bd34ab98e78`
(branch `exp/paper-camera-ready-analysis`), on 2026-09-29.

Changes made when copying (no behavior change beyond path resolution):

* absolute cluster paths in configs, docstrings and the README were replaced by the
  placeholders `$ECAD_RUNS/` (project run namespaces) and `$ECAD_DATA/` (legacy project
  data root); `cr_common.verify_inputs` expands environment variables in input paths.

The configs name the exact inputs (by SHA-256) behind each paper figure and table. Those
inputs are internal pipeline receipts; the released dataset tables carry the same counts
(see the dataset's `manifest.json`). The construction pipeline that produced them will be
published under `construction/` before the SCF '26 proceedings.
