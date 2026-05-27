const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const START_HOUR = 8;
const END_HOUR = 23;

let appData = null;
let currentSolution = null;
let evaluationResult = null;
let draggedGroupKey = null;
const dirtySections = new Set();
const temporarySolutions = [null, null, null];

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
  renderTable(appData.table_rows);
  renderLessonTable(appData.lesson_rows || []);
  renderVenueTable(appData.venue_rows);
  renderTravelTable(appData.travel_time_rows);
  renderTrainerTable(appData.trainer_availability_rows);
  renderPreferenceScoreTable(appData.preference_score_rows || []);
  renderTemporarySolutionSlots();
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
  const issueCount = evaluation ? (evaluation.diagnostics || []).length : (warnings || []).length;
  const items = [
    ["Status", solution ? solution.status : "No solution"],
    ["Scheduled", summary.scheduled_lessons ?? "-"],
    ["Unscheduled", summary.unscheduled_lessons ?? "-"],
    ["Score", evaluation ? evaluation.score : (summary.objective_value ?? "-")],
    ["Issues", issueCount],
  ];
  document.getElementById("summaryBar").innerHTML = items.map(([label, value]) => `
    <div class="summary-item">
      <span class="summary-label">${esc(label)}</span>
      <span class="summary-value">${esc(value)}</span>
    </div>
  `).join("");
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
    }
    (currentSolution.warnings || []).forEach((item) => lines.push(`Solver warning: ${item}`));
    (currentSolution.changes || []).forEach((item) => {
      lines.push(`Change: ${item.lesson_id} ${item.change_type} ${item.old_start || "-"} -> ${item.new_start || "-"}`);
    });
  }
  if (evaluationResult) {
    lines.push("");
    lines.push(`Evaluator score: ${evaluationResult.score}`);
    (evaluationResult.warnings || []).forEach((item) => lines.push(`Evaluator warning: ${item}`));
    (evaluationResult.diagnostics || []).forEach((item) => lines.push(`Diagnostic: ${item}`));
    if (!(evaluationResult.diagnostics || []).length) {
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

function appendGeneratedLessonForStudent(studentRow) {
  const existingStudent = (appData.students || []).find((student) => student.student_id === studentRow.student_id);
  appendRow("lessonTableBody", "lessonRowTemplate", {
    lesson_id: nextLessonIdForStudent(studentRow.student_id),
    student_id: studentRow.student_id,
    venue_id: defaultVenueForStudent(studentRow),
    duration_min: "60",
    must_schedule: "TRUE",
    priority: String(existingStudent ? existingStudent.priority : 1),
    shared_session_id: "",
    booking_day: "",
    booking_start: "",
    booking_end: "",
    booking_status: "",
    booking_lock_level: "1",
  });
  markDirty("lessons");
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
  fragment.querySelector(".remove-row").addEventListener("click", (event) => {
    event.target.closest("tr").remove();
    markDirty(sectionForBody(bodyId));
  });
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

function reconcileLessonsFromStudents() {
  const studentRows = collectRows();
  const lessonRows = collectLessonRows();
  let created = 0;
  studentRows.forEach((studentRow) => {
    if (!studentRow.student_id) {
      return;
    }
    const targetCount = Math.max(0, Number(studentRow.lessons_per_week) || 1);
    const currentCount = lessonRows.filter((lessonRow) => lessonRow.student_id === studentRow.student_id).length;
    for (let index = currentCount; index < targetCount; index += 1) {
      appendGeneratedLessonForStudent(studentRow);
      lessonRows.push({student_id: studentRow.student_id, lesson_id: nextLessonIdForStudent(studentRow.student_id)});
      created += 1;
    }
  });
  return created;
}

async function generateLessonsFromStudents() {
  setStatus("Generating and saving lessons...");
  const created = reconcileLessonsFromStudents();
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
    setStatus(created ? `Generated and saved ${created} lesson row(s)` : "Lesson rows already matched plans; Students and Lessons saved");
  } catch (error) {
    if (created) {
      markDirty("lessons");
    }
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
    renderCalendar(appData);
    evaluateCurrentSchedule().catch((error) => setStatus(JSON.stringify(error), true));
  }
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

function renderCalendar(data) {
  const grid = document.getElementById("calendarGrid");
  const available = availabilityMap(data.coach_availability || []);
  const groupsByCell = new Map();

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
      if (available.has(key)) {
        classes.push("available");
      }
      const groups = groupsByCell.get(key) || [];
      html += `<div class="${classes.join(" ")}" data-day="${esc(day)}" data-minute="${mark}">`;
      groups.forEach((group) => {
        const first = group.items[0];
        const names = group.items.map((item) => item.student_name).join(" / ");
        const changed = group.items.some((item) => item.is_changed);
        const status = first.booking_status || "manual";
        const statusClass = `status-${status || "manual"}`;
        html += `
          <div class="lesson-block ${statusClass} ${changed ? "changed" : ""}" draggable="true" data-group-key="${esc(group.key)}">
            <span class="lesson-title">${esc(names)}</span>
            <span class="lesson-status">${esc(status)}</span>
            <span class="lesson-meta">${esc(timeFromIso(first.start_datetime))}-${esc(timeFromIso(first.end_datetime))} - ${esc(first.venue_name)}</span>
          </div>`;
      });
      html += `</div>`;
    });
  }
  grid.innerHTML = html;
  bindCalendarDragHandlers();
}

