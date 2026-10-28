"""THE ONE HOME for "how much device-financing money did the carrier pay against THIS device?"

WHY IT EXISTS, stated once so nobody re-derives it. The answer was built inline inside
`commcalc/router.py::_device_line_reimb_run` — about sixty lines that bind §58's classification to
the org's own declarations, ask which components ARE the device-financing side under that org's own
routing, DECLINE a component reached on a basis §66 does not rule as evidence, enumerate the
reimbursement lag window symmetrically, read the per-line feed paged, and finally fold it per device
through §65's pure `device_reimb_recon.carrier_line_side`.

Then a second report needed the same figure: §COMMCALC-43's **chargeback amount** — the carrier money
standing against the handset on a flagged upgrade. Copying that block would have been TWO derivations
of one money question, and two paths answering one question is the defect the index rules forbid:
they drift, and the day they disagree the P&L's reimbursement line and a watchdog screen are both
quoting "the rebate on that phone" with different numbers.

So it is factored out and BOTH callers dereference it. Nothing here is new behaviour: the decisions,
their order and their reasons are the ones that were already in that block, moved whole.

WHAT THIS LAYER DECIDES, AND WHAT IT REFUSES TO
───────────────────────────────────────────────
  · **Which dollars are device money** — asked of §58 (`carrier_dollar_class`), never re-derived.
    `device_reimb_components` answers which components book to the line the device-financing
    reimbursement books to; the question is asked that way, rather than "every component routed off
    the generic commission line", because that older spelling was an INFERENCE that held only while
    exactly one component was routed anywhere (§69).
  · **Whether a classification is evidence at all** — asked of §66 (`component_is_evidence`), an
    ALLOWLIST, so an unruled basis reads as NOT evidence. The POLICY of declining it belongs to a
    per-DEVICE grain and is this layer's: a dollar placed on a category the component vocabulary has
    no row for names no phone, and counting it put $91,000.00 a month of per-store lump sums into a
    bucket a reader takes for unattributable device money. A statement that must book every dollar
    answers the same False differently, which is why §66 answers only the question.
  · **How long either side of the event the money may arrive** — asked of §27
    (`imei_rebate_report.period_window`). The window is SYMMETRIC: §27 anchors on the activation and
    runs forward, this layer anchors on a month and a payment routinely lands before the claim is
    settled, so a forward-only window reported paid devices as never-paid.
  · **It states no amount of its own.** `amount_for` returns None — never 0.00 — for a device the
    feed carries no row for, because a rebate lands months after the sale and a confident zero there
    is the fake-zero class (§19.26).
  · **It classifies nothing, books nothing and pays nothing.** No rate, tier, plan or paid/earned
    column is reachable from here.

`harness_carrier_device_money.py` is the lock: it fails the build if a second module reaches
`carrier_line_side` directly, if the §58/§66 questions are re-spelled instead of asked, or if a
caller stops dereferencing this home.
"""
from app.modules.commcalc import carrier_dollar_class as _cdc
from app.modules.commcalc import carrier_map as _cm
from app.modules.commcalc import device_reimb_recon as _drr
from app.modules.core import feed_read as _feed_read   # THE complete paged feed read (§19.48)

#: The P&L line key a component with no routing row of its own books to. Dereferenced from §58's
#: vocabulary rather than spelled again, so a rename there travels here.
DEFAULT_LINE = "carrier_comm"

#: The relation the per-line carrier feed lands in. ONE name, read by both callers.
FEED_TABLE = "raw_payment_detail"


def _s(v):
    return str(v if v is not None else "").strip()


def load_recon_config(client, org_id, house_org_id=None):
    """The org's `device_reimb_recon_config`, inheriting the house row, through §65's own folder.

    RULE TWO (mig 1063): house defaults ← house org row ← this tenant's row. A missing column or
    table is not an error — it is the house default, per the agent contract — and it SAYS SO rather
    than failing a report that can still be served.
    """
    def _blob(oid):
        if not oid:
            return None
        try:
            rows = (client.schema("commcalc").table("commission_org_config")
                    .select("device_reimb_recon_config").eq("org_id", oid).limit(1).execute().data) or []
        except Exception as e:
            print(f"WARN device_reimb_recon config unavailable (run migration 1063?): {e}")
            return None
        return (rows[0] if rows else {}).get("device_reimb_recon_config")

    house = _blob(house_org_id) if house_org_id and house_org_id != org_id else None
    return _drr.config_from_rows(_blob(org_id), house)


def device_components(cfg):
    """PURE: the components whose dollars ARE the device-financing side under this org's routing.

    §58's own question (`device_reimb_components`), asked rather than derived — see the module
    docstring for why the older "routed off the generic line" spelling was an inference.
    """
    lines = (cfg or {}).get("component_lines") or {}
    return set(_cdc.device_reimb_components(lines, DEFAULT_LINE) or ())


