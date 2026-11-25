"""PROOF + LOCK: EVERY OUTBOUND EMAIL IS RECORDED, BY THE TRANSPORT, IN ONE PLACE.

OWNER REPORT 2026-11-25: *"onboargin em,ail not reaching employees"*, said of NY Logistics Employee
Setup.

THE CLASS OF DEFECT (CLAUDE.md, "A fix is a DESIGN fix")
  The instance was the HR onboarding invite. Measured read-only on production before any code was
  written: two invites minted at 20:26:57 and 20:26:59 UTC that day, both stamped `invited`, both
  with an audit row saying `sent_to: <address>` — and `notify.send_log` holding NO row for either.
  Thirty days of that table held report sends and nothing else: no HR letter, no CRM mail, no
  helpdesk reply, no POS receipt, no access code. Email delivery itself was healthy (1,336 sends in
  ten days, 1,052 of them to gmail.com), so the reported symptom was not a broken provider; it was
  that NOBODY COULD TELL, because an accepted mail, a refused mail and a bounced address all left
  exactly the same trace: none.

  The CLASS is **an outbound message recorded by the caller that asked, instead of the transport
  that sent it.** `notify.send_log` was already the house's record of a send, and mig 714 had
  already added the three columns that carry what the provider says AFTERWARDS. Thirty-one call
  sites each decided for themselves whether to use any of it, and every one of them except the
  report senders decided not to. That is §19.18's pattern verbatim: the registry existed and the
  callers were never wired to it.

  THE SIBLING NOBODY FIXED. The identical defect was found and fixed for WhatsApp on 2026-07-18 —
  Meta accepts a message, returns an id, then silently drops it — which is what mig 714 and the Meta
  webhook are for. Email never got the sibling: there was no provider webhook at all, so
  `delivery_status` was NULL on every email row ever written. "Fixed for one channel and not the
  other is the same defect wearing a hat" (CLAUDE.md), so the delivery arithmetic that fix produced
  is FACTORED OUT of `remediation/router` into `notify/send_record` and dereferenced by both
  webhooks, rather than copied for the second one.

WHAT THIS PINS
  A. the pure arithmetic, DB-free: the attempt row, the per-channel status allowlist, the monotonic
     merge, the Resend event map, the Svix signature, and the verdict one surface shows;
  B. LOCK: `purpose` is a REQUIRED keyword argument of the transport, and EVERY call site passes it;
  C. LOCK: the transport itself records — if `send_email` stops dereferencing `send_record`, this
     fails;
  D. LOCK: ONE writer of `notify.send_log`. A second `.table("send_log")` anywhere fails the build;
  E. LOCK: ONE caller of the provider's HTTP API;
  F. LOCK: the delivery arithmetic has one home — `remediation/router` may not re-grow its own;
  G. LOCK: the webhook fails CLOSED, and is public for POST only;
  H. the ONE documented orgless sender (the pre-login code) is pinned, so a second cannot appear
     quietly;
  I. RULE TWO: no carrier, tenant or product name in the new module.

Every lock ships with a CONTROL check proving the scan can fail, because a build lock that matches
nothing passes vacuously (the 2026-10-03 near-miss in harness_billpay_fee_basis.py §H6).

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_email_send_record.py`
"""
import ast
import base64
import hashlib
import hmac
import io
import os
import sys
import tokenize

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def code_text(path):
    """Executable text with docstrings and comments removed, spacing preserved. A lock that scans
    token-joined text can never match a multi-token pattern, which is how a build lock passes
    vacuously."""
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


def py_files(root="app"):
    for base, _dirs, names in os.walk(root):
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.join(base, n)


TRANSPORT = "app/modules/notify/channels/email_resend.py"
NOTIFY_ROUTER = "app/modules/notify/router.py"
REMEDIATION = "app/modules/remediation/router.py"
MIDDLEWARE = "app/core/tenant_middleware.py"
CONFIG = "app/core/config.py"
AUTH_NOTIFY = "app/modules/core/auth_notify.py"
GRAPH = "app/modules/core/module_graph.py"
FACT = "email_send_record"

