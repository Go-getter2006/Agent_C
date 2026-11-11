"""诊断题库的源文件：运行本文件会生成 app/item_bank.json。

每道题字段说明：
    id / kp / title / task       题号、知识点、标题、这段代码本来要做什么
    has_bug                      True=有错题（五步作答），False=无错题（只判断有没有错）
    code                         代码（行号从 1 开始）
    bug_lines                    错误所在行（学生点中其中任一行算定位正确）
    cause / trigger              参考答案：错误原因、在什么情况下触发
    explain_keywords             模拟模式下判“解释”一步用的关键词（真实模式由大模型判）
    fix / fix_rationale          参考修复及其原理
    fix_check / fix_forbid       模拟模式下判“修复”一步：修复后代码须含任一 check、且不含任一 forbid
    explanation                  无错题：为什么没错
    variant                      变式题：题面、测试用例、参考程序（参考程序不会发给学生）
    irt                          a=区分度，d=难度（初始为经验值，收集数据后重新标定）
"""
import json
from pathlib import Path

ITEMS = []


def item(**kw):
    kw["code"] = kw["code"].strip("\n")
    if "variant" in kw and kw["variant"].get("reference"):
        kw["variant"]["reference"] = kw["variant"]["reference"].strip("\n")
    ITEMS.append(kw)


# ---------------------------------------------------------------- c03 输入输出
item(
    id="io-01", kp="c03", title="读入一个整数", task="读入一个整数 n，输出 2n。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", n);
    printf("%d\n", n * 2);
    return 0;
}
""",
    bug_lines=[5], bug_type="scanf 缺少取地址符",
    cause="scanf 需要变量的地址才能把读到的值存进去，这里传入的是 n 的值（而且 n 还没有初始化），scanf 会往一个不确定的地址写数据。",
    trigger="只要执行到这一行就会出问题：常见表现是程序崩溃，或者 n 没被赋值、输出一个随机数。",
    explain_keywords={"cause": ["&", "地址"], "trigger": ["崩溃", "随机", "任何", "都会", "执行", "运行", "只要"]},
    fix='    scanf("%d", &n);', fix_check=["&n"], fix_forbid=[],
    fix_rationale="加上 & 以后传入的是 n 的地址，scanf 才能把读到的整数写进 n。",
    variant={
        "prompt": "输入两个整数 a 和 b，输出它们的和。",
        "tests": [{"in": "3 5\n", "out": "8"}, {"in": "-2 7\n", "out": "5"}, {"in": "0 0\n", "out": "0"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int a, b;
    scanf("%d %d", &a, &b);
    printf("%d\n", a + b);
    return 0;
}
""",
        "check_tokens": ["&a", "&b"],
    },
    irt={"a": 1.2, "d": -0.8},
)

item(
    id="io-02", kp="c03", title="读入实数半径", task="读入圆的半径 r（实数），输出面积，保留两位小数。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    double r;
    scanf("%f", &r);
    printf("%.2f\n", 3.14159 * r * r);
    return 0;
}
""",
    bug_lines=[5], bug_type="scanf 格式符与类型不匹配",
    cause="r 是 double，scanf 读 double 必须用 %lf；用 %f 会按 float 的格式写入，r 得到的是错误的值。",
    trigger="输入任何半径，输出的面积都不对（例如输入 1 不会输出 3.14）。",
    explain_keywords={"cause": ["%lf", "lf", "double", "float"], "trigger": ["任何", "都", "例如", "比如", "输入"]},
    fix='    scanf("%lf", &r);', fix_check=["%lf"], fix_forbid=[],
    fix_rationale="%lf 告诉 scanf 按 double 的格式存储，和变量 r 的类型一致。",
    variant={
        "prompt": "输入一个实数 x，输出 x 的平方，保留三位小数。",
        "tests": [{"in": "1.5\n", "out": "2.250"}, {"in": "-2\n", "out": "4.000"}, {"in": "0\n", "out": "0.000"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    double x;
    scanf("%lf", &x);
    printf("%.3f\n", x * x);
    return 0;
}
""",
        "check_tokens": ["%lf"],
    },
    irt={"a": 1.2, "d": -0.3},
)

item(
    id="io-03", kp="c03", title="读入年龄和等级", task="先读入一个整数年龄，再读入一个表示等级的字符，然后原样输出。",
    has_bug=False,
    code=r"""
#include <stdio.h>

int main(void) {
    int age;
    char grade;
    scanf("%d", &age);
    scanf(" %c", &grade);
    printf("%d %c\n", age, grade);
    return 0;
}
""",
    explanation="\" %c\" 前面的空格会跳过上一次输入留下的换行符和空格，所以能正确读到等级字符，这段代码没有错。",
    expected_io={"in": "18\nA\n", "out": "18 A"},
    irt={"a": 1.0, "d": 0.0},
)

