"""
Real TrainingPeaks sync for the Elite Triathlon Coach Dashboard.

Auth: set TP_AUTH_COOKIE in .env to your current Production_tpAuth cookie
value (DevTools -> Application -> Cookies -> trainingpeaks.com, while logged
in -- right-click the row -> Copy value, these are long and easy to truncate
with a manual drag-select). The cookie has a finite lifetime; a 401 here
means it's expired and you need a fresh one.

Populates:
  data/activities.json        summary index (one row per workout)
  data/activities/{id}.json   per-workout pre-aggregated details + parsed
                               FIT laps/records from the original device file
  data/pmc.json                real CTL/ATL/TSB, computed by TrainingPeaks
  data/thresholds.json         appends a dated entry with your live athlete
                                settings (FTP/threshold HR/pace/CSS/weight)

Resumable: activity detail files are only (re)fetched if missing, unless
--refresh is passed. Requests are throttled (see trainingpeaks_client.py).

Known gaps, to refine once run against real data (see inline TODOs):
  - SPORT_MAP below (TP's workoutTypeValueId -> our sport strings) is a
    best-guess mapping, not yet verified against a real payload.
  - append_threshold_entry() stores the raw settings payload as-is; the exact
    field paths for ftp/threshold_hr/threshold_run_pace_per_km/css_per_100m
    still need confirming against a real response before we parse them out.
"""

import argparse
import gzip
import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from trainingpeaks_client import TPAuthError, TrainingPeaksClient

load_dotenv()

try:
    import fitparse
except ImportError:
    fitparse = None

DATA_DIR = Path(__file__).parent / "data"
DETAIL_DIR = DATA_DIR / "activities"

# TODO(verify): confirm these against real workoutTypeValueId values before
# trusting sport classification broadly -- unmapped ids fall back to "other".
SPORT_MAP = {
    1: "swim",
    2: "bike",
    3: "run",
    9: "strength",
}


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def to_our_activity(w: dict) -> dict:
    duration_s = w.get("totalTime")
    distance_m = w.get("distance")
    return {
        "id": w.get("workoutId"),
        "date": (w.get("workoutDay") or "").split("T")[0],
        "sport": SPORT_MAP.get(w.get("workoutTypeValueId"), "other"),
        "name": w.get("title") or "",
        "duration_min": round(duration_s / 60, 1) if duration_s else None,
        "distance_km": round(distance_m / 1000, 2) if distance_m else None,
        "avg_hr": w.get("heartRateAverage"),
        "avg_power": w.get("powerAverage"),
        "tss": w.get("tssActual"),
        "if": w.get("ifActual"),
        "normalized_power": w.get("normalizedPowerActual"),
        "description": w.get("description") or "",
        "device_files": w.get("workoutDeviceFileInfos", []),
    }


def fetch_laps_and_records(client: TrainingPeaksClient, workout_id, device_files: list[dict]):
    """Download + parse the original FIT file for lap/record-level detail.
    Returns ([], []) if unavailable -- callers should treat this as optional
    detail, not a hard failure."""
    if not device_files or fitparse is None:
        return [], []

    file_info = device_files[0]
    file_id = file_info.get("fileId")
    if file_id is None:
        return [], []

    try:
        raw = client.get_raw_file(workout_id, file_id)
        if raw[:2] == b"\x1f\x8b":  # gzip magic bytes
            raw = gzip.decompress(raw)
        fit = fitparse.FitFile(io.BytesIO(raw))
        laps = [msg.get_values() for msg in fit.get_messages("lap")]
        records = [msg.get_values() for msg in fit.get_messages("record")]
        return laps, records
    except Exception as e:
        print(f"    could not fetch/parse raw file for workout {workout_id}: {e}")
        return [], []


def sync_activities(client: TrainingPeaksClient, days_back: int, refresh: bool) -> None:
    index_path = DATA_DIR / "activities.json"
    existing = {a["id"]: a for a in load_json(index_path, [])}

    end = date.today()
    start = end - timedelta(days=days_back)
    workouts = client.get_workouts(start.isoformat(), end.isoformat())
    print(f"Fetched {len(workouts)} workout summaries from {start} to {end}")

    for w in workouts:
        wid = w.get("workoutId")
        if wid is None:
            continue
        existing[wid] = to_our_activity(w)

        detail_path = DETAIL_DIR / f"{wid}.json"
        if detail_path.exists() and not refresh:
            continue  # resumable: only fetch detail once per activity

        print(f"  Fetching details for workout {wid} ({w.get('title')!r})...")
        try:
            details = client.get_workout_details(wid)
        except Exception as e:
            print(f"    failed to fetch details: {e}")
            continue

        laps, records = fetch_laps_and_records(client, wid, w.get("workoutDeviceFileInfos") or [])

        save_json(detail_path, {
            "workout_id": wid,
            "details": details,
            "laps": laps,
            "records": records,
        })

    merged = sorted(existing.values(), key=lambda a: a["date"] or "")
    save_json(index_path, merged)
    print(f"Saved {len(merged)} activities -> {index_path}")


def sync_pmc(client: TrainingPeaksClient, days_back: int) -> None:
    end = date.today()
    start = end - timedelta(days=days_back)
    pmc = client.get_pmc(start.isoformat(), end.isoformat())
    out = [
        {
            "date": (p.get("workoutDay") or "").split("T")[0],
            "ctl": p.get("ctl"),
            "atl": p.get("atl"),
            "tsb": p.get("tsb"),
        }
        for p in pmc
    ]
    save_json(DATA_DIR / "pmc.json", out)
    print(f"Saved {len(out)} days of real PMC -> data/pmc.json")


def append_threshold_entry(client: TrainingPeaksClient) -> None:
    settings = client.get_athlete_settings()
    path = DATA_DIR / "thresholds.json"
    entries = load_json(path, [])
    entries.append({
        "date": date.today().isoformat(),
        # TODO(verify): parse ftp / threshold_hr / max_hr / threshold_run_pace_per_km /
        # css_per_100m / weight_kg out of the real zone-group shape once seen; keeping
        # the raw payload for now so nothing is lost.
        "raw_settings": settings,
    })
    save_json(path, entries)
    print(f"Appended threshold entry for {date.today()} -> data/thresholds.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90, help="days of activity history to sync (default 90)")
    parser.add_argument("--pmc-days", type=int, default=180, help="days of PMC history to sync (default 180)")
    parser.add_argument("--refresh", action="store_true", help="refetch activity details even if already saved")
    parser.add_argument("--skip-thresholds", action="store_true", help="skip appending a threshold entry")
    args = parser.parse_args()

    try:
        client = TrainingPeaksClient()
    except TPAuthError as e:
        print(f"Auth error: {e}")
        sys.exit(1)

    with client:
        try:
            sync_activities(client, days_back=args.days, refresh=args.refresh)
            sync_pmc(client, days_back=args.pmc_days)
            if not args.skip_thresholds:
                append_threshold_entry(client)
        except TPAuthError as e:
            print(f"Auth error mid-sync: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
