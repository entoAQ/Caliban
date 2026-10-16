#!/usr/bin/env python3
"""Create the inline labeling project in Label Studio and add new photos to it.

Run with Label Studio already started (start.ps1) and after pull_inline.py.
Safe to rerun: the project, its label config and its local-files storage are
made once, and only photos not already a task are imported.

Each task carries the reading's details (reading_id, taken_at, the vision
estimate...) as data fields, so the Data Manager can sort and filter by them
-- e.g. label a spread of estimates rather than a run of similar ones. They
are not shown on the labeling screen, so the vision estimate does not steer
the labels.

Usage:
    python sync_project.py
"""

import json
from pathlib import Path

from common import IMAGES, PROJECT_TITLE, ROOT, find_project, ls

HERE = Path(__file__).parent

INSTRUCTIONS = """<p>The larvae are already drawn (blue, from their colour): check them, then draw the frass.</p>
<ul>
<li><b>Larves</b>: outlines must hug the larvae. On the chute they are spread out, so a polygon around a group would be mostly bare chute. Touching larvae can share one outline. Fix what the colour got wrong: dark larvae it missed, anything on the rust stain at the top.</li>
<li><b>Frass</b>: each crumb or clump on its own where they are distinct, one outline around a patch of fine dust. Zoom in: grey-brown on a grey chute is easy to miss.</li>
<li><b>Shadows are not frass.</b> Every larva throws a hard dark shadow on the chute: leave it unlabeled.</li>
<li>Unusable photo (blurred, flash missed, empty chute): tick <i>Inutilisable</i> and submit, rather than skip.</li>
</ul>"""


def main():
    pid = find_project()
    if pid is None:
        pid = ls("POST", "/api/projects", {
            "title": PROJECT_TITLE,
            "description": "Photos du capteur en ligne (chute), pour entraîner le modèle larves/frass.",
            "label_config": (HERE / "label_config.xml").read_text(encoding="utf-8"),
            "expert_instruction": INSTRUCTIONS,
            "show_instruction": True,
        })["id"]
        print(f"Created project {pid}")
        # Label Studio only serves /data/local-files/ paths that fall inside
        # a local storage connected to the project. Never synced: syncing
        # would add bare tasks without the reading details.
        ls("POST", "/api/storages/localfiles", {
            "project": pid,
            "title": "Photos chute",
            "path": str(IMAGES),
            "use_blob_urls": True,
        })
    else:
        # Keep the instructions in step with this file.
        ls("PATCH", f"/api/projects/{pid}", {"expert_instruction": INSTRUCTIONS, "show_instruction": True})

    have = set()
    page = 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=500)
        tasks = resp["tasks"] if isinstance(resp, dict) else resp
        have.update(t["data"].get("reading_id") for t in tasks)
        if len(tasks) < 500:
            break
        page += 1

    index = json.loads((ROOT / "readings.json").read_text(encoding="utf-8"))
    new = []
    for rid, details in sorted(index.items()):
        if rid in have or not (IMAGES / f"{rid}.jpg").exists():
            continue
        new.append({"data": {"image": f"/data/local-files/?d=images/{rid}.jpg", **details}})

    if new:
        ls("POST", f"/api/projects/{pid}/import", new)
    print(f"Project {pid}: {len(new)} photos added, {len(have) + len(new)} in total")


if __name__ == "__main__":
    main()
