"""PROOF: the morning bill-pay declaration digest — what it alerts on, and what it refuses to.

OWNER ASK 2026-10-03, verbatim:
    "the system should create that report and send it to the dm and all above via whats app and
     email the next morning at 1030 am - nothing hardcoded"

WHY THIS ALERT IS NOT NOISE. Before §47.12 fixed the POS basis to include the customer service fee,
nine September store-days in ten disagreed with the rep's declaration, so a digest built on the old
comparison would have escalated 526 of 535 store-days and taught every district manager to ignore it.
With the basis corrected, yesterday's real dry run (2026-10-02, house org, read-only) flagged FIVE
store-days worth $278.53 out of 18, with 13 agreeing. That is a signal.

WHAT THIS PINS
  A. the config is per-tenant with house defaults, and a bad value degrades to the default rather
     than to "never alert" or "alert on everything";
  B. the classification — and that ABSENCE IS NOT ZERO: a store-day with no POS figure is refused as
     an alert BY NAME, and a $0.00 declaration against a real basis is the biggest exception there is;
  C. the digest reads the same in both renderings, counts what it could not assess, and never drops a
     row in silence;
  D. the dedup key makes a finding escalate once per day per recipient, and a CHANGE of class is
     news rather than a duplicate;
  E. the due-time rule: an hourly tick sends from the configured minute, once;
  F. channels — a recipient is reached on each channel they have an address for, skipped only when
     none can, and the email-only callers that existed before are byte-identical;
  G. migration 1043 is tied to the code it configures, and is OFF by default;
  H. RULE TWO, and the locks are ARMED.

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_billpay_declaration_alerts.py`
"""
import ast
import io
import sys
import tokenize

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.modules.closing import billpay_declaration_alerts as A    # noqa: E402
from app.modules.commcalc import manager_digest as MD              # noqa: E402


def code_text(path):
    """Executable text with docstrings and comments removed, spacing preserved."""
    src = open(path).read()
    lines = src.splitlines(keepends=True)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for i in range(body[0].lineno - 1, min(body[0].end_lineno, len(lines))):
                    lines[i] = "\n"
    kept = "".join(lines)
    cuts = {}
    for tok in tokenize.generate_tokens(io.StringIO(kept).readline):
        if tok.type == tokenize.COMMENT:
            cuts.setdefault(tok.start[0], tok.start[1])
    return "\n".join(ln[:cuts[n]] if n in cuts else ln
                     for n, ln in enumerate(kept.splitlines(), start=1))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. config: per-tenant, house defaults, bad values degrade ==")
d = A.resolve_config(None)
check("A1 no tenant row -> the house defaults", d["send_time"] == "10:30" and d["tolerance"] == 1.0
      and d["enabled"] is False and d["lookback_days"] == 1, d)
check("A2 OFF by default, so nothing sends on deploy", A.HOUSE_CONFIG["enabled"] is False)
check("A3 the default time is the mig-433 convention, not a number chosen here",
      A.HOUSE_CONFIG["send_time"] == MD.DEFAULT_ALERT_TIME == "10:30")
check("A4 a tenant's own values win",
      A.resolve_config({"billpay_declaration_alerts_enabled": True,
                        "billpay_declaration_alert_time": "07:15",
                        "billpay_declaration_alert_tolerance": 25,
                        "billpay_declaration_alert_lookback_days": 3})
      == {**A.HOUSE_CONFIG, "enabled": True, "send_time": "07:15", "tolerance": 25.0,
          "lookback_days": 3, "channels": ("email", "whatsapp")})
for bad in ("", "nonsense", "99:99", None, "10"):
    check(f"A5 an unparseable time {bad!r} degrades to the default, never to 'never alert'",
          A.resolve_config({"billpay_declaration_alert_time": bad})["send_time"] == "10:30")
for bad in ("x", None, ""):
    check(f"A6 an unparseable tolerance {bad!r} degrades to the default, never to 0 "
          f"(which would alert on every rounding cent)",
          A.resolve_config({"billpay_declaration_alert_tolerance": bad})["tolerance"] == 1.0)
