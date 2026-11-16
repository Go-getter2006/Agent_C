"""演示数据的原始内容：题目、测试输入、参考解、演示学生的每一次提交（源码）。

期望输出和每次提交的评测结果不在这里手写，由 build_fixture.py 用沙箱真实运行得到。
学号、姓名都是虚构的。
"""
from __future__ import annotations

TEACHER = "demo-teacher"
CLASSES = [{"id": 31, "name": "C 语言程序设计 01 班"}, {"id": 32, "name": "C 语言程序设计 02 班"}]
DEMO_STUDENT = ("2500000001", "林晓")
CLASSMATES = [
    ("2500000002", "周航"), ("2500000003", "陈一鸣"), ("2500000004", "王思远"), ("2500000005", "赵雨桐"),
    ("2500000006", "刘子墨"), ("2500000007", "孙可欣"), ("2500000008", "吴昊天"), ("2500000009", "郑佳怡"),
    ("2500000010", "冯立"), ("2500000011", "何静"), ("2500000012", "许文博"),
]
OTHER_CLASS_STUDENT = ("2500000101", "别班同学")

DAY = 24 * 60

# 作业：(编号, 名称, 开始于几分钟前, 截止于几分钟后（负数表示已截止）, 班级, 题号)
CONTESTS = [
    (501, "上机练习：一维数组", 6 * DAY, 12 * DAY, [31], [406, 407, 408, 409, 410, 411, 412]),
    (502, "上机练习：循环结构", 20 * DAY, 3 * DAY, [31], [301, 302, 303, 304]),
    (503, "上机练习：选择结构", 34 * DAY, -13 * DAY, [31], [201, 202, 203]),
    (504, "上机练习：指针", 5 * DAY, 9 * DAY, [32], [601]),
]


def _big(n: int, mul: int, add: int, mod: int, shift: int = 0) -> str:
    return " ".join(str((i * mul + add) % mod - shift) for i in range(n))


