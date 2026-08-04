"""PROOF HARNESS — MA "Overview of Accounts" reconciliation (mig 268 package).

Runs the REAL ma_overview module against fixtures through a fake Supabase client that mimics the
PostgREST builder surface (schema/table/select/eq/in_/range/execute and rpc). No network, no DB.

What it proves:
  1. every default tile computes the number a human computes by hand from the fixture rows,
  2. the sign convention (export negative = paid to dealer) is applied, so money tiles are positive,
  3. the deliberately-unmapped tiles stay unmapped (no fake 0) and are reported as such,
  4. a matching uploaded report produces delta 0 / status 'ok' on every mapped tile,
  5. NEGATIVE CONTROL — a deliberately wrong uploaded report produces the EXACT expected deltas and
     flips exactly the perturbed tiles to 'off' (a harness that only ever sees zeros proves nothing),
  6. the per-account cross-check finds the account that is wrong, and sorts it first,
  7. the explainers find: the account only in the report, the account only in our data, the rows with
     no IMEI, the multi-line activation, and the row dated outside its period,
  8. both uploaded-file shapes parse (per-account table AND the abbreviated two-column tile list),
  9. re-upload is idempotent — the same file twice produces the same delete keys and the same rows,
 10. a typo'd tile mapping is REJECTED with a reason instead of silently reading as "no rows matched",
 11. the RPC path and the paged-scan fallback produce IDENTICAL tile values.

Run:  python3 scratchpad/prove_ma_overview_recon.py
"""
import sys, os, calendar

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.modules.commcalc import ma_overview as mo   # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"          # a NON-house tenant on purpose (luxelink)
PERIOD = "July 2026"
FAILURES = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


def close(name, got, want, tol=0.005):
    ok = abs(float(got) - float(want)) <= tol
    if not ok:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


# ── period helpers (the real router's, reproduced so the harness is self-contained) ──────────────
_MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}


def month_year(p):
    p = str(p or "").strip()
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-":
        return int(p[5:7]), int(p[:4])
    parts = p.split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        return _MONTHS[parts[0]], int(parts[1])
    return 0, 0


def pvariants(p):
    mo_, yr = month_year(p)
    if not (1 <= mo_ <= 12 and yr):
        return [str(p or "").strip()]
    return list({str(p).strip(), f"{calendar.month_name[mo_]} {yr}", f"{yr}-{mo_:02d}"})


def canon_period(p):
    mo_, yr = month_year(p)
    return f"{calendar.month_name[mo_]} {yr}" if (1 <= mo_ <= 12 and yr) else str(p or "").strip()


