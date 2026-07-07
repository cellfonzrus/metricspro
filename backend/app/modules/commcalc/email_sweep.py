"""Generic email (IMAP) inbox sweep I/O — sibling of ftp_sweep.py. Pure IMAP/email parsing, no
app/DB deps (the router does the routing to parsers + processed-tracking, so no circular import).
Pulls attachments whose filename matches an fnmatch glob and returns their bytes; the router routes
each through the existing /upload pipeline exactly like the FTP sweep.

Extraction is deliberately tolerant (the recurring "the email arrives but the file won't come out"
class of bug): it unwraps .zip attachments, recovers spreadsheet/CSV parts that arrive WITHOUT a
filename (inline / octet-stream), and descends nested forwarded messages. list_messages() also returns
a full MIME part inventory so Test connection can SHOW what is inside an email even when nothing matched.
"""
import email
import imaplib
import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from email.header import decode_header

from app.modules.commcalc.ftp_sweep import match_upload_type  # shared glob → upload_type logic

# Extensions the downstream /upload pipeline can actually read.
_DATA_EXTS = (".xlsx", ".xlsm", ".xlsb", ".xls", ".csv", ".txt", ".tsv")
# Content-Types that mean "this part is a spreadsheet/CSV" even when it has no filename.
_DATA_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12",
    "text/csv", "application/csv", "application/x-csv",
    "text/comma-separated-values", "text/tab-separated-values",
}


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


def _ext_from_magic(payload):
    """Best-guess file extension from the leading magic bytes."""
    if not payload:
        return ""
    if payload[:4] == b"PK\x03\x04":       # ZIP container (xlsx is one too)
        return ".xlsx"
    if payload[:4] == b"\xD0\xCF\x11\xE0":  # OLE2 compound doc (legacy .xls)
        return ".xls"
    return ".csv"


def _ext_for_mime(ctype):
    ctype = (ctype or "").lower()
    if "spreadsheetml" in ctype:
        return ".xlsx"
    if "ms-excel" in ctype:
        return ".xls"
    if "tab-separated" in ctype:
        return ".tsv"
    if "csv" in ctype or "comma-separated" in ctype:
        return ".csv"
    return ""


def _looks_like_data(fname, ctype, payload):
    """Is this part a spreadsheet/CSV we can ingest — by extension, MIME, or magic bytes?
    Used ONLY for parts that arrive without a filename (so we never misread an email body as a file)."""
    if (fname or "").lower().endswith(_DATA_EXTS):
        return True
    if (ctype or "").lower() in _DATA_MIMES:
        return True
    if payload and payload[:4] in (b"PK\x03\x04", b"\xD0\xCF\x11\xE0"):
        return True
    return False


def _sniff_csv(payload):
    """Last resort for a nameless octet-stream attachment: does its head decode to delimited text?
    (A real CSV mailed with no filename and no text/csv MIME — otherwise indistinguishable from binary.)"""
    if not payload or payload[:4] in (b"PK\x03\x04", b"\xD0\xCF\x11\xE0"):
        return False
    head = payload[:4096]
    txt = None
    for enc in ("utf-8", "latin-1"):
        try:
            txt = head.decode(enc)
            break
        except Exception:
            continue
    if not txt or ("\n" not in txt and "\r" not in txt):
        return False
    if not any(d in txt for d in (",", "\t", ";")):
        return False
    printable = sum(1 for c in txt if c.isprintable() or c in "\r\n\t")
    return printable / max(len(txt), 1) > 0.9


def _is_zip_archive(fname, payload):
    """True when a part is a real .zip we should unwrap — NOT an .xlsx (which is also a PK zip)."""
    fl = (fname or "").lower()
    if fl.endswith((".xlsx", ".xlsm")):
        return False
    if fl.endswith(".zip"):
        return True
    if payload[:4] == b"PK\x03\x04" and not fl.endswith(_DATA_EXTS):
        # Nameless PK container — a bare xlsx (has xl/ + [Content_Types].xml) or a real archive. Peek.
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                names = z.namelist()
            if any(n == "[Content_Types].xml" or n.startswith("xl/") for n in names):
                return False
            return True
        except Exception:
            return False
    return False


