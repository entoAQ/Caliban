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
    """a: H x W x 3 RGB uint8. Warm, saturated, not dark (flashcam.product_share)."""
    f = a.astype(np.float32)
    mx, mn = f.max(axis=2), f.min(axis=2)
    return ((mx - mn) / np.maximum(mx, 1) > 0.28) & (f[..., 0] > f[..., 2] + 15) & (mx > 50)


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

    def dust(self, shape, now):
        """Dust mask at the reduced size for a photo of `shape`, or None."""
        self.frames = [(t, f) for t, f in self.frames if now - t < self.MAX_AGE_S][-self.KEEP:]
        step = self.step(shape[1])
        same = [f for _, f in self.frames if f.shape == (-(-shape[0] // step), -(-shape[1] // step), 3)]
        if len(same) < self.MIN:
            return None
        # The middle value by partition: a fraction of np.median's time on the Pi.
        return product_mask(np.partition(np.stack(same), len(same) // 2, axis=0)[len(same) // 2])

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


def measure(path, background=None, at=None):
    """One photo -> {"meo_px", "meo_n", "product_px", "ms", "boxes"};
    "strip": the same three counts without STRIP (boxes centred in it and its
    product pixels); "area" and "area_strip": MEO and product pixels as
    surface (AREA_WARM), recorded only; and with a Background that has enough
    photos, "clean": the same without the dust (see Background). The photo
    then joins the background. "bicolore": the Bicolore boxes' count, the
    area they cover (each pixel once) and the boxes themselves, shown only,
    never in the MEO numbers. "larve": larva_length(), for the lot's density
    estimate.

    boxes: each MEO box as [x0, y0, x1, y1, score], x and y as fractions of
    the whole photo's width and height -- the same on the full photo and on
    Caliban's smaller review copy -- so SGSC can draw what was counted.

    at: when the photo was taken (time.time() scale), for the background's
    age limit; now when not given -- right on Ariel, which measures as it
    shoots. lot_estimate.py passes it, measuring days-old photos."""
    t0 = time.monotonic()
    a = np.asarray(Image.open(path).convert("RGB"))
    h, w = a.shape[:2]
    now = time.time() if at is None else at
    dust = background.dust(a.shape, now) if background is not None else None
    step = background.step(w) if background is not None else 1
    # MEO area is the area the boxes cover, each pixel once: the model often
    # boxes one clump two or three times (a box inside a bigger one has a low
    # IoU, so NMS keeps both), and summing them read 13% more MEO than the
    # boxes cover on 1205 photos of 2026-10-24..26, up to 50% on a clumpy one.
    covered, covered_clean, covered_strip, covered_bi = (np.zeros((h, w), bool) for _ in range(4))
    clean_n = strip_n = 0
    strip = strip_mask(h, w)
    crop = a[int(h * CROP_TOP):, int(w * CROP_LEFT):]
    ch, cw = crop.shape[:2]
    tiles = [np.ascontiguousarray(crop[r * ch // ROWS:(r + 1) * ch // ROWS, c * cw // COLS:(c + 1) * cw // COLS])
             for r in range(ROWS) for c in range(COLS)]
    meo_n, boxes, bi_boxes = 0, [], []
    ox, oy = int(w * CROP_LEFT), int(h * CROP_TOP)
    for k, found in enumerate(detect_tiles(tiles)):
        tx, ty = ox + (k % COLS) * cw // COLS, oy + (k // COLS) * ch // ROWS
        for c, x0, y0, x1, y1, score in found:
            if c == BICOLORE:
                covered_bi[max(0, int(round(ty + y0))):int(round(ty + y1)),
                           max(0, int(round(tx + x0))):int(round(tx + x1))] = True
                bi_boxes.append([round(float(tx + x0) / w, 4), round(float(ty + y0) / h, 4),
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
    product = product_mask(crop)
    not_strip = ~strip[oy:, ox:]
    out = {"meo_px": float(covered.sum()), "meo_n": int(meo_n), "product_px": int(product.sum()),
           "boxes": boxes,
           "strip": {"meo_px": float(covered_strip.sum()), "meo_n": int(strip_n),
                     "product_px": int((product & not_strip).sum())},
           "bicolore": {"n": len(bi_boxes), "px": int(covered_bi.sum()),
                        "boxes": sorted(bi_boxes, key=lambda b: -b[4])[:MAX_BOXES]}}
    # Never at the cost of the reading: the MEO numbers above decide, this
    # only feeds an estimate.
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
