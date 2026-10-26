-- Inline rig: no instruction that rests on one photo (app/main.py
-- INLINE_DEFAULTS "decision_drop_one", window_drop_one). Only with decider
-- "yolo" and decision_window_min set (one_a_minute.sql).
--
-- Outside the no-change band, the instruction is decided on the 10-minute
-- window without whichever single photo pulls it furthest out. AUGMENTER
-- then shows only when every photo but one still says so; same for DIMINUER.
-- Replayed on 2026-10-23..26 (1858 live readings): AUGMENTER appearing
-- 116 -> 63, DIMINUER 100 -> 73; one photo of 84.6% had turned a clean
-- window into a 12% AUGMENTER major. A real build-up still shows the same
-- minute. The screen's "≈ X % ME" is that value; the plain pooled one is
-- kept in mesures.fenetre whenever the two differ.
--
-- Run AFTER the push has reached Prospero; before it, the key is ignored.

update system_config
set value = (value::jsonb || '{"decision_drop_one": true}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'decision_drop_one' as drop_one,
       value::jsonb ->> 'decision_window_min' as window_min
from system_config where key = 'inline_settings';

-- Off again:
-- update system_config set value = (value::jsonb || '{"decision_drop_one": false}'::jsonb)::text where key = 'inline_settings';
