#!/bin/bash
# Twice daily (launchd, 8:00 AM + 5:00 PM ET; 5 PM catches confirmed goalie starts): pull, rebuild dashboard + lineup check, publish the page.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
git pull --rebase --autostash -q || echo "git pull failed; building from local copy"
/usr/bin/python3 build.py --lineup-check || exit 1
mkdir -p site && cp output/index.html site/index.html
git add site/index.html
if ! git diff --cached --quiet; then
  git commit -qm "Daily build $(date +%F)"
  pushed=0
  for attempt in 1 2 3; do   # another session may have pushed meanwhile: rebase onto it and retry
    if git push -q; then pushed=1; break; fi
    echo "push rejected (attempt $attempt); pulling and retrying"
    git pull --rebase --autostash -q; sleep 5
  done
  if [ "$pushed" = 1 ]; then
    /usr/bin/python3 scripts/ensure_deploy.py   # re-publish once if GitHub fails the Pages deploy
  else
    echo "push failed after 3 attempts; page not published"
  fi
fi
