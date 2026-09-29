"""Draw a mapping's board polygon (and component labels, if present) on its photograph.

    python examples/overlay.py --mapping-id stage7_relation_... --out overlays/
    python examples/overlay.py --mapping-id ... --data-dir /path/to/local/snapshot

The photograph is rehydrated from its recorded URL and verified by SHA-256 first
(photos are not hosted). It is decoded with ``cv2.imread(..., cv2.IMREAD_COLOR)`` so that
the EXIF orientation is applied: every released photo coordinate is in that frame.

Component labels (``data/labels/components.parquet``, one row per COCO annotation of
``data/labels/coco/public_component_labels.json``) are nominal design intent projected
from CAD: amodal, DNP-flagged (orange), and not visibility-verified.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from boards_in_the_wild.load import load_table, resolve_file
from boards_in_the_wild.rehydrate import rehydrate_photo


def _labels(mapping_id: str, data_dir, revision):
    try:
        path = resolve_file("data/labels/components.parquet", data_dir, revision)
    except Exception:  # noqa: BLE001 - labels are optional in early releases
        return []
    import pyarrow.parquet as pq

    table = pq.read_table(path, filters=[("mapping_id", "=", mapping_id)])
    return table.to_pylist()


def draw(mapping: dict, photo_path: Path, labels: list[dict]) -> np.ndarray:
    image = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"cv2 could not decode {photo_path}")
    if (image.shape[1], image.shape[0]) != (mapping["photo_width"], mapping["photo_height"]):
        raise RuntimeError("decoded size differs from the recorded display-frame size")
    thickness = max(2, int(round(max(image.shape[:2]) / 400)))
    for label in labels:
        polygon = label.get("polygon_photo")
        if polygon is None and label.get("bbox_photo_xywh"):
            x, y, w, h = label["bbox_photo_xywh"]
            polygon = [x, y, x + w, y, x + w, y + h, x, y + h]
        if polygon:
            pts = np.asarray(polygon, dtype=float).reshape(-1, 2)
            color = (0, 165, 255) if label.get("cad_dnp") else (0, 255, 0)
            cv2.polylines(image, [np.round(pts).astype(np.int32)], True, color, max(1, thickness // 2))
    polygon = mapping.get("board_polygon_photo")
    if polygon:
        pts = np.round(np.asarray(polygon, dtype=float)).astype(np.int32)
        cv2.polylines(image, [pts], True, (255, 0, 255), thickness)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping-id", required=True, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("overlays"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--revision", default=None)
    args = parser.parse_args()
    wanted = set(args.mapping_id)
    mappings = {m["mapping_id"]: m for m in load_table("mappings", args.data_dir, args.revision).to_pylist()
                if m["mapping_id"] in wanted}
    photos = {p["photo_id"]: p for p in load_table("photos", args.data_dir, args.revision).to_pylist()
              if p["photo_id"] in {m["photo_id"] for m in mappings.values()}}
    for mapping_id in args.mapping_id:
        mapping = mappings[mapping_id]
        result = rehydrate_photo(photos[mapping["photo_id"]], args.out / "rehydrated")
        if result.status not in ("ok", "present"):
            print(f"{mapping_id}: photo not rehydrated ({result.status}: {result.detail})")
            continue
        image = draw(mapping, Path(result.path), _labels(mapping_id, args.data_dir, args.revision))
        target = args.out / f"{mapping_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), image)
        print(f"{mapping_id}: wrote {target}")


if __name__ == "__main__":
    main()
