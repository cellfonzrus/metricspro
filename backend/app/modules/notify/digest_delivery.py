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

WHAT IS NOT PARAMETERISED
  That a channel is only spoken to when it is configured AND the recipient has an address on it;
  that the record is written per channel and only after that channel delivered; and that the
  dedup fact lives in `storeops.alert_log` through `storeops/alert_log.py`. Those are the house
  rule. A caller supplies its own subject/body builder and nothing else about delivery.

RULE TWO: no carrier, tenant, store or product name appears here.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §15.2.
"""

from app.modules.storeops import alert_log as _log

CHANNELS = _log.CHANNELS


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


async def _send(ch, address, built, wa_filename):
    """THE only place this codebase speaks to a channel on a digest's behalf. True when it really
    delivered — a transport that answers without a message id delivered nothing."""
    if ch == "email":
        from app.modules.notify.channels import email_resend
        await email_resend.send_email(to=address, subject=built["subject"], html=built["html"])
        return True
    if ch == "whatsapp":
        from app.modules.notify.channels import whatsapp_meta
        # data=b"" is the text-only rung: a business-initiated message takes the approved template,
        # never a free-form text Meta accepts with a 200 and silently drops.
        res = await whatsapp_meta.send_document_detailed(
            address, b"", "text/plain", wa_filename, built.get("text") or "")
        return bool((res or {}).get("message_id"))
    return False


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
            leg["delivered"] = await _send(ch, addr, built, wa_filename)
        except Exception as e:
            leg["error"] = str(e)[:200]
            print(f"WARN {scope} {ch} to {addr} failed: {e}")
        if not leg["delivered"]:
            continue       # nothing carried, nothing recorded — this channel is owed it next run
        for it in todo:
            _log.record_sent(client, org_id, scope, it["ref_key"], addr, ch, detail=detail)
    row["delivered"] = sorted(ch for ch, leg in row["channels"].items() if leg["delivered"])
    return row


async def deliver_digests(client, org_id, scope, digests, *, build, wa_filename,
                          channels_ok, dry_run=False, preview_item=None, detail=None):
    """The fan-out: every planned digest from `manager_digest.plan_digests`, delivered per channel.

    Returns (sent, skipped, planned). `sent` counts recipients who were carried something on at
    least one channel; `skipped` counts recipients every channel was already square with.
    `preview_item(item)` lets a caller keep its own structured dry-run row shape — the view owns
    presentation, so a planned row never ships pre-joined display text.
    """
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
    return sent, skipped, planned
