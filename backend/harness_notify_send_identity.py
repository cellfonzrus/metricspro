"""PROOF: A NOTIFY HISTORY ROW NAMES THE REPORT IT CARRIED.

OWNER 2026-10-05: *"in the notify app, the employees are sending themselves reports or notification,
in the notify history it should show which report was exported"*.

THE CLASS OF DEFECT (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix")
  The instance is the export-bar send. The CLASS is that a send record had no owner for the question
  "which report is this", so each of the three writers answered it differently:
    * /notify/send + /send-to-designated stored the MACHINE key and the history rendered it raw,
      although the registry has held a human label for every report all along;
    * /notify/send-file — the universal path every page's "Send" button uses, i.e. the one employees
      actually use to send themselves a report — stored the literal "(client-export)" for EVERY
      report and DISCARDED the title the browser had already sent;
    * the no-login download row stored "(download)" on the same terms.
  Putting the title into the client-export row would have been the patchwork: the next send path
  would invent a fourth answer. So the fact has ONE home, every writer dereferences it, and the
  READER resolves it too — which is what makes the history correct for rows that predate mig 1057.

WHAT THIS PINS
  A. the resolution: a registered key is named by the REGISTRY (never a browser-supplied spelling),
     an unregistered send by its title, and neither by a machine key dressed up as a name;
  B. the REGRESSION the owner reported: a client export's row carries the report's name;
  C. the read side: a legacy row with no stored label still displays a name, with no DB column;
  D. the real writers, read from source — no send path names the report itself any more;
  E. graceful degrade: an un-run mig 1057 costs the stored label, never the history row;
  F. THE LOCKS, each with an ARMED CONTROL proving the scan can fail: one home, no writer building
     its own report_key, the reader stamping every row, RULE TWO.

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_notify_send_identity.py`
"""
import ast
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def code_text(path):
    """Executable text with docstrings removed, spacing preserved — so a lock cannot match its own
    explanation in a comment or docstring and pass vacuously."""
    src = open(path).read()
    lines = src.splitlines(keepends=True)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for i in range(body[0].lineno - 1, min(body[0].end_lineno, len(lines))):
                    lines[i] = "\n"
    out = []
    for ln in lines:
        s = ln.split("#")[0] if ln.lstrip().startswith("#") else ln
        out.append(s)
    return "".join(out)


from app.modules.notify import send_identity as SI   # noqa: E402  (pure leaf: no DB, no app wiring)

LABELS = {"closing_envelope_report": "Envelope Report", "portout_fraud": "Daily Port-Out Fraud Report"}


# ── A. the resolution ────────────────────────────────────────────────────────────────────────────
check("A1 a registered report is named by the REGISTRY, not by the machine key",
      SI.report_label("closing_envelope_report", None, LABELS) == "Envelope Report")
check("A2 the registry label WINS over a browser-supplied title (one spelling per report)",
      SI.report_label("closing_envelope_report", "envelope rpt (v2)", LABELS) == "Envelope Report")
check("A3 an unregistered send is named by its title",
      SI.report_label(SI.CLIENT_EXPORT_KEY, "Store Visit Compliance", LABELS) == "Store Visit Compliance")
check("A4 an unknown key with a title is named by the title, never by the key",
      SI.report_label("some_future_report", "Future Report", LABELS) == "Future Report")
check("A5 neither key nor title → a fallback word, never a machine key dressed as a name",
      SI.report_label(None, None, LABELS) == SI.FALLBACK_LABEL
      and SI.report_label(SI.CLIENT_EXPORT_KEY, "", LABELS) == SI.FALLBACK_LABEL)
check("A6 a sentinel key is never itself a label (it is a route, not a report)",
      SI.CLIENT_EXPORT_KEY not in (SI.report_label(SI.CLIENT_EXPORT_KEY, "X", LABELS),
                                   SI.report_label(SI.CLIENT_EXPORT_KEY, None, LABELS))
      and SI.report_label(SI.DOWNLOAD_KEY, None, LABELS) == SI.FALLBACK_LABEL)
check("A7 a label is one trimmed line, bounded to the column",
      SI.report_label(None, "  Weekly \n Owed  ", LABELS) == "Weekly Owed"
      and len(SI.report_label(None, "x" * 500, LABELS)) == 200)
check("A8 PURE — no IO, no app import, in the one home",
      not any(w in code_text("app/modules/notify/send_identity.py")
              for w in ("import ", "get_supabase", "requests", "sb()")))

# ── B. the regression the owner reported ─────────────────────────────────────────────────────────
row = SI.log_identity(None, "Daily Closing Summary", LABELS)
check("B1 REGRESSION: a client export's history row carries the REPORT NAME, not '(client-export)'",
      row[SI.LABEL_COLUMN] == "Daily Closing Summary", detail=str(row))
check("B2 … and it still records WHICH ROUTE carried it, so the two facts never collapse",
      row["report_key"] == SI.CLIENT_EXPORT_KEY)
check("B3 a registered report sent through the SAME client path is named by the registry",
      SI.log_identity("portout_fraud", "whatever the page called it", LABELS)[SI.LABEL_COLUMN]
      == "Daily Port-Out Fraud Report")
check("B4 every identity dict carries BOTH columns, always populated",
      all(set(d) == {"report_key", SI.LABEL_COLUMN} and all(d.values())
          for d in (SI.log_identity(None, None, LABELS),
                    SI.log_identity("portout_fraud", None, LABELS),
                    SI.log_identity(SI.DOWNLOAD_KEY, "f.pdf", LABELS))))

