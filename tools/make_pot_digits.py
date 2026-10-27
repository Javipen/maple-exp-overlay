"""Makes assets/pot_digits.npz, what potwatch.py reads the controller's potion popup with: the
"LT" label's letters and the count digits' shapes, from the popup pictures in tests/frames (whose
counts are known). Run from the project folder:  py -3 tools/make_pot_digits.py"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import potwatch as pw  # noqa: E402

FRAMES = ROOT / "tests" / "frames"
LABEL_AT = (276, 160)       # where the "LT" letters are in pots_lt_a.png
# The counts in each picture, slot by slot (None: no count there).
KNOWN = {"pots_lt_a.png": [None, 1068, 368, 51, 846, 1018, None, None],
         "pots_lt_b.png": [None, 992, 357, 21, 984, 1005, None, None]}


def windows(mask, text, shapes=None):
    """Each digit's spot in a slot's stroke mask, for a count known to be `text`: the start (and
    each digit's spot, give or take a pixel) that fits the digits' shapes best, or at first, before
    there are shapes, that puts the most of the strokes inside the digits' spots."""
    top, bottom = pw.GLYPH_TOP, pw.GLYPH_TOP + pw.GLYPH_H
    best = None
    for start in range(13):
        pos, found, score = start, [], 0.0
        for ch in text:
            w = pw.ONE_W if ch == "1" else pw.GLYPH_W
            options = []
            for at in range(max(0, pos - 1), pos + 2):
                win = mask[top:bottom, at:at + w].astype(np.float32)
                if win.shape[1] == w:
                    fit = -float(np.abs(win - shapes[ch]).mean()) if shapes else float(win.sum())
                    options.append((fit, at, win))
            if not options:
                break
            fit, at, win = max(options, key=lambda o: o[0])
            found.append((ch, win))
            score += fit
            pos = at + (pw.ONE_ADV if ch == "1" else pw.ADV)
        else:
            if best is None or score > best[0]:
                best = (score, found)
    return best[1] if best else []


def main():
    first = cv2.imread(str(FRAMES / "pots_lt_a.png"))
    x, y = LABEL_AT
    h, w = 26, 37
    label = pw._orange(first)[y:y + h, x:x + w]
    reader = pw.PotReader.__new__(pw.PotReader)
    reader.label = label
    masks = []
    for name, counts in KNOWN.items():
        img = cv2.imread(str(FRAMES / name))
        at = reader.find_label(img, pw.GH0)
        assert at is not None, name
        masks += [(pw.strokes(img, box, pw.GH0)[0], str(count))
                  for box, count in zip(reader.slots(at, pw.GH0), counts) if count is not None]
    shapes = None
    for _ in range(4):                  # line the digits up with the shapes so far, and average again
        found = {}
        for mask, text in masks:
            for ch, win in windows(mask, text, shapes):
                found.setdefault(ch, []).append(win)
        shapes = {d: np.mean(v, axis=0) for d, v in found.items()}
    missing = [d for d in "0123456789" if d not in found]
    assert not missing, f"no examples of {missing}"
    np.savez_compressed(ROOT / "assets" / "pot_digits.npz", label=label.astype(np.uint8),
                        **{f"d{d}": np.mean(v, axis=0).astype(np.float32) for d, v in found.items()})
    print("examples per digit:", {d: len(v) for d, v in sorted(found.items())})


if __name__ == "__main__":
    main()
