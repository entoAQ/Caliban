-- Inline rig: the destoner screen's thresholds for the handle instructions
-- (AQ, 2026-10-20). With the screen (rig/inline/screen.html INSTRUCTIONS):
--
--   under 4 %   DIMINUER   handle 2 cm clockwise
--   4 to 6 %    AUCUN CHANGEMENT
--   6 to 10 %   AUGMENTER  handle 2 cm anti-clockwise
--   10 % +      AUGMENTER  handle 4 cm anti-clockwise
--
-- Values in lab ME% (the detector's pooled estimate x yolo_scale). alert_at
-- (AQ told) is left as it is.
--
-- Run AFTER the push that brings the new screen has reached Ariel; before it,
-- the old screen shows the new thresholds with the old wording.

update system_config
set value = (value::jsonb || '{"decrease_below": 4, "increase_at": 6, "increase_medium_at": 10}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'decrease_below' as decrease_below, value::jsonb ->> 'increase_at' as increase_at,
       value::jsonb ->> 'increase_medium_at' as increase_medium_at, value::jsonb ->> 'alert_at' as alert_at,
       value::jsonb ->> 'mode' as mode
from system_config where key = 'inline_settings';
