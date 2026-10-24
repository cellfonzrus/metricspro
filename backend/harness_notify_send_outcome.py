"""PROOF: A FAILED SEND IS REPORTED WITH ITS RECIPIENT, ITS REASON AND ITS FIX — NEVER AS A COUNT.

OWNER 2026-10-24: *"send report shows Sent: 6 ok, 10 failed., which failed and why should be listed
with how to fix it"*.

THE INSTANCE, measured in production before anything was changed. The 18:03 send of "Closing & Cash
Pickup Status" wrote 16 `notify.send_log` rows: 6 emails sent, and 10 WhatsApp failures that are FIVE
people × TWO files (the .xlsx and the .pdf), every one of them Meta `(#133010) Account not
registered`. So "10 failed" overstated the damage by a factor of two, named nobody, and gave no
remedy — while the row carrying the provider's own words sat in the database. Those five people all
received the email, which the count also could not say.

THE CLASS (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix")
  Not "the Send box is terse". The class is that **nothing owned the question "what did this send
  actually do"**, so every send path answered it with the same two integers and threw the reasons
  away: `/notify/send`, `/notify/send-file`, `/notify/send-to-designated` — and `digest_delivery`
  parked the raw provider string on a leg for each reader to interpret. One home, every path
  dereferencing it, and the READ side stamping history rows written before it existed.

WHAT THIS PINS, over the REAL module and the REAL production error strings
  A. the allowlist: a ruled code yields a plain-language reason AND a remedy;
  B. the honesty rule: an UNRULED error is `recognised: False` with NO fix — a guessed remedy is
     worse than an admitted gap (same rule as `carrier_dollar_class.component_is_evidence`);
  C. channel scoping: a WhatsApp-only ruling never leaks onto an email failure;
  D. THE REGRESSION: the owner's exact 16 rows summarize to 6 ok / 5 failed, each named, with one
     deduped fix — and the 10 attempts are still stated so the old number is accountable;
  E. partial delivery: a recipient who got one file and not the other is FAILED, and says so;
  F. the headline, tri-state, and a send that carried nothing saying why;
  G. THE LOCKS, each with an ARMED CONTROL proving the scan can fail: no send path returns counts
     without an outcome, the digest leg dereferences the same home, the surfaces render it, and no
     remedy sentence is copied into the browser.

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_notify_send_outcome.py`
"""
import ast
import sys

sys.path.insert(0, ".")

from app.modules.notify import send_outcome as so   # noqa: E402  (the REAL module)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def code_text(path):
    """Executable text with docstrings blanked, so a lock cannot match its own explanation."""
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
        out.append(ln.split("#")[0] if ln.lstrip().startswith("#") else ln)
    return "".join(out)


# The production strings, copied verbatim from notify.send_log on 2026-10-24.
E_133010 = ('WhatsApp send 400: {"error":{"message":"(#133010) Account not registered","code":133010,'
            '"type":"OAuthException","fbtrace_id":"AJ4abuD7kxIEhiqkd75NBXP"}}')
E_131030 = ('WhatsApp send 400: {"error":{"message":"(#131030) Recipient phone number not in allowed '
            'list","code":131030,"type":"OAuthException","error_data":{"messaging_product":"whatsapp"}}}')
E_190 = ('WhatsApp media upload 401: {"error":{"message":"Authentication Error","code":190,'
         '"type":"OAuthException"}}')
E_132001 = ('WhatsApp send 404: {"error":{"message":"(#132001) Template name does not exist in the '
            'translation","code":132001,"type":"OAuthException"}}')
E_UNRULED = ('WhatsApp send 400: {"error":{"message":"(#999999) Something nobody has ruled on",'
             '"code":999999,"type":"OAuthException"}}')
E_NOT_CONFIGURED = "Email sending isn't set up yet — contact support to switch it on."

# ── A. the allowlist: a ruled code is named AND has a remedy ──────────────────────────────────────
c = so.classify("whatsapp", E_133010)
check("A1 the owner's actual failure is RULED: #133010 is recognised", c["recognised"] and c["code"] == "133010")
check("A2 its reason blames OUR setup, never the recipient's number",
      "our se" in c["reason"].lower() and "not the recipient" in c["reason"].lower(), detail=c["reason"])
