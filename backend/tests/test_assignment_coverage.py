"""相同质量下比较练习广度；多次提交同一题不改变覆盖率。"""
from types import SimpleNamespace

from app.mastery import KpState
from app.web.teacher import assignment_mastery


def test_quality_and_breadth_are_reported_separately():
    topics = {(1, n): {f"C{n % 5}"} for n in range(20)}
    states = {f"C{n}": KpState(f"C{n}", score=.8, level="基本掌握") for n in range(5)}

    def submitted(problem, judgement="AC", server=1):
        return SimpleNamespace(server_id=server, problem_id=problem, judgement=judgement)

    narrow = assignment_mastery(topics, [submitted(0)] * 105 + [submitted(1, "PENDING"), submitted(2, server=2)], states)
    broad = assignment_mastery(topics, [submitted(n) for n in range(20)], states)
    assert narrow == {"quality": 80, "score": 10, "coveredProblems": 1, "totalProblems": 20,
                      "coveredTopics": 1, "totalTopics": 5, "scoredTopics": 1}
    assert broad["score"] == broad["quality"] == 80
    assert assignment_mastery(topics, [submitted(0, "WA")], states) == narrow
    assert assignment_mastery(topics, [], states)["score"] is None
    assert assignment_mastery({(1, 0): set()}, [submitted(0)], states)["score"] is None
    assert assignment_mastery(topics, [submitted(0)], {})["score"] is None
