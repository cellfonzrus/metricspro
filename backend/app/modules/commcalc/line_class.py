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

RULE TWO: no carrier, POS vendor, tenant or product name appears here. The hint words are generic
activation vocabulary; a tenant's own words are config rows. stdlib only — the harness imports this file
directly.
"""
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
# generic activation vocabulary (config: `hints` in the same JSON; these are the house defaults)
HOUSE_HINTS = {
    "activation": ["new activation", "activation", "new line", "add a line", "add-a-line", "aal", "new act"],
    "upgrade": ["upgrade"],
    "byod": ["byod", "bring your own", "customer provided", "customer owned", "customer phone", "own device", "sim only"],
    "port": ["port-in", "port in", "port"],
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
# a hint that names this share of ALL scanned lines is a department word, not a type
BROAD_RATIO = 0.8
# the keys this module owns inside accessory_config.activation_details_rules (the Activation-Details
# basis keys — edge_* / upgrade_hidden_* — live beside them and are never touched by a save here)
OWNED_KEYS = ("fields", "tokens", "exact", "auto_activation_tokens", "hints", "metric_hints")


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


def resolve_rules(raw=None, ct_map=None, legacy_activation=None):
    """The full rules dict for one org. PURE.

    `raw`               = accessory_config.activation_details_rules (dict / None / junk).
    `ct_map`            = the mig-213 contract_type_map ({value: premium|upgrade|byod|none}) — absorbed
                          into `exact` (a tenant's newer `exact` key overrides it per value).
    `legacy_activation` = a TENANT-authored exec_metric_config 'activation' rules row
                          ({byod, upgrade, port} token lists), honoured only for a class `tokens`
                          does not declare — so an org that tuned its port token before this design
                          keeps it until it saves the one home; house defaults otherwise.
    Missing keys → house defaults (today's behaviour). Unknown keys are ignored."""
    raw = raw if isinstance(raw, dict) else {}
    tokens_raw = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else {}
    legacy = legacy_activation if isinstance(legacy_activation, dict) else {}
    tokens = {}
    for cls in CLASSES:
        v = tokens_raw.get(cls)
        if v is None and cls in ("byod", "upgrade", "port"):
            v = legacy.get(cls)
        tokens[cls] = _norm_tokens(v, HOUSE_TOKENS[cls])
    exact = _norm_exact(ct_map, {})
    exact = _norm_exact(raw.get("exact"), exact)
    hints_raw = raw.get("hints") if isinstance(raw.get("hints"), dict) else {}
    mhints_raw = raw.get("metric_hints") if isinstance(raw.get("metric_hints"), dict) else {}
    declared = {"fields": isinstance(raw.get("fields"), (list, tuple)) and bool(raw.get("fields")),
                "tokens": bool(tokens_raw), "exact": bool(exact)}
    return {
        "fields": _norm_fields(raw.get("fields")),
        "tokens": tokens,
        "exact": exact,
        "auto_activation_tokens": _norm_tokens(raw.get("auto_activation_tokens"), HOUSE_AUTO_ACTIVATION_TOKENS),
        "hints": {c: _norm_tokens(hints_raw.get(c), HOUSE_HINTS[c]) for c in CLASSES},
        "metric_hints": {b: _norm_tokens(mhints_raw.get(b), HOUSE_METRIC_HINTS[b]) for b in HOUSE_METRIC_HINTS},
        "declared": declared,
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
    return {"scanned": scanned, "skipped": skipped, "unclassified_lines": unclassified, "classes": classes,
            "activation_type_lines": sum(classes[c]["lines"] for c in ACTIVATION_TYPE_CLASSES),
            "activation_type_transactions": len(act_txns),
            "fields": list(r["fields"]), "source": r["source"]}


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


def suggest_rules(rows, current=None, skip=None, fields=CANDIDATE_FIELDS, broad_ratio=BROAD_RATIO):
    """Propose `fields` + `tokens` per class from the rows' own vocabulary. PURE.

    Returns {"scanned", "distinct": {field: n}, "per_class": {cls: [{field, token, lines, ratio, samples}]},
             "too_broad": [...], "proposal": {"fields": [...], "tokens": {cls: [...]}},
             "preview": count_classes(rows, proposal-as-rules), "current": count_classes(rows, current)}.
    The preview is THE predicate over the proposal, so the counts shown are the counts every report
    will produce once saved. A class with no hit proposes nothing (its tokens stay as they are)."""
    cur = current or HOUSE_RULES
    vals, scanned = distinct_values(rows, fields, skip)
    per_class, too_broad = {c: [] for c in CLASSES}, []
    for cls in CLASSES:
        for f in fields:
            acc, broad = _candidates(vals[f], scanned, cur["hints"][cls], broad_ratio)
            per_class[cls].extend({"field": f, **a} for a in acc)
            too_broad.extend({"class": cls, "field": f, **b} for b in broad)
    tokens = {}
    used_fields = []
    for cls in CLASSES:
        tk = []
        for c in per_class[cls]:
            if c["token"] not in tk:
                tk.append(c["token"])
            if c["field"] not in used_fields:
                used_fields.append(c["field"])
        tokens[cls] = tk if tk else list(cur["tokens"][cls])
    prop_fields = [f for f in cur["fields"]] + [f for f in fields if f in used_fields and f not in cur["fields"]]
    proposal = {"fields": prop_fields, "tokens": tokens}
    proposed_rules = resolve_rules({**{k: v for k, v in (cur or {}).items() if k in ("exact", "auto_activation_tokens", "hints", "metric_hints")},
                                    **proposal})
    return {"scanned": scanned, "distinct": {f: len(vals[f]) for f in fields}, "per_class": per_class,
            "too_broad": too_broad, "proposal": proposal,
            "preview": count_classes(rows, proposed_rules, skip),
            "current": count_classes(rows, cur, skip)}


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
def merge_into_raw(current_raw, fields=None, tokens=None, exact=None):
    """The JSON to store: the org's existing activation_details_rules with OUR keys replaced. PURE."""
    out = dict(current_raw) if isinstance(current_raw, dict) else {}
    if fields is not None:
        out["fields"] = _norm_fields(fields)
    if tokens is not None and isinstance(tokens, dict):
        out["tokens"] = {c: _norm_tokens(tokens.get(c), HOUSE_TOKENS[c]) if tokens.get(c) is not None else list(HOUSE_TOKENS[c])
                         for c in CLASSES}
    if exact is not None and isinstance(exact, dict):
        out["exact"] = _norm_exact(exact, {})
    return out
