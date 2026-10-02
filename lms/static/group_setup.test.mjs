/**
 * MCK-112 node checks for the shared "Students work" control.
 */
import {
  GROUP_SETUP_COPY,
  RANK_DEFAULTS_TO_GROUP,
  artifactGroupQPlan,
  groupPublishConfirmHtml,
  groupPublishToken,
  groupSetupHtml,
  groupSetupOptionsHtml,
  groupStyleFor,
  groupTokenNeedsTeamsShown,
  mediaRowPublishMode,
  mintGroupQ,
} from "./group_setup.js";

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL: ${msg}`);
  }
}

// groupStyleFor: the type decides the style.
check(groupStyleFor({ type: "mc" }) === "submit", "mc -> submit");
check(groupStyleFor({ type: "rank" }) === "submit", "rank -> submit");
for (const type of ["numeric", "open", "text", "share", "poll"]) {
  check(groupStyleFor({ type }) === "consensus", `${type} -> consensus`);
}
check(groupStyleFor({ id: "meet-a", type: "mc" }) === null, "meet-a -> null");
check(groupStyleFor({ id: "meet_team", type: "numeric" }) === null, "meet-team -> null");
check(groupStyleFor({ type: "artifact" }) === null, "artifact question w/o group_consensus -> null");
// Gate LOW: artifact cards never get a question-level Group (no "Individual
// in Group"); their Group is the Media row's Group Q.
check(
  groupStyleFor({ type: "artifact", publish_modes: ["individual", "group_consensus"] }) === null,
  "artifact question with catalogue group_consensus -> still null"
);
check(groupStyleFor({ type: "mc", artifact_id: "m1c2-transformations" }) === null, "artifact_id -> null");
check(
  groupStyleFor({ type: "estimate", publish_modes: ["individual", "group_consensus"] }) === "consensus",
  "catalogue group_consensus -> consensus"
);
// Gate LOW: "why" is not a server consensus type, so no Group offer.
check(groupStyleFor({ type: "why" }) === null, "why -> null (server rejects group_consensus)");
check(groupStyleFor({ surface: "canvas" }) === "shared", "whiteboard -> shared");
check(groupStyleFor({ surface: "media" }) === null, "plain media -> null");
check(groupStyleFor({ surface: "media", artifact: true }) === "shared", "artifact media -> shared");
check(groupStyleFor({ surface: "slides" }) === null, "slides -> null");
check(RANK_DEFAULTS_TO_GROUP === false, "D2 default: rank defaults to Individual");

// Publish token.
check(groupPublishToken("submit", "group", true) === "group_submit", "mc group token");
check(groupPublishToken("consensus", "group", true) === "group_consensus", "open group token");
check(groupPublishToken("submit", "group", false) === "individual", "no teams -> individual");
check(groupPublishToken(null, "group", true) === "individual", "no style -> individual");
check(groupTokenNeedsTeamsShown("group_submit"), "group_submit needs teams shown");
check(groupTokenNeedsTeamsShown("group_consensus"), "group_consensus needs teams shown");
check(!groupTokenNeedsTeamsShown("group_shared", { surface: "canvas" }), "whiteboard does not");
check(groupTokenNeedsTeamsShown("group_shared", { surface: "media" }), "artifact media does");

// No teams: no radio, a Set up teams button.
const noTeams = groupSetupHtml({ key: "q:7", style: "submit", status: "inactive", mode: "group", teamsReady: false });
check(!noTeams.includes('type="radio"'), "no teams -> no radio");
check(!noTeams.includes("disabled"), "no teams -> nothing greyed");
check(noTeams.includes('data-group-setup-teams="q:7"'), "no teams -> Set up teams button");
check(noTeams.includes(GROUP_SETUP_COPY.noTeams), "no teams line copy");

// Teams ready: two radios, one name per item, Group checked.
const ready = groupSetupHtml({ key: "q:7", style: "submit", status: "inactive", mode: "group", teamsReady: true });
check((ready.match(/type="radio"/g) || []).length === 2, "two radios");
check(ready.includes('name="group-setup-7"'), "radio name per item");
check(/value="group"[^>]*checked/.test(ready), "Group checked");
check(ready.includes("<legend>Students work</legend>"), "legend copy");
check(ready.includes('data-group-token="group_submit"'), "token on fieldset");
const surface = groupSetupHtml({ key: "s:canvas", style: "shared", status: "inactive", mode: "individual", teamsReady: true });
check(surface.includes('name="group-setup-surface-canvas"'), "surface radio name");

// Not group-capable: nothing.
check(groupSetupHtml({ key: "q:8", style: null, status: "inactive", mode: "individual", teamsReady: true }) === "", "null style -> empty");

// Live artifact Media keeps the switch (publish, then mint).
const liveArt = groupSetupHtml({ key: "s:media", style: "shared", status: "active", mode: "individual", teamsReady: true, surface: "media", liveSwitch: true });
check((liveArt.match(/type="radio"/g) || []).length === 2 && liveArt.includes('data-group-setup-live="1"') && !liveArt.includes("● Individual"), "live artifact media keeps radios");
check(groupSetupHtml({ key: "s:media", style: "shared", status: "active", mode: "individual", teamsReady: false, surface: "media", liveSwitch: true }).includes("● Individual"), "live switch needs teams");

// Live / closed chips.
const live = groupSetupHtml({ key: "q:7", style: "consensus", status: "active", mode: "group", teamsReady: true });
check(live.includes("● Group · agree on one") && live.includes('role="status"') && !live.includes("radio"), "live chip");
check(groupSetupHtml({ key: "s:canvas", style: "shared", status: "active", mode: "group", teamsReady: true, surface: "canvas" }).includes("● Group · shared board"), "whiteboard chip");
check(groupSetupHtml({ key: "s:media", style: "shared", status: "active", mode: "group", teamsReady: true, surface: "media" }).includes("● Group · shared view"), "media chip");
check(groupSetupHtml({ key: "q:7", style: "submit", status: "active", mode: "individual", teamsReady: true }).includes("● Individual"), "individual chip");
check(groupSetupHtml({ key: "q:7", style: "submit", status: "closed", mode: "group", teamsReady: true }).includes("Group · closed"), "closed chip");

// Options row: only on Group, names the style, lists teams.
const opts = groupSetupOptionsHtml({ key: "q:7", style: "submit", status: "inactive", mode: "group", teamsReady: true, teamNames: ["Team 1 · Tangents", "Team 2 · Secants"] });
check(opts.includes(GROUP_SETUP_COPY.styleLine.submit), "submit style line");
check(opts.includes("2 teams: Team 1 · Tangents, Team 2 · Secants."), "teams line");
check(opts.includes("Students see nothing until you publish."), "students see nothing");
check(opts.includes('aria-live="polite"'), "options row is polite");
check(!opts.includes("data-group-setup-groupq"), "no Group Q box on questions");
check(groupSetupOptionsHtml({ key: "q:7", style: "submit", status: "inactive", mode: "individual", teamsReady: true }) === "", "Individual hides options row");
check(groupSetupOptionsHtml({ key: "q:7", style: "submit", status: "inactive", mode: "group", teamsReady: false }) === "", "no teams hides options row");
const wb = groupSetupOptionsHtml({ key: "s:canvas", style: "shared", status: "inactive", mode: "group", teamsReady: true, surface: "canvas", groupQ: null });
check(wb.includes("Each group shares one board.") && !wb.includes("groupq"), "whiteboard line, no Group Q");
const art = groupSetupOptionsHtml({ key: "s:media", style: "shared", status: "inactive", mode: "group", teamsReady: true, surface: "media", groupQ: true });
check(art.includes("Each group shares one view.") && /data-group-setup-groupq="s:media"[^>]*checked/.test(art), "artifact Group Q box");
check(art.includes("Wait until teammates match"), "Group Q copy");

// Confirm copy.
const confirm = groupPublishConfirmHtml("q:7");
check(confirm.includes("Show teams and publish") && confirm.includes(">Cancel<"), "confirm buttons");
check(confirm.includes(GROUP_SETUP_COPY.confirmText.replace(/'/g, "&#39;")), "confirm text");

// MCK-112 follow-up 1 (MED): Group Q never reaches students on Individual.
check(artifactGroupQPlan({ on: false, mediaView: "student", mediaStatus: "active", teamsReady: true }) === "patch", "Group Q off always writes");
check(artifactGroupQPlan({ on: true, mediaView: "team", mediaStatus: "active", teamsReady: true }) === "patch", "Group live: Group Q writes now");
check(artifactGroupQPlan({ on: true, mediaView: "student", mediaStatus: "active", teamsReady: true }) === "switch", "live Individual: Group first (confirm), then Group Q");
check(artifactGroupQPlan({ on: true, mediaView: "none", mediaStatus: "inactive", teamsReady: true }) === "hold", "unpublished Media: hold until Publish");
check(artifactGroupQPlan({ on: true, mediaView: "student", mediaStatus: "inactive", teamsReady: true }) === "hold", "minted, Media row unpublished: hold");
check(artifactGroupQPlan({ on: true, mediaView: "student", mediaStatus: "active", teamsReady: false }) === "refuse", "no teams: refuse");
check(artifactGroupQPlan({ on: true, mediaView: "none", mediaStatus: "closed", teamsReady: true }) === "refuse", "closed Media: refuse");

// Follow-up 2 (LOW): the Media row records the mode it runs in.
check(mediaRowPublishMode("group") === "group_shared", "Group -> group_shared row");
check(mediaRowPublishMode("individual") === "individual", "Individual -> individual row");

// Follow-up 3 (LOW): a re-mint keeps the teacher's Group Q.
check(mintGroupQ({ mediaView: "team", storedGroupQ: true, frameGroupQ: false }) === true, "re-mint keeps stored Group Q over a stale iframe box");
check(mintGroupQ({ mediaView: "team", storedGroupQ: false, frameGroupQ: true }) === false, "teacher's stored off wins");
check(mintGroupQ({ mediaView: "team", frameGroupQ: true }) === true, "first mint on Group uses the iframe box");
check(mintGroupQ({ mediaView: "student", storedGroupQ: true, frameGroupQ: true }) === false, "never Individual + match teammates");

if (failures) process.exit(1);
console.log("ok");
