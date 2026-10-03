"""STORE VISIT FOLLOW-THROUGH — what a visit left to be done, who hears about it, and the accessory
order it asks for.

OWNER ASK 2026-10-03, verbatim:
    "based on the store visits need to create an email and whats app alert to the dm and all people
     above to send them a lit of all items which are needed to be done, also create a notification
     list for the store visit, default will be dm and above, a list of accesories to be created as a
     separate notification and a purchase oirder automatically created to be sent to vaccessorize"

THREE OUTPUTS, AND WHY THEY ARE THREE AND NOT ONE. A visit produces work for the store (fix the
display, finish the plan step) and a shopping list for the buyer. Those go to different people and
are acted on differently, so they are two notifications with two scopes and two dedup trails — the
owner asked for the accessory list "as a separate notification". The purchase order is the third:
the shopping list expressed as money, which is a DRAFT and nothing else until a human sends it.

NOTHING HERE IS A SECOND MECHANISM — what was already built and is dereferenced, not copied:

  WHO HEARS          `commcalc/manager_digest`. `recipients_for` is the house DM ∪ above-DM rule,
                     which is exactly the owner's "the dm and all people above", so no new default
                     is invented. The tenant's own NOTIFICATION LIST is the existing
                     `storeops.alert_recipient` rows (mig 089) — one table, one editor — read
                     through `manager_digest.named_extras` and `use_hierarchy`, the one home this
                     change adds for "the named list on top of the tree default". The DM-and-above
                     default is what a tenant gets with no list configured, which is the ask.
  ONE DIGEST EACH    `manager_digest.plan_digests`. Not a second fan-out, not a second dedup; the
                     ref_key spelling and the unreachable-recipient skip are the house rule.
  ONCE PER WHAT      `storeops.alert_log`, through the same already_sent / record_sent pair every
                     other sweep uses. The tail is (visit, item) and deliberately NOT the date: a
                     store visit is an EVENT, not a daily state, so its to-do list is announced once
                     and re-announced only for work that is new.
  THE ORDER          `commcalc.purchase_order` + `purchase_order_line` (mig 301) through
                     `supply/store.create_order`, with `source` naming where it came from — the same
                     table, numbering and draft->submitted lifecycle a supply cart already uses. A
                     store-visit accessory list is not a different kind of purchase order.
  WHAT NEEDS DOING   the visit's OWN rows (checklist responses, the DM's action-item overlay, the
                     agreed action plan, the visit header's evidence). This module does not decide
                     what a visit should contain; it reads what the visit recorded.

RULE TWO: no carrier, tenant, store, vendor or product name appears in this file. The accessory
vendor is a `commcalc.po_vendor` row the tenant points at (`store_visit_accessory_vendor_id`); the
reorder LINK was already per-tenant config (`storeops.store_visit_config`, mig 503).

PURE: stdlib only — no DB, no framework, no network — proven by `backend/harness_storevisit_alerts.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md.
"""

ALERT_SCOPE = "store_visit_todo"
ACCESSORY_SCOPE = "store_visit_accessories"

# `commcalc.purchase_order.source` for a PO raised from a store visit's accessory list. The same
# column `supply_cart` already uses, so one PO table answers "where did this order come from".
PO_SOURCE = "store_visit"

# A PO is a DRAFT and nothing more until a human sends it. 'submit' is deliberately NOT a mode: no
# transport to a vendor's own store exists yet, and a mode that silently did nothing would be worse
# than refusing the word. See the index entry for what integrating one needs.
PO_MODES = ("off", "draft")

# ── THE TO-DO VOCABULARY (one home) ─────────────────────────────────────────────────────────────
# What a visit can leave undone. A caller renders the label; nothing spells these strings itself.
KINDS = (
    ("checklist", "Checklist item not passed"),
    ("action_item", "Action item raised and not discussed"),
    ("action_plan", "Agreed action-plan step still open"),
    ("rep_coverage", "Scheduled rep did not work it, and no reason was given"),
    ("evidence", "Visit evidence missing"),
)

