#!/usr/bin/env python3
"""Derive a weekly shift template per rep from POS history, and EMIT THE SQL -- never apply it.

Owner request 2026-10-03: "Add the 9 sales reps from the 9 stores we worked on a schedule as they
have been working in the past, this needs analysis based on work history and then assign schedule,
again nothing is hard coded just as user entered and editable."

READ-ONLY. This tool SELECTs from `commcalc.raw_sales`, `commcalc.store_mapping`,
`commcalc.store_aliases` and `storeops.employees`, and writes nothing. Its output is SQL for a human
to read and run, which is the house rule for anything money-adjacent.

It is the CALLER that wires `app/modules/storeops/schedule_from_history.py` (the pure derivation) to
the mechanism that already exists -- `storeops.shift_templates` (mig 040) plus
`POST /storeops/shift-templates/apply`. No new table, no second scheduling path.

RULE TWO: no store, rep or tenant name is written in this file. Stores come from the roster, reps
from `storeops.employees`, thresholds from the config block below (all overridable on the command
line), and the store string -> store code binding from the platform's own resolver inputs
(`store_mapping` + `store_aliases`, index section 13) rather than an exact-address match.

Usage:
  python3 tools/derive_schedule_from_history.py --org <uuid> --periods "July 2026,August 2026" \
      --stores B-60TH,B-1710,...  --rate 17 --entered-monthly 4500 --month 2026-08
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.storeops import schedule_from_history as sfh   # noqa: E402

PAGE = 1000


def _fetch_all(table, schema, select, eqs=None, ins=None, order="id"):
    """Paged SELECT with a DETERMINISTIC order. PostgREST `range()` without an ORDER BY can skip or
    repeat rows between pages -- an unordered page walk silently lost 58 rows of one rep's August
    history while this tool was being written, which would have shortened a real schedule."""
    from app.core.database import get_supabase_admin
    sb = get_supabase_admin()
    out, off = [], 0
    while True:
        q = sb.schema(schema).table(table).select(select)
        for k, v in (eqs or {}).items():
            q = q.eq(k, v)
        for k, v in (ins or {}).items():
            q = q.in_(k, v)
        rows = q.order(order).range(off, off + PAGE - 1).execute().data or []
        out += rows
        if len(rows) < PAGE:
            return out
        off += PAGE


def _norm(s):
    return " ".join((s or "").strip().split()).lower()


def build_resolver(org, codes):
    """raw POS store string -> store code, from `store_mapping` + `store_aliases` ONLY (the same two
    tables the platform's store resolver reads), narrowed to the codes asked for. Whitespace and case
    are folded because a feed writes 'X Ave ' and 'X Ave' for one store."""
    mapping = _fetch_all("store_mapping", "commcalc", "store_code,store_address",
                         eqs={"org_id": org}, order="store_code")
    aliases = _fetch_all("store_aliases", "commcalc", "alias,store_code",
                         eqs={"org_id": org}, order="alias")
    want = set(codes)
    # One store can carry two codes (a relocation retires one). Fold every code that shares an
    # address with a wanted code onto the wanted code, so history filed under the retired code is
    # not lost and is not counted as a second store.
    addr_of = {r["store_code"]: _norm(r.get("store_address")) for r in mapping}
    addr_to_wanted = {addr_of[c]: c for c in want if addr_of.get(c)}
    table, known = {}, {}
    for r in mapping:
        folded = addr_to_wanted.get(_norm(r.get("store_address"))) or r["store_code"]
        for k in (_norm(r.get("store_address")), _norm(r["store_code"])):
            if k:
                known[k] = folded
                if folded in want:
                    table[k] = folded
    for r in aliases:
        code = r.get("store_code")
        folded = addr_to_wanted.get(addr_of.get(code, "\x00")) or code
        k = _norm(r.get("alias"))
        if k:
            known[k] = folded
            if folded in want:
                table[k] = folded
    # `known` is every string the platform CAN bind; `table` is the subset in scope. The split keeps
    # "another store, not asked for" from being reported as "a store string nothing recognises",
    # which is the only one of the two that is a data defect.
    return (lambda raw: table.get(_norm(raw))), table, known


def sql_literal(s):
    return "'" + str(s).replace("'", "''") + "'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--periods", required=True, help="comma-separated raw_sales period labels")
    ap.add_argument("--stores", required=True, help="comma-separated store codes")
    ap.add_argument("--rate", type=float, default=17.0)
    ap.add_argument("--entered-monthly", type=float, default=0.0)
    ap.add_argument("--month", default="", help="YYYY-MM for the monthly-hours cross-check")
    ap.add_argument("--regular-min-days", type=int, default=sfh.DEFAULTS["regular_min_days"])
    ap.add_argument("--weekday-min-fraction", type=float, default=sfh.DEFAULTS["weekday_min_fraction"])
    ap.add_argument("--open-percentile", type=float, default=sfh.DEFAULTS["open_percentile"])
    ap.add_argument("--close-percentile", type=float, default=sfh.DEFAULTS["close_percentile"])
    ap.add_argument("--round-minutes", type=int, default=sfh.DEFAULTS["round_minutes"])
    ap.add_argument("--weekday-denominator", default=sfh.DEFAULTS["weekday_denominator"],
                    choices=("traded", "calendar"))
    ap.add_argument("--day-groups", default=json.dumps(sfh.DEFAULTS["day_groups"]))
    ap.add_argument("--json", action="store_true", help="emit the derivation as JSON instead of SQL")
    a = ap.parse_args()

    codes = [c.strip() for c in a.stores.split(",") if c.strip()]
    periods = [p.strip() for p in a.periods.split(",") if p.strip()]
    cfg = sfh.config({
        "regular_min_days": a.regular_min_days,
        "weekday_min_fraction": a.weekday_min_fraction,
        "open_percentile": a.open_percentile,
        "close_percentile": a.close_percentile,
        "round_minutes": a.round_minutes,
        "day_groups": json.loads(a.day_groups),
        "weekday_denominator": a.weekday_denominator,
    })

    resolver, table, known = build_resolver(a.org, codes)
    employees = _fetch_all("employees", "storeops", "employee_id,name,home_store,pay_basis,pay_rate",
                           eqs={"org_id": a.org}, order="employee_id")
    emp_by_name = {_norm(e.get("name")): e for e in employees}

    raw = _fetch_all("raw_sales", "commcalc", "store,salesperson,trans_date,trans_ts,period",
                     eqs={"org_id": a.org}, ins={"period": periods})
    rows, unresolved = sfh.resolve_rows(
        [{"store": r.get("store"), "rep": r.get("salesperson"), "date": r.get("trans_date"),
          "month": r.get("period"),
          # The feed stamps local store wall-clock and labels it +00:00; the hour histogram runs
          # 09:00-19:00, which is a shop's trading day and not a UTC offset of one. Taken as local.
          "time": (r.get("trans_ts") or "")[11:16] or None} for r in raw],
        resolver)
    # Only reps that exist on the roster get a template; a POS name with no employee row is reported.
    off_roster = sorted({r["rep"] for r in rows if _norm(r["rep"]) not in emp_by_name})
    rows = [r for r in rows if _norm(r["rep"]) in emp_by_name]
    out_of_scope = {k: v for k, v in unresolved.items() if _norm(k) in known}
    unresolved = {k: v for k, v in unresolved.items() if _norm(k) not in known}

    spans = sfh.day_spans(rows)
    # Any rostered rep at a wanted store with NO history still needs a proposal -- as an assumption.
    seen_pairs = {(s, rp) for (s, rp, _d) in spans}
    assume = [{"store_code": e["home_store"], "rep": e["name"]} for e in employees
              if e.get("home_store") in codes
              and not any(rp == e["name"] for (_s, rp) in seen_pairs)]
    out = sfh.weekly_template(spans, cfg, assignments=assume)

    year, month = (int(a.month[:4]), int(a.month[5:7])) if a.month else (0, 0)
    if a.json:
        payload = {"rows": out["rows"], "relief": out["relief"], "house": out["house"],
                   "hours": {f"{k[0]}|{k[1]}": v for k, v in out["hours"].items()},
                   "unresolved": unresolved, "out_of_scope_stores": sorted(out_of_scope),
                   "off_roster": off_roster,
                   "resolver_entries": len(table),
                   "profiles": {f"{k[0]}|{k[1]}": {kk: vv for kk, vv in v.items()}
                                for k, v in out["profiles"].items()}}
        if year:
            per = {}
            for r in out["rows"]:
                k = (r["store_code"], r["rep"])
                per.setdefault(k, 0.0)
            for k in per:
                h = sfh.monthly_hours(out["rows"], year, month, store_code=k[0], rep=k[1])
                per[k] = sfh.rate_check(h, a.rate, a.entered_monthly or h * a.rate)
            payload["month_check"] = {f"{k[0]}|{k[1]}": v for k, v in per.items()}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print("-- shift_templates rows derived from POS history. Review before running.")
    for r in out["rows"]:
        e = emp_by_name[_norm(r["rep"])]
        print(f"-- {r['store_code']:8s} {r['rep']:24s} wd={r['weekday']} "
              f"{r['start_time']}-{r['end_time']} {r['scheduled_hours']}h "
              f"[{r['evidence']} n={r['samples']} worked {r['days_worked']}/{r['days_possible']}]")
        print(f"INSERT INTO storeops.shift_templates (org_id, employee_id, employee_name, "
              f"store_code, weekday, start_time, end_time, scheduled_hours) VALUES "
              f"({sql_literal(a.org)}, {sql_literal(e['employee_id'])}, {sql_literal(e['name'])}, "
              f"{sql_literal(r['store_code'])}, {r['weekday']}, {sql_literal(r['start_time'])}, "
              f"{sql_literal(r['end_time'])}, {r['scheduled_hours']})")
    if unresolved:
        print("-- UNRESOLVED feed store strings (a data defect -- reported, never guessed):",
              json.dumps(unresolved))
    if off_roster:
        print("-- POS names with no employee row (reported):", json.dumps(off_roster))
    for rel in out["relief"]:
        print(f"-- RELIEF, no recurring rows: {rel['store_code']} {rel['rep']} "
              f"days={rel['days']} {rel['days_by_month']}")


if __name__ == "__main__":
    main()
