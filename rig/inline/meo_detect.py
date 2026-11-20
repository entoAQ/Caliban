#!/usr/bin/env python3
"""MEO detector for the inline rig: the model trained on the boxed tiles
(training/amas/), run on Ariel itself.

Each photo is cut to the measured area (below the top 22%, right of the left
10% -- the crop the prompts used) and that into 3 x 2 tiles, exactly as the
tiles were cut for labelling (training/amas/tile_amas.py); the model finds
the MEO pieces on each tile. Per photo it returns the MEO box area and count,
and the product pixels (warm, saturated: flashcam.product_share's rule) of
the same area. Caliban turns a burst's sums into an ME% estimate:
scale x 100 x sum(meo_px) / sum(product_px).

No PyTorch: the model is exported to ONNX (models/meo.onnx) and run with
onnxruntime (pip install onnxruntime), with numpy and Pillow for the rest.
The session is made once and kept, ~100 MB.

Two kinds of model, told apart by "arch" in models/meo.json:
  "yolox-tiny" -- YOLOX (Apache-2.0), trained by training/amas/train_meo.py
                  from 2026-10-23: free to use in production and to keep
                  private.
  none         -- the YOLO11n models before it (Ultralytics, AGPL-3.0), read
                  until the first YOLOX model is promoted.
The two take their input differently (below); everything after the boxes is
the same. The training scripts run this same file on the laptop (use()), so
what is scored there is what Ariel runs.

    python3 meo_detect.py photo.jpg [more.jpg ...]    # per-photo numbers + timing
"""

import json
import os
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "models", "meo.onnx")
VERSION_FILE = os.path.join(HERE, "models", "meo.json")
CROP_TOP, CROP_LEFT = 0.22, 0.10
COLS, ROWS = 3, 2
SIZE = 416
CONF = 0.25
IOU = {"yolox": 0.45, "yolo11": 0.7}  # each family's own default
# Boxes sent per photo for review (SGSC draws them on the photo); a photo
# with more than this keeps the most confident ones. The area uses them all.
MAX_BOXES = 150
# The strip below the chute's lip where dust piles up (Tim's outline on a
# photo of 2026-10-26), as (x, y) fractions of the photo. It holds ~16% of
# the measured product but 22-39% of the MEO area (2026-10-24..26). measure()
# sends the numbers without it as "strip"; Caliban uses them with
# inline_settings.exclude_strip.
STRIP = [(0.173, 0.0), (0.173, 0.018), (0.291, 0.118), (0.415, 0.195), (0.560, 0.279),
         (0.705, 0.341), (0.839, 0.397), (1.0, 0.49), (1.0, 0.0)]
_strips = {}
# The metal lip of the destoner's exit (a steel edge across the top right of
# the frame). find_lip(): the bright grey pixels in pieces touching the
# frame's edge, LIP_MIN to LIP_MAX of the frame, a straight line fitted along
# them, and everything beyond their inner edge (plus LIP_MARGIN of the
# photo's width) is lip -- on the 10-26..11-10 view that also takes the
# copper wall behind it. The flash's hot spot is as bright and grey but never
# touches the edge. Fitted once on the empty-chute photo (flashcam.py
# reference) and kept in VIEW_FILE, as the lip is then whole: under a heavy
# flow the larvae hide most of it, and a fit on what is left can follow the
# frame's edge instead (2026-11-14 06:00: 4 deg against 19.3). Going below
# LIP_MIN once took 44% of a blurred frame, hence LIP_MAX too.
LIP_GREY_MIN, LIP_GREY_SPREAD, LIP_BLUE_MAX = 140, 40, 8
LIP_MIN, LIP_MAX = 0.001, 0.15
LIP_MARGIN = 0.012
# The camera's view: {"lip": find_lip()'s dict, "at"}, written by flashcam.py
# reference; DEFAULT_VIEW_FILE (in git) until it is, fitted on a 10-photo
# median of 2026-11-14 15:25 local (low flow, the view since the 11-13 lens
# fix). Background.view_check() looks for metal inside the lip's line by
# more than VIEW_OFF_PX: it moves in when the camera turns, while larvae
# over the lip only hide it.
VIEW_FILE = os.path.join(os.path.expanduser("~"), "inline_view.json")
DEFAULT_VIEW_FILE = os.path.join(HERE, "view.json")
VIEW_OFF_PX = 40
_view = {}
# The whole frame, measured beside the crop ("full", recorded only, never
# decides -- yet): the crop's 3 x 2 tiles as they are, plus FULL_TOP tiles of
# the same size along the top band and FULL_LEFT down the left strip, so every
# tile is the size the model was trained on. Tiles overlap; each pixel has
# one owner tile (_owners) and a box counts only from the tile that owns its
# centre, so nothing is counted twice.
FULL_TOP, FULL_LEFT = 4, 2
_owners = {}
# A box is bigger than the piece in it: on Tim's boxes of 2026-10-24..26 the
# warm pixels (red over blue by more than this, dark ones included) fill
# ~0.68 of the box, the bare chute and the pieces' shadows the rest.
# measure() sends the MEO pixels inside the boxes as "area", recorded beside
# the box numbers for the surface-area ME% under study (not used to decide;
# a piece touching a larva takes in its edge too).
AREA_WARM = 8
MEO = 0  # class 0 of MEO / Peau / Incertain / Bicolore / Poussière
# Bicolore (class 3): a dark or two-tone larva or prepupa. Sent beside MEO as
# "bicolore", for SGSC to show on the photos and per lot; nothing decides on
# it (2026-10-27, 4 Bicolore boxes in validation so far).
BICOLORE = 3
# Plastique (class 5, labelled since 2026-11-03, 48 boxes by 2026-11-20):
# a piece of plastic. Sent beside MEO as "plastique" (count and boxes) to see
# whether the model starts finding the pieces the lab counts (plastic_nc);
# nothing decides on it and it is never in the MEO numbers.
PLASTIQUE = 5
LARVA_MIN_SHARE = 0.05  # larva_length(): a nearly empty chute says little
LARVA_MIN = 3           # isolated larvae needed on a photo

