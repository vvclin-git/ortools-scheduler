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
            output_path = Path(temp_dir) / "solution.json"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                csv_runner.main(
                    [
                        "--input",
                        "csv_demo_input",
                        "--output",
                        str(output_path),
                        "--print-solution",
                    ]
                )

            printed = stdout.getvalue()
            self.assertIn("Schedule", printed)
            self.assertIn("lesson_bob", printed)
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
                "lesson_id,student_id,venue_id,duration_min,must_schedule,priority,shared_session_id",
                (template_dir / "lessons.csv").read_text(encoding="utf-8").splitlines()[0],
            )
            self.assertEqual(
                "key,value",
                (template_dir / "config.csv").read_text(encoding="utf-8").splitlines()[0],
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
            "Mon 18:00-21:00 preferred 100; Wed 19:00-21:00 acceptable 60",
        )

        self.assertEqual(2, len(preferences))
        self.assertEqual("stu_alice", preferences[0].student_id)
        self.assertEqual("Mon", preferences[0].day)
        self.assertEqual("18:00", preferences[0].start)
        self.assertEqual("21:00", preferences[0].end)
        self.assertEqual("preferred", preferences[0].level)
        self.assertEqual(100, preferences[0].score)

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
                        "available_timeslots": "Tue 10:00-12:00 preferred 100",
                        "venues": "default=gym_a",
                    }
                ],
            )

            self.assertTrue(result["ok"])
            self.assertIn("Alice Updated", (input_dir / "students.csv").read_text(encoding="utf-8"))
            self.assertIn("Tue,10:00,12:00,preferred,100", (input_dir / "preferences.csv").read_text(encoding="utf-8"))

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

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_run_solver_returns_solution_shape(self) -> None:
        """The web backend solve helper should write and return solver output."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)

            result = schedule_web_app.run_solver(input_dir, output_path)

            self.assertTrue(result["ok"])
            self.assertTrue(output_path.exists())
            self.assertIn(result["solution"]["status"], {"OPTIMAL", "FEASIBLE"})
            self.assertGreater(len(result["solution"]["schedule"]), 0)

    @unittest.skipIf(solver.cp_model is None, "OR-Tools is not installed.")
    def test_web_api_data_includes_solution_for_calendar_grid(self) -> None:
        """The data payload should include enough solved rows for the weekly grid."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_path = Path(temp_dir) / "solution.json"
            shutil.copytree("csv_demo_input", input_dir)
            schedule_web_app.run_solver(input_dir, output_path)

            payload = schedule_web_app.build_api_data(input_dir, output_path)

            self.assertIn("table_rows", payload)
            self.assertIn("lessons", payload)
            self.assertIn("coach_availability", payload)
            self.assertGreater(len(payload["solution"]["schedule"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