# An action-plan row in one of these statuses is finished; anything else is still owed.
PLAN_DONE_STATUSES = ("done", "complete", "completed", "closed", "cancelled", "canceled")

HOUSE_CONFIG = {
    # SAFE BY DEFAULT: nothing sends on deploy (the mig-905 posture). Both switches are per tenant.
    "enabled": False,
    "channels": ("whatsapp", "email"),
    "lookback_days": 7,          # how far back a sweep looks for submitted visits it has not told anyone about
    "min_items": 1,              # a visit with fewer open items than this is not alerted
    "accessory_enabled": False,
    "accessory_channels": ("whatsapp", "email"),
    "accessory_vendor_id": None,  # the commcalc.po_vendor row the accessory PO is raised against
    "po_mode": "off",            # 'off' | 'draft' — never sends; see PO_MODES
}


def kind_labels():
    """{key: label} for the to-do vocabulary, so no digest spells a kind's name itself. PURE."""
    return {k: lbl for (k, lbl) in KINDS}


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _s(v):
    return "" if v is None else str(v).strip()


def resolve_config(tenant_row=None):
    """The tenant's store-visit alert config over the house defaults, every field validated. An
    invalid value falls back to its default rather than to "never alert" or "alert about
    everything". PURE."""
    from app.modules.commcalc import manager_digest as _md
    row = tenant_row if isinstance(tenant_row, dict) else {}
    out = dict(HOUSE_CONFIG)
    out["enabled"] = bool(row.get("store_visit_alert_enabled", HOUSE_CONFIG["enabled"]))
    out["channels"] = _md.normalize_channels(
        row.get("store_visit_alert_channels") or HOUSE_CONFIG["channels"])
    out["accessory_enabled"] = bool(
        row.get("store_visit_accessory_alert_enabled", HOUSE_CONFIG["accessory_enabled"]))
    out["accessory_channels"] = _md.normalize_channels(
        row.get("store_visit_accessory_channels") or HOUSE_CONFIG["accessory_channels"])
    for key, col, lo, hi in (("lookback_days", "store_visit_alert_lookback_days", 1, 90),
                             ("min_items", "store_visit_alert_min_items", 1, 1000)):
        try:
            v = int(row.get(col))
        except (TypeError, ValueError):
            v = None
        out[key] = HOUSE_CONFIG[key] if v is None or v < lo else min(v, hi)
    vid = _s(row.get("store_visit_accessory_vendor_id"))
    out["accessory_vendor_id"] = vid or None
    mode = _s(row.get("store_visit_accessory_po_mode")).lower()
    out["po_mode"] = mode if mode in PO_MODES else HOUSE_CONFIG["po_mode"]
    # A draft PO with no vendor to raise it against is not a draft, it is a failure waiting to
    # happen at send time. Resolving it to 'off' here is the honest answer, and the caller reports
    # the reason rather than erroring on every sweep tick.
    if out["po_mode"] == "draft" and not out["accessory_vendor_id"]:
        out["po_mode"] = "off"
        out["po_mode_reason"] = "no accessory vendor is configured for this tenant"
    return out


def overdue(due_date, as_of):
    """Is a dated step past its due date? An unreadable or absent date is NOT overdue — a step we
    cannot date must not be reported as late. PURE."""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(_s(due_date)[:10])
        a = _dt.date.fromisoformat(_s(as_of)[:10])
    except (TypeError, ValueError):
        return False
    return d < a


