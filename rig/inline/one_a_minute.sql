-- Inline rig: one photo a minute, the instruction decided on the last ten
-- minutes pooled (app/main.py INLINE_DEFAULTS "decision_window_min"), instead
-- of a burst of 4-8 every 4 minutes and the median of the last 3 bursts.
--
-- Photos 8 s apart are 70% alike (2026-10-19, 52 live bursts): a burst of 8
-- is worth about 1.5 independent photos. One a minute uses the same flashes
-- and looks at the stream about three times as often.
--
-- decision_min_readings 5: after a start or a confirmed setting, hold until
-- five photos (five minutes) are in. Only with decider "yolo".
--
-- Run AFTER the push that brings decision_window_min has reached Prospero;
-- before it, the window is ignored and the instruction would follow the
-- median of the last 3 single photos.

update system_config
set value = (value::jsonb || '{"interval_min": 1, "group_size": 1, "group_max": 1, "group_target_cover": 0,
                               "decision_window_min": 10, "decision_min_readings": 5}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'interval_min' as interval_min, value::jsonb ->> 'group_size' as group_size,
       value::jsonb ->> 'group_max' as group_max, value::jsonb ->> 'decision_window_min' as window_min,
       value::jsonb ->> 'decision_min_readings' as min_readings
from system_config where key = 'inline_settings';

-- Back to bursts:
-- update system_config set value = (value::jsonb || '{"interval_min": 4, "group_size": 4, "group_max": 8, "group_target_cover": 1.2, "decision_window_min": 0, "decision_min_readings": 2}'::jsonb)::text where key = 'inline_settings';
