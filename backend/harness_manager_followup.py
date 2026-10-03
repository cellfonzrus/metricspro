"""PROOF: FOLLOW UP WITH MANAGERS — what it follows up on, who it reaches, and what it refuses to hide.

OWNER ASK 2026-10-03, verbatim:
    "then alert the management via a whats app message for all followup items with the managers -
     this will be a seprate module - Follow Up with Managers , all pending jobs assigned to the
     managers will be followed up via this module"

WHY THIS MODULE IS A ROLL-UP AND NOT A TO-DO LIST — measured read-only on the house org, 2026-10-03:
76,094 open items can be attributed and aged, 20,304 of them more than 90 days old, the oldest 324
days, and 14,372 carrying NO store and therefore no owner. A module that WhatsApps a district
manager 76,094 rows is not a follow-up, it is a denial of service. So a follow-up is per
(manager x queue) with an age band, and the escalation — work past the configured age also reaching
the manager ABOVE the owner — is what makes it accountability rather than a newsletter.

WHAT THIS PINS
  A. the config is per-tenant with house defaults, and a bad value degrades to the default rather
     than to "never follow up" or "follow up on everything";
  B. the follow-up VOCABULARY is dereferenced from the one compliance registry — this module does
     not get to decide what a pending job is;
  C. ageing — and that an age we cannot read is UNKNOWN, never 0: a 324-day-old row must not hide
     in the freshest bucket;
  D. the roll-up: per (store x queue), unowned work counted rather than dropped, and `oldest_days`
     None rather than 0 when nothing in a group could be aged;
  E. escalation: past the age it escalates, an unknown age never does, and escalated work sorts first;
  F. the digest — both renderings carry the same facts, the unowned footer is always present when
     there is unowned work, and a queue that could not be read is named;
  G. the dedup key is (store, queue, age BAND), so a follow-up re-escalates on ageing but not on a
     count ticking by one;
  H. migration 1044 is tied to the code it configures, and is OFF by default;
  I. no second fan-out / dedup / scheduler, RULE TWO, and the locks are ARMED.

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_manager_followup.py`
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


from app.modules.commcalc import manager_followup as F      # noqa: E402
from app.modules.commcalc import manager_digest as MD       # noqa: E402
from app.modules.commcalc import compliance_summary as CS   # noqa: E402


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


TODAY = "2026-10-03"

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. config: per-tenant, house defaults, bad values degrade ==")
d = F.resolve_config(None)
check("A1 no tenant row -> the house defaults", d["enabled"] is False and d["send_time"] == "10:30"
      and d["escalate_after_days"] == 30 and d["show_oldest"] == 5 and d["min_items"] == 1, d)
check("A2 OFF by default, so nothing sends on deploy", F.HOUSE_CONFIG["enabled"] is False)
check("A3 the default time is the mig-433 convention, not a number chosen here",
      F.HOUSE_CONFIG["send_time"] == MD.DEFAULT_ALERT_TIME == "10:30")
check("A4 the owner asked for WhatsApp, so WhatsApp is a default channel",
      "whatsapp" in F.HOUSE_CONFIG["channels"] and "email" in F.HOUSE_CONFIG["channels"])
check("A5 a tenant's own values win",
      F.resolve_config({"manager_followup_enabled": True, "manager_followup_time": "07:15",
                        "manager_followup_escalate_after_days": 14,
                        "manager_followup_show_oldest": 3, "manager_followup_min_items": 25})
      == {**F.HOUSE_CONFIG, "enabled": True, "send_time": "07:15", "escalate_after_days": 14,
          "show_oldest": 3, "min_items": 25, "channels": MD.normalize_channels(
              F.HOUSE_CONFIG["channels"])})
for bad in ("", "nonsense", "99:99", None, "10"):
    check(f"A6 an unparseable time {bad!r} degrades to the default, never to 'never follow up'",
          F.resolve_config({"manager_followup_time": bad})["send_time"] == "10:30")
for bad in ("x", None, "", 0, -3):
    check(f"A7 an unusable escalation age {bad!r} degrades to 30 days, never to 0 "
          f"(which would escalate every item the day it opens)",
          F.resolve_config({"manager_followup_escalate_after_days": bad})["escalate_after_days"]
          == 30)
check("A8 an absurd escalation age is clamped to a year rather than disabling escalation",
      F.resolve_config({"manager_followup_escalate_after_days": 99999})["escalate_after_days"]
      == 365)
check("A9 show_oldest is clamped, so a digest can never name 10,000 items on a phone",
      F.resolve_config({"manager_followup_show_oldest": 9999})["show_oldest"] == 25
      and F.resolve_config({"manager_followup_show_oldest": 0})["show_oldest"] == 5)
check("A10 min_items never falls to 0, which would follow up on a manager with nothing pending",
      F.resolve_config({"manager_followup_min_items": 0})["min_items"] == 1)
check("A11 channels come from the ONE vocabulary, so a typo cannot invent a channel",
      F.resolve_config({"manager_followup_channels": ["sms", "email"]})["channels"] == ("email",))
check("A12 an empty channel list degrades to the house channels, never to 'reach nobody'",
      F.resolve_config({"manager_followup_channels": []})["channels"]
      == MD.normalize_channels(F.HOUSE_CONFIG["channels"]))
check("A13 the channel order is CANONICAL, not the order it was typed in -- so two tenants that "
      "listed the same channels differently cannot behave differently",
      F.resolve_config({"manager_followup_channels": ["whatsapp", "email"]})["channels"]
      == F.resolve_config({"manager_followup_channels": ["email", "whatsapp"]})["channels"])

print("\n== B. the vocabulary is DEREFERENCED, never a second list ==")
check("B1 sources() IS the compliance registry, in its own order", F.sources() == tuple(CS.CATEGORIES))
check("B2 ... so a queue added to the registry appears here with no change to this module",
      len(F.sources()) == len(CS.CATEGORIES) >= 10)
check("B3 the labels a digest prints come from the registry, never spelled here",
      F.source_labels() == {k: lbl for (k, lbl, _h, _d) in CS.CATEGORIES})
_fu_text = code_text("app/modules/commcalc/manager_followup.py")
check("B4 LOCK: this module holds no literal queue label of its own -- a second list of what counts "
      "as a pending job is the duplicate defect the index rules forbid",
      not any(f'"{lbl}"' in _fu_text or f"'{lbl}'" in _fu_text
              for (_k, lbl, _h, _d) in CS.CATEGORIES))
check("B5 ... and it does not hold the registry's KEYS either",
      not any(f'"{k}"' in _fu_text for (k, _l, _h, _d) in CS.CATEGORIES))
check("B6 the queue's own page is carried through, so the digest links the list rather than "
      "reprinting 76,094 rows",
      all(isinstance(h, str) and h for (_k, _l, h, _d) in F.sources()))

print("\n== C. ageing — an age we cannot read is UNKNOWN, never 0 ==")
check("C1 whole days between two dates", F.age_days("2026-09-03", TODAY) == 30)
check("C2 a timestamp is read by its date part", F.age_days("2026-09-03T18:22:01Z", TODAY) == 30)
for bad in (None, "", "not-a-date", "2026-13-45"):
    check(f"C3 an unreadable opened-at {bad!r} ages to None, never to 0",
          F.age_days(bad, TODAY) is None)
check("C4 ... and None bands as UNKNOWN, so a row we cannot age never hides in the freshest bucket",
      F.age_band(None) == F.BAND_UNKNOWN and F.BAND_UNKNOWN != F.AGE_BANDS[0][0])
check("C5 a future-dated row (clock skew) is unknown, not fresh", F.age_band(-4) == F.BAND_UNKNOWN)
check("C6 the bands partition the ages with no gap and no overlap",
      [F.age_band(n) for n in (0, 7, 8, 30, 31, 60, 61, 90, 91, 324)]
      == ["0-7", "0-7", "8-30", "8-30", "31-60", "31-60", "61-90", "61-90", "90+", "90+"])
check("C7 the live oldest (324 days) lands in the oldest band, which is open-ended",
      F.age_band(324) == "90+" and F.AGE_BANDS[-1][2] is None)
check("C8 control: BAND_UNKNOWN is not one of the numeric bands",
      F.BAND_UNKNOWN not in [lbl for (lbl, _l, _h) in F.AGE_BANDS])

print("\n== D. the roll-up: per (store x queue), nothing dropped in silence ==")
ITEMS = [
    {"source": "commission_flags", "store_code": "B-1598", "opened_at": "2026-09-03",
     "ref": 1, "label": "PORT_OUT_NODATE"},
    {"source": "commission_flags", "store_code": "B-1598", "opened_at": "2025-11-13",
     "ref": 2, "label": "PORT_OUT_NODATE"},
    {"source": "commission_flags", "store_code": "B-1598", "opened_at": None, "ref": 3},
    {"source": "commission_flags", "store_code": "B-6149", "opened_at": "2026-10-01", "ref": 4},
    {"source": "pay_discrepancy", "store_code": "B-1598", "opened_at": "2026-08-01", "ref": 5},
    {"source": "pay_discrepancy", "store_code": "", "opened_at": "2026-08-01", "ref": 6},
    {"source": "pay_discrepancy", "store_code": None, "opened_at": "2026-08-01", "ref": 7},
    {"source": "", "store_code": "B-1598", "opened_at": "2026-08-01", "ref": 8},
]
S = F.summarize(ITEMS, TODAY, config=F.resolve_config(None))
check("D1 every item with a queue is counted", S["totals"]["open"] == 7)
check("D2 an item with NO queue has nothing to follow up on and is not invented a queue for",
      "" not in S["by_source"])
check("D3 work with no store is counted as UNATTRIBUTED, not dropped -- an unowned backlog is the "
      "most important thing a follow-up module can show",
      S["totals"]["unattributed"] == 2 and F.UNATTRIBUTED in S["by_store"])
check("D4 ... and it is NOT counted as a store", S["totals"]["stores"] == 2)
check("D5 the oldest age is the real oldest across every queue",
      S["totals"]["oldest_days"] == F.age_days("2025-11-13", TODAY))
check("D6 a store-and-queue group carries its own count, oldest and bands",
      S["by_store"]["B-1598"]["commission_flags"]["open"] == 3
      and S["by_store"]["B-1598"]["commission_flags"]["bands"][F.BAND_UNKNOWN] == 1)
check("D7 the oldest items are named worst-first and capped at show_oldest",
      [i["ref"] for i in S["by_store"]["B-1598"]["commission_flags"]["oldest_items"]][:2] == [2, 1])
_only_unaged = F.summarize([{"source": "q", "store_code": "S", "opened_at": None}], TODAY)
check("D8 a group where NOTHING could be aged reports oldest_days None, never 0 days -- 0 would "
      "read as 'opened today'",
      _only_unaged["by_source"]["q"]["oldest_days"] is None
      and _only_unaged["totals"]["oldest_days"] is None)
check("D9 an empty queue summarises to zeros rather than raising",
      F.summarize([], TODAY)["totals"]["open"] == 0)
check("D10 a non-dict item is skipped rather than crashing the whole sweep",
      F.summarize([None, "x", {"source": "q", "store_code": "S"}], TODAY)["totals"]["open"] == 1)
check("D11 the per-queue unattributed count is carried on the queue too, so a digest can say WHICH "
      "queue the unowned work is in",
      S["by_source"]["pay_discrepancy"]["unattributed"] == 2
      and S["by_source"]["commission_flags"]["unattributed"] == 0)

print("\n== E. escalation ==")
cfg = F.resolve_config(None)
check("E1 work past the configured age escalates", F.escalated(30, 30) is True
      and F.escalated(31, 30) is True)
check("E2 work younger than it does not", F.escalated(29, 30) is False)
check("E3 an UNKNOWN age never escalates -- chasing a manager over an age we could not read would "
      "send them after work that might be a day old",
      F.escalated(None, 30) is False)
check("E4 ... and a garbage age never escalates either", F.escalated("soon", 30) is False)
check("E5 a zero escalation age cannot make everything escalate on day 0",
      F.escalated(0, 0) is False)
FOLLOW = F.followup_items(S, config=cfg)
check("E6 a follow-up exists per (store x queue)", len(FOLLOW) == 3)
check("E7 UNATTRIBUTED work is NOT a follow-up item -- nobody owns it, so no manager can be "
      "followed up about it",
      all(it["store_code"] != F.UNATTRIBUTED for it in FOLLOW))
check("E8 ... but it stays visible through the totals, which the digest footer prints",
      S["totals"]["unattributed"] == 2)
check("E9 escalated work sorts first, then oldest, then biggest",
      FOLLOW[0]["escalated"] is True and FOLLOW[0]["store_code"] == "B-1598"
      and FOLLOW[0]["source"] == "commission_flags")
check("E10 each item carries the band the dedup key is built from",
      all(it["band"] == F.age_band(it["oldest_days"]) for it in FOLLOW))
check("E11 min_items is a floor on the group, so a one-item store can be left out",
      [it["store_code"] for it in F.followup_items(S, config={**cfg, "min_items": 3})]
      == ["B-1598"])
check("E12 control: with min_items at the house 1, nothing is filtered",
      len(F.followup_items(S, config={**cfg, "min_items": 1})) == 3)

print("\n== F. the digest: two renderings, the same facts, nothing hidden ==")
dg = F.build_digest("Abid", FOLLOW, totals=S["totals"], labels=F.source_labels(),
                    link="https://app.example/commcalc/manager-followup",
                    unavailable=["ops_chargebacks"])
check("F1 the subject leads with the size of the backlog and the escalation count",
      "5 open items" in dg["subject"] and "past escalation" in dg["subject"], dg["subject"])
check("F2 the WhatsApp body names the stores and queues, not raw keys",
      "B-1598" in dg["text"] and "Commission flags" in dg["text"]
      and "commission_flags" not in dg["text"], dg["text"])
check("F3 the email names them too", "B-1598" in dg["html"] and "Commission flags" in dg["html"])
check("F4 BOTH renderings carry the unowned footer -- two renderings of one digest that disagree "
      "is a digest nobody trusts",
      "cannot be assigned" in dg["text"] and "cannot be assigned" in dg["html"])
check("F5 a queue that could not be read is NAMED, so nobody reads the board as 'that is everything'",
      "could not be checked" in dg["html"])
check("F6 the full list is a link, not 76,094 lines in a WhatsApp message",
      "manager-followup" in dg["text"] and len(dg["text"].splitlines()) < 20)
check("F7 the oldest age is stated so the reader knows how bad it is",
      "oldest" in dg["text"].lower() and "oldest" in dg["html"].lower())
check("F8 an UNKNOWN age renders as unknown, never as 0 days",
      "age unknown" in F.build_digest(
          "X", F.followup_items(F.summarize(
              [{"source": "commission_flags", "store_code": "S", "opened_at": None}] * 2,
              TODAY), config=cfg))["html"])
_one = F.build_digest("X", [FOLLOW[0]], totals={"unattributed": 1})
check("F9 one item reads as one item in both renderings (no '1 items', no '1 item carry')",
      "1 items" not in _one["html"] and "item carry" not in _one["html"]
      and "carries no store" in _one["html"])
check("F10 no unowned work means no unowned footer -- the module does not cry wolf",
      "cannot be assigned" not in F.build_digest("X", FOLLOW, totals={"unattributed": 0})["html"])
check("F11 an unnamed recipient still gets a sane greeting rather than 'Hello None'",
      "Hello there" in F.build_digest(None, FOLLOW)["html"])
check("F12 the digest never prints an org id or a raw ref key to the reader",
      "org_id" not in dg["html"] and "ref_key" not in dg["html"])

print("\n== G. the dedup key: ageing is news, a count ticking by one is not ==")
k = F.key_parts(FOLLOW[0])
check("G1 the key is (store, queue, age band)",
      k == (FOLLOW[0]["store_code"], FOLLOW[0]["source"], FOLLOW[0]["band"]))
check("G2 the same work in the same band tomorrow is the SAME key, so it is not re-sent daily",
      F.key_parts({**FOLLOW[0], "open": FOLLOW[0]["open"] + 1}) == k)
check("G3 the same work that has AGED into an older band is a NEW key, so it re-escalates -- which "
      "is the whole point of a follow-up",
      F.key_parts({**FOLLOW[0], "band": "0-7"}) != k
      and FOLLOW[0]["band"] == "90+")
check("G4 a different queue at the same store is its own follow-up",
      F.key_parts({**FOLLOW[0], "source": "pay_discrepancy"}) != k)
check("G5 the key has no date in it, so one day's digest cannot be resent by changing the clock",
      TODAY not in [str(p) for p in k])
check("G6 a missing part is None rather than a crash, so a malformed item cannot kill the sweep",
      F.key_parts({}) == (None, None, None) and F.key_parts(None) == (None, None, None))

print("\n== H. migration 1044 ==")
_m = open("../database/migrations/1044_manager_followup.sql").read()
check("H1 every knob the owner's 'nothing hardcoded' requires is a column", all(
    c in _m for c in ("manager_followup_enabled", "manager_followup_time",
                      "manager_followup_channels", "manager_followup_escalate_after_days",
                      "manager_followup_show_oldest", "manager_followup_min_items")))
check("H2 additive and idempotent", _m.count("ADD COLUMN IF NOT EXISTS") >= 6)
check("H3 OFF by default, so nothing sends on deploy", "DEFAULT false" in _m)
check("H4 the time default is 10:30 and WhatsApp is a default channel",
      "DEFAULT '10:30'" in _m and '"whatsapp"' in _m)
check("H5 it declares a REVERT", "-- REVERT:" in _m)
check("H6 it introduces NO new table and reuses alert_log",
      "CREATE TABLE" not in _m.upper() and "alert_log" in _m)
check("H7 it performs no backfill, moves no money",
      "UPDATE " not in _m.upper().replace("DO UPDATE", "") and "INSERT INTO" not in _m.upper())
check("H8 the cron registration is HOURLY, because the per-tenant minute is compared in the handler",
      "cron.schedule('manager-followup-run-due', '10 * * * *'" in _m)
check("H9 the column names in the migration are the ones resolve_config reads",
      all(c in _fu_text for c in ("manager_followup_enabled", "manager_followup_time",
                                  "manager_followup_channels",
                                  "manager_followup_escalate_after_days",
                                  "manager_followup_show_oldest",
                                  "manager_followup_min_items")))
check("H10 it states the finding it refuses to hide rather than only the happy path",
      "unattributed" in _m and "no store" in _m)
check("H11 the migration number is not already taken by another migration",
      len([p for p in __import__("os").listdir("../database/migrations")
           if p.startswith("1044_")]) == 1)

print("\n== I. no second fan-out / dedup / scheduler, RULE TWO, locks ARMED ==")
_rt = code_text("app/modules/commcalc/router.py")
_blk = _rt.split("_FOLLOWUP_ATTRIBUTED", 1)[-1].split("def get_compliance_summary")[0]
check("I1 the sweep plans through the ONE fan-out, not its own manager loop",
      "_md.plan_digests(" in _blk)
check("I2 ... and does not roll its own DM-∪-above walk", '"above"' not in _blk
      and "org_chain" not in _blk)
check("I3 recipients come from the org tree helper, never a typed list",
      "_managers_above_dm(" in _blk)
check("I4 dedup rows go through the EXISTING alert_log helpers under this module's own scope",
      "_lateness_already_sent(so, oid, _fu.ALERT_SCOPE" in _blk
      and "_lateness_record_sent(so, oid, _fu.ALERT_SCOPE" in _blk)
check("I5 no new alert table is introduced", "alert_log" not in _blk)
check("I6 the due-time rule is the shared one, not a comparison written here",
      "_md.due_now(" in _blk)
check("I7 WhatsApp goes through the window-safe home, NEVER send_text -- a free-form 10:30 send "
      "returns 200 with a wamid and Meta silently drops it (the 2026-08-05 incident)",
      "send_document_detailed" in _blk and "send_text" not in _blk)
check("I8 a dedup row is written only when a channel actually DELIVERED, so an unconfigured "
      "channel cannot mark a follow-up sent and hide it tomorrow",
      "if delivered:" in _blk)
check("I9 the manual trigger DEFAULTS TO A DRY RUN", "dry_run=not send" in _blk)
check("I10 the cron entrypoint is secret-gated", "verify_notify_secret(x_notify_secret)" in _blk)
check("I11 a queue whose read FAILED reports truncated rather than 'there is none' -- absence is "
      "never zero (§47.8)",
      "return rows, True" in _blk)
check("I12 every queue's store string goes through the ONE store resolver, not a key this module "
      "decides -- without it the live follow-ups keyed on addresses resolved NO manager and the "
      "digest was silently empty",
      "_store_code_resolver(client, org_id)" in _blk and "_store_of(" in _blk
      and "return _resolve_store(v) or v" in _blk
      and _blk.count("_store_of((r or {}).get(store_key))") == 1)
check("I13 the board is org-scoped and span-scoped through the shared keyset",
      "require_org(org_id)" in _blk and "scope_keyset(authorization, org_id)" in _blk)
check("I14 ... but items with NO store survive the span filter, because the person who could assign "
      "an owner is exactly who must see them",
      'if not i.get("store_code") or in_keyset(' in _blk)
def _router_const(name):
    """Read a module-level literal out of the router WITHOUT importing it (importing the router
    needs the framework and the spreadsheet libraries — this harness stays DB- and dep-free)."""
    tree = ast.parse(open("app/modules/commcalc/router.py").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


_na = _router_const("_FOLLOWUP_NOT_ATTRIBUTED") or {}
check("I15 every queue this board cannot attribute is reported WITH A REASON, not omitted",
      "not_attributed" in _blk and len(_na) >= 7
      and all(isinstance(v, str) and len(v) > 20 for v in _na.values()), sorted(_na))
check("I15b the unattributable queues are REAL registry keys, so a rename cannot leave a queue "
      "silently unreported",
      set(_na) <= {k for (k, _l, _h, _d) in F.sources()}, sorted(set(_na) - set(F.source_labels())))
check("I15c the three attributed queues plus the unattributable ones account for the WHOLE "
      "registry -- a queue can never be silently neither",
      set(_na) | set(_router_const("_FOLLOWUP_ATTRIBUTED") or ())
      == {k for (k, _l, _h, _d) in F.sources()},
      sorted({k for (k, _l, _h, _d) in F.sources()}
             - set(_na) - set(_router_const("_FOLLOWUP_ATTRIBUTED") or ())))
_lower = _fu_text.lower()
check("I16 control: the scan dropped the prose (the docstring's 'owner ask' is gone)",
      "owner ask" in open("app/modules/commcalc/manager_followup.py").read().lower()
      and "owner ask" not in _lower)
for banned in ("boost", "epay", "vidapay", "cellfonz", "luxelink", "xfinity", "b-1598", "b-6149"):
    check(f"I17 RULE TWO: {banned!r} appears in no executable line of the module",
          banned not in _lower)
check("I18 control: the RULE TWO scan can fail -- a word that IS in the module is found",
      "escalated" in _lower)
check("I19 the module is PURE: no DB, no network, no framework at import time",
      not any(s in _fu_text for s in ("import requests", "supabase", "fastapi", "httpx")))
check("I20 LOCK: the sweep does not build its own digest text -- one renderer, so email and "
      "WhatsApp cannot drift",
      _blk.count("_fu.build_digest(") == 2 and "<table" not in _blk)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
