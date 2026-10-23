-- Inline rig: handle turns in quarter turns of the crank (AQ, 2026-10-24).
-- Caliban app/main.py TURN_UNITS / inline_turn, screen.html turnText.
--
--   under 4 %   DIMINUER   un quart de tour dans le sens horaire
--   4 to 6 %    AUCUN CHANGEMENT
--   6 to 10 %   AUGMENTER  un quart de tour dans le sens antihoraire
--   10 % +      AUGMENTER  un demi-tour dans le sens antihoraire
--
-- Whole quarter turns only; never more than one full turn at a time
-- (turn_max_qt 4, hard limit in Caliban), and after two full turns in a row
-- (turn_run_max_qt 8) the screen says « Ne plus tourner la poignée : aviser
-- l'AQ ». turn_gain_per_quarter stays 0 until what a quarter turn does to ME%
-- has been measured: the turns stay the fixed ones above.
--
-- Before running: a paint mark on the crank hub and four on the flange
-- (12, 3, 6, 9 o'clock), so a quarter turn is "from one mark to the next".
-- Back to cm: set turn_unit to "cm".

update system_config
set value = (value::jsonb || '{"turn_unit": "quarter", "turn_max_qt": 4, "turn_run_max_qt": 8,
                               "turn_gain_per_quarter": 0}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'turn_unit' as turn_unit, value::jsonb ->> 'turn_max_qt' as turn_max_qt,
       value::jsonb ->> 'turn_run_max_qt' as turn_run_max_qt, value::jsonb ->> 'adaptive_turns' as adaptive_turns,
       value::jsonb ->> 'mode' as mode
from system_config where key = 'inline_settings';
