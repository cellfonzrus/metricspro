"""SEND OUTCOME — the ONE home for "what did this send actually do, and what does a human do now?".

THE DEFECT THIS REMOVES (owner 2026-10-24): the Send box said `Sent: 6 ok, 10 failed.` and stopped
there. Everything needed to say more was already in hand — every send path builds one `send_log` row
per attempt carrying the channel, the address and the provider's own error string — and every path
then threw all of it away and returned two integers. A count is not a reason, so the only way to
learn who missed the report was to read the database.

Stated as a CLASS, because the instance is not the defect: **a failure is reported with its target
and its reason, never as a number.** The same two integers are returned by `/notify/send`,
`/notify/send-file` and `/notify/send-to-designated`, and the same raw provider string is parked on a
digest leg by `notify/digest_delivery`; all of them dereference this module rather than each growing
their own wording.

WHAT IS HERE
  `classify(channel, error)`  the provider's error -> a named code, a plain-language reason and the
                              fix. An **ALLOWLIST**: an error this house has not ruled on comes back
                              `recognised: False` with NO fix, because a confident wrong remedy costs
                              more than an honest "not recognised" (same rule as
                              `carrier_dollar_class.component_is_evidence`).
  `summarize(rows, note=)`    the attempt rows -> the outcome a human reads: who is owed the report,
                              on which channel, why, and what to do. PURE.
  `headline(outcome)`         the one sentence every surface shows, so the button, the designated-send
                              page and any future caller cannot word it three ways.

WHY THE COUNT CHANGES SHAPE. The attempt rows count one failure PER FILE: five people who did not get
a WhatsApp copy of an .xlsx and a .pdf render as "10 failed". People, not attachments, are what a
human acts on, so the outcome counts **(channel, target) pairs** and keeps the attempt count beside
it. `sent` / `failed` in the response are unchanged for callers that already read them.

RULE TWO: no carrier, tenant, store or product name appears here. Nothing here does IO.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §NOTIFY-01.
"""
import re

# ── the allowlist ────────────────────────────────────────────────────────────────────────────────
# (code, channel or "", reason, fix). `channel` empty means the rule applies on any channel.
# A rule is added only when the remedy is KNOWN — see the module docstring on why guessing is worse
# than silence.
_RULES = (
    # ── WhatsApp Cloud API, matched on Meta's own "(#code)" ──
    ("133010", "whatsapp",
     "Our WhatsApp sender number is not registered with Meta's Cloud API, so Meta refused every "
     "message — this is our end, not the recipient's.",
     "In Meta Business Suite → WhatsApp Manager → API Setup, register the sending number again (it "
     "asks for the 6-digit PIN), then check that WHATSAPP_PHONE_NUMBER_ID still names that number."),
    ("131030", "whatsapp",
     "Meta will not deliver to this number: the WhatsApp app is still in development mode, which "
     "only allows the numbers on its test list.",
     "Switch the Meta app to Live mode, or add this number to the test recipient list in Meta "
     "Business Suite → WhatsApp Manager → API Setup."),
    ("131026", "whatsapp",
     "Meta could not deliver to this number — it is not a WhatsApp account, or cannot receive this "
     "kind of message.",
     "Check the number on the person's record (it must be their WhatsApp number, with country code), "
     "or send them this report by email instead."),
    ("131047", "whatsapp",
     "More than 24 hours have passed since this person last messaged us, so Meta only allows an "
     "approved template, not a free message.",
     "Ask them to send any WhatsApp message to our number to reopen the 24-hour window, or have the "
     "template approved in Meta Business Suite → WhatsApp Manager → Message Templates."),
    ("132001", "whatsapp",
     "The WhatsApp message template Meta was asked for does not exist in that language.",
     "Create the template (or fix its name/language) in Meta Business Suite → WhatsApp Manager → "
     "Message Templates, and make WHATSAPP_TEMPLATE_NAME match it exactly."),
    ("132000", "whatsapp",
     "The template was sent the wrong number of fill-in values, so Meta rejected it.",
     "Compare the template's placeholders in WhatsApp Manager with what the sender fills in — they "
     "have to match one for one."),
    ("132018", "whatsapp",
     "The template was sent values it does not accept, so Meta rejected it.",
     "Compare the template's placeholders in WhatsApp Manager with what the sender fills in — they "
     "have to match one for one."),
    ("190", "",
     "Our access token has expired or been revoked, so the provider would not let us send at all.",
     "Generate a fresh permanent access token in Meta Business Suite → System Users and set it as "
     "WHATSAPP_ACCESS_TOKEN, then send again."),
    ("80007", "whatsapp",
     "We sent more messages than Meta's rate limit allows for this number.",
     "Nothing is broken — wait and send again. If it recurs, request a higher messaging limit in "
     "WhatsApp Manager."),
    ("100", "whatsapp",
     "Meta rejected one of the values we sent as invalid.",
     "Check the recipient's number is in full international form (+1 followed by 10 digits) on their "
     "record."),
    # ── our own pre-flight refusals (the channel was never configured) ──
    ("not_configured", "",
     "This channel is not switched on for this platform, so nothing was even attempted.",
     "Set this channel's credentials in the environment (email: RESEND_API_KEY and "
     "NOTIFY_FROM_EMAIL; WhatsApp: WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID), or untick "
     "the channel before sending."),
    # ── email (Resend) ──
    ("email_auth", "email",
     "Resend rejected our API key, so no email could be sent.",
     "Put a current API key from the Resend dashboard into RESEND_API_KEY and send again."),
    ("email_domain", "email",
     "Resend will not send from our address because its domain is not verified.",
     "Verify the sending domain in the Resend dashboard (Domains → add the DNS records it shows), "
     "then send again."),
    ("email_address", "email",
     "This email address is not one Resend will accept — it is malformed or mistyped.",
     "Fix the address on the person's record (People → the employee → Email), then send again."),
    ("email_rate", "email",
     "We sent more email than Resend's rate limit allows.",
     "Nothing is broken — wait a minute and send again."),
    ("email_attachment", "email",
     "The attached file was too large for Resend to accept.",
     "Narrow the report's filters so the file is smaller, or send the link instead of the file."),
)

