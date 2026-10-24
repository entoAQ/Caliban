#!/usr/bin/env python3
"""Pick the inline photos to box clumps on, spread over the lab lots and flows.

Only what counts as ME is labelled: clumps you could pick up between two
fingers. They are sparse (a handful per photo, dirty lot or clean), so the
lot difference only shows up summed over many photos -- which is why the
photos are chosen per lab lot rather than at random: the boxes then give,
per lot, sum(clump area) / sum(product area), to set against the lab ME%.

For every lot with a lab ME%, takes the inline photos taken since --since
of its probe window (lot_windows(): the last 45 minutes of the bag, the part
the lab's sample comes from), burst photos included. From those it measures the product share of up to
3 x --per-lot evenly spaced photos (warm, saturated pixels, rig/inline/
flashcam.py product_share), drops empty-chute ones and keeps --per-lot spread
evenly from lightest to heaviest flow.

Photos go to INLINE_LABELING_DIR/amas/, the choice to amas_photos.json in
the same folder, which sync_amas.py turns into Label Studio tasks. Photos
already on disk are not downloaded again; Supabase keeps review copies only
a few days, so older lots may be short.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY, and numpy + opencv for the
product share.

Usage:
    python pull_amas.py                  # new lots since the box rebuild, 19 photos each
    python pull_amas.py --per-lot 25
    python pull_amas.py --training 50    # 50 photos since the box rebuild, no lab needed
"""

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "labelstudio"))
from common import ROOT, http  # noqa: E402

AMAS = ROOT / "amas"
INDEX = AMAS / "amas_photos.json"
BUCKET = "vision-band-test-captures"
CHUTE_MOUNTED = "2026-10-13T12:30:00Z"
# Lens refocused by hand (flashcam.py focus-live, crispness 27 -> 34). New
# photos are taken from here on only: the set already labelled is from
# before, and softer photos are not added to it.
REFOCUSED = "2026-10-18T14:20:00Z"
# Superseded as the default by the box rebuild (2026-10-24), when the photos
# changed look again; the refocus stays for --since by hand.
from sync_amas import CURRENT_LOOK  # noqa: E402
# Lot windows, from the bagging scale's trace (2026-10-19, 51 bags over
# 10-15..10-18): the operator confirms a lot about 2 minutes after its last
# top-up, and product reaches the scale 0-2 minutes after passing Ariel. So a
# bag holds what Ariel saw from the previous confirmation to its own, less LAG.
# The lab's probe only reaches ~70% into the bag (Tim), so the lab describes
# the end of the fill: against the lab, the last PROBE of the bag matched best
# (33 lots: 0.99-1.04 average miss, against 1.40 for the whole bag). Hence two
# windows -- the probe window for anything set against the lab (fitting the
# scale, checking a model, the scorecard), the whole bag for the lot's own
# estimate, which also covers what the probe never reaches.
LAG = timedelta(minutes=2)
PROBE = timedelta(minutes=45)
WHOLE_CAP = timedelta(hours=24)    # a first bag after a weekend: no further back
EMPTY = 0.02                       # product share below this: chute nearly empty


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def lot_windows(closes):
    """(whole_start, probe_start, end) for each confirmation time, in order:
    a reading at t is in the whole bag if whole_start < t <= end, in the probe
    window if probe_start < t <= end."""
    out, prev = [], None
    for close in closes:
        whole = max(prev or close - WHOLE_CAP, close - WHOLE_CAP)
        out.append((whole - LAG, max(whole, close - PROBE) - LAG, close - LAG))
        prev = close
    return out


def product_share(path):
    """Share of warm, saturated pixels on the prompts' crop (flashcam.py)."""
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]
    a = img[int(h * .22)::4, int(w * .10)::4].astype(np.float32)
    b, r = a[..., 0], a[..., 2]
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    return round(float(((sat > 0.28) & (r > b + 15) & (mx > 50)).mean()), 4)


