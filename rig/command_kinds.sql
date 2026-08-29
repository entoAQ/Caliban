-- Let the queue carry work other than "photograph this lot".
--
-- Run once in the Supabase SQL editor, after capture_commands.sql.
--
-- The queue already solves the hard problem: the Pi sits behind NAT with no
-- inward path, so it polls outward for work. Framing previews and calibration
-- need exactly the same thing, and inventing a second mechanism for them would
-- mean a second thing to keep working.
--
-- Calibration is the interesting case. It is interactive at the bench -- three
-- stages with a tray swap between each -- and a queue cannot block waiting for
-- someone to change a tray. So it arrives as three separate commands, one per
-- stage, with the operator swapping trays between them. The browser does the
-- waiting, which is the one thing a browser is good at.

alter table capture_commands
    -- What to do. 'capture' is what every existing row is, and what the
    -- estimate button still sends.
    --
    -- 'preview' shoots and uploads a frame without analysing it. That is the
    -- part that makes remote calibration possible at all: without it the
    -- operator is lining up a tray by walking back to a terminal, and the
    -- whole point is not having to.
    add column if not exists kind text not null default 'capture'
        check (kind in ('capture', 'preview',
                        'calib_empty', 'calib_focus', 'calib_filled')),

    -- Whatever the stage measured: analogue gain, colour spread, lens
    -- position, tilt. Structured rather than a log line, because the browser
    -- has to decide whether the stage passed and show the operator why not.
    add column if not exists result jsonb;

-- lot_number is meaningless for a preview or a calibration stage, and forcing
-- a placeholder into it would put fictional lot numbers in the table for the
-- sake of a constraint.
alter table capture_commands
    alter column lot_number drop not null;

-- Required for a capture, and only for a capture.
alter table capture_commands
    drop constraint if exists capture_commands_lot_number_required;
alter table capture_commands
    add constraint capture_commands_lot_number_required
    check (kind <> 'capture' or lot_number is not null);
