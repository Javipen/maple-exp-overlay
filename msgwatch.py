"""Watches the game's message stack at the bottom right: every new line, and what it says.

Kills and pickups print one line each in a stack at the bottom right of the game: yellow
"You received EXP (+32)" and "Bonus EXP for PARTY (+10)" for a kill, white "You have gained
mesos (+16)" or "Lupin's Banana x1 earned. (Etc)" for a pickup. What the game does with them,
measured frame by frame in recordings of it:

- the lines sit on fixed rows (PITCH apart), all ending at the same spot on the right; a new
  line comes in on the bottom row and pushes the others up a row
- a new line is drawn at full brightness, starts to dim after a few tenths of a second, and is
  gone 2 to 3 seconds later
- the letters are always drawn the same, with a dark shadow down and to the right; the digits
  and "+" are all the same width (DIGIT_W), and "(" and ")" half that

So this looks at the stack often (10 times a second), reads every row, and works out how many
rows the stack moved up since the last look: the lines that were there are found again a row
or more higher (the same shape and amount, never brighter: a line only dims), and the rows
below them are new. Two identical "+16" lines in a row are still told apart, by the rows above
them and by brightness. A row is read digit by digit from the right, against the digits'
shapes (assets/msg_font.npz, made by tools/make_msg_font.py), and the kind of line from the
word before the bracket; a new line that isn't clear is read again at the next looks. Lines
with no amount (items) are left for Tesseract. A look where something bright passes over the
stack (a skill's flash) and no line can be found again is left out.

Everything is in pixels of a 4K game (gh 20) and scaled by gh / GH0.
"""
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

GH0 = 20.0
ROWS = 8                    # rows in the stack (more than are ever seen at once)
PITCH = 39.7                # from one row to the next
BOTTOM_UP = 441             # the bottom row's middle, above the game window's bottom
EDGE_IN = 20                # where the lines end, in from the window's right edge
AREA_W = 860                # how far left of that to look (the longest item lines)
HALF = 17                   # a row's band: this far above and below its middle
SHADOW = 3                  # the letters' shadow, down and to the right
DIGIT_W, BRACKET_W = 20, 10
CONTEXT_W = 120             # the word before the "(", that tells the kind of line
FRESH = 0.94                # a new line's letters: as bright as lines get (seen within a tenth of a second)
LOOK_W = 300                # how much of a line's right end to compare it by (all lines are longer)
END_W = 2 * DIGIT_W        # ...and its last two digits (or "+" and a digit), as they are
GAP = 30                    # the widest gap inside a line (between words)
SHORTEST = 200              # a line's lit part is at least this long...
PART = 100                  # ...or this long, if it reads as an amount line (over bright scenery, part of a
                            # line may not show)
DIM_STEP = 0.04             # a line may read this much brighter than last time (the scenery shows through)
NEW = 0.3                   # what calling a row a new line is worth (an old line found again: up to 1)
SETTLED = 0.35              # a line this old (s) has dimmed: new ones are brighter
SKIPS = 3                   # unclear looks left out in a row, at most
READ_OK, READ_MARGIN = 0.55, 0.04   # a digit's least match to its shape, and its lead over the next best
KIND_OK = 0.45
FEAT_LOW = 0.62             # a letter's pixels are at least this bright, of the line's brightest
KINDS = ("mesos", "exp", "bonus")
CHARS = "0123456789+"
TRIES = 4                   # looks to read a line that isn't clear (it's bright for longer)


def stack_area(rect, gh):
    """The part of the screen with the message stack: (x, y, w, h)."""
    wx, wy, ww, wh = rect
    s = gh / GH0
    right = wx + ww
    bottom = wy + wh - BOTTOM_UP * s + (HALF + 6) * s
    top = wy + wh - BOTTOM_UP * s - (ROWS - 1) * PITCH * s - (HALF + 6) * s
    x0 = right - int((EDGE_IN + AREA_W) * s)
    return int(x0), int(top), int(right - x0), int(bottom - top)


def row_middles(area_h, gh):
    """Each row's middle in a picture of stack_area, bottom row first."""
    s = gh / GH0
    bottom = area_h - (HALF + 6) * s
    return [bottom - k * PITCH * s for k in range(ROWS)]


def lightness(bgr):
    """White and yellow letters are both light in green and red (the scenery rarely is in both)."""
    return np.minimum(bgr[..., 1], bgr[..., 2]).astype(np.int16)


