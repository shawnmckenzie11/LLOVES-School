"""MCK-112 follow-up: Group Q waits for the confirm, Media row label, re-mint.

Three leftovers from the #209 gate (smoke-pr209-419b996, box re-gate
ac2fcf0):

1. MED: with teams hidden, ticking Group Q in the artifact iframe wrote
   ``group_q=true`` onto the live artifact *before* the teacher answered
   "Show teams and publish", so students sat on "Individual + match
   teammates" while the confirm was open.
2. LOW: a live switch of artifact Media to Group left the Media lifecycle
   row on ``publish_mode = individual`` (the server refused
   ``group_shared`` on media rows and the client never wrote it).
3. LOW: a re-mint replaced the artifact and reset "Wait until teammates
   match" to off while Media stayed on Group.

The staff_ap.js cases run the real functions in a node ``vm`` with the
network and DOM stubbed, so they check what would reach students.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import test_artifact as artifact_tests
from artifact import TRANSFORMATIONS_ARTIFACT_ID

LMS_DIR = Path(__file__).resolve().parent
STAFF_JS = LMS_DIR / "static" / "staff_ap.js"
GROUP_SETUP_JS = LMS_DIR / "static" / "group_setup.js"

# Real staff_ap.js functions the harness runs when they exist.
STAFF_FUNCTIONS = (
    "currentStudentView",
    "bindActiveMediaControls",
    "patchArtifactTeacherFlags",
    "clearArtifactGroupQ",
    "applyLiveArtifactMediaPick",
    "persistMediaRowMode",
    "syncMediaPickFromGroupQ",
    "teacherFrameArtifact",
    "syncTeacherMediaFrame",
    "requestArtifactGroupQ",
    "applyHeldArtifactGroupQ",
    "mintArtifactFromMedia",
)


def _function_source(js: str, name: str) -> str | None:
    """Return a top-level ``[async ]function name(...) {...}`` or None.

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
        return None
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
const { pathToFileURL } = require("url");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const tick = async (n = 12) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setImmediate(r)); };

