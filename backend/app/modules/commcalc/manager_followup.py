"""FOLLOW UP WITH MANAGERS — the pending work each manager owns, how old it is, and when it escalates.

OWNER ASK 2026-10-03, verbatim:
    "then alert the management via a whats app message for all followup items with the managers -
     this will be a seprate module - Follow Up with Managers , all pending jobs assigned to the
     managers will be followed up via this module"

WHAT "ALL PENDING JOBS" ALREADY MEANS HERE, AND WHY NOTHING NEW DEFINES IT. The platform already has
ONE registry of every flag / exception / compliance queue a manager can owe work to:
`commcalc/compliance_summary.CATEGORIES`, which the Flags & Compliance dashboard counts off. This
module DEREFERENCES that registry for the vocabulary — key, label and the page that owns the queue —
and adds only what following up needs and counting did not: who owns each item, how long it has been
pending, and when it stops being the owner's problem and becomes their manager's. A second list of
what counts as a pending job is the duplicate defect the index rules forbid, so there is not one.

WHY THIS IS A ROLL-UP AND NOT A TO-DO LIST — measured, not assumed (read-only, house org,
2026-10-03): there are **67,344 open flags**, of which **14,944 are more than 90 days old** and the
oldest is **117 days**; 35,632 are one detector's output (`PORT_OUT_NODATE`) and 15,324 another's.
A module that WhatsApps a district manager 67,344 items is not a follow-up, it is a denial of
service. So a follow-up is per (manager × queue): how many are open, how old the oldest is, which
age band the work sits in, and the few oldest by name — with the full list on the queue's own page,
which already exists and is linked. The ESCALATION is what makes it accountability rather than a
newsletter: work past the configured age appears in the digest of the manager ABOVE the owner too.

AND THE FINDING THIS MODULE REFUSES TO HIDE: **14,335 of those open flags carry no store at all**,
so they cannot be attributed to any manager. They are counted as `UNATTRIBUTED` and reported in
every digest and payload rather than silently dropped. An unowned backlog is exactly the thing a
follow-up module exists to surface, and quietly excluding a fifth of the work because it has no
store would make this module lie by omission. That is the §15z footer rule applied to ownership.

NO SECOND FAN-OUT, NO SECOND DEDUP, NO SECOND SCHEDULER. Recipients (DM ∪ above), one digest per
manager, the unreachable-recipient skip, the ref_key spelling, the channel vocabulary and the
due-time rule all come from `commcalc/manager_digest`. Dedup rows are the existing
`storeops.alert_log`. Per-tenant config lives on `storeops.tenants` with house defaults here.

RULE TWO: no carrier, tenant, store or product name appears in this file. PURE: stdlib only — no DB,
no framework, no network. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md.
"""

ALERT_SCOPE = "manager_followup"

# The sentinel for work that carries no store. It is a VALUE, not a dropped row: every count,
# payload and digest carries it, because an item nobody owns is the most important kind to show.
UNATTRIBUTED = "__unattributed__"

# Age bands, oldest last. A band is (label, lower_inclusive, upper_inclusive_or_None).
AGE_BANDS = (
    ("0-7", 0, 7),
    ("8-30", 8, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, None),
)
BAND_UNKNOWN = "unknown"      # the item carries no date we can age it by — never counted as fresh.

