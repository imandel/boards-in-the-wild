# Boards in the Wild — code

Schemas, loaders, rehydration and examples for the **Boards in the Wild** dataset
([`imandel/boards-in-the-wild`](https://huggingface.co/datasets/imandel/boards-in-the-wild) on
the Hugging Face Hub): geometrically registered pairs of photographs and ECAD files of
fabricated printed circuit boards.

The dataset hosts metadata, mappings, labels and the openly licensed CAD files. It does
**not** host photographs or CAD files whose terms do not permit redistribution; for those
it records source URLs, SHA-256 hashes, sizes and retrieval recipes, and this repository
retrieves and verifies them.

## Quickstart

```bash
git clone https://github.com/imandel/boards-in-the-wild && cd boards-in-the-wild
uv sync                      # or: pip install -e .
```

```python
from boards_in_the_wild.load import load_table

mappings = load_table("mappings").to_pandas()      # 4,697 accepted mappings
photos = load_table("photos").to_pandas()          # candidate photographs (links only)
objects = load_table("normalized_objects").to_pandas()
m = mappings.iloc[0]
print(m.mapping_id, m.normalized_object_id, m.side, m.photo_id, m.cad_distribution_class)
```

Rehydrate the photographs of some mappings (downloaded from the recorded URL, accepted
only on an exact SHA-256 and size match; failures are reported, never skipped):

```bash
uv run python -m boards_in_the_wild.rehydrate photos --out rehydrated --mapping-ids <mapping_id> ...
uv run python -m boards_in_the_wild.rehydrate cad    --out rehydrated --mapping-ids <mapping_id> --project
```

Draw a mapping's board polygon (and component labels, when present) on its photograph:

```bash
uv run python examples/overlay.py --mapping-id <mapping_id> --out overlays/
```

Project board millimetres onto the photograph for a mirrored normalized KiCad file, or see
the `kicad-cli` import recipe to rebuild a reference-only design locally:

```bash
uv run python examples/project_board_mm.py --mapping-id <mapping_id> --out overlays/
cat examples/kicad_cli_import.md
```

Every call accepts `--data-dir <local snapshot>` (the directory holding `manifest.json`)
and `--revision <tag>` to pin a dataset version.

## Conventions

* **Photo coordinates** are in the EXIF-oriented display frame: OpenCV `cv2.imread` with
  `IMREAD_COLOR`, EXIF orientation applied (`matcher_cv2_imread_color_exif_applied/v1`).
  Each photo records its original EXIF orientation.
* **Render → photo**: `mappings.render_to_photo_homography` maps registration-render
  pixels to photo pixels. **Board mm → photo**: `mappings.board_to_photo_matrix` where a
  validated board transform exists (`board_transform_status`).
* **Labels** are nominal design intent projected from CAD: amodal, DNP flagged, not
  visibility-verified.

## Repository layout

| path | contents |
|---|---|
| `src/boards_in_the_wild/schemas.py` | table names, keys, joins, coordinate convention |
| `src/boards_in_the_wild/load.py` | pyarrow/pandas loader (local snapshot or Hub) |
| `src/boards_in_the_wild/rehydrate.py` | download + SHA-256/size verification + archive-member extraction + failure report |
| `src/boards_in_the_wild/geometry.py` | homography and EXIF-frame helpers |
| `examples/` | overlay, board-mm projection, `kicad-cli` import recipe |
| `paper_figures/` | scripts behind the paper's figures and tables |
| `construction/` | corpus construction pipeline (**coming**, see below) |

## Construction pipeline (coming)

The corpus construction code — catalog, normalization, registration rendering,
photo–CAD matching, acceptance, projection — will be added under `construction/` before
the SCF '26 proceedings (December 2026). The directory placeholders mark where it lands.

## License and citation

Code: Apache-2.0 (see `LICENSE`). Dataset: CC BY 4.0 for metadata, mappings and labels;
mirrored CAD files keep their own licenses, listed per file (see the dataset card).

```bibtex
@inproceedings{mandel2026boards,
  title     = {Boards in the Wild},
  author    = {Mandel, Ilan and Castillo, Joey and Ju, Wendy and Roumen, Thijs},
  booktitle = {Proceedings of the 11th ACM Symposium on Computational Fabrication (SCF '26)},
  year      = {2026},
  publisher = {ACM},
  doi       = {10.1145/3828611.3845653}
}
```

Takedown requests and questions: im334@cornell.edu.
