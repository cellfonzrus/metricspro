"""Device Cost Reconciliation — the OPTION-A MEASUREMENT PASS. PURE math (no DB, no FastAPI, no HTTP).

WHAT THIS ANSWERS. "What did each device cost us, according to WHICH source, and how much of that is the
SAME device counted twice?" MetricsPro already knows a handset's cost in four different shapes with no
single answer and no agreement on WHEN the cost hits the books (docs/designs/device-cost-ledger.md §1).
This module lays all four side by side, tags each row with its source + arrangement + timing date, flags
the suspected IMEI overlaps, counts the rows that CANNOT reach an IMEI, and previews the month × store
delta between today's device-COGS route and the owner's §9 policy.

READ-ONLY, DISPLAY-ONLY, NOT MONEY-TOUCHING. Nothing here writes anything (in particular never
`commcalc.asset_ledger`, which belongs to mod-asset), no rate/tier/plan/payout is read, no recompute is
reachable, and no P&L / GP / calculator code path is modified. The policy column is a PREVIEW: the
Option-C flip that would point the P&L at it is HELD pending the owner's review of exactly this table.

THE FOUR SOURCES (design note §2 — every table/column named here is real, with its migration):
  ① commcalc.raw_ma_fulfillment      (mig 083)  one row per ORDER LINE   PURCHASE price      date_ordered
  ② commcalc.asset_ledger            (mod-asset) one row per ESN/device  CONSIGNMENT billing billing_friday
  ③ commcalc.raw_sales / daily_sales_feed        one row per SALE LINE   derived POS cost    trans_date
  ④ commcalc.inventory_aging_device  (mig 216)  one row per stocked dev INVENTORY unit cost  as_of_date

ARRANGEMENT COMES FROM CONFIG, NEVER FROM A GUESS (RULE TWO). A source row's arrangement is resolved
through the org's EXISTING config chain, in this order, and the chain that answered is reported on the
row so a wrong label is traceable instead of invisible:
    ① `raw_ma_fulfillment.source_id` → `commcalc.data_source.distributor_id` → `commcalc.distributors`
       (then `carrier_id` → distributors as the second key)
    ② `commcalc.payable_source_map` rows whose `source_table = 'asset_ledger'` → their `distributor_id`
       (then, only if the map is silent, the org's distributors carrying `has_asset_lending`)
    ③④ POS-derived: there is NO distributor behind a POS cost, and saying "terms" there would be an
       invention — they are labelled POS-derived and say so.
No distributor, carrier, tenant or arrangement is named in code; an unmapped row lands in the EXPLICIT,
SELECTABLE "(distributor not mapped)" bucket and is counted.

THE OWNER'S POLICY (§9, 2026-07-30 — this is the policy of record; a deviation needs a new owner yes):
  Q1 INVOICE-FIRST, SALE-TIME FALLBACK. A device whose cost is evidenced by an invoice in the system is
     recognized at the INVOICE amount; only a device with NO invoice falls back to the POS-derived cost
     (ext_price − GP) at SALE time. Dedup is by IMEI: a device recognized off an invoice is never
     recognized again at sale. → `recognize()`; the invoice sources are ① (the marketplace order the
     distributor invoiced) and ② (what VIP billed), precedence CONFIGURABLE (see PRECEDENCE below).
  Q2 CONSIGNMENT: LIABILITY UNTIL SOLD, VIP BILLING = COGS. `owed_to_vip` stays a liability (the
     verified ~$121k PayGo treatment is NEVER netted away — it is reported as its own figure), and the
     VIP-BILLED amount is the consignment device's COGS, on the verified Friday trigger.
  Q3 PERIODIC INVENTORY. Device P&L COGS ≈ recognized costs − Δ(inventory asset). BOTH legs are shown.
     ⚠️ MEASURED FINDING, not an assumption: neither inventory source keeps HISTORY —
     `inventory_aging_device` is UNIQUE on (org_id, imei) and `inventory_value` is PK (org_id, store), so
     each holds exactly ONE current snapshot. Δ(inventory) between two past month-ends is therefore NOT
     derivable from the data that exists today. This module reports the closing valuation it CAN see and
     returns `delta_inventory = None` with the reason, rather than printing a 0 that looks like "no
     change". Fixing that needs a period-stamped snapshot (an Option-B item), not a formula.
  Q4 GLOBAL DEFAULT + PER-TENANT OVERRIDE, MAPPING-DRIVEN. The measurement pass adds NO migration, so
     the recognition knobs (`precedence`, `ma_recognition_date`, `price_basis`) are REPORT PARAMETERS
     with the §9 defaults, stated out loud on the page and in every export subtitle — not constants
     buried in code. Option B moves them into a config table + admin UI; the vocabulary is already
     shared through `ma_upload.FIELD_LABELS` / `cost_field_catalog()`.

WHY THE HONEST COUNTS ARE THE POINT. The owner's "since it is IMEI-based it can never be duplicate
entries" premise holds ONLY for rows that can reach an IMEI. They cannot all:
  • ① has NO imei column. Its only link is `raw_ma_commission.activation_order` →
    `raw_ma_fulfillment.order_number` (the verified mig-083 join Device History uses), i.e. only for
    devices that were ACTIVATED. An ordered-but-unsold handset has no IMEI link at all.
  • VIP invoice evidence joins by `vip_invoice_devices.SERIAL`, not imei.
  • a ② ledger row with a blank ESN, a ③ device sale line with no `serial_1`, and a ④ snapshot row with
    neither imei nor serial are all un-keyable.
Every one of those is COUNTED and COSTED per source (`unlinkable_*`), and a recognized row that could
not be IMEI-deduped is reported as `at_risk` — dollars the dedup premise does not cover. Never zero by
assumption.

TENANT- AND CARRIER-AGNOSTIC. Nothing branches on a tenant, carrier, distributor or store name — an org
either has rows in a source or it does not, and an empty source is stated as empty (RULE ONE's "empty ≠
broken"). Markets come from the org's own /store-match chain, injected by the router.
"""

from app.modules.commcalc import ma_handset_cogs as _mhc      # reused math/period helpers (design §7)

# Re-exported so the router and the harness share ONE spelling-resolution path for the
# 'June 2026' vs '2026-06' duality (the recurring period-mismatch bug class).
to_num = _mhc.to_num
month_of = _mhc.month_of
month_label = _mhc.month_label
period_ym = _mhc.period_ym
canon_period = _mhc.canon_period
parse_date = _mhc.parse_date
_s = _mhc._s
_fold = _mhc._fold
_sel = _mhc._sel


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FOUR SOURCES — one registry, so the page, the tiles, the exports and the harness cannot disagree
# about what a source IS, what its dollar MEANS, or which date it is timed on.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MA = "ma_fulfillment"
AL = "asset_lending"
POS = "pos_sale"
INV = "inventory_snapshot"
SOURCES = (MA, AL, POS, INV)

SOURCE_META = {
    MA: {
        "n": "①", "label": "Marketplace order (purchase)",
        "table": "commcalc.raw_ma_fulfillment",
        "grain": "one row per order LINE",
        "amount_kind": "Order-line price (qty × unit price)",
        "timing": "ordered", "timing_label": "Date ordered",
        "invoice": True,
        "means": "what the marketplace/master-agent invoiced us for handsets we BOUGHT (we own them on "
                 "order). Ordered ≠ received ≠ sold.",
        "link": "IMEI only via raw_ma_commission.activation_order → raw_ma_fulfillment.order_number "
                "(activated devices only) — an ordered-but-unsold line has no IMEI at all.",
    },
    AL: {
        "n": "②", "label": "Consignment / asset lending (VIP billed)",
        "table": "commcalc.asset_ledger",
        "grain": "one row per ESN / device",
        "amount_kind": "Owed to VIP (the billed amount)",
        "timing": "vip_billed", "timing_label": "Billing Friday",
        "invoice": True,
        "means": "consignment: the distributor owns the device until it is billed/sold. Per §9 Q2 the "
                 "VIP-BILLED amount is this device's COGS, and owed_to_vip remains a liability — it is "
                 "reported separately here and never netted away.",
        "link": "IMEI-keyed (esn_imei). READ-ONLY: this table belongs to mod-asset and is never written.",
    },
    POS: {
        "n": "③", "label": "POS sale line (ext price − GP)",
        "table": "commcalc.raw_sales ∪ commcalc.daily_sales_feed",
        "grain": "one row per SALE line",
        "amount_kind": "Derived POS cost (ext_price − GP)",
        "timing": "sold", "timing_label": "Sale date",
        "invoice": False,
        "means": "there is no cost column on the POS export; cost is DERIVED as ext_price − GP. Per §9 "
                 "Q1 this is the FALLBACK, used only for a device with no invoice in the system.",
        "link": "IMEI-keyed via serial_1. A device line with a blank serial cannot be deduped.",
    },
    INV: {
        "n": "④", "label": "Inventory snapshot (unit cost)",
        "table": "commcalc.inventory_aging_device",
        "grain": "one row per device in stock",
        "amount_kind": "POS on-hand unit cost",
        "timing": "snapshot", "timing_label": "Snapshot as-of",
        "invoice": False,
        "means": "the VALUATION of unsold stock, not a cost recognition. Per §9 Q3 it is the "
                 "balance-sheet leg (periodic inventory), so it is NEVER a recognition source here.",
        "link": "IMEI-keyed (imei, else serial).",
    },
}

