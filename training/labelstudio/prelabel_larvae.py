#!/usr/bin/env python3
"""Pre-draw the larvae on the chute photos, by colour, as Label Studio predictions.

On the chute the larvae are tan (OpenCV hue ~20, saturation ~70) and the
chute is blue-grey (hue ~105, saturation ~30), measured on
IL-20261013-091313. The larva shadows are the chute's own colour, only
darker, so a hue + saturation threshold takes the larvae and leaves both
the chute and the shadows out. Touching larvae are split where they meet
(split_touching), so each region is about one larva and can be relabeled
on its own in Label Studio.

Frass is NOT pre-drawn. Grey-brown on a grey chute does not separate by
colour, which is the reason for training a model at all. The labeler
corrects the larvae and draws the frass.

The result goes in as a prediction (model_version below), never as an
annotation, so nothing counts as labeled until a person submits it. Photos
that already have a submitted annotation are left alone; rerunning replaces
this script's earlier predictions on the rest, so thresholds can be retuned
and reapplied.

Needs OpenCV, which the system Python does not have. Run it through uv:

    python -m uv run --no-project --python 3.12 --with opencv-python-headless --with numpy python prelabel_larvae.py
    ... prelabel_larvae.py --preview out/   # overlays only, Label Studio untouched
"""

import argparse
from pathlib import Path

import cv2
import numpy as np

from common import IMAGES, find_project, ls

MODEL_VERSION = "larves-couleur-v1"

# OpenCV HSV: hue 0-180, saturation and value 0-255.
HUE = (5, 40)          # tan/yellow. The chute sits at ~100-112.
MIN_SATURATION = 45    # chute ~20-37; larvae median ~73
MIN_VALUE = 70         # leaves out the darkest pixels (shadows, dark frass)
MIN_AREA_PX = 60       # smaller blobs are specks, not larvae (one larva is ~600+)
EMPTY_TAN = 0.03       # a photo under this share of tan is a bare chute (see stain_mask)
STAIN_SHARE = 0.5      # tan in more bare-chute photos than this = fixed on the chute
MIN_HOLE_PX = 40       # enclosed gaps smaller than this are filled, larger ones cut open
SEED_PX = 5.0          # watershed seed depth; a larva is ~18px wide (half-width ~9)
SIMPLIFY_PX = 1.5      # polygon simplification tolerance: fewer points to edit


def tan(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (HUE[0], MIN_SATURATION, MIN_VALUE), (HUE[1], 255, 255))


def stain_mask(paths):
    """Pixels that read tan on the empty chute: something fixed on it.

    There is a rust stain across the top of the chute, tan specks the
    threshold takes for larvae. Learnt from the photos of a bare chute (under
    EMPTY_TAN tan; the rig sent eight on 2026-10-13 10:57-11:08) and not from
    all photos: the product runs in the same two lanes every time, so across
    all photos those lanes would look like a fixed stain too. Worked out
    afresh each run, so it follows the chute if it is cleaned or lined."""
    count, n = None, 0
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        t = tan(img) > 0
        if t.mean() > EMPTY_TAN:
            continue  # product in view
        t = t.astype(np.uint16)
        count = t if count is None else count + t
        n += 1
    if n < 3:
        return None  # not enough bare-chute photos to tell
    stain = ((count / n) > STAIN_SHARE).astype(np.uint8) * 255
    # The specks flicker in and out of the threshold: grow them into the patch.
    return cv2.dilate(stain, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))


def open_holes(mask):
    """Cut each gap enclosed by larvae open to the outside.

    Touching larvae often form a ring with bare chute (and frass) inside.
    A polygon has no holes, so the blob's outline alone would claim the gap
    as larva. A 2px slit from the top of each gap up to the outside turns the
    gap into a deep notch the outline follows in and back out. Gaps smaller
    than MIN_HOLE_PX are only the space between a larva's segments: filled."""
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None:
        return mask
    for c, (_, _, _, parent) in zip(contours, hier[0]):
        if parent < 0:
            continue  # an outer outline, not a gap
        if cv2.contourArea(c) < MIN_HOLE_PX:
            cv2.drawContours(mask, [c], -1, 255, cv2.FILLED)
            continue
        x, y = c[c[:, 0, 1].argmin()][0]
        while y >= 0 and mask[y, x]:
            mask[y, x:x + 2] = 0
            y -= 1
    return mask


def split_touching(mask):
    """Label image: 0 = chute, one number per region of touching larvae.

    Splits touching larvae where they meet, so each comes out as its own
    region (relabelable on its own in Label Studio) instead of one cluster
    outline with hundreds of points. Watershed on the distance to the chute:
    every part of the mask deeper than SEED_PX is a seed (roughly one per
    larva body, ~18px wide), and each mask pixel goes to the seed it drains
    to, so the borders fall at the narrow joins between larvae. Nothing is
    cut away: every mask pixel ends up in some region. Larvae lying along
    each other with no narrowing between them stay together."""
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    n, seeds = cv2.connectedComponents((dist > SEED_PX).astype(np.uint8))
    markers = seeds + 1                      # 1 = chute, 2.. = seeds
    markers[(mask > 0) & (seeds == 0)] = 0   # to be decided by the watershed
    relief = cv2.cvtColor(255 - cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8),
                          cv2.COLOR_GRAY2BGR)
    cv2.watershed(relief, markers)
    inside = mask > 0
    labels = np.where(inside & (markers >= 2), markers - 1, 0).astype(np.float32)
    # Watershed border pixels (-1) inside the mask join a neighbouring region.
    border = inside & (markers == -1)
    labels[border] = cv2.dilate(labels, np.ones((3, 3), np.uint8))[border]
    labels = labels.astype(np.int32)
    # Larvae too thin for a seed drained to the chute: each its own region.
    _, thin = cv2.connectedComponents((inside & (labels == 0)).astype(np.uint8))
    labels[thin > 0] = thin[thin > 0] + n
    return labels


