-- Inline rig: mark the detector readings taken on an empty chute.
--
-- Run once in the Supabase SQL editor. The detector's ME% divides MEO box
-- area by product pixels, so a photo with almost no product reads nonsense:
-- IL-20261023-082444 read 3909 % from 39 product pixels and five false boxes
-- on bare chute (the flash had just been moved, so the empty-chute reference
-- no longer matched and let the photo through). Two earlier ones (2026-10-20)
-- had under 10 000 px too and read 0 %. Running flow is 240k-380k px.
-- From this commit on, the rig skips such photos (flashcam min_product) and
-- Caliban refuses them (YOLO_MIN_PRODUCT_PX); these predate that.
--
-- Marked, not deleted. Run the SELECT first: it should list exactly three.

select to_char(created_at at time zone 'America/Toronto', 'YYYY-MM-DD HH24:MI') as heure,
       lot_number_text as lecture, estimate_pct, mesures->'yolo'->>'product_px' as product_px, excluded_reason
from vision_band_estimates
where source = 'inline'
  and (mesures->'yolo'->>'product_px')::numeric < 10000
order by created_at;

update vision_band_estimates
set excluded_reason = 'Chute vide : moins de 10 000 pixels de produit, ME% du détecteur sans signification'
where source = 'inline'
  and (mesures->'yolo'->>'product_px')::numeric < 10000
  and excluded_reason is null;
