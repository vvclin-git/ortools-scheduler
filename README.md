# OR-Tools Trainer Scheduler

A Python MVP for scheduling trainer or coach lessons with Google OR-Tools
CP-SAT.

The solver takes lesson requests, student preferences, coach availability,
existing bookings, absences, venue travel times, and rescheduling rules, then
returns an optimized schedule plus a structured change summary.

## Current Capabilities

- Schedule required and optional lesson requests inside a planning horizon.
- Respect student preference windows and coach availability windows.
- Avoid student, coach, and venue absences.
- Keep completed, in-progress, locked, or high-lock-level bookings fixed.
- Apply an in-week freeze buffer so near-future bookings are not moved.
- Enforce travel-time feasibility between lessons at different venues.
- Prefer keeping existing confirmed or draft bookings when possible.
- Penalize cross-venue same-day schedules through objective weights.
- Load scheduler input from CSV files and write JSON solver output.
- Produce a debug request payload for inspecting parsed input and candidates.

## Repository Structure

```text
.
|-- trainer_solver_mvp.py          # Solver dataclasses, candidate generation, CP-SAT model
|-- run_solver_from_csv.py         # CSV loader and command-line runner
|-- test_trainer_solver_mvp.py     # Unit tests for solver behavior
|-- csv_input_schema.md            # CSV input contract
|-- csv_demo_input/                # Demo CSV input folder
|-- solution.json                  # Example generated solver output
|-- request_debug.json             # Example generated debug payload
|-- pyproject.toml                 # Python project metadata and dependencies
`-- README.md
```

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- OR-Tools, declared in `pyproject.toml` as `ortools>=9.15.6755`

## Setup

```bash
uv sync
```

If you are not using `uv`, install the project dependency manually:

```bash
pip install "ortools>=9.15.6755"
```

## Quick Start

Run the demo CSV input through the solver:

```bash
uv run python run_solver_from_csv.py --input csv_demo_input --output solution.json --dump-request request_debug.json --print-summary
```

This writes:

- `solution.json`: the solver response, including status, summary, schedule,
  changes, and warnings.
- `request_debug.json`: the parsed request and generated candidate assignments
  for debugging.

The included demo currently solves to an optimal schedule with four scheduled
lessons, no unscheduled lessons, and one moved lesson caused by an absence.

For terminal review, print the selected schedule and changes directly:

```bash
uv run python run_solver_from_csv.py --input csv_demo_input --output solution.json --print-summary --print-solution
```

For calendar review, also write an iCalendar file:

```bash
uv run python run_solver_from_csv.py --input csv_demo_input --output solution.json --output-ics schedule.ics --print-summary
```

The ICS export includes lesson events and transparent trainer-availability
events. Shared lessons are grouped into one event, and any lesson outside coach
availability is prefixed with `[OUTSIDE AVAILABILITY]`.

### Command-Line Workflow

The solver can be used without the browser UI. In this workflow, edit the CSV
files directly, validate them, then run the optimizer from the terminal.

1. Create or copy an input folder containing the required CSV files.

   ```bash
   uv run python run_solver_from_csv.py --init-template my_schedule_input
   ```

2. Edit the CSV files in a text editor, Excel, or Google Sheets. Keep one sheet
   per CSV file if using a spreadsheet, then export each sheet back to CSV.

3. Validate the input without solving.

   ```bash
   uv run python run_solver_from_csv.py --input my_schedule_input --validate-only
   ```

4. Run the solver and review output in the terminal.

   ```bash
   uv run python run_solver_from_csv.py --input my_schedule_input --output solution.json --print-summary --print-solution
   ```

5. Optionally write debug and calendar files.

   ```bash
   uv run python run_solver_from_csv.py --input my_schedule_input --output solution.json --dump-request request_debug.json --output-ics schedule.ics --print-summary
   ```

The command-line path writes `solution.json` and optional artifacts only. It
does not apply browser cleanup actions such as `Clean Students`, `Clean
Lessons`, or staged CSV import; those are web-app workflows. To avoid stale data
without the UI, edit or clear dependent CSV files directly. For example, after
replacing `students.csv`, also update or clear `preferences.csv`, `lessons.csv`,
and `existing_bookings.csv`.

Run the local browser organizer:

```bash
uv run python schedule_web_app.py --input csv_demo_input --output solution.json --port 8001
```

On Windows, you can also double-click `run_web_app.cmd` from the project
folder to start the app on port `8001` and open it in your default browser, or
run it from PowerShell:

```powershell
.\run_web_app.cmd
```

Then open `http://127.0.0.1:8001`. The organizer reads and writes the same CSV
folder, runs the optimizer, and visualizes the schedule in a weekly grid.
Inputs are split across pages with page-owned save actions:

- `Students`: edit students, default venues, and student preference windows,
  set `lessons_per_week`, set optional `couple` grouping keys, import
  `students.csv`/`preferences.csv`, and generate lesson rows from the current
  student plan.
