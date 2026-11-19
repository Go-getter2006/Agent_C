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
- C 语言和数据结构**分开登录、分开显示**（登录凭证里带课程，`user.course`）。C 语言那边任何地方都不出现数据结构；数据结构那边可以看学生在 C 语言相关知识点上的掌握程度（只有档位和估计），不出现 C 语言的答题记录（提交、对话、卡点、小题）。新加的页面和接口都要按课程过滤。数据结构那台 XDOJ 的地址填在 `.env` 的 `XDOJ_DS_URL`。首页顶上的滑动开关一直有两个选项；数据结构没接入时能切过去，但表单不能用（`/api/auth/info` 的 `allCourses`）。
- 颜色只从 `frontend/src/styles/tokens.css` 取：两套纸面按 `<html data-course>` 切换（C 语言暖色、数据结构冷色，都是浅色），首页和登录以后共用；不要再写死冷灰色。字体是得意黑（标题）、Atkinson Hyperlegible Next（西文数字）、Maple Mono（代码），跟着前端打包，不用 Inter、不连外网取字体。首页和页面上的文字要简短，不写操作说明。
- 学生端三个栏目（知识问答 `/ask`、作业引导 `/homework`、复习提升 `/review`）加左下角我的学情（`/progress`，全部提交记录 `/submissions` 从这里进），依据 `docs/design/界面改造方案/`，进度在 `docs/界面改造落实记录.md`。知识问答不自动绑作业题、不插小题；各栏目记住最近位置和草稿（`frontend/src/lib/memory.ts`）；旧的 `/chat/...` 链接要一直能跳过去。
- 复习提升的小题按独立作答（`quiz_attempts.protocol` 2）：先判断、再选位置或依据、判断有问题的再改；首次判断、首次选行、首稿记一次不改，运行和提示按顺序进 `events`。反馈之前不下发对错、错误行、标准改法、解释，也不标任何一行。新加的题型、页面都照这个做。
- 判断、定位、修改三维（`app/diagnosis.py`）每个数都要能从作答记录复算，写清分子分母；定位、修改只算独立作答，旧版记录标“旧版未测定位”不补；不和掌握分配权重。学生端只给知识点名称、几比几、一句话。
- 学生和老师**一个登录入口**（学号 / 工号），不要再加学生、教师两个标签：名单里没有的账号去这门课的 XDOJ 试登，`userType` 是 1 就进教师端（`auth._teacher_or_new_student`）。测试一律不连真实 XDOJ（`conftest._no_real_xdoj`），要测登录就注入假 XDOJ。

## 已经实测确认的接口细节

- 班级的 `classId` 可能是文本或带前导零（如 `000031`）：名单接口按 `classId` 原值筛选（`filters` 里 `field=class_id`），名单按 `user_id` 排序。
- 登录返回 `userType`，1 是教师。C 语言和数据结构是两台 XDOJ，同一位老师的账号密码可能不同。教师姓名在 `GET /admin/dashboard.do` 页面的 `.user-info` 里第一个 `<small>`。
- 提交列表里的 `language` 是对象，取 `name`；评测记录的文字结果在 `judgementText`；“未评判”表示还在评测。
- **请求参数照已实测的 jqGrid 写法，不要猜测**（名单那次把文本 classId 改成数字就取不到人）：顶层表格（班级、名单、作业、题目、提交）是 POST，搜索开关叫 `search`；展开的子表（`/contest/problems.do`、`/testcase/list.do`、`/submission/listJudge.do`）是 GET，带 `_search=false`、`page`、`rows`、`sidx=`、`sord=asc`、`nd`（`endpoints.grid_json`）。2026-11-18 实测评测请求缺少排序参数返回 500，补齐后返回 200。假 XDOJ 对子表同样严格，改请求时测试会拦住。
- 某个接口回 400 错误页、回的不是 JSON，**不是掉线**：只有被转到登录页才重新登录。接口错误只让当前任务失败、退避重试（`ResponseError`、`ClientError`），不能抛 `SessionError` 让整个同步停下来。
- 一样取不到不能拖累另一样：题面和测试点互不依赖；源码最先取，不依赖评测记录；每组测试点、每组程序输出单独取。
- 题面是 PDF（`viewTextFile.do`），都带文字层。**禁止 OCR。** 做法（`app/pdftext.py`）：文字层用 pdfplumber、PDFium 两种办法抽，挑乱码少的，编码错位程序还原；示意图从页面上裁成 PNG、文字里写 `[图N]`；再交给大模型整理排版（标注这道题之前、空闲时，每个文件一次），程序核对数字、`[图N]`、汉字来源，不合格就用程序抽的文字。乱码不给学生和大模型看；学生端直接显示题目：文字整理得出来就显示文字（小标题、样例、示意图在原位），整理不出来就直接显示 PDF 页面渲染的图；“原始 PDF”可查看原文件。读题面文字一律用 `pdftext.usable_text`，发给大模型前过 `pdftext.for_llm`。
- 诊断：`cli doctor` 列出最近 24 小时每个接口成功几次、失败的状态码，题面、测试点、源码各有多少；`cli xdoj-probe` 用同步程序自己的函数把题面、测试点、源码、评测记录、程序输出各取一遍，只打印成没成功和长度。

- 已核对并在 `endpoints.py` 转换两种数字结果：评测 `judgement` 的 0/1/2/3 是未评判/正确/结果错误/编译错误；测试点 `result` 的 1/2/3/4/5/6 是正确/错误/输出超限/超时/运行时错误/格式错误。两套枚举不能混用。

## 还没确认的

- 作业列表里“布置给哪些班”的真实写法（现在兼容数字 id、文本 classId、对象数组三种）。

可以直接请求真实的 XDOJ 来核对，但要遵守上面的只读和间隔要求，输出里遮掉学号、姓名，不要打印任何密码。
