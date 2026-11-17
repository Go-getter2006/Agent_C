"""沙箱服务：在独立容器里运行，通过共享卷上的 Unix 套接字收请求。

容器本身没有网络（network_mode: none），所以用套接字文件通信。
运行：python -m app.sandbox.server
"""
from __future__ import annotations

import json
import os
import socket
import sys

from .. import config
from .runner import run_local

MAX_REQUEST = 256 * 1024
MAX_INPUTS = 12


def handle(raw: bytes) -> dict:
    try:
        req = json.loads(raw.decode("utf-8"))
        source = str(req["source"])
        inputs = [str(x) for x in req.get("inputs", [])][:MAX_INPUTS]
        cpu = min(int(req.get("cpu_seconds", 1)), 5)
        mem = min(int(req.get("memory_mb", 64)), 256)
        cap = min(int(req.get("output_limit", 65536)), 1024 * 1024)
    except (KeyError, ValueError, TypeError):
        return {"status": "unavailable", "compile_output": "请求格式不对", "runs": []}
    return run_local(source, inputs, cpu, mem, cap).to_json()


def serve(path: str | None = None) -> None:
    path = path or config.SANDBOX_SOCKET
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
        srv.bind(path)
        os.chmod(path, 0o666)
        srv.listen(8)
        print(f"沙箱服务已启动：{path}", file=sys.stderr, flush=True)
        while True:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(10)
                chunks, size = [], 0
                try:
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_REQUEST:
                            break
                        chunks.append(chunk)
                    result = handle(b"".join(chunks)) if size <= MAX_REQUEST else {
                        "status": "unavailable", "compile_output": "请求太大", "runs": []}
                    conn.sendall(json.dumps(result).encode("utf-8"))
                except OSError:
                    continue


if __name__ == "__main__":
    serve()
