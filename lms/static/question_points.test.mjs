/**
 * MCK-262: the row and the editor share one points figure.
 */
import assert from "node:assert/strict";
import { questionPointsValue } from "./question_points.js";

assert.equal(questionPointsValue({ points: 4, live_points: null }), "4");
assert.equal(questionPointsValue({ points: 4, live_points: "" }), "4");
assert.equal(questionPointsValue({ points_possible: 5 }), "5");
assert.equal(questionPointsValue({ points: 4, live_points: 2 }), "2");
assert.equal(questionPointsValue({ points: 0 }), "0");
assert.equal(questionPointsValue({}), "1");
assert.equal(questionPointsValue(null), "1");

console.log("ok");
