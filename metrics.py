"""
Derived metrics that TrainingPeaks does NOT already give us.

TrainingPeaks' own workout summary/details already provide NP, IF, TSS,
CTL/ATL/TSB, mean-max power/pace curves, and time-in-zone -- see
trainingpeaks_client.py / trainingpeaks_sync.py. This module only computes
what's left: metrics that need the raw per-second stream or lap array, plus
the explicit, editable progression-verdict rules the athlete asked to be able
to see and disagree with.

Inputs throughout are plain dicts as parsed from a FIT file by `fitparse`
(see trainingpeaks_sync.py's `fetch_laps_and_records`) -- i.e. the list
stored under "records" / "laps" in data/activities/{id}.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Efficiency Factor
# ---------------------------------------------------------------------------

def efficiency_factor(power_or_speed: float | None, avg_hr: float | None) -> float | None:
    """EF = normalized power (bike) or normalized graded pace/speed (run) / avg HR.

    Rising EF at the same intensity over weeks is the aerobic-improvement
    signal the athlete cares about most. Returns None if either input is
    missing or avg_hr is zero (can't divide).
    """
    if power_or_speed is None or not avg_hr:
        return None
    return power_or_speed / avg_hr


# ---------------------------------------------------------------------------
# Aerobic decoupling (Pw:HR)
# ---------------------------------------------------------------------------

def _record_value(record: dict, *keys: str) -> float | None:
    for k in keys:
        v = record.get(k)
        if v is not None:
            return v
    return None


def compute_decoupling(records: list[dict], min_duration_s: int = 3600) -> dict | None:
    """First-half vs second-half EF, as a percentage.

    Only meaningful for steady-state sessions over an hour (per the athlete's
    brief) -- callers should still gate on session TYPE (e.g. skip intervals),
    since this function only guards on duration. Returns None if there isn't
    enough clean data (missing HR/power-or-speed, or too short).

    < 5% decoupling: aerobically sound at that intensity.
    > 5%: the intensity isn't yet sustainable at that duration.
    """
    usable = [
        r for r in records
        if r.get("heart_rate") and _record_value(r, "power", "enhanced_speed", "speed") is not None
    ]
    if len(usable) < 20:  # not enough clean samples to trust a split
        return None

    duration_s = len(usable)  # FIT records are ~1 Hz; good enough as a proxy
    if duration_s < min_duration_s:
        return None

    mid = len(usable) // 2
    first_half, second_half = usable[:mid], usable[mid:]

    def half_ef(half: list[dict]) -> float | None:
        outputs = [_record_value(r, "power", "enhanced_speed", "speed") for r in half]
        hrs = [r["heart_rate"] for r in half]
        avg_output = sum(outputs) / len(outputs)
        avg_hr = sum(hrs) / len(hrs)
        return efficiency_factor(avg_output, avg_hr)

    ef_first = half_ef(first_half)
    ef_second = half_ef(second_half)
    if not ef_first or not ef_second:
        return None

    decoupling_pct = (ef_first - ef_second) / ef_first * 100
    return {
        "ef_first_half": round(ef_first, 4),
        "ef_second_half": round(ef_second, 4),
        "decoupling_pct": round(decoupling_pct, 2),
        "aerobically_sound": decoupling_pct < 5.0,
    }


# ---------------------------------------------------------------------------
# Durability: first third vs final third of a long session
# ---------------------------------------------------------------------------

def compute_durability(records: list[dict]) -> dict | None:
    """Output in the final third vs the first third of a session.

    For a 70.3, this matters more than fresh threshold numbers -- a rising
    fresh FTP with worsening late-session output is the red flag this is
    meant to surface. Returns None if there isn't a usable power/speed
    stream.
    """
    usable = [r for r in records if _record_value(r, "power", "enhanced_speed", "speed") is not None]
    if len(usable) < 30:
        return None

    third = len(usable) // 3
    first_third = usable[:third]
    final_third = usable[-third:]

    def avg_output(chunk: list[dict]) -> float:
        vals = [_record_value(r, "power", "enhanced_speed", "speed") for r in chunk]
        return sum(vals) / len(vals)

    first_avg = avg_output(first_third)
    final_avg = avg_output(final_third)
    if not first_avg:
        return None

    fade_pct = (final_avg - first_avg) / first_avg * 100
    return {
        "first_third_avg": round(first_avg, 2),
        "final_third_avg": round(final_avg, 2),
        "fade_pct": round(fade_pct, 2),  # negative = output dropped late in the session
    }


# ---------------------------------------------------------------------------
# Rep-over-rep fade (interval sessions)
# ---------------------------------------------------------------------------

def compute_rep_fade(work_laps: list[dict], value_key: str = "avg_power") -> dict | None:
    """Whether output declined across work reps, as a percentage.

    `work_laps` should already be filtered to just the work intervals (not
    recovery laps) -- that selection is structural knowledge the caller has
    (e.g. from the planned session), not something this function can infer.
    """
    values = [lap.get(value_key) for lap in work_laps if lap.get(value_key) is not None]
    if len(values) < 2:
        return None

    first_rep, last_rep = values[0], values[-1]
    fade_pct = (last_rep - first_rep) / first_rep * 100 if first_rep else None
    return {
        "rep_values": values,
        "first_rep": first_rep,
        "last_rep": last_rep,
        "fade_pct": round(fade_pct, 2) if fade_pct is not None else None,
        "faded": fade_pct is not None and fade_pct < -3.0,  # >3% drop counts as fade
    }


# ---------------------------------------------------------------------------
# Progression verdict -- explicit, editable rules
# ---------------------------------------------------------------------------

@dataclass
class ProgressionRules:
    """Every threshold here is meant to be seen and argued with -- these are
    not hidden model judgments, they're config."""

    min_comparable_sessions: int = 2
    decoupling_ok_threshold_pct: float = 5.0
    stale_after_n_sessions: int = 3
    ef_improvement_threshold_pct: float = 1.0  # smaller than this counts as "no improvement"


DEFAULT_RULES = ProgressionRules()


@dataclass
class SessionOutcome:
    """One comparable prior session's outcome, as fed to evaluate_progression.
    Chronological order (oldest first) is assumed by the caller."""

    completed_at_target: bool
    hr_cost_direction: str  # "falling" | "stable" | "rising"
    decoupling_pct: float | None = None
    ef: float | None = None


def evaluate_progression(sessions: list[SessionOutcome], rules: ProgressionRules = DEFAULT_RULES) -> dict:
    """Ready to progress / hold / stale -- with the rule that produced the call.

    Mirrors the athlete's own stated logic:
      - progress: last 2-3 comparable sessions all hit target, HR cost stable
        or falling, decoupling under the threshold.
      - hold: any recent session faded or HR cost rose.
      - stale: `stale_after_n_sessions` consecutive sessions with no EF/power
        improvement.
    """
    if len(sessions) < rules.min_comparable_sessions:
        return {
            "verdict": "insufficient_data",
            "reason": f"Only {len(sessions)} comparable session(s) on record; need at least "
                      f"{rules.min_comparable_sessions} to call it either way.",
            "rule_applied": "min_comparable_sessions",
        }

    recent = sessions[-3:]

    faded_or_rising = [
        s for s in recent
        if not s.completed_at_target or s.hr_cost_direction == "rising"
    ]
    if faded_or_rising:
        return {
            "verdict": "hold",
            "reason": "At least one of the last sessions didn't hit target or showed rising HR cost "
                      "at the same output -- not ready to add reps, duration, or intensity yet.",
            "rule_applied": "hold_on_fade_or_rising_hr_cost",
        }

    decouplings = [s.decoupling_pct for s in recent if s.decoupling_pct is not None]
    if decouplings and any(d >= rules.decoupling_ok_threshold_pct for d in decouplings):
        return {
            "verdict": "hold",
            "reason": f"Decoupling at or above {rules.decoupling_ok_threshold_pct}% in at least one "
                      "recent session -- the current intensity/duration isn't aerobically settled yet.",
            "rule_applied": "hold_on_decoupling",
        }

    ef_values = [s.ef for s in sessions[-rules.stale_after_n_sessions:] if s.ef is not None]
    if len(ef_values) >= rules.stale_after_n_sessions:
        best_recent = max(ef_values[1:]) if len(ef_values) > 1 else ef_values[0]
        improvement_pct = (best_recent - ef_values[0]) / ef_values[0] * 100 if ef_values[0] else 0
        if improvement_pct < rules.ef_improvement_threshold_pct:
            return {
                "verdict": "stale",
                "reason": f"No EF improvement over the last {rules.stale_after_n_sessions} sessions "
                          f"({improvement_pct:.1f}% change) -- this session type looks due for a "
                          "change of stimulus.",
                "rule_applied": "stale_on_flat_ef",
            }

    return {
        "verdict": "progress",
        "reason": f"Last {len(recent)} comparable session(s) hit target with stable/falling HR cost "
                  f"and decoupling under {rules.decoupling_ok_threshold_pct}%.",
        "rule_applied": "progress_on_clean_recent_block",
    }
