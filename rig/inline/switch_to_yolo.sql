-- Inline rig: let the MEO detector on Ariel decide, instead of the vision
-- prompts (app/main.py INLINE_DEFAULTS "decider"). No vision call is made
-- for a reading once this is set.
--
-- Run ONLY after Ariel's log shows "detector N MEO pieces, X s for N photo(s)"
-- on a reading: until Ariel runs the detector, every reading would be refused
-- ("Détecteur MEO absent sur Ariel") -- ai_fallback is off on purpose.
--
-- yolo_scale: lab ME% = scale x 100 x MEO box area / product pixels,
-- fitted on the lab lots from 10-16 on (3.19, 2026-10-18). Refit after each
-- retrain (training/amas/lot_estimate.py prints it).

update system_config
set value = (value::jsonb || '{"decider": "yolo", "yolo_scale": 3.19, "ai_fallback": false}'::jsonb)::text
where key = 'inline_settings';

select value::jsonb ->> 'decider' as decider, value::jsonb ->> 'yolo_scale' as yolo_scale,
       value::jsonb ->> 'ai_fallback' as ai_fallback
from system_config where key = 'inline_settings';

-- Back to the vision prompts:
-- update system_config set value = (value::jsonb || '{"decider": "ai"}'::jsonb)::text where key = 'inline_settings';
