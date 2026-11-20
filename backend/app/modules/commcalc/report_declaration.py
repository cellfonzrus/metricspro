"""WHAT A TENANT HAS DECLARED ABOUT ITS PORTAL REPORTS, AS ONE EDITABLE THING (index §COMMCALC-48).

WHY THIS MODULE EXISTS. A pulled report is declared, never coded (§COMMCALC-07 / -37 / -39). The
declaration lives in TWO columns of `commcalc.data_source`, because each answers a different question
and each already has readers:

  • `portal_reports`     — WHICH reports: `[{key, label, nav[], export, line_map}]` (`pos_report_sweep.report_specs`)
  • `portal_calibration` — HOW to run one: `{<key>: {params: [{selector, kind, value, label}], …}}` (`portal_report_pull.report_params`)

Nothing could WRITE them. There is no screen, so a tenant's reports could only be declared by someone
with SQL access — which makes "config, not code" true on paper and false in practice, and is exactly
what the owner objected to on 2026-10-29: *"start definig the reports fromt eh back end again not
hardpcded but consumer defined editable once you build them"*.

An editor needs the declaration as ONE list of rows, one row per report, with its filters attached.
That is a THIRD shape, and a third shape is where this platform has been bitten before — so it is a
shape this module DERIVES and never stores:

    two columns  --rows(src_row)-->  editable rows  --columns(rows)-->  two columns

`columns(rows(src_row))` is the identity on anything the pull can read, which is the property that
makes an editor safe: a tenant who opens the editor and saves without touching anything has changed
nothing. The lock pins that round trip, because the alternative — an editor that quietly rewrites a
working declaration into its own dialect — is worse than no editor.

WHAT IT REUSES rather than rebuilds:
  • `pos_report_sweep.spec_key` — THE rule for "which report is this" (§COMMCALC-39). A second rule is
    the defect that made fourteen declared reports resolve to none.
  • `portal_report_pull.PARAM_KINDS` / `DATE_TOKENS` — the declared-filter vocabulary (§COMMCALC-37).
    A kind this platform cannot apply must not be offerable in an editor.
  • `report_columns.norm` (§COMMCALC-38) for comparing words.

It names no portal, no tenant and no report (RULE TWO): the fourteen reports one tenant wants are rows
in their database, and this module could not tell you one of them.

Proof: `backend/harness_report_declaration.py` — DB-free and browser-free.
"""
from app.modules.commcalc import pos_report_sweep as _sweep
from app.modules.commcalc import portal_report_pull as _prp
from app.modules.commcalc import pos_report_feed as _feed
from app.modules.commcalc import report_columns as _rc

# The fields an editor row carries. Everything here is either stored or DERIVED from what is stored —
# `per_day` is derived (§COMMCALC-37: a report whose filters mention {date} IS a per-day report, and a
# flag beside that could disagree with it), and `problems` is advice, never stored.
ROW_FIELDS = ("key", "label", "nav", "export", "params", "line_map",
              "report_url", "run_selector", "export_selector",
              "column_bindings", "unit_basis", "amount_sign", "pull")
# THE CONTROLS THE PULL PRESSES, and the address it may go to instead of clicking menu words
# (owner, 2026-11-04: "How to get the filters right"). `portal_report_pull` reads FOUR facts per
# report off `portal_calibration` — `params`, `run_selector`, `export_selector` and one of
# `nav_selector` / `report_url` — and until now this editor could write exactly ONE of them. So a
# capture that correctly read a portal's Run and Export buttons had nowhere to put them
# (`suggest_params` returns both; `merge_suggestion` read only `params`), and the pull fell back to
# hunting for a button whose words happen to be "Export" / "Download" / "CSV". On a portal that
# spells its button anything else, that finds nothing and imports zero rows while the declaration
# looks complete. Measured on the owner's own fourteen UPS Store reports, 2026-11-04: every one of
# them `params 0 | run None | export None | address None`.
#
# `nav_selector` stays out on purpose: nothing suggests it and no human would hand-write a framework's
# generated selector. It is still folded through untouched, so a capture that sets it keeps working.
CONTROL_FIELDS = ("report_url", "run_selector", "export_selector")
EXPORTS = ("csv", "excel", "xlsx", "pdf", "print")
MAX_ROWS = 60                 # a portal with more declared reports than this is a mistake, not a tenant
MAX_PARAMS = 24               # per report