# ---------------------------------------------------------------- c05 循环结构
item(
    id="loop-01", kp="c05", title="求 1 到 n 的和", task="输入正整数 n，输出 1+2+…+n。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int n, sum = 0;
    scanf("%d", &n);
    for (int i = 1; i < n; i++) {
        sum += i;
    }
    printf("%d\n", sum);
    return 0;
}
""",
    bug_lines=[6], bug_type="循环边界少一次",
    cause="循环条件是 i < n，i 最大只到 n-1，第 n 项没有加进去。",
    trigger="任何 n ≥ 1 都会少加 n，例如 n=3 输出 3 而不是 6；n=1 时输出 0。",
    explain_keywords={"cause": ["i<n", "i < n", "n-1", "少", "最后", "第n", "没加", "边界"],
                      "trigger": ["n=", "例如", "比如", "任何", "都会", "输入"]},
    fix="    for (int i = 1; i <= n; i++) {", fix_check=["i<=n"], fix_forbid=["i<n;"],
    fix_rationale="改成 i <= n 后，i 能取到 n，第 n 项也被加上。",
    variant={
        "prompt": "输入正整数 n，输出 1 到 n 中所有偶数的和。",
        "tests": [{"in": "4\n", "out": "6"}, {"in": "1\n", "out": "0"}, {"in": "10\n", "out": "30"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int n, sum = 0;
    scanf("%d", &n);
    for (int i = 2; i <= n; i += 2) sum += i;
    printf("%d\n", sum);
    return 0;
}
""",
        "check_tokens": ["<=", "%2", "+=2", "+= 2"],
    },
    irt={"a": 1.3, "d": -0.5},
)

item(
    id="loop-02", kp="c05", title="依次输出 1 到 n", task="输入正整数 n，每行输出一个数，依次输出 1 到 n。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int n, i = 1;
    scanf("%d", &n);
    while (i <= n) {
        printf("%d\n", i);
    }
    return 0;
}
""",
    bug_lines=[6, 7, 8], bug_type="循环变量没有更新（死循环）",
    cause="循环体里没有改变 i，条件 i <= n 一直成立，变成死循环，不停输出 1。",
    trigger="只要输入的 n ≥ 1 就会无限输出 1；n ≤ 0 时不进入循环，反而正常结束。",
    explain_keywords={"cause": ["i++", "自增", "没有改变", "不变", "死循环", "一直", "无限"],
                      "trigger": ["n>=1", "n≥1", "大于等于1", "正整数", "只要", "任何", "都会", "n<=0", "n≤0"]},
    fix="        i++;", fix_check=["i++", "++i", "i+=1", "i=i+1"], fix_forbid=[],
    fix_rationale="每输出一次就让 i 加 1，i 最终会超过 n，循环才能结束。",
    variant={
        "prompt": "输入正整数 n，每行输出一个数，按从 n 到 1 的顺序输出。",
        "tests": [{"in": "3\n", "out": "3\n2\n1"}, {"in": "1\n", "out": "1"}, {"in": "5\n", "out": "5\n4\n3\n2\n1"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int n;
    scanf("%d", &n);
    for (int i = n; i >= 1; i--) printf("%d\n", i);
    return 0;
}
""",
        "check_tokens": ["--", "-=1", "-= 1"],
    },
    irt={"a": 1.2, "d": -0.6},
)

item(
    id="loop-03", kp="c05", title="求 n 的阶乘", task="输入非负整数 n（n ≤ 12），输出 n!。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int n, i, fact = 1;
    scanf("%d", &n);
    for (i = 1; i <= n; i++);
    {
        fact *= i;
    }
    printf("%d\n", fact);
    return 0;
}
""",
    bug_lines=[6], bug_type="for 后面多了分号",
    cause="for 语句末尾多了一个分号，循环体变成空语句；下面花括号里的乘法只在循环结束后执行一次，此时 i = n+1。",
    trigger="输出总是 n+1：n=5 输出 6；只有 n=0 时碰巧输出 1，看起来是对的。",
    explain_keywords={"cause": ["分号", ";", "空语句", "循环体"], "trigger": ["n+1", "n=0", "碰巧", "总是", "例如", "比如", "任何"]},
    fix="    for (i = 1; i <= n; i++) {", fix_check=[], fix_forbid=["i++);"],
    fix_rationale="去掉分号后，花括号里的乘法才是循环体，每次循环都会执行。",
    variant={
        "prompt": "输入正整数 n，输出 1 到 n 中能被 3 整除的数的个数。",
        "tests": [{"in": "10\n", "out": "3"}, {"in": "2\n", "out": "0"}, {"in": "9\n", "out": "3"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int n, cnt = 0;
    scanf("%d", &n);
    for (int i = 1; i <= n; i++) {
        if (i % 3 == 0) cnt++;
    }
    printf("%d\n", cnt);
    return 0;
}
""",
        "check_tokens": ["%3", "% 3"],
    },
    irt={"a": 1.3, "d": 0.2},
)

