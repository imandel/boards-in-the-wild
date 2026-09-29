"""Project board millimetres onto a photograph (mirrored normalized KiCad file).

    python examples/project_board_mm.py --mapping-id <mapping_id> --out overlays/

For a mapping whose CAD is mirrored, downloads the normalized ``.kicad_pcb`` from the
dataset, parses footprint origins, pads and the Edge.Cuts outline, maps them with the
mapping's ``board_to_photo_matrix`` (board millimetres, KiCad board frame: x right,
y down) into the photograph (EXIF-oriented display frame) and draws them.

For reference-only designs, retrieve and convert the source yourself
(``examples/kicad_cli_import.md``) and pass ``--kicad-pcb <your board.kicad_pcb>``.
Mappings whose ``board_transform_status`` is not a validated transform carry no
``board_to_photo_matrix``; only ``render_to_photo_homography`` is published for them.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from boards_in_the_wild.geometry import apply_homography
from boards_in_the_wild.load import load_table, resolve_file
from boards_in_the_wild.rehydrate import rehydrate_photo


# ----------------------------------------------------------------------------- s-expressions
def parse_sexpr(text: str):
    tokens, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            tokens.append(c)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            tokens.append(("str", "".join(buf)))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            tokens.append(text[i:j])
            i = j
    stack = [[]]
    for tok in tokens:
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok[1] if isinstance(tok, tuple) else tok)
    return stack[0][0]


def children(node, key):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == key]


def first(node, key):
    found = children(node, key)
    return found[0] if found else None


def at_of(node):
    at = first(node, "at")
    if not at:
        return 0.0, 0.0, 0.0
    vals = [float(v) for v in at[1:4] if not isinstance(v, list) and _is_number(v)]
    x, y = vals[0], vals[1]
    return x, y, vals[2] if len(vals) > 2 else 0.0


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def board_geometry(pcb_path: Path):
    tree = parse_sexpr(pcb_path.read_text(errors="replace"))
    footprints, pads, edges = [], [], []
    for fp in children(tree, "footprint") + children(tree, "module"):
        fx, fy, frot = at_of(fp)
        layer = (first(fp, "layer") or [None, None])[1]
        ref = None
        for prop in children(fp, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = prop[2]
        for text in children(fp, "fp_text"):
            if len(text) > 2 and text[1] == "reference":
                ref = ref or text[2]
        footprints.append({"ref": ref, "x": fx, "y": fy, "rot": frot, "layer": layer})
        theta = math.radians(frot)
        for pad in children(fp, "pad"):
            px, py, _ = at_of(pad)
            # KiCad: y down, angles counter-clockwise on screen -> rotate by -theta
            ax = fx + px * math.cos(theta) + py * math.sin(theta)
            ay = fy - px * math.sin(theta) + py * math.cos(theta)
            pads.append({"ref": ref, "x": ax, "y": ay, "layer": layer})
    for item in (children(tree, "gr_line") + children(tree, "gr_rect") + children(tree, "gr_arc")
                 + children(tree, "gr_poly") + children(tree, "gr_circle")):
        layer = (first(item, "layer") or [None, None])[1]
        if layer != "Edge.Cuts":
            continue
        kind = item[0]
        if kind == "gr_poly":
            pts = [(float(p[1]), float(p[2])) for p in children(first(item, "pts") or ["pts"], "xy")]
            edges += list(zip(pts, pts[1:] + pts[:1]))
            continue
        s, e = first(item, "start"), first(item, "end")
        if not (s and e):
            continue
        a, b = (float(s[1]), float(s[2])), (float(e[1]), float(e[2]))
        if kind == "gr_rect":
            corners = [a, (b[0], a[1]), b, (a[0], b[1])]
            edges += list(zip(corners, corners[1:] + corners[:1]))
        elif kind == "gr_circle":  # start = centre, end = point on circle
            r = math.dist(a, b)
            pts = [(a[0] + r * math.cos(t), a[1] + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 49)]
            edges += list(zip(pts, pts[1:]))
        elif kind == "gr_arc" and first(item, "mid"):
            mid = first(item, "mid")
            m = (float(mid[1]), float(mid[2]))
            edges += [(a, m), (m, b)]  # coarse: two chords through the arc midpoint
        else:
            edges.append((a, b))
    return footprints, pads, edges


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping-id", required=True)
    parser.add_argument("--out", type=Path, default=Path("overlays"))
    parser.add_argument("--kicad-pcb", type=Path, default=None, help="local board for reference-only designs")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--revision", default=None)
    args = parser.parse_args()
    mapping = next(m for m in load_table("mappings", args.data_dir, args.revision).to_pylist()
                   if m["mapping_id"] == args.mapping_id)
    matrix = mapping.get("board_to_photo_matrix")
    if matrix is None:
        raise SystemExit(f"no validated board transform ({mapping['board_transform_status']}); "
                         "only render_to_photo_homography is published for this mapping")
    if args.kicad_pcb is None:
        obj = next(o for o in load_table("normalized_objects", args.data_dir, args.revision).to_pylist()
                   if o["normalized_object_id"] == mapping["normalized_object_id"])
        if not obj["mirror_path"]:
            raise SystemExit("reference-only design: retrieve and convert it yourself (kicad_cli_import.md)")
        pcb = resolve_file(obj["mirror_path"], args.data_dir, args.revision)
    else:
        pcb = args.kicad_pcb
    footprints, pads, edges = board_geometry(Path(pcb))
    photo = next(p for p in load_table("photos", args.data_dir, args.revision).to_pylist()
                 if p["photo_id"] == mapping["photo_id"])
    res = rehydrate_photo(photo, args.out / "rehydrated")
    if res.status not in ("ok", "present"):
        raise SystemExit(f"photo not rehydrated: {res.status} {res.detail}")
    image = cv2.imread(res.path, cv2.IMREAD_COLOR)
    side_layer = "F.Cu" if mapping["side"] == "F" else "B.Cu"
    thickness = max(2, int(round(max(image.shape[:2]) / 500)))
    for (a, b) in edges:
        p = apply_homography(matrix, [a, b]).round().astype(np.int32)
        cv2.line(image, tuple(int(v) for v in p[0]), tuple(int(v) for v in p[1]), (255, 0, 255), thickness)
    side_pads = [q for q in pads if q["layer"] == side_layer]
    if side_pads:
        pts = apply_homography(matrix, [(q["x"], q["y"]) for q in side_pads])
        for x, y in pts:
            cv2.circle(image, (int(round(x)), int(round(y))), thickness + 1, (0, 255, 255), -1)
    target = args.out / f"{args.mapping_id}.board_mm.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), image)
    print(f"{len(footprints)} footprints, {len(side_pads)} {mapping['side']}-side pads, "
          f"{len(edges)} Edge.Cuts segments -> {target}")


if __name__ == "__main__":
    main()
