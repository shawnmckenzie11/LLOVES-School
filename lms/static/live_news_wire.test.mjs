/**
 * LiveNewsWire client rules: stale seq drops, busy does not reload,
 * the fallback poll is slow, and an unchanged Artifact stays mounted.
 */
import {
  CATCHING_UP_COPY,
  FALLBACK_POLL_MS,
  isStaleNews,
  newsArtifactAction,
  newsKeepsArtifact,
  newsPaintsBoardInPlace,
  newsSkipsReload,
  newsWantsLightFetch,
} from "./live_news_wire.js";
import { artifactMountAction, mediaMountKey } from "./live_poll_feel.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (FALLBACK_POLL_MS < 15000 || FALLBACK_POLL_MS > 30000) {
  fail(`fallback poll must sit between 15s and 30s, got ${FALLBACK_POLL_MS}`);
}
if (CATCHING_UP_COPY !== "Catching up…") {
  fail("news fetch copy");
}

if (!isStaleNews({ type: "stage", state_seq: 3 }, 5)) {
  fail("older state_seq must drop");
}
if (isStaleNews({ type: "response_landed", state_seq: 5, scope: "class" }, 5)) {
  fail("same seq still paints a landed response");
}
if (isStaleNews({ type: "ping" }, 9)) {
  fail("ping has no seq to go stale");
}

const busy = { type: "busy", retry: true };
if (!newsSkipsReload(busy) || newsWantsLightFetch(busy)) {
  fail("busy must not fetch or reload");
}
if (newsWantsLightFetch({ type: "ping" }) || newsWantsLightFetch({ type: "hello" })) {
  fail("ping and hello are not fetches");
}
if (!newsWantsLightFetch({ type: "stage", state_seq: 2 })) {
  fail("stage is a light fetch");
}
if (!newsPaintsBoardInPlace({ type: "response_landed" })) {
  fail("response_landed paints the board in place");
}
if (!newsPaintsBoardInPlace({ type: "flag_work" })) {
  fail("flag_work paints the board in place");
}
if (newsPaintsBoardInPlace({ type: "stage" })) {
  fail("stage is not a submitter-log paint");
}

if (!newsKeepsArtifact({ type: "active_media", media_version: 4, artifact_id: "transforms" }, 4, "transforms")) {
  fail("same media version keeps the Artifact");
}
if (newsKeepsArtifact({ type: "active_media", media_version: 5, artifact_id: "transforms" }, 4, "transforms")) {
  fail("version bump may remount");
}
if (newsArtifactAction("mounted", busy) !== "keep") {
  fail("busy must not touch the Artifact iframe");
}

const mounted = mediaMountKey(
  { url: "/static/live-media/m1c2-transforms.html", media_version: "2" },
  "teacher"
);
const same = newsArtifactAction(
  mounted,
  { type: "active_media", media_version: "2", artifact_id: null },
  "teacher"
);
if (same === "clear") {
  fail("news must not clear an Artifact");
}
if (artifactMountAction(mounted, mounted) !== "keep") {
  fail("poll lock still keeps a stable mount");
}

console.log("ok");
