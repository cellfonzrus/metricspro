"""HARNESS — control-box BOARD ASSEMBLY (the I/O layer), with a fake Supabase client.

harness_control_box.py proves the pure decisions. This proves the layer that feeds them, which is
where the claims that matter to the owner actually live:

  A. THE BOARD COMPOSES, IT DOES NOT RE-DERIVE. The lamps come from the platform's EXISTING
     mechanisms — `core.import_health` attention providers and `commcalc.portal_session_health` —
     and a module that registers a NEW attention provider gains a lamp with no code change and no
     migration (the RULE TWO / duplicate-check claim, exercised by registering one mid-test).

  B. THE REGISTRY IS CONFIG, NOT CODE. A `core.system_check` row overlays the code default: the
     HOUSE row sets the platform-wide value, this tenant's row overrides it again, a row can switch a
     check OFF (⇒ unmonitored, never green), and a row can DECLARE a check no code knows about.

  C. HONESTY UNDER I/O FAILURE. A provider that raised, a heavy provider that was deferred, a probe
     whose table is missing, and a tenant with an automation switched off each produce a lamp that
     says what it does not know — never a green one.

  D. COVERAGE + THE SELF-CHECK ROW are on every board: the fraction actually measured, and the
     board's own daily-run freshness (a stopped watchman reads red instead of leaving stale greens).

No DB, no network: a FakeClient answers `.schema().table().select()…execute()` from in-memory rows,
the same shape scratchpad/luxelink_sales_flow_proof.py and harness_cross_tenant_isolation.py use.

Run:  cd backend && python3 harness_control_box_board.py      (exit 0 = all pass)
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core import control_box as cbx                 # noqa: E402
from app.modules.core import control_box_api as api             # noqa: E402
from app.modules.core import import_health as ih                # noqa: E402

PASS = FAIL = 0
NOW = datetime.now(timezone.utc)
HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "22222222-2222-2222-2222-222222222222"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s   %s" % (name, extra))


def ago(h):
    return (NOW - timedelta(hours=h)).isoformat()


def lamp_of(board, key):
    return next((c["lamp"] for c in board["checks"] if c["key"] == key), "<missing>")


def row_of(board, key):
    return next((c for c in board["checks"] if c["key"] == key), None)


# ── fake supabase client ─────────────────────────────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows, missing=False):
        self._rows = list(rows)
        self._missing = missing
        self._order = None
        self._desc = False
        self._limit = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self._rows = [r for r in self._rows if r.get(col) in set(vals)]
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._missing:
            raise RuntimeError("relation does not exist")
        rows = self._rows
        if self._order:
            rows = sorted(rows, key=lambda r: (r.get(self._order) is None, r.get(self._order) or ""),
                          reverse=self._desc)
        return FakeResult(rows[:self._limit] if self._limit else rows)

    def insert(self, row):
        return self

    def upsert(self, row, on_conflict=None):
        return self


class FakeSchema:
    def __init__(self, tables, schema):
        self._t, self._s = tables, schema

    def table(self, name):
        keyed = "%s.%s" % (self._s, name)
        if keyed not in self._t:
            return FakeQuery([], missing=True)      # table absent ⇒ the probe must report, not pass
        return FakeQuery(self._t[keyed])


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, s):
        return FakeSchema(self._t, s)


BASE_TABLES = {
    "storeops.tenants": [{"org_id": HOUSE, "name": "House", "is_active": True},
                         {"org_id": ORG_B, "name": "Tenant B", "is_active": True}],
    "commcalc.data_source": [],
    "commcalc.email_sweep_config": [{"org_id": HOUSE, "enabled": True, "last_run_at": ago(2)}],
    "storeops.google_review_sweep_config": [{"org_id": HOUSE, "enabled": True, "last_run_at": ago(2)}],
    "commcalc.account_statements": [{"org_id": HOUSE, "computed_at": ago(1)}],
    "core.system_check": [],
    "core.system_check_state": [{"org_id": HOUSE, "enabled": True, "last_run_at": ago(3)}],
    "core.system_check_run": [],
    "core.ai_budget_config": [],
    "core.ai_call_audit": [],
}


def tables(**over):
    t = {k: list(v) for k, v in BASE_TABLES.items()}
    t.update(over)
    return t


# ── stub the attention aggregator so the board's composition is observable ───────────────────────
_ATT = {"items": [], "deferred": [], "provider_errors": []}
_real_collect = ih.collect_attention


def _fake_collect(client, org_id, deep=False, feed_h=None):
    if _ATT.get("_raise"):
        raise RuntimeError("aggregator down")
    items = [i for i in _ATT["items"] if not (i.get("_heavy") and not deep)]
    return {"items": items, "deferred": _ATT["deferred"] if not deep else [],
            "provider_errors": _ATT["provider_errors"], "counts": {}, "deep": deep}


ih.collect_attention = _fake_collect

# The board imports collect_attention by name inside the function, so patching the module attribute
# is what the code under test will see. Confirm that before trusting anything below.
_probe = api._attention_evidence(FakeClient(tables()), HOUSE, False)
check("the harness is actually driving the board's aggregator call", _probe[1] is None)


# ══ A. composition: providers become lamps, with no code change ═══════════════════════════════════
print("\nA. the board COMPOSES existing mechanisms (attention providers, portal session health)")

before = len(api._provider_specs())


@ih.register_provider("harness_new_subsystem", label="A brand-new subsystem", group="ops",
                      cost="cheap")
def _p_new(client, org_id, ctx):
    return []


after = api._provider_specs()
check("registering a NEW attention provider adds a lamp with no code change here",
      len(after) == before + 1
      and any(s["key"] == "attention.harness_new_subsystem" for s in after),
      "%d -> %d" % (before, len(after)))
check("...and it inherits the provider's own label and group",
      next(s for s in after if s["key"] == "attention.harness_new_subsystem")["label"]
      == "A brand-new subsystem")

_ATT["items"] = []
board = api.build_board(FakeClient(tables()), HOUSE)
check("a provider with zero open items is GREEN on the board",
      lamp_of(board, "attention.harness_new_subsystem") == "green")

_ATT["items"] = [{"provider": "harness_new_subsystem", "severity": "error",
                  "label": "Two things broke", "detail": "d", "count": 2}]
board = api.build_board(FakeClient(tables()), HOUSE)
check("an ERROR item from that provider turns its lamp red",
      lamp_of(board, "attention.harness_new_subsystem") == "red")
check("...and the whole board's headline goes red", board["lamp"] == "red", board["headline"])
check("...and the row carries the module deep link the owner asked for",
      row_of(board, "attention.harness_new_subsystem")["deep_link"] == "/admin/import-health")

_ATT["items"] = [{"provider": "harness_new_subsystem", "severity": "warning", "label": "late",
                  "detail": "d", "count": 1}]
board = api.build_board(FakeClient(tables()), HOUSE)
check("a WARNING item is amber, not red", lamp_of(board, "attention.harness_new_subsystem") == "amber")
check("...and one amber does not make the board red", board["lamp"] == "amber", board["lamp"])

# portal sessions come from the SAME pair the existing /merchant-portals/health endpoint uses
_ATT["items"] = []
ds = [{"org_id": HOUSE, "id": "s1", "label": "VidaPay login", "processor": "vidapay",
       "session_state": "x", "session_expires_at": ago(5), "auth_status": "ok"}]
board = api.build_board(FakeClient(tables(**{"commcalc.data_source": ds})), HOUSE)
check("an EXPIRED merchant-portal session lights the portal lamp red",
      lamp_of(board, "portal_sessions") == "red", row_of(board, "portal_sessions"))
check("a tenant with NO portal sources reads unmonitored, not green",
      lamp_of(api.build_board(FakeClient(tables()), HOUSE), "portal_sessions") == "unmonitored")

# COVERAGE FIX proven here: merchant_portals.is_portal knows only the three CARD PROCESSORS, so
# filtering by it (as /merchant-portals/health does, correctly, for its own purpose) would leave the
# VidaPay/b2bsoft session logins that drive the nightly commission pull SILENTLY unwatched.
from app.modules.commcalc.merchant_portals import is_portal as _is_portal   # noqa: E402
check("the card-processor filter genuinely does NOT recognise vidapay/b2bsoft",
      not _is_portal("vidapay") and not _is_portal("b2bsoft"))
check("...so a non-card portal login WITH a session is still watched by this board",
      lamp_of(api.build_board(FakeClient(tables(**{"commcalc.data_source": ds})), HOUSE),
              "portal_sessions") == "red")
plain_feed = [{"org_id": HOUSE, "id": "f1", "label": "An FTP feed", "processor": "ftp"}]
check("...while a source with no session state at all is NOT judged by a session rule",
      lamp_of(api.build_board(FakeClient(tables(**{"commcalc.data_source": plain_feed})), HOUSE),
              "portal_sessions") == "unmonitored")

# ══ B. the registry is config, not code ══════════════════════════════════════════════════════════
print("\nB. registry overrides (RULE TWO — config rows beat code defaults)")

cfg_off = [{"org_id": HOUSE, "key": "sched_google_reviews", "enabled": False}]
board = api.build_board(FakeClient(tables(**{"core.system_check": cfg_off})), HOUSE)
check("a config row can switch a check OFF -> unmonitored, never green",
      lamp_of(board, "sched_google_reviews") == "unmonitored")

cfg_declare = [{"org_id": HOUSE, "key": "backup_drill", "kind": "unmonitored",
                "subsystem": "platform", "label": "Backup restore drill",
                "config": {"note": "declared gap"}}]
board = api.build_board(FakeClient(tables(**{"core.system_check": cfg_declare})), HOUSE)
check("a config row can DECLARE a check no code knows about",
      lamp_of(board, "backup_drill") == "unmonitored")
check("...and it is counted in the coverage gap, not hidden",
      "backup_drill" in board["coverage"]["unmonitored_keys"])

cfg_layered = [{"org_id": HOUSE, "key": "sched_email_sweep", "label": "House label"},
               {"org_id": ORG_B, "key": "sched_email_sweep", "label": "Tenant B label"}]
specs_b = api.effective_registry(FakeClient(tables(**{"core.system_check": cfg_layered})), ORG_B)
specs_h = api.effective_registry(FakeClient(tables(**{"core.system_check": cfg_layered})), HOUSE)
check("this tenant's row overrides the HOUSE row",
      next(s for s in specs_b if s["key"] == "sched_email_sweep")["label"] == "Tenant B label")
check("...and the house row still applies to the house tenant",
      next(s for s in specs_h if s["key"] == "sched_email_sweep")["label"] == "House label")
check("a tenant with no rows still gets the full code-derived registry",
      len(api.effective_registry(FakeClient(tables()), ORG_B)) == len(api.default_specs()))

# ══ C. honesty under I/O failure ═════════════════════════════════════════════════════════════════
print("\nC. nothing that failed to measure is allowed to read green")

_ATT["provider_errors"] = [{"key": "harness_new_subsystem", "error": "KeyError('org')"}]
board = api.build_board(FakeClient(tables()), HOUSE)
check("a provider that RAISED -> unknown", lamp_of(board, "attention.harness_new_subsystem") == "unknown")
_ATT["provider_errors"] = []

_ATT["deferred"] = [{"key": "harness_new_subsystem"}]
board = api.build_board(FakeClient(tables()), HOUSE, deep=False)
check("a HEAVY provider not run this pass -> unknown, never green (unmeasured != passing)",
      lamp_of(board, "attention.harness_new_subsystem") == "unknown")
board_deep = api.build_board(FakeClient(tables()), HOUSE, deep=True)
check("...and deep=1 actually runs it", lamp_of(board_deep, "attention.harness_new_subsystem") == "green")
_ATT["deferred"] = []

_ATT["_raise"] = True
board = api.build_board(FakeClient(tables()), HOUSE)
check("the whole aggregator being down makes every provider lamp unknown, not green",
      all(c["lamp"] == "unknown" for c in board["checks"] if c["kind"] == "attention_provider"))
check("...and the board headline is never green in that state", board["lamp"] != "green", board["lamp"])
_ATT["_raise"] = False

no_tbl = tables()
del no_tbl["commcalc.account_statements"]
board = api.build_board(FakeClient(no_tbl), HOUSE)
check("a heartbeat whose source table is missing -> unknown (probe error), never green",
      lamp_of(board, "sched_account_recompute") == "unknown")

off = [{"org_id": HOUSE, "enabled": False, "last_run_at": ago(9999)}]
board = api.build_board(FakeClient(tables(**{"commcalc.email_sweep_config": off})), HOUSE)
check("an automation the tenant switched OFF is unmonitored, not red forever",
      lamp_of(board, "sched_email_sweep") == "unmonitored")

board = api.build_board(FakeClient(tables(**{"commcalc.email_sweep_config": []})), HOUSE)
check("an automation this tenant never set up is unmonitored, not red",
      lamp_of(board, "sched_email_sweep") == "unmonitored")

stopped = [{"org_id": HOUSE, "enabled": True, "last_run_at": ago(200)}]
board = api.build_board(FakeClient(tables(**{"commcalc.email_sweep_config": stopped})), HOUSE)
check("a CONFIGURED automation that stopped producing is RED (the mig-950 failure)",
      lamp_of(board, "sched_email_sweep") == "red")

# ══ D. coverage + the self-check row ═════════════════════════════════════════════════════════════
print("\nD. coverage fraction and the board's row about itself")

board = api.build_board(FakeClient(tables()), HOUSE)
cov = board["coverage"]
check("every board reports registered / monitored / unmonitored",
      cov["registered"] == len(board["checks"]) and cov["monitored"] + cov["unmonitored"] == cov["registered"],
      cov)
check("...and states the fraction in words", "actually measured" in cov["note"], cov["note"])
check("the board carries a row about its OWN daily run",
      row_of(board, "control_box_daily_check") is not None)
check("...green when the daily check ran recently",
      lamp_of(board, "control_box_daily_check") == "green")

dead = [{"org_id": HOUSE, "enabled": True, "last_run_at": ago(300)}]
board = api.build_board(FakeClient(tables(**{"core.system_check_state": dead})), HOUSE)
check("...RED when the daily check has stopped (stale green lamps are the real danger)",
      lamp_of(board, "control_box_daily_check") == "red")
board = api.build_board(FakeClient(tables(**{"core.system_check_state": []})), HOUSE)
check("...RED for a tenant whose daily check has never run",
      lamp_of(board, "control_box_daily_check") == "red")

# every row an operator needs to act is present on every row
board = api.build_board(FakeClient(tables()), HOUSE)
check("every check row carries a lamp, a label and a subsystem",
      all(c.get("lamp") in cbx.LAMPS and c.get("label") and c.get("subsystem") for c in board["checks"]))
check("the board is sorted worst-first",
      [cbx._RANK[c["lamp"]] for c in board["checks"]]
      == sorted([cbx._RANK[c["lamp"]] for c in board["checks"]], reverse=True))

# ── the daily-check universe is TENANTS, not state rows ──────────────────────────────────────────
print("\nE. the daily check covers every tenant, including one never checked")

t = tables()
due = cbx.due_orgs([{"org_id": x["org_id"], **({} if x["org_id"] == ORG_B
                                               else {"last_run_at": ago(3)})}
                    for x in t["storeops.tenants"]], now=None)
check("a tenant with NO state row is due on the first tick (never invisible)",
      ORG_B in [d["org_id"] for d in due], [d["org_id"] for d in due])
check("a tenant checked 3h ago is not due again", HOUSE not in [d["org_id"] for d in due])

b = api.build_board(FakeClient(tables()), ORG_B)
check("a never-checked tenant still produces a full board", len(b["checks"]) == len(board["checks"]))
check("...whose own daily-check row is red", lamp_of(b, "control_box_daily_check") == "red")

ih.collect_attention = _real_collect
print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