def todo_items(visit, responses=None, action_items=None, plan=None, as_of=None):
    """PURE. What ONE visit left to be done, as a flat list of items.

    `visit` is the `storeops.store_visits` row; the other three are its own child rows
    (`store_visit_responses`, `visit_action_items`, `visit_action_plan`). Each item is
    {store_code, visit_id, kind, item_key, title, detail, due_date, is_overdue, rep} where
    `item_key` is stable for this (visit, thing) so the dedup trail does not re-announce it.

    WHAT COUNTS AS OPEN, stated once:
      checklist     a recorded answer that is not a pass. `checked` is the visit's own field; None
                    (asked, not answered) counts as open, because an unanswered check is not a pass.
      action_item   an item the DM's overlay carries with `discussed` falsy — it was raised and not
                    talked about.
      action_plan   a step whose status is not one of PLAN_DONE_STATUSES. Its due date rides along
                    and `is_overdue` is computed, never assumed.
      rep_coverage  the scheduled rep is not the actual rep AND no reason was recorded. A recorded
                    reason is an answer, so it is not open work.
      evidence      the visit header has no clean-store photo. One item, not a list of every photo.
    """
    v = visit if isinstance(visit, dict) else {}
    vid = _s(v.get("id"))
    store = _s(v.get("store_code"))
    as_of = _s(as_of) or _s(v.get("submitted_at")) or _s(v.get("check_in_at"))
    out = []

    def _add(kind, key, title, detail=None, due_date=None, rep=None):
        out.append({"store_code": store, "visit_id": vid, "kind": kind,
                    "item_key": "{0}:{1}".format(kind, key), "title": title or kind,
                    "detail": detail or None, "due_date": due_date or None,
                    "is_overdue": overdue(due_date, as_of) if due_date else False,
                    "rep": rep or None})

    for r in (responses or []):
        if not isinstance(r, dict):
            continue
        if r.get("checked") is True:
            continue
        key = _s(r.get("item_key")) or _s(r.get("id"))
        if not key:
            continue
        _add("checklist", key,
             _s(r.get("label_snapshot")) or _s(r.get("label")) or key,
             _s(r.get("note")) or None)

    for it in (action_items or []):
        if not isinstance(it, dict):
            continue
        if it.get("discussed"):
            continue
        key = _s(it.get("item_key")) or _s(it.get("id"))
        if not key:
            continue
        _add("action_item", key, _s(it.get("title")) or key,
             _s(it.get("detail")) or _s(it.get("comment")) or None, rep=_s(it.get("rep")) or None)

    for p in (plan or []):
        if not isinstance(p, dict):
            continue
        if _s(p.get("status")).lower() in PLAN_DONE_STATUSES:
            continue
        key = _s(p.get("id")) or _s(p.get("description"))[:60]
        if not key:
            continue
        _add("action_plan", key, _s(p.get("description")) or key, None,
             due_date=_s(p.get("due_date")) or None, rep=_s(p.get("rep")) or None)

    sched, actual = _s(v.get("scheduled_rep")), _s(v.get("actual_rep"))
    if sched and actual and sched.lower() != actual.lower() and not _s(v.get("rep_discrepancy_reason")):
        _add("rep_coverage", "scheduled_vs_actual",
             "Scheduled {0}, worked by {1}".format(sched, actual),
             "No reason was recorded for the change.", rep=actual)

    if not _s(v.get("clean_store_photo_path")):
        _add("evidence", "clean_store", "No clean-store photo was captured on this visit")

    return out


def summarize(items, as_of=None):
    """PURE. One tenant's open visit work rolled up: per store, per kind, and the totals a digest
    leads with. `oldest_due` is the earliest due date among overdue steps, or None — never a date
    invented for a step that carries none."""
    by_store, by_kind = {}, {}
    overdue_n = 0
    oldest_due = None
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        st = _s(it.get("store_code")) or "—"
        by_store[st] = by_store.get(st, 0) + 1
        k = _s(it.get("kind"))
        by_kind[k] = by_kind.get(k, 0) + 1
        if it.get("is_overdue"):
            overdue_n += 1
            d = _s(it.get("due_date"))[:10]
            if d and (oldest_due is None or d < oldest_due):
                oldest_due = d
    return {"by_store": by_store, "by_kind": by_kind,
            "totals": {"open": sum(by_store.values()), "overdue": overdue_n,
                       "stores": len(by_store), "visits": len({_s(i.get("visit_id"))
                                                               for i in (items or [])
                                                               if isinstance(i, dict)}),
                       "oldest_due": oldest_due}}


