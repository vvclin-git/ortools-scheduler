from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
import json

try:
    from ortools.sat.python import cp_model
except ImportError as exc:
    cp_model = None
    _ORTOOLS_IMPORT_ERROR = exc
else:
    _ORTOOLS_IMPORT_ERROR = None


DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class SolverMode(str, Enum):
    WEEKLY_PLANNING = "weekly_planning"
    IN_WEEK_RESCHEDULE = "in_week_reschedule"


@dataclass(frozen=True)
class Venue:
    venue_id: str
    name: str


@dataclass(frozen=True)
class Student:
    student_id: str
    name: str
    default_venue_id: str
    priority: int = 1


@dataclass(frozen=True)
class TravelTime:
    from_venue_id: str
    to_venue_id: str
    travel_min: int


@dataclass(frozen=True)
class LessonRequest:
    lesson_id: str
    student_id: str
    venue_id: str
    duration_min: int = 60
    must_schedule: bool = True
    priority: int = 1


@dataclass(frozen=True)
class StudentPreference:
    student_id: str
    day: str
    start: str
    end: str
    level: str
    score: int


@dataclass(frozen=True)
class CoachAvailability:
    day: str
    start: str
    end: str
    score: int = 0


@dataclass(frozen=True)
class Absence:
    entity_type: str  # "coach", "student", or "venue"
    entity_id: str
    start_datetime: str
    end_datetime: str
    reason: str = ""


@dataclass(frozen=True)
class ExistingBooking:
    booking_id: str
    lesson_id: str
    student_id: str
    venue_id: str
    start_datetime: str
    end_datetime: str
    status: str = "confirmed"
    lock_level: int = 1


@dataclass(frozen=True)
class FreezePolicy:
    now: str
    freeze_buffer_hours: int = 4

    @property
    def freeze_before(self) -> datetime:
        return parse_dt(self.now) + timedelta(hours=self.freeze_buffer_hours)


@dataclass(frozen=True)
class ObjectiveWeights:
    lesson_priority: int = 20
    keep_original_bonus: int = 800
    move_confirmed_penalty: int = 1000
    move_draft_penalty: int = 200
    cancel_optional_penalty: int = 3000
    cross_venue_pair_penalty_per_min: int = 1


@dataclass(frozen=True)
class SolverConfig:
    mode: SolverMode
    planning_start: str
    planning_end: str
    slot_size_min: int = 30
    max_solve_seconds: float = 10.0
    freeze_policy: Optional[FreezePolicy] = None
    weights: ObjectiveWeights = field(default_factory=ObjectiveWeights)


@dataclass(frozen=True)
class SolverRequest:
    config: SolverConfig
    students: List[Student]
    venues: List[Venue]
    travel_times: List[TravelTime]
    lessons: List[LessonRequest]
    preferences: List[StudentPreference]
    coach_availability: List[CoachAvailability]
    absences: List[Absence] = field(default_factory=list)
    existing_bookings: List[ExistingBooking] = field(default_factory=list)


@dataclass(frozen=True)
class ScheduledLesson:
    lesson_id: str
    student_id: str
    student_name: str
    venue_id: str
    venue_name: str
    start_datetime: str
    end_datetime: str
    preference_level: str
    preference_score: int
    is_changed: bool


@dataclass(frozen=True)
class ScheduleChange:
    lesson_id: str
    student_id: str
    student_name: str
    change_type: str
    old_start: Optional[str]
    new_start: Optional[str]
    old_venue_id: Optional[str]
    new_venue_id: Optional[str]
    reason: str
    impact_score: int


@dataclass(frozen=True)
class SolverSummary:
    scheduled_lessons: int
    unscheduled_lessons: int
    changed_lessons: int
    affected_students: int
    total_cross_venue_pair_penalty: int
    objective_value: Optional[int]


@dataclass(frozen=True)
class SolverResponse:
    status: str
    summary: SolverSummary
    schedule: List[ScheduledLesson]
    changes: List[ScheduleChange]
    warnings: List[str]


