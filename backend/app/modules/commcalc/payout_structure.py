"""PAYOUT STRUCTURE document — the employee-facing statement of HOW COMMISSION IS EARNED.

Owner directive 2026-08-11: _"i need payout structure to be communicated as the first pdf, make it in a
professional format"_ — a document you can hand an employee that says, in plain English, what pays, at what
rate, how often, and what never pays. A companion per-employee breakdown (what YOU earned, line by line)
is a separate document; this one is the STRUCTURE, not the statement.

READ-ONLY BY CONSTRUCTION. Nothing here writes, and nothing here computes pay. It RE-STATES the tenant's
own configuration (`commcalc.commission_plan` + its rules / tiers / assignments, migration 059 and the
later 232/260/261 columns) in the words an employee can act on. It therefore cannot move a payout — but it
CAN misinform, which is the whole risk of the document and the reason for the rules below.

THREE TRAPS THIS MODULE EXISTS TO AVOID — each one would print a WRONG RATE on a document employees rely on:

  ① `amount` AND `pct` are BOTH populated on real rows, and only one of them is read.
     `commission_engine._line_payout` reads `pct` for every pct_* kind and `amount` for the flat kinds.
     Luxelink's live data proves this is not theoretical: the NY accessory rule carries `amount=10.0` AND
     `pct=0.1` — the engine pays **10% of the sale price**, so a document that printed `amount` would tell
     every NY employee they earn $10.00 per accessory. `describe_rate()` reads the field the ENGINE reads,
     and nothing else.

  ② `contains` is a SUBSTRING test, not a list.
     Luxelink's `edge` rule is `tender_type contains "Credit Card; TW Financing Prepaid"` — one literal
     string, semicolons and all. Rendering it as "any of: Credit Card, TW Financing Prepaid" would describe
     a rule that pays on every credit-card sale. Only `in` is a list (comma-separated, per mig 059).

  ③ "per unit" does NOT mean "per line".
     Migration 260's pay gate collapses a `flat_per_unit` rule that matches on a TRANSACTION-level field
     (the tender) to **one payment per device** — that fix exists because one financed sale paid 8 x $25.
     The frequency column comes from `plan_pay_gate.resolve_unit_basis()`, the SAME resolver the engine
     calls, so this document can never drift from the arithmetic. It is not re-implemented here.

PURITY: everything above `render_pdf` is pure (no DB, no reportlab) so the English is unit-testable against
fixtures — see backend/harness_payout_structure.py. `render_pdf` imports reportlab lazily, the same way
notify/render.py does, so importing this module never costs the PDF stack.

MULTI-TENANT: the caller passes org-scoped rows; `build_doc` holds no org constant and reads no client.
"""
from datetime import datetime, timezone

from app.modules.commcalc import plan_pay_gate as _gate

# ── vocabulary ────────────────────────────────────────────────────────────────────────────────────
# How each match_field reads to a human. Keys are commission_engine.MATCH_FIELDS; an unknown field falls
# back to a humanized version of its own name rather than being dropped (a rule the reader can't see is
# worse than one described awkwardly).
FIELD_LABELS = {
    "contract_type": "Contract type",
    "tender_type": "Payment method",
    "department": "Department",
    "category": "Category",
    "product_desc": "Product name",
    "sku": "SKU",
    "trans_type": "Transaction type",
    "accessory": "Accessory classification",
    "activation_bucket": "Activation type",
    "any": "Every sale line",
}

# How the pay gate's unit basis reads to a human (mig 260). These are the employee-facing consequences of
# `plan_pay_gate.UNIT_BASES`.
BASIS_LABELS = {
    "per_line": "Each qualifying item",
    "per_device": "Once per device",
    "per_transaction": "Once per transaction",
}

BASIS_FOOTNOTES = {
    "per_device": ("Paid once for each device on the sale. The other items on the same receipt "
                   "(accessories, rate plans, activation fees) do not each earn this amount."),
    "per_transaction": "Paid once for the whole sale, however many items are on the receipt.",
}

SCOPE_LABELS = {"employee": "Employee", "store": "Store", "market": "Market", "default": "Everyone"}


def _s(v):
    return "" if v is None else str(v).strip()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(_s(v).replace("$", "").replace(",", "").replace("%", ""))
        except (TypeError, ValueError):
            return 0.0


def money(v):
    """$1,234.50 — two decimals, always. Pay documents do not round to whole dollars."""
    return f"${_f(v):,.2f}"


