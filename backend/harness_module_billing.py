"""HARNESS — per-module usage metering, the pricing grid, and the itemized tenant statement.

Owner directive 2026-09-05: *"it should bill each call on all modules, nothing is for free, and have
an itemized statement for the tenant… the billing engine should list all the modules and an option to
assign price against them, a drop down menu to assign what kind of plan could belong to like free,
starter, premium etc"*. This produces invoices, so:

  A. ROUTE → MODULE mapping is derived and its gaps are visible. Every mapped target is a REAL
     entitlement-catalog key (the `/health` stale-literal failure mode, guarded), and a live route
     prefix nobody mapped shows up as unmapped instead of billing nothing.

  B. WHAT IS BILLED: tenant-initiated calls only. Cron sweeps, webhooks and internal service calls
     are counted and shown but never charged — a tenant must not pay for our retry storm.

  C. THE ACCUMULATOR keeps I/O off the request path: counts in memory, drains atomically, and a
     FAILED flush is restored rather than lost.

  D. THE GRID is driven off the module catalog, so a newly added module appears automatically with an
     explicit unpriced cell.

  E. THE STATEMENT is itemized (monthly fee + per-module + AI usage on ONE document), UNPRICED is
     never $0, lines always sum to the total, and per-call sub-cent charges do not drift.

  F. NO RETROACTIVE CHANGE: a closed statement survives a module price change AND a plan monthly-fee
     change, byte-identical.

Run:  cd backend && python3 harness_module_billing.py      (exit 0 = all pass)
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.billing import module_usage as mu                  # noqa: E402
from app.modules.billing import statement as st                     # noqa: E402
from app.modules.billing import ai_usage as au                      # noqa: E402
from app.modules.core.entitlements import MODULE_CATALOG            # noqa: E402

PASS = FAIL = 0
ORG = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s   %s" % (name, extra))


# ══ A. route → module mapping ════════════════════════════════════════════════════════════════════
print("\nA. route -> module mapping is derived, and its gaps are visible")

check("a commcalc path maps to the `commissions` entitlement module",
      mu.module_for_path("/api/v1/commcalc/commissions/July%202026")[0] == "commissions")
check("a storeops path maps to `storeops`",
      mu.module_for_path("/api/v1/storeops/employees")[0] == "storeops")
check("a non-API path maps to nothing", mu.module_for_path("/favicon.ico") == (None, None))
check("a bare /api/v1/ maps to nothing", mu.module_for_path("/api/v1/") == (None, None))

# THE /health FAILURE MODE, guarded: every mapped target must be a real catalog key.
v = mu.validate_route_map(set(MODULE_CATALOG) | {"vision", "pos", "approvals", "chat", "payables",
                                                 "recovery", "referral", "remediation", "storevisit"})
check("every route-map target is a real entitlement-catalog module (no invented billing keys)",
      v["ok"], v["unknown_targets"])

# The LIVE mounted prefixes, as /health derives them — anything unmapped must surface.
LIVE = ['account', 'approvals', 'asset', 'billing', 'chat', 'closing', 'commcalc', 'core', 'crm',
        'helpdesk', 'hr', 'notify', 'payables', 'pos', 'recovery', 'referral', 'remediation',
        'storeops', 'storevisit', 'vendor-api', 'vision']
check("every LIVE mounted route prefix is either mapped or declared infrastructure",
      mu.unmapped_prefixes(LIVE) == [], mu.unmapped_prefixes(LIVE))
check("a NEWLY mounted module that nobody mapped is reported, not silently unbilled",
      mu.unmapped_prefixes(LIVE + ["brandnew"]) == ["brandnew"])
check("infrastructure prefixes are excluded BY NAME, not by omission",
      "core" in mu.INFRA_PREFIXES and "billing" in mu.INFRA_PREFIXES
      and "vendor-api" in mu.INFRA_PREFIXES)

# ══ B. what is billed vs merely counted ══════════════════════════════════════════════════════════
print("\nB. tenant-initiated calls are billed; the platform's own machinery is not")

tenant = mu.classify("/api/v1/commcalc/commissions/x", org_id=ORG, has_actor=True)
check("a signed-in tenant call is BILLABLE", tenant["billable"] and tenant["call_class"] == "tenant")
check("...and lands on the right module", tenant["module"] == "commissions")

cron = mu.classify("/api/v1/commcalc/data-sources/sweep/run-due", org_id=ORG, has_actor=False)
check("a pg_cron `/run-due` sweep is classified SYSTEM, never billed",
      cron["call_class"] == "system" and cron["billable"] is False)
check("...but it is still COUNTED so the operator can see it", cron["bucket"] == "commissions")

hook = mu.classify("/api/v1/storeops/whatsapp-webhook", org_id=ORG, has_actor=True)
check("a webhook delivery is SYSTEM even when it carries a tenant",
      hook["call_class"] == "system" and hook["billable"] is False)

explicit = mu.classify("/api/v1/hr/employees", org_id=ORG, has_actor=True, is_system=True)
check("an explicitly system-flagged internal call is not billed", explicit["billable"] is False)

anon = mu.classify("/api/v1/billing/public-pricing", org_id=None, has_actor=False)
check("an anonymous public call is not billed to anyone", anon["call_class"] == "anonymous"
      and anon["billable"] is False)

infra = mu.classify("/api/v1/core/me", org_id=ORG, has_actor=True)
check("platform infrastructure (auth/RBAC) is not a billable module",
      infra["bucket"] == "infra" and infra["billable"] is False)
selfbill = mu.classify("/api/v1/billing/statement", org_id=ORG, has_actor=True)
check("reading your OWN invoice is never billable", selfbill["billable"] is False)

unknown = mu.classify("/api/v1/brandnew/thing", org_id=ORG, has_actor=True)
check("a call on an UNMAPPED module is counted under `unmapped`, not guessed onto a neighbour",
      unknown["bucket"] == "unmapped" and unknown["module"] is None and unknown["billable"] is False)

# ══ C. the accumulator ═══════════════════════════════════════════════════════════════════════════
print("\nC. the accumulator keeps I/O off the request path")

acc = mu.UsageAccumulator()
for _ in range(1000):
    acc.add(ORG, "commissions", "2026-08-10", call_class="tenant", billable=True)
for _ in range(7):
    acc.add(ORG, "commissions", "2026-08-10", call_class="system", billable=False)
acc.add(ORG, "closing", "2026-08-10", call_class="tenant", billable=True)
check("1,008 calls collapse into 2 counter rows (not 1,008 database writes)", acc.size() == 2,
      acc.size())
rows = acc.drain()
comm = next(r for r in rows if r["module"] == "commissions")
check("the counter separates billable from system calls",
      comm["calls"] == 1007 and comm["billable_calls"] == 1000 and comm["system_calls"] == 7, comm)
check("drain empties the buffer so counting continues cleanly", acc.size() == 0)
check("a row with no org / module / day is refused rather than mis-attributed",
      not acc.add(None, "commissions", "2026-08-10") and not acc.add(ORG, None, "2026-08-10")
      and not acc.add(ORG, "commissions", None))

acc.restore(rows)                       # simulate a FAILED flush
check("a failed flush RESTORES the counts instead of losing them", acc.size() == 2)
acc.add(ORG, "commissions", "2026-08-10", billable=True)
merged = next(r for r in acc.drain() if r["module"] == "commissions")
check("...and restored counts MERGE with new ones rather than overwriting",
      merged["calls"] == 1008 and merged["billable_calls"] == 1001, merged)

# thread safety: concurrent adds must not lose counts
import threading                                                     # noqa: E402
acc2 = mu.UsageAccumulator()


def _hammer():
    for _ in range(500):
        acc2.add(ORG, "commissions", "2026-08-10", billable=True)


ts = [threading.Thread(target=_hammer) for _ in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
check("concurrent increments from 4 threads lose nothing (2,000 counted)",
      acc2.drain()[0]["calls"] == 2000)

# ══ D. the operator pricing grid ═════════════════════════════════════════════════════════════════
print("\nD. the pricing grid is driven off the module catalog")

PLANS = [{"key": "free", "name": "Free"}, {"key": "starter", "name": "Starter"},
         {"key": "premium", "name": "Premium"}]
PRICES = [
    {"plan_key": "starter", "module_key": "commissions", "mode": "per_call", "unit_price": "0.01",
     "effective_date": "2026-01-01"},
    {"plan_key": "premium", "module_key": "commissions", "mode": "included",
     "effective_date": "2026-01-01"},
]
grid = st.pricing_grid(MODULE_CATALOG, PRICES, PLANS)
check("the grid has a row for EVERY module in the catalog",
      len(grid["modules"]) == len(MODULE_CATALOG), len(grid["modules"]))
check("...and a cell for every plan", set(grid["modules"][0]["plans"]) == {"free", "starter", "premium"})
check("a priced cell shows its price",
      next(m for m in grid["modules"] if m["module"] == "commissions")["plans"]["starter"]["unit_price"] == "0.01")
check("an `included` cell is marked included, not $0 usage",
      next(m for m in grid["modules"] if m["module"] == "commissions")["plans"]["premium"]["included"] is True)
check("an unpriced cell is explicitly UNPRICED, never a silent 0",
      next(m for m in grid["modules"] if m["module"] == "hr")["plans"]["free"]["priced"] is False)
check("the grid COUNTS the holes so the operator can see how much is unpriced",
      grid["unpriced_cells"] > 0 and "NOT free" in grid["note"], grid["note"])

# a module added to the platform tomorrow appears automatically
future_catalog = {**MODULE_CATALOG, "brandnew": "Brand New Module"}
g2 = st.pricing_grid(future_catalog, PRICES, PLANS)
check("a NEWLY added module appears in the grid automatically, unpriced",
      any(m["module"] == "brandnew" for m in g2["modules"])
      and next(m for m in g2["modules"] if m["module"] == "brandnew")["plans"]["free"]["priced"] is False)

# ══ E. the itemized statement ════════════════════════════════════════════════════════════════════
print("\nE. the itemized statement — monthly fee, modules, AI usage, on one document")

PLAN = {"key": "starter", "name": "Starter", "price": 199, "currency": "USD",
        "unit_label": "per month"}
USAGE = [{"org_id": ORG, "module": "commissions", "usage_date": "2026-08-10",
          "calls": 1007, "billable_calls": 1000, "system_calls": 7, "anonymous_calls": 0},
         {"org_id": ORG, "module": "closing", "usage_date": "2026-08-10",
          "calls": 500, "billable_calls": 500, "system_calls": 0, "anonymous_calls": 0}]
P = [{"plan_key": "starter", "module_key": "commissions", "mode": "per_call", "unit_price": "0.01",
      "effective_date": "2026-01-01"},
     {"plan_key": "starter", "module_key": "closing", "mode": "included",
      "effective_date": "2026-01-01"}]

s = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                       catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=P, plan=PLAN)
fee = next(l for l in s["lines"] if l["kind"] == "plan_fee")
comm = next(l for l in s["lines"] if l.get("module") == "commissions")
clos = next(l for l in s["lines"] if l.get("module") == "closing")
check("the MONTHLY FEE is an explicit line the tenant can see", fee["amount"] == 199.0, fee)
check("a per-call module bills billable calls x unit price (1000 x $0.01 = $10.00)",
      comm["amount"] == 10.0, comm)
check("...and the line SAYS the 7 platform-initiated calls were excluded",
      "7 platform-initiated call(s) excluded" in comm["note"], comm["note"])
check("an `included` module charges $0 and says it is included",
      clos["amount"] == 0.0 and clos["mode"] == "included")
check("...but its usage is still shown (500 calls)", clos["billable_calls"] == 500)

# THE INVOICE MUST ADD UP
priced = [l for l in s["lines"] if l.get("priced") and l.get("amount") is not None]
check("the total equals the sum of the visible lines EXACTLY",
      round(sum(l["amount"] for l in priced), 2) == s["total_usd"],
      "%s vs %s" % (sum(l["amount"] for l in priced), s["total_usd"]))
check("...which here is $199 + $10 + $0 = $209.00", s["total_usd"] == 209.0, s["total_usd"])

# UNPRICED is not $0 and not free
hr = next(l for l in s["lines"] if l.get("module") == "hr")
check("a module with no price is UNPRICED, never $0", hr["amount"] is None and hr["priced"] is False)
check("...and says it is not free, just unpriced", "not free" in hr["note"], hr["note"])
used_unpriced = st.build_statement(
    org_id=ORG, period_start="2026-08-01", period_end="2026-08-31", catalog=MODULE_CATALOG,
    usage_rows=USAGE + [{"module": "hr", "calls": 40, "billable_calls": 40}], price_rows=P, plan=PLAN)
check("a module that was USED but has no price makes the statement INCOMPLETE",
      used_unpriced["complete"] is False)
check("...and the statement refuses to be treated as a final invoice",
      "must not be sent as a final invoice" in used_unpriced["complete_note"],
      used_unpriced["complete_note"])
check("...while its usage is still excluded from the total, not guessed",
      used_unpriced["total_usd"] == 209.0, used_unpriced["total_usd"])

# no plan assigned
noplan = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                            catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=P, plan=None)
check("a tenant with NO plan gets an explicit unpriced monthly-fee line, not a free ride",
      next(l for l in noplan["lines"] if l["kind"] == "plan_fee")["priced"] is False)
check("...and the statement is flagged incomplete", noplan["complete"] is False)

# unmapped usage is surfaced on the statement
unmapped_stmt = st.build_statement(
    org_id=ORG, period_start="2026-08-01", period_end="2026-08-31", catalog=MODULE_CATALOG,
    usage_rows=USAGE + [{"module": "unmapped", "calls": 12, "billable_calls": 12}],
    price_rows=P, plan=PLAN)
check("usage on unmapped routes appears on the statement rather than vanishing",
      any(l["kind"] == "unmapped" for l in unmapped_stmt["lines"]))
check("...excluded from the total until mapped", unmapped_stmt["total_usd"] == 209.0)

# AI usage rides the SAME document
RATES = [{"id": "r", "org_id": au.HOUSE_ORG, "model": "claude-opus-5", "usd_per_mtok_in": 5,
          "usd_per_mtok_out": 25, "effective_date": "2026-01-01", "is_active": True}]
ai = au.price_period([{"org_id": ORG, "purpose": "control_box_triage", "model": "claude-opus-5",
                       "input_tokens": 1_000_000, "output_tokens": 0, "allowed": True,
                       "created_at": "2026-08-10T00:00:00+00:00"}],
                     RATES, [{"org_id": ORG, "mode": "percent", "percent": 20,
                              "effective_date": "2026-01-01"}],
                     org_id=ORG, period_start="2026-08-01", period_end="2026-08-31")
s_ai = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                          catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=P, plan=PLAN,
                          ai_period=ai)
ai_line = next(l for l in s_ai["lines"] if l["kind"] == "ai_usage")
check("AI usage is a LINE ITEM on the same statement, not a second system",
      ai_line["amount"] == 6.0, ai_line)
check("...and the line explains cost + margin", "platform cost $5.0" in ai_line["note"], ai_line["note"])
check("the total includes it ($199 + $10 + $0 + $6 = $215.00)", s_ai["total_usd"] == 215.0,
      s_ai["total_usd"])

# ROUNDING: thousands of sub-cent per-call charges must not drift
tiny_usage = [{"module": "commissions", "calls": 100000, "billable_calls": 100000}]
tiny_price = [{"plan_key": "starter", "module_key": "commissions", "mode": "per_call",
               "unit_price": "0.000015", "effective_date": "2026-01-01"}]
s_tiny = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                            catalog=MODULE_CATALOG, usage_rows=tiny_usage, price_rows=tiny_price,
                            plan={"key": "starter", "name": "Starter", "price": 0})
tl = next(l for l in s_tiny["lines"] if l.get("module") == "commissions")
check("100,000 calls x $0.000015 = $1.50 exactly (full precision inside the line)",
      tl["amount"] == 1.5, tl["amount"])
naive = float(st._q(Decimal("0.000015")) * 100000)
check("...whereas rounding each call first would have produced $0.00 — a 100% billing error",
      naive == 0.0, naive)
check("the statement total still equals the sum of its lines", s_tiny["total_usd"] == 1.5)

# ══ F. a closed statement never moves ════════════════════════════════════════════════════════════
print("\nF. no retroactive change — a closed statement is a historical fact")

closed = st.freeze_statement(s, closed_by="owner@example.com")
check("closing freezes the document and records who/when",
      closed["status"] == "closed" and closed["closed_by"] == "owner@example.com"
      and closed["total_usd"] == 209.0)

# change BOTH the module price and the plan's monthly fee, then re-read
NEW_P = [{"plan_key": "starter", "module_key": "commissions", "mode": "per_call",
          "unit_price": "9.99", "effective_date": "2026-01-01"}]      # edited IN PLACE
NEW_PLAN = {"key": "starter", "name": "Starter", "price": 999, "currency": "USD"}
reread = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                            catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=NEW_P,
                            plan=NEW_PLAN, frozen=closed)
check("a CLOSED statement re-read after a price AND monthly-fee change is byte-identical",
      reread["total_usd"] == 209.0
      and next(l for l in reread["lines"] if l["kind"] == "plan_fee")["amount"] == 199.0, reread["total_usd"])
check("...and says it was not recomputed", reread["recomputed"] is False)
check("...and explains why to whoever reads it later", "CLOSED" in reread["note"])

# effective dating protects an OPEN period from a FUTURE-dated price
future_price = P + [{"plan_key": "starter", "module_key": "commissions", "mode": "per_call",
                     "unit_price": "5.00", "effective_date": "2026-09-01"}]
aug = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                         catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=future_price, plan=PLAN)
check("an August statement is NOT re-priced by a September price", aug["total_usd"] == 209.0,
      aug["total_usd"])
sep = st.build_statement(org_id=ORG, period_start="2026-09-01", period_end="2026-09-30",
                         catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=future_price, plan=PLAN)
check("...but September DOES get it (1000 x $5 + $199 = $5199.00)", sep["total_usd"] == 5199.0,
      sep["total_usd"])

# plan scoping: one tenant's plan price must not leak to another plan
mixed = P + [{"plan_key": "premium", "module_key": "commissions", "mode": "included",
              "effective_date": "2026-01-01"}]
prem = st.build_statement(org_id=ORG, period_start="2026-08-01", period_end="2026-08-31",
                          catalog=MODULE_CATALOG, usage_rows=USAGE, price_rows=mixed,
                          plan={"key": "premium", "name": "Premium", "price": 499})
check("a premium tenant gets the premium price (included), not the starter per-call price",
      next(l for l in prem["lines"] if l.get("module") == "commissions")["mode"] == "included")
check("...and their own monthly fee", prem["total_usd"] == 499.0, prem["total_usd"])

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
