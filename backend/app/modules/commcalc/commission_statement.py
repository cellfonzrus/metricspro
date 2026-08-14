"""INDIVIDUAL COMMISSION STATEMENT — the per-employee companion to the Payout Structure document.

`payout_structure.py` describes HOW commission is earned (the plan STRUCTURE: what pays, at what rate,
how often, what never pays). It closes by deferring exactly this document: _"Your own earnings for a
period are shown on your individual commission statement."_ This is that statement — what ONE employee
earned for ONE period, itemized, the page you hand a rep alongside the structure doc.

READ-ONLY BY CONSTRUCTION — the same rule the structure doc lives under, and the reason this module is a
NARRATOR, not a second calculator:

  • It COMPUTES NO PAY and WRITES NOTHING. Every dollar on the page is SOURCED from an existing
    single-point-of-truth and merely re-stated:
      - the plan component (what the assigned Commission Plan paid, per rule, with the matched sale
        lines) comes from `commission_drilldown.explain_rep`, whose plan half is
        `commission_engine.preview(detail=True, only_rep=…)` — the SAME resolution the live calc uses.
      - the multi-month residual/installment component (per device, per month, paid vs held + WHY)
        comes from that same drill-down, which reads the authoritative sale-installment ledger.
      - the headline total is `rep_commissions.total_payout` (the number the last Run Calculation
        actually paid), surfaced through the drill-down's `reconciliation` block.
      - the five canonical payout buckets (commission / spiff / equipment_rebate / residual_monthly /
        autopay_residual) come from `commission_ledger` via the caller.
    If a number was not already computed somewhere, this module narrates it — it never invents it.

  • It does not RE-DESCRIBE rates, conditions or frequencies in its own words either: the rate/condition/
    frequency sentences are produced by `payout_structure`'s pure helpers (`describe_rate`,
    `describe_condition`, `describe_frequency`), so a rule reads IDENTICALLY on the structure document and
    on the statement. The two pages are one system on purpose — same helpers, same palette, same footer
    tone — so an employee sees the plan and their own earnings described the same way.

PURITY: everything above `render_pdf` is pure (no DB client, no reportlab). The caller (the router) does
every org-scoped read and hands this module plain dicts, so the English/model is unit-testable against
fixtures — see backend/harness_commission_statement.py. `render_pdf` imports reportlab lazily, the same
way `payout_structure.render_pdf` does, so importing this module never costs the PDF stack.

MULTI-TENANT: the builder holds no org constant and reads no client. The caller passes the org-scoped
drill-down and bucket rows; every DB read happens in the router with `org_id`.

XSS/MARKUP SAFETY: reportlab's Paragraph parses mini-markup, so every employee/product/tenant string is
run through `esc` before it reaches a Paragraph (the same escaping `payout_structure` applies).
"""
from datetime import datetime, timezone

# Reuse the STRUCTURE document's pure narration + formatting so a rule reads the same on both pages and
# the two documents are a matched set (same money/pct formatting, same rate/condition/frequency English).
from app.modules.commcalc.payout_structure import (
    _s, _f, money, pct, display_label,
    describe_condition, describe_rate, describe_frequency,
)
from app.modules.commcalc import plan_pay_gate as _gate
from app.modules.commcalc.commission_ledger import CATEGORIES, CATEGORY_LABELS


# ── ACCESS GATE for the "Held / not yet paid" section (DATA_GRANT 'statement_held') ─────────────────
# Owner directive: the held itemization (matched-but-suppressed plan lines + held installments, each with
# its reason and would-have-paid $) is MANAGEMENT-ONLY and DEFAULT-CLOSED — off the PDF and the fmt=json
# model for everyone until granted. This is the PURE allower over a resolved caller dict (no I/O), the same
# shape as device_history.device_commission_allowed and the frontend hasDataGrant('statement_held'), so the
# router's `_can_view_statement_held` degrades CLOSED and this rule stays unit-testable in the harness.
STATEMENT_HELD_GRANT = "statement_held"


