/**
 * MCK-499: student image buttons, badge copy, and compact stack layout.
 */
import {
  newestOpenQuestionId,
  questionCardLayout,
  questionImageHtmlStudent,
  zoomBadgeLabel,
} from "./live_question_card.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (zoomBadgeLabel(false) !== "Tap to enlarge") fail("touch badge");
if (zoomBadgeLabel(true) !== "Click to enlarge") fail("desktop badge");

const html = questionImageHtmlStudent({
  image_urls: ["/static/graph-a.png", "/static/graph-b.png"],
  image_alts: ["First graph"],
});
if (!html.includes('class="live-q-image-btn"')) fail("image is not a button");
if (!html.includes('aria-label="Enlarge image 1 of 2"')) fail("enlarge label");
if (!html.includes('alt="First graph"')) fail("bank alt was dropped");
if (!html.includes('alt="Question image 2"')) fail("missing alt fallback");
if (!html.includes("Tap to enlarge")) fail("badge copy");
if (!html.includes('width="4"') || !html.includes('height="3"')) fail("size attributes");
if (html.includes('loading="lazy"')) fail("live question images must load eagerly");
if (!html.includes('loading="eager"') || !html.includes('decoding="async"')) {
  fail("eager async image attributes");
}
if (html.includes("live-question-card-options")) fail("image html picked up options");

const open = { id: 2, status: "active" };
const answered = { id: 1, status: "active", my_response: { response: { value: 4 } } };
const closed = { id: 3, status: "closed" };
if (questionCardLayout(open, 2) !== "is-open-focus") fail("open card is not the focus");
if (questionCardLayout(answered, 2) !== "is-open-extra") {
  fail("answered card must stay open after submit (MCK-509)");
}
if (questionCardLayout(closed, 2) !== "is-compact") fail("closed card did not collapse");
if (questionCardLayout({ id: 4, status: "active" }, 2) !== "is-open-extra") {
  fail("older open card took the focus");
}
if (newestOpenQuestionId([answered, open, closed]) !== 2) fail("newest open id");

console.log("ok");
