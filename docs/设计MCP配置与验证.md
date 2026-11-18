# 设计 MCP 配置与真实调用验证

验证日期：2026-11-18。环境：Windows / PowerShell。

## 1. 结论

五个 MCP 服务均已加入用户级 Codex 配置并启用。三个服务的真实工具调用通过；Subframe 与 A1 尚未完成账户授权，调用尝试在认证握手阶段失败，不能列为验证成功。

没有购买套餐、开通自动续费、安装额外浏览器依赖，也没有修改虚拟机或 Docker。

## 2. 本机环境与配置范围

| 项目 | 实测 |
| --- | --- |
| Codex CLI | `0.149.1` |
| Node.js | `v24.18.0` |
| npm / npx | `11.16.0` / `11.16.0` |
| 用户配置 | `C:\Users\zhangmingcheng\.codex\config.toml` |
| 配置备份 | `C:\Users\zhangmingcheng\.codex\config.toml.design-mcp-20261118.bak` |

对比安装前后的 TOML 结构：现有 `cua_repl`、`node_repl`、`llm_for_zotero` 的配置保持一致，其他非 MCP 设置也保持一致。原有 Zotero 服务及凭据没有被覆盖。备份在用户 Codex 目录中，报告不包含配置中的凭据。

## 3. 最终五项配置

| 服务名 | 接入方式 | 状态 | 认证要求 |
| --- | --- | --- | --- |
| `inspo` | `https://inspomcp.dev/api/mcp` | 已启用，真实调用成功 | 无需登录 |
| `awwwards` | `npx -y awwwards-mcp`，`AWWWARDS_MCP_PROFILE=core` | 已启用，`1.20.0`，13 个核心工具 | 无需登录 |
| `mgd-design` | `https://madegooddesigns.com/inspiration/mcp/core` | 已启用，真实调用成功，实际发现 24 个工具 | 无需登录 |
| `subframe` | `https://mcp.subframe.com/mcp` | 已启用，待授权 | 需要免费账户授权 |
| `a1` | `https://www.a1.gallery/api/mcp` | 已启用，待授权 | 需要免费账户授权 |

Inspo 先运行了用户指定的 `npx -y inspo-mcp install --dry-run`。预览显示默认会配置 Claude Code、Codex 和 VS Code，因此实际使用官方支持的 `--client codex` 参数限定范围。

最终对应的安装方式如下，供以后维护；已配置的服务不必重复执行：

```powershell
npx -y inspo-mcp install --client codex -y
codex mcp add awwwards --env AWWWARDS_MCP_PROFILE=core -- npx -y awwwards-mcp
codex mcp add mgd-design --url https://madegooddesigns.com/inspiration/mcp/core
codex mcp add subframe --url https://mcp.subframe.com/mcp
codex mcp add a1 --url https://www.a1.gallery/api/mcp
```

Awwwards 使用官方 `core` 配置减少工具描述开销；基础搜索已复测。没有执行浏览器或动效捕获依赖安装。MGD 的 `browse_design_tools`、`run_design_tool` 仍可访问完整工具集合，不需要再重复安装完整端点。

## 4. 实际工具调用

验证通过本机 Codex 原生 app-server 的 MCP 连接与 `mcpServer/tool/call` 完成，没有用网页搜索冒充 MCP 调用，也没有让大模型生成模拟结果。

