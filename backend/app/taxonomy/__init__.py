"""固定的表和枚举。用于统计的字段只能取这里定义的编码，大模型也只能从里面选。

编码只增不改不删；要合并时旧编码标“停用”并指向新编码。
"""
from .enums import *  # noqa: F401,F403
from .errors import ERRORS, error_name, errors_for_course, is_error_code  # noqa: F401
from .knowledge import (COURSE_NAMES, KNOWLEDGE_POINTS, TOPICS, course_of, course_prompt, kp_name,  # noqa: F401
                        kp_of_skill, kp_rank)
from .skills import SKILLS, is_skill_code, skill_name, skills_for_course, skills_of_kp  # noqa: F401
