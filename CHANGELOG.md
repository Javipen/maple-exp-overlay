# Changelog

## 1.2.0 (in testing)

- **A new message watcher: kills and pickups counted line by line.** The game's messages at the bottom
  right are looked at 10 times a second and read by the game's own font (its digits are always drawn
  the same), instead of being read by Tesseract a couple of times a second. In recordings of play, the
  old way missed close to half the mesos picked up: a line is only bright for about a second, all
  meso lines looked alike to it, and a line it couldn't read was lost. Now:
  - **every line is counted once**, two identical "+16" lines in a row included, and a skill's flash
    over the messages doesn't add any
  - **amounts are read exactly**: on 4K pictures of play, every one of 293 lines (Tesseract read some
    "+15" as 145, "+22" as 272)
  - **item lines are read again** (they were never read: a dimmed line was cut in half)
  - **the slime shows each kill's real EXP**, from its EXP and party bonus lines
- **Mesos/hr is on the same clock as EXP/hr.** Mesos picked up while paused (or idle) still count in
  Mesos gained and Profit, but not toward the rate, as that time isn't counted.

## 1.1.0

- **Pickups, read from the game's white message lines.** "You have gained mesos (+15)" and "Lupin's
  Banana x1 earned. (Etc)" are read as they come in (with the bundled Tesseract, a few ways at once,
  keeping what most readings agree on; anything unclear is left out).
  - **Mesos picked up are exact**, inventory open or not: Mesos gained and Mesos/hr use them.
  - **Loot** is counted, and priced at what NPC shops pay (Settings > Loot; Lupin's Banana and Green
    Mushroom Cap come with meowdb's prices, anything else is set there).
  - **Profit** takes Net session's place on the card: mesos picked up + loot - potions used.
  - **Potions picked up** are known exactly: Pot cost counts a rise as picked up only as far as the
    pickup lines say; any more is a purchase.
- **Save pictures for testing** (Settings, off by default): keeps pictures of the potion popup and the
  pickup lines, with what was read from them, in the data folder's captures folder, on this PC only.
- **Pot cost: what your potions cost, from the controller's potion popup.** Playing with a controller,
  hold the left trigger for a moment now and then: the overlay reads how many of each potion you have
  and prices what you've used at the NPC shops' prices (Red 50, Orange 150, Blue 220, Lemon 150,
  Meat 80, from meowdb.com). It shows as **Pot cost** in the repot row, in place of Trip time, from
  your last ring of the bell.
  - Items are recognised by their icons; names and prices can be changed in Settings > Potions.
  - Counts that go up (buying more) aren't counted as used. Nothing counts while paused.
  - Not looked at for 10 minutes while tracking: it's marked with a "~".
  - Potions picked up from monsters (a few more in the field) make up for some used. A big rise, or
    any in town, is a purchase and isn't counted.
  - Misreads are kept out: a count read with a digit lost (1068 as 106) is never counted, nor is a
    drop bigger than you could use in the time since the last look unless 8 looks agree, and a drop
    that's back where it was within 5 minutes is taken back.
- **EXP/hr is the real rate when you hunt in waves.** Tracking used to pause itself after 15 s without
  EXP, and cut that time out. So every respawn wait went missing, and EXP/hr could read 2-3x too high.
  - It now pauses itself after 2 minutes without EXP, a real break. If you had the old 15 s, it becomes
    2 minutes; a time you picked yourself is kept (Settings > Pause after no EXP for).
  - The EXP that ends a pause on the same map now counts: it was earned there.
- **The slime hops whenever EXP comes in, paused or not.** It used to stay still while tracking was
  paused (by itself or with Pause), and the first kill after each pause got no hop.
- **No more hops from damage numbers.** The slime hops on kills from the game's yellow EXP lines, and
  damage numbers by the messages can look like them. Now a line only counts if the EXP bar goes up
  within a few seconds of it, each rise backing one line. The lines make the slime hop only while most
  of the recent ones were real; otherwise it hops from the EXP bar. A hop from the EXP bar waits for the
  next reading to agree, so a misread can't make it hop.
- **Mesos spent counts your shop trips.** Spending happens in town, where tracking is paused, and the
  mesos used to stop counting there, so "spent" stayed at 0.
  - The mesos now keep counting from Start until Reset, through every pause, Pause included: you
    often pause to go shopping.
  - A big buy or sale (more than half your mesos) counts too, once it's read 3 times in a row. If it
    goes back to about what it was, it was a misread, and it's taken back.
  - Repotting on the way to a different map: what the trip cost goes with that map's numbers.
- **Mesos misreads with the front digits missing are caught.** If something lies across the mesos box,
  it could be found too narrow and the number read cut short (1,251,272 as 1,272). That showed up as
  millions spent and earned, a wildly negative mesos/hr, and a wrong repot trip.
  - A reading now only counts if the number ends inside the box, with room to its left.
  - A count that's the end of the other (1,272 and 1,251,272) is never counted as spent or earned.
  - A big change that goes back to about what it was within 5 minutes is taken back, even with mesos
    picked up in between.
- **No more "Can't read the EXP bar" just after a level-up.** Low in a level (say 0.03%) the % is too
  rough to tell your level from the EXP table, so it waited for an EXP gain, and counted every reading
  meanwhile as a failure. Now it uses your level from last time (or the next) when the reading fits it,
  and a reading it can't place yet is no longer a failure.
- **Clear the repot history:** Settings > Clear repot history, or the card's right-click menu (Shift+5).
  The last repot, the last trip and its time go blank until you ring the bell again.
- **Opening and closing the inventory no longer loses mesos.** Every change from one reading to the
  next counts, however long the inventory was closed and whatever paused in between.

## 1.0.2

- **Tracking keeps going past 999,999 EXP.** From 1,000,000 EXP the game stops showing the EXP number
  and shows only the % (`EXP. [98.74%]`). The overlay used to lose the EXP bar there and freeze until
  the next level-up. This hits from Lv 57 (the last 3% of the level), and gets worse with level: about
  half of Lv 70 and nearly all of Lv 90.
  - The EXP bar is now found by its `%` when the number is hidden.
  - EXP is worked out from the % and your kills, and shown with a `~`. It's kept to the range the % allows,
    so it never drifts. EXP/hr, the time to level and the session total stay right, to 0.01% of the level.
  - When the number shows again (after the level-up), it takes over again.
  - Started while the number is hidden: it uses your level from last time, and says so.
- **The slime hops on every kill, not just once per EXP reading.** The overlay watches the game's yellow
  EXP messages ("You received EXP (+38)") a few times a second, without reading them, only noticing new
  ones. Each one makes the slime hop with the EXP it's worth, learned from the EXP you actually gain (in a
  party, the party-bonus line is counted in). If those messages are off or covered, it hops from the EXP
  readings as before; with the number hidden, a hop for each kill a % step is likely worth.