# THE REMEDY THE ESTATE LEARNED THE HARD WAY (whatsapp-token-must-match-waba, after the 2026-10-22
# rotation): #133010 is the token/phone-number PAIRING, not a missing registration. This check pins
# the pairing wording so a future edit cannot quietly restore the plausible wrong fix.
check("A3 its fix is the PAIRING, not a re-registration — the confident wrong remedy is pinned out",
      "same meta app" in c["fix"].lower() and "system user" in c["fix"].lower()
      and "6-digit pin" not in c["fix"].lower(), detail=c["fix"])
check("A3b and it points at the diagnostic that already exists rather than a new one",
      "/notify/whatsapp-account" in c["fix"], detail=c["fix"])
check("A3c #190 stays a DIFFERENT fact: the token itself, with its 24h-expiry trap named",
      "24 hours" in so.classify("whatsapp", E_190)["fix"]
      and so.classify("whatsapp", E_190)["code"] != "133010")
check("A4 the provider's own words are KEPT beside the plain reason (never replaced)",
      "133010" in c["error"] and c["error"] != c["reason"])

c2 = so.classify("whatsapp", E_131030)
check("A5 #131030 is ruled, and its fix names the test allow-list / Live mode",
      c2["recognised"] and ("allow" in c2["fix"].lower() or "live" in c2["fix"].lower()), detail=c2["fix"])
check("A6 #131030's reason blames development mode, not the recipient",
      "development mode" in c2["reason"].lower(), detail=c2["reason"])
c3 = so.classify("whatsapp", E_190)
check("A7 a 401 whose body carries code 190 is read as an EXPIRED TOKEN, not an unknown 401",
      c3["recognised"] and c3["code"] == "190" and "token" in c3["reason"].lower(), detail=str(c3))
check("A8 #132001 is ruled and its fix names the template, not the recipient",
      so.classify("whatsapp", E_132001)["recognised"]
      and "template" in so.classify("whatsapp", E_132001)["fix"].lower())
c4 = so.classify("email", E_NOT_CONFIGURED)
check("A9 our own pre-flight refusal is a DIFFERENT fact: the channel was never tried",
      c4["recognised"] and c4["code"] == "not_configured"
      and "not switched on" in c4["reason"].lower(), detail=str(c4))

# ── B. the honesty rule: an unruled error prescribes NOTHING ──────────────────────────────────────
u = so.classify("whatsapp", E_UNRULED)
check("B1 an UNRULED provider code is recognised:False", u["recognised"] is False)
check("B2 and carries NO fix — a guessed remedy is worse than an admitted gap", u["fix"] == "")
check("B3 its reason falls back to the provider's own words, so a human can still act",
      "999999" in u["reason"], detail=u["reason"])
u2 = so.classify("email", "Resend 500: upstream exploded")
check("B4 an email failure with no ruled signature is also recognised:False, no fix",
      u2["recognised"] is False and u2["fix"] == "")
check("B5 an EMPTY error is not dressed up as a diagnosis",
      so.classify("email", "")["recognised"] is False and so.classify("email", None)["fix"] == "")

# ── C. channel scoping: a ruling cannot leak across channels ──────────────────────────────────────
check("C1 a WhatsApp-only ruling does NOT apply to an email failure quoting the same code",
      so.classify("email", E_131030)["recognised"] is False)
check("C2 control: the same string on its own channel IS ruled — C1 is scoping, not blindness",
      so.classify("whatsapp", E_131030)["recognised"] is True)
check("C3 a ruling declared for ANY channel (an expired token) reaches both",
      so.classify("email", E_190)["recognised"] and so.classify("whatsapp", E_190)["recognised"])
check("C4 the common word 'to' cannot match an email address ruling",
      so.classify("email", "Resend 500: failed to deliver to upstream")["code"] != "email_address")

# ── D. THE REGRESSION: the owner's 16 production rows ─────────────────────────────────────────────
EMAILS = ["rj@luxelinkwireless.com", "luis.martinez@luxelinkwireless.com",
          "brenda.romero@luxelinkwireless.com", "janet.garibay@luxelinkwireless.com",
          "dc@luxelinkwireless.com", "jose.utrera@luxelinkwireless.com"]
