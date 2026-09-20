"""Activation-Details TYPE bucketing — PURE + CONFIG-DRIVEN (RULE TWO; mig 313).

Extracted from router._activation_details_bucket (2026-09-02) so the classification is (a) provable
by a DB-free harness (harness_activation_bucketing.py) and (b) steered by per-org CONFIG
(commcalc.accessory_config.activation_details_rules, mig 313) instead of hard-coded token branches.

WHAT CHANGED vs the pre-313 hardcode (owner-approved fix, 2026-09-01 recon report):

1. EDGE over-match: the old rule matched the token 'edge' in the PRODUCT/PLAN NAME as well as the
   Contract Type, so every "Motorola Edge 2025" Port/Activation/Upgrade DEVICE landed in the Edge
   column (LuxeLink Aug-2026: all 16 "Edge" rows were Motorola Edge handsets, zero were Edge-program
   contracts). The b2b portal's own Edge column comes from the CONTRACT TYPE, so the HOUSE DEFAULT
   now matches 'edge' as a whole word in contract_type ONLY.
   TRADE-OFF: a tenant whose Edge-program lines are identifiable only by plan/product NAME would
   undercount Edge under the default — that tenant sets `edge_name_tokens` (e.g. ["edge plan"]) in
   its activation_details_rules config row and gets name matching back, scoped to its own org.
   No org-config = the house default above (the fixed behavior).

2. 'BYOD Upgrade' in the displayed Upgrade column: b2b's location report shows fewer Upgrades than
   we did because 'BYOD Upgrade' contracts are their own family. They stay EXCLUDED from Total
   Activation exactly like Upgrade (TA semantics identical) but no longer inflate the displayed
   Upgrade column — they classify to the separate 'BYOD Upgrade' bucket, which every consumer
   (_ad_cells_full / _AD_EXEC_KEY / _ACT_BUCKET_FIELD) carries as its own hidden `byod_upgrade`
   field. Config: `upgrade_hidden_contract_tokens` (contains-match on contract_type; [] restores
   the old single-Upgrade-family behavior for an org).

Everything here is stdlib-pure: no DB, no FastAPI — the proof harness imports this file directly.
"""
import re

# HOUSE DEFAULTS (org 00000000-…-01 posture): what an org with no activation_details_rules config
# row gets. Tokens are lowercase; matching lowercases the inputs.
HOUSE_DEFAULT_RULES = {
    # Whole-word contains on Contract Type → Edge. ('edge' must stand alone: 'Edge Activation'
    # matches, 'Knowledge' / 'Motorola Edge 2025' [a product NAME, not a contract type] do not.)
    "edge_contract_tokens": ["edge"],
    # Substring tokens over the NAME text (SP/PO name + product desc + category) → Edge.
    # DEFAULT EMPTY — this is the fix for the Motorola-Edge-device over-match; a tenant that really
    # names its Edge-program lines can opt name-matching back in per org.
    "edge_name_tokens": [],
    # Contract-type contains-tokens that route an Upgrade-family row to the hidden 'BYOD Upgrade'
    # bucket (excluded from Total Activation like Upgrade, but NOT shown in the Upgrade column).
    "upgrade_hidden_contract_tokens": ["byod upgrade"],
}

# Bucket precedence for de-duping a Serial# that appears on more than one line: the STRONGEST
# classification wins, so a device with both an Upgrade line and an Activation/Port/BYOD line counts
# as the real activation (never silently dropped into an excluded family by row order). 'BYOD
# Upgrade' ranks WITH Upgrade (0) — both are the excluded families, weakest by design.
BUCKET_RANK = {"Home Internet": 6, "Edge": 6, "Tablet": 6, "BYOD": 4, "Port": 3,
               "New Activation": 2, "Other": 1, "Upgrade": 0, "BYOD Upgrade": 0}

# The families excluded from b2b's Total Activation (everything else sums into it).
TOTAL_ACTIVATION_EXCLUDED = ("Upgrade", "BYOD Upgrade")


def _norm_tokens(value, default):
    """A config token list, lowercased/trimmed; None/absent → default; junk entries dropped."""
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple)):
        return list(default)
    out = []
    for t in value:
        s = str(t or "").strip().lower()
        if s:
            out.append(s)
    return out


