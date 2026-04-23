"""
Betting model using:
  - Pythagorean Win% (Bill James, exponent 1.83) from season RS/RA
  - Bayesian regression toward .500 for small samples
  - Log5 matchup formula for head-to-head probability
  - Starting pitcher ERA adjustment (Bayesian-regressed toward league avg by IP)
  - Recent form (last 10 games) minor adjustment
  - Home field advantage (~54%)
  - Expected Value vs. best available odds
  - Quarter-Kelly bet sizing
"""

import math
import logging

logger = logging.getLogger(__name__)

HOME_FIELD_ADVANTAGE = 0.54
PYTHAGOREAN_EXP = 1.83
REGRESSION_PRIOR_GAMES = 20   # Bayesian prior games at .500
ERA_ADJ_PER_RUN = 0.025        # Win prob shift per ERA-unit above/below team avg
ERA_ADJ_CAP = 0.07             # Cap SP adjustment at ±7%
FORM_ADJ_PER_GAME = 0.003      # Per game above/below .500 in last 10
MIN_GS_FOR_SP_ADJ = 2          # Minimum starts before trusting SP ERA

LEAGUE_AVG_ERA = 4.25          # MLB average ERA used for pitcher regression
ERA_PRIOR_IP = 40              # Virtual innings at league average (regression weight)


# ---------------------------------------------------------------------------
# Math primitives
# ---------------------------------------------------------------------------

def _pythagorean(rs: float, ra: float) -> float:
    if rs <= 0 or ra <= 0:
        return 0.500
    return rs ** PYTHAGOREAN_EXP / (rs ** PYTHAGOREAN_EXP + ra ** PYTHAGOREAN_EXP)


def _regress(wp: float, games_played: int) -> float:
    total = games_played + REGRESSION_PRIOR_GAMES
    return (wp * games_played + 0.5 * REGRESSION_PRIOR_GAMES) / total


def _log5(pa: float, pb: float) -> float:
    """Probability that team A beats team B given their independent win rates."""
    denom = pa + pb - 2 * pa * pb
    return 0.5 if abs(denom) < 1e-10 else (pa - pa * pb) / denom


def _clamp(val: float, lo: float = 0.05, hi: float = 0.95) -> float:
    return max(lo, min(hi, val))


def _parse_ip(ip_str) -> float:
    """Convert MLB innings-pitched string '12.1' (12 inn, 1 out) to decimal innings."""
    try:
        parts = str(ip_str).split('.')
        innings = int(parts[0])
        outs = int(parts[1]) if len(parts) > 1 else 0
        return innings + outs / 3.0
    except Exception:
        return 0.0


def _regress_era(era: float, ip_decimal: float) -> float:
    """
    Bayesian regression of ERA toward league average.
    At 0 IP returns league avg; at 80+ IP is ~2/3 actual ERA.
    Prevents early-season flukes (e.g. 0.38 ERA in 2 starts) from
    dominating the model.
    """
    total = ip_decimal + ERA_PRIOR_IP
    return (era * ip_decimal + LEAGUE_AVG_ERA * ERA_PRIOR_IP) / total


def american_to_decimal(american: int) -> float:
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / abs(american)


def probability_to_american(prob: float) -> int:
    prob = _clamp(prob, 0.01, 0.99)
    if prob >= 0.5:
        return -round((prob / (1 - prob)) * 100)
    return round(((1 - prob) / prob) * 100)


def implied_probs_no_vig(home_odds: int, away_odds: int):
    """Remove the bookmaker vig and return fair probabilities + vig%."""
    home_raw = 1 / american_to_decimal(home_odds)
    away_raw = 1 / american_to_decimal(away_odds)
    total = home_raw + away_raw
    vig = (total - 1.0) * 100
    return home_raw / total, away_raw / total, round(vig, 2)


def expected_value(model_prob: float, american_odds: int):
    """EV per $1 wagered. Positive = profitable long-run."""
    decimal = american_to_decimal(american_odds)
    ev = model_prob * decimal - 1.0
    return round(ev, 4), round(ev * 100, 2)


def kelly_fraction(model_prob: float, american_odds: int, fraction: float = 0.25) -> float:
    """Quarter-Kelly bet size as a percentage of bankroll."""
    b = american_to_decimal(american_odds) - 1
    if b <= 0:
        return 0.0
    q = 1 - model_prob
    k = (model_prob * b - q) / b
    return max(0.0, round(k * fraction * 100, 2))


