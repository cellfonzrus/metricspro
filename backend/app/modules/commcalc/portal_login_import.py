"""ONE HOME for "turn a pasted block of per-store portal logins into rows" (index §COMMCALC-83).

Owner, 2026-11-20, of the 29 per-store ePay back-office logins: *"Enter those for me"*.

WHY A PASTE AND NOT A SQL SCRIPT. Each of those logins is a credential for a system outside this
platform. The only place one may come to rest is the sealed column the credential registry names
(`core/portal_secret`), so the path from the operator's clipboard to that column must not pass
through a file, a commit, a log line or a chat message on the way. A pasted block posted once to the
existing writer is that path. There is no other reason this module exists: twenty-nine visits to a
form would have done, and been unusable.

WHAT THIS MODULE IS, PRECISELY. It is PURE — no DB, no network, no `sb()`, no logging. Text in,
rows out. It decides nothing about storage: the caller hands each row to the SAME single-login
writer the settings form uses (`router._save_source_row`), which is what seals the password, runs
the SSRF guard on the portal address and drops columns the database is behind on. A second writer
here would be the duplicate defect the house rules forbid, and it would be the one place where
"sealed" could silently stop being true.

A PASSWORD IS NEVER PART OF A VERDICT. Every function here returns, per line, what happened and why
— and the value is not in it, by construction rather than by care. `redacted_line()` is the one way
a line is ever quoted back to a human, and it is the only shape the endpoint is allowed to echo.

THREE STATES, NOT TWO (§COMMCALC-82's lesson, again). A line is `ready`, `skipped` (it says which
field is missing), or `unresolved` (its store is a spelling this org has not declared). An
unresolved line is NOT silently dropped and NOT guessed at: guessing would file a store's
credentials under another store, and dropping would read as "29 entered" when 23 were.

NO SECOND OPINION ABOUT WHICH STORE A WORD MEANS. "This spelling is that store" is already answered
once, for the whole platform, by `router._intake_store_resolver` over the org's union roster
(§13a) — alias, then address, then code, and `None` for anything that is not one of the org's own
stores. This module takes that `resolve` as an ARGUMENT and owns no matching of its own, which is
why a store renamed in the roster cannot mean one thing here and another everywhere else.
"""

#: The verdicts a line can carry. Three, deliberately — see the module docstring.
READY, SKIPPED, UNRESOLVED = "ready", "skipped", "unresolved"

#: The fields a pasted line may declare, in the order a header-less block is read in. `store` is
#: either a declared store CODE or a store name/address this org has declared; see `resolve_store`.
DEFAULT_ORDER = ("store", "username", "password")

#: Header spellings an operator might paste, folded to the field they mean. A header row is
#: OPTIONAL: a block with no header is read in `DEFAULT_ORDER`, which is the order the fields are
#: asked for on screen, so the two cannot disagree.
_HEADERS = {
    "store": "store", "store code": "store", "store_code": "store", "code": "store",
    "store name": "store", "location": "store", "site": "store",
    "username": "username", "user": "username", "user name": "username", "login": "username",
    "user id": "username", "userid": "username", "id": "username",
    "password": "password", "pass": "password", "pwd": "password",
    "label": "label", "name": "label", "description": "label",
}

#: What a line is split on, most specific first: a tab (what a spreadsheet paste gives), then a
#: pipe, then a comma. Spaces are NOT a separator — a store name and a label both contain them.
_SEPARATORS = ("\t", "|", ",")


def _clean(v):
    return " ".join(str(v or "").split())


def split_line(line):
    """PURE: one pasted line → its cells. The separator is whichever of `_SEPARATORS` the line
    actually contains, so one block may not mix them and a comma inside a store name cannot split a
    tab-separated paste."""
    s = str(line or "").rstrip("\r\n")
    for sep in _SEPARATORS:
        if sep in s:
            return [_clean(c) for c in s.split(sep)]
    return [_clean(s)] if _clean(s) else []


