# 给在这个仓库里工作的 Claude

程序设计智能体：只读同步学校 XDOJ（C 语言：`http://47.92.139.74/xdoj-ssm`）的练习数据，给学生答疑、出小题、安排复习，给老师看学情。
详细说明在 `README.md`、`docs/技术方案.md`、`docs/问题清单.md`、`docs/怎么验证.md`。

## 运行和测试

- 启动、停止：`./run.sh`、`./run.sh stop`、`./run.sh log`、`./run.sh update`（后台运行，网页和同步在同一个进程里）。
- 诊断同步：`cd backend && .venv/bin/python -m app.cli doctor`（只读本地库）。
- 核对真实接口：`.venv/bin/python -m app.cli xdoj-probe`（`.env` 里要有 `XDOJ_USER`、`XDOJ_PASS`）。
- 测试：`cd backend && .venv/bin/python -m pytest -q`，提交前全部要过。测试用的是 `app/xdoj/fake.py` 里的假 XDOJ；改了解析真实接口的代码，要让假 XDOJ 也能返回同样形状的数据，并加测试。
- 改了前端：在有 Node 的机器上 `cd frontend && npm run build`，把 `frontend/dist` 一起提交（服务器上不装 Node）。

## 必须遵守

- 对 XDOJ **只读**：只请求 `backend/app/xdoj/client.py` 里白名单上的接口；所有请求串行，间隔不低于 0.2 秒。
- 学生的密码、token、登录 IP **不进库、不进日志、不打印**。学生登录时把密码转发给 XDOJ 核对一次，不保存。
- 学生端不能看到：测试点的输入输出、概率数字、技能编码、工作流步骤、跨课程内容。
- 发给大模型的内容不带学号和姓名。
- 能用程序确定的判断（评测结果、编译诊断、小题判分）不交给大模型。
- 页面上不显示同步细节和调试信息，出错时才提示。
- 题库是同一台服务器上所有老师共用的：按服务器同步一次，不要按老师重复下载。

## 已经实测确认的接口细节

- 班级的 `classId` 可能是文本或带前导零（如 `000031`）：名单接口按 `classId` 原值筛选（`filters` 里 `field=class_id`），名单按 `user_id` 排序。
- 登录返回 `userType`，1 是教师。C 语言和数据结构是两台 XDOJ，同一位老师的账号密码可能不同：一位老师在本系统里一个账号，关联多个 XDOJ 账号（`teacher_sources`）。教师姓名在 `GET /admin/dashboard.do` 页面的 `.user-info` 里第一个 `<small>`。
- 提交列表里的 `language` 是对象，取 `name`；评测记录的文字结果在 `judgementText`；“未评判”表示还在评测。

## 还没确认的

- 测试点运行结果 `result` 的数字（1、2……）和评测记录 `judgement` 的数字各代表什么。确认后写进 `backend/app/taxonomy/enums.py`，现在只能在整条提交通过时推断各组都通过。
- 作业列表里“布置给哪些班”的真实写法（现在兼容数字 id、文本 classId、对象数组三种）。

可以直接请求真实的 XDOJ 来核对，但要遵守上面的只读和间隔要求，输出里遮掉学号、姓名，不要打印任何密码。
