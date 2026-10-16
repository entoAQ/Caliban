#!/usr/bin/env python3
"""Export the labeled inline photos as a YOLO-segmentation dataset.

Writes the layout training/augment_rotations.py reads (and a YOLO-seg
trainer after it):

    <out>/images/<reading>.jpg
    <out>/labels/<reading>.txt     "class_id x1 y1 x2 y2 ..." normalized 0-1
    <out>/classes.txt              one label per line, line number = class id

Class ids follow label_config.xml's label order (Larves = 0, Frass = 1).
A photo is exported once it has a submitted annotation; its most recent one
is used. Photos ticked "Inutilisable" and skipped ones are left out.

Usage:
    python export_yolo_seg.py --out dataset/
"""

import argparse
import re
import shutil
from pathlib import Path

from common import IMAGES, find_project, ls

HERE = Path(__file__).parent


def classes():
    xml = (HERE / "label_config.xml").read_text(encoding="utf-8")
    block = xml.split("<PolygonLabels", 1)[1].split("</PolygonLabels>", 1)[0]
    return re.findall(r'<Label value="([^"]+)"', block)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    names = classes()
    class_id = {n: i for i, n in enumerate(names)}
    pid = find_project()
    if pid is None:
        raise SystemExit("No inline project in Label Studio yet: run sync_project.py.")
    tasks = ls("GET", f"/api/projects/{pid}/export", exportType="JSON", download_all_tasks="true")

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    (args.out / "labels").mkdir(parents=True, exist_ok=True)
    (args.out / "classes.txt").write_text("\n".join(names) + "\n", encoding="utf-8")

    written = unusable = unlabeled = 0
    for task in tasks:
        done = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        if not done:
            unlabeled += 1
            continue
        result = max(done, key=lambda a: a["updated_at"])["result"]
        if any(r["type"] == "choices" and "Inutilisable" in r["value"]["choices"] for r in result):
            unusable += 1
            continue

        lines = []
        for r in result:
            if r["type"] != "polygonlabels":
                continue
            points = r["value"]["points"]
            if len(points) < 3:
                continue  # not an area
            coords = " ".join(f"{x / 100:.6f} {y / 100:.6f}" for x, y in points)
            lines.append(f"{class_id[r['value']['polygonlabels'][0]]} {coords}")

        rid = task["data"]["reading_id"]
        shutil.copy2(IMAGES / f"{rid}.jpg", args.out / "images" / f"{rid}.jpg")
        # An empty label file is a valid "nothing here" example, kept as such.
        (args.out / "labels" / f"{rid}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        written += 1

    print(f"{written} photos exported to {args.out} "
          f"({unusable} unusable, {unlabeled} not labeled yet)")


if __name__ == "__main__":
    main()
