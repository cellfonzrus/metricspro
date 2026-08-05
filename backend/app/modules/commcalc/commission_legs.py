"""Commission LEG attribution — 1st-month vs M2–M12 (owner directive 2026-08-04).

    "i need the gross profit report to have commission split in 2 parts - 1st Month commission which is
     paid the same month of the activation and the other is M2-M12 commission, any commission received
     for an activated number after the activated month will be in this category, this will also create a
     trend alignment with the 3MR and 6MR to assess how they affect the commission payout."

MONEY RULE — THIS FILE IS READ-ONLY WITH RESPECT TO PAY. It never writes, never feeds `_run_calculation`,
`rep_commissions`, the plan/installment engines or the P&L. It DECOMPOSES commission money the org has
already received into two legs so the Gross Profit report can show them side by side. The decomposition
is exact by construction: every dollar of a source is assigned to exactly one of `m1` / `trailing` /
`unsplit`, so m1 + trailing + unsplit == the source's existing total, to the cent, always.

WHY THREE BUCKETS FOR A TWO-PART ANSWER
The owner asked for two columns. Some carrier money genuinely does not state a month-of-life anywhere in
its source (e.g. ePay's "Boost Auto Top-Up", "2026 SIM card reimbursement"). Guessing which leg those
belong to would silently move money between the two columns the owner wants to trust. So a third,
explicitly-labelled `unsplit` bucket holds exactly that money, the page names it, and an admin resolves
it per label on /commcalc/commission-legs (commcalc.commission_leg_label_map). For an org that has mapped
everything, `unsplit` is $0 and the report is literally two columns.

HOW EACH SOURCE SPLITS (verified against the org's real export files, 2026-08-04)
  • ePay Commission Payment Detail (#50273 → raw_payment_detail) and the Comprehensive Compensation
    Report (#100614 → raw_comp_report) both name the leg IN THE TYPE STRING:
    "New Activation Bounty - Month 1" … "- Month 6", "Simplified SIM Loading Bounty - Month N",
    "Boost Ready Bounty - Month N", "Device Upgrade Bounty - Month N", "(In-Store) Device Financing
    Bounty - Month N", "BR BYOD SPIFF - Month N", "Boost 5G Network Migration Bounty - Month N".
    Month 1 = the activation-month leg, Month 2+ = money for an already-activated number.
    (`discrepancy_engine.parse_payment_type` has read that same token since the first commission build —
    this module reuses the idea, config-driven, instead of hard-coding a second copy of the vocabulary.)
    ⚠ The Payment Detail export HAS an "Activation Date/Swap Date" column but it is 100% NULL in the real
    file (30,339 / 30,339 rows on the Apr-2026 run), so a date-based split is NOT available here. The
    label is the only month-of-life the source carries.
  • VidaPay / master-agent (raw_ma_commission) names the leg in the COLUMN: spiff_m1 is the M1 leg,
    spiff_m2..spiff_m6 the trailing legs. NOTHING ELSE ON THAT EXPORT IS A COMMISSION LEG.
    ⚠ CORRECTED 2026-08-05 (`ma_m1_fields` default was the six margin columns, now empty).
    The first draft of this file put the activation-order MARGIN columns (rebate, device_margin,
    consumer_margin, consumer_financing, wallet_funding, fees_margin) into M1 "because they are
    recognised at activation". That over-stated the org's 1st-month commission by the whole margin
    block (owner-reported 2026-08-05: our M1 ~$124k vs VidaPay's stated M1 ~$28k, a 4.4x gap that is
    exactly Sigma(margins)). Three things in this repo already said otherwise and the leg split did not
    follow them:
      – OWNER, verbatim 2026-08-04 (recorded in ma_overview.py): "commission is only the current months
        commission paid out on the activations which would be M1, these are not margins but paid
        commission based on MRC." The /ma-overview-recon "Commissions Paid (M1)" tile therefore reads
        `spiff_m1` ALONE, and was live-verified at $17,140.91 for luxelink Feb-Jul 2026.
      – The canonical Commission Ledger's map of the SAME twelve columns
        (`ledger_ma_sync.DEFAULT_COMPONENTS`) carries `payment_month: None` on all six margins and
        `payment_month: 1` on spiff_m1 only.
      – VidaPay itself reports Rebates Paid and Fees Margin Paid as their OWN tiles, separate from
        Commissions Paid — so folding them into M1 double-counts them against the portal's own figure.
    The margins are real money the dealer receives and they stay in the COMMISSION COLUMN TOTAL, which
    does not move by one cent; they simply carry no month-of-life, so they sit in the honest `unsplit`
    bucket next to it. An org that wants them in M1 puts them back in `ma_m1_fields` (config, per org
    and carrier) — the behaviour is available, it is just no longer the default.
  • ePay MI/ATU residual (raw_mi) carries `mi_activation_date`, so residual splits on the owner's LITERAL
    definition: activation month == report month → 1st month; earlier → M2–M12; missing/unparseable →
    unsplit (never guessed).

Everything above is CONFIG (commcalc.commission_leg_config + commission_leg_label_map, migration 274),
resolved per (org, carrier) at runtime — no carrier or tenant name appears in the compute path. With the
migration un-run the module falls back to the code defaults below, so the GP report keeps working.
"""
import re as _re