# 每道题：标题、XDOJ 类别和标签、题面、参考解、测试输入、标注（简述、主技能、次技能、每个测试点考什么）
PROBLEMS: dict[int, dict] = {
    406: {
        "title": "成绩最高分", "category": "一维数组", "tags": ["一维数组"],
        "text": "输入 n 个学生的成绩，输出其中的最高分。\n输入：第一行是整数 n（1 ≤ n ≤ 1000），第二行是 n 个整数。\n输出：一个整数，最高分。",
        "brief": "输入 n 个学生的成绩，输出最高分", "primary": "C06.03.02",
        "secondary": ["C06.02.01", "C06.03.03", "C05.04.03"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i, a[1000];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = a[0];\n    for (i = 1; i < n; i++)\n        if (a[i] > max)\n            max = a[i];\n    printf("%d\\n", max);\n    return 0;\n}\n',
        "tests": [
            ("5\n78 92 65 88 70\n", "C06.03.03", "普通数据", True),
            ("8\n60 71 55 99 83 99 42 67\n", "C06.03.03", "普通数据", True),
            ("6\n-12 -7 -30 -7 -19 -25\n", "C06.03.02", "全是负数", False),
            ("1\n57\n", "C05.04.03", "只有一个数", False),
            ("1000\n" + _big(1000, 37, 11, 101) + "\n", "C06.01.02", "数据很多", False),
        ],
    },
    407: {
        "title": "最低气温", "category": "一维数组", "tags": ["一维数组"],
        "text": "输入一周七天的气温（整数，不低于零下 60 度，不高于 60 度），输出最低气温。\n输入：一行 7 个整数。\n输出：一个整数。",
        "brief": "输入一周七天的气温，输出最低温", "primary": "C06.03.02", "secondary": ["C06.02.01", "C06.03.03"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int temp[7], i;\n    for (i = 0; i < 7; i++)\n        scanf("%d", &temp[i]);\n    int low = temp[0];\n    for (i = 1; i < 7; i++)\n        if (temp[i] < low)\n            low = temp[i];\n    printf("%d\\n", low);\n    return 0;\n}\n',
        "tests": [
            ("3 -2 5 0 7 -6 1\n", "C06.03.03", "普通数据", True),
            ("12 15 9 20 18 11 14\n", "C06.03.02", "全是正数", False),
            ("-3 -8 -1 -8 -5 -2 -4\n", "C06.03.02", "全是负数", False),
            ("25 25 25 25 25 25 25\n", "C06.03.03", "每天都一样", False),
            ("0 4 2 9 1 3 6\n", "C06.03.03", "普通数据", True),
        ],
    },
    408: {
        "title": "数组逆序", "category": "一维数组", "tags": ["一维数组"],
        "text": "输入 n 个整数，把它们倒过来输出。\n输入：第一行是整数 n（1 ≤ n ≤ 1000），第二行是 n 个整数。\n输出：一行，倒序后的 n 个整数，用空格隔开。",
        "brief": "把 n 个数倒过来输出", "primary": "C06.06.03", "secondary": ["C06.02.01", "C06.02.02", "C06.01.02"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i, a[1000];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    for (i = n - 1; i >= 0; i--)\n        printf("%d ", a[i]);\n    printf("\\n");\n    return 0;\n}\n',
        "tests": [
            ("5\n4 8 15 16 23\n", "C06.06.03", "普通数据", True),
            ("1\n42\n", "C05.04.03", "只有一个数", False),
            ("600\n" + _big(600, 13, 5, 997) + "\n", "C06.01.02", "数据很多", False),
            ("1000\n" + _big(1000, 7, 3, 911) + "\n", "C06.01.02", "数据很多", False),
            ("800\n" + _big(800, 29, 1, 733) + "\n", "C06.01.02", "数据很多", False),
        ],
    },
    409: {
        "title": "统计字母", "category": "字符串", "tags": ["字符串", "一维数组"],
        "text": "输入一行文字，统计其中每个英文字母出现的次数，不区分大小写。\n输入：一行，不超过 200 个字符。\n输出：按字母顺序，每个出现过的字母占一行：小写字母、空格、次数。",
        "brief": "统计一行字里每个字母出现几次", "primary": "C08.05.02", "secondary": ["C08.02.02", "C08.04.01", "C06.01.01"],
        "solution": '#include <stdio.h>\n#include <ctype.h>\n\nint main()\n{\n    char line[256];\n    int cnt[26] = {0}, i;\n    if (fgets(line, sizeof(line), stdin) == NULL)\n        return 0;\n    for (i = 0; line[i] != \'\\0\'; i++)\n        if (isalpha((unsigned char)line[i]))\n            cnt[tolower((unsigned char)line[i]) - \'a\']++;\n    for (i = 0; i < 26; i++)\n        if (cnt[i] > 0)\n            printf("%c %d\\n", \'a\' + i, cnt[i]);\n    return 0;\n}\n',
        "tests": [
            ("hello world\n", "C08.05.02", "普通数据", True),
            ("Programming in C is Fun\n", "C08.04.01", "有大写字母", False),
            ("12345 !?\n", "C08.05.02", "没有字母", False),
            ("a\n", "C05.04.03", "只有一个字符", False),
            ("The quick brown fox jumps over the lazy dog\n", "C08.05.02", "普通数据", True),
        ],
    },
    410: {
        "title": "最大差值", "category": "一维数组", "tags": ["一维数组"],
        "text": "输入 n 个整数，输出其中最大值和最小值的差。\n输入：第一行是整数 n（1 ≤ n ≤ 100），第二行是 n 个整数。\n输出：一个整数。",
        "brief": "输入 n 个数，输出最大值和最小值的差", "primary": "C06.03.02", "secondary": ["C06.03.03", "C06.02.01"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i, a[100];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = a[0], min = a[0];\n    for (i = 1; i < n; i++) {\n        if (a[i] > max) max = a[i];\n        if (a[i] < min) min = a[i];\n    }\n    printf("%d\\n", max - min);\n    return 0;\n}\n',
        "tests": [
            ("5\n3 -4 9 -1 6\n", "C06.03.03", "普通数据", True),
            ("6\n-7 2 0 11 -3 5\n", "C06.03.03", "普通数据", True),
            ("4\n-9 -3 -14 -6\n", "C06.03.02", "全是负数", False),
            ("1\n8\n", "C05.04.03", "只有一个数", False),
            ("5\n21 34 8 15 40\n", "C06.03.02", "全是正数", False),
        ],
    },
    411: {
        "title": "高于平均分的人数", "category": "一维数组", "tags": ["一维数组"],
        "text": "输入 n 个学生的成绩，输出平均分（保留两位小数）和高于平均分的人数。\n输入：第一行是整数 n（1 ≤ n ≤ 100），第二行是 n 个整数。\n输出：一行，平均分和人数，用空格隔开。",
        "brief": "输入 n 个成绩，输出平均分和高于平均分的人数", "primary": "C06.03.05",
        "secondary": ["C06.03.01", "C01.02.03"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i, a[100], sum = 0, cnt = 0;\n    scanf("%d", &n);\n    for (i = 0; i < n; i++) {\n        scanf("%d", &a[i]);\n        sum += a[i];\n    }\n    double avg = (double)sum / n;\n    for (i = 0; i < n; i++)\n        if (a[i] > avg)\n            cnt++;\n    printf("%.2f %d\\n", avg, cnt);\n    return 0;\n}\n',
        "tests": [
            ("5\n78 92 65 88 69\n", "C06.03.05", "平均分不是整数", False),
            ("4\n60 70 80 90\n", "C06.03.01", "普通数据", True),
            ("3\n1 2 2\n", "C06.03.05", "平均分不是整数", False),
            ("2\n100 100\n", "C06.03.01", "分数都一样", False),
            ("6\n55 61 70 82 90 97\n", "C06.03.05", "平均分不是整数", False),
        ],
    },
    412: {
        "title": "查找元素", "category": "一维数组", "tags": ["一维数组", "查找"],
        "text": "输入 n 个整数和一个要找的数 x，输出 x 第一次出现的位置（从 1 开始数）；没有就输出 -1。\n输入：第一行是 n（1 ≤ n ≤ 100），第二行是 n 个整数，第三行是 x。\n输出：一个整数。",
        "brief": "在 n 个数里找 x 第一次出现的位置", "primary": "C06.04.01", "secondary": ["C06.04.02", "C06.02.02"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i, x, a[100], pos = -1;\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    scanf("%d", &x);\n    for (i = 0; i < n; i++)\n        if (a[i] == x) {\n            pos = i + 1;\n            break;\n        }\n    printf("%d\\n", pos);\n    return 0;\n}\n',
        "tests": [
            ("5\n3 8 6 1 9\n6\n", "C06.04.01", "普通数据", True),
            ("4\n2 4 6 8\n5\n", "C06.04.02", "找不到", False),
            ("6\n7 3 7 7 1 7\n7\n", "C06.04.01", "出现了好几次", False),
            ("1\n4\n4\n", "C05.04.03", "只有一个数", False),
            ("5\n10 20 30 40 50\n50\n", "C06.02.02", "在最后一个位置", False),
        ],
    },
    301: {
        "title": "阶乘求和", "category": "循环结构", "tags": ["循环"],
        "text": "输入 n，输出 1! + 2! + … + n! 的值。\n输入：一个整数 n（1 ≤ n ≤ 20）。\n输出：一个整数。",
        "brief": "输入 n，输出 1! 到 n! 的和", "primary": "C05.05.01", "secondary": ["C02.01.02", "C01.02.02", "C05.03.01"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    long long s = 0, f = 1;\n    scanf("%d", &n);\n    for (i = 1; i <= n; i++) {\n        f = f * i;\n        s = s + f;\n    }\n    printf("%lld\\n", s);\n    return 0;\n}\n',
        "tests": [
            ("1\n", "C05.04.03", "n 等于 1", False), ("5\n", "C05.05.01", "普通数据", True),
            ("10\n", "C05.05.01", "普通数据", True), ("15\n", "C02.01.02", "n 比较大", False),
            ("20\n", "C02.01.02", "n 比较大", False),
        ],
    },
    302: {
        "title": "各位数字之和", "category": "循环结构", "tags": ["循环"],
        "text": "输入一个正整数，输出它各位数字的和。\n输入：一个正整数（不超过 10 的 9 次方）。\n输出：一个整数。",
        "brief": "输入一个正整数，输出各位数字的和", "primary": "C12.03.01", "secondary": ["C05.01.01", "C03.01.02"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, s = 0;\n    scanf("%d", &n);\n    while (n > 0) {\n        s += n % 10;\n        n /= 10;\n    }\n    printf("%d\\n", s);\n    return 0;\n}\n',
        "tests": [("7\n", "C05.04.03", "只有一位", False), ("123\n", "C12.03.01", "普通数据", True),
                  ("9999\n", "C12.03.01", "普通数据", True), ("1000000\n", "C12.03.01", "中间有很多 0", False),
                  ("80706\n", "C12.03.01", "普通数据", True)],
    },
    303: {
        "title": "素数判断", "category": "循环结构", "tags": ["循环", "数学"],
        "text": "输入一个正整数 n，判断它是不是素数。\n输入：一个整数 n（1 ≤ n ≤ 100000）。\n输出：是素数输出 Yes，否则输出 No。",
        "brief": "输入 n，判断是不是素数", "primary": "C12.04.01", "secondary": ["C05.04.01", "C05.04.03", "C05.07.01"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int n, i, ok;\n    scanf("%d", &n);\n    ok = n > 1;\n    for (i = 2; i * i <= n; i++)\n        if (n % i == 0) {\n            ok = 0;\n            break;\n        }\n    printf(ok ? "Yes\\n" : "No\\n");\n    return 0;\n}\n',
        "tests": [("7\n", "C12.04.01", "普通数据", True), ("12\n", "C12.04.01", "普通数据", True),
                  ("1\n", "C05.04.03", "n 等于 1", False), ("2\n", "C05.04.01", "n 等于 2", False),
                  ("97\n", "C12.04.01", "普通数据", True)],
    },
    304: {
        "title": "多组数据求和", "category": "循环结构", "tags": ["循环", "多组输入"],
        "text": "有多组数据，每组是两个整数 a 和 b，输出它们的和。一直处理到输入结束。\n输入：若干行，每行两个整数。\n输出：每组数据输出一行，a + b 的值。",
        "brief": "有多组数据，每组两个整数，输出它们的和", "primary": "C05.08.01", "secondary": ["C01.03.01", "C05.01.01"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int a, b;\n    while (scanf("%d %d", &a, &b) == 2)\n        printf("%d\\n", a + b);\n    return 0;\n}\n',
        "tests": [("1 2\n", "C05.08.01", "只有一组", False), ("1 2\n3 4\n", "C05.08.01", "有好几组", False),
                  ("10 20\n-5 5\n7 8\n", "C05.08.01", "有好几组", False),
                  ("0 0\n100 200\n3 3\n9 1\n", "C05.08.01", "有好几组", False),
                  ("-1 -1\n-2 -3\n", "C05.08.01", "有好几组", False)],
    },
    201: {
        "title": "成绩等级", "category": "选择结构", "tags": ["分支"],
        "text": "输入一个百分制成绩，输出等级：90 分及以上为 A，80 到 89 为 B，70 到 79 为 C，60 到 69 为 D，60 以下为 E。\n输入：一个整数（0 到 100）。\n输出：一个大写字母。",
        "brief": "输入百分制成绩，输出 A 到 E 的等级", "primary": "C04.02.01", "secondary": ["C04.01.02", "C04.02.02"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int x;\n    scanf("%d", &x);\n    if (x >= 90) printf("A\\n");\n    else if (x >= 80) printf("B\\n");\n    else if (x >= 70) printf("C\\n");\n    else if (x >= 60) printf("D\\n");\n    else printf("E\\n");\n    return 0;\n}\n',
        "tests": [("95\n", "C04.02.01", "普通数据", True), ("90\n", "C04.01.02", "正好在分界上", False),
                  ("75\n", "C04.02.01", "普通数据", True), ("60\n", "C04.01.02", "正好在分界上", False),
                  ("59\n", "C04.02.01", "普通数据", True)],
    },
    202: {
        "title": "闰年判断", "category": "选择结构", "tags": ["分支"],
        "text": "输入一个年份，判断它是不是闰年。能被 4 整除但不能被 100 整除，或者能被 400 整除的年份是闰年。\n输入：一个整数。\n输出：是闰年输出 Yes，否则输出 No。",
        "brief": "输入年份，判断是不是闰年", "primary": "C03.03.01", "secondary": ["C03.03.02", "C04.01.01"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int y;\n    scanf("%d", &y);\n    if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)\n        printf("Yes\\n");\n    else\n        printf("No\\n");\n    return 0;\n}\n',
        "tests": [("2024\n", "C03.03.01", "普通的闰年", False), ("1900\n", "C03.03.02", "整百但不是闰年", False),
                  ("2000\n", "C03.03.02", "能被 400 整除", False), ("2023\n", "C03.03.01", "普通数据", True),
                  ("2100\n", "C03.03.02", "整百但不是闰年", False)],
    },
    203: {
        "title": "三数排序", "category": "选择结构", "tags": ["分支"],
        "text": "输入三个整数，按从小到大的顺序输出。\n输入：一行三个整数。\n输出：一行，从小到大的三个整数，用空格隔开。",
        "brief": "输入三个整数，从小到大输出", "primary": "C04.03.01", "secondary": ["C06.05.02", "C01.02.04"],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int a, b, c, t;\n    scanf("%d %d %d", &a, &b, &c);\n    if (a > b) { t = a; a = b; b = t; }\n    if (a > c) { t = a; a = c; c = t; }\n    if (b > c) { t = b; b = c; c = t; }\n    printf("%d %d %d\\n", a, b, c);\n    return 0;\n}\n',
        "tests": [("3 1 2\n", "C04.03.01", "普通数据", True), ("1 2 3\n", "C04.03.01", "已经排好", False),
                  ("9 5 1\n", "C04.03.01", "完全倒着", False), ("4 4 2\n", "C04.03.01", "有相同的数", False),
                  ("-1 -7 0\n", "C04.03.01", "有负数", False)],
    },
    601: {
        "title": "交换两个数", "category": "指针", "tags": ["指针"],
        "text": "用指针作参数，写函数交换两个整数的值。\n输入：两个整数。\n输出：交换后的两个整数。",
        "brief": "用指针参数交换两个整数", "primary": "C10.02.01", "secondary": ["C10.01.02"],
        "solution": '#include <stdio.h>\n\nvoid swap(int *x, int *y)\n{\n    int t = *x;\n    *x = *y;\n    *y = t;\n}\n\nint main()\n{\n    int a, b;\n    scanf("%d %d", &a, &b);\n    swap(&a, &b);\n    printf("%d %d\\n", a, b);\n    return 0;\n}\n',
        "tests": [("1 2\n", "C10.02.01", "普通数据", True), ("5 5\n", "C10.02.01", "两个数相同", False)],
    },
    999: {
        "title": "没有布置的题", "category": "其他", "tags": [],
        "text": "这道题在题库里，但没有布置给任何班级。\n输入：一个整数。\n输出：这个整数加一。",
        "brief": "输入一个整数，输出它加一", "primary": "C03.01.03", "secondary": [],
        "solution": '#include <stdio.h>\n\nint main()\n{\n    int x;\n    scanf("%d", &x);\n    printf("%d\\n", x + 1);\n    return 0;\n}\n',
        "tests": [("1\n", "C03.01.03", "普通数据", True)],
    },
}

