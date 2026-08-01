"""
Plan page analysis: today's planned session, what's coming up, and adherence
(planned vs actually completed) over a trailing window.

Planned sessions live in data/planned_sessions.json -- written to only by the
Coach Chat write-back tool (see server.py's propose_planned_sessions tool).
There is no separate planning UI; the plan is a byproduct of the coaching
conversation, and this module just reads it back out.

Matching a planned session to a completed one is a heuristic: same date +
same sport. That's a coarse proxy (doesn't check whether the SESSION TYPE was
actually followed), documented here as revisitable once there's a reason to
need something finer.
"""

from __future__ import annotations

from datetime import date, timedelta


def get_planned_for_date(planned_sessions: list[dict], date_str: str) -> dict | None:
    return next((s for s in planned_sessions if s.get("date") == date_str), None)


def get_upcoming(planned_sessions: list[dict], today: date, days: int = 14) -> list[dict]:
    today_str = today.isoformat()
    end_str = (today + timedelta(days=days)).isoformat()
    upcoming = [s for s in planned_sessions if today_str <= s.get("date", "") <= end_str]
    return sorted(upcoming, key=lambda s: (s["date"], s["sport"]))


def compute_adherence(planned_sessions: list[dict], activities: list[dict], today: date, days_back: int = 14) -> dict:
    start_str = (today - timedelta(days=days_back)).isoformat()
    today_str = today.isoformat()
    past_planned = [s for s in planned_sessions if start_str <= s.get("date", "") < today_str]

    completed_keys = {(a.get("date"), a.get("sport")) for a in activities}
    completed = sum(1 for s in past_planned if (s.get("date"), s.get("sport")) in completed_keys)
    missed = len(past_planned) - completed

    return {
        "window_days": days_back,
        "planned_count": len(past_planned),
        "completed_count": completed,
        "missed_count": missed,
        "adherence_pct": round(completed / len(past_planned) * 100, 1) if past_planned else None,
    }


def build_plan_summary(planned_sessions: list[dict], activities: list[dict], today: date) -> dict:
    return {
        "today": today.isoformat(),
        "today_planned": get_planned_for_date(planned_sessions, today.isoformat()),
        "upcoming": get_upcoming(planned_sessions, today),
        "adherence": compute_adherence(planned_sessions, activities, today),
    }
