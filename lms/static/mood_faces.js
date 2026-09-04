/**
 * Mood face next to a roster name on staff lists.
 * @param {string} name
 * @param {string|null|undefined} mood
 * @returns {string} HTML
 */
export function nameWithMood(name, mood) {
  const label = String(name || "").trim();
  const face = moodGlyph(mood);
  if (!face) return escapeHtml(label);
  return `<span class="mood-next-name" title="${escapeHtml(moodLabel(mood))}">${face} ${escapeHtml(label)}</span>`;
}

/**
 * Compact face for a stored mood key.
 * @param {string|null|undefined} mood
 * @returns {string}
 */
export function moodGlyph(mood) {
  if (mood === "good") return "😊";
  if (mood === "ok") return "😐";
  if (mood === "low") return "😞";
  return "";
}

/**
 * @param {string|null|undefined} mood
 * @returns {string}
 */
export function moodLabel(mood) {
  if (mood === "good") return "Good";
  if (mood === "ok") return "Okay";
  if (mood === "low") return "Not great";
  return "";
}

/**
 * Escape text for HTML.
 * @param {unknown} value
 * @returns {string}
 */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
