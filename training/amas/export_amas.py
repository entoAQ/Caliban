#!/usr/bin/env python3
"""Export the MEO boxes: a YOLO detection set and the per-lot check.

Works tile by tile: every submitted tile not ticked Inutilisable counts,
whether or not the rest of its photo is done. Writes to
INLINE_LABELING_DIR/amas_yolo/:

    images/{train,val}/, labels/{train,val}/, data.yaml
        the tiles, classes 0 MEO, 1 Peau, 2 Incertain. Split by lot (every
        4th lot in packaging order to val), so val tiles never share a lot
        with training ones.
    lots.csv
        per lot: tiles labelled, MEO pieces, sum(MEO box area) / sum(product
        area) in %, lab ME%. This is the test of the whole idea before any
        model is trained: if hand-boxed MEO does not follow the lab, a model
        copying it will not either.

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
from sync_amas import AMAS, EXCLUDED_LOTS, FIRST_LOT, find_project  # noqa: E402

CLASSES = ["MEO", "Peau", "Incertain"]
TILES = AMAS / "tiles"
OUT = ROOT / "amas_yolo"


def main():
    pid = find_project()
    if pid is None:
        raise SystemExit("No MEO project: run sync_amas.py first.")
    index = json.loads((TILES / "tiles.json").read_text(encoding="utf-8"))
    tasks = ls("GET", f"/api/projects/{pid}/export", exportType="JSON")

    first = {}
    for d in (d for d in index.values() if d["lot_number"] >= FIRST_LOT):
        first[d["lot_number"]] = min(first.get(d["lot_number"], d["taken_at"]), d["taken_at"])
    order = sorted(first, key=first.get)
    val_lots = set(order[3::4])
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (OUT / sub).mkdir(parents=True)

    per_lot = {}
    for t in tasks:
        anns = [a for a in t.get("annotations", []) if not a.get("was_cancelled")]
        name = t["data"].get("tile")
        if not anns or name not in index:
            continue
        res = anns[-1]["result"]
        if any("Inutilisable" in r["value"].get("choices", []) for r in res):
            continue
        d = index[name]
        # Lots before FIRST_LOT: their boxes are as good as any for training,
        # only their lot assignment is not trusted -- so train, never val,
        # and left out of the per-lot check.
        early = d["lot_number"] < FIRST_LOT or d.get("lab_me_pct") is None
        if d["lot_number"] in EXCLUDED_LOTS:
            continue
        boxes = [r for r in res if r["type"] == "rectanglelabels"]
        split = "val" if d["lot_number"] in val_lots and not early else "train"
        shutil.copy2(TILES / name, OUT / "images" / split / name)
        lines, meo_px, n_meo = [], 0.0, 0
        for r in boxes:
            v = r["value"]
            cls = CLASSES.index(v["rectanglelabels"][0])
            w, h = v["width"] / 100, v["height"] / 100
            lines.append(f"{cls} {v['x'] / 100 + w / 2:.6f} {v['y'] / 100 + h / 2:.6f} {w:.6f} {h:.6f}")
            if cls == 0:
                n_meo += 1
                meo_px += w * h * d["width"] * d["height"]
        (OUT / "labels" / split / (Path(name).stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")

        if early:
            continue
        s = per_lot.setdefault(d["lot_number"], {"lab": d["lab_me_pct"], "n": 0, "meo": 0, "area": 0.0, "prod": 0})
        s["n"] += 1
        s["meo"] += n_meo
        s["area"] += meo_px
        s["prod"] += d["product_px"]

    (OUT / "data.yaml").write_text(
        f"path: {OUT.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n"
        + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES)), encoding="utf-8")
    with open(OUT / "lots.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lot", "tiles", "meo", "meo_area_over_product_pct", "lab_me_pct", "split"])
        for lot in order:
            if lot in per_lot:
                s = per_lot[lot]
                ratio = 100 * s["area"] / s["prod"] if s["prod"] else 0
                w.writerow([lot, s["n"], s["meo"], f"{ratio:.2f}", s["lab"], "val" if lot in val_lots else "train"])
                print(f"{lot}  {s['n']:4d} tiles  {s['meo']:4d} MEO  ratio {ratio:6.2f}%  lab {s['lab']:5.2f}")
    print(f"Written to {OUT}")


if __name__ == "__main__":
    main()
