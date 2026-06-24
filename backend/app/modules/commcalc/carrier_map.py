"""Carrier category-mapping engine — SaaS framework Phase 1.

Maps a raw carrier compensation-category string to one of the 4 canonical components
(RESIDUAL / COMMISSION / SPIFF / REIMBURSEMENT) via commcalc.carrier_category_map, evaluated
most-specific-first (priority asc). Onboarding a new carrier = adding rows, no code change.
"""
import re

COMPONENTS = ("RESIDUAL", "COMMISSION", "SPIFF", "REIMBURSEMENT")


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
