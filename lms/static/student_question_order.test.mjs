/**
 * MCK-495: Q numbers follow published_at, then the live item id.
 */
import { compareQuestionPushOrder, questionPushLabel } from "./student_question_order.js";

const fail = (msg) => {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
};

const later = { id: 9, published_at: "2026-11-23T15:03:00", content: { text: "third" } };
const first = { id: 4, published_at: "2026-11-23T15:01:00", content: { text: "first" } };
const tiedHighId = { id: 8, published_at: "2026-11-23T15:02:00", content: { text: "tie-b" } };
const tiedLowId = { id: 5, published_at: "2026-11-23T15:02:00", content: { text: "tie-a" } };
const undated = { id: 2, published_at: "", kind: "question", content: { text: "undated" } };
const slides = { id: 1, published_at: "2026-11-23T15:00:00", kind: "slides" };

const session = [later, slides, undated, tiedHighId, first, tiedLowId];
const ordered = session
  .filter((row) => row.kind !== "slides")
  .slice()
  .sort(compareQuestionPushOrder)
  .map((row) => row.id);

if (ordered.join(",") !== "4,5,8,9,2") {
  fail(`push order ${ordered.join(",")} expected 4,5,8,9,2`);
}

const labels = [first, tiedLowId, tiedHighId, later, undated].map((row) =>
  questionPushLabel(row, session)
);
if (labels.join(",") !== "Q1,Q2,Q3,Q4,Q5") {
  fail(`labels ${labels.join(",")} expected Q1,Q2,Q3,Q4,Q5`);
}

if (questionPushLabel(slides, session) !== "Q6") {
  fail("a surface row is not a numbered question");
}

if (questionPushLabel(first, [later, first]) !== "Q1") {
  fail("earlier published_at stays Q1 when the list is newest-first");
}

console.log("ok");
