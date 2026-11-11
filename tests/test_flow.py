"""端到端流程测试（模拟模式）：对话 → 重复提问识别 → 诊断 → 掌握记录 → 教师页 → 导出。"""
import unittest

from app import config, diagnosis
from app.main import create_app


def correct_answer(item, step):
    if step == 1:
        return "有错" if item["has_bug"] else "没错"
    if step == 2:
        return item["bug_lines"][0]
    if step == 3:
        kw = item["explain_keywords"]
        return f"原因是{kw['cause'][0]}的问题，{kw['trigger'][0]}的时候就会出错。"
    if step == 4:
        lines = item["code"].split("\n")
        lines[item["bug_lines"][0] - 1] = item["fix"]
        return {"code": "\n".join(lines), "reason": "这样改以后边界和含义都对了，所以有效。"}
    if step == 5:
        return item["variant"]["reference"]
    raise ValueError(step)


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.c = cls.app.test_client()

    def chat(self, sid, msg, code=""):
        r = self.c.post("/api/chat", json={"student_id": sid, "message": msg, "code": code})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json

    def run_diagnosis(self, sid, kp_id, good=True):
        r = self.c.post("/api/diagnosis/start", json={"student_id": sid, "kp_id": kp_id})
        self.assertEqual(r.status_code, 200, r.json)
        d = r.json
        bank = diagnosis.bank()
        for _ in range(40):
            item, step = bank[d["item"]["id"]], d["step"]["step"]
            ans = correct_answer(item, step) if good else "__skip__"
            r = self.c.post("/api/diagnosis/answer",
                            json={"student_id": sid, "session_id": d["session_id"], "step": step, "answer": ans})
            self.assertEqual(r.status_code, 200, r.json)
            d = r.json
            if good:
                self.assertTrue(d["step_result"]["passed"], (item["id"], step, d["step_result"]))
            if d["status"] == "finished":
                return d
        self.fail("诊断没有结束")

    def test_conversation_episodes_and_reask(self):
        sid = "t_reask"
        a = self.chat(sid, "我用for循环求1到n的和，为什么结果总是少加了一个数？", "for (int i = 1; i < n; i++) sum += i;")
        self.assertTrue(a["episode"]["is_new"])
        self.assertEqual(a["episode"]["kp_id"], "c05")
        first_id = a["episode"]["id"]
        b = self.chat(sid, "我觉得是因为i<n，所以最后一个数没加上")
        self.assertFalse(b["episode"]["is_new"])
        self.assertEqual(b["analysis"]["icap"], "constructive")
        c = self.chat(sid, "还有一个问题：数组下标越界是什么意思？")
        self.assertTrue(c["episode"]["is_new"])
        self.assertEqual(c["episode"]["kp_id"], "c06")
        d = self.chat(sid, "为什么我用for循环求1到n的和，结果总是少加一个数？")
        self.assertTrue(d["episode"]["is_new"])
        self.assertEqual(d["episode"]["reask_of"], first_id)
        self.assertIn("回忆", d["reply"])

    def test_answer_seeking_is_passive(self):
        a = self.chat("t_passive", "直接给我冒泡排序的完整代码")
        self.assertEqual(a["analysis"]["help_type"], "answer_seeking")
        self.assertEqual(a["analysis"]["icap"], "passive")

    def test_good_diagnosis_reaches_mastery(self):
        sid = "t_good"
        self.chat(sid, "for循环的循环条件怎么写才不会少算一次？我觉得是i<=n")
        res = self.run_diagnosis(sid, "c05", good=True)["result"]
        self.assertLessEqual(res["n_items"], config.MODEL["max_items"])
        self.assertGreater(res["p_mastered"], 0.5)
        m = self.c.get(f"/api/mastery/{sid}").json["mastery"]
        self.assertEqual(m[0]["kp_id"], "c05")

    def test_bad_diagnosis_is_low_and_stops_early(self):
        res = self.run_diagnosis("t_bad", "c09", good=False)["result"]
        self.assertLess(res["p_mastered"], 0.1)
        self.assertEqual(res["stop_reason"], "confident_not_mastered")
        self.assertLessEqual(res["n_items"], 2)

    def test_second_diagnosis_uses_history_prior(self):
        sid = "t_hist"
        self.run_diagnosis(sid, "c07", good=True)
        r = self.c.post("/api/diagnosis/start", json={"student_id": sid, "kp_id": "c07"}).json
        self.assertIn("上次诊断", r["debug"]["prior"]["base"])
        self.assertGreater(r["debug"]["prior"]["mu0"], 0.3)

    def test_cannot_answer_others_session(self):
        r = self.c.post("/api/diagnosis/start", json={"student_id": "t_owner", "kp_id": "c03"}).json
        bad = self.c.post("/api/diagnosis/answer",
                          json={"student_id": "t_other", "session_id": r["session_id"], "step": 1, "answer": "有错"})
        self.assertEqual(bad.status_code, 403)

    def test_invalid_student_id(self):
        r = self.c.post("/api/chat", json={"student_id": "张三", "message": "hi"})
        self.assertEqual(r.status_code, 400)

    def test_no_items_for_kp(self):
        r = self.c.post("/api/diagnosis/start", json={"student_id": "t_noitem", "kp_id": "c10"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("available_kps", r.json)

    def test_teacher_and_export(self):
        self.run_diagnosis("t_export", "c08", good=True)
        ov = self.c.get("/api/teacher/overview")
        self.assertEqual(ov.status_code, 200)
        self.assertTrue(any(r["student_id"] == "t_export" for r in ov.json["rows"]))
        for name in ("episodes", "turns", "diagnoses", "responses", "ablation"):
            r = self.c.get(f"/api/export/{name}.csv")
            self.assertEqual(r.status_code, 200)
        text = self.c.get("/api/export/ablation.csv").get_data(as_text=True)
        for v in ("flat", "history", "engagement", "both"):
            self.assertIn(v, text)

    def test_teacher_blocked_from_remote_without_token(self):
        r = self.c.get("/api/teacher/overview", environ_base={"REMOTE_ADDR": "10.1.2.3"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
