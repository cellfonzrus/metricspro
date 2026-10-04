"""PROOF: AN ALERT'S SEND RECORD NAMES THE CHANNEL THAT CARRIED IT.

OWNER DECISION 2026-10-04, on the card "Make the send record per channel, so a failed WhatsApp
retries?": **"Fix it properly."**

THE CLASS OF DEFECT (CLAUDE.md, "A fix is a DESIGN fix")
  The instance reported was the store-visit digest. The CLASS is wider and older: every alert in
  this codebase recorded "sent" as soon as ANY channel delivered. A digest whose email went out and
  whose WhatsApp failed was marked done and the WhatsApp was never retried — the tenant had asked
  for both and got one, silently, forever. Fixing only the store-visit path would have been the
  patchwork the owner has twice ruled out.

  Worse, the fact had FOUR implementations, each with its own idea of what "already sent" means:
  the `_lateness_*` pair in storeops/router, `_expiry_already_sent` plus its own insert in the same
  file, and `closing/_send_alert`'s own select/insert pair. `closing/_send_alert` also wrote ONE row
  naming every recipient, so one person's successful email suppressed the alert for everybody else.

WHAT THIS PINS
  A. the row semantics — what a stored row means, and what a legacy row with no channel means;
  B. the per-channel decision: which items each channel still owes a recipient;
  C. the real `deliver_recipient` over a stub client: a failed channel records nothing, a
     successful one records only itself, and the retry carries exactly what is still owed;
  D. the real `deliver_digests` fan-out, including the dry run carrying and recording nothing;
  E. `record_sent` REFUSES to write a row that does not name a channel — there is no default;
  F. the recorded silence (nobody to tell) is not a delivery and suppresses nothing;
  G. migration 1051 is tied to the code it serves, and its backfill matches what the code reads;
  H. THE LOCKS, each with an ARMED CONTROL proving the scan can fail:
     one send record, one delivery ladder, no sweep keeping a private copy, RULE TWO.

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_alert_channel_record.py`
"""
import ast
import asyncio
import io
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
    vacuously (the 2026-10-03 near-miss in harness_billpay_fee_basis.py §H6)."""
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


from app.modules.storeops import alert_log as L            # noqa: E402
from app.modules.notify import digest_delivery as D        # noqa: E402

ALERT_LOG_PY = "app/modules/storeops/alert_log.py"
DELIVERY_PY = "app/modules/notify/digest_delivery.py"
SWEEP_FILES = ["app/modules/storeops/router.py", "app/modules/commcalc/router.py",
               "app/modules/closing/router.py", "app/modules/storevisit/router.py"]
MIG = "../database/migrations/1051_alert_log_channel.sql"


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  WHAT A STORED ROW MEANS")
# ════════════════════════════════════════════════════════════════════════════════════════════
check("A1 a row carried by email, read back for email, counts",
      L.row_matches({"channel": "email", "recipients": "a@b.c"}, channel="email",
                    recipient="a@b.c"))
check("A2 ...and the SAME row does not count for WhatsApp — the defect in one line",
      not L.row_matches({"channel": "email", "recipients": "a@b.c"}, channel="whatsapp",
                        recipient="a@b.c"))
check("A3 a legacy row with no channel carried nothing we can name, so it suppresses nothing",
      not L.row_matches({"channel": None, "recipients": "a@b.c"}, channel="email",
                        recipient="a@b.c"))
check("A4 a row naming several addresses counts for each of them — this is what keeps the "
      "pre-1051 rows written as a joined list meaningful, so nobody already told is re-told",
      L.row_matches({"channel": "email", "recipients": "a@b.c, d@e.f"}, channel="email",
                    recipient="d@e.f"))
check("A5 ...and not for an address it does not name",
      not L.row_matches({"channel": "email", "recipients": "a@b.c, d@e.f"}, channel="email",
                        recipient="z@z.z"))
check("A6 the address comparison is case- and space-insensitive, because an address spelled two "
      "ways is one person",
      L.row_matches({"channel": "email", "recipients": " A@B.C "}, channel="email",
                    recipient="a@b.c"))
check("A7 asking only about the channel ignores the recipient",
      L.row_matches({"channel": "whatsapp", "recipients": "555"}, channel="whatsapp"))
check("A8 a row with an empty channel never matches, even asked with an empty channel — "
      "'carried by nothing' is not a delivery",
      not L.row_matches({"channel": "", "recipients": "a@b.c"}, channel="",
                        recipient="a@b.c"))


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  WHAT EACH CHANNEL STILL OWES")
# ════════════════════════════════════════════════════════════════════════════════════════════
ITEMS = [{"ref_key": "k1"}, {"ref_key": "k2"}, {"ref_key": "k3"}]
CARRIED = {("k1", "email", "a@b.c"), ("k2", "email", "a@b.c")}

check("B1 email is owed only what it has not carried",
      [i["ref_key"] for i in L.pending_for_channel(ITEMS, CARRIED, "email", "a@b.c")] == ["k3"])
check("B2 WhatsApp is owed ALL THREE — the email's success says nothing about it. THIS is the "
      "behaviour the owner asked for",
      [i["ref_key"] for i in L.pending_for_channel(ITEMS, CARRIED, "whatsapp", "555")]
      == ["k1", "k2", "k3"])
check("B3 a different recipient is owed everything, whoever else was told",
      len(L.pending_for_channel(ITEMS, CARRIED, "email", "z@z.z")) == 3)
check("B4 an item with no ref_key is dropped, never sent under an empty key that would then "
      "suppress every other keyless item",
      L.pending_for_channel([{"ref_key": ""}, {"no": "key"}], set(), "email", "a@b.c") == [])

OK_BOTH = {"email": True, "whatsapp": True}
work = D.pending_by_channel({"email": "a@b.c", "whatsapp": "555"}, ITEMS, CARRIED, OK_BOTH)
check("B5 the recipient's outstanding work is per channel", sorted(work) == ["email", "whatsapp"]
      and len(work["email"][1]) == 1 and len(work["whatsapp"][1]) == 3)
check("B6 an UNCONFIGURED channel is not a channel that was tried, so it is absent and records "
      "nothing — the next run offers it again",
      sorted(D.pending_by_channel({"email": "a@b.c", "whatsapp": "555"}, ITEMS, CARRIED,
                                  {"email": True, "whatsapp": False})) == ["email"])
check("B7 a channel the recipient has no address on is absent",
      sorted(D.pending_by_channel({"email": "a@b.c"}, ITEMS, CARRIED, OK_BOTH)) == ["email"])
check("B8 a channel that is square is absent, so nobody is messaged with nothing to say",
      D.pending_by_channel({"email": "a@b.c"}, [{"ref_key": "k1"}], CARRIED, OK_BOTH) == {})


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  THE REAL deliver_recipient, OVER A STUB CLIENT")
# ════════════════════════════════════════════════════════════════════════════════════════════
class StubTable:
    def __init__(self, store):
        self.store, self.f = store, {}

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def in_(self, k, vs):
        self.f[k] = list(vs)
        return self

    def limit(self, _n):
        return self

    def insert(self, row):
        self._row = row
        return self

    def execute(self):
        if hasattr(self, "_row"):
            self.store.append(self._row)
            del self._row
            return type("R", (), {"data": []})
        out = []
        for r in self.store:
            ok = all((r.get(k) in v) if isinstance(v, list) else (r.get(k) == v)
                     for k, v in self.f.items())
            if ok:
                out.append(r)
        return type("R", (), {"data": out})


class StubClient:
    def __init__(self):
        self.rows = []

    def table(self, _name):
        return StubTable(self.rows)


SENT = []


def fake_senders(email_ok=True, wa_ok=True):
    """Replace the ONE channel seam. Nothing here touches a network."""
    async def _send(ch, address, built, wa_filename):
        SENT.append((ch, address, built["subject"]))
        return email_ok if ch == "email" else wa_ok
    return _send


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def build(_name, its):
    return {"subject": f"{len(its)} item(s)", "html": "<p>x</p>", "text": "x"}


_real_send = D._send
D._send = fake_senders(email_ok=True, wa_ok=False)
cli = StubClient()
SENT.clear()
row = run(D.deliver_recipient(cli, "org", "scope_x", addresses={"email": "a@b.c",
                                                               "whatsapp": "555"},
                              items=ITEMS, carried=set(), channels_ok=OK_BOTH, build=build,
                              to_name="Rana", wa_filename="x.txt"))
check("C1 both channels were tried", sorted(c for c, _a, _s in SENT) == ["email", "whatsapp"])
check("C2 only the channel that DELIVERED was recorded",
      sorted({r["channel"] for r in cli.rows}) == ["email"])
check("C3 ...one row per item, naming the address it reached",
      len(cli.rows) == 3 and {r["recipients"] for r in cli.rows} == {"a@b.c"})
check("C4 the planned row reports the failure rather than hiding it",
      row["channels"]["email"]["delivered"] is True
      and row["channels"]["whatsapp"]["delivered"] is False)

# the retry: WhatsApp now works, email has nothing left to say
D._send = fake_senders(email_ok=True, wa_ok=True)
carried = L.sent_pairs(cli, "org", "scope_x")
SENT.clear()
row2 = run(D.deliver_recipient(cli, "org", "scope_x", addresses={"email": "a@b.c",
                                                                "whatsapp": "555"},
                               items=ITEMS, carried=carried, channels_ok=OK_BOTH, build=build,
                               to_name="Rana", wa_filename="x.txt"))
check("C5 THE FIX: the retry sends on WhatsApp ONLY, and carries all three items",
      [c for c, _a, _s in SENT] == ["whatsapp"] and SENT[0][2] == "3 item(s)")
check("C6 ...and the email is NOT sent again, so the fix cannot become a double-send",
      not any(c == "email" for c, _a, _s in SENT))
check("C7 ...and WhatsApp's success is now recorded under its own channel",
      sorted({r["channel"] for r in cli.rows}) == ["email", "whatsapp"])

SENT.clear()
row3 = run(D.deliver_recipient(cli, "org", "scope_x", addresses={"email": "a@b.c",
                                                                "whatsapp": "555"},
                               items=ITEMS, carried=L.sent_pairs(cli, "org", "scope_x"),
                               channels_ok=OK_BOTH, build=build, to_name="Rana",
                               wa_filename="x.txt"))
check("C8 a third run sends nothing: both channels are square", SENT == []
      and row3["already_sent"] is True)

# a channel that RAISES is a channel that carried nothing
async def _boom(ch, _address, _built, _fn):
    if ch == "email":
        raise RuntimeError("transport down")
    return True


D._send = _boom
cli2 = StubClient()
SENT.clear()
row4 = run(D.deliver_recipient(cli2, "org", "scope_y", addresses={"email": "a@b.c",
                                                                 "whatsapp": "555"},
                               items=[{"ref_key": "k1"}], carried=set(), channels_ok=OK_BOTH,
                               build=build, to_name="Rana", wa_filename="x.txt"))
check("C9 a channel that RAISES records nothing and is still owed the finding",
      [r["channel"] for r in cli2.rows] == ["whatsapp"]
      and "error" in row4["channels"]["email"])
check("C10 ...and the sweep does not die with it", row4["channels"]["whatsapp"]["delivered"])


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  THE REAL deliver_digests FAN-OUT")
# ════════════════════════════════════════════════════════════════════════════════════════════
D._send = fake_senders(email_ok=True, wa_ok=True)
DIGESTS = [{"to": "a@b.c", "to_name": "A", "addresses": {"email": "a@b.c", "whatsapp": "1"},
            "items": [{"ref_key": "k1"}, {"ref_key": "k2"}]},
           {"to": "d@e.f", "to_name": "D", "addresses": {"email": "d@e.f"},
            "items": [{"ref_key": "k3"}]}]
cli3 = StubClient()
SENT.clear()
sent, skipped, planned = run(D.deliver_digests(cli3, "org", "sc", DIGESTS, build=build,
                                               wa_filename="x.txt", channels_ok=OK_BOTH,
                                               dry_run=True))
check("D1 a DRY RUN carries nothing and records nothing", SENT == [] and cli3.rows == []
      and sent == 0)
check("D2 ...but still names every recipient and what each channel would carry",
      len(planned) == 2 and planned[0]["channels"]["whatsapp"]["items"] == 2)

SENT.clear()
sent, skipped, planned = run(D.deliver_digests(cli3, "org", "sc", DIGESTS, build=build,
                                               wa_filename="x.txt", channels_ok=OK_BOTH))
check("D3 the real run carries one digest per recipient per owed channel", len(SENT) == 3)
check("D4 ...and counts the recipients carried, not the messages", sent == 2 and skipped == 0)
check("D5 a recipient with no WhatsApp address is emailed only",
      sorted(c for c, a, _s in SENT if a == "d@e.f") == ["email"])

SENT.clear()
sent2, skipped2, _p = run(D.deliver_digests(cli3, "org", "sc", DIGESTS, build=build,
                                            wa_filename="x.txt", channels_ok=OK_BOTH))
check("D6 running the same sweep twice sends nothing the second time",
      SENT == [] and sent2 == 0 and skipped2 == 2)

cli4 = StubClient()
SENT.clear()
_s, _k, planned4 = run(D.deliver_digests(cli4, "org", "sc", DIGESTS, build=build,
                                         wa_filename="x.txt", channels_ok=OK_BOTH, dry_run=True,
                                         preview_item=lambda i: {"k": i["ref_key"]}))
check("D7 a caller keeps its own STRUCTURED preview shape — the view owns presentation",
      planned4[0]["items"] == [{"k": "k1"}, {"k": "k2"}])

# REGRESSION, found by driving the real sweeps on 2026-10-04: with NO channel configured, the
# first cut reported every recipient as "already sent", because nothing was owed to any channel.
# An estate with no credentials would have read its whole backlog as delivered.
cli7 = StubClient()
row7 = run(D.deliver_recipient(cli7, "org", "sc", addresses={"email": "a@b.c"},
                               items=ITEMS, carried=set(),
                               channels_ok={"email": False, "whatsapp": False}, build=build,
                               to_name="A", wa_filename="x.txt"))
check("D8 REGRESSION: no channel configured is NOT 'already sent' — the quietest possible "
      "failure would be an estate reporting its backlog as delivered",
      row7["already_sent"] is False and row7.get("no_channel") is True)
check("D9 ...and nothing is recorded, so it all sends once a channel exists", cli7.rows == [])
D._send = _real_send


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  A RECORD THAT DOES NOT NAME A CHANNEL CANNOT BE WRITTEN")
# ════════════════════════════════════════════════════════════════════════════════════════════
cli5 = StubClient()
for bad in (None, "", "sms", "EMAIL "):
    try:
        L.record_sent(cli5, "org", "sc", "k", "a@b.c", bad)
        refused = False
    except ValueError:
        refused = True
    if bad == "EMAIL ":
        check("E1 a channel is normalised, not rejected, when it is the right word badly spelled",
              not refused and cli5.rows[-1]["channel"] == "email")
    else:
        check(f"E2 record_sent REFUSES {bad!r} — there is deliberately no default channel",
              refused)
check("E3 only the two channels the code can actually speak are allowed",
      L.CHANNELS == ("email", "whatsapp"))


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  A RECORDED SILENCE IS NOT A DELIVERY")
# ════════════════════════════════════════════════════════════════════════════════════════════
cli6 = StubClient()
L.record_silence(cli6, "org", "sc", "k9", detail={"suppressed": "no_recipients"})
check("F1 the silence is RECORDED, so 'this tenant has nobody to tell' is visible",
      len(cli6.rows) == 1 and cli6.rows[0]["recipients"] == "")
check("F2 ...with no channel, because nothing was carried",
      cli6.rows[0]["channel"] is None)
check("F3 ...and it suppresses nothing: the moment a recipient exists, the next occurrence "
      "alerts for real (the 2026-09-20 rule, kept)",
      L.sent_pairs(cli6, "org", "sc") == set()
      and not L.already_sent(cli6, "org", "sc", "k9", "email", recipient="a@b.c"))


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G  MIGRATION 1051 IS TIED TO THE CODE IT SERVES")
# ════════════════════════════════════════════════════════════════════════════════════════════
SQL = open(MIG).read()
check("G1 the column the code reads and writes is the column the migration adds",
      "ADD COLUMN IF NOT EXISTS channel" in SQL and '"channel"' in code_text(ALERT_LOG_PY))
check("G2 it is additive and idempotent", "IF NOT EXISTS" in SQL and "DROP TABLE" not in SQL
      and "DELETE FROM" not in SQL)
check("G3 it carries a REVERT note", "-- REVERT:" in SQL)
check("G4 the backfill classifies a legacy row by the only evidence there is — an email-shaped "
      "recipient — and leaves the rest NULL",
      "SET channel = 'email'" in SQL and "LIKE '%@%'" in SQL
      and "channel IS NULL" in SQL)
check("G5 the CHECK allows exactly the channels the code knows, plus NULL for 'carried nothing'",
      "channel IN ('email', 'whatsapp')" in SQL and "channel IS NULL OR" in SQL)
check("G6 the lookup the code issues is indexed",
      "(org_id, scope, ref_key, channel)" in SQL)
check("G7 PostgREST is told to reload, or the new column is invisible to every caller",
      "NOTIFY pgrst" in SQL)


# ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§H  THE LOCKS, EACH WITH AN ARMED CONTROL")
# ════════════════════════════════════════════════════════════════════════════════════════════
LOG_CODE = code_text(ALERT_LOG_PY)
DEL_CODE = code_text(DELIVERY_PY)
SWEEPS = {p: code_text(p) for p in SWEEP_FILES}

check("H1 LOCK: storeops.alert_log is touched in ONE file. A second reader is a second idea of "
      "what 'already sent' means, which is how this defect survived four rewrites",
      all('table("alert_log")' not in t for t in SWEEPS.values())
      and LOG_CODE.count('table(TABLE)') >= 3)
check("H2 control: the H1 scan can fail — the pattern it hunts for IS present in the one home",
      'table(TABLE)' in LOG_CODE)

check("H3 LOCK: the channel ladder lives in ONE file, so a new alert cannot ship a fifth copy "
      "that forgets the per-channel record",
      "send_document_detailed" in DEL_CODE
      and all("send_document_detailed" not in t for t in SWEEPS.values()))
check("H4 control: the H3 scan can fail — the pattern IS present in the delivery home",
      "send_document_detailed" in DEL_CODE)

check("H5 LOCK: the two superseded wrappers are GONE from every file, so no later change can "
      "reach for a name that records no channel",
      all("_lateness_record_sent" not in t and "_lateness_already_sent" not in t
          for t in SWEEPS.values()))
check("H6 control: the H5 scan can fail — the names it hunts for are findable in this harness",
      "_lateness_record_sent" in open(__file__).read())

check("H7 LOCK: the record is written only inside the delivered branch, never beside it",
      'if not leg["delivered"]:' in DEL_CODE
      and DEL_CODE.index('if not leg["delivered"]:') < DEL_CODE.index("_log.record_sent(")
      and DEL_CODE.count("_log.record_sent(") == 1)
check("H8 control: the H7 scan can fail — the branch it hunts for IS present",
      'if not leg["delivered"]:' in DEL_CODE)

check("H9 LOCK: every sweep that alerts dereferences the ONE delivery home",
      all("_delivery.deliver_recipient(" in t or "_delivery.deliver_digests(" in t
          for t in SWEEPS.values()))
check("H10 control: the H9 scan can fail — a file with no alert in it does not match",
      "_delivery.deliver_digests(" not in code_text("app/modules/commcalc/manager_digest.py"))

check("H11 LOCK: the deciding functions are PURE, so this proof is honest — the one home imports "
      "no client, no framework and no network library",
      "import requests" not in LOG_CODE and "from fastapi" not in LOG_CODE
      and "import requests" not in DEL_CODE and "from fastapi" not in DEL_CODE)
check("H12 control: the H11 scan can fail — a router does import fastapi",
      "from fastapi" in code_text("app/modules/storevisit/router.py"))

BANNED = ["boost", "luxelink", "vzone", "verizon", "xfinity", "metro", "cricket", "att",
          "t-mobile", "shopify", "accessorize"]
low = (LOG_CODE + DEL_CODE).lower()
check("H13 LOCK: RULE TWO — no carrier, tenant or vendor name in either new module",
      not [w for w in BANNED if w in low], detail=str([w for w in BANNED if w in low]))
check("H14 control: the RULE TWO scan can fail — a word that IS in the modules is found",
      "channel" in low)

check("H15 LOCK: the channel vocabulary has ONE home, so a caller cannot invent a channel",
      "CHANNELS = _log.CHANNELS" in DEL_CODE
      and all("CHANNELS = (" not in t for t in SWEEPS.values()))
check("H16 control: the H15 scan can fail — the definition IS in the one home",
      'CHANNELS = ("email", "whatsapp")' in LOG_CODE)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
