-- Ariel: « Vérifier la caméra » (check_camera) from SGSC's diagnostic page.
--
-- Run once in the Supabase SQL editor; safe to run again. The action list in
-- this constraint mirrors app/main.py's DOCTOR_DEVICES and doctor.py's FIXES:
-- until this runs, Caliban offers the button but the insert is refused.
--
-- check_camera runs rig/inline/camcheck.py on the Pi (camera listed, kernel
-- link errors, frame timing in the capture mode, how late the flash went
-- out) and returns its report in device_commands.output. Added for the flash
-- timing fault (FlashTiming) coming back, 2026-10-23.

alter table device_commands drop constraint if exists device_commands_action_check;
alter table device_commands add constraint device_commands_action_check check (action in (
    'repair_all', 'update_code', 'rollback_code', 'test_photo', 'send_logs', 'reboot',
    -- Ariel only
    'restart_loop', 'restart_screen', 'restart_kiosk', 'restore_kiosk', 'free_disk', 'test_flash',
    'check_camera',
    -- tray rig only
    'restart_poller'));