(async () => {
  const shared = await import(pathToFileURL(input.groupSetup).href);
  const log = [];
  const listeners = {};
  const frame = [];
  const mediaRow = { id: 11, kind: "media", status: "active", publish_mode: "individual", item: { item_type: "media" } };
  const ctx = {
    console,
    Promise,
    JSON,
    Map,
    Number,
    String,
    Boolean,
    Object,
    Array,
    Error,
    setImmediate,
    ...shared,
    heldArtifactGroupQ: null,
    artifactGroupQPending: false,
    artifactMintInFlight: false,
    mediaPushTimer: 0,
    liveSessionId: 5,
    lastLiveItems: [mediaRow],
    groupModeIntent: new Map(),
    groupPublishConfirms: new Map(),
    teacherState: {
      groups_configured: true,
      run_as_group: input.runAsGroup,
      teams_mode: input.runAsGroup ? "teams" : "solo",
      stage: "play",
      student_view: { media: input.mediaView, questions: "student" },
    },
    lastActiveMedia: {
      url: "/static/live-media/m1c2-transforms.html",
      artifact: {
        artifact_id: "mcf3m-m1-c2-transformations",
        snapshot: { a: 2, h: 1, k: -1 },
        target_mode: "graph",
        hot_cold_visible: false,
        group_q: input.storedGroupQ,
        accuracy_margin: 0.1,
      },
    },
    window: {
      location: { origin: "http://lms.test" },
      addEventListener: (type, fn) => { listeners[type] = fn; },
      setTimeout: () => 0,
      clearTimeout: () => {},
    },
    readLiveSessionId: () => 5,
    $: (id) => (id === "ap-media-preview" ? { contentWindow: { postMessage: (m) => frame.push(m) } } : null),
    staffLiveMediaState: (media) => ({
      type: "live-media-state",
      artifact: typeof ctx.teacherFrameArtifact === "function" ? ctx.teacherFrameArtifact(media.artifact) : media.artifact,
    }),
    surfaceStatus: () => input.mediaStatus,
    surfacePublishSelection: () => (ctx.teacherState.student_view.media === "team" ? "team" : "student"),
    lifecycleItemForSurface: (surface) => (surface === "media" ? mediaRow : null),
    adoptLiveItem: (item) => { if (item && Number(item.id) === mediaRow.id) Object.assign(mediaRow, item); },
    adoptTeacherState: () => {},
    shouldApplyLiveSnapshot: () => false,
    postActiveMedia: async (body) => {
      log.push({ kind: "media", group_q: body.artifact ? Boolean(body.artifact.group_q) : null });
      if (body.artifact) ctx.lastActiveMedia = { ...ctx.lastActiveMedia, artifact: { ...body.artifact } };
      return ctx.lastActiveMedia;
    },
    patchTeacherState: async (patch) => {
      log.push({ kind: "teacher", patch: JSON.parse(JSON.stringify(patch)) });
      Object.assign(ctx.teacherState, patch);
    },
    api: async (url, opts) => {
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      log.push({ kind: "api", url, method: (opts && opts.method) || "GET", body });
      if (url.endsWith("/settings")) return { item: { ...mediaRow, publish_mode: body.publish_mode } };
      return { active_media: ctx.lastActiveMedia };
    },
    confirmGroupPublish: (key) => new Promise((resolve) => {
      log.push({ kind: "confirm-open", key });
      ctx.groupPublishConfirms.set(key, (ok) => { ctx.groupPublishConfirms.delete(key); resolve(Boolean(ok)); });
    }),
    settleGroupPublishConfirm: (key, ok) => { const r = ctx.groupPublishConfirms.get(key); if (r) r(ok); },
    paintGroupSetupSurfaces: () => {},
    paintActiveMediaStatus: () => {},
    paintLiveQuestionCards: () => {},
    paintQuestionArtifact: () => {},
    applyLiveMcImportPayload: () => {},
    absorbSaveToCardSnapshot: (rows) => rows,
    questionCardsFromMetadata: (rows) => rows,
    pollLiveSessionAttendees: async () => {},
    showStaffMintToast: () => {},
    armTeacherMutationFeel: () => 1,
    settleTeacherMutationFeel: () => {},
    ensureLiveSessionMinted: async () => {},
    currentLivePageNumber: () => 3,
    saveSurfaceModeIntent: () => {},
    showError: (_sel, err) => log.push({ kind: "error", message: String(err && err.message) }),
  };
  vm.createContext(ctx);
  vm.runInContext(input.src, ctx);
  const studentGroupQWrites = () => log.filter((e) => e.kind === "media" && e.group_q === true).length;
  const out = { scenario: input.scenario };

  if (input.scenario === "tick-cancel" || input.scenario === "tick-confirm") {
    ctx.bindActiveMediaControls();
    listeners.message({
      origin: "http://lms.test",
      data: {
        source: "lloves-m1c2-transforms",
        type: "artifact-teacher-flags",
        hot_cold_visible: false,
        group_q: true,
        accuracy_margin: 0.1,
      },
    });
    await tick();
    out.confirmOpen = ctx.groupPublishConfirms.has("s:media");
    out.groupQWritesDuringConfirm = studentGroupQWrites();
    out.teacherWritesDuringConfirm = log.filter((e) => e.kind === "teacher").length;
    out.storedDuringConfirm = Boolean(ctx.lastActiveMedia.artifact.group_q);
    ctx.settleGroupPublishConfirm("s:media", input.scenario === "tick-confirm");
    await tick();
    out.groupQAfter = Boolean(ctx.lastActiveMedia.artifact.group_q);
    out.runAsGroupAfter = Boolean(ctx.teacherState.run_as_group);
    out.mediaViewAfter = ctx.teacherState.student_view.media;
    out.frameGroupQ = frame.length ? Boolean(frame[frame.length - 1].artifact && frame[frame.length - 1].artifact.group_q) : null;
    out.mediaRowMode = mediaRow.publish_mode;
    out.order = log
      .filter((e) => e.kind !== "confirm-open")
      .map((e) => {
        if (e.kind === "teacher") return "run_as_group" in e.patch ? "run_as_group" : `view:${e.patch.student_view && e.patch.student_view.media}`;
        if (e.kind === "api") return `row:${e.body.publish_mode}`;
        return `${e.kind}:${e.group_q}`;
      });
  } else if (input.scenario === "live-switch") {
    const okGroup = await ctx.applyLiveArtifactMediaPick("group");
    out.okGroup = okGroup;
    out.rowAfterGroup = mediaRow.publish_mode;
    out.viewAfterGroup = ctx.teacherState.student_view.media;
    await ctx.applyLiveArtifactMediaPick("individual");
    out.rowAfterIndividual = mediaRow.publish_mode;
    out.viewAfterIndividual = ctx.teacherState.student_view.media;
    out.settings = log.filter((e) => e.kind === "api" && e.url.endsWith("/items/11/settings")).map((e) => e.body.publish_mode);
  } else if (input.scenario === "remint") {
    await ctx.mintArtifactFromMedia({
      artifact_id: "mcf3m-m1-c2-transformations",
      snapshot: { a: -1, h: 2, k: 0 },
      target_mode: "equation",
      hot_cold_visible: false,
      group_q: input.frameGroupQ,
      accuracy_margin: 0.1,
    });
    const mint = log.find((e) => e.kind === "api" && e.url.endsWith("/artifacts"));
    out.mintGroupQ = mint ? mint.body.group_q : null;
  }
  out.errors = log.filter((e) => e.kind === "error").map((e) => e.message);
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
"""


def _run_staff(scenario: str, **opts: object) -> dict:
    """Run one harness scenario against the real staff_ap.js functions.

    Args:
        scenario: ``tick-cancel``, ``tick-confirm``, ``live-switch`` or ``remint``.
        **opts: ``runAsGroup``, ``mediaView``, ``mediaStatus``,
            ``storedGroupQ``, ``frameGroupQ``.
    """
    js = STAFF_JS.read_text(encoding="utf-8")
    src = "\n".join(
        part for part in (_function_source(js, name) for name in STAFF_FUNCTIONS) if part
    )
    payload = {
        "scenario": scenario,
        "src": src,
        "groupSetup": str(GROUP_SETUP_JS),
        "runAsGroup": False,
        "mediaView": "student",
        "mediaStatus": "active",
        "storedGroupQ": False,
        "frameGroupQ": False,
        **opts,
    }
    result = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class GroupQWaitsForConfirmTests(unittest.TestCase):
    """Fix 1 (MED): nothing students see changes until the confirm is answered."""

    def test_iframe_tick_with_teams_hidden_writes_nothing_until_cancel(self) -> None:
        """Tick, then Cancel (Esc settles the same confirm): students unchanged."""
        out = _run_staff("tick-cancel")
        self.assertTrue(out["confirmOpen"], out)
        self.assertEqual(out["groupQWritesDuringConfirm"], 0, out)
        self.assertFalse(out["storedDuringConfirm"], out)
        self.assertEqual(out["teacherWritesDuringConfirm"], 0, out)
        self.assertFalse(out["groupQAfter"], out)
        self.assertFalse(out["runAsGroupAfter"], out)
        self.assertEqual(out["mediaViewAfter"], "student", out)
        self.assertEqual(out["order"], [], out)
        # The iframe mirror box goes back to what students have.
        self.assertIs(out["frameGroupQ"], False, out)
        self.assertEqual(out["errors"], [], out)

    def test_iframe_tick_confirm_goes_group_before_group_q(self) -> None:
        """Confirm: show teams, Media to Group, row label, then Group Q."""
        out = _run_staff("tick-confirm")
        self.assertTrue(out["confirmOpen"], out)
        self.assertEqual(out["groupQWritesDuringConfirm"], 0, out)
        self.assertTrue(out["groupQAfter"], out)
        self.assertTrue(out["runAsGroupAfter"], out)
        self.assertEqual(out["mediaViewAfter"], "team", out)
        self.assertEqual(
            out["order"],
            ["run_as_group", "view:team", "row:group_shared", "media:true"],
            out,
        )
        self.assertEqual(out["errors"], [], out)

    def test_iframe_tick_with_teams_shown_never_runs_individual_with_group_q(self) -> None:
        """Teams already shown: no confirm, and Group goes live before Group Q."""
        out = _run_staff("tick-confirm", runAsGroup=True)
        self.assertFalse(out["confirmOpen"], out)
        self.assertEqual(
            out["order"], ["view:team", "row:group_shared", "media:true"], out
        )

    def test_unpublished_media_holds_the_tick_until_publish(self) -> None:
        """Media not published yet: the tick is held, nothing is written."""
        out = _run_staff("tick-cancel", mediaStatus="inactive")
        self.assertFalse(out["confirmOpen"], out)
        self.assertEqual(out["groupQWritesDuringConfirm"], 0, out)
        self.assertEqual(out["order"], [], out)

    def test_group_q_plan_is_pure_and_matches_the_rules(self) -> None:
        """Node harness: group_setup.test.mjs covers artifactGroupQPlan."""
        shared = GROUP_SETUP_JS.read_text(encoding="utf-8")
        self.assertTrue("export function artifactGroupQPlan(" in shared)
        js = STAFF_JS.read_text(encoding="utf-8")
        self.assertNotIn(".then(() => syncMediaPickFromGroupQ(", js)
        handler = _function_source(js, "bindActiveMediaControls") or ""
        self.assertIn("requestArtifactGroupQ(data)", handler)
        self.assertNotIn("patchArtifactTeacherFlags(data)", handler)


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class MediaRowLabelClientTests(unittest.TestCase):
    """Fix 2 (LOW, client): a live switch records the mode on the Media row."""

    def test_live_switch_writes_group_shared_then_individual(self) -> None:
        out = _run_staff("live-switch", runAsGroup=True)
        self.assertTrue(out["okGroup"], out)
        self.assertEqual(out["viewAfterGroup"], "team", out)
        self.assertEqual(out["rowAfterGroup"], "group_shared", out)
        self.assertEqual(out["viewAfterIndividual"], "student", out)
        self.assertEqual(out["rowAfterIndividual"], "individual", out)
        self.assertEqual(out["settings"], ["group_shared", "individual"], out)

    def test_publish_surface_records_group_on_media(self) -> None:
        js = STAFF_JS.read_text(encoding="utf-8")
        publish = js.split("async function publishSurface(")[1].split(
            "async function closeSurface("
        )[0]
        self.assertTrue(
            '(surface === "canvas" || surface === "media") && selected === "team"'
            in publish,
            "publishSurface must publish artifact Media Group as group_shared",
        )


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class RemintKeepsGroupQClientTests(unittest.TestCase):
    """Fix 3 (LOW, client): a re-mint sends the teacher's Group Q."""

    def test_remint_keeps_group_q_over_a_stale_iframe_box(self) -> None:
        out = _run_staff(
            "remint", runAsGroup=True, mediaView="team", storedGroupQ=True, frameGroupQ=False
        )
        self.assertIs(out["mintGroupQ"], True, out)

    def test_remint_never_sends_group_q_while_individual(self) -> None:
        out = _run_staff(
            "remint", mediaView="student", storedGroupQ=False, frameGroupQ=True
        )
        self.assertIs(out["mintGroupQ"], False, out)


class MediaRowLabelServerTests(unittest.TestCase):
    """Fix 2 (LOW, server): Media rows may record ``group_shared``."""

    setUp = artifact_tests.ArtifactGroupQTests.setUp
    tearDown = artifact_tests.ArtifactGroupQTests.tearDown

    def _media_row(self) -> dict:
        """Media lifecycle row on M1 C3 play."""
        self.school.set_live_session_teacher_state(
            self.live_session_id, live_module="M1", live_slot="C3", stage="play"
        )
        self.school.ensure_live_session_items(self.live_session_id)
        rows = [
            row
            for row in self.school.list_live_session_items(self.live_session_id)
            if str(row.get("kind") or "") == "media"
        ]
        self.assertTrue(rows)
        return rows[0]

    def test_publish_media_as_group_records_group_shared(self) -> None:
        row = self._media_row()
        res = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/items/{row['id']}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()["item"]["publish_mode"], "group_shared")
        self.assertEqual(res.get_json()["item"]["response_mode"], "individual")

    def test_live_switch_label_follows_the_mode(self) -> None:
        row = self._media_row()
        sid = self.live_session_id
        pub = self.staff.post(
            f"/api/live-sessions/{sid}/items/{row['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(pub.status_code, 200, pub.get_json())
        to_group = self.staff.patch(
            f"/api/live-sessions/{sid}/items/{row['id']}/settings",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(to_group.status_code, 200, to_group.get_json())
        item = to_group.get_json()["item"]
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["publish_mode"], "group_shared")
        stored = self.school.get_live_session_item(sid, int(row["id"]))
        self.assertEqual(stored["publish_mode"], "group_shared")
        back = self.staff.patch(
            f"/api/live-sessions/{sid}/items/{row['id']}/settings",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(back.status_code, 200, back.get_json())
        self.assertEqual(back.get_json()["item"]["publish_mode"], "individual")

    def test_media_still_rejects_question_only_modes_at_publish(self) -> None:
        row = self._media_row()
        res = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/items/{row['id']}/publish",
            json={"publish_mode": "group_consensus"},
        )
        self.assertNotEqual(res.status_code, 200, res.get_json())


class RemintKeepsGroupQServerTests(unittest.TestCase):
    """Fix 3 (LOW, server): a re-mint keeps "Wait until teammates match"."""

    setUp = artifact_tests.ArtifactGroupQTests.setUp
    tearDown = artifact_tests.ArtifactGroupQTests.tearDown

    def _mint(self, **extra: object) -> dict:
        """Mint on M1 C3 with optional body fields."""
        res = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "h": 1, "k": -1},
                "target_mode": "graph",
                **extra,
            },
        )
        self.assertEqual(res.status_code, 200, res.get_json())
        return res.get_json()

    def _group_live_with_group_q(self) -> None:
        """Mint, show teams, Media to Group, then tick Group Q."""
        self.school.set_live_session_teacher_state(
            self.live_session_id, live_module="M1", live_slot="C3", stage="play"
        )
        first = self._mint()
        self.school.set_live_session_teacher_state(self.live_session_id, run_as_group=True)
        view = dict(
            self.school.live_session_teacher_state_payload(self.live_session_id)["student_view"]
        )
        view["media"] = "team"
        self.school.set_live_session_teacher_state(self.live_session_id, student_view=view)
        patched = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"artifact": {**first["active_media"]["artifact"], "group_q": True}},
        )
        self.assertEqual(patched.status_code, 200, patched.get_json())

    def test_remint_without_group_q_keeps_it_on_while_group(self) -> None:
        self._group_live_with_group_q()
        second = self._mint(snapshot={"a": -1, "h": 2, "k": 0}, target_mode="equation")
        self.assertTrue(second["active_media"]["artifact"]["group_q"], second["active_media"])
        prompt = self.school.get_active_live_prompt(self.live_session_id)
        self.assertTrue(prompt["payload"]["group_q"])
        teacher = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertEqual(teacher["student_view"]["media"], "team")

    def test_explicit_group_q_false_on_remint_still_wins(self) -> None:
        self._group_live_with_group_q()
        second = self._mint(group_q=False)
        self.assertFalse(second["active_media"]["artifact"]["group_q"])

    def test_remint_while_individual_does_not_inherit_group_q(self) -> None:
        self.school.set_live_session_teacher_state(
            self.live_session_id, live_module="M1", live_slot="C3", stage="play"
        )
        self._mint(group_q=True)
        teacher = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertNotEqual(teacher["student_view"]["media"], "team")
        second = self._mint()
        self.assertFalse(second["active_media"]["artifact"]["group_q"])


if __name__ == "__main__":
    unittest.main()
