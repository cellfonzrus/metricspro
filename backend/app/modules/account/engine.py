"""Account engine — assembles P&L + Balance Sheet from the deterministic chart-of-accounts
inputs, scopes them consolidated / per-company / per-store, lets Claude narrate + analyze, and
persists each as a snapshot in commcalc.account_statements.

The NUMBERS are always the deterministic `coa.build_inputs` totals (exact, reproducible). Claude
produces the narrative + analysis notes over those figures (and the #10 missed-days reasoning in
recon.py); it never originates a dollar amount that ships. If ANTHROPIC_API_KEY is unset the engine
degrades to a deterministic summary.
"""
import json
from datetime import datetime, timezone

from app.core.config import settings
from app.modules.commcalc.calculator import safe_float
from app.modules.account import coa
from app.modules.account.ai_limits import ACCOUNT_AI_TIMEOUT_S, ACCOUNT_AI_MAX_RETRIES

ORG_ID = coa.ORG_ID


def _round(x):
    return round(safe_float(x), 2)


# ── scope resolution ────────────────────────────────────────────────────────────────────────
def _scoped(line, stores_in_scope, include_company_wide):
    """Return (amount, detail-dict) for a line under a scope.
    `stores_in_scope` = None for consolidated (all stores)."""
    by_store = line["by_store"]
    if stores_in_scope is None:
        amt = sum(by_store.values())
        detail = dict(line["detail"])
    else:
        amt = sum(v for s, v in by_store.items() if s in stores_in_scope)
        detail = {}  # detail is company-wide; only meaningful consolidated
    if include_company_wide:
        amt += line["company_wide"]
    return _round(amt), {k: _round(v) for k, v in detail.items() if v}


def _assemble(inputs, journal_rows, spec, label_map, sections_def, scope, stores_in_scope,
              include_company_wide, prior_accum_ni=0.0, current_ni=None):
    """Build one statement (pl or bs) deterministically for a scope."""
    statement = "pl" if sections_def[0][1] in ("revenue",) else "balance_sheet"
    # base lines from the spec
    lines_by_section = {sec_type: [] for _, sec_type in sections_def}
    for key, label, section, kind, _grain in spec:
        if section not in lines_by_section:
            continue
        if kind in ("manual", "computed"):
            amt, detail = 0.0, {}
        else:
            amt, detail = _scoped(inputs[key], stores_in_scope, include_company_wide)
        # per-line display-label override: coa may rename a line from data (e.g. the payroll line
        # becomes "Gross Payroll" once the exact-gross system line is present). Absent → spec label,
        # so a tenant without the override is byte-identical.
        disp = (inputs.get(key) or {}).get("label") or label
        # "auto_opt" lines (e.g. Payroll Expenses) materialize ONLY when they carry a value, so a
        # tenant that never pushes them shows no empty line — byte-identical to before the line existed.
        if kind == "auto_opt" and not amt and not detail:
            continue
        row = {"key": key, "label": disp, "amount": amt, "kind": kind, "detail": detail}
        # A line may carry a NOTE explaining a figure the number alone cannot explain — specifically a
        # DECLARED zero. Ruling K3(b) requires that a period with no distributor invoice report device
        # COGS as an honest zero "with reason"; without this passthrough that zero rendered exactly
        # like a measured zero, because `detail` drops zero-valued entries and nothing else survives
        # `_assemble`. Only emitted when `coa` actually set one, so every other line and every other
        # tenant keeps a byte-identical payload.
        note = (inputs.get(key) or {}).get("note")
        if note:
            row["note"] = note
        lines_by_section[section].append(row)

    # fold in manual journal entries (match by label to a spec line, else append)
    for je in journal_rows:
        if (je.get("statement") or "") != statement:
            continue
        sec = (je.get("account_type") or "").strip()
        if sec not in lines_by_section:
            continue
        amt = _round(je.get("amount"))
        label = (je.get("account_line") or "Manual entry").strip()
        match = next((ln for ln in lines_by_section[sec] if ln["label"] == label), None)
        if match:
            match["amount"] = _round(match["amount"] + amt)
        else:
            lines_by_section[sec].append(
                {"key": "je_" + label.lower().replace(" ", "_")[:24], "label": label,
                 "amount": amt, "kind": "manual", "detail": {}})

    sections, sec_total = [], {}
    for name, sec_type in sections_def:
        lns = lines_by_section[sec_type]
        sub = _round(sum(l["amount"] for l in lns))
        sec_total[sec_type] = sub
        sections.append({"name": name, "type": sec_type, "lines": lns, "subtotal": sub})

    out = {"statement_type": statement, "sections": sections}
    if statement == "pl":
        rev, cogs = sec_total.get("revenue", 0), sec_total.get("cogs", 0)
        opex, other = sec_total.get("opex", 0), sec_total.get("other", 0)
        out["gross_profit"] = _round(rev - cogs)
        out["net_operating_income"] = _round(out["gross_profit"] - opex)
        out["net_income"] = _round(out["net_operating_income"] - other)
    else:
        # retained earnings (computed) = accumulated prior net income + this period's net income
        retained = _round(prior_accum_ni + (current_ni or 0))
        for sec in sections:
            if sec["type"] == "equity":
                for ln in sec["lines"]:
                    if ln["key"] == "retained":
                        ln["amount"] = _round(ln["amount"] + retained)
                sec["subtotal"] = _round(sum(l["amount"] for l in sec["lines"]))
                sec_total["equity"] = sec["subtotal"]
        a, l, e = sec_total.get("asset", 0), sec_total.get("liability", 0), sec_total.get("equity", 0)
        out["assets_total"], out["liabilities_total"], out["equity_total"] = a, l, e
        out["imbalance"] = _round(a - (l + e))
        out["balanced"] = abs(out["imbalance"]) < 1.0
    return out


