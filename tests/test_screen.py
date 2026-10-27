"""Tests for finding things on screen: the real party list and inventory (cut from real
screenshots at three UI sizes) pasted over many kinds of background, solid and see-through.

Not finding something on a hard background is allowed (the tracker just waits for a
clearer look). A wrong answer, like a wrong mesos count or party size, is not.

Run from the project folder:  py -3 tests/test_screen.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import maple_exp as m  # noqa: E402

FRAMES = Path(__file__).parent / "frames"
PARTS = json.loads((FRAMES / "screen_parts.json").read_text())
rnd = random.Random(1)
failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def backgrounds(h, w):
    """Plain, busy and tricky backgrounds (white like the box, gold like the coin, ...)."""
    g = np.random.default_rng(rnd.randint(0, 10 ** 6))
    yy, xx = np.mgrid[0:h, 0:w]
    blobs = cv2.GaussianBlur(g.integers(0, 256, (h, w, 3), dtype=np.uint8), (0, 0), 6)
    return {"black": np.zeros((h, w, 3), np.uint8), "white": np.full((h, w, 3), 255, np.uint8),
            "dark gray": np.full((h, w, 3), 45, np.uint8), "light gray": np.full((h, w, 3), 215, np.uint8),
            "gold": np.full((h, w, 3), (40, 190, 230), np.uint8),
            "noise": g.integers(0, 256, (h, w, 3), dtype=np.uint8),
            "dark noise": g.integers(0, 70, (h, w, 3), dtype=np.uint8),
            "blobs": cv2.normalize(blobs, None, 0, 255, cv2.NORM_MINMAX),
            "stripes": ((xx // 7 % 2) * 255).astype(np.uint8)[..., None].repeat(3, 2),
            "gradient": (xx * 255 // max(w, 1)).astype(np.uint8)[..., None].repeat(3, 2)}


# ---- mesos: found and read right (or not at all), solid and see-through
reader = m.ScreenReader(m.DigitTemplates.load())
templates = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz").templates()
for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    counts = {"solid read": [0, 0], "see-through read": [0, 0], "found see-through": [0, 0]}
    wrong = []
    for alpha in (1.0, 0.75, 0.6, 0.5, 0.4):
        for bg_name, bg in backgrounds(int(h * 1.6), int(w * 1.8)).items():
            oy, ox = rnd.randint(0, bg.shape[0] - h), rnd.randint(0, bg.shape[1] - w)
            img = bg.astype(np.float32)
            img[oy:oy + h, ox:ox + w] = alpha * inv + (1 - alpha) * img[oy:oy + h, ox:ox + w]
            img = img.astype(np.uint8)
            box = (ox + bx, oy + by, bw, bh)
            kind = "solid read" if alpha == 1 else "see-through read"
            got = m.read_mesos_positions(img, box, templates)
            counts[kind][0] += got is not None and got[0] == info["mesos"]
            counts[kind][1] += 1
            if got and got[0] != info["mesos"]:
                wrong.append(f"read {got[0]} ({bg_name}, {alpha:.0%} visible)")
            found = reader._find_seethrough_box(img, gh)
            if found:
                read = m.read_mesos_positions(img, found, templates)
                if not read or read[0] != info["mesos"]:
                    wrong.append(f"found a box that reads {read and read[0]} ({bg_name}, {alpha:.0%} visible)")
                counts["found see-through"][0] += 1
            counts["found see-through"][1] += 1
            solid = m.find_mesos_field(img, gh)
            if solid and reader._readable(img, solid):
                read = m.read_mesos_positions(img, solid, templates)
                if read and read[0] != info["mesos"]:
                    wrong.append(f"solid finder: read {read[0]} ({bg_name}, {alpha:.0%} visible)")
    size = f"EXP digits {gh:.0f} px"
    check(f"mesos never read wrong, UI size {size} ({', '.join(wrong) or 'no wrong reads'})", not wrong)
    ok, total = counts["solid read"]
    check(f"mesos read on a solid inventory, {size}: {ok}/{total}", ok == total)
    ok, total = counts["see-through read"]
    check(f"mesos read on a see-through inventory, {size}: {ok}/{total}", ok >= 0.85 * total)
    ok, total = counts["found see-through"]
    print(f"     (mesos box found by its outline, {size}: {ok}/{total})")

# ---- a box found too narrow (something across its left part): never a number cut off at the front
for name, info in sorted(PARTS["inventory"].items()):
    inv, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["box"]
    cut = []
    for i in range(1, len(str(info["mesos"]))):
        left = int(m._mesos_spot((bx, by, bw, bh), i) - 0.3 * bh) + 1      # room for only i digits
        got = m.read_mesos_positions(inv, (left, by, bx + bw - left, bh), templates)
        if got and got[0] != info["mesos"]:
            cut.append(f"{got[0]} with room for {i} digits")
    check(f"a box too narrow for the number never reads it short, EXP digits {info['gh']:.0f} px "
          f"({', '.join(cut) or 'none read short'})", not cut)

# ---- storage and trade windows show a second mesos box: never read the wrong one
class FakeScreen:
    """Stands in for mss: one monitor showing `image` (the game), grab() cuts out of it."""
    def __init__(self, image):
        self.image = image
        self.monitors = [{"left": 0, "top": 0, "width": image.shape[1], "height": image.shape[0]}]

    def grab(self, r):
        part = self.image[r["top"]:r["top"] + r["height"], r["left"]:r["left"] + r["width"]]
        return np.dstack([part, np.full(part.shape[:2], 255, np.uint8)])


for name, info in sorted(PARTS["inventory"].items()):
    inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
    h, w = inv.shape[:2]
    size = f"EXP digits {gh:.0f} px"
    one_ok = two_ok = total = 0
    for bg_name, bg in backgrounds(int(h * 1.3), int(w * 3.2)).items():
        total += 1
        alone, both = bg.copy(), bg.copy()
        alone[:h, :w] = inv
        both[:h, :w] = inv
        both[:h, w + 10:2 * w + 10] = inv                         # storage's box beside yours
        one_ok += len(m.find_mesos_fields(alone, gh)) == 1
        two_ok += len(m.find_mesos_fields(both, gh)) == 2
    check(f"one inventory shows one mesos box, {size}: {one_ok}/{total}", one_ok == total)
    check(f"an inventory beside a storage / trade window shows two, {size}: {two_ok}/{total}", two_ok == total)

    # the reader follows along: finds yours, lets go while a second box is there, finds it again
    ww, wh = 3 * w + 20, int(h * 1.3)
    scene = np.full((wh, ww, 3), 25, np.uint8)
    scene[:h, :w] = inv
    reader = m.ScreenReader(m.DigitTemplates.load())
    reader.gh = gh
    reader.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz")
    blank = m.Reading(*([None] * len(m.Reading._fields)))
    def look(image):
        reader.next_scan = 0.0
        return reader._read_extras(FakeScreen(image), (0, 0, ww, wh), blank)
    look(scene)
    check(f"finds the box with only the inventory open, {size}", reader.mesos_box == (bx, by, bw, bh))
    crowded = scene.copy()
    crowded[:h, w + 10:2 * w + 10] = inv
    look(crowded)
    r = look(crowded)
    check(f"drops it and reads nothing while a second box is there, {size}",
          reader.mesos_box is None and reader.two_boxes and r.mesos is None)
    look(scene)
    check(f"finds it again once the storage is closed, {size}", reader.mesos_box == (bx, by, bw, bh) and not reader.two_boxes)

# ---- the costly outline search for a see-through mesos box doesn't run on every look
name, info = sorted(PARTS["inventory"].items())[1]
inv, gh, (bx, by, bw, bh) = cv2.imread(str(FRAMES / name)), info["gh"], info["box"]
h, w = inv.shape[:2]
empty = np.full((int(h * 1.3), int(w * 1.3), 3), 25, np.uint8)
see_through = empty.copy()
see_through[:h, :w] = (0.45 * inv + 0.55 * empty[:h, :w]).astype(np.uint8)
reader = m.ScreenReader(m.DigitTemplates.load())
reader.gh = gh
reader.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "x.npz")
blank = m.Reading(*([None] * len(m.Reading._fields)))
searches, clock = [], [1000.0]
real_find, real_monotonic = reader.box_finder.find, m.time.monotonic
reader.box_finder.find = lambda *a: searches.append(clock[0]) or real_find(*a)
m.time.monotonic = lambda: clock[0]
try:
    def look(image):
        reader.next_scan = 0.0
        return reader._read_extras(FakeScreen(image), (0, 0, image.shape[1], image.shape[0]), blank)
    for _ in range(12):                                         # 24 s of nothing to find
        look(empty)
        clock[0] += 2
    check(f"with no inventory open, the outline search runs about every {m.OUTLINE_SECONDS} s, not every look ({len(searches)} in 24 s)",
          2 <= len(searches) <= 24 // m.OUTLINE_SECONDS + 1)
    reader.next_outline = clock[0] + m.OUTLINE_SECONDS           # (just after a search that found nothing)
    found_after = None
    for i in range(1, 16):                                      # the inventory is opened
        look(see_through)
        clock[0] += 2
        if reader.mesos_box and found_after is None:
            found_after = 2 * i
    check(f"a see-through inventory is still found, within {m.OUTLINE_SECONDS + 4} s of opening it (took {found_after} s)",
          found_after is not None and found_after <= m.OUTLINE_SECONDS + 4)
finally:
    m.time.monotonic = real_monotonic

# ---- party: the right number of HP bars, from 1 to 6 members, on every background
for key, info in sorted(PARTS["party"].items(), key=lambda kv: kv[1]["gh"]):
    gh, pitch = info["gh"], info["pitch"]
    rows = [cv2.imread(str(FRAMES / r), cv2.IMREAD_UNCHANGED) for r in info["rows"]]
    rh, rw = rows[0].shape[:2]
    wrong, total = [], 0
    for members in range(1, 7):
        h = pitch * (members - 1) + rh
        for bg_name, bg in backgrounds(h + int(6 * gh), rw + int(8 * gh)).items():
            img = bg.copy()
            ox, oy = rnd.randint(0, img.shape[1] - rw), rnd.randint(0, img.shape[0] - h)
            for i in range(members):
                row = rows[i % len(rows)]
                region = img[oy + i * pitch:oy + i * pitch + rh, ox:ox + rw]
                a = row[..., 3:] / 255.0
                region[:] = (row[..., :3] * a + region * (1 - a)).astype(np.uint8)
            seen = max(1, len(m.find_party_bars(img, gh)))
            total += 1
            if seen != members:
                wrong.append(f"{members} as {seen} on {bg_name}")
    check(f"party size right, EXP digits {gh:.0f} px: {total - len(wrong)}/{total} "
          f"({', '.join(wrong[:5]) or 'all right'})", not wrong)
    clean = all(len(m.find_party_bars(bg, gh)) <= 1 for bg in backgrounds(900, 1400).values())
    check(f"no party list on screen means solo, EXP digits {gh:.0f} px", clean)

# ---- the class, from the status bar (the character name line is blanked in these)
_tess = m.find_tesseract()
m.TESS = m.ocr.Tesseract(_tess) if _tess else None
m.tesseract_ok = bool(_tess)
if m.tesseract_ok:
    for name, info in sorted(PARTS["status"].items()):
        got = m.read_job(cv2.imread(str(FRAMES / name)), info["x0"], info["cy"], info["gh"])
        check(f"reads the class at EXP digits {info['gh']:.0f} px: {got}", got == info["job"])
        look = {}
        m.read_job(cv2.imread(str(FRAMES / name)), info["x0"], info["cy"], info["gh"], look)
        check(f"...and shows what it looked at ({name}): a picture, boxes, no reason to give up",
              look["image"].size > 0 and len(look["boxes"]) == 2 and look["why"] == "")
    blank = np.full((300, 900, 3), 20, np.uint8)
    look = {}
    check("a picture with no status bar gives no class, and says why",
          m.read_job(blank, 600, 150, 11, look) is None and "HP bar" in look["why"])
    look = {}
    check("off the edge of the screen says so", m.read_job(blank, 10, 150, 11, look) is None and look["why"])
else:
    print("     (Tesseract isn't installed: class reading not tested)")

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
