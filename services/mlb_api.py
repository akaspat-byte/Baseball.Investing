import requests
import logging
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)
BASE_URL = "https://statsapi.mlb.com/api/v1"
CACHE_TTL = 1800  # 30 minutes


class MLBApiService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Baseball.Investing/1.0"
        self._cache = {}

    def _get(self, url, params=None, timeout=12):
        try:
            r = self.session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"MLB API request failed: {e}")
            return None

    def _cache_get(self, key):
        entry = self._cache.get(key)
        if entry and (time.time() - entry["ts"]) < CACHE_TTL:
            return entry["data"]
        return None

    def _cache_set(self, key, data):
        self._cache[key] = {"data": data, "ts": time.time()}

    # -------------------------------------------------------------------------
    # Schedule
    # -------------------------------------------------------------------------

    def get_schedule(self, game_date=None):
        if game_date is None:
            game_date = date.today().strftime("%Y-%m-%d")

        data = self._get(
            f"{BASE_URL}/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "probablePitcher,team",
                "gameType": "R",
            },
        )
        if not data:
            return []

        games = []
        for d in data.get("dates", []):
            for g in d.get("games", []):
                state = g.get("status", {}).get("abstractGameState", "")
                if state in ("Preview", "Live"):
                    games.append(g)
        return games

    # -------------------------------------------------------------------------
    # Standings (cached)
    # -------------------------------------------------------------------------

    def get_standings(self, season=None):
        if season is None:
            season = date.today().year

        cached = self._cache_get(f"standings_{season}")
        if cached is not None:
            return cached

        data = self._get(
            f"{BASE_URL}/standings",
            params={
                "leagueId": "103,104",
                "season": season,
                "standingsTypes": "regularSeason",
                "hydrate": "team,record,streak",
            },
        )

        standings = {}
        if data:
            for record in data.get("records", []):
                for tr in record.get("teamRecords", []):
                    tid = tr.get("team", {}).get("id")
                    if tid:
                        standings[tid] = tr

        self._cache_set(f"standings_{season}", standings)
        return standings

    # -------------------------------------------------------------------------
    # Pitcher stats (cached per player)
    # -------------------------------------------------------------------------

    def get_pitcher_stats(self, pitcher_id, season=None):
        if season is None:
            season = date.today().year

        cached = self._cache_get(f"pitcher_{pitcher_id}_{season}")
        if cached is not None:
            return cached

        data = self._get(
            f"{BASE_URL}/people/{pitcher_id}/stats",
            params={"stats": "season", "group": "pitching", "season": season},
        )

        stats = {}
        if data:
            splits = data.get("stats", [{}])[0].get("splits", [])
            if splits:
                stats = splits[0].get("stat", {})

        self._cache_set(f"pitcher_{pitcher_id}_{season}", stats)
        return stats

    # -------------------------------------------------------------------------
    # Main enriched schedule
    # -------------------------------------------------------------------------

    def get_todays_games_with_stats(self, game_date=None):
        if game_date is None:
            game_date = date.today().strftime("%Y-%m-%d")
        season = int(game_date.split("-")[0])

        games = self.get_schedule(game_date)
        standings = self.get_standings(season)

        # Collect unique pitcher IDs
        pitcher_ids = set()
        for g in games:
            teams = g.get("teams", {})
            for side in ("home", "away"):
                pid = teams.get(side, {}).get("probablePitcher", {}).get("id")
                if pid:
                    pitcher_ids.add(pid)

        # Fetch all pitcher stats in parallel
        pitcher_stats: dict = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(self.get_pitcher_stats, pid, season): pid for pid in pitcher_ids}
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    pitcher_stats[pid] = future.result()
                except Exception:
                    pitcher_stats[pid] = {}

        enriched = []
        for g in games:
            state = g.get("status", {}).get("abstractGameState", "")
            teams = g.get("teams", {})
            home_raw = teams.get("home", {})
            away_raw = teams.get("away", {})

            home_team = home_raw.get("team", {})
            away_team = away_raw.get("team", {})

            home_pitcher = home_raw.get("probablePitcher", {})
            away_pitcher = away_raw.get("probablePitcher", {})

            enriched.append(
                {
                    "game_id": g.get("gamePk"),
                    "game_time_utc": g.get("gameDate"),
                    "status": state,
                    "venue": g.get("venue", {}).get("name", ""),
                    "home": self._build_team(
                        home_team,
                        standings.get(home_team.get("id"), {}),
                        home_pitcher,
                        pitcher_stats.get(home_pitcher.get("id"), {}),
                    ),
                    "away": self._build_team(
                        away_team,
                        standings.get(away_team.get("id"), {}),
                        away_pitcher,
                        pitcher_stats.get(away_pitcher.get("id"), {}),
                    ),
                }
            )

        return {"date": game_date, "season": season, "games": enriched}

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _build_team(self, team, standing, pitcher, pitcher_stats):
        wins = standing.get("wins", 0)
        losses = standing.get("losses", 0)
        games_played = wins + losses

        lr = standing.get("leagueRecord", {})
        win_pct = float(lr.get("pct", "0.500")) if lr.get("pct") else 0.500

        runs_scored = standing.get("runsScored", 0) or 0
        runs_allowed = standing.get("runsAllowed", 0) or 0
        rs_pg = round(runs_scored / max(games_played, 1), 2)
        ra_pg = round(runs_allowed / max(games_played, 1), 2)

        streak = standing.get("streak", {}).get("streakCode", "")

        last_ten = "-"
        split_records = standing.get("records", {}).get("splitRecords", [])
        home_rec = "-"
        away_rec = "-"
        for rec in split_records:
            rtype = rec.get("type", "")
            w, lo = rec.get("wins", "-"), rec.get("losses", "-")
            if rtype == "lastTen":
                last_ten = f"{w}-{lo}"
            elif rtype == "home":
                home_rec = f"{w}-{lo}"
            elif rtype == "away":
                away_rec = f"{w}-{lo}"

        def _safe_float(val):
            try:
                return float(val) if val not in (None, "", "-") else None
            except (TypeError, ValueError):
                return None

        ps = pitcher_stats
        return {
            "id": team.get("id"),
            "name": team.get("name", ""),
            "abbreviation": team.get("abbreviation", ""),
            "wins": wins,
            "losses": losses,
            "games_played": games_played,
            "win_pct": win_pct,
            "runs_scored": runs_scored,
            "runs_allowed": runs_allowed,
            "rs_per_game": rs_pg,
            "ra_per_game": ra_pg,
            "streak": streak,
            "last_ten": last_ten,
            "home_record": home_rec,
            "away_record": away_rec,
            "pitcher": {
                "id": pitcher.get("id"),
                "name": pitcher.get("fullName", "TBD"),
                "era": _safe_float(ps.get("era")),
                "whip": _safe_float(ps.get("whip")),
                "k9": _safe_float(ps.get("strikeoutsPer9Inn")),
                "bb9": _safe_float(ps.get("walksPer9Inn")),
                "innings_pitched": ps.get("inningsPitched", "0.0"),
                "wins": ps.get("wins", 0),
                "losses": ps.get("losses", 0),
                "games_started": ps.get("gamesStarted", 0),
                "strikeouts": ps.get("strikeOuts", 0),
                "opponent_avg": _safe_float(ps.get("avg")),
                "opponent_ops": _safe_float(ps.get("ops")),
            },
        }
