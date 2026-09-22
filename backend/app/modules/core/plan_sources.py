"""WHERE A TENANT'S PLAN NAMES COME FROM — per-org config, ONE registry of landed sources, PURE.

OWNER (2026-09-22, verbatim): *"we have enough plans in the system to bring over but it does not give
an option to bring over"* — on the POS wizard step "Add your plans & features": "0 record(s) ready to
create in pos.service_plans. Neither source has anything yet … Bring over 0".

THE INSTANCE (org f4f1c16e…): commcalc.product_mrc 0 rows, commcalc.raw_mi 0 rows — the only two tables
the step read. Meanwhile commcalc.raw_sales held 8,350 landed sales lines whose category path names a
rate plan (32 distinct product names — "iPhone Rate Plan (DPA)" 2,632, "New Activation Rate Plan" 558 …
and, under the SAME branch, rebate lines: "DPA Upgrade iPhone (Rate Plan Rebate)" 1,587 — a rebate is
NOT a plan) and commcalc.commission_ledger held 1,494 statement lines naming the carrier's price plans.
The tenant has no subscriber report and may never have one; its plan names live in its sales export
and its commission statement, and the wizard could not see either.

THE CLASS (fixed here for every caller — CLAUDE.md 2026-09-20 "A fix is a DESIGN fix"):
  A wizard step derived a tenant fact from a FIXED PAIR of per-feed tables; a tenant whose data lives
  in another landed source saw "0" and no way to bring anything over. The P&L commission source (#273,
  index §4b) was the same class — fixed with a per-org source switch, one resolver, a suggestion the
  person confirms. The activation words (#267 / #271, index §30.12) were the same class — fixed with
  per-org words over the tenant's OWN vocabulary, proposed with counts, guarded when too broad,
  confirmed by the person. This module is that fix for plan names.

WHAT THIS MODULE IS:
  · `HOUSE_SOURCES` — THE ONE registry of the landed sources that can carry a plan name, in precedence
    order (a name the operator priced in the catalogue beats one observed anywhere else). Two kinds:
      - `catalogue` / `subscribers`: the pairing mig 074 defined (product_mrc keyed on raw_mi.customer_plan),
        each reporting an MRC. ON by default — today's behaviour, byte-identical (the compatibility pin
        in harness_pos_onboarding.py §P17).
      - `lines`: a landed line-level source (the sales export, the commission statement) whose lines
        carry a plan name in one column when OTHER words on the line say it is a plan line. OFF until
        the person confirms the words; no MRC (the source reports none) — the row says where to price it.
  · `resolve_config(raw)` — the org's `pos.pos_settings` row `plan_sources` (mig 725, the POS module's
    existing per-org config kv; NO migration) merged over the house registry: a tenant can switch a
    source on/off and declare its include / exclude words; it can NOT add a table (a table is a
    registry entry, never a tenant string). House defaults fill every missing key.
  · `line_matches(row, src)` — THE predicate for a `lines` source: any configured field contains an
    include word AND no configured field contains an exclude word ("rebate" is how the rebate lines
    under the rate-plan branch stay out).
  · `suggest_source(rows, src, hints)` — the 2.5a engine pattern (line_class.distinct_values /
    candidates, the same BROAD_RATIO): scan the DISTINCT values of the source's fields, match the
    generic hint words (config: `hints` in the same JSON), drop a word naming ≥ 80% of all lines (too
    broad — reported, never proposed), propose EXCLUDE words with the count of matched lines each
    would remove, and preview the lines / distinct names the proposal yields — computed by THE
    predicate, so what the person confirms is what the wizard brings over.
  · `refused_words(rows, src, attested)` — the guard over what is SAVED (index §30.12 addendum, face 1):
    an include word in force that names ≥ BROAD_RATIO of the source's lines is refused unless the
    person attested it by name (`broad_attested['<source>:<word>']`).
  · `plan_rows(folded, src, carrier)` — the candidates with their PROVENANCE (source, lines, first /
    last seen) and, since a `lines` source reports no charge, `monthly_fee` None + `mrc_next`.
  · `empty_sentence(sources)` — names the sources ACTUALLY checked and what each held (never "neither
    source" when four were checked).

RULE TWO: no carrier, POS vendor, tenant or product name appears here. The hint words are generic
plan vocabulary; a tenant's own words are config. The harness imports this file directly.
"""
from datetime import datetime as _dt, timezone as _tz

