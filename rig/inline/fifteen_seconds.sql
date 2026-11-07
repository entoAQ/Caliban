-- Inline rig: a photo every 15 s all the time, as a trial (Tim, 2026-11-07).
--
-- Why: on 2026-10-30..11-07, photos 30 s apart were as good as independent
-- (correlation 0.13 once the slow drift is taken out, against 0.10 at 60 s),
-- and at the same ME% level a 10-minute window at 30 s was about 0.7x as noisy
-- as at 60 s -- what independent photos give. Photos 8 s apart were 70% alike
-- on 2026-10-19 (old box), so somewhere between 30 s and 8 s extra photos stop
-- helping. This trial measures 15 s. The flash is on mains (2026-11-01), and
-- its firings are now counted per day (loop.py count_flash).
--
-- decision_min_readings goes from 5 to 10 so a decision still rests on about
-- the same stretch of product: 10 photos at 15 s is 2.5 minutes, as 5 at 30 s
-- was during a correction. The 10-minute window then pools about 40 photos
-- instead of 10, so instructions should flip less on the same product.
--
-- To judge it (compare with 2026-10-30..11-07 at matched ME% levels): the noise
-- of 5- and 10-minute windows, photo-to-photo correlation at 15 s with the slow
-- drift taken out, and the flash tally (retries, failures, gain) for the day.
-- About one production day is enough. Storage: about 3,600 photos a day at
-- ~0.36 MB, ~1.3 GB a day while it runs.
--
-- Run after the push has reached Caliban and Ariel (both read interval_always).

update system_config
set value = (value::jsonb || '{"interval_s": 15, "interval_always": true, "decision_min_readings": 10}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'interval_s' as interval_s, value::jsonb ->> 'interval_always' as always,
       value::jsonb ->> 'interval_min' as interval_min, value::jsonb ->> 'decision_window_min' as window_min,
       value::jsonb ->> 'decision_min_readings' as min_photos, value::jsonb ->> 'settle_min' as settle_min
from system_config where key = 'inline_settings';

-- Back to how it was (30 s during a correction, one a minute otherwise):
--
-- update system_config
-- set value = (value::jsonb || '{"interval_s": 30, "interval_always": false, "decision_min_readings": 5}'::jsonb)::text
-- where key = 'inline_settings';
