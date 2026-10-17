-- Measures and burst photos on inline readings.
--
-- Run once in the Supabase SQL editor. A group prompt (chute-g1) judges a
-- burst of photos as one sample and answers counts and a 0-100 score beside
-- its band; this keeps them on the reading, with the storage paths of every
-- photo in the burst (mesures.groupe). Same shape as vision_rescores.mesures
-- (rig/rescore_mesures.sql). Caliban saves readings without it until it exists.

alter table vision_band_estimates add column if not exists mesures jsonb;