item(
    id="loop-04", kp="c05", title="求 n 个数的最大值", task="输入 n（n ≥ 1）和 n 个整数，输出其中最大的数。",
    has_bug=False,
    code=r"""
#include <stdio.h>

int main(void) {
    int n, x, max;
    scanf("%d", &n);
    scanf("%d", &max);
    for (int i = 1; i < n; i++) {
        scanf("%d", &x);
        if (x > max) max = x;
    }
    printf("%d\n", max);
    return 0;
}
""",
    explanation="第一个数先读进 max，循环从 i=1 开始只需要再读 n-1 个数，所以 i < n 是对的，这段代码没有错。",
    expected_io={"in": "4\n3 -1 9 2\n", "out": "9"},
    irt={"a": 1.0, "d": -0.2},
)

# ---------------------------------------------------------------- c06 数组
item(
    id="arr-01", kp="c06", title="给数组赋平方值", task="把 0~4 的平方依次存入数组 a，然后输出 a[4]。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int a[5];
    for (int i = 0; i <= 5; i++) {
        a[i] = i * i;
    }
    printf("%d\n", a[4]);
    return 0;
}
""",
    bug_lines=[5, 6], bug_type="数组下标越界",
    cause="数组 a 只有 a[0]~a[4] 五个元素，循环条件 i <= 5 让 i 取到 5，a[5] 越界写入。",
    trigger="循环最后一次（i=5）时触发，会改写数组后面的内存，结果可能看起来正常，也可能出错或崩溃。",
    explain_keywords={"cause": ["越界", "a[5]", "下标", "<=", "超出", "只有5个", "a[4]"],
                      "trigger": ["i=5", "i等于5", "最后一次", "第6次", "取到5"]},
    fix="    for (int i = 0; i < 5; i++) {", fix_check=["i<5"], fix_forbid=["i<=5"],
    fix_rationale="下标从 0 开始，5 个元素的最大下标是 4，所以条件应为 i < 5。",
    variant={
        "prompt": "输入 n（1 ≤ n ≤ 100）和 n 个整数，按相反顺序输出（用空格隔开）。",
        "tests": [{"in": "3\n1 2 3\n", "out": "3 2 1"}, {"in": "1\n5\n", "out": "5"}, {"in": "4\n-1 0 7 2\n", "out": "2 7 0 -1"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int n, a[100];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    for (int i = n - 1; i >= 0; i--) printf("%d ", a[i]);
    printf("\n");
    return 0;
}
""",
        "check_tokens": ["n-1", "n - 1"],
    },
    irt={"a": 1.2, "d": -0.3},
)

item(
    id="arr-02", kp="c06", title="求数组最大值", task="输入 n（1 ≤ n ≤ 100）和 n 个整数，输出最大值。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int n, a[100], max = 0;
    scanf("%d", &n);
    for (int i = 0; i < n; i++) {
        scanf("%d", &a[i]);
        if (a[i] > max) max = a[i];
    }
    printf("%d\n", max);
    return 0;
}
""",
    bug_lines=[4], bug_type="初值设置不当",
    cause="max 的初值设成 0，如果所有数都比 0 小，max 永远不会被更新。",
    trigger="输入的数全是负数时触发，例如 3 个数 -1 -8 -3，输出 0 而不是 -1。",
    explain_keywords={"cause": ["初值", "初始", "max=0", "max = 0", "0"], "trigger": ["负数", "全是负", "都小于0", "都为负", "小于0"]},
    fix="    int n, a[100], max;  /* 读入第一个数后令 max = a[0] */",
    fix_check=["INT_MIN", "max=a[0]", "max = a[0]", "i==0", "i == 0", "-2147483648"], fix_forbid=["max=0", "max = 0"],
    fix_rationale="让 max 从第一个实际读入的数开始比较（或从 INT_MIN 开始），就不依赖数据的正负。",
    variant={
        "prompt": "输入 n（1 ≤ n ≤ 100）和 n 个整数，输出最小值。",
        "tests": [{"in": "3\n5 9 7\n", "out": "5"}, {"in": "3\n-1 -8 -3\n", "out": "-8"}, {"in": "1\n0\n", "out": "0"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int n, x, min;
    scanf("%d", &n);
    scanf("%d", &min);
    for (int i = 1; i < n; i++) {
        scanf("%d", &x);
        if (x < min) min = x;
    }
    printf("%d\n", min);
    return 0;
}
""",
        "check_tokens": ["<"],
    },
    irt={"a": 1.4, "d": 0.3},
)

