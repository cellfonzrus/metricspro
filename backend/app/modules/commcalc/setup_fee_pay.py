"""DEVICE SET-UP FEE / ACTIVATION FEE — recognition, per-carrier economics, and the employee pay item.

OWNER DIRECTIVE 2026-08-01, verbatim:
  "Exceutive MTD sould also have the device set fee collected by the stores as a column , it is already
   calculated towars commision in boost but there is no commisison being paid out on the luxelink side,
   also the device set up fee is the same as activation fee on luxelink , an option should be there in
   commission payout if this has to be a part of commission and what % is used to pay out comp, for
   example , the boost payd 100% of the device set up fee collected to the dealer and the employee get
   10%, but total collects actiuvation fee and payd the dealer 50% of the activation fee collected but
   the employee is npot being paid anythting right now, need ot build this into the system which can be
   confgured by the user and the company - if criclet delaer uses metrics pro they should be able to
   design based on their payouts"

THE PAY CONCEPT IS ONE THING WITH MANY CARRIER NAMES. Boost calls it a device SET-UP FEE; Total/luxelink
calls it an ACTIVATION FEE; a Cricket dealer will call it something else again. That is the
[[carrier-bucket-language-doctrine]] class: the taxonomy is per-carrier CONFIG, never a branch on a
carrier name. This module holds ONE concept — "a fee the store COLLECTS from the customer at the point
of sale, which the carrier shares with the dealer and the dealer may share with the employee" — and
three numbers per carrier:

    include_in_commission      does this fee pay the employee at all?
    employee_pct_of_collected  the employee's share OF THE AMOUNT COLLECTED (0.10 = 10%)
    dealer_share_pct           what the CARRIER pays the dealer of the amount collected — carrier
                               economics, informational on income surfaces, never part of employee pay

Owner's stated facts, which are SEEDS FOR A HUMAN TO CONFIRM, not defaults invented here:
    Boost           dealer 100% · employee 10%   <- ALREADY PAID TODAY by calculator.py
    Total/luxelink  dealer  50% · employee  0%   <- pays nothing today, and still pays nothing after
                                                    this package until the owner raises the number

WHAT IS NOT FORKED (the [[accessory-flow-divergences]] lesson)
  Recognition REUSES the EXISTING per-tenant config `commcalc.accessory_config.setup_fee_keywords`
  (migration 217) — the same list `router._is_setup_fee` already drives the Sales Report, Executive MTD
  and the accessory-target basis from, and the same list `accessory_definition.is_setup_fee` already
  excludes from the accessory basis. This module adds NO new keyword store and NO sixth classifier; it
  adds the MONEY on top of the recognition that already exists.

WHY THE MATCH MODE IS EXPLICIT
  Two matchers exist today and they do NOT agree on case: `calculator.py` used a case-SENSITIVE literal
  (`'Device Setup Charge' in product`) on the PAY path, while `router._is_setup_fee` lower-cases both
  sides on the REPORT path. Silently unifying them would move Boost pay for any line whose spelling
  differs only in case — a money change nobody asked for. So the mode is config with the default
  `legacy_case_sensitive`, which reproduces the pay path EXACTLY, and `divergence()` MEASURES the
  disagreement so the operator can migrate deliberately (contract: get the operator's OK before
  unifying a classifier).

MULTI-TENANT: every loader takes org_id and scopes on it. Nothing here writes.
DEGRADES: with migration 263 unapplied every loader returns the code defaults — which are "nobody's
employee pay changes" — so the fleet is byte-identical and every page renders.
"""

LEGACY_SETUP_KEYWORDS = ["Device Setup Charge"]
MATCH_MODES = ("legacy_case_sensitive", "case_insensitive")

# The per-carrier economics. `employee_pct_of_collected` is deliberately None, NOT 0.0: None means
# "nobody has stated it" and pays nothing while saying so; an explicit 0 is a DECISION and is honoured
# silently. (Same rule as the fwa flat amount — a blank must never be read as a number.)
PAY_DEFAULTS = {
    "include_in_commission": False,
    "employee_pct_of_collected": None,
    "dealer_share_pct": None,
    "match_mode": "legacy_case_sensitive",
    # The employee's set-up-fee pay is its OWN pay item and is NEVER folded into the accessory basis
    # (standing owner rule [[setup-fee-separate-pay-item]]). It still counts toward the accessory
    # TARGET where that concept exists — which is already true today via `acc_plus_setup`, and this
    # package does not touch it.
    "counts_toward_accessory_target": True,
}