# ── PURE LEAF, ON PURPOSE ────────────────────────────────────────────────────────────────────────
# `gp_report.py` is deliberately import-free (`from typing import Any` and nothing else) and
# `calculator.py` imports gp_report — so a module-level `from ...calculator import safe_float` here
# would close the cycle gp_report -> commission_legs -> calculator -> gp_report the moment the GP
# engine imports this file. The three helpers below are therefore LOCAL copies, each mirroring the
# ONE original that governs the number it protects:
#   `_safe_float`        mirrors **gp_report.safe_float** — the coercion the GP totals themselves are
#                        built with. It must be that one, not calculator's or ma_overview's more
#                        lenient variants, because the whole promise of this module is that the split
#                        re-sums to gp_report's totals to the cent.
#   `_parse_loose_date`  mirrors imei_rebate_report.parse_loose_date (ISO and US spellings, '-' or '/'
#                        separators; anything else -> None, never a guessed date)
#   `_period_ym`         mirrors imei_rebate_report.period_ym / device_history.canon_display_period
#                        ('June 2026' and '2026-06' — the two spellings that actually occur)
# `harness_commission_leg_split.py` asserts each one EQUAL to its original across a table of real and
# adversarial inputs, so this is a checked equivalence rather than a copy that can silently drift.
# (That harness is how the drift in the first draft of this file was caught: an over-clever copy
# accepted "$1,234.50" and "2026/04/15" that the originals do not, and rejected NaN that they do.)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], start=1)}
_D_ISO = _re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_D_US = _re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})")


def _safe_float(v) -> float:
    """Byte-identical to gp_report.safe_float — the coercion the GP money columns are summed with."""
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _parse_loose_date(v):
    """'YYYY-MM-DD' from a date-ish value, or None. raw_mi's date columns are TEXT holding
    `str(value)[:10]` of whatever the carrier report held, so BOTH the ISO and the US spellings occur,
    with either separator. Anything else -> None (never a guessed date)."""
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    m = _D_ISO.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _D_US.match(s)
        if not m:
            return None
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2999):
        return None
    return "%04d-%02d-%02d" % (y, mo, d)


def _period_ym(period):
    """(year, month) of a month-period written EITHER way ('June 2026' / '2026-06'), else None. The
    period-spelling duality is a recurring bug class here — both spellings MUST resolve to one month."""
    s = ("" if period is None else str(period)).strip()
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit():
        mo, yr = int(s[5:7]), int(s[:4])
    else:
        parts = s.split()
        if len(parts) == 2 and parts[0].lower() in _MONTHS and parts[1].isdigit():
            mo, yr = _MONTHS[parts[0].lower()], int(parts[1])
        else:
            return None
    return (yr, mo) if (1 <= mo <= 12 and yr) else None


ORG_ID = "00000000-0000-0000-0000-000000000001"
_NIL_CARRIER = "00000000-0000-0000-0000-000000000000"

# The three attribution buckets. `trailing` is the owner's "M2-M12".
M1 = "m1"
TRAILING = "trailing"
UNSPLIT = "unsplit"
BUCKETS = (M1, TRAILING, UNSPLIT)

# Code default = what the two seeded HOUSE rows in migration 274 contain, so behaviour is identical
# whether or not the migration has been applied.
DEFAULT_CFG = {
    "label_month_regex": r"month\s*[-#:]?\s*(\d+)",
    "m1_month": 1,
    "max_leg_month": 12,
    "unlabeled_bucket": UNSPLIT,
    "ma_month_field_prefix": "spiff_m",
    "ma_max_month": 6,
    # EMPTY BY DEFAULT (corrected 2026-08-05 — see the module docstring). Only `spiff_m1` is the MA
    # 1st-month COMMISSION leg. Any column named here is ADDITIONALLY forced into M1; the six
    # activation-order margin columns used to be listed here and inflated M1 by the whole margin block.
    "ma_m1_fields": [],
    "ma_payout_sign": -1.0,
    "mi_split_by_activation": True,
}

