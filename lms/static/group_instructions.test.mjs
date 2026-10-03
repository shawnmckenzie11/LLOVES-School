/**
 * MCK-155 S4 + S5 node checks for group instruction and waiting copy.
 */
import {
  GROUP_INSTRUCTION_COPY,
  consensusWaitHtml,
  groupInstructionHtml,
  groupInstructionKey,
  stillWritingLine,
  waitingLine,
} from "./group_instructions.js";

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL: ${msg}`);
  }
}

// One key per group type; individual has none.
check(groupInstructionKey({ response_mode: "group_consensus" }, { type: "open" }) === "open", "open");
check(groupInstructionKey({ response_mode: "group_submit" }, { type: "rank" }) === "rank", "rank");
check(groupInstructionKey({ response_mode: "group_submit" }, { type: "mc" }) === "mc", "mc");
check(groupInstructionKey({ response_mode: "individual" }, { type: "mc" }) === "", "individual");

// Wonder copy, exact.
check(
  GROUP_INSTRUCTION_COPY.open ===
    "Everyone writes their own answer first. Then your group sends one answer together.",
  "open copy"
);
check(GROUP_INSTRUCTION_COPY.rank === "Rank these together. Anyone can move them.", "rank copy");
check(
  GROUP_INSTRUCTION_COPY.mc === "Talk it over. One person sends your group's answer and why.",
  "mc copy (today's behaviour)"
);

// One line, only while active.
const active = groupInstructionHtml({ response_mode: "group_submit", status: "active" }, { type: "rank" });
check(active.split("<p").length - 1 === 1, "one line");
check(active.includes('data-group-instruction="rank"'), "rank marker");
check(groupInstructionHtml({ response_mode: "group_submit", status: "closed" }, { type: "rank" }) === "", "none when closed");
check(groupInstructionHtml({ response_mode: "individual", status: "active" }, { type: "mc" }) === "", "none individual");

// Waiting copy.
check(waitingLine(2) === "Waiting for 2 more teammates.", "many");
check(waitingLine(1) === "Waiting for 1 more teammate.", "one");
check(waitingLine(0) === "", "none");
check(stillWritingLine(["Ben", "Cy"]) === "Still writing: Ben, Cy", "names");
check(stillWritingLine(["Ben Lee"]) === "Still writing: Ben Lee", "names as the server sends them");
check(stillWritingLine(["Cy Twin", "Cy Other"]) === "Still writing: Cy Twin, Cy Other", "duplicate first names stay apart");
check(stillWritingLine(["🦊 Fox"]) === "Still writing: 🦊 Fox", "emoji-led codename shows in full");
check(stillWritingLine(["Cy", "Cy"]) === "Still writing: Cy", "exact repeats once");
check(stillWritingLine([]) === "", "empty names");

// Wait block: disabled button with its reason.
const wait = consensusWaitHtml({ waiting_count: 2, waiting_names: ["Ben", "Cy"] }, 7);
check(wait.includes("disabled") && wait.includes('aria-disabled="true"'), "button disabled");
check(wait.includes('aria-describedby="group-wait-7"') && wait.includes('id="group-wait-7"'), "reason linked");
check(wait.includes("Send group answer"), "button label");
check(wait.includes("Waiting for 2 more teammates."), "reason text");
check(wait.includes("Still writing: Ben, Cy"), "still writing");
check(wait.includes('aria-live="polite"'), "live region");
check(!consensusWaitHtml({ waiting_count: 1, waiting_names: [] }, 1).includes("Still writing"), "no names line when unknown");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");
