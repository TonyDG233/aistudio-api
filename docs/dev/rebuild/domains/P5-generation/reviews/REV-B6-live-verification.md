# Step B6 本地实机综合验收审计报告

- 日期：2026-10-31
- 目标树：`.kilo/worktrees/rebuild-upstream`
- 代理：`http://127.0.0.1:10809`
- 浏览器：主工作区 CloakBrowser
- 服务启动次数：**1 次常驻**
- 生成请求：**3 次 / 预算 5 次**
- 结论：**FAIL / PARTIAL，异常即停；未完成全部 10 项，未提交**

## 1. 阶段 1：0 生成验收

| 项目 | 结果 | 证据 |
|---|---|---|
| `/health` | PASS 200 | 本地服务 |
| 空 API Key | PASS 401 | `/v1/chat/completions` |
| 有效 Key `/v1/models` | PASS 200，31 个 | live_page |
| `/v1beta/models` | PASS 200，31 个 | live_page |
| `model_discovery.last_source` | PASS `live_page` | 服务日志 |
| 实例池 | PASS 2 个 active：Free / Pro；in_flight 0 | `/api/instances` |
| 控制面重复预热 | PASS 未发生；启动仅 `InstanceManager.startup()` | 服务日志 |
| `GET /api/keepalive` | PASS 200 | 本地 |
| Ultra 本地 403（Gemini） | PASS 403，含 `tier/model/required_tier/current_tier` | `veo-3.1-generate-preview` |
| Ultra 本地 403（OpenAI） | **FAIL** 403，但仅通用 error，缺四字段 | `/v1/chat/completions` |

## 2. 阶段 2：生成与工具

| # | 场景 | 结果 | 证据 |
|---|---|---|---|
| 1 | OpenAI Free 非流式 `gemini-3.8-flash` | **PASS 200**，内容 `OK`，有 response id，命中 inst-1 | 200 |
| 2 | OpenAI Paid 流式 `gemini-3.6-flash` | **PASS 200 SSE**，3 个事件、`OK`、`[DONE]`，命中 inst-2 | 200 |
| 3 | 原生 Gemini 流式 `gemini-3.8-flash` | **FAIL** HTTP 200，但客户端 0 事件；服务端无 stream complete | 200/0 events |
| — | countTokens `gemini-3.8-flash` | **PARTIAL** 服务端 200，`totalTokens=11`；客户端 JSON 解析失败 | 服务日志 |
| 4 | 原生 tools / signature_cache / `[toolshape]` | **未执行** | 异常即停 |
| 5 | PG-08 + ATG prefer 流式 | **未执行** | 异常即停 |
| 4 | B4 日志页/SSE/导出 | **PASS** `/logs` 200 HTML、SSE 200、export 200 | 本地 |
| 10 | 优雅关闭 | **PASS** 8080/9222 closed，Chrome 0 | 本地 |

## 3. 失败与判断

1. **原生 Gemini 流式异常**：HTTP 200 但客户端 0 SSE 事件，服务端日志只有“开始流式处理”，没有完成日志。未重试，未消耗第 4/5 次生成预算。
2. **countTokens 客户端解析异常**：服务端日志明确 `totalTokens=11` 且 HTTP 200；客户端 JSON 解析失败，需修正验收脚本或响应读取方式，不能据此判定服务端失败。
3. **OpenAI tier 403 错误体不完整**：状态码 403 正确，但缺少 `tier/model/required_tier/current_tier` 四字段；Gemini 403 完整。
4. **#4/#5 未执行**：因 #3 异常即停，原生 tools、signature_cache、`[toolshape]`、PG-08 + ATG 均未实测。

## 4. 安全与清理

- 未打印任何邮箱、Cookie、API Key、真实 IP 明文。
- 临时 API Key 已移除；`api_keys` 数量恢复为 0。
- `features.atg.mode` 已恢复默认 off。
- 根 `config.yaml` 仍 untracked，`git ls-files config.yaml` 为空。
- 单次服务已优雅停止；8080/9222 关闭，Chrome 进程 0。
- 本次没有重启服务，没有重新触发 BotGuard。

## 5. 证据

- `docs/dev/evidence/B6/results.json`
- `docs/dev/evidence/B6/diagnostics.json`

## 6. 结论

**B6 不能判定 PASS。** 当前确认为：

- 单次启动、Free/Pro 池、live 模型发现、Free/Paid 非流式与流式各一次均通过；
- 原生 Gemini 流式为空响应、OpenAI 403 错误体缺字段、countTokens 客户端解析异常；
- PG-08/ATG、原生 tools、signature_cache、`[toolshape]` 未完成实机验收。

在修复/复核这些问题前，不提交 B6 报告，不启动第二次实机服务。
