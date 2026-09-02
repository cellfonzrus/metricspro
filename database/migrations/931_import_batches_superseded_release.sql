-- 931_import_batches_superseded_release.sql — 'superseded' must RELEASE the duplicate-file hash
-- core band · follows 930. Additive + idempotent + safe to re-run.
--
-- THE BUG (found live 2026-09-02, LuxeLink MA re-upload): mig 732's duplicate guard is a partial
-- unique index on (org_id, file_sha256) WHERE status <> 'failed'. But `import_batches.claim(force=
-- True)` — the documented "load it again" override — marks the prior batch 'superseded' and then
-- INSERTS a fresh row with the same hash. 'superseded' still satisfies the index predicate, so the
-- insert hits 23505 and force is reported as... a duplicate. The override has therefore NEVER
-- worked since 732 landed, and an operator releasing a batch by superseding it (the incident
-- recovery tonight) is equally blocked; only mislabeling the batch 'failed' frees the slot.
--
-- THE FIX: exclude 'superseded' from the index predicate too, matching the semantics claim()
-- already assumes. 'failed' keeps meaning "parse/ingest broke, retry allowed"; 'superseded' keeps
-- meaning "an operator deliberately replaced this load" — both release the hash, every other
-- status still blocks a byte-identical re-import.
--
-- REVERT:
--   DROP INDEX IF EXISTS core.import_batches_org_hash_uidx;
--   CREATE UNIQUE INDEX import_batches_org_hash_uidx
--     ON core.import_batches (org_id, file_sha256) WHERE status <> 'failed';
--   (Only safe if no org has BOTH a superseded and a live batch for the same hash by then.)

DROP INDEX IF EXISTS core.import_batches_org_hash_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS import_batches_org_hash_uidx
  ON core.import_batches (org_id, file_sha256)
  WHERE status NOT IN ('failed', 'superseded');

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 931 complete — superseded batches release the duplicate-file hash (force re-upload works)' AS status;
