"""
Minimal TrainingPeaks API client.

Auth is a `Production_tpAuth` cookie value (no interactive login, so no
bot-blocking risk) exchanged for a short-lived Bearer token. Endpoints and
the exchange flow are verified against a real account; the exact shapes were
confirmed by inspecting the MIT-licensed JamsusMaximus/trainingpeaks-mcp
project's source, not guessed.

Set TP_AUTH_COOKIE in .env to your current cookie value (DevTools -> Application
-> Cookies -> trainingpeaks.com -> Production_tpAuth, while logged in). The
cookie has a finite lifetime -- a 401 here means it's expired and you need to
grab a fresh one.
"""

import os
import time

import httpx

TP_API_BASE = "https://tpapi.trainingpeaks.com"
TOKEN_ENDPOINT = "/users/v3/token"
MIN_REQUEST_INTERVAL = 0.5  # be polite to TrainingPeaks between requests


class TPAuthError(Exception):
    pass


class TrainingPeaksClient:
    def __init__(self, cookie: str | None = None, timeout: float = 30.0):
        self.cookie = cookie or os.environ.get("TP_AUTH_COOKIE")
        if not self.cookie:
            raise TPAuthError("TP_AUTH_COOKIE not set (add it to .env)")
        self._client = httpx.Client(timeout=timeout)
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._athlete_id: int | None = None
        self._last_request = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._client.close()

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    def _ensure_token(self):
        if self._access_token and time.time() < self._token_expires_at - 60:
            return
        self._throttle()
        r = self._client.get(
            f"{TP_API_BASE}{TOKEN_ENDPOINT}",
            headers={"Cookie": f"Production_tpAuth={self.cookie}", "Accept": "application/json"},
        )
        if r.status_code == 401:
            raise TPAuthError(
                "Cookie rejected (401/expired). Re-extract Production_tpAuth from a "
                "logged-in browser session and update TP_AUTH_COOKIE in .env."
            )
        if r.status_code == 500:
            raise TPAuthError(
                "Token exchange returned 500 - the cookie value is present but malformed "
                "(commonly a copy/paste truncation, given how long these are). Re-copy it "
                "carefully (right-click the row in DevTools -> Copy value)."
            )
        if r.status_code != 200:
            raise TPAuthError(f"Token exchange failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        if not data.get("success") or "token" not in data:
            raise TPAuthError("Unexpected token exchange response shape")
        token = data["token"]
        self._access_token = token["access_token"]
        self._token_expires_at = time.time() + token.get("expires_in", 3600)

    def _headers(self):
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, endpoint: str, params: dict | None = None):
        self._throttle()
        r = self._client.get(f"{TP_API_BASE}{endpoint}", headers=self._headers(), params=params)
        if r.status_code == 401:
            self._access_token = None  # token may have expired mid-run; retry once
            self._throttle()
            r = self._client.get(f"{TP_API_BASE}{endpoint}", headers=self._headers(), params=params)
        r.raise_for_status()
        return r.json() if r.content else None

    def _post(self, endpoint: str, json_body=None):
        self._throttle()
        r = self._client.post(f"{TP_API_BASE}{endpoint}", headers=self._headers(), json=json_body)
        r.raise_for_status()
        return r.json() if r.content else None

    def _get_raw(self, endpoint: str) -> bytes:
        self._throttle()
        r = self._client.get(f"{TP_API_BASE}{endpoint}", headers=self._headers())
        r.raise_for_status()
        return r.content

    def get_athlete_id(self) -> int:
        if self._athlete_id:
            return self._athlete_id
        user = self._get("/users/v3/user")
        user_data = user.get("user", user)
        athletes = user_data.get("athletes", [])
        athlete_id = athletes[0]["athleteId"] if athletes else user_data.get("personId")
        if not athlete_id:
            raise TPAuthError("Could not resolve athlete_id from /users/v3/user")
        self._athlete_id = athlete_id
        return athlete_id

    def get_workouts(self, start_date: str, end_date: str) -> list[dict]:
        """Workout summaries for a date range (YYYY-MM-DD, inclusive)."""
        athlete_id = self.get_athlete_id()
        data = self._get(f"/fitness/v6/athletes/{athlete_id}/workouts/{start_date}/{end_date}")
        return data or []

    def get_workout_details(self, workout_id) -> dict:
        """Pre-aggregated details: mean-max curves, time-in-zone, device file refs."""
        athlete_id = self.get_athlete_id()
        return self._get(f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}/details") or {}

    def get_raw_file(self, workout_id, file_id) -> bytes:
        """The original uploaded device file (FIT, sometimes gzipped)."""
        athlete_id = self.get_athlete_id()
        return self._get_raw(f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}/rawfiledata/{file_id}")

    def get_pmc(self, start_date: str, end_date: str, atl_constant: int = 7, ctl_constant: int = 42) -> list[dict]:
        """Real, TrainingPeaks-computed daily CTL/ATL/TSB for a date range."""
        athlete_id = self.get_athlete_id()
        body = {
            "atlConstant": atl_constant,
            "atlStart": 0,
            "ctlConstant": ctl_constant,
            "ctlStart": 0,
            "workoutTypes": [],
        }
        data = self._post(
            f"/fitness/v1/athletes/{athlete_id}/reporting/performancedata/{start_date}/{end_date}",
            body,
        )
        return data or []

    def get_athlete_settings(self) -> dict:
        """Zone groups (FTP/threshold HR/threshold pace live in here as `threshold`
        fields per zone group). Exact parsing into our thresholds.json schema is
        still unverified against a real payload -- see trainingpeaks_sync.py."""
        athlete_id = self.get_athlete_id()
        return self._get(f"/fitness/v1/athletes/{athlete_id}/settings") or {}
