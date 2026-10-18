#!/usr/bin/env python3
"""Cut the chosen photos into tiles small enough to check completely.

A whole photo is too much to go over piece by piece: something gets lost
in the streams. Each photo chosen by pull_amas.py is cut to the part the
measurement uses (the prompts' crop: below the top 22%, right of the left
10%) and that part into COLS x ROWS tiles, no overlap. A tile is labelled on
its own, a piece cut by its edge is boxed where it shows, so tiles add up
to the photo without counting anything twice -- and every finished tile is
usable, whether or not the rest of its photo is done.

Also counts each tile's product pixels (rig/inline/flashcam.py
product_share, at full resolution), the denominator of the lot ratio.

Writes INLINE_LABELING_DIR/amas/tiles/<photo>_r<row>c<col>.jpg and
amas/tiles.json. Tiles already made are kept.

Usage:
    python tile_amas.py
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "labelstudio"))
from common import ROOT  # noqa: E402

AMAS = ROOT / "amas"
TILES = AMAS / "tiles"
CROP_TOP, CROP_LEFT = 0.22, 0.10
COLS, ROWS = 3, 2


def product_px(img):
    a = img.astype(np.float32)
    b, r = a[..., 0], a[..., 2]
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    return int(((sat > 0.28) & (r > b + 15) & (mx > 50)).sum())


def main():
    photos = json.loads((AMAS / "amas_photos.json").read_text(encoding="utf-8"))
    out = TILES / "tiles.json"
    index = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    TILES.mkdir(parents=True, exist_ok=True)
    for name, d in photos.items():
        img = cv2.imread(str(AMAS / name))
        if img is None:
            continue
        h, w = img.shape[:2]
        crop = img[int(h * CROP_TOP):, int(w * CROP_LEFT):]
        ch, cw = crop.shape[:2]
        for r in range(ROWS):
            for c in range(COLS):
                tile = f"{Path(name).stem}_r{r + 1}c{c + 1}.jpg"
                if tile in index:
                    continue
                part = crop[r * ch // ROWS:(r + 1) * ch // ROWS, c * cw // COLS:(c + 1) * cw // COLS]
                cv2.imwrite(str(TILES / tile), part, [cv2.IMWRITE_JPEG_QUALITY, 95])
                index[tile] = {**d, "tile": tile, "row": r + 1, "col": c + 1,
                               "width": part.shape[1], "height": part.shape[0],
                               "product_px": product_px(part)}
    out.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(index)} tiles from {len(photos)} photos in {TILES}")


if __name__ == "__main__":
    main()
