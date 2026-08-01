"""
Progression page analysis: power curve overlay, durability trend, EF trend at
aerobic intensity, threshold trend, swim volume sufficiency check, and a
race-pace benchmark scaffold.

Per the athlete's own stated priority, power curve and durability trend are
the two elements this module gets right first -- everything else here is
secondary. All "what should this be" numbers live in metrics.py / this
module as plain, editable code, not opaque judgments.
"""

from __future__ import annotations

from datetime import date, timedelta

from metrics import compute_durability, efficiency_factor, parse_timestamp

DURATIONS_S = [5, 15, 60, 300, 600, 1200, 2400, 3600, 5400]  # 5s,15s,1m,5m,10m,20m,40m,60m,90m


# ---------------------------------------------------------------------------
# Power / pace curve (mean-maximal)
# ---------------------------------------------------------------------------

def mean_max_curve_single(records: list[dict], sport: str, durations_s: list[int] = DURATIONS_S) -> dict[int, float | None]:
    """Best average output sustained for each target duration in one activity.

    Uses the stream's own timestamps to estimate the average sample interval
    (records aren't guaranteed 1 Hz -- see metrics.compute_decoupling for the
    same reasoning), converts each target duration into an equivalent window
    of samples, then finds the best-average window via a prefix sum.
    """
    output_key = "power" if sport == "bike" else "enhanced_speed"
    usable = [r for r in records if r.get(output_key) is not None]
    result: dict[int, float | None] = {d: None for d in durations_s}
    if len(usable) < 2:
        return result

    values = [r[output_key] for r in usable]
    ts_first = parse_timestamp(usable[0].get("timestamp"))
    ts_last = parse_timestamp(usable[-1].get("timestamp"))
    if ts_first is not None and ts_last is not None and ts_last > ts_first:
        avg_interval = (ts_last - ts_first) / (len(usable) - 1)
    else:
        avg_interval = 1.0
    if avg_interval <= 0:
        avg_interval = 1.0

    n = len(values)
    prefix = [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)

    for d in durations_s:
        window = max(1, round(d / avg_interval))
        if window > n:
            continue
        best = max(
            (prefix[start + window] - prefix[start]) / window
            for start in range(0, n - window + 1)
        )
        result[d] = round(best, 1)
    return result


def compute_power_curve(records_list: list[list[dict]], sport: str, durations_s: list[int] = DURATIONS_S) -> dict[int, float | None]:
    """Combines multiple activities' curves by taking the best at each duration."""
    best: dict[int, float | None] = {d: None for d in durations_s}
    for records in records_list:
        curve = mean_max_curve_single(records, sport, durations_s)
        for d in durations_s:
            v = curve[d]
            if v is not None and (best[d] is None or v > best[d]):
                best[d] = v
    return best


def build_power_curve_overlay(activities: list[dict], load_detail, today: date, sport: str = "bike") -> dict:
    """All-time-best vs last-42-days vs where-the-curve-stood-6-months-ago, so
    the athlete can see whether the curve is actually shifting up or just
    holding its all-time shape."""
    sport_activities = [a for a in activities if a.get("sport") == sport]

    def records_for(acts: list[dict]) -> list[list[dict]]:
        out = []
        for a in acts:
            detail = load_detail(a["id"])
            recs = detail.get("records") or []
            if recs:
                out.append(recs)
        return out

    last_42_cutoff = (today - timedelta(days=42)).isoformat()
    six_mo_cutoff = (today - timedelta(days=182)).isoformat()

    all_time = records_for(sport_activities)
    last_42_days = records_for([a for a in sport_activities if a.get("date", "") >= last_42_cutoff])
    six_months_ago = records_for([a for a in sport_activities if a.get("date", "") <= six_mo_cutoff])

    return {
        "sport": sport,
        "durations_s": DURATIONS_S,
        "all_time_best": compute_power_curve(all_time, sport),
        "last_42_days": compute_power_curve(last_42_days, sport),
        "six_months_ago": compute_power_curve(six_months_ago, sport) if six_months_ago else None,
    }


# ---------------------------------------------------------------------------
# Durability trend
# ---------------------------------------------------------------------------

def compute_durability_trend(activities: list[dict], load_detail, sport: str, min_duration_min: int = 90) -> list[dict]:
    """Final-third-vs-first-third fade for every long session of this sport,
    oldest first -- a rising all-time power curve with worsening late-session
    fade is exactly the red flag this is meant to surface."""
    candidates = sorted(
        (a for a in activities if a.get("sport") == sport and (a.get("duration_min") or 0) >= min_duration_min),
        key=lambda a: a.get("date", ""),
    )
    trend = []
    for a in candidates:
        detail = load_detail(a["id"])
        durability = compute_durability(detail.get("records") or [])
        if durability is None:
            continue
        trend.append({
            "date": a["date"],
            "activity_id": a["id"],
            "name": a.get("name"),
            "fade_pct": durability["fade_pct"],
        })
    return trend


