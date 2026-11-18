"""Harness — app/modules/core/code128.py (DB-free, network-free).

WHAT IT PROVES, and why each part is here rather than trusted:

  A. The symbol table is internally sound: 107 entries, no duplicates, every data symbol exactly 11
     modules wide, the stop symbol 13, and every pattern's BAR widths summing to an even number —
     the structural parity rule of Code 128. A single mistyped row would scan as a different
     character on a real package; one was in fact caught this way while building it.
  B. Encode → measure → decode returns the original string, for every printable character the
     symbology carries and for the four RMAs read off the real drop-off labels.
  C. The modulo-103 check symbol matches a hand-computed known answer.
  D. **Widths measured off the actual label PHOTOGRAPHS decode to the RMA printed on that label.**
     These are not synthetic: each row is one scanline of pixel run-lengths from a photo Sanjot took
     in the store on 2026-11-18, including one label photographed upside down (its widths decode
     only when read right-to-left), and one whose bars drift so much that a single global module
     width fails — which is why the decoder re-derives the module width per symbol.
  E. A corrupted barcode returns None, never a plausible string. The whole point of re-printing is
     that the number is right; a decoder that guesses would hand the store a wrong RMA that scans.
  F. Input the symbology cannot carry is REFUSED by name, never silently dropped.
  G. The rendered SVG carries a quiet zone on both sides, escapes its text, and draws as many bar
     groups as the module string contains.

Run: python3 backend/harness_code128.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core.code128 import (  # noqa: E402
    PATTERNS, START_B, STOP, Code128Error, checksum, decode_widths, encode_modules,
    encode_values, encode_widths, render_svg,
)

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


# ── A. the table itself ──────────────────────────────────────────────────────────────────────────
print("A. symbol table")
ok("107 symbols (0..106)", len(PATTERNS) == 107, f"got {len(PATTERNS)}")
ok("no duplicate patterns", len(set(PATTERNS)) == 107)
bad_width = [i for i, p in enumerate(PATTERNS[:106]) if sum(int(c) for c in p) != 11]
ok("every data symbol is 11 modules", not bad_width, f"offenders {bad_width}")
ok("stop symbol is 13 modules in 7 runs",
   sum(int(c) for c in PATTERNS[STOP]) == 13 and len(PATTERNS[STOP]) == 7)
odd_parity = [i for i, p in enumerate(PATTERNS[:106])
              if sum(int(c) for c in p[0::2]) % 2 != 0]
ok("every symbol's bars sum to an even number (Code 128 parity)", not odd_parity,
   f"offenders {odd_parity}")
ok("Start B is symbol 104", PATTERNS[START_B] == "211214")


# ── B. round trip ────────────────────────────────────────────────────────────────────────────────
print("B. encode → widths → decode round trip")
# Four from RED labels and three from GREEN ones — decoded from the photographs themselves. The
# sort colour turns out to be irrelevant to the barcode: it tells the staff which bag a package goes
# in, and both colours carry the same 12-character reference in the same symbology. That is a
# MEASURED finding, which is why both colours are fixtures here.
REAL_RMAS = ["DTHdkRH1RRMA", "DxJKPcHgRRMA", "D7jPDVHpRRMA", "Dv9SPqHmRRMA",
             "DTdqPTHXRRMA", "D7Lm7CH2RRMA", "D55PMRHpRRMA"]
for s in REAL_RMAS:
    ok(f"real RMA {s}", decode_widths(encode_widths(s)) == s)
every_char = "".join(chr(c) for c in range(32, 127))
ok("every printable character the symbology carries",
   decode_widths(encode_widths(every_char)) == every_char)
ok("case is preserved (DTHdkRH1RRMA != DTHDKRH1RRMA)",
   decode_widths(encode_widths("DTHdkRH1RRMA")) != "DTHDKRH1RRMA")
ok("a one-character reference", decode_widths(encode_widths("A")) == "A")


# ── C. the check symbol ──────────────────────────────────────────────────────────────────────────
print("C. modulo-103 check symbol")
# Hand-computed: Start B (104) + 'A'(33)*1 + 'B'(34)*2 + 'C'(35)*3 = 104+33+68+105 = 310; 310%103=1
ok("known answer for 'ABC'", checksum([START_B, 33, 34, 35]) == 1,
   f"got {checksum([START_B, 33, 34, 35])}")
vals = encode_values("ABC")
ok("encode_values lays out start, data, check, stop",
   vals[0] == START_B and vals[1:4] == [33, 34, 35] and vals[4] == 1 and vals[5] == STOP, vals)


# ── D. widths measured off the real label photographs ────────────────────────────────────────────
print("D. real photographed labels (pixel run-lengths, one scanline each)")
PHOTO_SCANLINES = {
    # photo 3 of 2026-11-18 — "RED" label, Dr. Brown's bottles, read left-to-right
    "DTHdkRH1RRMA": [8, 4, 4, 7, 5, 15, 4, 4, 7, 12, 5, 11, 7, 5, 11, 12, 4, 3, 8, 11, 5, 3, 4, 11,
                     5, 14, 5, 8, 7, 4, 7, 15, 5, 7, 4, 4, 7, 12, 3, 5, 10, 5, 7, 11, 5, 3, 4, 11,
                     4, 8, 10, 9, 7, 4, 7, 12, 3, 4, 11, 5, 6, 12, 3, 5, 10, 5, 2, 5, 10, 5, 7, 11,
                     4, 3, 5, 11, 7, 11, 4, 7, 5, 14, 7, 5, 6, 12, 10, 5, 3, 4, 7],
    # photo 4 — "RED" label, engine mount
    "DxJKPcHgRRMA": [7, 4, 3, 8, 3, 15, 3, 5, 7, 11, 3, 12, 14, 8, 3, 7, 3, 5, 3, 4, 6, 5, 10, 11,
                     3, 4, 6, 12, 9, 5, 9, 5, 9, 5, 6, 5, 3, 14, 3, 4, 6, 7, 7, 10, 3, 4, 3, 11, 2,
                     7, 6, 4, 2, 15, 7, 11, 2, 5, 9, 5, 6, 12, 3, 4, 9, 5, 2, 5, 9, 5, 6, 11, 3, 3,
                     3, 10, 6, 10, 6, 4, 5, 10, 5, 3, 6, 9, 8, 4, 2, 4, 5],
}
for rma, widths in PHOTO_SCANLINES.items():
    ok(f"photo decodes to {rma}", decode_widths(widths) == rma, f"got {decode_widths(widths)}")

# This label was photographed UPSIDE DOWN. Its widths decode only when read in the other direction —
# which is why the photo layer must try both, and why a store person need not worry which way up the
# package lands on the counter.
UPSIDE_DOWN = [6, 4, 3, 7, 3, 14, 4, 3, 7, 10, 4, 10, 10, 4, 6, 4, 10, 3, 4, 14, 6, 7, 3, 4, 9, 5,
               9, 4, 6, 4, 3, 4, 6, 10, 4, 10, 10, 3, 3, 4, 6, 10, 7, 10, 4, 3, 3, 10, 4, 3, 3, 7,
               13, 7, 6, 10, 4, 3, 9, 4, 7, 10, 3, 3, 10, 4, 2, 4, 9, 4, 6, 10, 4, 2, 4, 10, 7, 10,
               9, 4, 3, 3, 6, 10, 7, 10, 9, 4, 3, 3, 7, 14, 1]
ok("an upside-down label decodes when read in reverse", decode_widths(UPSIDE_DOWN) == "D7jPDVHpRRMA",
   f"got {decode_widths(UPSIDE_DOWN)}")
ok("…and reads as nothing in the direction it was photographed",
   decode_widths(UPSIDE_DOWN[::-1]) is None)


# ── E. corruption is refused, not guessed ────────────────────────────────────────────────────────
print("E. a damaged barcode says nothing rather than something wrong")
clean = encode_widths("DTHdkRH1RRMA")
broken = list(clean)
broken[7] = broken[7] + 2          # one run stretched — a smear, a fold, a worn print head
ok("a stretched run is rejected", decode_widths(broken) is None, decode_widths(broken))
truncated = clean[:40]             # the label torn in half
ok("a truncated barcode is rejected", decode_widths(truncated) is None)
swapped = list(clean)
swapped[12], swapped[13] = swapped[13], swapped[12]
ok("a swapped bar/space pair is rejected or exact",
   decode_widths(swapped) in (None, "DTHdkRH1RRMA"), decode_widths(swapped))
ok("noise is rejected", decode_widths([3] * 91) is None)
ok("too few runs is rejected", decode_widths([2, 1, 1, 2, 1, 4]) is None)
# The check symbol is what makes the above safe: break ONLY the check and the data still parses.
v = encode_values("DTHdkRH1RRMA")
v[-2] = (v[-2] + 1) % 103
w = []
for sym in v:
    w.extend(int(c) for c in PATTERNS[sym])
ok("a wrong check symbol is rejected even though every symbol parses", decode_widths(w) is None)


# ── F. refusals by name ──────────────────────────────────────────────────────────────────────────
print("F. input the symbology cannot carry is refused")
for bad, why in [("", "empty"), ("RMAé123", "accented letter"), ("A\tB", "tab"),
                 ("RM A", "non-breaking space")]:
    try:
        encode_widths(bad)
        ok(f"refuses {why}", False, "it encoded")
    except Code128Error:
        ok(f"refuses {why}", True)
try:
    encode_widths(None)  # type: ignore[arg-type]
    ok("refuses a non-string", False)
except Code128Error:
    ok("refuses a non-string", True)


# ── G. the rendered label ────────────────────────────────────────────────────────────────────────
print("G. rendered SVG")
svg = render_svg("DTHdkRH1RRMA")
bits = encode_modules("DTHdkRH1RRMA")
ok("is an svg element", svg.startswith("<svg") and svg.endswith("</svg>"))
ok("prints the reference under the bars", "DTHdkRH1RRMA" in svg)
groups = len(re.findall(r"<rect x=", svg))
ok("draws one rect per bar group", groups == bits.count("10") + (1 if bits.endswith("1") else 0),
   f"{groups} rects")
ok("leaves a quiet zone before the first bar", '<rect x="36"' in svg, svg[:400])
ok("can be rendered without the text line", "<text" not in render_svg("ABC", show_text=False))
ok("escapes text that would break the markup", "&lt;" in render_svg("A<B"))
try:
    render_svg("ABC", module_px=0)
    ok("refuses a module narrower than a pixel", False)
except Code128Error:
    ok("refuses a module narrower than a pixel", True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
