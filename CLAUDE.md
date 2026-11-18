# 给在这个仓库里工作的 Claude

程序设计智能体：只读同步学校 XDOJ（C 语言：`http://47.92.139.74/xdoj-ssm`）的练习数据，给学生答疑、出小题、安排复习，给老师看学情。
详细说明在 `README.md`、`docs/技术方案.md`、`docs/创新点与方法.md`、`docs/问题清单.md`、`docs/怎么验证.md`。

## 运行和测试

- 启动、停止：`./run.sh`、`./run.sh stop`、`./run.sh log`、`./run.sh update`（后台运行，网页和同步在同一个进程里）。
- 诊断同步：`cd backend && .venv/bin/python -m app.cli doctor`（只读本地库）。
- 核对真实接口：`.venv/bin/python -m app.cli xdoj-probe`（`.env` 里要有 `XDOJ_USER`、`XDOJ_PASS`）。会打印各列表接口的字段名和类型、名单按 `classId` / `id` 筛的人数、评测结果的文字和数字对应、测试点运行记录的字段、`getRunOutput` 的字段名（只打印长度，不打印内容）。
- 测试：`cd backend && .venv/bin/python -m pytest -q`，提交前全部要过。测试用的是 `app/xdoj/fake.py` 里的假 XDOJ；改了解析真实接口的代码，要让假 XDOJ 也能返回同样形状的数据，并加测试。
- 改了前端：在有 Node 的机器上 `cd frontend && npm run build`，把 `frontend/dist` 一起提交（服务器上不装 Node）。
- 改了表结构：写一个迁移（`backend/alembic/versions/`），并在 `app/db.py` 的 `_REVISION_MARKS` 里加上这一版的痕迹（新加的列或新表）。启动时 `db.migrate()` 自动升级；以前用 `create_all` 建的库也能认出停在哪一版。迁移里建表先看表在不在。

## 必须遵守

- 对 XDOJ **只读**：只请求 `backend/app/xdoj/client.py` 里白名单上的接口；所有请求串行，间隔不低于 0.2 秒。
- 学生的密码、token、登录 IP **不进库、不进日志、不打印**。学生登录时把密码转发给 XDOJ 核对一次，不保存。
- 学生端不能看到：测试点的输入输出、概率数字、技能编码、工作流步骤、跨课程内容。大模型可以看到没通过的测试点（用来分析），但回复里不能出现，`chat/validate.py` 会拦。
- 发给大模型的内容不带学号和姓名。
- 能用程序确定的判断（评测结果、编译诊断、小题判分）不交给大模型。大模型评“理解”时，结论要引用学生原话、和事实冲突时下调（`app/assess.py`），新加的大模型评估也要照这个做。
- 页面上不显示同步细节和调试信息，出错时才提示。
- **全部自动，不加手动按钮**：名单、作业、提交的同步，智能体评估、评语、跨课程联系都由后台自动做（`xdoj/daemon.py`、`automation.py`、`graphmodel.nightly`），页面自己定时刷新。新功能也不要做成“点一下才执行”。
- 题库是同一台服务器上所有老师共用的：按服务器同步一次，不要按老师重复下载。
- C 语言和数据结构**分开登录、分开显示**（登录凭证里带课程，`user.course`）。C 语言那边任何地方都不出现数据结构；数据结构那边可以看学生在 C 语言相关知识点上的掌握程度（只有档位和估计），不出现 C 语言的答题记录（提交、对话、卡点、小题）。新加的页面和接口都要按课程过滤。数据结构那台 XDOJ 的地址填在 `.env` 的 `XDOJ_DS_URL`。

## 已经实测确认的接口细节

- 班级的 `classId` 可能是文本或带前导零（如 `000031`）：名单接口按 `classId` 原值筛选（`filters` 里 `field=class_id`），名单按 `user_id` 排序。
- 登录返回 `userType`，1 是教师。C 语言和数据结构是两台 XDOJ，同一位老师的账号密码可能不同。教师姓名在 `GET /admin/dashboard.do` 页面的 `.user-info` 里第一个 `<small>`。
- 提交列表里的 `language` 是对象，取 `name`；评测记录的文字结果在 `judgementText`；“未评判”表示还在评测。
- **请求参数照网页里 jqGrid 实际发的写，不要省略或改写**（名单那次把文本 classId 改成数字就取不到人）：顶层表格（班级、名单、作业、题目、提交）是 POST，搜索开关叫 `search`；展开的子表（`/contest/problems.do`、`/testcase/list.do`、`/submission/listJudge.do`）是 GET，带 `_search`、`page`、`rows`、`sidx`、`sord`、`nd`（`endpoints.grid_json`，被拒绝时换写法并记住）。假 XDOJ 对子表同样严格，改请求时测试会拦住。
- 某个接口回 400 错误页、回的不是 JSON，**不是掉线**：只有被转到登录页才重新登录。接口错误只让当前任务失败、退避重试（`ResponseError`、`ClientError`），不能抛 `SessionError` 让整个同步停下来。
- 一样取不到不能拖累另一样：题面和测试点互不依赖；源码最先取，不依赖评测记录；每组测试点、每组程序输出单独取。
- 题面是 PDF（`viewTextFile.do`），都带文字层。**禁止 OCR。** 做法（`app/pdftext.py`）：文字层用 pdfplumber、PDFium 两种办法抽，挑乱码少的，编码错位程序还原；示意图从页面上裁成 PNG、文字里写 `[图N]`；再交给大模型整理排版（标注这道题之前、空闲时，每个文件一次），程序核对数字、`[图N]`、汉字来源，不合格就用程序抽的文字。乱码不给学生和大模型看，学生端一直有“看原题”（原 PDF）。读题面文字一律用 `pdftext.usable_text`，发给大模型前过 `pdftext.for_llm`。
- 诊断：`cli doctor` 列出最近 24 小时每个接口成功几次、失败的状态码，题面、测试点、源码各有多少；`cli xdoj-probe` 用同步程序自己的函数把题面、测试点、源码、评测记录、程序输出各取一遍，只打印成没成功和长度。

## 还没确认的

- 测试点运行结果 `result` 的数字（1、2……）和评测记录 `judgement` 的数字各代表什么。确认后写进 `backend/app/taxonomy/enums.py`，现在只能在整条提交通过时推断各组都通过。
- 作业列表里“布置给哪些班”的真实写法（现在兼容数字 id、文本 classId、对象数组三种）。
- 子表接口当初被拒绝的确切原因（缺 `_search`，还是 `sidx`、`sord`）没有在真服务器上看过状态码；现在照网页的写法发，被拒绝时还会换写法。`xdoj-probe` 的“从头取一遍”和 `doctor` 的按接口统计能看出来。

可以直接请求真实的 XDOJ 来核对，但要遵守上面的只读和间隔要求，输出里遮掉学号、姓名，不要打印任何密码。
