# XDOJ 接口核对清单（交给能访问 XDOJ 的 AI 或同学执行）

已在 2026-11-18 使用真实教师会话核对班级和提交详情。班级的 `classId` 是文本筛选值，内部数字主键来自 `id`；`listJudge.do` 请求必须携带排序参数。正确、答案错误、编译错误三条提交的取证链路共 53 次请求均返回 200，取得了题面 PDF、源码、评测、标准数据和失败测试点实际输出。实际数据只在内存中核对，下面保留再次核对接口的步骤。

本次修复的验证范围：

- 真实 XDOJ：串行只读，请求至少间隔 1.1 秒；三条提交验证源码、PDF 文字、评测、失败测试点输入/标准输出/实际输出及编译诊断。实际学生数据只进入内存数据库。对模型入口注入测试后端，断言上述输入输出确实出现在请求中；没有调用收费的 DeepSeek。
- 本地回归：完整测试运行中 163 项通过、20 项因没有 gcc 跳过；教师接口的 `test_student_detail_has_internal_fields_for_teacher` 断言“基本掌握”，实际为“待加强”，在原主分支 `44c0cda` 也失败。之后核心同步、题面、对话、接口、标注和诊断回归 104 项通过；最后补充的优先缓存修复和长模型上下文检查均通过。
- 浏览器：1440 像素下学情卡片底部对齐、知识点内部滚动；390 像素下无横向溢出；原始 PDF 抽屉宽 900 像素；源码第一次返回 409 后自动重试成功；无浏览器脚本异常。前端 TypeScript 检查及生产构建通过，`frontend/dist` 随代码发布。
- 当前测试环境为 Windows；Linux 沙箱执行没有验证。真实模型的生成质量仍需在配置 DeepSeek key 的部署环境确认。

## 1. 规则（必须遵守）

- 地址前缀：`http://47.92.139.74/xdoj-ssm`（下文的路径都接在它后面）。
- **只读**：只发下面列出的请求，不提交、不修改任何东西。
- **一个一个发**，两次请求之间至少隔 0.2 秒。全部做完大约 25 个请求。
- 用**教师账号**登录。第 2.10 条另用一个学生账号，学生自己同意才做。
- **不要把这些内容发回来**：任何密码、token、登录 IP、cookie 的值。学号和姓名只保留前 2 个字符，其余用 `*` 代替，例如 `25********`、`林*`。班级号、作业号、题号、提交号、测试点号不敏感，照原样给。
- 测试点的输入输出内容不要发回来，只给字段名。

登录方法：

1. 先 `GET /`，拿到 `JSESSIONID`。
2. 再 `POST /user/login.do`，表单字段：`userId=账号`、`password=密码`、`preferLanguage=zh_CN`。

之后的请求都带着这个 cookie。除非特别写明，请求参数都是**表单**（`application/x-www-form-urlencoded`），不是 JSON。

## 2. 要发的请求和要回答的问题

### 2.1 登录
`POST /user/login.do`（教师账号）

- 返回 JSON 有哪些键？登录成功时 `error` 的值是多少？
- 再用错误的密码试一次：`error` 的值和提示文字是什么？

### 2.2 班级列表
`POST /class/list.do`，参数：`page=1`、`rows=5`、`sidx=id`、`sord=asc`

- 第一行的全部字段名和类型。
- `totalCount`、`totalpages` 这两个键在不在？
- **关键**：每个班的 `id` 和 `classId` 是不是同一个数？请列出前 3 个班的 `id`、`classId`、`className`。

### 2.3 学生名单（最关键）
`POST /user/listStudents.do`，参数：`page=1`、`rows=3`、`sidx=id`、`sord=asc`、`search=true`、`filters=<下面的 JSON 字符串>`

```
{"groupOp":"AND","rules":[{"field":"class_id","op":"eq","data":"X"}]}
```

拿 2.2 里第一个班做下面三次：

1. X = 这个班的 `classId`：`totalCount` 是多少？
2. X = 这个班的 `id`：`totalCount` 是多少？
3. 不带 `search` 和 `filters`：`totalCount` 是多少？

