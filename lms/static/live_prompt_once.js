/**
 * One published question is the side-panel card, not also the full-width prompt.
 *
 * Add New publishes a lifecycle card and the phone also used to paint the
 * legacy prompt shell ("NUMERIC · slide …") behind it. The shell stays for
 * a prompt that has no card.
 */

/**
 * Peel a positional catalogue key down to the item id.
 *
 * ``stage:page:order:item`` and ``class:n:staff:token`` both peel when the
 * second piece is a number. Hyphens and underscores match. Engine rides
 * are left whole so two steps of Meet stay two cards.
 *
 * @param {unknown} raw Item id or placement key.
 * @returns {string}
 */
export function livePromptAlias(raw) {
  const token = String(raw || "").trim();
  const parts = token.split(":");
  const peeled =
    parts.length >= 4 && /^\d+$/.test(parts[1]) ? parts.slice(3).join(":") : token;
  return peeled.toLowerCase().replaceAll("-", "_");
}

/**
 * True for rides that keep a prompt shell beside their own card.
 *
 * @param {string} alias Folded item id.
 * @returns {boolean}
 */
function engineRideAlias(alias) {
  return (
    alias === "minds_on" ||
    alias === "teams_spark" ||
    alias === "meet_team" ||
    alias === "meet_a" ||
    alias === "meet_b" ||
    alias === "meet_c" ||
    alias === "team_challenge"
  );
}

/**
 * True when a lifecycle card already shows this prompt.
 *
 * A shared prompt id matches. A shared catalogue id matches too. An
 * engine ride keeps its shell even when the prompt id matches. An idle
 * prompt does not.
 *
 * @param {any} payload Student state, including the legacy ``prompt``.
 * @returns {boolean}
 */
export function cardOwnsLivePrompt(payload) {
  const prompt = payload?.prompt;
  if (!prompt || !prompt.kind || prompt.kind === "idle") return false;
  const promptId = Number(prompt.id) || 0;
  const body = prompt.payload && typeof prompt.payload === "object" ? prompt.payload : {};
  const alias = livePromptAlias(body.item_id || body.pack || body.id || "");
  if (engineRideAlias(alias)) return false;
  const rows = []
    .concat(payload.active_questions || [])
    .concat(payload.closed_results || [])
    .concat(payload.saved_cards || [])
    .concat(payload.live_items || []);
  return rows.some((item) => {
    if (!item || typeof item !== "object") return false;
    const content = item.content && typeof item.content === "object" ? item.content : {};
    const itemAlias = livePromptAlias(
      item.item_id || content.item_id || content.id || ""
    );
    if (engineRideAlias(itemAlias)) return false;
    if (promptId && Number(item.prompt?.id || 0) === promptId) return true;
    return Boolean(alias) && alias === itemAlias;
  });
}
