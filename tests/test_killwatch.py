"""The kill watcher (killwatch.py): it notices new EXP lines in the game's message area, from real
lines out of a game screenshot ("You received EXP (+38)", "Bonus EXP for PARTY (+11)") moving the
way the game moves them. Run from the project folder:  py -3 tests/test_killwatch.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cv2  # noqa: E402

import killwatch as kw  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


FRAMES = ROOT / "tests" / "frames"
both = cv2.imread(str(FRAMES / "kill_lines.png"))
exp_line, party_line = both[:21], both[21:]
scenery = cv2.imread(str(FRAMES / "kill_scenery.png"))
TEXT_H, SLACK, PITCH = 12, 40, 21

check("the EXP lines are found in the real picture, both of them",
      len(kw.find_lines(both, TEXT_H, SLACK)) == 2)
check("nothing is found in plain scenery (yellow-brown bark included)", kw.find_lines(scenery, TEXT_H, SLACK) == [])


def frame(stack):
    """The message area with these lines, newest at the bottom, as the game shows them."""
    f = scenery.copy()
    for i, line in enumerate(reversed(stack)):
        y = 150 - i * PITCH
        if y < 0:
            break
        f[y:y + line.shape[0], :] = line
    return f


w = kw.KillWatcher()
stack = [exp_line, party_line]
steps = [("what's there at the first look is old", 0, list(stack))]
stack += [exp_line, party_line]
steps.append(("a kill in a party: its EXP line and its party-bonus line", 2, list(stack)))
stack += [exp_line, party_line]
steps.append(("the same kill again: identical lines still count", 2, list(stack)))
steps.append(("nothing new: nothing counted", 0, list(stack)))
stack = stack[1:]
steps.append(("the oldest line fades away: nothing counted", 0, list(stack)))
stack += [exp_line]
steps.append(("a solo kill: one line", 1, list(stack)))
stack += [party_line, exp_line, party_line]
steps.append(("three lines at once", 3, list(stack)))
for name, want, st in steps:
    got = w.look(frame(st), TEXT_H, SLACK)
    check(f"{name} ({got})", got == want)
w2 = kw.KillWatcher()
w2.look(frame([]), TEXT_H, SLACK)
check("messages appearing where there were none count", w2.look(frame([exp_line]), TEXT_H, SLACK) == 1)
# Two identical kills between two looks look exactly like one (the lines move up by one kill or two:
# the picture is the same). They're counted as one: a single hop, but no EXP is lost, because what
# a line is worth is learned from the EXP actually gained.
w3 = kw.KillWatcher()
s3 = [exp_line, party_line] * 2
w3.look(frame(s3), TEXT_H, SLACK)
check("two identical kills between two looks: counted as at least one",
      w3.look(frame(s3 + [exp_line, party_line] * 2), TEXT_H, SLACK) >= 2)
check("the message area is the right of the game, above its bottom UI",
      kw.message_area((0, 0, 1920, 1080)) == (1152, 540, 768, 453))

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
