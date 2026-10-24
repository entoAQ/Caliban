#!/usr/bin/env python3
"""Create the clump-box project in Label Studio and add the tiles.

Run with Label Studio started (training/labelstudio/start.ps1) and after
pull_amas.py and tile_amas.py. Safe to rerun: the project and its storage are made once,
instructions and labels are kept in step with this folder, and only tiles
not already a task are imported.

Tasks are the tiles made by tile_amas.py, carrying tile, photo, reading
and taken_at only. The lot, its lab ME% and
the flow stay in amas_photos.json, so nothing on screen hints at how dirty
the lot was.

Usage:
    python sync_amas.py
"""

import json
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT, ls  # noqa: E402
from tile_amas import COLS, CROP_LEFT, CROP_TOP, ROWS  # noqa: E402

import cv2  # noqa: E402

AMAS = ROOT / "amas"
# 10-13 was a short test day under different conditions: its photos'
# labels still train the model, but its lots are not used for calibration
# or the lot checks, and its unlabelled tiles are left out of the queue.
FIRST_LOT = "20261016"
# Ariel's box rebuilt (2026-10-24): camera turned, aperture closed, flash
# 1/32 and gain 2.0 -- crisper, darker photos with harder shadows. Photos
# from here on are what the model has to read now, so pull_amas.py takes
# them by default and reorder_amas.py puts them first in the queue. The
# older labels still train the model.
CURRENT_LOOK = "2026-10-24T18:40:00Z"
# Lots never to be compared with the lab or labelled (Tim, 2026-10-18).
EXCLUDED_LOTS = {"20261018-BB04"}
# Lots whose lab result is kept but does not match their photos (Tim,
# 2026-10-19: 10-17 BB03 read ~10% against a lab 5.33 with every model):
# never used to fit the scale or scored against the lab. Their boxes still
# train the model -- a box is right or wrong whatever the lot's lab says.
NO_FIT_LOTS = {"20261017-BB03"}
PROJECT_TITLE = "Chute en ligne - MEO"

INSTRUCTIONS = """<p>Each task is one tile, a sixth of a photo. Draw a box around every piece of <b>MEO</b> in the tile, and nothing else. A piece cut by the tile edge: box the part you can see.</p>
<ol>
<li><b>MEO</b> (1): any piece that is not a larva and that you could pick up between two fingers: frass clumps, seeds, other organic pieces. Frass has an <b>irregular, crumbly</b> edge; a larva has a <b>smooth</b> edge, gloss or segment lines.</li>
<li>The box is a plain rectangle, not the outline: drag it as tight as you can around the piece, as little chute or larva inside as possible. Pieces touching each other: one box each if you can tell them apart, one box around both if not.</li>
<li>Skip anything you could not pick up between two fingers: dust, crumbs, specks. They are not MEO.</li>
<li><b>Peau</b> (2): pale papery shed skin. Not MEO, boxed so the model learns to tell it apart.</li>
<li><b>Incertain</b> (3): you cannot tell MEO from larva (half hidden, blurred). Box it rather than guess.</li>
<li><b>Prépupe</b> (4): a dark prepupa, whole or only partly visible (cut off by a larva falling across it, or by the tile edge). Not MEO: boxed, the visible part only, so the model learns that dark is not enough. A pre-filled MEO box on a prepupa: switch it to Prépupe rather than deleting it.</li>
<li>Pale larvae, chute and shadows: nothing. A tile with no MEO is submitted with no box.</li>
</ol>
<p>Zoom in: grey-brown on a grey chute is easy to miss. Go over the whole tile.</p>
<p>Unusable tile (blurred, flash missed): tick <i>Inutilisable</i> and submit, rather than skip.</p>"""


def find_project():
    resp = ls("GET", "/api/projects", page_size=100)
    for p in resp.get("results", resp) if isinstance(resp, dict) else resp:
        if p["title"] in (PROJECT_TITLE, "Chute en ligne - Amas"):   # its first name
            return p["id"]
    return None


def to_tiles(photo, result):
    """A whole photo's labels as its tiles' results, boxes cut at tile edges
    as if drawn on the tile; slivers under 3 px dropped. Every tile gets a
    result, empty ones included: the photo was gone over in full."""
    img = cv2.imread(str(AMAS / photo))
    H, W = img.shape[:2]
    y0, x0 = int(H * CROP_TOP), int(W * CROP_LEFT)
    ch, cw = H - y0, W - x0
    unusable = any("Inutilisable" in r["value"].get("choices", []) for r in result)
    out = {}
    for r in range(ROWS):
        for c in range(COLS):
            ty0, ty1 = y0 + r * ch // ROWS, y0 + (r + 1) * ch // ROWS
            tx0, tx1 = x0 + c * cw // COLS, x0 + (c + 1) * cw // COLS
            tw, th = tx1 - tx0, ty1 - ty0
            res = [{"from_name": "usable", "to_name": "image", "type": "choices",
                    "value": {"choices": ["Inutilisable"]}}] if unusable else []
            for b in result:
                v = b["value"]
                if "rectanglelabels" not in v:
                    continue
                bx0, by0 = v["x"] / 100 * W, v["y"] / 100 * H
                bx1, by1 = bx0 + v["width"] / 100 * W, by0 + v["height"] / 100 * H
                cx0, cy0, cx1, cy1 = max(bx0, tx0), max(by0, ty0), min(bx1, tx1), min(by1, ty1)
                if cx1 - cx0 < 3 or cy1 - cy0 < 3:
                    continue
                res.append({"from_name": "box", "to_name": "image", "type": "rectanglelabels",
                            "original_width": tw, "original_height": th, "image_rotation": 0,
                            "value": {"x": 100 * (cx0 - tx0) / tw, "y": 100 * (cy0 - ty0) / th,
                                      "width": 100 * (cx1 - cx0) / tw, "height": 100 * (cy1 - cy0) / th,
                                      "rotation": 0, "rectanglelabels": v["rectanglelabels"]}})
            out[f"{Path(photo).stem}_r{r + 1}c{c + 1}.jpg"] = res
    return out


