"""THE ONE activation-type predicate for a POS sale line — config-driven, per org, PURE.

OWNER (2026-09-21, verbatim): *"sales report shows 88 txns but not a break up in to activations and
upgrade etc, also nothing on exec mtd"*.

THE INSTANCE (org f4f1c16e…, August 2026): 1,056 line-level rows, 88 invoices, `contract_type` BLANK on
every row — the export has no such column. The activation TYPE is carried in the CATEGORY PATH leaf
('… >> New Activation (…)', '… >> Upgrades (…)', '… >> Cellular Equipment >> Customer Provided Device')
and in the product name ('Customer Owned Device …'). Every report and every commission calculation read
zero activations, silently.

THE CLASS (fixed here for every caller — CLAUDE.md 2026-09-20 "A fix is a DESIGN fix"):
  A sale line's activation type was classified from ONE fixed column (`contract_type`) by token lists
  hard-coded in three places (calculator.classify_contract_type, router._exec_act_class, the mig-213
  contract_type_map resolver), and the Executive-MTD metric buckets from a house vocabulary. A POS that
  carries the same fact in another field, or in other words, yields silent zeros on every surface.

WHAT THIS MODULE IS:
  · `activation_class(row, rules)` → 'activation' | 'upgrade' | 'byod' | 'port' | 'hardware_only' | None —
    the ONE predicate. `rules` say WHICH FIELDS carry the fact (`fields`, default ['contract_type']) and
    WHICH TOKENS name each class (`tokens`, matched by CONTAINS over every configured field), plus an
    `exact` value → class map (the mig-213 contract_type_map, absorbed — it is the same fact).
  · `classify_line(row, rules)` → the pay-path bucket vocabulary 'premium' | 'upgrade' | 'byod' | None
    (`BUCKET_OF`), which is what commissions, targets and the Sales Report have always summed on.
  · `resolve_rules(raw, ct_map, legacy_activation)` → the full rules dict from the org's
    `accessory_config.activation_details_rules` JSON (mig 313 — the EXISTING per-org classification home,
    extended with `fields` / `tokens` / `exact`; no migration), house defaults filling every missing key.
    HOUSE DEFAULTS ARE TODAY'S BEHAVIOUR EXACTLY: the calculator's keyword list, the exec-MTD port token,
    contract_type only — `harness_line_class.py` replays the retired classifiers over every spelling in
    the seeds and asserts byte-identity (the money compatibility pin).
  · `suggest_rules(rows, ...)` / `suggest_metric_rules(rows, resolved, ...)` → the onboarding step's
    proposals: scan the DISTINCT values of every candidate field, match the house hint words per class
    (config: `hints` / `metric_hints` in the same JSON, house defaults below), drop a hint that names
    nearly every line (a department word inside every category path is not a type), and show the count
    each proposal would classify — computed by THIS predicate, so what the person confirms is what
    every report will count.
  · `count_classes(rows, rules, skip)` → the gate's measurement: how many lines / distinct invoices each
    class holds; zero activation-type lines over a slice that has lines is what the intake refuses to
    call "verified" until the rule is mapped or the person attests the file truly has no activations.

THE SECOND CLASS (2026-09-21, the owner's first save of step 2.5a — "the effective rule is not what the
person confirmed"): the tenant saved `fields: ['category']`, `tokens.activation: ['new activation',
'activation', …]`; the bare word sits inside every category path of that export (856 of 898 lines), so
every invoice counted as an activation (88 / 88) — the silent zero's twin. Three faces, one mechanism:
  · the too-broad guard ran over the PROPOSALS, never over what is SAVED → `token_shares` /
    `broad_tokens` / `refused_tokens` measure the EFFECTIVE rules over the rows in hand; the save
    refuses a refused word (unless the person attests it by name — recorded in the same JSON under
    `broad_attested`); GET / the commit / Exec MTD re-validate what is saved (`count_classes` carries
    `refused`; `rules_refused` / `refusal_sentence`);
  · house tokens are CONTRACT-TYPE words and leaked onto a tenant's declared fields → THE NO-LEAK RULE:
    house tokens (and the legacy exec 'activation' layer, and the mig-213 auto tokens) fill an
    undeclared class only when the fields read ARE the house field; under tenant-declared fields an
    undeclared class has NO words (the step shows it empty and says so);
  · the hint list is the ENGINE's input, never the person's starting text — the step seeds its editable
    words from `suggest_rules(...).proposal` only; the bare words 'activation' / 'port' are no longer
    hints (a department word, a suffix of 'support' / 'report'): the guard would catch them only after
    they had been proposed, so they are not proposed at all.

RULE TWO: no carrier, POS vendor, tenant or product name appears here. The hint words are generic
activation vocabulary; a tenant's own words are config rows. stdlib only — the harness imports this file
directly.
"""
from datetime import datetime as _dt, timezone as _tz

from app.modules.commcalc import exec_metric_defs as _emd     # pure: the Exec-MTD line predicate

CLASSES = ("activation", "upgrade", "byod", "port", "hardware_only")
# what the pay path / Sales Report / targets sum on: activation + port = a premium new line
BUCKET_OF = {"activation": "premium", "port": "premium", "upgrade": "upgrade", "byod": "byod",
             "hardware_only": None}
BUCKETS = ("premium", "upgrade", "byod")
# the classes that are an ACTIVATION TYPE (the gate counts these; hardware_only is an exclusion)
ACTIVATION_TYPE_CLASSES = ("activation", "port", "byod", "upgrade")
CLASS_LABELS = {"activation": "New activation", "upgrade": "Upgrade", "byod": "Bring-your-own-device",
                "port": "Port-in (number moved in)", "hardware_only": "Hardware only — not an activation"}

