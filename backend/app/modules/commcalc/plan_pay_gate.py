"""PAY GATE for the Commission-Plan engine — WHICH matched lines pay, HOW MANY times, and ON WHAT BASIS.

Four owner directives of 2026-08-01 all land at the SAME point in `commission_engine.preview()`: the
moment a rule has matched a sale line and the engine is about to turn that line into dollars. Rather
than four scattered branches, they are ONE gate with four independently-switchable concerns:

  ① UNIT DEDUP        "one trans id but paying out multiple times for the edge sale, one imie ca be
                       paid only once for the edge sale" / "any accessory or rate plan wil not paid
                       for the edge sale"
                      → a `flat_per_unit` rule that matches on a TRANSACTION-LEVEL field (the tender)
                        matches EVERY line of that transaction, and the engine pays per LINE. One
                        financed sale therefore paid 8 x $25. The gate collapses it to one payment per
                        DEVICE, anchored on the line that carries a device serial — which is also why
                        accessory / rate-plan / activation-fee / access-charge / wallet lines can never
                        carry it: they have no device serial.

  ② PAYOUT EXCLUSION  "there shgould be no paymentfor any rtr trasactions , again nothing hardocded,
                       but with mapping, map it in teh back end but let the user define going forward"
                      → a per-tenant EXCLUSION MAP. Ships with the RTR rule as a CODE-SEEDED
                        'confirmed' default (the owner ordered it mapped now) and is editable per
                        tenant thereafter. Matching is WORD-ANCHORED by default because 'RTR' is a
                        3-letter token and `contains` would bill 'CARTRIDGE' as an RTR transaction
                        (see [[edge-is-financing-not-device-model]] — the model-name collision class).

  ③ RULE SCOPE        "All activations are being paid $10 flat , this is only for NY employees, but
                       this empluee is in Chicago."
                      → a rule may state WHERE it applies (store / market / employee). Unscoped =
                        applies everywhere = today's behaviour, byte-identical.

  ⑤ ACCESSORY BASIS   "accessories not being paid , they should be paid as all of these have been
                       mapped"
                      → when a %-of-GP accessory line's GP is not believable (the mig-255
                        cost-integrity flags: cost == retail, negative cost, GP negative), pay the
                        percentage of the PRICE instead, and never pay a NEGATIVE accessory line.
                        DEFAULT OFF fleet-wide — a tenant switches it on knowingly.

WHAT IS AND IS NOT HARD-CODED
  Nothing here names a carrier, a tenant, a store or a product. ① keys on which match FIELD a rule
  uses (config, default `tender_type`) and on the STRUCTURAL serial test that
  `installment_category.serial_kind()` already provides fleet-wide. ② ships ONE seeded mapping whose
  every part — field, operator, value, enabled — is a config row the tenant can edit or delete. ③ and
  ⑤ read only the tenant's own rows. The one deliberate exception is stated out loud: the RTR seed is
  a default VALUE, not a branch, and `DEFAULT_EXCLUSIONS` is the single place it exists.

MULTI-TENANT: every loader takes `org_id` and scopes on it; nothing here writes.
DEGRADES: with migrations 260/261 unapplied every loader returns the code defaults, so the behaviour
is exactly the default described above and no page breaks.
"""
import re

from app.modules.commcalc import installment_category as _icat
from app.modules.commcalc import pay_data_quality as _pdq

# ── tables (mig 261) ──────────────────────────────────────────────────────────────────────────────
EXCLUSION_TABLE = "payout_exclusion_map"

# ── ① UNIT DEDUP ─────────────────────────────────────────────────────────────────────────────────
UNIT_BASES = ("per_line", "per_device", "per_transaction")

UNIT_DEFAULTS = {
    "enabled": True,
    # A rule matching on one of these fields is describing the TRANSACTION, not the line — every line
    # of the sale carries the same value, so a per-LINE payout multiplies by the receipt's length.
    # `tender_type` is the only such field in the engine's match vocabulary today; a tenant may add
    # more (or empty the list to switch the auto-detection off entirely).
    "auto_txn_level_fields": ["tender_type"],
    "default_basis": "per_device",
    # Which serial shapes identify a payable UNIT. 'imei' = a device (14-17 digits); 'iccid' = a SIM
    # (18-22). Owner rule: only the financed DEVICE line carries the payment.
    "unit_serial_kinds": ["imei"],
    # A line the tenant's own ACCESSORY DEFINITION calls an accessory can never be the anchor, even if
    # it somehow carries a device serial ("any accessory ... wil not paid for the edge sale").
    "exclude_accessory_units": True,
    # What to do with a transaction where the rule matched but NO line carries a payable serial (a
    # missing-IMEI import). 'once_per_transaction' pays ONE unit and warns; 'skip' pays nothing.
    # Default deliberately pays once: never silently zero a real sale over a data gap.
    "no_unit_fallback": "once_per_transaction",
}

