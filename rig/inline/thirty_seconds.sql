-- Inline rig: a photo every 30 s instead of every minute (AQ, 2026-10-30).
--
-- The first decision after a confirmed turn comes about 7.5 minutes after it (the
-- 5-minute wait, then 5 photos) instead of about 10.5, and the 10-minute average
-- holds about 20 photos instead of 9, so one wild photo counts for about half.
-- settle_min stays 5 for now: a trial, to judge what a 3-minute wait would do.
--
-- The flash fires twice as often. Photos 8 s apart were 70% alike on 2026-10-19
-- and photos a minute apart were not; 30 s is in between and has not been
-- measured, so check before relying on 5 photos as much as on 5 a minute apart.
--
-- Run after the push has reached Caliban and Ariel. Back to one a minute:
-- set interval_s to 0.

update system_config
set value = (value::jsonb || '{"interval_s": 30}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'interval_s' as interval_s, value::jsonb ->> 'interval_min' as interval_min,
       value::jsonb ->> 'decision_window_min' as window_min, value::jsonb ->> 'decision_min_readings' as min_photos,
       value::jsonb ->> 'settle_min' as settle_min, value::jsonb ->> 'mode' as mode
from system_config where key = 'inline_settings';
