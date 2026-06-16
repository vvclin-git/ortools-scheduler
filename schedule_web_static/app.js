const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const START_HOUR = 8;
const END_HOUR = 23;

let appData = null;
let currentSolution = null;
let evaluationResult = null;
let evaluationVersion = 0;
let draggedGroupKey = null;
let draggedPreferenceGroupKey = null;
let selectedGroupKey = null;
let selectedTrayKey = null;
const activeCalendarOverlays = new Set(["trainer"]);
const PREFERENCE_FRAME_CLASSES = [
  "preference-frame-preferred",
  "preference-frame-acceptable",
  "preference-frame-last_resort",
  "preference-frame-join-prev",
  "preference-frame-join-next",
];
let trayVisible = true;
const dirtySections = new Set();
const savedSolutions = [];
const MAX_SAVED_SOLUTIONS = 5;

function esc(value) {
  return String(value ?? "")
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

function formatIsoLocal(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

function dateForDayAndTime(anchorIso, targetDay, targetMinutes) {
  const date = new Date(anchorIso);
  const mondayOffset = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - mondayOffset + DAYS.indexOf(targetDay));
  date.setHours(Math.floor(targetMinutes / 60), targetMinutes % 60, 0, 0);
  return date;
}

function planningAnchorIso() {
  return (appData && appData.config && appData.config.planning_start)
    || (currentSolution && currentSolution.schedule && currentSolution.schedule[0] && currentSolution.schedule[0].start_datetime)
    || new Date().toISOString();
}

function addMinutes(iso, amount) {
  const date = new Date(iso);
  date.setMinutes(date.getMinutes() + amount);
  return formatIsoLocal(date);
}

function durationMinutes(startIso, endIso) {
  return Math.round((new Date(endIso) - new Date(startIso)) / 60000);
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
  currentSolution = appData.solution ? structuredClone(appData.solution) : null;
  applyLessonRowsToSchedule();
  const syncResult = syncScheduleToCurrentInput();
  evaluationResult = null;
  renderAll();
  if (currentSolution) {
    await evaluateCurrentSchedule();
  }
  setStatus("Ready");
  return syncResult;
}

function renderAll() {
  renderRuntimeConfig();
  syncOverlayControls();
  renderTrayVisibility();
  renderTable(appData.table_rows);
  renderLessonTable(appData.lesson_rows || []);
  renderVenueTable(appData.venue_rows);
  renderTravelTable(appData.travel_time_rows);
  renderTrainerTable(appData.trainer_availability_rows);
  renderConfigTable(appData.config_rows || []);
  renderPreferenceScoreTable(appData.preference_score_rows || []);
  renderSavedSolutions();
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderCalendar(appData);
  renderDiagnostics();
}

function setStatus(text, isWarning = false) {
  const node = document.getElementById("statusText");
  node.textContent = text;
  node.className = isWarning ? "warning" : "";
}

function renderSummary(solution, warnings, evaluation) {
  const summary = solution && solution.summary ? solution.summary : {};
  const criticalCount = evaluation ? (evaluation.critical || []).length : 0;
  const warningCount = evaluation ? (evaluation.warnings || []).length : (warnings || []).length;
  const items = [
    ["Status", solution ? solution.status : "No solution"],
    ["Scheduled", summary.scheduled_lessons ?? "-"],
    ["Unscheduled", summary.unscheduled_lessons ?? "-"],
    ["Score", evaluation ? evaluation.score : (summary.objective_value ?? "-")],
    ["Critical", criticalCount],
    ["Warnings", warningCount],
  ];
  document.getElementById("summaryBar").innerHTML = items.map(([label, value]) => `
    <div class="summary-item">
      <span class="summary-label">${esc(label)}</span>
      <span class="summary-value">${esc(value)}</span>
    </div>
  `).join("");
}

function configRowMap() {
  return new Map((appData.config_rows || []).map((row) => [row.key, row.value]));
}

function currentWeekRuntimeValues() {
  const now = new Date();
  const monday = new Date(now);
  const mondayOffset = (now.getDay() + 6) % 7;
  monday.setDate(now.getDate() - mondayOffset);
  monday.setHours(9, 0, 0, 0);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  sunday.setHours(22, 0, 0, 0);
  return {
    planning_start: formatIsoLocal(monday),
    planning_end: formatIsoLocal(sunday),
    freeze_now: formatIsoLocal(now),
  };
}

function runtimeConfigValues() {
  const rows = configRowMap();
  const currentWeek = currentWeekRuntimeValues();
  return {
    mode: (appData.config && appData.config.mode) || rows.get("mode") || "weekly_planning",
    planning_start: currentWeek.planning_start,
    planning_end: currentWeek.planning_end,
    freeze_now: currentWeek.freeze_now,
  };
}

function setRuntimeConfigControls(values) {
  document.getElementById("runtimeMode").value = values.mode || "weekly_planning";
  document.getElementById("runtimePlanningStart").value = values.planning_start || "";
  document.getElementById("runtimePlanningEnd").value = values.planning_end || "";
  document.getElementById("runtimeFreezeNow").value = values.freeze_now || "";
}

function renderRuntimeConfig() {
  setRuntimeConfigControls(runtimeConfigValues());
}

function collectRuntimeConfig() {
  return {
    mode: document.getElementById("runtimeMode").value,
    planning_start: document.getElementById("runtimePlanningStart").value.trim(),
    planning_end: document.getElementById("runtimePlanningEnd").value.trim(),
    freeze_now: document.getElementById("runtimeFreezeNow").value.trim(),
  };
}

function renderDiagnostics() {
  const lines = [];
  if (!currentSolution) {
    lines.push("No optimized solution has been loaded.");
  } else {
    lines.push(`Solver status: ${currentSolution.status}`);
    if (currentSolution.summary) {
      lines.push(`Solver objective: ${currentSolution.summary.objective_value ?? "-"}`);
      lines.push(`Scheduled: ${currentSolution.summary.scheduled_lessons}, unscheduled: ${currentSolution.summary.unscheduled_lessons}, changed: ${currentSolution.summary.changed_lessons ?? "-"}`);
      if (currentSolution.summary.candidate_count !== undefined) {
        lines.push(`Candidate count: ${currentSolution.summary.candidate_count}`);
      }
      if (currentSolution.summary.solve_wall_time_seconds !== undefined && currentSolution.summary.solve_wall_time_seconds !== null) {
        lines.push(`Solve time: ${currentSolution.summary.solve_wall_time_seconds}s`);
      }
    }
    (currentSolution.warnings || []).forEach((item) => lines.push(`Solver warning: ${item}`));
    (currentSolution.changes || []).forEach((item) => {
      lines.push(`Change: ${item.lesson_id} ${item.change_type} ${item.old_start || "-"} -> ${item.new_start || "-"}`);
    });
  }
  if (evaluationResult) {
    lines.push("");
    lines.push(`Evaluator score: ${evaluationResult.score}`);
    (evaluationResult.critical || []).forEach((item) => lines.push(`Critical: ${item}`));
    (evaluationResult.warnings || []).forEach((item) => lines.push(`Warning: ${item}`));
    if (!(evaluationResult.critical || []).length && !(evaluationResult.warnings || []).length) {
      lines.push("Diagnostic: no manual schedule issues found");
    }
  }
  document.getElementById("diagnosticText").textContent = lines.join("\n");
}

function venueOptions(selected, useDefaultValue = false) {
  return (appData.venues || []).map((venue) => {
    const value = useDefaultValue ? `default=${venue.venue_id}` : venue.venue_id;
    const label = `${venue.venue_id} - ${venue.name}`;
    return `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
}

function studentOptions(selected) {
  const byId = new Map((appData.students || []).map((student) => [student.student_id, student.name]));
  if (appData) {
    collectRows().forEach((row) => {
      if (row.student_id && !byId.has(row.student_id)) {
        byId.set(row.student_id, row.student_name || row.student_id);
      }
    });
  }
  return [...byId.entries()].map(([studentId, name]) => {
    const label = `${studentId} - ${name}`;
    return `<option value="${esc(studentId)}" ${studentId === selected ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
}

function nextStudentId() {
  const ids = [
    ...(appData.students || []).map((student) => student.student_id),
    ...collectRows().map((row) => row.student_id),
  ];
  const maxId = ids
    .filter((id) => /^\d+$/.test(id))
    .map((id) => Number(id))
    .reduce((max, id) => Math.max(max, id), 1000);
  return String(maxId + 1);
}

function nextLessonIdForStudent(studentId) {
  const ids = [
    ...(appData.lesson_rows || []).map((row) => row.lesson_id),
    ...collectLessonRows().map((row) => row.lesson_id),
  ];
  const escaped = studentId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^${escaped}-(\\d+)$`);
  const maxIndex = ids
    .map((id) => id.match(pattern))
    .filter(Boolean)
    .map((match) => Number(match[1]))
    .reduce((max, value) => Math.max(max, value), 0);
  return `${studentId}-${maxIndex + 1}`;
}

function defaultVenueForStudent(row) {
  const explicit = row.venues || "";
  if (explicit.startsWith("default=")) {
    return explicit.slice("default=".length);
  }
  return appData.venues && appData.venues[0] ? appData.venues[0].venue_id : "";
}

function appendGeneratedLessonForStudent(studentRow, sharedSessionId = "") {
  const existingStudent = (appData.students || []).find((student) => student.student_id === studentRow.student_id);
  appendRow("lessonTableBody", "lessonRowTemplate", {
    lesson_id: nextLessonIdForStudent(studentRow.student_id),
    student_id: studentRow.student_id,
    venue_id: defaultVenueForStudent(studentRow),
    duration_min: "60",
    must_schedule: "FALSE",
    priority: String(existingStudent ? existingStudent.priority : 1),
    shared_session_id: sharedSessionId,
    booking_day: "",
    booking_start: "",
    booking_end: "",
    booking_status: "",
    booking_lock_level: "1",
  });
  markDirty("lessons");
}

function sharedSessionForGeneratedLesson(studentRow, lessonIndex, studentRows) {
  const couple = studentRow.couple || "";
  if (!couple) {
    return "";
  }
  const group = studentRows.filter((row) => row.couple && row.couple === couple);
  if (group.length < 2) {
    return "";
  }
  const sharedCount = Math.min(...group.map((row) => Math.max(0, Number(row.lessons_per_week) || 1)));
  return lessonIndex < sharedCount ? `${couple}-${lessonIndex + 1}` : "";
}

function renderTable(rows) {
  renderRows("studentTableBody", "studentRowTemplate", rows);
}

function renderLessonTable(rows) {
  renderRows("lessonTableBody", "lessonRowTemplate", rows);
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

function renderConfigTable(rows) {
  renderRows("configTableBody", "configRowTemplate", rows);
}

function renderPreferenceScoreTable(rows) {
  renderRows("preferenceScoreTableBody", "preferenceScoreRowTemplate", rows);
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
    if (input.tagName === "SELECT" && input.dataset.field === "venues") {
      input.innerHTML = venueOptions(row.venues || "", true);
    } else if (input.tagName === "SELECT" && input.dataset.field === "venue_id") {
      input.innerHTML = venueOptions(row.venue_id || "", false);
    } else if (input.tagName === "SELECT" && input.dataset.field === "student_id") {
      input.innerHTML = studentOptions(row.student_id || "");
    } else if (input.tagName === "SELECT" && input.dataset.field === "booking_status" && row.booking_status === "in_progress") {
      input.insertAdjacentHTML("beforeend", '<option value="in_progress" disabled>in_progress</option>');
    }
    input.value = row[input.dataset.field] || input.value || "";
    if (bodyId === "lessonTableBody" && input.dataset.field === "booking_end") {
      input.value = lessonRowEnd(row);
    }
  });
  const removeButton = fragment.querySelector(".remove-row");
  if (removeButton) {
    removeButton.addEventListener("click", (event) => {
      event.target.closest("tr").remove();
      markDirty(sectionForBody(bodyId));
    });
  }
  fragment.querySelectorAll("[data-field]").forEach((input) => {
    input.addEventListener("input", () => handleRowEdit(bodyId, input));
    input.addEventListener("change", () => handleRowEdit(bodyId, input));
  });
  body.appendChild(fragment);
  if (bodyId === "lessonTableBody") {
    updateLessonBookingControlState(body.lastElementChild);
  }
}

function markDirty(section) {
  if (!section) {
    return;
  }
  dirtySections.add(section);
  setStatus(`${section} has unsaved changes`, true);
}

function clearDirty(section) {
  dirtySections.delete(section);
}

function lessonRowEnd(row) {
  if (!row.booking_start) {
    return "";
  }
  const duration = Number(row.duration_min);
  if (!Number.isFinite(duration) || duration <= 0) {
    return "";
  }
  return timeFromIso(addMinutes(`2026-01-05T${row.booking_start}`, duration));
}

function updateLessonBookingControlState(row) {
  if (!row) {
    return;
  }
  const status = row.querySelector('[data-field="booking_status"]');
  const start = row.querySelector('[data-field="booking_start"]');
  const day = row.querySelector('[data-field="booking_day"]');
  const end = row.querySelector('[data-field="booking_end"]');
  const duration = row.querySelector('[data-field="duration_min"]');
  const rowData = {};
  row.querySelectorAll("[data-field]").forEach((input) => {
    rowData[input.dataset.field] = input.value;
  });
  if (end) {
    end.value = lessonRowEnd(rowData);
  }
  const fixed = status && ["completed", "locked", "in_progress"].includes(status.value);
  if (status) {
    status.disabled = status.value === "in_progress";
  }
  if (day) {
    day.disabled = fixed;
  }
  if (start) {
    start.disabled = fixed;
  }
  if (duration) {
    duration.disabled = fixed;
  }
}

function syncLessonRowsFromDom() {
  appData.lesson_rows = collectLessonRows();
  appData.lessons = appData.lesson_rows.map((row) => ({
    lesson_id: row.lesson_id,
    student_id: row.student_id,
    venue_id: row.venue_id,
    duration_min: Number(row.duration_min) || 0,
    must_schedule: row.must_schedule !== "FALSE",
    priority: Number(row.priority) || 0,
    shared_session_id: row.shared_session_id || null,
  }));
}

function generatedLessonRowsFromStudents() {
  const studentRows = collectRows();
  const rows = [];
  studentRows.forEach((studentRow) => {
    if (!studentRow.student_id) {
      return;
    }
    const targetCount = Math.max(0, Number(studentRow.lessons_per_week) || 1);
    const existingStudent = (appData.students || []).find((student) => student.student_id === studentRow.student_id);
    for (let index = 0; index < targetCount; index += 1) {
      rows.push({
        lesson_id: `${studentRow.student_id}-${index + 1}`,
        student_id: studentRow.student_id,
        venue_id: defaultVenueForStudent(studentRow),
        duration_min: "60",
        must_schedule: "FALSE",
        priority: String(existingStudent ? existingStudent.priority : 1),
        shared_session_id: sharedSessionForGeneratedLesson(studentRow, index, studentRows),
        booking_day: "",
        booking_start: "",
        booking_end: "",
        booking_status: "",
        booking_lock_level: "1",
      });
    }
  });
  return rows;
}

async function generateLessonsFromStudents() {
  setStatus("Regenerating and saving lessons...");
  const generatedRows = generatedLessonRowsFromStudents();
  renderLessonTable(generatedRows);
  syncLessonRowsFromDom();
  applyLessonRowsToSchedule();
  renderCalendar(appData);
  try {
    await fetchJson("/api/students/preferences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectRows()}),
    });
    clearDirty("students");
    await fetchJson("/api/lessons", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectLessonRows()}),
    });
    clearDirty("lessons");
    await loadData();
    setStatus(`Regenerated and saved ${generatedRows.length} lesson row(s); old bookings were cleared`);
  } catch (error) {
    markDirty("lessons");
    setStatus(formatErrorStatus(error, "Generate lessons failed"), true);
  }
}

function restoreUnsavedLessonRows(rows) {
  if (!rows) {
    return;
  }
  appData.lesson_rows = rows;
  renderLessonTable(appData.lesson_rows);
  syncLessonRowsFromDom();
  applyLessonRowsToSchedule();
  renderCalendar(appData);
}

function sectionForBody(bodyId) {
  if (bodyId === "studentTableBody") {
    return "students";
  }
  if (bodyId === "lessonTableBody") {
    return "lessons";
  }
  if (bodyId === "venueTableBody" || bodyId === "travelTableBody") {
    return "venues";
  }
  if (bodyId === "trainerTableBody") {
    return "trainer";
  }
  if (bodyId === "configTableBody") {
    return "config";
  }
  if (bodyId === "preferenceScoreTableBody") {
    return "preferenceScores";
  }
  return "";
}

function handleRowEdit(bodyId, input) {
  const section = sectionForBody(bodyId);
  if (section) {
    markDirty(section);
  }
  const row = input.closest("tr");
  if (bodyId === "lessonTableBody") {
    if (input.dataset.field === "duration_min" || input.dataset.field === "booking_start" || input.dataset.field === "booking_status") {
      updateLessonBookingControlState(row);
    }
    syncLessonRowsFromDom();
    applyLessonRowsToSchedule();
    invalidateScheduleEvaluation();
    renderCalendar(appData);
    evaluateCurrentSchedule({refreshCalendar: true}).catch((error) => setStatus(JSON.stringify(error), true));
  }
}

function invalidateScheduleEvaluation() {
  evaluationVersion += 1;
  evaluationResult = null;
  if (appData) {
    renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
    renderDiagnostics();
  }
  return evaluationVersion;
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

function selectedStudentRows() {
  return [...document.querySelectorAll("#studentTableBody tr")]
    .filter((row) => row.querySelector(".student-select")?.checked);
}

function selectedStudentIds() {
  return selectedStudentRows()
    .map((row) => row.querySelector('[data-field="student_id"]')?.value.trim() || "")
    .filter(Boolean);
}

function coupleKeyForStudentIds(studentIds) {
  const normalized = [...studentIds]
    .map((studentId) => studentId.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""))
    .filter(Boolean)
    .sort();
  return normalized.length ? `couple_${normalized.join("_")}` : "";
}

function setSelectedCoupleValue(value) {
  selectedStudentRows().forEach((row) => {
    const input = row.querySelector('[data-field="couple"]');
    if (input) {
      input.value = value;
    }
  });
  markDirty("students");
}

function makeSelectedStudentsCouple() {
  const studentIds = selectedStudentIds();
  if (studentIds.length < 2) {
    setStatus("Select at least two students with IDs to make a couple", true);
    return;
  }
  const coupleKey = coupleKeyForStudentIds(studentIds);
  setSelectedCoupleValue(coupleKey);
  setStatus(`Set ${studentIds.length} selected student(s) to ${coupleKey}`, true);
}

function clearSelectedStudentsCouple() {
  const selectedRows = selectedStudentRows();
  if (!selectedRows.length) {
    setStatus("Select at least one student to clear couple", true);
    return;
  }
  setSelectedCoupleValue("");
  setStatus(`Cleared couple for ${selectedRows.length} selected student(s)`, true);
}

function collectLessonRows() {
  return collectTableRows("lessonTableBody");
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

function collectConfigRows() {
  return collectTableRows("configTableBody");
}

function collectPreferenceScoreRows() {
  return collectTableRows("preferenceScoreTableBody");
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

function overlayEnabled(name) {
  return activeCalendarOverlays.has(name);
}

function syncOverlayControls() {
  const byValue = {
    trainer: document.getElementById("calendarOverlayTrainer"),
    selected_preferences: document.getElementById("calendarOverlayPreferences"),
    preference_hotzone: document.getElementById("calendarOverlayHotzone"),
  };
  Object.entries(byValue).forEach(([value, input]) => {
    if (input) {
      input.checked = activeCalendarOverlays.has(value);
    }
  });
}

function shouldShowPreferenceFrames() {
  return overlayEnabled("selected_preferences") || Boolean(draggedPreferenceGroupKey);
}

function preferenceFrameLevelForCell(day, mark) {
  if (!shouldShowPreferenceFrames()) {
    return "";
  }
  const studentIds = activePreferenceStudentIds();
  if (!studentIds.size) {
    return "";
  }
  const levels = {preferred: 3, acceptable: 2, last_resort: 1};
  let bestLevel = "";
  let bestRank = 0;
  (appData.preferences || []).forEach((pref) => {
    if (!studentIds.has(pref.student_id) || pref.day !== day) {
      return;
    }
    const start = minutes(pref.start);
    const end = minutes(pref.end);
    if (start <= mark && mark + 30 <= end) {
      const rank = levels[pref.level] || 1;
      if (rank > bestRank) {
        bestRank = rank;
        bestLevel = pref.level;
      }
    }
  });
  return bestLevel;
}

function preferenceFrameClassForCell(day, mark) {
  const level = preferenceFrameLevelForCell(day, mark);
  return level ? `preference-frame-${level}` : "";
}

function preferenceFrameClassesForCell(day, mark) {
  const level = preferenceFrameLevelForCell(day, mark);
  if (!level) {
    return [];
  }
  const classes = [`preference-frame-${level}`];
  if (preferenceFrameLevelForCell(day, mark - 30) === level) {
    classes.push("preference-frame-join-prev");
  }
  if (preferenceFrameLevelForCell(day, mark + 30) === level) {
    classes.push("preference-frame-join-next");
  }
  return classes;
}

function updateCalendarPreferenceFrames() {
  document.querySelectorAll(".calendar-cell[data-day]").forEach((cell) => {
    cell.classList.remove(...PREFERENCE_FRAME_CLASSES);
    const frameClasses = preferenceFrameClassesForCell(cell.dataset.day, Number(cell.dataset.minute));
    if (frameClasses.length) {
      cell.classList.add(...frameClasses);
    }
  });
}

function preferredStudentCountsByCell(preferences) {
  const counts = new Map();
  (preferences || []).forEach((pref) => {
    if (pref.level !== "preferred") {
      return;
    }
    const start = minutes(pref.start);
    const end = minutes(pref.end);
    for (let mark = start; mark < end; mark += 30) {
      const key = `${pref.day}-${mark}`;
      if (!counts.has(key)) {
        counts.set(key, new Set());
      }
      counts.get(key).add(pref.student_id);
    }
  });
  return new Map([...counts.entries()].map(([key, studentIds]) => [key, studentIds.size]));
}

function hotzoneClassForCount(count, maxCount) {
  if (!count || !maxCount) {
    return "";
  }
  const band = Math.max(1, Math.min(5, Math.ceil((count / maxCount) * 5)));
  return `hotzone-${band}`;
}

function selectedGroup() {
  return lessonGroups(currentSolution, appData ? appData.lessons || [] : [])
    .find((group) => group.key === selectedGroupKey) || null;
}

function selectedTrayGroup() {
  return selectedTrayKey ? trayGroupByKey(selectedTrayKey) : null;
}

function draggedPreferenceGroup() {
  if (!draggedPreferenceGroupKey) {
    return null;
  }
  const trayGroup = trayGroupByKey(draggedPreferenceGroupKey);
  if (trayGroup) {
    return {type: "tray", group: trayGroup};
  }
  const group = lessonGroups(currentSolution, appData ? appData.lessons || [] : [])
    .find((item) => item.key === draggedPreferenceGroupKey);
  return group ? {type: "scheduled", group} : null;
}

function activePreferenceStudentIds() {
  const dragged = draggedPreferenceGroup();
  if (dragged && dragged.type === "scheduled") {
    return new Set(dragged.group.items.map((item) => item.student_id));
  }
  if (dragged && dragged.type === "tray") {
    return new Set(dragged.group.lessons.map((lesson) => lesson.student_id));
  }
  const group = selectedGroup();
  if (group) {
    return new Set(group.items.map((item) => item.student_id));
  }
  const trayGroup = selectedTrayGroup();
  if (trayGroup) {
    return new Set(trayGroup.lessons.map((lesson) => lesson.student_id));
  }
  return new Set();
}

function updateSelectedStatusControl() {
  const select = document.getElementById("selectedStatus");
  if (!select) {
    return;
  }
  const group = selectedGroup();
  select.disabled = !group;
  select.value = group ? (group.items[0].booking_status || "draft") : "";
}

function lessonGroups(solution, lessons) {
  if (!solution || !solution.schedule) {
    return [];
  }
  const lessonById = new Map(lessons.map((lesson) => [lesson.lesson_id, lesson]));
  const groups = new Map();
  solution.schedule.forEach((item) => {
    const lesson = lessonById.get(item.lesson_id);
    const shared = item.shared_session_id || (lesson && lesson.shared_session_id);
    const key = shared
      ? `shared-${shared}-${item.start_datetime}-${item.end_datetime}-${item.venue_id}`
      : `lesson-${item.lesson_id}-${item.start_datetime}-${item.end_datetime}-${item.venue_id}`;
    if (!groups.has(key)) {
      groups.set(key, {key, items: []});
    }
    groups.get(key).items.push(item);
  });
  return [...groups.values()];
}

function unscheduledLessonGroups() {
  const scheduledIds = new Set(((currentSolution && currentSolution.schedule) || []).map((item) => item.lesson_id));
  const studentById = new Map((appData.students || []).map((student) => [student.student_id, student]));
  const venueById = new Map((appData.venues || []).map((venue) => [venue.venue_id, venue]));
  const groups = new Map();
  (appData.lessons || []).forEach((lesson) => {
    if (scheduledIds.has(lesson.lesson_id)) {
      return;
    }
    const shared = lesson.shared_session_id || "";
    const key = shared ? `tray-shared-${shared}` : `tray-lesson-${lesson.lesson_id}`;
    if (!groups.has(key)) {
      groups.set(key, {key, shared_session_id: shared, lessons: []});
    }
    const student = studentById.get(lesson.student_id) || {};
    const venue = venueById.get(lesson.venue_id) || {};
    groups.get(key).lessons.push({
      ...lesson,
      student_name: student.name || lesson.student_id,
      venue_name: venue.name || lesson.venue_id,
    });
  });
  return [...groups.values()];
}

function trayGroupByKey(key) {
  return unscheduledLessonGroups().find((group) => group.key === key) || null;
}

function diagnosticIssuesByLesson() {
  const byLesson = new Map();
  const addIssue = (lessonId, message) => {
    if (!byLesson.has(lessonId)) {
      byLesson.set(lessonId, []);
    }
    byLesson.get(lessonId).push(message);
  };
  const messages = evaluationResult
    ? [...(evaluationResult.critical || []), ...(evaluationResult.warnings || [])]
    : [];
  messages.forEach((message) => {
    const text = String(message);
    const pairMatch = text.match(/^([^:]+?)\s+(?:to|and)\s+([^:]+?):/);
    if (pairMatch) {
      addIssue(pairMatch[1].trim(), text);
      addIssue(pairMatch[2].trim(), text);
      return;
    }
    const lessonId = text.split(":")[0].trim();
    if (!lessonId || lessonId.startsWith("Row ")) {
      return;
    }
    addIssue(lessonId, text);
  });
  return byLesson;
}

function currentLesson(item) {
  return (appData.lessons || []).find((candidate) => candidate.lesson_id === item.lesson_id);
}

function lessonDuration(item) {
  const lesson = currentLesson(item);
  const duration = Number(lesson && lesson.duration_min);
  return Number.isFinite(duration) && duration > 0
    ? duration
    : durationMinutes(item.start_datetime, item.end_datetime);
}

function groupDuration(group) {
  return Math.max(...group.items.map((item) => lessonDuration(item)));
}

function lessonRowById() {
  return new Map((appData.lesson_rows || []).map((row) => [row.lesson_id, row]));
}

function scheduleItemFromLessonRow(row) {
  if (!row.booking_day || !row.booking_start || !row.booking_status) {
    return null;
  }
  const lesson = (appData.lessons || []).find((item) => item.lesson_id === row.lesson_id) || row;
  const student = (appData.students || []).find((item) => item.student_id === row.student_id) || {};
  const venue = (appData.venues || []).find((item) => item.venue_id === row.venue_id) || {};
  const start = formatIsoLocal(dateForDayAndTime(planningAnchorIso(), row.booking_day, minutes(row.booking_start)));
  const duration = Number(row.duration_min) || durationMinutes(start, addMinutes(start, 60));
  return {
    lesson_id: row.lesson_id,
    student_id: row.student_id,
    student_name: student.name || row.student_id || row.lesson_id,
    venue_id: row.venue_id,
    venue_name: venue.name || row.venue_id,
    start_datetime: start,
    end_datetime: addMinutes(start, duration),
    preference_level: "booking",
    preference_score: 0,
    is_changed: row.booking_dirty === "TRUE",
    shared_session_id: row.shared_session_id || (lesson && lesson.shared_session_id) || "",
    booking_status: row.booking_status || "",
  };
}

function applyLessonRowsToSchedule() {
  if (!appData) {
    return;
  }
  const bookingItems = (appData.lesson_rows || [])
    .map(scheduleItemFromLessonRow)
    .filter(Boolean);
  if (!bookingItems.length) {
    return;
  }
  currentSolution = currentSolution || {status: "BOOKINGS", summary: {}, schedule: [], changes: [], warnings: []};
  const byLessonId = new Map((currentSolution.schedule || []).map((item) => [item.lesson_id, item]));
  bookingItems.forEach((item) => byLessonId.set(item.lesson_id, item));
  currentSolution.status = currentSolution.status || "BOOKINGS";
  currentSolution.schedule = [...byLessonId.values()];
  currentSolution.summary = {
    ...(currentSolution.summary || {}),
    scheduled_lessons: currentSolution.schedule.length,
  };
}

function updateLessonRowsFromSchedule(items, status = "draft", dirty = true) {
  const byLessonId = lessonRowById();
  items.forEach((item) => {
    const row = byLessonId.get(item.lesson_id);
    if (!row) {
      return;
    }
    row.booking_day = dayFromIso(item.start_datetime);
    row.booking_start = timeFromIso(item.start_datetime);
    row.booking_end = timeFromIso(item.end_datetime);
    row.booking_status = dirty ? status : (row.booking_status || status);
    row.booking_dirty = dirty ? "TRUE" : "";
    row.booking_readonly = ["completed", "locked", "in_progress"].includes(row.booking_status) ? "TRUE" : "FALSE";
  });
}

function syncScheduleToCurrentInput() {
  if (!currentSolution || !currentSolution.schedule || !appData) {
    return {resizedCount: 0, updatedCount: 0, removedCount: 0};
  }
  const studentById = new Map((appData.students || []).map((student) => [student.student_id, student]));
  const venueById = new Map((appData.venues || []).map((venue) => [venue.venue_id, venue]));
  let resizedCount = 0;
  let updatedCount = 0;
  let removedCount = 0;
  const synced = [];
  currentSolution.schedule.forEach((item) => {
    const lesson = currentLesson(item);
    if (!lesson) {
      removedCount += 1;
      return;
    }
    const student = studentById.get(lesson.student_id);
    const venue = venueById.get(lesson.venue_id);
    const duration = Number(lesson.duration_min);
    const endDatetime = addMinutes(item.start_datetime, duration);
    const next = {
      ...item,
      student_id: lesson.student_id,
      student_name: student ? student.name : item.student_name,
      venue_id: lesson.venue_id,
      venue_name: venue ? venue.name : item.venue_name,
      shared_session_id: lesson.shared_session_id || "",
      booking_status: item.booking_status || (lessonRowById().get(item.lesson_id) || {}).booking_status || "",
      end_datetime: endDatetime,
    };
    if (item.end_datetime !== endDatetime) {
      resizedCount += 1;
      next.is_changed = true;
    }
    if (
      item.student_id !== next.student_id
      || item.student_name !== next.student_name
      || item.venue_id !== next.venue_id
      || item.venue_name !== next.venue_name
      || (item.shared_session_id || "") !== next.shared_session_id
    ) {
      updatedCount += 1;
      next.is_changed = true;
    }
    synced.push(next);
  });
  currentSolution.schedule = synced;
  if (currentSolution.summary) {
    currentSolution.summary.scheduled_lessons = synced.length;
  }
  return {resizedCount, updatedCount, removedCount};
}

function renderUnscheduledTray() {
  const tray = document.getElementById("unscheduledTray");
  const count = document.getElementById("unscheduledCount");
  if (!tray || !count) {
    return;
  }
  const groups = unscheduledLessonGroups();
  count.textContent = String(groups.reduce((total, group) => total + group.lessons.length, 0));
  if (!groups.length) {
    tray.innerHTML = `<div class="tray-empty">No unscheduled lessons</div>`;
    return;
  }
  tray.innerHTML = `
    <table class="tray-table">
      <thead>
        <tr>
          <th>lesson</th>
          <th>student</th>
          <th>duration</th>
          <th>venue</th>
        </tr>
      </thead>
      <tbody>
      ${groups.map((group) => {
    const names = group.lessons.map((lesson) => lesson.student_name).join(" / ");
    const venueNames = [...new Set(group.lessons.map((lesson) => lesson.venue_name))].join(" / ");
    const duration = Math.max(...group.lessons.map((lesson) => Number(lesson.duration_min) || 60));
    const label = group.shared_session_id ? `shared ${group.shared_session_id}` : group.lessons[0].lesson_id;
    return `
        <tr class="tray-lesson ${group.key === selectedTrayKey ? "selected" : ""}" draggable="true" data-tray-key="${esc(group.key)}">
          <td>${esc(label)}</td>
          <td>${esc(names)}</td>
          <td>${esc(duration)} min</td>
          <td>${esc(venueNames)}</td>
        </tr>`;
  }).join("")}
      </tbody>
    </table>`;
  document.querySelectorAll(".tray-lesson").forEach((block) => {
    block.addEventListener("dragstart", (event) => {
      draggedGroupKey = block.dataset.trayKey;
      draggedPreferenceGroupKey = draggedGroupKey;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedGroupKey);
      updateCalendarPreferenceFrames();
    });
    block.addEventListener("dragend", () => {
      draggedPreferenceGroupKey = null;
      draggedGroupKey = null;
      updateCalendarPreferenceFrames();
    });
    block.addEventListener("click", () => {
      selectedTrayKey = block.dataset.trayKey;
      selectedGroupKey = null;
      renderCalendar(appData);
    });
  });
}

function renderTrayVisibility() {
  const shell = document.querySelector(".calendar-shell");
  const button = document.getElementById("toggleTrayButton");
  if (!shell || !button) {
    return;
  }
  shell.classList.toggle("tray-hidden", !trayVisible);
  button.textContent = trayVisible ? "Hide Tray" : "Show Tray";
}

function toggleTrayVisibility() {
  trayVisible = !trayVisible;
  renderTrayVisibility();
}

function renderCalendar(data) {
  const grid = document.getElementById("calendarGrid");
  const available = availabilityMap(data.coach_availability || []);
  const hotzoneCounts = overlayEnabled("preference_hotzone")
    ? preferredStudentCountsByCell(data.preferences || [])
    : new Map();
  const maxHotzoneCount = hotzoneCounts.size ? Math.max(...hotzoneCounts.values()) : 0;
  const groupsByCell = new Map();
  const issuesByLesson = diagnosticIssuesByLesson();
  const blocks = [];

  lessonGroups(currentSolution, data.lessons || []).forEach((group) => {
    const first = group.items[0];
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
      const hotzoneCount = hotzoneCounts.get(key) || 0;
      const hotzoneClass = hotzoneClassForCount(hotzoneCount, maxHotzoneCount);
      const preferenceFrameClasses = preferenceFrameClassesForCell(day, mark);
      if (hotzoneClass) {
        classes.push(hotzoneClass);
      } else if (overlayEnabled("trainer") && available.has(key)) {
        classes.push("available");
      }
      if (preferenceFrameClasses.length) {
        classes.push(...preferenceFrameClasses);
      }
      const groups = groupsByCell.get(key) || [];
      const hotzoneTitle = hotzoneCount ? ` title="${esc(`${hotzoneCount} students prefer this slot`)}"` : "";
      html += `<div class="${classes.join(" ")}" data-day="${esc(day)}" data-minute="${mark}" data-hotzone-count="${hotzoneCount}"${hotzoneTitle}>`;
      groups.forEach((group) => {
        const first = group.items[0];
        const names = group.items.map((item) => item.student_name).join(" / ");
        const changed = group.items.some((item) => item.is_changed);
        const status = first.booking_status || "manual";
        const statusClass = `status-${status || "manual"}`;
        const selectedClass = group.key === selectedGroupKey ? "selected" : "";
        const issues = group.items.flatMap((item) => issuesByLesson.get(item.lesson_id) || []);
        const issueText = issues.length ? issues[0].split(":").slice(1).join(":").trim() || issues[0] : "";
        const issueHtml = issueText ? `<span class="lesson-issue">${esc(issueText)}</span>` : "";
        const dayIndex = DAYS.indexOf(day);
        const startIndex = Math.floor((mark - START_HOUR * 60) / 30);
        const rowSpan = Math.max(1, Math.ceil(groupDuration(group) / 30));
        blocks.push(`
          <div class="lesson-block ${statusClass} ${selectedClass} ${changed ? "changed" : ""}" draggable="true" data-group-key="${esc(group.key)}" style="--day-index:${dayIndex};--start-index:${startIndex};--row-span:${rowSpan};">
            <span class="lesson-title">${esc(names)}</span>
            <span class="lesson-status">${esc(status)}</span>
            <span class="lesson-meta">${esc(timeFromIso(first.start_datetime))}-${esc(timeFromIso(first.end_datetime))} - ${esc(first.venue_name)}</span>
            ${issueHtml}
          </div>`);
      });
      html += `</div>`;
    });
  }
  grid.innerHTML = html + blocks.join("");
  bindCalendarDragHandlers();
  renderUnscheduledTray();
  bindTrayDropHandlers();
  updateSelectedStatusControl();
}

function bindCalendarDragHandlers() {
  document.querySelectorAll(".lesson-block").forEach((block) => {
    block.addEventListener("click", () => {
      selectedGroupKey = block.dataset.groupKey;
      selectedTrayKey = null;
      renderCalendar(appData);
    });
    block.addEventListener("dragstart", (event) => {
      draggedGroupKey = block.dataset.groupKey;
      draggedPreferenceGroupKey = draggedGroupKey;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedGroupKey);
      updateCalendarPreferenceFrames();
    });
    block.addEventListener("dragend", () => {
      draggedPreferenceGroupKey = null;
      draggedGroupKey = null;
      updateCalendarPreferenceFrames();
    });
  });
  document.querySelectorAll(".calendar-cell[data-day]").forEach((cell) => {
    cell.addEventListener("dragover", (event) => {
      event.preventDefault();
      cell.classList.add("drop-target");
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("drop-target"));
    cell.addEventListener("drop", async (event) => {
      event.preventDefault();
      cell.classList.remove("drop-target");
      const key = event.dataTransfer.getData("text/plain") || draggedGroupKey;
      try {
        await moveGroupTo(key, cell.dataset.day, Number(cell.dataset.minute));
      } finally {
        draggedPreferenceGroupKey = null;
        draggedGroupKey = null;
        renderCalendar(appData);
      }
    });
  });
}

function bindTrayDropHandlers() {
  const tray = document.querySelector(".unscheduled-tray");
  if (!tray) {
    return;
  }
  if (tray.dataset.dropBound === "true") {
    return;
  }
  tray.dataset.dropBound = "true";
  tray.addEventListener("dragover", (event) => {
    if (!draggedGroupKey || trayGroupByKey(draggedGroupKey)) {
      return;
    }
    event.preventDefault();
    tray.classList.add("drop-target");
  });
  tray.addEventListener("dragleave", () => tray.classList.remove("drop-target"));
  tray.addEventListener("drop", async (event) => {
    event.preventDefault();
    tray.classList.remove("drop-target");
    const key = event.dataTransfer.getData("text/plain") || draggedGroupKey;
    try {
      await unscheduleGroupToTray(key);
    } finally {
      draggedPreferenceGroupKey = null;
      draggedGroupKey = null;
      renderCalendar(appData);
    }
  });
}

function scheduleItemsFromTrayGroup(group, day, startMinute) {
  const studentById = new Map((appData.students || []).map((student) => [student.student_id, student]));
  const venueById = new Map((appData.venues || []).map((venue) => [venue.venue_id, venue]));
  const anchor = planningAnchorIso();
  const newStart = formatIsoLocal(dateForDayAndTime(anchor, day, startMinute));
  const duration = Math.max(...group.lessons.map((lesson) => Number(lesson.duration_min) || 60));
  const newEnd = addMinutes(newStart, duration);
  return group.lessons.map((lesson) => {
    const student = studentById.get(lesson.student_id) || {};
    const venue = venueById.get(lesson.venue_id) || {};
    return {
      lesson_id: lesson.lesson_id,
      student_id: lesson.student_id,
      student_name: student.name || lesson.student_id,
      venue_id: lesson.venue_id,
      venue_name: venue.name || lesson.venue_id,
      start_datetime: newStart,
      end_datetime: newEnd,
      preference_level: "manual",
      preference_score: 0,
      is_changed: true,
      shared_session_id: lesson.shared_session_id || "",
      booking_status: "draft",
    };
  });
}

function clearLessonRowsFromSchedule(lessonIds) {
  (appData.lesson_rows || []).forEach((row) => {
    if (!lessonIds.has(row.lesson_id)) {
      return;
    }
    row.booking_day = "";
    row.booking_start = "";
    row.booking_end = "";
    row.booking_status = "";
    row.booking_dirty = "";
    row.booking_readonly = "FALSE";
  });
}

async function unscheduleGroupToTray(groupKey) {
  if (!groupKey || trayGroupByKey(groupKey) || !currentSolution || !currentSolution.schedule) {
    return;
  }
  const group = lessonGroups(currentSolution, appData.lessons || []).find((item) => item.key === groupKey);
  if (!group) {
    return;
  }
  const fixedStatuses = new Set(["completed", "locked", "in_progress"]);
  const rowsById = lessonRowById();
  const hasFixed = group.items.some((item) => {
    const row = rowsById.get(item.lesson_id) || {};
    const status = item.booking_status || row.booking_status || "";
    return fixedStatuses.has(status);
  });
  if (hasFixed) {
    setStatus("Completed, locked, and in-progress lessons cannot be returned to the tray", true);
    return;
  }
  const lessonIds = new Set(group.items.map((item) => item.lesson_id));
  currentSolution.status = "MANUAL";
  currentSolution.schedule = (currentSolution.schedule || []).filter((item) => !lessonIds.has(item.lesson_id));
  currentSolution.summary = {
    ...(currentSolution.summary || {}),
    scheduled_lessons: currentSolution.schedule.length,
    unscheduled_lessons: Math.max(0, (appData.lessons || []).length - currentSolution.schedule.length),
  };
  clearLessonRowsFromSchedule(lessonIds);
  selectedGroupKey = null;
  selectedTrayKey = null;
  invalidateScheduleEvaluation();
  renderLessonTable(appData.lesson_rows || []);
  markDirty("lessons");
  renderCalendar(appData);
  await evaluateCurrentSchedule({refreshCalendar: true});
}

async function moveGroupTo(groupKey, day, startMinute) {
  if (!groupKey) {
    return;
  }
  currentSolution = currentSolution || {status: "MANUAL", summary: {}, schedule: [], changes: [], warnings: []};
  currentSolution.status = "MANUAL";
  const trayGroup = trayGroupByKey(groupKey);
  let movedItems = [];
  if (trayGroup) {
    movedItems = scheduleItemsFromTrayGroup(trayGroup, day, startMinute);
    const movedIds = new Set(movedItems.map((item) => item.lesson_id));
    currentSolution.schedule = [
      ...(currentSolution.schedule || []).filter((item) => !movedIds.has(item.lesson_id)),
      ...movedItems,
    ];
    selectedTrayKey = null;
  } else {
    const group = lessonGroups(currentSolution, appData.lessons || []).find((item) => item.key === groupKey);
    if (!group) {
      return;
    }
    const first = group.items[0];
    const newStart = formatIsoLocal(dateForDayAndTime(first.start_datetime, day, startMinute));
    const duration = groupDuration(group);
    const newEnd = addMinutes(newStart, duration);
    const lessonIds = new Set(group.items.map((item) => item.lesson_id));
    currentSolution.schedule = currentSolution.schedule.map((item) => {
      if (!lessonIds.has(item.lesson_id)) {
        return item;
      }
      const moved = {
        ...item,
        start_datetime: newStart,
        end_datetime: newEnd,
        is_changed: true,
      };
      movedItems.push(moved);
      return moved;
    });
  }
  selectedGroupKey = null;
  currentSolution.summary = {
    ...(currentSolution.summary || {}),
    scheduled_lessons: currentSolution.schedule.length,
    unscheduled_lessons: Math.max(0, (appData.lessons || []).length - currentSolution.schedule.length),
  };
  updateLessonRowsFromSchedule(movedItems, "draft", true);
  invalidateScheduleEvaluation();
  renderLessonTable(appData.lesson_rows || []);
  markDirty("lessons");
  renderCalendar(appData);
  await evaluateCurrentSchedule({refreshCalendar: true});
}

function schedulePlacements() {
  if (!currentSolution || !currentSolution.schedule) {
    return [];
  }
  const lessonById = new Map((appData.lessons || []).map((lesson) => [lesson.lesson_id, lesson]));
  const rowsById = lessonRowById();
  return currentSolution.schedule.map((item) => {
    const lesson = lessonById.get(item.lesson_id);
    const row = rowsById.get(item.lesson_id) || {};
    return {
      lesson_id: item.lesson_id,
      student_id: item.student_id || (lesson && lesson.student_id) || "",
      start_datetime: item.start_datetime,
      end_datetime: item.end_datetime,
      venue_id: item.venue_id,
      shared_session_id: item.shared_session_id || (lesson && lesson.shared_session_id) || "",
      booking_id: row.booking_id || `book_${item.lesson_id}`,
      booking_status: item.booking_status || row.booking_status || "",
      booking_lock_level: row.booking_lock_level || "1",
    };
  });
}

async function evaluateCurrentSchedule(options = {}) {
  const {refreshCalendar = false} = options;
  const requestVersion = evaluationVersion;
  if (!currentSolution) {
    evaluationResult = null;
    renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
    renderDiagnostics();
    if (refreshCalendar) {
      renderCalendar(appData);
    }
    return;
  }
  const result = await fetchJson("/api/evaluate-schedule", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({schedule: schedulePlacements()}),
  });
  if (requestVersion !== evaluationVersion) {
    return;
  }
  evaluationResult = result;
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderDiagnostics();
  if (refreshCalendar) {
    renderCalendar(appData);
  }
}

async function saveLessonRowsNow(statusText) {
  syncLessonRowsFromDom();
  await fetchJson("/api/lessons", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({rows: collectLessonRows()}),
  });
  clearDirty("lessons");
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderCalendar(appData);
  renderDiagnostics();
  setStatus(statusText);
}

async function cleanSchedule() {
  setStatus("Cleaning schedule...");
  (appData.lesson_rows || []).forEach((row) => {
    row.booking_id = "";
    row.booking_day = "";
    row.booking_start = "";
    row.booking_end = "";
    row.booking_status = "";
    row.booking_lock_level = "1";
    row.booking_dirty = "";
    row.booking_readonly = "FALSE";
  });
  currentSolution = null;
  selectedGroupKey = null;
  selectedTrayKey = null;
  invalidateScheduleEvaluation();
  renderLessonTable(appData.lesson_rows || []);
  renderSavedSolutions();
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderCalendar(appData);
  renderDiagnostics();
  markDirty("lessons");
  try {
    await saveLessonRowsNow("Schedule cleaned and saved");
  } catch (error) {
    setStatus(formatErrorStatus(error, "Clean schedule failed"), true);
  }
}

async function updateSelectedStatus() {
  const group = selectedGroup();
  const status = document.getElementById("selectedStatus").value;
  if (!group || !status) {
    return;
  }
  currentSolution.status = "MANUAL";
  const lessonIds = new Set(group.items.map((item) => item.lesson_id));
  currentSolution.schedule = (currentSolution.schedule || []).map((item) => (
    lessonIds.has(item.lesson_id)
      ? {...item, booking_status: status, is_changed: true}
      : item
  ));
  updateLessonRowsFromSchedule(currentSolution.schedule.filter((item) => lessonIds.has(item.lesson_id)), status, true);
  invalidateScheduleEvaluation();
  renderLessonTable(appData.lesson_rows || []);
  renderCalendar(appData);
  try {
    await saveLessonRowsNow(`Status changed to ${status}`);
  } catch (error) {
    setStatus(formatErrorStatus(error, "Status save failed"), true);
  }
}

function normalizedScheduleText() {
  return schedulePlacements()
    .map((item) => [
      item.lesson_id,
      item.start_datetime,
      item.end_datetime,
      item.venue_id,
      item.booking_status || "",
    ].join("|"))
    .sort()
    .join("\n");
}

function scheduleHash() {
  const text = normalizedScheduleText();
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0").slice(0, 6);
}

function savedSolutionLabel(hash) {
  const summary = currentSolution && currentSolution.summary ? currentSolution.summary : {};
  const scheduled = summary.scheduled_lessons ?? (currentSolution && currentSolution.schedule ? currentSolution.schedule.length : 0);
  const unscheduled = summary.unscheduled_lessons ?? Math.max(0, (appData.lessons || []).length - scheduled);
  const score = evaluationResult ? evaluationResult.score : (summary.objective_value ?? "-");
  return `${hash} | score ${score} | ${scheduled}/${unscheduled}`;
}

function renderSavedSolutions() {
  const container = document.getElementById("savedSolutions");
  container.innerHTML = "";
  savedSolutions.forEach((saved, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary saved-solution-button";
    button.textContent = saved.label;
    button.title = "Switch to this saved solution";
    button.addEventListener("click", () => restoreSavedSolution(index));
    container.appendChild(button);
  });
}

async function saveVisibleSolution() {
  if (!currentSolution || !currentSolution.schedule || !currentSolution.schedule.length) {
    setStatus("No visible schedule to save", true);
    return;
  }
  await evaluateCurrentSchedule({refreshCalendar: true});
  const criticalIssues = evaluationResult ? (evaluationResult.critical || []) : [];
  if (criticalIssues.length) {
    setStatus(`Save solution blocked by ${criticalIssues.length} critical issue(s)`, true);
    return;
  }
  if (savedSolutions.length >= MAX_SAVED_SOLUTIONS) {
    const confirmed = window.confirm("Saving this solution will remove the oldest saved solution. Continue?");
    if (!confirmed) {
      return;
    }
    savedSolutions.shift();
  }
  const hash = scheduleHash();
  savedSolutions.push({
    hash,
    label: savedSolutionLabel(hash),
    solution: structuredClone(currentSolution),
    lessonRows: structuredClone(appData.lesson_rows || []),
    evaluation: evaluationResult ? structuredClone(evaluationResult) : null,
  });
  renderSavedSolutions();
  setStatus(`Saved solution ${hash}`);
}

async function restoreSavedSolution(index) {
  const saved = savedSolutions[index];
  if (!saved) {
    return;
  }
  currentSolution = structuredClone(saved.solution);
  appData.lesson_rows = structuredClone(saved.lessonRows || []);
  renderLessonTable(appData.lesson_rows);
  syncLessonRowsFromDom();
  syncScheduleToCurrentInput();
  invalidateScheduleEvaluation();
  markDirty("lessons");
  renderSavedSolutions();
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderCalendar(appData);
  renderDiagnostics();
  await evaluateCurrentSchedule({refreshCalendar: true});
  setStatus(`Switched to solution ${saved.hash}`);
}

function csvEscape(value) {
  const text = String(value ?? "");
  if (/[",\n\r]/.test(text)) {
    return `"${text.replaceAll('"', '""')}"`;
  }
  return text;
}

function lockLevelForStatus(status, existing = "1") {
  const parsed = Number(existing);
  const base = Number.isFinite(parsed) && parsed >= 0 ? parsed : 1;
  if (status === "locked") {
    return String(Math.max(base, 3));
  }
  return String(base);
}

function bookingRowsFromVisibleSchedule() {
  const lessonById = new Map((appData.lessons || []).map((lesson) => [lesson.lesson_id, lesson]));
  const rowsById = lessonRowById();
  return schedulePlacements().map((item) => {
    const lesson = lessonById.get(item.lesson_id) || {};
    const row = rowsById.get(item.lesson_id) || {};
    const status = item.booking_status || row.booking_status || "draft";
    return {
      booking_id: row.booking_id || item.booking_id || `book_${item.lesson_id}`,
      lesson_id: item.lesson_id,
      student_id: item.student_id || lesson.student_id || "",
      venue_id: item.venue_id,
      start_datetime: item.start_datetime,
      end_datetime: item.end_datetime,
      status,
      lock_level: lockLevelForStatus(status, row.booking_lock_level || item.booking_lock_level || "1"),
    };
  });
}

function exportScheduleCsv() {
  const headers = ["booking_id", "lesson_id", "student_id", "venue_id", "start_datetime", "end_datetime", "status", "lock_level"];
  const lines = [headers.join(",")];
  bookingRowsFromVisibleSchedule().forEach((row) => {
    lines.push(headers.map((header) => csvEscape(row[header])).join(","));
  });
  const blob = new Blob([`${lines.join("\n")}\n`], {type: "text/csv"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "existing_bookings.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (quoted) {
      if (char === '"' && next === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(cell);
      cell = "";
    } else if (char === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (char !== "\r") {
      cell += char;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((item) => item.some((cellValue) => cellValue.trim()));
}

async function importScheduleCsvFile(file) {
  const rows = parseCsv(await file.text());
  const headers = rows.shift() || [];
  const required = ["booking_id", "lesson_id", "student_id", "venue_id", "start_datetime", "end_datetime", "status", "lock_level"];
  if (required.some((header) => !headers.includes(header))) {
    setStatus("Bookings CSV is missing required existing_bookings.csv columns", true);
    return;
  }
  const imported = rows.map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] || ""])));
  const lessonById = new Map((appData.lessons || []).map((lesson) => [lesson.lesson_id, lesson]));
  const studentById = new Map((appData.students || []).map((student) => [student.student_id, student]));
  const venueById = new Map((appData.venues || []).map((venue) => [venue.venue_id, venue]));
  const rowsById = lessonRowById();
  currentSolution = currentSolution || {status: "MANUAL", summary: {}, schedule: [], changes: [], warnings: []};
  currentSolution.status = "MANUAL";
  currentSolution.schedule = imported.map((row) => {
    const lesson = lessonById.get(row.lesson_id) || {};
    const studentId = row.student_id || lesson.student_id || "";
    const student = studentById.get(studentId) || {};
    const venue = venueById.get(row.venue_id) || {};
    const lessonRow = rowsById.get(row.lesson_id);
    if (lessonRow) {
      lessonRow.booking_id = row.booking_id || `book_${row.lesson_id}`;
      lessonRow.booking_day = dayFromIso(row.start_datetime);
      lessonRow.booking_start = timeFromIso(row.start_datetime);
      lessonRow.booking_end = timeFromIso(row.end_datetime);
      lessonRow.booking_status = row.status || "draft";
      lessonRow.booking_lock_level = row.lock_level || "1";
      lessonRow.booking_dirty = "TRUE";
    }
    return {
      lesson_id: row.lesson_id,
      student_id: studentId,
      student_name: student.name || row.lesson_id,
      venue_id: row.venue_id,
      venue_name: venue.name || row.venue_id,
      start_datetime: row.start_datetime,
      end_datetime: row.end_datetime,
      preference_level: "manual",
      preference_score: 0,
      is_changed: true,
      shared_session_id: lesson.shared_session_id || "",
      booking_status: row.status || "draft",
    };
  });
  currentSolution.summary = {
    ...(currentSolution.summary || {}),
    scheduled_lessons: currentSolution.schedule.length,
  };
  invalidateScheduleEvaluation();
  renderLessonTable(appData.lesson_rows || []);
  markDirty("lessons");
  renderCalendar(appData);
  await evaluateCurrentSchedule({refreshCalendar: true});
  setStatus(`Imported ${currentSolution.schedule.length} booking row(s); click Save Lessons to persist`);
}

function formatErrorStatus(error, fallback) {
  const messages = (error.errors || []).map((item) => `row ${item.row} ${item.field}: ${item.message}`);
  return messages.join("; ") || fallback;
}

function clearScheduleState() {
  currentSolution = null;
  selectedGroupKey = null;
  selectedTrayKey = null;
  invalidateScheduleEvaluation();
}

async function cleanStudents() {
  setStatus("Clearing students, lessons, bookings, and solution...");
  try {
    await fetchJson("/api/clear-students", {method: "POST"});
    clearScheduleState();
    clearDirty("students");
    clearDirty("lessons");
    const result = await loadData();
    renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
    renderDiagnostics();
    setStatus(`Students cleared; removed ${result.removedCount || 0} visible placement(s)`);
  } catch (error) {
    setStatus(formatErrorStatus(error, "Clean students failed"), true);
  }
}

async function cleanLessons() {
  setStatus("Clearing lessons, bookings, and solution...");
  try {
    await fetchJson("/api/clear-lessons", {method: "POST"});
    clearScheduleState();
    clearDirty("lessons");
    const result = await loadData();
    renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
    renderDiagnostics();
    setStatus(`Lessons cleared; removed ${result.removedCount || 0} visible placement(s)`);
  } catch (error) {
    setStatus(formatErrorStatus(error, "Clean lessons failed"), true);
  }
}

function setAllMustSchedule(value) {
  document.querySelectorAll('#lessonTableBody [data-field="must_schedule"]').forEach((input) => {
    input.value = value;
  });
  syncLessonRowsFromDom();
  markDirty("lessons");
  setStatus(value === "TRUE" ? "All lessons marked required; click Save Lessons to persist" : "All lessons marked optional; click Save Lessons to persist", true);
}

async function saveStudents() {
  setStatus("Saving students...");
  try {
    const beforeIds = new Set((appData.students || []).map((student) => student.student_id));
    const afterIds = new Set(collectRows().map((row) => row.student_id).filter(Boolean));
    const studentIdsChanged = beforeIds.size !== afterIds.size || [...beforeIds].some((studentId) => !afterIds.has(studentId));
    const unsavedLessonRows = dirtySections.has("lessons") && !studentIdsChanged ? collectLessonRows() : null;
    await fetchJson("/api/students/preferences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectRows()}),
    });
    if (studentIdsChanged) {
      await fetchJson("/api/clear-lessons", {method: "POST"});
      clearScheduleState();
      clearDirty("lessons");
    }
    clearDirty("students");
    await loadData();
    restoreUnsavedLessonRows(unsavedLessonRows);
    setStatus(studentIdsChanged ? "Students saved; lessons and bookings were cleared because student IDs changed" : "Students saved");
  } catch (error) {
    setStatus(formatErrorStatus(error, "Save students failed"), true);
  }
}

async function saveLessons() {
  setStatus("Saving lessons...");
  try {
    syncLessonRowsFromDom();
    await fetchJson("/api/lessons", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectLessonRows()}),
    });
    clearDirty("lessons");
    const result = await loadData();
    const details = [];
    if (result.updatedCount) {
      details.push(`updated ${result.updatedCount} visible placement(s)`);
    }
    if (result.resizedCount) {
      details.push(`resized ${result.resizedCount} visible placement(s)`);
    }
    if (result.removedCount) {
      details.push(`removed ${result.removedCount} stale placement(s)`);
    }
    setStatus(`Lessons saved${details.length ? `; ${details.join("; ")}` : ""}`);
  } catch (error) {
    setStatus(formatErrorStatus(error, "Save lessons failed"), true);
  }
}

