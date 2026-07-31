"""OPERATOR SCRIPT — the accessory %-of-GP options table, straight off live data. READ-ONLY.

This codespace has no Supabase credentials, so no number in the handoff was invented. Run this from
a machine that HAS them and it prints, per tenant, the same table the in-app Accessory Cost Audit
renders: which lines a %-of-basis rule pays on, which of them have an unusable cost, and what the
period WOULD have paid under each option.

    cd backend
    export SUPABASE_URL=...  SUPABASE_SERVICE_KEY=...
    python3 scratchpad/accessory_cost_options_report.py --period "July 2026" > options.md
    python3 scratchpad/accessory_cost_options_report.py --period "July 2026" --org-id <uuid>
    python3 scratchpad/accessory_cost_options_report.py --period "July 2026" \
            --c-basis assumed_gp --assume-gp-pct 0.40

SAFETY BY CONSTRUCTION:
  • the Supabase client is wrapped so insert/update/upsert/delete RAISE — it cannot write;
  • it calls the REAL `accessory_cost_audit.audit`, whose `current` column comes from the REAL
    `commission_engine.preview`, so there is no second implementation to drift;
  • tenants are DISCOVERED from storeops.tenants — org_id is never hard-coded;
  • nothing recomputes: this never touches POST /calculate (see [[recompute-gateway-timeout]]).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class ReadOnly:
    """Transparent proxy that forwards reads and RAISES on any write verb."""
    _BLOCKED = ("insert", "update", "upsert", "delete", "rpc")

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        if name in ReadOnly._BLOCKED:
            raise RuntimeError(f"READ-ONLY GUARD: {name}() attempted")
        v = getattr(object.__getattribute__(self, "_inner"), name)
        if callable(v):
            def wrap(*a, **k):
                return ReadOnly(v(*a, **k))
            return wrap
        return v


def money(v):
    return "—" if v is None else f"${float(v):,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="e.g. 'July 2026'")
    ap.add_argument("--org-id", default="", help="restrict to ONE tenant (default: every tenant)")
    ap.add_argument("--c-basis", default="price", choices=("price", "assumed_gp"))
    ap.add_argument("--assume-gp-pct", type=float, default=None)
    ap.add_argument("--max-items", type=int, default=60)
    args = ap.parse_args()

    if not (os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_SERVICE_KEY")
                                           or os.getenv("SUPABASE_KEY"))):
        print("SUPABASE_URL + SUPABASE_SERVICE_KEY must be exported.", file=sys.stderr)
        return 2

    from app.core.database import get_supabase
    from app.modules.commcalc import accessory_cost_audit as aca

    raw = get_supabase()
    client = ReadOnly(raw)

    if args.org_id:
        tenants = [{"org_id": args.org_id, "name": args.org_id}]
    else:
        tenants = (raw.schema("storeops").table("tenants").select("org_id,name")
                   .order("name").execute().data) or []

    print(f"# Accessory %-of-GP options — {args.period}\n")
    print("READ-ONLY. Nothing below has been applied; `Today` is what the engine pays right now.\n")

    for t in tenants:
        org = t.get("org_id")
        try:
            a = aca.audit(client, org, args.period, c_basis=args.c_basis,
                          assume_gp_pct=args.assume_gp_pct)
        except Exception as e:
            print(f"\n## {t.get('name')} — ERROR: {type(e).__name__}: {e}\n")
            continue
        if not a.get("ready"):
            print(f"\n## {t.get('name')} — {a.get('note')}\n")
            continue
        c = a["counts"]
        if not c["matched_lines"]:
            print(f"\n## {t.get('name')} — no %-of-basis rule matched a line. {a.get('note') or ''}\n")
            continue

        print(f"\n## {t.get('name')}  (`{org}`)\n")
        print(f"{c['matched_lines']} matched line(s) · **{c['suspect_lines']} with an unusable cost** "
              f"· {c['reps']} rep(s) · {c['rules']} rule(s)\n")

        print("### The rules doing the paying\n")
        print("| Rule | Pays | Stored rate | Reads as | Lines | Suspect | Paid today | Check |")
        print("|---|---|---:|---:|---:|---:|---:|---|")
        for r in a["rules"]:
            print(f"| {r['label'] or r['rule_id']} | {r['payout_kind']} | {r['pct']} | "
                  f"{float(r['pct']) * 100:.2f}% | {r['matched_lines']} | {r['suspect_lines']} | "
                  f"{money(r['paid'])} | {' '.join(r.get('rate_flags') or []) or 'ok'} |")

        print("\n### What the period would have paid\n")
        print("| Option | Total | Δ vs today |")
        print("|---|---:|---:|")
        for k in aca.OPTION_KEYS:
            v = a["totals"][k]
            d = "—" if k == "current" else money(v - a["totals"]["current"])
            if k == "option_a" and a["deltas"]["option_a"] == "unknown":
                v, d = None, "unknown until the POS costs are corrected"
            print(f"| {a['option_labels'][k]} | {money(v)} | {d} |")

        print("\n### Per rep\n")
        print("| Rep | Store | Lines | Suspect | Today | B (% of price) | Δ B | C (guarded) | Δ C | R (rate÷100) | Δ R |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in a["by_rep"]:
            print(f"| {r['rep']} | {r.get('store') or ''} | {r['matched_lines']} | {r['suspect_lines']} | "
                  f"{money(r['current'])} | {money(r['option_b'])} | {money(r['delta_b'])} | "
                  f"{money(r['option_c'])} | {money(r['delta_c'])} | {money(r['option_r'])} | "
                  f"{money(r['delta_r'])} |")

        print("\n### Items to fix in the POS (Option A worksheet)\n")
        print("| Item | SKU | Lines | Sold | GP | Implied cost min | max | Catalog cost | Check |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---|")
        for it in a["items"][:args.max_items]:
            if not it["flags"]:
                continue
            print(f"| {it['product']} | {it.get('sku') or ''} | {it['lines']} | "
                  f"{money(it['ext_price'])} | {money(it['gp'])} | {money(it['implied_cost_min'])} | "
                  f"{money(it['implied_cost_max'])} | {money(it['catalog_cost'])} | "
                  f"{'; '.join(it['flags'])} |")

    print("\n---\nRead-only guard active for every call in this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