# ── THE FIXTURE: raw_ma_commission rows for July 2026, three merchant accounts ───────────────────
# Money is written the way the real export writes it: NEGATIVE = paid to the dealer.
def C(acct, order, imei, at="New", at2="branded", sub="", fin="", status="Active", susp="",
      cm=0.0, dm=0.0, reb=0.0, fm=0.0, sp1=0.0, date="2026-07-10", period=PERIOD, org=None):
    return {"org_id": org or ORG, "merchant_account_id": acct, "activation_order": order, "imei": imei,
            "activation_type": at, "activation_type2": at2, "sub_type": sub, "is_financed": fin,
            "line_status": status, "suspension_reason": susp, "port_status": "", "perfect_sale": "",
            "consumer_margin": cm, "device_margin": dm, "consumer_financing": 0.0, "rebate": reb,
            "wallet_funding": 0.0, "fees": 0.0, "fees_margin": fm, "mrc_net_discount": 45.0,
            "consumer_value": 0.0, "spiff_m1": sp1, "spiff_m2": 0.0, "spiff_m3": 0.0,
            "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0, "tx_date": date, "period": period}


COMMISSION_ROWS = [
    # ── account A100: 4 activation rows (one is a 2nd line of the SAME order → multi-line), 1 TWP,
    #    1 financed (Edge), 1 with a BLANK imei, 1 dated in JUNE though filed under July.
    C("A100", "ORD-1", "IMEI-1", cm=-20.0, dm=-5.0, reb=-30.0, fm=-1.0, sp1=-4.0),
    C("A100", "ORD-1", "IMEI-2", sub="TWP", cm=-10.0, reb=-5.0),          # same order, 2nd line
    C("A100", "ORD-2", "", fin="Y", cm=-15.0, dm=-2.5, reb=-20.0, fm=-0.5),  # blank IMEI + Edge
    C("A100", "ORD-3", "IMEI-4", cm=-12.0, reb=-10.0, date="2026-06-30"),  # dated OUTSIDE the period
    # ── account B200: 2 activation rows, 1 on the last day of the month, 1 suspended/not-eligible.
    C("B200", "ORD-4", "IMEI-5", cm=-25.0, dm=-7.5, reb=-40.0, fm=-2.0, date="2026-07-31"),
    C("B200", "ORD-5", "IMEI-6", status="Suspended", susp="Non-Pay", cm=-8.0, reb=0.0),
    # ── a NON-activation row (blank Activation Type): must NOT count as an activation.
    C("B200", "ORD-6", "IMEI-7", at="", cm=-1.0),
    # ── a row in ANOTHER period: must never appear in a July total.
    C("A100", "ORD-9", "IMEI-9", cm=-999.0, reb=-999.0, date="2026-06-05", period="June 2026"),
]

DAILYTX_ROWS = [
    {"account_id": "A100", "account_name": "Lux Downtown", "order_type": "Postpaid Residual Order",
     "order_number": "R-1", "retail_cost": -120.00, "merchant_discount": -5.0, "merchant_invoice": 0.0,
     "org_id": ORG, "product_name": "Trac Autopay Residual", "tx_date": "2026-07-15", "period": PERIOD},
    {"account_id": "A100", "account_name": "Lux Downtown", "order_type": "Postpaid Residual Order",
     "order_number": "R-2", "retail_cost": -80.00, "merchant_discount": -3.0, "merchant_invoice": 0.0,
     "org_id": ORG, "product_name": "Trac Autopay Residual", "tx_date": "2026-07-20", "period": PERIOD},
    {"account_id": "B200", "account_name": "Lux Uptown", "order_type": "Airtime Order",
     "order_number": "R-3", "retail_cost": -500.00, "merchant_discount": -25.0, "merchant_invoice": 0.0,
     "org_id": ORG, "product_name": "Total 55 Refill", "tx_date": "2026-07-18", "period": PERIOD},
    {"account_id": "C300", "account_name": "Lux Airport", "order_type": "Postpaid Residual Order",
     "order_number": "R-4", "retail_cost": -40.00, "merchant_discount": -1.0, "merchant_invoice": 0.0,
     "org_id": ORG, "product_name": "Trac Autopay Residual", "tx_date": "2026-07-05", "period": PERIOD},
    {"account_id": "A100", "account_name": "Lux Downtown", "order_type": "Postpaid Residual Order",
     "order_number": "R-5", "retail_cost": -1000.00, "merchant_discount": -50.0, "merchant_invoice": 0.0,
     "org_id": ORG, "product_name": "Trac Autopay Residual", "tx_date": "2026-08-01", "period": "August 2026"},
]

# ── the HAND-COMPUTED truth for July (what a human gets with a calculator) ───────────────────────
TRUTH = {
    # rows with a non-blank Activation Type, July only: A100 x4 + B200 x2 = 6  (ORD-6 blank AT excluded,
    # the June-period row excluded)
    "activation_count": 6,
    "twp_count": 1,
    # residual = -(sum retail_cost of July 'Postpaid Residual Order' rows) = -(-120-80-40) = 240
    "residual": 240.00,
    # rebates = -(sum rebate July) = -(-30-5-20-10-40) = 105
    "rebates_paid": 105.00,
    "fees_margin_paid": 3.50,          # -(-1.0 -0.5 -2.0)
    # commissions = -(Σ consumer_margin + Σ device_margin) over ALL July rows (no filter — includes the
    # blank-activation-type row ORD-6 and the suspended row, by design: it is a money total, not a count)
    "commissions_paid": 106.00,        # -( -20-10-15-12-25-8-1 ) + -( -5 -2.5 -7.5 ) = 91 + 15 = 106
    "edge_count": 1,
}


# ── fake Supabase client ─────────────────────────────────────────────────────────────────────────
class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, rows, rpc_enabled=True):
        self.rows, self._f = rows, []

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self._f.append(lambda r: str(r.get(k, "")) == str(v)); return self

    def neq(self, k, v):
        self._f.append(lambda r: str(r.get(k, "")) != str(v)); return self

    def in_(self, k, vals):
        s = {str(x) for x in vals}
        self._f.append(lambda r: str(r.get(k, "")) in s); return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_k):
        return self

    def _rows(self):
        return [r for r in self.rows if all(f(r) for f in self._f)]

    def range(self, a, b):
        self._range = (a, b); return self

    def execute(self):
        rows = self._rows()
        if hasattr(self, "_range"):
            a, b = self._range
            rows = rows[a:b + 1]
        return _Res(rows)

    def delete(self):
        self._delete = True; return self

    def insert(self, recs):
        self._insert = recs; return self


