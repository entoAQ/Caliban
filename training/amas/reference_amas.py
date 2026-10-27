#!/usr/bin/env python3
"""The surface-area reference set: inline photos whose MEO is traced as
outlines, the yardstick any area measurement is scored against.

Not training. The photos are taken since the box rebuild and refocus
(sync_amas.CURRENT_LOOK), none of them already chosen for training, and
pull_amas.py never takes them afterwards; they live in their own folder
(INLINE_LABELING_DIR/amas_ref/) and their own Label Studio project, which
export_amas.py does not read. No pre-fill either: outlines drawn from the
model's boxes would carry the method being checked into its own yardstick.

Each photo is cut into the same 3 x 2 tiles of the measured crop as the
training tiles. A photo counts once all six tiles are submitted.

    python reference_amas.py pick              # choose 40 photos, cut the tiles
    python reference_amas.py pick --photos 60  # add to the set up to 60
    python reference_amas.py sync              # project + tasks in Label Studio
    python reference_amas.py score             # traced area against Ariel's model
    python reference_amas.py score --name meo-nodust-20261026

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY for pick, Label Studio running
(training/labelstudio/start.ps1) for sync and score.
"""

import argparse
import json
import os
import sys
import urllib.parse
import zlib
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
sys.path.insert(0, str(HERE.parents[1] / "rig" / "inline"))
from common import ROOT, http, ls  # noqa: E402
from pull_amas import INDEX, pick  # noqa: E402
from sync_amas import CURRENT_LOOK  # noqa: E402
from tile_amas import COLS, CROP_LEFT, CROP_TOP, ROWS  # noqa: E402

REF = ROOT / "amas_ref"
TILES = REF / "tiles"
REF_INDEX = REF / "reference.json"
PROJECT_TITLE = "Chute en ligne - MEO référence (contours)"

LABEL_CONFIG = """<View>
  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="false"/>
  <PolygonLabels name="outline" toName="image" strokeWidth="1" opacity="0.25" pointSize="small">
    <Label value="MEO" background="#ff7a00" hotkey="1"/>
    <Label value="Incertain" background="#9a9a9a" hotkey="2"/>
  </PolygonLabels>
  <Choices name="usable" toName="image" choice="single" showInline="true">
    <Choice value="Inutilisable"/>
  </Choices>
</View>"""

INSTRUCTIONS = """<p><b>Reference set, not training.</b> Each task is one tile, a sixth of a photo. Trace the outline of every piece of <b>MEO</b> in the tile, as closely as you can: these outlines are the measure every area reading is checked against.</p>
<ol>
<li><b>MEO</b> (1): the same pieces as in the box project -- anything not a larva that you could pick up between two fingers: frass clumps, seeds, other organic pieces. Crumbly edge, not the smooth edge of a larva.</li>
<li>Follow the piece's own edge: not its shadow on the chute, not the larva it touches. Where a larva lies over a piece, trace only the part you can see. Pieces touching each other: one outline each if you can tell them apart, one around both if not.</li>
<li>Skip crumbs and specks you could not pick up, and the dust built up on the chute (grainy layer that stays put): not MEO, not traced.</li>
<li><b>Incertain</b> (2): you cannot tell MEO from larva. Trace it rather than guess; it is counted apart.</li>
<li>Larvae, skins, dark larvae, chute and shadows: nothing. A tile with no MEO is submitted with nothing traced.</li>
</ol>
<p>Zoom in and go over the whole tile: a photo only counts once all six of its tiles are done.</p>
<p>Unusable tile (blurred, flash missed): tick <i>Inutilisable</i> and submit; the whole photo is then left out.</p>"""