def key_parts(item):
    """The dedup tail for a to-do item: the visit and the thing inside it. NOT the date — a visit is
    an event, so its to-do list is announced once and re-announced only for work that is new. PURE."""
    it = item or {}
    return (it.get("visit_id"), it.get("item_key"))


def accessory_key_parts(line):
    """The dedup tail for an accessory line: the visit, the item and the QUANTITY. A quantity change
    is a different order, so it is news; the same line at the same quantity is not. PURE."""
    ln = line or {}
    return (ln.get("visit_id"), ln.get("key"), ln.get("qty"))


# ── ACCESSORIES ─────────────────────────────────────────────────────────────────────────────────
def accessory_lines(rows, visits_by_id=None):
    """PURE. `storeops.store_visit_accessories` rows -> order lines, worst spelling tolerated.

    Quantities for the SAME item at the SAME store are added together, because two reps asking for
    five of a thing on two visits is one order for ten, and a PO with the same line twice is a PO a
    vendor queries. `key` is the merge identity (case- and space-folded name) and `name` keeps the
    first spelling seen, so the order reads the way a human wrote it.

    Returns lines sorted by store then name: [{store_code, visit_id, key, name, qty, notes, visits}].
    """
    vmap = visits_by_id if isinstance(visits_by_id, dict) else {}
    merged = {}
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        name = _s(r.get("accessory_name"))
        if not name:
            continue
        vid = _s(r.get("visit_id"))
        store = _s(r.get("store_code")) or _s((vmap.get(vid) or {}).get("store_code"))
        key = " ".join(name.lower().split())
        qty = _i(r.get("qty")) or 1
        slot = merged.get((store, key))
        if slot is None:
            slot = {"store_code": store, "visit_id": vid, "key": key, "name": name,
                    "qty": 0, "notes": [], "visits": []}
            merged[(store, key)] = slot
        slot["qty"] += max(1, qty)
        note = _s(r.get("note"))
        if note and note not in slot["notes"]:
            slot["notes"].append(note)
        if vid and vid not in slot["visits"]:
            slot["visits"].append(vid)
    out = list(merged.values())
    for ln in out:
        ln["notes"] = "; ".join(ln["notes"]) or None
    out.sort(key=lambda l: (l["store_code"] or "", l["name"].lower()))
    return out


def po_draft(lines, vendor_id, vendor_name=None, unit_costs=None, ship_to_store=None, market=None):
    """PURE. The accessory lines as ONE purchase-order draft, in the shape
    `supply/store.create_order` already consumes (mig-301 PO + lines). NOTHING IS SENT.

    `unit_costs` is {merge key: unit cost} from whatever price the tenant actually has — a vendor
    catalog row, a negotiated list. A line with NO known cost is kept at 0.00 and NAMED in
    `unpriced`: an accessory nobody has a price for still has to be ordered, and dropping it so the
    total looks complete would be the order lying about what was asked for. The total is therefore
    a FLOOR when `unpriced` is non-empty, and the caller says so.
    """
    costs = unit_costs if isinstance(unit_costs, dict) else {}
    po_lines, unpriced = [], []
    subtotal = 0.0
    for i, ln in enumerate(lines or []):
        qty = max(1, _i(ln.get("qty")) or 1)
        raw = costs.get(ln.get("key"))
        try:
            unit = round(float(raw), 2) if raw is not None else None
        except (TypeError, ValueError):
            unit = None
        if unit is None:
            unpriced.append(ln.get("name"))
            unit = 0.0
        ext = round(unit * qty, 2)
        subtotal += ext
        note = "; ".join([n for n in (ln.get("notes"), ln.get("store_code")) if n]) or None
        po_lines.append({"line_no": i + 1, "sku": None,
                         "device_model": _s(ln.get("name"))[:300] or "Accessory",
                         "qty_ordered": qty, "unit_cost": unit, "extended_cost": ext,
                         "store": ln.get("store_code") or None, "notes": note})
    subtotal = round(subtotal, 2)
    return {"vendor_id": vendor_id, "vendor_name": vendor_name, "lines": po_lines,
            "subtotal": subtotal, "shipping_estimate": 0.0, "total": subtotal,
            "ship_to_store": ship_to_store, "market": market,
            "unpriced": unpriced, "total_is_floor": bool(unpriced),
            "meta": {"source": PO_SOURCE,
                     # EVERY visit that contributed, not just the first one on each merged line:
                     # two visits asking for the same accessory merged into one order line, and an
                     # order that named only one of them would lose who asked.
                     "visits": sorted({v for l in (lines or [])
                                       for v in ([_s(l.get("visit_id"))]
                                                 + [_s(x) for x in (l.get("visits") or [])])
                                       if v}),
                     "unpriced": unpriced}}


