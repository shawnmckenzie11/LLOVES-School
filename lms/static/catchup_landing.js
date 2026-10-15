/** MCK-222: catch-up landing list pagination and offline retry. */

const root = document.getElementById("catchup-landing-root");
const list = document.getElementById("cl-list");
const moreBtn = document.getElementById("cl-show-older");

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function rowHtml(row, copy) {
  const title = row.title
    ? `<p class="cl-row-title">${escapeHtml(row.title)}</p>`
    : "";
  return `<a class="cl-row" href="${escapeHtml(row.url)}">
    <div class="cl-row-main">
      <p class="cl-row-label">${escapeHtml(row.label)}</p>
      ${title}
    </div>
    <div class="cl-tier-row cl-tier-row--compact" aria-label="Participation tiers">
      <div class="cl-tier"><span class="cl-tier-key">${escapeHtml(copy.t1Label)}</span><span class="cl-tier-val">${escapeHtml(row.tiers.t1)}</span></div>
      <div class="cl-tier"><span class="cl-tier-key">${escapeHtml(copy.t2Label)}</span><span class="cl-tier-val">${escapeHtml(row.tiers.t2)}</span></div>
      <div class="cl-tier"><span class="cl-tier-key">${escapeHtml(copy.t3Label)}</span><span class="cl-tier-val">${escapeHtml(row.tiers.t3)}</span></div>
    </div>
  </a>`;
}

async function loadMore() {
  if (!root || !list || !moreBtn) return;
  const offset = parseInt(root.dataset.offset || "0", 10) + list.querySelectorAll(".cl-row").length;
  const copy = {
    t1Label: moreBtn.dataset.t1 || "T1 Individual",
    t2Label: moreBtn.dataset.t2 || "T2 Group",
    t3Label: moreBtn.dataset.t3 || "T3 Understanding",
    offline: moreBtn.dataset.offline || "No connection. Retry.",
  };
  moreBtn.disabled = true;
  try {
    const res = await fetch(`/api/student/catchup-landing?offset=${offset}`, {
      credentials: "same-origin",
    });
    const data = await res.json();
    if (!data.ok) {
      window.alert(copy.offline);
      moreBtn.disabled = false;
      return;
    }
    data.rows.forEach((row) => {
      list.insertAdjacentHTML("beforeend", rowHtml(row, copy));
    });
    root.dataset.offset = String(offset);
    if (!data.has_more) {
      moreBtn.hidden = true;
    } else {
      moreBtn.disabled = false;
    }
  } catch (_err) {
    window.alert(copy.offline);
    moreBtn.disabled = false;
  }
}

if (moreBtn && root) {
  const heading = document.getElementById("cl-other-heading");
  if (heading) {
    moreBtn.dataset.t1 = heading.textContent.includes("T1")
      ? "T1 Individual"
      : "T1 Individual";
  }
  moreBtn.dataset.offline = root.dataset.offline || "No connection. Retry.";
  moreBtn.addEventListener("click", loadMore);
}

// MCK-42: "Not you?" signs this student out. Clear the device's unsent-answer
// outbox now too, in case the landing page resumes elsewhere.
document.querySelectorAll(".cl-not-you").forEach((link) => {
  link.addEventListener("click", () => {
    try {
      window.localStorage.removeItem("lloves-unsent-answers");
    } catch (_err) {
      /* storage blocked */
    }
  });
});
