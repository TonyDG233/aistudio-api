# Step B6 本地实机综合验收审计报告

- 日期：2026-10-31
- 目标树：`.kilo/worktrees/rebuild-upstream`
- 代理：`http://127.0.0.1:10809`
- 浏览器：主工作区 CloakBrowser（通过 `AISTUDIO_BROWSER_EXECUTABLE` 复用）
- 账号：47 个已复制到目标树；启动后可用约 43 Free / 2 Pro，另有部分账号 401 过期
- 模型目录：报告中“Page XHR 实拉 14 个模型”的表述**已被更正**；该 14 个 ID 实际来自静态 `_FALLBACK_MODELS`，不是 live 数据
- 结论：**FAIL / PARTIAL，已按失败即停规则终止；未完成全部 10 项**

> **更正（2026-10-31 复核）**：原报告称模型目录由 Page XHR 实拉，经复核为**错误**。实际根因：
> 1. 目标树 `app.py` 没有主分支的 `_model_warmup` 启动任务，缓存从未被 live 刷新；
> 2. `routes_models.py` 与 OpenAI `/v1/models` 传入的是 `runtime_state.client._session`（控制会话），而非 READY 的 `InstancePool`/`PooledSession`；
> 3. `_fetch_via_page` 失败仅被 `logger.debug` 吞掉，`get_models` 随后静默返回静态 fallback。
>
> `/v1/models` 返回的 14 个 ID 均为 `_FALLBACK_MODELS` 内容，`source` 应记为 `fallback / discovery not wired`，不能记为 live。
> 本报告仍为 **FAIL / PARTIAL**，不得据此判定 B6 PASS；模型动态发现/Paid 接线的修复需另行验收。
>
> **补充更正（2026-10-31）**：`model_discovery_fix.json` 是修复后的**另一次独立运行**，与本报告原始 B6 运行的 14 个 fallback 结果分开记录。它不能回溯证明本报告原始运行为 live，也不构成 B6 PASS；本报告结论维持 **FAIL / PARTIAL**。

## 1. 结果矩阵

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | 健康检查、空 Key 401、有效 Key 放行 | **PASS（部分）** | `/health` 200；无 Key `/v1/chat/completions` 401；有效 Key `/v1/models` 200、14 个模型（实为静态 fallback，非 live） |
| 2 | 多实例池 Free/Pro、单 Page、`active_requests=0` | **PASS（部分）** | 2 个 instance active，tier 为 Free/Pro，前后 `in_flight` 均为 0；因生成失败未做完整 page 泄漏检查 |
| 3 | B3 保活手动触发 | **PASS** | `GET /api/keepalive` 200；`auth_update`、`metadata_refresh` 均 200 |
| 4 | B4 日志静态页、SSE、导出、脱敏 | **PASS（带说明）** | `/logs` 在 `Accept: text/html` 下 200；`/api/logs/stream` 200 `text/event-stream`；`/api/logs/export?days=1` 200 `text/plain`，2,468,110 bytes；`[toolshape]` 存在且测试标记未泄露 |
| 5 | OpenAI 非流式/流式；Free/Pro；Free 请求 Paid 403 | **FAIL / 未完成** | Free `gemini-3.7-flash` 非流式 404；`gemini-3.5-flash` 403 permission；Pro 与流式因失败即停未执行；未完成 Free→Paid 403 场景 |
| 6 | 原生 Gemini 非流式/流式 | **未执行** | 依赖模型链路先通过 |
| 7 | PG-08 伪装工具历史 + ATG prefer 流式 | **未执行** | 首次脚本在普通 Free 请求即失败；未进入 PG-08 场景 |
| 8 | 原生 tools / signature_cache 真实写入 | **未执行** | 依赖生成链路 |
| 9 | countTokens | **FAIL** | `gemini-3.7-flash` 404 not_found；`gemini-3.5-flash` 503 surface_quota_exceeded |
| 10 | 优雅关闭 | **PASS** | 停止后 8080/9222 均 closed，Chrome 进程数 0 |

## 2. 关键失败与判断

1. Free 账号请求 `gemini-3.7-flash` 返回上游 404 `not_found_error`；关闭 ATG 后复测仍是 404。
2. Free 账号请求 `gemini-3.5-flash` 返回 403 `permission_error`（权限不足）。
3. 随后 `countTokens` 对 3.7 返回 404，对 3.5 返回 503 `surface_quota_exceeded`。
4. 这些不是 ATG 注入导致的错误；更像是模型可见性/账号权限/该 surface 配额问题。
5. 已使用 **3 次生成预算**，全部失败；按“失败即停、不盲目重试”未继续消耗第 4/5 次。
6. 未将 404/403/503 误报为 PASS，也未把失败归因于 B5b 代码。

## 3. 安全与清理

- 未打印任何邮箱、Cookie、API Key、真实 IP 明文。
- 临时 API Key 已全部清理；配置中 `api_keys` 数量为 0。
- `features.atg.mode` 已恢复为 off。
- 根 `config.yaml` 仍 untracked；`git ls-files config.yaml` 为空。
- 账号目录复制仅落在被 `.gitignore` 忽略的 `data/accounts/`。
- 服务优雅停止，8080/9222 端口关闭，Chrome 进程 0。

## 4. 证据

- `docs/dev/evidence/B6/results.json`
- `docs/dev/evidence/B6/diagnostics.json`
- `docs/dev/evidence/B6/model_discovery_fix.json`（修复后的另一次独立运行：`last_source=live_page`，31 个 live 模型，`/v1/models` 与 `/v1beta/models` 均 200 且 ID 与快照一致；不能回溯本报告原始 14 个 fallback，不作为 B6 PASS）

## 5. 建议（需用户裁决）

1. 提供一个**已确认可生成**的 Free 模型或账号，以及一个可用的 Pro 模型/账号；当前模型目录是全局发现结果，不代表当前 Free/Pro 账号都拥有生成权限。
2. 或授权在清理后重试一个更保守的模型（例如账号实际可访问的 flash/gemma 型号），但需新的请求预算。
3. 在模型/账号确认前，不继续 PG-08、ATG、native tools、countTokens 的实机验收。
4. 本次 B6 只能判定为 **FAIL / PARTIAL**，不能作为最终实机 PASS。
