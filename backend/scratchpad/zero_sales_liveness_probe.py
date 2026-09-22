"""OPERATOR PROBE — how loud will the Zero Sales report be on day one? READ-ONLY.

Answers the two questions the build gate asks before this report is shown to anyone:

  1. HOW MANY STORE-PERIODS HAVE NO FEED AT ALL — i.e. how many store-days in the window land in the
     'not reported' state. That is the number that says how much of this report is a data problem
     rather than a sales problem, and it is the number that would have been printed as "$0 / zero
     sales" by a report that did not distinguish absence from zero.
  2. HOW MANY STORE-DAYS WOULD HAVE ALERTED at the house default N=2 over the trailing 30 days.

Every figure is produced by calling the REAL `router._zero_sales_core` — the same code the endpoint
runs — so there is no second implementation to drift. The client is wrapped so insert/update/upsert/
delete raise before they reach PostgREST: it cannot write. Tenants are DISCOVERED from
storeops.tenants, never hard-coded (RULE ONE). No customer name, MDN, phone number or ZIP is read or
printed — only store codes, rep display names, counts and dates.

    python3 backend/scratchpad/zero_sales_liveness_probe.py [--days 30] [--org-id …]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _ROQuery:
    def __init__(self, q):
        self._q = q

    def __getattr__(self, name):
        if name in ("insert", "update", "upsert", "delete"):
            raise RuntimeError(f"BLOCKED: this probe is read-only; refusing {name}()")
        v = getattr(self._q, name)
        if callable(v):
            def _w(*a, **k):
                r = v(*a, **k)
                return _ROQuery(r) if hasattr(r, "execute") or hasattr(r, "select") else r
            return _w
        return v


class _ROSchema:
    def __init__(self, s):
        self._s = s

    def table(self, t):
        return _ROQuery(self._s.table(t))

    def rpc(self, *a, **k):
        return _ROQuery(self._s.rpc(*a, **k))


class ReadOnlyClient:
    def __init__(self, c):
        self._c = c

    def schema(self, s):
        return _ROSchema(self._c.schema(s))

    def table(self, t):
        return _ROQuery(self._c.table(t))

    def rpc(self, *a, **k):
        return _ROQuery(self._c.rpc(*a, **k))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--org-id", default=None)
    a = ap.parse_args()

    from datetime import date, timedelta
    from app.core.database import get_supabase
    from app.modules.commcalc import router as R
    from app.modules.commcalc import zero_sales as Z

    raw = get_supabase()
    client = ReadOnlyClient(raw)
    try:
        tenants = raw.schema("storeops").table("tenants").select("org_id,name").execute().data or []
    except Exception as e:
        print("could not list tenants:", e)
        return 1
    if a.org_id:
        tenants = [t for t in tenants if str(t.get("org_id")) == a.org_id]

    today = date.today()
    end = (today - timedelta(days=1)).isoformat()
    start = (today - timedelta(days=a.days)).isoformat()
    print(f"window {start} .. {end}   (N = house default {Z.HOUSE_CONFIG['consecutive_days']})\n")

    grand = {"stores": 0, "not_reported": 0, "measured_zero": 0, "had_sales": 0, "closed": 0,
             "alerting": 0, "unknown_cal": 0, "store_periods_no_feed": 0}
    for t in tenants:
        oid = t.get("org_id")
        name = str(t.get("name") or oid)[:34]
        try:
            rep = R._zero_sales_core(client, oid, start, end, as_of=today.isoformat())
        except Exception as e:
            print(f"{name:34s}  ERROR {type(e).__name__}: {str(e)[:120]}")
            continue
        srows = [r for r in rep["rows"] if r["grain"] == "store"]
        rrows = [r for r in rep["rows"] if r["grain"] == "rep"]
        nr = sum(r["days_not_reported"] for r in srows)
        mz = sum(r["days_measured_zero"] for r in srows)
        hs = sum(r["days_had_sales"] for r in srows)
        cl = sum(r["days_closed"] for r in srows)
        alerting = [r for r in srows if r["alerting"]]
        # "store-periods with no feed at all" — a store that reported on NO day in the window.
        dead = [r for r in srows if r["days_not_reported"] and not r["days_measured_zero"]
                and not r["days_had_sales"]]
        unknown = [r for r in srows if r["trading_calendar"] == "unknown"]
        # DAY-BY-DAY ALERT VOLUME: replay the sweep as if it had run every morning of the window.
        # An alert fires on day d when the run ENDING at d has reached N measured zeros; dedup is
        # per (store, last-zero-day), so that count IS the number of alert rows a manager would have
        # seen over the window. Uses the engine's own run rules — no second walk.
        N = rep["config"]["consecutive_days"]
        alert_days = 0
        alert_runs = 0
        for r in srows:
            run = 0
            for d in rep["days"]:
                st = r["day_states"].get(d)
                if st in Z.SKIPPED_STATES or st is None:
                    continue
                if st == Z.HAD_SALES:
                    run = 0
                elif st == Z.MEASURED_ZERO:
                    run += 1
                    if run >= N:
                        alert_days += 1
                        if run == N:
                            alert_runs += 1
                elif st == Z.NOT_REPORTED:
                    run = 0 if rep["config"]["gap_policy"] == Z.GAP_BREAK else run
        grand["alert_days"] = grand.get("alert_days", 0) + alert_days
        grand["alert_runs"] = grand.get("alert_runs", 0) + alert_runs
        print(f"{name:34s}  stores {len(srows):3d}  reps {len(rrows):4d}  "
              f"rows scanned {rep['scanned_rows']:7d}")
        print(f"{'':34s}  store-days: had_sales {hs:5d} | MEASURED ZERO {mz:5d} | "
              f"NOT REPORTED {nr:5d} | closed {cl:4d}")
        print(f"{'':34s}  stores with NO feed at all in the window: {len(dead):3d}"
              f"   would alert at N=2: {len(alerting):3d}"
              f"   unknown trading calendar: {len(unknown):3d}")
        if rep.get("rules_refused"):
            print(f"{'':34s}  RULE REFUSED — this org claims no zeros: {str(rep.get('note'))[:110]}")
        if alerting:
            print(f"{'':34s}  alerting stores: "
                  + ", ".join(f"{r['label']}({r['zero_days']}d)" for r in alerting[:12])
                  + (" …" if len(alerting) > 12 else ""))
        if dead:
            print(f"{'':34s}  no-feed stores: " + ", ".join(r["label"] for r in dead[:12])
                  + (" …" if len(dead) > 12 else ""))
        print(f"{'':34s}  replayed daily at N={N}: {alert_days} store-day alert row(s), "
              f"from {alert_runs} distinct run(s)")
        grand["stores"] += len(srows)
        grand["not_reported"] += nr
        grand["measured_zero"] += mz
        grand["had_sales"] += hs
        grand["closed"] += cl
        grand["alerting"] += len(alerting)
        grand["unknown_cal"] += len(unknown)
        grand["store_periods_no_feed"] += len(dead)
        print()

    print("=" * 78)
    print("PLATFORM TOTAL over the window")
    print(f"  stores in scope ....................... {grand['stores']}")
    print(f"  store-days HAD SALES .................. {grand['had_sales']}")
    print(f"  store-days MEASURED ZERO (the finding)  {grand['measured_zero']}")
    print(f"  store-days NOT REPORTED (no feed) ..... {grand['not_reported']}"
          "   <- printed as zero by a two-state report")
    print(f"  store-days CLOSED (not trading) ....... {grand['closed']}")
    print(f"  stores with NO feed at all ............ {grand['store_periods_no_feed']}")
    print(f"  stores that would ALERT at N=2 ........ {grand['alerting']}")
    print(f"  stores with an UNKNOWN trading calendar {grand['unknown_cal']}")
    print(f"  REPLAYED DAILY at N=2 over the window:")
    print(f"    store-day alert rows .............. {grand.get('alert_days', 0)}")
    print(f"    distinct runs that tripped N=2 .... {grand.get('alert_runs', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
