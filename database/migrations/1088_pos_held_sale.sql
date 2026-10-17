-- MIGRATION 1088: THE SUSPENDED SALE — one held cart per register
-- (owner 2026-10-16; docs/SYSTEM_DATA_FLOW_INDEX.md §POS-01). Idempotent, additive. OWNER-RUN — not applied by the agent.
--
-- Owner, verbatim: "also if a person leaves the cash register it gets blank if you come back it
-- shoudl automatically store for the next session in suspensed state"
--
-- WHAT WAS WRONG. The register's cart lived only in the browser's React state. A reload, a
-- navigation away, a crashed tab or the end of a shift erased a part-rung sale with no trace, and
-- the customer was then re-rung from memory — where the things remembered wrong are the discounts
-- and the serial numbers.
--
-- THE FACT THIS TABLE HOLDS, and why it is keyed this way. A suspended cart is a property of the
-- TERMINAL, not of a person and not of a browser profile. Its identity is
-- (org_id, store_code, register_number) — the SAME triple pos.register_sessions is keyed on (mig
-- 725), which is what makes the owner's "the next session" literal: the next cashier to open that
-- register is handed back exactly what the last one left on it. The partial unique index below
-- enforces ONE suspended cart per register, because a register with two "current sales" cannot say
-- which one the next scan belongs to.
--
-- WHAT IT IS NOT. It is not a sale. Nothing here writes pos.sales, burns a transaction number,
-- moves stock or records money — which is why the register may save it automatically without
-- asking, and why a cart that is never resumed has cost nothing. The lines are stored and restored
-- VERBATIM: a restore never re-prices a line or re-reads the catalog, because quietly moving a
-- figure is how a $99 line becomes $199 between the two halves of one sale. (Each line's TAX is
-- still re-derived from the store's live rate on restore, exactly as it is when the store is
-- switched mid-sale — a tax rate is a legal fact about the store, not a negotiated figure.)
-- `held_at` travels with the cart so the screen can say how old it is and the cashier can decide.
--
-- MONEY IMPACT: NONE. No existing row of any table is read or written by this file; no report,
-- statement or commission figure has a new input. The only writer is
-- `pos/held_sale.py` through PUT/DELETE /pos/held-sale, and the only reader is the register
-- itself. Proof (DB-free): `backend/harness_pos_held_sale.py`.
--
-- ADAPTIVE: the backend works before this file is applied. `GET /pos/held-sale` answers
-- {"held": null, "available": false} when the table is absent, and the register then behaves exactly
-- as it does today (a cart that is lost on reload) instead of erroring — so applying this file turns
-- the feature ON rather than being a precondition for the register working at all.
--
-- RULE TWO: no carrier, tenant, store, product or register number is named here; the register
-- number defaults to 1 to agree with pos.register_sessions.register_number's own default.
--
-- REVERT (paste and run to undo — drops only what this migration owns):
--   BEGIN;
--   DROP TABLE IF EXISTS pos.held_sales;
--   COMMIT;
--   NOTIFY pgrst, 'reload schema';

BEGIN;

CREATE TABLE IF NOT EXISTS pos.held_sales (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  store_code        TEXT NOT NULL,
  register_number   INT  NOT NULL DEFAULT 1,
  -- The cart, VERBATIM: the register's own line shape (pos/held_sale.LINE_FIELDS), which is the
  -- same shape POST /pos/sales/checkout consumes. Stored as given so resuming is a restore and not
  -- a re-derivation.
  cart              JSONB NOT NULL DEFAULT '[]'::jsonb,
  line_count        INT  NOT NULL DEFAULT 0,
  cart_total        NUMERIC(12,2) NOT NULL DEFAULT 0,   -- derived from `cart`; for the "a sale is waiting" line
  customer_id       UUID,                               -- the customer attached when it was suspended
  is_activation_sale BOOLEAN NOT NULL DEFAULT FALSE,
  note              TEXT,
  held_by           TEXT,                               -- storeops employee_id, else the login id
  held_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE pos.held_sales IS
  'The suspended sale: ONE held cart per register (mig 1088, index §POS-01). Keyed (org_id, store_code, '
  'register_number) — the same triple as pos.register_sessions — so the next session on that terminal is handed '
  'back what the last one left. Not a sale: no transaction number, no stock movement, no money. Lines are stored '
  'and restored verbatim; held_at says how old the prices are. One home: pos/held_sale.py.';

-- ONE suspended cart per register. Partial on nothing (every row is live) but spelled as a unique
-- index rather than a constraint so re-running this file is a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS pos_held_sales_one_per_register
  ON pos.held_sales (org_id, store_code, register_number);
CREATE INDEX IF NOT EXISTS pos_held_sales_org_updated
  ON pos.held_sales (org_id, updated_at DESC);

-- ── Security: the pos-schema posture (migs 724/725/1018) applied to THIS object only ───────────────
-- Backend-only: RLS on, nothing for anon / authenticated, service_role (the API) reads and writes.
-- Deliberately NOT mig 725's schema-wide GRANT block: re-running that would re-grant the PII
-- functions mig 909 revoked from service_role.
ALTER TABLE pos.held_sales ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON pos.held_sales FROM anon, authenticated;
GRANT ALL ON pos.held_sales TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1088 complete — pos.held_sales (one suspended cart per register).' AS status;
