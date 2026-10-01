"""Player news from ESPN's fantasy feed (Rotowire blurbs that cite the reporting source).

Public-page rules: show the one-line summary, credit Rotowire/ESPN and the cited reporter, and link to the
player's ESPN news page. Never republish the full story text (it's only parsed, for keywords and names).
ESPN has no league-wide feed, so we crawl a bounded watch set chosen by `watch_set`."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fh import config, espn

log = logging.getLogger(__name__)

FEED = "https://site.api.espn.com/apis/fantasy/v2/games/fhl/news/players?limit=%d&playerId=%d"
PLAYER_PAGE = "https://www.espn.com/nhl/player/news/_/id/%d"

INSIDERS = ["Elliotte Friedman", "Pierre LeBrun", "Darren Dreger", "Chris Johnston", "Frank Seravalli",
            "Kevin Weekes", "Jeff Marek", "Nick Kypreos", "David Pagnotta", "Bob McKenzie"]

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
    "role": r"\b(promoted|bump(?:ed)? up|moved (?:up )?to the (?:top|first)|top[- ]power[- ]play unit|PP1|"
            r"first power[- ]play unit|will (?:skate|center|play) on the (?:top|first) line|top-six role)",
    "starting": r"(slated to start|will start|gets? the nod|expected to start|between the pipes|guard the (?:cage|goal|net)|"
                r"defend the (?:home |road )?(?:crease|net|goal|cage)|draw the start|confirmed starter|tend the (?:twine|crease))",
}
NEGATIVE = {"injury", "suspension", "season_over", "trade", "demotion"}
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


def top(items: list, now: datetime, n: int, max_age_h: float = 72) -> list:
    fresh = [i for i in items if i.age_hours(now) <= max_age_h and (i.kinds or i.insider)]
    return sorted(fresh, key=lambda i: -relevance(i, now))[:n]


def starting_today(items: list, now: datetime) -> bool:
    """A 'slated to start' style blurb in the last 20 hours."""
    return any("starting" in i.kinds and i.age_hours(now) <= 20 for i in items)


# ---- watch set + opportunity alerts ------------------------------------------

def starting_goalies(players: list) -> dict:
    """NHL team -> goalie with the most projected games (our best guess at the starter)."""
    out = {}
    for p in players:
        if p.group == "G" and (p.team not in out or (p.base.gp or 0) > (out[p.team].base.gp or 0)):
            out[p.team] = p
    return out


def load_snapshot() -> dict:
    try:
        return json.loads((config.STATE / "status_snapshot.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_snapshot(players: list) -> None:
    (config.STATE / "status_snapshot.json").write_text(json.dumps({str(p.id): p.injury for p in players}))


def watch_set(mine: list, rostered: list, fas: list, add_targets: list, goalie_cands: list) -> dict:
    """Who to crawl: my roster, my add targets + stream goalies, every team's starting goalie, any rostered
    player whose ESPN status changed since last build or isn't ACTIVE, and FAs whose ownership is spiking."""
    snap = load_snapshot()
    ws = {p.id: p.name for p in list(mine) + list(add_targets) + list(goalie_cands)}
    ws.update({p.id: p.name for p in starting_goalies(rostered + fas).values()})
    ws.update({p.id: p.name for p in rostered
               if p.injury != "ACTIVE" or snap.get(str(p.id), p.injury) != p.injury})
    ws.update({p.id: p.name for p in fas if p.pct_change >= 5})
    return ws


@dataclass
class Opportunity:
    item: Item
    about: object         # the player the news is about
    beneficiaries: list   # FAs who gain


def opportunities(news: dict, by_id: dict, fas: list, now: datetime, max_age_h: float = 96) -> list:
    """Negative news about a non-FA player -> FAs who stand to gain: FAs named in the blurb first, then FAs on
    the same NHL team in the same group (for goalies: that team's FA backup)."""
    fa_ids = {p.id for p in fas}
    out = []
    for pid, items in news.items():
        p = by_id.get(pid)
        if p is None or pid in fa_ids:
            continue
        for it in items:
            if it.age_hours(now) > max_age_h or not (it.kinds & NEGATIVE) or "return" in it.kinds:
                continue
            named = [f for f in fas if f.name in it.text or (len(f.name.split()) > 1 and
                     re.search(r"\b%s\b" % re.escape(f.name.split()[-1]), it.text) and f.team == p.team)]
            same = sorted((f for f in fas if f.team == p.team and f.group == p.group and f not in named),
                          key=lambda f: -f.exp_game())[:1 if p.group == "G" else 2]
            bens = named + same
            if bens:
                out.append(Opportunity(item=it, about=p, beneficiaries=bens))
            break   # newest qualifying item per player
    return sorted(out, key=lambda o: -relevance(o.item, now))


def credit(it: Item) -> str:
    src = "Rotowire via ESPN"
    if it.insider:
        return "%s, citing %s" % (src, it.insider)
    if it.reporter:
        return "%s, citing %s" % (src, it.reporter)
    return src


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