def pct(v):
    """0.175 -> '17.5%'. Trailing zeros trimmed so 0.10 reads '10%', not '10.00%'."""
    n = _f(v) * 100.0
    s = f"{n:,.2f}".rstrip("0").rstrip(".")
    return f"{s or '0'}%"


def _quote(v):
    return f'“{_s(v)}”'


def display_label(v):
    """An admin's rule label, presented. Tenants type them as working shorthand ('accessory', 'twp',
    'edge2'), and a pay document in all-lowercase reads as a draft. Only the FIRST character is raised, and
    only when the label carries no capitals of its own — Title-casing would turn 'VHI' into 'Vhi' and
    'FIOS' into 'Fios', renaming the tenant's own products. PURE."""
    s = _s(v)
    if not s or any(c.isupper() for c in s):
        return s
    return s[0].upper() + s[1:]


# ── ① condition: which sales a rule matches ───────────────────────────────────────────────────────
def describe_condition(rule):
    """One sentence naming exactly which sale lines this rule matches. PURE.

    Mirrors `commission_engine._rule_matches` operator-for-operator: `equals` is exact (case-insensitive),
    `contains` is a SUBSTRING of the whole stored value, `in` is a comma-separated list. Trap ② — do not
    "helpfully" split a `contains` value on its separators; that describes a different, broader rule.
    """
    field = (_s(rule.get("match_field")) or "any").lower()
    op = (_s(rule.get("match_op")) or "equals").lower()
    val = _s(rule.get("match_value"))

    if field == "any":
        return "Every sale line"

    # The synthetic classifiers read as a plain English category rather than a field/value comparison.
    if field == "accessory":
        return ("Any item classified as an accessory" if val.lower() in ("yes", "true", "1")
                else "Any item NOT classified as an accessory")
    if field == "activation_bucket" and op == "equals" and val:
        return f"{val.title()} activations"

    label = FIELD_LABELS.get(field) or field.replace("_", " ").capitalize()
    if not val:
        return f"{label} (no value set)"
    if op == "in":
        opts = [x.strip() for x in val.split(",") if x.strip()]
        if len(opts) == 1:
            return f"{label} is {_quote(opts[0])}"
        return f"{label} is any of: {', '.join(_quote(o) for o in opts)}"
    if op == "contains":
        return f"{label} contains {_quote(val)}"
    return f"{label} is {_quote(val)}"


# ── ② rate: what one match earns ──────────────────────────────────────────────────────────────────
def describe_rate(rule):
    """(rate_text, pays_nothing) for one rule. PURE.

    Trap ① — reads the SAME field `commission_engine._line_payout` reads for that payout_kind: `pct` for
    every pct_* kind, `amount` for flat_per_unit / flat. The unread field is ignored even when populated.

    `pays_nothing` is True when the rate that WILL be read is zero, so the caller can move the rule to the
    "does not pay" section instead of printing "$0.00 per unit" in a pay table.
    """
    kind = (_s(rule.get("payout_kind")) or "flat_per_unit").lower()
    amt, p = _f(rule.get("amount")), _f(rule.get("pct"))
    if kind == "pct_gp":
        return f"{pct(p)} of gross profit", p == 0
    if kind == "pct_price":
        return f"{pct(p)} of the sale price", p == 0
    if kind == "pct_price_over_cost":
        return f"{pct(p)} of the margin (sale price less cost)", p == 0
    if kind == "pct_mrc":
        return f"{pct(p)} of the monthly plan charge", p == 0
    if kind == "flat":
        return f"{money(amt)} bonus", amt == 0
    return money(amt), amt == 0   # flat_per_unit — the frequency column says per what


def describe_frequency(rule, ucfg=None):
    """(frequency_text, footnote_or_None) — HOW OFTEN the rate is paid. PURE.

    Trap ③ — delegates to `plan_pay_gate.resolve_unit_basis`, the resolver the engine itself uses, so the
    document and the payout can never disagree about whether a rule pays per line or per device.
    """
    kind = (_s(rule.get("payout_kind")) or "flat_per_unit").lower()
    if kind == "flat":
        return "Once per pay period", None
    if kind != "flat_per_unit":
        # A %-of-basis rule reads each line's own price/GP/MRC and is never deduped (see resolve_unit_basis).
        return "Each qualifying item", None
    basis, _src = _gate.resolve_unit_basis(rule, ucfg if ucfg is not None else _gate.UNIT_DEFAULTS)
    return BASIS_LABELS.get(basis, BASIS_LABELS["per_line"]), BASIS_FOOTNOTES.get(basis)


