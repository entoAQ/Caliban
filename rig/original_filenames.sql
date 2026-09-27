-- Where each capture's full-resolution original lives.
--
-- Run once in the Supabase SQL editor.
--
-- Since 2026-09-27 Supabase Storage holds only a 2048 px review copy of each
-- capture, kept a few days, and the 12MP original stays on the rig in
-- ~/captures until it is moved to SharePoint. The rig names that file
-- {lot}_{YYYYmmdd-HHMMSS}_{band}.jpg, which has nothing in common with the
-- command id, so without these columns the only way back from a capture to
-- its original is guessing by lot and time.
--
-- The filename rather than a full path: the file moves (rig -> SharePoint),
-- its name does not. Null on rows from before this, on calibration stages
-- (no image), and on captures without an IR frame.

alter table capture_commands
    add column if not exists original_filename    text,
    add column if not exists ir_original_filename text;

create index if not exists capture_commands_original_filename_idx
    on capture_commands (original_filename)
    where original_filename is not null;
