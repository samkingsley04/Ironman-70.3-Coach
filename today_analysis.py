"""
Today page analysis: block/week position, race countdown, CTL/ATL/TSB ramp
rate, discipline balance vs a configurable target split, and intensity
distribution vs a configurable model (default polarized 80/20).

All the "what should this be" numbers (target split, intensity model, race
date) live in data/plan_config.json -- editable, not asserted as truth.
"""

from __future__ import annotations

from datetime import date, timedelta


def find_current_block(blocks: list[dict], today: date) -> dict | None:
    today_str = today.isoformat()
    for b in blocks:
        if b.get("start_date", "") <= today_str <= b.get("end_date", ""):
            return b
    return None


def days_to_race(race_date_str: str, today: date) -> int:
    return (date.fromisoformat(race_date_str) - today).days


def compute_ramp_rate(pmc: list[dict], weeks: int = 4) -> dict:
    """CTL change per week over the last N weeks, with the athlete's own
    stated reading: ~3-7 TSS/day/week is a normal build, above 8 is
    aggressive, negative during a taper is intended (a red flag outside one).
    """
    needed = weeks * 7 + 1
    if len(pmc) < needed:
        return {"weekly_ctl_change": None, "reading": "insufficient_data"}

    latest = pmc[-1]
    past = pmc[-needed]
    if latest.get("ctl") is None or past.get("ctl") is None:
        return {"weekly_ctl_change": None, "reading": "insufficient_data"}

    weekly_change = (latest["ctl"] - past["ctl"]) / weeks
    if weekly_change < 0:
        reading = "negative -- expected during a taper, a red flag otherwise"
    elif weekly_change <= 7:
        reading = "normal build (3-7 TSS/day/week is typical)"
    elif weekly_change <= 8:
        reading = "borderline aggressive"
    else:
        reading = "aggressive -- above 8 TSS/day/week, watch for overreaching"

    return {"weekly_ctl_change": round(weekly_change, 2), "reading": reading}


def compute_discipline_balance(activities: list[dict], today: date, target_split: dict, weeks: int = 4) -> dict:
    cutoff = (today - timedelta(weeks=weeks)).isoformat()
    recent = [a for a in activities if a.get("date", "") >= cutoff]

    totals: dict[str, dict] = {}
    for a in recent:
        sport = a.get("sport", "other")
        t = totals.setdefault(sport, {"hours": 0.0, "tss": 0.0})
        t["hours"] += (a.get("duration_min") or 0) / 60
        t["tss"] += a.get("tss") or 0

    total_hours = sum(t["hours"] for t in totals.values()) or 1
    actual_split = {sport: round(t["hours"] / total_hours, 3) for sport, t in totals.items()}

    # Negative = below target, positive = above. Only compares disciplines the
    # target split actually tracks (e.g. not strength).
    deltas = {
        sport: round(actual_split.get(sport, 0.0) - target, 3)
        for sport, target in target_split.items()
    }
    furthest_below = min(deltas, key=lambda s: deltas[s]) if deltas else None

    return {
        "last_n_weeks": weeks,
        "totals": {s: {"hours": round(t["hours"], 1), "tss": round(t["tss"], 1)} for s, t in totals.items()},
        "actual_split": actual_split,
        "target_split": target_split,
        "deltas": deltas,
        "furthest_below_target": furthest_below,
    }


def _bucket_if(if_value: float | None) -> str:
    """Session-level intensity bucket, weighted by the WHOLE session's
    duration -- a proxy for true time-in-zone. TrainingPeaks' real
    timeInPowerZones/timeInHeartRateZones/timeInSpeedZones (already fetched
    into every activity detail file by trainingpeaks_sync.py) would give a
    precise per-second answer, but that field's nested shape hasn't been
    inspected against a live payload yet -- this is the documented,
    revisitable stand-in until it is."""
    if if_value is None:
        return "moderate"
    if if_value < 0.75:
        return "easy"
    if if_value <= 0.90:
        return "moderate"
    return "hard"


def compute_intensity_distribution(activities: list[dict], today: date, weeks: int = 4) -> dict:
    cutoff = (today - timedelta(weeks=weeks)).isoformat()
    recent = [a for a in activities if a.get("date", "") >= cutoff and a.get("sport") != "strength"]

    by_sport: dict[str, dict] = {}
    for a in recent:
        sport = a.get("sport", "other")
        totals = by_sport.setdefault(sport, {"easy": 0.0, "moderate": 0.0, "hard": 0.0})
        totals[_bucket_if(a.get("if"))] += a.get("duration_min") or 0

    result = {}
    for sport, minutes in by_sport.items():
        total = sum(minutes.values()) or 1
        result[sport] = {
            "easy_pct": round(minutes["easy"] / total * 100, 1),
            "moderate_pct": round(minutes["moderate"] / total * 100, 1),
            "hard_pct": round(minutes["hard"] / total * 100, 1),
        }
    return result


def build_today_summary(blocks: list[dict], pmc: list[dict], activities: list[dict], plan_config: dict, today: date) -> dict:
    current_block = find_current_block(blocks, today)
    latest_pmc = pmc[-1] if pmc else None

    return {
        "today": today.isoformat(),
        "current_block": current_block,
        "goal_race": plan_config["goal_race"],
        "days_to_race": days_to_race(plan_config["goal_race"]["date"], today),
        "pmc": latest_pmc,
        "ramp_rate": compute_ramp_rate(pmc),
        "discipline_balance": compute_discipline_balance(
            activities, today, plan_config["discipline_target_split"]
        ),
        "intensity_distribution": {
            "by_sport": compute_intensity_distribution(activities, today),
            "model": plan_config["intensity_model"],
        },
        "planned_session": None,  # populated once the Plan page exists
    }