# ── DIGESTS ─────────────────────────────────────────────────────────────────────────────────────
def _esc(v):
    """Minimal HTML escaping. A rep's name, a store address or a free-text note typed by a DM goes
    into an email body, and `&`, `<` or `>` in any of them must not become markup. PURE."""
    return (_s(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_digest(name, items, totals=None, labels=None, link=None):
    """ONE recipient's store-visit to-do digest: {"subject", "html", "text"}. The text body is the
    WhatsApp message — short, because it is read on a phone — carrying the same facts in the same
    order as the email, because two renderings of one digest that disagree is a digest nobody
    trusts. PURE."""
    labels = labels or kind_labels()
    totals = totals or {}
    items = [it for it in (items or []) if isinstance(it, dict)]
    over = [it for it in items if it.get("is_overdue")]
    stores = sorted({_s(it.get("store_code")) for it in items})
    n = len(items)

    subject = "Store visit follow-up: {n} item{s} to do across {k} store{ks}{o}".format(
        n=n, s="" if n == 1 else "s", k=len(stores), ks="" if len(stores) == 1 else "s",
        o="" if not over else " — {0} past due".format(len(over)))

    rows = "".join(
        "<tr><td>{st}</td><td>{k}</td><td>{t}</td><td>{d}</td></tr>".format(
            st=_esc(it.get("store_code")), k=_esc(labels.get(_s(it.get("kind")), it.get("kind"))),
            t=_esc(it.get("title")),
            d=("<b>{0} (past due)</b>".format(_esc(it.get("due_date"))) if it.get("is_overdue")
               else _esc(it.get("due_date")))) for it in items)
    foot = []
    unattr = _i(totals.get("no_store"))
    if unattr:
        # The ownership footer, the §15z rule applied to visits: work recorded against no store
        # cannot be followed up with any manager, and saying so is the point.
        foot.append("<p><i>{0} open item{1} no store, so {2} cannot be assigned to a manager and "
                    "{2} not counted above.</i></p>".format(
                        unattr, " carries" if unattr == 1 else "s carry",
                        "it is" if unattr == 1 else "they are"))
    if link:
        foot.append('<p><a href="{0}">Open the store-visit board</a></p>'.format(_esc(link)))
    html = (
        "<p>Hello {name},</p>"
        "<p>{n} item{s} from recent store visits still need{v} doing across {k} store{ks}.{o}</p>"
        "<table border=1 cellpadding=6 cellspacing=0>"
        "<tr><th>Store</th><th>What</th><th>Item</th><th>Due</th></tr>{rows}</table>{foot}"
    ).format(name=_esc(name) or "there", n=n, s="" if n == 1 else "s",
             v="s" if n == 1 else "", k=len(stores), ks="" if len(stores) == 1 else "s",
             o="" if not over else " <b>{0} of them are past their due date.</b>".format(len(over)),
             rows=rows, foot="".join(foot))

    shown = (over or items)[:8]
    lines = ["{0} — {1}: {2}{3}".format(
        _s(it.get("store_code")), labels.get(_s(it.get("kind")), it.get("kind")),
        _s(it.get("title")), " (PAST DUE {0})".format(_s(it.get("due_date")))
        if it.get("is_overdue") else "") for it in shown]
    tail = []
    if len(over or items) > 8:
        tail.append("and {0} more.".format(len(over or items) - 8))
    if link:
        tail.append(link)
    text = "Store visit follow-up — {n} item{s} to do{o}.\n{rows}\n{tail}".format(
        n=n, s="" if n == 1 else "s",
        o="" if not over else ", {0} past due".format(len(over)),
        rows="\n".join(lines), tail=" ".join(tail)).strip()
    return {"subject": subject, "html": html, "text": text}


def build_accessory_digest(name, lines, vendor_name=None, link=None, po=None):
    """ONE recipient's accessory digest — the separate notification the owner asked for:
    {"subject", "html", "text"}. It says what was asked for, by which store, and what the draft
    purchase order is; it never claims an order was placed, because this module never places one.
    PURE."""
    lines = [l for l in (lines or []) if isinstance(l, dict)]
    units = sum(max(1, _i(l.get("qty")) or 1) for l in lines)
    stores = sorted({_s(l.get("store_code")) for l in lines})
    subject = "Accessories to order: {n} item{s}, {u} unit{us} across {k} store{ks}".format(
        n=len(lines), s="" if len(lines) == 1 else "s", u=units,
        us="" if units == 1 else "s", k=len(stores), ks="" if len(stores) == 1 else "s")
    rows = "".join("<tr><td>{st}</td><td>{nm}</td><td align=right>{q}</td><td>{nt}</td></tr>".format(
        st=_esc(l.get("store_code")), nm=_esc(l.get("name")),
        q=max(1, _i(l.get("qty")) or 1), nt=_esc(l.get("notes"))) for l in lines)
    foot = []
    if po:
        unpriced = po.get("unpriced") or []
        foot.append("<p>Draft purchase order {0}{1}: <b>{2}</b>{3}. "
                    "It is a DRAFT — nothing has been sent to the supplier.</p>".format(
                        _esc(po.get("po_number") or ""),
                        " for {0}".format(_esc(vendor_name)) if vendor_name else "",
                        "${0:,.2f}".format(float(po.get("total") or 0)),
                        " (a floor — {0} line{1} no price on file)".format(
                            len(unpriced), " has" if len(unpriced) == 1 else "s have")
                        if unpriced else ""))
    elif vendor_name:
        foot.append("<p>Supplier on file: {0}. No draft purchase order was raised.</p>".format(
            _esc(vendor_name)))
    if link:
        foot.append('<p><a href="{0}">Open the accessory list</a></p>'.format(_esc(link)))
    html = ("<p>Hello {name},</p><p>Recent store visits asked for {n} accessory line{s} "
            "({u} unit{us}).</p>"
            "<table border=1 cellpadding=6 cellspacing=0>"
            "<tr><th>Store</th><th>Accessory</th><th>Qty</th><th>Note</th></tr>{rows}</table>{foot}"
            ).format(name=_esc(name) or "there", n=len(lines),
                     s="" if len(lines) == 1 else "s", u=units, us="" if units == 1 else "s",
                     rows=rows, foot="".join(foot))
    shown = lines[:10]
    body = ["{0} — {1} x{2}".format(_s(l.get("store_code")), _s(l.get("name")),
                                    max(1, _i(l.get("qty")) or 1)) for l in shown]
    tail = []
    if len(lines) > 10:
        tail.append("and {0} more line{1}.".format(len(lines) - 10,
                                                   "" if len(lines) - 10 == 1 else "s"))
    if po:
        tail.append("Draft PO {0} ${1:,.2f} — not sent.".format(
            _s(po.get("po_number")), float(po.get("total") or 0)))
    if link:
        tail.append(link)
    text = "Accessories to order — {n} line{s}, {u} unit{us}.\n{rows}\n{tail}".format(
        n=len(lines), s="" if len(lines) == 1 else "s", u=units, us="" if units == 1 else "s",
        rows="\n".join(body), tail=" ".join(tail)).strip()
    return {"subject": subject, "html": html, "text": text}
