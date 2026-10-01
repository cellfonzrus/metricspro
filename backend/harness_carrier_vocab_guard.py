"""GUARD — carrier vocabulary must never cross sides in frontend page copy (owner 2026-09-04).

The owner's rule, verbatim intent: "the word total wireless cannot be on the boost side and the
word Boost cannot be on the total tenant." The durable mechanism is the mig-945/953 carrier label
preset system (report_col / report_banner / report_term scopes on commcalc.ui_label_override,
resolved by report_labels.py + frontend lib/report-labels.ts); nav-level features are gated by
NAV_CARRIERS in frontend/src/lib/rbac.ts (admin-overridable via caps['carrier:<href>']).

This static scan keeps NEW hardcoded carrier vocabulary out of rendered frontend copy:

  · It extracts DISPLAY segments per line (string literals + JSX text; comments stripped; pure
    identifier/path tokens skipped) from frontend/src/**/*.ts[x].
  · A Total-side term (VidaPay / T-CETRA / Total Wireless / MA Handset / MA Commission / MA Daily
    Tx / MA Tx / Total Access) or Boost-side term (Boost / VIP / ACIMA / PayGo / Dish / ePay /
    owed-to-VIP / Asset Ledger / Boost's ATU-MI wording) found in a display segment FAILS unless:
      (a) the file is a page whose href is carrier-gated in NAV_CARRIERS to that term's own side
          (a Boost-gated page may say Boost/VIP/ePay; a Total-gated page may say VidaPay/MA), or
      (b) the (file, side) pair is in the REVIEWED_EXCEPTIONS allowlist below — each entry is a
          deliberate decision with its reason (term-is-data config vocabularies, active-carrier
          lens-gated copy, data-conditional strings that only render on data of that carrier, the
          carrier-selection onboarding screen itself, other agents' surfaces).
  · POS VENDOR NAMES (owner 2026-09-20: "it should customize the message based on what POS is being
    used") are scanned by the SAME posture, one axis over. The vocabulary is DERIVED — never listed
    here — from the homes that declare a POS: the `report_term:*` / `pos_system` seeds in
    database/migrations (mig 953 'b2bsoft', mig 1004 'RQ'), the `commcalc.pos_profile` seed's
    pos_key + label (mig 200), and `report_kinds.HOUSE_KINDS[].applies_to_pos`. Each name yields its
    spellings (the code, the label's words, the brand stem: 'b2bsoft' / 'B2B Soft' / 'B2B'; 'RQ').
    A spelling in a DISPLAY segment of frontend/src/**, or as a BARE string literal in a page or
    component (the `term('pos_system', 'b2bsoft')` fallback that would print a vendor when the term
    is unresolved), FAILS unless the file is in POS_REVIEWED_EXCEPTIONS with its reason — and an entry
    whose file no longer carries a spelling FAILS as stale. Pages name the POS through
    lib/report-labels.ts usePosTerm() (the declared term, else the registry's neutral noun) and NEVER
    spell one. The backend payload modules that feed those pages (POS_BACKEND_COPY) are held to the
    same rule for whitespace-bearing string literals (report_labels.pos_term is their home), and the
    registry / intake / spine logic (POS_BACKEND_LOGIC) may not name a vendor at all — the check that
    #257's harness_report_kind_lock.py carried as its `pos_vendor` class, folded here so ONE lock
    owns this fact. Negative controls prove every rule can go red.

Adding a new carrier-branded string to shared copy → add a preset term (report_labels.LABELABLE_TERMS
+ a mig ≥953 seed) and render it via useReportLabels().term(), or gate the page in NAV_CARRIERS —
never extend REVIEWED_EXCEPTIONS as a shortcut. A sentence that names the POS → usePosTerm().pos.

  python3 backend/harness_carrier_vocab_guard.py
  python3 backend/harness_carrier_vocab_guard.py --print-pos-vocab   # the derived spellings, as JSON
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
RBAC = os.path.join(FE, "lib", "rbac.ts")
MIGRATIONS = os.path.join(ROOT, "database", "migrations")
BE = os.path.join(ROOT, "backend", "app", "modules")
sys.path.insert(0, os.path.join(ROOT, "backend"))

TOTAL_TERMS = re.compile(
    r"vidapay|t-?cetra|tettra|total\s+wireless|ma\s+handset|ma\s+commission|ma\s+daily\s+tx"
    r"|ma\s+tx\b|ma\s+fulfillment|total\s+access\b", re.I)
BOOST_TERMS = re.compile(
    r"\bboost\b|vip\s+wireless|\bvip\b|\bacima\b|\bpay-?go\b|\bdish\b|owed[ -]to[ -]vip"
    r"|asset\s+ledger|\bepay\b", re.I)

# ── REVIEWED EXCEPTIONS — (relative file path) -> {side or 'both': reason}. Every entry is a
#    decision from the 2026-09-04 sweep; do not add entries without the same review.
REVIEWED_EXCEPTIONS = {
    # The carrier-selection onboarding screen itself — the term IS the choice being offered.
    "components/CarrierPicker.tsx": {"both": "carrier-selection screen; carrier names are the data"},
    # Mechanism code: the lens/gate helpers must name carriers to normalize/scrub them.
    "lib/rbac.ts": {"both": "carrier lens/gate mechanism + nav registry (labels below are NAV_CARRIERS-gated at render)"},
    "lib/carrier-scope.ts": {"both": "the vocabulary-scrub mechanism itself"},
    "lib/auth-context.tsx": {"both": "active-carrier lens state (code values, not copy)"},
    "lib/report-labels.ts": {"both": "the preset-resolution mechanism"},
    # Config vocabularies where the term is data (processor ids, POS pick-lists, tender keys).
    "app/(platform)/commcalc/email-imports/page.tsx": {
        "both": "processor-id registry page: ids/how-tos render only for processors the tenant configured"},
    "app/(platform)/commcalc/connectors/page.tsx": {"both": "connector source-kind ids (data)"},
    "app/(platform)/pos/activations/page.tsx": {"both": "generic POS carrier pick-list (data)"},
    "app/(platform)/pos/customers/page.tsx": {"both": "generic POS carrier pick-list (data)"},
    "app/(platform)/pos/import/page.tsx": {"both": "generic POS business-type vocabulary (data)"},
    "app/(platform)/pos/vendors/page.tsx": {"both": "generic POS business-type vocabulary (data)"},
    "app/(platform)/pos/onboarding/page.tsx": {"boost": "POS onboarding names the consignment ledger option (data)"},
    # Active-carrier-lens-gated copy (renders only under the matching lens/mode) — verified in-file.
    "app/(platform)/commcalc/sales-report/page.tsx": {"boost": "showBoost = single-carrier boost lens gate"},
    "app/(platform)/commcalc/payout-plans/page.tsx": {"boost": "carrier-mode/lens-gated engine copy"},
    "app/(platform)/commcalc/reports/page.tsx": {"boost": "boost-engine table rendered only in carrierMode boost"},
    "app/(platform)/commcalc/daily-commission/page.tsx": {"boost": "mode==='boost' branch only"},
    "app/(platform)/commcalc/whatif/page.tsx": {
        "both": "engine/source-conditional labels (boost engine vs MA-source months of the selected carrier)"},
    "app/(platform)/commcalc/atu-opportunity/page.tsx": {"both": "per-carrier param labels lens-filtered in-file"},
    "app/(platform)/commcalc/commission-legs/page.tsx": {"both": "lens-gated empty-state + source names of loaded feeds"},
    "app/(platform)/commcalc/management-incentive/page.tsx": {"total": "carrier-named presets lens-hidden in-file"},
    "app/(platform)/commcalc/upload/page.tsx": {"both": "tiles registry-gated (report-kind registry, §30.9) + tileVisible-filtered in-file"},
    "app/(platform)/commcalc/_lib/uploadRoutes.ts": {
        "both": "upload ROUTE metadata keyed by route key; a route renders only when the tenant's report-kind registry names it (carrier scope is registry data, §30.9)"},
    "app/(platform)/commcalc/upload/wizard/page.tsx": {
        "boost": "FALLBACK_STEPS legacy boost defaults; connector-driven path is data-scoped"},
    # Data-conditional copy: the string renders only alongside that carrier's own data rows.
    "app/(platform)/commcalc/discrepancy/page.tsx": {"boost": "bounty-code glossary keyed to boost rows"},
    "app/(platform)/commcalc/commission-discrepancy/page.tsx": {"boost": "source values of loaded engines (data)"},
    "app/(platform)/commcalc/commission-ledger/page.tsx": {"boost": "source picker values (data)"},
    "app/(platform)/commcalc/device-history/DeviceHistoryLookup.tsx": {"boost": "PayGo row shown only when field present"},
    "app/(platform)/commcalc/device-history/deviceHistoryExport.ts": {
        "both": "MA sheet / PayGo rows exported only when that data exists"},
    "app/(platform)/commcalc/device-cost-recon/page.tsx": {"boost": "VIP caveat renders only with vip evidence rows"},
    "app/(platform)/commcalc/imei-rebates/page.tsx": {"boost": "feed options derived from loaded sources (data)"},
    "app/(platform)/components/EmployeeWidgets.impl.tsx": {"boost": "optional comp component, hidden when 0"},
    "components/EmployeeWidgets.impl.tsx": {"boost": "optional comp component, hidden when 0"},
    "app/(platform)/commcalc/gp/page.tsx": {"total": "MA-source caption only when source is MA"},
    "app/(platform)/commcalc/_lib/commissionExport.ts": {"boost": "boost-engine export builder (mode-gated rows)"},
    "app/(platform)/commcalc/commission-plans/page.tsx": {
        "boost": "tender examples are data values; engine wording is showBoost lens-gated"},
    "app/(platform)/commcalc/mapping/page.tsx": {"total": "cards filtered by carrierOKActive in-file"},
    "app/(platform)/commcalc/asset/_shared/NoLedgerData.tsx": {
        "boost": "shared empty-state rendered only by NAV-gated boost asset pages"},
    "app/(platform)/commcalc/commission-category-map/page.tsx": {"total": "page NAV-gated to total (mapping hub filtered)"},
    # Closing/ops surfaces now rendering via report_term presets (ep/fin) — remaining matches are
    # identifiers-in-strings and DM wording pending the owner's closing-vocabulary preview.
    "components/ClosingSubmitForm.tsx": {"boost": "labels wired to term(); custom-tender keys are data"},
    "components/DailyClosingVerify.tsx": {"boost": "labels wired to term(); remaining matches are field ids"},
    "app/(platform)/closing/_lib/SubmissionsTable.tsx": {"boost": "labels wired to term()"},
    "app/(platform)/closing/page.tsx": {"boost": "labels wired to term()"},
    # Other agents' surfaces (listed for their owners; not this agent's turf to reword).
    "app/(platform)/storeops/employees/page.tsx": {"boost": "payroll-workforce agent's surface (epay_login fields)"},
    "app/(platform)/storeops/schedule/page.tsx": {"boost": "payroll-workforce agent's surface"},
    "app/(platform)/admin/roles/page.tsx": {"boost": "module key labels (data)"},
    "app/(platform)/admin/labels/page.tsx": {"both": "the label-editor itself documents example labels"},
}

# ══ POS VENDOR VOCABULARY — derived from the seeds, never listed ═══════════════════════════════════
_TERM_SEED = re.compile(r"'report_term:[a-z0-9_-]+'\s*,\s*'pos_system'\s*,\s*'([^']+)'", re.I)
_PROFILE_SEED = re.compile(r"commcalc\.pos_profile\s*\([^)]*\)\s*VALUES\s*\(\s*\w+\s*,\s*'([^']+)'\s*,\s*'([^']+)'", re.I)
_GENERIC_WORDS = {"standard", "default", "pos", "system", "the", "and"}


def pos_vocabulary():
    """{spelling: origin} for every POS vendor name the seeds declare, in every spelling it is written.
    Origins: report_term seed (mig 953/1004), pos_profile seed (mig 200), report_kinds.HOUSE_KINDS."""
    names = {}
    for f in sorted(os.listdir(MIGRATIONS)):
        if not f.endswith(".sql"):
            continue
        src = open(os.path.join(MIGRATIONS, f), encoding="utf-8").read()
        for v in _TERM_SEED.findall(src):
            names.setdefault(v.strip(), f + " report_term pos_system")
        for key, label in _PROFILE_SEED.findall(src):
            names.setdefault(key.strip(), f + " pos_profile.pos_key")
            names.setdefault(label.strip(), f + " pos_profile.label")
    try:
        from app.modules.commcalc import report_kinds as _rk
        for r in _rk.HOUSE_KINDS:
            for c in r.get("applies_to_pos") or []:
                names.setdefault(str(c).strip(), "report_kinds.HOUSE_KINDS applies_to_pos")
    except Exception as e:      # pragma: no cover
        print("WARN could not read report_kinds.HOUSE_KINDS:", e)
    try:                        # the connector registry's POS scopes (mig 1014) — the same codes, one axis over
        from app.modules.commcalc import connector_registry as _cr
        for r in _cr.HOUSE_CONNECTORS:
            for c in r.get("applies_to_pos") or []:
                names.setdefault(str(c).strip(), "connector_registry.HOUSE_CONNECTORS applies_to_pos")
    except Exception as e:      # pragma: no cover
        print("WARN could not read connector_registry.HOUSE_CONNECTORS:", e)
    spellings = {}
    for name, origin in names.items():
        base = re.sub(r"\(.*?\)", "", name).strip()             # 'B2B Soft (standard)' → 'B2B Soft'
        words = [w for w in re.split(r"[^A-Za-z0-9]+", base) if w]
        squashed = "".join(words).lower()                       # 'b2bsoft' / 'rq'
        if squashed and squashed not in _GENERIC_WORDS:
            spellings.setdefault(squashed, origin)
        if len(words) > 1:
            spellings.setdefault(" ".join(w.lower() for w in words), origin)   # 'b2b soft'
            stem = words[0].lower()                                             # the brand stem 'b2b'
            if len(stem) >= 2 and stem not in _GENERIC_WORDS:
                spellings.setdefault(stem, origin)
    return spellings


def pos_regex(spellings):
    """One case-insensitive pattern over every spelling, whole-word; a space in a spelling tolerates
    '-' / '_' / nothing ('b2b soft' ~ 'B2B-Soft' ~ 'b2bsoft')."""
    alts = sorted({re.escape(sp).replace(r"\ ", r"[\s_-]*") for sp in spellings}, key=len, reverse=True)
    # identifier context (`g.b2b?.cash`, `recon.b2b_acc_gp`, `b2b_loaded`) is a FIELD NAME, not copy
    return re.compile(r"(?<![a-z0-9_.])(?:" + "|".join(alts) + r")(?![a-z0-9_.?])", re.I)


# ── POS REVIEWED EXCEPTIONS — (relative frontend file) -> reason. Only DATA homes: a connector id, a
#    stored category name. A stale entry (file no longer carries a spelling) FAILS, so this can only shrink.
POS_REVIEWED_EXCEPTIONS = {
    "app/(platform)/commcalc/upload/page.tsx": "AUTO_SOURCES connector id 'b2b' + its sweep route paths (data keys, rendered as 'POS portal')",
    "app/(platform)/commcalc/expenses/page.tsx": "'B2B Platform Fee' is a STORED expense-category name (tenant rows already carry it; renaming the default would split their history) + its matrix-upload alias key",
}

# ── BACKEND. LOGIC files may not name a vendor at all (folded from harness_report_kind_lock.py's
#    pos_vendor class); COPY files may not carry one in a whitespace-bearing string literal (a payload
#    `note` / `reason` / `message` / `detail`) — they call report_labels.pos_term. Allow = (file, a
#    signature substring of the literal) -> reason; stale FAILS.
POS_BACKEND_LOGIC = ["commcalc/report_kinds.py", "commcalc/onboarding_intake.py", "commcalc/implementation_spine.py",
                     "commcalc/invoice_tenders.py",     # 2026-09-21 — the invoice tender split (pure; no vendor, no brand)
                     "commcalc/connector_registry.py",  # 2026-09-21 — the connector registry: no vendor before its mirror marker
                     "pos/sales_from_reports.py",       # 2026-09-21 — sales rebuilt from the reports (the format is the DECLARED POS's, never a literal)
                     "pos/vendor_paid_lines.py",        # 2026-09-24 — the lines the vendor pays: learned category segments, no vendor, no carrier word
                     "pos/customer_identity.py",        # 2026-09-24 — who the customer is (§30.16): generic placeholder words, no vendor, no carrier
                     "pos/customer_master.py",          # 2026-09-24 — the customer's lines / merge / page payloads (§30.16): no vendor, no carrier
                     "pos/inventory_integrity.py",      # 2026-09-24 — inventory integrity flags / adjust reasons (§11b): generic stock words only
                     "pos/inventory_integrity_router.py",  # 2026-09-24 — its endpoints + the landing guard (§11b): no vendor, no carrier
                     "commcalc/inventory_sold_recon.py",   # 2026-09-24 — THE engine (+ the commission source, §11b): no vendor, no carrier
                     "core/plan_sources.py"]            # 2026-09-22 — the plan-name source registry + hint words (generic vocabulary; no vendor, no carrier)
POS_BACKEND_COPY = [
    "commcalc/router.py", "commcalc/sales_recon.py", "commcalc/imei_rebate_report.py", "commcalc/report_labels.py",
    "closing/router.py", "closing/attention_providers.py", "asset/router.py", "account/finance_attention.py",
    "core/onboarding.py",
    # 2026-09-21 — the #262 seam closed: the connector modules' error / status strings reach a page as a
    # connector's `last_detail` / `auth_message` / pull status, so they are PAYLOAD copy. They name the
    # connector by the registry row's label (connector_registry, mig 1014) passed in by the router.
    "commcalc/b2b_sweep.py", "commcalc/vidapay_sweep.py", "commcalc/import_audit.py",
    # the receipt formats: only their vocabulary DECLARATIONS may spell the POS (allowed below, pinned)
    "pos/receipt_formats/b2b.py", "pos/receipt_formats/rq.py", "pos/receipt_formats/base.py",
    "pos/receipt_formats/engine.py", "pos/receipt_formats/render.py", "pos/receipt_formats/registry.py",
]
POS_BACKEND_ALLOW = {
    # the receipt-format registry's vocabulary declarations — each format's LABEL is the name of the POS it
    # parses, keyed by POS_SOURCE (a picker data value, like a pos_profile label); not tenant-scoped copy
    ("pos/receipt_formats/b2b.py", "B2B (TCC / Verizon)"): "the format's own label — the POS this parser reads (picker data, keyed by POS_SOURCE)",
    ("pos/receipt_formats/rq.py", "RQ (Wireless Zone)"): "the format's own label — the POS this parser reads (picker data, keyed by POS_SOURCE)",
    ("commcalc/router.py", "B2B Soft (standard)"): "the mig-200 pos_profile seed mirrored in code (the house standard's own label — data for its OWN pos_key)",
    ("commcalc/router.py", "daily B2B sales export"): "mig-200 filename-rule note in the same mirror (data)",
    ("commcalc/router.py", "b2bsoft inventory aging"): "mig-200 filename-rule note in the same mirror (data)",
    ("commcalc/router.py", "B2B Soft wsreports"): "connector_vendor row written for the portal sweep (vendor_name is the row's key, data)",
    ("commcalc/router.py", "Upload the b2b Activation Details report"): "onboarding task rendered only when applies_when.pos matches that POS (data-conditional)",
    ("commcalc/router.py", "Upload the b2b Bill Payment Transactions report"): "onboarding task rendered only when applies_when.pos matches that POS (data-conditional)",
    ("commcalc/router.py", "Upload the b2b Store Performance scorecard"): "onboarding task rendered only when applies_when.pos matches that POS (data-conditional)",
    ("commcalc/router.py", "b2b soft"): "applies_when.pos tokens of those onboarding tasks (the gate's data value)",
    ("commcalc/router.py", "B2B Soft"): "the onboarding POS pick-list option + the connector_vendor row key written for the portal sweep (data values)",
}


_bare_lit_res = [re.compile(r"'((?:[^'\\]|\\.)*)'"), re.compile(r'"((?:[^"\\]|\\.)*)"'), re.compile(r"`([^`$]*)`")]


_prose_bad = re.compile(r"[<>=;'\"`]")


# EMPHASIS IS NOT CODE (defect found 2026-10-01, repaired as a class).
# `_prose_bad` rejects a line holding any of `< > = ; ' " \``, which is right for CODE and wrong for the
# commonest shape of real paragraph copy: a sentence that bolds or italicises one word. This line was on
# `main`, rendered to every reader, and the guard scored it GREEN:
#     bill-payment (ePay) cash taken that day. Bill-payment cash is a breakdown <i>inside</i> that figure,
# `<i>` tripped the reject, `display_segments`' `>text<` pattern only saw the word INSIDE the tag, and a
# cross-side carrier brand shipped. So inline emphasis and HTML entities are STRIPPED and the prose test
# applied to what remains — the line is read, not discarded. 1262 lines across the frontend became visible
# this way; they carry 0 new carrier and 0 new POS hits, and 11 pre-existing setup-internals hits that are
# registered as debt in SETUP_PROSE_DEBT rather than excused.
_INLINE_EMPHASIS = re.compile(r"</?(?:b|i|u|em|strong|small|br|code|span)\s*/?>")
_HTML_ENTITY = re.compile(r"&(?:apos|quot|amp|nbsp|mdash|ndash|rsquo|lsquo|hellip|#\d+);")


def jsx_prose_line(rel, ln):
    """A .tsx line that is JSX TEXT continuing a paragraph — no operator, no quote, only emphasis tags —
    is display copy the string/tag extractor cannot see ('No daily B2B feed loaded for {period} yet.')."""
    s = ln.strip()
    if not rel.endswith(".tsx") or s.startswith(("//", "*", "/*", "{/*")):
        return []
    s = _HTML_ENTITY.sub("", _INLINE_EMPHASIS.sub(" ", s))
    if " " not in s or _prose_bad.search(s):
        return []
    return [s]


def display_copy(rel, line):
    """THE one answer to "is this display copy?" — every scan in this file dereferences it.

    Three scans used to ask it three different ways: the POS and setup scans read string literals, tags
    AND prose; the cross-side CARRIER scan read only literals and tags, so a brand in a paragraph was
    invisible to the scan whose whole job is brands. One home, three callers (owner directive 2026-09-20:
    one fact, one home, dereferenced — never copied).
    """
    return display_segments(line) + jsx_prose_line(rel, line)


def pos_scan_frontend(files, rx, allow):
    """files: {rel: [lines]}. A spelling in a display segment anywhere, or a BARE literal equal to a
    spelling in app/ or components/ (the vendor-as-fallback case), fails outside `allow`. Returns
    (fails, stale)."""
    fails, seen = [], set()
    whole = re.compile(r"^\s*" + rx.pattern + r"\s*$", re.I)
    for rel, lines in sorted(files.items()):
        for i, (ln, is_cmt) in enumerate(comment_lines(lines), 1):
            if is_cmt:
                continue
            hit = None
            for seg in display_copy(rel, ln):
                m = rx.search(seg)
                if m:
                    hit = (m.group(0), seg[:110])
                    break
            if not hit and (rel.startswith("app/") or rel.startswith("components/")):
                for lrx in _bare_lit_res:
                    for lit in lrx.findall(ln):
                        if whole.match(lit):
                            hit = (lit, "bare literal " + repr(lit))
                            break
                    if hit:
                        break
            if not hit:
                continue
            seen.add(rel)
            if rel in allow:
                continue
            fails.append((rel, i, hit[0], hit[1]))
    stale = [k for k in allow if k not in seen]
    return fails, stale


_DOCSTRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')
_LOG_LINE = re.compile(r"\bprint\s*\(|\blog(?:ger)?\.(?:info|warning|warn|error|debug|exception)\s*\(")


def _py_code(src):
    """Python source with docstrings and # comments removed (they are prose, not copy)."""
    src = _DOCSTRING.sub('""', src)
    return "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))