def main():
    config = (HERE / "label_config.xml").read_text(encoding="utf-8")
    pid = find_project()
    if pid is None:
        pid = ls("POST", "/api/projects", {
            "title": PROJECT_TITLE,
            "description": "MEO (frass, graines...) sur la chute, pour mesurer le ME% par lot.",
            "label_config": config, "expert_instruction": INSTRUCTIONS, "show_instruction": True,
        })["id"]
        print(f"Created project {pid}")
        # Never synced, only connected: it is what lets Label Studio serve
        # /data/local-files/ paths under amas/.
        ls("POST", "/api/storages/localfiles", {
            "project": pid, "title": "Photos amas", "path": str(AMAS), "use_blob_urls": True,
        })
    else:
        ls("PATCH", f"/api/projects/{pid}", {
            "title": PROJECT_TITLE, "expert_instruction": INSTRUCTIONS, "show_instruction": True, "label_config": config,
        })

    have, page, whole, tasks_all = set(), 1, [], []
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=100, fields="all")
        tasks = resp["tasks"] if isinstance(resp, dict) else resp
        have.update(t["data"].get("tile") for t in tasks)
        whole += [t for t in tasks if not t["data"].get("tile")]
        tasks_all += tasks
        if len(tasks) < 100:
            break
        page += 1
    tiles_index = json.loads((AMAS / "tiles" / "tiles.json").read_text(encoding="utf-8"))
    early_tiles = {n for n, d in tiles_index.items()
                   if d["lot_number"] < FIRST_LOT and not d.get("training_only")}

    # Whole-photo tasks from before the tiles: their boxes are carried over
    # to the photo's tiles, then the whole-photo tasks are removed.
    carried = {}
    if whole:
        done = [t for t in ls("GET", f"/api/projects/{pid}/export", exportType="JSON")
                if not t["data"].get("tile") and any(not a.get("was_cancelled") for a in t["annotations"])]
        for t in done:
            ann = [a for a in t["annotations"] if not a.get("was_cancelled")][-1]
            carried.update(to_tiles(t["data"]["photo"], ann["result"]))
        ls("DELETE", f"/api/projects/{pid}/tasks")
        have = set()
        print(f"Removed {len(whole)} whole-photo tasks, boxes of {len(done)} carried over to their tiles")

    # Unlabelled tiles of lots before FIRST_LOT come out of the queue;
    # labelled ones stay (export skips them).
    early = [t["id"] for t in tasks_all if t["data"].get("tile") and t["data"]["tile"] in early_tiles
             and not (t.get("total_annotations") or t.get("draft_exists") or t.get("cancelled_annotations"))]
    if early:
        ls("POST", "/api/dm/actions", {"selectedItems": {"all": False, "included": early}},
           id="delete_tasks", project=pid)
        have -= {t["data"]["tile"] for t in tasks_all if t["id"] in set(early)}
        print(f"Removed {len(early)} unlabelled tiles of lots before {FIRST_LOT}")

    index = json.loads((AMAS / "tiles" / "tiles.json").read_text(encoding="utf-8"))
    # Photos shuffled by name hash so labelling order mixes lots and flows;
    # a photo's tiles stay together, in reading order.
    order = sorted(index.values(), key=lambda d: (zlib.crc32(d["photo"].encode()), d["row"], d["col"]))
    new = [{"data": {"image": f"/data/local-files/?d=amas/tiles/{d['tile']}", "tile": d["tile"],
                     "photo": d["photo"], "reading_id": d["reading_id"], "taken_at": d["taken_at"]}}
           for d in order if d["tile"] not in have and (AMAS / "tiles" / d["tile"]).exists()
           and (d["lot_number"] >= FIRST_LOT or d.get("training_only"))]
    for task in new:
        if task["data"]["tile"] in carried:
            task["annotations"] = [{"result": carried[task["data"]["tile"]]}]
    for i in range(0, len(new), 500):
        ls("POST", f"/api/projects/{pid}/import", new[i:i + 500])
    print(f"Project {pid}: {len(new)} tiles added, {len(have) + len(new)} in total")

if __name__ == "__main__":
    main()