## 1.0.1

- **Mesos/hr with the inventory closed:** you no longer need to keep the inventory open.
  - Mesos/hr is worked out over the time your mesos were last seen, so it holds steady while the
    inventory is closed instead of sinking.
  - When you open it again, everything picked up meanwhile counts.
  - While it's closed, Net session, Mesos gained and Since repot keep going as estimates, marked "~".
- **TO LV. once:** with the session row below (Session and Full), the time to the next level is no
  longer shown twice. The EXP gauge stretches across the card instead.
- **The repot row is optional:** turn it off in Settings or in the card's right-click menu, and the
  Full layout gets shorter.

## 1.0.0: the local-only edition

Based on beanguri's Maple Classic Exp Overlay 0.9.10-beta.

**Local only**
- The update check, run sharing (mcwo.app) and the GitHub bug-report page are removed. "Report a bug"
  now just saves its details in the data folder. Any network connection to anywhere but this PC is
  refused.

**The tracker card over the game**
- A MapleStory-style card:
  - a wooden header and a cream body
  - the LV. badge with orange tiles
  - a green EXP gauge
  - the time to the next level
- **Layouts:** Minimal, Session EXP and Full (with mesos and the repot row).
- **Themes:** Brown, Orange, Blue and Steel.
- **The Maplestory typeface.**
- **The slime:**
  - it bobs while tracking
  - it hops with "+EXP" floating up on every gain
  - it hops three times on a level up
  - it sleeps when paused
  - it gets dizzy when you die
  - it shows "?" when the EXP bar can't be read
