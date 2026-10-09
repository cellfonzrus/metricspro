"""THE one home for "which store is this string?" — PURE, DB-free.

THE CLASS (owner directive 2026-10-09, verbatim: *"chase trhew street number matching"*).
**A leading address token is not a store identity.** Every money source spells a store in its own
hand — the carrier's payment detail writes `116-36 Springfield Blvd Cambria Heights, NY 11411`, the
roster writes `11636 Springfield Blvd`, the POS writes `2778 Ephraim Ave`, the relocation lives on
in an alias — and for years each report compared the FIRST SPACE-SEPARATED TOKEN of those strings.
That token is not an identity: it drops money whose token no store row happens to lead with
(measured live, house org, Jul–Oct 2026: `116-36` every month, `2778` once the POS feed stopped
spelling it that way), it cannot tell `116-36` from `11636`, and when two store rows share a token
it picks the last one the loop happened to see — which is how a store's residual went to the row
with no Salesforce door on it.

So identity is resolved ONCE, here, against the spellings the org has actually DECLARED (its
`store_mapping` addresses, its `store_aliases`, its store codes), and a string nothing can place
resolves to itself rather than to a guess. `account.coa.store_resolver` is the I/O wrapper that
reads those two tables and returns `build_store_resolver`'s function; every caller takes the
callable and never re-derives the chain. No carrier, tenant, store or product name appears in this
file (RULE TWO) — every spelling it matches comes from the org's own config rows.

Enforced by `backend/harness_store_identity_lock.py`: a caller that stops dereferencing this home,
or a second leading-token join appearing anywhere in the backend, FAILS THE BUILD.
"""

__all__ = [
    "squash_key", "lead_num_key", "build_store_resolver", "store_key",
    "store_identity_index", "ambiguous_identities",
]


def _norm(v):
    return (str(v or "").strip()) or None


def squash_key(v):
    """UPPER alphanumeric-only spelling key — `'4640-A W Diversey Ave' == '4640a  w diversey ave'`.
    The same folding idea as `core.scope._squash` and `coa._squash_key`."""
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def lead_num_key(v):
    """The leading street number, digits only, so `'116-36 Springfield Blvd'` and
    `'11636 Springfield Blvd'` share a key. None when the lead token is not numeric (a store CODE
    like `'B-1800'`, or a name) — those only ever match exactly, never by number.

    This is NOT an identity. It is the LAST and weakest step of the chain below, it fires only when
    exactly one declared spelling claims the number, and it exists only because carriers print a
    street number the roster spells differently."""
    s = str(v or "").strip()
    tok = s.split(" ")[0] if s else ""
    if not tok or not tok[:1].isdigit():
        return None
    return "".join(ch for ch in tok if ch.isdigit()) or None


def build_store_resolver(mapping_rows, alias_rows=None):
    """PURE twin of `coa.store_resolver`: (store_mapping rows, store_aliases rows) -> resolve(raw).

    `resolve(raw)` returns the org's CANONICAL store address — the `store_mapping.store_address`
    spelling — for any string a feed carries, or the cleaned raw string when nothing can place it
    (never None for a non-empty input, never a guess). The chain, strongest first:

      1. exact `store_mapping.store_address` (case-insensitive)
      2. exact `store_aliases.alias` (case-insensitive)             — what the Store-Matching screen writes
      3. the raw string IS a `store_mapping.store_code`
      4. SQUASHED address spelling (case / punctuation / spacing)   [added 2026-10-09]
      5. SQUASHED alias spelling                                    [added 2026-10-09]
      6. UNAMBIGUOUS leading street number of a declared ADDRESS
      7. UNAMBIGUOUS leading street number of a declared ALIAS      [added 2026-10-09]
      8. nothing → the cleaned raw string

    Steps 4–7 fire ONLY when a single canonical address claims the key; two different stores
    claiming one key resolves to NOTHING rather than to an arbitrary winner (street numbers are not
    unique — `'3 Palisade Ave'` and a hypothetical `'3 Broadway'` both lead with `3`). Steps 1–3 and
    6 are the historical chain and are byte-identical. Steps 4, 5 and 7 are strictly ADDITIVE: they
    can only fire where the chain previously fell through to the raw string, because every earlier
    step still wins — 4/5 are a tighter match than 6/7, and 6 keeps precedence over 7.

    A code-keyed step never merges two stores: it lands on an address `store_mapping` already holds,
    so this can only collapse SPELLINGS of a known store, never invent a merge."""
    addr_by_addr, addr_by_code = {}, {}
    num_addrs, squash_addrs = {}, {}
    for r in (mapping_rows or []):
        addr = _norm((r or {}).get("store_address"))
        code = _norm((r or {}).get("store_code"))
        if addr:
            addr_by_addr[addr.lower()] = addr
            nk = lead_num_key(addr)
            if nk:
                num_addrs.setdefault(nk, set()).add(addr)
            sk = squash_key(addr)
            if sk:
                squash_addrs.setdefault(sk, set()).add(addr)
        if addr and code:
            addr_by_code[code.upper()] = addr

    alias_addr, alias_nums, alias_squash = {}, {}, {}
    for r in (alias_rows or []):
        al, code = _norm((r or {}).get("alias")), _norm((r or {}).get("store_code"))
        if not (al and code):
            continue
        target = addr_by_code.get(code.upper())
        if not target:
            continue                      # an alias for a code with no address resolves nothing
        alias_addr[al.lower()] = target
        nk = lead_num_key(al)
        if nk:
            alias_nums.setdefault(nk, set()).add(target)
        sk = squash_key(al)
        if sk:
            alias_squash.setdefault(sk, set()).add(target)

    def _unambiguous(d):
        return {k: next(iter(v)) for k, v in d.items() if len(v) == 1}

    addr_by_num = _unambiguous(num_addrs)
    addr_by_squash = _unambiguous(squash_addrs)
    alias_by_num = _unambiguous(alias_nums)
    alias_by_squash = _unambiguous(alias_squash)

    def resolve(raw):
        s = _norm(raw)
        if not s:
            return None
        low = s.lower()
        if low in addr_by_addr:
            return addr_by_addr[low]
        if low in alias_addr:
            return alias_addr[low]
        if s.upper() in addr_by_code:
            return addr_by_code[s.upper()]
        sk = squash_key(s)
        if sk and sk in addr_by_squash:
            return addr_by_squash[sk]
        if sk and sk in alias_by_squash:
            return alias_by_squash[sk]
        nk = lead_num_key(s)
        if nk and nk in addr_by_num:
            return addr_by_num[nk]
        if nk and nk in alias_by_num:
            return alias_by_num[nk]
        return s

    return resolve


