#!/usr/bin/env python3
"""Reorder the labelling queue.

Label Studio hands out tasks in the order they were imported, so this takes
the tasks nobody has labelled (no submitted label, no draft), removes them
and imports them again -- pre-filled boxes included -- in a new order:

    1. the rest of any photo already started, so it gets finished;
    2. photos taken since --since (default: the box rebuild,
       sync_amas.CURRENT_LOOK -- the look the model has to read now), and
       with lot numbers given, those lots' tiles too; photos mixed;
    3. everything else, in the old shuffled order, or with --hard hardest
       first -- most boxes the model is unsure of (score < 0.5), then most
       boxes, then most product (dense streams).

Labelled tiles are never touched. Lot numbers stay off the labelling screen.

Usage:
    python reorder_amas.py                       # new look first
    python reorder_amas.py 20261016-BB06 20261016-BB08
    python reorder_amas.py --hard
    python reorder_amas.py --since 2026-10-18T14:20:00Z
"""

import json
import sys
import zlib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ls  # noqa: E402
from sync_amas import AMAS, CURRENT_LOOK, find_project  # noqa: E402


def when(s):
    """An ISO time from Supabase or the command line, comparable."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        raise SystemExit(__doc__)
    hard = "--hard" in args
    since = CURRENT_LOOK
    if "--since" in args:
        i = args.index("--since")
        since = args[i + 1]
        del args[i:i + 2]
    since = when(since)
    first = {a for a in args if a != "--hard"}
    pid = find_project()
    index = json.loads((AMAS / "tiles" / "tiles.json").read_text(encoding="utf-8"))
    unknown = first - {d["lot_number"] for d in index.values()}
    if unknown:
        raise SystemExit(f"No tiles for {sorted(unknown)}")

    tasks, page = [], 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=100, fields="all")
        batch = resp["tasks"] if isinstance(resp, dict) else resp
        tasks += batch
        if len(batch) < 100:
            break
        page += 1
    done = [t for t in tasks if t.get("total_annotations") or t.get("annotations") or t.get("drafts")
            or t.get("draft_exists") or t.get("cancelled_annotations")]
    todo = [t for t in tasks if t not in done]
    started = {t["data"]["photo"] for t in done}

    def preds(t):
        return [p for p in (t.get("predictions") or []) if isinstance(p, dict)]

    def hardness(t):
        boxes = [r for p in preds(t)[-1:] for r in p["result"]]
        unsure = sum(1 for r in boxes if (r.get("score") or 1) < 0.5)
        d = index[t["data"]["tile"]]
        return unsure, len(boxes), d["product_px"] / (d["width"] * d["height"])

    def rank(t):
        d = index[t["data"]["tile"]]
        mix = zlib.crc32(d["photo"].encode())
        if d["photo"] in started:
            return (0, mix, d["row"], d["col"])
        if d["lot_number"] in first or (d.get("taken_at") and when(d["taken_at"]) >= since):
            return (1, mix, d["row"], d["col"])
        if hard:
            u, n, dens = hardness(t)
            return (2, -u, -n, -dens)
        return (2, mix, d["row"], d["col"])
    todo.sort(key=rank)

    new = [{"data": t["data"],
            "predictions": [{"result": p["result"], "model_version": p.get("model_version"),
                             "score": p.get("score")} for p in preds(t)[-1:]]}
           for t in todo]
    ids = [t["id"] for t in todo]
    for i in range(0, len(ids), 500):
        ls("POST", "/api/dm/actions", {"selectedItems": {"all": False, "included": ids[i:i + 500]}},
           id="delete_tasks", project=pid)
    for i in range(0, len(new), 500):
        ls("POST", f"/api/projects/{pid}/import", new[i:i + 500])
    groups = [rank(t)[0] for t in todo]
    print(f"{len(done)} labelled tiles kept; {len(todo)} re-queued: {groups.count(0)} finishing started "
          f"photos, {groups.count(1)} next (since {since:%Y-%m-%d %H:%M} UTC"
          f"{' or the lots given' if first else ''}), {groups.count(2)} after"
          f"{' (hardest first)' if hard else ''}; pre-filled boxes kept on "
          f"{sum(1 for t in new if t['predictions'])}")


if __name__ == "__main__":
    main()
