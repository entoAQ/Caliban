-- Remote diagnostic and repair for the inline rig (Pi `Ariel`, rig/inline/doctor.py).
--
-- Run once in the Supabase SQL editor, before deploying the Caliban commit
-- that adds /inline/doctor/report and /admin/devices.
--
-- Same shape as capture_commands, for the same reason: the Pi is behind NAT
-- with no inward path, so a repair asked for from SGSC waits here until the
-- Pi's next report (every minute) picks it up. The report itself is the poll
-- -- no second loop on the Pi.
--
-- Only Caliban touches these (service role, which bypasses RLS). RLS is on
-- with no policies, so the anon key the browser holds can read neither.

-- The latest checks from each device, one row per device, overwritten every
-- minute. History is not kept here: what a device looked like an hour ago is
-- in its journal, and a minute-by-minute jsonb history would be most of this
-- project's row count within a month.
create table if not exists device_status (
    device      text primary key,
    report      jsonb not null,
    reported_at timestamptz not null default now()
);

create table if not exists device_commands (
    id           bigint generated always as identity primary key,
    device       text not null,
    -- Whitelisted twice: here, and again on the Pi (doctor.py FIXES), which
    -- runs nothing it does not know by name.
    action       text not null check (action in (
                     'repair_all', 'restart_loop', 'restart_screen', 'restart_kiosk',
                     'restore_kiosk', 'update_code', 'free_disk', 'test_flash', 'reboot')),
    requested_by text,
    requested_at timestamptz not null default now(),
    -- Handed to the device in a report reply. A request not handed over
    -- within 10 minutes is never sent (see /inline/doctor/report): a reboot
    -- asked for during a Wi-Fi outage must not fire an hour later, when the
    -- outage is over and someone may be mid-calibration.
    sent_at      timestamptz,
    done_at      timestamptz,
    ok           boolean,
    detail       text
);

create index if not exists device_commands_pending
    on device_commands (device, requested_at) where sent_at is null;

alter table device_status   enable row level security;
alter table device_commands enable row level security;