def edges(bgr, s):
    """The letters' pixels that have their dark shadow down-right of them: light text, whatever its
    brightness, and hardly ever the scenery."""
    L = lightness(bgr)
    o = max(1, int(round(SHADOW * s)))
    shade = np.full_like(L, 255)                # (no shadow known at the right and bottom edges)
    shade[:-o, :-o] = np.minimum(L[o:, o:], L[o:, o - 1:-1])
    return ((L - shade) >= 80) & (L >= 110), L


def feature(bgr, s):
    """How much each pixel looks like a letter's, 0..1: light, and near a letter's shadow edge."""
    edge, L = edges(bgr, s)
    if edge.sum() < 10:
        return np.zeros(L.shape, np.float32)
    peak = float(np.percentile(L[edge], 95))
    k = max(3, int(round(5 * s)) | 1)
    near = cv2.dilate(edge.astype(np.uint8), np.ones((k, k), np.uint8)) > 0
    return (np.clip((L - FEAT_LOW * peak) / ((0.95 - FEAT_LOW) * peak), 0, 1) * near).astype(np.float32)


def _ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float((a * b).sum()) / d if d > 1e-6 else 0.0


def _same_end(a, b):
    """How alike two lines' ends are (-1..1), give or take a pixel up or down."""
    if a.shape != b.shape:
        return 0.0
    return max(_ncc(a[max(0, d):a.shape[0] + min(0, d)], b[max(0, -d):b.shape[0] + min(0, -d)]) for d in (-1, 0, 1))


def _amount(row):
    """What a row says, if it was read: (kind, amount), or None."""
    said = row.said
    return said[:2] if said and said[0] not in (None, "text") and said[1] is not None else None


@dataclass
class Row:
    right: int              # where its line ends (x in the band)
    left: int
    colour: str             # "white" or "yellow"
    bright: float           # how bright its letters are (255: just in)
    look: np.ndarray        # its shape, small, to find it again a row up
    end: np.ndarray         # its right end (the amount) as it is, to tell "+4" from "+7"
    said: tuple = None      # what it says (Font.read), when it could be read


@dataclass
class Line:
    """A line in the stack, from the look it came in. Once read (done): kind ("mesos", "exp",
    "bonus", "text" for a line with no amount, or None if it couldn't be read) and amount."""
    born: float
    colour: str
    row: int = 0
    bright: float = 255.0
    tries: list = field(default_factory=list)      # its readings so far: (kind, amount, sure)
    kind: str = None
    amount: int = None
    band: np.ndarray = None                        # its latest picture (Tesseract reads "text" lines from it)
    done: bool = False


class Font:
    """The message font's digits and "+", and the words that tell the kind of line."""

    def __init__(self, assets):
        data = np.load(str(Path(assets) / "msg_font.npz"))
        self.chars = {c: data[f"c{CHARS.index(c)}"].astype(np.float32) for c in CHARS}
        self.kinds = {k: data[f"k_{k}"].astype(np.float32) for k in KINDS if f"k_{k}" in data}
        self._scaled = {}

    def _at(self, s):
        """The shapes at scale s (the game at another size than 4K)."""
        if s not in self._scaled:
            def sz(t):
                return cv2.resize(t, (max(1, round(t.shape[1] * s)), max(1, round(t.shape[0] * s))),
                                  interpolation=cv2.INTER_AREA)
            self._scaled[s] = ({c: sz(t) for c, t in self.chars.items()}, {k: sz(t) for k, t in self.kinds.items()})
        return self._scaled[s]

    def read(self, band, s, right):
        """What a row's line says, from its band (a picture HALF above and below its middle, and a
        little more) and where it ends: (kind, amount, sure). kind: "mesos", "exp", "bonus", or "text"
        for a line that doesn't end in an amount (an item: Tesseract reads those); None when it went
        wrong part way. sure: every digit, and the kind, were clear."""
        F = feature(band, s)
        chars, kinds = self._at(s)
        h, w = chars["+"].shape
        mid = F.shape[0] // 2
        y0, y1 = max(0, mid - h // 2 - 2), min(F.shape[0], mid - h // 2 + h + 2)
        digits, sure, x1 = "", True, int(round(right - BRACKET_W * s))
        for k in range(8):
            # The first digit is looked for a few pixels either way (where a line ends is only known
            # to a pixel or two); each next one right where the last one was found, give or take one.
            slack = 3 if k == 0 else 1
            x0 = x1 - w - slack
            if x0 < 0:
                return ("text", None, False)
            area = F[y0:y1, x0:x1 + slack]
            if area.shape[0] < h or area.shape[1] < w:
                return ("text", None, False)
            ranked = []
            for c, t in chars.items():
                res = cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED)
                _, best, _, at = cv2.minMaxLoc(res)
                ranked.append((best, c, x0 + at[0]))
            ranked.sort(reverse=True)
            score, c, found = ranked[0]
            if score < READ_OK:
                return ("text", None, False) if k == 0 else (None, None, False)
            if score - ranked[1][0] < READ_MARGIN:
                sure = False
            if c == "+":
                break
            digits = c + digits
            x1 = found
        else:
            return (None, None, False)
        if not digits:
            return ("text", None, False)
        # The kind, from the word before the "(" (left of the "+").
        kind, best = None, KIND_OK
        xr = int(round(found - BRACKET_W * s))
        for kname, t in kinds.items():
            xl = xr - t.shape[1] - 2
            if xl < 0:
                continue
            area = F[max(0, mid - t.shape[0] // 2 - 2):mid - t.shape[0] // 2 + t.shape[0] + 2, xl:xr + 2]
            if area.shape[0] < t.shape[0] or area.shape[1] < t.shape[1]:
                continue
            _, sc, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED))
            if sc > best:
                kind, best = kname, sc
        return (kind, int(digits), sure and kind is not None)


