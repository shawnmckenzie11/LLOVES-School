/**
 * Staff "What's new" panel (MCK-124, slice 1).
 *
 * Reads ``/static/whats-new/releases.json`` (newest release first) and shows
 * the newest release in ``#whats-new-dialog``. It opens by itself once per
 * staff user per release: the seen release id lives in localStorage under
 * ``alc-whats-new:<user id>``. "Got it", × and Esc all close the dialog,
 * and closing marks the release seen. The "What's new" link reopens it.
 *
 * Hard rule: never on the live class. On ``body.course-live`` (the teacher
 * may be projecting) this module renders nothing, fetches nothing and never
 * opens, not even from the link. Text goes in with ``textContent`` only.
 */

export const SEEN_KEY_PREFIX = "alc-whats-new:";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * True on a page where the panel must never render or open.
 * @param {Document} doc
 * @returns {boolean}
 */
export function isLiveClassPage(doc) {
  const body = doc && doc.body;
  return !body || body.classList.contains("course-live");
}

/**
 * localStorage key for this staff user's last seen release.
 * @param {string|number} userId
 * @returns {string}
 */
export function seenKey(userId) {
  return `${SEEN_KEY_PREFIX}${String(userId || "").trim()}`;
}

/**
 * Newest release from the releases.json body, or null.
 * @param {any} data
 * @returns {any|null}
 */
export function newestRelease(data) {
  const releases = data && Array.isArray(data.releases) ? data.releases : [];
  const first = releases[0];
  return first && typeof first.id === "string" && first.id && Array.isArray(first.items)
    ? first
    : null;
}

/**
 * "Oct 2, 2026" from "2026-10-02" (no Date, so no timezone shift).
 * @param {string} iso
 * @returns {string}
 */
export function formatReleaseDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) return String(iso || "");
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${month} ${Number(match[3])}, ${match[1]}` : String(iso);
}

/**
 * Should the panel open by itself for this user and release?
 * @param {{doc: Document, storage: Storage|null, userId: string, release: any}} args
 * @returns {boolean}
 */
export function shouldAutoOpen({ doc, storage, userId, release }) {
  if (isLiveClassPage(doc) || !release || !userId) return false;
  let seen = null;
  try {
    seen = storage ? storage.getItem(seenKey(userId)) : null;
  } catch (_err) {
    seen = null;
  }
  return seen !== release.id;
}

/**
 * Fill the dialog with one release. textContent only, never innerHTML.
 * @param {Document} doc
 * @param {HTMLElement} dialog
 * @param {any} release
 */
export function renderRelease(doc, dialog, release) {
  const date = dialog.querySelector("[data-whats-new-date]");
  if (date) date.textContent = formatReleaseDate(release.date || release.id);
  const list = dialog.querySelector("[data-whats-new-list]");
  if (list) {
    list.replaceChildren();
    for (const item of release.items) {
      const li = doc.createElement("li");
      li.className = "whats-new-item";
      const title = doc.createElement("h3");
      title.className = "whats-new-item-title";
      title.textContent = String(item.title || "");
      const line = doc.createElement("p");
      line.className = "whats-new-item-line";
      line.textContent = String(item.line || "");
      const meta = doc.createElement("div");
      meta.className = "whats-new-item-meta";
      const audience = String(item.audience || "");
      if (audience) {
        const chip = doc.createElement("span");
        chip.className = audience.startsWith("Both") ? "whats-new-aud is-both" : "whats-new-aud";
        chip.textContent = audience;
        meta.appendChild(chip);
      }
      if (item.refs) {
        const refs = doc.createElement("span");
        refs.className = "whats-new-refs";
        refs.textContent = String(item.refs);
        meta.appendChild(refs);
      }
      li.append(title, line, meta);
      list.appendChild(li);
    }
  }
  const notYet = dialog.querySelector("[data-whats-new-notyet]");
  if (notYet) {
    const text = notYet.querySelector("[data-whats-new-notyet-text]");
    if (text) text.textContent = release.not_yet ? String(release.not_yet) : "";
    notYet.hidden = !release.not_yet;
  }
}

/**
 * Wire the panel on a staff page. Safe to call on any page.
 * @param {{document: Document, window?: any, fetch?: Function, storage?: Storage|null}} env
 * @returns {Promise<"live"|"absent"|"empty"|"error"|"opened"|"seen">}
 */
export async function initWhatsNew(env) {
  const doc = env.document;
  if (isLiveClassPage(doc)) return "live";
  const dialog = doc.getElementById("whats-new-dialog");
  const userId = String((doc.body.dataset && doc.body.dataset.userId) || "").trim();
  if (!dialog || !userId || typeof env.fetch !== "function") return "absent";
  const storage = env.storage === undefined ? null : env.storage;
  const src = dialog.getAttribute("data-releases-src") || "/static/whats-new/releases.json";
  let release = null;
  try {
    const res = await env.fetch(src, { credentials: "same-origin", cache: "no-cache" });
    if (!res || !res.ok) return "error";
    release = newestRelease(await res.json());
  } catch (_err) {
    return "error";
  }
  if (!release) return "empty";
  // The class might have gone live while the file loaded.
  if (isLiveClassPage(doc)) return "live";
  renderRelease(doc, dialog, release);

  const open = () => {
    if (isLiveClassPage(doc) || dialog.open) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };
  const markSeen = () => {
    try {
      if (storage) storage.setItem(seenKey(userId), release.id);
    } catch (_err) {
      /* storage full or blocked: it shows again next time, harmless */
    }
  };
  dialog.addEventListener("close", markSeen);
  for (const btn of dialog.querySelectorAll("[data-whats-new-close]")) {
    btn.addEventListener("click", () => dialog.close());
  }
  const link = doc.getElementById("whats-new-open");
  if (link) {
    link.hidden = false;
    link.addEventListener("click", (event) => {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      open();
    });
  }
  if (shouldAutoOpen({ doc, storage, userId, release })) {
    open();
    return "opened";
  }
  return "seen";
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  let storage = null;
  try {
    storage = window.localStorage;
  } catch (_err) {
    storage = null;
  }
  void initWhatsNew({ document, window, fetch: window.fetch.bind(window), storage });
}
