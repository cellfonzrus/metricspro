"""Franchise royalty report, cost centers, profit centers — the HTTP surface (owner 2026-09-25, mig 1022, index §37).

Every endpoint is gated by the `royalty` module (entitlements.require_module — a module whose vertical scope excludes the
tenant is never enabled, mig 1020) and every query is org-scoped (`.eq("org_id", org_id)` on each read / write; the
lock in backend/harness_royalty_lock.py fails the build on an unscoped chain in this file). The logic is PURE in
account/royalty.py and account/centers.py; this file reads, calls, writes.
"""
from datetime import date
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.core.entitlements import require_module
from app.modules.account import coa, royalty as R, centers as C, _period

router = APIRouter(prefix="/account", tags=["Account — Franchise royalty & centers"],
                   dependencies=[Depends(require_module("royalty"))])
ORG_ID = "00000000-0000-0000-0000-000000000001"
_MIG = R.MIGRATION


def sb():
    return get_supabase()


def _need_org(org_id):
    if not org_id:
        raise HTTPException(400, "org_id required")


def _pre_migration(e):
    return HTTPException(409, f"{_MIG} is not applied yet — the royalty / center tables do not exist ({str(e)[:120]})")


def _pl_lines():
    return [{"key": k, "label": lbl, "section": sec} for k, lbl, sec, *_ in coa.PL_SPEC]


def _ctx(client, org_id):
    vkey = R.tenant_vertical_key(client, org_id)
    vocab, ready = R.load_vocab(client, org_id, vkey)
    return vocab, ready, R.load_config(client, org_id)


def _store_index(client, org_id):
    centers = C.load_centers(client, org_id)
    idx, dupes = C.store_map_index(C.load_store_map(client, org_id), coa.store_resolver(client, org_id))
    return centers, idx, dupes


# ── the line vocabulary + config ──────────────────────────────────────────────────────────────────────
@router.get("/royalty/config")
def royalty_config(org_id: str = ORG_ID):
    _need_org(org_id)
    client = sb()
    vocab, ready, cfg = _ctx(client, org_id)
    return {"registry_ready": ready, "migration": _MIG, "config": cfg, "lines": vocab,
            "problems": R.vocab_problems(vocab), "pl_lines": _pl_lines(),
            "sections": list(R.SECTIONS), "daily_sources": list(R.DAILY_SOURCES), "match_fields": list(R.MATCH_FIELDS)}


class LineIn(LaxModel):
    line_key: str = ""
    label: Optional[str] = None
    section: Optional[str] = None
    role: Optional[str] = None
    aliases: Optional[list] = None
    pl_line_key: Optional[str] = None
    pl_note: Optional[str] = None
    rate: Optional[float] = None
    absorbs_remainder: Optional[bool] = None
    daily_categories: Optional[list] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


@router.put("/royalty/lines")
def royalty_line_upsert(body: LineIn, org_id: str = ORG_ID):
    """A tenant override of one vocabulary row (or a new line): the house row's fields + what was sent. The override
    is the tenant's own row, so the house default is never edited from a tenant."""
    _need_org(org_id)
    client = sb()
    vocab, _ready, _cfg = _ctx(client, org_id)
    cur = next((r for r in vocab if r["line_key"] == body.line_key), None)
    sent = body.model_fields_set
    base = dict(cur or {"line_key": R.slug(body.line_key or body.label or ""), "label": body.label or body.line_key,
                        "section": body.section or "sales", "role": "line", "aliases": [], "pl_line_key": None,
                        "pl_note": None, "rate": None, "absorbs_remainder": False, "daily_categories": [], "sort_order": 100})
    for k in ("label", "section", "role", "aliases", "pl_line_key", "pl_note", "rate", "absorbs_remainder",
              "daily_categories", "sort_order", "is_active"):
        if k in sent:
            base[k] = getattr(body, k)
    if base.get("pl_line_key") and base["pl_line_key"] not in {k for k, *_ in coa.PL_SPEC}:
        raise HTTPException(400, f"'{base['pl_line_key']}' is not a P&L line of the chart")
    if base.get("section") not in R.SECTIONS or (base.get("role") or "line") not in R.ROLES:
        raise HTTPException(400, "section / role outside the vocabulary")
    row = {"org_id": org_id, "line_key": base["line_key"], "label": base["label"], "section": base["section"],
           "role": base.get("role") or "line", "aliases": list(base.get("aliases") or []),
           "pl_line_key": base.get("pl_line_key") or None, "pl_note": base.get("pl_note") or None,
           "rate": base.get("rate"), "absorbs_remainder": bool(base.get("absorbs_remainder")),
           "daily_categories": list(base.get("daily_categories") or []), "sort_order": int(base.get("sort_order") or 100),
           "is_active": base.get("is_active") is not False}
    try:
        client.schema("commcalc").table("royalty_line_def").upsert(row, on_conflict="org_id,line_key").execute()
    except Exception as e:
        raise _pre_migration(e)
    vocab2, _r, _c = _ctx(client, org_id)
    return {"ok": True, "line": row, "problems": R.vocab_problems(vocab2)}


