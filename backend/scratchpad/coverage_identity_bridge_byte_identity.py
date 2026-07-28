"""BYTE-IDENTITY differential for agent/commission/coverage-identity-bridge.

Loads the PRISTINE origin/main commission_engine.py side by side with the working one and asserts that
`preview()` output is byte-identical across every shape the money path uses (default / detail /
plan_id-forced / only_rep, both a plan tenant and the house tenant, with and without a
commission_org_config row). This is the guarantee the package rests on: coverage/diagnostics may add
keys to the coverage block, but a payout number may not move until the owner flips store_resolution.

Run:  cd backend && \
      git -C .. show origin/main:backend/app/modules/commcalc/commission_engine.py > /tmp/engine_base.py && \
      python3 scratchpad/coverage_identity_bridge_byte_identity.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

BASE_PATH = os.environ.get("ENGINE_BASE", "/tmp/engine_base.py")
if not os.path.exists(BASE_PATH):
    print(f"SKIP — pristine engine not found at {BASE_PATH}. See the docstring for how to produce it.")
    sys.exit(0)

_spec = importlib.util.spec_from_file_location("engine_base", BASE_PATH)
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)

_real_exit = sys.exit
sys.exit = lambda *a, **k: None
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    import coverage_identity_bridge_proof as F      # noqa: E402  (fixture + FakeClient)
sys.exit = _real_exit
print("fixture proof self-check:", _buf.getvalue().strip().splitlines()[-2])

import app.modules.commcalc.commission_engine as NEW   # noqa: E402


def j(x):
    return json.dumps(x, sort_keys=True, default=str)


diffs = 0
for org in (F.ORG_A, F.ORG_B):
    for name, cfg_rows in (("no-config", []), ("exact", [F.cfg(org=org)]),
                           ("mapped", [F.cfg(org=org, plan_ct_resolution="mapped")])):
        for detail in (False, True):
            a = BASE.preview(F.base_store(cfg_rows), org, "July 2026", detail=detail)
            b = NEW.preview(F.base_store(cfg_rows), org, "July 2026", detail=detail)
            if j(a) != j(b):
                diffs += 1
                print(f"DIFF org={org[:8]} cfg={name} detail={detail}")
                print("  base:", j(a)[:600])
                print("  new :", j(b)[:600])
            else:
                print(f"  identical  org={org[:8]} cfg={name:9s} detail={str(detail):5s} "
                      f"totals={a['totals']}")

EXTRA = {"id": "aX", "org_id": F.ORG_A, "plan_id": "p1", "scope": "employee",
         "scope_value": "Sri ram, Nivas", "priority": 0}


def paying():
    c = F.base_store()
    c.store["commission_plan_assignment"] = F.ASSIGNS + [EXTRA]
    return c


for label, kw in (("paying fixture", {}), ("forced plan_id", {"plan_id": "p2"}),
                  ("only_rep + detail", {"only_rep": "Sri ram, Nivas", "detail": True})):
    a = BASE.preview(paying(), F.ORG_A, "July 2026", **kw)
    b = NEW.preview(paying(), F.ORG_A, "July 2026", **kw)
    same = j(a) == j(b)
    print(f"  {label:18s} base={a['totals']} new={b['totals']} identical={same}")
    if not same:
        diffs += 1

print(f"\nBYTE-IDENTITY DIFFS: {diffs}")
sys.exit(1 if diffs else 0)
