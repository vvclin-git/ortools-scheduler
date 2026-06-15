"""Local web UI for editing scheduler CSV input and viewing solver output."""

from __future__ import annotations

import argparse
import cgi
import csv
import json
import re
import shutil
import tempfile
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from run_solver_from_csv import (
    CsvInputError,
    OPTIONAL_FILES,
    REQUIRED_FILES,
    TEMPLATE_ROWS,
    load_absences,
    load_coach_availability,
    load_config,
    load_existing_bookings,
    load_lessons,
    load_preferences,
    load_solver_request,
    load_students,
    load_travel_times,
    load_venues,
    validate_solver_request,
    write_csv,
    write_json,
)
from trainer_solver_mvp import (
    FreezePolicy,
    LessonRequest,
    SolverMode,
    StudentPreference,
    build_travel_lookup,
    get_coach_availability_score,
    get_preference_score,
    get_travel_min,
    has_absence_conflict,
    overlaps,
    parse_dt,
    solve_schedule,
)


DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
DAY_LIST = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
VISIBLE_BOOKING_STATUSES = {"draft", "confirmed", "completed", "locked"}
ACCEPTED_BOOKING_STATUSES = {*VISIBLE_BOOKING_STATUSES, "in_progress"}
FIXED_UI_BOOKING_STATUSES = {"completed", "locked", "in_progress"}
DEFAULT_PREFERENCE_SCORES = {"preferred": 100, "acceptable": 60, "last_resort": 20}
CORE_CONFIG_KEYS = [
    "mode",
    "planning_start",
    "planning_end",
    "slot_size_min",
    "max_solve_seconds",
    "freeze_now",
    "freeze_buffer_hours",
]
CONFIG_DEFAULTS = {
    row["key"]: row["value"]
    for row in TEMPLATE_ROWS["config.csv"]
    if row["key"] in CORE_CONFIG_KEYS
}
STUDENT_HEADERS = ["student_id", "name", "default_venue_id", "priority", "lessons_per_week", "couple"]
PREFERENCE_HEADERS = ["student_id", "day", "start", "end", "level", "score"]
LESSON_HEADERS = ["lesson_id", "student_id", "venue_id", "duration_min", "must_schedule", "priority", "shared_session_id"]
BOOKING_HEADERS = ["booking_id", "lesson_id", "student_id", "venue_id", "start_datetime", "end_datetime", "status", "lock_level"]
DAY_SPEC_RE = re.compile(r"^(?P<start>Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:-(?P<end>Mon|Tue|Wed|Thu|Fri|Sat|Sun))?$")
TIME_RANGE_RE = re.compile(r"^(?P<start>\d{1,2}:?\d{2})-(?P<end>\d{1,2}:?\d{2})$")
LEVEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
DEFAULT_VENUE_RE = re.compile(r"^default=(?P<venue_id>[A-Za-z0-9_.:-]+)$")
STATIC_DIR = Path(__file__).with_name("schedule_web_static")
IMPORTABLE_FILES = set(REQUIRED_FILES + OPTIONAL_FILES)


class WebInputError(ValueError):
    """Raised when web input cannot be safely written to CSV."""

    def __init__(self, errors: List[Dict[str, Any]]):
        super().__init__("Invalid web input")
        self.errors = errors


def parse_time_to_minutes(value: str) -> int:
    """Parse HH:MM into minutes after midnight."""
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError(f"Invalid time {value!r}") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid time {value!r}")
    return hour * 60 + minute


def normalize_time_text(value: str) -> str:
    """Normalize compact HHMM or H:MM text to HH:MM."""
    text = value.strip()
    if ":" in text:
        hour_text, minute_text = text.split(":", 1)
    elif len(text) in {3, 4} and text.isdigit():
        hour_text, minute_text = text[:-2], text[-2:]
    else:
        raise ValueError(f"Invalid time {value!r}")
    hour = int(hour_text)
    minute = int(minute_text)
    normalized = f"{hour:02d}:{minute:02d}"
    parse_time_to_minutes(normalized)
    return normalized


def expand_day_spec(value: str, slot_index: int) -> List[str]:
    """Expand Mon or Mon-Fri into ordered weekday names."""
    match = DAY_SPEC_RE.match(value)
    if not match:
        raise ValueError(f"Slot {slot_index} has invalid day or day range {value!r}")
    start_index = DAY_LIST.index(match.group("start"))
    end_day = match.group("end")
    if not end_day:
        return [match.group("start")]
    end_index = DAY_LIST.index(end_day)
    if end_index < start_index:
        raise ValueError(f"Slot {slot_index} day range must run forward within the week")
    return DAY_LIST[start_index:end_index + 1]


def load_preference_score_map(input_dir: Path) -> Dict[str, int]:
    """Load web preference level scores from config.csv with defaults."""
    scores = dict(DEFAULT_PREFERENCE_SCORES)
    rows = read_csv_rows(input_dir / "config.csv")
    for row in rows:
        key = row.get("key", "")
        if not key.startswith("preference_score_"):
            continue
        level = key.removeprefix("preference_score_").strip()
        value = row.get("value", "").strip()
        if level and value:
            scores[level] = int(value)
    return scores


def build_preference_score_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable preference score rows for the Setup page."""
    return [
        {"level": level, "score": str(score)}
        for level, score in sorted(load_preference_score_map(input_dir).items())
    ]


def default_config_parameter_rows() -> List[Dict[str, str]]:
    """Build default editable core config rows for the Setup page."""
    return [{"key": key, "value": CONFIG_DEFAULTS[key]} for key in CORE_CONFIG_KEYS]


def build_config_parameter_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable core config rows, merging missing keys from defaults."""
    raw_values = {
        row.get("key", ""): row.get("value", "")
        for row in read_csv_rows(input_dir / "config.csv")
    }
    return [
        {"key": key, "value": raw_values.get(key, CONFIG_DEFAULTS[key])}
        for key in CORE_CONFIG_KEYS
    ]


def score_for_level(level: str, explicit_score: Optional[int], score_map: Dict[str, int], slot_index: int) -> int:
    """Resolve the numeric solver score for a preference level."""
    if level in score_map:
        return score_map[level]
    if explicit_score is not None:
        return explicit_score
    raise ValueError(f"Slot {slot_index} uses unknown level {level!r}; add it in Setup or provide an explicit score")