def statement_held_allowed(caller):
    """True iff the caller may see the statement's held section. PURE over a resolved caller dict:
      super_admin / perms.scope=='all' / role=='admin'                          -> allow
      'statement_held' in perms.modules, or perms.data.statement_held truthy    -> allow
      else (including an unresolvable/None caller)                              -> deny (default-closed)."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if STATEMENT_HELD_GRANT in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get(STATEMENT_HELD_GRANT)):
        return True
    return False


# ── plan component: the Commission-Plan rules that paid this rep this period ────────────────────────
def _plan_line_items(plan_component, ucfg):
    """(earned_rows, held_rows) from the plan component of a drill-down. PURE.

    earned = every rule with a positive payout, itemized with the SAME rate/condition/frequency English
    the structure document prints. held = matched lines the pay gate suppressed (scope / exclusion /
    unit-basis dedup), each carrying the drill-down's own suppression reason and the would-have-paid $ —
    never dropped, because a silently omitted line is how a $0 becomes unexplainable.
    """
    pc = plan_component or {}
    earned, held, footnotes = [], [], []
    for rule in pc.get("rules") or []:
        payout = _f(rule.get("payout"))
        cond = describe_condition(rule)
        what = display_label(rule.get("label")) or cond
        # Lines the gate matched but suppressed — surfaced as held, with the reason the engine attached.
        supp = [ln for ln in (rule.get("lines") or []) if ln.get("suppressed")]
        if supp:
            reason = _s(supp[0].get("suppressed_reason")) or "Not paid."
            would = round(sum(_f(ln.get("would_have_paid")) for ln in supp), 2)
            held.append({"what": what, "when": cond, "reason": reason,
                         "amount": money(would) if would else None, "units": len(supp)})
        if payout <= 0:
            continue
        rate, _zero = describe_rate(rule)
        freq, note = describe_frequency(rule, ucfg)
        if note and note not in footnotes:
            footnotes.append(note)
        units = int(_f(rule.get("qualifying_units") or rule.get("matched_lines")))
        earned.append({"what": what, "condition": cond, "rate": rate, "frequency": freq,
                       "units": units, "amount": money(payout), "source": "plan"})
    return earned, held, footnotes


# ── multi-month component: per-device residual / installment rows (paid vs held) ────────────────────
def _installment_items(multimonth_component):
    """(earned_rows, held_rows) from the multi-month component of a drill-down. PURE.

    A PAID installment is money the rep earned this period (a residual on an earlier sale), so it joins
    the earned table. A HELD installment is money that did NOT pay, with the drill-down's own hold reason
    (dealer not shown paid / line inactive / residual not received / no first-month payment) and the
    would-be amount so the rep can see what is waiting on what.
    """
    mm = multimonth_component or {}
    earned, held = [], []
    for dev in mm.get("devices") or []:
        prod = _s(dev.get("product") or dev.get("device_product") or dev.get("label"))
        for inst in dev.get("installments") or []:
            label = _s(inst.get("label")) or prod or "Multi-month residual"
            month = inst.get("month_index")
            when = (f"Month {int(_f(month))}" if month not in (None, "") else "Residual") + (
                f" — {prod}" if prod else "")
            paid = _s(inst.get("status")) == "paid"
            amt = _f(inst.get("amount"))
            if paid and amt != 0:
                earned.append({"what": label, "condition": when,
                               "rate": "Multi-month residual", "frequency": "Paid monthly",
                               "units": 1, "amount": money(amt), "source": "installment"})
            elif not paid:
                # withheld_amount is the would-be $ (None when unknown); expected_amount is the schedule's
                # figure. Prefer the concrete withheld number, fall back to expected.
                wa = inst.get("withheld_amount")
                shown = wa if wa not in (None, "") else inst.get("expected_amount")
                held.append({"what": label, "when": when,
                             "reason": _s(inst.get("hold_detail")) or "Held.",
                             "amount": money(shown) if shown not in (None, "") else None, "units": 1})
    return earned, held


# ── the five canonical payout buckets (commission_ledger rollup) ───────────────────────────────────
def _bucket_rows(buckets):
    """(rows, total, has_any) for the five canonical categories. PURE. `buckets` is a
    {category -> amount} map from the caller's commission_ledger rollup (any missing category is $0)."""
    rows, total, any_nonzero = [], 0.0, False
    for c in CATEGORIES:
        amt = _f((buckets or {}).get(c))
        total += amt
        if amt:
            any_nonzero = True
        rows.append({"key": c, "label": CATEGORY_LABELS.get(c, c), "amount": money(amt), "raw": round(amt, 2)})
    return rows, round(total, 2), any_nonzero