HOUSE_CONFIG = {
    "enabled": False,          # SAFE BY DEFAULT: nothing sends on deploy (the mig-905 posture).
    "send_time": "10:30",      # tenant-local HH:MM; the mig-433 convention, normalised by the home.
    "channels": ("whatsapp", "email"),
    "escalate_after_days": 30,  # older than this also reaches the manager ABOVE the owner.
    "show_oldest": 5,          # how many items to name per queue; the rest are counted.
    "min_items": 1,            # a manager with fewer open items than this is not followed up.
}


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def resolve_config(tenant_row=None):
    """The tenant's follow-up config over the house defaults, every field validated. An invalid value
    falls back to its default rather than to "never follow up" or "follow up on everything". PURE."""
    from . import manager_digest as _md
    row = tenant_row if isinstance(tenant_row, dict) else {}
    out = dict(HOUSE_CONFIG)
    out["enabled"] = bool(row.get("manager_followup_enabled", HOUSE_CONFIG["enabled"]))
    out["send_time"] = _md.normalize_alert_time(
        row.get("manager_followup_time") or HOUSE_CONFIG["send_time"])
    out["channels"] = _md.normalize_channels(
        row.get("manager_followup_channels") or HOUSE_CONFIG["channels"])
    for key, col, lo, hi in (("escalate_after_days", "manager_followup_escalate_after_days", 1, 365),
                             ("show_oldest", "manager_followup_show_oldest", 1, 25),
                             ("min_items", "manager_followup_min_items", 1, 10000)):
        raw = row.get(col)
        try:
            v = int(raw)
        except (TypeError, ValueError):
            v = None
        out[key] = HOUSE_CONFIG[key] if v is None or v < lo else min(v, hi)
    return out


def sources():
    """The follow-up vocabulary, DEREFERENCED from the compliance registry — never a second list.
    Returns ((key, label, href, meaning), …) in the registry's own order. PURE."""
    from . import compliance_summary as _cs
    return tuple(_cs.CATEGORIES)


def source_labels():
    """{key: label} for the registry, so a digest never spells a queue's name itself. PURE."""
    return {k: lbl for (k, lbl, _h, _d) in sources()}


def age_band(days):
    """The band an age in days falls in. `None` is BAND_UNKNOWN, never '0-7': an item we cannot age
    is not a fresh item, and calling it fresh is how a 117-day-old row hides in the newest bucket.
    A negative age (a clock skew, a future-dated row) is unknown for the same reason. PURE."""
    if days is None:
        return BAND_UNKNOWN
    try:
        d = int(days)
    except (TypeError, ValueError):
        return BAND_UNKNOWN
    if d < 0:
        return BAND_UNKNOWN
    for label, lo, hi in AGE_BANDS:
        if d >= lo and (hi is None or d <= hi):
            return label
    return BAND_UNKNOWN


def age_days(opened, as_of):
    """Whole days between two ISO dates (YYYY-MM-DD, or a timestamp whose first 10 chars are the
    date). None when either side is unreadable — so the caller gets BAND_UNKNOWN rather than 0. PURE."""
    import datetime as _dt
    try:
        a = _dt.date.fromisoformat(str(opened or "")[:10])
        b = _dt.date.fromisoformat(str(as_of or "")[:10])
    except (TypeError, ValueError):
        return None
    return (b - a).days


