"""--draft-live: poll ESPN draft picks, alert on my turn, warn on stale feed, rebuild the board."""
from __future__ import annotations

import logging
import subprocess
import time

from fh import board, config, draft
from fh.espn import FetchError, League, parse_league

log = logging.getLogger(__name__)

UNREACHABLE_AFTER = 20   # seconds without a successful fetch
LAG_GRACE = 30           # seconds past the pick timer before we call the feed stale


def notify(title: str, msg: str) -> None:
    print("\a", end="", flush=True)
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s" sound name "Glass"'
                        % (msg.replace('"', ""), title.replace('"', ""))], timeout=3, capture_output=True)
    except Exception:  # noqa: BLE001 - notification is best-effort
        pass


class LiveDraft:
    def __init__(self, players: list, repl: dict, swid: str, resolve=None, clock=time.time,
                 alert=notify, out_path=None, refresh: int = 10):
        self.players = players
        self.by_id = {p.id: p for p in players}
        self.repl = repl
        self.swid = swid
        self.resolve = resolve        # callable(ids) -> [Player] for picks outside our pool
        self.clock = clock
        self.alert = alert
        self.out_path = out_path or (config.OUTPUT / "draft_board.html")
        self.refresh = refresh
        self.league: League | None = None
        self.seen: dict = {}          # playerId -> (overall, teamId)
        self.last_ok = None
        self.last_pick_at = None
        self.last_alert_key = None
        self.status: dict = {}

    # ---- one poll -------------------------------------------------------
    def on_fetch_error(self, err: Exception) -> None:
        now = self.clock()
        if self.last_ok is None or now - self.last_ok > UNREACHABLE_AFTER:
            self.status["stale"] = "ESPN feed unreachable for %ss (%s) - watch the draft room directly." % (
                int(now - (self.last_ok or now)), err)

    def update(self, raw: dict) -> list:
        """Process one API response. Returns human-readable event lines."""
        now = self.clock()
        self.last_ok = now
        lg = parse_league(raw, self.swid)
        order_changed = self.league is not None and lg.pick_teams != self.league.pick_teams
        self.league = lg
        events = []
        if order_changed:
            events.append("Draft order changed. My picks now: %s" % ", ".join(map(str, lg.my_picks[:8])))
        new = [(ov, t, pid) for ov, t, pid in lg.picks if pid not in self.seen]
        missing = [pid for _, _, pid in new if pid not in self.by_id]
        if missing and self.resolve:
            try:
                for p in self.resolve(missing):
                    self.by_id[p.id] = p
                    self.players.append(p)
            except FetchError as e:
                log.warning("could not resolve player ids %s: %s", missing, e)
        for ov, t, pid in sorted(new):
            self.seen[pid] = (ov, t)
            p = self.by_id.get(pid)
            who = "%s (%s, VOR %+.0f)" % (p.name, p.group, p.vor) if p else "player #%d" % pid
            tag = " <- ME" if t == lg.my_team_id else ""
            events.append("#%d R%d %s: %s%s" % (ov, (ov - 1) // lg.size + 1, lg.teams.get(t, t), who, tag))
        if new or self.last_pick_at is None:
            self.last_pick_at = now
        self.status.pop("stale", None)
        self._check_lag(now)
        events += self._check_turn()
        return events

    @property
    def now_pick(self) -> int:
        return len(self.league.picks) + 1 if self.league else 1

    def _check_lag(self, now: float) -> None:
        lg = self.league
        if lg.drafted:
            return
        if lg.in_progress and now - self.last_pick_at > lg.time_per_pick + LAG_GRACE:
            self.status["stale"] = ("No new pick in %ds (timer is %ds) - ESPN feed may be lagging; trust the draft room."
                                    % (now - self.last_pick_at, lg.time_per_pick))
        elif (not lg.in_progress and not lg.picks and lg.draft_time
              and now > lg.draft_time.timestamp() + 120):
            self.status["stale"] = "Draft start time passed but feed says not started - check the draft room."

    def _check_turn(self) -> list:
        lg = self.league
        cur = self.now_pick
        upcoming = [p for p in lg.my_picks if p >= cur]
        self.status.pop("alert", None)
        if lg.drafted or not upcoming:
            self.status.update(alert="Draft complete." if lg.drafted else "", level="info")
            return []
        away = upcoming[0] - cur
        if away == 0:
            key, msg, level = ("clock", cur), "ON THE CLOCK - pick %d" % cur, "clock"
        elif away <= 2:
            key, msg, level = ("soon", upcoming[0]), "%d pick%s until you (pick %d)" % (away, "s" * (away > 1), upcoming[0]), "soon"
        else:
            return []
        self.status.update(alert=msg, level=level)
        if key == self.last_alert_key:
            return []
        self.last_alert_key = key
        if lg.in_progress or lg.picks:
            self.alert(lg.name, msg)
        mine = [self.by_id[pid] for pid, (_, t) in self.seen.items() if t == lg.my_team_id and pid in self.by_id]
        avail = [p for p in self.players if p.id not in self.seen]
        recs = draft.recommend(avail, mine, lg, cur, n=5)
        lines = [">>> " + msg]
        for i, r in enumerate(recs, 1):
            lines.append("    %d. %-24s %s  FP %.0f  VOR %+.0f  drop %+.0f  back %d%%"
                         % (i, r.player.name, r.player.group, r.player.fp, r.player.vor, r.dropoff, round(r.p_next * 100)))
        return lines

    def write_board(self) -> None:
        html_s = board.render(self.league, self.players, self.seen, self.now_pick, self.repl,
                              status=self.status, refresh=self.refresh)
        self.out_path.parent.mkdir(exist_ok=True)
        tmp = self.out_path.with_suffix(".tmp")
        tmp.write_text(html_s)
        tmp.replace(self.out_path)


def run(live: LiveDraft, fetch, interval: float = config.POLL_SECONDS, max_polls: int | None = None) -> None:
    polls, last_stale = 0, None
    while True:
        try:
            events = live.update(fetch())
            for e in events:
                log.info(e)
        except FetchError as e:
            live.on_fetch_error(e)
        stale = live.status.get("stale")
        if stale and stale[:20] != (last_stale or "")[:20]:  # log once per kind, not every poll
            log.warning(stale)
        last_stale = stale
        if live.league:
            live.write_board()
            if live.league.drafted:
                log.info("Draft complete. Board: %s", live.out_path)
                return
        polls += 1
        if max_polls and polls >= max_polls:
            return
        time.sleep(interval)