# Explicit, SELECTABLE buckets. A filter must never make rows vanish into a hole nobody can see.
NO_MARKET = _mhc.NO_MARKET                     # "(no market)" — shared spelling with Handset COGS
NO_STORE = "(no store)"
NO_PRODUCT = "(no product)"
NO_DEVICE_KEY = "(no IMEI / serial)"
NO_MONTH = "(no date)"
UNMAPPED_ARRANGEMENT = "(distributor not mapped)"
POS_ARRANGEMENT = "(POS-derived — no distributor)"
SNAPSHOT_ARRANGEMENT = "(POS snapshot — no distributor)"

ARRANGEMENT_LABEL = {"terms": "Terms (net credit)", "consignment": "Consignment",
                     "cod": "COD (paid up front)"}

# ── recognition policy (§9 Q1) — REPORT PARAMETERS with the owner's defaults, never buried constants ──
INVOICE_SOURCES = (MA, AL)                     # sources that constitute "an invoice in the system"
DEFAULT_PRECEDENCE = (MA, AL, POS)             # invoice-first, sale-time fallback
NEVER_RECOGNIZED = (INV,)                      # ④ is the C3 balance-sheet leg, not a recognition
MA_DATE_MODES = ("ordered", "filled", "shipped")
DEFAULT_MA_DATE = "ordered"

# A device key shorter than this is refused outright: a '0', a '1', an 'NA' or a padded blank would
# otherwise join two completely unrelated devices into one fake overlap.
MIN_DEVICE_KEY_LEN = 6
JUNK_DEVICE_KEYS = {"0", "00", "000", "N/A", "NA", "NONE", "NULL", "-", "--", "UNKNOWN", "TBD"}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# DEVICE KEY — the one cross-source join key
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def device_key(v):
    """Canonical cross-source device key, or None when the token cannot safely identify a device.

    Normalization is the SPELLING ALREADY ESTABLISHED for the asset↔sales device join in migration 009
    (`upper(regexp_replace(coalesce(serial_1,''), '\\.0$', ''))`): trim → drop a trailing '.0' (the
    spreadsheet float artefact every one of these importers meets) → upper-case. Alphanumeric serial
    characters are PRESERVED (④ and ② can carry a real serial, not only a numeric IMEI), so this is
    deliberately NOT a digits-only normalization.

    Refused (→ None): blank / 'nan' / 'none' / 'null', a known junk placeholder, and anything shorter
    than MIN_DEVICE_KEY_LEN. A refused token makes its row UN-LINKABLE, which is counted and shown —
    it never silently joins two devices.
    """
    s = ("" if v is None else str(v)).strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    if s.endswith(".0"):
        s = s[:-2]
    s = s.strip().upper()
    if not s or s in JUNK_DEVICE_KEYS or len(s) < MIN_DEVICE_KEY_LEN:
        return None
    return s


def norm_order(v):
    """Comparable form of an order key — the SAME normalization Device History's verified mig-083 join
    uses (`device_history.norm_order`), so the ① → IMEI link here and there can never diverge."""
    s = ("" if v is None else str(v)).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lower()


def order_imei_index(commission_rows):
    """{normalized activation_order: sorted[device_key]} from `raw_ma_commission` rows — the ONLY bridge
    from a marketplace ORDER to an IMEI (design §3). Rows with no order, or no usable device key, simply
    do not contribute: the resulting absence is what makes an ① line un-linkable, and that is counted.

    An order mapping to MORE THAN ONE IMEI is kept as a list on purpose (a shared/multi-line order is
    real — Device History guards the same case) so the overlap report can say "this order covers N
    devices" instead of silently picking one.
    """
    out = {}
    for r in commission_rows or []:
        o = norm_order(r.get("activation_order"))
        if not o:
            continue
        k = device_key(r.get("imei"))
        if not k:
            continue
        out.setdefault(o, set()).add(k)
    return {o: sorted(v) for o, v in out.items()}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ARRANGEMENT — resolved from the org's EXISTING config tables (RULE TWO), with the chain reported
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _dist_view(d, chain):
    """A distributor config row → the arrangement fields every event carries."""
    arr = _fold(d.get("arrangement")) or None
    name = _s(d.get("name"))
    label = ARRANGEMENT_LABEL.get(arr, (arr or "").title() or UNMAPPED_ARRANGEMENT)
    return {
        "distributor": name, "distributor_id": d.get("id"),
        "arrangement": arr,
        "arrangement_label": (f"{label} — {name}" if name else label),
        "arrangement_source": chain,
        "has_asset_lending": bool(d.get("has_asset_lending")),
        "terms_days": d.get("terms_days"), "billing_cycle": _s(d.get("billing_cycle")),
    }


def _unmapped(chain, label=UNMAPPED_ARRANGEMENT):
    return {"distributor": None, "distributor_id": None, "arrangement": None,
            "arrangement_label": label, "arrangement_source": chain,
            "has_asset_lending": False, "terms_days": None, "billing_cycle": None}


