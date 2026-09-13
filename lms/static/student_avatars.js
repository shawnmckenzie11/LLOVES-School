/**
 * Join-screen avatar glyphs next to student names (not mood faces).
 */

const STUDENT_AVATAR_EMOJI = {
  fox: "🦊",
  panda: "🐼",
  unicorn: "🦄",
  octopus: "🐙",
  dragon: "🐲",
  owl: "🦉",
};

/**
 * Emoji for a stored character key, or empty when unset.
 * @param {string|null|undefined} character
 * @returns {string}
 */
export function avatarGlyph(character) {
  const key = String(character || "").trim().toLowerCase();
  return STUDENT_AVATAR_EMOJI[key] || "";
}

/**
 * Avatar to the left of an escaped codename. No mood glyph.
 * @param {string} name
 * @param {string|null|undefined} character
 * @returns {string}
 */
export function nameWithAvatar(name, character) {
  const label = escapeHtml(String(name || "").trim() || "Student");
  const face = avatarGlyph(character);
  if (!face) return label;
  return `<span class="name-with-avatar"><span class="student-avatar" aria-hidden="true">${face}</span><span class="student-avatar-name">${label}</span></span>`;
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
