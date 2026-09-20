"""DB-FREE PROOF — a connector the platform will never run is not a BROKEN connector.

OWNER DIRECTIVE 2026-09-20: *"fix the dead connectors for ftp and b2b"*.

WHAT THEY ACTUALLY WERE. Neither was broken, and neither could ever have recovered:

  • B2B — `enabled=true`, and `connector_route_policy` (mig 998) CLOSES its `pull` route: the vendor
    emailed us not to use their 2FA login, so the platform deliberately does not attempt it (owner
    directive 2026-09-09). Its own `last_detail` says exactly that. The pure policy module existed and
    FOUR other call sites already honoured it — `_scan_connector_health` never did. Third instance of
    the §19.18 pattern: a registry written, and a caller left unwired.
  • FTP — `enabled=true` with credentials from 2026-06-26, and NOTHING dispatches it: no
    `connector_instances` row, no `sweep_kind`, no puller in `_sweep_registry()`, no cron.
    "No successful run in 30h" was never going to change, because no tick will ever pick it up.

ONE FACT UNDERNEATH BOTH: **`enabled=true` stopped meaning "this runs"**, and the health scan had no
way to ask. It judged on `enabled` + `last_run_at` + `last_status` alone, so it reported two connectors
that CANNOT run as failing, every day, for months.

MEASURED, house org, before → after (2026-09-20 20:23):

    ALERTED 7/day                              ALERTED 2/day
      Portal login (b2bsoft)  1581h  error       ePay sweep   658h  error   <- real, still alerts
      B2B sweep               1330h  disabled    VIP sweep     54h  stale   <- real, still alerts
      FTP import              2080h  0/0 files
      ePay sweep               658h  error     UNMONITORED (reported, never mailed)
      VIP sweep                 54h  stale       Portal login (b2bsoft) — route closed by config
      DLAR sweep                             the B2B sweep         — route closed by config
      (+ email, when it was down)                FTP import            — nothing dispatches it

Nothing real is silenced: ePay erroring for 658h and VIP 54h stale still alert, which is the whole
point — they were the two that mattered and they were buried.

UNMONITORED is the control box's own word (§23d honesty rules): declared, not checked, never folded
into a green headline. Coverage stays visible on GET /commcalc/connector-health; the daily false alarm
is what stops.

Run:  cd backend && python3 harness_connector_runnable.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.connector_route_policy as CRP

ROUTER = "app/modules/commcalc/router.py"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"got={got!r} want={want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()


def _func_src(rel, name):
    text = _src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


RSRC = _src(ROUTER)

# lift the decision function out, with only the module globals it reads
_ns = {"_CONNECTOR_SLUG_SOURCE": None, "_DISPATCHED_SWEEP_TABLES": None,
       "ORG_ID": "house", "_crp": lambda: CRP}
for name in ("_CONNECTOR_SLUG_SOURCE", "_DISPATCHED_SWEEP_TABLES"):
    for node in ast.parse(RSRC).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            _ns[name] = ast.literal_eval(node.value)
exec(_func_src(ROUTER, "_connector_unrunnable"), _ns)
unrunnable = _ns["_connector_unrunnable"]

ORG = "house"
CLOSED = [{"org_id": ORG, "connector": "b2b", "route": "pull", "allowed": False,
           "reason": "the vendor asked us not to use their 2FA login"},
          {"org_id": ORG, "connector": "b2bsoft", "route": "pull", "allowed": False,
           "reason": "the vendor asked us not to use their 2FA login"}]
ALL_KINDS = {"b2b", "dlar", "epay", "vip", "google_closing"}
REGISTERED = {(ORG, "b2b"), (ORG, "dlar"), (ORG, "epay"), (ORG, "vip")}


def U(table, row, policy=CLOSED, disp=ALL_KINDS, inst=REGISTERED):
    return unrunnable(None, table, {"org_id": ORG, **row}, policy, disp, inst)


print("\n§A  the two the owner named")
ok("A1 B2B: the route the owner CLOSED is why it cannot run",
   bool(U("b2b_sweep_config", {"connector": "b2b", "last_status": "disabled"})))
# The reason must come from the POLICY MODULE, not from a sentence the router made up — otherwise
# the wording drifts from what every other surface shows for the same closed route.
_pol = CRP.resolve(CLOSED, ORG, "b2b")
eq("A2 …and the reason is the policy module's own headline, verbatim",
   U("b2b_sweep_config", {"connector": "b2b"}), CRP.headline(_pol))
ok("A2b …which is a real sentence, not an empty string dressed as one",
   len(CRP.headline(_pol)) > 20 and CRP.is_closed(_pol), CRP.headline(_pol))
ok("A3 B2B's OTHER surface — the data_source portal row — is caught by the same rule",
   bool(U("data_source", {"processor": "b2bsoft", "last_status": "error: session expired"})),
   "one connector, two tables; missing either would leave half the false alarm")
ok("A4 FTP: nothing dispatches it — no sweep kind, no puller, no schedule",
   "dispatches" in U("ftp_sweep_config", {"last_status": "0/0 files ingested"}),
   U("ftp_sweep_config", {}))

print("\n§B  a REAL failure is never silenced — that is the whole point")
eq("B1 ePay erroring is still health-judged (dispatchable + registered)",
   U("epay_sweep_config", {"last_status": "error"}), "")
eq("B2 VIP stale is still health-judged", U("vip_sweep_config", {"last_status": "ok"}), "")
eq("B3 DLAR is still health-judged", U("dlar_sweep_config", {"last_status": "ok"}), "")
eq("B4 the MAILBOX is never called unrunnable — it has its own cron, outside this dispatcher",
   U("email_sweep_config", {"last_status": "login rejected"}), "")
eq("B5 a portal source with an OPEN route is still health-judged",
   U("data_source", {"processor": "payanywhere", "last_status": "error"}), "")

print("\n§C  the two ways a connector becomes unrunnable, independently")
ok("C1 registered + enabled, but its puller vanished from the registry",
   "no puller is registered" in U("epay_sweep_config", {}, disp=ALL_KINDS - {"epay"}))
ok("C2 puller exists, but THIS tenant never registered the connector",
   "no enabled connector registration" in U("epay_sweep_config", {}, inst=set()))
eq("C3 another tenant's registration does not make it runnable here",
   "no enabled connector registration" in U("epay_sweep_config", {}, inst={("other-org", "epay")}), True)
eq("C4 a closed route beats everything — even a perfectly dispatchable connector",
   bool(U("b2b_sweep_config", {"connector": "b2b"}, disp=ALL_KINDS, inst=REGISTERED)), True)

print("\n§D  the refusals — an empty registry must not mute the whole fleet")
eq("D1 NO policy rows (pre-mig-998) ⇒ no route is closed, nothing is muted by policy",
   U("b2b_sweep_config", {"connector": "b2b"}, policy=[]).startswith("no puller"), False)
eq("D2 …and with policy empty, a dispatchable connector stays health-judged",
   U("epay_sweep_config", {}, policy=[]), "")
ok("D3 an unreadable input can never break the scan — the question fails to ''",
   unrunnable(None, "epay_sweep_config", None, CLOSED, ALL_KINDS, REGISTERED) == "")
eq("D4 an unknown table is left alone (no table, no opinion)",
   U("some_future_config", {}), "")
ok("D5 RULE TWO — the slug map names COLUMNS, and carries no tenant in it",
   all(k.endswith("_config") or k == "data_source" for k in _ns["_CONNECTOR_SLUG_SOURCE"])
   and not any("luxelink" in str(v).lower() or "boost" in str(v).lower()
               for v in _ns["_CONNECTOR_SLUG_SOURCE"].values()))

print("\n§E  reported, never mailed — and the real ones still are")
SCAN = _func_src(ROUTER, "_scan_connector_health")
ok("E1 the scan asks runnability BEFORE judging health",
   SCAN.index("_connector_unrunnable(") < SCAN.index('failed = ("error" in status)'))
ok("E2 an unrunnable connector is emitted as `unmonitored`, not errored/stalled",
   '"kind": "unmonitored"' in SCAN)
ok("E3 …and flagged not-alertable, while a real failure is flagged alertable",
   '"alertable": False' in SCAN and '"alertable": True' in SCAN)
ok("E4 its ref_key carries no episode date — it is a standing state, not an incident",
   'unmonitored"' in SCAN and "since-{since}:{kind}" in SCAN)
DUE = _func_src(ROUTER, "connector_health_run_due")
ok("E5 the alert cron mails ONLY the alertable ones",
   'if f.get("alertable")' in DUE)
ok("E6 …and still REPORTS the unmonitored ones, so coverage never goes invisible",
   '"unmonitored":' in DUE and "not f.get(\"alertable\")" in DUE)
ok("E7 the three registries are read ONCE for the whole cross-tenant pass",
   SCAN.count("connector_route_policy") == 1 and SCAN.count("_sweep_registry()") == 1
   and SCAN.count("connector_instances") == 1)
ok("E8 each read degrades to empty, restoring the previous behaviour exactly",
   SCAN.count("except Exception:") >= 4)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
