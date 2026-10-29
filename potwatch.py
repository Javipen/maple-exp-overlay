"""Reads the potion counts in the controller's left-trigger popup.

Playing with a controller, holding the left trigger shows a popup of quickslots over the
bottom of the game: potions, food and the like, each with how many you have ("1068") in white
digits with a dark outline. An orange "LT" label sits under it (the right trigger shows a
skills popup with "RT" instead). This finds the popup by that label, then reads the count in
each slot, and keeps a small picture of each slot's icon so the items can be told apart.

Everything is measured in EXP-digit heights (gh: the overlay's measure of the game's size), from
a 4K game where gh is 20. The game draws at 1080p and doubles every pixel, so the shapes are
crisp and the same at every size.
"""
from pathlib import Path

import cv2
import numpy as np

GH0 = 20.0                  # gh in the 4K game everything here was measured in
# The 8 slots (left to right, top to bottom), as the top-left corner of each slot's light
# inside, from the top-left of the "LT" label's letters; and the size of that inside.
SLOTS = [(-265, -99), (-169, -144), (-169, -54), (-73, -99), (24, -99), (119, -144), (119, -54), (214, -99)]
SLOT_W, SLOT_H = 79, 71
DIGIT_TOP, DIGIT_BOTTOM = 0.72, 1.30    # the count's band, in slot heights from the slot's top
# The digits, in a slot's count band (see strokes): their top and height, their width (a 1's is
# less), and how far apart they are.
GLYPH_TOP, GLYPH_H, GLYPH_W, ONE_W, ADV, ONE_ADV = 4, 26, 15, 7, 22, 14
MATCH, MARGIN = 0.22, 0.03  # how far off a digit may be from its shape, and how much better than the next best
REACH = 10                  # 4K px past a slot's right edge a long count may end (see read_count)
END_INK = 0.1               # past the end of a number there's next to nothing (a clean count: under 0.04)
LOOK = (12, 9)              # an icon's look (columns, rows), to tell items apart


def _orange(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 0] >= 10) & (hsv[..., 0] <= 30) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).astype(np.float32)


def search_area(rect):
    """Where to look for the popup: the bottom of the game, right of its middle. (x, y, w, h)"""
    wx, wy, ww, wh = rect
    x0, y0 = wx + int(0.45 * ww), wy + int(0.62 * wh)
    return x0, y0, wx + int(0.95 * ww) - x0, wy + wh - y0