def larva_polygons(img, stain=None):
    """[[(x%, y%), ...], ...], one per region, in Label Studio's 0-100 coordinates."""
    raw = tan(img)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel)     # salt noise
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)   # pinholes, larva segment lines
    # Specks and the rust stain go before splitting, judged as whole blobs.
    n, blobs, stats, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < MIN_AREA_PX:
            mask[blobs == i] = 0
        elif stain is not None and (stain[blobs == i] > 0).mean() > 0.5:
            mask[blobs == i] = 0  # mostly the rust stain, not a larva on it
    labels = split_touching(mask)
    h, w = mask.shape
    polys = []
    order = np.argsort(labels, axis=None)
    bounds = np.searchsorted(labels.ravel()[order], np.arange(labels.max() + 2))
    for k in range(1, labels.max() + 1):
        idx = order[bounds[k]:bounds[k + 1]]
        if not len(idx):
            continue
        ys, xs = np.unravel_index(idx, labels.shape)
        x0, y0 = xs.min(), ys.min()
        region = np.zeros((ys.max() - y0 + 3, xs.max() - x0 + 3), np.uint8)
        region[ys - y0 + 1, xs - x0 + 1] = 255
        region = open_holes(region)
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            c = cv2.approxPolyDP(c, SIMPLIFY_PX, True)
            if len(c) < 3:
                continue
            polys.append([(round((x + x0 - 1) / w * 100, 3), round((y + y0 - 1) / h * 100, 3))
                          for x, y in c[:, 0]])
    return polys, mask


def preview(paths, out, stain):
    out.mkdir(parents=True, exist_ok=True)
    for p in paths:
        img = cv2.imread(str(p))
        polys, mask = larva_polygons(img, stain)
        # Drawn from the polygons, not the mask: exactly what Label Studio
        # receives, so a polygon claiming chute shows up here.
        h, w = mask.shape
        shapes = [np.array([(x * w / 100, y * h / 100) for x, y in poly], np.int32) for poly in polys]
        mask = np.zeros_like(mask)
        cv2.fillPoly(mask, shapes, 255)
        over = img.copy()
        over[mask > 0] = (0.5 * over[mask > 0] + 0.5 * np.array((237, 128, 47))).astype(np.uint8)
        cv2.polylines(over, shapes, True, (255, 255, 255), 1)
        cv2.imwrite(str(out / p.name), np.hstack([img, over]))
        print(f"{p.name}: {len(polys)} blobs, larvae {100 * (mask > 0).mean():.1f}% of the photo")


def to_result(polys, w, h):
    return [{
        "from_name": "label", "to_name": "image", "type": "polygonlabels",
        "original_width": w, "original_height": h, "image_rotation": 0,
        "value": {"points": [list(pt) for pt in poly], "polygonlabels": ["Larves"], "closed": True},
    } for poly in polys]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preview", type=Path, help="write side-by-side overlays here instead")
    ap.add_argument("--limit", type=int, help="only the first N photos (preview)")
    args = ap.parse_args()

    stain = stain_mask(sorted(IMAGES.glob("*.jpg")))
    if args.preview:
        preview(sorted(IMAGES.glob("*.jpg"))[:args.limit], args.preview, stain)
        if stain is not None:
            cv2.imwrite(str(args.preview / "_stain.png"), stain)
        return

    pid = find_project()
    if pid is None:
        raise SystemExit("No inline project in Label Studio yet: run sync_project.py.")

    tasks, page = [], 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=500, fields="all")
        batch = resp["tasks"] if isinstance(resp, dict) else resp
        tasks += batch
        if len(batch) < 500:
            break
        page += 1

    done = skipped = 0
    for t in tasks:
        if t.get("total_annotations", 0) > 0:
            skipped += 1
            continue
        for old in ls("GET", "/api/predictions", task=t["id"]):
            if old.get("model_version") == MODEL_VERSION:
                ls("DELETE", f"/api/predictions/{old['id']}")
        img = cv2.imread(str(IMAGES / f"{t['data']['reading_id']}.jpg"))
        if img is None:
            print(f"  {t['data']['reading_id']}: photo missing on disk, skipped")
            continue
        polys, mask = larva_polygons(img, stain)
        ls("POST", "/api/predictions", {
            "task": t["id"], "model_version": MODEL_VERSION,
            "result": to_result(polys, mask.shape[1], mask.shape[0]),
        })
        done += 1
    # Show these as the starting outline when a photo is opened. Only after
    # the upload: Label Studio refuses a model_version with no predictions.
    ls("PATCH", f"/api/projects/{pid}", {"model_version": MODEL_VERSION, "show_collab_predictions": True})
    print(f"Pre-drew larvae on {done} photos ({skipped} already labeled, left alone)")


if __name__ == "__main__":
    main()