# ── ② PAYOUT EXCLUSION ───────────────────────────────────────────────────────────────────────────
EXCLUSION_FIELDS = ("product_desc", "department", "category", "sku",
                    "contract_type", "trans_type", "tender_type")
EXCLUSION_OPS = ("word", "equals", "contains", "prefix", "suffix")

# THE ONE SEEDED VALUE IN THIS MODULE, and it is a config default, not a branch. Owner 2026-08-01:
# "there shgould be no paymentfor any rtr trasactions ... map it in teh back end but let the user
# define going forward". A tenant row in commcalc.payout_exclusion_map with the same (field, op,
# value) REPLACES this seed; a row with enabled=false switches it off.
DEFAULT_EXCLUSIONS = [
    {"code": "rtr", "label": "RTR (real-time refill / bill payment) transactions",
     "match_field": "product_desc", "match_op": "word", "match_value": "RTR",
     "enabled": True, "status": "confirmed", "source": "seed",
     "reason": ("RTR (real-time refill / bill payment) lines are not commissionable "
                "— owner directive 2026-08-01.")},
]

# ── ⑤ ACCESSORY BASIS GUARD ──────────────────────────────────────────────────────────────────────
ACC_BASIS_DEFAULTS = {
    # OFF fleet-wide. Switching it on changes accessory pay, so it is an explicit tenant decision.
    "enabled": False,
    # Which cost-integrity flags (pay_data_quality, mig 255) make GP unusable as a payout basis.
    "trigger_flags": ["cost_equals_price", "cost_negative", "cost_zero", "gp_negative"],
    # What to pay the percentage of when GP is unusable. 'ext_price' = the line's own selling price.
    "fallback_basis": "ext_price",
    # Optional haircut on that price (0.35 = "assume a 35% margin"). None = the full price. NEVER
    # invented: None means the tenant did not state one, and the full price is what the line sold for.
    "assumed_margin_pct": None,
    # An accessory line must never PAY NEGATIVE (a below-cost sale is not a rep clawback).
    "clamp_negative": True,
}

GATE_DEFAULTS = {"unit_basis": UNIT_DEFAULTS, "exclusions": {"enabled": True},
                 "accessory_basis_guard": ACC_BASIS_DEFAULTS}


# ══ config normalisation (PURE) ══════════════════════════════════════════════════════════════════
def _slist(v, allowed=None):
    out = []
    for x in (v if isinstance(v, (list, tuple)) else []):
        s = str(x or "").strip().lower()
        if s and (allowed is None or s in allowed) and s not in out:
            out.append(s)
    return out


def _num_or_none(v):
    """float(v) or None. A BLANK stays None — it is 'not stated', never 0.0 (the fwa-flat rule)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_gate_config(stored):
    """A stored `commission_org_config.plan_pay_gate` (dict or None) → the full config. PURE."""
    out = {"unit_basis": dict(UNIT_DEFAULTS), "exclusions": {"enabled": True},
           "accessory_basis_guard": dict(ACC_BASIS_DEFAULTS)}
    if not isinstance(stored, dict):
        return out
    u = stored.get("unit_basis")
    if isinstance(u, dict):
        d = out["unit_basis"]
        if "enabled" in u:
            d["enabled"] = bool(u["enabled"])
        if "auto_txn_level_fields" in u:
            d["auto_txn_level_fields"] = _slist(u["auto_txn_level_fields"])
        if "default_basis" in u:
            b = str(u["default_basis"] or "").strip().lower()
            d["default_basis"] = b if b in UNIT_BASES else UNIT_DEFAULTS["default_basis"]
        if "unit_serial_kinds" in u:
            d["unit_serial_kinds"] = _slist(u["unit_serial_kinds"], ("imei", "iccid"))
        if "exclude_accessory_units" in u:
            d["exclude_accessory_units"] = bool(u["exclude_accessory_units"])
        if "no_unit_fallback" in u:
            f = str(u["no_unit_fallback"] or "").strip().lower()
            d["no_unit_fallback"] = f if f in ("once_per_transaction", "skip") \
                else UNIT_DEFAULTS["no_unit_fallback"]
    e = stored.get("exclusions")
    if isinstance(e, dict) and "enabled" in e:
        out["exclusions"]["enabled"] = bool(e["enabled"])
    a = stored.get("accessory_basis_guard")
    if isinstance(a, dict):
        d = out["accessory_basis_guard"]
        if "enabled" in a:
            d["enabled"] = bool(a["enabled"])
        if "trigger_flags" in a:
            d["trigger_flags"] = _slist(a["trigger_flags"], tuple(_pdq.FLAG_LABELS))
        if "fallback_basis" in a:
            b = str(a["fallback_basis"] or "").strip().lower()
            d["fallback_basis"] = b if b in ("ext_price",) else "ext_price"
        if "assumed_margin_pct" in a:
            d["assumed_margin_pct"] = _num_or_none(a["assumed_margin_pct"])
        if "clamp_negative" in a:
            d["clamp_negative"] = bool(a["clamp_negative"])
    return out


def load_gate_config(client, org_id):
    """The tenant's pay-gate config (mig 260). Degrades to the code defaults — never raises."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("plan_pay_gate").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("plan_pay_gate")
    except Exception:
        stored = None
    cfg = normalize_gate_config(stored)
    cfg["_stored"] = isinstance(stored, dict)
    return cfg


