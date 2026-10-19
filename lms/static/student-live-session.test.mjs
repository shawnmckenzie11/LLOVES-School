/**
 * MCK-283: a heartbeat stays single-flight. Hidden tabs still use sendBeacon.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./student-live-session.js", import.meta.url), "utf8");
const start = src.indexOf("function sendHeartbeat()");
const end = src.indexOf("document.addEventListener(\"lloves-live-retry\"");
if (start < 0 || end < start) {
  console.error("sendHeartbeat not found");
  process.exit(1);
}
const body = src.slice(start, end);
const beacon = body.indexOf("document.visibilityState === \"hidden\"");
const guard = body.indexOf("if (heartbeatInFlight) return;");
const arm = body.indexOf("heartbeatInFlight = true;");
const fetchAt = body.indexOf("fetch(url,");
const clear = body.indexOf("heartbeatInFlight = false;");
if (!(beacon >= 0 && beacon < guard && guard < arm && arm < fetchAt && fetchAt < clear)) {
  console.error("heartbeat order is wrong", { beacon, guard, arm, fetchAt, clear });
  process.exit(1);
}
if (!body.includes("navigator.sendBeacon")) {
  console.error("hidden tabs lost sendBeacon");
  process.exit(1);
}
if (!body.includes(".finally(function ()")) {
  console.error("in-flight flag is not cleared when the beat finishes");
  process.exit(1);
}
console.log("student-live-session ok");
