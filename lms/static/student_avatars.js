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
 * MCK-116 Celebrations reward avatars. Same keys as EARNED_CHARACTERS (db.py)
 * and EARNED_AVATARS (student_portal.py). Art: /static/avatars/earned/<key>.svg.
 */
export const EARNED_AVATAR_KEYS = Object.freeze([
  "fox_scarf",
  "owl_glasses",
  "penguin_beanie",
  "frog_crown",
  "panda_leaf_crown",
  "unicorn_ribbon",
  "octopus_star",
  "dragon_lantern",
  "cat_bow_tie",
  "bear_medal",
  "rabbit_headphones",
  "hedgehog_acorn",
  "koala_pencil",
  "lion_cub_laurel",
  "turtle_star_shell",
  "axolotl_seashell",
  "raccoon_backpack",
  "whale_star_spout",
  "phoenix_chick_spark",
  "narwhal_star_horn",
]);

const EARNED_AVATAR_SET = new Set(EARNED_AVATAR_KEYS);

/**
 * Normalise a stored character key.
 * @param {string|null|undefined} character
 * @returns {string}
 */
function avatarKey(character) {
  return String(character || "").trim().toLowerCase();
}

/**
 * Art path for an earned avatar key, or empty when the key is not one.
 * @param {string|null|undefined} character
 * @returns {string}
 */
export function earnedAvatarSrc(character) {
  const key = avatarKey(character);
  return EARNED_AVATAR_SET.has(key) ? `/static/avatars/earned/${key}.svg` : "";
}

/**
 * Emoji for a stored character key, or empty when unset.
 * @param {string|null|undefined} character
 * @returns {string}
 */
export function avatarGlyph(character) {
  const key = avatarKey(character);
  return STUDENT_AVATAR_EMOJI[key] || "";
}

/**
 * Avatar markup: the emoji, or an <img> for an earned SVG. Empty when unset.
 * Keys are whitelisted, so the src never carries user text.
 * @param {string|null|undefined} character
 * @returns {string}
 */
export function avatarMarkup(character) {
  const src = earnedAvatarSrc(character);
  if (src) return `<img class="avatar-img" src="${src}" alt="">`;
  return avatarGlyph(character);
}

/**
 * Paint one avatar into an element with DOM calls (no innerHTML).
 * @param {HTMLElement} el
 * @param {string|null|undefined} character
 * @returns {boolean} True when something was painted.
 */
export function paintAvatar(el, character) {
  const src = earnedAvatarSrc(character);
  if (src) {
    const current = el.firstElementChild;
    if (el.childNodes.length === 1 && current && current.getAttribute("src") === src) return true;
    const img = el.ownerDocument.createElement("img");
    img.className = "avatar-img";
    img.src = src;
    img.alt = "";
    el.replaceChildren(img);
    return true;
  }
  const face = avatarGlyph(character);
  el.textContent = face;
  return Boolean(face);
}

/**
 * Avatar to the left of an escaped codename. No mood glyph.
 * @param {string} name
 * @param {string|null|undefined} character
 * @returns {string}
 */
export function nameWithAvatar(name, character) {
  const label = escapeHtml(String(name || "").trim() || "Student");
  const face = avatarMarkup(character);
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
