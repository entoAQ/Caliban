-- The structured measures of a measuring prompt (chute-m1: 0-100 score, MEO area share,
-- clumps by size, fines, stream density), kept on each re-score row.
--
-- Run once in the Supabase SQL editor before re-scoring with chute-m1 from
-- validation-band-test.html. Rows from prompts that measure nothing leave it
-- null. One jsonb column rather than one column per measure: the measures are
-- still being tried out, and the next prompt may ask for different ones.
--
-- Shape (Caliban _attach_measures), means over the rotations plus every
-- rotation's own answer:
--   {"score_meo": 22.5, "score_meo_rotations": ["20", "25", "25", "20"],
--    "aire_meo": 4.5, "aire_meo_rotations": [4, 5, 5, 4],
--    "amas_petits": 1.75, "amas_petits_rotations": ["peu", "plusieurs", ...],
--    "amas_moyens": ..., "amas_gros": ..., "flux": 1.0, "flux_rotations": [...]}
-- Clump scores: aucun 0, peu 1, plusieurs 2, beaucoup 3.
-- Fines: aucun 0, peu 1, moyen 2, beaucoup 3.
-- Stream: clairseme 0, moyen 1, dense 2.

alter table vision_rescores add column if not exists mesures jsonb;

-- chute-v1 against chute-m1 on the same photos, with the measures alongside:
--
--   select r.storage_path, r.prompt_version, r.predicted_band, r.estimate_pct,
--          r.mesures->>'score_meo' as score, r.mesures->>'aire_meo' as aire, r.mesures->>'amas_petits' as petits,
--          r.mesures->>'amas_moyens' as moyens, r.mesures->>'amas_gros' as gros,
--          r.mesures->>'fines' as fines, r.mesures->>'flux' as flux
--   from vision_rescores r
--   where r.run_id = '<run id>'
--   order by r.storage_path, r.prompt_version;
