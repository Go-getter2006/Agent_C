"""本机编译运行 C 程序并比对测试用例（仅限 Linux）。

注意：这里只做了超时和资源限制，不是安全沙箱。只在你自己的电脑上测试用；
正式给学生用时，变式题应接入学校的在线评测系统（如 XDOJ），或放进无网络的容器里运行。
"""
import os
import shutil
import subprocess
import sys
import tempfile

COMPILE_TIMEOUT = 15
RUN_TIMEOUT = 2


def gcc_available() -> bool:
    return shutil.which("gcc") is not None and sys.platform.startswith("linux")


def _limits():  # pragma: no cover - 只在子进程里执行
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def normalize_output(text: str) -> list:
    """比较时忽略多余的空格和换行：按空白切成词再比较。"""
    return text.split()


def compile_and_test(source: str, tests: list) -> dict:
    """返回 {"ok": bool, "stage": "compile"|"run", "detail": str, "results": [...]}。"""
    if not gcc_available():
        return {"ok": False, "stage": "unavailable", "detail": "本机没有 gcc 或不是 Linux，无法运行代码。", "results": []}
    workdir = tempfile.mkdtemp(prefix="ctutor_")
    try:
        src = os.path.join(workdir, "main.c")
        exe = os.path.join(workdir, "main")
        with open(src, "w", encoding="utf-8") as f:
            f.write(source)
        try:
            comp = subprocess.run(["gcc", "-std=c11", "-O0", "-w", "-o", exe, src, "-lm"],
                                  capture_output=True, text=True, timeout=COMPILE_TIMEOUT, cwd=workdir)
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "compile", "detail": "编译超时。", "results": []}
        if comp.returncode != 0:
            return {"ok": False, "stage": "compile", "detail": comp.stderr[-1500:], "results": []}
        results = []
        for case in tests:
            try:
                run = subprocess.run([exe], input=case.get("in", ""), capture_output=True, text=True,
                                     timeout=RUN_TIMEOUT, cwd=workdir, preexec_fn=_limits)
                actual = run.stdout
                passed = run.returncode == 0 and normalize_output(actual) == normalize_output(case.get("out", ""))
                results.append({"in": case.get("in", ""), "expected": case.get("out", ""), "actual": actual[-500:],
                                "returncode": run.returncode, "passed": passed})
            except subprocess.TimeoutExpired:
                results.append({"in": case.get("in", ""), "expected": case.get("out", ""), "actual": "",
                                "returncode": None, "passed": False, "timeout": True})
        ok = all(r["passed"] for r in results)
        failed = next((r for r in results if not r["passed"]), None)
        detail = "全部测试用例通过。" if ok else (
            "运行超时（可能是死循环）。" if failed and failed.get("timeout") else
            f"输入 {failed['in']!r} 时，期望输出 {failed['expected']!r}，实际输出 {failed['actual']!r}。")
        return {"ok": ok, "stage": "run", "detail": detail, "results": results}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
