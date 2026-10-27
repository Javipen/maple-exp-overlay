"""Reads pickup lines with Tesseract: the item lines the game's font can't (msgwatch.py reads the
amounts of the others).

Picking something up prints a white line in the message stack: "You have gained mesos (+15)"
for mesos, "Green Mushroo... x1 earned. (Etc)" for an item (its name cut short when it's long,
how many, and its inventory tab). A line is read a few ways at once, taking the answer most of
them agree on. Lines it can't make sense of are left out: a missed pickup is better than a
wrong one. (White EXP lines are told apart by their text, and ignored.)
"""
import re
from collections import Counter

import cv2
import numpy as np

_AMOUNT = re.compile(r"\+\s*(\d[\d,]*)(.?)")
# "earned" as it's read: earned, eared, eamed, carned...
_ITEM = re.compile(r"^(?P<name>.*?)\s*(?:[xX×]\s*(?P<qty>\d{1,4})\b)?[^A-Za-z]{0,3}\s*\b[eEcC][aA]\w{1,4}[dD]\b.*?[(\[{]\s*(?P<tab>[A-Za-z]{2,6})")
TABS = {"etc": "Etc", "use": "Use", "equip": "Equip", "eqp": "Equip", "setup": "Setup", "cash": "Cash"}


def parse(text):
    """What a message line says: ("mesos", amount), ("item", name, how many or None, tab), or None (an
    EXP line, a name over a character, or something that can't be read clearly)."""
    t = " ".join(text.replace("\u2026", "...").split())
    low = t.lower()
    if not t or "exp" in low or "receiv" in low or "bonus" in low:
        return None
    m = _ITEM.search(t)
    if m:
        name = re.sub(r"^[^A-Za-z]+", "", m.group("name"))
        name = re.sub(r"[^A-Za-z'.]+$", "", name).rstrip(".")      # (any bits of a misread "x1" off its end)
        name = re.sub(r"\s+[xX]$", "", name)
        tab = next((v for k, v in TABS.items() if m.group("tab").lower().startswith(k[:3])), None)
        if len(name) >= 3 and tab:
            return ("item", name, int(m.group("qty")) if m.group("qty") else None, tab)
        return None
    if re.search(r"ga\w{0,3}ed|m\w{0,2}s\w{0,2}s", low):
        m = _AMOUNT.search(t)
        # The amount must be followed by something (its bracket, however it's read): "(+17)" read as
        # "(+173" ends in a digit, and that digit may be the bracket. Then don't take it.
        if m and m.group(2) and not m.group(2).isdigit():
            digits = m.group(1).replace(",", "")
            if len(digits) <= 7:
                return ("mesos", int(digits))
    return None


def _key(p):
    """What two readings of a line must share to agree: the amount, or the item's name (its first
    letters: the end is easily misread) and tab."""
    return p[:2] if p[0] == "mesos" else ("item", re.sub(r"[^a-z]", "", p[1].lower())[:8], p[3])


def line_pictures(bgr, line, text_h):
    """A message line, ready for Tesseract a few ways: dark text on white, at two sizes, plain and smoothed."""
    y, x0, x1, _ = line
    pad = int(0.8 * text_h)
    crop = bgr[max(0, int(y - pad)):int(y + pad), max(0, x0 - int(0.3 * text_h)):x1 + int(0.3 * text_h)]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    whiteness = np.clip((hsv[..., 2].astype(np.float32) - 120) / 110, 0, 1) * (hsv[..., 1] <= 70)
    pictures = []
    for target in (36, 48):
        s = target / text_h
        big = cv2.resize(whiteness, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for img in ((big > 0.5) * 255.0, np.clip(big, 0, 1) * 255):
            pictures.append(cv2.copyMakeBorder((255 - img).astype(np.uint8), 24, 24, 24, 24,
                                               cv2.BORDER_CONSTANT, value=255))
    return pictures


def vote(texts):
    """The reading most of a line's pictures agree on (at least half of them), or None. An item's count
    is the one most readings give (1 if none can read it: it's almost always 1)."""
    parsed = [p for p in (parse(t) for t in texts) if p]
    found = Counter(_key(p) for p in parsed)
    if not found:
        return None
    key, n = found.most_common(1)[0]
    if n * 2 < len(texts):
        return None
    same = [p for p in parsed if _key(p) == key]
    if key[0] == "mesos":
        return same[0]
    names = Counter(p[1] for p in ([p for p in same if p[2]] or same))   # (the ones that read the "x1" are cleanest)
    counts = Counter(p[2] for p in same if p[2])
    return ("item", names.most_common(1)[0][0], counts.most_common(1)[0][0] if counts else 1, key[2])


def read_line(band, span, text_h, read_lines):
    """What a white message line says, from a picture of its row (band: the line's middle half way
    down) and where it starts and ends in it (span): parse()'s answer, or None.

    read_lines: reads a list of pictures, one line of text each (Tesseract)."""
    x0, x1 = span
    return vote(read_lines(line_pictures(band, (band.shape[0] / 2, x0, x1, None), text_h)))