def _strip_braces(text):
    """'{a} vs {f((b or {}))}' → '{} vs {}' — brace-depth aware, so a nested expression is dropped whole."""
    out, depth = [], 0
    for ch in text:
        if ch == "{":
            if depth == 0:
                out.append("{")
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append("}")
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _py_string_literals(src):
    """The STRING literals of a Python source (tokenize — never a regex across lines), excluding
    docstrings (a STRING that is a whole statement) and the strings of print()/log lines (operator
    output, not tenant-facing copy). f-strings yield their literal parts. Yields (line_no, text)."""
    import io
    import tokenize
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return out
    lines = src.split("\n")
    prev_sig = None
    for t in toks:
        if t.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
            if t.type == tokenize.NEWLINE:
                prev_sig = None
            continue
        text = None
        if t.type == tokenize.STRING:
            if prev_sig is None and t.string.lstrip("rRbBuUfF").startswith(('"""', "\'\'\'")):
                prev_sig = t; continue                       # a docstring
            body = t.string.lstrip("rRbBuUfF")
            text = body[3:-3] if body[:3] in ('"""', "\'\'\'") else body[1:-1]
            if "f" in t.string[:2].lower():                  # an f-string's {expressions} are code, not copy
                text = _strip_braces(text)
        elif getattr(tokenize, "FSTRING_MIDDLE", None) is not None and t.type == tokenize.FSTRING_MIDDLE:
            text = t.string
        if text is not None:
            ln = lines[t.start[0] - 1] if t.start[0] - 1 < len(lines) else ""
            if not _LOG_LINE.search(ln):
                out.append((t.start[0], text))
        prev_sig = t
    return out


