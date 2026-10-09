#!/usr/bin/env python3
"""MCK-183 follow-ups (with MCK-193 and MCK-182 LOWs).

1. Plain courses (non-math, no pack): roster validation says first names.
2. Ops LOW-1: Sentry masks /student/s/<x> and /invite/<token> anywhere,
   not only before ``?`` or ``#`` (server and browser scrubbers).
3. Ops LOW-2: a cross-site POST to /invite/<x>/switch never signs out, and
   only a valid open invite clears the session.
4. MCK-193: C2+ on a plain course shows the calm blank-deck note, not
   "No deck saved for M1 C1."
5. MCK-182 LOW: every spelling of /static/whats-new/releases.json is 404.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)

import sentry_wire  # noqa: E402
import staff_invites  # noqa: E402
from app import create_app, plain_name_wording  # noqa: E402
from test_sentry_student_steps_mck183 import FRAMES_SCRIPT  # noqa: E402

NODE = shutil.which("node")


class _AppCase(unittest.TestCase):
    """Fresh app with an active semester."""

    def setUp(self) -> None:
        """Temp sqlite app."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _sign_in(self, email: str) -> None:
        """Staff sign-in through the mock Google callback and email code."""
        self.client.get("/auth/google?portal=staff")
        rv = self.client.get(f"/auth/google/callback?email={email}&name=T")
        if "/verify-email" in rv.headers.get("Location", ""):
            code = self.school.get_user_by_email(email)["verification_code"]
            self.client.post("/verify-email", data={"code": code})


class PlainNameWordingTests(_AppCase):
    """1. First-names wording on plain courses; math keeps Codename."""

    def test_wording_function(self) -> None:
        """Server helper rewrites every Codename message."""
        cases = {
            "Codenames cannot contain commas": "First names cannot contain commas",
            "Codename is required": "First name is required",
            "Codenames must be 2–32 characters": "First names must be 2–32 characters",
            "Duplicate Codename: Sam": "Duplicate first name: Sam",
            "Add at least one Codename": "Add at least one first name",
            "That Codename is already on this roster": "That first name is already on this roster",
        }
        for raw, want in cases.items():
            self.assertEqual(plain_name_wording(raw), want)

    @unittest.skipUnless(NODE, "node is required")
    def test_browser_wording_matches(self) -> None:
        """staff_home.js uses the same wording through name_wording.js."""
        script = (
            'import { plainNameWording as f } from "./static/name_wording.js";\n'
            'console.log(JSON.stringify(["Codenames cannot contain commas.", '
            '"Codenames must be 2–32 characters.", "Duplicate Codename: Sam", '
            '"Add at least one Codename."].map(f)));'
        )
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            [
                "First names cannot contain commas.",
                "First names must be 2–32 characters.",
                "Duplicate first name: Sam",
                "Add at least one first name.",
            ],
        )
        src = (LMS_DIR / "static" / "staff_home.js").read_text(encoding="utf-8")
        self.assertIn('import { plainNameWording } from "/static/name_wording.js";', src)
        self.assertIn("plainNames ? plainNameWording(message) : message", src)

    def _populate(self, code: str, names: list[str]):
        """Populate Class for a fresh offering of ``code``."""
        teacher = self.school.get_user_by_email("t@gmail.com") or self.school.register_staff("t@gmail.com")
        offering = self.school.assign_course(teacher_user_id=int(teacher["id"]), ontario_code=code)
        self._sign_in("t@gmail.com")
        return self.client.post(
            "/api/staff/classes",
            json={"offering_id": offering["id"], "days": "M/W/F", "time": "2:00pm", "codenames": names},
        )

    def test_plain_course_server_messages(self) -> None:
        """SBI4U says first names; MCF3M keeps Codename."""
        plain = self._populate("SBI4U", ["Sam, J"])
        self.assertEqual(plain.status_code, 400)
        self.assertEqual(plain.get_json()["error"], "First names cannot contain commas")
        math = self._populate("MCF3M", ["Sam, J"])
        self.assertEqual(math.get_json()["error"], "Codenames cannot contain commas")

    def test_plain_course_roster_edit(self) -> None:
        """Save roster (PUT) on a plain class also says first names."""
        created = self._populate("SBI4U", ["Ana"])
        class_id = created.get_json()["class"]["id"]
        rv = self.client.put(f"/api/staff/classes/{class_id}/roster", json={"codenames": ["A"]})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "First names must be 2–32 characters")
        dup = self.client.put(f"/api/staff/classes/{class_id}/roster", json={"codenames": ["Sam", "sam"]})
        self.assertEqual(dup.get_json()["error"], "Duplicate first name: sam")


