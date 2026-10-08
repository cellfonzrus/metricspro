"""THE ONE HOME for "is this carrier dollar a commission, a spiff, a residual, or a reimbursement?"

OWNER REPORT 2026-10-08: *"i just checked the commission details for boost, the commission is over
stated as the device reimbursement is being added in the commision and also in device reimbursement
… example is 103 fulton street ↳ Commission (promo) $7,583.96 / Device-financing reimbursements
(Distributor) $7,999.93 … the report which pays us is same as what is reported in commision
7583.96"*, and then *"need to move that amount from commssiomn to teh device reimbursement"*.

THE CLASS, NOT THE INSTANCE. The instance was three September rows at one store. The class is:

    **"is this carrier dollar commission or a reimbursement?" was answered by TWO maps that
      disagreed, and the one the P&L read never saw the org's OWN declaration.**

Measured live, house org, `commcalc.raw_comp_report` March–October 2026:

  · `carrier_category_map` carries generic keyword rules — `contains <a promo word>` → COMMISSION at
    priority 40, `contains <an upgrade word>` → COMMISSION at 45 — which scoop up every
    period-renamed carrier offer as commission. That ladder is what the P&L's carrier drill-down
    read, so $2,784,846.76 sat on the commission line.
  · `commcalc.payment_categories` — the org's OWN declaration, 154 active rows — calls those SAME
    offers `Re-imbursement`. SEVEN of them match on the EXACT SAME SPELLING (no inference needed):
    the Q1 and Q2 2026 offers are declared, dollar for dollar, as reimbursements.
  · The disagreement runs BOTH ways and is not only about reimbursement: 79 types the org declares
    `Commission` ($775,198.35) were being labelled SPIFF by the ladder's bounty keyword, and
    $83,465.55 the org declares `Commission` matched no keyword rule at all and rendered "Unmapped".

So the classification has ONE home — this file — and every caller DEREFERENCES it. The ruling it
encodes, in order:

  1. **THE ORG'S OWN DECLARATION WINS.** A `compensation_type` present in `payment_categories` takes
     the component its declared category maps to. A declaration is a fact the org asserted; a
     keyword rule is a guess the platform made.
  2. **A PERIOD-RENAMED TWIN IS AN INFERENCE, AND IT TRAVELS SAYING SO.** The carrier renames its
     offers every quarter and the declarations were never carried forward, so an undeclared type
     often has an identically-stemmed prior-period twin that IS declared. That twin is used — and
     `basis` says `inferred_prior_year_twin`, `inferred` is True, and `twin_of` names the row the
     inference came from. Same shape as index §19.48/§19.49/§19.51: an inferred fact never travels
     without saying it was inferred. $1,077,227.91 of the house total is inferred, not declared.
  3. **THE KEYWORD LADDER IS THE FALLBACK, NEVER THE OVERRIDE.** `carrier_category_map` decides only
     for a type the org has not declared and whose stem has no declared twin.
  4. **AN UNDECLARED TYPE IS REPORTED, NEVER QUIETLY ADOPTED.** `declared` is False for every
     outcome that is not ruling 1, INDEPENDENTLY of whether a keyword rule happened to catch it, so
     `tally()` reports it either way. The house org's `<a quarterly true-up type>` ($4,429.00) books
     on its keyword fallback AND is reported as undeclared in the same breath.
  5. **A DECLARED CATEGORY OUTSIDE THE COMPONENT VOCABULARY IS NOT A GUESS EITHER.** The house org
     declares $260,500.00 under a category the four-component vocabulary has no row for; that is
     `declared_category_unmapped`, booked on its keyword fallback and REPORTED, because inventing a
     component for an unmapped category is exactly the silent decision this file exists to stop.

RULE TWO, ABSOLUTELY. There is not one carrier, tenant, quarter, product or promo name in this
file. Every string above is DATA: the declarations are rows in `payment_categories`, the keyword
ladder is rows in `carrier_category_map`, the category→component map and the rename pattern are
per-org config with house defaults. The fix is to READ the org's declaration, never to spell its
vocabulary in code.

WHAT THIS MODULE DOES *NOT* RESTATE. The READ of `payment_categories`, and the one folding rule for
a payment-type key, already have a home — §57 `commcalc/payment_category.py`, which exists precisely
because nine callers once each did their own `select("description,category")` and folded the key
nine ways. `load_declarations` and `declared_category` here are thin dereferences of it, so this
file adds a COMPONENT ruling on top of §57's CATEGORY read and no tenth copy of the read itself.
The sentinel for "nothing declared", the placeable categories and the unplaced reasons stay in
`pay_data_quality` (§19.48) where they already live.

PURE except for `load_config` and the `load_declarations` passthrough. `classify`, `tally`,
`component_of_declared_category` and `period_rename_twin` take no DB and no network, so a lock
proves them without a database (`harness_carrier_dollar_class.py`).
"""
from __future__ import annotations

