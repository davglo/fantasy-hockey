from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUTPUT = ROOT / "output"
ENV_FILE = ROOT / ".env"
RANKINGS_XLSX = ROOT / "2026-27-Fantasy-Projections-Yahoo-3.xlsx"

GAME = "fhl"
SEASON = 2027
LEAGUE_ID = 1366258109

API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/%s/seasons/%d" % (GAME, SEASON)
LEAGUE_URL = API + "/segments/0/leagues/%d" % LEAGUE_ID
POOL_URL = API + "/segments/0/leaguedefaults/1"

# ESPN hockey lineup slot IDs (verified against eligibleSlots in the player pool).
SLOT_NAMES = {0: "C", 1: "LW", 2: "RW", 3: "F", 4: "D", 5: "G", 6: "UTIL", 7: "BE", 8: "IR"}

# ESPN hockey stat IDs (verified against NHL API 2025-26 actuals).
STAT_NAMES = {
    0: "GS", 1: "W", 2: "L", 3: "SA", 4: "GA", 6: "SV", 7: "SO", 9: "OTL", 10: "GAA", 11: "SV%",
    13: "G", 14: "A", 15: "+/-", 16: "PTS", 17: "PIM", 18: "PPG", 19: "PPA", 20: "SHG", 21: "SHA",
    22: "GWG", 23: "FOW", 24: "FOL", 28: "HAT", 29: "SOG", 30: "GP", 31: "HIT", 32: "BLK",
    34: "GP", 38: "PPP", 39: "SHP",
}

POLL_SECONDS = 5

# Scoring changes agreed in the league but not yet reflected in ESPN settings (statId -> points).
# Harmless once ESPN matches. 2026-09-27: saves cut from 0.2 to 0.1.
SCORING_OVERRIDES = {6: 0.1}

# League rules not (or not reliably) in ESPN settings. 2026-09-28: Dave confirmed 6 adds per matchup.
ADDS_PER_MATCHUP = 6

# Playoff-week points count this many times a regular-season point when valuing moves.
PLAYOFF_WEIGHT = 2.0