def parse_timeslot_string(
    student_id: str,
    value: str,
    score_map: Optional[Dict[str, int]] = None,
    trainer_availability: Optional[List[Any]] = None,
) -> List[StudentPreference]:
    """Parse compact weekly availability into preference rows."""
    text = value.strip()
    scores = score_map or dict(DEFAULT_PREFERENCE_SCORES)
    trainer_by_day: Dict[str, List[Any]] = {}
    for window in trainer_availability or []:
        trainer_by_day.setdefault(window.day, []).append(window)

    if not text:
        if not trainer_availability:
            raise ValueError("Blank preferences require at least one trainer timeslot")
        return [
            StudentPreference(
                student_id=student_id,
                day=window.day,
                start=window.start,
                end=window.end,
                level="preferred",
                score=scores["preferred"],
            )
            for window in trainer_availability
        ]

    preferences: List[StudentPreference] = []
    for index, part in enumerate(text.split(";"), start=1):
        item = part.strip()
        if not item:
            continue
        tokens = item.split()
        if not tokens:
            continue
        days = expand_day_spec(tokens[0], index)
        time_range = None
        level = "preferred"
        explicit_score: Optional[int] = None

        token_index = 1
        if token_index < len(tokens) and TIME_RANGE_RE.match(tokens[token_index]):
            time_range = TIME_RANGE_RE.match(tokens[token_index])
            token_index += 1
        if token_index < len(tokens):
            level = tokens[token_index]
            if not LEVEL_RE.match(level):
                raise ValueError(f"Slot {index} has invalid level {level!r}")
            token_index += 1
        if token_index < len(tokens):
            try:
                explicit_score = int(tokens[token_index])
            except ValueError as exc:
                raise ValueError(f"Slot {index} score must be an integer") from exc
            token_index += 1
        if token_index != len(tokens):
            raise ValueError(f"Slot {index} has too many parts")

        score = score_for_level(level, explicit_score, scores, index)
        if time_range:
            start = normalize_time_text(time_range.group("start"))
            end = normalize_time_text(time_range.group("end"))
            if parse_time_to_minutes(start) >= parse_time_to_minutes(end):
                raise ValueError(f"Slot {index} start must be before end")
            for day in days:
                preferences.append(StudentPreference(student_id, day, start, end, level, score))
            continue

        added = 0
        for day in days:
            for window in trainer_by_day.get(day, []):
                preferences.append(
                    StudentPreference(
                        student_id=student_id,
                        day=day,
                        start=window.start,
                        end=window.end,
                        level=level,
                        score=score,
                    )
                )
                added += 1
        if not added:
            raise ValueError(
                f"Slot {index} did not match any trainer timeslots; add a time range or trainer availability"
            )
    return preferences


def format_timeslot_string(preferences: List[StudentPreference]) -> str:
    """Format preference rows as compact weekly availability text."""
    return "; ".join(
        f"{pref.day} {pref.start}-{pref.end} {pref.level} {pref.score}"
        for pref in preferences
    )


def format_time_from_datetime(value: str) -> str:
    """Return HH:MM from an ISO datetime string."""
    return parse_dt(value).strftime("%H:%M")


def day_from_datetime(value: str) -> str:
    """Return the weekly day label for an ISO datetime string."""
    return DAY_LIST[parse_dt(value).weekday()]


def booking_datetime_for_day_time(planning_start: str, day: str, time_value: str) -> datetime:
    """Build a booking datetime in the planning week for a day and HH:MM."""
    anchor = parse_dt(planning_start)
    week_start = anchor - timedelta(days=anchor.weekday())
    return datetime.combine(
        (week_start + timedelta(days=DAY_LIST.index(day))).date(),
        datetime.strptime(time_value, "%H:%M").time(),
    )


def booking_lock_level_for_status(status: str, existing_lock_level: int = 1) -> int:
    """Choose a lock level that preserves solver fixed-status behavior."""
    if status == "locked":
        return max(existing_lock_level, 3)
    if status in {"completed", "in_progress"}:
        return max(existing_lock_level, 1)
    return existing_lock_level if existing_lock_level >= 0 else 1


def parse_default_venue(value: str) -> str:
    """Parse the v1 venue string."""
    match = DEFAULT_VENUE_RE.match(value.strip())
    if not match:
        raise ValueError("Venue must look like 'default=gym_a'")
    return match.group("venue_id")


def read_solution(output_path: Path) -> Optional[Dict[str, Any]]:
    """Read an existing solver output JSON if present."""
    if not output_path.exists():
        return None
    with output_path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def clear_solution(output_path: Path) -> bool:
    """Remove stale optimizer output after CSV input has changed."""
    if not output_path.exists():
        return False
    output_path.unlink()
    return True


def write_csv_rows(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    """Write CSV rows, preserving headers even when the table is empty."""
    if rows:
        write_csv(path, [{header: row.get(header, "") for header in headers} for row in rows])
        return
    path.write_text(",".join(headers) + "\n", encoding="utf-8", newline="")


def clear_student_owned_data(input_dir: Path, output_path: Path) -> Dict[str, Any]:
    """Clear students and all child data that cannot safely outlive students."""
    input_dir.mkdir(parents=True, exist_ok=True)
    write_csv_rows(input_dir / "students.csv", STUDENT_HEADERS, [])
    write_csv_rows(input_dir / "preferences.csv", PREFERENCE_HEADERS, [])
    write_csv_rows(input_dir / "lessons.csv", LESSON_HEADERS, [])
    write_csv_rows(input_dir / "existing_bookings.csv", BOOKING_HEADERS, [])
    return {
        "ok": True,
        "cleared": ["students.csv", "preferences.csv", "lessons.csv", "existing_bookings.csv"],
        "solution_cleared": clear_solution(output_path),
    }


def clear_lesson_owned_data(input_dir: Path, output_path: Path) -> Dict[str, Any]:
    """Clear lessons and bookings without touching student/preferences data."""
    input_dir.mkdir(parents=True, exist_ok=True)
    write_csv_rows(input_dir / "lessons.csv", LESSON_HEADERS, [])
    write_csv_rows(input_dir / "existing_bookings.csv", BOOKING_HEADERS, [])
    return {
        "ok": True,
        "cleared": ["lessons.csv", "existing_bookings.csv"],
        "solution_cleared": clear_solution(output_path),
    }


def read_csv_rows(path: Path, required: bool = True) -> List[Dict[str, str]]:
    """Read CSV rows for web-only maintenance tasks."""
    if not path.exists():
        if required:
            raise CsvInputError(f"Missing CSV file: {path}")
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def is_numeric_student_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value or ""))


def is_numeric_lesson_id(value: str, student_ids: set[str]) -> bool:
    match = re.fullmatch(r"(\d+)-(\d+)", value or "")
    return bool(match and match.group(1) in student_ids)


def needs_numeric_id_migration(input_dir: Path) -> bool:
    """Return true if scheduler input still contains old-style student or lesson IDs."""
    student_rows = read_csv_rows(input_dir / "students.csv")
    lesson_rows = read_csv_rows(input_dir / "lessons.csv")
    student_ids = {row.get("student_id", "") for row in student_rows}
    return any(not is_numeric_student_id(item) for item in student_ids) or any(
        not is_numeric_lesson_id(row.get("lesson_id", ""), student_ids)
        for row in lesson_rows
    )


def backup_scheduler_files(input_dir: Path, output_path: Path) -> Path:
    """Copy active scheduler files before an automatic ID rewrite."""
    backup_dir = input_dir / f".id_migration_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    suffix = 1
    while backup_dir.exists():
        backup_dir = input_dir / f".id_migration_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suffix}"
        suffix += 1
    backup_dir.mkdir(parents=True)
    for file_name in REQUIRED_FILES + OPTIONAL_FILES:
        source = input_dir / file_name
        if source.exists():
            shutil.copy2(source, backup_dir / file_name)
    if output_path.exists():
        shutil.copy2(output_path, backup_dir / output_path.name)
    return backup_dir


def normalize_solution_ids(output_path: Path, student_id_map: Dict[str, str], lesson_id_map: Dict[str, str]) -> None:
    """Rewrite known ID references inside solution.json if it exists."""
    if not output_path.exists():
        return
    with output_path.open("r", encoding="utf-8-sig") as file:
        payload = json.load(file)
    for collection_name in ("schedule", "changes"):
        for item in payload.get(collection_name, []) or []:
            if item.get("student_id") in student_id_map:
                item["student_id"] = student_id_map[item["student_id"]]
            if item.get("lesson_id") in lesson_id_map:
                item["lesson_id"] = lesson_id_map[item["lesson_id"]]
    write_json(output_path, payload)


