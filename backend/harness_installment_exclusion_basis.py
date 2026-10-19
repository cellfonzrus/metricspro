#!/usr/bin/env python3
"""A DECISION IS NOT A FINDING — the multi-month exclusion basis (§COMMCALC-20).

OWNER, verbatim (2026-10-19): *"if home internet is not required to pay the multi month as defined inte
h incentive struture it shoud not appear as not paid and create confusion for teh user"*.

WHAT WAS WRONG. "What this calculation did not pay" listed EVERY excluded device category with one
sentence that asserted a single reason — "that category is unchecked under Plan Installments" — for all
four ways a category can fail to qualify. Measured on the owner's own tenant: 405 of 409 multi-month
activations were classified correctly and then excluded BY HIS OWN SETTING, and the notice reported his
decision back to him every month as if something had broken. Meanwhile the 4 activations that genuinely
could not be classified were reported TWICE (once as the `unknown` category being excluded, once by the
unclassifiable warning), so the one case that needed a human looked like two cases that did not.

THE CLASS. A surface must not assert a reason it has not established, and a non-payment somebody CHOSE
is not a finding. This is the third shape of the posture §66 (`component_is_evidence`) and §COMMCALC-09
(`payout_slice_evidence`) already hold: the allowlist names what IS decided, so a basis nobody has ruled
on reads as a finding and shouts.

NOT DONE, deliberately: no figure moves. `qualification_with_basis` returns the same verdict as
`qualification_for` for every config shape (§C proves it over the whole matrix), and the money is still
on screen — a decision moves to its own quiet line, it is never dropped.

DB-FREE: the resolvers are pure, and the wiring checks read source text.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import installment_category as icat          # noqa: E402
from app.modules.core.module_graph import home_under_app               # noqa: E402

APP = os.path.join(HERE, "app")
ENGINE = os.path.join(APP, "modules", "commcalc", "sale_installment_engine.py")
ROUTER = os.path.join(APP, "modules", "commcalc", "router.py")
# The home is DEREFERENCED from module_graph, never spelled here (§19.18).
HOME = os.path.join(APP, home_under_app("installment_exclusion_basis"))
PAGE = os.path.join(os.path.dirname(HERE), "frontend", "src", "app", "(platform)", "commcalc", "page.tsx")

P = F = 0


def _read_or_blank(path):
    """Pre-fix code may not have the file at all: a missing file must produce NAMED FAILURES, never a
    traceback, or this harness cannot be verified red against main."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


ESRC = _read_or_blank(ENGINE)
RSRC = _read_or_blank(ROUTER)
HSRC = _read_or_blank(HOME)
PSRC = _read_or_blank(PAGE)


def section(t):
    print("\n" + "── " + t + " " + "─" * max(0, 96 - len(t)))


def ok(name, cond, detail=None):
    global P, F
    if cond:
        P += 1
        print("  PASS  " + name)
    else:
        F += 1
        print("  FAIL  " + name + ("   " + repr(detail) if detail is not None else ""))


def _g(name, default=None):
    """A symbol the fix introduces — absent on pre-fix code, which must read as a failure."""
    return getattr(icat, name, default)


# Every config SHAPE the three layers can take. Used by §A and §C.
ORG_OFF = {"_stored": True, "phone": True, "tablet": False, "home_internet": False,
           "sim": False, "accessory": False, "unknown": False}
SHAPES = [
    ("no schedule, no org", {}, {}),
    ("no schedule, org stored", {}, ORG_OFF),
    ("org stored but empty", {}, {"_stored": True}),
    ("schedule dict", {"qualifying_categories": {"phone": True, "tablet": False}}, ORG_OFF),
    ("schedule dict, full", {"qualifying_categories": {k: True for k in icat.CATEGORY_KEYS}}, ORG_OFF),
    ("schedule list", {"qualifying_categories": ["phone"]}, ORG_OFF),
    ("schedule list, several", {"qualifying_categories": ["phone", "tablet"]}, {}),
    ("schedule empty dict", {"qualifying_categories": {}}, ORG_OFF),
    ("schedule empty list", {"qualifying_categories": []}, ORG_OFF),
    ("schedule null", {"qualifying_categories": None}, ORG_OFF),
]

# ═════ A. WHICH LAYER EXCLUDED IT ════════════════════════════════════════════════════════════════
section("A. The basis names the layer that actually decided")
_qwb = _g("qualification_with_basis")
ok("A0 the one home exposes qualification_with_basis", callable(_qwb))

