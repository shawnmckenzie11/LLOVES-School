/**
 * Registry flags the server painted for this page.
 *
 * Student pages carry the live session owner's flags. Staff pages carry
 * the signed-in teacher's flags. A missing or unreadable payload is off
 * for every key, so a disabled feature does not mount.
 */

/**
 * Read ``#lloves-features``. Booleans only. Unknown keys are off.
 * @returns {Record<string, boolean>}
 */
export function teacherFeatures() {
  const node = document.getElementById("lloves-features");
  if (!(node instanceof HTMLElement)) return {};
  try {
    const data = JSON.parse(node.textContent || "{}");
    if (!data || typeof data !== "object") return {};
    /** @type {Record<string, boolean>} */
    const flags = {};
    for (const [key, value] of Object.entries(data)) {
      flags[key] = value === true;
    }
    return flags;
  } catch (_err) {
    return {};
  }
}

/**
 * True when this page's teacher has ``key``.
 * @param {string} key Feature registry key.
 * @returns {boolean}
 */
export function featureOn(key) {
  return teacherFeatures()[key] === true;
}
