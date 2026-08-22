#!/bin/sh
# Pre-launch gate. Run this from the website/ directory BEFORE uploading:
#
#     sh check-before-launch.sh
#
# It fails if any [[CONFIRM: ...]] placeholder is still in the files. Those are the blanks only you
# can fill — your registered address, governing-law state, effective dates, DMCA agent. Publishing
# legal pages with the placeholders still in them is worse than publishing nothing, because it shows
# a court the documents were never finished.
set -e
hits=$(grep -rn "\[\[CONFIRM" . --include="*.html" --include="*.js" --include="*.txt" --include="*.xml" 2>/dev/null || true)
if [ -n "$hits" ]; then
  echo "NOT READY — unfilled placeholders remain:"
  echo ""
  echo "$hits"
  echo ""
  echo "Fill each one in, then run this again. See DEPLOY.md section 2 for what each answer means."
  exit 1
fi
# ── Effective date ────────────────────────────────────────────────────────────────────────────
# The documents carry an effective date. It should be the day you actually publish — a date in the
# past reads as "these terms were in force before anyone could read them", which is exactly the
# argument you do not want to have. This is a WARNING, not a failure: publishing a day or two after
# the stamp is normal, publishing months later is not.
stamped=$(sed -n 's/.*Effective: \([A-Z][a-z]* [0-9]*, [0-9]*\).*/\1/p' legal/terms.html | head -1)
today=$(date "+%B %-d, %Y" 2>/dev/null || date "+%B %d, %Y")
if [ -n "$stamped" ] && [ "$stamped" != "$today" ]; then
  echo "WARNING — the documents are stamped \"$stamped\" but today is \"$today\"."
  echo "If you are publishing today, re-stamp them first:"
  echo ""
  echo "    cd website/legal && sed -i 's/$stamped/$today/g' *.html"
  echo ""
fi

echo "No placeholders left."
echo ""
echo "Reminder — the checks a script cannot do for you:"
echo "  1. Has a lawyer in your state reviewed these documents?"
echo "  2. Is the DMCA agent registered with the U.S. Copyright Office (not just named on the page)?"
echo "  3. Do the entity name and address match your actual corporate filings?"
echo "  4. Does the trial length on the site match Admin -> Pricing & Free Trial?"
exit 0