# ══ ① UNIT DEDUP ═════════════════════════════════════════════════════════════════════════════════
def _norm_serial(v):
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def _f(v):
    return _pdq._f(v)


def resolve_unit_basis(rule, ucfg):
    """(basis, source) for ONE rule. PURE.

    Precedence:
      1. the rule's OWN `unit_basis` (a human said so)                        -> source 'rule'
      2. auto: a flat_per_unit rule matching on a TRANSACTION-LEVEL field     -> source 'auto_txn_field'
      3. 'per_line' — today's behaviour                                       -> source 'default'

    ONLY `flat_per_unit` is ever deduped. A %-of-basis rule reads each line's OWN price/GP/MRC, so
    collapsing its lines would silently delete dollars rather than stop double-paying them.
    """
    kind = str(rule.get("payout_kind") or "flat_per_unit").strip().lower()
    if kind != "flat_per_unit":
        want = str(rule.get("unit_basis") or "").strip().lower()
        if want in UNIT_BASES and want != "per_line":
            return "per_line", "ignored_non_flat_per_unit"
        return "per_line", "default"
    want = str(rule.get("unit_basis") or "").strip().lower()
    if want in UNIT_BASES:
        return want, "rule"
    if not ucfg.get("enabled", True):
        return "per_line", "disabled"
    field = str(rule.get("match_field") or "any").strip().lower()
    if field in (ucfg.get("auto_txn_level_fields") or []):
        b = str(ucfg.get("default_basis") or "per_device").strip().lower()
        return (b if b in UNIT_BASES else "per_device"), "auto_txn_field"
    return "per_line", "default"


def _pick_key(row):
    """Deterministic 'best line' key — highest ext_price first, then a stable value-based tiebreak.
    The SAME shape `commission_engine._activation_buckets` already uses for its rescue representative,
    so the two collapses agree and neither depends on row order. PURE."""
    return (-_f(row.get("ext_price")), str(row.get("product_desc") or ""),
            str(row.get("sku") or ""), str(row.get("serial_1") or ""), str(row.get("mdn") or ""))


