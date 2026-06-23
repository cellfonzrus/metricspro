# MetricsPro — User Guide (plain English)

What the system does and how to use it, in everyday language. No code. (Technical version:
[ARCHITECTURE.md](ARCHITECTURE.md).)

## What MetricsPro is

MetricsPro is the back office for a prepaid phone dealer. It pulls your numbers from every carrier and
vendor portal, puts them in one place, and tells you: **what you earned, what you're owed, what each
store and rep did, and where money is leaking.** It's being built to work for **any** carrier —
Boost today, then Cricket, Metro, Total Wireless — because they all pay the same way.

## How carriers pay you (the 4 buckets)

Every dollar a carrier pays lands in one of four buckets. Different carriers call them different names,
but they're always the same idea:

| Bucket | In plain terms |
|---|---|
| **Residual** | Your recurring monthly income for each customer who stays active. For Boost this is **MI + ATU**. |
| **Commission** | A one-time payment when you activate, upgrade, or port-in a line (usually a promo rate). |
| **SPIFF** | Bonus money / bounties the carrier offers that month for hitting goals (often paid out over several months). |
| **Reimbursement** | The carrier paying you back the difference between what a phone cost you and the discounted price you sold it for. |

> **Important:** the "Comprehensive Comp" report from Boost is **mostly Commission + SPIFF** (promos and
> bounties) — it is **not** residual. The page that used to say "Residual Trend" is now **"Total
> Compensation"**, and it shows your **true Residual (MI + ATU)** right next to it.

## The main areas

- **Data Imports** (`/commcalc/upload`) — where data comes in. Most portals **auto-sweep** on a schedule
  (ePay, Elevate Go/DLAR, VIP, Yoobic). For anything that can't auto-pull (B2B Soft), use the
  **Upload Wizard** — it tells you the exact report name, where to get it, and uploads it to the right place.
- **Commissions** — what each rep earned: activations, upgrades, accessories, trade-ins, plus KPI-based
  tiers. Backed by the carrier's MI/sales data.
- **Total Compensation** (`/commcalc/comp-trend`) — month-over-month total carrier compensation per
  account, with true Residual (MI+ATU) alongside, and "dips" that flag likely cancellations.
- **Daily Targets** — each store/rep's target vs. pace vs. achieved, with an action plan for what to fix.
- **Sales Analyzer** — 3-month retention / churn: which customers left before their 3rd bill, and why.
- **Asset / Devices** — your device inventory, aging (what's sitting too long), what you're owed back
  from VIP, and charges/appeals. Includes a hotsheet "expected vs. actually paid" check.
- **Accounts (P&L / Balance Sheet)** — your profit & loss and balance sheet, per company and per store,
  built from real data (with an optional written summary).
- **Store Ops** — employee scheduling, time-off, payroll, and District-Manager store visits.
- **Daily Closing** — each rep's end-of-day cash/card/accessory totals, checked against the carrier's
  actual sales for that day.
- **Flags & Compliance** — anything that needs attention: shortfalls, chargebacks, missed reimbursements.

## How to get your numbers in

1. Open **Data Imports**.
2. Auto-sweeps run on their own; check the "last uploaded" time on each. Hit **Run now** to refresh one.
3. For B2B Soft (manual), open the **Upload Wizard**, pick the report, and follow the steps.
4. After uploading a sales or DLAR file, the system recalculates commissions for that month automatically.

## Reading the money

- **Commission report** = what reps earned this month.
- **Total Compensation** = everything the carrier paid you (promos + bounties + reimbursement).
- **Residual (MI + ATU)** = your recurring base income. A drop here usually means customers canceling.
- **P&L** = revenue minus cost of goods minus expenses = your profit, per store and per company.
- **Owed to VIP / Reimbursement** = money still coming to you (or that you owe a vendor) on devices.

## Tips / gotchas

- After any change is pushed, wait for the system to finish deploying before testing.
- Months are labeled by name ("June 2026"). If a report looks empty, it's usually a date/period
  mismatch, not missing money — re-pick the month or re-upload.
- If a store shows up twice or under the wrong name, use **Store Matching** to map it — no SQL needed.
- A sweep that says "0 rows" is treated as a problem, not success — the system will keep your existing
  data rather than wipe it.
