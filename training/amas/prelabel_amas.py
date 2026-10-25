#!/usr/bin/env python3
"""Pre-fill the unlabelled tiles with a trained model's MEO boxes.

Run after train_meo.py. The model's boxes go in as Label Studio predictions
on every tile nobody has labelled yet: opening a tile shows them already
drawn, to correct rather than draw from scratch. A box only becomes a label
when the tile is submitted, so every pre-filled box is still checked by eye
-- and one left unfixed counts as yours, so check as carefully as before.

Rerun after retraining: the old predictions are replaced.

Nested boxes are dropped: where a box lies mostly (NESTED, 60%) inside a
more confident box of the same class, only the confident one is kept. The
YOLO11 models' own NMS (IoU 0.7) lets a box drawn around a whole clump and
a second around part of it both through -- 425 such pairs in the pre-fills
of 2026-10-25 -- and each one left unfixed is a doubled label.
--tidy applies the same to the pre-fills already in Label Studio, without
running a model.

Usage:
    python prelabel_amas.py                    # model from amas_runs/meo
    python prelabel_amas.py --name meo --conf 0.3
    python prelabel_amas.py --dry-run          # count boxes, change nothing
    python prelabel_amas.py --tidy             # drop nested boxes from the existing pre-fills
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
sys.path.insert(0, str(HERE.parents[1] / "rig" / "inline"))
from common import ROOT, ls  # noqa: E402
from sync_amas import AMAS, find_project  # noqa: E402
import meo_detect  # noqa: E402

CLASSES = ["MEO", "Peau", "Incertain", "Bicolore", "Poussière"]
NESTED = 0.6   # share of the smaller box's area inside the other


def drop_nested(boxes):
    """[(class, x0, y0, x1, y1, score)] -> the same without any box that lies
    NESTED or more inside a more confident box of the same class."""
    kept = []
    for b in sorted(boxes, key=lambda b: -b[5]):
        c, x0, y0, x1, y1 = b[:5]
        area = max((x1 - x0) * (y1 - y0), 1e-9)
        if not any(k[0] == c and
                   max(0, min(x1, k[3]) - max(x0, k[1])) * max(0, min(y1, k[4]) - max(y0, k[2]))
                   >= NESTED * min(area, (k[3] - k[1]) * (k[4] - k[2]))
                   for k in kept):
            kept.append(b)
    return kept


def unlabelled():
    """-> (project id, the tasks nobody has labelled, drafted or skipped yet)"""
    pid = find_project()
    tasks, page = [], 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=100, fields="all")
        batch = resp["tasks"] if isinstance(resp, dict) else resp
        tasks += batch
        if len(batch) < 100:
            break
        page += 1
    return pid, [t for t in tasks if not (t.get("total_annotations") or t.get("draft_exists")
                                          or t.get("cancelled_annotations"))]


def tidy(tasks, dry_run):
    """Drop nested boxes from the pre-fills already on the unlabelled tasks."""
    changed = dropped = 0
    for t in tasks:
        for p in t.get("predictions") or []:
            if not isinstance(p, dict):
                continue
            res = p.get("result") or []
            boxes = [(r["value"]["rectanglelabels"][0], r["value"]["x"], r["value"]["y"],
                      r["value"]["x"] + r["value"]["width"], r["value"]["y"] + r["value"]["height"],
                      r.get("score") or 0.0, i) for i, r in enumerate(res)]
            keep = {b[6] for b in drop_nested(boxes)}
            if len(keep) == len(res):
                continue
            changed += 1
            dropped += len(res) - len(keep)
            if not dry_run:
                ls("PATCH", f"/api/predictions/{p['id']}", {"result": [r for i, r in enumerate(res) if i in keep]})
    print(f"{'Would drop' if dry_run else 'Dropped'} {dropped} nested boxes on {changed} tiles")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="meo", help="training run in amas_runs/")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="pre-fill even when the model looks far off")
    ap.add_argument("--tidy", action="store_true", help="only drop nested boxes from the existing pre-fills")
    args = ap.parse_args()
    if args.tidy:
        tidy(unlabelled()[1], args.dry_run)
        return

    # The run's model as exported for Ariel, read by Ariel's own code.
    weights = ROOT / "amas_runs" / args.name / "ariel" / "meo.onnx"
    meo_detect.use(weights)
    meo_detect.CONF = args.conf
    version = f"{args.name}-{int(weights.stat().st_mtime)}"

    def predict(path):
        """-> (tile height, width, [(class, x0, y0, x1, y1, score)] in tile px)"""
        a = np.asarray(Image.open(path).convert("RGB"))
        return a.shape[0], a.shape[1], drop_nested(meo_detect.detect_tiles([a])[0])
    pid, todo = unlabelled()

    # Hand labels run ~2 MEO per tile. A model drawing many times that is not
    # ready: correcting a flood of boxes is slower than drawing from scratch.
    sample = todo[::max(1, len(todo) // 60)]
    per_tile = sum(len(predict(AMAS / "tiles" / t["data"]["tile"])[2]) for t in sample) / max(1, len(sample))
    print(f"model draws {per_tile:.1f} boxes per tile on a sample of {len(sample)}")
    if per_tile > 6 and not (args.force or args.dry_run):
        raise SystemExit("Too many boxes for a usable pre-fill: train longer or raise --conf. Nothing changed.")

    n_boxes = 0
    for i, t in enumerate(todo):
        img = AMAS / "tiles" / t["data"]["tile"]
        h, w, found = predict(img)
        result = []
        for c, x0, y0, x1, y1, s in found:
            # numpy's floats do not go through json.dumps (the YOLO11 export
            # gives float32 corners).
            x0, y0, x1, y1, s = (float(v) for v in (x0, y0, x1, y1, s))
            result.append({"from_name": "box", "to_name": "image", "type": "rectanglelabels",
                           "original_width": w, "original_height": h, "image_rotation": 0, "score": s,
                           "value": {"x": 100 * x0 / w, "y": 100 * y0 / h, "width": 100 * (x1 - x0) / w,
                                     "height": 100 * (y1 - y0) / h, "rotation": 0,
                                     "rectanglelabels": [CLASSES[int(c)]]}})
        n_boxes += len(result)
        if not args.dry_run:
            for old in t.get("predictions") or []:
                ls("DELETE", f"/api/predictions/{old['id'] if isinstance(old, dict) else old}")
            ls("POST", "/api/predictions", {"task": t["id"], "result": result, "model_version": version,
                                            "score": min((b["score"] for b in result), default=1.0)})
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(todo)} tiles", flush=True)

    if not args.dry_run:
        # Show the model's boxes on the labelling screen, ready to correct.
        ls("PATCH", f"/api/projects/{pid}", {"model_version": version, "show_collab_predictions": True})
    print(f"{'Would pre-fill' if args.dry_run else 'Pre-filled'} {len(todo)} tiles with {n_boxes} boxes "
          f"({version}, conf {args.conf})")


if __name__ == "__main__":
    main()
