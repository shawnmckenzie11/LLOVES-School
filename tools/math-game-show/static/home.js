import { api, hideError, showError } from "./common.js";

const STEPS = ["year", "semester", "course", "csv", "days", "time"];
const TITLES = {
  year: "School year",
  semester: "Semester",
  course: "Course code",
  csv: "Upload class CSV",
  days: "Days",
  time: "Time",
};

let step = 0;
let csvText = "";
let csvCount = 0;

const $ = (id) => document.getElementById(id);

/**
 * Show the home mode picker and hide wizard / existing-class panels.
 */
function showPicker() {
  $("mode-pick").classList.remove("hidden");
  $("wizard").classList.add("hidden");
  $("existing").classList.add("hidden");
  hideError("#error");
}

/**
 * Render one wizard field and hide the others.
 */
function renderStep() {
  const key = STEPS[step];
  $("wiz-progress").textContent = `Step ${step + 1} of ${STEPS.length}`;
  $("wiz-title").textContent = TITLES[key];
  for (const name of STEPS) {
    $(`step-${name}`).classList.toggle("hidden", name !== key);
  }
  $("wiz-back").textContent = step === 0 ? "Cancel" : "Back";
  $("wiz-next").textContent = step === STEPS.length - 1 ? "Create class" : "Next";
}

/**
 * Validate the current wizard step before advancing.
 * @returns {Promise<boolean>}
 */
async function validateStep() {
  hideError("#error");
  const key = STEPS[step];
  if (key === "year" && !$("year").value.trim()) {
    showError("#error", "Enter a school year (e.g. 2026/27).");
    return false;
  }
  if (key === "course" && !$("course").value.trim()) {
    showError("#error", "Enter a course code.");
    return false;
  }
  if (key === "csv") {
    if (!csvText) {
      showError("#error", "Choose a Canvas gradebook CSV.");
      return false;
    }
    try {
      const preview = await api("/api/csv/preview", {
        method: "POST",
        body: JSON.stringify({ csv_text: csvText }),
      });
      csvCount = preview.count;
      $("csv-status").textContent = `Imported preview: ${csvCount} students (Student + ID only).`;
    } catch (err) {
      showError("#error", err);
      return false;
    }
  }
  return true;
}

/**
 * Create the class from wizard fields and open the dashboard.
 */
async function submitClass() {
  const payload = {
    year: $("year").value.trim(),
    semester: $("semester").value,
    course_code: $("course").value.trim(),
    days: $("days").value,
    time: $("time").value,
    csv_text: csvText,
  };
  const data = await api("/api/classes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  location.href = `/class/${data.class.id}`;
}

/**
 * Load semester.json-backed defaults into the wizard.
 */
async function loadDefaults() {
  const data = await api("/api/defaults");
  $("year").value = data.year;
  $("semester").value = data.semester;
  $("course").value = data.course_code;
  $("time").innerHTML = data.time_options
    .map((t) => `<option value="${t}">${t}</option>`)
    .join("");
  $("time").value = "2:00pm";
}

/**
 * List classes for the current picker year/semester.
 */
async function loadExisting() {
  hideError("#error");
  const data = await api("/api/classes");
  $("existing-filter").textContent = `Showing ${data.year} · ${data.semester}`;
  const list = $("class-list");
  list.innerHTML = "";
  if (!data.classes.length) {
    list.textContent = `No classes for ${data.year} ${data.semester}.`;
    return;
  }
  for (const cls of data.classes) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `${cls.course_code} · ${cls.days} · ${cls.time}`;
    btn.addEventListener("click", () => {
      location.href = `/class/${cls.id}`;
    });
    list.appendChild(btn);
  }
}

$("btn-create").addEventListener("click", () => {
  $("mode-pick").classList.add("hidden");
  $("wizard").classList.remove("hidden");
  step = 0;
  csvText = "";
  $("csv").value = "";
  $("csv-status").textContent = "Keeps Student + ID only. Skips header / Points Possible rows.";
  renderStep();
});

$("btn-existing").addEventListener("click", async () => {
  $("mode-pick").classList.add("hidden");
  $("existing").classList.remove("hidden");
  try {
    await loadExisting();
  } catch (err) {
    showError("#error", err);
  }
});

$("exist-back").addEventListener("click", showPicker);

$("wiz-back").addEventListener("click", () => {
  if (step === 0) {
    showPicker();
    return;
  }
  step -= 1;
  renderStep();
});

$("wiz-next").addEventListener("click", async () => {
  if (!(await validateStep())) return;
  if (step === STEPS.length - 1) {
    try {
      await submitClass();
    } catch (err) {
      showError("#error", err);
    }
    return;
  }
  step += 1;
  renderStep();
});

$("csv").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  csvText = "";
  if (!file) return;
  csvText = await file.text();
  $("csv-status").textContent = `Selected ${file.name} (${Math.round(file.size / 1024)} KB).`;
});

loadDefaults().catch((err) => showError("#error", err));
