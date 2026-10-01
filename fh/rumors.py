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


def _mentions(title: str, name: str) -> bool:
    if name in title:
        return True
    last = name.split()[-1]
    # Last name alone only when distinctive enough (avoids "Hughes", "Tkachuk" style collisions being too loose)
    return len(last) >= 6 and re.search(r"\b%s\b" % re.escape(last), title) is not None


def refresh(watch: list) -> list:
    """watch: player names. Returns archived rumor/report headlines about them, newest first."""
    try:
        archive = json.loads(ARCHIVE.read_text())
    except (FileNotFoundError, ValueError):
        archive = []
    seen = {a["link"] for a in archive}
    for it in _fetch_feed():
        if it["link"] in seen or not RUMOR.search(it["title"]):
            continue
        names = [n for n in watch if _mentions(it["title"], n)]
        if names:
            archive.append(dict(it, players=names))
            seen.add(it["link"])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    archive = sorted((a for a in archive if a["date"] >= cutoff), key=lambda a: a["date"], reverse=True)
    config.STATE.mkdir(exist_ok=True)
    ARCHIVE.write_text(json.dumps(archive, indent=1))
    return [a for a in archive if any(n in watch for n in a["players"])]