check("A7 a NEGATIVE tolerance is read as its magnitude, never as 'alert on everything'",
      A.resolve_config({"billpay_declaration_alert_tolerance": -5})["tolerance"] == 5.0)
check("A8 the lookback is clamped to a sane span and never zero",
      A.resolve_config({"billpay_declaration_alert_lookback_days": 0})["lookback_days"] == 1
      and A.resolve_config({"billpay_declaration_alert_lookback_days": 9999})["lookback_days"] == 31)
check("A9 '9:5' is normalised so a string comparison against it is sound",
      A.resolve_config({"billpay_declaration_alert_time": "9:5"})["send_time"] == "09:05")

print("\n== B. classification — and absence is NOT zero ==")
check("B1 a declaration below the basis is under-declared",
      A.classify(0.0, 599.38) == A.CLASS_UNDER)
check("B2 a declaration above it is over-declared",
      A.classify(692.0, 286.67) == A.CLASS_OVER)
check("B3 a match agrees", A.classify(71.0, 71.0) == A.CLASS_AGREE)
check("B4 NO POS FIGURE is refused by name, never compared",
      A.classify(50.0, None) == A.CLASS_NO_POS
      and A.CLASS_NO_POS in A.REFUSED_AS_ALERT and A.CLASS_NO_POS not in A.ALERTABLE)
check("B5 ... and its gap is None, so no screen can render 'no difference' for it",
      A.gap_of(50.0, None) is None)
check("B6 $0.00 declared against $0.00 basis AGREES (an honest quiet day)",
      A.classify(0.0, 0.0) == A.CLASS_AGREE)
check("B7 $0.00 declared against a REAL basis is the biggest exception there is -- the two $0 cases "
      "must never collapse into each other",
      A.classify(0.0, 599.38) == A.CLASS_UNDER
      and A.classify(0.0, 0.0) != A.classify(0.0, 599.38))
check("B8 the tolerance is inclusive, so a difference AT it is rounding",
      A.classify(100.0, 101.0, tolerance=1.0) == A.CLASS_AGREE
      and A.classify(100.0, 101.01, tolerance=1.0) == A.CLASS_UNDER)
check("B9 only the two person-problems are alertable",
      A.ALERTABLE == (A.CLASS_UNDER, A.CLASS_OVER)
      and set(A.ALERTABLE) | set(A.REFUSED_AS_ALERT) == set(A.GAP_CLASSES))

SD = [
    {"store_code": "B-559", "close_date": "2026-09-10", "declared": 0.0, "pos_basis": 599.38,
     "fee_cash": 20.0, "bill_txns": 10},
    {"store_code": "B-3565", "close_date": "2026-09-16", "declared": 692.0, "pos_basis": 286.67,
     "fee_cash": 20.0},
    {"store_code": "B-103", "close_date": "2026-09-13", "declared": 71.0, "pos_basis": 71.0,
     "fee_cash": 4.0},
    {"store_code": "B-999", "close_date": "2026-09-13", "declared": 10.0, "pos_basis": None},
]
got = A.alert_items(SD)
check("B10 only the alertable classes become items",
      [i["store_code"] for i in got["items"]] == ["B-3565", "B-559"],
      [i["store_code"] for i in got["items"]])
check("B11 every class is counted, including the ones that are not alerts",
      got["counts"] == {A.CLASS_UNDER: 1, A.CLASS_OVER: 1, A.CLASS_AGREE: 1, A.CLASS_NO_POS: 1},
      got["counts"])
check("B12 the unassessable store-days are reported as refused, not forgotten",
      got["refused"] == {A.CLASS_NO_POS: 1})
check("B13 an item carries the fee that was added back, so the figure's change is explainable",
      got["items"][1]["fee_cash"] == 20.0)
check("B14 no store-days -> no items and no fabricated counts",
      A.alert_items([])["items"] == [] and A.alert_items(None)["items"] == [])