@dataclass(frozen=True)
class CandidateAssignment:
    candidate_id: str
    lesson_id: str
    student_id: str
    venue_id: str
    start: datetime
    end: datetime
    preference_level: str
    preference_score: int
    candidate_score: int
    is_original_time: bool
    is_fixed: bool = False


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def parse_time_min(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def day_name(dt: datetime) -> str:
    return DAYS[dt.weekday()]


def dt_to_slot_range(start: datetime, end: datetime, slot_size_min: int) -> List[datetime]:
    out = []
    current = start
    step = timedelta(minutes=slot_size_min)
    while current < end:
        out.append(current)
        current += step
    return out


def fits_weekly_window(start: datetime, end: datetime, day: str, win_start: str, win_end: str) -> bool:
    if day_name(start) != day:
        return False
    if start.date() != end.date():
        return False
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    return parse_time_min(win_start) <= start_min and end_min <= parse_time_min(win_end)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def build_travel_lookup(travel_times: List[TravelTime]) -> Dict[Tuple[str, str], int]:
    lookup = {}
    for row in travel_times:
        lookup[(row.from_venue_id, row.to_venue_id)] = row.travel_min
    return lookup


def get_travel_min(lookup: Dict[Tuple[str, str], int], from_venue: str, to_venue: str) -> int:
    if from_venue == to_venue:
        return 0
    return lookup.get((from_venue, to_venue), 10_000)


def get_preference_score(
    student_id: str,
    start: datetime,
    end: datetime,
    preferences: List[StudentPreference],
) -> Optional[Tuple[str, int]]:
    matches = []
    for pref in preferences:
        if pref.student_id != student_id:
            continue
        if fits_weekly_window(start, end, pref.day, pref.start, pref.end):
            matches.append((pref.level, pref.score))
    if not matches:
        return None
    return max(matches, key=lambda item: item[1])


def get_coach_availability_score(
    start: datetime,
    end: datetime,
    windows: List[CoachAvailability],
) -> Optional[int]:
    scores = []
    for win in windows:
        if fits_weekly_window(start, end, win.day, win.start, win.end):
            scores.append(win.score)
    if not scores:
        return None
    return max(scores)


def has_absence_conflict(
    lesson: LessonRequest,
    start: datetime,
    end: datetime,
    absences: List[Absence],
) -> bool:
    for absence in absences:
        abs_start = parse_dt(absence.start_datetime)
        abs_end = parse_dt(absence.end_datetime)
        if not overlaps(start, end, abs_start, abs_end):
            continue
        if absence.entity_type == "coach":
            return True
        if absence.entity_type == "student" and absence.entity_id == lesson.student_id:
            return True
        if absence.entity_type == "venue" and absence.entity_id == lesson.venue_id:
            return True
    return False


def build_candidate_assignments(req: SolverRequest) -> Tuple[List[CandidateAssignment], List[str]]:
    warnings = []
    config = req.config
    weights = config.weights
    horizon_start = parse_dt(config.planning_start)
    horizon_end = parse_dt(config.planning_end)
    slots = dt_to_slot_range(horizon_start, horizon_end, config.slot_size_min)
    booking_by_lesson = {b.lesson_id: b for b in req.existing_bookings}
    candidates: List[CandidateAssignment] = []

    freeze_before = None
    if config.mode == SolverMode.IN_WEEK_RESCHEDULE and config.freeze_policy is not None:
        freeze_before = config.freeze_policy.freeze_before

    for lesson in req.lessons:
        existing = booking_by_lesson.get(lesson.lesson_id)
        is_frozen = False
        if existing is not None:
            existing_start = parse_dt(existing.start_datetime)
            if existing.status in {"completed", "in_progress", "locked"}:
                is_frozen = True
            if existing.lock_level >= 3:
                is_frozen = True
            if freeze_before is not None and existing_start < freeze_before:
                is_frozen = True

        if is_frozen and existing is not None:
            pref = get_preference_score(
                lesson.student_id,
                parse_dt(existing.start_datetime),
                parse_dt(existing.end_datetime),
                req.preferences,
            ) or ("locked", 0)
            candidates.append(
                CandidateAssignment(
                    candidate_id=f"cand_{len(candidates):04d}",
                    lesson_id=lesson.lesson_id,
                    student_id=lesson.student_id,
                    venue_id=lesson.venue_id,
                    start=parse_dt(existing.start_datetime),
                    end=parse_dt(existing.end_datetime),
                    preference_level=pref[0],
                    preference_score=pref[1],
                    candidate_score=10_000,
                    is_original_time=True,
                    is_fixed=True,
                )
            )
            continue

        for start in slots:
            end = start + timedelta(minutes=lesson.duration_min)
            if end > horizon_end:
                continue
            if freeze_before is not None and start < freeze_before:
                continue
            coach_score = get_coach_availability_score(start, end, req.coach_availability)
            if coach_score is None:
                continue
            pref = get_preference_score(lesson.student_id, start, end, req.preferences)
            if pref is None:
                continue
            if has_absence_conflict(lesson, start, end, req.absences):
                continue

            is_original = False
            change_penalty = 0
            if existing is not None:
                old_start = parse_dt(existing.start_datetime)
                old_end = parse_dt(existing.end_datetime)
                is_original = start == old_start and end == old_end
                if is_original:
                    change_penalty = -weights.keep_original_bonus
                elif existing.status == "confirmed":
                    change_penalty = weights.move_confirmed_penalty
                else:
                    change_penalty = weights.move_draft_penalty

            score = (
                pref[1]
                + coach_score
                + lesson.priority * weights.lesson_priority
                - change_penalty
            )
            candidates.append(
                CandidateAssignment(
                    candidate_id=f"cand_{len(candidates):04d}",
                    lesson_id=lesson.lesson_id,
                    student_id=lesson.student_id,
                    venue_id=lesson.venue_id,
                    start=start,
                    end=end,
                    preference_level=pref[0],
                    preference_score=pref[1],
                    candidate_score=score,
                    is_original_time=is_original,
                )
            )

        if not any(c.lesson_id == lesson.lesson_id for c in candidates):
            warnings.append(f"No candidate generated for lesson_id={lesson.lesson_id}")

    return candidates, warnings


def candidates_can_coexist(
    a: CandidateAssignment,
    b: CandidateAssignment,
    travel_lookup: Dict[Tuple[str, str], int],
) -> bool:
    a_to_b = get_travel_min(travel_lookup, a.venue_id, b.venue_id)
    b_to_a = get_travel_min(travel_lookup, b.venue_id, a.venue_id)
    a_before_b = a.end + timedelta(minutes=a_to_b) <= b.start
    b_before_a = b.end + timedelta(minutes=b_to_a) <= a.start
    return a_before_b or b_before_a


def solve_schedule(req: SolverRequest) -> SolverResponse:
    if cp_model is None:
        raise RuntimeError(
            "OR-Tools is not installed. Install it with: pip install ortools"
        ) from _ORTOOLS_IMPORT_ERROR

    config = req.config
    weights = config.weights
    students = {s.student_id: s for s in req.students}
    venues = {v.venue_id: v for v in req.venues}
    booking_by_lesson = {b.lesson_id: b for b in req.existing_bookings}
    candidates, warnings = build_candidate_assignments(req)
    candidates_by_lesson: Dict[str, List[CandidateAssignment]] = {}
    for cand in candidates:
        candidates_by_lesson.setdefault(cand.lesson_id, []).append(cand)

    missing_required = [
        lesson.lesson_id
        for lesson in req.lessons
        if lesson.must_schedule and not candidates_by_lesson.get(lesson.lesson_id)
    ]
    if missing_required:
        return SolverResponse(
            status="INFEASIBLE_INPUT",
            summary=SolverSummary(0, len(req.lessons), 0, 0, 0, None),
            schedule=[],
            changes=[],
            warnings=warnings + [f"Required lessons have no candidates: {missing_required}"],
        )

    model = cp_model.CpModel()
    x = {cand.candidate_id: model.NewBoolVar(cand.candidate_id) for cand in candidates}

    for lesson in req.lessons:
        lesson_vars = [x[c.candidate_id] for c in candidates_by_lesson.get(lesson.lesson_id, [])]
        if not lesson_vars:
            continue
        if lesson.must_schedule:
            model.Add(sum(lesson_vars) == 1)
        else:
            model.Add(sum(lesson_vars) <= 1)

    for cand in candidates:
        if cand.is_fixed:
            model.Add(x[cand.candidate_id] == 1)

    travel_lookup = build_travel_lookup(req.travel_times)
    objective_terms = []
    cross_venue_pair_vars = []

    for cand in candidates:
        objective_terms.append(cand.candidate_score * x[cand.candidate_id])

    for i in range(len(candidates)):
        a = candidates[i]
        for j in range(i + 1, len(candidates)):
            b = candidates[j]
            if a.lesson_id == b.lesson_id:
                continue
            if not candidates_can_coexist(a, b, travel_lookup):
                model.Add(x[a.candidate_id] + x[b.candidate_id] <= 1)
                continue
            if a.start.date() == b.start.date() and a.venue_id != b.venue_id:
                both = model.NewBoolVar(f"both_{a.candidate_id}_{b.candidate_id}")
                model.Add(both <= x[a.candidate_id])
                model.Add(both <= x[b.candidate_id])
                model.Add(both >= x[a.candidate_id] + x[b.candidate_id] - 1)
                penalty_min = min(
                    get_travel_min(travel_lookup, a.venue_id, b.venue_id),
                    get_travel_min(travel_lookup, b.venue_id, a.venue_id),
                )
                penalty = penalty_min * weights.cross_venue_pair_penalty_per_min
                objective_terms.append(-penalty * both)
                cross_venue_pair_vars.append((both, penalty))

    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.max_solve_seconds
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolverResponse(
            status=solver.StatusName(status),
            summary=SolverSummary(0, len(req.lessons), 0, 0, 0, None),
            schedule=[],
            changes=[],
            warnings=warnings + ["No feasible schedule found."],
        )

    selected = [cand for cand in candidates if solver.Value(x[cand.candidate_id]) == 1]
    selected.sort(key=lambda c: c.start)
    scheduled: List[ScheduledLesson] = []
    changes: List[ScheduleChange] = []

    for cand in selected:
        student = students[cand.student_id]
        venue = venues[cand.venue_id]
        existing = booking_by_lesson.get(cand.lesson_id)
        is_changed = False
        old_start = None
        old_venue = None
        change_type = "added"
        impact_score = 0

        if existing is not None:
            old_start = existing.start_datetime
            old_venue = existing.venue_id
            same_time = parse_dt(existing.start_datetime) == cand.start
            same_venue = existing.venue_id == cand.venue_id
            is_changed = not (same_time and same_venue)
            change_type = "unchanged" if not is_changed else "moved"
            if is_changed:
                impact_score = weights.move_confirmed_penalty if existing.status == "confirmed" else weights.move_draft_penalty

        scheduled.append(
            ScheduledLesson(
                lesson_id=cand.lesson_id,
                student_id=cand.student_id,
                student_name=student.name,
                venue_id=cand.venue_id,
                venue_name=venue.name,
                start_datetime=cand.start.isoformat(timespec="minutes"),
                end_datetime=cand.end.isoformat(timespec="minutes"),
                preference_level=cand.preference_level,
                preference_score=cand.preference_score,
                is_changed=is_changed,
            )
        )
        changes.append(
            ScheduleChange(
                lesson_id=cand.lesson_id,
                student_id=cand.student_id,
                student_name=student.name,
                change_type=change_type,
                old_start=old_start,
                new_start=cand.start.isoformat(timespec="minutes"),
                old_venue_id=old_venue,
                new_venue_id=cand.venue_id,
                reason="solver_selected_candidate",
                impact_score=impact_score,
            )
        )

    selected_lesson_ids = {c.lesson_id for c in selected}
    unscheduled = [lesson for lesson in req.lessons if lesson.lesson_id not in selected_lesson_ids]
    for lesson in unscheduled:
        student = students[lesson.student_id]
        changes.append(
            ScheduleChange(
                lesson_id=lesson.lesson_id,
                student_id=lesson.student_id,
                student_name=student.name,
                change_type="unscheduled",
                old_start=None,
                new_start=None,
                old_venue_id=None,
                new_venue_id=None,
                reason="no_selected_candidate",
                impact_score=weights.cancel_optional_penalty,
            )
        )

    changed_lessons = sum(1 for ch in changes if ch.change_type == "moved")
    affected_students = len({ch.student_id for ch in changes if ch.change_type in {"moved", "unscheduled"}})
    total_cross_penalty = sum(p for var, p in cross_venue_pair_vars if solver.Value(var) == 1)

    return SolverResponse(
        status=solver.StatusName(status),
        summary=SolverSummary(
            scheduled_lessons=len(scheduled),
            unscheduled_lessons=len(unscheduled),
            changed_lessons=changed_lessons,
            affected_students=affected_students,
            total_cross_venue_pair_penalty=total_cross_penalty,
            objective_value=int(solver.ObjectiveValue()),
        ),
        schedule=scheduled,
        changes=changes,
        warnings=warnings,
    )


def make_demo_request() -> SolverRequest:
    return SolverRequest(
        config=SolverConfig(
            mode=SolverMode.IN_WEEK_RESCHEDULE,
            planning_start="2026-05-04T09:00:00",
            planning_end="2026-05-10T22:00:00",
            slot_size_min=30,
            max_solve_seconds=5.0,
            freeze_policy=FreezePolicy(
                now="2026-05-06T14:00:00",
                freeze_buffer_hours=4,
            ),
            weights=ObjectiveWeights(),
        ),
        students=[
            Student("stu_alice", "Alice", "gym_a", priority=2),
            Student("stu_bob", "Bob", "gym_b", priority=1),
            Student("stu_carol", "Carol", "gym_b", priority=1),
            Student("stu_david", "David", "gym_a", priority=1),
        ],
        venues=[
            Venue("gym_a", "左營館"),
            Venue("gym_b", "鳳山館"),
        ],
        travel_times=[
            TravelTime("gym_a", "gym_a", 0),
            TravelTime("gym_b", "gym_b", 0),
            TravelTime("gym_a", "gym_b", 50),
            TravelTime("gym_b", "gym_a", 50),
        ],
        lessons=[
            LessonRequest("lesson_alice", "stu_alice", "gym_a", duration_min=60, priority=2),
            LessonRequest("lesson_bob", "stu_bob", "gym_b", duration_min=60, priority=1),
            LessonRequest("lesson_carol", "stu_carol", "gym_b", duration_min=60, priority=1),
            LessonRequest("lesson_david", "stu_david", "gym_a", duration_min=60, priority=1),
        ],
        preferences=[
            StudentPreference("stu_alice", "Mon", "18:00", "21:00", "preferred", 100),
            StudentPreference("stu_alice", "Wed", "19:00", "21:00", "acceptable", 40),
            StudentPreference("stu_bob", "Thu", "19:00", "21:00", "preferred", 100),
            StudentPreference("stu_bob", "Fri", "19:00", "21:00", "acceptable", 60),
            StudentPreference("stu_carol", "Thu", "20:00", "22:00", "preferred", 100),
            StudentPreference("stu_carol", "Fri", "18:00", "21:00", "acceptable", 50),
            StudentPreference("stu_david", "Fri", "18:00", "21:00", "preferred", 100),
        ],
        coach_availability=[
            CoachAvailability("Mon", "10:00", "22:00"),
            CoachAvailability("Tue", "10:00", "22:00"),
            CoachAvailability("Wed", "10:00", "22:00"),
            CoachAvailability("Thu", "10:00", "22:00"),
            CoachAvailability("Fri", "10:00", "22:00"),
        ],
        existing_bookings=[
            ExistingBooking(
                "book_alice",
                "lesson_alice",
                "stu_alice",
                "gym_a",
                "2026-05-04T18:00:00",
                "2026-05-04T19:00:00",
                status="completed",
                lock_level=3,
            ),
            ExistingBooking(
                "book_bob",
                "lesson_bob",
                "stu_bob",
                "gym_b",
                "2026-05-07T19:00:00",
                "2026-05-07T20:00:00",
                status="confirmed",
                lock_level=1,
            ),
            ExistingBooking(
                "book_carol",
                "lesson_carol",
                "stu_carol",
                "gym_b",
                "2026-05-07T20:30:00",
                "2026-05-07T21:30:00",
                status="confirmed",
                lock_level=1,
            ),
            ExistingBooking(
                "book_david",
                "lesson_david",
                "stu_david",
                "gym_a",
                "2026-05-08T18:00:00",
                "2026-05-08T19:00:00",
                status="confirmed",
                lock_level=1,
            ),
        ],
        absences=[
            Absence(
                entity_type="student",
                entity_id="stu_bob",
                start_datetime="2026-05-07T18:00:00",
                end_datetime="2026-05-07T22:00:00",
                reason="Bob asks to reschedule Thursday class",
            )
        ],
    )


def response_to_json(response: SolverResponse) -> str:
    return json.dumps(asdict(response), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    request = make_demo_request()
    result = solve_schedule(request)
    print(response_to_json(result))
