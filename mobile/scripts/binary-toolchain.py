#!/usr/bin/env python3
"""What actually built an iOS binary — read out of the binary, never out of the config.

The crash that cost the owner three days (index §CORE-29.3c) was a toolchain that moved while
nothing recorded that it had: ``mobile/eas.json`` asked for ``"image": "latest"`` and no build this
repository ever made said what "latest" was that day. Build 14 was made with Xcode 27, and UIKit
killed it inside ``UIApplicationMain`` before a line of JavaScript ran.

A pin in eas.json is a REQUEST. ``DTXcode`` in the built ``Info.plist`` is the ANSWER. Only the
answer is evidence, so this reads the answer.

Usage::

    python3 scripts/binary-toolchain.py <unpacked-ipa-dir>     # prints key=value lines
    python3 scripts/binary-toolchain.py <dir> --expect-xcode 26

Exits non-zero, naming the mismatch, when the binary was built with a different Xcode major than
the one asked for. Stdlib only: ``plistlib`` reads Apple's binary plists, which is why this is
Python and not part of the app's own JavaScript.
"""

from __future__ import annotations

import argparse
import glob
import plistlib
import sys

# Read out of every binary, because a mismatch is only ever visible by comparing numbers.
FIELDS = (
    "DTXcode",
    "DTXcodeBuild",
    "DTSDKName",
    "DTPlatformVersion",
    "MinimumOSVersion",
    "CFBundleVersion",
    "CFBundleShortVersionString",
)


def find_info_plist(root: str) -> str:
    """The app's own Info.plist inside an unpacked .ipa."""
    paths = sorted(glob.glob(f"{root}/Payload/*.app/Info.plist"))
    if not paths:
        raise FileNotFoundError(f"no Payload/*.app/Info.plist under {root!r}")
    return paths[0]


def read_toolchain(plist_path: str) -> dict[str, str]:
    with open(plist_path, "rb") as fh:
        info = plistlib.load(fh)
    return {key: str(info.get(key, "")) for key in FIELDS}


def xcode_major(dtxcode: str) -> str:
    """``DTXcode`` is the version with its dot removed: Xcode 26.2 is ``2620``, 27.0 is ``2700``.

    The major is its leading digits less the last two, which keeps working when Apple reaches a
    three-digit major. Returns "" when the value is not a number this rule can read, so a caller
    reports an unreadable binary rather than silently comparing against nothing.
    """
    digits = dtxcode.strip()
    if not digits.isdigit() or len(digits) < 3:
        return ""
    return digits[:-2]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="directory holding the unpacked .ipa (contains Payload/)")
    ap.add_argument("--expect-xcode", default="",
                    help="Xcode major the build asked for; mismatch exits 1")
    args = ap.parse_args(argv)

    facts = read_toolchain(find_info_plist(args.root))
    for key, value in facts.items():
        print(f"{key}={value}")

    major = xcode_major(facts["DTXcode"])
    print(f"XcodeMajor={major}")

    want = args.expect_xcode.strip()
    if not want:
        return 0
    if not major:
        print(f"ERROR: DTXcode is {facts['DTXcode']!r}, which names no readable Xcode version, so "
              f"this binary cannot be checked against the pinned Xcode {want}.", file=sys.stderr)
        return 1
    if major != want:
        print(f"ERROR: this binary was built with Xcode {major} (DTXcode {facts['DTXcode']}, "
              f"{facts['DTSDKName']}) but mobile/eas.json pinned Xcode {want}. An app built with "
              f"the iOS 27 SDK is killed at launch by UIKit until it adopts the UIScene lifecycle, "
              f"which Expo SDK 52 does not — see index CORE-29.3c.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
