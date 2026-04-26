# Trainer Solver Input Data Guide

This project reads scheduler input from a folder of CSV files. Each CSV file
represents one table. You can edit the files directly, or keep one sheet per
table in Excel/Google Sheets and export each sheet as CSV.

Use this guide when preparing test data for the solver.

## Quick Workflow

Create a starter input folder:

```bash
uv run python run_solver_from_csv.py --init-template my_schedule_input
```

Edit the generated CSV files, then validate without solving:

```bash
uv run python run_solver_from_csv.py --input my_schedule_input --validate-only
```

Run the solver and print a quick terminal review:

```bash
uv run python run_solver_from_csv.py --input my_schedule_input --output solution.json --print-summary --print-solution
```

For deeper debugging, also dump the normalized request and generated candidates:

```bash
uv run python run_solver_from_csv.py --input my_schedule_input --output solution.json --dump-request request_debug.json --print-summary --print-solution
```

## Folder Structure

```text
my_schedule_input/
  config.csv
  students.csv
  venues.csv
  travel_times.csv
  lessons.csv
  preferences.csv
  coach_availability.csv
  existing_bookings.csv      # optional
  absences.csv               # optional
  weights.csv                # optional
```

Required files must exist, even if they only contain headers plus a few rows.
Optional files may be omitted.

## Data Preparation Order

Prepare test data in this order so IDs line up correctly:

1. `config.csv`: choose planning dates and mode.
2. `venues.csv`: define all places where lessons can happen.
3. `students.csv`: define students and their default venue.
4. `travel_times.csv`: define travel time between venues.
5. `lessons.csv`: define the lessons that need scheduling.
6. `preferences.csv`: define when each student can attend.
7. `coach_availability.csv`: define when the coach can teach.
8. Optional `existing_bookings.csv`: add current bookings for rescheduling tests.
9. Optional `absences.csv`: add coach, student, or venue blocks.
10. Optional `weights.csv`: tune the objective.

## General Format Rules

- CSV files must use headers exactly as documented below.
- IDs are case-sensitive and should not contain spaces.
- Display-name fields such as `students.name` and `venues.name` may contain
  Traditional Chinese or other Unicode text.
