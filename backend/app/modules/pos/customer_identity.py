"""WHO THE CUSTOMER IS — the one home for how a customer is recognised across uploads, receipts and the POS.
PURE (stdlib).

OWNER (2026-09-24): *"these customers which are uploaded should be available in the pos to make the sales in
future and if any data is later uploaded for the same customer the system to check for existing fields to
match the data and update their record rather than creating a new record"*.

MEASURED (live, the first rebuild of 23 invoices): every receipt CREATED a customer — the matcher selected a
`notes` column mig 725 never created, the select failed, the failure read as "not found" — and the POS's
placeholder bill-to names ('Walk In' ×4) became customers. This module holds the facts every matcher needs:

  · `norm_name` — one spelling of a person's name for comparison (case, spaces, punctuation);
  · `is_placeholder` — a bill-to that names nobody (the house words below, generic, plus the org's own list
    from config) never becomes, and never matches, a customer.

The words below are generic POS placeholders, not a carrier, tenant or product name (RULE TWO). An org adds
its own through `pos.pos_settings` key CONFIG_KEY (`{"placeholders": [...]}`), read by `resolve_config`.

THE CUSTOMER MASTER (owner 2026-09-24, index §30.16) adds, still pure:
  · `norm_phone` — THE phone key of every customer match (a device id is not a phone; < 10 digits is not a
    phone; else `crm.pipeline_core.normalize_phone`, the national number the CRM already keys on);
    `tracking_kind` — a line's tracking # is a phone number OR a device id;
  · `decide` — THE match decision (phone + name → phone within 2 years under another name, combined →
    the name alone → create; a placeholder never), over plain candidate rows;
  · `fill_patch` — what an upload may write onto an existing customer: EMPTY fields only;
  · `invoice_lines` — one invoice's phone lines, each with its device and plan (the pairing rule and the plan
    predicate INJECTED — `inventory_sold_recon.line_pairings`, `plan_sources.line_matches`).
"""
from __future__ import annotations

import re

CONFIG_KEY = "customer_identity"
HOUSE_PLACEHOLDERS = ("walk in", "walkin", "walk in customer", "no customer", "no name", "cash customer", "cash sale",
                      "guest", "customer", "unknown", "n a", "na", "none", "test")


def _s(v):
    return "" if v is None else str(v).strip()


def norm_name(v):
    """A name for comparison: lower case, punctuation → space, runs of spaces → one."""
    return " ".join(re.sub(r"[^\w]+", " ", _s(v).lower()).split())


def resolve_config(raw):
    """The org's row value → {'placeholders': [normalised words]} (house words + the org's own)."""
    v = raw if isinstance(raw, dict) else {}
    extra = [norm_name(x) for x in (v.get("placeholders") or []) if norm_name(x)]
    words = list(HOUSE_PLACEHOLDERS)
    for w in extra:
        if w not in words:
            words.append(w)
    return {"placeholders": words}


def is_placeholder(name, config=None):
    """True for a bill-to that names nobody: empty, or one of the placeholder words (house + org)."""
    n = norm_name(name)
    if not n:
        return True
    words = (config or {}).get("placeholders") or HOUSE_PLACEHOLDERS
    return n in words


def split_name(full):
    """'First Middle Last' → ('First', 'Middle Last') on single spaces (the stored first / last name)."""
    parts = _s(full).split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def same_name(row, first, last):
    """Does a stored customer row carry this first + last name (normalised both sides)?"""
    return norm_name(row.get("first_name")) == norm_name(first) and norm_name(row.get("last_name")) == norm_name(last)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE CUSTOMER MASTER (owner 2026-09-24; index §30.16) — one phone rule, one match decision, one fill rule,
# one per-invoice line rule. PURE: plain rows in, plain dicts out; the I/O lives in receipt_import (the
# matcher) and pos/customer_master (lines, merge, the customer page's payloads).
#
# OWNER, verbatim: *"if any data is later uploaded for the same customer the system to check for existing
# fields to match the data and update their record rather than creating a new record, the matching will be
# based on customer name address and phone numbers, - phone number and name to be checked first for all, if
# in the last 2 years data a customer came in twice to get phones on different names they should be combined
# together … each customer will bear separate lines with individual phone numbers in line with their imei and
# plan"*.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.crm.pipeline_core import normalize_phone as _national   # noqa: E402 — THE national-number rule (pure; mig 800's SQL twin)
from app.modules.pos.receipt_formats.base import is_serial as _is_device  # noqa: E402 — THE 14–16-digit device-id rule (pure)

WINDOW_DAYS = 730          # "in the last 2 years" — a shared phone line combines two names only inside this window
MIN_PHONE_DIGITS = 10      # fewer digits names no phone line (a fragment never matches a customer)

