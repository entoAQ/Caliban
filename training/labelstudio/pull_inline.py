#!/usr/bin/env python3
"""Download the inline rig's chute photos from Supabase for labeling.

Takes every inline reading with a stored photo since the rig went on the
chute (2026-10-13 08:30 America/Toronto, see rig/inline/
exclude_bench_readings.sql) that is not excluded, saves the photo to
INLINE_LABELING_DIR/images/<reading>.jpg and its reading details to
INLINE_LABELING_DIR/readings.json, which sync_project.py turns into Label
Studio tasks. Photos already on disk are not downloaded again, so it is safe
to rerun whenever there are new readings.

The stored photo is Caliban's review copy (app/main.py review_copy): the
inline camera's 1332x990 frame is under MODEL_MAX_EDGE, so it is full size,
just re-encoded at quality 90. Supabase only keeps review copies for a few
days, so run this regularly rather than counting on old photos still being
there.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in the environment.

Usage:
    python pull_inline.py                 # everything since the chute mount
    python pull_inline.py --since 2026-10-20
"""

import argparse
import json
import os
import urllib.error
import urllib.parse

from common import IMAGES, ROOT, http

BUCKET = "vision-band-test-captures"   # app/main.py BAND_TEST_CAPTURE_BUCKET
CHUTE_MOUNTED = "2026-10-13T12:30:00Z"  # 08:30 America/Toronto


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default=CHUTE_MOUNTED,
                    help="ISO date/time, UTC unless it says otherwise (default: chute mount)")
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    rows, offset = [], 0
    while True:
        query = urllib.parse.urlencode([
            ("select", "lot_number_text,created_at,storage_path,predicted_band,estimate_pct,"
                       "operator_instruction,prompt_version,photo_mean"),
            ("source", "eq.inline"),
            ("excluded_reason", "is.null"),
            ("storage_path", "not.is.null"),
            ("created_at", f"gte.{args.since}"),
            ("order", "created_at"),
        ])
        page = http("GET", f"{url}/rest/v1/vision_band_estimates?{query}",
                    {**auth, "Range": f"{offset}-{offset + 999}"})
        rows += page
        if len(page) < 1000:
            break
        offset += 1000

    # Each reading is judged by more than one prompt, one row each, all
    # pointing at the same photo. The deciding row (the one carrying the
    # instruction) supplies the reading's details.
    readings = {}
    for r in rows:
        rid = r["lot_number_text"]
        if rid not in readings or r["operator_instruction"]:
            readings[rid] = r

    IMAGES.mkdir(parents=True, exist_ok=True)
    index_path = ROOT / "readings.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}

    new = missing = 0
    for rid, r in readings.items():
        dest = IMAGES / f"{rid}.jpg"
        if not dest.exists():
            try:
                content = http("GET", f"{url}/storage/v1/object/{BUCKET}/{r['storage_path']}",
                               auth, raw=True)
            except urllib.error.HTTPError as e:
                # Gone from Storage already (review copies are short-lived).
                print(f"  {rid}: photo not in Storage ({e.code}), skipped")
                missing += 1
                continue
            dest.write_bytes(content)
            new += 1
        index[rid] = {
            "reading_id": rid,
            "taken_at": r["created_at"],
            "predicted_band": r["predicted_band"],
            "estimate_pct": r["estimate_pct"],
            "operator_instruction": r["operator_instruction"],
            "prompt_version": r["prompt_version"],
            "photo_mean": r["photo_mean"],
        }

    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(readings)} readings since {args.since}: {new} new photos, "
          f"{missing} no longer in Storage, {len(index)} in {index_path}")


if __name__ == "__main__":
    main()
