"""Out-of-band (2FA) code reader — the missing primitive for UNATTENDED portal logins.

WHY THIS EXISTS. The portal drivers (`vidapay_sweep`, `b2b_sweep`) are built INTERACTIVE by design:
`begin_login()` reaches the code-entry screen and then stops, because a human reads the code out of their
email/SMS and calls `complete_2fa(code)`. vidapay_sweep says so itself — "a human clicks these; a headless
login that only DETECTS the 2FA screen never receives a code". So even once the WAF is cleared and the
selectors are calibrated, a SCHEDULED run still dies at the code prompt: the report can never be pulled
without a person. This module is the piece that closes that gap — it reads the code the portal just
emailed, so `complete_2fa` can be driven by the cron instead of by a human.

REUSE, NOT REBUILD. The tenant's mailbox is already configured and already reachable: `email_sweep` owns
the IMAP connection (`_connect_resilient`), the folder walk (`_iter_messages`) and the MIME part walk
(`_leaf_parts`). This module adds only the code-specific rules on top, and takes its IMAP settings from
the SAME `commcalc.email_sweep_config` row the daily sweep uses — one mailbox, one credential, no second
copy to rotate.

CONFIG, NEVER CODE. Which sender, which subject, how the code looks and how fresh it must be are per
LOGIN (`commcalc.data_source`, mig 307), because different portals mail codes differently. Nothing about
any portal, carrier or tenant appears in a branch here.

SECURITY POSTURE.
  • A 2FA code is a CREDENTIAL. It is never logged, never returned in an error string, and never stored.
    Callers get it back and use it immediately; diagnostics only ever say how many digits were found and
    which message it came from.
  • FRESHNESS IS A SECURITY CONTROL, not a nicety. Without a hard age limit the reader would happily
    replay a code from an old message — which both fails the login (portals expire codes in minutes) and
    would let anything that ever landed a matching email in the mailbox steer a future login. A candidate
    older than `max_age_seconds` is refused outright, even when it is the only one.
  • The sender/subject filters narrow WHICH messages may supply a code. They are a filter, not proof of
    origin — mail headers are forgeable — so they are combined with the freshness window rather than
    trusted alone.

The extraction + selection logic is PURE (strings and dicts in, result out) so it is unit-provable
without a mailbox; only `read_latest_code` touches IMAP.
"""
import re
from datetime import datetime, timedelta, timezone

# A standalone run of digits, i.e. one that is not a SLICE of a longer number — an order id, a phone
# number, a money amount, a date — where a naive \d{4,8} would happily match part of it.
#   lookbehind: no digit immediately before, and no separator-that-follows-a-digit ('12,345' → skip 345)
#   lookahead:  no digit immediately after,  and no separator-that-precedes-a-digit ('1-800' → skip 1)
# The separator tests deliberately require a digit on the far side, so ORDINARY SENTENCE PUNCTUATION does
# not disqualify a code: "Your code is 123456." must still match (the '.' is a full stop, not a decimal
# point) — the case that made the very first realistic example fail.
_RUN = re.compile(r"(?<!\d)(?<![\d][.,\-/])(\d{4,10})(?!\d)(?![.,\-/]\d)")

# Words that sit next to a real code in the sentence that carries it. A candidate near one of these wins
# over a bare number elsewhere in the mail (footers are full of bare numbers).
_CODE_WORDS = ("code", "otp", "pin", "passcode", "verification", "verify", "security",
               "authentication", "one-time", "one time", "2fa", "two-factor", "token")

# Numbers that are almost never a 2FA code even when they are the right length.
_YEARISH = re.compile(r"^(19|20)\d{2}$")