import re

from app.modules.commcalc import carrier_map as _cm

# The canonical component vocabulary is `carrier_map`'s — DEREFERENCED, never re-spelled here, so a
# fifth component can only ever be added in one place.
COMPONENTS = _cm.COMPONENTS

# How an outcome was reached. Part of every result and of the coverage report; never a default.
BASIS_DECLARED = "declared"
BASIS_INFERRED_TWIN = "inferred_prior_year_twin"
BASIS_KEYWORD = "keyword_rule"
BASIS_DECLARED_UNMAPPED = "declared_category_unmapped"
BASIS_UNRESOLVED = "unresolved"

BASIS_REASONS = {
    BASIS_DECLARED:
        "The org's own payment_categories row declares this payment type's category, and a "
        "declaration always beats a keyword rule.",
    BASIS_INFERRED_TWIN:
        "This payment type is NOT declared. An identically-stemmed earlier-period type IS declared, "
        "and that declaration was carried forward. This is an INFERENCE, not the org's word.",
    BASIS_KEYWORD:
        "Undeclared, with no earlier-period twin. The org's carrier_category_map keyword ladder "
        "decided, which is a platform guess — declare the type to replace it with a fact.",
    BASIS_DECLARED_UNMAPPED:
        "The org declared a category this org's category→component map has no row for, so the "
        "declaration could not be honoured and the keyword ladder decided instead.",
    BASIS_UNRESOLVED:
        "Undeclared, no earlier-period twin, and no keyword rule matched. The component is unknown "
        "and is reported as unknown rather than folded into a bucket.",
}

# Every basis other than `declared` is reported by `tally` as money the org has not declared.
UNDECLARED_BASES = (BASIS_INFERRED_TWIN, BASIS_KEYWORD, BASIS_DECLARED_UNMAPPED, BASIS_UNRESOLVED)

# Whose figure the device-financing reimbursement line carries. An unknown value keeps the house
# default, so a typo can never silently re-recognise revenue.
DEVICE_REIMB_SOURCES = ("carrier_paid", "distributor_claim")

CONFIG_COLUMNS = (
    ("carrier_class_declaration_wins", "1062_carrier_dollar_class.sql"),
    ("carrier_class_category_components", "1062_carrier_dollar_class.sql"),
    ("carrier_class_rename_inference", "1062_carrier_dollar_class.sql"),
    ("carrier_class_rename_pattern", "1062_carrier_dollar_class.sql"),
    ("carrier_class_rename_lookback", "1062_carrier_dollar_class.sql"),
    ("carrier_component_lines", "1062_carrier_dollar_class.sql"),
    ("pl_device_reimb_source", "1062_carrier_dollar_class.sql"),
)
CONFIG_MIGRATION = dict(CONFIG_COLUMNS)


