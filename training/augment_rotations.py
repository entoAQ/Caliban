#!/usr/bin/env python3
"""Turn one tagged tray photo into eight, for free.

Why this is valid here specifically: a tray of scattered larvae has no
canonical orientation -- turning the photo or mirroring it doesn't change
what's true about the sample, only how it's presented. That's the exact
reasoning app/main.py's TRANSFORMS list already relies on for the MEO
rotation-averaging estimate (see the comment above it in azure_band_test).
The same eight transforms applied there to the IMAGE are applied here to a
CVAT-exported YOLO-segmentation LABEL alongside it, so one manually-tagged
photo yields eight fully-accurate training examples instead of one -- no
extra tagging, just geometry.

Do not point this at a photo where orientation DOES mean something (a
labelled diagram, text, anything with a top). It is only valid for the
loose-material tray photos this project actually tags.

Input layout (matches a standard YOLO-seg export):
    images/<name>.jpg
    labels/<name>.txt      -- one line per object: "class_id x1 y1 x2 y2 ..."
                               (polygon points, normalized 0-1)

Output: the same layout, eight files per input, suffixed by transform
    (_r0, _r90, _r180, _r270, _r0m, _r90m, _r180m, _r270m).

Usage:
    python3 augment_rotations.py --images images/ --labels labels/ --out augmented/
"""

import argparse
from pathlib import Path

from PIL import Image

# Identical to TRANSFORMS in app/main.py's azure_band_test -- ordered the
# same way for the same reason (0/180 most different first), though order
# doesn't matter for augmentation the way it does for spending rotation
# budget wisely on a live estimate.
TRANSFORMS = [
    (0, False), (180, False), (90, False), (270, False),
    (0, True), (180, True), (90, True), (270, True),
]


def _rotate_point(nx, ny, angle):
    """Map a normalized (x, y) point through the same rotation PIL's
    Image.rotate(angle, expand=True) applies to the image itself.

    Verified empirically against PIL, not derived by hand -- a marker pixel
    at a known position was rotated and its output position measured for
    each of the four angles before writing these formulas. Continuous
    (normalized) form of the discrete pixel mapping PIL actually produced:

        angle=0:    unchanged
        angle=180:  (x, y) -> (1-x, 1-y)
        angle=90:   (x, y) -> (y, 1-x)      -- width/height swap
        angle=270:  (x, y) -> (1-y, x)      -- width/height swap

    Normalized coordinates make the swapped-dimension cases (90/270) work
    out to the same formula regardless of the image's actual aspect ratio,
    which is why this operates on normalized points rather than pixels.
    """
    if angle == 0:
        return nx, ny
    if angle == 180:
        return 1 - nx, 1 - ny
    if angle == 90:
        return ny, 1 - nx
    if angle == 270:
        return 1 - ny, nx
    raise ValueError(f"Only 0/90/180/270 are valid (multiples of 90, no interpolation); got {angle}")


def _mirror_point(nx, ny):
    """Horizontal flip, applied AFTER rotation -- must match the image
    transform's own order (rotate, then transpose(FLIP_LEFT_RIGHT)), or the
    mask and the photo silently stop agreeing with each other."""
    return 1 - nx, ny


def transform_image(img, angle, mirror):
    out = img.rotate(angle, expand=True)
    if mirror:
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    return out


def transform_label_file(lines, angle, mirror):
    """lines: raw lines from a YOLO-seg .txt file. Returns transformed lines."""
    out_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        class_id = parts[0]
        coords = [float(v) for v in parts[1:]]
        if len(coords) % 2 != 0:
            raise ValueError(f"Odd number of coordinates in line: {line!r}")
        new_coords = []
        for i in range(0, len(coords), 2):
            nx, ny = coords[i], coords[i + 1]
            nx, ny = _rotate_point(nx, ny, angle)
            if mirror:
                nx, ny = _mirror_point(nx, ny)
            # Clamp -- floating point can push a point that started exactly
            # on an edge (0.0 or 1.0) a hair outside after two transforms,
            # which some training loaders reject outright.
            nx = min(1.0, max(0.0, nx))
            ny = min(1.0, max(0.0, ny))
            new_coords.append(f"{nx:.6f}")
            new_coords.append(f"{ny:.6f}")
        out_lines.append(class_id + " " + " ".join(new_coords))
    return out_lines


def _suffix(angle, mirror):
    return f"_r{angle}" + ("m" if mirror else "")


def augment_one(image_path, label_path, out_images_dir, out_labels_dir):
    img = Image.open(image_path).convert("RGB")
    lines = label_path.read_text().splitlines() if label_path.exists() else []

    written = 0
    for angle, mirror in TRANSFORMS:
        suffix = _suffix(angle, mirror)
        out_img = transform_image(img, angle, mirror)
        out_img_path = out_images_dir / f"{image_path.stem}{suffix}{image_path.suffix}"
        out_img.save(out_img_path, quality=95)

        out_lines = transform_label_file(lines, angle, mirror)
        out_label_path = out_labels_dir / f"{label_path.stem}{suffix}.txt"
        out_label_path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""))
        written += 1
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True, type=Path, help="directory of source images")
    ap.add_argument("--labels", required=True, type=Path, help="directory of matching YOLO-seg .txt labels")
    ap.add_argument("--out", required=True, type=Path, help="output root -- gets images/ and labels/ subdirs")
    args = ap.parse_args()

    out_images_dir = args.out / "images"
    out_labels_dir = args.out / "labels"
    out_images_dir.mkdir(parents=True, exist_ok=True)
    out_labels_dir.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        p for p in args.images.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    if not image_paths:
        raise SystemExit(f"No images found in {args.images}")

    total_written = 0
    skipped_no_label = 0
    for image_path in image_paths:
        label_path = args.labels / f"{image_path.stem}.txt"
        if not label_path.exists():
            # An image with no label file means "no tagged objects" in some
            # export conventions (an empty/background frame), not an error --
            # still worth augmenting so the model sees negative examples too.
            skipped_no_label += 1
        total_written += augment_one(image_path, label_path, out_images_dir, out_labels_dir)

    print(f"{len(image_paths)} source image(s) -> {total_written} augmented pair(s) "
          f"written to {args.out}")
    if skipped_no_label:
        print(f"({skipped_no_label} source image(s) had no label file -- "
              f"treated as background-only, still augmented)")


if __name__ == "__main__":
    main()