class PotReader:
    """Finds the left-trigger popup in a picture and reads the counts in its slots."""

    def __init__(self, assets):
        data = np.load(str(Path(assets) / "pot_digits.npz"))
        self.label = data["label"].astype(np.float32)          # the "LT" letters (orange or not)
        self.shapes = {str(d): data[f"d{d}"].astype(np.float32) for d in range(10) if f"d{d}" in data}

    def find_label(self, bgr, gh):
        """(x, y) of the "LT" letters' top-left in bgr, or None when the popup isn't open."""
        s = gh / GH0
        tpl = cv2.resize(self.label, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        mask = _orange(bgr)
        if mask.shape[0] < tpl.shape[0] or mask.shape[1] < tpl.shape[1] or mask.sum() < 0.3 * tpl.sum():
            return None
        res = cv2.matchTemplate(mask, tpl, cv2.TM_CCOEFF_NORMED)
        _, best, _, at = cv2.minMaxLoc(res)
        return at if best >= 0.75 else None

    def slots(self, at, gh):
        """The 8 slots' insides (x, y, w, h), for the label found at `at`."""
        s = gh / GH0
        return [(int(round(at[0] + dx * s)), int(round(at[1] + dy * s)), int(round(SLOT_W * s)), int(round(SLOT_H * s)))
                for dx, dy in SLOTS]

    def look(self, bgr, gh):
        """None when the popup isn't open; else a list, one per slot, of (icon look, count or None)."""
        at = self.find_label(bgr, gh)
        if at is None:
            return None
        out = []
        for box in self.slots(at, gh):
            x, y, w, h = box
            if x < 0 or y < 0 or x + w > bgr.shape[1] or y + int(DIGIT_BOTTOM * h) > bgr.shape[0]:
                out.append((None, None))
                continue
            out.append((icon_look(bgr, box), self.read_count(bgr, box, gh)))
        return out

    def read_count(self, bgr, box, gh):
        """The count in a slot, or None if there's none (or it isn't clear). A 4-digit count can run past
        the slot's right edge (2203): only when the count can't be read within the slot, a little past
        it is looked at too."""
        mask, _ = strokes(bgr, box, gh)
        got = decode(mask, self.shapes, gh / GH0)
        if not got:
            wide, _ = strokes(bgr, box, gh, REACH)
            if wide[:, mask.shape[1]:].sum() >= 3 * gh / GH0:      # (strokes past the edge: a digit runs on)
                got = decode(wide, self.shapes, gh / GH0)
        if not got or (len(got[0]) > 1 and got[0][0] == "0"):
            return None
        return int(got[0])


def icon_look(bgr, box):
    """A small colour picture of a slot's icon (its top part, above the count), 0..1."""
    x, y, w, h = box
    part = bgr[y:y + int(0.62 * h), x:x + w]
    return cv2.resize(part, LOOK, interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def same_icon(a, b):
    """Do two icon looks show the same item?"""
    return a is not None and b is not None and float(np.abs(a - b).mean()) < 0.03


def strokes(bgr, box, gh, reach=0):
    """The count's band of a slot as a mask (0/1) of the digits' light strokes, and its left x.
    reach: how far past the slot's right edge to look (4K px), for a long count that ends over it.

    The digits are drawn as thin light outlines (white, light blue at the bottom) inside a black
    plate, over the icon: the light pixels inside the black plate are the digits."""
    x, y, w, h = box
    y0, y1 = y + int(DIGIT_TOP * h), y + int(DIGIT_BOTTOM * h)
    x0 = max(0, x - int(round(8 * gh / GH0)))                    # (a count can start over the slot's frame)
    x1 = x + w - 2 + int(round(reach * gh / GH0))
    hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    # The black plate fills the gaps between the strokes, which are thin; not the slot's light frame,
    # which is wider.
    k = max(3, int(round(5 * gh / GH0)))
    plate = cv2.morphologyEx((hsv[..., 2] < 70).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    light = (hsv[..., 2] > 140) & ((hsv[..., 1] < 120) | ((hsv[..., 0] >= 85) & (hsv[..., 0] <= 125)))
    return (light & (plate > 0)).astype(np.uint8), x0


def decode(mask, shapes, s=1.0):
    """Reads the digits in a slot's stroke mask (see strokes), left to right. The digits are a fixed
    width apart (a 1 is narrower), starting at the slot's left: each spot is compared with every
    digit's shape. Returns (text, how far off the worst digit was), or None if anything's unclear."""
    r0 = int(round(GLYPH_TOP * s))
    r1 = r0 + int(round(GLYPH_H * s))
    width = mask.shape[1]
    best = None
    jiggle = max(1, int(round(s)))
    for start in range(0, int(round(12 * s)) + 1):
        pos, text, worst = start, "", 0.0
        while True:
            spot = mask[r0:r1, pos:pos + int(round(GLYPH_W * s))]
            if spot.size == 0 or spot.mean() < 0.06:
                break                                   # no more digits
            scored = {}                                 # digit: (how far off, where), at its best spot nearby
            for d, t in shapes.items():
                w = int(round(t.shape[1] * s))
                for at in range(max(0, pos - jiggle), pos + jiggle + 1):
                    if at + w > width:
                        continue
                    win = mask[r0:r1, at:at + w].astype(np.float32)
                    if win.shape != t.shape:
                        win = cv2.resize(win, (t.shape[1], t.shape[0]), interpolation=cv2.INTER_AREA)
                    diff = float(np.abs(win - t).mean())
                    if d not in scored or diff < scored[d][0]:
                        scored[d] = (diff, at)
            ranked = sorted((v[0], d, v[1]) for d, v in scored.items())
            if not ranked or ranked[0][0] > MATCH:
                if spot.mean() >= END_INK:
                    text = None                         # strokes here, but no clear digit: don't read it short
                break                                   # nothing here: the number ended
            if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < MARGIN:
                text = None                             # a digit, but which one isn't clear
                break
            diff, d, at = ranked[0]
            text, worst = text + d, max(worst, diff)
            pos = at + int(round((ONE_ADV if d == "1" else ADV) * s))
            if len(text) > 7:
                text = None
                break
        if text and (best is None or worst < best[1]):
            best = (text, worst)
    return best


# What the NPC shops sell them for (meowdb.com, MapleStory Classic), for a first guess at an item
# from its icon's colour; anything else is "Item" at 0 mesos until it's set in Settings.
SHOP_PRICES = {"Red Potion": 50, "Orange Potion": 150, "White Potion": 300, "Blue Potion": 220,
               "Mana Elixir": 620, "Meat": 80, "Lemon": 150}


def guess_item(look):
    """(name, price) the icon's colour suggests: red, orange, blue potions, a lemon or meat."""
    hsv = cv2.cvtColor((look * 255).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(int)
    colour = hsv[(hsv[:, 1] > 80) & (hsv[:, 2] > 60)]
    if len(colour) < 5:
        return "Item", 0
    hue, val = int(np.median(colour[:, 0])), int(np.median(colour[:, 2]))
    if 95 <= hue <= 130:
        name = "Blue Potion"
    elif 24 <= hue <= 36:
        name = "Lemon"
    elif 10 <= hue < 24:
        name = "Orange Potion"
    elif hue < 10 or hue >= 165:
        name = "Meat" if val < 150 else "Red Potion"
    else:
        return "Item", 0
    return name, SHOP_PRICES[name]