def default_config():
    """House defaults. Every value is overridable per org — no tenant ever needs a code branch.

    `category_components` maps a `payment_categories.category` label (lower-cased, whitespace
    collapsed) to one of `COMPONENTS`. The house map covers the three spellings the platform's own
    seed vocabulary uses for each component plus the hyphen variant the house org actually stores; a
    category with no row here is `declared_category_unmapped` and is REPORTED, never guessed.

    `rename_pattern` is the per-org shape of a period-renamed payment type: a regex with named
    groups `period` (the part that changes as the carrier re-issues the offer) and `stem` (the part
    that identifies the offer). The default recognises a leading four-digit year and a one-letter
    quarter marker WITHOUT naming any of them in a branch — the pattern is config, and an org whose
    carrier renames differently changes the row, not this file.
    """
    return {
        "declaration_wins": True,
        "category_components": {
            "commission": "COMMISSION",
            "re-imbursement": "REIMBURSEMENT",
            "reimbursement": "REIMBURSEMENT",
            "rebate": "REIMBURSEMENT",
            "residual": "RESIDUAL",
            "spiff": "SPIFF",
            "bounty": "SPIFF",
        },
        "rename_inference": True,
        "rename_pattern": r"^(?P<period>\d{4}\s+\w\d)\s+(?P<stem>\S.*)$",
        "rename_lookback": 4,
        # WHERE A COMPONENT BOOKS. Owner 2026-10-08: *"need to move that amount from commssiomn to
        # teh device reimbursement"*. A component with no row here books to the caller's default
        # line, so adding a component can never silently drop it off the statement. The KEY is a
        # component and the VALUE a P&L line key — both platform vocabulary, neither a tenant name.
        #
        # THE HOUSE DEFAULT IS EMPTY, DELIBERATELY. Rerouting a component moves $3.1M of booked
        # revenue between two P&L lines, and a P&L recompute is a MONEY MOVE that only the owner
        # approves. So the mechanism ships inert: until mig `1062` is applied and the owner's row
        # names the destination, every component books where it always did and every org's statement
        # is byte-identical. What changes unconditionally is the CLASSIFICATION — the drill-down now
        # shows the org's own declaration instead of a keyword guess — which moves no line total.
        # Migration 1062 seeds {"REIMBURSEMENT": "vip_reimb"} for the house org; §58.4 has the
        # measured restatement that applying it produces.
        "component_lines": {},
        # WHOSE FIGURE THE DEVICE-FINANCING REIMBURSEMENT LINE CARRIES. Owner 2026-10-08:
        # *"distributors payments are not in additon to the reimbursement they are the same payments
        # but the discrepancy nbetween them shows that the distributor claims it was paid bunt epay
        # never paid it"*. One payment, two sides: the carrier statement is the MONEY, the
        # distributor's asset-ledger reimbursement is a CLAIM about that same money.
        #   'carrier_paid'       — the line carries what the carrier actually paid;
        #                        the distributor's claim is HELD and reported, never booked as
        #                        revenue, so the same dollars cannot be recognised twice.
        #   'distributor_claim'  (house default) — the pre-2026-10-08 posture, byte-identical, and
        #                        correct for any org whose distributor ledger IS its receipt of
        #                        record.
        #   The HOUSE DEFAULT is the legacy posture for the same reason `component_lines` is empty:
        #   de-recognising the claim is a revenue reduction, and that is the owner's call to apply.
        "device_reimb_source": "distributor_claim",
        "config_columns_missing": [],
        "config_migrations_missing": [],
    }


def config_migrations_missing(columns_missing):
    """PURE: the migration files (deduped, in column order) that add the missing columns."""
    out = []
    for c in columns_missing or ():
        m = CONFIG_MIGRATION.get(c)
        if m and m not in out:
            out.append(m)
    return out


