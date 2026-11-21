"""Player news from ESPN's fantasy feed (Rotowire blurbs that cite the reporting source).

Public-page rules: show the one-line summary, credit Rotowire/ESPN and the cited reporter, and link to the
player's ESPN news page. Never republish the full story text (it's only parsed, for keywords and names).
ESPN's fantasy feed is per player, so we crawl a bounded watch set chosen by `watch_set`; league headlines
come from ESPN's NHL news feed (headline + link only)."""
from __future__ import annotations

import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from fh import espn

log = logging.getLogger(__name__)

FEED = "https://site.api.espn.com/apis/fantasy/v2/games/fhl/news/players?limit=%d&playerId=%d"
PLAYER_PAGE = "https://www.espn.com/nhl/player/news/_/id/%d"

INSIDERS = ["Elliotte Friedman", "Pierre LeBrun", "Darren Dreger", "Chris Johnston", "Frank Seravalli",
            "Kevin Weekes", "Jeff Marek", "Nick Kypreos", "David Pagnotta", "Bob McKenzie"]

_SIDE = r"(?:home |road |visiting )?"
START_CONFIRMED = (r"(slated to start|set to start|will start|gets? the (?:nod|start|call)|draw the start|confirmed (?:as the )?starter|"
                   r"between the %spipes|(?:guard|defend|patrol|protect|man) the %s(?:cage|goal|net|crease|blue paint)|"
                   r"tend the %s(?:twine|crease|goal|net)|will be in (?:goal|net)|in net (?:for|against)|start in goal)"
                   % (_SIDE, _SIDE, _SIDE))
START_EXPECTED = r"(expected to (?:start|be in goal|get the (?:nod|start)|guard|defend|tend)|likely to start|projected to start|in line to start)"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

KINDS = {
    "injury": r"\b(injur\w*|out (?:for|indefinitely|week|month)|week-to-week|month-to-month|day-to-day|"
              r"surgery|sidelined|IR\b|injured reserve|LTIR|concussion|upper-body|lower-body|won't play|will miss)",
    "suspension": r"\bsuspen\w*",
    "season_over": r"(out for the (?:rest of the )?season|season-ending|"
                   r"miss (?:the )?(?:rest|remainder) of the (?:regular )?season|out for the year)",
    "trade": r"\b(traded|acquired|trade request|requested a trade|dealt to)\b",
    "demotion": r"\b(scratch\w*|demot\w*|assigned to|sent down|waived|placed on waivers|fourth line|press box)",
    "return": r"\b(activated|cleared to (?:play|return)|back in the lineup|will return|returns? to (?:the )?lineup|"
              r"returned to (?:action|the lineup)|reinstated|rejoin\w* the (?:team|lineup)|practic\w* in full|"
              r"set to return|expected to return (?:tonight|Thursday|Friday|Saturday|Sunday|Monday|Tuesday|Wednesday))",
    "role": r"\b(promoted|bump(?:ed)? up|moved (?:up )?to the (?:top|first|second)|top[- ]power[- ]play unit|PP1|"
            r"first power[- ]play unit|(?:top|first|second)[- ]line|top-six|top six|top pairing|first pairing|"
            r"recalled|called up|named (?:the )?starter|starting (?:job|role)|No\. 1 (?:goalie|netminder)|"
            r"expanded role|bigger role|increased (?:role|ice time)|(?:quarterback|run) the (?:top|first) unit)",
    "starting": START_CONFIRMED + "|" + START_EXPECTED,
}
BIG = {"injury", "suspension", "season_over", "trade", "demotion", "return", "role"}   # kinds worth a league-news slot
REPORTER = re.compile(r"((?:[A-Z][\w'.-]+ ){1,2}[A-Z][\w'.-]+) of (?:the )?([A-Z][\w.'&-]+(?: [A-Z][\w.'&-]+)*)")


@dataclass
class Item:
    player_id: int
    player: str
    published: datetime
    summary: str          # one-line description (safe to show)
    text: str             # summary + story, for parsing only
    reporter: str = ""
    insider: str = ""
    kinds: set = field(default_factory=set)

    @property
    def link(self) -> str:
        return PLAYER_PAGE % self.player_id

    def age_hours(self, now: datetime) -> float:
        return (now - self.published).total_seconds() / 3600


