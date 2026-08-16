"""
Weekly training summary report.

Reads the real synced data (data/activities.json, daily_metrics.json,
pmc.json) and produces a compact Markdown report for a trailing window --
meant to be pasted straight into a separate coaching conversation, not read
in a browser. Data only, no interpretation: raw numbers and computed
metrics (TSS, EF, decoupling, durability, ramp rate), no readings, labels,
or judgments layered on top -- see metrics.py / today_analysis.py /
session_analysis.py for how each number is derived.

Usage:
    python weekly_report.py                  # last 7 days ending today
    python weekly_report.py --days 14        # last 14 days
    python weekly_report.py --end 2026-07-25 # last 7 days ending that date
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from session_analysis import compute_activity_metrics
from today_analysis import compute_ramp_rate

DATA_DIR = Path(__file__).parent / "data"
DETAIL_DIR = DATA_DIR / "activities"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_json(name: str, default=None):
    path = DATA_DIR / name
    if not path.exists():
        return default if default is not None else []
    return json.loads(path.read_text())


def load_detail(activity_id) -> dict:
    path = DETAIL_DIR / f"{activity_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def week_activities(activities: list[dict], start: date, end: date) -> list[dict]:
    return [a for a in activities if start.isoformat() <= a.get("date", "") <= end.isoformat()]


def summarize_by_sport(activities: list[dict]) -> dict:
    by_sport: dict[str, dict] = {}
    for a in activities:
        sport = a.get("sport", "other")
        s = by_sport.setdefault(sport, {"count": 0, "hours": 0.0, "tss": 0.0, "distance_km": 0.0})
        s["count"] += 1
        s["hours"] += (a.get("duration_min") or 0) / 60
        s["tss"] += a.get("tss") or 0
        s["distance_km"] += a.get("distance_km") or 0
    return by_sport


def health_averages(daily_metrics: list[dict], start: date, end: date) -> dict:
    week = [d for d in daily_metrics if start.isoformat() <= d.get("date", "") <= end.isoformat()]

    def avg(key: str):
        vals = [d[key] for d in week if d.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "resting_hr": avg("resting_hr"),
        "hrv_ms": avg("hrv_ms"),
        "sleep_hours": avg("sleep_hours"),
        "body_battery": avg("body_battery"),
        "days_with_data": len(week),
    }


def _session_line(a: dict) -> list[str]:
    detail = load_detail(a["id"])
    computed = compute_activity_metrics(a, detail) if detail else {}

    bits = []
    if a.get("duration_min"):
        bits.append(f"{a['duration_min']}min")
    if a.get("tss"):
        bits.append(f"TSS {round(a['tss'], 1)}")
    if a.get("if"):
        bits.append(f"IF {a['if']}")
    if a.get("avg_hr"):
        bits.append(f"avg HR {a['avg_hr']}")
    if a.get("sport") == "bike" and a.get("normalized_power"):
        bits.append(f"NP {a['normalized_power']}W")
    elif a.get("avg_pace"):
        bits.append(f"pace {a['avg_pace']}")
    if computed.get("decoupling"):
        bits.append(f"decoupling {computed['decoupling']['decoupling_pct']}%")
    if computed.get("durability"):
        bits.append(f"durability {computed['durability']['fade_pct']}%")
    if computed.get("rep_fade"):
        bits.append(f"rep fade {computed['rep_fade']['fade_pct']}%")

    lines = [f"- **{a['date']} ({a['sport']}) {a['name']}** -- {' · '.join(bits)}"]
    if a.get("description"):
        lines.append(f"  _{a['description']}_")
    return lines


def build_report(end: date, days: int = 7) -> str:
    start = end - timedelta(days=days - 1)

    activities = load_json("activities.json", [])
    daily_metrics = load_json("daily_metrics.json", [])
    pmc = load_json("pmc.json", [])

    week_acts = sorted(week_activities(activities, start, end), key=lambda a: a.get("date", ""))
    by_sport = summarize_by_sport(week_acts)
    health = health_averages(daily_metrics, start, end)
    ramp = compute_ramp_rate(pmc)
    latest_pmc = pmc[-1] if pmc else None

    lines = [f"# Training Summary: {start.isoformat()} to {end.isoformat()}", ""]

    lines.append("## Load by discipline")
    if by_sport:
        for sport in sorted(by_sport):
            s = by_sport[sport]
            piece = f"- **{sport}**: {s['count']} session(s), {round(s['hours'], 1)}h, {round(s['tss'], 1)} TSS"
            if s["distance_km"]:
                piece += f", {round(s['distance_km'], 1)}km"
            lines.append(piece)
    else:
        lines.append("- No sessions logged this week.")
    lines.append("")

    lines.append("## Fitness (PMC)")
    if latest_pmc:
        lines.append(
            f"- CTL (fitness): {latest_pmc.get('ctl')}, ATL (fatigue): {latest_pmc.get('atl')}, "
            f"TSB (form): {latest_pmc.get('tsb')}"
        )
    if ramp.get("weekly_ctl_change") is not None:
        lines.append(f"- Ramp rate (trailing 4 weeks): {ramp['weekly_ctl_change']} CTL/week")
    lines.append("")

    lines.append("## Health (weekly averages)")
    if health["days_with_data"]:
        lines.append(f"- Resting HR: {health['resting_hr']} bpm")
        lines.append(f"- HRV: {health['hrv_ms']} ms")
        lines.append(f"- Sleep: {health['sleep_hours']} hrs")
        lines.append(f"- Body Battery: {health['body_battery']}")
    else:
        lines.append("- No health data logged this week.")
    lines.append("")

    lines.append("## Sessions this week")
    if week_acts:
        for a in week_acts:
            lines.extend(_session_line(a))
    else:
        lines.append("- Nothing logged.")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", type=str, default=None, help="last day of the report window, YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=7, help="how many days back to include (default 7)")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    report = build_report(end, days=args.days)

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"weekly-{end.isoformat()}.md"
    out_path.write_text(report)

    print(report)
    print(f"(also saved to {out_path})")


if __name__ == "__main__":
    main()