class ArrangementIndex:
    """Resolves "what ARRANGEMENT is this cost row on?" from config only.

    Inputs are the org-scoped rows the router read:
      `distributors`  — commcalc.distributors (mig 058: arrangement ∈ terms|consignment|cod,
                        has_asset_lending, carrier_id)
      `data_sources`  — commcalc.data_source  (mig 083: id → distributor_id, the precise link for ①)
      `source_maps`   — commcalc.payable_source_map (mig 095: per-carrier source_table → distributor_id,
                        already editable at /commcalc/payables source-maps — so ②'s distributor is
                        CONFIGURED, not inferred from a name)

    Nothing is guessed: when no chain answers, the row gets the SELECTABLE "(distributor not mapped)"
    bucket, `arrangement=None`, and the reason it could not be resolved. `notes()` reports the config
    gaps (no distributors configured / several asset-lending distributors / no map row) so the page can
    tell the operator exactly which config row to add.
    """

    def __init__(self, distributors=(), data_sources=(), source_maps=()):
        self.rows = [d for d in (distributors or []) if d]
        self.by_id = {}
        self.by_carrier = {}
        for d in self.rows:
            did = _s(d.get("id"))
            if did:
                self.by_id[did] = d
            cid = _s(d.get("carrier_id"))
            # first ACTIVE distributor for a carrier wins; an inactive one is only a fallback
            if cid:
                cur = self.by_carrier.get(cid)
                if cur is None or (not cur.get("is_active") and d.get("is_active")):
                    self.by_carrier[cid] = d
        self.ds_to_dist = {}
        for s in (data_sources or []):
            sid, did = _s(s.get("id")), _s(s.get("distributor_id"))
            if sid and did:
                self.ds_to_dist[sid] = did
        self.map_by_table = {}
        for m in (source_maps or []):
            t = _fold(m.get("source_table"))
            if t and _s(m.get("distributor_id")):
                self.map_by_table.setdefault(t, []).append(m)
        self.asset_lending = [d for d in self.rows if d.get("has_asset_lending")]

    # ── ① a marketplace order line ────────────────────────────────────────────────────────────────
    def for_ma(self, row):
        sid = _s((row or {}).get("source_id"))
        if sid and sid in self.ds_to_dist:
            d = self.by_id.get(self.ds_to_dist[sid])
            if d:
                return _dist_view(d, "data_source → distributor")
        cid = _s((row or {}).get("carrier_id"))
        if cid and cid in self.by_carrier:
            return _dist_view(self.by_carrier[cid], "carrier → distributor")
        return _unmapped("no data_source/carrier link to a distributor")

    # ── ② the asset-lending ledger ────────────────────────────────────────────────────────────────
    def for_asset(self, row=None):
        cid = _s((row or {}).get("carrier_id"))
        for m in self.map_by_table.get("asset_ledger", []):
            if cid and _s(m.get("carrier_id")) and _s(m.get("carrier_id")) != cid:
                continue
            d = self.by_id.get(_s(m.get("distributor_id")))
            if d:
                return _dist_view(d, "payable_source_map → distributor")
        if len(self.asset_lending) == 1:
            return _dist_view(self.asset_lending[0], "distributors.has_asset_lending (only one)")
        if len(self.asset_lending) > 1:
            v = _unmapped(f"{len(self.asset_lending)} distributors carry asset lending — ambiguous",
                          f"Consignment — {UNMAPPED_ARRANGEMENT}")
            v["arrangement"] = "consignment"      # the LEDGER is consignment by construction (mig 058)
            return v
        return _unmapped("no distributor configured with asset lending")

    # ── ③④ POS-derived: there is no distributor behind a POS number ────────────────────────────────
    def for_pos(self):
        return _unmapped("POS-derived cost — no distributor involved", POS_ARRANGEMENT)

    def for_inventory(self):
        return _unmapped("POS snapshot — no distributor involved", SNAPSHOT_ARRANGEMENT)

    def notes(self):
        out = []
        if not self.rows:
            out.append("No distributors are configured for this org, so no cost row can be tagged with "
                       "an arrangement — add them at Distributors (commcalc.distributors) and every "
                       "row's arrangement fills in with no other change.")
        elif len(self.asset_lending) > 1 and not self.map_by_table.get("asset_ledger"):
            out.append(f"{len(self.asset_lending)} distributors are marked as carrying asset lending, so "
                       "the ledger's distributor is ambiguous. The rows are still shown as CONSIGNMENT "
                       "(that is what the asset-lending ledger is) but without a distributor name — set "
                       "`source_table = 'asset_ledger'` on the right carrier's payable source map to "
                       "name it.")
        elif not self.asset_lending and not self.map_by_table.get("asset_ledger"):
            out.append("No distributor is configured with asset lending and no payable source map points "
                       "at `asset_ledger`, so consignment rows carry no distributor name.")
        return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# STORE / MARKET — through the org's OWN /store-match chain, injected by the router
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def resolve_place(store_of, key_of, *keys):
    """(store_label, store_key, market, matched_on) for the first store key that resolves.

    `store_of(raw) -> (label, market)` is the org's existing /store-match chain (`_ir_store_resolver`);
    `key_of(raw) -> str` is the canonicalizer that decides which physical store two spellings collapse
    to — the SAME one used for today's device-COGS leg, so the delta table compares like with like.
    Both are optional: with neither, the raw string is its own label and key (upper-cased) and the market
    is None → the SELECTABLE "(no market)" bucket. A resolver that raises never loses the row.
    """
    raw = next((_s(k) for k in keys if _s(k)), None)
    label, market, matched = None, None, None
    if raw and store_of:
        for k in keys:
            kk = _s(k)
            if not kk:
                continue
            try:
                lbl, mkt = store_of(kk)
            except Exception:                       # a resolver hiccup must never lose the row
                lbl, mkt = None, None
            if lbl or mkt:
                label, market, matched = (lbl or label), (mkt or market), kk
                if market:
                    break
    label = label or raw
    key = None
    if key_of and raw:
        try:
            key = key_of(label or raw)
        except Exception:
            key = None
    if not key:
        key = (label or raw or "").strip().upper() or None
    return label, key, market, matched


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# EVENT BUILDERS — one per source. Every event has the SAME shape, so tiles/groups/exports are one code
# path and a new source is a builder, not a special case.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _event(source, **kw):
    m = SOURCE_META[source]
    ev = {
        "source": source, "source_n": m["n"], "source_label": m["label"],
        "source_table": m["table"], "amount_kind": m["amount_kind"],
        "timing": m["timing"], "timing_label": m["timing_label"],
        "is_invoice": m["invoice"],
        "device_key": None, "device_key_raw": None,
        "amount": None, "event_date": None, "month": None, "month_label": None,
        "store": None, "store_label": None, "store_key": None, "market": None,
        "product": None, "ref": None,
        "linkable": False, "unlink_reason": None,
        # filled in by recognize()
        "recognized": False, "recognition_reason": None, "suppressed_by": None,
        "dedup_covered": False,
    }
    ev.update(kw)
    ev["month"] = month_of(ev["event_date"]) if ev.get("event_date") else None
    ev["month_label"] = month_label(ev["month"]) if ev.get("month") else None
    ev["store_label"] = ev.get("store_label") or ev.get("store") or NO_STORE
    ev["device_key_label"] = ev.get("device_key") or NO_DEVICE_KEY
    ev["product_label"] = ev.get("product") or NO_PRODUCT
    ev["arrangement_label"] = ev.get("arrangement_label") or UNMAPPED_ARRANGEMENT
    return ev


def ma_events(fulfillment_rows, order_imeis=None, arrangements=None, store_of=None, key_of=None,
              price_basis="unit", ma_date=DEFAULT_MA_DATE, include_cancelled=False):
    """① marketplace ORDER LINES → cost events.

    The extension is `ma_handset_cogs.line_from_row` verbatim (design §7 — the ① adapter is REUSED, not
    re-implemented), so this report and the shipped Handset COGS report can never disagree about qty ×
    price, order state, or ship-to resolution.

    CANCELLED lines are NOT a cost. They are excluded from the events by default (`include_cancelled`
    stays a parameter so the operator can see them) and counted in the returned notes, mirroring the
    Handset COGS report's committed-COGS rule rather than inventing a second one.

    The IMEI link is the verified mig-083 join and NOTHING ELSE: `order_imeis` is
    `order_imei_index(raw_ma_commission rows)`. A line whose order is absent from that index is emitted
    with `linkable = False` and an explicit reason — never dropped, never assumed to be unique.

    `ma_date` picks WHICH date times the recognition (ordered | filled | shipped). Default 'ordered' =
    the design's stated timing; the alternative exists because ordered ≠ received, and an operator who
    books on receipt must be able to see that view without a code change.
    """
    order_imeis = order_imeis or {}
    mode = ma_date if ma_date in MA_DATE_MODES else DEFAULT_MA_DATE
    lines = _mhc.build_rows(fulfillment_rows, store_of=None, price_basis=price_basis)
    # id → source row, so the arrangement lookup is O(1) per line. `build_rows` re-sorts, so the source
    # row cannot be found by position; a row with no id degrades to the unmapped bucket (and says so)
    # rather than costing a full scan per line on a 200k-row feed.
    by_id = {r.get("id"): r for r in (fulfillment_rows or []) if r.get("id") is not None}
    out, cancelled, cancelled_amt = [], 0, 0.0
    for ln in lines:
        if ln.get("state") == "cancelled":
            cancelled += 1
            cancelled_amt += float(ln.get("ext_cost") or 0)
            if not include_cancelled:
                continue
        raw = by_id.get(ln.get("id")) or {}
        arr = (arrangements.for_ma(raw) if arrangements else _unmapped("no arrangement config supplied"))
        label, skey, market, _m = resolve_place(store_of, key_of, ln.get("business_address"),
                                               ln.get("business_name"))
        d = {"ordered": ln.get("date_ordered"), "filled": ln.get("date_filled"),
             "shipped": ln.get("date_shipped")}.get(mode) or ln.get("date_ordered")
        keys = order_imeis.get(norm_order(ln.get("order_number")), [])
        out.append(_event(
            MA,
            device_key=(keys[0] if len(keys) == 1 else None),
            device_key_raw=(",".join(keys) if keys else None),
            linked_keys=keys,
            amount=ln.get("ext_cost"),
            event_date=d,
            timing_label={"ordered": "Date ordered", "filled": "Date filled",
                          "shipped": "Date shipped"}[mode],
            store=ln.get("ship_to"), store_label=(label or ln.get("ship_to_label")),
            store_key=skey, market=market,
            product=ln.get("product"), ref=ln.get("order_number"),
            qty=ln.get("qty"), unit_price=ln.get("unit_price"),
            state=ln.get("state"), state_label=ln.get("state_label"),
            status=ln.get("order_status"),
            linkable=bool(keys),
            unlink_reason=(None if keys else
                           ("no activation (raw_ma_commission.activation_order) links this order to an "
                            "IMEI — an ordered-but-unsold handset has no IMEI link at all")),
            ambiguous_link=(len(keys) > 1),
            **{k: v for k, v in arr.items()}))
    notes = []
    if cancelled and not include_cancelled:
        notes.append(f"{cancelled} cancelled marketplace order line(s) (≈ ${cancelled_amt:,.2f}) are "
                     "excluded — a cancelled order is not a cost. Turn on “include cancelled” "
                     "to see them.")
    return out, notes


