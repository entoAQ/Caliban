#!/usr/bin/env python3
"""How well the inline photos predict a lot's bulk density, by larva length.

Why: v_lot_density_estimate (sgsc-app) is the lot's weight / a 14-day median
bag volume. With every lot filled to the same 360 kg the weight no longer says
anything, so the view gives every lot the same number.

The measure: meo_detect.larva_length(), the code Ariel runs -- on each photo,
the isolated larvae (a blob of product that is solid and elongated, so not two
touching) and the 75th percentile of their lengths, in pixels of the 1332 px
photo; a lot's value is the median over its probe window (the last 45 minutes, the part the lab's sample comes
from). Longer larvae, lower density, as bigger larvae nest badly. On 74 lots
(2026-10-30) it was the best of 34 photo measures on 13 of 14 held-out days,
and held within a day and with flow and brightness taken out.

Scored against real measurements only. Weight estimates saved through the SGSC
form look like ordinary rows, without a note: est_density is rounded to one
decimal, so only values with two decimals are taken as measured. That also
drops the few measured values that happen to be typed with fewer.

Each held-out day is predicted by a straight line fitted on the other days,
each camera look (pull_amas.REFOCUSED, sync_amas.CURRENT_LOOK) with its own
offset, against the plain mean of the other days. Printed only: nothing here
changes SGSC or Ariel.

Photos come from lot_estimate.py --download-only; lengths are cached in
INLINE_LABELING_DIR/amas/larva_lengths.json.

Usage:
    python density_study.py
"""

import json
import os
import statistics
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
sys.path.insert(0, str(HERE.parents[1] / "rig" / "inline"))
import meo_detect  # noqa: E402
from common import ROOT, http  # noqa: E402
from lot_estimate import CACHE, fetch_lots  # noqa: E402
from meo_detect import CROP_LEFT, CROP_TOP, product_mask  # noqa: E402
from pull_amas import CHUTE_MOUNTED, REFOCUSED, ts  # noqa: E402
from sync_amas import CURRENT_LOOK  # noqa: E402

LENGTHS = ROOT / "amas" / "larva_lengths.json"
MIN_PHOTOS = 5      # in a lot's probe window
NEAR_FILL = (330, 370)  # lots already filled about as the 360 kg lots will be
BANDS = [170, 200, 220]  # v_lot_density_estimate's


def larva_length(path):
    """Ariel's number for a photo (meo_detect.larva_length), or None."""
    a = np.asarray(Image.open(path).convert("RGB"))
    h, w = a.shape[:2]
    return meo_detect.larva_length(product_mask(a[int(h * CROP_TOP):, int(w * CROP_LEFT):]))["len_p75"]


def real_densities(url, auth):
    """{lot_number: (measured density g/L, weight kg)} since the chute was mounted."""
    def rest(table, params):
        rows, offset = [], 0
        while True:
            page = http("GET", f"{url}/rest/v1/{table}?{urllib.parse.urlencode(params)}",
                        {**auth, "Range": f"{offset}-{offset + 999}"})
            rows += page
            if len(page) < 1000:
                return rows
            offset += 1000
    test = rest("test_definitions", [("select", "id"), ("code", "eq.bulk_density")])[0]["id"]
    lots = {l["id"]: l for l in rest("lots", [("select", "id,lot_number,actual_weight"), ("legacy_import", "is.false"),
                                              ("packaged_at", f"gte.{CHUTE_MOUNTED}")])}
    out = {}
    for r in rest("test_results", [("select", "lot_id,result_value,notes"), ("test_id", f"eq.{test}"),
                                   ("is_superseded", "is.false"), ("created_at", f"gte.{CHUTE_MOUNTED}"),
                                   ("order", "created_at")]):
        v = r["result_value"]
        note = (r.get("notes") or "").strip().lower()
        if r["lot_id"] in lots and v and 50 < v < 500 and round(v, 1) != v and not note.startswith("estimation"):
            lot = lots[r["lot_id"]]
            out[lot["lot_number"]] = (v, lot["actual_weight"])
    return out


