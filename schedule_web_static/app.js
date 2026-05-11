const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const START_HOUR = 8;
const END_HOUR = 23;

let appData = null;

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function minutes(value) {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
}

function timeLabel(total) {
  const hour = String(Math.floor(total / 60)).padStart(2, "0");
  const minute = String(total % 60).padStart(2, "0");
  return `${hour}:${minute}`;
}

function dayFromIso(value) {
  const date = new Date(value);
  return DAYS[(date.getDay() + 6) % 7];
}

function timeFromIso(value) {
  return value.slice(11, 16);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw payload;
  }
  return payload;
}

async function loadData() {
  setStatus("Loading schedule data...");
  appData = await fetchJson("/api/data");
  renderTable(appData.table_rows);
  renderVenueTable(appData.venue_rows);
  renderTravelTable(appData.travel_time_rows);
  renderTrainerTable(appData.trainer_availability_rows);
  renderSummary(appData.solution, appData.validation_warnings);
  renderCalendar(appData);
  setStatus("Ready");
}

function setStatus(text, isWarning = false) {
  const node = document.getElementById("statusText");
  node.textContent = text;
  node.className = isWarning ? "warning" : "";
}

function renderSummary(solution, warnings) {
  const summary = solution && solution.summary ? solution.summary : {};
  const items = [
    ["Status", solution ? solution.status : "No solution"],
    ["Scheduled", summary.scheduled_lessons ?? "-"],
    ["Unscheduled", summary.unscheduled_lessons ?? "-"],
    ["Changed", summary.changed_lessons ?? "-"],
    ["Candidates", summary.candidate_count ?? "-"],
  ];
  if (warnings && warnings.length > 0) {
    items[0][1] = `${items[0][1]} - ${warnings.length} warning(s)`;
  }
  document.getElementById("summaryBar").innerHTML = items.map(([label, value]) => `
    <div class="summary-item">
      <span class="summary-label">${esc(label)}</span>
      <span class="summary-value">${esc(value)}</span>
    </div>
  `).join("");
}

function renderTable(rows) {
  renderRows("studentTableBody", "studentRowTemplate", rows);
}

function renderVenueTable(rows) {
  renderRows("venueTableBody", "venueRowTemplate", rows);
}

function renderTravelTable(rows) {
  renderRows("travelTableBody", "travelRowTemplate", rows);
}

function renderTrainerTable(rows) {
  renderRows("trainerTableBody", "trainerRowTemplate", rows);
}

function renderRows(bodyId, templateId, rows) {
  const targetBody = document.getElementById(bodyId);
  targetBody.innerHTML = "";
  rows.forEach((row) => appendRow(bodyId, templateId, row));
}

function appendRow(bodyId, templateId, row = {}) {
  const body = document.getElementById(bodyId);
  const template = document.getElementById(templateId);
  const fragment = template.content.cloneNode(true);
  fragment.querySelectorAll("[data-field]").forEach((input) => {
    input.value = row[input.dataset.field] || "";
  });
  fragment.querySelector(".remove-row").addEventListener("click", (event) => {
    event.target.closest("tr").remove();
  });
  body.appendChild(fragment);
}

function collectTableRows(bodyId) {
  return [...document.querySelectorAll(`#${bodyId} tr`)].map((tr) => {
    const row = {};
    tr.querySelectorAll("[data-field]").forEach((input) => {
      row[input.dataset.field] = input.value;
    });
    return row;
  });
}

function collectRows() {
  return collectTableRows("studentTableBody");
}

function collectVenueRows() {
  return collectTableRows("venueTableBody");
}

function collectTravelRows() {
  return collectTableRows("travelTableBody");
}

function collectTrainerRows() {
  return collectTableRows("trainerTableBody");
}

function availabilityMap(windows) {
  const map = new Set();
  windows.forEach((window) => {
    const start = minutes(window.start);
    const end = minutes(window.end);
    for (let mark = start; mark < end; mark += 30) {
      map.add(`${window.day}-${mark}`);
    }
  });
  return map;
}

function lessonGroups(solution, lessons) {
  if (!solution || !solution.schedule) {
    return [];
  }
  const lessonById = new Map(lessons.map((lesson) => [lesson.lesson_id, lesson]));
  const groups = new Map();
  solution.schedule.forEach((item) => {
    const lesson = lessonById.get(item.lesson_id);
    const shared = lesson && lesson.shared_session_id;
    const key = shared
      ? `shared-${shared}-${item.start_datetime}-${item.end_datetime}-${item.venue_id}`
      : `lesson-${item.lesson_id}-${item.start_datetime}-${item.end_datetime}-${item.venue_id}`;
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key).push(item);
  });
  return [...groups.values()];
}