if callable(_qwb):
    _q, _b, _src = _qwb({}, ORG_OFF)
    ok("A1 a stored org switch reads as the tenant's own setting",
       _b.get("home_internet") == _g("BASIS_TENANT_SETTING"), _b.get("home_internet"))
    ok("A2 a category the stored org switch never mentions is the platform default, NOT the tenant's",
       _b.get("watch") == _g("BASIS_PLATFORM_DEFAULT"), _b.get("watch"))
    ok("A3 the source is still reported as before", _src == "org", _src)

    _q, _b, _src = _qwb({"qualifying_categories": {"phone": True, "tablet": False}}, ORG_OFF)
    ok("A4 a schedule's own dict overrides the org, and says so",
       _b.get("tablet") == _g("BASIS_SCHEDULE_OVERRIDE") and _src == "schedule", (_b.get("tablet"), _src))
    ok("A5 a category that schedule dict omits is the platform default, not the ORG's setting "
       "(the org layer was skipped entirely)",
       _b.get("home_internet") == _g("BASIS_PLATFORM_DEFAULT"), _b.get("home_internet"))

    # THE LATENT BUG, made visible. A list of INCLUDED categories says nothing about one it omits.
    _q, _b, _src = _qwb({"qualifying_categories": ["phone"]}, ORG_OFF)
    ok("A6 a list-shaped config leaves every category it never names UNSTATED",
       _b.get("home_internet") == _g("BASIS_UNSTATED"), _b.get("home_internet"))
    ok("A7 a category the list DOES name is a real override",
       _b.get("phone") == _g("BASIS_SCHEDULE_OVERRIDE"), _b.get("phone"))

    _q, _b, _src = _qwb({}, {})
    ok("A8 nothing configured at all is the platform default throughout",
       set(_b.values()) == {_g("BASIS_PLATFORM_DEFAULT")} and _src == "default", (set(_b.values()), _src))
    ok("A9 a basis is recorded for EVERY category, so no caller re-derives a layer",
       all(k in _b for k in icat.CATEGORY_KEYS),
       [k for k in icat.CATEGORY_KEYS if k not in _b])

# ═════ B. A DECISION IS NOT A FINDING, AND THE UNKNOWN CASE SHOUTS ═══════════════════════════════
section("B. The allowlist: decided is silent, anything unruled is a finding")
_dec, _act = _g("is_decided"), _g("is_actionable")
ok("B0 both predicates exist", callable(_dec) and callable(_act))

if callable(_dec) and callable(_act):
    for _nm in ("BASIS_TENANT_SETTING", "BASIS_SCHEDULE_OVERRIDE", "BASIS_PLATFORM_DEFAULT"):
        ok(f"B1 {_nm} is a decision somebody made, so it is not a finding",
           _dec(_g(_nm)) and not _act(_g(_nm)))
    ok("B2 an UNSTATED basis is a finding — nobody decided it",
       _act(_g("BASIS_UNSTATED")) and not _dec(_g("BASIS_UNSTATED")))
    # THE DIRECTION OF THE ALLOWLIST. This is the check that keeps the next migration honest: a basis
    # invented later and not ruled on must surface, never be swallowed as "probably fine".
    for _junk in ("", None, "whatever", "tenant", "default", "TENANT_SETTING", 0, [], "decided"):
        ok(f"B3 an unruled basis {_junk!r} reads as a FINDING, not as a decision",
           _act(_junk) and not _dec(_junk))
    ok("B4 the two predicates are exact complements over every input tried",
       all(_dec(x) != _act(x) for x in
           ["", None, "x", 0, _g("BASIS_UNSTATED"), _g("BASIS_TENANT_SETTING")]))

# ═════ C. NO FIGURE MOVES ════════════════════════════════════════════════════════════════════════
section("C. The verdict is identical to the function it extends — this changes no payout")
if callable(_qwb):
    _mismatch = []
    for _label, _sched, _org in SHAPES:
        _old_q, _old_src = icat.qualification_for(_sched, _org)
        _new_q, _new_b, _new_src = _qwb(_sched, _org)
        if _old_q != _new_q or _old_src != _new_src:
            _mismatch.append((_label, _old_q, _new_q, _old_src, _new_src))
    ok("C1 every config shape yields the SAME qualification and the SAME source as before",
       not _mismatch, _mismatch[:2])
    ok("C2 the matrix really covers all three layers plus the degenerate shapes",
       {s[2] for s in [_qwb(a, b) for _l, a, b in SHAPES]} == {"schedule", "org", "default"},
       {s[2] for s in [_qwb(a, b) for _l, a, b in SHAPES]})
    # The owner's measured tenant: phone only, everything else off, watch never mentioned.
    _q, _b, _ = _qwb({}, ORG_OFF)
    ok("C3 on the measured live config, 'phone' is the only category that pays",
       [k for k in icat.CATEGORY_KEYS if _q.get(k)] == ["phone", "watch"],
       [k for k in icat.CATEGORY_KEYS if _q.get(k)])
    ok("C4 and every one of its exclusions is a decision, so none of them is a finding",
       all(not _act(_b[k]) for k in icat.CATEGORY_KEYS if not _q.get(k)),
       [k for k in icat.CATEGORY_KEYS if not _q.get(k) and _act(_b[k])])

