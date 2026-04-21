import requests
import os
import logging

logger = logging.getLogger(__name__)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


class OddsApiService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("ODDS_API_KEY", "")
        self.requests_remaining = None
        self.requests_used = None

    def get_mlb_odds(self):
        """Fetch MLB moneyline (h2h) odds. Returns (data, error_msg)."""
        if not self.api_key:
            return None, "No API key provided"

        try:
            resp = requests.get(
                f"{ODDS_API_BASE}/sports/baseball_mlb/odds/",
                params={
                    "apiKey": self.api_key,
                    "regions": "us",
                    "markets": "h2h",
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                },
                timeout=15,
            )

            self.requests_remaining = resp.headers.get("x-requests-remaining")
            self.requests_used = resp.headers.get("x-requests-used")

            if resp.status_code == 401:
                return None, "Invalid API key — get a free key at the-odds-api.com"
            if resp.status_code == 422:
                return None, "MLB season unavailable or off-season"
            if resp.status_code == 429:
                return None, "API rate limit reached — try again later"

            resp.raise_for_status()
            return resp.json(), None

        except requests.exceptions.ConnectionError:
            return None, "Network error — check internet connection"
        except requests.exceptions.Timeout:
            return None, "Request timed out"
        except requests.exceptions.RequestException as e:
            return None, str(e)