def normalize_numeric_ids(input_dir: Path, output_path: Path) -> Dict[str, Any]:
    """Migrate old-style CSV IDs to stable numeric student and lesson IDs."""
    if not needs_numeric_id_migration(input_dir):
        return {"changed": False, "backup_dir": ""}

    backup_dir = backup_scheduler_files(input_dir, output_path)
    student_rows = read_csv_rows(input_dir / "students.csv")
    lesson_rows = read_csv_rows(input_dir / "lessons.csv")
    preference_rows = read_csv_rows(input_dir / "preferences.csv")
    booking_rows = read_csv_rows(input_dir / "existing_bookings.csv", required=False)
    absence_rows = read_csv_rows(input_dir / "absences.csv", required=False)

    student_id_map = {
        row.get("student_id", ""): str(1001 + index)
        for index, row in enumerate(student_rows)
    }
    lesson_counter_by_student: Dict[str, int] = {}
    lesson_id_map: Dict[str, str] = {}
    for row in lesson_rows:
        old_student_id = row.get("student_id", "")
        new_student_id = student_id_map.get(old_student_id, old_student_id)
        lesson_counter_by_student[new_student_id] = lesson_counter_by_student.get(new_student_id, 0) + 1
        lesson_id_map[row.get("lesson_id", "")] = f"{new_student_id}-{lesson_counter_by_student[new_student_id]}"

    for row in student_rows:
        row["student_id"] = student_id_map.get(row.get("student_id", ""), row.get("student_id", ""))
        row.setdefault("lessons_per_week", "1")
        row["lessons_per_week"] = row.get("lessons_per_week", "") or "1"
        if row.get("couple", "") in student_id_map:
            row["couple"] = student_id_map[row["couple"]]
    for row in preference_rows:
        row["student_id"] = student_id_map.get(row.get("student_id", ""), row.get("student_id", ""))
    for row in lesson_rows:
        row["lesson_id"] = lesson_id_map.get(row.get("lesson_id", ""), row.get("lesson_id", ""))
        row["student_id"] = student_id_map.get(row.get("student_id", ""), row.get("student_id", ""))
    for row in booking_rows:
        row["lesson_id"] = lesson_id_map.get(row.get("lesson_id", ""), row.get("lesson_id", ""))
        row["student_id"] = student_id_map.get(row.get("student_id", ""), row.get("student_id", ""))
        row["booking_id"] = f"book_{row['lesson_id']}"
    for row in absence_rows:
        if row.get("entity_type") == "student":
            row["entity_id"] = student_id_map.get(row.get("entity_id", ""), row.get("entity_id", ""))

    write_csv_rows(input_dir / "students.csv", STUDENT_HEADERS, student_rows)
    write_csv_rows(input_dir / "preferences.csv", PREFERENCE_HEADERS, preference_rows)
    write_csv_rows(input_dir / "lessons.csv", LESSON_HEADERS, lesson_rows)
    write_csv_rows(input_dir / "existing_bookings.csv", BOOKING_HEADERS, booking_rows)
    write_csv_rows(input_dir / "absences.csv", ["entity_type", "entity_id", "start_datetime", "end_datetime", "reason"], absence_rows)
    normalize_solution_ids(output_path, student_id_map, lesson_id_map)
    return {"changed": True, "backup_dir": str(backup_dir)}


def import_csv_files(input_dir: Path, files: Dict[str, bytes], cascade_student_dependents: bool = False) -> Dict[str, Any]:
    """Validate and write uploaded scheduler CSV files into the active input folder."""
    if not files:
        raise WebInputError([{"row": 0, "field": "files", "message": "At least one CSV file is required"}])

    errors: List[Dict[str, Any]] = []
    for file_name, content in files.items():
        if file_name not in IMPORTABLE_FILES:
            errors.append(
                {
                    "row": 0,
                    "field": "files",
                    "message": f"Unsupported CSV file: {file_name}",
                }
            )
        if not content.strip():
            errors.append(
                {
                    "row": 0,
                    "field": file_name,
                    "message": "Uploaded CSV file is empty",
                }
            )

    remaining_required = [
        name
        for name in REQUIRED_FILES
        if name not in files and not (input_dir / name).exists()
    ]
    if remaining_required:
        errors.append(
            {
                "row": 0,
                "field": "files",
                "message": f"Missing required CSV files: {', '.join(remaining_required)}",
            }
        )

    if errors:
        raise WebInputError(errors)

    input_dir.mkdir(parents=True, exist_ok=True)
    written = []
    cascaded_files: List[str] = []
    with tempfile.TemporaryDirectory(prefix="scheduler_import_") as temp_name:
        staging_dir = Path(temp_name)
        for file_name in REQUIRED_FILES + OPTIONAL_FILES:
            source = input_dir / file_name
            if source.exists():
                shutil.copy2(source, staging_dir / file_name)
        for file_name, content in files.items():
            (staging_dir / file_name).write_bytes(content.replace(b"\r\n", b"\n"))
            written.append(file_name)
        if cascade_student_dependents:
            write_csv_rows(staging_dir / "lessons.csv", LESSON_HEADERS, [])
            write_csv_rows(staging_dir / "existing_bookings.csv", BOOKING_HEADERS, [])
            cascaded_files = ["lessons.csv", "existing_bookings.csv"]

        request = load_solver_request(staging_dir)
        validation_warnings = validate_solver_request(request)

        for file_name in sorted(set(written + cascaded_files)):
            shutil.copy2(staging_dir / file_name, input_dir / file_name)
    return {
        "ok": True,
        "imported_files": sorted(written),
        "cascaded_files": cascaded_files,
        "validation_warnings": validation_warnings,
    }


def build_table_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable student/preference table rows."""
    students = load_students(input_dir)
    preferences = load_preferences(input_dir)
    raw_students = {
        row.get("student_id", ""): row
        for row in read_csv_rows(input_dir / "students.csv")
    }
    by_student: Dict[str, List[StudentPreference]] = {}
    for pref in preferences:
        by_student.setdefault(pref.student_id, []).append(pref)

    return [
        {
            "student_id": student.student_id,
            "student_name": student.name,
            "lessons_per_week": str(student.lessons_per_week),
            "couple": raw_students.get(student.student_id, {}).get("couple", ""),
            "available_timeslots": format_timeslot_string(by_student.get(student.student_id, [])),
            "venues": f"default={student.default_venue_id}",
        }
        for student in students
    ]


def build_venue_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable venue table rows."""
    return [
        {"venue_id": venue.venue_id, "venue_name": venue.name}
        for venue in load_venues(input_dir)
    ]