# ── HOUSE DEFAULTS — today's behaviour, verbatim ──────────────────────────────────────────────────
HOUSE_FIELDS = ("contract_type",)
# `activation` = calculator._PREMIUM_KEYS as it stood (owner ruling 2026-07-16 on 'idv' included);
# byod / upgrade / port = the contains tokens classify_contract_type and the exec port split used.
HOUSE_TOKENS = {
    "byod": ["byod"],
    "upgrade": ["upgrade"],
    "port": ["port"],
    "activation": ["activation", "port-in", "port in", "add a line", "add-a-line", "new line", " aal",
                   "aal ", "idv", "port with idv"],
    "hardware_only": [],
}
# The mig-213 opt-in: a tenant that maintains an exact map also gets the well-known NON-PHONE
# activation categories recognised as an activation (router._AUTO_ACT_CATEGORY_KEYS, 2026-08-13).
# Applied ONLY when `exact` is non-empty — the house org has an empty map and is byte-identical.
HOUSE_AUTO_ACTIVATION_TOKENS = ["home internet", "home-internet", "fixed wireless", "fwa", "fios", "tablet", "edge"]
# the fields the suggestion engine scans, in the order a proposal prefers them
CANDIDATE_FIELDS = ("contract_type", "category", "department", "product_desc", "trans_type")
# generic activation vocabulary (config: `hints` in the same JSON; these are the house defaults).
# DECISION (2026-09-21): the bare words 'activation' and 'port' are NOT hints. 'activation' is the
# department word inside every category path of a price-sheet export and a substring of 'activation
# fee'; 'port' is a substring of 'support' / 'report'. The too-broad guard would refuse them only once
# proposed and only when they name ≥ BROAD_RATIO of the lines — a word naming 30% of the lines wrongly
# would sail through — so a word that is generic ON ITS OWN is never a proposal. A tenant whose column
# carries exactly 'Activation' as a value is served by the house tokens (a contract-type column) or by
# typing the word, which the guard then measures.
HOUSE_HINTS = {
    "activation": ["new activation", "new line", "add a line", "add-a-line", "aal", "new act"],
    "upgrade": ["upgrade"],
    "byod": ["byod", "bring your own", "customer provided", "customer owned", "customer phone", "own device", "sim only"],
    "port": ["port-in", "port in", "port in activation", "number port", "ported"],
    "hardware_only": ["hardware only", "prepaid", "equipment only", "no activation", "device only"],
}
# the Executive-MTD line buckets' generic vocabulary (config: `metric_hints`)
HOUSE_METRIC_HINTS = {
    "phones": ["smartphone", "smart phone", "basic phone", "feature phone", "flip phone", "iphone", "android",
               "cellphone", "cell phone", "handset"],
    "bill_payment": ["bill pay", "bill payment", "recharge", "refill", "top up", "top-up", "wallet funding", "rtr"],
    "accessory": ["accessor", "case", "charger", "screen protector", "cable", "headphone", "earbud"],
    "activation_fee": ["activation fee", "access charge", "setup fee", "set-up fee", "device setup", "upgrade fee"],
    "protect": ["protect", "insurance", "warranty"],
}
# which row column each metric proposal names, and the line_match key it becomes
METRIC_FIELDS = (("category", "category_contains"), ("department", "department_contains"),
                 ("product_desc", "product_desc_contains"))
# a word that names this share of ALL scanned lines is a department word, not a type — the SAME ratio
# gates a hint (never proposed) and a saved token (refused unless attested by name)
BROAD_RATIO = 0.8
# the keys this module owns inside accessory_config.activation_details_rules (the Activation-Details
# basis keys — edge_* / upgrade_hidden_* — live beside them and are never touched by a save here)
OWNED_KEYS = ("fields", "tokens", "exact", "auto_activation_tokens", "hints", "metric_hints", "broad_attested",
              "event")

# ── THE ACTIVATION EVENT (owner 2026-09-25) — what ONE activation / upgrade IS ─────────────────────
# Owner, verbatim: *"commisison for teh reps need to be claculated per action and per upgrade as defined
# in teh incentive payout, the system sis calculating per line item"*. The predicate above says WHICH
# LINES are activation-type; an invoice carries several such lines per activation (the rate-plan rebate,
# the tracking line, the plan line — each names the same phone line). The EVENT is the activation: the
# phone line (MDN) the activation-type lines name; else the device they name; else the invoice.
# Config (RULE TWO), per org, in the SAME JSON (`accessory_config.activation_details_rules.event`):
#   keys       — which line identity makes one event, tried in order per invoice ('phone' = the line's
#                `mdn` column or a phone-shaped tracking #; 'device' = a device-shaped tracking #, via
#                `customer_identity.tracking_kind` — THE phone/device rule, never re-implemented). [] =
#                one event per invoice.
#   precedence — which class an event takes when its lines carry several (the line predicate's own
#                precedence, byod > upgrade > port > activation, by default).
#   count_unit — what the COUNTING surfaces (Sales Report / Executive MTD / Targets cells, the Boost
#                calculator's activation counts, the daily closing) count per bucket: 'transaction' (the
#                house default — distinct invoices per bucket, exactly as they always have) or 'event'
#                (these events). A per-org config row flips it; the pay gate's `per_event` basis always
#                pays events. Measured deltas per tenant are in the index §6f before anyone flips it.
EVENT_KEY_KINDS = ("phone", "device")
COUNT_UNITS = ("transaction", "event")
HOUSE_EVENT = {"keys": ["phone", "device"], "precedence": ["byod", "upgrade", "port", "activation"],
               "count_unit": "transaction"}
