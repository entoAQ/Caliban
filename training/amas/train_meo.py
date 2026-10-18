#!/usr/bin/env python3
"""Train the MEO detector on the exported tiles, and check it per lot.

Run export_amas.py first. Trains a small YOLO detector (ultralytics,
CPU is enough at this size) on INLINE_LABELING_DIR/amas_yolo, then for the
val lots -- lots it never saw in training -- sets the model's MEO area over
product area beside the hand-boxed ratio and the lab ME%. The model is only
worth using where it tracks the hand boxes; whether the hand boxes track the
lab is export_amas.py's lots.csv.

Runs land in INLINE_LABELING_DIR/amas_runs/<name>/ (weights/best.pt).

Needs ultralytics (pip install ultralytics).

Usage:
    python train_meo.py                   # 80 epochs
    python train_meo.py --epochs 3 --name smoke
"""

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT  # noqa: E402

DATA = ROOT / "amas_yolo"
TILES = ROOT / "amas" / "tiles"
RUNS = ROOT / "amas_runs"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--name", default="meo")
    ap.add_argument("--model", default="yolo11n.pt", help="starting weights")
    ap.add_argument("--conf", type=float, default=0.25, help="detection threshold for the lot check")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(args.model)
    # Tiles are ~400 px: train at that size, so 10-30 px pieces are not
    # shrunk. Flips are fine (the chute has no up/down for a clump), colour
    # jitter kept small (colour is half of telling frass from larva).
    model.train(data=str(DATA / "data.yaml"), epochs=args.epochs, imgsz=416, batch=16,
                project=str(RUNS), name=args.name, exist_ok=True, device="cpu",
                fliplr=0.5, flipud=0.5, hsv_h=0.005, hsv_s=0.3, hsv_v=0.2, mosaic=1.0,
                plots=False, verbose=False)

    best = YOLO(str(RUNS / args.name / "weights" / "best.pt"))
    index = json.loads((TILES / "tiles.json").read_text(encoding="utf-8"))
    lots = {}
    for img in sorted((DATA / "images" / "val").glob("*.jpg")):
        d = index[img.name]
        hand = 0.0
        for line in (DATA / "labels" / "val" / (img.stem + ".txt")).read_text().splitlines():
            c, _, _, w, h = line.split()
            if c == "0":
                hand += float(w) * float(h) * d["width"] * d["height"]
        r = best.predict(str(img), imgsz=416, conf=args.conf, device="cpu", verbose=False)[0]
        model_px = sum(float((b[2] - b[0]) * (b[3] - b[1]))
                       for b, c in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist()) if int(c) == 0)
        s = lots.setdefault(d["lot_number"], {"lab": d["lab_me_pct"], "n": 0, "hand": 0.0, "model": 0.0, "prod": 0})
        s["n"] += 1
        s["hand"] += hand
        s["model"] += model_px
        s["prod"] += d["product_px"]

    out = RUNS / args.name / "val_lots.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lot", "tiles", "hand_ratio_pct", "model_ratio_pct", "lab_me_pct"])
        for lot, s in sorted(lots.items()):
            hr = 100 * s["hand"] / s["prod"] if s["prod"] else 0
            mr = 100 * s["model"] / s["prod"] if s["prod"] else 0
            w.writerow([lot, s["n"], f"{hr:.2f}", f"{mr:.2f}", s["lab"]])
            print(f"{lot}  {s['n']:4d} tiles  hand {hr:6.2f}%  model {mr:6.2f}%  lab {s['lab']:5.2f}")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