RULE_PLACEHOLDER = "placeholder"
RULE_EMPTY = "empty"
RULE_PHONE_NAME = "phone_and_name"          # (a)
RULE_PHONE_COMBINE = "phone_combined"       # (b)
RULE_NAME = "name"                          # (c)
RULE_NAME_AMBIGUOUS = "name_ambiguous"      # (c) several carry the name and nothing tells them apart
RULE_CREATE = "create"                      # (d)


def _digits(v):
    return re.sub(r"\D", "", _s(v))


def norm_phone(v):
    """THE phone key every customer match compares (one rule, dereferenced — never copied): a device id
    (14–16 digits, `receipt_formats.base.is_serial`) is NOT a phone; fewer than 10 digits is not a phone;
    otherwise the national 10-digit number by `crm.pipeline_core.normalize_phone` (a leading US 1 dropped; an
    extension typed at the end kept off — a naive last-10 would turn '5165550134 x22' into another number).
    None when it is not a phone."""
    d = _digits(v)
    if len(d) < MIN_PHONE_DIGITS or _is_device(d):
        return None
    n = _national(v)
    return n if len(n) == MIN_PHONE_DIGITS else None


def tracking_kind(v):
    """A sale line's tracking # holds EITHER a phone number (the line's MDN) OR a device id (the IMEI):
    ('phone', mdn) | ('device', imei) | (None, None)."""
    d = _digits(v)
    if d and _is_device(d):
        return ("device", d)
    p = norm_phone(v)
    return ("phone", p) if p else (None, None)


def norm_address(v):
    """An address for comparison (a customer row's address_1 / address_2 / city / state / zip, or a string):
    `norm_name` over it. '' when there is none."""
    if isinstance(v, dict):
        v = " ".join(_s(v.get(k)) for k in ("address_1", "address_2", "city", "state", "zip"))
    return norm_name(v)


def display_name(row):
    """A stored customer row's full name as one string (first + last, else the company name)."""
    r = row or {}
    full = " ".join(x for x in (_s(r.get("first_name")), _s(r.get("last_name"))) if x)
    return full or _s(r.get("company_name"))


def _date(v):
    import datetime as _d
    try:
        return _d.date.fromisoformat(_s(v)[:10])
    except ValueError:
        return None


def days_apart(a, b):
    """|a − b| in days for two ISO dates; None when either is unknown."""
    da, db = _date(a), _date(b)
    if not da or not db:
        return None
    return abs((da - db).days)


def _names_of(c):
    out = {norm_name(c.get("name"))} | {norm_name(a) for a in (c.get("aliases") or ())}
    out.discard("")
    return out


def _latest(dates):
    ds = sorted((_s(d)[:10] for d in dates if _s(d)), reverse=True)
    return ds[0] if ds else None