# the bucket vocabulary the pay path stamps (engine `activation_bucket`) → the class an event reasons in
CLASS_OF_BUCKET = {"premium": "activation", "upgrade": "upgrade", "byod": "byod"}
EVENT_NO_KEY = "no phone line or device on the invoice's activation-type lines — counted once for the invoice"
EVENT_EVIDENCE_SHARED = ("activation-type line(s) name no phone line of their own and the invoice has several "
                         "activations — kept as evidence of the invoice, never as another activation")


def _now_iso():
    return _dt.now(_tz.utc).isoformat(timespec="seconds")


def attest_key(cls, token):
    """The key a too-broad attestation is recorded under: '<class>:<token>' (the token verbatim —
    a leading / trailing space is load-bearing)."""
    return f"{_s(cls)}:{str(token or '').lower()}"


def _norm_attested(mapping):
    """{attest_key: {by, at, ratio, reason}} — junk dropped. A value that is not a dict is kept as
    {'by': str(value)} so an older hand-written row still counts as an attestation."""
    out = {}
    for k, v in (mapping or {}).items() if isinstance(mapping, dict) else ():
        key = str(k or "").lower()
        if not key or ":" not in key:
            continue
        out[key] = dict(v) if isinstance(v, dict) else {"by": str(v or "") or None}
    return out


def _s(v):
    return "" if v is None else str(v).strip().lower()


def _norm_tokens(value, default):
    """A token list, lowercased/trimmed; None/absent → default (a copy); junk dropped; [] = disabled."""
    if value is None or not isinstance(value, (list, tuple)):
        return list(default)
    out = []
    for t in value:
        s = str(t or "").lower()
        if s.strip():
            out.append(s)          # kept verbatim: a leading / trailing space is load-bearing (' aal')
    return out


def _norm_fields(value):
    if not isinstance(value, (list, tuple)):
        return list(HOUSE_FIELDS)
    out = []
    for f in value:
        s = _s(f)
        if s and s not in out:
            out.append(s)
    return out or list(HOUSE_FIELDS)


def _norm_exact(mapping, into):
    for k, v in (mapping or {}).items() if isinstance(mapping, dict) else ():
        kk, vv = _s(k), _s(v)
        if vv == "premium":                 # the mig-213 bucket word for a plain activation
            vv = "activation"
        if kk and (vv in CLASSES or vv == "none"):
            into[kk] = vv
    return into


def resolve_event(raw=None):
    """The org's EVENT config (`activation_details_rules.event`) → {'keys': [...], 'precedence': [...]}.
    Missing / junk → the house default. `keys: []` is honoured (one event per invoice); unknown kinds are
    dropped; a precedence list is completed with any class it omits, in the house order. PURE."""
    raw = raw if isinstance(raw, dict) else {}
    keys = list(HOUSE_EVENT["keys"])
    if isinstance(raw.get("keys"), (list, tuple)):
        keys = []
        for k in raw["keys"]:
            s = _s(k)
            if s in EVENT_KEY_KINDS and s not in keys:
                keys.append(s)
    prec = []
    if isinstance(raw.get("precedence"), (list, tuple)):
        for c in raw["precedence"]:
            s = _s(c)
            if s in ACTIVATION_TYPE_CLASSES and s not in prec:
                prec.append(s)
    for c in HOUSE_EVENT["precedence"]:
        if c not in prec:
            prec.append(c)
    unit = _s(raw.get("count_unit"))
    return {"keys": keys, "precedence": prec,
            "count_unit": unit if unit in COUNT_UNITS else HOUSE_EVENT["count_unit"],
            "source": "tenant" if any(k in raw for k in ("keys", "precedence", "count_unit")) else "house"}


def resolve_rules(raw=None, ct_map=None, legacy_activation=None):
    """The full rules dict for one org. PURE.

    `raw`               = accessory_config.activation_details_rules (dict / None / junk).
    `ct_map`            = the mig-213 contract_type_map ({value: premium|upgrade|byod|none}) — absorbed
                          into `exact` (a tenant's newer `exact` key overrides it per value).
    `legacy_activation` = a TENANT-authored exec_metric_config 'activation' rules row
                          ({byod, upgrade, port} token lists), honoured only for a class `tokens`
                          does not declare — so an org that tuned its port token before this design
                          keeps it until it saves the one home; house defaults otherwise.
    Missing keys → house defaults (today's behaviour). Unknown keys are ignored.

    THE NO-LEAK RULE: the house tokens are CONTRACT-TYPE words ('activation', ' aal', 'idv', 'port' …).
    They — and the legacy exec 'activation' layer and the mig-213 auto tokens, which are the same kind of
    word — fill an undeclared class ONLY when the fields read are the house field. Once the fields are
    tenant-declared and differ from it, an undeclared class has NO words: `house_fill` says which
    applied. Every org with no declaration resolves exactly as before (the pin)."""
    raw = raw if isinstance(raw, dict) else {}
    tokens_raw = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else {}
    legacy = legacy_activation if isinstance(legacy_activation, dict) else {}
    fields = _norm_fields(raw.get("fields"))
    house_fill = fields == list(HOUSE_FIELDS)
    tokens = {}
    for cls in CLASSES:
        v = tokens_raw.get(cls)
        if v is None and cls in ("byod", "upgrade", "port") and house_fill:
            v = legacy.get(cls)
        tokens[cls] = _norm_tokens(v, HOUSE_TOKENS[cls] if house_fill else [])
    exact = _norm_exact(ct_map, {})
    exact = _norm_exact(raw.get("exact"), exact)
    hints_raw = raw.get("hints") if isinstance(raw.get("hints"), dict) else {}
    mhints_raw = raw.get("metric_hints") if isinstance(raw.get("metric_hints"), dict) else {}
    declared = {"fields": isinstance(raw.get("fields"), (list, tuple)) and bool(raw.get("fields")),
                "tokens": bool(tokens_raw), "exact": bool(exact)}
    return {
        "fields": fields,
        "tokens": tokens,
        "exact": exact,
        "auto_activation_tokens": _norm_tokens(raw.get("auto_activation_tokens"),
                                               HOUSE_AUTO_ACTIVATION_TOKENS if house_fill else []),
        "hints": {c: _norm_tokens(hints_raw.get(c), HOUSE_HINTS[c]) for c in CLASSES},
        "metric_hints": {b: _norm_tokens(mhints_raw.get(b), HOUSE_METRIC_HINTS[b]) for b in HOUSE_METRIC_HINTS},
        "broad_attested": _norm_attested(raw.get("broad_attested")),
        "event": resolve_event(raw.get("event")),
        "declared": declared,
        "house_fill": house_fill,
        "source": "tenant" if (declared["fields"] or declared["tokens"] or declared["exact"]) else "house",
    }


