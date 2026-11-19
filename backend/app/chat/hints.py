"""固定模板用到的提示语：每种常见错误的反问（H0）、位置（H1）、原理（H2）。

用在两处：没有配置大模型时；大模型写的回复两次都没通过校验时。
模板里可以用 {var}（出问题那一行的变量）、{init}（它的初值）、{line}、{situation}（哪类情况）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Hint:
    ask: str = ""        # H0：反问一次
    where: str = ""      # H1：大致位置或哪类情况
    why: str = ""        # H2：原理和触发条件
    example: str = ""    # 乙级：自己构造的简单例子；有 so 时例子代替 why
    so: str = ""         # 结论，接在原理或例子后面
    keys: tuple[str, ...] = ()  # 规则兜底：回答里出现这些，算答到点上了


HINTS: dict[str, Hint] = {
    "TY09": Hint(
        ask="`{var}` 在第 {line} 行用到的时候，它的值是多少？是在哪一行给它的？",
        where="问题在 `{var}` 的初值上，看第 {line} 行。",
        why="`{var}` 还没有被赋过合适的值就参加了运算，结果就不可靠。用之前要给它一个确定的、合理的初值。",
        keys=(r"没.*(赋|初始|初值)", r"随机", r"不确定")),
    "TY01": Hint(
        ask="第 {line} 行里，除号两边各是什么类型？算出来的结果会带小数吗？",
        where="问题在第 {line} 行的除法。",
        why="两个整数相除，结果还是整数，小数部分直接丢掉。要先把其中一个转成 double 再除。",
        example="比如 `7 / 2` 得到 3，不是 3.5。",
        keys=(r"整数|整型|int", r"小数.*(丢|没|掉)", r"截|取整")),
    "TY02": Hint(
        ask="{situation_when}第 {line} 行算出来的数会有多大？`int` 放得下吗？",
        where="问题在数的大小：{situation_when}结果超出了 `int` 能表示的范围。",
        why="`int` 最大大约是 21 亿。结果超过这个范围就会溢出，变成负数或一个不相干的数。要改用 `long long`，输出用 `%lld`。",
        example="比如 13 的阶乘是 6227020800，已经超过了 `int` 的范围。",
        keys=(r"溢出|超出|超过|放不下|太大|范围", r"long\s*long")),
    "IO02": Hint(
        ask="第 {line} 行的格式说明符，和后面那个变量的类型对得上吗？",
        where="问题在第 {line} 行的格式说明符。",
        why="`%d` 对应 `int`，`%lld` 对应 `long long`，`%f` 对应小数。对不上的话，输出可能是乱数。",
        keys=(r"%l+d|%lf|%f", r"类型|对不上|不匹配|不一致")),
    "IO04": Hint(
        ask="把你的输出和题目要求的格式逐个字符对一下：空格、换行的位置都一样吗？",
        where="数值是对的，问题在输出格式：空格或换行和题目要求的不一样。",
        why="数值是对的，但评测是逐个字符比较的，多一个空格、少一个换行都算错。对照题面的输出格式改。",
        keys=(r"空格|换行|格式",)),
    "IO05": Hint(
        ask="你的程序除了题目要的结果，还输出了别的东西吗？",
        where="输出里多了题目没要求的内容，比如提示语或调试时加的输出。",
        why="评测只认题目要求的输出。`printf` 里的提示文字、调试时加的输出都要去掉。",
        keys=(r"多.*输出|提示|调试",)),
    "IO06": Hint(
        ask="题目要求保留几位小数？你输出了几位？",
        where="数值是对的，小数位数和题目要求的不一样。",
        why="保留几位小数用 `%.2f` 这样的写法控制，点后面的数字就是位数。",
        keys=(r"位数|几位|小数",)),
    "IO10": Hint(
        ask="题目说有多组数据。你的程序读完第一组之后，还会接着读吗？",
        where="问题在读入：只处理了第一组数据。",
        why="多组数据要用循环一直读到输入结束，常见写法是 `while (scanf(...) != EOF)`。",
        keys=(r"多组|循环|只.*一组|EOF",)),
    "BR01": Hint(
        ask="第 {line} 行的条件里，用的是一个等号还是两个？它们的意思一样吗？",
        where="问题在第 {line} 行的条件里的等号。",
        why="`=` 是赋值，`==` 才是判断相等。条件里写成 `=`，会先赋值，再拿赋的值当条件。",
        keys=(r"==|两个等号|赋值",)),
    "BR03": Hint(
        ask="把第 {line} 行的条件念成一句话：这几个条件是要同时成立，还是成立一个就行？",
        where="问题在第 {line} 行，几个条件之间的连接方式。",
        why="`&&` 表示都要成立，`||` 表示成立一个就行。用反了，条件的范围就变了。",
        keys=(r"&&|\|\||同时|并且|或者",)),
    "BR04": Hint(
        ask="{situation_when}第 {line} 行的条件成立还是不成立？题目要求的是哪一种？",
        where="问题在第 {line} 行条件的边界：正好等于端点值的时候。",
        why="`<` 和 `<=` 只差在正好相等的那个值上。端点算不算在内，要对照题目的要求。",
        keys=(r"等于|<=|>=|端点|边界",)),
    "AL02": Hint(
        ask="{situation_when}你的程序每一步会怎么走？",
        where="一般的数据是对的，问题出在特殊的输入上：{situation_when}会出错。",
        why="{situation_when}循环可能一次都不执行，结果就停在初值上。这种边界情况要单独走一遍，必要时单独处理。",
        keys=(r"不执行|不进|初值|特殊|单独",)),
    "AL03": Hint(
        ask="{situation_when}你的程序会输出什么？题目要求的是什么？",
        where="一般的数据是对的，问题出在特殊的情况上：{situation_when}会出错。",
        why="{situation_when}程序走的还是一般情况的路。这种特殊情况要单独考虑。",
        keys=(r"特殊|单独|没考虑",)),
    "AR01": Hint(
        ask="第 {line} 行的下标最大会取到多少？数组能用的下标范围是多少？",
        where="问题在第 {line} 行的下标，它超出了数组的范围。",
        why="长度为 n 的数组，能用的下标是 0 到 n-1。访问到范围之外，程序可能崩溃，也可能得到乱数。",
        keys=(r"越界|超出|n-1|范围",)),
    "AR02": Hint(
        ask="题目里数据最多有多少个？你的数组开了多大？",
        where="问题在数组的大小：{situation_when}数组放不下。",
        why="数组要按题目给的最大数据量来开，再多留几位。开小了，数据一多就会写到数组外面，程序可能崩溃。",
        keys=(r"开小|太小|不够|放不下|越界|开大",)),
    "AR04": Hint(
        ask="你读数据时下标从几开始，用的时候又是从几开始？两处一样吗？",
        where="问题在下标的起点：读入和使用的地方不一致。",
        why="C 语言的数组下标从 0 开始。读入和使用如果一个从 0、一个从 1，就会整体错开一位。",
        keys=(r"从\s*0|从\s*1|错开|不一致|不一样",)),
    "LP01": Hint(
        ask="第 {line} 行的循环一共执行几次？第一次和最后一次，循环变量各是多少？",
        where="问题在第 {line} 行循环的起点或终点。",
        why="循环的起点或终点差一个，就会少处理或多处理一项。把第一次和最后一次的下标代进去核对。",
        keys=(r"少.*一|多.*一|次数|<=|边界",)),
    "LP02": Hint(
        ask="`{var}` 是在哪里清零的？第二轮开始的时候，它的值是多少？",
        where="问题在 `{var}` 清零的位置。",
        why="累加、计数用的变量，每次重新统计之前都要清零。初始化写在循环外面，就会把上一轮的结果带进来。",
        keys=(r"清零|初始化|上一(轮|组)|没.*归零",)),
    "LP03": Hint(
        ask="第 {line} 行的循环，什么时候会结束？循环变量在循环体里变了吗？",
        where="问题在第 {line} 行的循环，它停不下来。",
        why="循环条件一直成立就停不下来。检查循环变量有没有在每一轮更新，更新的方向对不对。",
        keys=(r"死循环|停不|没.*更新|一直",)),
    "CE01": Hint(
        ask="编译没有通过。看第 {line} 行和它的上一行：每条语句都结束了吗？括号都配对了吗？",
        where="编译没有通过：第 {line} 行附近少了分号，或者括号、花括号没有配对。",
        why="每条语句要用分号结束，括号和花括号要成对。编译器报的行号常常是出错位置的下一行，往上一行找。",
        keys=(r"分号|括号",)),
    "CE02": Hint(
        ask="编译没有通过。第 {line} 行用到的名字，在前面声明过吗？拼写一样吗？",
        where="编译没有通过：第 {line} 行用了一个没有声明的名字，可能是拼错了，或者忘了定义。",
        why="变量要先声明再使用，名字的大小写和拼写要完全一致。",
        keys=(r"声明|定义|拼",)),
    "CE04": Hint(
        ask="编译没有通过。第 {line} 行调用的函数，名字写对了吗？对应的头文件包含了吗？",
        where="编译没有通过：第 {line} 行调用的函数没有声明，可能是函数名拼错了，或者少了头文件。",
        why="库函数要先包含它的头文件，比如 `sqrt` 在 `math.h` 里。函数名拼错也会报同样的错。",
        keys=(r"头文件|include|拼",)),
    "CE05": Hint(
        ask="编译没有通过。看第 {line} 行的标点：分号、括号、引号是英文的吗？",
        where="编译没有通过：第 {line} 行有中文标点。",
        why="C 语言里的分号、逗号、括号、引号都必须是英文的。中文输入法下打出来的不行。",
        keys=(r"中文|标点|全角",)),
}

GENERIC = Hint(
    ask="先看这几行。{situation_when}每一步变量的值和你想的一样吗？",
    where="问题在第 {line} 行附近。{situation_sentence}",
    why="这里的问题是“{error_name}”。对照第 {line} 行，把出错的那类情况代进去走一遍。",
)
GENERIC_NO_LINE = Hint(
    ask="没通过的那一组，数据可能比较特殊。{situation_when}你的程序会怎么处理？",
    where="{situation_sentence}想想这种情况下，程序的每一步会怎么走。",
    why="这里的问题是“{error_name}”。{situation_sentence}",
)


def extreme_hint(var: str, init: str, direction: str, arr: str = "", cmp_line: int | None = None) -> Hint:
    """最值的初值设定：`max = 0`、`min = 0` 这类写法。direction：max 或 min。"""
    cmp_word = "小" if direction == "max" else "大"
    never = f"第 {cmp_line} 行一次都不会替换" if cmp_line else "后面的比较一次都不会成立"
    first = f"第一个数 `{arr}[0]`" if arr else "第一个数"
    example = ""
    if init.strip() == "0":
        example = (f"假如输入是 `-5 -2 -8`，每个数都比 0 小，{never}，最后输出 0。" if direction == "max"
                   else f"假如输入是 `5 2 8`，没有哪个数比 0 小，{never}，最后输出 0。")
    return Hint(
        ask="如果{situation_or_all}，`{var}` 一开始等于 {init}，会发生什么？",
        where="问题在第 {line} 行，`{var}` 的初值。{situation_sentence}",
        why=f"如果每个数都比 {init} {cmp_word}，{never}，`{var}` 就一直是 {init}。",
        example=example,
        so=f"所以初值要用一个真实存在的数，比如{first}。",
        keys=(r"不会.*(替换|更新|变|成立)", r"(还是|一直是|输出|得到|结果是)\s*`?" + re.escape(init.strip()),
              r"都比.*" + cmp_word, r"第一个|\[0\]"),
    )


def hint_for(error_code: str, has_line: bool) -> Hint:
    hint = HINTS.get(error_code)
    if hint is not None and (has_line or "{line}" not in hint.ask + hint.where + hint.why):
        return hint
    return GENERIC if has_line else GENERIC_NO_LINE


def answered(text: str, hint: Hint) -> bool | None:
    """规则兜底：学生对反问的回答有没有答到点上。判不了返回 None。"""
    if not hint.keys:
        return None
    if re.search(r"没关系|没问题|不影响|没事|无所谓|应该可以|不会有问题", text):
        return False
    return any(re.search(k, text) for k in hint.keys)


# 没有大模型时，只能讲这几个最常被问到的概念
CONCEPT_NOTES: list[tuple[str, str]] = [
    (r"%l?d.*%l+d|%d.*%ld|%ld|%lld", "`%d` 对应 `int`，`%ld` 对应 `long`，`%lld` 对应 `long long`。对不上的话，输出可能是乱数。"),
    (r"=\s*和\s*==|==.*区别|赋值.*相等", "`=` 是赋值，把右边的值放进左边的变量；`==` 是判断两边是否相等。条件里要用 `==`。"),
    (r"(?<![\w.])\d+\s*/\s*\d+(?![\w.]).*(?:double|float|浮点)", "C 先计算右边，再赋值。比如 `double x = 5 / 2;` 先做整数除法得到 2，再把 2 转成 2.0 存入 x。写成 `5.0 / 2` 或 `(double)5 / 2` 才得到 2.5。"),
    (r"整数除法|整除|/.*小数", "两个整数相除，结果还是整数，小数部分直接丢掉，比如 `7 / 2` 是 3。要得到小数，先把其中一个转成 double。"),
    (r"下标|从\s*0\s*开始", "数组下标从 0 开始。长度为 n 的数组，能用的下标是 0 到 n-1。"),
    (r"scanf.*&|取地址|为什么.*&", "`scanf` 要把读到的值放进变量里，所以要给它变量的地址，写成 `&a`。数组名本身就是地址，不用再加 `&`。"),
    (r"&&.*\|\||与.*或|逻辑运算", "`&&` 表示两边都成立才算成立，`||` 表示有一边成立就算成立。"),
    (r"long long|溢出|int.*范围", "`int` 能表示的范围大约是正负 21 亿。超过这个范围要用 `long long`，输入输出用 `%lld`。"),
    (r"break.*continue|continue.*break", "`break` 结束整个循环；`continue` 只跳过这一轮剩下的部分，接着做下一轮。"),
    (r"'\\0'|结束符|字符串.*结尾", "C 语言的字符串以 `'\\0'` 结尾。字符数组要多留一个位置放它，否则输出时会带出乱码。"),
]
