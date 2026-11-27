"""Proof harness: A SCREEN NAMED "MY" SHOWS ONE PERSON, AND THE SERVER DECIDES WHICH (§CORE-33.5).

DB-free. The pure half of `commcalc/payout_audience.py` over real inputs, plus an AST lock over the
two handlers behind `/commcalc/targets/my` and over the page itself.

OWNER, 2026-11-27: *"under my targets it should only shpow the name of the person whoc is logged in
not all reps , that needs to chnage across teh paltform"*.

WHAT HE WAS LOOKING AT. The screen carried its own confession since before sign-in existed:
*"Until logins are enabled, pick your store and name below. This view will be locked to you
automatically once sign-in is live."* Sign-in went live; nobody closed it.

THE TWO DEFECTS UNDER THE COSMETIC ONE, both proved end-to-end in
`tests/integration/test_my_targets_is_the_viewers_own.py`:
  1. `/targets/{period}/calendar` took `rep` as a plain parameter with NO person check, and published
     `reps` — every name that worked or sold at the store. A confined employee is granted read on
     `/commcalc/targets`, so a rep could name a colleague and read their figures.
  2. `/targets/{period}/summary`, the OTHER call on the same page, broke each store down into one row
     per rep carrying their achieved activations, upgrades, accessories and conversion. The store
     list was narrowed to the caller's own store; the rows inside it were not.
  (and 3) the calendar's store guard read `if ks and not in_keyset(...)`, and a self-scoped rep
     resolves to an EMPTY keyset, which is falsy — so the guard never ran for the one viewer it had
     to bound, while the sibling `/summary` substituted the rep's own store. Two paths, one question,
     opposite answers.

THE CLASS: **"may this door open" and "whose rows come out of it" are two questions, and a surface a
rep is SUPPOSED to reach can only be judged by the second.** `rbac.SELF_OWN_STORE_SURFACES` had
already DECLARED the answer for this exact page — *"If it also carries a per-employee pay /
commission / compensation / PII column, that column is filtered to the caller's own employee_id …
Adding a surface to this list without that filter is a payroll leak between colleagues"* — and the
wiring was never done. That is §19.18, the registry written and its callers left unwired.

NO SECOND MECHANISM WAS BUILT. The duplicate check found that `/targets/{period}/action-plan` had
already been fixed with `_caller_rep_keys` + `payout_audience.requested_rep_is_mine`. A first draft
of this change added a parallel name-keyed dimension to `core/scope.py`; it was DELETED and the
existing home extended instead — `payout_audience.name_fold` / `mine_names` — which is also why the
accent fix below reaches the commission surfaces that share it.

WHAT THIS PROVES
  §A  the fold: two spellings of one person match; two people never do.
  §B  it is ADDITIVE — every key the old rule produced is still produced.
  §C  `mine_names`: the picker, the de-duplication, and the manager left untouched.
  §D  ★ NO LOCKOUT ★ — the reported `unmatched_self` state, never an empty list nor a widening.
  §E  `requested_rep_is_mine` refuses a colleague and admits every spelling of oneself.
  §F  the LOCK: both handlers dereference the one home, and neither spells a comparison itself.
  §G  the LOCK: the page reads the server's `locked_rep` and keeps no phase-word of its own.
  §H  ARMED: the pre-change rules, reproduced, get the owner's case wrong.
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import payout_audience as pa        # noqa: E402
from app.modules.commcalc import targets_engine as te         # noqa: E402
from app.modules.core import person_identity as _pi           # noqa: E402
from harnesslib import py_code_only                           # noqa: E402

PASS = FAIL = 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s" % label)


# The three live spellings of ONE person, and a colleague.
ROSTER = "Diana Antunez"
POS = "Antunez, Diana"
ACCENT = "Díana Antúnez"
SHOUT = "DIANA  ANTUNEZ"
OTHER = "Marco Reyes"
MIDDLE = "Antunez, Diana, R"

print("\n§A  the fold — one person's spellings agree, two people never do")
ok(pa.name_fold(ROSTER) == pa.name_fold(POS),
   "the POS comma form folds to the roster name (the 2026-09-02 finding)")
ok(pa.name_fold(ROSTER) == pa.name_fold(ACCENT),
   "accents fold away (the Martínez finding — str.isalnum() is True for 'í')")
ok(pa.name_fold(ROSTER) == pa.name_fold(SHOUT), "case and double spaces fold away")
ok(pa.name_fold(ROSTER) != pa.name_fold(OTHER), "a colleague does NOT fold to her")
ok(pa.name_fold(ROSTER) != pa.name_fold(MIDDLE),
   "a dropped middle initial is NOT forgiven — that is a judgement a human makes, and a key that "
   "merged them could admit a different person")
ok(pa.name_fold("") == "" and pa.name_fold(None) == "" and pa.name_fold("   ") == "",
   "a blank name yields no key at all, so it can never be a wildcard")
ok(pa.name_fold(ROSTER).startswith("fold:"),
   "the key is prefixed, so it can never collide with an UPPER raw name in the same set")

print("\n§B  it is ADDITIVE — nothing that matched before stops matching")


def _old_rep_row_keys(row, canon=None, fields=pa.REP_NAME_FIELDS):
    """`rep_row_keys` exactly as it was before this change: UPPER raw name + the alias canon."""
    out = set()
    for f in fields or ():
        v = str((row or {}).get(f) or "").strip()
        if not v:
            continue
        out.add(v.upper())
        if canon is not None:
            c = str(canon(v) or "").strip()
            if c:
                out.add(c.upper())
    return out


_canon = {"D ANTUNEZ": ROSTER}.get
for row in ({"rep": ROSTER}, {"rep": POS}, {"salesperson": OTHER},
            {"rep": "D Antunez"}, {"rep": ""}, {"storeops_name": ACCENT, "rep": OTHER}):
    old = _old_rep_row_keys(row, canon=lambda v: _canon(v.upper()) or "")
    new = pa.rep_row_keys(row, canon=lambda v: _canon(v.upper()) or "")
    ok(old - {""} <= new, "every old key survives for %r" % (row,))
ok(pa.rep_row_keys({"rep": ""}) == set(), "a blank field still yields nothing")

print("\n§C  mine_names — the picker")
STORE = [ROSTER, POS, ACCENT, OTHER]
mine, unmatched = pa.mine_names(STORE, {ROSTER.upper(), pa.name_fold(ROSTER)})
ok(mine == [ROSTER], "the three spellings of her collapse to ONE entry, first spelling winning")
ok(OTHER not in mine, "the colleague is not offered")
ok(unmatched is False, "…and that is not the lockout state")
same, un2 = pa.mine_names(STORE, None)
ok(same == STORE and un2 is False,
   "a manager (rep_keys None) gets the list unchanged — no existing response shifts")
ok(pa.mine_names([], None) == ([], False), "an empty store is not a lockout for a manager")
only_pos, _ = pa.mine_names([POS, OTHER], {pa.name_fold(ROSTER)})
ok(only_pos == [POS],
   "she is found when the feed spells her ONLY the other way round — the lockout this prevents")

print("\n§D  ★ NO LOCKOUT ★ — the reported state, never a widening")
none_mine, unmatched = pa.mine_names([OTHER], {pa.name_fold(ROSTER)})
ok(none_mine == [], "a viewer whose name is at no store is offered nobody…")
ok(unmatched is True, "…and that is REPORTED, so the screen can say why instead of showing zeros")
ok(pa.mine_names([], {pa.name_fold(ROSTER)}) == ([], False),
   "nothing on offer is NOT the lockout state — there is nothing to be unmatched against")
ok(pa.mine_names([OTHER], set()) == ([], False),
   "an unplaceable viewer (empty keys) is a provisioning problem with its own message, not this one")
ok(pa.mine_names([ROSTER, OTHER], set()) == ([], False),
   "…and an empty key set is deny-all, never allow-all")

print("\n§E  requested_rep_is_mine — the parameter, not the dropdown")
MINE = {ROSTER.upper(), pa.name_fold(ROSTER)}
ok(pa.requested_rep_is_mine(OTHER, MINE) is False, "a colleague named directly is refused")
for spelling in (ROSTER, POS, ACCENT, SHOUT):
    ok(pa.requested_rep_is_mine(spelling, MINE) is True, "…and %r is her own" % spelling)
ok(pa.requested_rep_is_mine(OTHER, None) is True, "a manager is unrestricted")
ok(pa.requested_rep_is_mine("", MINE) is True, "a blank request asks for no narrowing")
ok(pa.requested_rep_is_mine(OTHER, set()) is False,
   "an unplaceable self caller gets nobody, not everybody")

print("\n§A2  the fold is NOT an identity on its own — the security pass on this change")
# Found by the security review of this very PR, both confirmed by running them before being closed.
# `folded_token_key` throws WORD ORDER away, which is an accepted cost for a JOIN (a wrong figure)
# and a cross-employee breach as an AUTHORIZATION equality.
TWIN_A, TWIN_B = "Mark Kellie", "Kellie Mark"
ok(pa.name_fold(TWIN_A) == pa.name_fold(TWIN_B),
   "two DIFFERENT people whose names are the same words in a different order DO share the fold — "
   "the pair `person_identity.token_key` cites as live data")
ok(pa.name_fold("Maria Diaz Diaz") == pa.name_fold("Diaz Diaz Maria"),
   "…and a duplicated surname folds the same way, because the tokens are a SET")
ok(pa.requested_rep_is_mine(TWIN_B, {TWIN_A.upper(), pa.name_fold(TWIN_A)}) is True,
   "ARMED: so the fold ALONE admits the other person — which is why a caller may only add it to "
   "its own key set after proving the roster holds one such person (`_fold_names_one_person`), "
   "and why that guard is pinned on the real handler in the integration gate")

# THE OTHER HALF: the guard's key and the DATA LAYER's key are not the same function.
_ACCENT = "\u00e1\u00f1\u00e1 Lopez"
ok(_pi.folded_token_key(_ACCENT) == _pi.folded_token_key("Ana Lopez"),
   "a crafted spelling folds to the caller, so it PASSES the guard…")
ok(te.name_key(_ACCENT) == te.name_key("Lopez") != te.name_key("Ana Lopez"),
   "…while `targets_engine.name_key` DELETES the non-ASCII letters and selects a DIFFERENT rep. "
   "Two keys that are not comparable — which is why a restricted caller's rows are selected under "
   "the roster's own spelling and never under the text of the request")
for _bad in (None, "", "   ", "— · —"):
    ok(pa.name_fold(_bad) == "",
       "name_fold(%r) yields NO key rather than raising or matching everything — its caller's "
       "enclosing except treats failure as UNRESTRICTED, so this must fail closed" % (_bad,))

print("\n§F  the LOCK — both handlers dereference the one home")
_ROUTER = os.path.join(os.path.dirname(os.path.abspath(pa.__file__)), "router.py")
_src = open(_ROUTER, encoding="utf-8").read()
_code = py_code_only(_src)
_tree = ast.parse(_src)
_fns = {n.name: n for n in ast.walk(_tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

# The two handlers behind /commcalc/targets/my, plus the sibling that was already correct — all
# three must keep dereferencing, because the defect was one of them drifting from the others.
#
# EVERY SHAPE CHECK BELOW RUNS ON COMMENT-STRIPPED SOURCE. The first draft of this harness read the
# raw segment and failed on its OWN fix: the comment in `get_target_calendar` QUOTES the old
# `if ks and not in_keyset(...)` line so the next reader knows what was wrong, and a prose match
# cannot tell a citation from the code. Comments SHOULD name the defect; what may not come back is
# the line the server runs. (Same lesson as §COMMCALC-50.8's close-handler lock.)
_NARROWERS = ("mine_only(", "mine_names(", "requested_rep_is_mine(")
for fn in ("get_target_calendar", "get_targets_summary", "get_action_plan"):
    node = _fns.get(fn)
    ok(node is not None, "%s found" % fn)
    if not node:
        continue
    seg = py_code_only(ast.get_source_segment(_src, node) or "")
    # Either name of the SAME home: `_caller_rep_keys` is the thin wrapper over
    # `_caller_rep_identity`, which a handler that must also SELECT under the caller's own name asks
    # for directly. What is forbidden is answering the question without asking either.
    ok("_caller_rep_keys" in seg or "_caller_rep_identity" in seg,
       "%s asks WHOSE rows through the one home" % fn)
    ok("_caller_self_keyset" in seg, "%s asks WHICH stores through the one home" % fn)
    ok(any(n in seg for n in _NARROWERS),
       "%s narrows through payout_audience rather than by hand" % fn)
    # A handler that re-derives WHO IS CALLING is the copy that drifts — that is what
    # `_caller_rep_keys` is for, and reading `auth_id` here would be a second identity path.
    ok("auth_id" not in seg, "%s does not re-derive the caller's identity" % fn)
    # THE FALSY-KEYSET BUG, pinned as a shape: `if ks and ...` lets an EMPTY keyset — exactly what a
    # self-scoped rep resolves to — skip the store guard entirely.
    ok(not re.search(r"\bif ks and not in_keyset", seg),
       "%s does not let an empty keyset skip the store guard" % fn)

# THE TWO SHAPES THE SECURITY PASS ADDED.
_cal_seg = py_code_only(ast.get_source_segment(_src, _fns["get_target_calendar"]) or "")
ok("_caller_rep_identity" in _cal_seg and "rep = _own_name" in _cal_seg,
   "the calendar selects a restricted caller's rows under the ROSTER's spelling, never under the "
   "text of the request — so nothing a caller can type changes whose rows come back")
_ident = py_code_only(ast.get_source_segment(_src, _fns["_caller_rep_identity"]) or "")
ok("_admissible_fold_keys" in _ident and "_fold_is_one_identity" in _src,
   "the caller's own key set admits the order-free fold only when exactly one declared identity "
   "carries it — otherwise \"Mark Kellie\" and \"Kellie Mark\" are one identity")
_amb = py_code_only(ast.get_source_segment(_src, _fns["_fold_is_one_identity"]) or "")
ok("return False" in _amb and "read_all" in _amb,
   "…and that gate fails CLOSED on an unreadable roster, reading through the shared paged home "
   "(which RAISES) so a truncation can never turn two colliding people into one")
ok("cmap" in _amb and "who.add" in _amb,
   "…and its universe is the roster PLUS the declared alias map, reduced to canonical identities")

# ★ THE LOCK THAT HOLE 3 NEEDED ★ — found by the SECOND security pass on this change. The first
# gate asked its question of the roster NAME and added the ALIAS folds un-gated beside it, excused
# in a comment saying a declaration carries its own authority. It does — for itself. It carries
# none over a second employee who folds the same way, and because the fold of "KELLIE, MARK" IS the
# fold of "Mark Kellie", the ordinary name_map row every epay-fed rep has handed back the exact key
# the gate had withheld. One un-gated add beside a gated one is not a guard.
#
# The shape that makes a repeat impossible: ONE producer of a fold key for a caller's key set, and
# the gate lives inside it. So the lock is an absence — the identity builder must not compute a
# fold itself — which no dataflow analysis is needed to check and no future edit can satisfy by
# accident.
ok("_admissible_fold_keys" in _ident,
   "the caller's key set gets its folds from the ONE producer that gates them")

# THE LOCK'S OWN HOLE, found by the THIRD security pass. The check here used to be the single
# absence `"name_fold" not in _ident` — and the same commit shipped a SECOND SPELLING of that call,
# `_pa_fold_key`, a helper of mine two functions below. `keys.add(_pa_fold_key(alias))` is hole 3
# restored verbatim and contains no "name_fold", so it passed. `_pi.folded_token_key(...)` and
# `keys |= {something}` passed too. An absence that names one identifier is not an absence; the
# evasion was a function I had just written and documented as "reached by name".
#
# So the lock is now a WHITELIST of what may join the key set, read off the AST: every `keys.add(X)`
# and `keys |= X` in the identity builder must have an X from this list, verbatim. A new
# contributor to a caller's authorization keys — by any spelling, through any helper, computed any
# way — fails the build and has to be added here deliberately. There is nothing left to dodge,
# because the check no longer asks what the code must NOT say.
_ALLOWED_KEY_SOURCES = {
    "name.upper()",                                     # the caller's own roster spelling
    "str(alias).strip().upper()",                       # a spelling a human DECLARED to be them
    "_admissible_fold_keys(org_id, mine, cmap=cmap)",   # folds, each one gated
}
_contributors = []
for _n in ast.walk(_fns["_caller_rep_identity"]):
    # `keys.add(X)`
    if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
            and _n.func.attr in ("add", "update")
            and isinstance(_n.func.value, ast.Name) and _n.func.value.id == "keys"):
        _contributors += [ast.get_source_segment(_src, a) or "?" for a in _n.args]
    # `keys |= X`, `keys = keys | X`
    if (isinstance(_n, ast.AugAssign) and isinstance(_n.target, ast.Name)
            and _n.target.id == "keys"):
        _contributors.append(ast.get_source_segment(_src, _n.value) or "?")
    if (isinstance(_n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "keys" for t in _n.targets)
            and not isinstance(_n.value, (ast.Call, ast.Set, ast.Dict))
            and (ast.get_source_segment(_src, _n.value) or "") not in ("set()",)):
        _contributors.append(ast.get_source_segment(_src, _n.value) or "?")
_unexpected = [c for c in _contributors if c.strip() not in _ALLOWED_KEY_SOURCES]
ok(_contributors, "the identity builder does assemble a key set that can be audited")
ok(not _unexpected,
   "EVERY contributor to the caller's authorization key set is on the whitelist — unexpected: %r. "
   "A fold may only arrive through `_admissible_fold_keys`, which gates it; that was hole 3, and a "
   "second SPELLING of the same call was the hole in this lock." % (_unexpected,))
ok(len(re.findall(r"name_fold", _ident)) == 0,
   "…and the identity builder still names no fold producer directly")
# The indirection that defeated the old absence check is GONE, not merely unused: a named wrapper
# round `name_fold` is a second spelling waiting to be called.
ok("_pa_fold_key" not in _code,
   "no named indirection to `name_fold` survives in the router — the thing that made the previous "
   "spelling of this lock passable")
_prod = py_code_only(ast.get_source_segment(_src, _fns["_admissible_fold_keys"]) or "")
ok("_fold_is_one_identity" in _prod and "name_fold" in _prod,
   "every fold the producer returns passed the gate — and the producer is the one place in the "
   "router where a caller's fold is computed at all")
ok(len(re.findall(r"\badd\(", _prod)) == 1 and "out.discard" in _prod,
   "…and it has exactly one add site, inside the gate's own branch")
# ARMED: the producer really does refuse the ambiguous pair and keep the unambiguous one. Pure, so
# the universe is injected rather than read.
_seen = {}
ok(pa.name_fold("KELLIE, MARK") == pa.name_fold(TWIN_A) == pa.name_fold(TWIN_B),
   "ARMED: the POS alias, the roster name and the COLLEAGUE all share one fold — which is why "
   "gating the roster name alone proved nothing")
ok(not re.search(r"needs no ambiguity check", _src),
   "…and the reasoning that excused the un-gated alias fold is gone from the source, so it cannot "
   "be copied forward")

ok("Those are not your figures to read." in _code,
   "the calendar REFUSES a foreign rep rather than silently serving the caller's own figures under "
   "somebody else's name")

print("\n§G  the LOCK — the page reads the server's answer")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # …/metricspro
_PAGE = os.path.join(_REPO, "frontend", "src", "app", "(platform)", "commcalc", "targets", "my",
                     "page.tsx")
ok(os.path.exists(_PAGE), "My Targets page found at its expected path")
_page = open(_PAGE, encoding="utf-8").read()


def _tsx_code_only(src: str) -> str:
    """The page with its prose removed — `{/* … */}` and `/* … */` blocks, and whole-line `//`.

    SAME REASON AS §F, and the reason this function exists at all: the comment on this page QUOTES
    the notice it replaced ("Until logins are enabled…") so the next reader knows what was wrong
    there. A lock that searched the raw file would fail on its own citation and the only way to make
    it pass would be to delete the explanation. Comments SHOULD name the defect; what may not come
    back is a line the browser runs. An inline trailing `//` is left alone on purpose — stripping it
    would need to know about string literals and `https://`."""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in out.splitlines() if not l.strip().startswith("//"))


_pcode = _tsx_code_only(_page)
ok("locked_rep" in _pcode, "the page reads the server's `locked_rep`")
ok("rep_unmatched" in _pcode, "…and the server's lockout state")
ok("Until logins are enabled" not in _pcode,
   "the placeholder notice that outlived sign-in is GONE from the rendered page")
ok("Until logins are enabled" in _page,
   "…and is still QUOTED in a comment, so the next reader knows what this screen used to do")
ok(not re.search(r"reps\.length\s*===?\s*1", _pcode),
   "the page does NOT decide the lock from the length of the list — a second spelling of one person "
   "would silently turn the lock back into a picker")
ok("Select your name" not in _pcode,
   "nobody is asked to pick their own name out of a list any more")
ok("Merge duplicate reps" in _pcode and re.search(r"!lockedRep[^\n]*&&", _pcode),
   "the admin-only rep-merge link is offered only to a viewer who still has a picker — for a "
   "locked employee it is a link straight into `not_your_access` (the §CORE-33.4 dead end)")

print("\n§H  ARMED: the pre-change rules, reproduced")
ok(not pa.requested_rep_is_mine(OTHER, MINE),
   "ARMED: the fixed rule refuses the colleague the old one served")
_old_mine = _old_rep_row_keys({"rep": ACCENT})
ok(not (_old_mine & MINE),
   "ARMED: the OLD fold could not match her own accented spelling to her roster name — so the "
   "narrowing this change adds would have locked her out of her own page. That is why the fold "
   "shipped in the same commit as the filter, and why §A pins it.")
_old_pos = _old_rep_row_keys({"rep": POS})
ok(not (_old_pos & {ROSTER.upper()}),
   "ARMED: nor her POS comma spelling — the identical mismatch that once zeroed a whole tenant's "
   "per-employee daily targets")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — a screen named 'My' could show somebody else, or a caller started deciding for "
          "itself whose rows are whose. The one home is commcalc/payout_audience.py "
          "(name_fold / rep_row_keys / mine_names / mine_only / requested_rep_is_mine) over the "
          "caller keys from commcalc/router._caller_rep_keys; the store dimension is "
          "_caller_self_keyset. No handler compares a rep name itself, and the page renders the "
          "server's `locked_rep` rather than counting the list.")
    sys.exit(1)
print("OK — one person per 'My' screen, one home for whose rows are whose, every spelling of oneself "
      "admitted and no colleague served.")
sys.exit(0)
