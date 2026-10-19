#!/usr/bin/env python3
"""Scorecard of the live MEO detector against the lab, lot by lot.

For every lot with a real lab ME% (estimates noted "Estimation...", the
excluded lots and the no-fit lots left out, as everywhere in this folder)
whose window holds readings decided by the detector (prompt yolo-...),
pools them as the screen's estimate does -- scale x 100 x sum(MEO) /
sum(product) over the lot's photos -- and sets it beside the lab.

Written to INLINE_LABELING_DIR/scorecard.csv (one row per lot) and
scorecard.txt (the summary), and printed. Nothing goes to SGSC. Run by hand
or daily by the scheduled task "Caliban MEO scorecard":

    python scorecard.py
"""

import csv
import os
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT, http  # noqa: E402
from lot_estimate import band, fetch_lots  # noqa: E402
from sync_amas import EXCLUDED_LOTS, FIRST_LOT, NO_FIT_LOTS  # noqa: E402


def main():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    lots = [l for l in fetch_lots(url, auth) if l["lot"] >= FIRST_LOT and l["lot"] not in EXCLUDED_LOTS
            and l["lot"] not in NO_FIT_LOTS and l["lab"] is not None]

    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode([("select", "lot_number_text,prompt_version,mesures"), ("source", "eq.inline"),
                                    ("prompt_version", "like.yolo-*"), ("operator_instruction", "not.is.null")])
        page = http("GET", f"{url}/rest/v1/vision_band_estimates?{q}", {**auth, "Range": f"{offset}-{offset + 999}"})
        rows += page
        if len(page) < 1000:
            break
        offset += 1000
    yolo = {r["lot_number_text"]: r for r in rows}

    out = []
    for l in lots:
        mine = [yolo[rid] for rid, _ in l["readings"] if rid in yolo]
        ys = [(r.get("mesures") or {}).get("yolo") or {} for r in mine]
        ys = [y for y in ys if y.get("product_px")]
        if not ys:
            continue
        # Each reading carries its own scale; pooled at the scale each was shown with.
        est = sum(y["scale"] * 100 * y["meo_px"] for y in ys) / sum(y["product_px"] for y in ys)
        versions = sorted({r["prompt_version"] for r in mine})
        out.append({"lot": l["lot"], "lab": l["lab"], "detector": round(est, 2), "readings": len(ys),
                    "miss": round(est - l["lab"], 2), "lab_band": band(l["lab"]), "detector_band": band(est),
                    "same_band": band(est) == band(l["lab"]), "models": " ".join(versions)})

    path = ROOT / "scorecard.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["lot", "lab", "detector", "readings", "miss", "lab_band",
                                          "detector_band", "same_band", "models"])
        w.writeheader()
        w.writerows(out)

    lines = [f"MEO detector scorecard, {datetime.now():%Y-%m-%d %H:%M}",
             f"{'lot':15s} {'lab':>6s} {'detector':>8s} {'miss':>6s}  readings  band"]
    for r in out:
        lines.append(f"{r['lot']:15s} {r['lab']:6.2f} {r['detector']:8.2f} {r['miss']:+6.2f}  {r['readings']:8d}  "
                     f"{r['detector_band']:6s} {'ok' if r['same_band'] else 'lab ' + r['lab_band']}")
    if out:
        misses = [abs(r["miss"]) for r in out]
        lines.append(f"\n{len(out)} lots: average miss {sum(misses) / len(misses):.2f} points, "
                     f"same band {sum(r['same_band'] for r in out)} of {len(out)}, "
                     f"read low {sum(r['miss'] < 0 for r in out)}, high {sum(r['miss'] > 0 for r in out)}")
    else:
        lines.append("\nNo lot with both a lab result and detector readings yet.")
    text = "\n".join(lines)
    (ROOT / "scorecard.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