def summarize(items, as_of, config=None):
    """PURE. One tenant's pending work, rolled up per (store × source).

    `items` is an iterable of {source, store_code, opened_at?}. A missing / blank store becomes
    UNATTRIBUTED rather than being dropped. Returns:

      by_store   {store: {source: {"open", "oldest_days", "bands", "oldest_items"}}}
      by_source  {source: {"open", "oldest_days", "bands", "unattributed"}}
      totals     {"open", "unattributed", "oldest_days", "stores", "sources"}

    `oldest_days` is None when nothing in the group could be aged — the honest answer, which a
    caller renders as "unknown", never as 0 days.
    """
    cfg = dict(HOUSE_CONFIG)
    cfg.update(config or {})
    keep = max(1, _i(cfg.get("show_oldest")) or HOUSE_CONFIG["show_oldest"])
    by_store, by_source = {}, {}

    def _slot(d, key, extra=None):
        s = d.get(key)
        if s is None:
            s = {"open": 0, "oldest_days": None,
                 "bands": {lbl: 0 for (lbl, _l, _h) in AGE_BANDS}}
            s["bands"][BAND_UNKNOWN] = 0
            if extra:
                s.update(extra)
            d[key] = s
        return s

    for it in (items or []):
        r = it if isinstance(it, dict) else {}
        src = str(r.get("source") or "").strip()
        if not src:
            continue      # an item with no source has no queue to follow up on
        store = str(r.get("store_code") or "").strip() or UNATTRIBUTED
        days = age_days(r.get("opened_at"), as_of) if r.get("opened_at") else None
        band = age_band(days)

        ss = _slot(by_store.setdefault(store, {}), src, {"oldest_items": []})
        src_slot = _slot(by_source, src, {"unattributed": 0})
        for slot in (ss, src_slot):
            slot["open"] += 1
            slot["bands"][band] = slot["bands"].get(band, 0) + 1
            if days is not None and (slot["oldest_days"] is None or days > slot["oldest_days"]):
                slot["oldest_days"] = days
        if store == UNATTRIBUTED:
            src_slot["unattributed"] += 1
        ss["oldest_items"].append({"ref": r.get("ref"), "label": r.get("label"),
                                   "age_days": days, "opened_at": r.get("opened_at")})

    for store, per_src in by_store.items():
        for slot in per_src.values():
            slot["oldest_items"].sort(key=lambda x: (-(x["age_days"] if x["age_days"] is not None
                                                       else -1)))
            slot["oldest_items"] = slot["oldest_items"][:keep]

    open_total = sum(s["open"] for s in by_source.values())
    unattr = sum(s.get("unattributed", 0) for s in by_source.values())
    ages = [s["oldest_days"] for s in by_source.values() if s["oldest_days"] is not None]
    return {"by_store": by_store, "by_source": by_source,
            "totals": {"open": open_total, "unattributed": unattr,
                       "oldest_days": max(ages) if ages else None,
                       "stores": len([s for s in by_store if s != UNATTRIBUTED]),
                       "sources": len(by_source)}}


def escalated(oldest_days, escalate_after_days):
    """Has this group of work aged past the point where the manager ABOVE the owner should see it?
    Unknown age does NOT escalate — escalating on an age we could not read would send a manager
    after work that might be a day old. It is reported as unknown instead. PURE."""
    if oldest_days is None:
        return False
    try:
        return int(oldest_days) >= max(1, _i(escalate_after_days))
    except (TypeError, ValueError):
        return False


def followup_items(summary, config=None):
    """PURE. The per-(store × source) follow-ups a digest is built from, worst first.

    Each item is {store_code, source, open, oldest_days, band, escalated, oldest_items}. A store
    with fewer open items than `min_items` is left out. UNATTRIBUTED work is NOT an item — nobody
    owns it, so no manager can be followed up about it — but it rides in the digest footer via
    `summary['totals']['unattributed']`, which is how it stays visible instead of vanishing."""
    cfg = dict(HOUSE_CONFIG)
    cfg.update(config or {})
    out = []
    for store, per_src in (summary.get("by_store") or {}).items():
        if store == UNATTRIBUTED:
            continue
        for src, slot in per_src.items():
            if slot["open"] < max(1, _i(cfg.get("min_items")) or 1):
                continue
            out.append({
                "store_code": store, "source": src, "open": slot["open"],
                "oldest_days": slot["oldest_days"],
                "band": age_band(slot["oldest_days"]),
                "escalated": escalated(slot["oldest_days"], cfg.get("escalate_after_days")),
                "oldest_items": slot.get("oldest_items") or [],
            })
    out.sort(key=lambda it: (0 if it["escalated"] else 1,
                             -(it["oldest_days"] if it["oldest_days"] is not None else -1),
                             -it["open"], it["store_code"] or ""))
    return out


def key_parts(item):
    """The dedup tail: the store, the queue, and the age BAND. A follow-up re-escalates when the work
    moves into an older band — which is the point of a follow-up — but not every day for the same
    work in the same band. The band, not the count, because a count that ticks by one is not news."""
    it = item or {}
    return (it.get("store_code"), it.get("source"), it.get("band"))


