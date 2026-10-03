/**
 * MCK-161: Contest Questions helper copy and pure logic. Empty-group copy
 * (capital Q, no "Contest has no Contest Questions"), the section heading,
 * stale-pick status and detection (LOW-8), and the typed-search reveal
 * offset under the sticky search row (LOW-6).
 */
import {
  contentRowsView,
  contentSectionHeading,
  contentStalePicks,
  contentStaleText,
  searchResultsScroll,
} from "./content_questions_help.js";

let failures = 0;
function eq(actual, expected, label) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    failures += 1;
    console.error(`FAIL ${label}\n  expected: ${e}\n  actual:   ${a}`);
  }
}

// Empty-group copy.
eq(contentRowsView({ module: "M7", label: "Module 7", items: [] }).empty,
  "Module 7 has no Contest Questions yet.", "module empty line");
eq(contentRowsView({ module: "COURSE", label: "Contest", items: [] }).empty,
  "No course-wide Contest Questions yet.", "course-wide empty line");
eq(contentRowsView({ module: "M1", items: [{ question_id: 3 }] }).empty, "", "filled group");

// Heading allows for the course-wide group.
eq(contentSectionHeading(6), "Contest Questions · top 6 per module and course-wide", "heading");
eq(contentSectionHeading(), "Contest Questions · top 6 per module and course-wide", "heading default");

// Stale status copy.
eq(contentStaleText(1),
  "One pick is no longer in its group's top 6, so nothing was imported. The list is refreshed and that pick is unticked. Check your picks and import again.",
  "stale one");
eq(contentStaleText(3).startsWith("3 picks are no longer in their group's top 6, so nothing was imported."),
  true, "stale many");
eq(/\d{2,}|problem|Contest Contest/.test(contentStaleText(1)), false, "no ids / no Contest Contest");

// Stale detection: only reloaded groups judge their picks.
const picks = [
  { question_id: 11, module: "M1" },
  { question_id: 64, module: "COURSE" },
  { question_id: 59, module: "COURSE" },
  { question_id: 80, module: "M2" },
];
eq(contentStalePicks(picks, new Map([["M1", [11, 12]], ["COURSE", [59, 60]]])),
  [{ question_id: 64, module: "COURSE" }], "one stale course pick");
eq(contentStalePicks(picks, { M1: [], COURSE: [64, 59] }),
  [{ question_id: 11, module: "M1" }], "object lookup");
eq(contentStalePicks(picks, new Map()), [], "nothing reloaded");
eq(contentStalePicks(null, new Map([["M1", []]])), [], "no picks");

// Typed-search reveal under the sticky head.
eq(searchResultsScroll({ resultsTop: 200, headBottom: 120, viewBottom: 540 }), 0, "already visible");
eq(searchResultsScroll({ resultsTop: 1510, headBottom: 160, viewBottom: 547 }), 1344, "below the fold");
eq(searchResultsScroll({ resultsTop: 500, headBottom: 160, viewBottom: 547 }), 334, "too close to the bottom");
eq(searchResultsScroll({ resultsTop: 40, headBottom: 160, viewBottom: 547 }), -126, "hidden under the head");
eq(searchResultsScroll({}), 0, "bad box");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("content_questions_help.test.mjs: all passed");