# THE HOME IS NOT DECLARED HERE. A lock that types its own `HOME = ...` is the duplicate defect
# wearing the shape of its own cure (index §50 / §19.18), so the ruling is read from the one home
# for "what else answers this question" — module_graph — and the files above are the CALLERS this
# lock scans, never a second declaration of the home.
import importlib.util                                    # noqa: E402

_graph_spec = importlib.util.spec_from_file_location("_module_graph_proof", GRAPH)
_graph = importlib.util.module_from_spec(_graph_spec)
_graph_spec.loader.exec_module(_graph)
HOME = _graph.FACTS[FACT]["homes"][0]

HOME_CODE = code_text(HOME)
TRANSPORT_CODE = code_text(TRANSPORT)

# Loaded BY PATH, not by package import: this harness runs on a bare CI python with no fastapi,
# pydantic or httpx installed, and `app.modules.notify.send_record` is stdlib-only precisely so it
# can be proved that way. A package import would drag in app/core/config and defeat the point.
import importlib.util                                    # noqa: E402

_spec = importlib.util.spec_from_file_location("_send_record_proof", HOME)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

# ── A. the pure arithmetic ───────────────────────────────────────────────────────────────────────
print("\n── A. the arithmetic, proved with no database ──")
HOUSE = "00000000-0000-0000-0000-000000000001"
row = sr.attempt_row(org_id=HOUSE, purpose="hr_onboarding_invite", channel="email",
                     target="Rep@Example.com", status="sent", provider_message_id="re_1")
check("A1 an attempt row names the tenant, what was sent, the address and the provider's id",
      row["org_id"] == HOUSE and row["report_key"] == "hr_onboarding_invite"
      and row["target"] == "Rep@Example.com" and row["provider_message_id"] == "re_1")


def raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


check("A2 a send that cannot say WHAT IT IS cannot be recorded — no default purpose exists",
      raises(lambda: sr.attempt_row(org_id=HOUSE, purpose="  ", channel="email",
                                    target="a@b.c", status="sent")))
check("A3 an attempt records only what the TRANSPORT saw — 'delivered' is refused here",
      all(raises(lambda s=s: sr.attempt_row(org_id=HOUSE, purpose="p", channel="email",
                                            target="a@b.c", status=s))
          for s in ("delivered", "bounced", "pending", "")))
check("A4 each channel accepts only the words ITS OWN provider uses",
      sr.valid_status("email", "bounced") and not sr.valid_status("whatsapp", "bounced")
      and sr.valid_status("whatsapp", "read") and not sr.valid_status("email", "read"))
check("A5 an unruled status, and an unruled channel, record NOTHING (allowlist)",
      not sr.valid_status("email", "probably_fine") and not sr.valid_status("pigeon", "delivered"))
check("A6 a failure state always wins, however late it arrives",
      all(sr.merge_status(x, "bounced") == "bounced"
          for x in ("sent", "delivered", "read", "delayed", None, "")))
check("A7 a later healthy status never overwrites a failure",
      all(sr.merge_status("bounced", x) == "bounced"
          for x in ("sent", "delivered", "read", "delayed")))
check("A8 the status never regresses, and an unknown word changes nothing",
      sr.merge_status("read", "delivered") == "read"
      and sr.merge_status("delivered", "opened") == "delivered")
check("A9 the 2026-07-18 WhatsApp behaviour is unchanged by factoring it out",
      sr.merge_status(None, "sent") == "sent" and sr.merge_status("sent", "delivered") == "delivered"
      and sr.merge_status("delivered", "read") == "read"
      and sr.merge_status("read", "failed") == "failed"
      and sr.merge_status("failed", "read") == "failed")
_meta = sr.flatten_meta_errors([{"code": 131026, "title": "Message undeliverable",
                                 "error_data": {"details": "Receiver is incapable"}}])
