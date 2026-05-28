"""
Unit tests for trainer_solver_mvp.py.

Run:
    pip install ortools
    python -m unittest test_trainer_solver_mvp.py -v

Or with uv:
    uv add ortools
    uv run python -m unittest test_trainer_solver_mvp.py -v
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import run_solver_from_csv as csv_runner
import schedule_web_app
import trainer_solver_mvp as solver


def write_old_id_web_fixture(input_dir: Path) -> None:
    """Write a minimal old-ID fixture for migration tests."""
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "students.csv").write_text(
        "\n".join(
            [
                "student_id,name,default_venue_id,priority",
                "stu_alice,Alice,gym_a,2",
                "stu_bob,Bob,gym_b,1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (input_dir / "preferences.csv").write_text(
        "\n".join(
            [
                "student_id,day,start,end,level,score",
                "stu_alice,Mon,18:00,21:00,preferred,100",
                "stu_bob,Tue,18:00,21:00,preferred,100",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (input_dir / "lessons.csv").write_text(
        "\n".join(
            [
                "lesson_id,student_id,venue_id,duration_min,must_schedule,priority,shared_session_id",
                "lesson_alice,stu_alice,gym_a,60,TRUE,2,",
                "lesson_bob,stu_bob,gym_b,60,TRUE,1,",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (input_dir / "existing_bookings.csv").write_text(
        "\n".join(
            [
                "booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level",
                "book_alice,lesson_alice,stu_alice,gym_a,2026-05-04T18:00:00,2026-05-04T19:00:00,confirmed,1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (input_dir / "absences.csv").write_text(
        "entity_type,entity_id,start_datetime,end_datetime,reason\n",
        encoding="utf-8",
    )
    (input_dir / "venues.csv").write_text("venue_id,name\ngym_a,Left Studio\ngym_b,Right Studio\n", encoding="utf-8")
    (input_dir / "travel_times.csv").write_text(
        "from_venue_id,to_venue_id,travel_min\ngym_a,gym_a,0\ngym_b,gym_b,0\ngym_a,gym_b,15\ngym_b,gym_a,15\n",
        encoding="utf-8",
    )
    (input_dir / "coach_availability.csv").write_text(
        "day,start,end,score\nMon,18:00,21:00,0\nTue,18:00,21:00,0\n",
        encoding="utf-8",
    )


class TrainerSolverMvpTests(unittest.TestCase):
    def test_candidate_generation_has_candidates_for_demo_request(self) -> None:
        """The demo request should generate at least one candidate for every lesson."""
        request = solver.make_demo_request()
        candidates, warnings = solver.build_candidate_assignments(request)

        lesson_ids_with_candidates = {candidate.lesson_id for candidate in candidates}
        expected_lesson_ids = {lesson.lesson_id for lesson in request.lessons}

        self.assertEqual(expected_lesson_ids, lesson_ids_with_candidates)
        self.assertEqual([], warnings)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_demo_reschedules_absent_bob_and_keeps_completed_alice(self) -> None:
        """Bob is absent on Thu evening, so the solver should move Bob and keep Alice fixed."""
        request = solver.make_demo_request()
        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(4, response.summary.scheduled_lessons)
        self.assertEqual(0, response.summary.unscheduled_lessons)

        by_lesson = {item.lesson_id: item for item in response.schedule}

        alice = by_lesson["lesson_alice"]
        bob = by_lesson["lesson_bob"]

        self.assertEqual("2026-05-04T18:00", alice.start_datetime)
        self.assertFalse(alice.is_changed)

        self.assertNotEqual("2026-05-07T19:00", bob.start_datetime)
        self.assertTrue(bob.is_changed)
        self.assertTrue(
            bob.start_datetime.startswith("2026-05-08"),
            msg=f"Expected Bob to be moved to Friday in the demo data, got {bob.start_datetime}",
        )

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_required_lesson_without_preferences_returns_infeasible_input(self) -> None:
        """A required lesson with no generated candidate should return INFEASIBLE_INPUT."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)

        request.preferences[:] = [
            preference
            for preference in request.preferences
            if preference.student_id != "stu_bob"
        ]

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("lesson_bob", " ".join(response.warnings))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_locked_booking_cannot_be_moved_even_if_other_slots_exist(self) -> None:
        """A lock_level >= 3 booking should be fixed to its original time."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)

        locked_bookings = []
        for booking in request.existing_bookings:
            if booking.lesson_id == "lesson_david":
                locked_bookings.append(
                    solver.ExistingBooking(
                        booking_id=booking.booking_id,
                        lesson_id=booking.lesson_id,
                        student_id=booking.student_id,
                        venue_id=booking.venue_id,
                        start_datetime=booking.start_datetime,
                        end_datetime=booking.end_datetime,
                        status="locked",
                        lock_level=3,
                    )
                )
            else:
                locked_bookings.append(booking)

        request.existing_bookings[:] = locked_bookings

        response = solver.solve_schedule(request)
        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})

        by_lesson = {item.lesson_id: item for item in response.schedule}
        david = by_lesson["lesson_david"]

        self.assertEqual("2026-05-08T18:00", david.start_datetime)
        self.assertFalse(david.is_changed)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_no_absence_keeps_original_confirmed_schedule(self) -> None:
        """Without new absences, the solver should keep the original confirmed schedule."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.absences[:] = []

        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        by_lesson = {item.lesson_id: item for item in response.schedule}

        self.assertEqual("2026-05-07T19:00", by_lesson["lesson_bob"].start_datetime)
        self.assertEqual("2026-05-07T20:30", by_lesson["lesson_carol"].start_datetime)
        self.assertEqual("2026-05-08T18:00", by_lesson["lesson_david"].start_datetime)

        self.assertFalse(by_lesson["lesson_bob"].is_changed)
        self.assertFalse(by_lesson["lesson_carol"].is_changed)
        self.assertFalse(by_lesson["lesson_david"].is_changed)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_travel_time_conflict_forces_later_slot(self) -> None:
        """If travel time is insufficient, two lessons cannot be selected back-to-back."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T17:00:00",
                planning_end="2026-05-04T23:00:00",
                slot_size_min=30,
                max_solve_seconds=5.0,
            ),
            students=[
                solver.Student("stu_a", "Student A", "gym_a", priority=1),
                solver.Student("stu_b", "Student B", "gym_b", priority=1),
            ],
            venues=[
                solver.Venue("gym_a", "Gym A"),
                solver.Venue("gym_b", "Gym B"),
            ],
            travel_times=[
                solver.TravelTime("gym_a", "gym_a", 0),
                solver.TravelTime("gym_b", "gym_b", 0),
                solver.TravelTime("gym_a", "gym_b", 120),
                solver.TravelTime("gym_b", "gym_a", 120),
            ],
            lessons=[
                solver.LessonRequest("lesson_a", "stu_a", "gym_a", duration_min=60),
                solver.LessonRequest("lesson_b", "stu_b", "gym_b", duration_min=60),
            ],
            preferences=[
                solver.StudentPreference("stu_a", "Mon", "18:00", "19:00", "preferred", 100),
                solver.StudentPreference("stu_b", "Mon", "19:00", "20:00", "preferred", 100),
                solver.StudentPreference("stu_b", "Mon", "21:00", "22:00", "acceptable", 10),
            ],
            coach_availability=[
                solver.CoachAvailability("Mon", "17:00", "23:00"),
            ],
        )

        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        by_lesson = {item.lesson_id: item for item in response.schedule}

        self.assertEqual("2026-05-04T18:00", by_lesson["lesson_a"].start_datetime)
        self.assertEqual("2026-05-04T21:00", by_lesson["lesson_b"].start_datetime)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_shared_session_students_can_take_same_lesson(self) -> None:
        """Lessons with the same shared_session_id may use the same coach slot."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T17:00:00",
                planning_end="2026-05-04T20:00:00",
                slot_size_min=60,
                max_solve_seconds=5.0,
            ),
            students=[
                solver.Student("stu_a", "Student A", "gym_a"),
                solver.Student("stu_b", "Student B", "gym_a"),
            ],
            venues=[solver.Venue("gym_a", "Gym A")],
            travel_times=[],
            lessons=[
                solver.LessonRequest(
                    "lesson_a",
                    "stu_a",
                    "gym_a",
                    duration_min=60,
                    shared_session_id="couple_1",
                ),
                solver.LessonRequest(
                    "lesson_b",
                    "stu_b",
                    "gym_a",
                    duration_min=60,
                    shared_session_id="couple_1",
                ),
            ],
            preferences=[
                solver.StudentPreference("stu_a", "Mon", "18:00", "19:00", "preferred", 100),
                solver.StudentPreference("stu_b", "Mon", "18:00", "19:00", "preferred", 100),
            ],
            coach_availability=[solver.CoachAvailability("Mon", "18:00", "19:00")],
        )

        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(2, response.summary.scheduled_lessons)
        by_lesson = {item.lesson_id: item for item in response.schedule}
        self.assertEqual("2026-05-04T18:00", by_lesson["lesson_a"].start_datetime)
        self.assertEqual("2026-05-04T18:00", by_lesson["lesson_b"].start_datetime)
        self.assertEqual("2026-05-04T19:00", by_lesson["lesson_a"].end_datetime)
        self.assertEqual("2026-05-04T19:00", by_lesson["lesson_b"].end_datetime)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_required_shared_session_without_common_candidate_reports_clear_warning(self) -> None:
        """Required shared lessons need at least one common time and venue candidate."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.lessons[0] = solver.LessonRequest(
            "lesson_alice",
            "stu_alice",
            "gym_a",
            duration_min=60,
            priority=2,
            shared_session_id="couple_1",
        )
        request.lessons[1] = solver.LessonRequest(
            "lesson_bob",
            "stu_bob",
            "gym_b",
            duration_min=60,
            priority=1,
            shared_session_id="couple_1",
        )

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("shared_session_id=couple_1", " ".join(response.warnings))
        self.assertIn("no common time and venue", " ".join(response.warnings))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_in_week_freeze_buffer_keeps_near_future_booking_fixed(self) -> None:
        """A confirmed lesson before freeze_before should be fixed even if it has alternatives."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.absences[:] = []

        updated_bookings = []
        for booking in request.existing_bookings:
            if booking.lesson_id == "lesson_bob":
                updated_bookings.append(
                    solver.ExistingBooking(
                        booking_id=booking.booking_id,
                        lesson_id=booking.lesson_id,
                        student_id=booking.student_id,
                        venue_id=booking.venue_id,
                        start_datetime="2026-05-06T17:00:00",
                        end_datetime="2026-05-06T18:00:00",
                        status="confirmed",
                        lock_level=1,
                    )
                )
            else:
                updated_bookings.append(booking)

        request.existing_bookings[:] = updated_bookings
        request.preferences.append(
            solver.StudentPreference(
                "stu_bob",
                "Wed",
                "17:00",
                "18:00",
                "preferred",
                100,
            )
        )

        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        by_lesson = {item.lesson_id: item for item in response.schedule}
        bob = by_lesson["lesson_bob"]

        self.assertEqual("2026-05-06T17:00", bob.start_datetime)
        self.assertFalse(bob.is_changed)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_overlapping_fixed_bookings_return_infeasible_input(self) -> None:
        """Fixed bookings should be rejected before solve if they overlap."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.absences[:] = []
        request.existing_bookings[:] = [
            solver.ExistingBooking(
                "book_alice",
                "lesson_alice",
                "stu_alice",
                "gym_a",
                "2026-05-08T18:00:00",
                "2026-05-08T19:00:00",
                status="locked",
                lock_level=3,
            ),
            solver.ExistingBooking(
                "book_david",
                "lesson_david",
                "stu_david",
                "gym_a",
                "2026-05-08T18:30:00",
                "2026-05-08T19:30:00",
                status="locked",
                lock_level=3,
            ),
        ]

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("overlap", " ".join(response.warnings))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_shared_fixed_bookings_must_have_same_time_and_venue(self) -> None:
        """Fixed shared-session bookings should be rejected when they do not match exactly."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.absences[:] = []
        request.lessons[1] = solver.LessonRequest(
            "lesson_bob",
            "stu_bob",
            "gym_b",
            duration_min=60,
            shared_session_id="couple_1",
        )
        request.lessons[2] = solver.LessonRequest(
            "lesson_carol",
            "stu_carol",
            "gym_b",
            duration_min=60,
            shared_session_id="couple_1",
        )
        request.existing_bookings[:] = [
            solver.ExistingBooking(
                "book_bob",
                "lesson_bob",
                "stu_bob",
                "gym_b",
                "2026-05-07T19:00:00",
                "2026-05-07T20:00:00",
                status="locked",
                lock_level=3,
            ),
            solver.ExistingBooking(
                "book_carol",
                "lesson_carol",
                "stu_carol",
                "gym_b",
                "2026-05-07T20:30:00",
                "2026-05-07T21:30:00",
                status="locked",
                lock_level=3,
            ),
        ]

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("share session", " ".join(response.warnings))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_fixed_booking_with_absence_returns_infeasible_input(self) -> None:
        """A fixed booking that conflicts with absence data should be rejected."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.existing_bookings[:] = [
            solver.ExistingBooking(
                "book_bob",
                "lesson_bob",
                "stu_bob",
                "gym_b",
                "2026-05-07T19:00:00",
                "2026-05-07T20:00:00",
                status="locked",
                lock_level=3,
            )
        ]

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("absence", " ".join(response.warnings))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_optional_lesson_without_candidates_does_not_make_solve_infeasible(self) -> None:
        """Optional lessons with no candidate should be reported unscheduled, not infeasible."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.lessons.append(
            solver.LessonRequest(
                "lesson_optional",
                "stu_bob",
                "gym_b",
                duration_min=60,
                must_schedule=False,
            )
        )

        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        by_change = {item.lesson_id: item for item in response.changes}
        self.assertEqual("unscheduled", by_change["lesson_optional"].change_type)
        self.assertEqual(1, response.summary.unscheduled_lessons)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_optional_lesson_can_be_left_unscheduled_when_penalty_is_low(self) -> None:
        """Optional feasible candidates should only be selected when the objective justifies it."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T17:00:00",
                planning_end="2026-05-04T20:00:00",
                slot_size_min=60,
                max_solve_seconds=5.0,
                weights=solver.ObjectiveWeights(cancel_optional_penalty=10),
            ),
            students=[solver.Student("stu_a", "Student A", "gym_a")],
            venues=[solver.Venue("gym_a", "Gym A")],
            travel_times=[],
            lessons=[
                solver.LessonRequest(
                    "lesson_optional",
                    "stu_a",
                    "gym_a",
                    duration_min=60,
                    must_schedule=False,
                    priority=0,
                )
            ],
            preferences=[
                solver.StudentPreference("stu_a", "Mon", "18:00", "19:00", "acceptable", 0)
            ],
            coach_availability=[
                solver.CoachAvailability("Mon", "18:00", "19:00", score=-100)
            ],
        )

        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual([], response.schedule)
        self.assertEqual(1, response.summary.unscheduled_lessons)
        self.assertEqual("unscheduled", response.changes[0].change_type)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_missing_references_are_caught_before_solve(self) -> None:
        """Invalid request references should return INFEASIBLE_INPUT instead of KeyError."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.lessons[0] = solver.LessonRequest("lesson_alice", "missing_student", "gym_a")

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("missing student_id", " ".join(response.warnings))

    def test_unknown_travel_path_uses_large_fallback_minutes(self) -> None:
        """Missing travel paths use an explicit large fallback rather than zero travel."""
        self.assertEqual(10_000, solver.get_travel_min({}, "gym_a", "gym_b"))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_duplicate_ids_are_rejected(self) -> None:
        """Duplicate core IDs should be rejected at validation time."""
        request = solver.make_demo_request()
        request = copy.deepcopy(request)
        request.students.append(
            solver.Student("stu_alice", "Duplicate Alice", "gym_a")
        )

        response = solver.solve_schedule(request)

        self.assertEqual("INFEASIBLE_INPUT", response.status)
        self.assertIn("Duplicate student_id", " ".join(response.warnings))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_demo_reports_candidate_count(self) -> None:
        """The solver summary should expose candidate count for scale monitoring."""
        request = solver.make_demo_request()
        response = solver.solve_schedule(request)

        self.assertIn(response.status, {"OPTIMAL", "FEASIBLE"})
        self.assertGreater(response.summary.candidate_count, 0)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_format_solution_table_includes_schedule_changes_and_warnings(self) -> None:
        """The terminal solution view should include the key review sections."""
        request = solver.make_demo_request()
        response = solver.solve_schedule(request)

        text = csv_runner.format_solution_table(response)

        self.assertIn("Schedule", text)
        self.assertIn("Changes", text)
        self.assertIn("lesson_bob", text)
        self.assertIn("moved", text)

        warning_response = solver.SolverResponse(
            status=response.status,
            summary=response.summary,
            schedule=response.schedule,
            changes=response.changes,
            warnings=["example warning"],
        )
        warning_text = csv_runner.format_solution_table(warning_response)
        self.assertIn("Warnings", warning_text)
        self.assertIn("example warning", warning_text)

    def test_schedule_to_ics_exports_single_lesson_event(self) -> None:
        """A regular scheduled lesson should export as one calendar event."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T10:00:00",
                planning_end="2026-05-04T20:00:00",
            ),
            students=[solver.Student("stu_a", "Anna", "gym_a")],
            venues=[solver.Venue("gym_a", "Gym A")],
            travel_times=[],
            lessons=[solver.LessonRequest("lesson_a", "stu_a", "gym_a")],
            preferences=[],
            coach_availability=[solver.CoachAvailability("Mon", "10:00", "20:00")],
        )
        response = solver.SolverResponse(
            status="OPTIMAL",
            summary=solver.SolverSummary(1, 0, 0, 0, 0, 10, 1),
            schedule=[
                solver.ScheduledLesson(
                    "lesson_a",
                    "stu_a",
                    "Anna",
                    "gym_a",
                    "Gym A",
                    "2026-05-04T10:00",
                    "2026-05-04T11:00",
                    "preferred",
                    100,
                    False,
                )
            ],
            changes=[],
            warnings=[],
        )

        text = csv_runner.schedule_to_ics(response, request)

        self.assertIn("BEGIN:VCALENDAR", text)
        self.assertEqual(2, text.count("BEGIN:VEVENT"))
        self.assertIn("SUMMARY:Anna - Gym A", text)
        self.assertIn("DTSTART:20260504T100000", text)
        self.assertIn("LOCATION:Gym A", text)

    def test_schedule_to_ics_groups_shared_lessons(self) -> None:
        """Shared-session rows at the same time and venue should export as one event."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T10:00:00",
                planning_end="2026-05-04T20:00:00",
            ),
            students=[
                solver.Student("stu_a", "Anna", "gym_a"),
                solver.Student("stu_b", "Brian", "gym_a"),
            ],
            venues=[solver.Venue("gym_a", "Gym A")],
            travel_times=[],
            lessons=[
                solver.LessonRequest("lesson_a", "stu_a", "gym_a", shared_session_id="couple_1"),
                solver.LessonRequest("lesson_b", "stu_b", "gym_a", shared_session_id="couple_1"),
            ],
            preferences=[],
            coach_availability=[solver.CoachAvailability("Mon", "10:00", "20:00")],
        )
        response = solver.SolverResponse(
            status="OPTIMAL",
            summary=solver.SolverSummary(2, 0, 0, 0, 0, 20, 2),
            schedule=[
                solver.ScheduledLesson(
                    "lesson_a",
                    "stu_a",
                    "Anna",
                    "gym_a",
                    "Gym A",
                    "2026-05-04T10:00",
                    "2026-05-04T11:30",
                    "preferred",
                    100,
                    False,
                ),
                solver.ScheduledLesson(
                    "lesson_b",
                    "stu_b",
                    "Brian",
                    "gym_a",
                    "Gym A",
                    "2026-05-04T10:00",
                    "2026-05-04T11:30",
                    "preferred",
                    100,
                    False,
                ),
            ],
            changes=[],
            warnings=[],
        )

        text = csv_runner.schedule_to_ics(response, request)

        self.assertEqual(2, text.count("BEGIN:VEVENT"))
        self.assertIn("SUMMARY:Anna / Brian - Gym A", text)
        self.assertIn("Lesson IDs: lesson_a\\, lesson_b", text)

    def test_schedule_to_ics_exports_availability_windows(self) -> None:
        """Trainer availability should appear as transparent events across the horizon."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T10:00:00",
                planning_end="2026-05-06T20:00:00",
            ),
            students=[],
            venues=[],
            travel_times=[],
            lessons=[],
            preferences=[],
            coach_availability=[
                solver.CoachAvailability("Mon", "10:00", "20:00"),
                solver.CoachAvailability("Tue", "10:00", "20:00"),
                solver.CoachAvailability("Wed", "10:00", "20:00"),
            ],
        )
        response = solver.SolverResponse(
            status="OPTIMAL",
            summary=solver.SolverSummary(0, 0, 0, 0, 0, 0, 0),
            schedule=[],
            changes=[],
            warnings=[],
        )

        text = csv_runner.schedule_to_ics(response, request)

        self.assertEqual(3, text.count("BEGIN:VEVENT"))
        self.assertEqual(3, text.count("SUMMARY:Available - 10:00-20:00"))
        self.assertEqual(3, text.count("TRANSP:TRANSPARENT"))

    def test_schedule_to_ics_flags_lesson_outside_availability(self) -> None:
        """Out-of-window lessons should be visibly flagged in the event title."""
        request = solver.SolverRequest(
            config=solver.SolverConfig(
                mode=solver.SolverMode.WEEKLY_PLANNING,
                planning_start="2026-05-04T10:00:00",
                planning_end="2026-05-04T20:00:00",
            ),
            students=[solver.Student("stu_a", "Anna", "gym_a")],
            venues=[solver.Venue("gym_a", "Gym A")],
            travel_times=[],
            lessons=[solver.LessonRequest("lesson_a", "stu_a", "gym_a")],
            preferences=[],
            coach_availability=[solver.CoachAvailability("Mon", "10:00", "12:00")],
        )
        response = solver.SolverResponse(
            status="OPTIMAL",
            summary=solver.SolverSummary(1, 0, 0, 0, 0, 10, 1),
            schedule=[
                solver.ScheduledLesson(
                    "lesson_a",
                    "stu_a",
                    "Anna",
                    "gym_a",
                    "Gym A",
                    "2026-05-04T13:00",
                    "2026-05-04T14:00",
                    "preferred",
                    100,
                    False,
                )
            ],
            changes=[],
            warnings=[],
        )

        text = csv_runner.schedule_to_ics(response, request)
        unfolded = text.replace("\r\n ", "")

        self.assertIn("SUMMARY:[OUTSIDE AVAILABILITY] Anna - Gym A", text)
        self.assertIn("Warning: this lesson is outside trainer availability.", unfolded)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_print_solution_cli_prints_table_and_writes_json(self) -> None:
        """--print-solution should print review rows while preserving JSON output."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            stdout = io.StringIO()
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1002-1",
                        "student_id": "1002",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1003-1",
                        "student_id": "1003",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1004-1",
                        "student_id": "1004",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                ],
            )

            with contextlib.redirect_stdout(stdout):
                csv_runner.main(
                    [
                        "--input",
                        str(input_dir),
                        "--output",
                        str(output_path),
                        "--print-solution",
                    ]
                )

            printed = stdout.getvalue()
            self.assertIn("Schedule", printed)
            self.assertIn("1002-1", printed)
            self.assertTrue(output_path.exists())

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn(payload["status"], {"OPTIMAL", "FEASIBLE"})
            self.assertIn("schedule", payload)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_output_ics_cli_writes_calendar_file(self) -> None:
        """--output-ics should write a calendar while preserving JSON output."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "solution.json"
            ics_path = Path(temp_dir) / "schedule.ics"

            csv_runner.main(
                [
                    "--input",
                    "csv_demo_input",
                    "--output",
                    str(output_path),
                    "--output-ics",
                    str(ics_path),
                ]
            )

            self.assertTrue(output_path.exists())
            self.assertTrue(ics_path.exists())
            text = ics_path.read_text(encoding="utf-8")
            self.assertIn("BEGIN:VCALENDAR", text)
            self.assertIn("SUMMARY:", text)
            self.assertNotIn("\r\r\n", text)

    def test_init_template_creates_expected_csv_files_and_headers(self) -> None:
        """The CSV template helper should create the complete folder contract."""
        with tempfile.TemporaryDirectory() as temp_dir:
            template_dir = Path(temp_dir) / "input_template"
            written = csv_runner.create_csv_template(template_dir)

            self.assertEqual(
                set(csv_runner.REQUIRED_FILES + csv_runner.OPTIONAL_FILES),
                {path.name for path in written},
            )
            self.assertEqual(
                "student_id,name,default_venue_id,priority,lessons_per_week,couple",
                (template_dir / "students.csv").read_text(encoding="utf-8").splitlines()[0],
            )
            self.assertEqual(
                "lesson_id,student_id,venue_id,duration_min,must_schedule,priority,shared_session_id",
                (template_dir / "lessons.csv").read_text(encoding="utf-8").splitlines()[0],
            )
            self.assertEqual(
                "key,value",
                (template_dir / "config.csv").read_text(encoding="utf-8").splitlines()[0],
            )
            self.assertIn(
                "preference_score_last_resort,20",
                (template_dir / "config.csv").read_text(encoding="utf-8"),
            )

    def test_init_template_refuses_to_overwrite_without_force(self) -> None:
        """Template creation should avoid clobbering user-edited CSV files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            template_dir = Path(temp_dir) / "input_template"
            csv_runner.create_csv_template(template_dir)

            with self.assertRaises(csv_runner.CsvInputError):
                csv_runner.create_csv_template(template_dir)

            written = csv_runner.create_csv_template(template_dir, force=True)
            self.assertTrue(written)

    def test_validate_only_succeeds_for_demo_input(self) -> None:
        """--validate-only should load and validate input without solving."""
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            csv_runner.main(["--input", "csv_demo_input", "--validate-only"])

        self.assertIn("validation=ok", stdout.getvalue())

    def test_validate_only_reports_validation_failures(self) -> None:
        """--validate-only should report solver-side validation errors."""
        with tempfile.TemporaryDirectory() as temp_dir:
            template_dir = Path(temp_dir) / "bad_input"
            csv_runner.create_csv_template(template_dir)
            csv_runner.write_csv(
                template_dir / "lessons.csv",
                [
                    {
                        "lesson_id": "lesson_bad",
                        "student_id": "missing_student",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    }
                ],
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit):
                    csv_runner.main(["--input", str(template_dir), "--validate-only"])

            printed = stdout.getvalue()
            self.assertIn("validation=failed", printed)
            self.assertIn("missing student_id", printed)

    def test_web_timeslot_parser_accepts_compact_weekly_string(self) -> None:
        """The web input format should map cleanly to preferences.csv rows."""
        preferences = schedule_web_app.parse_timeslot_string(
            "stu_alice",
            "Mon 18:00-21:00 preferred 100; Wed 19:00-21:00 acceptable",
        )

        self.assertEqual(2, len(preferences))
        self.assertEqual("stu_alice", preferences[0].student_id)
        self.assertEqual("Mon", preferences[0].day)
        self.assertEqual("18:00", preferences[0].start)
        self.assertEqual("21:00", preferences[0].end)
        self.assertEqual("preferred", preferences[0].level)
        self.assertEqual(100, preferences[0].score)
        self.assertEqual("acceptable", preferences[1].level)
        self.assertEqual(60, preferences[1].score)

    def test_web_timeslot_parser_expands_weekday_range_and_compact_time(self) -> None:
        """Weekday ranges should expand into normalized preference rows."""
        preferences = schedule_web_app.parse_timeslot_string(
            "stu_alice",
            "Mon-Fri 0900-1200 preferred",
            {"preferred": 80, "acceptable": 40},
        )

        self.assertEqual(["Mon", "Tue", "Wed", "Thu", "Fri"], [item.day for item in preferences])
        self.assertTrue(all(item.start == "09:00" and item.end == "12:00" for item in preferences))
        self.assertTrue(all(item.level == "preferred" and item.score == 80 for item in preferences))

    def test_web_timeslot_parser_uses_trainer_timeslots_for_weekday_only(self) -> None:
        """Weekday-only shortcuts should use trainer availability windows."""
        preferences = schedule_web_app.parse_timeslot_string(
            "stu_alice",
            "Mon-Wed",
            {"preferred": 90},
            [
                solver.CoachAvailability("Mon", "10:00", "12:00", 0),
                solver.CoachAvailability("Wed", "13:00", "15:00", 0),
            ],
        )

        self.assertEqual(
            [("Mon", "10:00", "12:00", "preferred", 90), ("Wed", "13:00", "15:00", "preferred", 90)],
            [(item.day, item.start, item.end, item.level, item.score) for item in preferences],
        )

    def test_web_timeslot_parser_uses_trainer_timeslots_for_blank_preferences(self) -> None:
        """Blank preference text should default to all trainer availability as preferred."""
        preferences = schedule_web_app.parse_timeslot_string(
            "stu_alice",
            "",
            {"preferred": 95},
            [
                solver.CoachAvailability("Mon", "10:00", "12:00", 0),
                solver.CoachAvailability("Tue", "13:00", "15:00", 0),
            ],
        )

        self.assertEqual(
            [("Mon", "10:00", "12:00", "preferred", 95), ("Tue", "13:00", "15:00", "preferred", 95)],
            [(item.day, item.start, item.end, item.level, item.score) for item in preferences],
        )

    def test_web_timeslot_parser_rejects_blank_preferences_without_trainer_timeslots(self) -> None:
        """Blank preference defaults need trainer availability to expand from."""
        with self.assertRaises(ValueError) as raised:
            schedule_web_app.parse_timeslot_string("stu_alice", "", {"preferred": 95}, [])

        self.assertIn("Blank preferences require", str(raised.exception))

    def test_web_timeslot_parser_rejects_unknown_level_without_score(self) -> None:
        """Unknown preference levels need Setup configuration or an explicit score."""
        with self.assertRaises(ValueError) as raised:
            schedule_web_app.parse_timeslot_string("stu_alice", "Mon 09:00-12:00 ideal")

        self.assertIn("unknown level", str(raised.exception))

    def test_web_timeslot_parser_accepts_explicit_score_for_unknown_level(self) -> None:
        """Legacy explicit score syntax remains compatible for custom levels."""
        preferences = schedule_web_app.parse_timeslot_string("stu_alice", "Mon 9:00-12:00 ideal 75")

        self.assertEqual("09:00", preferences[0].start)
        self.assertEqual("ideal", preferences[0].level)
        self.assertEqual(75, preferences[0].score)

    def test_web_timeslot_parser_rejects_invalid_day_range(self) -> None:
        """Day ranges should run forward within the week."""
        with self.assertRaises(ValueError) as raised:
            schedule_web_app.parse_timeslot_string("stu_alice", "Fri-Mon 0900-1200 preferred")

        self.assertIn("day range", str(raised.exception))

    def test_web_timeslot_formatter_round_trips_compact_string(self) -> None:
        """Existing preferences should render into the editable compact string."""
        text = schedule_web_app.format_timeslot_string(
            [
                solver.StudentPreference("stu_alice", "Mon", "18:00", "21:00", "preferred", 100),
                solver.StudentPreference("stu_alice", "Wed", "19:00", "21:00", "acceptable", 60),
            ]
        )

        self.assertEqual(
            "Mon 18:00-21:00 preferred 100; Wed 19:00-21:00 acceptable 60",
            text,
        )

    def test_web_save_student_preferences_writes_csv_rows(self) -> None:
        """Saving the web table should rewrite students.csv and preferences.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_student_preferences(
                input_dir,
                [
                    {
                        "student_id": "stu_alice",
                        "student_name": "Alice Updated",
                        "lessons_per_week": "2",
                        "couple": "pair_a",
                        "available_timeslots": "Tue 10:00-12:00 preferred 100",
                        "venues": "default=gym_a",
                    }
                ],
            )

            self.assertTrue(result["ok"])
            self.assertIn("Alice Updated", (input_dir / "students.csv").read_text(encoding="utf-8"))
            students_text = (input_dir / "students.csv").read_text(encoding="utf-8")
            self.assertIn("lessons_per_week", students_text.splitlines()[0])
            self.assertIn("couple", students_text.splitlines()[0])
            self.assertIn(",2,pair_a", students_text)
            self.assertIn("Tue,10:00,12:00,preferred,100", (input_dir / "preferences.csv").read_text(encoding="utf-8"))

    def test_web_student_rows_load_couple_column(self) -> None:
        """The Students page should expose the optional students.csv couple column."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            (input_dir / "students.csv").write_text(
                "student_id,name,default_venue_id,priority,lessons_per_week,couple\n"
                "1001,Alice,gym_a,1,1,pair_a\n",
                encoding="utf-8",
            )

            rows = schedule_web_app.build_table_rows(input_dir)

            self.assertEqual("pair_a", rows[0]["couple"])

    def test_web_preference_score_defaults_are_available(self) -> None:
        """Missing config score keys should fall back to current level-score pairs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            scores = schedule_web_app.load_preference_score_map(input_dir)

            self.assertEqual(100, scores["preferred"])
            self.assertEqual(60, scores["acceptable"])
            self.assertEqual(20, scores["last_resort"])

    def test_web_save_preference_scores_updates_config_only(self) -> None:
        """Preference score settings should be stored as config.csv key/value rows."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            students_before = (input_dir / "students.csv").read_text(encoding="utf-8")

            result = schedule_web_app.save_preference_scores(
                input_dir,
                [{"level": "preferred", "score": "120"}, {"level": "acceptable", "score": "50"}],
            )

            self.assertTrue(result["ok"])
            config_text = (input_dir / "config.csv").read_text(encoding="utf-8")
            self.assertIn("preference_score_preferred,120", config_text)
            self.assertIn("preference_score_acceptable,50", config_text)
            self.assertEqual(students_before, (input_dir / "students.csv").read_text(encoding="utf-8"))

    def test_web_build_api_data_includes_config_parameters(self) -> None:
        """Setup should expose editable core config rows and default values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            payload = schedule_web_app.build_api_data(input_dir, Path(temp_dir) / "missing.json")

            config_keys = {row["key"] for row in payload["config_rows"]}
            default_keys = {row["key"] for row in payload["default_config_rows"]}
            self.assertIn("max_solve_seconds", config_keys)
            self.assertIn("mode", config_keys)
            self.assertEqual(config_keys, default_keys)

    def test_web_save_config_parameters_preserves_preference_scores(self) -> None:
        """Saving core Setup config should not remove preference score settings."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_config_parameters(
                input_dir,
                [
                    {"key": "mode", "value": "weekly_planning"},
                    {"key": "planning_start", "value": "2026-05-04T09:00:00"},
                    {"key": "planning_end", "value": "2026-05-10T22:00:00"},
                    {"key": "slot_size_min", "value": "30"},
                    {"key": "max_solve_seconds", "value": "15.0"},
                    {"key": "freeze_now", "value": "2026-05-06T14:00:00"},
                    {"key": "freeze_buffer_hours", "value": "4"},
                ],
            )

            config_text = (input_dir / "config.csv").read_text(encoding="utf-8")
            self.assertTrue(result["ok"])
            self.assertIn("mode,weekly_planning", config_text)
            self.assertIn("max_solve_seconds,15.0", config_text)
            self.assertIn("preference_score_preferred,100", config_text)

    def test_web_save_config_parameters_rejects_invalid_values(self) -> None:
        """Invalid solver config values should fail before writing config.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            with self.assertRaises(schedule_web_app.WebInputError):
                schedule_web_app.save_config_parameters(
                    input_dir,
                    [
                        {"key": "mode", "value": "weekly_planning"},
                        {"key": "planning_start", "value": "2026-05-04T09:00:00"},
                        {"key": "planning_end", "value": "2026-05-10T22:00:00"},
                        {"key": "slot_size_min", "value": "0"},
                        {"key": "max_solve_seconds", "value": "15.0"},
                        {"key": "freeze_now", "value": "2026-05-06T14:00:00"},
                        {"key": "freeze_buffer_hours", "value": "4"},
                    ],
                )

    def test_web_clear_solution_removes_stale_optimizer_output(self) -> None:
        """CSV-changing web saves should be able to invalidate old solution JSON."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "solution.json"
            output_path.write_text('{"status":"FEASIBLE","schedule":[]}', encoding="utf-8")

            self.assertTrue(schedule_web_app.clear_solution(output_path))
            self.assertFalse(output_path.exists())
            self.assertFalse(schedule_web_app.clear_solution(output_path))

    def test_web_runtime_config_overrides_request_without_writing_csv(self) -> None:
        """Organizer runtime config values should affect only the in-memory solve request."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            csv_runner.create_csv_template(input_dir)
            before = (input_dir / "config.csv").read_text(encoding="utf-8")
            request = csv_runner.load_solver_request(input_dir)

            updated = schedule_web_app.apply_runtime_config_overrides(
                request,
                {
                    "mode": "weekly_planning",
                    "planning_start": "2026-05-11T09:00:00",
                    "planning_end": "2026-05-17T22:00:00",
                    "freeze_now": "2026-05-12T10:00:00",
                },
            )

            self.assertEqual(solver.SolverMode.WEEKLY_PLANNING, updated.config.mode)
            self.assertEqual("2026-05-11T09:00:00", updated.config.planning_start)
            self.assertEqual("2026-05-12T10:00:00", updated.config.freeze_policy.now)
            self.assertEqual(before, (input_dir / "config.csv").read_text(encoding="utf-8"))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_run_solver_records_solve_wall_time(self) -> None:
        """Web optimizer output should include elapsed solve time in the summary."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            csv_runner.create_csv_template(input_dir)

            result = schedule_web_app.run_solver(input_dir, output_path)

            self.assertIn("solve_wall_time_seconds", result["solution"]["summary"])
            self.assertIsInstance(result["solution"]["summary"]["solve_wall_time_seconds"], float)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("solve_wall_time_seconds", written["summary"])

    def test_web_save_student_preferences_uses_configured_scores(self) -> None:
        """Saving Students should convert levels to configured numeric scores."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_preference_scores(
                input_dir,
                [{"level": "preferred", "score": "120"}, {"level": "acceptable", "score": "55"}],
            )

            schedule_web_app.save_student_preferences(
                input_dir,
                [
                    {
                        "student_id": "1001",
                        "student_name": "Alice",
                        "lessons_per_week": "1",
                        "available_timeslots": "Mon-Fri 0900-1200 preferred; Fri acceptable",
                        "venues": "default=gym_a",
                    }
                ],
            )

            preferences_text = (input_dir / "preferences.csv").read_text(encoding="utf-8")
            self.assertIn("1001,Mon,09:00,12:00,preferred,120", preferences_text)
            self.assertIn("1001,Fri,10:00,22:00,acceptable,55", preferences_text)

    def test_web_save_student_preferences_expands_blank_to_trainer_timeslots(self) -> None:
        """Blank student preference text should use trainer availability defaults."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_preference_scores(
                input_dir,
                [{"level": "preferred", "score": "130"}, {"level": "acceptable", "score": "55"}],
            )

            schedule_web_app.save_student_preferences(
                input_dir,
                [
                    {
                        "student_id": "1001",
                        "student_name": "Alice",
                        "lessons_per_week": "1",
                        "available_timeslots": "",
                        "venues": "default=gym_a",
                    }
                ],
            )

            preferences_text = (input_dir / "preferences.csv").read_text(encoding="utf-8")
            self.assertIn("1001,Mon,10:00,22:00,preferred,130", preferences_text)
            self.assertIn("1001,Fri,10:00,22:00,preferred,130", preferences_text)

    def test_web_save_student_preferences_rejects_blank_without_trainer_timeslots(self) -> None:
        """Blank student preferences should fail clearly when no trainer slots exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            (input_dir / "coach_availability.csv").write_text("day,start,end,score\n", encoding="utf-8")

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_student_preferences(
                    input_dir,
                    [
                        {
                            "student_id": "1001",
                            "student_name": "Alice",
                            "lessons_per_week": "1",
                            "available_timeslots": "",
                            "venues": "default=gym_a",
                        }
                    ],
                )

            self.assertIn("Blank preferences require", raised.exception.errors[0]["message"])

    def test_web_student_rows_default_lessons_per_week(self) -> None:
        """Old students.csv files without lessons_per_week should default to one."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            (input_dir / "students.csv").write_text(
                "student_id,name,default_venue_id,priority\nstu_a,Alice,gym_a,1\n",
                encoding="utf-8",
            )

            rows = schedule_web_app.build_table_rows(input_dir)

            self.assertEqual("1", rows[0]["lessons_per_week"])

    def test_web_save_student_preferences_rejects_invalid_timeslot_without_writing(self) -> None:
        """Invalid web input should not overwrite existing CSV files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            original = (input_dir / "preferences.csv").read_text(encoding="utf-8")

            with self.assertRaises(schedule_web_app.WebInputError):
                schedule_web_app.save_student_preferences(
                    input_dir,
                    [
                        {
                            "student_id": "stu_alice",
                            "student_name": "Alice",
                            "available_timeslots": "Monday evening",
                            "venues": "default=gym_a",
                        }
                    ],
                )

            self.assertEqual(original, (input_dir / "preferences.csv").read_text(encoding="utf-8"))

    def test_web_save_student_preferences_rejects_unknown_default_venue(self) -> None:
        """The v1 venue string should validate against venues.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_student_preferences(
                    input_dir,
                    [
                        {
                            "student_id": "stu_alice",
                            "student_name": "Alice",
                            "available_timeslots": "Mon 18:00-21:00 preferred 100",
                            "venues": "default=missing_gym",
                        }
                    ],
                )

            self.assertIn("Unknown default venue_id", raised.exception.errors[0]["message"])

    def test_web_build_api_data_includes_lesson_rows_with_shared_session(self) -> None:
        """The web payload should expose lessons for the Schedule Input lesson editor."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_test_input_shared_lessons", input_dir)

            payload = schedule_web_app.build_api_data(input_dir, Path(temp_dir) / "missing.json")

            self.assertIn("lesson_rows", payload)
            shared_values = {row["shared_session_id"] for row in payload["lesson_rows"]}
            self.assertIn("couple_anna_brian", shared_values)

    def test_web_build_api_data_merges_lesson_booking_rows(self) -> None:
        """Lesson rows should include editable booking time and status fields."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            (input_dir / "existing_bookings.csv").write_text(
                "\n".join(
                    [
                        "booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level",
                        "book_1001-1,1001-1,1001,gym_a,2026-05-04T18:00:00,2026-05-04T19:00:00,completed,3",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            payload = schedule_web_app.build_api_data(input_dir, Path(temp_dir) / "missing.json")

            row_by_id = {row["lesson_id"]: row for row in payload["lesson_rows"]}
            alice = row_by_id["1001-1"]
            self.assertEqual("Mon", alice["booking_day"])
            self.assertEqual("18:00", alice["booking_start"])
            self.assertEqual("19:00", alice["booking_end"])
            self.assertEqual("completed", alice["booking_status"])
            self.assertEqual("TRUE", alice["booking_readonly"])

    def test_web_save_lessons_writes_csv_rows(self) -> None:
        """The lesson editor should rewrite lessons.csv with shared session data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "lesson_pair_a",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "pair_a",
                    }
                ],
            )

            self.assertTrue(result["ok"])
            self.assertIn(
                "lesson_pair_a,1001,gym_a,60,TRUE,2,pair_a",
                (input_dir / "lessons.csv").read_text(encoding="utf-8"),
            )

    def test_web_save_lessons_writes_booking_rows(self) -> None:
        """Saving lesson times should rewrite existing_bookings.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "lesson_pair_a",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "pair_a",
                        "booking_day": "Fri",
                        "booking_start": "19:00",
                        "booking_status": "draft",
                    }
                ],
            )

            self.assertTrue(result["ok"])
            self.assertEqual(1, result["booking_rows"])
            text = (input_dir / "existing_bookings.csv").read_text(encoding="utf-8")
            self.assertIn("book_lesson_pair_a,lesson_pair_a,1001,gym_a,2026-05-08T19:00:00,2026-05-08T20:00:00,draft,1", text)

    def test_web_save_lessons_clears_booking_when_time_is_blank(self) -> None:
        """Blank booking controls should remove the lesson's booking row."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "",
                        "booking_day": "",
                        "booking_start": "",
                        "booking_status": "",
                    }
                ],
            )

            self.assertTrue(result["ok"])
            self.assertEqual(0, result["booking_rows"])
            self.assertEqual(
                "booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level\n",
                (input_dir / "existing_bookings.csv").read_text(encoding="utf-8"),
            )

    def test_web_save_lessons_rejects_bad_rows_without_writing(self) -> None:
        """Invalid lesson editor rows should not overwrite lessons.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            original = (input_dir / "lessons.csv").read_text(encoding="utf-8")

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_lessons(
                    input_dir,
                    [
                        {
                            "lesson_id": "lesson_bad",
                            "student_id": "missing_student",
                            "venue_id": "missing_venue",
                            "duration_min": "0",
                            "must_schedule": "maybe",
                            "priority": "-1",
                            "shared_session_id": "",
                        }
                    ],
                )

            messages = " ".join(error["message"] for error in raised.exception.errors)
            self.assertIn("Unknown student_id", messages)
            self.assertIn("Unknown venue_id", messages)
            self.assertEqual(original, (input_dir / "lessons.csv").read_text(encoding="utf-8"))

    def test_web_save_lessons_rejects_incomplete_booking(self) -> None:
        """Booking day, start, and status must be saved together."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            (input_dir / "existing_bookings.csv").write_text(
                "\n".join(
                    [
                        "booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level",
                        "book_1001-1,1001-1,1001,gym_a,2026-05-04T18:00:00,2026-05-04T19:00:00,completed,3",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_lessons(
                    input_dir,
                    [
                        {
                            "lesson_id": "1001-1",
                            "student_id": "1001",
                            "venue_id": "gym_a",
                            "duration_min": "60",
                            "must_schedule": "TRUE",
                            "priority": "2",
                            "shared_session_id": "",
                            "booking_day": "Fri",
                            "booking_start": "",
                            "booking_status": "draft",
                        }
                    ],
                )

            self.assertIn("day, start, and status are required together", raised.exception.errors[0]["message"])

    def test_web_save_lessons_rejects_fixed_time_edit_without_status_change(self) -> None:
        """Completed or locked booking times cannot move unless status changes first."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)
            (input_dir / "existing_bookings.csv").write_text(
                "\n".join(
                    [
                        "booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level",
                        "book_1001-1,1001-1,1001,gym_a,2026-05-04T18:00:00,2026-05-04T19:00:00,completed,3",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_lessons(
                    input_dir,
                    [
                        {
                            "lesson_id": "1001-1",
                            "student_id": "1001",
                            "venue_id": "gym_a",
                            "duration_min": "60",
                            "must_schedule": "TRUE",
                            "priority": "2",
                            "shared_session_id": "",
                            "booking_day": "Fri",
                            "booking_start": "19:00",
                            "booking_status": "completed",
                        }
                    ],
                )

            self.assertIn("change fixed booking status", raised.exception.errors[0]["message"])

    def test_web_numeric_id_migration_rewrites_references_and_creates_backup(self) -> None:
        """Startup ID migration should rewrite old IDs across CSV references."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            write_old_id_web_fixture(input_dir)
            output_path.write_text(
                json.dumps(
                    {
                        "schedule": [{"lesson_id": "lesson_alice", "student_id": "stu_alice"}],
                        "changes": [{"lesson_id": "lesson_bob", "student_id": "stu_bob"}],
                    }
                ),
                encoding="utf-8",
            )

            result = schedule_web_app.normalize_numeric_ids(input_dir, output_path)

            self.assertTrue(result["changed"])
            self.assertTrue(Path(result["backup_dir"]).exists())
            students_text = (input_dir / "students.csv").read_text(encoding="utf-8")
            lessons_text = (input_dir / "lessons.csv").read_text(encoding="utf-8")
            bookings_text = (input_dir / "existing_bookings.csv").read_text(encoding="utf-8")
            self.assertIn("1001", students_text)
            self.assertIn("1001-1", lessons_text)
            self.assertIn("book_1001-1", bookings_text)
            self.assertNotIn("stu_alice", students_text + lessons_text + bookings_text)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("1001-1", payload["schedule"][0]["lesson_id"])
            self.assertEqual("1001", payload["schedule"][0]["student_id"])

    def test_web_numeric_id_migration_is_idempotent(self) -> None:
        """Already-normalized input should not create another backup or rewrite."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)

            before_backups = sorted(input_dir.glob(".id_migration_backup_*"))
            result = schedule_web_app.normalize_numeric_ids(input_dir, output_path)
            after_backups = sorted(input_dir.glob(".id_migration_backup_*"))

            self.assertFalse(result["changed"])
            self.assertEqual(before_backups, after_backups)

    def test_web_save_venues_and_travel_times_writes_csv_rows(self) -> None:
        """The web setup editor should rewrite venues.csv and travel_times.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_venues_and_travel_times(
                input_dir,
                [
                    {"venue_id": "gym_a", "venue_name": "Left Studio"},
                    {"venue_id": "gym_b", "venue_name": "Right Studio"},
                    {"venue_id": "gym_c", "venue_name": "New Studio"},
                ],
                [
                    {"from_venue_id": "gym_a", "to_venue_id": "gym_a", "travel_min": "0"},
                    {"from_venue_id": "gym_a", "to_venue_id": "gym_c", "travel_min": "35"},
                ],
            )

            self.assertTrue(result["ok"])
            self.assertIn("gym_c,New Studio", (input_dir / "venues.csv").read_text(encoding="utf-8"))
            self.assertIn("gym_a,gym_c,35", (input_dir / "travel_times.csv").read_text(encoding="utf-8"))

    def test_web_save_venues_accepts_home_as_commute_venue(self) -> None:
        """Commute should work as a normal venue/travel row."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_venues_and_travel_times(
                input_dir,
                [
                    {"venue_id": "gym_a", "venue_name": "Left Studio"},
                    {"venue_id": "gym_b", "venue_name": "Right Studio"},
                    {"venue_id": "home", "venue_name": "Home"},
                ],
                [
                    {"from_venue_id": "home", "to_venue_id": "gym_a", "travel_min": "25"},
                    {"from_venue_id": "gym_a", "to_venue_id": "home", "travel_min": "25"},
                ],
            )

            self.assertTrue(result["ok"])
            self.assertIn("home,Home", (input_dir / "venues.csv").read_text(encoding="utf-8"))
            self.assertIn("home,gym_a,25", (input_dir / "travel_times.csv").read_text(encoding="utf-8"))

    def test_web_save_venues_rejects_removing_referenced_venue(self) -> None:
        """Venue deletion should not leave existing scheduler input with missing references."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_venues_and_travel_times(
                    input_dir,
                    [{"venue_id": "gym_b", "venue_name": "Right Studio"}],
                    [{"from_venue_id": "gym_b", "to_venue_id": "gym_b", "travel_min": "0"}],
                )

            messages = " ".join(error["message"] for error in raised.exception.errors)
            self.assertIn("Cannot remove venue_id=gym_a", messages)

    def test_web_save_trainer_availability_writes_csv_rows(self) -> None:
        """Trainer timeslot edits should rewrite coach_availability.csv."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.save_trainer_availability(
                input_dir,
                [
                    {"day": "Mon", "start": "09:00", "end": "12:00", "score": "5"},
                    {"day": "Sat", "start": "13:00", "end": "18:00", "score": "0"},
                ],
            )

            self.assertTrue(result["ok"])
            text = (input_dir / "coach_availability.csv").read_text(encoding="utf-8")
            self.assertIn("Mon,09:00,12:00,5", text)
            self.assertIn("Sat,13:00,18:00,0", text)

    def test_web_save_trainer_availability_rejects_bad_time_range(self) -> None:
        """Trainer timeslot validation should reject start times after end times."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.save_trainer_availability(
                    input_dir,
                    [{"day": "Mon", "start": "18:00", "end": "10:00", "score": "0"}],
                )

            self.assertIn("start must be before end", raised.exception.errors[0]["message"])

    def test_web_import_csv_files_writes_known_scheduler_files(self) -> None:
        """CSV upload should overwrite supported scheduler CSV files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.import_csv_files(
                input_dir,
                {
                    "coach_availability.csv": b"day,start,end,score\nSat,09:00,12:00,0\n",
                    "preferences.csv": b"student_id,day,start,end,level,score\nstu_alice,Sat,09:00,12:00,preferred,100\n",
                },
            )

            self.assertTrue(result["ok"])
            self.assertEqual(["coach_availability.csv", "preferences.csv"], result["imported_files"])
            self.assertIn("Sat,09:00,12:00,0", (input_dir / "coach_availability.csv").read_text(encoding="utf-8"))

    def test_web_import_route_reads_multipart_before_json_body(self) -> None:
        """Multipart CSV uploads should not be parsed as JSON first."""
        script = Path("schedule_web_app.py").read_text(encoding="utf-8")
        import_index = script.index('if parsed.path == "/api/import-csv":')
        json_index = script.index("payload = self.read_json_body()")

        self.assertLess(import_index, json_index)

    def test_web_import_csv_files_rejects_unknown_file_name(self) -> None:
        """CSV upload should reject files outside the scheduler CSV contract."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_demo_input", input_dir)

            with self.assertRaises(schedule_web_app.WebInputError) as raised:
                schedule_web_app.import_csv_files(
                    input_dir,
                    {"notes.csv": b"hello,world\n"},
                )

            self.assertIn("Unsupported CSV file", raised.exception.errors[0]["message"])

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_evaluate_schedule_scores_solution_without_writing(self) -> None:
        """Manual schedule evaluation should score placements without touching solution.json."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1002-1",
                        "student_id": "1002",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1003-1",
                        "student_id": "1003",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1004-1",
                        "student_id": "1004",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                ],
            )
            solved = schedule_web_app.run_solver(input_dir, output_path)
            before = output_path.read_text(encoding="utf-8")

            result = schedule_web_app.evaluate_schedule(input_dir, solved["solution"]["schedule"])

            self.assertTrue(result["ok"])
            self.assertIsInstance(result["score"], int)
            self.assertEqual(before, output_path.read_text(encoding="utf-8"))

    def test_web_evaluate_schedule_reports_manual_conflicts(self) -> None:
        """Evaluator diagnostics should report invalid manual schedule placements."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree("csv_test_input_shared_lessons", input_dir)

            result = schedule_web_app.evaluate_schedule(
                input_dir,
                [
                    {
                        "lesson_id": "lesson_anna",
                        "start_datetime": "2026-05-04T09:00",
                        "end_datetime": "2026-05-04T10:30",
                        "venue_id": "jianguo",
                        "shared_session_id": "couple_anna_brian",
                    },
                    {
                        "lesson_id": "lesson_brian",
                        "start_datetime": "2026-05-04T11:00",
                        "end_datetime": "2026-05-04T12:30",
                        "venue_id": "jianguo",
                        "shared_session_id": "couple_anna_brian",
                    },
                    {
                        "lesson_id": "lesson_cindy",
                        "start_datetime": "2026-05-04T09:30",
                        "end_datetime": "2026-05-04T10:30",
                        "venue_id": "fengshan",
                        "shared_session_id": "",
                    },
                ],
            )

            diagnostics = " ".join(result["diagnostics"])
            self.assertIn("overlapping lessons", diagnostics)
            self.assertIn("shared lessons must use the same time and venue", diagnostics)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_run_solver_returns_solution_shape(self) -> None:
        """The web backend solve helper should write and return solver output."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1002-1",
                        "student_id": "1002",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1003-1",
                        "student_id": "1003",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1004-1",
                        "student_id": "1004",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                ],
            )

            result = schedule_web_app.run_solver(input_dir, output_path)

            self.assertTrue(result["ok"])
            self.assertTrue(output_path.exists())
            self.assertIn(result["solution"]["status"], {"OPTIMAL", "FEASIBLE"})
            self.assertGreater(len(result["solution"]["schedule"]), 0)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_run_solver_does_not_overwrite_solution_when_infeasible(self) -> None:
        """A failed web solve should leave the last visible solution on disk."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1002-1",
                        "student_id": "1002",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1003-1",
                        "student_id": "1003",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1004-1",
                        "student_id": "1004",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                ],
            )
            good_result = schedule_web_app.run_solver(input_dir, output_path)
            before = output_path.read_text(encoding="utf-8")

            schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "couple_1",
                    },
                    {
                        "lesson_id": "1002-1",
                        "student_id": "1002",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "couple_1",
                    },
                    {
                        "lesson_id": "1003-1",
                        "student_id": "1003",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1004-1",
                        "student_id": "1004",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                ],
            )

            bad_result = schedule_web_app.run_solver(input_dir, output_path)

            self.assertTrue(good_result["ok"])
            self.assertFalse(bad_result["ok"])
            self.assertIn("no common time and venue", " ".join(bad_result["validation_warnings"]))
            self.assertEqual(before, output_path.read_text(encoding="utf-8"))

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_api_data_includes_solution_for_calendar_grid(self) -> None:
        """The data payload should include enough solved rows for the weekly grid."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.save_lessons(
                input_dir,
                [
                    {
                        "lesson_id": "1001-1",
                        "student_id": "1001",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "2",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1002-1",
                        "student_id": "1002",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1003-1",
                        "student_id": "1003",
                        "venue_id": "gym_b",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                    {
                        "lesson_id": "1004-1",
                        "student_id": "1004",
                        "venue_id": "gym_a",
                        "duration_min": "60",
                        "must_schedule": "TRUE",
                        "priority": "1",
                        "shared_session_id": "",
                    },
                ],
            )
            schedule_web_app.run_solver(input_dir, output_path)

            payload = schedule_web_app.build_api_data(input_dir, output_path)

            self.assertIn("table_rows", payload)
            self.assertIn("lessons", payload)
            self.assertIn("coach_availability", payload)
            self.assertGreater(len(payload["solution"]["schedule"]), 0)

    def test_web_static_exposes_manual_schedule_controls(self) -> None:
        """The static UI should include manual schedule controls and lesson editor hooks."""
        html = Path("schedule_web_static/index.html").read_text(encoding="utf-8")
        script = Path("schedule_web_static/app.js").read_text(encoding="utf-8")

        self.assertNotIn("saveButton", html)
        self.assertIn('data-view="students"', html)
        self.assertIn('data-view="lessons"', html)
        self.assertIn("saveStudentsButton", html)
        self.assertIn("generateLessonsButton", html)
        self.assertIn("importStudentCsvButton", html)
        self.assertIn("cleanStudentsButton", html)
        self.assertIn("studentCsvInput", html)
        self.assertIn("saveLessonsButton", html)
        self.assertIn("cleanLessonsButton", html)
        self.assertIn("organizerSaveLessonsButton", html)
        self.assertIn("saveSolutionButton", html)
        self.assertIn("savedSolutions", html)
        self.assertIn("runtimeMode", html)
        self.assertIn("runtimePlanningStart", html)
        self.assertIn("runtimePlanningEnd", html)
        self.assertIn("runtimeFreezeNow", html)
        self.assertIn('type="datetime-local"', html)
        self.assertNotIn("saveRuntimeConfigButton", html)
        self.assertIn("calendarBackgroundMode", html)
        self.assertIn("selectedStatus", html)
        self.assertIn("cleanScheduleButton", html)
        self.assertIn("toggleTrayButton", html)
        self.assertIn("unscheduledTray", html)
        self.assertIn("allOptionalButton", html)
        self.assertIn("allRequiredButton", html)
        self.assertNotIn('data-temp-slot="0"', html)
        self.assertIn("saveVenuesButton", html)
        self.assertIn("saveTrainerButton", html)
        self.assertIn("Config Parameters", html)
        self.assertIn("configTableBody", html)
        self.assertIn("saveConfigButton", html)
        self.assertIn("resetConfigButton", html)
        self.assertIn("Preference Scores", html)
        self.assertIn("savePreferenceScoresButton", html)
        self.assertIn("preferenceScoreTableBody", html)
        self.assertIn("Mon-Fri 0900-1200 preferred; Fri acceptable; Sat", html)
        self.assertIn("resetScheduleButton", html)
        self.assertIn("lessonTableBody", html)
        self.assertIn("shared_session_id", html)
        self.assertIn("lessons_per_week", html)
        self.assertIn("couple", html)
        self.assertIn("booking_day", html)
        self.assertIn("booking_start", html)
        self.assertIn("booking_status", html)
        self.assertIn("/api/evaluate-schedule", script)
        self.assertIn("draggable=\"true\"", script)
        self.assertIn("saveStudents", script)
        self.assertIn("saveLessons", script)
        self.assertIn("importStudentCsvFiles", script)
        self.assertIn("students.csv\", \"preferences.csv", script)
        self.assertIn('"/api/students/preferences"', script)
        self.assertIn('"/api/lessons"', script)
        self.assertIn("savePreferenceScores", script)
        self.assertIn("/api/preference-scores", script)
        self.assertIn("saveConfigParameters", script)
        self.assertIn("/api/config", script)
        self.assertNotIn("saveRuntimeConfig", script)
        self.assertIn("collectRuntimeConfig", script)
        self.assertIn("runtime_config", script)
        self.assertIn("resetConfigDefaults", script)
        self.assertIn("cleanStudents", script)
        self.assertIn("cleanLessons", script)
        self.assertIn("payload.solution", script)
        self.assertIn("Candidate count", script)
        self.assertIn("Solve time", script)
        self.assertIn("generateLessonsFromStudents", script)
        self.assertIn("reconcileLessonsFromStudents", script)
        self.assertIn('must_schedule: "FALSE"', script)
        self.assertIn("setAllMustSchedule", script)
        self.assertIn("renderUnscheduledTray", script)
        self.assertIn("toggleTrayVisibility", script)
        self.assertIn("tray-table", script)
        self.assertIn("selectedTrayKey", script)
        self.assertIn("--row-span:${rowSpan}", script)
        self.assertIn("preferenceClassForCell", script)
        self.assertIn("diagnosticIssuesByLesson", script)
        self.assertIn("lesson-issue", script)
        self.assertIn("updateSelectedStatus", script)
        self.assertIn("cleanSchedule", script)
        self.assertIn("sharedSessionForGeneratedLesson", script)
        self.assertIn("lessonIndex < sharedCount ? couple", script)
        self.assertIn("(?:to|and)", script)
        self.assertIn("savedSolutions", script)
        self.assertIn("MAX_SAVED_SOLUTIONS", script)
        self.assertIn("window.confirm", script)
        self.assertIn("structuredClone(currentSolution)", script)
        self.assertIn("saveVisibleSolution", script)
        self.assertIn("appendGeneratedLessonForStudent", script)
        self.assertIn("restoreUnsavedLessonRows", script)
        self.assertIn('dirtySections.has("lessons") ? collectLessonRows() : null', script)
        self.assertIn("updateLessonRowsFromSchedule", script)
        self.assertIn("lesson-status", script)
        self.assertIn("status-confirmed", Path("schedule_web_static/styles.css").read_text(encoding="utf-8"))
        self.assertIn("syncScheduleToCurrentInput", script)
        self.assertIn("updated ${result.updatedCount} visible placement(s)", script)
        self.assertIn("resized ${result.resizedCount} visible placement(s)", script)
        self.assertIn('"booking_id", "lesson_id", "student_id", "venue_id", "start_datetime", "end_datetime", "status", "lock_level"', script)
        self.assertNotIn('"lesson_id", "start_datetime", "end_datetime", "venue_id", "shared_session_id"', script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
