"""全局配置。所有可调参数集中在这里，方便以后用真实数据标定。

环境变量（也可以写在项目根目录的 .env 文件里）：
    DEEPSEEK_API_KEY      DeepSeek 的 API Key；不填则自动进入“模拟模式”，不调用大模型
    DEEPSEEK_BASE_URL     默认 https://api.deepseek.com
    DEEPSEEK_MODEL        默认 deepseek-chat（分析、判分、辅导都用它）
    MOCK_LLM              设为 1 强制使用模拟模式（离线测试用）
    ENABLE_CODE_RUNNER    设为 1 时，变式题会在本机用 gcc 编译运行测试用例（仅限 Linux，仅供本机测试）
    TEACHER_TOKEN         教师页和数据导出的口令；不设置时只允许本机访问
    CTUTOR_DATA_DIR       数据库存放目录，默认项目下的 data/
    HOST / PORT           服务监听地址，默认 127.0.0.1:5000
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """读取 .env（只支持 KEY=VALUE 每行一个），不覆盖已经存在的环境变量。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("CTUTOR_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "ctutor.sqlite3"
ITEM_BANK_PATH = BASE_DIR / "app" / "item_bank.json"
STATIC_DIR = BASE_DIR / "static"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MOCK_LLM = os.environ.get("MOCK_LLM", "0") == "1" or not DEEPSEEK_API_KEY
ENABLE_CODE_RUNNER = os.environ.get("ENABLE_CODE_RUNNER", "0") == "1"
TEACHER_TOKEN = os.environ.get("TEACHER_TOKEN", "")
SHOW_DEBUG = os.environ.get("SHOW_DEBUG", "1") == "1"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))

# 五个表现维度（诊断题的五步，按顺序逐步作答，前一步没通过就不再往下问）
STEP_NAMES = ["判断有错", "定位问题", "解释原因与触发条件", "修复并说明", "独立完成变式题"]
STEP_SUPPORTS = ["能察觉异常", "能定位问题", "理解错误涉及的原理", "能完成局部纠错", "提供更强的迁移证据"]

# 学情模型参数（初始值是经验设定，需要用收集到的数据重新标定）
MODEL = {
    # 能力 θ 的离散网格
    "grid_min": -3.0,
    "grid_max": 3.0,
    "grid_step": 0.1,
    # 每一步相对题目难度 d 的偏移：判断、定位、解释、修复、变式
    "step_offsets": [-1.5, -0.8, 0.0, 0.5, 1.2],
    # 无错题（只考“判断有没有错”）的偏移和猜对概率
    "clean_offset": -0.3,
    "default_a": 1.2,
    # “独立掌握”的分界：中等难度题能完成“修复并说明”这一步
    "theta_cut": 0.5,
    # 总体先验
    "pop_mean": 0.0,
    "pop_sd": 1.0,
    # 先验：距上次诊断每过一天，方差增加 q（时间越久越不确定）
    "drift_per_day": 0.02,
    # 先验：同一知识点每重复问一次，均值下调 ρ（最多计 3 次）
    "reask_penalty": 0.2,
    "reask_cap": 3,
    # 先验：本轮对话投入指数 e∈[0,1] 的权重 β（投入≠熟练，所以权重小）
    "engagement_weight": 0.6,
    "prior_sd_min": 0.5,
    "prior_sd_max": 1.2,
    # 线上使用哪种先验：flat / history / engagement / both
    "prior_variant": "both",
    # 自适应出题与停止规则
    "min_items": 1,
    "max_items": 4,
    "sd_target": 0.40,
    "confidence": 0.90,
    # evr：选“期望方差下降最大”的题；evr_per_step：再除以预计作答步数（省时间）
    "selection": "evr",
    # 历史检索：两次提问相似度达到该值算“重复提问”
    "similarity_threshold": 0.5,
    "similarity_candidate": 0.3,
}

# 本轮对话投入指数 e 的权重：ICAP 平均分、给出自己尝试的比例、不是索要答案的比例
ENGAGEMENT_WEIGHTS = {"icap": 0.5, "attempt": 0.3, "not_answer_seeking": 0.2}
ICAP_SCORE = {"passive": 0, "active": 1, "constructive": 2, "interactive": 3}
