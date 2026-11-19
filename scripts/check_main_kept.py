#!/usr/bin/env python3
"""合并前检查：维护者直接在 main 上提交的改动，这个分支有没有删掉或改掉。

用法（在仓库根目录，先 `git fetch origin main` 并把 main 合进当前分支）：
    python3 scripts/check_main_kept.py            # 对比 origin/main 和 HEAD
    python3 scripts/check_main_kept.py --base main --head some-branch

做法：找出 main 上不是智能体会话提交的普通提交（提交说明里没有 `Claude-Session:`），取它们新增的每一行；
这一行现在 main 里还有、当前分支里却没有了，就列出来（是这个分支要删掉或改掉的）。
有输出就逐条确认：维护者要求改的才可以删，并在 PR 里写明；否则把那些行恢复。没有输出、退出码 0 才能合并。
只读 git，不改任何东西。frontend/dist 是构建产物，不检查（冲突时在合并后的源码上重新构建）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

MIN_LEN = 8   # 太短的行（括号、空行、return;）到处都有，不拿来比


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    if r.returncode:
        raise SystemExit(f"git {' '.join(args)} 失败：{r.stderr.strip()}")
    return r.stdout


_files: dict[tuple[str, str], set[str] | None] = {}


def lines_at(rev: str, path: str) -> set[str] | None:
    key = (rev, path)
    if key not in _files:
        r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, errors="replace")
        _files[key] = None if r.returncode else {ln.strip() for ln in r.stdout.splitlines()}
    return _files[key]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main", help="维护者的主分支（默认 origin/main）")
    ap.add_argument("--head", default="HEAD", help="要合进去的分支（默认 HEAD）")
    args = ap.parse_args()

    log = git("log", args.base, "--no-merges", "--format=%H%x1f%an%x1f%s%x1f%(trailers:key=Claude-Session,valueonly,separator=)%x1e")
    user = []
    for rec in log.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 4 and not parts[3].strip():
            user.append(parts[:3])

    lost: dict[tuple[str, str, str], list[str]] = {}
    for sha, author, subject in user:
        parent = git("rev-list", "--parents", "-n", "1", sha).split()[1:]
        if not parent:
            continue   # 第一个提交：整个项目的起点，不逐行比
        diff = git("diff", "--no-color", "-U0", parent[0], sha, "--", ".", ":(exclude)frontend/dist")
        path = None
        for ln in diff.splitlines():
            if ln.startswith("+++ "):
                path = ln[6:] if ln.startswith("+++ b/") else None
            elif ln.startswith("+") and not ln.startswith("+++") and path:
                s = ln[1:].strip()
                if len(s) < MIN_LEN:
                    continue
                on_base, on_head = lines_at(args.base, path), lines_at(args.head, path)
                if on_base is not None and s in on_base and (on_head is None or s not in on_head):
                    lost.setdefault((sha[:7], subject[:60], path), []).append(s)

    if not lost:
        print(f"维护者在 {args.base} 上的 {len(user)} 个提交，新增的内容 {args.head} 里都在。")
        return 0
    total = sum(len(v) for v in lost.values())
    print(f"{args.head} 会删掉或改掉维护者在 {args.base} 上写的 {total} 行：")
    for (sha, subject, path), ls in lost.items():
        print(f"\n  {sha} {subject}\n    {path}：{len(ls)} 行")
        for s in ls[:6]:
            print(f"      - {s[:120]}")
        if len(ls) > 6:
            print(f"      …还有 {len(ls) - 6} 行")
    print("\n逐条确认：维护者要求改的才可以删（在 PR 里写明）；否则把这些行恢复。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
