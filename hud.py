"""The EXP tracker card drawn over the game, in the look of MapleStory's own windows.

A wooden header with a maple leaf, a cream body, the "LV." badge with orange digit tiles, a green
EXP gauge with a slime riding its end, and the time to the next level; then, depending on the
layout, a row of session numbers and a row of mesos numbers:

    minimal   header + level, gauge and time to level
    session   ...plus last 5 min, EXP/hr, this session and time to level
    full      ...plus mesos/hr, net session, mesos gained and mesos spent

Four colour themes (brown, orange, blue, steel) change the header, the badge and the time.

Everything is drawn here with Pillow in units of the reference's pixels and scaled, so it stays
sharp at any size; the slime, leaves and coins are pictures in assets/hud. The card is drawn in
two parts: everything but the slime (again only when a number changes), and on top of that, many
times a second, the slime (bobbing, hopping when EXP comes in), the "+EXP" that floats up, and
in edit mode the frame you drag.

It's a borderless, click-through window over the game, painted pixel by pixel (rounded corners
and a soft shadow). In edit mode (Shift+5 by default) it stops being click-through: drag it to
move it, drag its right edge to make it wider, its bottom edge to make it bigger, its corner for
both, and right-click for its layout, theme and size. Where it is, is kept relative to the game
window, so it follows the game.
"""
import math
import threading
import time
import tkinter as tk
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

LAYOUTS = {"minimal": "Minimal", "session": "Session EXP", "full": "Full EXP + Mesos"}
THEMES = {"brown": "Brown", "orange": "Orange", "blue": "Blue", "steel": "Steel"}

W0 = 1390                   # the card's usual width in units (the reference's pixels)
MIN_WIDTH, MAX_WIDTH = 950, 2800
HEIGHTS = {"minimal": 186, "session": 284, "full": 460}
ROWS = {"minimal": 1, "session": 2, "full": 4}          # rows of numbers (extra height is shared between them)
REPOT_ROW = 92              # units: the full layout's repot row (it can be turned off)


def _height(layout, repot=True):
    """The card's height in units (shadow not included), with or without the full layout's repot row."""
    return HEIGHTS.get(layout, HEIGHTS["full"]) - (0 if repot or layout != "full" else REPOT_ROW)


def _rows(layout, repot=True):
    return ROWS.get(layout, 4) - (0 if repot or layout != "full" else 1)
MAX_TALL = 320              # units of extra height the bottom edge can add
MARGIN = 12                 # units of room around the card for its shadow
TOP_ROOM = 50               # ...and more above it, for the "+EXP" floating up from the slime
SS = 2                      # drawn this many times larger, then shrunk: smooth edges
MIN_SCALE, MAX_SCALE = 0.25, 1.5
SLIME_H = 96                # units: the slime's height

# Sampled from the reference pictures.
CREAM, CREAM_RIM, PANEL_RIM, DIVIDER = (253, 246, 235), (228, 214, 197), (236, 225, 211), (230, 217, 202)
LABEL, VALUE = (102, 83, 73), (26, 20, 16)
GOOD, BAD = (22, 128, 26), (226, 16, 38)
EXP_YELLOW, WHITE = (255, 228, 107), (255, 255, 255)
TILE_TOP, TILE_BOTTOM, TILE_RIM, TILE_HI, TILE_INK = (255, 181, 64), (232, 115, 15), (185, 80, 12), (255, 214, 130), (160, 66, 10)
FILL_STOPS = ((216, 252, 100), (160, 228, 20), (97, 175, 10))
TROUGH_TOP, TROUGH_BOTTOM, TROUGH_RIM = (70, 66, 74), (42, 40, 44), (43, 42, 46)
SHADOW = (40, 25, 10)

THEME = {
    "brown": dict(header=((185, 127, 70), (150, 98, 51)), header_hi=(216, 151, 83), header_edge=(120, 76, 41),
                  outline=(92, 68, 50), title_shadow=(90, 58, 30), leaf="leaf_orange",
                  badge=((60, 58, 68), (30, 33, 46)), badge_rim=(32, 39, 51), badge_hi=(110, 108, 118),
                  time_label=LABEL, time_value=VALUE),
    "orange": dict(header=((251, 168, 1), (182, 98, 2)), header_hi=(255, 211, 106), header_edge=(117, 61, 12),
                   outline=(107, 58, 12), title_shadow=(130, 62, 0), leaf="leaf_yellow",
                   badge=((60, 58, 68), (30, 33, 46)), badge_rim=(32, 39, 51), badge_hi=(110, 108, 118),
                   time_label=(150, 84, 10), time_value=(120, 52, 0)),
    "blue": dict(header=((20, 146, 217), (10, 90, 154)), header_hi=(110, 190, 240), header_edge=(12, 64, 116),
                 outline=(12, 58, 104), title_shadow=(8, 50, 100), leaf="leaf_blue",
                 badge=((22, 92, 170), (14, 66, 133)), badge_rim=(10, 53, 107), badge_hi=(120, 170, 225),
                 time_label=(12, 61, 154), time_value=(18, 79, 190)),
    "steel": dict(header=((159, 167, 178), (113, 122, 137)), header_hi=(225, 230, 234), header_edge=(88, 97, 112),
                  outline=(74, 79, 89), title_shadow=(70, 76, 88), leaf="leaf_gray",
                  badge=((112, 120, 137), (94, 106, 124)), badge_rim=(74, 79, 89), badge_hi=(190, 196, 208),
                  time_label=(88, 96, 110), time_value=(40, 46, 56)),
}

_WIN_FONTS = Path(r"C:\Windows\Fonts")