from app.modules.commcalc import line_class as _lc     # the 2.5a suggestion engine (pure)

CONFIG_HOME = "pos.pos_settings"          # mig 725 — the POS module's per-org config kv (org row: store_code IS NULL)
CONFIG_KEY = "plan_sources"
MRC_NEXT = "price it under Payout Schedules → Plan MRC"
BROAD_RATIO = _lc.BROAD_RATIO             # the SAME ratio the activation words use (one guard, one number)
KINDS = ("catalogue", "subscribers", "lines")

# ── THE ONE REGISTRY (precedence order — first wins a name collision) ────────────────────────────
HOUSE_SOURCES = (
    {"key": "catalogue", "kind": "catalogue", "table": "commcalc.product_mrc",
     "label": "Your rate-plan catalogue", "where": "Payout Schedules → Plan MRC",
     "name_field": "plan_pattern", "mrc_field": "mrc", "enabled": True},
    {"key": "subscribers", "kind": "subscribers", "table": "commcalc.raw_mi",
     "label": "Your subscriber report", "where": "the carrier subscriber / MI report upload",
     "name_field": "customer_plan", "mrc_field": "base_mrc", "enabled": True},
    {"key": "sales_lines", "kind": "lines", "table": "commcalc.raw_sales",
     "label": "Your sales export (line level)", "where": "the sales upload / onboarding intake",
     "name_field": "product_desc", "fields": ("category", "product_desc", "department"),
     "date_field": "trans_date", "enabled": False, "include": (), "exclude": ()},
    {"key": "statement_lines", "kind": "lines", "table": "commcalc.commission_ledger",
     "label": "Your commission statement", "where": "the commission-statement intake",
     "name_field": "product_name", "fields": ("product_name", "order_type", "category"),
     "date_field": "trans_date", "enabled": False, "include": (), "exclude": ()},
)
# generic plan vocabulary — the engine's INPUT, never the person's starting text (the proposal is)
HOUSE_INCLUDE_HINTS = ["rate plan", "price plan", "service plan", "monthly plan", "unlimited", "plan"]
# words that mean a line under a plan branch is NOT a plan (a rebate, a bonus, a clawback …)
HOUSE_EXCLUDE_HINTS = ["rebate", "kicker", "spiff", "bonus", "credit", "chargeback", "clawback", "adjustment"]
OWNED_KEYS = ("sources", "hints", "broad_attested")
PLAN_NAME_MAX = 120


def _now_iso():
    return _dt.now(_tz.utc).replace(microsecond=0).isoformat()


def _s(v):
    return str(v or "").strip().lower()


def _words(value, default=()):
    if value is None:
        return [str(w) for w in default]
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for w in (value if isinstance(value, (list, tuple)) else []):
        w = _s(w)
        if w and w not in out:
            out.append(w)
    return out


def source_keys():
    return [s["key"] for s in HOUSE_SOURCES]


def attest_key(source_key, word):
    return f"{_s(source_key)}:{_s(word)}"


def _norm_attested(mapping):
    out = {}
    for k, v in (mapping.items() if isinstance(mapping, dict) else []):
        key = _s(k)
        if key and ":" in key:
            out[key] = dict(v) if isinstance(v, dict) else {}
    return out


def norm_broad_ok(value):
    if isinstance(value, str):
        value = value.split(",")
    return [_s(x) for x in (value if isinstance(value, (list, tuple)) else []) if _s(x) and ":" in _s(x)]