def cmd_pick(args):
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    params = [("select", "id,lot_number_text,created_at,storage_path,mesures"), ("source", "eq.inline"),
              ("excluded_reason", "is.null"), ("storage_path", "not.is.null"),
              ("operator_instruction", "not.is.null"), ("created_at", f"gte.{CURRENT_LOOK}"), ("order", "created_at")]
    while True:
        page = http("GET", f"{url}/rest/v1/vision_band_estimates?{urllib.parse.urlencode(params)}",
                    {**auth, "Range": f"{offset}-{offset + 999}"})
        rows += page
        if len(page) < 1000:
            break
        offset += 1000

    training = set(json.loads(INDEX.read_text(encoding="utf-8"))) if INDEX.exists() else set()
    ref = json.loads(REF_INDEX.read_text(encoding="utf-8")) if REF_INDEX.exists() else {}
    want = args.photos - len(ref)
    if want <= 0:
        print(f"{len(ref)} photos in the reference set already")
        return
    # One photo per reading (a burst's photos are seconds apart and alike),
    # spread over the whole time since the rebuild, then over flow.
    photos = [(r["lot_number_text"], r["storage_path"], r["created_at"]) for r in rows
              if Path(r["storage_path"]).name not in training and Path(r["storage_path"]).name not in ref]
    REF.mkdir(parents=True, exist_ok=True)
    TILES.mkdir(parents=True, exist_ok=True)
    # pull_amas.pick() downloads into amas/; the chosen ones are moved here.
    measured, keep = pick(photos, want, url, auth)
    for share, name, reading, taken in keep:
        src = INDEX.parent / name
        dest = REF / name
        if not dest.exists():
            src.replace(dest)
        ref[name] = {"photo": name, "reading": reading, "taken_at": taken, "product_share": share}
        img = cv2.imread(str(dest))
        h, w = img.shape[:2]
        crop = img[int(h * CROP_TOP):, int(w * CROP_LEFT):]
        ch, cw = crop.shape[:2]
        for r in range(ROWS):
            for c in range(COLS):
                part = crop[r * ch // ROWS:(r + 1) * ch // ROWS, c * cw // COLS:(c + 1) * cw // COLS]
                cv2.imwrite(str(TILES / f"{dest.stem}_r{r + 1}c{c + 1}.jpg"), part, [cv2.IMWRITE_JPEG_QUALITY, 95])
    # Photos measured but not kept stay in amas/ unindexed, as pull_amas.py leaves them.
    REF_INDEX.write_text(json.dumps(ref, indent=1, ensure_ascii=False), encoding="utf-8")
    days = {d["photo"][3:11] for d in ref.values()}
    print(f"{len(keep)} photos added ({len(photos)} candidates, {len(measured)} measured), "
          f"{len(ref)} in the set over {len(days)} days, flow {min(d['product_share'] for d in ref.values()):.2f}"
          f"-{max(d['product_share'] for d in ref.values()):.2f}")


def find_project():
    resp = ls("GET", "/api/projects", page_size=100)
    for p in resp.get("results", resp) if isinstance(resp, dict) else resp:
        if p["title"] == PROJECT_TITLE:
            return p["id"]
    return None


def all_tasks(pid):
    tasks, page = [], 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=100, fields="all")
        batch = resp["tasks"] if isinstance(resp, dict) else resp
        tasks += batch
        if len(batch) < 100:
            return tasks
        page += 1