class Art:
    """The fonts and pictures the card is drawn with (each loaded once, kept at each size used)."""

    def __init__(self, assets):
        assets = Path(assets)
        fonts = assets / "fonts"
        self.font_paths = {"bold": next((p for p in (fonts / "MaplestoryBold.ttf", _WIN_FONTS / "tahomabd.ttf",
                                                     fonts / "ManropeExtraBold.ttf") if p.exists()), None)}
        self.sprite_dir = assets / "hud"
        self._fonts, self._sprites, self._sized = {}, {}, {}

    def font(self, px):
        px = max(6, int(round(px)))
        if px not in self._fonts:
            path = self.font_paths["bold"]
            self._fonts[px] = ImageFont.truetype(str(path), px) if path else ImageFont.load_default()
        return self._fonts[px]

    def sprite(self, name, height, stretch=(1.0, 1.0)):
        """The picture, `height` pixels tall (None if it's missing); stretch (x, y) squashes or stretches it."""
        sx, sy = round(stretch[0], 2), round(stretch[1], 2)
        height = max(2, int(round(height * sy)))
        key = (name, height, sx, sy)
        if key not in self._sized:
            if name not in self._sprites:
                path = self.sprite_dir / f"{name}.png"
                self._sprites[name] = Image.open(path).convert("RGBA") if path.exists() else None
            img = self._sprites[name]
            if len(self._sized) > 400:                         # (sizes from old resizes aren't needed again)
                self._sized.clear()
            self._sized[key] = None if img is None else img.resize(
                (max(1, round(img.width * height / sy / img.height * sx)), height), Image.LANCZOS)
        return self._sized[key]


# --------------------------------------------------------------------------- drawing

def _gradient(w, h, stops):
    """A top-to-bottom gradient through the colours in stops, as an RGB picture."""
    w, h = max(1, int(w)), max(1, int(h))
    t = np.linspace(0, 1, h)[:, None]
    pos = np.linspace(0, 1, len(stops))
    rows = np.stack([np.interp(t[:, 0], pos, [s[c] for s in stops]) for c in range(3)], axis=1)
    return Image.fromarray(np.repeat(rows[:, None, :], w, axis=1).astype(np.uint8), "RGB")


def _rounded(size, box, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, max(0, radius), fill=255)
    return mask


