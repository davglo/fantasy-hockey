"""Trade rumors and reports from Yahoo Sports' public NHL RSS feed.

Only headlines are kept and shown, credited to Yahoo Sports with a link to the article - never article text.
The feed holds ~1 day of items, so matches are archived (14 days) to keep a running saga visible."""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from fh import config, espn

log = logging.getLogger(__name__)

FEED = "https://sports.yahoo.com/nhl/rss.xml"
ARCHIVE = config.STATE / "rumors_archive.json"
KEEP_DAYS = 14
RUMOR = re.compile(r"\b(trade|rumou?r|rumblings|pitch|deal|suitor|interest|request|saga|market|insider|"
                   r"sign|extension|waiv|injur|out|return|surgery|suspen)\w*", re.I)


def _fetch_feed() -> list:
    def get():
        r = espn._curl_text(FEED)
        items = []
        for block in re.findall(r"<item>(.*?)</item>", r, re.S):
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S)
            link = re.search(r"<link>(.*?)</link>", block, re.S)
            date = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            if title and link and date:
                items.append({"title": html.unescape(title.group(1).strip()), "link": link.group(1).strip(),
                              "date": parsedate_to_datetime(date.group(1).strip()).astimezone(timezone.utc).isoformat()})
        return {"items": items}
    try:
        return espn.cached("yahoo_nhl_rss", 3600, get)["items"]
    except espn.FetchError as e:
        log.info("Yahoo NHL feed unavailable (%s)", e)
        return []


def _mentions(title: str, name: str, last_counts: dict) -> bool:
    """Full name always matches. A bare last name only matches when no other known NHL player shares it
    (Hellebuyck: yes; Hughes: Jack/Quinn/Luke - only 'Jack Hughes' counts) and it isn't preceded by a
    different first name or initials ('TJ Hughes', 'T.J. Hughes')."""
    if name in title:
        return True
    first, last = name.split()[0], name.split()[-1]
    if last_counts.get(last, 0) > 1:
        return False
    for m in re.finditer(r"(?:\b([A-Z][\w.'-]*)\s+)?\b%s\b" % re.escape(last), title):
        before = (m.group(1) or "").replace(".", "")
        if before and before != first and (len(before) <= 3 and before.isupper()):
            continue    # initials of a different player
        return True
    return False


def refresh(watch: list, known_names: list) -> list:
    """watch: player names; known_names: every NHL player we know (for last-name collisions).
    Returns archived rumor/report headlines about watched players, newest first."""
    last_counts = {}
    for n in set(known_names) | set(watch):
        last_counts[n.split()[-1]] = last_counts.get(n.split()[-1], 0) + 1
    try:
        archive = json.loads(ARCHIVE.read_text())
    except (FileNotFoundError, ValueError):
        archive = []
    seen = {a["link"] for a in archive}
    for it in _fetch_feed():
        if it["link"] in seen or not RUMOR.search(it["title"]):
            continue
        names = [n for n in watch if _mentions(it["title"], n, last_counts)]
        if names:
            archive.append(dict(it, players=names))
            seen.add(it["link"])
    for a in archive:   # re-check old matches under the current rules (fixes earlier false matches)
        a["players"] = [n for n in a["players"] if _mentions(a["title"], n, last_counts)]
    archive = [a for a in archive if a["players"] or a["link"] in seen]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    archive = sorted((a for a in archive if a["date"] >= cutoff), key=lambda a: a["date"], reverse=True)
    config.STATE.mkdir(exist_ok=True)
    ARCHIVE.write_text(json.dumps(archive, indent=1))
    return [a for a in archive if any(n in watch for n in a["players"])]
