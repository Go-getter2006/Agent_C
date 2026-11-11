"""检查题库：字段齐全、行号有效；有 gcc 时再编译运行参考程序和无错题。

用法：python scripts/validate_item_bank.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.knowledge import KP_BY_ID  # noqa: E402
from app import runner  # noqa: E402

REQUIRED_BUGGY = ["id", "kp", "title", "task", "code", "bug_lines", "cause", "trigger", "fix", "fix_rationale", "irt"]
REQUIRED_CLEAN = ["id", "kp", "title", "task", "code", "explanation", "irt"]


def main() -> int:
    bank = json.loads((ROOT / "app" / "item_bank.json").read_text(encoding="utf-8"))
    errors, checked = [], 0
    ids = set()
    for it in bank["items"]:
        if it["id"] in ids:
            errors.append(f"{it['id']}: 题号重复")
        ids.add(it["id"])
        if it["kp"] not in KP_BY_ID:
            errors.append(f"{it['id']}: 未知知识点 {it['kp']}")
        req = REQUIRED_BUGGY if it["has_bug"] else REQUIRED_CLEAN
        for key in req:
            if key not in it:
                errors.append(f"{it['id']}: 缺少字段 {key}")
        n_lines = len(it["code"].split("\n"))
        for ln in it.get("bug_lines", []):
            if not 1 <= ln <= n_lines:
                errors.append(f"{it['id']}: 错误行号 {ln} 超出代码范围 1..{n_lines}")
        if runner.gcc_available():
            if it["has_bug"] and it.get("variant", {}).get("reference"):
                res = runner.compile_and_test(it["variant"]["reference"], it["variant"]["tests"])
                checked += 1
                if not res["ok"]:
                    errors.append(f"{it['id']}: 变式题参考程序未通过测试：{res['detail']}")
            if not it["has_bug"] and it.get("expected_io"):
                res = runner.compile_and_test(it["code"], [it["expected_io"]])
                checked += 1
                if not res["ok"]:
                    errors.append(f"{it['id']}: 无错题实际运行结果与预期不符：{res['detail']}")
    print(f"题目 {len(bank['items'])} 道；运行检查 {checked} 次；问题 {len(errors)} 个")
    for e in errors:
        print("  -", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
