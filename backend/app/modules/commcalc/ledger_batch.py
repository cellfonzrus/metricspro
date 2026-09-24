"""MANY MONTHLY STATEMENTS IN ONE GO — the batch plan for the Commission Ledger import (owner 2026-09-24;
index §30.17). PURE: no I/O, no client, no carrier / tenant / product named (RULE TWO).

Owner, verbatim: *"give me an option to upload commisison for multiple periods at the same time since
tehy only give monthly commision reports , on teh upload commssion received page"*

The carrier issues ONE statement per month. The owner picks several monthly files at once (e.g. 12) on
the Commission Ledger page and lands them in one go, each under its OWN month. This module is only the
PLAN — which month each file is, whether that month is already landed, and what refuses the batch. It
lands nothing, parses nothing and maps nothing:

  · the file is read, mapped, footer-dropped and classified by the router's single-file path
    (`_ledger_prepare_file` — factored out of `/commission-ledger/import`, which now calls it too);
  · it is landed by `_ledger_import_prepared` → `_ledger_land_rows`, THE one lander (index §30.15);
  · the month a file IS comes from the statement's OWN dates through the intake's detector
    (`onboarding_intake.period_proposal` over the mapping's period field — the same proposal the 3.9
    card pre-fills) and is spelled by THE one canonicaliser (`commission_ledger.canonical_period` →
    `account/_period.canonical_period`); a month the person types is spelled the same way;
  · "already landed" is `commission_ledger.landings_for` over the statement's FAMILY rows — the very
    measure the lander takes before it replaces (every stored key × every period spelling × origin).

`harness_ledger_batch_lock.py` fails the build if this module stops dereferencing those, or grows a
month parser, a month-name table, a ledger insert or a period filter of its own.
"""
from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import onboarding_intake as OI
from app.modules.commcalc import ledger_ma_sync as _lms
from app.modules.account import _period as _pd

ORIGIN_FILE = _lms.ORIGIN_FILE          # the lander's origin for a file — dereferenced, never spelled
YEAR_MIN, YEAR_MAX = 2000, 2100         # a four-digit year: "Aug 26" is not a month the ledger can store

SOURCE_SET = "set"                      # the person typed / picked the month on the preview
SOURCE_DETECTED = "detected"            # read off the statement's own dates


def _s(v):
    return str(v or "").strip()


