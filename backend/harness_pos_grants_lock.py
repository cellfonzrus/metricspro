"""LOCK — the SSN / driver's-licence functions migration 909 switched off stay switched off.

THE DEFECT (2026-09-24). Migration 909 (owner decision) revoked EXECUTE on pos.customer_pii_set / _get / _last4 and
pos.pii_key from every role, service_role included. Migration 1017 copied the mig-725 POS lockdown block, which
contains `GRANT ALL ON ALL FUNCTIONS IN SCHEMA pos TO service_role` — and so re-opened three of them to the backend's
service key. Migration 1019 puts 909's state back.

THE CLASS: a copied schema-wide GRANT silently undoes a later, narrower REVOKE. This lock fails the build when:
  (a) a migration numbered after 909 grants ALL FUNCTIONS IN SCHEMA pos (to anyone);
  (b) a migration numbered after 909 grants anything on one of the four PII functions;
  (c) the repair (1019) stops revoking all four from service_role.
Comment lines (`--`) are ignored — a migration may DESCRIBE the grant it must not make.

Stdlib only: run `python backend/harness_pos_grants_lock.py` from the repo root or from backend/.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = os.path.join(HERE, "..", "database", "migrations")
DISABLED_AT = 909                       # the migration that switched the PII functions off
REPAIR = "1019_pos_pii_functions_stay_revoked.sql"
PII_FUNCTIONS = ("customer_pii_set", "customer_pii_get", "customer_pii_last4", "pii_key")

SCHEMA_WIDE = re.compile(r"\bGRANT\b[^;']*\bON\s+ALL\s+FUNCTIONS\s+IN\s+SCHEMA\s+pos\b", re.I)
ONE_PII = re.compile(r"\bGRANT\b[^;']*\bON\s+FUNCTION\s+pos\.(" + "|".join(PII_FUNCTIONS) + r")\b", re.I)
REVOKE_PII = re.compile(r"\bREVOKE\s+ALL\s+ON\s+FUNCTION\s+pos\.(" + "|".join(PII_FUNCTIONS) + r")\b[^;]*\bservice_role\b", re.I)


def _code(sql):
    """The SQL with `--` comments removed (a migration's header may describe what it must not do)."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _number(name):
    m = re.match(r"(\d+)_", name)
    return int(m.group(1)) if m else None


def violations(files):
    """files: {name: sql text} → [plain sentences]; [] = the lock holds."""
    out = []
    for name in sorted(files):
        n = _number(name)
        if n is None or n <= DISABLED_AT:
            continue
        code = _code(files[name])
        if SCHEMA_WIDE.search(code):
            out.append(f"{name}: grants ALL FUNCTIONS IN SCHEMA pos — that re-opens the PII functions migration 909 switched off; "
                       "grant the specific new function instead")
        for m in ONE_PII.finditer(code):
            out.append(f"{name}: grants on pos.{m.group(1)} — switched off by migration 909 (an owner decision)")
    repair = files.get(REPAIR)
    if repair is None:
        out.append(f"{REPAIR} is missing — it is what puts migration 909's revokes back after 1017")
    else:
        got = {m.group(1) for m in REVOKE_PII.finditer(_code(repair))}
        missing = [f for f in PII_FUNCTIONS if f not in got]
        if missing:
            out.append(f"{REPAIR} no longer revokes from service_role: " + ", ".join(missing))
    return out


def _load():
    return {f: open(os.path.join(MIGRATIONS, f), encoding="utf-8").read()
            for f in os.listdir(MIGRATIONS) if f.endswith(".sql")}


def main():
    passed = failed = 0

    def check(label, ok):
        nonlocal passed, failed
        print(("  PASS  " if ok else "  FAIL  ") + label)
        passed += ok
        failed += (not ok)

    files = _load()
    real = violations(files)
    for v in real:
        print("  ✗ " + v)
    check("the repository's migrations hold the lock", real == [])

    # negative controls — each must go RED
    base = dict(files)
    bad = dict(base, **{"2000_x.sql": "DO $$ BEGIN EXECUTE 'GRANT ALL ON ALL FUNCTIONS IN SCHEMA pos TO service_role'; END $$;"})
    check("a later schema-wide function grant → RED", bool(violations(bad)))
    bad = dict(base, **{"2000_x.sql": "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pos TO authenticated;"})
    check("a schema-wide EXECUTE grant to another role → RED", bool(violations(bad)))
    bad = dict(base, **{"2000_x.sql": "GRANT EXECUTE ON FUNCTION pos.customer_pii_get(UUID, UUID) TO service_role;"})
    check("a grant on one PII function → RED", bool(violations(bad)))
    no_repair = {k: v for k, v in base.items() if k != REPAIR}
    check("the repair migration deleted → RED", bool(violations(no_repair)))
    if REPAIR in base:
        weak = dict(base, **{REPAIR: re.sub(r"REVOKE ALL ON FUNCTION pos\.customer_pii_last4[^;]*;", "", base[REPAIR])})
        check("the repair stops revoking one function → RED", bool(violations(weak)))
    ok = dict(base, **{"2000_x.sql": "-- never: GRANT ALL ON ALL FUNCTIONS IN SCHEMA pos TO service_role\nGRANT EXECUTE ON FUNCTION pos.new_fn() TO service_role;"})
    check("a comment describing the grant, plus a grant on a NEW function → still green", violations(ok) == [])
    old = dict(base, **{"0725_old.sql": "EXECUTE 'GRANT ALL ON ALL FUNCTIONS IN SCHEMA pos TO service_role';"})
    check("a migration before 909 is history, not a violation", violations(old) == [])

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("OK — the PII functions switched off in 909 stay off; no later migration re-grants them.")


if __name__ == "__main__":
    main()