def look(when):
    return 0 if when < ts(REFOCUSED) else 1 if when < ts(CURRENT_LOOK) else 2


def main():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    real = real_densities(url, auth)

    cached = json.loads(LENGTHS.read_text(encoding="utf-8")) if LENGTHS.exists() else {}
    rows = []
    for lot in fetch_lots(url, auth):
        if lot["lot"] not in real:
            continue
        names = [Path(p).name for _, g in lot["readings"] for p in g]
        for name in names:
            if name not in cached and (CACHE / name).exists():
                cached[name] = larva_length(CACHE / name)
        lengths = [cached[n] for n in names if cached.get(n) is not None]
        if len(lengths) < MIN_PHOTOS:
            continue
        density, weight = real[lot["lot"]]
        # IL-YYYYMMDD-HHMMSS, in Ariel's local time, the same as this machine's.
        when = datetime.strptime(Path(names[-1]).stem[3:18], "%Y%m%d-%H%M%S").astimezone()
        rows.append({"lot": lot["lot"], "density": density, "weight": weight, "length": statistics.median(lengths),
                     "day": lot["lot"][:8], "look": look(when), "photos": len(lengths)})
    LENGTHS.write_text(json.dumps(cached), encoding="utf-8")

    y = np.array([r["density"] for r in rows])
    x = np.array([r["length"] for r in rows])
    days = np.array([r["day"] for r in rows])
    looks = np.array([r["look"] for r in rows])
    weights = np.array([r["weight"] or 0 for r in rows], float)

    def offsets(train):
        return np.array([x[train & (looks == k)].mean() if (train & (looks == k)).any() else x[train].mean()
                         for k in looks])

    pred, flat = np.zeros(len(y)), np.zeros(len(y))
    for d in set(days):
        test, train = days == d, days != d
        centred = x - offsets(train)
        pred[test] = np.polyval(np.polyfit(centred[train], y[train], 1), centred[test])
        flat[test] = y[train].mean()

    def report(name, m):
        if m.sum() < 3:
            return
        print(f"  {name:32s} {m.sum():3d} lots   miss {np.abs(pred - y)[m].mean():5.1f} g/L, "
              f"flat {np.abs(flat - y)[m].mean():5.1f}   r {np.corrcoef(pred[m], y[m])[0, 1]:+.2f}   "
              f"band {np.mean(np.digitize(pred[m], BANDS) == np.digitize(y[m], BANDS)):4.0%}, "
              f"flat {np.mean(np.digitize(flat[m], BANDS) == np.digitize(y[m], BANDS)):4.0%}")

    print(f"{len(rows)} lots with a measured density and {MIN_PHOTOS}+ photos, {len(set(days))} days; "
          f"density sd {y.std():.1f} g/L. Held-out day, one straight line:")
    report("all", np.ones(len(y), bool))
    report(f"weight {NEAR_FILL[0]}-{NEAR_FILL[1]} kg", (weights >= NEAR_FILL[0]) & (weights <= NEAR_FILL[1]))
    for k, name in enumerate(("before the refocus", "refocused", "rebuilt box")):
        report(name, looks == k)

    every = np.ones(len(y), bool)
    slope, _ = np.polyfit(x - offsets(every), y, 1)
    now = looks == looks.max()
    print(f"\n  slope {slope:.2f} g/L per px, all looks. On the current look, {now.sum()} lots: "
          f"density = {y[now].mean():.1f} {slope:+.2f} x (length - {x[now].mean():.1f})")
    print(f"  lots at {NEAR_FILL[0]}-{NEAR_FILL[1]} kg: weight says nothing, density still spans "
          f"{y[(weights >= NEAR_FILL[0]) & (weights <= NEAR_FILL[1])].min():.0f}-"
          f"{y[(weights >= NEAR_FILL[0]) & (weights <= NEAR_FILL[1])].max():.0f} g/L")


if __name__ == "__main__":
    main()