def _money(x):
    try:
        return round(float(x or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


# ── THE MONTH ────────────────────────────────────────────────────────────────────────────────────
def resolve_period(text):
    """The canonical month a person's text names ('aug 2026' / '2026-08' / 'August 2026' → 'August 2026'),
    or None when it does not name a month of a four-digit year. Spelled by THE canonicaliser
    (`commission_ledger.canonical_period`); `_period.parse_period` only answers "is it a month". PURE."""
    raw = _s(text)
    if not raw:
        return None
    m, y = _pd.parse_period(raw)
    if not (1 <= m <= 12 and YEAR_MIN <= y <= YEAR_MAX):
        return None
    return CL.canonical_period(raw)


def detect_period(mapped_rows, date_field="trans_date"):
    """The month a statement IS, from its OWN dates — the intake's detector
    (`onboarding_intake.period_proposal`, the proposal the 3.9 card pre-fills), spelled canonically.
    `proposed` is None when no line carries a date, or when the two leading months hold the SAME number
    of lines (a tie is a question, never a guess). `spans` says the dates cover more than one month —
    the proposal is the month with the most lines, and the preview says so. PURE."""
    prop = OI.period_proposal(mapped_rows or [], date_field)
    months = [{"period": CL.canonical_period(m["period"]), "rows": int(m["rows"])} for m in prop.get("months") or []]
    tied = len(months) > 1 and months[0]["rows"] == months[1]["rows"]
    proposed = None
    if prop.get("proposed") and not tied:
        proposed = resolve_period(prop["proposed"])
    return {"proposed": proposed, "months": months, "spans": bool(prop.get("spans_two_months")),
            "tied": tied, "dated_rows": int(prop.get("dated_rows") or 0),
            "span_from": prop.get("span_from"), "span_to": prop.get("span_to"), "date_field": date_field}


# ── THE COLUMNS — the saved mapping (per statement type) applies to every file ─────────────────────
def mapping_shape(headers, hdr_rules):
    """Which of the mapping's fields this file's headers carry. `present` = the target fields whose
    source header is in the file (case-insensitive, exactly as `column_mapping.apply_mapping` matches);
    `missing` = [{target_field, header}] the mapping reads and this file does not have. PURE."""
    have = {_s(h).lower() for h in headers or [] if _s(h)}
    present, missing = [], []
    for r in hdr_rules or []:
        tf, src = _s(r.get("target_field")), _s(r.get("source_header"))
        if not tf:
            continue
        if src and src.lower() in have:
            present.append(tf)
        else:
            missing.append({"target_field": tf, "header": src})
    return {"present": sorted(set(present)), "missing": missing}


def reference_shape(shapes):
    """The shape most files share (ties → the earliest file's) — the one the saved mapping reads the
    batch by; a file whose shape differs is named, never silently mapped short. PURE."""
    counts, first = {}, {}
    for i, sh in enumerate(shapes):
        if sh is None:
            continue
        k = tuple(sh["present"])
        counts[k] = counts.get(k, 0) + 1
        first.setdefault(k, i)
    if not counts:
        return None
    return list(max(counts, key=lambda k: (counts[k], -first[k])))


# ── THE PLAN ─────────────────────────────────────────────────────────────────────────────────────
def plan_batch(files, overrides=None, family_rows=None, required_fields=(), origin=ORIGIN_FILE):
    """The per-file preview and the batch verdict. PURE.

    `files` — one dict per uploaded file, in upload order: {filename, error (a read/map refusal, or
    None), rows (usable lines), payout_total, net_total, footer_rows, detection (detect_period), shape
    (mapping_shape)}. `overrides` — a list aligned with `files`: the month the person set for that file
    ('' = none). `family_rows` — the statement's ledger rows (every stored key × every period spelling),
    what `commission_ledger.landings_for` measures. `required_fields` — the mapping's required fields
    (a file whose headers carry none of one maps to nothing).

    A file is BLOCKED (the batch lands nothing until it is fixed or removed) when: it could not be read
    or mapped; its month cannot be determined and none was set; the month set is not a month; another
    file resolves to the SAME month; or its headers differ from the batch's (named) — the saved mapping
    would read it short. An already-landed month is not blocked: it is REPLACED by the existing rule
    (the lander wipes the statement's family × every spelling × this origin) and needs `confirm_replace`;
    a month that also holds a landing from ANOTHER origin needs `confirm_other_origin` (landing makes
    two landings, which every ledger reader refuses to add until one is retired)."""
    overrides = list(overrides or [])
    out = []
    ref = reference_shape([None if f.get("error") else f.get("shape") for f in files])
    for i, f in enumerate(files):
        fn = _s(f.get("filename")) or f"file {i + 1}"
        det = f.get("detection") or {}
        ov = _s(overrides[i]) if i < len(overrides) else ""
        blocking, warnings = [], []
        period, source = None, None
        if f.get("error"):
            blocking.append(_s(f["error"]))
        if ov:
            period = resolve_period(ov)
            source = SOURCE_SET
            if not period:
                blocking.append(f"'{ov}' is not a month — type it like 'August 2026'")
        elif det.get("proposed"):
            period, source = det["proposed"], SOURCE_DETECTED
            if det.get("spans"):
                mix = ", ".join(f"{m['period']} ({m['rows']:,})" for m in det.get("months") or [])
                warnings.append(f"the dates span {mix} — {period} has the most lines; change it if the statement is for another month")
        elif not f.get("error"):
            if det.get("tied"):
                mix = " and ".join(f"{m['period']} ({m['rows']:,})" for m in (det.get("months") or [])[:2])
                blocking.append(f"the dates split evenly between {mix} — set the month this statement is for")
            else:
                blocking.append("the month could not be read from the statement's own dates"
                                + (f" ({det.get('date_field')} is blank on every line)" if det.get("date_field") else "")
                                + " — set the month this statement is for")
        shape = f.get("shape") or {}
        differs = []
        if not f.get("error") and ref is not None and shape:
            differs = sorted(set(ref) ^ set(shape.get("present") or []))
            if differs:
                lacking = [m for m in shape.get("missing") or [] if m["target_field"] in set(ref)]
                named = ", ".join(f"'{m['header']}' ({m['target_field']})" for m in lacking) or ", ".join(differs)
                blocking.append(f"its columns differ from the other files: {named} — the saved mapping would read it "
                                "short; map this layout on the setup wizard, then upload it")
        req_missing = [m for m in shape.get("missing") or [] if m["target_field"] in set(required_fields or ())]
        if req_missing and not f.get("error"):
            blocking.append("it has no " + ", ".join(f"'{m['header']}' ({m['target_field']})" for m in req_missing)
                            + " column, which every line needs")
        if int(f.get("footer_rows") or 0):
            warnings.append(f"{int(f['footer_rows'])} total/footer row(s) left out, as a single import does")
        out.append({"index": i, "filename": fn, "rows": int(f.get("rows") or 0),
                    "payout_total": _money(f.get("payout_total")), "net_total": _money(f.get("net_total")),
                    "period": period, "period_source": source, "detected": det.get("proposed"),
                    "detection": det, "override": ov or None, "headers_differ": differs,
                    "blocking": blocking, "warnings": warnings, "already": None,
                    "replaces": [], "other_origin": []})
    # TWO FILES, ONE MONTH — never double-land, never let the later file silently replace the earlier
    by_period = {}
    for p in out:
        if p["period"]:
            by_period.setdefault(p["period"], []).append(p)
    for per, ps in by_period.items():
        if len(ps) > 1:
            for p in ps:
                others = ", ".join(q["filename"] for q in ps if q is not p)
                p["blocking"].append(f"{per} is also the month of {others} — one statement per month; fix the month or remove a file")
    # ALREADY LANDED — the lander's own measure (landings_for over the family), per resolved month
    for p in out:
        if not p["period"]:
            continue
        groups = CL.landings_for(family_rows or [], p["period"])
        landings = [L for g in groups for L in g["landings"]]
        if landings:
            p["already"] = {"rows": sum(int(L["rows"]) for L in landings),
                            "payout_total": _money(sum(L["payout_total"] for L in landings)),
                            "landings": landings}
            p["replaces"] = [L for L in landings if (L.get("origin") or ORIGIN_FILE) == origin]
            p["other_origin"] = [L for L in landings if (L.get("origin") or ORIGIN_FILE) != origin]
    for p in out:
        p["action"] = "refused" if p["blocking"] else ("replace" if p["replaces"] else "land")
    blocked = [p for p in out if p["blocking"]]
    return {"files": out, "refused": bool(blocked) or not out,
            "refusals": [f"{p['filename']}: " + "; ".join(p["blocking"]) for p in blocked]
            + ([] if out else ["no files were uploaded"]),
            "replace_periods": [p["period"] for p in out if p["replaces"] and not p["blocking"]],
            "other_origin_periods": [p["period"] for p in out if p["other_origin"] and not p["blocking"]],
            "to_land": [p["index"] for p in out if not p["blocking"]]}


def commit_refusals(plan, confirm_replace=False, confirm_other_origin=False):
    """Why the batch may NOT land (nothing is written when this is non-empty). PURE."""
    out = list(plan.get("refusals") or [])
    if out:
        return out
    if plan.get("replace_periods") and not confirm_replace:
        out.append(f"{len(plan['replace_periods'])} month(s) are already landed and would be REPLACED ("
                   + ", ".join(plan["replace_periods"]) + ") — confirm the replacement to proceed")
    if plan.get("other_origin_periods") and not confirm_other_origin:
        out.append("month(s) " + ", ".join(plan["other_origin_periods"]) + " also hold a landing from another source "
                   "(the MA data refresh); a file landing beside it makes two landings, which every ledger reader refuses to "
                   "add until one is retired — confirm to proceed")
    return out


def result_sentence(results):
    """The per-file outcome, in words: which files landed (month, rows), which did not and why, and that
    nothing landed was rolled back. PURE."""
    landed = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    s = f"Landed {len(landed)} of {len(results)} file(s)"
    if landed:
        s += ": " + "; ".join(f"{r['filename']} → {r['period']} ({int(r.get('saved') or 0):,} lines)" for r in landed)
    if failed:
        s += ". NOT landed: " + "; ".join(f"{r['filename']} — {r.get('error')}" for r in failed)
        s += ". The files that landed stay landed — a failure on one file rolls back no other"
    return s + "."
