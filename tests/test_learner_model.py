import json
import unittest

from app import config
from app import learner_model as lm


def load_items(kp=None):
    items = json.loads(config.ITEM_BANK_PATH.read_text(encoding="utf-8"))["items"]
    return [i for i in items if kp is None or i["kp"] == kp]


class LearnerModelTest(unittest.TestCase):
    def setUp(self):
        self.th = lm.grid()
        self.prior = lm.normal_prior(0.0, 1.0, self.th)
        self.item = next(i for i in load_items("c05") if i["id"] == "loop-01")

    def test_grid(self):
        self.assertEqual(len(self.th), 61)
        self.assertAlmostEqual(self.th[0], -3.0)
        self.assertAlmostEqual(self.th[-1], 3.0)

    def test_outcome_probs_sum_to_one(self):
        params = lm.step_params(self.item)
        self.assertEqual(len(params), 5)
        for t in (-2.0, 0.0, 2.5):
            self.assertAlmostEqual(sum(lm.outcome_probs(params, t)), 1.0, places=9)

    def test_update_direction(self):
        params = lm.step_params(self.item)
        base = lm.summarize(self.prior, self.th)["mu"]
        good = lm.summarize(lm.update(self.prior, self.th, params, [(j, True) for j in range(1, 6)]), self.th)
        bad = lm.summarize(lm.update(self.prior, self.th, params, [(1, False)]), self.th)
        self.assertGreater(good["mu"], base + 0.5)
        self.assertLess(bad["mu"], base - 0.5)
        self.assertLess(good["sd"], 1.0)

    def test_clean_item_single_step_with_guessing(self):
        clean = next(i for i in load_items("c05") if not i["has_bug"])
        params = lm.step_params(clean)
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0][2], 0.5)

    def test_select_prefers_informative_items(self):
        items = load_items("c05")
        best, score, scored = lm.select_item(self.prior, self.th, items)
        self.assertTrue(best["has_bug"])
        self.assertGreater(score, 0)
        self.assertEqual(scored[0]["item_id"], best["id"])
        clean_score = next(s for s in scored if s["item_id"] == "loop-04")["evr"]
        self.assertLess(clean_score, score)

    def test_stop_rules(self):
        cfg = config.MODEL
        s = {"sd": 0.3, "p_mastered": 0.5}
        self.assertIsNone(lm.stop_reason(s, 0, None))
        self.assertEqual(lm.stop_reason(s, 1, 0.1), "precise_enough")
        self.assertEqual(lm.stop_reason({"sd": 0.8, "p_mastered": 0.95}, 1, 0.1), "confident_mastered")
        self.assertEqual(lm.stop_reason({"sd": 0.8, "p_mastered": 0.05}, 2, 0.1), "confident_not_mastered")
        self.assertEqual(lm.stop_reason({"sd": 0.8, "p_mastered": 0.5}, cfg["max_items"], 0.1), "max_items")
        self.assertIsNone(lm.stop_reason({"sd": 0.8, "p_mastered": 0.5}, 1, 0.1))

    def test_prior_components(self):
        hist = {"last_diag": {"mu": 1.0, "sd": 0.4, "days_ago": 30}, "reask_count": 2}
        eng = {"engagement_index": 0.9}
        mu_f, sd_f, _ = lm.compute_prior(hist, eng, variant="flat")
        self.assertEqual((mu_f, sd_f), (0.0, 1.0))
        mu_h, sd_h, _ = lm.compute_prior(hist, None, variant="history")
        self.assertAlmostEqual(mu_h, 1.0 - 0.4)          # 重复提问两次，各降 0.2
        self.assertGreater(sd_h, 0.4)                     # 隔了 30 天，不确定性变大
        mu_e, _, _ = lm.compute_prior(None, eng, variant="engagement")
        self.assertGreater(mu_e, 0.0)
        mu_b, _, _ = lm.compute_prior(hist, {"engagement_index": 0.1}, variant="both")
        self.assertLess(mu_b, mu_h)

    def test_profile_fix_step_equals_mastery(self):
        params = lm.step_params(self.item)
        post = lm.update(self.prior, self.th, params, [(1, True), (2, True), (3, False)])
        prof = lm.profile(post, self.th)
        self.assertEqual(len(prof["steps"]), 5)
        self.assertAlmostEqual(prof["steps"][3]["p"], lm.summarize(post, self.th)["p_mastered"], places=3)
        ps = [s["p"] for s in prof["steps"]]
        self.assertEqual(ps, sorted(ps, reverse=True))  # 越往后的维度越难达到

    def test_engagement_index_range(self):
        hi = lm.engagement_index({"n_student_turns": 3, "mean_icap": 3, "attempt_ratio": 1, "answer_seek_ratio": 0})
        lo = lm.engagement_index({"n_student_turns": 3, "mean_icap": 0, "attempt_ratio": 0, "answer_seek_ratio": 1})
        self.assertAlmostEqual(hi, 1.0)
        self.assertAlmostEqual(lo, 0.0)
        self.assertIsNone(lm.engagement_index({"n_student_turns": 0}))


if __name__ == "__main__":
    unittest.main()