check("B15 a malformed store-day cannot crash the sweep",
      A.alert_items([None, "x", {}])["items"] == [])

print("\n== C. the digest ==")
dg = A.build_digest("Dana", got["items"], counts=got["counts"])
check("C1 the subject names the count and the money", "2 store-days" in dg["subject"]
      and "$1,004.71" in dg["subject"], dg["subject"])
check("C2 both renderings exist and carry the same stores",
      all(s in dg["html"] for s in ("B-559", "B-3565"))
      and all(s in dg["text"] for s in ("B-559", "B-3565")))
check("C3 the two renderings agree on the total -- two versions of one digest that disagree is a "
      "digest nobody trusts",
      "$1,004.71" in dg["text"] and "1,004.71" in dg["subject"])
check("C4 the footer says what could NOT be assessed, so a thin digest is never read as health",
      "could not be assessed" in dg["html"] and "could not be assessed" in dg["text"])
check("C5 ... and says it is a feed gap, not somebody's mistake",
      "not a declaration problem" in dg["html"] and "not a declaration problem" in dg["text"])
check("C6 the matched store-days are mentioned but not listed",
      "matched and are not listed" in dg["html"])
many = A.alert_items([{"store_code": f"S{i}", "close_date": "2026-09-10", "declared": 0.0,
                       "pos_basis": 100.0 + i} for i in range(40)])["items"]
dg2 = A.build_digest("Dana", many, max_rows=5)
check("C7 rows beyond the cap are COUNTED, never silently dropped",
      "35 more store-days" in dg2["html"] and "35 more" in dg2["text"], dg2["text"][-120:])
check("C8 ... and the subject still counts all 40, so the cap never understates the problem",
      "40 store-days" in dg2["subject"], dg2["subject"])
check("C9 a digest with no refusals says nothing about refusals",
      "could not be assessed" not in A.build_digest("D", got["items"], counts={})["html"])
check("C10 the direction is stated in words, not left to a sign",
      "not declared" in dg["text"] and "not in the sales data" in dg["text"])

print("\n== D. dedup identity ==")
it = got["items"][0]
check("D1 the key tail is store, day and class",
      A.key_parts(it) == (it["store_code"], it["close_date"], it["gap_class"]))
k1 = MD.ref_key(A.ALERT_SCOPE, "2026-10-03", "a@b.c", *A.key_parts(it))
check("D2 the key is built by the fan-out's spelling, not this module's",
      k1.startswith("billpay_declaration|2026-10-03|a@b.c|"))
check("D3 the same finding twice is one key (escalates once per day per recipient)",
      k1 == MD.ref_key(A.ALERT_SCOPE, "2026-10-03", "A@B.C", *A.key_parts(it)))
flipped = {**it, "gap_class": A.CLASS_UNDER if it["gap_class"] == A.CLASS_OVER else A.CLASS_OVER}
check("D4 a store-day that CHANGES class is a different finding -- 'declared too little' becoming "
      "'declared too much' is news, not a duplicate",
      k1 != MD.ref_key(A.ALERT_SCOPE, "2026-10-03", "a@b.c", *A.key_parts(flipped)))
check("D5 a new DAY re-keys, so tomorrow can escalate again if it is still wrong",
      k1 != MD.ref_key(A.ALERT_SCOPE, "2026-10-04", "a@b.c", *A.key_parts(it)))
check("D6 the scope is the alert_log scope, so the row and the key agree by construction",
      A.ALERT_SCOPE == "billpay_declaration" and k1.split("|")[0] == A.ALERT_SCOPE)

print("\n== E. the due-time rule (one home) ==")
check("E1 the configured minute is due, and every minute after it that day",
      MD.due_now("10:30", "10:30") and MD.due_now("10:31", "10:30") and MD.due_now("23:59", "10:30"))
check("E2 a minute before is not due", not MD.due_now("10:29", "10:30")
      and not MD.due_now("00:00", "10:30"))
check("E3 an unreadable clock is NOT due -- a clock we cannot read must not trigger a fan-out",
      not MD.due_now("", "10:30") and not MD.due_now("xx:yy", "10:30")
      and not MD.due_now(None, "10:30"))
