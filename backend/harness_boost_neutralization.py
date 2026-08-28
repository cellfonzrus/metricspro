"""Proof: Part-1 carrier-neutrality rewrite.

Verifies (a) app.main imports clean, (b) the commission calc-refusal messages that a NON-Boost tenant
can hit no longer contain any 'Boost' wording, and (c) the market-label defaults no longer fall back to
the string 'Boost'. Pure source inspection + one import — no DB. Run: python -m scratchpad.boost_neutralization_proof
"""
import re, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]          # backend/
APP = ROOT / "app" / "modules"
ok = True
def ck(label, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + label)
    ok = ok and cond

# (a) clean import
import app.main  # noqa: F401
ck("app.main imports clean", True)

router_src = (APP / "commcalc" / "router.py").read_text()

# (b) the two REFUSAL messages a non-Boost (plan-mode) tenant raises must be carrier-neutral.
#     Grab a window of source starting at each message's anchor and assert no 'Boost' wording.
def window(anchor, tail, src):
    """Message text from its anchor up to (and including) its known tail phrase — bounds the
    multi-line f-string so the check never bleeds into the adjacent code comments."""
    i = src.find(anchor)
    if i < 0:
        return None
    j = src.find(tail, i)
    return src[i:j + len(tail)] if j >= 0 else None

zero_wipe = window("REFUSED to overwrite", "overwrite deliberately.", router_src)
unconfigured = window("NO commission source configured", "override once.", router_src)
ck("zero-wipe refusal message present", zero_wipe is not None)
ck("unconfigured refusal message present", unconfigured is not None)
for lbl, b in (("zero-wipe", zero_wipe), ("unconfigured", unconfigured)):
    ck(f"{lbl} refusal carrier-neutral (no 'Boost')", b is not None and 'Boost' not in b and 'non-Boost' not in b)
# safety behaviour preserved: still a zero-wipe refusal keyed on the default carrier + force override
ck("zero-wipe still refuses + keeps snapshot + names default carrier",
   zero_wipe is not None and all(k in zero_wipe for k in ("REFUSED to overwrite", "default carrier", "force=true")))

# (c) market-label defaults: no `or 'Boost'` fallbacks remain in the touched files.
for rel in ("commcalc/router.py", "commcalc/gp_report.py", "account/residual_subs.py"):
    src = (APP / rel).read_text()
    hits = re.findall(r"(?:market[^\n]*?or\s*['\"]Boost['\"]|['\"]Boost['\"][^\n]*?market)", src)
    ck(f"{rel}: no market->'Boost' default", not hits)

# migration 920 exists and drops the Boost market default from sync_to_commcalc
mig = (ROOT.parent / "database" / "migrations" / "920_storeops_sync_market_neutral.sql")
ck("migration 920 present", mig.exists())
mtext = mig.read_text() if mig.exists() else ""
mbody = mtext[mtext.find("CREATE OR REPLACE FUNCTION"):] if "CREATE OR REPLACE FUNCTION" in mtext else ""
ck("mig 920 recreates sync_to_commcalc with COALESCE(NEW.market,'') not 'Boost'",
   "sync_to_commcalc" in mbody and "COALESCE(NEW.market,'')" in mbody and "'Boost'" not in mbody)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
