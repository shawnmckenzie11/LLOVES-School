/**
 * MCK-262: bank live defaults seed an inactive, unedited live card.
 */
import assert from "node:assert/strict";
import { bankLiveStart } from "./live_start.js";

const untouched = { created_at: "t", updated_at: "t", status: "inactive" };

assert.deepEqual(
  bankLiveStart(
    { live_response_mode: "group", live_show_results: false, live_points: 3 },
    { ...untouched, response_mode: "individual", show_live_results: true }
  ),
  {
    response_mode: "group_submit",
    publish_mode: "group_submit",
    show_live_results: false,
    live_points: 3,
  }
);

assert.equal(
  bankLiveStart(
    { live_response_mode: null, live_show_results: null, live_points: null },
    { ...untouched, response_mode: "individual", show_live_results: 1 }
  ).response_mode,
  "individual"
);

assert.equal(
  bankLiveStart(
    { live_response_mode: "group" },
    {
      response_mode: "individual",
      show_live_results: true,
      status: "inactive",
      created_at: "t0",
      updated_at: "t1",
    }
  ).response_mode,
  "individual"
);

assert.equal(
  bankLiveStart(
    { live_response_mode: "group", live_show_results: false },
    { ...untouched, response_mode: "individual", show_live_results: true, status: "active" }
  ).response_mode,
  "individual"
);

assert.equal(
  bankLiveStart(
    { live_response_mode: "group" },
    { ...untouched, response_mode: "individual" },
    { modeHeld: true }
  ).response_mode,
  "individual"
);

assert.equal(
  bankLiveStart(
    { live_response_mode: "individual", import_group_preset: "rank" },
    { ...untouched, response_mode: "group_submit" }
  ).response_mode,
  "individual"
);

assert.equal(bankLiveStart({ live_points: "" }, untouched).live_points, null);

console.log("ok");
