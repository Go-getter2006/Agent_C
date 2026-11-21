"""题目标注：1 个主技能 + 最多 4 个次技能、每个测试点考什么、一两句话的题目简述。

需要一份正确代码作参照：先用同学的通过提交（XDOJ 已经用全部测试点验证过）；
没有时让大模型写一份，用全部测试点在沙箱里跑通了才算数。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config, llm, pdftext
from .. import models as m
from ..sandbox import run_program
from ..sandbox.precheck import check_reference_solution
from ..taxonomy import KNOWLEDGE_POINTS, course_prompt, is_skill_code, kp_of_skill, skills_for_course
from ..xdoj.assemble import source_text
from ..xdoj.util import decode_bytes, json_hash, sha256

log = logging.getLogger("labeling.problems")


def problem_content_hash(s: Session, server_id: int, problem_id: int) -> str:
    text = s.get(m.ProblemText, (server_id, problem_id))
    cases = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id).order_by(m.Testcase.testcase_id)).all()
    # 题面识别出来的文字也算进去：同一个 PDF 重新识别（以前是乱码）后要重新标注
    return json_hash([text.file_hash if text else "", sha256(pdftext.usable_text(text).encode("utf-8")),
                      [(c.testcase_id, c.io_hash) for c in cases]])


def _same_output(a: str, b: str) -> bool:
    norm = lambda t: [line.rstrip() for line in (t or "").replace("\r\n", "\n").rstrip().split("\n")]  # noqa: E731
    return norm(a) == norm(b)


def verify_solution(s: Session, server_id: int, problem_id: int, source: str) -> bool:
    """用全部测试点跑一遍，全对才算数。"""
    if check_reference_solution(source) is not None:
        return False
    cases = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id)).all()
    cases = [c for c in cases if c.input_bytes is not None and c.output_bytes is not None]
    if not cases:
        return False
    inputs = [decode_bytes(c.input_bytes, c.input_encoding)[0] or "" for c in cases]
    result = run_program(source, inputs, cpu_seconds=2, memory_mb=128)
    if result.status != "ok" or len(result.runs) != len(cases):
        return False
    for run, case in zip(result.runs, cases):
        want = decode_bytes(case.output_bytes, case.output_encoding)[0] or ""
        if run.status != "ok" or not _same_output(run.stdout, want):
            return False
    return True


def reference_solution(s: Session, server_id: int, problem_id: int) -> str | None:
    """取一份正确代码：库里验证过的 → 同学的通过提交 → 大模型写并跑通。"""
    saved = s.scalar(select(m.ReferenceSolution).where(
        m.ReferenceSolution.server_id == server_id, m.ReferenceSolution.problem_id == problem_id,
        m.ReferenceSolution.verified.is_(True)).order_by(m.ReferenceSolution.id.desc()))
    if saved is not None:
        return saved.source_code
    peers = s.scalars(select(m.Submission.submission_id).where(
        m.Submission.server_id == server_id, m.Submission.problem_id == problem_id,
        m.Submission.judgement == "AC", m.Submission.detail_status == "done"
    ).order_by(m.Submission.submission_id.desc()).limit(5)).all()
    best = None
    for sid in peers:
        text = source_text(s, server_id, sid)
        if text and (best is None or len(text) < len(best)):
            best = text
    if best is not None:
        s.add(m.ReferenceSolution(server_id=server_id, problem_id=problem_id, origin="peer",
                                  source_code=best, verified=True))
        return best
    if not llm.available():
        return None
    statement = pdftext.usable_text(s.get(m.ProblemText, (server_id, problem_id)))
    if not statement:
        return None
    try:
        code = llm.call_json(
            "reference_solution",
            "你是 C 语言课程的助教。根据题面写一份正确、朴素的 C 程序，只用标准库。"
            '输出 JSON：{"code":"完整的 C 源码"}',
            pdftext.for_llm(statement),
            lambda d: str(d["code"]) if isinstance(d.get("code"), str) and "main" in d["code"] else (_ for _ in ()).throw(ValueError("没有给出完整程序")),
            retries=1, max_tokens=1500)
    except llm.LLMUnavailable:
        return None
    ok = verify_solution(s, server_id, problem_id, code)
    s.add(m.ReferenceSolution(server_id=server_id, problem_id=problem_id, origin="llm", source_code=code, verified=ok))
    return code if ok else None


_SYSTEM = """你是 C 语言课程的助教，任务是标注一道编程题考什么。只能从给出的技能表里选编码。输出 JSON：
{"brief":"一两句话说这题要干什么，不超过30字，不出现题号",
 "primary":"C06.03.02","secondary":["C06.02.01"],
 "testcases":[{"id":1397,"skill":"C06.03.02","situation":"全是负数","ordinary":false}]}
