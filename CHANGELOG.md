# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Added a local browser organizer split into Organizer, Students, Lessons, and
  Setup pages with section-specific save actions.
- Added Organizer manual scheduling: drag/drop calendar editing, a left-side
  resizable unscheduled lesson tray, immediate schedule evaluation, visible
  diagnostics on blocks, reset, saved in-browser solution snapshots, and
  booking CSV import/export using `existing_bookings.csv`.
- Added Organizer controls for selected-session status saving, booking status
  coloring, trainer-availability versus selected-student preference backgrounds,
  current-week runtime date/time inputs, solve-time diagnostics, and an
  Organizer-side `Save Lessons`.
- Added editable Students and Lessons workflows: `lessons_per_week`, `couple`,
  student CSV import, compact preference text parsing, Setup-managed preference
  scores, `last_resort` defaults, generated lessons, shared-session editing,
  lesson booking fields, and bulk required/optional controls.
- Added data ownership cleanup behavior: `Clean Students` clears students,
  preferences, lessons, bookings, and stale solutions; `Clean Lessons` clears
  lessons, bookings, and stale solutions; `Clean Schedule` clears booking times
  and persists immediately.
- Added staged CSV import validation so failed imports do not partially
  overwrite active input files; student-scoped CSV import clears old
  lessons/bookings to avoid orphan data.
- Added automatic web-app startup migration from text IDs to numeric student
  and lesson IDs with timestamped CSV backups.
- Added `run_web_app.cmd` as a Windows shortcut that starts the app on port
  `8001` and opens the default browser automatically.
- Added a read-only `/api/evaluate-schedule` endpoint for scoring manual
  schedules without writing solver output.
- Documented commute setup by modeling `home` as a normal venue in the existing
  travel-time matrix.
- Added `--print-solution` to print a compact terminal schedule and change
  table for quick review.
- Added `--init-template` to generate a starter CSV input folder using the
  existing schema.
- Added `--validate-only` to load and validate CSV input without solving.
- Added `candidate_count` to solver summaries for scale monitoring.
- Added `shared_session_id` on lessons so couples or other student groups can
  share the same scheduled lesson slot.
- Added optional `--output-ics` calendar export with lesson events, grouped
  shared lessons, transparent coach availability windows, and out-of-window
  lesson warnings.

### Changed

- Organizer calendar rows, shared page typography, right-aligned compact header
  summary, unscheduled tray text, compact import/export toolbar labels, and
  surrounding controls now scale with the viewport so the full daily timeslot
  range is visible without the previous oversized vertical grid.
- `Clean Schedule` now keeps temporary saved solution snapshots available so
  users can compare candidates after clearing the working calendar.
- Added a preference hotzone calendar background that highlights unweighted
  preferred-student demand for each 30-minute slot.
- Calendar overlays now use independent toggles, and selected-student
  preferences render as colored frames that can appear with trainer or hotzone
  backgrounds, including while a lesson is being dragged.
- Selected-student preference frames now merge vertically across contiguous
  same-day slots.
- Drag-time preference frames now update in place so the temporary overlay does
  not interrupt native lesson drag/drop.
- Scheduled Organizer lessons can now be dragged back to the unscheduled tray to
  clear their visible booking time.
- Students rows now support bulk `Make Couple` and `Clear Couple` actions from
  selected rows.
- Removed the Organizer `Reset` button; saved solution snapshots and `Clean
  Schedule` remain available for switching or clearing the working calendar.
- Organizer drag/drop diagnostics now clear stale preference warnings while a
  placement is being re-evaluated and repaint blocks when fresh diagnostics
  return.
- Optimizer results now load into the Lessons page as unsaved draft booking
  times so users can review before persisting them.
- Students `Generate Lessons` now regenerates lesson rows from the current
  student/couple plan with index-specific shared session IDs, creates shared
  rows only up to the smallest group lesson count, defaults generated lessons
  to optional, and clears old bookings.