def asset_events(asset_rows, arrangements=None, store_of=None, key_of=None,
                 owed_field="owed_to_vip", date_field="billing_friday"):
    """② asset_ledger / consignment → cost events. READ-ONLY over mod-asset's table.

    The dollar is `owed_to_vip` — the VIP-BILLED amount, which §9 Q2 makes the consignment device's COGS
    — and the timing is `billing_friday`, the VERIFIED Friday billing trigger. Neither is re-derived
    here: the trigger stays exactly as the asset module computed it. When `billing_friday` is blank the
    event keeps the fallback date the ledger does have (trigger_date → due_date → acquired_date) and
    SAYS which one it used, so a device is never silently attributed to the wrong month.

    `owed_field` / `date_field` are parameters because `commcalc.payable_source_map` already CONFIGURES
    them per carrier (mig 095: `owed_field`, `billing_friday_field`) — the router passes the configured
    names so a tenant whose ledger spells them differently needs a config row, not code.

    Each event also carries the facts C2 and the ②↔④ overlap need: `on_inventory` (unsold),
    `reimbursement`, `selling_price`. `owed_to_vip` is reported as a LIABILITY figure of its own and is
    never netted away — the 2026-06-25 incident is not repeated.

    TWO "UNSOLD" DEFINITIONS EXIST IN THE CODEBASE and this report shows both rather than silently
    picking one: the ASSET module (the table's owner, /asset/oninventory, `asset_charges_summary`) defines
    on-inventory as `date_sold IS NULL AND category ILIKE '%On Inventory%'`, while the finance P&L's
    balance-sheet leg uses `status == 'On Inventory'`. `on_inventory` follows the asset module's
    definition (its table, its rule); `on_inventory_status` carries the finance one, and
    `oninv_definitions_agree` flags every row where the two disagree so the divergence is measurable
    instead of invisible.
    """
    out = []
    for r in asset_rows or []:
        arr = (arrangements.for_asset(r) if arrangements else _unmapped("no arrangement config supplied"))
        label, skey, market, _m = resolve_place(store_of, key_of, r.get("store"))
        market = market or _s(r.get("market"))
        billed = bool(_s(r.get(date_field)))
        d, d_src = _s(r.get(date_field)), date_field
        if not d:
            for alt in ("trigger_date", "due_date", "acquired_date"):
                if _s(r.get(alt)):
                    d, d_src = _s(r.get(alt)), alt
                    break
        k = device_key(r.get("esn_imei"))
        status = _s(r.get("status")) or ""
        cat = _s(r.get("category")) or ""
        sold = parse_date(r.get("date_sold"))
        oninv_asset = ("on inventory" in cat.strip().lower()) and not sold
        oninv_status = status.strip().lower() == "on inventory"
        out.append(_event(
            AL,
            device_key=k, device_key_raw=_s(r.get("esn_imei")),
            amount=to_num(r.get(owed_field)),
            event_date=parse_date(d),
            timing_label=("Billing Friday" if d_src == date_field
                          else f"{d_src.replace('_', ' ').title()} (no {date_field})"),
            date_source=d_src,
            store=_s(r.get("store")), store_label=label, store_key=skey, market=market,
            product=_s(r.get("device_model")), ref=_s(r.get("esn_imei")),
            category=cat, status=status,
            billed=billed,
            on_inventory=oninv_asset,
            on_inventory_status=oninv_status,
            oninv_definitions_agree=(oninv_asset == oninv_status),
            reimbursement=to_num(r.get("reimbursement")),
            selling_price=to_num(r.get("selling_price")),
            date_sold=sold,
            linkable=bool(k),
            unlink_reason=(None if k else "no usable ESN/IMEI on the ledger row"),
            **{kk: vv for kk, vv in arr.items()}))
    return out


def pos_events(sale_rows, is_device=None, arrangements=None, store_of=None, key_of=None):
    """③ POS sale lines → cost events, DEVICE lines only.

    The cost is `device_history.pos_cost_from_sale`'s definition — `ext_price − GP` — because that is the
    only cost signal the 78-column export carries. `is_device(department)` is the tenant's OWN
    device/accessory classifier (the same config the P&L and the commission side use, injected by the
    router): accessory lines are not device cost and are excluded here, not re-classified. With NO
    classifier supplied every line is taken, which is why the router always supplies one.

    Voided lines are skipped — the same guard the P&L applies — so a voided sale can never book a cost.

    NOTE the deliberate difference from today's P&L route, which the delta table exists to expose:
    today's `device_cost` books `ext − gp` even when that is ≤ 0, while a ≤ 0 derivation is not a cost
    (`pos_cost_from_sale` returns None). Those rows are emitted with `amount = None` and counted as
    priceless rather than being summed as $0 or as a negative cost.
    """
    out = []
    for r in sale_rows or []:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        dept = _s(r.get("department"))
        if is_device is not None:
            try:
                if not is_device(dept or ""):
                    continue
            except Exception:
                continue
        ext, gp = to_num(r.get("ext_price")), to_num(r.get("gp"))
        cost = None
        if ext is not None and gp is not None:
            c = round(ext - gp, 2)
            cost = c if c > 0 else None
        label, skey, market, _m = resolve_place(store_of, key_of, r.get("store"))
        k = device_key(r.get("serial_1"))
        arr = (arrangements.for_pos() if arrangements else _unmapped("POS-derived", POS_ARRANGEMENT))
        out.append(_event(
            POS,
            device_key=k, device_key_raw=_s(r.get("serial_1")),
            amount=cost,
            event_date=parse_date(r.get("trans_date")),
            store=_s(r.get("store")), store_label=label, store_key=skey, market=market,
            product=_s(r.get("product_desc")), ref=_s(r.get("trans_id")),
            rep=_s(r.get("salesperson")),
            department=dept, category=_s(r.get("category")),
            ext_price=ext, gp=gp,
            sale_source=_s(r.get("_src")) or "raw_sales",
            linkable=bool(k),
            unlink_reason=(None if k else
                           "no serial_1 on the sale line — this device cost cannot be IMEI-deduped"),
            priceless_reason=(None if cost is not None else
                              ("ext_price − GP is not a positive number (blank column, a $0 line, or "
                               "GP ≥ ext price) — counted, never summed as $0")),
            **{kk: vv for kk, vv in arr.items()}))
    return out


def inventory_events(inv_rows, arrangements=None, store_of=None, key_of=None):
    """④ inventory snapshot → VALUATION events (never a recognition — §9 Q3).

    `unit_cost` is the POS on-hand cost the b2bsoft Inventory Aging report carries per device. A blank
    or ≤ 0 cost is no-signal (`device_history.inv_device_cost`'s rule) and is counted, not summed as $0.
    The event date is the snapshot's `as_of_date` (falling back to `received_date`) — this is a
    point-in-time valuation, so it is NOT a monthly cost and is excluded from every recognition total.
    """
    out = []
    for r in inv_rows or []:
        amt = to_num(r.get("unit_cost"))
        if amt is not None and amt <= 0:
            amt = None
        label, skey, market, _m = resolve_place(store_of, key_of, r.get("store"))
        k = device_key(r.get("imei")) or device_key(r.get("serial"))
        arr = (arrangements.for_inventory() if arrangements
               else _unmapped("POS snapshot", SNAPSHOT_ARRANGEMENT))
        out.append(_event(
            INV,
            device_key=k, device_key_raw=(_s(r.get("imei")) or _s(r.get("serial"))),
            amount=amt,
            event_date=parse_date(r.get("as_of_date")) or parse_date(r.get("received_date")),
            store=_s(r.get("store")), store_label=label, store_key=skey, market=market,
            product=_s(r.get("item")), ref=(_s(r.get("imei")) or _s(r.get("serial"))),
            sku=_s(r.get("sku")),
            received_date=parse_date(r.get("received_date")),
            as_of_date=parse_date(r.get("as_of_date")),
            days_in_stock=r.get("days_in_stock"),
            linkable=bool(k),
            unlink_reason=(None if k else "no imei or serial on the snapshot row"),
            priceless_reason=(None if amt is not None else
                              "no positive unit_cost on the snapshot row — counted, never summed as $0"),
            **{kk: vv for kk, vv in arr.items()}))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# OVERLAPS — the double-count map of design §3, MEASURED
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The four pairs the note names, each with the sentence that says what a naive sum would do wrong.
OVERLAP_PAIRS = (
    (MA, POS, "ma_pos", "① purchase + ③ POS cost of the same device — the handset is expensed twice, "
                        "once on order and once on sale"),
    (AL, POS, "al_pos", "② owed_to_vip + ③ POS cost — consignment billing and the sale-time cost are "
                        "the same device on two arrangements"),
    (MA, INV, "ma_inv", "① purchase + ④ unit cost — the same unsold unit counted as both a purchase "
                        "and an inventory value"),
    (AL, INV, "al_inv", "② on_inventory + ④ — two inventory valuations of one device"),
)
OVERLAP_LABEL = {
    "ma_pos": "① Marketplace order ∩ ③ POS sale",
    "al_pos": "② Consignment ∩ ③ POS sale",
    "ma_inv": "① Marketplace order ∩ ④ Inventory",
    "al_inv": "② Consignment ∩ ④ Inventory",
}


def _keys_of(ev):
    """The device keys an event can be joined on. ① can legitimately carry SEVERAL (a shared
    activation order), so the overlap scan uses the whole linked set while recognition (which must pick
    exactly one row) only trusts an unambiguous single key."""
    if ev.get("linked_keys"):
        return list(ev["linked_keys"])
    return [ev["device_key"]] if ev.get("device_key") else []


