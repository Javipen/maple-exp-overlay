"""Notices kills as they happen, from the game's EXP messages.

Each kill prints a yellow line at the bottom right of the game, "You received EXP (+38)" (and,
in a party, "Bonus EXP for PARTY (+11)" with it). New lines come in at the bottom and push the
older ones up, and they fade away at the top. This doesn't read them: it only notices how many
new yellow lines came in since its last look, which works even when they come fast. Lines are
told apart by where they are and what they look like, so two identical "+38" lines in a row
still count as two.

The overlay uses the count to make the slime hop the moment EXP comes in, and to keep the EXP
going while the game hides the number (from 1,000,000 EXP it only shows the %).
"""
import cv2
import numpy as np

SIG = (48, 8)               # a line's look, shrunk to this many columns and rows, to compare lines by


def message_area(rect):
    """Where the game shows its messages: the right of the window, above the bottom UI. (x, y, w, h)."""
    wx, wy, ww, wh = rect
    x0, y0 = wx + int(0.6 * ww), wy + int(0.5 * wh)
    return x0, y0, wx + ww - x0, wy + int(0.92 * wh) - y0


def yellow(hsv):
    """The EXP lines' yellow."""
    hue, sat, val = cv2.split(hsv)
    return ((hue >= 18) & (hue <= 40) & (sat >= 120) & (val >= 140)).astype(np.uint8)


def white(hsv):
    """The other messages' white ("You have gained mesos (+15)", "Banana x1 earned. (Etc)")."""
    _, sat, val = cv2.split(hsv)
    return ((sat <= 40) & (val >= 200)).astype(np.uint8)


def find_lines(bgr, text_h, right_slack, colour=yellow):
    """The lines of one colour (the yellow EXP lines, by default) in a picture of the message area,
    top to bottom: [(y, x0, x1, look)].

    text_h: about how tall the game's text is (px). right_slack: how far from the area's right
    edge a line may end (the messages are lined up on the right)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = colour(hsv)
    width = mask.shape[1]
    # Letters joined sideways into words and words into lines (not up and down: lines stay apart),
    # so yellow things in the scenery beside the text don't join in.
    join = cv2.dilate(mask, np.ones((1, max(3, int(0.8 * text_h))), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(join, connectivity=4)
    lines = []
    for i in range(1, n):
        x0, y0, w, h = (int(v) for v in stats[i][:4])
        if x0 + w < width - right_slack or w < 4 * 0.5 * text_h:
            continue                                      # not lined up on the right, or too short
        own = mask[y0:y0 + h, x0:x0 + w] * (labels[y0:y0 + h, x0:x0 + w] == i)
        # A stray yellow stroke can still join lines one above the other: split them again where
        # (nearly) nothing is between them.
        rows = own.sum(axis=1)
        on = rows > max(1, 0.12 * rows.max())
        start = None
        for r, flag in enumerate(list(on) + [False]):
            if flag and start is None:
                start = r
            elif not flag and start is not None:
                band = own[start:r]
                cols = np.nonzero(band.any(axis=0))[0]
                bx0, bx1 = x0 + int(cols[0]), x0 + int(cols[-1]) + 1
                bh = r - start
                part = band[:, cols[0]:cols[-1] + 1]
                if (0.5 * text_h <= bh <= 2.0 * text_h and bx1 - bx0 >= 4 * bh and bx1 >= width - right_slack
                        and 0.05 <= part.mean() <= 0.6):          # (thin letters, not a solid yellow thing)
                    look = cv2.resize(part.astype(np.float32), SIG, interpolation=cv2.INTER_AREA) > 0.2
                    lines.append((y0 + (start + r) / 2, bx0, bx1, look))
                start = None
    return sorted(lines, key=lambda line: line[0])


def _same(a, b):
    """Do two lines look the same (the same message)?"""
    wa, wb = a[2] - a[1], b[2] - b[1]
    return abs(wa - wb) <= 0.15 * max(wa, wb) and (a[3] == b[3]).mean() >= 0.8


def count_new(before, now, tol):
    """How many of the lines in `now` weren't there in `before`.

    Old lines only move up (by however many new lines came in), so the move that lines up the
    most old lines with lines that look the same is found; the lines left over below them are
    new. A move that would leave an unexplained line *above* the lined-up ones is wrong (that
    happens with identical lines: "+38" then "+38"), so it counts against that move."""
    if not now:
        return 0
    if not before:
        return len(now)
    moves = {0.0} | {b[0] - n[0] for b in before for n in now if n[0] <= b[0] + tol}
    best = None
    for d in sorted(moves):
        used, matched = set(), []
        for b in before:
            for i, n in enumerate(now):
                if i not in used and abs(b[0] - d - n[0]) <= tol and _same(b, n):
                    used.add(i)
                    matched.append(n[0])
                    break
        if not matched:
            continue
        lowest = max(matched)
        above = sum(1 for i, n in enumerate(now) if i not in used and n[0] < lowest)
        new = sum(1 for i, n in enumerate(now) if i not in used and n[0] > lowest)
        score = len(matched) - 2 * above
        if best is None or score > best[0]:
            best = (score, new)
    if best is None or best[0] <= 0:
        return len(now)                                   # nothing carried over: all of them are new
    return best[1]


class KillWatcher:
    """Looks at the message area again and again, and says how many new EXP lines came in each time."""

    def __init__(self):
        self.before = None

    def reset(self):
        self.before = None

    def look(self, bgr, text_h, right_slack):
        lines = find_lines(bgr, text_h, right_slack)
        if self.before is None:                           # (the first look: what's there is old)
            self.before = lines
            return 0
        new = count_new(self.before, lines, 0.45 * text_h)
        self.before = lines
        return new
