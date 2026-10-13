-- Caliban notices when a device stops reporting (after device_doctor.sql).
--
-- Run once in the Supabase SQL editor, before deploying the Caliban commit
-- that reads it.
--
-- offline_since is set when Caliban's watcher sees no report for 10 minutes,
-- and cleared by the device's next report. Kept in the table rather than in
-- Caliban's memory because Caliban runs two worker processes, each with its
-- own watcher: setting it is a conditional UPDATE (only where it is still
-- null), so exactly one worker wins the outage and exactly one alert goes
-- out -- and likewise one "back online" when it is cleared.

alter table device_status add column if not exists offline_since timestamptz;