def describe_rule_scope(rule):
    """'Applies to' text when a rule is scoped to a store/market/employee (mig 261 ③), else None. PURE.

    Printing this is not optional: an unscoped-looking $10 activation rule that in fact applies only to NY
    is precisely the complaint that created the feature.

    `_gate.rule_scope` is authoritative for WHETHER a rule is scoped, but it returns values run through
    `_canon` (lower-cased, punctuation flattened) for MATCHING. Displaying those would print
    "957 pennsylvania ave" where the admin typed "957 Pennsylvania Ave", so the text is rebuilt from the
    raw column and the resolver is used only for the decision.
    """
    kind, vals = _gate.rule_scope(rule)
    if not kind or not vals:
        return None
    raw = rule.get("applies_scope_value")
    shown = [_s(v) for v in (raw if isinstance(raw, (list, tuple)) else _s(raw).split(",")) if _s(v)]
    return f"{SCOPE_LABELS.get(kind, kind.title())}: {', '.join(shown or vals)}"


# Exclusion rows (mig 261) use a WIDER operator vocabulary than commission rules — `word`, `prefix` and
# `suffix` exist because 'RTR' is a 3-letter token and a plain `contains` would exclude 'CARTRIDGE'.
# Routing them through describe_condition would print 'is "RTR"' for a word-anchored match.
def describe_exclusion_condition(rule):
    """One sentence naming which lines an exclusion row removes from pay. PURE."""
    op = (_s(rule.get("match_op")) or "word").lower()
    field = (_s(rule.get("match_field")) or "").lower()
    val = _s(rule.get("match_value"))
    label = FIELD_LABELS.get(field) or field.replace("_", " ").capitalize()
    if not val:
        return f"{label} (no value set)"
    if op == "word":
        return f"{label} contains the word {_quote(val)}"
    if op == "prefix":
        return f"{label} starts with {_quote(val)}"
    if op == "suffix":
        return f"{label} ends with {_quote(val)}"
    return describe_condition(rule)


# ── ③ who a plan applies to ───────────────────────────────────────────────────────────────────────
def describe_assignments(assignments):
    """{'lines': [...], 'people': [...], 'is_default': bool} for a plan's assignment rows. PURE.

    Grouped by scope in the engine's own precedence order (employee > store > market > default) so the
    document explains the same hierarchy `_resolve_plan_for` applies.
    """
    by_scope = {}
    for a in assignments or []:
        scope = (_s(a.get("scope")) or "default").lower()
        val = _s(a.get("scope_value"))
        by_scope.setdefault(scope, [])
        if val:
            by_scope[scope].append(val)
    lines, people = [], sorted(by_scope.get("employee", []), key=lambda s: s.lower())
    if people:
        lines.append(f"{len(people)} named employee{'s' if len(people) != 1 else ''}")
    for scope in ("store", "market"):
        vals = sorted(by_scope.get(scope, []), key=lambda s: s.lower())
        if vals:
            lines.append(f"{SCOPE_LABELS[scope]}: {', '.join(vals)}")
    is_default = "default" in by_scope
    if is_default:
        lines.append("Everyone not covered by a more specific plan")
    return {"lines": lines, "people": people, "is_default": is_default}


def describe_tiers(plan):
    """{'metric','rows','below'} tier table, or None when the plan does not tier. PURE."""
    tiers = sorted(plan.get("tiers") or [], key=lambda t: _f(t.get("min_count")))
    if not tiers:
        return None
    metric = _s(plan.get("base_tier_metric")) or _s((tiers[0] or {}).get("metric")) or "qualifying units"
    rows = []
    for t in tiers:
        mult = _f(t.get("multiplier"))
        rows.append({
            "label": _s(t.get("label")) or f"{int(_f(t.get('min_count')))}+ {metric}",
            "min_count": int(_f(t.get("min_count"))),
            "multiplier": f"{mult:g}x",
            "effect": ("full rate" if mult == 1 else
                       f"{pct(mult - 1)} more than the base rate" if mult > 1 else
                       f"{pct(1 - mult)} less than the base rate"),
        })
    below = plan.get("tier_below_min_multiplier")
    return {"metric": metric, "rows": rows,
            "below": (f"{_f(below):g}x" if below is not None else None)}


