"""Executive-MTD metric DEFINITIONS: carrier presets, resolution, and the silent-zero detector.

WHY (owner report 2026-09-04: "executive mtd in cellfonz r us does not have bill payment qty,
but luxelink has it, fix it"):

    ROOT CAUSE, found by reading the two orgs' live vocabulary side by side. The `bill_payment`
    bucket is matched by EXACT department/category membership against tokens stored per org in
    commcalc.exec_metric_config (mig 204), falling back to a token set hard-coded in Python.
    Those fallback tokens are ONE carrier's spelling:

        department ['rtr']  ·  category ['rtr product', 'other carr. payments']

    LuxeLink's b2bsoft export spells its bill-payment lines exactly that way (department 'rtr',
    category 'other carr. payments'), so LuxeLink matched and reported. CellfonzRUs's export
    spells the SAME concept differently — department 'bill payments', category 'boost rtr' /
    'xfinity refill' — so it matched NOTHING and the column read ~0 (live: 2 lines / $74.77 in
    August 2026, against 6,869 real bill-payment lines worth $359,873.05).

    This is the LI/1115-Liberty defect class again (owner 2026-09-04: "fix as a design not a band
    aid as this could happen to a new store also"): a vocabulary hard-coded to one tenant's
    spelling, and NO signal when it matches nothing. A new tenant whose POS spells a department
    differently gets a silently-zero column with no error — the report simply lies quietly.

WHAT THIS MODULE IS (pure; no DB, no I/O; stdlib only — proof: harness_exec_metric_defs.py):

  1. CODE_DEFAULTS + `line_match` — the ONE bucket-rule vocabulary and the ONE line predicate.
     router._EXEC_METRIC_DEFAULTS / router._exec_line_match now re-point HERE rather than keeping
     a second copy (the duplicate-check build gate, CLAUDE.md 2026-09-02).

  2. CARRIER PRESETS, resolved exactly like the mig-945/953 label presets and reusing THEIR
     carrier identity primitives (report_labels.normalize_carrier_code / default_carrier) — no
     second carrier resolver:

         tenant row  >  house carrier preset (for the org's carrier)  >  built-in code default

     Storage EXTENDS the existing commcalc.exec_metric_config with a nullable `carrier` column
     (mig 962): carrier IS NULL = that org's own definition (every pre-962 row, unchanged);
     carrier NOT NULL at the HOUSE org = that carrier's preset. LAZY auto-assign, same as
     mig 945: a new tenant that picks a carrier at setup inherits the preset the moment the
     resolver runs — no setup hook, and an org with no carrier row / no preset falls through to
     the built-in defaults, byte-identical to today.

  3. `bucket_coverage` — THE PRECAUTION. A bucket whose rules match ZERO lines while the period
     HAS sales is reported as a gap, naming the bucket and the most common department/category
     values it did not match, so the fix is one settings edit away. Same philosophy (and the same
     display-only, never-raises posture) as the existing `_classification_gaps` banner that names
     uncounted contract types. A silently-zero metric is the failure this makes impossible.

RULE TWO: no carrier or tenant name appears in this module. The presets are DATA (mig 962 rows);
this file only knows how to resolve and how to notice a bucket that matches nothing.
"""

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# ── The ONE built-in bucket vocabulary (moved verbatim from router._EXEC_METRIC_DEFAULTS) ─────────
# These stay as the LAST fallback only. They are one carrier's spelling by history; a tenant whose
# POS differs is expected to inherit a carrier preset (layer 2) or set its own row (layer 1).
CODE_DEFAULTS = {
    'activation':     {'rules': {'byod': ['byod'], 'upgrade': ['upgrade'], 'port': ['port']}, 'basis': 'count'},
    'phones':         {'rules': {'category': ['cellphone', 'kittedbranded']}, 'basis': 'count'},
    'bill_payment':   {'rules': {'department': ['rtr'], 'category': ['rtr product', 'other carr. payments']}, 'basis': 'count'},
    'accessory':      {'rules': {'category': ['accessory', 'handsetbranded', 'accessories']}, 'basis': 'ext_price'},
    'activation_fee': {'rules': {'product_desc_contains': ['access charge']}, 'basis': 'ext_price'},
    'protect':        {'rules': {'product_desc_contains': ['protect'],
                                 'exclude_product_desc_contains': ['screen protect'],
                                 'exclude_department': ['rtr'],
                                 'exclude_category': ['rtr product', 'other carr. payments']}, 'basis': 'count'},
}
BUCKETS = tuple(CODE_DEFAULTS.keys())

