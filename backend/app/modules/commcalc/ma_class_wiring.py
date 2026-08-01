"""MONEY WIRING for the MA product classes — the owner-gated step `ma_product_class` deliberately did NOT do.

OWNER GO-AHEAD 2026-08-01, in chat, AFTER confirming the classes on /commcalc/ma-product-class:
"go ahead and fix, it updated the classes".

`commcalc.ma_product_class_map` says what KIND of money each `raw_ma_daily_tx.product_name` is
(commission / spiff / residual / billpayment / device_sale / …). Until now nothing read it. This module
wires it into EXACTLY TWO consumers, each behind its own per-tenant mode flag whose DEFAULT is
'legacy' = today's behaviour to the cent:

  CONSUMER 1 — the canonical ledger (`commission_ledger.classify` via `commission_category_map`).
      Today a Daily-Tx line is bucketed by CONTAINS rules ('Commission' -> commission, 'SPF' -> spiff,
      'Residual' -> residual_monthly, 'Subsidy' -> equipment_rebate). Those rules read a LABEL, so they
      cannot tell `Total ALL ACCESS Plan $65` (a customer plan purchase) from
      `Total ALL ACCESS Plan $65 New Activation Commission` (a commission line) except by luck of
      substring. The wiring adds a new `match_op = 'product_class'`, so a tenant writes ONE rule per
      canonical bucket — `product_class = spiff  ->  spiff` — instead of guessing keywords.
      Re-bucketing existing ledger rows changes What-If carrier income on the next refresh, which is
      why it is flagged.

  CONSUMER 2 — What-If carrier income (`whatif._ma_carrier_income`).
      Its RESIDUAL leg selects by `order_type LIKE <residual_order_type>` and its airtime leg sums
      `merchant_discount` for EVERY non-residual row — so a device sale, a customer bill payment and a
      wallet funding all land in "airtime margin" today. With classes live, `residual` and `billpayment`
      become the honest selectors and `device_sale` / `wallet` / `adjustment_memo` / fees leave the
      income total entirely. That MOVES A DISPLAYED NUMBER, so it is flagged too.

  NOT A CONSUMER, EVER: `calculator.py`, `commission_engine.py`, `rep_commissions`. Rep pay derives from
  POS sales x Commission Plans — never from the MA daily file. Asserted by byte-identity in the proof.

  OUT OF SCOPE, RECORDED: the P&L / GP leg (`account/coa.py`, `account/residual_subs.py`). `billpayment`
  and `device_sale` are revenue WITH A COST, `fee` is an expense, `adjustment_memo` is a correction —
  feeding classes there is the biggest of the three moves and is sequenced BEHIND the device-cost
  recognition policy decision. It is a mod-finance package, owner-gated. Nothing here touches it.

════════════════════════════════════════════════════════════════════════════════════════════════════
ONLY A **CONFIRMED** MAPPING CAN MOVE A DOLLAR.
`confirmed_index()` keeps rows with status == 'confirmed' and NOTHING else. A proposal — including
every built-in proposal in `ma_product_class.DEFAULT_PROPOSALS`, including the four the seed flagged
`AMBIGUOUS — please verify` (Total Wireless Device Upgrade / Total ALL ACCESS Plan $65 New Activation
Commission / Home Internet Router TO / Credit Debit Memo) — classifies NOTHING and is surfaced loudly
instead. There is no code path that reads a proposed row into money: the built-in fallback that makes
the CONFIG page work pre-migration is deliberately absent here.
════════════════════════════════════════════════════════════════════════════════════════════════════

FAIL-CLOSED IN FOUR PLACES, so "off" is never an accident:
  1. no config row            -> mode 'legacy'                (absent == today)
  2. mode 'legacy'            -> the class index is NEVER attached to the rules; product_class rules
                                 cannot match at all (`commission_ledger._match` requires the compiled
                                 index) and the carrier-income class legs are computed but NOT displayed
  3. no CONFIRMED mapping     -> that name has no class; a product_class rule cannot match it
  4. a class with no leg row  -> `excluded` (it does not silently become income)

REVERT IS A DROPDOWN, NOT A DEPLOY. Set the consumer's mode back to 'legacy' on
/commcalc/ma-class-wiring. No SQL, no redeploy, no recompute. (And for the ledger, the re-bucketing only
lands in stored rows on the NEXT refresh/import — reverting before a refresh means nothing ever moved.)

PURE + (a small, clearly-fenced) LOAD SECTION. Everything that computes is DB-free and unit-testable;
the four `load_*` functions do org-scoped reads and degrade to the code defaults when migration 265 has
not been run. No writes live here — those are router endpoints.
"""