item(
    id="arr-03", kp="c06", title="冒泡排序", task="把数组 {5,2,4,1,3} 从小到大排序后输出。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int a[5] = {5, 2, 4, 1, 3};
    for (int i = 0; i < 5 - 1; i++) {
        for (int j = 0; j < 5 - i; j++) {
            if (a[j] > a[j + 1]) {
                int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
            }
        }
    }
    for (int i = 0; i < 5; i++) printf("%d ", a[i]);
    return 0;
}
""",
    bug_lines=[6, 7], bug_type="内层循环边界导致越界",
    cause="内层循环条件 j < 5 - i 在 i=0 时让 j 取到 4，此时 a[j+1] 就是 a[5]，越界访问。",
    trigger="第一轮（i=0）的最后一次比较就会访问 a[5]；越界读到的值可能被换进数组，导致结果出错。",
    explain_keywords={"cause": ["越界", "a[j+1]", "a[j + 1]", "a[5]", "j+1", "-1"], "trigger": ["i=0", "第一轮", "j=4", "j等于4", "最后一次"]},
    fix="        for (int j = 0; j < 5 - 1 - i; j++) {", fix_check=["5-1-i", "4-i", "5-i-1"], fix_forbid=["j<5-i;"],
    fix_rationale="每次比较 a[j] 和 a[j+1]，j 最大只能到 5-2-i，所以条件写成 j < 5-1-i。",
    variant={
        "prompt": "输入 n（1 ≤ n ≤ 100）和 n 个整数，用冒泡排序从小到大输出（用空格隔开）。",
        "tests": [{"in": "5\n3 1 4 1 5\n", "out": "1 1 3 4 5"}, {"in": "1\n7\n", "out": "7"}, {"in": "3\n-2 9 0\n", "out": "-2 0 9"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    int n, a[100];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - 1 - i; j++)
            if (a[j] > a[j + 1]) { int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t; }
    for (int i = 0; i < n; i++) printf("%d ", a[i]);
    printf("\n");
    return 0;
}
""",
        "check_tokens": ["n-1-i", "n - 1 - i", "n-i-1"],
    },
    irt={"a": 1.3, "d": 0.5},
)

item(
    id="arr-04", kp="c06", title="求数组平均值", task="求数组 {1,2,3,4} 的平均值，保留两位小数输出。",
    has_bug=False,
    code=r"""
#include <stdio.h>

int main(void) {
    int a[4] = {1, 2, 3, 4};
    int sum = 0;
    for (int i = 0; i < 4; i++) sum += a[i];
    double avg = (double)sum / 4;
    printf("%.2f\n", avg);
    return 0;
}
""",
    explanation="(double)sum / 4 先把 sum 转成 double 再做实数除法，输出 2.50，没有整数除法的问题，这段代码没有错。",
    expected_io={"in": "", "out": "2.50"},
    irt={"a": 1.0, "d": 0.0},
)

