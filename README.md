# 程序设计智能体

给上 C 语言程序设计课（以及之后的数据结构课）的学生和任课教师用。系统只读地同步 XDOJ 上的练习提交，自动分析没通过的提交错在哪、涉及哪个技能；学生可以就某次提交或某个概念提问；按每个学生自己的情况出很短的代码小题，检验是否真的掌握；卡过的地方在快忘的时候安排复习；给学生和教师看学情。只面向平时练习和答疑，不接考试。

设计依据是《给 ClaudeCode 的总说明》及其附件（对话工作流与标签库、XDOJ 同步程序开发说明、设计规范 A 工作台）。这些原始文档没有放进这个公开仓库。

## 界面

| 学生：对话 | 教师：知识图谱 |
|---|---|
| ![对话](docs/design/screenshots/对话-1440.png) | ![知识图谱](docs/design/screenshots/教师-知识图谱-详情-1440.png) |
| **学生：学情** | **教师：总览** |
| ![学情](docs/design/screenshots/学情-1440.png) | ![总览](docs/design/screenshots/教师-总览-1440.png) |

全部截图（四个宽度）在 [docs/design/screenshots/](docs/design/screenshots/)，设计上和规范不一样的地方见 [docs/design/审查记录.md](docs/design/审查记录.md)。

## 现在做到哪了

| 部分 | 状态 |
|---|---|
| 数据表、固定编码表 | 已完成。C 语言 65 个知识点、128 条技能；数据结构 30 个知识点、56 条技能；两门课之间 28 条跨课程先修边 |
| XDOJ 服务器 | C 语言的地址 `http://47.92.139.74/xdoj-ssm` 写进了代码（出处是《总说明》3.1）；数据结构的服务器地址不同，还没给出。别的课用 C 语言的地址会被拒绝 |
| XDOJ 连接器和同步（白名单、限速、轮询、详情、回读、回填、证据 JSON） | 已完成，用假 XDOJ 测过，**没有连过真实服务器**（这个开发环境连不上那个地址） |
| 标注（规则 + 大模型）、题目标注、参考解验证 | 已完成，大模型路径只用桩测过 |
| 对话工作流（8 步、规则表、回复校验、卡点递进） | 已完成；没配大模型时走规则和固定模板 |
| 小题 | 按学生的情况出题：优先改学生自己通过的代码，其次让大模型出，再次用模板换名字，最后才用题库；每道题都在沙箱里验证过 |
| 掌握程度 | v2（默认）：可靠性加权（雷同的通过打折）、个人化的遗忘、知识图先验、80% 区间；v1 保留作对照 |
| 复习 | 间隔重复：每一项有自己的半衰期，快忘的时候提醒，答对了间隔拉长，连对三次算巩固 |
| 学生端页面（登录、对话、提交、差异、复习、学情） | 已完成，四个宽度检查过 |
| 教师端（总览、学生、学生明细、卡点、知识图谱、模型对比、日志申请、数据源） | 已完成 |
| 日志申请 | 老师按需下载自己学生的使用记录（CSV / JSON），要写用途、留审计记录、链接 7 天失效 |
| 图模型（贝叶斯网络，只在教师端显示） | 已完成，参数是经验值，先修边是未审定的初稿 |
| 登录 | 老师用 XDOJ 账号登录，系统自动同步他的班级和名单；学生用名单里的学号和 XDOJ 密码登录（转发给 XDOJ 核对，不保存密码）。**学生这条还没有用真实账号测过** |
| 新技能和新错误自动入库、接入数据结构的服务器 | 没做（在待确认清单里） |

没做完和需要你确认的地方都列在 [docs/问题清单.md](docs/问题清单.md)。

## 运行

### 试用（Linux 机器或虚拟机，一条命令）

```bash
git clone https://github.com/Go-getter2006/Agent_C.git && cd Agent_C
./run.sh deps     # Ubuntu / Debian 上装 Python、gcc、Node 20，只需一次（要 sudo）
./run.sh          # 第一次会自动装依赖、构建、生成 .env 和密钥，并问一次 DeepSeek 的 key
```

然后浏览器打开 `http://这台机器的地址:8000`：

