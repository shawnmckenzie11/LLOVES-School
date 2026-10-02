/**
 * MCK-112: one "Students work [Individual | Group]" control for every
 * group-capable item in Run Live Class (spec:
 * group-activity-setup-unified-ia-v0.md, copy: Wonder v1).
 *
 * The control sits on each item's Publish row, just left of Publish.
 * Picking Group adds one options row naming how that type runs as a
 * group. The type decides the style; the teacher only picks Individual
 * or Group. Anything that does not apply is not rendered (hidden, never
 * disabled). With no teams there is no control, only a short reason and
 * a Set up teams button.
 *
 * Pure: no DOM, no fetch, so the node harness can import it.
 */

/** Final teacher-facing copy (Wonder v1). Do not add helper text. */
export const GROUP_SETUP_COPY = Object.freeze({
  legend: "Students work",
  individual: "Individual",
  group: "Group",
  styleLine: Object.freeze({
    submit: "Each group sends one answer. Anyone in the group can send it.",
    consensus: "Everyone answers, then each group agrees on one.",
    shared_board: "Each group shares one board.",
    shared_view: "Each group shares one view.",
  }),
  groupQ: "Wait until teammates match",
  studentsSeeNothing: "Students see nothing until you publish.",
  noTeams: "Group work needs teams first.",
  setUpTeams: "Set up teams",
  liveChip: Object.freeze({
    submit: "● Group · one answer each",
    consensus: "● Group · agree on one",
    shared_board: "● Group · shared board",
    shared_view: "● Group · shared view",
    individual: "● Individual",
  }),
  closedGroup: "Group · closed",
  closedIndividual: "Individual · closed",
  saveFailed: "Couldn't save that. Try again.",
  confirmText:
    "Students can't see teams yet. Publishing as a group will show team names to the class.",
  confirmYes: "Show teams and publish",
  confirmCancel: "Cancel",
});

/**
 * D2 (open for Shawn): rank used to default to Group whenever teams
 * existed. The spec default is Individual like every other type. Flip
 * this to restore the old rank default.
 */
export const RANK_DEFAULTS_TO_GROUP = false;

/** Item ids that never run as a group (Meet chain, Meet team). */
const NOT_GROUP_IDS = new Set(["meet-team", "meet-a", "meet-b", "meet-c"]);

/**
 * Question types that run as a consensus group. Must match the server's
 * open-response check (``SchoolDB._question_is_open_response``) so the
 * control never offers a Group the server would reject (MCK-112 gate).
 */
const CONSENSUS_TYPES = new Set(["numeric", "text", "open", "share", "poll"]);

/** Artifact question cards: Group lives on the Media row (Group Q), never here. */
const ARTIFACT_TYPE = "artifact";

/**
 * Group style for one item, or null when it is not group-capable.
 *
 * @param {any} item Question payload (``type``, ``id``, ``publish_modes``)
 *   or a surface descriptor ``{surface: "canvas"|"media"|"slides", artifact?: boolean}``.
 * @param {any} [card] Lifecycle/card row, read as a fallback.
 * @returns {"submit"|"consensus"|"shared"|null}
 */
export function groupStyleFor(item, card) {
  const surface = String(item?.surface || "").toLowerCase();
  if (surface) {
    if (surface === "canvas" || surface === "whiteboard") return "shared";
    if (surface === "media") return item?.artifact ? "shared" : null;
    return null;
  }
  const id = String(item?.id || card?.item_id || card?.id || "")
    .toLowerCase()
    .replace(/_/g, "-");
  if (NOT_GROUP_IDS.has(id)) return null;
  const type = String(item?.type || card?.type || item?.kind || "").toLowerCase();
  if (type === ARTIFACT_TYPE || item?.artifact_id || card?.item?.artifact_id) return null;
  if (type === "mc" || type === "rank") return "submit";
  if (CONSENSUS_TYPES.has(type) || Boolean(item?.integer_only || card?.integer_only)) {
    return "consensus";
  }
  const modes = Array.isArray(item?.publish_modes)
    ? item.publish_modes
    : Array.isArray(item?.capabilities?.publish_modes)
      ? item.capabilities.publish_modes
      : [];
  if (modes.includes("group_consensus")) return "consensus";
  return null;
}

/**
 * Stored publish/response token for a style.
 * @param {"submit"|"consensus"|"shared"|null} style
 * @returns {string}
 */
export function groupModeToken(style) {
  if (style === "submit") return "group_submit";
  if (style === "consensus") return "group_consensus";
  if (style === "shared") return "group_shared";
  return "individual";
}

/**
 * True when a stored token is a group mode.
 * @param {unknown} token
 * @returns {boolean}
 */
export function isGroupModeToken(token) {
  const t = String(token || "");
  return t === "group_submit" || t === "group_consensus" || t === "group_shared";
}

