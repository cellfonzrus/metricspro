"""DIGEST DELIVERY — the ONE home for "carry this message to one recipient on their channels, and
record what each channel actually did".

CLAUDE.md, "One fact, one home, dereferenced — never copied". Five copies of the same ladder existed
before this module: the manager follow-up digest, the ePay discrepancy digest, the zero-sales
digest, the bill-pay declaration digest and the store-visit digests each held their own
"try email, try WhatsApp, then write one dedup row". They drifted exactly as the rule predicts —
three sent on both channels and two on email only, and ALL of them recorded the send without saying
which channel carried it, so a failed WhatsApp was never retried (owner decision 2026-10-04,
"Fix it properly").

WHAT IS HERE
  `pending_by_channel`  PURE — which of a recipient's items each channel still owes them.
  `deliver_recipient`   the ladder: per channel, build from what that channel still owes, send,
                        and record THAT channel. One channel failing never marks another's work
                        done, and never marks its own.
  `deliver_digests`     the fan-out loop over `manager_digest.plan_digests` output.
  `company_item`        PURE — the ONE company-level notification a scope's findings add up to, for
                        the declared `inbox` route (index §CORE-49). A per-manager digest is
                        ADDRESSED and a company item is not, so this is a different shape of the
                        same findings, derived here once rather than per calling scope.

WHAT IS NOT PARAMETERISED
  That a channel is only spoken to when it is configured AND the recipient has an address on it;
  that the record is written per channel and only after that channel delivered; and that the
  dedup fact lives in `storeops.alert_log` through `storeops/alert_log.py`. Those are the house
  rule. A caller supplies its own subject/body builder and nothing else about delivery.

RULE TWO: no carrier, tenant, store or product name appears here.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §15.2.
"""

from app.modules.storeops import alert_log as _log

#: The ADDRESSED channels only (`alert_log.MESSAGE_CHANNELS`). The send record knows one more —
#: the company inbox — which has no address and is never offered to a recipient (index §CORE-49).
CHANNELS = _log.MESSAGE_CHANNELS


def pending_by_channel(addresses, items, carried, channels_ok):
    """PURE. {channel: (address, [items that channel still owes])} for ONE recipient.

      addresses    {"email": …, "whatsapp": …} — the channels that can actually reach them.
      items        that recipient's digest items, each carrying a `ref_key`.
      carried      `alert_log.sent_pairs` output: {(ref_key, channel, address)}.
      channels_ok  {"email": bool, "whatsapp": bool} — which channels are configured at all.

    A channel with no address, no configuration, or nothing left to say is simply absent from the
    answer. An unconfigured channel is NOT a channel that was tried, so it records nothing and the
    next run offers it again.
    """
    out = {}
    for ch in CHANNELS:
        addr = str((addresses or {}).get(ch) or "").strip()
        if not addr or not (channels_ok or {}).get(ch):
            continue
        todo = _log.pending_for_channel(items, carried, ch, addr)
        if todo:
            out[ch] = (addr, todo)
    return out


async def _send(ch, address, built, wa_filename, from_name=""):
    """THE only place this codebase speaks to a channel on a digest's behalf. True when it really
    delivered — a transport that answers without a message id delivered nothing."""
    if ch == "email":
        from app.modules.notify.channels import email_resend
        await email_resend.send_email(to=address, subject=built["subject"], html=built["html"],
                                     from_name=from_name)
        return True
    if ch == "whatsapp":
        from app.modules.notify.channels import whatsapp_meta
        # data=b"" is the text-only rung: a business-initiated message takes the approved template,
        # never a free-form text Meta accepts with a 200 and silently drops.
        res = await whatsapp_meta.send_document_detailed(
            address, b"", "text/plain", wa_filename, built.get("text") or "")
        return bool((res or {}).get("message_id"))
    return False


def _tenant_from_name(client, org_id) -> str:
    """A digest is a tenant's own mail, so it carries the tenant's name — the one home for it
    (`core/tenant_identity`), never a second read (owner 2026-10-17, §POS-02)."""
    from app.modules.core import tenant_identity as _ti
    try:
        return _ti.display_name(client, org_id)
    except Exception:
        return ""