class _Rpc:
    """The real PostgREST rpc() returns a BUILDER whose .execute() carries .data."""

    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeClient:
    """Mimics the PostgREST builder. `rpc_enabled=False` simulates migration 268 being UNRUN, which is
    how the harness proves the Python fallback returns identical numbers."""

    def __init__(self, tables, rpc_enabled=True):
        self.tables, self.rpc_enabled = tables, rpc_enabled
        self.deleted, self.inserted = [], []

    def schema(self, _s):
        return self

    def table(self, name):
        if name not in self.tables:
            raise RuntimeError(f'relation "commcalc.{name}" does not exist')
        q = _Q(self.tables[name])
        q._name, q._client = name, self
        orig_exec = q.execute

        def ex():
            if getattr(q, "_delete", False):
                keep, gone = [], []
                for r in self.tables[q._name]:
                    (gone if all(f(r) for f in q._f) else keep).append(r)
                self.tables[q._name] = keep
                self.deleted.append((q._name, len(gone)))
                return _Res(gone)
            if getattr(q, "_insert", None) is not None:
                self.tables[q._name].extend(q._insert)
                self.inserted.append((q._name, len(q._insert)))
                return _Res(q._insert)
            return orig_exec()
        q.execute = ex
        return q

    # ── the mig-268 cubes, computed the way the SQL computes them ──
    def rpc(self, fn, args):
        return _Rpc(lambda: self._rpc(fn, args))

    def _rpc(self, fn, args):
        if not self.rpc_enabled:
            raise RuntimeError(f'function commcalc.{fn} does not exist')
        org, periods = args.get("p_org"), set(args.get("p_periods") or [])
        accts = set(args.get("p_accounts") or []) or None
        if fn == "ma_overview_commission_cube":
            return _Res(self._cube("raw_ma_commission", org, periods, accts))
        if fn == "ma_overview_dailytx_cube":
            return _Res(self._cube("raw_ma_daily_tx", org, periods, accts))
        if fn in ("ma_overview_commission_dates", "ma_overview_dailytx_dates"):
            tbl = ("raw_ma_commission" if fn.startswith("ma_overview_commission")
                   else "raw_ma_daily_tx")
            akey = mo.SOURCES[tbl]["account_key"]
            acc = {}
            for r in self.tables.get(tbl, []):
                if periods and str(r.get("period")) not in periods:
                    continue
                if accts and str(r.get(akey)) not in accts:
                    continue
                k = (str(r.get("tx_date") or "")[:10], str(r.get("period") or ""))
                acc[k] = acc.get(k, 0) + 1
            return _Res([{"tx_date": k[0], "period": k[1], "rows_n": v} for k, v in acc.items()])
        if fn == "ma_overview_commission_accounts":
            acc = {}
            for r in self.tables.get("raw_ma_commission", []):
                if periods and str(r.get("period")) not in periods:
                    continue
                a = str(r.get("merchant_account_id") or "").strip() or "?"
                if accts and a not in accts:
                    continue
                d = acc.setdefault(a, {"merchant_account_id": a, "rows_n": 0, "_o": set(),
                                       "_i": set(), "imei_blank_n": 0})
                d["rows_n"] += 1
                if str(r.get("activation_order") or "").strip():
                    d["_o"].add(str(r.get("activation_order")).strip())
                if str(r.get("imei") or "").strip():
                    d["_i"].add(str(r.get("imei")).strip())
                else:
                    d["imei_blank_n"] += 1
            out = []
            for d in acc.values():
                d["orders_n"] = len(d.pop("_o")); d["imei_n"] = len(d.pop("_i"))
                out.append(d)
            return _Res(out)
        if fn == "ma_overview_periods":
            out = {}
            for tbl in ("raw_ma_commission", "raw_ma_daily_tx", "ma_overview_upload"):
                for r in self.tables.get(tbl, []):
                    out[str(r.get("period") or "")] = 1
            return _Res([{"period": p} for p in out if p])
        raise RuntimeError(f"unknown rpc {fn}")

    def _cube(self, tbl, org, periods, accts):
        spec = mo.SOURCES[tbl]
        akey = spec["account_key"]
        out = {}
        for r in self.tables.get(tbl, []):
            if periods and str(r.get("period")) not in periods:
                continue
            a = str(r.get(akey) or "").strip() or "?"
            if accts and a not in accts:
                continue
            key = (a,) + tuple(str(r.get(d) or "").strip() for d in spec["dims"])
            g = out.get(key)
            if g is None:
                g = {akey: a, "rows_n": 0, "orders_n": 0, "imei_n": 0, "imei_blank_n": 0,
                     "min_tx_date": None, "max_tx_date": None, "_o": set(), "_i": set()}
                for i, dcol in enumerate(spec["dims"]):
                    g[dcol] = key[i + 1]
                for m in spec["money"]:
                    g[m] = 0.0
                if tbl == "raw_ma_daily_tx":
                    g["account_name"] = str(r.get("account_name") or "")
                out[key] = g
            g["rows_n"] += 1
            for m in spec["money"]:
                g[m] += float(r.get(m) or 0)
            ordcol = "activation_order" if tbl == "raw_ma_commission" else "order_number"
            if str(r.get(ordcol) or "").strip():
                g["_o"].add(str(r.get(ordcol)).strip())
            if tbl == "raw_ma_commission":
                if str(r.get("imei") or "").strip():
                    g["_i"].add(str(r.get("imei")).strip())
                else:
                    g["imei_blank_n"] += 1
        res = []
        for g in out.values():
            g["orders_n"] = len(g.pop("_o"))
            g["imei_n"] = len(g.pop("_i"))
            res.append(g)
        return res


