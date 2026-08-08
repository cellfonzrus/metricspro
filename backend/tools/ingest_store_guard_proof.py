"""PROOF — cross-tenant ingest guard (owner-approved 2026-08-06).

Offline, stub client. Replays the ACTUAL 2026-07-14 incident shape: a batch of house sales rows with
six Luxelink line items for `4640-A W Diversey Ave` mixed in, against the house org's real known-store
set. Proves the guard catches it, proves the default mode changes NO data, and proves the guard can
never break an ingest or lose a row.

    python3 backend/tools/ingest_store_guard_proof.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.modules.commcalc import ingest_store_guard as G  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append((name, got, want))


ORG = "00000000-0000-0000-0000-000000000001"

# The house org's real shape (probed live 2026-08-06): B-codes + street addresses.
MAPPING = [{"store_code": "B-1115", "store_address": "1115 Liberty Ave"},
           {"store_code": "B-509", "store_address": "509 Nostrand Ave"},
           {"store_code": "B-103", "store_address": "103 Fulton Ave"}]
ROSTER = [{"store_code": "B-1115", "address": "1115 Liberty Ave"},
          {"store_code": "T-902", "address": None},          # disabled, but STILL known
          {"store_code": "Cellular Services", "address": ""}]
ALIASES = [{"alias": "1115 LIBERTY", "store_code": "B-1115"}]

# The incident: 4 legitimate house lines + the 6 Luxelink Diversey lines.
HOUSE_ROWS = [{"store": "1115 Liberty Ave", "trans_id": "1", "ext_price": 10.0},
              {"store": "509 Nostrand Ave", "trans_id": "2", "ext_price": 20.0},
              {"store": "1115 LIBERTY", "trans_id": "3", "ext_price": 5.0},     # via alias
              {"store": "103 Fulton Ave #2", "trans_id": "4", "ext_price": 7.0}]  # via street number
LUX_ROWS = [{"store": "4640-A W Diversey Ave", "trans_id": "3400", "ext_price": v}
            for v in (0.0, 30.0, 0.0, 0.0, 29.99)] + \
           [{"store": "4640-A W Diversey Ave", "trans_id": "3402", "ext_price": 40.0}]
BATCH = HOUSE_ROWS + LUX_ROWS


class _Q:
    def __init__(self, rows, sel, boom=False):
        if boom:
            raise RuntimeError("relation does not exist")
        self._rows, self._sel = rows, sel

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        cols = [c.strip() for c in self._sel.split(",")]
        if cols == ["*"]:                      # PostgREST select('*') returns whole rows
            data = [dict(r) for r in self._rows]
        else:
            data = [{c: r.get(c) for c in cols if c in r} for r in self._rows]
        return type("R", (), {"data": data})()


class _Tbl:
    def __init__(self, data, name, inserted):
        self._d, self._n, self._ins = data, name, inserted

    def select(self, sel):
        return _Q(self._d.get(self._n, []), sel, boom=(self._n in self._d.get("__boom__", [])))

    def insert(self, rows, **k):
        if self._n in self._d.get("__boom__", []):     # a missing table raises on WRITE too
            raise RuntimeError("relation does not exist")
        self._ins.setdefault(self._n, []).extend(rows if isinstance(rows, list) else [rows])
        return _Q([], "id")


class _Schema:
    def __init__(self, data, inserted):
        self._d, self._ins = data, inserted

    def table(self, n):
        return _Tbl(self._d, n, self._ins)


class _Client:
    def __init__(self, mode="warn", **over):
        cfg = {"org_id": ORG, "mode": mode, "block_min_rows": 0,
               "allow_creates_alias": True, "notify_on_flag": True}
        cfg.update({k: v for k, v in over.items() if k != "boom" and k != "empty_roster"})
        self.inserted = {}
        self._d = {
            "ingest_store_guard": [cfg] if mode is not None else [],
            "store_mapping": [] if over.get("empty_roster") else MAPPING,
            "stores": [] if over.get("empty_roster") else ROSTER,
            "store_aliases": [] if over.get("empty_roster") else ALIASES,
            "__boom__": over.get("boom") or [],
        }

    def schema(self, _s):
        return _Schema(self._d, self.inserted)


def run(mode="warn", table="raw_sales", rows=None, **over):
    return G.screen(_Client(mode, **over), ORG, BATCH if rows is None else rows, table,
                    source="email_sweep", upload_type="daily_sales", period="July 2026")


# ── 1. THE INCIDENT IS CAUGHT ───────────────────────────────────────────────────────────────────
r = run("warn")
check("warn: exactly ONE unknown store found", r["unknown_stores"], 1)
check("warn: it is the Luxelink store", [f["store_raw"] for f in r["flags"]], ["4640-A W Diversey Ave"])
check("warn: all 6 of its lines counted", r["rows_flagged"], 6)
check("warn: dollars sized ($99.99, the real figure)", r["flags"][0]["amount_seen"], 99.99)
check("warn: a sample is captured for a human", len(r["flags"][0]["sample"]), 3)

# ── 2. THE DEFAULT MODE MOVES NO DATA — the whole reason 'warn' is the default ──────────────────
check("warn: EVERY row is still written", len(r["kept"]), len(BATCH))
check("warn: the kept list is the IDENTICAL object (provably untouched)", r["kept"] is BATCH, True)
check("warn: nothing is withheld", r["rows_withheld"], 0)
check("warn: withheld payload is None (nothing parked)", r["flags"][0]["withheld_rows"], None)

# ── 3. 'off' is byte-identical to life before the feature ───────────────────────────────────────
r_off = run("off")
check("off: no flags at all", r_off["flags"], [])
check("off: kept is the identical object", r_off["kept"] is BATCH, True)
check("off: it did not even check", r_off["checked"], False)

# ── 4. 'block' withholds — but LOSES NOTHING ────────────────────────────────────────────────────
r_b = run("block")
check("block: the 6 foreign lines are withheld", r_b["rows_withheld"], 6)
check("block: only the 4 house lines are written", len(r_b["kept"]), 4)
check("block: no house row was withheld",
      sorted(x["trans_id"] for x in r_b["kept"]), ["1", "2", "3", "4"])
check("block: the withheld rows are PARKED IN FULL, not discarded",
      len(r_b["flags"][0]["withheld_rows"]), 6)
check("block: the parked payload is the real rows",
      r_b["flags"][0]["withheld_rows"] == LUX_ROWS, True)

# ── 5. block_min_rows — a real new store opening must not be walled out ─────────────────────────
r_m = run("block", block_min_rows=3)      # 6 lines > 3 -> treated as a real batch, not a mis-file
check("block+min: a 6-line batch is flagged but NOT withheld", r_m["rows_withheld"], 0)
check("block+min: it is still recorded for review", r_m["unknown_stores"], 1)
r_m2 = run("block", block_min_rows=10)    # 6 <= 10 -> looks like a mis-file, withhold
check("block+min: a small batch under the threshold IS withheld", r_m2["rows_withheld"], 6)

# ── 6. KNOWN-STORE RESOLUTION (the same chain /store-unmatched uses) ────────────────────────────
is_known, n = G.known_store_matcher(_Client("warn"), ORG)
check("known: store_mapping address", is_known("1115 Liberty Ave"), True)
check("known: store_code", is_known("B-509"), True)
check("known: alias spelling", is_known("1115 LIBERTY"), True)
check("known: case/whitespace insensitive", is_known("  1115 liberty ave "), True)
check("known: leading street number match", is_known("103 Fulton Ave #2"), True)
check("known: a DISABLED store is still KNOWN (history must keep flowing)", is_known("T-902"), True)
check("known: a blank store is never a tenant question", is_known(""), True)
check("known: the Luxelink store is NOT known", is_known("4640-A W Diversey Ave"), False)

# ── 7. FAIL OPEN — a guard must never break an ingest or wall off a new tenant ──────────────────
check("fail-open: brand-new tenant with NO stores keeps everything",
      run("block", empty_roster=True)["kept"] is BATCH, True)
check("fail-open: migration 280 unrun (config table missing) -> default warn, all rows kept",
      len(run("warn", boom=["ingest_store_guard"])["kept"]), len(BATCH))
check("fail-open: unreadable roster tables -> keeps everything",
      run("block", boom=["store_mapping", "stores", "store_aliases"])["kept"] is BATCH, True)
check("fail-open: an unguarded table is never touched",
      run("block", table="raw_payment_detail")["kept"] is BATCH, True)
check("fail-open: an empty batch is a no-op", run("block", rows=[])["flags"], [])

# ── 8. CONFIG defaults + validation ─────────────────────────────────────────────────────────────
cfg = G.get_config(_Client("warn", boom=["ingest_store_guard"]), ORG)
check("config: missing table -> mode defaults to warn", cfg["mode"], "warn")
check("config: missing table -> ready False (UI shows the 'run migration' banner)", cfg["ready"], False)
check("config: a garbage mode falls back to warn, never to block",
      G.get_config(_Client("banana"), ORG)["mode"], "warn")
check("config: 'warn' is the shipped default", G.DEFAULT_CONFIG["mode"], "warn")
check("config: only the sales basis is guarded", sorted(G.GUARDED_TABLES), ["daily_sales_feed", "raw_sales"])

# ── 9. record() writes the queue rows, and never raises ─────────────────────────────────────────
c = _Client("block")
res = G.screen(c, ORG, BATCH, "raw_sales", source="promotion", upload_type="sales", period="July 2026")
check("record: one queue row per unknown store", G.record(c, ORG, res), 1)
q = c.inserted["ingest_store_quarantine"][0]
check("record: org stamped (RULE ONE write-side)", q["org_id"], ORG)
check("record: status starts pending", q["status"], "pending")
check("record: carries the row count", q["rows_seen"], 6)
check("record: carries the dollars", q["amount_seen"], 99.99)
check("record: carries the provenance", (q["source"], q["target_table"], q["period"]),
      ("promotion", "raw_sales", "July 2026"))
check("record: a broken queue table returns 0, never raises",
      G.record(_Client("block", boom=["ingest_store_quarantine"]), ORG, res), 0)
check("record: nothing to record -> 0", G.record(c, ORG, {"flags": []}), 0)

# ── 10. screen_and_record is the one call an ingest path makes ─────────────────────────────────
check("screen_and_record: warn returns every row",
      len(G.screen_and_record(_Client("warn"), ORG, BATCH, "raw_sales")), len(BATCH))
check("screen_and_record: block returns only the org's own rows",
      len(G.screen_and_record(_Client("block"), ORG, BATCH, "raw_sales")), 4)

for n_, g, w in FAIL:
    print(f"FAIL  {n_}\n        got  {g!r}\n        want {w!r}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
