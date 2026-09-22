-- Add a capture kind for reject-stream photos, separate from a line sample.
--
-- Already run once in the Supabase SQL editor (2026-09-21) -- kept in the
-- repo so the constraint is documented, not because it needs running again.
--
-- A reject photo is never a production sample and must never be eligible for
-- /azure-band-test (the MEO analysis) or end up in vision_band_estimates --
-- that is exactly what caused the false AQ alert on 2026-09-14/15, when two
-- unmarked reject-stream photos got averaged into a lot like ordinary tray
-- samples. The guarantee is structural, not operator discipline:
-- _load_rig_capture in app/main.py only lets kind='capture' through
-- /azure-band-test, so a 'capture_reject' command cannot be analysed even if
-- something calls that endpoint directly with its id. It is allowed through
-- /operator/captures/{id}/image (same as 'capture') so an operator or QC can
-- still review the frame.

alter table capture_commands
    drop constraint if exists capture_commands_kind_check;
alter table capture_commands
    add constraint capture_commands_kind_check
    check (kind in ('capture', 'capture_reject', 'preview',
                    'calib_empty', 'calib_focus', 'calib_filled'));
