"""编译并运行一小段 C 程序：限时、限内存、不能联网、不能写文件。

两种方式（config.SANDBOX_MODE）：
- socket：把请求交给独立的沙箱容器（docker-compose 里的 sandbox 服务：没有网络、根文件系统只读、
  非 root、限制进程数和内存）。正式环境用这个。
- local：在本机直接运行，靠 rlimit 限时限内存限文件，能用 unshare 时再去掉网络。
  读文件隔离不了，所以只供开发和测试；进沙箱的代码都先过 precheck。
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from .. import config

COMPILE_TIMEOUT = 15
WALL_FACTOR = 3  # 墙上时间上限 = CPU 时间上限 × 3


@dataclass
class RunResult:
    status: str  # ok / runtime_error / timeout / output_limit
    stdout: str = ""
    exit_code: int | None = None
    time_ms: int = 0


@dataclass
class ProgramResult:
    status: str  # ok / compile_error / unavailable
    compile_output: str = ""
    runs: list[RunResult] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ProgramResult":
        return cls(
            status=data.get("status", "unavailable"),
            compile_output=data.get("compile_output", ""),
            runs=[RunResult(**r) for r in data.get("runs", [])],
        )


@lru_cache(maxsize=1)
def _gcc() -> str | None:
    return shutil.which("gcc") or shutil.which("cc")


@lru_cache(maxsize=1)
def _netns_prefix() -> tuple[str, ...]:
    """普通用户：用 unshare 开用户命名空间来去掉网络，不行就返回空。root 不走这里，见 _limits。"""
    if os.geteuid() == 0:
        return ()
    unshare = shutil.which("unshare")
    if not unshare:
        return ()
    prefix = (unshare, "-r", "-n", "--")
    try:
        ok = subprocess.run([*prefix, "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return prefix if ok else ()


def sandbox_available() -> bool:
    if config.SANDBOX_MODE == "socket":
        return os.path.exists(config.SANDBOX_SOCKET)
    return _gcc() is not None


def _limits(cpu_seconds: int, memory_mb: int, drop_root: bool):
    def apply():  # 在子进程 exec 之前执行
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        mem = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # 不能写任何文件
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
        os.setsid()
        if drop_root and os.geteuid() == 0:
            # root：先进新的网络命名空间（里面没有任何网卡），再降成 nobody
            if hasattr(os, "unshare"):
                try:
                    os.unshare(os.CLONE_NEWNET)
                except OSError:
                    pass
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # 不能再创建进程
        except (ValueError, OSError):
            pass

    return apply


def _compile(source: str, workdir: Path) -> tuple[bool, str]:
    gcc = _gcc()
    assert gcc
    src = workdir / "main.c"
    src.write_text(source, encoding="utf-8")
    try:
        proc = subprocess.run(
            [gcc, "-x", "c", "-std=gnu11", "-O1", "-fno-diagnostics-color", "-o", "prog", "main.c", "-lm"],
            cwd=workdir, capture_output=True, timeout=COMPILE_TIMEOUT,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        return False, "编译超时"
    diag = proc.stderr.decode("utf-8", "replace")
    return proc.returncode == 0, diag


def _run_one(workdir: Path, stdin: str, cpu_seconds: int, memory_mb: int, output_limit: int) -> RunResult:
    cmd = [*_netns_prefix(), str(workdir / "prog")]
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=_limits(cpu_seconds, memory_mb, drop_root=True), env={"LANG": "C"},
        )
    except OSError:
        return RunResult(status="runtime_error")
    try:
        out, _ = proc.communicate(stdin.encode("utf-8"), timeout=cpu_seconds * WALL_FACTOR)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()
        proc.communicate()
        return RunResult(status="timeout", time_ms=int((time.monotonic() - started) * 1000))
    elapsed = int((time.monotonic() - started) * 1000)
    text = out[:output_limit].decode("utf-8", "replace")
    if len(out) > output_limit:
        return RunResult(status="output_limit", stdout=text, exit_code=proc.returncode, time_ms=elapsed)
    if proc.returncode in (-24, 128 + 24) or proc.returncode == -9:  # SIGXCPU / 被杀
        return RunResult(status="timeout", stdout=text, exit_code=proc.returncode, time_ms=elapsed)
    if proc.returncode != 0:
        return RunResult(status="runtime_error", stdout=text, exit_code=proc.returncode, time_ms=elapsed)
    return RunResult(status="ok", stdout=text, exit_code=0, time_ms=elapsed)


def run_local(source: str, inputs: list[str], cpu_seconds: int, memory_mb: int, output_limit: int) -> ProgramResult:
    if _gcc() is None:
        return ProgramResult(status="unavailable", compile_output="这台机器上没有 gcc")
    with tempfile.TemporaryDirectory(prefix="sbx-") as tmp:
        workdir = Path(tmp)
        os.chmod(workdir, 0o755)
        ok, diag = _compile(source, workdir)
        if not ok:
            return ProgramResult(status="compile_error", compile_output=diag)
        os.chmod(workdir / "prog", 0o755)
        (workdir / "main.c").unlink(missing_ok=True)
        runs = [_run_one(workdir, text, cpu_seconds, memory_mb, output_limit) for text in inputs]
        return ProgramResult(status="ok", compile_output=diag, runs=runs)


def run_via_socket(payload: dict, timeout: float = 60.0) -> ProgramResult:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(config.SANDBOX_SOCKET)
            s.sendall(json.dumps(payload).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return ProgramResult.from_json(json.loads(b"".join(chunks).decode("utf-8")))
    except (OSError, ValueError) as exc:
        return ProgramResult(status="unavailable", compile_output=f"沙箱连不上：{type(exc).__name__}")


def run_program(
    source: str,
    inputs: list[str],
    cpu_seconds: int | None = None,
    memory_mb: int | None = None,
    output_limit: int | None = None,
) -> ProgramResult:
    """编译一次，对每组输入各运行一次。"""
    cpu = cpu_seconds or config.SANDBOX_CPU_SECONDS
    mem = memory_mb or config.SANDBOX_MEMORY_MB
    cap = output_limit or config.SANDBOX_OUTPUT_LIMIT
    if config.SANDBOX_MODE == "socket":
        return run_via_socket(
            {"source": source, "inputs": inputs, "cpu_seconds": cpu, "memory_mb": mem, "output_limit": cap}
        )
    return run_local(source, inputs, cpu, mem, cap)