def _unzip_data(payload):
    """Yield (inner_filename, inner_bytes) for every ingestible data file inside a .zip attachment."""
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                base = n.split("/")[-1]
                if base.lower().endswith(_DATA_EXTS):
                    try:
                        out.append((base, z.read(n)))
                    except Exception:
                        continue
    except Exception:
        pass
    return out


def _leaf_parts(msg):
    """Yield a descriptor for every leaf MIME part (skipping multipart/message containers, which
    walk() descends into anyway). Basis for both extraction and the Test-connection diagnostics."""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_type() == "message/rfc822":
            continue  # container: walk() already yields its embedded message's parts
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        disp = (part.get("Content-Disposition") or "")
        yield {
            "content_type": (part.get_content_type() or "").lower(),
            "disposition": disp,
            "filename": _decode(part.get_filename()),
            "payload": payload,
            "size": len(payload or b""),
        }


def _attachments(msg):
    """Yield (filename, payload_bytes) for every ingestible attachment in a message.

    Tolerant by design — this is where "the email is here but the file won't extract" gets fixed:
      • real .zip attachments are unwrapped and each inner data file is yielded on its own;
      • a spreadsheet/CSV part with NO filename (inline / octet-stream) is recovered and given a
        synthesized name so the pattern rules + parser have something to work with;
      • any normally-named attachment flows through exactly as before (no regression to Boost).
    """
    idx = 0
    for leaf in _leaf_parts(msg):
        payload = leaf["payload"]
        if not payload:
            continue
        fname = leaf["filename"]
        ctype = leaf["content_type"]
        disp = (leaf["disposition"] or "").lower()

        # (1) A real .zip → unwrap and yield the inner data files.
        if _is_zip_archive(fname, payload):
            for zn, zb in _unzip_data(payload):
                yield zn, zb
            continue

        # (2) Any named attachment flows through unchanged (pattern-match filters it downstream).
        if fname:
            yield fname, payload
            continue

        # (3) NEW: a nameless part that is clearly a spreadsheet/CSV — by MIME, magic bytes, or (for an
        #     explicitly-marked attachment) a text sniff — gets a synthesized name so it can match + parse.
        if _looks_like_data(fname, ctype, payload):
            idx += 1
            ext = _ext_for_mime(ctype) or _ext_from_magic(payload)
            yield f"attachment-{idx}{ext}", payload
        elif "attachment" in disp and _sniff_csv(payload):
            idx += 1
            yield f"attachment-{idx}.csv", payload


def _part_inventory(msg):
    """Human-readable list of every leaf part (for Test-connection diagnostics). The email body
    (text/plain, text/html) is collapsed to a single 'body' note so the file parts stand out."""
    parts, bodies = [], 0
    for leaf in _leaf_parts(msg):
        ct = leaf["content_type"]
        if ct in ("text/plain", "text/html") and not leaf["filename"]:
            bodies += 1
            continue
        parts.append({
            "content_type": ct,
            "filename": leaf["filename"] or "(no filename)",
            "size": leaf["size"],
            "disposition": (leaf["disposition"] or "").split(";")[0].strip() or "(none)",
        })
    if bodies:
        parts.append({"content_type": "text/*", "filename": "(message body)", "size": 0,
                      "disposition": f"{bodies} body part(s)"})
    return parts


def list_messages(cfg, limit=50):
    """For the test/preview: recent messages with from/subject/date + the ingestible files we can
    extract (with pattern match) AND a full MIME part inventory so a non-matching email is diagnosable.
    Read-only."""
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
                        "date": msg.get("Date") or "", "attachments": atts,
                        "parts": _part_inventory(msg)})
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
            atts = list(_attachments(msg))
            # b2bsoft attaches BOTH an .xlsx (the good one) and a same-named .csv that ingests 0 rows and
            # errors on every hourly email — pure noise. When a .csv has a same-stem .xlsx/.xls sibling in
            # the SAME message, skip the .csv (the .xlsx wins). A .csv with no Excel sibling still ingests.
            xlsx_stems = {os.path.splitext(f)[0].lower() for f, _ in atts if f.lower().endswith((".xlsx", ".xls"))}
            for fname, payload in atts:
                if fname.lower().endswith(".csv") and os.path.splitext(fname)[0].lower() in xlsx_stems:
                    continue
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
