#!/usr/bin/env python3
"""MCK-116 S2: earned reward avatars (art, key lists, picker, earned seam)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from db import EARNED_CHARACTERS, STUDENT_CHARACTERS  # noqa: E402
from student_portal import (  # noqa: E402
    EARNED_AVATARS,
    character_choices,
    earned_avatar_keys,
)

ART_DIR = LMS_DIR / "static" / "avatars" / "earned"
AVATARS_JS = LMS_DIR / "static" / "student_avatars.js"
SVG_NS = "{http://www.w3.org/2000/svg}"

# Grid order and names from the MCK-116 spec, §2.3 (name = alt text).
SPEC_AVATARS = (
    ("fox_scarf", "Fox in a scarf"),
    ("owl_glasses", "Owl in round glasses"),
    ("penguin_beanie", "Penguin in a beanie"),
    ("frog_crown", "Frog in a tiny crown"),
    ("panda_leaf_crown", "Panda in a leaf crown"),
    ("unicorn_ribbon", "Unicorn with a ribbon"),
    ("octopus_star", "Octopus holding a star"),
    ("dragon_lantern", "Dragon with a lantern"),
    ("cat_bow_tie", "Cat in a bow tie"),
    ("bear_medal", "Bear with a medal"),
    ("rabbit_headphones", "Rabbit in headphones"),
    ("hedgehog_acorn", "Hedgehog with an acorn"),
    ("koala_pencil", "Koala with a pencil"),
    ("lion_cub_laurel", "Lion cub in a laurel"),
    ("turtle_star_shell", "Turtle with a star shell"),
    ("axolotl_seashell", "Axolotl with a seashell"),
    ("raccoon_backpack", "Raccoon with a backpack"),
    ("whale_star_spout", "Whale with a star spout"),
    ("phoenix_chick_spark", "Phoenix chick with a spark"),
    ("narwhal_star_horn", "Narwhal with a star horn"),
)

GOLD = "#f5c518"
GOLD_DARK = "#c99a06"
TEAL = "#2dd4bf"
# Spec §2.2 tokens, plus the one-step darker / lighter same-hue shades §2.1 allows.
PALETTE = {
    "#1f2937", "#f4f6fb", "#fff4e0", "#f28a2e", "#7a3f12", "#a86b3c", "#7a4a26",
    "#9aa6b8", "#3a4559", "#5cc46a", "#2f8f46", "#c8f0c4", "#f6a6c1", "#a78bfa",
    "#60a5fa", "#2f6fc0", GOLD, GOLD_DARK,
    "#e7c9a0",  # cream shade (owl belly marks, horns, muzzles)
    "#d9779b",  # pink shade (axolotl gills, nostrils)
    "#7c63d6",  # purple shade (octopus spots, smile)
    "#e25b2c",  # phoenix flame (crest, wings); not gold
}
ALLOWED_TAGS = {"svg", "title", "path", "circle", "ellipse", "rect"}
ALLOWED_ATTRS = {
    "xmlns", "viewBox", "width", "height", "role", "aria-label", "d", "fill",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "cx", "cy", "r", "rx", "ry", "x", "y",
}


def _js_earned_keys() -> list[str]:
    """Parse ``EARNED_AVATAR_KEYS`` out of student_avatars.js."""
    text = AVATARS_JS.read_text(encoding="utf-8")
    block = text.split("EARNED_AVATAR_KEYS = Object.freeze([", 1)[1].split("]);", 1)[0]
    return re.findall(r'"([a-z_]+)"', block)


class EarnedAvatarListTests(unittest.TestCase):
    """The three key lists and the files agree with the spec."""

    def test_three_lists_match_spec_order(self) -> None:
        """db.EARNED_CHARACTERS == EARNED_AVATARS keys == JS keys == spec §2.3."""
        spec_keys = tuple(key for key, _ in SPEC_AVATARS)
        self.assertEqual(len(spec_keys), 20)
        self.assertEqual(EARNED_CHARACTERS, spec_keys)
        self.assertEqual(tuple(EARNED_AVATARS), spec_keys)
        self.assertEqual(tuple(_js_earned_keys()), spec_keys)
        self.assertEqual(dict(SPEC_AVATARS), EARNED_AVATARS)

    def test_keys_are_snake_case_and_separate_from_the_six_emoji(self) -> None:
        """Keys are lowercase snake_case and never collide with the base set."""
        self.assertEqual(STUDENT_CHARACTERS, ("fox", "panda", "unicorn", "octopus", "dragon", "owl"))
        self.assertFalse(set(EARNED_CHARACTERS) & set(STUDENT_CHARACTERS))
        self.assertEqual(len(set(EARNED_CHARACTERS)), 20)
        for key in EARNED_CHARACTERS:
            self.assertRegex(key, r"^[a-z]+(_[a-z]+)+$")

    def test_exactly_twenty_files(self) -> None:
        """One SVG per key, nothing else in the folder."""
        files = sorted(path.name for path in ART_DIR.iterdir())
        self.assertEqual(files, sorted(f"{key}.svg" for key in EARNED_CHARACTERS))


class EarnedAvatarArtTests(unittest.TestCase):
    """Every SVG follows the spec's production and style rules."""

    def _each(self):
        for key, name in SPEC_AVATARS:
            path = ART_DIR / f"{key}.svg"
            with self.subTest(avatar=key):
                yield key, name, path, ET.parse(path).getroot()

    def test_root_is_36_unit_canvas_with_name(self) -> None:
        """viewBox 0 0 36 36, 72x72, role=img, aria-label and <title> = name."""
        for _key, name, _path, root in self._each():
            self.assertEqual(root.tag, f"{SVG_NS}svg")
            self.assertEqual(root.get("viewBox"), "0 0 36 36")
            self.assertEqual(root.get("width"), "72")
            self.assertEqual(root.get("height"), "72")
            self.assertEqual(root.get("role"), "img")
            self.assertEqual(root.get("aria-label"), name)
            self.assertIsNone(root.get("transform"))
            title = root.find(f"{SVG_NS}title")
            self.assertIsNotNone(title)
            assert title is not None
            self.assertEqual(title.text, name)
            self.assertEqual(list(root)[0], title)

    def test_small_and_only_allowed_elements(self) -> None:
        """<= 4 KB; path/circle/ellipse/rect only; no style, script, text, refs."""
        for _key, _name, path, root in self._each():
            raw = path.read_text(encoding="utf-8")
            self.assertLessEqual(len(raw.encode("utf-8")), 4096)
            for banned in ("<style", "<script", "<image", "<text", "Gradient", "<filter",
                           "<mask", "<use", "href", "url(", "<g", "transform", "opacity"):
                self.assertNotIn(banned, raw)
            for el in root.iter():
                tag = el.tag.replace(SVG_NS, "")
                self.assertIn(tag, ALLOWED_TAGS)
                self.assertFalse(set(el.attrib) - ALLOWED_ATTRS, f"{tag} {el.attrib}")

    def test_palette_gold_keepsake_and_no_teal(self) -> None:
        """Flat palette fills, gold present (the keepsake), teal never used."""
        for _key, _name, _path, root in self._each():
            colours = set()
            gold_shapes = 0
            for el in list(root)[1:]:
                for attr in ("fill", "stroke"):
                    value = (el.get(attr) or "").lower()
                    if value and value != "none":
                        colours.add(value)
                if (el.get("fill") or "").lower() == GOLD or (el.get("stroke") or "").lower() == GOLD:
                    gold_shapes += 1
            self.assertTrue(colours <= PALETTE, sorted(colours - PALETTE))
            self.assertNotIn(TEAL, colours)
            self.assertGreaterEqual(gold_shapes, 1)
            body = colours - {GOLD, GOLD_DARK, "#1f2937"}
            # Sample bar (frog, owl): 4 body colours + one shade or cheek pink.
            self.assertLessEqual(len(body), 5, sorted(body))

    def test_detail_strokes_are_one_to_one_point_six_units(self) -> None:
        """Strokes read at 20 px: 1.0–1.6 units."""
        for _key, _name, _path, root in self._each():
            for el in root.iter():
                width = el.get("stroke-width")
                if width is not None:
                    self.assertGreaterEqual(float(width), 1.0)
                    self.assertLessEqual(float(width), 1.6)

    def test_simple_shapes_stay_on_the_canvas(self) -> None:
        """Circles, ellipses and rects sit inside the 36-unit box."""
        for _key, _name, _path, root in self._each():
            for el in root.iter():
                tag = el.tag.replace(SVG_NS, "")
                if tag == "circle":
                    cx, cy, r = (float(el.get(k)) for k in ("cx", "cy", "r"))
                    box = (cx - r, cy - r, cx + r, cy + r)
                elif tag == "ellipse":
                    cx, cy, rx, ry = (float(el.get(k)) for k in ("cx", "cy", "rx", "ry"))
                    box = (cx - rx, cy - ry, cx + rx, cy + ry)
                elif tag == "rect":
                    x, y, w, h = (float(el.get(k)) for k in ("x", "y", "width", "height"))
                    box = (x, y, x + w, y + h)
                else:
                    continue
                self.assertGreaterEqual(min(box[0], box[1]), 0.0, el.attrib)
                self.assertLessEqual(max(box[2], box[3]), 36.0, el.attrib)