_BY_CODE = {(code, ch): (reason, fix) for (code, ch, reason, fix) in _RULES}
# A code ruled on for every channel ("") is reachable from any channel.
_ANY_CHANNEL = {code for (code, ch, _r, _f) in _RULES if not ch}

_UNRECOGNISED = ("The provider rejected this one and we have no ruling on its reason yet.",
                 "")

_META_CODE = re.compile(r"\(#(\d+)\)")
_HTTP_CODE = re.compile(r"\b(\d{3})\b")


def provider_code(channel, error) -> str:
    """The code this house keys a remedy on, or "" when the error carries none we recognise.

    Meta states its own `(#131030)`; Resend does not, so an email failure is keyed by what the
    message SAYS, which is the only signal the transport gives us. Our own pre-flight refusal
    ("isn't set up yet") is keyed `not_configured` — a channel that was never tried is a different
    fact from one that tried and was refused."""
    text = str(error or "")
    low = text.lower()
    if "isn't set up yet" in low or "isnt set up yet" in low or "aren't set up yet" in low:
        return "not_configured"
    m = _META_CODE.search(text)
    if m:
        return m.group(1)
    if "authentication error" in low or '"code":190' in low.replace(" ", ""):
        return "190"
    if str(channel) == "email":
        # Resend states the fault in words, so the words are the key. Each test names a phrase the
        # provider actually uses — never a word as common as "to", which every error text contains.
        if "not verified" in low or "verify a domain" in low:
            return "email_domain"
        if "too large" in low or "exceeds the maximum" in low:
            return "email_attachment"
        if "invalid `to`" in low or "invalid to field" in low or "invalid email" in low \
                or "not a valid email" in low:
            return "email_address"
        http = _HTTP_CODE.search(text)
        status = http.group(1) if http else ""
        if status in ("401", "403"):
            return "email_auth"
        if status == "429":
            return "email_rate"
        if status == "422":
            return "email_address"
    return ""