# ── the document ──────────────────────────────────────────────────────────────────────────────────
def build_doc(plans, tenant_name="", gate_cfg=None, exclusions=None, generated_at=None, plan_id=None):
    """Turn a tenant's plan configuration into the payout-structure DOCUMENT MODEL. PURE — no I/O.

    `plans`      : commission_engine._load_plans() output (plan dicts with nested rules/tiers/assignments)
    `gate_cfg`   : plan_pay_gate.load_gate_config() output; None => the code defaults (which ARE the
                   owner's rule — see mig 260's header), so an unconfigured tenant is described correctly.
    `exclusions` : plan_pay_gate.load_exclusions() output — what NEVER pays, for any plan.
    `plan_id`    : render one plan only (a per-plan handout) instead of the whole book.

    The model is deliberately literal: every string an employee will read is decided HERE, where it can be
    tested, not in the renderer.
    """
    ucfg = ((gate_cfg or {}).get("unit_basis") or _gate.UNIT_DEFAULTS)
    # "%-d" is a glibc extension — it raises on non-glibc platforms. Built explicitly instead so the
    # document renders the same everywhere it might run.
    _now = datetime.now(timezone.utc)
    when = generated_at or f"{_now.strftime('%B')} {_now.day}, {_now.year}"

    sel = [p for p in (plans or []) if not plan_id or _s(p.get("id")) == _s(plan_id)]
    # Inactive plans are listed last and LABELLED rather than hidden: an employee assigned to a paused plan
    # must be able to see why they are earning nothing.
    sel.sort(key=lambda p: (not bool(p.get("is_active", True)), _s(p.get("name")).lower()))

    out_plans, footnotes = [], []
    for p in sel:
        pay_items, no_pay_items, warnings = [], [], []
        for r in sorted(p.get("rules") or [], key=lambda x: (_f(x.get("sort")), _s(x.get("label")))):
            cond = describe_condition(r)
            rate, zero = describe_rate(r)
            freq, note = describe_frequency(r, ucfg)
            what = display_label(r.get("label")) or cond
            scope = describe_rule_scope(r)
            qualifies = bool(r.get("qualifies", True))
            if note and note not in footnotes:
                footnotes.append(note)
            if not qualifies:
                no_pay_items.append({"what": what, "condition": cond,
                                     "why": "Tracked for reporting only — does not pay."})
            elif zero:
                no_pay_items.append({"what": what, "condition": cond,
                                     "why": "Currently set to zero — earns no commission."})
            else:
                pay_items.append({"what": what, "condition": cond, "rate": rate, "frequency": freq,
                                  "scope": scope, "tiered": bool(r.get("tiered"))})

        applies = describe_assignments(p.get("assignments"))
        tiers = describe_tiers(p)
        if any(i["tiered"] for i in pay_items) and not tiers:
            # A rule flagged tiered with no tier table pays its base rate — say so rather than implying a
            # multiplier the plan cannot apply.
            warnings.append("Some items are marked as tier-scaled, but this plan has no tiers configured, "
                            "so they pay at the base rate shown.")
        if not pay_items:
            warnings.append("This plan has no paying items configured. Anyone assigned to it earns "
                            "$0.00 in plan commission." if not no_pay_items else
                            "None of this plan's items currently pay.")
        if not applies["lines"]:
            warnings.append("This plan is not assigned to anyone yet.")

        out_plans.append({
            "id": _s(p.get("id")),
            "name": _s(p.get("name")) or "Untitled plan",
            "active": bool(p.get("is_active", True)),
            "applies": applies,
            "pay_items": pay_items,
            "no_pay_items": no_pay_items,
            "tiers": tiers,
            "notes": _s(p.get("notes")) or None,
            "warnings": warnings,
        })

    never = []
    for e in (exclusions or []):
        if not bool(e.get("enabled", True)):
            continue
        never.append({"label": _s(e.get("label")) or _s(e.get("code")),
                      "condition": describe_exclusion_condition(e),
                      "reason": _s(e.get("reason")) or None})

    return {
        "title": "Commission Payout Structure",
        "tenant": _s(tenant_name),
        "generated_at": when,
        "plans": out_plans,
        "never_pays": never,
        "footnotes": footnotes,
        "how_it_works": _how_it_works(out_plans),
    }


