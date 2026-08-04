-- 274_commission_leg_split.sql — 1st-month vs M2–M12 commission-leg attribution (RULE TWO: config, not code)
--
-- OWNER DIRECTIVE 2026-08-04 (verbatim, in-chat):
--   "i need the gross profit report to have commission split in 2 parts - 1st Month commission which is
--    paid the same month of the activation and the other is M2-M12 commission, any commission received for
--    an activated number after the activated month will be in this category, this will also create a trend
--    alignment with the 3MR and 6MR to assess how they affect the commission payout."
--
-- READ-ONLY / REPORTING ONLY. Nothing here changes a payout, a rate, a tier, a plan rule or any input to
-- the calc. It decomposes commission money the org has ALREADY RECEIVED into two legs for reporting.
--
-- WHAT SPLITS WHAT (verified against the org's real export files, 2026-08-04):
--   • ePay Commission Payment Detail (#50273 → commcalc.raw_payment_detail) and the Comprehensive
--     Compensation Report (#100614 → commcalc.raw_comp_report) label EVERY multi-month leg in the type
--     string itself: "New Activation Bounty - Month 1" … "- Month 6", "Simplified SIM Loading Bounty -
--     Month N", "Boost Ready Bounty - Month N", "Device Upgrade Bounty - Month N", "(In-Store) Device
--     Financing Bounty - Month N", "BR BYOD SPIFF - Month N". Month 1 = the activation-month leg;
--     Month 2..N = money received for an already-activated number. The month token is the split.
--     Labels with NO month token ("Boost Auto Top-Up", "2026 SIM card reimbursement", "2026 Q2 Promo
--     Upgrade", "Commission Withholding", "Ramp Up Subsidy", "UNL Premium - 2 Month Promo") carry no
--     month-of-life in the source; they land in the honest `unsplit` bucket until an admin maps them.
--     NOTE: the ePay Payment Detail export DOES have an "Activation Date/Swap Date" column, but it is
--     100% NULL in the real file (30,339/30,339 rows, Apr-2026 run) — so a date-based split is NOT
--     available on this source. The label is the only truth it carries.
--   • VidaPay/master-agent (commcalc.raw_ma_commission) labels the leg in the COLUMN name: spiff_m1 is
--     the M1 leg, spiff_m2..spiff_m6 are the trailing legs. The activation-time margin components
--     (rebate, device_margin, consumer_margin, consumer_financing, wallet_funding, fees_margin) are
--     recognised on the activation order → M1.
--   • ePay MI/ATU residual (commcalc.raw_mi) carries `mi_activation_date`, so residual splits by the
--     owner's LITERAL definition: activation month == report month → 1st month, earlier → M2–M12.
--
-- SAFE: additive + idempotent. Every consumer degrades to a pure code default when these objects are
-- absent, so the Gross Profit report keeps working byte-identically before this migration is run.

-- ═══ 1. Per-(org, carrier) leg-attribution config ════════════════════════════════════════════════
-- Resolution (commission_legs.resolve_leg_config), same ladder as mig 223:
--   org+carrier row → org mode-default (carrier_id = nil) → HOUSE mode-default → code default.
-- Every tenant therefore inherits the two seeded house rows with zero per-tenant setup.
CREATE TABLE IF NOT EXISTS commcalc.commission_leg_config (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id    UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',  -- nil = mode-default row
  carrier_mode  TEXT NOT NULL DEFAULT 'boost',      -- used when carrier_id is nil: 'boost' | 'plan'

  -- ── label-driven split (ePay payment_type / comp compensation_type) ──
  -- Case-insensitive regex whose FIRST capture group is the leg's month-of-life.
  label_month_regex     TEXT NOT NULL DEFAULT 'month\s*[-#:]?\s*(\d+)',
  m1_month              INT  NOT NULL DEFAULT 1,   -- the month-of-life that IS the 1st-month leg
  max_leg_month         INT  NOT NULL DEFAULT 12,  -- highest leg the ladder view shows (M2..this = trailing)
  -- Where money whose source states NO month-of-life goes. 'unsplit' = honest default (shown as its own
  -- column + banner, never silently folded into a leg). 'm1' / 'trailing' available once an org decides.
  unlabeled_bucket      TEXT NOT NULL DEFAULT 'unsplit',

  -- ── master-agent (raw_ma_commission) column-driven split ──
  ma_month_field_prefix TEXT   NOT NULL DEFAULT 'spiff_m',   -- leg N column = prefix || N
  ma_max_month          INT    NOT NULL DEFAULT 6,           -- highest per-leg column the export carries
  ma_m1_fields          TEXT[] NOT NULL DEFAULT ARRAY['rebate','device_margin','consumer_margin',
                                                      'consumer_financing','wallet_funding','fees_margin'],
  ma_payout_sign        NUMERIC NOT NULL DEFAULT -1,         -- MA posts payouts NEGATIVE → received = raw * -1

  -- ── residual (raw_mi) date-driven split ──
  mi_split_by_activation BOOLEAN NOT NULL DEFAULT true,      -- false = residual stays unsplit

  is_active     BOOLEAN NOT NULL DEFAULT true,
  notes         TEXT,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, carrier_id, carrier_mode)
);
CREATE INDEX IF NOT EXISTS commission_leg_config_org ON commcalc.commission_leg_config (org_id);

COMMENT ON TABLE commcalc.commission_leg_config IS
  'Per-(org,carrier) rules that attribute RECEIVED commission money to the 1st-month leg vs the M2-M12 trailing legs, for the Gross Profit report''s commission split (owner directive 2026-08-04). Reporting only — never an input to a payout.';

INSERT INTO commcalc.commission_leg_config
  (org_id, carrier_id, carrier_mode, label_month_regex, m1_month, max_leg_month, unlabeled_bucket,
   ma_month_field_prefix, ma_max_month, ma_m1_fields, ma_payout_sign, mi_split_by_activation, notes)
VALUES
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'boost',
   'month\s*[-#:]?\s*(\d+)', 1, 12, 'unsplit', 'spiff_m', 6,
   ARRAY['rebate','device_margin','consumer_margin','consumer_financing','wallet_funding','fees_margin'],
   -1, true,
   'House/Boost default. ePay Payment Detail + Comprehensive Comp label the leg in the type string ("... - Month N"); Month 1 = activation-month leg. MI/ATU residual splits on raw_mi.mi_activation_date vs the report month. Un-monthed labels stay in the honest unsplit bucket until mapped on /commcalc/commission-legs.'),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'plan',
   'month\s*[-#:]?\s*(\d+)', 1, 12, 'unsplit', 'spiff_m', 6,
   ARRAY['rebate','device_margin','consumer_margin','consumer_financing','wallet_funding','fees_margin'],
   -1, true,
   'Master-agent (VidaPay/Total) default. raw_ma_commission names the leg in the COLUMN: spiff_m1 = M1 leg, spiff_m2..m6 = trailing. Activation-order margins (rebate/device_margin/consumer_margin/consumer_financing/wallet_funding/fees_margin) are recognised at activation -> M1.')
ON CONFLICT (org_id, carrier_id, carrier_mode) DO NOTHING;

-- ═══ 2. Per-org explicit label → leg mapping (pick-don't-type admin surface) ═════════════════════
-- Overrides the regex for one exact label. This is how an org resolves its own un-monthed labels
-- ("Boost Auto Top-Up" -> trailing, "2026 Q2 Promo New Act Offer" -> m1, ...) without a code change.
CREATE TABLE IF NOT EXISTS commcalc.commission_leg_label_map (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  label      TEXT NOT NULL,                     -- exact payment_type / compensation_type as it appears
  bucket     TEXT NOT NULL CHECK (bucket IN ('m1','trailing','unsplit')),
  leg_month  INT,                               -- optional month-of-life for the leg-ladder view
  note       TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, label)
);
CREATE INDEX IF NOT EXISTS commission_leg_label_map_org ON commcalc.commission_leg_label_map (org_id);

COMMENT ON TABLE commcalc.commission_leg_label_map IS
  'Per-org override of the 1st-month vs M2-M12 attribution for one exact carrier payment/compensation label. Empty = the regex in commission_leg_config decides. Edited on /commcalc/commission-legs.';

-- ═══ 3. Aggregate RPCs — the trend must not page 360k rows into Python ══════════════════════════
-- Cardinality is tiny (labels x stores x months), so the whole 12-month trend is one round trip.
-- Every RPC is STABLE + org-scoped by argument; the backend calls them with the service role.

-- 3a. Received commission by (source, period, store street-number, label) — the label carries the leg.
CREATE OR REPLACE FUNCTION commcalc.commission_leg_label_rollup(p_org_id uuid, p_periods text[])
RETURNS TABLE (source text, period text, store_num text, label text, category text,
               amount numeric, n bigint)
LANGUAGE sql STABLE AS $$
  SELECT 'payment_detail'::text,
         pd.period,
         split_part(btrim(coalesce(pd.business_address, '')), ' ', 1),
         btrim(coalesce(pd.payment_type, '')),
         coalesce(pc.category, 'Unknown'),
         sum(coalesce(pd.amount, 0)),
         count(*)
    FROM commcalc.raw_payment_detail pd
    LEFT JOIN commcalc.payment_categories pc
           ON pc.org_id = pd.org_id
          AND btrim(pc.description) = btrim(pd.payment_type)
   WHERE pd.org_id = p_org_id
     AND pd.period = ANY (p_periods)
   GROUP BY 2, 3, 4, 5
  UNION ALL
  SELECT 'comp_report'::text,
         cr.period,
         split_part(btrim(coalesce(cr.business_address, '')), ' ', 1),
         btrim(coalesce(cr.compensation_type, '')),
         ''::text,
         sum(coalesce(cr.payment_amount, 0)),
         count(*)
    FROM commcalc.raw_comp_report cr
   WHERE cr.org_id = p_org_id
     AND cr.period = ANY (p_periods)
   GROUP BY 2, 3, 4;
$$;

COMMENT ON FUNCTION commcalc.commission_leg_label_rollup(uuid, text[]) IS
  'Received-commission rollup for the GP commission-leg split/trend: (source, period, store street number, raw label, mapped payment category) -> summed amount. The leg (Month N) lives in the label; the backend classifies it from commission_leg_config so the rule stays config, not SQL.';

-- 3b. MI/ATU residual by (period, leg month) — leg = months between mi_activation_date and the report
--     month, +1 (activation month itself = leg 1). The column is DATE in prod (::text casts make the
--     regex work there AND on any env that stored raw text; a date casts to ISO so branch 1 matches); anything else yields leg_month NULL = honestly unsplit.
--     Period is read from period_year/period_month (populated by every raw_mi writer) so the
--     'June 2026' vs '2026-06' spelling duality cannot bite here.
CREATE OR REPLACE FUNCTION commcalc.commission_leg_mi_rollup(p_org_id uuid, p_periods text[])
RETURNS TABLE (period text, salesforce_id text, leg_month int, mi numeric, atu numeric, n bigint)
LANGUAGE sql STABLE AS $$
  WITH src AS (
    SELECT m.period,
           coalesce(m.salesforce_id, '') AS salesforce_id,
           coalesce(m.actual_mi_payout, 0) AS mi,
           coalesce(m.actual_atu_payout, 0) AS atu,
           m.period_year AS py,
           m.period_month AS pm,
           CASE
             WHEN m.mi_activation_date::text ~ '^\d{4}-\d{1,2}-\d{1,2}'
               THEN (substring(m.mi_activation_date::text from '^(\d{4})'))::int
             WHEN m.mi_activation_date::text ~ '^\d{1,2}/\d{1,2}/\d{4}'
               THEN (substring(m.mi_activation_date::text from '(\d{4})$'))::int
             ELSE NULL END AS ay,
           CASE
             WHEN m.mi_activation_date::text ~ '^\d{4}-\d{1,2}-\d{1,2}'
               THEN (substring(m.mi_activation_date::text from '^\d{4}-(\d{1,2})'))::int
             WHEN m.mi_activation_date::text ~ '^\d{1,2}/\d{1,2}/\d{4}'
               THEN (substring(m.mi_activation_date::text from '^(\d{1,2})/'))::int
             ELSE NULL END AS am
      FROM commcalc.raw_mi m
     WHERE m.org_id = p_org_id
       AND m.period = ANY (p_periods)
  )
  SELECT period,
         salesforce_id,
         CASE WHEN py IS NULL OR pm IS NULL OR ay IS NULL OR am IS NULL THEN NULL
              WHEN (py - ay) * 12 + (pm - am) < 0 THEN NULL   -- activation AFTER the report month = data oddity
              ELSE (py - ay) * 12 + (pm - am) + 1 END AS leg_month,
         sum(mi), sum(atu), count(*)
    FROM src
   GROUP BY 1, 2, 3;
$$;

COMMENT ON FUNCTION commcalc.commission_leg_mi_rollup(uuid, text[]) IS
  'ePay MI/ATU residual rolled up by (period, salesforce_id, month-of-life). leg_month 1 = the subscriber activated in the report month; >1 = residual on an already-activated number; NULL = mi_activation_date missing/unparseable (reported as unsplit, never guessed).';

-- 3c. Master-agent commission components by period — the leg is the COLUMN NAME, so return them wide
--     and let the backend apply the org's configured prefix/field lists.
CREATE OR REPLACE FUNCTION commcalc.commission_leg_ma_rollup(p_org_id uuid, p_periods text[])
RETURNS TABLE (period text, device_margin numeric, consumer_margin numeric, consumer_financing numeric,
               rebate numeric, wallet_funding numeric, fees_margin numeric,
               spiff_m1 numeric, spiff_m2 numeric, spiff_m3 numeric,
               spiff_m4 numeric, spiff_m5 numeric, spiff_m6 numeric, n bigint)
LANGUAGE sql STABLE AS $$
  SELECT c.period,
         sum(coalesce(c.device_margin, 0)), sum(coalesce(c.consumer_margin, 0)),
         sum(coalesce(c.consumer_financing, 0)), sum(coalesce(c.rebate, 0)),
         sum(coalesce(c.wallet_funding, 0)), sum(coalesce(c.fees_margin, 0)),
         sum(coalesce(c.spiff_m1, 0)), sum(coalesce(c.spiff_m2, 0)), sum(coalesce(c.spiff_m3, 0)),
         sum(coalesce(c.spiff_m4, 0)), sum(coalesce(c.spiff_m5, 0)), sum(coalesce(c.spiff_m6, 0)),
         count(*)
    FROM commcalc.raw_ma_commission c
   WHERE c.org_id = p_org_id
     AND c.period = ANY (p_periods)
   GROUP BY 1;
$$;

COMMENT ON FUNCTION commcalc.commission_leg_ma_rollup(uuid, text[]) IS
  'VidaPay/master-agent commission components summed per period for the GP commission-leg split. Raw (unsigned) sums — the backend applies the org''s configured ma_payout_sign, exactly as _compute_gp/residual_subs already do.';

-- ═══ 3d. Commission Ledger: Category → Bucket Map carries the leg too ═══════════════════════════
-- OWNER SCOPE EXTENSION 2026-08-04: "also update commission m2-m12 this in the Category → Bucket Map
-- (Commission Ledger) and everywhere else commission touches."
--
-- STRICTLY ADDITIVE — this NEVER re-buckets an existing mapped row. `leg_bucket` is NULL on every
-- existing rule, and NULL means "derive the leg from the line itself" (the ledger already parses a
-- payment month out of every product label — `commission_ledger.parse_payment_month`: 'TBV MONTH 4' →
-- 4, 'Commission - M1 Proration' → 1). A rule only overrides that derivation when a human explicitly
-- sets it on /commcalc/commission-category-map. The five canonical CATEGORIES are untouched: the leg is
-- a second, orthogonal dimension (category × leg), not a re-categorisation.
--
-- Nothing is stamped onto commcalc.commission_ledger rows: the leg is derived at READ time in
-- `commission_ledger.summarize()`, so there is no backfill, no re-sync, and no ingest-path change that
-- could reject an insert on a tenant that hasn't run this migration.
ALTER TABLE commcalc.commission_category_map
  ADD COLUMN IF NOT EXISTS leg_bucket TEXT;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'commission_category_map_leg_bucket_ck') THEN
    ALTER TABLE commcalc.commission_category_map
      ADD CONSTRAINT commission_category_map_leg_bucket_ck
      CHECK (leg_bucket IS NULL OR leg_bucket IN ('m1', 'trailing', 'unsplit'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_category_map.leg_bucket IS
  'OPTIONAL commission-leg override for lines this rule classifies: m1 = 1st-month commission (received in the activation month), trailing = M2-M12, unsplit = source states no month-of-life. NULL (the default, and the value on every pre-existing rule) = derive the leg from the line''s own payment month / label, so adding this column re-buckets nothing.';

-- ═══ 4. RLS — locked down per contract §5 (no anon/authenticated grants; service role bypasses) ══
ALTER TABLE commcalc.commission_leg_config    ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.commission_leg_label_map ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 274 complete — commission_leg_config ('
       || (SELECT count(*) FROM commcalc.commission_leg_config
             WHERE org_id = '00000000-0000-0000-0000-000000000001')
       || ' house rows) + commission_leg_label_map + 3 rollup RPCs' AS status;
