"""C 语言知识点表。prereq 是先修关系，以后可以用来做课程结构画像。"""

KNOWLEDGE_POINTS = [
    {"id": "c01", "name": "变量与数据类型", "prereq": [],
     "keywords": ["int", "float", "double", "char", "类型", "变量", "溢出", "强制转换", "类型转换", "long"]},
    {"id": "c02", "name": "运算符与表达式", "prereq": ["c01"],
     "keywords": ["运算符", "优先级", "取余", "%", "整除", "自增", "++", "==", "逻辑运算", "表达式"]},
    {"id": "c03", "name": "输入输出", "prereq": ["c01"],
     "keywords": ["scanf", "printf", "输入", "输出", "%d", "%lf", "%f", "%c", "格式", "getchar", "gets", "fgets"]},
    {"id": "c04", "name": "分支结构", "prereq": ["c02"],
     "keywords": ["if", "else", "switch", "case", "break", "分支", "判断", "条件"]},
    {"id": "c05", "name": "循环结构", "prereq": ["c04"],
     "keywords": ["for", "while", "do", "循环", "死循环", "累加", "阶乘", "continue", "循环条件"]},
    {"id": "c06", "name": "数组", "prereq": ["c05"],
     "keywords": ["数组", "下标", "越界", "a[", "arr", "排序", "冒泡", "最大值", "最小值", "二维"]},
    {"id": "c07", "name": "字符串", "prereq": ["c06"],
     "keywords": ["字符串", "strlen", "strcpy", "strcmp", "\\0", "字符数组", "回文", "单词", "string.h"]},
    {"id": "c08", "name": "函数", "prereq": ["c05"],
     "keywords": ["函数", "返回值", "return", "参数", "形参", "实参", "递归", "调用", "声明", "void"]},
    {"id": "c09", "name": "指针", "prereq": ["c06", "c08"],
     "keywords": ["指针", "*p", "地址", "&", "解引用", "野指针", "malloc", "free", "int *", "->"]},
    {"id": "c10", "name": "结构体", "prereq": ["c09"],
     "keywords": ["struct", "结构体", "成员", "typedef", "."]},
]

KP_BY_ID = {kp["id"]: kp for kp in KNOWLEDGE_POINTS}
OTHER_KP = {"id": "other", "name": "其他/与C语言无关", "prereq": [], "keywords": []}


def kp_name(kp_id: str) -> str:
    return KP_BY_ID.get(kp_id, OTHER_KP)["name"]


def kp_list_text() -> str:
    return "\n".join(f"{kp['id']}：{kp['name']}" for kp in KNOWLEDGE_POINTS) + "\nother：其他/与C语言无关"


def guess_kp(text: str) -> str:
    """模拟模式下用关键词猜知识点；越靠后的知识点越具体，同分时取后面的。"""
    low = text.lower()
    best, best_score = "other", 0
    for kp in KNOWLEDGE_POINTS:
        score = sum(1 for k in kp["keywords"] if k.lower() in low)
        if score >= best_score and score > 0:
            best, best_score = kp["id"], score
    return best