def store_key(resolve, raw) -> str:
    """THE key a row is grouped and joined on: the canonical store spelling, never the raw string
    and never a token of it. `resolve` is the callable `build_store_resolver` / `coa.store_resolver`
    returns, handed in because this module does no I/O.

    `resolve` None, or a string the resolver cannot place, falls back to the stripped raw value — so
    an unknown store still groups with itself and never with another, and the money stays visible on
    a row of its own instead of being dropped."""
    raw_s = str(raw or "").strip()
    if not raw_s:
        return ""
    if resolve is None:
        return raw_s
    try:
        return str(resolve(raw_s) or raw_s).strip() or raw_s
    except Exception:
        return raw_s


def store_identity_index(mapping_rows, resolve=None) -> dict:
    """PURE: `store_mapping` rows -> {CANONICAL store_address: identity}, where identity is
    `{'store': …, 'store_code', 'market', 'salesforce_id', 'codes': [...], 'ambiguous': bool}`.

    WHY THIS EXISTS, measured live (house org, 2026-10-09): THREE canonical addresses are claimed by
    TWO `store_mapping` rows each — a relocation, a re-code and a rogue code — and in exactly one of
    each pair the `salesforce_id` (the carrier's door, which the residual feed joins on) is NULL. A
    `{token: row}` loop keeps whichever row it saw LAST, so a coin-flip decided whether that store's
    residual could be joined at all. §13d's `store_identity_audit` reports those pairs for the owner
    to merge; until a merge happens this index must still answer deterministically, so:

      • the identity is folded field-by-field, first NON-EMPTY wins;
      • the rows are ordered so a row carrying a `salesforce_id` is considered first (the row the
        CARRIER knows is the live store), then by `store_code` — total and reproducible, never
        insertion order;
      • every claiming code is kept in `codes` and `ambiguous` is True, so the collision is
        REPORTED rather than silently resolved.

    An explicitly inactive row (`is_active is False`) is skipped, matching the report's own
    NULL-safe `is_active` predicate. `resolve` None keeps the raw mapping spelling as the key."""
    rows = []
    for r in (mapping_rows or []):
        if (r or {}).get("is_active") is False:
            continue
        addr = _norm((r or {}).get("store_address"))
        if not addr:
            continue
        rows.append(r)
    rows.sort(key=lambda r: (0 if _norm(r.get("salesforce_id")) else 1,
                             str(r.get("store_code") or "")))
    out: dict = {}
    for r in rows:
        key = store_key(resolve, r.get("store_address"))
        if not key:
            continue
        ent = out.setdefault(key, {"store": key, "store_code": "", "market": "",
                                   "salesforce_id": "", "codes": [], "ambiguous": False})
        for field in ("store_code", "market", "salesforce_id"):
            if not ent[field]:
                ent[field] = str(r.get(field) or "").strip()
        code = str(r.get("store_code") or "").strip()
        if code and code not in ent["codes"]:
            ent["codes"].append(code)
    for ent in out.values():
        ent["ambiguous"] = len(ent["codes"]) > 1
    return out


def ambiguous_identities(index) -> list:
    """PURE: the §13d evidence rows out of `store_identity_index` — one physical store spelled by
    two codes, named so a report can say so instead of hiding the pick it made."""
    return [{"store": k, "store_code": v.get("store_code"), "codes": list(v.get("codes") or [])}
            for k, v in sorted((index or {}).items()) if v.get("ambiguous")]
