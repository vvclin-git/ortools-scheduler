# Trainer Scheduler Test Dataset — Medium

## Summary

- Difficulty tier: Medium
- Number of students: 40
- Venue ratio: gym_a:gym_b = 26:14
- Same-last-name couple pairs: 6
- Twice-per-week students: 8
- Blank-preference students: 5
- Broad flexible students: 7
- Narrow demanding students: 11
- Semi-flexible students: 17

## Intended Stress Factors

- Realistic evening demand.
- Mix of clean and imperfect couple overlaps.
- Multiple twice-per-week students need more than one viable slot.
- Medium venue and travel pressure.

## Files

- `students.csv`: scheduler-compatible student records.
- `preferences.csv`: normalized solver-compatible preference windows.
- `student_preference_source.csv`: app-facing fuzzy preference strings.
- `README.md`: this dataset note.

## Notes

- No lesson rows are generated in this dataset.
- Couples are indicated only by shared last names and intentionally overlapping preferences.
- When lessons are generated later, same-last-name pairs can be assigned matching `shared_session_id` values.
- Blank preference strings are intentional and expand from `coach_availability.csv`.
- Valid venue IDs were read from the uploaded `venues.csv`; this dataset uses `gym_a` and `gym_b` only.
- Preference scores used: preferred=100, acceptable=60, last_resort=20.
