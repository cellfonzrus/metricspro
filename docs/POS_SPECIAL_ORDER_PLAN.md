# POS — Customer Special Order

Owner directive 2026-08-19. Let a store sell an item it doesn't stock (accessories / electronics) by
special-ordering it from a back-end vendor (Amazon), **shipped to the store**, with the **source hidden**
from both the customer and store staff — while booking the sale, cost, and profit through the accounting
rails we already have.

> **Two audiences for this doc:** the plan/architecture (for whoever builds it) and an
> **Operator / Owner TODO** section at the end (desktop tasks for the owner). Jump to
> [Operator / Owner TODO](#operator--owner-todo-do-these-at-a-desktop) for the checklist.

---

## The insight that shapes everything

The accounting engine **never posts a "profit" number — profit is derived** (`gross_profit = revenue −
COGS`, computed at read time in `account/engine.py` and the GP scorecard `commcalc/router.py:_compute_gp`).
So a special order books **two** things and profit falls out automatically:

- **Declared sale price → a POS sale line's `unit_price`** → flows via `pos/commcalc_feed.py` into
  `commcalc.raw_sales` → a **revenue** line (`accessory_rev` / `device_rev`).
- **Vendor (Amazon) cost → that same sale line's `cost`** → the existing POS device-cost path books it
  as **COGS** (`gp = ext − cost`).
- **Profit = revenue − COGS**, derived per store — already feeding the store P&L and the net-profit-target
  scorecard (`storeops.stores.net_profit_target`, mig 855).

**Consequence:** once a special order becomes a normal `pos.sales` line (via the `pos.checkout` RPC),
the sale, COGS, and profit all appear correctly with **zero new accounting plumbing**. That is the
backbone of the whole feature.

---

## Architecture (reuses existing rails)

| Concern | Reuses |
|---|---|
| Product catalog | `pos.products` (add `is_special_order` flag) |
| Hidden vendor linkage | **new** `pos.special_order_vendor` (Amazon ASIN/URL/cost), HQ-only |
| Sale + tender | `pos.checkout` RPC → `pos.sales` / `pos.sale_items` / `pos.sale_payments` |
| Sale → revenue | `pos/commcalc_feed.py` → `commcalc.raw_sales` → P&L |
| Cost → COGS, profit derived | sale line `cost` → device-cost path; `engine._assemble` derives profit |
| Vendor order + ship-to-store + receiving | existing purchase-order model `po_*` (`POST /po`, `POST /po/{id}/receive`) |
| Source-hiding | split read APIs + a `pos_special_order_admin` permission + neutral UI |

---

## Phase 0 — Decisions & compliance gate (BEFORE going live)

These are the make-or-break calls; the build is low-risk once settled. **See the Operator TODO.**

1. **Amazon terms (legal/vendor sign-off).** Reselling Amazon purchases to customers while hiding the
   source touches Amazon's resale/dropship policies. The safer framing is **procurement, not dropship**:
   order on **Amazon Business**, **ship to the store**, store hands the item to the customer. Confirm this
   is acceptable under Amazon Business terms.
2. **Sales tax / resale posture.** Resale certificate on Amazon Business vs. paying tax on the buy; how the
   customer is charged tax at the store.
3. **Ordering mechanism — start MANUAL.** Phase 4 = an HQ/ops queue where a person places the Amazon
   Business order (ship-to store). Simpler, ToS-safer, and keeps Amazon invisible to store staff. An
   Amazon Business **API** auto-order is a later, optional phase.
4. **Pay at order time** (matches "sale captured at time of ordering") + a cancel/refund policy.
5. **Margin guardrail** — declared sale ≥ cost + minimum margin; block loss-making orders.
6. **Commission credit** — booking as a POS sale gives the rep GP/commission credit. Decide if desired.
7. **Returns/warranty owner** — the store, per receipt.

---

## Implementation phases

### Phase 1 — Hidden vendor catalog (backend)  ✅ STARTED
- **Migration 864** (`database/migrations/864_pos_special_order_catalog.sql`): `pos.products.is_special_order`
  + `pos.special_order_vendor` (HQ-only Amazon linkage), org-scoped per the mig-728 FK convention.
- **Endpoints** (`backend/app/modules/pos/router.py`):
  - `GET /pos/special-orders/catalog` — **neutral**, store/customer-facing; returns only product fields,
    **never reads the vendor table** (source-hiding at the API boundary).
  - `GET /pos/special-orders/catalog/admin` — HQ-only (perm `pos_special_order_admin`), includes vendor
    linkage.
  - `POST /pos/special-orders/catalog`, `PATCH /pos/special-orders/catalog/{id}` — HQ create/update of the
    item + vendor linkage.
- **Remaining in Phase 1b:** HQ catalog-management UI; register `pos_special_order_admin` in the Roles UI.

### Phase 2 — POS "Customer Special Order" flow (store-facing)
- A `🧾 Customer Special Order` button on the POS home action panel (`pos/sales/page.tsx` ~line 857) + a
  nav entry in `rbac.ts`.
- Neutral screen (no Amazon): search the special-order catalog → pick item → **declared sale price**
  (default `retail_price`, editable within the margin guardrail) → attach customer + ship-to store → take
  payment via the existing POS tender.
- **New tables** `pos.special_orders` / `pos.special_order_items` (org-scoped, composite FKs) capturing
  customer, store, employee, item, declared sale price, captured cost, payment, and **status**
  (`requested → ordered → shipped → received → delivered`).

### Phase 3 — Booking (sale + COGS + profit)
- On order, create a `pos.sales` line via `pos.checkout` with `unit_price = declared sale`,
  `cost = vendor cost`. Sale, COGS, and profit flow automatically (see "the insight" above).
- **Cost true-up:** capture catalog cost at order; reconcile the **actual** Amazon cost at fulfillment onto
  the sale line so COGS is exact.

### Phase 4 — Fulfillment queue (HQ/ops — where Amazon lives)
- HQ/ops-only "Special Orders" queue (perm `pos_special_order_admin`) showing each order with its **Amazon
  ASIN/link** and **ship-to store**. Reuse the purchase-order model (`po_*`, `POST /po`, `POST /po/{id}/receive`).
- Ops places the Amazon Business order (ship-to store) → status ordered/shipped/received → store notified on
  arrival → hand to customer → mark delivered.

### Phase 5 — (Optional, later) Amazon Business API automation
Only if Phase 0 clears it: auto-place the order from the ops queue via the Amazon Business API.

---

## Source-hiding, enforced in layers
1. **API** — the store/customer catalog endpoint never returns the vendor table.
2. **Permission** — vendor linkage + ops queue require `pos_special_order_admin` (HQ); store staff don't
   hold it, so they can't see the vendor **or** self-order from it (the whole point).
3. **UI** — neutral "Special Order" branding everywhere store/customer-facing; no Amazon logo/name/URL.

## Risks
- Amazon ToS + sales tax/resale (**the real risk — Phase 0 gate**).
- Cancellations/refunds before fulfillment (void the POS sale + reverse COGS).
- Cost drift between catalog cost and actual Amazon price (the true-up in Phase 3).
- Commission credit on special orders (a policy decision).

---

## Operator / Owner TODO (do these at a desktop)

These are **yours** — decisions and console tasks the build can't do. Ordered; #1–2 gate go-live.

- [ ] **1. Amazon terms — get sign-off (BLOCKER).** Open an **Amazon Business** account (not a personal
      Amazon account). Confirm with legal/your Amazon rep that **buying to resell, shipped to your store,
      with the source not shown to the customer** is acceptable under Amazon Business terms. This is the one
      thing that can stop the whole feature — settle it before turning anything on.
- [ ] **2. Sales tax / resale.** Decide the tax posture: put a **resale certificate** on the Amazon Business
      account (buy tax-exempt) vs. paying tax on each buy. Confirm how the customer is charged tax at the
      store (the POS tax code already handles the sell side).
- [ ] **3. Choose the ordering mechanism.** Start **manual** (an HQ/ops person places each Amazon Business
      order, ship-to store) — recommended. Only consider the Amazon **API** automation (Phase 5) later, and
      only if #1 clears it.
- [ ] **4. Apply migration 864.** In the Supabase SQL editor, run
      `database/migrations/864_pos_special_order_catalog.sql` (adds `pos.products.is_special_order` +
      `pos.special_order_vendor`). Additive and idempotent.
- [ ] **5. Grant the HQ permission.** In **Roles & Access**, give **`pos_special_order_admin`** to the
      owner/HQ role(s) that will manage the catalog and place orders. **Do NOT** give it to store roles —
      that grant is exactly what keeps Amazon hidden from stores. (Admin / scope-'all' roles get it by
      default; store roles do not.)
- [ ] **6. Decide the policy knobs** (tell me and I'll wire them): minimum **margin %**; **pay at order**
      vs. pay on pickup; whether special-order sales **count toward rep commission/KPIs**; the
      **cancellation/refund** policy; who owns **returns/warranty** (default: the store).
- [ ] **7. Seed the initial catalog.** Once the HQ catalog UI ships (Phase 1b) — or via the API now — add
      the first special-order items: customer-facing name/price + the hidden Amazon ASIN/URL/cost.

---

## Status
- **Phase 1 (backend hidden catalog): started** — migration 864 + the four catalog endpoints landed.
- Next build steps: Phase 1b (HQ catalog UI + register the permission), then Phase 2 (the POS flow +
  special-order tables), then Phase 3 (booking wire-up), then Phase 4 (ops fulfillment queue).
- Blocked-on-owner: the Phase 0 decisions (TODO #1–3, #6) before this can go live.
