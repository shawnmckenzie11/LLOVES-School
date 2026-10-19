/**
 * MCK-283: draft failure notes stay nameless, and a fresh 409 skips resync.
 */
import {
  DRAFT_SAVE_NOTE,
  TURNS_DRAFT_SERVER_TEXT,
  draftFailureNote,
  groupFlowReplySkipsResync,
} from "./phone_save.js";

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL: ${msg}`);
  }
}

check(
  draftFailureNote(400, TURNS_DRAFT_SERVER_TEXT) === TURNS_DRAFT_SERVER_TEXT,
  "take-turns 400 keeps the server sentence",
);
check(
  draftFailureNote(400, "Student 01 just placed that one. Pick another.") === DRAFT_SAVE_NOTE,
  "a 400 that names someone stays the calm line",
);
check(draftFailureNote(500, TURNS_DRAFT_SERVER_TEXT) === DRAFT_SAVE_NOTE, "only status 400");
check(draftFailureNote(0, "") === DRAFT_SAVE_NOTE, "a dropped request");
check(!DRAFT_SAVE_NOTE.includes("Student"), "calm line has no name");

check(groupFlowReplySkipsResync(409, { group_submit: { team_id: 1 } }), "409 with card");
check(!groupFlowReplySkipsResync(409, {}), "409 without card still resyncs");
check(!groupFlowReplySkipsResync(409, null), "409 without a body");
check(!groupFlowReplySkipsResync(400, { group_submit: {} }), "400 still resyncs");
check(!groupFlowReplySkipsResync(200, { group_submit: {} }), "success is not a 409 skip");

if (failures) {
  console.error(`${failures} failed`);
  process.exit(1);
}
console.log("phone_save ok");