def new_client(upload_rows=None, rpc_enabled=True):
    return FakeClient({
        "raw_ma_commission": [dict(r) for r in COMMISSION_ROWS],
        "raw_ma_daily_tx": [dict(r) for r in DAILYTX_ROWS],
        "ma_overview_upload": [dict(r) for r in (upload_rows or [])],
        "ma_overview_tile_config": [],
    }, rpc_enabled=rpc_enabled)


def tile_map(payload):
    return {t["tile_key"]: t for t in payload["tiles"]}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n① SYSTEM SIDE — every default tile against a hand-computed fixture")
cl = new_client()
p1 = mo.compute(cl, ORG, PERIOD, pvariants, canon_period, month_year)
tm = tile_map(p1)
for k, want in TRUTH.items():
    close(f"tile {k} = {want}", tm[k]["system"], want)
check("commissions_not_eligible stays UNMAPPED (no fake 0)", tm["commissions_not_eligible"]["mapped"], False)
check("commissions_not_eligible system is None", tm["commissions_not_eligible"]["system"], None)
check("appeal_count stays UNMAPPED", tm["appeal_count"]["mapped"], False)
check("money tiles are POSITIVE after the sign convention", all(
    tm[k]["system"] > 0 for k in ("residual", "rebates_paid", "fees_margin_paid", "commissions_paid")), True)
