/**
 * Teacher Catch-up links card on Lesson Slides (MCK-203).
 *
 * One card sits under each module's saved-deck list. Each module has four
 * rows, C1–C4. A row that has not run yet stays grey. When a class has run
 * more than once, the newest run is the row and older runs sit in
 * "Earlier links (n)". Copy writes the plain URL only.
 */

/** Live classes inside one module. */
export const CATCHUP_SLOTS = Object.freeze(["C1", "C2", "C3", "C4"]);

/** Grey-row copy when End Live Class has not created a link yet. */
export const PENDING_COPY = "Link appears after End Live Class.";

/** Shown on the card when GET catch-up links fails (network or server). */
export const CATCHUP_LOAD_ERROR = "Could not load catch-up links.";

/** Copy button label before and after the brief "Copied" state. */
const COPY_LINK_LABEL = "Copy link";

/** How long the copy button shows "Copied" before reverting. */
const COPY_CONFIRM_MS = 2000;

const MONTHS = Object.freeze([
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]);

/**
 * Normalize a live-module token to ``M1`` form.
 * @param {unknown} raw
 * @returns {string}
 */
export function moduleKeyOf(raw) {
  const text = String(raw || "").trim().toUpperCase();
  if (!text) return "";
  if (/^M\d+$/.test(text)) return text;
  const match = text.match(/^M?(\d+)$/);
  if (!match) return "";
  const key = `M${Number(match[1])}`;
  return /^M\d+$/.test(key) ? key : "";
}

/**
 * Normalize a live-class token to ``C1`` form.
 * @param {unknown} raw
 * @returns {string}
 */
export function slotOf(raw) {
  const text = String(raw || "").trim().toUpperCase();
  const match = text.match(/^C?(\d+)$/) || text.match(/C(\d+)/);
  return match ? `C${Number(match[1])}` : text;
}

/**
 * Heading for one module section.
 * @param {string} moduleKey
 * @returns {string}
 */
export function moduleTitle(moduleKey) {
  const match = String(moduleKey || "").match(/^M(\d+)$/i);
  return match ? `Module ${Number(match[1])}` : String(moduleKey || "Module");
}

/**
 * Teacher-facing class name for a slot, matching the deck list.
 * @param {string} slot
 * @returns {string}
 */
export function classLabel(slot) {
  return `Class ${slot}`;
}

/**
 * True when the link is open. Only real true/1 values count.
 * @param {Record<string, unknown> | null | undefined} link
 * @returns {boolean}
 */
export function linkIsOpen(link) {
  const value = link?.is_open;
  return value === true || value === 1 || value === "1";
}

/**
 * Time used to pick the newest run.
 *
 * A ``run_key`` that embeds an ISO timestamp (``session:<id>:<started_at>``)
 * is the run clock. Otherwise ``created_at`` is the clock. ``run_key`` itself
 * breaks remaining ties so two runs are never collapsed.
 * @param {Record<string, unknown> | null | undefined} link
 * @returns {string}
 */
export function catchupRunTime(link) {
  const key = String(link?.run_key || "");
  const embedded = key.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
  if (embedded) return embedded[1];
  return String(link?.created_at || "");
}

/**
 * Newest run first. See ``catchupRunTime``.
 * @param {Record<string, unknown>} a
 * @param {Record<string, unknown>} b
 * @returns {number}
 */
export function compareCatchupNewest(a, b) {
  const left = catchupRunTime(a);
  const right = catchupRunTime(b);
  if (left !== right) return left < right ? 1 : -1;
  const keyA = String(a?.run_key || "");
  const keyB = String(b?.run_key || "");
  if (keyA !== keyB) return keyA < keyB ? 1 : -1;
  return 0;
}

/**
 * Module keys that need a deck list and a card, or ``M1`` when nothing exists.
 * @param {Array<Record<string, unknown>> | null | undefined} decks
 * @param {Array<Record<string, unknown>> | null | undefined} links
 * @returns {string[]}
 */