# DEREFERENCED, not copied: the kinds of filter this platform can actually apply and the tokens that
# mean "the day this run covers" are §COMMCALC-37's vocabulary, and an editor that offered a kind the
# pull cannot apply would be offering a filter that is silently ignored.
PARAM_KINDS = _prp.PARAM_KINDS
DATE_TOKENS = _prp.DATE_TOKENS

# WHETHER THIS REPORT IS PULLED AT ALL (owner ask 2026-11-20: "pull the required reports"). The name
# and the meaning of an absent value are `portal_report_pull`'s, dereferenced — the pull loop is the
# reader, and an editor that invented its own spelling or its own default would be declaring a
# different switch from the one that decides anything.
PULL_FIELD = _prp.PULL_FIELD

# HOW TO READ THIS REPORT once it has been downloaded (owner 2026-11-17). Three facts about a
# tenant's own export that nothing could declare, so no export could answer a declared category:
# which COLUMN is which fact when two could be (`column_bindings`), whether a line states its own
# quantity or IS one (`unit_basis`), and which SIGN is a sale (`amount_sign`). Every one of them is
# DEREFERENCED from the home that owns it — this editor adds no vocabulary of its own, and the
# defaults are what every existing tenant already reads by, so a report nobody has declared these on
# is parsed byte-identically to before.
READING_FIELDS = ("column_bindings", "unit_basis", "amount_sign")
UNIT_BASES = _feed.UNIT_BASES
UNIT_BASIS_LABELS = dict(_feed.UNIT_BASIS_LABELS)
AMOUNT_SIGNS = _feed.AMOUNT_SIGNS
AMOUNT_SIGN_LABELS = dict(_feed.AMOUNT_SIGN_LABELS)
#: The fields a binding may name — the AXES `pos_report_feed` reads, never a category.
BINDABLE_FIELDS = tuple(_feed.COLUMN_SYNONYMS)


def _clean_bindings(value):
    """{field: the export's own column header} for the axes this feed reads. A binding naming a
    field the feed does not read is dropped — it could never be honoured and storing it would look
    like a declaration that works. PURE."""
    out = {}
    for field, header in (dict(value) if isinstance(value, dict) else {}).items():
        f, h = str(field or "").strip(), str(header or "").strip()
        if f in BINDABLE_FIELDS and h:
            out[f] = h[:120]
    return out


def _clean_choice(value, allowed):
    v = str(value or "").strip().lower()
    return v if v in allowed else ""


def _clean_words(v):
    """A nav hint list from whatever the editor sent: a list, or one string split on commas / arrows.

    Typing "Reporting > POS > Item Sales" is what a human does when asked for the menu words that reach
    a report, so it is accepted and split rather than stored as one unusable hint."""
    if isinstance(v, str):
        parts = [p for chunk in v.split(">") for p in chunk.split(",")]
    elif isinstance(v, (list, tuple)):
        parts = [str(p) for p in v]
    else:
        return ()
    return tuple(p.strip() for p in parts if str(p).strip())