# ── the config ───────────────────────────────────────────────────────────────────────────────────
def resolve_config(raw=None):
    """The rules in force for one org: the house registry with the tenant's `sources` (per key:
    enabled / include / exclude), `hints` and `broad_attested` merged over it. PURE.
    Returns {"sources": [...], "declared": bool, "source": "house"|"tenant", "hints": {...},
             "broad_attested": {...}}. A key the tenant names that is not in the registry is ignored —
    a table is a registry entry, never a tenant string."""
    raw = raw if isinstance(raw, dict) else {}
    tenant = raw.get("sources") if isinstance(raw.get("sources"), dict) else {}
    hints_raw = raw.get("hints") if isinstance(raw.get("hints"), dict) else {}
    hints = {"include": _words(hints_raw.get("include"), HOUSE_INCLUDE_HINTS),
             "exclude": _words(hints_raw.get("exclude"), HOUSE_EXCLUDE_HINTS)}
    out, declared = [], False
    for house in HOUSE_SOURCES:
        src = {k: (list(v) if isinstance(v, tuple) else v) for k, v in house.items()}
        t = tenant.get(house["key"])
        t = t if isinstance(t, dict) else {}
        src["declared"] = bool(t)
        declared = declared or bool(t)
        if "enabled" in t:
            src["enabled"] = bool(t.get("enabled"))
        if house["kind"] == "lines":
            src["include"] = _words(t.get("include"), house["include"])
            src["exclude"] = _words(t.get("exclude"), house["exclude"])
        out.append(src)
    return {"sources": out, "declared": declared, "source": "tenant" if declared else "house",
            "hints": hints, "broad_attested": _norm_attested(raw.get("broad_attested"))}


HOUSE_CONFIG = resolve_config(None)


def source_of(cfg, key):
    return next((s for s in (cfg or HOUSE_CONFIG)["sources"] if s["key"] == key), None)


# ── THE predicate for a `lines` source ───────────────────────────────────────────────────────────
def line_texts(row, src):
    return [_s((row or {}).get(f)) for f in src.get("fields") or ()]


def _hit(words, texts):
    return any(w in t for w in words for t in texts if t)


def line_matches(row, src):
    """A line is a plan line when any configured field contains an include word and none contains an
    exclude word. A source with no include words matches NOTHING (nothing is ever guessed)."""
    inc = src.get("include") or []
    if not inc:
        return False
    texts = line_texts(row, src)
    return _hit(inc, texts) and not _hit(src.get("exclude") or [], texts)


def fold_plan_lines(rows, src):
    """PURE. Distinct plan names on the matched lines — first spelling kept, case-insensitive — with the
    line count and the first / last date seen. Sorted by lines desc: what the tenant sells most, first."""
    seen = {}
    nf, df = src.get("name_field"), src.get("date_field")
    for row in rows or []:
        if not line_matches(row, src):
            continue
        name = str((row or {}).get(nf) or "").strip()
        if not name:
            continue
        slot = seen.setdefault(name.lower(), {"plan_name": name[:PLAN_NAME_MAX], "lines": 0,
                                              "first_seen": None, "last_seen": None})
        slot["lines"] += 1
        d = str((row or {}).get(df) or "").strip()[:10] if df else ""
        if d:
            slot["first_seen"] = d if slot["first_seen"] is None or d < slot["first_seen"] else slot["first_seen"]
            slot["last_seen"] = d if slot["last_seen"] is None or d > slot["last_seen"] else slot["last_seen"]
    out = list(seen.values())
    out.sort(key=lambda p: (-p["lines"], p["plan_name"].lower()))
    return out


def plan_rows(folded, src, carrier=""):
    """The candidates as pos.service_plans rows-to-be, each carrying its provenance. A `lines` source
    reports no monthly charge, so monthly_fee is None and `mrc_next` says where to price it — nothing
    is invented."""
    return [{"plan_name": p["plan_name"], "monthly_fee": None, "carrier": carrier or "",
             "plan_description": None, "status": "active",
             "source": src["key"], "source_label": src.get("label"), "lines": p["lines"],
             "first_seen": p.get("first_seen"), "last_seen": p.get("last_seen"), "mrc_next": MRC_NEXT}
            for p in folded or []]


# ── the suggestion engine (the 2.5a pattern, reused — not a sibling) ──────────────────────────────
def _include_candidates(vals, scanned, fields, hints, broad_ratio):
    accepted, broad = [], []
    for f in fields:
        acc, brd = _lc.candidate_words(vals.get(f) or {}, scanned, hints, broad_ratio)
        accepted.extend({"field": f, **a} for a in acc)
        broad.extend({"field": f, **b} for b in brd)
    return accepted, broad


def _count(rows, src):
    lines, names = 0, set()
    nf = src.get("name_field")
    for row in rows or []:
        if line_matches(row, src):
            lines += 1
            n = _s((row or {}).get(nf))
            if n:
                names.add(n)
    return {"lines": lines, "plans": len(names)}


