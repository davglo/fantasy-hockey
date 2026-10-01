#!/bin/bash
# Twice daily (launchd, 12:00 PM + 5:00 PM ET; 5 PM catches confirmed goalie starts): pull, rebuild dashboard + lineup check, publish the page.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
git pull --rebase --autostash -q || echo "git pull failed; building from local copy"
/usr/bin/python3 build.py --lineup-check || exit 1
mkdir -p site && cp output/index.html site/index.html
git add site/index.html
if ! git diff --cached --quiet; then
  git commit -qm "Daily build $(date +%F)" && git push -q || echo "push failed; page not published"
fi
