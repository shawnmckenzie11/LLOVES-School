/**
 * A published question that already has a card does not also paint the prompt shell.
 */
import { cardOwnsLivePrompt, livePromptAlias } from "./live_prompt_once.js";

const fail = (msg) => {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
};

if (livePromptAlias("staff-q-abc") !== "staff_q_abc") fail("hyphen alias");
if (cardOwnsLivePrompt({ prompt: { id: 2, kind: "numeric", payload: { text: "How many?" } } })) {
  fail("a prompt with no card was treated as owned");
}

const pushed = {
  prompt: { id: 9, kind: "numeric", payload: { text: "How many roots?" } },
  active_questions: [
    {
      id: 13,
      item_id: "staff-q-1",
      prompt: { id: 9, kind: "numeric", payload: { text: "How many roots?" } },
      content: { type: "numeric", text: "How many roots?" },
    },
    {
      id: 4,
      item_id: "other-question",
      prompt: { id: 3, kind: "mc" },
      content: { type: "mc", text: "Other" },
    },
  ],
};
if (!cardOwnsLivePrompt(pushed)) fail("the published card did not own the prompt shell");
if (
  cardOwnsLivePrompt({
    prompt: { id: 1, kind: "numeric", payload: { item_id: "minds_on", text: "Waiting" } },
    active_questions: [{ id: 2, item_id: "minds_on", content: { text: "Waiting" } }],
  })
) {
  fail("an engine ride lost its prompt shell");
}

for (const ride of ["minds_on", "meet_a", "meet_team", "teams_spark", "team_challenge"]) {
  if (
    cardOwnsLivePrompt({
      prompt: { id: 9, kind: "mc", payload: { item_id: ride, text: "Ride" } },
      active_questions: [
        {
          id: 2,
          item_id: ride,
          prompt: { id: 9, kind: "mc", payload: { item_id: ride } },
          content: { text: "Ride" },
        },
      ],
    })
  ) {
    fail(`${ride} lost its shell because the prompt id matched before the ride check`);
  }
}

console.log("ok");