async function saveVenuesAndTravel() {
  setStatus("Saving venues and travel...");
  try {
    await fetchJson("/api/venues/travel", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({venues: collectVenueRows(), travel_times: collectTravelRows()}),
    });
    clearDirty("venues");
    await loadData();
    setStatus("Venues and travel saved");
  } catch (error) {
    setStatus(formatErrorStatus(error, "Save venues and travel failed"), true);
  }
}

async function saveTrainerAvailability() {
  setStatus("Saving trainer timeslots...");
  try {
    await fetchJson("/api/trainer-availability", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectTrainerRows()}),
    });
    clearDirty("trainer");
    await loadData();
    setStatus("Trainer timeslots saved");
  } catch (error) {
    setStatus(formatErrorStatus(error, "Save trainer timeslots failed"), true);
  }
}

async function savePreferenceScores() {
  setStatus("Saving preference scores...");
  try {
    await fetchJson("/api/preference-scores", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectPreferenceScoreRows()}),
    });
    clearDirty("preferenceScores");
    await loadData();
    setStatus("Preference scores saved");
  } catch (error) {
    setStatus(formatErrorStatus(error, "Save preference scores failed"), true);
  }
}

function resetConfigDefaults() {
  renderConfigTable(appData.default_config_rows || []);
  markDirty("config");
  setStatus("Config reset to defaults in the editor; click Save Config to persist", true);
}