要求：primary 正好 1 个；secondary 最多 4 个，只列这道题专门要求、做不好就过不了的技能（比如保留两位小数、
按指定的分隔符输出、读到输入结束）；几乎每道题都会顺带用到的基本写法（读一个整数、输出一个整数后换行、声明变量）不要列。
每个测试点都要给：skill 是它主要考的技能，
situation 是不超过 8 个字的“哪类情况”（如“只有一个数”“全是负数”“数据很多”），不能照抄测试数据；
普通数据的 ordinary 填 true，situation 写“普通数据”。"""


def save_problem_label(s: Session, server_id: int, problem_id: int, brief: str, primary: str,
                       secondary: list[str], testcases: list[dict], source: str) -> m.ProblemLabel:
    """保存一版标注：旧的不删，标为非当前；版本号加一。"""
    prev = s.scalars(select(m.ProblemLabel).where(
        m.ProblemLabel.server_id == server_id, m.ProblemLabel.problem_id == problem_id
    ).order_by(m.ProblemLabel.version.desc())).first()
    s.execute(update(m.ProblemLabel).where(
        m.ProblemLabel.server_id == server_id, m.ProblemLabel.problem_id == problem_id).values(is_current=False))
    label = m.ProblemLabel(
        server_id=server_id, problem_id=problem_id, version=(prev.version + 1) if prev else 1, is_current=True,
        brief=brief[:255], primary_skill=primary, secondary_skills=[c for c in secondary if c != primary][:4],
        source=source, taxonomy_version=config.TAXONOMY_VERSION, labeler_version=config.LABELER_VERSION,
        content_hash=problem_content_hash(s, server_id, problem_id))
    s.add(label)
    s.flush()
    for t in testcases:
        s.add(m.TestcaseLabel(problem_label_id=label.id, server_id=server_id, testcase_id=int(t["id"]),
                              skill_code=t["skill"], situation=str(t.get("situation", ""))[:64],
                              is_ordinary=bool(t.get("ordinary"))))
    return label


# XDOJ 自带的类别、标签、作业标题、题目标题里的关键词 → 技能。程序确定的兜底标注：大模型还没标（没配、题面还没
# 整理好、标注失败）时用它，做对的题照样记成证据、知识图谱照样知道作业覆盖了哪些知识点。大模型标好以后取代它。
# 越具体的放越前面：第一个对上的是主技能。
TAG_SKILLS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("二维数组", "矩阵"), "C07.01.01"),
    (("排序",), "C06.05.01"),
    (("二分",), "C06.04.03"),
    (("查找", "搜索"), "C06.04.01"),
    (("最大值", "最小值", "最值", "最高分", "最低", "最大", "最小"), "C06.03.03"),
    (("平均",), "C06.03.05"),
    (("字符串", "字符数组"), "C08.05.01"),
    (("大小写", "字母", "字符"), "C08.04.01"),
    (("一维数组", "数组"), "C06.02.01"),
    (("结构体",), "C11.01.01"),
    (("指针",), "C10.01.01"),
    (("递归",), "C09.06.01"),
    (("函数",), "C09.01.01"),
    (("素数", "质数"), "C12.04.01"),
    (("公约数", "公倍数"), "C12.04.02"),
    (("回文", "反转", "逆序数"), "C12.03.02"),
    (("数位", "各位"), "C12.03.01"),
    (("进制",), "C12.06.01"),
    (("递推", "斐波那契", "数列"), "C12.05.01"),
    (("枚举", "穷举"), "C12.01.01"),
    (("模拟",), "C12.02.01"),
    (("嵌套循环", "双重循环", "多重循环", "图形", "打印"), "C05.06.01"),
    (("累加", "累乘", "求和", "阶乘", "计数"), "C05.05.01"),
    (("多组",), "C05.08.01"),
    (("循环",), "C05.03.01"),
    (("switch",), "C04.04.01"),
    (("多分支", "else if"), "C04.02.01"),
    (("分支", "选择", "判断"), "C04.01.01"),
    (("浮点", "精度", "小数"), "C02.02.01"),
    (("溢出", "大数", "取值范围"), "C02.01.01"),
    (("基本计算", "顺序结构", "顺序", "计算", "公式"), "C03.01.03"),
    (("输入输出", "格式"), "C01.03.01"),
)


def tag_skills(texts: list[str]) -> list[str]:
    """按 TAG_SKILLS 从这些文字里认出技能，最多三个，第一个是主技能。"""
    out: list[str] = []
    for text in texts:   # 先认标签（最具体），再认类别、作业标题、题目标题
        low = (text or "").lower()
        for words, skill in TAG_SKILLS:
            if skill not in out and any(w.lower() in low for w in words):
                out.append(skill)
    return out[:3]


def tag_label(s: Session, server_id: int, problem_id: int) -> m.ProblemLabel | None:
    """按 XDOJ 的标签、类别、作业标题、题目标题兜底标注一道题（来源记为 tags）。认不出就返回 None。"""
    problem = s.get(m.Problem, (server_id, problem_id))
    if problem is None:
        return None
    tags = list(s.scalars(select(m.ProblemTag.tag).where(
        m.ProblemTag.server_id == server_id, m.ProblemTag.problem_id == problem_id)).all())
    contests = list(s.scalars(select(m.Contest.title).join(
        m.ContestProblem, (m.ContestProblem.server_id == m.Contest.server_id)
        & (m.ContestProblem.contest_id == m.Contest.contest_id)).where(
        m.ContestProblem.server_id == server_id, m.ContestProblem.problem_id == problem_id)).all())
    skills = tag_skills(tags + [problem.category or ""] + [problem.title or ""] + contests)
    if not skills:
        return None
    label = save_problem_label(s, server_id, problem_id, problem.title or "", skills[0], skills[1:], [], "tags")
    label.content_hash = ""   # 不是按题面标的：大模型能标的时候一定重标
    return label


def ensure_label(s: Session, server_id: int, problem_id: int) -> m.ProblemLabel | None:
    """这道题的当前标注：先试大模型；大模型标不了时用 XDOJ 的标签兜底。"""
    from .labeler import current_problem_label

    label = annotate_problem(s, server_id, problem_id)
    if label is None:
        label = current_problem_label(s, server_id, problem_id) or tag_label(s, server_id, problem_id)
    return label


def label_assigned(s: Session, limit: int = 20) -> int:
    """作业里还没有任何标注的题，按标签兜底标上（后台空闲时自动做，不需要大模型）。返回标了几道。"""
    from .labeler import current_problem_label

    rows = s.execute(select(m.ContestProblem.server_id, m.ContestProblem.problem_id).distinct()).all()
    done = 0
    for server_id, problem_id in rows:
        if done >= limit:
            break
        if current_problem_label(s, server_id, problem_id) is None and tag_label(s, server_id, problem_id) is not None:
            done += 1
    return done


def annotate_problem(s: Session, server_id: int, problem_id: int, force: bool = False) -> m.ProblemLabel | None:
    """标注一道题。题面和测试点没变、已经标过时直接返回。没有大模型时返回 None（留到以后标）。"""
    from .labeler import current_problem_label

    pdftext.prepare(s, server_id, problem_id)   # PDF 题面先交给大模型整理（每个文件一次），标注用整理好的
    current = current_problem_label(s, server_id, problem_id)
    if current is not None and not force and current.content_hash == problem_content_hash(s, server_id, problem_id):
        return current
    if not llm.available():
        return current
    problem = s.get(m.Problem, (server_id, problem_id))
    text = s.get(m.ProblemText, (server_id, problem_id))
    if problem is None or not pdftext.usable_text(text):  # 乱码、还没识别出来的题面不拿去标注
        return current
    cases = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id
    ).order_by(m.Testcase.seq, m.Testcase.testcase_id)).all()
    solution = reference_solution(s, server_id, problem_id)
    tags = s.scalars(select(m.ProblemTag.tag).where(
        m.ProblemTag.server_id == server_id, m.ProblemTag.problem_id == problem_id)).all()
    case_ids = {c.testcase_id for c in cases}

    def validate(d: dict):
        primary = str(d.get("primary", ""))
        if not is_skill_code(primary):
            raise ValueError(f"主技能 {primary} 不在技能表里")
        secondary = [str(c) for c in (d.get("secondary") or [])][:4]
        for code in secondary:
            if not is_skill_code(code):
                raise ValueError(f"次技能 {code} 不在技能表里")
        brief = str(d.get("brief", "")).strip()
        if not brief or len(brief) > 60:
            raise ValueError("简述为空或太长")
        out = []
        for t in d.get("testcases") or []:
            tid = t.get("id")
            if tid not in case_ids:
                continue
            if not is_skill_code(str(t.get("skill", ""))):
                raise ValueError(f"测试点 {tid} 的技能不在技能表里")
            out.append({"id": tid, "skill": t["skill"], "situation": str(t.get("situation", ""))[:16],
                        "ordinary": bool(t.get("ordinary"))})
        if len(out) != len(case_ids):
            raise ValueError("每个测试点都要标")
        return brief, primary, secondary, out

    server = s.get(m.Server, server_id)
    course_skills = skills_for_course(server.course if server else "C")
    skill_table = "\n".join(f"{code} {item[0]}（{KNOWLEDGE_POINTS[kp_of_skill(code)]}）" for code, item in course_skills.items())
    # 测试点完整给出；没取到的是 null，和“空输出”分开（和写回复、标注提交时的证据一样）
    case_text = json.dumps([
        {"testcaseId": c.testcase_id,
         "standardInput": decode_bytes(c.input_bytes, c.input_encoding)[0] if c.input_bytes is not None else None,
         "standardOutput": decode_bytes(c.output_bytes, c.output_encoding)[0] if c.output_bytes is not None else None}
        for c in cases], ensure_ascii=False)
    user = (f"【技能表】\n{skill_table}\n\n【题目】{problem.title}\n【XDOJ 的类别和标签】{problem.category} {' '.join(tags)}\n"
            f"【题面】\n{pdftext.for_llm(pdftext.usable_text(text))}\n\n【正确代码】\n{solution or '（暂无）'}\n\n【测试点】\n{case_text}")
    try:
        brief, primary, secondary, tcs = llm.call_json("label_problem", course_prompt(_SYSTEM, server.course if server else "C"), user, validate,
                                                       retries=1, max_tokens=1200)
    except llm.LLMUnavailable:
        llm.flush_records(s)
        return current
    llm.flush_records(s)
    new = save_problem_label(s, server_id, problem_id, brief, primary, secondary, tcs, "llm")
    if current is not None:
        from .. import mastery

        mastery.follow_relabel(s, server_id, problem_id, new)   # 以前的“通过”证据跟着新标注走
    return new


_relabel_tried: dict[tuple[int, int], float] = {}


def relabel_changed(s: Session, limit: int = 5) -> int:
    """技能表改过以后（config.RELABEL_SKILLS 的意思变窄了），用到这些技能的旧标注在后台用大模型重新标一次；
    没用到的只把版本号改成现在的。没有大模型时不动；标不成的一小时内不再试。返回试了几道。"""
    import time

    if not llm.available():
        return 0
    rows = s.scalars(select(m.ProblemLabel).where(
        m.ProblemLabel.is_current.is_(True), m.ProblemLabel.taxonomy_version != config.TAXONOMY_VERSION)).all()
    done = 0
    for lab in rows:
        if done >= limit:
            break
        if not ({lab.primary_skill, *(lab.secondary_skills or [])} & config.RELABEL_SKILLS):
            lab.taxonomy_version = config.TAXONOMY_VERSION   # 这道题的标注在新表里意思没变
            continue
        key = (lab.server_id, lab.problem_id)
        if time.monotonic() - _relabel_tried.get(key, -1e9) < 3600:
            continue
        _relabel_tried[key] = time.monotonic()
        annotate_problem(s, lab.server_id, lab.problem_id, force=True)
        done += 1
    return done
