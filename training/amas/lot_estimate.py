#!/usr/bin/env python3
"""Estimate lots' ME% from all their inline photos with the trained model.

For every lot, takes every inline photo of the bag (bursts included,
pull_amas.lot_windows()), runs the detector tile by tile on the measured
crop and pools MEO area (what the boxes cover) / product px. The detector is the
run's model as exported for Ariel (amas_runs/<name>/ariel/meo.onnx), read by
Ariel's own rig/inline/meo_detect.py, so the numbers are Ariel's.

Anything set against the lab uses the probe window only -- the last 45
minutes of the bag, the part the lab's probe reaches: the scale from that
ratio to lab ME% (lab = k x ratio, the density factor) is fitted on the lots
the model trained on, and the other lots -- the val lots and lots never
labelled at all -- are estimated blind with it: an ME% with an 80% range
from a bootstrap over the window's readings, and the band. The last column
is the whole bag with the same scale: the lot's own estimate, which is not
compared with the lab. The table is also saved as amas_runs/<name>/lots.json
for compare_models.py.

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
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
sys.path.insert(0, str(HERE.parents[1] / "rig" / "inline"))
from common import ROOT, http  # noqa: E402
from pull_amas import BUCKET, CHUTE_MOUNTED, lot_windows, ts  # noqa: E402
from sync_amas import CURRENT_LOOK, EXCLUDED_LOTS, FIRST_LOT, NO_FIT_LOTS  # noqa: E402

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
        ("operator_instruction", "not.is.null"), ("created_at", f"gte.{CHUTE_MOUNTED}"),
        ("order", "created_at")])
    out = []
    for lot, (whole, probe, end) in zip(lots, lot_windows([ts(l["packaged_at"]) for l in lots])):
        wt = latest.get((lot["id"], tests["me_organic_wt"]))
        den = latest.get((lot["id"], tests["bulk_density"]))
        # "readings": the probe window, for the lab; "whole": the bag.
        whole_r = [(r["lot_number_text"], (r.get("mesures") or {}).get("groupe") or [r["storage_path"]],
                    ts(r["created_at"]) > probe) for r in rows if whole < ts(r["created_at"]) <= end]
        out.append({"lot": lot["lot_number"], "lab": round(100 * wt / den, 2) if wt is not None and den else None,
                    "readings": [(rid, g) for rid, g, in_probe in whole_r if in_probe],
                    "whole": [(rid, g) for rid, g, _ in whole_r],
                    # Every reading on the rebuilt box (sync_amas.CURRENT_LOOK).
                    "current": bool(whole_r) and ts(rows_t(whole_r[0][0], rows)) >= ts(CURRENT_LOOK)})
    return out


def rows_t(rid, rows):
    return next(r["created_at"] for r in rows if r["lot_number_text"] == rid)


def loo_miss(lots, key):
    """Mean miss against the lab, each lot scaled by a fit on the others."""
    ok = [l for l in lots if l[key]]
    miss = []
    for l in ok:
        rest = [o for o in ok if o is not l]
        if rest:
            k = sum(o["lab"] * o[key] for o in rest) / sum(o[key] ** 2 for o in rest)
            miss.append(abs(k * l[key] - l["lab"]))
    return sum(miss) / max(1, len(miss)), len(miss)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="meo")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    lots = [l for l in fetch_lots(url, auth) if l["lot"] >= FIRST_LOT and l["lot"] not in EXCLUDED_LOTS]
    CACHE.mkdir(parents=True, exist_ok=True)
    got = gone = 0
    for lot in lots:
        for _, paths in lot["whole"]:
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

    import meo_detect
    onnx = ROOT / "amas_runs" / args.name / "ariel" / "meo.onnx"
    if not onnx.exists():
        sys.exit(f"No model for Ariel in {onnx.parent} (train_meo.py makes it).")
    meo_detect.use(onnx)
    meo_detect.CONF = args.conf
    data = ROOT / "amas_yolo"
    trained = {json.loads((ROOT / "amas" / "tiles" / "tiles.json").read_text(encoding="utf-8"))[f.name]["lot_number"]
               for f in (data / "images" / "train").glob("*.jpg")}

    # Ariel's dust mask (meo_detect.Background), photo by photo in the order
    # they were taken -- lots and readings come in time order -- so every
    # lot also gets the dust-free ratio Caliban uses with inline_settings.
    # dust_mask, and a second scale is fitted on it. Until a background has
    # five photos (the first of a run, after a stop) a photo's raw numbers
    # stand in, close to what Ariel sends then.
    background = meo_detect.Background()
    # And without the dust strip (meo_detect.STRIP, inline_settings.
    # exclude_strip): a third scale, and how steady the strip's share is.
    clean, strip = {}, {}

    def photo(path):
        # IL-YYYYMMDD-HHMMSS, burst photos -2, -3 ... about 12 s apart.
        k = path.stem[19:]
        at = datetime.strptime(path.stem[3:18], "%Y%m%d-%H%M%S").timestamp() + 12 * (int(k) - 1 if k else 0)
        m = meo_detect.measure(path, background, at)
        c = m.get("clean") or m
        clean[path.name] = (c["meo_px"], c["product_px"])
        strip[path.name] = (m["strip"]["meo_px"], m["strip"]["product_px"])
        return m["meo_px"], m["product_px"]

    def pooled(pr):
        return 100 * sum(m for m, _ in pr) / max(1, sum(p for _, p in pr)) if pr else None

    for lot in lots:
        measured, cleaned, stripped = {}, {}, {}
        for rid, paths in lot["whole"]:
            ok = [CACHE / Path(p).name for p in paths if (CACHE / Path(p).name).exists()]
            ms = [photo(p) for p in ok]
            if ms:
                measured[rid] = (sum(m for m, _ in ms), sum(p for _, p in ms))
                cleaned[rid] = (sum(clean[p.name][0] for p in ok), sum(clean[p.name][1] for p in ok))
                stripped[rid] = (sum(strip[p.name][0] for p in ok), sum(strip[p.name][1] for p in ok))
        lot["per_reading"] = [measured[rid] for rid, _ in lot["readings"] if rid in measured]
        lot["ratio"] = pooled(lot["per_reading"])
        lot["whole_ratio"] = pooled(list(measured.values()))
        lot["whole_n"] = len(measured)
        lot["clean_ratio"] = pooled([cleaned[rid] for rid, _ in lot["readings"] if rid in cleaned])
        probe = [rid for rid, _ in lot["readings"] if rid in stripped]
        lot["strip_ratio"] = pooled([stripped[rid] for rid in probe])
        # The strip's share of the probe window's MEO area and product.
        lot["strip_share"] = (1 - sum(stripped[r][0] for r in probe) / max(1, sum(measured[r][0] for r in probe)),
                              1 - sum(stripped[r][1] for r in probe) / max(1, sum(measured[r][1] for r in probe))
                              ) if probe else None
        print(f"  {lot['lot']}: {len(lot['per_reading'])} readings in the probe window, {len(measured)} in the bag",
              flush=True)

    cal = [l for l in lots if l["lot"] in trained and l["lab"] is not None and l["ratio"] and l["lot"] not in NO_FIT_LOTS]
    k = sum(l["lab"] * l["ratio"] for l in cal) / sum(l["ratio"] ** 2 for l in cal)
    print(f"\nscale fitted on {len(cal)} training lots: lab = {k:.2f} x ratio\n")
    # The same fit on the dust-free ratio: the scale for dust_mask on.
    kd = sum(l["lab"] * l["clean_ratio"] for l in cal) / sum(l["clean_ratio"] ** 2 for l in cal)

    def blind_miss(scale, key):
        b = [abs(scale * l[key] - l["lab"]) for l in lots
             if l[key] and l["lab"] is not None and l["lot"] not in trained and l["lot"] not in NO_FIT_LOTS]
        return sum(b) / max(1, len(b)), len(b)
    (m_raw, n_blind), (m_dust, _) = blind_miss(k, "ratio"), blind_miss(kd, "clean_ratio")
    print(f"dust mask: lab = {kd:.2f} x dust-free ratio; the blind lots ({n_blind}) miss by "
          f"{m_dust:.2f} with it, {m_raw:.2f} without")
    ks = sum(l["lab"] * l["strip_ratio"] for l in cal) / sum(l["strip_ratio"] ** 2 for l in cal)
    m_strip, _ = blind_miss(ks, "strip_ratio")
    print(f"dust strip out: lab = {ks:.2f} x ratio without the strip; the blind lots miss by "
          f"{m_strip:.2f} with it out, {m_raw:.2f} with it in")
    # Removing it is only fair if it takes about the same share of the MEO
    # every lot: a share that swings with the lot would bias some lots.
    shares = [l["strip_share"] for l in lots if l["strip_share"] and l["lab"] is not None]
    if shares:
        meo_sh = sorted(s[0] for s in shares)
        print(f"  strip's share of the MEO area, lot by lot: median {100 * meo_sh[len(meo_sh) // 2]:.0f}%, "
              f"middle 80% {100 * meo_sh[len(meo_sh) // 10]:.0f}-{100 * meo_sh[len(meo_sh) * 9 // 10]:.0f}%; "
              f"of the product {100 * sorted(s[1] for s in shares)[len(shares) // 2]:.0f}%")
    # The strip was drawn on the rebuilt box: what counts is the lots since,
    # each scaled by a fit on the others (whatever the model trained on).
    now = [l for l in lots if l["current"] and l["lab"] is not None and l["ratio"] and l["lot"] not in NO_FIT_LOTS]
    if len(now) >= 3:
        (a, n), (b, _) = loo_miss(now, "ratio"), loo_miss(now, "strip_ratio")
        print(f"  lots on the rebuilt box ({n}), each scaled on the others: miss {b:.2f} with the strip out, "
              f"{a:.2f} with it in")
        for l in now:
            print(f"    {l['lot']}: lab {l['lab']:5.2f}   strip's share of MEO {100 * l['strip_share'][0]:3.0f}%, "
                  f"of product {100 * l['strip_share'][1]:3.0f}%")
    print()
    # For retrain.ps1: the scale goes into Caliban's inline_settings.yolo_scale
    # -- yolo_scale_dust instead, when dust_mask is turned on with it, and
    # yolo_scale_strip with exclude_strip.
    (ROOT / "amas_runs" / args.name / "scale.json").write_text(
        json.dumps({"yolo_scale": round(k, 3), "yolo_scale_dust": round(kd, 3), "yolo_scale_strip": round(ks, 3),
                    "lots": len(cal), "blind_miss": round(m_raw, 3), "blind_miss_dust": round(m_dust, 3),
                    "blind_miss_strip": round(m_strip, 3)}), encoding="utf-8")
    random.seed(3)
    rows = []
    print(f"{'lot':15s} {'used':10s} readings  estimate  80% range     band     lab    lab band   whole bag (readings)")
    for l in lots:
        if not l["ratio"]:
            if l["whole_ratio"]:
                print(f"{l['lot']:15s} {'':10s} {0:5d}   {'':48s}   {k * l['whole_ratio']:6.2f} ({l['whole_n']})")
            continue
        pr = l["per_reading"]
        bs = sorted(k * 100 * sum(m for m, _ in s) / max(1, sum(p for _, p in s))
                    for s in (random.choices(pr, k=len(pr)) for _ in range(1000)))
        est = k * l["ratio"]
        used = "not fitted" if l["lot"] in NO_FIT_LOTS else "training" if l["lot"] in trained else "BLIND"
        rows.append({"lot": l["lot"], "used": used, "readings": len(pr), "estimate": round(est, 3),
                     "lab": l["lab"], "whole_bag": round(k * l["whole_ratio"], 3) if l["whole_ratio"] else None})
        lab = f"{l['lab']:5.2f}   {band(l['lab']):6s}" if l["lab"] is not None else "  -            "
        print(f"{l['lot']:15s} {used:10s} {len(pr):5d}   {est:6.2f}   {bs[100]:5.2f}-{bs[900]:5.2f}   "
              f"{band(est):7s} {lab}   {k * l['whole_ratio']:6.2f} ({l['whole_n']})")
    (ROOT / "amas_runs" / args.name / "lots.json").write_text(
        json.dumps({"yolo_scale": round(k, 3), "lots": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