export function lessonModuleKeys(decks, links) {
  const keys = new Set();
  for (const deck of decks || []) {
    const n = Number(deck?.module_number);
    if (Number.isInteger(n) && n > 0) keys.add(`M${n}`);
  }
  for (const link of links || []) {
    const key = moduleKeyOf(link?.live_module);
    if (key) keys.add(key);
  }
  if (!keys.size) keys.add("M1");
  return [...keys].sort(compareModuleKeys);
}

/**
 * Numeric module order, then text.
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
function compareModuleKeys(a, b) {
  const left = Number(String(a).replace(/^M/i, ""));
  const right = Number(String(b).replace(/^M/i, ""));
  if (Number.isFinite(left) && Number.isFinite(right) && left !== right) {
    return left - right;
  }
  return String(a).localeCompare(String(b));
}

/**
 * Saved decks for one module, live-class order.
 * @param {Array<Record<string, unknown>> | null | undefined} decks
 * @param {string} moduleKey
 * @returns {Array<Record<string, unknown>>}
 */
export function decksForModule(decks, moduleKey) {
  const n = Number(String(moduleKey).replace(/^M/i, ""));
  return (decks || [])
    .filter((deck) => Number(deck?.module_number) === n)
    .slice()
    .sort((a, b) => Number(a?.live_index) - Number(b?.live_index));
}

/**
 * Links for one module slot, newest run first.
 * @param {Array<Record<string, unknown>> | null | undefined} links
 * @param {string} moduleKey
 * @param {string} slot
 * @returns {Array<Record<string, unknown>>}
 */
export function runsForSlot(links, moduleKey, slot) {
  return (links || [])
    .filter((link) => moduleKeyOf(link?.live_module) === moduleKey && slotOf(link?.live_slot) === slot)
    .slice()
    .sort(compareCatchupNewest);
}

/**
 * View model: one block per module, four slots, newest run plus earlier runs.
 * @param {Array<Record<string, unknown>> | null | undefined} decks
 * @param {Array<Record<string, unknown>> | null | undefined} links
 * @returns {Array<{moduleKey: string, title: string, decks: Array<Record<string, unknown>>, slots: Array<{slot: string, label: string, current: Record<string, unknown> | null, earlier: Array<Record<string, unknown>>}>}>}
 */
export function buildLessonSlideCatchup(decks, links) {
  return lessonModuleKeys(decks, links).map((moduleKey) => ({
    moduleKey,
    title: moduleTitle(moduleKey),
    decks: decksForModule(decks, moduleKey),
    slots: CATCHUP_SLOTS.map((slot) => {
      const runs = runsForSlot(links, moduleKey, slot);
      return {
        slot,
        label: classLabel(slot),
        current: runs[0] || null,
        earlier: runs.slice(1),
      };
    }),
  }));
}

/**
 * ``Oct 13, 2026`` from an ISO timestamp. Empty when the value is not a date.
 * @param {unknown} iso
 * @returns {string}
 */
export function formatCatchupDate(iso) {
  const match = String(iso || "").match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "";
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return "";
  return `${month} ${Number(match[3])}, ${match[1]}`;
}

/**
 * Same-origin preview path. Refuses anything that is not a token.
 * @param {unknown} token
 * @returns {string}
 */
export function previewHref(token) {
  const clean = String(token || "").trim();
  if (!/^[A-Za-z0-9_-]+$/.test(clean)) return "";
  return `/catchup/${encodeURIComponent(clean)}`;
}

/**
 * Plain http(s) URL to copy. Falls back to the preview path.
 * @param {Record<string, unknown> | null | undefined} link
 * @returns {string}
 */
export function plainCatchupUrl(link) {
  const url = String(link?.url || "").trim();
  if (/^https?:\/\//i.test(url) && !/[\s<>]/.test(url)) return url;
  return previewHref(link?.token);
}

/**
 * http(s) or same-origin href for a saved deck. Empty when unsafe.
 * @param {unknown} raw
 * @returns {string}
 */
export function safeHttpHref(raw) {
  const url = String(raw || "").trim();
  if (!url) return "";
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return parsed.href;
  } catch {
    return "";
  }
  return "";
}