_session = None


def use(model):
    """Run another model file than Ariel's (the training scripts); its
    meo.json beside it says which kind it is."""
    global MODEL, VERSION_FILE, _session
    MODEL = str(model)
    VERSION_FILE = os.path.join(os.path.dirname(MODEL), "meo.json")
    _session = None


def _meta():
    try:
        # utf-8-sig: PowerShell writes meo.json with a BOM.
        with open(VERSION_FILE, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def version():
    # "+union": MEO area counted once where boxes overlap (measure()), from
    # 2026-10-26; readings without it summed the boxes, ~13% more on average,
    # and were scaled for that. Caliban keeps it in prompt_version.
    return (_meta().get("version") or "meo") + "+union"


def arch():
    return "yolox" if str(_meta().get("arch") or "").startswith("yolox") else "yolo11"


def session():
    global _session
    if _session is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        # Two threads: quick enough, and leaves the Pi's other cores for the
        # camera loop and the screen.
        opts.intra_op_num_threads = 2
        _session = ort.InferenceSession(MODEL, opts, providers=["CPUExecutionProvider"])
    return _session


def product_mask(a):
    """a: H x W x 3 RGB uint8. Warm, saturated, not dark (flashcam.product_share).

    Saturation (mx - mn) / mx > 0.28 is tested as 25 (mx - mn) > 7 mx in
    integers: the same pixels as the float version (no ratio of two 8-bit
    values lies close enough to 7/25 for float32 to round across it), in
    about half its time on a full photo (2026-11-16)."""
    r, g, b = (a[..., i].astype(np.int16) for i in range(3))
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    return (25 * (mx - mn) > 7 * np.maximum(mx, 1)) & (r > b + 15) & (mx > 50)


def _letterbox(tile):
    """Resize to fit SIZE x SIZE keeping proportions, pad grey (114) evenly on
    both sides, as the model was trained (ultralytics LetterBox)."""
    h, w = tile.shape[:2]
    r = min(SIZE / h, SIZE / w)
    nw, nh = int(round(w * r)), int(round(h * r))
    img = np.asarray(Image.fromarray(tile).resize((nw, nh), Image.BILINEAR))
    dw, dh = (SIZE - nw) / 2, (SIZE - nh) / 2
    left, top = int(round(dw - 0.1)), int(round(dh - 0.1))
    out = np.full((SIZE, SIZE, 3), 114, np.uint8)
    out[top:top + nh, left:left + nw] = img
    return out, r, left, top


def letterbox_yolox(tile):
    """YOLOX's input: resized to fit SIZE x SIZE keeping proportions, grey
    (114) padding right and below only. train_meo.py uses this same function."""
    h, w = tile.shape[:2]
    r = min(SIZE / h, SIZE / w)
    nw, nh = int(w * r), int(h * r)
    out = np.full((SIZE, SIZE, 3), 114, np.uint8)
    out[:nh, :nw] = np.asarray(Image.fromarray(tile).resize((nw, nh), Image.BILINEAR))
    return out, r


def yolox_input(img):
    """SIZE x SIZE RGB uint8 -> the network's input: BGR, 0-255, as YOLOX's
    COCO weights were trained."""
    return np.ascontiguousarray(img[..., ::-1].transpose(2, 0, 1), dtype=np.float32)[None]


def _nms(boxes, scores, iou_max):
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        x0 = np.maximum(boxes[i, 0], boxes[rest, 0]); y0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x1 = np.minimum(boxes[i, 2], boxes[rest, 2]); y1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        iou = inter / (area[i] + area[rest] - inter + 1e-9)
        order = rest[iou <= iou_max]
    return keep


def detect_tiles(tiles):
    """[tile RGB uint8] -> per tile a list of (class, x0, y0, x1, y1, score)
    in the tile's own pixels."""
    s = session()
    name = s.get_inputs()[0].name
    kind = arch()
    out = []
    for tile in tiles:
        if kind == "yolox":
            img, r = letterbox_yolox(tile)
            left = top = 0
            pred = s.run(None, {name: yolox_input(img)})[0][0]   # (anchors, 4 + objectness + classes)
            cls_scores = pred[:, 5:] * pred[:, 4:5]
        else:
            img, r, left, top = _letterbox(tile)
            x = (img.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
            pred = s.run(None, {name: x})[0][0].T          # (anchors, 4 + classes)
            cls_scores = pred[:, 4:]
        cls = cls_scores.argmax(axis=1)
        score = cls_scores[np.arange(len(cls)), cls]
        m = score >= CONF
        if not m.any():
            out.append([])
            continue
        b, cls, score = pred[m, :4], cls[m], score[m]
        xyxy = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2,
                         b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], axis=1)
        # Per-class NMS, as both families do by default.
        found = []
        for c in np.unique(cls):
            idx = np.where(cls == c)[0]
            for k in _nms(xyxy[idx], score[idx], IOU[kind]):
                j = idx[k]
                x0, y0, x1, y1 = xyxy[j]
                x0, x1 = (x0 - left) / r, (x1 - left) / r
                y0, y1 = (y0 - top) / r, (y1 - top) / r
                h, w = tile.shape[:2]
                found.append((int(c), max(0.0, x0), max(0.0, y0), min(w, x1), min(h, y1), float(score[j])))
        out.append(found)
    return out


