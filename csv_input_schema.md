# Trainer Solver CSV Input Schema

Use this folder structure:

```text
csv_input/
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

Run:

```bash
uv run python run_solver_from_csv.py --input csv_input --output solution.json --dump-request request_debug.json --print-summary
```

Print a compact terminal solution table:

```bash
uv run python run_solver_from_csv.py --input csv_input --output solution.json --print-summary --print-solution
```

Create a starter input folder:

```bash
uv run python run_solver_from_csv.py --init-template csv_input
```

Validate input without solving:

```bash
uv run python run_solver_from_csv.py --input csv_input --validate-only
```

Spreadsheet users can keep one sheet per CSV file and export each sheet as CSV.

## config.csv

| column | meaning |
|---|---|
| key | config key |
| value | config value |

Required keys: `mode`, `planning_start`, `planning_end`.

Supported mode values:

- `weekly_planning`
- `in_week_reschedule`

Optional keys:

- `slot_size_min`
- `max_solve_seconds`
- `freeze_now`
- `freeze_buffer_hours`

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

## students.csv

```csv
student_id,name,default_venue_id,priority
stu_alice,Alice,gym_a,2
```

## venues.csv

```csv
venue_id,name
gym_a,左營館
```

## travel_times.csv

```csv
from_venue_id,to_venue_id,travel_min
gym_a,gym_b,50
```

Write both directions if needed, for example `gym_a -> gym_b` and `gym_b -> gym_a`.

## lessons.csv

```csv
lesson_id,student_id,venue_id,duration_min,must_schedule,priority
lesson_alice,stu_alice,gym_a,60,TRUE,2
```

## preferences.csv

```csv
student_id,day,start,end,level,score
stu_alice,Mon,18:00,21:00,preferred,100
```

Day must use: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun`.

## coach_availability.csv

```csv
day,start,end,score
Mon,10:00,22:00,0
```

## existing_bookings.csv optional

```csv
booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level
book_alice,lesson_alice,stu_alice,gym_a,2026-05-04T18:00:00,2026-05-04T19:00:00,confirmed,1
```

Common status values:

- `draft`
- `confirmed`
- `completed`
- `in_progress`
- `locked`

`lock_level >= 3` means the booking cannot be moved.

## absences.csv optional

```csv
entity_type,entity_id,start_datetime,end_datetime,reason
student,stu_bob,2026-05-07T18:00:00,2026-05-07T22:00:00,Bob asks to reschedule
```

Supported `entity_type` values:

- `student`
- `coach`
- `venue`

## weights.csv optional

```csv
key,value
lesson_priority,20
keep_original_bonus,800
move_confirmed_penalty,1000
move_draft_penalty,200
cancel_optional_penalty,3000
cross_venue_pair_penalty_per_min,1
```
