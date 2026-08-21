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
echo "No placeholders left."
echo ""
echo "Reminder — the checks a script cannot do for you:"
echo "  1. Has a lawyer in your state reviewed these documents?"
echo "  2. Is the DMCA agent registered with the U.S. Copyright Office (not just named on the page)?"
echo "  3. Do the entity name and address match your actual corporate filings?"
echo "  4. Does the trial length on the site match Admin -> Pricing & Free Trial?"
exit 0