/**
 * True when ``className`` contains ``name``.
 * @param {Element | null | undefined} node
 * @param {string} name
 * @returns {boolean}
 */
function hasClass(node, name) {
  const value = node && node.className;
  const text = typeof value === "string" ? value : "";
  return text.split(/\s+/).includes(name);
}

/**
 * Nearest ancestor (including the node) whose class list contains ``name``.
 * @param {Element | null | undefined} node
 * @param {string} name
 * @returns {Element | null}
 */
function ancestorWithClass(node, name) {
  let cur = node || null;
  while (cur) {
    if (hasClass(cur, name)) return cur;
    cur = cur.parentElement || null;
  }
  return null;
}

/**
 * Create an element on the mount's document.
 * @param {Document} doc
 * @param {string} tag
 * @returns {HTMLElement}
 */
function el(doc, tag) {
  return doc.createElement(tag);
}

/**
 * Paint Open/Closed badge and switch text on one row.
 * @param {Element} row
 * @param {boolean} open
 * @returns {void}
 */
export function paintOpenState(row, open) {
  const badge = row.querySelector(".catchup-badge");
  if (badge) {
    badge.textContent = open ? "Open" : "Closed";
    badge.className = open ? "catchup-badge is-open" : "catchup-badge is-muted";
  }
  const text = row.querySelector(".catchup-switch-text");
  if (text) text.textContent = open ? "Open" : "Closed";
  const input = row.querySelector(".catchup-toggle");
  if (input) input.checked = Boolean(open);
}

/**
 * POST ``{"open": bool}`` for one catch-up token.
 * @param {string | number} classId
 * @param {string} token
 * @param {boolean} open
 * @param {typeof fetch} [fetchImpl]
 * @returns {Promise<boolean>}
 */
export async function postCatchupOpen(classId, token, open, fetchImpl = fetch) {
  const res = await fetchImpl(
    `/api/classes/${classId}/catchup-links/${encodeURIComponent(token)}`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ open: Boolean(open) }),
    },
  );
  return Boolean(res && res.ok);
}

/**
 * Write the plain link to the clipboard. Never builds a frame.
 * @param {string} url
 * @param {HTMLButtonElement | null} [button]
 * @returns {Promise<boolean>}
 */
export async function copyPlainLink(url, button) {
  const plain = String(url || "");
  if (!plain || /[<>]/.test(plain) || /^javascript:/i.test(plain)) return false;
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(plain);
    } else {
      throw new Error("clipboard unavailable");
    }
    if (button) {
      const resetId = button.getAttribute("data-copy-reset-id");
      if (resetId) clearTimeout(Number(resetId));
      button.textContent = "Copied";
      const id = setTimeout(() => {
        button.textContent = COPY_LINK_LABEL;
        if (typeof button.removeAttribute === "function") {
          button.removeAttribute("data-copy-reset-id");
        } else {
          button.setAttribute("data-copy-reset-id", "");
        }
      }, COPY_CONFIRM_MS);
      button.setAttribute("data-copy-reset-id", String(id));
    }
    return true;
  } catch {
    if (typeof window !== "undefined" && typeof window.prompt === "function") {
      window.prompt("Copy this link", plain);
    }
    return false;
  }
}

/**
 * One link's controls: class name, created date, badge, toggle, copy, preview.
 * @param {Document} doc
 * @param {string} slot
 * @param {Record<string, unknown>} link
 * @param {boolean} earlier
 * @returns {HTMLDivElement}
 */
