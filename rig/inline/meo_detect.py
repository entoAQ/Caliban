#!/usr/bin/env python3
"""MEO detector for the inline rig: the YOLO model trained on the boxed tiles
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
IOU = 0.7
# Boxes sent per photo for review (SGSC draws them on the photo); a photo
# with more than this keeps the most confident ones. The sums use them all.
MAX_BOXES = 150
MEO = 0  # class 0 of MEO / Peau / Incertain (/ Prépupe from the next retrain)

_session = None


def version():
    try:
        with open(VERSION_FILE) as f:
            return json.load(f).get("version") or "meo"
    except (OSError, ValueError):
        return "meo"


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


def _nms(boxes, scores):
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
        order = rest[iou <= IOU]
    return keep


def detect_tiles(tiles):
    """[tile RGB uint8] -> per tile a list of (class, x0, y0, x1, y1, score)
    in the tile's own pixels."""
    s = session()
    name = s.get_inputs()[0].name
    out = []
    for tile in tiles:
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
        # Per-class NMS, as ultralytics does by default.
        found = []
        for c in np.unique(cls):
            idx = np.where(cls == c)[0]
            for k in _nms(xyxy[idx], score[idx]):
                j = idx[k]
                x0, y0, x1, y1 = xyxy[j]
                x0, x1 = (x0 - left) / r, (x1 - left) / r
                y0, y1 = (y0 - top) / r, (y1 - top) / r
                h, w = tile.shape[:2]
                found.append((int(c), max(0.0, x0), max(0.0, y0), min(w, x1), min(h, y1), float(score[j])))
        out.append(found)
    return out


def measure(path):
    """One photo -> {"meo_px", "meo_n", "product_px", "ms", "boxes"}.

    boxes: each MEO box as [x0, y0, x1, y1, score], x and y as fractions of
    the whole photo's width and height -- the same on the full photo and on
    Caliban's smaller review copy -- so SGSC can draw what was counted."""
    t0 = time.monotonic()
    a = np.asarray(Image.open(path).convert("RGB"))
    h, w = a.shape[:2]
    crop = a[int(h * CROP_TOP):, int(w * CROP_LEFT):]
    ch, cw = crop.shape[:2]
    tiles = [np.ascontiguousarray(crop[r * ch // ROWS:(r + 1) * ch // ROWS, c * cw // COLS:(c + 1) * cw // COLS])
             for r in range(ROWS) for c in range(COLS)]
    meo_px, meo_n, boxes = 0.0, 0, []
    ox, oy = int(w * CROP_LEFT), int(h * CROP_TOP)
    for k, found in enumerate(detect_tiles(tiles)):
        tx, ty = ox + (k % COLS) * cw // COLS, oy + (k // COLS) * ch // ROWS
        for c, x0, y0, x1, y1, score in found:
            if c == MEO:
                meo_px += (x1 - x0) * (y1 - y0)
                meo_n += 1
                # Plain floats: numpy's do not go through json.dumps.
                boxes.append([round(float(tx + x0) / w, 4), round(float(ty + y0) / h, 4),
                              round(float(tx + x1) / w, 4), round(float(ty + y1) / h, 4), round(float(score), 2)])
    boxes = sorted(boxes, key=lambda b: -b[4])[:MAX_BOXES]
    return {"meo_px": round(float(meo_px), 1), "meo_n": int(meo_n), "product_px": int(product_mask(crop).sum()),
            "ms": int((time.monotonic() - t0) * 1000), "boxes": boxes}


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(os.path.basename(p), measure(p), flush=True)