def classify(channel, error) -> dict:
    """One failed attempt -> {code, reason, fix, recognised, error}.

    ALLOWLIST: a code this house has not ruled on comes back `recognised: False` with an empty `fix`
    and the provider's own words as the reason. A surface shows that honestly rather than prescribing
    a remedy nobody has checked."""
    code = provider_code(channel, error)
    key = (code, "" if code in _ANY_CHANNEL else str(channel or ""))
    ruled = _BY_CODE.get(key)
    raw = str(error or "").strip()
    if not ruled:
        reason, fix = _UNRECOGNISED
        return {"code": code, "reason": (raw[:300] or reason) if raw else reason,
                "fix": fix, "recognised": False, "error": raw[:500]}
    return {"code": code, "reason": ruled[0], "fix": ruled[1], "recognised": True,
            "error": raw[:500]}


def summarize(rows, note="") -> dict:
    """PURE. The attempt rows a send just built -> the outcome a human reads.

    `rows` are the `send_log` row dicts themselves (channel / target / status / error), so no caller
    has to keep a second list in step with them.

    One entry per (channel, target) — a PERSON on a CHANNEL, not an attachment. A target that
    carried one file and failed another is `partial`: something it was owed did not arrive, so it is
    counted as failed and said so.
    """
    order = []
    seen = {}
    attempts_sent = attempts_failed = 0
    for r in (rows or []):
        ch = str((r or {}).get("channel") or "")
        target = str((r or {}).get("target") or "")
        status = str((r or {}).get("status") or "")
        attempts_sent += status == "sent"
        attempts_failed += status == "failed"
        key = (ch, target)
        if key not in seen:
            seen[key] = {"channel": ch, "target": target, "attempts": 0, "sent": 0, "failed": 0,
                         "first_error": None}
            order.append(key)
        e = seen[key]
        e["attempts"] += 1
        if status == "sent":
            e["sent"] += 1
        elif status == "failed":
            e["failed"] += 1
            if e["first_error"] is None:
                e["first_error"] = (r or {}).get("error")

    ok, failed = [], []
    for key in order:
        e = seen[key]
        if not e["failed"]:
            ok.append({"channel": e["channel"], "target": e["target"], "attempts": e["attempts"]})
            continue
        row = {"channel": e["channel"], "target": e["target"], "attempts": e["attempts"],
               "failed_attempts": e["failed"], "partial": bool(e["sent"]),
               **classify(e["channel"], e["first_error"])}
        failed.append(row)

    fixes = []
    for f in failed:
        if f["fix"] and f["fix"] not in fixes:
            fixes.append(f["fix"])
    out = {"ok": ok, "failed": failed, "ok_count": len(ok), "failed_count": len(failed),
           "attempts": {"sent": attempts_sent, "failed": attempts_failed},
           "fixes": fixes, "note": str(note or "")}
    out["headline"] = headline(out)
    return out


def headline(outcome) -> str:
    """The ONE sentence a surface shows. Counts PEOPLE on channels, never attachments, and never
    claims success it cannot see."""
    o = outcome or {}
    ok = int(o.get("ok_count") or 0)
    bad = int(o.get("failed_count") or 0)
    note = str(o.get("note") or "").strip()
    if not ok and not bad:
        return note or "Nothing was sent — there was no recipient to send to."
    total = ok + bad
    if not bad:
        return f"Sent to all {ok} recipient{'' if ok == 1 else 's'}."
    if not ok:
        return (f"Nothing arrived — all {bad} recipient{'' if bad == 1 else 's'} failed. "
                "The reason for each is below.")
    return (f"Sent to {ok} of {total} recipients. {bad} failed — the reason for each is below.")


def stamp(rows) -> list:
    """READ side: stamp a `reason` and a `fix` onto send-history rows, so the history explains a
    failure in the same words the Send box used. Rows written before this module existed are stamped
    too — a display fact is resolved by the read, never assumed to be stored (§13b.1 pattern)."""
    out = []
    for r in (rows or []):
        row = dict(r or {})
        err = row.get("delivery_error") or row.get("error")
        if str(row.get("status") or "") == "failed" or row.get("delivery_error") or \
                str(row.get("delivery_status") or "") == "failed":
            if err:
                c = classify(row.get("channel"), err)
                row["failure_reason"] = c["reason"]
                row["failure_fix"] = c["fix"]
                row["failure_code"] = c["code"]
        out.append(row)
    return out