HOUSE_RULES = resolve_rules(None)


def _texts(row, rules):
    r = row or {}
    return [_s(r.get(f)) for f in rules["fields"]]


def _hit(tokens, texts):
    return any(t in x for x in texts if x for t in tokens)


def activation_class(row, rules=None):
    """THE predicate. Precedence (today's, composed): an exact-mapped value wins ('none' = excluded);
    else hardware_only → byod → upgrade → the activation gate (activation tokens; the auto tokens for a
    tenant with an exact map) → then 'port' when a port token is present, else 'activation'. None = not
    an activation line (an accessory, a bill payment, a feature). PURE, never raises."""
    r = rules or HOUSE_RULES
    texts = _texts(row, r)
    cls = None
    exact = r["exact"]
    if exact:
        for x in texts:
            if x and x in exact:
                cls = exact[x]
                break
        if cls == "none":
            return None
    tk = r["tokens"]
    if cls is None:
        if _hit(tk["hardware_only"], texts):
            return "hardware_only"
        if _hit(tk["byod"], texts):
            return "byod"
        if _hit(tk["upgrade"], texts):
            return "upgrade"
        if not _hit(tk["activation"], texts) and not (exact and _hit(r["auto_activation_tokens"], texts)):
            return None
        cls = "activation"
    # the Port sub-split keeps the retired split's precedence (upgrade > byod > port) even for an
    # exact-mapped activation: a mapped 'BYOD Port' stays a plain activation, never a port
    if cls == "activation" and _hit(tk["port"], texts) and not _hit(tk["upgrade"], texts) and not _hit(tk["byod"], texts):
        return "port"
    return cls


def bucket_of(cls):
    return BUCKET_OF.get(cls)


def classify_line(row, rules=None):
    """'premium' | 'upgrade' | 'byod' | None — the bucket every money and display surface sums on."""
    return BUCKET_OF.get(activation_class(row, rules))


# ── THE ACTIVATION EVENT — one definition, dereferenced by every surface that pays or counts per
#    activation (plan pay gate `per_event`, the rep breakdown, Exec MTD / Sales Report counts, the
#    Boost calculator). `harness_activation_event_lock.py` fails the build on a second copy. ─────────
def line_event_keys(row):
    """{'phone': mdn | None, 'device': imei | None} — the identities ONE sale line names. The phone is the
    line's own `mdn` column, else a phone-shaped tracking # (`serial_1`); the device is a device-shaped
    tracking #. Both through `customer_identity` (THE phone key + THE phone-vs-device rule). PURE."""
    from app.modules.pos import customer_identity as _ci      # pure; lazy so this module stays light
    r = row or {}
    kind, val = _ci.tracking_kind(r.get("serial_1"))
    phone = _ci.norm_phone(r.get("mdn")) or (val if kind == "phone" else None)
    return {"phone": phone, "device": val if kind == "device" else None}


def _class_of(label):
    s = _s(label)
    if s in CLASS_OF_BUCKET:
        return CLASS_OF_BUCKET[s]
    return s if s in ACTIVATION_TYPE_CLASSES else None


def _default_txn_of(row, txn_field="trans_id"):
    return str((row or {}).get(txn_field) or "").strip()