def header_fields(cells):
    """PURE: the fields a header row declares, or None when this is not a header row.

    A row is a header only when EVERY non-empty cell is a known header word. That matters: a store
    whose code happens to be "ID" must not turn its own data line into a header and vanish.
    """
    found = []
    for c in (cells or []):
        key = _clean(c).lower()
        if not key:
            continue
        if key not in _HEADERS:
            return None
        found.append(_HEADERS[key])
    return tuple(found) if found else None


def resolve_store(token, resolve):
    """PURE: a pasted store token → `(declared store code, how)`, or `("", "")` when this org has
    not declared that spelling.

    `resolve` is the platform's own `(code | None, how)` store resolver, passed in. The only thing
    this function adds is tolerating its two answer shapes and never letting a `None` become a
    store code — an unplaced spelling answers `("", "")`, which the caller reports as `UNRESOLVED`
    rather than filing a credential somewhere plausible.
    """
    t = _clean(token)
    if not t or resolve is None:
        return "", ""
    try:
        got = resolve(t)
    except Exception:
        return "", ""
    code, how = got if isinstance(got, (tuple, list)) else (got, "")
    return (_clean(code), _clean(how)) if code else ("", "")


def redacted_line(n, cells, fields):
    """PURE: how a line is quoted back to a human. The password cell is replaced by its LENGTH, so
    an operator can tell a line apart and see that something was read, and no part of the value
    exists in the answer."""
    out = []
    for i, c in enumerate(cells or []):
        field = fields[i] if i < len(fields) else ""
        out.append(("%d character password" % len(c)) if field == "password" and c else c)
    return {"line": n, "cells": out}


def parse(text, *, processor, portal_url="", resolve=None):
    """PURE: a pasted block → `{"rows": [...], "lines": [...], "ready": n, "skipped": n,
    "unresolved": n}`.

    `rows` are ready to hand, one at a time, to the single-login writer: `processor`, `store_code`,
    `username`, `password`, `label` and the shared `portal_url`. `lines` is the per-line account an
    operator reads, carrying the verdict, the reason, and never a credential.

    A blank line is not a line. A repeated store within one paste is `SKIPPED` naming the earlier
    line: two logins for one store would make "which login is this store's" a question with two
    answers, and the console's grid is keyed on the store.
    """
    fields = DEFAULT_ORDER
    rows, lines = [], []
    ready = skipped = unresolved = 0
    seen = {}
    for n, raw in enumerate(str(text or "").splitlines(), start=1):
        cells = split_line(raw)
        if not cells:
            continue
        hdr = header_fields(cells)
        if hdr and not rows and not lines:
            fields = hdr
            lines.append({"verdict": SKIPPED, "reason": "read as the header row",
                          **redacted_line(n, cells, ())})
            continue
        got = {}
        for i, c in enumerate(cells):
            if i < len(fields) and fields[i]:
                got[fields[i]] = c
        shown = redacted_line(n, cells, fields)
        missing = [f for f in ("store", "username", "password") if not _clean(got.get(f))]
        if missing:
            skipped += 1
            lines.append({"verdict": SKIPPED,
                          "reason": "no %s on this line" % " or ".join(missing), **shown})
            continue
        code, how = resolve_store(got["store"], resolve)
        if not code:
            unresolved += 1
            lines.append({"verdict": UNRESOLVED, **shown,
                          "reason": ("“%s” is not a store this organisation has declared — declare "
                                     "it (or paste its store code) and import again"
                                     % got["store"])})
            continue
        if code in seen:
            skipped += 1
            lines.append({"verdict": SKIPPED, **shown,
                          "reason": "store %s is already on line %d of this paste"
                                    % (code, seen[code])})
            continue
        seen[code] = n
        row = {"processor": str(processor or "").strip(), "store_code": code,
               "username": got["username"], "password": got["password"]}
        if _clean(got.get("label")):
            row["label"] = _clean(got["label"])
        if _clean(portal_url):
            row["portal_url"] = _clean(portal_url)
        rows.append(row)
        ready += 1
        lines.append({"verdict": READY, **shown,
                      "reason": "store %s%s" % (code, " (matched by %s)" % how if how else "")})
    return {"rows": rows, "lines": lines, "ready": ready, "skipped": skipped,
            "unresolved": unresolved}