def parse(raw: dict, pid: int, name: str) -> Item | None:
    if raw.get("type") != "Rotowire":
        return None      # ESPN feature stories: long-form, not news
    summary = (raw.get("description") or raw.get("headline") or "").strip()
    text = summary + " " + (raw.get("story") or "")
    try:
        pub = datetime.strptime(raw.get("published", "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    it = Item(player_id=pid, player=name, published=pub, summary=summary, text=text)
    m = REPORTER.search(summary)
    if m:
        it.reporter = "%s (%s)" % (m.group(1), m.group(2))
    it.insider = next((n for n in INSIDERS if n in text), "")
    # Tag from the summary sentence only: the story often recaps old injuries or speculates.
    it.kinds = {k for k, rx in KINDS.items() if re.search(rx, summary, re.I)}
    return it


def fetch(pid: int, name: str, limit: int = 6) -> list:
    try:
        raw = espn.cached("news_%d" % pid, 3600, lambda: espn._curl(FEED % (limit, pid), timeout=10))
    except espn.FetchError as e:
        log.info("news unavailable for %s (%s)", name, e)
        return []
    return [i for i in (parse(r, pid, name) for r in raw.get("feed", [])) if i]


def fetch_many(players: dict, threads: int = 8) -> dict:
    """players: id -> name. Returns id -> [Item] newest first."""
    with ThreadPoolExecutor(threads) as ex:
        res = dict(zip(players, ex.map(lambda kv: fetch(*kv), players.items())))
    return {pid: sorted(items, key=lambda i: i.published, reverse=True) for pid, items in res.items()}


def relevance(it: Item, now: datetime) -> float:
    """Higher = more fantasy-relevant. Recency decays over ~3 days."""
    w = {"season_over": 6, "suspension": 4, "trade": 4, "injury": 4, "return": 3, "starting": 2, "role": 2, "demotion": 2}
    base = max([w.get(k, 0) for k in it.kinds] + [0.5])
    if it.insider:
        base += 3
    return base * max(0.1, 1 - it.age_hours(now) / 72)


MAX_AGE_H = 48   # nothing stays on the board longer than 2 days (Dave, 2026-10-04)


def newsworthy(i: Item) -> bool:
    """Goalie start notes feed the start-status column, not the news list."""
    return bool(i.kinds - {"starting"}) or bool(i.insider)


def top(items: list, now: datetime, n: int, max_age_h: float = MAX_AGE_H) -> list:
    fresh = [i for i in items if i.age_hours(now) <= max_age_h and newsworthy(i)]
    return sorted(fresh, key=lambda i: -relevance(i, now))[:n]


def start_status(items: list, game_day) -> str:
    """'confirmed' / 'expected' / '' for a goalie on game_day (a date, ET). A start note counts when it names
    that weekday (teams often announce the day before) or, with no weekday named, was posted on game day."""
    from fh.config import ET
    best = ""
    for i in items:
        if "starting" not in i.kinds:
            continue
        posted = i.published.astimezone(ET).date()
        if not (0 <= (game_day - posted).days <= 2):
            continue
        days = [d for d in WEEKDAYS if re.search(r"\b%s\b" % d, i.summary)]
        if days:
            if WEEKDAYS[game_day.weekday()] not in days:
                continue
        elif posted != game_day:
            continue    # no weekday named ("tonight", "Thursday's game" omitted): only same-day notes count
        if "preseason" in i.summary.lower():
            continue
        if re.search(START_CONFIRMED, i.summary, re.I):
            return "confirmed"
        if re.search(START_EXPECTED, i.summary, re.I):
            best = "expected"
    return best


# ---- watch set + opportunity alerts ------------------------------------------

def starting_goalies(players: list) -> dict:
    """NHL team -> goalie with the most projected games (our best guess at the starter)."""
    out = {}
    for p in players:
        if p.group == "G" and (p.team not in out or (p.base.gp or 0) > (out[p.team].base.gp or 0)):
            out[p.team] = p
    return out



def watch_set(mine: list, rostered: list, fas: list, add_targets: list, goalie_cands: list, top_fas: int = 40) -> dict:
    """Who to crawl: my roster, my add targets + stream goalies, every team's starting goalie, every player
    rostered in this league, FAs whose ownership is spiking, and the top free agents by projection."""
    ws = {p.id: p.name for p in list(mine) + list(add_targets) + list(goalie_cands)}
    ws.update({p.id: p.name for p in sorted(fas, key=lambda p: -p.exp_game())[:top_fas]})
    ws.update({p.id: p.name for p in starting_goalies(rostered + fas).values()})
    ws.update({p.id: p.name for p in rostered})   # everyone rostered in this league: the league-news section covers them all
    ws.update({p.id: p.name for p in fas if p.pct_change >= 5})
    return ws


def credit(it: Item) -> str:
    src = "Rotowire via ESPN"
    if it.insider:
        return "%s, citing %s" % (src, it.insider)
    if it.reporter:
        return "%s, citing %s" % (src, it.reporter)
    return src


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


HEADLINES = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news?limit=60"
YAHOO = "https://sports.yahoo.com/nhl/rss.xml"
HEADLINE_KEEP = re.compile(r"\b(trade[sd]?|injur\w*|suspen\w*|surgery|out for|sidelined|waive[sd]?|signs?|extension|fired|hires?|"
                           r"resign\w*|contract|return\w*|call[- ]?up|demot\w*|scratch\w*|hospital\w*|concussion)\b", re.I)
HEADLINE_SKIP = re.compile(r"(power rankings|what to watch|highlights|vs\.|scoring tracker|most |which |all-time|"
                           r"mock draft|odds|picks|predictions?|fantasy hockey|ranking|trade targets|trade assets|can explore)", re.I)


def _yahoo_items() -> list:
    def get():
        items = []
        for block in re.findall(r"<item>(.*?)</item>", espn._curl_text(YAHOO), re.S):
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


def league_headlines(now: datetime, max_age_h: float = 72, n: int = 10) -> list:
    """League-wide NHL headlines (injuries, trades, signings, call-ups, coaching moves) from ESPN's news feed and
    Yahoo Sports' RSS. Headline + link only, credited to the outlet - never article text."""
    out = []
    try:
        espn_items = espn.cached("espn_headlines", 3600, lambda: espn._curl(HEADLINES, timeout=10)).get("articles", [])
    except espn.FetchError as e:
        log.info("ESPN headlines unavailable (%s)", e)
        espn_items = []
    cands = []
    for a in espn_items:
        if a.get("type") not in ("HeadlineNews", "Story") or a.get("premium"):
            continue
        try:
            pub = datetime.strptime(a["published"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        cands.append({"title": (a.get("headline") or "").strip(), "published": pub, "source": "ESPN",
                      "link": ((a.get("links") or {}).get("web") or {}).get("href", "")})
    for y in _yahoo_items():
        cands.append({"title": y["title"], "link": y["link"], "source": "Yahoo Sports",
                      "published": datetime.fromisoformat(y["date"])})
    seen = set()
    for c in sorted(cands, key=lambda x: x["published"], reverse=True):
        t = c["title"]
        if (not t or not c["link"] or HEADLINE_SKIP.search(t) or "podcast" in t.lower() or not HEADLINE_KEEP.search(t)
                or (now - c["published"]).total_seconds() > max_age_h * 3600 or t.lower() in seen):
            continue
        seen.add(t.lower())
        out.append(c)
    return out[:n]


def mine_items(mine: list, nws: dict, now: datetime, n: int = 8) -> list:
    """Newest fantasy-relevant item per player on my roster (injury, role, trade, insider reports), last 48h."""
    best = [top(nws.get(p.id, []), now, 1)[:1] for p in mine]
    return top([i for b in best for i in b], now, n)


def around_items(nws: dict, players: list, my_ids: set, now: datetime, n: int = 12, max_age_h: float = 72) -> list:
    """[(player, item)] for big news (injury, trade, role change...) on players rostered by other teams in this league
    or owned in 25%+ of ESPN leagues. One item per player, most relevant first."""
    out = []
    for p in players:
        if p.id in my_ids or (p.owner is None and p.base.pct_owned < 25):
            continue
        it = next((i for i in nws.get(p.id, []) if i.kinds & BIG and i.age_hours(now) <= max_age_h), None)
        if it:
            out.append((p, it))
    return sorted(out, key=lambda t: -relevance(t[1], now))[:n]
