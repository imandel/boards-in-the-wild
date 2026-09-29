"""Small geometry helpers for mappings (numpy only).

Conventions
-----------
* Photo pixels are in the EXIF-oriented display frame
  (``matcher_cv2_imread_color_exif_applied/v1``): decode with
  ``cv2.imread(path, cv2.IMREAD_COLOR)``, which applies the EXIF orientation.
* ``render_to_photo_homography`` maps registration-render pixels (square canvas of
  ``render_canvas_size`` px) to photo pixels.
* ``board_to_photo_matrix`` (where validated) maps board millimetres in the KiCad SVG
  board frame (x right, y down, origin at the exported board bounding box, back side
  mirrored inside the transform) to photo pixels.
"""

from __future__ import annotations

import numpy as np


def as_matrix(value) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (3, 3):
        raise ValueError(f"expected 3x3 matrix, got {matrix.shape}")
    return matrix


def apply_homography(matrix, points) -> np.ndarray:
    """Apply a 3x3 projective transform to an (N, 2) array of points."""

    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    homogeneous = np.c_[pts, np.ones(len(pts))] @ as_matrix(matrix).T
    return homogeneous[:, :2] / homogeneous[:, 2:3]


def raw_to_display(points, orientation: int | None, raw_width: int, raw_height: int) -> np.ndarray:
    """Map points from the stored pixel grid to the EXIF display frame (orientations 1-8)."""

    x, y = np.asarray(points, dtype=float).reshape(-1, 2).T
    w, h = raw_width - 1, raw_height - 1
    mapping = {
        None: (x, y), 0: (x, y), 1: (x, y),
        2: (w - x, y), 3: (w - x, h - y), 4: (x, h - y),
        5: (y, x), 6: (h - y, x), 7: (h - y, w - x), 8: (y, w - x),
    }
    nx, ny = mapping[orientation]
    return np.c_[nx, ny]