# ── the document ───────────────────────────────────────────────────────────────────────────────────
def build_statement(explain, buckets=None, tenant_name="", rep_name="", period="",
                    generated_at=None, gate_cfg=None, total_payout=None, include_held=False):
    """Turn one rep's drill-down + bucket rollup into the STATEMENT DOCUMENT MODEL. PURE — no I/O.

    `explain`      : commission_drilldown.explain_rep(...) output for this rep+period (the single source
                     of every dollar — plan_component, multimonth_component, reconciliation, zero_explanation).
    `buckets`      : {category -> amount} five-bucket rollup from commission_ledger for this rep, or None.
    `gate_cfg`     : plan_pay_gate.load_gate_config() output; None => code defaults (which ARE the owner's
                     rule) so an unconfigured tenant's frequency column is still correct.
    `total_payout` : optional explicit headline (the caller's rep_commissions.total_payout). When None the
                     drill-down's own reconciliation block supplies it, falling back to the components.
    `include_held` : whether to carry the "Held / not yet paid" section. DEFAULT-FALSE by owner directive —
                     the held itemization (matched-but-suppressed plan lines + held installments, each with
                     its reason) is OMITTED from BOTH the PDF and the fmt=json model unless the CALLER has
                     the gated permission and passes this True. When False the model's `held` is [] (the
                     renderer only draws the table when non-empty) and the intro never mentions held items.

    Every string the employee reads is decided HERE, where it can be tested, not in the renderer.
    """
    explain = explain or {}
    ucfg = ((gate_cfg or {}).get("unit_basis") or _gate.UNIT_DEFAULTS)
    _now = datetime.now(timezone.utc)
    when = generated_at or f"{_now.strftime('%B')} {_now.day}, {_now.year}"

    pc = explain.get("plan_component") or {}
    mm = explain.get("multimonth_component") or {}
    recon = explain.get("reconciliation") or {}

    plan_earned, plan_held, footnotes = _plan_line_items(pc, ucfg)
    inst_earned, inst_held = _installment_items(mm)
    earned = plan_earned + inst_earned
    # DEFAULT-OFF, GATED: the held section is dropped from the model entirely unless the caller was granted
    # permission (the router computes include_held from the DATA_GRANT). Everything below — is_empty, the
    # intro's held branch, the PDF section, the fmt=json array — then sees no held rows.
    held = (plan_held + inst_held) if include_held else []

    plan_subtotal = round(_f(pc.get("total_payout")), 2)
    inst_subtotal = round(_f((mm.get("totals") or {}).get("amount")), 2)

    # HEADLINE TOTAL — the paid number, sourced (never re-summed as if authoritative):
    #   1. an explicit caller value (rep_commissions.total_payout passed in), else
    #   2. the drill-down's reconciliation (also rep_commissions.total_payout), else
    #   3. the two components, clearly labelled as a component sum when no last-calc row exists.
    if total_payout is not None:
        total = round(_f(total_payout), 2)
        total_source = "rep_commissions (last Run Calculation)"
    elif recon.get("total_payout") is not None:
        total = round(_f(recon.get("total_payout")), 2)
        total_source = _s(recon.get("source")) or "rep_commissions (last Run Calculation)"
    else:
        total = round(plan_subtotal + inst_subtotal, 2)
        total_source = "Computed from the plan and multi-month components (no last-calc row on file)."

    bucket_rows, bucket_total, has_buckets = _bucket_rows(buckets)

    # Employee-facing notes: WHY $0 (or partly) straight from the drill-down, then any drill-down note.
    notes = [n for n in (explain.get("zero_explanation") or []) if _s(n)]
    if _s(explain.get("note")):
        notes.append(_s(explain.get("note")))

    plan_name = _s(pc.get("plan_name")) or None
    # A carrier-STATEMENT-mode rep (Luxelink runs mixed engines) has no plan rules, so `earned`/`held`
    # come back empty — but the five canonical buckets ARE their per-line breakup. Treat the statement
    # as empty only when there is nothing at all to itemize, so a statement-mode rep with real earnings
    # sees the category breakup rather than a bare headline number (the "breakup not showing" report).
    is_empty = not earned and not held and not has_buckets

    return {
        "title": "Incentive Statement",
        "tenant": _s(tenant_name),
        "employee": _s(rep_name) or _s(explain.get("rep")),
        "period": _s(period) or _s(explain.get("period")),
        "generated_at": when,
        "summary": {
            "total_payout": money(total),
            "total_raw": total,
            "total_source": total_source,
            "plan_name": plan_name,
            "plan_subtotal": money(plan_subtotal),
            "installment_subtotal": money(inst_subtotal),
            "installments_paid": int(_f((mm.get("totals") or {}).get("paid"))),
            "installments_held": int(_f((mm.get("totals") or {}).get("withheld"))),
            "buckets": bucket_rows,
            "bucket_total": money(bucket_total),
            "has_buckets": has_buckets,
        },
        "earned": earned,
        "held": held,
        "notes": notes,
        "footnotes": footnotes,
        "empty": is_empty,
        "intro": _intro(plan_name, earned, held, has_buckets),
    }


