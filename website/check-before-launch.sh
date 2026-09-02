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

# ── The two stylesheets must agree on their design tokens ────────────────────────────────────
# home.css styles the homepage, styles.css the legal documents and 404. They deliberately carry
# their own copy of the same :root token block rather than sharing a third file, which would cost
# every page an extra request. The cost of a copy is drift — a palette changed in one place and
# not the other, so the legal pages slowly stop matching the site. This compares them.
# POSIX sh only: no process substitution, this script runs under dash in CI.
if [ -f assets/home.css ] && [ -f assets/styles.css ]; then
  _a=$(sed -n '/^:root{/,/^}$/p' assets/home.css   | tr -d ' \t')
  _b=$(sed -n '/^:root{/,/^}$/p' assets/styles.css | tr -d ' \t')
  if [ "$_a" != "$_b" ]; then
    echo "FAIL: the :root token blocks in assets/home.css and assets/styles.css have drifted."
    echo "      They are duplicated on purpose and must stay identical."
    echo "      Copy the :root block from home.css into styles.css and re-run."
    exit 1
  fi
fi

echo ""
echo "Reminder — the checks a script cannot do for you:"
echo "  1. Has a lawyer in your state reviewed these documents?"
echo "  2. Is the DMCA agent registered with the U.S. Copyright Office (not just named on the page)?"
echo "  3. Do the entity name and address match your actual corporate filings?"
echo "  4. Does the trial length on the site match Admin -> Pricing & Free Trial?"
exit 0