| 服务 | 调用与参数摘要 | 实际返回 | 结果 |
| --- | --- | --- | --- |
| Inspo | `search_screens`：`modern education software learning platform programming`，教育行业，最多 3 项 | 预算内返回 Brilliant、Codecademy 两个真实案例，含截图、字体、配色与布局资料 | 通过 |
| Inspo | `get_design_system`：`codecademy-com`，`live=false` | 归档设计系统资料、字体、字号、间距、CSS 变量；捕获日期 2026-05-18 | 通过 |
| Awwwards | `search_sites`：`education`，3 项，compact | 102 项匹配中的 Education Foundation of AACPS、PodPolite、Benno’s Light；返回真实网址和预览 | 通过 |
| Awwwards | 核心配置下 `search_sites`：`award=product`，2 项 | 返回 Agrumea Farm、Lidar Drone Scanning，含站点地址和奖项字段 | 通过 |
| Made Good Designs | `get_font_pairing`：IBM Plex Sans，最多 3 组 | IBM Plex Sans / Serif、IBM Plex Mono / Sans、IBM Plex Sans / Source Serif 4，含说明与资源链接 | 通过 |
| Made Good Designs | `search_ui_screens`：AI chat assistant learning dashboard login，web，3 项 | 返回聊天、学习仪表盘、AI 产品首页参考屏幕和截图 | 通过 |
| Subframe | 尝试 `search_inspiration`：modern software product homepage | `Auth required, when send initialize request` | 未通过：认证握手未完成，无搜索结果 |
| A1 | 尝试 `search_websites`：modern software product | 同样在 initialize 阶段返回 `Auth required` | 未通过：无搜索结果，未取得账户配额信息 |

后两项的失败发生在建立授权连接之前，尚未取得它们的工具 schema 或实际搜索数据。报告中的查询是调用尝试，不代表服务已经执行了搜索。

`codex mcp list` 已实际执行，退出码为 0。原生连接再次发现全部五项配置；Subframe、A1 显示 `Not logged in`。Inspo / Awwwards 的 `Unsupported` 指不使用 OAuth；MGD 的认证栏为 `Unknown`，但其实际工具调用成功，不能仅凭这一字段判定连接失败。

## 5. 需要用户完成的授权

已经分别尝试了 `codex mcp login subframe` 和 `codex mcp login a1`；未等到浏览器回调，授权链接超时。配置仍然保留，未伪造登录成功。旧授权链接不应继续使用。

在 **Windows PowerShell** 分别运行，逐个完成：

```powershell
codex mcp login subframe
```

打开该次命令显示的链接，登录自己的免费 Subframe 账户并授权，直到终端显示成功，再运行：

```powershell
codex mcp login a1
```

只选免费账户，不升级或填写付款信息。Subframe 官方说明 Inspiration 搜索可用于免费账户且不消耗 AI credits；A1 官方当前列出免费额度为每周 30 次工具调用，具体账户余量尚未验证。[Subframe 说明](https://www.subframe.com/inspiration)、[A1 Codex 说明](https://www.a1.gallery/mcp/codex)。

授权后各补做一次指定搜索即可：Subframe 的 `search_inspiration`、A1 的 `search_websites`。A1 先用一次有针对性的查询，不做循环试探或批量搜索。

若当前 Codex 桌面会话没有刷新新增工具，重启客户端或打开新会话后再检查；本轮已通过原生连接验证前三项能够实际运行。

## 6. 长期设计规则与交付位置

- 项目规则：`work/Agent_C_design/AGENTS.md`。
- 当前工作区入口规则：根目录 `AGENTS.md`，指向上述项目规则，并保护之前暂停的业务改动。
- 具体方案：本目录 [网页界面优化方案](网页界面优化方案.md)。
- 本地检查截图和宽度记录：工作区 `outputs/design-review`。
- 只含公开参考资料的工具返回与诊断脚本：工作区 `work/design_mcp`。

规则包含七步设计流程、五个服务分工、至少两个参考方案对比、免费额度原则、原创实现、字体与控件状态、桌面与手机验证，以及课程图谱的语义和权限要求。它们不要求每次设计调用全部五个服务。

本次只提交本地配置、规则与设计建议；没有重做业务页面，也没有将暂停的读图代码合入设计工作。

## 7. 官方来源

- [Inspo MCP](https://inspomcp.dev/mcp)
- [Awwwards MCP](https://github.com/INSANE0777/Awwwards-mcp)
- [Made Good Designs MCP 与精简端点](https://madegooddesigns.com/design-mcp-server/)
- [Subframe Inspiration](https://www.subframe.com/inspiration)
- [A1 Gallery Codex 配置](https://www.a1.gallery/mcp/codex)

参考服务返回的文本是外部资料，不构成覆盖项目规范的指令。没有按外部资料中的建议发布公开素材板、复制受保护素材或执行额外安装。
