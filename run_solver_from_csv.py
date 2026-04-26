"""Run trainer_solver_mvp from CSV input files and write a JSON solution.

Usage:
    python run_solver_from_csv.py --input csv_demo_input --output solution.json

Required CSV files:
    config.csv
    students.csv
    venues.csv
    travel_times.csv
    lessons.csv
    preferences.csv
    coach_availability.csv

Optional CSV files:
    existing_bookings.csv
    absences.csv
    weights.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from trainer_solver_mvp import (
    Absence,
    CoachAvailability,
    ExistingBooking,
    FreezePolicy,
    LessonRequest,
    ObjectiveWeights,
    SolverConfig,
    SolverMode,
    SolverRequest,
    Student,
    StudentPreference,
    TravelTime,
    Venue,
    build_candidate_assignments,
    solve_schedule,
    validate_fixed_bookings,
    validate_request,
)


REQUIRED_FILES = [
    "config.csv",
    "students.csv",
    "venues.csv",
    "travel_times.csv",
    "lessons.csv",
    "preferences.csv",
    "coach_availability.csv",
]

OPTIONAL_FILES = [
    "existing_bookings.csv",
    "absences.csv",
    "weights.csv",
]

TEMPLATE_ROWS: Dict[str, List[Dict[str, str]]] = {
    "config.csv": [
        {"key": "mode", "value": "in_week_reschedule"},
        {"key": "planning_start", "value": "2026-05-04T09:00:00"},
        {"key": "planning_end", "value": "2026-05-10T22:00:00"},
        {"key": "slot_size_min", "value": "30"},
        {"key": "max_solve_seconds", "value": "5.0"},
        {"key": "freeze_now", "value": "2026-05-06T14:00:00"},
        {"key": "freeze_buffer_hours", "value": "4"},
    ],
    "students.csv": [
        {"student_id": "stu_alice", "name": "王小明", "default_venue_id": "gym_a", "priority": "2"},
    ],
    "venues.csv": [
        {"venue_id": "gym_a", "name": "台北教室A"},
    ],
    "travel_times.csv": [
        {"from_venue_id": "gym_a", "to_venue_id": "gym_a", "travel_min": "0"},
    ],
    "lessons.csv": [
        {
            "lesson_id": "lesson_alice",
            "student_id": "stu_alice",
            "venue_id": "gym_a",
            "duration_min": "60",
            "must_schedule": "TRUE",
            "priority": "2",
            "shared_session_id": "",
        },
    ],
    "preferences.csv": [
        {
            "student_id": "stu_alice",
            "day": "Mon",
            "start": "18:00",
            "end": "21:00",
            "level": "preferred",
            "score": "100",
        },
    ],
    "coach_availability.csv": [
        {"day": "Mon", "start": "10:00", "end": "22:00", "score": "0"},
    ],
    "existing_bookings.csv": [
        {
            "booking_id": "book_alice",
            "lesson_id": "lesson_alice",
            "student_id": "stu_alice",
            "venue_id": "gym_a",
            "start_datetime": "2026-05-04T18:00:00",
            "end_datetime": "2026-05-04T19:00:00",
            "status": "confirmed",
            "lock_level": "1",
        },
    ],
    "absences.csv": [
        {
            "entity_type": "student",
            "entity_id": "stu_alice",
            "start_datetime": "2026-05-07T18:00:00",
            "end_datetime": "2026-05-07T22:00:00",
            "reason": "Example absence",
        },
    ],
    "weights.csv": [
        {"key": "lesson_priority", "value": "20"},
        {"key": "keep_original_bonus", "value": "800"},
        {"key": "move_confirmed_penalty", "value": "1000"},
        {"key": "move_draft_penalty", "value": "200"},
        {"key": "cancel_optional_penalty", "value": "3000"},
        {"key": "cross_venue_pair_penalty_per_min", "value": "1"},
    ],
}


class CsvInputError(ValueError):
    """Raised when CSV input is missing or malformed."""


def read_dict_rows(path: Path, required: bool = True) -> List[Dict[str, str]]:
    """Read a CSV file into a list of dictionaries."""
    if not path.exists():
        if required:
            raise CsvInputError(f"Missing required CSV file: {path}")
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise CsvInputError(f"CSV file has no header: {path}")
        return [normalize_row(row) for row in reader]


def normalize_row(row: Dict[str, Optional[str]]) -> Dict[str, str]:
    """Trim spaces and convert None cells to empty strings."""
    return {
        (key or "").strip(): (value or "").strip()
        for key, value in row.items()
    }


def read_key_value_csv(path: Path, required: bool = True) -> Dict[str, str]:
    """Read key,value style CSV into a dictionary."""
    rows = read_dict_rows(path, required=required)
    if not rows:
        return {}

    if "key" not in rows[0] or "value" not in rows[0]:
        raise CsvInputError(f"Expected columns 'key,value' in {path}")

    data: Dict[str, str] = {}
    for row in rows:
        key = row.get("key", "")
        if not key:
            continue
        data[key] = row.get("value", "")
    return data


def require(row: Dict[str, str], column: str, file_name: str) -> str:
    """Return a required CSV cell value."""
    value = row.get(column, "")
    if value == "":
        raise CsvInputError(f"Missing '{column}' in {file_name}: {row}")
    return value


def parse_int(value: str, default: Optional[int] = None) -> int:
    """Parse an integer with an optional default for blank cells."""
    if value == "" and default is not None:
        return default
    return int(value)


def parse_float(value: str, default: Optional[float] = None) -> float:
    """Parse a float with an optional default for blank cells."""
    if value == "" and default is not None:
        return default
    return float(value)


def parse_bool(value: str, default: Optional[bool] = None) -> bool:
    """Parse common CSV boolean values."""
    if value == "" and default is not None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "t", "1", "yes", "y"}:
        return True
    if normalized in {"false", "f", "0", "no", "n"}:
        return False
    raise CsvInputError(f"Invalid boolean value: {value!r}")


def parse_mode(value: str) -> SolverMode:
    """Parse a solver mode string."""
    normalized = value.strip()
    try:
        return SolverMode(normalized)
    except ValueError as exc:
        valid = ", ".join(mode.value for mode in SolverMode)
        raise CsvInputError(f"Invalid solver mode {value!r}. Valid values: {valid}") from exc


def load_weights(input_dir: Path) -> ObjectiveWeights:
    """Load optional objective weights from weights.csv."""
    raw = read_key_value_csv(input_dir / "weights.csv", required=False)
    defaults = ObjectiveWeights()

    return ObjectiveWeights(
        lesson_priority=parse_int(raw.get("lesson_priority", ""), defaults.lesson_priority),
        keep_original_bonus=parse_int(raw.get("keep_original_bonus", ""), defaults.keep_original_bonus),
        move_confirmed_penalty=parse_int(raw.get("move_confirmed_penalty", ""), defaults.move_confirmed_penalty),
        move_draft_penalty=parse_int(raw.get("move_draft_penalty", ""), defaults.move_draft_penalty),
        cancel_optional_penalty=parse_int(raw.get("cancel_optional_penalty", ""), defaults.cancel_optional_penalty),
        cross_venue_pair_penalty_per_min=parse_int(
            raw.get("cross_venue_pair_penalty_per_min", ""),
            defaults.cross_venue_pair_penalty_per_min,
        ),
    )


def load_config(input_dir: Path) -> SolverConfig:
    """Load solver config from config.csv."""
    raw = read_key_value_csv(input_dir / "config.csv", required=True)
    weights = load_weights(input_dir)

    freeze_policy = None
    freeze_now = raw.get("freeze_now", "")
    if freeze_now:
        freeze_policy = FreezePolicy(
            now=freeze_now,
            freeze_buffer_hours=parse_int(raw.get("freeze_buffer_hours", ""), 4),
        )

    return SolverConfig(
        mode=parse_mode(raw.get("mode", SolverMode.WEEKLY_PLANNING.value)),
        planning_start=require(raw, "planning_start", "config.csv"),
        planning_end=require(raw, "planning_end", "config.csv"),
        slot_size_min=parse_int(raw.get("slot_size_min", ""), 30),
        max_solve_seconds=parse_float(raw.get("max_solve_seconds", ""), 10.0),
        freeze_policy=freeze_policy,
        weights=weights,
    )


def load_students(input_dir: Path) -> List[Student]:
    """Load students.csv."""
    rows = read_dict_rows(input_dir / "students.csv")
    return [
        Student(
            student_id=require(row, "student_id", "students.csv"),
            name=require(row, "name", "students.csv"),
            default_venue_id=require(row, "default_venue_id", "students.csv"),
            priority=parse_int(row.get("priority", ""), 1),
        )
        for row in rows
    ]


def load_venues(input_dir: Path) -> List[Venue]:
    """Load venues.csv."""
    rows = read_dict_rows(input_dir / "venues.csv")
    return [
        Venue(
            venue_id=require(row, "venue_id", "venues.csv"),
            name=require(row, "name", "venues.csv"),
        )
        for row in rows
    ]


def load_travel_times(input_dir: Path) -> List[TravelTime]:
    """Load travel_times.csv."""
    rows = read_dict_rows(input_dir / "travel_times.csv")
    return [
        TravelTime(
            from_venue_id=require(row, "from_venue_id", "travel_times.csv"),
            to_venue_id=require(row, "to_venue_id", "travel_times.csv"),
            travel_min=parse_int(require(row, "travel_min", "travel_times.csv")),
        )
        for row in rows
    ]


def load_lessons(input_dir: Path) -> List[LessonRequest]:
    """Load lessons.csv."""
    rows = read_dict_rows(input_dir / "lessons.csv")
    return [
        LessonRequest(
            lesson_id=require(row, "lesson_id", "lessons.csv"),
            student_id=require(row, "student_id", "lessons.csv"),
            venue_id=require(row, "venue_id", "lessons.csv"),
            duration_min=parse_int(row.get("duration_min", ""), 60),
            must_schedule=parse_bool(row.get("must_schedule", ""), True),
            priority=parse_int(row.get("priority", ""), 1),
            shared_session_id=row.get("shared_session_id", "") or None,
        )
        for row in rows
    ]


def load_preferences(input_dir: Path) -> List[StudentPreference]:
    """Load preferences.csv."""
    rows = read_dict_rows(input_dir / "preferences.csv")
    return [
        StudentPreference(
            student_id=require(row, "student_id", "preferences.csv"),
            day=require(row, "day", "preferences.csv"),
            start=require(row, "start", "preferences.csv"),
            end=require(row, "end", "preferences.csv"),
            level=require(row, "level", "preferences.csv"),
            score=parse_int(require(row, "score", "preferences.csv")),
        )
        for row in rows
    ]


def load_coach_availability(input_dir: Path) -> List[CoachAvailability]:
    """Load coach_availability.csv."""
    rows = read_dict_rows(input_dir / "coach_availability.csv")
    return [
        CoachAvailability(
            day=require(row, "day", "coach_availability.csv"),
            start=require(row, "start", "coach_availability.csv"),
            end=require(row, "end", "coach_availability.csv"),
            score=parse_int(row.get("score", ""), 0),
        )
        for row in rows
    ]


def load_existing_bookings(input_dir: Path) -> List[ExistingBooking]:
    """Load optional existing_bookings.csv."""
    rows = read_dict_rows(input_dir / "existing_bookings.csv", required=False)
    return [
        ExistingBooking(
            booking_id=require(row, "booking_id", "existing_bookings.csv"),
            lesson_id=require(row, "lesson_id", "existing_bookings.csv"),
            student_id=require(row, "student_id", "existing_bookings.csv"),
            venue_id=require(row, "venue_id", "existing_bookings.csv"),
            start_datetime=require(row, "start_datetime", "existing_bookings.csv"),
            end_datetime=require(row, "end_datetime", "existing_bookings.csv"),
            status=row.get("status", "confirmed") or "confirmed",
            lock_level=parse_int(row.get("lock_level", ""), 1),
        )
        for row in rows
    ]


def load_absences(input_dir: Path) -> List[Absence]:
    """Load optional absences.csv."""
    rows = read_dict_rows(input_dir / "absences.csv", required=False)
    return [
        Absence(
            entity_type=require(row, "entity_type", "absences.csv"),
            entity_id=require(row, "entity_id", "absences.csv"),
            start_datetime=require(row, "start_datetime", "absences.csv"),
            end_datetime=require(row, "end_datetime", "absences.csv"),
            reason=row.get("reason", ""),
        )
        for row in rows
    ]


def load_solver_request(input_dir: Path) -> SolverRequest:
    """Load a complete SolverRequest from a CSV input folder."""
    validate_required_files(input_dir)
    return SolverRequest(
        config=load_config(input_dir),
        students=load_students(input_dir),
        venues=load_venues(input_dir),
        travel_times=load_travel_times(input_dir),
        lessons=load_lessons(input_dir),
        preferences=load_preferences(input_dir),
        coach_availability=load_coach_availability(input_dir),
        absences=load_absences(input_dir),
        existing_bookings=load_existing_bookings(input_dir),
    )


def validate_required_files(input_dir: Path) -> None:
    """Validate that all required CSV files exist."""
    missing = [name for name in REQUIRED_FILES if not (input_dir / name).exists()]
    if missing:
        raise CsvInputError(f"Missing required CSV files in {input_dir}: {missing}")


def write_json(path: Path, payload: Any) -> None:
    """Write a JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    """Write CSV rows using the dictionary keys as headers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def create_csv_template(output_dir: Path, force: bool = False) -> List[Path]:
    """Create a user-editable CSV input template folder."""
    files = REQUIRED_FILES + OPTIONAL_FILES
    existing = [output_dir / name for name in files if (output_dir / name).exists()]
    if existing and not force:
        names = ", ".join(str(path) for path in existing)
        raise CsvInputError(f"Refusing to overwrite existing template files: {names}")

    written = []
    for name in files:
        path = output_dir / name
        write_csv(path, TEMPLATE_ROWS[name])
        written.append(path)
    return written


def build_debug_payload(request: SolverRequest) -> Dict[str, Any]:
    """Build a JSON-serializable debug payload for request and candidates."""
    candidates, warnings = build_candidate_assignments(request)
    return {
        "request": asdict(request),
        "candidate_count": len(candidates),
        "candidates": [
            {
                **asdict(candidate),
                "start": candidate.start.isoformat(timespec="minutes"),
                "end": candidate.end.isoformat(timespec="minutes"),
            }
            for candidate in candidates
        ],
        "warnings": warnings,
    }


def format_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Format a compact ASCII table."""
    values = [[str(value) for value in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in values)) if values else len(header)
        for index, header in enumerate(headers)
    ]

    def format_row(row: List[Any]) -> str:
        return " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))

    separator = "-+-".join("-" * width for width in widths)
    lines = [format_row(headers), separator]
    lines.extend(format_row(row) for row in values)
    return "\n".join(lines)


