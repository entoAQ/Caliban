#!/usr/bin/env python3
"""Train the MEO detector on the exported tiles, and check it per lot.

Run export_amas.py first. Trains a YOLOX-Tiny detector (or Nano: about half
the training time and a third of Ariel's time, less exact) on
INLINE_LABELING_DIR/amas_yolo (CPU is enough at this size), exports it for
Ariel, then for the val lots -- lots it never saw in training -- sets the
model's MEO area over product area beside the hand-boxed ratio and the lab
ME%. The model is only worth using where it tracks the hand boxes; whether
the hand boxes track the lab is export_amas.py's lots.csv.

Why YOLOX: it is Apache-2.0 (Megvii's code and COCO weights; the package is
Pixeltable's maintained copy), so the model trained here is ours to run in
production and to keep private. The YOLO11n models before it came from
Ultralytics' package, AGPL-3.0, which Ultralytics reads as covering trained
weights too. Nothing here or on Ariel uses Ultralytics any more.

Runs land in INLINE_LABELING_DIR/amas_runs/<name>/: weights/last.pth (the
smoothed weights at the last round), and ariel/meo.onnx + meo.json -- the
model as Ariel runs it, checked here with Ariel's own meo_detect.py.

Needs: pip install --no-deps pixeltable-yolox
       pip install loguru tabulate thop pycocotools opencv-python onnx onnxruntime
(--no-deps: two of its listed extras, for its own export tool, do not
install on Python 3.14 and are not needed here.)
The COCO starting weights (40 MB for Tiny, Apache-2.0) are downloaded once from
YOLOX's GitHub releases into ~/.cache/yolox/weights.

Usage:
    python train_meo.py                   # 80 epochs, YOLOX-Tiny
    python train_meo.py --epochs 3 --name smoke
    python train_meo.py --arch yolox-nano

Cut off part-way, the same command again carries on from the last round
finished (weights/resume.pth).
"""

import argparse
import csv
import json
import math
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "labelstudio"))
sys.path.insert(0, str(HERE.parents[1] / "rig" / "inline"))
from common import ROOT  # noqa: E402
import meo_detect  # noqa: E402

DATA = ROOT / "amas_yolo"
TILES = ROOT / "amas" / "tiles"
RUNS = ROOT / "amas_runs"
ARCHES = ("yolox-tiny", "yolox-nano")
WEIGHTS_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/{}.pth"
CLASSES = 5  # MEO / Peau / Incertain / Bicolore / Poussière (data.yaml)
SIZE = meo_detect.SIZE
MAX_LABELS = 120