def pos_scan_backend(sources, rx, allow, logic=POS_BACKEND_LOGIC, copy=POS_BACKEND_COPY):
    """sources: {rel: src}. LOGIC files: no spelling in code at all (the registry mirror's data rows
    after HOUSE_KEYS excepted; onboarding_intake's string keys blanked). COPY files: no spelling inside
    a whitespace-bearing string literal outside `allow`. Returns (fails, stale)."""
    fails, seen = [], set()
    for rel in logic:
        body = _py_code(sources.get(rel, ""))
        if rel.endswith("report_kinds.py") and "HOUSE_KEYS = " in body:
            body = body.split("HOUSE_KEYS = ", 1)[1]
        if rel.endswith("connector_registry.py") and "HOUSE_CONNECTORS = [" in body:
            # the seed's mirror is DATA (vendor labels, hosts, POS codes); the logic after its marker is not
            body = body.split("HOUSE_CONNECTORS = [", 1)[0] + body.split("HOUSE_CONNECTOR_KEYS = ", 1)[1]
        if rel.endswith("onboarding_intake.py"):
            body = re.sub(r'"[^"\n]*"', '""', body)
        m = rx.search(body)
        if m:
            fails.append((rel, "logic", m.group(0), body[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")))
    for rel in copy:
        for ln, lit in _py_string_literals(sources.get(rel, "")):
            if " " not in lit or not rx.search(lit):
                continue
            key = next((k for k in allow if k[0] == rel and k[1] in lit), None)
            if key:
                seen.add(key)
                continue
            fails.append((rel + ":" + str(ln), "copy", rx.search(lit).group(0), lit[:110]))
    stale = [k for k in allow if k not in seen]
    return fails, stale


_page_re = re.compile(r"app/\(platform\)(/.*)/page\.tsx$")


def nav_carriers():
    src = open(RBAC, encoding="utf-8").read()
    m = re.search(r"NAV_CARRIERS: Record<string, string\[\]> = \{(.*?)\n\}", src, re.S)
    out = {}
    for href, arr in re.findall(r"'(/[^']+)':\s*\[([^\]]*)\]", m.group(1)):
        out[href] = re.findall(r"'([^']+)'", arr)
    return out


def file_gate(rel, nav):
    m = _page_re.search(rel)
    if not m:
        return None
    return nav.get(m.group(1))


def comment_lines(lines):
    """Yield (line, is_comment) with a crude //, /* */, {/* */} tracker."""
    inblock = False
    for ln in lines:
        s = ln.strip()
        if inblock:
            yield ln, True
            if "*/" in s:
                inblock = False
            continue
        if s.startswith("//") or s.startswith("*"):
            yield ln, True
        elif s.startswith("/*") or s.startswith("{/*"):
            yield ln, True
            if "*/" not in s:
                inblock = True
        else:
            yield ln, False


_seg_res = [re.compile(r"'((?:[^'\\]|\\.)*)'"), re.compile(r'"((?:[^"\\]|\\.)*)"'),
            re.compile(r"`((?:[^`\\]|\\.)*)`"), re.compile(r">([^<>]+)<"),
            re.compile(r">([^<>{}\"']+)$")]


def display_segments(line):
    """String-literal + JSX-text segments that look like display copy (has whitespace, no path)."""
    out = []
    for rx in _seg_res:
        for seg in rx.findall(line):
            seg = seg.strip()
            if not seg or " " not in seg:      # pure tokens/ids/urls are not display copy
                continue
            if "/" in seg and seg.split()[0].count("/") > 1:  # path-ish
                continue
            out.append(seg)
    return out


def extractor_controls():
    """ARMED CONTROLS for display_copy — the one answer to "is this display copy?" (added 2026-10-01).

    Every rule here failed before the extractor was unified and widened, which is the only reason to
    believe the widening is real. E1 is the line that actually shipped a cross-side brand to production.
    """
    print("\n— the display-copy extractor (one home: display_copy; §19.36 siblings) —")
    ok = True
    page = "app/(platform)/closing/envelope-report/page.tsx"
    # The line as it stood on main, rendered to every reader, scored GREEN by the old extractor:
    shipped = "            bill-payment (ePay) cash taken that day. Bill-payment cash is a breakdown <i>inside</i> that figure,"
    fixed = "            bill-payment cash taken that day. Bill-payment cash is a breakdown <i>inside</i> that figure,"
    hit = lambda ln: any(BOOST_TERMS.search(seg) or TOTAL_TERMS.search(seg) for seg in display_copy(page, ln))
    ok &= _ctl("E1 the brand in an <i>-bearing paragraph is SEEN → RED (the shipped defect reproduced)", hit(shipped))
    ok &= _ctl("E2 …the same sentence with the brand removed → GREEN", not hit(fixed))
    ok &= _ctl("E3 a brand behind <b>…</b> and an &apos; entity is SEEN → RED",
               hit("            the rep&apos;s <b>VidaPay</b> drawer total for that day,"))
    # Widening must not start reading CODE as copy — these are the reasons _prose_bad exists.
    ok &= _ctl("E4 a code line is still not prose (an operator / a quote / a tag with attributes)",
               not display_copy(page, "  const label = cfg.wide ? 'a' : 'b'")
               and not display_copy(page, '  <div className="row" onClick={go}>'))
    ok &= _ctl("E5 a comment is still prose, not copy → GREEN",
               not jsx_prose_line(page, "            // the ePay drawer is the whole drawer"))
    ok &= _ctl("E6 a .ts (non-JSX) file contributes no prose lines",
               not jsx_prose_line("lib/report-labels.ts", "            the ePay drawer is the whole drawer"))
    # The wiring itself: a scan that stops dereferencing display_copy un-does all of the above.
    # THIS function's own text is cut out first — it quotes the very strings it is looking for, and a
    # rule that matches its own source is a rule that cannot fail (the self-reference trap that already
    # bit harness_envelope_receipt_basis and harness_screen_link_guard).
    raw = open(os.path.abspath(__file__), encoding="utf-8").read()
    # Anchored to the START of a line: the quoted copies of these very def lines inside this function are
    # INDENTED, so an unanchored .index() finds one of them and cuts in the wrong place (it did, first try).
    _at = lambda name: re.search(r"^def %s\(\):" % name, raw, re.M).start()
    src = raw[:_at("extractor_controls")] + raw[_at("main"):]
    body = src[re.search(r"^def main\(\):", src, re.M).start():]
    ok &= _ctl("E7 the cross-side carrier scan dereferences display_copy (not display_segments alone)",
               "for seg in display_copy(rel, ln):" in body and "for seg in display_segments(ln):" not in src)
    ok &= _ctl("E8 all three scans dereference it — one home, three callers",
               src.count("for seg in display_copy(") == 3)
    ok &= _ctl("E8b …and the cut-out is honest (this function's own text really was excluded)",
               "def extractor_controls():" not in src and len(src) < len(raw))
    # The debt ledger is a RATCHET: over the pin is a new defect, under the pin must be paid down.
    mk = lambda rel, n: [(rel, i, "Migration", "Migration 1 hasnt run") for i in range(n)]
    one = {"a.tsx": 1}
    ok &= _ctl("E9 a file over its pin → RED (a NEW tenant-facing migration sentence)",
               len(split_setup_debt(mk("a.tsx", 2), one)[0]) == 1)
    ok &= _ctl("E10 a file at its pin → carried as debt, not as a pass",
               split_setup_debt(mk("a.tsx", 1), one)[0] == [] and len(split_setup_debt(mk("a.tsx", 1), one)[1]) == 1)
    ok &= _ctl("E11 a file UNDER its pin → RED (lower the pin; this is what forces the list to shrink)",
               split_setup_debt(mk("a.tsx", 0), one)[2] == [("a.tsx", 1, 0)])
    ok &= _ctl("E12 an unpinned file's hit is a real fail, never silent debt",
               len(split_setup_debt(mk("b.tsx", 1), one)[0]) == 1)
    print("  OK — the display-copy extractor holds." if ok else "  FAIL — the display-copy extractor is not sound.")
    return ok


def main():
    nav = nav_carriers()
    fails = []
    scanned = 0
    for dirpath, _dirs, files in os.walk(FE):
        for f in files:
            if not f.endswith((".ts", ".tsx")):
                continue
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, FE)
            scanned += 1
            gate = file_gate(rel.replace(os.sep, "/"), nav) or []
            exc = REVIEWED_EXCEPTIONS.get(rel.replace(os.sep, "/"), {})
            lines = open(path, encoding="utf-8").read().splitlines()
            for i, (ln, is_cmt) in enumerate(comment_lines(lines), 1):
                if is_cmt:
                    continue
                # display_copy, not display_segments: a brand in a <b>-bearing paragraph was invisible
                # to this scan until 2026-10-01 (see jsx_prose_line).
                for seg in display_copy(rel, ln):
                    for side, rx in (("total", TOTAL_TERMS), ("boost", BOOST_TERMS)):
                        mm = rx.search(seg)
                        if not mm:
                            continue
                        if side in gate:               # page gated to this term's own side — allowed
                            continue
                        if "both" in exc or side in exc:
                            continue
                        fails.append((rel, i, side, mm.group(0), seg[:110]))
    print(f"scanned {scanned} frontend files; NAV gates: "
          f"{sum(1 for v in nav.values() if v)} hrefs; exceptions pinned: {len(REVIEWED_EXCEPTIONS)}")
    bad = False
    if fails:
        bad = True
        print(f"\nFAIL — {len(fails)} hardcoded cross-side carrier term(s) in display copy:")
        for rel, i, side, term, seg in fails:
            print(f"  {rel}:{i}  [{side}:{term}]  {seg}")
        print("\nFix: resolve the name via useReportLabels().term() (preset data, mig 953), reword "
              "neutrally, or carrier-gate the page in NAV_CARRIERS — see this file's docstring.")
    else:
        print("OK — no hardcoded cross-side carrier vocabulary in rendered frontend copy.")
    if not extractor_controls():
        bad = True
    if not pos_guard():
        bad = True
    if not setup_guard():
        bad = True
    sys.exit(1 if bad else 0)


def _fe_files():
    out = {}
    for dirpath, _dirs, files in os.walk(FE):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                path = os.path.join(dirpath, f)
                out[os.path.relpath(path, FE).replace(os.sep, "/")] = open(path, encoding="utf-8").read().splitlines()
    return out


def _be_sources():
    out = {}
    for rel in POS_BACKEND_LOGIC + POS_BACKEND_COPY:
        p = os.path.join(BE, rel)
        out[rel] = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    return out


def _ctl(label, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    return bool(cond)


def pos_guard():
    """THE POS LOCK. Returns True when green; prints its own report."""
    print("\n— POS vendor vocabulary (derived from the seeds; owner 2026-09-20) —")
    vocab = pos_vocabulary()
    rx = pos_regex(vocab)
    ok = True
    print("  spellings: " + ", ".join(f"{k} ← {v}" for k, v in sorted(vocab.items())))
    if not vocab or not rx.search("b2bsoft") or not rx.search("RQ"):
        print("  FAIL  the derived vocabulary must cover the seeds' own values (b2bsoft, RQ) — is a seed regex stale?")
        ok = False
    fe = _fe_files()
    fails, stale = pos_scan_frontend(fe, rx, POS_REVIEWED_EXCEPTIONS)
    if fails:
        ok = False
        print(f"  FAIL  {len(fails)} POS vendor name(s) in frontend copy / bare literals:")
        for rel, i, term, seg in fails:
            print(f"        {rel}:{i}  [{term}]  {seg}")
        print("        Fix: usePosTerm().pos (lib/report-labels.ts) — the declared POS, else the registry's neutral noun.")
    else:
        print(f"  OK    no POS vendor name in frontend copy; exceptions pinned: {len(POS_REVIEWED_EXCEPTIONS)}")
    if stale:
        ok = False
        print(f"  FAIL  stale POS exception(s) (file no longer carries a spelling — remove them): {stale}")
    be = _be_sources()
    bfails, bstale = pos_scan_backend(be, rx, POS_BACKEND_ALLOW)
    if bfails:
        ok = False
        print(f"  FAIL  {len(bfails)} POS vendor name(s) in backend logic / payload copy:")
        for rel, cls, term, seg in bfails:
            print(f"        {rel}  [{cls}:{term}]  {seg}")
        print("        Fix: report_labels.pos_term(client, org_id) in copy; registry data for logic.")
    else:
        print(f"  OK    backend: no vendor in registry/intake/spine logic, none in payload copy outside {len(POS_BACKEND_ALLOW)} allowed data literals")
    if bstale:
        ok = False
        print(f"  FAIL  stale backend allow entr(ies): {bstale}")

    # negative controls — a lock that cannot go red proves nothing
    print("  — negative controls —")
    page = "app/(platform)/commcalc/sales-recon/page.tsx"
    base = fe.get(page, [])
    f1, _ = pos_scan_frontend({page: base + ["          No daily B2B feed loaded for {period} yet."]}, rx, POS_REVIEWED_EXCEPTIONS)
    ok &= _ctl("reintroduce a vendor name in a page's copy → RED", bool(f1))
    f2, _ = pos_scan_frontend({page: base + ["  const label = term('pos_system', 'b2bsoft')"]}, rx, POS_REVIEWED_EXCEPTIONS)
    ok &= _ctl("a page reading the term but spelling the vendor as its fallback → RED", bool(f2))
    f0, _ = pos_scan_frontend({page: base}, rx, POS_REVIEWED_EXCEPTIONS)
    f3, _ = pos_scan_frontend({page: base + ["  const label = term('pos_system', 'POS')", "  const t = `the daily ${pos} feed`"]}, rx, POS_REVIEWED_EXCEPTIONS)
    ok &= _ctl("the neutral fallback / the term in a template → GREEN", len(f3) == len(f0))
    f4, _ = pos_scan_frontend({page: base + ["  // the daily b2bsoft feed (a comment is prose, not copy)"]}, rx, POS_REVIEWED_EXCEPTIONS)
    ok &= _ctl("a vendor name in a comment → GREEN", len(f4) == len(f0))
    _, s5 = pos_scan_frontend({page: base}, rx, {**POS_REVIEWED_EXCEPTIONS, page: "stale on purpose"})
    ok &= _ctl("a stale POS exception → RED", page in s5)
    bctl = dict(be)
    bctl["commcalc/sales_recon.py"] = be.get("commcalc/sales_recon.py", "") + '\nX = "is in the daily B2B feed"\n'
    f6, _ = pos_scan_backend(bctl, rx, POS_BACKEND_ALLOW)
    ok &= _ctl("a vendor name in a backend payload string → RED", any(c == "copy" for _, c, _, _ in f6))
    bctl = dict(be)
    bctl["commcalc/implementation_spine.py"] = be.get("commcalc/implementation_spine.py", "") + "\nPOS = 'b2bsoft'\n"
    f7, _ = pos_scan_backend(bctl, rx, POS_BACKEND_ALLOW)
    ok &= _ctl("a vendor name in registry/intake/spine logic → RED (the folded #257 class)", any(c == "logic" for _, c, _, _ in f7))
    bctl = dict(be)
    bctl["commcalc/sales_recon.py"] = be.get("commcalc/sales_recon.py", "") + '\nX = f"is in the daily {pos} feed"\n'
    f8, _ = pos_scan_backend(bctl, rx, POS_BACKEND_ALLOW)
    ok &= _ctl("the term in a backend f-string → GREEN", len(f8) == len(bfails))
    _, s9 = pos_scan_backend(be, rx, {**POS_BACKEND_ALLOW, ("commcalc/sales_recon.py", "nowhere"): "stale on purpose"})
    ok &= _ctl("a stale backend allow entry → RED", ("commcalc/sales_recon.py", "nowhere") in s9)
    print("  " + ("OK — the POS vocabulary lock holds." if ok else "FAIL — the POS vocabulary lock is open."))
    return ok


# ══ SETUP INTERNALS — no migration / SQL-editor wording in customer-facing copy (owner 2026-09-29, §19.36) ══
# Owner: Display Labels said "Needs migration 068_ui_label_override.sql" — "this migration should not be
# mentioned in customer facing". THE CLASS: an internal build/setup fact (a migration file or number, "apply
# mig", "run it in the Supabase SQL editor", a table in a "not applied" hint, PostgREST's own not-applied
# errors) reaching rendered copy or an API response. ONE HOME per side, dereferenced:
#   · backend  app/core/setup_notice.py — SETUP_NOTICE, the detector SETUP_INTERNAL, `neutralize`, and the
#     SetupNoticeMiddleware boundary every JSON response crosses (+ report_registry.build_payload for mail);
#   · frontend lib/setupNotice.tsx — SETUP_NOTICE (locked equal), <SetupNotice detail=…/> (the detail shown to
#     the platform super admin only), setupFailed().
# This lock fails the build when: rendered frontend copy carries the wording outside the home and the named
# super-admin pages below; a backend string literal names a migration in a shape the boundary's detector
# would NOT catch (so nothing can slip past it); the middleware is unregistered or not innermost; the report
# builder stops dereferencing `neutralize`; the gate is re-derived; the two sentences drift. The boundary's
# behaviour is proved here too, DB-free on a hand-driven ASGI app. Negative controls prove each rule goes red.
SETUP_FE_HOME = "lib/setupNotice.tsx"
SETUP_BE_HOME = os.path.join(ROOT, "backend", "app", "core", "setup_notice.py")
SETUP_MAIN = os.path.join(ROOT, "backend", "app", "main.py")
SETUP_REPORTS = os.path.join(ROOT, "backend", "app", "modules", "notify", "report_registry.py")
BE_APP = os.path.join(ROOT, "backend", "app")
SETUP_FE_WORDS = re.compile(r"\bmigrations?\b|(?<![\w-])mig\s*#?\d|\bSQL\s+editor\b|(?-i:\bSupabase\b)", re.I)
SETUP_SQL_NAME = re.compile(r"\b\d{3,4}[a-z]?_[a-z0-9_]+\.sql\b", re.I)
# A page the platform super admin ALONE can open may keep technical detail. Each entry is VERIFIED below: its
# href must be platform-only at every NAV occurrence (rbac.PLATFORM_ONLY_HREFS — the route guard bounces anyone
# else — and its endpoints sit behind core.router._require_super_admin). A stale entry FAILS.
SETUP_SUPER_ADMIN_PAGES = {
    "app/(platform)/admin/billing/page.tsx": "/admin/billing — tenant billing plans + platform costs (vendor names are the data)",
    "app/(platform)/admin/pricing/page.tsx": "/admin/pricing — platform price list + free trial",
    "app/(platform)/admin/access-log/page.tsx": "/admin/access-log — the platform's system access log",
    "app/(platform)/admin/control-box/page.tsx": "/admin/control-box — the platform red/green board (§20); cron migrations are its subject",
    "app/(platform)/admin/fix-requests/page.tsx": "/admin/fix-requests — the auto-fix pipeline; SQL / migrations are its work product",
    "app/(platform)/admin/business-types/page.tsx": "/admin/business-types — the platform's vertical registry editor (§35)",
}
# The operator console route group: its layout asks the SERVER (`loadOperatorMe` → GET /core/operator/me) and
# renders nothing but an explanation for a non-operator. Verified below.
SETUP_OPERATOR_TREE = "app/(operator)/"
SETUP_OPERATOR_LAYOUT = "app/(operator)/operator/layout.tsx"
# Backend strings that name a migration in a shape the detector does not catch — each reviewed: not a setup
# hint a TENANT reads. (file relative to backend/app, a signature substring) -> reason. Stale FAILS.
SETUP_BACKEND_ALLOW = {
    ("modules/billing/platform_costs.py", "Supabase (database)"):
        "a vendor NAME in the platform's own cost list (served to /admin/billing, super-admin only), not a setup hint",
    ("modules/commcalc/connector_registry.py", "its pull route is closed by mig 998"):
        "the mig-1014 connector seed mirrored in code (harness_connector_scope_lock pins mirror == seed); the live "
        "text is DB data — rewording it is a data migration, surfaced for the owner (§19.36)",
    ("modules/commcalc/connector_registry.py", "report kinds carry in mig 1010"):
        "the mig-1014 connector seed mirror (two rows) — same reason as above",
    ("modules/core/control_box_api.py", " / mig 9"):
        "the System Control Box's index references (§20) — served by a _require_super_admin endpoint only",
    ("modules/core/control_box_api.py", "the failure mig 950 found by accident"):
        "a Control Box check's note — super-admin only (§20)",
    ("modules/core/operator.py", "keep the migration 984 rollback SQL to hand"):
        "the operator console's policy warning — the operator console only (§22)",
}
# WHERE a setup-hint string may be emitted. The boundary reads ONLY setup_notice.MESSAGE_KEYS (never data);
# a hint emitted under any other constant key reaches the customer verbatim, so it FAILS unless reviewed here:
# (file relative to backend/app, key) -> reason. Stale FAILS.
SETUP_KEY_LEDGER = {
    ("modules/commcalc/landing_identity.py", "raw_sales_product"):
        "TABLE_MIGRATION — a lookup (landing table -> the migration that creates it); its value reaches a client "
        "only inside a refusal `detail`, which the boundary reads",
    ("modules/commcalc/landing_identity.py", "raw_sales_invoice"): "TABLE_MIGRATION lookup — as above",
    ("modules/commcalc/landing_identity.py", "raw_sales_invoice_tender"): "TABLE_MIGRATION lookup — as above",
}

_SANCTIONED = re.compile(r"""\bdetail\s*=\s*\{[^{}]*\}|\bdetail\s*[=:]\s*(?:'[^']*'|"[^"]*"|`[^`$]*`)""")
_INLINE_CMT = re.compile(r"\{/\*.*?\*/\}|/\*.*?\*/")
_TRAIL_CMT = re.compile(r"(^|\s)//.*$")


def _setup_code(ln):
    """A code line with its inline / trailing comments removed and the sanctioned `detail=` carrier blanked."""
    ln = _TRAIL_CMT.sub(r"\1", _INLINE_CMT.sub("", ln))
    return _SANCTIONED.sub('detail=""', ln)


def platform_only_hrefs():
    """The hrefs every NAV occurrence of which is platform-only — rbac.PLATFORM_ONLY_HREFS, read statically."""
    src = open(RBAC, encoding="utf-8").read()
    body = src[src.index("export const NAV: NavGroup[] = ["):]
    body = body[:body.index("\n]\n")]
    heads = list(re.finditer(r"^  \{ group: '([^']+)'([^\n]*)$", body, re.M))
    occ = {}
    for n, m in enumerate(heads):
        chunk = body[m.end():heads[n + 1].start() if n + 1 < len(heads) else len(body)]
        for it in re.findall(r"^\s*(\{ href: '[^\n]*\}),?\s*$", chunk, re.M):
            h = re.search(r"href: '([^']*)'", it).group(1)
            occ.setdefault(h, []).append("platformOnly: true" in m.group(2) or "platformOnly: true" in it)
    return {h for h, v in occ.items() if v and all(v)}


# ── REAL, PRE-EXISTING SETUP-INTERNALS COPY — DEBT, NOT AN EXCUSAL (measured 2026-10-01) ──────────
# When display_copy started reading <b>-bearing paragraphs (see jsx_prose_line), 11 sentences that had
# always been rendered to TENANTS became visible — each one telling a customer to run a migration:
#   "ℹ️ Migration 421 hasnt run on this tenant yet — Save will fail until it does."
# These are the §19.36 defect the owner reported ("Display Labels"), in pages nobody had converted to
# <SetupNotice>. They are NOT excused: SETUP_SUPER_ADMIN_PAGES means "a platform admin is the only
# reader, so the detail is allowed", and these are tenant pages. They are PINNED instead, by exact
# count per file, so that:
#   · a NEW one anywhere fails the build (the count goes up → FAIL);
#   · fixing one fails the build until the pin is lowered (the count goes down → FAIL, "lower the pin"),
#     which is what makes this a ratchet and not a permanent excusal;
#   · the list can only ever shrink to {}.
# Remedy for each, when it is taken on: render the tenant sentence through lib/setupNotice.tsx and let
# the super-admin detail ride `<SetupNotice detail=…/>`, exactly as N3 proves GREEN.
SETUP_PROSE_DEBT = {
    "app/(platform)/admin/carrier-documents/page.tsx": 1,
    "app/(platform)/admin/training/page.tsx": 1,
    "app/(platform)/admin/whats-new/page.tsx": 1,
    "app/(platform)/commcalc/accessory-definition/page.tsx": 1,
    "app/(platform)/commcalc/commission-category-map/page.tsx": 1,
    "app/(platform)/commcalc/commission-ledger/page.tsx": 1,
    "app/(platform)/commcalc/commission-plans/page.tsx": 1,
    "app/(platform)/commcalc/ma-class-wiring/page.tsx": 1,
    "app/(platform)/storeops/attendance/page.tsx": 1,
    "app/(platform)/storeops/timeclock/page.tsx": 2,
}


def split_setup_debt(fails, debt=None):
    """(fails, debt) → (real_fails, debt_hits, ledger_problems) against the pinned per-file counts.

    A file over its pin contributes the EXCESS to real_fails (a new defect). A file under its pin, or a
    pinned file with no hits at all, is a ledger problem — the pin must be lowered, which is how the
    list is forced to shrink as the pages are converted.
    """
    debt = SETUP_PROSE_DEBT if debt is None else debt
    per = {}
    for f in fails:
        per.setdefault(f[0], []).append(f)
    real, taken, problems = [], [], []
    for rel, hits in sorted(per.items()):
        pin = debt.get(rel, 0)
        taken.extend(hits[:pin])
        real.extend(hits[pin:])
    for rel, pin in sorted(debt.items()):
        got = len(per.get(rel, []))
        if got < pin:
            problems.append((rel, pin, got))
    return real, taken, problems


def setup_scan_frontend(files, detector, excused=SETUP_SUPER_ADMIN_PAGES):
    """files: {rel: [lines]}. Returns (fails, seen_excused). A `NNN_name.sql` anywhere in non-comment code, or
    migration / SQL-editor wording (or anything the backend detector recognises) in a DISPLAY segment, FAILS
    outside the home, the operator tree and the excused super-admin pages."""
    fails, seen = [], set()
    for rel, lines in sorted(files.items()):
        if rel == SETUP_FE_HOME:
            continue
        for i, (ln, is_cmt) in enumerate(comment_lines(lines), 1):
            if is_cmt:
                continue
            code = _setup_code(ln)
            hit = None
            m = SETUP_SQL_NAME.search(code)
            if m:
                hit = (m.group(0), code.strip()[:110])
            else:
                for seg in display_copy(rel, code):
                    m = detector.search(seg) or SETUP_FE_WORDS.search(seg)
                    if m:
                        hit = (m.group(0), seg[:110])
                        break
            if not hit:
                continue
            if rel in excused or rel.startswith(SETUP_OPERATOR_TREE):
                seen.add(rel)
                continue
            fails.append((rel, i, hit[0], hit[1]))
    return fails, seen


_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%[sdr]")


def _py_joined_literals(src):
    """(line, text) per STRING EXPRESSION of a Python source: implicitly concatenated literals JOINED (the text
    the client receives), docstrings and print()/log lines skipped, every {placeholder} / %s read as '000' (the
    value interpolated after "migration" is a migration id or file)."""
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    lines = src.split("\n")
    out, run, state = [], [], {"line": None}

    def flush():
        if run:
            out.append((state["line"], "".join(run)))
        run.clear()

    prev_sig = None
    for t in toks:
        if t.type in (tokenize.NL, tokenize.COMMENT):
            continue
        if t.type == tokenize.STRING:
            body = t.string.lstrip("rRbBuUfF")
            docstring = prev_sig is None and not run and body[:3] in ('"""', "'''")
            text = body[3:-3] if body[:3] in ('"""', "'''") else body[1:-1]
            if "f" in t.string[:2].lower():
                text = _strip_braces(text)
            ln = lines[t.start[0] - 1] if t.start[0] - 1 < len(lines) else ""
            if docstring or _LOG_LINE.search(ln):
                prev_sig = t
                continue
            if not run:
                state["line"] = t.start[0]
            run.append(_PLACEHOLDER.sub("000", text))
            prev_sig = t
            continue
        flush()
        prev_sig = None if t.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT) else t
    flush()
    return out


def setup_scan_backend(sources, detector, allow=SETUP_BACKEND_ALLOW):
    """sources: {rel: src}. Every string expression that names a migration (the frontend words, or a .sql file)
    must be one the boundary's detector recognises — otherwise it would reach a customer verbatim. A bare
    one-word literal ('migration' as a dict KEY) is not copy. Returns (fails, stale)."""
    fails, seen = [], set()
    for rel, src in sorted(sources.items()):
        for ln, text in _py_joined_literals(src):
            if " " not in text.strip() and not SETUP_SQL_NAME.search(text):
                continue
            if not (SETUP_FE_WORDS.search(text) or SETUP_SQL_NAME.search(text)):
                continue
            if detector.search(text):
                continue
            key = next((k for k in allow if k[0] == rel and k[1] in text), None)
            if key:
                seen.add(key)
                continue
            fails.append((rel + ":" + str(ln), text[:120]))
    return fails, [k for k in allow if k not in seen]


_EMIT_PH = re.compile(r"\{[^{}]*\}|%[sdr]")


def _str_text(node):
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "000" for v in node.values)
    return None


def setup_emissions(src, detector):
    """[(line, text, key)] — every setup-hint string (the detector's verdict) emitted DIRECTLY under a constant
    KEY: a dict entry `{"k": …}`, a subscript assignment `x["k"] = …`, a keyword argument `k=…`, or — one hop —
    a `NAME = …` / `NAME.append(…)` whose NAME is then used so (a module constant anywhere in the module, a local
    within its function). An HTTPException argument or a raised error's message is `detail`. A helper's
    positional argument or a return value is not a key and is not judged — the runtime reads message keys only."""
    import ast
    import collections
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    par = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            par[c] = n

    def scope_of(node):
        a = par.get(node)
        while a is not None and not isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            a = par.get(a)
        return a if a is not None else tree

    loads = collections.defaultdict(list)
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            loads[(id(scope_of(n)), n.id)].append(n)
            loads[("module", n.id)].append(n)

    def uses(tg):
        sc = scope_of(tg)
        return loads.get(("module", tg.id), []) if isinstance(sc, ast.Module) else loads.get((id(sc), tg.id), [])

    build = (ast.BinOp, ast.BoolOp, ast.IfExp, ast.FormattedValue, ast.JoinedStr)

    def up(node):
        cur, p = node, par.get(node)
        while isinstance(p, build) or (isinstance(p, ast.Attribute) and p.attr == "format") \
                or (isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr == "format"
                    and cur is p.func):
            cur, p = p, par.get(p)
        return cur, p

    def exc_call(call):
        fn = getattr(call, "func", None)
        nm = getattr(fn, "id", None) or getattr(fn, "attr", None) or ""
        return nm == "HTTPException" or nm.endswith(("Error", "Exception"))

    def keys_at(node, hop):
        cur, p = up(node)
        if isinstance(p, ast.Dict):
            i = next((i for i, v in enumerate(p.values) if v is cur), None)
            if i is not None and isinstance(p.keys[i], ast.Constant):
                return [str(p.keys[i].value)]
            return []
        if isinstance(p, ast.keyword) and p.arg:
            return ["detail"] if exc_call(par.get(p)) else [p.arg]
        if isinstance(p, ast.Call) and exc_call(p) and cur in p.args:
            return ["detail"]
        if isinstance(p, (ast.Assign, ast.AnnAssign)):
            out = []
            for tg in (p.targets if isinstance(p, ast.Assign) else [p.target]):
                if isinstance(tg, ast.Subscript) and isinstance(tg.slice, ast.Constant):
                    out.append(str(tg.slice.value))
                elif isinstance(tg, ast.Name) and hop < 1:
                    for u in uses(tg):
                        out += keys_at(u, hop + 1)
            return out
        if isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr == "append" and hop < 1:
            t = p.func.value
            if isinstance(t, ast.Name):
                out = []
                for u in uses(t):
                    out += keys_at(u, hop + 1)
                return out
            if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant):
                return [str(t.slice.value)]
        return []

    out = []
    for n in ast.walk(tree):
        t = _str_text(n)
        if t is None or isinstance(par.get(n), ast.JoinedStr):
            continue
        if not detector.search(_EMIT_PH.sub("000", t)):
            continue
        for k in sorted(set(keys_at(n, 0))):
            out.append((n.lineno, t, k))
    return out