- Save CSV files as UTF-8. UTF-8 with BOM is also accepted.
- The solver writes JSON output as UTF-8 and preserves Traditional Chinese text.
- Dates use ISO datetime format: `YYYY-MM-DDTHH:MM:SS`.
- Times use 24-hour format: `HH:MM`.
- Days must be one of: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun`.
- Boolean values may use `TRUE`/`FALSE`, `true`/`false`, `1`/`0`, or `yes`/`no`.
- Blank optional numeric cells use the solver defaults.
- Unknown references are rejected by validation. For example, every
  `lesson.student_id` must exist in `students.csv`.

## Required Files

### config.csv

Key-value settings for the solve.

| column | required | meaning |
|---|---:|---|
| `key` | yes | Config key name. |
| `value` | yes | Config value. |

Required keys:

| key | example | meaning |
|---|---|---|
| `mode` | `weekly_planning` | Solver mode. Use `weekly_planning` for a fresh plan, or `in_week_reschedule` when modifying current bookings. |
| `planning_start` | `2026-05-04T09:00:00` | Start of the planning horizon. |
| `planning_end` | `2026-05-10T22:00:00` | End of the planning horizon. Must be after `planning_start`. |

Optional keys:

| key | default | meaning |
|---|---:|---|
| `slot_size_min` | `30` | Candidate start-time interval in minutes. |
| `max_solve_seconds` | `10.0` | OR-Tools solve time limit. |
| `freeze_now` | blank | Current timestamp for freeze-window logic. |
| `freeze_buffer_hours` | `4` | In `in_week_reschedule`, bookings before `freeze_now + freeze_buffer_hours` are fixed. |

Example:

```csv
key,value
mode,in_week_reschedule
planning_start,2026-05-04T09:00:00
planning_end,2026-05-10T22:00:00
slot_size_min,30
max_solve_seconds,5.0
freeze_now,2026-05-06T14:00:00
freeze_buffer_hours,4
```

### students.csv

Students who may receive lessons.

| column | required | example | meaning |
|---|---:|---|---|
| `student_id` | yes | `stu_alice` | Unique student ID. |
| `name` | yes | `王小明` | Display name in output. Traditional Chinese is supported. |
| `default_venue_id` | yes | `gym_a` | Student's usual venue. Must exist in `venues.csv`. |
| `priority` | no | `2` | Student priority for future use and data labeling. Defaults to `1`. |

Example:

```csv
student_id,name,default_venue_id,priority
stu_alice,王小明,gym_a,2
stu_bob,陳美華,gym_b,1
```

### venues.csv

Places where lessons can happen.

| column | required | example | meaning |
|---|---:|---|---|
| `venue_id` | yes | `gym_a` | Unique venue ID. |
| `name` | yes | `台北教室A` | Display name in output. Traditional Chinese is supported. |

Example:

```csv
venue_id,name
gym_a,台北教室A
gym_b,板橋教室B
```

### travel_times.csv

Travel time between venues in minutes.

| column | required | example | meaning |
|---|---:|---|---|
| `from_venue_id` | yes | `gym_a` | Start venue. Must exist in `venues.csv`. |
| `to_venue_id` | yes | `gym_b` | End venue. Must exist in `venues.csv`. |
| `travel_min` | yes | `50` | Non-negative travel time in minutes. |

Write both directions if travel should work both ways. Missing paths are treated
as a very large travel time, so back-to-back scheduling between those venues will
usually be impossible.

Example:

```csv
from_venue_id,to_venue_id,travel_min
gym_a,gym_a,0
gym_b,gym_b,0
gym_a,gym_b,50
gym_b,gym_a,50
```

### lessons.csv

Lesson requests to schedule.

| column | required | example | meaning |
|---|---:|---|---|
| `lesson_id` | yes | `lesson_alice_1` | Unique lesson ID. |
| `student_id` | yes | `stu_alice` | Student receiving the lesson. Must exist in `students.csv`. |
| `venue_id` | yes | `gym_a` | Venue for the lesson. Must exist in `venues.csv`. |
| `duration_min` | no | `60` | Lesson duration in minutes. Defaults to `60`; must be positive. |
| `must_schedule` | no | `TRUE` | Required lessons must be scheduled. Optional lessons may be left unscheduled. Defaults to `TRUE`. |
| `priority` | no | `2` | Lesson priority used in scoring. Defaults to `1`. |
| `shared_session_id` | no | `couple_lee` | Set the same value on multiple lesson rows when those students share one lesson slot. Leave blank for normal one-student lessons. |

Example:

```csv
lesson_id,student_id,venue_id,duration_min,must_schedule,priority,shared_session_id
lesson_alice_1,stu_alice,gym_a,60,TRUE,2,
lesson_bob_1,stu_bob,gym_b,60,FALSE,1,couple_lee
lesson_carol_1,stu_carol,gym_b,60,FALSE,1,couple_lee
```

Rows with the same `shared_session_id` are scheduled as the same lesson only
when their selected start time, end time, and venue match exactly. They still
use each student's own preference and absence data.

### preferences.csv

Student availability and preference windows. A lesson candidate is generated
only when the full lesson fits inside one of that student's preference windows.

| column | required | example | meaning |
|---|---:|---|---|
| `student_id` | yes | `stu_alice` | Must exist in `students.csv`. |
| `day` | yes | `Mon` | Weekly day name. |
| `start` | yes | `18:00` | Window start time. |
| `end` | yes | `21:00` | Window end time. Must be after `start`. |
| `level` | yes | `preferred` | Label shown in output. Current solver uses `score` for ranking. |
| `score` | yes | `100` | Numeric preference score. Higher is better. |

Use multiple rows per student if they have multiple possible windows.

Example:

```csv
student_id,day,start,end,level,score
stu_alice,Mon,18:00,21:00,preferred,100
stu_alice,Wed,19:00,21:00,acceptable,60
stu_bob,Fri,18:00,22:00,preferred,100
```

### coach_availability.csv

Coach teaching windows. A lesson candidate is generated only when the full
lesson fits inside a coach availability window.

| column | required | example | meaning |
|---|---:|---|---|
| `day` | yes | `Mon` | Weekly day name. |
| `start` | yes | `10:00` | Availability start time. |
| `end` | yes | `22:00` | Availability end time. Must be after `start`. |
| `score` | no | `0` | Numeric score added to candidates in this window. Defaults to `0`. |

Example:

```csv
day,start,end,score
Mon,10:00,22:00,0
Tue,10:00,22:00,0
Fri,10:00,22:00,10
```

## Optional Files

### existing_bookings.csv

Current bookings used for rescheduling scenarios. If omitted, the solver treats
the problem as a fresh schedule.

| column | required | example | meaning |
|---|---:|---|---|
| `booking_id` | yes | `book_alice_1` | Unique booking ID. |
| `lesson_id` | yes | `lesson_alice_1` | Must exist in `lessons.csv`. |
| `student_id` | yes | `stu_alice` | Must exist in `students.csv`. |
| `venue_id` | yes | `gym_a` | Must exist in `venues.csv`. |
| `start_datetime` | yes | `2026-05-04T18:00:00` | Existing booking start. |
| `end_datetime` | yes | `2026-05-04T19:00:00` | Existing booking end. Must be after start. |
| `status` | no | `confirmed` | Booking state. Defaults to `confirmed`. |
| `lock_level` | no | `1` | `lock_level >= 3` means fixed. Defaults to `1`. |

Supported `status` values:

- `draft`
- `confirmed`
- `completed`
- `in_progress`
- `locked`

Fixed bookings:

- `completed`, `in_progress`, and `locked` bookings cannot move.
- Any booking with `lock_level >= 3` cannot move.
- In `in_week_reschedule`, bookings before the freeze threshold cannot move.
- Fixed bookings are validated against absences, coach availability, horizon,
  overlap, and travel-time conflicts before solving.

Example:

```csv
booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level
book_alice_1,lesson_alice_1,stu_alice,gym_a,2026-05-04T18:00:00,2026-05-04T19:00:00,confirmed,1
book_bob_1,lesson_bob_1,stu_bob,gym_b,2026-05-07T19:00:00,2026-05-07T20:00:00,locked,3
```

### absences.csv

Blocks time for a student, the coach, or a venue.

| column | required | example | meaning |
|---|---:|---|---|
| `entity_type` | yes | `student` | One of `student`, `coach`, or `venue`. |
| `entity_id` | yes | `stu_bob` | Student ID, venue ID, or a coach label. Coach absences apply globally. |
| `start_datetime` | yes | `2026-05-07T18:00:00` | Absence start. |
| `end_datetime` | yes | `2026-05-07T22:00:00` | Absence end. Must be after start. |
| `reason` | no | `Bob asks to reschedule` | Free-text note for humans. |

Example:

```csv
entity_type,entity_id,start_datetime,end_datetime,reason
student,stu_bob,2026-05-07T18:00:00,2026-05-07T22:00:00,Bob asks to reschedule
coach,coach_main,2026-05-08T12:00:00,2026-05-08T14:00:00,Lunch break
venue,gym_a,2026-05-09T10:00:00,2026-05-09T12:00:00,Venue maintenance
```

### weights.csv

Optional objective weights. Omit this file to use defaults.

| key | default | meaning |
|---|---:|---|
| `lesson_priority` | `20` | Score multiplier for `lessons.priority`. |
| `keep_original_bonus` | `800` | Bonus for keeping an existing booking at the same time. |
| `move_confirmed_penalty` | `1000` | Penalty for moving a confirmed booking. |
| `move_draft_penalty` | `200` | Penalty for moving a draft booking. |
| `cancel_optional_penalty` | `3000` | Penalty for leaving an optional lesson unscheduled. |
| `cross_venue_pair_penalty_per_min` | `1` | Same-day cross-venue penalty per travel minute. |

Example:

```csv
key,value
lesson_priority,20
keep_original_bonus,800
move_confirmed_penalty,1000
move_draft_penalty,200
cancel_optional_penalty,3000
cross_venue_pair_penalty_per_min,1
```

## Suggested Test Scenarios

Create separate input folders for each scenario so outputs are easy to compare.

### Fresh weekly plan

- `mode=weekly_planning`
- Leave `existing_bookings.csv` empty or omit it.
- Add several students, lessons, preferences, and coach availability windows.
- Expected: required lessons are scheduled when feasible; optional lessons may
  be scheduled or left unscheduled depending on scores and penalties.

### In-week reschedule

- `mode=in_week_reschedule`
- Set `freeze_now` and `freeze_buffer_hours`.
- Add existing bookings and one student absence that conflicts with a booking.
- Expected: movable bookings can move; frozen, locked, completed, and
  in-progress bookings stay fixed or return `INFEASIBLE_INPUT` if impossible.

### Travel-time pressure

- Add at least two venues.
- Set non-zero travel times in both directions.
- Give the coach back-to-back student preferences at different venues.
- Expected: the solver adds enough time between venues or chooses another slot.

### Infeasible required lesson

- Add a required lesson for a student with no matching preference window or no
  matching coach availability.
- Expected: status is `INFEASIBLE_INPUT` if no candidate exists for a required
  lesson.

### Optional lesson tradeoff

- Add an optional lesson with a low-scoring or inconvenient slot.
- Tune `cancel_optional_penalty`.
- Expected: a higher penalty makes optional lessons more likely to be scheduled.

## Validation Checklist

Before solving, run:

```bash
uv run python run_solver_from_csv.py --input my_schedule_input --validate-only
```

Check these common issues:

- Every referenced `student_id`, `venue_id`, and `lesson_id` exists.
- IDs are unique within their own file.
- `planning_start` is before `planning_end`.
- Durations, slot size, travel times, and lock levels are valid numbers.
- Preference and availability windows use valid day names and `HH:MM` times.
- Datetime ranges use `YYYY-MM-DDTHH:MM:SS` and start before end.
- Fixed bookings do not overlap, violate travel time, conflict with absences,
  fall outside coach availability, or fall outside the planning horizon.
