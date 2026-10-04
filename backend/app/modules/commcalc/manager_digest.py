"""MANAGER DIGEST FAN-OUT — the ONE home for "who gets alerted about a store, and once per what".

CLAUDE.md, "A fix is a DESIGN fix" / "One fact, one home, dereferenced — never copied": a second
alerting mechanism, a second dedup rule or a second recipient resolution is the duplicate defect the
index rules forbid. Before this file existed, `epay_alerts.plan_emails` held the only implementation
of a pattern that is not about ePay at all:

    per-store items  ->  the DM and every manager above the DM for that store
                     ->  ONE digest per manager (managers with no email skipped)
                     ->  a stable ref_key per (recipient, store, date, kind) so `storeops.alert_log`
                         can escalate a given finding ONCE per day.

That pattern is now HERE, and ePay dereferences it (`epay_alerts.plan_emails` is a thin wrapper that
supplies its own subject/HTML builder). A new alert KIND — the zero-sales alert, `zero_sales.py` —
is a new caller of THIS function, never a second fan-out.

WHAT IS PARAMETERISED, AND WHAT IS NOT
  Parameterised: the alert SCOPE name, which field of an item names the store, which fields make the
  item's identity inside a store-day, and how a digest's subject/HTML read. Those are per-alert.
  NOT parameterised: that recipients are DM ∪ above-DM, that a recipient with no email is skipped,
  that one recipient gets one digest, that an item reachable by two hierarchy paths is listed once,
  and the ref_key SPELLING. Those are the house rule, and a caller cannot vary them.

REF_KEY SPELLING (the dedup convention, one home):

    "<scope>|<today>|<lower(email)>|<part>|<part>|…"

  `scope` is also the `storeops.alert_log.scope` value the caller writes, so the row and the key
  agree by construction. The caller's `key_parts` supplies the per-alert tail (store, date, kind).

RULE TWO: no carrier, tenant, store or product name appears here. Pure stdlib — no DB, no framework,
no network — so `backend/harness_manager_digest.py` and the callers' own harnesses prove it DB-free.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §15.
"""


def ref_key(scope, today, email, *parts):
    """THE dedup key spelling, for every alert kind. `parts` is the per-alert tail — typically the
    store, the date the finding is about, and the kind. Values are stringified as-is (None -> '');
    the email is lower-cased and trimmed so a recipient spelled two ways dedups to one key."""
    tail = "|".join("" if p is None else str(p) for p in parts)
    return "{s}|{d}|{e}|{t}".format(
        s=str(scope or "").strip(), d=str(today or ""),
        e=str(email or "").strip().lower(), t=tail)


def recipients_for(hierarchy):
    """The house recipient rule for ONE store: the District Manager(s) AND every manager ABOVE the
    DM. `hierarchy` is what `storeops.router._managers_above_dm` returns —
    {"dm": [{name, email, …}], "above": [{name, email, …}]}. A malformed/absent entry yields no
    recipients (an alert nobody can receive is silently dropped, never raised: an alerting bug must
    not take the sweep down)."""
    h = hierarchy if isinstance(hierarchy, dict) else {}
    return list(h.get("dm") or []) + list(h.get("above") or [])


# ── IS A TENANT'S DAILY ALERT DUE ON THIS TICK? ─────────────────────────────────────────────────
# ONE home for the mig-433 convention, which was spelled inline at each sweep: a tenant configures a
# tenant-local HH:MM and an HOURLY pg_cron tick fires every sweep, so each sweep must decide for
# itself whether the configured minute has arrived. Three sweeps spelling `now.strftime("%H:%M") <
# send_time` is three chances for one of them to drift on the edge case (a blank setting, a 24:00, a
# '9:30' with no leading zero) and send a digest an hour early or not at all.
DEFAULT_ALERT_TIME = "10:30"


def normalize_alert_time(value):
    """A tenant's configured HH:MM, normalised so a comparison is sound. Accepts 'H:MM', 'HH:MM' and
    'HH:MM:SS'; anything unparseable or out of range resolves to the house default rather than
    refusing to alert at all (an alert that silently never fires is the failure this guards). PURE."""
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_ALERT_TIME
    parts = raw.split(":")
    if len(parts) < 2:
        return DEFAULT_ALERT_TIME
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return DEFAULT_ALERT_TIME
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return DEFAULT_ALERT_TIME
    return "{:02d}:{:02d}".format(hh, mm)


def due_now(now_local_hhmm, send_time):
    """Has the tenant's configured send time arrived in its OWN local day? `now_local_hhmm` is the
    tenant-local time as 'HH:MM'. True from that minute until midnight, which is what makes an hourly
    tick safe: the first tick at or after the minute sends, and `alert_log` dedup stops every later
    tick that day from sending again. A malformed `now` is NOT due -- a clock we cannot read must not
    trigger a fan-out. PURE."""
    now = str(now_local_hhmm or "").strip()
    if len(now) < 5 or now[2] != ":":
        return False
    try:
        int(now[:2]), int(now[3:5])
    except (TypeError, ValueError):
        return False
    return now[:5] >= normalize_alert_time(send_time)


