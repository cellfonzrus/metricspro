"""BUILD GUARD + ALLOCATOR — a section id carries its module, and a claim on one cannot be taken twice.

Owner directive 2026-10-11: *"So we should assign the numbers with a prefix of the module we are
working with and update the same in the index and back connection it"*.

THE DEFECT, NOT THE INSTANCE. The instance was pull request #438 losing §72, §73 and §74 to three
earlier merges inside an hour. The class is: **an identifier with no allocator is chosen by reading
the file, so every branch open at once reads the same free id as free.** `harness_unique_numbers_lock.py`
already failed the build on a duplicate — but only once both claims sat in one tree, which is after
one of them merged. Detection was never the gap; allocation was. Index section CORE-01 states the
scheme; this file is what makes it true of every tree CI builds.

WHAT THIS FAILS ON
  A  the register in CORE-01.5 is missing, unparseable, or claims one id twice.
  B  a register row names a section that is not a real heading of the index.
  C  a top-level `##` heading has no register row — so the allocator cannot be bypassed — or a
     top-level heading carries no id at all.
  D  a register row names a module that is not a folder under backend/app/modules/, or a folder is
     neither named by a row nor listed in CORE-01.6 with a reason.
  E  CORE-01.6 excuses a module that IS now covered (the list may only shrink), or lists one twice.
  F  a declared module is contradicted by the section's own body — the modules whose code paths it
     cites, wherever it cites any.
  G  a NEW bare-numeric id: the numeric space is frozen at the 112 ids that predate the directive
     (LEGACY_NUMERIC below, which may only shrink). A prefixed id must be <MODULE>-NN with the
     module's own folder name and a per-module sequence that starts at 01 and leaves no gap, so a
     stale claim cannot hide in a hole.
  H  a `module_graph.FACTS[*]["index"]` reference that does not resolve to a REGISTERED section.
     This is the code→section half of the back connection, and it tightens the graph guard's own
     check E from "a real heading" to "a registered section".

Z  negative controls — each check is planted with its own defect and must fail. A check that cannot
   fail is not a check (index 24).

ONE HOME, DEREFERENCED. "An id is claimed once" already lived in `harness_unique_numbers_lock.py`,
which already parsed this file's headings, its `§N.M **…**` paragraphs and its TOC rows. This file
imports `_HEADING`, `_PARA` and `GRANDFATHERED_SECTIONS` from there rather than keeping a second
copy of any of them; that lock's own checks moved inside its `main()` so it can be imported at all.
"Which files answer which question" stays in `app/modules/core/module_graph.py` and is read, never
copied.

Stdlib only, no DB, no network.
  Run:      python3 harness_index_section_register.py
  Allocate: python3 harness_index_section_register.py --next commcalc
"""
from __future__ import annotations

import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
MODULES_DIR = os.path.join(HERE, "app", "modules")

sys.path.insert(0, HERE)
from harness_unique_numbers_lock import (  # noqa: E402  (the ONE home for id uniqueness)
    _HEADING,
    _PARA,
)
from app.modules.core import module_graph as G  # noqa: E402  (the ONE home for the module graph)

REGISTER = ("<!-- SECTION-REGISTER:BEGIN -->", "<!-- SECTION-REGISTER:END -->")
EXCUSES = ("<!-- MODULES-WITHOUT-SECTION:BEGIN -->", "<!-- MODULES-WITHOUT-SECTION:END -->")

# FROZEN. The ids that predate the directive, registered as they are because renumbering them would
# rewrite 4,061 references in 895 files (CORE-01.7). This list may only SHRINK — a numeric id that
# is not in it is a NEW bare number, which check G refuses. Never add to it: allocate <MODULE>-NN.
LEGACY_NUMERIC = frozenset({
    '1', '2', '3', '4', '4a', '4b', '4c', '4d', '4e', '5', '6', '7', '8', '9', '10', '11', '12',
    '13', '14', '15z', '15v', '16', '17', '18', '19', '20', '21', '22', '23', '23b', '23c',
    '23d', '23e', '23f', '23g', '23h', '23i', '23j', '23k', '23l', '23m', '23n', '23o', '23p',
    '23s', '24', '23q', '23r', '23t', '23u', '23v', '23w', '23x', '23y', '23z', '25', '26',
    '27', '28', '29', '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40', '41',
    '42', '43', '41a', '44', '45', '46', '47', '48', '49', '50', '51', '52', '53', '54', '55',
    '56', '57', '58', '59', '60', '61', '62', '63', '64', '65', '66', '67', '68', '76', '71',
    '69', '70', '75', '72', '73', '74', '23aa', '15.1', '15.2', '15.3',
})

