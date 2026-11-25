/**
 * MCK-526: first paint of a published MC includes stem and option images.
 * applyPushedQuestionCard paints from contentFromPushedQuestionCard.
 */
import {
  contentFromPushedQuestionCard,
  liveAnswerControlsHtml,
  liveChoiceLabels,
  liveQuestionHeadHtml,
} from "./live_question_card.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

const format = (text) => String(text || "");
const stem = "/curriculum-bank-images/MCF3M/nelson_assets/mck526/stem.png";
const field = "/curriculum-bank-images/MCF3M/nelson_assets/mck526/field.png";
const optA = "/curriculum-bank-images/MCF3M/nelson_assets/mck526/opt-a.png";
const optB = "/curriculum-bank-images/MCF3M/nelson_assets/mck526/opt-b.png";

const card = {
  item_id: 12,
  prompt_id: 8,
  kind: "mc",
  text: "Which graph?",
  text_html: `<p>Which graph?</p><img src="${stem}" alt="">`,
  options: ["A", "B"],
  options_html: [`<img src="${optA}" alt="">`, `<img src="${optB}" alt="">`],
  options_image_urls: [optA, optB],
  image_url: field,
  image_urls: [field],
  mode: "individual",
  flow: "",
  rank_mode: "",
  published_ms: 1795000000123,
  correct_answer: "must-not-copy",
  key: "A",
};

const content = contentFromPushedQuestionCard(card);
if (!content) fail("postcard content was dropped");
if (content.text !== "Which graph?") fail("stem text");
if (!String(content.text_html).includes(stem)) fail("stem html image");
if (content.image_url !== field) fail("gallery image_url");
if (!Array.isArray(content.image_urls) || content.image_urls[0] !== field) {
  fail("gallery image_urls");
}
if (JSON.stringify(content).includes("must-not-copy")) fail("answer key leaked into content");
if (JSON.stringify(content).includes('"key":')) fail("key field leaked into content");

const head = liveQuestionHeadHtml(content, "mc", format);
if (!head.includes(field)) fail("head is missing the gallery image");
if (!head.includes(stem) && !head.includes("Which graph?")) fail("head is missing the stem");

const controls = liveAnswerControlsHtml("mc", content, "item:12", "—", "Submit Answer");
if ((controls.match(/class="prompt-choice/g) || []).length !== 2) {
  fail("expected two choice buttons");
}
if (!controls.includes(optA) || !controls.includes(optB)) {
  fail("option images were not painted");
}
if (!controls.includes('data-live-choice="A"') || !controls.includes('data-live-choice="B"')) {
  fail("image-only options need a submit value");
}

const emptyLabels = contentFromPushedQuestionCard({
  ...card,
  options: ["", ""],
});
if (!emptyLabels) fail("image-only postcard with empty labels was dropped");
const labels = liveChoiceLabels(emptyLabels);
if (labels.length !== 2) fail(`image-only labels should keep two slots, got ${labels}`);
const emptyControls = liveAnswerControlsHtml(
  "mc",
  emptyLabels,
  "item:12",
  "—",
  "Submit Answer"
);
if ((emptyControls.match(/class="prompt-choice/g) || []).length !== 2) {
  fail("applyPushedQuestionCard must render buttons for image-only options");
}
if (!emptyControls.includes(optA) || !emptyControls.includes(optB)) {
  fail("image-only buttons are missing option figures");
}

const imageUrlsOnly = contentFromPushedQuestionCard({
  item_id: 13,
  kind: "mc",
  text: "Which graph?",
  options: [],
  options_image_urls: [optA, optB],
});
if (!imageUrlsOnly) fail("options_image_urls-only postcard was dropped");
const urlControls = liveAnswerControlsHtml(
  "mc",
  imageUrlsOnly,
  "item:13",
  "—",
  "Submit Answer"
);
if ((urlControls.match(/class="prompt-choice/g) || []).length !== 2) {
  fail("option image URLs alone should still render buttons");
}
if (!urlControls.includes(`src="${optA}"`)) fail("option image URL was not used as a figure");

const postcardLabels = liveChoiceLabels(content);
const stateShaped = {
  type: "mc",
  text: "Which graph?",
  options: ["", ""],
  choices: [
    { id: "b", html: `<img src="${optA}" alt="">`, correct: false },
    { id: "a", html: `<img src="${optB}" alt="">`, correct: true },
  ],
  options_html: [`<img src="${optA}" alt="">`, `<img src="${optB}" alt="">`],
  options_image_urls: [optA, optB],
};
const stateLabels = liveChoiceLabels(stateShaped);
if (JSON.stringify(postcardLabels) !== JSON.stringify(["A", "B"])) {
  fail(`postcard letters should be A,B got ${postcardLabels}`);
}
if (JSON.stringify(stateLabels) !== JSON.stringify(postcardLabels)) {
  fail(` /state labels ${stateLabels} must match postcard ${postcardLabels}`);
}
if (stateLabels.includes("a") || stateLabels.includes("b")) {
  fail("image-only buttons must use slot letters, not catalogue ids");
}

const mixed = liveChoiceLabels({
  type: "mc",
  options: ["Constant rate", "", "A curve"],
  choices: [
    { id: "a", text: "Constant rate" },
    { id: "b", html: `<img src="${optA}" alt="">` },
    { id: "c", text: "A curve" },
  ],
  options_html: ["", `<img src="${optA}" alt="">`, ""],
  options_image_urls: ["", optA, ""],
});
if (JSON.stringify(mixed) !== JSON.stringify(["Constant rate", "B", "A curve"])) {
  fail(`mixed list should keep text and letter the image-only slot, got ${mixed}`);
}

const textOnly = liveChoiceLabels({
  type: "mc",
  options: ["kite", "moth", "wren", "lark"],
});
if (JSON.stringify(textOnly) !== JSON.stringify(["kite", "moth", "wren", "lark"])) {
  fail(`text-only labels changed, got ${textOnly}`);
}

console.log("ok");