check("A10 Meta's errors[] flatten to one human string",
      "131026" in _meta and "undeliverable" in _meta and "incapable" in _meta)
_b = sr.delivery_from_resend({"type": "email.bounced",
                              "data": {"email_id": "re_9",
                                       "bounce": {"type": "Permanent", "subType": "General",
                                                  "message": "550 5.1.1 user unknown"}}})
check("A11 a bounce carries the provider's own reason", _b[:2] == ("re_9", "bounced")
      and "550" in _b[2])
_sup = sr.delivery_from_resend({"type": "email.failed",
                                "data": {"email_id": "re_s",
                                         "failed": {"reason": "suppressed: previously bounced"}}})
check("A11b a SUPPRESSED send (confirmed live 2026-11-25) is recorded as a provider REFUSAL",
      _sup[:2] == ("re_s", "failed") and "suppress" in _sup[2].lower()
      and sr.delivery_verdict({"status": "sent", "delivery_status": "failed",
                               "delivery_error": _sup[2]})[0] == "refused")
check("A11c a refusal is NOT reported as a bounce — the remedy is ours, not the recipient's",
      sr.delivery_verdict({"status": "sent", "delivery_status": "failed"})[0] == "refused"
      and sr.delivery_verdict({"status": "sent", "delivery_status": "bounced"})[0] == "bounced")
check("A11d a refusal with no reason still names the likely cause",
      "suppression list" in sr.delivery_from_resend(
          {"type": "email.failed", "data": {"email_id": "re_s"}})[2])
check("A12 an OPEN is not a delivery — it records nothing",
      all(sr.delivery_from_resend({"type": t, "data": {"email_id": "re_9"}}) is None
          for t in ("email.opened", "email.clicked", "email.unheard_of", "", None)))
check("A13 an event with no message id cannot be attached to anything",
      sr.delivery_from_resend({"type": "email.delivered", "data": {}}) is None
      and sr.delivery_from_resend("not a dict") is None)

SECRET = "whsec_" + base64.b64encode(b"a-test-signing-key-32-bytes-long").decode()


def sign(secret, mid, ts, body):
    key = base64.b64decode(secret.split("_", 1)[1])
    return "v1," + base64.b64encode(
        hmac.new(key, sr.svix_signed_payload(mid, ts, body), hashlib.sha256).digest()).decode()


_body = b'{"type":"email.delivered","data":{"email_id":"re_1"}}'
_good = sign(SECRET, "msg_1", "1700000000", _body)
check("A14 a correctly signed payload verifies",
      sr.svix_signature_ok(SECRET, "msg_1", "1700000000", _good, _body, now=1700000000))
check("A15 an UNSET secret rejects every payload (fail closed)",
      not sr.svix_signature_ok("", "msg_1", "1700000000", _good, _body, now=1700000000))
