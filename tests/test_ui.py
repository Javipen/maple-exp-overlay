"""Drives the real overlay window (it needs a screen, so it isn't part of test_logic.py): the simple main
window, the tracker card over the game, the repot bell, Settings. Run from the project folder:
    py -3 tests/test_ui.py

It uses its own temporary data folder and never goes online."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(tempfile.mkdtemp())
os.environ["MAPLE_EXP_DATA"] = str(DATA)                    # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def pump(seconds=0.0):
    end = time.time() + seconds
    root.update()
    while time.time() < end:
        root.update()


# ---- the main window: simple, and it draws at every size and in every state
m.save_config({**m.DEFAULTS, "hotkeys_on": False, "pos": [60, 60]})
root = m.tk.Tk()
app = m.App(root, m.DigitTemplates.load())
app.messages = None                     # (the tests don't watch the real screen's messages: what's there is told to it)
root.update()
t, meso = app.tracker, app.meso
check("the main window is the simple one", isinstance(app.view, m.SimpleView))
labels = {b.text for b in (app.view.start_btn, app.view.repot_btn, app.view.move_btn)}
check(f"...with Start, the repot bell and Move card ({labels})", labels == {"Start", "Ring repot bell", "Move card"})
gone = [w for w in ("Log this run", "Best maps", "Details") if any(
    getattr(c, "text", None) == w for c in app.view.winfo_children() + [g for c in app.view.winfo_children()
                                                                      for g in c.winfo_children()])]
check(f"...and none of the old parts: no log, no Log this run, no details ({gone})", not gone and not app.log_open
      and not app.log_panel.winfo_ismapped())
check("no compact mode or log hotkeys any more", set(m.HOTKEY_ACTIONS) == {"start", "reset", "repot", "edit"})
sizes_ok, widths = True, {}
for scale in m.UI_SCALES:
    try:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for case in ("idle", "tracking", "huge"):
            if case == "idle":
                t.exp = t.pct = t.req = None
                t.rate = lambda: None
                t.rolling_rate = lambda: None
            else:
                t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
                huge = case == "huge"
                t.rate = lambda huge=huge: 123456789.0 if huge else 72566.0
                t.rolling_rate = lambda huge=huge: 99999999.0 if huge else 71900.0
                t.elapsed = lambda: 5620.0
                meso.value, meso.gained = 18449220, 41000
                app.level = 35
            app.session.on = case != "idle"
            app._update_display()
            root.update()
            widths[scale] = app.view.winfo_width()
    except Exception as e:                                   # (printed, so a failure says where)
        print("   error at", scale, "%:", type(e).__name__, e)
        sizes_ok = False
check("the window draws at every size (60% to 200%) and in every state: idle, tracking, huge numbers", sizes_ok)
check(f"a bigger size makes the window wider, not clipped ({widths})",
      all(widths[a] < widths[b] for a, b in zip(m.UI_SCALES, m.UI_SCALES[1:])))
app.view.show(state="alert", state_text="Can't read the EXP bar", message="A very long message " * 12)
root.update()
check("long messages still fit the window", app.view.winfo_reqwidth() <= app.theme.px(400) + 4)
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

picked = []
selector = m.RegionSelector(root, picked.append)             # (it crashed on Windows in 0.9.7 and 0.9.8-beta)
root.update()
check("Select area opens its full-screen picker", selector.win.winfo_exists())
selector._finish(None)
check("...and Esc closes it without choosing anything", picked == [None] or picked == [])
import numpy as np
fake = m.Reading(*([None] * len(m.Reading._fields)))._replace(
    where="MapleStory window 1920x1080", text="3,060 [1.04%]", image=np.full((40, 200, 3), 60, np.uint8), boxes=[])
app.last_capture = fake
app.job = None
app.reader.job_look = {"image": np.full((60, 300, 3), 90, np.uint8), "boxes": [(10, 10, 80, 40, "exp")],
                       "why": "the tries didn't agree on what it says"}
before = set(root.winfo_children())
app.view_capture()
root.update()
win = next(w for w in set(root.winfo_children()) - before)
texts = " | ".join(w.cget("text") for w in win.winfo_children() if w.winfo_class() == "Label" and w.cget("text"))
check("View capture says why the class wasn't read", "Class. Not read: the tries didn't agree" in texts)
win.destroy()
app.reader.job_look = {"image": None, "boxes": [], "why": "this window is covering the class name: move it"}
app.view_capture(); root.update()
win = next(w for w in set(root.winfo_children()) - before)
check("...and works when there is no picture (the overlay is in the way)",
      "covering the class name" in " ".join(w.cget("text") for w in win.winfo_children() if w.winfo_class() == "Label"))
win.destroy()
app.reader.box = (700, 1000, 200, 30)                          # (the EXP bar is found: only then is the class worth asking about)
files = app._save_report_files(False)
text = app._report_text(files)
check("a bug report says why the class wasn't read", "Class not read: this window is covering the class name" in text)
check("it saves a picture, and the details as a text file", files["picture"].exists() and files["details"].exists())
check("...with the whole story in the text file", all(w in files["details"].read_text(encoding="utf-8")
                                                      for w in ("== Game window ==", "== What it found ==", "== What happened")))
check("it never contains the Windows user name",
      not any(len(s) > 2 and s in text + files["details"].read_text(encoding="utf-8")
              for s in {str(m.Path.home()), m.os.environ.get("USERNAME", "")}))
print(text)
app.job, app.reader.job_look, app.last_capture = None, None, None
app.reader.box = None

# ---- the EXP tracker card over the game (a 1920x1080 game window at 100, 50)
import hud  # noqa: E402
card_c = {"level": 52, "fill": 0.648, "exp": "1,680,960", "req": "2,592,000", "pct": "64.8%", "next_level": 53,
          "time": "02:14:00", "last5": "+12,300", "rate": "175,200", "session": "+87,450", "mesos_rate": "245,000",
          "mesos_net": "+121,000", "mesos_gained": "+250,000", "mesos_spent": "-129,000", "slime": "happy"}
game = (100, 50, 1920, 1080)
real_update_hud, app._update_hud = app._update_hud, lambda *a: None   # (no game is running: keep the app from hiding it)
app.cfg.pop("hud_offset", None)
app.hud.set_style(layout="full", theme="brown")
app.hud.show(game, card_c)
root.update()
cx, cy, cw, ch = m.PLATFORM.own_window_rect(app.hud.window.win)               # (where Windows has it)
check(f"the card starts at the game's bottom left, above the chat ({cx}, {cy}, {cw}, {ch})",
      100 <= cx <= 120 and cy + ch == 50 + 1080 - round(0.17 * 1080) and (cw, ch) == hud.card_size("full", 0.5))
if m.PLATFORM.name == "Windows":
    check(f"the card is painted pixel by pixel (rounded, with a shadow) ({getattr(m.PLATFORM, 'paint_error', None)})",
          getattr(m.PLATFORM, "paint_error", None) is None and app.hud.window.photo is None)
art = hud.Art(m.ASSETS)
check("the Maplestory typeface and the pictures (slime, leaves, coins) come with the overlay",
      art.font_paths["bold"].name == "MaplestoryBold.ttf"
      and all(art.sprite(n, 40) is not None for n in ("slime_happy", "slime_sleepy", "slime_sparkle", "slime_dizzy",
                                                      "slime_confused", "slime_calm", "leaf_orange", "leaf_yellow",
                                                      "leaf_blue", "leaf_gray", "coin_pair", "coin", "coin_stack", "money_bag")))
check("every layout draws in every theme, small and large",
      all(hud.render(lay, th, s, card_c, art).size == hud.card_size(lay, s)
          for lay in hud.LAYOUTS for th in hud.THEMES for s in (0.3, 0.5)))
check("Minimal is the shortest and Full the tallest, all the same width",
      hud.card_size("minimal", 1)[1] < hud.card_size("session", 1)[1] < hud.card_size("full", 1)[1]
      and len({hud.card_size(lay, 1)[0] for lay in hud.LAYOUTS}) == 1)
a = np.asarray(hud.render("full", "brown", 0.5, card_c, art))[:, :, 3]
check("the card has see-through corners around it and a solid body", a[0, 0] == 0 and a[a.shape[0] // 2, a.shape[1] // 2] == 255)
odd = dict(card_c, level=None, status="Looking for the game", exp="?", req="?", fill=0, time="--:--:--",
           mesos_net="\u2014", slime="confused", last5="\u2014")
check("it draws with nothing known yet", hud.render("full", "steel", 0.4, odd, art).size == hud.card_size("full", 0.4))
long = dict(card_c, level=200, exp="123,456,789,012", req="999,999,999,999", rate="12,345,678,901", mesos_gained="+9,876,543,210")
check("big numbers and three-digit levels still draw", hud.render("full", "blue", 0.5, long, art).size == hud.card_size("full", 0.5))

# edit mode: the card itself takes the mouse (no window over it that could slip behind other windows)
def under_mouse(x, y):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.WindowFromPoint.restype, u.WindowFromPoint.argtypes = wintypes.HWND, [wintypes.POINT]
    u.GetAncestor.restype, u.GetAncestor.argtypes = wintypes.HWND, [wintypes.HWND, wintypes.UINT]
    return u.GetAncestor(u.WindowFromPoint(wintypes.POINT(x, y)), 2)


def drag(x0, y0, x1, y1):
    """A mouse drag, given to tkinter (posted mouse messages would use the real mouse's place)."""
    win, (rx, ry) = app.hud.window.win, app.hud.rect[:2]
    ev = lambda seq, x, y, **kw: win.event_generate(seq, x=x - rx, y=y - ry, rootx=x, rooty=y, when="now", **kw)
    ev("<ButtonPress-1>", x0, y0)
    for i in range(1, 6):
        ev("<Motion>", x0 + (x1 - x0) * i // 5, y0 + (y1 - y0) * i // 5, state=0x100)
    ev("<ButtonRelease-1>", x1, y1)
    root.update()


card_hwnd = app.hud.window.win.wm_frame() if m.PLATFORM.name == "Windows" else None
x, y, w, h = app.hud.rect
middle = (x + w // 2, y + h // 2)
if m.PLATFORM.name == "Windows":
    card_hwnd = int(card_hwnd, 16)
    check("locked, clicks on the card go through to the game", under_mouse(*middle) != card_hwnd)
app.hud.set_editing(True)
root.update()
if m.PLATFORM.name == "Windows":
    check("in edit mode the card itself is under the mouse", under_mouse(*middle) == card_hwnd)
    for i in range(3):                                             # (the app's refreshes mustn't change that)
        app.hud.show(game, dict(card_c, time=f"02:14:0{i}"))
        root.update()
    check("...and stays so while it's refreshed", under_mouse(*middle) == card_hwnd)
gold = np.asarray(hud.compose(app.hud._base[1], app.hud._base[2], app.hud.art, {"edit": True}))
check("edit mode draws a gold frame around the card", ((gold[:, :, 0] > 240) & (gold[:, :, 1] > 190) & (gold[:, :, 2] < 80)).sum() > 200)
drag(*middle, middle[0] + 120, middle[1] + 60)
check(f"dragging it moves it ({(x, y)} -> {app.hud.rect[:2]})", app.hud.rect[:2] == (x + 120, y + 60))
x, y, w, h = app.hud.rect
edge = int(hud.MARGIN * app.hud._ppu())
drag(x + w - edge, y + h // 2, x + w - edge + 150, y + h // 2)
check(f"dragging its right edge makes it wider, not taller ({(w, h)} -> {app.hud.rect[2:]})",
      app.hud.rect[2] == w + 150 and app.hud.rect[3] == h)
x, y, w, h = app.hud.rect
scale_before = app.cfg["hud_scale"]
drag(x + w // 2, y + h - edge, x + w // 2, y + h - edge + 40)
check(f"dragging its bottom edge makes it taller, the text the same size ({(w, h)} -> {app.hud.rect[2:]})",
      abs(app.hud.rect[3] - (h + 40)) <= 2 and app.hud.rect[2] == w and app.cfg["hud_scale"] == scale_before
      and app.cfg["hud_tall"] > 0)
x, y, w, h = app.hud.rect
drag(x + w - edge, y + h - edge, x + w - edge + 100, y + h - edge + 10)
grown = app.hud.rect[2] / w
check(f"dragging its corner makes all of it bigger, keeping its shape ({(w, h)} -> {app.hud.rect[2:]})",
      grown > 1.1 and abs(app.hud.rect[3] / h - grown) < 0.02 and app.cfg["hud_scale"] > scale_before)
app.cfg.update(hud_width=1390, hud_tall=0)
app.hud.move_to((400, 300))
app.hud.set_scale(0.6)
app.hud.set_style(layout="session", theme="blue")
app.hud.set_editing(False)
root.update()
if m.PLATFORM.name == "Windows":
    x, y, w, h = app.hud.rect
    check("locked again, clicks go through it", under_mouse(x + w // 2, y + h // 2) != card_hwnd)
saved = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
check(f"where it is, its size, layout and theme are saved ({saved.get('hud_offset')}, {saved.get('hud_scale')})",
      saved.get("hud_offset") == [300, 250] and saved.get("hud_scale") == 0.6
      and (saved.get("hud_layout"), saved.get("hud_theme")) == ("session", "blue"))
check("it is where it was put, and the size it was given", app.hud.rect[:2] == (400, 300)
      and app.hud.rect[2:] == hud.card_size("session", 0.6))
app.hud.show((600, 50, 1920, 1080), card_c)                       # the game window moved 500 px right
check("it follows the game window when that moves", app.hud.rect[:2] == (900, 300))
app.hud.show((0, 0, 3840, 2160), card_c)                          # a 4K game: twice as big, same place in it
check("it grows with a bigger game window, keeping its place in it",
      app.hud.rect[:2] == (600, 500) and app.hud.rect[2:] == hud.card_size("session", 1.2))
app.hud.move_to((-5000, -5000))
check("it can't be lost off the game window", app.hud.rect[0] >= -app.hud.rect[2] + 80 and app.hud.rect[1] >= -app.hud.rect[3] + 60)
app.hud.set_scale(9)
check("its size stays between the smallest and largest", app.cfg["hud_scale"] == hud.MAX_SCALE)
app.hud.set_width(99999)
check("so does its width", app.cfg["hud_width"] == hud.MAX_WIDTH)

# the slime: it hops when EXP comes in, with the EXP floating up; three hops for a level up; sleeps when paused
mv = hud.Moves()
rest = mv.frame("happy", 10.0)
mv.gain(1234, now=10.0)
mid = mv.frame("happy", 10.0 + hud.HOP / 2)
check(f"EXP coming in makes it hop, happy, with '+1,234 EXP' floating up ({mid['dy']:.0f} units up)",
      mid["dy"] < -30 and mid["slime"] == "squint" and any(f[0] == "+1,234 EXP" for f in mid["floats"]))
push = mv.frame("happy", 10.02)
check("it squashes as it pushes off, and stretches in the air", push["sy"] < 0.95 < 1.05 < mid["sy"])
after = mv.frame("happy", 13.5)
check("then it settles back, and the number is gone", after["dy"] > -3 and not after["floats"] and after["slime"] == "happy")
mv.gain(100, now=20.0)
mv.gain(200, now=20.1)
check("EXP coming in fast queues the hops, one after another", len(mv.hops) == 2 and mv.hops[1] >= mv.hops[0] + hud.HOP - 1e-9)
lv = hud.Moves()
lv.level_up(now=5.0)
check("a level up: three hops and LEVEL UP!", len(lv.hops) == 3 and any(f[0] == "LEVEL UP!" for f in lv.frame("sparkle", 5.1)["floats"]))
check("paused, it sleeps with z's", hud.Moves().frame("sleepy", 1.0)["zs"] and hud.Moves().frame("sleepy", 1.0)["slime"] == "sleepy")
base, info = hud.render_base("full", "brown", 0.5, card_c, art)
frames = [hud.compose(base, info, art, mid), hud.compose(base, info, art, rest)]
check("its frames are drawn over the same card, the same size", frames[0].size == frames[1].size == base.size
      and np.asarray(frames[0]).tobytes() != np.asarray(frames[1]).tobytes())
import time as _t  # noqa: E402
t0 = _t.perf_counter()
for _ in range(10):
    hud.compose(base, info, art, mid)
check(f"a frame is quick to draw ({(_t.perf_counter() - t0) * 100:.1f} ms)", (_t.perf_counter() - t0) * 100 < 15)
calib = hud.render("full", "brown", 0.5, dict(card_c, calibrating=200, rate="Calibrating\u2026", time="--:--:--"), art)
check("while calibrating it still draws", calib.size == hud.card_size("full", 0.5))
infos = {(lay, wid): hud.render_base(lay, "brown", 0.5, card_c, art, wid)[1] for lay in hud.LAYOUTS
         for wid in (950, 1390, 2400)}
check("Minimal shows the time to level at the top, in the last column at every width",
      all(abs(i["time_x"] - i["columns"][3]) < 0.01 for (lay, _), i in infos.items() if lay == "minimal"))
check("with the session row below (Session, Full), TO LV. isn't shown twice: none at the top",
      all(i["time_x"] is None for (lay, _), i in infos.items() if lay != "minimal"))
check("...and the gauge takes that room, out to the card's right edge",
      all(i["gauge"][1] > i["columns"][3] + 100 * 0.5 for (lay, _), i in infos.items() if lay != "minimal")
      and all(i["gauge"][1] < i["columns"][3] for (lay, _), i in infos.items() if lay == "minimal"))
with_row, without = hud.card_size("full", 0.5), hud.card_size("full", 0.5, repot=False)
check(f"the repot row can be turned off: Full gets shorter, as wide ({with_row} -> {without})",
      without[0] == with_row[0] and without[1] == with_row[1] - round(hud.REPOT_ROW * 0.5)
      and hud.render("full", "brown", 0.5, card_c, art, repot=False).size == without
      and hud.render_base("full", "brown", 0.5, card_c, art, repot=False)[1]["bell"] is None
      and hud.card_size("session", 0.5, repot=False) == hud.card_size("session", 0.5))
app.hud.show(game, card_c)
app.hud.set_repot(False)
check("turning it off (Settings or the card's menu) is saved and redraws the card",
      app.cfg["hud_repot"] is False and app.hud.rect[3] == hud.card_size(app.hud.layout, app.hud._ppu(),
                                                                          app.hud.width, app.hud.tall, False)[1])
app.hud.set_repot(True)
check("a guess (~, inventory closed) is coloured like the number", hud._tone("~+12,800") == hud.GOOD
      and hud._tone("~-500") == hud.BAD)
check("characters the typeface doesn't have (it draws them as boxes) are swapped", hud.plain("— a –") == "- a -")
short, tall_pic = hud.render("full", "brown", 0.5, card_c, art), hud.render("full", "brown", 0.5, card_c, art, tall=120)
check("extra height makes it taller, as wide", tall_pic.width == short.width and tall_pic.height == short.height + 60)
_, inf = hud.render_base("full", "brown", 0.5, card_c, art)
check("the full layout has the repot row with its bell", inf["bell"] is not None
      and hud.render_base("session", "brown", 0.5, card_c, art)[1]["bell"] is None)
bell = hud.Moves()
bell.ring(312450, now=50.0)
ringing = bell.frame("happy", 50.05)                              # (the top of its first swing)
check("ringing the bell swings it and shows the trip's profit in the header",
      abs(ringing["bell"][0]) > 1 and any(f[0] == "DING!  TRIP +312,450" and f[5] == "header" for f in ringing["floats"]))
check("...and it settles", "bell" not in bell.frame("happy", 52.0))
lost = hud.Moves()
lost.ring(-5000, now=1.0)
check("a trip that lost mesos says so, in red", lost.frame("happy", 1.1)["floats"][0][0] == "DING!  TRIP -5,000"
      and lost.frame("happy", 1.1)["floats"][0][1][0] > 200)
app.reader.hud_rects = ((10, 20, 300, 100),)
dummy = (np.full((1080, 1920, 3), 200, np.uint8), 0, 0)
hidden = app.reader._hide_own(dummy)[0]
check("the screen reader never sees the card (it's blacked out of its screenshots)",
      hidden[50, 100].max() == 0 and hidden[1000, 1800].max() == 200)
app.reader.hud_rects = ()
app.cfg.update(hud_layout="full", hud_theme="brown", hud_scale=0.5)
app.cfg.pop("hud_offset", None)
app.hud.hide()
app._update_hud = real_update_hud
app.reader.hwnd = None
app._update_display()
check("with no game running, the card isn't shown", not app.hud.window.shown and app.hud.rect is None)

# the first 5 minutes of tracking calibrate; the slime hops for EXP that comes in
real_front, m.PLATFORM.game_in_front = m.PLATFORM.game_in_front, lambda: True
app.reader.hwnd, app.reader.last_rect = 1, (100, 50, 1920, 1080)
tr = app.tracker
tr.req, tr.exp, tr.pct, tr.gained, tr.running = 555680, 233385, 42.0, 48000, False
tr.elapsed = lambda: 100.0                                        # (tracked for 100 s so far)
app._update_display()
cont = app.hud.content
check(f"2 minutes in: EXP/hr, mesos/hr and the time to level wait, with a countdown ({cont.get('calibrating')}, {tr.elapsed()})",
      cont["rate"].startswith("Calibrating") and cont["mesos_rate"].startswith("Calibrating")
      and cont["time"] == "--:--:--" and round(cont["calibrating"] or 0) == 200)
tr.elapsed = lambda: 360.0
app._update_display()
cont = app.hud.content
check(f"after 5 minutes they show ({cont['rate']}, {cont['time']})",
      cont["calibrating"] is None and cont["rate"].replace(",", "").isdigit() and cont["time"] != "--:--:--")
app.hud.moves = m.HudCard.__init__.__globals__["Moves"]()     # (no hops left over: they expire)
hops = 0
app._hud_seen, app._hud_pending = (tr.gained, app.level_up_at), None    # (48,000 was set, not gained)
tr.gained += 777
app._update_display()
check("EXP coming in waits for the next reading to agree (a misread never makes it hop)",
      len(app.hud.moves.hops) == hops)
app._update_display()
check("...not just the next look at the same reading", len(app.hud.moves.hops) == hops)
tr._reads += 1
app._update_display()
check("...then it makes the slime hop", len(app.hud.moves.hops) == hops + 1
      and any("+777 EXP" == f[0] for f in app.hud.moves.floats))

# kills, from the game's messages (msgwatch.py): a hop per kill, with the EXP its lines say
tr.running = True
app._kill_exp.clear()
app._last_kill_at = -1e9
app.hud.moves = m.HudCard.__init__.__globals__["Moves"]()
app._messages([("exp", 17), ("exp", 5)], m.time.monotonic())
check(f"a kill (its EXP and party bonus lines) makes the slime hop at once, with their EXP ({app.hud.moves.floats[-1][0]})",
      len(app.hud.moves.hops) == 1 and app.hud.moves.floats[-1][0] == "+22 EXP")
seen = (tr.gained, app.level_up_at)
hops_before = len(app.hud.moves.hops)
app._hud_seen = seen
tr.gained += 22
app._update_hud(None, None, 57, (999941, 96.98), "Tracking")
check("while kills are seen, the EXP readings don't make it hop again (no double hops)",
      len(app.hud.moves.hops) == hops_before)
tr.hidden, tr.kill_hint = True, 0.0
app._kill(49, m.time.monotonic())
check(f"while the game hides the EXP number, each kill carries its EXP on ({tr.kill_hint:.1f})", tr.kill_hint == 49)
# No EXP messages seen lately (turned off, or covered), and the number hidden: hops from the % steps.
# A clean history: 10 kills of 49 EXP each, then one 0.01% step at Lv 57 (103 EXP, about 2 kills).
t0 = m.time.monotonic()
app._kill_exp.clear()
for i in range(10):
    app._kill_exp.append((t0 - 100 + i, 49))
tr.gained = 10 * 49
app._last_kill_at = -1e9
app.hud.moves = m.HudCard.__init__.__globals__["Moves"]()
app._hud_seen = (tr.gained, app.level_up_at)
tr.gained += 103
app._update_hud(None, None, 57, (999941, 96.98), "Tracking")
tr._reads += 1
app._update_hud(None, None, 57, (999941, 96.98), "Tracking")
check(f"no messages and the number hidden: hops from the % steps, one per likely kill ({len(app.hud.moves.hops)})",
      len(app.hud.moves.hops) == 2)
tr.hidden, tr.kill_hint, tr.running = False, 0.0, False
# paused (by itself, or not started): the EXP isn't counted, but the slime still hops for it
app.hud.moves = m.HudCard.__init__.__globals__["Moves"]()
app._last_kill_at = -1e9
tr.exp, tr.req = 500000, 1000000
app._update_hud(None, None, 57, (500000, 50.0), "Paused")
tr.exp = 500150
app._update_hud(None, None, 57, (500150, 50.01), "Paused")
tr._reads += 1
app._update_hud(None, None, 57, (500150, 50.01), "Paused")
check(f"paused, EXP coming in still makes the slime hop ({[f[0] for f in app.hud.moves.floats]})",
      len(app.hud.moves.hops) == 1 and app.hud.moves.floats[-1][0] == "+150 EXP")
app._kill(30, m.time.monotonic())
check("...and so does a kill seen in the messages (the EXP not carried on: tracking's off)",
      len(app.hud.moves.hops) == 2 and tr.kill_hint == 0)

# the repot bell: what each trip made, from one ring to the next
app.cfg.pop("repot", None)
meso.value = None
app.ring_repot()
check("rung with the inventory closed, it waits to read the mesos", app._repot_pending and "repot" not in app.cfg)
meso.value = 1_000_000
app._update_display()
check("...and rings as soon as it can: the first ring just saves the mesos",
      not app._repot_pending and app.cfg["repot"]["mesos"] == 1_000_000 and app.cfg["repot"].get("trip") is None)
cont = app.hud.content
check(f"the card shows the mesos at the last repot ({cont['repot_at']}, {cont['repot_since']})",
      cont["repot_at"] == "1,000,000" and cont["repot_since"] == "+0" and cont["repot_trip"] == "-")
meso.value = 1_312_450                                          # trained, sold the loot, bought potions again
app._update_display()
check("since the repot: live, what you've made so far", app.hud.content["repot_since"] == "+312,450")
app.ring_repot()
app._update_display()
cont = app.hud.content
check(f"ringing again after a trip: the exact profit of the trip ({cont['repot_trip']})",
      cont["repot_trip"] == "+312,450" and cont["repot_at"] == "1,312,450" and cont["repot_since"] == "+0"
      and "+312,450" in app.status.get())
check("...with the bell rung on the card", app.hud.moves.rang is not None)
meso.value = 1_100_000
app.ring_repot()
check("a trip that cost mesos is a loss", app.cfg["repot"]["trip"] == -212_450)
saved = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
check("the repot is saved, so it carries on after a restart", saved["repot"]["mesos"] == 1_100_000
      and saved["repot"]["trip"] == -212_450 and saved["repot"]["trips"] == 3)
check("the repot bell has a hotkey (Shift+3)", m.DEFAULT_HOTKEYS["repot"] == "Shift+3")
app.cfg.pop("repot", None)
meso.value = None
m.PLATFORM.game_in_front = real_front
app.reader.hwnd, app.reader.last_rect = None, None
del tr.elapsed
tr.new_session()
app.hud.hide()
# ---- every Size at every Windows scaling: nothing drawn outside its box, nothing cut off, nothing broken
def spill(widget, slack=2):
    """Texts or pictures that stick out of the canvas they're drawn in (listed by kind)."""
    bad = []
    for canvas in [widget] + [c for c in widget.winfo_children()]:
        stack = [canvas]
        while stack:
            c = stack.pop()
            stack.extend(c.winfo_children())
            if c.winfo_class() != "Canvas" or not c.winfo_ismapped():
                continue
            c.update_idletasks()
            w, h = c.winfo_width(), c.winfo_height()
            for item in c.find_all():
                if c.type(item) != "text" or c.itemcget(item, "state") == "hidden" or not c.itemcget(item, "text"):
                    continue
                x0, y0, x1, y1 = c.bbox(item)
                if x0 < -slack or y0 < -slack or x1 > w + slack or y1 > h + slack:
                    bad.append(f"{c.itemcget(item, 'text')[:18]!r} {x0},{y0},{x1},{y1} in {w}x{h}")
    return bad


app._check_screen = lambda: None                          # (it would put the screen's own scaling back)
layout_bad = []
for dpi in (96, 120, 144, 192):                          # Windows scaling 100%, 125%, 150%, 200%
    app._dpi = dpi
    for scale in m.UI_SCALES:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for case in ("tracking", "huge"):
            t.exp, t.pct, t.req, t.gained, t.levels = 31204, 54.2, 57600, 412300, 2
            huge = case == "huge"
            t.rate = lambda huge=huge: 123456789.0 if huge else 72566.0
            t.rolling_rate = lambda huge=huge: 99999999.0 if huge else 71900.0
            t.elapsed = lambda: 5620.0
            meso.value, meso.gained = 18449220, 41000
            app.level = 35
            app.session.on = True
            app._update_display()
            root.update()
            for line in spill(app.view):
                layout_bad.append(f"{dpi}dpi {scale}% {case}: {line}")
check("at every Size and Windows scaling, no text sticks out of its box on the main window"
      + (f" ({len(layout_bad)} problems, first: {layout_bad[:3]})" if layout_bad else ""), not layout_bad)

fonts_small = []
for dpi in (96, 120, 144, 192):
    app._dpi = dpi
    for scale in m.UI_SCALES:
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        for name, f in app.fonts.items():
            if abs(int(f.cget("size"))) < 9:
                fonts_small.append(f"{dpi}dpi {scale}% {name} {f.cget('size')}")
        for (weight, size), f in app.theme._tk.items():
            if abs(int(f.cget("size"))) < m.MIN_TEXT_PX if hasattr(m, "MIN_TEXT_PX") else False:
                fonts_small.append(f"{dpi}dpi {scale}% {weight} {size}")
check(f"text never gets smaller than the readable minimum ({fonts_small[:3]})", not fonts_small)

# Settings still fits its contents at the smallest and biggest sizes
fit_bad = []
for dpi in (96, 192):
    app._dpi = dpi
    for scale in (60, 70, 100, 200):
        app.cfg["ui_scale"] = scale
        app._apply_ui_scale()
        root.update()
        app.open_settings()
        sw = app.settings_win
        sw.geometry("")
        sw.update()
        for child in sw.winfo_children():
            stack = [child]
            while stack:
                c = stack.pop()
                stack.extend(c.winfo_children())
                if c.winfo_ismapped() and (c.winfo_rootx() + c.winfo_width() > sw.winfo_rootx() + sw.winfo_width() + 2
                                           or c.winfo_rooty() + c.winfo_height() > sw.winfo_rooty() + sw.winfo_height() + 2):
                    fit_bad.append(f"{dpi}dpi {scale}% settings: {c.winfo_class()} sticks out")
        for line in spill(sw):
            fit_bad.append(f"{dpi}dpi {scale}% settings: {line}")
        sw.destroy()
        app.settings_win = None
check(f"Settings fits at the smallest and biggest Sizes ({fit_bad[:3]})", not fit_bad)
app._dpi = 192 if app._dpi not in (96, 120, 144, 192) else app._dpi
app.cfg["ui_scale"] = 100
app._apply_ui_scale()

# ---- Size and Transparency limits
check("sizes from 60% to 200% are offered", m.UI_SCALES[0] == 60 and m.UI_SCALES[1] == 70 and m.UI_SCALES[-1] == 200)
saved = m.CONFIG_PATH
cfg_file = Path(tempfile.mkdtemp()) / "config.json"
cfg_file.write_text(json.dumps({"transparency": 90, "ui_scale": 70}), encoding="utf-8")
m.CONFIG_PATH = cfg_file
loaded = m.load_config()
m.CONFIG_PATH = saved
check("an older config with transparency 90 loads as the 40% limit, and a saved 70% size is kept",
      loaded["transparency"] == 40 and loaded["ui_scale"] == 70)
cfg_file.write_text(json.dumps({"transparency": -5, "ui_scale": 65}), encoding="utf-8")
m.CONFIG_PATH = cfg_file
loaded = m.load_config()
m.CONFIG_PATH = saved
check("a negative transparency is 0, and a size that isn't offered is 100%", loaded["transparency"] == 0 and loaded["ui_scale"] == 100)
check("transparency can't go past 40%", m.MAX_TRANSPARENCY == 40)

# ---- the transparency slider stops at the limit, and the window can't fade past it
app.cfg["transparency"] = 90
app._apply_window_settings()
check("the window is never more see-through than the limit (opacity %.2f)" % float(root.attributes("-alpha")),
      float(root.attributes("-alpha")) >= 1 - m.MAX_TRANSPARENCY / 100 - 0.01)
app.cfg["transparency"] = 0
app._apply_window_settings()

# ---- hotkeys in Settings: one waiting at a time, and clicking it again cancels
if app.hotkeys.supported:
    app.open_settings()
    sw = app.settings_win
    sw.update()
    ui = app._hotkey_ui
    names = list(ui["buttons"])
    keys_before = dict(app.cfg["hotkeys"])
    texts = lambda: {a: b.itemcget(b._txt, "text") for a, b in ui["buttons"].items()}
    ui["choose"](names[0])
    check("clicking a hotkey makes it wait for keys", ui["choosing"]["action"] == names[0] and texts()[names[0]] == "Press keys...")
    ui["choose"](names[1])
    t2 = texts()
    check("clicking another one moves the wait to it: only one says Press keys",
          ui["choosing"]["action"] == names[1] and sum(v == "Press keys..." for v in t2.values()) == 1
          and t2[names[0]] == (keys_before[names[0]] or "Off"))
    ui["choose"](names[1])
    check("clicking the waiting one again cancels it, and every key is as it was",
          ui["choosing"]["action"] is None and texts() == {a: (keys_before[a] or "Off") for a in names}
          and app.cfg["hotkeys"] == keys_before)
    check("hotkeys work again after cancelling", getattr(app.hotkeys, "_paused", True) is False)
    ui["choose"](names[2])
    esc = type("E", (), {"keysym": "Escape", "state": 0, "keycode": 27})()
    ui["pressed"](esc)
    check("Esc cancels too, keeping the key", ui["choosing"]["action"] is None and app.cfg["hotkeys"] == keys_before)
    ui["choose"](names[3])
    back = type("E", (), {"keysym": "BackSpace", "state": 0, "keycode": 8})()
    ui["pressed"](back)
    check("Backspace turns that hotkey off", app.cfg["hotkeys"][names[3]] == "" and ui["choosing"]["action"] is None)
    app.cfg["hotkeys"].update(keys_before)
    m.save_config(app.cfg)
    ui["choose"](names[0])
    ui["on"].set(False)
    sw.update()
    check("turning hotkeys off while one is waiting ends the wait", ui["choosing"]["action"] is None)
    ui["on"].set(True)
    sw.update()
    sw.destroy()
    app.settings_win = None
    check("closing Settings leaves hotkeys working", getattr(app.hotkeys, "_paused", True) is False)

# ---- towns aren't logged; a minimap that can't be seen pauses
blank = m.Reading(*([None] * len(m.Reading._fields)))
app._handle(blank._replace(where="Reading", map="Perion"))
check("on a town map the status says nothing is logged", app.in_town and "In a town" in app.status.get())
entries = len(app.autolog.entries)
app._log_tick(m.time.monotonic())
check("...and nothing is logged there", len(app.autolog.entries) == entries)
app._handle(blank._replace(where="Reading", map="Rocky Road II"))
check("on a hunting map it's normal again", not app.in_town and "In a town" not in app.status.get())
real_ok = m.tesseract_ok
m.tesseract_ok = True
app.session.on, app.session.waiting = True, "idle"
app.reader.map_seen_at = m.time.monotonic() - m.MAP_BLIND_SECONDS - 5
app._handle(blank._replace(where="Reading"))
check("a minimap name unseen for a while pauses it", app.session.waiting == "blind")
app.reader.map_seen_at = m.time.monotonic()
app.session.on, app.session.waiting = False, None
m.tesseract_ok = real_ok

# ---- just after a level-up the % is too rough to tell the level: the level from last time
saved_session = app.session
app.session = m.Session(120)
app.tracker, app.meso = app.session.exp, app.session.meso
app.cfg["last_level"], app.rejected = 53, 0
for _ in range(8):                                              # Lv 53: 240 / 832,902 EXP
    app._handle(blank._replace(where="Reading", candidates=[(240, 0.03)], confident=True))
check(f"at 0.03% of a level, the level from last time is used if the reading fits it "
      f"(req {app.tracker.req}, rejected {app.rejected})",
      app.tracker.req == m.EXP_TABLE[52] and app.rejected == 0 and app._state()[1] != "Can't read the EXP bar")
app.session = m.Session(120)
app.tracker, app.meso = app.session.exp, app.session.meso
app.cfg["last_level"], app.level = 20, None                     # (another character: doesn't fit)
for _ in range(8):
    app._handle(blank._replace(where="Reading", candidates=[(240, 0.03)], confident=True))
check(f"...and if it doesn't fit, it waits for an EXP gain without calling the reading a failure "
      f"(req {app.tracker.req}, rejected {app.rejected})",
      app.tracker.req is None and app.rejected == 0 and app._state()[1] != "Can't read the EXP bar")
app.session = saved_session
app.tracker, app.meso = app.session.exp, app.session.meso

# ---- the repot bell's history can be cleared
app.cfg["repot"] = {"mesos": 1252407, "at": m.time.time() - 600, "trip": 1239820, "trips": 16}
check("with a repot saved, the card shows it", app._repot_content()["repot_at"] == "1,252,407")
app.clear_repot(confirm=False)
check("clearing the repot history blanks the repot row",
      "repot" not in app.cfg and app._repot_content()["repot_at"] == "Ring the bell"
      and "repot" not in json.loads(m.CONFIG_PATH.read_text(encoding="utf-8")))
check("the card's right-click menu offers it", app.hud.on_clear_repot == app.clear_repot)

# ---- potions used, from the controller's potion popup: priced, shown as Pot cost in the repot row
import potwatch  # noqa: E402
cv2, tk, ROOT = m.cv2, m.tk, Path(__file__).resolve().parent.parent
popup = potwatch.PotReader(m.ASSETS).look(cv2.imread(str(ROOT / "tests" / "frames" / "pots_lt_a.png")), 20)
app.cfg.pop("pots", None)
app.cfg.pop("pot_cost", None)
app.pots = m.PotTracker()
app.session.on = True
check("before the popup's been read, Pot cost shows a dash", app._pot_cost_text() == "-")
for _ in range(3):
    app._pots(popup, m.time.monotonic())
check(f"the popup's items are learned, with names and prices guessed ({[i['name'] for i in app.cfg['pots']]})",
      [i["name"] for i in app.cfg["pots"]] == ["Meat", "Blue Potion", "Red Potion", "Lemon", "Orange Potion"]
      and app._pot_cost_text() == "0")
used = [(look, c - 10 if c and c == 1018 else c - 2 if c and c == 368 else c) for look, c in popup]
for _ in range(2):
    app._pots(used, m.time.monotonic())                         # 10 orange (150) and 2 blue (220) used
check(f"potions used are priced: Pot cost ({app._pot_cost_text()})",
      app.cfg["pot_cost"] == 10 * 150 + 2 * 220 and app._pot_cost_text() == "-1,940")
app.cfg["pots"][1]["price"] = 300                               # (set in Settings)
blue = [(look, 360 if c == 366 else c) for look, c in used]
for _ in range(2):
    app._pots(blue, m.time.monotonic())
check("a price set in Settings is used from then on", app.cfg["pot_cost"] == 1940 + 6 * 300)
app.cfg["repot"] = {"mesos": 1000000, "at": m.time.time(), "trip": None, "trips": 1}
meso.value = 1000000
app.ring_repot()
check("ringing the repot bell starts Pot cost again from 0", app.cfg["pot_cost"] == 0 and app._pot_cost_text() == "0"
      and app._repot_content()["repot_pots"] == "0")
app.cfg["pot_cost"] = 500
app.clear_repot(confirm=False)
check("clearing the repot history clears Pot cost too", "pot_cost" not in app.cfg)
app.pots.seen_at = m.time.monotonic() - m.POTS_STALE - 1
check("not looked at for a while: Pot cost is marked as not up to date", app._pot_cost_text().startswith("~"))
app.pots.seen_at = m.time.monotonic() - 5                      # (looked at before coming into town)
app.session.started = True
app._handle(blank._replace(where="Reading", map="Henesys"))
check(f"coming into a town with the popup not looked at since: a reminder to hold LT before buying ({app._pot_cost_text()})",
      app.in_town and app._pot_cost_text() == "Hold LT")
app._pots(popup, m.time.monotonic())
check(f"...gone once the popup's been looked at ({app._pot_cost_text()})", app._pot_cost_text() == "0")
app._handle(blank._replace(where="Reading", map="Rocky Road II"))
check("...and not shown on a hunting map", not app.in_town and app._pot_cost_text() == "0")
app.session.started = False
box = tk.Frame(app.root)
app._fill_pots(box, {"bg": "white"}, {"bg": "white"}, 300)
check("Settings lists the potions, each with its price", len(box.winfo_children()) == 5)
box.destroy()
app.session.on = False

# ---- pickups, from the pickup lines: mesos exact, loot priced, potions picked up known; Profit on the card
app.session.started, app.session.on = True, True
app.picked_mesos, app.loot, app.pickups_seen, app.session_pot_cost = 0, {}, False, 0
app.cfg["pots"] = [{"id": 4, "name": "Lemon", "price": 150, "look": [[[0.0, 0.0, 0.0]]]}]
app._pickups([("mesos", 15), ("mesos", 17), ("item", "Lupin's Banana", 1, "Etc"), ("item", "Lupin's Banana", 2, "Etc"),
              ("item", "Lemon", 3, "Use")], m.time.monotonic())
check(f"mesos picked up add up exactly ({app.picked_mesos})", app.picked_mesos == 32)
check(f"items go on the loot list, counted ({list(app.loot.values())})", list(app.loot.values()) == [("Lupin's Banana", 3)])
check("potions picked up are told to the potion count, not put on the loot list", app.pots.picked.get(4) == 3 and app.pots.exact)
app.cfg["loot_prices"] = {"Lupin's Banana": 25}
app.session_pot_cost = 40
check(f"the loot is worth its NPC price ({app.loot_value()})", app.loot_value() == 75)
check("a long name cut short in its pickup line still finds its shop price (Green Mushroo... is a Green Mushroom Cap)",
      m._shop_price("Green Mushroo") == 14 and m._shop_price("Lupin's Banana") == 37)
front, m.PLATFORM.game_in_front = m.PLATFORM.game_in_front, lambda: True
hwnd, rect = app.reader.hwnd, app.reader.last_rect
app.reader.hwnd, app.reader.last_rect = 1, (100, 50, 1920, 1080)
app._update_display()
m.PLATFORM.game_in_front, app.reader.hwnd, app.reader.last_rect = front, hwnd, rect
cont = app.hud.content
check(f"the card shows Profit: mesos picked up + loot - potions used ({cont.get('net_label')}: {cont.get('mesos_net')})",
      cont.get("net_label") == "PROFIT" and cont.get("mesos_net") == "+67" and cont.get("mesos_gained") == "+32")
box = tk.Frame(app.root)
app._fill_loot(box, {"bg": "white"}, {"bg": "white"}, 300)
check("Settings lists the loot, each with its price", len(box.winfo_children()) == 1)
box.destroy()
app.reset()
check("Reset starts the pickups again from nothing", app.picked_mesos == 0 and not app.loot and not app.pickups_seen)
app._pickups([("mesos", 99)], m.time.monotonic())
check("...and before Start, nothing is counted", app.picked_mesos == 0)
app.session.started, running = True, app.tracker.running
app.tracker.running = True
app._pickups([("mesos", 40)], m.time.monotonic())
app.tracker.running = False
app._pickups([("mesos", 60)], m.time.monotonic())
check(f"mesos/hr counts only what's picked up while the EXP clock runs, as EXP/hr does "
      f"({app.timed_mesos} of {app.picked_mesos})", app.picked_mesos == 100 and app.timed_mesos == 40)
app._messages([("mesos", 5), ("exp", 9), ("item", "Lupin's Banana", 1, "Etc")], m.time.monotonic())
check("message lines go where they belong: mesos and loot picked up, a kill's EXP",
      app.picked_mesos == 105 and list(app.loot.values()) == [("Lupin's Banana", 1)] and app._kill_exp[-1][1] == 9)
app.reset()
app.session.started, app.tracker.running = False, running
app.cfg["save_captures"] = True
app._save_capture("pickups", cv2.imread(str(ROOT / "tests" / "frames" / "pickups_mesos.png")), [("mesos", 15)])
saved = list((m.DATA_DIR / "captures").glob("pickups-*.png"))
check("with Save pictures on, a picture and what was read are kept in the data folder",
      len(saved) == 1 and "('mesos', 15)" in (m.DATA_DIR / "captures" / "read.txt").read_text(encoding="utf-8"))
app.cfg["save_captures"] = False
app.session.on = False

check("the typeface is the bundled one, or it fell back cleanly", app._own_fonts in (True, False) and bool(app.theme.fam["extra"]))
app.close()

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