def format_solution_table(response: Any) -> str:
    """Format a solver response for terminal review."""
    lines = [
        "Solution",
        f"status={response.status}",
        (
            f"scheduled={response.summary.scheduled_lessons} "
            f"unscheduled={response.summary.unscheduled_lessons} "
            f"changed={response.summary.changed_lessons} "
            f"candidates={getattr(response.summary, 'candidate_count', 0)}"
        ),
        "",
        "Schedule",
    ]

    if response.schedule:
        lines.append(
            format_table(
                ["lesson", "student", "venue", "start", "end", "preference", "changed"],
                [
                    [
                        item.lesson_id,
                        item.student_name,
                        item.venue_name,
                        item.start_datetime,
                        item.end_datetime,
                        item.preference_level,
                        "yes" if item.is_changed else "no",
                    ]
                    for item in response.schedule
                ],
            )
        )
    else:
        lines.append("(none)")

    lines.extend(["", "Changes"])
    if response.changes:
        lines.append(
            format_table(
                ["lesson", "student", "type", "old_start", "new_start", "impact"],
                [
                    [
                        item.lesson_id,
                        item.student_name,
                        item.change_type,
                        item.old_start or "",
                        item.new_start or "",
                        item.impact_score,
                    ]
                    for item in response.changes
                ],
            )
        )
    else:
        lines.append("(none)")

    if response.warnings:
        lines.extend(["", "Warnings"])
        lines.extend(f"- {warning}" for warning in response.warnings)

    return "\n".join(lines)


