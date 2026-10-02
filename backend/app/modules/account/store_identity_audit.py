"""Store-identity audit — the ONE home for the invariant that every spelling of one physical store
collapses to ONE canonical key under the app's store resolver (`account.coa.store_resolver`, §13).

WHY THIS EXISTS — the class, not the instance
─────────────────────────────────────────────
Owner, 2026-10-02: B-1800's money and B-1115's money were each appearing twice, under two keys.

The instance was two bad rows. The CLASS is that a store's identity is asserted in THREE tables and
nothing required them to agree:

  • `storeops.stores`         — the roster: which stores exist (code, and sometimes an address)
  • `commcalc.store_mapping`  — the canonical ADDRESS per code; this is what `store_resolver` reads
  • `commcalc.store_aliases`  — POS spellings → code

`store_resolver`'s chain (exact address → alias → raw-is-a-code → unambiguous leading number) can
only COLLAPSE spellings onto an address that `store_mapping` already carries. So:

  • a `store_mapping` row whose address box holds the store CODE instead of a location
    (`B-1800` → `'B-1800'`) gives the resolver no address to collapse onto, and
  • a roster store with NO `store_mapping` row at all (`B-1115`) gives it nothing whatsoever —
    its alias is DROPPED, because the alias→address step needs the code to be in `store_mapping`.

In both shapes the store's spellings resolve to DIFFERENT keys and one store reads as two. That is
the defect, and it is invisible: nothing errors, the totals just split.

WHAT THIS MODULE IS
───────────────────
A PURE audit over the three tables' rows. It does not repair data and it does not resolve stores
itself — it takes the REAL `resolve` and reports where the invariant is broken, so the same fact is
checked by CI (against fixtures) and by the live runbook (against a tenant) without a second copy
of the rule. `audit()` returning `[]` IS the invariant.

THESE FINDINGS ARE INTERNAL DIAGNOSTICS, NOT DISPLAY COPY (§19.38). They deliberately name the
tables and columns at fault, because that is the whole point of a diagnosis — so the text is carried
under `diagnosis`, which is NOT one of `core.setup_notice.MESSAGE_KEYS`. A message-shaped key
(`detail`, `note`, `hint`, …) is a key whose value can reach a tenant through an API response, and
the §19.38 lock rightly fails the build on a table name under one. The first draft of this module
used `detail` and was caught by that lock.

So: do NOT return these findings to a tenant as they stand. They are for CI, the owner-run runbook
and the server log. A tenant-facing surface must render its own business-words sentence from `kind`
and `store_code` and drop the diagnosis.

The two row-shapes above are reported in their own right (`PLACEHOLDER_ADDRESS`,
`ROSTER_WITHOUT_MAPPING`) because they are the causes and are repairable by a data row; `SPLIT_KEYS`
is the consequence and is the finding that actually matters — a shape not yet enumerated here still
surfaces as a split.
"""

import re

PLACEHOLDER_ADDRESS = "placeholder_address"
ROSTER_WITHOUT_MAPPING = "roster_without_mapping"
SPLIT_KEYS = "split_keys"

FINDING_KINDS = (PLACEHOLDER_ADDRESS, ROSTER_WITHOUT_MAPPING, SPLIT_KEYS)


def _t(s):
    return str(s or "").strip()


def _squash(s):
    """Case-folded, alnum-only key — word boundaries DISSOLVED ('B-1800' == 'b1800' == 'B 1800').
    Used for matching a store CODE across spellings, never for judging an address (see `_key`)."""
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _key(s):
    """Case-folded, punctuation-normalized key with word boundaries PRESERVED: every run of
    non-alphanumerics becomes one space ('B-1800' -> 'b 1800', '1800 Great Neck Rd' -> '1800 great
    neck rd').

    The distinction from `_squash` is load-bearing and was found by the proof harness. The house has
    a store whose CODE was derived from its address by deleting the spaces — `1800GreatNeckRd` with
    address `'1800 Great Neck Rd'`. Under `_squash` those two are equal, so that row looked like a
    placeholder when it carries a perfectly good street address. Keeping the boundaries tells a code
    typed into the address box apart from an address the code was named after."""
    return " ".join(re.split(r"[^a-z0-9]+", str(s or "").lower())).strip()


