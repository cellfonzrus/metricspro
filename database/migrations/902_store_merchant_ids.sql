-- 902_store_merchant_ids.sql — per-store payment-processor merchant IDs (owner directive 2026-08-20).
--
-- A store transacts third-party payments through one or more processors, each of which identifies the
-- store by its own merchant/terminal id:
--   • Boost  → ePay ID     (the "TerminalID" on the ePay Daily Transaction Detail report)
--   • Total  → Vidapay ID
--   • other carriers → their own processor's merchant id
--
-- These ids are how an ingested processor report (ePay DTD, Vidapay, …) resolves each transaction back to
-- OUR store. They are set at store setup — mandatory per active processor unless the operator ticks
-- "not required" for a store that does not run that processor's payments. One row per (store, processor).

CREATE TABLE IF NOT EXISTS storeops.store_merchant_id (
    id            BIGSERIAL PRIMARY KEY,
    org_id        UUID NOT NULL,
    store_code    TEXT NOT NULL,
    processor     TEXT NOT NULL,                    -- 'epay' (Boost), 'vidapay' (Total), extensible
    merchant_id   TEXT,                             -- the store's id at that processor (NULL when not_required)
    not_required  BOOLEAN NOT NULL DEFAULT FALSE,   -- operator opted this store out of this processor
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, store_code, processor)
);

-- Resolve a processor report row (processor + merchant_id) back to a store_code, fast.
CREATE INDEX IF NOT EXISTS store_merchant_id_lookup
    ON storeops.store_merchant_id (org_id, processor, merchant_id)
    WHERE merchant_id IS NOT NULL;

-- List every processor id configured for a store (the store-setup panel).
CREATE INDEX IF NOT EXISTS store_merchant_id_by_store
    ON storeops.store_merchant_id (org_id, store_code);
