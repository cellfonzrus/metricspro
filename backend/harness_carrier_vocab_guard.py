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
  · b2bsoft/RTPOS/RQ (POS vendor names) are NOT scanned: they are POS vocabulary, absent from the
    owner's banned list (the POS brand is the same on both sides today).

Adding a new carrier-branded string to shared copy → add a preset term (report_labels.LABELABLE_TERMS
+ a mig ≥953 seed) and render it via useReportLabels().term(), or gate the page in NAV_CARRIERS —
never extend REVIEWED_EXCEPTIONS as a shortcut.

  python3 backend/harness_carrier_vocab_guard.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
RBAC = os.path.join(FE, "lib", "rbac.ts")

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
    "app/(platform)/commcalc/upload/page.tsx": {"both": "tiles carrier-tagged + tileVisible-filtered in-file"},
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
    if fails:
        print(f"\nFAIL — {len(fails)} hardcoded cross-side carrier term(s) in display copy:")
        for rel, i, side, term, seg in fails:
            print(f"  {rel}:{i}  [{side}:{term}]  {seg}")
        print("\nFix: resolve the name via useReportLabels().term() (preset data, mig 953), reword "
              "neutrally, or carrier-gate the page in NAV_CARRIERS — see this file's docstring.")
        sys.exit(1)
    print("OK — no hardcoded cross-side carrier vocabulary in rendered frontend copy.")
    sys.exit(0)


if __name__ == "__main__":
    main()
