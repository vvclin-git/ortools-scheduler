# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Added `lessons_per_week` student planning metadata plus a Students-page
  lesson generation workflow.
- Added automatic web-app startup migration from text IDs to numeric student
  and lesson IDs with timestamped CSV backups.
- Added organizer lesson-block coloring and labels for booking statuses.
- Added Setup preference score mapping for readable student preference levels.
- Added flexible Students timeslot text with weekday ranges, compact times, and
  trainer-timeslot expansion for weekday-only entries.
- Added `last_resort` preference score defaults, blank-preference expansion from
  trainer availability, Organizer `Save Lessons`, and three memory-only
  temporary Organizer solution slots.
- Split the browser organizer into Organizer, Students, Lessons, and Setup
  pages with section-specific save actions.
- Added editable lesson booking day, start, derived end, and status controls
  backed by `existing_bookings.csv`.
- Added browser-local drag-and-drop schedule editing with immediate manual
  schedule evaluation, diagnostics, reset, and placement CSV import/export.
- Added a lesson editor to the local browser organizer, including student and
  venue dropdowns plus shared-session editing.
- Added a read-only `/api/evaluate-schedule` endpoint for scoring manual
  schedules without writing solver output.
- Added `run_web_app.cmd` as a Windows shortcut for launching the local browser
  schedule organizer with the demo CSV input.
- Added a local browser schedule organizer with a standard-library Python web
  server, CSV-backed student availability editing, optimizer execution, and a
  weekly grid output view.
- Added frontend editing for students, venues, venue travel times, and trainer
  timeslots.
- Added frontend CSV import for supported scheduler input files.
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

- Optimizer results now load into the Lessons page as unsaved draft booking
  times so users can review before persisting them.
- Added solver-side request validation for duplicate IDs, missing references,
  invalid planning windows, invalid durations, bad day/time values, unknown
  booking or absence values, and negative travel times.
- Added explicit optional-lesson unscheduled decision variables so
  `cancel_optional_penalty` affects objective behavior.
- Documented the CSV template, validation, and terminal quick-review workflows.
- Expanded the input data format documentation into a test-data preparation
  guide with file order, column definitions, examples, scenario ideas, and
  validation checks.
- Clarified that student and venue display names support Traditional Chinese
  when CSV files are saved as UTF-8.

### Fixed

- Organizer drag-and-drop now updates the in-memory Lessons booking time and
  waits for `Save Lessons` before writing `existing_bookings.csv`.
- Made the browser organizer apply current lesson input details to visible
  placements after saving, including venue, student, shared-session, and
  duration changes.
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

- Expanded the unit suite to 41 tests, covering solver validation,
  fixed-booking conflicts, optional lessons, CLI solution printing, CSV
  template creation, overwrite protection, validation-only mode, web frontend
  parser behavior, CSV save validation, solve output shape, and weekly-grid
  payload data.
- Added backend coverage for venue/travel-time editing and trainer timeslot
  validation.
- Added backend coverage for CSV import success and invalid upload rejection.

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