function linkControls(doc, slot, link, earlier) {
  const open = linkIsOpen(link);
  const row = el(doc, "div");
  row.className = earlier ? "catchup-row is-earlier" : "catchup-row";
  row.setAttribute("data-run-key", String(link.run_key || ""));

  const name = el(doc, "span");
  name.className = "catchup-class";
  name.textContent = classLabel(slot);

  const when = formatCatchupDate(link.created_at);
  const date = el(doc, "time");
  date.className = "catchup-date";
  if (when) {
    date.textContent = when;
    date.setAttribute("datetime", String(link.created_at || ""));
  }

  const badge = el(doc, "span");
  badge.className = open ? "catchup-badge is-open" : "catchup-badge is-muted";
  badge.textContent = open ? "Open" : "Closed";

  const label = el(doc, "label");
  label.className = "catchup-switch";
  const input = el(doc, "input");
  input.type = "checkbox";
  input.className = "catchup-toggle";
  input.checked = open;
  input.setAttribute("data-token", String(link.token || ""));
  input.setAttribute("aria-label", `${classLabel(slot)} catch-up link`);
  const switchText = el(doc, "span");
  switchText.className = "catchup-switch-text";
  switchText.textContent = open ? "Open" : "Closed";
  label.append(input, switchText);

  const copy = el(doc, "button");
  copy.type = "button";
  copy.className = "secondary catchup-copy";
  copy.textContent = COPY_LINK_LABEL;
  copy.setAttribute("data-url", plainCatchupUrl(link));

  row.append(name, date, badge, label, copy);
  const href = previewHref(link.token);
  if (href) {
    const preview = el(doc, "a");
    preview.className = "btn secondary catchup-preview";
    preview.textContent = "Preview";
    preview.setAttribute("href", href);
    preview.setAttribute("target", "_blank");
    preview.setAttribute("rel", "noopener noreferrer");
    row.append(preview);
  }
  return row;
}

/**
 * Grey row for a class that has not ended yet.
 * @param {Document} doc
 * @param {string} slot
 * @returns {HTMLLIElement}
 */
function pendingRow(doc, slot) {
  const item = el(doc, "li");
  item.className = "catchup-slot is-pending";
  item.setAttribute("data-slot", slot);
  const name = el(doc, "span");
  name.className = "catchup-class";
  name.textContent = classLabel(slot);
  const message = el(doc, "span");
  message.className = "catchup-pending";
  message.textContent = PENDING_COPY;
  item.append(name, message);
  return item;
}

/**
 * Current run, plus the earlier-links expander when this class ran again.
 * @param {Document} doc
 * @param {{slot: string, current: Record<string, unknown>, earlier: Array<Record<string, unknown>>}} slot
 * @returns {HTMLLIElement}
 */
function activeSlot(doc, slot) {
  const item = el(doc, "li");
  item.className = "catchup-slot";
  item.setAttribute("data-slot", slot.slot);
  item.append(linkControls(doc, slot.slot, slot.current, false));
  if (slot.earlier.length) {
    const details = el(doc, "details");
    details.className = "catchup-earlier";
    const summary = el(doc, "summary");
    summary.textContent = `Earlier links (${slot.earlier.length})`;
    details.append(summary);
    for (const link of slot.earlier) {
      details.append(linkControls(doc, slot.slot, link, true));
    }
    item.append(details);
  }
  return item;
}

/**
 * Deck table for one module. Same columns as the old combined list.
 * @param {Document} doc
 * @param {string} moduleKey
 * @param {Array<Record<string, unknown>>} decks
 * @returns {HTMLDivElement}
 */