check("no report uploaded -> status 'no_report'", tm["activation_count"]["status"], "no_report")
check("cube came from the RPC", p1["cube_source"]["raw_ma_commission"], "rpc")
check("tile config source = code defaults (no rows saved)", p1["config_source"], "code_default")

print("\n② RPC path vs the migration-unrun PYTHON FALLBACK — identical numbers")
p1b = mo.compute(new_client(rpc_enabled=False), ORG, PERIOD, pvariants, canon_period, month_year)
tmb = tile_map(p1b)
check("fallback engaged", p1b["cube_source"]["raw_ma_commission"], "python_fallback")
for k in TRUTH:
    close(f"fallback tile {k} identical", tmb[k]["system"], tm[k]["system"])

print("\n③ POSITIVE CONTROL — an uploaded report that MATCHES gives delta 0 / status ok")
matching = [
    {"org_id": ORG, "period": PERIOD, "merchant_account_id": "A100", "account_name": "Lux Downtown",
     "activation_count": 4, "twp_count": 1, "residual": 200.00, "rebates_paid": 65.00,
     "fees_margin_paid": 1.50, "commissions_paid": 64.50, "edge_count": 1,
     "commissions_not_eligible": 0, "appeal_count": 0, "extra": {}},
    {"org_id": ORG, "period": PERIOD, "merchant_account_id": "B200", "account_name": "Lux Uptown",
     "activation_count": 2, "twp_count": 0, "residual": 0.00, "rebates_paid": 40.00,
     "fees_margin_paid": 2.00, "commissions_paid": 41.50, "edge_count": 0,
     "commissions_not_eligible": 1, "appeal_count": 0, "extra": {}},
    {"org_id": ORG, "period": PERIOD, "merchant_account_id": "C300", "account_name": "Lux Airport",
     "activation_count": 0, "twp_count": 0, "residual": 40.00, "rebates_paid": 0.00,
     "fees_margin_paid": 0.00, "commissions_paid": 0.00, "edge_count": 0,
     "commissions_not_eligible": 0, "appeal_count": 0, "extra": {}},
]
p2 = mo.compute(new_client(matching), ORG, PERIOD, pvariants, canon_period, month_year)
tm2 = tile_map(p2)
for k in TRUTH:
    close(f"delta {k} == 0", tm2[k]["delta"], 0.0)
    check(f"status {k} == ok", tm2[k]["status"], "ok")
check("every mapped tile reconciles", all(t["status"] == "ok" for t in p2["tiles"] if t["mapped"]), True)
check("unmapped tiles report 'unmapped', not a delta", tm2["appeal_count"]["status"], "unmapped")
check("report provenance recorded", p2["report"]["present"], True)

print("\n④ NEGATIVE CONTROL — a deliberately WRONG report produces the exact expected deltas")
wrong = [dict(r) for r in matching]
wrong[0]["activation_count"] = 9          # +5 too many stated  -> our 6 vs stated 11 => delta -5
wrong[1]["rebates_paid"] = 140.00         # +100 too much stated -> our 105 vs 205  => delta -100
p3 = mo.compute(new_client(wrong), ORG, PERIOD, pvariants, canon_period, month_year)
tm3 = tile_map(p3)
close("activation_count delta = -5", tm3["activation_count"]["delta"], -5.0)
check("activation_count status flips to 'off'", tm3["activation_count"]["status"], "off")
close("rebates_paid delta = -100", tm3["rebates_paid"]["delta"], -100.0)
check("rebates_paid status flips to 'off'", tm3["rebates_paid"]["status"], "off")
check("the UNPERTURBED tiles stay ok", [tm3[k]["status"] for k in
                                        ("twp_count", "residual", "fees_margin_paid",
                                         "commissions_paid", "edge_count")],
      ["ok"] * 5)