# ---------------------------------------------------------------- 有错的写法（演示学生和同学都会用到）
BUGGY: dict[str, dict] = {
    "406-ce": {  # 第 10 行少分号
        "problem": 406, "label": None,
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[1000];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = 0\n    for (i = 1; i < n; i++)\n        if (a[i] > max) max = a[i];\n    printf("%d\\n", max);\n    return 0;\n}\n',
    },
    "406-skip-first": {  # 初值 0，而且比较从第二个数开始
        "problem": 406, "label": ("TY09", "C06.03.02", 10, "max 的初值是 0"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[1000];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = 0;\n    for (i = 1; i < n; i++)\n        if (a[i] > max) max = a[i];\n    printf("%d\\n", max);\n    return 0;\n}\n',
    },
    "406-zero-init": {
        "problem": 406, "label": ("TY09", "C06.03.02", 10, "max 的初值是 0"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[1000];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = 0;\n    for (i = 0; i < n; i++)\n        if (a[i] > max) max = a[i];\n    printf("%d\\n", max);\n    return 0;\n}\n',
    },
    "407-sentinel": {  # 用 100 当初值：这道题的数据范围内碰巧是对的
        "problem": 407, "label": None,
        "source": '#include <stdio.h>\n\nint main()\n{\n    int temp[7], i;\n    for (i = 0; i < 7; i++)\n        scanf("%d", &temp[i]);\n    int min = 100;\n    for (i = 0; i < 7; i++)\n        if (temp[i] < min) min = temp[i];\n    printf("%d\\n", min);\n    return 0;\n}\n',
    },
    "408-index": {
        "problem": 408, "label": ("AR04", "C06.02.02", 10, "读入从 0 开始，输出却从 n 到 1"), "ub": True,
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[100];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    for (i = n; i >= 1; i--)\n        printf("%d ", a[i]);\n    return 0;\n}\n',
    },
    "408-small-array": {
        "problem": 408, "label": ("AR02", "C06.01.02", 6, "数组只开了 100"), "ub": True,
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[100];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    for (i = n - 1; i >= 0; i--)\n        printf("%d ", a[i]);\n    return 0;\n}\n',
    },
    "410-zero-init": {
        "problem": 410, "label": ("TY09", "C06.03.02", 10, "max 和 min 的初值都是 0"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[100];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = 0, min = 0;\n    for (i = 0; i < n; i++) {\n        if (a[i] > max) max = a[i];\n        if (a[i] < min) min = a[i];\n    }\n    printf("%d\\n", max - min);\n    return 0;\n}\n',
    },
    "411-intdiv": {
        "problem": 411, "label": None,  # 规则能判：输出等于正确值截掉小数
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i, a[100];\n    int sum = 0, cnt = 0;\n    scanf("%d", &n);\n    for (i = 0; i < n; i++) {\n        scanf("%d", &a[i]);\n        sum += a[i];\n    }\n    double avg = sum / n;\n    for (i = 0; i < n; i++)\n        if (a[i] > avg) cnt++;\n    printf("%.2f %d\\n", avg, cnt);\n    return 0;\n}\n',
    },
    "301-int": {
        "problem": 301, "label": ("TY02", "C02.01.02", 6, "s 和 f 都是 int，n 大的时候放不下"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int s = 0, f = 1;\n    scanf("%d", &n);\n    for (i = 1; i <= n; i++) {\n        f = f * i;\n        s = s + f;\n    }\n    printf("%d\\n", s);\n    return 0;\n}\n',
    },
    "301-printf": {
        "problem": 301, "label": ("IO02", "C01.02.02", 12, "s 是 long long，输出却用了 %d"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    long long s = 0, f = 1;\n    scanf("%d", &n);\n    for (i = 1; i <= n; i++) {\n        f = f * i;\n        s = s + f;\n    }\n    printf("%d\\n", s);\n    return 0;\n}\n',
    },
    "303-one": {
        "problem": 303, "label": ("AL02", "C05.04.03", 7, "n 等于 1 时循环不执行，直接当成素数"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    scanf("%d", &n);\n    int ok = 1;\n    for (i = 2; i * i <= n; i++)\n        if (n % i == 0) ok = 0;\n    if (ok) printf("Yes\\n");\n    else printf("No\\n");\n    return 0;\n}\n',
    },
    "304-single": {
        "problem": 304, "label": ("IO10", "C05.08.01", 6, "只读了一组数据"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int a, b;\n    scanf("%d %d", &a, &b);\n    printf("%d\\n", a + b);\n    return 0;\n}\n',
    },
    "202-and": {
        "problem": 202, "label": ("BR03", "C03.03.01", 7, "三个条件全用 && 连在一起"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int y;\n    scanf("%d", &y);\n    if (y % 4 == 0 && y % 100 != 0 && y % 400 == 0)\n        printf("Yes\\n");\n    else\n        printf("No\\n");\n    return 0;\n}\n',
    },
    "203-lines": {
        "problem": 203, "label": None,  # 规则能判：去掉空白后和标准输出一致
        "source": '#include <stdio.h>\n\nint main()\n{\n    int a, b, c, t;\n    scanf("%d %d %d", &a, &b, &c);\n    if (a > b) { t = a; a = b; b = t; }\n    if (a > c) { t = a; a = c; c = t; }\n    if (b > c) { t = b; b = c; c = t; }\n    printf("%d\\n%d\\n%d\\n", a, b, c);\n    return 0;\n}\n',
    },
    "201-boundary": {
        "problem": 201, "label": ("BR04", "C04.01.02", 7, "分界用了 >，正好等于分界的分数归错了等级"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int x;\n    scanf("%d", &x);\n    if (x > 90) printf("A\\n");\n    else if (x > 80) printf("B\\n");\n    else if (x > 70) printf("C\\n");\n    else if (x > 60) printf("D\\n");\n    else printf("E\\n");\n    return 0;\n}\n',
    },
    "302-loop": {
        "problem": 302, "label": ("LP04", "C05.01.01", 7, "循环条件写成了 n > 10，最高位没加上"),
        "source": '#include <stdio.h>\n\nint main()\n{\n    int n, s = 0;\n    scanf("%d", &n);\n    while (n > 10) {\n        s += n % 10;\n        n /= 10;\n    }\n    printf("%d\\n", s);\n    return 0;\n}\n',
    },
}