def load_split(split):
    """[(RGB uint8 tile, boxes as N x 5 [class, x0, y0, x1, y1] in tile px)]"""
    out = []
    for img in sorted((DATA / "images" / split).glob("*.jpg")):
        a = np.asarray(Image.open(img).convert("RGB"))
        h, w = a.shape[:2]
        rows = []
        lbl = DATA / "labels" / split / (img.stem + ".txt")
        for line in (lbl.read_text().splitlines() if lbl.exists() else []):
            c, cx, cy, bw, bh = map(float, line.split())
            rows.append([c, (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h])
        out.append((a, np.array(rows, np.float32).reshape(-1, 5)))
    return out


def jitter_colour(a, rng):
    """Small colour changes: colour is half of telling frass from larva, so
    hue barely moves (about 1 degree); saturation +-30 %, brightness +-20 %."""
    import cv2
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + rng.uniform(-1, 1)) % 180
    hsv[..., 1] = np.clip(hsv[..., 1] * rng.uniform(0.7, 1.3), 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * rng.uniform(0.8, 1.2), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def placed(tile, boxes, scale):
    """Tile resized by scale, boxes with it."""
    h, w = tile.shape[:2]
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    img = np.asarray(Image.fromarray(tile).resize((nw, nh), Image.BILINEAR))
    b = boxes.copy()
    b[:, 1:] *= scale
    return img, b


def mosaic(data, rng):
    """Four tiles around a random centre on a 2 x SIZE canvas, then a SIZE
    window over the centre: more pieces and edges per image, with pieces at
    the size the model sees on Ariel (about +-25 %)."""
    canvas = np.full((2 * SIZE, 2 * SIZE, 3), 114, np.uint8)
    xc, yc = int(rng.uniform(0.6, 1.4) * SIZE), int(rng.uniform(0.6, 1.4) * SIZE)
    all_boxes = []
    for k in range(4):
        tile, boxes = data[rng.randrange(len(data))]
        img, b = placed(tile, boxes, min(SIZE / tile.shape[0], SIZE / tile.shape[1]) * rng.uniform(0.8, 1.25))
        h, w = img.shape[:2]
        x0 = xc - w if k in (0, 2) else xc
        y0 = yc - h if k in (0, 1) else yc
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x0 + w, 2 * SIZE), min(y0 + h, 2 * SIZE)
        canvas[cy0:cy1, cx0:cx1] = img[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        b[:, [1, 3]] += x0
        b[:, [2, 4]] += y0
        all_boxes.append(b)
    wx = int(np.clip(xc - SIZE // 2 + rng.uniform(-SIZE / 4, SIZE / 4), 0, SIZE))
    wy = int(np.clip(yc - SIZE // 2 + rng.uniform(-SIZE / 4, SIZE / 4), 0, SIZE))
    b = np.concatenate(all_boxes)
    b[:, [1, 3]] -= wx
    b[:, [2, 4]] -= wy
    return canvas[wy:wy + SIZE, wx:wx + SIZE], b


def plain(data, i):
    tile, boxes = data[i]
    img, r = meo_detect.letterbox_yolox(tile)
    b = boxes.copy()
    b[:, 1:] *= r
    return img, b


def sample(data, i, rng, use_mosaic):
    img, b = mosaic(data, rng) if use_mosaic and rng.random() < 0.5 else plain(data, i)
    img = np.ascontiguousarray(img)
    if rng.random() < 0.5:  # the chute has no left/right or up/down for a clump
        img = img[:, ::-1]
        b[:, [1, 3]] = SIZE - b[:, [3, 1]]
    if rng.random() < 0.5:
        img = img[::-1]
        b[:, [2, 4]] = SIZE - b[:, [4, 2]]
    img = jitter_colour(np.ascontiguousarray(img), rng)
    # Keep what is still mostly in view and not a sliver.
    if len(b):
        area = (b[:, 3] - b[:, 1]) * (b[:, 4] - b[:, 2])
        b[:, 1:] = np.clip(b[:, 1:], 0, SIZE)
        kept = (b[:, 3] - b[:, 1]) * (b[:, 4] - b[:, 2])
        b = b[(kept > 0.5 * np.maximum(area, 1e-6)) & (b[:, 3] - b[:, 1] > 2) & (b[:, 4] - b[:, 2] > 2)]
    target = np.zeros((MAX_LABELS, 5), np.float32)
    b = b[:MAX_LABELS]
    if len(b):
        # YOLOX's labels: class, centre x, centre y, width, height (input px).
        target[:len(b)] = np.stack([b[:, 0], (b[:, 1] + b[:, 3]) / 2, (b[:, 2] + b[:, 4]) / 2,
                                    b[:, 3] - b[:, 1], b[:, 4] - b[:, 2]], axis=1)
    return meo_detect.yolox_input(img)[0], target


def build_model(arch):
    import torch
    from yolox.config import YoloxConfig
    from yolox.utils import load_checkpoint
    name = arch.replace("-", "_")
    cfg = YoloxConfig.get_named_config(name)
    cfg.num_classes = CLASSES
    model = cfg.get_model()
    start = Path.home() / ".cache" / "yolox" / "weights" / f"{name}.pth"
    if not start.exists():
        start.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading the COCO starting weights from {WEIGHTS_URL.format(name)}", flush=True)
        urllib.request.urlretrieve(WEIGHTS_URL.format(name), f"{start}.tmp")
        os.replace(f"{start}.tmp", start)
    own = model.state_dict()
    coco = {k: v for k, v in load_checkpoint(start)["model"].items() if k in own and v.shape == own[k].shape}
    model.load_state_dict(coco, strict=False)
    print(f"started from COCO weights: {len(coco)}/{len(own)} tensors "
          f"(the rest is the 4-class output, learnt here)", flush=True)
    return model, torch


def optimiser(model, torch, lr):
    no_decay, decay, biases = [], [], []
    for k, v in model.named_modules():
        if hasattr(v, "bias") and isinstance(v.bias, torch.nn.Parameter):
            biases.append(v.bias)
        if isinstance(v, torch.nn.BatchNorm2d) or "bn" in k:
            no_decay.append(v.weight)
        elif hasattr(v, "weight") and isinstance(v.weight, torch.nn.Parameter):
            decay.append(v.weight)
    opt = torch.optim.SGD(no_decay, lr=lr, momentum=0.9, nesterov=True)
    opt.add_param_group({"params": decay, "weight_decay": 5e-4})
    opt.add_param_group({"params": biases})
    return opt


def export(model, torch, path):
    from yolox.models.network_blocks import SiLU
    from yolox.utils import replace_module
    model = replace_module(model, torch.nn.SiLU, SiLU).eval()
    model.head.decode_in_inference = True  # boxes come out decoded, as meo_detect reads them
    torch.onnx.export(model, torch.zeros(1, 3, SIZE, SIZE), str(path), input_names=["images"],
                      output_names=["output"], opset_version=12, dynamo=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--name", default="meo")
    ap.add_argument("--arch", choices=ARCHES, default="yolox-tiny")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--conf", type=float, default=0.25, help="detection threshold for the lot check")
    args = ap.parse_args()

    model, torch = build_model(args.arch)
    from yolox.utils import LRScheduler, ModelEMA
    torch.set_num_threads(os.cpu_count() or 4)
    torch.manual_seed(0)
    rng = random.Random(0)
    train = load_split("train")
    print(f"{len(train)} training tiles, {sum(len(b) for _, b in train)} boxes", flush=True)

    out = RUNS / args.name
    (out / "weights").mkdir(parents=True, exist_ok=True)
    per_epoch = math.ceil(len(train) / args.batch)
    lr = 0.01 / 64 * args.batch
    # The last rounds without mosaics, on tiles as Ariel sees them (YOLOX's
    # recipe: no_aug_epochs).
    plain_epochs = max(1, round(args.epochs * 0.15)) if args.epochs >= 4 else 0
    sched = LRScheduler("yoloxwarmcos", lr, per_epoch, args.epochs, warmup_epochs=min(3, args.epochs // 4),
                        warmup_lr_start=0, no_aug_epochs=plain_epochs, min_lr_ratio=0.05)
    opt = optimiser(model, torch, lr)
    ema = ModelEMA(model, 0.9998)
    step, first = 0, 0
    # Saved after every round, so a run cut off (laptop asleep, window
    # closed) carries on from its last round when started again with the
    # same --name, instead of from the beginning.
    state = out / "weights" / "resume.pth"
    if state.exists():
        st = torch.load(state, weights_only=False)
        if st["epochs"] == args.epochs and st["arch"] == args.arch:
            model.load_state_dict(st["model"])
            ema.ema.load_state_dict(st["ema"])
            ema.updates = st["ema_updates"]
            opt.load_state_dict(st["opt"])
            rng.setstate(st["rng"])
            torch.set_rng_state(st["torch_rng"])
            step, first = st["step"], st["epoch"]
            print(f"carrying on after round {first}/{args.epochs}", flush=True)
    with open(out / "results.csv", "a" if first else "w", newline="", encoding="utf-8") as f:
        log = csv.writer(f)
        if not first:
            log.writerow(["epoch", "loss", "iou_loss", "conf_loss", "cls_loss", "seconds"])
        for epoch in range(first, args.epochs):
            model.train()
            use_mosaic = epoch < args.epochs - plain_epochs
            order = list(range(len(train)))
            rng.shuffle(order)
            t0, sums = time.monotonic(), np.zeros(4)
            for s in range(0, len(order), args.batch):
                xs, ts = zip(*(sample(train, i, rng, use_mosaic) for i in order[s:s + args.batch]))
                for g in opt.param_groups:
                    g["lr"] = sched.update_lr(step + 1)
                losses = model(torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ts)))
                opt.zero_grad()
                losses["total_loss"].backward()
                opt.step()
                ema.update(model)
                step += 1
                sums += [float(losses[k].detach()) for k in ("total_loss", "iou_loss", "conf_loss", "cls_loss")]
            n = math.ceil(len(order) / args.batch)
            secs = time.monotonic() - t0
            log.writerow([epoch + 1, *[f"{v / n:.4f}" for v in sums], f"{secs:.0f}"])
            f.flush()
            print(f"  round {epoch + 1}/{args.epochs}  loss {sums[0] / n:.3f}  "
                  f"{'mosaic' if use_mosaic else 'plain'}  {secs:.0f} s", flush=True)
            torch.save({"epochs": args.epochs, "arch": args.arch, "epoch": epoch + 1, "step": step,
                        "model": model.state_dict(), "ema": ema.ema.state_dict(), "ema_updates": ema.updates,
                        "opt": opt.state_dict(), "rng": rng.getstate(), "torch_rng": torch.get_rng_state()},
                       f"{state}.tmp")
            os.replace(f"{state}.tmp", state)

    best = ema.ema
    torch.save({"model": best.state_dict(), "arch": args.arch, "classes": CLASSES}, out / "weights" / "last.pth")
    ariel = out / "ariel"
    ariel.mkdir(exist_ok=True)
    export(best, torch, ariel / "meo.onnx")
    # retrain.ps1 adds the fitted scale once lot_estimate.py has it.
    (ariel / "meo.json").write_text(json.dumps(
        {"version": args.name, "arch": args.arch, "license": "Apache-2.0 (YOLOX)", "trained_on_tiles": len(train)},
        indent=1), encoding="utf-8")
    print(f"model for Ariel: {ariel / 'meo.onnx'}", flush=True)

    # The lot check runs the exported model through Ariel's own code.
    meo_detect.use(ariel / "meo.onnx")
    meo_detect.CONF = args.conf
    index = json.loads((TILES / "tiles.json").read_text(encoding="utf-8"))
    lots = {}
    for img in sorted((DATA / "images" / "val").glob("*.jpg")):
        d = index[img.name]
        hand = 0.0
        for line in (DATA / "labels" / "val" / (img.stem + ".txt")).read_text().splitlines():
            c, _, _, w, h = line.split()
            if c == "0":
                hand += float(w) * float(h) * d["width"] * d["height"]
        found = meo_detect.detect_tiles([np.asarray(Image.open(img).convert("RGB"))])[0]
        model_px = sum((x1 - x0) * (y1 - y0) for c, x0, y0, x1, y1, _ in found if c == meo_detect.MEO)
        s = lots.setdefault(d["lot_number"], {"lab": d["lab_me_pct"], "n": 0, "hand": 0.0, "model": 0.0, "prod": 0})
        s["n"] += 1
        s["hand"] += hand
        s["model"] += model_px
        s["prod"] += d["product_px"]

    path = out / "val_lots.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lot", "tiles", "hand_ratio_pct", "model_ratio_pct", "lab_me_pct"])
        for lot, s in sorted(lots.items()):
            hr = 100 * s["hand"] / s["prod"] if s["prod"] else 0
            mr = 100 * s["model"] / s["prod"] if s["prod"] else 0
            w.writerow([lot, s["n"], f"{hr:.2f}", f"{mr:.2f}", s["lab"]])
            print(f"{lot}  {s['n']:4d} tiles  hand {hr:6.2f}%  model {mr:6.2f}%  lab {s['lab']:5.2f}")
    print(f"Written to {path}")


if __name__ == "__main__":
    main()