def build_travel_time_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable venue travel-time rows."""
    return [
        {
            "from_venue_id": travel.from_venue_id,
            "to_venue_id": travel.to_venue_id,
            "travel_min": str(travel.travel_min),
        }
        for travel in load_travel_times(input_dir)
    ]


def build_lesson_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable lesson request rows merged with current booking state."""
    bookings_by_lesson: Dict[str, Any] = {}
    duplicate_bookings = set()
    for booking in load_existing_bookings(input_dir):
        if booking.lesson_id in bookings_by_lesson:
            duplicate_bookings.add(booking.lesson_id)
            continue
        bookings_by_lesson[booking.lesson_id] = booking

    return [
        {
            "lesson_id": lesson.lesson_id,
            "student_id": lesson.student_id,
            "venue_id": lesson.venue_id,
            "duration_min": str(lesson.duration_min),
            "must_schedule": "TRUE" if lesson.must_schedule else "FALSE",
            "priority": str(lesson.priority),
            "shared_session_id": lesson.shared_session_id or "",
            "booking_id": bookings_by_lesson[lesson.lesson_id].booking_id if lesson.lesson_id in bookings_by_lesson else "",
            "booking_day": day_from_datetime(bookings_by_lesson[lesson.lesson_id].start_datetime) if lesson.lesson_id in bookings_by_lesson else "",
            "booking_start": format_time_from_datetime(bookings_by_lesson[lesson.lesson_id].start_datetime) if lesson.lesson_id in bookings_by_lesson else "",
            "booking_end": format_time_from_datetime(bookings_by_lesson[lesson.lesson_id].end_datetime) if lesson.lesson_id in bookings_by_lesson else "",
            "booking_status": bookings_by_lesson[lesson.lesson_id].status if lesson.lesson_id in bookings_by_lesson else "",
            "booking_lock_level": str(bookings_by_lesson[lesson.lesson_id].lock_level) if lesson.lesson_id in bookings_by_lesson else "1",
            "booking_readonly": "TRUE" if lesson.lesson_id in bookings_by_lesson and bookings_by_lesson[lesson.lesson_id].status in FIXED_UI_BOOKING_STATUSES else "FALSE",
            "booking_error": "duplicate booking rows" if lesson.lesson_id in duplicate_bookings else "",
        }
        for lesson in load_lessons(input_dir)
    ]


def build_trainer_availability_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable trainer availability rows."""
    return [
        {
            "day": item.day,
            "start": item.start,
            "end": item.end,
            "score": str(item.score),
        }
        for item in load_coach_availability(input_dir)
    ]


def build_api_data(input_dir: Path, output_path: Path) -> Dict[str, Any]:
    """Build the payload for GET /api/data."""
    validation_warnings: List[str] = []
    config_payload: Dict[str, str] = {}
    try:
        request = load_solver_request(input_dir)
        validation_warnings = validate_solver_request(request)
        config_payload = {
            "mode": request.config.mode.value,
            "planning_start": request.config.planning_start,
            "planning_end": request.config.planning_end,
            "freeze_now": request.config.freeze_policy.now if request.config.freeze_policy else "",
        }
    except Exception as exc:
        validation_warnings = [str(exc)]

    return {
        "config": config_payload,
        "table_rows": build_table_rows(input_dir),
        "venue_rows": build_venue_rows(input_dir),
        "travel_time_rows": build_travel_time_rows(input_dir),
        "lesson_rows": build_lesson_rows(input_dir),
        "trainer_availability_rows": build_trainer_availability_rows(input_dir),
        "preference_score_rows": build_preference_score_rows(input_dir),
        "config_rows": build_config_parameter_rows(input_dir),
        "default_config_rows": default_config_parameter_rows(),
        "students": [asdict(item) for item in load_students(input_dir)],
        "venues": [asdict(item) for item in load_venues(input_dir)],
        "travel_times": [asdict(item) for item in load_travel_times(input_dir)],
        "lessons": [asdict(item) for item in load_lessons(input_dir)],
        "preferences": [asdict(item) for item in load_preferences(input_dir)],
        "coach_availability": [asdict(item) for item in load_coach_availability(input_dir)],
        "existing_bookings": [asdict(item) for item in load_existing_bookings(input_dir)],
        "absences": [asdict(item) for item in load_absences(input_dir)],
        "solution": read_solution(output_path),
        "validation_warnings": validation_warnings,
    }


def validate_table_rows(input_dir: Path, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Validate frontend table rows and return students/preferences CSV rows."""
    venue_ids = {venue.venue_id for venue in load_venues(input_dir)}
    existing_students = {student.student_id: student for student in load_students(input_dir)}
    preference_scores = load_preference_score_map(input_dir)
    trainer_availability = load_coach_availability(input_dir)
    errors: List[Dict[str, Any]] = []
    student_rows: List[Dict[str, str]] = []
    preference_rows: List[Dict[str, str]] = []
    seen_ids = set()

    if not rows:
        raise WebInputError([{"row": 0, "field": "rows", "message": "At least one student row is required"}])

    for row_index, raw in enumerate(rows, start=1):
        student_id = str(raw.get("student_id", "")).strip()
        student_name = str(raw.get("student_name", "")).strip()
        lessons_per_week_text = str(raw.get("lessons_per_week", "1")).strip() or "1"
        couple = str(raw.get("couple", "")).strip()
        timeslots = str(raw.get("available_timeslots", "")).strip()
        venues = str(raw.get("venues", "")).strip()

        if not student_id:
            errors.append({"row": row_index, "field": "student_id", "message": "student_id is required"})
            continue
        if student_id in seen_ids:
            errors.append({"row": row_index, "field": "student_id", "message": f"Duplicate student_id={student_id}"})
            continue
        seen_ids.add(student_id)
        if not student_name:
            errors.append({"row": row_index, "field": "student_name", "message": "student_name is required"})
        try:
            default_venue_id = parse_default_venue(venues)
            if default_venue_id not in venue_ids:
                errors.append(
                    {
                        "row": row_index,
                        "field": "venues",
                        "message": f"Unknown default venue_id={default_venue_id}",
                    }
                )
        except ValueError as exc:
            default_venue_id = ""
            errors.append({"row": row_index, "field": "venues", "message": str(exc)})

        try:
            parsed_preferences = parse_timeslot_string(student_id, timeslots, preference_scores, trainer_availability)
        except ValueError as exc:
            parsed_preferences = []
            errors.append({"row": row_index, "field": "available_timeslots", "message": str(exc)})
        try:
            lessons_per_week = int(lessons_per_week_text)
            if lessons_per_week < 0:
                raise ValueError
        except ValueError:
            lessons_per_week = 1
            errors.append({"row": row_index, "field": "lessons_per_week", "message": "lessons_per_week must be a non-negative integer"})

        existing = existing_students.get(student_id)
        student_rows.append(
            {
                "student_id": student_id,
                "name": student_name,
                "default_venue_id": default_venue_id,
                "priority": str(existing.priority if existing is not None else 1),
                "lessons_per_week": str(lessons_per_week),
                "couple": couple,
            }
        )
        for pref in parsed_preferences:
            preference_rows.append(
                {
                    "student_id": pref.student_id,
                    "day": pref.day,
                    "start": pref.start,
                    "end": pref.end,
                    "level": pref.level,
                    "score": str(pref.score),
                }
            )

    if errors:
        raise WebInputError(errors)
    return student_rows, preference_rows