- **Edit mode (Shift+5):**
  - drag the card to move it
  - the right edge makes it wider
  - the bottom edge makes it taller
  - the corner scales it
  - right-click for layout, theme and size

  Its place is kept relative to the game window.
- **Calibration:** the first 5 minutes of tracking calibrate. EXP/hr, mesos/hr and the time to level
  wait until then, with a countdown in the header.
- **Mesos gained and mesos spent** are kept apart, as well as the net.

**The repot bell (Shift+3)**
- Ring it after each shop trip. The card shows:
  - the mesos at the last repot
  - what you've made since
  - the exact profit of the last trip (from one ring to the next, from your actual mesos)
  - the time since
- Each ring shakes the bell and shows the trip's profit in the header. The repot is saved across
  restarts.

**Simpler**
- The main window is just Start/Pause, Reset, the repot bell, Move card and Settings.
- The EXP log, notes, "Log this run", compact mode and its shapes, the stat tiles and the details
  panel are gone.

## 0.9.10-beta

**When the minimap's name is covered**
- After 3 seconds without the minimap's name, the status line says so ("Can't see the minimap's name. Still tracking...") and says when it is back. A short cover changes nothing in your run; it still pauses after 45 seconds.
- While the name is hidden, nothing new is saved to the log. If the name comes back on the same map, the hidden time counts. If it comes back on a different map, the stretch that was in progress is dropped (stretches already saved stay as they were), so the new map's EXP is never saved under the old map.
- It looks for a missing name every second (was every 3), and retries an unreadable one every 2 seconds (was 5).

