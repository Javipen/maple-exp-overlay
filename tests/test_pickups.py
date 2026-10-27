"""Pickups, from the game's white message lines (pickwatch.py): reading what a line says, and reading
real 4K lines (cropped to the lines) by the game's font, and with Tesseract when it's here.
Run from the project folder:  py -3 tests/test_pickups.py"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()
import cv2  # noqa: E402

import maple_exp as m  # noqa: E402
import msgwatch as mw  # noqa: E402
import ocr  # noqa: E402
import pickwatch as pk  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


# ---- what a line says, from what Tesseract made of real lines
said = {"You have gained mesos (+15)": ("mesos", 15),
        "You have qained mesas (+17)": ("mesos", 17),
        "YOU have gained mesos {+30}": ("mesos", 30),
        "You have gained mesos f+30%": ("mesos", 30),          # (the brackets misread)
        "You have gained mesos (+304": None,                    # (the closing bracket read as a 4: not taken)
        "qained mesos {+173": None,
        "You reteived EXP (+1174": None,                        # (white EXP lines are ignored)
        "You received EXP (+11)": None,
        "Black Pia Fluegel": None,                              # (names over characters)
        "(Etc!": None,
        "Lupin's Banana x1 earned. (Etc)": ("item", "Lupin's Banana", 1, "Etc"),
        "Green Mushroo... x1 earned. (Etc)": ("item", "Green Mushroo", 1, "Etc"),
        "Lemon x2 earned. (Use)": ("item", "Lemon", 2, "Use"),
        "Orange Potion x1 eamed. (Use)": ("item", "Orange Potion", 1, "Use"),
        "Lupin's Banana x) eared. (Etc)": ("item", "Lupin's Banana", None, "Etc")}
wrong = {t: pk.parse(t) for t, want in said.items() if pk.parse(t) != want}
check(f"message lines are read for what they say ({wrong or 'all right'})", not wrong)
check("a line's readings are voted on: the amount most of them agree on",
      pk.vote(["You have gained mesos f+30%", "You have gained mesos (+30%", "YoU have gained mesos f+30%",
               "You have gained mesos (+304"]) == ("mesos", 30))
check("...an item's name from the clean readings, its count 1 when it can't be read",
      pk.vote(["Lupin's Bananas] earned. (Etc)", "Lupin's Bananas) earned. (Etc)", "Lupin's Banana x) eared. (Etc)",
               "Lupin's Banana x1 earned. (Etc)"]) == ("item", "Lupin's Banana", 1, "Etc"))
check("...and nothing when they don't agree", pk.vote(["You have gained mesos (+15)", "You have gained mesos (+16)",
                                                        "junk", "more junk"]) is None)

# ---- real lines (a 4K game: text about 20 px), from a picture of each line's row and where it starts and ends
FRAMES = ROOT / "tests" / "frames"
mesos = cv2.imread(str(FRAMES / "pickups_mesos.png"))
item = cv2.imread(str(FRAMES / "pickups_item.png"))
LINES = [(mesos, 40, (58, 516), ("mesos", 17)), (mesos, 79, (58, 516), ("mesos", 15)),
         (item, 22, (30, 518), ("item", "Lupin's Banana", 1, "Etc"))]


def row(img, y):
    return img[max(0, y - 21):y + 22]


font = mw.Font(ROOT / "assets")
got = [font.read(row(img, y), 1.0, x1)[:2] for img, y, (x0, x1), _ in LINES]
check(f"the game's font reads the mesos lines, and finds no amount in the item line ({got})",
      got == [("mesos", 17), ("mesos", 15), ("text", None)])

tess = os.environ.get("TESSERACT") or m.find_tesseract()
build = ROOT / "build" / "dist" / "Maple Classic Exp Overlay" / "tesseract" / "tesseract.exe"
tess = tess or (str(build) if build.is_file() else None)
if tess:
    T = ocr.Tesseract(tess)
    got = [pk.read_line(row(img, y), span, 20, T.read_lines) for img, y, span, _ in LINES]
    check(f"Tesseract reads them too: the mesos, and the item ({got})", got == [want for *_, want in LINES])
else:
    print("     (Tesseract isn't here: reading the pictures not tested)")

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