def setup_scan_keys(sources, detector, is_message_key, ledger=SETUP_KEY_LEDGER):
    """Every setup hint the backend emits under a constant key must be under a MESSAGE key (the only values
    the boundary reads) or be reviewed in `ledger`. Returns (fails, stale, measured_keys)."""
    fails, seen, measured = [], set(), {}
    for rel, src in sorted(sources.items()):
        for ln, text, key in setup_emissions(src, detector):
            measured[key] = measured.get(key, 0) + 1
            if is_message_key(key):
                continue
            if (rel, key) in ledger:
                seen.add((rel, key))
                continue
            fails.append((f"{rel}:{ln}", key, text[:100]))
    return fails, [k for k in ledger if k not in seen], measured


def _be_app_sources():
    out = {}
    for dp, _d, fs in os.walk(BE_APP):
        for f in fs:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                if os.path.abspath(p) == os.path.abspath(SETUP_BE_HOME):
                    continue                          # the home's own detector patterns
                out[os.path.relpath(p, BE_APP).replace(os.sep, "/")] = open(p, encoding="utf-8").read()
    return out


def setup_wiring(main_src, reports_src, home_src, fe_home_src):
    """The wires that keep the design from un-wiring. Returns [(label, ok)]."""
    out = []
    i_mw = main_src.find("app.add_middleware(SetupNoticeMiddleware)")
    i_gz = main_src.find("app.add_middleware(GZipMiddleware")
    out.append(("W1 the boundary is registered in main.py, INNERMOST (before GZip — it reads the plain JSON body)",
                i_mw != -1 and i_gz != -1 and i_mw < i_gz))
    m = re.search(r"async def build_payload\(.*?(?=\n(?:async )?def |\Z)", reports_src, re.S)
    out.append(("W2 the outbound report builder (report_registry.build_payload) dereferences setup_notice.neutralize",
                bool(m and re.search(r"\bneutralize\(", _py_code(m.group(0))))))
    code = _py_code(home_src)
    out.append(("W3 the super-admin answer is THE one gate (core.router._require_super_admin), not a re-derived rung",
                "import _require_super_admin" in code and "_platform_admin_rungs" not in code
                and not re.search(r"""\[\s*["']super_admin["']\s*\]|\.get\(\s*["']super_admin""", code)))
    be = re.search(r'^SETUP_NOTICE\s*=\s*"([^"]+)"', home_src, re.M)
    fe = re.search(r'export const SETUP_NOTICE\s*=\s*"([^"]+)"', fe_home_src)
    out.append(("W4 the customer sentence has one wording (frontend SETUP_NOTICE == backend SETUP_NOTICE)",
                bool(be and fe and be.group(1) == fe.group(1))))
    out.append(("W5 the frontend detail is gated by rbac.isPlatformAdmin (the one super-admin predicate)",
                "isPlatformAdmin(" in fe_home_src and "from './rbac'" in fe_home_src))
    return out