function deckTable(doc, moduleKey, decks) {
  const wrap = el(doc, "div");
  wrap.className = "table-wrap";
  const table = el(doc, "table");
  table.className = "ls-live-lessons ls-saved-decks";
  table.setAttribute("data-module", moduleKey);
  const head = el(doc, "thead");
  const headRow = el(doc, "tr");
  for (const label of ["Module", "Class", "Slides"]) {
    const cell = el(doc, "th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = el(doc, "tbody");
  if (!decks.length) {
    const row = el(doc, "tr");
    const cell = el(doc, "td");
    cell.setAttribute("colspan", "3");
    cell.textContent = "No Google decks saved for this module yet.";
    row.append(cell);
    body.append(row);
  } else {
    for (const deck of decks) {
      const row = el(doc, "tr");
      const moduleCell = el(doc, "td");
      moduleCell.textContent = `Module ${Number(deck.module_number || 0)}`;
      const classCell = el(doc, "td");
      classCell.textContent = `Class C${Number(deck.live_index || 0)}`;
      const linkCell = el(doc, "td");
      const href = safeHttpHref(deck.presentation_url);
      if (href) {
        const anchor = el(doc, "a");
        anchor.textContent = "Open slides";
        anchor.setAttribute("href", href);
        anchor.setAttribute("target", "_blank");
        anchor.setAttribute("rel", "noopener");
        linkCell.append(anchor);
      } else {
        linkCell.textContent = "Open slides";
      }
      row.append(moduleCell, classCell, linkCell);
      body.append(row);
    }
  }
  table.append(head, body);
  wrap.append(table);
  return wrap;
}

/**
 * One module: deck list, then the Catch-up links card.
 * @param {Document} doc
 * @param {ReturnType<typeof buildLessonSlideCatchup>[number]} mod
 * @param {string} error
 * @returns {HTMLElement}
 */
function renderModule(doc, mod, error) {
  const section = el(doc, "section");
  section.className = "ls-module-block";
  section.setAttribute("data-module", mod.moduleKey);
  const heading = el(doc, "h3");
  heading.className = "ls-module-title";
  heading.textContent = mod.title;
  const card = el(doc, "article");
  card.className = "card catchup-links-card";
  card.setAttribute("data-live-module", mod.moduleKey);
  const title = el(doc, "h3");
  title.textContent = "Catch-up links";
  const hint = el(doc, "p");
  hint.className = "hint compact";
  hint.textContent = "Copy the plain link. There is no embed.";
  card.append(title, hint);
  if (error) {
    const note = el(doc, "p");
    note.className = "hint catchup-error";
    note.textContent = error;
    card.append(note);
  } else {
    const list = el(doc, "ul");
    list.className = "catchup-rows";
    for (const slot of mod.slots) {
      list.append(slot.current ? activeSlot(doc, slot) : pendingRow(doc, slot.slot));
    }
    card.append(list);
  }
  section.append(heading, deckTable(doc, mod.moduleKey, mod.decks), card);
  return section;
}

/**
 * Replace the Lesson Slides module mount with deck lists and catch-up cards.
 * @param {Element} mount
 * @param {{decks?: Array<Record<string, unknown>>, links?: Array<Record<string, unknown>>, error?: string}} options
 * @returns {void}
 */
export function paintLessonModuleSections(mount, options) {
  if (!mount) return;
  const doc = mount.ownerDocument || globalThis.document;
  const model = buildLessonSlideCatchup(options?.decks, options?.links);
  const error = String(options?.error || "");
  mount.replaceChildren(...model.map((mod) => renderModule(doc, mod, error)));
}

/**
 * Toggle and copy listeners. Safe to call once; later calls no-op.
 * @param {Element | null | undefined} root
 * @param {string | number} classId
 * @returns {void}
 */
export function bindCatchupLinks(root, classId) {
  if (!root || root.getAttribute("data-catchup-bound") === "1") return;
  root.setAttribute("data-catchup-bound", "1");
  root.addEventListener("change", (event) => {
    const input = event.target;
    if (!input || !hasClass(input, "catchup-toggle")) return undefined;
    const token = input.getAttribute("data-token") || "";
    const open = Boolean(input.checked);
    const prior = !open;
    void (async () => {
      try {
        const ok = await postCatchupOpen(classId, token, open);
        const row = ancestorWithClass(input, "catchup-row");
        if (!ok) {
          input.checked = prior;
          return;
        }
        if (row) paintOpenState(row, open);
      } catch {
        input.checked = prior;
      }
    })();
  });
  root.addEventListener("click", (event) => {
    const button = ancestorWithClass(event.target, "catchup-copy");
    if (!button) return undefined;
    event.preventDefault();
    return copyPlainLink(button.getAttribute("data-url") || "", button);
  });
}