def strip_html(s):
    """Text out of an HTML part: drop script/style outright, turn tags into spaces (so `<b>12</b><b>34</b>`
    does NOT become '1234'), unescape entities, collapse whitespace. Plain text passes through unharmed."""
    s = str(s or "")
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                    ("&quot;", '"'), ("&#39;", "'"), ("&zwnj;", ""), ("&#8203;", "")):
        s = s.replace(ent, ch)
    s = re.sub(r"&#x?[0-9a-fA-F]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_code(body, code_regex=None, length=None):
    """The 2FA code in `body`, or None.

    `code_regex` (per-login config) wins outright when set — group 1 if the pattern has one, else the
    whole match — so a portal with an unusual format ('AB-12345', a 3-digit code) is a config change, not
    a code change. Otherwise: scan standalone digit runs, prefer one sitting near a code word, and fall
    back to the only plausible run in the mail. `length`, when set, requires exactly that many digits.

    Deliberately conservative: when several equally-plausible candidates disagree it returns None rather
    than guessing, because submitting a wrong code can lock the portal account."""
    text = strip_html(body)
    if not text:
        return None

    if code_regex:
        try:
            m = re.search(code_regex, text)
        except re.error:
            return None                                   # a bad pattern must not crash a login
        if not m:
            return None
        return (m.group(1) if m.re.groups else m.group(0)).strip() or None

    lo = hi = int(length) if length else None
    if lo is None:
        lo, hi = 4, 8

    cands = []
    for m in _RUN.finditer(text):
        d = m.group(1)
        if not (lo <= len(d) <= hi):
            continue
        if _YEARISH.match(d):
            near_word = _near_code_word(text, m.start())
            if not near_word:
                continue                                  # a bare '2026' is a year, not a code
        cands.append((d, m.start()))
    if not cands:
        return None

    keyed = [d for d, pos in cands if _near_code_word(text, pos)]
    if keyed:
        # all keyword-adjacent candidates agreeing = confident; disagreeing = ambiguous, refuse.
        return keyed[0] if len(set(keyed)) == 1 else None
    uniq = {d for d, _ in cands}
    return cands[0][0] if len(uniq) == 1 else None


def _near_code_word(text, pos, window=48):
    """Does a code word appear just before (or just after) the digits at `pos`? The code usually follows
    its label ('Your verification code is 123456'), but some mails put it first ('123456 is your code')."""
    lo = max(0, pos - window)
    around = text[lo:pos + window].lower()
    return any(w in around for w in _CODE_WORDS)


def message_matches(from_addr, subject, from_contains=None, subject_contains=None):
    """Is this message allowed to supply a code? Case-insensitive substring match; an unset filter matches
    everything. A FILTER, not proof of origin — headers are forgeable — so callers pair it with the
    freshness window."""
    f = str(from_addr or "").lower()
    s = str(subject or "").lower()
    if from_contains and str(from_contains).strip().lower() not in f:
        return False
    if subject_contains and str(subject_contains).strip().lower() not in s:
        return False
    return True


def pick_message(candidates, now=None, max_age_seconds=300, not_before=None):
    """The NEWEST candidate inside the freshness window, or None.

    `candidates` = [{date: datetime, body: str, from: str, subject: str}]. A message with no usable date
    is REFUSED, not assumed fresh — "unknown age" must never pass an age check. Anything older than
    `max_age_seconds` is refused even when it is the only candidate: portals expire codes in minutes, so
    an old match is either useless or someone else's.

    `not_before` is the stronger, caller-supplied floor an UNATTENDED login needs: the moment the login
    was started. Without it a poll could pick up the code from a PREVIOUS run that is still inside the
    age window and submit a code the portal has already retired. Applied on top of `max_age_seconds`,
    never instead of it."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(seconds=int(max_age_seconds or 0))
    if isinstance(not_before, datetime):
        nb = not_before if not_before.tzinfo else not_before.replace(tzinfo=timezone.utc)
        cutoff = max(cutoff, nb)
    fresh = []
    for c in candidates or []:
        d = c.get("date")
        if not isinstance(d, datetime):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if d < cutoff or d > now + timedelta(minutes=5):   # clock skew tolerance; not a time machine
            continue
        fresh.append((d, c))
    if not fresh:
        return None
    fresh.sort(key=lambda t: t[0], reverse=True)
    return fresh[0][1]


def code_from_messages(candidates, rules=None, now=None):
    """End-to-end over already-fetched messages: filter by sender/subject, keep the newest fresh one, and
    extract its code. PURE — this is the whole decision, so the IMAP layer stays a thin fetch.

    `rules` = {from_contains, subject_contains, code_regex, code_length, max_age_seconds}.
    Returns {"code", "message": {...}} or {"code": None, "reason": "<why>"} — the reason NEVER contains
    the code or any message body."""
    r = rules or {}
    matched = [c for c in (candidates or [])
               if message_matches(c.get("from"), c.get("subject"),
                                  r.get("from_contains"), r.get("subject_contains"))]
    if not matched:
        return {"code": None, "reason": "no message matched the configured sender/subject filter"}
    picked = pick_message(matched, now=now,
                          max_age_seconds=r.get("max_age_seconds", 300),
                          not_before=r.get("not_before"))
    if not picked:
        return {"code": None,
                "reason": (f"{len(matched)} message(s) matched the filter but none was newer than "
                           f"{r.get('max_age_seconds', 300)}s"
                           + (" / newer than the moment this login started"
                              if r.get("not_before") else "")
                           + " (a stale code is refused, never replayed)")}
    code = extract_code(picked.get("body"), r.get("code_regex"), r.get("code_length"))
    if not code:
        return {"code": None,
                "reason": "the newest matching message carried no unambiguous code "
                          "(set code_regex on the login if this portal's format is unusual)"}
    return {"code": code,
            "message": {"from": picked.get("from"), "subject": picked.get("subject"),
                        "date": picked.get("date"), "digits": len(code)}}   # never the code itself


def rules_from_source(row):
    """The mig-307 `oob_*` columns of a `commcalc.data_source` row, as the rules dict
    `read_latest_code` / `code_from_messages` expect. PURE — this is the one place the column names
    are known, so the router and any future caller can never drift on the mapping. An unset column
    simply doesn't constrain; a malformed number is dropped rather than crashing a login
    (`max_age_seconds` then falls back to the reader's 300s default, which is the TIGHTER choice)."""
    r = row or {}
    rules = {}
    for col, key in (("oob_from_contains", "from_contains"),
                     ("oob_subject_contains", "subject_contains"),
                     ("oob_code_regex", "code_regex")):
        v = str(r.get(col) or "").strip()
        if v:
            rules[key] = v
    for col, key in (("oob_code_length", "code_length"),
                     ("oob_max_age_seconds", "max_age_seconds")):
        try:
            v = int(r.get(col))
            if v > 0:
                rules[key] = v
        except (TypeError, ValueError):
            pass
    return rules


def read_latest_code(imap_cfg, rules=None, now=None, lookback_minutes=30):
    """Fetch recent mail and return `code_from_messages` over it. The ONLY function here that does I/O.

    `imap_cfg` is the tenant's `commcalc.email_sweep_config` row — the same mailbox and credential the
    daily attachment sweep already uses, so enabling unattended 2FA adds no new secret to manage.
    Best-effort: any IMAP failure returns a reason rather than raising, so a portal run reports "could not
    read the code" instead of a stack trace. Never logs the code or a message body."""
    from app.modules.commcalc import email_sweep as _es
    cands = []
    try:
        M = _es._connect_resilient(imap_cfg)
    except Exception as e:
        return {"code": None, "reason": f"mailbox unreachable: {type(e).__name__}"}
    try:
        # A 2FA code is minutes old; scan a short window rather than the sweep's since_days.
        cfg = dict(imap_cfg or {})
        cfg["since_days"] = 1
        for msg in _es._iter_messages(M, cfg):
            try:
                body = ""
                for part in _es._leaf_parts(msg):
                    ctype = (part.get_content_type() or "").lower()
                    if ctype not in ("text/plain", "text/html"):
                        continue
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode(part.get_content_charset() or "utf-8",
                                               errors="replace") + " "
                if not body.strip():
                    continue
                cands.append({"date": _msg_date(msg), "body": body,
                              "from": _es._decode(msg.get("From")),
                              "subject": _es._decode(msg.get("Subject"))})
            except Exception:
                continue                                   # one unreadable message never fails the read
    except Exception as e:
        return {"code": None, "reason": f"mailbox scan failed: {type(e).__name__}"}
    finally:
        try:
            M.logout()
        except Exception:
            pass
    r = dict(rules or {})
    r.setdefault("max_age_seconds", int(lookback_minutes) * 60)
    return code_from_messages(cands, r, now=now)


def _msg_date(msg):
    """The message's Date header as an aware datetime, or None (which pick_message then refuses)."""
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(msg.get("Date"))
        if d and d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None
