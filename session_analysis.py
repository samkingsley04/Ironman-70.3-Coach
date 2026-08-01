"""
Session-level analysis for the Sessions page: per-activity derived metrics,
comparable-session grouping, and feeding metrics.evaluate_progression.

Comparable-session matching (v1, heuristic): group by sport + a normalized
session name with rep-count/duration prefixes stripped (e.g. "Run Session:
4x4min Threshold" and "Run Session: 6x6min Threshold/VO2" both normalize to
"run session: threshold/vo2"-ish keys). This is a name-based proxy for
matching on workout structure -- TrainingPeaks' own "structure" field on a
workout may encode this more precisely, but that field's shape hasn't been
inspected yet, so this is the documented, revisitable stand-in.

"completed_at_target" / "hr_cost_direction" (inputs metrics.py needs but
TrainingPeaks doesn't hand us directly) are also heuristics, documented
inline where they're derived -- editable/replaceable once we have a real
notion of "target" from the plan.
"""

from __future__ import annotations

import re

from metrics import (
    SessionOutcome,
    compute_decoupling,
    compute_durability,
    compute_rep_fade,
    efficiency_factor,
    evaluate_progression,
)

_REP_PATTERN = re.compile(r"\d+\s*x\s*\d+\s*(min|sec|s|km|m)?", re.IGNORECASE)
_NUM_PATTERN = re.compile(r"\b\d+\b")


def normalize_session_name(name: str) -> str:
    """Strip rep-count/duration tokens so structurally-similar sessions with
    different rep counts or durations group together."""
    stripped = _REP_PATTERN.sub("", name or "")
    stripped = _NUM_PATTERN.sub("", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" :-").lower()
    return stripped


def find_comparable_sessions(activity: dict, all_activities: list[dict], limit: int = 5) -> list[dict]:
    """Same sport + matching normalized name, most recent first, excluding the
    activity itself."""
    key = normalize_session_name(activity.get("name", ""))
    sport = activity.get("sport")
    if not key:
        return []
    matches = [
        a for a in all_activities
        if a.get("id") != activity.get("id")
        and a.get("sport") == sport
        and normalize_session_name(a.get("name", "")) == key
    ]
    matches.sort(key=lambda a: a.get("date") or "", reverse=True)
    return matches[:limit]


def _lap_value_key(sport: str) -> str:
    return "avg_power" if sport == "bike" else "enhanced_avg_speed"


def compute_activity_metrics(activity: dict, detail: dict) -> dict:
    """EF, decoupling, durability, rep-fade for one activity -- everything
    TrainingPeaks' own summary/details endpoints don't already give us."""
    laps = detail.get("laps") or []
    records = detail.get("records") or []
    sport = activity.get("sport")

    output = activity.get("normalized_power") if sport == "bike" else None
    if output is None and sport != "bike" and activity.get("distance_km") and activity.get("duration_min"):
        # crude average speed (m/s) fallback when we don't have a normalized value
        output = activity["distance_km"] * 1000 / (activity["duration_min"] * 60)
    ef = efficiency_factor(output, activity.get("avg_hr"))

    decoupling = compute_decoupling(records) if records else None
    durability = compute_durability(records) if records else None
    rep_fade = compute_rep_fade(laps, value_key=_lap_value_key(sport)) if len(laps) > 1 else None

    return {"ef": ef, "decoupling": decoupling, "durability": durability, "rep_fade": rep_fade}


def _completed_at_target(activity: dict, comparable_durations: list[float], rep_fade: dict | None) -> bool:
    """Heuristic stand-in for 'hit the planned session' -- we don't have an
    explicit numeric target from the plan yet, so this proxies on: didn't
    fade badly across reps, and wasn't cut meaningfully short vs. this
    session type's typical duration."""
    if rep_fade and rep_fade.get("faded"):
        return False
    duration = activity.get("duration_min") or 0
    if comparable_durations:
        median = sorted(comparable_durations)[len(comparable_durations) // 2]
        if median and duration < median * 0.75:
            return False
    return True


def build_progression_verdict(activity: dict, all_activities: list[dict], load_detail) -> dict:
    """Assemble SessionOutcome history for this session's comparable group,
    INCLUDING the session being viewed as the most recent data point (the
    question is "am I ready to progress the next one of these, given how
    the last few -- including this one -- went"), and hand it to
    metrics.evaluate_progression. `load_detail(id)` loads a single activity's
    detail dict (injected so this stays testable without hitting the
    filesystem)."""
    comparable = find_comparable_sessions(activity, all_activities, limit=6)
    comparable_chronological = list(reversed(comparable)) + [activity]  # oldest first, this session last
    durations = [a.get("duration_min") or 0 for a in comparable_chronological]

    outcomes = []
    prior_ef = None
    for a in comparable_chronological:
        detail = load_detail(a["id"])
        m = compute_activity_metrics(a, detail)
        completed = _completed_at_target(a, durations, m["rep_fade"])
        if prior_ef is not None and m["ef"] is not None:
            if m["ef"] > prior_ef * 1.01:
                hr_cost_direction = "falling"  # EF rose -> same output for less HR, or more output for same HR
            elif m["ef"] < prior_ef * 0.99:
                hr_cost_direction = "rising"
            else:
                hr_cost_direction = "stable"
        else:
            hr_cost_direction = "stable"
        prior_ef = m["ef"] if m["ef"] is not None else prior_ef

        decoupling_pct = m["decoupling"]["decoupling_pct"] if m["decoupling"] else None
        outcomes.append(SessionOutcome(
            completed_at_target=completed,
            hr_cost_direction=hr_cost_direction,
            decoupling_pct=decoupling_pct,
            ef=m["ef"],
        ))

    verdict = evaluate_progression(outcomes)
    return {
        "verdict": verdict,
        "comparable_sessions": [
            {"id": a["id"], "date": a["date"], "name": a["name"]}
            for a in comparable  # most-recent-first for display
        ],
    }
