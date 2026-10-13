-- Inline rig: mark every reading taken before it was mounted on the chute.
--
-- Run once in the Supabase SQL editor. Before 2026-10-13 08:30 (America/
-- Toronto) the inline rig was on the bench: photographing a tray of larvae
-- on the lab counter, first through a Camera Module 3 Wide NoIR (no infrared
-- filter, pink colours, low detail) and that morning through the HQ camera
-- on the same tray. None of it is product going down the chute, and none of
-- it was judged with a chute prompt. Marked, not deleted: the rows and photos
-- stay for reference, and every view that skips excluded_reason ignores them.
--
-- Run the SELECT first and check it lists only bench readings.

select to_char(created_at at time zone 'America/Toronto', 'YYYY-MM-DD HH24:MI') as heure,
       lot_number_text as lecture, predicted_band, operator_instruction, excluded_reason
from vision_band_estimates
where source = 'inline'
  and created_at < timestamptz '2026-10-13 08:30 America/Toronto'
order by created_at;

update vision_band_estimates
set excluded_reason = 'Banc d''essai : avant installation sur la chute (plateau au labo, '
                      || 'caméra Module 3 Wide NoIR puis HQ) -- pas une lecture de produit'
where source = 'inline'
  and created_at < timestamptz '2026-10-13 08:30 America/Toronto'
  and excluded_reason is null;
