"""Tests for the tracking and logging logic (no game or screen needed).

Run from the project folder:  py -3 tests/test_logic.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def new_log():
    folder = Path(tempfile.mkdtemp())
    return m.AutoLog(15, folder / "log.json", folder / "log.txt"), folder / "log.json"


def farm(log, t0, seconds, party=1, map_name="Rocky Road II", level=27, every=3, gap_at=None, gap=0, job=None, hidden=False):
    """100 EXP every `every` seconds (optionally with a pause); returns (end time, logs written)."""
    t, nxt, logged = t0, t0, []
    while t < t0 + seconds:
        paused = gap_at is not None and gap_at <= t - t0 < gap_at + gap
        if t >= nxt:
            if not paused:
                log.gain(t, 100)
            nxt = t + every
        entry = log.tick(t, map_name, level, party, None, job, hidden=hidden)
        if entry:
            logged.append(round(t - t0))
        t += 0.5
    return t, logged


# ---- the log
# ---- the minimap's name is hidden: nothing is saved under a map name that may be stale
log, _ = new_log()
t, _ = farm(log, 0, 290)
t, due = farm(log, t, 50, hidden=True)                       # the 5-minute mark falls inside the hidden stretch
t, _ = farm(log, t, 10, map_name="The Swamp of Despair I")   # the name is back: a different map
check("a stretch that comes due while the name is hidden isn't saved under the old map",
      not due and not log.entries and log.start is not None and log.block_map == "The Swamp of Despair I")

log, _ = new_log()
t, _ = farm(log, 0, 290)
t, due = farm(log, t, 50, hidden=True)
t, later = farm(log, t, 10)                                  # the name is back: the same map
check("...on the same map it is saved once the name is back, and the hidden time counts",
      not due and later and len(log.entries) == 1 and log.entries[0]["seconds"] >= 335
      and log.entries[0]["map"] == "Rocky Road II")

log, _ = new_log()
t, _ = farm(log, 0, 880)                                     # two 5-minute stretches are saved on the way
n = len(log.entries)
t, due = farm(log, t, 40, hidden=True)                       # the 15-minute run comes due while hidden
t, _ = farm(log, t, 10, map_name="The Swamp of Despair I")
check("a 15-minute run that comes due while the name is hidden isn't saved if the map turns out different",
      n == 1 and not log.runs)

log, _ = new_log()
t, _ = farm(log, 0, 880)
t, _ = farm(log, t, 40, hidden=True)
t, _ = farm(log, t, 10)
check("...but on the same map it is saved once the name is back", len(log.runs) == 1 and log.runs[0]["map"] == "Rocky Road II")

log, path = new_log()
_, logged = farm(log, 0, 660)
check(f"logs after 5 min of non-stop EXP, then updates at 10 min ({logged})", logged == [300, 600])
rate = log.entries[0]["exp"] / log.entries[0]["seconds"] * 3600
check(f"EXP/hr is right (120,000, got {rate:,.0f})", abs(rate - 120000) < 2000)

log, _ = new_log()
_, logged = farm(log, 0, 540, gap_at=180, gap=20)
check(f"a 20 s break restarts the 5-minute clock ({logged})", logged and 495 <= logged[0] <= 505)

log, _ = new_log()
_, logged = farm(log, 0, 330, gap_at=100, gap=10)
check(f"a 10 s break doesn't ({logged})", logged and 300 <= logged[0] <= 301)

log, _ = new_log()
t, a = farm(log, 0, 200, party=1)
t, b = farm(log, t, 400, party=4)
check("changing party size restarts the clock", not a and b and 300 <= b[0] <= 305)

log, _ = new_log()
t, _ = farm(log, 0, 310, level=27)
farm(log, t + 60, 310, level=28)
check("a new level gets its own line", len(log.entries) == 2)

a, path = new_log()
b = m.AutoLog(15, path, path.with_suffix(".txt"))
farm(a, 0, 310, party=5)
farm(b, 1000, 310, party=4)
check("two copies of the app can't erase each other's lines",
      sorted(e["party"] for e in json.loads(path.read_text())["entries"]) == [4, 5])

e = {"date": "2026-10-11", "map": "The Land of Wild Boar II", "level": 29, "party": 4,
     "exp": 51862, "seconds": 3600, "mesos": 32713, "mesos_seconds": 3600}
check("full line format", m.format_entry(e) ==
      "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - 4ppl party - 51,862exp/hr - 32,713mesos/hr - 1 h 00 min")
check("table row format", m.log_row(e)[1:] == ("The Land of Wild Boar II", 29, "", "4ppl", "1h 00m", "51,862", "32,713", ""))
check("the class is in the line", m.format_entry({**e, "class": "Bandit"}).startswith(
    "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - Bandit - 4ppl party"))

# ---- party sizes of one map share a row in the log
five = {**e, "party": 5, "exp": 50000, "seconds": 1800, "mesos": 10000, "mesos_seconds": 1800}
solo = {**e, "party": 1}
other_level = {**e, "party": 3, "level": 30}
other_day = {**e, "party": 3, "date": "2026-10-12"}
old_line = {k: v for k, v in e.items() if k not in ("party", "mesos", "mesos_seconds")}  # from before party tracking
groups = m.party_groups([five, solo, e, other_level, other_day, old_line])
shape = [[x["party"] if "party" in x else "-" for x in g] for _, g in groups]
check(f"a map's party sizes share a row, smallest first; solo, other levels and days don't ({shape})",
      shape == [[4, 5], [1], [3], [3], ["-"]])
row = m.group_row(groups[0][1])
check(f"the shared row is over all their time ({row[5:]})",
      row[4:] == ("4–5ppl ▸", "1h 30m", f"{(51862 + 50000) / 5400 * 3600:,.0f}", f"{(32713 + 10000) / 5400 * 3600:,.0f}", ""))
check("open, its arrow points down", m.group_row(groups[0][1], True)[4] == "4–5ppl ▾")
check("a party size under it shows just its own numbers",
      m.log_row(five, part=True) == ("", "", "", "", "5ppl", "30m", "100,000", "20,000", ""))
check("the lines themselves aren't changed", five["exp"] == 50000 and e["seconds"] == 3600)

# ---- class, seals and 15-minute runs
log, _ = new_log()
t, _ = farm(log, 0, 310, job="Bandit")
farm(log, t + 60, 310, job="Hunter")
check("a different class gets its own line", sorted(e["class"] for e in log.entries) == ["Bandit", "Hunter"])

log, path = new_log()
farm(log, 0, 310)
check("a line the tracker wrote is sealed", log.sealed(log.entries[0]) and not log.edited(log.entries[0]))
data = json.loads(path.read_text())
data["entries"][0]["exp"] *= 2                                   # someone edits the file by hand
path.write_text(json.dumps(data))
log = m.AutoLog(15, path, path.with_suffix(".txt"))
check("a line edited by hand is marked as edited", log.edited(log.entries[0]))
farm(log, 1000, 310)
check("more EXP added to an edited line doesn't make it trusted again",
      len(log.entries) == 1 and log.edited(log.entries[0]))
data["entries"].append(dict(data["entries"][0], level=28, seal="0" * 24))   # a made-up line
path.write_text(json.dumps(data))
check("a line added by hand isn't trusted either", m.AutoLog(15, path, path.with_suffix(".txt")).edited(
    [e for e in json.loads(path.read_text())["entries"] if e["level"] == 28][0]))
log, _ = new_log()
farm(log, 0, 310)
log.rename(log.entries[0], "Renamed Map")
check("renaming a map in the app keeps the line sealed (the name as read stays on record)",
      log.sealed(log.entries[0]) and log.entries[0]["map_read"] == "Rocky Road II")

log, path = new_log()
farm(log, 0, 950, job="Bandit")
runs = log.runs
check(f"15 minutes of non-stop farming is saved as a sealed run ({len(runs)})",
      len(runs) == 1 and abs(runs[0]["seconds"] - 900) < 2 and log.sealed(runs[0], m._SEALED_RUN)
      and abs(runs[0]["exp"] / runs[0]["seconds"] * 3600 - 120000) < 2000)
check("the log line counts its runs", log.runs_done("Rocky Road II", 1, "Bandit") == 1)
data = json.loads(path.read_text())
data["runs"][0]["exp"] *= 3                                      # a run edited by hand
path.write_text(json.dumps(data))
check("an edited run isn't counted", m.AutoLog(15, path, path.with_suffix(".txt")).runs_done(
    "Rocky Road II", 1, "Bandit") == 0)
log, _ = new_log()
farm(log, 0, 3650)
check(f"an hour of farming makes 4 runs, one after another ({len(log.runs)})",
      len(log.runs) == 4 and all(abs(r["seconds"] - 900) < 2 for r in log.runs))
log, _ = new_log()
t, _ = farm(log, 0, 500, level=30)
farm(log, t, 500, level=31)
check("a level-up doesn't stop a run (the run records both levels)",
      len(log.runs) == 1 and (log.runs[0]["level_start"], log.runs[0]["level_end"]) == (30, 31))
log, _ = new_log()
t, _ = farm(log, 0, 600, party=1)
farm(log, t, 600, party=4)
check("a party-size change restarts the run", len(log.runs) == 0)
log, _ = new_log()
farm(log, 0, 1000, gap_at=500, gap=20)
check("a break restarts the run", len(log.runs) == 0)
log, _ = new_log()
t, _ = farm(log, 0, 600)
farm(log, t, 600, map_name="The Swamp of Despair I")
check("a new map restarts the run", len(log.runs) == 0)
# ---- hotkeys
check("hotkeys by default: Shift+1 start, Shift+2 reset, Shift+3 the repot bell, Shift+5 move the card",
      m.DEFAULT_HOTKEYS == {"start": "Shift+1", "reset": "Shift+2", "repot": "Shift+3", "edit": "Shift+5"})
check("a hotkey needs Ctrl, Alt or Shift (or an F key), so it can't block plain typing",
      m.parse_hotkey("Shift+1") == (4, 0x31) and m.parse_hotkey("F5") == (0, 0x74)
      and m.parse_hotkey("A") is None and m.parse_hotkey("Win+1") is None and m.parse_hotkey("Shift+!") is None)
check("keys pressed in Settings get the right names", m.hotkey_text(False, False, True, 0x31) == "Shift+1"
      and m.hotkey_text(True, True, False, 0x5A) == "Ctrl+Alt+Z" and m.hotkey_text(False, False, False, 0x41) is None)
m.CONFIG_PATH.write_text(json.dumps({"hotkeys": {"start": "Ctrl+Q", "reset": "", "skip": "nonsense", "compact": 5},
                                     "ui_scale": 3, "compact": "yes"}))
cfg = m.load_config()
check(f"hotkeys from the settings file: kept if valid or off, else the default ({cfg['hotkeys']})",
      cfg["hotkeys"] == {"start": "Ctrl+Q", "reset": "", "repot": "Shift+3", "edit": "Shift+5"})
check("a size or compact setting edited by hand can't break the window", cfg["ui_scale"] == 100 and cfg["compact"] is False)
m.CONFIG_PATH.write_text(json.dumps({"compact_shape": "triangle", "seen_version": 5}))
cfg = m.load_config()
check("a compact shape that doesn't exist falls back to Square, and a damaged seen version to none",
      cfg["compact_shape"] == "square" and cfg["seen_version"] == "")
m.CONFIG_PATH.write_text(json.dumps({"compact_shape": "bar", "seen_version": "0.9.9-beta"}))
cfg = m.load_config()
check("a chosen compact shape and the last seen version are kept", cfg["compact_shape"] == "bar" and cfg["seen_version"] == "0.9.9-beta")
check(f"the right OS backend is made for {sys.platform} ({type(m.PLATFORM).__name__})",
      type(m.PLATFORM).__name__ == {"win32": "WindowsPlatform", "darwin": "MacPlatform"}.get(sys.platform))
try:
    m.create_platform("plan9")
    unsupported = False
except RuntimeError:
    unsupported = True
check("an OS the overlay doesn't run on is turned down clearly", unsupported)
check("the game isn't found when it isn't running (no crash looking)",
      m.PLATFORM.find_game_window() is None or m.PLATFORM.client_rect(m.PLATFORM.find_game_window()) is not None)
if sys.platform == "win32":
    from platforms import windows
    pressed = []
    hk = m.PLATFORM.create_hotkeys(pressed.append)
    hk.in_front = lambda: True                               # as if the game were in front
    hk.set({"start": "Ctrl+Alt+Shift+F12"})
    import time as _time
    _time.sleep(0.8)                                         # it registers within a quarter second
    ctypes_ok = windows._user32.PostThreadMessageW(hk.thread_id, 0x0312, 1, 0)   # as if the keys were pressed
    _time.sleep(0.3)
    check(f"a hotkey press reaches the overlay ({pressed}, problems: {hk.problems})", ctypes_ok and pressed == ["start"])
    hk.in_front = lambda: False                              # another program in front: off
    _time.sleep(0.6)
    windows._user32.PostThreadMessageW(hk.thread_id, 0x0312, 1, 0)
    _time.sleep(0.3)
    check("hotkeys are off while another program is in front", pressed == ["start"])
if sys.platform == "darwin":
    from platforms import macos
    W = macos.MacWindow
    check("on macOS the game is told apart from browsers and the overlay itself",
          macos.is_game(W(1, 10, "wine64-preloader", "MapleStory", 0, (0, 0, 800, 600), True), 99)
          and macos.is_game(W(2, 11, "MapleStory.exe", "", 0, (0, 0, 800, 600), True), 99)
          and not macos.is_game(W(3, 12, "Google Chrome", "MapleStory Classic guide", 0, (0, 0, 800, 600), True), 99)
          and not macos.is_game(W(4, 99, "Python", "MapleStory", 0, (0, 0, 800, 600), True), 99)
          and not macos.is_game(W(5, 13, "wine64-preloader", "MapleStory", 25, (0, 0, 800, 600), True), 99))
    check("macOS windows can be listed", isinstance(macos.window_list(), list))
check("how long each line was measured over", [m.fmt_logged(s) for s in (300.4, 1500, 3900)] == ["5 min", "25 min", "1 h 05 min"]
      and [m.fmt_logged(s, short=True) for s in (300.4, 1500, 3900)] == ["5m", "25m", "1h 05m"])

log, _ = new_log()
log.entries = [{"date": "2026-10-08", "map": "Kerning City", "level": 27, "exp": 1, "seconds": 1},
               {"date": "2026-10-08", "map": "Rocky Road II", "level": 27, "exp": 1, "seconds": 1}]
check("a misread map name is matched to the known one", log.canonical("Ketning City") == "Kerning City")
check("Rocky Road I and II stay different maps", log.canonical("Rocky Road I") == "Rocky Road I")
check("'Il' at the end is read as 'II'", m._clean_map_name("Rocky Road Il") == "Rocky Road II")
check("the level comes from the EXP table", m.level_for_req(63666) == 27 and m.level_for_req(60000) is None)

# ---- updating: logs and settings from every released version open with nothing lost
import shutil
real_config = m.CONFIG_PATH
for old in sorted((Path(__file__).parent / "upgrade").iterdir()):
    folder = Path(tempfile.mkdtemp())
    shutil.copy2(old / "exp_log.json", folder / "log.json")
    before = json.loads((old / "exp_log.json").read_text(encoding="utf-8"))
    log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
    same = [all(a.get(k) == b.get(k) for k in ("date", "map", "level", "party", "exp", "seconds", "mesos", "class", "note"))
            for a, b in zip(before["entries"], log.entries)]
    m.CONFIG_PATH = old / "config.json"
    cfg = m.load_config()
    m.CONFIG_PATH = real_config
    check(f"updating from {old.name}: every log line, run, rename and setting is kept",
          len(log.entries) == len(before["entries"]) and all(same) and not any(log.edited(e) for e in log.entries)
          and len(log.runs) == len(before.get("runs", [])) and all(log.sealed(r, m._SEALED_RUN) for r in log.runs)
          and log.aliases == before["aliases"] and not list(folder.glob("exp_log.damaged-*"))
          and (cfg["pos"], cfg["transparency"], cfg["gap_seconds"], cfg["topmost"]) == ([5300, 800], 30, 20, False))
    # ...and carrying on farming afterwards, with the old lines (and any notes on them) left as they were
    farm(log, 0, 310, map_name="Updated Map", level=41)
    after = json.loads((folder / "log.json").read_text(encoding="utf-8"))["entries"]
    old_notes = {(e["date"], e["map"], e["level"], e.get("party")): e.get("note") for e in before["entries"]}
    now_notes = {(e["date"], e["map"], e["level"], e.get("party")): e.get("note") for e in after}
    check(f"farming after updating from {old.name} keeps every old line and note",
          all(k in now_notes and now_notes[k] == v for k, v in old_notes.items())
          and len(after) == len(before["entries"]) + 1)
check("the newest upgrade fixture has notes in it (so keeping them is really tested)",
      sum("note" in e for e in json.loads((Path(__file__).parent / "upgrade" / "0.9.10-beta" / "exp_log.json")
                                          .read_text(encoding="utf-8"))["entries"]) >= 3)


# ---- personal notes on log lines: only on this PC, and never lost
import inspect
log, path = new_log()
t, _ = farm(log, 0, 310, party=4)
e1 = log.entries[0]
updated = e1["updated"]
check("a line starts with no note", m.note_of(e1) == "" and m.log_row(e1)[8] == "" and "note" not in e1)
check("writing a note says it changed", log.set_note([e1], "  Great with a Priest.\r\nBring 200 pots \n") is True)
on_disk = json.loads(path.read_text(encoding="utf-8"))["entries"][0]
check(f"the note is in the log file, tidied up ({on_disk.get('note')!r})", on_disk.get("note") == "Great with a Priest.\nBring 200 pots")
e1 = log.entries[0]
check("the line is as trusted as before (a note isn't one of the sealed fields)", log.sealed(e1) and not log.edited(e1))
check("a note doesn't move the line in the log (it isn't an update to it)", e1["updated"] == updated)
check("the Note column shows a mark", m.log_row(e1)[8] == m.NOTE_MARK)
check("notes aren't in the log's copy or in copied lines",
      "Priest" not in path.with_suffix(".txt").read_text(encoding="utf-8") and "Priest" not in m.format_entry(e1)
      and "Priest" not in log.text_path.read_text(encoding="utf-8"))
log2 = m.AutoLog(15, path, path.with_suffix(".txt"))
check("the note is there after closing and opening the overlay", m.note_of(log2.entries[0]) == "Great with a Priest.\nBring 200 pots")
check("writing the same note again changes nothing", log2.set_note(log2.entries, "Great with a Priest.\nBring 200 pots") is False)
t, _ = farm(log2, t + 60, 310, party=4)
check("more farming on that line keeps its note (and adds to the line)",
      len(log2.entries) == 1 and m.note_of(log2.entries[0]) == "Great with a Priest.\nBring 200 pots"
      and log2.entries[0]["exp"] > e1["exp"] and log2.sealed(log2.entries[0]))
check("an empty note takes it off", log2.set_note(log2.entries, "  \n ") is True and "note" not in log2.entries[0])
check("a note can't be longer than the limit", (log2.set_note(log2.entries, "x" * 9000), len(log2.entries[0]["note"]))[1] == m.NOTE_MAX)
check("control characters and lone surrogates are taken out of notes, new lines and tabs kept",
      m.clean_note("a\x00b\x07c\ud800d\r\ne\tf\x7f") == "abcd\ne\tf" and m.clean_note(None) == "" and m.clean_note(5) == "")

# a map's party sizes shown as one line share one note
log, path = new_log()
t, _ = farm(log, 0, 310, party=4)
t, _ = farm(log, t + 60, 310, party=5)
four, five_ = sorted(log.entries, key=lambda e: e["party"])
check("two party sizes get one note together", log.set_note([four, five_], "Pots: 60 red") is True
      and [m.note_of(e) for e in log.entries] == ["Pots: 60 red"] * 2)
check("a line's party sizes with different notes show each once; the same note shows once",
      m.notes_of([{"note": "A"}, {"note": "B"}, {"note": "A"}, {}]) == "A\n\nB" and m.notes_of([{}, {"note": ""}]) == "")
check("a mark on the shared line if any of its party sizes has a note",
      m.group_row([{**four, "note": ""}, {**five_, "note": "Only on this one"}])[8] == m.NOTE_MARK
      and m.group_row([{**four, "note": "Only on this one"}, {**five_}])[8] == m.NOTE_MARK)
log.set_note([log.entries[0]], "")
log.set_note([log.entries[1]], "")
check("...and none when none do", m.group_row(log.entries)[8] == "")

# another copy of the overlay (or an older one) can't erase a note
a, path = new_log()
farm(a, 0, 310, party=2)
b = m.AutoLog(15, path, path.with_suffix(".txt"))               # a second copy, which doesn't know about the note
a.set_note(a.entries, "Written in the first copy")
farm(b, 5000, 310, party=3, map_name="Ant Tunnel I")
kept = {e["party"]: m.note_of(e) for e in json.loads(path.read_text(encoding="utf-8"))["entries"]}
check(f"a note written while another copy had the log open is kept ({kept})",
      kept == {2: "Written in the first copy", 3: ""})

# renaming a map so two lines become one keeps both notes
log, path = new_log()
t, _ = farm(log, 0, 310, map_name="Ant Tunnel I")
t, _ = farm(log, t + 60, 310, map_name="Ant Tunel I")             # the same map, misread
log.set_note([x for x in log.entries if x["map"] == "Ant Tunnel I"], "Mobs drop etc")
log.set_note([x for x in log.entries if x["map"] == "Ant Tunel I"], "Crowded at night")
log.rename([x for x in log.entries if x["map"] == "Ant Tunel I"][0], "Ant Tunnel I")
check(f"renaming two lines into one keeps both notes ({[m.note_of(x) for x in log.entries]})",
      len(log.entries) == 1 and m.note_of(log.entries[0]) == "Mobs drop etc\n\nCrowded at night"
      and log.sealed(log.entries[0]))
before = log.entries[0]
t, _ = farm(log, t + 60, 310, map_name="Other Map")
log.rename(log.entries[0], "Renamed Map") if log.entries[0]["map"] == "Other Map" else log.rename(log.entries[1], "Renamed Map")
check("renaming a map keeps its note", any(m.note_of(x) == "Mobs drop etc\n\nCrowded at night" for x in log.entries))
log.delete([x for x in log.entries if x["map"] == "Renamed Map"][0])
check("deleting a line deletes its note, and only its note",
      [m.note_of(x) for x in log.entries] == ["Mobs drop etc\n\nCrowded at night"])

# a log edited by hand, or written by a future version, with odd notes: nothing crashes, nothing is thrown away
folder = Path(tempfile.mkdtemp())
odd = [123, ["a", "b"], None, "x" * 50000, "a\x00b\ud800", {"text": "later versions might"}, ""]
lines = [{"date": "2026-10-1%d" % i, "map": "Map %d" % i, "level": 30, "party": 1, "exp": 5000, "seconds": 300,
          "note": n} for i, n in enumerate(odd)]
(folder / "log.json").write_text(json.dumps({"version": 3, "entries": lines}), encoding="utf-8")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
shown = [(m.note_of(x), m.log_row(x)[8]) for x in log.entries]
check("odd notes load, and can be shown", len(log.entries) == len(odd) and all(isinstance(n, str) and len(n) < 60000 for n, _ in shown)
      and not list(folder.glob("exp_log.damaged-*")))
log._change(lambda: None)
t, _ = farm(log, 0, 310, map_name="Another Map")
after = {x["map"]: x for x in json.loads((folder / "log.json").read_text(encoding="utf-8"))["entries"]}
check("odd notes are left exactly as they were when the log is saved for another reason",
      all("note" in after[f"Map {i}"] and after[f"Map {i}"]["note"] == n for i, n in enumerate(odd)))

# local only: nothing in the overlay can go online
src = inspect.getsource(m)
check("local only: no web requests, browser links or sharing anywhere in the overlay",
      all(w not in src for w in ("urlopen(", "webbrowser", "urllib", "requests.", "post_runs", "check_for_update", "https://")))
import socket  # noqa: E402
try:
    socket.create_connection(("example.com", 80), timeout=2)
    reached = True
except OSError as e:
    reached = "local only" not in str(e)
check("local only: a connection to the internet is refused", not reached)
check("the bug report never includes notes",
      not any(w in inspect.getsource(m.App._report_text) for w in ("note_of", "notes_of", '"note"', "autolog.entries")))

# a copy of the log is kept for each day, so a recent one is always there
folder = Path(tempfile.mkdtemp())
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
t, _ = farm(log, 0, 310)
check("the first save has nothing to back up yet", not (folder / "backups").exists())
log.set_note(log.entries, "kept in the backup too")
today = folder / "backups" / f"exp_log-{m.date.today().isoformat()}.json"
check("the first save of a day keeps a copy of the log as it was", today.exists()
      and json.loads(today.read_text(encoding="utf-8"))["entries"][0].get("note") is None)
size = today.stat().st_size
log.set_note(log.entries, "changed again")
check("...only once a day", len(list((folder / "backups").glob("*.json"))) == 1 and today.stat().st_size == size)
for d in range(1, 10):                                            # nine older days
    (folder / "backups" / f"exp_log-2026-01-{d:02d}.json").write_text(json.dumps({"entries": [{"x": d}]}), encoding="utf-8")
today.unlink()
log.set_note(log.entries, "and again")
kept = sorted(x.name for x in (folder / "backups").glob("*.json"))
check(f"only the latest {m.BACKUPS_KEPT} days are kept ({len(kept)} files)",
      len(kept) == m.BACKUPS_KEPT and kept[-1] == today.name and "exp_log-2026-01-01.json" not in kept)
folder = Path(tempfile.mkdtemp())
(folder / "log.json").write_text('{"entries": [{"date": "2026', encoding="utf-8")        # damaged
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
farm(log, 0, 310)
check("a damaged log isn't taken for a backup (it has its own copy)",
      not (folder / "backups").exists() and len(list(folder.glob("exp_log.damaged-*.json"))) == 1)

# ---- damaged files
folder = Path(tempfile.mkdtemp())
(folder / "log.json").write_text('{"entries": [{"date": "2026-10-1', encoding="utf-8")   # cut off
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
farm(log, 0, 310)
check("a damaged log is copied before anything is saved over it",
      len(list(folder.glob("exp_log.damaged-*.json"))) == 1 and len(log.entries) == 1)
folder = Path(tempfile.mkdtemp())
good = {"date": "2026-10-11", "map": "Kerning City", "level": 27, "party": 1, "exp": 100, "seconds": 300}
(folder / "log.json").write_text(json.dumps({"version": 2, "entries": [good, {"map": 5}]}), encoding="utf-8")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
check("a damaged line is left out (with a copy kept), the rest is kept",
      log.entries == [good] and len(list(folder.glob("exp_log.damaged-*.json"))) == 1)
m.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": "15", "transparency": 400, "pos": "x",
                                     "log_size": [5], "mode": "region"}), encoding="utf-8")
cfg = m.load_config()
check("damaged settings fall back to the defaults",
      cfg["gap_seconds"] == 120 and cfg["transparency"] == m.MAX_TRANSPARENCY and "pos" not in cfg
      and "log_size" not in cfg and cfg["mode"] == "window")
m.CONFIG_PATH.write_text("[1, 2", encoding="utf-8")
check("an unreadable settings file gives the defaults", m.load_config()["topmost"] is True)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": 15}), encoding="utf-8")
check("1.0.2's 15 s pause (cutting out every respawn wait) becomes 2 minutes", m.load_config()["gap_seconds"] == 120)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": 15, "pause_rule": 2}), encoding="utf-8")
check("...but 15 s picked since then is kept", m.load_config()["gap_seconds"] == 15)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": 45}), encoding="utf-8")
check("...and so is any other time picked before", m.load_config()["gap_seconds"] == 45)

# ---- mesos
t = m.MesoTracker()
for v in (9820, 9820):
    t.observe(v, 0, 0, True)
check("the first count needs 3 readings that agree exactly", t.value is None)
for v in (9820, 9850, 9850):
    t.observe(v, 0, 0, True)
check("mesos picked up are counted", t.gained == 30)
t.observe(9999, 0, 0, True)
check("a one-off reading isn't counted yet", t.value == 9850)
for v in (820, 820, 820, 9880, 9880, 9880):
    t.observe(v, 0, 0, True)
check(f"a lost digit and its correction cancel out: only the 30 picked up meanwhile counts ({t.gained}, spent {t.spent})",
      t.gained == 60 and t.spent == 0 and t.value == 9880)
t2 = m.MesoTracker()
for v in (9820, 9820, 9820, 9850, 9850, 9700, 9700, 9900, 9900):
    t2.observe(v, 0, 0, True)
check(f"mesos gained and spent are kept apart, and add up to the net ({t2.earned}, {t2.spent}, {t2.gained})",
      (t2.earned, t2.spent, t2.gained) == (230, 150, 80))
# the inventory closed: mesos/hr holds (worked out up to the last reading), and what's picked up meanwhile counts
t3 = m.MesoTracker()
for el, v in ((0, 100000), (1, 100000), (2, 100000), (300, 103000), (301, 103000)):   # +3,000 in about 5 minutes
    t3.observe(v, el, el, True)
rate = t3.per_hour(301)
check(f"mesos/hr while the inventory is open ({rate:,.0f})", abs(rate - 3000 / 299 * 3600) < 1)
check(f"closed for 10 minutes: mesos/hr holds instead of sinking ({t3.per_hour(901):,.0f})",
      abs(t3.per_hour(901) - rate) < 1 and t3.per_5min(901) == t3.per_5min(301))
check(f"...and the mesos picked up meanwhile are guessed at that rate ({t3.guess_since_seen(901):,.0f})",
      t3.unseen(901) == 600 and abs(t3.guess_since_seen(901) - rate * 600 / 3600) < 1)
for el, v in ((901, 108000), (902, 108000)):                                 # opened again: +5,000 meanwhile
    t3.observe(v, el, el, True)
check(f"opened again: everything picked up meanwhile counts, over all that time ({t3.per_hour(902):,.0f})",
      t3.gained == 8000 and abs(t3.per_hour(902) - 8000 / 900 * 3600) < 1 and t3.unseen(902) == 0)
for v in (25000, 25000, 25000):
    t.observe(v, 0, 0, True)
check(f"selling loot (a big jump) counts, once read 3 times ({t.gained}, earned {t.earned})",
      t.gained == 60 + 15120 and t.earned == 60 + 15120)
for v in (250000,) * 7:
    t.observe(v, 0, 0, True)
check("an extra digit (10x) needs 8 readings in a row, not 3", t.value == 25000)
t = m.MesoTracker()
for v in (20000, 20000, 20000, 15001, 15001, 20000, 20000, 20040, 20040):
    t.observe(v, 0, 0, True)
check(f"a misread twice in a row and its correction cancel out (gained {t.gained})", t.gained == 40)

for before, after, gained in ((9, 10, 1), (99, 100, 1), (9999, 10000, 1),
                              (9000, 10000, 1000), (10000, 9000, -1000),
                              (9980, 10020, 40), (999999, 1000000, 1)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 2:
        t.observe(v, 0, 0, True)
    check(f"a digit rollover still needs 3 exact readings ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 60, 60, True)
    check(f"a small digit rollover counts the gain ({before} to {after}: {t.gained})",
          t.value == after and t.gained == gained and abs(t.per_hour(60) - gained * 60) < 1e-6
          and t.per_5min(60) == gained * 5)
    for v in (before,) * 3:
        t.observe(v, 60, 60, True)
    check(f"a digit rollover and its correction cancel out ({after} to {before})",
          t.value == before and t.gained == 0)

for before, after, nearby, gained in ((9980, 10020, 10010, 40), (10020, 9980, 9990, -40)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (nearby, after, nearby, after, after):
        t.observe(v, 0, 0, True)
    check(f"fluctuating readings across a digit boundary aren't enough ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 0, 0, True)
    check(f"a digit rollover counts once the readings agree exactly ({before} to {after})",
          t.value == after and t.gained == gained)

for before, after in ((0, 1), (0, 10), (9, 19), (99, 199), (9999, 19999),
                      (19999, 9999), (8, 10), (89, 100), (8999, 10000)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 3:
        t.observe(v, 0, 0, True)
    front = str(max(before, after)).endswith(str(min(before, after))) and min(before, after) > 0
    check(f"zero, extra or lost digits, and wider jumps count once read 3 times, unless one is the end of "
          f"the other: front digits lost ({before} to {after})",
          t.value == (before if front else after) and t.gained == (0 if front else after - before))
    for v in (before,) * 3:
        t.observe(v, 0, 0, True)
    check(f"...and correcting one takes it back: it was a misread ({after} to {before})",
          t.value == before and t.gained == 0 and t.earned == 0 and t.spent == 0)

for before, after in ((9, 90), (99, 990), (9999, 99990), (10, 1), (100, 10), (10000, 1000)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 7:
        t.observe(v, 0, 0, True)
    check(f"a tenfold change still waits for 8 exact readings ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 0, 0, True)
    check(f"a confirmed tenfold change still rebases ({before} to {after})",
          t.value == after and t.gained == 0)

# the front digits lost: 1,251,272 read as 1,272 (the box found too narrow)
t = m.MesoTracker()
for v in (1251272,) * 3 + (1272,) * 7:
    t.observe(v, 0, 0, True)
check("front digits lost (1,251,272 read as 1,272) need 8 readings in a row", t.value == 1251272)
t.observe(1272, 0, 0, True)
for v in (1400, 1400):                                          # +128 picked up meanwhile
    t.observe(v, 10, 10, True)
for v in (1251400,) * 8:                                        # read right again
    t.observe(v, 20, 20, True)
check(f"...and aren't spent, nor earned when they're back: only what was picked up counts "
      f"(gained {t.gained}, earned {t.earned}, spent {t.spent})",
      t.value == 1251400 and t.gained == 128 and t.earned == 128 and t.spent == 0)
# any other big misread: taken back when the count comes back, even with mesos picked up meanwhile
t = m.MesoTracker()
for v in (1250000,) * 3 + (620000,) * 3 + (620500,) * 2 + (1250500,) * 3:
    t.observe(v, 30, 30, True)
check(f"a big misread is taken back when the right count is back, with mesos picked up meanwhile "
      f"(gained {t.gained}, earned {t.earned}, spent {t.spent})",
      t.gained == 500 and t.earned == 500 and t.spent == 0)
t = m.MesoTracker()
for v in (1250000,) * 3 + (620000,) * 3:                        # a real buy...
    t.observe(v, 30, 30, True)
for v in (1250000,) * 3:                                        # ...earned back over 10 minutes
    t.observe(v, 630, 630, True)
check(f"a real big buy earned back later isn't taken back (spent {t.spent}, earned {t.earned})",
      t.spent == 630000 and t.earned == 630000)

t = m.MesoTracker()
for v in (9980,) * 3:
    t.observe(v, 0, 0, True)
t.pause()
for v in (10020,) * 3:
    t.observe(v, 30, 0, False)
check("a digit rollover while paused isn't counted", t.value == 10020 and t.gained == 0)
t.observe(10020, 60, 0, True)
for v in (10030,) * 2:
    t.observe(v, 90, 30, True)
check("mesos picked up after resuming still count", t.value == 10030 and t.gained == 10)

# ---- EXP outliers
def exp_tracker(e=47850, p=50.0):
    tr = m.ExpTracker(); tr.start()
    for _ in range(3):
        tr.observe([(e, p)], True)                       # Lv 30, 50%
    return tr

tr = exp_tracker()
for e in (86130, 86130, 47850, 47850, 47950, 47950):     # too high twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"the real number coming back after a misread isn't taken for a level-up "
      f"(gained {tr.gained:.0f}, levels {tr.levels})", tr.levels == 0 and tr.gained == 100)
tr = exp_tracker(95500, 99.79)
for e in (150, 150, 270):                                # a real level-up into Lv 31 (108,480)
    tr.observe([(e, int(e / 108480 * 10000) / 100)], True)
check(f"a real level-up counts, and the new level's EXP is known right away "
      f"(gained {tr.gained:.0f}, Lv {m.level_for_req(tr.req)})",
      tr.levels == 1 and tr.gained == 200 + 150 + 120 and tr.req == 108480)
tr = exp_tracker()
tr.observe([(0, 0.0)], True)                             # a misread 0
check("a misread 0 doesn't crash or count", tr.gained == 0)
tr = exp_tracker(95680, 99.97)
for e, p in ((32, 0.06), (32, 0.06), (32, 0.02), (32, 0.02), (152, 0.14)):   # 0.02% misread as 0.06%, twice
    tr.observe([(e, p)], True)
check(f"a level-up whose first readings are misread still counts right (gained {tr.gained:.0f}, levels {tr.levels})",
      tr.levels == 1 and tr.gained == 20 + 32 + 120)
tr = exp_tracker(76560, 80.0)
for e in (19140, 19140, 76660, 76660):                   # 80% misread as 20% twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"EXP never drops by more than a death costs: a big drop is ignored (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.levels == 0)
tr = exp_tracker()
for _ in range(10):
    tr.observe([(47850, 70.0)], False)                   # a misread that suggests another level
check("misreads can't change the level's EXP once it's known from the table", tr.req == 95700)

# ---- dying: EXP drops a few % of the level (Lv 30 needs 95,700)
def read(tr, e):
    return tr.observe([(e, int(e / 95700 * 10000) / 100)], True)

tr = exp_tracker()
for e in (48850, 48850):                                 # +1,000
    read(tr, e)
for e in (39280, 39280):                                 # dies: -10% (9,570)
    note = read(tr, e)[1]
check(f"dying isn't taken off the EXP gained (gained {tr.gained:.0f}, deaths {tr.deaths}, lost {tr.last_loss:.1f}%)",
      note == "died" and tr.gained == 1000 and tr.deaths == 1 and abs(tr.last_loss - 10) < 0.1)
read(tr, 39780)
check(f"after dying, EXP gained counts as usual (gained {tr.gained:.0f})", tr.gained == 1500)
tr = exp_tracker(479, 0.5)
for e in (0, 0):                                         # dies at 0.5%: EXP goes to 0
    read(tr, e)
check(f"dying with less EXP than it costs (down to 0) isn't counted either (gained {tr.gained:.0f})",
      tr.gained == 0 and tr.deaths == 1)
tr = exp_tracker()
for e in (43065, 47850):                                 # 5% lower once (a misread), then the real number
    read(tr, e)
check("a single low reading is ignored", tr.gained == 0 and tr.deaths == 0)
tr = exp_tracker()
for e in (43065, 43065, 47850, 47950):                   # the same low misread twice, then the real number
    read(tr, e)
check(f"a misread that looks like dying, then the real number: no EXP made up (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.deaths == 0)
tr = exp_tracker()
for e in (50721, 50721, 47850, 47950):                   # 3% too high twice, then the real number
    read(tr, e)
check(f"a misread a bit too high and the real number after it cancel out (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.deaths == 0)
log, _ = new_log()
log.gain(0, 100)
log.gain(1, -2000)                                       # more taken back than gained
for s in range(2, 1000):
    if s % 3 == 0:
        log.gain(s, 1)
    log.tick(s, "Rocky Road II", 27, 1)
check(f"no log line or run is ever made of EXP lost (lines: {[e['exp'] for e in log.entries]}, runs: {len(log.runs)})",
      [e["exp"] for e in log.entries] == [200] and log.runs == [])   # the 5-minute blocks after the loss


# ---- EXP tracking with noisy readings: misreads must never inflate the gains
random.seed(1)
reqs, worst = [54900, 63666, 73080], 0.0
for trial in range(60):
    tr = m.ExpTracker()
    tr.start()
    lvl, e, true_gain, counting = 0, random.randint(20000, 50000), 0, False
    for step in range(500):
        if random.random() < 0.3:
            g = random.choice([50, 100, 150])
            e += g
            if e >= reqs[lvl]:
                e, lvl = e - reqs[lvl], lvl + 1
            true_gain += g if counting else 0
        p = int(e / reqs[lvl] * 10000) / 100
        bad = int(str(e)[:-1] or 0) if random.random() < 0.1 else None       # a dropped digit
        ok, note = tr.observe([(bad, p)] if bad is not None else [(e, p)], bad is None)
        if note == "baseline":
            counting, true_gain = True, 0
    worst = max(worst, (tr.gained - true_gain) / max(true_gain, 1))
check(f"misread EXP never inflates the gain (worst over-count {worst:.1%})", worst <= 0.02)

# ---- reading the real EXP bar, captured at different game resolutions
import cv2  # noqa: E402
tpl = m.DigitTemplates.load()
for f in sorted((Path(__file__).parent / "frames").glob("exp_*.png")):
    lay = m.find_exp(cv2.imread(str(f)))
    got = m.read_layout(tpl, lay) if lay else None
    check(f"reads the EXP bar at {f.stem[4:]} ({got})", got == (68601, 71.68))
check("a reading that fits a level is trusted", m.fits_exp_table(68601, 71.68))
tr = m.ExpTracker()
tr.observe([(68601, 71.68)], True)
tr.observe([(68601, 71.68)], True)
check("knows the level from two clean readings, without waiting for EXP gains",
      tr.req == 95700 and m.level_for_req(tr.req) == 30)
tr = m.ExpTracker()
tr.observe([(1, 71.68)], False)
tr.observe([(1, 71.68)], False)
check("an untrusted reading doesn't set the level", tr.req is None)
check("'1 [71.68%]' (digits cut off) fits no level, so it isn't trusted", not m.fits_exp_table(1, 71.68))

# ---- from 1,000,000 EXP the game hides the EXP number: "EXP. [98.74%]", the % alone
for f in sorted((Path(__file__).parent / "frames").glob("exp_*.png")):
    img = cv2.imread(str(f))
    lay = m.find_exp(img)
    if not lay:
        continue
    # The number erased, as the game leaves it: the bar's dark background up to the '['.
    x0 = int(lay.box[0] - 0.4 * lay.gh)
    x1 = int(min(c[0] - c[1] / 2 for c in lay.pct) - 0.95 * lay.gh)
    y0, y1 = int(lay.cy - 0.75 * lay.gh), int(lay.cy + 0.75 * lay.gh)
    region = img[y0:y1, x0:x1].reshape(-1, 3)
    img[y0:y1, x0:x1] = m.np.median(region[region.max(axis=1) < 120], axis=0)
    hidden_lay = m.find_exp(img)
    got = m.read_layout(tpl, hidden_lay) if hidden_lay else None
    check(f"the number hidden ({f.stem[4:]}): the EXP bar is still found, by its % ({got})", got == (None, 71.68))
    reading = m.ScreenReader(tpl)._read((img, 0, 0), "test")[0]
    check(f"...and read as the % alone, trusted ({reading.text})", reading.confident and reading.candidates == [(None, 71.68)])
REQ57 = m.EXP_TABLE[56]                                          # Lv 57: 1,031,036 EXP
tr = m.ExpTracker()
tr.start()
for _ in range(2):
    tr.observe([(999941, 96.98)], True)                        # the last number the game shows
check("Lv 57 known from the last numbers shown", tr.req == REQ57 and tr.exp == 999941)
before = tr.gained
ok, _ = tr.observe([(None, 96.99)], True)                       # 1,000,000 or more: the number is hidden
check(f"the number hidden: the EXP is worked out from the % ({tr.exp:,})",
      ok and tr.hidden and abs(tr.exp / REQ57 * 100 - 96.99) <= 0.011)
tr.kill_hint = 49 * 3                                           # three kills seen since, about 49 EXP each
exp_before = tr.exp
tr.observe([(None, 97.0)], True)
check(f"kills seen since carry it on, kept to what the % allows ({tr.exp:,})",
      tr.exp >= exp_before and abs(tr.exp / REQ57 * 100 - 97.0) <= 0.011)
tr.kill_hint = 99999                                            # far more than the % allows: never drifts
tr.observe([(None, 97.01)], True)
check(f"the EXP never strays from the % ({tr.exp / REQ57 * 100:.3f}%)", abs(tr.exp / REQ57 * 100 - 97.01) <= 0.011)
for p in (97.5, 98.0, 98.74):
    tr.observe([(None, p)], True)
check(f"the EXP gained counts as the % goes up ({tr.gained - before:,} EXP over 1.76%)",
      abs((tr.gained - before) - 0.0176 * REQ57) <= 0.0003 * REQ57)
tr.observe([(None, 93.74)], True)
tr.observe([(None, 93.74)], True)
check("a death (5% lost) with the number hidden is still a death, not counted against EXP/hr",
      tr.deaths == 1 and abs(tr.last_loss - 5.0) < 0.05)
lvl_before = tr.levels
for _ in range(2):
    tr.observe([(812, 0.07)], True)                             # level up: the number shows again
check("a level-up brings the number back, and is counted", tr.levels == lvl_before + 1 and not tr.hidden
      and tr.exp == 812 and tr.req == m.EXP_TABLE[57])
fresh = m.ExpTracker()
ok, note = fresh.observe([(None, 98.74)], True)
check("started while the number is hidden: nothing is guessed without the level", not ok and "hides" in note)
check("...unless the level is known from last time", fresh.assume_level(57) == 57 and fresh.observe([(None, 98.74)], True)[0]
      and abs(fresh.exp - 0.9874 * REQ57) <= 0.0001 * REQ57)

# ---- pausing by itself (a fake clock, so these run instantly)
clock = [10000.0]
m.time.monotonic = lambda: clock[0]
EXP0 = 60000


def session(gap=15):
    s = m.Session(gap)
    s.map_seen("Rocky Road II")
    for _ in range(2):                                  # learns the level (Lv 30)
        s.observe([(EXP0, round(EXP0 / 95700 * 100, 2))], True, clock[0])
    return s


def gains(s, seconds, every=3, map_name=None):
    """Kills worth 100 EXP every few seconds; the clock moves in half-second steps."""
    end, nxt = clock[0] + seconds, clock[0] + every
    while clock[0] < end:
        clock[0] += 0.5
        if clock[0] >= nxt:
            nxt += every
            e = s.exp.exp + 100
            s.observe([(e, int(e / 95700 * 10000) / 100)], True, clock[0])
        s.tick(clock[0])


def idle(s, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        clock[0] += 0.5
        s.tick(clock[0])


s = session()
s.start(clock[0])
gains(s, 30)
idle(s, 40)
check(f"no EXP for 15 s pauses by itself, without counting the idle time "
      f"({s.waiting}, {s.exp.elapsed():.0f} s)", s.waiting == "idle" and abs(s.exp.elapsed() - 30) < 0.6)
gains(s, 3.5)
check(f"the next EXP gain carries on, and counts (it was earned on this map: {s.exp.gained})",
      s.waiting is None and s.exp.running and s.exp.gained == 1100)
gained = s.exp.gained
s.map_seen("The Swamp of Despair I")
check("leaving the map pauses by itself", s.waiting == "map" and not s.exp.running)
s.map_seen("Rocky Road II")
gains(s, 6.5)
check("back on the same map, it carries on adding up", s.exp.running and s.exp.gained == gained + 200)
s.map_seen("The Swamp of Despair I")
gains(s, 6.5)
check(f"EXP on a different map starts from zero, still knowing the level "
      f"(gained {s.exp.gained}, Lv {m.level_for_req(s.exp.req)})",
      s.map == "The Swamp of Despair I" and s.exp.gained == 100 and s.exp.req == 95700)
s.pause()
gains(s, 6.5)
check("after pressing Pause, it stays paused", not s.exp.running and s.on is False)
s = session(gap=60)
s.start(clock[0])
gains(s, 9.5)
idle(s, 40)
check("the pause time can be changed in Settings (60 s: a 40 s break doesn't pause)", s.exp.running)


# ---- the minimap's name can't be seen: pause rather than log under an old map name
s = session()
s.start(clock[0])
gains(s, 12)
gained = s.exp.gained
s.map_blind()
check("when the minimap's name has been out of sight, it pauses", s.waiting == "blind" and not s.exp.running)
gains(s, 12)
check("...and EXP that comes in meanwhile doesn't restart it (it could be another map)",
      s.waiting == "blind" and not s.exp.running and s.exp.gained == gained)
s.map_seen("Rocky Road II")
check("...seeing the same map again lets the usual rule carry on", s.waiting == "map")
gains(s, 6.5)
check("...at the next EXP gain", s.exp.running and s.waiting is None and s.map == "Rocky Road II")
s.map_blind()
s.map_seen("The Swamp of Despair I")
gains(s, 6.5)
check("...but a different map starts from zero", s.map == "The Swamp of Despair I" and s.exp.gained == 100)
s = session()
check("with Start never pressed, nothing pauses", (s.map_blind(), s.waiting)[1] is None)
s = session()
s.start(clock[0])
gains(s, 12)
idle(s, 40)
s.map_blind()
check("paused by being idle, then blind: it waits for the minimap too", s.waiting == "blind")

# ---- EXP/hr when hunting in waves: the respawn waits are part of the time
s = session(gap=m.DEFAULTS["gap_seconds"])
s.start(clock[0])
t0 = clock[0]
for _ in range(20):                                     # 5 kills 3 s apart, then 25 s for the respawn
    gains(s, 15)
    idle(s, 25)
true_rate = 20 * 5 * 100 / (clock[0] - t0) * 3600
check(f"hunting in waves, EXP/hr is the real rate ({s.exp.rate():,.0f} for {true_rate:,.0f})",
      s.exp.running and abs(s.exp.rate() / true_rate - 1) < 0.05)


# ---- mesos through pauses: shop trips are what "spent" is
def mesos(s, value, times=3):
    for _ in range(times):
        clock[0] += 0.5
        s.meso.observe(value, clock[0], s.exp.elapsed(), s.started)


s = session()
s.start(clock[0])
mesos(s, 500000)
gains(s, 12)
mesos(s, 503000)
s.map_seen("Perion")                                    # off to town
mesos(s, 470000)                                        # potions: -33,000
s.map_seen("Rocky Road II")
gains(s, 6.5)
check(f"a shop trip in town counts as spent, though tracking was paused ({s.meso.spent:,})",
      s.meso.spent == 33000 and s.meso.earned == 3000 and s.exp.running)
mesos(s, 150000)                                        # a big buy: more than half the mesos
check(f"a big buy (more than half the mesos) counts as spent too ({s.meso.spent:,})", s.meso.spent == 353000)
s = session()
s.start(clock[0])
mesos(s, 500000)
gains(s, 12)
mesos(s, 503000)
s.map_seen("Perion")
mesos(s, 470000)
s.map_seen("The Swamp of Despair I")                    # back out to a different map
gains(s, 6.5)
check(f"repotting on the way to a new map: the trip's spending goes with the new map's numbers "
      f"(spent {s.meso.spent:,}, earned {s.meso.earned:,})",
      s.map == "The Swamp of Despair I" and s.meso.spent == 33000 and s.meso.earned == 0 and s.meso.gained == -33000)
s = session(gap=15)
s.start(clock[0])
mesos(s, 500000)
gains(s, 12)
idle(s, 20)                                             # (inventory closed, looting, paused by itself)
gains(s, 6.5)
mesos(s, 504000)                                        # opened again
check(f"mesos picked up with the inventory closed count, even across a pause ({s.meso.earned:,})",
      s.meso.earned == 4000)
s.pause()
mesos(s, 400000)
check(f"after pressing Pause (to go shopping, say), the mesos still count ({s.meso.spent:,})", s.meso.spent == 104000)
s.reset()
mesos(s, 300000)
check("after Reset they don't, until Start", s.meso.spent == 0 and s.meso.earned == 0 and not s.meso.counting)


# ---- map names: the game's spelling, OCR slips, towns
check(f"the game's map list is bundled ({len(m.map_list().entries)} maps)", len(m.map_list().entries) > 300)
slips = {"ist Accompaniment <Bonus>": "1st Accompaniment <Bonus>", "1st Accompaniment <Bonus>": "1st Accompaniment <Bonus>",
         "The Land of Wild Boar Il": "The Land of Wild Boar II", "Rocky Road lI": "Rocky Road II",
         "Tree Dungeon Monkey Forest II": "Tree Dungeon Monkey Forest II", "Ant Tunnel l": "Ant Tunnel I"}
bad = {k: m.snap_map_name(m._clean_map_name(k)) for k, v in slips.items() if m.snap_map_name(m._clean_map_name(k)) != v}
check(f"OCR slips like 'ist' for '1st' and 'Il' for 'II' are put right ({bad})", not bad)
check("a map that isn't on the list (an event map) is kept as read", m.snap_map_name("Halloween Event Field") == "Halloween Event Field")
check("a different Roman numeral is a different map (not snapped)", m.snap_map_name("Rocky Road IV") == "Rocky Road IV")
check("towns are known", m.is_town("Perion") and m.is_town("Henesys") and m.is_town("Free Market <3>"))
check("...a hunting map with a town's name in it isn't a town", not m.is_town("Henesys Hunting Ground II") and not m.is_town("Perion Street Corner"))
check("...and an unknown name isn't", not m.is_town("Halloween Event Field") and not m.is_town(None))
check("a short, unclear name isn't forced onto a map", m.snap_map_name("Perlon X") == "Perlon X")


# ---- Roman numerals misread by OCR ("Vil" is VII), in the overlay's own cleaning
for read, want in {"Tree Dungeon Forest Up North Vil": "Tree Dungeon Forest Up North VII", "Tree Dungeon Forest Up North Vill": "Tree Dungeon Forest Up North VIII",
                   "Ant Tunnel lV": "Ant Tunnel IV", "Ant Tunnel il": "Ant Tunnel II", "Rocky Road iil": "Rocky Road III",
                   "Free Market 11": "Free Market 11", "Maple Mill": "Maple Mill", "Cave VX": "Cave VX"}.items():
    got = m._clean_map_name(read)
    check(f"'{read}' reads as '{want}' ({got})", got == want)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