_CFG_KEYS = tuple(DEFAULT_CFG.keys())


# ── config resolution (org+carrier → org mode-default → house mode-default → code) ────────────────
def resolve_leg_config(client, org_id, carrier_id=None, carrier_mode="boost"):
    """The org's leg-attribution config. Same resolution ladder as mig 223's gate-source config, so an
    admin can override per carrier while every tenant inherits the seeded house defaults. NEVER raises:
    a missing table (migration un-run) or a query error returns the code default."""
    cfg = dict(DEFAULT_CFG)
    cfg["_resolved_from"] = "code_default"
    mode = (carrier_mode or "boost").strip().lower() or "boost"
    try:
        rows = (client.schema("commcalc").table("commission_leg_config").select("*")
                .in_("org_id", [org_id, ORG_ID]).eq("is_active", True).execute().data) or []
    except Exception:
        return cfg
    org_rows = [r for r in rows if str(r.get("org_id")) == str(org_id)]
    house_rows = [r for r in rows if str(r.get("org_id")) == ORG_ID]

    chosen, src = None, None
    if carrier_id:
        chosen = next((r for r in org_rows if str(r.get("carrier_id")) == str(carrier_id)), None)
        src = "org_carrier" if chosen else None
    if chosen is None:
        chosen = next((r for r in org_rows if str(r.get("carrier_id")) == _NIL_CARRIER
                       and (r.get("carrier_mode") or "boost") == mode), None)
        src = "org_mode_default" if chosen else src
    if chosen is None:
        chosen = next((r for r in house_rows if str(r.get("carrier_id")) == _NIL_CARRIER
                       and (r.get("carrier_mode") or "boost") == mode), None)
        src = "house_mode_default" if chosen else src
    if chosen is None:
        return cfg
    for k in _CFG_KEYS:
        v = chosen.get(k)
        if v is not None and v != "" and v != []:
            cfg[k] = v
    cfg["_resolved_from"] = src or "code_default"
    return cfg