async def deliver_recipient(client, org_id, scope, *, addresses, items, carried, channels_ok,
                            build, to_name, wa_filename, dry_run=False, detail=None):
    """Carry ONE recipient's outstanding items on each channel that still owes them.

    Returns a planned row: `channels` names what each channel still owed and whether it carried it;
    `already_sent` is true only when EVERY reachable channel was already square, which is the shape
    every pre-1051 caller's dry-run preview already read.
    """
    work = pending_by_channel(addresses, items, carried, channels_ok)
    # "Already sent" must mean EVERY channel that can reach this recipient is square — never "no
    # channel is configured". Collapsing those two would let an estate with no credentials report
    # its backlog as delivered, which is the quietest possible failure.
    reachable = [ch for ch in CHANNELS
                 if str((addresses or {}).get(ch) or "").strip() and (channels_ok or {}).get(ch)]
    row = {"to": (addresses or {}).get("email") or (addresses or {}).get("whatsapp") or "",
           "addresses": dict(addresses or {}),
           "already_sent": bool(reachable) and not work, "channels": {}}
    if not reachable:
        row["no_channel"] = True       # nothing could carry it; nothing is recorded, so it retries
    if not work:
        return row
    # The preview names the largest outstanding set, so a caller's existing `subject`/`items`
    # fields still describe what this recipient is about to be told.
    # The tenant's name is read ONCE for this recipient, not once per channel leg.
    from_name = _tenant_from_name(client, org_id)
    widest = max(work.values(), key=lambda p: len(p[1]))[1]
    built_preview = build(to_name, widest)
    row["subject"] = built_preview["subject"]
    row["items"] = widest
    for ch, (addr, todo) in sorted(work.items()):
        leg = {"address": addr, "items": len(todo), "delivered": False}
        row["channels"][ch] = leg
        if dry_run:
            continue
        built = build(to_name, todo)
        try:
            leg["delivered"] = await _send(ch, addr, built, wa_filename, from_name=from_name)
        except Exception as e:
            leg["error"] = str(e)[:200]
            # SIBLING OF THE SEND BOX (§NOTIFY-01): a digest leg parked the provider's raw string and
            # left every reader to interpret it. The reason and the remedy come from the one home, so
            # a digest preview explains a dead channel in the same words a report send does.
            from app.modules.notify import send_outcome as _why
            leg.update({k: v for k, v in _why.classify(ch, e).items()
                        if k in ("code", "reason", "fix", "recognised")})
            print(f"WARN {scope} {ch} to {addr} failed: {e}")
        if not leg["delivered"]:
            continue       # nothing carried, nothing recorded — this channel is owed it next run
        for it in todo:
            _log.record_sent(client, org_id, scope, it["ref_key"], addr, ch, detail=detail)
    row["delivered"] = sorted(ch for ch, leg in row["channels"].items() if leg["delivered"])
    return row


def company_item(scope, digests, item_label):
    """PURE. The ONE company-level notification this scope's findings add up to, or None.

    A per-manager digest answers "what does THIS manager owe attention to"; a company inbox item
    answers "what does this COMPANY owe attention to". The second is the first's findings, deduped
    across recipients and stated once — so a finding two managers were both emailed about is one
    line here, and the same findings tomorrow produce a byte-identical item that OVERWRITES rather
    than nags (owner 2026-11-24).

      item_label(item) -> the stable label of one finding, e.g. "S1 2026-08-20 fee".

    WHY THE LABEL IS REQUIRED and the `ref_key` is not used in its place: a ref_key carries the
    dedup DATE, so a body built from ref_keys would differ every midnight and re-nag every single
    day — the exact defect this mechanism exists to remove. A caller that cannot name its findings
    cannot take this route (`deliver_digests` then sends the messages and says why), which is the
    safe direction.

    The inbox identity is the (scope, company) pair, NOT the findings: one open item per scope, whose
    content moves as the findings move. That is what "overwrite if it is the same message" asks for.
    """
    seen, labels = set(), []
    for dg in (digests or []):
        for it in (dg.get("items") or []):
            lab = str(item_label(it) or "").strip()
            if not lab or lab in seen:
                continue
            seen.add(lab)
            labels.append(lab)
    if not labels:
        return None
    labels.sort()      # the body must not depend on recipient order, or it would "change" at random
    n = len(labels)
    return {
        "inbox_key": f"digest:{scope}",
        "title": f"{n} open {scope.replace('_', ' ')} finding{'' if n == 1 else 's'}",
        "body": ("These are open now. This item is updated in place while they stay open, and closes "
                 "itself once the check reports none.\n\n" + "\n".join(labels)),
        "affected": labels,
    }