- `Lessons`: edit lesson requests plus scheduled booking day, start, derived
  end, status, required/optional state, venue, and shared-session grouping.
- `Setup`: import CSV files, edit venues/travel, edit trainer timeslots, and
  adjust solver config parameters other than `planning_start`, `planning_end`,
  and `freeze_now`, plus preference level scores, with separate save buttons.
  `Reset Defaults` restores the config editor to the template defaults; click
  `Save Config` to write those values to `config.csv`.

Student preference text can use readable levels without numeric scores. The
Setup page maps levels such as `preferred` and `acceptable` to solver scores,
defaulting to `100`, `60`, and `20` for `preferred`, `acceptable`, and
`last_resort`. The Students page accepts compact forms such as
`Mon-Fri 0900-1200 preferred`, `Mon 9:00-12:00 acceptable`, or `Fri`; blank
preference text and weekday entries without an explicit time use the matching
trainer timeslots as `preferred`.

The Organizer page supports manual schedule review and repair:

- Drag scheduled sessions to another day/time, drag unscheduled lessons from
  the left-side resizable tray into the calendar, and drag scheduled lessons
  back to the tray to clear their visible booking time.
- Review the full daily timeslot range in a compact Organizer layout that
  scales the calendar row height, shared typography, and surrounding controls
  to the browser viewport.
- Click a scheduled block to change its status; status changes are saved
  immediately for that session/group.
- Toggle calendar overlays for trainer availability, selected-student
  preference frames, and preference hotzones. Preference frames use different
  colors for `preferred`, `acceptable`, and `last_resort`, and also appear
  temporarily while a lesson is being dragged without rebuilding the drag
  source.
- Use the preference hotzone background to see unweighted demand across all
  students, counting how many students prefer each visible 30-minute slot.
- Review score and conflict diagnostics in the diagnostics panel and directly
  on affected calendar blocks; the top summary keeps separate critical and
  warning counts in the same header row, and diagnostics refresh after each
  visible drag/drop placement so old preference warnings are not reused.
- Save temporary in-browser solution snapshots, clear only the current working
  calendar, or export/import the visible schedule with the solver-readable
  booking CSV format:

```csv
booking_id,lesson_id,student_id,venue_id,start_datetime,end_datetime,status,lock_level
```

Manual organizer edits update the in-memory Lessons booking fields first.
Either `Save Lessons` button persists those booking fields to
`existing_bookings.csv`. `Save Solution` creates up to five memory-only
fast-switch snapshots labeled with a schedule hash, score, and
scheduled/unscheduled counts; snapshots are cleared by page refresh or app
restart.

Manual evaluator feedback is split into blocking `Critical:` issues and
non-blocking `Warning:` issues. `Save Lessons` and `Save Solution` both block
critical schedule-feasibility conflicts before saving. That includes
overlapping bookings, shared-session time or venue mismatches,
venue-travel infeasibility, invalid rows or times, absence conflicts, and
similar manual-schedule conflicts. Preference-window misses, coach-availability
misses, and required lessons left out of the manual schedule still appear as
warnings in the Organizer, but they do not block the save by themselves.

Frequently changed runtime config values are available on the Organizer:
`mode`, `planning_start`, `planning_end`, and `freeze_now`. The Organizer opens
with `planning_start` and `planning_end` set to the current local week and
`freeze_now` set to the current local date/time, even if `config.csv` contains
older runtime dates. `Run Optimizer` validates the visible Organizer values,
uses them for the solve request, and writes them back to `config.csv`, so the
Organizer is the active editor for the planning horizon and freeze timestamp.

Lesson time and status fields are booking edits, not lesson request fields.
Blank day/start/status means the lesson has no current booking row. When set,
the UI writes one booking row per lesson, using `book_<lesson_id>` for new
bookings. The app exposes `draft`, `confirmed`, `completed`, and `locked`;
completed and locked rows keep their time fixed until the status is changed.

Data cleanup follows ownership rules to avoid old data mixing with new data:

- `Clean Students` immediately clears students, preferences, lessons, bookings,
  and stale solution data.
- `Clean Lessons` immediately clears lessons, bookings, and stale solution
  data.
- `Clean Schedule` immediately clears only booking times/statuses, persists the
  cleared booking state, and keeps temporary saved solution snapshots available.
- Student CSV import replaces students/preferences and clears old
  lessons/bookings.
- General scheduler CSV import validates files in a temporary staged copy
  before replacing active CSV files.

After a successful optimizer run, scheduled solution times are loaded into the
Lessons page as unsaved `draft` booking times. Review or edit them, then click
`Save Lessons` to persist them to `existing_bookings.csv`. If the optimizer
returns validation errors or an infeasible result, the Organizer diagnostics
panel shows the solver status, solve time, candidate count when available, and
solver warnings so the failed run can be inspected without overwriting the
previous usable `solution.json`.