def save_student_preferences(input_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and write students.csv and preferences.csv."""
    student_rows, preference_rows = validate_table_rows(input_dir, rows)
    write_csv_rows(input_dir / "students.csv", STUDENT_HEADERS, student_rows)
    if preference_rows:
        write_csv(input_dir / "preferences.csv", preference_rows)
    else:
        write_csv_rows(input_dir / "preferences.csv", PREFERENCE_HEADERS, [])
    return {"ok": True, "saved_rows": len(student_rows), "preference_rows": len(preference_rows)}


def validate_preference_score_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Validate editable preference level score rows."""
    errors: List[Dict[str, Any]] = []
    parsed_rows: List[Dict[str, str]] = []
    seen_levels = set()
    if not rows:
        raise WebInputError([{"row": 0, "field": "preference_scores", "message": "At least one preference score is required"}])

    for row_index, raw in enumerate(rows, start=1):
        level = str(raw.get("level", "")).strip()
        score_text = str(raw.get("score", "")).strip()
        if not LEVEL_RE.match(level):
            errors.append({"row": row_index, "field": "level", "message": "level must start with a letter or underscore"})
            continue
        if level in seen_levels:
            errors.append({"row": row_index, "field": "level", "message": f"Duplicate level={level}"})
            continue
        seen_levels.add(level)
        try:
            score = int(score_text)
        except ValueError:
            score = 0
            errors.append({"row": row_index, "field": "score", "message": "score must be an integer"})
        parsed_rows.append({"level": level, "score": str(score)})

    if "preferred" not in seen_levels:
        errors.append({"row": 0, "field": "level", "message": "preferred level is required"})
    if errors:
        raise WebInputError(errors)
    return parsed_rows


def save_preference_scores(input_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and write web preference score settings into config.csv."""
    parsed_rows = validate_preference_score_rows(rows)
    config_rows = read_csv_rows(input_dir / "config.csv")
    score_keys = {f"preference_score_{row['level']}": row["score"] for row in parsed_rows}
    retained = [row for row in config_rows if not row.get("key", "").startswith("preference_score_")]
    retained.extend({"key": key, "value": value} for key, value in sorted(score_keys.items()))
    write_csv_rows(input_dir / "config.csv", ["key", "value"], retained)
    return {"ok": True, "preference_score_rows": len(parsed_rows)}


def validate_config_parameter_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Validate editable core solver config rows."""
    errors: List[Dict[str, Any]] = []
    parsed_by_key: Dict[str, str] = {}
    allowed = set(CORE_CONFIG_KEYS)

    if not rows:
        raise WebInputError([{"row": 0, "field": "config", "message": "At least one config row is required"}])

    for row_index, raw in enumerate(rows, start=1):
        key = str(raw.get("key", "")).strip()
        value = str(raw.get("value", "")).strip()
        if key not in allowed:
            errors.append({"row": row_index, "field": "key", "message": f"Unknown config key={key}"})
            continue
        if key in parsed_by_key:
            errors.append({"row": row_index, "field": "key", "message": f"Duplicate config key={key}"})
            continue
        parsed_by_key[key] = value

    for key in CORE_CONFIG_KEYS:
        if key not in parsed_by_key:
            parsed_by_key[key] = CONFIG_DEFAULTS[key]

    if parsed_by_key["mode"] not in {"weekly_planning", "in_week_reschedule"}:
        errors.append({"row": 0, "field": "mode", "message": "mode must be weekly_planning or in_week_reschedule"})
    try:
        planning_start = parse_dt(parsed_by_key["planning_start"])
        planning_end = parse_dt(parsed_by_key["planning_end"])
        if planning_end <= planning_start:
            errors.append({"row": 0, "field": "planning_end", "message": "planning_end must be after planning_start"})
    except ValueError as exc:
        errors.append({"row": 0, "field": "planning_start", "message": str(exc)})
    try:
        slot_size = int(parsed_by_key["slot_size_min"])
        if slot_size <= 0:
            raise ValueError
    except ValueError:
        errors.append({"row": 0, "field": "slot_size_min", "message": "slot_size_min must be a positive integer"})
    try:
        max_seconds = float(parsed_by_key["max_solve_seconds"])
        if max_seconds <= 0:
            raise ValueError
    except ValueError:
        errors.append({"row": 0, "field": "max_solve_seconds", "message": "max_solve_seconds must be a positive number"})
    if parsed_by_key["freeze_now"]:
        try:
            parse_dt(parsed_by_key["freeze_now"])
        except ValueError as exc:
            errors.append({"row": 0, "field": "freeze_now", "message": str(exc)})
    try:
        freeze_buffer_hours = int(parsed_by_key["freeze_buffer_hours"])
        if freeze_buffer_hours < 0:
            raise ValueError
    except ValueError:
        errors.append({"row": 0, "field": "freeze_buffer_hours", "message": "freeze_buffer_hours must be a non-negative integer"})

    if errors:
        raise WebInputError(errors)
    return [{"key": key, "value": parsed_by_key[key]} for key in CORE_CONFIG_KEYS]


def save_config_parameters(input_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and write core solver config settings into config.csv."""
    parsed_rows = validate_config_parameter_rows(rows)
    config_rows = read_csv_rows(input_dir / "config.csv")
    core_keys = set(CORE_CONFIG_KEYS)
    retained = [row for row in config_rows if row.get("key", "") not in core_keys]
    write_csv_rows(input_dir / "config.csv", ["key", "value"], parsed_rows + retained)
    return {"ok": True, "config_rows": len(parsed_rows)}


def apply_runtime_config_overrides(request: Any, overrides: Dict[str, Any]) -> Any:
    """Apply Organizer runtime config values to a loaded request without writing CSV."""
    if not overrides:
        return request

    errors: List[Dict[str, Any]] = []
    allowed = {"mode", "planning_start", "planning_end", "freeze_now"}
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        errors.append({"row": 0, "field": "runtime_config", "message": f"Unknown runtime config key(s): {', '.join(unknown)}"})

    mode_text = str(overrides.get("mode", request.config.mode.value)).strip()
    planning_start = str(overrides.get("planning_start", request.config.planning_start)).strip()
    planning_end = str(overrides.get("planning_end", request.config.planning_end)).strip()
    freeze_now = str(overrides.get("freeze_now", request.config.freeze_policy.now if request.config.freeze_policy else "")).strip()

    try:
        mode = SolverMode(mode_text)
    except ValueError:
        mode = request.config.mode
        errors.append({"row": 0, "field": "mode", "message": "mode must be weekly_planning or in_week_reschedule"})
    try:
        start_dt = parse_dt(planning_start)
        end_dt = parse_dt(planning_end)
        if end_dt <= start_dt:
            errors.append({"row": 0, "field": "planning_end", "message": "planning_end must be after planning_start"})
    except ValueError as exc:
        errors.append({"row": 0, "field": "planning_start", "message": str(exc)})
    if freeze_now:
        try:
            parse_dt(freeze_now)
        except ValueError as exc:
            errors.append({"row": 0, "field": "freeze_now", "message": str(exc)})

    if errors:
        raise WebInputError(errors)

    freeze_policy = None
    if freeze_now:
        freeze_policy = FreezePolicy(
            now=freeze_now,
            freeze_buffer_hours=request.config.freeze_policy.freeze_buffer_hours if request.config.freeze_policy else 4,
        )
    config = replace(
        request.config,
        mode=mode,
        planning_start=planning_start,
        planning_end=planning_end,
        freeze_policy=freeze_policy,
    )
    return replace(request, config=config)


def parse_web_bool(value: Any) -> bool:
    """Parse a frontend boolean-ish value."""
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise ValueError("must_schedule must be TRUE or FALSE")


def validate_lesson_rows(
    input_dir: Path,
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Validate editable lesson rows and return lessons.csv and existing_bookings.csv rows."""
    student_ids = {student.student_id for student in load_students(input_dir)}
    venue_ids = {venue.venue_id for venue in load_venues(input_dir)}
    config = load_config(input_dir)
    planning_start = parse_dt(config.planning_start)
    planning_end = parse_dt(config.planning_end)
    existing_bookings_by_lesson = {}
    duplicate_existing_lessons = set()
    for booking in load_existing_bookings(input_dir):
        if booking.lesson_id in existing_bookings_by_lesson:
            duplicate_existing_lessons.add(booking.lesson_id)
            continue
        existing_bookings_by_lesson[booking.lesson_id] = booking
    errors: List[Dict[str, Any]] = []
    parsed_lessons: List[Dict[str, str]] = []
    parsed_bookings: List[Dict[str, str]] = []
    seen_ids = set()

    if not rows:
        raise WebInputError([{"row": 0, "field": "lessons", "message": "At least one lesson row is required"}])

    for row_index, raw in enumerate(rows, start=1):
        lesson_id = str(raw.get("lesson_id", "")).strip()
        student_id = str(raw.get("student_id", "")).strip()
        venue_id = str(raw.get("venue_id", "")).strip()
        duration_text = str(raw.get("duration_min", "")).strip()
        must_schedule_text = str(raw.get("must_schedule", "TRUE")).strip() or "TRUE"
        priority_text = str(raw.get("priority", "1")).strip() or "1"
        shared_session_id = str(raw.get("shared_session_id", "")).strip()
        booking_day = str(raw.get("booking_day", "")).strip()
        booking_start = str(raw.get("booking_start", "")).strip()
        booking_status = str(raw.get("booking_status", "")).strip()
        booking_lock_level_text = str(raw.get("booking_lock_level", "")).strip()

        if not lesson_id:
            errors.append({"row": row_index, "field": "lesson_id", "message": "lesson_id is required"})
            continue
        if lesson_id in seen_ids:
            errors.append({"row": row_index, "field": "lesson_id", "message": f"Duplicate lesson_id={lesson_id}"})
            continue
        seen_ids.add(lesson_id)
        if student_id not in student_ids:
            errors.append({"row": row_index, "field": "student_id", "message": f"Unknown student_id={student_id}"})
        if venue_id not in venue_ids:
            errors.append({"row": row_index, "field": "venue_id", "message": f"Unknown venue_id={venue_id}"})
        try:
            duration_min = int(duration_text)
            if duration_min <= 0:
                raise ValueError
        except ValueError:
            duration_min = 0
            errors.append({"row": row_index, "field": "duration_min", "message": "duration_min must be a positive integer"})
        try:
            must_schedule = parse_web_bool(must_schedule_text)
        except ValueError as exc:
            must_schedule = True
            errors.append({"row": row_index, "field": "must_schedule", "message": str(exc)})
        try:
            priority = int(priority_text)
            if priority < 0:
                raise ValueError
        except ValueError:
            priority = 0
            errors.append({"row": row_index, "field": "priority", "message": "priority must be a non-negative integer"})

        parsed_lessons.append(
            {
                "lesson_id": lesson_id,
                "student_id": student_id,
                "venue_id": venue_id,
                "duration_min": str(duration_min),
                "must_schedule": "TRUE" if must_schedule else "FALSE",
                "priority": str(priority),
                "shared_session_id": shared_session_id,
            }
        )
        if lesson_id in duplicate_existing_lessons:
            errors.append({"row": row_index, "field": "booking_status", "message": f"Duplicate existing booking rows for lesson_id={lesson_id}"})

        has_booking_input = any([booking_day, booking_start, booking_status])
        if not has_booking_input:
            continue
        if not booking_day or not booking_start or not booking_status:
            errors.append({"row": row_index, "field": "booking_status", "message": "day, start, and status are required together"})
            continue
        if booking_day not in DAYS:
            errors.append({"row": row_index, "field": "booking_day", "message": f"Invalid day={booking_day}"})
            continue
        try:
            parse_time_to_minutes(booking_start)
        except ValueError as exc:
            errors.append({"row": row_index, "field": "booking_start", "message": str(exc)})
            continue
        if booking_status not in ACCEPTED_BOOKING_STATUSES:
            errors.append({"row": row_index, "field": "booking_status", "message": f"Invalid status={booking_status}"})
            continue
        existing_booking = existing_bookings_by_lesson.get(lesson_id)
        try:
            booking_lock_level = int(booking_lock_level_text or (existing_booking.lock_level if existing_booking else 1))
            if booking_lock_level < 0:
                raise ValueError
        except ValueError:
            booking_lock_level = 1
            errors.append({"row": row_index, "field": "booking_lock_level", "message": "lock_level must be a non-negative integer"})
        start_dt = booking_datetime_for_day_time(config.planning_start, booking_day, booking_start)
        end_dt = start_dt + timedelta(minutes=duration_min)
        if start_dt < planning_start or end_dt > planning_end:
            errors.append({"row": row_index, "field": "booking_start", "message": "booking time must be inside the planning window"})
        if (
            existing_booking
            and existing_booking.status in FIXED_UI_BOOKING_STATUSES
            and booking_status == existing_booking.status
            and (
                parse_dt(existing_booking.start_datetime) != start_dt
                or parse_dt(existing_booking.end_datetime) != end_dt
            )
        ):
            errors.append({"row": row_index, "field": "booking_start", "message": "change fixed booking status before editing its time"})
        parsed_bookings.append(
            {
                "booking_id": existing_booking.booking_id if existing_booking else f"book_{lesson_id}",
                "lesson_id": lesson_id,
                "student_id": student_id,
                "venue_id": venue_id,
                "start_datetime": start_dt.isoformat(timespec="seconds"),
                "end_datetime": end_dt.isoformat(timespec="seconds"),
                "status": booking_status,
                "lock_level": str(booking_lock_level_for_status(booking_status, booking_lock_level)),
            }
        )

    if errors:
        raise WebInputError(errors)
    return parsed_lessons, parsed_bookings


def save_lessons(input_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and write lessons.csv and existing_bookings.csv."""
    parsed_lessons, parsed_bookings = validate_lesson_rows(input_dir, rows)
    write_csv_rows(
        input_dir / "lessons.csv",
        LESSON_HEADERS,
        parsed_lessons,
    )
    write_csv_rows(input_dir / "existing_bookings.csv", BOOKING_HEADERS, parsed_bookings)
    return {"ok": True, "lesson_rows": len(parsed_lessons), "booking_rows": len(parsed_bookings)}


def validate_venue_and_travel_rows(
    input_dir: Path,
    venue_rows: List[Dict[str, Any]],
    travel_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Validate editable venues and travel-time rows."""
    errors: List[Dict[str, Any]] = []
    parsed_venues: List[Dict[str, str]] = []
    venue_ids = set()

    if not venue_rows:
        raise WebInputError([{"row": 0, "field": "venues", "message": "At least one venue is required"}])

    for row_index, raw in enumerate(venue_rows, start=1):
        venue_id = str(raw.get("venue_id", "")).strip()
        venue_name = str(raw.get("venue_name", "")).strip()
        if not venue_id:
            errors.append({"row": row_index, "field": "venue_id", "message": "venue_id is required"})
            continue
        if venue_id in venue_ids:
            errors.append({"row": row_index, "field": "venue_id", "message": f"Duplicate venue_id={venue_id}"})
            continue
        venue_ids.add(venue_id)
        if not venue_name:
            errors.append({"row": row_index, "field": "venue_name", "message": "venue_name is required"})
        parsed_venues.append({"venue_id": venue_id, "name": venue_name})

    referenced = set()
    referenced.update(student.default_venue_id for student in load_students(input_dir))
    referenced.update(lesson.venue_id for lesson in load_lessons(input_dir))
    referenced.update(booking.venue_id for booking in load_existing_bookings(input_dir))
    referenced.update(
        absence.entity_id
        for absence in load_absences(input_dir)
        if absence.entity_type == "venue"
    )
    missing_references = sorted(venue_id for venue_id in referenced if venue_id and venue_id not in venue_ids)
    for venue_id in missing_references:
        errors.append(
            {
                "row": 0,
                "field": "venue_id",
                "message": f"Cannot remove venue_id={venue_id}; it is still referenced by scheduler input",
            }
        )

    parsed_travel: List[Dict[str, str]] = []
    seen_travel = set()
    for row_index, raw in enumerate(travel_rows, start=1):
        from_venue_id = str(raw.get("from_venue_id", "")).strip()
        to_venue_id = str(raw.get("to_venue_id", "")).strip()
        travel_min_text = str(raw.get("travel_min", "")).strip()
        if not from_venue_id and not to_venue_id and not travel_min_text:
            continue
        if from_venue_id not in venue_ids:
            errors.append({"row": row_index, "field": "from_venue_id", "message": f"Unknown venue_id={from_venue_id}"})
        if to_venue_id not in venue_ids:
            errors.append({"row": row_index, "field": "to_venue_id", "message": f"Unknown venue_id={to_venue_id}"})
        travel_key = (from_venue_id, to_venue_id)
        if travel_key in seen_travel:
            errors.append({"row": row_index, "field": "travel_min", "message": f"Duplicate travel path {from_venue_id}->{to_venue_id}"})
        seen_travel.add(travel_key)
        try:
            travel_min = int(travel_min_text)
            if travel_min < 0:
                raise ValueError
        except ValueError:
            travel_min = 0
            errors.append({"row": row_index, "field": "travel_min", "message": "travel_min must be a non-negative integer"})
        parsed_travel.append(
            {
                "from_venue_id": from_venue_id,
                "to_venue_id": to_venue_id,
                "travel_min": str(travel_min),
            }
        )

    if errors:
        raise WebInputError(errors)
    return parsed_venues, parsed_travel


def save_venues_and_travel_times(
    input_dir: Path,
    venue_rows: List[Dict[str, Any]],
    travel_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate and write venues.csv and travel_times.csv."""
    parsed_venues, parsed_travel = validate_venue_and_travel_rows(input_dir, venue_rows, travel_rows)
    write_csv_rows(input_dir / "venues.csv", ["venue_id", "name"], parsed_venues)
    write_csv_rows(input_dir / "travel_times.csv", ["from_venue_id", "to_venue_id", "travel_min"], parsed_travel)
    return {"ok": True, "venue_rows": len(parsed_venues), "travel_time_rows": len(parsed_travel)}


def validate_trainer_availability_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Validate editable trainer availability rows."""
    errors: List[Dict[str, Any]] = []
    parsed_rows: List[Dict[str, str]] = []

    if not rows:
        raise WebInputError([{"row": 0, "field": "trainer_availability", "message": "At least one trainer timeslot is required"}])

    for row_index, raw in enumerate(rows, start=1):
        day = str(raw.get("day", "")).strip()
        start = str(raw.get("start", "")).strip()
        end = str(raw.get("end", "")).strip()
        score_text = str(raw.get("score", "0")).strip() or "0"
        if day not in DAYS:
            errors.append({"row": row_index, "field": "day", "message": f"Invalid day={day}"})
        try:
            if parse_time_to_minutes(start) >= parse_time_to_minutes(end):
                errors.append({"row": row_index, "field": "end", "message": "start must be before end"})
        except ValueError as exc:
            errors.append({"row": row_index, "field": "start", "message": str(exc)})
        try:
            score = int(score_text)
        except ValueError:
            score = 0
            errors.append({"row": row_index, "field": "score", "message": "score must be an integer"})
        parsed_rows.append({"day": day, "start": start, "end": end, "score": str(score)})

    if errors:
        raise WebInputError(errors)
    return parsed_rows


def save_trainer_availability(input_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and write coach_availability.csv."""
    parsed_rows = validate_trainer_availability_rows(rows)
    write_csv_rows(input_dir / "coach_availability.csv", ["day", "start", "end", "score"], parsed_rows)
    return {"ok": True, "trainer_availability_rows": len(parsed_rows)}


def run_solver(input_dir: Path, output_path: Path, runtime_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load CSV input, run the optimizer, write JSON, and return the response."""
    request = load_solver_request(input_dir)
    request = apply_runtime_config_overrides(request, runtime_config or {})
    validation_warnings = validate_solver_request(request)
    if validation_warnings:
        return {"ok": False, "validation_warnings": validation_warnings}
    started = time.perf_counter()
    response = solve_schedule(request)
    payload = asdict(response)
    elapsed = round(time.perf_counter() - started, 3)
    payload.setdefault("summary", {})["solve_wall_time_seconds"] = elapsed
    if response.status not in {"OPTIMAL", "FEASIBLE"}:
        return {"ok": False, "validation_warnings": response.warnings, "solution": payload}
    write_json(output_path, payload)
    return {"ok": True, "solution": payload}


def schedule_item_lesson(request_lessons: Dict[str, LessonRequest], item: Dict[str, Any]) -> Optional[LessonRequest]:
    """Return a lesson copy with the evaluated placement venue."""
    lesson_id = str(item.get("lesson_id", "")).strip()
    lesson = request_lessons.get(lesson_id)
    if lesson is None:
        return None
    venue_id = str(item.get("venue_id", lesson.venue_id)).strip() or lesson.venue_id
    shared_session_id = str(item.get("shared_session_id", "")).strip() or lesson.shared_session_id
    return LessonRequest(
        lesson_id=lesson.lesson_id,
        student_id=lesson.student_id,
        venue_id=venue_id,
        duration_min=lesson.duration_min,
        must_schedule=lesson.must_schedule,
        priority=lesson.priority,
        shared_session_id=shared_session_id,
    )


def evaluate_schedule(input_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score a manually arranged schedule without solving or writing files."""
    request = load_solver_request(input_dir)
    lessons_by_id = {lesson.lesson_id: lesson for lesson in request.lessons}
    venues_by_id = {venue.venue_id: venue for venue in request.venues}
    travel_lookup = build_travel_lookup(request.travel_times)
    weights = request.config.weights
    diagnostics: List[str] = []
    warnings: List[str] = []
    placements: List[Dict[str, Any]] = []
    score = 0

    seen_lesson_ids = set()
    for index, raw in enumerate(rows, start=1):
        lesson_id = str(raw.get("lesson_id", "")).strip()
        if lesson_id in seen_lesson_ids:
            diagnostics.append(f"Row {index}: duplicate placement for lesson_id={lesson_id}")
            continue
        seen_lesson_ids.add(lesson_id)
        lesson = schedule_item_lesson(lessons_by_id, raw)
        if lesson is None:
            diagnostics.append(f"Row {index}: unknown lesson_id={lesson_id}")
            continue
        if lesson.venue_id not in venues_by_id:
            diagnostics.append(f"Row {index}: unknown venue_id={lesson.venue_id}")
            continue
        try:
            start = parse_dt(str(raw.get("start_datetime", "")).strip())
            end = parse_dt(str(raw.get("end_datetime", "")).strip())
        except ValueError:
            diagnostics.append(f"Row {index}: invalid start_datetime or end_datetime")
            continue
        if end <= start:
            diagnostics.append(f"Row {index}: end_datetime must be after start_datetime")
            continue
        if end - start != timedelta(minutes=lesson.duration_min):
            diagnostics.append(
                f"Row {index}: duration does not match lesson duration_min={lesson.duration_min}"
            )

        pref = get_preference_score(lesson.student_id, start, end, request.preferences)
        if pref is None:
            diagnostics.append(f"{lesson.lesson_id}: outside student preference windows")
            pref_score = 0
        else:
            pref_score = pref[1]
            score += pref_score

        coach_score = get_coach_availability_score(start, end, request.coach_availability)
        if coach_score is None:
            diagnostics.append(f"{lesson.lesson_id}: outside coach availability")
            coach_score = 0
        else:
            score += coach_score

        if has_absence_conflict(lesson, start, end, request.absences):
            diagnostics.append(f"{lesson.lesson_id}: conflicts with an absence")

        score += lesson.priority * weights.lesson_priority
        placements.append(
            {
                "lesson": lesson,
                "start": start,
                "end": end,
                "preference_score": pref_score,
                "coach_score": coach_score,
            }
        )

    for lesson in request.lessons:
        if lesson.lesson_id in seen_lesson_ids:
            continue
        if lesson.must_schedule:
            diagnostics.append(f"{lesson.lesson_id}: required lesson is missing from manual schedule")
        else:
            score -= weights.cancel_optional_penalty

    total_cross_penalty = 0
    for left_index, left in enumerate(placements):
        for right in placements[left_index + 1:]:
            left_lesson = left["lesson"]
            right_lesson = right["lesson"]
            same_shared = (
                bool(left_lesson.shared_session_id)
                and bool(right_lesson.shared_session_id)
                and left_lesson.shared_session_id == right_lesson.shared_session_id
            )
            same_slot = (
                left["start"] == right["start"]
                and left["end"] == right["end"]
                and left_lesson.venue_id == right_lesson.venue_id
            )
            if same_shared and not same_slot:
                diagnostics.append(
                    f"{left_lesson.shared_session_id}: shared lessons must use the same time and venue"
                )
            if overlaps(left["start"], left["end"], right["start"], right["end"]):
                if not (same_shared and same_slot):
                    diagnostics.append(
                        f"{left_lesson.lesson_id} and {right_lesson.lesson_id}: overlapping lessons"
                    )
                continue
            if left["start"].date() != right["start"].date():
                continue
            first, second = (left, right) if left["start"] <= right["start"] else (right, left)
            first_lesson = first["lesson"]
            second_lesson = second["lesson"]
            travel_min = get_travel_min(travel_lookup, first_lesson.venue_id, second_lesson.venue_id)
            if first["end"] + timedelta(minutes=travel_min) > second["start"]:
                diagnostics.append(
                    f"{first_lesson.lesson_id} to {second_lesson.lesson_id}: needs {travel_min} minutes travel time"
                )
            if first_lesson.venue_id != second_lesson.venue_id:
                pair_penalty = min(
                    get_travel_min(travel_lookup, first_lesson.venue_id, second_lesson.venue_id),
                    get_travel_min(travel_lookup, second_lesson.venue_id, first_lesson.venue_id),
                ) * weights.cross_venue_pair_penalty_per_min
                total_cross_penalty += pair_penalty
                score -= pair_penalty

    if diagnostics:
        warnings.append(f"{len(diagnostics)} diagnostic issue(s) found")

    return {
        "ok": True,
        "score": score,
        "summary": {
            "scheduled_lessons": len(placements),
            "unscheduled_lessons": len(request.lessons) - len(seen_lesson_ids & set(lessons_by_id)),
            "total_cross_venue_pair_penalty": total_cross_penalty,
        },
        "warnings": warnings,
        "diagnostics": diagnostics,
    }


class ScheduleWebHandler(SimpleHTTPRequestHandler):
    """Serve static frontend files and local scheduler API endpoints."""

    input_dir: Path
    output_path: Path

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/data":
            self.send_json(build_api_data(self.input_dir, self.output_path))
            return
        if parsed.path == "/api/solution":
            solution = read_solution(self.output_path)
            if solution is None:
                self.send_json({"error": "No solution has been written yet."}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"solution": solution})
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/import-csv":
                query = parse_qs(parsed.query)
                result = import_csv_files(
                    self.input_dir,
                    self.read_multipart_files(),
                    cascade_student_dependents=query.get("scope", [""])[0] == "students",
                )
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            payload = self.read_json_body()
            if parsed.path == "/api/clear-students":
                self.send_json(clear_student_owned_data(self.input_dir, self.output_path))
                return
            if parsed.path == "/api/clear-lessons":
                self.send_json(clear_lesson_owned_data(self.input_dir, self.output_path))
                return
            if parsed.path == "/api/students/preferences":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                result = save_student_preferences(self.input_dir, rows)
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            if parsed.path == "/api/lessons":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                result = save_lessons(self.input_dir, rows)
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            if parsed.path == "/api/venues/travel":
                venue_rows = payload.get("venues", [])
                travel_rows = payload.get("travel_times", [])
                if not isinstance(venue_rows, list) or not isinstance(travel_rows, list):
                    raise WebInputError([{"row": 0, "field": "venues", "message": "venues and travel_times must be lists"}])
                result = save_venues_and_travel_times(self.input_dir, venue_rows, travel_rows)
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            if parsed.path == "/api/trainer-availability":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                result = save_trainer_availability(self.input_dir, rows)
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            if parsed.path == "/api/preference-scores":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                result = save_preference_scores(self.input_dir, rows)
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            if parsed.path == "/api/config":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                result = save_config_parameters(self.input_dir, rows)
                result["solution_cleared"] = clear_solution(self.output_path)
                self.send_json(result)
                return
            if parsed.path == "/api/solve":
                runtime_config = payload.get("runtime_config", {})
                if not isinstance(runtime_config, dict):
                    raise WebInputError([{"row": 0, "field": "runtime_config", "message": "runtime_config must be an object"}])
                self.send_json(run_solver(self.input_dir, self.output_path, runtime_config))
                return
            if parsed.path == "/api/evaluate-schedule":
                rows = payload.get("schedule", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "schedule", "message": "schedule must be a list"}])
                self.send_json(evaluate_schedule(self.input_dir, rows))
                return
            self.send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except WebInputError as exc:
            self.send_json({"ok": False, "errors": exc.errors}, HTTPStatus.BAD_REQUEST)
        except (CsvInputError, ValueError) as exc:
            self.send_json({"ok": False, "errors": [{"row": 0, "field": "request", "message": str(exc)}]}, HTTPStatus.BAD_REQUEST)

    def read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body)

    def read_multipart_files(self) -> Dict[str, bytes]:
        """Read uploaded CSV files from multipart/form-data."""
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise WebInputError([{"row": 0, "field": "files", "message": "Expected multipart/form-data"}])
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )
        files: Dict[str, bytes] = {}
        fields = form["files"] if "files" in form else []
        if not isinstance(fields, list):
            fields = [fields]
        for field in fields:
            file_name = Path(field.filename or "").name
            if not file_name:
                continue
            files[file_name] = field.file.read()
        return files

    def send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def build_handler(input_dir: Path, output_path: Path) -> type[ScheduleWebHandler]:
    """Create a request handler class bound to the selected CSV folder."""
    class BoundScheduleWebHandler(ScheduleWebHandler):
        pass

    BoundScheduleWebHandler.input_dir = input_dir
    BoundScheduleWebHandler.output_path = output_path
    return BoundScheduleWebHandler


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local trainer schedule web app.")
    parser.add_argument("--input", required=True, help="Folder containing scheduler CSV input files.")
    parser.add_argument("--output", default="solution.json", help="Path to write/read solver JSON output.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8000, help="Bind port. Defaults to 8000.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    input_dir = Path(args.input)
    output_path = Path(args.output)
    migration = normalize_numeric_ids(input_dir, output_path)
    handler = build_handler(input_dir, output_path)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"serving=http://{args.host}:{args.port}")
    print(f"input={input_dir}")
    print(f"output={output_path}")
    if migration["changed"]:
        print(f"id_migration_backup={migration['backup_dir']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
