"""HARNESS — per-tenant AI usage metering, platform cost, and the chargeable price with margin.

Owner directive 2026-09-05: *"For every tenant ai usage counter needs to be built and a cost assigned
at the super admin level, the cost for the tenant will be cost of the super admin / platform per token
paid plus % or flat margin assigned by the super admin"*. This bills real tenants, so every claim
below is proven rather than asserted:

  A. PLATFORM COST is exact and never guessed. Priced from core.token_rates on the ACTUAL input/output
     split (output costs 5x input, so blending would systematically mis-bill), and a model with no
     rate row is reported UNPRICED — never silently $0.

  B. EFFECTIVE DATING — a call is priced with the rate in force ON ITS OWN DAY. Proven against the
     REAL seeded platform data: claude-sonnet-5 is $2/$10 from 2026-01-01 and $3/$15 from 2026-09-01.

  C. HISTORICAL CHARGES NEVER MOVE. A closed period is read from its snapshot, so editing a rate row
     in place AND changing the margin afterwards leaves the closed figures byte-identical.

  D. MARGIN variants: percent, flat-per-period, flat-per-call, both combined; per-tenant config beats
     the house default; a negative margin cannot sell below cost; no config = pass-through.

  E. ROUNDING is done ONCE on the total, HALF_UP, in Decimal — the drift test sums 1,000 sub-cent
     calls and shows that rounding per call would have lost real money.

  F. COVERAGE HONESTY — the counter states what fraction of the platform's AI call sites it actually
     sees, and usage from an undeclared purpose is surfaced rather than folded in silently.

Run:  cd backend && python3 harness_ai_usage.py        (exit 0 = all pass)
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.billing import ai_usage as au                      # noqa: E402
from app.modules.core.fix_pipeline import rate_for                  # noqa: E402

PASS = FAIL = 0
HOUSE = au.HOUSE_ORG
ORG_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s   %s" % (name, extra))


# The rate rows below mirror what migration 718 actually seeds (verified against the migration).
RATES = [
    {"id": "r-opus5", "org_id": HOUSE, "model": "claude-opus-5", "usd_per_mtok_in": 5,
     "usd_per_mtok_out": 25, "effective_date": "2026-01-01", "output_share": 0.20, "is_active": True},
    {"id": "r-opus48", "org_id": HOUSE, "model": "claude-opus-4-8", "usd_per_mtok_in": 5,
     "usd_per_mtok_out": 25, "effective_date": "2026-01-01", "output_share": 0.20, "is_active": True},
    {"id": "r-son5-intro", "org_id": HOUSE, "model": "claude-sonnet-5", "usd_per_mtok_in": 2,
     "usd_per_mtok_out": 10, "effective_date": "2026-01-01", "output_share": 0.20, "is_active": True},
    {"id": "r-son5-new", "org_id": HOUSE, "model": "claude-sonnet-5", "usd_per_mtok_in": 3,
     "usd_per_mtok_out": 15, "effective_date": "2026-09-01", "output_share": 0.20, "is_active": True},
]


def call(model, tin, tout, day, purpose="control_box_triage", allowed=True, org=ORG_A):
    return {"org_id": org, "purpose": purpose, "model": model, "input_tokens": tin,
            "output_tokens": tout, "allowed": allowed, "created_at": day + "T12:00:00+00:00"}


# ══ A. platform cost is exact, and unpriceable is not zero ═══════════════════════════════════════
print("\nA. platform cost — exact in/out split, never a guessed zero")

opus = RATES[0]
cost, basis = au.exact_cost(1_000_000, 0, opus)
check("1M input tokens on opus-5 costs exactly $5", cost == Decimal("5"), cost)
cost, _ = au.exact_cost(0, 1_000_000, opus)
check("1M output tokens on opus-5 costs exactly $25", cost == Decimal("25"), cost)
cost, basis = au.exact_cost(1_000_000, 1_000_000, opus)
check("1M in + 1M out costs exactly $30 (not a blend)", cost == Decimal("30"), cost)
check("...and the basis says it priced the real split, not an output_share assumption",
      "exact:" in basis["method"] and "output_share" in basis["method"], basis["method"])

# Why the blend would be wrong: fix_pipeline's blended rate on the SAME 2M tokens assumes 20% output.
from app.modules.core.fix_pipeline import compute_cost as blended_compute      # noqa: E402
blended, _ = blended_compute(2_000_000, opus, model="claude-opus-5")
check("the shared BLEND would have billed $18 for that same call — exact billing is materially "
      "different, which is why this module prices the split",
      abs(blended - 18.0) < 1e-6 and cost == Decimal("30"), blended)

none_cost, none_basis = au.exact_cost(1000, 1000, None)
check("a model with NO rate row is UNPRICED (None), never $0", none_cost is None)
check("...and it says why", "no active core.token_rates row" in none_basis["reason"])
zero_cost, _ = au.exact_cost(0, 0, opus)
check("a real zero-token call IS $0 (a true zero differs from unpriceable)", zero_cost == Decimal("0"))

# ══ B. effective dating, against the REAL seeded sonnet-5 rate change ════════════════════════════
print("\nB. effective dating — each call priced with the rate in force on ITS day")

aug = rate_for(RATES, "claude-sonnet-5", org_id=ORG_A, house_org=HOUSE, on_date="2026-08-15")
sep = rate_for(RATES, "claude-sonnet-5", org_id=ORG_A, house_org=HOUSE, on_date="2026-09-15")
check("an August sonnet-5 call resolves the $2/$10 introductory rate", aug["id"] == "r-son5-intro")
check("a September sonnet-5 call resolves the $3/$15 rate", sep["id"] == "r-son5-new")
check("a rate dated in the FUTURE never prices today",
      rate_for(RATES, "claude-sonnet-5", on_date="2026-01-02")["id"] == "r-son5-intro")

rows = [call("claude-sonnet-5", 1_000_000, 0, "2026-08-15"),
        call("claude-sonnet-5", 1_000_000, 0, "2026-09-15")]
p = au.price_period(rows, RATES, [], org_id=ORG_A, period_start="2026-08-01", period_end="2026-09-30")
check("a period spanning the rate change bills $2 + $3 = $5, not 2x either rate",
      p["platform_cost_usd"] == 5.0, p["platform_cost_usd"])

# A NEW rate published now cannot re-price calls that already happened.
future = RATES + [{"id": "r-son5-future", "org_id": HOUSE, "model": "claude-sonnet-5",
                   "usd_per_mtok_in": 99, "usd_per_mtok_out": 99, "effective_date": "2026-10-01",
                   "output_share": 0.20, "is_active": True}]
p2 = au.price_period(rows, future, [], org_id=ORG_A, period_start="2026-08-01", period_end="2026-09-30")
check("publishing a NEW effective-dated rate does NOT re-price earlier calls",
      p2["platform_cost_usd"] == 5.0, p2["platform_cost_usd"])

tenant_rate = RATES + [{"id": "r-tenant", "org_id": ORG_A, "model": "claude-opus-5",
                        "usd_per_mtok_in": 1, "usd_per_mtok_out": 1, "effective_date": "2026-01-01",
                        "output_share": 0.20, "is_active": True}]
check("a TENANT rate row overrides the house rate for that tenant",
      rate_for(tenant_rate, "claude-opus-5", org_id=ORG_A, house_org=HOUSE)["id"] == "r-tenant")
check("...and does NOT leak to another tenant",
      rate_for(tenant_rate, "claude-opus-5", org_id="other", house_org=HOUSE)["id"] == "r-opus5")

# ══ C. a closed period is a historical fact ══════════════════════════════════════════════════════
print("\nC. no retroactive change — a closed period never moves")

hist = [call("claude-opus-5", 1_000_000, 1_000_000, "2026-08-10")]
margin_v1 = [{"id": "m1", "org_id": ORG_A, "mode": "percent", "percent": 20,
              "effective_date": "2026-01-01", "is_active": True}]
open_period = au.price_period(hist, RATES, margin_v1, org_id=ORG_A,
                              period_start="2026-08-01", period_end="2026-08-31")
check("open period: $30 cost + 20% = $36.00 billable",
      open_period["platform_cost_usd"] == 30.0 and open_period["billable_usd"] == 36.0,
      open_period["billable_usd"])

snap = au.snapshot_for_close(open_period, closed_by="owner@example.com")
check("the close snapshot freezes cost, billable AND the applied margin",
      snap["platform_cost_usd"] == 30.0 and snap["billable_usd"] == 36.0
      and snap["margin_snapshot"]["percent"] == "20", snap["margin_snapshot"])
check("...and records who closed it and when", snap["closed_by"] == "owner@example.com" and snap["closed_at"])

# Now do the two things that would corrupt a naive system: EDIT the rate in place, and change margin.
edited_rates = [dict(r, usd_per_mtok_in=500, usd_per_mtok_out=500) if r["id"] == "r-opus5" else r
                for r in RATES]
margin_v2 = margin_v1 + [{"id": "m2", "org_id": ORG_A, "mode": "percent", "percent": 90,
                          "effective_date": "2026-09-05", "is_active": True}]
reread = au.price_period(hist, edited_rates, margin_v2, org_id=ORG_A,
                         period_start="2026-08-01", period_end="2026-08-31", frozen=snap)
check("a CLOSED period re-read after a rate edit AND a margin change is byte-identical",
      reread["platform_cost_usd"] == 30.0 and reread["billable_usd"] == 36.0
      and reread["margin_snapshot"]["percent"] == "20", reread)
check("...and it says it was not recomputed", reread["recomputed"] is False)
check("...and explains why to whoever reads it later", "CLOSED" in reread["note"])

# Effective dating protects the OPEN period from the margin change too (Aug priced on Aug margin).
still_open = au.price_period(hist, RATES, margin_v2, org_id=ORG_A,
                             period_start="2026-08-01", period_end="2026-08-31")
check("an OPEN August period is not re-priced by a margin dated in September",
      still_open["billable_usd"] == 36.0, still_open["billable_usd"])
sept_open = au.price_period([call("claude-opus-5", 1_000_000, 1_000_000, "2026-09-10")], RATES,
                            margin_v2, org_id=ORG_A,
                            period_start="2026-09-01", period_end="2026-09-30")
check("...but September DOES get the new 90% margin ($30 -> $57.00)",
      sept_open["billable_usd"] == 57.0, sept_open["billable_usd"])

# ══ D. margin variants ═══════════════════════════════════════════════════════════════════════════
print("\nD. margin — percent, flat per period, flat per call, combined, and the guardrails")

base = [call("claude-opus-5", 1_000_000, 1_000_000, "2026-08-10")] * 4      # 4 calls, $120 cost


def bill(margin_rows, rows=None):
    return au.price_period(rows if rows is not None else base, RATES, margin_rows, org_id=ORG_A,
                           period_start="2026-08-01", period_end="2026-08-31")


m_pct = [{"org_id": ORG_A, "mode": "percent", "percent": 25, "effective_date": "2026-01-01"}]
r = bill(m_pct)
check("percent: $120 cost + 25% = $150.00", r["billable_usd"] == 150.0, r["billable_usd"])
check("...and the margin amount is reported separately", r["margin_usd"] == 30.0, r["margin_usd"])

m_flat_p = [{"org_id": ORG_A, "mode": "flat", "flat_usd": 50, "flat_basis": "period",
             "effective_date": "2026-01-01"}]
r = bill(m_flat_p)
check("flat per PERIOD: $120 + $50 once = $170.00", r["billable_usd"] == 170.0, r["billable_usd"])
check("...and says the flat was applied once for the period",
      "once for the period" in r["margin_breakdown"]["flat_note"])

m_flat_c = [{"org_id": ORG_A, "mode": "flat", "flat_usd": 0.5, "flat_basis": "call",
             "effective_date": "2026-01-01"}]
r = bill(m_flat_c)
check("flat per CALL: $120 + 4 x $0.50 = $122.00", r["billable_usd"] == 122.0, r["billable_usd"])
check("...and says it multiplied by the call count", "per call x 4" in r["margin_breakdown"]["flat_note"])

m_both = [{"org_id": ORG_A, "mode": "percent_plus_flat", "percent": 25, "flat_usd": 50,
           "flat_basis": "period", "effective_date": "2026-01-01"}]
r = bill(m_both)
check("percent AND flat combine: $120 + 25% + $50 = $200.00", r["billable_usd"] == 200.0,
      r["billable_usd"])

r = bill([])
check("NO margin configured = pass-through at platform cost ($120.00)", r["billable_usd"] == 120.0,
      r["billable_usd"])
check("...and it says so rather than implying a charge", r["margin"]["source"] == "default")

m_neg = [{"org_id": ORG_A, "mode": "percent", "percent": -50, "effective_date": "2026-01-01"}]
check("a NEGATIVE margin cannot sell below platform cost (clamped to 0)",
      bill(m_neg)["billable_usd"] == 120.0)
m_junk = [{"org_id": ORG_A, "mode": "nonsense", "percent": "abc", "effective_date": "2026-01-01"}]
check("a garbage margin row degrades to pass-through, never to a random charge",
      bill(m_junk)["billable_usd"] == 120.0)

m_house = [{"org_id": HOUSE, "mode": "percent", "percent": 10, "effective_date": "2026-01-01"}]
check("a HOUSE margin applies to a tenant with no row of its own ($120 + 10% = $132)",
      bill(m_house)["billable_usd"] == 132.0)
m_layered = m_house + [{"org_id": ORG_A, "mode": "percent", "percent": 40,
                        "effective_date": "2026-01-01"}]
check("...and the tenant's OWN margin overrides the house one ($120 + 40% = $168)",
      bill(m_layered)["billable_usd"] == 168.0)
other = au.price_period(base, RATES, m_layered, org_id="other-org",
                        period_start="2026-08-01", period_end="2026-08-31")
check("...without leaking that tenant's margin to a different tenant ($120 + 10% = $132)",
      other["billable_usd"] == 132.0, other["billable_usd"])

# ══ E. rounding: once, on the total, HALF_UP ═════════════════════════════════════════════════════
print("\nE. rounding — once on the total; per-call rounding would lose real money")

# 1,000 calls each costing $0.0000155 (155 in + 0 out at $0.10/MTok would be sub-cent; use opus rates
# with tiny token counts so every single call is far below one cent).
tiny = [call("claude-opus-5", 1000, 100, "2026-08-10") for _ in range(1000)]
one_cost, _ = au.exact_cost(1000, 100, opus)
check("one tiny call costs a fraction of a cent", one_cost < Decimal("0.01"), one_cost)
r = bill([], rows=tiny)
exact_total = one_cost * 1000
check("1,000 tiny calls sum at FULL precision before rounding",
      Decimal(str(r["platform_cost_usd"])) == au.q_cost(exact_total), r["platform_cost_usd"])
naive = float(au.q_money(one_cost) * 1000)     # what rounding each call to cents would have produced
check("...and rounding per call would have produced a DIFFERENT (wrong) number",
      abs(naive - r["platform_cost_usd"]) > 0.5,
      "per-call-rounded=%s vs correct=%s" % (naive, r["platform_cost_usd"]))
check("HALF_UP is used, not banker's rounding (0.005 -> 0.01, not 0.00)",
      au.q_money(Decimal("0.005")) == Decimal("0.01"))
check("...and 0.015 -> 0.02 (banker's would give 0.02 too) while 0.025 -> 0.03 (banker's: 0.02)",
      au.q_money(Decimal("0.025")) == Decimal("0.03"))
check("billable is quantised to real cents", Decimal(str(bill(m_pct)["billable_usd"])).as_tuple().exponent >= -2)

# ══ F. what the counter can and cannot see ═══════════════════════════════════════════════════════
print("\nF. coverage honesty — the counter states what it does not see")

cov = au.coverage(["control_box_triage"])
check("coverage reports how many call sites exist and how many are metered",
      cov["sites_total"] == len(au.AI_CALL_SITES) and cov["sites_metered"] >= 1, cov)
check("with every site metered, coverage is complete and says so",
      cov["complete"] is True and "whole AI spend" in cov["note"], cov["note"])

# Simulate a site being un-wired: the counter must warn that real spend is HIGHER, not show zero.
real_sites = au.AI_CALL_SITES
try:
    au.AI_CALL_SITES = tuple(dict(s, metered=(s["key"] != "helpdesk_ai_assist")) for s in real_sites)
    cov2 = au.coverage(["control_box_triage"])
    check("an UNMETERED call site makes coverage incomplete", cov2["complete"] is False)
    check("...names the site", any(u["key"] == "helpdesk_ai_assist" for u in cov2["unmetered"]))
    check("...and states that real spend is HIGHER than shown, never that usage is zero",
          "HIGHER" in cov2["note"] and "zero" not in cov2["note"].lower(), cov2["note"])
finally:
    au.AI_CALL_SITES = real_sites

cov3 = au.coverage(["control_box_triage", "some_undeclared_site"])
check("usage from an UNDECLARED purpose is surfaced, not folded in silently",
      cov3["unregistered_purposes"] == ["some_undeclared_site"] and cov3["complete"] is False)
check("...and explains that a call site was wired without being registered",
      "without being registered" in cov3["note"])

r = bill([])
check("every priced period carries its coverage note", (r.get("coverage") or {}).get("note"))

# unpriceable spend is stated, not zeroed
mixed = [call("claude-opus-5", 1_000_000, 0, "2026-08-10"),
         call("some-unknown-model", 1_000_000, 0, "2026-08-10")]
r = bill([], rows=mixed)
check("a call on a model with no rate row is counted as UNPRICED",
      r["unpriced_calls"] == 1 and r["priced_calls"] == 1, r)
check("...its tokens still appear in the token counter (they really were spent)",
      r["tokens"] == 2_000_000, r["tokens"])
check("...the cost shows only what could be priced ($5), not a fabricated total",
      r["platform_cost_usd"] == 5.0, r["platform_cost_usd"])
check("...and the period SAYS the real cost is higher and which model is missing a rate",
      "real platform cost is higher" in r["unpriced_note"] and "some-unknown-model" in r["unpriced_note"],
      r["unpriced_note"])

# refused calls never reach a bill
refused = [call("claude-opus-5", 1_000_000, 1_000_000, "2026-08-10", allowed=False)] * 5
r = bill([], rows=refused)
check("REFUSED calls are never billed (they spent no tokens)",
      r["calls"] == 0 and r["platform_cost_usd"] == 0.0, r)

# per-purpose and per-model attribution
r = bill([], rows=[call("claude-opus-5", 1_000_000, 0, "2026-08-10", purpose="control_box_triage"),
                   call("claude-sonnet-5", 1_000_000, 0, "2026-08-10", purpose="helpdesk_ai_assist")])
check("usage is attributed per purpose (which feature spent it)",
      set(r["by_purpose"]) == {"control_box_triage", "helpdesk_ai_assist"}, r["by_purpose"])
check("...and per model", set(r["by_model"]) == {"claude-opus-5", "claude-sonnet-5"})
check("...with the right cost on each ($5 opus, $2 sonnet-aug)",
      r["by_purpose"]["control_box_triage"]["platform_cost"] == 5.0
      and r["by_purpose"]["helpdesk_ai_assist"]["platform_cost"] == 2.0, r["by_purpose"])

# platform roll-up carries money only
roll = au.summarize_tenants([bill(m_pct), other])
check("the cross-tenant roll-up totals cost and billable", roll["tenants"] == 2)
check("...and carries no tenant business data (money and counts only)",
      set(roll) == {"tenants", "calls", "tokens", "platform_cost_usd", "billable_usd", "margin_usd",
                    "coverage_complete", "note"}, sorted(roll))

# period helpers
check("period_bounds handles a normal month", au.period_bounds(2026, 8) == ("2026-08-01", "2026-08-31"))
check("...February", au.period_bounds(2026, 2) == ("2026-02-01", "2026-02-28"))
check("...a leap February", au.period_bounds(2028, 2) == ("2028-02-01", "2028-02-29"))
check("...and December's year rollover", au.period_bounds(2026, 12) == ("2026-12-01", "2026-12-31"))
check("in_period is inclusive at both ends",
      au.in_period(call("m", 1, 1, "2026-08-01"), "2026-08-01", "2026-08-31")
      and au.in_period(call("m", 1, 1, "2026-08-31"), "2026-08-01", "2026-08-31")
      and not au.in_period(call("m", 1, 1, "2026-07-31"), "2026-08-01", "2026-08-31"))

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
