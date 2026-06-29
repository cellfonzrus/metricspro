"""Generic email (IMAP) inbox sweep I/O — sibling of ftp_sweep.py. Pure IMAP/email parsing, no
app/DB deps (the router does the routing to parsers + processed-tracking, so no circular import).
Pulls attachments whose filename matches an fnmatch glob and returns their bytes; the router routes
each through the existing /upload pipeline exactly like the FTP sweep.
"""
import email
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header

from app.modules.commcalc.ftp_sweep import match_upload_type  # shared glob → upload_type logic


def _decode(s):
    """Decode an RFC2047-encoded header/filename to a plain str."""
    if not s:
        return ""
    parts = []
    for txt, enc in decode_header(s):
        if isinstance(txt, bytes):
            try:
                parts.append(txt.decode(enc or "utf-8", "replace"))
            except Exception:
                parts.append(txt.decode("utf-8", "replace"))
        else:
            parts.append(txt)
    return "".join(parts)


def _connect(cfg):
    host = (cfg.get("imap_host") or "").strip()
    if not host:
        raise ValueError("IMAP host not configured")
    port = int(cfg.get("imap_port") or (993 if cfg.get("use_ssl", True) else 143))
    user = (cfg.get("username") or "").strip()
    pw = cfg.get("password") or ""
    # Gmail / Yahoo / Outlook / iCloud App Passwords are SHOWN as 4 groups of 4 ("abcd efgh ijkl mnop"),
    # but IMAP wants the 16 characters with NO spaces — pasting them with the display spaces is the #1
    # cause of "[AUTHENTICATIONFAILED] Invalid credentials". Strip the spaces when it matches that shape
    # (16 alphanumerics once spaces are removed); otherwise just trim the ends so a normal password is safe.
    _nospace = pw.replace(" ", "")
    pw = _nospace if (" " in pw and len(_nospace) == 16 and _nospace.isalnum()) else pw.strip()
    if cfg.get("use_ssl", True):
        M = imaplib.IMAP4_SSL(host, port)
    else:
        M = imaplib.IMAP4(host, port)
        try:
            M.starttls()
        except Exception:
            pass
    M.login(user, pw)
    M.select((cfg.get("mailbox") or "INBOX"), readonly=True)
    return M


def _search_criteria(cfg):
    """Build IMAP search args: bound by SINCE <since_days> and optional FROM <from_filter>."""
    crit = []
    days = int(cfg.get("since_days") or 14)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    crit += ["SINCE", since]
    frm = (cfg.get("from_filter") or "").strip()
    if frm:
        crit += ["FROM", frm]
    return crit


def _iter_messages(M, cfg):
    """Yield (message_id, email.message.Message) for messages matching the search window."""
    typ, data = M.search(None, *_search_criteria(cfg))
    if typ != "OK" or not data or not data[0]:
        return
    for num in data[0].split():
        typ, msgdata = M.fetch(num, "(RFC822)")
        if typ != "OK" or not msgdata or not msgdata[0]:
            continue
        msg = email.message_from_bytes(msgdata[0][1])
        mid = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip() or f"uid-{num.decode()}"
        yield mid, msg


def _attachments(msg):
    """Yield (filename, payload_bytes) for every attachment part of a message."""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "")
        fname = part.get_filename()
        if not fname and "attachment" not in disp.lower():
            continue
        fname = _decode(fname)
        if not fname:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            continue
        yield fname, payload


def list_messages(cfg, limit=50):
    """For the test/preview: recent messages with from/subject/date + attachment filenames and which
    match a configured pattern. Read-only."""
    patterns = cfg.get("patterns") or []
    M = _connect(cfg)
    out = []
    try:
        for mid, msg in _iter_messages(M, cfg):
            atts = []
            for fname, payload in _attachments(msg):
                atts.append({"name": fname, "size": len(payload or b""),
                             "matches": match_upload_type(fname, patterns)})
            out.append({"from": _decode(msg.get("From")), "subject": _decode(msg.get("Subject")),
                        "date": msg.get("Date") or "", "attachments": atts})
            if len(out) >= limit:
                break
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out


def fetch_new_attachments(cfg, already):
    """Download every attachment matching a configured pattern that isn't already processed.
    `already` is a set of (message_id, filename). Returns [{message_id, name, size, upload_type, bytes}]."""
    patterns = cfg.get("patterns") or []
    M = _connect(cfg)
    out = []
    try:
        for mid, msg in _iter_messages(M, cfg):
            for fname, payload in _attachments(msg):
                ut = match_upload_type(fname, patterns)
                if not ut:
                    continue
                if (mid, fname) in already:
                    continue
                out.append({"message_id": mid, "name": fname, "size": len(payload or b""),
                            "upload_type": ut, "bytes": payload})
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out