# ── WHICH CHANNELS CAN REACH A RECIPIENT ────────────────────────────────────────────────────────
# The house rule used to be stated as "a manager with no email is skipped -- the house rule, not a
# knob", which was right while email was the only channel. The owner asked for WhatsApp as well
# (2026-10-03), and the honest generalisation is: a recipient is reachable on a channel when they
# have an ADDRESS for that channel, and is skipped only when no requested channel can reach them.
# That is still not a knob -- a caller chooses which channels to request, never who gets skipped.
CHANNEL_ADDRESS_FIELD = {"email": "email", "whatsapp": "phone"}
DEFAULT_CHANNELS = ("email",)


def normalize_channels(channels):
    """The requested channels, in a stable order, unknown names dropped. Empty/garbage resolves to
    the house default (email), so no caller can accidentally request nothing. PURE."""
    if isinstance(channels, str):
        channels = [channels]
    if not isinstance(channels, (list, tuple, set)):
        return DEFAULT_CHANNELS
    want = [str(c).strip().lower() for c in channels]
    keep = tuple(c for c in ("email", "whatsapp") if c in want)
    return keep or DEFAULT_CHANNELS


# ── A TENANT'S OWN NOTIFICATION LIST, ON TOP OF THE HOUSE DEFAULT ───────────────────────────────
# The house default is DM ∪ above-DM, resolved from the org tree (`recipients_for`). A tenant may
# also keep a NAMED list — "these people hear about this scope" — and that list already has ONE home
# and one editor: `storeops.alert_recipient` rows (mig 089), with `scope`, `email`, `whatsapp`,
# `via_email`, `via_whatsapp` and `include_dm`, edited on the Cash & Closing Alerts page.
#
# Before this, that list was read in exactly one place — `closing/router._alert_recipients` — which
# ALSO carried its own sending, its own dedup and a DM-only (not DM-∪-above) hierarchy fallback. A
# new alert kind that wanted "the default recipients PLUS this tenant's named list" therefore had a
# choice between two half-mechanisms. Neither is duplicated here: the ROWS keep their one home and
# their one editor, and this function is the one place that turns them into recipients in the shape
# `plan_digests` already fans out to. `closing/_alert_recipients` is the remaining sibling reader —
# it is NOT re-pointed here in this change because it also owns the legacy per-row via_email /
# via_whatsapp send and the org-admin last resort; it reads the same rows, so a tenant edits one list.
def use_hierarchy(rows):
    """Should the ORG-TREE default (DM ∪ above) be used for this scope? Yes when the tenant has
    configured no named list at all (the house default is not something you can lose by forgetting
    to configure anything), and yes when any configured row asks to keep the DM in. A tenant that
    has a list and clears `include_dm` on all of it has SAID "just these people". PURE."""
    rs = [r for r in (rows or []) if isinstance(r, dict)]
    if not rs:
        return True
    return any(bool(r.get("include_dm")) for r in rs)


def named_extras(rows, scope, channels=DEFAULT_CHANNELS):
    """PURE. `storeops.alert_recipient` rows -> recipients in `recipients_for`'s shape, so a named
    recipient and a resolved manager are the same kind of thing downstream.

    A row counts for `scope` when its own scope is that scope or the catch-all 'all' — the same rule
    the rows' existing reader uses, so one list serves both. `via_email` / `via_whatsapp` are
    honoured: an address the tenant has switched off for a channel is not an address on that channel.
    The row's `whatsapp` column is mapped to `phone`, which is the field name the channel vocabulary
    (CHANNEL_ADDRESS_FIELD) already uses, so no caller learns a second spelling."""
    want = normalize_channels(channels)
    want_scope = {str(scope or "").strip().lower(), "all"}
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("scope") or "").strip().lower() not in want_scope:
            continue
        email = str(r.get("email") or "").strip()
        phone = str(r.get("whatsapp") or "").strip()
        if r.get("via_email") is False:
            email = ""
        if phone and r.get("via_whatsapp") is False:
            phone = ""
        m = {"name": r.get("name") or email or phone, "email": email, "phone": phone,
             "named": True}
        if addresses_for(m, want):
            out.append(m)
    return out


def addresses_for(manager, channels=DEFAULT_CHANNELS):
    """{channel: address} for ONE manager, holding only the channels they actually have an address
    for. A recipient with no address on any requested channel yields {} and the caller skips them --
    the same house rule as before, now stated per channel instead of per email. PURE."""
    m = manager if isinstance(manager, dict) else {}
    out = {}
    for ch in normalize_channels(channels):
        addr = str(m.get(CHANNEL_ADDRESS_FIELD[ch]) or "").strip()
        if addr:
            out[ch] = addr
    return out


