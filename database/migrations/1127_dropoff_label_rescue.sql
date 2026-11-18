-- 1127_dropoff_label_rescue.sql — RE-PRINTING THE BARCODE A DROP-OFF LABEL LOST (index §STOREOPS-12)
--
-- OWNER, verbatim 2026-11-18: "sometimes the barcode, which is used to consolidate the packages, do
-- not work properly, and we have packages sitting in the store which are not scanned ... the user
-- should be able to upload the picture to our platform and the platform should generate a barcode
-- using the reference numbers this way, the barcodes can then be scanned back into the UPS system".
--
-- WHAT WAS MEASURED BEFORE WRITING THIS. Four photographs of real stranded packages (Amazon "RED"
-- return labels, dropped off 13 Nov 2026 at the Brooklyn store) were decoded here, bar by bar, from
-- the photographs themselves. Every one is Code 128 start code B carrying EXACTLY the reference
-- printed beneath it, and every one matches the 12-character shape in the store's own iShip
-- "Enter or scan RMA to select" list. The symbology is therefore a measured fact, not an assumption,
-- and it lives in code (app/modules/core/code128.py) because it is a published standard.
--
-- WHAT IS *NOT* A HOUSE FACT, and is why the first table exists: WHICH labels a tenant handles and
-- what their references look like. "Amazon prints red, green and blue labels and its reference looks
-- like D·······RRMA" is one tenant's description of one vendor's paperwork. CLAUDE.md, owner
-- directive 2026-10-30: a vocabulary a tenant declares is a REQUIRED argument, never a default with
-- another tenant's words behind it. So:
--
--   · This migration SEEDS NOTHING -- not even for the tenant that asked. Every organisation
--     declares its own label types on the screen, and an organisation that has declared none is
--     TOLD it has declared none rather than silently handed somebody else's patterns.
--   · Reading the BARS needs no declaration at all (Code 128 carries its own check symbol), so a
--     tenant with an empty table can still rescue a package whose barcode is merely too faint for a
--     hand scanner. Only reading the printed TEXT needs the tenant to have described its labels.
--
-- WHY THE SECOND TABLE. The reference that gets re-printed is the one a PERSON confirmed, which may
-- differ from what the machine read. Recording both -- with which of the two was proposed, and by
-- whom -- is what makes a later "this package went back to the wrong vendor" answerable instead of a
-- mystery. It is an audit trail, not a queue: nothing in the platform decides anything from it.
--
-- Idempotent and additive. No figure in any report moves: nothing reads these tables but the
-- drop-off rescue screen itself.
--
-- REVERT:
--   drop table if exists storeops.dropoff_label_scan;
--   drop table if exists storeops.dropoff_label_profile;

create schema if not exists storeops;

-- ── The label types an organisation declares ────────────────────────────────────────────────────
create table if not exists storeops.dropoff_label_profile (
    id                 uuid primary key default gen_random_uuid(),
    org_id             uuid not null,
    label_name         text not null,          -- the tenant's own words, e.g. 'Amazon return (RED)'
    reference_pattern  text not null,          -- a regular expression, anchored by the reader
    symbology          text not null default 'code128b',
    notes              text,
    active             boolean not null default true,
    created_at         timestamptz not null default now(),
    created_by         text
);

-- One name per organisation: two rows called the same thing would make "which declaration matched"
-- unanswerable on the audit row below.
create unique index if not exists dropoff_label_profile_org_name_uq
    on storeops.dropoff_label_profile (org_id, lower(label_name));

create index if not exists dropoff_label_profile_org_idx
    on storeops.dropoff_label_profile (org_id) where active;

-- ── What was read, what was confirmed, and by whom ──────────────────────────────────────────────
create table if not exists storeops.dropoff_label_scan (
    id                   uuid primary key default gen_random_uuid(),
    org_id               uuid not null,
    store_code           text,
    read_source          text,                 -- 'barcode' (decoded, self-verifying) | 'text' | null
    read_reference       text,                 -- what the platform proposed, null when it read nothing
    confirmed_reference  text not null,        -- what the person accepted; this is what was printed
    corrected            boolean not null default false,
    label_name           text,
    profile_id           uuid,
    photo_name           text,
    read_by              text,
    created_at           timestamptz not null default now()
);

create index if not exists dropoff_label_scan_org_created_idx
    on storeops.dropoff_label_scan (org_id, created_at desc);

create index if not exists dropoff_label_scan_org_reference_idx
    on storeops.dropoff_label_scan (org_id, confirmed_reference);

-- CORE-13: a migration grants what it creates. service_role only -- the browser never reads these
-- tables directly; every read goes through the API, which scopes by org.
grant select, insert, update, delete on storeops.dropoff_label_profile to service_role;
grant select, insert, update, delete on storeops.dropoff_label_scan    to service_role;
