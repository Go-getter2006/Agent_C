"""小题题库。每道题：显示给学生的代码（不超过 10 行）+ 隐藏的模板（头文件、读入、输出）+ 检查数据。

入库前要先验证（quiz.verify_item）：有错的代码真的出错，没错的代码和标准改法真的通过。
检查数据是小题自带的，不是 XDOJ 的测试点，运行没通过时可以告诉学生其中一组。
"""
from __future__ import annotations

PLACEHOLDER = "/*<<CODE>>*/"

_ARRAY_TEMPLATE = """#include <stdio.h>
int main(void) {
    int n, i, a[100];
    scanf("%d", &n);
    for (i = 0; i < n; i++) scanf("%d", &a[i]);
/*<<CODE>>*/
    printf("%d\\n", RESULT);
    return 0;
}
"""

ITEMS: list[dict] = [
    # ---------------------------------------------------------------- 最值的初值设定
    {
        "code": "max-init-fix", "quiz_type": "FIX", "skill_code": "C06.03.02", "error_code": "TY09", "scene": "求最大值",
        "stem": "这段想求数组里的最大值，对吗？", "caption": "这段代码想求最大值",
        "shown_code": "int max = 0;\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];",
        "template": _ARRAY_TEMPLATE.replace("RESULT", "max"),
        "has_bug": True, "bug_line": 1, "bug_lines": [1],
        "standard_fix": "int max = a[0];\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];",
        "checks": [{"input": "3\n-5 -2 -8\n", "expected": "-2"}, {"input": "4\n3 9 2 7\n", "expected": "9"},
                   {"input": "1\n-1\n", "expected": "-1"}],
        "choices": [{"label": "int max = a[0];", "edits": {"1": "int max = a[0];"}},
                    {"label": "int max = -1;", "edits": {"1": "int max = -1;"}},
                    {"label": "for (i = 1; i <= n; i++)", "edits": {"2": "for (i = 1; i <= n; i++)"}},
                    {"label": "if (a[i] >= max) max = a[i];", "edits": {"3": "    if (a[i] >= max) max = a[i];"}}],
        "explanation": "全是负数时，0 比每个数都大，max 不会被替换。初值要取数组里真实存在的数。",
    },
    {
        "code": "min-init-fix", "quiz_type": "FIX", "skill_code": "C06.03.02", "error_code": "TY09", "scene": "求最小值",
        "stem": "这段想求最小值，对吗？", "caption": "这段代码想求最小值",
        "shown_code": "int min = 0;\nfor (i = 0; i < n; i++)\n    if (a[i] < min) min = a[i];",
        "template": _ARRAY_TEMPLATE.replace("RESULT", "min"),
        "has_bug": True, "bug_line": 1, "bug_lines": [1],
        "standard_fix": "int min = a[0];\nfor (i = 0; i < n; i++)\n    if (a[i] < min) min = a[i];",
        "checks": [{"input": "3\n5 2 8\n", "expected": "2"}, {"input": "4\n-3 -9 -2 -7\n", "expected": "-9"},
                   {"input": "2\n150 200\n", "expected": "150"}, {"input": "1\n6\n", "expected": "6"}],
        "choices": [{"label": "int min = a[0];", "edits": {"1": "int min = a[0];"}},
                    {"label": "int min = 100;", "edits": {"1": "int min = 100;"}},
                    {"label": "if (a[i] > min) min = a[i];", "edits": {"3": "    if (a[i] > min) min = a[i];"}},
                    {"label": "for (i = 1; i < n; i++)", "edits": {"2": "for (i = 1; i < n; i++)"}}],
        "explanation": "数据都是正数时，没有哪个数比 0 小，min 一直是 0。初值要取数组里真实存在的数。",
    },
    {
        "code": "max-from-zero-judge", "quiz_type": "JUDGE", "skill_code": "C06.03.02", "error_code": "",
        "scene": "循环起点",
        "stem": "第 2 行改成从 i = 0 开始，结果还对吗？", "caption": "这段代码想求最大值",
        "shown_code": "int max = a[0];\nfor (i = 1; i < n; i++)\n    if (a[i] > max) max = a[i];",
        "judged_code": "int max = a[0];\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];",
        "template": _ARRAY_TEMPLATE.replace("RESULT", "max"),
        "has_bug": False, "bug_line": None, "bug_lines": [],
        "checks": [{"input": "3\n-5 -2 -8\n", "expected": "-2"}, {"input": "4\n3 9 2 7\n", "expected": "9"},
                   {"input": "1\n4\n", "expected": "4"}],
        "explanation": "还对。从 0 开始只是多拿 a[0] 和自己比一次，不影响结果。",
    },
    {
        "code": "max-plain-judge", "quiz_type": "JUDGE", "skill_code": "C06.03.02", "error_code": "", "scene": "从第二个数开始",
        "stem": "这段想求最大值，循环从 1 开始。对吗？", "caption": "这段代码想求最大值",
        "shown_code": "int max = a[0];\nfor (i = 1; i < n; i++)\n    if (a[i] > max) max = a[i];",
        "template": _ARRAY_TEMPLATE.replace("RESULT", "max"),
        "has_bug": False, "bug_line": None, "bug_lines": [],
        "checks": [{"input": "3\n-5 -2 -8\n", "expected": "-2"}, {"input": "1\n4\n", "expected": "4"},
                   {"input": "4\n9 3 2 7\n", "expected": "9"}],
        "explanation": "是对的。a[0] 已经当了初值，从第二个数开始比就够了。",
    },
    # ---------------------------------------------------------------- 估计范围，防止溢出
    {
        "code": "factorial-overflow-fix", "quiz_type": "FIX", "skill_code": "C02.01.02", "error_code": "TY02",
        "scene": "阶乘",
        "stem": "输入 n（不超过 20），输出 n 的阶乘。这段对吗？", "caption": "这段代码想求 n 的阶乘",
        "shown_code": "int f = 1;\nfor (i = 1; i <= n; i++)\n    f = f * i;\nprintf(\"%d\\n\", f);",
        "template": "#include <stdio.h>\nint main(void) {\n    int n, i;\n    scanf(\"%d\", &n);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 1, "bug_lines": [1, 4],
        "standard_fix": "long long f = 1;\nfor (i = 1; i <= n; i++)\n    f = f * i;\nprintf(\"%lld\\n\", f);",
        "checks": [{"input": "13\n", "expected": "6227020800"}, {"input": "20\n", "expected": "2432902008176640000"},
                   {"input": "5\n", "expected": "120"}],
        "choices": [{"label": "f 改成 long long，输出改成 %lld",
                     "edits": {"1": "long long f = 1;", "4": "printf(\"%lld\\n\", f);"}},
                    {"label": "只把输出改成 %lld", "edits": {"4": "printf(\"%lld\\n\", f);"}},
                    {"label": "f 的初值改成 0", "edits": {"1": "int f = 0;"}},
                    {"label": "循环条件改成 i < n", "edits": {"2": "for (i = 1; i < n; i++)"}}],
        "explanation": "13 的阶乘已经超过 int 能表示的范围。f 要用 long long，输出用 %lld。",
    },
    {
        "code": "factorial-small-judge", "quiz_type": "JUDGE", "skill_code": "C02.01.02", "error_code": "",
        "scene": "小范围阶乘",
        "stem": "输入 n（不超过 12），输出 n 的阶乘。这段对吗？", "caption": "这段代码想求 n 的阶乘",
        "shown_code": "int f = 1;\nfor (i = 1; i <= n; i++)\n    f = f * i;\nprintf(\"%d\\n\", f);",
        "template": "#include <stdio.h>\nint main(void) {\n    int n, i;\n    scanf(\"%d\", &n);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": False, "bug_line": None, "bug_lines": [],
        "checks": [{"input": "12\n", "expected": "479001600"}, {"input": "1\n", "expected": "1"},
                   {"input": "10\n", "expected": "3628800"}],
        "explanation": "是对的。12 的阶乘是 479001600，没有超过 int 的范围。",
    },
    # ---------------------------------------------------------------- 求平均值时避免整数除法
    {
        "code": "average-intdiv-fix", "quiz_type": "FIX", "skill_code": "C06.03.05", "error_code": "TY01",
        "scene": "平均分",
        "stem": "这段想输出平均分（保留两位小数），对吗？", "caption": "这段代码想求平均分",
        "shown_code": "int sum = 0;\nfor (i = 0; i < n; i++)\n    sum += a[i];\ndouble avg = sum / n;\nprintf(\"%.2f\\n\", avg);",
        "template": "#include <stdio.h>\nint main(void) {\n    int n, i, a[100];\n    scanf(\"%d\", &n);\n    for (i = 0; i < n; i++) scanf(\"%d\", &a[i]);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 4, "bug_lines": [1, 4],
        "standard_fix": "int sum = 0;\nfor (i = 0; i < n; i++)\n    sum += a[i];\ndouble avg = (double)sum / n;\nprintf(\"%.2f\\n\", avg);",
        "checks": [{"input": "2\n1 2\n", "expected": "1.50"}, {"input": "3\n80 90 100\n", "expected": "90.00"},
                   {"input": "4\n1 1 1 2\n", "expected": "1.25"}],
        "choices": [{"label": "double avg = (double)sum / n;", "edits": {"4": "double avg = (double)sum / n;"}},
                    {"label": "double avg = (double)(sum / n);", "edits": {"4": "double avg = (double)(sum / n);"}},
                    {"label": "printf(\"%.2lf\\n\", avg);", "edits": {"5": "printf(\"%.2lf\\n\", avg);"}},
                    {"label": "for (i = 1; i <= n; i++)", "edits": {"2": "for (i = 1; i <= n; i++)"}}],
        "explanation": "sum 和 n 都是整数，相除会先丢掉小数。要先把其中一个转成 double 再除。",
    },
    {
        "code": "average-double-judge", "quiz_type": "JUDGE", "skill_code": "C06.03.05", "error_code": "",
        "scene": "double 累加",
        "stem": "这段想输出平均分（保留两位小数），对吗？", "caption": "这段代码想求平均分",
        "shown_code": "double sum = 0;\nfor (i = 0; i < n; i++)\n    sum += a[i];\nprintf(\"%.2f\\n\", sum / n);",
        "template": "#include <stdio.h>\nint main(void) {\n    int n, i, a[100];\n    scanf(\"%d\", &n);\n    for (i = 0; i < n; i++) scanf(\"%d\", &a[i]);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": False, "bug_line": None, "bug_lines": [],
        "checks": [{"input": "2\n1 2\n", "expected": "1.50"}, {"input": "4\n1 1 1 2\n", "expected": "1.25"}],
        "explanation": "是对的。sum 是 double，sum / n 按小数除法算。",
    },
    # ---------------------------------------------------------------- 累加变量的初值
    {
        "code": "sum-reset-fix", "quiz_type": "FIX", "skill_code": "C05.05.01", "error_code": "LP02", "scene": "多组求和",
        "stem": "有 t 组数据，每组输出 n 个数的和。这段对吗？", "caption": "这段代码想分组求和",
        "shown_code": "int sum = 0;\nwhile (t--) {\n    scanf(\"%d\", &n);\n    for (i = 0; i < n; i++) { scanf(\"%d\", &x); sum += x; }\n    printf(\"%d\\n\", sum);\n}",
        "template": "#include <stdio.h>\nint main(void) {\n    int t, n, i, x;\n    scanf(\"%d\", &t);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 1, "bug_lines": [1, 2, 3, 4, 5],
        "standard_fix": "int sum;\nwhile (t--) {\n    scanf(\"%d\", &n); sum = 0;\n    for (i = 0; i < n; i++) { scanf(\"%d\", &x); sum += x; }\n    printf(\"%d\\n\", sum);\n}",
        "checks": [{"input": "2\n2\n1 2\n3\n1 1 1\n", "expected": "3\n3"}, {"input": "1\n3\n4 5 6\n", "expected": "15"},
                   {"input": "3\n1\n7\n1\n8\n1\n9\n", "expected": "7\n8\n9"}],
        "choices": [{"label": "第 3 行末尾加上 sum = 0;", "edits": {"3": "    scanf(\"%d\", &n); sum = 0;"}},
                    {"label": "第 1 行改成 int sum = 1;", "edits": {"1": "int sum = 1;"}},
                    {"label": "第 2 行改成 while (t-- > 1) {", "edits": {"2": "while (t-- > 1) {"}},
                    {"label": "第 4 行的 i = 0 改成 i = 1", "edits": {"4": "    for (i = 1; i < n; i++) { scanf(\"%d\", &x); sum += x; }"}}],
        "explanation": "sum 只在最开始清零一次，第二组会接着上一组往上加。每组开始时都要清零。",
    },
    # ---------------------------------------------------------------- 边界输入
    {
        "code": "prime-one-fix", "quiz_type": "FIX", "skill_code": "C05.04.03", "error_code": "AL02", "scene": "素数",
        "stem": "输入 n（n ≥ 1），是素数输出 Yes，否则输出 No。对吗？", "caption": "这段代码想判断素数",
        "shown_code": "int ok = 1;\nfor (i = 2; i * i <= n; i++)\n    if (n % i == 0) ok = 0;\nprintf(ok ? \"Yes\\n\" : \"No\\n\");",
        "template": "#include <stdio.h>\nint main(void) {\n    int n, i;\n    scanf(\"%d\", &n);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 1, "bug_lines": [1, 4],
        "standard_fix": "int ok = n > 1;\nfor (i = 2; i * i <= n; i++)\n    if (n % i == 0) ok = 0;\nprintf(ok ? \"Yes\\n\" : \"No\\n\");",
        "checks": [{"input": "1\n", "expected": "No"}, {"input": "7\n", "expected": "Yes"},
                   {"input": "9\n", "expected": "No"}, {"input": "2\n", "expected": "Yes"}],
        "choices": [{"label": "int ok = n > 1;", "edits": {"1": "int ok = n > 1;"}},
                    {"label": "for (i = 2; i <= n; i++)", "edits": {"2": "for (i = 2; i <= n; i++)"}},
                    {"label": "for (i = 1; i * i <= n; i++)", "edits": {"2": "for (i = 1; i * i <= n; i++)"}},
                    {"label": "if (n % i != 0) ok = 0;", "edits": {"3": "    if (n % i != 0) ok = 0;"}}],
        "explanation": "n 等于 1 时循环一次都不执行，ok 还是 1，但 1 不是素数。要单独处理 1。",
    },
    # ---------------------------------------------------------------- 逻辑运算、区间条件
    {
        "code": "leap-logic-fix", "quiz_type": "FIX", "skill_code": "C03.03.01", "error_code": "BR03", "scene": "闰年",
        "stem": "闰年：能被 4 整除但不能被 100 整除，或能被 400 整除。对吗？", "caption": "这段代码想判断闰年",
        "shown_code": "if (y % 4 == 0 || y % 100 != 0 || y % 400 == 0)\n    printf(\"Yes\\n\");\nelse\n    printf(\"No\\n\");",
        "template": "#include <stdio.h>\nint main(void) {\n    int y;\n    scanf(\"%d\", &y);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 1, "bug_lines": [1],
        "standard_fix": "if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)\n    printf(\"Yes\\n\");\nelse\n    printf(\"No\\n\");",
        "checks": [{"input": "1900\n", "expected": "No"}, {"input": "2024\n", "expected": "Yes"},
                   {"input": "2000\n", "expected": "Yes"}, {"input": "2023\n", "expected": "No"}],
        "choices": [{"label": "if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)",
                     "edits": {"1": "if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)"}},
                    {"label": "if (y % 4 == 0 && y % 100 != 0 && y % 400 == 0)",
                     "edits": {"1": "if (y % 4 == 0 && y % 100 != 0 && y % 400 == 0)"}},
                    {"label": "if (y % 4 == 0 || y % 400 == 0)", "edits": {"1": "if (y % 4 == 0 || y % 400 == 0)"}},
                    {"label": "把 Yes 和 No 对调", "edits": {"2": "    printf(\"No\\n\");", "4": "    printf(\"Yes\\n\");"}}],
        "explanation": "“能被 4 整除”和“不能被 100 整除”要同时成立，中间用 &&，不是 ||。",
    },
    {
        "code": "range-chain-judge", "quiz_type": "JUDGE", "skill_code": "C03.02.02", "error_code": "BR02",
        "scene": "区间判断",
        "stem": "想判断 x 在 1 到 100 之间（含两端），这段对吗？", "caption": "这段代码想判断范围",
        "shown_code": "if (1 <= x <= 100)\n    printf(\"in\\n\");\nelse\n    printf(\"out\\n\");",
        "template": "#include <stdio.h>\nint main(void) {\n    int x;\n    scanf(\"%d\", &x);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 1, "bug_lines": [1],
        "standard_fix": "if (x >= 1 && x <= 100)\n    printf(\"in\\n\");\nelse\n    printf(\"out\\n\");",
        "checks": [{"input": "200\n", "expected": "out"}, {"input": "50\n", "expected": "in"},
                   {"input": "0\n", "expected": "out"}],
        "explanation": "1 <= x <= 100 会先算 1 <= x，得到 0 或 1，再和 100 比，永远成立。要写成两个条件用 && 连起来。",
    },
    {
        "code": "range-and-judge", "quiz_type": "JUDGE", "skill_code": "C03.03.01", "error_code": "", "scene": "区间判断",
        "stem": "想判断 x 在 1 到 100 之间（含两端），这段对吗？", "caption": "这段代码想判断范围",
        "shown_code": "if (x >= 1 && x <= 100)\n    printf(\"in\\n\");\nelse\n    printf(\"out\\n\");",
        "template": "#include <stdio.h>\nint main(void) {\n    int x;\n    scanf(\"%d\", &x);\n/*<<CODE>>*/\n    return 0;\n}\n",
        "has_bug": False, "bug_line": None, "bug_lines": [],
        "checks": [{"input": "200\n", "expected": "out"}, {"input": "100\n", "expected": "in"},
                   {"input": "1\n", "expected": "in"}, {"input": "0\n", "expected": "out"}],
        "explanation": "是对的。两个条件都要成立，用 && 连接。",
    },
    # ---------------------------------------------------------------- 下标从 0 开始
    {
        "code": "index-start-judge", "quiz_type": "JUDGE", "skill_code": "C06.02.02", "error_code": "AR04",
        "scene": "输出数组",
        "stem": "n 个数已经读进 a[0] 到 a[n-1]，这段想把它们依次输出。对吗？", "caption": "这段代码想输出数组",
        "shown_code": "for (i = 1; i <= n; i++)\n    printf(\"%d \", a[i]);",
        "template": "#include <stdio.h>\nstatic int a[101];\nint main(void) {\n    int n, i;\n    scanf(\"%d\", &n);\n    for (i = 0; i < n; i++) scanf(\"%d\", &a[i]);\n/*<<CODE>>*/\n    printf(\"\\n\");\n    return 0;\n}\n",
        "has_bug": True, "bug_line": 1, "bug_lines": [1],
        "standard_fix": "for (i = 0; i < n; i++)\n    printf(\"%d \", a[i]);",
        "checks": [{"input": "3\n4 5 6\n", "expected": "4 5 6"}, {"input": "1\n9\n", "expected": "9"}],
        "explanation": "数据放在 a[0] 到 a[n-1]，循环却从 1 走到 n，漏掉了第一个数，还多读了 a[n]。",
    },
]

# 运行没通过时给学生看的“输入”：读 n 再读 n 个数的题，只显示那一行数，更好懂。
for _item in ITEMS:
    if "scanf(\"%d\", &a[i]);" in _item["template"]:
        for _check in _item["checks"]:
            _check.setdefault("show", " ".join(_check["input"].split("\n")[1:]).strip())