check("E4 a tenant with a blank send time still alerts, at the house hour",
      MD.due_now("10:30", "") and MD.due_now("10:30", None))
check("E5 the comparison has ONE home -- the sweep does not spell it",
      "strftime" in code_text("app/modules/closing/router.py")
      and "due_now" in code_text("app/modules/closing/router.py"))
check("E6 ... and this module does not spell it either",
      "due_now" not in code_text("app/modules/closing/billpay_declaration_alerts.py"))

print("\n== F. channels ==")
check("F1 a recipient with both addresses is reachable on both",
      MD.addresses_for({"email": "a@b.c", "phone": "555"}, ("email", "whatsapp"))
      == {"email": "a@b.c", "whatsapp": "555"})
check("F2 a recipient with only a phone is still reachable when WhatsApp is requested",
      MD.addresses_for({"phone": "555"}, ("email", "whatsapp")) == {"whatsapp": "555"})
check("F3 ... and is skipped when only email is requested (the pre-2026-10-03 rule, unchanged)",
      MD.addresses_for({"phone": "555"}, ("email",)) == {})
check("F4 a recipient with no address at all is skipped",
      MD.addresses_for({}, ("email", "whatsapp")) == {} and MD.addresses_for(None) == {})
check("F5 blank/whitespace addresses do not count as addresses",
      MD.addresses_for({"email": "  ", "phone": ""}, ("email", "whatsapp")) == {})
check("F6 an unknown channel cannot be requested into existence",
      MD.normalize_channels(["sms", "pigeon"]) == MD.DEFAULT_CHANNELS
      and set(MD.CHANNEL_ADDRESS_FIELD) == {"email", "whatsapp"})
# BYTE-IDENTITY for the callers that existed before channels did.
HIER = {"S1": {"dm": [{"name": "Dana", "email": "dana@x.test", "phone": "555"}],
               "above": [{"name": "Rob", "email": "rob@x.test"}]}}
ITEMS = [{"store_code": "S1", "close_date": "2026-09-10", "gap_class": "under_declared",
          "gap": -10.0}]
plan_old = MD.plan_digests(ITEMS, HIER, "2026-10-03", scope="t",
                           build=lambda n, i: {"subject": "s", "html": "h"},
                           key_parts=lambda i: (i["store_code"],))
check("F7 an email-only plan carries no 'addresses' key, so every existing reader is unchanged",
      all("addresses" not in d for d in plan_old["digests"]), plan_old["digests"][0].keys())
check("F8 ... and still keys on the email, so no ref_key ever written changes",
      plan_old["digests"][0]["items"][0]["ref_key"] == MD.ref_key("t", "2026-10-03",
                                                                  "dana@x.test", "S1"))
plan_new = MD.plan_digests(ITEMS, HIER, "2026-10-03", scope="t",
                           build=lambda n, i: {"subject": "s", "html": "h", "text": "t"},
                           key_parts=lambda i: (i["store_code"],),
                           channels=("email", "whatsapp"))
check("F9 with WhatsApp requested the plan names each channel that can reach the recipient",
      plan_new["digests"][0]["addresses"] == {"email": "dana@x.test", "whatsapp": "555"},
      plan_new["digests"][0].get("addresses"))
check("F10 ... the manager with no phone is still reached by email, not dropped",
      plan_new["digests"][1]["addresses"] == {"email": "rob@x.test"})
check("F11 ... and the dedup identity is STILL the email, so turning WhatsApp on does not "
      "re-escalate every finding",
      plan_new["digests"][0]["items"][0]["ref_key"]
      == plan_old["digests"][0]["items"][0]["ref_key"])
check("F12 the plain-text body rides along only when a channel needs it",
      "text" in plan_new["digests"][0] and "text" not in plan_old["digests"][0])

