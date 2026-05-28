# Trainer Scheduler Test Dataset — Hard

## Summary

- Difficulty tier: Hard
- Number of students: 40
- Venue ratio: gym_a:gym_b = 30:10
- Same-last-name couple pairs: 8
- Twice-per-week students: 12
- Blank-preference students: 2
- Broad flexible students: 4
- Narrow demanding students: 20
- Semi-flexible students: 14

## Intended Stress Factors

- Peak-hour-heavy narrow windows.
- High venue concentration at the primary venue.
- Many couples and twice-per-week students.
- Useful for infeasibility diagnostics and manual adjustment workflows.

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
