"""假的 XDOJ：按《同步程序开发说明》第 4 节的接口清单，在内存里模拟一台服务器。

用途：1. 测试（不连真实服务器）；2. 演示数据走真实的同步流程入库。
它返回的字段名是按文档写的；真实服务器上没有逐一核对过的字段，见 docs/问题清单.md。
名单接口故意带上密码、令牌、登录 IP，用来验证这些字段不会进库。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx


@dataclass
class FakeRun:
    id: int
    testcase_id: int
    result: str
    run_time: int = 1
    memory: int = 1024
    program_output: str | None = None


@dataclass
class FakeJudge:
    id: int
    result: str
    points: float = 0
    start: str = ""
    end: str = ""
    compile_output: str | None = None
    runs: list[FakeRun] = field(default_factory=list)


@dataclass
class FakeSubmission:
    id: int
    user_id: str
    user_name: str
    problem_id: int
    contest_id: int
    class_id: int
    submit_time: str
    source: bytes
    judges: list[FakeJudge] = field(default_factory=list)
    language: str = "C"

    @property
    def judgement(self) -> str:
        return self.judges[-1].result if self.judges else "等待评测"

    @property
    def points(self) -> float:
        return self.judges[-1].points if self.judges else 0


# 展开的子表：真实网页用 GET 取，参数是 jqGrid 的 _search、nd、page、rows、sidx、sord
GRID_PATHS = frozenset({"/contest/problems.do", "/testcase/list.do", "/submission/listJudge.do"})
# 真实服务器下载文件（源码、测试点）时响应头写的就是这个
DOWNLOAD_TYPE = "multipart/form-data"


class FakeXdoj:
    def __init__(self, teacher_id: str = "teacher01", password: str = "fake-password"):
        self.teacher_id = teacher_id
        self.password = password
        self.classes: list[dict] = []
        self.students: list[dict] = []
        self.contests: list[dict] = []
        self.problems: list[dict] = []
        self.problem_files: dict[int, tuple[bytes, str]] = {}
        self.testcases: dict[int, list[dict]] = {}
        self.submissions: dict[int, FakeSubmission] = {}
        self.requests: list[tuple[str, str]] = []
        self.request_times: list[float] = []
        self.fail_queue: list[int] = []  # 接下来几次请求直接返回这些状态码
        self._sessions: dict[str, bool] = {}
        self._next_session = 1
        self.login_count = 0
        self.clock = None  # 测试可以注入时钟，记录请求时刻
        self.student_passwords: dict[str, str] = {}  # 学生自己在 XDOJ 上的密码（只在假服务器里，用来测学生登录）
        self.teacher_name = "王老师"
        # 和真实接口一样：评测记录、测试点结果只给数字，语言是一个对象（测试用来核对兼容）
        self.numeric_results = False
        self.language_object = False
        # 和真实接口一样严格：展开的子表（GET）要带网页里 jqGrid 发的分页和搜索参数，不带就是 Tomcat 的 400 错误页（HTML）。
        # 以前假服务器什么参数都收，测试全过、真服务器却拒绝——名单那次也是这样漏掉的
        self.strict_grids = True
        self.fail_paths: dict[str, int] = {}          # 某个接口一直返回这个状态码（HTML 错误页）
        self.grid_reject_sidx: dict[str, str] = {}    # 某个子表按这个 sidx 排序时报 500（比如联表后 id 有歧义）
        self.last_args: dict[str, dict[str, str]] = {}  # 每个接口最近一次收到的参数（测试核对请求的写法）

    # ------------------------------------------------------------ 造数据
    def add_student(self, user_id: str, name: str, class_id: int) -> None:
        self.students.append({
            "userId": user_id, "userName": name, "classId": class_id,
            # 下面三个字段真实接口也会返回；同步程序必须在内存里就丢掉
            "password": f"pw-{user_id}-SECRET", "token": f"tok-{user_id}-SECRET", "loginIp": "10.9.8.7",
        })

    def add_testcase(self, problem_id: int, testcase_id: int, data_in: str, data_out: str,
                     points: float = 20, description: str = "") -> None:
        self.testcases.setdefault(problem_id, []).append({
            "id": testcase_id, "problemId": 0, "description": description, "points": points,
            "input": data_in.encode("utf-8"), "output": data_out.encode("utf-8")})

    def expire_sessions(self) -> None:
        for key in self._sessions:
            self._sessions[key] = False

    # ------------------------------------------------------------ HTTP
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _json(self, payload, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=payload)

    @staticmethod
    def _error_page(status: int) -> httpx.Response:
        """Tomcat 的错误页：HTML，不是 JSON。"""
        return httpx.Response(status, text=f"<html><head><title>HTTP Status {status}</title></head><body>"
                                           f"<h1>HTTP Status {status}</h1></body></html>",
                              headers={"content-type": "text/html;charset=utf-8"})

    @staticmethod
    def _page(rows: list[dict], page: int, size: int) -> dict:
        total = len(rows)
        pages = max(1, (total + size - 1) // size)
        start = (page - 1) * size
        return {"totalCount": total, "totalpages": pages, "page": page, "data": rows[start:start + size]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        marker = "/xdoj-ssm"
        if marker in path:
            path = path.split(marker, 1)[1] or "/"
        self.requests.append((request.method, path))
        if self.clock is not None:
            self.request_times.append(self.clock())
        if self.fail_queue:
            return httpx.Response(self.fail_queue.pop(0), text="error")
        if path in self.fail_paths:
            return self._error_page(self.fail_paths[path])

        args: dict[str, str] = {k: v for k, v in request.url.params.items()}
        if request.method == "POST":
            body = request.content.decode("utf-8")
            args.update({k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()})

        if path != "/user/login.do":
            self.last_args[path] = {**args, "__ajax": request.headers.get("x-requested-with", "")}
        cookie = request.headers.get("cookie", "")
        session_id = ""
        for part in cookie.split(";"):
            if part.strip().startswith("JSESSIONID="):
                session_id = part.strip().split("=", 1)[1]

        if path == "/" and request.method == "GET":
            session_id = f"S{self._next_session}"
            self._next_session += 1
            self._sessions[session_id] = False
            return httpx.Response(200, text="<html>login</html>",
                                  headers={"set-cookie": f"JSESSIONID={session_id}; Path=/"})
        if path == "/user/login.do":
            uid, pw = args.get("userId"), args.get("password")
            teacher_ok = uid == self.teacher_id and pw == self.password
            student_ok = uid in self.student_passwords and pw == self.student_passwords[uid]
            if (teacher_ok or student_ok) and session_id in self._sessions:
                self._sessions[session_id] = True
                self.login_count += 1
                return self._json({"error": 0, "userId": uid, "userType": 1 if teacher_ok else 2})
            return self._json({"error": 1, "message": "用户名或密码错误"})
        if not self._sessions.get(session_id):
            return httpx.Response(302, headers={"location": "/xdoj-ssm/login.jsp"})
        if path == "/admin/dashboard.do":
            return httpx.Response(200, text=f'<ul><li class="light-blue"><span class="user-info"><small>{self.teacher_name}</small>'
                                            f'<small>{self.teacher_id}</small></span></li></ul>')

        if request.method == "GET" and path in GRID_PATHS:
            if self.strict_grids and not {"_search", "page", "rows", "sidx", "sord"} <= set(args):
                return self._error_page(400)   # 缺了必填的参数（Spring 的 @RequestParam）
            if self.grid_reject_sidx.get(path) is not None and args.get("sidx") == self.grid_reject_sidx[path]:
                return self._error_page(500)
        page = int(args.get("page", 1) or 1)
        size = int(args.get("rows", 20) or 20)
        filters = json.loads(args["filters"]) if args.get("filters") else {"rules": []}

        if path == "/class/list.do":
            return self._json(self._page(self.classes, page, size))
        if path == "/user/listStudents.do":
            rows = self.students
            for rule in filters.get("rules", []):
                if rule.get("field") == "class_id":
                    rows = [r for r in rows if str(r["classId"]) == str(rule.get("data"))]
            return self._json(self._page(rows, page, size))
        if path == "/contest/list.do":
            return self._json(self._page(self.contests, page, size))
        if path == "/contest/problems.do":
            contest = next((c for c in self.contests if str(c["id"]) == args.get("contestId")), None)
            rows = [{"problemId": pid} for pid in (contest or {}).get("_problem_ids", [])]
            return self._json(self._page(rows, page, size))
        if path == "/problem/list.do":
            return self._json(self._page(self.problems, page, size))
        if path == "/problem/viewTextFile.do":
            data, ctype = self.problem_files.get(int(args.get("problemId", 0)), (b"", "text/plain"))
            return httpx.Response(200, content=data, headers={"content-type": ctype})
        if path == "/testcase/list.do":
            rows = [{k: v for k, v in t.items() if k not in ("input", "output")}
                    for t in self.testcases.get(int(args.get("problemId", 0)), [])]
            return self._json(self._page(rows, page, size))
        if path in ("/testcase/downloadInputFile.do", "/testcase/downloadOutputFile.do"):
            tid = int(args.get("id", 0))
            for rows in self.testcases.values():
                for t in rows:
                    if t["id"] == tid:
                        key = "input" if "Input" in path else "output"
                        return httpx.Response(200, content=t[key], headers={"content-type": DOWNLOAD_TYPE})
            return httpx.Response(404, text="not found")
        if path == "/submission/list.do":
            rows = sorted(self.submissions.values(), key=lambda x: x.id)
            for rule in filters.get("rules", []):
                if rule.get("field") == "s.id" and rule.get("op") == "gt":
                    rows = [r for r in rows if r.id > int(rule.get("data", 0))]
                elif rule.get("field") not in (None, "s.id"):
                    return httpx.Response(500, text="unsupported filter")  # 实测：按时间筛选会报 500
            if args.get("sord") == "desc":
                rows = rows[::-1]
            data = [{"id": r.id, "userId": r.user_id, "userName": r.user_name, "problemId": r.problem_id,
                     "contestId": r.contest_id, "classId": r.class_id, "submitTime": r.submit_time,
                     "language": ({"id": 1, "name": r.language, "compiler": "gcc " * 30} if self.language_object
                                  else r.language),
                     "judgementText": r.judgement, "points": r.points} for r in rows]   # 真实接口：文字结果在 judgementText
            return self._json(self._page(data, page, size))
        if path == "/submission/listJudge.do":
            sub = self.submissions.get(int(args.get("submissionId", 0)))
            rows = [{"id": j.id, "submissionId": sub.id, "points": j.points,
                     "startTime": j.start, "endTime": j.end, "compileOutputStr": j.compile_output,
                     **({"judgement": 9} if self.numeric_results else {"resultStr": j.result})}
                    for j in (sub.judges if sub else [])]
            return self._json(self._page(rows, page, size))
        if path == "/submission/listAllJudgeRun.do":
            jid = int(args.get("judgeId", 0))
            for sub in self.submissions.values():
                for j in sub.judges:
                    if j.id == jid:
                        return self._json([{"id": r.id, "judgeId": jid, "testcaseId": r.testcase_id,
                                            "result": (1 if r.result == "正确" else 2) if self.numeric_results else r.result,
                                            "runTime": r.run_time, "memory": r.memory}
                                           for r in j.runs])
            return self._json([])
        if path == "/submission/getRunOutput.do":
            rid = int(args.get("id", 0))
            for sub in self.submissions.values():
                for j in sub.judges:
                    for r in j.runs:
                        if r.id == rid:
                            tc = next((t for t in self.testcases.get(sub.problem_id, [])
                                       if t["id"] == r.testcase_id), None)
                            return self._json({
                                "standardInput": tc["input"].decode("utf-8") if tc else None,
                                "standardOutput": tc["output"].decode("utf-8") if tc else None,
                                "programOutput": r.program_output})
            return httpx.Response(404, text="not found")
        if path == "/submission/downloadSource.do":
            sub = self.submissions.get(int(args.get("id", 0)))
            if sub is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, content=sub.source, headers={"content-type": DOWNLOAD_TYPE})
        return httpx.Response(404, text="not found")