# ── C. the read side (no column needed) ──────────────────────────────────────────────────────────
check("C1 a row written since mig 1057 displays its stored name",
      SI.display_label({"report_key": "x", SI.LABEL_COLUMN: "Asset Ledger"}, LABELS) == "Asset Ledger")
check("C2 a LEGACY row (no label column) still displays the registry name",
      SI.display_label({"report_key": "portout_fraud"}, LABELS) == "Daily Port-Out Fraud Report")
check("C3 a legacy client-export row degrades to a word, never to '(client-export)'",
      SI.display_label({"report_key": SI.CLIENT_EXPORT_KEY}, LABELS) == SI.FALLBACK_LABEL)
check("C4 a legacy download row is named by the stored filename when that is all there is",
      SI.display_label({"report_key": SI.DOWNLOAD_KEY, "filename": "Envelope Count.pdf"}, LABELS)
      == "Envelope Count.pdf")
stamped = SI.stamp_display([{"report_key": "portout_fraud"}, {}, None], LABELS)
check("C5 the reader stamps EVERY row, including an empty one, and never mutates the input",
      len(stamped) == 3 and all(r.get(SI.LABEL_COLUMN) for r in stamped))

# ── D. the real writers ──────────────────────────────────────────────────────
ROUTER = code_text("app/modules/notify/router.py")
check("D1 the client-export writer dereferences the one home (the literal is GONE)",
      '"(client-export)"' not in ROUTER and "send_identity.log_identity" in ROUTER)
def send_log_row_literals(src):
    """Every dict literal in `src` that IS a send_log row — it names a channel and a status. Found by
    AST so the lock cannot be fooled by formatting, and so the OTHER dicts in the router (report_config
    rows, failure_log details, API responses) are not swept in with them."""
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"channel", "status"} <= keys:
                out.append(keys)
    return out

ROWS = send_log_row_literals(open("app/modules/notify/router.py").read())
check("D2 every send_log row DEREFERENCES the one home — none names report_key by hand",
      len(ROWS) >= 3 and not [r for r in ROWS if "report_key" in r],
      detail=f"{len(ROWS)} rows found; hand-built: {[sorted(r) for r in ROWS if 'report_key' in r]}")
# ARMED CONTROL: the pre-fix router line, fed to the same scanner. If this ever stops being flagged the
# D2 lock has gone blind and a new send path could quietly go back to naming the report itself.
_WAS = 'row = {"org_id": o, "report_key": "(client-export)", "channel": c, "status": s}'
check("D3 control: the D2 scan can fail — the PRE-FIX row is flagged by the same scanner",
      [r for r in send_log_row_literals(_WAS) if "report_key" in r])
check("D4 the reader stamps the history before returning it",
      "send_identity.stamp_display" in ROUTER)
check("D5 the stored artifact names its report too, so the DOWNLOAD row can",
      "send_identity.report_label" in ROUTER and "report_label=ident" in ROUTER)

# ── E. graceful degrade when mig 1057 is un-run ──────────────────────────────────────────────────
check("E1 the label is listed as an OPTIONAL column, so an un-run migration costs the label only",
      "_OPTIONAL_LOG_COLUMNS" in ROUTER and "send_identity.LABEL_COLUMN" in ROUTER)
check("E2 the degrade drops optional columns ONE AT A TIME and retries (never loses the row)",
      "drop.append(" in ROUTER and "for attempt in range(len(_OPTIONAL_LOG_COLUMNS)" in ROUTER)
MIG = open("../database/migrations/1057_notify_send_log_report_label.sql").read()
check("E3 mig 1057 is additive, idempotent and carries a REVERT note",
      "ADD COLUMN IF NOT EXISTS report_label" in MIG and "-- REVERT:" in MIG
      and "DROP TABLE" not in MIG.upper())
check("E4 mig 1057 adds the column to BOTH tables the writers use",
      "notify.send_log      ADD COLUMN" in MIG and "notify.send_artifact ADD COLUMN" in MIG)

# ── F. the locks ─────────────────────────────────────────────────────────────────────────────────
SI_CODE = code_text("app/modules/notify/send_identity.py")
check("F1 LOCK: the registry exposes ONE label map, and send_identity never hard-codes a label",
      "def report_labels()" in code_text("app/modules/notify/report_registry.py")
      and "report_registry.report_labels()" in ROUTER
      and 'REPORTS' not in SI_CODE)
check("F2 control: the F1 scan can fail — the router DOES mention report_registry",
      "report_registry" in ROUTER)
BANNED = ["boost", "luxelink", "vzone", "verizon", "metro", "cricket", "t-mobile", "accessorize"]
hits = [w for w in BANNED if w in (SI_CODE + MIG).lower()]
check("F3 LOCK: RULE TWO — no carrier or tenant name in the one home or its migration", not hits,
      detail=str(hits))
check("F4 control: the RULE TWO scan can fail — a word that IS there is found",
      "report" in (SI_CODE + MIG).lower())
check("F5 LOCK: send_identity is the ONLY module that decides a report's display name",
      sum(1 for f in ("app/modules/notify/router.py", "app/modules/notify/report_registry.py")
          if "def report_label(" in code_text(f)) == 0)
check("F6 LOCK: the frontend shows the resolved name, not the machine key",
      "l.report_label || l.report_key"
      in open("../frontend/src/app/(platform)/notify/page.tsx").read())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
