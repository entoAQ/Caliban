-- Inline rig: the scale for MEO area counted once where boxes overlap
-- (rig/inline/meo_detect.py measure(), version "...+union", 2026-10-26).
--
-- Summed boxes read 16.1% more MEO area than the boxes cover (1121 readings,
-- 2026-10-24 18:40 .. 10-26, the rebuilt box), so 2.37 x 1.161 = 2.75 keeps
-- the average reading where it was while clumpy photos stop counting one
-- clump two or three times. A stopgap until tonight's retrain fits its own
-- scale (lot_estimate.py already measures the covered area).
--
-- Run once Ariel is on the new code: the readings' prompt_version ends in
-- "+union" (Ariel pulls within 5 minutes of the push).

update system_config
set value = (value::jsonb || '{"yolo_scale": 2.75}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'yolo_scale' as yolo_scale,
       (select prompt_version from vision_band_estimates where source = 'inline'
        order by created_at desc limit 1) as latest_version
from system_config where key = 'inline_settings';

-- Back (with Ariel's previous code, summed boxes):
-- update system_config set value = (value::jsonb || '{"yolo_scale": 2.37}'::jsonb)::text where key = 'inline_settings';