def _intro(plan_name, earned, held, has_buckets):
    """The opening explainer, built FROM this statement's own content so it never promises a section the
    page does not contain. PURE."""
    bullets = [
        "This statement lists what you earned in incentive for the period shown. Every amount is taken "
        "from the same calculation that produced your payout — it is a summary of that result, not a new "
        "calculation.",
    ]
    if plan_name:
        bullets.append(f"Your plan incentive is earned under “{plan_name}”. Each item below is a "
                       "rule from that plan that one or more of your sales matched.")
    has_installment = any(i.get("source") == "installment" for i in earned)
    if held:
        # Held section is present (granted caller) — the intro may promise it.
        bullets.append("Residuals and multi-month items are paid over several months. An item can be held "
                       "for a month and pay in a later one; anything held is listed with the reason.")
    elif has_installment:
        # Multi-month earnings but the held section is off — describe the timing WITHOUT referencing held,
        # so the intro never promises a section the page does not contain (held is default-off + gated).
        bullets.append("Residuals and multi-month items are paid over several months, so an item can pay "
                       "in a later period than the sale.")
    if has_buckets:
        bullets.append("The category summary groups the same payout into the standard commission, spiff, "
                       "rebate and residual buckets.")
    bullets.append("Returned, voided and refunded sales do not earn incentive, and chargebacks are "
                   "deducted from the period in which they are applied.")
    return bullets


# ── PDF rendering ──────────────────────────────────────────────────────────────────────────────────
# Palette shared with payout_structure.py so the statement and the structure doc read as one system.
_INK = (0.13, 0.16, 0.22)        # body text
_NAVY = (0.11, 0.20, 0.36)       # headers / rules
_MUTED = (0.45, 0.50, 0.58)      # captions
_BAND = (0.94, 0.96, 0.98)       # zebra + callout fill
_WARN = (0.72, 0.42, 0.05)       # advisory text
_GREEN = (0.10, 0.42, 0.28)      # the earned-total figure


def render_pdf(doc):
    """Render ONE statement model to PDF bytes. reportlab imported lazily (payout_structure pattern)."""
    return _render_docs([doc])


def render_statements_pdf(docs):
    """Render a LIST of statement models into ONE multi-page PDF, one rep per page (PageBreak between).
    Reuses the exact single-statement story so a batch page is byte-for-byte a single page. An empty
    list yields a valid one-page 'no reps' document rather than a crash. reportlab imported lazily."""
    return _render_docs(list(docs or []))