class Background:
    """The chute without the product moving over it: the per-pixel median of
    the last few photos. Larvae and ME fall through between photos (a minute
    apart, or 12 s in a burst), so they drop out of the median; what stays is
    the plate and whatever is stuck to it. Where that background is itself
    product-coloured (product_mask), it is dust -- the frass that builds up
    along the top and in the top-right corner of the rebuilt box, which the
    detector boxed as MEO: 52% of the MEO area on 2026-10-25, the ME ratio
    about 45% too high. measure() leaves boxes centred on dust, and dust
    pixels, out of its "clean" numbers.

    Kept in memory by the loop, a reduced copy of each photo (every STEP-th
    pixel, ~500 px wide -- the dust lies in patches): 8 of them are ~3 MB. Nothing until MIN photos
    from the last MAX_AGE_S are in -- after a restart or a stop, the first
    photos go without "clean"."""
    KEEP, MIN, MAX_AGE_S, WIDTH = 8, 5, 900, 500

    def __init__(self):
        self.frames = []    # (time.time(), reduced RGB)

    def step(self, w):
        return max(1, round(w / self.WIDTH))

    def median(self, shape, now):
        """The chute without the product, at the reduced size, for a photo of
        `shape`, or None until there are MIN recent photos."""
        self.frames = [(t, f) for t, f in self.frames if now - t < self.MAX_AGE_S][-self.KEEP:]
        step = self.step(shape[1])
        same = [f for _, f in self.frames if f.shape == (-(-shape[0] // step), -(-shape[1] // step), 3)]
        if len(same) < self.MIN:
            return None
        # The middle value by partition: a fraction of np.median's time on the Pi.
        return np.partition(np.stack(same), len(same) // 2, axis=0)[len(same) // 2]

    def dust(self, shape, now, med=None):
        """Dust mask at the reduced size for a photo of `shape`, or None."""
        med = self.median(shape, now) if med is None else med
        return None if med is None else product_mask(med)

    def view_check(self, shape, med, lip):
        """On this median, the metal seen ("metal", share of the frame) and
        the share of it inside the lip's line by more than VIEW_OFF_PX
        ("off"): ~0 while the camera is where the lip was fitted, None with
        too little metal to tell. None without a median or a lip."""
        if med is None or lip is None:
            return None
        step = self.step(shape[1])
        metal = _metal(med)
        rh, rw = metal.shape
        if metal.sum() < LIP_MIN * rh * rw:
            return {"metal": round(float(metal.mean()), 4), "off": None}
        h, w = shape[:2]
        v, u = np.nonzero(metal)
        # Photo px inside the line (the half-plane's normal is a unit vector
        # once b is scaled back by w / h).
        inside = w * (lip["a"] * (u + 0.5) * step / w + lip["b"] * (v + 0.5) * step / h - lip["k"])
        return {"metal": round(float(metal.mean()), 4),
                "off": round(float((inside > VIEW_OFF_PX).mean()), 3)}

    def add(self, a, now):
        step = self.step(a.shape[1])
        self.frames.append((now, np.ascontiguousarray(a[::step, ::step])))


def _components(mask, diagonal=True):
    """Connected components of a boolean mask, by runs of pixels: {label:
    [(row, first_col, last_col), ...]}. 8-way; 4-way without diagonal."""
    reach = 1 if diagonal else 0
    parent, comps, prev = {}, {}, []
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for r, row in enumerate(mask):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        here, j = [], 0
        for a, b in zip(edges[::2].tolist(), (edges[1::2] - 1).tolist()):
            label = None
            while j < len(prev) and prev[j][1] < a - reach:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] <= b + reach:
                other = find(prev[k][2])
                if label is None:
                    label = other
                elif other != label:
                    parent[other] = label
                k += 1
            if label is None:
                label = len(parent)
                parent[label] = label
            here.append((a, b, label))
            comps.setdefault(label, []).append((r, a, b))
        prev = here
    out = {}
    for label, runs in comps.items():
        out.setdefault(find(label), []).extend(runs)
    return out


def _hull(points):
    """Convex hull of integer (x, y) points, by Andrew's monotone chain."""
    points = sorted(set(points))
    if len(points) < 3:
        return points
    def half(pts):
        h = []
        for p in pts:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h[:-1]
    return half(points) + half(points[::-1])


def _neighbours(mask):
    """The nine 3 x 3 shifts of a boolean mask, padded with False."""
    p = np.pad(mask, 1)
    h, w = p.shape
    return np.stack([p[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])


def larva_length(product):
    """{"n", "len_p75"} from a photo's product mask (1332 x 990 photo): the
    isolated larvae -- a blob that is solid and elongated, so not two touching
    -- and the 75th percentile of their lengths in px. Longer larvae, lower
    bulk density: training/amas/density_study.py. len_p75 is None on a nearly
    empty chute or with under LARVA_MIN found. Caliban keeps it per photo;
    the lot's density estimate is worked out from it in Supabase."""
    if product.mean() < LARVA_MIN_SHARE:
        return {"n": 0, "len_p75": None}
    # A 3 x 3 opening clears the fines and the thin bridges between larvae.
    opened = _neighbours(_neighbours(product).all(axis=0)).any(axis=0)
    # Fill the holes: a larva's dark rings often leave some, and it is still
    # one larva. Background is 4-way, or it leaks out through diagonal steps
    # in an outline.
    h, w = opened.shape
    for runs in _components(~opened, diagonal=False).values():
        if not any(r in (0, h - 1) or a == 0 or b == w - 1 for r, a, b in runs):
            for r, a, b in runs:
                opened[r, a:b + 1] = True
    # Pixels with all four direct neighbours set: the rest are the outline.
    around = _neighbours(opened)
    inner = opened & around[1] & around[3] & around[5] & around[7]
    lengths = []
    for runs in _components(opened).values():
        pixels = sum(b - a + 1 for _, a, b in runs)
        if not 40 <= pixels <= 3400:    # the outline area is checked below
            continue
        hull = _hull([(a, r) for r, a, _ in runs] + [(b, r) for r, _, b in runs])
        if len(hull) < 3:
            continue
        # Area inside the outline through the pixel centres (Pick's theorem),
        # to compare with the hull, which runs through them too.
        outline = pixels - sum(int(inner[r, a:b + 1].sum()) for r, a, b in runs)
        area = pixels - outline / 2 - 1
        if not 40 <= area < 3000:
            continue
        xy = np.array(hull, float)
        edges = np.roll(xy, -1, axis=0) - xy
        hull_area = abs(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1])) / 2
        if hull_area <= 0:
            continue
        # The smallest rectangle around a hull has a side along one of its edges.
        u = edges / np.hypot(edges[:, 0], edges[:, 1])[:, None]
        along = np.ptp(xy @ u.T, axis=0)
        across = np.ptp(xy @ np.stack([-u[:, 1], u[:, 0]], axis=1).T, axis=0)
        k = int(np.argmin(along * across))
        long, short = max(along[k], across[k]), min(along[k], across[k])
        if short > 0 and area / hull_area >= 0.88 and 1.4 <= long / short <= 5:
            lengths.append(float(long))
    if len(lengths) < LARVA_MIN:
        return {"n": len(lengths), "len_p75": None}
    return {"n": len(lengths), "len_p75": round(float(np.percentile(lengths, 75)), 2)}


def strip_mask(h, w):
    """STRIP as a boolean mask of an h x w photo."""
    if (h, w) not in _strips:
        from PIL import ImageDraw
        im = Image.new("1", (w, h), 0)
        ImageDraw.Draw(im).polygon([(x * w, y * h) for x, y in STRIP], fill=1)
        _strips[(h, w)] = np.asarray(im, bool)
    return _strips[(h, w)]


def find_lip(med, step, shape):
    """The lip (see LIP_MIN) on a photo or Background median `med` (reduced
    by `step` from a photo of `shape`), or None when it does not show.
    Returned as the half-plane a*u + b*v < k in photo fractions (u = x / w,
    v = y / h), its angle in degrees and its share of the frame."""
    metal = _metal(med)
    rh, rw = metal.shape
    if metal.sum() < LIP_MIN * rh * rw:
        return None
    v, u = np.nonzero(metal)
    pts = np.stack([u, v], axis=1).astype(float) * step        # photo px
    c = pts.mean(axis=0)
    d = np.linalg.svd(pts - c, full_matrices=False)[2][0]       # along the lip
    nrm = np.array([-d[1], d[0]])
    h, w = shape[:2]
    if np.dot(np.array([w / 2, h / 2]) - c, nrm) < 0:
        nrm = -nrm                                              # towards the frame's centre
    # Inner edge in photo px, then the half-plane in photo fractions divided
    # by the width, so it holds at any size of the same frame.
    edge = float(np.percentile((pts - c) @ nrm, 99) + LIP_MARGIN * w + c @ nrm)
    lip = {"a": float(nrm[0]), "b": float(nrm[1] * h / w), "k": edge / w,
           "angle": round(float(np.degrees(np.arctan2(d[1], d[0])) % 180), 1)}
    share = float(lip_mask(lip, rh, rw).mean())
    if share > LIP_MAX:
        return None
    lip["share"] = round(share, 4)
    return lip


def _metal(img):
    """Bright neutral grey pixels in pieces of at least LIP_MIN of the frame
    that touch its edge: the lip (and the bar on older views). The flash's
    hot spot is as bright, and on a near-empty chute reaches the bottom edge,
    but the chute is blue: blue over red ~+19 there, ~0 on the metal
    (2026-11-14), hence LIP_BLUE_MAX."""
    f = img.astype(np.int16)
    mn, mx = f.min(axis=2), f.max(axis=2)
    grey = (mn > LIP_GREY_MIN) & (mx - mn < LIP_GREY_SPREAD) & (f[..., 2] - f[..., 0] < LIP_BLUE_MAX)
    rh, rw = grey.shape
    out = np.zeros((rh, rw), bool)
    for runs in _components(grey).values():
        if sum(b - a + 1 for _, a, b in runs) < LIP_MIN * rh * rw:
            continue
        if not any(r in (0, rh - 1) or a == 0 or b == rw - 1 for r, a, b in runs):
            continue
        for r, a, b in runs:
            out[r, a:b + 1] = True
    return out


def load_view():
    """VIEW_FILE, or DEFAULT_VIEW_FILE until a reference is taken; {} when
    neither reads. Re-read when the file changes."""
    for path in (VIEW_FILE, DEFAULT_VIEW_FILE):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if _view.get("key") != (path, mtime):
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            _view.clear()
            _view.update(key=(path, mtime), data={**data, "source": os.path.basename(path)})
        return _view["data"]
    return {}


def save_view(photo_path):
    """Fit the lip on an empty-chute photo (flashcam.py reference) and write
    VIEW_FILE. Returns the view written, or None (the old one kept) when no
    lip shows."""
    a = np.asarray(Image.open(photo_path).convert("RGB"))
    step = max(1, round(a.shape[1] / Background.WIDTH))
    lip = find_lip(a[::step, ::step], step, a.shape)
    if lip is None:
        return None
    view = {"lip": lip, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "photo": os.path.basename(photo_path)}
    with open(VIEW_FILE + ".tmp", "w") as f:
        json.dump(view, f, indent=1)
    os.replace(VIEW_FILE + ".tmp", VIEW_FILE)
    return view


def lip_mask(lip, h, w):
    """The lip's half-plane as a boolean mask of an h x w image."""
    v, u = np.mgrid[0:h, 0:w]
    return lip["a"] * (u + 0.5) / w + lip["b"] * (v + 0.5) / h < lip["k"]


_lip_cache = {}


def _lip_px(lip, h, w):
    """lip_mask at the photo's size, the last one kept (the lip rarely moves)."""
    if lip is None:
        return None
    key = (lip["a"], lip["b"], lip["k"], h, w)
    if key not in _lip_cache:
        _lip_cache.clear()
        _lip_cache[key] = lip_mask(lip, h, w)
    return _lip_cache[key]


def lip_outline(lip):
    """The lip's half-plane clipped to the photo, as (x, y) fractions."""
    def inside(p):
        return lip["a"] * p[0] + lip["b"] * p[1] < lip["k"]
    corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    out = []
    for p, q in zip(corners, corners[1:] + corners[:1]):
        if inside(p):
            out.append(p)
        if inside(p) != inside(q):
            fp = lip["a"] * p[0] + lip["b"] * p[1] - lip["k"]
            fq = lip["a"] * q[0] + lip["b"] * q[1] - lip["k"]
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return [[round(x, 4), round(y, 4)] for x, y in out]


def _half(a):
    h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
    a = a[:h, :w]
    return (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4


def _laplacian(a):
    return a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1] - 4 * a[1:-1, 1:-1]


def crispness(a, product):
    """How sharp the photo is: fine over coarse Laplacian energy (at half and
    quarter size) on the outlines of the product, so it does not depend on
    how much product there is. None with too little product. 2026-11-14:
    0.31-0.35 sharp (11-08, and from the 11-13 lens fix), 0.18 on the
    blurred 11-13 morning, +/-0.01 photo to photo. doctor.py check_focus
    compares it with the reference taken when the view was accepted."""
    g = a.astype(np.float32).mean(axis=2)
    n = _neighbours(product)
    edge = n.any(axis=0) & ~n.all(axis=0)
    g1, g2 = _half(g), _half(_half(g))
    e1 = edge[:g1.shape[0] * 2:2, :g1.shape[1] * 2:2]
    e1 = e1 | np.roll(e1, 1, axis=0) | np.roll(e1, 1, axis=1)
    e2 = e1[:g2.shape[0] * 2:2, :g2.shape[1] * 2:2]
    l1, l2 = _laplacian(g1)[e1[1:-1, 1:-1]], _laplacian(g2)[e2[1:-1, 1:-1]]
    if l1.size < 200 or l2.size < 50:
        return None
    return round(float(np.var(l1) / max(float(np.var(l2)), 1e-6)), 3)


def full_tiles(h, w):
    """The full frame's tiles as (x0, y0, x1, y1) photo px: the crop's
    ROWS x COLS first, exactly as measure() cuts them, then FULL_TOP along the
    top band and FULL_LEFT down the left strip, each the crop tiles' size."""
    ox, oy = int(w * CROP_LEFT), int(h * CROP_TOP)
    ch, cw = h - oy, w - ox
    rects = [(ox + c * cw // COLS, oy + r * ch // ROWS, ox + (c + 1) * cw // COLS, oy + (r + 1) * ch // ROWS)
             for r in range(ROWS) for c in range(COLS)]
    tw, th = cw // COLS, ch // ROWS
    rects += [(x, 0, x + tw, th) for x in (round(i * (w - tw) / (FULL_TOP - 1)) for i in range(FULL_TOP))]
    rects += [(0, y, tw, y + th) for y in (round(th + i * (h - 2 * th) / max(1, FULL_LEFT - 1)) for i in range(FULL_LEFT))]
    return rects


def owners(h, w):
    """Per pixel, the index into full_tiles() of the tile that counts it: the
    crop's own tiles inside the crop, outside it the nearest tile centre
    among the extra tiles that cover the pixel."""
    if (h, w) not in _owners:
        rects = full_tiles(h, w)
        own = np.full((h, w), -1, np.int16)
        for k, (x0, y0, x1, y1) in enumerate(rects[:ROWS * COLS]):
            own[y0:y1, x0:x1] = k
        v, u = np.mgrid[0:h, 0:w]
        best = np.full((h, w), np.inf)
        for k, (x0, y0, x1, y1) in enumerate(rects[ROWS * COLS:], ROWS * COLS):
            d = np.hypot(u - (x0 + x1) / 2, v - (y0 + y1) / 2)
            take = (own < ROWS * COLS) & (own != -1)
            ok = (u >= x0) & (u < x1) & (v >= y0) & (v < y1) & ~take & (d < best)
            own[ok], best[ok] = k, d[ok]
        _owners[(h, w)] = own
    return _owners[(h, w)]


def measure(path, background=None, at=None, extras=True):
    """One photo -> {"meo_px", "meo_n", "product_px", "ms", "boxes"};
    "strip": the same three counts without STRIP (boxes centred in it and its
    product pixels); "area" and "area_strip": MEO and product pixels as
    surface (AREA_WARM), recorded only; and with a Background that has enough
    photos, "clean": the same without the dust (see Background). The photo
    then joins the background. "bicolore": the Bicolore boxes' count, the
    area they cover (each pixel once) and the boxes themselves, shown only,
    never in the MEO numbers. "plastique": the Plastique boxes' count and
    the boxes, recorded the same way. "larve": larva_length(), for the lot's density
    estimate.

    "full": meo_px / meo_n / product_px over the whole frame without the
    lip (full_tiles, load_view), and "boxes" the MEO boxes it adds outside
    the crop; recorded beside the crop's numbers, which still decide.
    "view": with a Background, view_check() -- is the lip still where it
    was fitted. "crisp": crispness() over the whole frame, for the focus
    check.

    boxes: each MEO box as [x0, y0, x1, y1, score], x and y as fractions of
    the whole photo's width and height -- the same on the full photo and on
    Caliban's smaller review copy -- so SGSC can draw what was counted.

    at: when the photo was taken (time.time() scale), for the background's
    age limit; now when not given -- right on Ariel, which measures as it
    shoots. lot_estimate.py passes it, measuring days-old photos.

    extras=False leaves out "full" and "larve", the two recorded-only
    studies: half the model runs (the full frame's extra tiles) and the
    larva search: about half the time of a photo (2026-11-16 profile). The loop asks for them on
    every EXTRAS_EVERY-th photo (loop.py); everything that decides, and the
    camera checks ("view", "crisp"), is the same either way."""
    t0 = time.monotonic()
    a = np.asarray(Image.open(path).convert("RGB"))
    h, w = a.shape[:2]
    now = time.time() if at is None else at
    med = background.median(a.shape, now) if background is not None else None
    dust = product_mask(med) if med is not None else None
    step = background.step(w) if background is not None else 1
    lip = load_view().get("lip")
    lip_px = _lip_px(lip, h, w)
    # MEO area is the area the boxes cover, each pixel once: the model often
    # boxes one clump two or three times (a box inside a bigger one has a low
    # IoU, so NMS keeps both), and summing them read 13% more MEO than the
    # boxes cover on 1205 photos of 2026-10-24..26, up to 50% on a clumpy one.
    covered, covered_clean, covered_strip, covered_bi, covered_full = (np.zeros((h, w), bool) for _ in range(5))
    clean_n = strip_n = full_n = 0
    full_boxes = []
    strip = strip_mask(h, w)
    crop = a[int(h * CROP_TOP):, int(w * CROP_LEFT):]
    ch, cw = crop.shape[:2]
    tiles = [np.ascontiguousarray(crop[r * ch // ROWS:(r + 1) * ch // ROWS, c * cw // COLS:(c + 1) * cw // COLS])
             for r in range(ROWS) for c in range(COLS)]
    rects = full_tiles(h, w)
    if extras:
        tiles += [np.ascontiguousarray(a[y0:y1, x0:x1]) for x0, y0, x1, y1 in rects[ROWS * COLS:]]
        own = owners(h, w)
    meo_n, boxes, bi_boxes, pl_boxes = 0, [], [], []
    ox, oy = int(w * CROP_LEFT), int(h * CROP_TOP)
    for k, found in enumerate(detect_tiles(tiles)):
        tx, ty = rects[k][:2]
        in_crop = k < ROWS * COLS
        for c, x0, y0, x1, y1, score in found:
            cy, cx = min(h - 1, int(ty + (y0 + y1) / 2)), min(w - 1, int(tx + (x0 + x1) / 2))
            if extras and c == MEO and own[cy, cx] == k and not (lip_px is not None and lip_px[cy, cx]):
                covered_full[max(0, int(round(ty + y0))):int(round(ty + y1)),
                             max(0, int(round(tx + x0))):int(round(tx + x1))] = True
                full_n += 1
                if not in_crop:
                    full_boxes.append([round(float(tx + x0) / w, 4), round(float(ty + y0) / h, 4),
                                       round(float(tx + x1) / w, 4), round(float(ty + y1) / h, 4),
                                       round(float(score), 2)])
            if not in_crop:
                continue
            if c == BICOLORE:
                covered_bi[max(0, int(round(ty + y0))):int(round(ty + y1)),
                           max(0, int(round(tx + x0))):int(round(tx + x1))] = True
                bi_boxes.append([round(float(tx + x0) / w, 4), round(float(ty + y0) / h, 4),
                                 round(float(tx + x1) / w, 4), round(float(ty + y1) / h, 4), round(float(score), 2)])
            if c == PLASTIQUE:
                pl_boxes.append([round(float(tx + x0) / w, 4), round(float(ty + y0) / h, 4),
                                 round(float(tx + x1) / w, 4), round(float(ty + y1) / h, 4), round(float(score), 2)])
            if c == MEO:
                cover = (slice(max(0, int(round(ty + y0))), int(round(ty + y1))),
                         slice(max(0, int(round(tx + x0))), int(round(tx + x1))))
                covered[cover] = True
                meo_n += 1
                if not strip[min(h - 1, int(ty + (y0 + y1) / 2)), min(w - 1, int(tx + (x0 + x1) / 2))]:
                    covered_strip[cover] = True
                    strip_n += 1
                if dust is not None and not dust[min(dust.shape[0] - 1, int((ty + (y0 + y1) / 2) // step)),
                                                 min(dust.shape[1] - 1, int((tx + (x0 + x1) / 2) // step))]:
                    covered_clean[cover] = True
                    clean_n += 1
                # Plain floats: numpy's do not go through json.dumps.
                boxes.append([round(float(tx + x0) / w, 4), round(float(ty + y0) / h, 4),
                              round(float(tx + x1) / w, 4), round(float(ty + y1) / h, 4), round(float(score), 2)])
    boxes = sorted(boxes, key=lambda b: -b[4])[:MAX_BOXES]
    product_all = product_mask(a)
    product = product_all[oy:, ox:]
    not_strip = ~strip[oy:, ox:]
    out = {"meo_px": float(covered.sum()), "meo_n": int(meo_n), "product_px": int(product.sum()),
           "boxes": boxes,
           "strip": {"meo_px": float(covered_strip.sum()), "meo_n": int(strip_n),
                     "product_px": int((product & not_strip).sum())},
           "bicolore": {"n": len(bi_boxes), "px": int(covered_bi.sum()),
                        "boxes": sorted(bi_boxes, key=lambda b: -b[4])[:MAX_BOXES]},
           "plastique": {"n": len(pl_boxes), "boxes": sorted(pl_boxes, key=lambda b: -b[4])[:MAX_BOXES]}}
    # Never at the cost of the reading: the MEO numbers above decide, this
    # only feeds an estimate.
    if extras:
        try:
            out["larve"] = larva_length(product)
        except Exception as e:
            print(f"larva_length failed: {type(e).__name__}: {e}", flush=True)
            out["larve"] = {"n": 0, "len_p75": None}
    # Surface area: MEO pixels are the warm ones inside the boxes, product
    # the larvae and that MEO (dark frass mostly fails product_mask).
    f = crop.astype(np.int16)
    meo_area = covered[oy:, ox:] & (f[..., 0] > f[..., 2] + AREA_WARM)
    meo_area_strip = covered_strip[oy:, ox:] & meo_area
    out["area"] = {"meo_px": int(meo_area.sum()), "product_px": int((product | meo_area).sum())}
    out["area_strip"] = {"meo_px": int(meo_area_strip.sum()),
                         "product_px": int(((product | meo_area) & not_strip).sum())}
    if extras:
        product_full = product_all if lip_px is None else product_all & ~lip_px
        out["full"] = {"meo_px": float(covered_full.sum()), "meo_n": int(full_n),
                       "product_px": int(product_full.sum()),
                       "boxes": sorted(full_boxes, key=lambda b: -b[4])[:MAX_BOXES]}
    if background is not None:
        view = background.view_check(a.shape, med, lip)
        if view is not None:
            out["view"] = view
    try:
        out["crisp"] = crispness(a, product_all)
    except Exception as e:
        print(f"crispness failed: {type(e).__name__}: {e}", flush=True)
        out["crisp"] = None
    if dust is not None:
        full = dust.repeat(step, axis=0).repeat(step, axis=1)[int(h * CROP_TOP):h, int(w * CROP_LEFT):w]
        out["clean"] = {"meo_px": float(covered_clean.sum()), "meo_n": int(clean_n),
                        "product_px": int((product & ~full).sum())}
    if background is not None:
        background.add(a, now)
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(os.path.basename(p), measure(p), flush=True)
