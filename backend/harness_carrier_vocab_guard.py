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
                     "pos/sales_from_reports.py"]       # 2026-09-21 — sales rebuilt from the reports (the format is the DECLARED POS's, never a literal)
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


def jsx_prose_line(rel, ln):
    """A .tsx line that is pure JSX TEXT continuing a paragraph — no tag, no operator, no quote — is
    display copy the string/tag extractor cannot see ('No daily B2B feed loaded for {period} yet.')."""
    s = ln.strip()
    if not rel.endswith(".tsx") or " " not in s or _prose_bad.search(s) or s.startswith(("//", "*", "/*", "{/*")):
        return []
    return [s]


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
            for seg in display_segments(ln) + jsx_prose_line(rel, ln):
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
                for seg in display_segments(ln):
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
    if not pos_guard():
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


if __name__ == "__main__":
    if "--print-pos-vocab" in sys.argv:
        v = pos_vocabulary()
        print(json.dumps({"spellings": v, "regex": pos_regex(v).pattern}, indent=1))
        sys.exit(0)
    main()
