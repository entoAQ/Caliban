-- dbo.CameraEntries (plant SQL Server): what Cam1 (Ariel, the inline camera)
-- tells Ignition about ME % in the finished product, and what Ignition did
-- about it on the destoner fan (motor Hz).
-- Run once, by hand, after the Ignition tech has reviewed it.
-- RUN 2026-11-13 (Tim, VS Code): 13 columns, both CHECKs and the index
-- confirmed; CameraEntryId was already the primary key (PK_CameraEntries).
-- Columns are only ever added at the end; none is renamed or removed.
--
-- One row = one request from Cam1, answered once by Ignition, on that row.
--
--   Cam1 INSERTs a row when it wants a change (Step +1 / -1), and a Step 0 row
--   every 5 min otherwise, so Ignition can tell the camera is alive. It never
--   changes a row afterwards. Only one request is open at a time: no new row
--   until Ignition has answered the last one or it has expired (ValidUntilUtc).
--
--   Ignition, every 15 s, reads the newest row for CameraId = 1:
--     IF AppliedTimeUtc IS NULL                 -- not answered yet
--       IF Step <> 0 AND CameraState = 'OK'
--          AND SYSUTCDATETIME() < ValidUntilUtc
--          AND auto mode on, within Hz limits, no interlock
--         move the fan one increment in the Step's direction
--       write AppliedTimeUtc, AppliedStep, HzBefore, HzAfter, IgnitionNote
--     -- every row gets an answer, Step 0 and refusals included
--   Nothing else: Ignition never has to interpret ME %; DecisionMePct is
--   there for data review, not control. Per-photo ME % and photo counts stay
--   on the SGSC side (Supabase), traceable by ReadingId.
--
--   Cam1 reads its own row back. An applied step, or an Hz that differs from the
--   previous answer (manual change, start-up setpoint), restarts its count: it
--   waits for the product to settle and pools fresh photos before asking again.
--
-- Step means the EFFECT wanted, not a motor direction:
--   +1 AUGMENTER  ME % too high  -> separate harder
--   -1 DIMINUER   ME % low, good product going to rejects -> separate less
--    0 no change
-- Which way Hz moves for +1, and how many Hz one increment is, belong to
-- Ignition (they are properties of the new destoner, set from its step tests).

-- Runs as ONE batch, all or nothing: if any check or step fails, nothing is
-- changed. Safe to run from VS Code (mssql extension) or SSMS.
SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- Checks first: the columns below are NOT NULL, which SQL Server only allows
-- on an empty table, and this must not run twice.
IF EXISTS (SELECT 1 FROM dbo.CameraEntries)
    THROW 50001, 'dbo.CameraEntries already has rows: stop and ask before adding NOT NULL columns.', 1;
IF COL_LENGTH('dbo.CameraEntries', 'ReadingId') IS NOT NULL
    THROW 50002, 'ReadingId already exists: this script has already been run.', 1;

ALTER TABLE dbo.CameraEntries ADD
-- Written by Cam1, at INSERT
    ReadingId       nvarchar(32)  NOT NULL,  -- e.g. IL-20261110-134045; same id in Supabase, for tracing
    ReadingTimeUtc  datetime2(0)  NOT NULL,  -- last photo behind this row, UTC
    ValidUntilUtc   datetime2(0)  NOT NULL,  -- act only before this (ReadingTimeUtc + 2 min); after it the row is stale
    CameraState     nvarchar(10)  NOT NULL,  -- OK | NO_PRODUCT (line stopped / too little product) | FAULT (dark photos, flash) -- act only on OK
    Step            smallint      NOT NULL   -- +1 AUGMENTER, -1 DIMINUER, 0 no change (see above)
        CONSTRAINT CK_CameraEntries_Step CHECK (Step IN (-1, 0, 1)),
    DecisionMePct   decimal(5,2)  NULL,      -- ME % the Step is based on (recent photos pooled); NULL while settling

-- Written by Ignition, once per row (UPDATE ... WHERE CameraEntryId = @id AND AppliedTimeUtc IS NULL)
    AppliedTimeUtc  datetime2(0)  NULL,      -- when Ignition answered, UTC; NULL = not answered yet
    AppliedStep     smallint      NULL       -- what it actually did: +1, -1, or 0
        CONSTRAINT CK_CameraEntries_AppliedStep CHECK (AppliedStep IN (-1, 0, 1)),
    HzBefore        decimal(5,2)  NULL,      -- fan setpoint when it read the row
    HzAfter         decimal(5,2)  NULL,      -- fan setpoint after (= HzBefore when AppliedStep = 0)
    IgnitionNote    nvarchar(20)  NULL;      -- why, when AppliedStep <> Step: MANUAL | LIMIT_MIN | LIMIT_MAX | INTERLOCK | STALE | CAMERA_NOT_OK | LINE_STOPPED

-- Ignition's 15 s read is the newest row: TOP 1 ... WHERE CameraId = 1
-- ORDER BY ReadingTimeUtc DESC. Without this it scans the table as rows pile up
-- (~300 rows/day). Cam1 reads back only its own row, by CameraEntryId.
-- (Through EXEC so it compiles after the ALTER above, inside the same batch.)
EXEC('CREATE INDEX IX_CameraEntries_Camera_Time
          ON dbo.CameraEntries (CameraId, ReadingTimeUtc DESC);');

-- Existing columns: CameraEntryId uniqueidentifier = a UUID Cam1 derives from
-- ReadingId (a retried INSERT hits the same key, so never a duplicate row);
-- CameraId int = 1 (Cam1 / Ariel). Kept so more cameras can share the table:
-- each gets its own CameraId, and Ignition reads the newest row per camera.
-- "Never a duplicate" needs CameraEntryId to be unique. If IT didn't make it
-- the primary key (or otherwise unique), make it unique here.
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes i
    JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    WHERE i.object_id = OBJECT_ID('dbo.CameraEntries')
      AND i.is_unique = 1
      AND COL_NAME(ic.object_id, ic.column_id) = 'CameraEntryId'
      AND (SELECT COUNT(*) FROM sys.index_columns c2
           WHERE c2.object_id = i.object_id AND c2.index_id = i.index_id AND c2.key_ordinal > 0) = 1
)
    CREATE UNIQUE INDEX UX_CameraEntries_CameraEntryId ON dbo.CameraEntries (CameraEntryId);

COMMIT TRANSACTION;

-- Check afterwards (should list the 12 columns):
--   SELECT name, TYPE_NAME(user_type_id) AS type, max_length, is_nullable
--   FROM sys.columns WHERE object_id = OBJECT_ID('dbo.CameraEntries') ORDER BY column_id;
--
-- Times are UTC throughout (no daylight-saving jumps); Ignition converts for display.