def _asgi_run(mw_cls, body_msgs, ctype=b"application/json", path="/api/v1/x", headers=()):
    """Drive a pure-ASGI middleware around a stub app that sends `body_msgs`; return (start, body)."""
    import asyncio
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 400,
                    "headers": [(b"content-type", ctype), (b"content-length", str(sum(len(b) for b in body_msgs)).encode())]})
        for n, b in enumerate(body_msgs):
            await send({"type": "http.response.body", "body": b, "more_body": n < len(body_msgs) - 1})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "path": path, "method": "POST", "headers": list(headers)}
    asyncio.run(mw_cls(app)(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start, body


def setup_guard():
    """THE SETUP-INTERNALS LOCK. Returns True when green; prints its own report."""
    import io
    import contextlib
    print("\n— setup internals in customer-facing copy (one home: core/setup_notice.py + lib/setupNotice.tsx; §19.36) —")
    from app.core import setup_notice as sn
    det = sn.SETUP_INTERNAL
    ok = True
    fe = _fe_files()
    all_fails, seen = setup_scan_frontend(fe, det)
    fails, debt_hits, debt_problems = split_setup_debt(all_fails)
    if fails:
        ok = False
        print(f"  FAIL  {len(fails)} migration / SQL-editor mention(s) in rendered frontend copy:")
        for rel, i, term, seg in fails:
            print(f"        {rel}:{i}  [{term}]  {seg}")
        print("        Fix: <SetupNotice detail=\"…\" /> or setupFailed(…) from lib/setupNotice.tsx — the detail reaches the platform super admin only.")
    else:
        print(f"  OK    no NEW setup internals in rendered frontend copy ({len(fe)} files; super-admin pages excused: {len(SETUP_SUPER_ADMIN_PAGES)} + the operator console)")
    if debt_hits:
        # Printed every run, as a defect being carried — not as a pass. See SETUP_PROSE_DEBT.
        print(f"  DEBT  {len(debt_hits)} tenant-facing sentence(s) still name a migration, pinned in SETUP_PROSE_DEBT:")
        for rel, i, term, seg in debt_hits:
            print(f"        {rel}:{i}  [{term}]  {seg}")
        print("        These are REAL §19.36 defects awaiting conversion to <SetupNotice>; the pin may only shrink.")
    for rel, pin, got in debt_problems:
        ok = False
        print(f"  FAIL  SETUP_PROSE_DEBT pins {pin} for {rel} but {got} remain — lower the pin to {got} (the ratchet).")
    po = platform_only_hrefs()
    for rel, why in SETUP_SUPER_ADMIN_PAGES.items():
        m = _page_re.search(rel)
        href = m.group(1) if m else None
        if href not in po:
            ok = False
            print(f"  FAIL  excused page {rel} is NOT platform-only in NAV ({href}) — a tenant can open it; fix its copy instead")
        if rel not in seen:
            ok = False
            print(f"  FAIL  stale super-admin excusal (no technical wording left — remove it): {rel}")
    lay = "\n".join(fe.get(SETUP_OPERATOR_LAYOUT, []))
    if not ("loadOperatorMe(" in lay and "if (!me)" in lay):
        ok = False
        print("  FAIL  the operator console layout no longer fails closed on the server's operator answer — its pages are not excusable")
    bsrc = _be_app_sources()
    bfails, bstale = setup_scan_backend(bsrc, det)
    if bfails:
        ok = False
        print(f"  FAIL  {len(bfails)} backend string(s) name a migration in a shape the boundary would NOT catch:")
        for where, text in bfails:
            print(f"        {where}  {text!r}")
        print("        Fix: say it in a shape SETUP_INTERNAL recognises (\"run migration 071\", \"migration 071 is not applied\"), or drop the internals.")
    else:
        print(f"  OK    every backend string that names a migration is caught by the boundary ({len(bsrc)} modules)")
    if bstale:
        ok = False
        print(f"  FAIL  stale backend allow entr(ies): {bstale}")
    kfails, kstale, measured = setup_scan_keys(bsrc, det, sn.is_message_key)
    if kfails:
        ok = False
        print(f"  FAIL  {len(kfails)} setup hint(s) emitted under a key the boundary does NOT read (it reads only MESSAGE_KEYS, never data):")
        for where, key, text in kfails:
            print(f"        {where}  [{key}]  {text!r}")
        print("        Fix: drop the internals from the text, or emit it under a message key (detail / note / hint / …).")
    else:
        mk = sorted(k for k in measured if sn.is_message_key(k))
        print(f"  OK    every setup hint the backend emits under a key is under a message key — measured: "
              + ", ".join(f"{k}×{measured[k]}" for k in sorted(mk, key=lambda k: -measured[k])))
    if kstale:
        ok = False
        print(f"  FAIL  stale key-ledger entr(ies): {kstale}")
    unused = sorted(k for k in sn.MESSAGE_KEYS if k not in measured and k not in ("detail", "warnings"))
    ok &= _ctl("K1 MESSAGE_KEYS holds no key the backend never emits a hint under (a wider list reads more customer data)"
               + (f" — unused: {unused}" if unused else ""), not unused)
    rd = lambda p: open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    main_src, rep_src, home_src = rd(SETUP_MAIN), rd(SETUP_REPORTS), rd(SETUP_BE_HOME)
    fe_home = "\n".join(fe.get(SETUP_FE_HOME, []))
    for label, good in setup_wiring(main_src, rep_src, home_src, fe_home):
        ok &= _ctl(label, good)

    # ── the boundary's behaviour, DB-free (a hand-driven ASGI app; the gate stubbed) ──────────────────────────
    print("  — the boundary (SetupNoticeMiddleware) —")

    class NotSuper(sn.SetupNoticeMiddleware):
        @staticmethod
        def _is_super_admin(headers):
            return False

    class Super(sn.SetupNoticeMiddleware):
        @staticmethod
        def _is_super_admin(headers):
            return True

    class GateFault(sn.SetupNoticeMiddleware):
        @staticmethod
        def _is_super_admin(headers):
            raise RuntimeError("gate down")

    leak = json.dumps({"detail": "Save failed — is migration 068_ui_label_override.sql applied?"}).encode()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        st, b = _asgi_run(NotSuper, [leak])
    got = json.loads(b)
    ok &= _ctl("B1 a tenant gets the ONE sentence for a migration-naming detail (the Display Labels defect)",
               got == {"detail": sn.SETUP_NOTICE})
    ok &= _ctl("B2 …the content-length is recomputed", dict(st["headers"]).get(b"content-length") == str(len(b)).encode())
    ok &= _ctl("B3 …and the technical detail is written to the server log", "068_ui_label_override.sql" in err.getvalue())
    _, b = _asgi_run(Super, [leak])
    ok &= _ctl("B4 the platform super admin sees the detail unchanged", b == leak)
    with contextlib.redirect_stderr(io.StringIO()):
        _, b = _asgi_run(GateFault, [leak])
    ok &= _ctl("B5 a gate fault hides the detail (fail closed)", json.loads(b) == {"detail": sn.SETUP_NOTICE})
    plain = json.dumps({"rows": [{"plan": "Port-in Migration", "n": 3}], "note": "All caught up."}).encode()
    _, b = _asgi_run(NotSuper, [plain])
    ok &= _ctl("B6 a response with no hint is returned as the SAME bytes object", b is plain)
    _, b = _asgi_run(NotSuper, [b"run migration 071"], ctype=b"text/csv")
    ok &= _ctl("B7 a non-JSON body (a CSV / file download) streams through untouched", b == b"run migration 071")
    with contextlib.redirect_stderr(io.StringIO()):
        _, b = _asgi_run(NotSuper, [leak[:20], leak[20:]])
    ok &= _ctl("B8 a JSON body sent in chunks is still read whole", json.loads(b) == {"detail": sn.SETUP_NOTICE})
    _, b = _asgi_run(NotSuper, [leak], path="/openapi.json")
    ok &= _ctl("B9 the developer surfaces (/openapi.json, /docs) are left verbatim", b == leak)
    mixed = {"note": "Totals are in. The ledger needs migration 251 to refresh. Other text stays.",
             "hint": "{'code': 'PGRST205', 'message': \"Could not find the table 'hr.x' in the schema cache\"}"}
    new, orig = sn.neutralize(mixed)
    ok &= _ctl("B10 a longer note keeps its other sentences; a raw PostgREST not-applied error is neutralized",
               new["note"] == "Totals are in. " + sn.SETUP_NOTICE + " Other text stays."
               and new["hint"] == sn.SETUP_NOTICE and len(orig) == 2)
    corpus = [t for src in bsrc.values() for _, t in _py_joined_literals(src) if det.search(t)]
    ok &= _ctl(f"B11 the byte prefilter admits every detected backend string ({len(corpus)}) under every message key",
               bool(corpus) and all(sn.may_carry_hint(json.dumps({k: t}).encode()) for t in corpus for k in ("detail", "note")))
    nested = {"ready": False, "config": {"note": "Run migration 245 first."}, "warnings": ["Migration 274 not applied yet."]}
    new, orig = sn.neutralize(nested)
    ok &= _ctl("B12 a nested config dict's note and a warnings list of strings are read",
               new["config"]["note"] == sn.SETUP_NOTICE and new["warnings"] == [sn.SETUP_NOTICE] and len(orig) == 2)

    # ── DATA IS NEVER TOUCHED (coordinator review 2026-09-29) ─────────────────────────────────────────────────
    print("  — data is never touched —")
    data_cells = ["Pending Migration", "Port-in Migration", "Migration 100", "migration 1017 customers",
                  "Run migration 071 first", "fixed in the Supabase SQL editor", "relation \"x\" does not exist"]
    rows = [{"plan": c, "status": c, "note": c, "notes": c, "detail": c, "message": c, "rep": "Miguel"} for c in data_cells]
    for label, payload in [
        ("D1 report rows whose cells (incl. note / detail / message columns) say 'Pending Migration', 'Port-in Migration', "
         "'Migration 100', 'migration 1017 customers', 'run migration 071', 'Supabase SQL editor'", {"rows": rows, "count": 7}),
        ("D2 the same rows nested in a data dict ({by_store: {S1: {rows: […]}}})", {"by_store": {"S1": {"rows": rows}}}),
        ("D3 a bare JSON list body", rows),
        ("D4 a customer's own record keys (notes / status / label / description) at the top level",
         {"id": 7, "notes": "moved to the Supabase SQL editor", "status": "Pending Migration", "label": "Migration 100",
          "description": "run migration 071 at the store"}),
        ("D5 a customer note under `note` that merely mentions a carrier migration", {"note": "Customer asked about Port-in Migration pricing."}),
    ]:
        body = json.dumps(payload).encode()
        with contextlib.redirect_stderr(io.StringIO()):
            _, b = _asgi_run(NotSuper, [body])
        new, orig = sn.neutralize(payload)
        ok &= _ctl(label + " → byte-identical, SAME object", b is body and new is payload and orig == [])
    rep_payload = {"title": "Hours Approval", "subtitle": "Pay period 1–15", "sheets": [{"rows": rows}]}
    new, orig = sn.neutralize(rep_payload)
    ok &= _ctl("D6 a mailed report's sheets / rows are never read (only its subtitle is)", new is rep_payload and orig == [])

    # ── PERFORMANCE: a multi-MB data body is never parsed ────────────────────────────────────────────────────
    print("  — performance —")
    import time
    big_rows = [{"rep": "Miguel Migration", "plan": "Port-in Migration", "status": "Pending Migration", "i": i,
                 "mrc": 45.0, "store": "Migration Ave"} for i in range(40000)]
    big = json.dumps({"rows": big_rows, "total": 40000}).encode()
    calls = {"n": 0}
    real_loads = sn._loads

    def counting(b):
        calls["n"] += 1
        return real_loads(b)

    sn._loads = counting
    try:
        t0 = time.perf_counter()
        _, b = _asgi_run(NotSuper, [big])
        dt = time.perf_counter() - t0
        ok &= _ctl(f"P1 a {len(big) / 1e6:.1f} MB report body full of 'Migration' / 'Miguel' cells: never json.loads'd "
                   f"(parses: {calls['n']}), returned as the SAME bytes object — {dt * 1000:.0f} ms through the middleware",
                   b is big and calls["n"] == 0)
        noted = json.dumps({"rows": [dict(r, note="Pending Migration") for r in big_rows[:20000]]}).encode()
        calls["n"] = 0
        t0 = time.perf_counter()
        _, b = _asgi_run(NotSuper, [noted])
        dt2 = time.perf_counter() - t0
        ok &= _ctl(f"P2 worst case — rows carry a `note` column saying 'Pending Migration' ({len(noted) / 1e6:.1f} MB): "
                   f"parsed once ({calls['n']}), walked without entering the rows, SAME bytes object — {dt2 * 1000:.0f} ms",
                   b is noted and calls["n"] == 1)
        t0 = time.perf_counter()
        hit = sn.may_carry_hint(big)
        dt3 = time.perf_counter() - t0
        ok &= _ctl(f"P3 the key-aware prefilter on the {len(big) / 1e6:.1f} MB body: {dt3 * 1000:.1f} ms, verdict no-parse", not hit)
    finally:
        sn._loads = real_loads

    # ── negative controls — a lock that cannot go red proves nothing ────────────────────────────────────────────
    print("  — negative controls —")
    page = "app/(platform)/admin/labels/page.tsx"
    base = fe.get(page, [])
    f0, _ = setup_scan_frontend({page: base}, det)
    f1, _ = setup_scan_frontend({page: base + ["        Needs migration <code>068_ui_label_override.sql</code>. Edits show on the next sidebar load."]}, det)
    ok &= _ctl("N1 the owner's Display Labels line put back → RED (the defect reproduced)", len(f1) > len(f0))
    f2, _ = setup_scan_frontend({page: base + ["      setMsg((e instanceof Error && e.message) || 'Save failed — is migration 071 applied?')"]}, det)
    ok &= _ctl("N2 a toast fallback naming a migration → RED", len(f2) > len(f0))
    f3, _ = setup_scan_frontend({page: base + ["        <SetupNotice detail=\"068_ui_label_override.sql\" />",
                                              "        <SetupNotice detail={'mig 071'} lead=\"Showing the defaults.\" />"]}, det)
    ok &= _ctl("N3 the migration name as <SetupNotice detail=…/> (super-admin only) → GREEN", len(f3) == len(f0))
    f4, _ = setup_scan_frontend({page: base + ["  // run migration 068_ui_label_override.sql first (a comment is prose)",
                                              "  const x = useState<string[]>([])   // mig 247 — trailing comment"]}, det)
    ok &= _ctl("N4 a migration in a comment / trailing comment → GREEN", len(f4) == len(f0))
    f5, _ = setup_scan_frontend({page: base + ["        <b>1007_onboarding_intake_state.sql</b>"]}, det)
    ok &= _ctl("N5 a bare .sql file name in JSX (no spaces) → RED", len(f5) > len(f0))
    f6, _ = setup_scan_frontend({page: base + ["        Run it in the Supabase SQL editor, then reload."]}, det)
    ok &= _ctl("N6 'Supabase SQL editor' prose → RED", len(f6) > len(f0))
    exc = {**SETUP_SUPER_ADMIN_PAGES, page: "not platform-only on purpose"}
    ok &= _ctl("N7 an excusal of a tenant page is refused (admin/labels is not platform-only in NAV)", "/admin/labels" not in po)
    _, s8 = setup_scan_frontend({page: ["  const a = 1", "  return <div>Display labels</div>"]}, det, exc)
    ok &= _ctl("N8 an excusal whose page carries no technical wording is stale → RED", page not in s8)
    bb = {"commcalc/x.py": 'raise HTTPException(500, "The ledger migration is still outstanding: " + str(e))\n'}
    b9, _ = setup_scan_backend(bb, det)
    ok &= _ctl("N9 a backend message naming a migration in a shape the boundary misses → RED", bool(b9))
    bb = {"commcalc/x.py": 'raise HTTPException(500, "could not save (is migration "\n    f"{MIG} applied?): {e}")\n'
                           'print("run migration 071 — operator log")\nX = {"migration": MIG}\n'}
    b10, _ = setup_scan_backend(bb, det)
    ok &= _ctl("N10 a split / f-string hint the boundary catches, a log line, a dict key → GREEN", not b10)
    _, s11 = setup_scan_backend({}, det, {("commcalc/x.py", "nowhere"): "stale on purpose"})
    ok &= _ctl("N11 a stale backend allow entry → RED", bool(s11))
    w1 = lambda w: w[next(k for k in w if k.startswith("W1"))]
    w = dict(setup_wiring(main_src.replace("app.add_middleware(SetupNoticeMiddleware)", ""), rep_src, home_src, fe_home))
    ok &= _ctl("N12 the boundary unregistered → RED", not w1(w))
    moved = main_src.replace("app.add_middleware(SetupNoticeMiddleware)", "") + "\napp.add_middleware(SetupNoticeMiddleware)\n"
    ok &= _ctl("N13 the boundary registered OUTER of GZip (it would read compressed bytes) → RED",
               not w1(dict(setup_wiring(moved, rep_src, home_src, fe_home))))
    w = dict(setup_wiring(main_src, rep_src.replace("_sn.neutralize(payload)", "(payload, [])"), home_src, fe_home))
    ok &= _ctl("N14 the report builder stops dereferencing neutralize → RED", not w[next(k for k in w if k.startswith("W2"))])
    w = dict(setup_wiring(main_src, rep_src, home_src + '\ndef _x(r):\n    return r.get("super_admin")\n', fe_home))
    ok &= _ctl("N15 a re-derived super-admin rung in the home → RED", not w[next(k for k in w if k.startswith("W3"))])
    w = dict(setup_wiring(main_src, rep_src, home_src, fe_home.replace("Contact support", "Call us")))
    ok &= _ctl("N16 the frontend sentence drifts from the backend's → RED", not w[next(k for k in w if k.startswith("W4"))])
    k17, _, _ = setup_scan_keys({"commcalc/x.py": 'def f():\n    return {"banner": "Run migration 071 first."}\n'}, det, sn.is_message_key)
    ok &= _ctl("N17 a setup hint returned under a non-message key (`banner`) → RED", bool(k17))
    k18, _, _ = setup_scan_keys({"commcalc/x.py":
        'MSG = "run migration 071 first"\n'
        'def f(e):\n    if e:\n        raise HTTPException(500, f"could not save — is migration {MIG} applied? {e}")\n'
        '    warn = []\n    warn.append("Migration 274 not applied yet.")\n'
        '    return {"note": "Needs migration 621.", "hint": MSG, "warnings": warn, "rows": []}\n'}, det, sn.is_message_key)
    ok &= _ctl("N18 hints under detail / note / hint / a warnings list (direct, one-hop constant, appended local) → GREEN", not k18)
    k19, _, _ = setup_scan_keys({"commcalc/x.py": 'MSG = "run migration 071 first"\ndef f():\n    return {"label": MSG}\n'},
                                det, sn.is_message_key)
    ok &= _ctl("N19 a hint constant used one hop away under a non-message key (`label`) → RED", bool(k19))
    _, s20, _ = setup_scan_keys({}, det, sn.is_message_key, {("commcalc/x.py", "banner"): "stale on purpose"})
    ok &= _ctl("N20 a stale key-ledger entry → RED", bool(s20))
    new, _ = sn.neutralize({"rows": [{"note": "Run migration 071 first."}]})
    ok &= _ctl("N21 a hint INSIDE a list outside a message key is left alone (data is never entered) — by design",
               new["rows"][0]["note"] == "Run migration 071 first.")
    print("  " + ("OK — the setup-internals lock holds." if ok else "FAIL — the setup-internals lock is open."))
    return ok


if __name__ == "__main__":
    if "--print-pos-vocab" in sys.argv:
        v = pos_vocabulary()
        print(json.dumps({"spellings": v, "regex": pos_regex(v).pattern}, indent=1))
        sys.exit(0)
    main()