function bindCalendarDragHandlers() {
  document.querySelectorAll(".lesson-block").forEach((block) => {
    block.addEventListener("dragstart", (event) => {
      draggedGroupKey = block.dataset.groupKey;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedGroupKey);
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
      await moveGroupTo(key, cell.dataset.day, Number(cell.dataset.minute));
    });
  });
}

async function moveGroupTo(groupKey, day, startMinute) {
  if (!currentSolution || !groupKey) {
    return;
  }
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
    return {
      ...item,
      start_datetime: newStart,
      end_datetime: newEnd,
      is_changed: true,
    };
  });
  updateLessonRowsFromSchedule(currentSolution.schedule.filter((item) => lessonIds.has(item.lesson_id)), "draft", true);
  renderLessonTable(appData.lesson_rows || []);
  markDirty("lessons");
  renderCalendar(appData);
  await evaluateCurrentSchedule();
}

function schedulePlacements() {
  if (!currentSolution || !currentSolution.schedule) {
    return [];
  }
  const lessonById = new Map((appData.lessons || []).map((lesson) => [lesson.lesson_id, lesson]));
  return currentSolution.schedule.map((item) => {
    const lesson = lessonById.get(item.lesson_id);
    return {
      lesson_id: item.lesson_id,
      start_datetime: item.start_datetime,
      end_datetime: item.end_datetime,
      venue_id: item.venue_id,
      shared_session_id: item.shared_session_id || (lesson && lesson.shared_session_id) || "",
      booking_status: item.booking_status || "",
    };
  });
}

async function evaluateCurrentSchedule() {
  if (!currentSolution) {
    evaluationResult = null;
    renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
    renderDiagnostics();
    return;
  }
  evaluationResult = await fetchJson("/api/evaluate-schedule", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({schedule: schedulePlacements()}),
  });
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderDiagnostics();
}

function resetSchedule() {
  currentSolution = appData.solution ? structuredClone(appData.solution) : null;
  syncScheduleToCurrentInput();
  if (currentSolution && currentSolution.schedule) {
    updateLessonRowsFromSchedule(currentSolution.schedule, "draft", true);
    renderLessonTable(appData.lesson_rows || []);
    markDirty("lessons");
  }
  renderCalendar(appData);
  evaluateCurrentSchedule().catch((error) => setStatus(JSON.stringify(error), true));
  setStatus("Schedule reset to optimized solution");
}

function renderTemporarySolutionSlots() {
  document.querySelectorAll("[data-temp-slot]").forEach((button) => {
    const index = Number(button.dataset.tempSlot);
    const filled = Boolean(temporarySolutions[index]);
    button.classList.toggle("filled", filled);
    button.textContent = filled ? `Slot ${index + 1}` : `Slot ${index + 1} empty`;
    button.title = filled ? "Switch to this temporary solution" : "Save the current visible solution here";
  });
}

async function useTemporarySolutionSlot(index) {
  if (!temporarySolutions[index]) {
    if (!currentSolution || !currentSolution.schedule) {
      setStatus("No visible schedule to save in this slot", true);
      return;
    }
    temporarySolutions[index] = {
      solution: structuredClone(currentSolution),
      lessonRows: structuredClone(appData.lesson_rows || []),
      evaluation: evaluationResult ? structuredClone(evaluationResult) : null,
    };
    renderTemporarySolutionSlots();
    setStatus(`Saved current schedule to Slot ${index + 1}`);
    return;
  }

  const saved = temporarySolutions[index];
  currentSolution = structuredClone(saved.solution);
  appData.lesson_rows = structuredClone(saved.lessonRows || []);
  renderLessonTable(appData.lesson_rows);
  syncLessonRowsFromDom();
  syncScheduleToCurrentInput();
  evaluationResult = saved.evaluation ? structuredClone(saved.evaluation) : null;
  markDirty("lessons");
  renderTemporarySolutionSlots();
  renderSummary(currentSolution, appData.validation_warnings, evaluationResult);
  renderCalendar(appData);
  renderDiagnostics();
  await evaluateCurrentSchedule();
  setStatus(`Switched to Slot ${index + 1}`);
}