class MessageWatcher:
    """Looks at the message stack again and again (look()), and says what new lines came in."""

    def __init__(self, font):
        self.font = font
        self.before = None              # the rows at the last look
        self.lines = {}                 # row -> Line, the lines in the stack now
        self.edge = None                # where the lines end, learned from the bright ones
        self.peak = {}                  # how bright new lines are, by colour (it depends on the game's size)
        self.skipped = 0                # looks in a row left out as unclear
        self.fit = []                   # how well the old lines were found again, at the last look

    def reset(self):
        self.before, self.lines, self.skipped = None, {}, 0

    def rows(self, img, gh):
        """Each row's line (a Row), or None where there's none, bottom row first."""
        s = gh / GH0
        h = int(round(HALF * s))
        out, bands = [], []
        W = img.shape[1]
        guess = W - EDGE_IN * s if self.edge is None else self.edge
        for k, y in enumerate(row_middles(img.shape[0], gh)):
            yc = int(round(y))
            m = int(round(4 * s))               # (room for the reader to line the shapes up)
            band = img[max(0, yc - h - m):yc + h + 1 + m]
            bands.append(band)
            body = band[m:-m] if m else band
            edge, L = edges(body, s)
            cols = edge.sum(axis=0) >= max(1, int(2 * s))
            xs = np.nonzero(cols)[0]
            near = 10 * s if self.edge is not None else 1.5 * GH0 * s     # (wider until it's learned)
            xs = xs[np.abs(xs + 1 - guess) <= near] if xs.size else xs
            if not xs.size:
                out.append(None)
                continue
            right = int(xs[-1]) + 1
            # The line's left end: letters and words, with gaps of no more than GAP.
            on = np.nonzero(cols[:right])[0]
            gaps = np.nonzero(np.diff(on) > GAP * s)[0]
            left = int(on[gaps[-1] + 1]) if gaps.size else int(on[0])
            if right - left < PART * s:
                out.append(None)
                continue
            px = body[edge]
            core = edge[:, right - int(LOOK_W * s):right]
            bright = float(np.median(L[:, right - int(LOOK_W * s):right][core])) if core.any() else 0.0
            yellow = px[:, 0].mean() < 0.55 * max(1.0, px[:, 2].mean())
            part = edge[:, right - int(LOOK_W * s):right].astype(np.float32)
            look = cv2.resize(part, (48, 8), interpolation=cv2.INTER_AREA)
            end = feature(body[:, right - int((END_W + BRACKET_W) * s):right - int(BRACKET_W * s)], s)
            # (Most looks see the same lines as the last: a row that hasn't changed isn't read again.)
            was = self.before[k] if self.before else None
            if was is not None and was.right == right and _same_end(was.end, end) > 0.98                     and float(np.abs(was.look - look).mean()) < 0.01:
                said = was.said
            else:
                said = self.font.read(band, s, right)
            if right - left < SHORTEST * s and not (said and said[0] not in (None, "text")):
                out.append(None)
                continue
            out.append(Row(right, left, "yellow" if yellow else "white", bright, look, end, said))
        return out, bands

    def _fresh(self, row):
        """Is a row's line as bright as a new one?"""
        return row.bright >= FRESH * self.peak.get(row.colour, row.bright)

    def _shift(self, before, now, s, age=None):
        """How many rows the stack moved up since the last look (new lines in at the bottom): the move
        that explains the most rows as lines that were already there (a row up or more, the same
        shape, no brighter), and the rest below them as new lines at full brightness."""
        best = None
        # The last line in (the bottom row before) has dimmed a little once it's a few tenths of a
        # second old: a new line now is brighter. (A monkey walking over it changes how it looks,
        # not how bright it is.)
        last = before[0]
        dimmed = last is not None and age is not None and age >= SETTLED
        for shift in range(0, ROWS):
            score, sames = 0.0, []
            newest = max((now[k].bright for k in range(shift) if now[k]), default=0.0)
            if shift and dimmed and newest < last.bright:
                score -= 3.0
            for k in range(ROWS):
                n = now[k]
                if k < shift:
                    score += NEW if n and self._fresh(n) else -3.0
                    continue
                b = before[k - shift]
                if b is None and n is None:
                    continue
                if b is None:
                    score -= 3.0                    # a line out of nowhere above the new ones
                elif n is None:
                    score -= 0.2                    # a line faded out (they do)
                else:
                    same = 0.5 * (1.0 - float(np.abs(b.look - n.look).mean()) * 2.5) + 0.5 * _same_end(b.end, n.end)
                    if n.colour != b.colour:                    # (a dim line's colour is less sure)
                        same -= 1.0 if min(self._fresh(n), self._fresh(b)) else 0.3
                    if _amount(b) is not None and _amount(n) is not None:
                        same += 0.3 if _amount(b) == _amount(n) else -1.5     # (read: the same line or not)
                    brighter = (n.bright - b.bright) / self.peak.get(n.colour, 255) - DIM_STEP
                    same -= min(3.0, max(0.0, brighter) / DIM_STEP)
                    score += same
                    sames.append(same)
            score -= 0.05 * shift
            if best is None or score > best[0]:
                best = (score, shift, sames)
        self.fit = best[2]
        return best[1]

    def look(self, img, gh, now):
        """A new look at stack_area (a picture of it), at time now. Returns the lines that came in and
        have been read since the last look (Line, done). A line is read again at the next looks
        while it's unclear (and while it's there): a row up, the scenery behind it is different."""
        s = gh / GH0
        rows, bands = self.rows(img, gh)
        for r in rows:
            if r:
                top = self.peak.get(r.colour, 0) * 0.999
                self.peak[r.colour] = max(top, r.bright)
        bright_ends = [r.right for r in rows if r and self._fresh(r)]
        if bright_ends:
            e = float(np.median(bright_ends))
            self.edge = e if self.edge is None else 0.9 * self.edge + 0.1 * e
        if self.before is None:                 # (the first look: what's there is old)
            self.before = rows
            self.lines = {k: Line(now, r.colour, k, r.bright, done=True) for k, r in enumerate(rows) if r}
            return []
        bottom = self.lines.get(0)
        shift = self._shift(self.before, rows, s, now - bottom.born if bottom else None)
        # A look where none of the lines that should still be there (a row or more up) is found again
        # is left out: something passed over the stack (a skill's flash, a monster). The next look is
        # compared with the last clear one; lines last 2 to 3 seconds, so nothing's lost meanwhile.
        reach = sum(1 for k, b in enumerate(self.before) if b is not None and k + shift < ROWS)
        if reach >= 2 and not self.fit and self.skipped < SKIPS:
            self.skipped += 1
            return []
        self.skipped = 0
        moved, out = {}, []
        for k, line in self.lines.items():
            if k + shift < ROWS and rows[k + shift] is not None:
                line.row, line.bright = k + shift, rows[k + shift].bright
                moved[k + shift] = line
            elif not line.done:
                self._finish(line, out)         # gone before it was clear: the best of its readings
        for k in range(shift):
            if rows[k] is not None:
                moved[k] = Line(now, rows[k].colour, k, rows[k].bright)
        self.lines, self.before = moved, rows
        for line in self.lines.values():
            if line.done:
                continue
            kind, amount, sure = rows[line.row].said
            line.band = bands[line.row]
            line.tries.append((kind, amount, sure))
            if sure or len(line.tries) >= TRIES:
                self._finish(line, out)
        return out

    @staticmethod
    def _finish(line, out):
        """A line's reading, from its tries: a sure one; else the amount most of them read; else
        "text" (no amount: an item, for Tesseract); else None (nothing could be read)."""
        sure = [t for t in line.tries if t[2]]
        amounts = [t[:2] for t in line.tries if t[0] not in (None, "text") and t[1] is not None]
        if sure:
            line.kind, line.amount = sure[0][:2]
        elif amounts:
            line.kind, line.amount = max(set(amounts), key=amounts.count)
        elif any(t[0] == "text" for t in line.tries):
            line.kind, line.amount = "text", None
        if line.band is not None:
            line.band = line.band.copy()
        line.done = True
        out.append(line)
