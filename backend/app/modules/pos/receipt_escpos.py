"""THE receipt as commands a till printer understands (§POS-04).

Owner 2026-10-20, verbatim: *"the receipts will print to the thermal printer only if desired by the
user, there cannot be any harcoded rule so we need have a helper"*.

WHAT THIS IS, AND WHAT IT IS NOT. A browser can hand a page to the operating system's print dialog;
it cannot speak to a USB till printer, and it cannot install a driver. So a receipt that goes to the
printer *directly* — no dialog, a proper cut — has to leave this platform as the printer's own byte
stream and be handed over by a small helper running on the till PC (`tools/pos-print-helper/`).

THIS IS NOT A SECOND RECEIPT. It renders `sale_receipt.slip_model` — the one home for what a till
slip says — exactly as `sale_receipt.thermal_html` does. Every toggle an admin sets and every figure
the slip adds up is decided there, once, for both media. If this module read the sale or the template
for itself, the paper copy and the printed copy would answer the same question twice and drift.

RULE TWO. No printer make is named or branched on here. The command set below is the ESC/POS
vocabulary every till printer of this kind implements; which machine is at the counter is free text
the store declares (`receipt_printer_label`) and the helper's own concern, never this module's.
"""
import base64

# ── the command vocabulary (ESC/POS, the same on every printer that speaks it) ────────────────────
ESC = b"\x1b"
GS = b"\x1d"
INIT = ESC + b"@"                      # reset: clear any state the last slip left behind
ALIGN = {"left": ESC + b"a\x00", "center": ESC + b"a\x01", "right": ESC + b"a\x02"}
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
BIG_ON, BIG_OFF = GS + b"!\x11", GS + b"!\x00"   # double width + height, and back to normal
FEED_CUT = b"\n" * 4 + GS + b"V\x42\x00"         # feed the slip clear of the head, then cut

#: The printer's own single-byte page; a character it has never heard of prints as "?" rather than
#: silently truncating the line.
CODEPAGE = "cp437"


def _line(text="", *, align="left", bold=False, big=False) -> bytes:
    out = ALIGN.get(align, ALIGN["left"])
    if big:
        out += BIG_ON
    if bold:
        out += BOLD_ON
    out += str(text).encode(CODEPAGE, "replace")
    if bold:
        out += BOLD_OFF
    if big:
        out += BIG_OFF
    return out + b"\n"


def wrap(text, cols) -> list:
    """`text` broken onto lines of at most `cols` characters, on spaces where it can be and mid-word
    where a single word is longer than the roll is wide. A line the printer would silently cut off is
    a line the customer cannot read, so nothing is ever handed over too long."""
    out = []
    for para in str(text or "").replace("\r", "").split("\n"):
        words, line = para.split(), ""
        if not words:
            out.append("")
            continue
        for w in words:
            while len(w) > cols:
                if line:
                    out.append(line)
                    line = ""
                out.append(w[:cols])
                w = w[cols:]
            if not line:
                line = w
            elif len(line) + 1 + len(w) <= cols:
                line += " " + w
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
    return out


def two_col(left, right, cols) -> str:
    """A label on the left and its figure hard against the right edge — how every till slip in the
    world prints a total. The label gives way when the two cannot both fit."""
    left, right = str(left), str(right)
    room = cols - len(right) - 1
    if room < 1:
        return right[-cols:]
    if len(left) > room:
        left = left[:room]
    return left + " " * (cols - len(left) - len(right)) + right


def _money(v) -> str:
    return f"{float(v or 0):,.2f}"


def lines(model) -> list:
    """The slip as the rows of characters that will print, in order — the readable half of this
    module, and what the proof harness asserts against."""
    cols = _cols(model)
    out = []
    for i, ln in enumerate(model["head_lines"]):
        for row in wrap(ln["text"], cols):
            out.append({"text": row, "align": "center",
                        "bold": bool(ln.get("strong")), "big": bool(ln.get("strong")) and i == 0})
    out.append({"text": "-" * cols, "align": "left"})
    for it in model["items"]:
        for row in wrap(it["description"], cols):
            out.append({"text": row, "align": "left", "bold": True})
        if it["serial"]:
            for row in wrap(f'S/N: {it["serial"]}', cols):
                out.append({"text": row, "align": "left"})
        less = f' -{_money(it["less"])}' if it["less"] else ""
        out.append({"text": two_col(f'{it["qty"]} x {_money(it["unit_price"])}{less}',
                                    _money(it["line_total"]), cols), "align": "left"})
    out.append({"text": "-" * cols, "align": "left"})
    for row in model["totals"]:
        figure = ("-" if row.get("negative") else "") + _money(row["value"])
        out.append({"text": two_col(row["label"], figure, cols), "align": "left",
                    "bold": bool(row.get("grand")), "big": bool(row.get("grand"))})
    out.append({"text": "-" * cols, "align": "left"})
    for row in model["paid"]:
        out.append({"text": two_col(row["label"], _money(row["value"]), cols), "align": "left"})
    if model["footer"]:
        out.append({"text": "-" * cols, "align": "left"})
        for row in wrap(model["footer"], cols):
            out.append({"text": row, "align": "center"})
    for block in model["blocks"]:
        out.append({"text": "-" * cols, "align": "left"})
        for row in wrap(block["text"], cols):
            out.append({"text": row, "align": "left"})
        if block["signature"]:
            out.append({"text": "", "align": "left"})
            out.append({"text": "X " + "_" * (cols - 4), "align": "left"})
            out.append({"text": "Customer signature", "align": "left"})
    return out


def _cols(model) -> int:
    from . import sale_receipt as _sr
    return _sr.PAPER_COLS.get(model.get("paper_width_mm"), _sr.PAPER_COLS[_sr.DEFAULT_PAPER_MM])


def payload(model) -> bytes:
    """The whole byte stream for one slip: reset, the lines, feed and cut."""
    out = INIT
    for ln in lines(model):
        out += _line(ln["text"], align=ln.get("align", "left"),
                     bold=ln.get("bold", False), big=ln.get("big", False))
    return out + FEED_CUT


def payload_base64(model) -> str:
    """The same bytes as text, because the browser carries them to the till helper over JSON and
    never looks inside: the register is a courier here, not a renderer."""
    return base64.b64encode(payload(model)).decode("ascii")