# ---------------------------------------------------------------------------
# Efficiency Factor trend (aerobic intensity only)
# ---------------------------------------------------------------------------

def _activity_output(a: dict) -> float | None:
    if a.get("sport") == "bike":
        return a.get("normalized_power") or a.get("avg_power")
    distance_km, duration_min = a.get("distance_km"), a.get("duration_min")
    if not distance_km or not duration_min:
        return None
    return distance_km * 1000 / (duration_min * 60)  # m/s


def compute_ef_trend(activities: list[dict], sport: str, max_if: float = 0.85) -> list[dict]:
    """EF over time, restricted to aerobic/easy efforts (IF <= max_if) -- a
    rising EF trend at the SAME intensity is the aerobic-improvement signal,
    so mixing in hard sessions would make the trend meaningless."""
    candidates = sorted(
        (a for a in activities if a.get("sport") == sport and a.get("if") is not None and a["if"] <= max_if),
        key=lambda a: a.get("date", ""),
    )
    trend = []
    for a in candidates:
        ef = efficiency_factor(_activity_output(a), a.get("avg_hr"))
        if ef is None:
            continue
        trend.append({"date": a["date"], "activity_id": a["id"], "ef": round(ef, 4)})
    return trend


# ---------------------------------------------------------------------------
# Threshold trend
# ---------------------------------------------------------------------------

def compute_threshold_trend(thresholds_history: list[dict]) -> dict:
    """Chronological pass-through of data/thresholds.json. With only one
    synced entry so far, this can't be a trend yet -- has_trend tells the
    frontend to show current values instead of pretending there's a chart."""
    ordered = sorted(thresholds_history, key=lambda t: t.get("date", ""))
    return {
        "entries": ordered,
        "latest": ordered[-1] if ordered else None,
        "has_trend": len(ordered) >= 2,
    }


# ---------------------------------------------------------------------------
# Swim volume sufficiency check
# ---------------------------------------------------------------------------

def compute_swim_volume_check(activities: list[dict], today: date, weeks: int = 8, min_hours_per_week: float = 1.0) -> dict:
    """Explicit call-out of whether there's enough swim volume for the EF
    trend above to mean anything, per the athlete's own requirement -- rather
    than silently plotting a noisy trend from 2 pool sessions a month."""
    cutoff = (today - timedelta(weeks=weeks)).isoformat()
    swims = [a for a in activities if a.get("sport") == "swim" and a.get("date", "") >= cutoff]
    total_hours = sum((a.get("duration_min") or 0) for a in swims) / 60
    avg_hours_per_week = total_hours / weeks
    sufficient = avg_hours_per_week >= min_hours_per_week

    note = (
        "Swim volume is high enough that the EF/pace trend is meaningful."
        if sufficient else
        f"Only ~{round(avg_hours_per_week, 2)}h/week of swimming over the last {weeks} weeks -- "
        "below the threshold where a trend means much. Treat any swim numbers here as noisy."
    )
    return {
        "weeks": weeks,
        "session_count": len(swims),
        "avg_hours_per_week": round(avg_hours_per_week, 2),
        "min_hours_per_week": min_hours_per_week,
        "sufficient": sufficient,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Race-pace benchmarks (scaffold -- no tagging UI yet)
# ---------------------------------------------------------------------------

def build_race_pace_benchmarks(race_pace_tags: list[dict]) -> dict:
    if not race_pace_tags:
        return {
            "tagged": False,
            "sessions": [],
            "note": "No sessions tagged as race-pace efforts yet -- tagging isn't built yet.",
        }
    return {"tagged": True, "sessions": race_pace_tags}


# ---------------------------------------------------------------------------
# Combined summary
# ---------------------------------------------------------------------------

def build_progression_summary(
    activities: list[dict],
    load_detail,
    thresholds_history: list[dict],
    today: date,
    race_pace_tags: list[dict] | None = None,
) -> dict:
    return {
        "today": today.isoformat(),
        "power_curve": build_power_curve_overlay(activities, load_detail, today, sport="bike"),
        "durability_trend": {
            "bike": compute_durability_trend(activities, load_detail, "bike"),
            "run": compute_durability_trend(activities, load_detail, "run"),
        },
        "ef_trend": {
            "bike": compute_ef_trend(activities, "bike"),
            "run": compute_ef_trend(activities, "run"),
            "swim": compute_ef_trend(activities, "swim"),
        },
        "threshold_trend": compute_threshold_trend(thresholds_history),
        "swim_volume_check": compute_swim_volume_check(activities, today),
        "race_pace_benchmarks": build_race_pace_benchmarks(race_pace_tags or []),
    }
