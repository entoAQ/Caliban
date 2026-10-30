-- Inline rig: quarter turns of the crank, sized from what the last one did
-- (AQ, 2026-10-30). Instead of quarter_turns.sql and adaptive_turns.sql.
--
--   under 4 %   DIMINUER   un quart de tour dans le sens horaire
--   4 to 6 %    AUCUN CHANGEMENT
--   6 to 10 %   AUGMENTER  un quart de tour dans le sens antihoraire
--   10 % +      AUGMENTER  un demi-tour dans le sens antihoraire
--
--   settle_min 5            after a confirmed turn, 5 minutes of photos are not
--                           counted; the next instruction comes about 10 minutes
--                           after the turn
--   adaptive_turns true     another AUGMENTER right after a confirmed one is
--                           (ME% - 5) / (ME% moved per quarter by that turn)
--   turn_gain_per_quarter 4 the effect expected; a rough figure (a quarter is about
--                           6 cm at the grip, 0.5 to 1 point per cm), and what the
--                           last turn did counts only within half..double of it
--   turn_max_qt 2           largest turn, a half turn: 12 cm, already 3 times
--                           today's largest. Caliban's own limit is a full turn
--   turn_run_max_qt 8       two full turns in a row, then "aviser l'AQ"; also when
--                           ME% is 2 points higher after a turn than before it
--   decrease_pause_min 15   after a DIMINUER, none for 15 minutes
--   decrease_max_in_row 2   after 2 in a row, none until ME% is back at 4 %
--
-- Before running: ack_aq_notified.sql, the push reaching Ariel and Caliban, and a
-- paint mark on the crank hub and four on the flange (12, 3, 6, 9 o'clock), so a
-- quarter turn is "from one mark to the next".
-- Back to cm: turn_unit "cm", adaptive_turns false, settle_min, decrease_pause_min
-- and decrease_max_in_row 0.

update system_config
set value = (value::jsonb || '{"turn_unit": "quarter", "turn_max_qt": 2, "turn_run_max_qt": 8,
                               "turn_gain_per_quarter": 4, "adaptive_turns": true, "settle_min": 5,
                               "decrease_pause_min": 15, "decrease_max_in_row": 2}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'turn_unit' as turn_unit, value::jsonb ->> 'turn_max_qt' as turn_max_qt,
       value::jsonb ->> 'turn_run_max_qt' as turn_run_max_qt, value::jsonb ->> 'adaptive_turns' as adaptive_turns,
       value::jsonb ->> 'settle_min' as settle_min, value::jsonb ->> 'decrease_pause_min' as decrease_pause_min,
       value::jsonb ->> 'decrease_max_in_row' as decrease_max_in_row, value::jsonb ->> 'mode' as mode
from system_config where key = 'inline_settings';