# The two perturbed accounts must occupy the top two slots, worst RELATIVE delta first: B200's rebate is
# off by 100 on a stated 140 (0.71) vs A100's activation off by 5 on a stated 9 (0.56) — so B200 leads.
check("both perturbed accounts rank above the clean one",
      [r["account_id"] for r in p3["per_account"]], ["B200", "A100", "C300"])
_a100 = next(r for r in p3["per_account"] if r["account_id"] == "A100")
_b200 = next(r for r in p3["per_account"] if r["account_id"] == "B200")
close("per-account activation delta on A100 = -5", _a100["d_activation_count"], -5.0)
close("per-account rebate delta on B200 = -100", _b200["d_rebates_paid"], -100.0)
close("the CLEAN account shows zero delta everywhere",
      sum(abs(v) for k, v in p3["per_account"][2].items() if k.startswith("d_") and v is not None), 0.0)

print("\n⑤ EXPLAINERS — the rows that plausibly explain a delta")
missing_acct = [dict(r) for r in matching] + [
    {"org_id": ORG, "period": PERIOD, "merchant_account_id": "Z999", "account_name": "Ghost Store",
     "activation_count": 3, "twp_count": 0, "residual": 0, "rebates_paid": 0, "fees_margin_paid": 0,
     "commissions_paid": 0, "edge_count": 0, "commissions_not_eligible": 0, "appeal_count": 0,
     "extra": {}}]
p4 = mo.compute(new_client(missing_acct), ORG, PERIOD, pvariants, canon_period, month_year)
ex = p4["explain"]
check("account only in the report is found", [a["account_id"] for a in ex["accounts_only_in_report"]], ["Z999"])
check("rows with no IMEI counted", ex["missing_imei"]["rows"], 1)
check("multi-line activation detected (7 rows / 6 orders)", ex["multi_line_activations"]["extra_lines"], 1)
check("row dated OUTSIDE its period found", ex["date_boundary"]["rows_dated_outside_period"], 1)
check("...and it is the 2026-06-30 row", ex["date_boundary"]["outside_dates"][0]["tx_date"], "2026-06-30")
check("rows on the month's last day counted", ex["date_boundary"]["rows_on_last_day"], 1)
check("residual feed's out-of-period rows counted separately",
      ex["residual_date_boundary"]["rows_dated_outside_period"], 0)
close("basis alternative: M1-M6 spiff total", ex["basis_alternatives"]["spiff_m1_m6_total"], 4.00)
check("account only in OUR data is found (C300 has residual but the report of ③ lists it, so none here)",
      [a["account_id"] for a in ex["accounts_only_in_system"]], [])
check("unmapped-tile candidates expose the real line_status values",
      sorted(v["value"] for v in p4["unmapped_candidates"]["line_status"]), ["Active", "Suspended"])
check("...and the real suspension reasons",
      sorted(v["value"] for v in p4["unmapped_candidates"]["suspension_reason"]), ["(blank)", "Non-Pay"])

print("\n⑥ ACCOUNT FILTER narrows BOTH sides (WYSIWYG)")
p5 = mo.compute(new_client(matching), ORG, PERIOD, pvariants, canon_period, month_year, accounts=["A100"])
tm5 = tile_map(p5)
close("activation_count narrowed to A100", tm5["activation_count"]["system"], 4)
close("residual narrowed to A100", tm5["residual"]["system"], 200.00)
check("only A100 in the per-account table", [r["account_id"] for r in p5["per_account"]], ["A100"])
close("the STATED side is narrowed too (not our 4 vs the report's whole-company 6)",
      tm5["activation_count"]["uploaded"], 4)
check("...so a filtered view still reconciles", tm5["activation_count"]["status"], "ok")