def is_placeholder_address(store_code, store_address):
    """True when a `store_mapping` row carries NO real location: a blank address box, or an address
    box holding nothing but the store CODE.

    This is the one home for that rule. A placeholder is not a location, so the resolver has nothing
    to collapse the store's other spellings onto — see the module docstring.
    """
    addr, code = _t(store_address), _t(store_code)
    if not addr:
        return True
    a, c = _key(addr), _key(code)
    if a == c:
        return True
    # A single-token address that is the code with its punctuation changed ('b1800' for 'B-1800').
    # Guarded to single-token addresses so a multi-word street address is never caught here.
    return " " not in a and _squash(addr) == _squash(code)


def spellings_for_code(store_code, mapping_rows, store_rows, alias_rows):
    """Every raw string a feed could carry for this store: its code, its mapped address, its roster
    address, and each confirmed POS alias. Blank values are dropped; nothing is normalized — these
    are fed to the REAL resolver exactly as a feed would."""
    code = _t(store_code)
    out = [code] if code else []
    for r in mapping_rows or ():
        if _squash(r.get("store_code")) == _squash(code):
            out.append(_t(r.get("store_address")))
    for r in store_rows or ():
        if _squash(r.get("store_code")) == _squash(code):
            out.append(_t(r.get("address")))
    for r in alias_rows or ():
        if _squash(r.get("store_code")) == _squash(code):
            out.append(_t(r.get("alias")))
    seen, uniq = set(), []
    for s in out:
        if s and s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return uniq


def known_codes(mapping_rows, store_rows, alias_rows):
    """Every store code any of the three tables asserts, first spelling wins for display."""
    out = {}
    for rows, col in ((store_rows, "store_code"), (mapping_rows, "store_code"),
                      (alias_rows, "store_code")):
        for r in rows or ():
            c = _t(r.get(col))
            if c and _squash(c) not in out:
                out[_squash(c)] = c
    return [out[k] for k in sorted(out)]


def audit(resolve, mapping_rows, store_rows, alias_rows=()):
    """Report every place the store-identity invariant is broken. `[]` means it holds.

    `resolve` is the REAL resolver (`coa.store_resolver(client, org_id)`) — this never re-implements
    resolution, so the audit cannot drift from the thing it audits.

    Each finding: {kind, store_code, diagnosis, spellings, keys}. Deterministic order.
    """
    findings = []

    for r in mapping_rows or ():
        code = _t(r.get("store_code"))
        if code and is_placeholder_address(code, r.get("store_address")):
            findings.append({
                "kind": PLACEHOLDER_ADDRESS, "store_code": code,
                "diagnosis": ("store_mapping.store_address is %r — the store CODE, not a location, so "
                           "the resolver has no address to collapse this store's spellings onto"
                           % _t(r.get("store_address"))),
                "spellings": [], "keys": []})

    mapped = {_squash(r.get("store_code")) for r in (mapping_rows or ()) if _t(r.get("store_code"))}
    for r in store_rows or ():
        code = _t(r.get("store_code"))
        if code and _squash(code) not in mapped:
            findings.append({
                "kind": ROSTER_WITHOUT_MAPPING, "store_code": code,
                "diagnosis": ("on storeops.stores (address %r) with NO commcalc.store_mapping row, so "
                           "neither its address nor its aliases can resolve to it"
                           % _t(r.get("address"))),
                "spellings": [], "keys": []})

    for code in known_codes(mapping_rows, store_rows, alias_rows):
        sp = spellings_for_code(code, mapping_rows, store_rows, alias_rows)
        if len(sp) < 2:
            continue
        keys = {}
        for s in sp:
            k = _t(resolve(s)).lower()
            keys.setdefault(k, []).append(s)
        if len(keys) > 1:
            findings.append({
                "kind": SPLIT_KEYS, "store_code": code,
                "diagnosis": ("%d spellings of ONE store resolve to %d different canonical keys — its "
                           "money reads as %d stores" % (len(sp), len(keys), len(keys))),
                "spellings": sp, "keys": sorted(keys)})

    findings.sort(key=lambda f: (FINDING_KINDS.index(f["kind"]), f["store_code"]))
    return findings


def format_findings(findings):
    """Human-readable report — used by the live runbook and the harness alike."""
    if not findings:
        return "store identity: OK — every spelling of every store resolves to one canonical key"
    out = ["store identity: %d finding(s)" % len(findings)]
    for f in findings:
        out.append("  [%s] %s — %s" % (f["kind"], f["store_code"], f["diagnosis"]))
        if f["keys"]:
            out.append("      spellings: %s" % ", ".join(repr(s) for s in f["spellings"]))
            out.append("      keys:      %s" % ", ".join(repr(k) for k in f["keys"]))
    return "\n".join(out)
