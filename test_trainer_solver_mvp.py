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

import copy
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