from app.modules.commcalc import ma_product_class

# ── tables (mig 265) ────────────────────────────────────────────────────────────────────────────────
CONFIG_TABLE = "ma_class_wiring_config"     # (org, consumer) -> mode
LEG_TABLE = "ma_class_income_leg"           # (org, product_class) -> carrier-income leg
CLASS_MAP_TABLE = ma_product_class.MAP_TABLE   # the confirmed classifications (mig 254)
MIGRATION = "265_commission_ma_class_money_wiring.sql"
CLASS_MIGRATION = "254_commission_ma_product_class.sql"

DEFAULT_SOURCE_REPORT = "ma_daily_tx"

# ── the two consumers ───────────────────────────────────────────────────────────────────────────────
CONSUMER_LEDGER = "ledger"
CONSUMER_INCOME = "carrier_income"
CONSUMERS = (CONSUMER_LEDGER, CONSUMER_INCOME)
CONSUMER_LABELS = {
    CONSUMER_LEDGER: "Commission Ledger — how a Daily-Tx line is bucketed",
    CONSUMER_INCOME: "What-If → Carrier income — which lines count as residual / airtime",
}

# ── the mode ────────────────────────────────────────────────────────────────────────────────────────
MODE_LEGACY = "legacy"
MODE_CLASS = "class"
MODES = (MODE_LEGACY, MODE_CLASS)
DEFAULT_MODE = MODE_LEGACY
MODE_LABELS = {
    MODE_LEGACY: "Legacy — keyword rules / order-type (today's behaviour)",
    MODE_CLASS: "Product class — use the confirmed MA product classification",
}

# ── carrier-income legs a class can feed ────────────────────────────────────────────────────────────
LEG_RESIDUAL = "residual"
LEG_AIRTIME = "airtime"
LEG_EXCLUDED = "excluded"
INCOME_LEGS = (LEG_RESIDUAL, LEG_AIRTIME, LEG_EXCLUDED)
LEG_LABELS = {
    LEG_RESIDUAL: "Residual (residual_mi_atu)",
    LEG_AIRTIME: "Airtime margin (merchant discount)",
    LEG_EXCLUDED: "Not carrier income — leave it out of the total",
}

# The code default, used when mig 265 has not run or a class has no row. It is the HONEST mapping the
# design of record states — residual and billpayment are the two legs, everything else leaves the total.
# It only ever takes effect in mode 'class'; in 'legacy' nothing here is displayed.
DEFAULT_INCOME_LEGS = {
    "residual": LEG_RESIDUAL,
    "billpayment": LEG_AIRTIME,
}

# The new match_op on commcalc.commission_category_map (consumer 1).
MATCH_OP = "product_class"

# Classes whose canonical ledger bucket is unambiguous — the only ones the rule PROPOSER offers by
# default. `residual` is deliberately NOT here: the class vocabulary has ONE residual class while the
# ledger has TWO buckets (residual_monthly and autopay_residual), so a single class rule would COLLAPSE
# Auto Pay residual into residual_monthly. That is a real bucket move and the owner has to ask for it.
UNAMBIGUOUS_BUCKET = {
    "commission": "commission",
    "spiff": "spiff",
    "subsidy": "equipment_rebate",
}
# Offered, but only with the collapse warning attached (see `bucket_proposals`).
WARNED_BUCKET = {
    "residual": ("residual_monthly",
                 "The ledger has TWO residual buckets (Residual / monthly and Auto Pay residual) but the "
                 "product classification has ONE 'residual' class. Applying this rule moves every Auto "
                 "Pay residual line into 'Residual / monthly'. Carrier income does not change (both "
                 "residual buckets are excluded from it), but the ledger's own report splits differently."),
}
# Classes that are NOT a dealer payout at all. Mapping one to the ledger's 'charge' pseudo-bucket takes
# its dollars OUT of payout_total — which is the point — but it is a money decision, so it is only ever
# offered, never seeded.
NON_PAYOUT_BUCKET = ("billpayment", "device_sale", "wallet", "fee", "sim_kit", "adjustment_memo",
                     "protection", "financing")
CHARGE_BUCKET = "charge"

# The marker `ma_product_class.DEFAULT_PROPOSALS` uses on a judgement call it refuses to make alone.
AMBIGUOUS_MARK = "AMBIGUOUS"

CONFIRMED = "confirmed"

# The bucket a label with NO confirmed class falls into. It is never a leg and never a money bucket —
# it exists so the dollars are countable and nameable in the delta panels.
UNCLASSIFIED = "(unclassified)"


def normalize(name):
    """The one normalization, borrowed from the classification engine so the two can never drift."""
    return ma_product_class.normalize(name)