# ── prior-period accumulation for retained earnings ──────────────────────────────────────────
def _prior_accum_ni(client, org_id, scope_key, period):
    pm, py = coa.parse_period(period)
    cur = py * 100 + pm
    total = 0.0
    try:
        rows = (client.schema("commcalc").table("account_statements")
                .select("period,payload").eq("org_id", org_id)
                .eq("statement_type", "pl").eq("scope_key", scope_key).execute().data) or []
        for r in rows:
            m, y = coa.parse_period(r.get("period") or "")
            if y * 100 + m < cur:
                total += safe_float((r.get("payload") or {}).get("net_income"))
    except Exception:
        pass
    return _round(total)


# ── Claude narrative / analysis (numbers stay deterministic) ─────────────────────────────────
def _narrate(pl, bs, scope_label, period):
    """Return (narrative_text, model_id). Degrades to a deterministic summary if no API key."""
    if not settings.ANTHROPIC_API_KEY:
        gp = pl.get("gross_profit", 0)
        ni = pl.get("net_income", 0)
        bal = "balances" if bs.get("balanced") else f"is out of balance by {bs.get('imbalance')}"
        return (f"{scope_label} — {period}. Gross profit {gp:,.2f}; net income {ni:,.2f}. "
                f"Balance sheet {bal}. (Set ANTHROPIC_API_KEY for the full narrative.)",
                "deterministic")
    try:
        # SEV-1 2026-07-30 (event-loop safety). This is the SYNCHRONOUS Anthropic client, which is
        # correct ONLY because compute_and_store() runs in a WORKER THREAD: both async callers
        # (/account/compute/{period} and /account/run-due) hop via run_in_threadpool. Two rules, do
        # not break either:
        #   1. NEVER call compute_and_store() straight from an `async def`. The sync HTTP call would
        #      then run ON the single uvicorn event loop and stall EVERY endpoint (including /health)
        #      — exactly what /helpdesk/ai-assist did to the whole backend on 2026-07-30.
        #   2. Keep the explicit timeout + max_retries. The SDK defaults to 600s x 2 retries (~30
        #      min); uncapped, one stalled narrative pins a worker thread and hangs this request for
        #      half an hour. Env-tunable (ACCOUNT_AI_TIMEOUT_S / ACCOUNT_AI_MAX_RETRIES) so the
        #      operator can widen it for slow extended-thinking responses with no deploy.
        # A timeout lands in the `except` below -> "(Narrative unavailable: APITimeoutError.)". The
        # narrative is commentary only; every P&L / Balance-Sheet FIGURE is deterministic and
        # unaffected either way.
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY,
                           timeout=ACCOUNT_AI_TIMEOUT_S, max_retries=ACCOUNT_AI_MAX_RETRIES)
        prompt = (
            "You are the financial analyst for a multi-store cellular retailer (cash-basis books). "
            "Below are a finalized, deterministically-computed Profit & Loss and Balance Sheet for one "
            "scope and period. The numbers are authoritative — do NOT recompute or change any figure. "
            "Write a concise (120-200 word) plain-English narrative for the owner: what the P&L shows "
            "(gross margin, biggest revenue and cost drivers), the net result, what the balance sheet "
            "says, and — if it is out of balance — exactly what to enter (cash/opening balances via the "
            "journal) to make Assets = Liabilities + Equity. Flag any line that looks unusually high, "
            "zero, or likely needs a manual journal entry (e.g. wages, cash, owner capital). Return ONLY "
            "the narrative text, no preamble.\n\n"
            f"SCOPE: {scope_label}  PERIOD: {period}\n\n"
            f"P&L:\n{json.dumps(pl, indent=2)}\n\nBALANCE SHEET:\n{json.dumps(bs, indent=2)}")
        msg = client.messages.create(
            model=settings.ACCOUNT_ENGINE_MODEL,
            max_tokens=1200,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        return (text or "(no narrative)", settings.ACCOUNT_ENGINE_MODEL)
    except Exception as e:
        return (f"(Narrative unavailable: {type(e).__name__}.)", "deterministic")


# ── public: compute + persist all scopes for a period ────────────────────────────────────────
def compute_and_store(client, org_id, period):
    """Build inputs once, assemble + persist consolidated, per-company, and per-store snapshots.
    Claude narrates the CONSOLIDATED statements (one call) to bound cost; other scopes get a
    deterministic note. Returns a summary dict."""
    inputs = coa.build_inputs(client, org_id, period)
    journal = (client.schema("commcalc").table("journal_entries").select("*")
               .eq("org_id", org_id).eq("period", period).execute().data) or []
    # THE shared store→company attribution (owner bug 2026-09-02 "companies … proper information is
    # not being displayed"): exact match first (byte-identical where it used to match), then squashed
    # spelling, then unambiguous leading street number, else the default company — so a sales-feed
    # spelling drift ('1115 Liberty Ave Brooklyn, NY 11208' vs the assignment's '1115 Liberty Ave')
    # no longer leaks a store's whole month into 'Default Company'. Same matcher statement_filter
    # uses for the company-scope × store/market filter composition (coa.company_assignment).
    company_of, default_co, companies = coa.company_assignment(client, org_id)
    co_name = {c["id"]: c["name"] for c in companies}

    # every store that appears in any line
    all_stores = set()
    for ln in inputs.values():
        all_stores.update(ln["by_store"].keys())

    scopes = [("consolidated", "Consolidated (all companies)", None, True)]
    for c in companies:
        cstores = {s for s in all_stores if company_of(s) == c["id"]}
        scopes.append((f"company:{c['id']}", c["name"], cstores, False))
    for s in sorted(all_stores):
        scopes.append((f"store:{s}", s, {s}, False))

    # Purge ALL prior snapshots for this period BEFORE writing the fresh set. Otherwise scopes
    # that no longer exist (e.g. a store spelling the resolver now merges into another) linger as
    # orphan rows and keep re-appearing as duplicates in the /overview scope dropdown. _persist
    # below then inserts the current scopes cleanly.
    client.schema("commcalc").table("account_statements").delete() \
        .eq("org_id", org_id).eq("period", period).execute()

    written = 0
    for scope_key, scope_label, stores_in_scope, include_cw in scopes:
        jscope = _journal_for_scope(journal, scope_key, stores_in_scope)
        # P&L first (net income feeds retained earnings on the BS)
        pl = _assemble(inputs, jscope, coa.PL_SPEC, coa.PL_LABEL,
                       [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
                        ("Operating Expenses", "opex"), ("Other", "other")],
                       scope_key, stores_in_scope, include_cw)
        prior = _prior_accum_ni(client, org_id, scope_key, period)
        bs = _assemble(inputs, jscope, coa.BS_SPEC, coa.BS_LABEL,
                       [("Assets", "asset"), ("Liabilities", "liability"), ("Equity", "equity")],
                       scope_key, stores_in_scope, include_cw,
                       prior_accum_ni=prior, current_ni=pl["net_income"])
        for stmt in (pl, bs):
            stmt["period"], stmt["scope_key"], stmt["scope_label"] = period, scope_key, scope_label
        pl["notes"] = _notes(scope_key, include_cw)
        bs["notes"] = _notes(scope_key, include_cw) + (
            [] if bs["balanced"] else ["Balance sheet is not yet balanced — enter cash / opening "
                                       "balances via the Journal so Assets = Liabilities + Equity."])

        if scope_key == "consolidated":
            narrative, model = _narrate(pl, bs, scope_label, period)
        else:
            narrative, model = "", "deterministic"

        for stmt, st_type in ((pl, "pl"), (bs, "balance_sheet")):
            _persist(client, org_id, period, st_type, scope_key, scope_label, stmt,
                     narrative if st_type == "pl" else "", model, stmt.get("balanced", True))
            written += 1

    return {"period": period, "snapshots": written, "scopes": len(scopes),
            "companies": len(companies), "stores": len(all_stores),
            "engine": "claude+deterministic" if settings.ANTHROPIC_API_KEY else "deterministic"}


def _journal_for_scope(journal, scope_key, stores_in_scope):
    """Manual entries apply to their own scope (+ consolidated). Unscoped entries (no company,
    no store) apply to the consolidated view only — they can't be attributed to a sub-scope."""
    if scope_key == "consolidated":
        return journal
    if scope_key.startswith("company:"):
        cid = scope_key.split(":", 1)[1]
        ss = stores_in_scope or set()
        return [j for j in journal if j.get("company_id") == cid or (j.get("store_address") in ss)]
    if scope_key.startswith("store:"):
        addr = scope_key.split(":", 1)[1]
        return [j for j in journal if j.get("store_address") == addr]
    return journal


def _notes(scope_key, include_cw):
    n = []
    if not include_cw:
        n.append("Company-wide income (MI/ATU residual, carrier incentives without a matching store) "
                 "is shown only in the Consolidated view.")
    n.append("Cash, fixtures and owner capital are MANUAL — enter them in the Journal. Wages = "
             "StoreOps payroll (shifts × pay rate); rep commissions, sales, VIP, reimbursements, "
             "store expenses and wages are automatic.")
    return n


def _persist(client, org_id, period, st_type, scope_key, scope_label, payload, narrative, model, ok):
    row = {"org_id": org_id, "period": period, "statement_type": st_type, "scope_key": scope_key,
           "scope_label": scope_label, "payload": payload, "narrative": narrative, "model": model,
           "crosscheck_ok": bool(ok), "computed_at": datetime.now(timezone.utc).isoformat()}
    # compute_and_store has already purged this period's snapshots, so a plain insert is clean.
    client.schema("commcalc").table("account_statements").insert(row).execute()
