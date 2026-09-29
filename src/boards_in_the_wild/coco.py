"""Helpers for the public COCO files (``data/labels/coco/public_component_labels*.json``).

Photos are not hosted: every image has ``file_name = "link-only:<photo_sha256>"`` and
``photo_file_location_id`` (= ``photos.photo_id``). After rehydration
(``python -m boards_in_the_wild.rehydrate photos --out DIR ...``) point the COCO at the
local files with :func:`localize`.
"""

from __future__ import annotations

import json
from pathlib import Path


def localize(coco_path: str | Path, rehydrated_dir: str | Path, out_path: str | Path) -> dict:
    """Rewrite file_name to the rehydrated photo path; drop images whose photo is missing."""

    coco = json.loads(Path(coco_path).read_text())
    photos = Path(rehydrated_dir) / "photos"
    by_id = {p.stem: p for p in photos.glob("*")} if photos.is_dir() else {}
    kept = []
    for image in coco["images"]:
        local = by_id.get(image["photo_file_location_id"])
        if local is not None:
            image["file_name"] = str(local)
            kept.append(image)
    ids = {image["id"] for image in kept}
    coco["images"] = kept
    coco["annotations"] = [a for a in coco["annotations"] if a["image_id"] in ids]
    Path(out_path).write_text(json.dumps(coco))
    return {"images": len(kept), "annotations": len(coco["annotations"])}