def load_label_map(client, org_id):
    """{lowercased label: {'bucket', 'leg_month'}} — the org's explicit per-label overrides.
    {} when the table is absent (migration un-run) or empty."""
    try:
        rows = (client.schema("commcalc").table("commission_leg_label_map")
                .select("label,bucket,leg_month").eq("org_id", org_id).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        lbl = str(r.get("label") or "").strip().lower()
        b = str(r.get("bucket") or "").strip().lower()
        if lbl and b in BUCKETS:
            lm = r.get("leg_month")
            out[lbl] = {"bucket": b, "leg_month": int(lm) if lm not in (None, "") else None}
    return out


# ── pure classification ──────────────────────────────────────────────────────────────────────────
def _int(v, dflt):
    try:
        return int(v)
    except (TypeError, ValueError):
        return dflt


def leg_month_from_label(label, cfg=None):
    """The month-of-life stated in a carrier payment/compensation label, or None. PURE.
    'New Activation Bounty - Month 3' -> 3 · 'Boost Auto Top-Up' -> None."""
    cfg = cfg or DEFAULT_CFG
    s = str(label or "").strip()
    if not s:
        return None
    pat = str(cfg.get("label_month_regex") or DEFAULT_CFG["label_month_regex"])
    try:
        m = _re.search(pat, s, _re.IGNORECASE)
    except _re.error:                      # an admin saved a broken regex — fall back, never 500
        m = _re.search(DEFAULT_CFG["label_month_regex"], s, _re.IGNORECASE)
    if not m or not m.groups():
        return None
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def bucket_for_leg(leg_month, cfg=None):
    """Month-of-life -> bucket. PURE. m1_month is the 1st-month leg; anything later is trailing."""
    cfg = cfg or DEFAULT_CFG
    if leg_month is None:
        return UNSPLIT
    m1 = _int(cfg.get("m1_month"), 1)
    return M1 if leg_month == m1 else (TRAILING if leg_month > m1 else UNSPLIT)


def classify_label(label, cfg=None, label_map=None):
    """(bucket, leg_month, why) for one carrier payment/compensation label. PURE.
    Order: explicit per-org override -> month token in the label -> the org's unlabeled_bucket."""
    cfg = cfg or DEFAULT_CFG
    key = str(label or "").strip().lower()
    ov = (label_map or {}).get(key)
    if ov:
        return ov["bucket"], ov.get("leg_month"), "label_override"
    n = leg_month_from_label(label, cfg)
    if n is not None:
        return bucket_for_leg(n, cfg), n, "month_in_label"
    fallback = str(cfg.get("unlabeled_bucket") or UNSPLIT).strip().lower()
    if fallback not in BUCKETS:
        fallback = UNSPLIT
    return fallback, (1 if fallback == M1 else None), "no_month_in_label"


def leg_month_from_dates(period, activation_date):
    """Month-of-life of a subscriber in a report month: activation month == report month -> 1.
    None when either side is missing/unparseable, or the activation is AFTER the report month (a data
    oddity we refuse to guess about). PURE — no DB, no clock."""
    pym = _period_ym(period)
    a = _parse_loose_date(activation_date)
    if not pym or not a:
        return None
    ay, am = int(a[:4]), int(a[5:7])
    off = (pym[0] - ay) * 12 + (pym[1] - am)
    return off + 1 if off >= 0 else None


def classify_activation(period, activation_date, cfg=None):
    """(bucket, leg_month, why) for residual-style money whose source carries an activation DATE. PURE."""
    cfg = cfg or DEFAULT_CFG
    if not cfg.get("mi_split_by_activation", True):
        return UNSPLIT, None, "activation_split_disabled"
    n = leg_month_from_dates(period, activation_date)
    if n is None:
        return UNSPLIT, None, "no_activation_date"
    return bucket_for_leg(n, cfg), n, "activation_date"


def ma_field_leg(field, cfg=None):
    """(bucket, leg_month) for ONE raw_ma_commission money column. PURE.
    spiff_mN -> leg N; a column the org has EXPLICITLY listed in `ma_m1_fields` -> leg 1 (empty by
    default: the margin columns are not commission legs); anything else -> unsplit."""
    cfg = cfg or DEFAULT_CFG
    f = str(field or "").strip().lower()
    prefix = str(cfg.get("ma_month_field_prefix") or DEFAULT_CFG["ma_month_field_prefix"]).lower()
    if prefix and f.startswith(prefix):
        tail = f[len(prefix):]
        if tail.isdigit():
            n = int(tail)
            if 1 <= n <= _int(cfg.get("ma_max_month"), 6):
                return bucket_for_leg(n, cfg), n
            return TRAILING if n > _int(cfg.get("m1_month"), 1) else UNSPLIT, n
    m1f = {str(x).strip().lower() for x in (cfg.get("ma_m1_fields") or []) if str(x).strip()}
    if f in m1f:
        return M1, _int(cfg.get("m1_month"), 1)
    return UNSPLIT, None


def split_ma_components(sums, components, cfg=None):
    """Split summed raw_ma_commission components into the three buckets + the leg ladder. PURE.

    `sums`: {column: raw summed value}. `components`: the EXACT column list the caller's own total is
    built from (the router passes account.residual_subs._MA_COMPONENTS) — iterating the caller's list is
    what makes m1+trailing+unsplit identical to that caller's total, cent for cent.
    Applies the configured ma_payout_sign so the result is money the dealer RECEIVES (positive)."""
    cfg = cfg or DEFAULT_CFG
    raw_sign = _safe_float(cfg.get("ma_payout_sign"))
    sign = 1.0 if raw_sign > 0 else -1.0
    out = {M1: 0.0, TRAILING: 0.0, UNSPLIT: 0.0}
    ladder, fields = {}, {}
    for c in components:
        amt = _safe_float((sums or {}).get(c)) * sign
        b, leg = ma_field_leg(c, cfg)
        out[b] += amt
        fields[c] = {"bucket": b, "leg_month": leg, "amount": round(amt, 2)}
        key = leg if leg is not None else "unknown"
        ladder[key] = round(ladder.get(key, 0.0) + amt, 2)
    return {"buckets": {k: round(v, 2) for k, v in out.items()},
            "leg_ladder": ladder, "fields": fields,
            # The columns whose money carries NO month-of-life, so a surface can NAME them instead of
            # showing an unexplained 'unsplit' pile (the margin block lives here by default).
            "unsplit_fields": [c for c in components if fields.get(c, {}).get("bucket") == UNSPLIT],
            "total": round(sum(out.values()), 2)}


# ── the injectable classifier bundle the GP engine uses ──────────────────────────────────────────
class LegClassifier:
    """What `gp_report.calc_gp_report` is handed so it can bucket each source line WITHOUT knowing any
    carrier vocabulary. Construct from resolved config (`for_org`) or bare for the pure code default."""

    def __init__(self, cfg=None, label_map=None):
        self.cfg = dict(cfg or DEFAULT_CFG)
        self.label_map = dict(label_map or {})

    # -- carrier payment / compensation labels (ePay Payment Detail, Comprehensive Comp) --
    def label(self, label):
        return classify_label(label, self.cfg, self.label_map)

    def label_bucket(self, label):
        return self.label(label)[0]

    # -- residual with an activation date (ePay MI/ATU) --
    def activation(self, period, activation_date):
        return classify_activation(period, activation_date, self.cfg)

    def activation_bucket(self, period, activation_date):
        return self.activation(period, activation_date)[0]

    # -- master-agent component columns --
    def ma(self, sums, components):
        return split_ma_components(sums, components, self.cfg)

    def describe(self):
        """Plain-English, page-displayable statement of what this org's split is actually doing."""
        return {
            "resolved_from": self.cfg.get("_resolved_from", "code_default"),
            "m1_month": _int(self.cfg.get("m1_month"), 1),
            "max_leg_month": _int(self.cfg.get("max_leg_month"), 12),
            "unlabeled_bucket": self.cfg.get("unlabeled_bucket", UNSPLIT),
            "label_overrides": len(self.label_map),
            "mi_split_by_activation": bool(self.cfg.get("mi_split_by_activation", True)),
            "ma_month_field_prefix": self.cfg.get("ma_month_field_prefix"),
            "ma_max_month": _int(self.cfg.get("ma_max_month"), 6),
            "ma_m1_fields": list(self.cfg.get("ma_m1_fields") or []),
            "sources": [
                {"source": "raw_payment_detail (ePay Commission Payment Detail)",
                 "splits_on": "the month stated in the payment type — \"… - Month N\"",
                 "splittable": True},
                {"source": "raw_comp_report (Comprehensive Compensation)",
                 "splits_on": "the month stated in the compensation type — \"… - Month N\"",
                 "splittable": True},
                {"source": "raw_mi (ePay MI/ATU residual)",
                 "splits_on": "mi_activation_date vs the report month",
                 "splittable": bool(self.cfg.get("mi_split_by_activation", True))},
                {"source": "raw_ma_commission (VidaPay / master agent)",
                 "splits_on": ("the leg column — spiff_m1 = 1st month, spiff_m2…m6 = trailing. The "
                               "activation-order margin columns (rebate, device/consumer margin, "
                               "consumer financing, wallet funding, fees margin) are NOT commission "
                               "legs — VidaPay reports them as their own figures — so they stay "
                               "unsplit unless this org lists them in ma_m1_fields."),
                 "splittable": True},
            ],
        }


def for_org(client, org_id, carrier_mode="boost", carrier_id=None):
    """The org's LegClassifier. Never raises — degrades to the code default on any config error."""
    try:
        cfg = resolve_leg_config(client, org_id, carrier_id=carrier_id, carrier_mode=carrier_mode)
    except Exception:
        cfg = dict(DEFAULT_CFG)
    try:
        lm = load_label_map(client, org_id)
    except Exception:
        lm = {}
    return LegClassifier(cfg, lm)


def default_classifier():
    """The pure, DB-free classifier (code defaults, no overrides)."""
    return LegClassifier()


# ── shared row-shape helpers so every surface names the buckets identically ───────────────────────
def empty_split():
    return {M1: 0.0, TRAILING: 0.0, UNSPLIT: 0.0}


def public_keys(prefix):
    """The three public payload keys for a money column, e.g. ('comm_m1','comm_m2_12','comm_unsplit')."""
    return f"{prefix}_m1", f"{prefix}_m2_12", f"{prefix}_unsplit"


def to_public(prefix, split):
    """{'comm_m1':…, 'comm_m2_12':…, 'comm_unsplit':…} from an internal bucket dict."""
    k1, k2, ku = public_keys(prefix)
    return {k1: round(_safe_float(split.get(M1)), 2),
            k2: round(_safe_float(split.get(TRAILING)), 2),
            ku: round(_safe_float(split.get(UNSPLIT)), 2)}