# ── the CONFIRMED-ONLY index (the money gate) ───────────────────────────────────────────────────────
def confirmed_index(map_rows):
    """{trimmed product_name: class_key} for rows the owner has CONFIRMED, and nothing else.

    Deliberately unlike `ma_product_class.build_index`: there is NO built-in-proposal fallback here. A
    built-in proposal is a suggestion this module wrote; letting it move a dollar because a tenant has
    not run migration 254 yet would be exactly the failure this whole design exists to prevent. Blank
    names, blank classes and the reserved 'unmapped' sentinel are all dropped."""
    idx = {}
    for r in (map_rows or []):
        if normalize(r.get("status")).lower() != CONFIRMED:
            continue
        name = normalize(r.get("product_name"))
        cls = normalize(r.get("product_class"))
        if not name or not cls or cls == ma_product_class.UNMAPPED:
            continue
        idx[name] = cls
    return idx


def class_of(product_name, index):
    """The CONFIRMED class of one label, or None. `None` never means 'zero' — every caller reports it."""
    return (index or {}).get(normalize(product_name))


def index_status(map_rows):
    """What the owner still has to decide, in numbers + names. The delta panels render this verbatim.

    `ambiguous_pending` is the loud one the dispatch asked for: a name the seed flagged
    'AMBIGUOUS — please verify' that is STILL unconfirmed. It classifies nothing (by construction —
    `confirmed_index` skipped it); this is how the owner finds out that is why a line is unclassified.
    `ambiguous_confirmed` is its mirror: judgement calls that ARE now live money."""
    confirmed, proposed = [], []
    amb_pending, amb_confirmed = [], []
    for r in (map_rows or []):
        name = normalize(r.get("product_name"))
        if not name:
            continue
        cls = normalize(r.get("product_class"))
        is_conf = normalize(r.get("status")).lower() == CONFIRMED
        is_amb = AMBIGUOUS_MARK in str(r.get("note") or "")
        entry = {"product_name": name, "product_class": cls, "note": (r.get("note") or "")[:300]}
        (confirmed if is_conf else proposed).append(entry)
        if is_amb:
            (amb_confirmed if is_conf else amb_pending).append(entry)
    return {
        "rows": len(confirmed) + len(proposed),
        "confirmed": len(confirmed), "proposed": len(proposed),
        "confirmed_names": sorted(e["product_name"] for e in confirmed),
        "proposed_names": sorted(e["product_name"] for e in proposed),
        "ambiguous_pending": sorted(amb_pending, key=lambda e: e["product_name"]),
        "ambiguous_confirmed": sorted(amb_confirmed, key=lambda e: e["product_name"]),
        "note": ("Only CONFIRMED mappings classify money. %d name(s) are still proposed and classify "
                 "nothing." % len(proposed)) if proposed else
                "Every saved mapping is confirmed.",
    }


# ── the carrier-income leg map ──────────────────────────────────────────────────────────────────────
def income_legs_from(rows):
    """{class_key: leg} from the tenant's rows, falling back to DEFAULT_INCOME_LEGS when there are none.
    An unknown leg value is treated as 'excluded' — the fail-closed direction."""
    out = {}
    for r in (rows or []):
        cls = normalize(r.get("product_class"))
        leg = normalize(r.get("income_leg")).lower()
        if not cls:
            continue
        out[cls] = leg if leg in INCOME_LEGS else LEG_EXCLUDED
    return out or dict(DEFAULT_INCOME_LEGS)


def leg_for(product_class, legs):
    """A class with no row is EXCLUDED from carrier income. Absent never means 'income'."""
    if not product_class:
        return LEG_EXCLUDED
    leg = (legs or {}).get(normalize(product_class))
    return leg if leg in INCOME_LEGS else LEG_EXCLUDED


def residual_classes(legs):
    """The classes feeding the RESIDUAL leg — used by the ledger double-count guard so a row can never
    appear both in a ledger income bucket and in the residual leg."""
    return {c for c, l in (legs or {}).items() if l == LEG_RESIDUAL}


def leg_rows(legs, classes):
    """One row per class in the vocabulary for the admin grid (RULE THREE: pick, don't type)."""
    out = []
    for c in (classes or []):
        key = c.get("class_key") if isinstance(c, dict) else c
        if not key or key == ma_product_class.UNMAPPED:
            continue
        out.append({"product_class": key,
                    "label": (c.get("label") if isinstance(c, dict) else key) or key,
                    "income_leg": leg_for(key, legs),
                    "default_leg": DEFAULT_INCOME_LEGS.get(key, LEG_EXCLUDED)})
    return out


