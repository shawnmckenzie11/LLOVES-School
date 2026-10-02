/**
 * MCK-116: Celebrations avatar reward pop-up (student character + home).
 *
 * One calm screen: pick one of the 20 reward avatars. "Pick later", the
 * scrim and Esc all keep the reward waiting (no request). Stacked rewards
 * play one after the other on the same screen. Never stays open once the
 * class is under way (MCK-116 MED-1): body ``is-live`` / not waiting, a
 * teacher-pushed question or TEAMS (``reward_window`` false on
 * /api/student/state, body ``is-reward-shut``), or a 409 from the claim.
 * Copy is Wonder v1.1, word for word.
 */
(function () {
  "use strict";

  const root = document.getElementById("avatar-reward");
  if (!root) return;
  const body = document.body;
  const dataEl = document.getElementById("avatar-reward-data");
  /** @type {{id:number, award_title:string}[]} */
  let grants = [];
  try {
    grants = JSON.parse((dataEl && dataEl.textContent) || "[]") || [];
  } catch (_err) {
    grants = [];
  }
  let done = 0;
  let busy = false;
  let opener = null;
  let closeTimer = 0;
  let shut = false;
  let pollTimer = 0;
  const POLL_MS = 4000;

  const modal = root.querySelector(".rw-modal");
  const titleEl = document.getElementById("rw-title");
  const reasonEl = document.getElementById("rw-reason");
  const countEl = document.getElementById("rw-count");
  const grid = document.getElementById("rw-grid");
  const promptEl = document.getElementById("rw-prompt");
  const claimed = document.getElementById("rw-claimed");
  const claimedImg = document.getElementById("rw-claimed-img");
  const foot = document.getElementById("rw-foot");
  const errorEl = document.getElementById("rw-error");
  const keep = document.getElementById("rw-keep");
  const later = document.getElementById("rw-later");
  const scrim = root.querySelector(".rw-scrim");
  const nextUrl = root.getAttribute("data-next-url") || "";
  const token = root.getAttribute("data-visit-token") || "";
  const KEEP_LABEL = "Keep this one";
  const SAVING_LABEL = "Saving…";

  function radios() {
    return Array.from(grid.querySelectorAll('input[name="rw-avatar"]'));
  }

  function selected() {
    const hit = radios().find((r) => r.checked && !r.disabled);
    return hit ? hit.value : "";
  }

  /** True while the class is under way (never show the pop-up then). */
  function midClass() {
    if (shut) return true;
    if (body.classList.contains("is-live")) return true;
    if (body.classList.contains("is-reward-shut")) return true;
    if (!body.classList.contains("student-home")) return false;
    return (
      !body.classList.contains("is-waiting-room") &&
      !body.classList.contains("is-game-show-welcome")
    );
  }

  function entries() {
    return Array.from(document.querySelectorAll("[data-reward-open]"));
  }

  function paintEntries() {
    const show = grants.length > 0 && !midClass();
    entries().forEach((el) => {
      el.hidden = !show;
    });
  }

  function paintSelection() {
    radios().forEach((r) => {
      const card = r.closest(".rw-card");
      if (card) card.classList.toggle("is-selected", r.checked && !r.disabled);
    });
    keep.setAttribute("aria-disabled", selected() && !busy ? "false" : "true");
  }

  function paintGrant() {
    const g = grants[0];
    if (!g) return;
    reasonEl.textContent = "You're on the board for " + g.award_title + ".";
    const total = done + grants.length;
    countEl.hidden = total < 2;
    countEl.textContent = "Reward " + (done + 1) + " of " + total;
    radios().forEach((r) => {
      r.checked = false;
    });
    errorEl.hidden = true;
    claimed.hidden = true;
    grid.hidden = false;
    promptEl.hidden = false;
    foot.hidden = false;
    paintSelection();
  }

  function setInert(on) {
    Array.from(body.children).forEach((el) => {
      if (el === root || el.tagName === "SCRIPT") return;
      if (on) el.setAttribute("inert", "");
      else el.removeAttribute("inert");
    });
  }

  function open(from) {
    if (!grants.length || midClass() || !root.hidden) return;
    opener = from || null;
    paintGrant();
    root.hidden = false;
    body.classList.add("rw-open");
    setInert(true);
    titleEl.focus();
    watchState();
  }

  /** Class got going: close, hide the slot / chip, never reopen this page. */
  function shutDown() {
    shut = true;
    close();
    paintEntries();
  }

  /**
   * While the pop-up is open, watch /api/student/state (``reward_window``).
   * The picker page has no live poll of its own, and on home a pushed
   * question does not always repaint the page before its next full poll.
   */
  function watchState() {
    window.clearTimeout(pollTimer);
    if (root.hidden || shut) return;
    pollTimer = window.setTimeout(async () => {
      if (root.hidden || shut) return;
      try {
        const init = { credentials: "same-origin", headers: { "X-Student-Visit-Token": token } };
        const res = await fetch(
          "/api/student/state",
          typeof window.studentVisitFetchInit === "function" ? window.studentVisitFetchInit(init) : init
        );
        const data = res.ok ? await res.json().catch(() => null) : null;
        if (data && (data.scoring || data.reward_window === false)) {
          shutDown();
          return;
        }
      } catch (_err) {
        /* keep the pop-up; the claim route still says 409 mid-class */
      }
      watchState();
    }, POLL_MS);
  }

  function close() {
    if (root.hidden) return;
    window.clearTimeout(pollTimer);
    window.clearTimeout(closeTimer);
    root.hidden = true;
    body.classList.remove("rw-open");
    setInert(false);
    paintEntries();
    const back =
      (opener && !opener.hidden && opener) ||
      entries().find((el) => !el.hidden) ||
      document.querySelector(".char-card input") ||
      document.getElementById("me-avatar");
    if (back && typeof back.focus === "function") back.focus();
  }

  function later_() {
    if (busy) return;
    close();
  }

  function markOwned(key) {
    radios().forEach((r) => {
      if (r.value !== key) return;
      r.checked = false;
      r.disabled = true;
      const card = r.closest(".rw-card");
      if (card && !card.classList.contains("is-owned")) {
        card.classList.add("is-owned");
        const tag = document.createElement("span");
        tag.className = "tag-earned";
        tag.textContent = "Earned";
        card.appendChild(tag);
      }
    });
  }

  function paintMeAvatar(src) {
    const me = document.getElementById("me-avatar");
    if (!me || !src) return;
    const img = document.createElement("img");
    img.className = "avatar-img";
    img.src = src;
    img.alt = "";
    me.replaceChildren(img);
    me.hidden = false;
  }

  function showClaimed(data) {
    claimedImg.src = data.src || "";
    grid.hidden = true;
    promptEl.hidden = true;
    countEl.hidden = true;
    foot.hidden = true;
    claimed.hidden = false;
    markOwned(data.avatar_key);
    paintMeAvatar(data.src);
    done += 1;
    grants.shift();
    if (data.next && !grants.some((g) => g.id === data.next.grant_id)) {
      grants.unshift({ id: data.next.grant_id, award_title: data.next.award_title });
    }
    closeTimer = window.setTimeout(() => {
      if (grants.length && !midClass()) {
        paintGrant();
        titleEl.focus();
        return;
      }
      close();
      if (nextUrl) window.location.assign(nextUrl);
    }, 2000);
  }

  async function claim() {
    const key = selected();
    const g = grants[0];
    if (busy || !key || !g) return;
    busy = true;
    errorEl.hidden = true;
    keep.textContent = SAVING_LABEL;
    keep.setAttribute("aria-busy", "true");
    keep.classList.add("is-busy");
    later.setAttribute("aria-disabled", "true");
    grid.classList.add("is-inert");
    let data = null;
    let underWay = false;
    try {
      const res = await fetch("/api/student/avatar-reward/claim", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Student-Visit-Token": token,
        },
        body: JSON.stringify({ grant_id: g.id, avatar_key: key, visit_token: token }),
      });
      data = await res.json().catch(() => null);
      // 409: class is under way (MED-1). The reward keeps waiting.
      underWay = res.status === 409;
      if (!res.ok || !data || !data.ok) data = null;
    } catch (_err) {
      data = null;
    }
    busy = false;
    keep.textContent = KEEP_LABEL;
    keep.removeAttribute("aria-busy");
    keep.classList.remove("is-busy");
    later.removeAttribute("aria-disabled");
    grid.classList.remove("is-inert");
    if (underWay) {
      shutDown();
      return;
    }
    if (!data) {
      errorEl.hidden = false;
      paintSelection();
      return;
    }
    showClaimed(data);
  }

  grid.addEventListener("change", paintSelection);
  keep.addEventListener("click", () => {
    if (keep.getAttribute("aria-disabled") === "true") return;
    claim();
  });
  later.addEventListener("click", later_);
  scrim.addEventListener("click", later_);
  root.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      later_();
      return;
    }
    if (ev.key !== "Tab") return;
    const focusable = Array.from(
      modal.querySelectorAll("button, input:not([disabled]), [tabindex='-1']")
    ).filter((el) => !el.closest("[hidden]") && el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  });
  entries().forEach((el) => {
    el.addEventListener("click", () => open(el));
  });

  // The class got going: close (the reward keeps waiting) and hide the chip.
  new MutationObserver(() => {
    if (midClass()) close();
    paintEntries();
  }).observe(body, { attributes: true, attributeFilter: ["class"] });

  paintEntries();
  if (root.getAttribute("data-auto-open") === "1") open(null);
})();