def clean_params(raw):
    """The declared filters of ONE report, normalized to what `portal_report_pull.report_params` reads.

    A step whose kind this platform cannot apply, or that names no control, is DROPPED — the same rule
    the reader applies, so the editor cannot save a filter the pull would silently ignore. `value` is
    kept verbatim, date tokens included: substituting one here would freeze a per-day report onto the
    day it was edited."""
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        sel = str(item.get("selector") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not sel or kind not in _prp.PARAM_KINDS:
            continue
        val = item.get("value")
        out.append({"selector": sel, "kind": kind,
                    "value": None if val is None else str(val),
                    "label": str(item.get("label") or sel)[:80]})
        if len(out) >= MAX_PARAMS:
            break
    return out


def _clean_control(v):
    """One control the pull presses, from whatever the editor sent: a trimmed string, or "" .

    A selector is typed by a human from what a page shows, so it is taken as written and never
    normalised — a CSS selector is case-sensitive and a portal's generated id depends on it."""
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    return str(v).strip()[:300]


def row_problems(row):
    """What a human must fix about one row, in their words. Advice — never stored, never a refusal.

    A row with problems is still saved: a declaration captured halfway is worth keeping, and
    §COMMCALC-37 already refuses to EXPORT a report whose declared filters could not be set, so a
    half-declared report cannot quietly produce figures."""
    out = []
    if not row.get("key"):
        out.append("give this report a name")
    if not row.get("pull", True):
        # EVERY problem below is about reaching and running the page. None of them is a problem for a
        # report nobody pulls — and one such report is not a mistake: a tenant may map a report
        # precisely so a file they export by hand can be read, which needs the reading fields and no
        # address, no filters and no export button at all.
        return out + ["not pulled — only files you upload by hand are read through this report"]
    if not (row.get("nav") or row.get("report_url")):
        # EITHER answer reaches the page: `navigate_to_report` tries the declared address before the
        # menu words, and on a portal whose reports all live on ONE page the address is the only one
        # of the two that means anything.
        out.append("say which menu words reach it on the portal, or paste the report page's address")
    params = row.get("params") or []
    if not params:
        out.append("no filters are declared, so it will export whatever the page happens to show")
    else:
        tokens = " ".join(str(p.get("value") or "") for p in params)
        if not any(t in tokens for t in _prp.DATE_TOKENS):
            out.append("no date filter mentions a day, so every run exports the same page")
    # THE CONTROLS, in the operator's words. Said as advice and not a refusal for the same reason the
    # rest of this is: a half-declared report is still worth keeping. But an undeclared Export control
    # is the one omission that reads as working and is not — the pull guesses at the words "Export",
    # "Download" and "CSV", and a portal that spells its button differently imports nothing.
    if not row.get("export_selector"):
        out.append("no export control is declared, so the pull has to guess which button downloads "
                   "the file")
    return out


def row(item, calibration=None):
    """ONE editor row from one declared report plus its calibration entry. PURE.

    Accepts both declared shapes `pos_report_sweep.report_specs` accepts — the object and the bare
    string — because a tenant who declared bare keys must be able to open the editor too.
    """
    cal = dict(calibration or {})
    if isinstance(item, str):
        key = _sweep.spec_key(item)
        label, nav, export, line_map = item.strip(), (), "csv", {}
    elif isinstance(item, dict):
        key = _sweep.spec_key(item)
        label = str(item.get("label") or key)
        nav = _clean_words(item.get("nav") or item.get("nav_hints"))
        export = str(item.get("export") or "csv").strip().lower() or "csv"
        line_map = item.get("line_map") if isinstance(item.get("line_map"), dict) else {}
    else:
        return None
    if not key:
        return None
    if not nav:
        # The same fallback the pull derives, shown rather than hidden: a tenant looking at the editor
        # should see the hint their declaration actually gives the navigation.
        nav = tuple(w for w in key.split("_") if len(w) > 2)
    out = {"key": key, "label": label, "nav": list(nav),
           # Absence is YES, read through the pull's own reader so the tick box can never show one
           # answer while the loop acts on another.
           "pull": _prp.report_requested({"portal_reports": [item]}, key),
           "export": export if export in EXPORTS else "csv",
           "params": clean_params(cal.get("params")),
           "line_map": dict(line_map),
           "column_bindings": _clean_bindings(cal.get("column_bindings")),
           "unit_basis": _clean_choice(cal.get("unit_basis"), UNIT_BASES),
           "amount_sign": _clean_choice(cal.get("amount_sign"), AMOUNT_SIGNS)}
    # DEREFERENCED from the one declaration of which controls the pull presses, so a field added
    # there reaches the editor without a second list here.
    for field in CONTROL_FIELDS:
        out[field] = _clean_control(cal.get(field))
    out["per_day"] = any("{date}" in str(p.get("value") or "") for p in out["params"])
    out["problems"] = row_problems(out)
    return out


def rows(src_row):
    """EVERY declared report of one source as editor rows, in declared order. PURE.

    Deduped by key, first one wins — two rows claiming one key is the ambiguity §COMMCALC-39 exists to
    stop, and the editor must not present it as two independent reports.
    """
    src = src_row or {}
    declared = src.get("portal_reports") or []
    if isinstance(declared, dict):
        declared = [declared]
    cal = src.get("portal_calibration") if isinstance(src.get("portal_calibration"), dict) else {}
    out, seen = [], set()
    for item in declared:
        r = row(item, (cal or {}).get(_sweep.spec_key(item)))
        if not r or r["key"] in seen:
            continue
        seen.add(r["key"])
        out.append(r)
        if len(out) >= MAX_ROWS:
            break
    return out


def columns(editor_rows, existing_calibration=None):
    """The two stored columns from editor rows. PURE. The inverse of `rows`.

    `existing_calibration` is folded through UNTOUCHED for every key it carries beyond `params`, and
    for keys the editor did not show. A calibration entry may hold things this editor does not edit
    (the login declaration of §COMMCALC-46, a capture's notes), and an editor that dropped what it did
    not understand would delete a tenant's working configuration on a save that looked like a no-op.
    """
    keep = dict(existing_calibration or {}) if isinstance(existing_calibration, dict) else {}
    reports, cal, seen = [], {}, set()
    for item in (editor_rows or []):
        if not isinstance(item, dict):
            continue
        key = _sweep.spec_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        nav = _clean_words(item.get("nav"))
        export = str(item.get("export") or "csv").strip().lower()
        spec = {"key": key, "label": str(item.get("label") or key)[:120],
                "nav": list(nav), "export": export if export in EXPORTS else "csv"}
        # WRITTEN ONLY WHEN SWITCHED OFF. Absence already means "pull it", so storing `pull: true`
        # on every report would rewrite every declaration written before this field existed — and
        # `columns(rows(src))` being the identity is the property that makes this editor safe to
        # open. Ticked on is the absence of a declaration, not a declaration of yes.
        if not item.get(PULL_FIELD, True):
            spec[PULL_FIELD] = False
        lm = item.get("line_map")
        if isinstance(lm, dict) and lm:
            spec["line_map"] = lm
        reports.append(spec)
        entry = dict(keep.get(key) or {})
        entry["params"] = clean_params(item.get("params"))
        # Cleared on the screen means cleared in the declaration — the key is DROPPED rather than
        # stored empty, so the parser reads "nothing declared" exactly as it did before this editor
        # could write it, and the house default applies instead of an empty string.
        for field, cleaner in (("column_bindings", _clean_bindings),
                               ("unit_basis", lambda v: _clean_choice(v, UNIT_BASES)),
                               ("amount_sign", lambda v: _clean_choice(v, AMOUNT_SIGNS))):
            val = cleaner(item.get(field))
            if val:
                entry[field] = val
            else:
                entry.pop(field, None)
        for field in CONTROL_FIELDS:
            val = _clean_control(item.get(field))
            # A field CLEARED on the screen is cleared in the declaration — the key is dropped rather
            # than stored empty, so `calibrated()` reads "nothing declared" exactly as it did before
            # this editor could write it.
            if val:
                entry[field] = val
            else:
                entry.pop(field, None)
        cal[key] = entry
        if len(reports) >= MAX_ROWS:
            break
    for key, entry in keep.items():
        if key not in cal:
            cal[key] = entry
    return reports, cal


def merge_suggestion(editor_rows, key, suggestion):
    """Fold a capture's suggestion (§COMMCALC-39 `capture_report_calibration`) into one row. PURE.

    The capture READS a page and suggests; it never decides. So a selector a human has already edited
    is kept, and only a filter the row does not have yet is added — a capture that overwrote the
    declaration a tenant had corrected would make the editor untrustworthy on exactly the pages that
    needed correcting. Returns new rows; nothing is mutated in place.

    THE CONTROLS FOLD THE SAME WAY (owner, 2026-11-04). `suggest_params` has always returned
    `run_selector` and `export_selector` under their OWN keys — deliberately, because the loop presses
    them at a fixed point in its order and a param pressing Run early would export the previous run's
    grid — and this function read only `params`, so both were discarded on every capture since
    §COMMCALC-48 shipped. A control the row ALREADY declares is kept, for the same reason a corrected
    selector is: the human who typed it was looking at the page.
    """
    want = _sweep.spec_key(key)
    sent = suggestion or {}
    sug = clean_params(sent.get("params"))
    out = []
    for item in (editor_rows or []):
        r = dict(item or {})
        if _sweep.spec_key(r) != want:
            out.append(r)
            continue
        have = clean_params(r.get("params"))
        by_sel = {p["selector"] for p in have}
        by_label = {_rc.norm(p["label"]) for p in have}
        for p in sug:
            if p["selector"] in by_sel or _rc.norm(p["label"]) in by_label:
                continue
            have.append(p)
        r["params"] = have
        for field in CONTROL_FIELDS:
            if _clean_control(r.get(field)):
                continue                      # a human's answer stands
            got = _clean_control(sent.get(field))
            if got:
                r[field] = got
        out.append(r)
    return out