# 演示学生的提交：(题号, 写法（"ok" 表示自己写对了的版本）, 几分钟前)
# 自己写对的版本和参考解略有不同时，放在 OWN_SOLUTIONS 里。
OWN_SOLUTIONS: dict[int, str] = {
    410: '#include <stdio.h>\n\nint main()\n{\n    int n, i;\n    int a[100];\n    scanf("%d", &n);\n    for (i = 0; i < n; i++)\n        scanf("%d", &a[i]);\n    int max = a[0], min = a[0];\n    for (i = 1; i < n; i++) {\n        if (a[i] > max) max = a[i];\n        if (a[i] < min) min = a[i];\n    }\n    printf("%d\\n", max - min);\n    return 0;\n}\n',
    411: '#include <stdio.h>\n\nint main()\n{\n    int n, i, a[100];\n    int sum = 0, cnt = 0;\n    scanf("%d", &n);\n    for (i = 0; i < n; i++) {\n        scanf("%d", &a[i]);\n        sum += a[i];\n    }\n    double avg = (double)sum / n;\n    for (i = 0; i < n; i++)\n        if (a[i] > avg) cnt++;\n    printf("%.2f %d\\n", avg, cnt);\n    return 0;\n}\n',
}

DEMO_SUBMISSIONS: list[tuple[int, str, int]] = [
    (201, "ok", 30 * DAY),
    (202, "202-and", 29 * DAY + 5), (202, "ok", 29 * DAY),
    (203, "203-lines", 28 * DAY + 4), (203, "ok", 28 * DAY),
    (302, "ok", 12 * DAY),
    (303, "303-one", 11 * DAY + 6), (303, "ok", 11 * DAY),
    (304, "304-single", 10 * DAY + 9), (304, "ok", 10 * DAY),
    (301, "301-int", 9 * DAY + 20), (301, "301-printf", 9 * DAY),
    (411, "411-intdiv", 2 * DAY + 7), (411, "ok", 2 * DAY),
    (410, "410-zero-init", 1 * DAY + 25), (410, "ok", 1 * DAY),
    (408, "408-index", 55), (408, "408-small-array", 51),
    (407, "407-sentinel", 28),
    (406, "406-ce", 17), (406, "406-skip-first", 11), (406, "406-zero-init", 6),
]

# 同学会犯的错（题号 -> 可能的有错写法），用来让教师端有全班的数据可看
CLASSMATE_BUGS: dict[int, list[str]] = {
    406: ["406-zero-init", "406-skip-first"], 408: ["408-small-array", "408-index"], 410: ["410-zero-init"],
    411: ["411-intdiv"], 301: ["301-int", "301-printf"], 303: ["303-one"], 304: ["304-single"],
    202: ["202-and"], 203: ["203-lines"], 201: ["201-boundary"], 302: ["302-loop"],
}