PREFIXED = re.compile(r"^([A-Z][A-Z0-9]{2,11})-([0-9]{2})$")
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")

passed = failed = 0


def check(name: str, ok: bool, detail="") -> bool:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")
    return bool(ok)


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────
def modules_on_disk(path=MODULES_DIR) -> set:
    return {d for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d)) and not d.startswith("__")}


def table_rows(lines, markers) -> list:
    """-> [(col1, col2), …] between the markers, header and separator rows dropped."""
    try:
        a = lines.index(markers[0])
        b = lines.index(markers[1])
    except ValueError:
        return []
    out = []
    for line in lines[a + 1:b]:
        m = _ROW.match(line)
        if not m:
            continue
        one, two = m.group(1), m.group(2)
        if set(one) <= set("-: ") or one in ("Section", "Module"):
            continue
        out.append((one, two))
    return out


def parse_register(lines) -> list:
    """-> [(section_id, [module, …]), …] in file order, duplicates KEPT so check A can see them."""
    return [(sid, [m.strip() for m in mods.split(",") if m.strip()])
            for sid, mods in table_rows(lines, REGISTER)]


def headings(lines) -> set:
    """Every numbered heading id in the index — the ONE home's regex, not a second copy."""
    return {m.group(1) for line in lines for m in [_HEADING.match(line)] if m} | \
           {m.group(1) for line in lines for m in [_PARA.match(line)] if m}


# A top-level heading's id: legacy numeric (`6p`, `23aa`, `15.1`) or module-prefixed. Deliberately
# narrow — a `## ` heading that does not match is an UNNUMBERED top-level section, which `unnumbered`
# reports rather than quietly treating its first word as an id.
TOP = re.compile(r"^## +§?\s*((?:\d+[a-z]*|[A-Z][A-Z0-9]{2,11}-\d{2})(?:\.\d+[a-z]?)*)\.?\s+\S")
ANY_TOP = re.compile(r"^## +\S")


def sections(lines) -> "tuple[dict, list]":
    """-> ({top_level_id: body lines, `###` subsections included}, [unnumbered heading, …])"""
    out = collections.OrderedDict()
    unnumbered = []
    cur = None
    for line in lines:
        if ANY_TOP.match(line):
            m = TOP.match(line)
            if m:
                cur = m.group(1)
                out.setdefault(cur, [])
            else:
                unnumbered.append(line.strip())
                cur = None
        elif cur is not None:
            out[cur].append(line)
    return out, unnumbered


def cited_modules(body, on_disk) -> set:
    pat = re.compile(r"\b(?:modules/)?(" + "|".join(sorted(on_disk)) + r")/[A-Za-z0-9_./]+\.(?:py|sql)")
    return set(pat.findall("\n".join(body)))


def parent_of(sid: str) -> str:
    """'19.43'->'19'  '72.2'->'72'  '6p'->'6'  'CORE-01.2'->'CORE-01'  '14w'->'14'"""
    base = sid.split(".")[0]
    return base if PREFIXED.match(base) else (re.match(r"^\d+", base).group(0) if re.match(r"^\d+", base) else base)


def resolves(sid: str, registered) -> bool:
    return sid in registered or parent_of(sid) in registered


# ── checks ───────────────────────────────────────────────────────────────────────────────────────
def check_register(rows) -> list:
    """A — parseable and no id claimed twice. -> problems."""
    probs = []
    if len(rows) < 100:
        probs.append(f"the register yielded {len(rows)} rows — the markers or the table moved")
    dupes = [sid for sid, n in collections.Counter(s for s, _ in rows).items() if n > 1]
    for sid in sorted(dupes):
        probs.append(f"register id {sid} is claimed by {len(rows)} rows — one id, one section")
    for sid, mods in rows:
        if not mods:
            probs.append(f"register id {sid} names no module")
    return probs