def device_dollar_predicate_from(*, declarations, rules, cfg, components):
    """PURE: `is_device_dollar(raw_payment_type) -> bool`, or **None** when nothing is routed.

    None is not "no dollar is device money" — it is "this org has declared no routing", which a
    caller must be able to report as unconfigured rather than as a measured zero.

    Memoized per type string, because a month of one tenant's feed is tens of thousands of rows over
    a few dozen distinct payment types.
    """
    comps = {str(c or "").upper() for c in (components or ())}
    if not comps:
        return None
    cache = {}

    def is_device_dollar(raw_type):
        key = _s(raw_type)
        hit = cache.get(key)
        if hit is None:
            c = _cdc.classify(declarations, rules or (), raw_type, cfg) or {}
            hit = cache[key] = (str(c.get("component") or "").upper() in comps
                                and _cdc.component_is_evidence(c.get("basis")))
        return hit

    return is_device_dollar


def classification_inputs(client, org_id):
    """(declarations, rules, cfg) — §58 bound to this org. Each read through its own one home."""
    cfg = _cdc.load_config(client, org_id)
    try:
        rules = _cm.load_rules(client, org_id)
    except Exception as e:
        print(f"WARN carrier keyword ladder unavailable, declarations only: {e}")
        rules = []
    return _cdc.load_declarations(client, org_id), rules, cfg


def device_dollar_predicate(client, org_id):
    """(is_device_dollar | None, components) for this org, read through §58 and §66."""
    decl, rules, cdc_cfg = classification_inputs(client, org_id)
    comps = device_components(cdc_cfg)
    return device_dollar_predicate_from(declarations=decl, rules=rules, cfg=cdc_cfg,
                                        components=comps), comps


def lag_periods(cfg, period):
    """The months the reimbursement for an event in `period` may have landed in — SYMMETRIC.

    The lag LENGTH and the month enumeration are §27's (`imei_rebate_report.period_window`); this
    layer states no lag rule of its own. No period asked for means no window (None), never a guess.
    """
    p = _s(period)
    if not p:
        return None
    from app.modules.commcalc.imei_rebate_report import period_window as _window
    lag = int((cfg or {}).get("lag_months") or 0)
    anchor = _drr.month_key(p)
    if not (anchor and lag):
        return _window(p, 0) if p else None
    y, m = int(anchor[:4]), int(anchor[5:7]) - lag
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return _window(f"{y:04d}-{m:02d}", lag * 2)


def feed_periods(cfg, period, pvariants):
    """Every SPELLING of every month in the lag window, so the read matches a feed stored as either
    'September 2026' or '2026-09'. `pvariants` is the platform's one period-spelling home, injected
    so no second spelling rule travels with this module."""
    window = lag_periods(cfg, period)
    if window is None:
        return None
    return sorted({v for p in window for v in (pvariants(p) or [p])})


def per_device(client, org_id, period, *, cfg=None, resolve_store=None, pvariants=None,
               is_device_dollar=None, components=None):
    """The carrier's per-line feed folded PER DEVICE, over the reimbursement lag window.

    Returns `(index, meta)` where `index` is §65's own
    `{"devices": {device_id: {...}}, "unidentified": {...}}` and `meta` states what the figure rests
    on: the components counted, whether any routing was declared at all, the lag and the window.
    `configured: False` means this org has declared no device-financing routing — with that, not one
    dollar is called device money and not one is called missing.
    """
    cfg = cfg or load_recon_config(client, org_id)
    cols = cfg["columns"]
    if is_device_dollar is None and components is None:
        is_device_dollar, components = device_dollar_predicate(client, org_id)
    configured = bool(components)
    window = lag_periods(cfg, period)
    periods = feed_periods(cfg, period, pvariants) if pvariants else None

    def _q():
        q = (client.schema("commcalc").table(FEED_TABLE)
             .select(",".join(sorted({cols["line_store"], cols["line_amount"],
                                      cols["line_category"], cols["line_device_id"],
                                      cols["line_date"], cols["line_rep"]})))
             .eq("org_id", org_id))
        return q.in_("period", periods) if periods else q

    rows = _feed_read.read_all(_q)          # PAGED through the one home (§19.48) — no row ceiling
    index = _drr.carrier_line_side(rows, cfg, is_device_dollar if configured else None,
                                   resolve_store)
    return index, {
        "configured": configured,
        "components": sorted(components or ()),
        "lag_months": cfg.get("lag_months"),
        "lag_window": list(window or ([period] if period else [])),
        "feed_table": FEED_TABLE,
        "feed_rows": len(rows),
    }


def amount_for(index, device_id):
    """The device-financing dollars standing against one device, or **None** when the feed carries
    no row for it.

    None, never 0.00. A rebate lands months after the sale, so "nothing yet" is UNMEASURED, and a
    screen that renders it as $0.00 has stated a measurement nobody made (§19.26).
    """
    dev = _s(device_id)
    if not dev or not isinstance(index, dict):
        return None
    cell = (index.get("devices") or {}).get(dev)
    if not cell:
        return None
    return round(float(cell.get("total") or 0.0), 2)


def lines_for(index, device_id):
    """How many feed lines the device's amount is Σ over — 0 when there are none. The count a
    drill-down needs to say what the figure rests on."""
    dev = _s(device_id)
    if not dev or not isinstance(index, dict):
        return 0
    return int(((index.get("devices") or {}).get(dev) or {}).get("lines") or 0)