def find_overlaps(events):
    """Group events by device key and report every device seen in MORE THAN ONE source.

    Returns (overlaps, summary):
      overlaps — one row per overlapping DEVICE: {device_key, sources, pairs, rows, gross_amount,
                 duplicate_amount, by_source{source: amount}, stores, products}. `duplicate_amount` is
                 what a naive sum would ADD ON TOP of the single best figure (Σ amounts − max amount) —
                 the honest size of the double-count, not a guess at which source is "right".
      summary  — device/row/$ counts overall and per NAMED pair, plus the ①-specific counts, so the page
                 can show each of the design-§3 pairs separately instead of one blended number.

    ①'s participation is real but INDIRECT (it joins through the activation order), so an ① row appears
    in an overlap only when that join produced a key. ① rows that never reached an IMEI are NOT silently
    treated as non-overlapping — they are the `unlinkable` population, reported separately, because an
    un-linkable purchase COULD be the same device as a POS sale and nothing in the data can prove it
    either way.
    """
    by_key = {}
    for ev in events or []:
        for k in _keys_of(ev):
            by_key.setdefault(k, []).append(ev)

    overlaps = []
    pair_stat = {code: {"devices": 0, "amount": 0.0, "duplicate_amount": 0.0}
                 for _a, _b, code, _d in OVERLAP_PAIRS}
    for k, evs in by_key.items():
        srcs = {e["source"] for e in evs}
        if len(srcs) < 2:
            continue
        by_source, gross = {}, 0.0
        for e in evs:
            a = e.get("amount")
            if a is None:
                continue
            by_source[e["source"]] = round(by_source.get(e["source"], 0.0) + float(a), 2)
            gross += float(a)
        dup = round(gross - max(by_source.values()), 2) if by_source else 0.0
        pairs = []
        for a, b, code, _desc in OVERLAP_PAIRS:
            if a in srcs and b in srcs:
                pairs.append(code)
                pair_stat[code]["devices"] += 1
                pair_stat[code]["amount"] = round(pair_stat[code]["amount"]
                                                  + (by_source.get(a, 0.0) + by_source.get(b, 0.0)), 2)
                pair_stat[code]["duplicate_amount"] = round(
                    pair_stat[code]["duplicate_amount"]
                    + min(by_source.get(a, 0.0), by_source.get(b, 0.0)), 2)
        overlaps.append({
            "device_key": k,
            "sources": sorted(srcs, key=lambda s: SOURCES.index(s)),
            "source_ns": " ".join(SOURCE_META[s]["n"] for s in sorted(srcs, key=lambda s: SOURCES.index(s))),
            "pairs": pairs,
            "pair_labels": [OVERLAP_LABEL[p] for p in pairs],
            "rows": len(evs),
            "by_source": by_source,
            "gross_amount": round(gross, 2),
            "duplicate_amount": dup,
            "stores": sorted({e.get("store_label") for e in evs if e.get("store_label")}),
            "products": sorted({e.get("product") for e in evs if e.get("product")}),
            "months": sorted({e.get("month") for e in evs if e.get("month")}),
        })
    overlaps.sort(key=lambda o: (-(o["duplicate_amount"] or 0), o["device_key"]))
    summary = {
        "devices": len(overlaps),
        "rows": sum(o["rows"] for o in overlaps),
        "gross_amount": round(sum(o["gross_amount"] for o in overlaps), 2),
        "duplicate_amount": round(sum(o["duplicate_amount"] for o in overlaps), 2),
        "pairs": [{"code": c, "label": OVERLAP_LABEL[c], "why": d,
                   "devices": pair_stat[c]["devices"],
                   "amount": pair_stat[c]["amount"],
                   "duplicate_amount": pair_stat[c]["duplicate_amount"]}
                  for _a, _b, c, d in OVERLAP_PAIRS],
        "ambiguous_link_rows": sum(1 for e in (events or []) if e.get("ambiguous_link")),
    }
    return overlaps, summary


def unlinkable_summary(events):
    """Per-source counts + $ of rows that CANNOT reach an IMEI (§9 Q1's caveat, measured not assumed).
    Also reports `priceless` rows (a row with no usable dollar) separately, because "we cannot join it"
    and "there is no number on it" are different problems with different fixes."""
    out = {}
    for s in SOURCES:
        evs = [e for e in (events or []) if e["source"] == s]
        un = [e for e in evs if not e.get("linkable")]
        pl = [e for e in evs if e.get("amount") is None]
        out[s] = {
            "source": s, "n": SOURCE_META[s]["n"], "label": SOURCE_META[s]["label"],
            "rows": len(evs),
            "linkable_rows": len(evs) - len(un),
            "unlinkable_rows": len(un),
            "unlinkable_amount": round(sum(float(e["amount"]) for e in un if e.get("amount") is not None), 2),
            "priceless_rows": len(pl),
            "amount": round(sum(float(e["amount"]) for e in evs if e.get("amount") is not None), 2),
            "reasons": sorted({e["unlink_reason"] for e in un if e.get("unlink_reason")}),
        }
    out["total"] = {
        "rows": sum(out[s]["rows"] for s in SOURCES),
        "unlinkable_rows": sum(out[s]["unlinkable_rows"] for s in SOURCES),
        "unlinkable_amount": round(sum(out[s]["unlinkable_amount"] for s in SOURCES), 2),
        "priceless_rows": sum(out[s]["priceless_rows"] for s in SOURCES),
        "amount": round(sum(out[s]["amount"] for s in SOURCES), 2),
    }
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# RECOGNITION — the owner's §9 Q1/Q2 policy, applied to the events (PREVIEW ONLY, nothing is written)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def parse_precedence(csv):
    """A precedence list from a CSV parameter, validated against SOURCES and de-duped, with ④ removed
    (it is the C3 valuation leg and can never recognize a cost). Anything unusable falls back to the
    §9 default `ma_fulfillment,asset_lending,pos_sale` — invoice-first, sale-time fallback."""
    if isinstance(csv, (list, tuple)):
        raw = [str(x).strip().lower() for x in csv]
    else:
        raw = [s.strip().lower() for s in str(csv or "").split(",")]
    out = []
    for s in raw:
        if s in SOURCES and s not in NEVER_RECOGNIZED and s not in out:
            out.append(s)
    return tuple(out) if out else DEFAULT_PRECEDENCE


def recognize(events, precedence=DEFAULT_PRECEDENCE):
    """Apply §9 Q1 (invoice-first, sale-time fallback, IMEI dedup) in place and return the summary.

    Per DEVICE KEY: among the events that carry a real dollar and a recognizable source, the one whose
    source comes FIRST in `precedence` wins; within one source the EARLIEST event date wins (first
    evidence), then the larger amount, then a stable id — so the choice is deterministic and a re-run
    cannot flip. Everything else in that group is marked `recognized = False` with `suppressed_by`
    naming what beat it. That IS the "never recognized again at sale" rule.

    Events with NO device key cannot be deduped. They are still recognized when their source is a
    recognition source and they carry a dollar — because the cost is real — but `dedup_covered` stays
    False and they are totalled as `at_risk_amount`: the dollars the owner's "IMEI-based, so never
    duplicate" premise does not cover. This is the number the measurement pass exists to produce.

    ④ inventory rows are never recognized (§9 Q3 — they are the balance-sheet leg) and say so.
    """
    prec = tuple(precedence or DEFAULT_PRECEDENCE)
    rank = {s: i for i, s in enumerate(prec)}
    groups = {}
    loose = []
    for ev in events or []:
        ev["recognized"] = False
        ev["suppressed_by"] = None
        ev["dedup_covered"] = False
        if ev["source"] in NEVER_RECOGNIZED:
            ev["recognition_reason"] = ("inventory VALUATION (§9 Q3 balance-sheet leg) — never a cost "
                                        "recognition")
            continue
        if ev["source"] not in rank:
            ev["recognition_reason"] = f"{SOURCE_META[ev['source']]['label']} is not in the recognition precedence"
            continue
        if ev.get("amount") is None:
            ev["recognition_reason"] = ev.get("priceless_reason") or "no usable dollar on this row"
            continue
        # §9 Q2 read strictly: the VIP-BILLED amount is the consignment device's COGS. A consignment
        # device the distributor has NOT billed yet therefore has no COGS yet — it is liability +
        # inventory only. Those rows are shown (they are needed for the C3 valuation and the ②↔④
        # overlap) but they are NOT a cost, and they say why instead of quietly inflating the month.
        if ev["source"] == AL and ev.get("billed") is False:
            ev["recognition_reason"] = ("not billed by the distributor yet — §9 Q2 makes the BILLED "
                                        "amount the COGS, so this device is liability + inventory only, "
                                        "not yet a cost")
            continue
        k = ev.get("device_key")
        if k:
            groups.setdefault(k, []).append(ev)
        else:
            loose.append(ev)

    def _order(e):
        return (rank[e["source"]], e.get("event_date") or "9999-99-99",
                -float(e.get("amount") or 0), str(e.get("ref") or ""))

    for k, evs in groups.items():
        evs.sort(key=_order)
        win = evs[0]
        win["recognized"] = True
        win["dedup_covered"] = True
        win["recognition_reason"] = (
            f"invoice evidence — {win['source_n']} {win['source_label']}"
            f"{' @ ' + win['event_date'] if win.get('event_date') else ''}"
            if win["is_invoice"] else
            f"no invoice in the system for this device — POS cost at sale (§9 Q1 fallback)"
            f"{' @ ' + win['event_date'] if win.get('event_date') else ''}")
        for e in evs[1:]:
            e["dedup_covered"] = True
            e["suppressed_by"] = f"{win['source_n']} {win['source_label']}"
            e["recognition_reason"] = (f"superseded by {win['source_n']} {win['source_label']} on the "
                                       f"same IMEI (§9 Q1 dedup)")
    for e in loose:
        e["recognized"] = True
        e["dedup_covered"] = False
        e["recognition_reason"] = (
            f"{'invoice evidence' if e['is_invoice'] else 'POS cost at sale'} — but this row has NO "
            f"IMEI/serial, so it CANNOT be deduped against the other sources ({e.get('unlink_reason') or 'no device key'})")

    return policy_summary(events, prec)