function csvEscape(value) {
  const text = String(value ?? "");
  if (/[",\n\r]/.test(text)) {
    return `"${text.replaceAll('"', '""')}"`;
  }
  return text;
}

function exportScheduleCsv() {
  const headers = ["lesson_id", "start_datetime", "end_datetime", "venue_id", "shared_session_id"];
  const lines = [headers.join(",")];
  schedulePlacements().forEach((row) => {
    lines.push(headers.map((header) => csvEscape(row[header])).join(","));
  });
  const blob = new Blob([`${lines.join("\n")}\n`], {type: "text/csv"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "manual_schedule.csv";
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
  const required = ["lesson_id", "start_datetime", "end_datetime", "venue_id", "shared_session_id"];
  if (required.some((header) => !headers.includes(header))) {
    setStatus("Schedule CSV is missing required placement columns", true);
    return;
  }
  const imported = rows.map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] || ""])));
  const lessonById = new Map((appData.lessons || []).map((lesson) => [lesson.lesson_id, lesson]));
  const studentById = new Map((appData.students || []).map((student) => [student.student_id, student]));
  const venueById = new Map((appData.venues || []).map((venue) => [venue.venue_id, venue]));
  currentSolution = currentSolution || {status: "MANUAL", summary: {}, schedule: [], changes: [], warnings: []};
  currentSolution.status = "MANUAL";
  currentSolution.schedule = imported.map((row) => {
    const lesson = lessonById.get(row.lesson_id) || {};
    const student = studentById.get(lesson.student_id) || {};
    const venue = venueById.get(row.venue_id) || {};
    return {
      lesson_id: row.lesson_id,
      student_id: lesson.student_id || "",
      student_name: student.name || row.lesson_id,
      venue_id: row.venue_id,
      venue_name: venue.name || row.venue_id,
      start_datetime: row.start_datetime,
      end_datetime: row.end_datetime,
      preference_level: "manual",
      preference_score: 0,
      is_changed: true,
      shared_session_id: row.shared_session_id || "",
    };
  });
  currentSolution.summary = {
    ...(currentSolution.summary || {}),
    scheduled_lessons: currentSolution.schedule.length,
  };
  updateLessonRowsFromSchedule(currentSolution.schedule, "draft", true);
  renderLessonTable(appData.lesson_rows || []);
  markDirty("lessons");
  renderCalendar(appData);
  await evaluateCurrentSchedule();
  setStatus(`Imported ${currentSolution.schedule.length} schedule placement(s)`);
}

function formatErrorStatus(error, fallback) {
  const messages = (error.errors || []).map((item) => `row ${item.row} ${item.field}: ${item.message}`);
  return messages.join("; ") || fallback;
}

async function saveStudents() {
  setStatus("Saving students...");
  try {
    const unsavedLessonRows = dirtySections.has("lessons") ? collectLessonRows() : null;
    await fetchJson("/api/students/preferences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: collectRows()}),
    });
    clearDirty("students");
    await loadData();
    restoreUnsavedLessonRows(unsavedLessonRows);
    setStatus("Students saved");
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

async function runOptimizer() {
  setStatus("Running optimizer...");
  try {
    const payload = await fetchJson("/api/solve", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    if (!payload.ok) {
      setStatus((payload.validation_warnings || []).join("; ") || "Validation failed", true);
      return;
    }
    await loadData();
    if (currentSolution && currentSolution.schedule) {
      updateLessonRowsFromSchedule(currentSolution.schedule, "draft", true);
      renderLessonTable(appData.lesson_rows || []);
      applyLessonRowsToSchedule();
      markDirty("lessons");
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
document.getElementById("generateLessonsButton").addEventListener("click", generateLessonsFromStudents);
document.getElementById("resetScheduleButton").addEventListener("click", resetSchedule);
document.querySelectorAll("[data-temp-slot]").forEach((button) => {
  button.addEventListener("click", () => useTemporarySolutionSlot(Number(button.dataset.tempSlot)));
});
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
document.getElementById("addStudentButton").addEventListener("click", () => {
  const studentId = nextStudentId();
  const studentRow = {
    student_id: studentId,
    student_name: "",
    lessons_per_week: "1",
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
    must_schedule: "TRUE",
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