def check_resolution(rows, heads) -> list:
    """B — every register row names a real heading of the index."""
    return [f"register id {sid} is not a heading of the index"
            for sid in sorted({s for s, _ in rows}) if sid not in heads]


def check_coverage(top, rows) -> list:
    """C — every top-level `##` section has a register row."""
    registered = {s for s, _ in rows}
    return [f"section {sid} has no register row — allocate one in CORE-01.5"
            for sid in top if sid not in registered]


def check_modules(rows, excuses, on_disk) -> list:
    """D/E — both directions between the module folders and the register."""
    probs = []
    named = {m for _, mods in rows for m in mods}
    for m in sorted(named):
        if m not in on_disk:
            probs.append(f"register names module {m!r}, which is not a folder under app/modules/")
    excused = [m for m, _ in excuses]
    for m, why in excuses:
        if not why.strip():
            probs.append(f"excused module {m} gives no reason")
        if m in named:
            probs.append(f"module {m} is excused in CORE-01.6 but IS now registered — shrink the list")
        if m not in on_disk:
            probs.append(f"excused module {m!r} is not a folder under app/modules/")
    for m, n in collections.Counter(excused).items():
        if n > 1:
            probs.append(f"module {m} is excused {n} times")
    for m in sorted(on_disk - named - set(excused)):
        probs.append(f"module {m} is in neither the register nor CORE-01.6 — register a section or "
                     f"say why there is none")
    return probs


# A section citing this many distinct modules is CROSS-CUTTING — the three cross-references, the gap
# list, the harness audit. Its body spans the estate, so no single declaration can be contradicted
# by it, and check F reports rather than rules on it.
CROSS_CUTTING = 4


def check_declared_vs_cited(rows, bodies, on_disk) -> list:
    """F — a declared module the section's own body contradicts."""
    probs = []
    for sid, mods in rows:
        body = bodies.get(sid)
        if body is None:
            continue
        cited = cited_modules(body, on_disk)
        if len(cited) >= CROSS_CUTTING:
            continue
        if cited and not (set(mods) & cited):
            probs.append(f"section {sid} declares {mods} but cites code only in {sorted(cited)}")
    return probs


def check_scheme_closed(rows) -> list:
    """G — the numeric space is frozen; a prefixed id is well formed and gapless per module."""
    probs = []
    ids = [s for s, _ in rows]
    numeric = {s for s in ids if not s[0].isupper()}
    for s in sorted(numeric - LEGACY_NUMERIC):
        probs.append(f"{s} is a NEW bare-numeric section — the numeric space is closed; "
                     f"allocate <MODULE>-NN instead")
    for s in sorted(LEGACY_NUMERIC - numeric):
        probs.append(f"frozen legacy id {s} is no longer registered — shrink LEGACY_NUMERIC")
    seq = collections.defaultdict(set)
    for sid, mods in rows:
        if sid[0].isupper():
            m = PREFIXED.match(sid)
            if not m:
                probs.append(f"{sid} is not <MODULE>-NN (upper-cased folder name, two digits)")
                continue
            prefix, num = m.group(1), int(m.group(2))
            if prefix.lower() not in {x.lower() for x in mods}:
                probs.append(f"{sid} carries prefix {prefix} but is declared {mods}")
            seq[prefix].add(num)
    for prefix, nums in sorted(seq.items()):
        want = set(range(1, max(nums) + 1))
        if nums != want:
            probs.append(f"{prefix} numbers {sorted(nums)} leave a gap — take the next one, "
                         f"{sorted(want - nums)}")
    return probs


def check_graph_interlink(rows, graph) -> list:
    """H — every fact's index reference resolves to a REGISTERED section."""
    registered = {s for s, _ in rows}
    probs = []
    for key in sorted(graph.FACTS):
        for ref in graph.FACTS[key]["index"]:
            if not resolves(ref, registered):
                probs.append(f"module_graph fact {key!r} points at §{ref}, which is not a "
                             f"registered section")
    return probs


# ── allocator ────────────────────────────────────────────────────────────────────────────────────
def next_id(rows, module: str) -> str:
    prefix = module.upper()
    taken = {int(m.group(2)) for s, _ in rows for m in [PREFIXED.match(s)] if m and m.group(1) == prefix}
    n = 1
    while n in taken:
        n += 1
    return f"{prefix}-{n:02d}"