def cmd_sync(args):
    pid = find_project()
    if pid is None:
        pid = ls("POST", "/api/projects", {
            "title": PROJECT_TITLE,
            "description": "Contours des MEO sur des photos de la chute : la référence de surface, pas l'entraînement.",
            "label_config": LABEL_CONFIG, "expert_instruction": INSTRUCTIONS, "show_instruction": True,
        })["id"]
        print(f"Created project {pid}")
        # Only connected, never synced: lets Label Studio serve amas_ref/.
        ls("POST", "/api/storages/localfiles", {
            "project": pid, "title": "Photos référence", "path": str(REF), "use_blob_urls": True,
        })
    else:
        ls("PATCH", f"/api/projects/{pid}", {
            "title": PROJECT_TITLE, "expert_instruction": INSTRUCTIONS, "show_instruction": True,
            "label_config": LABEL_CONFIG,
        })
    have = {t["data"].get("tile") for t in all_tasks(pid)}
    ref = json.loads(REF_INDEX.read_text(encoding="utf-8"))
    # Photos mixed by name hash, a photo's tiles together in reading order.
    new = [{"data": {"image": f"/data/local-files/?d=amas_ref/tiles/{tile}", "tile": tile, "photo": d["photo"],
                     "taken_at": d["taken_at"]}}
           for d in sorted(ref.values(), key=lambda d: zlib.crc32(d["photo"].encode()))
           for tile in (f"{Path(d['photo']).stem}_r{r + 1}c{c + 1}.jpg" for r in range(ROWS) for c in range(COLS))
           if tile not in have and (TILES / tile).exists()]
    for i in range(0, len(new), 500):
        ls("POST", f"/api/projects/{pid}/import", new[i:i + 500])
    print(f"Project {pid}: {len(new)} tiles added, {len(have) + len(new)} in total")


def traced(result, h, w):
    """A tile's submitted result -> (MEO mask, Incertain mask, unusable)."""
    masks = {"MEO": np.zeros((h, w), np.uint8), "Incertain": np.zeros((h, w), np.uint8)}
    unusable = False
    for r in result:
        v = r["value"]
        if "Inutilisable" in v.get("choices", []):
            unusable = True
        label = (v.get("polygonlabels") or [None])[0]
        if label in masks and len(v.get("points") or []) >= 3:
            pts = np.array([[x / 100 * w, y / 100 * h] for x, y in v["points"]], np.float32)
            cv2.fillPoly(masks[label], [np.round(pts).astype(np.int32)], 1)
    return masks["MEO"].astype(bool), masks["Incertain"].astype(bool), unusable