def activation_events(rows, rules=None, classes=None, skip=None, txn_field="trans_id", txn_of=None):
    """THE activation / upgrade EVENTS in `rows` — what a per-activation or per-upgrade rate pays on and
    what every activation count counts. PURE, never raises on odd rows.

    Each activation-type line (THE predicate `activation_class`, or the caller's already-resolved label
    per row in `classes` — a class or a pay bucket, e.g. the engine's `activation_bucket` incl. the mig-224
    rescue) belongs to its invoice. Per invoice, the FIRST kind in the org's `event.keys` that any of its
    activation-type lines names defines the events: one event per distinct value (one per phone line).
    A line naming no value of that kind is EVIDENCE: attached to the invoice's only event, or — when the
    invoice has several — kept unattributed (`row_event` None) and reported, never a new event. No line
    names any configured kind → ONE event for the invoice. An event's class is its lines' strongest class
    by `event.precedence`.

    Returns {"events": [{id, trans_id, key, key_kind, cls, bucket, classes:{cls: lines}, lines:[i]}],
             "row_event": {row index: event id | None}, "unattributed": [{trans_id, line, cls}],
             "ambiguous": [{trans_id, code, detail, ...}], "config": {...}}
    `id` = '<trans_id>|<kind>:<value>' (or '<trans_id>|invoice'); a line with no trans id is its own
    invoice ('#row<i>'), so two sales are never merged by a missing id."""
    r = rules or HOUSE_RULES
    ecfg = r.get("event") or resolve_event(None)
    keys = list(ecfg.get("keys") or [])
    prec = list(ecfg.get("precedence") or HOUSE_EVENT["precedence"])
    rank = {c: i for i, c in enumerate(prec)}
    lines_by_txn, order = {}, []
    for i, row in enumerate(rows or []):
        try:
            if skip is not None and skip(row):
                continue
            cls = _class_of(classes[i]) if classes is not None else activation_class(row, r)
        except Exception:
            cls = None
        if cls not in ACTIVATION_TYPE_CLASSES:
            continue
        tid = (txn_of(row) if txn_of is not None else _default_txn_of(row, txn_field)) or f"#row{i}"
        if tid not in lines_by_txn:
            lines_by_txn[tid] = []
            order.append(tid)
        lines_by_txn[tid].append((i, cls, line_event_keys(row)))
    events, row_event, unattributed, ambiguous = [], {}, [], []
    for tid in order:
        lns = lines_by_txn[tid]
        kind = next((k for k in keys if any(ek.get(k) for _i, _c, ek in lns)), None)
        groups = {}
        if kind is None:
            groups[("invoice", "")] = [(i, c) for i, c, _ek in lns]
            if keys:
                ambiguous.append({"trans_id": tid, "code": "event_no_key", "detail": EVENT_NO_KEY,
                                  "lines": len(lns)})
        else:
            loose = []
            for i, c, ek in lns:
                v = ek.get(kind)
                if v:
                    groups.setdefault((kind, v), []).append((i, c))
                else:
                    loose.append((i, c))
            if loose and len(groups) == 1:
                next(iter(groups.values())).extend(loose)
            elif loose:
                for i, c in loose:
                    row_event[i] = None
                    unattributed.append({"trans_id": tid, "line": i, "cls": c})
                ambiguous.append({"trans_id": tid, "code": "event_evidence_shared", "detail": EVENT_EVIDENCE_SHARED,
                                  "lines": len(loose), "events": len(groups)})
        for (k, v) in sorted(groups):
            members = sorted(groups[(k, v)])
            counts = {}
            for _i, c in members:
                counts[c] = counts.get(c, 0) + 1
            cls = min(counts, key=lambda c: rank.get(c, len(rank)))
            eid = f"{tid}|{k}:{v}" if k != "invoice" else f"{tid}|invoice"
            if len(counts) > 1:
                ambiguous.append({"trans_id": tid, "code": "event_mixed_classes", "event": eid,
                                  "classes": dict(counts), "cls": cls,
                                  "detail": (f"one activation's lines carry {', '.join(sorted(counts))} — "
                                             f"counted once, as {cls} (event precedence)")})
            events.append({"id": eid, "trans_id": tid, "key": v or None, "key_kind": k,
                           "cls": cls, "bucket": BUCKET_OF.get(cls), "classes": counts,
                           "lines": [i for i, _c in members]})
            for i, _c in members:
                row_event[i] = eid
    # the same phone line / device as an event on SEVERAL invoices — each invoice is its own sale, so each
    # is its own event; reported, never merged (a re-ring or a re-used number is for a person to judge)
    seen_key = {}
    for e in events:
        if e["key_kind"] != "invoice":
            seen_key.setdefault((e["key_kind"], e["key"], e["cls"]), []).append(e["trans_id"])
    for (k, v, c), tids in sorted(seen_key.items()):
        if len(tids) > 1:
            ambiguous.append({"trans_id": tids[0], "code": "event_key_on_several_invoices", "key": v,
                              "key_kind": k, "cls": c, "invoices": tids,
                              "detail": (f"{k} {v} is a {c} on {len(tids)} invoices ({', '.join(tids)}) — each "
                                         f"counted as its own sale; check for a re-ring")})
    return {"events": events, "row_event": row_event, "unattributed": unattributed,
            "ambiguous": ambiguous, "config": {"keys": keys, "precedence": prec,
                                               "source": ecfg.get("source", "house")}}


def count_unit_of(rules=None):
    """'transaction' | 'event' — the org's configured counting unit (house: 'transaction')."""
    return ((rules or HOUSE_RULES).get("event") or HOUSE_EVENT).get("count_unit") or HOUSE_EVENT["count_unit"]


def activation_units(rows, rules=None, skip=None, txn_of=None, unit=None, require_txn=True):
    """THE per-row COUNT key every activation-counting surface adds to its bucket set — parallel to `rows`:
    (bucket, unit_id, cls) for a line that counts, else None. PURE.

    unit 'transaction' (the house default): (classify bucket, trans id, class) for every classified line
      with a trans id — byte-identical to the `classify_line(row) → set.add(tid)` every counting surface
      has always done (the lock pins the replay).
    unit 'event': (event bucket, event id, event class) for every line of an activation EVENT — so a set of
      unit ids counts each activation / upgrade once, in ONE bucket (the event's). An unattributed evidence
      line and a line with no trans id count nothing.
    `unit` None → the org's `event.count_unit`. `txn_of(row)` → the caller's trans-id spelling (default:
    stripped `trans_id`). `require_txn=False` keeps a line with a BLANK trans id counting (under
    'transaction' as the one '' invoice — the Boost calculator's historic behaviour, kept byte-identical)."""
    r = rules or HOUSE_RULES
    u = unit if unit in COUNT_UNITS else count_unit_of(r)
    rows = list(rows or [])
    tof = txn_of if txn_of is not None else _default_txn_of
    out = [None] * len(rows)
    if u == "transaction":
        for i, row in enumerate(rows):
            if skip is not None and skip(row):
                continue
            tid = tof(row)
            if not tid and require_txn:
                continue
            cls = activation_class(row, r)
            b = BUCKET_OF.get(cls)
            if b:
                out[i] = (b, tid, cls)
        return out
    ev = activation_events(rows, r, skip=skip, txn_of=tof)
    by_id = {e["id"]: e for e in ev["events"]}
    for i, row in enumerate(rows):
        eid = ev["row_event"].get(i)
        e = by_id.get(eid) if eid else None
        if e is None or not e.get("bucket") or (require_txn and not tof(row)):
            continue
        out[i] = (e["bucket"], eid, e["cls"])
    return out


