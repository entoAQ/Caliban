#!/usr/bin/env python3
"""Put some lots' tiles first in the labelling queue.

Label Studio hands out tasks in the order they were imported, so this takes
the tasks nobody has labelled (no submitted label, no draft), removes them
and imports them again in a new order:

    1. the rest of any photo already started, so it gets finished;
    2. the tiles of the given lots, photos mixed across those lots;
    3. everything else, as before.

Labelled tiles are never touched. Lot numbers stay off the labelling screen,
so you still cannot tell which lot a tile is from.

Usage:
    python reorder_amas.py 20261013-BB03 20261016-BB06 20261016-BB08
"""

import json
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ls  # noqa: E402
from sync_amas import AMAS, find_project  # noqa: E402


def main():
    first = set(sys.argv[1:])
    if not first:
        raise SystemExit(__doc__)
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

    def rank(t):
        d = index[t["data"]["tile"]]
        group = 0 if d["photo"] in started else 1 if d["lot_number"] in first else 2
        return group, zlib.crc32(d["photo"].encode()), d["row"], d["col"]
    todo.sort(key=rank)

    ids = [t["id"] for t in todo]
    for i in range(0, len(ids), 500):
        ls("POST", "/api/dm/actions", {"selectedItems": {"all": False, "included": ids[i:i + 500]}},
           id="delete_tasks", project=pid)
    new = [{"data": t["data"]} for t in todo]
    for i in range(0, len(new), 500):
        ls("POST", f"/api/projects/{pid}/import", new[i:i + 500])
    n_first = sum(1 for t in todo if rank(t)[0] < 2)
    print(f"{len(done)} labelled tiles kept; {len(todo)} re-queued, {n_first} of them first "
          f"({sum(1 for t in todo if rank(t)[0] == 0)} finishing started photos)")


if __name__ == "__main__":
    main()