def plan_digests(items, hierarchy_by_store, today, *, scope, build, key_parts,
                 store_of=lambda it: it.get("store_code"), kind=None,
                 channels=DEFAULT_CHANNELS, extra_recipients=(), use_tree=True):
    """PURE. Decide the emails to send for ONE tenant, for ONE alert scope.

      items                a flat list of per-store findings (any shape the caller's `build` reads).
      hierarchy_by_store   {store: {"dm": […], "above": […]}} — already resolved by the caller.
      today                the tenant-local date string the dedup is keyed on.
      scope                the alert scope, e.g. 'epay_discrepancy' / 'zero_sales'. Also the
                           storeops.alert_log scope the caller writes.
      build(name, items)   -> {"subject", "html"} for ONE recipient's digest.
      key_parts(item)      -> the tuple identifying this finding inside the store-day, e.g.
                           (store, date, kind).
      store_of(item)       -> the store this finding belongs to.
      kind                 the digest label put on each planned digest (defaults to scope).
      extra_recipients     the tenant's own NAMED list for this scope (see `named_extras`), in the
                           same shape as a resolved manager. A named recipient is not per-store —
                           they asked to hear about the SCOPE — so they receive every store's items,
                           and the same one-digest-per-recipient and ref_key dedup as everyone else.
                           A person who is both named and resolved from the tree gets ONE digest,
                           because the identity is the address, not how they were found.
      use_tree             False means "only the named list": a tenant that keeps a list and clears
                           `include_dm` on all of it has said "just these people" (`use_hierarchy`).
                           It cannot silence a scope by accident — an empty list keeps the default.

    Returns {"digests": [{kind, to, to_name, subject, html, items:[{…, ref_key}]}]}, sorted by
    recipient so a caller's output is stable. Every item carries the ref_key the caller filters on
    BEFORE sending and records AFTER sending — the same two calls ePay already makes.
    """
    by_store = {}
    for it in (items or []):
        by_store.setdefault(store_of(it), []).append(it)

    want = normalize_channels(channels)
    mgr = {}   # identity -> {"name", "email", "addresses", "items": [...]}
    extras = [m for m in (extra_recipients or []) if isinstance(m, dict)]
    for store, store_items in by_store.items():
        tree = recipients_for(hierarchy_by_store.get(store)) if use_tree else []
        for m in tree + extras:
            addrs = addresses_for(m, want)
            if not addrs:
                continue   # no requested channel can reach them — the house rule, not a knob.
            # THE DEDUP IDENTITY stays the EMAIL wherever there is one, so every ref_key an
            # email-only caller has ever written is byte-identical and no finding re-escalates.
            # A recipient reachable ONLY on WhatsApp keys on that address instead, which is the
            # honest answer: they are a different row in alert_log because they are a different
            # address, not a silently dropped manager (the pre-2026-10-03 behaviour).
            ident = addrs.get("email") or addrs.get("whatsapp")
            slot = mgr.setdefault(ident.lower(), {"name": (m or {}).get("name") or ident,
                                                  "email": addrs.get("email", ""),
                                                  "addresses": addrs, "items": []})
            # A manager reached through two hierarchy paths can carry an address on one path and not
            # the other; the union is what can reach them, never the last path seen.
            for ch, a in addrs.items():
                slot["addresses"].setdefault(ch, a)
            for it in store_items:
                slot["items"].append({**it, "ref_key": ref_key(scope, today, ident, *key_parts(it))})

    digests = []
    for slot in mgr.values():
        # One recipient can oversee the same store through BOTH the DM node and an ancestor — dedup
        # by ref_key so a finding is never listed twice in one digest.
        seen, uniq = set(), []
        for it in slot["items"]:
            if it["ref_key"] in seen:
                continue
            seen.add(it["ref_key"])
            uniq.append(it)
        built = build(slot["name"], uniq)
        d = {"kind": kind or scope, "to": slot["email"], "to_name": slot["name"],
             "subject": built["subject"], "html": built["html"], "items": uniq}
        # `to` is kept as the email for every existing caller and reader. `addresses` is additive and
        # names each channel that can actually reach this recipient; `text` is only present when a
        # caller asked for a channel that needs plain text, so an email-only plan is unchanged.
        if want != DEFAULT_CHANNELS or set(slot["addresses"]) != {"email"}:
            d["addresses"] = dict(slot["addresses"])
            if "text" in built:
                d["text"] = built["text"]
        digests.append(d)
    digests.sort(key=lambda d: (d["to"] or (d.get("addresses") or {}).get("whatsapp") or "").lower())
    return {"digests": digests}