class EarnedSeamAndChoicesTests(unittest.TestCase):
    """The S1 seam and character_choices()."""

    def test_seam_returns_nothing_earned_until_s1(self) -> None:
        """No store yet: every student has earned nothing."""
        got = earned_avatar_keys(object(), 1, 1)
        self.assertEqual(got, ())
        self.assertIsInstance(got, tuple)

    def test_choices_default_to_the_six_emoji(self) -> None:
        """No earned keys: the picker is today's six, unchanged."""
        choices = character_choices()
        self.assertEqual([row["key"] for row in choices], list(STUDENT_CHARACTERS))
        self.assertTrue(all(row["emoji"] and not row["earned"] for row in choices))
        self.assertEqual(character_choices(()), choices)

    def test_choices_append_owned_in_grid_order_and_ignore_unknown(self) -> None:
        """Owned keys follow the six, in spec order; unknown keys are dropped."""
        choices = character_choices(["koala_pencil", "nope", "fox_scarf", "fox"])
        self.assertEqual(
            [row["key"] for row in choices],
            [*STUDENT_CHARACTERS, "fox_scarf", "koala_pencil"],
        )
        fox = choices[6]
        self.assertEqual(fox["label"], "Fox in a scarf")
        self.assertEqual(fox["img"], "/static/avatars/earned/fox_scarf.svg")
        self.assertTrue(fox["earned"])
        self.assertNotIn("emoji", fox)