**Compact shapes and what's new**
- **Compact mode has three shapes:** **Square** (as before), **Wide** (longer sideways, with a row of four numbers like a streamer's tracker) and **Bar** (one thin strip). Pick one in Settings, under Compact shape. The full view and the log are unchanged.
- **A new install picks its shape on the first launch**, from three small pictures.
- **A short "What's new" window** shows once after you update to a new version (never on a first install).

**New address**
- The best-maps website is now at **mcwo.app** (opening the old maple-classic-exp-overlay.pages.dev page now sends you there; older overlays keep working on the old address for now). The overlay's Best maps button and sharing use the new one.

**Cleaner map names**
- A map's name is now put right against the game's own list of maps (from meowdb.com, thank you): OCR slips like "ist Accompaniment" for "1st Accompaniment" or "Boar Il" for "Boar II" become one map, in your log and on the best-maps site. A map that isn't on the list (an event map, a new map) is used as it was read.
- **The minimap's name can't be seen** (hidden, covered): after 45 seconds the overlay pauses instead of carrying on under the map you were on before, and carries on when it can read the name again. (This is likely why a town like Perion could end up in people's logs.)
- **Towns** aren't logged ("In a town: nothing is logged here"), and the site turns town runs down.

**The best-maps website: harder to fake**
- A new player's runs start counting a day after their first, and after 4 runs, so made-up players can't change the numbers at once. Runs from a banned player are refused and hidden.
- Runs are checked before anything is stored: the job must be a real job, the map name plain English text (adverts, links and look-alike letters are turned down), and EXP and mesos must be believable for the level.
- One home's IPv6 addresses count as one connection, a day's new players and runs have a limit (so a flood can't use up the database), and nobody farms more than 12 different maps a day.
- A map name the game doesn't list shows only once 3 players have shared it on 2 different days.

**The best-maps website**
- **Include new players.** A new box in Best maps and the ladder shows the players who joined lately and don't count yet (they count for everyone after a day and 4 runs). Maps that use them say so, their dots are faded, and the header shows how many are new. Off by default; map names the game doesn't list never come from new players.
- **A closer level range.** A map's numbers start from players within 3 levels of yours, and go to 5 or 8 only if the map needs more players to be ranked (the row says "wider range").
- **% of a level per hour and time per level** on every map ("about 14 h per level"), which compares players at different levels.
- **Sort** by EXP/hr, %/hr or mesos/hr, and **hide early data**.
- **Details** under every map: solo vs party, EXP/hr at each level, and the last 7 days against before.
- **Leveling ladder:** the best map for each 5 levels of your class and path.
- **Copy link**: the address always holds your level, class, path and sort, so a view can be shared.
- It tells you when the newest run was shared.
- **Maps at your level come first**: maps that had to use players from further away (up to 8 levels) are listed under their own "Wider range" heading.
- **Dots are coloured by the player's level** against yours (4+ lower, 1–3 lower, your level, 1–3 higher, 4+ higher), so you can see which level the high-EXP players are at, and hovering a dot or a level bar in Details says the level and number of players.

## 0.9.9-beta

**New**
- **Smaller sizes:** Settings > Size now goes down to 70% and 60% (it was 80%). The smallest text stops at a readable size.
- **Transparency stops at 40%.** More and the window could fade so far that you couldn't find Settings again. A higher saved value becomes 40%.

**Fixed**
- **Hotkeys in Settings:** clicking one to change it could leave several looking selected (and the first click could fail with an error). Now only one waits for keys at a time: clicking another moves it, clicking the same one again (or Esc) cancels, and hotkeys come back right away if you turn them off or click away from Settings.
- A long number in the three small panels (like 99,999,999) now shrinks to fit instead of running out of its box.
- **Select area** crashed on Windows ("expected integer but got bold") since 0.9.7. It opens again.

**Better bug reports**
- The report now also saves **bug-report.txt**: your screens and Windows scaling, the game window, where each part (EXP bar, mesos, minimap, party list) was found, how the reading thread is doing, what the overlay last said (with times), and the end of errors.log. At the top it lists what looks wrong, like "the overlay is covering the class name".
- The picture now also shows where the class and map name are read, with a line on how each went. View capture shows the same, and says why the class wasn't read.
- Optionally (it asks) a small picture of the whole game window, saved only if you say yes.

## 0.9.8-beta

**Lighter and more reliable**
- **Far less CPU:** the overlay used a thread per core for tiny pictures, and the threads mostly waited on each other. It now uses one. In a test on a 1080p game window that took a normal look from about 100 ms of CPU to 12 ms, and the look at the whole window (every 4 seconds) from about 350 ms to 60 ms. How much you gain depends on your core count; the more cores, the more.
- Reading the map name and your class asks Tesseract once instead of six times (about 4.5 times faster), with the same answers.
- **Storage and trade windows:** they show a second mesos box next to yours, and the overlay could read that one instead. It now sees there are two, reads neither, and picks yours up again once the window closes (thanks to Xoere, whose recording showed it).
- Map names and classes now also work when your Windows user name has letters your PC's text code page can't hold (some non-Latin names): Tesseract couldn't open its pictures there, so they were never read.
- `pytesseract` isn't used any more: one less package in the download.
- Thanks to thanhphamvan, whose fork's measurements pointed at the extra threads and the six Tesseract runs.

**A new look**
- The whole overlay is redrawn: a big glowing EXP/hr, a live trend line (is your pace rising or falling vs the last 5 minutes?), a progress bar to your next level, a status pill (looking for the game / ready / tracking / paused), and one clear Start button. The title bar matches the window (Windows 10 and 11).
- Less clutter: three key numbers up front (last 5 min, mesos/hr, elapsed); everything else is one click away under **Details**, and it remembers if you opened it.
- "Don't log" is now a switch called **Log this run** (no more double negatives). Select area and View capture are under the **...** menu.
- Log, notes, Settings and the update window match, with a bundled typeface (Manrope, SIL Open Font License), real switches, a gold slider and thin scrollbars.
- **Compact mode shows mesos/hr** too, under the EXP/hr number, in the same window size.
- Just as light as before: the numbers move pre-drawn pictures instead of redrawing, and nothing is redrawn while nothing changes.

## 0.9.7-beta

**New**
- **Notes on your log lines:** click a line and write a note about it in the box under the log. A ✎ in the new **Note** column marks lines that have one. Notes save as you type and **stay on your PC only**: they live in your log file, are never shared with the best-maps site, and the overlay's tests check that nothing the overlay sends can include them. Renaming a map (even when it merges two lines), farming more on a line, and updating keep your notes. Deleting a line asks first, and says its note goes too. Typing in the box doesn't trigger the hotkeys.
- **A backup of your log each day** (the last 7, in a `backups` folder next to it), so a recent copy of your log and notes is always there.
- **One log line per party**: playing a map in a party, people join and leave, and each party size used to get its own line. Now a map's party sizes on a day share one line (e.g. "4–5ppl ▸"), with EXP/hr and mesos/hr over all of that time. Click it to see a line for each party size, to compare them. Solo play keeps its own line. Your log itself isn't changed, only how it's shown.
- **macOS** (running from source; thanks to thanhphamvan): works on a Mac with the game in CrossOver, Whisky, Parallels or a game stream. It finds the game window by itself, as on Windows. See "For developers" in the README. Hotkeys aren't available on macOS yet. It has only been tried with the game by its author; the maintainer has no Mac, so reports are welcome.

**Fixed**
- Mesos: a small change across a digit boundary, like 9,980 to 10,020, is now counted instead of dropped (thanks to TaylorAdamCA).
- Typing "!" in a note or a map name no longer triggers the Shift+1 hotkey.

**Safer**
- Downloads are now built by GitHub from this code, never on a PC, from exact package files checked against their SHA-256. Each one gets an attestation anyone can check (`gh attestation verify`), and its SHA-256 is on the release page and on the build's page (the same one in all three places). See "Where the download comes from" in the README.
- Built with Pillow 12.3, which fixes the problems published since 11.3. (The overlay never opens image files from elsewhere, so it wasn't affected, but now nothing is flagged.)
- It never runs a `tesseract.exe` from the folder it happens to be started from, only its own or an installed one.
- Sharing is off whenever the overlay runs in test mode (`MAPLE_EXP_DATA` set), so made-up test runs can never reach the real site.

**The best-maps website**
- Security headers: browsers only run the site's own scripts, and other sites can't show it inside theirs.
- Map names that are web addresses are turned down, and early data only shows map names at least 2 players have shared, so one person can't put a made-up name on the site.
- Much lighter on its database (small summary tables, and answers shared between Cloudflare's locations), so it keeps working as more people use it.
- Every player is a dot on a chart: each map's row shows what every player gets there, the typical EXP/hr, what the middle half of players get, and the runs left out as unusual. Point at (or tap) anything for the numbers.
- Maps with fewer than 3 players are shown greyed out as early data, instead of "No maps here yet".
- A bar at the top shows the latest runs players shared, one at a time; click one to see the best maps for that level, class and party. One run per player, only maps at least 2 players have shared, and nothing unusual, so made-up runs stay off the front page.

## 0.9.6-beta

**New**
- **Compact mode** (the Compact button at the top, or Shift+4): a much smaller window with just EXP/hr, when you'll level, the last 5 minutes, mesos/hr and Start/Pause. The button then says Full, to bring everything back.
- **Hotkeys**, while MapleStory or the overlay is in front: Shift+1 Start / Pause, Shift+2 Reset, Shift+3 Don't log, Shift+4 Compact. You can change them, or turn them off, in Settings.
- **Size** in Settings: makes the text and the window smaller or bigger, from 80% to 200%, and changes right away. It stops at 80%, so it can't get too small to read or use.

**Fixed**
- On a second screen with different Windows scaling (e.g. a 1440p screen at 125% next to a 5K one at 200%), the window was far too big. It's now sized for the screen it's on, and resizes when moved.
- The Settings window could stay at its old size after the size changed, cutting its contents off. It now always fits, and opens over the overlay (on the same screen).

## 0.9.5-beta

**Fixed**
- Dying (losing a few % of your EXP) made EXP gained and EXP/hr go down, even below zero. Now the loss isn't counted: it says how much you lost and carries on.
- A run the best-maps site turned down was sent again every 30 minutes, and enough of them could stop newer runs from being shared.
- A log file edited by hand (or damaged) could stop the overlay from starting. Lines it can't use are now left out (a copy of the file is kept), and it starts as usual.

**The best-maps website** (already live)
- Checks every run harder: no more EXP than the levels it covers hold, and start times in a sensible range.
- Only 6 players can share from one internet connection a day, so it's much harder to make up players.
- Other websites can't make their visitors' browsers send runs.
- The code kept for spam limits can no longer be turned back into an internet address.

## 0.9.4-beta

**New**
- **Best maps** link at the top of the overlay: opens the best-maps website already showing the maps for your level, party size and class.
- **Runs for the best-maps site are 15 minutes** instead of an hour, so more of your farming counts (a break or a new map no longer throws away up to an hour).
- **Smaller download:** 82 MB instead of 98 MB. Parts of the libraries it never used are left out.
- **The best-maps website** fills in sooner: it now looks 5 levels either way, and counts parties of any size together (solo or party).
- Downloads can now be built on GitHub straight from the code, the first step toward code signing (so Windows stops warning about new downloads).

## 0.9.3-beta

**New**
- **New name: Maple Classic Exp Overlay** (it was Maple EXP/hr). The program is now `Maple Classic Exp Overlay.exe`. Your log and settings carry over.
- **Class**: read from the status bar (never your character's name), shown in the log, and different classes get separate lines.
- **1-hour runs**: the overlay counts toward a full hour of non-stop farming on one map with the same party size, and saves each finished hour (these are what the best-maps site uses). A level-up doesn't stop a run; a break, a new map or a new party size starts it over.
- **Best maps for your class**: if you agree (it asks once), finished 1-hour runs are shared anonymously with the new stats site, maple-classic-exp-overlay.pages.dev, which shows the best maps for each class and path (named after its 4th job, like Night Lord or Bishop), level and party size. You can change this in Settings.
- **Sealed log**: every line the overlay writes is sealed, so lines edited or added by hand are greyed out, and an edited 1-hour run is never shared. Deleting a line, or renaming a map in the overlay, keeps everything else trusted.

**Fixed**
- The update window showed names with underscores wrong (e.g. explog.txt).

## 0.9.2-beta

**New**
- **Time** in the log: how long each line is based on, growing by 5 minutes with every update. Also in copied lines and `exp_log.txt`.
- **Report a bug** button (in Settings and in View capture): opens a bug report on GitHub, already filled in with what the overlay was reading, plus a picture to drag in. Nothing is sent until you click Submit.
- **Reset** now also looks for the minimap, inventory and party list again, so it picks them up after you move them.

**Fixed**
- The map name wasn't read when the overlay's window was right next to the minimap (but not covering it). The same is fixed for the party list.

## 0.9.1-beta

**New**
- **Pauses by itself** when you get no EXP for 15 seconds (you can change this in Settings) or leave the map, and carries on at your next EXP gain. Paused time doesn't count toward EXP/hr. On a new map, the numbers start from zero.
- **Don't log** button: keeps the current run out of the log while still showing everything. It turns itself off when the run ends.
- **Update check:** tells you when a new version is out and what's new, so you can decide whether to update. You can turn it off, or check by hand, in Settings.
- Your level shows within seconds of opening, and the time to your next level sits right under EXP/hr, like "Lv 31 in 36 min".
- The log's columns can be resized, and the window can be made bigger while the log is open. Both are remembered.

**Fixed**
- In a 1920x1080 window, the EXP number could be read with its first digits cut off.
- Mesos weren't read when the overlay's window was close to (but not covering) the inventory, or when a see-through inventory was in front of a busy background.

**More reliable**
- Much stronger protection against misread numbers, tested with thousands of simulated misreads and screenshots over many backgrounds: a misread can't pass for a level-up, a lost level, or extra mesos.
- Clearer status messages. "Select area" and "Follow game" are now one button.
- Safer saving: a damaged log file is backed up before anything is saved over it, and damaged settings fall back to the defaults.

## 0.9.0-beta

First public beta.

- Shows your EXP per hour, % per hour, EXP gained, and time to your next level.
- Finds the MapleStory window by itself, at any window size, windowed or fullscreen.
- Reads your mesos from the inventory, even while it's see-through during fights, and shows mesos per hour and per 5 minutes.
- Notices whether you're solo or in a party, and how many people are in it.
- Reads the map name from the minimap.
- Keeps a log of your EXP/hr and mesos/hr for each day, map, level and party size, updated every 5 minutes of non-stop farming.
- Settings for the break length, window transparency and always-on-top.
- Saves your settings and log in `%APPDATA%\MapleEXP`, and writes problems to `errors.log` there.
