-- Inline rig: adaptive handle turns (AQ, 2026-10-23). Caliban app/main.py
-- inline_turn / settling_s, Ariel loop.py ack_runs, screen.html turnText.
--
--   settle_min 5            after a confirmed turn, 5 minutes of photos are not
--                           counted (still the product from before the turn); the
--                           next instruction comes from the 5 photos after that,
--                           about 10 minutes after the turn
--   adaptive_turns true     a first AUGMENTER keeps 2 cm / 4 cm; another right
--                           after a confirmed turn is sized on what that turn did:
--                           (ME% - 5) / (ME% moved per cm), whole cm, 2 to 6
--   turn_gain_per_cm 1.0    the effect expected (about 1 point per cm measured
--                           2026-10-20..23); a turn that did less than half of it
--                           counts as half
--   turn_max_cm 6           largest single turn (Caliban never shows more than
--                           10 cm or a fraction, whatever is set here)
--   turn_run_max_cm 12      anticlockwise cm over AUGMENTER turns in a row; then
--                           "Ne plus tourner la poignée : aviser l'AQ". Also when
--                           ME% is 2 points higher after a turn than before it
--   decrease_pause_min 15   after a DIMINUER, none for 15 minutes
--   decrease_max_in_row 2   after 2 DIMINUER in a row, none until ME% is back at
--                           4 % or above (or 4 hours have passed)
--
-- Run AFTER the push has reached Ariel (loop.py, screen.py, screen.html) and
-- Caliban has redeployed. Off again: set adaptive_turns false and the other
-- values to 0 (settle_min 0, decrease_pause_min 0, decrease_max_in_row 0).

update system_config
set value = (value::jsonb || '{"settle_min": 5, "adaptive_turns": true, "turn_gain_per_cm": 1.0,
                               "turn_max_cm": 6, "turn_run_max_cm": 12,
                               "decrease_pause_min": 15, "decrease_max_in_row": 2}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'settle_min' as settle_min, value::jsonb ->> 'adaptive_turns' as adaptive_turns,
       value::jsonb ->> 'turn_max_cm' as turn_max_cm, value::jsonb ->> 'turn_run_max_cm' as turn_run_max_cm,
       value::jsonb ->> 'decrease_pause_min' as decrease_pause_min,
       value::jsonb ->> 'decrease_max_in_row' as decrease_max_in_row, value::jsonb ->> 'mode' as mode
from system_config where key = 'inline_settings';