# Buckets whose rules classify SALE LINES by department/category/product_desc. `activation` is the
# odd one out: its rules are contract-type keyword tokens consumed by _exec_act_class, not line
# predicates, so the coverage detector must skip it (a zero here means "no activations", which is
# a legitimate answer, not a broken definition).
LINE_BUCKETS = ('phones', 'bill_payment', 'accessory', 'activation_fee', 'protect')


def line_match(rule, dept, cat, pdesc):
    """True if a sale line matches a bucket rule (all case-insensitive; inputs already lowercased).
    category/department = EXACT membership in a token list; product_desc_contains = substring;
    exclude_* negate first. Match = ANY positive predicate true AND no exclusion true.

    Moved here from router._exec_line_match so the predicate has ONE definition. The exact-vs-
    substring asymmetry is deliberate and load-bearing: a substring department match would let
    'boost rtr' inside "$8 included in your boost rtr payment" (a PROTECTION-plan line) count as a
    bill payment — 1,339 such lines exist in the house org's August 2026 data alone."""
    rule = rule or {}
    if rule.get('exclude_department') and dept in rule['exclude_department']:
        return False
    if rule.get('exclude_category') and cat in rule['exclude_category']:
        return False
    if rule.get('exclude_product_desc_contains') and any(t in pdesc for t in rule['exclude_product_desc_contains']):
        return False
    if rule.get('category') and cat in rule['category']:
        return True
    if rule.get('department') and dept in rule['department']:
        return True
    if rule.get('product_desc_contains') and any(t in pdesc for t in rule['product_desc_contains']):
        return True
    return False


def _norm_carrier(code_or_name):
    """Canonical carrier code. Delegates to report_labels (mig 945/953) so the preset key written
    by a migration is the key this resolver looks up — ONE carrier identity, not two. Falls back to
    a local lowercase/strip only if that import is unavailable, so this module stays importable
    stand-alone (the harness relies on that)."""
    try:
        from app.modules.commcalc.report_labels import normalize_carrier_code
        return normalize_carrier_code(code_or_name)
    except Exception:
        return str(code_or_name or '').strip().lower()


def org_carrier(carrier_rows):
    """The org's carrier code for preset lookup ('' = none chosen → presets do not apply).
    Delegates to report_labels.default_carrier for the same is_default/sole/first precedence."""
    try:
        from app.modules.commcalc.report_labels import default_carrier
        return default_carrier(carrier_rows)
    except Exception:
        for r in (carrier_rows or []):
            if r and (r.get('code') or r.get('name')):
                return _norm_carrier(r.get('code') or r.get('name'))
        return ''


def split_rows(rows, org_id, house_org=HOUSE_ORG):
    """Split raw exec_metric_config rows into this org's OWN definitions and the house CARRIER
    PRESETS. Mirrors report_labels.parse_label_rows.

    Returns {"own": {bucket: {'rules','basis'}},
             "presets": {carrier_code: {bucket: {'rules','basis'}}}}

    A row is a PRESET only when it carries a carrier AND belongs to the house org — a tenant can
    never publish a preset for anyone else. A house row with no carrier is the house org's OWN
    definition (which is what every pre-962 row is), so the house tenant keeps behaving like any
    other tenant."""
    own, presets = {}, {}
    for r in (rows or []):
        if not r:
            continue
        bucket = r.get('bucket')
        if bucket not in CODE_DEFAULTS:
            continue          # unknown bucket = ignored, never crashes the report
        entry = {'rules': r.get('rules') or {}, 'basis': r.get('basis') or CODE_DEFAULTS[bucket]['basis']}
        carrier = _norm_carrier(r.get('carrier'))
        row_org = r.get('org_id')
        if carrier and row_org == house_org:
            presets.setdefault(carrier, {})[bucket] = entry
        elif not carrier and row_org == org_id:
            own[bucket] = entry
    return {"own": own, "presets": presets}