# ── the mode ────────────────────────────────────────────────────────────────────────────────────────
def mode_from(rows, consumer):
    """The saved mode for one consumer; DEFAULT_MODE when there is no row or the value is unknown."""
    for r in (rows or []):
        if normalize(r.get("consumer")) == consumer:
            m = normalize(r.get("mode")).lower()
            return m if m in MODES else DEFAULT_MODE
    return DEFAULT_MODE


def modes_from(rows):
    return {c: mode_from(rows, c) for c in CONSUMERS}


# ── consumer 1: compiling a product_class rule ──────────────────────────────────────────────────────
def compile_rules(rules, class_index):
    """Attach the CONFIRMED class index to every `match_op='product_class'` rule.

    This is how the wiring reaches `commission_ledger.classify()` WITHOUT changing its signature or
    `build_row`'s or `ledger_ma_sync.derive`'s — the rule carries its own compiled matcher, the way a
    compiled regex would. A rule with no index attached can never match (`_match` returns False), so
    legacy mode is byte-identical whether or not product_class rows exist. PURE."""
    if not class_index:
        return list(rules or [])
    out = []
    for r in (rules or []):
        if (r or {}).get("match_op") == MATCH_OP:
            r = dict(r)
            r["_class_index"] = class_index
        out.append(r)
    return out


def has_class_rules(rules):
    return any((r or {}).get("match_op") == MATCH_OP for r in (rules or []))


# ── consumer 1: evidence-bound rule PROPOSALS (never auto-applied) ──────────────────────────────────
def bucket_proposals(class_index, ledger_rows, rules, name_col="product_name"):
    """Propose one `product_class -> canonical bucket` rule per class the tenant actually has data for,
    each carrying the EVIDENCE (how many ledger lines, how many dollars, which buckets they sit in
    today) and, where it applies, the warning that makes it a decision rather than a click.

    Nothing here writes. The apply endpoint takes an explicit list of classes — never "all". PURE."""
    have = {(r or {}).get("pattern"): r for r in (rules or []) if (r or {}).get("match_op") == MATCH_OP}
    seen = {}
    for row in (ledger_rows or []):
        cls = class_of(row.get(name_col), class_index)
        if not cls:
            continue
        e = seen.setdefault(cls, {"lines": 0, "payout": 0.0, "today": {}, "examples": []})
        e["lines"] += 1
        e["payout"] = round(e["payout"] + _sf(row.get("payout_total")), 2)
        cat = normalize(row.get("category")) or "other"
        t = e["today"].setdefault(cat, {"lines": 0, "payout": 0.0})
        t["lines"] += 1
        t["payout"] = round(t["payout"] + _sf(row.get("payout_total")), 2)
        nm = normalize(row.get(name_col))
        if nm and nm not in e["examples"] and len(e["examples"]) < 8:
            e["examples"].append(nm)
    out = []
    for cls in sorted(seen):
        e = seen[cls]
        bucket, warn = UNAMBIGUOUS_BUCKET.get(cls), None
        if not bucket and cls in WARNED_BUCKET:
            bucket, warn = WARNED_BUCKET[cls]
        if not bucket and cls in NON_PAYOUT_BUCKET:
            bucket = CHARGE_BUCKET
            warn = ("'%s' is not a dealer payout. Mapping it to 'charge' takes its dollars OUT of the "
                    "ledger's payout total — which is the point, and which is a money decision."
                    % cls)
        out.append({"product_class": cls, "proposed_category": bucket,
                    "already_configured": cls in have,
                    "current_category": (have.get(cls) or {}).get("category"),
                    "lines": e["lines"], "payout_total": e["payout"],
                    "today_by_category": e["today"], "examples": e["examples"],
                    "warning": warn,
                    "kind": ("unambiguous" if cls in UNAMBIGUOUS_BUCKET else
                             ("warned" if cls in WARNED_BUCKET else
                              ("non_payout" if cls in NON_PAYOUT_BUCKET else "undecided")))})
    return out


# ── the cross-consumer conflict guard ───────────────────────────────────────────────────────────────
def conflicts(rules, legs):
    """A class routed BOTH into a ledger payout bucket (consumer 1) and into a carrier-income leg
    (consumer 2) is the shape of a double count. The two read different columns, so it is not
    automatically wrong — which is exactly why it is reported rather than blocked. PURE."""
    out = []
    for r in (rules or []):
        if (r or {}).get("match_op") != MATCH_OP:
            continue
        cls = normalize(r.get("pattern"))
        cat = normalize(r.get("category"))
        leg = leg_for(cls, legs)
        if cat and cat != CHARGE_BUCKET and leg != LEG_EXCLUDED:
            out.append({"product_class": cls, "ledger_category": cat, "income_leg": leg,
                        "why": ("class '%s' books a ledger payout bucket ('%s') AND feeds carrier "
                                "income ('%s'). The ledger books the line's own amount column while the "
                                "income leg sums merchant discount, so these may be different dollars — "
                                "check it before flipping both consumers on." % (cls, cat, leg))})
    return out