def policy_summary(events, precedence=DEFAULT_PRECEDENCE):
    """Summarize an ALREADY-DECIDED event list — the totals only, no decisions re-made.

    WHY IT IS SEPARATE FROM `recognize()`: the IMEI dedup must be decided over the WHOLE window (filter
    first and a different row wins, which would be a fake number), but the totals a human reads must
    describe WHAT IS ON SCREEN (RULE FOUR's WYSIWYG). So the router calls `recognize()` once over every
    event and this over the FILTERED subset. Decided globally, reported locally — and no filtered view
    can ever show a total that includes rows it is not showing.
    """
    prec = tuple(precedence or DEFAULT_PRECEDENCE)
    rec = [e for e in (events or []) if e.get("recognized")]
    sup = [e for e in (events or []) if e.get("suppressed_by")]
    at_risk = [e for e in rec if not e.get("dedup_covered")]
    by_source = {}
    for e in rec:
        b = by_source.setdefault(e["source"], {"source": e["source"], "n": e["source_n"],
                                              "label": e["source_label"], "rows": 0, "amount": 0.0})
        b["rows"] += 1
        b["amount"] = round(b["amount"] + float(e.get("amount") or 0), 2)
    return {
        "precedence": list(prec),
        "precedence_label": " → ".join(f"{SOURCE_META[s]['n']} {SOURCE_META[s]['label']}" for s in prec),
        "recognized_rows": len(rec),
        "recognized_amount": round(sum(float(e.get("amount") or 0) for e in rec), 2),
        "by_source": [by_source[s] for s in SOURCES if s in by_source],
        "suppressed_rows": len(sup),
        "suppressed_amount": round(sum(float(e.get("amount") or 0) for e in sup), 2),
        "at_risk_rows": len(at_risk),
        "at_risk_amount": round(sum(float(e.get("amount") or 0) for e in at_risk), 2),
        "invoice_rows": sum(1 for e in rec if e["is_invoice"]),
        "invoice_amount": round(sum(float(e.get("amount") or 0) for e in rec if e["is_invoice"]), 2),
        "fallback_rows": sum(1 for e in rec if not e["is_invoice"]),
        "fallback_amount": round(sum(float(e.get("amount") or 0) for e in rec if not e["is_invoice"]), 2),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# C2 LIABILITY + C3 INVENTORY LEGS — both shown, neither netted away
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def liability_leg(asset_events_all):
    """§9 Q2: consignment is a LIABILITY until sold. The unsold `owed_to_vip` is reported as its own
    figure so a reader can see it was NOT netted against COGS (the 2026-06-25 incident: this liability
    was wrongly zeroed once and had to be restored — memory `owed-vip-paygo-standalone-liability`)."""
    unsold = [e for e in (asset_events_all or []) if e.get("on_inventory")]
    by_status = [e for e in (asset_events_all or []) if e.get("on_inventory_status")]
    disagree = [e for e in (asset_events_all or [])
                if e.get("oninv_definitions_agree") is False]
    out = {
        "unsold_devices": len(unsold),
        "unsold_owed": round(sum(float(e["amount"]) for e in unsold if e.get("amount") is not None), 2),
        "all_devices": len(asset_events_all or []),
        "all_owed": round(sum(float(e["amount"]) for e in (asset_events_all or [])
                              if e.get("amount") is not None), 2),
        "status_unsold_devices": len(by_status),
        "status_unsold_owed": round(sum(float(e["amount"]) for e in by_status
                                        if e.get("amount") is not None), 2),
        "definition_disagree_devices": len(disagree),
        "definition_disagree_owed": round(sum(float(e["amount"]) for e in disagree
                                              if e.get("amount") is not None), 2),
        "note": ("Consignment billing stays a LIABILITY until the device sells (§9 Q2) — it has to be "
                 "paid regardless. It is shown here as its own figure and is NEVER netted against COGS."),
        "definition_note": None,
    }
    if disagree:
        out["definition_note"] = (
            f"{len(disagree):,} ledger row(s) (${out['definition_disagree_owed']:,.2f}) are unsold under "
            "ONE definition and not the other: the asset module (the table's owner) reads "
            "`date_sold IS NULL AND category ILIKE '%On Inventory%'`, the P&L's balance-sheet leg reads "
            "`status = 'On Inventory'`. Both figures are shown above — this report does not pick one, "
            "because unifying them would change the balance sheet.")
    return out


def inventory_leg(inv_events_all, asset_events_all):
    """§9 Q3's balance-sheet leg, and the honest reason its Δ cannot be computed today.

    Reports: the POS snapshot valuation (④ Σ unit_cost), the ledger's own unsold valuation (② unsold
    owed_to_vip), the devices valued by BOTH (the design-§3 "two inventory valuations of one device"
    overlap) and the snapshot's as-of date range.

    `delta_inventory` is deliberately **None**: `commcalc.inventory_aging_device` is UNIQUE on
    (org_id, imei) and `commcalc.inventory_value` is PK (org_id, store), so each holds exactly ONE
    CURRENT snapshot — there is no month-end history to subtract. Returning 0 here would read as "no
    change", which is a different and false claim. Period-stamped snapshots are an Option-B item.
    """
    inv_valued = [e for e in (inv_events_all or []) if e.get("amount") is not None]
    unsold = [e for e in (asset_events_all or [])
              if e.get("on_inventory") and e.get("amount") is not None]
    inv_keys = {e["device_key"] for e in inv_valued if e.get("device_key")}
    both = [e for e in unsold if e.get("device_key") in inv_keys]
    asof = sorted({e.get("as_of_date") for e in (inv_events_all or []) if e.get("as_of_date")})
    return {
        "snapshot_devices": len(inv_valued),
        "snapshot_amount": round(sum(float(e["amount"]) for e in inv_valued), 2),
        "snapshot_as_of_from": (asof[0] if asof else None),
        "snapshot_as_of_to": (asof[-1] if asof else None),
        "snapshot_priceless_rows": sum(1 for e in (inv_events_all or []) if e.get("amount") is None),
        "ledger_unsold_devices": len(unsold),
        "ledger_unsold_amount": round(sum(float(e["amount"]) for e in unsold), 2),
        "double_valued_devices": len(both),
        "double_valued_amount": round(sum(float(e["amount"]) for e in both), 2),
        "delta_inventory": None,
        "delta_note": ("Δ(inventory) is NOT derivable from today's data: inventory_aging_device is "
                       "UNIQUE on (org_id, imei) and inventory_value is PK (org_id, store), so each "
                       "holds ONE CURRENT snapshot with no month-end history to subtract. The closing "
                       "valuation above is what the data can prove; a 0 here would falsely read as "
                       "“no change”. Period-stamped snapshots are an Option-B item, not a "
                       "formula this report can invent."),
        "policy_note": ("§9 Q3 (periodic inventory): device P&L COGS ≈ recognized costs − "
                        "Δ(inventory asset). BOTH legs are shown so the owner sees the adjustment, "
                        "not just the gross recognition."),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# DELTA PREVIEW — month × store: today's device-COGS route vs the §9 policy
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def delta_table(today_map, events, store_labels=None, market_of=None):
    """The deliverable the owner reviews before any Option-C decision.

    `today_map`  — {(month 'YYYY-MM', store_key): amount} produced by TODAY's device-COGS route (the
                   P&L's `device_cost` line: Σ (ext_price − GP) over device sale lines). The router
                   computes it through the finance module's OWN classifier + store resolver so the
                   comparison is against the real route, not a re-implementation of it.
    `events`     — the recognized/suppressed events, already filtered exactly as the page shows them.

    Returns (rows, totals). Each row: month, store, today, policy, delta, delta_pct, the policy split by
    source, and the at-risk (un-dedupable) portion of that cell — so a big delta can be read as "this is
    invoice cost arriving in a different month" rather than an unexplained jump. Sorted by month, then
    biggest absolute delta.

    Cells present in only ONE leg are KEPT (a month/store the policy recognizes but today's route does
    not is exactly the finding), with the missing side as 0 and `only_in` naming which leg it came from.
    """
    store_labels = dict(store_labels or {})
    pol = {}
    for e in events or []:
        if not e.get("recognized"):
            continue
        key = (e.get("month") or "", e.get("store_key") or "")
        c = pol.setdefault(key, {"amount": 0.0, "rows": 0, "by_source": {}, "at_risk": 0.0,
                                 "label": e.get("store_label"), "market": e.get("market")})
        a = float(e.get("amount") or 0)
        c["amount"] = round(c["amount"] + a, 2)
        c["rows"] += 1
        c["by_source"][e["source"]] = round(c["by_source"].get(e["source"], 0.0) + a, 2)
        if not e.get("dedup_covered"):
            c["at_risk"] = round(c["at_risk"] + a, 2)
        if not c["label"]:
            c["label"] = e.get("store_label")
        if not c["market"]:
            c["market"] = e.get("market")

    rows = []
    for key in sorted(set(list(today_map or {}) + list(pol)),
                      key=lambda k: (k[0] or "￿", k[1] or "")):
        m, sk = key
        today = round(float((today_map or {}).get(key) or 0), 2)
        c = pol.get(key) or {"amount": 0.0, "rows": 0, "by_source": {}, "at_risk": 0.0,
                             "label": None, "market": None}
        policy = round(float(c["amount"]), 2)
        label = c.get("label") or store_labels.get(sk) or sk or NO_STORE
        mkt = c.get("market")
        if not mkt and market_of:
            try:
                mkt = market_of(sk)
            except Exception:
                mkt = None
        only = None
        if today and not policy:
            only = "today"
        elif policy and not today:
            only = "policy"
        rows.append({
            "month": m or "", "month_label": (month_label(m) if m else NO_MONTH),
            "store_key": sk or "", "store": label, "market": mkt or NO_MARKET,
            "today": today, "policy": policy,
            "delta": round(policy - today, 2),
            "delta_pct": (round((policy - today) / today * 100, 1) if today else None),
            "policy_rows": c["rows"],
            "policy_by_source": {s: c["by_source"].get(s, 0.0) for s in SOURCES if s in c["by_source"]},
            "at_risk": c["at_risk"],
            "only_in": only,
        })
    rows.sort(key=lambda r: (r["month"] or "￿", -abs(r["delta"]), r["store"].lower()))
    totals = {
        "today": round(sum(r["today"] for r in rows), 2),
        "policy": round(sum(r["policy"] for r in rows), 2),
        "delta": round(sum(r["delta"] for r in rows), 2),
        "at_risk": round(sum(r["at_risk"] for r in rows), 2),
        "cells": len(rows),
        "months": len({r["month"] for r in rows if r["month"]}),
        "stores": len({r["store_key"] for r in rows if r["store_key"]}),
        "only_today_cells": sum(1 for r in rows if r["only_in"] == "today"),
        "only_policy_cells": sum(1 for r in rows if r["only_in"] == "policy"),
    }
    totals["delta_pct"] = (round(totals["delta"] / totals["today"] * 100, 1)
                           if totals["today"] else None)
    by_month = {}
    for r in rows:
        b = by_month.setdefault(r["month"], {"month": r["month"], "month_label": r["month_label"],
                                             "today": 0.0, "policy": 0.0, "at_risk": 0.0, "cells": 0})
        b["today"] = round(b["today"] + r["today"], 2)
        b["policy"] = round(b["policy"] + r["policy"], 2)
        b["at_risk"] = round(b["at_risk"] + r["at_risk"], 2)
        b["cells"] += 1
    for b in by_month.values():
        b["delta"] = round(b["policy"] - b["today"], 2)
        b["delta_pct"] = (round(b["delta"] / b["today"] * 100, 1) if b["today"] else None)
    totals["by_month"] = sorted(by_month.values(), key=lambda b: b["month"] or "￿")
    return rows, totals


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# FILTERS + OPTIONS (RULE FIVE, pick-don't-type, from the values PRESENT IN THE DATA)
# Every filter is applied HERE, server-side, so tiles ≡ table ≡ delta ≡ export (RULE FOUR's WYSIWYG).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _opt_list(events, get):
    seen = {}
    for e in events or []:
        v = get(e)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            seen.setdefault(s.lower(), s)
    return sorted(seen.values(), key=lambda s: s.lower())


def filter_options(events):
    """Option lists for the standard bar + the appended facets, from the UNFILTERED events so a picker
    never collapses to the current selection. Sentinel buckets are APPENDED only when they are real."""
    stores = _opt_list(events, lambda e: e.get("store_label"))
    if any(not _s(e.get("store_label")) or e.get("store_label") == NO_STORE for e in events or []):
        stores = [s for s in stores if s != NO_STORE] + [NO_STORE]
    markets = _opt_list(events, lambda e: e.get("market"))
    if any(not _s(e.get("market")) for e in events or []):
        markets = markets + [NO_MARKET]
    reps = _opt_list(events, lambda e: e.get("rep"))
    products = _opt_list(events, lambda e: e.get("product"))
    if any(not _s(e.get("product")) for e in events or []):
        products = products + [NO_PRODUCT]
    months = sorted({e["month"] for e in events or [] if e.get("month")})
    return {
        "source_options": [{"id": s, "label": f"{SOURCE_META[s]['n']} {SOURCE_META[s]['label']}"}
                           for s in SOURCES if any(e["source"] == s for e in events or [])],
        "arrangement_options": _opt_list(events, lambda e: e.get("arrangement_label")),
        "timing_options": [{"id": t, "label": lbl} for t, lbl in
                           (("ordered", "Ordered (①)"), ("vip_billed", "VIP billed (②)"),
                            ("sold", "Sold (③)"), ("snapshot", "Snapshot (④)"))
                           if any(e["timing"] == t for e in events or [])],
        "store_options": stores, "market_options": markets, "rep_options": reps,
        "product_options": products,
        "month_options": [{"id": m, "label": month_label(m)} for m in months],
        "distributor_options": _opt_list(events, lambda e: e.get("distributor")),
    }


def apply_filters(events, *, sources="", arrangements="", timings="", stores="", markets="", reps="",
                  products="", months="", overlap_device_keys=None, overlap_only=False,
                  unlinkable_only=False, recognized_only=False, min_amount=0):
    """Narrow the events by the RULE FIVE core set (period/month · store · market · rep) plus the
    appended facets (source · arrangement · timing · product) and the three investigation toggles.

    RULE FIVE deviation, STATED not hidden: only ③ carries a rep/salesperson — a marketplace order is
    placed against the dealer account, the asset ledger is keyed on a device, and an inventory snapshot
    has no seller. A `reps` selection therefore narrows ③ and, because silently dropping the other
    three sources would misreport them as $0, rows from a rep-less source are KEPT and the endpoint says
    so in a note. `unlinkable_only` / `overlap_only` / `recognized_only` are investigation views, not
    defaults.
    """
    src, arr, tim = _sel(sources), _sel(arrangements), _sel(timings)
    st, mk, rp = _sel(stores), _sel(markets), _sel(reps)
    pr, mo = _sel(products), _sel(months)
    okeys = set(overlap_device_keys or ())
    try:
        floor = abs(float(min_amount or 0))
    except (TypeError, ValueError):
        floor = 0.0
    out = []
    for e in events or []:
        if src and e["source"] not in src:
            continue
        if arr and _fold(e.get("arrangement_label")) not in arr:
            continue
        if tim and e["timing"] not in tim:
            continue
        if st:
            lbl = _fold(e.get("store_label"))
            raw = _fold(e.get("store"))
            if not (lbl in st or raw in st or ((not lbl or lbl == _fold(NO_STORE))
                                               and _fold(NO_STORE) in st)):
                continue
        if mk:
            m = _fold(e.get("market"))
            if not ((m and m in mk) or ((not m) and _fold(NO_MARKET) in mk)):
                continue
        if rp and e.get("rep") is not None:
            if _fold(e.get("rep")) not in rp:
                continue
        if pr:
            p = _fold(e.get("product"))
            if not (p in pr or ((not p) and _fold(NO_PRODUCT) in pr)):
                continue
        if mo and _fold(e.get("month")) not in mo:
            continue
        if overlap_only and not (set(_keys_of(e)) & okeys):
            continue
        if unlinkable_only and e.get("linkable"):
            continue
        if recognized_only and not e.get("recognized"):
            continue
        if floor and abs(float(e.get("amount") or 0)) < floor:
            continue
        out.append(e)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# TILES + GROUPING
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def tiles_for(events, overlap_summary=None, policy=None, today=None, inventory=None, liability=None):
    """Headline numbers over the FILTERED events, so the tiles can never disagree with the table.

    `naive_total` is labelled naive ON PURPOSE: it is the number you get by summing all four sources,
    which is the very thing this report exists to show is wrong. The matched-overlap $ underneath it is
    how much of that number is the same device twice.
    """
    per = unlinkable_summary(events)
    naive = per["total"]["amount"]
    pol = policy or {}
    tod = today or {}
    net = None
    if tod.get("available"):
        net = round(float(pol.get("recognized_amount") or 0) - float(tod.get("device_cogs") or 0), 2)
    return {
        "rows": per["total"]["rows"],
        "devices": len({k for e in events or [] for k in _keys_of(e)}),
        "naive_total": naive,
        "by_source": [per[s] for s in SOURCES],
        "overlap": overlap_summary or {"devices": 0, "rows": 0, "gross_amount": 0.0,
                                       "duplicate_amount": 0.0, "pairs": []},
        "unlinkable": {"rows": per["total"]["unlinkable_rows"],
                       "amount": per["total"]["unlinkable_amount"],
                       "by_source": {s: per[s]["unlinkable_amount"] for s in SOURCES},
                       "rows_by_source": {s: per[s]["unlinkable_rows"] for s in SOURCES}},
        "priceless_rows": per["total"]["priceless_rows"],
        "policy": {k: pol.get(k) for k in ("recognized_rows", "recognized_amount", "suppressed_rows",
                                           "suppressed_amount", "at_risk_rows", "at_risk_amount",
                                           "invoice_amount", "fallback_amount", "by_source",
                                           "precedence_label")},
        "today": tod,
        "net_delta": net,
        "inventory": inventory or {},
        "liability": liability or {},
    }


GROUP_BY = ("source", "arrangement", "month", "store", "market", "product", "device", "timing",
            "distributor")
GROUP_LABEL = {"source": "Source", "arrangement": "Arrangement", "month": "Month", "store": "Store",
               "market": "Market", "product": "Device / item", "device": "Device (IMEI)",
               "timing": "Timing", "distributor": "Distributor"}


def _group_key(e, gb):
    if gb == "source":
        return (e["source"], f"{e['source_n']} {e['source_label']}")
    if gb == "arrangement":
        return (_fold(e.get("arrangement_label")), e.get("arrangement_label") or UNMAPPED_ARRANGEMENT)
    if gb == "month":
        return (e.get("month") or "", e.get("month_label") or NO_MONTH)
    if gb == "store":
        return (_fold(e.get("store_label")), e.get("store_label") or NO_STORE)
    if gb == "market":
        return (_fold(e.get("market")), e.get("market") or NO_MARKET)
    if gb == "product":
        return (_fold(e.get("product")), e.get("product") or NO_PRODUCT)
    if gb == "device":
        return (e.get("device_key") or "", e.get("device_key") or NO_DEVICE_KEY)
    if gb == "distributor":
        return (_fold(e.get("distributor")), e.get("distributor") or UNMAPPED_ARRANGEMENT)
    return (e["timing"], e.get("timing_label") or e["timing"])


def group_rows(events, group_by="source"):
    """Aggregate the FILTERED events by one dimension. Each group reports the naive total AND the
    recognized total side by side — the gap between them IS the double-count in that slice."""
    gb = group_by if group_by in GROUP_BY else "source"
    buckets = {}
    for e in events or []:
        k, label = _group_key(e, gb)
        buckets.setdefault(k, {"key": k, "label": label, "rows": []})["rows"].append(e)
    out = []
    for b in buckets.values():
        evs = b["rows"]
        amt = [float(e["amount"]) for e in evs if e.get("amount") is not None]
        rec = [e for e in evs if e.get("recognized")]
        sup = [e for e in evs if e.get("suppressed_by")]
        un = [e for e in evs if not e.get("linkable")]
        dates = sorted({e["event_date"] for e in evs if e.get("event_date")})
        out.append({
            "key": b["key"], "label": b["label"],
            "rows": len(evs),
            "devices": len({k for e in evs for k in _keys_of(e)}),
            "amount": round(sum(amt), 2),
            "recognized_rows": len(rec),
            "recognized_amount": round(sum(float(e["amount"] or 0) for e in rec), 2),
            "suppressed_rows": len(sup),
            "suppressed_amount": round(sum(float(e["amount"] or 0) for e in sup), 2),
            "unlinkable_rows": len(un),
            "unlinkable_amount": round(sum(float(e["amount"]) for e in un
                                           if e.get("amount") is not None), 2),
            "priceless_rows": sum(1 for e in evs if e.get("amount") is None),
            "sources": sorted({e["source"] for e in evs}, key=lambda s: SOURCES.index(s)),
            "first_date": (dates[0] if dates else None), "last_date": (dates[-1] if dates else None),
        })
    out.sort(key=lambda g: (-(g["amount"] or 0), g["label"].lower()))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# HONEST HEADER TEXT — the definitions the report states out loud, carried into every export subtitle
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def definition_note():
    return ("One row per DEVICE-COST record from all four sources, tagged with its source, the "
            "distributor ARRANGEMENT that source is on (read from this org's distributor config, not "
            "guessed) and the date it is timed on. Read-only measurement: no P&L, GP, payout, rate or "
            "plan number is changed, and nothing is written.")


def policy_note(policy=None, ma_date=DEFAULT_MA_DATE, price_basis="unit"):
    p = (policy or {}).get("precedence_label") or " → ".join(
        f"{SOURCE_META[s]['n']} {SOURCE_META[s]['label']}" for s in DEFAULT_PRECEDENCE)
    return (f"POLICY PREVIEW (owner answers 2026-07-30, design note §9): invoice-first with a sale-time "
            f"fallback, deduped by IMEI — precedence {p}. Consignment billing is the device's COGS while "
            f"owed_to_vip stays a liability (shown separately, never netted away). Unsold inventory is a "
            f"balance-sheet asset, so the ④ snapshot is a valuation, never a recognition. ① is timed on "
            f"its “{ma_date}” date and extended on the “{price_basis}” price basis. "
            f"Display only — the P&L is untouched.")


def caveat_note(unlink=None, policy=None):
    u = (unlink or {}).get("total") or {}
    bits = [("“Since it is IMEI-based it can never be duplicate entries” holds only for rows "
             "that can REACH an IMEI.")]
    if u.get("unlinkable_rows"):
        bits.append(f"{u['unlinkable_rows']:,} row(s) worth ${u.get('unlinkable_amount', 0):,.2f} "
                    "cannot be linked to one, so they are counted and shown rather than assumed unique.")
    else:
        bits.append("In this window every row reached a device key — measured, not assumed.")
    if (policy or {}).get("at_risk_rows"):
        bits.append(f"${policy['at_risk_amount']:,.2f} of the recognized total sits on rows with no "
                    "IMEI/serial and therefore could not be deduped at all.")
    bits.append("① reaches an IMEI ONLY through raw_ma_commission.activation_order (activated devices "
                "only); VIP invoice evidence joins by SERIAL, not imei.")
    return " ".join(bits)


def source_legend():
    """The four sources as the page's legend + every export's provenance block. Includes the
    `ma_upload.FIELD_LABELS` asset-lending parity for ①'s cost field, so one handset cost is described
    in ONE vocabulary across the two files (design §7)."""
    out = []
    for s in SOURCES:
        m = SOURCE_META[s]
        out.append({"source": s, "n": m["n"], "label": m["label"], "table": m["table"],
                    "grain": m["grain"], "amount_kind": m["amount_kind"],
                    "timing": m["timing"], "timing_label": m["timing_label"],
                    "is_invoice": m["invoice"], "means": m["means"], "link": m["link"]})
    try:
        from app.modules.commcalc import ma_upload as _mu
        price = _mu.field_meta("price")
        out[0]["cost_field"] = {"col": price["col"], "label": price["label"],
                                "asset_field": price["asset_field"],
                                "asset_label": price["asset_label"],
                                "parity_note": price["parity_note"]}
    except Exception:                                      # a vocabulary miss must never break the page
        pass
    return out


# ── the PAGE gate (default-closed, same shape as the shipped ma_handset_cogs grant) ────────────────
GRANT_KEY = "device_cost_recon"


def device_cost_recon_allowed(caller):
    """Gate the WHOLE REPORT. DEFAULT-CLOSED, grantable via the DATA_GRANTS 'device_cost_recon' key —
    the same resolution SHAPE as `ma_handset_cogs.ma_handset_cogs_allowed`: this surface exposes what
    every device cost us, per device, across every source, which is strictly MORE sensitive than the
    per-source reports it reconciles.

    PURE over an already-resolved caller dict (no DB, no HTTP) so it is unit-provable:
      super_admin / perms.scope == 'all' / role == 'admin'                        -> allow
      'device_cost_recon' in perms.modules, or perms.data.device_cost_recon truthy -> allow
      else (including caller=None, i.e. an unresolvable token)                    -> DENY

    Frontend mirror: `hasDataGrant(perms, 'device_cost_recon')`.
    """
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if GRANT_KEY in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get(GRANT_KEY)):
        return True
    return False
