#!/usr/bin/env python3
"""DB-FREE PROOF — a rebuild never deletes what it cannot replace (index §COMMCALC-41).

THE DEFECT THIS LOCKS, measured on production 2026-10-27. `engine.build_ledger` was run for the
house org. `_build_one_carrier` deleted the carrier's rows and THEN inserted the rebuilt ones, so it
deleted all 11,000 Boost/VIP rows and failed to insert any:

    PGRST204  Could not find the 'rebate_basis' column of 'device_payable_ledger' in the schema cache

`build_ledger` catches per carrier so one bad config cannot abort the rest, so the call returned
normally — HTTP 200, `written: 0` — describing a table it had just emptied. The rows came back only
because a snapshot had been taken first. The cause was that two migrations (1093, 1094) had never
been applied while later-numbered ones had; the CLASS is wider, and is what is locked here: a
delete+insert rebuild turns into a delete whenever the relation refuses the row shape, for any
reason — a dropped column, a renamed one, a revoked grant.

WHAT IS LOCKED
  A  `write_shape` as pure arithmetic: what is missing, and that an UNKNOWN column set refuses
     nothing (an empty relation has nothing to lose, and must not be read as having no columns).
  B  THE ORDER, which is the fix: refuse, then delete, then insert. Proven by driving the real
     `_build_one_carrier` against a recording client — on a refusal NOT ONE delete is issued.
  C  THE REGRESSION, in the exact shape of the live defect: a ledger missing `rebate_basis`.
  D  The refusal reaches a reader — `build_ledger` reports it apart from an ordinary error, and the
     endpoint turns it into a non-2xx instead of a success-shaped 200.
  E  The old behaviour cannot come back: the delete has ONE home and it is not called before the
     check.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.payables import write_shape as ws                      # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE_PY = os.path.join(ROOT, "app", "modules", "payables", "engine.py")
ROUTER_PY = os.path.join(ROOT, "app", "modules", "payables", "router.py")
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def section(t):
    print("\n%s\n%s" % (t, "-" * len(t)))


# ── the recording client: no sockets, no credentials, no supabase ────────────────────────────────
#: The live ledger's columns as production carried them on 2026-10-27 — `rebate_basis` absent, which
#: is the whole defect. Spelled once, used by every fixture below.
LEDGER_COLUMNS_AS_SHIPPED = [
    "id", "org_id", "carrier_id", "imei", "store", "device_model", "status", "owed", "net_owed",
    "due_date", "due_source", "owed_source", "sold_date", "sold_flag", "priority", "raw",
    "rebate_amount", "rebate_date", "rebate_source", "rebate_mismatch", "epay_rebate_amount",
    "net_offset", "invoice_date", "invoice_source", "bill_path", "billing_friday",
    "window_start", "window_end", "built_at",
]
#: What the current engine writes. The one key the shipped table has no column for.
MISSING_ON_LIVE = "rebate_basis"


class Table:
    def __init__(self, client, name):
        self.c, self.name, self._sel, self._limit = client, name, None, None

    def select(self, *_a, **_k):
        self._sel = True
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, lo, hi):
        self._lo, self._hi = lo, hi
        return self

    def eq(self, *_a):
        return self

    def gt(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def delete(self):
        self.c.log.append(("delete", self.name))
        return self

    def insert(self, rows):
        self.c.log.append(("insert", self.name, len(rows)))
        bad = [k for k in (rows[0] if rows else {}) if k not in self.c.columns.get(self.name, [])]
        if bad and self.name in self.c.columns:
            # the real PostgREST failure, in its real shape
            raise Exception("{'message': \"Could not find the '%s' column of '%s' in the schema "
                            "cache\", 'code': 'PGRST204'}" % (bad[0], self.name))
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.c.data.get(self.name, [])
        if self._limit:
            r.data = r.data[:self._limit]
        return r


class Client:
    """Records every delete and insert, and answers reads from `data`."""

    def __init__(self, data=None, columns=None):
        self.data, self.columns, self.log = dict(data or {}), dict(columns or {}), []

    def schema(self, _s):
        return self

    def table(self, name):
        return Table(self, name)

    def deletes(self):
        return [e for e in self.log if e[0] == "delete"]

    def inserts(self):
        return [e for e in self.log if e[0] == "insert"]


def ledger_sample(columns):
    """One row of the ledger as the relation would return it — every column present, values NULL."""
    return {c: None for c in columns}


# ══ §A — write_shape as pure arithmetic ══════════════════════════════════════════════════════════
section("§A  WHAT IS MISSING, AND WHAT AN UNKNOWN COLUMN SET MAY REFUSE (nothing)")

LIVE = set(LEDGER_COLUMNS_AS_SHIPPED)
ROW = {c: None for c in LEDGER_COLUMNS_AS_SHIPPED}
ROW[MISSING_ON_LIVE] = "asset_ledger"

check("A1 a key the relation has no column for is named",
      ws.missing_columns(ROW, LIVE) == [MISSING_ON_LIVE], ws.missing_columns(ROW, LIVE))
check("A2 a row the relation can take is not refused",
      ws.missing_columns({c: None for c in LEDGER_COLUMNS_AS_SHIPPED}, LIVE) == [])
check("A3 a row with FEWER keys than the relation has columns is fine — a rebuild need not fill "
      "every column, and must not be refused for leaving one out",
      ws.missing_columns({"id": 1, "org_id": "x"}, LIVE) == [])
check("A4 an UNKNOWN column set (None) refuses nothing — 'I could not look' must not read the same "
      "as 'it has no columns', or every rebuild would be refused forever",
      ws.missing_columns(ROW, None) == [] and ws.missing_columns(ROW, set()) == [])
check("A5 several missing columns are ALL named, sorted, so one apply fixes the lot rather than "
      "one rebuild per missing column",
      ws.missing_columns(dict(ROW, zzz=1, aaa=2), LIVE) == ["aaa", MISSING_ON_LIVE, "zzz"],
      ws.missing_columns(dict(ROW, zzz=1, aaa=2), LIVE))

check("A6 an EMPTY relation answers None, not an empty set — it has nothing to lose, so the build "
      "proceeds exactly as before",
      ws.live_columns(Client({"device_payable_ledger": []}), "device_payable_ledger") is None)
check("A7 a NON-EMPTY relation answers its columns, read from the row's KEYS — a NULL column is "
      "still a column, so a sparse sample cannot cause a false refusal",
      ws.live_columns(Client({"device_payable_ledger": [ledger_sample(LEDGER_COLUMNS_AS_SHIPPED)]}),
                      "device_payable_ledger") == LIVE)


class Exploding(Client):
    def table(self, name):                               # the relation cannot be read at all
        raise Exception("permission denied for table %s" % name)


check("A8 a relation that cannot be READ answers None rather than raising — the guard may refuse a "
      "rebuild, it may never be the thing that breaks one",
      ws.live_columns(Exploding(), "device_payable_ledger") is None)

try:
    ws.refuse_if_unwritable(
        Client({"device_payable_ledger": [ledger_sample(LEDGER_COLUMNS_AS_SHIPPED)]}),
        "device_payable_ledger", ROW, label="Boost / VIP")
    _raised = None
except ws.WriteShapeRefused as e:
    _raised = e
check("A9 the refusal names the relation, the missing columns and the carrier, so the reader is "
      "told WHICH declaration is absent rather than that the rebuild is broken",
      _raised is not None and _raised.relation == "device_payable_ledger"
      and _raised.missing == [MISSING_ON_LIVE] and "Boost / VIP" in str(_raised)
      and "Nothing was deleted" in str(_raised), _raised)


# ══ §B — THE ORDER, driven through the real _build_one_carrier ═══════════════════════════════════
section("§B  REFUSE, THEN DELETE, THEN INSERT — proven on the real build, not on a copy of it")

from app.modules.payables import engine                                  # noqa: E402

CFG = {"carrier_id": "C1", "label": "A distributor", "source_table": "src_feed",
       "imei_field": "imei", "owed_field": "owed", "store_field": "store"}


def drive(ledger_columns, source_rows, row_keys, holds_rows=True):
    """Run the REAL `_build_one_carrier` with its row-building stubbed to a known shape.

    Only `_rows_for_page` is stubbed — everything the fix touches (the order of the delete, the
    preflight, the sweep) is the shipped code. Returns the recording client.

    `ledger_columns` is what the RELATION has; `holds_rows` is whether it currently holds any. The
    two are deliberately separate: an empty table still has its columns, and conflating the two is
    how a fixture would "prove" a guard that real life never exercises.
    """
    c = Client({"device_payable_ledger": ([ledger_sample(ledger_columns)] if holds_rows
                                          else []),
                "src_feed": source_rows},
               {"device_payable_ledger": ledger_columns or []})
    saved = engine._rows_for_page
    engine._rows_for_page = lambda *a, **k: [dict.fromkeys(row_keys) | {"status": "open"}
                                             for _ in range(len(source_rows))]
    c.refused = c.exploded = None
    try:
        engine._build_one_carrier(c, "ORG", CFG, None, 0, {}, {}, complaints=None)
    except ws.WriteShapeRefused as e:
        c.refused = e
    except Exception as e:
        # The relation's own refusal, reaching the caller AFTER the delete — i.e. exactly the
        # pre-fix behaviour. Recorded rather than raised so this harness REPORTS the regression
        # instead of dying on it: a lock that crashes names nothing.
        c.exploded = e
    finally:
        engine._rows_for_page = saved
    return c


GOOD_KEYS = ["status", "org_id", "carrier_id", "imei"]
BAD_KEYS = GOOD_KEYS + [MISSING_ON_LIVE]

bad = drive(LEDGER_COLUMNS_AS_SHIPPED, [{"imei": "1", "owed": 1}], BAD_KEYS)
check("B1 THE FIX: when the relation cannot take the row, the rebuild is refused and NOT ONE "
      "delete is issued — the carrier's rows are exactly as they were",
      bad.refused is not None and bad.exploded is None and bad.deletes() == [],
      (bad.log, repr(bad.exploded)[:160]))
check("B2 …and nothing was inserted either, so the table is untouched in both directions",
      bad.inserts() == [], bad.log)

good = drive(LEDGER_COLUMNS_AS_SHIPPED, [{"imei": "1", "owed": 1}], GOOD_KEYS)
check("B3 when the relation CAN take the row, the rebuild proceeds — one delete, then the insert",
      good.refused is None and len(good.deletes()) == 1 and len(good.inserts()) == 1, good.log)
check("B4 …and the DELETE COMES FIRST, before any insert — a rebuild replaces a snapshot, it must "
      "never append to one",
      [e[0] for e in good.log if e[0] in ("delete", "insert")][:2] == ["delete", "insert"], good.log)

empty_src = drive(LEDGER_COLUMNS_AS_SHIPPED, [], GOOD_KEYS)
check("B5 a carrier whose source carries nothing still has its stale snapshot cleared — the "
      "pre-fix answer, reached without ever risking a delete it could not follow through",
      empty_src.refused is None and len(empty_src.deletes()) == 1
      and empty_src.inserts() == [], empty_src.log)

empty_ledger = drive(LEDGER_COLUMNS_AS_SHIPPED + [MISSING_ON_LIVE],
                     [{"imei": "1", "owed": 1}], BAD_KEYS, holds_rows=False)
check("B6 an EMPTY ledger proceeds — its column set cannot be sampled, and there is nothing to "
      "lose; a guard that blocked the first build of a fresh environment would be worse than the "
      "defect it prevents",
      empty_ledger.refused is None and len(empty_ledger.deletes()) == 1
      and len(empty_ledger.inserts()) == 1, empty_ledger.log)

many = drive(LEDGER_COLUMNS_AS_SHIPPED, [{"imei": str(i), "owed": 1} for i in range(3)], GOOD_KEYS)
check("B7 the snapshot is swept ONCE for the carrier, not once per page — a second sweep would "
      "delete the rows the first page just wrote",
      len(many.deletes()) == 1, many.log)


# ══ §C — THE REGRESSION, in the live defect's exact shape ════════════════════════════════════════
section("§C  THE 2026-10-27 SHAPE: a ledger with no `rebate_basis` column")

check("C1 the engine really does WRITE that column into the ledger row — if it stops, this "
      "fixture stops describing anything and must be re-pointed rather than quietly passing",
      '"%s": ' % MISSING_ON_LIVE in open(ENGINE_PY, encoding="utf-8").read(),
      "the engine no longer writes %s" % MISSING_ON_LIVE)
check("C2 driving that exact shape refuses, names the column, and deletes nothing — the 11,000 "
      "rows survive",
      bad.refused.missing == [MISSING_ON_LIVE] and bad.deletes() == [], bad.refused)


# ══ §D — THE REFUSAL REACHES A READER ════════════════════════════════════════════════════════════
section("§D  A REFUSED REBUILD IS REPORTED AS ONE, AND IS NOT A 200")

saved_build = engine._build_one_carrier
saved_maps = engine._load_source_maps
saved_terms = engine._tenant_priority_pct
saved_alias = engine._load_model_alias
saved_reimb = engine._reimb_types
engine._load_source_maps = lambda *a, **k: [CFG]
engine._tenant_priority_pct = lambda *a, **k: 0
engine._load_model_alias = lambda *a, **k: {}
engine._reimb_types = lambda *a, **k: {}
engine._build_one_carrier = lambda *a, **k: (_ for _ in ()).throw(
    ws.WriteShapeRefused("device_payable_ledger", [MISSING_ON_LIVE], "A distributor"))
RES = engine.build_ledger(Client(), "ORG")
engine._build_one_carrier, engine._load_source_maps = saved_build, saved_maps
engine._tenant_priority_pct, engine._load_model_alias = saved_terms, saved_alias
engine._reimb_types = saved_reimb

check("D1 a block is reported APART from an ordinary error — 'this carrier rebuilt badly' and "
      "'this carrier was left exactly as it was' are different sentences",
      len(RES["blocked"]) == 1 and RES["blocked"][0]["missing_columns"] == [MISSING_ON_LIVE],
      RES.get("blocked"))
check("D2 …and the per-carrier entry says the rows are intact, so nobody reads `rows: 0` as loss",
      RES["per_carrier"][0].get("blocked") is True
      and RES["per_carrier"][0].get("rows_left_intact") is True, RES["per_carrier"])
check("D3 a blocked carrier is NOT counted as rebuilt — `carriers` and `written` stay at zero",
      RES["carriers"] == 0 and RES["written"] == 0, (RES["carriers"], RES["written"]))

RCODE = open(ROUTER_PY, encoding="utf-8").read()
check("D4 the endpoint turns a block into a NON-2xx — the other half of the defect was a 200 "
      "describing an emptied table, and a screen must not be able to render 'rebuilt' over it",
      'if built.get("blocked")' in RCODE and "HTTPException(409" in RCODE)
check("D5 …and it does so BEFORE the flags refresh, so a refused rebuild cannot go on to restate "
      "the discrepancy flags off a ledger it did not write",
      RCODE.index('if built.get("blocked")') < RCODE.index("sync_payable_flags"))


# ══ §E — THE OLD BEHAVIOUR CANNOT COME BACK ══════════════════════════════════════════════════════
section("§E  THE DELETE HAS ONE HOME, AND IT IS NOT REACHED BEFORE THE CHECK")

SRC = open(ENGINE_PY, encoding="utf-8").read()
TREE = ast.parse(SRC)


def fn(name):
    for n in ast.walk(TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def calls_in(node):
    return [n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]


def deletes_in(node):
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "delete"]


check("E1 the ledger delete has exactly ONE home (`_sweep_carrier`) — a second copy is how the "
      "order gets restored by accident",
      len(deletes_in(fn("_sweep_carrier"))) == 1
      and deletes_in(fn("_build_one_carrier")) == [], len(deletes_in(fn("_build_one_carrier"))))

BUILD = fn("_build_one_carrier")
order = [c for c in calls_in(BUILD) if c in ("_sweep_carrier",)]
attrs = [n.func.attr for n in ast.walk(BUILD)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and n.func.attr in ("refuse_if_unwritable",)]
check("E2 `_build_one_carrier` asks `write_shape.refuse_if_unwritable` — the guard is WIRED, not "
      "merely written (three registries in this repo were written and never dereferenced, §19.18)",
      attrs.count("refuse_if_unwritable") == 1, attrs)
check("E3 …and the refusal is raised by the module whose job it is, so a caller cannot soften it "
      "into a warning by catching the wrong class",
      "WriteShapeRefused" in SRC and "except _ws.WriteShapeRefused" in SRC)

body = SRC[SRC.index("def _build_one_carrier"):SRC.index("def _sweep_carrier")]
check("E4 THE RATCHET: inside the build, the preflight call appears BEFORE the sweep call — the "
      "single line of source order that the 11,000 rows depended on",
      body.index("refuse_if_unwritable") < body.index("_sweep_carrier("),
      (body.index("refuse_if_unwritable"), body.index("_sweep_carrier(")))

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
