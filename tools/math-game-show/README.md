# Math Game Show

Local teacher app: a private class spreadsheet (Canvas CSV in, session columns out) and a public ESPN-style scoreboard window you screen-share on Zoom. Students never get a URL. Binds **127.0.0.1** only.

**Setting this up on another Mac (you or a supply teacher):** see [TEACHER-SETUP.md](TEACHER-SETUP.md).

## Run during Zoom

From the repo root:

```bash
python3 tools/math-game-show/server.py
```

Opens [http://127.0.0.1:8766/](http://127.0.0.1:8766/). Use `--no-browser` if you already have a tab.

1. **Create New Class** (year / semester / course / Canvas CSV / days / time) or **Start Existing Class**.
2. On the class dashboard, **Begin a New Game**: confirm or change the date, then attendance → teams → names. Cancel returns to the class dashboard. A second game on the same slot is labeled `_2`, `_3`, … rather than jumping to the next class day.
3. Keep the **teacher game** window on your machine. Share the **scoreboard** window on Zoom (`/scoreboard`). There is one board: live scores if a game is running, Final Score right after End Game, otherwise waiting. Live games have three teacher-started rounds (scores stay a running total):
   - **Round 1** Open Question Round — 20:00, starts when you Create Teams
   - **Round 2** Team Challenge Question — 10:00, **Start Round 2** (early start is fine; 0:00 does not auto-advance)
   - **Round 3** Consolidation Round — 10:00, **Start Round 3**
   The student scoreboard shows the same round title and countdown above the ESPN bar.
4. Optional **always-on-top overlay** (stays above Notebook/slides on your Mac; share that window on Zoom if students should see it):
   ```bash
   python3 tools/math-game-show/overlay.py
   ```
   First run installs Electron under `overlay/`. The Game Show server must already be running.
5. **End Game** writes credited individual scores into that date column and attaches a log of immutable events.

Python 3 stdlib only (sqlite3 + `ThreadingHTTPServer`). No pip install. Data lives in `tools/math-game-show/data/` (gitignored): `app.sqlite`, uploaded CSVs, JSONL logs.

Scoring stores **immutable point events** plus separate **individual credited scores** and **team scores**. The class dashboard cell is that student's credited total for the session (individual awards, plus team awards when you choose “each member” or “split”). “Team only” awards raise the ESPN bar without changing individual cells.

The class spreadsheet can add or remove students. Class columns are created by **Begin a New Game**. Next to Sort, **Total · Open Question · Team Challenge · Formative · All** picks one compact slice per lesson or stacked Open / Challenge / Formative (older migrated scores sit in Open Question). **SUBTOTAL** and **TOTAL SCORE** follow that same slice. Beside that group, **Last class · Last week · This year** is the period the live scoreboard **Leaders** ticker uses (saved per class, not in the browser). Last week is the last two scored classes; This year is every scored class on this roster. Spreadsheet cells do not change. While a game is live, the student ticker names **Leaders** and **Most Improved** (when a prior period of the same shape exists) for Open Question, Team Challenge, and Formative; **End Game** replaces it with the winning-team ticker. **Log TOTAL as Subtotal** freezes a named overall snapshot to the right of the last class column. A live **SUBTOTAL** column (left of **TOTAL SCORE**) then only sums newer class columns. Frozen SUBTOTAL columns can be deleted with × the same way class columns can.

On the teacher game, all team boards sit side by side (scroll sideways if they do not fit). **Add Student** next to **Quit Game** puts a roster student who is not present onto a chosen team at 0.

A sanitized Canvas-shaped fixture is at `fixtures/sample-canvas-grades.csv`. Do not commit real gradebook exports (IDs + first names).

## Later

- **Teacher game** — students awarding points to one another (scoring is teacher-only for now).
