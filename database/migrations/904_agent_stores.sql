-- 904_agent_stores.sql — agent (sub-dealer) stores + is_agent flag (owner directive 2026-08-20).
--
-- Some stores are AGENTS of Cellfonz R Us: independent Boost sub-dealers whose ePay volume we reconcile
-- for REPORTING, but who are NOT part of our operational scheduling (no corporate shifts / staff). Flag
-- them `is_agent` so financial/ePay reporting (closing recon, ePay payment + fee recon, dashboards)
-- includes them while the SCHEDULE excludes them. Their ePay Daily Transaction Detail terminals are mapped
-- here so the payment/fee recon resolves each agent store. They otherwise stay exactly as they are.
--
-- No ON CONFLICT (the tenant SQL runner rejects it) — every insert is guarded with NOT EXISTS instead, so
-- this migration is safe to re-run.

ALTER TABLE storeops.stores ADD COLUMN IF NOT EXISTS is_agent BOOLEAN NOT NULL DEFAULT FALSE;

-- Index so the schedule's "operational stores only" reads stay fast when they exclude agents.
CREATE INDEX IF NOT EXISTS stores_operational
    ON storeops.stores (org_id) WHERE is_agent = FALSE;

-- The 9 Boost ePay agent stores (Cellfonz `…0001`), created with a B- code + is_agent=TRUE. `market` is
-- left NULL (agents are not in a corporate market); address filled where known.
INSERT INTO storeops.stores (org_id, store_code, address, is_active, is_agent, notes)
SELECT v.org_id, v.store_code, v.address, TRUE, TRUE,
       'ePay agent store — Boost sub-agent of Cellfonz R Us (reporting only, excluded from schedule)'
FROM (VALUES
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-6149', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-5619', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-60TH', '1 S 60th St, Philadelphia'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-6507', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-1710', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-3605', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-2701', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-2778', NULL),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-723',  NULL)
) AS v(org_id, store_code, address)
WHERE NOT EXISTS (
    SELECT 1 FROM storeops.stores s WHERE s.org_id = v.org_id AND s.store_code = v.store_code
);

-- Their ePay (Boost) terminal ids → the payment/fee recon now resolves these agent stores.
INSERT INTO storeops.store_merchant_id (org_id, store_code, processor, merchant_id)
SELECT v.org_id, v.store_code, 'epay', v.terminal_id
FROM (VALUES
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-6149', '643996'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-5619', '643998'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-60TH', '644076'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-6507', '644078'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-1710', '644080'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-3605', '644082'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-2701', '644083'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-2778', '644087'),
  ('00000000-0000-0000-0000-000000000001'::uuid, 'B-723',  '644089')
) AS v(org_id, store_code, terminal_id)
WHERE NOT EXISTS (
    SELECT 1 FROM storeops.store_merchant_id x
    WHERE x.org_id = v.org_id AND x.store_code = v.store_code AND x.processor = 'epay'
);