def load_config(client, org_id):
    """Per-org classification config (`commcalc.commission_org_config`), org-scoped and ADAPTIVE:
    a missing table, row or column degrades to `default_config()` and says which column was absent
    on `config_columns_missing`, so a surface can print "apply migration X" instead of silently
    reading a default. NEVER raises. An invalid value keeps the house default — a typo can never
    silently re-file a booked statement."""
    cfg = default_config()
    try:
        from app.core import column_tolerant as _ct      # the ONE reading rule for a config row
        rd = _ct.read_row(lambda: client.schema("commcalc").table("commission_org_config"),
                          lambda q: q.eq("org_id", org_id),
                          expected=[c for c, _m in CONFIG_COLUMNS])
        cfg["config_columns_missing"] = list(rd.missing)
        cfg["config_migrations_missing"] = config_migrations_missing(rd.missing)
        r = rd.row or {}
        if isinstance(r.get("carrier_class_declaration_wins"), bool):
            cfg["declaration_wins"] = r["carrier_class_declaration_wins"]
        if isinstance(r.get("carrier_class_category_components"), dict):
            # explicit {} honoured: the org can switch declaration-mapping off entirely
            m = {}
            for k, v in r["carrier_class_category_components"].items():
                key, comp = _norm(k), str(v or "").strip().upper()
                if key and comp in COMPONENTS:
                    m[key] = comp
            cfg["category_components"] = m
        if isinstance(r.get("carrier_class_rename_inference"), bool):
            cfg["rename_inference"] = r["carrier_class_rename_inference"]
        pat = str(r.get("carrier_class_rename_pattern") or "").strip()
        if pat and _valid_rename_pattern(pat):
            cfg["rename_pattern"] = pat
        if isinstance(r.get("carrier_component_lines"), dict):
            # explicit {} honoured: every component then books to the caller's default line
            cl = {}
            for k, v in r["carrier_component_lines"].items():
                comp, line = str(k or "").strip().upper(), str(v or "").strip()
                if comp in COMPONENTS and line:
                    cl[comp] = line
            cfg["component_lines"] = cl
        src = _norm(r.get("pl_device_reimb_source"))
        if src in DEVICE_REIMB_SOURCES:
            cfg["device_reimb_source"] = src
        lb = r.get("carrier_class_rename_lookback")
        if isinstance(lb, int) and not isinstance(lb, bool) and lb >= 0:
            cfg["rename_lookback"] = lb
    except Exception:
        pass
    return cfg


def _valid_rename_pattern(pat):
    """A rename pattern is usable only if it compiles AND names both groups the inference needs.
    An unusable pattern keeps the house default rather than silently disabling the inference."""
    try:
        rx = re.compile(pat)
    except re.error:
        return False
    return "period" in rx.groupindex and "stem" in rx.groupindex


def _norm(v):
    return " ".join(str(v or "").strip().split()).lower()


def load_declarations(client, org_id):
    """The org's declaration map, READ THROUGH ITS EXISTING ONE HOME (§57
    `payment_category.load_map`) — this module adds no tenth copy of that read.

    §57 already owns the read and the ONE folding rule for a payment-type key (trimmed,
    case-flattened), because nine call sites once folded it nine different ways. Everything this
    module does with the map goes through §57's `category_of`, so a cosmetic re-spelling of a
    payment type can never be read as a new, undeclared type here while §57 reads it as declared."""
    from app.modules.commcalc import payment_category as _pc
    return _pc.load_map(client, org_id)


def declared_category(declarations, raw_type):
    """The org's declared category for `raw_type`, or None — §57's `category_of`, dereferenced.

    `declarations` is whatever `load_declarations` returned, so the folding rule travels with the
    map instead of being re-implemented against it."""
    from app.modules.commcalc import payment_category as _pc
    return _pc.category_of(declarations, raw_type)


def component_of_declared_category(category, cfg=None):
    """PURE: the component an org's declared category means, or None when the org's map has no row
    for it. None is an honest "the declaration cannot be honoured", never a default component."""
    c = (cfg or default_config()).get("category_components") or {}
    return c.get(_norm(category))