def word_shares(rows, src):
    """{word: {lines, ratio}} — how many of the source's lines each include word in force names."""
    n, hits = 0, {w: 0 for w in (src.get("include") or [])}
    for row in rows or []:
        n += 1
        texts = line_texts(row, src)
        for w in hits:
            if any(w in t for t in texts if t):
                hits[w] += 1
    return {w: {"lines": c, "ratio": round((c / n) if n else 0.0, 3)} for w, c in hits.items()}, n


def refused_words(rows, src, attested=None, broad_ratio=BROAD_RATIO):
    """The include words in force that are TOO BROAD over these rows and not attested by name."""
    shares, n = word_shares(rows, src)
    att = _norm_attested(attested)
    out = []
    for w, sh in shares.items():
        if n and sh["ratio"] >= broad_ratio and attest_key(src["key"], w) not in att:
            out.append({"source": src["key"], "word": w, "lines": sh["lines"], "ratio": sh["ratio"], "scanned": n})
    return out


def refusal_sentence(refused, label=None):
    if not refused:
        return None
    parts = [f"\"{r['word']}\" names {int(round(r['ratio'] * 100))}% of {r['scanned']:,} lines"
             + (f" in {label}" if label else "") for r in refused]
    return ("word refused — " + "; ".join(parts) + " — that is a heading, not a plan. Pick a narrower "
            "word, or confirm it by name if every one of those lines really is a plan.")


def suggest_source(rows, src, hints=None, attested=None, broad_ratio=BROAD_RATIO):
    """Propose include / exclude words for ONE `lines` source from the rows' own vocabulary. PURE.

    Returns {"scanned", "distinct": {field: n}, "include": [{field, token, lines, ratio, samples}],
             "too_broad": [...], "exclude": [{token, removes, samples}], "proposal": {include, exclude},
             "preview": {lines, plans}, "current": {lines, plans}, "refused": [...]}.
    THE PROPOSAL IS THE ONLY SEED of the card's editable words: the person's declared words (minus any
    refused as too broad) plus the hint hits; with nothing declared, the hits. `preview` is THE
    predicate over the proposal, so the counts shown are the counts "Bring over" will create."""
    hints = hints or HOUSE_CONFIG["hints"]
    fields = list(src.get("fields") or ())
    vals, scanned = _lc.distinct_values(rows, fields)
    include, too_broad = _include_candidates(vals, scanned, fields, hints["include"], broad_ratio)
    hits = []
    for c in include:
        if c["token"] not in hits:
            hits.append(c["token"])
    refused = refused_words(rows, src, attested, broad_ratio) if src.get("include") else []
    refused_keys = {attest_key(r["source"], r["word"]) for r in refused}
    if src.get("declared") and src.get("include"):
        keep = [w for w in src["include"] if attest_key(src["key"], w) not in refused_keys]
        prop_inc = keep + [h for h in hits if h not in keep]
    else:
        prop_inc = hits
    # exclude words are measured over the lines the proposed include words would take — "rebate would
    # drop 3,165 of them" is the number the person needs, not how often the word appears anywhere
    probe = {**src, "include": prop_inc, "exclude": []}
    exclude, seen_sets = [], []
    for h in hints["exclude"]:
        removes, samples = 0, {}
        for row in rows or []:
            if not line_matches(row, probe):
                continue
            texts = line_texts(row, probe)
            if any(h in t for t in texts if t):
                removes += 1
                for t in texts:
                    if h in t:
                        samples[t] = samples.get(t, 0) + 1
                        break
        if removes:
            top = [v for v, _ in sorted(samples.items(), key=lambda kv: -kv[1])[:3]]
            exclude.append({"token": h, "removes": removes, "samples": top})
    ex_hits = [e["token"] for e in exclude]
    prop_exc = list(src.get("exclude") or []) + [h for h in ex_hits if h not in (src.get("exclude") or [])] \
        if src.get("declared") else ex_hits
    proposal = {"include": prop_inc, "exclude": prop_exc}
    return {"scanned": scanned, "distinct": {f: len(vals.get(f) or {}) for f in fields},
            "include": include, "too_broad": too_broad, "exclude": exclude, "proposal": proposal,
            "preview": _count(rows, {**src, **proposal}), "current": _count(rows, src),
            "refused": refused}


