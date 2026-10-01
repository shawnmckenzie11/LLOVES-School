/**
 * Copy and guards for the Set Class live-deck chips.
 *
 * The painter in staff_ap.js owns the DOM. These functions stay pure so
 * the helper line, the empty-course chip, and a late server reply can be
 * checked without a browser.
 */

/**
 * Short label for the previous challenge, such as ``M1 C2``.
 *
 * @param {{previousLabel?: string, previousSlot?: string, previousModule?: string}} state
 * @returns {string}
 */
function previousChallengeLabel(state) {
  const label = String(state.previousLabel || "").trim();
  if (label) return label;
  const moduleName = String(state.previousModule || "").trim().toUpperCase();
  const slot = String(state.previousSlot || "").trim().toUpperCase();
  if (moduleName && slot) return `${moduleName} ${slot}`;
  return "";
}

/**
 * Helper line under the Set Class live-deck chips.
 *
 * ``M1 C2`` in the copy is the previous challenge label. Loading and
 * error win. A mode that can actually run is described next. An empty
 * course list is described before a greyed Previous chip, so a normal
 * C1 (other decks exist) still explains Previous, and a course with no
 * other decks explains Course deck.
 *
 * @param {{
 *   phase?: string,
 *   mode?: string,
 *   previousAvailable?: boolean,
 *   previousLabel?: string,
 *   previousSlot?: string,
 *   previousModule?: string,
 *   courseDeckCount?: number,
 * }} [state]
 * @returns {string}
 */
export function deckSeedHelpText(state = {}) {
  const phase = String(state.phase || "ready");
  if (phase === "loading") return "Loading decks…";
  if (phase === "error") return "Couldn't load decks. Try again.";

  const mode = String(state.mode || "current");
  const previousAvailable = Boolean(state.previousAvailable);
  const currentAvailable = Boolean(state.currentAvailable);
  const courseDisabled = courseDeckChoiceDisabled(state.courseDeckCount);
  const label = previousChallengeLabel(state);

  if (mode === "current") {
    return currentAvailable
      ? "Keeps the deck already set for this class."
      : "No deck set yet.";
  }
  if (mode === "previous" && previousAvailable) {
    return label
      ? `Starts from a copy of your ${label} deck.`
      : "Starts from a copy of your previous deck.";
  }
  if (mode === "course" && !courseDisabled) {
    return "Starts from a copy of the deck you pick.";
  }
  if (courseDisabled) return "No other decks in this course yet.";
  if (!previousAvailable) {
    if (String(state.previousSlot || "").trim()) {
      const name = label || "The previous challenge";
      return `${name} has no saved deck yet. Starting blank.`;
    }
    return "No earlier challenge in this module. Starting blank.";
  }
  if (mode === "blank") return "Starts from a blank 7-page deck.";
  return "Keeps the deck already set for this class.";
}

/**
 * Pick the chip that should be selected after options load.
 *
 * Use current stays selected when this slot already has a real deck.
 * Otherwise Previous is the fallback when that chip can run. When
 * Previous is unavailable and Course deck can run, Course deck is
 * selected so the helper describes the chip that is actually on.
 *
 * @param {{mode?: string, currentAvailable?: boolean, previousAvailable?: boolean, courseDeckCount?: number}} state
 * @returns {"current"|"previous"|"course"}
 */
export function deckSeedDefaultMode(state = {}) {
  const currentOk = Boolean(state.currentAvailable);
  const previousOk = Boolean(state.previousAvailable);
  const courseOk = !courseDeckChoiceDisabled(state.courseDeckCount);
  const requested = String(state.mode || "current");
  if (requested === "current" && currentOk) return "current";
  if (requested === "previous" && previousOk) return "previous";
  if (requested === "course" && courseOk) return "course";
  if (currentOk) return "current";
  if (!previousOk && courseOk) return "course";
  return "previous";
}

/**
 * Whether the Course deck chip should be greyed out.
 *
 * Next fails with "Choose a deck from this course." when that mode is
 * selected and the list is empty. Wonder agreed to disable the chip.
 * Shawn's final OK is still pending: make this return false to revert
 * without touching the picker layout.
 *
 * @param {number} [deckCount] How many other decks the course list returned.
 * @returns {boolean}
 */
export function courseDeckChoiceDisabled(deckCount) {
  return !(Number(deckCount) > 0);
}

/**
 * Whether a deck-seed response still matches the Set Class selection.
 *
 * A late reply for another class or challenge must not paint Previous.
 * Module and slot compare case-insensitively. A missing class, module,
 * or slot does not match.
 *
 * @param {{classId?: number|string, class_id?: number|string, module?: string, slot?: string}|null} response
 * @param {{classId?: number|string, class_id?: number|string, module?: string, slot?: string}|null} selection
 * @returns {boolean}
 */
export function deckSeedResponseIsCurrent(response, selection) {
  if (!response || !selection) return false;
  const responseClass = response.classId ?? response.class_id;
  const selectionClass = selection.classId ?? selection.class_id;
  if (responseClass == null || selectionClass == null) return false;
  if (String(responseClass) === "" || String(selectionClass) === "") return false;
  if (String(responseClass) !== String(selectionClass)) return false;
  const moduleName = String(response.module || "").trim().toUpperCase();
  const slot = String(response.slot || "").trim().toUpperCase();
  if (!moduleName || !slot) return false;
  return (
    moduleName === String(selection.module || "").trim().toUpperCase() &&
    slot === String(selection.slot || "").trim().toUpperCase()
  );
}
