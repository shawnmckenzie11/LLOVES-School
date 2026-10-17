/**
 * Set Class start choice mapped onto the requests the page already sends.
 *
 * The segmented control is UI only. Live, test, and open office still post
 * the same kind, run, and deck fields as the old Session type and Run as
 * toggles.
 */

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
  if (mode === "test") {
    return { kind: "live", run: "test", is_test: true, deck: "test_copy" };
  }
  if (mode === "office") {
    return { kind: "office", run: "live", is_test: false, deck: "chip" };
  }
  return { kind: "live", run: "live", is_test: false, deck: "chip" };
}

/**
 * Footer line for the Set Class card.
 *
 * Live and test name the module, class, and deck. A test run says
 * "current deck" because it copies that deck. Office names the next open
 * office and leaves the deck clause off.
 *
 * @param {{mode?: string, module?: string, slot?: string, deck?: string}} parts
 * @returns {string} For example ``Starting test class: Module 2, Class 1, current deck.``
 */
export function setClassSummaryLine(parts) {
  const mode = parts?.mode === "test" || parts?.mode === "office" ? parts.mode : "live";
  const module = String(parts?.module || "M1").trim().toUpperCase();
  const slot = String(parts?.slot || "C1").trim().toUpperCase();
  const modNum = module.replace(/^M/, "") || module;
  const modLabel = `Module ${modNum}`;
  if (mode === "office") {
    const officeNum = slot.replace(/^O/, "") || slot;
    return `Starting open office: ${modLabel}, Open office ${officeNum}.`;
  }
  const type = mode === "test" ? "test class" : "live class";
  const classNum = slot.replace(/^C/, "") || slot;
  const deck = parts?.deck === "previous" || parts?.deck === "course" ? parts.deck : "current";
  const deckPhrase =
    deck === "previous" ? "previous deck" : deck === "course" ? "course deck" : "current deck";
  return `Starting ${type}: ${modLabel}, Class ${classNum}, ${deckPhrase}.`;
}
