/**
 * Starting values for a Run Live Class card from a bank question.
 *
 * The bank may store Individual or Group, whether results start visible,
 * and the award amount. The award dialog already read ``live_points``.
 * Response mode and results visibility use the same stored fields while
 * the card is still inactive and nobody has edited the lifecycle row
 * (``created_at`` still equals ``updated_at``, or the row does not exist
 * yet). A teacher hold, an active or closed card, or a row whose
 * ``updated_at`` has moved keeps the lifecycle value.
 */

/**
 * @param {unknown} value
 * @returns {boolean}
 */
function flagOn(value) {
  return !(value === false || value === 0 || value === "0" || value === "false");
}

/**
 * @param {Record<string, unknown>|null|undefined} item
 * @param {Record<string, unknown>|null|undefined} card
 * @param {{modeHeld?: boolean, showHeld?: boolean}} [holds]
 * @returns {{response_mode: string, publish_mode: string, show_live_results: boolean, live_points: number|null}}
 */
export function bankLiveStart(item, card, holds = {}) {
  const source = item && typeof item === "object" ? item : {};
  const row = card && typeof card === "object" ? card : {};
  const status = String(row.status || "inactive").toLowerCase();
  const inactive = status !== "active" && status !== "closed";
  const created = String(row.created_at || "");
  const updated = String(row.updated_at || "");
  const untouched = !created || !updated || created === updated;
  const canSeed = inactive && untouched;
  let response = String(row.response_mode || source.response_mode || "individual");
  let publish = String(row.publish_mode || response || "individual");
  let show = flagOn(row.show_live_results);
  const mode = String(source.live_response_mode ?? row.live_response_mode ?? "")
    .trim()
    .toLowerCase();
  const showDefault = source.live_show_results ?? row.live_show_results;
  if (canSeed && !holds.modeHeld) {
    if (mode === "group") {
      response = "group_submit";
      publish = "group_submit";
    } else if (mode === "individual") {
      response = "individual";
      publish = "individual";
    }
  }
  if (canSeed && !holds.showHeld && showDefault != null && showDefault !== "") {
    show = flagOn(showDefault);
  }
  const rawPoints = source.live_points ?? row.live_points;
  const points = rawPoints == null || rawPoints === "" ? null : Number(rawPoints);
  return {
    response_mode: response,
    publish_mode: publish,
    show_live_results: show,
    live_points: Number.isFinite(points) ? points : null,
  };
}