PHONES = ["+17734417567", "+15167655559", "+17086906276", "+13128249142", "+17737241282"]
rows = [{"channel": "email", "target": a, "status": "sent", "error": None} for a in EMAILS]
for ph in PHONES:                      # two files per recipient — the .xlsx and the .pdf
    for _ in range(2):
        rows.append({"channel": "whatsapp", "target": ph, "status": "failed", "error": E_133010})
out = so.summarize(rows)
check("D1 the reported shape is reproduced: 16 attempts, 6 sent and 10 failed",
      out["attempts"] == {"sent": 6, "failed": 10}, detail=str(out["attempts"]))
check("D2 but the outcome counts PEOPLE: 6 ok and FIVE failed, not ten",
      (out["ok_count"], out["failed_count"]) == (6, 5),
      detail=f"{out['ok_count']}/{out['failed_count']}")
check("D3 every failed recipient is NAMED", sorted(f["target"] for f in out["failed"]) == sorted(PHONES))
check("D4 each names its channel", all(f["channel"] == "whatsapp" for f in out["failed"]))
check("D5 each carries the plain reason and the remedy",
      all(f["reason"] and f["fix"] for f in out["failed"]))
check("D6 each states the two attempts behind it, so '10 failed' is accountable",
      all(f["attempts"] == 2 and f["failed_attempts"] == 2 for f in out["failed"]))
check("D7 one shared cause is ONE fix line, not five copies", out["fixes"] == [out["failed"][0]["fix"]])
check("D8 the headline says who got it and who did not",
      out["headline"] == "Sent to 6 of 11 recipients. 5 failed — the reason for each is below.",
      detail=out["headline"])
check("D9 the six who DID receive it are listed too (the count could not say this)",
      sorted(o["target"] for o in out["ok"]) == sorted(EMAILS))
check("D10 none of the five is marked partial — they got no WhatsApp copy at all",
      all(f["partial"] is False for f in out["failed"]))

# ── E. partial delivery ───────────────────────────────────────────────────────────────────────────
part = so.summarize([
    {"channel": "whatsapp", "target": "+15550001111", "status": "sent", "error": None},
    {"channel": "whatsapp", "target": "+15550001111", "status": "failed", "error": E_131030},
])
check("E1 a recipient who got ONE file and not the other counts as FAILED, not sent",
      (part["ok_count"], part["failed_count"]) == (0, 1))
check("E2 and is marked partial, so nobody re-sends the half that arrived",
      part["failed"][0]["partial"] is True)
check("E3 its reason is the failed leg's, not silence", part["failed"][0]["code"] == "131030")

# ── F. the headline, tri-state ────────────────────────────────────────────────────────────────────
all_ok = so.summarize([{"channel": "email", "target": "a@b.c", "status": "sent", "error": None}])
check("F1 everything delivered: no failure language at all",
      all_ok["headline"] == "Sent to all 1 recipient." and all_ok["failed"] == [], detail=all_ok["headline"])
none_ok = so.summarize([{"channel": "whatsapp", "target": "+1555", "status": "failed", "error": E_133010}])
check("F2 nothing delivered: the headline says NOTHING arrived — never 'sent'",
      none_ok["headline"].startswith("Nothing arrived") and "sent" not in none_ok["headline"].split("—")[0].lower(),
      detail=none_ok["headline"])
nobody = so.summarize([], note="no active designated recipients for this report")
check("F3 a send with nobody to send to says WHY, and claims no success",
      nobody["headline"] == "no active designated recipients for this report"
      and nobody["ok_count"] == 0 and nobody["failed_count"] == 0, detail=nobody["headline"])
check("F4 an empty send with no note still says nothing was sent, not 'sent to 0'",
      "Nothing was sent" in so.summarize([])["headline"], detail=so.summarize([])["headline"])
check("F5 headline() is the ONE sentence — summarize stamps exactly what it returns",
      out["headline"] == so.headline(out))

# ── G. the locks ──────────────────────────────────────────────────────────────────────────────────
ROUTER_SRC = open("app/modules/notify/router.py").read()
ROUTER = code_text("app/modules/notify/router.py")


