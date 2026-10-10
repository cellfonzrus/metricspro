"""Carrier category-mapping engine — SaaS framework Phase 1.

Maps a raw carrier compensation-category string to one of the canonical components via
commcalc.carrier_category_map, evaluated most-specific-first (priority asc). Onboarding a new
carrier = adding rows, no code change.

`COMPONENTS` IS THE PLATFORM'S WHOLE VOCABULARY FOR "WHAT KIND OF CARRIER MONEY IS THIS" — the one
place it is spelled, dereferenced by §58's `carrier_dollar_class` and by every surface that routes a
carrier dollar to a P&L line or a Gross-Profit column.

WHY IT GREW PAST FOUR (index §69). A tenant declares its OWN category for each payment type
(`commcalc.payment_categories`, §57), and §58's ruling is that the org's declaration beats the
keyword ladder below. That ruling can only be HONOURED for a category the vocabulary can express:
with four components, a category outside them resolved to no component, so §58 fell back to this
ladder and a platform keyword guess stood where the org's own word already existed
(`basis='declared_category_unmapped'`). Measured live 2026-10-10 on the house org, that cost
$260,500.00 of market-development money its own declaration calls MDF: it booked on the
device-financing reimbursement line.

So the vocabulary covers every kind of carrier money the platform's own routing maps can name a
destination for — which is a STRUCTURAL rule, locked by
`harness_declared_category_honourable.py`: every Gross-Profit money column except the
unclassified one must be reachable from some component, or a dollar belonging in it has no
component to be declared into and must be placed by a guess again.

RULE TWO. Not one carrier, tenant or product name here or in the rows' meaning: these are kinds of
money (a residual, a commission, a spiff, a reimbursement, market-development funds, a chargeback),
and WHICH kind a tenant's category means is a config row, never a branch.
"""
import re

COMPONENTS = ("RESIDUAL", "COMMISSION", "SPIFF", "REIMBURSEMENT", "MDF", "CHARGEBACK")


def load_rules(client, org_id, carrier_id=None):
    """Active rules for the org, ordered by priority (lower first). carrier_id filters to that
    carrier's rules + any carrier-agnostic (NULL carrier_id) fallback rules."""
    rows = (client.schema("commcalc").table("carrier_category_map").select("*")
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    if carrier_id:
        rows = [r for r in rows if (not r.get("carrier_id")) or r.get("carrier_id") == carrier_id]
    return sorted(rows, key=lambda r: r.get("priority") if r.get("priority") is not None else 100)


def match_rule(rules, raw_category):
    """First matching rule (rules already priority-ordered), or None."""
    s = (raw_category or "").strip()
    if not s:
        return None
    low = s.lower()
    for r in rules:
        pat = (r.get("raw_category") or "").strip()
        if not pat:
            continue
        mt = (r.get("match_type") or "exact").lower()
        pl = pat.lower()
        if mt == "exact" and low == pl:
            return r
        if mt == "prefix" and low.startswith(pl):
            return r
        if mt == "contains" and pl in low:
            return r
        if mt == "regex":
            try:
                if re.search(pat, s, re.I):
                    return r
            except re.error:
                pass
    return None


def classify(rules, raw_category):
    r = match_rule(rules, raw_category)
    return {"component": r.get("component"), "subtype": r.get("subtype")} if r else {"component": None, "subtype": None}
