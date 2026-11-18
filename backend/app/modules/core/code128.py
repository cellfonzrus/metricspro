"""Code 128 — the ONE home for turning a reference string into a scannable barcode, and for
reading one back out of measured bar/space widths.

WHY THIS FILE EXISTS. A UPS Store consolidates return drop-offs by scanning the RMA barcode the
return label printer produced. When that barcode will not scan — crumpled, faint, torn, printed
light — the package sits in the back room unscanned and never goes back to the vendor. The store
has the number (it is printed in plain text under the bars); what it has lost is a MACHINE-READABLE
copy of it. So the platform re-prints one.

WHAT THE REAL LABELS USE, measured rather than assumed (2026-11-18): four sample photos of Amazon
"RED" return labels were decoded here, bar by bar, from the photographs themselves. Every one is
**Code 128, start code B**, with the standard modulo-103 symbol check, and the encoded data is
EXACTLY the RMA printed beneath it — no prefix, no suffix, no delimiters:

    DTHdkRH1RRMA   DxJKPcHgRRMA   D7jPDVHpRRMA   (and Dv9SPqHmRRMA, partially decoded)

Those four strings, and the symbol widths measured off the photos, are fixtures in
backend/harness_code128.py so this claim is re-proved on every run rather than remembered.

WHY BOTH DIRECTIONS LIVE HERE. The encoder and the decoder are two statements of ONE fact — the
Code 128 symbol table. Two copies of that table would drift (the house rule: one fact, one home,
dereferenced, never copied), and keeping them together buys a true round-trip proof: the harness
encodes a string, measures the bars it produced, decodes them, and demands the original back.

NO DEPENDENCIES AND NO IMAGES. Rendering is SVG text, so a store prints from the browser and the
API needs no imaging stack. Reading a barcode out of a PHOTOGRAPH is a different job (finding the
bars in a picture) and lives in storeops/dropoff_photo.py; it hands its measured widths to
`decode_widths` here rather than carrying its own table.

THIS MODULE IS TENANT-NEUTRAL. Code 128 is a published symbology, not a tenant's vocabulary: it is
a POLICY, so a house default is right here. WHICH symbology a given label type uses, and what its
reference numbers look like, ARE a tenant's own declarations and live in storeops/dropoff_label.py.
"""
from __future__ import annotations

# ── The symbol table ─────────────────────────────────────────────────────────────────────────────
# Index = symbol value (0..106). Each entry is six run widths, bar-first (bar, space, bar, space,
# bar, space), in modules. Values 0..102 are the data symbols; 103/104/105 are Start A/B/C; 106 is
# the stop symbol, which carries a seventh run (the 2-module termination bar).
PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212",
    "221213", "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221",
    "223211", "221132", "221231", "213212", "223112", "312131", "311222", "321122", "321221",
    "312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321",
    "112313", "132113", "132311", "211313", "231113", "231311", "112133", "112331", "132131",
    "113123", "113321", "133121", "313121", "211331", "231131", "213113", "213311", "213131",
    "311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111", "111242",
    "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311",
    "113141", "114131", "311141", "411131",
    "211412",   # 103 — Start A
    "211214",   # 104 — Start B
    "211232",   # 105 — Start C
    "2331112",  # 106 — Stop (seven runs)
)

START_B = 104
STOP = 106

# The lowest and highest ASCII code set B can carry: space (32) through '~' (126).
_CHAR_MIN, _CHAR_MAX = 32, 126


class Code128Error(ValueError):
    """The input cannot be carried by this symbology — stated, never silently mangled."""


# ── Encode ───────────────────────────────────────────────────────────────────────────────────────
def encode_values(data: str) -> list[int]:
    """`data` → the full list of symbol values: Start B, one per character, the check symbol, Stop.

    Code set B only, deliberately. It carries every character a return reference uses (upper case,
    lower case, digits, punctuation) and — unlike set C's digit pairs or set A's control codes — it
    needs no mode switching, so the encoder has no branch that could place a character in the wrong
    set. A character set B cannot carry RAISES rather than being dropped or folded: a barcode that
    silently loses a character scans cleanly and sends the package to the wrong place.
    """
    if not isinstance(data, str) or data == "":
        raise Code128Error("Nothing to encode — the reference is empty.")
    values = [START_B]
    for ch in data:
        o = ord(ch)
        if o < _CHAR_MIN or o > _CHAR_MAX:
            raise Code128Error(
                f"{ch!r} cannot be printed in a Code 128-B barcode (only ASCII space..~)."
            )
        values.append(o - _CHAR_MIN)
    values.append(checksum(values))
    values.append(STOP)
    return values


def checksum(values: list[int]) -> int:
    """The modulo-103 symbol check: start value + each data symbol weighted by its 1-based position.

    `values` is the start symbol followed by the data symbols (no check, no stop).
    """
    if not values:
        raise Code128Error("A checksum needs at least the start symbol.")
    total = values[0] + sum((i + 1) * v for i, v in enumerate(values[1:]))
    return total % 103