check("A16 a tampered body, a wrong id and a missing header all fail",
      not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", _good, _body + b" ", now=1700000000)
      and not sr.svix_signature_ok(SECRET, "msg_X", "1700000000", _good, _body, now=1700000000)
      and not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", "", _body, now=1700000000))
check("A17 a captured payload cannot be replayed forever",
      not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", _good, _body, now=1700004000))
check("A18 several signatures pass while a secret is rotated",
      sr.svix_signature_ok(SECRET, "msg_1", "1700000000", "v1,AAAA " + _good, _body,
                           now=1700000000))
check("A19 NO RECORD IS NEVER REPORTED AS SENT — the whole reported defect, in one line",
      sr.delivery_verdict(None)[0] == "unknown")
check("A20 an accepted mail is its own state, and the surface mentions spam",
      sr.delivery_verdict({"status": "sent"})[0] == "accepted"
      and "spam" in sr.delivery_verdict({"status": "sent"})[1].lower())
check("A21 refused, bounced, complained, delayed and delivered each read as themselves",
      sr.delivery_verdict({"status": "failed", "error": "422"})[0] == "failed"
      and sr.delivery_verdict({"status": "sent", "delivery_status": "bounced"})[0] == "bounced"
      and sr.delivery_verdict({"status": "sent", "delivery_status": "complained"})[0] == "complained"
      and sr.delivery_verdict({"status": "sent", "delivery_status": "delayed"})[0] == "delayed"
      and sr.delivery_verdict({"status": "sent", "delivery_status": "delivered"})[0] == "delivered")

# ── B. purpose is required, and every caller passes it ───────────────────────────────────────────
print("\n── B. LOCK: a send must say what it is ──")
# The transport's signature is read from its SOURCE, for the same reason the module above is loaded
# by path: no fastapi, no httpx, no credentials.
_send_def = next((n for n in ast.walk(ast.parse(open(TRANSPORT).read()))
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "send_email"), None)
_kwonly = [a.arg for a in (_send_def.args.kwonlyargs if _send_def else [])]
_kwdefaults = list(_send_def.args.kw_defaults) if _send_def else []
_purpose_default = (_kwdefaults[_kwonly.index("purpose")]
                    if "purpose" in _kwonly and len(_kwdefaults) == len(_kwonly) else "?")
check("B1 LOCK: `purpose` is a KEYWORD-ONLY parameter of the transport", "purpose" in _kwonly,
      detail=str(_kwonly))
check("B2 LOCK: `purpose` has NO DEFAULT — a caller that forgets fails loudly, never silently",
      _purpose_default is None)
check("B2b control: the signature scan can fail — a sibling keyword-only arg DOES have a default",
      "label" in _kwonly and _kwdefaults[_kwonly.index("label")] is not None)

CALL_SITES, MISSING, ORGLESS = [], [], []
for path in py_files():
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if fname != "send_email":
            continue
        kw = {k.arg for k in node.keywords}
        CALL_SITES.append(f"{path}:{node.lineno}")
        if "purpose" not in kw:
            MISSING.append(f"{path}:{node.lineno}")
        if "org_id" not in kw:
            ORGLESS.append(f"{path}:{node.lineno}")

check("B3 LOCK: EVERY send_email call site names its purpose", not MISSING,
      detail=", ".join(MISSING[:6]))

#: WHO SENDS EMAIL IN THIS HOUSE, declared, not counted. A bare `len(CALL_SITES) >= 25` is a lower
#: bound on a set this lock derived from the code, so a module that STOPPED sending — or one that
#: appeared — would slide under it unnamed (the derived-subject-set rule; CI's own guard caught this
#: assertion on 2026-11-25 before it shipped). Declared as a map so a module that gains or loses a
#: send names itself, which is the point: thirty-one senders, each of which must record what it did.
EXPECTED_SENDERS = {
    "app/modules/closing/router.py": 3,
    "app/modules/core/auth_notify.py": 2,
    "app/modules/crm/router.py": 1,
    "app/modules/helpdesk/router.py": 3,
    "app/modules/hr/letters.py": 1,
    "app/modules/hr/router.py": 5,
    "app/modules/notify/digest_delivery.py": 1,
    "app/modules/notify/router.py": 3,
    "app/modules/pos/router.py": 2,
    "app/modules/recovery/router.py": 1,
    "app/modules/referral/router.py": 2,
    "app/modules/remediation/router.py": 1,
    "app/modules/storeops/payroll_approval.py": 2,
    "app/modules/storeops/router.py": 4,
}
_found = {}
for _site in CALL_SITES:
    _found[_site.rsplit(":", 1)[0]] = _found.get(_site.rsplit(":", 1)[0], 0) + 1
check("B4 the senders are EXACTLY the declared set (a module that gains or loses one names itself)",
      _found == EXPECTED_SENDERS,
      detail="added/changed: %s | missing: %s" % (
          {k: v for k, v in sorted(_found.items()) if EXPECTED_SENDERS.get(k) != v},
          {k: v for k, v in sorted(EXPECTED_SENDERS.items()) if k not in _found}))
check("B4b control: the declared set is not vacuous — 31 sends across 14 modules",
      sum(EXPECTED_SENDERS.values()) == 31 and len(EXPECTED_SENDERS) == 14)

# ── C. the transport is what records ────────────────────────────────────────────────────────────
print("\n── C. LOCK: the transport records, not the caller ──")
check("C1 LOCK: the transport dereferences the one home",
      "from app.modules.notify import send_record" in TRANSPORT_CODE
      and "send_record.record_attempt(" in TRANSPORT_CODE)
check("C2 LOCK: it records a REFUSAL too, not only an acceptance",
      TRANSPORT_CODE.count("_record(\"failed\"") >= 3)
check("C3 LOCK: it records the acceptance with the provider's own message id",
      "_record(\"sent\", mid=mid)" in TRANSPORT_CODE)
check("C4 control: the C1 scan can fail — the home does NOT call itself that way",
      "send_record.record_attempt(" not in HOME_CODE)

# ── D. one writer of the table ──────────────────────────────────────────────────────────────────
print("\n── D. LOCK: notify.send_log has ONE writer ──")
WRITE_VERBS = {"insert", "upsert", "update", "delete"}


def _chain_tables(node):
    """Every `.table("x")` in the receiver chain of a call, so a write can be attributed to the
    relation it writes however long the chain is."""
    out, cur = [], node
    while isinstance(cur, (ast.Call, ast.Attribute)):
        if isinstance(cur, ast.Call):
            f = cur.func
            if isinstance(f, ast.Attribute) and f.attr == "table" and cur.args \
                    and isinstance(cur.args[0], ast.Constant):
                out.append(cur.args[0].value)
            cur = f
        else:
            cur = cur.value
    return out


def send_log_writers(path):
    try:
        tree = ast.parse(open(path).read())
    except SyntaxError:                                    # pragma: no cover - defensive
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in WRITE_VERBS and "send_log" in _chain_tables(node):
            hits.append(f"{path}:{node.lineno}")
    return hits


def send_log_writers_in_home():
    """The home writes through `_table(...)`, not a literal `.table("send_log")`, so the D1 scan
    cannot see it. The control proves the scan works by pointing it at a literal copy of the home's
    own write shape — if THIS stops matching, D1 has stopped being able to fail."""
    probe = ast.parse('c.schema("notify").table("send_log").insert(r).execute()\n'
                      'c.table("send_log").update(u).eq("id", i).execute()\n'
                      'c.table("send_log").delete().eq("id", i).execute()\n')
    return [n.func.attr for n in ast.walk(probe)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in WRITE_VERBS and "send_log" in _chain_tables(n)]


WRITERS = sorted(h for p in py_files() if p != HOME for h in send_log_writers(p))
check("D1 LOCK: no module but the one home WRITES notify.send_log", not WRITERS,
      detail=", ".join(WRITERS))
check("D1b readers are allowed, and there still ARE some — this lock is about writers",
      any('table("send_log")' in code_text(p) for p in py_files() if p != HOME))
check("D2 control: the D1 scan can fail — the same scan DOES find the home's own writes",
      len(send_log_writers_in_home()) >= 3, detail=str(send_log_writers_in_home()))
check("D3 LOCK: the notify router's own insert now delegates to the home",
      "send_record.insert_rows(log_rows)" in code_text(NOTIFY_ROUTER))
check("D4 LOCK: the report path does not write a SECOND email row of its own",
      code_text(NOTIFY_ROUTER).count('_insert_log([r for r in log_rows if r.get("channel") != "email"]') == 2)

# ── E. one caller of the provider ───────────────────────────────────────────────────────────────
print("\n── E. LOCK: one caller of the provider's HTTP API ──")
PROVIDER_CALLERS = sorted(p for p in py_files()
                          if "api.resend.com" in code_text(p) and p != TRANSPORT)
check("E1 LOCK: nothing but the transport speaks to the provider", not PROVIDER_CALLERS,
      detail=", ".join(PROVIDER_CALLERS))
check("E2 control: the E1 scan can fail — the transport DOES hold the address",
      "api.resend.com" in TRANSPORT_CODE)

# ── F. the delivery arithmetic has one home ─────────────────────────────────────────────────────
print("\n── F. LOCK: the delivery arithmetic is not copied per channel ──")
REM_CODE = code_text(REMEDIATION)
check("F1 LOCK: remediation dereferences the home rather than holding its own merge",
      "_merge_delivery_status = _send_record.merge_status" in REM_CODE
      and "def _merge_delivery_status(" not in REM_CODE)
check("F2 LOCK: remediation no longer holds its own rank table or error flattener",
      "def _flatten_delivery_errors(" not in REM_CODE
      and "_DELIVERY_RANK = _send_record.DELIVERY_RANK" in REM_CODE)
check("F3 LOCK: the Meta handler stamps through the home's one write path",
      "_send_record.stamp_delivery(" in REM_CODE)
check("F4 control: the F1/F2 scans can fail — the definitions ARE in the home",
      "def merge_status(" in HOME_CODE and "def flatten_meta_errors(" in HOME_CODE
      and "DELIVERY_RANK = {" in HOME_CODE)

# ── G. the webhook's posture ────────────────────────────────────────────────────────────────────
print("\n── G. LOCK: the webhook fails closed, and is public for POST only ──")
MID_CODE = code_text(MIDDLEWARE)
NOTIFY_CODE = code_text(NOTIFY_ROUTER)
check("G1 LOCK: the webhook verifies the signature before reading the body's meaning",
      "send_record.svix_signature_ok(settings.RESEND_WEBHOOK_SECRET" in NOTIFY_CODE)
check("G2 LOCK: an unverified POST is refused",
      'raise HTTPException(403, "signature verification failed")' in NOTIFY_CODE)
check("G3 LOCK: the path is on the public allowlist as an EXACT path",
      '"/api/v1/notify/email-webhook",' in MID_CODE)
check("G4 LOCK: it is public for POST ONLY — never by inheritance for every method",
      '"/api/v1/notify/email-webhook": ("POST",),' in MID_CODE)
check("G5 LOCK: the secret has NO break-glass that would accept anonymous payloads",
      "RESEND_WEBHOOK_SECRET" in code_text(CONFIG)
      and "RESEND_WEBHOOK_REQUIRE_SIGNATURE" not in code_text(CONFIG))
check("G6 control: the G3/G4 scans can fail — the Meta sibling is there too, same shape",
      '"/api/v1/remediation/whatsapp-webhook": ("GET", "POST"),' in MID_CODE)

# ── H. the one documented orgless sender ────────────────────────────────────────────────────────
print("\n── H. LOCK: one documented sender records nothing, and only one ──")
ALLOWED_ORGLESS = {AUTH_NOTIFY}
UNEXPECTED = sorted({s.rsplit(":", 1)[0] for s in ORGLESS} - ALLOWED_ORGLESS)
check("H1 LOCK: only the pre-login code sender may send without naming a tenant", not UNEXPECTED,
      detail=", ".join(UNEXPECTED))
check("H2 control: the H1 scan can fail — the allowed one IS found orgless",
      any(s.startswith(AUTH_NOTIFY) for s in ORGLESS))
check("H3 the exception is DOCUMENTED where it is taken, not only here",
      "THE ONE DOCUMENTED ORGLESS SEND" in open(AUTH_NOTIFY).read())

# ── I. RULE TWO ─────────────────────────────────────────────────────────────────────────────────
print("\n── I. RULE TWO ──")
BANNED = ["boost", "luxelink", "vzone", "verizon", "xfinity", "metro", "cricket",
          "t-mobile", "shopify", "accessorize", "logistics"]
low = HOME_CODE.lower()
hits = [w for w in BANNED if w in low]
check("I1 LOCK: no carrier, tenant or vendor name in the one home", not hits, detail=str(hits))
check("I2 control: the RULE TWO scan can fail — a word that IS there is found", "channel" in low)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
print("ALL GREEN" if not FAIL else "RED")
sys.exit(1 if FAIL else 0)
