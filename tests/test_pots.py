"""The potion popup reader (potwatch.py) and the potions-used count (PotTracker), from real pictures of
the controller's left-trigger popup in a 4K game, cropped to just the popup. Run from the project
folder:  py -3 tests/test_pots.py"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()
import cv2  # noqa: E402

import maple_exp as m  # noqa: E402
import potwatch as pw  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


FRAMES = ROOT / "tests" / "frames"
reader = pw.PotReader(ROOT / "assets")
a = cv2.imread(str(FRAMES / "pots_lt_a.png"))      # a lossless capture
b = cv2.imread(str(FRAMES / "pots_lt_b.png"))      # from a compressed screenshot (colours bleed a little)

got = reader.look(a, 20)
check(f"the popup is found and every count read right ({[c for _, c in got]})",
      [c for _, c in got] == [None, 1068, 368, 51, 846, 1018, None, None])
got_b = reader.look(b, 20)
counts_b = [c for _, c in got_b]
# (In the compressed screenshot the lemon's yellow bleeds into the 8 of its 984: not checked there.)
check(f"...and in another picture, with other counts ({counts_b})",
      counts_b[:4] == [None, 992, 357, 21] and counts_b[5:] == [1005, None, None])
got_c = [c for _, c in reader.look(cv2.imread(str(FRAMES / "pots_lt_c.png")), 20)]   # (a capture, cropped to the popup)
check(f"a 4-digit count that runs over the slot's right edge is read whole ({got_c})",
      got_c == [None, 588, 683, 102, 717, 2203, None, None])
check("the right trigger's popup (skills) isn't read as potions", reader.look(cv2.imread(str(FRAMES / "pots_rt.png")), 20) is None)
check("nor the usual controller buttons", reader.look(cv2.imread(str(FRAMES / "pots_none.png")), 20) is None)
names = [pw.guess_item(look)[0] if c is not None else None for look, c in got]
check(f"each item is guessed from its icon's colour, with the shops' prices ({names})",
      names == [None, "Meat", "Blue Potion", "Red Potion", "Lemon", "Orange Potion", None, None]
      and pw.guess_item(got[2][0])[1] == 220 and pw.guess_item(got[1][0])[1] == 80)
same = all(pw.same_icon(got[i][0], got_b[i][0]) for i in range(1, 6))
apart = not any(pw.same_icon(got[i][0], got[j][0]) for i in range(1, 6) for j in range(1, 6) if i != j)
check("an item's icon is known again in another picture, and told apart from the others "
      "(blue and red potions included)", same and apart)

# A count's last digit made unclear (a fade, something passing over it): never read short (1068 as 106)
import numpy as np  # noqa: E402
at = reader.find_label(a, 20)
short = []
for i, box in enumerate(reader.slots(at, 20)):
    want = got[i][1]
    if want is None:
        continue
    mask, x0 = pw.strokes(a, box, 20)
    right = x0 + int(np.nonzero(mask.any(axis=0))[0][-1])
    y0 = box[1] + int(pw.DIGIT_TOP * box[3])
    for cut in (0.2, 0.3, 0.5, 0.7, 0.9):
        smudged = a.copy()
        smudged[y0:y0 + 40, right - int(16 * cut):right + 1] = (20, 20, 20)
        read = reader.read_count(smudged, box, 20)
        if read not in (None, want):
            short.append(f"{want} as {read}")
check(f"a count whose last digit is unclear is read right or not at all ({', '.join(short) or 'never short'})", not short)

# ---- potions used: counts confirmed by looks that agree; a count going up is a purchase
t = m.PotTracker()
for counts in ({1: 1068}, {1: 1068}):
    t.observe(counts, 0, True)
check("the first count needs 3 looks that agree", 1 not in t.counts)
t.observe({1: 1068}, 0, True)
check("...then it's known", t.counts[1] == 1068)
check("a drop needs 2 looks that agree", t.observe({1: 1060}, 1, True) == {})
check("...then it's potions used", t.observe({1: 1060}, 2, True) == {1: 8})
check("a one-off misread is never counted", t.observe({1: 1006}, 3, True) == {} and t.observe({1: 1060}, 4, True) == {}
      and t.counts[1] == 1060)
used = [t.observe({1: 1500}, 5 + i, True) for i in range(3)]
check("a count going up (bought more) needs 3 looks, and isn't potions used", used == [{}, {}, {}] and t.counts[1] == 1500)
for i in range(2):
    used = t.observe({1: 1490}, 10 + i, False)
check("with tracking off (Pause, or not started), what's used isn't counted", used == {} and t.counts[1] == 1490)
for i in range(2):
    used = t.observe({1: 1400}, 600 + i, True)
check("closed for 10 minutes: everything used meanwhile counts at the next look", used == {1: 90})

# misreads: a count read short (a digit lost) or a drop too big for the time, and taken back
t = m.PotTracker()
for i in range(3):
    t.observe({1: 1068}, i, True)
used = [t.observe({1: 106}, 3 + i, True) for i in range(7)]
check("a count read with a digit lost (1068 as 106) isn't believed from 2 looks, nor 7",
      all(u == {} for u in used) and t.counts[1] == 1068)
check("...and even after 8 it isn't counted as potions used", t.observe({1: 106}, 10, True) == {} and t.counts[1] == 106)
for i in range(3):
    t.observe({1: 1068}, 11 + i, True)
check("...nor is the right count coming back counted as anything", t.counts[1] == 1068)
used = [t.observe({1: 500}, 20 + i, True) for i in range(8)]
check("568 used in 7 seconds: needs 8 looks in a row", all(u == {} for u in used[:7]) and used[7] == {1: 568})
used = [t.observe({1: 1068}, 40 + i, True) for i in range(3)]
check(f"...and when the count is back where it was soon after, it's taken back as a misread ({used[-1]})",
      used[-1] == {1: -568})

# potions picked up from monsters make up for some used; bought ones don't count
t = m.PotTracker()
for i in range(3):
    t.observe({4: 846}, i, True)
for i in range(2):
    t.observe({4: 800}, 300 + i, True)                         # 46 lemons used in 5 minutes
used = [t.observe({4: 805}, 400 + i, True) for i in range(3)]
check(f"a few more in the field: picked up, they make up for some used ({used[-1]})", used[-1] == {4: -5})
used = [t.observe({4: 1305}, 500 + i, True) for i in range(3)]
check("500 more at once: bought, not counted", all(u == {} for u in used) and t.counts[4] == 1305)
used = [t.observe({4: 1310}, 900 + i, True, in_town=True) for i in range(3)]
check("any rise in town is bought, not counted", all(u == {} for u in used) and t.counts[4] == 1310)

# with the pickup lines read (pickwatch.py), pickups are known: a rise is a pickup only as far as they say
t = m.PotTracker()
for i in range(3):
    t.observe({4: 846}, i, True)
t.picked_up(4, 2)
used = [t.observe({4: 848}, 60 + i, True) for i in range(3)]
check(f"2 lemons picked up (their pickup lines read): the rise of 2 is them ({used[-1]})", used[-1] == {4: -2})
used = [t.observe({4: 851}, 120 + i, True) for i in range(3)]
check("a rise with no pickup lines behind it is bought, not picked up", all(u == {} for u in used) and t.counts[4] == 851)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