def _how_it_works(out_plans):
    """The opening explainer. Built FROM the resolved plans, so it never claims something the tenant's own
    configuration does not do. PURE."""
    bullets = [
        "Commission is calculated from the sales recorded in the point-of-sale system. Every sale line is "
        "checked against the rules of the plan you are assigned to.",
        "When a line matches a rule, it earns that rule's rate. A single sale can earn from more than one "
        "rule — for example a device and its accessories.",
    ]
    if len(out_plans) > 1:
        bullets.append("More than one plan exists. The plan assigned to you personally takes precedence "
                       "over a plan assigned to your store, then your market, then the company default.")
    if any(p["tiers"] for p in out_plans):
        bullets.append("Some plans pay more once you pass a volume tier. The tier you reach applies to the "
                       "items marked as tier-scaled.")
    bullets.append("Returned, voided and refunded sales do not earn commission. Chargebacks are deducted "
                   "from the payout for the period in which they are applied.")
    return bullets


# ── PDF rendering ─────────────────────────────────────────────────────────────────────────────────
# Colors are defined once here so the document reads as one system across sections.
_INK = (0.13, 0.16, 0.22)        # body text
_NAVY = (0.11, 0.20, 0.36)       # headers / rules
_MUTED = (0.45, 0.50, 0.58)      # captions
_BAND = (0.94, 0.96, 0.98)       # zebra + callout fill
_WARN = (0.72, 0.42, 0.05)       # advisory text


