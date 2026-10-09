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
  // MCK-193: a plain course (no pack, no decks, C1) starts blank, calmly.
  if (courseDisabled && !previousAvailable && !String(state.previousSlot || "").trim()) {
    return "No decks yet, and that's fine. Class starts with a blank deck.";
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
 * ``chosen`` says whether ``mode`` came from the teacher clicking a
 * chip. When it is ``false`` the mode was auto-picked for some other
 * slot, so it is ignored and the default is worked out again for this
 * slot (MCK-77: an auto Course deck must not follow the teacher to a
 * slot that already has a deck). Leave ``chosen`` out to keep the old
 * behaviour, where ``mode`` is always honoured when it can run.
 *
 * @param {{mode?: string, chosen?: boolean, currentAvailable?: boolean, previousAvailable?: boolean, courseDeckCount?: number}} state
 * @returns {"current"|"previous"|"course"}
 */
export function deckSeedDefaultMode(state = {}) {
  const currentOk = Boolean(state.currentAvailable);
  const previousOk = Boolean(state.previousAvailable);
  const courseOk = !courseDeckChoiceDisabled(state.courseDeckCount);
  const requested = state.chosen === false ? "current" : String(state.mode || "current");
  if (requested === "current" && currentOk) return "current";
  if (requested === "previous" && previousOk) return "previous";
  if (requested === "course" && courseOk) return "course";
  if (currentOk) return "current";
  if (!previousOk && courseOk) return "course";
  return "previous";
}

/**
 * Pick the deck mode Set Class should send when the teacher confirms.
 *
 * A chip the teacher clicked is sent as-is. An auto-picked chip is only
 * trusted when the loaded options are for the selected module and slot:
 * then the default is worked out again from those options, so a slot
 * that already has a deck keeps it. When the options are missing or
 * belong to another slot (still loading, or a stale reply), nothing is
 * written ("current").
 *
 * MCK-193: when the loaded options say this slot has no deck and no
 * previous deck to copy (a plain course with no pack, or any C1 with no
 * other decks), Previous cannot run, so "blank" is sent instead of
 * failing with "No previous challenge in this module." The server keeps
 * an existing deck (keep_existing), so this never replaces one.
 *
 * @param {{selected?: string, chosen?: boolean, catalog?: any, pack?: {module?: string, slot?: string}}} state
 * @returns {"current"|"previous"|"course"|"blank"}
 */
export function deckSeedConfirmMode(state = {}) {
  const selected = String(state.selected || "current");
  const catalog = state.catalog;
  const pack = state.pack || {};
  const sameSlot =
    Boolean(catalog) &&
    String(catalog.module || "").toUpperCase() === String(pack.module || "").toUpperCase() &&
    String(catalog.slot || "").toUpperCase() === String(pack.slot || "").toUpperCase();
  const previousCannotRun =
    sameSlot && !catalog.current?.available && !catalog.previous?.available;
  if (state.chosen) {
    if (selected === "previous" && previousCannotRun) return "blank";
    return selected === "previous" || selected === "course" ? selected : "current";
  }
  if (!sameSlot || catalog.current?.available) return "current";
  const decks = Array.isArray(catalog.decks) ? catalog.decks : [];
  const mode = deckSeedDefaultMode({
    chosen: false,
    currentAvailable: false,
    previousAvailable: Boolean(catalog.previous?.available),
    courseDeckCount: decks.length,
  });
  return mode === "previous" && previousCannotRun ? "blank" : mode;
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

/* ===== MCK-132: Course deck copy between sections (From → Source deck → confirm) =====
 * Every user-facing string below is placeholder copy from the Mobbin spec
 * (set-class-course-deck-copy-ia-v0.md) and is tagged "copy: Wonder" so
 * Wonder can swap the text without touching logic.
 */

/**
 * Natural sort key for a deck, read from its tokens (never from a label).
 * ``M2``/``C3`` -> ``[2, 3]``; M2 sorts before M10. Missing digits -> 0.
 *
 * @param {string} module
 * @param {string} slot
 * @returns {[number, number]}
 */
export function deckNaturalKey(module, slot) {
  const num = (token) => {
    const digits = String(token || "").replace(/\D+/g, "");
    return digits ? Number.parseInt(digits, 10) : 0;
  };
  return [num(module), num(slot)];
}

/** Plain deck name built from tokens, such as ``M2 C3``. */
function deckName(module, slot) {
  return `${String(module || "").toUpperCase()} ${String(slot || "").toUpperCase()}`.trim();
}

/** ``9 questions`` / ``1 question``. copy: Wonder */
function questionCount(n) {
  const count = Number(n) || 0;
  return `${count} ${count === 1 ? "question" : "questions"}`; // copy: Wonder
}

/** ``11 decks`` / ``1 deck`` / ``no decks``. copy: Wonder */
function deckCount(n) {
  const count = Number(n) || 0;
  if (!count) return "no decks"; // copy: Wonder
  return `${count} ${count === 1 ? "deck" : "decks"}`; // copy: Wonder
}

/**
 * Whether a deck row belongs to one class.
 * @param {any} deck
 * @param {number|string} classId
 */
function deckInClass(deck, classId) {
  return String(deck?.class_id ?? deck?.classId ?? "") === String(classId ?? "");
}

/**
 * The From list: sibling sections first (by section index), then this class.
 *
 * Uses the server's ``sources`` when present. An older server without
 * ``sources`` falls back to the sections that appear in ``decks``.
 *
 * @param {any[]|undefined} sources Server ``sources`` rows.
 * @param {any[]|undefined} decks Server ``decks`` rows.
 * @returns {{classId: string, sectionCode: string, label: string, count: number, isThisClass: boolean, sectionIndex: number}[]}
 */
export function deckCopySources(sources, decks) {
  const rows = Array.isArray(decks) ? decks : [];
  const countFor = (classId) => rows.filter((deck) => deckInClass(deck, classId)).length;
  let list = Array.isArray(sources) ? sources : [];
  if (!list.length) {
    const seen = new Map();
    for (const deck of rows) {
      const id = String(deck.class_id ?? deck.classId ?? "");
      if (!id || seen.has(id)) continue;
      seen.set(id, {
        class_id: id,
        section_code: deck.section_code || deck.sectionCode || "",
        section_index: deck.section_index ?? 1,
        is_this_class: Boolean(deck.same_class ?? deck.sameClass),
      });
    }
    list = [...seen.values()];
  }
  const shaped = list.map((row) => {
    const classId = String(row.class_id ?? row.classId ?? "");
    const sectionCode = String(row.section_code || row.sectionCode || "");
    const isThisClass = Boolean(row.is_this_class ?? row.isThisClass);
    const count = countFor(classId);
    const label = isThisClass
      ? `This class (${sectionCode}) · ${deckCount(count)}` // copy: Wonder
      : `${sectionCode} · ${deckCount(count)}`; // copy: Wonder
    return {
      classId,
      sectionCode,
      label,
      count,
      isThisClass,
      sectionIndex: Number(row.section_index ?? row.sectionIndex ?? 1) || 1,
    };
  });
  shaped.sort((a, b) => {
    if (a.isThisClass !== b.isThisClass) return a.isThisClass ? 1 : -1;
    if (a.sectionIndex !== b.sectionIndex) return a.sectionIndex - b.sectionIndex;
    return Number(a.classId) - Number(b.classId);
  });
  return shaped;
}

/**
 * Default From: the first sibling with a deck in the target M·C, otherwise
 * the first sibling, otherwise this class.
 *
 * @param {{classId: string, isThisClass: boolean}[]} srcs From ``deckCopySources``.
 * @param {any[]} decks Server ``decks`` rows.
 * @param {{module?: string, slot?: string}} pack Target module and slot.
 * @returns {string} class id, or ``""`` when there is no source.
 */
export function defaultCopySource(srcs, decks, pack = {}) {
  const list = Array.isArray(srcs) ? srcs : [];
  const rows = Array.isArray(decks) ? decks : [];
  const moduleName = String(pack.module || "").toUpperCase();
  const slot = String(pack.slot || "").toUpperCase();
  const siblings = list.filter((src) => !src.isThisClass);
  const sameSlot = siblings.find((src) =>
    rows.some(
      (deck) =>
        deckInClass(deck, src.classId) &&
        String(deck.module || "").toUpperCase() === moduleName &&
        String(deck.slot || "").toUpperCase() === slot
    )
  );
  if (sameSlot) return sameSlot.classId;
  if (siblings.length) return siblings[0].classId;
  const own = list.find((src) => src.isThisClass);
  return own ? own.classId : "";
}

/**
 * Source deck options for one section, grouped by module in natural order.
 * Values stay ``classId:M:C`` (unchanged from MCK-51). The deck in the
 * target slot gets a ``· same slot`` suffix.
 *
 * @param {any[]} decks Server ``decks`` rows.
 * @param {string|number} classId Chosen From section.
 * @param {{module?: string, slot?: string}} pack Target module and slot.
 * @returns {{group: string, options: {value: string, label: string, name: string, count: number, sameSlot: boolean}[]}[]}
 */
export function deckOptionsBySource(decks, classId, pack = {}) {
  const rows = (Array.isArray(decks) ? decks : []).filter((deck) => deckInClass(deck, classId));
  const moduleName = String(pack.module || "").toUpperCase();
  const slot = String(pack.slot || "").toUpperCase();
  rows.sort((a, b) => {
    const [am, as] = deckNaturalKey(a.module, a.slot);
    const [bm, bs] = deckNaturalKey(b.module, b.slot);
    return am - bm || as - bs;
  });
  /** @type {Map<string, any>} */
  const groups = new Map();
  for (const deck of rows) {
    const mod = String(deck.module || "").toUpperCase();
    const s = String(deck.slot || "").toUpperCase();
    const [modNumber] = deckNaturalKey(mod, s);
    const group = `Module ${modNumber}`; // copy: Wonder
    if (!groups.has(group)) groups.set(group, { group, options: [] });
    const sameSlot = mod === moduleName && s === slot;
    const name = deckName(mod, s);
    const count = Number(deck.question_count ?? deck.questionCount ?? 0) || 0;
    groups.get(group).options.push({
      value: `${String(classId)}:${mod}:${s}`,
      label: `${name} · ${questionCount(count)}${sameSlot ? " · same slot" : ""}`, // copy: Wonder
      name,
      count,
      sameSlot,
    });
  }
  return [...groups.values()];
}

/**
 * Default Source deck: the target M·C when the section has it, otherwise
 * ``""`` (the placeholder), so an overwrite is never preselected by
 * accident.
 *
 * @param {any[]} decks
 * @param {string|number} classId
 * @param {{module?: string, slot?: string}} pack
 * @returns {string}
 */
export function defaultCopyDeck(decks, classId, pack = {}) {
  for (const group of deckOptionsBySource(decks, classId, pack)) {
    const hit = group.options.find((opt) => opt.sameSlot);
    if (hit) return hit.value;
  }
  return "";
}

/** Placeholder option text for the Source deck select. */
export const DECK_COPY_PLACEHOLDER = "Choose a deck…"; // copy: Wonder

/**
 * Helper line under the chips while Course deck is on, for the two empty
 * states. Returns ``""`` when the normal helper line applies.
 *
 * @param {{sources?: any[], chosen?: string, course?: string}} state
 * @returns {string}
 */
export function deckCopyHelpText(state = {}) {
  const list = Array.isArray(state.sources) ? state.sources : [];
  const chosen = list.find((src) => String(src.classId) === String(state.chosen ?? ""));
  if (chosen && !chosen.isThisClass && !chosen.count) {
    return `${chosen.sectionCode} has no saved decks yet. Pick another section.`; // copy: Wonder
  }
  if (list.length && list.every((src) => src.isThisClass)) {
    const course = String(state.course || chosen?.sectionCode || "this course");
    return `No other current sections of ${course}. Pick a deck from this class.`; // copy: Wonder
  }
  return "";
}

/**
 * State of the inline confirm strip under the Set Class row.
 *
 * @param {{
 *   mode?: string,
 *   chosen?: boolean,
 *   deck?: {value?: string, name?: string, count?: number}|null,
 *   source?: {sectionCode?: string, isThisClass?: boolean}|null,
 *   target?: {sectionCode?: string, name?: string},
 *   current?: {available?: boolean, question_count?: number},
 *   phase?: "idle"|"copying"|"nudge"|"error",
 *   error?: string,
 *   done?: {text?: string}|null,
 * }} state
 * @returns {{hidden: boolean, variant: string, route: string, text: string,
 *   goLabel: string, showGo: boolean, showKeep: boolean, busy: boolean,
 *   holdsNext: boolean, replace: boolean}}
 */
export function deckCopyConfirmState(state = {}) {
  const base = {
    hidden: true,
    variant: "",
    route: "",
    text: "",
    goLabel: "",
    showGo: false,
    showKeep: false,
    busy: false,
    holdsNext: false,
    replace: false,
  };
  if (state.done && state.done.text) {
    return { ...base, hidden: false, variant: "success", text: String(state.done.text) };
  }
  const mode = String(state.mode || "current");
  if (mode !== "course" || !state.chosen) return base;
  const phase = String(state.phase || "idle");
  const deck = state.deck || null;
  if (!deck || !deck.value) {
    // A clicked Course deck with no deck picked used to fail after /begin.
    // Hold Next here instead.
    if (phase === "nudge") {
      return {
        ...base,
        hidden: false,
        variant: "nudge",
        text: "Choose a deck to copy first.", // copy: Wonder
        holdsNext: true,
      };
    }
    return { ...base, holdsNext: true };
  }
  const source = state.source || {};
  const target = state.target || {};
  const sourceSection = String(source.sectionCode || "");
  const route = `${sourceSection} · ${deck.name} → ${target.sectionCode || ""} · ${target.name || ""}.`; // copy: Wonder
  const current = state.current || {};
  const replace = Boolean(current.available);
  const qs = questionCount(deck.count);
  let variant = replace ? "replace" : "info";
  let text = replace
    ? `Replaces this class's current ${target.name} deck (${questionCount(current.question_count)}) with ${qs}, pages, media and team challenge. ${source.isThisClass ? `${sourceSection} · ${deck.name}` : sourceSection} is not changed. Can't be undone.` // copy: Wonder
    : `Copies ${qs}, pages, media and team challenge.`; // copy: Wonder
  const goLabel = replace ? "Replace deck" : "Copy deck"; // copy: Wonder
  if (phase === "copying") {
    text = "Copying…"; // copy: Wonder
  } else if (phase === "nudge" && replace) {
    variant = "nudge";
    text = "Replace the deck or keep current first."; // copy: Wonder
  } else if (phase === "error") {
    variant = "nudge";
    text = String(state.error || "Couldn't copy the deck. Try again."); // copy: Wonder
  }
  return {
    ...base,
    hidden: false,
    variant,
    route,
    text,
    goLabel,
    showGo: true,
    showKeep: replace,
    busy: phase === "copying",
    // An empty target loses nothing, so Next may still copy (as today).
    holdsNext: replace,
    replace,
  };
}

/**
 * Success line after a copy.
 * @param {{sourceSection: string, deckName: string, targetSection: string, targetName: string, count: number}} info
 * @returns {string}
 */
export function deckCopySuccessText(info) {
  return `✓ Copied ${info.sourceSection} · ${info.deckName} → ${info.targetSection} · ${info.targetName} (${questionCount(info.count)}).`; // copy: Wonder
}
