"""Makes assets/items.txt, the game's item list that itemwatch.py names items from: each item that can
drop or be bought (Cash Shop items left out), with its inventory tab, from meowdb.com's list of every
MapleStory Classic item (meowdb.com/msclassic/item-db/all). One line each: the tab, a tab character,
the name ("(Lv 30)" left off).

This is the only thing here that goes online, and only when it's run by hand, to make the list that
comes with the overlay: the overlay itself never does. Run from the project folder:
py -3 tools/make_items.py"""
import html
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://meowdb.com/msclassic/item-db/all"
TABS = ("Equip", "Use", "Setup", "Etc", "Cash")


def main():
    page = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=60)
    text = page.read().decode("utf-8")
    text = text[text.find("<main"):]
    # The page lists the items under a heading for each tab, each item a link to its own page.
    tok = re.compile(r'<h[1-6][^>]*>(.*?)</h[1-6]>|<a[^>]*href="/msclassic/item-db/(\d+)"[^>]*>(.*?)</a>', re.S)
    tab, rows = None, set()
    for m in tok.finditer(text):
        if m.group(1) is not None:
            heading = re.sub("<[^>]+>", "", m.group(1)).strip()
            tab = heading if heading in TABS else tab
        elif tab and tab != "Cash":
            name = html.unescape(re.sub("<[^>]+>", "", m.group(3))).strip()
            name = re.sub(r"\s*\(Lv \d+\)$", "", name)
            if name:
                rows.add((tab, name))
    out = ROOT / "assets" / "items.txt"
    out.write_text("".join(f"{t}\t{n}\n" for t, n in sorted(rows)), encoding="utf-8", newline="\n")
    print(f"{out.name}: {len(rows)} items")


if __name__ == "__main__":
    main()
