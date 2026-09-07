"""PROOF: every self-healing cron registrar calls its RPC in the schema the migration defines it in.

OWNER 2026-09-07: *"Railway deployed"*. Verifying that the boot hooks had actually taken effect turned
up a defect that had been swallowing three of them.

THE BUG. Each `_ensure_*_cron()` helper self-registers a pg_cron job on every boot by calling an
idempotent RPC. The shared Supabase client is built with NO options, so its default PostgREST schema
is `public` — and PostgREST resolves a function ONLY inside the request's schema. Three registrars
called `sb().rpc(...)` with no `.schema()`, while their migrations define the functions elsewhere:

    _ensure_data_sources_cron        -> commcalc.ensure_data_sources_cron        (mig 956)  PORTAL PULLS
    _ensure_doc_expiry_alert_cron    -> storeops.ensure_doc_expiry_alert_cron    (mig 967)  LEASE/COI EXPIRY
    _ensure_google_reviews_sweep_cron-> storeops.ensure_google_reviews_sweep_cron           REVIEWS SWEEP

Confirmed live 2026-09-07: `public.ensure_data_sources_cron` and `public.ensure_doc_expiry_alert_cron`
both return PGRST202 (no such function), while `core.ensure_system_check_cron` and
`storeops.ensure_doc_expiry_alert_cron` resolve. So those three calls could only ever raise
"function not found".

WHY IT WAS INVISIBLE, and why that is the real lesson. Every one of these hooks is deliberately
best-effort — `except Exception: print("WARN … self-register failed")` — because a cron registrar
must never block boot. That is right. But it means a registrar that can NEVER succeed looks exactly
like one skipped for a missing secret: a WARN line at boot and silence forever after. Each hook's own
docstring says it exists because "mig 241 shipped its cron as a commented-out block for someone to
paste" and scheduling "depended on a human remembering". The hooks replaced that with automation which
then failed just as quietly. `_ensure_account_recompute_cron`, `_ensure_email_sweep_cron` and
`_ensure_system_check_cron` always named their schema — the drift is what a static check catches and a
best-effort try/except never will.

WHAT THIS PINS: for every `ensure_*_cron` RPC call in the codebase, the schema passed to `.schema()`
equals the schema the CREATE FUNCTION in database/migrations/ uses. Both halves are read from the
real files, so adding a registrar or moving a function re-checks itself.

PURE: stdlib only, no DB, no imports of app code — it parses source and SQL.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MIGRATIONS = os.path.join(REPO, "database", "migrations")

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


# ── where each function is actually DEFINED (the migrations are the schema of truth) ─────────────
defined = {}
for fn in sorted(os.listdir(MIGRATIONS)):
    if not fn.endswith(".sql"):
        continue
    sql = open(os.path.join(MIGRATIONS, fn), encoding="utf-8", errors="replace").read()
    for schema, name in re.findall(
            r"create\s+or\s+replace\s+function\s+([a-z_]+)\.(ensure_[a-z_]*cron)", sql, re.I):
        defined[name.lower()] = (schema.lower(), fn)

# ── where each is CALLED, and with which schema ──────────────────────────────────────────────────
def rpc_calls(path):
    """(function_name, schema_or_None, lineno) for every `.rpc("ensure_…cron", …)` in a file."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "rpc" and n.args):
            continue
        first = n.args[0]
        name = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
        if not name or not (name.startswith("ensure_") and name.endswith("cron")):
            continue
        # Walk back down the attribute chain looking for `.schema("x")`. Terminates on ANY node that
        # is not a call/attribute — `sb()` bottoms out at a bare Name, and a harness that raises there
        # reads as "not run" rather than "failed" (index §24, the whole point of that audit).
        schema, cur = None, n.func.value
        while isinstance(cur, (ast.Call, ast.Attribute)):
            if isinstance(cur, ast.Call) and getattr(cur.func, "attr", "") == "schema" and cur.args:
                a = cur.args[0]
                if isinstance(a, ast.Constant):
                    schema = a.value
                break
            nxt = cur.func if isinstance(cur, ast.Call) else cur.value
            cur = getattr(nxt, "value", None) if isinstance(cur, ast.Call) else nxt
            if cur is None:
                break
        out.append((name, schema, n.lineno))
    return out


calls = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(root, f)
            for name, schema, line in rpc_calls(p):
                calls.append((name, schema, os.path.relpath(p, HERE), line))

print("=" * 78)
print("A. Every cron registrar is matched to a real function")
print("=" * 78)
check("A1 the migrations define at least one ensure_*_cron function",
      len(defined) >= 3, str(sorted(defined)))
check("A2 at least three registrars call one (the hooks exist)",
      len(calls) >= 3, str(calls))
unknown = [c for c in calls if c[0] not in defined]
check("A3 every call names a function some migration actually creates "
      "(a typo here is a registrar that can never succeed)",
      not unknown, str(unknown))

print()
print("=" * 78)
print("B. THE DEFECT: the call's schema must be the definition's schema")
print("=" * 78)
# The shared client is built with no options, so an omitted .schema() means PostgREST looks in
# `public` — where none of these functions live.
DEFAULT = "public"
wrong = []
for name, schema, path, line in calls:
    if name not in defined:
        continue
    want = defined[name][0]
    got = schema or DEFAULT
    if got != want:
        wrong.append(f"{path}:{line} {name} -> called in '{got}', defined in '{want}' ({defined[name][1]})")
check("B1 no registrar calls its RPC in the wrong schema "
      "(omitting .schema() means `public`, where none of these functions exist)",
      not wrong, "\n        " + "\n        ".join(wrong))

for name in sorted(defined):
    hits = [c for c in calls if c[0] == name]
    if not hits:
        continue
    want = defined[name][0]
    got = {(c[1] or DEFAULT) for c in hits}
    check(f"B2 {name} -> {want} (defined in {defined[name][1]})",
          got == {want}, f"called in {sorted(got)}")

print()
print("=" * 78)
print("C. The three the owner's redeploy would otherwise have skipped in silence")
print("=" * 78)
for name, want, what in (("ensure_data_sources_cron", "commcalc", "portal pulls / merchant scrapes"),
                         ("ensure_doc_expiry_alert_cron", "storeops", "lease + COI expiry alerts"),
                         ("ensure_google_reviews_sweep_cron", "storeops", "google reviews sweep")):
    hits = [c for c in calls if c[0] == name]
    check(f"C: {what} — {name} is called in '{want}'",
          bool(hits) and all((c[1] or DEFAULT) == want for c in hits),
          f"{'no call site found' if not hits else 'called in ' + str(sorted({c[1] or DEFAULT for c in hits}))}")

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
