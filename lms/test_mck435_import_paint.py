"""MCK-435: imported bank questions paint before deck refresh and teacher poll.

The content panel is drawn from the import response. A deck GET runs only
when that response has no deck, and it does not block the paint. The
teacher's own /state poll is background and quiet, so the pending
Reconnecting strip stays down. Nothing here fetches student /state.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
STAFF_JS = LMS_DIR / "static" / "staff_ap.js"
NODE = shutil.which("node")

FUNCTIONS = (
    "applyLiveMcImportPayload",
    "absorbImportedPlacement",
    "staffPollShowsPendingStrip",
    "staffPollQueueIsQuiet",
    "staffPollCoalesceArmsStrip",
    "staffPollMayApplyDeck",
    "scheduleQuietTeacherPoll",
    "settleTeacherDeckFollowUp",
    "refreshLessonDeckMetadata",
    "importLiveMcFromBank",
    "importLiveContentQuestions",
    "relocatePlaylistItem",
)


def _function_source(js: str, name: str) -> str:
    """Return one top-level function from ``staff_ap.js``.

    Args:
        js: Whole script text.
        name: Function name to extract.
    """
    start = -1
    for prefix in ("\nasync function ", "\nfunction "):
        at = js.find(f"{prefix}{name}(")
        if at >= 0:
            start = at + 1
            break
    if start < 0:
        raise AssertionError(f"missing {name}")
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        char = js[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const log = [];
let releaseDeck;
let releasePoll;
const deckPending = new Promise((resolve) => { releaseDeck = resolve; });
const pollPending = new Promise((resolve) => { releasePoll = resolve; });
let deckSettled = false;
let pollSettled = false;
deckPending.then(() => { deckSettled = true; });
pollPending.then(() => { pollSettled = true; });
const ctx = {
  classId: 4,
  teacherState: { live_module: "M1", live_slot: "C1", stage: "round" },
  liveSessionId: 9,
  lastLiveMetadata: { questions: [], items: [] },
  lastQuestionCards: [],
  lastLiveItems: [],
  staffStateNeedsFull: false,
  teacherDeckEpoch: 0,
  readLiveSessionId: () => ctx.liveSessionId,
  currentLivePageNumber: () => 2,
  adoptLiveItemsSnapshot: (items) => { ctx.lastLiveItems = items; },
  questionCardsFromMetadata: (rows) => (Array.isArray(rows) ? rows : []),
  applyLocalPlaylistCardChange: () => { log.push("local-card"); },
  paintLiveQuestionCards: () => {
    const questions = (ctx.lastLiveMetadata && ctx.lastLiveMetadata.questions) || [];
    log.push("paint-cards:" + questions.map((row) => String(row && row.id || "")).join(","));
  },
  paintQuestionArtifact: () => { log.push("paint-artifact"); },
  pollLiveSessionAttendees: (opts) => {
    log.push("poll:" + JSON.stringify({
      quiet: Boolean(opts && opts.quiet),
      full: Boolean(opts && opts.full),
      force: Boolean(opts && opts.force),
    }));
    return pollPending;
  },
  api: (url) => {
    const path = String(url || "");
    log.push("api:" + path);
    if (path.includes("/api/student") || path.includes("/state")) {
      return Promise.reject(new Error("student or session state from the paint path"));
    }
    if (path.includes("deck?fresh")) return deckPending;
    return Promise.resolve(input.payload);
  },
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
const out = { scenario: input.scenario };

function paintAt() {
  return log.findIndex((row) => String(row).startsWith("paint-cards"));
}

(async () => {
  if (input.scenario === "predicates") {
    out.quietHidesStrip = ctx.staffPollShowsPendingStrip({ quiet: true }) === false;
    out.intervalShowsStrip = ctx.staffPollShowsPendingStrip({}) === true;
    out.newsShowsStrip = ctx.staffPollShowsPendingStrip({ fromNews: true }) === true;
    out.staleSkipsDeck = ctx.staffPollMayApplyDeck(0, 1) === false;
    out.sameEpochApplies = ctx.staffPollMayApplyDeck(1, 1) === true;
    out.quietQueueAlone = ctx.staffPollQueueIsQuiet({ quiet: true }, null) === true;
    out.loudQueueAlone = ctx.staffPollQueueIsQuiet({}, null) === false;
    out.quietCannotSilenceLoud = ctx.staffPollQueueIsQuiet({ quiet: true }, { quiet: false }) === false;
    out.loudClearsQuiet = ctx.staffPollQueueIsQuiet({}, { quiet: true }) === false;
    out.twoQuietsStayQuiet = ctx.staffPollQueueIsQuiet({ quiet: true }, { quiet: true }) === true;
    out.newsIsLoud = ctx.staffPollQueueIsQuiet({ quiet: true }, { fromNews: true }) === false;
    out.intervalArms = ctx.staffPollCoalesceArmsStrip({}) === true;
    out.newsArms = ctx.staffPollCoalesceArmsStrip({ fromNews: true }) === true;
    out.quietDoesNotArm = ctx.staffPollCoalesceArmsStrip({ full: true, force: true, quiet: true }) === false;
  } else if (input.scenario === "two-imports") {
    const decks = [];
    let importPosts = 0;
    ctx.api = (url) => {
      const path = String(url || "");
      log.push("api:" + path);
      if (path.includes("/api/student") || path.includes("/state")) {
        return Promise.reject(new Error("student or session state from the paint path"));
      }
      if (path.includes("deck?fresh")) {
        let release;
        const pending = new Promise((resolve) => { release = resolve; });
        decks.push({ release, pending });
        return pending;
      }
      importPosts += 1;
      return Promise.resolve(importPosts === 1 ? input.first : input.second);
    };
    await ctx.importLiveMcFromBank({ question_id: 1 });
    await ctx.importLiveMcFromBank({ question_id: 2 });
    const ids = () => ((ctx.lastLiveMetadata && ctx.lastLiveMetadata.questions) || []).map((row) => String((row && row.id) || ""));
    out.beforeLate = ids();
    out.decks = decks.length;
    out.epoch = ctx.teacherDeckEpoch;
    decks[0].release({ live_metadata: { questions: [{ id: "q1" }], items: [] } });
    await decks[0].pending;
    await new Promise((resolve) => setImmediate(resolve));
    out.afterLate = ids();
    decks[1].release({ live_metadata: { questions: [{ id: "q1" }, { id: "q2" }], items: [] } });
    await decks[1].pending;
    await new Promise((resolve) => setImmediate(resolve));
    out.afterFresh = ids();
  } else if (input.scenario === "bank-with-deck") {
    const done = ctx.importLiveMcFromBank({ question_id: 42 });
    const winner = await Promise.race([
      done.then(() => "import-done"),
      deckPending.then(() => "deck-done"),
      pollPending.then(() => "poll-done"),
    ]);
    out.winner = winner;
    out.deckStarted = log.some((row) => String(row).includes("deck?fresh"));
    out.deckSettled = deckSettled;
    out.pollSettled = pollSettled;
    out.paintBeforePoll = paintAt() >= 0 && paintAt() < log.findIndex((row) => String(row).startsWith("poll:"));
    out.paintedNew = log.some((row) => String(row).startsWith("paint-cards:") && String(row).includes("q-new"));
    out.epoch = ctx.teacherDeckEpoch;
    out.pollQuiet = log.some((row) => row === 'poll:{"quiet":true,"full":true,"force":true}');
    out.studentFetch = log.some((row) => String(row).includes("/api/student"));
  } else if (input.scenario === "bank-without-deck") {
    const done = ctx.importLiveMcFromBank({ question_id: 42 });
    const winner = await Promise.race([
      done.then(() => "import-done"),
      deckPending.then(() => "deck-done"),
      pollPending.then(() => "poll-done"),
    ]);
    out.winner = winner;
    const deckAt = log.findIndex((row) => String(row).includes("deck?fresh"));
    out.paintBeforeDeck = paintAt() >= 0 && deckAt > paintAt();
    out.paintBeforePoll = paintAt() < log.findIndex((row) => String(row).startsWith("poll:"));
    out.deckSettled = deckSettled;
    out.pollSettled = pollSettled;
    releaseDeck({ live_metadata: { questions: [{ id: "q-new" }], items: [] } });
    releasePoll();
    await done;
    await new Promise((resolve) => setImmediate(resolve));
    out.deckSettledAfter = deckSettled;
    out.repainted = log.filter((row) => String(row).startsWith("paint-cards:")).length >= 2;
  } else if (input.scenario === "contest") {
    const done = ctx.importLiveContentQuestions([{ question_id: 7, module: "M1" }]);
    const winner = await Promise.race([
      done.then((count) => "import-done:" + count),
      pollPending.then(() => "poll-done"),
    ]);
    out.winner = winner;
    out.pollSettled = pollSettled;
    out.paintBeforePoll = paintAt() < log.findIndex((row) => String(row).startsWith("poll:"));
    out.deckStarted = log.some((row) => String(row).includes("deck?fresh"));
  } else if (input.scenario === "relocate") {
    const done = ctx.relocatePlaylistItem("q-old", { action: "remove" });
    const winner = await Promise.race([
      done.then(() => "import-done"),
      pollPending.then(() => "poll-done"),
    ]);
    out.winner = winner;
    out.pollSettled = pollSettled;
    out.paintBeforePoll = paintAt() < log.findIndex((row) => String(row).startsWith("poll:"));
    out.deckStarted = log.some((row) => String(row).includes("deck?fresh"));
  }
  out.log = log;
  console.log(JSON.stringify(out));
})().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


def _run(scenario: str, payload: dict | None = None, **extra: object) -> dict:
    """Run one paint-order scenario against the real staff functions.

    Args:
        scenario: Harness scenario name.
        payload: Import or playlist response body.
        **extra: Extra JSON fields, such as the two import payloads.
    """
    js = STAFF_JS.read_text(encoding="utf-8")
    src = "\n".join(_function_source(js, name) for name in FUNCTIONS)
    body = json.dumps({"scenario": scenario, "src": src, "payload": payload or {}, **extra})
    result = subprocess.run(
        [NODE or "node", "-e", HARNESS],
        input=body,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is required")
class ImportPaintOrderTests(unittest.TestCase):
    """The panel is on screen before deck refresh and the teacher poll finish."""

    def test_pending_strip_stays_down_for_teacher_polls(self) -> None:
        """quiet polls skip Reconnecting. An older poll does not wipe the deck."""
        out = _run("predicates")
        self.assertTrue(out["quietHidesStrip"], out)
        self.assertTrue(out["intervalShowsStrip"], out)
        self.assertTrue(out["newsShowsStrip"], out)
        self.assertTrue(out["staleSkipsDeck"], out)
        self.assertTrue(out["sameEpochApplies"], out)
        self.assertTrue(out["quietQueueAlone"], out)
        self.assertTrue(out["loudQueueAlone"], out)
        self.assertTrue(out["quietCannotSilenceLoud"], out)
        self.assertTrue(out["loudClearsQuiet"], out)
        self.assertTrue(out["twoQuietsStayQuiet"], out)
        self.assertTrue(out["newsIsLoud"], out)
        self.assertTrue(out["intervalArms"], out)
        self.assertTrue(out["newsArms"], out)
        self.assertTrue(out["quietDoesNotArm"], out)

    def test_late_deck_fetch_does_not_wipe_a_newer_import(self) -> None:
        """A slow deck?fresh=1 from the first import is dropped after the second."""
        out = _run(
            "two-imports",
            first={
                "ok": True,
                "placement": {"item_id": "q1", "item": {"id": "q1", "text": "stem"}},
            },
            second={
                "ok": True,
                "placement": {"item_id": "q2", "item": {"id": "q2", "text": "stem"}},
            },
        )
        self.assertEqual(out["decks"], 2, out)
        self.assertGreaterEqual(out["epoch"], 2, out)
        self.assertIn("q1", out["beforeLate"], out)
        self.assertIn("q2", out["beforeLate"], out)
        self.assertIn("q2", out["afterLate"], out)
        self.assertEqual(out["afterFresh"], ["q1", "q2"], out)

    def test_bank_import_with_deck_paints_before_poll_resolves(self) -> None:
        """A response that already has the deck skips the deck GET."""
        out = _run(
            "bank-with-deck",
            {
                "ok": True,
                "live_metadata": {
                    "questions": [{"id": "q-existing"}],
                    "items": [{"id": "q-existing"}],
                },
                "placement": {
                    "item_id": "q-new",
                    "item": {"id": "q-new", "text": "stem"},
                },
                "question_cards": [{"id": "q-new", "text": "stem"}],
            },
        )
        self.assertEqual(out["winner"], "import-done", out)
        self.assertTrue(out["paintedNew"], out)
        self.assertTrue(out["paintBeforePoll"], out)
        self.assertFalse(out["deckStarted"], out)
        self.assertFalse(out["deckSettled"], out)
        self.assertFalse(out["pollSettled"], out)
        self.assertTrue(out["pollQuiet"], out)
        self.assertGreaterEqual(out["epoch"], 1, out)
        self.assertFalse(out["studentFetch"], out)

    def test_bank_import_without_deck_paints_before_deck_resolves(self) -> None:
        """When the response has no deck, the panel still paints first."""
        out = _run(
            "bank-without-deck",
            {
                "ok": True,
                "placement": {
                    "item_id": "q-new",
                    "item": {"id": "q-new", "text": "stem"},
                },
            },
        )
        self.assertEqual(out["winner"], "import-done", out)
        self.assertTrue(out["paintBeforeDeck"], out)
        self.assertTrue(out["paintBeforePoll"], out)
        self.assertFalse(out["deckSettled"], out)
        self.assertFalse(out["pollSettled"], out)
        self.assertTrue(out["deckSettledAfter"], out)
        self.assertTrue(out["repainted"], out)

    def test_contest_import_returns_before_poll_resolves(self) -> None:
        """Contest import paints and returns the count while the poll is out."""
        out = _run(
            "contest",
            {
                "ok": True,
                "imported": 2,
                "live_metadata": {"questions": [{"id": "c-1"}], "items": []},
            },
        )
        self.assertEqual(out["winner"], "import-done:2", out)
        self.assertTrue(out["paintBeforePoll"], out)
        self.assertFalse(out["pollSettled"], out)
        self.assertFalse(out["deckStarted"], out)

    def test_relocate_paints_before_poll_resolves(self) -> None:
        """Move/remove paints from the playlist response, then polls quietly."""
        out = _run(
            "relocate",
            {
                "ok": True,
                "live_metadata": {"questions": [{"id": "q-keep"}], "items": []},
                "question_cards": [{"id": "q-keep"}],
            },
        )
        self.assertEqual(out["winner"], "import-done", out)
        self.assertTrue(out["paintBeforePoll"], out)
        self.assertFalse(out["pollSettled"], out)
        self.assertFalse(out["deckStarted"], out)


class ImportPaintSourceTests(unittest.TestCase):
    """The teacher paint path does not reload students, and CI still guards fan-out."""

    def test_import_functions_do_not_await_deck_or_poll(self) -> None:
        """Bank, contest, and relocate paint, then hand follow-up to the background."""
        js = STAFF_JS.read_text(encoding="utf-8")
        for name, end in (
            ("importLiveMcFromBank", "function plainPromptHtml("),
            ("importLiveContentQuestions", "async function refreshLiveDeckAfterContentImportError("),
            ("relocatePlaylistItem", "function playlistPageLabel("),
        ):
            body = js.split(f"async function {name}(")[1].split(end)[0]
            self.assertIn("paintLiveQuestionCards()", body, name)
            self.assertNotIn("await refreshLessonDeckMetadata()", body, name)
            self.assertNotIn("await pollLiveSessionAttendees", body, name)
            self.assertNotIn("/api/student", body, name)
            self.assertNotIn("location.reload", body, name)
        follow = js.split("function settleTeacherDeckFollowUp(")[1].split(
            "async function importLiveMcFromBank("
        )[0]
        self.assertIn("refreshLessonDeckMetadata()", follow)
        self.assertNotIn("await refreshLessonDeckMetadata()", follow)
        self.assertLess(
            follow.index("scheduleQuietTeacherPoll()"),
            follow.index("refreshLessonDeckMetadata()"),
        )
        self.assertIn("if (!applied) return;", follow)
        quiet = js.split("function scheduleQuietTeacherPoll(")[1].split(
            "function settleTeacherDeckFollowUp("
        )[0]
        self.assertIn("quiet: true", quiet)
        self.assertNotIn("/api/student", quiet)
        poll = js.split("async function pollLiveSessionAttendees(")[1].split(
            "function startStaffNewsWire("
        )[0]
        self.assertLess(
            poll.index("if (staffPollShowsPendingStrip(opts)) armStaffPendingFeel();"),
            poll.index("await api("),
        )
        self.assertIn("staffPollMayApplyDeck(deckEpochAtFetch, teacherDeckEpoch)", poll)
        self.assertIn("if (staffPollCoalesceArmsStrip(opts)) armStaffPendingFeel();", poll)
        self.assertGreaterEqual(poll.count("staffPollQueueIsQuiet(opts, sessionPollQueued)"), 2)
        deck = js.split("async function refreshLessonDeckMetadata(")[1].split(
            "function applyLiveMcImportPayload("
        )[0]
        self.assertLess(
            deck.index("const epochAtFetch = teacherDeckEpoch"),
            deck.index("await api("),
        )
        self.assertIn("if (!staffPollMayApplyDeck(epochAtFetch, teacherDeckEpoch)) return false;", deck)

    def test_fanout_guard_and_load_budget_stay_in_ci(self) -> None:
        """MCK-283's regression and the 30-seat budget still run on this diff."""
        text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: fan-out guard", text)
        self.assertIn("test_fanout_guard.py", text)
        self.assertIn("name: 30-seat load budget", text)
        self.assertIn("--sqlite-budget", text)
        self.assertIn("--students 30", text)