def event_counts(events_result):
    """{bucket: distinct events} and {class: distinct events} over an `activation_events` result — the
    number a per-activation count shows. PURE."""
    by_bucket, by_class = {}, {}
    for e in (events_result or {}).get("events") or []:
        if e.get("bucket"):
            by_bucket[e["bucket"]] = by_bucket.get(e["bucket"], 0) + 1
        by_class[e["cls"]] = by_class.get(e["cls"], 0) + 1
    return {"by_bucket": by_bucket, "by_class": by_class}


# ── the gate's measurement ───────────────────────────────────────────────────────────────────────
def count_classes(rows, rules=None, skip=None, txn_field="trans_id"):
    """Lines and DISTINCT transactions per class over `rows` (after `skip(row)` when given — the
    caller passes the shared voided / Return rule). `activation_type_lines` counts every class in
    ACTIVATION_TYPE_CLASSES; that number being 0 over a slice with lines is the gate."""
    r = rules or HOUSE_RULES
    per = {c: {"lines": 0, "txns": set()} for c in CLASSES}
    scanned = skipped = unclassified = 0
    act_txns = set()
    for row in rows or []:
        if skip is not None and skip(row):
            skipped += 1
            continue
        scanned += 1
        cls = activation_class(row, r)
        if not cls:
            unclassified += 1
            continue
        per[cls]["lines"] += 1
        tid = str((row or {}).get(txn_field) or "").strip()
        if tid:
            per[cls]["txns"].add(tid)
            if cls in ACTIVATION_TYPE_CLASSES:
                act_txns.add(tid)
    classes = {c: {"lines": per[c]["lines"], "transactions": len(per[c]["txns"]), "label": CLASS_LABELS[c]}
               for c in CLASSES}
    # RE-VALIDATION of what is in force (the second class): a tenant-declared word that names nearly
    # every line is carried here so the gate, the Stage-4 row and the Exec MTD banner can refuse it —
    # house defaults are never measured (the pin: an org with no declaration is byte-identical)
    broad = broad_tokens(rows, r, skip) if r["declared"]["tokens"] else []
    return {"scanned": scanned, "skipped": skipped, "unclassified_lines": unclassified, "classes": classes,
            "activation_type_lines": sum(classes[c]["lines"] for c in ACTIVATION_TYPE_CLASSES),
            "activation_type_transactions": len(act_txns),
            "fields": list(r["fields"]), "source": r["source"],
            "broad": broad, "refused": [b for b in broad if not b["attested"]]}


def gate_open(counts):
    """True when the slice HAS lines and not one is an activation type — the state a commit may not
    call verified without a mapped rule or an attestation."""
    c = counts or {}
    return int(c.get("scanned") or 0) > 0 and int(c.get("activation_type_lines") or 0) == 0


def gate_sentence(counts, step_label="Onboarding — step 2.5a"):
    c = counts or {}
    return (f"{int(c.get('scanned') or 0):,} sales line(s) landed and not one could be told apart as an "
            f"activation, upgrade, port-in or bring-your-own-device line (fields read: "
            f"{', '.join(c.get('fields') or [])}). Map which words mean which under {step_label}, or "
            f"attest that this file truly has no activations.")


# ── THE TOO-BROAD GUARD over what is IN FORCE / what would be SAVED (the second class) ──────────────
def token_shares(rows, rules=None, skip=None):
    """For EVERY token of every class in `rules`: how many scanned lines carry it in any configured
    field, and that share of the scanned lines. ([{class, token, lines, ratio}], scanned). PURE."""
    r = rules or HOUSE_RULES
    toks = [(cls, t) for cls in CLASSES for t in r["tokens"][cls]]
    hits = [0] * len(toks)
    scanned = 0
    for row in rows or []:
        if skip is not None and skip(row):
            continue
        scanned += 1
        if not toks:
            continue
        texts = [x for x in _texts(row, r) if x]
        if not texts:
            continue
        for i, (_cls, t) in enumerate(toks):
            if any(t in x for x in texts):
                hits[i] += 1
    return ([{"class": cls, "token": t, "lines": hits[i],
              "ratio": round(hits[i] / scanned, 3) if scanned else 0.0}
             for i, (cls, t) in enumerate(toks)], scanned)


def broad_tokens(rows, rules=None, skip=None, broad_ratio=BROAD_RATIO):
    """The tokens of `rules` that name ≥ `broad_ratio` of the scanned lines — a department word, not a
    type — each with `attested` (the person attested that word by name; recorded under
    `broad_attested` in the same JSON). PURE. Over no rows nothing is broad (nothing is measured)."""
    r = rules or HOUSE_RULES
    shares, scanned = token_shares(rows, r, skip)
    att = r.get("broad_attested") or {}
    return [{**s, "scanned": scanned, "attested": attest_key(s["class"], s["token"]) in att}
            for s in shares if scanned and s["ratio"] >= broad_ratio]