def ev_to_stars(ev_pct: float) -> int:
    if ev_pct >= 10:
        return 5
    if ev_pct >= 7:
        return 4
    if ev_pct >= 5:
        return 3
    if ev_pct >= 3:
        return 2
    if ev_pct >= 1:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Team name matching (Odds API ↔ MLB API)
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return name.lower().strip()


def _team_nickname(name: str) -> str:
    """Last word of team name — usually the nickname."""
    parts = name.strip().split()
    return parts[-1].lower() if parts else ""


def teams_match(name1: str, name2: str) -> bool:
    n1, n2 = _normalize(name1), _normalize(name2)
    if n1 == n2:
        return True
    # Nickname match (e.g. "Yankees" == "Yankees")
    if _team_nickname(n1) == _team_nickname(n2):
        return True
    # Word-overlap (handles "Athletics" vs "Oakland Athletics")
    stop = {"the", "of", "at", "san", "los", "new", "las"}
    w1 = set(n1.split()) - stop
    w2 = set(n2.split()) - stop
    return len(w1 & w2) >= 2


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

class BettingModel:

    # ------------------------------------------------------------------
    # Probability engine
    # ------------------------------------------------------------------

    def _sp_adjustment(self, team: dict) -> float:
        pitcher = team.get("pitcher", {})
        sp_era = pitcher.get("era")
        gs = pitcher.get("games_started", 0) or 0
        ip_decimal = _parse_ip(pitcher.get("innings_pitched", "0"))

        if sp_era is None or gs < MIN_GS_FOR_SP_ADJ:
            return 0.0

        # Regress ERA toward league average weighted by innings pitched
        regressed_era = _regress_era(sp_era, ip_decimal)

        # Implied team ERA ≈ RA/G * 0.90
        team_era_est = team.get("ra_per_game", 4.5) * 0.90
        era_diff = team_era_est - regressed_era   # positive → SP better than team avg
        return _clamp(era_diff * ERA_ADJ_PER_RUN, -ERA_ADJ_CAP, ERA_ADJ_CAP)

    def _form_adjustment(self, last_ten: str) -> float:
        try:
            if not last_ten or last_ten == "-":
                return 0.0
            w, l = last_ten.split("-")
            wins = int(w)
            return (wins - 5) * FORM_ADJ_PER_GAME
        except Exception:
            return 0.0

    def calculate_win_probability(self, home: dict, away: dict):
        """Return (home_prob, away_prob, breakdown_dict)."""
        home_rs = max(home.get("rs_per_game", 4.5), 0.1)
        home_ra = max(home.get("ra_per_game", 4.5), 0.1)
        away_rs = max(away.get("rs_per_game", 4.5), 0.1)
        away_ra = max(away.get("ra_per_game", 4.5), 0.1)

        home_gp = home.get("games_played", 0) or 0
        away_gp = away.get("games_played", 0) or 0

        home_pyth = _regress(_pythagorean(home_rs, home_ra), home_gp)
        away_pyth = _regress(_pythagorean(away_rs, away_ra), away_gp)

        # Log5 base, then blend with neutral .500 based on sample maturity
        log5_prob = _log5(home_pyth, away_pyth)
        total_gp = home_gp + away_gp
        skill_weight = min(1.0, total_gp / 50)  # Full weight at 50+ combined games
        base_prob = (1 - skill_weight) * 0.5 + skill_weight * log5_prob

        hf_boost = HOME_FIELD_ADVANTAGE - 0.5   # +0.04
        home_sp_adj = self._sp_adjustment(home)
        away_sp_adj = self._sp_adjustment(away)
        home_form = self._form_adjustment(home.get("last_ten", ""))
        away_form = self._form_adjustment(away.get("last_ten", ""))

        final = _clamp(base_prob + hf_boost + home_sp_adj - away_sp_adj + home_form - away_form)

        breakdown = {
            "home_pythagorean_wp": round(home_pyth, 3),
            "away_pythagorean_wp": round(away_pyth, 3),
            "log5_prob": round(log5_prob, 3),
            "skill_weight": round(skill_weight, 2),
            "home_sp_adj": round(home_sp_adj, 3),
            "away_sp_adj": round(away_sp_adj, 3),
            "home_form_adj": round(home_form, 3),
            "away_form_adj": round(away_form, 3),
        }
        return final, 1 - final, breakdown

    # ------------------------------------------------------------------
    # Odds matching
    # ------------------------------------------------------------------

    def _find_odds_game(self, game: dict, odds_data: list):
        if not odds_data:
            return None
        home_name = game["home"]["name"]
        away_name = game["away"]["name"]
        for og in odds_data:
            oh = og.get("home_team", "")
            oa = og.get("away_team", "")
            if teams_match(home_name, oh) and teams_match(away_name, oa):
                return og
            # Handle reversed home/away in some feeds
            if teams_match(home_name, oa) and teams_match(away_name, oh):
                return og
        return None

    def _best_odds_for_team(self, odds_game: dict, team_name: str):
        """Return (best_odds, best_book, all_odds_list)."""
        best = None
        best_book = None
        all_lines = []
        for bk in odds_game.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for outcome in mkt.get("outcomes", []):
                    if teams_match(outcome.get("name", ""), team_name):
                        price = outcome.get("price")
                        if price is not None:
                            all_lines.append({"book": bk.get("title"), "odds": price})
                            if best is None or price > best:
                                best = price
                                best_book = bk.get("title")
        return best, best_book, all_lines

    # ------------------------------------------------------------------
    # Full game analysis
    # ------------------------------------------------------------------

    def analyze_game(self, game: dict, odds_data=None) -> dict:
        home = game["home"]
        away = game["away"]
        is_live = game.get("status") == "Live"

        home_prob, away_prob, breakdown = self.calculate_win_probability(home, away)

        result = {
            **game,
            "model": {
                "home_win_prob": round(home_prob, 3),
                "away_win_prob": round(away_prob, 3),
                "home_fair_odds": probability_to_american(home_prob),
                "away_fair_odds": probability_to_american(away_prob),
                "breakdown": breakdown,
                "pitcher_info_available": (
                    home["pitcher"].get("era") is not None
                    or away["pitcher"].get("era") is not None
                ),
            },
            "market": None,
            "bets": [],
            "odds_matched": False,
            "is_live": is_live,
        }

        # Never compare pre-game model probabilities against live in-game odds —
        # live lines reflect current score/situation, not pre-game fair value.
        if is_live or not odds_data:
            return result

        odds_game = self._find_odds_game(game, odds_data)
        if not odds_game:
            return result

        result["odds_matched"] = True
        home_best, home_book, home_lines = self._best_odds_for_team(odds_game, home["name"])
        away_best, away_book, away_lines = self._best_odds_for_team(odds_game, away["name"])

        market = {
            "home_best_odds": home_best,
            "home_best_book": home_book,
            "home_all_odds": home_lines,
            "away_best_odds": away_best,
            "away_best_book": away_book,
            "away_all_odds": away_lines,
            "commence_time": odds_game.get("commence_time"),
        }

        if home_best is not None and away_best is not None:
            h_fair, a_fair, vig = implied_probs_no_vig(home_best, away_best)
            market["home_implied_prob"] = round(h_fair, 3)
            market["away_implied_prob"] = round(a_fair, 3)
            market["vig_pct"] = vig

        result["market"] = market

        # Build bet recommendations for both sides
        bets = []
        for side, prob, best_odds, book, lines, implied in [
            ("home", home_prob, home_best, home_book, home_lines, market.get("home_implied_prob")),
            ("away", away_prob, away_best, away_book, away_lines, market.get("away_implied_prob")),
        ]:
            team = home if side == "home" else away
            if best_odds is None:
                continue
            ev, ev_pct = expected_value(prob, best_odds)
            edge = round(prob - (implied or 0), 3) if implied else None
            bets.append(
                {
                    "team": team["name"],
                    "abbreviation": team.get("abbreviation", ""),
                    "side": side,
                    "best_odds": best_odds,
                    "best_book": book,
                    "all_odds": lines,
                    "model_prob": round(prob, 3),
                    "implied_prob": implied,
                    "edge": edge,
                    "ev": ev,
                    "ev_pct": ev_pct,
                    "stars": ev_to_stars(ev_pct),
                    "kelly_pct": kelly_fraction(prob, best_odds),
                }
            )

        result["bets"] = sorted(bets, key=lambda b: b["ev_pct"], reverse=True)
        return result

    def get_top_bets(self, analyzed_games: list, min_ev_pct: float = 1.0) -> list:
        top = []
        for game in analyzed_games:
            for bet in game.get("bets", []):
                if bet.get("ev_pct", 0) >= min_ev_pct:
                    top.append(
                        {
                            **bet,
                            "matchup": f"{game['away']['name']} @ {game['home']['name']}",
                            "game_id": game.get("game_id"),
                            "game_time_utc": game.get("game_time_utc"),
                            "venue": game.get("venue", ""),
                        }
                    )
        return sorted(top, key=lambda b: b["ev_pct"], reverse=True)
