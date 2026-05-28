# Trainer Scheduler Test Dataset — Easy

## Summary

- Difficulty tier: Easy
- Number of students: 40
- Venue ratio: gym_a:gym_b = 24:16
- Same-last-name couple pairs: 4
- Twice-per-week students: 4
- Blank-preference students: 8
- Broad flexible students: 12
- Narrow demanding students: 4
- Semi-flexible students: 16

## Intended Stress Factors

- Mostly broad or blank preference windows.
- Low evening concentration.
- Couples have broad overlapping windows.
- Designed as parser/UI/happy-path data.

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