/**
 * Publish token for one item given the teacher's Individual/Group pick.
 * With no teams it is always Individual, so a stale Group never fails.
 * @param {"submit"|"consensus"|"shared"|null} style
 * @param {"individual"|"group"} pick
 * @param {boolean} teamsReady
 * @returns {string}
 */
export function groupPublishToken(style, pick, teamsReady) {
  if (!style || !teamsReady || pick !== "group") return "individual";
  return groupModeToken(style);
}

/**
 * True when publishing this token needs class-wide Run as Group
 * ("Show teams to students") on the server.
 * @param {string} token
 * @param {{surface?: string}} [opts]
 * @returns {boolean}
 */
export function groupTokenNeedsTeamsShown(token, opts = {}) {
  const t = String(token || "");
  if (t === "group_submit" || t === "group_consensus") return true;
  // Artifact media Group (Group Q) only works with teams shown. The
  // whiteboard shares ink over locked teams without it.
  return t === "group_shared" && String(opts.surface || "") === "media";
}

/**
 * MCK-112 follow-up: what turning "Wait until teammates match" (Group Q)
 * on or off may do right now. Group Q only makes sense while artifact
 * Media runs as Group, so it must never reach students while they are on
 * Individual (that was "Individual + match teammates" before the teacher
 * answered "Show teams and publish").
 *
 * - ``patch``: write it now (turning it off, or Group is already live).
 * - ``switch``: switch live Media to Group first (asking "Show teams and
 *   publish" when teams are hidden), then write it. Cancel writes nothing.
 * - ``hold``: Media is not published yet; keep it with the held Group pick
 *   and write it at Publish.
 * - ``refuse``: no teams (no Group to wait on), or Media is closed.
 *
 * @param {{on: boolean, mediaView?: string, mediaStatus?: string, teamsReady?: boolean}} opts
 *   ``mediaView`` is ``student_view.media``; ``mediaStatus`` the Media row status.
 * @returns {"patch"|"switch"|"hold"|"refuse"}
 */
export function artifactGroupQPlan(opts) {
  if (!opts?.on) return "patch";
  if (String(opts.mediaView || "") === "team") return "patch";
  const status = String(opts.mediaStatus || "").toLowerCase();
  if (!opts.teamsReady || status === "closed") return "refuse";
  if (status === "inactive") return "hold";
  return "switch";
}

/**
 * Publish token the Media lifecycle row records for a Students work pick,
 * so the row shows the mode Media is really running in (MCK-112 follow-up).
 * @param {"individual"|"group"} pick
 * @returns {"group_shared"|"individual"}
 */
export function mediaRowPublishMode(pick) {
  return pick === "group" ? "group_shared" : "individual";
}

/**
 * Group Q to send with a (re-)mint. The teacher's current setting on the
 * live artifact wins over the iframe's mirror box (which can be stale
 * right after the iframe remounts), and it is only on while Media runs as
 * Group (MCK-112 follow-up).
 * @param {{mediaView?: string, storedGroupQ?: unknown, frameGroupQ?: unknown}} opts
 * @returns {boolean}
 */
export function mintGroupQ(opts) {
  if (String(opts?.mediaView || "") !== "team") return false;
  if (typeof opts.storedGroupQ === "boolean") return opts.storedGroupQ;
  return Boolean(opts.frameGroupQ);
}

/**
 * Escape text for HTML.
 * @param {unknown} value
 * @returns {string}
 */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Copy key for a style (shared splits board vs view).
 * @param {"submit"|"consensus"|"shared"} style
 * @param {string} [surface]
 * @returns {"submit"|"consensus"|"shared_board"|"shared_view"}
 */
function styleCopyKey(style, surface) {
  if (style === "shared") return String(surface || "") === "media" ? "shared_view" : "shared_board";
  return style;
}

/**
 * Radio group name for one key (``q:12`` → ``group-setup-12``,
 * ``s:canvas`` → ``group-setup-surface-canvas``).
 * @param {string} key
 * @returns {string}
 */
export function groupSetupRadioName(key) {
  const [scope, id] = String(key || "").split(":");
  return scope === "s" ? `group-setup-surface-${id}` : `group-setup-${id}`;
}

/**
 * The "Students work" control, the locked chip, or the no-teams line.
 *
 * @param {{
 *   key: string,
 *   style: "submit"|"consensus"|"shared"|null,
 *   status?: string,
 *   mode: "individual"|"group",
 *   teamsReady: boolean,
 *   surface?: string,
 *   liveSwitch?: boolean,
 * }} opts ``mode`` is the pick (or, when live/closed, what it ran as).
 *   ``liveSwitch`` keeps the Individual/Group choice on a live item that
 *   can switch in place (artifact Media: publish, then mint).
 * @returns {string} Empty when the item is not group-capable.
 */