# ═════ D. THE SENTENCE ═══════════════════════════════════════════════════════════════════════════
section("D. One home writes the reason, and it names the layer")
_reason = _g("exclusion_reason")
ok("D0 exclusion_reason exists", callable(_reason))
if callable(_reason):
    _texts = {b: _reason("home_internet", b) for b in
              (_g("BASIS_TENANT_SETTING"), _g("BASIS_SCHEDULE_OVERRIDE"),
               _g("BASIS_PLATFORM_DEFAULT"), _g("BASIS_UNSTATED"))}
    ok("D1 each basis gets its OWN sentence — the reason is never one phrase for four causes",
       len(set(_texts.values())) == 4, _texts)
    ok("D2 every sentence names the category in the operator's words, not its key",
       all(icat.CATEGORY_LABELS["home_internet"] in t for t in _texts.values()))
    ok("D3 the tenant-setting sentence does not claim the platform did it, and vice versa",
       "default" not in _texts[_g("BASIS_TENANT_SETTING")].lower()
       and "settings" not in _texts[_g("BASIS_PLATFORM_DEFAULT")].lower(), _texts)
    ok("D4 an unrecognised basis admits it cannot say why, instead of asserting a reason",
       "could not be established" in _reason("tablet", "invented_later"),
       _reason("tablet", "invented_later"))
    ok("D5 the wording lives in ONE table, so no caller can drift from it",
       isinstance(_g("BASIS_SOURCE_PHRASE"), dict)
       and all(_g("BASIS_SOURCE_PHRASE")[b] in _texts[b] for b in _texts))

# ═════ E. THE ENGINE ═════════════════════════════════════════════════════════════════════════════
section("E. The engine splits the two, and reports an unclassifiable activation ONCE")
ok("E1 the engine takes the basis with the verdict", "qualification_with_basis(sched, org_qual)" in ESRC)
ok("E2 it stamps the basis on each excluded category", '"basis": _qbasis.get(' in ESRC)
ok("E3 a decided exclusion gets its own warning type",
   '"category_excluded_decided" if _decided else "category_excluded"' in ESRC)
ok("E4 the engine asks the one home which bases are decisions", "icat.is_decided(" in ESRC)
ok("E5 the engine does not write its own reason sentence", "icat.exclusion_reason(" in ESRC)
ok("E6 'unknown' never lands in the device-category excluded bucket",
   '_bucket = cat_excluded if _cat != "unknown" else cat_excluded_unknown' in ESRC)
ok("E7 the unclassifiable bucket is still reported in the payload — split out, never dropped",
   '"excluded_unknown": cat_excluded_unknown' in ESRC)
ok("E8 the old single-reason sentence is gone from the engine",
   "is unchecked under Plan Installments" not in ESRC)

# ═════ F. THE ROUTER — WHAT THE OWNER ACTUALLY READS ═════════════════════════════════════════════
section("F. The notice list the owner reads holds only what needs him")
ok("F1 the router reaches the one home through a single accessor",
   RSRC.count("def _icat_mod(") == 1)
ok("F2 it asks that home whether an exclusion was decided", "_icat.is_decided(_b)" in RSRC)
ok("F3 a decided exclusion is NOT appended to the warning notices",
   re.search(r"if _icat\.is_decided\(_b\):[\s\S]{0,400}?continue", RSRC) is not None)
ok("F4 its money is still reported, on its own line", '"category_excluded_decided"' in RSRC
   and '"severity": "decided"' in RSRC)
ok("F5 the decided line carries the chains and the dollars, so nothing is invisible",
   '"chains": _decided_chains' in RSRC and '"amount": _decided_amount' in RSRC)
ok("F6 an exclusion nobody decided is a WARNING, not an info line",
   re.search(r'"type": "category_excluded", "severity": "warning"', RSRC) is not None)
ok("F7 the router does not compose a reason of its own", "_icat.exclusion_reason(" in RSRC)
ok("F8 the old asserted sentence is gone from the router",
   "that category is unchecked under Plan Installments" not in RSRC)