def _render_docs(docs):
    """Shared renderer for one-or-many statements. Builds the styles + story helpers ONCE, then emits
    each doc's flowables (separated by a page break) into a single SimpleDocTemplate."""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                    HRFlowable, PageBreak)

    docs = list(docs or [])

    def C(rgb):
        return colors.Color(*rgb)

    def esc(v):
        """reportlab Paragraph parses mini-markup — a product name with & or < would crash the render or
        silently eat text. Same escaping payout_structure applies (harness_export_xss_upload)."""
        return (_s(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    buf = BytesIO()
    margin = 0.75 * inch
    page_w, page_h = LETTER
    avail = page_w - 2 * margin

    ss = getSampleStyleSheet()
    st_title = ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=22,
                              textColor=C(_NAVY), alignment=0, spaceAfter=2, leading=26)
    st_tenant = ParagraphStyle("tn", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=12,
                               textColor=C(_INK), spaceAfter=1, leading=15)
    st_meta = ParagraphStyle("m", parent=ss["Normal"], fontSize=8.5, textColor=C(_MUTED), leading=12)
    st_h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=13,
                           textColor=C(_NAVY), spaceBefore=14, spaceAfter=5, leading=16)
    st_h3 = ParagraphStyle("h3", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=9.5,
                           textColor=C(_INK), spaceBefore=8, spaceAfter=3, leading=12)
    st_body = ParagraphStyle("b", parent=ss["Normal"], fontSize=9.5, textColor=C(_INK), leading=13.5,
                             spaceAfter=4)
    st_bullet = ParagraphStyle("bu", parent=st_body, leftIndent=12, bulletIndent=2, spaceAfter=3)
    st_cap = ParagraphStyle("c", parent=ss["Normal"], fontSize=8.5, textColor=C(_MUTED), leading=11.5,
                            spaceAfter=3)
    st_warn = ParagraphStyle("w", parent=st_cap, textColor=C(_WARN))
    st_cell = ParagraphStyle("cl", parent=ss["Normal"], fontSize=8.8, textColor=C(_INK), leading=11.5)
    st_cell_b = ParagraphStyle("clb", parent=st_cell, fontName="Helvetica-Bold")
    st_cell_r = ParagraphStyle("clr", parent=st_cell, alignment=TA_RIGHT, fontName="Helvetica-Bold")
    st_head = ParagraphStyle("hd", parent=ss["Normal"], fontSize=8, fontName="Helvetica-Bold",
                             textColor=colors.white, leading=10)
    st_head_r = ParagraphStyle("hdr", parent=st_head, alignment=TA_RIGHT)
    # The big earned figure in the summary band.
    st_total = ParagraphStyle("tot", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=22,
                              textColor=C(_GREEN), alignment=TA_RIGHT, leading=24)
    st_total_lbl = ParagraphStyle("totl", parent=ss["Normal"], fontSize=8.5, textColor=C(_MUTED),
                                  leading=11)

    def table(data, widths, aligns=None, header_rows=1):
        h = header_rows
        t = Table(data, colWidths=widths, repeatRows=h, hAlign="LEFT")
        style = [
            ("BACKGROUND", (0, 0), (-1, h - 1), C(_NAVY)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, h), (-1, -1), [colors.white, C(_BAND)]),
            ("LINEBELOW", (0, h - 1), (-1, -2), 0.4, colors.Color(0.86, 0.88, 0.91)),
        ]
        t.setStyle(TableStyle(style + (aligns or [])))
        return t

    def emit(doc, story):
        """Append ONE statement's flowables to `story`. Shared by the single- and multi-rep renderers."""
        title = _s(doc.get("title")) or "Incentive Statement"
        tenant = _s(doc.get("tenant"))
        employee = _s(doc.get("employee"))
        period = _s(doc.get("period"))
        summary = doc.get("summary") or {}

        # ── masthead ──
        if tenant:
            story.append(Paragraph(esc(tenant), st_tenant))
        story.append(Paragraph(esc(title), st_title))
        who = " · ".join(p for p in [employee, (f"Period {period}" if period else "")] if p)
        if who:
            story.append(Paragraph(esc(who), st_meta))
        story.append(Paragraph(f"Generated {esc(doc.get('generated_at'))}", st_meta))
        story.append(Spacer(1, 6))
        story.append(HRFlowable(width="100%", thickness=1.6, color=C(_NAVY), spaceAfter=10))

        # ── summary band: the headline total + how it splits ──
        left = []
        if summary.get("plan_name"):
            left.append(Paragraph(f"<b>Plan:</b> {esc(summary.get('plan_name'))}", st_cell))
        left.append(Paragraph(f"Plan incentive: {esc(summary.get('plan_subtotal'))}", st_cell))
        _ip = summary.get("installments_paid") or 0
        _ih = summary.get("installments_held") or 0
        if summary.get("installment_subtotal") and (_ip or _ih):
            left.append(Paragraph(
                f"Multi-month residual: {esc(summary.get('installment_subtotal'))} "
                f"({_ip} paid, {_ih} held)", st_cell))
        right = [Paragraph("Total earned this period", st_total_lbl),
                 Paragraph(esc(summary.get("total_payout")), st_total)]
        band = Table([[left, right]], colWidths=[avail * 0.6, avail * 0.4], hAlign="LEFT")
        band.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C(_BAND)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), 1.4, C(_NAVY)),
        ]))
        story.append(band)
        story.append(Paragraph(esc(summary.get("total_source")), st_cap))

        story.append(Paragraph("About this statement", st_h2))
        for b in doc.get("intro") or []:
            story.append(Paragraph(esc(b), st_bullet, bulletText="•"))

        # ── what you earned ──
        earned = doc.get("earned") or []
        if earned:
            story.append(Paragraph("What you earned", st_h2))
            rows = [[Paragraph("Item", st_head), Paragraph("What qualified it", st_head),
                     Paragraph("Rate", st_head_r), Paragraph("How often", st_head),
                     Paragraph("Amount", st_head_r)]]
            for it in earned:
                cond = esc(it["condition"])
                if it.get("units"):
                    cond += f"<br/><font size=7 color='#6b7280'>{it['units']} qualifying item(s)</font>"
                rows.append([Paragraph(esc(it["what"]), st_cell_b), Paragraph(cond, st_cell),
                             Paragraph(esc(it["rate"]), st_cell_r), Paragraph(esc(it["frequency"]), st_cell),
                             Paragraph(esc(it["amount"]), st_cell_r)])
            rows.append([Paragraph("Total earned", st_cell_b), "", "", "",
                         Paragraph(esc(summary.get("total_payout")), st_cell_r)])
            n = len(rows) - 1
            story.append(table(
                rows, [avail * 0.22, avail * 0.34, avail * 0.16, avail * 0.13, avail * 0.15],
                [("ALIGN", (2, 0), (2, -1), "RIGHT"), ("ALIGN", (4, 0), (4, -1), "RIGHT"),
                 ("SPAN", (0, n), (3, n)),
                 ("BACKGROUND", (0, n), (-1, n), C(_BAND)),
                 ("LINEABOVE", (0, n), (-1, n), 0.8, C(_NAVY)),
                 ("FONTNAME", (4, n), (4, n), "Helvetica-Bold")]))
            if summary.get("total_source"):
                story.append(Paragraph(f"Payout of record: {esc(summary.get('total_source'))}", st_cap))
        elif doc.get("empty"):
            story.append(Paragraph("What you earned", st_h2))
            story.append(Paragraph("No incentive was earned for this period. The reasons below explain why.",
                                   st_body))

        # ── held / not yet paid ──
        held = doc.get("held") or []
        if held:
            story.append(Paragraph("Held or not yet paid", st_h2))
            story.append(Paragraph("These items did not pay this period. Each shows the reason; a held "
                                   "residual can pay in a later period once the reason clears.", st_cap))
            rows = [[Paragraph("Item", st_head), Paragraph("When", st_head),
                     Paragraph("Why it did not pay", st_head), Paragraph("Amount held", st_head_r)]]
            for it in held:
                rows.append([Paragraph(esc(it["what"]), st_cell_b), Paragraph(esc(it["when"]), st_cell),
                             Paragraph(esc(it["reason"]), st_cell),
                             Paragraph(esc(it["amount"] or "—"), st_cell_r)])
            story.append(table(rows, [avail * 0.20, avail * 0.18, avail * 0.47, avail * 0.15],
                               [("ALIGN", (3, 0), (3, -1), "RIGHT")]))

        # ── canonical payout categories (commission ledger) — for a carrier-STATEMENT-mode rep with no
        #    plan rules, THIS is the per-line breakup, so it is deliberately shown as such. ──
        if summary.get("has_buckets"):
            story.append(Paragraph("Payout by category", st_h2))
            _cap = ("Your earnings for the period, itemized by commission category." if not earned
                    else "The same payout, grouped into the standard commission ledger buckets.")
            story.append(Paragraph(_cap, st_cap))
            rows = [[Paragraph("Category", st_head), Paragraph("Amount", st_head_r)]]
            for b in summary.get("buckets") or []:
                rows.append([Paragraph(esc(b["label"]), st_cell_b), Paragraph(esc(b["amount"]), st_cell_r)])
            rows.append([Paragraph("Total", st_cell_b), Paragraph(esc(summary.get("bucket_total")), st_cell_r)])
            n = len(rows) - 1
            story.append(table(rows, [avail * 0.6, avail * 0.4],
                               [("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                ("BACKGROUND", (0, n), (-1, n), C(_BAND)),
                                ("LINEABOVE", (0, n), (-1, n), 0.8, C(_NAVY)),
                                ("FONTNAME", (1, n), (1, n), "Helvetica-Bold")]))

        # ── notes (why $0 / partial) ──
        if doc.get("notes"):
            story.append(Paragraph("Notes", st_h2))
            for nnote in doc["notes"]:
                story.append(Paragraph(esc(nnote), st_bullet, bulletText="•"))

        if doc.get("footnotes"):
            for fn in doc["footnotes"]:
                story.append(Paragraph(esc(fn), st_cap))

        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=0.6, color=colors.Color(0.8, 0.83, 0.87),
                                spaceAfter=6))
        story.append(Paragraph(
            "This statement summarises incentive calculated for the period shown, from the figures produced "
            "by the last incentive run. It is provided for your reference. The incentive structure it is "
            "based on is described in your Payout Structure document. If a figure looks wrong, raise it with "
            "your manager before the period closes.", st_cap))

    # ── footer identity + document metadata (batch-aware) ──
    _first = docs[0] if docs else {}
    tenant0 = _s(_first.get("tenant"))
    subject = "Incentive Statement" if len(docs) <= 1 else "Incentive Statements"
    if len(docs) == 1:
        _emp, _per = _s(_first.get("employee")), _s(_first.get("period"))
        _title0 = _s(_first.get("title")) or subject
        footer_txt = f"{_emp + ' — ' if _emp else ''}{_title0}" + (f" — {_per}" if _per else "")
        pdf_title = f"{_title0}{' — ' + _emp if _emp else ''}"
    else:
        footer_txt = f"{tenant0 + ' — ' if tenant0 else ''}Incentive Statements"
        pdf_title = f"Incentive Statements{' — ' + tenant0 if tenant0 else ''}"

    def on_page(canvas, _doc):
        """Footer: identity + page numbers. The standing caveat rides at the end of each rep's story."""
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(C(_MUTED))
        canvas.drawString(margin, margin * 0.55, footer_txt)
        canvas.drawRightString(page_w - margin, margin * 0.55, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    pdf = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin * 0.9,
                            title=pdf_title, author=tenant0 or "MetricsPro", subject=subject)

    story = []
    if not docs:
        # A period with no reps still yields a valid one-page document, never a crash.
        story.append(Paragraph("Incentive Statements", st_title))
        story.append(Paragraph("No reps with incentive for this period.", st_body))
    else:
        for _i, _d in enumerate(docs):
            if _i:
                story.append(PageBreak())
            emit(_d, story)

    pdf.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def filename_for(doc):
    """A stable, readable download name: tenant + employee + period + 'commission-statement'."""
    parts = [p for p in [_s(doc.get("tenant")), _s(doc.get("employee")), _s(doc.get("period")),
                         "commission-statement"] if p]
    slug = "-".join(parts).lower()
    keep = [c if (c.isalnum() or c == "-") else "-" for c in slug]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return (out.strip("-") or "commission-statement") + ".pdf"


# ── batch: one model per employee for a period (so a zip/export is possible later) ─────────────────
def build_statements(inputs, tenant_name="", period="", generated_at=None, gate_cfg=None,
                     include_held=False):
    """Map `build_statement` over a list of per-employee inputs. PURE — the caller has already fetched
    each rep's drill-down + buckets.

    `inputs`       : [{"rep": name, "explain": <explain_rep dict>, "buckets": {cat->amt}?,
                      "total_payout": float?}, ...]
    `include_held` : gate for the "Held / not yet paid" section, threaded to every statement (default-off).
    Returns        : [statement doc model, ...] in the given order.
    """
    out = []
    for row in inputs or []:
        out.append(build_statement(
            row.get("explain") or {}, buckets=row.get("buckets"),
            tenant_name=tenant_name, rep_name=_s(row.get("rep")),
            period=period, generated_at=generated_at, gate_cfg=gate_cfg,
            total_payout=row.get("total_payout"), include_held=include_held))
    return out
