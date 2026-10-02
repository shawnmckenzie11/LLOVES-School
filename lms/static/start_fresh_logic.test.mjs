/**
 * MCK-160: Start fresh helpers. Error text for JSON reasons vs HTML/proxy
 * bodies (LOW-11), success copy with skipped (shared) courses, and the
 * course list wording.
 */
import {
  FRESH_COPY,
  FreshError,
  errorText,
  joinCourses,
  postStartFresh,
  successText,
} from "./start_fresh_logic.js";

let failures = 0;
function eq(actual, expected, label) {
  if (actual !== expected) {
    failures += 1;
    console.error(`FAIL ${label}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
  }
}

function fakeFetch(status, body, calls = []) {
  return async (url, init) => {
    calls.push({ url, init });
    return { ok: status >= 200 && status < 300, status, text: async () => body };
  };
}

async function failure(status, body) {
  try {
    await postStartFresh(fakeFetch(status, body), { scope: "all" });
  } catch (err) {
    return err;
  }
  return null;
}

// joinCourses
eq(joinCourses([]), "", "join empty");
eq(joinCourses(["MCR3U"]), "MCR3U", "join one");
eq(joinCourses(["MCR3U", "SBI3U"]), "MCR3U and SBI3U", "join two");
eq(joinCourses(["MCF3M", "MCR3U", "SBI3U"]), "MCF3M, MCR3U and SBI3U", "join three");

// successText
eq(successText({ value: "all" }), FRESH_COPY.toastAll, "all, nothing skipped");
eq(successText({ value: "7", name: "MCF3M" }), "Celebrations started fresh for MCF3M. Past records are kept.", "one class");
eq(
  successText({ value: "all", started: ["MCF3M"], skipped: ["MCR3U"] }),
  "Celebrations started fresh for MCF3M. Past records are kept. MCR3U skipped: shared with another teacher's class.",
  "all with one skipped names what started",
);
eq(
  successText({ value: "all", started: ["MCF3M"], skipped: ["MCR3U", "SBI3U"] }),
  "Celebrations started fresh for MCF3M. Past records are kept. MCR3U and SBI3U skipped: shared with other teachers' classes.",
  "all with two skipped",
);

// postStartFresh: success
{
  const calls = [];
  const data = await postStartFresh(fakeFetch(200, '{"ok":true,"periods":[]}', calls), { scope: "all" });
  eq(data.ok, true, "success body");
  eq(calls[0].url, "/api/staff/celebrations/start-fresh", "url");
  eq(calls[0].init.method, "POST", "method");
  eq(calls[0].init.credentials, "same-origin", "credentials");
  eq(calls[0].init.body, '{"scope":"all"}', "payload");
}

// postStartFresh: JSON reasons are shown as sent
{
  const err = await failure(409, '{"ok":false,"error":"A class is running. Save or end it, then try again."}');
  eq(err instanceof FreshError, true, "409 is FreshError");
  eq(err.status, 409, "409 status");
  eq(errorText(err), "A class is running. Save or end it, then try again.", "409 reason shown");
}

// LOW-11: non-JSON and odd bodies get the generic copy
const generic = [
  [500, '<!doctype html>\n<html lang=en>\n<title>500 Internal Server Error</title>\n<h1>Internal Server Error</h1>'],
  [502, "upstream connect error or disconnect/reset before headers. reset reason: connection termination"],
  [502, ""],
  [500, '{"ok":false}'],
  [500, '{"ok":false,"error":"<b>boom</b>"}'],
  [500, `{"ok":false,"error":"${"x".repeat(300)}"}`],
  [200, "not json"],
  [200, '{"ok":false,"error":""}'],
];
for (const [status, body] of generic) {
  const err = await failure(status, body);
  eq(err instanceof FreshError, true, `FreshError for ${status} ${body.slice(0, 20)}`);
  eq(errorText(err), FRESH_COPY.error, `generic text for ${status} ${body.slice(0, 30)}`);
}
// A JSON 500 from the route's catch-all is shown (it is the generic copy).
{
  const err = await failure(500, '{"ok":false,"error":"Couldn\'t start fresh. Try again."}');
  eq(errorText(err), "Couldn't start fresh. Try again.", "catch-all JSON");
}
// Network failure (fetch rejects with TypeError) and odd values.
{
  let err = null;
  try {
    await postStartFresh(async () => {
      throw new TypeError("Failed to fetch");
    }, {});
  } catch (e) {
    err = e;
  }
  eq(errorText(err), FRESH_COPY.error, "network error is generic");
  eq(errorText(new Error("<html>")), FRESH_COPY.error, "plain Error is generic");
  eq(errorText(undefined), FRESH_COPY.error, "undefined is generic");
}

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("start_fresh_logic.test.mjs: ok");
