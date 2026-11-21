"""测试公用：数据库、假时钟、一台小的假 XDOJ。

默认用内存 SQLite；设置 TEST_DATABASE_URL 可以换成 PostgreSQL 跑同一套测试。
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("DEV_LOGIN_PASSWORD", "test-pass")
os.environ["SYNC_IN_WEB"] = "0"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["SANDBOX_MODE"] = "local"

from app import config, db  # noqa: E402
from app import models as m  # noqa: E402
from app.xdoj.client import RateLimiter, XdojClient  # noqa: E402
from app.xdoj.fake import FakeJudge, FakeRun, FakeSubmission, FakeXdoj  # noqa: E402
from app.xdoj.store import SyncContext, request_logger  # noqa: E402

config.DEEPSEEK_API_KEY = ""
config.DEV_LOGIN_PASSWORD = "test-pass"
config.SYNC_IN_WEB = False


@pytest.fixture(autouse=True)
def _fresh_page_cache():
    """教师端汇总页的结果缓存（app/web/cache.py）每个测试清空，别拿到上一个测试的库算出来的结果。"""
    from app.web import cache

    cache.clear()
    cache.BACKGROUND = False   # 数据变了当场重算；测后台重算的测试自己打开
    yield
    cache.clear()
    cache.BACKGROUND = False


@pytest.fixture(autouse=True)
def _fresh_mastery_params():
    """每周学到的掌握程度参数在进程里缓存（mastery.active_params）：换了库的测试不能拿到上一个库学的那一版。"""
    from app import mastery

    mastery.forget_active_params()
    yield
    mastery.forget_active_params()


@pytest.fixture(autouse=True)
def _always_challenge(monkeypatch):
    """小题判断“满足要求”以后追不追问是随机的（一半）；测试里默认一定追问，测随机的测试自己改。"""
    monkeypatch.setitem(config.QUIZ, "challenge_p", 1.0)


@pytest.fixture(autouse=True)
def _no_real_xdoj(monkeypatch):
    """测试不连真实的 XDOJ：登录时没注入假 XDOJ 的，一律当作连不上（代码里写着 C 语言那台的真实地址）。"""
    import httpx

    from app.web import teacher as teacher_api

    def refuse(request):
        raise httpx.ConnectError("tests do not reach the real XDOJ")

    monkeypatch.setattr(teacher_api, "_test_transport", httpx.MockTransport(refuse))


@pytest.fixture()
def database():
    url = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    engine = db.init_engine(url)
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    yield engine
    db.Base.metadata.drop_all(engine)
    engine.dispose()


class FakeClock:
    """可以手动拨的时钟：既当墙上时间，也当限速器的单调时钟。"""

    def __init__(self, start: dt.datetime | None = None):
        self.t = start or dt.datetime(2026, 11, 16, 13, 0, 0, tzinfo=db.UTC)  # 北京时间 21:00
        self.mono = 1000.0
        self.slept: list[float] = []

    def now(self) -> dt.datetime:
        return self.t

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.t += dt.timedelta(seconds=seconds)
        self.mono += seconds


@pytest.fixture()
def clock():
    return FakeClock()


def small_fake() -> FakeXdoj:
    """一个班、三名学生、一个作业、两道题、几条提交。"""
    fx = FakeXdoj()
    fx.classes = [{"id": 31, "classId": 31, "className": "测试一班", "ownerId": fx.teacher_id}]
    fx.add_student("2500000001", "学生甲", 31)
    fx.add_student("2500000002", "学生乙", 31)
    fx.add_student("2500000003", "学生丙", 31)
    fx.contests = [{"id": 501, "title": "上机练习：测试", "startTime": "2026-11-10 08:00:00",
                    "endTime": "2026-11-30 23:59:59", "type": "练习", "classes": [{"id": 31}],
                    "_problem_ids": [406, 407]}]
    fx.problems = [
        {"id": 406, "title": "两数之和", "categoryName": "顺序结构", "tags": [{"name": "输入输出"}],
         "difficulty": "易", "timeLimit": 1000, "memoryLimit": 65536},
        {"id": 407, "title": "求最大值", "categoryName": "一维数组", "tags": [{"name": "一维数组"}],
         "difficulty": "易", "timeLimit": 1000, "memoryLimit": 65536},
    ]
    fx.problem_files[406] = ("输入两个整数，输出它们的和。".encode("utf-8"), "text/plain; charset=utf-8")
    fx.problem_files[407] = ("输入 n 个整数，输出最大值。".encode("utf-8"), "text/plain; charset=utf-8")
    fx.add_testcase(406, 1396, "1 1\n", "2\n")
    fx.add_testcase(406, 1397, "2 3\n", "5\n")
    fx.add_testcase(407, 1401, "3\n1 5 2\n", "5\n")
    fx.add_testcase(407, 1402, "2\n-4 -9\n", "-4\n")

    # 一条答案错误的提交：第二个测试点没过
    fx.submissions[537101] = FakeSubmission(
        id=537101, user_id="2500000001", user_name="学生甲", problem_id=406, contest_id=501, class_id=31,
        submit_time="2026-11-16 20:58:00",
        source="#include <stdio.h>\r\nint main(){\tint a,b;  scanf(\"%d %d\",&a,&b);\r\n printf(\"%d\\n\",a*b+1); }\r\n".encode("utf-8"),
        judges=[FakeJudge(id=537601, result="答案错误", points=50, start="2026-11-16 20:58:01",
                          end="2026-11-16 20:58:02",
                          runs=[FakeRun(2153401, 1396, "正确"), FakeRun(2153402, 1397, "错误", program_output="7\n")])])
    # 一条编译错误的提交：源码是 GBK 编码
    fx.submissions[537102] = FakeSubmission(
        id=537102, user_id="2500000002", user_name="学生乙", problem_id=406, contest_id=501, class_id=31,
        submit_time="2026-11-16 20:59:00", source="// 两数之和\nint main(){ return 0 }\n".encode("gbk"),
        judges=[FakeJudge(id=537602, result="编译错误", points=0, compile_output="main.c:2:23: error: expected ';' before '}' token")])
    # 一条通过的提交
    fx.submissions[537103] = FakeSubmission(
        id=537103, user_id="2500000003", user_name="学生丙", problem_id=406, contest_id=501, class_id=31,
        submit_time="2026-11-16 21:00:00", source=b"int main(){}\n",
        judges=[FakeJudge(id=537603, result="正确", points=100,
                          runs=[FakeRun(2153403, 1396, "正确"), FakeRun(2153404, 1397, "正确")])])
    # 一条程序什么都没输出的提交
    fx.submissions[537104] = FakeSubmission(
        id=537104, user_id="2500000001", user_name="学生甲", problem_id=407, contest_id=501, class_id=31,
        submit_time="2026-11-16 21:01:00", source=b"int main(){return 0;}\n",
        judges=[FakeJudge(id=537604, result="答案错误", points=0,
                          runs=[FakeRun(2153405, 1401, "错误", program_output=""),
                                FakeRun(2153406, 1402, "错误", program_output="")])])
    return fx


def make_context(fx: FakeXdoj, clock: FakeClock, server_id: int = 1, log_requests: bool = True) -> SyncContext:
    with db.session_scope() as s:
        if s.get(m.Server, server_id) is None:
            s.add(m.Server(id=server_id, code=f"s{server_id}", name="测试服务器", course="C",
                           base_url="http://fake.test/xdoj-ssm"))
    fx.clock = clock.monotonic
    limiter = RateLimiter(0.5, clock=clock.monotonic, sleep=clock.sleep)
    client = XdojClient("http://fake.test/xdoj-ssm", fx.teacher_id, fx.password, limiter=limiter,
                        transport=fx.transport(), sleep=clock.sleep,
                        on_request=request_logger(server_id) if log_requests else None)
    return SyncContext(server_id=server_id, client=client, term="2026秋", clock=clock.now)


@pytest.fixture()
def fake():
    return small_fake()


@pytest.fixture()
def ctx(database, fake, clock):
    return make_context(fake, clock)


# ------------------------------------------------------------ 演示数据
_SEEDED: dict = {}


@pytest.fixture()
def demo(tmp_path_factory, tmp_path):
    """入好演示数据的库。SQLite 下只入一次，之后每个测试拿一份拷贝；PostgreSQL 下每次重新入库。"""
    import shutil

    from app.demo.seed import seed

    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = db.init_engine(url)
        db.Base.metadata.drop_all(engine)
        info = seed(verify_quiz=False)
    else:
        if "path" not in _SEEDED:
            template = tmp_path_factory.mktemp("seed") / "demo.sqlite3"
            db.init_engine(f"sqlite:///{template}")
            _SEEDED["info"] = seed(verify_quiz=False)
            db.engine().dispose()
            _SEEDED["path"] = template
        copy = tmp_path / "demo.sqlite3"
        shutil.copy(_SEEDED["path"], copy)
        engine = db.init_engine(f"sqlite:///{copy}")
        info = _SEEDED["info"]
    yield info
    engine.dispose()
