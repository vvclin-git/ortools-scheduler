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

See [csv_input_schema.md](csv_input_schema.md) for the full CSV contract,
supported values, and examples.

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

The tests cover candidate generation, absence-driven rescheduling, locked
bookings, confirmed-booking preservation, travel-time conflicts, freeze-buffer
behavior, and infeasible required lessons.

## Current Limitations and Future Work

- This is an MVP solver module, not a full product or service.
- There is no UI, API server, database integration, authentication, or deployment
  layer.
- Input validation is intentionally lightweight and mostly handled by the CSV
  loader and candidate generation path.
- Infeasible-case diagnostics are basic and should be expanded for production
  use.
- Objective scoring is configurable but still simple; real deployments may need
  more domain-specific weights and fairness constraints.
- The public Python API may change as the data model becomes more stable.
