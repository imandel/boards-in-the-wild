# Rebuilding a reference-only design locally with `kicad-cli`

Reference-only CAD files are not redistributed. Retrieve the original file yourself and
convert it to KiCad the same way the corpus did:

1. **Retrieve and verify** the source file (exact SHA-256 and size):

   ```bash
   uv run python -m boards_in_the_wild.rehydrate cad --out rehydrated --candidate-ids <candidate_id> --project
   ```

   `rehydrated/rehydrate_report.jsonl` lists every file as `ok`, `hash_mismatch`,
   `download_failed`, `archive_member_not_found` or `manual_retrieval_required`.

2. **Convert** with KiCad 10 (the corpus used KiCad 10.0.4; a small Allegro subset used the
   10.0.0 importer as a fallback). The importer format follows
   `board_candidates.probable_format`:

   ```bash
   kicad-cli pcb import --format <altium|eagle|allegro|pads|...> \
       --output board.kicad_pcb --report-format json --report-file import_report.json \
       <retrieved source file>
   ```

   KiCad-format sources (`.kicad_pcb`, legacy `.brd`) need no import. Binary (pre-6)
   Eagle boards were converted with `pcb-rnd` before import.

3. **Check** the result against the release: the normalized file is not guaranteed to be
   byte-identical to ours (importer versions differ), so compare geometry, not hashes —
   e.g. footprint count and pad count against `normalized_objects.footprint_count` /
   `pad_count`, and the Edge.Cuts outline extent against the board polygon of the mapping.

4. **Project** with the mapping's `board_to_photo_matrix` (board millimetres in the KiCad
   SVG board frame → photo pixels), as in `examples/project_board_mm.py`. The same code
   works on your local file; only mirrored objects can be downloaded from the dataset.

Component-level labels (position, box/polygon, category, reference designator, DNP flag)
are released for every mapping, including reference-only ones; full pad/net/copper
geometry is released only for mirrored designs and can be regenerated from your local
normalized file.
