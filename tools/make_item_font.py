"""Makes assets/item_font.npz, what itemwatch.py reads item pickup lines with: the shapes of the end of
the line (each inventory tab in brackets, "earned.", the "x" before the count, the "..." of a name
cut short), and the pictures of the names of the items seen in tests/frames/item_rows.png.

That picture is a stack of item lines from a 4K game (gh 20), 43 px a row (the line's middle half
way down), each line right-aligned, kept as lightness only (the lower of green and red, which is all
the reader uses). Its rows are listed in item_rows.txt: tab, count and the item's full name, read by
eye. Run from the project folder:  py -3 tools/make_item_font.py"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import itemwatch as iw  # noqa: E402
import msgwatch as mw  # noqa: E402

FRAMES = ROOT / "tests" / "frames"
ROW_H = 43
WORD_GAP = 8                # px of nothing between two words (letters are closer)
DOTS_W = 20                 # the "..." at the end of a name cut short
TAB_W = {"Etc": 65, "Use": 82, "Equip": 108}     # each tab's width, brackets and all
EARNED_W = 113              # "earned."
X1_W = 26                   # "x1"


def rows(stem="item_rows"):
    """[(band, tab, count, name)]: each row as a BGR picture."""
    grey = cv2.imread(str(FRAMES / f"{stem}.png"), cv2.IMREAD_GRAYSCALE)
    out = []
    for i, line in enumerate(x for x in (FRAMES / f"{stem}.txt").read_text(encoding="utf-8").split("\n") if x.strip()):
        tab, qty, name = line.split("\t")
        band = grey[i * ROW_H:(i + 1) * ROW_H]
        out.append((cv2.merge([band, band, band]), tab, int(qty), name))
    return out


def words(band):
    """The line's words, from the right: [(x0, x1)], and where the line ends."""
    edge, _ = mw.edges(band, 1.0)
    cols = np.nonzero(edge.sum(axis=0) > 0)[0]
    runs, start = [], cols[0]
    for a, b in zip(cols[:-1], cols[1:]):
        if b - a > WORD_GAP:
            runs.append((start, a + 1))
            start = b
    runs.append((start, cols[-1] + 1))
    return runs[::-1]


def average(crops):
    """The shapes lined up on the first one (a pixel or two either way), and averaged."""
    ref, out = crops[0], [crops[0]]
    for c in crops[1:]:
        c = c[:, :ref.shape[1]] if c.shape[1] >= ref.shape[1] else np.pad(c, ((0, 0), (0, ref.shape[1] - c.shape[1])))
        _, dy, dx = max((mw._ncc(ref, np.roll(np.roll(c, dy, 0), dx, 1)), dy, dx)
                        for dy in (-2, -1, 0, 1, 2) for dx in (-2, -1, 0, 1, 2))
        out.append(np.roll(np.roll(c, dy, 0), dx, 1))
    return np.mean(out, axis=0).astype(np.float32)


def main():
    p, top, bottom = iw.PAD, 4, ROW_H - 4
    tabs, earned, xs, dots = {}, [], [], []
    # The shapes come from the clean lines: those whose words (split where there's nothing between
    # them) are as wide as they should be. On a faded line, or one with scenery behind it, the gaps
    # can fill in.
    for band, tab, qty, name in rows():
        F = mw.feature(band, 1.0)
        w = words(band)
        if len(w) < 4 or abs(w[0][1] - w[0][0] - TAB_W[tab]) > 1 or abs(w[1][1] - w[1][0] - EARNED_W) > 1:
            continue
        (t0, t1), (e0, e1), (q0, q1), (n0, n1) = w[:4]
        tabs.setdefault(tab, []).append(F[top:bottom, t0 - p:t1 + p])
        earned.append(F[top:bottom, e0 - p:e1 + p])
        if qty != 1 or abs(q1 - q0 - X1_W) > 1:
            continue
        xs.append(F[top:bottom, q0 - p:q0 + 13 + p])     # the "x" (the digits after it: msg_font.npz)
        cut = name.startswith(("Green Mushroom", "Arrows for Crossbows", "Green Woodsman", "Animal Fur"))
        if cut and 6 <= q0 - n1 <= 14:
            dots.append(F[top:bottom, n1 - DOTS_W - p:n1 + p])
    shapes = {f"t_{k}": average(v) for k, v in tabs.items()}
    shapes.update(earned=average(earned), x=average(xs), dots=average(dots))
    out = ROOT / "assets" / "item_font.npz"
    np.savez_compressed(str(out), **shapes)

    # The names, as the reader sees them (where it finds a name's end), up to KEEP of each.
    reader = iw.ItemReader(ROOT / "assets")
    names, missed = [], 0
    for band, tab, qty, name in rows():
        F = mw.feature(band, 1.0)
        w = words(band)
        end = reader.tail(F, int(w[0][1]), 1.0)
        if end is None or end[:2] != (tab, qty):
            missed += 1
            continue
        if sum(1 for n, _, _ in names if n == name) < iw.KEEP:
            names.append((name, tab, F[:, reader.name_left(F, end[2], 1.0):end[2]]))
    shapes.update(n_names=np.array([n for n, _, _ in names]), n_tabs=np.array([t for _, t, _ in names]))
    shapes.update({f"n{k}": pic for k, (_, _, pic) in enumerate(names)})
    np.savez_compressed(str(out), **shapes)
    print(f"{out.name}: tabs {sorted(tabs)}, {len(names)} name pictures of {len(set(n for n, _, _ in names))} items"
          f" ({missed} rows whose end wasn't read)")


if __name__ == "__main__":
    main()
