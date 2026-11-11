"""测试统一使用模拟模式和临时数据目录（必须在导入 app 之前设置）。"""
import os
import tempfile

os.environ["MOCK_LLM"] = "1"
os.environ["ENABLE_CODE_RUNNER"] = "0"
os.environ["TEACHER_TOKEN"] = ""
os.environ["CTUTOR_DATA_DIR"] = tempfile.mkdtemp(prefix="ctutor_test_")
