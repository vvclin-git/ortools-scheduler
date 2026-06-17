Project Overview
================

This project is a Python-based OR-Tools scheduling MVP for coach/student lesson assignment.

The project includes:

- A CP-SAT based scheduling solver
- CSV-based data loading and validation
- A local browser-based organizer UI
- iCalendar/export support
- A unittest-based regression test suite

Prioritize correctness, testability, small diffs, and clear separation between solver, CSV workflow, backend, and frontend code.

Repository Map
==============

Use the smallest relevant file set for each task.

- `trainer_solver_mvp.py`

  - Core solver model, constraints, objectives, and result decoding.

- `run_solver_from_csv.py`

  - CSV loading, validation, CLI flow, and conversion into solver input.

- `schedule_web_app.py`

  - Local web organizer backend, import/export flow, save paths, cleanup behavior, and server-side validation.

- `schedule_web_static/`

  - Frontend HTML, JavaScript, and CSS for the local organizer UI.

- `csv_input_schema.md`

  - Source of truth for CSV files, columns, valid values, and data contract.

- `test_trainer_solver_mvp.py`

  - Main unittest suite. Treat tests as executable behavior documentation.

- `README.md`

  - Human-facing setup and usage guide.

- `CHANGELOG.md`

  - Release or milestone-level change history.

- `docs/AI_HANDOFF.md`

  - Current development status and next-thread handoff context.

Startup Workflow
================

At the beginning of a new Codex thread:

1. Read `AGENTS.md`.
2. Read `docs/AI_HANDOFF.md` if it exists.
3. Use the user's latest prompt as the highest-priority instruction.
4. If `docs/AI_HANDOFF.md` conflicts with the user's prompt, follow the user's prompt.
5. If the handoff file appears stale or incomplete, mention the uncertainty before making broad changes.

Do not use `README.md` or `CHANGELOG.md` as progress-tracking documents.

Source-of-Truth Routing
=======================

Use the right source of truth for the task.

- Solver behavior: `trainer_solver_mvp.py` and tests.
- CSV behavior: `run_solver_from_csv.py`, `csv_input_schema.md`, and tests.
- Web backend behavior: `schedule_web_app.py` and tests.
- Frontend behavior: `schedule_web_static/`.
- Current development state: `docs/AI_HANDOFF.md`.
- User-facing usage: `README.md`.
- Release-level history: `CHANGELOG.md`.

Do not infer mutable scheduling rules from `AGENTS.md`. Solver behavior should be defined by code, tests, and explicit user instructions.

Documentation Update Policy
===========================

Do not update `README.md` or `CHANGELOG.md` for every small task.

Update `README.md` only when user-facing setup, usage, CLI flow, web flow, examples, or major limitations change.

Update `CHANGELOG.md` only for milestones, release preparation, user-visible features, breaking changes, or important bug fixes.

Update `csv_input_schema.md` when the CSV data contract changes, including files, columns, valid values, required fields, ID formats, time formats, or validation rules.

Use `docs/AI_HANDOFF.md` for internal progress handoff.

Handoff Update Policy
=====================

When the user asks to summarize the thread, prepare a handoff, wrap up, or continue in a new Codex thread, update:

`docs/AI_HANDOFF.md`

Use overwrite, not append.

Keep the handoff concise and focused on current state, not full history.

Include only:

- Current focus
- Current state
- Files recently changed
- Important decisions
- Tests run
- Known issues
- Next recommended step
- Suggested scope for the next thread

Do not update the handoff file after every small edit unless the user asks.

Architecture Boundaries
=======================

Keep responsibilities separated.

- Solver code should not depend on raw CSV column names.
- CSV loading should normalize and validate data before solver input.
- UI code should not construct OR-Tools constraints directly.
- Frontend code should not become the final authority for scheduling rules.
- Backend validation should remain authoritative for saved/imported data.
- Avoid combining solver, CSV, backend, and frontend refactors in one task unless explicitly requested.

When behavior changes, update or add focused tests.

Scheduling Rule Changes
=======================

Do not treat existing scheduling behavior as casual implementation detail.

When changing solver behavior:

- Identify the existing behavior in code and tests first.
- Make the smallest change needed.
- Add or update regression tests.
- Avoid changing objectives and hard constraints in the same edit unless required.
- Do not weaken validation silently.
- Explain any behavior change in the final summary.

`AGENTS.md` should not be used as the source of truth for detailed scheduling rules.

CSV Schema Change Policy
========================

When changing CSV files, columns, valid values, or validation behavior:

1. Update the loader and validation logic.
2. Update or add tests.
3. Update `csv_input_schema.md`.
4. Update demo or test fixtures only if needed.
5. Update `README.md` only if user-facing workflow changes.

Keep raw CSV parsing separate from solver logic.

Data and ID Safety
==================

Be careful with cross-file references and persisted data.

This may include:

- Student IDs
- Lesson IDs
- Shared session IDs
- Booking IDs
- Venue IDs
- Absence references
- Existing booking references
- Solution data references

Preserve reference consistency across related CSV files and generated solution data.

Do not change migration, cleanup, import, rollback, or save behavior without tests.

Failed imports or failed optimizer runs should not overwrite the last usable data or output unless explicitly intended.

Generated Files and Runtime Artifacts
=====================================

Do not treat generated files as source of truth.

Runtime or generated artifacts may include:

- `solution.json`
- `request_debug.json`
- `schedule.ics`
- `web_app_server.log`
- `web_app_server.err.log`
- `.id_migration_backup_*`
- Temporary browser testing output

Do not update or commit generated artifacts unless the user explicitly asks.

Local Server and UI Testing Policy
==================================

Start a local HTTP server only when needed for UI testing.

If a server is started:

- Record the command, host, and port.
- Reuse an existing appropriate server instead of starting redundant servers.
- Stop the server after testing.
- Before finishing, confirm that the server was stopped.
- If it cannot be stopped, report the remaining process, port, and command.

Do not leave background HTTP servers running unless the user explicitly asks.

Development Guidelines
======================

Prefer small, focused, high-confidence changes.

- Modify only files relevant to the task.
- Avoid unrelated refactors.
- Avoid formatting-only diffs.
- Avoid broad rewrites of large files.
- Preserve existing public behavior unless the task changes it.
- Prefer explicit validation errors over silent fallback behavior.
- Keep functions testable.
- Avoid hidden global state.
- Use clear names for constraint and validation logic.
- Keep code comments in English.
- Do not add dependencies unless clearly justified.

For large files such as `trainer_solver_mvp.py`, `schedule_web_app.py`, or large frontend files, identify the smallest relevant functions before editing.

Testing Guidelines
==================

Use `unittest` unless the project is explicitly migrated to another test framework.

Recommended full test command:

```powershell
uv run python -m unittest test_trainer_solver_mvp.py -v
```

Fallback command:

```powershell
python -m unittest test_trainer_solver_mvp.py -v
```

For focused changes, run the smallest relevant tests first when practical.

When modifying solver, CSV, web backend, frontend, export, cleanup, or migration behavior, add or update focused regression tests.

Do not claim tests passed unless they were actually run.

If tests were not run, clearly state why.

Completion Checklist
====================

Before finishing a coding task, report:

- What changed
- Files modified
- Tests run
- Test results
- Tests not run, if any
- Known risks or limitations
- Follow-up tasks, if any
- Whether docs or handoff files were updated
- Whether any local HTTP server was started and stopped

If the task only produced a plan and did not modify files, say so explicitly.
