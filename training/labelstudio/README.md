# Labeling the inline (chute) photos in Label Studio

Label Studio runs locally on the labeling PC. The tray photos use CVAT
(`../CVAT_SETUP.md`); the inline photos use this setup. The taxonomy is the
same: `Larves` and `Frass` as polygons, with the background left
unlabeled. Unlike the packed tray, the chute spreads the larvae out, so outlines
hug the larvae rather than enclosing groups; `prelabel_larvae.py` pre-draws them
from their colour. The output is the YOLO-seg layout that `../augment_rotations.py`
and a YOLO-seg trainer read.

## One-time install

No Docker and no admin rights. Label Studio needs Python ≤3.12, so `uv`
installs it with its own Python, separate from the system 3.14:

```powershell
python -m pip install --user uv
python -m uv tool install label-studio --python 3.12
```

## Each labeling session

```powershell
# 1. Start Label Studio (leave this window open). The first start creates the
#    login and saves it in ~\inline_labeling\ls-login.txt.
powershell -ExecutionPolicy Bypass -File training\labelstudio\start.ps1

# 2. In another window: fetch new photos, then add them to the project.
#    Needs SUPABASE_URL / SUPABASE_SERVICE_KEY in the environment.
cd training\labelstudio
python pull_inline.py
python sync_project.py

# 3. Pre-draw the larvae from their colour (needs OpenCV, hence uv):
python -m uv run --no-project --python 3.12 --with opencv-python-headless --with numpy python prelabel_larvae.py
```

The pre-drawn larvae show up as the starting outline when a photo is opened.
They are suggestions only: a photo counts as labeled once you submit it.
Rerunning `prelabel_larvae.py` redraws photos nobody has submitted yet. Use
`--preview <folder>` to see side-by-side overlays without touching Label
Studio, e.g. after changing its thresholds.

Then open http://127.0.0.1:8080 and use the login in `ls-login.txt`.

**Run `pull_inline.py` often.** Supabase keeps review copies for only a few
days. The originals stay on Ariel in `~/captures/inline/`, but that copy
isn't kept forever either.

## Labeling rules

These are also shown in the project's instructions:

- **Larves** are pre-drawn. Check them and fix what the colour got wrong:
  dark larvae it missed, and anything on the rust stain at the top of the
  chute. The script learns the stain from empty-chute photos and leaves it
  out. Outlines must hug the larvae; touching larvae can share one.
- **Frass**: draw each crumb or clump separately where they're distinct, and
  one outline around a patch of fine dust. The chute is mid-grey, close in
  tone to frass, so zoom in.
- **Shadows are not frass.** Every larva casts a hard dark shadow on the
  chute. Leave shadows unlabeled.
- For an unusable photo (blurred, flash missed, empty chute), tick
  **Inutilisable** and submit.

Every task has the reading's details (`reading_id`, `taken_at`,
`estimate_pct`, `predicted_band`...). Use them in the Data Manager to sort
and filter, for example to label a spread of estimates. They are not shown
on the labeling screen, so the vision estimate can't bias the labels.

## Export for training

```powershell
python export_yolo_seg.py --out C:\path\to\dataset
python ..\augment_rotations.py --images C:\path\to\dataset\images --labels C:\path\to\dataset\labels --out C:\path\to\augmented
```

Class ids follow the label order in `label_config.xml` (Larves = 0,
Frass = 1). Add new labels at the end only.

Check before using `augment_rotations.py` on chute photos: its "no canonical
orientation" argument was made for loose material in a tray. A chute photo
has a flow direction, chute edges, and a fixed flash position, so a 90°
rotation shows the model views it will never see in production. A
horizontal mirror and a 180° rotation are the safer subset.

## Where things live

Everything is in `INLINE_LABELING_DIR` (default `~\inline_labeling`), outside
the repo:

| Path | What |
|---|---|
| `images/` | the photos, `<reading_id>.jpg` |
| `readings.json` | each photo's reading details, written by `pull_inline.py` |
| `ls-data/` | Label Studio's database, which holds **all the labels**. Back it up. |
| `ls-login.txt` | local login and API token |