class _Canvas:
    """The card being drawn: an RGBA picture, with sizes given in units (scaled by k)."""

    def __init__(self, w, h, k, art):
        self.k, self.art = k, art
        self.img = Image.new("RGBA", (int(round(w * k)), int(round(h * k))), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def u(self, v):
        return int(round(v * self.k))

    def box(self, x, y, w, h, radius, stops, rim=None, rim_w=0, hi=None):
        """A rounded box with a gradient, an optional rim and an optional highlight line along its top."""
        x0, y0, x1, y1 = self.u(x), self.u(y), self.u(x + w), self.u(y + h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        r = self.u(radius)
        if rim:
            self.img.paste(rim + (255,), (x0, y0), _rounded((x1 - x0, y1 - y0), (0, 0, x1 - x0 - 1, y1 - y0 - 1), r))
            rw = max(1, self.u(rim_w))
            x0, y0, x1, y1, r = x0 + rw, y0 + rw, x1 - rw, y1 - rw, max(0, r - rw)
        mask = _rounded((x1 - x0, y1 - y0), (0, 0, x1 - x0 - 1, y1 - y0 - 1), r)
        self.img.paste(_gradient(x1 - x0, y1 - y0, stops), (x0, y0), mask)
        if hi:                                              # a light line just inside the top
            line = Image.new("L", (x1 - x0, y1 - y0), 0)
            hw = max(1, self.u(2))
            ImageDraw.Draw(line).rectangle((r // 2, hw, x1 - x0 - r // 2, hw * 2), fill=255)
            self.img.paste(hi + (255,), (x0, y0), Image.fromarray(np.minimum(np.asarray(line), np.asarray(mask))))

    def line(self, x0, y0, x1, y1, color, width=2):
        self.d.line((self.u(x0), self.u(y0), self.u(x1), self.u(y1)), fill=color + (255,), width=max(1, self.u(width)))

    def text(self, x, y, text, size, color, anchor="lm", stroke=0, stroke_color=None, shadow=None, room=None):
        """Text at (x, y) units; made smaller until it fits in `room` units if given. Returns its width (units)."""
        text = plain(text)
        font = self.art.font(size * self.k)
        if room:
            while font.getlength(text) > room * self.k and font.size > 6:
                font = self.art.font(font.size - 1)
        sw = self.u(stroke) if stroke else 0
        if shadow:
            self.d.text((self.u(x), self.u(y) + max(1, self.u(2))), text, font=font, fill=shadow + (200,), anchor=anchor,
                        stroke_width=sw, stroke_fill=shadow + (200,))
        self.d.text((self.u(x), self.u(y)), text, font=font, fill=color + (255,), anchor=anchor,
                    stroke_width=sw, stroke_fill=(stroke_color or color) + (255,))
        return font.getlength(text) / self.k

    def sprite(self, name, x, y, height, anchor="lm"):
        """A picture `height` units tall at (x, y): anchor "lm" (left, middle), "mm" or "mb" (middle, bottom)."""
        img = self.art.sprite(name, height * self.k)
        if img is None:
            return 0
        px, py = self.u(x), self.u(y)
        px -= {"l": 0, "m": img.width // 2}[anchor[0]]
        py -= {"m": img.height // 2, "b": img.height}[anchor[1]]
        self.img.alpha_composite(img, (max(0, px), max(0, py)))
        return img.width / self.k


def plain(text):
    """The text with the characters the Maplestory typeface doesn't have (it draws them as boxes) swapped."""
    return text.replace("—", "-").replace("–", "-")


def _tone(value):
    value = value.lstrip("~")                       # (a guess, while the inventory is closed: coloured the same)
    return GOOD if value.startswith("+") and value != "+0" else BAD if value.startswith("-") and value != "-" else VALUE


def render_base(layout, theme, scale, c, art, width=W0, tall=0, repot=True):
    """The card without its slime, as an RGBA picture (shadow included), and where things are on it
    (in its pixels) for compose(): layout and theme as in LAYOUTS / THEMES, scale = pixels per unit,
    width = its width in units, tall = extra height in units (shared out between its rows: the text
    stays the same size), repot = the full layout's repot row on or off, c = what it shows (see App._update_hud)."""
    t = THEME.get(theme, THEME["brown"])
    tall = min(MAX_TALL, max(0, tall))
    H0 = _height(layout, repot) + tall
    extra = tall / _rows(layout, repot)
    W = min(MAX_WIDTH, max(MIN_WIDTH, width))
    cw = (W - 22) / 4                               # the columns everything lines up on
    k = max(scale, min(scale * SS, 1.0))        # (drawn at least at the reference's size, then shrunk: smooth edges)
    cv = _Canvas(W + 2 * MARGIN, H0 + 2 * MARGIN + TOP_ROOM, k, art)
    x0, y0 = MARGIN, MARGIN + TOP_ROOM

    # ---- the shadow, the frame, the body and the header
    shadow = _rounded(cv.img.size, (cv.u(x0 + 2), cv.u(y0 + 6), cv.u(x0 + W - 2), cv.u(y0 + H0 + 4)), cv.u(22))
    shadow = shadow.filter(ImageFilter.GaussianBlur(cv.u(6)))
    cv.img.paste(SHADOW + (255,), (0, 0), shadow.point(lambda v: v * 0.38))
    cv.box(x0, y0, W, H0, 22, [CREAM], rim=t["outline"], rim_w=3.5)
    inner = (x0 + 3.5, y0 + 3.5, W - 7, H0 - 7)
    cv.d.rounded_rectangle((cv.u(inner[0]), cv.u(inner[1]), cv.u(inner[0] + inner[2]) - 1, cv.u(inner[1] + inner[3]) - 1),
                           cv.u(18.5), outline=CREAM_RIM + (255,), width=max(1, cv.u(2.5)))
    head_h = 62
    hx0, hy0, hx1, hy1 = cv.u(inner[0]), cv.u(inner[1]), cv.u(inner[0] + inner[2]), cv.u(inner[1] + head_h)
    head_mask = _rounded((hx1 - hx0, cv.u(inner[1] + inner[3]) - hy0), (0, 0, hx1 - hx0 - 1, cv.u(inner[3]) - 1), cv.u(18.5))
    head_mask = head_mask.crop((0, 0, hx1 - hx0, hy1 - hy0))
    cv.img.paste(_gradient(hx1 - hx0, hy1 - hy0, t["header"]), (hx0, hy0), head_mask)
    hl = Image.new("L", head_mask.size, 0)
    ImageDraw.Draw(hl).rectangle((0, max(1, cv.u(1.5)), head_mask.size[0], max(2, cv.u(3.5))), fill=255)
    cv.img.paste(t["header_hi"] + (255,), (hx0, hy0), Image.fromarray(np.minimum(np.asarray(hl), np.asarray(head_mask))))
    cv.d.rectangle((hx0, hy1 - max(1, cv.u(3)), hx1 - 1, hy1), fill=t["header_edge"] + (255,))
    head_cy = y0 + 3.5 + head_h / 2
    cv.sprite(t["leaf"], x0 + 22, head_cy, 40)
    cv.text(x0 + 78, head_cy + 1, "EXP TRACKER", 31, WHITE, shadow=t["title_shadow"])
    if c.get("calibrating") is not None:            # the first 5 minutes: the rates aren't reliable yet
        left = int(c["calibrating"])
        cv.text(x0 + W - 26, head_cy + 1, f"CALIBRATING  {left // 60}:{left % 60:02d}", 24, (255, 244, 214),
                anchor="rm", shadow=t["title_shadow"])

    # ---- the top row: level badge, gauge (the slime is added by compose()), time to the next level
    top = y0 + 3.5 + head_h
    top_h = 106 + extra
    cy = top + top_h / 2 + 1
    digits = str(c.get("level") or "?")
    lv_font = 44
    lv_w = art.font(lv_font * k).getlength("LV.") / k
    tile_w, tile_h, gap = 47, 60, 5
    badge_x, badge_h = x0 + 24, 80
    badge_w = 18 + lv_w + 14 + len(digits) * (tile_w + gap) - gap + 12
    cv.box(badge_x, cy - badge_h / 2, badge_w, badge_h, 15, t["badge"], rim=t["badge_rim"], rim_w=3, hi=t["badge_hi"])
    cv.text(badge_x + 18, cy + 1, "LV.", lv_font, WHITE, stroke=1.5, stroke_color=(20, 20, 26))
    tx = badge_x + 18 + lv_w + 14
    for ch in digits:
        cv.box(tx, cy - tile_h / 2, tile_w, tile_h, 9, [TILE_TOP, TILE_BOTTOM], rim=TILE_RIM, rim_w=2.5, hi=TILE_HI)
        cv.text(tx + tile_w / 2, cy + 2, ch, 46, WHITE, anchor="mm", stroke=3, stroke_color=TILE_INK)
        tx += tile_w + gap
    badge_end = badge_x + badge_w

    # The time to the next level, on the right, in the last column. With the session row below (which shows
    # it too), it isn't shown twice: the gauge gets that room instead.
    nxt = c.get("next_level")
    time_top = layout == "minimal"
    rb = x0 + 11 + 3 * cw if time_top else x0 + W - 4
    if time_top:
        cv.line(rb, cy - 38, rb, cy + 38, DIVIDER, 2.5)
        cv.text(rb + 34, cy - 22, f"TO LV. {nxt}" if nxt else "TO NEXT LV.", 23, t["time_label"])
        cv.text(rb + 34, cy + 20, c.get("time") or "--:--:--", 50, t["time_value"], room=cw - 46)

    gx0, gx1, gh = badge_end + 22, rb - 24, 54
    gy = cy - gh / 2
    cv.box(gx0, gy, gx1 - gx0, gh, gh / 2, [TROUGH_TOP, TROUGH_BOTTOM], rim=TROUGH_RIM, rim_w=3, hi=(96, 93, 100))
    fill = min(1.0, max(0.0, c.get("fill") or 0.0))
    inner_w = gx1 - gx0 - 9
    fx1 = gx0 + 4.5 + inner_w * fill
    if fill > 0 and fx1 - gx0 > gh * 0.6:
        cv.box(gx0 + 4.5, gy + 4.5, fx1 - gx0 - 4.5, gh - 9, (gh - 9) / 2, FILL_STOPS, hi=(236, 248, 176))
    slime_x = min(max(fx1, gx0 + 52), gx1 - 46)
    sprite = art.sprite("slime_happy", SLIME_H)
    slime_w = sprite.width / sprite.height * SLIME_H if sprite else 90
    # EXP 1,680,960 / 2,592,000 [64.8%], beside the slime (or a status line)
    if c.get("status"):
        cv.text((slime_x + slime_w / 2 + gx1) / 2, cy + 1, c["status"], 25, WHITE, anchor="mm", stroke=2,
                stroke_color=(30, 30, 34), room=gx1 - slime_x - slime_w / 2 - 24)
    else:
        parts = [("EXP", 28, EXP_YELLOW), (f"{c.get('exp', '?')} / {c.get('req', '?')}", 25, WHITE),
                 (f"[{c.get('pct', '?')}]", 27, WHITE)]
        widths = [art.font(size * k).getlength(text) / k for text, size, _ in parts]
        need = sum(widths) + 16 + 22
        # Right of the slime (on the dark part, as in the reference) when it fits; else left of it, on the green.
        right_room = gx1 - 24 - (slime_x + slime_w / 2 + 6)
        left_room = slime_x - slime_w / 2 - 4 - (gx0 + 22)
        if need <= right_room or right_room >= left_room:
            room, end = right_room, gx1 - 24
        else:
            room, end = left_room, slime_x - slime_w / 2 - 4
        shrink = min(1.0, room / max(1, need))
        x = end - need * shrink
        for (text, size, color), w, space in zip(parts, widths, (16, 22, 0)):
            cv.text(x, cy + 1, text, size * shrink, color, stroke=2, stroke_color=(30, 30, 34))
            x += (w + space) * shrink

    # ---- the session row, the mesos row and the repot row: four columns each
    bell_at = None

    def panel(py, ph, cells, icons=False):
        nonlocal bell_at
        cv.box(x0 + 11, py, W - 22, ph, 13, [CREAM], rim=PANEL_RIM, rim_w=2)
        mid = py + ph / 2
        for i, (icon, label, value, tone) in enumerate(cells):
            cx = x0 + 11 + i * cw
            if i:
                cv.line(cx, py + 16, cx, py + ph - 16, DIVIDER, 2.5)
            tx = cx + 34
            if icons:
                if icon == "bell":                  # (the bell is drawn by compose(): it rings)
                    bell_at = (cx + 52, mid + 2)
                else:
                    cv.sprite(icon, cx + 26, mid + 2, 54)
                tx = cx + 102
            cv.text(tx, mid - 18, label, 23, LABEL, room=cx + cw - tx - 12)
            waiting = value.startswith(("Calibrating", "Ring the bell", "Open your inventory"))
            color = LABEL if waiting else _tone(value) if tone else t["time_value"] if label.startswith("TO ") else VALUE
            cv.text(tx, mid + 18, value, (26 if waiting else 43) if not icons else (26 if waiting else 41), color,
                    room=cx + cw - tx - 12)

    # (As in the reference, only the net session and mesos spent are coloured: green up, red down; and the
    # repot profits, the same way.)
    y = top + top_h + 4
    if layout in ("session", "full"):
        nxt_label = f"TO LV. {nxt}" if nxt else "TO NEXT LV."
        panel(y, 94 + extra, [(None, "LAST 5 MIN", c.get("last5", "-"), False),
                              (None, "EXP/HR", c.get("rate", "-"), False),
                              (None, "THIS SESSION", c.get("session", "-"), False),
                              (None, nxt_label, c.get("time") or "--:--:--", False)])
        y += 94 + extra + 4
    if layout == "full":
        panel(y, 88 + extra, [("coin_pair", "MESOS / HR", c.get("mesos_rate", "-"), False),
                              ("coin", c.get("net_label", "NET SESSION"), c.get("mesos_net", "-"), True),
                              ("coin_stack", "MESOS GAINED", c.get("mesos_gained", "-"), False),
                              ("money_bag", "MESOS SPENT", c.get("mesos_spent", "-"), True)], icons=True)
        y += 88 + extra + 4
    if layout == "full" and repot:
        panel(y, 88 + extra, [("bell", "LAST REPOT", c.get("repot_at", "-"), False),
                              ("coin", "SINCE REPOT", c.get("repot_since", "-"), True),
                              ("coin_stack", "LAST TRIP", c.get("repot_trip", "-"), True),
                              ("money_bag", "POT COST", c.get("repot_pots", "-"), True)], icons=True)

    size = card_size(layout, scale, W, tall, repot)
    img = cv.img.resize(size, Image.LANCZOS)
    info = {"ppu": scale, "slime": ((slime_x - 6) * scale, (gy + gh + 9) * scale),
            "card": (x0 * scale, y0 * scale, (x0 + W) * scale, (y0 + H0) * scale),
            "header": (head_cy * scale, (x0 + W - 26) * scale),
            "bell": (bell_at[0] * scale, bell_at[1] * scale) if bell_at else None,
            "columns": [(x0 + 11 + i * cw + 34) * scale for i in range(4)],
            "time_x": (rb + 34) * scale if time_top else None, "gauge": (gx0 * scale, gx1 * scale)}
    return img, info


def _paste(img, part, x, y):
    """Puts the RGBA picture `part` on img with its top-left at (x, y), cutting off whatever sticks out."""
    x, y = int(round(x)), int(round(y))
    l, t = max(0, -x), max(0, -y)
    r, b = min(part.width, img.width - x), min(part.height, img.height - y)
    if r > l and b > t:
        img.alpha_composite(part.crop((l, t, r, b)) if (l, t, r, b) != (0, 0, part.width, part.height) else part,
                            (x + l, y + t))


def _floating_text(art, text, px, color, alpha):
    """Text with a dark outline (like the game's damage numbers), faded to alpha, as an RGBA picture."""
    font = art.font(px)
    stroke = max(1, round(px / 9))
    l, t, r, b = font.getbbox(text, stroke_width=stroke)
    layer = Image.new("RGBA", (r - l + 2, b - t + 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((1 - l, 1 - t), text, font=font, fill=color + (255,), stroke_width=stroke,
                               stroke_fill=(40, 26, 10, 255))
    if alpha < 1:
        a = np.asarray(layer).copy()
        a[:, :, 3] = (a[:, :, 3] * max(0.0, alpha)).astype(np.uint8)
        layer = Image.fromarray(a, "RGBA")
    return layer


def compose(base, info, art, a):
    """One frame of the card: the base picture with the slime and what floats over it on top.

    a: slime (picture name), dx / dy (units: the slime's offset, up is negative), sx / sy (its
    squash and stretch), floats [(text, colour, size in units, rise in units, alpha, from "slime" or
    "bell")], zs [(rise, alpha)] (a sleeping slime's z's), bell (its tilt in degrees and how much
    bigger it is, while it rings), edit (draw the edit-mode frame), opacity (0 to 1)."""
    img = base.copy()
    ppu = info["ppu"]
    bell = info.get("bell")
    if bell:                                       # the repot bell, swinging while it rings
        angle, pop = a.get("bell", (0.0, 1.0))
        b = art.sprite("bell", 56 * ppu * pop)
        if b is not None:
            if abs(angle) > 0.5:
                b = b.rotate(angle, resample=Image.BICUBIC, expand=True, center=(b.width / 2, b.height * 0.9))
            _paste(img, b, bell[0] - b.width / 2, bell[1] - b.height / 2)
    cx, bottom = info["slime"]
    sprite = art.sprite(f"slime_{a.get('slime', 'happy')}", SLIME_H * ppu, (a.get("sx", 1.0), a.get("sy", 1.0)))
    top = rest = bottom - SLIME_H * ppu            # (where its top is, and is at rest: the "+EXP" floats from there)
    if sprite:
        x = cx + a.get("dx", 0) * ppu - sprite.width / 2
        y = bottom + a.get("dy", 0) * ppu - sprite.height
        _paste(img, sprite, x, y)
        top = y
    for rise, alpha in a.get("zs", ()):
        z = _floating_text(art, "z", 26 * ppu * (0.7 + rise / 80), (190, 214, 255), alpha)
        _paste(img, z, cx + (28 + rise * 0.45) * ppu, top + (10 - rise) * ppu - z.height)
    for text, color, size, rise, alpha, origin in a.get("floats", ()):
        f = _floating_text(art, text, size * ppu, color, alpha)
        if origin == "header":                     # a toast in the header, on a dark pill (over the countdown)
            hy, hx = info["header"]
            pad = round(12 * ppu)
            pill = Image.new("RGBA", (f.width + 2 * pad, f.height + pad), (0, 0, 0, 0))
            ImageDraw.Draw(pill).rounded_rectangle((0, 0, pill.width - 1, pill.height - 1), pill.height // 2,
                                                   fill=(48, 30, 16, round(235 * alpha)))
            pill.alpha_composite(f, (pad, pad // 2))
            _paste(img, pill, hx - pill.width, hy - pill.height / 2 - min(rise, 14) * ppu * 0.25)
        elif origin == "slime":
            _paste(img, f, cx - f.width / 2, rest - rise * ppu - f.height)
    if a.get("edit"):
        _edit_frame(img, info)
    opacity = a.get("opacity", 1.0)
    if opacity < 1:
        arr = np.asarray(img).copy()
        arr[:, :, 3] = (arr[:, :, 3] * opacity).astype(np.uint8)
        img = Image.fromarray(arr, "RGBA")
    return img


GOLD = (255, 204, 51)


def _edit_frame(img, info):
    """Edit mode: a dashed gold frame, handles on the right and bottom edges and in the corner, and a hint."""
    x0, y0, x1, y1 = (int(round(v)) for v in info["card"])
    ppu = info["ppu"]
    w = max(2, round(4 * ppu))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), max(2, round(22 * ppu)), outline=GOLD + (255,), width=w)
    dash = max(6, round(26 * ppu))
    yy, xx = np.mgrid[0:img.height, 0:img.width]
    arr = np.asarray(layer).copy()
    arr[((xx + yy) // dash) % 2 == 1, 3] = 0                   # (dashes)
    layer = Image.fromarray(arr, "RGBA")
    d = ImageDraw.Draw(layer)
    hs = max(6, round(16 * ppu))                               # the handles
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    d.rounded_rectangle((x1 - hs // 2, my - hs * 2, x1 + hs // 2, my + hs * 2), hs // 2, fill=GOLD + (255,),
                        outline=(120, 80, 0, 255))
    d.rounded_rectangle((mx - hs * 2, y1 - hs // 2, mx + hs * 2, y1 + hs // 2), hs // 2, fill=GOLD + (255,),
                        outline=(120, 80, 0, 255))
    g = hs * 3
    d.polygon([(x1 + 1, y1 - g), (x1 + 1, y1 + 1), (x1 - g, y1 + 1)], fill=GOLD + (255,), outline=(120, 80, 0, 255))
    img.alpha_composite(layer)


def render(layout, theme, scale, c, art, opacity=1.0, width=W0, tall=0, repot=True):
    """The card as a still picture: the slime at rest (for previews and tests)."""
    base, info = render_base(layout, theme, scale, c, art, width, tall, repot)
    return compose(base, info, art, {"slime": c.get("slime", "happy"), "opacity": opacity})


def card_size(layout, scale, width=W0, tall=0, repot=True):
    """The card's window size in pixels (shadow included)."""
    width = min(MAX_WIDTH, max(MIN_WIDTH, width))
    tall = min(MAX_TALL, max(0, tall))
    return (int(round((width + 2 * MARGIN) * scale)),
            int(round((_height(layout, repot) + tall + 2 * MARGIN + TOP_ROOM) * scale)))


# --------------------------------------------------------------------------- the slime's moves

HOP = 0.62                  # seconds per hop


def _hop(p):
    """(height in units, x stretch, y stretch) at p (0 to 1) through a hop: squash, jump, stretch, land, squash."""
    height = 40 * 4 * p * (1 - p)
    if p < 0.14:
        s = 0.84 + 0.22 * (p / 0.14)                          # pushing off
    elif p < 0.82:
        s = 1.06                                               # in the air, a little stretched
    else:
        s = 1.06 - 0.24 * ((p - 0.82) / 0.18)                  # landing
    return height, 1 + (1 - s) * 0.9, s


RING = 1.0                  # seconds the repot bell swings for


class Moves:
    """What the slime is doing: hops (one per EXP gain, three for a level up), the numbers floating
    up, and its idle bob, wobble or sleep; and the repot bell ringing. frame() says where they are now."""

    def __init__(self):
        self.hops = []              # start times
        self.floats = []            # (text, colour, size, start, seconds, from "slime" or "bell")
        self.happy_until = 0.0      # after EXP: its happiest face for a moment
        self.rang = None            # when the repot bell was rung

    def gain(self, amount, now=None):
        """A hop, with "+amount EXP" floating up ("+EXP" when the amount isn't known)."""
        now = time.monotonic() if now is None else now
        start = max([now] + [h + HOP for h in self.hops if h + HOP > now])   # (after any hop in progress)
        self.hops = [h for h in self.hops if h + HOP > now][-2:] + [start]
        self.floats = [f for f in self.floats if f[3] + f[4] > now][-3:] + [
            (f"+{amount:,.0f} EXP" if amount else "+EXP", EXP_YELLOW, 30, now, 1.4, "slime")]
        self.happy_until = start + HOP + 1.2

    def level_up(self, now=None):
        now = time.monotonic() if now is None else now
        self.hops = [now, now + HOP, now + 2 * HOP]
        self.floats.append(("LEVEL UP!", (255, 214, 64), 42, now, 2.6, "slime"))
        self.happy_until = now + 3 * HOP + 1.5

    def ring(self, profit=None, now=None):
        """The repot bell: it swings, and the trip's profit (or loss) floats up from it."""
        now = time.monotonic() if now is None else now
        self.rang = now
        if profit is None:
            text, color = "DING!  REPOT SAVED", (255, 214, 64)
        else:
            text, color = f"DING!  TRIP {profit:+,.0f}", (120, 230, 110) if profit >= 0 else (255, 120, 110)
        self.floats.append((text, color, 28, now, 3.5, "header"))
        if profit is not None and profit > 0:
            self.happy_until = now + 2.0                     # (the slime's happy about a profit too)

    def busy(self, now):
        """True while something is moving fast (hops, floating numbers, the bell): draw often."""
        return (any(h + HOP > now for h in self.hops) or any(f[3] + f[4] > now for f in self.floats)
                or (self.rang is not None and now < self.rang + RING))

    def frame(self, mood, now):
        """The compose() settings for this moment, for a slime in this mood (from the tracker)."""
        a = {"slime": mood, "dx": 0.0, "dy": 0.0, "sx": 1.0, "sy": 1.0}
        hop = next((h for h in self.hops if h <= now < h + HOP), None)
        if hop is not None:
            height, sx, sy = _hop((now - hop) / HOP)
            a.update(dy=-height, sx=sx, sy=sy)
        elif mood == "sleepy":                                  # slow breathing, and z's
            breath = math.sin(now * 2 * math.pi / 3.2)
            a.update(sy=1 + 0.025 * breath, sx=1 - 0.02 * breath)
            a["zs"] = [(((now + k * 1.1) % 3.3) / 3.3 * 34, 1 - ((now + k * 1.1) % 3.3) / 3.3) for k in range(3)]
        elif mood == "dizzy":
            a["dx"] = 3 * math.sin(now * 2 * math.pi / 0.55)
        else:                                                   # a gentle bob
            bob = math.sin(now * 2 * math.pi / 1.6)
            a.update(dy=-2 * max(0.0, bob), sy=1 + 0.03 * bob, sx=1 - 0.025 * bob)
        if now < self.happy_until and mood not in ("sparkle", "dizzy"):
            a["slime"] = "squint"
        floats = []
        for text, color, size, start, secs, origin in self.floats:
            p = (now - start) / secs
            if 0 <= p < 1:
                floats.append((text, color, size * (1 + 0.25 * max(0.0, 0.15 - p) / 0.15), 10 + 46 * p,
                               1.0 if p < 0.6 else 1 - (p - 0.6) / 0.4, origin))
        a["floats"] = floats
        if self.rang is not None and 0 <= now - self.rang < RING:     # ding: a swing that dies away, and a pop
            p = (now - self.rang) / RING
            a["bell"] = (16 * math.sin(p * 2 * math.pi * 5) * (1 - p) ** 1.5, 1 + 0.18 * max(0.0, 1 - p * 4))
        return a


# --------------------------------------------------------------------------- windows

class _CardWindow:
    """The card's own window: click-through (except in edit mode), painted pixel by pixel."""

    def __init__(self, root, platform):
        self.platform = platform
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("+-32000+-32000")                   # (mapped once, out of sight, to set it up)
        self.label = tk.Label(self.win, bd=0, highlightthickness=0, bg="#fdf6eb")
        self.label.pack(fill="both", expand=True)
        self.win.update_idletasks()
        platform.make_overlay(self.win)
        # (Windows won't paint a see-through window that was hidden before its first picture: give it a blank one.)
        platform.paint_overlay(self.win, Image.new("RGBA", (1, 1), (0, 0, 0, 0)), -32000, -32000)
        platform.show_overlay(self.win, False)
        self.shown = False
        self.at = None              # where it is on screen (x, y, w, h)
        self.photo = None
        self.raised_at = 0.0

    def hide(self):
        if self.shown:
            self.platform.show_overlay(self.win, False)
            self.shown = False

    def paint(self, x, y, image):
        """Shows this picture at (x, y), on top of the game."""
        at = (x, y, image.width, image.height)
        if at != self.at:           # (tkinter is told too, so the mouse lands in the right place in edit mode)
            self.at = at
            self.win.geometry(f"{image.width}x{image.height}+{x}+{y}")
        if self.platform.paint_overlay(self.win, image, x, y):
            if not self.shown or time.monotonic() - self.raised_at > 1:
                self.platform.show_overlay(self.win, True)    # (now and then: stays above the game)
                self.raised_at = time.monotonic()
        else:                       # (flat, on cream)
            flat = Image.new("RGBA", image.size, CREAM + (255,))
            self.photo = ImageTk.PhotoImage(Image.alpha_composite(flat, image).convert("RGB"), master=self.win)
            self.label.configure(image=self.photo, width=image.width, height=image.height)
            self.platform.show_overlay(self.win, True, at)
        self.shown = True


class HudCard:
    """The EXP tracker card over the game. Its settings live in cfg (hud_layout, hud_theme, hud_scale,
    hud_width, hud_offset, hud_opacity); save() is called when they change."""

    EDGE = 12               # px: how near an edge the mouse must be to resize from it

    def __init__(self, root, platform, assets, cfg, save):
        self.root, self.platform = root, platform
        self.art = Art(assets)
        self.cfg, self.save = cfg, save
        self.window = _CardWindow(root, platform)
        self.moves = Moves()
        self.editing = False
        self.rect = None            # where the card is on screen (x, y, w, h), when shown
        self.game = None            # the game window's game area, the last time it was shown
        self.content = None
        self.mood = "happy"
        self._base = None           # (key, picture, info)
        self._fresh = None          # ...a newer one drawn in the background
        self._drawing = False
        self._drag = None
        self.on_clear_repot = None  # (the app's "clear the repot history", offered in the right-click menu)
        win = self.window.win
        win.bind("<ButtonPress-1>", self._press)
        win.bind("<B1-Motion>", self._move)
        win.bind("<ButtonRelease-1>", self._release)
        win.bind("<Button-3>", self._menu)
        win.bind("<Motion>", self._hover)
        self._tick()

    # ---- settings
    @property
    def layout(self):
        return self.cfg.get("hud_layout", "full")

    @property
    def theme(self):
        return self.cfg.get("hud_theme", "brown")

    @property
    def scale(self):
        return self.cfg.get("hud_scale", 0.5)

    @property
    def width(self):
        return self.cfg.get("hud_width", W0)

    @property
    def tall(self):
        return self.cfg.get("hud_tall", 0)

    @property
    def repot(self):
        """The full layout's repot row: on or off."""
        return self.cfg.get("hud_repot", True)

    def set_repot(self, on):
        self.cfg["hud_repot"] = bool(on)
        self.save()
        self.redraw()

    @property
    def offset(self):
        """Where the card's top-left is, from the game area's top-left (pixels at a 1080-tall game)."""
        off = self.cfg.get("hud_offset")
        return tuple(off) if off else None

    def _ppu(self, scale=None):
        """Pixels per unit, at this size and the game window's size."""
        return (self.scale if scale is None else scale) * (self.game[3] / 1080 if self.game else 1.0)

    def set_style(self, layout=None, theme=None):
        if layout in LAYOUTS:
            self.cfg["hud_layout"] = layout
        if theme in THEMES:
            self.cfg["hud_theme"] = theme
        self.save()
        self.redraw()

    def set_scale(self, scale):
        self.cfg["hud_scale"] = round(min(MAX_SCALE, max(MIN_SCALE, scale)), 3)
        self.redraw()

    def set_width(self, width):
        self.cfg["hud_width"] = int(round(min(MAX_WIDTH, max(MIN_WIDTH, width))))
        self.redraw()

    def set_tall(self, tall):
        """Extra height in units: the rows get roomier, the text stays the same size."""
        self.cfg["hud_tall"] = int(round(min(MAX_TALL, max(0, tall))))
        self.redraw()

    def resize_by(self, factor):
        self.set_scale(self.scale * factor)
        self.save()

    def move_to(self, screen_xy):
        """Puts the card's top-left at this screen point (kept as an offset from the game window)."""
        if not self.game:
            return
        gx, gy, _, gh = self.game
        f = 1080 / gh
        self.cfg["hud_offset"] = [round((screen_xy[0] - gx) * f), round((screen_xy[1] - gy) * f)]
        self.redraw()

    def set_editing(self, on):
        self.editing = bool(on)
        self.platform.set_click_through(self.window.win, not self.editing)
        if not self.editing:
            self.window.win.configure(cursor="")
            self.save()
        self.redraw()

    # ---- what's on it
    def celebrate(self, exp=0, level_up=False, kill=False):
        """EXP came in (a hop and "+EXP" floating up), a kill was seen (a hop, with the EXP it's likely
        worth, or just "+EXP" until that's known), or a level was reached (three hops)."""
        if level_up:
            self.moves.level_up()
        elif kill or (exp and exp > 0):
            self.moves.gain(exp if exp and exp > 0 else None)

    def ring(self, profit=None):
        """The repot bell was rung: it swings, with the trip's profit (if there was a trip before) floating up."""
        self.moves.ring(profit)

    def hide(self):
        self.window.hide()
        self.rect = None

    def place(self, game):
        """Where the card goes over this game area: (x, y, w, h) on screen."""
        gx, gy, gw, gh = game
        w, h = card_size(self.layout, self.scale * gh / 1080, self.width, self.tall, self.repot)
        off = self.offset
        if off is None:             # at first: the bottom left, above the game's chat (and its messages)
            x, y = gx + round(8 * gh / 1080), gy + gh - round(0.17 * gh) - h
        else:
            x, y = gx + round(off[0] * gh / 1080), gy + round(off[1] * gh / 1080)
        # never lost: at least a part of it stays over the game
        x = min(max(x, gx - w + 80), gx + gw - 80)
        y = min(max(y, gy - h + 60), gy + gh - 60)
        return x, y, w, h

    def show(self, game, content):
        self.game, self.content = game, content
        self.mood = content.get("slime", "happy")
        self.redraw()

    def redraw(self):
        """Draws the card again now (the slime's moves carry on from the next tick)."""
        if not (self.game and self.content):
            return
        self.rect = self.place(self.game)
        self._paint(time.monotonic())

    def _paint(self, now):
        ppu = self._ppu()
        key = (self.layout, self.theme, round(ppu, 4), self.width, self.tall, self.repot, repr(self.content))
        if self._fresh:                                         # (drawn in the background: take it)
            self._base, self._fresh = self._fresh, None
        if not self._base or self._base[0][:6] != key[:6]:
            # A new size, layout or theme (or being resized): draw it now, so it never shows stretched.
            self._base = (key,) + render_base(self.layout, self.theme, ppu, self.content, self.art, self.width, self.tall,
                                              self.repot)
        elif self._base[0] != key and not self._drawing:
            # Only the numbers changed: draw them in the background, the slime carries on meanwhile.
            self._drawing = True
            args = (self.layout, self.theme, ppu, dict(self.content), self.art, self.width, self.tall, self.repot)

            def work():
                try:
                    self._fresh = (key,) + render_base(*args)
                finally:
                    self._drawing = False
            threading.Thread(target=work, daemon=True).start()
        a = self.moves.frame(self.mood, now)
        a.update(edit=self.editing, opacity=self.cfg.get("hud_opacity", 1.0))
        self.window.paint(self.rect[0], self.rect[1], compose(self._base[1], self._base[2], self.art, a))

    def _tick(self):
        """The slime's animation: about 30 frames a second while it hops, 12 otherwise."""
        now = time.monotonic()
        busy = self.moves.busy(now)
        try:
            if self.window.shown and self.rect and self._drag is None:
                self._paint(now)
        except Exception:
            pass                    # (a frame missed is nothing; the next one tries again)
        self.root.after(33 if busy else 83, self._tick)

    # ---- edit mode: the mouse
    def _zone(self, e):
        """What the mouse is over: "corner", "right", "bottom" or "move"."""
        x, y, w, h = self.rect
        ppu = self._ppu()
        x1, y1 = x + w - MARGIN * ppu, y + h - MARGIN * ppu      # the card's own edges (inside its shadow)
        edge = max(self.EDGE, 14 * ppu)
        right, bottom = abs(e.x_root - x1) <= edge, abs(e.y_root - y1) <= edge
        return "corner" if right and bottom else "right" if right else "bottom" if bottom else "move"

    def _hover(self, e):
        if self.editing and self.rect:
            self.window.win.configure(cursor={"corner": "size_nw_se", "right": "sb_h_double_arrow",
                                              "bottom": "sb_v_double_arrow", "move": "fleur"}[self._zone(e)])

    def _press(self, e):
        if self.editing and self.rect:
            self._drag = (e.x_root, e.y_root, self._zone(e), self.scale, self.width, self.tall, self.rect)

    def _move(self, e):
        """Dragging in edit mode. The middle moves it. The right edge makes it wider and the bottom edge
        taller, the text staying the same size; the corner makes all of it bigger or smaller, keeping its shape."""
        if not self._drag:
            return
        sx, sy, zone, scale, width, tall, (x, y, w, h) = self._drag
        dx, dy = e.x_root - sx, e.y_root - sy
        ppu = self._ppu()
        if zone == "move":
            self.move_to((x + dx, y + dy))
        elif zone == "right":
            self.set_width(width + dx / ppu)
        elif zone == "bottom":
            self.set_tall(tall + dy / ppu)
        else:                       # the corner: by whichever way it was dragged further, as a share of the size
            grow = max((w + dx) / w, (h + dy) / h)
            self.set_scale(scale * max(0.2, grow))

    def _release(self, e):
        if self._drag:
            self._drag = None
            self.save()
            self.redraw()

    def _menu(self, e):
        if not self.editing:
            return
        m = tk.Menu(self.window.win, tearoff=0)
        lay, th, rp = tk.StringVar(m, self.layout), tk.StringVar(m, self.theme), tk.BooleanVar(m, self.repot)
        m._vars = (lay, th, rp)                                  # (kept alive while the menu is)
        for key, name in LAYOUTS.items():
            m.add_radiobutton(label=name, value=key, variable=lay, command=lambda k=key: self.set_style(layout=k))
        m.add_checkbutton(label="Show the repot row (Full)", variable=rp, command=lambda: self.set_repot(rp.get()))
        if self.on_clear_repot:
            m.add_command(label="Clear repot history...", command=self.on_clear_repot)
        m.add_separator()
        for key, name in THEMES.items():
            m.add_radiobutton(label=f"{name} theme", value=key, variable=th, command=lambda k=key: self.set_style(theme=k))
        m.add_separator()
        m.add_command(label="Bigger", command=lambda: self.resize_by(1.1))
        m.add_command(label="Smaller", command=lambda: self.resize_by(1 / 1.1))
        m.add_command(label="Usual size", command=lambda: (self.cfg.update(hud_scale=0.5, hud_width=W0, hud_tall=0),
                                                          self.save(), self.redraw()))
        m.add_separator()
        m.add_command(label="Done (lock it in place)", command=lambda: self.set_editing(False))
        m.tk_popup(e.x_root, e.y_root)