def select_paying_lines(matched_rows, basis, ucfg, is_accessory=None):
    """Which of a rule's MATCHED lines actually pay, under `basis`.

    Returns (payers, suppressed, notes) where
      payers      = [row, …]                     — pay exactly as today, one payout each
      suppressed  = [(row, reason_code), …]      — matched, displayed, but pay $0
      notes       = [{code, trans_id, …}, …]     — things the operator must be told

    PURE (no I/O). `is_accessory` is an optional callable(row) -> bool supplied by the caller so this
    module never becomes a sixth accessory classifier.
    """
    rows = list(matched_rows or [])
    if basis == "per_line" or not rows:
        return rows, [], []

    kinds = set(ucfg.get("unit_serial_kinds") or UNIT_DEFAULTS["unit_serial_kinds"])
    excl_acc = bool(ucfg.get("exclude_accessory_units", True)) and callable(is_accessory)
    fallback = str(ucfg.get("no_unit_fallback") or "once_per_transaction")

    # group by transaction. A BLANK trans_id cannot be grouped — each such line stays its own group so
    # two unrelated sales are never merged into one payment by a missing id.
    groups, blanks = {}, []
    for r in rows:
        tid = str(r.get("trans_id") or "").strip()
        if tid:
            groups.setdefault(tid, []).append(r)
        else:
            blanks.append(r)

    payers, suppressed, notes = [], [], []
    payers.extend(blanks)
    if blanks:
        notes.append({"code": "unit_blank_trans_id", "lines": len(blanks),
                      "detail": (f"{len(blanks)} matched line(s) carry no transaction id, so they could "
                                 f"not be grouped and each still pays once.")})

    for tid in sorted(groups):
        grp = groups[tid]
        if len(grp) == 1 and basis == "per_transaction":
            payers.append(grp[0])
            continue
        if basis == "per_transaction":
            best = min(grp, key=_pick_key)
            payers.append(best)
            for r in grp:
                if r is not best:
                    suppressed.append((r, "unit_same_transaction"))
            continue

        # per_device: the payment anchors on a line carrying a payable device serial.
        anchors = {}
        for r in grp:
            if _icat.serial_kind(r.get("serial_1")) not in kinds:
                continue
            if excl_acc:
                try:
                    if is_accessory(r):
                        continue
                except Exception:
                    pass
            k = _norm_serial(r.get("serial_1"))
            cur = anchors.get(k)
            if cur is None or _pick_key(r) < _pick_key(cur):
                anchors[k] = r
        if anchors:
            chosen = set(id(x) for x in anchors.values())
            for k in sorted(anchors):
                payers.append(anchors[k])
            for r in grp:
                if id(r) not in chosen:
                    suppressed.append((r, "unit_not_device_line"))
            if len(grp) > len(anchors):
                notes.append({"code": "unit_collapsed", "trans_id": tid,
                              "matched_lines": len(grp), "units_paid": len(anchors),
                              "detail": (f"transaction {tid}: {len(grp)} matched line(s) collapsed to "
                                         f"{len(anchors)} device unit(s).")})
            continue
        # no payable serial anywhere in this transaction
        if fallback == "skip":
            for r in grp:
                suppressed.append((r, "unit_no_device_id_skipped"))
            notes.append({"code": "unit_no_device_id", "trans_id": tid, "matched_lines": len(grp),
                          "units_paid": 0,
                          "detail": (f"transaction {tid}: no matched line carries a device serial and the "
                                     f"tenant's fallback is 'skip', so it paid nothing.")})
        else:
            best = min(grp, key=_pick_key)
            payers.append(best)
            for r in grp:
                if r is not best:
                    suppressed.append((r, "unit_same_transaction"))
            if len(grp) > 1:
                # a single serial-less matched line pays exactly once either way — nothing happened,
                # so nothing is reported. The guard stays signal, not noise.
                notes.append({"code": "unit_no_device_id", "trans_id": tid, "matched_lines": len(grp),
                              "units_paid": 1,
                              "detail": (f"transaction {tid}: no matched line carries a device serial "
                                         f"(missing IMEI in the import), so it paid ONCE for the whole "
                                         f"transaction instead of once per device.")})
    return payers, suppressed, notes


SUPPRESS_LABELS = {
    "unit_not_device_line": "Not the financed device line — this sale's payment is made once per "
                            "device, on the line that carries the device serial.",
    "unit_same_transaction": "Already paid once for this transaction.",
    "unit_no_device_id_skipped": "No line of this transaction carries a device serial, and this "
                                 "tenant's setting is to pay nothing in that case.",
    "excluded": "Excluded from payout by the tenant's payout-exclusion mapping.",
}


