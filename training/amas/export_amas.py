#!/usr/bin/env python3
"""Export the clump boxes: a YOLO detection set and the per-lot check.

Writes to INLINE_LABELING_DIR/amas_yolo/:

    images/{train,val}/, labels/{train,val}/, data.yaml
        classes 0 MEO, 1 Peau, 2 Incertain. Split by lot (every 4th lot
        in packaging order to val), so val photos never share a lot with
        training ones.
    lots.csv
        per lot: photos labelled, clumps, sum(clump box area) / sum(product
        area) in %, lab ME%. This is the test of the whole idea before any
        model is trained: if hand-boxed clumps do not follow the lab, a model
        copying them will not either.

Only submitted photos not ticked Inutilisable are used.

Usage:
    python export_amas.py
"""

import csv
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT, ls  # noqa: E402
from sync_amas import AMAS, find_project  # noqa: E402

CLASSES = ["MEO", "Peau", "Incertain"]
OUT = ROOT / "amas_yolo"


def main():
    pid = find_project()
    if pid is None:
        raise SystemExit("No MEO project: run sync_amas.py first.")
    index = json.loads((AMAS / "amas_photos.json").read_text(encoding="utf-8"))
    tasks = ls("GET", f"/api/projects/{pid}/export", exportType="JSON")

    order = sorted({d["lot_number"] for d in index.values()},
                   key=lambda n: min(d["taken_at"] for d in index.values() if d["lot_number"] == n))
    val_lots = set(order[3::4])
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (OUT / sub).mkdir(parents=True)

    per_lot = {}
    for t in tasks:
        anns = [a for a in t.get("annotations", []) if not a.get("was_cancelled")]
        name = t["data"].get("photo")
        if not anns or name not in index:
            continue
        res = anns[-1]["result"]
        if any("Inutilisable" in r["value"].get("choices", []) for r in res):
            continue
        d = index[name]
        boxes = [r for r in res if r["type"] == "rectanglelabels"]
        split = "val" if d["lot_number"] in val_lots else "train"
        shutil.copy2(AMAS / name, OUT / "images" / split / name)
        lines, amas_area = [], 0.0
        for r in boxes:
            v = r["value"]
            cls = CLASSES.index(v["rectanglelabels"][0])
            w, h = v["width"] / 100, v["height"] / 100
            lines.append(f"{cls} {v['x'] / 100 + w / 2:.6f} {v['y'] / 100 + h / 2:.6f} {w:.6f} {h:.6f}")
            if cls == 0:
                amas_area += w * h
        (OUT / "labels" / split / (Path(name).stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")

        s = per_lot.setdefault(d["lot_number"], {"lab": d["lab_me_pct"], "n": 0, "amas": 0, "area": 0.0, "prod": 0.0})
        s["n"] += 1
        s["amas"] += sum(1 for r in boxes if r["value"]["rectanglelabels"][0] == "MEO")
        s["area"] += amas_area
        # product_share is measured on the prompts' crop (0.78 x 0.90 of the frame)
        s["prod"] += d["product_share"] * 0.78 * 0.90

    (OUT / "data.yaml").write_text(
        f"path: {OUT.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n"
        + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES)), encoding="utf-8")
    with open(OUT / "lots.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lot", "photos", "meo", "meo_area_over_product_pct", "lab_me_pct", "split"])
        for lot in order:
            if lot in per_lot:
                s = per_lot[lot]
                ratio = 100 * s["area"] / s["prod"] if s["prod"] else 0
                w.writerow([lot, s["n"], s["amas"], f"{ratio:.2f}", s["lab"], "val" if lot in val_lots else "train"])
                print(f"{lot}  {s['n']:3d} photos  {s["amas"]:4d} MEO  ratio {ratio:6.2f}%  lab {s['lab']:5.2f}")
    print(f"Written to {OUT}")


if __name__ == "__main__":
    main()
