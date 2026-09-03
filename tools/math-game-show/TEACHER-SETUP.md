# Math Game Show — setup for another Mac

Plain-language steps for Shawn or a supply teacher. Students never get a link. Everything runs only on **this** computer. You screen-share one window on Zoom so the class can see the scoreboard.

You need the **vlc-math-modernization** folder on the Mac. Remember where you put it (often **Documents**).

**Easiest:** Shawn copies the whole folder (AirDrop, USB, or shared drive). If `tools/math-game-show/data/` is included, **Start Existing Class** will show the roster. If that folder is missing, create the class again from a Canvas CSV.

**From GitHub:** clone the repo, then check out the branch that has the overlay (`game-show-scoreboard-overlay`) if you want the always-on-top bar. A fresh clone has **no** saved classes.

---

## One-time setup (first time on this Mac)

### 1. Check Python

1. Open **Terminal** (Spotlight → type `Terminal`).
2. Paste this and press Return:

```bash
python3 --version
```

You should see something like `Python 3.11` or newer.

If macOS asks to install developer tools, click **Install** and wait. Then run the same command again.

You do **not** need to `pip install` anything for the Game Show itself.

### 2. (Optional) Overlay — scoreboard that stays on top of slides

Only if you want the bar to stay visible while you switch to SMART Notebook or slides.

1. Install **Node** from [https://nodejs.org](https://nodejs.org) (LTS). Restart Terminal after installing.
2. Check it:

```bash
node --version
npm --version
```

You can skip this section and still run the game with a normal browser scoreboard window.

---

## Every live class

Do these in order. Leave Terminal open while you teach.

### 1. Start the program

In Terminal:

```bash
cd ~/Documents/vlc-math-modernization
python3 tools/math-game-show/server.py
```

If the folder is not in Documents, drag the **vlc-math-modernization** folder onto Terminal after typing `cd ` (space), then press Return, then run the `python3 …` line.

A browser tab should open at [http://127.0.0.1:8766/](http://127.0.0.1:8766/).

If a tab is already open, you can start with:

```bash
python3 tools/math-game-show/server.py --no-browser
```

and go to that address yourself.

**Leave this Terminal window running.** Closing it stops the game.

### 2. Open or create the class

On [http://127.0.0.1:8766/](http://127.0.0.1:8766/):

- **Start Existing Class** — use this if this Mac already has the class (Shawn copied the folder **including** `tools/math-game-show/data/`).
- **Create New Class** — first time on this Mac, or if there is no saved class. You will be asked, one step at a time:
  - School year (example: `2026/27`)
  - Semester
  - Course code (example: `MCF3M`)
  - **Canvas gradebook CSV** (needs a **Student** column and an **ID** column; other columns are ignored)
  - Days: **M/W/F** or **T/Th/F**
  - Start time (75-minute period)

Do not email or commit a real gradebook CSV. Keep it on this Mac only.

### 3. Begin a New Game

On the class spreadsheet page:

1. Click **Begin a New Game**.
2. Confirm the date (or **Select Manually**). Past dates are blocked.
3. **Mark Attendance** — check who is in class. The count updates as you click.
4. Assign teams: **Random**, **Balanced** (even sizes, similar career totals), or **Manual**.
5. Inspect names. Click **Create Teams**.

That opens:

- **Teacher Game Dashboard** — you award points here (keep this on your machine; do not share it on Zoom). **ROUND 1 · Open Question Round** starts a 20:00 countdown when you Create Teams.
- **Scoreboard** — a dark ESPN-style bar with the same round title and countdown. **This is the window you share on Zoom.**

If you leave setup without **Cancel**, click **Begin a New Game** again — it starts fresh at attendance.

### 4. Zoom

1. In Zoom, **Share** → pick the **Scoreboard** window only (not the whole desktop, and not Teacher Game).
2. Keep Teacher Game on your screen for +/− buttons.

Students should never see [http://127.0.0.1:8766/](http://127.0.0.1:8766/) in the chat.

### 5. Optional: always-on-top overlay

Use this if you need the bar on top of Notebook or slides **on your Mac**.

Open a **second** Terminal (server stays in the first). From the same repo folder:

```bash
python3 tools/math-game-show/overlay.py
```

Do not pass a class number. The overlay is the same single scoreboard as the browser: current scores if a game is live, Final Score right after End Game, or “waiting” if you are on the class dashboard.

The first time this runs it downloads Electron (needs Node). Drag the strip to move it.

If you want **students** to see the overlay, share **that** overlay window on Zoom instead of the browser scoreboard.

### 6. During the game (Teacher Game)

| Control | What it does |
| --- | --- |
| **Start Round 2** / **Start Round 3** | Starts the next round’s clock (10:00). Only the next round; 0:00 does not auto-advance. Early start is fine. |
| **+1 / +5 / +10** on a team | Then choose **Each member**, **Split**, or **Small Team Bonus** |
| **−5** on a team | Team penalty only; student spreadsheet cells do not change |
| **+1 / +5 / +10 / −1** on a student | That student’s score (and the team bar) |
| **Student View** (beside the title) | Hides prior “strength” totals; click **Show strength** to bring them back |
| **Add Student** | Late arrival from the roster who is not marked present. Pick a team; they start at 0 |
| **End Game** | Saves this class’s scores into the date column and shows **FINAL SCORE** on the scoreboard |
| **Quit Game** | Throws away this game. Nothing is saved. Use if you started by mistake |

### 7. After class

1. Click **End Game** if you want scores kept.
2. In Terminal, click the window and press **Control+C** to stop the server.
3. Close the overlay if you used it.

---

## Next class on the same Mac

1. Start the server (Every live class, step 1).
2. **Start Existing Class**.
3. **Begin a New Game** again.

A second game on the same day/time is labeled `_2`, `_3`, … Next to Sort, **Total · Open Question · Team Challenge · Formative · All** shows one slice per lesson or stacked rounds (older scores sit in Open Question). **SUBTOTAL** and **TOTAL SCORE** follow the selected slice. A frozen **SUBTOTAL** column (Log TOTAL as Subtotal) is still an overall snapshot; new games still go to the right. The × on a freeze deletes that snapshot only; class scores stay and live **SUBTOTAL** recounts from any remaining freeze.

Next to that slice group, **Last class · Last week · This year** chooses which scored classes the Zoom scoreboard **Leaders** ticker uses. Last week means the last two scored classes. This year is every scored class on this spreadsheet. The sheet cells stay the same.

---

## If something goes wrong

| Problem | Try this |
| --- | --- |
| Browser says the site can’t be reached | The server is not running. Start it again in Terminal. |
| `Address already in use` | A copy is already running. Use the tab at [http://127.0.0.1:8766/](http://127.0.0.1:8766/), or quit Terminal and start once. |
| No classes listed | This Mac has no saved data. Use **Create New Class**, or copy `tools/math-game-show/data/` from Shawn’s Mac. |
| Overlay does nothing | Server must be running first. Check Node. Restart the overlay after you Create Teams. |
| macOS blocks Electron | System Settings → Privacy & Security → allow the app, then try again. |
| Scoreboard stuck on an old game | Restart the overlay (`python3 tools/math-game-show/overlay.py`). It should wait until a game is live. |
| Wrong folder | `cd` into **vlc-math-modernization** (the folder that contains `tools`). |

---

## What supply teachers do *not* need

- Students do not install anything.
- There is no class URL to post in Canvas.
- You do not need Cursor, git, or a GitHub login to **run** the game if the folder is already on the Mac.
- Do not share the Teacher Game window or the spreadsheet on Zoom (names and controls stay private).
