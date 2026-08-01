"""Unit tests for metrics.py, run with: python -m unittest tests.test_metrics"""

import unittest

from metrics import (
    DEFAULT_RULES,
    ProgressionRules,
    SessionOutcome,
    compute_decoupling,
    compute_durability,
    compute_rep_fade,
    efficiency_factor,
    evaluate_progression,
)


class TestEfficiencyFactor(unittest.TestCase):
    def test_known_values(self):
        # 200W at 140bpm -> EF ~1.4286
        self.assertAlmostEqual(efficiency_factor(200, 140), 200 / 140, places=6)

    def test_missing_inputs_return_none(self):
        self.assertIsNone(efficiency_factor(None, 140))
        self.assertIsNone(efficiency_factor(200, None))
        self.assertIsNone(efficiency_factor(200, 0))


class TestDecoupling(unittest.TestCase):
    def _make_records(self, n, power, hr):
        return [{"power": power, "heart_rate": hr} for _ in range(n)]

    def test_known_decoupling(self):
        # First half: 200W @ 140bpm -> EF = 1.42857
        # Second half: 190W @ 145bpm -> EF = 1.31034
        # decoupling = (1.42857 - 1.31034) / 1.42857 * 100 = 8.2765...%
        records = self._make_records(50, 200, 140) + self._make_records(50, 190, 145)
        result = compute_decoupling(records, min_duration_s=10)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["decoupling_pct"], 8.28, places=1)
        self.assertFalse(result["aerobically_sound"])  # > 5%

    def test_stable_effort_is_aerobically_sound(self):
        # Identical output/HR both halves -> 0% decoupling
        records = self._make_records(50, 200, 140) + self._make_records(50, 200, 140)
        result = compute_decoupling(records, min_duration_s=10)
        self.assertAlmostEqual(result["decoupling_pct"], 0.0, places=6)
        self.assertTrue(result["aerobically_sound"])

    def test_too_short_returns_none(self):
        records = self._make_records(50, 200, 140) + self._make_records(50, 190, 145)
        self.assertIsNone(compute_decoupling(records, min_duration_s=3600))

    def test_missing_data_returns_none(self):
        records = [{"power": 200} for _ in range(30)]  # no heart_rate
        self.assertIsNone(compute_decoupling(records, min_duration_s=10))


class TestDurability(unittest.TestCase):
    def test_known_fade(self):
        # first third: 250W avg, final third: 225W avg -> fade = -10%
        records = (
            [{"power": 250} for _ in range(10)]
            + [{"power": 240} for _ in range(10)]
            + [{"power": 225} for _ in range(10)]
        )
        result = compute_durability(records)
        self.assertIsNotNone(result)
        self.assertEqual(result["first_third_avg"], 250)
        self.assertEqual(result["final_third_avg"], 225)
        self.assertAlmostEqual(result["fade_pct"], -10.0, places=6)

    def test_negligible_data_returns_none(self):
        self.assertIsNone(compute_durability([{"power": 250} for _ in range(5)]))


class TestRepFade(unittest.TestCase):
    def test_known_fade(self):
        laps = [{"avg_power": v} for v in [300, 295, 290, 280]]
        result = compute_rep_fade(laps)
        self.assertAlmostEqual(result["fade_pct"], (280 - 300) / 300 * 100, places=2)
        self.assertTrue(result["faded"])

    def test_stable_reps_not_faded(self):
        laps = [{"avg_power": v} for v in [300, 301, 299, 300]]
        result = compute_rep_fade(laps)
        self.assertFalse(result["faded"])

    def test_single_rep_returns_none(self):
        self.assertIsNone(compute_rep_fade([{"avg_power": 300}]))


class TestEvaluateProgression(unittest.TestCase):
    def test_insufficient_data(self):
        result = evaluate_progression([SessionOutcome(True, "stable")])
        self.assertEqual(result["verdict"], "insufficient_data")

    def test_progress_on_clean_block(self):
        sessions = [
            SessionOutcome(True, "falling", decoupling_pct=3.0, ef=1.40),
            SessionOutcome(True, "stable", decoupling_pct=3.5, ef=1.42),
            SessionOutcome(True, "stable", decoupling_pct=4.0, ef=1.45),
        ]
        result = evaluate_progression(sessions)
        self.assertEqual(result["verdict"], "progress")

    def test_hold_on_missed_target(self):
        sessions = [
            SessionOutcome(True, "stable", decoupling_pct=3.0, ef=1.40),
            SessionOutcome(False, "stable", decoupling_pct=3.5, ef=1.38),
        ]
        result = evaluate_progression(sessions)
        self.assertEqual(result["verdict"], "hold")
        self.assertEqual(result["rule_applied"], "hold_on_fade_or_rising_hr_cost")

    def test_hold_on_rising_hr_cost(self):
        sessions = [
            SessionOutcome(True, "stable", decoupling_pct=3.0, ef=1.40),
            SessionOutcome(True, "rising", decoupling_pct=3.0, ef=1.38),
        ]
        result = evaluate_progression(sessions)
        self.assertEqual(result["verdict"], "hold")

    def test_hold_on_high_decoupling(self):
        sessions = [
            SessionOutcome(True, "stable", decoupling_pct=6.0, ef=1.40),
            SessionOutcome(True, "stable", decoupling_pct=5.5, ef=1.41),
        ]
        result = evaluate_progression(sessions)
        self.assertEqual(result["verdict"], "hold")
        self.assertEqual(result["rule_applied"], "hold_on_decoupling")

    def test_stale_on_flat_ef(self):
        sessions = [
            SessionOutcome(True, "stable", decoupling_pct=2.0, ef=1.40),
            SessionOutcome(True, "stable", decoupling_pct=2.0, ef=1.401),
            SessionOutcome(True, "stable", decoupling_pct=2.0, ef=1.399),
        ]
        result = evaluate_progression(sessions)
        self.assertEqual(result["verdict"], "stale")

    def test_custom_rules_object(self):
        strict_rules = ProgressionRules(decoupling_ok_threshold_pct=2.0)
        sessions = [
            SessionOutcome(True, "stable", decoupling_pct=3.0, ef=1.40),
            SessionOutcome(True, "stable", decoupling_pct=3.0, ef=1.45),
        ]
        result = evaluate_progression(sessions, rules=strict_rules)
        self.assertEqual(result["verdict"], "hold")  # 3.0% now exceeds the strict 2.0% bar

    def test_default_rules_are_a_dataclass_instance(self):
        self.assertIsInstance(DEFAULT_RULES, ProgressionRules)


if __name__ == "__main__":
    unittest.main()