def allocate(module: str) -> int:
    on_disk = modules_on_disk()
    if module not in on_disk:
        print(f"{module!r} is not a folder under backend/app/modules/. Pick one of: "
              f"{', '.join(sorted(on_disk))}")
        return 1
    lines = open(INDEX, encoding="utf-8").read().split("\n")
    rows = parse_register(lines)
    sid = next_id(rows, module)
    print(f"{sid}\n")
    print("Paste this row into the register in CORE-01.5, keeping the table in file order:\n")
    print(f"| {sid} | {module} |\n")
    print(f"Head the section `## §{sid}. <TITLE>` and add a row to the §1 TOC.")
    print("If the register line conflicts when you push, somebody else took it: re-run this and "
          "re-paste. Nothing outside your branch references it yet.")
    return 0


# ── negative controls ────────────────────────────────────────────────────────────────────────────
def controls(on_disk) -> None:
    print("Z. negative controls — each check is planted with its own defect")
    check("control: a register the markers no longer bound fails A",
          any("the markers or the table moved" in p for p in check_register([])))
    check("control: one id claimed twice fails A",
          any("claimed by" in p for p in check_register([("9", ["a"]), ("9", ["b"])])))
    check("control: a row naming no module fails A",
          any("names no module" in p for p in check_register([("9", [])])))
    check("control: a register id that is not a heading fails B",
          check_resolution([("999", ["core"])], {"1"}) != [])
    check("control: an unregistered `##` section fails C",
          check_coverage(["77"], [("1", ["core"])]) != [])
    check("control: an unnumbered `##` heading is reported, not read as an id",
          sections(["## Appendix — notes", "body"]) == ({}, ["## Appendix — notes"]))
    check("control: every id shape is read as a top-level section",
          list(sections(["## 23aa. A", "## §43 B", "## §CORE-01. C", "## 6p. D"])[0]) ==
          ["23aa", "43", "CORE-01", "6p"])
    check("control: a registered section passes C",
          check_coverage(["1"], [("1", ["core"])]) == [])
    check("control: the grandfathered duplicate heading §23s is still ONE register row",
          sum(1 for s, _ in parse_register(open(INDEX, encoding="utf-8").read().split("\n"))
              if s == "23s") == 1)
    check("control: a module that is not a folder fails D",
          any("not a folder" in p for p in check_modules([("1", ["nosuch"])], [], {"core"})))
    check("control: a folder in neither place fails D",
          any("in neither" in p for p in check_modules([("1", ["core"])], [], {"core", "crm"})))
    check("control: an excuse for a covered module fails E",
          any("shrink the list" in p
              for p in check_modules([("1", ["crm"])], [("crm", "undocumented")], {"crm"})))
    check("control: an excuse with no reason fails E",
          any("no reason" in p for p in check_modules([("1", ["core"])], [("crm", "  ")],
                                                      {"core", "crm"})))
    body = ["see backend/app/modules/closing/router.py:10 for the write"]
    check("control: a declared module the body contradicts fails F",
          check_declared_vs_cited([("1", ["commcalc"])], {"1": body}, on_disk) != [])
    check("control: a declared module the body cites passes F",
          check_declared_vs_cited([("1", ["closing"])], {"1": body}, on_disk) == [])
    wide = ["app/modules/%s/router.py:1" % m for m in sorted(on_disk)[:CROSS_CUTTING]]
    check("control: a cross-cutting section is reported on, not ruled on, by F",
          check_declared_vs_cited([("1", ["core"])], {"1": wide}, on_disk) == [])
    check("control: three modules is still narrow enough for F to rule",
          check_declared_vs_cited([("1", ["core"])], {"1": wide[:3]}, on_disk) != [])
    check("control: a body citing nothing passes F",
          check_declared_vs_cited([("1", ["commcalc"])], {"1": ["prose only"]}, on_disk) == [])
    check("control: a NEW bare number fails G",
          any("numeric space is closed" in p for p in check_scheme_closed([("77", ["commcalc"])])))
    check("control: a prefixed id passes G where a bare one fails",
          not any("numeric space is closed" in p
                  for p in check_scheme_closed([("COMMCALC-01", ["commcalc"])])))
    check("control: a malformed prefixed id fails G",
          any("is not <MODULE>-NN" in p for p in check_scheme_closed([("COMMCALC-1", ["commcalc"])])))
    check("control: a prefix that disagrees with the declared module fails G",
          any("carries prefix" in p for p in check_scheme_closed([("CLOSING-01", ["commcalc"])])))
    check("control: a gap in a module's sequence fails G",
          any("leave a gap" in p for p in check_scheme_closed([("CORE-01", ["core"]),
                                                               ("CORE-03", ["core"])])))
    check("control: a dropped frozen legacy id fails G",
          any("shrink LEGACY_NUMERIC" in p for p in check_scheme_closed([("1", ["core"])])))

    class _G:
        FACTS = {"planted": {"index": ("99999.9",)}}
    check("control: a fact pointing at an unregistered section fails H",
          check_graph_interlink([("1", ["core"])], _G) != [])
    check("control: a fact pointing at a registered section's subsection passes H",
          check_graph_interlink([("19", ["core"])],
                                type("g", (), {"FACTS": {"k": {"index": ("19.43",)}}})) == [])
    check("control: the allocator skips a taken number",
          next_id([("CORE-01", ["core"])], "core") == "CORE-02")
    check("control: the allocator fills a hole rather than running past it",
          next_id([("CORE-02", ["core"])], "core") == "CORE-01")
    check("control: parent_of resolves every id shape",
          [parent_of(s) for s in ("19.43", "72.2", "6p", "CORE-01.2", "14w", "23s.8")] ==
          ["19", "72", "6", "CORE-01", "14", "23"])