async function saveConfigParameters() {
  setStatus("Saving config parameters...");
  try {
    await fetchJson("/api/config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectConfigRows()}),
    });
    clearDirty("config");
    await loadData();
    setStatus("Config parameters saved");
  } catch (error) {
    setStatus(formatErrorStatus(error, "Save config failed"), true);
  }
}

async function runOptimizer() {
  setStatus("Running optimizer...");
  const runtimeConfig = collectRuntimeConfig();
  try {
    const payload = await fetchJson("/api/solve", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({runtime_config: runtimeConfig}),
    });
    if (!payload.ok) {
      if (payload.solution) {
        currentSolution = structuredClone(payload.solution);
      } else {
        currentSolution = {
          status: "VALIDATION_FAILED",
          summary: null,
          schedule: [],
          changes: [],
          warnings: payload.validation_warnings || [],
        };
      }
      evaluationResult = null;
      renderSummary(currentSolution, payload.validation_warnings || [], evaluationResult);
      renderCalendar(appData);
      renderDiagnostics();
      setStatus((payload.validation_warnings || []).join("; ") || "Validation failed", true);
      return;
    }
    await loadData();
    appData.config = {...(appData.config || {}), ...runtimeConfig};
    setRuntimeConfigControls(runtimeConfig);
    if (currentSolution && currentSolution.schedule) {
      updateLessonRowsFromSchedule(currentSolution.schedule, "draft", true);
      renderLessonTable(appData.lesson_rows || []);
      applyLessonRowsToSchedule();
      markDirty("lessons");
      renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
      renderCalendar(appData);
      renderDiagnostics();
    }
    setStatus("Optimizer finished; review and save Lessons to persist draft booking times");
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

async function importStudentCsvFiles() {
  const input = document.getElementById("studentCsvInput");
  if (!input.files.length) {
    setStatus("Choose students.csv, preferences.csv, or both first", true);
    return;
  }
  const allowed = new Set(["students.csv", "preferences.csv"]);
  const files = [...input.files];
  const unsupported = files.filter((file) => !allowed.has(file.name));
  if (unsupported.length) {
    setStatus(`Students import only accepts students.csv and preferences.csv; rejected ${unsupported.map((file) => file.name).join(", ")}`, true);
    input.value = "";
    return;
  }
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file, file.name));
  setStatus("Importing student CSV files...");
  try {
    const payload = await fetchJson("/api/import-csv?scope=students", {
      method: "POST",
      body: formData,
    });
    await loadData();
    const warningCount = (payload.validation_warnings || []).length;
    clearDirty("students");
    clearDirty("lessons");
    const cascaded = (payload.cascaded_files || []).length ? `; cleared ${payload.cascaded_files.join(", ")}` : "";
    setStatus(`Imported ${payload.imported_files.join(", ")}${cascaded}${warningCount ? ` with ${warningCount} warning(s)` : ""}`);
    input.value = "";
  } catch (error) {
    const messages = (error.errors || []).map((item) => `${item.field}: ${item.message}`);
    setStatus(messages.join("; ") || "Student CSV import failed", true);
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

document.getElementById("solveButton").addEventListener("click", runOptimizer);
document.getElementById("saveStudentsButton").addEventListener("click", saveStudents);
document.getElementById("saveLessonsButton").addEventListener("click", saveLessons);
document.getElementById("organizerSaveLessonsButton").addEventListener("click", saveLessons);
document.getElementById("saveVenuesButton").addEventListener("click", saveVenuesAndTravel);
document.getElementById("saveTrainerButton").addEventListener("click", saveTrainerAvailability);
document.getElementById("savePreferenceScoresButton").addEventListener("click", savePreferenceScores);
document.getElementById("saveConfigButton").addEventListener("click", saveConfigParameters);
document.getElementById("resetConfigButton").addEventListener("click", resetConfigDefaults);
document.getElementById("generateLessonsButton").addEventListener("click", generateLessonsFromStudents);
document.getElementById("makeCoupleButton").addEventListener("click", makeSelectedStudentsCouple);
document.getElementById("clearCoupleButton").addEventListener("click", clearSelectedStudentsCouple);
document.getElementById("cleanStudentsButton").addEventListener("click", () => {
  cleanStudents().catch((error) => setStatus(JSON.stringify(error), true));
});
document.getElementById("cleanLessonsButton").addEventListener("click", () => {
  cleanLessons().catch((error) => setStatus(JSON.stringify(error), true));
});
document.getElementById("cleanScheduleButton").addEventListener("click", cleanSchedule);
document.getElementById("saveSolutionButton").addEventListener("click", () => {
  saveVisibleSolution().catch((error) => setStatus(formatErrorStatus(error, "Save solution failed"), true));
});
document.querySelectorAll("[data-calendar-overlay]").forEach((input) => {
  input.addEventListener("change", () => {
    if (input.checked) {
      activeCalendarOverlays.add(input.value);
    } else {
      activeCalendarOverlays.delete(input.value);
    }
    renderCalendar(appData);
  });
});
document.addEventListener("dragend", () => {
  if (!draggedPreferenceGroupKey && !draggedGroupKey) {
    return;
  }
  draggedPreferenceGroupKey = null;
  draggedGroupKey = null;
  updateCalendarPreferenceFrames();
});
document.getElementById("toggleTrayButton").addEventListener("click", toggleTrayVisibility);
document.getElementById("selectedStatus").addEventListener("change", () => {
  updateSelectedStatus().catch((error) => setStatus(JSON.stringify(error), true));
});
document.getElementById("allOptionalButton").addEventListener("click", () => setAllMustSchedule("FALSE"));
document.getElementById("allRequiredButton").addEventListener("click", () => setAllMustSchedule("TRUE"));
document.getElementById("exportScheduleButton").addEventListener("click", exportScheduleCsv);
document.getElementById("importScheduleButton").addEventListener("click", () => document.getElementById("scheduleCsvInput").click());
document.getElementById("scheduleCsvInput").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) {
    importScheduleCsvFile(file).catch((error) => setStatus(JSON.stringify(error), true));
  }
  event.target.value = "";
});
document.getElementById("importCsvButton").addEventListener("click", importCsvFiles);
document.getElementById("importStudentCsvButton").addEventListener("click", () => document.getElementById("studentCsvInput").click());
document.getElementById("studentCsvInput").addEventListener("change", (event) => {
  if (event.target.files.length) {
    importStudentCsvFiles().catch((error) => setStatus(JSON.stringify(error), true));
  }
});
document.getElementById("addStudentButton").addEventListener("click", () => {
  const studentId = nextStudentId();
  const studentRow = {
    student_id: studentId,
    student_name: "",
    lessons_per_week: "1",
    couple: "",
    available_timeslots: "",
    venues: appData.venues && appData.venues[0] ? `default=${appData.venues[0].venue_id}` : "",
  };
  appendRow("studentTableBody", "studentRowTemplate", studentRow);
  appendGeneratedLessonForStudent(studentRow);
  markDirty("students");
});
document.getElementById("addLessonButton").addEventListener("click", () => {
  const studentId = appData.students && appData.students[0] ? appData.students[0].student_id : "";
  appendRow("lessonTableBody", "lessonRowTemplate", {
    lesson_id: studentId ? nextLessonIdForStudent(studentId) : "",
    student_id: studentId,
    venue_id: appData.venues && appData.venues[0] ? appData.venues[0].venue_id : "",
    duration_min: "60",
    must_schedule: "FALSE",
    priority: "1",
    shared_session_id: "",
    booking_day: "",
    booking_start: "",
    booking_end: "",
    booking_status: "",
    booking_lock_level: "1",
  });
  markDirty("lessons");
});
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
document.getElementById("addPreferenceScoreButton").addEventListener("click", () => appendRow("preferenceScoreTableBody", "preferenceScoreRowTemplate", {
  level: "",
  score: "0",
}));
loadData().catch((error) => setStatus(JSON.stringify(error), true));