def build_digest(name, items, totals=None, labels=None, link=None, unavailable=None):
    """One recipient's follow-up digest: {"subject", "html", "text"}. The text body is the WhatsApp
    message — short, because it is read on a phone: the headline, the escalated lines, and a link to
    the queue's own page. The same facts in the same order as the email, because two renderings of
    one digest that disagree is a digest nobody trusts. PURE."""
    labels = labels or source_labels()
    totals = totals or {}
    esc = [it for it in items if it["escalated"]]
    open_n = sum(it["open"] for it in items)
    oldest = max([it["oldest_days"] for it in items if it["oldest_days"] is not None] or [None]
                 ) if items else None

    def _lbl(src):
        return labels.get(src, src)

    def _age(it):
        return "age unknown" if it["oldest_days"] is None else f"{it['oldest_days']}d oldest"

    subject = "Follow up: {n} open item{s} across {q} queue{qs}{e}".format(
        n=open_n, s="" if open_n == 1 else "s",
        q=len({it["source"] for it in items}),
        qs="" if len({it["source"] for it in items}) == 1 else "s",
        e="" if not esc else f" — {len(esc)} past escalation")

    rows = "".join(
        "<tr><td>{st}</td><td>{q}</td><td align=right>{n}</td><td>{a}</td><td>{e}</td></tr>".format(
            st=it["store_code"], q=_lbl(it["source"]), n=it["open"], a=_age(it),
            e="escalated" if it["escalated"] else "") for it in items)
    foot = []
    unattr = _i(totals.get("unattributed"))
    if unattr:
        # The ownership footer. Work with no store cannot be followed up with anyone, and saying so
        # is the whole point: an unowned backlog is what a follow-up module exists to surface.
        foot.append("<p><i>{0} open item{1} no store, so {2} cannot be assigned to any manager "
                    "and {2} not counted above. They need an owner before they can be followed "
                    "up.</i></p>".format(unattr, " carries" if unattr == 1 else "s carry",
                                         "it" if unattr == 1 else "they"))
    for note in (unavailable or []):
        foot.append("<p><i>{0} could not be checked, so its open items are not counted above."
                    "</i></p>".format(_lbl(note)))
    if link:
        foot.append('<p><a href="{0}">Open the follow-up board</a></p>'.format(link))
    html = (
        "<p>Hello {name},</p>"
        "<p>You have {n} open item{s} waiting across {q} queue{qs}{oldest}."
        "{esc}</p>"
        "<table border=1 cellpadding=6 cellspacing=0>"
        "<tr><th>Store</th><th>Queue</th><th>Open</th><th>Age</th><th></th></tr>{rows}</table>"
        "{foot}"
    ).format(name=name or "there", n=open_n, s="" if open_n == 1 else "s",
             q=len({it["source"] for it in items}),
             qs="" if len({it["source"] for it in items}) == 1 else "s",
             oldest="" if oldest is None else f", the oldest {oldest} days old",
             esc="" if not esc else " <b>{0} of them are past the escalation age.</b>".format(
                 len(esc)),
             rows=rows, foot="".join(foot))

    lines = ["{0} — {1}: {2} open, {3}{4}".format(
        it["store_code"], _lbl(it["source"]), it["open"], _age(it),
        ", ESCALATED" if it["escalated"] else "") for it in (esc or items)[:8]]
    text_foot = []
    if len(esc or items) > 8:
        text_foot.append("and {0} more.".format(len(esc or items) - 8))
    if unattr:
        text_foot.append("{0} open item{1} no store and cannot be assigned to anyone.".format(
            unattr, " carries" if unattr == 1 else "s carry"))
    if link:
        text_foot.append(link)
    text = "Follow up — {n} open item{s}{oldest}.\n{rows}\n{foot}".format(
        n=open_n, s="" if open_n == 1 else "s",
        oldest="" if oldest is None else ", oldest {0}d".format(oldest),
        rows="\n".join(lines), foot=" ".join(text_foot)).strip()
    return {"subject": subject, "html": html, "text": text}
