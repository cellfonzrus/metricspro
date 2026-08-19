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
| Pluggable vendors | **new** `pos.vendor_connector` registry + `pos/vendor_adapters.py` (outbound) + `pos/vendor_api.py` (inbound) |
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
- **New table** `pos.special_orders` (mig 865, org-scoped, composite FK; single-line per order for now —
  multi-line can be added later) capturing customer, store, employee, item, declared sale price, captured
  cost, the booked `sale_id`, and **status** (`requested → ordered → shipped → received → delivered`).
  The sale-item inventory trigger is re-created to **skip special-order products**, so a special-order
  checkout is inventory-neutral even where `allow_negative_inventory` is off.

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

## Pluggable vendor connectors (plug-and-play)

Owner directive 2026-08-19: *"create an option to plug and play if other vendors also have a drop
shipment platform to use with their API to connect to our system or with our API to connect to them."*

Amazon is just the first dropship vendor. Any vendor becomes usable by registering a
**`pos.vendor_connector`** row (**migration 866**) — adding a vendor is a **data change, not a code
change** (except a bespoke outbound API). A connector's `integration_mode` picks one of three ways to
integrate, covering **both directions** the owner asked for:

| Mode | Direction | How it works | Code needed to add a vendor |
|---|---|---|---|
| `manual` | none | HQ/ops places each order from the fulfillment queue. Amazon at launch. | none |
| `inbound_api` | **their platform → our API** | The vendor **polls our API** (`pos/vendor_api.py`) to pull its queued orders and post back status/tracking, authenticating with a per-vendor token. | none |
| `outbound_api` | **our system → their API** | **We call the vendor's API** (`pos/vendor_adapters.py`) to place the order, using an `api_base_url` + a `credential_ref` (the **name** of an env/secret — the raw key is never in the DB). | none for a plain REST vendor; a bespoke API registers one adapter subclass |

**The catalog binds a product to a vendor** by `pos.special_order_vendor.vendor == vendor_connector.vendor_key`.
On `POST /pos/special-orders`, after the sale is booked, `create_special_order` resolves the product's
connector and calls its adapter — but **placement never blocks the sale**: an outbound-API failure just
leaves the order `requested` in the manual queue with a breadcrumb note.

**Outbound (our system → their API)** — `pos/vendor_adapters.py`:
- `VendorAdapter` interface (`place_order` / `refresh`) + a registry (`get_adapter(connector)`).
- `ManualAdapter`, `InboundApiAdapter` (both just queue), and a **generic** `OutboundApiAdapter` that
  POSTs a neutral JSON order to `api_base_url`, config-driven (`place_path`, `auth_header`,
  `auth_scheme`, `order_ref_key`, `tracking_key`, `timeout`) so a plain REST vendor needs **zero code**.
- A vendor with a non-standard API registers its own subclass: `register_adapter("vendor:<key>", Cls)`,
  which wins over the generic mode adapter.
- HQ manages connectors via `GET/POST/PATCH /pos/vendor-connectors` (gated `pos_special_order_admin`).

**Inbound (their platform → our API)** — `pos/vendor_api.py`, mounted at `/api/v1/vendor-api`:
- A **separate router with NO member-auth gate** — a vendor isn't an org member. Each request carries the
  vendor's **bearer token**; we SHA-256 it and match `pos.vendor_connector.inbound_token_hash` (only the
  hash is stored — the raw token is shown **once** at registration). That resolves the (org, vendor_key),
  and the vendor sees/touches **only its own** vendor_key's orders.
- `GET /vendor-api/orders` — the vendor pulls its queued orders (ship-to store + address, item SKU, qty,
  a reference). **Reverse source-hiding:** no customer PII, no sale price, no cost, no margin.
- `POST /vendor-api/orders/{id}/status` — the vendor posts `ordered|shipped|received|cancelled` + tracking
  + its order ref (it can't set `requested` or `delivered` — those are ours).

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
- [ ] **8. Apply migrations 865 + 866.** In the Supabase SQL editor run
      `database/migrations/865_pos_special_orders.sql` (the order record + inventory-neutral trigger) and
      `database/migrations/866_pos_vendor_connectors.sql` (the plug-and-play vendor registry; seeds a
      `manual` Amazon connector). Both additive and idempotent.
- [ ] **9. (Only when adding a non-Amazon vendor) register a connector.** In the HQ vendor-connector
      admin, add the vendor and pick its mode: **manual** (HQ fulfills), **outbound_api** (we call them —
      give the API base URL and put the API key in a Railway secret, then set `credential_ref` to that
      secret's **name**, never the key), or **inbound_api** (they call us — we generate a token, shown
      **once**, that you hand to the vendor). Then link catalog items to that vendor.

---

## Status
- **Phase 1 (backend hidden catalog): done** — migration 864 + the four catalog endpoints.
- **Phase 2 (order + booking): done (backend)** — migration 865 (`pos.special_orders` + inventory-neutral
  trigger) + `POST/GET/PATCH /pos/special-orders`. On order it books the sale (declared price → revenue,
  vendor cost → COGS; profit derives) and records the order, enforcing the margin floor.
- **Phase 2.5 (plug-and-play vendors): done (backend)** — migration 866 (`pos.vendor_connector`) +
  `pos/vendor_adapters.py` (outbound) + `pos/vendor_api.py` (inbound, token-authed) + the
  `/pos/vendor-connectors` admin CRUD.
- **Phase 1b/2b (frontend): done** — two neutral pages under `/pos/special-orders/`:
  - `/pos/special-orders` — store-facing: neutral catalog search → declared price (margin-floored) →
    customer + ship-to + payment → `POST /pos/special-orders`, plus an Orders tab with a vendor-free
    lifecycle status. A `🧾 Customer Special Order` button was added to the register action panel.
  - `/pos/special-orders/manage` — HQ-only (locks unless the caller holds `pos_special_order_admin`):
    a **Catalog** tab (items + hidden vendor linkage) and a **Vendors** tab (the connector registry,
    with the inbound token shown once). Two nav entries added in `rbac.ts` (the manage page is
    `all`/`market` in nav AND server-gated by the permission).
- Next build steps: Phase 4 (a dedicated HQ ops fulfillment queue UI — today the `manage` page + the
  store Orders tab cover the essentials), then Phase 5 (Amazon Business API automation, ToS-gated).
- Blocked-on-owner: the Phase 0 decisions (TODO #1–3, #6) before this can go live; apply migrations
  864/865/866 (TODO #4, #8) and grant `pos_special_order_admin` to HQ roles (TODO #5).
