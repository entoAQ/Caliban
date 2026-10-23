#!/usr/bin/env python3
"""Score a newly trained MEO detector against the one on Ariel, on the same lots.

Each model's lot table comes from lot_estimate.py (amas_runs/<name>/lots.json):
lab ME% against the probe window, scaled by the model's own fitted scale. Only
lots that are blind to both models and have a lab result and at least
MIN_READINGS readings count -- a lot one of them trained on would flatter it.

The live model is the version in rig/inline/models/meo.json. Its table is
(re)made first (lot_estimate.py, about 10 minutes) if this laptop has none, or
if it lacks lab lots the new model's table has.

Prints average miss, bias and lots in the right band for both, and a verdict:
the new model is better only if it misses less on average and puts no fewer
lots in the right band. Writes amas_runs/<new>/compare.json.

Usage:
    python compare_models.py --name meo-20261019-1653
"""

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from lot_estimate import band  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MIN_READINGS = 3


def table(name, need=()):
    f = ROOT / "amas_runs" / name / "lots.json"
    if not f.exists() or not set(need) <= {r["lot"] for r in json.loads(f.read_text(encoding="utf-8"))["lots"]}:
        if not (ROOT / "amas_runs" / name / "ariel" / "meo.onnx").exists():
            sys.exit(f"No lot table or model for {name} in {ROOT / 'amas_runs'}.")
        print(f"No up-to-date lot table for {name}; running lot_estimate.py on it (about 10 minutes)...", flush=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name("lot_estimate.py")), "--name", name],
                       check=True, stdout=subprocess.DEVNULL)
    return {r["lot"]: r for r in json.loads(f.read_text(encoding="utf-8"))["lots"]}


def score(t, lots):
    m = [t[k]["estimate"] - t[k]["lab"] for k in lots]
    return {"miss": round(statistics.mean(map(abs, m)), 2), "bias": round(statistics.mean(m), 2),
            "band": sum(band(t[k]["estimate"]) == band(t[k]["lab"]) for k in lots)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True, help="the new model's run name")
    ap.add_argument("--live", help="the model to beat (default: rig/inline/models/meo.json)")
    args = ap.parse_args()
    live = args.live or json.loads(
        (REPO / "rig" / "inline" / "models" / "meo.json").read_text(encoding="utf-8-sig"))["version"]
    if live == args.name:
        sys.exit(f"{args.name} is already the model in rig/inline/models.")

    new = table(args.name)
    old = table(live, [k for k, r in new.items() if r["lab"] is not None])
    lots = sorted(k for k in new if k in old
                  and new[k]["used"] == "BLIND" and old[k]["used"] == "BLIND" and new[k]["lab"] is not None
                  and new[k]["readings"] >= MIN_READINGS and old[k]["readings"] >= MIN_READINGS)
    if not lots:
        sys.exit("No lot is blind to both models with a lab result.")
    s_new, s_old = score(new, lots), score(old, lots)
    better = s_new["miss"] < s_old["miss"] and s_new["band"] >= s_old["band"]

    print(f"\n{len(lots)} lots blind to both, lab ME% against the probe window:")
    print(f"  {'model':22s} {'average miss':>12s} {'bias':>7s} {'right band':>11s}")
    for nm, s in ((f"{live} (Ariel)", s_old), (f"{args.name} (new)", s_new)):
        print(f"  {nm:22s} {s['miss']:12.2f} {s['bias']:+7.2f} {s['band']:6d}/{len(lots)}")
    print(f"\n  {'lot':15s} {'lab':>6s} {'Ariel':>7s} {'new':>7s}")
    for k in lots:
        print(f"  {k:15s} {new[k]['lab']:6.2f} {old[k]['estimate']:7.2f} {new[k]['estimate']:7.2f}")
    print(f"\nVERDICT: {args.name} is {'BETTER' if better else 'NOT better'} than {live}.")
    (ROOT / "amas_runs" / args.name / "compare.json").write_text(json.dumps(
        {"live": live, "lots": lots, "live_score": s_old, "new_score": s_new, "better": better}, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    main()