def validate_solver_request(request: SolverRequest) -> List[str]:
    """Run solver-side validation without solving."""
    warnings = validate_request(request)
    if warnings:
        return warnings
    return validate_fixed_bookings(request)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run trainer scheduler solver from CSV files.")
    parser.add_argument("--input", default=None, help="Folder containing CSV input files.")
    parser.add_argument("--output", default="solution.json", help="Output solution JSON path.")
    parser.add_argument(
        "--init-template",
        default=None,
        help="Create a CSV input template folder and exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --init-template to overwrite existing template CSV files.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Load and validate CSV input without solving.",
    )
    parser.add_argument(
        "--dump-request",
        default=None,
        help="Optional path to write normalized request and generated candidates as JSON.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a short summary after solving.",
    )
    parser.add_argument(
        "--print-solution",
        action="store_true",
        help="Print a compact schedule and changes table after solving.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry point."""
    args = parse_args(argv)

    if args.init_template:
        written = create_csv_template(Path(args.init_template), force=args.force)
        print(f"created_template={args.init_template}")
        print(f"files={len(written)}")
        print("Edit these CSV files directly, or use Excel/Google Sheets and export each sheet as CSV.")
        return

    if not args.input:
        raise CsvInputError("Missing --input. Use --input CSV_FOLDER or --init-template TEMPLATE_FOLDER.")

    input_dir = Path(args.input)
    output_path = Path(args.output)

    request = load_solver_request(input_dir)

    if args.validate_only:
        warnings = validate_solver_request(request)
        if warnings:
            print("validation=failed")
            for warning in warnings:
                print(f"warning={warning}")
            raise SystemExit(1)
        print("validation=ok")
        return

    if args.dump_request:
        write_json(Path(args.dump_request), build_debug_payload(request))

    response = solve_schedule(request)
    write_json(output_path, asdict(response))

    if args.print_summary:
        print(f"status={response.status}")
        print(f"scheduled={response.summary.scheduled_lessons}")
        print(f"unscheduled={response.summary.unscheduled_lessons}")
        print(f"changed={response.summary.changed_lessons}")
        print(f"output={output_path}")

    if args.print_solution:
        print(format_solution_table(response))


if __name__ == "__main__":
    main()