Any successful web save or scheduler CSV import clears the previous
`solution.json` because the saved input may no longer match that optimizer
output. Run the optimizer again to create a fresh solution, or use saved booking
rows in `existing_bookings.csv` as the current manual schedule.

When the web app starts, it normalizes old text IDs into numeric CSV IDs. Student
IDs become `1001`, `1002`, and so on; lesson IDs become values like `1001-1`.
References in preferences, lessons, bookings, student absences, and
`solution.json` are rewritten together. The app creates a timestamped
`.id_migration_backup_...` folder before rewriting.

The Students page can create lesson rows from each student's `lessons_per_week`
plan. `Add Student` creates a numeric student row and one matching unscheduled
lesson row immediately. Use row checkboxes with `Make Couple` or `Clear Couple`
to bulk edit the existing `couple` column before saving or regenerating lessons.
`Generate Lessons` regenerates lesson rows from the current `lessons_per_week`
and `couple` values, saves both Students and Lessons, and clears old booking
times so stale lesson/session groupings do not survive student changes. When two
or more students share the same non-blank `couple` value, generated lesson rows
use indexed shared IDs such as `pair_a-1` only up to the smallest lesson count
inside the group. New and generated lesson rows default to optional; use the
Lessons page bulk buttons to mark all visible lessons required or optional.

When lesson input changes affect duration, `Save Lessons` updates the visible
organizer placements to match the current `duration_min` values before
evaluation. Run the optimizer again when you want the solver to choose new
times for the changed lesson lengths.

Shared lessons must have the same `shared_session_id`, venue, start time, and
end time. Each student in the shared session still needs a preference window
that covers that exact slot; otherwise the optimizer reports that the shared
session has no common time and venue candidate.

The setup page also accepts direct CSV uploads for the scheduler input files,
including `students.csv`, `preferences.csv`, `coach_availability.csv`,
`venues.csv`, `travel_times.csv`, `lessons.csv`, and the optional CSV files.
For trainer commute time, add a normal venue such as `home` and enter
`home -> venue` and `venue -> home` rows in the venue travel-time table.

## CSV Input

CSV input is stored as one folder with required and optional files.

Required files:

- `config.csv`
- `students.csv`
- `venues.csv`
- `travel_times.csv`
- `lessons.csv`
- `preferences.csv`
- `coach_availability.csv`

Optional files:

- `existing_bookings.csv`
- `absences.csv`
- `weights.csv`

See [csv_input_schema.md](csv_input_schema.md) for the full test-data
preparation guide, including file order, required columns, valid values,
examples, scenario ideas, and validation checks.

For couple or shared lessons, give each student's lesson row the same
`shared_session_id` in `lessons.csv`. Blank values keep the normal one-student
lesson behavior.

Create a starter CSV input folder:

```bash
uv run python run_solver_from_csv.py --init-template my_schedule_input
```

Validate an input folder without solving:

```bash
uv run python run_solver_from_csv.py --input my_schedule_input --validate-only
```

Excel or Google Sheets users can edit the generated CSV files directly, or keep
one sheet per CSV file and export each sheet back to CSV before running the
solver.

Use stable ASCII IDs such as `stu_alice` or `gym_a` for references. Display
names for students and venues may be Traditional Chinese, as long as CSV files
are saved as UTF-8.

## Programmatic Usage

The main solver API is:

```python
from trainer_solver_mvp import SolverRequest, solve_schedule

response = solve_schedule(request)
```

Where `request` is a `SolverRequest` containing:

- `SolverConfig`
- students
- venues
- travel times
- lesson requests
- student preferences
- coach availability
- optional absences
- optional existing bookings

`solve_schedule` returns a `SolverResponse` with:

- `status`
- `summary`
- `schedule`
- `changes`
- `warnings`

## Testing

Run the unit test suite:

```bash
uv run python -m unittest test_trainer_solver_mvp.py -v
```

The suite currently has 82 tests covering solver behavior, CSV parsing and
validation, terminal output, iCalendar export, web save paths, staged import
rollback, data cleanup cascades, manual schedule evaluation, and fixture ID
migration.

## Current Limitations and Future Work

- This is still an MVP local scheduler, not a hosted multi-user product.
- The browser organizer is a local standard-library web app; there is no
  production API service, database integration, authentication, or deployment
  layer.
- The command-line workflow has no interactive cleanup UI. CSV ownership rules
  must be handled by editing dependent files directly.
- Input validation exists in the CSV loader, solver request validation, and web
  save paths, but production-grade diagnostics and recovery flows would need
  more work.
- Infeasible-case diagnostics are basic and should be expanded for production
  use.
- Objective scoring is configurable but still simple; real deployments may need
  more domain-specific weights and fairness constraints.
- The public Python API may change as the data model becomes more stable.