async def deliver_digests(client, org_id, scope, digests, *, build, wa_filename,
                          channels_ok, dry_run=False, preview_item=None, detail=None,
                          item_label=None, deep_link=None, severity=None):
    """The fan-out: every planned digest from `manager_digest.plan_digests`, delivered per channel.

    Returns (sent, skipped, planned). `sent` counts recipients who were carried something on at
    least one channel; `skipped` counts recipients every channel was already square with.
    `preview_item(item)` lets a caller keep its own structured dry-run row shape — the view owns
    presentation, so a planned row never ships pre-joined display text.

    THE ROUTE (index §CORE-49). A company whose `storeops.alert_route` row sends this scope to the
    `inbox` gets ONE overwriting company item instead of the addressed mail; `both` gets the item AND
    the mail; an undeclared scope behaves exactly as it always has. `item_label` is what makes the
    inbox route AVAILABLE to a scope at all — without it the item cannot be named, so the messages go
    out as before and the planned rows say `inbox_unnameable`. Nothing is ever silenced: a route that
    says `inbox` but whose item could not be written (the migration un-run, the table unreachable)
    also falls back to sending.
    """
    from app.modules.core import notification_inbox as _inbox
    route, filed, item = _inbox.DEFAULT_ROUTE, None, None
    try:
        route = _inbox.route_for(_inbox.routes(client, org_id=org_id), scope, org_id)
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN {scope} route read failed for {org_id}: {e} — sending as a message")
    if route in ("inbox", "both"):
        item = company_item(scope, digests, item_label) if item_label else None
        if item and not dry_run:
            filed = _inbox.file_item(client, org_id, scope, item["inbox_key"], item["title"],
                                     item["body"], severity or _inbox.DEFAULT_SEVERITY,
                                     deep_link=deep_link, affected=item["affected"])
            if (filed or {}).get("state") in ("created", "changed"):
                _log.record_sent(client, org_id, scope, item["inbox_key"], _inbox.CHANNEL,
                                 _inbox.CHANNEL, detail=detail)
        if item_label and not dry_run:
            # SELF-CLEARING, on every path including the one where nothing is open: the item is this
            # scope's whole answer for this company, so an empty answer closes it.
            _inbox.resolve_missing(client, org_id, scope,
                                   [f"digest:{scope}"] if item else [],
                                   prefix=f"digest:{scope}")
    if _inbox.suppresses_message(route, (item or {}).get("inbox_key"), filed):
        return 0, 0, [{"route": route, "inbox": filed, "recipients": len(digests or [])}]
    carried = _log.sent_pairs(client, org_id, scope)
    sent = skipped = 0
    planned = []
    for dg in (digests or []):
        addrs = dg.get("addresses") or ({"email": dg["to"]} if dg.get("to") else {})
        row = await deliver_recipient(client, org_id, scope, addresses=addrs,
                                      items=dg.get("items") or [], carried=carried,
                                      channels_ok=channels_ok, build=build,
                                      to_name=dg.get("to_name"), wa_filename=wa_filename,
                                      dry_run=dry_run, detail=detail)
        if row["already_sent"]:
            skipped += 1
        elif row.get("delivered"):
            sent += 1       # dry run carries nothing, so it counts nothing — as before 1051
        if preview_item and row.get("items"):
            row["items"] = [preview_item(i) for i in row["items"]]
        elif row.get("items"):
            row["items"] = len(row["items"])
        planned.append(row)
    if route != _inbox.DEFAULT_ROUTE:
        # Only when the inbox was actually ASKED for. A scope still on `message` gets the planned
        # rows its caller has always had and nothing appended: a pseudo-row in that list is a
        # surprise to every existing reader of it (it broke `harness_epay_alerts` S5 immediately).
        note = {"route": route, "inbox": filed}
        if route in ("inbox", "both") and not item_label:
            note["inbox_unnameable"] = True     # the caller named no labels; see `company_item`
        elif route == "inbox" and (filed or {}).get("state") not in _inbox.FILED_STATES and item:
            note["inbox_unavailable"] = True     # nothing silenced: the mail above went out
        planned.append(note)
    return sent, skipped, planned
