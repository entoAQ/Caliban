-- Numeric larvae-area estimate for reject-stream readings (reject-v6).
--
-- Run once in the Supabase SQL editor BEFORE deploying the reject-v6 prompt:
-- _run_reject_vision inserts larves_pct with every reading, and an insert
-- naming a column that doesn't exist yet fails outright, losing the reading.
--
-- The model's own estimate of the share of the tray's occupied area that is
-- larvae, 0-100, rounded to the nearest 5. A vision-model guess, not a
-- measurement -- expect +/-10-15 points; the categorical LARVES column and
-- the DECISION still come from the same answer and are unchanged by this.
-- Null on readings from reject-v5 and earlier.

alter table reject_stream_readings
    add column if not exists larves_pct numeric;