# ── the tenant's ACCESSORY DEFINITION, as a predicate (mig 257) ──────────────────────────────────
def definition_drives_pay(client, org_id):
    """Does this tenant's ACCESSORY DEFINITION (mig 257) also decide what the plan engine PAYS?
    (`accessory_config.definition_drives_pay`, mig 276.)

    DEFAULT FALSE, and FALSE on any error/missing column — so a tenant that has never touched it, and
    every tenant before migration 276 runs, takes exactly the pre-2026-08-05 engine branch and no
    payout number moves. Read-only; never raises.

    WHY THIS EXISTS: the owner maps products on /commcalc/accessory-definition, which writes
    `accessory_definition_map`. The money path's synthetic `accessory` match_field is stamped by
    `accessory_catalog.AccessoryClassifier`, which reads a DIFFERENT surface (accessory_config's
    dept/category/keyword lists + the raw_catalog category layer) and has never read the definition —
    migration 257 states that explicitly. A rule `accessory equals yes` therefore matched ZERO lines
    for products the owner had mapped, which is the luxelink trans-3207 $0 report of 2026-08-05.
    This switch is how a tenant closes that gap knowingly."""
    try:
        rows = (client.schema("commcalc").table("accessory_config")
                .select("definition_drives_pay").eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return False
    if not rows:
        return False
    return bool(rows[0].get("definition_drives_pay"))


def accessory_predicate(client, org_id):
    """callable(row) -> bool built from the tenant's OWN accessory definition, or None when the tenant
    has none. NOT a new classifier — it calls `accessory_definition.classify()`, the mapping surface the
    owner already curates ("all of these have been mapped"). Confirmed mappings only; a set-up fee is
    never an accessory (standing owner rule). Degrades to None on any error."""
    try:
        from app.modules.commcalc import accessory_definition as _ad
    except Exception:
        return None
    map_rows = []
    try:
        map_rows = (client.schema("commcalc").table(_ad.MAP_TABLE).select("*")
                    .eq("org_id", org_id).limit(100000).execute().data) or []
    except Exception:
        map_rows = []
    stored = None
    try:
        rows = (client.schema("commcalc").table("accessory_config")
                .select("definition_field_rule,setup_fee_products").eq("org_id", org_id)
                .limit(1).execute().data) or []
        if rows:
            stored = rows[0]
    except Exception:
        stored = None
    try:
        rule, _refused = _ad.normalize_field_rule((stored or {}).get("definition_field_rule"))
        index = _ad.build_index(map_rows)
        setup_kws = set((stored or {}).get("setup_fee_products") or ())
    except Exception:
        return None
    if not map_rows and not (rule or {}).get("enabled", True):
        return None

    def _is_acc(row):
        try:
            return bool(_ad.classify(row, index, rule, setup_kws, mode="confirmed").get("is_accessory"))
        except Exception:
            return False
    return _is_acc


# ══ ② PAYOUT EXCLUSION ═══════════════════════════════════════════════════════════════════════════
def normalize_exclusion(row):
    """One stored exclusion row → the canonical shape, or None when unusable. PURE."""
    if not isinstance(row, dict):
        return None
    f = str(row.get("match_field") or "").strip().lower()
    op = str(row.get("match_op") or "word").strip().lower()
    v = str(row.get("match_value") or "").strip()
    if f not in EXCLUSION_FIELDS or not v:
        return None
    if op not in EXCLUSION_OPS:
        op = "word"
    st = str(row.get("status") or "confirmed").strip().lower()
    return {"id": row.get("id"), "code": (row.get("code") or "").strip() or None,
            "label": row.get("label") or None, "match_field": f, "match_op": op, "match_value": v,
            "enabled": bool(row.get("enabled", True)), "status": st if st in ("confirmed", "proposed") else "confirmed",
            "reason": row.get("reason") or None, "source": row.get("source") or "tenant"}


def load_exclusions(client, org_id, include_proposed=False):
    """(rules, ready). The tenant's exclusion map, layered over the code seed.

    A tenant row with the SAME (match_field, match_op, lower(match_value)) as a seed REPLACES it — so
    `enabled=false` on that row switches the seed off without deleting anything. Degrades to the seed
    alone when migration 261 is unapplied; never raises."""
    stored, ready = [], True
    try:
        stored = (client.schema("commcalc").table(EXCLUSION_TABLE).select("*")
                  .eq("org_id", org_id).limit(2000).execute().data) or []
    except Exception:
        stored, ready = [], False
    out, seen = [], {}
    for r in stored:
        n = normalize_exclusion(r)
        if not n:
            continue
        n["source"] = n.get("source") or "tenant"
        key = (n["match_field"], n["match_op"], n["match_value"].lower())
        seen[key] = n
        out.append(n)
    for s in DEFAULT_EXCLUSIONS:
        key = (s["match_field"], s["match_op"], s["match_value"].lower())
        if key in seen:
            continue
        out.append(dict(s))
    if not include_proposed:
        out = [r for r in out if r.get("status") == "confirmed"]
    return [r for r in out if r.get("enabled", True)], ready


def _exc_value(row, field):
    return str(row.get(field, "") or "").strip()


def exclusion_hit(row, rules):
    """The FIRST exclusion rule this sale line trips, else None. PURE.

    `word` (the default and the RTR seed's operator) is WORD-ANCHORED: it matches the token, never a
    substring. This is the guard against the model-name collision class — 'RTR' inside 'CARTRIDGE' or
    'PARTRIDGE' is not an RTR transaction, and a `contains` rule would have said it was."""
    for r in rules or []:
        have = _exc_value(row, r["match_field"])
        if not have:
            continue
        want, op = r["match_value"], r["match_op"]
        hl, wl = have.lower(), want.lower()
        hit = False
        if op == "word":
            hit = re.search(r"(?<![0-9A-Za-z])" + re.escape(want) + r"(?![0-9A-Za-z])",
                            have, re.IGNORECASE) is not None
        elif op == "equals":
            hit = hl == wl
        elif op == "contains":
            hit = wl in hl
        elif op == "prefix":
            hit = hl.startswith(wl)
        elif op == "suffix":
            hit = hl.endswith(wl)
        if hit:
            return r
    return None


# ══ ③ RULE SCOPE ═════════════════════════════════════════════════════════════════════════════════
SCOPE_KINDS = ("store", "market", "employee")


def _canon(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def rule_scope(rule):
    """(kind, [values]) for a rule's optional WHERE-IT-APPLIES scope, or (None, []). PURE."""
    k = str(rule.get("applies_scope_kind") or "").strip().lower()
    if k not in SCOPE_KINDS:
        return None, []
    raw = rule.get("applies_scope_value")
    vals = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    vals = [_canon(v) for v in vals]
    vals = [v for v in vals if v]
    if not vals:
        return None, []
    return k, vals


def rule_applies_here(rule, store="", market="", rep="", store_keys=None):
    """(applies, reason). An UNSCOPED rule applies everywhere — today's behaviour, byte-identical.

    A scoped rule applies only where the rep's own store / market / name matches. `store_keys` is the
    optional set of alias-resolved store identities the engine already computes (mig 249), so a scoped
    rule matches the same store spellings a store-scope plan assignment does. PURE."""
    kind, vals = rule_scope(rule)
    if not kind:
        return True, "unscoped"
    if kind == "store":
        have = {_canon(store)} | {_canon(x) for x in (store_keys or [])}
    elif kind == "market":
        have = {_canon(market)}
    else:
        have = {_canon(rep)}
    have.discard("")
    if have & set(vals):
        return True, f"scope_{kind}_match"
    return False, f"scope_{kind}_miss"


# ══ ⑤ ACCESSORY BASIS GUARD ══════════════════════════════════════════════════════════════════════
def guarded_pct_gp(row, pct, acfg, cost_cfg=None, is_accessory=True):
    """(amount, basis_used, flags, note) for ONE %-of-GP accessory line. PURE.

    OFF (the default) or a non-accessory line -> (None, …) meaning "the caller pays exactly as today".
    ON: when the line's GP trips one of the tenant's cost-integrity flags the percentage is paid on the
    PRICE instead (optionally haircut by an assumed margin the tenant states), and an accessory line
    can never pay a negative amount.

    Returns amount=None when nothing should change, so a caller that ignores this module is unaffected.
    """
    if not acfg or not acfg.get("enabled"):
        return None, None, [], None
    if not is_accessory:
        return None, None, [], None
    flags = _pdq.line_flags(row.get("ext_price"), row.get("gp"), cost_cfg)
    trig = set(acfg.get("trigger_flags") or ACC_BASIS_DEFAULTS["trigger_flags"])
    hit = [f for f in flags if f in trig]
    gp = _f(row.get("gp"))
    if not hit:
        if acfg.get("clamp_negative", True) and gp < 0 and pct > 0:
            return 0.0, "gp_clamped", ["gp_negative_clamped"], (
                "GP is negative on this accessory line; a %-of-GP payout would have been negative, "
                "which is not a rep clawback. Clamped to $0.")
        return None, None, [], None
    basis = _f(row.get("ext_price"))
    margin = acfg.get("assumed_margin_pct")
    if margin is not None:
        basis = basis * float(margin)
    amt = round(float(pct) * basis, 2)
    if acfg.get("clamp_negative", True) and amt < 0:
        amt = 0.0
    codes = list(hit)
    note = ("GP is not usable as a payout basis on this line (" + ", ".join(hit) + "), so the rate was "
            "paid on the selling price" + (f" x {margin}" if margin is not None else "") + " instead.")
    return amt, ("ext_price" if margin is None else "ext_price_margin"), codes, note