1. **老师**在登录页选“教师”，用自己的 XDOJ 账号登录。系统会去 XDOJ 核对一次，成功后自动同步他名下的班级、学生名单、作业和提交（后台同步和网页在同一个进程里，不用另外启动）。“数据源”页能看到同步进度。
2. **学生**用名单里的学号和自己的 XDOJ 密码登录。密码只拿去 XDOJ 核对一次，不保存。名单还没同步下来的学号登录不了。

以后再启动还是 `./run.sh`；拉新代码并更新用 `./run.sh update`；想开机自启用 `./run.sh service`。

只想看看界面、不连 XDOJ：`./run.sh demo`。演示数据放在单独的库里（`data/demo.sqlite3`），是虚构的一个班：学生学号 `2500000001`，教师选“教师”后账号 `demo-teacher`，密码都是 `.env` 里的 `DEV_LOGIN_PASSWORD`（第一次运行时设成 `demo`）。

为什么是 Linux：小题的“运行检查”要编译、运行学生改的代码，沙箱用的是 Linux 才有的限制（资源上限、断网、换成无权限用户）。Windows 上可以跑网页，但小题检查不安全，不建议。

### 正式部署（公网）

服务器上只装 Docker，不用装 Python 和 Node：

```bash
git clone https://github.com/Go-getter2006/Agent_C.git && cd Agent_C
cp .env.example .env    # 填 SECRET_KEY、POSTGRES_PASSWORD、DEEPSEEK_API_KEY，要 HTTPS 再填 DOMAIN
docker compose --profile https up -d --build
```

会起五个容器：PostgreSQL、网页、后台同步、沙箱（没有网络、只读、去掉全部特权）、Caddy（自动申请 HTTPS 证书）。已经有学校网关或 nginx 做 HTTPS 的，去掉 `--profile https`，把网关转发到本机 8000 端口。更新：`git pull && docker compose up -d --build`。这份 compose 在开发环境里没有实际启动过，第一次部署请留意。

### 命令行里测 XDOJ（可选）

```bash
cd backend && . .venv/bin/activate
# .env 里填 XDOJ_USER、XDOJ_PASS（地址不用填，就是 C 语言那台）
python -m app.cli login-test          # 只测登录
python -m app.cli xdoj-probe          # 核对接口字段名（只打印字段名和类型）
```

对 XDOJ 只读：客户端里写死了允许访问的十几个查询接口，其他路径一律不发；所有请求排队，两次请求至少间隔 0.3 秒（《同步程序开发说明》定的下限）。名单接口带回的学生密码、令牌、登录 IP 在内存里就丢掉，不进库也不进日志。

## 测试

```bash
cd backend
python -m pytest tests -q                                   # 内存 SQLite
TEST_DATABASE_URL=postgresql+psycopg://用户:密码@主机/库 python -m pytest tests -q   # 同一套测试跑在 PostgreSQL 上
```

## 目录

```
backend/
  app/
    config.py        全部配置和可调参数（权重、阈值都带版本号）
    models.py        数据表
    taxonomy/        固定编码表：知识点、技能、错误类型、先修边初稿、枚举
    xdoj/            连接器、同步、主循环、证据 JSON、假 XDOJ
    labeling/        错误标注（规则优先）、题目标注
    llm.py           大模型网关
    sandbox/         编译运行小题代码
    chat/            对话工作流：分析、取证、规则表、生成、校验
    servers.py       已知的 XDOJ 服务器（C 语言的地址写在这里）
    quiz.py quizbank.py   小题的作答和判分、题库
    quizgen.py       按学生情况出小题
    review.py        复习（间隔重复）
    similarity.py    通过代码的雷同检查
    mastery.py       掌握程度 v1、v2
    graphmodel.py    图模型（只在教师端）
    progress.py      学生学情页
    logs.py          教师的日志申请
    catalog.py       学生可见范围
    web/             接口：登录、学生端、教师端
    demo/            演示数据
    cli.py           命令行
  alembic/           数据库迁移
  tests/
frontend/            React + TypeScript + Vite
  src/styles/tokens.css   设计令牌
  src/components/    外壳、对话、小题、图表、知识图谱
  src/pages/         学生端页面；teacher/ 是教师端
docs/                技术方案、问题清单、怎么验证、设计审查记录
```