def encode_widths(data: str) -> list[int]:
    """`data` → the run widths in modules, bar first, alternating bar/space across the whole symbol.

    This is what a printer draws and what a scanner measures, so it is also the surface the decoder
    reads — which is what makes the round-trip proof in the harness possible.
    """
    out: list[int] = []
    for v in encode_values(data):
        out.extend(int(c) for c in PATTERNS[v])
    return out


def encode_modules(data: str) -> str:
    """`data` → a string of '1' (bar) and '0' (space), one character per module.

    The quiet zone is NOT included: a quiet zone is a property of the printed label, and the renderer
    owns it. Returning it here would let a caller print a barcode whose quiet zone is already in the
    picture and then add another, or none at all.
    """
    bits: list[str] = []
    bar = True
    for w in encode_widths(data):
        bits.append(("1" if bar else "0") * w)
        bar = not bar
    return "".join(bits)


# ── Decode ───────────────────────────────────────────────────────────────────────────────────────
_BY_PATTERN = {p: i for i, p in enumerate(PATTERNS)}


def _quantize(widths: list[float], modules: int) -> str:
    """Measured widths → the pattern string, using THIS symbol's own module width.

    Each Code 128 symbol is exactly 11 modules wide (the stop is 13), so the module width is
    re-derived per symbol instead of once for the whole barcode. That is not a refinement: a
    photograph of a label is never square to the camera, so the apparent module width drifts across
    the barcode, and a single global estimate decodes the first few characters and then fails. This
    was measured on the real photos — a global estimate read "Dv9" and lost the rest.
    """
    m = sum(widths) / float(modules)
    if m <= 0:
        return ""
    return "".join(str(max(1, min(4, int(round(w / m))))) for w in widths)


def decode_widths(widths: list[float]) -> str | None:
    """Measured run widths (bar first) → the encoded string, or None if this is not a valid symbol.

    None means "this is not a Code 128 barcode I can vouch for" — never a best guess. The check
    symbol is verified before anything is returned, because the whole point of re-printing a barcode
    is that the number is right; a decoder that returns probable text would hand the store a wrong
    RMA that scans perfectly.
    """
    w = [float(x) for x in widths]
    if len(w) < 20:
        return None
    values: list[int] = []
    i = 0
    while i + 6 <= len(w):
        if i + 7 <= len(w) and _quantize(w[i:i + 7], 13) == PATTERNS[STOP]:
            values.append(STOP)
            i += 7
            break
        v = _BY_PATTERN.get(_quantize(w[i:i + 6], 11))
        if v is None:
            return None
        values.append(v)
        i += 6
    if len(values) < 4 or values[-1] != STOP or values[0] != START_B:
        # Start A and C are valid Code 128 but are not what these labels use; rather than decode a
        # symbol set this platform has never seen on a real label and cannot test, say nothing.
        return None
    body = values[1:-1]
    check, data = body[-1], body[:-1]
    if not data or checksum([values[0]] + data) != check:
        return None
    return "".join(chr(v + _CHAR_MIN) for v in data if 0 <= v <= _CHAR_MAX - _CHAR_MIN)


# ── Render ───────────────────────────────────────────────────────────────────────────────────────
def render_svg(
    data: str,
    *,
    module_px: int = 3,
    height_px: int = 120,
    quiet_modules: int = 12,
    show_text: bool = True,
) -> str:
    """`data` → a self-contained SVG the browser can print.

    `module_px` 3 at 96dpi is ~0.8mm per module, comfortably above the 0.25mm minimum a handheld
    laser scanner needs, and `quiet_modules` 12 exceeds the 10-module quiet zone the specification
    requires on each side — the margin is deliberate, because this barcode exists precisely because
    a marginal one failed to scan.

    The human-readable text is printed BENEATH the bars by default and is not optional in practice:
    it is how a person confirms the re-print matches the package before it goes in the bag.
    """
    if module_px < 1:
        raise Code128Error("A barcode module must be at least one pixel wide.")
    bits = encode_modules(data)
    text_h = 26 if show_text else 0
    width = (len(bits) + 2 * quiet_modules) * module_px
    total_h = height_px + text_h
    rects: list[str] = []
    x = quiet_modules
    i = 0
    while i < len(bits):
        j = i
        while j < len(bits) and bits[j] == bits[i]:
            j += 1
        if bits[i] == "1":
            rects.append(
                f'<rect x="{x * module_px}" y="0" width="{(j - i) * module_px}" '
                f'height="{height_px}" fill="#000"/>'
            )
        x += j - i
        i = j
    label = ""
    if show_text:
        safe = (data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace('"', "&quot;"))
        label = (
            f'<text x="{width / 2}" y="{height_px + 20}" text-anchor="middle" '
            f'font-family="monospace" font-size="18" letter-spacing="1" fill="#000">{safe}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_h}" '
        f'viewBox="0 0 {width} {total_h}" role="img" aria-label="Barcode">'
        f'<rect width="{width}" height="{total_h}" fill="#fff"/>'
        f"{''.join(rects)}{label}</svg>"
    )
