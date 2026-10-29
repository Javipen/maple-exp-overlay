"""Item lines, read by the game's font (itemwatch.py): the end of the line (tab, "earned.", count) by
its shapes, the name by its picture once it's known, and Tesseract's reading of a new name matched to
the game's item list. Real 4K lines (cropped to the lines; no player names): the ones the shapes were
made from (item_rows.png) and others held out (item_rows_test.png). Run from the project folder:
py -3 tests/test_items.py"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("MAPLE_EXP_DATA", tempfile.mkdtemp())

import cv2  # noqa: E402
import itemwatch as iw  # noqa: E402
import make_item_font as mk  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def span(band):
    w = mk.words(band)
    return int(w[-1][0]), int(w[0][1])


def score(stem, reader, read_lines=None):
    right, wrong = 0, []
    rows = mk.rows(stem)
    for band, tab, qty, name in rows:
        got = reader.read(band, span(band), 1.0, read_lines)
        if got == ("item", name, qty, tab):
            right += 1
        elif got is not None:
            wrong.append(f"{name} as {got}")
    return right, len(rows), wrong


reader = iw.ItemReader(ROOT / "assets")
right, n, wrong = score("item_rows", reader)
check(f"the lines the shapes were made from: {right} of {n} read, none wrong ({wrong or 'none'})",
      not wrong and right >= 0.85 * n)
right, n, wrong = score("item_rows_test", reader)
# (The ones missed: faded lines, and two cut off at the right edge of the picture.)
check(f"lines held out: {right} of {n} read, none wrong ({wrong or 'none'})", not wrong and right >= 0.8 * n)

# ---- not item lines: nothing read from them (without Tesseract)
mesos = cv2.imread(str(ROOT / "tests" / "frames" / "pickups_mesos.png"))
got = [reader.read(mesos[max(0, y - 21):y + 22], (58, 516), 1.0) for y in (40, 79)]
check(f"mesos lines aren't read as items ({got})", got == [None, None])

# ---- Tesseract's reading of a name, matched to the game's item list (on its tab; cut short: by its start)
items = iw.load_items(ROOT / "assets" / "items.txt")
check(f"the item list has the game's items, by tab ({ {k: len(v) for k, v in items.items()} })",
      len(items.get("Etc", [])) > 300 and "Lupin's Banana" in items["Etc"] and "Red Potion" in items["Use"]
      and "Cash" not in items)
cases = [("Green Miushrog", "Etc", True, "Green Mushroom Cap"), ("Red Fation", "Use", False, "Red Potion"),
         ("Arrows for Cros", "Use", True, "Arrows for Crossbows"), ("Green woods", "Equip", True, "Green Woodsman Boots"),
         ("Lupin's Bananas", "Etc", False, "Lupin's Banana"), ("Animal Fur Bun", "Etc", True, "Animal Fur Bundle"),
         ("Fed Fotiongl", "Use", False, None), ("Lupin's Banana", "Use", False, None)]
got = [iw.match_name(raw, tab, items, cut) for raw, tab, cut, _ in cases]
check(f"misread names find their item, and one too far off (or on the wrong tab) finds none ({got})",
      got == [want for *_, want in cases])

# ---- a name not known by sight: named once from Tesseract and the list, then known by its picture
learned = Path(tempfile.mkdtemp()) / "item_names.npz"
fresh = iw.ItemReader(ROOT / "assets", learned)
fresh.names = [x for x in fresh.names if x[0] != "Lupin's Banana"]
asked = []


def tesseract(images):          # (Tesseract misreading it a little, as it does)
    asked.append(len(images))
    return ["Lupin's Bananas x1 earned. (Etc)"] * len(images)


bananas = [(band, qty) for band, tab, qty, name in mk.rows("item_rows_test") if name == "Lupin's Banana"]
first = fresh.read(bananas[0][0], span(bananas[0][0]), 1.0)
check("without Tesseract, a name not known by sight is left out", first is None)
first = fresh.read(bananas[0][0], span(bananas[0][0]), 1.0, tesseract)
check(f"with it, the name is read and matched to the item list ({first})",
      first == ("item", "Lupin's Banana", 1, "Etc") and fresh.learned == 1 and learned.is_file())
asked.clear()
later = [fresh.read(b, span(b), 1.0, tesseract) for b, _ in bananas[1:]]
check(f"...and from then on it's known by sight, without Tesseract "
      f"({sum(g is not None for g in later)} of {len(later)} read, Tesseract asked {len(asked)} times)",
      sum(g == ("item", "Lupin's Banana", 1, "Etc") for g in later) >= len(later) - 3 and len(asked) <= 3)
again = iw.ItemReader(ROOT / "assets", learned)
check("what was learned is kept in the data folder for next time",
      again.learned >= 1 and any(n == "Lupin's Banana" for n, _, _ in again.names[-again.learned:]))

# ---- a line whose end has no shape here (a "(Setup)" tab): Tesseract reads it, matched to the list
got = reader.read(mesos[18:61], (58, 516), 1.0, lambda images: ["Wooden Bnech x1 earned. (Setup)"] * len(images))
check(f"a line whose end isn't read by its shapes is read by Tesseract, its name matched to the list ({got})",
      got == ("item", "Wooden Bench", 1, "Setup"))

print(f"\n{failures} FAILED" if failures else "\nALL PASSED")
sys.exit(1 if failures else 0)
