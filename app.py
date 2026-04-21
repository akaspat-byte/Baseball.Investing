import logging
import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from services.mlb_api import MLBApiService
from services.model import BettingModel
from services.odds_api import OddsApiService

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Simple in-memory cache (keyed by date + api_key presence)
_cache: dict = {}
CACHE_TTL = 900  # 15 minutes


def _cache_key(has_key: bool) -> str:
    from datetime import date
    return f"{date.today().isoformat()}_{has_key}"


def _cached_result(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _store_result(key: str, data: dict):
    _cache[key] = {"data": data, "ts": time.time()}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/analyze")
def analyze():
    """
    Query params:
        key  — Odds API key (optional; falls back to ODDS_API_KEY env var)
        fresh — if "1", bypass cache
    """
    odds_key = request.args.get("key", "").strip() or os.getenv("ODDS_API_KEY", "")
    force_fresh = request.args.get("fresh") == "1"

    cache_key = _cache_key(bool(odds_key))
    if not force_fresh:
        cached = _cached_result(cache_key)
        if cached:
            logger.info("Returning cached analysis")
            return jsonify({**cached, "cached": True})

    try:
        mlb = MLBApiService()
        games_data = mlb.get_todays_games_with_stats()

        odds_data = None
        odds_error = None
        requests_remaining = None

        if odds_key:
            svc = OddsApiService(odds_key)
            odds_data, odds_error = svc.get_mlb_odds()
            requests_remaining = svc.requests_remaining

        model = BettingModel()
        analyzed = [model.analyze_game(g, odds_data) for g in games_data["games"]]
        top_bets = model.get_top_bets(analyzed)

        result = {
            "success": True,
            "date": games_data["date"],
            "season": games_data["season"],
            "games_count": len(analyzed),
            "games": analyzed,
            "top_bets": top_bets,
            "odds_available": odds_data is not None,
            "odds_matched_count": sum(1 for g in analyzed if g.get("odds_matched")),
            "odds_error": odds_error,
            "requests_remaining": requests_remaining,
            "cached": False,
        }

        _store_result(cache_key, result)
        return jsonify(result)

    except Exception as exc:
        logger.exception("Analysis failed")
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
