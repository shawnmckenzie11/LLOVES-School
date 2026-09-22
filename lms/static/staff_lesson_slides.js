/**
 * Lesson Slides wizard: preview connected async Lessons, then build a Drive deck.
 */
const root = document.getElementById("lesson-slides-root");
const classId = root?.dataset.classId;

/**
 * Read wizard numeric/text fields.
 * @returns {{module:number, live_index:number, lives_per_module:number, weeks_per_module:number, keyword:string}}
 */
function wizardFields() {
  return {
    module: Number(document.getElementById("ls-module")?.value || 1),
    live_index: Number(document.getElementById("ls-live-index")?.value || 1),
    lives_per_module: Number(document.getElementById("ls-lives")?.value || 4),
    weeks_per_module: Number(document.getElementById("ls-weeks")?.value || 2),
    keyword: String(document.getElementById("ls-keyword")?.value || "Lesson"),
  };
}

/**
 * Query string for preview GET.
 * @returns {string}
 */
function previewQuery() {
  const f = wizardFields();
  const q = new URLSearchParams({
    module: String(f.module),
    live_index: String(f.live_index),
    lives_per_module: String(f.lives_per_module),
    weeks_per_module: String(f.weeks_per_module),
    keyword: f.keyword,
  });
  return q.toString();
}

/**
 * Return a same-origin or http(s) href, or empty if unsafe.
 * @param {unknown} raw
 * @returns {string}
 */
function safeDeckHref(raw) {
  const url = String(raw || "").trim();
  if (!url) return "";
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return parsed.href;
    }
  } catch {
    return "";
  }
  return "";
}

/**
 * Paint saved Google decks from the lesson-slides GET payload.
 * @param {Record<string, unknown>} data
 */
function paintSavedDecks(data) {
  const body = document.getElementById("ls-saved-decks-body");
  if (!body) return;
  const decks = Array.isArray(data.saved_decks) ? data.saved_decks : [];
  body.replaceChildren();
  if (!decks.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.textContent = "No Google decks saved for this class yet.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const deck of decks) {
    const moduleN = Number(deck.module_number || 0);
    const liveI = Number(deck.live_index || 0);
    const href = safeDeckHref(deck.presentation_url);
    const tr = document.createElement("tr");
    const moduleTd = document.createElement("td");
    moduleTd.textContent = `Module ${moduleN}`;
    const classTd = document.createElement("td");
    classTd.textContent = `Class C${liveI}`;
    const linkTd = document.createElement("td");
    if (href) {
      const a = document.createElement("a");
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "Open slides";
      linkTd.appendChild(a);
    } else {
      linkTd.textContent = "Open slides";
    }
    tr.append(moduleTd, classTd, linkTd);
    body.appendChild(tr);
  }
}

/**
 * Load saved live-lesson files and paint the inventory table.
 * @returns {Promise<void>}
 */