print("\n⑦ UPLOADED-FILE PARSING — both shapes the portal produces")
tiles, _src = mo.resolve_tiles(new_client(), ORG)
per_account_file = [
    {"MerchantAccountId": "A100", "Account Name": "Lux Downtown", "Activation Count": "4",
     "TWP Count": "1", "Residual": "$200.00", "Rebates Paid": "$65.00", "Fees Margin Paid": "$1.50",
     "Commissions Paid": "$64.50", "Commissions Not Eligible": "0", "Edge": "1", "Appeal Count": "0",
     "Something Else": "keep me"},
]
rows, warns = mo.parse_overview_rows(tiles, per_account_file, PERIOD, canon_period)
check("per-account shape: one row", len(rows), 1)
check("per-account: account id read", rows[0]["merchant_account_id"], "A100")
close("per-account: money parsed out of '$200.00'", rows[0]["residual"], 200.0)
close("per-account: count parsed", rows[0]["activation_count"], 4)
check("per-account: unmapped column kept verbatim", rows[0]["extra"].get("Something Else"), "keep me")

tile_list_file = [
    {"Metric": "Activation Count", "Value": "1.1K"},
    {"Metric": "TWP Count", "Value": "279"},
    {"Metric": "Residual", "Value": "$28.3K"},
    {"Metric": "Rebates Paid", "Value": "$173.7K"},
    {"Metric": "Fees Margin Paid", "Value": "$4.0K"},
    {"Metric": "Commissions Paid", "Value": "$27.0K"},
    {"Metric": "Commissions Not Eligible", "Value": "4"},
    {"Metric": "Edge", "Value": "4"},
    {"Metric": "Appeal Count", "Value": "0"},
]
rows2, warns2 = mo.parse_overview_rows(tiles, tile_list_file, PERIOD, canon_period)
check("tile-list shape: one report-total row", len(rows2), 1)
check("tile-list: stored as the '*' total row", rows2[0]["merchant_account_id"], "*")
close("tile-list: '1.1K' -> 1100", rows2[0]["activation_count"], 1100)
close("tile-list: '279' -> 279", rows2[0]["twp_count"], 279)
close("tile-list: '$28.3K' -> 28300", rows2[0]["residual"], 28300)
close("tile-list: '$173.7K' -> 173700", rows2[0]["rebates_paid"], 173700)
close("tile-list: '$4.0K' -> 4000", rows2[0]["fees_margin_paid"], 4000)
close("tile-list: '$27.0K' -> 27000", rows2[0]["commissions_paid"], 27000)
close("tile-list: '4' -> 4", rows2[0]["commissions_not_eligible"], 4)
close("tile-list: '0' -> 0", rows2[0]["appeal_count"], 0)
check("tile-list: the abbreviation is DISCLOSED, not hidden",
      any("ABBREVIATED" in w for w in warns2), True)
check("tile-list: flagged on the row for the page", rows2[0]["extra"]["stated_abbreviated"], True)

print("\n⑧ IDEMPOTENT RE-UPLOAD — replace by (org, period, account), never wipe another period")
cl2 = new_client()
cl2.tables["ma_overview_upload"] = [
    {"org_id": ORG, "period": "June 2026", "merchant_account_id": "A100", "activation_count": 999},
]
recs = mo.upload_rows_to_records(rows, ORG, "overview.csv", "tester", month_year)
check("org stamped on every record (RULE ONE write side)", all(r["org_id"] == ORG for r in recs), True)
check("period parts derived", (recs[0]["period_month"], recs[0]["period_year"]), (7, 2026))
r1 = mo.persist_upload(cl2, ORG, recs)
r2 = mo.persist_upload(cl2, ORG, mo.upload_rows_to_records(rows, ORG, "overview.csv", "tester", month_year))
july = [r for r in cl2.tables["ma_overview_upload"] if r["period"] == "July 2026"]
june = [r for r in cl2.tables["ma_overview_upload"] if r["period"] == "June 2026"]
check("first upload saved 1 row", r1["saved"], 1)
check("re-upload still leaves exactly 1 July row (no duplicate)", len(july), 1)
check("the OTHER period is untouched", (len(june), june[0]["activation_count"]), (1, 999))

