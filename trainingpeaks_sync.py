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

SPORT_MAP is confirmed against a real account for swim/bike/run/strength
(1/2/3/9). Other workoutTypeValueId codes (e.g. 13, 100) fall back to
"other" -- intentionally unmapped, out of scope for this athlete.
"""

import argparse
import gzip
import io
import json
import sys
from datetime import date, datetime, timedelta
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

# Confirmed live against a real account. Codes 13/100 (seen but unmapped, out
# of scope for this athlete) intentionally fall back to "other".
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
    }


def _normalize_timestamps(laps: list[dict], records: list[dict]) -> tuple[list[dict], list[dict]]:
    """fitparse hands back real datetime objects for timestamp-like fields
    (record "timestamp", lap "start_time"). Convert every datetime value to
    seconds elapsed since the session's first record, matching the sample
    data generator's convention -- everything downstream (metrics.py's
    duration math, the frontend's timestamp/60-for-elapsed-minutes charts)
    assumes an elapsed-seconds stream, not wall-clock datetimes. Without
    this, json.dumps(default=str) would silently stringify them instead."""
    first_ts = next((r["timestamp"] for r in records if isinstance(r.get("timestamp"), datetime)), None)
    if first_ts is None:
        return laps, records

    def convert(d: dict) -> dict:
        return {k: (v - first_ts).total_seconds() if isinstance(v, datetime) else v for k, v in d.items()}

    return [convert(l) for l in laps], [convert(r) for r in records]


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
        return _normalize_timestamps(laps, records)
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

        laps, records = fetch_laps_and_records(client, wid, details.get("workoutDeviceFileInfos") or [])

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


# TP's own workoutTypeId convention (matches SPORT_MAP's keys, confirmed live).
_WORKOUT_TYPE_ID = {"swim": 1, "bike": 2, "run": 3}


def _zone_group_for(zone_groups: list | None, workout_type_id: int):
    for g in zone_groups or []:
        if isinstance(g, dict) and g.get("workoutTypeId") == workout_type_id:
            return g
    return None


def _mps_to_pace_per_km(mps: float | None) -> str | None:
    if not mps:
        return None
    seconds_per_km = 1000 / mps
    return f"{int(seconds_per_km // 60)}:{round(seconds_per_km % 60):02d}"


def _mps_to_pace_per_100m(mps: float | None) -> str | None:
    if not mps:
        return None
    seconds_per_100m = 100 / mps
    return f"{int(seconds_per_100m // 60)}:{round(seconds_per_100m % 60):02d}"


def parse_thresholds(raw_settings: dict) -> dict:
    """Extract just the athlete's physiological thresholds from TP's settings
    payload. Deliberately drops every other field the endpoint returns
    (address, email, phone, birthday, userIdentifierHash, etc.) -- this
    function is the PII boundary for what actually lands in thresholds.json.

    Field names (workoutTypeId / threshold / zones) come from the zone-group
    shape used by TP's own zone-update endpoints, not guessed. `threshold` is
    stored as a speed (m/s) for speed zones, hence the pace conversion below --
    that unit assumption is the one part of this parser not yet double-checked
    against a live numeric value, only against the shape.
    """
    power_zones = raw_settings.get("powerZones") or []
    hr_zones = raw_settings.get("heartRateZones") or []
    speed_zones = raw_settings.get("speedZones") or []

    bike_power = _zone_group_for(power_zones, _WORKOUT_TYPE_ID["bike"])
    hr_group = _zone_group_for(hr_zones, 0) or (hr_zones[0] if hr_zones else None)
    run_speed = _zone_group_for(speed_zones, _WORKOUT_TYPE_ID["run"])
    swim_speed = _zone_group_for(speed_zones, _WORKOUT_TYPE_ID["swim"])

    return {
        "ftp": bike_power.get("threshold") if bike_power else None,
        "threshold_hr": hr_group.get("threshold") if hr_group else None,
        "max_hr": hr_group.get("maxHr") if hr_group else None,
        "resting_hr": hr_group.get("restingHr") if hr_group else None,
        "threshold_run_pace_per_km": _mps_to_pace_per_km(run_speed.get("threshold")) if run_speed else None,
        "css_per_100m": _mps_to_pace_per_100m(swim_speed.get("threshold")) if swim_speed else None,
        # Not present on this endpoint's payload (checked the real top-level key
        # list -- no weight field there) -- needs a different endpoint, not yet found.
        "weight_kg": None,
    }


def append_threshold_entry(client: TrainingPeaksClient) -> None:
    settings = client.get_athlete_settings()
    path = DATA_DIR / "thresholds.json"
    entries = load_json(path, [])
    entry = {"date": date.today().isoformat(), **parse_thresholds(settings)}
    entries.append(entry)
    save_json(path, entries)
    print(f"Appended threshold entry for {date.today()} -> data/thresholds.json")


def migrate_detail_timestamps() -> None:
    """One-time local fix for detail files fetched before _normalize_timestamps
    existed. Those files have "timestamp"/"start_time" as datetime STRINGS
    (json.dumps's default=str fallback, e.g. "2026-07-29 21:47:08") instead
    of elapsed seconds, which breaks duration math and chart x-axis labels.
    Pure local JSON rewrite -- no TrainingPeaks auth or API calls needed, so
    it doesn't cost anything against the cookie's lifetime or rate limits.
    Safe to re-run: files already in elapsed-seconds form are left alone.
    """
    if not DETAIL_DIR.exists():
        print("No detail files to migrate.")
        return

    migrated = 0
    for path in sorted(DETAIL_DIR.glob("*.json")):
        detail = json.loads(path.read_text())
        records = detail.get("records") or []
        laps = detail.get("laps") or []

        first_dt = None
        for r in records:
            ts = r.get("timestamp")
            if isinstance(ts, str):
                try:
                    first_dt = datetime.fromisoformat(ts)
                except ValueError:
                    pass
                break

        if first_dt is None:
            continue  # already numeric (or already migrated, or no records)

        def convert(d: dict) -> dict:
            out = dict(d)
            for key in ("timestamp", "start_time"):
                v = out.get(key)
                if isinstance(v, str):
                    try:
                        out[key] = (datetime.fromisoformat(v) - first_dt).total_seconds()
                    except ValueError:
                        pass
            return out

        detail["records"] = [convert(r) for r in records]
        detail["laps"] = [convert(l) for l in laps]
        save_json(path, detail)
        migrated += 1

    print(f"Migrated timestamps in {migrated} detail file(s) (already-clean files left untouched).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90, help="days of activity history to sync (default 90)")
    parser.add_argument("--pmc-days", type=int, default=180, help="days of PMC history to sync (default 180)")
    parser.add_argument("--refresh", action="store_true", help="refetch activity details even if already saved")
    parser.add_argument("--skip-thresholds", action="store_true", help="skip appending a threshold entry")
    parser.add_argument(
        "--migrate-timestamps", action="store_true",
        help="one-time local fix for detail files with datetime-string timestamps (no TP auth needed, exits after running)",
    )
    args = parser.parse_args()

    if args.migrate_timestamps:
        migrate_detail_timestamps()
        return

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