- Successful web saves and scheduler CSV imports now clear stale
  `solution.json` so old optimizer placements do not reappear after input
  changes.
- Added solver-side request validation for duplicate IDs, missing references,
  invalid planning windows, invalid durations, bad day/time values, unknown
  booking or absence values, and negative travel times.
- Added explicit optional-lesson unscheduled decision variables so
  `cancel_optional_penalty` affects objective behavior.
- Documented the CSV template, validation, and terminal quick-review workflows.
- Expanded the input data format documentation into a test-data preparation
  guide with file order, column definitions, examples, scenario ideas, and
  validation checks.
- Clarified the command-line/no-UI workflow and corrected the limitations
  section to distinguish the local browser organizer from missing production
  service features.
- Clarified that student and venue display names support Traditional Chinese
  when CSV files are saved as UTF-8.

### Fixed

- Organizer drag-and-drop now updates the in-memory Lessons booking time and
  waits for `Save Lessons` before writing `existing_bookings.csv`.
- Made the browser organizer apply current lesson input details to visible
  placements after saving, including venue, student, shared-session, and
  duration changes.
- Fixed calendar block sizing so scheduled lessons fill their occupied
  timeslots without distorting the time column.
- Fixed status changes and student imports that could move scheduled lessons
  back into the unscheduled tray by preserving/clearing dependent state
  consistently.
- Prevented failed web optimizer runs from overwriting the last usable
  `solution.json` with an empty infeasible schedule.
- Added a clearer infeasible-input warning when required shared lessons have no
  common time and venue candidate.
- Updated the browser organizer to resize visible schedule placements to the
  current lesson durations after input saves or resets, avoiding stale
  `solution.json` block lengths after `duration_min` edits.
- Fixed fixed/frozen bookings being forced into the model without first
  checking planning horizon, coach availability, absence conflicts, and
  conflicts with other fixed bookings.
- Made missing travel paths use an explicit large fallback travel time.

### Tested

- Expanded the unit suite to 82 passing tests, covering solver validation,
  fixed-booking conflicts, optional lessons, CLI solution printing, CSV
  template creation, overwrite protection, validation-only mode, web frontend
  parser behavior, CSV save validation, solve output shape, and weekly-grid
  payload data.
- Added backend coverage for venue/travel-time editing and trainer timeslot
  validation.
- Added backend coverage for CSV import success, invalid upload rejection,
  staged import rollback, student-scoped import cleanup, and clean
  Students/Lessons cascades.
- Updated web tests to derive active fixture IDs from the copied CSV data
  instead of hardcoding demo IDs; migration tests keep intentional `1001`
  assertions for old text-ID normalization.

## [0.1.0] - 2026-04-26

### Added

- Initial OR-Tools CP-SAT trainer scheduling MVP.
- Solver dataclasses for configuration, students, venues, travel times, lesson
  requests, preferences, coach availability, absences, existing bookings, and
  solver responses.
- Candidate generation for feasible lesson slots across a configurable planning
  horizon.
- Support for weekly planning and in-week rescheduling modes.
- Student preference and coach availability scoring.
- Absence handling for students, coaches, and venues.
- Existing-booking handling with support for confirmed, draft, completed,
  in-progress, locked, and high-lock-level bookings.
- Freeze-buffer behavior for in-week rescheduling.
- Travel-time feasibility checks between venues.
- Objective weights for lesson priority, keeping original bookings, moving
  confirmed or draft bookings, optional cancellation, and cross-venue penalties.
- CSV command-line runner for loading input data and writing solver output.
- Demo CSV input folder with sample scheduler data.
- JSON solution output and optional debug request output.
- Unit tests covering core MVP scheduling behavior.

### Documented

- CSV input schema and examples.
- GitHub-ready README with setup, quick-start, CSV input overview,
  programmatic usage, and test instructions.

### Known Limitations

- MVP-only API surface; data models and scoring behavior may change.
- No UI, API server, database integration, or deployment layer.
- Limited infeasible-case diagnostics.
- Lightweight validation intended for demo and development workflows.