def _f(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).replace("$", "").replace(",", "").strip() or 0)
        except (TypeError, ValueError):
            return 0.0


def _pct_or_none(v):
    """float(v) or None. A BLANK stays None — 'not stated', never 0.0. An explicit '0' IS 0.0."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ══ recognition (reuses the mig-217 keyword list; adds no new store) ═════════════════════════════
def normalize_keywords(kws):
    """The tenant's set-up-fee keyword list, cleaned. Empty -> the legacy default. PURE."""
    out = [str(k).strip() for k in (kws or []) if str(k).strip()]
    return out or list(LEGACY_SETUP_KEYWORDS)


def is_setup_fee(product, keywords, match_mode="legacy_case_sensitive"):
    """Is this sale line the device set-up / activation fee? PURE.

    `legacy_case_sensitive` reproduces calculator.py's historic predicate EXACTLY — a case-sensitive
    substring test against each keyword — so the Boost pay path is byte-identical by construction.
    `case_insensitive` matches what the Sales Report / Executive MTD already do.
    """
    p = str(product or "")
    kws = normalize_keywords(keywords)
    if not p:
        return False
    if str(match_mode or "").strip().lower() == "case_insensitive":
        pl = p.strip().lower()
        return any(str(k).strip().lower() in pl for k in kws)
    return any(k in p for k in kws)


def divergence(rows, keywords):
    """Lines the two historic matchers DISAGREE about, with their dollars. PURE, read-only.

    This is the honest form of "should we unify the two set-up-fee classifiers?" — it MEASURES the
    disagreement on the tenant's own data instead of asserting there is none. An empty result means
    switching `match_mode` to case_insensitive moves $0.
    """
    kws = normalize_keywords(keywords)
    out = []
    for r in rows or []:
        p = str(r.get("product_desc") or "")
        strict = is_setup_fee(p, kws, "legacy_case_sensitive")
        loose = is_setup_fee(p, kws, "case_insensitive")
        if strict != loose:
            out.append({"product_desc": p[:200], "trans_id": str(r.get("trans_id") or "").strip(),
                        "date": str(r.get("trans_date") or "")[:10],
                        "salesperson": r.get("salesperson"), "store": r.get("store"),
                        "ext_price": round(_f(r.get("ext_price")), 2),
                        "matched_by": "case_insensitive_only" if loose else "case_sensitive_only"})
    return out


def candidates(rows, keywords, cap=60):
    """Every DISTINCT product description in the tenant's own sales that could be the fee, ranked by
    the money it carries, and flagged with whether the current mapping already catches it. PURE.

    This is what makes the mapping PICK-DON'T-TYPE (contract RULE THREE): the owner chooses from what
    their POS actually wrote — "Device Setup Charge", "Activation payment", "Access Charge - $25 …" —
    rather than typing a string that silently matches nothing. NOTHING is auto-selected: naming the
    fee is a money decision and it belongs to the owner.
    """
    kws = normalize_keywords(keywords)
    agg = {}
    for r in rows or []:
        p = str(r.get("product_desc") or "").strip()
        if not p:
            continue
        e = agg.setdefault(p, {"product_desc": p[:200], "lines": 0, "ext_price": 0.0, "gp": 0.0,
                               "transactions": set(), "first": None, "last": None})
        e["lines"] += 1
        e["ext_price"] = round(e["ext_price"] + _f(r.get("ext_price")), 2)
        e["gp"] = round(e["gp"] + _f(r.get("gp")), 2)
        t = str(r.get("trans_id") or "").strip()
        if t:
            e["transactions"].add(t)
        d = str(r.get("trans_date") or "")[:10]
        if d:
            e["first"] = d if e["first"] is None else min(e["first"], d)
            e["last"] = d if e["last"] is None else max(e["last"], d)
    out = []
    for e in agg.values():
        e["transactions"] = len(e["transactions"])
        e["mapped_now"] = is_setup_fee(e["product_desc"], kws, "case_insensitive")
        # A fee the STORE COLLECTS carries money on the line. A $0 line cannot be a collected fee, and
        # saying so stops the owner mapping a bookkeeping line by mistake.
        e["collects_money"] = e["ext_price"] > 0
        out.append(e)
    out.sort(key=lambda x: (not x["mapped_now"], -x["ext_price"], x["product_desc"]))
    return out[:cap]