async function loadLiveLessons() {
  const body = document.getElementById("ls-live-lessons-body");
  if (!classId || !body) return;
  const res = await fetch(`/api/classes/${classId}/live-lessons`, {
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  const lessons = Array.isArray(data.lessons) ? data.lessons : [];
  body.replaceChildren();
  if (!res.ok || !lessons.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "No saved live lesson files yet.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const lesson of lessons) {
    const tr = document.createElement("tr");
    const plain = [
      lesson.module || "",
      lesson.live_class || "",
      lesson.page_count ?? "",
      lesson.question_count ?? "",
      lesson.media_label || "—",
      lesson.artifact_label || "—",
    ];
    for (const value of plain) {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    }
    tr.appendChild(scanCell(lesson));
    body.appendChild(tr);
  }
}

/**
 * Scan cell: one-line pack summary plus question stems, no lesson open.
 * @param {Record<string, unknown>} lesson
 * @returns {HTMLTableCellElement}
 */
function scanCell(lesson) {
  const td = document.createElement("td");
  const line = document.createElement("p");
  line.className = "ls-scan-line";
  line.textContent = String(lesson.scan || "");
  td.appendChild(line);
  const questions = Array.isArray(lesson.questions) ? lesson.questions : [];
  if (!questions.length) return td;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Stems";
  details.appendChild(summary);
  const list = document.createElement("ul");
  for (const question of questions) {
    const item = document.createElement("li");
    const text = String(question?.text || question?.id || "").trim();
    item.textContent = text;
    list.appendChild(item);
  }
  details.appendChild(list);
  td.appendChild(details);
  return td;
}

/**
 * Paint preview summary, connected Lessons, and challenge defaults.
 * @param {Record<string, unknown>} data
 */
function paintPreview(data) {
  const status = document.getElementById("ls-preview-status");
  const list = document.getElementById("ls-preview-list");
  const codes = document.getElementById("ls-expect-codes");
  const preview = data.preview || data;
  if (status) status.textContent = String(preview.summary || "");
  if (list) {
    list.replaceChildren();
    for (const lesson of preview.connected_lessons || []) {
      const li = document.createElement("li");
      li.textContent = `Lesson ${lesson.lesson_number}: ${lesson.title || ""}`;
      list.appendChild(li);
    }
  }
  if (codes) {
    const found = preview.expectation_codes || [];
    codes.textContent = found.length
      ? `Expectations: ${found.join(", ")}`
      : "No seeded expectation codes matched these Lessons.";
  }
  const challenge = preview.team_challenge || {};
  const context = document.getElementById("ls-context");
  const question = document.getElementById("ls-question");
  const notes = document.getElementById("ls-notes");
  if (context && !context.value) context.value = String(challenge.context || "");
  if (question && !question.value) question.value = String(challenge.question || "");
  if (notes && !notes.value) notes.value = String(challenge.speaker_notes || "");
  const open = document.getElementById("ls-open");
  const rebuild = document.getElementById("ls-rebuild");
  const url = data.presentation_url || "";
  if (open) {
    open.hidden = !url;
    if (url) open.href = url;
  }
  if (rebuild) rebuild.hidden = !url;
  paintSavedDecks(data);
}

/**
 * Connect / skip chrome for the operator allowlist.
 * @param {Record<string, unknown>} status
 */
function paintConnect(status) {
  const connect = document.getElementById("ls-connect");
  const hint = document.getElementById("ls-status");
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  if (connect) {
    connect.href = status.connect_url || `/auth/google/slides?next=${next}`;
    connect.hidden = !status.allowed || Boolean(status.connected);
  }
  if (hint && !hint.textContent) {
    if (!status.allowed) {
      hint.textContent = "Google Slides connect is limited to solutions@ and Shawn's Gmail.";
    } else if (status.connected) {
      hint.textContent = status.mock
        ? "Slides connected in mock mode (HTML preview)."
        : "Google Slides connected.";
    }
  }
}

/**
 * GET preview + existing deck.
 * @returns {Promise<void>}
 */
async function loadPreview() {
  if (!classId) return;
  const res = await fetch(`/api/classes/${classId}/lesson-slides?${previewQuery()}`, {
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const status = document.getElementById("ls-preview-status");
    if (status) status.textContent = data.error || "Could not preview Lessons.";
    return;
  }
  paintPreview(data);
}

/**
 * POST copy + fill.
 * @param {boolean} force
 * @returns {Promise<void>}
 */
async function buildDeck(force) {
  if (!classId) return;
  const hint = document.getElementById("ls-status");
  if (hint) hint.textContent = force ? "Rebuilding deck…" : "Building deck…";
  const fields = wizardFields();
  const res = await fetch(`/api/classes/${classId}/lesson-slides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({
      ...fields,
      context: document.getElementById("ls-context")?.value || "",
      question: document.getElementById("ls-question")?.value || "",
      speaker_notes: document.getElementById("ls-notes")?.value || "",
      force_regenerate: Boolean(force),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 409 && data.needs_slides_connect) {
    if (hint) hint.textContent = "Connect Google Slides first.";
    const connect = document.getElementById("ls-connect");
    if (connect) {
      connect.hidden = false;
      if (data.connect_url) connect.href = data.connect_url;
    }
    return;
  }
  if (!res.ok) {
    if (hint) hint.textContent = data.error || "Could not build the deck.";
    return;
  }
  paintPreview(data);
  const notes = [];
  if (data.reused) notes.push("Opened the existing deck for this live class.");
  if (data.image_fallback_note) notes.push(data.image_fallback_note);
  if (hint) hint.textContent = notes.join(" ") || "Deck ready.";
}

document.getElementById("ls-preview")?.addEventListener("click", () => {
  loadPreview().catch((err) => {
    const status = document.getElementById("ls-preview-status");
    if (status) status.textContent = err?.message || "Preview failed.";
  });
});
document.getElementById("ls-build")?.addEventListener("click", () => {
  buildDeck(false).catch((err) => {
    const hint = document.getElementById("ls-status");
    if (hint) hint.textContent = err?.message || "Build failed.";
  });
});
document.getElementById("ls-rebuild")?.addEventListener("click", () => {
  buildDeck(true).catch((err) => {
    const hint = document.getElementById("ls-status");
    if (hint) hint.textContent = err?.message || "Rebuild failed.";
  });
});

const slidesReturn = new URLSearchParams(window.location.search).get("slides");
if (slidesReturn === "connected" || slidesReturn === "mock") {
  const hint = document.getElementById("ls-status");
  if (hint) {
    hint.textContent =
      slidesReturn === "mock"
        ? "Slides connected in mock mode (HTML preview)."
        : "Google Slides connected.";
  }
}

if (root && classId) {
  const next = encodeURIComponent(
    `/staff/class/${classId}?tab=lesson-slides`
  );
  fetch(`/api/auth/slides-status?next=/staff/class/${classId}?tab=lesson-slides`, {
    credentials: "same-origin",
  })
    .then((res) => res.json())
    .then((status) => {
      if (status.connect_url) {
        /* keep */
      } else {
        status.connect_url = `/auth/google/slides?next=${next}`;
      }
      paintConnect(status);
    })
    .catch(() => {});
  loadPreview().catch(() => {});
  loadLiveLessons().catch(() => {});
}