def pick(photos, n, url, auth):
    """From (reading, path, taken_at) photos: measure the product share of up
    to 3 x n evenly spaced ones, drop empty-chute ones and keep n spread
    evenly from lightest to heaviest flow. Returns (measured, kept)."""
    n_cand = min(len(photos), 3 * n)
    cands = [photos[int((i + .5) * len(photos) / n_cand)] for i in range(n_cand)]
    measured = []
    for rid, path, taken in cands:
        dest = AMAS / Path(path).name
        cached = AMAS / "all" / dest.name   # lot_estimate.py's copy, kept after Storage drops it
        if not dest.exists() and cached.exists():
            shutil.copyfile(cached, dest)
        if not dest.exists():
            try:
                dest.write_bytes(http("GET", f"{url}/storage/v1/object/{BUCKET}/{path}", auth, raw=True))
            except urllib.error.HTTPError:
                continue   # review copy already gone
        share = product_share(dest)
        if share is not None and share >= EMPTY:
            measured.append((share, dest.name, rid, taken))
    measured.sort()
    k = min(len(measured), n)
    return measured, [measured[int((i + .5) * len(measured) / k)] for i in range(k)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--per-lot", type=int, default=19)
    ap.add_argument("--since", default=CURRENT_LOOK,
                    help="only photos from this time on, UTC (default: the box rebuild, sync_amas.CURRENT_LOOK)")
    ap.add_argument("--training", type=int, default=0, metavar="N",
                    help="instead: N photos since --since whatever their lot, lab result or not, "
                         "for training only (spread across flow)")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    def rest(table, params):
        rows, offset = [], 0
        while True:
            page = http("GET", f"{url}/rest/v1/{table}?{urllib.parse.urlencode(params)}",
                        {**auth, "Range": f"{offset}-{offset + 999}"})
            rows += page
            if len(page) < 1000:
                return rows
            offset += 1000

    # Lab ME% = 100 x me_organic_wt / bulk_density, latest non-superseded of each.
    tests = {t["code"]: t["id"] for t in rest("test_definitions", [
        ("select", "id,code"), ("code", "in.(me_organic_wt,bulk_density)")])}
    lots = rest("lots", [("select", "id,lot_number,packaged_at"), ("legacy_import", "is.false"),
                         ("packaged_at", f"gte.{CHUTE_MOUNTED}"), ("order", "packaged_at,lot_number")])
    results = rest("test_results", [
        ("select", "lot_id,test_id,result_value,notes"), ("is_superseded", "is.false"),
        ("test_id", f"in.({tests['me_organic_wt']},{tests['bulk_density']})"),
        ("lot_id", "in.(%s)" % ",".join(str(l["id"]) for l in lots)), ("order", "created_at.desc")])
    latest = {}
    for r in results:
        # Lots released on the inline estimate carry it as a test result,
        # noted "Estimation (en ligne, 2h) ..." / "Estimation (poids) ...":
        # not a lab value, never compared or calibrated against.
        if (r.get("notes") or "").strip().lower().startswith("estimation"):
            continue
        latest.setdefault((r["lot_id"], r["test_id"]), r["result_value"])

    rows = rest("vision_band_estimates", [
        ("select", "lot_number_text,created_at,storage_path,mesures"),
        ("source", "eq.inline"), ("excluded_reason", "is.null"), ("storage_path", "not.is.null"),
        ("operator_instruction", "not.is.null"), ("created_at", f"gte.{args.since}"),
        ("order", "created_at")])

    AMAS.mkdir(parents=True, exist_ok=True)
    index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    if args.training:
        # Training does not need the lab: a box is right or wrong whatever the
        # lot. No lot and no lab, so these never enter a lot check.
        photos = [(r["lot_number_text"], p, r["created_at"]) for r in rows
                  for p in (r.get("mesures") or {}).get("groupe") or [r["storage_path"]]]
        photos = [ph for ph in photos if Path(ph[1]).name not in index]
        measured, keep = pick(photos, args.training, url, auth)
        for share, name, rid, taken in keep:
            index[name] = {"photo": name, "reading_id": rid, "taken_at": taken,
                           "lot_number": "", "lab_me_pct": None, "product_share": share, "training_only": True}
        INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"training only: {len(photos)} photos since {args.since}, {len(measured)} measured, "
              f"{len(keep)} kept (flow {keep[0][0] if keep else 0:.2f}-{keep[-1][0] if keep else 0:.2f})")
        return

    from sync_amas import EXCLUDED_LOTS
    for lot, (_, start, end) in zip(lots, lot_windows([ts(l["packaged_at"]) for l in lots])):
        wt = latest.get((lot["id"], tests["me_organic_wt"]))
        den = latest.get((lot["id"], tests["bulk_density"]))
        if wt is None or not den or lot["lot_number"] in EXCLUDED_LOTS:
            continue
        lab = round(100 * wt / den, 2)

        photos = []   # (reading_id, storage path, taken_at), bursts in full
        for r in rows:
            if start < ts(r["created_at"]) <= end:
                group = (r.get("mesures") or {}).get("groupe") or [r["storage_path"]]
                photos += [(r["lot_number_text"], p, r["created_at"]) for p in group]
        if not photos:
            continue

        measured, keep = pick(photos, args.per_lot, url, auth)
        for share, name, rid, taken in keep:
            index[name] = {"photo": name, "reading_id": rid, "taken_at": taken,
                           "lot_number": lot["lot_number"], "lab_me_pct": lab, "product_share": share}
        print(f"{lot['lot_number']}  lab {lab:5.2f}  {len(photos):4d} photos in window, "
              f"{len(measured):3d} measured, {len(keep):2d} kept  (flow {keep[0][0] if keep else 0:.2f}"
              f"-{keep[-1][0] if keep else 0:.2f})", flush=True)

    INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(index)} photos in {INDEX}")


if __name__ == "__main__":
    main()