OUTAGE_FUNCTIONS = (
    "staffPollShowsPendingStrip",
    "staffPollQueueIsQuiet",
    "staffPollCoalesceArmsStrip",
    "shouldShowStaffReconnectPending",
    "armStaffPendingFeel",
    "pollLiveSessionAttendees",
)

OUTAGE_HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const timers = [];
let now = 0;
let nextId = 1;
function fakeSetTimeout(fn, ms) {
  const id = nextId++;
  timers.push({ id, fn, at: now + Number(ms || 0), fired: false, cleared: false });
  return id;
}
function fakeClearTimeout(id) {
  const row = timers.find((item) => item.id === id);
  if (row) row.cleared = true;
}
function advance(ms) {
  now += ms;
  for (let guard = 0; guard < 30; guard += 1) {
    const due = timers
      .filter((item) => !item.fired && !item.cleared && item.at <= now)
      .sort((a, b) => a.at - b.at || a.id - b.id);
    if (!due.length) return;
    const next = due[0];
    next.fired = true;
    next.fn();
  }
}
const paints = [];
const ctx = {
  AbortController,
  liveSessionId: 9,
  readLiveSessionId: () => 9,
  teacherState: { state_seq: 1 },
  lastGoodStateSeq: 1,
  teacherDeckEpoch: 0,
  staffStateNeedsFull: false,
  sessionPollInFlight: false,
  sessionPollQueued: null,
  staffNewsFetch: false,
  staffPollAwaitingNetwork: false,
  sessionPollAbort: null,
  staffPollAbortReason: "",
  reconnectPendingTimer: 0,
  staffOutageStartedAt: 0,
  RECONNECT_PENDING_MS: 800,
  STAFF_STATE_TIMEOUT_MS: 15000,
  CATCHING_UP_COPY: "Catching up…",
  reconnectCopy: (mode) => (mode === "pending" ? "Reconnecting…" : ""),
  paintStaffReconnect: (mode, message) => { paints.push({ mode, message, at: now }); },
  armStaffStuckTimer: () => {},
  window: { setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout },
  api: () => new Promise(() => {}),
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
const out = { scenario: input.scenario };
const pending = () => paints.filter((row) => row.message === "Reconnecting…");

(async () => {
  if (input.scenario === "interval-arms") {
    void ctx.pollLiveSessionAttendees({ full: true, force: true, quiet: true });
    await new Promise((resolve) => setImmediate(resolve));
    advance(800);
    out.quietAlone = pending().length;
    ctx.pollLiveSessionAttendees();
    advance(799);
    out.before800 = pending().length;
    advance(1);
    out.at800 = pending().length;
    out.at = pending().length ? pending()[0].at : null;
  } else if (input.scenario === "queue-loud-wins") {
    void ctx.pollLiveSessionAttendees({ quiet: true });
    await new Promise((resolve) => setImmediate(resolve));
    ctx.pollLiveSessionAttendees({ full: true, force: true });
    ctx.pollLiveSessionAttendees({ full: true, force: true, quiet: true });
    out.quiet = Boolean(ctx.sessionPollQueued && ctx.sessionPollQueued.quiet);
    out.full = Boolean(ctx.sessionPollQueued && ctx.sessionPollQueued.full);
  } else if (input.scenario === "queue-news-wins") {
    void ctx.pollLiveSessionAttendees({ quiet: true });
    await new Promise((resolve) => setImmediate(resolve));
    ctx.pollLiveSessionAttendees({ full: true, force: true, quiet: true });
    out.quietBeforeNews = Boolean(ctx.sessionPollQueued && ctx.sessionPollQueued.quiet);
    ctx.pollLiveSessionAttendees({ fromNews: true });
    out.quiet = Boolean(ctx.sessionPollQueued && ctx.sessionPollQueued.quiet);
    out.fromNews = Boolean(ctx.sessionPollQueued && ctx.sessionPollQueued.fromNews);
  }
  console.log(JSON.stringify(out));
})().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


def _run_outage(scenario: str) -> dict:
    """Run one in-flight poll scenario with a fake 800ms clock.

    Args:
        scenario: ``interval-arms``, ``queue-loud-wins``, or ``queue-news-wins``.
    """
    js = STAFF_JS.read_text(encoding="utf-8")
    src = "\n".join(_function_source(js, name) for name in OUTAGE_FUNCTIONS)
    result = subprocess.run(
        [NODE or "node", "-e", OUTAGE_HARNESS],
        input=json.dumps({"scenario": scenario, "src": src}),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is required")
class QuietPollOutageTests(unittest.TestCase):
    """A hung quiet poll must not hide a later interval or news tick."""

    def test_interval_shows_the_strip_within_800ms(self) -> None:
        """The quiet poll hangs. The interval tick arms Reconnecting at ~800ms."""
        out = _run_outage("interval-arms")
        self.assertEqual(out["quietAlone"], 0, out)
        self.assertEqual(out["before800"], 0, out)
        self.assertEqual(out["at800"], 1, out)

    def test_quiet_request_cannot_silence_a_queued_loud_poll(self) -> None:
        """The queue stays loud when any waiting request is loud."""
        out = _run_outage("queue-loud-wins")
        self.assertFalse(out["quiet"], out)
        self.assertTrue(out["full"], out)
        news = _run_outage("queue-news-wins")
        self.assertTrue(news["quietBeforeNews"], news)
        self.assertFalse(news["quiet"], news)
        self.assertTrue(news["fromNews"], news)


if __name__ == "__main__":
    unittest.main()
