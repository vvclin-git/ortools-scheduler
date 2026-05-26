"""Local web UI for editing scheduler CSV input and viewing solver output."""

from __future__ import annotations

import argparse
import cgi
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from run_solver_from_csv import (
    CsvInputError,
    OPTIONAL_FILES,
    REQUIRED_FILES,
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
    LessonRequest,
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
LESSON_HEADERS = ["lesson_id", "student_id", "venue_id", "duration_min", "must_schedule", "priority", "shared_session_id"]
BOOKING_HEADERS = ["booking_id", "lesson_id", "student_id", "venue_id", "start_datetime", "end_datetime", "status", "lock_level"]
TIMESLOT_RE = re.compile(
    r"^(?P<day>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})\s+"
    r"(?P<level>[A-Za-z_][A-Za-z0-9_-]*)\s+"
    r"(?P<score>-?\d+)$"
)
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


def parse_timeslot_string(student_id: str, value: str) -> List[StudentPreference]:
    """Parse compact weekly availability into preference rows."""
    text = value.strip()
    if not text:
        return []

    preferences: List[StudentPreference] = []
    for index, part in enumerate(text.split(";"), start=1):
        item = part.strip()
        if not item:
            continue
        match = TIMESLOT_RE.match(item)
        if not match:
            raise ValueError(
                f"Slot {index} must look like 'Mon 18:00-21:00 preferred 100'"
            )
        day = match.group("day")
        start = match.group("start")
        end = match.group("end")
        if day not in DAYS:
            raise ValueError(f"Slot {index} has invalid day {day!r}")
        if parse_time_to_minutes(start) >= parse_time_to_minutes(end):
            raise ValueError(f"Slot {index} start must be before end")
        preferences.append(
            StudentPreference(
                student_id=student_id,
                day=day,
                start=start,
                end=end,
                level=match.group("level"),
                score=int(match.group("score")),
            )
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


def write_csv_rows(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    """Write CSV rows, preserving headers even when the table is empty."""
    if rows:
        write_csv(path, rows)
        return
    path.write_text(",".join(headers) + "\n", encoding="utf-8", newline="")


def import_csv_files(input_dir: Path, files: Dict[str, bytes]) -> Dict[str, Any]:
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
    for file_name, content in files.items():
        path = input_dir / file_name
        path.write_bytes(content.replace(b"\r\n", b"\n"))
        written.append(file_name)

    request = load_solver_request(input_dir)
    validation_warnings = validate_solver_request(request)
    return {
        "ok": True,
        "imported_files": sorted(written),
        "validation_warnings": validation_warnings,
    }


def build_table_rows(input_dir: Path) -> List[Dict[str, str]]:
    """Build editable student/preference table rows."""
    students = load_students(input_dir)
    preferences = load_preferences(input_dir)
    by_student: Dict[str, List[StudentPreference]] = {}
    for pref in preferences:
        by_student.setdefault(pref.student_id, []).append(pref)

    return [
        {
            "student_id": student.student_id,
            "student_name": student.name,
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
            "planning_start": request.config.planning_start,
            "planning_end": request.config.planning_end,
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
    errors: List[Dict[str, Any]] = []
    student_rows: List[Dict[str, str]] = []
    preference_rows: List[Dict[str, str]] = []
    seen_ids = set()

    if not rows:
        raise WebInputError([{"row": 0, "field": "rows", "message": "At least one student row is required"}])

    for row_index, raw in enumerate(rows, start=1):
        student_id = str(raw.get("student_id", "")).strip()
        student_name = str(raw.get("student_name", "")).strip()
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
            parsed_preferences = parse_timeslot_string(student_id, timeslots)
        except ValueError as exc:
            parsed_preferences = []
            errors.append({"row": row_index, "field": "available_timeslots", "message": str(exc)})

        existing = existing_students.get(student_id)
        student_rows.append(
            {
                "student_id": student_id,
                "name": student_name,
                "default_venue_id": default_venue_id,
                "priority": str(existing.priority if existing is not None else 1),
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
    write_csv(input_dir / "students.csv", student_rows)
    if preference_rows:
        write_csv(input_dir / "preferences.csv", preference_rows)
    else:
        write_csv_rows(input_dir / "preferences.csv", ["student_id", "day", "start", "end", "level", "score"], [])
    return {"ok": True, "saved_rows": len(student_rows), "preference_rows": len(preference_rows)}


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


def run_solver(input_dir: Path, output_path: Path) -> Dict[str, Any]:
    """Load CSV input, run the optimizer, write JSON, and return the response."""
    request = load_solver_request(input_dir)
    validation_warnings = validate_solver_request(request)
    if validation_warnings:
        return {"ok": False, "validation_warnings": validation_warnings}
    response = solve_schedule(request)
    payload = asdict(response)
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
            payload = self.read_json_body()
            if parsed.path == "/api/students/preferences":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                self.send_json(save_student_preferences(self.input_dir, rows))
                return
            if parsed.path == "/api/lessons":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                self.send_json(save_lessons(self.input_dir, rows))
                return
            if parsed.path == "/api/venues/travel":
                venue_rows = payload.get("venues", [])
                travel_rows = payload.get("travel_times", [])
                if not isinstance(venue_rows, list) or not isinstance(travel_rows, list):
                    raise WebInputError([{"row": 0, "field": "venues", "message": "venues and travel_times must be lists"}])
                self.send_json(save_venues_and_travel_times(self.input_dir, venue_rows, travel_rows))
                return
            if parsed.path == "/api/trainer-availability":
                rows = payload.get("rows", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "rows", "message": "rows must be a list"}])
                self.send_json(save_trainer_availability(self.input_dir, rows))
                return
            if parsed.path == "/api/solve":
                self.send_json(run_solver(self.input_dir, self.output_path))
                return
            if parsed.path == "/api/evaluate-schedule":
                rows = payload.get("schedule", [])
                if not isinstance(rows, list):
                    raise WebInputError([{"row": 0, "field": "schedule", "message": "schedule must be a list"}])
                self.send_json(evaluate_schedule(self.input_dir, rows))
                return
            if parsed.path == "/api/import-csv":
                self.send_json(import_csv_files(self.input_dir, self.read_multipart_files()))
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
    handler = build_handler(input_dir, output_path)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"serving=http://{args.host}:{args.port}")
    print(f"input={input_dir}")
    print(f"output={output_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
