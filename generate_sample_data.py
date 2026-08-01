"""
Generates realistic fake training/health data for the Elite Triathlon Coach Dashboard.

This writes the exact JSON files (data/daily_metrics.json, data/activities.json,
data/pmc.json, data/weekly_summary.json, data/blocks.json) and per-activity
detail files (data/activities/{id}.json, with synthesized "laps"/"records"
matching real FIT field names) that trainingpeaks_sync.py populates for real.
Keep the schema stable.

Athlete profile baked into the simulation: elite amateur triathlete, 70.3 PB ~4:30,
Ironman PB ~10:25, FTP ~285W, run threshold pace ~3:42/km, swim CSS ~1:22/100m.
Currently in a long base/build buildup toward Sunshine Coast 70.3 (Sept 2027).
"""

import itertools
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).parent / "data"
NUM_DAYS = 210
TODAY = date.today()
START_DATE = TODAY - timedelta(days=NUM_DAYS - 1)

# Athlete thresholds used to derive realistic paces/power for fake sessions.
FTP_BIKE_W = 285
THRESHOLD_RUN_PACE_S_PER_KM = 222  # 3:42/km
CSS_SWIM_PACE_S_PER_100M = 82      # 1:22/100m

SPORT_COLORS_NOTE = "swim=blue bike=graphite run=orange strength=gray (see styles.css)"

TRAINING_STATUSES = [
    "Peaking", "Productive", "Maintaining", "Recovery",
    "Overreaching", "Detraining", "Unproductive",
]

SWIM_DESCRIPTIONS = [
    "CSS intervals felt smooth, holding pace through the back half.",
    "Technique-focused session, long paddle/pull set.",
    "Choppy open water, sighting practice for race conditions.",
    "Easy recovery swim, just moving the arms.",
    "Broken 400s at threshold, negative split the last rep.",
    "Pull buoy endurance set, aerobic and controlled.",
]
BIKE_DESCRIPTIONS = [
    "Sweet spot intervals on the trainer, legs felt heavy from Sunday.",
    "Long steady ride, nutrition practice for race day.",
    "VO2 reps on the climb, hit all target powers.",
    "Easy spin to flush the legs out.",
    "Race-simulation ride: 30min build into 20min at 70.3 power.",
    "Group ride turned into an unplanned threshold session.",
    "Aero position work, holding watts steady in the drops.",
]
RUN_DESCRIPTIONS = [
    "Tempo run off the bike, legs found their rhythm by km 3.",
    "Long run, negative split the back 5k.",
    "Track intervals, hitting threshold pace comfortably.",
    "Easy shakeout run, kept it conversational.",
    "Brick run after the long ride, heavy legs early then settled in.",
    "Progression run finishing at half marathon race pace.",
]
STRENGTH_DESCRIPTIONS = [
    "Lower body strength + core, focused on single-leg stability.",
    "Full body maintenance session, kept loads moderate in-season.",
    "Injury-prevention circuit: hips, glutes, ankles.",
]

