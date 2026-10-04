"""ALERT LOG — the ONE home for "has this finding already reached this recipient, on this channel?"

CLAUDE.md, "A fix is a DESIGN fix": the class of defect this module exists to kill is

    a send record that does not say WHICH CHANNEL carried the message.

Before migration 1051, `storeops.alert_log` held one row per (scope, ref_key) and the code wrote it
as soon as ANY channel delivered. So a digest whose email went out and whose WhatsApp failed was
recorded as done, and the WhatsApp was never retried — the recipient was told on one channel and
silently not on the other, forever. The tenant had asked for both. (Owner decision 2026-10-04:
"Fix it properly".)

The fact is now spelled per channel AND per recipient, and this module is the only place that reads
or writes it. Four separate implementations used to exist — the lateness pair in `storeops/router`,
`_expiry_already_sent` + its own insert in the same file, and `closing/_send_alert`'s own
select/insert pair — which is the duplicate defect the index rules forbid: each had its own idea of
what "already sent" means. `harness_alert_channel_record.py` fails the build if a second one appears.

WHAT A ROW MEANS
  A row says: this scope's finding `ref_key` was CARRIED to `recipients` over `channel`. It is
  written only after a channel actually delivered, so a send that reached nobody is not "already
  alerted" (the 2026-09-20 rule, kept).

  `channel` is NULL only on a legacy row that migration 1051 could not classify (its `recipients`
  was empty, i.e. it recorded a deliberate silence rather than a delivery). A NULL-channel row
  satisfies no channel, which is the honest reading: nothing is known to have been carried.

RECIPIENT MATCHING
  A row's `recipients` is one address or a comma-separated list of them. A finding counts as already
  carried to an address when that address IS the row's recipients, or is one of its members. The
  membership arm is what keeps the pre-1051 rows (written as a joined list by `closing/_send_alert`)
  meaningful, so switching to per-recipient records does not re-alert anybody already told.

RULE TWO: no carrier, tenant, store or product name appears here. The decision functions are pure
stdlib so `harness_alert_channel_record.py` proves them DB-free; only `already_sent`, `sent_pairs`
and `record_sent` touch the client, and they never raise.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §15.1.
"""

TABLE = "alert_log"

#: Every channel a message can be carried on. A new channel is added HERE and nowhere else.
CHANNELS = ("email", "whatsapp")


def _s(v):
    return str(v or "").strip()


def addresses_in(recipients):
    """PURE. The set of addresses a stored `recipients` value names, lower-cased.

    One address, or a comma-separated list of them — both shapes have been written to this table.
    """
    return {p.strip().lower() for p in _s(recipients).split(",") if p.strip()}


def row_matches(row, *, channel, recipient=None):
    """PURE. Does this stored row say the finding was carried to `recipient` over `channel`?

    A row with no channel (legacy, unclassifiable) matches nothing: see the module docstring.
    A call that names no recipient asks only about the channel.
    """
    if _s((row or {}).get("channel")).lower() != _s(channel).lower() or not _s(channel):
        return False
    if recipient is None:
        return True
    return _s(recipient).lower() in addresses_in((row or {}).get("recipients"))


def pending_for_channel(items, carried, channel, recipient=None):
    """PURE. The items of one recipient's digest that `channel` has NOT carried yet.

    `carried` is what `sent_pairs` returned: a set of (ref_key, channel, address) triples. This is
    the whole of the per-channel decision, so the harness proves it without a database.
    """
    out = []
    for it in (items or []):
        rk = _s((it or {}).get("ref_key"))
        if not rk:
            continue
        if (rk, _s(channel).lower(), _s(recipient).lower()) in carried:
            continue
        out.append(it)
    return out


def sent_pairs(client, org_id, scopes, ref_keys=None):
    """What has already been carried, for one org and one or more scopes: a set of
    (ref_key, channel, address) triples, lower-cased.

    ONE read per sweep rather than one per item — the pre-1051 code issued a query per item per
    recipient, and asking per channel as well would have doubled it. `client` is already scoped to
    the storeops schema. Never raises: an unreadable log means nothing is known to have been sent,
    which re-sends rather than silently suppresses.
    """
    want = [scopes] if isinstance(scopes, str) else [s for s in (scopes or []) if s]
    if not want:
        return set()
    try:
        q = (client.table(TABLE).select("ref_key,channel,recipients")
             .eq("org_id", org_id).in_("scope", want))
        if ref_keys:
            q = q.in_("ref_key", list(ref_keys))
        rows = (q.limit(20000).execute().data) or []
    except Exception:
        return set()
    out = set()
    for r in rows:
        ch = _s(r.get("channel")).lower()
        if not ch:
            continue       # legacy, unclassifiable — carried nothing we can name
        rk = _s(r.get("ref_key"))
        for a in addresses_in(r.get("recipients")):
            out.add((rk, ch, a))
    return out


def already_sent(client, org_id, scope, ref_key, channel, recipient=None):
    """Has this one finding already been carried to this recipient over this channel?

    The single-row question, for callers that hold one finding rather than a planned digest. Never
    raises: unknown reads as "not sent", so a failure re-sends rather than silently suppresses.
    """
    try:
        rows = (client.table(TABLE).select("channel,recipients").eq("org_id", org_id)
                .eq("scope", scope).eq("ref_key", ref_key).limit(200).execute().data) or []
    except Exception:
        return False
    return any(row_matches(r, channel=channel, recipient=recipient) for r in rows)


def record_sent(client, org_id, scope, ref_key, recipients, channel, detail=None):
    """Record that `channel` CARRIED this finding to `recipients`. Call it only after a channel
    actually delivered — a send that reached nobody is not "already alerted".

    `channel` is required and must be one of CHANNELS: a row that does not say how it was carried is
    the defect this module exists to remove, so there is deliberately no default.
    """
    ch = _s(channel).lower()
    if ch not in CHANNELS:
        raise ValueError(f"record_sent needs a channel in {CHANNELS}, got {channel!r}")
    body = {"org_id": org_id, "scope": scope, "ref_key": ref_key,
            "recipients": recipients, "channel": ch,
            "detail": {"kind": scope, **(detail or {})}}
    try:
        client.table(TABLE).insert(body).execute()
    except Exception:
        pass


def record_silence(client, org_id, scope, ref_key, detail=None):
    """Record that this scope had NOBODY to tell. Written with no channel and no recipients, which
    `row_matches` deliberately does not treat as delivered: the moment a recipient exists, the next
    occurrence alerts for real. Kept from `closing/_send_alert`, where the rule was established."""
    try:
        client.table(TABLE).insert({"org_id": org_id, "scope": scope, "ref_key": ref_key,
                                    "recipients": "", "channel": None,
                                    "detail": {"kind": scope, **(detail or {})}}).execute()
    except Exception:
        pass