def period_rename_twin(declarations, raw_type, cfg=None):
    """PURE: `(twin_description, twin_category)` for the newest earlier-period declaration whose
    STEM matches this type's stem and whose category this org's map can honour, or None.

    "Earlier" is decided by the `period` group's own text compared as a string, so no calendar is
    hard-coded and an org whose carrier numbers its offers differently needs only its own pattern.
    The newest qualifying twin wins, and only a twin within `rename_lookback` distinct earlier
    periods is used, so a stale vocabulary cannot reach forward indefinitely."""
    cfg = cfg or default_config()
    if not cfg.get("rename_inference") or not declarations:
        return None
    pat = cfg.get("rename_pattern") or ""
    if not _valid_rename_pattern(pat):
        return None
    rx = re.compile(pat)
    m = rx.match(str(raw_type or "").strip())
    if not m:
        return None
    period, stem = _norm(m.group("period")), _norm(m.group("stem"))
    if not period or not stem:
        return None
    cands = []
    for desc, cat in (declarations or {}).items():
        if component_of_declared_category(cat, cfg) is None:
            continue
        dm = rx.match(str(desc).strip())
        if not dm:
            continue
        if _norm(dm.group("stem")) != stem:
            continue
        p = _norm(dm.group("period"))
        if p >= period:                      # same or later period is not a carry-forward
            continue
        cands.append((p, desc, cat))
    if not cands:
        return None
    cands.sort(reverse=True)
    periods = []
    for p, _d, _c in cands:
        if p not in periods:
            periods.append(p)
    lookback = cfg.get("rename_lookback")
    if isinstance(lookback, int) and lookback > 0:
        allowed = set(periods[:lookback])
        cands = [c for c in cands if c[0] in allowed]
        if not cands:
            return None
    return (cands[0][1], cands[0][2])


def classify(declarations, rules, raw_type, cfg=None):
    """PURE. The whole ruling, in one place, for ONE `compensation_type` / `payment_type` string.

    `declarations` is `load_declarations`'s map, `rules` is `carrier_map.load_rules`'s priority-
    ordered ladder. Returns a dict that always carries:

        component         one of COMPONENTS, or None when nothing could resolve it
        basis             one of the BASIS_* values — how the component was reached
        declared          True ONLY for basis `declared`; an inference is never a declaration
        inferred          True for basis `inferred_prior_year_twin`
        declared_category the org's declared category string, when it declared one
        twin_of           (description, category) of the inference's source, when inferred
        keyword_component what the keyword ladder alone would have said (for the before/after report)
        subtype           the matched keyword rule's subtype, when the ladder decided
    """
    cfg = cfg or default_config()
    s = str(raw_type or "").strip()
    kw = _cm.classify(rules or (), s) if s else {"component": None, "subtype": None}
    out = {"payment_type": s, "component": None, "basis": BASIS_UNRESOLVED, "declared": False,
           "inferred": False, "declared_category": None, "twin_of": None,
           "keyword_component": kw.get("component"), "subtype": None}
    if not s:
        return out

    # §57 owns the folding rule, so the lookup goes through it rather than past it
    declared_cat = declared_category(declarations, s)
    if declared_cat is not None:
        out["declared_category"] = declared_cat
        comp = component_of_declared_category(declared_cat, cfg) if cfg.get("declaration_wins") else None
        if comp:
            out.update(component=comp, basis=BASIS_DECLARED, declared=True)
            return out
        # declared, but the org's map cannot honour the category: fall through to the ladder and SAY SO
        out.update(component=kw.get("component"), basis=BASIS_DECLARED_UNMAPPED,
                   subtype=kw.get("subtype"))
        return out

    twin = period_rename_twin(declarations, s, cfg) if cfg.get("declaration_wins") else None
    if twin:
        comp = component_of_declared_category(twin[1], cfg)
        if comp:
            out.update(component=comp, basis=BASIS_INFERRED_TWIN, inferred=True, twin_of=twin)
            return out

    if kw.get("component"):
        out.update(component=kw["component"], basis=BASIS_KEYWORD, subtype=kw.get("subtype"))
        return out
    return out


def component_line(component, line_map, default_line):
    """PURE: the P&L line key a component books to. `line_map` is the org's component→line config;
    a component with no row books to `default_line`, so a new component can never silently vanish
    off the statement."""
    if not component:
        return default_line
    return (line_map or {}).get(str(component).upper()) or default_line


