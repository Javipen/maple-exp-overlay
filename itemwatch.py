"""Reads item pickup lines by the game's font, and names the item from the game's item list.

Picking an item up prints a white line in the message stack: "Lupin's Banana x1 earned. (Etc)", the
name cut short with "..." when it's long ("Green Mushroo... x1 earned. (Etc)"). The end of the line
is drawn the same way every time, so it's read by the shapes of its pieces (assets/item_font.npz):
the inventory tab in brackets, "earned.", the count, and the "x" before it. That leaves the name.

An item is known by the picture of its name once it's been seen: the drops of the maps the overlay
was made in come with it, and the first time any other item comes in, Tesseract reads its name and
it's matched to the closest name in the game's item list (assets/items.txt, from meowdb.com) on that
tab (by its start, when it's cut short). Its picture is kept in the data folder, so from then on it's
known by sight. A name that matches nothing in the list closely enough is left out: a missed pickup
is better than a wrong one.

Sizes are 4K pixels (gh 20), like msgwatch.py's; at other sizes everything is scaled by gh / 20.
"""
import difflib
import re
from pathlib import Path

import cv2
import numpy as np

import msgwatch as mw
from pickwatch import read_line as tesseract_line

GH0 = 20.0
TAB_GAP = 13                # px between "earned." and the tab's "("
DIGIT_GAP = 10              # px between the count's last digit cell and "earned."
PAD = 2                     # px of room kept around each shape in item_font.npz
TAIL_OK = 0.6               # how alike the tab and "earned." must look to their shapes
X_OK = 0.45                 # ...and the "x" (small: it looks less alike)
DIGIT_OK = 0.55             # ...and each digit of the count
NAME_OK, NAME_MARGIN = 0.8, 0.05    # a name's picture: how alike, and how much more than any other's
NAME_SLACK = 6              # px a name's end may be off from where it's looked for
LIFT = 6                    # px a line may sit higher or lower than its row's middle (caught moving up)
NAME_GAP = 16               # px between two words of a name, at most (more: something else)
DOTS_OK = 0.7               # the "..." at the end of a name cut short
MATCH_OK, MATCH_MARGIN = 0.75, 0.05  # Tesseract's reading against the item list: how close, how much closer
KEEP = 3                    # pictures kept of each name (from different lines)


def norm(name):
    """A name for comparing: lower case, letters, digits and apostrophes, single spaces."""
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", name.lower()).split())


def load_items(path):
    """The game's item list (items.txt: tab, a tab character, the name): {tab: [names]}."""
    items = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        tab, _, name = line.partition("\t")
        if name:
            items.setdefault(tab, []).append(name)
    return items


def match_name(raw, tab, items, cut):
    """The item a name read by Tesseract is (raw: as read; cut: it ends in "...", so only its start
    is shown), from the names on its tab, or None if none is clearly closest."""
    key = norm(raw)
    if len(key) < 3:
        return None
    scored = []
    for name in items.get(tab, []):
        n = norm(name)
        if cut:
            if len(n) <= len(key):
                continue                        # (cut short: the name is longer than what's shown)
            n = n[:len(key)]
        scored.append((difflib.SequenceMatcher(None, key, n).ratio(), name, n))
    if not scored:
        return None
    scored.sort(reverse=True)
    best, name, shown = scored[0]
    # (Names that look the same as shown, "Arrows for Bows" and "Arrows for Bows x", can't be told
    # apart: they count as a runner-up.)
    second = max((s for s, nm, sh in scored[1:] if nm != name), default=0.0)
    return name if best >= MATCH_OK and best - second >= MATCH_MARGIN else None


