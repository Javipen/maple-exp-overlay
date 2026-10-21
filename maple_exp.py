"""
Maple Classic Exp Overlay: EXP/hr, mesos/hr and time to level for MapleStory Classic World.

Finds the MapleStory window, locates the "EXP. 12,345 [12.34%]" text in its status bar at
any window size, reads the digits by matching them against examples of the game's font
(Tesseract OCR as a fallback), and calculates EXP per hour, %/hr, and time to next level.

While tracking it also reads the map name from the minimap, your mesos from the inventory
and your party size from the party list, and keeps a log of EXP/hr and mesos/hr per day,
map, level and party size.

It only looks at the screen: it never reads or changes the game's files or memory and
never sends input to the game. Your settings and log are kept in the MapleEXP folder
inside %APPDATA% on Windows, ~/Library/Application Support on macOS (named after its old
name, Maple EXP/hr, so updating keeps them). It runs on Windows and macOS: what differs
between them is in platforms/.
"""
import hashlib
import hmac
import json
import math
import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")        # (before numpy is imported)
import queue
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import tkinter as tk
import traceback
from collections import Counter, deque, namedtuple
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog, ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

import ocr
import report
from hud import LAYOUTS as HUD_LAYOUTS, THEMES as HUD_THEMES, HudCard
from platforms import create_platform, hotkey_text, parse_hotkey
from report import Health, Recent
from ui_gfx import INK, TEXT_DIM
from ui_kit import (ACCENT, ACCENT_SOFT, BAD, BG, DIM, FAINT, FG, GOOD, LINE, LIST_BG, LIST_BG2, LIST_PART,  # noqa: F401
                    ON_ACCENT, RAISED, RAISED_HI, SELECT, SURFACE, WARN, setup_ttk)
from ui_main import SHAPES, Button, PopupMenu, SimpleView, Slider, Theme, Toggle

# Everything that differs between Windows and macOS (finding the game window, screen scaling,
# hotkeys...) is behind this one object: see platforms/.
PLATFORM = create_platform()
PLATFORM.setup_process()
cv2.setNumThreads(1)


# Local only: this copy never goes online. The update check and run sharing are gone, and as a
# backstop any network connection to somewhere other than this PC is refused.
def _local_only():
    def is_local(address):
        host = address[0] if isinstance(address, tuple) else address
        return isinstance(host, str) and (host in ("localhost", "::1") or host.startswith("127."))

    def refuse(what):
        raise OSError(f"{what}: blocked, this overlay is local only")

    real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    socket.socket.connect = lambda self, a: real_connect(self, a) if is_local(a) else refuse(f"connecting to {a}")
    socket.socket.connect_ex = lambda self, a: real_connect_ex(self, a) if is_local(a) else refuse(f"connecting to {a}")
    socket.getaddrinfo = lambda host, *rest, **kw: (real_getaddrinfo(host, *rest, **kw) if host is None or is_local(host)
                                                    else refuse(f"looking up {host}"))


_local_only()

VERSION = "1.0.1"                  # the local-only edition (based on Maple Classic Exp Overlay 0.9.10-beta)
WINDOW_TITLE = "Maple Classic Exp Overlay"    # the version is shown in Settings
OLD_TITLES = ("Maple EXP/hr",)               # what versions before 0.9.3 were called

# Files that come with the program (inside the .exe when packaged), the folder it runs from,
# and the folder for your settings, log and learned digits (MAPLE_EXP_DATA can override it).
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = APP_DIR / "assets"
HOME_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MAPLE_EXP_DATA") or PLATFORM.data_root() / "MapleEXP")

CONFIG_PATH = DATA_DIR / "config.json"
ERROR_LOG_PATH = DATA_DIR / "errors.log"
TEMPLATE_PATH = ASSETS / "digits.npz"
ICON_PATH = ASSETS / "icon.ico"
POLL_SECONDS = 1.5          # how often to read the EXP bar
MESOS_UNSEEN = 15           # seconds without reading the mesos (the inventory closed) before the card guesses them
HUD_CALIBRATION = 300       # seconds of tracking before the tracker card shows rates and the time to level
LOGGING = False             # the EXP log (one line per map, level and party) isn't kept in this simplified copy
ROLLING_WINDOW = 300        # seconds used for the "last 5 min" rate
SEARCH_STRIP = 0.22         # bottom part of the game window searched for the EXP text
LOG_PATH = DATA_DIR / "exp_log.json"
LOG_TEXT_PATH = DATA_DIR / "exp_log.txt"
LOG_BLOCK = 300             # seconds of non-stop EXP gain behind each log update
MESOS_PATH = DATA_DIR / "mesos_digits.npz"             # learned as you play...
MESOS_START_PATH = ASSETS / "mesos_digits.npz"          # ...starting from these
MESOS_BOX_PATH = ASSETS / "mesos_box.npz"
MAPS_PATH = ASSETS / "maps.txt"            # the game's map names (see server/tools/make_maps.py)
MAP_HIDDEN_SECONDS = 3      # the minimap's name unseen this long: say so, and hold back saving to the log
MAP_BLIND_SECONDS = 45      # the minimap's name box unseen this long: the map may have changed unnoticed
SCAN_SECONDS = 4            # how often the whole window is checked for the inventory and party
OUTLINE_SECONDS = 8         # how often the (costly) search for a see-through mesos box may run
DEFAULTS = {"gap_seconds": 15, "transparency": 0, "topmost": True,
            "skip_version": "", "ui_scale": 100, "compact": False, "hotkeys_on": True,
            "details": False, "compact_shape": "square", "seen_version": "",
            # The EXP tracker card over the game (hud.py): its layout and theme, its size (pixels per unit
            # of its 1390-unit-wide drawing, for a 1080-tall game; it grows and shrinks with the game) and
            # how solid it is. hud_offset (set by moving it) is where it is, from the game's top-left.
            "hud": True, "hud_layout": "full", "hud_theme": "brown", "hud_scale": 0.5, "hud_width": 1390,
            "hud_tall": 0, "hud_repot": True, "hud_opacity": 1.0}
SHAPE_LABELS = {"square": "Square", "wide": "Wide", "bar": "Bar"}
SHAPE_BLURBS = {"square": "The narrow card: the big number, the level bar and Start/Pause.",
                "wide": "Longer sideways: the level bar on top and four numbers in a row.",
                "bar": "One thin strip with the main numbers and a slim level bar."}
# What's new, shown once after updating to a version (plain words, newest changes only). A version with no entry shows nothing.
WHATS_NEW = {"0.9.10-beta": (
    "Compact mode now comes in three shapes: Square, Wide and Bar. Pick one in Settings, under Compact shape.",
    "Map names are matched to the game's own list, so one map is one line in your log.",
    "If the minimap's name is covered, it tells you after a few seconds, keeps the log clean, and pauses if it stays hidden.",
    "Towns are no longer logged.",
    "The best-maps website moved to mcwo.app, with a new look and an Include new players box.")}
# Settings > Size, in %: only these, so the window can't get too small to read or use.
UI_SCALES = (60, 70, 80, 90, 100, 110, 125, 150, 175, 200)
MAX_TRANSPARENCY = 40       # %: more and the window can fade out of sight, with Settings in it

# EXP needed to finish each level in MapleStory Classic World (index 0 = level 1),
# from meowdb.com/msclassic/guides/exp-table-level-1-to-100.
EXP_TABLE = [
    15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716,
    2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680, 20216,
    24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700,
    108480, 122760, 138666, 155540, 174216, 194832, 216600, 240500, 266682, 294216,
    324240, 356916, 391160, 428280, 468450, 510420, 555680, 604416, 655200, 709716,
    748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536, 1147132, 1209994,
    1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750, 2062925,
    2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
    3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059,
    25069923, 52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548,
]

tesseract_ok = False        # set in main(); Tesseract is only a fallback
TESS = None                 # the ocr.Tesseract to read text with, once main() has found one


# --------------------------------------------------------------------------- files

_error_lock = threading.Lock()
_last_errors = {}


def log_error(what, exc=None):
    """Adds an error, with details, to errors.log in the data folder (the same one at most
    once a minute), so players can send it in when something goes wrong."""
    key = (what, repr(exc))
    with _error_lock:
        now = time.monotonic()
        if now - _last_errors.get(key, -1e9) < 60:
            return
        _last_errors[key] = now
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if ERROR_LOG_PATH.exists() and ERROR_LOG_PATH.stat().st_size > 1_000_000:
            os.replace(ERROR_LOG_PATH, ERROR_LOG_PATH.with_suffix(".old.log"))
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] v{VERSION}: {what}\n{details}\n")
    except Exception:
        pass


def load_config():
    """The saved settings, with anything missing or damaged replaced by the defaults."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {}
    except Exception as e:
        log_error("reading settings (using the defaults)", e)
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    for key, default in DEFAULTS.items():
        value = cfg.get(key)
        if type(value) is not type(default) or (key == "gap_seconds" and not 1 <= value <= 3600):
            cfg[key] = default
    cfg["transparency"] = min(MAX_TRANSPARENCY, max(0, cfg["transparency"]))
    if cfg["compact_shape"] not in SHAPES:
        cfg["compact_shape"] = "square"
    if cfg["ui_scale"] not in UI_SCALES:
        cfg["ui_scale"] = 100
    for key, low, high in (("hud_scale", 0.25, 1.5), ("hud_width", 950, 2800), ("hud_tall", 0, 320),
                           ("hud_opacity", 0.3, 1.0)):
        if not low <= cfg[key] <= high:
            cfg[key] = DEFAULTS[key]
    if cfg["hud_layout"] not in HUD_LAYOUTS:
        cfg["hud_layout"] = DEFAULTS["hud_layout"]
    if cfg["hud_theme"] not in HUD_THEMES:
        cfg["hud_theme"] = DEFAULTS["hud_theme"]
    keys = cfg.get("hotkeys") if isinstance(cfg.get("hotkeys"), dict) else {}
    cfg["hotkeys"] = {a: keys[a] if a in keys and (keys[a] == "" or parse_hotkey(keys[a])) else k
                      for a, k in DEFAULT_HOTKEYS.items()}            # "" is a hotkey turned off
    ints = lambda v, n: isinstance(v, list) and len(v) == n and all(type(x) is int for x in v)
    checks = {"pos": lambda v: ints(v, 2), "log_size": lambda v: ints(v, 2) and min(v) >= 100,
              "mesos_box": lambda v: ints(v, 6), "mode": lambda v: v in ("window", "region"),
              "region": lambda v: isinstance(v, dict) and all(type(v.get(k)) is int and (
                  k in ("left", "top") or v[k] > 0) for k in ("left", "top", "width", "height")),
              "log_columns": lambda v: isinstance(v, dict), "log_columns_pt": lambda v: isinstance(v, dict),
              "log_open": lambda v: type(v) is bool,
              "share_runs": lambda v: type(v) is bool,       # missing: not asked yet
              "hud_offset": lambda v: ints(v, 2),
              "repot": lambda v: isinstance(v, dict) and type(v.get("mesos")) is int}
    for key, ok in checks.items():
        if key in cfg and cfg[key] is not None and not ok(cfg[key]):
            del cfg[key]
    if cfg.get("mode") == "region" and not cfg.get("region"):
        cfg["mode"] = "window"
    return cfg


def save_config(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)                    # never leaves a half-written file
    except Exception as e:
        log_error("saving settings", e)


def move_old_data():
    """Earlier versions kept settings and the log next to the program: copy them over once."""
    for name in ("config.json", "exp_log.json", "exp_log.txt", "mesos_digits.npz"):
        old, new = HOME_DIR / name, DATA_DIR / name
        if old.exists() and not new.exists():
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, new)
            except Exception as e:
                log_error(f"copying {name} from the program's folder", e)


def find_tesseract():
    """The Tesseract that comes with the download, else an installed one. Never one in the
    folder the overlay happens to be started from (Windows' usual search looks there first,
    so a tesseract.exe someone left in, say, Downloads could be run)."""
    return next((str(p) for p in PLATFORM.tesseract_candidates(HOME_DIR, APP_DIR)
                 if p.is_file() and os.access(p, os.X_OK)), None)


# --------------------------------------------------------------------------- versions
#
# (The update check and run sharing were taken out: this copy never goes online.)

def version_key(version):
    """'v0.9.1-beta' -> something to compare versions by. A full release beats its betas."""
    main, _, pre = version.strip().lstrip("vV").partition("-")
    nums = [int(x) for x in re.findall(r"\d+", main)[:3]]
    return tuple(nums + [0] * (3 - len(nums))), 0 if pre else 1, pre


# --------------------------------------------------------------------------- hotkeys
#
# Shift+1 to 4 by default (changeable in Settings). How they're caught is up to the OS
# (PLATFORM.create_hotkeys); they're only on while MapleStory or the overlay is in front.

HOTKEY_ACTIONS = {"start": "Start / Pause", "reset": "Reset", "repot": "Repot bell", "edit": "Move tracker card"}
DEFAULT_HOTKEYS = {"start": "Shift+1", "reset": "Shift+2", "repot": "Shift+3", "edit": "Shift+5"}


# --------------------------------------------------------------------------- screenshots

def grab(sct, left, top, width, height):
    """Screenshot of a screen rectangle, clipped to the monitors: (bgr, left, top) or None."""
    v = sct.monitors[0]
    x0, y0 = max(left, v["left"]), max(top, v["top"])
    x1 = min(left + width, v["left"] + v["width"])
    y1 = min(top + height, v["top"] + v["height"])
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    shot = np.array(sct.grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}))
    return np.ascontiguousarray(shot[:, :, :3]), x0, y0


# --------------------------------------------------------------------------- reading the bar
#
# The status bar shows "EXP. 38,849 [61.02%]": white digits and green brackets. The game
# draws it in a small pixel font scaled up to the window size, so once a digit is rescaled
# to a standard height it can be recognised by comparing it pixel by pixel with saved
# examples of each digit (digits.npz).

Blob = namedtuple("Blob", "i x y w h")
# cells are digit positions (centre x, width, "digit" or "wide"); V is the brightness image
Layout = namedtuple("Layout", "gh cy exp pct box V")

CANON = 24                  # digits are compared at this height
PH, PW = 28, 19             # size of one digit patch at that height
SHIFT = 3                   # px of wiggle room when lining a digit up with an example
MIN_SCORE = 0.75            # correlation the best digit needs (true matches score ~0.9+)
MIN_LEAD = 0.03             # ...and how far ahead of the runner-up it must be


def _blobs(mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return labels, [Blob(i, *map(int, stats[i][:4])) for i in range(1, n)]


def _left_heavy(labels, b):
    """True for '[' (stroke on the left), False for ']'."""
    m = labels[b.y:b.y + b.h, b.x:b.x + b.w] == b.i
    half = max(1, b.w // 2)
    return m[:, :half].sum() > m[:, b.w - half:].sum()


def _cells(blobs, gh):
    """Digit positions. A blob as wide as several digits (touching digits) is split evenly."""
    pitch = 0.84 * gh
    out = []
    for b in blobs:
        if 0.95 * b.h < b.w < 1.25 * gh:
            out.append((b.x + b.w / 2, b.w, "wide"))          # the '%' sign
            continue
        n = max(1, int(round((b.w + 0.1 * gh) / pitch)))
        out.extend((b.x + (k + 0.5) * b.w / n, b.w / n, "digit") for k in range(n))
    return out


def find_exp(bgr):
    """Locate the EXP text in a screenshot. Returns a Layout or None."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    green = (sat >= 90) & (val >= 110) & (hue >= 26) & (hue <= 50)
    white = (sat < 70) & (val >= 140)
    glabels, gblobs = _blobs(green)
    _, wblobs = _blobs(white)
    brackets = sorted((b for b in gblobs if b.h >= 5 and b.w <= 0.6 * b.h), key=lambda b: b.x)
    V = val.astype(np.float32)
    found = []
    for o in brackets:
        bh, cy = o.h, o.y + o.h / 2
        on_line = lambda b: abs(b.y + b.h / 2 - cy) <= 0.3 * bh
        if o.w >= 6 and not _left_heavy(glabels, o):
            continue                                          # clearly a ']'
        right = [c for c in brackets if c.x > o.x + o.w and c.x - o.x <= 9 * bh
                 and on_line(c) and abs(c.h - bh) <= 0.25 * bh]
        if not right:
            continue
        c = right[0]
        if c.w >= 6 and _left_heavy(glabels, c):
            continue                                          # clearly another '['
        line = [b for b in wblobs if on_line(b) and b.h >= 0.6 * bh]
        inside = sorted((b for b in line if b.x >= o.x + o.w and b.x + b.w <= c.x), key=lambda b: b.x)
        if len(inside) < 2 or any(b.h > 1.25 * bh for b in inside):
            continue
        # The EXP number ends right before '['; walk left until a gap or the taller label.
        # Digit height comes from the % digits (same font): a single digit can be shorter
        # when the game scales its font by an odd amount (a '1' was 9 px next to 11 px digits).
        dh = float(np.median([b.h for b in inside]))
        run, edge = [], o.x
        for b in sorted((b for b in line if b.x + b.w <= o.x), key=lambda b: -b.x):
            if edge - (b.x + b.w) > 2.0 * bh or b.h > 1.25 * bh or b.h > 1.15 * dh:
                break
            run.append(b)
            edge = b.x
        if not run:
            continue                                          # HP/MP: label before '['
        run.reverse()
        heights = [b.h for b in run + inside if b.w <= 0.85 * b.h]
        gh = float(np.median(heights)) if heights else float(bh)
        found.append(Layout(gh, cy, _cells(run, gh), _cells(inside, gh),
                            (run[0].x, int(cy - bh), c.x + c.w, int(cy + bh)), V))
    if not found:
        return None
    # The status bar is the lowest line found; EXP is the right-most value on it.
    low = max(f.cy for f in found)
    return max((f for f in found if f.cy >= low - 0.5 * f.gh), key=lambda f: f.box[0])


def digit_patch(V, cx, cy, gh, shift):
    """Brightness patch around one digit, rescaled so the digit is CANON px tall."""
    k = gh / CANON
    x0 = cx - (PW / 2 + shift) * k
    y0 = cy - (PH / 2 + shift) * k
    M = np.float32([[1 / k, 0, -x0 / k], [0, 1 / k, -y0 / k]])
    return cv2.warpAffine(V, M, (PW + 2 * shift, PH + 2 * shift), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _unit_rows(a):
    a = a - a.mean(axis=1, keepdims=True)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-6)


class DigitTemplates:
    """Example patches of each digit; scores a patch against all of them at once."""

    def __init__(self, examples):
        labels, rows = [], []
        for digit, patches in sorted(examples.items()):
            for p in patches:
                labels.append(digit)
                rows.append(np.asarray(p, np.float32).reshape(-1))
        self.labels = np.array(labels)
        self.digits = sorted(set(labels))
        self.mat = _unit_rows(np.stack(rows))

    @classmethod
    def load(cls, path=TEMPLATE_PATH):
        try:
            data = np.load(path)
            if int(data["canon"]) != CANON:
                return None
            return cls({k[1:]: data[k] for k in data.files if k.startswith("d")})
        except Exception:
            return None

    def scores(self, patch):
        """Best correlation per digit, trying every small shift of the patch."""
        windows = np.lib.stride_tricks.sliding_window_view(patch, (PH, PW)).reshape(-1, PH * PW)
        s = (_unit_rows(windows) @ self.mat.T).max(axis=0)
        return {d: float(s[self.labels == d].max()) for d in self.digits}

    def match(self, lay, cell):
        """(digit, score, lead over the runner-up) for one digit position."""
        best = {}
        for f in (0.95, 1.0, 1.05):         # allow for small errors in the size estimate
            for d, s in self.scores(digit_patch(lay.V, cell[0], lay.cy, lay.gh * f, SHIFT)).items():
                best[d] = max(best.get(d, -1.0), s)
        ranked = sorted(best.values(), reverse=True)
        lead = ranked[0] - ranked[1] if len(ranked) > 1 else 1.0
        return max(best, key=best.get), ranked[0], lead


def _pct_cells(lay):
    """The digits inside the brackets: drops the '%' sign right before the ']'."""
    return [c for c in lay.pct if lay.box[2] - c[0] > 1.5 * lay.gh]


def _read_digits(tpl, lay, cells):
    s = ""
    for cell in cells:
        if cell[2] != "digit":
            return None
        digit, score, lead = tpl.match(lay, cell)
        if score < MIN_SCORE or lead < MIN_LEAD:
            return None
        s += digit
    return s


def read_layout(tpl, lay):
    """(exp, pct) read with the digit templates, or None if any digit is unclear."""
    exp_s = _read_digits(tpl, lay, lay.exp)
    pct_s = _read_digits(tpl, lay, _pct_cells(lay))
    if not exp_s or not pct_s or not 3 <= len(pct_s) <= 5:
        return None
    if (len(exp_s) > 1 and exp_s[0] == "0") or (len(pct_s) > 3 and pct_s[0] == "0"):
        return None                                # a leading 0: some digits were cut off
    return int(exp_s), int(pct_s) / 100          # the game always shows 2 decimals