# ── main ─────────────────────────────────────────────────────────────────────────────────────────
def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--next":
        if len(args) < 2:
            print("usage: harness_index_section_register.py --next <module folder name>")
            return 1
        return allocate(args[1])

    lines = open(INDEX, encoding="utf-8").read().split("\n")
    on_disk = modules_on_disk()
    rows = parse_register(lines)
    excuses = table_rows(lines, EXCUSES)
    heads = headings(lines)
    bodies, unnumbered = sections(lines)
    top = list(bodies)

    print("A. the register parses and no id is claimed twice")
    check("the register was found and read", len(rows) >= 100, f"{len(rows)} rows")
    probs = check_register(rows)
    check("no register id is claimed twice", not probs, "\n        " + "\n        ".join(probs))

    print("B. every register row names a real heading")
    probs = check_resolution(rows, heads)
    check("every register id resolves to a heading of the index", not probs,
          "\n        " + "\n        ".join(probs))

    print("C. every top-level section is registered — the allocator cannot be bypassed")
    check("the index yielded its sections", len(top) > 100, f"{len(top)} `##` sections")
    probs = check_coverage(top, rows)
    check("every `##` section has a register row", not probs,
          "\n        " + "\n        ".join(probs))
    check("no top-level section is unnumbered — an id is how it is referenced at all",
          not unnumbered, "\n        " + "\n        ".join(unnumbered))

    print("D/E. every module folder resolves to a section, or says why it does not")
    check("the module folders were read", len(on_disk) > 15, f"{len(on_disk)} folders")
    probs = check_modules(rows, excuses, on_disk)
    check("the module ↔ section mapping is complete in both directions", not probs,
          "\n        " + "\n        ".join(probs))

    print("F. a declared module is not contradicted by the section's own body")
    probs = check_declared_vs_cited(rows, bodies, on_disk)
    check("every declared module is among the modules its section cites (where it cites any)",
          not probs, "\n        " + "\n        ".join(probs))

    print("G. the scheme is ONE scheme — the numeric space is closed")
    probs = check_scheme_closed(rows)
    check("no new bare number, and every prefixed id is well formed and gapless", not probs,
          "\n        " + "\n        ".join(probs))

    print("H. the module graph's index references resolve to REGISTERED sections")
    probs = check_graph_interlink(rows, G)
    check("every fact names a registered section", not probs,
          "\n        " + "\n        ".join(probs))

    controls(on_disk)

    print(f"\nregister: {len(rows)} sections, {len({m for _, ms in rows for m in ms})} modules, "
          f"{len(excuses)} module(s) with no section yet")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