def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# LOADS — org-scoped reads. Every one degrades to the code default instead of raising, so a tenant that
# has not run migration 265 keeps today's behaviour rather than seeing an error page.
# ════════════════════════════════════════════════════════════════════════════════════════════════════
def load_config_rows(client, org_id):
    """(rows, ready). ready=False == migration 265 not applied -> both consumers read 'legacy'."""
    try:
        rows = (client.schema("commcalc").table(CONFIG_TABLE).select("*")
                .eq("org_id", org_id).execute().data) or []
        return rows, True
    except Exception:
        return [], False


def load_mode(client, org_id, consumer):
    """(mode, meta). The single question every consumer asks before it changes anything."""
    rows, ready = load_config_rows(client, org_id)
    mode = mode_from(rows, consumer) if ready else DEFAULT_MODE
    return mode, {"ready": ready, "consumer": consumer, "mode": mode,
                  "migration": None if ready else MIGRATION,
                  "default_mode": DEFAULT_MODE}


def load_income_legs(client, org_id):
    """({class: leg}, ready). Falls back to DEFAULT_INCOME_LEGS."""
    try:
        rows = (client.schema("commcalc").table(LEG_TABLE).select("*")
                .eq("org_id", org_id).execute().data) or []
        return income_legs_from(rows), True
    except Exception:
        return dict(DEFAULT_INCOME_LEGS), False


def load_class_map_rows(client, org_id, source_report=DEFAULT_SOURCE_REPORT):
    """(rows, ready). The raw mig-254 mapping rows for one tenant + source, org-scoped."""
    try:
        rows = (client.schema("commcalc").table(CLASS_MAP_TABLE).select("*")
                .eq("org_id", org_id).eq("source_report", source_report)
                .limit(100000).execute().data) or []
        return rows, True
    except Exception:
        return [], False


def load_class_index(client, org_id, source_report=DEFAULT_SOURCE_REPORT):
    """({name: class}, meta). CONFIRMED rows only — see the module header. `meta` carries everything the
    delta panels need to explain a $0: whether mig 254 ran, how many names are confirmed vs still
    proposed, and which AMBIGUOUS judgement calls are still pending."""
    rows, ready = load_class_map_rows(client, org_id, source_report)
    idx = confirmed_index(rows)
    meta = index_status(rows)
    meta.update({"ready": ready, "source_report": source_report,
                 "migration": None if ready else CLASS_MIGRATION,
                 "classified_names": len(idx)})
    return idx, meta


def ledger_rules_with_class(client, org_id, source_report, rules):
    """CONSUMER 1's single entry point: give me the tenant's ledger rules, compiled for whichever mode
    they are actually in.

    In mode 'legacy' — the default, and what every tenant has until an owner flips the dropdown — the
    rules come back UNTOUCHED (`is` the same list contents, no index attached), so `classify()` cannot
    behave differently by even one line. Returns (rules, meta)."""
    mode, mmeta = load_mode(client, org_id, CONSUMER_LEDGER)
    meta = {"mode": mode, "config_ready": mmeta["ready"], "migration": mmeta["migration"],
            "class_rules": sum(1 for r in (rules or []) if (r or {}).get("match_op") == MATCH_OP),
            "applied": False, "classified_names": 0, "class_ready": None}
    if mode != MODE_CLASS:
        meta["why"] = ("Ledger wiring is in LEGACY mode — product-class rules are ignored and lines are "
                       "bucketed by the keyword rules exactly as before.")
        return list(rules or []), meta
    idx, imeta = load_class_index(client, org_id, source_report)
    meta.update({"applied": bool(idx), "classified_names": len(idx), "class_ready": imeta["ready"],
                 "class_status": imeta})
    if not idx:
        meta["why"] = ("Ledger wiring is in PRODUCT CLASS mode but this tenant has no CONFIRMED product "
                       "classifications, so product-class rules match nothing and every line falls back "
                       "to the keyword rules. Confirm the classes on /commcalc/ma-product-class.")
        return list(rules or []), meta
    meta["why"] = ("Ledger wiring is in PRODUCT CLASS mode: %d confirmed name(s) are matched by class "
                   "BEFORE any keyword rule is tried." % len(idx))
    return compile_rules(rules, idx), meta
