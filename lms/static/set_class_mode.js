/**
 * Set Class start choice mapped onto the requests the page already sends.
 *
 * The segmented control is UI only. Live, test, and open office still post
 * the same kind, run, and deck fields as the old Session type and Run as
 * toggles.
 */

/**
 * Whether Set Class asks which slide deck.
 *
 * Live class and test class use the same deck prompt. Open office hides it.
 *
 * @param {"live"|"test"|"office"|string} mode
 * @returns {boolean}
 */
export function setClassPromptsForDeck(mode) {
  return mode === "live" || mode === "test";
}

/**
 * Start choices Run Live Class offers (MCK-447 removed Test class).
 * @type {readonly ("live"|"office")[]}
 */
export const SET_CLASS_START_MODES = Object.freeze(["live", "office"]);

/**
 * Map one start choice onto the existing kind and run flags.
 *
 * Live is a live class run live. Test is a live class run as a test
 * (``is_test`` true, deck mode ``test_copy``). Office is the next
 * open-office slot run live (``is_test`` false, deck chips unchanged).
 *
 * @param {"live"|"test"|"office"} mode
 * @returns {{kind: "live"|"office", run: "live"|"test", is_test: boolean, deck: "chip"|"test_copy"}}
 */
export function setClassStartFields(mode) {
  // MCK-447: Run Live Class no longer offers Test class. A stray "test"
  // starts a live class; past test runs can still be deleted server-side.
  if (mode === "test" || !SET_CLASS_START_MODES.includes(mode)) {
    return { kind: "live", run: "live", is_test: false, deck: "chip" };
  }
  if (mode === "office") {
    return { kind: "office", run: "live", is_test: false, deck: "chip" };
  }
  return { kind: "live", run: "live", is_test: false, deck: "chip" };
}

/**
 * The saved class a live start would replace, or ``null``.
 *
 * End keeps one Lesson Slides row and one catch-up link per module and
 * class, so starting a slot that already has a saved real class replaces
 * it. Open office always takes the next unused number, so it never asks.
 *
 * @param {Record<string, string>|null|undefined} saved ``M4C1`` → Toronto time label.
 * @param {{mode?: string, module?: string, slot?: string}} pick
 * @returns {{module: string, slot: string, when: string}|null}
 */
export function savedSlotConflict(saved, pick) {
  if (pick?.mode === "office") return null;
  const module = String(pick?.module || "").trim().toUpperCase();
  const slot = String(pick?.slot || "").trim().toUpperCase();
  if (!module || !slot.startsWith("C") || !saved || typeof saved !== "object") return null;
  const key = `${module}${slot}`;
  if (!Object.prototype.hasOwnProperty.call(saved, key)) return null;
  return { module, slot, when: String(saved[key] || "").trim() };
}

/**
 * Title, body, and Override label for the saved-class prompt.
 *
 * @param {{module: string, slot: string, when: string}} conflict From ``savedSlotConflict``.
 * @returns {{title: string, body: string, override: string}}
 */
export function savedSlotPrompt(conflict) {
  const modNum = conflict.module.replace(/^M/, "") || conflict.module;
  const classNum = conflict.slot.replace(/^C/, "") || conflict.slot;
  const name = `Module ${modNum}, Class ${classNum}`;
  const when = conflict.when ? ` from ${conflict.when}` : "";
  return {
    title: `${name} already has a saved class`,
    body:
      `Lesson Slides has a class${when} saved as ${name}. Starting this one replaces ` +
      "that entry and its catch-up link when you end the class. Pick another class number " +
      "if this is a different lesson.",
    override: `Override ${conflict.module} ${conflict.slot}`,
  };
}

/**
 * Footer line for the Set Class card.
 *
 * Live and test name the module, class, and deck. A test run says
 * "current deck" because it copies that deck. Office names the next open
 * office and leaves the deck clause off.
 *
 * @param {{mode?: string, module?: string, slot?: string, deck?: string, slides?: string}} parts
 * @returns {string} For example ``Starting live class: Module 2, Class 1, current deck.``
 */
export function setClassSummaryLine(parts) {
  // MCK-447: no Test class choice, so no "Starting test class" line.
  const mode = parts?.mode === "office" ? "office" : "live";
  const module = String(parts?.module || "M1").trim().toUpperCase();
  const slot = String(parts?.slot || "C1").trim().toUpperCase();
  const modNum = module.replace(/^M/, "") || module;
  const modLabel = `Module ${modNum}`;
  if (mode === "office") {
    const officeNum = slot.replace(/^O/, "") || slot;
    return `Starting open office: ${modLabel}, Open office ${officeNum}.`;
  }
  const type = "live class";
  const classNum = slot.replace(/^C/, "") || slot;
  const deck = parts?.deck === "previous" || parts?.deck === "course" ? parts.deck : "current";
  const deckPhrase =
    deck === "previous" ? "previous deck" : deck === "course" ? "course deck" : "current deck";
  const slides = String(parts?.slides || "").replace(/\s+/g, " ").trim();
  const slidesClause = slides ? `, ${slides}` : "";
  return `Starting ${type}: ${modLabel}, Class ${classNum}, ${deckPhrase}${slidesClause}.`;
}