class AddStudentValidationTests(PlainNameWordingTests):
    """#272 gate LOW-1: POST /api/classes/<id>/students validation is a 400."""

    def test_add_student_validation_is_400_plain_and_math(self) -> None:
        """Plain says first names, math says Codename; never a 500."""
        cases = (
            ("SBI4U", {"codename": "Ann, Bo"}, "First names cannot contain commas"),
            ("SBI4U", {"codename": "X"}, "First names must be 2–32 characters"),
            ("MCF3M", {"codename": "Ann, Bo"}, "Codenames cannot contain commas"),
            ("MCF3M", {"codename": "X"}, "Codenames must be 2–32 characters"),
        )
        class_ids: dict[str, int] = {}
        for code, body, want in cases:
            if code not in class_ids:
                created = self._populate(code, ["Ava", "Ben"])
                self.assertEqual(created.status_code, 200, created.get_json())
                class_ids[code] = int(created.get_json()["class"]["id"])
            rv = self.client.post(f"/api/classes/{class_ids[code]}/students", json=body)
            self.assertEqual(rv.status_code, 400, (code, body, rv.get_data(as_text=True)))
            self.assertEqual(rv.get_json()["error"], want)
        ok = self.client.post(f"/api/classes/{class_ids['SBI4U']}/students", json={"codename": "Cyd"})
        self.assertEqual(ok.status_code, 200, ok.get_json())


TOKEN_SCRIPT_TAIL = r"""
const SEAT = "SEATtok987secret";
const INV = "INVITEtok555secret";
const ev = {
  message: `qa msg https://alc.mckenzian.com/student/s/${SEAT} and /invite/${INV}`,
  exception: { values: [{ type: "Error", value: `qa report at /student/s/${SEAT}`, stacktrace: { frames: [
    { filename: `https://alc.mckenzian.com/student/s/${SEAT}`, abs_path: `https://alc.mckenzian.com/invite/${INV}` }] } }] },
  request: { url: `https://alc.mckenzian.com/student/s/${SEAT}` },
  breadcrumbs: [
    { category: "navigation", data: { from: `/invite/${INV}`, to: `/student/s/${SEAT}` } },
    { category: "sentry.event", message: `Error on /student/s/${SEAT}` },
    { category: "fetch", data: { url: `/invite/${INV}/switch`, method: "POST" } },
    { category: "custom", message: "x", data: { note: `went to /student/s/${SEAT}` } },
  ],
  tags: { qa: `/student/s/${SEAT}` },
  contexts: { qa: { where: `/invite/${INV}` } },
};
const res = sandbox.__opts.beforeSend(ev);
const flat = JSON.stringify(res);
if (flat.includes(SEAT) || flat.includes(INV)) { console.error("leak " + flat); process.exit(1); }
if (!flat.includes("/student/s/[token]") || !flat.includes("/invite/[token]")) { console.error("no mask " + flat); process.exit(1); }
console.log("ok2");
"""


