"""THE one home for "what format may an uploaded document be" (index §POS-16.3).

**Why it is here and not in a module.** The rule — sniff the MAGIC BYTES, never trust the
extension, check the result against the tenant's allow-list — was written inside `hr/router.py`
and reachable only from the three onboarding upload routes. A wholesale customer sending back a
completed W-9 (§POS-16) asks the identical question from a different module, and a second sniffer
there is how one upload path would come to accept a renamed executable while the other refused it.
One fact, one home, dereferenced.

**The load-bearing rule, stated once.** A file IS what its bytes say. The filename's extension can
never grant access: it is read only to word the refusal when the header matches nothing known. That
is deliberate — a renamed `.exe`, a shell script, a zip bomb, a `.txt` called `w9.pdf` all have no
PDF/JPEG/PNG signature, so none of them pass however they are named.

PURE: no IO, no app imports, stdlib only. The caller supplies the bytes, the name and the tenant's
allow-list (the rows it already read).
"""
from __future__ import annotations

#: The signature of each format this platform can recognise. A format absent from here cannot be
#: allowed by any tenant — an allow-list entry nothing can sniff accepts nothing, which is the safe
#: direction.
MAGIC = {
    "pdf": (lambda b: b[:5] == b"%PDF-"),
    "jpeg": (lambda b: b[:3] == b"\xff\xd8\xff"),
    "png": (lambda b: b[:8] == b"\x89PNG\r\n\x1a\n"),
}
#: Every format key, derived. Callers offering a settings picker read this rather than listing them.
FORMATS: tuple[str, ...] = tuple(MAGIC)
#: Extension spellings that mean the same format. Used ONLY to normalise an allow-list entry and to
#: label an unrecognised file in the refusal — never to decide that a file is acceptable.
EXT_ALIASES = {"jpg": "jpeg", "jpeg": "jpeg", "pdf": "pdf", "png": "png"}
#: What a tenant that has declared nothing accepts: a scan or a photo of a signed form.
DEFAULT_FORMATS: tuple[str, ...] = ("pdf", "jpeg")


def sniff(data: bytes):
    """The REAL format of these bytes, or None when the header matches nothing known."""
    head = (data or b"")[:16]
    for fmt, test in MAGIC.items():
        try:
            if test(head):
                return fmt
        except Exception:
            continue
    return None


def normalise(allowed) -> tuple[str, ...]:
    """A tenant's allow-list as format keys: aliases folded, blanks dropped, the declared default
    when nothing readable was declared (an empty list must not read as "anything goes")."""
    out = []
    for a in (allowed or ()):
        key = EXT_ALIASES.get(str(a).strip().lower(), str(a).strip().lower())
        if key and key not in out:
            out.append(key)
    return tuple(out) or DEFAULT_FORMATS


def allowed(data: bytes, filename: str, allow) -> tuple[bool, str | None]:
    """`(ok, detected)` — ok iff the bytes sniff to a format on the tenant's list.

    `detected` is the sniffed format when there is one; otherwise the EXTENSION's claim, carried
    only so the refusal can say what the file looked like. It is never why a file passes.
    """
    sniffed = sniff(data)
    if not sniffed:
        name = filename or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        return False, (EXT_ALIASES.get(ext, ext) or None)
    return sniffed in normalise(allow), sniffed


def label(formats) -> str:
    """How a list of formats is written for a person: `PDF/JPEG`."""
    return "/".join(f.upper() for f in normalise(formats))


def refusal_sentence(allow, detected) -> str:
    """The one wording of "we cannot take this file", so two upload screens cannot differ."""
    accepted = label(allow)
    if detected:
        looked = f" — this looks like a {str(detected).upper()} file"
    else:
        looked = " — this file's format could not be recognized"
    return f"Only {accepted} files are accepted here{looked}. Please upload a {accepted} file."
