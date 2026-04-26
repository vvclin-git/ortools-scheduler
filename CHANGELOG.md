# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Added `--print-solution` to print a compact terminal schedule and change
  table for quick review.
- Added `--init-template` to generate a starter CSV input folder using the
  existing schema.
- Added `--validate-only` to load and validate CSV input without solving.
- Added `candidate_count` to solver summaries for scale monitoring.

### Changed

- Added solver-side request validation for duplicate IDs, missing references,
  invalid planning windows, invalid durations, bad day/time values, unknown
  booking or absence values, and negative travel times.
- Added explicit optional-lesson unscheduled decision variables so
  `cancel_optional_penalty` affects objective behavior.
- Documented the CSV template, validation, and terminal quick-review workflows.

### Fixed

- Fixed fixed/frozen bookings being forced into the model without first
  checking planning horizon, coach availability, absence conflicts, and
  conflicts with other fixed bookings.
- Made missing travel paths use an explicit large fallback travel time.

### Tested

- Expanded the unit suite from 7 to 21 tests, covering solver validation,
  fixed-booking conflicts, optional lessons, CLI solution printing, CSV
  template creation, overwrite protection, and validation-only mode.

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