class SentryPathTokenTests(unittest.TestCase):
    """2. Seat and invite paths are masked without a query after them."""

    def test_server_masks_bare_paths_everywhere(self) -> None:
        """Message, logentry, exception value, frames, breadcrumbs, request, transaction."""
        seat, inv = "SEATtok987secret", "INVITEtok555secret"
        event = {
            "message": f"boom /student/s/{seat} and /invite/{inv}/switch",
            "logentry": {"message": "at %s", "params": [f"/invite/{inv}"]},
            "exception": {"values": [{"value": f"GET /student/s/{seat} failed",
                                      "stacktrace": {"frames": [{"filename": f"/student/s/{seat}"}]}}]},
            "breadcrumbs": {"values": [{"message": f"nav /invite/{inv}",
                                        "data": {"url": f"https://alc.mckenzian.com/student/s/{seat}"}}]},
            "request": {"url": f"https://alc.mckenzian.com/student/s/{seat}"},
            "transaction": f"/invite/{inv}",
        }
        out = sentry_wire.before_send(event, {})
        flat = json.dumps(out)
        self.assertNotIn(seat, flat)
        self.assertNotIn(inv, flat)
        self.assertIn("/student/s/[token]", flat)
        self.assertIn("/invite/[token]/switch", flat)
        self.assertEqual(sentry_wire.mask_path_tokens("/api/staff/invites/5"), "/api/staff/invites/5")

    @unittest.skipUnless(NODE, "node is required")
    def test_browser_masks_bare_paths_everywhere(self) -> None:
        """sentry-live.js beforeSend masks the same paths with no query."""
        script = FRAMES_SCRIPT.split("const TOK =")[0] + TOKEN_SCRIPT_TAIL
        proc = subprocess.run(
            [NODE, "-e", script, str(LMS_DIR / "static" / "sentry-live.js")],
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("ok2", proc.stdout)


class InviteSwitchCsrfTests(_AppCase):
    """3. /invite/<x>/switch: no cross-site sign-out; valid invite only."""

    def setUp(self) -> None:
        """Signed-in teacher plus one open invite for someone else."""
        super().setUp()
        _invite, self.token = staff_invites.create_or_refresh_invite(
            self.school, email="rae.teacher@gmail.com", first_name="Rae",
            preset=("SBI4U", "M/W/F", "2:00pm"),
        )
        self.school.register_staff("other@gmail.com")
        self._sign_in("other@gmail.com")

    def _signed_in(self) -> bool:
        """Is the test client still signed in?"""
        with self.client.session_transaction() as sess:
            return bool(sess.get("logged_in"))

    def test_cross_site_fetch_metadata_is_refused(self) -> None:
        """The Ops repro: an auto-submitted form from another site."""
        rv = self.client.post(
            f"/invite/{self.token}/switch",
            headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
        )
        self.assertEqual(rv.status_code, 403)
        self.assertTrue(self._signed_in())

    def test_foreign_origin_or_referer_is_refused(self) -> None:
        """No fetch metadata, but the Origin (or Referer) is another host."""
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Referer": "https://evil.example/x"}):
            rv = self.client.post(f"/invite/{self.token}/switch", headers=headers)
            self.assertEqual(rv.status_code, 403, headers)
            self.assertTrue(self._signed_in(), headers)

    def test_same_origin_with_origin_null_passes(self) -> None:
        """#272 gate MED-1: real Chrome on invite.html (no-referrer) sends Origin: null."""
        rv = self.client.post(
            f"/invite/{self.token}/switch",
            headers={"Sec-Fetch-Site": "same-origin", "Origin": "null"},
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith(f"/invite/{self.token}"))
        self.assertFalse(self._signed_in())

    def test_cross_site_or_same_site_with_origin_null_fails(self) -> None:
        """Fetch metadata decides: cross-site and same-site are refused."""
        for site in ("cross-site", "same-site"):
            rv = self.client.post(
                f"/invite/{self.token}/switch",
                headers={"Sec-Fetch-Site": site, "Origin": "null"},
            )
            self.assertEqual(rv.status_code, 403, site)
            self.assertTrue(self._signed_in(), site)

    def test_unknown_invite_does_not_sign_out(self) -> None:
        """/invite/anything/switch from our own page leaves the session alone."""
        rv = self.client.post(
            "/invite/anything/switch",
            headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(self._signed_in())

    def test_valid_invite_same_origin_signs_out(self) -> None:
        """The real button still works."""
        rv = self.client.post(
            f"/invite/{self.token}/switch",
            headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith(f"/invite/{self.token}"))
        self.assertFalse(self._signed_in())


@unittest.skipUnless(NODE, "node is required")
class PlainCourseLaterChallengeNoteTests(unittest.TestCase):
    """4. Plain course on C2+: calm blank-deck note, blank confirm."""

    def test_c2_note_and_confirm(self) -> None:
        """C2 with no deck and no M1 C1 deck never shows 'No deck saved'."""
        script = r"""
import { deckSeedConfirmMode, deckSeedDefaultMode, deckSeedHelpText } from "./static/deck_seed_help.js";
const state = { previousAvailable: false, previousLabel: "M1 C1", previousSlot: "C1", previousModule: "M1", courseDeckCount: 0, currentAvailable: false };
const mode = deckSeedDefaultMode({ mode: "current", ...state });
const catalog = { module: "M1", slot: "C2", current: { available: false },
  previous: { available: false, module: "M1", slot: "C1", label: "M1 C1", message: "No deck saved for M1 C1." }, decks: [] };
console.log(JSON.stringify({
  note: deckSeedHelpText({ phase: "ready", mode, ...state }),
  confirm: deckSeedConfirmMode({ selected: mode, chosen: false, catalog, pack: { module: "M1", slot: "C2" } }),
  mathNote: deckSeedHelpText({ phase: "ready", mode: "previous", ...state, courseDeckCount: 3 }),
}));
"""
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(got["note"], "No decks yet, and that's fine. Class starts with a blank deck.")
        self.assertNotIn("No deck saved", got["note"])
        self.assertEqual(got["confirm"], "blank")
        self.assertEqual(got["mathNote"], "M1 C1 has no saved deck yet. Starting blank.")


class WhatsNewFilePathTests(_AppCase):
    """5. No spelling of the deploy history file is served."""

    def test_every_variant_is_404(self) -> None:
        """Dot segments, doubled slashes, encodings, case and backslashes."""
        variants = (
            "/static/whats-new/releases.json",
            "/static/./whats-new/releases.json",
            "/static/.//whats-new/releases.json",
            "/static//whats-new/releases.json",
            "/static/whats-new/./releases.json",
            "/static/x/../whats-new/releases.json",
            "/static/%2e/whats-new/releases.json",
            "/static/%2E/whats-new/releases.json",
            "/static/whats-new%2freleases.json",
            "/static/./WHATS-NEW/releases.json",
            "/static/whats-new\\releases.json",
            "/static/./whats-new/",
        )
        for path in variants:
            self.assertEqual(self.client.get(path).status_code, 404, path)
        ok = self.client.get("/static/name_wording.js")
        self.assertEqual(ok.status_code, 200)
        ok.close()


if __name__ == "__main__":
    unittest.main()
