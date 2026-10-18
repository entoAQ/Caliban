#!/usr/bin/env python3
"""Estimate lots' ME% from all their inline photos with the trained model.

For every lot with a lab ME% (and any lot named with --lots), takes every
inline photo in the lot's window (bursts included, same window as
pull_amas.py), runs the detector tile by tile on the measured crop and
pools sum(MEO box area) / sum(product px) over the lot.

The scale from that ratio to lab ME% (lab = k x ratio, the density factor)
is fitted on the lots the model trained on; the other lots -- the val lots
and lots never labelled at all -- are estimated blind with it: an ME% with
an 80% range from a bootstrap over the lot's readings, and the band.

Photos are cached in INLINE_LABELING_DIR/amas/all/ (Supabase keeps them only a
few days); --download-only fetches them without running the model.

Usage:
    python lot_estimate.py --download-only
    python lot_estimate.py --name meo
"""

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT, http  # noqa: E402
from pull_amas import BUCKET, CHUTE_MOUNTED, LAG, WINDOW, ts  # noqa: E402
from tile_amas import COLS, CROP_LEFT, CROP_TOP, ROWS  # noqa: E402

CACHE = ROOT / "amas" / "all"
BANDS = [(3, "<3%"), (5, "3-5%"), (8, "5-8%"), (10, "8-10%"), (999, ">10%")]


def band(v):
    return next(name for top, name in BANDS if v < top)


def fetch_lots(url, auth):
    def rest(table, params):
        rows, offset = [], 0
        while True:
            page = http("GET", f"{url}/rest/v1/{table}?{urllib.parse.urlencode(params)}",
                        {**auth, "Range": f"{offset}-{offset + 999}"})
            rows += page
            if len(page) < 1000:
                return rows
            offset += 1000
    tests = {t["code"]: t["id"] for t in rest("test_definitions", [
        ("select", "id,code"), ("code", "in.(me_organic_wt,bulk_density)")])}
    lots = rest("lots", [("select", "id,lot_number,packaged_at"), ("legacy_import", "is.false"),
                         ("packaged_at", f"gte.{CHUTE_MOUNTED}"), ("order", "packaged_at,lot_number")])
    results = rest("test_results", [
        ("select", "lot_id,test_id,result_value"), ("is_superseded", "is.false"),
        ("test_id", f"in.({tests['me_organic_wt']},{tests['bulk_density']})"),
        ("lot_id", "in.(%s)" % ",".join(str(l["id"]) for l in lots)), ("order", "created_at.desc")])
    latest = {}
    for r in results:
        latest.setdefault((r["lot_id"], r["test_id"]), r["result_value"])
    rows = rest("vision_band_estimates", [
        ("select", "lot_number_text,created_at,storage_path,mesures"),
        ("source", "eq.inline"), ("excluded_reason", "is.null"), ("storage_path", "not.is.null"),
        ("operator_instruction", "not.is.null"), ("created_at", f"gte.{CHUTE_MOUNTED}"),
        ("order", "created_at")])
    out, prev = [], None
    for lot in lots:
        close = ts(lot["packaged_at"])
        start, prev = max(prev or close - WINDOW, close - WINDOW) - LAG, close
        wt = latest.get((lot["id"], tests["me_organic_wt"]))
        den = latest.get((lot["id"], tests["bulk_density"]))
        readings = [(r["lot_number_text"], (r.get("mesures") or {}).get("groupe") or [r["storage_path"]])
                    for r in rows if start < ts(r["created_at"]) <= close - LAG]
        out.append({"lot": lot["lot_number"], "lab": round(100 * wt / den, 2) if wt is not None and den else None,
                    "readings": readings})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="meo")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    lots = fetch_lots(url, auth)
    CACHE.mkdir(parents=True, exist_ok=True)
    got = gone = 0
    for lot in lots:
        for _, paths in lot["readings"]:
            for p in paths:
                dest = CACHE / Path(p).name
                if dest.exists():
                    continue
                try:
                    dest.write_bytes(http("GET", f"{url}/storage/v1/object/{BUCKET}/{p}", auth, raw=True))
                    got += 1
                except urllib.error.HTTPError:
                    gone += 1
    print(f"photos: {got} downloaded, {gone} no longer in Storage")
    if args.download_only:
        return

    import numpy as np
    from PIL import Image
    from ultralytics import YOLO
    model = YOLO(str(ROOT / "amas_runs" / args.name / "weights" / "best.pt"))
    data = ROOT / "amas_yolo"
    trained = {json.loads((ROOT / "amas" / "tiles" / "tiles.json").read_text(encoding="utf-8"))[f.name]["lot_number"]
               for f in (data / "images" / "train").glob("*.jpg")}

    def photo(path):
        a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        h, w = a.shape[:2]
        crop = a[int(h * CROP_TOP):, int(w * CROP_LEFT):]
        mx, mn = crop.max(axis=2), crop.min(axis=2)
        prod = int((((mx - mn) / np.maximum(mx, 1) > 0.28) & (crop[..., 0] > crop[..., 2] + 15) & (mx > 50)).sum())
        ch, cw = crop.shape[:2]
        tiles = [crop[r * ch // ROWS:(r + 1) * ch // ROWS, c * cw // COLS:(c + 1) * cw // COLS][..., ::-1].astype(np.uint8)
                 for r in range(ROWS) for c in range(COLS)]
        meo = 0.0
        for res in model.predict([np.ascontiguousarray(t) for t in tiles], imgsz=416, conf=args.conf,
                                 device="cpu", verbose=False):
            meo += sum(float((b[2] - b[0]) * (b[3] - b[1]))
                       for b, c in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist()) if int(c) == 0)
        return meo, prod

    for lot in lots:
        per_reading = []
        for _, paths in lot["readings"]:
            ms = [photo(CACHE / Path(p).name) for p in paths if (CACHE / Path(p).name).exists()]
            if ms:
                per_reading.append((sum(m for m, _ in ms), sum(p for _, p in ms)))
        lot["per_reading"] = per_reading
        lot["ratio"] = (100 * sum(m for m, _ in per_reading) / max(1, sum(p for _, p in per_reading))
                        if per_reading else None)
        print(f"  {lot['lot']}: {len(per_reading)} readings read", flush=True)

    cal = [l for l in lots if l["lot"] in trained and l["lab"] is not None and l["ratio"]]
    k = sum(l["lab"] * l["ratio"] for l in cal) / sum(l["ratio"] ** 2 for l in cal)
    print(f"\nscale fitted on {len(cal)} training lots: lab = {k:.2f} x ratio\n")
    random.seed(3)
    print(f"{'lot':15s} {'used':10s} readings  estimate  80% range     band     lab    lab band")
    for l in lots:
        if not l["ratio"]:
            continue
        pr = l["per_reading"]
        bs = sorted(k * 100 * sum(m for m, _ in s) / max(1, sum(p for _, p in s))
                    for s in (random.choices(pr, k=len(pr)) for _ in range(1000)))
        est = k * l["ratio"]
        used = "training" if l["lot"] in trained else "BLIND"
        lab = f"{l['lab']:5.2f}   {band(l['lab'])}" if l["lab"] is not None else "  -"
        print(f"{l['lot']:15s} {used:10s} {len(pr):5d}   {est:6.2f}   {bs[100]:5.2f}-{bs[900]:5.2f}   "
              f"{band(est):7s} {lab}")


if __name__ == "__main__":
    main()