# ── the save shape ───────────────────────────────────────────────────────────────────────────────
def merge_into_raw(current_raw, sources=None, broad_ok=None, by=None, shares=None):
    """The JSON to store: the org's existing plan_sources row with OUR keys replaced. PURE.
    `sources` = {key: {enabled?, include?, exclude?}} merged PER SOURCE (a partial body never erases
    another source's words); an unknown key is dropped; `broad_ok` = ['<source>:<word>', …] the person
    attested by name, recorded with `by`, the time and the measured share; an attestation for a word
    no longer in force is dropped."""
    out = dict(current_raw) if isinstance(current_raw, dict) else {}
    merged = dict(out.get("sources")) if isinstance(out.get("sources"), dict) else {}
    for key, body in (sources.items() if isinstance(sources, dict) else []):
        house = next((h for h in HOUSE_SOURCES if h["key"] == key), None)
        if not house or not isinstance(body, dict):
            continue
        cur = dict(merged.get(key)) if isinstance(merged.get(key), dict) else {}
        if "enabled" in body:
            cur["enabled"] = bool(body.get("enabled"))
        if house["kind"] == "lines":
            if body.get("include") is not None:
                cur["include"] = _words(body.get("include"))
            if body.get("exclude") is not None:
                cur["exclude"] = _words(body.get("exclude"))
        merged[key] = cur
    out["sources"] = merged
    att = _norm_attested(out.get("broad_attested"))
    ratio_of = {attest_key(x["source"], x["word"]): x.get("ratio") for x in (shares or [])}
    for key in norm_broad_ok(broad_ok):
        att[key] = {"by": by, "at": _now_iso(), "ratio": ratio_of.get(key)}
    if att or "broad_attested" in out:
        in_force = {attest_key(k, w) for k, v in merged.items() if isinstance(v, dict)
                    for w in (v.get("include") or [])}
        out["broad_attested"] = {k: v for k, v in att.items() if k in in_force}
    return out


# ── the empty state names what was checked ────────────────────────────────────────────────────────
def source_sentence(info):
    """One clause per checked source: what it is, where it lives, what it held."""
    label, table = info.get("label") or info.get("key"), info.get("table") or ""
    head = f"{label} ({table})"
    if info.get("error"):
        return f"{head} — could not be read: {str(info['error'])[:120]}"
    if info.get("kind") == "lines":
        sug = info.get("suggest") or {}
        if info.get("refused"):
            return f"{head} — switched on, but its word was refused as too broad ({info['refused'][0]['word']})"
        if info.get("enabled"):
            return f"{head} — {int(info.get('matched') or 0):,} plan line(s), {int(info.get('plans') or 0):,} name(s)"
        prop = (sug.get("proposal") or {}).get("include") or []
        pv = sug.get("preview") or {}
        if prop:
            return (f"{head} — {int(pv.get('lines') or 0):,} line(s) carry a plan word "
                    f"({', '.join(prop[:3])}), not switched on yet")
        return f"{head} — {int(info.get('rows') or 0):,} line(s), none carries a plan word"
    n = int(info.get("rows") or 0)
    return f"{head} — {n:,} row(s)" if n else f"{head} — empty"


def empty_sentence(sources):
    """Why the list is empty, naming every source ACTUALLY checked and its count, and the next step."""
    sources = list(sources or [])
    if not sources:
        sources = [{**s, "rows": 0} for s in HOUSE_SOURCES]
    n = len(sources)
    reason = (f"Checked {n} place(s) MetricsPro can take plan names from — "
              + "; ".join(source_sentence(s) for s in sources) + ".")
    proposable = [s for s in sources if s.get("kind") == "lines" and not s.get("enabled")
                  and ((s.get("suggest") or {}).get("proposal") or {}).get("include")]
    if proposable:
        nxt = ("Tick " + " or ".join(str(s.get("label")) for s in proposable) + " below, confirm the "
               "words that mark a plan line (and the ones that mean it is a rebate or bonus, not a "
               "plan), save, and the plans appear here to bring over.")
    else:
        nxt = ("Upload a carrier subscriber / MI report, land a sales export or commission statement "
               "that names your plans, or price your plans under Payout Schedules → Plan MRC — any one "
               "fills this list. Until then, use the CSV template below.")
    return {"empty_reason": reason, "empty_next": nxt}