def refused_tokens(rows, rules=None, skip=None, broad_ratio=BROAD_RATIO):
    """The broad tokens nobody attested — what a save is REFUSED for and what a saved rule is refused
    over at re-validation. Only tenant-declared tokens are measured (house defaults are the pin)."""
    r = rules or HOUSE_RULES
    if not r["declared"]["tokens"]:
        return []
    return [b for b in broad_tokens(rows, r, skip, broad_ratio) if not b["attested"]]


def rules_refused(counts):
    """True when the counts (count_classes) were produced by a rule with an unattested too-broad word:
    the numbers are NOT a split, they are the silent zero's twin (every invoice an activation)."""
    return bool((counts or {}).get("refused"))


def _refused_words(refused):
    return ", ".join(f"\"{b['token']}\" under {CLASS_LABELS.get(b['class'], b['class']).lower()} "
                     f"({int(round(b['ratio'] * 100))}% of the lines)" for b in refused)


def refusal_sentence(refused, scanned=None, step_label="Onboarding — step 2.5a", saving=False):
    """The refusal in plain words, naming each word with its share. `saving` phrases it for the save
    (nothing written); otherwise for a rule already in force."""
    n = int(scanned or (refused[0].get("scanned") if refused else 0) or 0)
    words = _refused_words(refused or [])
    if saving:
        return (f"Not saved — {words} would name nearly every one of the {n:,} sales lines read, so it is a "
                f"department word, not an activation type; with it every invoice would count. Remove the word, "
                f"or tick \"keep this word anyway\" to attest it by name.")
    return (f"The activation rule in force cannot be trusted: {words} names nearly every one of the {n:,} sales "
            f"lines read, so every invoice counts as that type. Fix the words under {step_label} (remove the "
            f"word, or attest it by name there).")


def norm_broad_ok(value):
    """The save body's attestation → ['<class>:<token>', …]: strings 'class:token' or {class, token}
    objects; junk dropped."""
    out = []
    for item in (value or []) if isinstance(value, (list, tuple)) else ():
        if isinstance(item, dict):
            key = attest_key(item.get("class"), item.get("token"))
        else:
            key = str(item or "").lower()
        if ":" in key and key.split(":", 1)[0] in CLASSES and key.split(":", 1)[1] and key not in out:
            out.append(key)
    return out


# ── the suggestion engine ────────────────────────────────────────────────────────────────────────
def distinct_values(rows, fields=CANDIDATE_FIELDS, skip=None):
    """{field: {lowercased value: line count}} over the rows (blank values dropped)."""
    out = {f: {} for f in fields}
    n = 0
    for row in rows or []:
        if skip is not None and skip(row):
            continue
        n += 1
        for f in fields:
            v = _s((row or {}).get(f))
            if v:
                out[f][v] = out[f].get(v, 0) + 1
    return out, n


def _candidates(values, scanned, hints, broad_ratio):
    """[{token, lines, samples}] for one field, one class — hints that hit, in hint order, the too-broad
    ones listed separately; a hint naming EXACTLY the lines an earlier one named is dropped (one word
    per set of lines). A wider or narrower word is kept: whether 'smartphone' or 'android' is the
    right word is the person's call, and the preview counts either way."""
    accepted, broad = [], []
    for h in hints:
        hits = {v: c for v, c in values.items() if h in v}
        if not hits:
            continue
        lines = sum(hits.values())
        ratio = (lines / scanned) if scanned else 0.0
        entry = {"token": h, "lines": lines, "ratio": round(ratio, 3),
                 "samples": [v for v, _ in sorted(hits.items(), key=lambda kv: -kv[1])[:3]]}
        if ratio >= broad_ratio:
            broad.append(entry)
            continue
        vs = set(hits)
        if any(vs == a["_values"] for a in accepted):
            continue
        entry["_values"] = vs
        accepted.append(entry)
    for a in accepted:
        a.pop("_values", None)
    return accepted, broad


# the public name of the candidate scan, for the OTHER per-org word rules that reuse this engine rather
# than growing a sibling (core/plan_sources.py — which landed sources carry a tenant's plan names, 2026-09-22)
candidate_words = _candidates