def cmd_score(args):
    import meo_detect
    if args.name:
        meo_detect.use(ROOT / "amas_runs" / args.name / "ariel" / "meo.onnx")
    pid = find_project()
    if pid is None:
        raise SystemExit("No reference project yet: python reference_amas.py sync")
    ref = json.loads(REF_INDEX.read_text(encoding="utf-8"))
    tiles, people = {}, {}
    for t in ls("GET", f"/api/projects/{pid}/export", exportType="JSON"):
        anns = [a for a in t["annotations"] if not a.get("was_cancelled")]
        if anns:
            # The latest annotation counts; with two people's, the second is
            # kept for the agreement line.
            anns.sort(key=lambda a: a.get("updated_at") or a.get("created_at") or "")
            tiles[t["data"]["tile"]] = anns[-1]["result"]
            others = [a for a in anns if a.get("completed_by") != anns[-1].get("completed_by")]
            if others:
                people[t["data"]["tile"]] = others[-1]["result"]

    rows, agree = [], []
    for name, d in sorted(ref.items()):
        names = [f"{Path(name).stem}_r{r + 1}c{c + 1}.jpg" for r in range(ROWS) for c in range(COLS)]
        if not all(n in tiles for n in names):
            continue
        img = cv2.imread(str(REF / name))[..., ::-1]
        h, w = img.shape[:2]
        y0, x0 = int(h * CROP_TOP), int(w * CROP_LEFT)
        ch, cw = h - y0, w - x0
        meo, unsure, other = (np.zeros((ch, cw), bool) for _ in range(3))
        bad = False
        for k, n in enumerate(names):
            r, c = divmod(k, COLS)
            sl = (slice(r * ch // ROWS, (r + 1) * ch // ROWS), slice(c * cw // COLS, (c + 1) * cw // COLS))
            th, tw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
            m, u, unusable = traced(tiles[n], th, tw)
            meo[sl], unsure[sl], bad = m, u, bad or unusable
            if n in people:
                other[sl] = traced(people[n], th, tw)[0]
        if bad:
            continue
        out = meo_detect.measure(str(REF / name))
        crop = np.ascontiguousarray(img[y0:, x0:])
        product = meo_detect.product_mask(crop)
        # The measured MEO area, rebuilt from the boxes as measure() makes it.
        f = crop.astype(np.int16)
        boxes = np.zeros((ch, cw), bool)
        for bx0, by0, bx1, by1, _ in out["boxes"]:
            boxes[max(0, int(round(by0 * h)) - y0):int(round(by1 * h)) - y0,
                  max(0, int(round(bx0 * w)) - x0):int(round(bx1 * w)) - x0] = True
        warm = boxes & (f[..., 0] > f[..., 2] + meo_detect.AREA_WARM)
        rows.append({"photo": name, "flow": d["product_share"],
                     "true_px": int(meo.sum()), "unsure_px": int(unsure.sum()),
                     "true_product_px": int((product | meo).sum()),
                     "box_px": out["meo_px"], "area_px": out["area"]["meo_px"],
                     "area_product_px": out["area"]["product_px"],
                     "hit_px": int((warm & meo).sum()), "warm_px": int(warm.sum())})
        if any(n in people for n in names):
            agree.append((int(meo.sum()), int(other.sum()), int((meo & other).sum()), int((meo | other).sum())))

    print(f"{len(rows)} of {len(ref)} reference photos fully traced "
          f"({len(tiles)} of {len(ref) * ROWS * COLS} tiles submitted)")
    if not rows:
        return
    print(f"\n  photo                   flow   traced   boxes  box+pixels   (MEO px; area ME% traced / box+pixels)")
    for r in rows:
        print(f"  {r['photo']:22s} {r['flow']:5.2f} {r['true_px']:8d} {r['box_px']:7.0f} {r['area_px']:9d}"
              f"     {100 * r['true_px'] / max(1, r['true_product_px']):5.2f}% / "
              f"{100 * r['area_px'] / max(1, r['area_product_px']):5.2f}%")
    tot = {k: sum(r[k] for r in rows) for k in rows[0] if k not in ("photo", "flow")}
    true_pct = 100 * tot["true_px"] / tot["true_product_px"]
    area_pct = 100 * tot["area_px"] / tot["area_product_px"]
    print(f"\npooled area ME%: traced {true_pct:.2f}%, box+pixels {area_pct:.2f}% "
          f"({area_pct / true_pct:.2f} x); boxes alone are {tot['box_px'] / max(1, tot['true_px']):.2f} x the traced area")
    print(f"box+pixels finds {tot['hit_px'] / max(1, tot['true_px']):.0%} of the traced MEO pixels; "
          f"{tot['hit_px'] / max(1, tot['warm_px']):.0%} of what it counts is traced MEO")
    misses = sorted(abs(100 * r["area_px"] / max(1, r["area_product_px"]) - 100 * r["true_px"] / max(1, r["true_product_px"]))
                    for r in rows)
    print(f"per photo, box+pixels misses the traced area ME% by {np.median(misses):.2f} points (median), "
          f"{misses[int(.9 * (len(misses) - 1))]:.2f} at the 90th percentile")
    print(f"Incertain traced: {tot['unsure_px'] / max(1, tot['true_px']):.0%} of the MEO area, left out above")
    if agree:
        a = np.array(agree)
        print(f"two people on {len(a)} photos: their MEO area differs by {np.median(abs(a[:, 0] - a[:, 1]) / np.maximum(1, a[:, 0])):.0%}"
              f" (median), outlines overlap {a[:, 2].sum() / max(1, a[:, 3].sum()):.0%}")
    out = REF / f"score-{args.name or 'ariel'}.json"
    out.write_text(json.dumps({"model": args.name or "ariel", "rows": rows}, indent=1), encoding="utf-8")
    print(f"written to {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--photos", type=int, default=40, help="size of the set (photos already in it count)")
    sub.add_parser("sync")
    p = sub.add_parser("score")
    p.add_argument("--name", help="a training run in amas_runs/ instead of Ariel's model")
    args = ap.parse_args()
    {"pick": cmd_pick, "sync": cmd_sync, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
