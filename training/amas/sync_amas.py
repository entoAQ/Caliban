#!/usr/bin/env python3
"""Create the clump-box project in Label Studio and add the chosen photos.

Run with Label Studio started (training/labelstudio/start.ps1) and after
pull_amas.py. Safe to rerun: the project and its storage are made once,
instructions and labels are kept in step with this folder, and only photos
not already a task are imported.

Tasks carry the photo, reading and taken_at only. The lot, its lab ME% and
the flow stay in amas_photos.json, so nothing on screen hints at how dirty
the lot was.

Usage:
    python sync_amas.py
"""

import json
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
from common import ROOT, ls  # noqa: E402

AMAS = ROOT / "amas"
PROJECT_TITLE = "Chute en ligne - Amas"

INSTRUCTIONS = """<p>Draw a box around every <b>clump</b> in the photo, and nothing else.</p>
<ol>
<li><b>Amas</b> (1): frass clump you could pick up between two fingers. Frass has an <b>irregular, crumbly</b> edge; a larva has a <b>smooth</b> edge, gloss or segment lines. Box tight around the clump.</li>
<li>Skip anything you could not pick up between two fingers: dust, crumbs, specks. They are not ME.</li>
<li><b>Peau</b> (2): pale papery shed skin. Not ME, boxed so the model learns to tell it apart.</li>
<li><b>Incertain</b> (3): you cannot tell clump from larva (half hidden, blurred). Box it rather than guess.</li>
<li>Larvae, chute and shadows: nothing. A photo with no clump is submitted with no box.</li>
</ol>
<p>Zoom in: grey-brown on a grey chute is easy to miss. Go over the whole photo, side streams included.</p>
<p>Unusable photo (blurred, flash missed, empty chute): tick <i>Inutilisable</i> and submit, rather than skip.</p>"""


def find_project():
    resp = ls("GET", "/api/projects", page_size=100)
    for p in resp.get("results", resp) if isinstance(resp, dict) else resp:
        if p["title"] == PROJECT_TITLE:
            return p["id"]
    return None


def main():
    config = (HERE / "label_config.xml").read_text(encoding="utf-8")
    pid = find_project()
    if pid is None:
        pid = ls("POST", "/api/projects", {
            "title": PROJECT_TITLE,
            "description": "Amas de frass sur la chute, pour mesurer le ME% par lot.",
            "label_config": config, "expert_instruction": INSTRUCTIONS, "show_instruction": True,
        })["id"]
        print(f"Created project {pid}")
        # Never synced, only connected: it is what lets Label Studio serve
        # /data/local-files/ paths under amas/.
        ls("POST", "/api/storages/localfiles", {
            "project": pid, "title": "Photos amas", "path": str(AMAS), "use_blob_urls": True,
        })
    else:
        ls("PATCH", f"/api/projects/{pid}", {
            "expert_instruction": INSTRUCTIONS, "show_instruction": True, "label_config": config,
        })

    have, page = set(), 1
    while True:
        resp = ls("GET", "/api/tasks", project=pid, page=page, page_size=500)
        tasks = resp["tasks"] if isinstance(resp, dict) else resp
        have.update(t["data"].get("photo") for t in tasks)
        if len(tasks) < 500:
            break
        page += 1

    index = json.loads((AMAS / "amas_photos.json").read_text(encoding="utf-8"))
    # Shuffled by name hash so labelling order mixes lots and flows.
    new = [{"data": {"image": f"/data/local-files/?d=amas/{name}", "photo": name,
                     "reading_id": d["reading_id"], "taken_at": d["taken_at"]}}
           for name, d in sorted(index.items(), key=lambda kv: zlib.crc32(kv[0].encode()))
           if name not in have and (AMAS / name).exists()]
    if new:
        ls("POST", f"/api/projects/{pid}/import", new)
    print(f"Project {pid}: {len(new)} photos added, {len(have) + len(new)} in total")


if __name__ == "__main__":
    main()
