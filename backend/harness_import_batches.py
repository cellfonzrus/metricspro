"""HARNESS — DDIA Phase 1 import idempotency + the F1 anon-function lockdown (platform-core, 2026-08-09).

TWO CONTROLS, one script, because they shipped together and both are the kind of guarantee that fails
SILENTLY when it regresses — nobody ever reports "my duplicate upload was NOT blocked".

  PART 1 — OFFLINE (no database, no keys; runs anywhere, including CI)
    1. the guard DEGRADES OPEN — with no reachable database, claim() returns `unavailable` and does
       NOT raise, so an unapplied migration can never take the platform's imports offline. This is
       deliberately the opposite of migration 420's fail-closed rule; see import_batches.__doc__.
    2. sha256 is over the raw bytes and distinguishes a one-byte change
    3. `_is_duplicate_error` recognises a unique violation however PostgREST spelled it — a miss here
       would silently downgrade a duplicate to `unavailable`, i.e. LET THE DOUBLE-IMPORT THROUGH
    4. the duplicate response is shaped so nothing is inserted and the operator is told why
    5. `_sweep_ingest_outcome` classifies that response as TERMINAL — a re-delivered email attachment
       must not become a permanently retrying ⚠️ in the sweep history (every other named 0-row marker
       IS retryable, so this branch is load-bearing and easy to lose)

  PART 2 — LIVE, and it WRITES NOTHING: every probe runs inside a DO block that RAISEs at the end, so
  the transaction rolls back. Needs tools/sbsql.py (set SBSQL_PATH if it is not beside the repo).
    6. same bytes + same org → the partial unique index rejects the second claim
    7. a DIFFERENT org is never blocked by another tenant's file (two tenants legitimately upload
       byte-identical carrier templates, and one tenant must never block another)
    8. a FAILED batch releases the hash → the corrected re-upload still works. This is WHY the index
       is partial; get it wrong and a broken import becomes unretryable
    9. no function in our six schemas is EXECUTE-able by anon or authenticated (mig 724)
   10. service_role still executes every one of them (a lockdown that breaks the backend is not a fix)
   11. a NEWLY created function is born locked in every schema — the guarantee mig 724 only appeared
       to give, and mig 731 actually delivers via an event trigger

    python3 backend/harness_import_batches.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import import_batches as ib   # noqa: E402

SCHEMAS = ("core", "commcalc", "storeops", "pos", "notify", "public")
ORG1 = "00000000-0000-0000-0000-0000000dead1"
ORG2 = "00000000-0000-0000-0000-0000000dead2"

_passed, _failed, _skipped = 0, 0, 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + str(detail)[:220]) if detail else ''}")


def find_sbsql():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.environ.get("SBSQL_PATH"),
                 os.path.join(here, "tools", "sbsql.py"),
                 "/workspaces/commcalc/tools/sbsql.py"):
        if cand and os.path.exists(cand):
            return cand
    return None


def sql(stmt, sbsql):
    out = subprocess.run([sys.executable, sbsql, stmt], capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(sbsql)))
    return (out.stdout or "") + (out.stderr or "")


def part1():
    print("\nPART 1 — offline (no database)")

    # 1. Degrade-open. With no SUPABASE_KEY in the environment the client cannot be built at all,
    #    which is the harshest version of "the guard is unreachable".
    res = ib.claim(org_id=ORG1, source="sales", content=b"anything", file_name="a.csv")
    check("1a. unreachable database → 'unavailable', not an exception",
          res.get("state") == ib.UNAVAILABLE, res)
    check("1b. …and it still reports the hash it would have used", len(res.get("sha256", "")) == 64, res)
    for fn, arg in ((ib.complete, {"row_count": 5}), (ib.fail, {"error": "x"})):
        try:
            fn("11111111-1111-1111-1111-111111111111", **arg)
            check(f"1c. {fn.__name__}() never raises", True)
        except Exception as e:
            check(f"1c. {fn.__name__}() never raises", False, e)

    # 2. Hash identity.
    check("2a. sha256 is stable for identical bytes",
          ib.sha256_hex(b"ROW1\nROW2\n") == ib.sha256_hex(b"ROW1\nROW2\n"))
    check("2b. a one-byte change is a different batch",
          ib.sha256_hex(b"ROW1\nROW2\n") != ib.sha256_hex(b"ROW1\nROW3\n"))
    check("2c. an empty file still hashes", len(ib.sha256_hex(b"")) == 64)

    # 3. Unique-violation recognition. Each shape below is a real way the client has surfaced it.
    class _E(Exception):
        def __init__(self, msg, code=None):
            super().__init__(msg)
            self.code = code
    cases = [
        (_E("dup", "23505"), True, "structured code"),
        (_E('duplicate key value violates unique constraint "import_batches_org_hash_uidx"'), True, "message"),
        (_E("SQLSTATE 23505: whatever"), True, "embedded sqlstate"),
        (_E("connection reset by peer"), False, "unrelated network error"),
        (_E("permission denied for table import_batches", "42501"), False, "unrelated pg error"),
    ]
    for exc, expect, label in cases:
        check(f"3. duplicate-error detection: {label} → {expect}",
              ib._is_duplicate_error(exc) is expect, exc)

    # 4. The refusal the operator sees.
    resp = ib.duplicate_response("sales", {"sha256": "a" * 64, "prior": {
        "file_name": "May.xlsx", "created_at": "2026-08-01T10:00:00+00:00", "row_count": 1234}})
    check("4a. refusal is status=skipped with the terminal marker",
          resp.get("status") == "skipped" and resp.get("skipped") == "duplicate_file", resp)
    check("4b. refusal inserts zero rows", resp.get("rows") == 0, resp)
    check("4c. refusal names the prior file, date and row count",
          all(t in resp["reason"] for t in ("May.xlsx", "2026-08-01", "1,234")), resp["reason"])
    check("4d. refusal tells the operator how to override", "force=true" in resp["reason"], resp["reason"])

    # 5. How the sweeps read it. Imported from the router itself — replicating the logic here would
    #    prove nothing about the code that actually runs.
    try:
        from app.modules.commcalc.router import _sweep_ingest_outcome
    except Exception as e:
        check("5. sweep classifies a duplicate as terminal", False, f"could not import router: {e}")
        return
    out = _sweep_ingest_outcome(resp, upload_type="sales")
    check("5a. sweep status is 'skipped'", out["status"] == "skipped", out)
    check("5b. sweep marks it TERMINAL (no infinite retry of identical bytes)", out["terminal"] is True, out)
    check("5c. sweep records 0 rows saved", out["rows_saved"] == 0, out)
    other = _sweep_ingest_outcome({"status": "skipped", "skipped": "price_guard", "rows": 0},
                                  upload_type="sales")
    check("5d. control — an ordinary 0-row marker is still RETRYABLE", other["terminal"] is False, other)


def part2():
    global _skipped
    print("\nPART 2 — live (rolled back; writes nothing)")
    sbsql = find_sbsql()
    if not sbsql:
        _skipped += 1
        print("  SKIP  tools/sbsql.py not found — set SBSQL_PATH to run the live half")
        return

    probe = (
        "DO $$ DECLARE r text := ''; b1 uuid; "
        "BEGIN "
        f"INSERT INTO core.import_batches(org_id,source,format_version,file_sha256,file_bytes,status) "
        f"VALUES ('{ORG1}','sales','v1','harness_hash_1',10,'parsing') RETURNING id INTO b1; "
        "BEGIN "
        f"INSERT INTO core.import_batches(org_id,source,format_version,file_sha256,file_bytes,status) "
        f"VALUES ('{ORG1}','sales','v1','harness_hash_1',10,'parsing'); r := r || 'DUP_BLOCKED=f '; "
        "EXCEPTION WHEN unique_violation THEN r := r || 'DUP_BLOCKED=t '; END; "
        "BEGIN "
        f"INSERT INTO core.import_batches(org_id,source,format_version,file_sha256,file_bytes,status) "
        f"VALUES ('{ORG2}','sales','v1','harness_hash_1',10,'parsing'); r := r || 'OTHER_ORG_OK=t '; "
        "EXCEPTION WHEN unique_violation THEN r := r || 'OTHER_ORG_OK=f '; END; "
        "UPDATE core.import_batches SET status='failed' WHERE id=b1; "
        "BEGIN "
        f"INSERT INTO core.import_batches(org_id,source,format_version,file_sha256,file_bytes,status) "
        f"VALUES ('{ORG1}','sales','v1','harness_hash_1',10,'parsing'); r := r || 'RETRY_AFTER_FAIL=t '; "
        "EXCEPTION WHEN unique_violation THEN r := r || 'RETRY_AFTER_FAIL=f '; END; "
        "RAISE EXCEPTION 'PROBE %', r; END $$;")
    text = sql(probe, sbsql)
    check("6. same bytes + same org → second claim REJECTED", "DUP_BLOCKED=t" in text, text)
    check("7. a different org is NOT blocked by our file", "OTHER_ORG_OK=t" in text, text)
    check("8. a FAILED batch releases the hash for a genuine retry",
          "RETRY_AFTER_FAIL=t" in text, text)

    inlist = ", ".join(f"'{s}'" for s in SCHEMAS)
    grants = sql(
        "select count(*) filter (where has_function_privilege('anon', p.oid,'EXECUTE')) as anon, "
        "count(*) filter (where has_function_privilege('authenticated', p.oid,'EXECUTE')) as auth, "
        "count(*) filter (where has_function_privilege('service_role', p.oid,'EXECUTE')) as svc, "
        "count(*) as total from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        f"where n.nspname in ({inlist})", sbsql)
    try:
        r = json.loads(grants)[0]
    except Exception:
        check("9/10. could read function grants", False, grants)
        return
    check("9a. zero functions executable by anon", int(r["anon"]) == 0, r)
    check("9b. zero functions executable by authenticated", int(r["auth"]) == 0, r)
    check("10. service_role still executes every function", int(r["svc"]) == int(r["total"]), r)

    for schema in SCHEMAS:
        out = sql("DO $$ DECLARE e boolean; BEGIN "
                  f"CREATE FUNCTION {schema}._harness_probe_fn() RETURNS int LANGUAGE sql AS 'select 1'; "
                  f"SELECT has_function_privilege('anon','{schema}._harness_probe_fn()','EXECUTE') INTO e; "
                  "RAISE EXCEPTION 'PROBE anon_exec=%', e; END $$;", sbsql)
        check(f"11. a new function in {schema} is born locked", "PROBE anon_exec=f" in out, out)


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    part1()
    part2()
    print(f"\n{_passed} passed / {_failed} failed" + (f" / {_skipped} skipped" if _skipped else ""))
    sys.exit(1 if _failed else 0)