def tally(rows, declarations, rules, cfg=None, amount_key="payment_amount",
          type_key="compensation_type"):
    """PURE. The coverage report: every dollar of a carrier feed accounted for by COMPONENT and by
    BASIS, with the undeclared money named per payment type, and `balances` as the arithmetic proof
    that nothing fell between the two breakdowns.

    This is what makes an inference and an undeclared type VISIBLE on the statement instead of
    indistinguishable from the org's own word. A reader presenting a classified figure while
    `undeclared_total` is non-zero is presenting a number that rests on a guess by a KNOWN amount,
    and can say so.
    """
    cfg = cfg or default_config()
    by_component, by_basis, undeclared, inferred, reclassified = {}, {}, {}, {}, {}
    total = 0.0
    seen = {}
    for r in (rows or []):
        amt = _f(r.get(amount_key))
        t = str(r.get(type_key) or "").strip()
        total += amt
        c = seen.get(t)
        if c is None:
            c = seen[t] = classify(declarations, rules, t, cfg)
        comp = c["component"] or "UNKNOWN"
        d = by_component.setdefault(comp, {"component": comp, "amount": 0.0, "rows": 0})
        d["amount"] += amt
        d["rows"] += 1
        b = by_basis.setdefault(c["basis"], {"basis": c["basis"], "amount": 0.0, "rows": 0,
                                             "reason": BASIS_REASONS.get(c["basis"], "")})
        b["amount"] += amt
        b["rows"] += 1
        if c["basis"] in UNDECLARED_BASES:
            u = undeclared.setdefault(t, {"payment_type": t, "basis": c["basis"], "amount": 0.0,
                                          "rows": 0, "component": c["component"],
                                          "declared_category": c["declared_category"]})
            u["amount"] += amt
            u["rows"] += 1
        if c["inferred"]:
            i = inferred.setdefault(t, {"payment_type": t, "twin_of": c["twin_of"],
                                        "component": c["component"], "amount": 0.0, "rows": 0})
            i["amount"] += amt
            i["rows"] += 1
        if c["component"] != c["keyword_component"]:
            k = "%s->%s" % (c["keyword_component"] or "UNKNOWN", c["component"] or "UNKNOWN")
            rc = reclassified.setdefault(k, {"from": c["keyword_component"], "to": c["component"],
                                             "amount": 0.0, "rows": 0, "payment_types": []})
            rc["amount"] += amt
            rc["rows"] += 1
            if t not in rc["payment_types"]:
                rc["payment_types"].append(t)

    for d in (by_component, by_basis, undeclared, inferred, reclassified):
        for v in d.values():
            v["amount"] = round(v["amount"], 2)
    total = round(total, 2)
    comp_sum = round(sum(v["amount"] for v in by_component.values()), 2)
    basis_sum = round(sum(v["amount"] for v in by_basis.values()), 2)
    declared_total = round((by_basis.get(BASIS_DECLARED) or {}).get("amount") or 0.0, 2)
    return {
        "total": total,
        "by_component": sorted(by_component.values(), key=lambda v: -v["amount"]),
        "by_basis": sorted(by_basis.values(), key=lambda v: -v["amount"]),
        "undeclared": sorted(undeclared.values(), key=lambda v: -v["amount"]),
        "inferred": sorted(inferred.values(), key=lambda v: -v["amount"]),
        "reclassified_vs_keyword": sorted(reclassified.values(), key=lambda v: -v["amount"]),
        "declared_total": declared_total,
        "undeclared_total": round(total - declared_total, 2),
        # The proof. A mismatch means a row escaped a branch above, not float drift.
        "balances": comp_sum == total and basis_sum == total,
        "config_columns_missing": list(cfg.get("config_columns_missing") or ()),
        "config_migrations_missing": list(cfg.get("config_migrations_missing") or ()),
    }


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
