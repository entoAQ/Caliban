#!/usr/bin/env python3
"""Write the four rotations of a capture, for measuring model variance.

    python3 rotate.py ~/captures/LOT12345_*_visible.jpg

Writes ~/captures/rotations/LOT12345_..._rot000.jpg and 090, 180, 270.

The point is that a levelled dish of randomly-arranged material has no
orientation. Rotating it cannot change how much MEO is present, so the four
estimates *should* be identical -- and any spread between them is the model's
own noise, measured without needing a lab value to compare against.

That number is worth having before any other prompt work. It is the floor: no
amount of rewording gets an estimate more precise than the model's variance on
the same sample, and if the floor turns out to be a band wide, that is worth
knowing before spending a week trying to shave a band off.

Crops to a centred square first, so all four come out the same shape. Without
that, 90 and 270 would differ from 0 and 180 in aspect ratio and framing, and
part of any spread would be that rather than the rotation.
"""

import sys
from pathlib import Path

from PIL import Image

OUT_DIR = Path.home() / "captures" / "rotations"


def main():
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        sys.exit("Usage: rotate.py IMAGE [IMAGE ...]")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for path in paths:
        if not path.exists():
            print(f"  skipped, not found: {path}")
            continue

        img = Image.open(path)
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        square = img.crop((left, top, left + side, top + side))

        stem = path.stem
        for angle in (0, 90, 180, 270):
            out = OUT_DIR / f"{stem}_rot{angle:03d}.jpg"
            # expand is irrelevant on a square, but quality is not: these go
            # through the same analysis as a real capture and should not carry
            # extra compression damage the original did not have.
            square.rotate(angle).save(out, quality=95)
            print(out)


if __name__ == "__main__":
    main()
