"""Makes assets/msg_font.npz, what msgwatch.py reads the game's message lines with: the shapes of the
digits and "+", and of the word before the bracket that tells the kind of line ("mesos", "EXP",
"PARTY"), from tests/frames/msg_rows.png.

That picture is a stack of message rows from a 4K game (gh 20), each cropped to the right end of its
line, kept as lightness only (the lower of green and red, which is all the reader uses). Its rows
are listed in msg_rows.txt: the kind of line and its amount ("-" for a line with none). They were
read by Tesseract and the digit shapes together, and the ones the two disagreed on were checked
by eye. Run from the project folder:  py -3 tools/make_msg_font.py"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import msgwatch as mw  # noqa: E402

FRAMES = ROOT / "tests" / "frames"
ROW_H = 34                  # each row's height in msg_rows.png (its line's middle: half way down)
PAD = 4                     # room above and below, for the shapes to be lined up


def rows():
    """[(band, kind, amount or None)]: each row as a BGR picture, with PAD rows of nothing around it."""
    grey = cv2.imread(str(FRAMES / "msg_rows.png"), cv2.IMREAD_GRAYSCALE)
    labels = (FRAMES / "msg_rows.txt").read_text(encoding="utf-8").split("\n")
    out = []
    for i, line in enumerate(x for x in labels if x.strip()):
        kind, amount = line.split()
        band = np.pad(grey[i * ROW_H:(i + 1) * ROW_H], ((PAD, PAD), (0, 0)))
        out.append((cv2.merge([band, band, band]), kind, None if amount == "-" else int(amount)))
    return out


def right_end(band):
    """Where the row's line ends."""
    edge, _ = mw.edges(band, 1.0)
    xs = np.nonzero(edge.sum(axis=0) >= 2)[0]
    return int(xs[-1]) + 1


def spots(band, amount):
    """Each character's spot from the right (")" left out): the amount's digits last to first, then
    "+", then the word before the "(". [(char, x0, x1)]"""
    right = right_end(band)
    text = str(amount)
    out = []
    for k, c in enumerate(reversed("+" + text)):
        x1 = right - mw.BRACKET_W - k * mw.DIGIT_W
        out.append((c, x1 - mw.DIGIT_W, x1))
    plus = out[-1][1]
    out.append(("word", plus - mw.BRACKET_W - mw.CONTEXT_W, plus - mw.BRACKET_W))
    return out


def lined_up(F, x0, x1, shape):
    """The window of F at (x0, x1) moved a pixel or two to fit shape best (or as it is, without one)."""
    top = PAD + mw.HALF - ROW_H // 2
    if shape is None:
        return F[top:top + ROW_H, x0:x1]
    best = None
    for dx in (-1, 0, 1):
        for dy in (-2, -1, 0, 1, 2):
            win = F[top + dy:top + dy + ROW_H, x0 + dx:x1 + dx]
            if win.shape == shape.shape:
                fit = mw._ncc(win, shape)
                if best is None or fit > best[0]:
                    best = (fit, win)
    return best[1]


def main():
    data = [(mw.feature(band, 1.0), spots(band, amount), kind) for band, kind, amount in rows() if amount is not None]
    shapes = {}
    for _ in range(3):                                   # (each time lined up to the last shapes)
        found = {}
        for F, places, kind in data:
            for c, x0, x1 in places:
                key = f"k_{kind}" if c == "word" else c
                found.setdefault(key, []).append(lined_up(F, x0, x1, shapes.get(key)))
        shapes = {k: np.mean(v, axis=0).astype(np.float32) for k, v in found.items()}
    missing = [c for c in mw.CHARS if c not in shapes]
    assert not missing, f"no examples of {missing}"
    out = {f"c{mw.CHARS.index(c)}": shapes[c] for c in mw.CHARS}
    out.update({k: v for k, v in shapes.items() if k.startswith("k_")})
    np.savez_compressed(str(ROOT / "assets" / "msg_font.npz"), **out)
    print("msg_font.npz:", ", ".join(f"{k} ({len(found[k])})" for k in sorted(found)))


if __name__ == "__main__":
    main()