def _ocr_cells(bgr, lay, cells):
    """Tesseract on the area covering cells, two ways. Returns digit strings."""
    if not cells:
        return []
    g = lay.gh
    x0 = int(min(c[0] - c[1] / 2 for c in cells) - 0.1 * g)
    x1 = int(max(c[0] + c[1] / 2 for c in cells) + 0.1 * g)
    crop = bgr[max(0, int(lay.cy - 0.75 * g)):int(lay.cy + 0.75 * g), max(0, x0):x1]
    if crop.size == 0:
        return []
    big = cv2.resize(crop, None, fx=40 / g, fy=40 / g, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    white = (((hsv[..., 1] < 70) & (hsv[..., 2] >= 140)) * 255).astype(np.uint8)
    white = cv2.dilate(white, np.ones((3, 3), np.uint8))
    _, bright = cv2.threshold(hsv[..., 2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    images = [cv2.copyMakeBorder(255 - mask, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
              for mask in (white, bright)]
    return [re.sub(r"\D", "", text) for text in TESS.read_lines(images, "0123456789,.")]


def tesseract_candidates(bgr, lay):
    """Fallback: (exp, pct) guesses from Tesseract. The tracker picks one that adds up."""
    if not tesseract_ok:
        return []
    exps = {int(s) for s in _ocr_cells(bgr, lay, lay.exp) if s}
    pcts = {int(s) / 100 for s in _ocr_cells(bgr, lay, _pct_cells(lay))
            if 3 <= len(s) <= 5}
    return [(e, p) for e in sorted(exps) for p in sorted(pcts)]


def build_templates(samples, scales=(1.0, 0.75, 0.55, 0.4, 0.3), path=TEMPLATE_PATH):
    """Make digits.npz from screenshots of the bar: [(bgr, "38,849", "61.02"), ...].

    Each screenshot is also shrunk to several sizes so small windows are covered too.
    """
    examples = {}
    for bgr, exp_s, pct_s in samples:
        for f in scales:
            img = bgr if f == 1.0 else cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            lay = find_exp(img)
            if not lay:
                continue
            exp_cells, pct_cells = lay.exp, _pct_cells(lay)
            exp_t, pct_t = re.sub(r"\D", "", exp_s), re.sub(r"\D", "", pct_s)
            if len(exp_cells) != len(exp_t) or len(pct_cells) != len(pct_t):
                continue
            for cell, ch in zip(exp_cells + pct_cells, exp_t + pct_t):
                examples.setdefault(ch, []).append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
    np.savez(path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in examples.items()})
    return {k: len(v) for k, v in sorted(examples.items())}


# --------------------------------------------------------------------------- map name and level
#
# The minimap's light-blue info box shows the region and, under it, the map name in white
# text. It's read with Tesseract a few different ways, and the most common answer wins.
# The level comes from the EXP table: each level needs a different amount of EXP.

def level_for_req(req):
    """The level whose EXP requirement matches req, from the Classic World EXP table."""
    if not req:
        return None
    for level, need in enumerate(EXP_TABLE, start=1):
        if abs(req - need) <= 0.005 * need:
            return level
    return None


def levels_for(exp, pct):
    """The levels whose EXP requirement fits exp at pct% (% is shown with 2 decimals)."""
    req, err = exp / (pct / 100), 0.002 + 0.01 / pct
    return [level for level, need in enumerate(EXP_TABLE, start=1)
            if abs(req - need) <= err * need and exp < need]


def fits_exp_table(exp, pct):
    """True if exp at pct% matches some level's EXP requirement. A misread almost never does
    (e.g. '1 [71.68%]' for '68,601 [71.68%]'), so a reading that doesn't isn't trusted."""
    return pct < 0.2 or bool(levels_for(exp, pct))      # under 0.2%: too little EXP to tell


# Job names as the status bar shows them (old-style, as in Classic World). A reading is
# matched to the closest of these; a clear reading that matches none is kept as read.
JOBS = ["Beginner",
        "Swordman", "Fighter", "Crusader", "Hero", "Page", "White Knight", "Paladin",
        "Spearman", "Dragon Knight", "Dark Knight",
        "Magician", "Wizard (Fire,Poison)", "Mage (Fire,Poison)", "Arch Mage (Fire,Poison)",
        "Wizard (Ice,Lightning)", "Mage (Ice,Lightning)", "Arch Mage (Ice,Lightning)",
        "Cleric", "Priest", "Bishop",
        "Archer", "Hunter", "Ranger", "Bowmaster", "Crossbowman", "Sniper", "Marksman",
        "Rogue", "Assassin", "Hermit", "Night Lord", "Bandit", "Chief Bandit", "Shadower",
        "Pirate", "Brawler", "Marauder", "Buccaneer", "Gunslinger", "Outlaw", "Corsair",
        "Warrior", "Bowman", "Thief"]


def _clean_job(text):
    t = re.sub(r"[^A-Za-z(),/ ]", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 3 else ""


def read_job(bgr, x0, cy, g, debug=None):
    """The job name in the status bar (e.g. 'Bandit'), from a shot where the EXP number's
    first digit is at x0 and its middle at cy, or None if it can't be read clearly.
    debug: a dict to fill with what it looked at ("image", "boxes") and, if it gave up, "why".

    The job sits on the line of the HP bar's label ("HP"), right before it; the character
    name is on the line under it and is never read."""
    ry, rx = max(0, int(cy - 2.5 * g)), max(0, int(x0 - 64 * g))
    reg = bgr[ry:int(cy + 3 * g), rx:max(0, int(x0 - 20 * g))]
    if debug is not None:
        debug.clear()
        debug.update(image=reg, boxes=[], why="")

    def give_up(why):
        if debug is not None:
            debug["why"] = why
        return None

    if reg.size == 0:
        return give_up("the status bar is off screen here")
    white = _whiteness(reg)
    _, blobs = _blobs(white > 0.3)                    # thin letters: a gentle white check
    # The HP bar: 13-16 EXP digits wide, its left end 31-35 of them before the EXP number
    # (the region starts 64 before it). Not to be mixed up with the panels' light borders.
    bars = [b for b in blobs if 9 * g <= b.w <= 20 * g and 1.4 * g <= b.h <= 2.8 * g
            and 24 * g <= b.x <= 39 * g]
    if not bars:
        return give_up("can't find the HP bar next to the class name")
    hp = min(bars, key=lambda b: b.x)                 # the HP bar (the MP bar is right of it)
    if debug is not None:
        debug["boxes"].append((hp.x, hp.y, hp.x + hp.w, hp.y + hp.h, "mesos"))
    label = [b for b in blobs if hp.x - 0.5 * g <= b.x <= hp.x + 3 * g
             and b.y + b.h <= hp.y + 0.2 * g and 0.6 * g <= b.h <= 1.6 * g]
    if not label:
        return give_up("can't find the HP label")
    top, bottom = min(b.y for b in label), max(b.y + b.h for b in label)
    row = sorted((b for b in blobs if b.x + b.w <= hp.x - g and b.y >= top - 0.3 * g
                  and b.y + b.h <= bottom + 0.4 * g and b.h >= 0.3 * g), key=lambda b: b.x)
    if len(row) < 2:
        return give_up("no class name found left of the HP bar")
    words = [[row[0]]]
    for b in row[1:]:
        if b.x - max(c.x + c.w for c in words[-1]) > 2.0 * g:
            words.append([b])
        else:
            words[-1].append(b)
    word = words[-1]                                  # the letters right before "HP"
    x_a, x_b = max(0, min(b.x for b in word) - int(0.4 * g)), max(b.x + b.w for b in word) + int(0.4 * g)
    if debug is not None:
        debug["boxes"].append((x_a, top, x_b, bottom, "exp"))
    crop = white[max(0, top - int(0.4 * g)):bottom + int(0.4 * g), x_a:x_b]
    glyph_h = max(b.h for b in word)
    if glyph_h > 10:                                  # big screens: read at small-window size,
        k = 10.0 / glyph_h                            # where Tesseract reads this font best
        crop, glyph_h = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA), 10.0
    votes = _ocr_votes(crop.astype(np.float32), glyph_h, None, _clean_job)
    text, n = votes.most_common(1)[0] if votes else ("", 0)
    if n < 2:
        return give_up("the tries didn't agree on what it says" + (f" (best guess: {text})" if text else ""))
    score, best = max((SequenceMatcher(None, text.lower(), j.lower()).ratio(), j) for j in JOBS)
    if score >= 0.72:
        return best
    if n >= 3:
        return text.title()
    return give_up(f"it reads \"{text}\", which isn't a class it knows")


def _whiteness(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    return np.clip((hsv[..., 2] - 150) / 90, 0, 1) * np.clip((80 - hsv[..., 1]) / 50, 0, 1)


def _ocr_votes(whiteness, glyph_h, chars, clean):
    """Tesseract on white text at a few sizes, with and without smoothing (all in one run).
    Counts answers. chars: the only characters it may answer with, or None."""
    images = []
    for target in (36, 48, 60):
        s = target / glyph_h
        big = cv2.resize(whiteness, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for img in ((big > 0.5) * 255.0, np.clip(big, 0, 1) * 255):
            images.append(cv2.copyMakeBorder((255 - img).astype(np.uint8), 24, 24, 24, 24,
                                             cv2.BORDER_CONSTANT, value=255))
    votes = Counter()
    for text in TESS.read_lines(images, chars):
        text = clean(text)
        if text:
            votes[text] += 1
    return votes


def find_map_box(bgr, gh):
    """(x, y, w, h) of the minimap's info box in a shot of the window's top-left, or None."""
    hue, sat, val = [c.astype(np.int16) for c in cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))]
    box = (abs(hue - 100) <= 8) & (abs(sat - 64) <= 30) & (abs(val - 204) <= 30)
    n, _, stats, _ = cv2.connectedComponentsWithStats(box.astype(np.uint8), connectivity=8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = map(int, stats[i][:4])
    h = min(h, int(round(7.3 * gh)))            # just the info box, not the whole minimap
    return (x, y, w, h) if w >= 10 * gh and h >= 4 * gh else None


_ROMAN = re.compile(r"^(?:X{0,2})(?:IX|IV|V?I{0,3})$")


def _fix_roman(name):
    """A last word that is a Roman numeral misread ("Vil" -> VII, "lV" -> IV, "il" -> II, "V1" -> VI). Plain numbers
    ("Free Market 11") and ordinary words are left alone. The site does the same (server/src/mapnames.js)."""
    def fix(m):
        token = m.group(2)
        if not re.search(r"[IVXivxl|!]", token):
            return m.group(0)                          # only digits: a number
        numeral = re.sub(r"[li1|!]", "I", token, flags=re.I).upper()
        return m.group(1) + numeral if numeral and _ROMAN.match(numeral) else m.group(0)
    return re.sub(r"(\s)([IVXivxl1|!]{1,6})$", fix, name)


def _clean_map_name(text):
    t = text.replace("|", "I")                         # a lone "I" (as in "... I") reads as "|"
    t = re.sub(r"(?<=\s)[!\[\]](?=\s*$)", "I", t)
    t = re.sub(r"[^A-Za-z0-9 '<>:.&()\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    t = _fix_roman(t)                                  # Roman numerals read as l's, 1's, i's ("Vil" is VII)
    t = re.sub(r"(^|\s)[IiLl]st(?=\s|$)", r"\g<1>1st", t)     # "ist Accompaniment" is "1st Accompaniment"
    t = re.sub(r"(^|\s)[Zz]nd(?=\s|$)", r"\g<1>2nd", t)
    t = re.sub(r"(^|\s)[SsBb5]rd(?=\s|$)", r"\g<1>3rd", t)
    return t if len(t) >= 3 else ""


def _map_key(name):
    """Only letters and digits, lower case: names that differ in punctuation are the same map."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = row
    return prev[-1]


class MapList:
    """The game's own map names (assets/maps.txt, from meowdb.com), to spell a map the way the game
    does and to know the towns, where nobody farms. It helps; it isn't a gate: a name that isn't
    in it (an event map, a new map) is used as it was read. The site applies the same rules."""

    def __init__(self, path=MAPS_PATH):
        self.entries, self.by_key = [], {}
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            name, _, town = line.partition("\t")
            style = _clean_map_name(name)               # as the overlay writes names (no commas)
            if style:
                entry = (style, _map_key(name), town.strip() == "1", _numbers(style))
                self.entries.append(entry)
                self.by_key[entry[1]] = entry

    def find(self, read):
        """The list's entry (name, key, is_town, numbers) for a name as read, or None."""
        key = _map_key(read)
        if key in self.by_key:
            return self.by_key[key]
        nums, best, second = _numbers(read), None, float("inf")
        for e in self.entries:
            if e[3] != nums or abs(len(e[1]) - len(key)) > 2:
                continue
            d = _edit_distance(key, e[1])
            if best is None or d < best[0]:
                second = best[0] if best else second
                best = (d, e)
            elif d < second:
                second = d
        # one clear match: a letter misread (two in a long name). Two names about as close, or too far, are left alone.
        allowed = 2 if len(key) >= 24 else 1
        return best[1] if best and best[0] <= allowed and second - best[0] >= 1 else None


_MAP_LIST = None


def map_list():
    global _MAP_LIST
    if _MAP_LIST is None:
        _MAP_LIST = MapList()
    return _MAP_LIST


def snap_map_name(name):
    """The game's spelling of a map name as read (or the name as it is, if it isn't on the list)."""
    e = map_list().find(name)
    return e[0] if e else name


def is_town(name):
    """Is this one of the game's towns? (A name not on the list isn't.)"""
    e = map_list().by_key.get(_map_key(name or ""))
    return bool(e and e[2])


def read_map_name(bgr, gh):
    """The map name (the lower line of white text) from a shot of the minimap's info box."""
    white = _whiteness(bgr[:, int(0.85 * bgr.shape[0]):])      # skip the map's icon
    _, blobs = _blobs(white > 0.5)
    glyphs = sorted((b for b in blobs if 0.3 * gh <= b.h <= 1.6 * gh and b.w <= 2.5 * gh),
                    key=lambda b: b.y + b.h / 2)
    lines = []
    for b in glyphs:
        last = lines[-1][-1] if lines else None
        if last and abs(b.y + b.h / 2 - (last.y + last.h / 2)) <= 0.5 * gh:
            lines[-1].append(b)
        else:
            lines.append([b])
    lines = [ln for ln in lines if len(ln) >= 3]
    if not lines:
        return None
    ln = lines[-1]
    x0, x1 = max(0, min(b.x for b in ln) - 2), max(b.x + b.w for b in ln) + 2
    y0, y1 = max(0, min(b.y for b in ln) - 2), max(b.y + b.h for b in ln) + 2
    votes = _ocr_votes(white[y0:y1, x0:x1], max(b.h for b in ln), None, _clean_map_name)
    name, n = votes.most_common(1)[0] if votes else (None, 0)
    return snap_map_name(name) if n >= 2 else None


# --------------------------------------------------------------------------- mesos and party
#
# The inventory's mesos box (white, gold coin on its left) is found while it's solid, then
# read where it is, even while the window is see-through during fights: the digits stay
# about as much darker than the box as when it's solid. The digits are a different font
# from the EXP bar's, so examples of them are learned from your own screen
# (mesos_digits.npz), each checked by Tesseract first. Until all ten are learned the EXP
# font's digits are used, which match these closely enough.
# Party: the small party list shows one HP bar per member, in an evenly spaced column.

def find_mesos_fields(bgr, gh=None, hsv=None):
    """Every solid white mesos box (gold coin on its left) as (x, y, w, h), biggest first.

    With gh (the EXP digit height), a box must also be the mesos box's size at that scale:
    about 19.1 x 1.82 EXP digits. hsv: the same picture as HSV, if it's at hand already."""
    hue, sat, val = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)
    n, _, stats, _ = cv2.connectedComponentsWithStats(((val > 225) & (sat < 30)).astype(np.uint8),
                                                      connectivity=8)
    coin = (hue >= 12) & (hue <= 35) & (sat >= 100) & (val >= 140)
    found = []
    for i in range(1, n):
        x, y, w, h, area = map(int, stats[i])
        if h < 8 or not 7 * h <= w <= 16 * h or area < 0.6 * w * h:
            continue
        if gh and not (16.2 * gh <= w <= 22 * gh and 1.45 * gh <= h <= 2.2 * gh):
            continue
        left = coin[y:y + h, max(0, x - int(1.6 * h)):x]
        if left.size == 0 or left.mean() < 0.15:
            continue
        found.append((area, (x, y, w, h)))
    return [box for _, box in sorted(found, reverse=True)]


def find_mesos_field(bgr, gh=None):
    """The biggest solid mesos box (see find_mesos_fields), or None."""
    fields = find_mesos_fields(bgr, gh)
    return fields[0] if fields else None


def mesos_layout(bgr, field):
    """Digit cells of the number in the mesos box, or None if they can't be picked out cleanly.

    Works on how much darker each pixel is than the box right around it, so a see-through
    box over a changing background still works. Anything doubtful returns None: a wrong
    number is worse than waiting for a clean look.
    """
    x, y, w, h = field
    inner = bgr[y + 2:y + h - 2, x + 2:x + w - 2]
    if inner.size == 0:
        return None
    V = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    dh0 = 0.61 * h                                   # digits are ~61% as tall as the box
    k = max(3, int(round(0.35 * dh0)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    peak = float(np.percentile(strokes, 99.7))
    if peak < 40:
        return None
    band = np.zeros(strokes.shape, bool)             # the row the digits sit on
    mid = inner.shape[0] / 2
    b0, b1 = max(0, int(mid - 0.65 * dh0)), int(mid + 0.65 * dh0)
    band[b0:b1] = strokes[b0:b1] > 0.4 * peak
    c = max(1, int(round(0.06 * dh0)))
    band = cv2.morphologyEx(band.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((c, c), np.uint8)) > 0
    _, blobs = _blobs(band)
    digits = sorted((b for b in blobs if 0.6 * dh0 <= b.h <= 1.25 * dh0), key=lambda b: -b.x)
    if not digits or inner.shape[1] - (digits[0].x + digits[0].w) > 0.8 * dh0:
        return None                                  # the number is right-aligned
    run, edge = [], None
    for b in digits:
        if edge is not None and edge - (b.x + b.w) > 0.9 * dh0:
            break
        run.append(b)
        edge = b.x if edge is None else min(edge, b.x)
    run.reverse()
    dh = float(np.median([b.h for b in run]))
    pitch = 0.74 * dh
    cells = []
    for b in run:
        n = max(1, int(round((b.w + 0.12 * dh) / pitch)))
        cells.extend((b.x + (j + 0.5) * b.w / n, b.w / n, "digit") for j in range(n))
    # Nothing digit-like may be left over to the left of the number (a dropped digit).
    left = band[:, :max(0, int(cells[0][0] - cells[0][1] / 2 - 0.25 * dh))]
    if left.sum() > 0.25 * band[:, int(cells[-1][0] - cells[-1][1] / 2):].sum():
        return None
    # A comma after every 3 digits from the right makes that gap wider; anything else is off.
    centers = [c[0] for c in cells][::-1]
    for i in range(len(centers) - 1):
        want = 1.11 * dh if (i + 1) % 3 == 0 else 0.74 * dh
        if abs(centers[i] - centers[i + 1] - want) > 0.2 * dh:
            return None
    cy = float(np.median([b.y + b.h / 2 for b in run]))
    return Layout(dh, cy, cells, [], (0, 0, 0, 0), strokes)


# The game right-aligns the number: digit centres sit at these distances from the box's right
# edge, in box heights: first digit, each further digit, and extra room for every comma.
MESOS_FIRST, MESOS_PITCH, MESOS_COMMA = 0.36, 0.447, 0.30


def _mesos_spot(field, i):
    """x of the i-th digit from the right in the mesos box (i = -1: just right of them)."""
    x, y, w, h = field
    return x + w - (MESOS_FIRST + i * MESOS_PITCH + (max(i, 0) // 3) * MESOS_COMMA) * h


def _mesos_strokes(bgr, field):
    """A Layout for matching digits in the mesos box: how much darker each pixel is than
    the box around it (works solid or see-through)."""
    x, y, w, h = field
    V = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
    k = max(3, int(round(0.35 * 0.615 * h)) | 1)
    strokes = cv2.morphologyEx(V, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) - V
    return Layout(0.615 * h, y + 0.525 * h, [], [], (0, 0, 0, 0), strokes)


def mesos_box_lines_up(bgr, field, templates):
    """For a box found by its outline: does its right edge line up with the number? The
    first digit must match very clearly (in the wrong place the box's border can pass for
    a '1'), and just right of it there must be no digit (else the box is a digit too far
    left and the number would be read short)."""
    lay, h = _mesos_strokes(bgr, field), field[3]
    _, first, _ = templates.match(lay, (_mesos_spot(field, 0), 0.4 * h, "digit"))
    _, after, _ = templates.match(lay, (_mesos_spot(field, -1), 0.4 * h, "digit"))
    return first >= 0.85 and after < 0.84


def read_mesos_positions(bgr, field, templates, digit_count=None):
    """Reads the mesos box digit by digit at the spots the game draws them.

    Nothing has to be separated from the background first, so it also works when the
    inventory is see-through over a busy background. Every spot must clearly be a digit
    or clearly not one, and once the number ends none of the next few spots to its left
    may be a clear digit, so a digit lost in a busy background can't silently shorten the
    number from the middle. If digit_count is given, the number must have exactly that many digits (which
    also catches a lost first digit). Returns (number, Layout, weakest score, weakest lead),
    or None.
    """
    x, y, w, h = field
    lay = _mesos_strokes(bgr, field)
    digits, cells, scores, leads, ended = "", [], [], [], 0
    for i in range(16):
        cx = _mesos_spot(field, i)
        if cx - 0.3 * h < x:
            break
        digit, score, lead = templates.match(lay, (cx, 0.4 * h, "digit"))
        empty = bool(digits) and score < 0.65
        if ended:
            # A clear digit after the end means one was missed in between. (Only a clear
            # one: a see-through box's background often looks a little digit-like.)
            if score >= 0.75 and lead >= 0.05:
                return None
            ended += 1
            if ended > 3:
                break
        elif score >= 0.75 and lead >= 0.05:          # clearly a digit
            digits, cells = digit + digits, [(cx, 0.4 * h, "digit")] + cells
            scores.append(score)
            leads.append(lead)
        elif empty:
            ended = 1                                 # the number ends here
        else:
            return None                               # unclear: don't guess
    if not digits or (len(digits) > 1 and digits[0] == "0"):
        return None
    if scores[-1] < 0.85:
        return None          # the first digit must be very clear: a see-through box's background
                             # can pass for an extra '1' in front (real first digits score ~0.95)
    if digit_count and len(digits) != digit_count:
        return None
    return int(digits), lay._replace(exp=cells), min(scores), min(leads)


def read_mesos_digits(lay, templates):
    """The number in the mesos box, or None if any digit is unclear."""
    s = ""
    for cell in lay.exp:
        digit, score, lead = templates.match(lay, cell)
        if score < 0.70 or lead < 0.04:
            return None
        s += digit
    return int(s)


def tesseract_mesos(lay):
    """(number, how many of 6 Tesseract tries agreed) for the mesos box, or (None, 0)."""
    if not tesseract_ok:
        return None, 0
    first, last = lay.exp[0], lay.exp[-1]
    x0, x1 = max(0, int(first[0] - first[1] / 2 - 0.5 * lay.gh)), int(last[0] + last[1] / 2 + 0.5 * lay.gh)
    y0, y1 = max(0, int(lay.cy - 0.8 * lay.gh)), int(lay.cy + 0.8 * lay.gh)
    img = np.clip(lay.V[y0:y1, x0:x1] / max(1.0, float(np.percentile(lay.V, 99.7))), 0, 1)

    def clean(text):
        return re.sub(r"\D", "", text) if re.fullmatch(r"[\d,.\s]+", text.strip() or "x") else ""

    votes = _ocr_votes(img.astype(np.float32), lay.gh, "0123456789,", clean)
    digits, n = votes.most_common(1)[0] if votes else ("", 0)
    return (int(digits), n) if n >= 4 else (None, n)


class MesosDigits:
    """Examples of the mesos box's digits, learned from your own screen as you play."""

    PER_DIGIT = 12

    def __init__(self, path=MESOS_PATH, start=MESOS_START_PATH):
        self.path = path
        self.examples = {}
        self._templates = None
        for source in (path, start):                 # what you've learned, else what comes with it
            try:
                data = np.load(source)
                if int(data["canon"]) == CANON:
                    self.examples = {k[1:]: list(data[k]) for k in data.files if k.startswith("d")}
                    break
            except Exception:
                continue

    @property
    def complete(self):
        return all(str(d) in self.examples for d in range(10))

    def templates(self):
        if self._templates is None and self.examples:
            self._templates = DigitTemplates({d: np.stack(v) for d, v in self.examples.items()})
        return self._templates

    def learn(self, lay, value):
        digits = str(value)
        if len(lay.exp) != len(digits):
            return
        added = False
        for cell, ch in zip(lay.exp, digits):
            examples = self.examples.setdefault(ch, [])
            if len(examples) < self.PER_DIGIT:
                examples.append(digit_patch(lay.V, cell[0], lay.cy, lay.gh, 0))
                added = True
        if added:
            self._templates = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                np.savez(self.path, canon=CANON, **{f"d{k}": np.stack(v) for k, v in self.examples.items()})
            except Exception as e:
                log_error("saving the learned mesos digits", e)


def _edges(bgr, hsv=None):
    v = (cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2].astype(np.float32)
    return cv2.magnitude(cv2.Sobel(v, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(v, cv2.CV_32F, 0, 1, ksize=3))


class MesosBoxFinder:
    """Finds the mesos box even while the inventory is see-through.

    It looks for the outline of the whole row (gold coin, box, MESOS label) at the size the
    UI is drawn at, which stays visible when the window is see-through. The outline is kept
    in mesos_box.npz. Work is done at a small scale (EXP digits 12 px tall) to be quick.
    """

    GH = 12.0

    def __init__(self, path=MESOS_BOX_PATH):
        self.edges = None
        try:
            data = np.load(path)
            self.edges, self.mask = data["edges"], data["mask"]
            self.ox, self.oy, self.bw, self.bh = map(float, data["box"])
        except Exception:
            pass

    @classmethod
    def build(cls, bgr, field, gh, path=MESOS_BOX_PATH):
        """Make mesos_box.npz from a screenshot showing the solid box at `field`."""
        x, y, w, h = field
        x0, x1 = int(x - 1.45 * h), int(x + w + 2.7 * h)
        y0, y1 = int(y - 0.35 * h), int(y + 1.35 * h)
        s = cls.GH / gh
        edges = cv2.resize(_edges(bgr[y0:y1, x0:x1]), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        mask = np.ones(edges.shape, np.float32)       # leave out the inside of the box
        mask[int((y - y0 + 0.12 * h) * s):int((y - y0 + 0.88 * h) * s),
             int((x - x0 + 0.12 * h) * s):int((x - x0 + w - 0.12 * h) * s)] = 0
        np.savez(path, edges=edges, mask=mask, box=np.float32([(x - x0) * s, (y - y0) * s, w * s, h * s]))

    def find(self, bgr, gh, hsv=None):
        """(x, y, w, h) of the best-matching mesos box in a screenshot, or None.

        Compared with the EXP digits the box is drawn bigger in small game windows (in real
        screenshots: x1.12 at EXP digits 7 px tall, x1.07 at 11 px, x1.0 at 28 px), so sizes
        around that are tried and the clearest wins. Its left and right edges are then
        lined up with the box's border lines, because the digits are read at fixed
        distances from the right edge."""
        if self.edges is None:
            return None
        edges0 = _edges(bgr, hsv)
        expect = 0.961 + 1.092 / gh
        found = None
        for size in (expect, expect * 1.025, expect / 1.025):
            s = self.GH / (gh * size)
            edges = cv2.resize(edges0, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if edges.shape[0] < self.edges.shape[0] or edges.shape[1] < self.edges.shape[1]:
                continue
            r = np.nan_to_num(cv2.matchTemplate(edges, self.edges, cv2.TM_CCORR_NORMED, mask=self.mask),
                              nan=0.0, posinf=0.0, neginf=0.0)
            r[np.abs(r) > 1.0] = 0.0                   # flat areas (e.g. all black) give junk values
            _, best, _, (px, py) = cv2.minMaxLoc(r)
            r[max(0, py - 3):py + 4, max(0, px - 12):px + 13] = 0
            _, second, _, _ = cv2.minMaxLoc(r)
            enough = 0.72 if gh >= 10 else 0.64       # tiny windows match less sharply
            if best >= enough and best - second >= 0.05 and (found is None or best > found[0]):
                found = (best, (int((px + self.ox) / s), int((py + self.oy) / s),
                                int(self.bw / s), int(self.bh / s)))
        if not found:
            return None                                # not clearly there
        x, y, w, h = found[1]
        # Line up the right edge (the digits are read from there) with the inside of the
        # box's border: leaving the box it gets darker, solid or see-through.
        gx = cv2.Sobel((cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2].astype(np.float32),
                       cv2.CV_32F, 1, 0, ksize=3)
        rows = gx[max(0, y + h // 4):y + 3 * h // 4 + 1].sum(axis=0)
        reach = max(2, int(0.25 * h))
        lo, hi = max(0, x + w - reach), min(len(rows), x + w + reach + 1)
        right = lo + int(np.argmin(rows[lo:hi])) if hi - lo >= 3 else x + w
        return right - w, y, w, h


def _agrees(value, last):
    """Two mesos readings that could both be right: same number of digits and close together."""
    return last is not None and len(str(value)) == len(str(last)) and abs(value - last) <= max(1000, 0.02 * last)


def find_party_bars(bgr, gh, hsv=None):
    """HP bars of the party list's members: the longest evenly spaced column of identical
    HP-bar outlines. A single bar (or none) means you're on your own."""
    val = (cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv)[..., 2]
    n, _, stats, _ = cv2.connectedComponentsWithStats((val < 70).astype(np.uint8), connectivity=8)
    bars = sorted((tuple(map(int, stats[i][:4])) for i in range(1, n)
                   if 7.5 * gh <= stats[i][2] <= 10.5 * gh and 1.3 * gh <= stats[i][3] <= 1.85 * gh),
                  key=lambda b: b[1])
    best = []
    for i, first in enumerate(bars):
        same = [b for b in bars[i:] if abs(b[0] - first[0]) <= 0.5 * gh
                and abs(b[2] - first[2]) <= 0.5 * gh and abs(b[3] - first[3]) <= 0.3 * gh]
        column, pitch = [first], None
        for b in same[1:]:
            step = b[1] - column[-1][1]
            if pitch is None and 1.8 * gh <= step <= 3.0 * gh:
                pitch = step
            if pitch is None or abs(step - pitch) > 0.3 * gh:
                break
            column.append(b)
        if len(column) > len(best):
            best = column
    return best if len(best) >= 2 else best[:1]


# --------------------------------------------------------------------------- finding it on screen

Reading = namedtuple("Reading", "where candidates confident text image boxes map note "
                                 "mesos mesos_image mesos_boxes party mesos_at",
                     defaults=((), False, "", None, (), None, None, None, None, (), None, None))


def _view(img, lay):
    """Crop around the EXP text and the digit boxes in it, for View capture."""
    g = lay.gh
    x0, x1 = max(0, int(lay.box[0] - 2 * g)), min(img.shape[1], int(lay.box[2] + g))
    y0, y1 = max(0, int(lay.cy - 1.2 * g)), min(img.shape[0], int(lay.cy + 1.2 * g))
    boxes = [(c[0] - c[1] / 2 - x0, lay.cy - g / 2 - y0, c[0] + c[1] / 2 - x0, lay.cy + g / 2 - y0, kind)
             for kind, cells in (("exp", lay.exp), ("pct", _pct_cells(lay))) for c in cells]
    return img[y0:y1, x0:x1].copy(), boxes


def _problem(where, text):
    """A status message about reading the EXP bar ("where" only matters for a selected area)."""
    return f"Selected area: {text}" if where == "Selected area" else text


class ScreenReader:
    """Finds the EXP text on screen and reads it, following the game window around.

    It also watches the minimap so the map name is read again whenever it changes, reads
    the inventory's mesos and, every few seconds, counts the members in the party list.
    """

    def __init__(self, templates, mode="window", region=None):
        self.templates = templates
        self.mode, self.region = mode, region
        self.hwnd = None
        self.size = None
        self.box = None             # where the EXP text is, relative to the game window
        self.misses = 0
        self.last_full_search = 0.0
        self.gh = None              # EXP digit height: how big the game's UI is drawn
        self.map_box = None         # the minimap's info box, relative to the game window
        self.map_print = None       # rough picture of its text, to notice map changes
        self.map_unread = False
        self.next_map_search = 0.0
        self.next_map_read = 0.0
        self.mesos_box = None       # the inventory's mesos box, relative to the game window
        self.saved_mesos = None     # (window w, h, x, y, w, h) remembered from last time
        self.box_finder = MesosBoxFinder()
        self.mesos_digits = MesosDigits()
        self.mesos_checked = None   # last mesos number Tesseract confirmed
        self.mesos_count = None     # how many digits the mesos number has
        self.mesos_pending = (None, 0)
        self.mesos_fails = 0
        self.next_mesos_check = 0.0
        self.mesos_covered = False  # this app's window is in the way of the mesos
        self.two_boxes = False      # a storage / trade window shows a second mesos box
        self.next_outline = 0.0     # the earliest time to search for a see-through mesos box again
        self.party_area = None      # where the party list was last seen, on screen
        self.next_scan = 0.0
        self.own_rect = None        # this app's window, which screenshots can't see through
        self.hud_rects = ()         # the EXP / mesos panels over the game: blacked out like it
        self.look_again_requested = False
        self.exp_anchor = None      # where the EXP number starts, relative to the game window
        self.job = None             # the class shown in the status bar
        self.job_look = None        # what the last try at reading it saw: {"image", "boxes", "why"}
        self.map_look = None        # the same for the map name: {"image", "text"}
        self.map_seen_at = None     # when the minimap's name box was last seen (so a map change would have shown)
        self.last_rect = None       # where the game window was at the last look (x, y, w, h)
        self.next_job_read = 0.0

    def use_window(self):
        self.mode, self.hwnd, self.box = "window", None, None

    def use_region(self, region):
        self.mode, self.region = "region", region

    def request_map(self):
        """Read the map name on the next look, even if it doesn't seem to have changed."""
        self.next_map_search, self.next_map_read, self.map_unread = 0.0, 0.0, True

    def look_again(self):
        """Forget where everything was (EXP bar, minimap, inventory, party list) and look
        for it all again. Done by the reading thread itself, at its next look."""
        self.look_again_requested = True

    def _forget_places(self):
        self.box, self.misses = None, 0
        self.map_box, self.map_print = None, None
        self.mesos_box = self.saved_mesos = None
        self.mesos_fails, self.mesos_count, self.mesos_pending = 0, None, (None, 0)
        self.mesos_covered, self.party_area, self.next_scan = False, None, 0.0
        self.two_boxes, self.next_outline = False, 0.0
        self.exp_anchor, self.next_job_read = None, 0.0
        self.request_map()

    def read(self, sct):
        if self.look_again_requested:
            self.look_again_requested = False
            self._forget_places()
        rect = self._game_rect()
        self.last_rect = rect
        if self.mode == "region":
            reading = self._read_region(sct, rect)
        elif not self.hwnd and PLATFORM.missing_permission():
            return Reading(PLATFORM.missing_permission())
        elif not self.hwnd:
            return Reading("MapleStory window not found. Is the game running?")
        elif not rect:
            return Reading("MapleStory is minimized. Bring the game back up.")
        else:
            reading = self._read_window(sct, rect)
        if rect and self.gh:
            name, note = self._read_info(sct, rect)
            reading = self._read_extras(sct, rect, reading._replace(map=name, note=note))
            if self.exp_anchor and self.mode == "window":
                self._read_job(sct, rect)
        return reading

    def map_blind_seconds(self):
        """How long the minimap's name box hasn't been visible (0 if it never was: no map to lose)."""
        return 0.0 if self.map_seen_at is None else time.monotonic() - self.map_seen_at

    def diagnostics(self):
        """What this reader knows about where things are, for bug reports (a plain dict)."""
        rect, g = self.last_rect, self.gh
        covering = []
        if rect and g:
            wx, wy = rect[0], rect[1]
            if self.box and self._covered(wx + self.box[0], wy + self.box[1], self.box[2], self.box[3]):
                covering.append("EXP bar")
            if self.mesos_box and self._covered(wx + self.mesos_box[0], wy + self.mesos_box[1], self.mesos_box[2],
                                                self.mesos_box[3], margin=max(2, int(0.3 * self.mesos_box[3]))):
                covering.append("mesos box")
            if self.map_box and self._covered(wx + self.map_box[0], wy + self.map_box[1], self.map_box[2], self.map_box[3]):
                covering.append("minimap name")
            if self.party_area and self._covered(*self.party_area):
                covering.append("party list")
            if self.exp_anchor:
                ax, ay = self.exp_anchor
                if self._covered(wx + int(ax - 65 * g), wy + int(ay - 3 * g), int(46 * g), int(6.5 * g)):
                    covering.append("class name")
        return dict(mode=self.mode, found=bool(self.hwnd), rect=rect, gh=g, region=self.region,
                    own_rect=self.own_rect, covering=covering, exp_box=self.box, exp_misses=self.misses,
                    map_box=self.map_box, map_unread=self.map_unread, mesos_box=self.mesos_box,
                    mesos_fails=self.mesos_fails, two_boxes=self.two_boxes, mesos_learned=self.mesos_digits.complete,
                    party_area=self.party_area, job_why=(self.job_look or {}).get("why") or "")

    def _read_job(self, sct, rect):
        """Reads the class from the status bar now and then (it only changes at a job advance)."""
        now = time.monotonic()
        if not tesseract_ok or now < self.next_job_read:
            return
        wx, wy, _, _ = rect
        ax, ay = self.exp_anchor
        g = self.gh
        x0, y0, w, h = wx + int(ax - 65 * g), wy + int(ay - 3 * g), int(46 * g), int(6.5 * g)
        if self._covered(x0, y0, w, h):
            self.next_job_read = now + 5
            self.job_look = {"image": None, "boxes": [], "why": "this window is covering the class name: move it"}
            return
        shot = grab(sct, x0, y0, w, h)
        look = {}
        job = read_job(shot[0], wx + ax - shot[1], wy + ay - shot[2], g, look) if shot else None
        self.job_look = look or None
        if job:
            self.job = job
        self.next_job_read = now + (60 if job else 10)

    def _game_rect(self):
        if not (self.hwnd and PLATFORM.window_exists(self.hwnd)):
            self.hwnd, self.box, self.map_box = PLATFORM.find_game_window(), None, None
        rect = PLATFORM.client_rect(self.hwnd) if self.hwnd else None
        if rect and rect[2:] != self.size:                  # resized: find everything again
            self.size, self.box, self.map_box, self.gh = rect[2:], None, None, None
            self.exp_anchor, self.next_job_read = None, 0.0
            self.mesos_box, self.party_area, self.next_scan = None, None, 0.0
            saved = self.saved_mesos
            if saved and tuple(saved[:2]) == tuple(rect[2:]):
                self.mesos_box = tuple(saved[2:])           # where it was last time
        return rect

    def _hide_own(self, shot):
        """The screenshot with this app's windows (the main one and the EXP / mesos panels) blacked
        out, so it can never read itself."""
        if not shot:
            return shot
        img, sx, sy = shot
        copied = False
        for r in [self.own_rect] + list(self.hud_rects):
            if not r:
                continue
            x0, y0 = max(0, r[0] - sx), max(0, r[1] - sy)
            x1, y1 = min(img.shape[1], r[0] + r[2] - sx), min(img.shape[0], r[1] + r[3] - sy)
            if x0 < x1 and y0 < y1:
                if not copied:
                    img, copied = img.copy(), True
                img[y0:y1, x0:x1] = 0
        return img, sx, sy

    def _covered(self, x, y, w, h, margin=0):
        """Does this app's window overlap (or come within margin px of) the given area?"""
        r = self.own_rect
        return bool(r) and (x - margin < r[0] + r[2] and r[0] < x + w + margin
                            and y - margin < r[1] + r[3] and r[1] < y + h + margin)

    def _remember(self, lay, rect, shot=None):
        if lay and rect:
            self.gh = lay.gh                # tells the minimap reader how big the UI is
            if shot:                        # where the EXP number starts: the class is left of it
                self.exp_anchor = (shot[1] - rect[0] + lay.box[0], shot[2] - rect[1] + lay.cy)

    def _read_region(self, sct, rect):
        r = self.region
        if not r:
            return Reading("No area selected. Click Select area.")
        shot = self._hide_own(grab(sct, r["left"], r["top"], r["width"], r["height"]))
        reading, lay = self._read(shot, "Selected area")
        if reading.confident:
            self._remember(lay, rect)
        return reading

    def _read_window(self, sct, rect):
        wx, wy, ww, wh = rect
        where = f"MapleStory window {ww}x{wh}"
        if self.box:
            bx, by, bw, bh = self.box
            if self._covered(wx + bx, wy + by, bw, bh):
                return Reading("This window is covering the EXP bar. Move it.")
            shot = grab(sct, wx + bx, wy + by, bw, bh)
            reading, lay = self._read(shot, where)
            if reading.confident:
                self.misses = 0
                self._remember(lay, rect, shot)
                return reading
            # Not reading cleanly here (a dialog in the way, or this was the wrong spot):
            # after a few tries, look for the EXP text again.
            self.misses += 1
            if self.misses < 3:
                return reading
            self.box = None

        # Search the bottom of the window (and now and then the whole window).
        top = int(wh * (1 - SEARCH_STRIP))
        shot = self._hide_own(grab(sct, wx, wy + top, ww, wh - top))
        lay = find_exp(shot[0]) if shot else None
        if not lay and time.monotonic() - self.last_full_search > 10:
            self.last_full_search = time.monotonic()
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            lay = find_exp(shot[0]) if shot else None
        if not lay:
            if self._covered(wx, wy + top, ww, wh - top):
                return Reading("Can't find the EXP bar. Is this window covering it?")
            return Reading("Can't find the EXP bar at the bottom of the game. Is something covering it?")
        img, sx, sy = shot
        g = lay.gh
        x0, x1 = max(0, int(lay.box[0] - g)), min(img.shape[1], int(lay.box[2] + 6 * g))
        y0, y1 = max(0, int(lay.cy - 1.4 * g)), min(img.shape[0], int(lay.cy + 1.4 * g))
        reading, lay = self._read(shot, where, lay)
        if reading.confident:             # only remember a spot whose digits match the game font
            self.box = (sx - wx + x0, sy - wy + y0, x1 - x0, y1 - y0)
            self.misses = 0
            self._remember(lay, rect, shot)
        return reading

    def _read(self, shot, where, lay=None):
        """Returns (Reading, Layout or None)."""
        if not shot:
            return Reading(_problem(where, "The EXP bar is off screen.")), None
        img = shot[0]
        lay = lay or find_exp(img)
        if not lay:
            return Reading(_problem(where, "Can't find the EXP bar.")), None
        view, boxes = _view(img, lay)
        got = read_layout(self.templates, lay) if self.templates else None
        if got and fits_exp_table(*got):
            return Reading(where, [got], True, f"{got[0]:,} [{got[1]:.2f}%]", view, boxes), lay
        # Unclear digits, or a reading that fits no level: not trusted. Readings that do fit
        # a level go first, so the tracker tries those.
        cands = ([got] if got else []) + [c for c in tesseract_candidates(img, lay) if c != got]
        cands.sort(key=lambda c: not fits_exp_table(*c))
        if cands:
            e, p = cands[0]
            how = "" if (e, p) == got else " (Tesseract)"
            return Reading(where, cands, False, f"{e:,} [{p:.2f}%]{how}", view, boxes), lay
        return Reading(_problem(where, "Found the EXP bar, but can't read its numbers clearly."),
                       image=view, boxes=boxes), lay

    def _read_extras(self, sct, rect, reading):
        """Adds the mesos count and, every few seconds, the party size to a reading."""
        wx, wy, ww, wh = rect
        now, g = time.monotonic(), self.gh
        notes = [reading.note] if reading.note else []
        party = moved = None
        if now >= self.next_scan:                     # look over the whole window
            self.next_scan = now + (SCAN_SECONDS if self.mesos_box else 2)
            shot = self._hide_own(grab(sct, wx, wy, ww, wh))
            if shot:
                img, sx, sy = shot
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)      # (made once for everything below)
                fields = find_mesos_fields(img, g, hsv)         # the solid box is easy to spot
                # Storage and trade windows show a second mesos box next to yours, and which
                # is which can't be told from the picture: read neither until it's closed.
                self.two_boxes = len(fields) > 1
                field = None if self.two_boxes else (fields[0] if fields else None)
                if field and not self._readable(img, field):
                    field = None                      # a white patch in the scene, not the box
                # Look for it by its outline too if it's lost, or this window is in the way of
                # where it was (it may have been moved from there).
                if (field is None and not self.two_boxes and now >= self.next_outline
                        and (self.mesos_box is None or self.mesos_fails >= 10 or self.mesos_covered)):
                    field = self._find_seethrough_box(img, g, hsv)
                    if field is None:
                        self.next_outline = now + OUTLINE_SECONDS   # (it costs a lot on a big screen)
                if self.two_boxes:
                    self.mesos_box = None
                if field:
                    box = (sx - wx + field[0], sy - wy + field[1], field[2], field[3])
                    old = self.mesos_box
                    if old is None or max(abs(a - b) for a, b in zip(box, old)) > 3:
                        self.mesos_box, self.mesos_fails = box, 0
                        moved = (ww, wh) + box
                area = self.party_area
                if area and self._covered(*area):
                    notes.append("This window is covering the party list.")
                else:
                    bars = find_party_bars(img, g, hsv)
                    party = max(1, len(bars))
                    if len(bars) >= 2:
                        x0, y0 = min(b[0] for b in bars), min(b[1] for b in bars)
                        x1, y1 = max(b[0] + b[2] for b in bars), max(b[1] + b[3] for b in bars)
                        # Only the HP bars are counted, so only they (and a little room
                        # around them) must be clear of this window.
                        self.party_area = (sx + x0 - int(g), sy + y0 - int(g),
                                           x1 - x0 + int(2 * g), y1 - y0 + int(2 * g))
        mesos, image, boxes = None, None, ()
        self.mesos_covered = False
        if self.mesos_box:
            bx, by, bw, bh = self.mesos_box
            # (the digits only need the box and a sliver around it to be seen)
            if self._covered(wx + bx, wy + by, bw, bh, margin=max(2, int(0.3 * bh))):
                notes.append("This window is covering the inventory's mesos.")
                self.mesos_covered = True
            else:
                mesos, image, boxes = self._read_mesos(sct, wx + bx, wy + by, bw, bh)
        return reading._replace(mesos=mesos, mesos_image=image, mesos_boxes=boxes, party=party,
                                mesos_at=moved, note=" ".join(notes) or None)

    def _readable(self, img, field):
        """Clear mesos digits at field? (Always yes while the mesos font is still being
        learned: then Tesseract checks every new number instead.)"""
        learned = self.mesos_digits
        return not learned.complete or read_mesos_positions(img, field, learned.templates()) is not None

    def _find_seethrough_box(self, img, g, hsv=None):
        """The mesos box by its outline, checked by reading clear digits there.

        The digits are read at fixed spots from the box's right edge, so a box found a bit
        off could read a wrong number: it must also line up with the number."""
        field = self.box_finder.find(img, g, hsv)
        if not field:
            return None
        learned = self.mesos_digits
        templates = learned.templates() if learned.complete else self.templates
        if not templates or not mesos_box_lines_up(img, field, templates):
            return None
        return field if read_mesos_positions(img, field, templates) else None

    def _read_mesos(self, sct, x, y, w, h):
        """Reads the mesos box at (x, y, w, h) on screen. Returns (number, image, boxes)."""
        shot = grab(sct, x - 2 * h, y - h, w + 4 * h, 3 * h)
        if not shot:
            return None, None, ()
        img, sx, sy = shot
        fx, fy = x - sx, y - sy
        inner = cv2.cvtColor(img[fy + 2:fy + h - 2, fx + 2:fx + w - 2], cv2.COLOR_BGR2HSV)
        solid = ((inner[..., 2] > 225) & (inner[..., 1] < 30)).mean() > 0.6

        def boxes_of(lay, ox=0, oy=0):
            return [(ox + c[0] - c[1] / 2, oy + lay.cy - lay.gh / 2,
                     ox + c[0] + c[1] / 2, oy + lay.cy + lay.gh / 2, "mesos") for c in lay.exp]

        learned = self.mesos_digits
        if learned.complete:                          # all ten mesos digits learned
            got = read_mesos_positions(img, (fx, fy, w, h), learned.templates())
            if not got:
                self.mesos_fails += 1
                return None, img, ()
            self.mesos_fails = 0
            value, lay, score, lead = got
            if not solid and len(str(value)) != self.mesos_count:
                # The first reading, or a different number of digits than before (that only
                # happens when crossing 10,000, 100,000...): on a see-through box, only
                # believe it once 3 readings in a row agree.
                last, n = self.mesos_pending
                self.mesos_pending = (value, n + 1 if _agrees(value, last) else 1)
                if self.mesos_pending[1] < 3:
                    return None, img, boxes_of(lay)
            self.mesos_count, self.mesos_pending = len(str(value)), (None, 0)
            if solid and score >= 0.95 and lead >= 0.15:
                learned.learn(lay, value)             # more examples, from very clean looks
            return value, img, boxes_of(lay)
        # Still learning the mesos font: a new number needs Tesseract's OK. On a solid box
        # Tesseract alone is enough; on a see-through box both readers must agree.
        lay = mesos_layout(img, (fx, fy, w, h))
        if not lay:
            self.mesos_fails += 1
            return None, img, ()
        self.mesos_fails = 0
        boxes = boxes_of(lay, fx + 2, fy + 2)
        value = read_mesos_digits(lay, self.templates) if self.templates else None
        if value is not None and value == self.mesos_checked:
            return value, img, boxes
        now = time.monotonic()
        if now < self.next_mesos_check or (not solid and value is None):
            return None, img, boxes
        self.next_mesos_check = now + (2 if value is not None else 5)
        checked, votes = tesseract_mesos(lay)
        if checked is None or (not solid and checked != value):
            return None, img, boxes
        self.mesos_checked = checked
        if checked == value or votes == 6:            # two readers agree: learn these digits
            learned.learn(lay, checked)
        return checked, img, boxes

    def _read_info(self, sct, rect):
        """Returns (map name if it was just read, note)."""
        if not tesseract_ok:
            return None, "Map names can't be read: Tesseract OCR is missing (see the README)."
        if self.own_rect is None:
            return None, None                   # wait until we know where this app's window is
        wx, wy, ww, wh = rect
        g, now = self.gh, time.monotonic()
        name = note = None

        hidden = self.map_blind_seconds() > MAP_HIDDEN_SECONDS
        if self.map_box is None and now >= self.next_map_search:
            self.next_map_search = now + (1 if hidden else 3)     # looks harder for a name that has gone missing
            shot = self._hide_own(grab(sct, wx, wy, int(ww * 0.4), int(wh * 0.3)))
            box = find_map_box(shot[0], g) if shot else None
            if box:
                self.map_box = (shot[1] - wx + box[0], shot[2] - wy + box[1], box[2], box[3])
                self.map_print, self.map_unread = None, True
        if self.map_box is None:
            if self._covered(wx, wy, int(ww * 0.4), int(wh * 0.3)):
                note = "This window may be covering the minimap. Move it so the map name can be read."
            else:
                note = "Can't see the map name. Is the minimap open?"
        else:
            bx, by, bw, bh = self.map_box
            if self._covered(wx + bx, wy + by, bw, bh, margin=max(3, int(0.1 * g))):
                # Even a box that only touches this window may be cut off by it (it was
                # found with this window blacked out). Find the whole box again (and read
                # the name) once this window is out of the way.
                note = "This window is covering the minimap. Move it so the map name can be read."
                self.map_box, self.map_unread = None, True
            else:
                shot = grab(sct, wx + bx, wy + by, bw, bh)
                if shot:
                    self.map_seen_at = now
                    picture = cv2.resize(_whiteness(shot[0]), (64, 16), interpolation=cv2.INTER_AREA) > 0.3
                    if self.map_print is not None and (picture != self.map_print).mean() > 0.04:
                        # Changed map: find the box again (its size can change), then read it.
                        self.map_box, self.next_map_search = None, now + 1
                        self.map_unread, self.next_map_read = True, 0.0
                    elif self.map_unread and now >= self.next_map_read:
                        name = read_map_name(shot[0], g)
                        self.map_look = {"image": shot[0], "text": name}
                        self.map_unread = name is None
                        self.next_map_read = now + (2 if hidden else 5)   # if unreadable, try again later
                    self.map_print = picture
        return name, note


# --------------------------------------------------------------------------- tracking

class ExpTracker:
    """Turns noisy readings into trustworthy EXP gains.

    Every level needs a fixed amount of EXP (req), and the bar shows exp = pct% * req.
    req comes from the EXP table as soon as two clean readings point at the same level
    (otherwise it's learned from several *different* readings that agree with each other),
    then a reading only counts if its EXP and % agree with req. Misread digits break that,
    so they are ignored instead of being counted as gains.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.running = False
        self._accum = 0.0
        self._resumed = None
        self.exp = None                       # last trusted EXP
        self.pct = None
        self.last_read = None                 # latest reading, trusted or not (for display)
        self.req = None                       # EXP required for the current level
        self._req_exact = False               # req came straight from the EXP table
        self._table_level = None              # level the last clean reading pointed at
        self._seen = {}                       # distinct (exp, pct) readings this level -> weight
        self._pending = None                  # big change waiting for confirmation (e, p, reads)
        self._recent = deque(maxlen=40)       # (when, EXP) of the latest trusted readings
        self._reads = 0                       # readings seen, as a clock for _recent
        self._misfits = 0                     # readings in a row that didn't fit req
        self._pending_lvl = None              # level-up waiting for confirmation
        self._level_start = None              # (exp, pct) right after a level-up, unverified
        self._prev_req = None                 # previous level's req, bounds the new one
        self._before_death = None             # (reads, EXP) just before the last death
        self.deaths = 0
        self.last_loss = None                 # % of the level's EXP the last death cost
        self.gained = 0
        self.levels = 0
        self.samples = deque()                # (elapsed, gained)

    def start(self):
        if not self.running:
            self.running = True
            self._resumed = time.monotonic()

    def pause(self, at=None):
        """Stops the clock (as of `at`, e.g. the last EXP gain, so idle time doesn't count)."""
        if self.running:
            end = time.monotonic() if at is None else min(time.monotonic(), at)
            self._accum += max(0.0, end - self._resumed)
            self.running = False

    def new_session(self):
        """Numbers from zero again, keeping what's known about the level and current EXP."""
        self.running = False
        self._accum, self._resumed = 0.0, None
        self.gained, self.levels, self.deaths = 0, 0, 0
        self.samples = deque()

    def elapsed(self):
        if self.running:
            return self._accum + time.monotonic() - self._resumed
        return self._accum

    # ---- level requirement
    @staticmethod
    def _rel_err(pct):
        # % is shown with 2 decimals, so exp / pct is only this precise
        return 0.002 + 0.01 / pct

    def _learn_req(self, candidates, confident):
        weight = 2 if confident else 1        # font-matched readings are rarely wrong
        for e, p in candidates:
            if p >= 0.2 and e > 0:                # (a misread 0 says nothing about req)
                self._seen[(e, p)] = max(self._seen.get((e, p), 0), weight)
        while len(self._seen) > 60:
            del self._seen[next(iter(self._seen))]
        ests = [(e / (p / 100), self._rel_err(p), w) for (e, p), w in self._seen.items()]
        if self._prev_req:                    # the next level needs about as much or more
            ests = [x for x in ests if 0.98 <= x[0] / self._prev_req <= 2.5]
        group_of = lambda c, ce: [x for x in ests if abs(x[0] - c) / c <= ce + x[1]]
        weight_of = lambda g: sum(x[2] for x in g)
        center = lambda g: sum(x[0] * x[2] / x[1] ** 2 for x in g) / sum(x[2] / x[1] ** 2 for x in g)
        groups = sorted((group_of(c, ce) for c, ce, _ in ests), key=weight_of, reverse=True)
        if not groups or weight_of(groups[0]) < 3:
            return
        best = groups[0]
        mid = center(best)
        if self.req is None:
            # Lock in only once one value clearly beats any other (misreads can agree too).
            rivals = [g for g in groups if not 0.99 <= center(g) / mid <= 1.01]
            if not rivals or weight_of(best) >= 2 * weight_of(rivals[0]):
                self.req = mid
            return
        if self._req_exact:
            return                            # straight from the EXP table: misreads can't move it
        current = group_of(self.req, 0.003)
        if 0.99 <= mid / self.req <= 1.01 or weight_of(best) < 1.5 * weight_of(current) + 1:
            if current:
                self.req = center(current)
            return
        # A different value now clearly has more support: the old one came from misreads.
        # Switch, and restart the baseline so the change isn't counted as a gain.
        self.req, self._req_exact = mid, False
        self.exp = self.pct = None
        self._pending = None

    def _req_from_table(self, reading):
        """Takes the level's EXP straight from the EXP table once two clean readings in a
        row point at the same single level, so there's no wait for EXP gains."""
        e, p = reading
        levels = levels_for(e, p) if p >= 0.5 else []
        level = levels[0] if len(levels) == 1 else None
        if level and self._prev_req and EXP_TABLE[level - 1] < 0.98 * self._prev_req:
            level = None                      # after a level-up, the next level needs more
        if level and level == self._table_level:
            self.req, self._req_exact = EXP_TABLE[level - 1], True
        self._table_level = level

    def _consistent(self, e, p):
        return abs(e / self.req * 100 - p) <= 0.02 + 0.003 * p

    def _add_gain(self, amount):
        if self.running:
            self.gained += amount
            now = self.elapsed()
            self.samples.append((now, self.gained))
            while self.samples and now - self.samples[0][0] > ROLLING_WINDOW:
                self.samples.popleft()

    # ---- main entry
    def observe(self, candidates, confident=False):
        """Feed the (exp, pct) guesses for one screenshot. Returns (accepted, note)."""
        candidates = [(e, p) for e, p in candidates if p is not None and 0 <= p < 100]
        if not candidates:
            return False, "unreadable"
        self._reads += 1
        self.last_read = candidates[0]

        if self._check_level_up(candidates):
            return True, "LEVEL UP!"
        if self._pending_lvl:
            # Looks like a level-up, not confirmed yet: don't also take it as EXP lost.
            return False, "level up? waiting to confirm"

        if self.req is None and confident:
            self._req_from_table(candidates[0])
        self._learn_req(candidates, confident)
        if self.req is None:
            return False, "calibrating: learns your level's EXP after your next EXP gain"

        good = [c for c in candidates if self._consistent(*c)]
        if not good:
            self._misfits += 1
            if self._req_exact and self._misfits >= 30:
                # Nothing has fitted this level for a long while: the level itself may be
                # wrong. Work it out again.
                self.req, self._req_exact, self._table_level, self._misfits = None, False, None, 0
                self.exp = self.pct = self._pending = None      # and start counting afresh
            return False, "misread, ignored"
        self._misfits = 0
        e, p = good[0]
        self.last_read = good[0]

        if self._level_start:
            # First trusted reading after a level-up: fix the start value if it was misread.
            e0, p0 = self._level_start
            self._level_start = None
            if not self._consistent(e0, p0):
                fixed = round(p0 / 100 * self.req)
                self._add_gain(fixed - e0)
                self.exp = fixed

        if self.exp is None:
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"

        delta = e - self.exp
        if (0.01 * self.req <= -delta or (e == 0 and delta < 0)) and -delta <= 0.15 * self.req                 and not self._was_near(e):
            # EXP went down a few % of the level (or to 0): dying (it costs up to about 10%). Once a
            # second reading agrees, carry on from the new number without counting the loss,
            # so a death doesn't drag EXP/hr down (or below zero).
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.005 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "EXP went down, waiting to confirm"
            self._pending = None
            self._before_death = (self._reads, self.exp)
            self.last_loss = -delta / self.req * 100
            self.deaths += 1
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "died"
        if self._before_death and self._reads - self._before_death[0] <= 40 and delta > 0 \
                and e >= self._before_death[1] - 0.005 * self.req:
            # Right back to where it was before "dying", in one go: those low readings were a
            # misread, not a death (EXP lost on dying comes back only slowly, from monsters).
            self._before_death, self.deaths = None, max(0, self.deaths - 1)
            self._pending = None
            self.exp, self.pct = e, p
            self._recent.append((self._reads, e))
            return True, "baseline"
        if abs(delta) > 0.15 * self.req:
            # Unusually big change in one tick: only believe it if the next read agrees.
            last = self._pending
            n = last[2] + 1 if last and abs(e - last[0]) <= 0.15 * self.req else 1
            self._pending = (e, p, n)
            if n < 2:
                return False, "big jump, waiting to confirm"
            if delta < 0 and not self._undoes_jump(e):
                # EXP never drops this much (dying costs at most 10%; level-ups are checked
                # separately), so this is a misread, even twice in a row. If it keeps
                # showing up, carry on from it, without counting the drop.
                if n < 8:
                    return False, "misread, ignored"
                self._pending, self.exp, self.pct = None, e, p
                self._recent.append((self._reads, e))
                return True, "baseline"
        self._pending = None
        self._add_gain(delta)
        self.exp, self.pct = e, p
        self._recent.append((self._reads, e))
        return True, ""

    def _was_near(self, e):
        """Was EXP about e a moment ago? Then going down to it is the right number coming
        back after a misread that was a bit too high (and counts, to cancel that out)."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.005 * self.req for r, x in self._recent)

    def _undoes_jump(self, e):
        """Was EXP around e a moment ago? Then a big drop to it is the right number coming
        back after a misread that was too high, and must count, to cancel that out."""
        return any(self._reads - r <= 40 and abs(e - x) <= 0.15 * self.req for r, x in self._recent)

    def _check_level_up(self, candidates):
        """% dropped a lot and EXP reset low: a level-up, once readings in a row agree on it.

        If the level is known, readings that fit the next level's EXP from the table count,
        and two in a row are enough. Otherwise a reading that still fits this level's EXP is
        never a level-up (e.g. the right number coming back after a misread that was too
        high), and as the next level's EXP is only known to be bigger, it takes five.
        """
        if self.exp is None or self.pct is None or self.req is None:
            return False
        level = level_for_req(self.req)
        nxt = EXP_TABLE[level] if level and level < len(EXP_TABLE) else None
        hits = []
        for e, p in candidates:
            if not (self.pct - p > 30 and e < self.exp):
                continue
            if nxt and abs(e / nxt * 100 - p) <= 0.02 + 0.003 * p:
                hits.append((e, p, 2))        # fits the next level exactly
            elif self._consistent(e, p):
                continue                      # still this level: not a level-up
            elif p >= 0.2 and 0.98 * self.req <= e / (p / 100) <= 2.5 * self.req:
                hits.append((e, p, 5))        # next level's EXP is similar or bigger
        if not hits:
            self._pending_lvl = None
            return False
        e, p, need = hits[0]
        last = self._pending_lvl
        # Each reading must carry on from the one before (EXP only goes up from there).
        n = last[2] + 1 if last and 0 <= e - last[0] <= 0.15 * (nxt or self.req) else 1
        self._pending_lvl = (e, p, n)
        if n < need:
            return False
        self._pending_lvl = self._pending = None
        if self.running:
            self.levels += 1
        self._add_gain(self.req - self.exp + e)
        self.exp, self.pct = e, p
        self._prev_req = self.req
        if need == 2:                          # the new level's EXP is known: no relearning
            self.req, self._req_exact = nxt, True
        else:
            self.req, self._req_exact = None, False
        self._table_level = None
        self._seen.clear()
        self._recent.clear()                   # the old level's numbers mean nothing now
        self._recent.append((self._reads, e))
        self._level_start = (e, p)
        return True

    def rate(self):
        t = self.elapsed()
        return self.gained / t * 3600 if t >= 10 else None

    def rolling_rate(self):
        if len(self.samples) < 2:
            return None
        (t0, g0), (t1, g1) = self.samples[0], self.samples[-1]
        return (g1 - g0) / (t1 - t0) * 3600 if t1 - t0 >= 30 else None

    def recent_gain(self):
        """EXP gained in the last 5 minutes (or since tracking started, if that's less), or None."""
        if len(self.samples) < 2:
            return None
        return self.samples[-1][1] - self.samples[0][1]


class MesoTracker:
    """Turns mesos readings into mesos gained per hour and per 5 minutes.

    A new count only counts once two readings in a row agree on it (three for a big jump),
    so a one-off misread never shows up as mesos gained. A change by more than half,
    or a digit-count change bigger than 10%, starts counting afresh from the new number
    instead: that's a shop visit or a corrected misread, not mesos picked up. Small digit
    rollovers count after three identical readings.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.value = None                     # confirmed mesos count
        self.seen_at = None                   # when it was last confirmed
        self.seen_el = None                   # ...in tracking time (the inventory may be closed since)
        self._candidate = (None, 0, 0)
        self.gained = 0
        self.earned = self.spent = 0          # the ups and downs that make up gained (spent is positive)
        self.samples = deque()                # (elapsed, gained)
        self.start = None                     # elapsed time when counting began
        self.counting = False

    def pause(self):
        self.counting = False                 # don't count what changes while paused

    def observe(self, value, now, elapsed, running):
        jump = False
        if value == self.value:
            self._candidate = (None, 0, 0)        # unchanged: nothing new to confirm
        else:
            # How many readings in a row back the new number: close to it, and exactly it.
            last, n, same = self._candidate
            n = n + 1 if _agrees(value, last) else 1
            same = same + 1 if value == last else 1
            self._candidate = (value, n, same)
            old = self.value
            # Measured against the bigger of the two, so a misread and its correction are
            # always judged the same way (both counted, or both not), and cancel out.
            top = max(value, old) if old is not None else 0
            digits_changed = old is not None and len(str(value)) != len(str(old))
            jump = old is not None and abs(value - old) > (0.1 if digits_changed else 0.5) * top
            big = old is not None and abs(value - old) > max(10000, 0.05 * top)
            # The first count, a digit change and a jump need the very same number 3 times.
            # First counts and jumps aren't gained, so a misread there would never cancel
            # out; digit changes can also be different, nearby misreads of an extra digit.
            # About 10x (or a tenth): almost always a digit read too many (or lost).
            tenfold = old is not None and old > 0 and (9 <= value / old <= 11 or 0.09 <= value / old <= 0.11)
            if old is None or jump or digits_changed:
                if same < (8 if tenfold else 3):
                    return
            elif n < (3 if big else 2):
                return
            self._candidate = (None, 0, 0)
        self.seen_at, self.seen_el = now, elapsed
        if running and not self.counting:
            self.counting = True                  # count changes from this reading on
            self.start = elapsed if self.start is None else self.start
            self.samples.append((elapsed, self.gained))
        elif running and value != self.value and not jump:
            self.gained += value - self.value
            if value > self.value:
                self.earned += value - self.value
            else:
                self.spent += self.value - value
            self.samples.append((elapsed, self.gained))
            while len(self.samples) > 2 and self.samples[1][0] <= elapsed - ROLLING_WINDOW:
                self.samples.popleft()
        self.value = value

    # The mesos are only read while the inventory is open. So the rates are worked out over the
    # time up to the last reading, not up to now: with the inventory closed they hold steady
    # (instead of sinking as the time grows with nothing counted), and when it's opened again
    # everything picked up meanwhile is counted at once, over all that time.

    def _end(self, elapsed):
        return min(elapsed, self.seen_el) if self.seen_el is not None else elapsed

    def per_hour(self, elapsed):
        end = self._end(elapsed)
        if self.start is None or end - self.start < 30:
            return None
        return self.gained / (end - self.start) * 3600

    def per_5min(self, elapsed):
        if self.start is None:
            return None
        end = self._end(elapsed)
        t0 = max(self.start, end - ROLLING_WINDOW)
        if end - t0 < 60:
            return None
        before = 0
        for t, g in self.samples:
            if t > t0:
                break
            before = g
        return (self.gained - before) / (end - t0) * ROLLING_WINDOW

    def unseen(self, elapsed):
        """Seconds of tracking since the mesos were last read (the inventory closed), or 0."""
        return max(0.0, elapsed - self.seen_el) if self.seen_el is not None and self.counting else 0.0

    def guess_since_seen(self, elapsed):
        """Mesos probably picked up since the last reading, at the rate so far (0 if there's no rate yet)."""
        rate = self.per_hour(elapsed)
        return max(0.0, rate) * self.unseen(elapsed) / 3600 if rate else 0.0


class Session:
    """What's on screen: EXP and mesos since Start, and when tracking pauses by itself.

    After Start it tracks until Pause is pressed, except that it pauses by itself when no
    EXP comes in for `gap` seconds or you leave the map, and carries on at the next EXP
    gain: from zero if that's on a different map, so the numbers are always for the map
    you're on. Time spent paused never counts, and an idle pause rewinds the clock to the
    last EXP gain. The gain that ends a pause starts the clock again rather than counting,
    just like a log stretch's first gain.
    """

    def __init__(self, gap):
        self.exp, self.meso = ExpTracker(), MesoTracker()
        self.gap = gap
        self.on = False             # Start was pressed (and Pause wasn't)
        self.waiting = None         # paused by itself: "idle" or "map"
        self.map = None             # the map these numbers are for
        self.here = None            # the map you're on now
        self.last_gain = None

    def start(self, now):
        self.on, self.waiting, self.last_gain = True, None, now
        self.map = self.map or self.here
        self.exp.start()

    def pause(self):
        self.on, self.waiting = False, None
        self.exp.pause()
        self.meso.pause()

    def reset(self):
        self.exp.reset()
        self.meso.reset()
        self.on, self.waiting, self.map, self.last_gain = False, None, None, None

    def _wait(self, why):
        self.exp.pause(at=self.last_gain)
        self.meso.pause()
        self.waiting = why

    def observe(self, candidates, confident, now):
        """Feeds an EXP reading to the tracker. Returns the tracker's (accepted, note)."""
        t = self.exp
        exp_before, gained_before = t.exp, t.gained
        result = t.observe(candidates, confident)
        rose = exp_before is not None and t.exp is not None and (
            t.exp > exp_before or result[1] == "LEVEL UP!")
        if t.running and t.gained > gained_before:
            self.last_gain = now
        elif self.on and self.waiting and self.waiting != "blind" and rose:
            if self.map and self.here and self.here != self.map:
                t.new_session()             # a different map: its own numbers
                self.meso.reset()
                self.map = self.here
            self.waiting = None
            self.last_gain = now
            t.start()
        return result

    def map_blind(self):
        """The minimap's name has been out of sight for a while (hidden, covered, or the game
        closed it): you may have changed maps without it noticing, so it pauses instead of
        carrying on under the old name. It carries on once the name can be read again."""
        self.here = None
        if self.waiting == "blind" or not self.on:
            return
        if self.exp.running:
            self._wait("blind")
        elif self.waiting:
            self.waiting = "blind"

    def map_seen(self, name):
        """The map name was read (already matched to known names, see AutoLog.canonical)."""
        if self.waiting == "blind":
            self.waiting = "map"            # visible again: the usual rule decides (same map: carry on; another: new numbers)
        self.here = name
        if not self.on:
            return
        if self.map is None:
            self.map = name
        elif name != self.map:              # left the map
            if self.exp.running:
                self._wait("map")
            elif self.waiting:
                self.waiting = "map"

    def tick(self, now):
        """Call regularly: pauses after `gap` seconds without EXP."""
        if self.on and self.exp.running and now - self.last_gain > self.gap:
            self._wait("idle")


# --------------------------------------------------------------------------- auto log

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def party_label(n):
    return "Solo" if not n or n <= 1 else f"{n}ppl party"


def fmt_logged(seconds, short=False):
    """How long a log line was measured over: '25 min' / '1 h 05 min', or short '25m' / '1h 05m'."""
    hours, minutes = divmod(int(round((seconds or 0) / 60)), 60)
    if short:
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def format_entry(e):
    """e.g. 'Oct 8th, 2026 - Rocky Road II - Lv 27 - Bandit - Solo - 15,429exp/hr - 3,210mesos/hr - 25 min'"""
    d = date.fromisoformat(e["date"])
    level = e["level"] if e["level"] is not None else "?"
    parts = [f"{MONTHS[d.month - 1]} {_ordinal(d.day)}, {d.year}", e["map"], f"Lv {level}"]
    if e.get("class"):
        parts.append(e["class"])
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    if tracked:
        parts.append(party_label(e["party"]))
    parts.append(f"{e['exp'] / e['seconds'] * 3600 if e['seconds'] else 0:,.0f}exp/hr")
    if tracked:
        ms = e.get("mesos_seconds", 0)
        parts.append(f"{e['mesos'] / ms * 3600:,.0f}mesos/hr" if ms else "?mesos/hr")
    parts.append(fmt_logged(e["seconds"]))
    return " - ".join(parts)


def clean_note(text):
    """A note as it's kept: plain text with \\n line breaks, no control characters or lone
    surrogates (neither can be shown or saved), without blanks at either end."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or (ch >= " " and ch != "\x7f"))
    return text.encode("utf-8", "ignore").decode("utf-8").strip()


def note_of(e):
    """The personal note on a log line, ready to show ('' if none, or if what's there isn't text)."""
    n = e.get("note")
    return clean_note(n) if isinstance(n, str) else ""


def notes_of(entries):
    """One note for log lines shown as one (a map's party sizes): each different note once."""
    return "\n\n".join(dict.fromkeys(n for n in map(note_of, entries) if n))


def log_row(e, part=False):
    """The log table's cells for an entry: Date, Map, Lv, Class, Party, Time, EXP/hr, Mesos/hr,
    Note. `part`: one party size under a merged party line, so just its own numbers."""
    d = date.fromisoformat(e["date"])
    day = f"{MONTHS[d.month - 1]} {d.day}" + ("" if d.year == date.today().year else f" '{d.year % 100:02d}")
    level = e["level"] if e["level"] is not None else "?"
    tracked = "party" in e                    # entries from before party/mesos tracking lack these
    party = ("Solo" if not e["party"] or e["party"] <= 1 else f"{e['party']}ppl") if tracked else ""
    exp = f"{e['exp'] / e['seconds'] * 3600:,.0f}" if e["seconds"] else "—"
    ms = e.get("mesos_seconds", 0)
    mesos = f"{e['mesos'] / ms * 3600:,.0f}" if ms else ("?" if tracked else "")
    note = NOTE_MARK if note_of(e) else ""
    if part:
        return "", "", "", "", party, fmt_logged(e["seconds"], short=True), exp, mesos, note
    return day, e["map"], level, e.get("class") or "", party, fmt_logged(e["seconds"], short=True), exp, mesos, note


def party_groups(entries):
    """Log lines as the log shows them, newest first: the party sizes (2 to 6) of one map on
    one day, at one level and class, together (smallest party first); everything else, solo
    play included, on its own. (Party sizes come and go as people join and leave.)"""
    groups = {}
    for e in entries:
        party = e.get("party")
        key = (e["date"], e["map"], e["level"], e.get("class"), "party") if party and party >= 2 else id(e)
        groups.setdefault(key, []).append(e)
    return [(key, sorted(g, key=lambda e: e["party"]) if len(g) > 1 else g) for key, g in groups.items()]


def merged(entries):
    """Log lines added together, so EXP/hr and mesos/hr are over all of their time."""
    m = dict(entries[0])
    for k in ("exp", "seconds", "mesos", "mesos_seconds"):
        m[k] = sum(e.get(k) or 0 for e in entries)
    return m


def group_row(entries, is_open=False):
    """The log table's cells for a map's party sizes together, e.g. Party '4–5ppl ▸'."""
    row = list(log_row(merged(entries)))
    sizes = [e["party"] for e in entries]
    row[4] = f"{sizes[0]}–{sizes[-1]}ppl {'▾' if is_open else '▸'}"
    row[8] = NOTE_MARK if notes_of(entries) else ""
    return tuple(row)


def _numbers(name):
    """Roman numerals and numbers in a map name. Maps that differ in these are different maps."""
    return re.findall(r"\b(?:[IVX]+|\d+)\b", name)


LOG_VERSION = 3     # 2: mesos/hr from the mesos tracker; 3: class, seals and runs
                    # (a line's personal "note" needed no new version: it's one more optional field,
                    # which older versions leave alone and keep)
NOTE_MAX = 4000     # characters in a note on a log line
NOTE_MARK = "\u270e"            # shown in the log's Note column for lines that have one
BACKUPS_KEPT = 7    # days of log backups kept (backups/ in the data folder)
RUN_SECONDS = 900   # a run: 15 minutes of non-stop farming on one map, one party size
                    # (until 0.9.4 a run was an hour; those are still shared as they are)

# Each line the tracker writes is sealed: a code worked out from its numbers. A line edited
# (or added) by hand doesn't match its seal any more, so it's known not to be the tracker's
# own. (Someone who digs into the program could still forge one, which is why anything
# shared will also be checked against what other players get.)
# (The old name stays in the key: changing it would unseal every log written so far.)
_SEAL_KEY = hashlib.sha256(b"Maple EXP/hr log seal: keep this log honest").digest()
_SEALED_LINE = ("date", "map_read", "level", "party", "class", "exp", "seconds", "mesos", "mesos_seconds")
_SEALED_RUN = ("date", "started", "map_read", "class", "level_start", "level_end", "party", "exp",
               "seconds", "mesos", "mesos_seconds", "version")


class AutoLog:
    """EXP/hr and mesos/hr per day, map, level, class and party size, measured over 5-minute
    stretches of non-stop EXP gain, plus 15-minute runs (shared with the stats site, if agreed).

    A stretch starts with an EXP gain and is thrown away if no EXP comes in for `gap`
    seconds, or the map or party size changes. Each finished stretch is added to that day's
    entry for the map, level, class and party size, so the entry gets more accurate the
    longer you play there. Mesos are counted for a stretch when the mesos were read during it.

    Alongside, a run goes on as long as the stretches do (a level-up doesn't stop it, a new
    map or party size does). Every full 15 minutes is saved as a run of its own.

    Every change re-reads the log file first and saves straight away, so lines written by
    anyone else (another copy of the app, or by hand) are never overwritten.
    """

    def __init__(self, gap_seconds, path=LOG_PATH, text_path=LOG_TEXT_PATH):
        self.gap = gap_seconds
        self.path, self.text_path = path, text_path
        self.entries, self.aliases, self.runs = [], {}, []   # aliases: misread/renamed map -> name
        self.install = secrets.token_hex(8)           # this log's own id, part of its seals
        self._kept = set()                            # problems a copy of the file was kept for
        version = self._load()
        if version < 2:
            def clear_old_mesos():                    # logged by the old, unreliable mesos counting
                for e in self.entries:
                    if "party" in e:
                        e["mesos"] = e["mesos_seconds"] = 0
            self._change(clear_old_mesos)
        elif version < LOG_VERSION:
            self._change(lambda: None)                # saves this log's id with it
        self.reset_clock()

    @staticmethod
    def key(e):
        return e["date"], e["map"], e["level"], e.get("party"), e.get("class")

    # ---- seals
    def _seal(self, record, fields):
        data = json.dumps([record.get(k) for k in fields], separators=(",", ":"))
        return hmac.new(_SEAL_KEY + self.install.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]

    def sealed(self, e, fields=_SEALED_LINE):
        """True for a line exactly as the tracker wrote it."""
        return isinstance(e.get("seal"), str) and hmac.compare_digest(e["seal"], self._seal(e, fields))

    def edited(self, e):
        """True for a line that was changed outside the tracker (older lines have no seal)."""
        return "seal" in e and not self.sealed(e)

    def _reseal(self, e, ok):
        e["seal"] = self._seal(e, _SEALED_LINE) if ok else "edited"

    # ---- the file
    def _load(self):
        """Reads the log file into memory (keeps what's there if it can't be read).
        Returns the file's format version."""
        if not self.path.exists():
            return LOG_VERSION
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries, aliases = data.get("entries", []), data.get("aliases", {})
            runs = data.get("runs", [])
            if not isinstance(entries, list) or not isinstance(aliases, dict) or not isinstance(runs, list):
                raise ValueError("unexpected contents")
        except Exception as e:
            self._keep_copy("reading the EXP log", e)        # damaged, e.g. edited by hand
            return LOG_VERSION
        good = [e for e in entries if self._valid(e)]
        if len(good) < len(entries):
            self._keep_copy(f"reading the EXP log: left out {len(entries) - len(good)} damaged lines")
        self.entries = good
        self.aliases = {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}
        self.runs = [r for r in runs if isinstance(r, dict)]   # (only sealed ones are ever shared)
        if isinstance(data.get("install"), str) and re.fullmatch(r"[0-9a-f]{16}", data["install"]):
            self.install = data["install"]
        version = data.get("version", 1)
        return version if type(version) is int else LOG_VERSION

    def _keep_copy(self, what, exc=None):
        """Keeps a copy of the log file before it's saved over (once per problem)."""
        backup = self.path.with_name(f"exp_log.damaged-{time.strftime('%Y%m%d-%H%M%S')}.json")
        if what not in self._kept:
            self._kept.add(what)
            try:
                shutil.copy2(self.path, backup)
            except Exception:
                pass
            log_error(f"{what} (the file was copied to {backup.name})", exc)

    @staticmethod
    def _valid(e):
        """An entry with everything the log needs, each of the right kind, as the overlay writes
        them. Anything else is left out, not crashed on: the file may have been edited by hand."""
        def number(x):
            return type(x) in (int, float) and math.isfinite(x)

        def optional(key, ok, empty=False):           # missing (or empty), or of the right kind
            return key not in e or (empty and e[key] is None) or ok(e[key])
        try:
            date.fromisoformat(e["date"])
            return (isinstance(e["map"], str) and e["map"].strip() != "" and len(e["map"]) <= 200
                    and number(e["exp"]) and number(e["seconds"]) and e["seconds"] >= 0
                    and (e["level"] is None or (type(e["level"]) is int and 1 <= e["level"] <= 300))
                    and optional("party", lambda v: type(v) is int and 1 <= v <= 6, empty=True)
                    and optional("class", lambda v: isinstance(v, str), empty=True)
                    and optional("map_read", lambda v: isinstance(v, str), empty=True)
                    and optional("seal", lambda v: isinstance(v, str))
                    and optional("updated", number)
                    and optional("mesos", number) and optional("mesos_seconds", number)
                    and (not e.get("mesos_seconds") or number(e.get("mesos"))))
        except Exception:
            return False

    def _change(self, change):
        """Applies change() to the latest log on disk and saves it."""
        self._load()
        result = change()
        self.save()
        return result

    # ---- tracking
    def reset_clock(self):
        self.start = self.last_gain = self.block_map = self.block_party = self.meso0 = None
        self.block_exp = 0
        self.run = None                                  # the 15-minute run going on, if any

    def gain(self, now, amount):
        if amount > 0:
            if self.start is None:
                self.start, self.block_exp = now, 0          # this gain starts the clock
                self.run = self.run or {"start": now, "exp": 0, "map": None}
            else:
                self.block_exp += amount
                if self.run:
                    self.run["exp"] += amount
            self.last_gain = now
        elif self.start is not None:
            self.block_exp += amount                         # a misread too high, taken back
            if self.run:
                self.run["exp"] += amount

    def tick(self, now, map_name, level, party=1, mesos=None, job=None, map_read=None, hidden=False):
        """Call regularly while tracking. mesos is (mesos gained so far, when last read) from
        the MesoTracker, or None; job is the class; map_read the map name exactly as read.
        Returns the entry when one was logged."""
        if self.start is None:
            return None
        if now - self.last_gain > self.gap:
            self.reset_clock()                               # no EXP for too long: start over
            return None
        if map_name:
            if self.block_map and map_name != self.block_map:
                self.reset_clock()                           # changed maps: start over
                return None
            self.block_map = map_name
        if self.block_party is not None and party != self.block_party:
            self.reset_clock()                               # party size changed: start over
            return None
        self.block_party = party
        if self.meso0 is None and mesos and mesos[1] >= self.start - 2:
            self.meso0 = mesos                               # mesos as the stretch began
        self._run_tick(now, map_name, level, party, mesos, job, map_read, hidden)
        if hidden or now - self.start < LOG_BLOCK:       # (hidden: the map may have changed, so nothing is saved yet)
            return None
        if self.block_exp <= 0:                              # nothing gained after all
            self.start, self.block_exp = now, 0
            return None
        meso_gain = meso_seconds = 0
        if self.meso0 and mesos and mesos[1] > self.meso0[1]:
            meso_gain, meso_seconds = mesos[0] - self.meso0[0], mesos[1] - self.meso0[1]
        entry = self._add(self.block_map or "Unknown map", level, party, self.block_exp,
                          now - self.start, meso_gain, meso_seconds, job, map_read or self.block_map)
        self.start, self.block_exp = now, 0                  # the next 5 minutes start now
        self.meso0 = mesos if mesos and now - mesos[1] < 10 else None
        return entry

    def _run_tick(self, now, map_name, level, party, mesos, job, map_read, hidden=False):
        r = self.run
        if r is None:
            return
        if r["map"] is None:                                 # what the run is about, once known
            if not map_name:
                r["start"] = now                             # no map yet: the run can't start
                r["exp"] = 0
                return
            r.update(map=map_name, map_read=map_read or map_name, party=party, job=job,
                     level_start=level, wall=time.time() - (now - r["start"]), meso0=None)
        if job and r["job"] and job != r["job"]:
            self.run = {"start": now, "exp": 0, "map": None}  # a different class: a new run
            return
        r["job"] = r["job"] or job
        r["level"] = level
        if r["meso0"] is None and mesos and mesos[1] >= r["start"] - 2:
            r["meso0"] = mesos
        if hidden or now - r["start"] < RUN_SECONDS:
            return
        meso_gain = meso_seconds = 0
        if r["meso0"] and mesos and mesos[1] > r["meso0"][1]:
            meso_gain, meso_seconds = mesos[0] - r["meso0"][0], mesos[1] - r["meso0"][1]
        if r["exp"] > 0:
            self._add_run(r, now - r["start"], meso_gain, meso_seconds)
        self.run = {"start": now, "exp": 0, "map": None}     # straight on into the next run
        self._run_tick(now, map_name, level, party, mesos, job, map_read, hidden)

    def run_progress(self, now):
        """Seconds into the 15-minute run going on, or None."""
        r = self.run
        return None if r is None or r["map"] is None else max(0.0, now - r["start"])

    def runs_done(self, map_name, party, job, day=None):
        """How many runs were finished (sealed) on a day for a map, party size and class."""
        day = day or date.today().isoformat()
        return sum(1 for r in self.runs if r.get("date") == day and r.get("map") == map_name
                   and r.get("party") == party and r.get("class") == job and self.sealed(r, _SEALED_RUN))

    def unshared_runs(self):
        """Finished runs (exactly as the overlay saved them) not shared yet."""
        return [r for r in self.runs if not r.get("shared") and self.sealed(r, _SEALED_RUN)][:50]

    def mark_shared(self, started):
        def apply():
            for r in self.runs:
                if r.get("started") in started:
                    r["shared"] = True
        self._change(apply)

    def seconds_left(self, now):
        return None if self.start is None else max(0.0, LOG_BLOCK - (now - self.start))

    def _add(self, map_name, level, party, exp, seconds, mesos, meso_seconds, job=None, map_read=None):
        def add():
            key = (date.today().isoformat(), map_name, level, party, job)
            for e in self.entries:
                if self.key(e) == key:
                    ok = self.sealed(e)                      # still exactly the tracker's own?
                    e["exp"] += exp
                    e["seconds"] += seconds
                    e["mesos"] = e.get("mesos", 0) + mesos
                    e["mesos_seconds"] = e.get("mesos_seconds", 0) + meso_seconds
                    if "seal" in e:
                        self._reseal(e, ok)
                    break
            else:
                e = {"date": key[0], "map": map_name, "map_read": map_read or map_name, "level": level,
                     "party": party, "class": job, "exp": exp, "seconds": seconds, "mesos": mesos,
                     "mesos_seconds": meso_seconds}
                self._reseal(e, True)
                self.entries.append(e)
            e["updated"] = time.time()
            return e
        return self._change(add)

    def _add_run(self, r, seconds, mesos, meso_seconds):
        run = {"date": date.fromtimestamp(r["wall"]).isoformat(),
               "started": time.strftime("%Y-%m-%dT%H:%M", time.localtime(r["wall"])),
               "map": r["map"], "map_read": r["map_read"], "class": r["job"],
               "level_start": r["level_start"], "level_end": r["level"], "party": r["party"],
               "exp": r["exp"], "seconds": round(seconds, 1), "mesos": mesos,
               "mesos_seconds": round(meso_seconds, 1), "version": VERSION}
        run["seal"] = self._seal(run, _SEALED_RUN)
        self._change(lambda: self.runs.append(run))
        return run

    def canonical(self, name, also=()):
        """The name to log a map under: applies renames and fixes small misreads (matching
        the maps in the log, and any names in `also`, e.g. the map you were just on)."""
        name = self.aliases.get(name, name)
        best, score = name, 0.0
        for known in {e["map"] for e in self.entries} | set(self.aliases.values()) | {n for n in also if n}:
            if _numbers(known) == _numbers(name):
                s = SequenceMatcher(None, known.lower(), name.lower()).ratio()
                if s > score:
                    best, score = known, s
        return best if score >= 0.85 else name

    def rename(self, entry, new_name):
        """Renames a map (and remembers it for future reads). The map name as read stays on
        record (map_read), so renamed lines keep their seal."""
        old, key = entry["map"], self.key(entry)

        def apply():
            if old == "Unknown map":
                for e in self.entries:                       # just this one
                    if self.key(e) == key:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
            else:
                self.aliases[old] = new_name                 # and future reads of the old name
                for k, v in self.aliases.items():
                    if v == old:
                        self.aliases[k] = new_name
                for e in self.entries:
                    if e["map"] == old:
                        e.setdefault("map_read", old)
                        e["map"] = new_name
                for r in self.runs:
                    if r.get("map") == old:
                        r["map"] = new_name                  # (runs are sealed by map_read)
            merged = {}
            for e in self.entries:                           # combine entries that now match
                if self.key(e) in merged:
                    m = merged[self.key(e)]
                    ok = self.sealed(m) and self.sealed(e)
                    for field in ("exp", "seconds", "mesos", "mesos_seconds"):
                        if field in e:
                            m[field] = m.get(field, 0) + e[field]
                    m["updated"] = max(m.get("updated", 0), e.get("updated", 0))
                    if "seal" in m or "seal" in e:
                        self._reseal(m, ok)
                    both = notes_of([m, e])                   # a note on either line is kept
                    if both and both != m.get("note"):
                        m["note"] = both
                else:
                    merged[self.key(e)] = e
            self.entries = list(merged.values())

        self._change(apply)
        if old != "Unknown map" and self.block_map == old:
            self.block_map = new_name
        if old != "Unknown map" and self.run and self.run.get("map") == old:
            self.run["map"] = new_name

    def set_note(self, entries, text):
        """Writes a personal note on these log lines (the same one on each: e.g. a map's party
        sizes shown as one line). An empty note takes it off. Returns True if anything changed.

        A note only ever lives in this log file, on this PC. It isn't one of the sealed fields,
        so a line stays as trusted as it was, and it's never part of a run: what's shared with
        the stats site was made from the runs alone (sharing is gone from this local-only copy)."""
        keys = {self.key(e) for e in entries}
        text = clean_note(text)[:NOTE_MAX]

        def apply():
            changed = False
            for e in self.entries:
                if self.key(e) not in keys:
                    continue
                if text and e.get("note") != text:
                    e["note"], changed = text, True
                elif not text and "note" in e:
                    del e["note"]
                    changed = True
            return changed

        return self._change(apply)

    def delete(self, entry):
        key = self.key(entry)

        def apply():
            self.entries = [e for e in self.entries if self.key(e) != key]

        self._change(apply)

    def newest_first(self):
        return sorted(self.entries, key=lambda e: e.get("updated") or 0, reverse=True)

    def _back_up(self):
        """Before the first save of each day: a copy of the log as it was, in backups/ (the last
        few days are kept), so that whatever goes wrong, a recent copy of it all, notes
        included, is still there. To go back to one, close the overlay and copy it over
        exp_log.json."""
        try:
            if not self.path.exists():
                return
            folder = self.path.parent / "backups"
            copy = folder / f"exp_log-{date.today().isoformat()}.json"
            if copy.exists():
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))     # (a damaged file has its own copy)
            if not (data.get("entries") or data.get("runs")):
                return                                                  # nothing in it worth a backup
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, copy)
            for old in sorted(folder.glob("exp_log-*.json"))[:-BACKUPS_KEPT]:
                old.unlink()
        except Exception as e:
            log_error("backing up the EXP log", e)

    def save(self):
        self._back_up()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": LOG_VERSION, "install": self.install, "entries": self.entries,
                                       "aliases": self.aliases, "runs": self.runs}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self.text_path.write_text("".join(format_entry(e) + "\n" for e in self.newest_first()),
                                      encoding="utf-8")
        except Exception as e:
            log_error("saving the EXP log", e)


# --------------------------------------------------------------------------- UI helpers

def fmt_num(n):
    return "—" if n is None else f"{n:,.0f}"


def fmt_short(n):
    if n is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.2f}{suffix}"
    return f"{n:,.0f}"


def fmt_eta(seconds):
    """A time to wait, the way people say it: '36 min', '2 h 05 min'. None if too far off."""
    if seconds is None or seconds < 0 or seconds >= 100 * 3600:
        return None
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_clock(seconds):
    """A time to wait as a clock, like the tracker card shows it: '02:14:00' ('--:--:--' if unknown or too far)."""
    if seconds is None or seconds < 0 or seconds >= 100 * 3600:
        return "--:--:--"
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_dur(seconds):
    if seconds is None or seconds < 0 or seconds > 99 * 3600:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RegionSelector:
    """Full-screen translucent overlay; drag a box around the EXP bar text."""

    def __init__(self, root, on_done):
        self.on_done = on_done
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.origin = (v["left"], v["top"])
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, bg="black", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(v["width"] // 2, 60, fill="white", font=(PLATFORM.font("Segoe UI", "bold")[0], 20, PLATFORM.font("Segoe UI", "bold")[1]),
                                text="Drag a box around the EXP numbers  (Esc to cancel)")
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ffcc00", width=2)

    def _drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, *self.start, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, x1 = sorted((self.start[0], e.x))
        y0, y1 = sorted((self.start[1], e.y))
        if x1 - x0 < 10 or y1 - y0 < 5:
            self._finish(None)
            return
        self._finish({"left": self.origin[0] + x0, "top": self.origin[1] + y0,
                      "width": x1 - x0, "height": y1 - y0})

    def _finish(self, region):
        self.win.destroy()
        self.on_done(region)


# --------------------------------------------------------------------------- app

FIELD = RAISED              # inputs and buttons
# The window's fonts: (family, size at 100%, weight, underline). Settings > Size scales them all.
FONTS = {"small": ("Manrope", 9, "normal", False), "text": ("Manrope", 10, "normal", False),
         "link": ("Manrope SemiBold", 10, "normal", False), "row": ("Manrope", 10, "normal", False),
         "icon": ("Segoe UI", 12, "normal", False), "value": ("Manrope Bold", 11, "normal", False),
         "next": ("Manrope SemiBold", 11, "normal", False), "title": ("Manrope Bold", 13, "normal", False),
         "caps": ("Manrope SemiBold", 9, "normal", False), "strong": ("Manrope SemiBold", 10, "normal", False),
         "big": ("Manrope ExtraBold", 32, "normal", False)}
FONT_FILES = ("Manrope.ttf", "ManropeSemiBold.ttf", "ManropeBold.ttf", "ManropeExtraBold.ttf")
FALLBACK_FONT = {"Manrope": "Segoe UI", "Manrope SemiBold": "Segoe UI Semibold", "Manrope Bold": "Segoe UI Semibold",
                 "Manrope ExtraBold": "Segoe UI Semibold"}
LOG_HINT = "Click a line to write a note about it. Right-click to copy, rename or delete it."


class App:
    def __init__(self, root, templates):
        self.root = root
        self._fresh = not CONFIG_PATH.exists()          # a first install: it picks a compact shape
        self.cfg = {**DEFAULTS, **load_config()}
        mode = self.cfg.get("mode", "window")
        self.reader = ScreenReader(templates, mode, self.cfg.get("region"))
        saved = self.cfg.get("mesos_box")
        if isinstance(saved, list) and len(saved) == 6:
            self.reader.saved_mesos = tuple(saved)
        self.session = Session(self.cfg["gap_seconds"])
        self.tracker, self.meso = self.session.exp, self.session.meso
        self.party = 1
        self._party_seen = (None, 0)
        self.last_mesos = None
        self.rejected = 0
        self.started, self.cpu0 = time.monotonic(), time.process_time()
        self.recent, self.health = Recent(40, self.started), Health()      # for bug reports
        self.autolog = AutoLog(self.cfg["gap_seconds"])
        self.map_name = None
        self.in_town = False                # on a town map: nobody farms there, so nothing is logged
        self.level = None
        self.note = None
        self.last_logged = None
        self.skip_log = False               # "Don't log" is on: this run isn't logged
        self.job = None                     # the class, from the status bar
        self.map_read = None                # the map name exactly as read
        self.map_hidden, self.map_back_at = False, None     # the minimap's name out of sight for a few seconds
        self.found_at = None                # when the EXP bar was first read for this level
        self.level_up_at = None
        self.died_at = None
        self.log_open = False
        self.settings_win = None
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.last_capture = None

        # The typeface is bundled with the overlay and made available to this program only.
        self._own_fonts = PLATFORM.register_fonts([ASSETS / "fonts" / n for n in FONT_FILES])
        family_of = lambda f: f if self._own_fonts else FALLBACK_FONT.get(f, f)
        self.fonts = {name: tkfont.Font(root, underline=underline, **dict(zip(("family", "weight"),
                                                                               PLATFORM.font(family_of(family), weight))))
                      for name, (family, _, weight, underline) in FONTS.items()}
        pos = self.cfg.get("pos")
        self._dpi = (PLATFORM.screen_dpi(pos[0] + 40, pos[1] + 15) if pos else None) or root.winfo_fpixels("1i")
        self._apply_ui_scale()
        self.hotkey_note = None
        self.hotkeys = PLATFORM.create_hotkeys(lambda action: self.q.put(("hotkey", action)))
        self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        self.compact = False

        root.title(WINDOW_TITLE)
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass
        root.report_callback_exception = lambda kind, exc, tb: self._oops("a button or menu", exc)
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._apply_window_settings()
        self._restore_position()

        setup_ttk(root)
        PLATFORM.style_window(root, INK, TEXT_DIM)          # a title bar in the overlay's own colours
        self._trend, self._trend_at, self._start_text = deque(maxlen=60), 0.0, "Start"
        self.status = tk.StringVar()
        self.status.trace_add("write", lambda *_: self.view.show(message=self.status.get()) if hasattr(self, "view") else None)
        root.columnconfigure(1, weight=1)                 # the log gets any extra room
        root.rowconfigure(0, weight=1)
        self._build_view()
        self._show_skip_log()
        for delay in (0, 150, 800):                        # (Windows can reset it while the window is first shown)
            root.after(delay, lambda: PLATFORM.style_window(root, INK, TEXT_DIM))

        self._build_log_panel()
        self.hud = HudCard(root, PLATFORM, ASSETS, self.cfg, lambda: save_config(self.cfg))

        if not templates:
            self.status.set("Some of the program's files are missing, so EXP reading is less "
                            "reliable. Please download it again.")
        else:
            self.status.set("Looking for the MapleStory window..." if mode == "window"
                            else "Using your selected area.")

        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        root.after(200, self._pump)
        root.after(500, self._refresh)

    # ---- the main window
    def _build_view(self):
        """(Re)builds the main window at the current size: it's drawn smoothly for each screen's scaling
        and for Settings > Size, so a new size means new pictures."""
        if getattr(self, "view", None):
            self.view.destroy()
        self.theme = Theme(self.root, self._px_per_pt * 0.75, ASSETS / "fonts", self._own_fonts, PLATFORM.font)
        # The simple window: the numbers are on the tracker card over the game.
        callbacks = dict(settings=self.open_settings, start=self.toggle, reset=self.reset, repot=self.ring_repot,
                         move=self.toggle_hud_editing)
        self.view = self.main = SimpleView(self.root, self.theme, callbacks)
        self.view.grid(row=0, column=0, sticky="n")
        self._set_start(self._start_text)
        self.view.show(message=self.status.get())

    def _style_frame(self):
        """The title bar in the overlay's colours. (Windows resets it when the frame changes, e.g. when the
        log opens or it goes compact, so this runs again a moment after.)"""
        for delay in (30, 300):
            self.root.after(delay, lambda: PLATFORM.style_window(self.root, INK, TEXT_DIM))

    def _details_changed(self, is_open):
        self.cfg["details"] = is_open
        save_config(self.cfg)

    def _open_menu(self, anchor):
        PopupMenu(self.theme, anchor, [
            ("Follow the game" if self.reader.mode == "region" else "Select area", None, self.toggle_area),
            ("View capture", None, self.view_capture)])

    def _logging_clicked(self, on):
        if self.skip_log == on:                           # the switch and the state disagree: flip
            self.toggle_skip_log()

    # ---- log panel
    def _build_log_panel(self):
        p = self.log_panel = tk.Frame(self.root, bg=BG)
        tk.Label(p, text="EXP log", bg=BG, fg=FG, font=self.fonts["title"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.log_status = tk.StringVar()
        tk.Label(p, textvariable=self.log_status, bg=BG, fg=DIM, font=self.fonts["text"],
                 justify="left", anchor="w").grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 10))

        # A compact table: one row per day, map, level and class, solo or in a party (click a
        # party row for a row per party size). Drag the lines between the column titles to
        # resize a column; widths are remembered.
        cell, head = self.fonts["text"], self.fonts["small"]
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Log.Treeview", background=LIST_BG, fieldbackground=LIST_BG, foreground=FG,
                        font=cell, rowheight=int(cell.metrics("linespace") * 1.8), borderwidth=0)
        style.configure("Log.Treeview.Heading", background=BG, foreground=DIM, relief="flat",
                        borderwidth=0, font=self.fonts["caps"], padding=(0, 6))
        style.map("Log.Treeview", background=[("selected", SELECT)], foreground=[("selected", ACCENT)])
        style.map("Log.Treeview.Heading", background=[("active", BG)])
        # (key, title, widest usual value, alignment): starting widths are measured in the
        # real font, so nothing is cut off at any screen scaling.
        columns = (("date", "Date", "Sep 30", "w"), ("map", "Map", "The Swamp of Despair II", "w"),
                   ("lv", "Lv", "99", "center"), ("class", "Class", "Crossbowman", "w"),
                   ("party", "Party", "2–6ppl ▸", "center"),
                   ("time", "Time", "12h 30m", "center"),
                   ("exp", "EXP/hr", "888,888", "e"), ("mesos", "Mesos/hr", "888,888", "e"),
                   ("note", "Note", NOTE_MARK, "center"))
        # Saved widths are in points, so they look the same on screens with different scaling
        # (older versions saved pixels: those are used as they are, once).
        saved = {k: round(v * self._px_per_pt) for k, v in (self.cfg.get("log_columns_pt") or {}).items()
                 if type(v) in (int, float)} or self.cfg.get("log_columns") or {}
        pad = cell.measure("00")
        self._log_titles = {c[0]: c[1] for c in columns}
        tree = self.log_tree = ttk.Treeview(p, columns=[c[0] for c in columns], show="headings",
                                            style="Log.Treeview", height=11, selectmode="browse")
        for key, title, sample, anchor in columns:
            width = max(cell.measure(sample), head.measure(title)) + pad
            if isinstance(saved.get(key), int) and 10 <= saved[key] <= 2000:
                # (Party is never narrower than a merged party row: older versions' Party
                # column was sized for "6ppl".)
                width = max(saved[key], width) if key == "party" else saved[key]
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width, minwidth=pad, anchor=anchor, stretch=False)
        tree.tag_configure("odd", background=LIST_BG2)
        tree.tag_configure("part", background=LIST_PART, foreground="#b3b8c0")   # one party size
        tree.tag_configure("edited", foreground="#6b7079")      # changed outside the tracker
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(p, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        # Notes: a place for the player's own notes on the selected line. They're only ever kept
        # in the log file on this PC (never shared), and saved as they're typed.
        self._note_key = self._note_entries = self._note_job = None
        self._note_dirty, self._note_label = False, ""
        nb = tk.Frame(p, bg=BG)
        nb.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        nb.columnconfigure(0, weight=1)
        self.note_title = tk.Label(nb, text="", bg=BG, fg=DIM, font=head, anchor="w", justify="left")
        self.note_title.grid(row=0, column=0, sticky="we")
        self.note_text = tk.Text(nb, height=3, wrap="word", undo=True, maxundo=100, bg=LIST_BG, fg=FG,
                                 insertbackground=FG, relief="flat", font=cell, padx=8, pady=6,
                                 highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        self.note_text.grid(row=1, column=0, sticky="we", pady=(3, 0))
        self.note_text.bind("<<Modified>>", self._note_modified)
        self.note_text.bind("<FocusIn>", lambda e: self.hotkeys.pause(True))      # typing "!" isn't Shift+1
        self.note_text.bind("<FocusOut>", lambda e: (self.hotkeys.pause(False), self._note_flush()))
        self._note_show(None)
        self.log_hint = tk.StringVar(value=LOG_HINT)
        hint = tk.Label(p, textvariable=self.log_hint, bg=BG, fg=DIM, font=head, justify="left", anchor="w")
        hint.grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))

        def fit(e):
            hint.config(wraplength=max(120, e.width - 8))
            self.note_title.config(wraplength=max(120, e.width - 8))
        p.bind("<Configure>", fit)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(2, weight=1)
        tree.bind("<Button-3>", self._log_menu)
        tree.bind("<<TreeviewSelect>>", self._log_selected)
        tree.bind("<Button-1>", self._log_click)
        tree.bind("<<TreeviewOpen>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        tree.bind("<<TreeviewClose>>", lambda e: tree.after_idle(self._sync_group, tree.focus()))
        self.shown, self.groups, self.open_groups = {}, {}, set()
        self._fill_log()

    def toggle_log(self, remember=True):
        if self.log_open:
            self._note_flush()
            self._remember_log_size()                   # before it closes
        self.log_open = not self.log_open
        root = self.root
        if self.log_open:
            # With the log open the window can be resized (e.g. for long map names).
            self.log_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=(10, 8))
            root.update_idletasks()
            root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
            root.resizable(True, True)
            size = self.cfg.get("log_size")
            root.geometry(f"{size[0]}x{size[1]}" if size else "")
        else:
            self.log_panel.grid_remove()
            root.resizable(False, False)
            root.minsize(1, 1)
            root.geometry("")                           # back to just the tracker's size
        self.view.log_btn.set(kind="secondary" if self.log_open else "ghost")
        self._style_frame()
        if remember:
            self.cfg["log_open"] = self.log_open
            save_config(self.cfg)

    # ---- compact mode and size
    def set_compact(self, on):
        """Compact mode: just EXP/hr, when you'll level, the last 5 minutes and mesos/hr on one
        line, and Start/Pause, in a much smaller window. The Compact button (it says Full in
        compact mode), Shift+4, or double-clicking the big number switches it."""
        self.compact = self.cfg["compact"] = on
        if on and self.log_open:
            self.toggle_log(remember=False)                 # out of the way, but remembered
        self.view.set_compact(on, self.cfg["compact_shape"])
        self._style_frame()
        if not on and self.cfg.get("log_open"):
            self.toggle_log(remember=False)
        else:
            self.root.geometry("")
        save_config(self.cfg)

    def set_shape(self, shape):
        """Settings > Compact shape: Square, Wide or Bar. Only compact mode looks different."""
        if shape not in SHAPES:
            return
        self.cfg["compact_shape"] = shape
        if self.compact:
            self.view.set_compact(True, shape)
            self._style_frame()
            self.root.geometry("")
        save_config(self.cfg)

    def _apply_ui_scale(self):
        """Sizes every font by Settings > Size and for the screen the window is on (screens can
        have different Windows scaling), and the log's rows and columns with them."""
        old, self._px_per_pt = getattr(self, "_px_per_pt", None), self.cfg["ui_scale"] / 100 * self._dpi / 72
        for name, (_, size, _, _) in FONTS.items():
            # In pixels (negative), never under 9 so the smallest text stays readable.
            self.fonts[name].configure(size=-max(9, round(size * self._px_per_pt)))
        if old is not None and getattr(self, "view", None):
            self._build_view()
        if old is None or not hasattr(self, "log_tree"):    # at start, before the window is built
            return
        self._fit_settings()
        ttk.Style(self.root).configure("Log.Treeview", rowheight=int(self.fonts["text"].metrics("linespace") * 1.8))
        head, pad = self.fonts["caps"], self.fonts["text"].measure("00")
        for c in self.log_tree["columns"]:
            width = round(self.log_tree.column(c, "width") * self._px_per_pt / old)
            # (text doesn't shrink as far as the window does, so a column keeps room for its heading)
            self.log_tree.column(c, width=max(10, width, head.measure(self._log_titles.get(c, "")) + pad))
        self.cfg["log_size"] = None                         # a size from before may not fit any more
        if self.log_open:
            self.root.update_idletasks()
            self.root.minsize(self.main.winfo_reqwidth() + 150, self.main.winfo_reqheight())
        self.root.geometry("")

    def _fit_settings(self):
        """Keeps the Settings window as big as what's in it. (Moving a window to a screen with
        different scaling can leave it at a fixed size, which would cut its contents off.)"""
        w = self.settings_win
        if w and w.winfo_exists() and (w.winfo_width() < w.winfo_reqwidth() or w.winfo_height() < w.winfo_reqheight()):
            w.geometry("")

    def _check_screen(self):
        """Resizes the text when the window is moved to a screen with different Windows scaling
        (judged by its title bar, which stays put when the window changes size)."""
        r = self._window_rect()
        dpi = PLATFORM.screen_dpi(r[0] + 40, r[1] + 15) if r else None
        if dpi and dpi != self._dpi:
            self._dpi = dpi
            self._apply_ui_scale()

    def _fill_log(self):
        """The log, newest first. A map's party sizes on a day share a row (smallest to biggest
        party, all their time together); clicking it shows a row for each party size."""
        tree = self.log_tree
        self._note_flush()                                  # a note being typed is saved first,
        keep, rows = self._selected_key(), {}               # and the same line stays selected
        tree.delete(*tree.get_children())
        self.shown, self.groups = {}, {}
        edited = lambda e: ("edited",) if self.autolog.edited(e) else ()
        for i, (key, entries) in enumerate(party_groups(self.autolog.newest_first())):
            stripe = ("odd",) if i % 2 else ()
            if len(entries) == 1:
                iid = tree.insert("", "end", values=log_row(entries[0]), tags=stripe + edited(entries[0]))
                self.shown[iid] = entries[0]
                rows[self._line_key(entries[0])] = iid
                continue
            is_open = key in self.open_groups
            all_edited = all(self.autolog.edited(e) for e in entries)
            iid = tree.insert("", "end", values=group_row(entries, is_open), open=is_open,
                              tags=stripe + (("edited",) if all_edited else ()))
            self.groups[iid] = (key, entries)
            rows[key] = iid
            for e in entries:
                child = tree.insert(iid, "end", values=log_row(e, part=True), tags=("part",) + edited(e))
                self.shown[child] = e
                rows[self._line_key(e)] = child
        if keep in rows:
            tree.selection_set(rows[keep])
        else:
            self._note_show(None)                           # that line is gone (renamed, merged, deleted)
        self.log_hint.set(LOG_HINT if self.shown else "No logs yet. Press Start and gain EXP for 5 minutes.")

    # ---- personal notes on log lines
    @staticmethod
    def _line_key(e):
        return ("line",) + AutoLog.key(e)

    def _selected_entries(self):
        """(a key for the selected row, the log lines it stands for), or (None, None)."""
        sel = self.log_tree.selection()
        if not sel:
            return None, None
        if sel[0] in self.groups:
            return self.groups[sel[0]]
        e = self.shown.get(sel[0])
        return (self._line_key(e), [e]) if e else (None, None)

    def _selected_key(self):
        return self._selected_entries()[0]

    def _note_show(self, key, entries=None):
        """Puts the selected line's note in the box (or empties it, with nothing selected)."""
        t = self.note_text
        if key is not None and key == self._note_key:
            self._note_entries = entries                    # the same line (the log was refilled): leave
            return                                          # the text, and whoever is typing in it, alone
        self._note_key, self._note_entries, self._note_dirty = key, entries, False
        t.config(state="normal")
        t.delete("1.0", "end")
        if key is None:
            self._note_label = ""
            self.note_title.config(text="Notes: click a line to write one (they stay on this PC).")
            t.config(state="disabled", bg=LIST_PART)
        else:
            first, party = entries[0], entries[0].get("party")
            where = f"{log_row(first)[0]} \u00b7 {first['map']}" + (f" \u00b7 {party}ppl" if len(entries) == 1 and party and party > 1 else "")
            self._note_label = f"Note for {where} (kept on this PC only)"
            self.note_title.config(text=self._note_label)
            t.insert("1.0", notes_of(entries))
            t.config(bg=LIST_BG)
        t.edit_reset()
        t.edit_modified(False)

    def _note_modified(self, _event=None):
        t = self.note_text
        if not t.edit_modified():
            return
        t.edit_modified(False)
        if self._note_key is None:
            return
        over = len(t.get("1.0", "end-1c")) - NOTE_MAX
        if over > 0:                                        # too long: what doesn't fit isn't kept
            t.delete(f"end-1c-{over}c", "end-1c")
            self.note_title.config(text=f"A note can be {NOTE_MAX:,} characters at most.")
        else:
            self.note_title.config(text=self._note_label)
        self._note_dirty = True
        if self._note_job:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(900, self._note_flush)

    def _note_flush(self):
        """Saves the note being typed, if it was changed (as you type, when you click another
        line or leave the box, and when the log or the overlay closes)."""
        if self._note_job:
            self.root.after_cancel(self._note_job)
            self._note_job = None
        if not self._note_dirty or self._note_key is None:
            return
        self._note_dirty = False
        text = clean_note(self.note_text.get("1.0", "end-1c"))[:NOTE_MAX]
        try:
            self.autolog.set_note(self._note_entries, text)
        except Exception as e:
            log_error("saving a note", e)
            self._note_dirty = True
            self.note_title.config(text="Couldn't save the note (see errors.log).")
            return
        for e in self._note_entries:                        # the rows on screen
            if text:
                e["note"] = text
            else:
                e.pop("note", None)
        self._note_marks()
        self.note_title.config(text=f"{self._note_label} \u00b7 saved")

    def _note_marks(self):
        """The Note column: a mark on the lines that have a note."""
        for iid, (_, entries) in self.groups.items():
            self.log_tree.set(iid, "note", NOTE_MARK if notes_of(entries) else "")
        for iid, e in self.shown.items():
            self.log_tree.set(iid, "note", NOTE_MARK if note_of(e) else "")

    def _log_click(self, event):
        """Clicking a merged party row opens it (a row per party size) or closes it."""
        iid = self.log_tree.identify_row(event.y)
        if iid in self.groups and self.log_tree.identify_region(event.x, event.y) == "cell":
            self.log_tree.item(iid, open=not self.log_tree.item(iid, "open"))
            self._sync_group(iid)

    def _sync_group(self, iid):
        """After a party row opens or closes: its arrow, and remembering it for next time."""
        if iid not in self.groups:
            return
        key, entries = self.groups[iid]
        is_open = bool(self.log_tree.item(iid, "open"))
        self.open_groups.add(key) if is_open else self.open_groups.discard(key)
        self.log_tree.item(iid, values=group_row(entries, is_open))
        if self.log_tree.selection() == (iid,):
            self._log_selected(None)

    def _log_selected(self, _event):
        self._note_flush()                                  # the line left is saved first
        key, entries = self._selected_entries()
        self._note_show(key, entries)
        sel = self.log_tree.selection()
        group = self.groups.get(sel[0]) if sel else None
        if group:
            entries = group[1]
            sizes = ", ".join(str(e["party"]) for e in entries[:-1]) + f" and {entries[-1]['party']}"
            runs = sum(self.autolog.runs_done(e["map"], e["party"], e.get("class"), e["date"]) for e in entries)
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            what = "hide" if self.log_tree.item(sel[0], "open") else "see"
            self.log_hint.set(f"{entries[0]['map']}: parties of {sizes}, over {fmt_logged(sum(e['seconds'] for e in entries))} "
                              f"of non-stop EXP gain in all.{hours} Click it to {what} each party size. "
                              f"Right-click to copy, rename or delete.")
            return
        e = self.shown.get(sel[0]) if sel else None
        if e:
            if self.autolog.edited(e):
                self.log_hint.set("This line was changed outside the overlay (it no longer matches "
                                  "the overlay's seal), so it won't count toward the best-maps site.")
                return
            runs = self.autolog.runs_done(e["map"], e.get("party"), e.get("class"), e["date"])
            hours = (f" Includes {runs} full {RUN_SECONDS // 60}-minute run{'s' * (runs != 1)} of non-stop farming."
                     if runs else "")
            self.log_hint.set(f"{e['map']}: measured over {fmt_logged(e['seconds'])} of non-stop EXP gain."
                              f"{hours} Right-click to copy, rename or delete.")

    def _log_menu(self, event):
        """Right-click: copy, rename or delete a line (a merged party row: all its party sizes)."""
        iid = self.log_tree.identify_row(event.y)
        entries = self.groups[iid][1] if iid in self.groups else [self.shown[iid]] if iid in self.shown else None
        if not entries:
            return
        self.log_tree.selection_set(iid)
        many = len(entries) > 1
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy lines" if many else "Copy line", command=lambda: self._copy(entries))
        menu.add_command(label="Rename map...", command=lambda: self._rename(entries))
        menu.add_command(label=f"Delete all {len(entries)} party sizes" if many else "Delete entry",
                         command=lambda: self._delete(entries))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy(self, entries):
        text = "\n".join(format_entry(e) for e in entries)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log_hint.set("Copied: " + text if len(entries) == 1 else f"Copied {len(entries)} lines, one per party size.")

    def _rename(self, entries):
        self._note_flush()
        self.hotkeys.pause(True)                            # typing "!" in a name isn't Shift+1
        try:
            name = simpledialog.askstring("Rename map", "Map name:", initialvalue=entries[0]["map"],
                                          parent=self.root)
        finally:
            self.hotkeys.pause(False)
        if name and name.strip():
            for entry in entries:             # (one rename covers them all, unless it's "Unknown map")
                if entry["map"] != name.strip():
                    self.autolog.rename(entry, name.strip())
            if self.map_name:
                self.map_name = self.autolog.canonical(self.map_name)
            s = self.session                    # so a renamed map isn't taken as a new one
            s.map, s.here = (self.autolog.canonical(n) if n else n for n in (s.map, s.here))
            self._fill_log()

    def _delete(self, entries):
        self._note_flush()
        lines = "\n".join(format_entry(e) for e in entries)
        question = f"Delete these {len(entries)} log entries?" if len(entries) > 1 else "Delete this log entry?"
        if any(note_of(e) for e in entries):
            question += " The note" + ("s" if len(entries) > 1 else "") + " on " + ("them" if len(entries) > 1 else "it") + " will be deleted too."
        if messagebox.askyesno("Delete entry", f"{question}\n\n{lines}", parent=self.root):
            for entry in entries:
                self.autolog.delete(entry)
            self._fill_log()

    def _log_status_text(self, now):
        where = " · ".join(x for x in (self.map_name or "Map not read yet", f"Lv {self.level or '?'}",
                                       self.job, party_label(self.party)) if x)
        if self.skip_log:
            return f"{where}\nNot logging this run."
        if not self.session.on:
            return f"{where}\nPress Start to log EXP/hr here."
        if self.session.waiting:
            return f"{where}\nPaused. Logging carries on at your next EXP gain."
        left = self.autolog.seconds_left(now)
        if left is None:
            text = f"{where}\nWaiting for EXP gain..."
        else:
            text = f"{where}\nNext log in {int(left) // 60}:{int(left) % 60:02d} of non-stop EXP gain"
        if self.last_logged and now - self.last_logged < 8:
            text += "  (just logged)"
        run = self.autolog.run_progress(now)
        done = self.autolog.runs_done(self.map_name, self.party, self.job) if self.map_name else 0
        if run is not None or done:
            line = f"{RUN_SECONDS // 60}-min run: {int(run // 60) if run is not None else 0} of {RUN_SECONDS // 60} min"
            text += f"\n{line}" + (f" · {done} done here today" if done else "")
        return text

    # ---- settings
    def _apply_window_settings(self):
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.attributes("-alpha", 1 - min(MAX_TRANSPARENCY, max(0, int(self.cfg["transparency"]))) / 100)

    def _switch(self, parent, text, var):
        """An on/off switch tied to a BooleanVar."""
        t = Toggle(parent, self.theme, text, bool(var.get()), lambda on: var.set(on), BG)
        var.trace_add("write", lambda *_: t.set(bool(var.get())))
        return t

    def _section(self, parent, text, first=False):
        tk.Label(parent, text=text.upper(), bg=BG, fg=DIM, font=self.theme.font("semibold", 10)).pack(
            anchor="w", pady=(0 if first else self.theme.px(20), self.theme.px(8)))

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        th = self.theme
        px = th.px
        w = self.settings_win = tk.Toplevel(self.root)
        w.title("Settings")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.transient(self.root)
        r = self._window_rect()
        if r:                                    # over the overlay, so on the same screen (and scaling)
            w.geometry(f"+{r[0] + 40}+{r[1] + 40}")
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        body = tk.Frame(w, bg=BG)
        body.pack(padx=px(26), pady=px(22))
        text = dict(bg=BG, fg=FG, font=th.font("semibold", 12))
        dim = dict(bg=BG, fg=DIM, font=th.font("regular", 11))
        wrap = px(380)

        self._section(body, "Tracking", first=True)
        gap = tk.StringVar(value=str(self.cfg["gap_seconds"]))
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        tk.Label(line, text="Pause after no EXP for", **text).pack(side="left")
        gap_step = tk.Frame(line, bg=BG)
        gap_step.pack(side="right")

        def gap_by(by):
            try:
                now_ = int(gap.get())
            except ValueError:
                now_ = self.cfg["gap_seconds"]
            gap.set(str(min(300, max(5, now_ + by))))
        Button(gap_step, th, text="−", kind="secondary", width=38, height=34, radius=10, size=15,
               command=lambda: gap_by(-5), bg=BG).pack(side="left")
        tk.Label(gap_step, textvariable=gap, width=4, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
        Button(gap_step, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
               command=lambda: gap_by(5), bg=BG).pack(side="left")
        tk.Label(gap_step, text="s", bg=BG, fg=DIM, font=th.font("regular", 11)).pack(side="left", padx=(px(6), 0))

        self._section(body, "Window")
        transparency = tk.IntVar(value=int(self.cfg["transparency"]))
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        tk.Label(line, text="Transparency", **text).pack(side="left")
        percent = tk.Label(line, text=f"{transparency.get()}%", **dim)
        percent.pack(side="right")
        transparency.trace_add("write", lambda *_: percent.config(text=f"{int(float(transparency.get()))}%"))
        Slider(body, th, transparency, 0, MAX_TRANSPARENCY, 330, BG).pack(anchor="w", pady=(px(6), px(12)))
        size_line = tk.Frame(body, bg=BG)
        size_line.pack(fill="x")
        tk.Label(size_line, text="Size", **text).pack(side="left")
        # \u2212 100% +: steps through UI_SCALES only, so it stops at the smallest and biggest sizes.
        size = tk.StringVar(value=f"{self.cfg['ui_scale']}%")
        stepper = tk.Frame(size_line, bg=BG)
        stepper.pack(side="right")

        def nudge(by):
            i = UI_SCALES.index(int(size.get().rstrip("%"))) + by
            if 0 <= i < len(UI_SCALES):
                size.set(f"{UI_SCALES[i]}%")
            smaller.set(enabled=i > 0)
            bigger.set(enabled=i < len(UI_SCALES) - 1)
        smaller = Button(stepper, th, text="\u2212", kind="secondary", width=38, height=34, radius=10, size=15,
                         command=lambda: nudge(-1), bg=BG)
        smaller.pack(side="left")
        tk.Label(stepper, textvariable=size, width=5, bg=BG, fg=FG, font=th.font("bold", 12)).pack(side="left")
        bigger = Button(stepper, th, text="+", kind="secondary", width=38, height=34, radius=10, size=15,
                        command=lambda: nudge(1), bg=BG)
        bigger.pack(side="left")
        nudge(0)
        topmost = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        self._switch(body, "Always on top of other windows", topmost).pack(anchor="w", pady=(px(14), 0))

        self._section(body, "Tracker card over the game")
        hud = tk.BooleanVar(value=bool(self.cfg["hud"]))
        self._switch(body, "Show the EXP tracker card while you play", hud).pack(anchor="w")
        repot = tk.BooleanVar(value=bool(self.cfg["hud_repot"]))
        self._switch(body, "Show the repot row (Full layout)", repot).pack(anchor="w", pady=(px(6), 0))
        for title, options, key in (("Layout", {"minimal": "Minimal", "session": "Session", "full": "Full"}, "hud_layout"),
                                    ("Theme", HUD_THEMES, "hud_theme")):
            line = tk.Frame(body, bg=BG)
            line.pack(fill="x", pady=(px(10), 0))
            tk.Label(line, text=title, **text).pack(side="left")
            box = tk.Frame(line, bg=BG)
            box.pack(side="right")
            btns = {}

            def pick(k, key=key, btns=btns):
                self.hud.set_style(**{key.split("_")[1]: k})
                for kk, b in btns.items():
                    b.set(kind="primary" if kk == k else "secondary")
            for k, name in options.items():
                btns[k] = Button(box, th, text=name, width=70, height=32, radius=10, size=11, bg=BG,
                                 kind="primary" if k == self.cfg[key] else "secondary", command=lambda k=k, p=pick: p(k))
                btns[k].pack(side="left", padx=(px(5), 0))
        Button(body, th, text=f"Move / resize the card  ({self.cfg['hotkeys'].get('edit') or 'no hotkey'})",
               kind="secondary", width=260, height=34, radius=10, size=12, bg=BG,
               command=self.toggle_hud_editing).pack(anchor="w", pady=(px(10), 0))
        tk.Label(body, text="Local only: this copy never goes online. Nothing it reads or saves leaves this PC.",
                 wraplength=wrap, justify="left", **dim).pack(anchor="w", pady=(px(8), 0))

        def apply(*_):
            try:
                seconds = int(gap.get())
            except ValueError:
                seconds = 0
            if 1 <= seconds <= 3600:
                self.cfg["gap_seconds"] = self.autolog.gap = self.session.gap = seconds
            self.cfg["transparency"] = min(MAX_TRANSPARENCY, max(0, int(float(transparency.get()))))
            self.cfg["topmost"] = bool(topmost.get())
            self.cfg["hud"] = bool(hud.get())
            if bool(repot.get()) != self.hud.repot:
                self.hud.set_repot(repot.get())
            self._apply_window_settings()
            w.attributes("-topmost", self.cfg["topmost"])
            scale = int(size.get().rstrip("%"))
            if scale in UI_SCALES and scale != self.cfg["ui_scale"]:
                self.cfg["ui_scale"] = scale
                self._apply_ui_scale()
                self.root.after(60, self._reopen_settings)        # (drawn again at the new size)
            save_config(self.cfg)

        for var in (gap, transparency, topmost, hud, repot, size):
            var.trace_add("write", apply)
        self._hotkey_settings(body)
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(px(22), px(4)))
        Button(buttons, th, text="Close", kind="primary", width=96, height=38, radius=11, size=12,
               command=w.destroy, bg=BG).pack(side="right")
        Button(buttons, th, text="Data folder", kind="secondary", width=104, height=38, radius=11, size=12,
               command=self.open_data_folder, bg=BG).pack(side="left")
        Button(buttons, th, text="Report a bug", kind="secondary", width=116, height=38, radius=11, size=12,
               command=lambda: self.report_bug(w), bg=BG).pack(side="left", padx=(px(8), 0))
        tk.Label(body, text=f"Your settings and repot are saved in {DATA_DIR}", wraplength=wrap, justify="left",
                 bg=BG, fg=FAINT, font=th.font("regular", 10)).pack(anchor="w", pady=(px(8), 0))
        tk.Label(body, text=f"{WINDOW_TITLE} v{VERSION}, local-only edition.", bg=BG, fg=FAINT,
                 font=th.font("regular", 10)).pack(anchor="w", pady=(px(2), 0))

    def _reopen_settings(self):
        """After a size change: Settings again, in the same place, drawn for the new size."""
        old = self.settings_win
        if old and old.winfo_exists():
            where = f"+{old.winfo_x()}+{old.winfo_y()}"
            old.destroy()
            self.open_settings()
            self.settings_win.geometry(where)

    def _hotkey_settings(self, body):
        """Settings' Hotkeys section: on or off, and each one changeable (click it, then press
        the keys; Esc keeps it as it was, Backspace turns it off)."""
        th, w = self.theme, body.winfo_toplevel()
        px = th.px
        self._section(body, "Hotkeys")
        box = tk.Frame(body, bg=BG)
        box.pack(anchor="w", fill="x")
        if not self.hotkeys.supported:
            tk.Label(box, text=f"Hotkeys aren't available on {PLATFORM.name} yet: use the buttons.",
                     bg=BG, fg=DIM, font=th.font("regular", 11)).pack(anchor="w")
            return
        on = tk.BooleanVar(value=self.cfg["hotkeys_on"])
        self._switch(box, "Hotkeys, while MapleStory or the overlay is in front", on).pack(anchor="w", pady=(0, px(8)))
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w", padx=(px(2), 0))
        buttons, notes, choosing = {}, {}, {"action": None}
        idle_hint = "Click one, then press the keys you want. Backspace turns it off."
        hint = tk.StringVar(value=idle_hint)

        def show():
            for action, b in buttons.items():
                key = self.cfg["hotkeys"][action]
                if action == choosing["action"]:
                    b.set(text="Press keys...", kind="primary")
                else:
                    b.set(text=key or "Off", kind="secondary" if key else "ghost")
                notes[action].config(text=self.hotkeys.problems.get(action, ""))

        def stop():
            """Nobody is waiting for keys: hotkeys work again, every button shows its key."""
            choosing["action"] = None
            hint.set(idle_hint)
            self.hotkeys.pause(False)
            show()

        def watch():
            """Clicking away from Settings (into the game, say) ends the wait, so the hotkeys never stay off."""
            if choosing["action"] and w.winfo_exists():
                if w.focus_displayof() is None:
                    stop()
                else:
                    w.after(400, watch)

        def choose(action):
            if choosing["action"] == action:         # clicking the waiting one again cancels it
                stop()
                return
            first = choosing["action"] is None
            choosing["action"] = action              # (one at a time: another that was waiting goes back to its key)
            self.hotkeys.pause(True)                 # so the keys reach this window
            hint.set(f"Press the keys for {HOTKEY_ACTIONS[action]}. Click it again or press Esc to cancel.")
            show()
            w.focus_force()
            if first:
                w.after(400, watch)

        def done(action, key):
            choosing["action"] = None
            hint.set(idle_hint)
            if key:
                for other, k in self.cfg["hotkeys"].items():
                    if k == key and other != action:
                        self.cfg["hotkeys"][other] = ""      # one key, one job
            self.cfg["hotkeys"][action] = key
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
            self.hotkeys.pause(False)
            show()

        def pressed(e):
            action = choosing["action"]
            if not action or e.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                                          "Win_L", "Win_R", "Caps_Lock", "Num_Lock"):
                return None
            if e.keysym == "Escape":
                done(action, self.cfg["hotkeys"][action])
            elif e.keysym in ("BackSpace", "Delete"):
                done(action, "")
            else:
                key = hotkey_text(bool(e.state & 0x4), bool(e.state & 0x20000), bool(e.state & 0x1), e.keycode)
                if not key:
                    hint.set("Use Ctrl, Alt or Shift with a number or letter, or an F key.")
                    return "break"
                done(action, key)
            return "break"

        for i, (action, name) in enumerate(HOTKEY_ACTIONS.items()):
            tk.Label(grid, text=name, bg=BG, fg=FG, font=th.font("regular", 12), width=14, anchor="w").grid(
                row=i, column=0, sticky="w", pady=px(3))
            buttons[action] = Button(grid, th, text="", kind="secondary", width=124, height=34, radius=10, size=12,
                                     command=lambda a=action: choose(a), bg=BG)
            buttons[action].grid(row=i, column=1, sticky="w", pady=px(3))
            notes[action] = tk.Label(grid, bg=BG, fg="#ff8a7a", font=th.font("regular", 10))
            notes[action].grid(row=i, column=2, sticky="w", padx=(px(8), 0))
        tk.Label(box, textvariable=hint, bg=BG, fg=FAINT, font=th.font("regular", 10)).pack(anchor="w", pady=(px(6), 0))
        w.bind("<KeyPress>", pressed)
        w.bind("<Destroy>", lambda e: e.widget is w and self.hotkeys.pause(False))
        self._hotkey_ui = {"buttons": buttons, "choose": choose, "choosing": choosing, "pressed": pressed, "on": on}   # (tests)

        def switch(*_):
            if choosing["action"]:
                stop()
            self.cfg["hotkeys_on"] = bool(on.get())
            save_config(self.cfg)
            self.hotkeys.set(self.cfg["hotkeys"], self.cfg["hotkeys_on"])
        on.trace_add("write", switch)
        show()

    def open_data_folder(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            PLATFORM.open_folder(DATA_DIR)
        except Exception as e:
            self._oops("opening the data folder", e)

    def _restore_position(self):
        pos = self.cfg.get("pos")
        if not pos:
            return
        with mss.mss() as sct:
            screens = sct.monitors[1:]
        # Only if its top-left is still on a screen (not one that was unplugged, nor the
        # gap between two screens of different sizes).
        if any(v["left"] <= pos[0] < v["left"] + v["width"] - 50
               and v["top"] <= pos[1] < v["top"] + v["height"] - 50 for v in screens):
            self.root.geometry(f"+{pos[0]}+{pos[1]}")

    def _window_rect(self):
        """This app's window on screen (physical pixels), or None."""
        try:
            return PLATFORM.own_window_rect(self.root)
        except Exception:
            return None

    # ---- background capture loop
    def _worker(self):
        with mss.mss() as sct:
            while not self.stop_evt.is_set():
                t0 = time.monotonic()
                try:
                    self.q.put(("reading", self.reader.read(sct)))
                    self.health.finished(t0)
                except Exception as e:
                    log_error("reading the screen", e)
                    self.health.failed(e)
                    self.q.put(("error", f"{type(e).__name__}: {e}"))
                self.stop_evt.wait(max(0.1, POLL_SECONDS - (time.monotonic() - t0)))
            self.health.alive = False

    def _first_run(self):
        """Once per install or update: a new install picks its compact shape, an update shows what's new."""
        seen = self.cfg.get("seen_version", "")
        newer = not seen or version_key(VERSION) > version_key(seen)
        if newer:
            self.cfg["seen_version"] = VERSION                 # (first, so a crash in a window can't repeat it)
            save_config(self.cfg)
        if self._fresh and newer:
            self._shape_picker(None)
        elif newer and WHATS_NEW.get(VERSION):
            self._whats_new(WHATS_NEW[VERSION], None)

    def _small_window(self, title, then):
        """A window for the one-time messages: like the other dialogs, and `then` runs when it closes."""
        th = self.theme
        w = tk.Toplevel(self.root)
        w.title(title)
        w.configure(bg=BG, padx=th.px(26), pady=th.px(22))
        w.resizable(False, False)
        w.transient(self.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            w.after(delay, lambda: PLATFORM.style_window(w, INK, TEXT_DIM) if w.winfo_exists() else None)
        w.bind("<Destroy>", lambda e: then and e.widget is w and self.root.after(100, then))
        return w

    def _shape_photo(self, shape, width):
        """The picture of a compact shape in the picker (assets/shape_<name>.png), `width` logical pixels wide."""
        try:
            img = Image.open(ASSETS / f"shape_{shape}.png").convert("RGBA")
            w = self.theme.px(width)
            return ImageTk.PhotoImage(img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS), master=self.root)
        except Exception:
            return None

    def _shape_picker(self, then):
        """A new install chooses how compact mode looks. Closing the window keeps Square."""
        th = self.theme
        w = self._small_window("Choose a compact look", then)
        tk.Label(w, text="Pick a compact look", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text="Compact mode is the small version of the overlay (the compact icon, or Shift+4). "
                         "Choose how it looks. You can change it any time in Settings.", bg=BG, fg=DIM,
                 font=th.font("regular", 12), wraplength=th.px(500), justify="left").pack(anchor="w", pady=(2, th.px(14)))
        widths = {"square": 150, "wide": 250, "bar": 300}

        def choose(shape):
            self.set_shape(shape)
            w.destroy()
        for shape in SHAPES:
            card = tk.Frame(w, bg=LIST_BG, highlightthickness=1, highlightbackground=LINE)
            card.pack(fill="x", pady=(0, th.px(10)))
            photo = self._shape_photo(shape, widths[shape])
            if photo:
                pic = tk.Label(card, image=photo, bg=LIST_BG, cursor="hand2")
                pic.image = photo
                pic.pack(side="left", padx=th.px(12), pady=th.px(12))
                pic.bind("<Button-1>", lambda e, s=shape: choose(s))
            side = tk.Frame(card, bg=LIST_BG)
            side.pack(side="left", fill="both", expand=True, padx=(0, th.px(12)), pady=th.px(12))
            tk.Label(side, text=SHAPE_LABELS[shape], bg=LIST_BG, fg=FG, font=th.font("bold", 14)).pack(anchor="w")
            tk.Label(side, text=SHAPE_BLURBS[shape], bg=LIST_BG, fg=DIM, font=th.font("regular", 11),
                     wraplength=th.px(190), justify="left").pack(anchor="w", pady=(2, th.px(8)))
            Button(side, th, text=f"Use {SHAPE_LABELS[shape]}", kind="primary" if shape == "square" else "secondary",
                   width=120, height=34, radius=10, size=12, command=lambda s=shape: choose(s), bg=LIST_BG).pack(anchor="w")
        w.update_idletasks()

    def _whats_new(self, notes, then):
        """After an update: a few plain lines on what changed. Shown once per version."""
        th = self.theme
        w = self._small_window("What's new", then)
        tk.Label(w, text=f"What's new in v{VERSION.split('-')[0]}", bg=BG, fg=ACCENT, font=th.font("extra", 20)).pack(anchor="w")
        tk.Label(w, text="Thanks for updating. Your log, notes and settings are kept.", bg=BG, fg=DIM,
                 font=th.font("regular", 12)).pack(anchor="w", pady=(2, th.px(12)))
        for line in notes:
            row = tk.Frame(w, bg=BG)
            row.pack(fill="x", pady=(0, th.px(6)))
            tk.Label(row, text="\u2022", bg=BG, fg=ACCENT, font=th.font("bold", 12)).pack(side="left", anchor="n")
            tk.Label(row, text=line, bg=BG, fg=FG, font=th.font("regular", 12), wraplength=th.px(430), justify="left").pack(
                side="left", padx=(th.px(8), 0))
        Button(w, th, text="Got it", kind="primary", width=110, height=40, radius=12, size=13, command=w.destroy,
               bg=BG).pack(anchor="e", pady=(th.px(10), 0))

    def _oops(self, what, exc):
        """Something unexpected went wrong: note it in errors.log and keep going."""
        log_error(what, exc)
        self.status.set("Something went wrong, but the overlay keeps running. Details are in "
                        "errors.log (Settings > Open data folder).")

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "error":
                    self.recent.add(f"Error reading the screen: {payload}")
                    self.status.set("Something went wrong reading the screen. Trying again... "
                                    "(details are in errors.log: Settings > Open data folder)")
                elif kind == "hotkey":
                    self._hotkey(payload)
                else:
                    self._handle(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self._oops("handling a reading", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(200, self._pump)

    def _handle(self, r):
        t, now = self.tracker, time.monotonic()
        if r.image is not None:
            self.last_capture = r
        if r.mesos_image is not None:
            self.last_mesos = r
        if r.mesos_at:                                  # remember where the mesos box is
            self.cfg["mesos_box"] = list(r.mesos_at)
            self.reader.saved_mesos = tuple(r.mesos_at)
            save_config(self.cfg)
        if r.mesos is not None:
            self.meso.observe(r.mesos, now, t.elapsed(), t.running)
        if r.party is not None:                         # believe a new party size once seen twice
            seen, n = self._party_seen
            n = n + 1 if r.party == seen else 1
            self._party_seen = (r.party, n)
            if n >= 2:
                self.party = r.party
        if self.reader.job:
            self.job = self.reader.job
        if r.map:
            self.map_read = r.map                       # as read (kept on record if renamed)
            # A slightly misread name of the map you're on still counts as that map.
            self.map_name = self.autolog.canonical(r.map, also=(self.map_name, self.session.map))
            waiting = self.session.waiting
            self.session.map_seen(self.map_name)
            self._after_pause_check(waiting)
        self.in_town = is_town(self.map_name)
        hidden = self._name_hidden()
        if hidden != self.map_hidden:
            self.map_hidden = hidden
            if not hidden:
                self.map_back_at = now          # (says "back" for a few seconds)
        if (self.session.on and self.map_name and self.reader.mode == "window" and tesseract_ok
                and self.reader.map_blind_seconds() > MAP_BLIND_SECONDS):
            self.session.map_blind()
        self.note = r.note
        if r.candidates:
            if self.found_at is None or t.req is not None:
                self.found_at = now
            before = t.gained
            ok, note = self.session.observe(r.candidates, r.confident, now)
            self.rejected = 0 if ok else self.rejected + 1
            if self.rejected >= 10:                 # keeps not adding up: find the EXP bar again
                self.reader.box, self.rejected = None, 0
            if t.running and t.gained != before and not self.skip_log:
                self.autolog.gain(now, t.gained - before)
            if note == "died":
                self.died_at = now
            if note == "LEVEL UP!":
                self.level_up_at = now
                if self.level:
                    self.level += 1             # until the new level's EXP confirms it
            status = self._status_for(note)
        else:
            status = r.where
        if self.in_town:
            status += "\nIn a town: nothing is logged here."
        if self.map_hidden and self.tracker.running:
            status += (f"\nCan't see the minimap's name. Still tracking; it pauses if this lasts "
                       f"{MAP_BLIND_SECONDS} seconds.")
        elif self.map_back_at and now - self.map_back_at < 6 and self.session.on:
            status += "\nThe minimap's name is back."
        if self.note:
            status += f"\n{self.note}"
        self.status.set(status)
        self.recent.add(status)
        self.level = level_for_req(t.req) or self.level
        self._log_tick(now)

    def _status_for(self, note):
        """What the status line says while the EXP bar is being read (details: View capture)."""
        t = self.tracker
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return f"Level up! You're now Lv {self.level}." if self.level else "Level up!"
        if self.hotkey_note and time.monotonic() - self.hotkey_note[1] < 4:
            return self.hotkey_note[0]
        if self.died_at and time.monotonic() - self.died_at < 30 and t.last_loss:
            return f"You died and lost {t.last_loss:.1f}% EXP. That isn't taken off your EXP/hr."
        if self.rejected >= 5:
            return "Having trouble reading the EXP bar clearly. View capture shows what it sees."
        if t.req is None:
            # Normally the level is known within seconds; if not, it needs an EXP gain.
            if time.monotonic() - self.found_at < 8:
                return "Found your EXP bar. Working out your level..."
            return "Found your EXP bar. Your level shows after your next EXP gain."
        s = self.session
        if s.waiting == "idle":
            return (f"Paused: no EXP for {s.gap} seconds, so this time doesn't count. "
                    "It carries on at your next EXP gain.")
        if s.waiting == "map":
            return ("Paused: you left the map. It carries on at your next EXP gain "
                    "(from zero if that's on a different map).")
        if s.waiting == "blind":
            return ("Paused: the minimap's name can't be seen, so it can't tell which map you're on. "
                    "It carries on when the minimap is back.")
        if t.running:
            return "Tracking your EXP. Not logging this run." if self.skip_log else "Tracking your EXP."
        if t.elapsed() > 0:
            return "Paused. Press Resume to carry on."
        return "Ready. Press Start when you begin farming."

    def _name_hidden(self):
        """True while tracking and the minimap's name hasn't been seen for a few seconds."""
        return bool(self.session.on and self.map_name and self.reader.mode == "window" and tesseract_ok
                    and self.reader.map_blind_seconds() > MAP_HIDDEN_SECONDS)

    def _log_tick(self, now):
        if not LOGGING or not self.tracker.running or self.skip_log or self.in_town:
            return                                  # (the EXP log is off in this simplified copy)
        m = self.meso
        mesos = (m.gained, m.seen_at) if m.value is not None and now - m.seen_at <= 30 else None
        entry = self.autolog.tick(now, self.map_name, self.level, self.party, mesos, self.job, self.map_read,
                                  hidden=self.map_hidden)
        if entry:
            self.last_logged = now
            self._fill_log()

    def _refresh(self):
        try:
            self._check_screen()
            self._fit_settings()
            self._update_display()
        except Exception as e:
            self._oops("updating the window", e)
        finally:
            if not self.stop_evt.is_set():
                self.root.after(500, self._refresh)

    def _state(self):
        """A word for the status pill: where things stand."""
        t, sess = self.tracker, self.session
        if self.reader.mode == "window" and not self.reader.hwnd:
            return "waiting", "Looking for the game"
        if self.rejected >= 5:
            return "alert", "Can't read the EXP bar"
        if t.req is None:
            return "waiting", "Reading your EXP bar"
        if self.level_up_at and time.monotonic() - self.level_up_at < 6:
            return "tracking", "Level up!"
        if sess.waiting:
            return "paused", "Paused"
        if t.running:
            return "tracking", f"Tracking \u00b7 Lv {self.level}" if self.level else "Tracking"
        return ("paused", "Paused") if t.elapsed() > 0 else ("ready", "Ready")

    def _update_display(self):
        t, now = self.tracker, time.monotonic()
        rate, rolling = t.rate(), t.rolling_rate()
        shown = (t.exp, t.pct) if t.exp is not None else t.last_read
        best_rate = rolling or rate
        level = level_for_req(t.req) or self.level
        eta = fmt_eta((t.req - t.exp) / best_rate * 3600) if (
            t.req and t.exp is not None and best_rate and best_rate > 0) else None
        if t.running and rolling and now - self._trend_at >= 15:      # a point on the trend line every 15 s
            self._trend.append(rolling)
            self._trend_at = now
        m, el = self.meso, t.elapsed()
        if m.value is None:
            mesos = "move this window" if self.reader.mesos_covered else "open inventory"
        else:
            mesos = fmt_num(m.value) + (f"  (+{fmt_short(m.gained)})" if m.gained > 0 else "")
        state, state_text = self._state()
        self.view.show(
            rate=fmt_num(rate) if rate is not None else None,
            delta=(rolling / rate - 1) if rate and rolling else None,
            spark=list(self._trend), pct=shown[1] if shown else None,
            eta=f"Lv {level + 1} in {eta}" if level and eta else f"Lv {level}" if level else "",
            level=level or None, eta_time=eta or "",
            mesos=fmt_num(m.per_hour(el)),
            tiles={"Last 5 min": fmt_num(rolling), "Mesos / hr": fmt_num(m.per_hour(el)), "Elapsed": fmt_dur(el)},
            details={"% / hr": f"{rate / t.req * 100:.2f}%" if rate and t.req else "\u2014",
                     "Gained": f"{fmt_short(t.gained)}{f'  (+{t.levels} lvl)' if t.levels else ''}",
                     "Current": f"{fmt_num(shown[0])} [{shown[1]:.2f}%]" if shown else "\u2014",
                     "Mesos": mesos, "Mesos / 5 min": fmt_num(m.per_5min(el))},
            state=state, state_text=state_text)
        self._update_hud(rate, rolling, level, shown, state_text)
        waiting = self.session.waiting
        self.session.tick(now)
        if self.session.waiting != waiting:             # just paused by itself: say why now
            self._after_pause_check(waiting)
            self.status.set(self._status_for(""))
        self._log_tick(now)
        self.log_status.set(self._log_status_text(now))
        self.reader.own_rect = self._window_rect()

    def _update_hud(self, rate, rolling, level, shown, state_text):
        """The EXP tracker card over the game (hud.py): shown while the game (or this overlay) is in
        front and its window is known, or while it's being moved."""
        t = self.tracker
        if getattr(self, "_repot_pending", False) and self.meso.value is not None:
            self.ring_repot()                              # (rung while the mesos couldn't be read: now they can)
        # The slime hops when EXP comes in (three times for a level up): only for what comes in while it's shown.
        seen_gain, seen_level_up = getattr(self, "_hud_seen", (t.gained, self.level_up_at))
        self._hud_seen = (t.gained, self.level_up_at)
        rect = self.reader.last_rect if self.reader.hwnd else None
        if not (rect and (self.hud.editing or (self.cfg["hud"] and PLATFORM.game_in_front()))):
            self.hud.hide()
            self.reader.hud_rects = ()
            return
        if self.hud.window.shown:
            if self.level_up_at and self.level_up_at != seen_level_up:
                self.hud.celebrate(level_up=True)
            elif t.gained > seen_gain:
                self.hud.celebrate(exp=t.gained - seen_gain)
        m, now = self.meso, time.monotonic()
        el = t.elapsed()
        # The first 5 minutes of tracking calibrate: a rate from a minute or two swings wildly, so EXP/hr,
        # mesos/hr and the time to level wait until there's enough to go on (a countdown shows how long).
        calibrating = HUD_CALIBRATION - el if (t.running or el > 0) and el < HUD_CALIBRATION else None
        best = rolling or rate
        to_level = (t.req - t.exp) / best * 3600 if t.req and t.exp is not None and best and best > 0 else None
        recent = t.recent_gain()
        per_hr = m.per_hour(el)
        mesos_known = m.value is not None
        # With the inventory closed, the mesos can't be read: what's been picked up since is guessed at the
        # rate so far, and shown with a "~" until the inventory is open again.
        guess = m.guess_since_seen(el) if m.unseen(el) > MESOS_UNSEEN else 0.0
        est = "~" if guess else ""
        if self.level_up_at and now - self.level_up_at < 6:
            slime = "sparkle"
        elif self.died_at and now - self.died_at < 30:
            slime = "dizzy"
        elif not self.reader.hwnd or self.rejected >= 5 or t.req is None:
            slime = "confused"
        elif self.session.waiting or (not t.running and el > 0):
            slime = "sleepy"
        elif not t.running:
            slime = "calm"
        else:
            slime = "happy"
        content = {
            "level": level, "next_level": level + 1 if level else None,
            "fill": shown[1] / 100 if shown else 0.0, "status": None if shown and t.req else state_text,
            "exp": fmt_num(shown[0]) if shown else "?", "req": fmt_num(t.req) if t.req else "?",
            "pct": f"{shown[1]:.1f}%" if shown else "?",
            "time": fmt_clock(None if calibrating else to_level), "slime": slime,
            "calibrating": calibrating,
            "last5": f"+{fmt_num(recent)}" if recent else "+0" if recent == 0 else "—",
            "rate": "Calibrating…" if calibrating else fmt_num(rate) if rate is not None else "—",
            "session": f"+{fmt_num(t.gained)}" if t.gained else "+0",
            "mesos_rate": "Calibrating…" if calibrating else fmt_num(per_hr) if per_hr is not None else "—",
            "mesos_net": f"{est}{m.gained + guess:+,.0f}" if mesos_known else "—",
            "mesos_gained": f"{est}+{fmt_num(m.earned + guess)}" if mesos_known else "—",
            "mesos_spent": f"-{fmt_num(m.spent)}" if mesos_known and m.spent else "0" if mesos_known else "—"}
        content.update(self._repot_content(guess))
        self.reader.hud_rects = (self.hud.place(rect),)  # (first, so the reader never sees it)
        self.hud.show(rect, content)

    # ---- the repot bell
    #
    # Ring it (Shift+3, or the bell button) when a shop trip is done: potions bought, loot sold. It
    # remembers the mesos you have then; the card shows how many mesos you've made since (live), and
    # what the last trip made: the mesos from one ring to the next, buying and selling included. It's
    # worked out from the mesos you have, not from the gains and losses seen on the way, so big shop
    # visits count exactly. Kept in the settings, so it carries on after the overlay restarts.

    def ring_repot(self):
        if self.meso.value is None:
            self._repot_pending = True
            self.status.set("Repot bell: open your inventory so it can read your mesos. It rings as soon as it can.")
            return
        self._repot_pending = False
        now_mesos = self.meso.value
        last = self.cfg.get("repot") if isinstance(self.cfg.get("repot"), dict) else {}
        profit = now_mesos - last["mesos"] if isinstance(last.get("mesos"), int) else None
        self.cfg["repot"] = {"mesos": now_mesos, "at": time.time(), "trip": profit if profit is not None else last.get("trip"),
                             "trips": int(last.get("trips") or 0) + 1}
        save_config(self.cfg)
        self.hud.ring(profit)
        self.status.set(f"Ding! Repot saved at {fmt_num(now_mesos)} mesos."
                        + (f" That trip made {profit:+,} mesos." if profit is not None else
                           " Ring it again after your next trip to see what the trip made."))

    def _repot_content(self, guess=0.0):
        """What the card's repot row shows (guess: mesos probably picked up since the inventory was closed)."""
        r = self.cfg.get("repot") if isinstance(self.cfg.get("repot"), dict) else {}
        if not isinstance(r.get("mesos"), int):
            return {"repot_at": "Ring the bell", "repot_since": "-", "repot_trip": "-", "repot_time": "-"}
        now = self.meso.value
        trip = r.get("trip")
        since = time.time() - r["at"] if isinstance(r.get("at"), (int, float)) else None
        return {"repot_at": fmt_num(r["mesos"]),
                "repot_since": (f"{'~' if guess else ''}{now + guess - r['mesos']:+,.0f}" if now is not None
                                else "Open your inventory"),
                "repot_trip": f"{trip:+,}" if isinstance(trip, int) else "-",
                "repot_time": fmt_dur(since) if since is not None and since >= 0 else "-"}

    def toggle_hud_editing(self):
        """Edit mode for the tracker card: drag it, resize it, pick its layout and theme (Shift+5 by default)."""
        if not self.reader.last_rect or not self.reader.hwnd:
            self.status.set("Start MapleStory first: the tracker card is placed over the game.")
            return
        self.hud.set_editing(not self.hud.editing)
        key = self.cfg["hotkeys"].get("edit") or "Settings > Move / resize"
        self.status.set(f"Moving the tracker card: drag it to move it, its right edge to widen it, its bottom edge to "
                        f"make it bigger, right-click for layout, theme and size. {key} locks it."
                        if self.hud.editing else "Tracker card locked in place.")
        self._update_display()

    # ---- buttons
    def _hotkey(self, action):
        if action == "start":
            self.toggle()
            done = "started" if self.session.on else "paused"
        elif action == "reset":
            self.reset()
            done = "reset"
        elif action == "repot":
            self.ring_repot()
            return
        elif action == "edit":
            self.toggle_hud_editing()
            return
        else:
            return
        self.hotkey_note = (f"{self.cfg['hotkeys'][action]}: {done}.", time.monotonic())
        self.status.set(self.hotkey_note[0])

    def toggle(self):
        if self.session.on:                     # (also while it's paused by itself)
            self.recent.add("You paused")
            self.session.pause()
            self._set_start("Resume")
        else:
            self.recent.add("You pressed Start")
            self.session.start(time.monotonic())
            self.reader.request_map()           # read the map name now
            self._set_start("Pause")
        self.autolog.reset_clock()
        if self.tracker.exp is not None:
            self.status.set(self._status_for(""))   # right away, not at the next read

    def _set_start(self, text):
        """The Start button: gold when pressing it starts something, quiet while it's running."""
        self._start_text = text
        self.view.set_start(text, "pause" if text == "Pause" else "play", "secondary" if text == "Pause" else "primary")

    def reset(self):
        self.session.reset()
        self.autolog.reset_clock()
        self.skip_log = False
        self._show_skip_log()
        self._set_start("Start")
        # Also look for the minimap, inventory and party list again (e.g. after moving them).
        self.reader.look_again()
        self.map_name, self.party, self._party_seen = None, 1, (None, 0)
        self.cfg.pop("mesos_box", None)
        save_config(self.cfg)
        self.status.set("Reset. Looking for the map name, inventory and party list again...")

    def toggle_skip_log(self):
        self.skip_log = not self.skip_log
        self.autolog.reset_clock()              # nothing from before or during it is logged
        self._show_skip_log()
        self.status.set("This run won't be logged. Logging turns back on when you leave the map "
                        "or stop getting EXP." if self.skip_log else "Logging this run again.")

    def _show_skip_log(self):
        self.view.logging.set(not self.skip_log, "Not logging" if self.skip_log else "Log this run")

    def _after_pause_check(self, waiting_before):
        """When the run ends (tracking pauses by itself), "Don't log" turns off again, so
        the next run is logged. (Pressed while already paused, it's for the next run.)"""
        if self.skip_log and waiting_before is None and self.session.waiting:
            self.skip_log = False
            self._show_skip_log()

    def _new_source(self):
        # New place to read from -> new baseline, but keep the session totals.
        self.tracker.exp = self.tracker.pct = None
        self.tracker._pending = self.tracker._pending_lvl = None
        self.autolog.reset_clock()
        save_config(self.cfg)

    def _show_area_mode(self):
        pass                                              # (the More menu reads the mode when it opens)

    def toggle_area(self):
        if self.reader.mode == "region":
            self.follow_game()
        else:
            self.select_area()

    def follow_game(self):
        self.reader.use_window()
        self.cfg["mode"] = "window"
        self._new_source()
        self._show_area_mode()
        self.status.set("Following the MapleStory window...")

    def select_area(self):
        def done(region):
            if region:
                self.reader.use_region(region)
                self.cfg.update(mode="region", region=region)
                self._new_source()
                self._show_area_mode()
                self.status.set("Using your selected area. Click Follow game to go back to "
                                "finding it automatically.")
        RegionSelector(self.root, done)

    @staticmethod
    def _boxed_image(bgr, boxes):
        """A screenshot crop, enlarged, with a box drawn around each digit found."""
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        scale = min(4.0, max(1.0, 120 / img.height), 900 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        colors = {"exp": "#ffcc33", "pct": "#33ccff", "mesos": "#66dd66"}
        for x0, y0, x1, y1, kind in boxes:
            draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                           outline=colors[kind], width=2)
        return img

    def _boxed(self, bgr, boxes):
        return ImageTk.PhotoImage(self._boxed_image(bgr, boxes))

    def _class_line(self):
        """What the class reading shows: (picture or None, boxes, a line saying how it went)."""
        look = self.reader.job_look
        if self.job:
            how = f"Class. Read: {self.job}"
        elif look:
            how = f"Class. Not read: {look.get('why') or 'no clear look yet'}"
        else:
            how = "Class. Not read yet (it's tried every few seconds once the EXP bar is found)"
        return (look or {}).get("image"), (look or {}).get("boxes", []), how

    def view_capture(self):
        r, mr = self.last_capture, self.last_mesos
        if not r and not mr:
            messagebox.showinfo("View capture", "Nothing has been read from the game yet.\n\n"
                                "Make sure MapleStory is open and its EXP bar is showing.",
                                parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Last capture")
        win.configure(bg=BG)
        win.attributes("-topmost", bool(self.cfg["topmost"]))
        for delay in (0, 150, 800):
            win.after(delay, lambda: PLATFORM.style_window(win, INK, TEXT_DIM) if win.winfo_exists() else None)
        th = self.theme
        shots = self._look_shots()
        win._imgs = []
        for shot, boxes, text in shots:
            if shot is not None and shot.size:
                win._imgs.append(self._boxed(shot, boxes))
                tk.Label(win, image=win._imgs[-1], bg=BG).pack(padx=th.px(16), pady=(th.px(16), th.px(4)))
            else:
                tk.Frame(win, bg=BG, height=th.px(12)).pack()
            tk.Label(win, text=text, bg=BG, fg=FG, font=th.font("semibold", 12), wraplength=th.px(560),
                     justify="left").pack(anchor="w", padx=th.px(16))
        bottom = tk.Frame(win, bg=BG)
        bottom.pack(fill="x", padx=th.px(16), pady=(th.px(12), th.px(16)))
        tk.Label(bottom, text="Boxes show where each digit was found.", bg=BG, fg=DIM,
                 font=th.font("regular", 11)).pack(side="left")
        Button(bottom, th, text="Something's wrong? Report a bug", kind="secondary", width=240, height=36, radius=10,
               size=12, command=lambda: self.report_bug(win), bg=BG).pack(side="right")

    # ---- bug reports
    def report_bug(self, parent=None):
        """Saves a picture and a text file of what the tracker knows in the data folder and
        shows them. Nothing is sent anywhere: this copy is local only."""
        parent = parent or self.root
        screen = messagebox.askyesno(
            "Save bug details",
            "The details of what the overlay was reading are saved in its data folder (nothing is sent).\n\n"
            "Also save a small picture of the whole game window? It can include chat and character names.",
            default="no", parent=parent)
        files = self._save_report_files(screen)
        shown = files.get("picture") or files.get("details")
        if shown:
            try:
                PLATFORM.show_file(shown)
            except Exception as e:
                log_error("opening the bug report's folder", e)

    @staticmethod
    def _private(text):
        """Without your Windows user name (it's in file paths)."""
        for secret in {str(Path.home()), os.environ.get("USERNAME", "")}:
            if len(secret) > 2:
                text = text.replace(secret, "<you>")
        return text

    def _report_facts(self):
        """Everything a bug report says, as a plain dict (see report.py)."""
        r, t = self.last_capture, self.tracker
        d = self.reader.diagnostics()
        rect = d["rect"]
        monitors = []
        try:
            with mss.mss() as sct:
                monitors = [(m_["width"], m_["height"]) for m_ in sct.monitors[1:]]
        except Exception as e:
            log_error("listing the screens for a bug report", e)
        errors = ""
        try:
            errors = self._private(ERROR_LOG_PATH.read_text(encoding="utf-8", errors="replace"))[-2500:]
        except Exception:
            pass
        now = time.monotonic()
        h = self.health
        up = max(1.0, now - self.started)
        tess = TESS
        return {
            "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "system": PLATFORM.version(),
            "python": sys.version.split()[0], "monitors": monitors, "uptime_s": up,
            "cpu_pct": round((time.process_time() - self.cpu0) / up * 100, 1),
            "dpi_awareness": PLATFORM.dpi_awareness(),
            "dpi": PLATFORM.screen_dpi(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2) if rect else None,
            "tesseract": {"found": bool(tess), "version": tess.version() if tess else None,
                          "where": ("included" if str(APP_DIR) in str(tess.cmd) else "installed") if tess else None},
            "game": {"mode": d["mode"], "found": d["found"], "rect": rect, "gh": d["gh"], "region": d["region"],
                     "minimized": bool(d["found"] and not rect and d["mode"] == "window"),
                     "fullscreen": bool(rect and any(rect[2] == mw and rect[3] == mh for mw, mh in monitors))},
            "overlay": {"rect": d["own_rect"], "covering": d["covering"]},
            "reader": {**{k: d[k] for k in ("exp_box", "exp_misses", "map_box", "mesos_box", "mesos_fails", "two_boxes",
                                            "mesos_learned", "party_area", "job_why")},
                       "level_unknown": t.req is None},
            "health": {"alive": h.alive and self._worker_alive(), "polls": h.polls, "errors": h.errors,
                       "last_error": h.last_error, "average_ms": h.average_ms(), "max_ms": h.max_ms,
                       "last_ms": h.last_ms, "age": None if h.last_at is None else now - h.last_at},
            "rejected": self.rejected, "status": self.status.get().replace("\n", " | "),
            "last_exp": r.text if r else None, "level": self.level, "job": self.job, "map": self.map_name,
            "party": party_label(self.party),
            "mesos": fmt_num(self.meso.value) if self.meso.value is not None else None, "tracking": bool(self.session.on),
            "tracker": f"paused: {self.session.waiting or 'no'}, gained {fmt_num(t.gained)} EXP in {fmt_dur(t.elapsed())}",
            "settings": {"mode": self.reader.mode, **{k: self.cfg.get(k) for k in ("gap_seconds", "transparency", "topmost", "ui_scale",
                                                      "compact", "hotkeys_on", "hud")}},
            "timeline": [self._private(x) for x in self.recent.lines()], "errors": errors,
        }

    def _worker_alive(self):
        return getattr(self, "_worker_thread", None) is None or self._worker_thread.is_alive()

    def _look_shots(self):
        """What each reader last saw, with a line on how it went: [(picture or None, boxes, text)]."""
        r, mr = self.last_capture, self.last_mesos
        shots = []
        if r:
            shots.append((r.image, r.boxes, f"EXP bar. Read: {r.text or 'nothing'}   ({r.where})"))
        if mr:
            read = fmt_num(mr.mesos) if mr.mesos is not None else "nothing (waiting for a clear look)"
            shots.append((mr.mesos_image, mr.mesos_boxes, f"Mesos. Read: {read}"))
        if r:
            shots.append(self._class_line())
            look = self.reader.map_look
            if look:
                shots.append((look["image"], [], f"Map name. Read: {look['text'] or 'nothing (not clear yet)'}"))
        return shots

    def _save_report_files(self, with_screen=False):
        """The picture of what was read, the details as text, and (if asked) the game window:
        saved in the data folder. Returns {"picture", "details", "screen"} (missing ones left out)."""
        files = {}
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_error("making the data folder for a bug report", e)
            return files
        facts = None
        try:
            facts = self._report_facts()
            path = DATA_DIR / "bug-report.txt"
            path.write_text(self._private(report.details(facts)), encoding="utf-8")
            files["details"] = path
        except Exception as e:
            log_error("saving the bug report details", e)
        self._report_facts_cache = facts
        picture = self._save_report_picture()
        if picture:
            files["picture"] = picture
        if with_screen:
            screen = self._save_report_screen()
            if screen:
                files["screen"] = screen
        return files

    def _captioned(self, image, text):
        """A crop with a line of text above it."""
        try:
            font = ImageFont.truetype(str(ASSETS / "fonts" / "ManropeSemiBold.ttf"), 15)
        except Exception:
            font = ImageFont.load_default()
        w = max(image.width if image is not None else 0, 760)
        sheet = Image.new("RGB", (w, (image.height if image is not None else 0) + 26), BG)
        ImageDraw.Draw(sheet).text((2, 3), text[:110], fill=FG, font=font)
        if image is not None:
            sheet.paste(image, (0, 26))
        return sheet

    def _save_report_picture(self):
        """What each reader last saw (EXP bar, mesos, class, map name), with captions, as one PNG."""
        parts = []
        for shot, boxes, text in self._look_shots():
            image = self._boxed_image(shot, boxes) if shot is not None and shot.size else None
            parts.append(self._captioned(image, text))
        if not parts:
            return None
        sheet = Image.new("RGB", (max(p.width for p in parts), sum(p.height + 8 for p in parts)), BG)
        y = 0
        for p in parts:
            sheet.paste(p, (0, y))
            y += p.height + 8
        path = DATA_DIR / "bug-report.png"
        try:
            sheet.save(path)
            return path
        except Exception as e:
            log_error("saving the bug report picture", e)
            return None

    def _save_report_screen(self):
        """The game window as the overlay sees it (this window blacked out), shrunk, as a JPEG."""
        rect = self.reader.last_rect
        if not rect:
            return None
        try:
            with mss.mss() as sct:
                shot = self.reader._hide_own(grab(sct, *rect))
            if not shot:
                return None
            img = Image.fromarray(cv2.cvtColor(shot[0], cv2.COLOR_BGR2RGB))
            img.thumbnail((1600, 900), Image.LANCZOS)
            path = DATA_DIR / "bug-report-game.jpg"
            img.save(path, quality=72)
            return path
        except Exception as e:
            log_error("saving the bug report's game picture", e)
            return None

    def _report_text(self, files):
        """The bug report's text: room to describe it, then the short details (the whole of it is
        in bug-report.txt: the address a report is opened with can't be very long)."""
        facts = getattr(self, "_report_facts_cache", None) or self._report_facts()
        attach = [f"`{files[k].name}`" for k in ("picture", "details", "screen") if k in files]
        errors = (facts.get("errors") or "").strip()[-700:]
        lines = [
            "**What went wrong?**", "(Describe it here: what did you do, what did you expect, what did it show?)", "",
            "**Files**",
            (f"(Drag in {' and '.join(attach)} from the folder that just opened: they show what the overlay read "
             "and how it's doing.)" if attach else "(If you can, add a screenshot of the game and the overlay.)"), "",
            "---", "<details><summary>Details (filled in by the overlay)</summary>", "",
            self._private(report.summary(facts)),
        ]
        if errors:
            lines += ["", "Recent errors:", "```", errors, "```"]
        lines += ["", "</details>"]
        return "\n".join(lines)

    def _remember_log_size(self):
        """Keeps the log's column widths, and the window size if the user resized it."""
        tree, root = self.log_tree, self.root
        self.cfg["log_columns_pt"] = {c: round(int(tree.column(c, "width")) / self._px_per_pt, 1)
                                      for c in tree["columns"]}
        self.cfg.pop("log_columns", None)
        if self.log_open:
            size = [root.winfo_width(), root.winfo_height()]
            natural = [root.winfo_reqwidth(), root.winfo_reqheight()]
            self.cfg["log_size"] = size if size != natural else None

    def close(self):
        self._note_flush()
        self.stop_evt.set()
        try:
            self._remember_log_size()
        except Exception as e:
            log_error("saving the log size", e)
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()


def main():
    global tesseract_ok, TESS
    # Only one copy at a time (per data folder): two copies would each keep their own log.
    # If it's already running, bring that window to the front instead. (The lock keeps the old
    # name, so an older version and this one can't run at the same time either.)
    key = hashlib.md5(str(DATA_DIR).lower().encode()).hexdigest()[:12]
    if not PLATFORM.claim_single_instance(key, WINDOW_TITLE, OLD_TITLES):
        return
    PLATFORM.ask_permissions()
    move_old_data()
    tess = find_tesseract()
    if tess:
        TESS = ocr.Tesseract(tess)
        tesseract_ok = True
    root = tk.Tk()
    App(root, DigitTemplates.load())
    root.mainloop()


if __name__ == "__main__":
    main()