def resolve(rows, org_id, carrier_rows=None, house_org=HOUSE_ORG):
    """Resolve the effective per-bucket definitions for one org.

        tenant row  >  house carrier preset  >  built-in code default

    Returns {bucket: {'rules': {...}, 'basis': ..., 'source': 'tenant'|'carrier_preset'|'default'}}.
    `source` is reported so the settings UI (and the harness) can show WHERE a definition came
    from — an inherited preset must never look like a choice the tenant made.

    Byte-identical to the pre-962 behavior whenever the org has no carrier or no preset exists:
    every bucket falls through to its own row, else the code default."""
    split = split_rows(rows, org_id, house_org=house_org)
    carrier = org_carrier(carrier_rows)
    preset = split["presets"].get(carrier, {}) if carrier else {}
    out = {}
    for bucket, dflt in CODE_DEFAULTS.items():
        if bucket in split["own"]:
            entry, src = split["own"][bucket], 'tenant'
        elif bucket in preset:
            entry, src = preset[bucket], 'carrier_preset'
        else:
            entry, src = {'rules': dict(dflt['rules']), 'basis': dflt['basis']}, 'default'
        out[bucket] = {'rules': dict(entry['rules'] or {}),
                       'basis': entry.get('basis') or dflt['basis'],
                       'source': src}
    return out


def strip_sources(resolved):
    """`resolve` output reduced to the {bucket: {'rules','basis'}} shape the aggregation consumes,
    so adding provenance cannot change a single computed number."""
    return {b: {'rules': dict(v['rules']), 'basis': v['basis']} for b, v in (resolved or {}).items()}


# ── THE PRECAUTION: notice a bucket that matches nothing ──────────────────────────────────────────

def _cell(v):
    return str(v or '').strip().lower()


def bucket_coverage(sale_rows, resolved, buckets=LINE_BUCKETS, top_n=5):
    """Report which LINE buckets matched ZERO rows over a period that HAS rows.

    Returns {'scanned': int,
             'gaps': [{'bucket','source','matched':0,'unmatched_departments':[(value,count)…],
                       'unmatched_categories':[…]}],
             'matched': {bucket: count},
             'note': str|None}

    A gap means the tenant's stored vocabulary for that bucket does not appear in its own data —
    the CellfonzRUs bill-payment defect exactly. The suggestion payload names the department and
    category values that DID occur, because that list is the answer to "what should the tokens be".

    DISPLAY-ONLY and total-preserving: this reads the same rows the aggregation reads and changes
    no computed figure. `scanned == 0` returns no gaps — an empty period is not a broken
    definition, and reporting one would train people to ignore the banner."""
    rows = sale_rows or []
    matched = {b: 0 for b in buckets}
    dept_ct, cat_ct = {}, {}
    scanned = 0
    for r in rows:
        if not r:
            continue
        scanned += 1
        d, c, p = _cell(r.get('department')), _cell(r.get('category')), _cell(r.get('product_desc'))
        if d:
            dept_ct[d] = dept_ct.get(d, 0) + 1
        if c:
            cat_ct[c] = cat_ct.get(c, 0) + 1
        for b in buckets:
            if line_match((resolved.get(b) or {}).get('rules'), d, c, p):
                matched[b] += 1
    gaps = []
    if scanned:
        for b in buckets:
            if matched[b] == 0:
                gaps.append({
                    'bucket': b,
                    'source': (resolved.get(b) or {}).get('source', 'default'),
                    'matched': 0,
                    'unmatched_departments': sorted(dept_ct.items(), key=lambda kv: -kv[1])[:top_n],
                    'unmatched_categories': sorted(cat_ct.items(), key=lambda kv: -kv[1])[:top_n],
                })
    note = None
    if gaps:
        names = ', '.join(g['bucket'] for g in gaps)
        note = (f"{len(gaps)} metric definition(s) matched no sales lines this period ({names}). "
                f"The stored department/category tokens do not appear in this tenant's data, so the "
                f"column reads 0 — set them under Metric definitions.")
    return {'scanned': scanned, 'gaps': gaps, 'matched': matched, 'note': note}