class EarnedPickerRouteTests(unittest.TestCase):
    """/student/character with and without earned avatars."""

    def setUp(self) -> None:
        """One rostered class in a running live session; Maple joined, mood done."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(f"/staff/class/{self.class_id}/run-live", follow_redirects=False)
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.session_code = str(live["session_code"])

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _join(self, name: str):
        """Join as ``name`` and skip past mood; returns (client, student_id)."""
        client = self.app.test_client()
        join = client.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": name},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302)
        mood = client.post("/student/mood", data={"mood": "good"}, follow_redirects=False)
        self.assertIn("/student/character", mood.headers.get("Location", ""))
        row = self.school.game.find_student_by_codename(self.class_id, name)
        assert row is not None
        return client, int(row["id"])

    def _character_key(self, student_id: int):
        return self.school.game.get_student(self.class_id, student_id).get("character_key")

    def test_today_picker_unchanged_without_earned(self) -> None:
        """Seam says nothing earned: six emoji, no Earned tag, earned keys refused."""
        client, maple_id = self._join("Maple")
        html = client.get("/student/character").get_data(as_text=True)
        self.assertEqual(html.count('name="character"'), 6)
        self.assertNotIn("tag-earned", html)
        self.assertNotIn("/static/avatars/earned/", html)
        for glyph in "🦊🐼🦄🐙🐲🦉":
            self.assertIn(glyph, html)
        refused = client.post("/student/character", data={"character": "fox_scarf"})
        self.assertEqual(refused.status_code, 200)
        self.assertIn("Choose an avatar.", refused.get_data(as_text=True))
        self.assertIsNone(self._character_key(maple_id))
        picked = client.post("/student/character", data={"character": "owl"}, follow_redirects=False)
        self.assertEqual(picked.status_code, 302)
        self.assertIn("/student/home", picked.headers.get("Location", ""))
        self.assertEqual(self._character_key(maple_id), "owl")

    def test_picker_renders_all_twenty_earned_with_tag(self) -> None:
        """All 20 owned: 26 tiles, each earned tile has its SVG, name and Earned."""
        client, maple_id = self._join("Maple")
        with mock.patch("app.earned_avatar_keys", return_value=EARNED_CHARACTERS):
            html = client.get("/student/character").get_data(as_text=True)
        self.assertEqual(html.count('name="character"'), 26)
        self.assertEqual(html.count('<span class="tag-earned">Earned</span>'), 20)
        self.assertEqual(html.count('class="char-card is-earned"'), 20)
        for key, name in SPEC_AVATARS:
            self.assertIn(f'value="{key}"', html)
            self.assertIn(f'src="/static/avatars/earned/{key}.svg"', html)
            self.assertIn(f'<span class="visually-hidden">{name}</span>', html)
        for key in STUDENT_CHARACTERS:
            self.assertIn(f'value="{key}"', html)
        self.assertLess(html.index('value="owl"'), html.index('value="fox_scarf"'))
        self.assertIsNone(self._character_key(maple_id))

    def test_owned_earned_avatar_can_be_picked_and_reaches_state(self) -> None:
        """An owned key saves and comes back on /api/student/state."""
        client, maple_id = self._join("Maple")
        with mock.patch("app.earned_avatar_keys", return_value=("koala_pencil",)):
            page = client.get("/student/character").get_data(as_text=True)
            self.assertEqual(page.count('name="character"'), 7)
            picked = client.post(
                "/student/character",
                data={"character": "koala_pencil"},
                follow_redirects=False,
            )
        self.assertEqual(picked.status_code, 302)
        self.assertIn("/student/home", picked.headers.get("Location", ""))
        self.assertEqual(self._character_key(maple_id), "koala_pencil")
        state = client.get("/api/student/state").get_json()
        self.assertEqual((state.get("me") or {}).get("character"), "koala_pencil")

    def test_earned_avatar_is_per_student(self) -> None:
        """Maple owns one; Aspen's picker and POST don't get it."""
        maple, maple_id = self._join("Maple")
        aspen, aspen_id = self._join("Aspen")

        def owned(_school, _class_id, student_id):
            return ("bear_medal",) if student_id == maple_id else ()

        with mock.patch("app.earned_avatar_keys", side_effect=owned):
            maple_html = maple.get("/student/character").get_data(as_text=True)
            aspen_html = aspen.get("/student/character").get_data(as_text=True)
            refused = aspen.post("/student/character", data={"character": "bear_medal"})
        self.assertIn('value="bear_medal"', maple_html)
        self.assertNotIn('value="bear_medal"', aspen_html)
        self.assertNotIn("tag-earned", aspen_html)
        self.assertIn("Choose an avatar.", refused.get_data(as_text=True))
        self.assertIsNone(self._character_key(aspen_id))

    def test_set_character_accepts_earned_keys_and_rejects_unknown(self) -> None:
        """The game DB stores either set; anything else is refused."""
        _client, maple_id = self._join("Maple")
        self.school.game.set_character(self.class_id, maple_id, "narwhal_star_horn")
        self.assertEqual(self._character_key(maple_id), "narwhal_star_horn")
        with self.assertRaises(ValueError):
            self.school.game.set_character(self.class_id, maple_id, "dragon_lantern.svg")

    def test_static_route_serves_the_svgs(self) -> None:
        """Every file is served as image/svg+xml from /static/avatars/earned/."""
        client = self.app.test_client()
        for key in EARNED_CHARACTERS:
            with self.subTest(avatar=key):
                rv = client.get(f"/static/avatars/earned/{key}.svg")
                self.assertEqual(rv.status_code, 200)
                self.assertIn("image/svg+xml", rv.headers.get("Content-Type", ""))
                rv.close()


class EarnedAvatarJsTests(unittest.TestCase):
    """student_avatars.js renders emoji or the earned SVG (node harness)."""

    def test_markup_paint_and_name_row(self) -> None:
        """avatarMarkup / paintAvatar / nameWithAvatar for both kinds of key."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        mod = AVATARS_JS.resolve().as_uri()
        script = f"""
import {{ EARNED_AVATAR_KEYS, avatarGlyph, avatarMarkup, earnedAvatarSrc, nameWithAvatar, paintAvatar }} from {json.dumps(mod)};
function fakeEl() {{
  const el = {{
    childNodes: [], text: "",
    get firstElementChild() {{ return this.childNodes.find((n) => n.tag) || null; }},
    set textContent(v) {{ this.text = String(v); this.childNodes = v ? [{{ text: v }}] : []; }},
    get textContent() {{ return this.text; }},
    replaceChildren(...nodes) {{ this.childNodes = nodes; this.text = ""; this.replaced = (this.replaced || 0) + 1; }},
    ownerDocument: {{ createElement: (tag) => ({{ tag, attrs: {{}}, set src(v) {{ this.attrs.src = v; }},
      getAttribute(name) {{ return this.attrs[name]; }} }}) }},
  }};
  return el;
}}
const emojiEl = fakeEl();
const svgEl = fakeEl();
const emptyEl = fakeEl();
const paintedEmoji = paintAvatar(emojiEl, "fox");
const paintedSvg = paintAvatar(svgEl, "owl_glasses");
paintAvatar(svgEl, "owl_glasses");
const paintedEmpty = paintAvatar(emptyEl, "");
console.log(JSON.stringify({{
  count: EARNED_AVATAR_KEYS.length,
  frozen: Object.isFrozen(EARNED_AVATAR_KEYS),
  fox: avatarMarkup("fox"),
  scarf: avatarMarkup(" Fox_Scarf "),
  glyphForEarned: avatarGlyph("fox_scarf"),
  bad: avatarMarkup('"><img src=x onerror=alert(1)>'),
  badSrc: earnedAvatarSrc("../app.py"),
  none: avatarMarkup(null),
  name: nameWithAvatar("<Nova>", "octopus_star"),
  plain: nameWithAvatar("Nova", "nope"),
  paintedEmoji, emojiText: emojiEl.textContent,
  paintedSvg, svgSrc: svgEl.childNodes[0].attrs.src, svgAlt: svgEl.childNodes[0].alt,
  svgClass: svgEl.childNodes[0].className, replaced: svgEl.replaced,
  paintedEmpty, emptyText: emptyEl.textContent,
}}));
"""
        done = subprocess.run(
            [node, "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        got = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(got["count"], 20)
        self.assertTrue(got["frozen"])
        self.assertEqual(got["fox"], "🦊")
        self.assertEqual(
            got["scarf"],
            '<img class="avatar-img" src="/static/avatars/earned/fox_scarf.svg" alt="">',
        )
        self.assertEqual(got["glyphForEarned"], "")
        self.assertEqual(got["bad"], "")
        self.assertEqual(got["badSrc"], "")
        self.assertEqual(got["none"], "")
        self.assertIn('src="/static/avatars/earned/octopus_star.svg"', got["name"])
        self.assertIn("&lt;Nova&gt;", got["name"])
        self.assertIn('class="student-avatar" aria-hidden="true"', got["name"])
        self.assertEqual(got["plain"], "Nova")
        self.assertTrue(got["paintedEmoji"])
        self.assertEqual(got["emojiText"], "🦊")
        self.assertTrue(got["paintedSvg"])
        self.assertEqual(got["svgSrc"], "/static/avatars/earned/owl_glasses.svg")
        self.assertEqual(got["svgAlt"], "")
        self.assertEqual(got["svgClass"], "avatar-img")
        self.assertEqual(got["replaced"], 1)
        self.assertFalse(got["paintedEmpty"])
        self.assertEqual(got["emptyText"], "")

    def test_css_sizes_and_tag(self) -> None:
        """Picker image 2.9rem, inline 1.15em, Earned tag styled; overlay sized too."""
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        overlay_css = (
            REPO_ROOT / "tools" / "math-game-show" / "static" / "app.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".char-card .avatar-img {\n  width: 2.9rem;\n  height: 2.9rem;", css)
        self.assertIn(".avatar-img {\n  display: inline-block;\n  width: 1.15em;", css)
        self.assertIn(".tag-earned {", css)
        self.assertIn(".student-avatar .avatar-img {", overlay_css)
        template = (LMS_DIR / "templates" / "student" / "character.html").read_text(encoding="utf-8")
        self.assertIn('<span class="tag-earned">Earned</span>', template)


if __name__ == "__main__":
    unittest.main()
