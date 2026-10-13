-- Remote repair, second round (after device_doctor.sql and device_offline.sql).
--
-- Run once in the Supabase SQL editor, BEFORE deploying the Caliban commit
-- that offers these actions -- until then the insert is refused by the old
-- check constraint and SGSC says so.
--
-- New actions:
--   send_logs       the last lines of the rig's journals, so a fault can be
--                   read from a desk instead of over Pi Connect
--   test_photo      a flash photo of the chute, uploaded, so "is the tote in
--                   place / the lens clean / the chute where it should be"
--                   can be answered without walking there
--   rollback_code   back to the last version that ran clean, with automatic
--                   updates paused until update_code is pressed
--
-- And two places for what an action brings back: `output` (text: logs) and
-- `attachment` (a storage path in vision-band-test-captures: the photo).

alter table device_commands drop constraint if exists device_commands_action_check;
alter table device_commands add constraint device_commands_action_check check (action in (
    'repair_all', 'restart_loop', 'restart_screen', 'restart_kiosk', 'restore_kiosk',
    'update_code', 'free_disk', 'test_flash', 'reboot',
    'send_logs', 'test_photo', 'rollback_code'));

alter table device_commands add column if not exists output     text;
alter table device_commands add column if not exists attachment text;