print("\n== G. migration 1043 ==")
_m = open("../database/migrations/1043_billpay_declaration_alerts.sql").read()
check("G1 every knob the owner named is a column", all(
    c in _m for c in ("billpay_declaration_alert_time", "billpay_declaration_alert_channels",
                      "billpay_declaration_alert_tolerance",
                      "billpay_declaration_alerts_enabled",
                      "billpay_declaration_alert_lookback_days")))
check("G2 additive and idempotent", _m.count("ADD COLUMN IF NOT EXISTS") >= 5)
check("G3 OFF by default, so nothing sends on deploy", "DEFAULT false" in _m)
check("G4 the time default is 10:30 and the channels default to both",
      "DEFAULT '10:30'" in _m and '"email","whatsapp"' in _m)
check("G5 it declares a REVERT", "-- REVERT:" in _m)
check("G6 it introduces NO new table and reuses alert_log",
      "CREATE TABLE" not in _m.upper() and "alert_log" in _m)
check("G7 it performs no backfill and moves no money",
      "UPDATE " not in _m.upper().replace("DO UPDATE", "") and "INSERT INTO" not in _m.upper())
check("G8 the cron registration is HOURLY, because the per-tenant minute is compared in the handler",
      "cron.schedule('billpay-declaration-run-due', '5 * * * *'" in _m)
check("G9 the column names in the migration are the ones resolve_config reads",
      all(c in code_text("app/modules/closing/billpay_declaration_alerts.py")
          for c in ("billpay_declaration_alert_time", "billpay_declaration_alert_channels")))
check("G10 it states the refusal this alert is built on", "no_pos_figure" in _m
      and "NEVER alerted" in _m)

print("\n== H. RULE TWO, and the locks are ARMED ==")
_a = code_text("app/modules/closing/billpay_declaration_alerts.py").lower()
check("H0 control: the scan dropped the prose (the docstring's 'owner' is gone)",
      "owner" in open("app/modules/closing/billpay_declaration_alerts.py").read().lower()
      and "owner" not in _a)
for banned in ("boost", "epay", "vidapay", "cellfonz", "luxelink", "xfinity", "b-2612", "b-103"):
    check(f"H1 {banned!r} appears in no executable line", banned not in _a)
_sw = code_text("app/modules/closing/router.py")
check("H2 the sweep does not roll its own DM-∪-above loop",
      '"above"' not in _sw.split("_run_billpay_declaration_alerts")[-1].split("def _billpay_declaration_store_days")[0])
check("H3 the sweep plans through the ONE fan-out", "_md.plan_digests(" in _sw)
check("H4 the sweep writes dedup rows through the EXISTING alert_log helpers",
      "_lateness_already_sent(so, oid, _bda.ALERT_SCOPE" in _sw
      and "_lateness_record_sent(so, oid, _bda.ALERT_SCOPE" in _sw)
check("H5 no new alert table is introduced by the sweep",
      "alert_log" not in _sw.split("_run_billpay_declaration_alerts")[-1][:6000])
check("H6 WhatsApp goes through the window-safe home, NEVER send_text -- a free-form 10:30 send "
      "returns 200 and is silently dropped (the 2026-08-05 incident)",
      "send_document_detailed" in _sw and "send_text" not in _sw)
check("H7 the declared side is the figure IN FORCE (DM corrections applied), so a manager is never "
      "chased about a store-day they already fixed",
      "declared_billpay_in_force" in _sw)
check("H8 the POS side dereferences the fee-corrected home, not a raw key",
      "pos_billpay_cash" in _sw)
check("H9 control: the RULE TWO scan can fail -- a word that IS in the module is found",
      "declared" in _a)
check("H10 control: a dedup row is written only when a channel actually delivered, so an "
      "unconfigured channel cannot mark a finding escalated and hide it tomorrow",
      "if delivered:" in _sw)
check("H11 control: the alertable/refused split really partitions the vocabulary, so a new class "
      "cannot be silently neither",
      not (set(A.ALERTABLE) & set(A.REFUSED_AS_ALERT))
      and set(A.ALERTABLE) | set(A.REFUSED_AS_ALERT) == set(A.GAP_CLASSES))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