def resolve_rules(raw):
    """Normalize an activation_details_rules JSONB value (dict, possibly {}/None/garbage) into the
    full rules dict, house defaults filling every missing key. Unknown keys are ignored. PURE."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "edge_contract_tokens": _norm_tokens(raw.get("edge_contract_tokens"),
                                             HOUSE_DEFAULT_RULES["edge_contract_tokens"]),
        "edge_name_tokens": _norm_tokens(raw.get("edge_name_tokens"),
                                         HOUSE_DEFAULT_RULES["edge_name_tokens"]),
        "upgrade_hidden_contract_tokens": _norm_tokens(
            raw.get("upgrade_hidden_contract_tokens"),
            HOUSE_DEFAULT_RULES["upgrade_hidden_contract_tokens"]),
    }


def _word_hit(tokens, text):
    """True when any token appears as a WHOLE WORD (alnum-boundary) in text. Guarded — a token that
    breaks re never crashes classification (re.escape makes that impossible, belt+braces anyway)."""
    for t in tokens:
        try:
            if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text):
                return True
        except re.error:
            continue
    return False


def _contains_hit(tokens, text):
    return any(t in text for t in tokens)


def activation_details_bucket(contract_type, sp_name, product, category, rules=None):
    """Activation TYPE for a b2b Activation Details line — the SAME columns the b2b "Month To Date
    Location Sales Report" breaks Total Activation into: New Activation / Port / BYOD / Tablet /
    Home Internet / Edge / Upgrade (+ the hidden 'BYOD Upgrade' family, excluded from TA like
    Upgrade but not displayed in the Upgrade column).

    PRECEDENCE (owner reconciliation 2026-08-26, config-fix 2026-09-01): non-phone DEVICE families
    (Home Internet / Edge / Tablet) first, then the excluded Upgrade families (any Contract Type
    containing 'upgrade'; hidden-token match → 'BYOD Upgrade'), then BYOD, then Port (the word
    'port' only — never 'idv', an insurance attach), then a plain New activation.

    `rules` = resolve_rules(...) output (None → house defaults). PURE, stdlib-only."""
    r = rules or resolve_rules(None)
    ct = str(contract_type or "").lower()
    nm = f"{sp_name or ''} {product or ''} {category or ''}".lower()
    if "home internet" in nm or "fwa" in nm or "fixed wireless" in nm:
        return "Home Internet"
    # EDGE — contract_type whole-word by default; NAME tokens only where an org configured them
    # (the Motorola-Edge-device over-match fix; see module docstring trade-off).
    if _word_hit(r["edge_contract_tokens"], ct) or _contains_hit(r["edge_name_tokens"], nm):
        return "Edge"
    if "tablet" in nm or "galaxy tab" in nm:
        return "Tablet"
    if "upgrade" in ct:
        if _contains_hit(r["upgrade_hidden_contract_tokens"], ct):
            return "BYOD Upgrade"
        return "Upgrade"
    if "byod" in ct or "customer phone" in nm:
        return "BYOD"
    if "port" in ct:
        return "Port"
    if "activation" in ct:
        return "New Activation"
    return "Other"


# ── CROSS-BUCKET TRANSACTION DIAGNOSTIC (owner question 2026-09-20) ─────────────────────────────
# Owner: "the total activations should still match for every month as they are coming from b2b data".
#
# TOTAL ACTIVATION is the sum of four DISTINCT-transaction counts (Activation+Port / BYOD / Upgrade).
# `router._sales_cell_agg` assigns a transaction to a bucket PER LINE, so a single ticket carrying two
# differently-classified activation lines — e.g. a tablet 'Activation AAL' line and a 'BYOD Activation'
# line on the same sale — is added to TWO bucket sets and counted TWICE in the total. It is one sale.
#
# This function MEASURES that, per transaction, so the gap between our number and b2bsoft's can be
# reconciled ticket by ticket instead of guessed at. It is a DIAGNOSTIC: it classifies nothing, changes
# no bucket and moves no money. Deciding which bucket such a ticket belongs to is a money decision
# (on an exec-MTD-basis plan each extra bucket membership is another paid unit) and belongs to the
# owner, so nothing here resolves it.
#
# PURE, stdlib-only. Takes the cells `_sales_cell_agg` already produced — it does not re-classify, so it
# can never disagree with the classifier it is reporting on.
ACTIVATION_BUCKET_SETS = (("_prem", "Activation"), ("_byod", "BYOD"), ("_upg", "Upgrade"))


def mixed_bucket_transactions(cells, per_rep=False):
    """Transactions counted in MORE THAN ONE activation bucket. PURE.

    `cells` is `router._sales_cell_agg(...)` output: {(store, rep, date) -> cell}, each cell carrying the
    distinct-transaction sets `_prem` / `_byod` / `_upg`.

    Returns {"transactions": {trans_id: {"buckets": [...], "rep": str, "store": str, "extra": int}},
             "count": int, "extra_units": int, "by_rep": {rep: extra_units}}
    where `extra` is how many times that ONE sale is counted beyond the first, and `extra_units` is the
    total inflation of TOTAL ACTIVATION. On a per-unit pay basis `extra_units` is also the number of
    units paid more than once.
    """
    seen = {}
    for key, cell in (cells or {}).items():
        store = (cell or {}).get("store") or (key[0] if isinstance(key, tuple) else "")
        rep = (cell or {}).get("salesperson") or (key[1] if isinstance(key, tuple) and len(key) > 1 else "")
        for attr, label in ACTIVATION_BUCKET_SETS:
            for tid in ((cell or {}).get(attr) or ()):
                t = str(tid).strip()
                if not t:
                    continue
                e = seen.setdefault(t, {"buckets": [], "rep": rep, "store": store})
                if label not in e["buckets"]:
                    e["buckets"].append(label)
    out, by_rep, extra_units = {}, {}, 0
    for t, e in seen.items():
        if len(e["buckets"]) < 2:
            continue
        extra = len(e["buckets"]) - 1
        extra_units += extra
        by_rep[e["rep"]] = by_rep.get(e["rep"], 0) + extra
        out[t] = {"buckets": sorted(e["buckets"]), "rep": e["rep"], "store": e["store"], "extra": extra}
    return {"transactions": out, "count": len(out), "extra_units": extra_units,
            "by_rep": dict(sorted(by_rep.items(), key=lambda kv: -kv[1]))}
