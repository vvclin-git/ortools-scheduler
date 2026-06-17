AI Handoff Notes
================

Current Focus
-------------

Set up repository-level Codex guidance and a concise handoff note for future Codex threads. No solver, CSV workflow, backend, frontend, or runtime behavior changes were made in this thread.

Current State
-------------

- Solver:
  - Status: Not changed in this thread.
  - Notes: Treat `trainer_solver_mvp.py` and `test_trainer_solver_mvp.py` as the source of truth for solver behavior.

- CSV workflow:
  - Status: Not changed in this thread.
  - Notes: Treat `run_solver_from_csv.py`, `csv_input_schema.md`, and tests as the source of truth for CSV behavior.

- Web organizer:
  - Status: Not changed in this thread.
  - Notes: Treat `schedule_web_app.py` and tests as the source of truth for backend import/export, save, cleanup, and validation behavior.

- Frontend UI:
  - Status: Not changed in this thread.
  - Notes: Treat `schedule_web_static/` as the source of truth for local organizer UI behavior.

- Tests:
  - Status: Not run.
  - Notes: Only documentation files were added.

Recently Changed Files
----------------------

| File | Change Summary | Notes |
| --- | --- | --- |
| `AGENTS.md` | Added repository guidance for Codex threads, source-of-truth routing, documentation policy, handoff policy, architecture boundaries, generated-file policy, server policy, development guidelines, testing guidelines, and completion checklist. | New file. Documentation-only change. |
| `docs/AI_HANDOFF.md` | Added current handoff note using the user's requested template structure. | New file. Created because `docs/` and the handoff file did not previously exist. |

Important Decisions
-------------------

- Future Codex threads should read `AGENTS.md` first, then `docs/AI_HANDOFF.md` if it exists.
- `README.md` and `CHANGELOG.md` should not be used as progress-tracking documents.
- Solver, CSV workflow, backend, and frontend responsibilities should stay separated.
- Backend validation remains authoritative for saved/imported data.
- Generated runtime artifacts should not be treated as source of truth or committed unless explicitly requested.

Tests Run
---------

No tests were run.

Result:

- Not run.
- Notes: The changes were documentation-only and did not modify executable behavior.

Known Issues
------------

No active issues were identified in this thread.

Next Recommended Step
---------------------

For the next coding task, read `AGENTS.md` and this handoff first, then inspect only the smallest relevant files for the user's requested change before editing.

Suggested Scope for Next Thread
-------------------------------

Modify only files directly required by the next user request.

Avoid modifying:

- `README.md` unless user-facing setup, usage, CLI flow, web flow, examples, or major limitations change.
- `CHANGELOG.md` unless preparing a release, milestone note, user-visible feature, breaking change, or important bug fix.
- Generated output files.
- Unrelated solver, backend, frontend, or CSV workflow files.

Documentation Status
--------------------

- `README.md` updated:
  - No.
  - Notes: Not needed for this documentation setup task.

- `CHANGELOG.md` updated:
  - No.
  - Notes: Not needed for this documentation setup task.

- `csv_input_schema.md` updated:
  - No.
  - Notes: No CSV data contract changes were made.

- `docs/AI_HANDOFF.md` updated by overwrite:
  - Yes.
  - Notes: Initial handoff file created at the path defined in `AGENTS.md`.

Local Server Status
-------------------

No local HTTP server was started.

- Command used: Not applicable.
- Host and port: Not applicable.
- Server stopped:
  - Not applicable.
- Notes: No UI testing was needed.

Handoff Prompt for Next Codex Thread
------------------------------------

Paste this into the next Codex thread if needed.

Read AGENTS.md first.
Then read docs/AI_HANDOFF.md.

Goal:
[one specific task]

Current context:
Repository guidance has been added in AGENTS.md. The current handoff exists at docs/AI_HANDOFF.md. No runtime behavior has been changed in the current setup thread.

Scope:
Modify only:
- [file 1]
- [file 2]

Avoid modifying:
- README.md
- CHANGELOG.md
- generated files
- unrelated modules

Before coding:
Briefly restate the plan.

After coding:
Summarize changed files, tests run, test results, risks, and whether any local server was started and stopped.