class ItemReader:
    """Reads item lines: read() for one line's picture."""

    def __init__(self, assets, learned=None):
        data = np.load(str(Path(assets) / "item_font.npz"))
        self.tabs = {k[2:]: data[k].astype(np.float32) for k in data.files if k.startswith("t_")}
        self.earned = data["earned"].astype(np.float32)
        self.x = data["x"].astype(np.float32)
        self.dots = data["dots"].astype(np.float32)
        self.chars = {c: t for c, t in mw.Font(assets).chars.items() if c.isdigit()}
        self.items = load_items(Path(assets) / "items.txt")
        self.names = []                         # [name, tab, picture (4K)]: the names known by sight
        self._load(data, "n")
        self.learned_path = Path(learned) if learned else None
        self.learned = 0                        # how many of self.names were learned here (they're at the end)
        if self.learned_path and self.learned_path.is_file():
            try:
                before = len(self.names)
                self._load(np.load(str(self.learned_path)), "n")
                self.learned = len(self.names) - before
            except Exception:
                pass
        self._scaled = {}

    def _load(self, data, prefix):
        if f"{prefix}_names" not in data.files:
            return
        for k, (name, tab) in enumerate(zip(data[f"{prefix}_names"], data[f"{prefix}_tabs"])):
            self.names.append([str(name), str(tab), data[f"{prefix}{k}"].astype(np.float32)])

    def _save(self):
        if not self.learned_path:
            return
        mine = self.names[len(self.names) - self.learned:]
        arrays = {"n_names": np.array([n for n, _, _ in mine]), "n_tabs": np.array([t for _, t, _ in mine])}
        arrays.update({f"n{k}": pic for k, (_, _, pic) in enumerate(mine)})
        try:
            self.learned_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(str(self.learned_path), **arrays)
        except Exception:
            pass

    def _at(self, s):
        """The shapes at scale s."""
        if s not in self._scaled:
            def sz(t):
                return cv2.resize(t, (max(1, round(t.shape[1] * s)), max(1, round(t.shape[0] * s))),
                                  interpolation=cv2.INTER_AREA)
            self._scaled[s] = ({k: sz(t) for k, t in self.tabs.items()}, sz(self.earned), sz(self.x), sz(self.dots),
                               {c: sz(t) for c, t in self.chars.items()})
        return self._scaled[s]

    @staticmethod
    def _find(F, t, x0, x1):
        """Where shape t fits best in F between x0 and x1 (its left edge), and how well: (score, x)."""
        x0, x1 = max(0, int(x0)), min(F.shape[1], int(x1))
        area = F[:, x0:x1]
        if area.shape[0] < t.shape[0] or area.shape[1] < t.shape[1]:
            return -1.0, x0
        _, score, _, at = cv2.minMaxLoc(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED))
        return float(score), x0 + at[0]

    def tail(self, F, right, s):
        """The end of an item line (F: its feature picture, right: where it ends): (tab, count, where
        the name ends, whether it's cut short), or None if it isn't an item line or isn't clear."""
        tabs, earned, x, dots, chars = self._at(s)
        F = np.pad(F, ((int(LIFT * s), int(LIFT * s)), (0, 0)))
        p = PAD * s
        ranked = sorted(((*self._find(F, t, right - t.shape[1] - 4 * s, right + 6 * s), k) for k, t in tabs.items()),
                        reverse=True)
        score, tx, tab = ranked[0]
        if score < TAIL_OK or (len(ranked) > 1 and score - ranked[1][0] < 0.05):
            return None
        want = tx + p - TAB_GAP * s - (earned.shape[1] - 2 * p) - p
        score, ex = self._find(F, earned, want - 8 * s, want + earned.shape[1] + 8 * s)
        if score < TAIL_OK:
            return None
        # The count, right to left from just before "earned.", then the "x" before it.
        x1, digits = ex + p - DIGIT_GAP * s, ""
        h, w = chars["0"].shape
        for k in range(5):
            slack = 8 * s if k == 0 else 1
            got = sorted(((*self._find(F, t, x1 - w - slack, x1 + slack + 1), c) for c, t in chars.items()), reverse=True)
            if got[0][0] < DIGIT_OK:
                break
            digits, x1 = got[0][2] + digits, got[0][1]
        if not digits or digits[0] == "0":
            return None
        score, xx = self._find(F, x, x1 - x.shape[1] - 4 * s, x1 + 2 * s)
        if score < X_OK:
            return None                         # (no "x": it was something over the line, not its count)
        # The name ends at the last letter before the "x".
        cols = np.nonzero(F[:, :max(0, int(xx + p - 4 * s))].max(axis=0) > 0.3)[0]
        if not cols.size:
            return None
        name_right = int(cols[-1]) + 1
        cut, _ = self._find(F, dots, name_right - dots.shape[1] - 2 * s, name_right + 3 * s)
        return tab, int(digits), name_right, cut >= DOTS_OK

    @staticmethod
    def name_left(F, right, s):
        """Where a name ending at right starts: its letters, with gaps no wider than a space."""
        ink = (F > 0.3).sum(axis=0) >= 2
        x, gap = int(right) - 1, 0
        left = x
        while x >= 0 and gap <= NAME_GAP * s:
            if ink[x]:
                left, gap = x, 0
            else:
                gap += 1
            x -= 1
        return left

    def _picture(self, F, left, right, s):
        """A name's picture, at 4K size."""
        pic = F[:, max(0, int(left)):int(right)]
        if s != 1.0 and pic.size:
            pic = cv2.resize(pic, (max(1, round(pic.shape[1] / s)), max(1, round(pic.shape[0] / s))),
                             interpolation=cv2.INTER_LINEAR)
        return pic

    def known(self, F, name_right, tab, s):
        """The name ending at name_right in F, from the names known by sight (on that tab), or None. Each
        is compared by its picture lined up on the name's end (where it starts isn't as sure: there
        can be scenery just left of a line)."""
        scores = {}
        G = self._picture(np.pad(F, ((int(LIFT * s), int(LIFT * s)), (0, 0))), 0, name_right + NAME_SLACK * s, s)
        end = G.shape[1]
        for name, t, ref in self.names:
            if t != tab:
                continue
            core = ref[2:-2, 2:-2]
            area = G[:, max(0, end - core.shape[1] - 2 * NAME_SLACK - 2):end]
            if core.shape[0] > area.shape[0] or core.shape[1] > area.shape[1]:
                continue
            _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, core, cv2.TM_CCOEFF_NORMED))
            scores[name] = max(scores.get(name, -1.0), float(score))
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        return best if score >= NAME_OK and score - second >= NAME_MARGIN else None

    def learn(self, name, tab, pic):
        """Keeps a picture of a name, to know it by sight from now on (up to KEEP of each)."""
        if sum(1 for n, t, _ in self.names if n == name and t == tab) >= KEEP:
            return
        self.names.append([name, tab, pic.astype(np.float32)])
        self.learned += 1
        self._save()

    def read(self, band, span, s, read_lines=None):
        """What a white line with no amount the font knows says: ("item", name, count, tab), ("mesos",
        amount) (a mesos line the font couldn't read: Tesseract can), or None. band: the line's row
        (msgwatch's band: its middle half way down); span: where the line starts and ends in it (x).
        read_lines: Tesseract, for a name not known by sight yet (left out without it), and for a
        line whose end isn't clear (a tab with no shape here yet, like "(Setup)")."""
        F = mw.feature(band, s)
        end = self.tail(F, span[1], s)
        if end is None:
            got = tesseract_line(band, span, GH0 * s, read_lines) if read_lines is not None else None
            if got and got[0] == "item":
                name = match_name(got[1], got[3], self.items, False) or match_name(got[1], got[3], self.items, True)
                return ("item", name, got[2] or 1, got[3]) if name else None
            return got
        tab, qty, name_right, cut = end
        name = self.known(F, name_right, tab, s)
        if name is None and read_lines is not None:
            pic = self._picture(F, self.name_left(F, name_right, s), name_right, s)
            got = tesseract_line(band, span, GH0 * s, read_lines)
            if got and got[0] == "item":
                name = match_name(got[1], tab, self.items, cut)
                if name:
                    self.learn(name, tab, pic)
        return ("item", name, qty, tab) if name else None