# ---------------------------------------------------------------- c07 字符串
item(
    id="str-01", kp="c07", title="复制字符串", task="把 \"hello\" 复制到字符数组 s 中并输出。",
    has_bug=True,
    code=r"""
#include <stdio.h>
#include <string.h>

int main(void) {
    char s[5];
    strcpy(s, "hello");
    printf("%s\n", s);
    return 0;
}
""",
    bug_lines=[5, 6], bug_type="字符数组空间不足（没给结尾的 \\0 留位置）",
    cause="\"hello\" 有 5 个字符，再加结尾的 '\\0' 需要 6 个字节，s 只有 5 个字节，strcpy 越界写入。",
    trigger="复制的字符串长度大于等于数组长度时触发；这里 \"hello\" 正好 5 个字符，结尾的 '\\0' 写到了数组外面。",
    explain_keywords={"cause": ["\\0", "结束符", "结尾", "6个", "6 个", "越界", "空间不够", "不够"],
                      "trigger": ["长度", "5个字符", "5 个字符", "等于", "大于等于", "hello", "超过"]},
    fix="    char s[6];",
    fix_check=["s[6]", "s[7]", "s[8]", "s[10]", "s[16]", "s[20]", "s[32]", "s[50]", "s[64]", "s[100]", "s[]"],
    fix_forbid=["s[5]"],
    fix_rationale="数组至少要比字符个数多 1，才能放下结尾的 '\\0'。",
    variant={
        "prompt": "输入一个不含空格的单词（长度不超过 99），不使用 strlen，输出它的长度。",
        "tests": [{"in": "hello\n", "out": "5"}, {"in": "a\n", "out": "1"}, {"in": "xidian2026\n", "out": "10"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    char s[100];
    int len = 0;
    scanf("%99s", s);
    while (s[len] != '\0') len++;
    printf("%d\n", len);
    return 0;
}
""",
        "check_tokens": ["\\0", "'\\0'", "!=0", "!= 0"],
    },
    irt={"a": 1.2, "d": 0.3},
)

item(
    id="str-02", kp="c07", title="比较两个单词", task="读入两个单词，相同输出 same，否则输出 different。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    char a[20], b[20];
    scanf("%s %s", a, b);
    if (a == b) printf("same\n");
    else printf("different\n");
    return 0;
}
""",
    bug_lines=[6], bug_type="用 == 比较字符串",
    cause="a 和 b 是两个不同数组的首地址，a == b 比较的是地址而不是内容，结果永远为假；比较内容要用 strcmp。",
    trigger="输入两个相同的单词（例如 abc abc）时，本该输出 same，却输出 different。",
    explain_keywords={"cause": ["地址", "strcmp", "首地址", "指针"], "trigger": ["相同", "一样", "相等", "abc abc", "两个单词"]},
    fix='    if (strcmp(a, b) == 0) printf("same\\n");  /* 需要 #include <string.h> */',
    fix_check=["strcmp"], fix_forbid=["a==b"],
    fix_rationale="strcmp 逐个比较两个字符串的字符，内容相同时返回 0。",
    variant={
        "prompt": "输入一个单词和一个字母（用空格隔开），输出这个字母在单词中出现的次数。",
        "tests": [{"in": "banana a\n", "out": "3"}, {"in": "hello z\n", "out": "0"}, {"in": "aaa a\n", "out": "3"}],
        "reference": r"""
#include <stdio.h>
int main(void) {
    char s[100], c;
    int cnt = 0;
    scanf("%99s %c", s, &c);
    for (int i = 0; s[i] != '\0'; i++) if (s[i] == c) cnt++;
    printf("%d\n", cnt);
    return 0;
}
""",
        "check_tokens": ["\\0", "==c", "== c"],
    },
    irt={"a": 1.3, "d": 0.0},
)

item(
    id="str-03", kp="c07", title="字符串逆序", task="读入一个单词，输出它的逆序。",
    has_bug=True,
    code=r"""
#include <stdio.h>
#include <string.h>

int main(void) {
    char s[100], t[100];
    scanf("%s", s);
    int len = strlen(s);
    for (int i = 0; i < len; i++) {
        t[i] = s[len - i];
    }
    t[len] = '\0';
    printf("%s\n", t);
    return 0;
}
""",
    bug_lines=[9], bug_type="下标差一（取到了结尾的 \\0）",
    cause="i=0 时 s[len - i] 就是 s[len]，也就是结尾的 '\\0'，它被复制到 t[0]，所以 t 一开头就结束了。",
    trigger="输入任何非空单词都会输出空行（什么都不显示）。",
    explain_keywords={"cause": ["len-i", "len - i", "\\0", "s[len]", "下标", "-1", "差一"], "trigger": ["i=0", "空", "什么都不", "任何", "不输出"]},
    fix="        t[i] = s[len - 1 - i];", fix_check=["len-1-i", "len-i-1"], fix_forbid=["s[len-i]"],
    fix_rationale="最后一个字符的下标是 len-1，所以第 i 个位置应取 s[len-1-i]。",
    variant={
        "prompt": "输入一个单词，判断它是不是回文（正着读和倒着读一样），是输出 yes，否则输出 no。",
        "tests": [{"in": "level\n", "out": "yes"}, {"in": "abc\n", "out": "no"}, {"in": "a\n", "out": "yes"}],
        "reference": r"""
#include <stdio.h>
#include <string.h>
int main(void) {
    char s[100];
    scanf("%99s", s);
    int len = strlen(s), ok = 1;
    for (int i = 0; i < len / 2; i++) if (s[i] != s[len - 1 - i]) ok = 0;
    printf("%s\n", ok ? "yes" : "no");
    return 0;
}
""",
        "check_tokens": ["len-1-i", "len - 1 - i", "len-i-1"],
    },
    irt={"a": 1.3, "d": 0.6},
)

item(
    id="str-04", kp="c07", title="sizeof 与 strlen", task="输出字符数组 s 占用的字节数和字符串 s 的长度。",
    has_bug=False,
    code=r"""
#include <stdio.h>
#include <string.h>

int main(void) {
    char s[] = "abc";
    printf("%zu %zu\n", sizeof(s), strlen(s));
    return 0;
}
""",
    explanation="sizeof(s) 是数组占用的字节数（含结尾的 '\\0'）为 4，strlen(s) 是字符个数为 3，输出 4 3，符合题意，这段代码没有错。",
    expected_io={"in": "", "out": "4 3"},
    irt={"a": 1.1, "d": 0.4},
)

# ---------------------------------------------------------------- c08 函数
item(
    id="func-01", kp="c08", title="交换两个数（函数）", task="调用 swap 交换 x 和 y，输出 2 1。",
    has_bug=True,
    code=r"""
#include <stdio.h>

void swap(int a, int b) {
    int t = a;
    a = b;
    b = t;
}

int main(void) {
    int x = 1, y = 2;
    swap(x, y);
    printf("%d %d\n", x, y);
    return 0;
}
""",
    bug_lines=[3, 11], bug_type="值传递无法修改实参",
    cause="C 语言函数参数是值传递，a、b 只是 x、y 的副本，交换副本不影响 main 里的 x 和 y。",
    trigger="任何一次调用都不会交换，输出仍是 1 2。",
    explain_keywords={"cause": ["值传递", "传值", "副本", "拷贝", "形参", "复制"], "trigger": ["任何", "调用", "都不", "不变", "1 2"]},
    fix="void swap(int *a, int *b) { int t = *a; *a = *b; *b = t; }   /* 调用：swap(&x, &y); */",
    fix_check=["*a", "&x"], fix_forbid=[],
    fix_rationale="传入 x、y 的地址，函数通过指针直接修改 main 里的变量。",
    variant={
        "prompt": "写函数 int max3(int a, int b, int c) 返回三个数中的最大值；main 读入三个整数并输出结果。",
        "tests": [{"in": "1 5 3\n", "out": "5"}, {"in": "-1 -5 -3\n", "out": "-1"}, {"in": "2 2 2\n", "out": "2"}],
        "reference": r"""
#include <stdio.h>
int max3(int a, int b, int c) {
    int m = a;
    if (b > m) m = b;
    if (c > m) m = c;
    return m;
}
int main(void) {
    int a, b, c;
    scanf("%d %d %d", &a, &b, &c);
    printf("%d\n", max3(a, b, c));
    return 0;
}
""",
        "check_tokens": ["return", "max3("],
    },
    irt={"a": 1.3, "d": 0.0},
)

item(
    id="func-02", kp="c08", title="求符号", task="读入整数 x，正数输出 1，负数输出 -1，零输出 0。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int sign(int x) {
    if (x > 0) return 1;
    else if (x < 0) return -1;
}

int main(void) {
    int x;
    scanf("%d", &x);
    printf("%d\n", sign(x));
    return 0;
}
""",
    bug_lines=[5, 6], bug_type="有的分支没有返回值",
    cause="x 等于 0 时两个条件都不成立，函数执行到结尾也没有 return，返回值是不确定的。",
    trigger="输入 0 时触发，输出可能是任意数（不一定是 0）。",
    explain_keywords={"cause": ["没有返回", "缺少return", "缺少 return", "返回值", "x==0", "x=0", "零", "等于0"],
                      "trigger": ["0", "零", "x=0", "输入0"]},
    fix="    return 0;   /* 在函数末尾补上 */", fix_check=["return 0"], fix_forbid=[],
    fix_rationale="所有执行路径都必须返回值，补上 x 为 0 时的返回。",
    variant={
        "prompt": "写函数 int myabs(int x) 返回 x 的绝对值；main 读入一个整数并输出其绝对值。",
        "tests": [{"in": "-7\n", "out": "7"}, {"in": "0\n", "out": "0"}, {"in": "12\n", "out": "12"}],
        "reference": r"""
#include <stdio.h>
int myabs(int x) { return x < 0 ? -x : x; }
int main(void) {
    int x;
    scanf("%d", &x);
    printf("%d\n", myabs(x));
    return 0;
}
""",
        "check_tokens": ["return", "myabs("],
    },
    irt={"a": 1.2, "d": 0.4},
)

item(
    id="func-03", kp="c08", title="递归求阶乘", task="用递归函数计算 5!，输出 120。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int fact(int n) {
    return n * fact(n - 1);
}

int main(void) {
    printf("%d\n", fact(5));
    return 0;
}
""",
    bug_lines=[3, 4], bug_type="递归缺少终止条件",
    cause="递归没有结束条件，fact 会一直调用 fact(n-1)，n 变成 0、负数后仍不停止。",
    trigger="任何一次调用都会无限递归，最终栈溢出、程序崩溃。",
    explain_keywords={"cause": ["终止条件", "结束条件", "出口", "基准", "无限递归", "n==0", "n==1", "n<=1", "停止"],
                      "trigger": ["任何", "调用", "栈溢出", "崩溃", "一直", "都会"]},
    fix="    if (n <= 1) return 1;   /* 加在 return n * fact(n - 1); 前面 */",
    fix_check=["n<=1", "n==0", "n==1", "n<1", "n<2"], fix_forbid=[],
    fix_rationale="n 小到 1（或 0）时直接返回 1，递归就有了出口。",
    variant={
        "prompt": "写递归函数 int sum(int n) 返回 1+2+…+n（n ≥ 1）；main 读入 n 并输出结果。",
        "tests": [{"in": "1\n", "out": "1"}, {"in": "4\n", "out": "10"}, {"in": "100\n", "out": "5050"}],
        "reference": r"""
#include <stdio.h>
int sum(int n) {
    if (n == 1) return 1;
    return n + sum(n - 1);
}
int main(void) {
    int n;
    scanf("%d", &n);
    printf("%d\n", sum(n));
    return 0;
}
""",
        "check_tokens": ["sum(n-1)", "sum(n - 1)"],
    },
    irt={"a": 1.3, "d": 0.2},
)

item(
    id="func-04", kp="c08", title="最大公约数", task="输出 12 和 18 的最大公约数 6。",
    has_bug=False,
    code=r"""
#include <stdio.h>

int gcd(int a, int b) {
    while (b != 0) {
        int r = a % b;
        a = b;
        b = r;
    }
    return a;
}

int main(void) {
    printf("%d\n", gcd(12, 18));
    return 0;
}
""",
    explanation="辗转相除法：每次用余数替换，b 为 0 时 a 就是最大公约数；12 和 18 输出 6，这段代码没有错。",
    expected_io={"in": "", "out": "6"},
    irt={"a": 1.0, "d": 0.0},
)

# ---------------------------------------------------------------- c09 指针
item(
    id="ptr-01", kp="c09", title="通过指针赋值", task="让 p 指向一个整数，把它设为 10 并输出。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int main(void) {
    int *p;
    *p = 10;
    printf("%d\n", *p);
    return 0;
}
""",
    bug_lines=[4, 5], bug_type="未初始化的指针（野指针）",
    cause="p 没有初始化，不知道指向哪里，*p = 10 是往一个随机地址写数据。",
    trigger="执行到 *p = 10 就会出问题：可能崩溃，也可能悄悄改坏别的数据，结果不确定。",
    explain_keywords={"cause": ["没有初始化", "未初始化", "野指针", "没有指向", "随机地址", "不确定", "初始化"],
                      "trigger": ["执行", "运行", "崩溃", "任何", "都会", "*p=10", "*p = 10"]},
    fix="    int x; int *p = &x;", fix_check=["&x", "malloc"], fix_forbid=[],
    fix_rationale="先让 p 指向一个真实存在的变量（或用 malloc 申请的内存），再通过 *p 访问。",
    variant={
        "prompt": "写函数 void triple(int *p)，把 p 指向的整数变成原来的 3 倍；main 读入整数 x，调用后输出 x。",
        "tests": [{"in": "4\n", "out": "12"}, {"in": "-2\n", "out": "-6"}, {"in": "0\n", "out": "0"}],
        "reference": r"""
#include <stdio.h>
void triple(int *p) { *p = *p * 3; }
int main(void) {
    int x;
    scanf("%d", &x);
    triple(&x);
    printf("%d\n", x);
    return 0;
}
""",
        "check_tokens": ["*p", "&x"],
    },
    irt={"a": 1.3, "d": 0.2},
)

item(
    id="ptr-02", kp="c09", title="函数返回数组", task="通过函数得到数组 {1,2,3} 并输出。",
    has_bug=True,
    code=r"""
#include <stdio.h>

int *make(void) {
    int a[3] = {1, 2, 3};
    return a;
}

int main(void) {
    int *p = make();
    printf("%d %d %d\n", p[0], p[1], p[2]);
    return 0;
}
""",
    bug_lines=[4, 5], bug_type="返回局部变量的地址",
    cause="a 是函数里的局部数组，make 返回后它的存储空间就失效了，p 指向一块已经无效的内存。",
    trigger="函数返回之后再通过 p 访问时触发；后续再调用别的函数（比如 printf）可能覆盖这块内存，输出变成乱码。",
    explain_keywords={"cause": ["局部", "生命周期", "释放", "销毁", "栈", "失效", "函数结束"],
                      "trigger": ["返回后", "返回之后", "函数结束", "访问", "printf", "调用"]},
    fix="    static int a[3] = {1, 2, 3};", fix_check=["static", "malloc"], fix_forbid=[],
    fix_rationale="static 数组在整个程序运行期间都存在（或改用 malloc，或由调用者传入数组）。",
    variant={
        "prompt": "写函数 void fill(int *a, int n)，把数组前 n 个元素依次赋值为 1, 2, …, n；main 读入 n（1 ≤ n ≤ 100），调用后用空格隔开输出数组。",
        "tests": [{"in": "3\n", "out": "1 2 3"}, {"in": "1\n", "out": "1"}, {"in": "5\n", "out": "1 2 3 4 5"}],
        "reference": r"""
#include <stdio.h>
void fill(int *a, int n) { for (int i = 0; i < n; i++) a[i] = i + 1; }
int main(void) {
    int n, a[100];
    scanf("%d", &n);
    fill(a, n);
    for (int i = 0; i < n; i++) printf("%d ", a[i]);
    printf("\n");
    return 0;
}
""",
        "check_tokens": ["fill("],
    },
    irt={"a": 1.4, "d": 0.8},
)

item(
    id="ptr-03", kp="c09", title="交换两个数（指针版）", task="调用 swap(&x, &y) 交换 x 和 y，输出 2 1。",
    has_bug=True,
    code=r"""
#include <stdio.h>

void swap(int *a, int *b) {
    int *t = a;
    a = b;
    b = t;
}

int main(void) {
    int x = 1, y = 2;
    swap(&x, &y);
    printf("%d %d\n", x, y);
    return 0;
}
""",
    bug_lines=[4, 5, 6], bug_type="交换了指针本身而不是指向的值",
    cause="函数里交换的是指针变量 a、b 本身（它们也是副本），没有通过 *a、*b 修改 x 和 y。",
    trigger="任何一次调用，x 和 y 都不变，输出仍是 1 2。",
    explain_keywords={"cause": ["指针本身", "交换的是指针", "*a", "解引用", "副本", "地址"], "trigger": ["任何", "调用", "不变", "都不", "1 2"]},
    fix="    int t = *a; *a = *b; *b = t;", fix_check=["*a=*b", "*a = *b"], fix_forbid=["int*t=a"],
    fix_rationale="通过 *a、*b 交换两个地址里存的值，main 中的 x、y 才会改变。",
    variant={
        "prompt": "写函数 void sort2(int *a, int *b)，使调用后 *a ≤ *b；main 读入两个整数，调用后按顺序输出（用空格隔开）。",
        "tests": [{"in": "5 3\n", "out": "3 5"}, {"in": "1 2\n", "out": "1 2"}, {"in": "-1 -4\n", "out": "-4 -1"}],
        "reference": r"""
#include <stdio.h>
void sort2(int *a, int *b) { if (*a > *b) { int t = *a; *a = *b; *b = t; } }
int main(void) {
    int x, y;
    scanf("%d %d", &x, &y);
    sort2(&x, &y);
    printf("%d %d\n", x, y);
    return 0;
}
""",
        "check_tokens": ["*a", "&x"],
    },
    irt={"a": 1.3, "d": 0.5},
)

item(
    id="ptr-04", kp="c09", title="用指针遍历数组", task="用指针遍历数组 {1,2,3,4,5}，输出元素之和 15。",
    has_bug=False,
    code=r"""
#include <stdio.h>

int main(void) {
    int a[5] = {1, 2, 3, 4, 5};
    int sum = 0;
    for (int *p = a; p < a + 5; p++) {
        sum += *p;
    }
    printf("%d\n", sum);
    return 0;
}
""",
    explanation="p 从 a（即 &a[0]）开始，每次加 1 指向下一个元素，到 a+5（最后一个元素的后一个位置）停止，这段代码没有错。",
    expected_io={"in": "", "out": "15"},
    irt={"a": 1.1, "d": 0.3},
)


def main():
    out = Path(__file__).resolve().parent.parent / "app" / "item_bank.json"
    out.write_text(json.dumps({"version": 1, "items": ITEMS}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(ITEMS)} items -> {out}")


if __name__ == "__main__":
    main()
