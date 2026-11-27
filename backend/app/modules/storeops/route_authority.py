"""ONE HOME — does a storeops route ASK WHO IS CALLING?

Index §STOREOPS-07.8. Built 2026-11-27 by the daily security pass (§CORE-42) after the storeops
rotation found **20 write routes that took no `authorization` parameter at all**. A route that does
not take it cannot ask: its only boundary is the tenant, so every signed-in person in the tenant is
equal on it, whatever the UI shows them.

THE CLASS, and why it is not new. The owner ruled on exactly this shape on 2026-08-05 for core's
RBAC endpoints (`harness_privesc_rbac_gates`): *"a logged-in NON-ADMIN with devtools crafts an HTTP
request carrying their own valid JWT ... these endpoints self-gated on nothing but the UI hiding the
control."* §STOREOPS-07.65 is the same class one surface over — a person-keyed read that applied the
pay gate and never asked whose employee id was passed. Storeops' own writes were never swept for it.
Hiding the page is not gating the route; `rep_surface` confines a scope-`self` employee and says
nothing about a store manager reaching an admin's write.

ONE FACT, ONE HOME, DEREFERENCED. The vocabulary below is the only list of what counts as asking,
and `harness_storeops_route_authority.py` READS it rather than keeping a copy. A new write route
that asks nothing fails the build; it cannot be added back quietly.
"""
from __future__ import annotations

import ast
import os

# ── the vocabulary of ASKING ──────────────────────────────────────────────────────────────────────
# A helper that resolves the CALLER and refuses somebody. Each one already exists and is already the
# house answer for its question — this module introduces no new rule and copies none.
AUTHORITY_GATES = (
    "require_person_write",      # storeops/router — THE gate for a write ABOUT a person (2026-11-04)
    "require_not_individual_contributor",   # storeops/router — THE gate for a manager act (2026-11-20)
    "person_write_allowed",
    "_require_manager",          # storeops/router — "a manager, not a plain rep" (PUT /merchant-ids)
    "gate_person_rows",          # storeops/router §14w — "whose people may this caller read/act on"
    "_require_lease_access",     # storeops/router — the lease/doc surface's own gate
    "scope_keyset",              # core/scope — the caller's store span, applied to rows
    "in_keyset",
    "scope_emp_ids",
    "visible_people_keyset",
    "can_see_pay",               # storeops/pay_visibility — the mig-434 pay gate
    "strip_pay",
    "_caller_identity",
    "_caller_app_user",
    "_caller_span_codes",
    "_gr_manager_span",
    "_gate",                     # schedule_week_api's own store-span gate
    "_may_decide",
    "_require_google_reviews_admin",   # storeops/router — that feature's own admin gate
    "gate_pay_write",            # storeops — may this caller write a PAY field
    "_caller_employee_id",       # resolves the caller to a person
    "caller_scope",              # core/scope — the caller's own scope
)

# A scheduler entrypoint carries a SHARED SECRET instead of a user token. That is asking too — of a
# different principal — so it is gated, not ungated.
SECRET_GATES = ("verify_notify_secret", "x_notify_secret", "x_cron_secret")

# ── the reads not yet gated, each excused BY NAME with a reason ───────────────────────────────────
# A ratchet, never a licence: this map may only SHRINK. A read is excused when it returns the
# tenant's own setup vocabulary (which every signed-in person in that tenant may legitimately see)
# rather than a named person's or another store's facts. Anything per-person or per-store that is
# NOT listed here is a defect, and `payroll-settings` is why the list is explicit.
EXCUSED_READS = {
    "GET /dropoff-label/profiles": "the tenant's own label profiles; declared rep-readable surface",
    "GET /dropoff-label/barcode.svg": "renders the barcode the caller already holds; no lookup",
    "GET /timeclock/stores": "the tenant's store list, same facts as the declared /storeops/stores",
    "GET /timeoff-conflict-mode": "one tenant-level policy word",
    "GET /markets": "the tenant's own market vocabulary",
    "GET /org/tree": "the tenant's org chart, shown on every org screen",
    "GET /org/levels": "the tenant's org level vocabulary",
    "GET /org/employees": "the roster the org chart draws; narrowing is §STOREOPS-07.9 work",
    "GET /pto-accrual-config": "one tenant-level policy row",
    "GET /payroll-tax-config": "tenant-level tax setup, no person in it",
    "GET /payroll-expense-items": "the tenant's expense vocabulary",
    "GET /merchant-ids/processors": "a static list of processor names",
    "GET /merchant-ids": "store merchant ids; its own PUT and /coverage sibling both gate — "
                         "excused only to keep this PR's subject the WRITES, §STOREOPS-07.9",
    "GET /dropoff-label/scans": "per-store scan list, org-scoped only; same debt, §STOREOPS-07.9",
}

_WRITE_DECORATORS = (".post(", ".put(", ".patch(", ".delete(")


def _module_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _route_decorators(src: str, node: ast.AST) -> list[str]:
    out = []
    for d in getattr(node, "decorator_list", ()) or ():
        seg = ast.get_source_segment(src, d) or ""
        if "router." in seg:
            out.append(seg.strip())
    return out


def _path_and_methods(dec: str) -> tuple[str, tuple[str, ...]]:
    """("/org/units", ("POST",)) from '@router.post("/org/units")'."""
    verb = dec.split("router.", 1)[-1].split("(", 1)[0].strip().upper()
    path = ""
    for q in ('"', "'"):
        if q in dec:
            parts = dec.split(q)
            if len(parts) > 1:
                path = parts[1]
                break
    return path, (verb,)


def classify_routes() -> list[dict]:
    """Every storeops route, with how (and whether) it asks who is calling.

    Pure AST over the shipped source — no import, no DB, no network, so the lock that reads this is
    DB-free by construction and cannot be fooled by a monkeypatch.
    """
    rows: list[dict] = []
    d = _module_dir()
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".py") or fn == os.path.basename(__file__):
            continue
        src = open(os.path.join(d, fn), encoding="utf-8").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decs = _route_decorators(src, node)
            if not decs:
                continue
            body = ast.get_source_segment(src, node) or ""
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            takes_auth = "authorization" in args
            gates = tuple(g for g in AUTHORITY_GATES if g in body)
            secret = any(s in body or s in args for s in SECRET_GATES)
            for dec in decs:
                path, methods = _path_and_methods(dec)
                for m in methods:
                    rows.append({
                        "file": fn, "line": node.lineno, "func": node.name,
                        "method": m, "path": path, "key": f"{m} {path}",
                        "takes_authorization": takes_auth,
                        "gates": gates, "secret_gated": secret,
                        "is_write": any(w in dec for w in _WRITE_DECORATORS),
                        "asks": bool(secret or (takes_auth and gates)),
                    })
    return rows


def ungated_writes(rows=None) -> list[dict]:
    """Every WRITE route that asks nobody. This list must stay EMPTY."""
    return [r for r in (rows if rows is not None else classify_routes())
            if r["is_write"] and not r["asks"]]


def ungated_reads(rows=None) -> list[dict]:
    """Every READ route that asks nobody and is NOT excused by name above."""
    return [r for r in (rows if rows is not None else classify_routes())
            if (not r["is_write"]) and (not r["asks"]) and r["key"] not in EXCUSED_READS]
