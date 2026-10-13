-- Remote repair for both rigs: everything since device_doctor.sql, in one go.
--
-- Run once in the Supabase SQL editor (after rig/inline/device_doctor.sql,
-- which created the tables). Every statement is safe to run again. Replaces
-- rig/inline/device_commands_v2.sql, which was never run.
--
-- 1. offline_since (also in rig/inline/device_offline.sql -- harmless twice):
--    set by Caliban's watcher when a rig stops reporting, cleared when it
--    comes back; a conditional UPDATE, so its two workers send one alert.
--
-- 2. The repairs SGSC may ask for, for either rig. Ariel's, plus the tray
--    rig's restart_poller, plus for both:
--      send_logs      the rig's recent journals, read from a desk
--      test_photo     a photo from the rig's camera, shown in SGSC
--      rollback_code  back to the last version that ran clean, updates paused
--
-- 3. Where a repair's answer goes: `output` (text: logs) and `attachment`
--    (a path in the vision-band-test-captures bucket: the photo).
--
-- Rows are told apart by `device` ('ariel', 'prosperos'), which Caliban sets
-- from the key the rig reported with -- never from the request.

alter table device_status add column if not exists offline_since timestamptz;

alter table device_commands drop constraint if exists device_commands_action_check;
alter table device_commands add constraint device_commands_action_check check (action in (
    'repair_all', 'update_code', 'rollback_code', 'test_photo', 'send_logs', 'reboot',
    -- Ariel only
    'restart_loop', 'restart_screen', 'restart_kiosk', 'restore_kiosk', 'free_disk', 'test_flash',
    -- tray rig only
    'restart_poller'));

alter table device_commands add column if not exists output     text;
alter table device_commands add column if not exists attachment text;