def suggest_rules(rows, current=None, skip=None, fields=CANDIDATE_FIELDS, broad_ratio=BROAD_RATIO):
    """Propose `fields` + `tokens` per class from the rows' own vocabulary. PURE.

    Returns {"scanned", "distinct": {field: n}, "per_class": {cls: [{field, token, lines, ratio, samples}]},
             "too_broad": [...], "proposal": {"fields": [...], "tokens": {cls: [...]}},
             "preview": count_classes(rows, proposal-as-rules), "current": count_classes(rows, current)}.
    The preview is THE predicate over the proposal, so the counts shown are the counts every report
    will produce once saved. THE PROPOSAL IS THE ONLY SEED of the step's editable words: per class it is
    the person's own declared words (minus any refused as too broad) plus the hint hits in this file;
    with nothing declared, the hits — else, under the house field only, the house words (today's
    behaviour); under tenant fields an undeclared class proposes NO words (the no-leak rule)."""
    cur = current or HOUSE_RULES
    vals, scanned = distinct_values(rows, fields, skip)
    per_class, too_broad = {c: [] for c in CLASSES}, []
    for cls in CLASSES:
        for f in fields:
            acc, broad = _candidates(vals[f], scanned, cur["hints"][cls], broad_ratio)
            per_class[cls].extend({"field": f, **a} for a in acc)
            too_broad.extend({"class": cls, "field": f, **b} for b in broad)
    used_fields = []
    for cls in CLASSES:
        for c in per_class[cls]:
            if c["field"] not in used_fields:
                used_fields.append(c["field"])
    prop_fields = [f for f in cur["fields"]] + [f for f in fields if f in used_fields and f not in cur["fields"]]
    current = count_classes(rows, cur, skip)
    refused = {attest_key(b["class"], b["token"]) for b in current["refused"]}
    tokens = {}
    for cls in CLASSES:
        hits = []
        for c in per_class[cls]:
            if c["token"] not in hits:
                hits.append(c["token"])
        if cur["declared"]["tokens"]:
            keep = [t for t in cur["tokens"][cls] if attest_key(cls, t) not in refused]
            tk = keep + [t for t in hits if t not in keep]
        elif hits:
            tk = hits
        else:
            tk = list(cur["tokens"][cls]) if prop_fields == list(HOUSE_FIELDS) else []
        tokens[cls] = tk
    proposal = {"fields": prop_fields, "tokens": tokens}
    # the preview resolves the proposal exactly as the save would store it (fields + tokens over the
    # org's exact map and attestations; the house auto tokens follow the no-leak rule, never carried)
    proposed_rules = resolve_rules({**{k: v for k, v in (cur or {}).items() if k in ("exact", "hints", "metric_hints", "broad_attested")},
                                    **proposal})
    return {"scanned": scanned, "distinct": {f: len(vals[f]) for f in fields}, "per_class": per_class,
            "too_broad": too_broad, "proposal": proposal,
            "preview": count_classes(rows, proposed_rules, skip),
            "current": current, "refused": current["refused"]}


def suggest_metric_rules(rows, resolved, hints=None, skip=None, broad_ratio=BROAD_RATIO):
    """For each Executive-MTD LINE bucket: what its resolved rule matches today over these rows, and —
    when it matches NOTHING (the mig-962 silent-zero detector's gap) — a proposal built from the
    bucket's hint words over category / department / product name, as `<field>_contains` tokens MERGED
    onto the current rule (nothing a tenant set is lost). PURE."""
    hints = hints or HOUSE_METRIC_HINTS
    vals, scanned = distinct_values(rows, [f for f, _ in METRIC_FIELDS], skip)
    out = {}
    for bucket in _emd.LINE_BUCKETS:
        entry = (resolved or {}).get(bucket) or {}
        rule = dict(entry.get("rules") or {})
        matched = 0
        for row in rows or []:
            if skip is not None and skip(row):
                continue
            if _emd.line_match(rule, _s(row.get("department")), _s(row.get("category")), _s(row.get("product_desc"))):
                matched += 1
        item = {"bucket": bucket, "matched": matched, "source": entry.get("source", "default"),
                "applicable": bool(entry.get("applicable", True)), "rules": rule, "candidates": [],
                "proposal": None, "preview": None}
        if matched == 0 and scanned and item["applicable"]:
            proposal = dict(rule)
            for f, key in METRIC_FIELDS:
                acc, _broad = _candidates(vals[f], scanned, hints.get(bucket) or [], broad_ratio)
                if acc:
                    item["candidates"].extend({"field": f, **a} for a in acc)
                    proposal[key] = sorted({*(proposal.get(key) or []), *(a["token"] for a in acc)})
            if item["candidates"]:
                pv = 0
                for row in rows or []:
                    if skip is not None and skip(row):
                        continue
                    if _emd.line_match(proposal, _s(row.get("department")), _s(row.get("category")), _s(row.get("product_desc"))):
                        pv += 1
                item["proposal"], item["preview"] = proposal, pv
        out[bucket] = item
    return {"scanned": scanned, "buckets": out}


# ── the save shape (the PUT merges; the Activation-Details basis keys beside ours are kept) ───────
def merge_into_raw(current_raw, fields=None, tokens=None, exact=None, broad_ok=None, by=None, shares=None):
    """The JSON to store: the org's existing activation_details_rules with OUR keys replaced. PURE.
    `tokens` merges PER CLASS over the stored map (a partial body never erases another class; an
    explicit [] disables a class); a class never given is NOT stored — the resolver fills it (house words
    under the house field, nothing under tenant fields) — so no copy of the house list lives in a tenant
    row. `broad_ok` = ['<class>:<token>', …] the person attested by name (recorded with `by`, the time
    and the measured share from `shares`); an attestation for a word no longer in force is dropped."""
    out = dict(current_raw) if isinstance(current_raw, dict) else {}
    if fields is not None:
        out["fields"] = _norm_fields(fields)
    if tokens is not None and isinstance(tokens, dict):
        merged = dict(out.get("tokens")) if isinstance(out.get("tokens"), dict) else {}
        for c in CLASSES:
            if tokens.get(c) is not None:
                merged[c] = _norm_tokens(tokens.get(c), [])
        out["tokens"] = {c: v for c, v in merged.items() if c in CLASSES}
    if exact is not None and isinstance(exact, dict):
        out["exact"] = _norm_exact(exact, {})
    att = _norm_attested(out.get("broad_attested"))
    ratio_of = {attest_key(x["class"], x["token"]): x.get("ratio") for x in (shares or [])}
    for key in norm_broad_ok(broad_ok):
        att[key] = {"by": by, "at": _now_iso(), "ratio": ratio_of.get(key)}
    if att or "broad_attested" in out:
        in_force = {attest_key(c, t) for c, ts in (out.get("tokens") or {}).items() if isinstance(ts, list) for t in ts}
        out["broad_attested"] = {k: v for k, v in att.items() if k in in_force}
    return out
