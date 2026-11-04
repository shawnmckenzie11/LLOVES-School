/**
 * MCK-443: a removed deck card stays gone after (Re)move.
 */
import assert from "node:assert/strict";
import { deckItemAlias, withoutRemovedCards } from "./deck_card_merge.js";

const stale = [
  { id: "minds_on", text: "A graph of ..." },
  { id: "meet-team", text: "Today I'm the teammate who..." },
  { id: "bank-import-7", text: "Kept" },
];

// Removed built-ins are dropped even when the live cards still carry them.
assert.deepEqual(
  withoutRemovedCards(stale, ["minds-on", "meet:3:1:meet_team"], ["bank-import-7"]).map(
    (c) => c.id
  ),
  ["bank-import-7"]
);

// The deck is authoritative: an id listed again comes back.
assert.deepEqual(
  withoutRemovedCards(stale, ["meet-team"], ["meet_team", "bank-import-7"]).map(
    (c) => c.id
  ),
  ["minds_on", "meet-team", "bank-import-7"]
);

// Nothing removed: cards pass through untouched.
assert.equal(withoutRemovedCards(stale, [], []), stale);
assert.deepEqual(withoutRemovedCards(null, ["x"], []), []);
assert.equal(deckItemAlias("round:2:1:Teams-Spark"), "teams_spark");

console.log("deck_card_merge ok");