另外给出第一行的全部字段名和类型。学号、姓名按规则遮掉；密码、token、IP 这类字段只写字段名，不写值。

### 2.4 作业列表
`POST /contest/list.do`，参数：`page=1`、`rows=3`、`sidx=id`、`sord=desc`

- 第一行的全部字段名和类型。
- 有没有表示“布置给哪些班”的字段（比如 `classes`）？有的话，它的样子是什么（数字数组，还是 `[{"id":..}]` 这种对象数组）？里面的数对应 2.2 的 `id` 还是 `classId`？
- 有没有表示“包含哪些题”的字段（比如 `problems`）？样子同上。
- `startTime`、`endTime` 的格式，给一个例子。

### 2.5 作业包含的题
`GET /contest/problems.do`，参数：`contestId=<2.4 第一个作业的 id>`、`page=1`、`rows=50`

- 第一行的全部字段名和类型。题号是哪个字段（`problemId` 还是 `id`）？

### 2.6 题库
`POST /problem/list.do`，参数：`page=1`、`rows=2`、`sidx=id`、`sord=asc`

- 第一行的全部字段名和类型；`tags` 的样子。
- `totalCount`，也就是题库一共几道题。

### 2.7 测试点列表
`GET /testcase/list.do`，参数：`problemId=<2.5 里的一个题号>`、`page=1`、`rows=20`

- 第一行的全部字段名和类型。**不要**下载测试点文件。

### 2.8 提交列表（关键）
`POST /submission/list.do`，参数：`page=1`、`rows=3`、`sidx=s.id`、`sord=desc`

- 第一行的全部字段名和类型。学号、姓名按规则遮掉。
- 表示评测结果的字段叫什么？请列出它出现过的所有取值。可以换 `rows=100` 再取一页来看，例如“正确”“答案错误”“编译错误”“运行错误”“时间超限”……如果结果是数字，请给出数字和含义的对应。
- 提交时间的字段名和格式。

再带上筛选条件试一次，确认按提交号增量取数的写法可用：
`page=1`、`rows=3`、`sidx=s.id`、`sord=asc`、`search=true`、`filters={"groupOp":"AND","rules":[{"field":"s.id","op":"gt","data":"<比最新提交号小 3 的数>"}]}`

- 返回的是不是正好那几条？

### 2.9 一条提交的评测详情
找一条“答案错误”的提交号 S，依次请求：

1. `GET /submission/listJudge.do`，参数 `submissionId=S`、`page=1`、`rows=20`、`sidx=`、`sord=asc`、`_search=false`：逐页读取评测记录，文字结果为 `judgementText`，数字结果为 `judgement`。缺少排序参数时实测返回 500。
2. `POST /submission/listAllJudgeRun.do`，表单 `judgeId=<上一步的 id>`：第一行的全部字段名和类型；`result` 有哪些取值。
3. `POST /submission/getRunOutput.do`，表单 `id=<上一步某个没通过的 runId>`：返回的字段名（只给字段名，不给内容）。

如果能找到一条“编译错误”的提交，也做第 1 步，看 `compileOutputStr` 有没有内容。

再找一条刚交、还在评测中的提交（没有的话跳过）：它在 2.8 的列表里、在第 1 步里分别长什么样？

### 2.10 学生账号能不能登录（学生同意才做）
用一个学生自己的账号做一次 2.1 的登录：`error` 是不是 0？学生的账号和密码不要发回来，只说成功还是失败、返回的提示文字。

## 3. 回报格式

每一条按这个样子写，可以直接贴回来：

```
### 2.2 /class/list.do
请求：POST page=1 rows=5 sidx=id sord=asc
totalCount=12 totalpages=3
第一行字段：{"id": "int", "classId": "int", "className": "str", "ownerId": "str", ...}
前 3 个班：id=101 classId=2501 className=C语言1班；id=102 classId=2502 ...
回答：id 和 classId 不一样
```

请求出错的，写上状态码和返回的前 200 个字符（先遮掉其中的敏感内容）。