export function groupSetupHtml(opts) {
  const style = opts?.style || null;
  if (!style) return "";
  const C = GROUP_SETUP_COPY;
  const key = String(opts.key || "");
  const status = String(opts.status || "inactive").toLowerCase();
  const group = opts.mode === "group";
  const liveSwitch = status === "active" && Boolean(opts.liveSwitch) && Boolean(opts.teamsReady);
  if (status === "active" && !liveSwitch) {
    const text = group ? C.liveChip[styleCopyKey(style, opts.surface)] : C.liveChip.individual;
    return `<span class="live-group-setup-chip is-live" role="status" data-group-setup-chip="${esc(key)}">${esc(text)}</span>`;
  }
  if (status === "closed") {
    return `<span class="live-group-setup-chip is-closed" role="status" data-group-setup-chip="${esc(key)}">${esc(
      group ? C.closedGroup : C.closedIndividual
    )}</span>`;
  }
  if (!opts.teamsReady) {
    return `<span class="live-group-setup-noteams" data-group-setup-noteams="${esc(key)}">${esc(C.noTeams)} <button type="button" class="secondary live-q-btn" data-group-setup-teams="${esc(key)}">${esc(C.setUpTeams)}</button></span>`;
  }
  const name = esc(groupSetupRadioName(key));
  const choice = (value, label) => {
    const on = (value === "group") === group;
    return `<label class="live-group-setup-choice${on ? " is-on" : ""}"><input type="radio" name="${name}" value="${value}" data-group-setup="${esc(key)}"${on ? " checked" : ""}><span>${esc(label)}</span></label>`;
  };
  const live = liveSwitch ? ' data-group-setup-live="1"' : "";
  return `<fieldset class="live-group-setup" data-group-setup-key="${esc(key)}" data-group-token="${esc(groupModeToken(style))}"${live}><legend>${esc(C.legend)}</legend><span class="live-group-setup-seg">${choice("individual", C.individual)}${choice("group", C.group)}</span></fieldset>`;
}

/**
 * The options row under the control. Only rendered while Group is picked
 * on an inactive item with teams.
 *
 * @param {{
 *   key: string,
 *   style: "submit"|"consensus"|"shared"|null,
 *   status?: string,
 *   mode: "individual"|"group",
 *   teamsReady: boolean,
 *   teamNames?: string[],
 *   surface?: string,
 *   groupQ?: boolean|null,
 * }} opts ``groupQ`` non-null renders the artifact "Wait until teammates
 *   match" checkbox (media with an artifact only).
 * @returns {string}
 */
export function groupSetupOptionsHtml(opts) {
  const style = opts?.style || null;
  const status = String(opts?.status || "inactive").toLowerCase();
  const artifactLive = opts?.groupQ != null && status !== "closed";
  if (!style || !opts.teamsReady || opts.mode !== "group") return "";
  if (status !== "inactive" && !artifactLive) return "";
  const C = GROUP_SETUP_COPY;
  const key = String(opts.key || "");
  const names = (Array.isArray(opts.teamNames) ? opts.teamNames : [])
    .map((n) => String(n || "").trim())
    .filter(Boolean);
  const teamsLine = names.length
    ? `<p class="live-group-setup-teams">${esc(`${names.length} teams: ${names.join(", ")}.`)} ${esc(C.studentsSeeNothing)}</p>`
    : `<p class="live-group-setup-teams">${esc(C.studentsSeeNothing)}</p>`;
  const groupQ =
    opts.groupQ == null
      ? ""
      : `<label class="live-group-setup-groupq"><input type="checkbox" data-group-setup-groupq="${esc(key)}"${opts.groupQ ? " checked" : ""}><span>${esc(C.groupQ)}</span></label>`;
  return `<div class="live-group-setup-opts" aria-live="polite" data-group-setup-opts="${esc(key)}"><p class="live-group-setup-style">${esc(
    C.styleLine[styleCopyKey(style, opts.surface)]
  )}</p>${status === "inactive" ? teamsLine : ""}${groupQ}</div>`;
}

/**
 * In-place confirm that replaces Publish while Run as Group is off.
 * @param {string} key
 * @returns {string}
 */
export function groupPublishConfirmHtml(key) {
  const C = GROUP_SETUP_COPY;
  return `<span class="live-group-setup-confirm" role="group" aria-label="${esc(C.confirmYes)}" data-group-confirm="${esc(key)}">
      <span class="live-group-setup-confirm-text">${esc(C.confirmText)}</span>
      <button type="button" class="live-q-btn" data-group-confirm-yes="${esc(key)}">${esc(C.confirmYes)}</button>
      <button type="button" class="secondary live-q-btn" data-group-confirm-cancel="${esc(key)}">${esc(C.confirmCancel)}</button>
    </span>`;
}