function renderCalendar(data) {
  const grid = document.getElementById("calendarGrid");
  const available = availabilityMap(data.coach_availability || []);
  const groupsByCell = new Map();

  lessonGroups(data.solution, data.lessons || []).forEach((group) => {
    const first = group[0];
    const day = dayFromIso(first.start_datetime);
    const start = minutes(timeFromIso(first.start_datetime));
    const key = `${day}-${start}`;
    if (!groupsByCell.has(key)) {
      groupsByCell.set(key, []);
    }
    groupsByCell.get(key).push(group);
  });

  let html = `<div class="calendar-cell calendar-head"></div>`;
  DAYS.forEach((day) => {
    html += `<div class="calendar-cell calendar-head">${esc(day)}</div>`;
  });

  for (let mark = START_HOUR * 60; mark <= END_HOUR * 60; mark += 30) {
    html += `<div class="calendar-cell time-cell">${esc(timeLabel(mark))}</div>`;
    DAYS.forEach((day) => {
      const key = `${day}-${mark}`;
      const classes = ["calendar-cell"];
      if (available.has(key)) {
        classes.push("available");
      }
      const groups = groupsByCell.get(key) || [];
      html += `<div class="${classes.join(" ")}">`;
      groups.forEach((group) => {
        const first = group[0];
        const names = group.map((item) => item.student_name).join(" / ");
        const changed = group.some((item) => item.is_changed);
        html += `
          <div class="lesson-block ${changed ? "changed" : ""}">
            <span class="lesson-title">${esc(names)}</span>
            <span class="lesson-meta">${esc(timeFromIso(first.start_datetime))}-${esc(timeFromIso(first.end_datetime))} - ${esc(first.venue_name)}</span>
          </div>`;
      });
      html += `</div>`;
    });
  }
  grid.innerHTML = html;
}

async function saveInput() {
  setStatus("Saving input...");
  try {
    await fetchJson("/api/venues/travel", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({venues: collectVenueRows(), travel_times: collectTravelRows()}),
    });
    await fetchJson("/api/trainer-availability", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectTrainerRows()}),
    });
    await fetchJson("/api/students/preferences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectRows()}),
    });
    await loadData();
    setStatus("Input saved");
  } catch (error) {
    const messages = (error.errors || []).map((item) => `row ${item.row} ${item.field}: ${item.message}`);
    setStatus(messages.join("; ") || "Save failed", true);
  }
}

async function runOptimizer() {
  setStatus("Running optimizer...");
  try {
    const payload = await fetchJson("/api/solve", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    if (!payload.ok) {
      setStatus((payload.validation_warnings || []).join("; ") || "Validation failed", true);
      return;
    }
    await loadData();
    setStatus("Optimizer finished");
  } catch (error) {
    setStatus(JSON.stringify(error), true);
  }
}

async function importCsvFiles() {
  const input = document.getElementById("csvFileInput");
  if (!input.files.length) {
    setStatus("Choose one or more scheduler CSV files first", true);
    return;
  }
  const formData = new FormData();
  [...input.files].forEach((file) => formData.append("files", file, file.name));
  setStatus("Importing CSV files...");
  try {
    const payload = await fetchJson("/api/import-csv", {
      method: "POST",
      body: formData,
    });
    await loadData();
    const warningCount = (payload.validation_warnings || []).length;
    setStatus(`Imported ${payload.imported_files.length} CSV file(s)${warningCount ? ` with ${warningCount} warning(s)` : ""}`);
    input.value = "";
  } catch (error) {
    const messages = (error.errors || []).map((item) => `${item.field}: ${item.message}`);
    setStatus(messages.join("; ") || "CSV import failed", true);
  }
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(`${button.dataset.view}View`).classList.add("active");
  });
});

document.getElementById("saveButton").addEventListener("click", saveInput);
document.getElementById("solveButton").addEventListener("click", runOptimizer);
document.getElementById("importCsvButton").addEventListener("click", importCsvFiles);
document.getElementById("addStudentButton").addEventListener("click", () => appendRow("studentTableBody", "studentRowTemplate", {
  student_id: "",
  student_name: "",
  available_timeslots: "",
  venues: "default=",
}));
document.getElementById("addVenueButton").addEventListener("click", () => appendRow("venueTableBody", "venueRowTemplate", {
  venue_id: "",
  venue_name: "",
}));
document.getElementById("addTravelButton").addEventListener("click", () => appendRow("travelTableBody", "travelRowTemplate", {
  from_venue_id: "",
  to_venue_id: "",
  travel_min: "0",
}));
document.getElementById("addTrainerButton").addEventListener("click", () => appendRow("trainerTableBody", "trainerRowTemplate", {
  day: "Mon",
  start: "10:00",
  end: "22:00",
  score: "0",
}));
loadData().catch((error) => setStatus(JSON.stringify(error), true));
