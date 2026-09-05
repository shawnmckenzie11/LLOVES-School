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
  const key = String(mood || "").trim();
  const map = {
    good: "😊",
    ok: "😐",
    low: "😞",
    tired: "😴",
    energetic: "⚡",
    focused: "🎯",
    anxious: "😰",
    confused: "😕",
    excited: "🤩",
  };
  return map[key] || "";
}

/**
 * @param {string|null|undefined} mood
 * @returns {string}
 */
export function moodLabel(mood) {
  const key = String(mood || "").trim();
  const map = {
    good: "Good",
    ok: "Okay",
    low: "Not great",
    tired: "Tired",
    energetic: "Energetic",
    focused: "Focused",
    anxious: "Anxious",
    confused: "Confused",
    excited: "Excited",
  };
  return map[key] || "";
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
