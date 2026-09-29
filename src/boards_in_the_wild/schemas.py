"""Table names, key columns and conventions of the Boards in the Wild release.

Every Parquet file carries its schema version in its file metadata under
``boards_in_the_wild.schema_version``; ``manifest.json`` lists every table, row count,
column list and SHA-256.
"""

from __future__ import annotations

HF_REPO_ID = "imandel/boards-in-the-wild"
RELEASE = "boards-in-the-wild"
TABLE_SCHEMA = "boards-in-the-wild.table/v1"

#: All photo coordinates (board polygons, homography outputs, labels) are in this frame:
#: OpenCV ``cv2.imread(path, cv2.IMREAD_COLOR)``, which applies the EXIF orientation.
PHOTO_COORDINATE_FRAME = "matcher_cv2_imread_color_exif_applied/v1"

#: table name -> (relative path, primary key)
TABLES: dict[str, tuple[str, str | None]] = {
    "sources": ("data/sources.parquet", "source_id"),
    "source_records": ("data/source_records.parquet", "source_record_id"),
    "project_groups": ("data/project_groups.parquet", "project_group_id"),
    "license_observations": ("data/license_observations.parquet", "observation_id"),
    "board_candidates": ("data/board_candidates.parquet", "candidate_id"),
    "cad_files": ("data/cad_files.parquet", "file_id"),
    "normalized_objects": ("data/normalized_objects.parquet", "normalized_object_id"),
    "photos": ("data/photos.parquet", "photo_id"),
    "mappings": ("data/mappings.parquet", "mapping_id"),
    "splits": ("data/splits.parquet", "leakage_family_id"),
    "precision_sampling_frame": ("data/evaluation/precision_sampling_frame.parquet", "pair_id"),
    "precision_final_labels": ("data/evaluation/precision_final_labels.parquet", "review_id"),
    "landmark_annotations": ("data/evaluation/landmark_annotations.parquet", "landmark_row_id"),
    "multiboard_review": ("data/evaluation/multiboard_review.parquet", "review_id"),
}

#: Joins that must close (child table, child column) -> (parent table, parent column).
JOINS = {
    ("mappings", "normalized_object_id"): ("normalized_objects", "normalized_object_id"),
    ("mappings", "photo_id"): ("photos", "photo_id"),
    ("mappings", "leakage_family_id"): ("splits", "leakage_family_id"),
    ("board_candidates", "candidate_id"): ("cad_files", "file_id"),
}

DISTRIBUTION_CLASSES = ("mirrored", "reference_only")
RETRIEVAL_RECIPES = ("direct_file", "repository_path", "package_member", "record_locator_only")