def decide(incoming, candidates, window_days=WINDOW_DAYS, config=None):
    """THE MATCH DECISION — who an incoming sale's customer is, over plain rows. PURE.

    incoming   = {"name": str, "phones": [numbers], "date": "YYYY-MM-DD" | None, "address": str | None}
    candidates = [{"id", "name", "aliases": [names], "address": str, "merged_into": id | None,
                   "created_at": str, "phones": {mdn: last_seen "YYYY-MM-DD" | None}}]
                 (the I/O hands every customer that shares a phone line OR carries the name / an alias;
                 a merged-away customer is ignored — its lines and names already live on the survivor)

    The owner's order — phone number and name first, for all:
      (a) a phone line in common AND the same name (or one of its aliases)                  → match
      (b) a phone line in common, a DIFFERENT name, and that line last seen within `window_days`
          of this sale (either side)                                                         → match: combined,
          the incoming name becomes an alias. Older than the window → the number was reassigned: it names
          nobody here (listed in `reassigned`)
      (c) no usable phone line: the same name on exactly one customer — an address on both sides that
          differs rules a candidate out, one that agrees picks among several                  → match; several
          still alike → the first created (rule `name_ambiguous`, said: merge them on the customer page)
      (d) otherwise                                                                          → create
    A placeholder bill-to ('Walk In' — `is_placeholder`) never matches and never creates (action `none`).
    Returns {"action": "match"|"create"|"none", "customer_id", "rule", "reason", "alias_to_add", "reassigned"}."""
    inc = incoming or {}
    name = _s(inc.get("name"))
    nn = norm_name(name)
    phones = []
    for p in inc.get("phones") or ():
        q = norm_phone(p)
        if q and q not in phones:
            phones.append(q)
    date = _s(inc.get("date"))[:10] or None
    addr = norm_address(inc.get("address"))
    out = {"action": "none", "customer_id": None, "rule": None, "reason": "", "alias_to_add": None, "reassigned": []}
    if is_placeholder(name, config):
        out.update(rule=RULE_PLACEHOLDER if nn else RULE_EMPTY,
                   reason=(f"'{name}' is a placeholder bill-to — it names nobody, so the sale keeps no customer"
                           if nn else "the sale names no customer"))
        return out
    live = [c for c in (candidates or []) if c and c.get("id") and not c.get("merged_into")]

    def overlap(c):
        ph = c.get("phones") or {}
        return {m: ph.get(m) for m in phones if m in ph}

    def most_recent(cs):
        return sorted(cs, key=lambda c: (_latest(overlap(c).values()) or "", _s(c.get("created_at")), _s(c.get("id"))), reverse=True)[0]

    # (a) a phone line + the name
    a = [c for c in live if overlap(c) and nn in _names_of(c)]
    if a:
        c = most_recent(a)
        out.update(action="match", customer_id=c["id"], rule=RULE_PHONE_NAME,
                   reason=f"phone line {', '.join(sorted(overlap(c)))} and the name '{name}' are already this customer's")
        return out
    # (b) a phone line within the window under another name → combined
    recent, stale, undated = [], [], []
    for c in live:
        ov = overlap(c)
        if not ov:
            continue
        gaps = [days_apart(d, date) for d in ov.values()]
        if any(g is not None and g <= window_days for g in gaps):
            recent.append(c)
        elif any(g is None for g in gaps):
            undated.append(c)
        else:
            stale.append(c)
    out["reassigned"] = sorted({m for c in stale for m in overlap(c)})
    if recent:
        c = most_recent(recent)
        ov = overlap(c)
        out.update(action="match", customer_id=c["id"], rule=RULE_PHONE_COMBINE,
                   alias_to_add=name if nn not in _names_of(c) else None,
                   reason=(f"phone line {', '.join(sorted(ov))} was last seen on '{c.get('name')}' on {_latest(ov.values())}, within "
                           f"{window_days} days of this sale — the same customer under another name: combined, '{name}' kept as an alias"))
        return out
    # (c) the name alone
    named = [c for c in live if nn in _names_of(c)]
    if addr:
        named = [c for c in named if not norm_address(c.get("address")) or norm_address(c.get("address")) == addr]
        same_addr = [c for c in named if norm_address(c.get("address")) == addr]
        if len(named) > 1 and len(same_addr) == 1:
            named = same_addr
    if len(named) == 1:
        c = named[0]
        out.update(action="match", customer_id=c["id"], rule=RULE_NAME,
                   reason=f"the name '{name}' is this customer's" + (" (no phone line in common)" if phones else ""))
        return out
    if len(named) > 1:
        c = sorted(named, key=lambda c: (_s(c.get("created_at")), _s(c.get("id"))))[0]
        out.update(action="match", customer_id=c["id"], rule=RULE_NAME_AMBIGUOUS,
                   reason=(f"{len(named)} customers carry the name '{name}' and nothing tells them apart — matched the first created; "
                           "merge or correct them on the customer page"))
        return out
    # (d) a new customer
    why = []
    if out["reassigned"]:
        why.append(f"phone line {', '.join(out['reassigned'])} was last seen under another name more than {window_days} days "
                   "before this sale — the number was reassigned")
    if undated:
        why.append("a shared phone line carries no date on one side, so two different names are not combined on it")
    out.update(action="create", rule=RULE_CREATE,
               reason="no customer carries this phone line or name — a new customer" + (": " + "; ".join(why) if why else ""))
    return out


def fill_patch(row, incoming):
    """What an upload may WRITE onto an existing customer: only fields that are EMPTY — a filled field is never
    overwritten with a different value (a different name becomes an alias; a new number the secondary phone
    when that is empty, else it lives on the customer's lines). PURE. Returns {column: value}."""
    r = row or {}
    inc = incoming or {}
    patch = {}
    have = {norm_phone(r.get("phone_primary")), norm_phone(r.get("phone_secondary"))} - {None}
    new = []
    for p in inc.get("phones") or ():
        q = norm_phone(p)
        if q and q not in have and q not in new:
            new.append(q)
    if not _s(r.get("phone_primary")) and new:
        patch["phone_primary"] = new.pop(0)
    if not _s(r.get("phone_secondary")) and new:
        patch["phone_secondary"] = new.pop(0)
    if not _s(r.get("email")) and _s(inc.get("email")):
        patch["email"] = _s(inc.get("email"))
    if not any(_s(r.get(k)) for k in ("address_1", "city", "zip")) and _s(inc.get("address")):
        patch["address_1"] = _s(inc.get("address"))
    return patch


