"""Wait for the GitHub Pages deploy of a commit; if GitHub fails or cancels it (e.g. no runner available),
re-publish once by committing a fresh copy of site/index.html, which starts a new deploy.
Uses only the public GitHub API (no token); 1 retry max so it can't loop."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime

REPO = "davglo/fantasy-hockey"
POLL_SECONDS, MAX_WAIT = 60, 20 * 60


def log(msg: str) -> None:
    print("%s ensure_deploy: %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def run_for(sha: str):
    out = subprocess.run(["curl", "-sS", "-m", "20", "https://api.github.com/repos/%s/actions/runs?per_page=15" % REPO],
                         capture_output=True, text=True).stdout
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except ValueError:
        return None
    return next((r for r in runs if r["head_sha"] == sha), None)


def wait(sha: str):
    deadline = time.time() + MAX_WAIT
    while time.time() < deadline:
        r = run_for(sha)
        if r and r["status"] == "completed":
            return r["conclusion"]
        time.sleep(POLL_SECONDS)
    return "timed_out_waiting"


def push_with_retry() -> bool:
    for _ in range(3):
        if subprocess.run(["git", "push", "-q"]).returncode == 0:
            return True
        log("push rejected; pulling and retrying")
        subprocess.run(["git", "pull", "--rebase", "--autostash", "-q"])
        time.sleep(5)
    return False


def main() -> int:
    sha = git("rev-parse", "HEAD")
    result = wait(sha)
    log("deploy of %s: %s" % (sha[:7], result))
    if result == "success":
        return 0
    with open("site/index.html", "a") as f:   # harmless comment = new content = new deploy
        f.write("\n<!-- redeploy %s -->\n" % datetime.now().isoformat(timespec="seconds"))
    git("add", "site/index.html")
    git("commit", "-qm", "Redeploy after failed Pages run (%s)" % result)
    if not push_with_retry():
        log("redeploy push failed")
        return 1
    result = wait(git("rev-parse", "HEAD"))
    log("redeploy: %s" % result)
    return 0 if result == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
