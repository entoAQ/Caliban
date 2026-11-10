-- Inline rig: a photo every 20 s all the time (Tim, 2026-11-10), after two
-- days at 15 s (fifteen_seconds.sql).
--
-- Why: at 15 s the flash began missing -- retries rose from ~0 at one a minute
-- to about 1 photo in 9 by 2026-11-10, every one checked a "no flash" with the
-- trigger on time. 20 s is a quarter fewer firings while the supply is looked
-- at (1000-2200 uF capacitor at the flash end, dummy-cell contacts).
--
-- What 15 s gave, 10-minute windows at matched ME% levels (2026-10-30..11-10):
-- noise +-0.30-0.34 at 15 s against +-0.50-0.71 at 30 s and +-0.76-0.87 at 60 s.
-- Overstated: the 15 s days are all on meo-nodust-20261107, and photos 15 s
-- apart were 27% alike once the drift is taken out (20% at 30 s), which the
-- split-half measure undercounts. 20 s keeps most of it: 30 photos a window
-- instead of 40.
--
-- decision_min_readings 8: a decision after a turn then rests on 2 min 40 s of
-- photos, as 10 at 15 s (2.5 min) did. The photo grid (loop.py SLOT_GRACE_S)
-- follows interval_s, so 20 s comes out at exactly 20 s.

update system_config
set value = (value::jsonb || '{"interval_s": 20, "interval_always": true, "decision_min_readings": 8}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'interval_s' as interval_s, value::jsonb ->> 'interval_always' as always,
       value::jsonb ->> 'decision_window_min' as window_min, value::jsonb ->> 'decision_min_readings' as min_photos,
       value::jsonb ->> 'settle_min' as settle_min
from system_config where key = 'inline_settings';

-- Back to 15 s: fifteen_seconds.sql. Back to 30 s during a correction and one a
-- minute otherwise: interval_s 30, interval_always false, decision_min_readings 5.