def count_returns_without_outcome(src):
    """Every `return {...}` in the notify router whose keys include BOTH 'sent' and 'failed' is a send
    result. It MUST also carry 'outcome' — that is the un-wiring lock: a new send path that answers
    with two integers fails the build."""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if {"sent", "failed"} <= keys and "outcome" not in keys:
            bad.append(node.lineno)
    return bad


bad = count_returns_without_outcome(ROUTER_SRC)
check("G1 LOCK: no send path in notify/router.py returns counts without an outcome", not bad,
      detail=f"lines {bad}")
ARMED = 'def f():\n    return {"sent": 0, "failed": 0, "skipped": "x"}\n'
check("G2 control: the G1 scan can fail — the pre-fix literal IS caught",
      count_returns_without_outcome(ARMED) == [2])
check("G3 control: the G1 scan is not vacuous — the router really does return send results",
      any({"sent", "failed"} <= {k.value for k in n.value.keys
                                 if isinstance(k, ast.Constant) and isinstance(k.value, str)}
          for n in ast.walk(ast.parse(ROUTER_SRC))
          if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)))
check("G4 LOCK: both delivery paths dereference the one home",
      ROUTER.count("send_outcome.summarize(log_rows)") == 2, detail=ROUTER)
check("G5 LOCK: the READ side stamps history too, so an old row is explained",
      "send_outcome.stamp(" in ROUTER)
DIGEST = code_text("app/modules/notify/digest_delivery.py")
check("G6 LOCK: the digest leg dereferences the SAME home — the sibling is not left behind",
      "classify(" in DIGEST and "send_outcome" in DIGEST)
check("G7 LOCK: no module but the one home rules on a provider error code",
      sum(1 for f in ("app/modules/notify/router.py", "app/modules/notify/digest_delivery.py",
                      "app/modules/notify/channels/whatsapp_meta.py",
                      "app/modules/notify/channels/email_resend.py")
          if "131030" in code_text(f) or "133010" in code_text(f)) == 0)
FE_VIEW = open("../frontend/src/lib/send-outcome.tsx").read()
FE_BTN = open("../frontend/src/lib/send-report.tsx").read()
FE_DESIG = open("../frontend/src/app/(platform)/notify/report-recipients/page.tsx").read()
FE_HIST = open("../frontend/src/app/(platform)/notify/page.tsx").read()
check("G8 LOCK: every send surface renders the ONE view, none of them its own list",
      all("SendOutcomeView" in f for f in (FE_BTN, FE_DESIG)) and "SendOutcomeView" in FE_VIEW)
check("G9 LOCK: the browser renders the backend's words — no remedy sentence is COPIED into it",
      not any(fix[:40] in (FE_VIEW + FE_BTN + FE_DESIG + FE_HIST)
              for (_c, _ch, _r, fix) in so._RULES if fix))
check("G10 control: the G9 scan can fail — a remedy sentence IS findable in the backend",
      any(fix[:40] in open("app/modules/notify/send_outcome.py").read()
          for (_c, _ch, _r, fix) in so._RULES if fix))
check("G11 LOCK: the history page shows the stamped reason, not only the raw provider text",
      "failure_reason" in FE_HIST and "failure_fix" in FE_HIST)
check("G12 LOCK: the button no longer leads with the count-only sentence",
      "res?.outcome" in FE_BTN and FE_BTN.index("res?.outcome") < FE_BTN.index("ok, ${res.failed} failed"))
BANNED = ["boost", "luxelink", "vzone", "verizon", "metro", "cricket", "t-mobile", "accessorize"]
SO_CODE = code_text("app/modules/notify/send_outcome.py")
hits = [w for w in BANNED if w in SO_CODE.lower()]
check("G13 LOCK: RULE TWO — no carrier or tenant name in the one home", not hits, detail=str(hits))
check("G14 control: the RULE TWO scan can fail — a word that IS there is found",
      "whatsapp" in SO_CODE.lower())
check("G15 LOCK: the one home does no IO — a pure fact cannot acquire a database read",
      not any(w in SO_CODE for w in ("import httpx", "get_supabase", "sb()", "requests.")))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
