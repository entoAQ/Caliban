#!/usr/bin/env python3
"""Pre-fill the unlabelled tiles with a trained model's MEO boxes.

Run after train_meo.py. The model's boxes go in as Label Studio predictions
on every tile nobody has labelled yet: opening a tile shows them already
drawn, to correct rather than draw from scratch. A box only becomes a label
when the tile is submitted, so every pre-filled box is still checked by eye
-- and one left unfixed counts as yours, so check as carefully as before.

Rerun after retraining: the old predictions are replaced.

Usage:
    python prelabel_amas.py                    # model from amas_runs/meo
    python prelabel_amas.py --name meo --conf 0.3
    python prelabel_amas.py --dry-run          # count boxes, change nothing
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT, ls  # noqa: E402
from sync_amas import AMAS, find_project  # noqa: E402

CLASSES = ["MEO", "Peau", "Incertain", "Prépupe"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="meo", help="training run in amas_runs/")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="pre-fill even when the model looks far off")
    args = ap.parse_args()

    from ultralytics import YOLO
    weights = ROOT / "amas_runs" / args.name / "weights" / "best.pt"
    model = YOLO(str(weights))
    version = f"{args.name}-{int(weights.stat().st_mtime)}"
    pid = find_project()

    tasks, page = [], 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=100, fields="all")
        batch = resp["tasks"] if isinstance(resp, dict) else resp
        tasks += batch
        if len(batch) < 100:
            break
        page += 1
    todo = [t for t in tasks if not (t.get("total_annotations") or t.get("draft_exists")
                                     or t.get("cancelled_annotations"))]

    # Hand labels run ~2 MEO per tile. A model drawing many times that is not
    # ready: correcting a flood of boxes is slower than drawing from scratch.
    sample = todo[::max(1, len(todo) // 60)]
    per_tile = sum(len(model.predict(str(AMAS / "tiles" / t["data"]["tile"]), imgsz=416, conf=args.conf,
                                     device="cpu", verbose=False)[0].boxes) for t in sample) / max(1, len(sample))
    print(f"model draws {per_tile:.1f} boxes per tile on a sample of {len(sample)}")
    if per_tile > 6 and not (args.force or args.dry_run):
        raise SystemExit("Too many boxes for a usable pre-fill: train longer or raise --conf. Nothing changed.")

    n_boxes = 0
    for i, t in enumerate(todo):
        img = AMAS / "tiles" / t["data"]["tile"]
        r = model.predict(str(img), imgsz=416, conf=args.conf, device="cpu", verbose=False)[0]
        h, w = r.orig_shape
        result = []
        for (x0, y0, x1, y1), c, s in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
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