def render_pdf(doc):
    """Render the document model to PDF bytes. reportlab is imported lazily (notify/render.py pattern)."""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                    HRFlowable)

    def C(rgb):
        return colors.Color(*rgb)

    def esc(v):
        """reportlab Paragraph parses mini-markup — a product name with & or < would crash the render or
        silently eat text. Same escaping notify/render.py learned to apply (harness_export_xss_upload)."""
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
    # The plan-identity band that rides on top of each rate table and repeats across page breaks.
    st_band_title = ParagraphStyle("bt", parent=ss["Normal"], fontSize=12.5, fontName="Helvetica-Bold",
                                   textColor=colors.white, leading=15)
    st_band_sub = ParagraphStyle("bs", parent=ss["Normal"], fontSize=8, textColor=C((0.72, 0.79, 0.88)),
                                 leading=11)

    title = _s(doc.get("title")) or "Commission Payout Structure"
    tenant = _s(doc.get("tenant"))

    def on_page(canvas, _doc):
        """Footer: page numbers + the standing caveat that this describes configuration, not an amount owed."""
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(C(_MUTED))
        canvas.drawString(margin, margin * 0.55,
                          f"{tenant + ' — ' if tenant else ''}{title}")
        canvas.drawRightString(page_w - margin, margin * 0.55, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    pdf = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin * 0.9,
                            title=f"{title}{' — ' + tenant if tenant else ''}",
                            author=tenant or "MetricsPro", subject=title)

    story = []
    # ── masthead ──
    if tenant:
        story.append(Paragraph(esc(tenant), st_tenant))
    story.append(Paragraph(esc(title), st_title))
    story.append(Paragraph(f"Effective as configured on {esc(doc.get('generated_at'))}", st_meta))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.6, color=C(_NAVY), spaceAfter=10))

    story.append(Paragraph("How your commission works", st_h2))
    for b in doc.get("how_it_works") or []:
        story.append(Paragraph(esc(b), st_bullet, bulletText="•"))

    def table(data, widths, aligns=None, header_rows=1):
        """`header_rows` header lines are painted navy and REPEAT on every page the table spans."""
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

    for plan in doc.get("plans") or []:
        title_txt = esc(plan["name"]) + ("" if plan["active"] else "  (not active)")
        applies = plan["applies"]["lines"]
        applies_txt = ("Applies to: " + esc(", ".join(applies))) if applies else ""

        if plan["pay_items"]:
            # The plan's NAME and who it applies to are the table's first two header rows, and all three
            # header rows repeat (repeatRows=3). A long plan may therefore split across pages without ever
            # leaving rates standing under no plan name — which reads as the WRONG plan's rates — and
            # without forcing a half-empty page to keep the block whole.
            rows = [
                [Paragraph(title_txt, st_band_title), "", "", ""],
                [Paragraph(applies_txt, st_band_sub), "", "", ""],
                [Paragraph("What pays", st_head), Paragraph("When it applies", st_head),
                 Paragraph("Rate", st_head_r), Paragraph("How often", st_head)],
            ]
            for it in plan["pay_items"]:
                cond = esc(it["condition"])
                if it["scope"]:
                    cond += f"<br/><font size=7.5 color='#6b7280'>Only for {esc(it['scope'])}</font>"
                rate = esc(it["rate"]) + ("<br/><font size=7 color='#6b7280'>tier-scaled</font>"
                                          if it["tiered"] else "")
                rows.append([Paragraph(esc(it["what"]), st_cell_b), Paragraph(cond, st_cell),
                             Paragraph(rate, st_cell_r), Paragraph(esc(it["frequency"]), st_cell)])
            story.append(Spacer(1, 12))
            story.append(table(
                rows, [avail * 0.21, avail * 0.37, avail * 0.20, avail * 0.22],
                [("ALIGN", (2, 0), (2, -1), "RIGHT"),
                 ("SPAN", (0, 0), (-1, 0)), ("SPAN", (0, 1), (-1, 1)),
                 ("BACKGROUND", (0, 0), (-1, 1), C(_NAVY)),
                 ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 1)],
                header_rows=3))
        else:
            story.append(Paragraph(title_txt, st_h2))
            if applies_txt:
                story.append(Paragraph(applies_txt, st_cap))

        if plan["tiers"]:
            story.append(Paragraph(f"Volume tiers — based on {esc(plan['tiers']['metric'])}", st_h3))
            rows = [[Paragraph("Tier", st_head), Paragraph("Reach", st_head),
                     Paragraph("Multiplier", st_head_r), Paragraph("Effect", st_head)]]
            for t in plan["tiers"]["rows"]:
                rows.append([Paragraph(esc(t["label"]), st_cell_b),
                             Paragraph(f"{t['min_count']} or more", st_cell),
                             Paragraph(esc(t["multiplier"]), st_cell_r),
                             Paragraph(esc(t["effect"]), st_cell)])
            story.append(table(rows, [avail * 0.28, avail * 0.22, avail * 0.18, avail * 0.32],
                               [("ALIGN", (2, 0), (2, -1), "RIGHT")]))
            if plan["tiers"]["below"]:
                story.append(Paragraph(f"Below the first tier, tier-scaled items pay "
                                       f"{esc(plan['tiers']['below'])} of the base rate.", st_cap))

        if plan["no_pay_items"]:
            story.append(Paragraph("Included in this plan but not paid", st_h3))
            for it in plan["no_pay_items"]:
                story.append(Paragraph(f"<b>{esc(it['what'])}</b> — {esc(it['condition'])}. "
                                       f"{esc(it['why'])}", st_cap))

        if plan["notes"]:
            story.append(Paragraph(esc(plan["notes"]), st_cap))
        for w in plan["warnings"]:
            story.append(Paragraph(esc(w), st_warn))

    if doc.get("never_pays"):
        story.append(Paragraph("What never earns commission", st_h2))
        story.append(Paragraph("These apply to every plan, regardless of the rules above.", st_cap))
        rows = [[Paragraph("Excluded", st_head), Paragraph("When it applies", st_head),
                 Paragraph("Reason", st_head)]]
        for e in doc["never_pays"]:
            rows.append([Paragraph(esc(e["label"]), st_cell_b), Paragraph(esc(e["condition"]), st_cell),
                         Paragraph(esc(e["reason"] or "—"), st_cell)])
        story.append(table(rows, [avail * 0.24, avail * 0.44, avail * 0.32]))

    if doc.get("footnotes"):
        story.append(Paragraph("Notes", st_h2))
        for n in doc["footnotes"]:
            story.append(Paragraph(esc(n), st_bullet, bulletText="•"))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.Color(0.8, 0.83, 0.87),
                            spaceAfter=6))
    story.append(Paragraph(
        "This document describes the commission structure as configured on the date shown. It is not a "
        "statement of earnings and does not create an entitlement to any amount. Your own earnings for a "
        "period are shown on your individual commission statement.", st_cap))

    pdf.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def filename_for(doc):
    """A stable, readable download name: tenant + document + date."""
    parts = [p for p in [_s(doc.get("tenant")), "payout-structure", _s(doc.get("generated_at"))] if p]
    slug = "-".join(parts).lower()
    keep = [c if (c.isalnum() or c == "-") else "-" for c in slug]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return (out.strip("-") or "payout-structure") + ".pdf"