# ══ per-carrier economics config ═════════════════════════════════════════════════════════════════
def normalize_pay_config(stored):
    """A stored `commission_org_config.setup_fee_pay` -> {'default': {...}, 'by_carrier': {id: {...}}}.

    PURE. None/garbage -> the code defaults, which pay nobody anything."""
    def one(d):
        out = dict(PAY_DEFAULTS)
        if not isinstance(d, dict):
            return out
        if "include_in_commission" in d:
            out["include_in_commission"] = bool(d["include_in_commission"])
        if "employee_pct_of_collected" in d:
            out["employee_pct_of_collected"] = _pct_or_none(d["employee_pct_of_collected"])
        if "dealer_share_pct" in d:
            out["dealer_share_pct"] = _pct_or_none(d["dealer_share_pct"])
        if "match_mode" in d:
            m = str(d["match_mode"] or "").strip().lower()
            out["match_mode"] = m if m in MATCH_MODES else PAY_DEFAULTS["match_mode"]
        if "counts_toward_accessory_target" in d:
            out["counts_toward_accessory_target"] = bool(d["counts_toward_accessory_target"])
        return out

    if not isinstance(stored, dict):
        return {"default": dict(PAY_DEFAULTS), "by_carrier": {}}
    # a flat dict (no 'default'/'by_carrier' envelope) is read as the org default — tolerant of the
    # shape a human would type into the SQL editor.
    if "default" not in stored and "by_carrier" not in stored:
        return {"default": one(stored), "by_carrier": {}}
    by = {}
    for k, v in (stored.get("by_carrier") or {}).items():
        if str(k or "").strip():
            by[str(k).strip()] = one(v)
    return {"default": one(stored.get("default")), "by_carrier": by}


def resolve_for_carrier(cfg, carrier_id=None):
    """(settings, source) for one carrier. Per-carrier row wins, else the org default. PURE."""
    if carrier_id and str(carrier_id).strip() in (cfg.get("by_carrier") or {}):
        return cfg["by_carrier"][str(carrier_id).strip()], "carrier"
    return cfg.get("default") or dict(PAY_DEFAULTS), "org_default"


def load_pay_config(client, org_id):
    """The tenant's set-up-fee economics (mig 263). Degrades to the code defaults; never raises."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("setup_fee_pay").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("setup_fee_pay")
    except Exception:
        stored = None
    out = normalize_pay_config(stored)
    out["_stored"] = isinstance(stored, dict)
    return out


def load_keywords(client, org_id):
    """The tenant's mig-217 set-up-fee keyword list — the SAME row the Sales Report / Executive MTD
    already read. Degrades to the legacy default; never raises."""
    try:
        rows = (client.schema("commcalc").table("accessory_config")
                .select("setup_fee_keywords").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            return normalize_keywords(rows[0].get("setup_fee_keywords"))
    except Exception:
        pass
    return list(LEGACY_SETUP_KEYWORDS)


# ══ the money ════════════════════════════════════════════════════════════════════════════════════
def collected(rows, keywords, match_mode="legacy_case_sensitive", skip=None):
    """(amount, lines) the stores COLLECTED in set-up/activation fees. PURE.

    `skip` is an optional callable(row) -> bool so a caller can honour the pay gate's exclusion map
    (mig 261) — an excluded line is not collected revenue for pay purposes either.
    """
    amt, n = 0.0, 0
    for r in rows or []:
        if not is_setup_fee(r.get("product_desc"), keywords, match_mode):
            continue
        if skip is not None:
            try:
                if skip(r):
                    continue
            except Exception:
                pass
        amt += _f(r.get("ext_price"))
        n += 1
    return round(amt, 2), n


def employee_pay(amount_collected, settings):
    """(pay, status) for one rep's collected fees under one carrier's settings. PURE.

    status is one of:
      'paid'            a percentage was stated and applied
      'excluded'        the tenant says this fee is not part of employee commission
      'unconfigured'    it IS meant to pay, but no percentage has been stated -> pays NOTHING and the
                        caller MUST warn. The engine never guesses a rate.
      'zero_by_choice'  an explicit 0 was stated (a decision, not a gap) -> silent
    """
    if not settings.get("include_in_commission"):
        return 0.0, "excluded"
    pct = settings.get("employee_pct_of_collected")
    if pct is None:
        return 0.0, "unconfigured"
    if pct == 0:
        return 0.0, "zero_by_choice"
    return round(float(pct) * _f(amount_collected), 2), "paid"


def dealer_share(amount_collected, settings):
    """(amount, stated) the CARRIER pays the dealer of what was collected. Informational — no employee
    payout reads this. `stated` is False when nobody has entered the percentage. PURE."""
    pct = settings.get("dealer_share_pct")
    if pct is None:
        return None, False
    return round(float(pct) * _f(amount_collected), 2), True