SPORT_ID = {"swim": 0, "bike": 1, "run": 2, "strength": 3}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_pace(seconds_per_unit):
    m = int(seconds_per_unit // 60)
    s = int(round(seconds_per_unit % 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"


def week_start(d):
    return d - timedelta(days=d.weekday())


def build_week_plan(week_index, num_weeks):
    """Returns target weekly hours + a periodization phase for one training week."""
    progress = week_index / max(1, num_weeks - 1)
    base_hours = 9.0 + progress * 5.0  # slow build from ~9h to ~14h across the window
    cycle_pos = week_index % 4
    if cycle_pos == 3:
        phase = "recovery"
        target_hours = base_hours * 0.62
    else:
        phase = "build"
        target_hours = base_hours * (0.90 + 0.05 * cycle_pos)
    target_hours *= random.uniform(0.93, 1.07)
    return phase, round(target_hours, 2)


def synthesize_session_detail(sport, intensity, is_long, duration_min, avg_hr, output_avg, is_interval):
    """Fake per-activity laps/records matching the exact field names real FIT
    files return (confirmed against a live TrainingPeaks account -- see
    trainingpeaks_sync.py). Lets the Sessions page's metrics.py calculations
    (decoupling/durability/rep-fade) run against realistic-shaped data without
    needing a real sync.

    output_key is "power" for bike (records) / "avg_power" (laps), or
    "enhanced_speed" / "enhanced_avg_speed" for run and swim.
    """
    is_bike = sport == "bike"
    sample_every_s = 5
    n_samples = max(4, round(duration_min * 60 / sample_every_s))

    records = []
    decouple = is_long and duration_min >= 60  # aerobic drift only worth faking on long steady work
    for i in range(n_samples):
        frac = i / max(1, n_samples - 1)
        output = output_avg * (1 + random.uniform(-0.05, 0.05)) if output_avg else None
        hr = avg_hr * (1 + random.uniform(-0.03, 0.03)) if avg_hr else None
        if decouple and output is not None and hr is not None:
            output *= (1 - 0.05 * frac)   # output drifts down ~5% by the end
            hr *= (1 + 0.035 * frac)      # HR drifts up ~3.5% by the end
        record = {"timestamp": i * sample_every_s, "heart_rate": round(hr) if hr else None}
        if is_bike:
            record["power"] = round(output) if output is not None else None
            record["cadence"] = round(clamp(85 + random.uniform(-5, 5), 60, 105))
        else:
            record["enhanced_speed"] = round(output, 3) if output is not None else None
            record["cadence"] = round(clamp(82 + random.uniform(-4, 4), 60, 100))
        records.append(record)

    laps = []
    if is_interval and output_avg:
        n_reps = random.choice([4, 5, 6])
        fade_total_pct = random.uniform(-8, 1)  # negative = faded across reps
        for rep in range(n_reps):
            frac = rep / max(1, n_reps - 1)
            rep_output = output_avg * (1 + fade_total_pct / 100 * frac) * (1 + random.uniform(-0.02, 0.02))
            rep_hr = avg_hr * (1 + random.uniform(-0.02, 0.03)) if avg_hr else None
            lap = {
                "message_index": rep,
                "start_time": rep * 300,
                "total_elapsed_time": 240 + random.uniform(-10, 10),
                "avg_heart_rate": round(rep_hr) if rep_hr else None,
                "max_heart_rate": round(rep_hr * 1.05) if rep_hr else None,
            }
            if is_bike:
                lap["avg_power"] = round(rep_output)
                lap["normalized_power"] = round(rep_output * random.uniform(1.0, 1.03))
            else:
                lap["enhanced_avg_speed"] = round(rep_output, 3)
            laps.append(lap)
    else:
        # single whole-session lap for steady/easy/recovery sessions
        lap = {
            "message_index": 0,
            "start_time": 0,
            "total_elapsed_time": duration_min * 60,
            "avg_heart_rate": round(avg_hr) if avg_hr else None,
            "max_heart_rate": round(avg_hr * 1.08) if avg_hr else None,
        }
        if is_bike and output_avg:
            lap["avg_power"] = round(output_avg)
            lap["normalized_power"] = round(output_avg * random.uniform(1.0, 1.05))
        elif output_avg:
            lap["enhanced_avg_speed"] = round(output_avg, 3)
        laps.append(lap)

    return laps, records


def gen_activities():
    activities = []
    activity_details = {}
    id_counter = itertools.count(1)
    num_weeks = math.ceil(NUM_DAYS / 7) + 1

    for week_index in range(num_weeks):
        monday = week_start(START_DATE) + timedelta(weeks=week_index)
        if monday > TODAY:
            break
        phase, target_hours = build_week_plan(week_index, num_weeks)

        long_bike_day = 5   # Saturday
        long_run_day = 6    # Sunday

        # Rough hour split across the week's sessions, then convert to durations.
        swim_sessions = 3 if phase == "build" else 2
        bike_sessions = 3 if phase == "build" else 2
        run_sessions = 4 if phase == "build" else 3
        strength_sessions = 1 if (week_index % 2 == 0 and phase == "build") else 0

        swim_hours = target_hours * 0.18
        bike_hours = target_hours * 0.45
        run_hours = target_hours * 0.32
        strength_hours = target_hours * 0.05 if strength_sessions else 0

        # Pick which weekdays get which sport (deterministic pattern + jitter).
        day_assignments = {d: [] for d in range(7)}
        swim_days = random.sample([0, 1, 3, 4], k=min(swim_sessions, 4))
        bike_days = list({1, long_bike_day, 3} if phase == "build" else {long_bike_day, 1})
        run_days = list({2, long_run_day, 4, 0} if phase == "build" else {long_run_day, 2, 4})
        bike_days = bike_days[:bike_sessions]
        run_days = run_days[:run_sessions]

        for d in swim_days:
            day_assignments[d].append("swim")
        for d in bike_days:
            day_assignments[d].append("bike")
        for d in run_days:
            day_assignments[d].append("run")
        if strength_sessions:
            day_assignments[random.choice([0, 2, 4])].append("strength")

        per_swim_hr = swim_hours / max(1, len(swim_days))
        per_bike_hr = bike_hours / max(1, len(bike_days))
        per_run_hr = run_hours / max(1, len(run_days))

        for offset in range(7):
            d = monday + timedelta(days=offset)
            if d < START_DATE or d > TODAY:
                continue
            sports_today = day_assignments[offset]
            for sport in sports_today:
                is_long = (sport == "bike" and offset == long_bike_day) or \
                          (sport == "run" and offset == long_run_day)

                if sport == "swim":
                    duration_min = clamp(per_swim_hr * 60 * random.uniform(0.8, 1.25), 25, 90)
                    if phase == "recovery":
                        intensity = random.choices(["endurance", "easy"], weights=[40, 60])[0]
                    else:
                        intensity = random.choices(["endurance", "threshold", "easy"], weights=[55, 25, 20])[0]
                    IF = {"endurance": random.uniform(0.72, 0.8),
                          "threshold": random.uniform(0.86, 0.95),
                          "easy": random.uniform(0.55, 0.65)}[intensity]
                    pace_100 = CSS_SWIM_PACE_S_PER_100M / IF * random.uniform(0.98, 1.03)
                    distance_km = round((duration_min * 60 / pace_100) * 0.1, 2)
                    avg_hr = round(clamp(128 + IF * 40 + random.uniform(-4, 4), 105, 175))
                    tss = round(duration_min / 60 * IF ** 2 * 100, 1)
                    name = {"endurance": "Endurance Swim", "threshold": "Threshold Swim Set",
                            "easy": "Recovery Swim"}[intensity]
                    desc = random.choice(SWIM_DESCRIPTIONS)
                    avg_power = None
                    normalized_power = None
                    avg_pace = f"{fmt_pace(pace_100)}/100m"

                elif sport == "bike":
                    if is_long:
                        duration_min = clamp(per_bike_hr * 60 * random.uniform(1.6, 2.1), 90, 300)
                        intensity = "endurance"
                    else:
                        duration_min = clamp(per_bike_hr * 60 * random.uniform(0.6, 1.1), 40, 120)
                        if phase == "recovery":
                            intensity = random.choices(["endurance", "easy"], weights=[35, 65])[0]
                        else:
                            intensity = random.choices(
                                ["endurance", "threshold", "vo2", "easy"], weights=[50, 22, 13, 15]
                            )[0]
                    IF = {"endurance": random.uniform(0.68, 0.78), "threshold": random.uniform(0.85, 0.95),
                          "vo2": random.uniform(0.95, 1.05), "easy": random.uniform(0.55, 0.65)}[intensity]
                    normalized_power = round(FTP_BIKE_W * IF * random.uniform(0.97, 1.02))
                    avg_power = round(normalized_power * random.uniform(0.90, 0.98))
                    speed_kmh = 28 + IF * 12 + random.uniform(-1.5, 1.5)
                    distance_km = round(duration_min / 60 * speed_kmh, 1)
                    avg_hr = round(clamp(122 + IF * 45 + random.uniform(-4, 4), 100, 178))
                    tss = round(duration_min / 60 * IF ** 2 * 100, 1)
                    name = "Long Endurance Ride" if is_long else {
                        "endurance": "Endurance Ride", "threshold": "Sweet Spot / Threshold Ride",
                        "vo2": "VO2 Max Intervals", "easy": "Recovery Spin"}[intensity]
                    desc = random.choice(BIKE_DESCRIPTIONS)
                    avg_pace = None

                elif sport == "run":
                    if is_long:
                        duration_min = clamp(per_run_hr * 60 * random.uniform(1.5, 1.9), 60, 150)
                        intensity = "endurance"
                    else:
                        duration_min = clamp(per_run_hr * 60 * random.uniform(0.55, 1.0), 25, 75)
                        if phase == "recovery":
                            intensity = random.choices(["endurance", "easy"], weights=[35, 65])[0]
                        else:
                            intensity = random.choices(
                                ["endurance", "threshold", "easy", "brick"], weights=[45, 20, 20, 15]
                            )[0]
                    IF = {"endurance": random.uniform(0.72, 0.80), "threshold": random.uniform(0.88, 0.97),
                          "easy": random.uniform(0.58, 0.68), "brick": random.uniform(0.75, 0.85)}[intensity]
                    pace_s_km = THRESHOLD_RUN_PACE_S_PER_KM / IF * random.uniform(0.98, 1.03)
                    distance_km = round((duration_min * 60) / pace_s_km, 2)
                    avg_hr = round(clamp(130 + IF * 42 + random.uniform(-4, 4), 108, 182))
                    tss = round(duration_min / 60 * IF ** 2 * 100, 1)
                    name = "Long Run" if is_long else {
                        "endurance": "Endurance Run", "threshold": "Threshold Intervals",
                        "easy": "Recovery Run", "brick": "Brick Run"}[intensity]
                    desc = random.choice(RUN_DESCRIPTIONS)
                    avg_power = None
                    normalized_power = None
                    avg_pace = f"{fmt_pace(pace_s_km)}/km"

                else:  # strength
                    duration_min = clamp(strength_hours * 60 * random.uniform(0.8, 1.2), 30, 60)
                    IF = random.uniform(0.4, 0.55)
                    tss = round(duration_min / 60 * IF ** 2 * 100, 1)
                    avg_hr = round(clamp(105 + random.uniform(-8, 12), 90, 140))
                    distance_km = 0.0
                    name = "Strength & Conditioning"
                    desc = random.choice(STRENGTH_DESCRIPTIONS)
                    avg_power = None
                    normalized_power = None
                    avg_pace = None

                training_load = round(tss * random.uniform(2.2, 2.9))
                kudos = round(clamp(tss / 3 + random.uniform(0, 15), 3, 60))
                activity_id = next(id_counter)

                activities.append({
                    "id": activity_id,
                    "date": d.isoformat(),
                    "sport": sport,
                    "name": name,
                    "duration_min": round(duration_min),
                    "distance_km": distance_km,
                    "avg_hr": avg_hr,
                    "avg_power": avg_power,
                    "avg_pace": avg_pace,
                    "tss": tss,
                    "if": round(IF, 2),
                    "normalized_power": normalized_power,
                    "training_load": training_load,
                    "strava_kudos": kudos,
                    "description": desc,
                })

                if sport == "strength":
                    laps, records = [], []
                else:
                    is_interval = intensity in ("threshold", "vo2") and not is_long
                    if sport == "bike":
                        output_avg = avg_power
                    elif sport == "run":
                        output_avg = 1000 / pace_s_km
                    else:  # swim
                        output_avg = 100 / pace_100
                    laps, records = synthesize_session_detail(
                        sport, intensity, is_long, duration_min, avg_hr, output_avg, is_interval
                    )
                activity_details[activity_id] = {
                    "workout_id": activity_id,
                    "details": {},
                    "laps": laps,
                    "records": records,
                }

    activities.sort(key=lambda a: (a["date"], SPORT_ID.get(a["sport"], 9)))
    return activities, activity_details


def gen_daily_metrics_and_pmc(activities):
    tss_by_date = {}
    for a in activities:
        tss_by_date[a["date"]] = tss_by_date.get(a["date"], 0) + a["tss"]

    daily_metrics = []
    pmc = []

    ctl, atl = 48.0, 46.0
    hrv_baseline = 82.0
    vo2_run = 56.4
    vo2_bike = 54.2

    d = START_DATE
    day_idx = 0
    while d <= TODAY:
        day_tss = tss_by_date.get(d.isoformat(), 0.0)
        ctl = ctl + (day_tss - ctl) / 42
        atl = atl + (day_tss - atl) / 7
        tsb = round(ctl - atl, 1)

        is_weekend = d.weekday() >= 5
        fatigue = clamp((atl - ctl) / 20, -1.5, 1.5)  # >0 means fatigued

        resting_hr = round(clamp(46 + fatigue * 4 + random.uniform(-2, 2), 40, 58))
        hrv_ms = round(clamp(hrv_baseline - fatigue * 10 + random.uniform(-5, 5), 40, 110))
        sleep_hours = round(clamp(7.3 + (0.4 if is_weekend else 0) + random.uniform(-1.0, 0.8), 5.0, 9.5), 1)
        sleep_score = round(clamp(78 - fatigue * 10 + (sleep_hours - 7.3) * 8 + random.uniform(-6, 6), 35, 98))
        body_battery = round(clamp(70 - fatigue * 20 + (sleep_score - 75) * 0.3 + random.uniform(-8, 8), 8, 98))
        stress_avg = round(clamp(32 + fatigue * 15 - (sleep_score - 75) * 0.15 + random.uniform(-6, 6), 8, 85))
        steps = round(clamp(6200 + (2500 if day_tss > 60 else 0) + random.uniform(-1800, 2000), 1500, 15000))
        training_readiness = round(clamp(
            72 - fatigue * 22 + (hrv_ms - hrv_baseline) * 0.3 + (sleep_score - 75) * 0.25 + random.uniform(-5, 5),
            5, 100
        ))

        if day_idx % 11 == 0:
            vo2_run = clamp(vo2_run + random.uniform(-0.1, 0.15), 54.0, 61.0)
            vo2_bike = clamp(vo2_bike + random.uniform(-0.1, 0.12), 52.0, 58.5)

        if tsb < -22:
            status = "Overreaching"
        elif tsb < -8:
            status = "Productive"
        elif tsb < 5:
            status = "Maintaining" if day_idx < NUM_DAYS - 14 else "Productive"
        elif tsb < 15:
            status = "Maintaining"
        elif ctl < atl - 5:
            status = "Detraining"
        else:
            status = "Recovery"

        daily_metrics.append({
            "date": d.isoformat(),
            "resting_hr": resting_hr,
            "hrv_ms": hrv_ms,
            "sleep_score": sleep_score,
            "sleep_hours": sleep_hours,
            "body_battery": body_battery,
            "stress_avg": stress_avg,
            "steps": steps,
            "training_readiness": training_readiness,
            "vo2max_running": round(vo2_run, 1),
            "vo2max_cycling": round(vo2_bike, 1),
            "training_status": status,
        })
        pmc.append({
            "date": d.isoformat(),
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": tsb,
        })

        prior_tsb = tsb
        d += timedelta(days=1)
        day_idx += 1

    return daily_metrics, pmc


def gen_weekly_summary(activities):
    buckets = {}  # (week_start_iso, sport) -> {hours, distance_km, tss}
    for a in activities:
        d = date.fromisoformat(a["date"])
        wk = week_start(d).isoformat()
        key = (wk, a["sport"])
        b = buckets.setdefault(key, {"hours": 0.0, "distance_km": 0.0, "tss": 0.0})
        b["hours"] += a["duration_min"] / 60
        b["distance_km"] += a["distance_km"]
        b["tss"] += a["tss"]

    rows = []
    for (wk, sport), v in sorted(buckets.items(), key=lambda kv: (kv[0][0], SPORT_ID.get(kv[0][1], 9))):
        rows.append({
            "week": wk,
            "sport": sport,
            "hours": round(v["hours"], 2),
            "distance_km": round(v["distance_km"], 1),
            "tss": round(v["tss"], 1),
        })
    return rows


def main():
    DATA_DIR.mkdir(exist_ok=True)
    detail_dir = DATA_DIR / "activities"
    detail_dir.mkdir(exist_ok=True)

    activities, activity_details = gen_activities()
    daily_metrics, pmc = gen_daily_metrics_and_pmc(activities)
    weekly_summary = gen_weekly_summary(activities)

    (DATA_DIR / "activities.json").write_text(json.dumps(activities, indent=2))
    (DATA_DIR / "daily_metrics.json").write_text(json.dumps(daily_metrics, indent=2))
    (DATA_DIR / "pmc.json").write_text(json.dumps(pmc, indent=2))
    (DATA_DIR / "weekly_summary.json").write_text(json.dumps(weekly_summary, indent=2))

    for activity_id, detail in activity_details.items():
        (detail_dir / f"{activity_id}.json").write_text(json.dumps(detail, indent=2))

    blocks_path = DATA_DIR / "blocks.json"
    if not blocks_path.exists():
        blocks_path.write_text(json.dumps([], indent=2))

    print(f"Generated {len(daily_metrics)} days of daily_metrics -> data/daily_metrics.json")
    print(f"Generated {len(activities)} activities -> data/activities.json")
    print(f"Generated {len(activity_details)} per-activity detail files -> data/activities/")
    print(f"Generated {len(pmc)} days of PMC -> data/pmc.json")
    print(f"Generated {len(weekly_summary)} weekly_summary rows -> data/weekly_summary.json")
    print("data/blocks.json ready (empty array if not already present)")


if __name__ == "__main__":
    main()