class ConfigIn(LaxModel):
    fee_basis: Optional[str] = None
    tolerance: Optional[float] = None
    daily_source: Optional[str] = None
    daily_match_field: Optional[str] = None
    book_pl: Optional[bool] = None
    center_pattern: Optional[str] = None
    period_pattern: Optional[str] = None
    lookback_months: Optional[int] = None


LOOKBACK_MIGRATION = "1027_royalty_lookback_months.sql"


@router.put("/royalty/config")
def royalty_config_save(body: ConfigIn, org_id: str = ORG_ID):
    _need_org(org_id)
    row = {"org_id": org_id, **{k: getattr(body, k) for k in body.model_fields_set}}
    resolved = R.resolve_config(row)
    for k in ("fee_basis", "daily_source", "daily_match_field", "lookback_months"):
        if k in row and row[k] is not None and row[k] != resolved[k]:
            raise HTTPException(400, f"{k} '{row[k]}' is not one of the allowed values"
                                + (" (%d–%d months)" % R.LOOKBACK_BOUNDS if k == "lookback_months" else ""))
    # The lookback column arrives with its own migration (1027): it is written only when it CHANGES, so the settings
    # form (which sends the whole resolved config back) keeps saving every other knob before that migration is applied.
    lookback = row.pop("lookback_months", None)
    client = sb()
    try:
        client.schema("commcalc").table("royalty_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise _pre_migration(e)
    if lookback is not None and lookback != R.load_config(client, org_id)["lookback_months"]:
        try:
            client.schema("commcalc").table("royalty_config") \
                .upsert({"org_id": org_id, "lookback_months": lookback}, on_conflict="org_id").execute()
        except Exception as e:
            raise HTTPException(409, f"{LOOKBACK_MIGRATION} is not applied yet — the lookback setting cannot be saved "
                                     f"(the other settings were saved) ({str(e)[:120]})")
    return {"ok": True, "config": R.resolve_config({**row, "lookback_months": lookback})}


# ── parse / import / manual entry ────────────────────────────────────────────────────────────────────
def _text_of(data: bytes, filename: str):
    """(text, source) from an uploaded PDF / HTML / text file. PDF text via pdfplumber (requirements.txt)."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        try:
            import io
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages), "pdf"
        except Exception as e:
            raise HTTPException(400, f"could not read the PDF ({type(e).__name__}) — save the page as HTML or paste its text")
    text = data.decode("utf-8", errors="replace")
    return text, ("html" if R.looks_like_html(text) else "text")


def _parse_input(client, org_id, file: Optional[UploadFile], text: str, ctx=None):
    vocab, _ready, cfg = ctx or _ctx(client, org_id)
    if file is not None:
        data = file.file.read()
        raw, source = _text_of(data, file.filename or "")
        fname = file.filename
    elif (text or "").strip():
        raw, source, fname = text, ("html" if R.looks_like_html(text) else "text"), None
    else:
        raise HTTPException(400, "send the report as a file (PDF / saved HTML / text) or paste its text")
    parsed = R.parse(raw, vocab, cfg)
    if not parsed["lines"]:
        raise HTTPException(400, "no report lines were found — is this the royalty report page? (no section header "
                                 "or line label from the line vocabulary matched)")
    return parsed, R.validate(parsed, vocab, cfg), vocab, cfg, source, fname


def _public_parsed(parsed):
    return {"center": parsed.get("center"), "period_label": parsed.get("period_label"),
            "lines": R.line_rows(parsed), "unknown": parsed.get("unknown"), "sections_seen": parsed.get("sections_seen"),
            "derived_totals": parsed.get("derived_totals") or []}


@router.post("/royalty/parse")
def royalty_parse(org_id: str = ORG_ID, file: Optional[UploadFile] = File(None), text: str = Form("")):
    """Preview: the lines read and every check the report's figures fail — NOTHING is written."""
    _need_org(org_id)
    parsed, val, vocab, _cfg, source, fname = _parse_input(sb(), org_id, file, text)
    b, cov = R.pl_bookings([{"store_ref": None, "lines": R.line_rows(parsed)}], vocab, {k for k, *_ in coa.PL_SPEC})
    return {"parsed": _public_parsed(parsed), "validation": val, "source": source, "file_name": fname,
            "pl_coverage": cov}


def _report_key(parsed, center, period):
    """THE ONE center × month resolution of a report (an entered value wins, else the report's own header): (center,
    canonical period, problem). The writer refuses on the problem; the multi-month plan reads the same answer, so a
    batch can never file a report under a month the single import would not."""
    center = (center or parsed.get("center") or "").strip()
    period_c = _period.canonical_period(period or parsed.get("period_label") or "")
    m, y = _period.parse_period(period_c)
    if not center:
        return center, period_c, "the center could not be read from the report — enter it"
    if not (1 <= m <= 12 and y):
        return center, period_c, "the royalty period could not be read from the report — enter it (e.g. June 2026)"
    return center, period_c, None


def _write_report(client, org_id, parsed, val, source, fname, center, period, store_ref):
    center, period_c, problem = _report_key(parsed, center, period)
    if problem:
        raise HTTPException(400, problem)
    store_note = None
    if not (store_ref or "").strip():
        centers, idx, _d = _store_index(client, org_id)
        store_ref, store_note = C.store_for_center(center, centers, idx)
    head = {"org_id": org_id, "center_code": center, "store_ref": (store_ref or None), "period": period_c,
            "source": source, "file_name": fname, **R.header_fields(parsed, val)}
    try:
        client.schema("commcalc").table("royalty_report").delete() \
            .eq("org_id", org_id).eq("center_code", center).in_("period", _period.period_keys(period_c)).execute()
        ins = client.schema("commcalc").table("royalty_report").insert(head).execute()
        rid = (ins.data or [{}])[0].get("id")
        rows = [{**ln, "org_id": org_id, "report_id": rid} for ln in R.line_rows(parsed)]
        if rows:
            client.schema("commcalc").table("royalty_report_line").insert(rows).execute()
    except HTTPException:
        raise
    except Exception as e:
        raise _pre_migration(e)
    return {"id": rid, "center_code": center, "period": period_c, "store_ref": store_ref or None,
            "store_note": store_note, "status": val["status"], "flags": val["flags"]}


@router.post("/royalty/import")
def royalty_import(org_id: str = ORG_ID, file: Optional[UploadFile] = File(None), text: str = Form(""),
                   center: str = Form(""), period: str = Form(""), store_ref: str = Form("")):
    """Parse → validate → store the REPORT's figures (one report per center × month; a re-import replaces it). A
    flagged report is stored WITH its flags — the numbers are the franchisor's, the flags are ours."""
    _need_org(org_id)
    client = sb()
    parsed, val, _vocab, _cfg, source, fname = _parse_input(client, org_id, file, text)
    out = _write_report(client, org_id, parsed, val, source, fname, center, period, store_ref)
    return {**out, "validation": val, "parsed": _public_parsed(parsed)}


class ManualIn(LaxModel):
    center_code: str = ""
    period: str = ""
    store_ref: Optional[str] = None
    entries: list = []


@router.post("/royalty/manual")
def royalty_manual(body: ManualIn, org_id: str = ORG_ID):
    """The manual-entry form: the same validation and the same writer as an import. A blank total is DERIVED from
    the lines and recorded as derived (so it is never mistaken for a printed figure)."""
    _need_org(org_id)
    client = sb()
    vocab, _ready, cfg = _ctx(client, org_id)
    parsed = R.fill_totals(R.from_manual(body.entries, vocab), vocab)
    if not parsed["lines"]:
        raise HTTPException(400, "enter at least one line")
    val = R.validate(parsed, vocab, cfg)
    out = _write_report(client, org_id, parsed, val, "manual", None, body.center_code, body.period, body.store_ref)
    return {**out, "validation": val, "derived_totals": parsed.get("derived_totals")}


# ── many months in one go (owner 2026-09-26, index §37.10) — the batch is N single imports ─────────────
def _this_period():
    """This month, canonical — the end of the lookback window (a module function so the proof can pin 'today')."""
    t = date.today()
    return _period.canonical_period(f"{t.year:04d}-{t.month:02d}")


def _window(cfg):
    months = cfg["lookback_months"]
    return R.lookback_window(_this_period(), months), months


def _on_file(client, org_id, window):
    """The reports this org already holds in the window (org-scoped, through the module's one reader)."""
    keys = sorted({k for p in window for k in _period.period_keys(p)})
    reps = R.load_reports(client, org_id, keys, with_lines=False)
    idx = {}
    for r in reps:
        idx[(r.get("center_code"), _period.canonical_period(r.get("period") or ""))] = {
            "id": r.get("id"), "total_due": r.get("total_due"), "status": r.get("status")}
    return reps, idx


def _batch_read(client, org_id, files):
    """Each file through the SINGLE import's own reader (`_parse_input` → R.parse → R.validate) and the writer's own
    key resolution (`_report_key`). A file that cannot be read is an item with its error — it never stops the others."""
    ctx = _ctx(client, org_id)
    items, prepared = [], {}
    for i, f in enumerate(files or []):
        base = {"index": i, "file_name": getattr(f, "filename", None) or f"file {i + 1}"}
        try:
            parsed, val, _vocab, _cfg, source, fname = _parse_input(client, org_id, f, "", ctx)
        except HTTPException as e:
            items.append({**base, "error": str(e.detail), "center": None, "period": None})
            continue
        except Exception as e:                      # a crash on one file is that file's error, never the batch's
            items.append({**base, "error": f"could not read this file ({type(e).__name__})", "center": None, "period": None})
            continue
        center, period_c, _problem = _report_key(parsed, "", "")
        m, y = _period.parse_period(period_c)
        rep = val.get("reported") or {}
        items.append({**base, "error": None, "center": center or None, "period": period_c if (1 <= m <= 12 and y) else None,
                      "period_label": parsed.get("period_label"), "source": source, "lines": len(parsed.get("lines") or []),
                      "status": val["status"], "flags": val["flags"], "total_due": rep.get("total_due"),
                      "gross_sales": rep.get("gross_sales")})
        prepared[i] = (parsed, val, source, fname)
    return items, prepared, ctx[2]


def _batch_plan(client, org_id, files):
    items, prepared, cfg = _batch_read(client, org_id, files)
    window, months = _window(cfg)
    reps, idx = _on_file(client, org_id, window)
    return R.batch_plan(items, window, idx), prepared, window, months, reps


def _coverage_payload(window, months, reps):
    return {"lookback_months": months, **R.coverage(window, reps)}


@router.get("/royalty/coverage")
def royalty_coverage(org_id: str = ORG_ID):
    """The lookback window (this month and `lookback_months` before it — per-org config, house default 24) and which
    months hold a report, per center."""
    _need_org(org_id)
    client = sb()
    window, months = _window(R.load_config(client, org_id))
    reps, _idx = _on_file(client, org_id, window)
    return _coverage_payload(window, months, reps)


@router.post("/royalty/batch/preview")
def royalty_batch_preview(org_id: str = ORG_ID, files: List[UploadFile] = File(...)):
    """Many reports at once — the PREVIEW: per file the center and month read off its own header, the checks, whether a
    report for that center × month is already on file (it will be replaced), and every reason it cannot land.
    NOTHING is written."""
    _need_org(org_id)
    client = sb()
    plan, _prepared, window, months, reps = _batch_plan(client, org_id, files)
    return {"files": plan, "ready": sum(1 for r in plan if r["ready"]), "coverage": _coverage_payload(window, months, reps)}


@router.post("/royalty/batch/import")
def royalty_batch_import(org_id: str = ORG_ID, files: List[UploadFile] = File(...)):
    """Many reports at once — IMPORT the files sent (the page sends only the ones ticked on the preview). The plan is
    re-run on exactly these files; each READY file lands through the single import's own writer (`_write_report`, which
    replaces that center × month), one at a time, in upload order. A refused or failing file is reported in
    `results` and never stops, rolls back or hides another."""
    _need_org(org_id)
    client = sb()
    plan, prepared, window, months, _reps = _batch_plan(client, org_id, files)
    results = []
    for r in plan:
        base = {"index": r["index"], "file_name": r["file_name"], "center_code": r.get("center"), "period": r.get("period")}
        if not r["ready"]:
            results.append({**base, "ok": False, "error": "; ".join(r["refusals"])})
            continue
        parsed, val, source, fname = prepared[r["index"]]
        try:
            out = _write_report(client, org_id, parsed, val, source, fname, "", "", "")
        except HTTPException as e:
            results.append({**base, "ok": False, "error": str(e.detail)})
            continue
        except Exception as e:
            results.append({**base, "ok": False, "error": f"could not be saved ({type(e).__name__}: {str(e)[:120]})"})
            continue
        results.append({**base, "ok": True, "id": out["id"], "replaced": r["replace"], "status": out["status"],
                        "flags": len(out["flags"]), "store_ref": out["store_ref"], "store_note": out["store_note"],
                        "total_due": r.get("total_due")})
    reps_after, _i = _on_file(client, org_id, window)
    return {"results": results, "imported": sum(1 for x in results if x["ok"]),
            "failed": sum(1 for x in results if not x["ok"]), "sentence": R.batch_outcome(results),
            "coverage": _coverage_payload(window, months, reps_after)}


# ── reading reports ──────────────────────────────────────────────────────────────────────────────────
@router.get("/royalty/reports")
def royalty_reports(org_id: str = ORG_ID, period: str = ""):
    _need_org(org_id)
    reps = R.load_reports(sb(), org_id, _period.period_keys(period) if period else None, with_lines=False)
    reps.sort(key=lambda r: (_period.parse_period(r.get("period") or "")[1], _period.parse_period(r.get("period") or "")[0],
                             r.get("center_code") or ""), reverse=True)
    return {"reports": reps}


@router.get("/royalty/report/{report_id}")
def royalty_report(report_id: str, org_id: str = ORG_ID):
    _need_org(org_id)
    client = sb()
    try:
        rows = (client.schema("commcalc").table("royalty_report").select("*")
                .eq("org_id", org_id).eq("id", report_id).limit(1).execute().data) or []
    except Exception as e:
        raise _pre_migration(e)
    if not rows:
        raise HTTPException(404, "no such report for this company")
    rep = rows[0]
    rep["lines"] = (client.schema("commcalc").table("royalty_report_line").select("*")
                    .eq("org_id", org_id).eq("report_id", report_id).execute().data) or []
    vocab, _ready, _cfg = _ctx(client, org_id)
    _b, cov = R.pl_bookings([rep], vocab, {k for k, *_ in coa.PL_SPEC})
    return {"report": rep, "pl_coverage": cov, "pl_lines": _pl_lines()}


@router.delete("/royalty/report/{report_id}")
def royalty_report_delete(report_id: str, org_id: str = ORG_ID):
    _need_org(org_id)
    try:
        sb().schema("commcalc").table("royalty_report").delete().eq("org_id", org_id).eq("id", report_id).execute()
    except Exception as e:
        raise _pre_migration(e)
    return {"ok": True}


# ── the reconciliation ───────────────────────────────────────────────────────────────────────────────
def _daily_rows(client, org_id, cfg, period):
    """The month's daily POS rows from the configured EXISTING landing (raw_sales_product by default, or raw_sales),
    voids excluded — read, never re-ingested."""
    table = cfg["daily_source"]
    cols = "trans_date,store,category,department,product_desc,ext_price,voided"
    try:
        rows = coa._fetch_all(client, table, cols, {"org_id": org_id, "period": _period.period_keys(period)})
    except Exception:
        return None
    out = []
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
            continue
        out.append({"date": str(r.get("trans_date") or "")[:10], "store": r.get("store"), "category": r.get("category"),
                    "department": r.get("department"), "product_desc": r.get("product_desc"), "amount": r.get("ext_price")})
    return out


def _tender_rows(client, org_id, period):
    m, y = _period.parse_period(period)
    if not (m and y):
        return []
    start = f"{y:04d}-{m:02d}-01"
    end = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
    try:
        return (client.schema("commcalc").table("pos_tender_summary").select("close_date,store,amount")
                .eq("org_id", org_id).gte("close_date", start).lt("close_date", end).execute().data) or []
    except Exception:
        return []


def _recon_for(client, org_id, rep, vocab, cfg, daily, tenders, resolve):
    note = None
    store = resolve(rep["store_ref"]) if rep.get("store_ref") else None
    stores_seen = {resolve(r.get("store")) for r in daily or [] if r.get("store")}
    if store:
        mine = [r for r in daily if resolve(r.get("store")) == store]
        tmine = [t for t in tenders if resolve(t.get("store")) == store]
    elif len(stores_seen) == 1:
        mine, tmine = list(daily), list(tenders)
        note = "the report's center has no store mapped; the daily report carries one store only, so it is used"
    else:
        mine, tmine = [], []
        note = ("the report's center has no store mapped (Profit Centers → map the store) — the daily side cannot be "
                "chosen among %d stores" % len(stores_seen)) if stores_seen else None
    res = R.reconcile(rep.get("lines") or [], vocab, mine, cfg["daily_match_field"], cfg["tolerance"])
    res["tenders"] = R.tender_crosscheck(rep.get("total_gross_sales") or 0, tmine)
    res["note"] = note
    res["store"] = store
    return res


@router.get("/royalty/recon/{period}")
def royalty_recon(period: str, org_id: str = ORG_ID, center: str = ""):
    _need_org(org_id)
    client = sb()
    vocab, _ready, cfg = _ctx(client, org_id)
    reps = R.load_reports(client, org_id, _period.period_keys(period))
    if center:
        reps = [r for r in reps if (r.get("center_code") or "") == center]
    daily = _daily_rows(client, org_id, cfg, period)
    tenders = _tender_rows(client, org_id, period)
    resolve = coa.store_resolver(client, org_id)
    out = []
    for rep in reps:
        out.append({"report_id": rep["id"], "center_code": rep.get("center_code"), "period": rep.get("period"),
                    "store_ref": rep.get("store_ref"),
                    **_recon_for(client, org_id, rep, vocab, cfg, daily or [], tenders, resolve)})
    return {"period": _period.canonical_period(period), "daily_source": cfg["daily_source"],
            "daily_available": daily is not None, "daily_rows": len(daily or []), "tender_rows": len(tenders),
            "match_field": cfg["daily_match_field"], "centers": out,
            "needs": None if daily else ("No daily report rows for this month in the configured daily source "
                                         f"({cfg['daily_source']}). Upload the daily report through Upload Files / the "
                                         "onboarding intake; its category column is what each royalty line is summed from.")}


@router.get("/royalty/summary")
def royalty_summary(org_id: str = ORG_ID):
    """The operations-dashboard tiles: the latest period's STR, fees due, recon variance, unmapped lines."""
    _need_org(org_id)
    client = sb()
    reps = R.load_reports(client, org_id, None, with_lines=False)
    if not reps:
        return R.summary([])
    s0 = R.summary(reps)
    period = s0["period"]
    vocab, _ready, cfg = _ctx(client, org_id)
    latest = R.load_reports(client, org_id, _period.period_keys(period))
    daily = _daily_rows(client, org_id, cfg, period) or []
    tenders = _tender_rows(client, org_id, period)
    resolve = coa.store_resolver(client, org_id)
    recon = {r["id"]: _recon_for(client, org_id, r, vocab, cfg, daily, tenders, resolve) for r in latest} if daily else {}
    _b, cov = R.pl_bookings(latest, vocab, {k for k, *_ in coa.PL_SPEC})
    return R.summary(reps, recon, cov)


# ── cost centers + profit centers ─────────────────────────────────────────────────────────────────────
@router.get("/centers")
def centers_get(org_id: str = ORG_ID):
    _need_org(org_id)
    client = sb()
    centers, idx, dupes = _store_index(client, org_id)
    stores = sorted(set(coa.store_code_to_address(client, org_id).values()))
    return {"centers": centers, "store_map": [{"store": s, "profit_center_code": c} for s, c in sorted(idx.items())],
            "store_map_conflicts": dupes, "line_tags": C.load_line_tags(client, org_id),
            "problems": {t: C.tree_problems(C.centers_of(centers, t)) for t in C.CENTER_TYPES},
            "pl_lines": _pl_lines(), "stores": stores}


class CenterIn(LaxModel):
    center_type: str = "cost"
    code: str = ""
    name: str = ""
    parent_code: Optional[str] = None
    external_ref: Optional[str] = None
    is_active: bool = True
    notes: Optional[str] = None


@router.put("/centers")
def centers_upsert(body: CenterIn, org_id: str = ORG_ID):
    _need_org(org_id)
    if body.center_type not in C.CENTER_TYPES:
        raise HTTPException(400, "center_type is cost or profit")
    if not C.valid_code(body.code):
        raise HTTPException(400, "a code is 1–40 letters, digits, spaces or . _ / -")
    row = {"org_id": org_id, "center_type": body.center_type, "code": body.code.strip(),
           "name": (body.name or body.code).strip(), "parent_code": (body.parent_code or "").strip() or None,
           "external_ref": (body.external_ref or "").strip() or None, "is_active": bool(body.is_active),
           "notes": (body.notes or "").strip() or None}
    client = sb()
    try:
        client.schema("commcalc").table("finance_center").upsert(row, on_conflict="org_id,center_type,code").execute()
    except Exception as e:
        raise _pre_migration(e)
    cs = C.centers_of(C.load_centers(client, org_id), body.center_type)
    return {"ok": True, "center": row, "problems": C.tree_problems(cs)}


@router.delete("/centers/{center_type}/{code}")
def centers_delete(center_type: str, code: str, org_id: str = ORG_ID):
    _need_org(org_id)
    try:
        sb().schema("commcalc").table("finance_center").delete() \
            .eq("org_id", org_id).eq("center_type", center_type).eq("code", code).execute()
    except Exception as e:
        raise _pre_migration(e)
    return {"ok": True}


class StoreMapIn(LaxModel):
    store_ref: str = ""
    profit_center_code: Optional[str] = None


@router.put("/centers/store-map")
def centers_store_map(body: StoreMapIn, org_id: str = ORG_ID):
    _need_org(org_id)
    if not body.store_ref.strip():
        raise HTTPException(400, "store_ref required")
    q = sb().schema("commcalc").table("profit_center_store")
    try:
        if not (body.profit_center_code or "").strip():
            q.delete().eq("org_id", org_id).eq("store_ref", body.store_ref.strip()).execute()
        else:
            q.upsert({"org_id": org_id, "store_ref": body.store_ref.strip(),
                      "profit_center_code": body.profit_center_code.strip()}, on_conflict="org_id,store_ref").execute()
    except Exception as e:
        raise _pre_migration(e)
    return {"ok": True}


class LineTagIn(LaxModel):
    pl_line_key: str = ""
    detail_label: Optional[str] = None
    cost_center_code: Optional[str] = None


@router.put("/centers/line-tag")
def centers_line_tag(body: LineTagIn, org_id: str = ORG_ID):
    _need_org(org_id)
    if body.pl_line_key not in {k for k, *_ in coa.PL_SPEC}:
        raise HTTPException(400, "not a P&L line of the chart")
    dl = (body.detail_label or "").strip()
    q = sb().schema("commcalc").table("pl_line_cost_center")
    try:
        if not (body.cost_center_code or "").strip():
            q.delete().eq("org_id", org_id).eq("pl_line_key", body.pl_line_key).eq("detail_label", dl).execute()
        else:
            q.upsert({"org_id": org_id, "pl_line_key": body.pl_line_key, "detail_label": dl,
                      "cost_center_code": body.cost_center_code.strip()}, on_conflict="org_id,pl_line_key,detail_label").execute()
    except Exception as e:
        raise _pre_migration(e)
    return {"ok": True}


@router.get("/centers/pl/{period}")
def centers_profit_pl(period: str, org_id: str = ORG_ID, profit_center: str = ""):
    """A profit center's P&L — the statement engine's own `profit_center:<code>` scope (fresh, on demand)."""
    _need_org(org_id)
    from app.modules.account import statement_engine
    if not profit_center:
        raise HTTPException(400, "profit_center required")
    return statement_engine.statement(sb(), org_id, period, scope=C.SCOPE_PREFIX + profit_center, kinds=("pl",))


@router.get("/centers/cost-view/{period}")
def centers_cost_view(period: str, org_id: str = ORG_ID, scope: str = "consolidated"):
    """The assembled P&L (any scope the engine knows) regrouped by cost center — ties to the statement to the cent."""
    _need_org(org_id)
    from app.modules.account import statement_engine
    client = sb()
    st = statement_engine.statement(client, org_id, period, scope=scope, kinds=("pl",))
    if not st.get("computed"):
        return {"computed": False, "note": st.get("note")}
    cs = C.load_centers(client, org_id)
    view = C.cost_center_view(st["pl"], C.tag_index(C.load_line_tags(client, org_id)), cs)
    return {"computed": True, "period": st["period"], "scope": st["scope"], "scope_label": st.get("scope_label"), **view}