def invoice_lines(rows, line_pairings, device_key, plan_line=None):
    """ONE INVOICE'S PHONE LINES — one entry per distinct phone number (MDN) in the lines' tracking #, each with
    the device (IMEI) paired to it and its plan. PURE; the pairing and the plan predicate are INJECTED —
    `inventory_sold_recon.line_pairings` (THE one device ↔ phone-number rule) and `plan_sources.line_matches`
    bound to the org's `sales_lines` words — never re-implemented here.

    Pairing: the tracking # values are split by `tracking_kind`; each MDN is paired by `line_pairings` through
    its (invoice, contract #) group — the register's Contract Details pairs a phone line and its device under
    one contract — and, when that group holds no device, through the whole invoice ('same transaction').
    Several devices → unpaired with the rule's own reason (never guessed). Plan: a plan line whose tracking #
    is the MDN, else the only plan name in its contract group, else the only plan name on the invoice tied to no
    number and no contract, else '' (a plan tied to ANOTHER number is never borrowed).
    Returns [{"mdn", "imei", "pairing", "via", "reason", "plan", "device_name", "contract_no", "contract_type"}]
    ordered by MDN; [] when the invoice names no phone number."""
    rows = [r or {} for r in (rows or [])]
    inv = next((_s(r.get("trans_id")) for r in rows if _s(r.get("trans_id"))), "invoice")
    parsed = []                                    # (row, kind, value, contract #) — a separate `mdn` column counts as a phone line too
    for r in rows:
        k, v = tracking_kind(r.get("serial_1"))
        col = norm_phone(r.get("mdn"))
        if k == "device" and col:                  # one line naming its device AND its number: paired on the same line
            parsed.append((r, "both", (v, col), _s(r.get("contract_no"))))
        elif k is None and col:
            parsed.append((r, "phone", col, _s(r.get("contract_no"))))
        else:
            parsed.append((r, k, v, _s(r.get("contract_no"))))

    def dev(k, v):
        return v[0] if k == "both" else (v if k == "device" else None)

    def mob(k, v):
        return v[1] if k == "both" else (v if k == "phone" else None)

    mdns = sorted({mob(k, v) for _r, k, v, _c in parsed if mob(k, v)})
    if not mdns:
        return []
    device_name = {}
    for r, k, v, _c in parsed:
        if dev(k, v) and dev(k, v) not in device_name:
            device_name[dev(k, v)] = _s(r.get("product_desc")) or None
    contract_of = {}
    for _r, k, v, c in parsed:
        if mob(k, v) and c and mob(k, v) not in contract_of:
            contract_of[mob(k, v)] = c

    def bridge(group_of):
        return [{"serial_1": dev(k, v), "mdn": mob(k, v), "trans_id": group_of(c)} for _r, k, v, c in parsed if k]

    probe = [{"serial": None, "mdn": m, "trans_id": m} for m in mdns]
    by_contract = line_pairings(probe, device_key, bridge(lambda c: f"{inv}|{c}"), "serial", "mdn", "trans_id", "serial_1", "mdn")
    by_invoice = line_pairings(probe, device_key, bridge(lambda c: inv), "serial", "mdn", "trans_id", "serial_1", "mdn")
    plans = [(_s(r.get("product_desc")), mob(k, v), c) for r, k, v, c in parsed
             if plan_line is not None and _s(r.get("product_desc")) and plan_line(r)]
    out = []
    for m, pc, pi in zip(mdns, by_contract, by_invoice):
        use = pc if (pc.get("key") or pc.get("candidates") is not None) else pi
        c = contract_of.get(m, "")
        mine = sorted({n for n, pm, _c in plans if pm == m})
        grp = sorted({n for n, pm, pcn in plans if c and pcn == c and pm in (None, m)})
        free = sorted({n for n, pm, pcn in plans if pm is None and not pcn})      # a plan line tied to no number and no contract
        plan = mine[0] if mine else (grp[0] if len(grp) == 1 else (free[0] if len(free) == 1 else ""))
        ct = next((_s(r.get("contract_type")) for r, k, v, cc in parsed
                   if _s(r.get("contract_type")) and (mob(k, v) == m or (c and cc == c))), "")
        out.append({"mdn": m, "imei": use.get("key"), "pairing": use.get("pairing"), "via": use.get("via"),
                    "reason": None if use.get("key") else use.get("reason"), "plan": plan,
                    "device_name": device_name.get(use.get("key")) if use.get("key") else None,
                    "contract_no": c or None, "contract_type": ct or None})
    return out
