"""READ-ONLY before/after ledger for the owner rulings K1/K2/K3 mass recompute.

Ruling K3(a): "this is a mass money-touching recompute — it needs its own before/after ledger per
period, not a silent sweep." This is that ledger.

WHAT IT DOES
    For every period that already has a stored statement, prints the OLD reported net income (read
    straight out of `commcalc.account_statements`) beside the NEW net income that the patched
    `coa.build_inputs` + `engine._assemble` produce, with the per-line deltas.

WHAT IT DOES NOT DO
    It never writes. It does not call `engine.compute_and_store`. Every Supabase write verb is blocked
    at the client wrapper, so the script cannot rewrite a reported number even by mistake — executing
    the recompute is the owner's Gate-2 call, not this script's.

PREVIEWING THE CONFIG WITHOUT SEEDING IT
    Migration 621's money seed is deliberately commented out, so `account_config` in prod does NOT yet
    carry `device_cogs_mode` / `payroll_expense_names`. `--preview-config` injects the values the seed
    WOULD set, purely on the read path, so the owner can see the exact delta before anything is
    applied. Without that flag the script reports what a recompute would do TODAY (i.e. K1 + #7 only).

USAGE
    python3 finance_recompute_ledger.py --org 854f6d7b-6590-4e4d-88ab-646f560d4f4c --preview-config
    python3 finance_recompute_ledger.py --org <org> --period "July 2026"
    python3 finance_recompute_ledger.py --org <org> --json    # machine-readable

REQUIRES the backend's normal Supabase env (SUPABASE_URL / service key), same as the app.
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

WRITE_VERBS = ("insert", "update", "upsert", "delete")

# The values migration 621's commented-out seed would install. Kept here so the preview and the
# migration cannot drift apart silently — if you change one, change the other.
PREVIEW_CONFIG = {
    "payroll_expense_names": ["Employee Salaries", "DM Salaries", "Owner / Mgmt Salaries"],
    "payroll_expense_routes": {},
    "device_cogs_mode": "auto",
}


class _ReadOnlyQ:
    """Wraps a live supabase query builder, forwards reads, and refuses every write verb."""

    def __init__(self, q, table, overrides):
        self._q, self._table, self._ov = q, table, overrides

    def __getattr__(self, name):
        if name in WRITE_VERBS:
            raise AssertionError(
                "finance_recompute_ledger is READ-ONLY — refused %s() on %s. Executing the recompute "
                "is the owner's Gate-2 decision." % (name, self._table))
        attr = getattr(self._q, name)
        if not callable(attr):
            return attr

        def _wrapped(*a, **kw):
            r = attr(*a, **kw)
            # `execute()` returns a response, not a builder
            if name == "execute":
                if self._table == "account_config" and self._ov:
                    rows = list(r.data or [])
                    merged = dict(rows[0]) if rows else {}
                    merged.update(self._ov)
                    r.data = [merged]
                return r
            return _ReadOnlyQ(r, self._table, self._ov)

        return _wrapped


class _ReadOnlySchema:
    def __init__(self, s, overrides):
        self._s, self._ov = s, overrides

    def table(self, name):
        return _ReadOnlyQ(self._s.table(name), name, self._ov)

    def rpc(self, *a, **kw):
        return _ReadOnlyQ(self._s.rpc(*a, **kw), "rpc", None)


class ReadOnlyClient:
    """A live client that can only read, and can optionally pretend `account_config` is seeded."""

    def __init__(self, inner, config_overrides=None):
        self._inner, self._ov = inner, config_overrides

    def schema(self, name):
        return _ReadOnlySchema(self._inner.schema(name), self._ov)

    def table(self, name):
        return self.schema("public").table(name)


def consolidated_pl(client, org_id, period):
    """Assemble the CONSOLIDATED P&L exactly as `engine.compute_and_store` would, without persisting."""
    from app.modules.account import coa, engine
    inputs = coa.build_inputs(client, org_id, period)
    journal = (client.schema("commcalc").table("journal_entries").select("*")
               .eq("org_id", org_id).eq("period", period).execute().data) or []
    pl = engine._assemble(
        inputs, journal, coa.PL_SPEC, coa.PL_LABEL,
        [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
         ("Operating Expenses", "opex"), ("Other", "other")],
        "consolidated", None, True)
    return pl, inputs


def flat_lines(pl):
    out = {}
    for sec in pl["sections"]:
        for ln in sec["lines"]:
            out[ln["label"]] = ln["amount"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--period", default="", help="one period; default = every period with a snapshot")
    ap.add_argument("--preview-config", action="store_true",
                    help="pretend mig 621's money seed is applied (read path only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from app.core.database import get_supabase
    raw = get_supabase()
    client = ReadOnlyClient(raw, PREVIEW_CONFIG if args.preview_config else None)

    snaps = (raw.schema("commcalc").table("account_statements")
             .select("period,payload,computed_at").eq("org_id", args.org)
             .eq("statement_type", "pl").eq("scope_key", "consolidated").execute().data) or []
    by_period = {}
    for s in snaps:
        p = s.get("period") or ""
        if p and (not args.period or p == args.period):
            prev = by_period.get(p)
            if not prev or (s.get("computed_at") or "") > (prev.get("computed_at") or ""):
                by_period[p] = s

    if not by_period:
        print("No consolidated P&L snapshot for org=%s%s — nothing to compare."
              % (args.org, (" period=%s" % args.period) if args.period else ""))
        return 1

    from app.modules.account import coa
    rows = []
    for period in sorted(by_period, key=lambda p: coa.parse_period(p)[::-1]):
        snap = by_period[period]
        old_pl = snap.get("payload") or {}
        old_ni = round(float(old_pl.get("net_income") or 0), 2)
        try:
            new_pl, inputs = consolidated_pl(client, args.org, period)
        except Exception as e:
            rows.append({"period": period, "old_ni": old_ni, "new_ni": None,
                         "error": "%s: %s" % (type(e).__name__, e)})
            continue
        new_ni = round(float(new_pl.get("net_income") or 0), 2)
        old_l, new_l = flat_lines(old_pl), flat_lines(new_pl)
        deltas = {}
        for label in set(old_l) | set(new_l):
            d = round(new_l.get(label, 0.0) - old_l.get(label, 0.0), 2)
            if abs(d) >= 0.01:
                deltas[label] = {"old": old_l.get(label, 0.0), "new": new_l.get(label, 0.0), "delta": d}
        rows.append({
            "period": period, "computed_at": snap.get("computed_at"),
            "old_ni": old_ni, "new_ni": new_ni, "delta": round(new_ni - old_ni, 2),
            "old_gp": round(float(old_pl.get("gross_profit") or 0), 2),
            "new_gp": round(float(new_pl.get("gross_profit") or 0), 2),
            "lines": deltas,
            "device_cogs_meta": (inputs.get("device_cost") or {}).get("meta") or {},
            "displaced_pos_cost": (inputs.get("device_cost") or {}).get("displaced_pos_cost"),
        })

    if args.json:
        print(json.dumps({"org_id": args.org, "preview_config": args.preview_config, "periods": rows},
                         indent=2, default=str))
        return 0

    print("\nBEFORE / AFTER LEDGER — org %s%s" % (args.org, "  [PREVIEW CONFIG]" if args.preview_config else ""))
    print("READ-ONLY. Nothing was written. Executing the recompute is the owner's Gate-2 call.\n")
    print("%-14s %16s %16s %14s   %s" % ("PERIOD", "OLD NET INCOME", "NEW NET INCOME", "DELTA", "SNAPSHOT"))
    print("-" * 100)
    tot_old = tot_new = 0.0
    for r in rows:
        if r.get("new_ni") is None:
            print("%-14s %16.2f %16s %14s   %s" % (r["period"], r["old_ni"], "ERROR", "-", r["error"]))
            continue
        tot_old += r["old_ni"]
        tot_new += r["new_ni"]
        print("%-14s %16.2f %16.2f %14.2f   %s"
              % (r["period"], r["old_ni"], r["new_ni"], r["delta"], str(r.get("computed_at"))[:19]))
    print("-" * 100)
    print("%-14s %16.2f %16.2f %14.2f" % ("TOTAL", tot_old, tot_new, round(tot_new - tot_old, 2)))

    for r in rows:
        if r.get("new_ni") is None or not r["lines"]:
            continue
        print("\n  %s — lines that move:" % r["period"])
        for label, d in sorted(r["lines"].items(), key=lambda kv: -abs(kv[1]["delta"])):
            print("    %-52s %14.2f -> %14.2f  (%+.2f)" % (label[:52], d["old"], d["new"], d["delta"]))
        if r.get("displaced_pos_cost") is not None:
            print("    (POS device cost displaced by the invoice source: %.2f)" % r["displaced_pos_cost"])
        meta = r.get("device_cogs_meta") or {}
        if meta.get("ma"):
            m = meta["ma"]
            print("    device COGS coverage: %s rows -> %s distinct IMEI (%s dup dropped); "
                  "%s priced, %s unknown-SKU, %s unpriced  ⇐ THE UN-LINKABLE REMAINDER"
                  % (m.get("rows"), m.get("distinct_imei"), m.get("dedup_dropped"),
                     m.get("priced"), m.get("unknown_sku"), m.get("unpriced_sku")))
        if meta.get("honest_zero"):
            print("    ⚠️  %s" % meta["honest_zero"])
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
