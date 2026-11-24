/**
 * Teacher class-list drawer labels (MCK-500, MCK-503).
 *
 * The live shell starts collapsed. One click opens an overlay drawer.
 * The choice is stored per teacher so a slide change does not reset it.
 */

export const CLASS_LIST_STORAGE_PREFIX = "lloves-class-list:";

/**
 * localStorage key for one teacher's open/closed choice.
 *
 * @param {string|number} teacherId Staff user id. Empty when the page has none.
 * @returns {string}
 */
export function classListStorageKey(teacherId) {
  return CLASS_LIST_STORAGE_PREFIX + String(teacherId || "");
}

/**
 * Button label. Collapsed shows how many students are present.
 *
 * @param {boolean} open
 * @param {number} presentCount
 * @returns {string}
 */
export function classListToggleLabel(open, presentCount) {
  const count = Number(presentCount);
  const n = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  return `Class (${n})`;
}

/**
 * Tooltip for the class-list control.
 *
 * @param {boolean} open
 * @returns {string}
 */
export function classListToggleTitle(open) {
  return open ? "Hide class list" : "Show class list";
}

/**
 * Accessible name for the toggle and close controls.
 *
 * @param {boolean} open
 * @returns {string}
 */
export function classListToggleAriaLabel(open) {
  return classListToggleTitle(open);
}

/**
 * Read the stored choice. Missing or unreadable storage stays collapsed.
 *
 * @param {Storage|null|undefined} storage
 * @param {string|number} teacherId
 * @returns {boolean}
 */
export function classListStoredOpen(storage, teacherId) {
  try {
    return Boolean(storage && storage.getItem(classListStorageKey(teacherId)) === "open");
  } catch (_err) {
    return false;
  }
}