print("\n⑨ CONFIG VALIDATION — a typo'd mapping is REJECTED, never a silent zero")
bad = dict(mo.DEFAULT_TILES[0]); bad["filter_field"] = "activaton_type"     # typo
check("typo'd filter column rejected", bool(mo.tile_problems(bad)), True)
bad2 = dict(mo.DEFAULT_TILES[3]); bad2["value_fields"] = "rebbate"
check("typo'd money column rejected", bool(mo.tile_problems(bad2)), True)
bad3 = dict(mo.DEFAULT_TILES[3]); bad3["source_table"] = "raw_sales"
check("a non-MA source table rejected", bool(mo.tile_problems(bad3)), True)
check("every shipped default tile is VALID", [t["tile_key"] for t in mo.DEFAULT_TILES
                                              if mo.tile_problems(t)], [])
cl3 = new_client()
cl3.tables["ma_overview_tile_config"] = [
    {"org_id": ORG, "tile_key": "rebates_paid", "filter_field": "nope", "filter_op": "eq",
     "filter_value": "x", "is_active": True},
]
p6 = mo.compute(cl3, ORG, PERIOD, pvariants, canon_period, month_year)
tm6 = tile_map(p6)
check("a broken saved mapping renders 'config_error', not a number", tm6["rebates_paid"]["status"], "config_error")
check("...and its system value is blank, not 0", tm6["rebates_paid"]["system"], None)
check("...while the other tiles still compute", tm6["activation_count"]["system"], 6.0)

print("\n⑩ TENANT OVERRIDE — a saved tile row wins, and the money follows the config")
cl4 = new_client(matching)
cl4.tables["ma_overview_tile_config"] = [
    # this tenant's portal counts ORDERS, and its "Commissions Paid" INCLUDES the M1-M6 spiffs
    {"org_id": ORG, "tile_key": "commissions_paid",
     "value_fields": "consumer_margin,device_margin,spiff_m1", "is_active": True},
]
p7 = mo.compute(cl4, ORG, PERIOD, pvariants, canon_period, month_year)
check("tile config source flips to the org's rows", p7["config_source"], "org_config")
close("commissions_paid now includes the spiff (106 + 4)", tile_map(p7)["commissions_paid"]["system"], 110.0)
check("...and the delta against the unchanged report is exposed",
      tile_map(p7)["commissions_paid"]["status"], "off")

print("\n⑪ FILTER OPERATORS")
g = {"sub_type": "TWP", "is_financed": "Yes", "line_status": "Suspended", "order_type": "Postpaid Residual Order"}
check("eq", mo.match_filter(g, "sub_type", "eq", "twp"), True)
check("neq", mo.match_filter(g, "sub_type", "neq", "TWP"), False)
check("in", mo.match_filter(g, "line_status", "in", "Active,Suspended"), True)
check("not_in", mo.match_filter(g, "line_status", "not_in", "Active,Suspended"), False)
check("contains", mo.match_filter(g, "order_type", "contains", "Residual Order"), True)
check("truthy (Yes)", mo.match_filter(g, "is_financed", "truthy", None), True)
check("truthy (blank)", mo.match_filter({"is_financed": ""}, "is_financed", "truthy", None), False)
check("nonblank", mo.match_filter({"activation_type": "New"}, "activation_type", "nonblank", None), True)
check("blank on blank", mo.match_filter({"activation_type": ""}, "activation_type", "blank", None), True)
check("no filter field = match all", mo.match_filter(g, None, None, None), True)

print("\n⑫ PERIOD SPELLING — '2026-07' reads the same rows as 'July 2026'")
p8 = mo.compute(new_client(matching), ORG, "2026-07", pvariants, canon_period, month_year)
close("activation_count identical under the other spelling", tile_map(p8)["activation_count"]["system"], 6)
check("period canonicalized for display", p8["period"], "July 2026")

print("\n" + ("=" * 78))
if FAILURES:
    print(f"❌ {len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print("   - " + f)
    sys.exit(1)
print("✅ ALL CHECKS PASSED — MA Overview recon math, parsing, idempotence, config validation, "
      "explainers, RPC/fallback parity and the negative control.")