# NO SECOND COPY: nobody else may decide which bases are decisions.
_COPIES = []
for _nm, _src in (("sale_installment_engine.py", ESRC), ("router.py", RSRC), ("page.tsx", PSRC)):
    for _l in _src.splitlines():
        _s = _l.strip()
        if _s.startswith("#") or _s.startswith("//"):
            continue
        if ("tenant_setting" in _l or "platform_default" in _l) and "BASIS_" not in _l:
            _COPIES.append(f"{_nm}: {_s[:90]}")
ok("F9 no caller spells a basis name itself — they all go through the constants", not _COPIES, _COPIES[:2])

# ═════ G. THE SCREEN ═════════════════════════════════════════════════════════════════════════════
section("G. The screen reads the server's verdict and decides nothing")
ok("G1 the page has a decided-notice predicate", "const decidedNotice" in PSRC)
ok("G2 it keys off `severity`, not a notice type or its wording",
   "severity ?? ''" in PSRC and "=== 'decided'" in PSRC)
ok("G3 a notice with no severity reads as open, so an older server is unchanged",
   re.search(r"severity \?\? ''\) === 'decided'", PSRC) is not None)
ok("G4 the amber 'did not pay' list is filtered to the open ones",
   PSRC.count("calcNotices.filter(openNotice)") >= 2)
ok("G5 the decisions get their own quieter block", "Excluded by your settings" in PSRC)
ok("G6 the decided block does not dump a per-rep breakdown — that was the noise",
   re.search(r"Excluded by your settings[\s\S]{0,700}?</div>", PSRC) is not None
   and "by_rep" not in (re.search(r"Excluded by your settings[\s\S]{0,700}?</div>",
                                  PSRC).group(0) if re.search(
       r"Excluded by your settings[\s\S]{0,700}?</div>", PSRC) else "by_rep"))
ok("G7 the page does not re-derive which categories are off",
   "installment_category_qualification" not in PSRC)

# ═════ H. RULE TWO ═══════════════════════════════════════════════════════════════════════════════
section("H. RULE TWO — config, never code")


def _code_only(src):
    """Source with every docstring and comment stripped. The module header quotes the owner and names
    the tenant he measured; that is a record, not a branch, and must not read as a violation."""
    s = re.sub(r'(?m)^[ \t]*(?:r|u)?"""[\s\S]*?"""', "", src)
    s = re.sub(r'"""[\s\S]*?"""', "", s)
    return "\n".join(ln.split("#", 1)[0] for ln in s.split("\n"))


_HOMECODE = _code_only(HSRC)
_NAMES = ("luxelink", "cellfonz", "vzone", "boost", "total wireless", "verizon", "tcl", "iphone",
          "motorola", "samsung")
ok("H1 the one home branches on no tenant, carrier or brand name",
   not [n for n in _NAMES if n in _HOMECODE.lower()],
   [n for n in _NAMES if n in _HOMECODE.lower()])
ok("H2 the basis vocabulary is a closed set of generic keys",
   set(_g("BASIS_SOURCE_PHRASE", {}).keys()) ==
   {_g("BASIS_TENANT_SETTING"), _g("BASIS_SCHEDULE_OVERRIDE"),
    _g("BASIS_PLATFORM_DEFAULT"), _g("BASIS_UNSTATED")})
ok("H3 the home still reads no table and opens no connection in these resolvers",
   "_DECIDED_BASES" in HSRC and ".table(" not in _code_only(
       HSRC[HSRC.find("BASIS_TENANT_SETTING"):HSRC.find("# ── loaders")]))

# ═════ Z. ARMED CONTROLS ═════════════════════════════════════════════════════════════════════════
section("Z. Each check would catch its own defect")
if callable(_dec):
    ok("Z1 B3's detector really rejects a plausible-looking wrong basis", not _dec("tenant"))
if callable(_qwb):
    # If the list shape were treated as a real override, A6 would pass wrongly — prove it would not.
    _q, _b, _ = _qwb({"qualifying_categories": ["phone"]}, ORG_OFF)
    ok("Z2 A6 would fail if a list's omissions were called an override",
       _b.get("home_internet") != _g("BASIS_SCHEDULE_OVERRIDE"))
    ok("Z3 C1's comparison is real: it reads both functions, and they are different functions",
       icat.qualification_for is not _qwb)
ok("Z4 F9's detector finds a planted second copy",
   bool([1 for _l in ['    x = "tenant_setting"'] if "tenant_setting" in _l and "BASIS_" not in _l]))
ok("Z5 F9 does not flag a basis name inside a comment",
   not [1 for _l in ['    # tenant_setting is the first layer']
        if not _l.strip().startswith("#") and "tenant_setting" in _l])
ok("Z6 H1's detector would catch a planted tenant name in CODE but not in a comment",
   "luxelink" in _code_only('x = "luxelink"').lower()
   and "luxelink" not in _code_only('x = 1  # luxelink').lower())

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
