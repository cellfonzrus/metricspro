-- 903_epay_daily_tx.sql — ingested ePay "Daily Transaction Detail" rows (owner directive 2026-08-20).
--
-- The Boost/ePay owner-portal report, one row per transaction line. Two line kinds matter:
--   • PAYMENT lines (Boost RTR PayGo / New Account Replen / Xfinity refill …) — the bill payment $
--   • FEE lines     (product title contains "FEE") — the ePay service charge $
-- Reconciled per store-day against our own raw_sales (product_desc "boost rtr" = payment,
-- "epay service charge" = fee). Each row's TerminalID resolves to OUR store via storeops.store_merchant_id
-- (processor 'epay').
--
-- Idempotent for the hourly re-pull: unique on (org_id, transaction_id, transaction_source_id).

CREATE TABLE IF NOT EXISTS commcalc.raw_epay_daily_tx (
    id                     BIGSERIAL PRIMARY KEY,
    org_id                 UUID NOT NULL,
    transaction_id         TEXT NOT NULL,
    transaction_source_id  TEXT NOT NULL,        -- 1 = main/payment line, 12 = fee line, 22 = opt svc, …
    invoice_id             TEXT,
    settlement_date        DATE,                 -- the business day this settles to (our close_date)
    terminal_id            TEXT,                 -- the store's ePay merchant/terminal id
    user_name              TEXT,                 -- often encodes the store (418Uniondale, Epay652, …)
    product                TEXT,
    product_title          TEXT,
    tx_type                TEXT,                 -- 'Sold' (refund/void kinds handled in aggregation)
    host_timestamp         TEXT,
    control_number         TEXT,
    retail                 NUMERIC DEFAULT 0,    -- the $ amount on this line
    discount               NUMERIC DEFAULT 0,
    cost                   NUMERIC DEFAULT 0,
    commission             NUMERIC DEFAULT 0,
    is_fee                 BOOLEAN NOT NULL DEFAULT FALSE,   -- product_title contains "FEE"
    store_code             TEXT,                 -- resolved from terminal_id (NULL until the terminal is mapped)
    source_batch           TEXT,                 -- upload/pull batch id, for traceability
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, transaction_id, transaction_source_id)
);

-- Per-store-day aggregation for the recon (payment vs fee), and the resolve/unmapped views.
CREATE INDEX IF NOT EXISTS raw_epay_daily_tx_store_day
    ON commcalc.raw_epay_daily_tx (org_id, store_code, settlement_date);
CREATE INDEX IF NOT EXISTS raw_epay_daily_tx_terminal
    ON commcalc.raw_epay_daily_tx (org_id, terminal_id, settlement_date);
