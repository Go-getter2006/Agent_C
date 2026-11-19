"""对话工作流：用演示数据走几条完整的路径。没有配置大模型时走规则和固定模板。"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select

from app import config, db, llm, mastery, pipeline, review
from app import models as m
from app.chat import context, validate, workflow
from app.chat.policy import PolicyInput, decide
from app.chat.validate import Draft

UID = "2500000001"


def texts(msg) -> str:
    return "".join(b.get("text", "") for b in msg.content["blocks"] if b["t"] == "p")


def codes(msg) -> list[dict]:
    return [b for b in msg.content["blocks"] if b["t"] == "code"]


def last_step(s) -> m.ChatStep:
    return s.scalars(select(m.ChatStep).order_by(m.ChatStep.id.desc())).first()


# ------------------------------------------------------------ 规则表
def test_rule_table_order():
    base = dict(bound=True, has_code=True, has_failure=True, diag_confidence=0.9)
    assert decide(PolicyInput(intent="OTHER")).rule_id == "R01"
    assert decide(PolicyInput(intent="CONCEPT")).rule_id == "R02"
    assert decide(PolicyInput(intent="TASK", bound=True)).rule_id == "R03"
    assert decide(PolicyInput(intent="DEBUG")).rule_id == "R04"
    assert decide(PolicyInput(intent="TASK")).rule_id == "R04"            # 问题意，但不知道是哪道题
    assert decide(PolicyInput(intent="DEBUG", **{**base, "diag_confidence": 0.3})).rule_id == "R05"
    assert decide(PolicyInput(intent="REPLY", answer_correct=True, **base)).rule_id == "R06"
    d = decide(PolicyInput(intent="STUCK", **base))
    assert (d.rule_id, d.help_level, d.kind) == ("R07", "H3", "stuck")
    d = decide(PolicyInput(intent="REPLY", answer_correct=False, failed_replies=1, last_help="H2", help_count=2, **base))
    assert d.rule_id == "R07"                                             # 追问过一次仍答不上
    d = decide(PolicyInput(intent="REPLY", answer_correct=False, last_help="H0", help_count=1, **base))
    assert (d.rule_id, d.help_level) == ("R08", "H1")                     # 升一级
    d = decide(PolicyInput(intent="REPLY", action="hint", last_help="H1", help_count=2, **base))
    assert (d.rule_id, d.help_level, d.reveal_level) == ("R08", "H2", "乙")
    d = decide(PolicyInput(intent="REPLY", action="hint", last_help="H2", help_count=3, **base))
    assert (d.rule_id, d.kind) == ("R08", "stuck")                        # 升到 H3 时按卡点处理
    d = decide(PolicyInput(intent="DEBUG", no_progress=True, last_help="H0", help_count=1, **base))
    assert (d.rule_id, d.help_level) == ("R09", "H2")
    d = decide(PolicyInput(intent="ANSWER", base_level="基本掌握", **base))
    assert (d.rule_id, d.help_level) == ("R10", "H1")
    d = decide(PolicyInput(intent="ANSWER", base_level="待加强", **base))
    assert (d.rule_id, d.help_level) == ("R10", "H2")
    d = decide(PolicyInput(intent="DEBUG", willingness="HIGH", base_level="已掌握", **base))
    assert (d.rule_id, d.help_level, d.reveal_level) == ("R11", "H0", "甲")
    d = decide(PolicyInput(intent="DEBUG", willingness="UNKNOWN", base_level="待加强", **base))
    assert (d.rule_id, d.help_level) == ("R11", "H1")
    assert decide(PolicyInput(intent="REPLY", action="think", **base)).kind == "ack"


# ------------------------------------------------------------ 演示数据里的三段对话
def test_seeded_story(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        msgs = workflow.thread_messages(s, UID, (sid, 406))
        assert len({x.failure_submission_id for x in msgs if x.failure_submission_id}) == 3
        msgs = msgs[-5:]
        assert [x.role for x in msgs] == ["agent", "student", "agent", "student", "agent"]
        # 第 2 次提交又犯了在「最大差值」里改正过的错：智能体主动先说，摘出当时改对的代码
        assert msgs[0].content["proactive"]["kind"] == "recurrence" and "最大差值" in texts(msgs[0])
        old = codes(msgs[0])[0]
        assert old["caption"].startswith("你在「最大差值」里改对后的代码") and any("a[0]" in x for x in old["lines"])
        first, second = msgs[2], msgs[4]
        block = codes(first)[0]
        assert block["caption"] == "你的第 3 次提交，第 8 到 12 行" and block["start"] == 8 and block["mark"] == [10]
        assert block["lines"][-3:] == ["int max = 0;", "for (i = 0; i < n; i++)", "    if (a[i] > max) max = a[i];"]
        # 提醒过以后又交了一次、错的还是同一处：往上帮一级，直接指出位置
        assert "`max` 的初值" in texts(first)
        assert "有关系。假如输入是 `-5 -2 -8`" in texts(second) and "第 12 行一次都不会替换" in texts(second)
        assert second.content["actions"] == ["think", "hint", "tell"]
        steps = s.scalars(select(m.ChatStep).where(m.ChatStep.problem_id == 406, m.ChatStep.user_id == UID)
                          .order_by(m.ChatStep.id)).all()
        assert [(x.intent, x.rule_id, x.help_level, x.reveal_level) for x in steps] == [
            ("DEBUG", "R08", "H1", "甲"), ("REPLY", "R08", "H2", "乙")]
        st = steps[0]
        assert st.bind_by == "thread" and st.root_error_code == "TY09" and st.root_skill_code == "C06.03.02"
        # 演示故事里他在“最值”上反复出同一个错（OJ 上没过的提交也是证据），所以是“待加强”
        assert st.willingness == "HIGH" and st.base_level == "待加强" and st.check_result["ok"]
        assert st.prompt_version and st.workflow_version and st.evidence_card["situation"] == "全是负数"
        # 昨天那一回合：给了示例代码后通过，出了一道小题
        rounds = s.scalars(select(m.ChatRound).where(m.ChatRound.problem_id == 410)).all()
        assert [(r.status, r.end_reason, r.max_help_level, r.quiz_triggered) for r in rounds] == [
            ("closed", "通过", "H3", False)]
        stuck = s.scalars(select(m.StuckPoint).order_by(m.StuckPoint.id)).all()
        assert [(p.problem_id, p.skill_code, p.nth, p.resolution, p.passed_after) for p in stuck] == [
            (301, "C02.01.02", 1, "example_given", None), (410, "C06.03.02", 1, "example_given", True)]
        assert mastery.skill_state(s, UID, "C06.03.02").level == "待加强"
        # 整数范围已经做过一次间隔复习，早先的卡点仍保留在证据中。
        state = mastery.skill_state(s, UID, "C02.01.02")
        assert state.events >= 2 and state.f > 0


def test_second_time_stuck_recall_then_blank(demo):
    sid = demo["server_id"]
    thread = (sid, 406)
    with db.session_scope() as s:
        # 点“再给点提示”：已经到过 H2，再升就要给代码了；他昨天在同一个技能上卡过 → 先让他回忆
        out = workflow.handle(s, UID, thread, action="hint")
        assert texts(out[0]) == "再给点提示"
        reply = texts(out[1])
        assert "上次「最大差值」（输入 n 个数，输出最大值和最小值的差）你也卡在这里" in reply and not codes(out[1])
        st = last_step(s)
        assert (st.rule_id, st.help_level, st.reply["flow"], st.stuck_count) == ("R08", "H0", "recall", 2)
        sp = s.get(m.StuckPoint, st.stuck_point_id)
        assert (sp.nth, sp.skill_code, sp.resolution) == (2, "C06.03.02", "pending")
        ev = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.source_key == f"stuck:{sp.id}")).all()
        assert [(e.event_type, e.success, e.weight) for e in ev] == [("stuck_again", False, 1.5)]

        # 想不起来 → 拿出上次给他的那段代码，把关键一行挖空
        out = workflow.handle(s, UID, thread, "忘了。")
        block = codes(out[1])[0]
        assert block["caption"] == "上次给你的那段" and block["lines"][0] == "______" and block["mark"] == [1]
        assert block["lines"][1:] == ["for (i = 1; i < n; i++)", "    if (a[i] > max) max = a[i];"]
        assert "空着的那一行" in texts(out[1])
        st = last_step(s)
        assert (st.intent, st.help_level, st.reply["flow"]) == ("STUCK", "H2", "blank")
        assert s.get(m.StuckPoint, sp.id).resolution == "blank_filled"

        # 补上了
        out = workflow.handle(s, UID, thread, "int max = a[0];")
        assert texts(out[1]).startswith("对")
        rnd = s.scalars(select(m.ChatRound).where(m.ChatRound.problem_id == 406, m.ChatRound.user_id == UID)).one()
        assert rnd.max_help_level == "H2" and rnd.recall_stage == "done"
        item = s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == UID, m.ReviewItem.skill_code == "C06.03.02")).one()
        assert item.state == "active" and item.lapses >= 1   # 第二次卡住：在复习里记一次遗忘


def test_recall_succeeds(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        workflow.handle(s, UID, (sid, 406), action="hint")
        out = workflow.handle(s, UID, (sid, 406), "想起来了，初值应该用第一个数 a[0]，不能用 0")
        assert texts(out[1]).startswith("对，就是这个思路")
        sp = s.scalars(select(m.StuckPoint).order_by(m.StuckPoint.id.desc())).first()
        assert sp.resolution == "recalled"


def test_think_button_and_understood(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        out = workflow.handle(s, UID, (sid, 406), action="think")
        assert texts(out[1]).startswith("好，你先想") and out[1].content["actions"] == ["hint", "tell"]
        st = last_step(s)
        assert (st.help_level, st.willingness, st.analysis_source) == ("NA", "HIGH", "button")
        out = workflow.handle(s, UID, (sid, 406), "懂了，我去改")
        rnd = s.scalars(select(m.ChatRound).where(m.ChatRound.problem_id == 406, m.ChatRound.user_id == UID)).one()
        assert (rnd.status, rnd.end_reason) == ("closed", "学生表示懂了") and out[1].content["actions"] == []


def test_first_time_stuck_gets_example_and_tell_escalates(demo):
    sid = demo["server_id"]
    thread = (sid, 408)
    with db.session_scope() as s:
        out = workflow.handle(s, UID, thread, "为什么运行错误？", submission_key=None)
        st = last_step(s)
        assert (st.rule_id, st.help_level, st.root_error_code, st.root_skill_code) == ("R11", "H1", "AR02", "C06.01.02")
        assert "数据很多的时候，数组放不下" in texts(out[1]) and codes(out[1])[0]["mark"] == [6]
        out = workflow.handle(s, UID, thread, action="tell")
        st = last_step(s)
        assert (st.rule_id, st.help_level, st.willingness) == ("R10", "H2", "LOW") and "数组要按题目给的最大数据量来开" in texts(out[1])
        out = workflow.handle(s, UID, thread, action="tell")
        st = last_step(s)
        assert (st.help_level, st.reply["flow"]) == ("H3", "example") and out[1].content["actions"] == []
        sp = s.get(m.StuckPoint, st.stuck_point_id)
        assert (sp.nth, sp.resolution, sp.skill_code) == (1, "example_given", "C06.01.02")
        assert mastery.skill_state(s, UID, "C06.01.02").f > 0.99


def test_ask_why_binds_that_submission(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        subs = s.scalars(select(m.Submission).where(
            m.Submission.user_id == UID, m.Submission.problem_id == 406).order_by(m.Submission.submission_id)).all()
        ce = subs[0]
        workflow.close_round(s, workflow.open_round(s, UID), "换题", db.now())
        out = workflow.handle(s, UID, (sid, 406), workflow.ASK_WHY_TEXT, submission_key=f"s{sid}-{ce.submission_id}")
        st = last_step(s)
        assert (st.bind_by, st.submission_id, st.root_error_code) == ("ask_why", ce.submission_id, "CE01")
        assert "编译没有通过" in texts(out[1]) and codes(out[1])[0]["caption"].startswith("你的第 1 次提交")
        trig = s.scalars(select(m.Trigger).where(m.Trigger.kind == "submission_priority")).all()
        assert [t.target_id for t in trig] == [str(ce.submission_id)]


def test_general_thread_binding_and_other_intents(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        out = workflow.handle(s, UID, None, "数组逆序那道题为什么一直运行错误？")
        st = last_step(s)
        assert (st.bind_by, st.problem_id) == ("name", 408)
        out = workflow.handle(s, UID, None, "%d 和 %ld 有什么区别？")
        st = last_step(s)
        assert (st.intent, st.rule_id) == ("CONCEPT", "R02") and "`%ld` 对应 `long`" in texts(out[1])
        out = workflow.handle(s, UID, None, "你好")
        assert last_step(s).rule_id == "R01" and "学习上的问题" in texts(out[1])
        # 最近 30 分钟只有「成绩最高分」没通过：自动绑定到它
        key, sub, by, cands = context.bind(s, UID, None, None, "为什么一直错？", "", db.now())
        assert (key, by) == ((sid, 406), "recent")
        # 很久没有提交：对不上是哪道题，就问
        later = db.now() + dt.timedelta(hours=5)
        out = workflow.handle(s, UID, None, "为什么一直错？", now=later)
        assert last_step(s).rule_id == "R04" and "哪一道题" in texts(out[1])
        # 别的班的题、没布置的题：绑定不上
        assert context.bind(s, UID, (sid, 601), None, "x", "", db.now())[0] is None
        assert context.bind(s, UID, (sid, 999), None, "x", "", db.now())[0] is None
        assert context.bind(s, UID, None, None, "交换两个数那题怎么做", "", later)[0] is None


def test_pass_closes_round_and_queues_review(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        last = s.scalars(select(m.Submission).where(m.Submission.user_id == UID, m.Submission.problem_id == 406)
                         .order_by(m.Submission.submission_id.desc())).first()
        new_id = s.scalar(select(m.Submission.submission_id).order_by(m.Submission.submission_id.desc())) + 1
        s.add(m.Submission(server_id=sid, submission_id=new_id, user_id=UID, problem_id=406, contest_id=501,
                           class_id=31, submit_time=db.now(), judgement="AC", judgement_text="正确", points=100,
                           detail_status="done"))
        s.flush()
        pipeline.process_submission(s, sid, new_id)
        rnd = s.scalars(select(m.ChatRound).where(m.ChatRound.problem_id == 406, m.ChatRound.user_id == UID)).one()
        assert (rnd.status, rnd.end_reason, rnd.quiz_triggered) == ("closed", "通过", False)
        ev = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.source_key == f"pass:{sid}:{new_id}")).all()
        assert {e.event_type for e in ev} == {"pass_after_h2"} and {e.weight for e in ev} == {0.2}
        assert {e.skill_code for e in ev} == {"C06.03.02", "C06.02.01", "C06.03.03", "C05.04.03"}
        msgs = workflow.thread_messages(s, UID, (sid, 406))
        assert all(msg.kind != "quiz" for msg in msgs)
        ri = s.scalar(select(m.ReviewItem).where(m.ReviewItem.user_id == UID, m.ReviewItem.skill_code == "C06.03.02"))
        assert ri.trigger_submission_id == new_id and review.available_at(ri) > db.now()
        pipeline.process_submission(s, sid, new_id)   # 重复处理：不重复记证据，不重复出题
        assert len(workflow.thread_messages(s, UID, (sid, 406))) == len(msgs)
        del last


def test_no_message_leaks_testcase_data(demo):
    """对话里的任何内容都不含测试点的原始输入、标准输出。"""
    sid = demo["server_id"]
    with db.session_scope() as s:
        for pid in (406, 408, 301):
            workflow.handle(s, UID, (sid, pid), "为什么错？")
            workflow.handle(s, UID, (sid, pid), action="tell")
            workflow.handle(s, UID, (sid, pid), action="tell")
        blob = " ".join(json.dumps(x.content, ensure_ascii=False) for x in s.scalars(select(m.ChatMessage)).all())
        blob = " ".join(blob.split())
        checked = 0
        for tc in s.scalars(select(m.Testcase)).all():
            for raw in (tc.input_bytes, tc.output_bytes):
                for line in raw.decode("utf-8").splitlines():
                    line = " ".join(line.split())
                    if len(line.split()) >= 3:
                        checked += 1
                        assert line not in blob, f"泄露了测试点数据：{line[:40]}"
        assert checked > 20


# ------------------------------------------------------------ 回复校验
def test_validate_checks():
    source = "\n".join(f"line{i};" for i in range(1, 15))
    testcases = [{"standardInput": "6\n-12 -7 -30 -7 -19 -25\n", "standardOutput": "-7\n", "programOutput": "0\n"}]
    common = dict(help_level="H0", reveal_level="甲", kind="help", source=source, testcases=testcases,
                  reference=None, current_title="成绩最高分", visible_titles={"最大差值": "输入 n 个数，输出最大值和最小值的差"})

    def problems(draft, **kw):
        return validate.check(draft, **{**common, **kw})

    ok = Draft(before="看你这几行：", quote=(10, 12, [10]), after="如果输入的数全是负数，会发生什么？")
    assert problems(ok) == []
    assert any("原始数据" in p for p in problems(Draft(before="比如输入 -12 -7 -30 -7 -19 -25 的时候就不对。"), reveal_level="乙", help_level="H2"))
    assert any("哪类情况" in p for p in problems(Draft(before="比如输入 3 5 的时候。")))
    assert problems(Draft(before="假如输入是 `-5 -2 -8`，最后输出 0。"), reveal_level="乙", help_level="H2") == []
    assert any("第 40 行" in p for p in problems(Draft(before="看第 40 行。", quote=(10, 12, [10]))))
    assert any("没有的名字" in p for p in problems(Draft(before="`total` 没有初始化。", quote=(10, 12, []))))
    assert any("不能给新写的代码" in p for p in problems(Draft(before="这样写：", code="int max = a[0];")))
    assert any("最多 8 行" in p for p in problems(Draft(before="示例：", code="\n".join(f"x{i} = {i};" for i in range(9))),
                                                 help_level="H3", reveal_level="乙", kind="stuck"))
    assert any("题号" in p for p in problems(Draft(before="和第 406 题一样。")))
    assert any("没有带简述" in p for p in problems(Draft(before="上次「最大差值」你也卡在这里。")))
    assert any("看不到的题" in p for p in problems(Draft(before="和「交换两个数」（用指针参数交换两个整数）一样。")))
    assert problems(Draft(before="上次「最大差值」（输入 n 个数，输出最大值和最小值的差）你也卡在这里。")) == []
    assert any("内部用语" in p for p in problems(Draft(before="这是 TY09 类的错误。")))
    assert any("内部用语" in p for p in problems(Draft(before="根据你的学情，先看初值。")))
    assert any("emoji" in p for p in problems(Draft(before="加油 \U0001F389")))
    assert any("太长" in p for p in problems(Draft(before="讲" * 151)))
    assert problems(Draft(before="讲" * 200), kind="concept", help_level="NA") == []
    assert any("没有附代码块" in p for p in problems(Draft(before="看你这几行，第 3 行有问题吗？")))
    ref = "#include <stdio.h>\nint main()\n{\nint max = a[0];\nfor (i = 1; i < n; i++)\nif (a[i] > max) max = a[i];\nreturn 0;\n}"
    whole = "int max = a[0];\nfor (i = 1; i < n; i++)\nif (a[i] > max) max = a[i];"
    assert any("太像" in p for p in problems(Draft(before="示例：", code=whole), help_level="H3", reveal_level="乙",
                                            kind="stuck", reference=ref))


# ------------------------------------------------------------ 有大模型时
def test_llm_reply_is_validated_and_falls_back(demo):
    sid = demo["server_id"]
    calls = []

    def backend(messages, json_mode, max_tokens, temperature):
        system = messages[0]["content"]
        calls.append(system[:12])
        if "分析" in system[:20]:
            return json.dumps({"intent": "DEBUG", "skillCode": "C06.03.02", "hasAttempt": True,
                               "icap": "constructive", "answerCorrect": None, "understood": False}), 50, 20
        # 写回复的大模型两次都乱来：编了测试数据，还带了内部编码
        return json.dumps({"before": "这是 TY09。测试数据是 -12 -7 -30 -7 -19 -25，你输出了 0。", "quote": None,
                           "code": None, "after": ""}, ensure_ascii=False), 200, 60

    with db.session_scope() as s:
        workflow.close_round(s, workflow.open_round(s, UID), "换题", db.now())
    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            out = workflow.handle(s, UID, (sid, 406), "还是有一组不过，我检查了循环，没看出问题。")
            st = last_step(s)
            assert st.analysis_source == "llm" and st.skill_code == "C06.03.02"
            assert (st.reply_source, st.retries) == ("template", 2) and st.check_result["ok"]
            assert "TY09" not in texts(out[1]) and "-12" not in texts(out[1])
            assert codes(out[1])[0]["start"] == 8 and codes(out[1])[0]["mark"] == [10]
            sent = json.dumps([c for c in calls], ensure_ascii=False)
            assert len(calls) == 3 and sent
    finally:
        llm.set_backend(None)


def test_llm_good_reply_used_and_sees_failed_tests(demo):
    sid = demo["server_id"]
    seen = []

    def backend(messages, json_mode, max_tokens, temperature):
        seen.append("\n".join(x["content"] for x in messages))
        if "分析" in messages[0]["content"][:20]:
            return json.dumps({"intent": "DEBUG", "skillCode": "", "hasAttempt": False, "icap": "active",
                               "answerCorrect": None, "understood": False}), 50, 20
        return json.dumps({"before": "循环没有问题。看你这几行：", "quote": {"from": 10, "to": 12, "mark": [10]},
                           "code": None, "after": "成绩全是负数的时候，`max` 最后会是多少？"}, ensure_ascii=False), 200, 60

    with db.session_scope() as s:
        workflow.close_round(s, workflow.open_round(s, UID), "换题", db.now())
    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            out = workflow.handle(s, UID, (sid, 406), "有一组不过，是循环写错了吗？")
            st = last_step(s)
            assert (st.reply_source, st.retries, st.model_version) == ("llm", 0, config.DEEPSEEK_MODEL)
            assert texts(out[1]).startswith("循环没有问题") and out[1].content["actions"] == ["think", "hint", "tell"]
        everything = "\n".join(seen)
        # 大模型能看到没通过的那组输入（用来判断错在哪），但看不到学号和姓名
        assert "-12 -7 -30" in everything and "绝不能把这些数据告诉学生" in everything
        assert UID not in everything and "林晓" not in everything
        assert "全是负数" in everything
    finally:
        llm.set_backend(None)


def test_proactive_message_rules(demo):
    """主动消息：只对刚交的提交；这道题上已经说过话就不再插嘴；摘出的是改对的那几行。"""
    import datetime as dt

    from app.chat import proactive

    sid = demo["server_id"]
    with db.session_scope() as s:
        sub = s.get(m.Submission, (sid, 537201))
        assert proactive.on_failed_submission(s, sub) is None          # 之后已经在对话了
        assert proactive.on_failed_submission(s, sub, now=sub.submit_time + dt.timedelta(hours=5)) is None  # 太旧
    before = ["int max = 0;", "for (i = 0; i < n; i++)", "    if (a[i] > max) max = a[i];", "printf(\"%d\", max);"]
    after = ["int max = a[0];", "for (i = 1; i < n; i++)", "    if (a[i] > max) max = a[i];", "printf(\"%d\", max);"]
    start, lines, marks = proactive.fix_excerpt(before, after, hint_line=1)
    assert start == 1 and lines[0] == "int max = a[0];" and marks == [1, 2]


def test_model_evidence_keeps_long_outputs_and_missing_values():
    expected = "prefix " * 500 + "EXPECTED_END"
    actual = "prefix " * 500 + "ACTUAL_END"
    evidence = {
        "userId": UID, "userName": "测试生", "password": "unused-secret",
        "problemText": "输入整数，输出其和。", "compileOutput": "main.c: warning: value unused",
        "testCases": [{"testcaseId": i, "standardInput": f"INPUT_{i}", "standardOutput": expected,
                       "programOutput": actual, "passed": False} for i in range(6)] + [
                           {"testcaseId": 7, "programOutput": None},
                           {"testcaseId": 8, "programOutput": ""}],
    }
    data = llm.evidence_for_model(evidence, UID, "测试生")
    assert len(data["testCases"]) == 8
    assert data["testCases"][5]["standardOutput"] == expected
    assert data["testCases"][5]["programOutput"] == actual
    assert data["testCases"][6]["programOutput"] is None
    assert data["testCases"][7]["programOutput"] == ""
    assert "userId" not in data and "password" not in data

    from app.catalog import ProblemInfo
    from app.chat import reply
    from app.chat.policy import Decision

    seen = []

    def backend(messages, *_args):
        seen.append(messages[1]["content"])
        return json.dumps({"before": "先检查边界条件下的变量变化。"}, ensure_ascii=False), 0, 0

    ctx = context.Ctx(user_id=UID, user_name="测试生", problem=ProblemInfo(1, 406, "测试题", ""),
                      source="int value;\n" * 600 + "// FULL_SOURCE_END", evidence=evidence)
    llm.set_backend(backend)
    try:
        reply.generate_llm(ctx, Decision("R11", "H1", "甲", "help"), "help", "为什么错了？", [])
        assert all(marker in seen[-1] for marker in ("INPUT_5", "EXPECTED_END", "ACTUAL_END", "FULL_SOURCE_END"))
        assert UID not in seen[-1] and "测试生" not in seen[-1]
    finally:
        llm.set_backend(None)


def test_llm_test_data_leak_is_rejected(demo):
    sid = demo["server_id"]

    def backend(messages, json_mode, max_tokens, temperature):
        if "分析" in messages[0]["content"][:20]:
            return json.dumps({"intent": "DEBUG", "hasAttempt": True}), 20, 10
        return json.dumps({"before": "测试输入是 -12 -7 -30，你照着算。", "code": None}, ensure_ascii=False), 30, 10

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            workflow.close_round(s, workflow.open_round(s, UID), "换题", db.now())
            out = workflow.handle(s, UID, (sid, 406), "为什么错了？")
            assert "-12 -7 -30" not in texts(out[1])
            assert last_step(s).reply_source == "template"
    finally:
        llm.set_backend(None)
